# E2E Latency Benchmark Guide (Pumice) for Kernel Optimizations

Read this before you measure any end-to-end latency number. It tells you how to run the Pumice E2E benchmarks and how to read what they print. Use them to show that a kernel optimization improves *real* inference latency under **production parity**:
- CUDA graphs enabled (or the exact mode used in production)
- torch.compile enabled (or the exact mode used in production)
- same TP/DP/EP topology and serving knobs
- the engine built from the tree that you attribute the result to

Default gates and required reporting are not in this file. They live in `references/validation-defaults.md`.

## Contents
- What "E2E" means here
- Tool Selection (which invocation each stage uses)
- Quickstart (baseline vs optimized)
- The freshness gate (read before your first sweep)
- Workload selection (decode-heavy vs prefill-heavy)
- Batch-size sweep
- Parity checklist (must match baseline)
- Interpreting output + speedup math
- Troubleshooting
- Recording results

## What "E2E" means here

Pumice is a **server**. `run_pumice_grpc_bench.py` launches the server (`rust_grpc_server.py`, or the `pumice-server` binary), waits on the `HealthCheck` RPC, and then sends load over the `Generate` streaming RPC. The client tokenizes the prompts.

One **wave** is one benchmark iteration: `batch_size` requests that the runner submits at the same time. Wave latency is `max(complete_ts) - min(submit_ts)`, the wall-clock time of the batch. For each bucket the runner writes:

```json
{"avg_latency": <mean wave seconds>,
 "latencies": [<per-wave seconds>, ...],
 "percentiles": {"10": ..., "25": ..., "50": ..., "75": ..., "90": ..., "99": ...}}
```

`avg_latency` is the authoritative E2E scalar for every gate and every speedup calculation. The per-request stream timestamps also give the phase split: `prefill_s = first_token_ts - submit_ts` and `decode_s = last_token_ts - first_token_ts`. These fill `prefill_avg_s`, `decode_avg_s`, and `decode_share_of_e2e`.

Every row also records `host`, `gpu`, `model_runner`, `vllm_version`, and `torch_version`. `model_runner` is the runner that confdb selected for the model (for gpt-oss on CUDA it is `vllm`, the embedded vLLM 0.30.0 runner). On Neuron, `model_runner` is `neuron` and `vllm_version` is null. Rows from an L40S (sm_89) host are labelled "harness validation only". Do not use them for a kernel speedup claim.

## Tool Selection

Pick the invocation that matches your stage.

| Stage | Tool | Why |
|-------|------|-----|
| Stage 1 (baseline) | `run_pumice_grpc_bench.py --round {N} --slot baseline --labels baseline --capture-golden-refs` | Clean E2E timing. **No profiling flags are allowed** (guard enforced). These are the authoritative numbers for speedup calculations. |
| Stage 1 (profiling) | `run_pumice_grpc_bench.py --round {N} --slot profiling --nsys-profile --nsys-mode node --warmup-waves 1 --profile-waves 1` | The runner wraps the server in nsys with `--capture-range=cudaProfilerApi`. The `StartProfile` and `StopProfile` RPCs open and close the capture window. The runner resolves `--nsys-trace` from `target.hardware` (`cuda-sw` on Blackwell). E2E numbers from this slot have profiler overhead and are not used for comparisons. See `nsys-profiling-guide.md`. |
| Stage 5 (validation) | `run_pumice_grpc_bench.py --round {N} --slot opt/{op_id} --labels opt --baseline-from $STAGE1_DIR --fresh-cache` | GPU-locked optimized-only sweep, compared against the Stage 1 baseline. Never re-run a baseline from the worktree (§ E2E Baseline Reuse in `validation-defaults.md`). |
| Stage 6 (integration) | `run_pumice_grpc_bench.py --round {N} --slot integration --labels opt --baseline-from $STAGE1_DIR --fresh-cache` (round >= 2 adds `--baseline-from-arm opt`) | Gate-quality combined measurement vs the Stage 1 baseline when two or more tracks pass; a single unchanged passer uses the short-circuit. EXHAUSTED rounds skip it. Round >= 2 MUST import the promoted arm. If it does not, the delta is cumulative, not incremental. See § Combined Validation Workflow in `orchestration/integration-logic.md`. |

For all measurements reported in `validation_results.md` or used for profiling, use the runner. A server that you start by hand plus a client that you write by hand is not a measurement. It skips the arm env sanitization, the freshness gate, the prefix-cache regime check, and the output schema that every downstream consumer reads.

