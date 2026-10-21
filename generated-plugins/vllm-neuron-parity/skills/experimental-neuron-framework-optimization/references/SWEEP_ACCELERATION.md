# Sweep Acceleration Playbook

*How to stop a configuration sweep from dominating an optimization campaign's wall-clock and
gating every downstream stage. Portable across models/hosts; numbers are illustrative examples,
never expected results.*

Companion to the AutoBridge workflow's Step 5 (config sweep). Lesson codes in parentheses refer
to `references/FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md` (bare `Dn`/`Hn`) and
`references/KERNEL_OPTIMIZATION_LESSONS.md` (prefixed `KERNEL`), both shipped in this skill.
For how to record what the sweep finds, see `references/INVESTIGATION_OPTIMIZATION_PLAYBOOK.md`
(one `STEP_NN` deep dive per axis driven to a verdict).

---

## The core observation

A sweep *feels* like a measurement loop, but on a compiled-graph accelerator stack its wall-time
is overwhelmingly **graph compilation + multi-rank weight load**, not the capture or benchmark.
A single campaign can spend the large majority of its hours in bring-up and a small fraction
measuring. Every speedup below attacks bring-up, point count, or lost/wedged time — not the loop.

Instrument it: report the **bring-up : measurement time ratio** at the end of every session. If
bring-up dominates, the sweep was mis-ordered — the hardware was not slow (D4).

---

## Tier 1 — Stop paying compile + bring-up per point (largest wins)

### 1. Group the sweep by compiled-graph cache key — one bring-up per key (D4)
Enumerate the distinct compiled-graph cache keys the sweep needs **before** launching, then bring
each up exactly once.

- **Forces a recompile (new cache key):** parallelism/shard degree, cache-block count override,
  block/page size, sequence/context buckets, batched-tokens bucket, KV-cache dtype, quantization
  family, compiler optimization level, layer count, and any memory-utilization knob that changes
  the block count.
- **Free inside one already-warm server (NO recompile):** actual in-flight concurrency / batch,
  arrival rate, and profile-capture on/off.

So the structure is: for each distinct compiled artifact, bring it up once and run **all** the
batch / concurrency / arrival-rate variations *and* the profile capture inside that one live
server. A sweep that relaunches a fresh server per point re-pays compile+load every time for
nothing.

### 2. AOT-precompile every cache-key variant off-device, in parallel (D4, D7)
The compiler is host/CPU-bound; the device is only needed for **load + measure**. Pre-warm the
compile cache for every distinct cache key concurrently (on the host, or a separate box) *before*
touching the device. Then the device phase is load-and-measure only, and the "one device job at a
time" rule stops being a serialization bottleneck because no compiling happens on the critical path.

### 3. Pin and persist the compile cache; stage weights node-local (D7, D2, D9)
- **Pin ONE explicit cache root** in the launch script rather than inheriting a default that
  varies with home dir / filesystem. Several stacks silently relocate the cache when the default
  lands on a network FS — so the path you clean may not be the path you fill.
- **Enumerate ALL cache roots** (system compiler cache + framework cache) and only clear the ones
  you mean to; never clear the root you are about to read from.
- **Verify a cache HIT by compile time** — a warm launch is fast, a cold compile is not. If a
  "fresh" compile finishes in warm-cache time, it found a cache you did not clear.
- **Stage the checkpoint to node-local disk.** Many-rank concurrent reads of a large sharded
  checkpoint from one shared/root filesystem is the standard cause of a bring-up that never
  reaches warm-up. Verify the fast mount actually exists before relying on it.

### 4. Budget the profiler's own HBM into the FIRST compile (KERNEL F4/A3)
The device profiler needs a memory pool + a per-core notification buffer. On a near-full-memory
model it silently fails (generation succeeds, the trace dir holds only empty stubs). The fix is to
free headroom via the memory-util knob — but that knob **changes the cache key and forces a
recompile**. Compute the profiler's budget and set the knob **once, on the first compile**, so you
do not discover the need on the third launch and recompile mid-sweep. A suspiciously tiny trace
directory is a FAILED capture, not a small workload.

---

## Tier 2 — Run fewer points (prune analytically, apportion from data on disk)

### 5. Do the analytic prune first — it is free (C1, C2)
Analytic parallelism planning + per-operator entitlement run at **zero device time** and already
remove the configurations that cannot clear the bar and identify the interior-optimum degree. That
*is* the "remove obviously-slow configs before sweeping" step. Never brute-force the parallelism
search by serial compiles when the analytic model answers it in seconds.

