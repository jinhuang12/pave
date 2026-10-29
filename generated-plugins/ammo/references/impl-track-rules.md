# Implementation Track Rules

These are the agent-facing rules for a Stages 4-5 parallel implementation track. The impl-champion owns the track and must follow every rule here. Read it before you edit a source file, and again before you record a gate result. It covers who may change code, when a rebuild is required, the fixed gate sequence, the one-attempt recovery path when per-batch-size results are mixed, and how to name the env flag you add.

## Worktree Build Rules

The champion compiles. The kernel correctness & speedup checks run against the committed, compiled code.

| Change Type | Required Action | Time |
|-------------|----------------|------|
| **Pure Python** (model code, Triton kernels, CuTeDSL kernels, configs) | Edit, test, commit. **No rebuild.** | Immediate (CuTeDSL JIT-compiles on first import; cache under `$CUTE_DSL_CACHE_DIR` or `/tmp/{user}/cutlass_python_cache`) |
| **`csrc/`** (the prebuilt `pumice/_ops` libraries) | The worktree build stages the engine's prebuilt `_ops` libraries and has no `_ops` build step. Do not start a `csrc/` track unless the lead approves a build path. The vLLM runner (gpt-oss on CUDA) does not load `_ops`. | n/a |
| **Pumice Rust engine** (`pumice-rust/src/**`, `pumice-rust/build.rs`, `Cargo.toml`) | From the worktree root: `scripts/ammo pumice-stage-native.sh "$PWD/.venv/bin/python" "$PWD" --features "nixl-stub cmb"`. These are the engine build features; without `cmb`, the server ignores `--cmb-port`. The script runs `cargo build --release` and then stages the new `pumice_rust` extension into `.venv`. A plain `cargo build` does not stage; the server then loads the old extension. `scripts/preflight_pumice.py` checks the cmb-api patch entry (`references/e2e-latency-guide.md` § Troubleshooting). | Minutes when cold. Less with a warm `sccache`. |
| **Pumice confdb TOML** (`pumice-rust/confdb/*.toml`) | Same as the Rust engine row. `build.rs` compiles the TOML files into the engine, so a TOML edit is a source change. | Same as the Rust engine row |

A Rust or confdb edit has no effect until you rebuild and re-stage. The sweep runner refuses to measure a build that is older than its Rust or confdb sources.

Keep `CARGO_HOME`, `CARGO_TARGET_DIR`, `SCCACHE_DIR`, `RUSTC_WRAPPER`, and `RUSTUP_HOME` as they are set, unless the approved task changes them. `references/campaign-env.md` sets `CARGO_TARGET_DIR`. The other four come from your environment.

## Source Modification Rules

- Only the champion modifies source files (`src/pumice/`, `pumice-rust/`, `csrc/`).
- Write your kernel-gate artifacts to `{artifact_dir}/rounds/{CR}/tracks/{op_id}/validator_tests/`, where `{CR}` is `campaign.current_round`. The `validator_tests/` dirname is kept as a historical label because downstream consumers read from this exact path: the state.json merge and the layout manifest (`scripts/artifact_layout.json`).
- Kernel gates hold one assigned unit from `references/gpu-pool.md`.

## Write the Kernel Gates From the Plan

Derive your kernel correctness tests and benchmarks from **the selected proposal and the typed validation obligations**, not from the code you wrote. Test what the optimization SHOULD do (the plan) rather than only what it DOES do (the implementation). Hold yourself to the plan's intent: keep assertions tight, and report benchmark numbers straight.

## Champion-Owned Validation

The champion owns all Stage 5 validation, running the kernel-level gates:

```
Kernel-Level:
  Gate 5.1a: Kernel correctness tests (champion-authored, from the plan)
  Gate 5.2: Kernel speedup benchmark under production parity (boundary: `references/validation-defaults.md` § Gate 5.2)

E2E-Level (Champion):
  Gate 5.3a: opt_profiling/{op_id} (production activation; separate profiling invocation)
  Gate 5.1b: opt_correctness/{op_id} (clean production correctness)
  Gate 5.3b: Sweep E2E latency (per-BS verdicts)
  Writes final validation_results.md with evidence chain
```

For host/dispatch/inter-kernel mechanisms, run the actual-runner boundary A/B as a cheap early gate before the E2E sweep. An ordinary track must confirm its projected magnitude. A `contingent_host_spike` has no projected host-saving magnitude, so its first obligation is instead the minimum reversible production-boundary spike, recorded as its first Gate 5.2 run; it stops unless the occurrence-weighted E2E-equivalent improvement meets or exceeds `campaign.config.min_e2e_improvement_pct` (`references/e2e-delta-math.md` § Pre-Implementation Magnitude: Slice Split).

Run Gate 5.1b and Gate 5.3a as separate sweep invocations. Profiler overhead and trace-buffer growth can invalidate or exhaust a full correctness run, and the sweep script rejects the combined invocation.

## When a Kernel Gate Fails

When a kernel correctness or speedup check fails:

1. Champion records the gate failure details
2. Champion diagnoses root cause
3. Champion fixes implementation, recompiles if needed
4. Champion states the causal hypothesis, runs a focused smoke test, and counts the attempt
5. Champion commits and re-runs the kernel gates (no E2E sweep until 5.1a PASSes)

