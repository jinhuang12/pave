# Optimization loop for any NVIDIA GPU system

Fill every `{{SLOT}}` in "Run settings". Two worked fills and a
ready-to-paste invocation are in `optimize-loop-fills.md`, beside this file.

## Goal

Move {{SYSTEM}}'s north-star metric, {{METRIC_NAME}}, as close to its
physical bound on {{HARDWARE}} as the system allows. Every change that ships
keeps the correctness gate, {{GATE_NAME}}, and passes the four track gates of
stage 4, including a floor argument that its reviewer agrees with. Run the
loop round after round until the stop rule fires. Then deliver the branch
and the report.

The **incumbent** is the best build that has shipped so far. In round 1 it is
the baseline. Every change is compared to the incumbent.

## Run settings

| Slot | What to fill in |
|---|---|
| `{{SYSTEM}}` | What is optimized, the path to its code, and the commit |
| `{{HARDWARE}}` | GPU type, interconnect |
| `{{GPU_IDS}}` | GPUs this run may use |
| `{{TARGET}}` | What stays frozen and defines "production": inputs, model or data, precision rules, launch settings that are part of the target |
| `{{OPERATING_POINTS}}` | The fixed cases the metric is measured at (sizes, batch sizes, scenes, request mixes) |
| `{{WORLD_SIZE}}` | GPUs one run of the system needs (default 1) |
| `{{PHASES}}` | The segments of one unit of work, in order, that do not overlap in time (e.g. prefill, then decode steps; or forward, backward, optimizer step) |
| `{{METRIC_NAME}}` | Name and unit, and whether lower or higher is better |
| `{{METRIC_COMMAND}}` | The production run at one operating point that prints the metric, with its warm-up |
| `{{METRIC_RUNS}}` | Separate runs per number (default 5) |
| `{{GATE_NAME}}` | The correctness check, and whether a higher or lower score is better |
| `{{GATE_COMMAND}}` | The command that prints its score, or pass or fail |
| `{{GATE_RUNS}}` | Baseline runs of the gate, to measure its spread (default 3) |
| `{{SCOPE}}` | What workers may change: config and flags, kernels, framework or engine code, numerics, data layout |
| `{{BUILD}}` | How to make an isolated copy: worktree, env, build or install steps |
| `{{BOUND_HINTS}}` | Optional. A known bound model, peak numbers, or limits from the vendor |
| `{{LEASE_TOOL}}` | A GPU lease tool (default: `generated-plugins/ammo/scripts/gpu_reservation.py` under the pave-init plugin root, copied into the run folder; standard library only) |
| `{{FLOOR_GAP_PCT}}` | Stop when the metric is this close to the bound |
| `{{BACKSTOP_PCT}}` | The smallest metric gain worth building |
| `{{RUN_DIR}}` | Run folder, outside version control (default `<code>/.optimize/<run-id>/`) |
| `{{BRANCH}}` | Run branch (default `optimize/<run-id>`) |

## Conditions every agent upholds

1. **Production parity.** Every measurement uses `{{TARGET}}` unchanged,
   with no debug or profiler flags. Profile in a separate run; a profiled
   run never counts as a metric run, because profilers slow what they
   measure.
