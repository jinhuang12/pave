# torch.compile Contract for Kernel Optimizations (Pumice, by runner kind)

Pumice runs model code under torch.compile and CUDA graphs. That machinery imposes assumptions on any model or kernel change you make, and eager-mode testing never shows you when you broke one. Read this before you edit compiled code, and again before you claim a result.

This document defines 6 invariants. Each one gives the rule, what breaks if you violate it, the fix, source paths you can verify in this worktree, and the validation check that proves you complied. After the invariants come the three sanctioned ways to pick a kernel per batch size, the one thing you must never do, and the checklist to run after integration.

## Which Runner You Compile Under

The six invariants hold for both Pumice CUDA runners, because the Pumice compile layer is a port of the vLLM design (`PumiceBackend`, then `PiecewiseBackend`, then deferred `standalone_compile`). The source paths and the log lines are different. Find the runner kind first:

- The server log line `Backends from confdb` and the `model_runner` field of every bench row give the runner kind. confdb sets it per model and SM family (`model_runner = "vllm"` for gpt-oss in `pumice-rust/confdb/gpt_oss.toml`).
- **Runner `vllm`** (gpt-oss on CUDA): the model runs in the embedded vLLM 0.30.0 runner. The compile stack is the vLLM one, under `vllm/compilation/` in the installed package (`$ENGINE_VENV/lib/python3.12/site-packages/vllm/`). That package is pinned (`shared/pumice_pins.py`) and is not in your worktree. Pumice passes compile settings to it through `compilation_config` (`src/pumice/vllm_adapter/bootstrap.py`: the confdb keys `max_cudagraph_capture_size`, `cudagraph_capture_sizes`, and `vllm_cudagraph_mode`). Every `vllm/...` path below applies. Verify each one in the installed 0.30.0 package, not from memory.
- **Runner `pumice`** (the native runner): the compile stack is in your worktree under `src/pumice/compilation/` and `src/pumice/engine/`. Each invariant below gives the Pumice path next to the vLLM path. Cite the path of the runner that the row used.
- **Runner `neuron`**: torch-xla and neuronx-cc compile the graph. This contract does not apply; see `references/accelerator-binding.md`.

`--enforce-eager`, `--no-compile`, and `PUMICE_COMPILE_TARGET=0` turn off parts of this machinery. Use them for diagnosis only. A binding measurement never uses them.

## Quick Reference

