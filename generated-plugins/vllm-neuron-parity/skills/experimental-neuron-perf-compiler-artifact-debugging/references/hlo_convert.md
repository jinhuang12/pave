# Reading HLO

Two paths: `hlo_convert` binary (preferred) and `hlo_pb2` protobuf (always
available).

## Path 1: `hlo_convert` binary

The compiler ships a CLI that round-trips XLA HLO between formats.

```bash
HLO_CONVERT=$(/usr/bin/python -c "import neuronxcc, os; print(os.path.join(os.path.dirname(neuronxcc.__file__), 'starfish/bin/hlo_convert'))")

# proto → text (canonical XLA HLO IR)
$HLO_CONVERT --input graph.hlo --output graph.hlo.txt \
    --input-type proto --output-type text

# proto → text-form proto (less readable, but full proto)
$HLO_CONVERT --input graph.hlo --output graph.hlo.proto.txt \
    --input-type proto --output-type proto_ascii

# StableHLO interop
$HLO_CONVERT --input graph.hlo --output graph.stablehlo \
    --input-type proto --output-type stablehlo
```

Confirm the binary exists before invoking:

```bash
test -x $HLO_CONVERT || echo "hlo_convert missing — falling back to hlo_pb2"
```

If missing, the wheel is one of the dev builds that stripped it. Either roll
back to a release-numbered build (e.g. `2.0.258074` or later) or use Path 2.

## Path 2: `hlo_pb2` protobuf (always available)

The compiler always ships the protobuf definitions even when the binary tool is
absent. This is the safest fallback and also the right tool for programmatic
inspection.

### Load + basic stats

```python
from neuronxcc.thirdparty_libs.xla.service import hlo_pb2

m = hlo_pb2.HloModuleProto()
m.ParseFromString(open("graph.hlo", "rb").read())

print(f"name:           {m.name}")
print(f"computations:   {len(m.computations)}")
print(f"entry id:       {m.entry_computation_id}")
print(f"entry layout:   {m.host_program_shape}")
```

### Find ops by id, name, or opcode

```python
def find_ops(m, *, id_in=None, name_in=None, opcode_in=None):
    """Yield (computation_name, instruction) tuples matching filters."""
    for c in m.computations:
        for ins in c.instructions:
            if id_in is not None and ins.id not in id_in:
                continue
            if name_in is not None and ins.name not in name_in:
                continue
            if opcode_in is not None and ins.opcode not in opcode_in:
                continue
            yield c.name, ins

# Examples
for cn, ins in find_ops(m, id_in={349, 700}):
    sh = list(ins.shape.dimensions)
    print(f"  {ins.name:30} opcode={ins.opcode:10} shape={sh}")

for cn, ins in find_ops(m, opcode_in={"scatter"}):
    operand_ids = list(ins.operand_ids)
    print(f"  {ins.name:30} operands={operand_ids}")
```

### Dump to canonical text form (textproto)

`google.protobuf.text_format` gives the same content `hlo_convert
--output-type proto_ascii` would produce.

```python
from google.protobuf import text_format
with open("graph.hlo.txt", "w") as f:
    f.write(text_format.MessageToString(m))
```

This is **textproto**, not the canonical `HloModule { ... }` IR text. For
that you need `hlo_convert --output-type text` or upstream XLA's
`hlo-translate`.

### Inspect must-alias declarations

The `input_output_alias` map records which output buffers alias which input
slots. KV-cache scatter outputs typically alias their input cache slots with
`kind=MUST_ALIAS=2`.

```python
print("\nKV cache aliases (kind=must-alias):")
for entry in m.input_output_alias.entries:
    if entry.kind == 2:  # MUST_ALIAS
        print(f"  output[{list(entry.output_shape_index)}] "
              f"<- input[{entry.parameter_number}]"
              f"[{list(entry.parameter_shape_index)}]")
```

If the compiler emits `TiledOffloadedMemCpy` despite `must-alias` here, that's
the redundant-copy bug.

### Op metadata

```python
md = ins.metadata
print(f"op_type:     {md.op_type}")        # e.g. 'aten__index_put'
print(f"op_name:     {md.op_name}")        # framework op name
print(f"source_file: {md.source_file}")    # often empty for vllm-neuron
print(f"source_line: {md.source_line}")
```

If `source_file` and `source_line` are empty, see
[source_map.md](source_map.md) for alternative attribution.

## When to use which

| Goal | Use |
|------|-----|
| Quick visual scan of structure | `hlo_convert --output-type text` |
| Programmatic op-by-op analysis | `hlo_pb2` |
| Round-trip into StableHLO for cross-tool work | `hlo_convert --output-type stablehlo` |
| Get aliases / metadata | `hlo_pb2` (text form doesn't always render this nicely) |
