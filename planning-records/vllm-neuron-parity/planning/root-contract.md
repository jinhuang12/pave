# Root node contract — vllm-neuron-parity

Frozen 2026-08-25 by the lead from approved `requirements.md` and verified
`system-map.md`. This contract changes only by returning to Stage 1 approval
(a conflict that touches goal, acceptance, effects, or authority routes to the
user, never around them). Planners reference it; they never copy or edit it.

AMENDED 2026-08-25 (Stage-1 user decision, Neuron 2.32 re-baseline): pin is
`vllm==0.24.0` via official branch `release-0.24.0.1.1.0`; fork sync manual
and user-owned, verified at kickoff preflight; hosts on SDK 2.32 (compiler
neuronx-cc 2.27.5334.0). The pinned base, release-branch name, and SDK
version are invocation-time run inputs — graph text stays pin-generic.
Concrete 0.21/0.24 values below are the values current at amendment time.

Source-structure rule (pave-spec §9.12.2): the absorbed engines — the
feature-port skill's 20-node flat graph and autoport's root+5-sub-workflow
bundle — bind BEHAVIOR (acceptance conditions, invariants, observed failure
modes) and never graph shape. The candidate seams listed in system-map.md
"Graph implications" are evidence-backed hints, not design authority. Derive
every node from this contract's goal and check it against the behavior
evidence.

## 1. Purpose

Bring the vLLM-Neuron platform plugin (fork `jinhuang12/vllm-neuron`, pinned
at the synced official release — `vllm==0.24.0` / `release-0.24.0.1.1.0`
since the 2026-08-25 amendment) to parity with upstream vLLM on NVIDIA GPUs,
measured on a
repo-tracked feature × model parity scorecard. One run of the generated
workflow: scan the upstream delta against the pinned base, cost each requested
target's routes — route A backport at the pin (reference sources: A1 upstream
GPU vLLM, and A2 tpu-inference when the target is TPU-supported; A2 is
design-port only, both its front-ends lower to JAX/XLA) and route B pin
upgrade (fallback, justified on cost; upgrade campaigns gate on a regression
matrix and run exclusively) — rank the backlog, and, per user-approved
target + route + source, execute campaigns (type A feature port, type B model
port; shared intake and shared validation back-end) until each target passes
its kickoff-declared correctness gate and performance gate against a GPU
baseline. Each campaign closes through gate 3 as exactly one of: evidence-
backed PR opened on the fork; no-benefit closure ("correct port, not viable
on this hardware" — scorecard cell `measured-not-viable-at-pin`, evidence and
upstream-issue draft kept); or honest blocked terminal.

Out of scope:
- Continuous upstream tracking — the delta is computed once per run against
  the pinned base at invocation.
- Fork sync with official plugin releases (stays manual).
- Merging PRs (stays human).
- Hardware provisioning of any kind, ever.
- Changing kickoff-declared metrics, thresholds, or methods without an
  explicit user decision recorded in run state.

## 2. Inputs

