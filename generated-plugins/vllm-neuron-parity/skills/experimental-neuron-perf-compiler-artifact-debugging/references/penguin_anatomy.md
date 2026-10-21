# Penguin output anatomy and IR conventions

What's in a `penguin-sg*/` directory after `--internal-compiler-debug-mode=penguin`,
what each file means, and how to read the IR.

## Files emitted per compile

| File / dir | What it is | When to look |
|------------|-----------|--------------|
| `penguin-sgNNNN/penguin.py` | Final pre-tensorizer IR for partition NNNN. **The most useful single file.** | Always. |
| `penguin-sgNNNN/penguin.txt` | Same as `.py` but in legacy text form (older compilers). | If `.py` is missing or for visual comparison. |
| `penguin-sgNNNN/penguin.mlir` | MLIR-affine form (newer compilers route some sg's through MLIR). | When op categories include `m3.AffineLoad`, `m1.Loop`. |
| `penguin-sgNNNN/.git/` | Each compiler pass is a commit. `git log --oneline` lists passes in reverse. | Bisecting which pass crashed or changed IR. |
| `penguin.py.0000NN` | Concatenated dump of all partitions at top-level. | Whole-program grep when sg-id is unknown. |
| `debug_info_penguin.dbg_sgNNNN` | Pass-level debug info (binary). **Missing for the sg that crashed.** | Diagnostic — find which sg's `MaskPropagation` (or other pass) aborted. |
| `debug_info_hlo.dbg_sgNNNN` | HLO-level partitioning info. | When you need the HLO id ↔ sg mapping. |
| `all_metrics.csv` | Per-sg `HloMacCount`, `Traffic`, `ArithmeticIntensity`, `model-type`. | Identifying compute- vs memory-bound regions. |
| `LiveRangeReport_PostHloPart.txt` | SBUF live-range and tensor-lifetime info. | Memory-pressure debugging. |
| `log-neuron-cc.txt` | The full compile log. | Pass-by-pass timing, assertions, statistics. |

## Penguin IR `DebugLocation` fields

Every penguin op carries a `DebugLocation`:

```python
m4.DebugLocation(
    tensor_op_name="_cosine.4502",   # HLO op name (with leading underscore)
    hlo_id=700,                       # HLO instruction id
    file="",                          # framework source file (often empty)
    line=0,                           # framework source line (often empty)
    kernel="",
)
```

- **`tensor_op_name`** ↔ HLO `name` field (strip the leading `_`).
- **`hlo_id`** ↔ HLO `id` field (set by the framework lowering).
- **`file` / `line`** are populated only when the framework emits HLO metadata
  (torch_xla in some build paths does, vllm-neuron + neuronx-cc 2.x typically
  does **not**). When empty, see [source_map.md](source_map.md).

## Penguin IR dialects in modern compilers

Newer neuronx-cc (2.0.258xxx+) routes some subgraphs through an **MLIR-affine**
dialect rather than the legacy Penguin/Starfish IR. Distinguish them by op
signature:

| Dialect | Signature ops |
|---------|--------------|
| Legacy Penguin | `m6.NeuronIndicesAP`, `m2.Loop`, `m5.NeuronSBTensor`, `m11.ALUOpcode`, `m8.TensorTensorOp` |
| MLIR-affine | `m3.AffineLoad`, `m3.AffineStore`, `m3.AffineAccess`, `m1.Loop`, `m5.UnaryOp`, `m5.BinaryOp`, `m5.make_cast` |

Sometimes only one sg in a graph uses the new dialect (e.g. the
`lm_head`/sampler partition). When a crash is sg-specific and only that sg
has a `penguin.mlir` file, the new dialect's lowering is the suspect. See
[crash_triage.md](crash_triage.md) for the bisection recipe.
