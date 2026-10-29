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
- Quickstart (command shape)
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

Every row also records `host`, `gpu`, `model_runner`, `vllm_version`, and `torch_version`. `model_runner` is the runner that confdb selected for the model (for gpt-oss on CUDA it is `vllm`, the embedded vLLM runner at the pinned version). Rows from an L40S (sm_89) host are labelled "harness validation only". Do not use them for a kernel speedup claim.

## Tool Selection

This section owns every sweep command and the comparator rule; other files point here. Pick the row that matches your stage.

| Stage | Tool | Why |
|-------|------|-----|
| Stage 1 (baseline) | `run_pumice_grpc_bench.py --round {N} --slot baseline --labels baseline --capture-golden-refs` | Clean E2E timing. **No profiling flags are allowed** (guard enforced). These are the authoritative numbers for speedup calculations. |
| Stage 1 (profiling) | `run_pumice_grpc_bench.py --round {N} --slot profiling --nsys-profile --nsys-mode node --warmup-waves 1 --profile-waves 1` | The runner wraps the server in nsys with `--capture-range=cudaProfilerApi`. The `StartProfile` and `StopProfile` RPCs open and close the capture window. The runner resolves `--nsys-trace` from `target.hardware` (`cuda-sw` on Blackwell). E2E numbers from this slot have profiler overhead and are not used for comparisons. See `nsys-profiling-guide.md`. |
| Stage 5 (correctness) | `run_pumice_grpc_bench.py --round {N} --slot opt_correctness/{op_id} --labels opt --verify-correctness --baseline-from $STAGE1_DIR` in Round 1. Round 2 and later import `--baseline-from $GOLDEN_DIR` instead. | Gate 5.1b. The runner reads `golden_refs.json` from the imported dir and stops when it is missing. |
| Stage 5 (validation) | `run_pumice_grpc_bench.py --round {N} --slot opt/{op_id} --labels opt --baseline-from $STAGE1_DIR --fresh-cache` in Round 1. Round 2 and later import `--baseline-from $PROMOTED_DIR --baseline-from-arm opt` instead. | GPU-locked optimized-only sweep, compared against the imported comparator. |
| Stage 6 (integration) | `run_pumice_grpc_bench.py --round {N} --slot integration --labels opt --baseline-from $STAGE1_DIR --fresh-cache` in Round 1. Round 2 and later import `--baseline-from $PROMOTED_DIR --baseline-from-arm opt` instead. | Gate-quality combined measurement when two or more tracks pass; a single unchanged passer uses the short-circuit. EXHAUSTED rounds skip it. See § Combined Validation Workflow in `orchestration/integration-logic.md`. |
| Post-SHIP (golden refs) | `run_pumice_grpc_bench.py --round {N} --slot golden_capture --labels baseline --capture-golden-refs` | Fresh golden refs from the promoted mainline. They become the next round's correctness comparator. |

**Comparator rule.** A Stage 5 or Stage 6 sweep runs only the opt arm and imports its comparator with `--baseline-from`:

- Round 1 imports the Stage 1 baseline dir, `$STAGE1_DIR` = `{artifact_dir}/rounds/1/sweeps/baseline`. It also holds the golden refs.
- Round 2 and later import the latest promoted integration dir, `$PROMOTED_DIR` = `{artifact_dir}/rounds/{P}/sweeps/integration`, where `{P}` is the latest round that shipped. If no round has shipped yet, keep `$STAGE1_DIR`. `--baseline-from-arm opt` makes the promoted arm the comparator. Without it the runner imports the pre-SHIP baseline arm of that dir, and the delta counts every earlier ship again. Correctness imports `$GOLDEN_DIR` = `{artifact_dir}/rounds/{P}/sweeps/golden_capture`.
- A paired rerun of the baseline arm happens only when the comparator is stale (`references/audit-invariants.md` § Cross-Artifact Checks). It runs from the campaign mainline, never from a track worktree, because a worktree can execute the optimized path in both arms (`references/validation-defaults.md` § E2E Baseline Reuse Requirement).

For all measurements reported in `validation_results.md` or used for profiling, use the runner. A server that you start by hand plus a client that you write by hand is not a measurement. It skips the arm env sanitization, the freshness gate, the prefix-cache regime check, and the output schema that every downstream consumer reads.

## Quickstart

Run the runner from the root of the checkout under test (the engine root or a track worktree), and always pass the artifact directory explicitly. Inside a track worktree the launcher runs the worktree venv, so the server loads the track's Rust build. Every sweep has this shape, with the flags from the § Tool Selection row for your stage:

```bash
scripts/ammo run_pumice_grpc_bench.py --artifact-dir {artifact_dir} <flags from § Tool Selection>
```

The Stage 1 commands, written out in full, are in `references/nsys-profiling-guide.md` § Required Stage 1 Commands.

Do not put the workload on the command line. The runner reads the model path, workload, server flags, and env config from `target.json`, so you never need `--model`, `--batch-size`, or the rest. The runner always passes `--tensor-parallel-size`, `--max-model-len`, and `--metrics-port` to the server. The prompt source follows the same rule: the runner reads `bench.prompt_source` when its `--prompt-source` flag is absent (freeze it with `new_target.py --prompt-source raw` when `input_len` cannot hold the chat template, see § Prompt Source in `workload-invocation.md`); pass the flag by hand only to override one run.