```
┌─────────────────────────────────────────────────────────────────────────┐
│ torch.compile Contract, 6 Invariants (vllm and pumice runners)          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. SINGLE TRACE: forward() traced ONCE → ONE FX graph.                │
│     Python `if shape` bakes at trace time. Dead code at other shapes.  │
│                                                                         │
│  2. PER-RANGE VARIATION: Only via InductorPass or _pass_context.       │
│     compile_range.start is a concrete int. NOT model attrs or SymInts. │
│                                                                         │
│  3. MUTATION: mutates_args inside IR op fused impls → stale SSA.       │
│     Always mutates_args=[] + return new tensor.                        │
│                                                                         │
│  4. DATA_PTR: Triton in IR op → data_ptr() crash under make_fx.       │
│     Wrap Triton in direct_register_custom_op.                          │
│                                                                         │
│  5. PARTITIONS: Only splitting_ops create CUDA graph boundaries.       │
│     direct_register_custom_op alone does NOT cause partitions.         │
│                                                                         │
│  6. RANGE OVERHEAD: compile_ranges_endpoints adds ~0.5-1% overhead     │
│     to batch sizes in the smaller range. Not a bug: structural cost.  │
│                                                                         │
│  VALIDATION: accuracy all BS + kernel fires + partition count +         │
│              dispatch correctness + E2E latency + structural overhead   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Invariant 1: Single Trace, Multiple Compilations

Dynamo traces the model `forward()` ONCE and produces ONE FX graph. That single graph is then compiled N times (once per compile range) with different `example_inputs`. So Python control flow in `forward()` evaluates once, at trace time, and its result is baked as a constant into every compiled artifact.

Any Python `if` that depends on tensor shapes, batch size, or runtime state therefore specializes to ONE path. At other shapes the wrong branch executes, which gives you an accuracy regression or a silent correctness failure.

**Source**: `vllm/compilation/piecewise_backend.py`: `self.graph` (singular) is passed to `.compile()` in a loop over `range_entries`:
```python
range_entry.runnable = self.vllm_backend.compiler_manager.compile(
    self.graph,  # <-- SAME graph for all ranges
    args_list,   # <-- different args per range
    compile_range=range_entry.compile_range,
)
```

**Pumice runner**: `src/pumice/compilation/backend.py`, `PiecewiseBackend.compile_all_ranges()`, loops over `self.range_entries` with the SAME `self.graph` and per-range fake args from `get_fake_args_from_graph(self.graph)`.

**Validate**: after integration, run E2E accuracy at ALL batch sizes in the capture list (`cudagraph_capture_sizes` on the vLLM runner, the ladder that `capture_cudagraph()` captures on the Pumice runner). If any BS shows >1pp accuracy regression, suspect trace-time baking.

---

## Invariant 2: Inductor Passes Run Per-Range (The Variation Point)

An Inductor pass is the only place where compiled behavior may differ per range. Before each per-range compilation the engine sets `pass_context(compile_range)`, and all Inductor passes (including IR lowering) run inside that context. There, `get_pass_context().compile_range.start` and `.end` are **concrete integers**, so they are the ONLY safe way to vary compiled behavior per range.

Three tempting mechanisms do not work. A Python bool set at model construction gives one value for all ranges, because the model is constructed once. Fake tensor shapes during lowering may be SymInts for multi-size ranges. Module attributes are the same object across all compilations.

**Source**:
- `vllm/compilation/backends.py`: `with pass_context(compile_range):`
- `vllm/compilation/passes/inductor_pass.py`: `get_pass_context()` / `_pass_context` implementation
- `vllm/compilation/passes/pass_manager.py`: consumed during pass execution

**Pumice runner**:
- `src/pumice/compilation/backend.py`: `PiecewiseBackend._compile_range()` runs inside `with pass_context(range_entry.compile_range):`. `PumiceBackend._compile_single_graph()` has the same wrap.
- `src/pumice/compilation/passes/pass_context.py`: `PassContext`, `get_pass_context()`, and the `pass_context(compile_range)` context manager.
- `src/pumice/compilation/passes/pass_manager.py`: `PumicePostGradPassManager` calls `is_applicable_for_range` for each pass and each range.

**Validate**: if your mechanism uses `_pass_context.compile_range.start`, write a smoke test that exercises the gate under each compile range. Confirm it returns the expected value for both small and large ranges.

---

## Invariant 3: In-Place Mutation Goes Stale Under auto_functionalize_v1

Keep custom ops functional. Set `mutates_args=[]` and return a new tensor. vLLM uses `enable_auto_functionalized_v2=False` (v1). Under v1, an in-place mutation inside a nested custom op (for example, an opaque op called from within an IR op's fused impl) is NOT correctly propagated back into the surrounding FX graph's SSA form. The mutated tensor becomes stale, and all downstream ops use pre-mutation data.

`mutates_args=["residual"]` on a custom op called inside an IR op impl is the classic violation: the residual stream goes stale, which costs you catastrophic accuracy loss.

```python
# WRONG: stale SSA reference under auto_functionalize_v1
@torch.library.custom_op("vllm::my_op", mutates_args=["residual"])
def bad(x, residual, ...):
    residual.add_(x)  # mutation not propagated
    return output

# CORRECT: functional, returns new tensor
@torch.library.custom_op("vllm::my_op", mutates_args=[])
def good(x, residual, ...):
    new_residual = residual + x
    return output, new_residual
```

**Pumice runner**: `src/pumice/engine/model_runner.py` sets `"enable_auto_functionalized_v2": False` in the Inductor options. Register ops with `direct_register_custom_op("my_op", impl, mutates_args=[])` (`src/pumice/utils/custom_ops.py`, namespace `pumice::`). See also `src/pumice/compilation/passes/inplace_functionalization.py` and `fix_functionalization_pass.py`.

**Validate**: run E2E accuracy after you implement. A >1pp regression that disappears under eager mode (`--enforce-eager`, diagnostic only) points at mutation propagation. Check that no `mutates_args` are used inside IR op impls.

---

## Invariant 4: Wrap Triton Before an IR Op Calls It (data_ptr() vs make_fx)

You cannot directly inline a Triton kernel into an IR op impl. Triton kernels call `tensor.data_ptr()` for pointer arguments, and Inductor's `make_fx` tracing (used by `replace_by_example` during IR lowering) uses FunctionalTensor, which doesn't support `data_ptr()`. Call a Triton kernel directly inside an IR op fused impl and you get `RuntimeError: data_ptr() not available on FunctionalTensor`.

Wrap the Triton kernel in `direct_register_custom_op` with a `register_fake`:
```python
@torch.library.custom_op("vllm::my_triton_wrapper", mutates_args=[])
def wrapper(x: torch.Tensor, w: torch.Tensor, ...) -> tuple[torch.Tensor, torch.Tensor]:
    # data_ptr() is fine inside custom op body (not traced by make_fx)
    return my_triton_kernel_launcher(x, w, ...)

@wrapper.register_fake
def wrapper_fake(x, w, ...):
    return torch.empty_like(x, dtype=torch.float8_e4m3fn), torch.empty(...)