2. **GPU leases.** Every GPU command runs on GPUs leased for it ("GPU
   leases" below).
3. **Noise rule.** Each metric number is `{{METRIC_RUNS}}` separate runs. A
   change is better at an operating point when its worst run beats the
   incumbent's best run. It is worse when its best run loses to the
   incumbent's worst run. Anything else is a tie, so run-to-run noise never
   ships a change. Stop the runs at a point as soon as one run fails to beat
   the incumbent's best: it can no longer be better there.
4. **Correctness gate (hard).** The baseline runs the gate `{{GATE_RUNS}}`
   times. A scored gate fails when it scores worse than the worst baseline
   run. A pass-or-fail gate must pass every run. No change ships past a
   failed gate, numerics changes included.
5. **Evidence.** Every number in any message or file comes from an artifact
   in `{{RUN_DIR}}`: the command, its raw output, and a fingerprint (source
   tree hash, GPU type, driver, CUDA, key library versions, settings). Any
   stored run, profile, or dossier is reused while the inputs it came from
   are unchanged. A reviewer reruns only the number that decides a verdict,
   under its own lease, and reads saved profiles instead of capturing new
   ones.
6. **Arithmetic lives in scripts.** Mined numbers, the noise rule, the gate
   rule, EV, and the stop verdict each come from one script, written once
   and kept in `{{RUN_DIR}}`. Never compute, copy, or edit them by hand.

## GPU leases

The lease tool keeps one lease table for the host. Its contract: reserve N
GPUs for a unique owner and print their ids, refuse at once when not enough
are free, release by owner, and show the table. With the default tool:

```bash
OWNER=<run-id>:<agent-name>
CVD=$(CUDA_VISIBLE_DEVICES={{GPU_IDS}} python3 {{LEASE_TOOL}} reserve \
      --num-gpus <N> --session-id "$OWNER" --lease-hours <H>) \
  && CUDA_VISIBLE_DEVICES=$CVD <command>
python3 {{LEASE_TOOL}} release-session --session-id "$OWNER"
python3 {{LEASE_TOOL}} status --table
```

- `<N>` is `{{WORLD_SIZE}}` for a metric, gate, or profile run, and 1 for a
  kernel benchmark or a micro experiment. A multi-GPU run gets one
  contiguous block.
- `<H>` covers that one command. Leases do not extend themselves.
- A refusal means the pool is full. Do CPU work, then try again. Never wait
  in a long sleep loop.
- Release before you return. The lead releases a worker's leases when it
  logs that worker's DONE or RESPAWN.
- Never stop a process you did not start: it may be another run's live
  work. Report it to the lead.
- Contention makes a result invalid. If another process appears on your GPUs
  during a timed run, discard the run and rerun it.

## Physical bound

For each operating point, compute the best value the metric could reach on
this hardware. Start from `{{BOUND_HINTS}}` when given. Otherwise derive it:

- Each GPU phase costs at least the larger of (bytes it must move /
  achievable memory bandwidth) and (FLOPs it must do / achievable peak
  FLOPs at its precision).
- Each transfer it cannot avoid (host-device, GPU-GPU, network) costs at
  least bytes / achievable link bandwidth.
- Work that could overlap other work adds nothing to the bound. Host time
  that could hide behind GPU work is removable.
- The bound is the sum along the critical path, in the metric's unit.

Measure achievable bandwidth and FLOPs with a short microbenchmark. Record
the spec-sheet numbers beside them, and use the measured ones. When a change
alters precision or the work itself, recompute the bound.

## The math: Amdahl's law

Express the metric as time per unit of work (latency, step time, time per
item). For a throughput metric, use its inverse. Then, for any component:

| Term | Meaning |
|---|---|
| `T` | The clean metric run's wall time per unit of work |
| `f` | The component's share of `T`: `f = f_phase x phase_busy x phase_share` |
| `f_phase` | Its share of GPU time inside its phase. Never use it alone as the multiplier. |
| `phase_busy` | Merged kernel time / wall time of that phase, from the same trace. Merge overlapping kernels; never sum them. |
| `phase_share` | That phase's wall time / `T`, from the clean metric run, not a profiled run |
| `s` | Component speedup, old time / new time |
| `component bound` | The least time the component can take on this hardware (physical bound rules) |
| `removable` | (component time - component bound) / component time |

- **Projected gain** = `f x (1 - 1/s)`.
- **Addressable share** = `f x removable`. No candidate may project more. A
  component whose addressable share is below `{{BACKSTOP_PCT}}` cannot pay
  off.
- **Required speedup** to reach the backstop (both as fractions):
  `s_req = 1 / (1 - backstop / f)`.
- Shares are always of `T`, never of a sum of phase times.
- Disjoint gains add. Overlapping gains do not: measure overlapping work
  across one larger boundary.
- The cumulative gain is the baseline metric over the incumbent's metric
  (inverted when higher is better). Never multiply per-round gains.

## The loop

Plan one stage at a time, from the previous wave's files. Each stage is one
or more waves, within the skill's wave cap. The lead reads files and rules;
every number and every decision table comes from a worker.

Workers and reviewers never see this file unless a subtask points to it.
Every subtask lists this file under Inputs and names the sections that
apply to it. Each track's subtask puts all four gates in Acceptance, and its
review brief quotes gate 4's reviewer paragraph under Check for.

After every stage, run a stage audit even when no seam spans workers: a
whole-goal brief whose Goal is that stage's Exit line, sent to a newly
spawned reviewer that reviewed no worker in that stage. Repair findings as
the skill's Step 6 says. A stage audit that still returns REVISE after 2
repair rounds is an ESCALATE, and the lead rules.

```
[1 baseline] -> [2 mine] -> [3 debate] -> [4 parallel tracks] -> [5 integrate] -> [6 stop or loop]
                   ^           ^                                                         |
                   |           +------------------ shipped nothing ----------------------+
                   +------------------------------ new incumbent ------------------------+
```

### 1. Baseline (round 1 only)

Four parallel workers:
- **Metric:** `{{METRIC_COMMAND}}` at every point in `{{OPERATING_POINTS}}`,
  `{{METRIC_RUNS}}` runs each, with the wall time of each phase in
  `{{PHASES}}`.
- **Gate:** `{{GATE_COMMAND}}`, `{{GATE_RUNS}}` runs, scores and spread.
- **Profile:** one nsys capture of `{{METRIC_COMMAND}}` per operating point
  (with the trace options the system needs, such as CUDA graph node tracing
  or child process tracing), exported to SQLite, one per GPU rank when
  `{{WORLD_SIZE}}` is above 1. Then ncu on the kernels that hold the most
  GPU time. It also writes `constraints.md`: the correctness rules every
  change must keep, including the system's own hazards (for example CUDA
  graph capture safety).