### 6. Sweep only the axes that move the binding op, highest-leverage first (D1, H7, KERNEL D5)
Rank sweep axes by their expected effect on the **confirmed** binding operator, and test the
top one first; only continue if it does not clear the bar. Example: on a weight-HBM-bound decode,
a precision change that halves weight bytes is the first axis to try; capacity/bucket axes come
after. Do not grid axes that cannot move the binding term — that is measurement with no hypothesis.

### 7. Apportion an axis from two points instead of sweeping it end-to-end (KERNEL B13)
When the question is per-item vs fixed cost, you rarely need the whole axis. From one capture at
**two** points of the swept parameter, fit `time = intercept + slope × parameter`: the slope is
the per-item term, the intercept is the invariant (weight read + collectives + fixed overhead).
Two points characterize the axis with no additional device time. (Only valid if compiled width
tracks the swept parameter — at a fixed padded width the slope is an artifact.)

### 8. Reduced-layer extrapolation — where, and only where, it transfers (KERNEL A8, D5)
A reduced-layer build of the same architecture/config surfaces per-layer pathologies for a
fraction of the cost, and de-risks the profiler tooling. Its transfer boundary is sharp:

- **Transfers (use it):** per-kernel correctness & efficiency, a custom kernel not firing, the
  per-layer collective pattern and compute/collective overlap structure, relative rankings, and
  depth-independent compile errors. Extrapolate a per-step time to full depth with a **two-point
  linear per-layer model** (capture at two depths; separate fixed overhead from per-layer slope).
- **Does NOT transfer (never conclude from it):** absolute SLA numbers; anything capacity- or
  HBM-budget-dominated (cache-block count, max concurrent sequences, batch at the capacity
  boundary, KV headroom) — a reduced model has a completely different memory budget because the
  full-model weights are what dominate capacity.

Consequence: reduced-layer extrapolation helps a **compute/collective-bound** leg (e.g. prefill
latency) far more than a **capacity/memory-bound** leg (e.g. decode throughput), and it can never
certify the SLA. Keep it under a separate run id so it cannot satisfy a feasibility/calibration gate.

### 9. Shake down config on a tiny model, spend full weights only on the number (D5)
"Does this config stand up / do the flags parse / is the topology valid" is a reduced-size-model
or tiny-e2e-test question answered in minutes. Reserve full-weight launches for the actual SLA
numbers, so a flag typo is not discovered 40 minutes into a full-weight bring-up.

### 10. Read the free per-phase serving metrics instead of adding captures (KERNEL A2)
Where prefill and decode are mutually exclusive phases, the engine's periodic log lines give
per-phase throughput, running/waiting counts, and cache usage at **zero device time**. Use them to
cross-check a capture and to build the serving model — do not stand up an extra launch to measure
what the log already prints.

---

## Tier 3 — Stop losing the budget to wedged / contended device state

### 11. One clean device job at a time; full teardown after any abnormal exit (D15, D16, KERNEL I2)
This is the single biggest *silent* time sink. A cancelled or crashed job does **not** stop the
device processes it spawned; a relaunch then stacks onto stale/contending state, and every request
times out (e.g. a long default request timeout) producing garbage that gets re-run. After ANY
abnormal termination: kill the whole **process group** (not just the parent PID), wait for device
state to drain, and verify from the device listing that nothing holds the device before relaunching.
Never assert "nothing is running" from memory — check the process table.

### 11.5 The launch fix is `NEURON_SKIP_EFA_AFFINITY=1`; do NOT reference cores in env vars (D17)
Two separate bring-ups on this box were lost to hand-invented environment variables — the second
compounding the first because the "fix" swapped one wrong var for another. The correct posture is to
NOT set any device/affinity core var at all and let vLLM place devices:
- **A single TP=N replica needs no device env var.** `--tensor-parallel-size N` allocates the cores;
  when `NEURON_VISIBLE_DEVICES` is unset and this is the sole replica on the node,
  `neuron_worker._get_visible_devices()` auto-defaults it to `range(local_world_size)` (cores
  `0..N-1`).
- **DP replicas are placed by vLLM.** Use `--data-parallel-size`; vLLM slices the node and assigns
  each replica a disjoint window via `parallel_config.assigned_physical_gpu_ids`. If DP places >1
  local replica without that assignment the worker fails loudly — never hand-carve a second replica
  with a per-launch `NEURON_VISIBLE_DEVICES=8,...,15`.

