# Inspection recipes & common pitfalls

Quick recipes for poking at HLO / penguin IR after the artifacts are
generated. Use this when you know what artifact you have and need a
one-liner to extract a specific signal.

## HLO via proto (no `hlo_convert` needed)

```python
from neuronxcc.thirdparty_libs.xla.service import hlo_pb2
m = hlo_pb2.HloModuleProto()
m.ParseFromString(open("graph.hlo", "rb").read())
print(f"{m.name}, {len(m.computations)} computations")

# Find ops by HLO id (e.g. ids 349, 700)
for c in m.computations:
    for ins in c.instructions:
        if ins.id in {349, 700}:
            sh = list(ins.shape.dimensions)
            print(f"{ins.name:25} opcode={ins.opcode} shape={sh} id={ins.id}")
```

For converting HLO proto to text, see [hlo_convert.md](hlo_convert.md).

## Penguin sg pass history

```bash
cd <penguin_dump>/penguin-sg0002
git log --oneline                # newest → oldest pass commits
git log --oneline --reverse      # init → latest
git show <pass_hash>:penguin.py  # IR after that pass
```

The crashing sg is the one *missing* a `debug_info_penguin.dbg_sgNNNN`
file at the top level. Inside that sg, the next pass after `git log -1`'s
output is the one that crashed — see [crash_triage.md](crash_triage.md).

## Count IR ops by class (detect dialect or fusion changes)

```bash
grep -oE '\bm[0-9]+\.\w+\b' penguin-sg0002/penguin.py \
    | sort | uniq -c | sort -rn | head
```

Spike in `m3.Affine*` → sg routed through MLIR-affine dialect; spike in
`m5.TiledOffloadedMemCpy` → redundant-copy bug (next recipe).

## Detect redundant `TiledOffloadedMemCpy` (must-alias bug)

```bash
grep -c "TiledOffloadedMemCpy" penguin-sg*/penguin.py
# > 0 in any sg → redundant copy present
# bytes/step = count × free_indices_tripcount × dtype_bytes
```

Indicates the compiler is making a copy where `must-alias` should have
elided one — usually a consequence of an aliasing rewrite that the
backend didn't honor. Drill into [dma_redundancy.md](dma_redundancy.md).

## Count HLO `must-alias` entries

```bash
head -1 graph.hlo.txt | grep -oE 'must-alias' | wc -l
```

For hybrid KV cache models, this should equal the number of unique typed
KV cache views the framework binds. See
[kv_cache_memory_model.md](kv_cache_memory_model.md) for what "unique
typed view" means at each of the three sharing layers.

## Find which sg crashed

```bash
ls debug_info_penguin.dbg_sg*    # list those that succeeded
ls -d penguin-sg*                 # list all sgs
# The sg dir without a matching debug_info_penguin.* is the crashing one.
```

## Common pitfalls

- **Cache hashes don't change with compiler-content if version string is identical.**
  Two `0.0.0.0+6f4dba590d` builds with different binaries hash to the same key.
  Use `VLLM_NEURON_DISABLE_COMPILE_CACHE=1` to force fresh compile, or ensure
  the wheel reports a different version string.
- **`metadata.source_file=""` in HLO is normal for vllm-neuron.** Use
  [source_map.md](source_map.md) for a chain that works without it.
- **Penguin compile is slow.** Plan ~3–5 min for 4-layer decode HLOs; 10–15
  min for full 60-layer HLOs. Run in background.
- **Some dev wheels strip `hlo_convert`.** Versions like `0.0.0.0+4cec6732ab`
  shipped without it; release-numbered builds (e.g. `2.0.258074`) include
  it. Roll back if you need the tool.
- **Don't compile inside a production cache dir.** The compile dumps tons
  of files in cwd. Always `cp graph.hlo` to a fresh `/mnt/data/penguin_*/`
  dir first.
- **Always lift compile flags from `command.txt`.** Hand-writing the
  `neuronx-cc compile ...` invocation drops framework-emitted flags like
  `--internal-hlo2tensorizer-options=...--experimental-unsafe-fp8e4m3fn-as-fp8e4m3`,
  causing spurious `NCC_EVRF051` / `NCC_EARG002` errors that are not the
  real bug. The Quick start in SKILL.md walks through this.

## Recording findings

After running a workflow, write findings into a markdown file in the artifact
dir (e.g. `<DBG>/FINDINGS.md`). Structure:

1. **One-line verdict** — what changed / why it crashed / where the time goes.
2. **Evidence table** — penguin op counts, byte counts, before/after metrics.
3. **Source map** — `penguin tensor_op_name → HLO id → framework source` chain.
4. **Action item** — what to file in a support ticket, or what model code to
   change.