- **Bound:** the microbenchmark and the bound per operating point.

Exit: `rounds/1/baseline/summary.json` holds the metric, the phase times,
the gate spread, and the gap to the bound per operating point, each with its
artifact.

### 2. Bottleneck mining

Mining reads the evidence that exists and runs no new metric run. In round 1
that is the baseline. In later rounds stage 5 has already run the mining
script on the new incumbent, so the round starts at 2b with those tables.
Three waves:

**2a. Tables (one miner, round 1).**
- Split the trace by phase first. Set aside one-time phases (warm-up,
  capture, compile), then rank each phase in `{{PHASES}}` at steady state.
- A kernel whose count is far from its expected count per unit of work is
  transient until the trace proves otherwise.
- Partition every kernel into families by symbol patterns (for example GEMM,
  convolution, attention, elementwise and reductions, communication, memory
  copies, compiler-fused kernels). The partition is complete: any remainder
  is named and its size disclosed.
- Per family and phase: kernel count, time, `f_phase`, `phase_busy`,
  `phase_share`, `f`, the component bound, `removable`, and the addressable
  share.
- The time no family owns (inter-kernel gaps, launch overhead, host time) is
  its own slice.
- Per rank, when `{{WORLD_SIZE}}` is above 1: busy time, stragglers,
  collective skew.
- Select the families with the largest addressable share, plus the
  cross-cutting slice.
- Output: `rounds/N/mining/tables.md` and `mined.json`.

**2b. Dossiers (one worker per selected family, plus one for the
cross-cutting slice).** Facts and bounds only. No proposal, ranking, or
speedup estimate. Every claim cites a trace row, a `mined.json` field, or a
file and line. Sections, in order:
1. Kernel chain in one unit of work, in trace order: symbol, count,
   duration, phase.
2. Source map: the dispatch path from the entry point to each kernel, a
   file and line for every hop.
3. Component bound: check the miner's value against bytes and FLOPs per
   call for this shape and dtype. Counter claims (bandwidth, occupancy,
   stalls, tensor-core use) need targeted ncu on that kernel. If the check
   fails, end the dossier `UNGROUNDED: <reason>` and the miner fixes the
   tables.
4. Removable fraction: the bytes that need not move and the launches that
   need not happen, with the fusion boundary that limits it.
5. Technology landscape (top 3 families only): how the kernel is written
   (Triton, CUTLASS, CUDA C++, a library), the SM generation, the op's
   character, and the nearest mature kernel for this op, shape, and dtype.