The two failure modes seen, verbatim, and their real fix:
- `NEURON_RT_VISIBLE_CORES=0-7` → `RuntimeError: NEURON_RT_VISIBLE_CORES cannot be used with
  multi-processing execution on vLLM. Set NEURON_VISIBLE_DEVICES to control device visibility.`
  **Fix:** don't set a core var at all — setting one is the mistake. `NEURON_RT_VISIBLE_CORES` is
  never the vLLM knob.
- (after removing that) `RuntimeError: No EFA device found at /sys/bus/pci/devices/<bdf>/infiniband
  ... set NEURON_SKIP_EFA_AFFINITY=1 to skip it on instances without EFA.` **Fix:** export
  `NEURON_SKIP_EFA_AFFINITY=1` — this was the only genuinely-required missing var. trn2.48xlarge
  here has no EFA infiniband, and EFA affinity is a CPU perf hint, not a correctness requirement.

Load-bearing env for a single TP=N colocated replica on trn2 (reuse the `profile` stage's preamble;
note there is NO core var here):
```bash
export NEURON_SKIP_EFA_AFFINITY=1               # REQUIRED on boxes without EFA
export VLLM_ENABLE_V1_MULTIPROCESSING=0         # single-process engine
export NEURON_RT_EXEC_TIMEOUT=600
export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=3600
export NEURON_RT_ENABLE_DGE_NOTIFICATIONS=0
export NEURON_PLATFORM_TARGET_OVERRIDE=trn2
export VLLM_CACHE_ROOT=/tmp/<run-id>_cache      # pin ONE root so cache-key-preserving relaunches HIT
```
A bring-up that dies on an env-var error leaves NO capture and is NOT a profile — verify readiness
with `curl :$PORT/health` before running any bench.

### 12. Deadline + pollable progress, never one oversized sleep (D10, D11, D3)
- Poll on short intervals against a growing artifact; never block on a single large sleep, and
  never `sleep` before the first check.
- Size readiness/startup timeouts from **measured** single-replica load time × replica count — not
  a huge insurance value. A long timeout is the mechanism by which a wedged bring-up consumes the
  session; detect failure from **absence of forward progress in the log**, not from a timeout
  expiring.
- Read the timeout variable name **from the error message verbatim** — stacks expose several
  (per-step, launch-to-ready, worker-termination) and raising the wrong one tears down a healthy
  engine.
- Never pipe a long-running or serving subcommand into an end-of-input consumer (it blocks
  forever). Redirect to a file and poll the file. A profile *viewer* bound to a port is finished
  work waiting for a client that will never connect — not a running job.

### 13. Ingest cheaply so "analyze" doesn't become its own multi-hour gate (KERNEL A5, F5)
Pin ONE rank, skip warm-up with an iteration delay so the window is decode-only and a handful of
steps, ingest to a columnar store (parquet) and query specific tables — never serialize a whole
multi-rank trace (that buffers hundreds of GB with zero output). Bound every ingest with a timeout
and confirm the output grows within ~1 min or kill and re-capture smaller. When you re-capture
smaller, change ONLY the window (one-variable hygiene applies to captures too).

---

## One-screen checklist

- [ ] Distinct compiled cache keys enumerated; sweep grouped so each artifact is brought up once.
- [ ] All cache-key variants AOT-precompiled off-device, in parallel, before device time.
- [ ] Cache root pinned + all roots enumerated; cache HIT verified by compile time; weights staged node-local.
- [ ] Profiler HBM budgeted into the first compile (no mid-sweep recompile to fit the profiler).
- [ ] Axes ranked by effect on the confirmed binding op; highest-leverage axis first; no off-target grid.
- [ ] Per-item vs fixed cost apportioned from two points where possible (no full axis sweep).
- [ ] Reduced-layer extrapolation used only for structure/relative/per-layer-linear, never capacity/SLA.
- [ ] Config shakedown on a tiny model; full weights only for the SLA number.
- [ ] Free per-phase serving-log metrics read off the same warm server.
- [ ] One clean device job at a time; full process-group teardown after any abnormal exit.
- [ ] Serving-launch env REUSED from the proven launch script (NEURON_SKIP_EFA_AFFINITY=1 present); NO core-selection env var set (TP allocates cores; DP via --data-parallel-size).
- [ ] Every long step has a deadline + pollable progress; timeouts sized to measured load × replicas.
- [ ] Ingest: one rank, small decode-only window, parquet, bounded; re-capture changes only the window.
