---
name: ammo
description: Profile and optimize vLLM inference on NVIDIA CUDA or AWS Neuron. Use when targeting a specific model, accelerator, dtype, parallelism, and workload to improve latency. Runs a staged multi-agent campaign (baseline, profiling, debate, isolated implementation tracks, integration, report) that takes hours; not for a one-off kernel question, a code review, or a single benchmark run, which a direct subagent handles.
---

# AMMO - Automated Model Micro-Optimizer

AMMO is a staged, multi-agent accelerator optimization campaign. It searches widely, builds each candidate in an isolated worktree, and ships only changes that are production-active, correct, and statistically faster.

The Lead Role and stage-dispatch instructions apply to the root orchestrator. Child agents use the shared campaign constraints and their own role assignment. Reading this file does not change a child's role. Role behavior lives in `agents/`, stage choreography in `orchestration/`, and domain policy in `references/`. Open a linked file when its stage or decision becomes active. Do not preload the whole corpus.

## Operating Principle

Preserve agent judgment for the hard calls: reading profiles, designing candidates, critique, debugging, and weighing evidence. Scripts, schemas, and hooks enforce only mechanical invariants: state shape, legal transitions, required artifacts, ownership, and arithmetic consistency. A mechanical check never substitutes for an agent's technical review.

Every rule has exactly one owning document. Other files may say why or when the rule applies, but they must point to the owner instead of restating the rule. If two documents disagree, the higher one on this list wins:

1. The user-approved target and policy in `state.json`.
2. `schemas/state.schema.json` and `scripts/transitions.json` for state shape, enums, and legal transitions.
3. The topical `references/*.md` file.
4. The stage's `orchestration/*.md` file.
5. The role's own `agents/*.md` file.

Record an explicit user override and its scope. Never bend raw evidence to make it look supported. Do not add approval or testing gates. Complete the binding gates; add further tests when a change, failure, or unresolved question requires them.

## Accelerator Runtime Binding

Read server-injected `AMMO_RUNTIME_PROFILE`: missing means `vllm-cuda`; otherwise use `vllm-neuron`. Do not ask the user to choose a backend. On `vllm-cuda`, use CUDA graphs, Nsys, and NCU as this skill's profiling references define. On `vllm-neuron` (the `pumice-neuron` profile), use the Pumice worktree, `scripts/run_pumice_grpc_bench.py` for timing, and `scripts/runtime_ops.py capture-neff`/`normalize` for attribution (the runner has no `--accelerator-profile`); `references/accelerator-binding.md` owns the details.

AMMO's seven stages, agents, audits, five binding gates, GSM8K accuracy rule, end-to-end speedup criteria, workloads, statistics, verdict semantics, and stop logic are identical on both runtimes. On both runtimes, profiler-free timing is authoritative. `vllm bench serve` is diagnostic only for batch targets. Serving targets use the managed runner under `references/serving-campaign.md`. The serialized status stays `GPU_BLOCKED` on both runtimes.

## Serving Deployment Binding

When `target.json.serving` exists, read `references/serving-campaign.md` before dispatch. It owns the serving instrument, objective units, evidence paths, and domain-specific stop check. Keep every stage, agent, audit, implementation-isolation rule, and authored-mechanism requirement below. The batch sweep, serial Amdahl projection, diluted-pass exception, and batch-only short-circuit do not apply to this target. Scaffold with `new_target.py --serving-manifest` and synchronize evidence with `ammo_state.py serving-sync`.

## Lead Role

You are the lead orchestrator. You scaffold, delegate, moderate, reconcile, update state, and enforce gates; you never implement an optimization and you never judge your own technical claim.