6. Correctness rules: the rules in `constraints.md` that touch these
   kernels.

The cross-cutting dossier replaces sections 1-4 with: gap accounting per
unit of work, launch overhead against kernel time, the boundaries of any
captured or compiled regions, and the per-rank comparison.

**2c. Report (one worker).** Fold the tables and the dossiers into
`rounds/N/mining/bottleneck_analysis.md`. Paste the tables as they are.
Check that no two dossiers count the same kernel time. Disclose every
ungrounded dossier.

Exit: `bottleneck_analysis.md` ranks every slice by addressable share, and
each ranked number traces to `mined.json`.

### 3. Candidate debate

Four short waves. Each wave's workers read the previous wave's files.

**3a. Propose.** 2-4 champion workers each propose 2-3 candidates alone,
from the same report, inside `{{SCOPE}}`. They differ by component,
mechanism, or technology. Each candidate carries:

```
- Mechanism: <what you author, in brief>
- Slice: <the slice it attacks, with its f and addressable share from mined.json>
- Precision: <lossless | lossy> and why
- Baseline: <the production implementation it replaces, with trace evidence>
- Technology: <Triton | CUTLASS | CUDA C++ | library | compiler pass | host code | config>
- Evidence: <tier: analysis | measured | profiler-confirmed> and
  <scope: bound | proxy | production boundary>
- Projection: <per operating point: f, s, f x (1 - 1/s), s_req>
```

A champion may run small experiments to test its claim, on a lease, with
the script and log kept under `rounds/N/debate/experiments/`. It does not
change the system's source in this stage. A proxy measurement (a harness
that is not the production path) shows a kernel works; it cannot size a
gain.

**3b. Critique.** Fresh workers. Each critiques the candidates of one
champion file it did not write: the projection, the evidence scope, the
hardware assumptions, precision, the rules in `constraints.md`, a
regression at another operating point, or a mechanism already tried.

**3c. Rebut.** One fresh worker per champion file answers each critique
from that file's evidence, concedes it, or names a mitigation. It may run
one small experiment when that settles a point.

**3d. Select (one worker, with a script).** For each surviving candidate:
1. Its admissible projected gain `f x (1 - 1/s)`, from bound or
   production-boundary evidence, reaches `{{BACKSTOP_PCT}}`. A proxy-only
   gain, or an open objection that the evidence does not cover the
   production boundary, makes it zero.
2. `P`, 0-10, the chance it passes stage 4 as proposed, judged from its
   evidence tier and the critiques it could not answer. Write the reason
   beside each `P`.
3. `EV = P/10 x projected gain`.
4. Rank by `EV`. Take winners in rank order, up to the GPU count divided by
   `{{WORLD_SIZE}}` and within the wave cap. No two winners are the same bet
   (same slice, same mechanism kind, same technology). Prefer winners on
   disjoint slices, so their gains add. One slot may go to the largest
   admissible gain with `P` of 3 or more, as a long shot that must prove its
   gain at the production boundary first.

Exit: `rounds/N/debate/winners.md` lists every candidate with its numbers,
its verdict, and the reasons.

### 4. Parallel tracks

Each winner becomes one track, built by one worker. Tracks overlap in time
but stay isolated:

| Isolated per track | How |
|---|---|
| Code | Its own git worktree and branch, made with `{{BUILD}}` |
| Env | Its own env. Never install into the incumbent's env. |
| GPUs | Its own leases, for GPU commands only |
| CPU | When host time is part of the metric, timed runs get their own cores (`taskset`), away from builds |
| Write set | `rounds/N/tracks/<id>/` and its worktree |

Each track compares to the incumbent's stored runs and profiles
(condition 5).

Gates, all four in the track's Acceptance:
1. **Correctness:** component tests against the old implementation, then
   the gate per condition 4.
2. **Mechanism:** an nsys or ncu A/B at the production boundary shows the
   targeted slice got cheaper, by roughly the projected `s`.
3. **Metric:** better than the incumbent at one or more operating points and
   worse at none, per condition 3.