## Quickstart

Run the runner from the worktree root, and always pass the artifact directory explicitly:

```bash
# Stage 1a: Clean E2E baseline (no profiling, authoritative timing)
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round 1 --slot baseline --labels baseline \
  --capture-golden-refs

# Stage 1b: Profiling traces (separate invocation, so the overhead does not pollute the baseline)
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round 1 --slot profiling --labels baseline \
  --nsys-profile --nsys-mode node --warmup-waves 1 --profile-waves 1

# Stage 5: Per-track validation sweep (opt-only vs Stage 1 baseline, isolated cache)
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round {N} --slot opt/{op_id} \
  --labels opt --baseline-from {artifact_dir}/rounds/{N}/sweeps/baseline --fresh-cache

# Stage 6: Integration sweep (--fresh-cache for gate-quality measurement)
# Round >= 2 MUST append `--baseline-from-arm opt` to the command below: the
# comparator is then the promoted arm, not the stale pre-SHIP baseline arm of
# that directory. Without it the measured delta is cumulative, not incremental.
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round {N} --slot integration \
  --labels opt --baseline-from {artifact_dir}/rounds/{N}/sweeps/baseline --fresh-cache

# Post-SHIP: Golden-refs capture after environment promotion
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round {N} --slot golden_capture \
  --labels baseline --capture-golden-refs
```

Do not put the workload on the command line. The runner reads the model path, workload, server flags, and env config from `target.json`, so you never need `--model`, `--batch-size`, or the rest. The runner always passes `--tensor-parallel-size`, `--max-model-len`, and `--metrics-port` to the server. The prompt source follows the same rule: the runner reads `bench.prompt_source` when its `--prompt-source` flag is absent (freeze it with `new_target.py --prompt-source raw` when `input_len` cannot hold the chat template, see § Prompt Source in `workload-invocation.md`); pass the flag by hand only to override one run.

### Prefix-cache regime

Both arms of a comparison use the same prefix-cache regime. `target.json` records it, and the runner passes the same `--enable-prefix-caching` or `--no-enable-prefix-caching` flag to both servers. The flag overrides the confdb value. For each arm the runner checks three things: both argvs carry the flag, the server log shows the line `Prefix caching launch override: X -> Y`, and the `--metrics-port` counters `prefix_cache_hits_total` and `prefix_cache_queries_total` give the same hit ratio within 1 pp (or zero queries on both arms when the regime is off). A mismatch fails closed. Do not "fix" it by editing one arm.

### Fresh-cache isolation (`--fresh-cache`)

This flag keeps a sweep from using warm compile caches from a previous sweep. It allocates `{out_root}/cache/{sweep_id}/` and sets `PUMICE_CACHE_ROOT` and `TRITON_CACHE_DIR` in the env of the server. The cache is removed on success. The first server launch pays the full compile (minutes for large models). Later launches in the same sweep use the warm in-sweep cache.

The runner sets the cache root AFTER `_sanitize_engine_op_env`. The order is necessary: `PUMICE_CACHE_ROOT` matches the `^PUMICE_` sanitizer prefix, so an earlier injection is removed again.

## The freshness gate (read before your first sweep)

Before it measures, the runner compares the newest mtime of the engine sources against the built engine artifacts. The sources are `pumice-rust/src/**/*.rs`, `pumice-rust/confdb/*.toml`, `build.rs`, both `Cargo.toml` files, and `$CARGO_HOME/config.toml`. The artifacts are the staged `pumice_rust` `.so` in the venv, then the release directory. If a source is newer, the runner **refuses to measure**:

```
REFUSING TO MEASURE: engine freshness check failed (...)
```

This is not a harness bug. It tells you that your Rust or confdb edit is not in the binary. Rebuild and re-stage the engine from the worktree root:

```bash
# CUDA sessions:
pumice-stage-native.sh .venv/bin/python "$PWD" --features "nixl-stub cmb"
# Neuron sessions:
pumice-stage-native.sh .venv/bin/python "$PWD" --features nixl-stub
```

Use the same cargo features as the image build. If you leave out `cmb` on CUDA, the server ignores `--cmb-port`. The script runs `cargo build --release` for `pumice-rust/` and copies the new `pumice_rust` `.so` into the venv. The session bootstrap sets `CARGO_HOME`, `CARGO_TARGET_DIR`, `SCCACHE_DIR`, and `RUSTC_WRAPPER` for your session. Do not change them.