- Keep every stage and role below; never skip or merge them for speed.
- Read the relevant state at dispatch, transitions, reconciliation, context recovery, and when related state changes. Update stage, round, audit, and per-track fields when they actually change.
- Freeze every parameter the user set: model, hardware, dtype, TP/DP/EP, ISL/OSL, batch, maximum length, and serving flags. Changes to frozen parameters or approved scope need explicit user approval. Repairs within an approved candidate, including its declared mechanism flags under `references/validation-defaults.md`, remain authorized.
- Spawn `ammo-investigator` when evidence is ambiguous, a failure is unexplained, or a consequential decision has no established answer.
- Talk to live teammates with `SendMessage`; plain text in the lead thread does not reach them. To retire a teammate, follow `references/shutdown-protocol.md`.
- Never kill a process you do not own. Never use a GPU you have not reserved. `references/gpu-pool.md` is the only authority on GPU ownership.

## Invocation

Collect every parameter the user gave. Scaffold once with `.venv/bin/python scripts/new_target.py`, and omit only the flags the user did not set. `references/workload-invocation.md` owns defaults, workload-matrix handling, `max_num_seqs`, DP/EP semantics, and the canonical command.

After scaffolding, `target.json`, `state.json`, and `rounds/{N}/constraints.md` are the frozen target contract. `references/artifact-layout.md` owns artifact paths.

## Stage and Agent Contract

Every stage and role below is mandatory when its trigger fires.

| Stage | Required agents | Required outcome | Detailed authority |
|---|---|---|---|
| `1_baseline` | lead + fresh `ammo-researcher` | clean production-parity baseline/golden capture and a separate bounded attribution profile | `agents/ammo-researcher.md`, `references/nsys-profiling-guide.md` |
| `2_bottleneck_mining` | lead + `ammo-researcher` | grounded `bottleneck_analysis.md` with `## Technology Landscape`, workload dilution, component shares, and physical bounds | `agents/ammo-researcher.md`, `references/e2e-delta-math.md` |
| `3_debate` | lead + 2-4 `ammo-champion` | independent candidates, adversarial debate, an eligibility record, and 2-3 winners | `orchestration/debate-protocol.md` |
| `4_5_parallel_tracks` | one `ammo-impl-champion` and one paired `ammo-transcript-monitor` per winner | isolated implementation, complete gates, a terminal verdict, durable evidence | `orchestration/parallel-tracks.md` |
| `6_integration` | lead; `ammo-resolver` plus independent merge review when required | the exact validated composition, an integration measurement, SHIP or round EXHAUSTED | `orchestration/integration-logic.md` |
| `7_campaign_eval` | lead | mechanical continue-or-stop from current opportunity | this file, Stage 7 |
| `7b_report` | fresh `ammo-report-writer` + fresh adversarial fact-checker | artifact-grounded `REPORT.md` accepted with `ok:true` | `agents/ammo-report-writer.md` |

`ammo-auditor` is mandatory after Stage 1 (`T_AUDIT_S1`), Stage 2 (`T_AUDIT_S2`), Stages 4-5 (`T_AUDIT_S45`), and Stages 6-7 (`T_AUDIT_S67`). Auditors may hand bounded evidence tasks to `ammo-delegate`. `ammo-investigator` is available at any uncertain fork or root-cause problem. The implementation role is registered as `ammo-impl-champion` and the report role as `ammo-report-writer`.

**Team-member vs subagent contract:** Only `ammo-champion`, `ammo-impl-champion`, and `ammo-transcript-monitor` are team members. Spawn each one with a unique `name` and `run_in_background=True`. The first named spawn forms the session's single implicit team, and later named spawns join it. Do not pass `team_name`; there is no TeamCreate tool. While a teammate's round is active, reuse it by addressing its `name` with `SendMessage`. Never re-spawn a `name` that is still on the roster. Put the campaign round and a bounded attempt suffix in any name that can recur. When a business identifier such as `OP-001` names a track, keep the raw `op_id` in state, prompts, monitor ledgers, and artifact paths. All other roles — `ammo-researcher`, `ammo-auditor`, `ammo-investigator`, `ammo-delegate`, `ammo-report-writer`, and `ammo-resolver` — are one-shot subagents: spawn them with `subagent_type` + `prompt` only, and no `name`. `state.json.team_name` is descriptive bookkeeping, never a runtime handle.

