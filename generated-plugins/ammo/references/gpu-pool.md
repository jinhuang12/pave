# GPU Pool Reservation

This file governs every accelerator command on both runtimes; the file, script,
and flag names are spelled with GPU (`gpu_reservation.py`, `--num-gpus`). All
accelerator commands must use this reservation pattern. The campaign shares one
resource pool with every other campaign on the host; each agent must reserve
its exclusive subset before use.

## Runtime Binding

Read `AMMO_RUNTIME_PROFILE`. A missing value means `pumice-cuda`. The legacy
names `vllm-cuda` and `vllm-neuron` are aliases of `pumice-cuda` and
`pumice-neuron`; the rules below apply to both names. `target.json`
`runtime.profile` selects the profile at scaffold time; do not select a backend or change LNC. LNC fixes the size
of one reserved unit and is recorded in the run fingerprint, so changing it
invalidates every reservation and comparison; report an LNC error to the lead.
Pass `--num-resources N` to `gpu_reservation.py`; `--num-gpus N` is the same
argument.

- `pumice-cuda` (legacy `vllm-cuda`): one resource is one GPU. Use `CUDA_VISIBLE_DEVICES`; the
  CUDA/Nsys/NCU rules below apply.
- `pumice-neuron` (legacy `vllm-neuron`): one resource is one logical NeuronCore. The pool and
  every child reservation are contiguous. For engine or serving-level profiling,
  assign the printed `start-end` value to `NEURON_VISIBLE_DEVICES`. The engine
  maps each worker rank to one core and sets that worker's
  `NEURON_RT_VISIBLE_CORES`. Use `NEURON_RT_VISIBLE_CORES` yourself only for a
  direct, single-process NKI/runtime command.

## Reservation Pattern

Reserve first, then run the real command against the device list the reserve call prints:

On `pumice-cuda`:

```bash
CVD=$(scripts/ammo gpu_reservation.py reserve \
  --num-gpus N --session-id {gpu_session_id} --no-auto-release) && \
  CUDA_VISIBLE_DEVICES=$CVD <command>
```

On `pumice-neuron`:

```bash
NVD=$(scripts/ammo gpu_reservation.py reserve \
  --num-resources N --session-id {gpu_session_id} --no-auto-release) && \
  NEURON_VISIBLE_DEVICES=$NVD <engine-or-serving-profile-command>
```

For a direct, single-process NKI/runtime command, use the same reservation and
assign its printed value to `NEURON_RT_VISIBLE_CORES`. Never pass that variable
to an engine parent (the Pumice server or `run_pumice_grpc_bench.py`). An empty Neuron visibility value is not a no-core sentinel.

- `--session-id {gpu_session_id}`: the spawner supplies an ID unique per active agent (campaign + op + agent nonce). Never share the logical `op_id` between a champion and delegates.
- `--no-auto-release`: marks a persistent child reservation, so PostToolUse does not release it at command completion. Use only the exact owner injected for your agent. Command-scoped reservations omit this flag and release after the observed command.

**Session-id rule:** treat the exact injected owner as opaque and pass it unchanged. It may include a colon; do not parse, normalize, share, or reconstruct it. A reserve call without `--session-id` uses `AMMO_SESSION_ID`, which is the campaign id.

If you minted an ad-hoc id, always release it explicitly before returning:

`scripts/ammo gpu_reservation.py release-session --session-id {gpu_session_id} || true`

Pass `--lease-hours 2` (or higher) on the reserve call for long sweeps or profiler captures that run >10 min, so a run that legitimately exceeds the **15 min default** lease does not expire mid-run. The script does NOT auto-extend profiler leases.

## How Your Resources Get Released

Command-scoped reservations auto-release when the command completes: the PostToolUse hook detects the reservation pattern and releases by session ID. For persistent child reservations, the SubagentStop hook releases resources still held by `$CLAUDE_SESSION_ID` (or `$CLAUDE_SESSION_ID:$AGENT_ID`) when you return to the parent. Explicit `release-session` calls and lease expiry are the recovery paths.

## How Much to Reserve

On Neuron, every count below means logical NeuronCores. CUDA-only tools such as NCU and Nsys do not apply; a
Neuron Profile or NKI capture reserves the resource count required by its
declared production topology.

| Task | Resource count | Notes |
|------|-------------|-------|
| Kernel benchmarks | 1 | One assigned unit |
| Micro-experiments (debate) | 1 | Keep brief to minimize contention |
| Static analysis (`ncu --query-metrics`) | 1 | CUDA only; no kernel execution |
| nsys single-kernel traces | 1 | CUDA only; existing binary |
| E2E sweeps | `{tp*dp}` | Match the engine world size (TP × DP) from target.json |
| Parallel tracks / debate experiments | 1-N | Pool may exceed TP×DP; use remaining resources |

An E2E sweep needs `N = TP × DP` accelerator units reserved as one contiguous
block, because each DP replica runs its own TP group. On CUDA these are GPUs; on
Neuron they are logical NeuronCores. Reserve only TP and the other DP replicas
cannot start. If no contiguous block of `TP × DP` units is free, retry after
other agents release.

The campaign environment (`references/campaign-env.md`) exposes `AMMO_TP_SIZE` and `AMMO_DP_SIZE` so you can compute `N` without reopening `target.json`:

```bash
NUM_GPUS=$(( ${AMMO_TP_SIZE:-1} * ${AMMO_DP_SIZE:-1} ))
NUM_RESOURCES=$NUM_GPUS
```

On CUDA the pool is every GPU on the host. `gpu_reservation.py` reads it from
`nvidia-smi --query-gpu=index --format=csv,noheader`, with a torch device count
as the fallback. One lease table, `/tmp/ammo_gpu_res` (`AMMO_GPU_RES_DIR`),
serves every campaign on the host, so two campaigns can never hold the same
physical GPU. The pool is usually larger than one world size. Extra resources
enable parallel experiment tracks.

The lease table knows only AMMO leases. A GPU that other work on the host uses
still looks free to it. When `CUDA_VISIBLE_DEVICES` is set in the caller's
environment, `reserve` takes only the GPUs it names, so set it to keep the
campaign off GPUs that other work uses. Discover the pool and the current leases
with `gpu_reservation.py status`.

## When the Pool Is Full

If the pool is exhausted, the reserve command fails immediately with a `ReservationError`. Yield control and retry on a later turn; do not hold the foreground in a long sleep loop. Continue useful CPU-only work while waiting when possible.

For CPU-only commands (file reads, roofline math, ISA inspection), no reservation is needed.

## Never Kill Another Agent's Processes

**Never kill processes on accelerator resources you don't own.** The agents of this campaign, other campaigns, and other work on the host share the GPUs, so a process you did not start is somebody's live work:

- Only terminate processes YOU started. Do not `kill`, `pkill`, or `killall` processes belonging to other agents.
- On CUDA, if `nvidia-smi` shows an unknown process on "your" GPU, report the
  overlap. On Neuron, do the same for an unknown process using your visible core
  range. Wait; do not kill it.
- If you suspect a zombie/leaked process, report it to the orchestrator rather than killing it yourself.

## Diagnostics (Orchestrator Only)

Only the orchestrator runs these:

- `scripts/ammo gpu_reservation.py status --table`: print current reservation state as a table (one row per pool resource). The table holds the leases of every campaign on the host.
- `scripts/ammo gpu_reservation.py force-clear --all --session-id <crashed_session_id>`: clear stale reservations after crashes (target and session flags are both required; `--gpu-ids 0,1` narrows the target, `--force-no-session` is the emergency override). The table is host-wide, so `--force-no-session` also clears the leases of other campaigns.
