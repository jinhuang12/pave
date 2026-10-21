# Diagnosing redundant DMA / KV cache copies

The most impactful framework-side perf bug we've seen is the compiler emitting
**full-tensor copies** for ops that should be in-place updates (e.g. the
`scatter` HLO op that maintains the KV cache during decode). The HLO declares
`must-alias` between input and output buffers, but the lowering ignores it and
materializes a fresh intermediate.

## Step 1 — measure

Look at decode-NEFF Summary stats — large `hbm_write_bytes` with no
algorithmic justification is the headline signal.

```python
import pandas as pd
s = pd.read_parquet(".../Summary.parquet").iloc[0]
print(f"total_time:               {float(s['total_time'])*1e6:.0f} us")
print(f"hbm_write_bytes:          {float(s['hbm_write_bytes'])/1e6:.1f} MB")
print(f"hbm_read_bytes:           {float(s['hbm_read_bytes'])/1e6:.1f} MB")
print(f"dma_transfer_total_bytes: {float(s['dma_transfer_total_bytes'])/1e6:.1f} MB")
```

Per-decode-step writes that exceed the model output size by a large factor
(>10×) are suspicious. A clean must-alias should write only the new entries
(e.g. `64 tokens × head_dim × layers × dtype_bytes` ≈ 100s of KB to a few MB).

## Step 2 — confirm in penguin IR

```bash
grep -c "TiledOffloadedMemCpy" <DBG>/penguin-sg*/penguin.py
```

Any count > 0 means the compiler emitted `input → intermediate` full-cache
copies. Each op typically moves an entire KV cache slice.

```bash
# Show the sites
grep -n "TiledOffloadedMemCpy" <DBG>/penguin-sg0000/penguin.py | head -5
```

A typical offending line:

```python
v218 = m16.TiledOffloadedMemCpy(
    srcs=[NeuronIndicesAP(shape=(1, 16777216), tensor=v214 [input cache], ...)],
    dst=NeuronIndicesAP(shape=(1, 16777216), tensor=v216 [intermediate "scatter.1"], ...),
    dtype="float8e4",
    dl=DebugLocation(tensor_op_name="aten__index_put_scatter.462", hlo_id=242, ...)
)
```

Note: `tensor_op_name="aten__index_put_scatter.*"` — XLA's HLO `scatter` op
that should be in-place but got materialized.

## Step 3 — confirm HLO declares must-alias

```python
from neuronxcc.thirdparty_libs.xla.service import hlo_pb2
m = hlo_pb2.HloModuleProto()
m.ParseFromString(open("graph.hlo", "rb").read())

print("\nKV cache aliases (must-alias):")
for entry in m.input_output_alias.entries:
    if entry.kind == 2:  # MUST_ALIAS
        print(f"  output[{list(entry.output_shape_index)}] "
              f"<- input[{entry.parameter_number}]"
              f"[{list(entry.parameter_shape_index)}]")
```

If aliases are present **and** `TiledOffloadedMemCpy` ops are still emitted,
the compiler is ignoring the directive. That's a compiler-side bug.

## Step 4 — quantify cost per step

Per-IR-op bytes = `free_indices_tripcount × dtype_bytes`:

| dtype | bytes/elem |
|-------|-----------:|
| `float8e4` (fp8) | 1 |
| `bfloat16` | 2 |
| `float16` | 2 |
| `float32` | 4 |
| `int32` | 4 |

So `shape=(1, 16777216)` of `float8e4` = 16 MB; same shape `bfloat16` = 32 MB.

Per step:
```
total_redundant_bytes = (sum of bytes/op across all sg's)
                       × (hw expansion factor — usually ~1, sometimes 2× from sub-tile DMA)
```

Cross-check with runtime: per-instruction breakdown filtered to the
`bir_instruction_name` prefix that came from the offending IR op (visible in
the penguin IR's `id=` field).

```python
inst = pd.read_parquet(".../Instruction.parquet")
target = inst[inst['bir_instruction_name'].fillna('').str.startswith('I-1403-')]
print(f"  count:     {len(target)}")
print(f"  active_us: {target['duration_ns'].sum()/1e3:.1f}")
print(f"  by op:     {target.groupby(['engine','opcode']).size().to_dict()}")
```

## Step 5 — cross-check on a fixed compiler

If a newer compiler claims to fix this, re-run penguin compile on the same HLO
with that compiler and re-check:

```bash
# Should now be 0
grep -c "TiledOffloadedMemCpy" <DBG_NEW>/penguin-sg*/penguin.py
```

The replacement pattern (correct lowering) writes directly to an `output*`
named tensor:

```python
# CORRECT: scatter writes directly into aliased output buffer
v883 = m4.Tensor(name="output1", shape=(8192, 1, 16, 256), dtype="float8e4", ...)
v876 = m8.NeuronIndirectSave(
    src=<scattered values>,
    dst=NeuronIndirectAP(tensor=v883, ...),  # direct alias-target
    dl=DebugLocation(tensor_op_name="aten__index_put_scatter.462", ...)
)
```

No intermediate `scatter.N` tensor; no `TiledOffloadedMemCpy`.

## Common offenders beyond KV cache

The same pattern shows up for any HLO op that produces a tensor aliased to an
input. Watch for:

| HLO op | Typical use | When it gets materialized |
|--------|-------------|---------------------------|
| `scatter` | KV cache update, gather-scatter sampler | `aten__index_put` |
| `dynamic-update-slice` | sliding window cache, position embeddings | `aten__index_copy` |
| `select` over aliased operand | masked update | `aten__where` with in-place hint |

If a runtime profile shows large `hbm_write_bytes` and the framework code
looks like an in-place update, run this whole flow.

## Reporting template

When filing a ticket, include:

1. HLO file + the alias map dump
2. Penguin IR snippet showing the `TiledOffloadedMemCpy` instance
3. Decode-NEFF Summary.parquet `hbm_write_bytes` before/after a known-good
   compiler version, if available
4. Per-instruction byte/latency breakdown filtered to the offending bir id
5. End-to-end throughput delta (bench_results.json before/after)