**cmb-api note.** `pumice-rust/Cargo.toml` depends on the internal crate `amzn-mantle-cmb-api`, and Pumice ships no `Cargo.lock`. So cargo must resolve this crate on every build, also when the `cmb` feature is off. The fix is a `[patch.crates-io]` entry in `$CARGO_HOME/config.toml`, not an edit to the Pumice `Cargo.toml`:

```toml
[patch.crates-io]
amzn-mantle-cmb-api = { path = "/home/ubuntu/AWSMantle/share/common/cmb-api" }
```

The session image writes this entry into `/opt/cargo/config.toml`, and the bootstrap copies that file into your session `CARGO_HOME`. If cargo stops with `no matching package named amzn-mantle-cmb-api`, the patch is missing from the active `$CARGO_HOME/config.toml`. Outside the session image, write the same entry into your own `$CARGO_HOME/config.toml` before the first build. Never edit `Cargo.toml` to fix this, and never commit a `Cargo.lock` for it.

`--allow-stale-engine` is only for infrastructure debugging. It writes `freshness.overridden=true` into the output JSON, so the evidence shows the override. A number that you measure with it after a Rust or confdb edit is not evidence.

Python-only edits (`src/pumice/**`) do not need a rebuild. The editable install makes them live at the next server launch.

## Workload selection

### Decode-heavy (recommended for decode-bucket optimizations)

Benchmark decode-heavy when the optimization targets **decode buckets** (small `M` per step). Many optimization fast-paths are tuned for those buckets, and a decode-heavy workload makes that visible:

- short `input_len` (for example 64)
- long `output_len` (for example 512)
- sweep your decode bucket `batch_sizes` set

Set these in `target.json:workload` through `new_target.py`, not on the runner command line.

### Prefill-heavy (optional)

Run a second, prefill-heavy sweep if you claim a prefill win: a large input length, and usually a smaller output length. Keep those results separate from the decode-heavy results.

## Batch-size sweep

Sweep the **same bucket set** you profiled in Stage 1 and plan to enable in Stage 6.

The runner sweeps all buckets in one invocation and launches the server once per label. Pass `--round {N} --slot {SLOT}` so results land in the canonical round-scoped layout:

```bash
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} --round {CR} --slot baseline
```

`{SLOT}` is one of: `baseline` (Stage 1), `opt/{op_id}` (Stage 5 per-track), `integration` (Stage 6 combined sweep), `golden_capture` (post-SHIP golden-ref refresh).

The runner takes the workload config from `target.json`. Both formats work: the flat
format (`input_len`, `output_len`, `batch_sizes`), and `workload_matrix` for
multi-dimensional `(input_len x output_len x batch_size)` sweeps.

Nsys profiles need a **separate short profiling invocation** (`--slot profiling`), run after the clean baseline. `--slot baseline` plus any profiler flag is a hard error, so profiling overhead can never contaminate authoritative timing. The E2E numbers from a profiling invocation also have nsys overhead and are NOT authoritative. For the capture-window mechanics, the Blackwell `cuda-sw` trace backend, the `profile_meta.waves[]` correlation contract, and the TP>1 per-rank caveat, see `nsys-profiling-guide.md`.

## Parity checklist (must match baseline)

Your numbers are not trustworthy until every item below holds.

- Same model weights + same revision/commit. The model path is a plain directory with `config.json` (the Rust loader rejects the HF cache layout).
- Same dtype/quantization (FP8/MXFP4 formats and scale shapes matter).
- Same TP/DP/EP topology and identical routing/dispatch mode.
- Same runner kind (`model_runner` in the row) and the same confdb-resolved backends (attention, MoE, KV layout, block size) for this `(architecture, SM, quant)`. These are not CLI flags. Read them from the server log line `Backends from confdb`, or from `pumice_rust.resolve_config(...)`.
- Same CUDA graphs mode and torch.compile mode: no `--enforce-eager`, no `--no-compile`, no `PUMICE_COMPILE_TARGET=0`.
- Same scheduler knobs that affect bucketing (`--max-num-seqs`, `--max-num-batched-tokens`, chunked prefill on/off).
- Same prefix-cache regime on both arms (§ Prefix-cache regime).
- Same engine build for the arm you claim: the freshness gate proves that the artifact is newer than its sources, and `--fresh-cache` proves that the compile cache did not leak across sweeps.
- Confirm that the **optimized path actually executed**, with one of:
  - a server-side enablement log line, or
  - an instrumentation counter, or
  - an unmistakable kernel name in the Nsight Systems timeline of the server.

