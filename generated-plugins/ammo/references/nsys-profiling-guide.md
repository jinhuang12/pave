# Nsight Systems Profiling Guide

This guide owns how AMMO captures GPU traces and how it reads them. Read it before you run a profiler in Stage 1, and again before you write a bottleneck claim in Stage 2. It gives the exact commands, the capture semantics, and the list of what counts as valid evidence. It applies to `pumice-cuda` sessions. Neuron sessions have no nsys; see `references/accelerator-binding.md`.

## The Default Workflow

Stage 2 has one default profiling workflow:

1. Run a clean E2E baseline sweep with no profiler flags.
2. Run a short Nsight Systems capture of the server. The runner resolves the CUDA trace backend for your hardware.
3. Mine the nsys report for kernel ranking, launch chains, per-rank/per-device skew, CUDA memcpy/NVLink activity, and timing shares.
4. Run targeted Nsight Compute only for physical-ceiling claims such as occupancy, achieved bandwidth counters, or SM utilization.

Do not use profiler-contaminated latency as the official E2E baseline.

## How Capture Works (read this first)

Pumice is a **server**. The profiled process is the server, not the client. The runner does these steps:

1. It wraps the server launch in
   `nsys profile --capture-range=cudaProfilerApi --capture-range-end=stop --trace=... --cuda-graph-trace=<mode>`.
2. It lets the server warm up and runs the `--warmup-waves` that it discards.
3. It sends the **`StartProfile`** RPC. The engine loop reads the pending flag on its next iteration and calls `torch.cuda.profiler.start()`. This opens the nsys capture range.
4. It sends `--profile-waves` measured waves for the bucket.
5. It sends the **`StopProfile`** RPC. The engine calls `torch.cuda.profiler.stop()`, and nsys closes the capture range. With `--capture-range-end=stop`, nsys also stops collecting.

Two consequences:

- **There is no knob that captures only output step N.** The profiler entry is a plain start/stop pair. To profile a specific decode depth, send a workload that is at that depth inside the `StartProfile`/`StopProfile` window (§ Profiling a Specific Decode Depth). The prefill chunk budget is the operator-set `--max-num-batched-tokens` (server default 8192), not a fixed constant.
- **Client-side NVTX cannot appear in the timeline of the server.** The client and the server are different processes. So the capture identity lives in the output JSON under `profile_meta`:

```json
"profile_meta": {
  "schema": "AMMO_CAPTURE_V2",
  "capture_mechanism": "grpc_start_stop_profile+nsys_cudaProfilerApi",
  "nsys_report": "baseline_bs8.nsys-rep",
  "server_start_unix": ..., "profile_start_unix": ..., "profile_stop_unix": ...,
  "profile_window_s": ..., "profiled_wave_count": ...,
  "waves": [{"...": "per-wave start/end wall clock + request count + profiled flag"}]
}
```

Correlate the wall-clock boundaries in `profile_meta.waves[]` with the capture window **offline**. A claim such as "this kernel ran during wave 3" must cite those timestamps, not an NVTX range name.

## Required Stage 1 Commands

Run two separate sweeps, in this order.

Clean baseline, used for all speedup math:

```bash
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} \
  --target-json {artifact_dir}/target.json \
  --round {N} \
  --slot baseline \
  --labels baseline \
  --capture-golden-refs
```

Bounded nsys capture, used for bottleneck attribution on every CUDA platform:

```bash
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} \
  --target-json {artifact_dir}/target.json \
  --round {N} \
  --slot profiling \
  --labels baseline \
  --nsys-profile \
  --nsys-mode node \
  --warmup-waves 1 \
  --profile-waves 1 \
  --timeout-s 1800
```

`--slot baseline` plus any profiler flag is invalid; the runner enforces this guard.

Keep the profiled window SHORT. `--profile-waves 1` with `--warmup-waves 1` is the default shape. The warmup absorbs graph capture, compile, and FlashInfer autotune, so the capture holds steady-state work only. The trace grows almost linearly with `--profile-waves`. Increase it only when one wave has too few events to rank.

The runner resolves the trace backend (§ Trace Backend). Do not pass `--nsys-trace` unless you must override it.

Keep `--nsys-mode node` for the default Stage 2 path. Node mode (`--cuda-graph-trace=node`) is the ranking source for CUDA graph workloads, and almost all decode, eagle, and verify passes are graph-captured.

Outputs:

```text
{artifact_dir}/rounds/{N}/sweeps/baseline/e2e_latency_results.json
{artifact_dir}/rounds/{N}/profiling/nsys/*.nsys-rep
{artifact_dir}/rounds/{N}/sweeps/profiling/json/*.json      # carries profile_meta
```