### Prefix-cache regime

Both arms of a comparison use the same prefix-cache regime. `target.json` records it, and the runner passes the same `--enable-prefix-caching` or `--no-enable-prefix-caching` flag to both servers. The flag overrides the confdb value. For each arm the runner checks three things: both argvs carry the flag, the server log shows the line `Prefix caching launch override: X -> Y`, and the `--metrics-port` counters `prefix_cache_hits_total` and `prefix_cache_queries_total` give the same hit ratio within 1 pp (or zero queries on both arms when the regime is off). A mismatch fails closed. Do not "fix" it by editing one arm.

### Fresh-cache isolation (`--fresh-cache`)

This flag keeps a sweep from using warm compile caches from a previous sweep. It allocates `{out_root}/cache/{sweep_id}/` and sets `PUMICE_CACHE_ROOT` and `TRITON_CACHE_DIR` in the env of the server. The cache is removed on success. The first server launch pays the full compile (minutes for large models). Later launches in the same sweep use the warm in-sweep cache.

## The freshness gate (read before your first sweep)

Before it measures, the runner compares the newest mtime of the engine sources against the built engine artifacts. The sources are `pumice-rust/src/**/*.rs`, `pumice-rust/confdb/*.toml`, `build.rs`, both `Cargo.toml` files, and `$CARGO_HOME/config.toml`. The artifacts are the staged `pumice_rust` `.so` in the venv, then the release directory. If a source is newer, the runner **refuses to measure**:

```
REFUSING TO MEASURE: engine freshness check failed (...)
```

This is not a harness bug. It tells you that your Rust or confdb edit is not in the binary. Rebuild and re-stage the engine from the worktree root:

```bash
scripts/ammo pumice-stage-native.sh "$PWD/.venv/bin/python" "$PWD" --features "nixl-stub cmb"
```

On Neuron, which v1 does not run, follow `references/accelerator-binding.md` § Neuron Execution and Evidence for the rebuild and the row fields.

Use the same cargo features as the engine build. If you leave out `cmb`, the server ignores `--cmb-port`. The script runs `cargo build --release` for `pumice-rust/` and copies the new `pumice_rust` `.so` into the venv. Keep `CARGO_HOME`, `CARGO_TARGET_DIR`, `SCCACHE_DIR`, and `RUSTC_WRAPPER` as your environment and `references/campaign-env.md` set them. Do not change them.

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

The runner sweeps all buckets in one invocation and launches the server once per label. Pass `--round {N} --slot {SLOT}` so results land in the canonical round-scoped layout.

Slot names and their paths are in `references/artifact-layout.md` § Path Resolution Rules.

The runner takes the workload config from `target.json`. Both formats work: the flat
format (`input_len`, `output_len`, `batch_sizes`), and `workload_matrix` for
multi-dimensional `(input_len x output_len x batch_size)` sweeps.

Profile in a separate `--slot profiling` run after the clean baseline; see `nsys-profiling-guide.md`.

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
Pumice ships no `Cargo.lock`, so every build needs a `[patch.crates-io]` row for the internal crate `amzn-mantle-cmb-api` in the active `$CARGO_HOME/config.toml`; the user adds it before the first engine build, and `scripts/preflight_pumice.py` checks it. Never edit `Cargo.toml` or commit a `Cargo.lock` to fix this.

### Server never becomes healthy (`HealthCheck` timeout)
- Read the captured server log for the bucket. A startup crash is usually a config or backend problem, not a timeout.
- `CUBLAS_STATUS_NOT_INITIALIZED` on the first cuBLAS call means that the major version of `CUDA_HOME` does not match `torch.version.cuda`.
- Unsupported GPU: confdb resolves backends per SM family (`sm90`, `sm100` for B200 and B300, `sm120`). A family with no section for the model fails at startup. For gpt-oss on L40S (sm_89, family `sm80`), the engine checkout needs the AMMO-local `[sm80]` row ("Ada/Ampere harness only"; see § Model and Hardware Support in `references/workload-invocation.md`). Other models have no `sm80` row.
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
- `--fresh-cache` on the opt arm forces a cold compile of the changed code. The warmup wave (`--warmup-waves`, default 1, discarded) absorbs that compile, so it does not enter the timed waves. The Stage 1 command takes no `--fresh-cache`.

## Recording results

Write the Stage 5 per-track results to `{artifact_dir}/rounds/{CR}/tracks/{op_id}/validation_results.md` with:
- full repro commands (baseline + optimized) and env vars
- the bucket set and capture/compile settings
- the runner kind, the confdb-resolved backends, and the engine build identity for each arm (commit + artifact freshness)
- baseline vs optimized tables (speedup + improvement)
- evidence that the optimized path executed

The Stage 6 integration record is `{artifact_dir}/rounds/{CR}/sweeps/integration/e2e_latency_results.json`, written by the runner. `references/artifact-layout.md` owns every artifact path.

Use `references/validation-defaults.md` for the minimum reporting template and default gates.