### Debug-only: run eager

Use eager mode only to debug correctness or functional issues. Never use it for a production-parity perf claim. Set it in the server args of a throwaway launch, not in a slot that makes gate artifacts:

```bash
.venv/bin/python rust_grpc_server.py --model <path> --port 50051 --enforce-eager
```

## Interpreting output

The runner writes `e2e_latency_results.{json,md}` for each slot, plus one JSON file for each bucket. The authoritative scalar is `avg_latency` (mean wave seconds).

Compute speedup and improvement:

```python
baseline_s = 10.95
opt_s = 10.20

speedup = baseline_s / opt_s
improvement_pct = (baseline_s - opt_s) / baseline_s * 100
```

`verify_validation_gates.py` compares the reported per-bucket `avg_s` against the `avg_latency` of each bucket JSON, and derives `speedup == baseline_avg / opt_avg` again on that scalar. Do not report a speedup that you calculate from percentiles, throughput, or a phase sub-interval. Those values are diagnostic.

If your measured E2E improvement is small, sanity-check the component share with `references/e2e-delta-math.md`.

## Troubleshooting

### `REFUSING TO MEASURE: engine freshness check failed`
Your Rust or confdb edit is not built. See § The freshness gate. Do not use `--allow-stale-engine`.

### `no matching package named amzn-mantle-cmb-api`
The `[patch.crates-io]` entry is missing from the active `$CARGO_HOME/config.toml`. See the cmb-api note in § The freshness gate.

### Server never becomes healthy (`HealthCheck` timeout)
- Read the captured server log for the bucket. A startup crash is usually a config or backend problem, not a timeout.
- `CUBLAS_STATUS_NOT_INITIALIZED` on the first cuBLAS call means that the major version of `CUDA_HOME` does not match `torch.version.cuda`.
- Unsupported GPU: confdb resolves backends per SM family (`sm90`, `sm100` for B200 and B300, `sm120`). A family with no section for the model fails at startup. For gpt-oss on L40S (sm_89, family `sm80`), the session image applies an AMMO-local `[sm80]` row ("Ada/Ampere harness only"). Other models have no `sm80` row.
- A cold FlashInfer JIT cache can make the FIRST launch on a new host take about 11 minutes (SM100 MoE extension). Raise `--health-timeout-s` for that one launch. Later launches start in less than 30 s.

### Optimized path not activating
- Verify that the enable flag is set (`bench.opt_env` in `target.json`, default-off flag).
- For a confdb change, verify the resolved dict for THIS `(arch, SM, quant)`. An edit to `[sm100]` changes nothing on an sm_90 host.
- Verify that a compiled specialization exists for your `(dtype, TP/EP, bucket set)`.
- Verify that your bucket guard matches the validated envelope.
- Use Nsight Systems on the server to confirm which kernels run under the captured graph.

### "No E2E win" even though microbench is faster
Common causes:
- The target component is a small fraction of end-to-end (`f` small), so the expected E2E gain is bounded (see `references/e2e-delta-math.md`).
- The Inductor pass pipeline already fused the chain in the compiled baseline (see `e2e-delta-math.md` § Which Passes to Check).
- Graph breaks or unexpected fallbacks (different kernels between baseline and optimized runs).
- Another bottleneck dominates (attention, KV, scheduler, host launch gaps).

### High variance / poor reproducibility
- Increase the waves per bucket, and keep `--warmup-waves` at 1 or more so that the runner discards the cold effects of the first wave.
- Isolate the GPU: no other jobs, and no power or thermal limit.
- Use `--fresh-cache` on all compared arms or on none. A warm compile cache against a cold one is a real latency difference on the first waves.

## Recording results

Write the Stage 5 per-track results to `{artifact_dir}/rounds/{CR}/tracks/{op_id}/validation_results.md` with:
- full repro commands (baseline + optimized) and env vars
- the bucket set and capture/compile settings
- the runner kind, the confdb-resolved backends, and the engine build identity for each arm (commit + artifact freshness)
- baseline vs optimized tables (speedup + improvement)
- evidence that the optimized path executed

The Stage 6 integration record is `{artifact_dir}/rounds/{CR}/sweeps/integration/e2e_latency_results.json`, written by the runner. `references/artifact-layout.md` owns every artifact path.

Use `references/validation-defaults.md` for the minimum reporting template and default gates.
