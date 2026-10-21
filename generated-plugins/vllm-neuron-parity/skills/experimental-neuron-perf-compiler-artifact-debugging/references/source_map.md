# Source attribution: penguin IR → HLO → framework code

Penguin IR carries a `DebugLocation`, but the `file` and `line` fields are
typically empty for vllm-neuron / torch-xla compiles. This guide shows how
to recover source attribution anyway.

## What's in penguin IR

Each penguin op has:

```python
m4.DebugLocation(
    tensor_op_name="_cosine.4502",   # HLO op name with leading underscore
    hlo_id=700,                       # HLO instruction id
    file="<eval_with_key>.16",        # may be a synthesized FX path or empty
    line=193,                         # may be set or 0
    kernel="",
)
```

When `file=""` and `line=0`, you have to chain through HLO + FX graph + model.py.

## The chain

```
penguin op           → HLO instruction         → FX graph node       → model.py
tensor_op_name=      → ins.name=               → call_function/      → forward_*
"_cosine.4502"          "cosine.4502"             call_method            method
hlo_id=700              ins.id=700                target=torch.cos       
                        ins.shape=[64, 256]       
                        ins.metadata.op_name=
                        "aten__cos"
```

## Step 1: penguin op → HLO instruction

Strip the leading underscore from `tensor_op_name`:

```bash
# In penguin IR
grep -m3 'tensor_op_name="_cosine\.4502"' <DBG>/penguin-sg*/penguin.py
```

Look up in HLO:

```python
from neuronxcc.thirdparty_libs.xla.service import hlo_pb2
m = hlo_pb2.HloModuleProto()
m.ParseFromString(open("graph.hlo", "rb").read())

target_id = 700
for c in m.computations:
    for ins in c.instructions:
        if ins.id == target_id:
            print(f"name:         {ins.name}")
            print(f"opcode:       {ins.opcode}")
            print(f"shape:        {list(ins.shape.dimensions)}")
            print(f"operands:     {list(ins.operand_ids)}")
            md = ins.metadata
            print(f"op_name:      {md.op_name}")
            print(f"op_type:      {md.op_type}")
            print(f"source_file:  '{md.source_file}'")
            print(f"source_line:  {md.source_line}")
```

If `metadata.op_name` is set (e.g. `"aten__cos"` or `"aten__index_put"`),
that's a strong handle into the framework op set.

## Step 2: HLO op → FX graph node

The framework caches an FX graph dump alongside the HLO at compile time. For
vllm-neuron, look at:

```
<compile_cache>/<hash>/fxgraph.txt
# OR
<compile_cache>/<hash>/dev0_*.rank0/fxgraph.txt
```

These dumps are produced by `vllm_neuron/compile/capture_backend.py:_setup_compilation_logging`.

The HLO instruction's `op_name` (e.g. `aten__cos`) usually appears once or a
few times in `fxgraph.txt` as `target=torch.cos` or similar. For more uniqueness,
match by **shape**:

```bash
# Find FX nodes with the same dtype+shape that produce a cos
grep -nE "torch\.cos|aten\.cos|target=torch\.ops\.aten\.cos" <fxgraph.txt> | head -10
```

## Step 3: FX graph node → model.py

Each FX node in vllm-neuron's dump contains a hint to where it came from:

```
%cos_3 : [num_users=1] = call_function[target=torch.cos](
    args = (%emb,), kwargs = {}
)
```

The variable name `cos_3` is auto-generated; it usually appears in the model
source via the local-variable name, e.g. `cos = emb.cos()` in
`model.py:Gemma4RotaryEmbedding.forward()`.

For Gemma4, the chain is:

| HLO op | Likely model.py site |
|--------|---------------------|
| `aten__cos` shape `[T, head_dim]` | `Gemma4RotaryEmbedding.forward` cos call |
| `aten__sin` shape `[T, head_dim]` | same function, sin call |
| `aten__index_put` `[blocks, ..., head_dim]` | `forward_decode` KV cache scatter |
| `aten__index_select` shape `[B, vocab/TP]` | `Sampler.forward` mask shard |
| `aten__matmul` huge `[T, hidden]` | `lm_head(...)` |

## Step 4: when nothing else works — shape pattern matching

When every metadata layer is empty, fall back to **shape signature** matching.
For Gemma4 specifically:

| Shape signature | Likely site |
|-----------------|-------------|
| `[64, 256]` cos/sin | SWA layer RoPE (`head_dim=256`) |
| `[64, 512]` cos/sin | Global layer RoPE (`head_dim=512`, `partial_rotary_factor=0.25`) |
| `[8192, 1, 16, 256]` fp8 scatter | per-rank KV cache update at TP=16, batch=64 |
| `[64, 16384]` bool gather | logit_mask shard at vocab/TP |
| `[16384, 5376]` bf16 matmul | lm_head per-rank weight (vocab/TP × hidden) |

These are model-specific; build similar tables for other architectures by
reading `config.py` and the model definition.

## Step 5: when source attribution is critical (e.g. for a ticket)

Always include in the ticket:

1. The HLO **id** of the offending op (most stable identifier)
2. The HLO **name** (e.g. `cosine.4502`)
3. The HLO **shape** (lets the model author find it by inspection)
4. The HLO **op_name** if set (e.g. `aten__cos`)
5. The penguin IR **subgraph** it landed in (e.g. `sg0002`)
6. The model.py source location if you can confidently identify it

The compiler team can reproduce from items 1–5 alone; item 6 saves them time.

## Why the metadata is empty

- **vllm-neuron's `convert_fx_to_hlo` lowering** doesn't propagate Python source
  locations; it only uses torch.compile's internal node names.
- **torch_xla in lazy-tensor mode** can populate metadata when `XLA_IR_DEBUG=1`
  and `XLA_HLO_DEBUG=1` are set (see profiling skill). Most production runs
  don't set these.
- **HLO `metadata.stack_frame_id` + `stack_frame_index`** is a newer XLA feature
  that some versions of torch_xla emit; check `m.stack_frame_index.file_names`
  to see if it's populated.

If running a fresh debug compile, set `XLA_IR_DEBUG=1 XLA_HLO_DEBUG=1
NEURON_FRAMEWORK_DEBUG=1` before the run that produces the HLO. That gets
metadata into the HLO at framework lowering time, before the compiler ever
sees it.