### Stage 1: Baseline Capture

Dispatch a fresh `ammo-researcher` with `task_type: baseline`, `artifact_dir`, the round, and the attempt. It runs exactly two logically separate measurements:

1. a profiler-free production E2E baseline plus golden references;
2. a bounded runtime attribution capture: Nsys on `vllm-cuda`, or Neuron Profile and neuron-explorer evidence on `vllm-neuron`.

Profiler-run timing is never the official E2E timing. When the researcher finishes, run the mechanical gate and `T_AUDIT_S1`. Fix every consequential finding before Stage 2.

### Stage 2: Bottleneck Mining

Dispatch `ammo-researcher` with `task_type: mining`. It mines the production evidence that already exists; it never recaptures just to make a better story. In Round 1 that evidence is the Stage-1 baseline and attribution capture. After a material SHIP, the promoted E2E baseline is the prior round's Stage-6 integration result (a single-pass short-circuit writes its copy into that same slot), plus the attribution and activation evidence already on disk that still applies. Do not add a fresh post-SHIP baseline sweep, profiling pass, or provenance sidecar. Ground every binding claim in primary artifacts. NCU is CUDA-only. On Neuron, normalize Neuron Profile evidence through `scripts/runtime_ops.py` before existing mining math consumes it.

Run `T_AUDIT_S2`. The audit must independently confirm that the measured bottleneck, the opportunity math, the code mapping, and the candidate frontier are real before debate starts.

### Stage 3: Candidate Proposal and Adversarial Debate

Spawn 2-4 `ammo-champion` agents. Each one reads the full profile and submits 2-3 candidates it grounded on its own. Optional lenses only suggest where an agent looks first; they never limit its search.

The lead runs the full protocol in `orchestration/debate-protocol.md`: proposals, eligibility, one round of critique and rebuttal, and winner selection by expected value. Debate is mandatory. A scope objection may be closed only by the lead or critic, and only with cited evidence that the declared production boundary was actually measured. An owner cannot close its own objection.

Only production-boundary magnitude may enter expected-value ranking. Proxy or synthetic evidence can show feasibility or a bound; it can never supply production E2E magnitude. The protocol owns the evidence ladder and `references/e2e-delta-math.md` owns projection.

### Stages 4-5: Parallel Tracks