Never "fix" a failure by loosening your own test. The gate exists to catch a broken kernel before the expensive E2E sweep. A weakened assertion only defers the failure to Gate 5.1b (GSM8K) or 5.3b (latency), where it surfaces anyway. By then you have lost the kernel-level diagnosis you would have had here.

The report generator finds DILUTED_PASS after the sweep, so never design a mechanism toward it (`references/validation-defaults.md` § DILUTED_PASS Ship Path).

## Track State Reconciliation

A failure is not complete until `state.json` is reconciled against the on-disk gate result files, and the orchestrator does that reconciliation. An infrastructure blocker follows the same rule: it is not recorded until the orchestrator reconciles the track entry.

The champion never edits `state.json` directly. It writes the gate artifacts and `rounds/{CR}/tracks/{op_id}/validation_results.md`, and the orchestrator merges them into `campaign.rounds[$IDX].parallel_tracks.tracks[op_id]` at each checkpoint (see `orchestration/parallel-tracks.md` § Reconciliation and Cohort Barrier):

- Gate 5.1a: `rounds/{CR}/tracks/{op_id}/validator_tests/gate_5_1a_results.json`
- Gate 5.2: `rounds/{CR}/tracks/{op_id}/validator_tests/gate_5_2_results.json`
- Gate 5.3b (E2E sweep): `rounds/{CR}/sweeps/opt/{op_id}/e2e_latency_results.json`

The reconciled track entry must carry a `status` and `verdict` that agree with these gate files (`verdict` is `null` for `GPU_BLOCKED`). `GPU_BLOCKED` is a lead-triage blocker, not a terminal pass/fail verdict, so Stage 6 must not count it as complete. Never leave a failed or blocked track at `IN_PROGRESS` with stale verdict fields.

## GATING_REQUIRED Workflow

When per-BS verdicts show mixed results (some PASS + some REGRESSED), the track enters GATING_REQUIRED:

1. Sweep reports per-BS verdict table showing mixed results
2. Champion evaluates gating feasibility (is the dispatch site compatible with a gating mechanism?)
3. If feasible: champion runs crossover probing benchmarks itself
4. Champion runs the kernel sweep + E2E confirmation per `crossover-probing.md`
5. Champion implements gating mechanism per § Batch-Size Dispatch Mechanisms below (decision tree)
6. Champion adds the enable flag, default off. The flag name is the public name of the optimization: derive it from the mechanism per § Env Flag Naming (PR-Ready) below, never from the internal `op_id`.
7. Champion commits gated implementation
8. Champion re-runs the kernel gates on the gated kernel (correctness & speedup)
9. Champion re-runs separate activation profiling, clean correctness, and clean E2E on gated code (5.3a, 5.1b, 5.3b). All BS must be PASS or NOISE
10. If both kernel re-validation and sweep pass: verdict = `GATED_PASS`. If either fails: verdict = `FAIL`.

One gating attempt per track. No nested gating.

The `crossover_probe` validation obligation means the track runs crossover probing when its per-BS verdicts are mixed. All-PASS verdicts satisfy it through the sweep itself.

## Env Flag Naming (PR-Ready)

Every campaign flag you add is a public name. This holds for a GATED_PASS dispatch gate and for any opt-in optimization. The name ships in your diff, it is promoted into `target.json:bench.baseline_env` on SHIP, and the enable instructions copy it verbatim. So the name a reviewer reads is the name you type here.

On the Pumice runner, name the flag `PUMICE_<SCOPE>_<MECHANISM>[_<ARCH>]`. Read it with `os.environ.get` in Python or `env::var` in Rust, and default it off. The bench runner sets it on the opt arm from `target.json` `bench.opt_env` (for example `{"PUMICE_MY_OPT": "1"}`). On the vLLM runner (gpt-oss on CUDA), a `VLLM_*` flag lives in the installed vLLM package, so it ships only through `runtime_pkg.patch`.

- `<SCOPE>`: the model family or subsystem the flag governs (`NEMOTRON3`, `MAMBA2`, `MOE`, `ATTN`). Use the model family when the optimization is checkpoint-specific; use the subsystem when it is general.
- `<MECHANISM>`: what the kernel or dispatch does (`FP8_PREFILL_GEMM`, `GATED_RMS_NORM_FUSION`, `TWO_STREAM`, `SSD_FUSED_STATE`).
- `<ARCH>`: an optional hardware tag when the path is architecture-gated (`SM100`, `SM90`).

Good: `PUMICE_NEMOTRON3_FP8_PREFILL_GEMM_SM100` for a CUTLASS SM100 FP8 dense GEMM on the Nemotron-3 prefill path. Bad: `PUMICE_OP004`. An `op_id` is a campaign tracking handle, so a stranger cannot tell what that flag does or whether it is safe to enable. If you deleted the campaign's `state.json`, the name must still tell a stranger what the optimization does. A name that is only the `op_id` with a prefix is a BLOCKING finding at the Stage 4-5 audit.