The runner writes one `.nsys-rep` for each label and bucket. The bucket JSON `profile_meta` records the capture identity: report path, start/stop wall clock, and per-wave boundaries.

## TP>1 Per-Rank Caveat

At TP=1 the capture above sees every kernel. At TP>1 it can miss ranks. Rank 0 runs in the engine process, and only rank 0 gets the `torch.cuda.profiler.start()` call (`pumice-rust/src/engine.rs`, `cuda_profiler_action`). Rust spawns ranks 1..N-1 as separate processes (`src/pumice/vllm_adapter/bootstrap.py`). These ranks never call `cudaProfilerStart`, so `--capture-range=cudaProfilerApi` can record rank 0 only.

For every TP>1 capture, do this per-rank check:

1. Keep `--nsys-mode node` (the runner passes `--cuda-graph-trace=node`).
2. Run `nsys stats --force-export=true --report cuda_gpu_kern_sum` on the report and confirm that there is one PID for each rank (TP ranks, and each DP rank).
3. If a rank is missing, capture again with a timed window instead of the profiler API range: `--capture-range=none --duration <s>` (through `--nsys-extra-flags`). Choose `<s>` so that the window covers the measured waves, and label the row and the analysis "timed window".

A timed-window trace also holds warmup and idle time. Use `profile_meta.waves[]` to cut the analysis to the measured waves. Do not report per-rank skew from a trace that holds rank 0 only. If the check fails and you do not capture again, write "rank 0 only" next to every number from that trace.

## Trace Backend

`_default_nsys_trace()` in the runner owns this rule: `--nsys-trace` defaults to `cuda-sw` when `target.hardware` names Blackwell (B200/B300/GB200/GB300, SM100/SM120), else `cuda`. The runner prints the resolved value and its source. The script is the only place this rule lives; there is no hand-maintained hardware matrix to read or keep current.

On Blackwell, the hardware event system for CUDA tracing interacts poorly with CUDA graph replay. The result is long warmup/profiling stalls, followed by RPC or NCCL watchdog timeouts. This affects all workloads (not only MoE or TP>1). `cuda-sw` still captures CUDA API/software activity and graph node attribution, and it avoids that hardware-tracing path. On a non-Blackwell target, override to `cuda-sw` only after the logs match that replay/collective timeout failure.

L40S (sm_89) captures are harness validation only. The AMMO-local `[sm80]` confdb row lets gpt-oss boot there, but no kernel ranking from an L40S trace supports a claim for a production GPU.

## Profiling a Specific Decode Depth

There is no arming knob, so shape the workload:

1. Set the `input_len` and `output_len` of the bucket in `target.json` so that the wave spends its time at the depth you care about.
2. Keep `--profile-waves 1`, so the capture is one batch wall-clock window.
3. Read the depth back from `profile_meta.waves[]` plus the workload entry of the bucket. Do not claim a depth that the workload did not run.

For a prefill-dominated capture, use a long `input_len` with a short `output_len`. For a decode-dominated capture, do the opposite. The capture range opens after warmup, so the capture of a long-output bucket is decode steady state.

## When a Capture Fails

If the command fails:

- Check the supervisor log under `rounds/{N}/sweeps/profiling/logs/` and the captured server log for the bucket.
- Check the printed `nsys_trace:` line. It reports the resolved backend and whether it came from `target.hardware` or an explicit flag.
- A first-launch stall of about 11 minutes on a new host is the FlashInfer SM100 MoE JIT compile, not an nsys problem. Raise `--health-timeout-s` one time. Later launches start in less than 30 s.
- Increase `--timeout-s` only if the logs show useful forward progress.
- Reduce `--profile-waves` (or the profiled bucket set) only as a last resort, and write down what you left out.
- An empty capture range (a `.nsys-rep` with no kernels) means that `StartProfile` did not reach the engine loop, or the run ended before `StopProfile`. Check that `profile_meta.profile_start_unix` and `profile_stop_unix` both have values.
- A TP>1 report with fewer PIDs than ranks is the per-rank caveat, not a failure. See § TP>1 Per-Rank Caveat.

## Stage 2 Mining

Mine the existing nsys reports. Do not run the sweep again only to analyze traces.

Minimum analysis:

- Export nsys reports to SQLite or stats tables.
- Rank kernels by total GPU time and count.
- Group kernel chains in timestamp order, not architecture order.
- Map top kernels to source paths or generated backends when possible. On the Pumice runner: `src/pumice/kernels/`, `src/pumice/layers/`, `csrc/`, `src/pumice/third_party/triton_kernels/`. `triton_poi_fused_*` and `triton_red_*` names come from Inductor and point at `src/pumice/compilation/`, not at a hand-written kernel. On the vLLM runner, kernels come from the installed vLLM 0.30.0 package. Host-side gaps point at `pumice-rust/src/engine.rs` or the Python model runner.
- For TP > 1 or DP > 1, compare all rank/device reports and report per-rank skew. TP ranks 1..N-1 and each DP rank are separate processes (§ TP>1 Per-Rank Caveat).
- Separate compute kernels from communication kernels and memcpy/P2P traffic.
- Compute `f_decode`, `decode_share_of_e2e`, and `f_e2e` from measured Stage 1 data.

Useful commands:

```bash
nsys stats --force-export=true --report cuda_gpu_kern_sum \
  {artifact_dir}/rounds/{N}/profiling/nsys/baseline*.nsys-rep

nsys stats --force-export=true --report cuda_gpu_trace \
  {artifact_dir}/rounds/{N}/profiling/nsys/baseline*.nsys-rep

nsys export --type sqlite --force-overwrite=true \
  --output {artifact_dir}/rounds/{N}/profiling/nsys/baseline.sqlite \
  {artifact_dir}/rounds/{N}/profiling/nsys/baseline*.nsys-rep
```

Report approximate trace timings honestly. A value such as `~74 us` is valid when it comes from the nsys trace. Do not infer kernel duration or order from source code or architecture diagrams.

## Optional Graph Diagnostics

`--nsys-mode graph` is optional diagnostic enrichment. Use it only when node-mode results leave a specific open question about graph structure, launch grouping, CUDA graph replay, or metadata available only in graph view.

If node and graph mode disagree, use node-mode timing for bottleneck ranking (§ Required Stage 1 Commands) and explain why graph mode was captured.

Graph diagnostic command:

```bash
.venv/bin/python scripts/run_pumice_grpc_bench.py \
  --artifact-dir {artifact_dir} \
  --target-json {artifact_dir}/target.json \
  --round {N} \
  --slot profiling \
  --labels baseline \
  --nsys-profile \
  --nsys-mode graph \
  --warmup-waves 1 \
  --profile-waves 1 \
  --timeout-s 1800
```

Graph diagnostics use the same hardware-derived trace backend as node mode.

## Targeted NCU

The physical-ceiling gate is disjunctive: back the claim with targeted Nsight Compute, or with an explicitly cited hardware spec plus the math. Either one alone is enough. Nsys alone is not.

These count as physical-ceiling claims:

- Occupancy or achieved occupancy.
- SM utilization or tensor-core utilization.
- Achieved memory bandwidth from hardware counters.
- Register pressure, shared-memory pressure, or stall-reason claims.
- "Kernel X can improve by at most Y%" where Y comes from a hardware ceiling.

Keep NCU narrow. Profile only the top kernels identified by nsys, with representative bucket sizes. Store results under:

```text
{artifact_dir}/rounds/{N}/profiling/ncu/
```

Stage 2 may rank bottlenecks from nsys without NCU.

## Valid Stage 2 Evidence

Acceptable:

- `rounds/{N}/profiling/nsys/*.nsys-rep`
- nsys stats/export tables derived from those reports
- `profile_meta` from the profiling bucket JSON, for capture-window and wave correlation
- sweep JSON from `rounds/{N}/sweeps/baseline/`
- targeted NCU CSV/report for hardware-counter claims
- source-code mapping used only to explain what a measured kernel is

Not acceptable:

- Profiler-run latency as official E2E timing.
- Architecture-inferred kernel chains without trace timestamps.
- Occupancy/bandwidth-counter claims without targeted NCU.
- Graph-mode timing as the primary bottleneck ranking source.
- A wave or depth attribution that cites no `profile_meta` timestamps.
- A per-rank claim from a TP>1 trace that failed the per-rank check.
- A trace from a server built before the last `pumice-rust/` or `confdb/` edit that you attribute.

## Report Checklist

`rounds/{N}/mining/bottleneck_analysis.md` must include:

- Exact Stage 1 baseline and profiling commands.
- Artifact paths for every trace used, plus the `profile_meta.nsys_report` and capture-window values they came from.
- The runner kind, the confdb-resolved backends, and the engine build identity of the profiled server.
- Top kernels/components by measured time.
- Per-rank/per-device comparison for TP/DP runs, with the per-rank check result ("all ranks", "rank 0 only", or "timed window").
- `f_decode`, `decode_share_of_e2e`, and `f_e2e` tables.
- Technology Landscape entries for the top components.
- Any NCU-backed physical-ceiling claims with paths to the raw NCU artifacts.