```

Then call `wrapper()` from your IR op fused impl. Inductor treats the custom op as an opaque node but can still inline the surrounding IR op body.

**Source**: `vllm/utils/torch_utils.py`: `direct_register_custom_op` implementation.

**Pumice runner**: `src/pumice/utils/custom_ops.py`, `direct_register_custom_op(name, impl, mutates_args=[], fake_impl=...)` on `Library("pumice", "FRAGMENT")`. The op appears as `torch.ops.pumice.<name>`. IR lowering uses `match.replace_by_example` in `src/pumice/compilation/passes/ir_lowering_pass.py`.

**Validate**: if IR op lowering fails with `data_ptr()` errors, wrap the Triton call in an opaque custom op.

---

## Invariant 5: CUDA Graph Partition Boundaries (splitting_ops)

Only ops in `splitting_ops` create partition boundaries. That set controls CUDA graph piecewise splitting by forcing graph boundaries, and `direct_register_custom_op` does NOT automatically add your op to it.

If your custom op IS in `splitting_ops` (or it triggers a code path that hits a splitting op), CUDA graph partitions multiply. Each partition boundary adds replay overhead that can cancel your kernel savings.

**Source**: `vllm/config/compilation.py`: `splitting_ops` defaults to `list(self._attention_ops)` (attention ops only). Check what's in the set:
```bash
grep -n "splitting_ops\|_attention_ops" vllm/config/compilation.py
```

**Pumice runner**: `PumiceBackend.splitting_ops` in `src/pumice/compilation/backend.py` (at 9d126713: `pumice::unified_attention` and `pumice::glm_mla_attention`). The default path is an FX split (`split_graph()`). `PUMICE_INDUCTOR_GRAPH_PARTITION=1` selects the Inductor partition path instead.

**Validate**: compare CUDA graph partition count before/after your change:
```bash
# vLLM runner:
grep "PIECEWISE=\|FULL=" <server_log>
# Pumice runner:
grep "Split graph into\|graphs captured" <server_log>
# If your change increases the partition, piece, or captured-graph count, investigate.
```

---

## Invariant 6: compile_ranges_endpoints Structural Overhead

`compile_ranges_endpoints=[N]` splits compilation into multiple ranges, and the split itself costs latency. It adds an additional Inductor compilation per piecewise subgraph, additional CUDA graph captures for batch sizes in the new range, and a small but measurable overhead (~0.5-1.0%) for batch sizes in the smaller range.

Nothing breaks functionally. But batch sizes that land in the smaller range may show a latency regression from the mechanism itself, even if no kernel code changes apply to that range.

**Pumice runner**: `compile_ranges` defaults to one `Range(1, max_num_batched_tokens)` (`src/pumice/engine/model_runner.py`). The only split at 9d126713 is the AllReduce+RMSNorm fusion range. Add an endpoint there only when you need one.

**Validate**: run E2E latency at ALL batch sizes. If a BS in the "unmodified" range regresses by 0.5-1.0%, this is likely structural overhead from the range split itself, not a bug in your kernel code.

---

## How to Dispatch on Batch Size

Three mechanisms are sanctioned for shape-dependent dispatch: different kernel paths for different batch sizes under torch.compile. The examples use vLLM paths. The Pumice runner equivalents are in § Source Files. Choose by the overhead you can pay and by how much graph work you want to do.

### Option 1: `torch.cond` (Simplest, ~2-4 µs overhead)

Use `torch.cond` when both branches return same shape/dtype and ~2-4 µs per call is acceptable. Production example at `vllm/model_executor/kernels/linear/scaled_mm/flashinfer.py`:

```python
condition = input.shape[0] < 32
return torch.cond(
    condition,
    run_flashinfer_swapAB,     # M < 32 path
    run_deepgemm,               # M >= 32 path
    (input, weight, weight_scale),  # ALL inputs as positional args
)
```

Pass ALL inputs (including weights) as positional args. Module closures (`self.linear.weight`) cause Dynamo Parameter-lifting failure → compile error.

**Does NOT create partition boundaries.**

---

### Option 2: `InductorPass.is_applicable_for_range` (Zero overhead, graph rewrite)

Use an Inductor pass when you need different graph structure per range and are comfortable writing Inductor passes.

```python
# Pattern from vllm/compilation/passes/fusion/collective_fusion.py
class MyPass(VllmInductorPass):
    def is_applicable_for_range(self, compile_range: Range) -> bool:
        return compile_range.start >= 32

    def __call__(self, graph: fx.Graph) -> None:
        # Pattern-match and rewrite graph nodes; only runs for applicable ranges
        ...
