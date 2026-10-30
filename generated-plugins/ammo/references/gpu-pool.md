# GPU Pool Reservation

This file governs every accelerator command, and every one must use this
reservation pattern. The campaign shares one resource pool with every other
campaign on the host; each agent must reserve its exclusive subset before use.

## Runtime Binding

One resource is one GPU. Use `CUDA_VISIBLE_DEVICES`; the CUDA, Nsys, and NCU
rules below apply. `target.json` `runtime.profile` selects the profile at
scaffold time; do not select a backend. Pass `--num-gpus N` to
`gpu_reservation.py`; `--num-resources N` is the same argument.

On Neuron, which v1 does not run, follow `references/accelerator-binding.md` § Neuron Execution and Evidence for the reservation unit, the visibility variables, and LNC.

## Reservation Pattern

Reserve first, then run the real command against the device list the reserve call prints:

```bash
CVD=$(scripts/ammo gpu_reservation.py reserve \
  --num-gpus N --session-id {gpu_session_id} --no-auto-release) && \
  CUDA_VISIBLE_DEVICES=$CVD <command>
```

- `--session-id {gpu_session_id}`: the spawner supplies an ID unique per active agent (campaign + op + agent nonce). Never share the logical `op_id` between a champion and delegates.
- `--no-auto-release`: marks a persistent child reservation, so PostToolUse does not release it at command completion. Use only the exact owner injected for your agent. Command-scoped reservations omit this flag and release after the observed command.

**Session-id rule:** treat the exact injected owner as opaque and pass it unchanged. It may include a colon; do not parse, normalize, share, or reconstruct it. A reserve call without `--session-id` uses `AMMO_SESSION_ID`, which is the campaign id.

If you minted an ad-hoc id, always release it explicitly before returning:

`scripts/ammo gpu_reservation.py release-session --session-id {gpu_session_id} || true`

Pass `--lease-hours 2` or more on the reserve call for a sweep or profiler capture longer than 10 min. The default lease is 15 min and does not extend itself.

## How Your Resources Get Released

Command-scoped reservations auto-release when the command completes: the PostToolUse hook detects the reservation pattern and releases by session ID. For persistent child reservations, the SubagentStop hook releases resources still held by `$CLAUDE_SESSION_ID` (or `$CLAUDE_SESSION_ID:$AGENT_ID`) when you return to the parent. Explicit `release-session` calls and lease expiry are the recovery paths.

## How Much to Reserve

| Task | Resource count | Notes |
|------|-------------|-------|
| Kernel benchmarks | 1 | One assigned unit |
| Micro-experiments (debate) | 1 | Keep brief to minimize contention |
| Static analysis (`ncu --query-metrics`) | 1 | CUDA only; no kernel execution |
| nsys single-kernel traces | 1 | CUDA only; existing binary |
| E2E sweeps | `{tp*dp}` | Match the engine world size (TP × DP) from target.json |
| Parallel tracks / debate experiments | 1-N | Pool may exceed TP×DP; use remaining resources |

An E2E sweep needs `N = TP × DP` accelerator units reserved as one contiguous
block, because each DP replica runs its own TP group. Reserve only TP and the
other DP replicas cannot start. If no contiguous block of `TP × DP` units is
free, retry after other agents release.

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

The agents of this campaign, other campaigns, and other work on the host share the GPUs, so a process you did not start is somebody's live work. Stop only processes you started. For any other process on your GPUs, including a suspected zombie, report it to the lead and wait.

## Diagnostics (Orchestrator Only)

Only the orchestrator runs these:

- `scripts/ammo gpu_reservation.py status --table`: print current reservation state as a table (one row per pool resource). The table holds the leases of every campaign on the host.
- `scripts/ammo gpu_reservation.py force-clear --all --session-id <crashed_session_id>`: clear stale reservations after crashes (target and session flags are both required; `--gpu-ids 0,1` narrows the target, `--force-no-session` is the emergency override). The table is host-wide, so `--force-no-session` also clears the leases of other campaigns.