**Reserved names:** `PUMICE_PLATFORM` and `PUMICE_VLLM_RUNNER_ONLY` are engine platform names, set by the engine build or the host environment, that the bench runner always keeps (`ENGINE_INFRA_ENV` in `run_pumice_grpc_bench.py`), so do not use them as A/B flags.

## Batch-Size Dispatch Mechanisms (for GATED_PASS Optimizations)

A `GATED_PASS` optimization needs a dispatch mechanism so it activates only at the beneficial batch sizes. Pick the variant that fits the dispatch site's context, using the decision tree below.

### Decision Tree

```
1. Is the dispatch site inside a fullgraph-compiled region (torch.compile fullgraph=True)?
   YES -> Use torch.cond() (Variant 1)

2. Is the dispatch site inside a registered custom op, whose Python body Dynamo does not trace?
   YES -> Use Python if/else on M dimension (Variant 2)
         Sub-decision: Is the threshold architectural or empirical?
           ARCHITECTURAL (e.g., kernel's BLOCK_M determines max M) -> hardcode threshold
           EMPIRICAL (from crossover probing) -> use probed threshold with conservative bias

3. Is the dispatch site at module init time or platform level?
   YES -> Use init-time function pointer selection (Variant 3)
```

### Variant 1: torch.cond (Fullgraph-Compiled Paths)

Use this when `torch.compile(fullgraph=True)` traces through the dispatch site. A standard Python `if` on tensor shape causes graph breaks under fullgraph mode.

```python
# Exemplars: the `torch.cond` row in torch-compile-contract.md § Source Files
condition = input.shape[0] < crossover_threshold
return torch.cond(
    condition,
    optimized_fn,    # Active for BS below threshold
    baseline_fn,     # Fallback for BS above threshold
    (input, weight, *other_args),
)
```

Both branches must return same-shape tensors.

### Variant 2: Python if/else (Inside a Custom Op)

> In the templates below, `{MECHANISM}` stands for everything after `PUMICE_` in the flag name (for example `MOE_TWO_STREAM` or `NEMOTRON3_FP8_PREFILL_GEMM_SM100`), per § Env Flag Naming (PR-Ready) above.

Use this inside a registered custom op, whose Python body Dynamo does not trace. The conditional runs at CUDA graph capture and is frozen there, so replay has no runtime cost. Each batch-size bucket captures a separate graph with the correct branch. A shape `if` in traced `forward()` code bakes at trace time instead (`torch-compile-contract.md` § Invariant 1).

```python
# Two-level dispatch: env flag enables, M-check selects
import os

# Level 1: env flag gate (read once at init, default off)
if os.environ.get("PUMICE_{MECHANISM}", "0") == "1":
    gemm_fn = gated_optimized_gemm
else:
    gemm_fn = default_gemm

# Level 2: M-check inside the custom op (frozen in the CUDA graph per bucket)
def gated_optimized_gemm(layer, x, weight, bias=None):
    M = x.numel() // x.shape[-1]
    if M <= crossover_threshold:  # Beneficial range
        return optimized_kernel(x, weight)
    return default_kernel(x, weight, bias)  # Baseline fallback
```

### Variant 3: Init-Time Function Pointer (Module Load)

Use this for dispatch at module or platform level. It costs nothing per call.

```python
# Pattern: init-time selection, as `dispatch_unquantized_gemm` does in the vLLM runner
def dispatch_kernel():
    if os.environ.get("PUMICE_{MECHANISM}", "0") == "1" and M_typical <= crossover_threshold:
        return optimized_fn
    return default_fn
```

### Priority Dispatch Chain (Overlapping Call Sites)

Use this for the rare case where 2+ gated optimizations share a call site:

```python
AMMO_DISPATCH_CHAIN = [
    # (condition, kernel_fn, name) -- first match wins
    (lambda M: 2 <= M <= 16, fused_qkv_fn, "fused_qkv"),
    (lambda M: 2 <= M <= 32, selective_fn, "selective_gemm"),
]

def ammo_dispatch(layer, x, weight, bias=None):
    M = x.numel() // x.shape[-1]
    for condition, kernel_fn, name in AMMO_DISPATCH_CHAIN:
        if condition(M):
            return kernel_fn(layer, x, weight, bias)
    return default_fn(layer, x, weight, bias)
```

## Stage 1 Baseline Reuse

The rule and the baseline artifact paths live in `validation-defaults.md`
§ E2E Baseline Reuse Requirement.

## Track Constraints

These constraints apply to the champion, including its kernel gates:

1. **All batch sizes.** Test every batch size in target.json. No exceptions. No cherry-picking.
2. **Production parity.** Per `references/validation-defaults.md` § Production Parity Requirement.
3. **Production baseline.** Per `references/validation-defaults.md` § Dual Baseline Requirement. Never use naive PyTorch.

## References

- `validation-defaults.md`: verdict thresholds (noise_tolerance_pct, catastrophic_regression_pct) and per-BS classification logic
- `crossover-probing.md`: crossover probing protocol for GATING_REQUIRED tracks
- `gpu-pool.md`: GPU reservation pattern
- `cudagraph-safety.md`: CUDA graph capture checklist