4. **Floor argument:** the worker shows why its final implementation cannot
   get faster on {{HARDWARE}} under `{{TARGET}}`, `{{SCOPE}}`, and the gate.
   It writes `rounds/N/tracks/<id>/floor.md`: the slice's time, its
   component bound, the gap, and the reason the gap cannot close, from nsys
   at every operating point and ncu on the changed kernels. A reason is
   valid only when it is one of these, with its evidence:
   - **Physical:** it already runs at the measured achievable bandwidth or
     FLOPs, within measurement noise. Cite the ncu result.
   - **Dependency:** the work must run in series with other work and cannot
     overlap it. Cite the trace and the data dependency.
   - **Constraint:** closing the gap needs a change outside `{{SCOPE}}` or
     `{{TARGET}}`, or a change that fails the gate. Cite the rule or the
     failed attempt.
   - **Tried:** an approach that could close the gap was built, measured,
     and lost. Cite its numbers, and say why no other known approach
     applies.

   "Not tried", "too hard", and "no time" are not reasons.

Run the gates cheapest first: component tests, then gate 2, then the full
gate and the metric at the same time on separate leases, then gate 4. Gates
2 and 4 use one capture of the final build; capture again only after the
code changes. After a REVISE, rerun only the gates the change can affect.

The track's reviewer reviews gate 4 like every other gate, and its job there
is to find headroom: rerun the number that decides it, test each reason
against its evidence, and look for known approaches the worker never tried.
A headroom claim that names an approach and its projected gain is a HIGH
finding; a claim that names none is not a finding. The reviewer's PASS
states in one line that it agrees the implementation cannot get faster
under the stated constraints, with the gap that remains. Headroom is a
REVISE: the worker improves the implementation and reruns the gates it
affects.

At the skill's review-round limit, the lead's ruling is set in advance: an
open gate 4 finding lets the change ship on gates 1-3, and the headroom
claim goes to the next round's debate. An open gate 1-3 finding is a
REJECT.

Track verdicts: SHIP; GATED SHIP (it wins at some operating points and loses
at others, and the system can enable it only where it wins); or REJECT. A
REJECT names the mechanism and why it failed, so later debates do not repeat
it. Record the measured `s` beside the projected `s`.

Exit: every track has a verdict, its evidence, and, for SHIP and GATED SHIP,
a floor argument its reviewer agreed with or a recorded headroom claim.

### 5. Integrate

One worker applies the SHIP and GATED SHIP changes to the run branch, one
commit per change. It runs the gate and the metric for the combination, at
the same time on separate leases; condition 5 reuses a track's runs when
the branch equals that track's build. If the combination is worse than the best single change, it ships
the best subset that passes. The result is the new incumbent.

The worker then captures the new incumbent's metric, phase times, gate, and
nsys evidence, and runs the mining script on it. Those tables are the next
round's 2a output. It writes `rounds/N/decision.json`: the gap to the bound
per operating point, the top addressable share, what shipped, projected
against measured gain per track, and the stop verdict from the stop script.

Exit: `decision.json` exists, and every number in it traces to an artifact.

### 6. Stop or loop

The lead reads the stop verdict in `decision.json`:
- Every operating point within `{{FLOOR_GAP_PCT}}` of its bound: stop. The
  bound is reached.
- Top addressable share below `{{BACKSTOP_PCT}}`: stop. Nothing big enough
  is left.
- Two rounds in a row ship nothing: stop as exhausted.
- The round shipped: start the next round at 2b, on stage 5's tables.
- The round shipped nothing: start the next round at stage 3 on the same
  mining, with the REJECT mechanisms and the headroom claims passed to its
  debate. Start at stage 2 instead if evidence shows the mining was wrong.

Do not ask me between rounds. Ask once, after investigation, as the skill
says. Use that round to fill blank slots and to confirm the GPUs.

## Deliver

- The run branch `{{BRANCH}}`, one commit per shipped change. Do not push.
- `REPORT.md` in `{{RUN_DIR}}`, written by one worker, in plain English: the
  baseline, the bound, the incumbent's metric per operating point, the
  cumulative gain, the gate scores, what shipped, what failed and why,
  projected against measured gains, why the run stopped, and each shipped
  change's floor argument with its reviewer's verdict. Every number links to
  its artifact.