- The fork `jinhuang12/vllm-neuron` at `/Users/jinhun/GitHub/vllm-neuron`,
  pin `vllm==0.24.0` after the user's manual sync of `release-0.24.0.1.1.0`
  (kickoff preflight verifies the branch exists on the fork; read-only until
  a campaign's own worktree/branch).
- Upstream `vllm-project/vllm` (full local clone at `/Users/jinhun/GitHub/vllm`,
  tags through v0.28.0) — route-A1 source and route-B target.
- `vllm-project/tpu-inference` via `gh` (no local clone) — route-A2 source;
  TPU support detected from its `support_matrices/*.csv`.
- Standing Neuron and GPU instances named at invocation, reached over SSH —
  no fixed fleet; DLAMI baseline venv path discovered per host, never
  hardcoded.
- Per-target weight artifacts (e.g. drafter checkpoints) — existence verified
  at kickoff.
- Delegate skills in the NeuronAgenticDevelopment repo (equivalence,
  benchmark, profiling, compiler/runtime debugging, NKI, op lowering,
  parallelism/entitlement) — an EXTERNAL dependency deployed to `~/.claude`,
  not bundleable; availability preflighted at kickoff; dispatch passes
  through guardrails (see Effects).
- Absorbed references: `skills/vllm-neuron-feature-port` and the
  model-port/autoport released bundle — behavior evidence only.
- Persistent cross-run artifacts in the NeuronAgenticDevelopment repo:
  parity scorecard, ranked backlog, backport debt ledger,
  failure-fingerprint file (read at intake, updated at closures).
- The user's target request and instance list at invocation.

## 3. Effects and authority limits

Allowed effects:
- SSH to the standing instances named at invocation (in-band, in-workflow —
  never out-of-band handoff files).
- Parallel isolated git worktrees; campaign branches on the fork.
- Per-campaign venvs via the freeze-replicate recipe (~10 GB per replica).
- Autonomous recovery of the campaign's own assigned Neuron host(s) only,
  under an exclusive hardware-queue lease with pre-action identity
  re-verification (drain/checkpoint first; everything logged; no user gate).
- Branch push and PR open on the `jinhuang12/vllm-neuron` fork only.
- Serialized single-writer updates to scorecard / backlog / debt ledger /
  fingerprint file in the workflow repo (never on campaign branches).

Forbidden effects (blocking-hook expectation, ssh-wrapped forms included):
1. Mutating protected base branches (the pinned release branches —
   `release-0.24.0.1.1.0` and the historical `release-0.21.0.1.0.0` — plus
   `main`, `mainline`) on the fork or the vllm-project upstream remote.
2. Clearing the shared Neuron compile cache
   (`$VLLM_CACHE_ROOT/neuron/compile_cache`,
   `~/.cache/vllm/neuron/compile_cache`, `/var/tmp/neuron-compile-cache`).
3. `cp -a` venv cloning and any pip write into `/opt`.
4. `neuronx_distributed*` (NxDI) imports in ported code.
5. Autonomous reboot/reset of the GPU baseline instance (user confirmation
   only).
6. Removing or bypassing the benchmark skill's provisioning STOP gate.

Delegate-dispatch guardrails (allowed-effect qualifier): intercept the two
delegates whose documented remedies clear the compile cache; always pass
`--target-stack vllm_neuron` to the equivalence adapter (its auto-detect
defaults to the forbidden NxDI stack); scope the benchmark skill away from
its self-provisioning branch.

Authority limits:
- Exactly 3 user gates per campaign: kickoff contract, design approval,
  close-out. The workflow refuses to start a campaign without a complete
  kickoff contract (targets, routes, sources, thresholds, methods, models,
  hosts, test layout, spend notes, delegate/venv/weights/GPU-baseline
  preflights, pinned-release-branch-present-on-fork + SDK-uniformity
  preflight (2.32 / neuronx-cc 2.27.5334.0, NumPy ≥ 2.2), deepseek
  carry-over decision).
- The agent that measured a number never adjudicates it; comparator sets are
  pre-registered before measurement; verdicts read stable artifacts, never
  first-sighting signals.
- Practical adversarial review (material-findings-only) after each
  high-stakes artifact: gap-scan/route-analysis verdicts, campaign designs,
  implementations (before hardware spend and before PR), measurement
  verdicts.
- Kickoff-declared criteria change only by explicit recorded user decision.
- PR merge is human. GPU-baseline reboot is user-confirmed.

## 4. Outcomes

- `run_complete` — SUCCESS (definition of done): every campaign the user
  approved at gate 1 has reached exactly one declared gate-3 closure
  (PR opened / no-benefit closure / blocked terminal), and the scorecard,
  backlog, debt ledger, and fingerprint file reflect each closure — settled
  by reading, per campaign, the close-out approval record plus its
  world-produced closure evidence (the PR URL resolvable on the fork via
  `gh`, or the recorded no-benefit/blocked evidence bundle) and the updated
  cross-run artifacts on disk. Never settled by the lead's own assertion.
- `run_paused` — the user pauses, or required hardware/capability is
  unavailable with resumable state preserved (maps to terminal status
  `incomplete` when a run closes in this condition).
- `run_blocked` — a required capability, resource, or authority is
  unavailable and no declared route remains (terminal status `blocked`).
- `run_aborted` — the user stops the run before the approved campaign set
  closes (terminal status `closed_unaccepted`).

The planner derives intermediate outcomes freely; the root outcome set above
is fixed. Unexpected evidence that fits no declared outcome follows the
evolving-tier contract: block honestly, preserve the discovery, revise the
graph between runs — never invent an outcome or edge mid-run.

## 5. Roles

- Lead orchestrator (the generated skill's lead): routing, run state, gate
  presentation, hardware-queue arbitration, single-writer merges of the
  cross-run artifacts.
- User: approval authority at the 3 gates, criterion changes, GPU-baseline
  reboot confirmation, PR merge.
- Worker perspectives the graph must keep separate (exact role set is the
  planner's to derive): investigation (gap scan / route analysis / costing),
  campaign implementation, measurement, adjudication (never the measurer),
  adversarial review at the four high-stakes points, delegate dispatch.

## 6. Shared-state ownership

| Record | Single writer | Notes |
|---|---|---|
| Generated skill's run state | Lead | Schema-validated appends + compaction; points at artifacts, never inlines them (19.2 MB inline-history precedent) |
| Per-campaign state + evidence dir | That campaign's executor | One layout for both campaign types; one-file-per-event pattern |
| Scorecard / backlog / debt ledger / fingerprints | Lead (shared back-end), serialized | Repo-tracked, never on campaign branches; persist across runs |
| Hardware queue / lease record | Lead (queue service) | Exclusive leases; identity re-verified before any recovery action |
| Approval records | Lead, verbatim | Artifact existence is never approval |

## 7. Global budgets

- No attempt caps. Two-tier no-progress breaker: (1) failure-fingerprint
  no-identical-retry against the repo-tracked fingerprint file; (2) at every
  10 hardware attempts per target, the orchestrator halts and re-derives the
  approach from first principles — revised design, route change, or gate-3
  close-out.
- CPU-first increments (`VLLM_NEURON_CPU_MODE=1`) before hardware attempts.
- Conflict-aware scheduling: campaigns with overlapping predicted file
  surfaces serialize (`neuron_model_runner.py` > `platform.py` > scheduler >
  model subpackages > NIXL connectors); upgrade-route campaigns run
  exclusively.
- ~10 GB disk per venv replica; one compiler version across campaigns.

## 8. Lead-owned run-wide artifacts (drafted here, finalized at assembly)

- Evolution tier: **evolving** (user-approved 2026-08-25) — ships revision
  workspace, freeze script, seven-rule contract; v1 freezes before first
  real execution.
- Enforcement record: the six forbidden effects map to blocking hooks
  (deny-fragment precedent in the absorbed skill, extended with /opt-write,
  `cp -a` venv, and delegate cache-clear interception); everything else
  defaults to observing/reinjection; the P1–P9 rung table from the prior
  run is the template. Lead-alignment hook pair included by default.
- State contract: one machine-checkable schema as the single shape
  authority + validation helper; compaction designed in.