For each winner, spawn one `ammo-impl-champion` (`isolation: worktree`, so the Agent tool creates the track's own branch and worktree) and one read-only `ammo-transcript-monitor`. Record each track's stable identity in state. The monitor reviews methodology continuously and runs its own collision checks; it does not implement and does not author the track verdict.

Keep all five binding gates — 5.1a, 5.1b, 5.2, 5.3a, 5.3b — as defined in `references/validation-defaults.md`.

`references/validation-defaults.md` owns the exact comparator, tolerances, activation proof, production-parity rules, and verdict semantics. Before a track may report `FAIL`, the implementer must exhaust the applicable fixes and the canonical fallback path in `references/impl-track-rules.md`. Route unexplained failures to `ammo-investigator`. `DILUTED_PASS` is discovered only after validation; it stays canonical status `PASS` with `diluted:true` and contributes measured E2E only.

Do not enter Stage 6 until every track is terminal: `PASS`, `GATED_PASS`, or `FAIL`. Reconcile state against the primary gate artifacts before that transition. Once all tracks are terminal, run the projection-accuracy diagnostics and `T_AUDIT_S45`.

### Stage 6: Integration Validation

Follow `orchestration/integration-logic.md` and keep the whole decision matrix:

- No passer: the round is EXHAUSTED.
- One unchanged passer: the single-track short-circuit is permitted.
- Multiple compatible passers: compose them, then rerun full-model correctness and a clean E2E.
- Overlap, gating, or merge ambiguity: use the prescribed dominance logic, or spawn `ammo-resolver` plus an independent merge review.

On SHIP: run the pre-ship mechanical checks, integrate the exact validated change, promote its environment contract, clear the experimental environment, refresh the golden references after promotion, and run `T_AUDIT_S67`. The canonical Stage-6 integration result becomes the new production baseline — a combined integration sweep supplies it when several tracks pass, and a single-pass short-circuit copies the validated Stage-5 result into the same integration slot. Compute cumulative gain as a direct ratio — original Round-1 baseline latency divided by current integrated latency — and never multiply round deltas.

### Stage 7: Campaign Evaluation

Stage 7 is autonomous and mechanical: stop iff the current production baseline's `bottleneck_mining.top_addressable_e2e_pct` — the largest measured `f_e2e × removable_fraction` across non-overlapping slices — is below `min_e2e_improvement_pct`; otherwise continue. A material SHIP may never use its pre-promotion mining for this decision.

When the stop condition fires, the terminal token records how the round ended: use `campaign_complete` after a SHIP integration status (`single_pass`, `combined`, or `gated_pass`) and `campaign_exhausted` after an EXHAUSTED/failed integration. Never pick between them by narrative preference. Before you set the terminal token (and before Stage 7b), enumerate the roster and drain every remaining teammate per `references/shutdown-protocol.md`.

- After SHIP, if the change is material, create the next round at `2_bottleneck_mining` and mine the promoted prior-round Stage-6 integration/single-pass evidence. If the newly mined addressable impact is below the floor, terminate as `campaign_complete` from the audited SHIP outcome. Otherwise run the normal Stage-2 gate and `T_AUDIT_S2`, then enter `3_debate`.
- After EXHAUSTED, reuse the mining that is still valid, append structured `exhausted_technologies`, and enter `3_debate` with a genuine mechanism pivot.
- If mining was proven wrong, set boolean `mining_invalidated:true` with a reason and re-enter Stage 2.
- If every shipped track is `diluted:true`, reuse valid mining as for EXHAUSTED, because the measured baseline did not materially move.
- Let every track in the current cohort reach a terminal state before you start the next round.

Do not stop for qualitative "good enough," time, difficulty, or token reasons. `references/validation-defaults.md` owns the invalid-stop list and the minimum-improvement policy.

### Stage 7b: Report and Fact Check

At `campaign_complete` or `campaign_exhausted`, spawn a fresh `ammo-report-writer`. It must build every factual claim from campaign artifacts, disclose missing or failed evidence, and get `ok:true` from a fresh adversarial fact-checker before returning the report.

## Audit Gates

The lead follows `orchestration/audit-protocol.md`. `references/audit-invariants.md` owns the stage-specific questions, and they reach the auditor at the prescribed time.

Every new round entry must carry `"audit": {}` from creation, so the mandatory audit gates cannot fail open.

For each of `T_AUDIT_S1`, `T_AUDIT_S2`, `T_AUDIT_S45`, and `T_AUDIT_S67`:

1. Spawn a fresh `ammo-auditor`. Do not preload the Phase 2 checklist.
2. The auditor first writes `## Phase 1 - Independent Reconstruction` from primary evidence and critically weighs any delegate findings.
3. The hook then delivers `## Phase 2 - Checklist Verification`.
4. The lead fixes consequential findings and re-audits. It never edits evidence to manufacture a pass.
5. After the bounded review loop, apply `audit-protocol.md` § Repair and Re-audit: Stage 45 may quarantine an attributable track; Stage 67 removes and remeasures the implicated promoted code; shared or uncertain scope pauses.

Auditors and delegates never write campaign evidence. Independent audit is the semantic gate; hook validation is only its mechanical backstop.

## State Management

`schemas/state.schema.json` owns the closed state shape and enums. `scripts/transitions.json` owns legal transitions. Do not restate or invent enum values in prose. Keep these stage values exactly:

`1_baseline` -> `2_bottleneck_mining` -> `3_debate` -> `4_5_parallel_tracks` -> `6_integration` -> `7_campaign_eval` -> `7b_report`.

At each real transition:

- set the current stage and its matching `started_at`/`completed_at` timestamps;
- write per-track gate, verdict, and metric fields when their primary artifacts land;
- write the typed `selected_candidates` contract and debate enrichment at selection;
- write mining enrichment from the structured `f_e2e` table before advancing;
- keep provenance, attempts, audit records, exhaustion memory, shipped identities, the environment contract, and the direct cumulative speedup.

Use `scripts/ammo_state.py` for state operations and validation. Frontend fields consume campaign truth; they are never a second policy source.

## Pause and Resume

Pause only when the user asks or the audit escalation path requires it. A pause keeps state and artifacts; it does not turn unfinished tracks into failures.

On resume:

1. Read this file, `state.json`, and the active round's primary artifacts.
2. Confirm `campaign.status`, `campaign.current_round`, and `campaign.current_stage`.
3. Inspect accelerator reservations and clear only stale reservations owned by the crashed session.
4. Reconcile debate or track state with on-disk artifacts; never infer completion from narrative alone.
5. Reuse stable agent/task identities where possible and resume from the last verified gate.
6. If the campaign is terminal and `REPORT.md` is absent, run Stage 7b.

Repair malformed or legacy state through the schema/backfill path in `scripts/ammo_state.py`. Never rebuild it from guesses. Full artifact reconciliation lives in `orchestration/parallel-tracks.md`.

## Failure and Escalation

- Ordinary technical failure: investigate, fix it if the evidence says it is fixable, and continue the same stage.
- Unexplained correctness, activation, measurement, or integration failure: spawn `ammo-investigator` with the primary artifacts and one concrete decision question.
- Critical evidence or safety failure: halt the current stage, preserve the evidence, and resolve it before you advance.
- Escalate to the user only for missing authority or an unrecoverable conflict, never an ordinary engineering fork.

Record campaign-scoped blockers under `{artifact_dir}/blockers/`.

## Authority Index

Read only what the active task requires.

| Topic | Authority |
|---|---|
| Workload/configuration | `references/workload-invocation.md` |
| Artifact paths and authority | `references/artifact-layout.md` |
| Accelerator ownership/isolation | `references/gpu-pool.md` |
| Profiling | `references/nsys-profiling-guide.md` on `vllm-cuda`; `scripts/runtime_ops.py` plus the Neuron profiling skills on `vllm-neuron` |
| Opportunity/projection math | `references/e2e-delta-math.md` |
| Validation and verdicts | `references/validation-defaults.md` |
| Implementation fallback/gating | `references/impl-track-rules.md` |
| torch.compile/CUDA graphs | `references/torch-compile-contract.md`, `references/cudagraph-safety.md` |
| Audit checklist | `references/audit-invariants.md` |
| Messaging/delegation | `references/champion-common-patterns.md` |
| Shutdown | `references/shutdown-protocol.md` |
| Stage 3: candidates, evidence, debate, winner selection | `orchestration/debate-protocol.md` |
| Track choreography | `orchestration/parallel-tracks.md` |
| Integration | `orchestration/integration-logic.md` |
| Audits | `orchestration/audit-protocol.md` |

## Active Helpers

- `scripts/new_target.py`: scaffold target and state.
- Authoritative baseline, opt, profiling, correctness, and integration runner: read `target.json` `bench.runner` first. Use `scripts/run_pumice_grpc_bench.py` for `pumice_grpc_bench` (every `pumice-*` profile). Use `scripts/run_vllm_bench_latency_sweep.py` for `vllm_bench_latency`.
  The vLLM sweep exits with `Unsupported bench.runner` on any other value. `references/artifact-layout.md` § Bench Runner gives the Pumice invocation.
- `scripts/check_projection_accuracy.py`: append `## Projection Accuracy` diagnostics.
- `scripts/gpu_reservation.py status --table` / `scripts/gpu_reservation.py force-clear --all --session-id <crashed_session_id>`: inspect or clear owned stale reservations.
- `scripts/ammo_state.py`: validate, update, transition, and backfill campaign state.

Run Python helpers through the active worktree `.venv/bin/python`. Never modify a helper just to get past a failed semantic gate.