```

**Production uses**: `collective_fusion.py`, `sequence_parallelism.py`, `allreduce_rms_fusion.py`, `rope_kvcache_fusion.py`.

---

### Option 3: vLLM IR Op + `_pass_context.compile_range` (Zero overhead, impl dispatch)

Use an IR op when you have native + fused impls of the same op and need zero-overhead per-range dispatch. The fused impl is inlined into the FX graph at lowering time via `replace_by_example`, so it costs zero runtime overhead.

```python
from vllm.compilation.passes.inductor_pass import _pass_context

def _fused_supports_args(x, residual, norm_w, eps, weight, weight_scale, ...):
    """Gate fused impl on compile range."""
    if _pass_context is None:
        return False  # eager mode → always native (fail-safe)
    return _pass_context.compile_range.start >= 32
```

**Implementation checklist**:
1. Register IR op with native + fused impls
2. Fused `supports_args` checks `_pass_context.compile_range.start >= N` (concrete int, NOT fake tensor shapes, which may be SymInts)
3. Wrap Triton kernel in `direct_register_custom_op` with `mutates_args=[]` (Invariant 3 + 4)
4. Return mutated tensors as outputs (never in-place)
5. Set `compile_ranges_endpoints=[N-1]` in compilation config
6. Test dispatch under each range with a smoke test

---

### NEVER: Python `if tensor.shape[0] < N` in compiled forward

Bakes at trace time. One path for all shapes. Wrong results guaranteed at non-traced batch sizes.

---

## The Compile Cache Can Hide Your Edit

A warm compile cache can serve an artifact that was compiled before your edit.

- **Pumice runner**: `CompilerCache` (`src/pumice/compilation/backend.py`) keeps artifacts under `$PUMICE_CACHE_ROOT/torch_compile_cache/<hash>/`. The hash covers the Inductor options, the compile-relevant env, the torch/CUDA version, the GPU arch, the source revision, and the contents of `src/pumice/`, `csrc/`, and `pumice-rust/confdb/`. An edit outside those roots does not change the hash. A Rust edit under `pumice-rust/src/` needs a rebuild, which is a different hazard (the runner freshness gate).
- **vLLM runner**: the vLLM compile cache lives under the cache root that the runner sets. Do not assume that its hash sees your worktree edits.
- On both runners, use `--fresh-cache` for gate-quality sweeps. To prove that a compile-time mechanism fired, force a cold compile with `PUMICE_DISABLE_COMPILE_CACHE=1` on a diagnostic launch.

---

## Post-Integration Validation Checklist

| Check | Method | Pass Criteria |
|-------|--------|---------------|
| Accuracy all BS | E2E accuracy at each BS in campaign | < 1pp regression from baseline |
| Kernel fires | nsys trace, grep kernel name | Kernel present with expected instance count |
| Partition count | Server log `PIECEWISE=` / `FULL=` (vLLM runner) or `Split graph into` + `graphs captured` (Pumice runner) | Unchanged from baseline |
| Dispatch correctness | Smoke test `supports_args` under each range | Expected True/False per range |
| E2E latency | Clean sweep (no nsys), production workload | ≥ threshold at target BS |
| Structural overhead | Latency at BS in smaller range | < 1% regression acceptable |

---

## Source Files

vLLM paths are relative to the installed vLLM 0.30.0 package. Pumice paths are relative to your worktree.

| Component | vLLM runner (vLLM 0.30.0) | Pumice runner |
|-----------|---------------------------|---------------|
| Single graph compiled per range | `vllm/compilation/piecewise_backend.py` | `src/pumice/compilation/backend.py` (`PiecewiseBackend.compile_all_ranges`) |
| `pass_context(compile_range)` | `vllm/compilation/backends.py` | `src/pumice/compilation/backend.py` (`_compile_range`) |
| `get_pass_context()` / `_pass_context` | `vllm/compilation/passes/inductor_pass.py` | `src/pumice/compilation/passes/pass_context.py` |
| Pass context consumption | `vllm/compilation/passes/pass_manager.py` | `src/pumice/compilation/passes/pass_manager.py` |
| `splitting_ops` definition | `vllm/config/compilation.py` | `src/pumice/compilation/backend.py` (`PumiceBackend.splitting_ops`) |
| `direct_register_custom_op` | `vllm/utils/torch_utils.py` | `src/pumice/utils/custom_ops.py` |
| `torch.cond` production usage | `vllm/model_executor/kernels/linear/scaled_mm/flashinfer.py` | `src/pumice/layers/quantization/deepseek_v4_fp8.py` |
| `is_applicable_for_range` pattern | `vllm/compilation/passes/fusion/collective_fusion.py` | `src/pumice/compilation/passes/allreduce_rms_fusion.py` |
| Compile settings from confdb | n/a | `src/pumice/vllm_adapter/bootstrap.py` (`compilation_config`) |
| Inductor options + `compile_ranges` | `vllm/config/compilation.py` | `src/pumice/engine/model_runner.py` |
