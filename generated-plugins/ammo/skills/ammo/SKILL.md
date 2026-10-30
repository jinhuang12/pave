---
name: ammo
description: >-
  Profile and optimize Pumice inference latency on NVIDIA GPUs, inside a local
  Pumice checkout. Use when targeting a specific model, GPU, dtype,
  parallelism, and workload. Runs a staged multi-agent campaign (baseline,
  profiling, debate, isolated implementation tracks, integration, report)
  that takes hours; not for a one-off kernel question, a code review, or a
  single benchmark run, which a direct subagent handles.
  Registers campaign-scoped hooks from hooks/hooks.json on Claude Code and
  codex/hooks.json on Codex. Every hook but the two worktree hooks stays
  silent outside a campaign, as hooks/README.md records.
  Manual-only: use only when the user invokes /ammo:ammo.
metadata:
  compatibility: >-
    Requires the harness's plugin hooks, the ammo:* role agents, the pave-init
    plugin from the same marketplace, bash, jq, flock, git, and Python 3.11 or
    later. On Claude Code, hooks/hooks.json registers the PreToolUse,
    PostToolUse, PreCompact, SessionStart, Stop, SubagentStop, WorktreeCreate,
    and WorktreeRemove hooks, agents/*.md registers the eight role agents
    (ammo:ammo-researcher, ammo:ammo-champion, ammo:ammo-impl-champion,
    ammo:ammo-transcript-monitor, ammo:ammo-auditor, ammo:ammo-delegate,
    ammo:ammo-resolver, ammo:ammo-report-writer), and pave-init supplies
    pave-init:adversarial-investigator, plus the orchestrate skill and
    pave-init:orchestrate-worker for the Stage 2 family wave. On Codex, the hook modules that
    codex/RUNTIME.md lists run, codex/install_agents.py installs the ammo:*
    custom agents, pave-init's own codex/install_agents.py installs
    pave-init:adversarial-investigator, and codex/RUNTIME.md lists the settings
    that turn on multi-agent work. The campaign needs a pre-built Pumice checkout at the
    commit scripts/pumice_pins.py pins, with its venv (pumice and pumice_rust
    importable), cargo build --release done, model weights in a plain model
    directory, and NVIDIA GPUs with nvidia-smi. Preflight checks these and
    pauses the campaign, naming the gap. The runtime profile is pumice-cuda
    only. Graph validation (scripts/validate_pave.py) and the revision log
    tool (scripts/record_revision.py) need pyyaml and jsonschema and fail
    closed without them. The test battery needs uv, or pytest, pyyaml,
    jsonschema, pydantic, httpx, numpy, and psutil.
---

# AMMO - Automated Model Micro-Optimizer

AMMO is a staged, multi-agent accelerator optimization campaign. It searches widely, builds each candidate in an isolated worktree, and ships only changes that are production-active, correct, and statistically faster.

Resolve the plugin root as the absolute directory that holds `skills/`, `agents/`, `orchestration/`, `references/`, `schemas/`, and `scripts/` (from `<root>/skills/ammo/SKILL.md`, use `<root>`). Every `agents/...`, `orchestration/...`, `references/...`, `schemas/...`, and `scripts/...` path in this file and in the files it links resolves under that root, never under the engine checkout. Treat the plugin root as read-only, and do not test it for write access: campaign files go to the campaign workspace `<engine>/.ammo/<campaign-id>/`, and source edits go to the engine checkout or a track worktree. Run plugin scripts through the launcher, `<root>/scripts/ammo <script> [args]`: it finds the campaign and runs the script with the campaign's Python. Start every dispatch prompt with a `plugin_root: <absolute plugin root>` line, and give the absolute campaign dir on an `artifact_dir:` line. A child has no other way to find the plugin root. On Codex the same skill is `$ammo:ammo`.

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

Read the runtime profile from `target.json` `runtime.profile`. `new_target.py` writes it at scaffold, and the launcher and hooks export it as `AMMO_RUNTIME_PROFILE`. Do not ask the user to choose a backend. v1 ships and tests the CUDA path only: the profile is `pumice-cuda`, and scaffold and preflight refuse any other profile. On `pumice-cuda`, use CUDA graphs, Nsys, and NCU as this skill's profiling references define, and `scripts/run_pumice_grpc_bench.py` for timing. `references/accelerator-binding.md` owns the profile vocabulary.

AMMO's seven stages, agents, audits, five binding gates, GSM8K accuracy rule, end-to-end speedup criteria, workloads, statistics, verdict semantics, and stop logic do not depend on the runtime profile. Profiler-free timing is authoritative. `vllm bench serve` is diagnostic only for batch targets. Serving targets use the managed runner under `references/serving-campaign.md`. The serialized status stays `GPU_BLOCKED`.

## Serving Deployment Binding

When `target.json.serving` exists, read `references/serving-campaign.md` before dispatch. It owns the serving instrument, objective units, evidence paths, and domain-specific stop check. Keep every stage, agent, audit, implementation-isolation rule, and authored-mechanism requirement below. The batch sweep, serial Amdahl projection, diluted-pass exception, and batch-only short-circuit do not apply to this target. Scaffold with `new_target.py --serving-manifest` and synchronize evidence with `ammo_state.py serving-sync`.

## Lead Role

You are the lead orchestrator. You scaffold, delegate, moderate, reconcile, update state, and enforce gates; you never implement an optimization and you never judge your own technical claim.

- Keep every stage and role below; never skip or merge them for speed.
- Read the relevant state at dispatch, transitions, reconciliation, context recovery, and when related state changes. Update stage, round, audit, and per-track fields when they actually change.
- Freeze every parameter the user set: model, hardware, dtype, TP/DP/EP, ISL/OSL, batch, maximum length, and serving flags. Changes to frozen parameters or approved scope need explicit user approval. Repairs within an approved candidate, including its declared mechanism flags under `references/validation-defaults.md`, remain authorized.
- Spawn a fresh, one-shot `pave-init:adversarial-investigator` with no team-member name when evidence is ambiguous, a failure is unexplained, or a consequential decision has no established answer. Its prompt carries the `plugin_root:` line, the primary artifact paths, and one concrete decision question. It returns a verdict with file:line evidence.
- Talk to live team members through the harness's agent messaging; plain text in the lead thread does not reach them. To retire a team member, follow `references/shutdown-protocol.md`.
- Never kill a process you do not own. Never use a GPU you have not reserved. `references/gpu-pool.md` is the only authority on GPU ownership.

`references/harness-binding.md` maps every spawn, naming, messaging, waiting, release, and long-command idiom in this skill and its linked files to the tools of Claude Code and of Codex. Read it before the first dispatch. Every AMMO role spawns at its plugin address `ammo:<role>`, for example `ammo:ammo-auditor`. The investigator is the pave-init agent `pave-init:adversarial-investigator`.

## Invocation

The user starts the harness inside a Pumice checkout: the engine root. Before you scaffold, read `<engine>/.ammo/active`. If it names a campaign whose `state.json` `campaign.status` is not terminal, resume that campaign (§ Pause and Resume) instead of scaffolding a new one.

Collect every parameter the user gave. Scaffold once with `<root>/scripts/ammo new_target.py`, run from the engine root, and omit only the flags the user did not set. `references/workload-invocation.md` owns defaults, workload-matrix handling, `max_num_seqs`, DP/EP semantics, and the canonical command. The scaffold mints the campaign id (or takes `--campaign-id`), writes the campaign workspace `<engine>/.ammo/<campaign-id>/`, and points the marker `<engine>/.ammo/active` at that id. Hooks, scripts, and a later resume find the campaign through that marker. The campaign dir is the `artifact_dir` of every dispatch, and the campaign id is the value of every `session_id` field and `--session-id` flag.

After scaffolding, `target.json`, `state.json`, and `rounds/{N}/constraints.md` are the frozen target contract. `references/artifact-layout.md` owns artifact paths.

Before any GPU work, run preflight from the engine root: `<root>/scripts/ammo preflight_pumice.py --model-path <the target model directory>`. A non-zero exit means the pre-built engine has a gap. Record each gap under `{artifact_dir}/blockers/`, name it to the user, pause the campaign, and do not start Stage 1. The user owns the engine build, the venv, the weights, and the GPUs.

## Stage and Agent Contract

Every stage and role below is mandatory when its trigger fires.

| Stage | Required agents | Required outcome | Detailed authority |
|---|---|---|---|
| `1_baseline` | lead + fresh `ammo-researcher` | clean production-parity baseline/golden capture and a separate bounded attribution profile | `agents/ammo-researcher.md`, `references/nsys-profiling-guide.md` |
| `2_bottleneck_mining` | lead + `ammo-researcher` + one pave-init orchestrate worker per selected family, with reviewers | one reviewed dossier per selected family, and a grounded `bottleneck_analysis.md` with `## Technology Landscape`, workload dilution, component shares, and physical bounds | `orchestration/mining-orchestration.md`, `agents/ammo-researcher.md`, `references/mining-dossier.md`, `references/nsys-profiling-guide.md`, `references/e2e-delta-math.md` |
| `3_debate` | lead + 2-4 `ammo-champion` | independent candidates, adversarial debate, an eligibility record, and 2-3 winners | `orchestration/debate-protocol.md` |
| `4_5_parallel_tracks` | one `ammo-impl-champion` and one paired `ammo-transcript-monitor` per winner | isolated implementation, complete gates, a terminal verdict, durable evidence | `orchestration/parallel-tracks.md` |
| `6_integration` | lead; `ammo-resolver` plus independent merge review when required | the exact validated composition, an integration measurement, SHIP or round EXHAUSTED | `orchestration/integration-logic.md` |
| `7_campaign_eval` | lead | mechanical continue-or-stop from current opportunity | this file, Stage 7 |
| `7b_report` | fresh `ammo-report-writer` + fresh adversarial fact-checker | artifact-grounded `REPORT.md` accepted with `ok:true` | `agents/ammo-report-writer.md` |

`ammo-auditor` is mandatory after Stage 1 (`T_AUDIT_S1`), Stage 2 (`T_AUDIT_S2`), Stages 4-5 (`T_AUDIT_S45`), and Stage 6 (`T_AUDIT_S67`, which must pass before Stage 7 starts). Auditors may hand bounded evidence tasks to `ammo-delegate`. `pave-init:adversarial-investigator` is available at any uncertain fork or root-cause problem (§ Lead Role). The implementation role is registered as `ammo-impl-champion` and the report role as `ammo-report-writer`.

**Team-member vs subagent contract:** Only `ammo-champion`, `ammo-impl-champion`, and `ammo-transcript-monitor` are team members. A team member is a named, reusable background agent: give each one a unique name, and while its round is active, reuse it by sending a message to that name. Never spawn a second agent under a name that is still live. Put the campaign round and a bounded attempt suffix in any name that can recur. When a business identifier such as `OP-001` names a track, keep the raw `op_id` in state, prompts, monitor ledgers, and artifact paths. All other roles (`ammo-researcher`, `ammo-auditor`, `pave-init:adversarial-investigator`, `ammo-delegate`, `ammo-report-writer`, and `ammo-resolver`) are one-shot subagents: spawn each one fresh with its role and its prompt, so that its final report returns to its caller. The Stage 2 family wave adds named workers and reviewers of its own (§ Stage 2). `state.json.team_name` is descriptive bookkeeping, never a runtime handle.

### Stage 1: Baseline Capture

Dispatch a fresh `ammo-researcher` with `task_type: baseline` and `artifact_dir`. For a repair pass, put the attempt number and the audit findings in `context`. It runs exactly two logically separate measurements:

1. a profiler-free production E2E baseline plus golden references;
2. a bounded runtime attribution capture with Nsys.

Profiler-run timing is never the official E2E timing. When the researcher finishes, run `<root>/scripts/ammo ammo_state.py ingest-baseline --state {artifact_dir}/state.json --from {artifact_dir}/rounds/{N}/sweeps/baseline/e2e_latency_results.json`, then run `T_AUDIT_S1`. Fix every consequential finding before Stage 2.

### Stage 2: Bottleneck Mining

Stage 2 mines the evidence that already exists. `agents/ammo-researcher.md` § Stage 2 Evidence names it for each round. Do not add a fresh post-SHIP baseline sweep, profiling pass, or provenance sidecar.

1. Dispatch `ammo-researcher` with `task_type: mining_tables`. It stops after `tables.md`, and `mined.json` names the selected families.
2. Run the family wave with the pave-init orchestrate skill as `orchestration/mining-orchestration.md` describes: one reviewed dossier per selected family, plus the cross-cutting slice. The wave's workers and reviewers are that skill's named team members. A serving target opens no family run.
3. Dispatch `ammo-researcher` with `task_type: mining_report`. It writes `bottleneck_analysis.md` from `tables.md` and the dossiers.
4. Run `<root>/scripts/ammo ammo_state.py enrich --state {artifact_dir}/state.json --mining --from {artifact_dir}/rounds/{N}/mining/bottleneck_analysis.md`. A serving target runs `ammo_state.py serving-sync --kind mining` instead (`references/serving-campaign.md`).
5. Run `T_AUDIT_S2`. The dossiers are in its scope.

### Stage 3: Candidate Proposal and Adversarial Debate

Spawn 2-4 `ammo-champion` agents.

The lead runs the full protocol in `orchestration/debate-protocol.md`: proposals, eligibility, one round of critique and rebuttal, and winner selection by expected value. Debate is mandatory.

The protocol owns the evidence ladder and `references/e2e-delta-math.md` owns projection.

### Stages 4-5: Parallel Tracks

For each winner, give one `ammo-impl-champion` its own branch and worktree under the engine root, and pair it with one read-only `ammo-transcript-monitor`. `orchestration/parallel-tracks.md` owns how the worktree is made on each harness. Record each track's stable identity in state. The monitor reviews methodology continuously and runs its own collision checks; it does not implement and does not author the track verdict.

Keep all five binding gates (5.1a, 5.1b, 5.2, 5.3a, 5.3b) as defined in `references/validation-defaults.md`.

`references/validation-defaults.md` owns the exact comparator, tolerances, activation proof, production-parity rules, and verdict semantics. Before a track may report `FAIL`, the implementer must exhaust the applicable fixes and the fallback ladder in `references/validation-defaults.md` § Per-BS Verdicts and Track-Level Fallback Ladder. Route unexplained failures to `pave-init:adversarial-investigator` (§ Lead Role). `DILUTED_PASS` is discovered only after validation; it stays canonical status `PASS` with `diluted:true` and contributes measured E2E only.

Do not enter Stage 6 until every track is terminal: `PASS`, `GATED_PASS`, or `FAIL`. Reconcile state against the primary gate artifacts before that transition. Once all tracks are terminal, run the projection-accuracy diagnostics and `T_AUDIT_S45`.

### Stage 6: Integration Validation

Follow `orchestration/integration-logic.md`: § Four Branches decides what ships, and § Promotion and Finalization orders the SHIP steps.

On SHIP, compute cumulative gain as a direct ratio (original Round-1 baseline latency divided by current integrated latency) and never multiply round deltas.

### Stage 7: Campaign Evaluation

Stage 7 is autonomous and mechanical: stop iff the current production baseline's `bottleneck_mining.top_addressable_e2e_pct` (the largest measured `f_e2e × removable_fraction` across non-overlapping slices) is below `min_e2e_improvement_pct`; otherwise continue.

When the stop condition fires, the terminal token records how the round ended: use `campaign_complete` after a SHIP integration status (`single_pass`, `combined`, or `gated_pass`) and `campaign_exhausted` after an EXHAUSTED/failed integration. Before you set the terminal token (and before Stage 7b), enumerate the roster and drain every remaining team member per `references/shutdown-protocol.md`.

To continue, open the next round with `<root>/scripts/ammo ammo_state.py advance --state {artifact_dir}/state.json --outcome SHIP` or `--outcome EXHAUSTED`. It picks the next stage and writes the new round entry. Set `mining_invalidated` and `exhausted_technologies` on the current round first, because `advance` moves `current_round` and reads `mining_invalidated`.

- After SHIP, `advance` opens Stage 2 to mine the promoted Stage-6 evidence. Finish Stage 2 with `T_AUDIT_S2`. Then, if the newly mined addressable impact is below the floor, terminate as `campaign_complete`; otherwise enter `3_debate`. If every shipped track is `diluted:true`, `advance` enters `3_debate` on the valid mining instead, because the measured baseline did not materially move.
- After EXHAUSTED, append structured `exhausted_technologies` first. `advance` then enters `3_debate` on the mining that is still valid, and the debate needs a genuine mechanism pivot.
- If mining was proven wrong, set boolean `mining_invalidated:true` with a reason before you advance EXHAUSTED. `advance` then re-enters Stage 2.

`references/validation-defaults.md` owns the invalid-stop list and the minimum-improvement policy.

### Stage 7b: Report and Fact Check

At `campaign_complete` or `campaign_exhausted`, spawn a fresh `ammo-report-writer`. The writer does not check its own report. You run the fact-check gate:

1. When the writer returns, spawn a fresh `ammo-delegate` as the adversarial fact-checker. Its prompt is the `plugin_root:` line, the `artifact_dir:` line, and the fact-check assignment in `agents/ammo-report-writer.md`, unchanged. Give it nothing else: no expected answers, and no request to confirm the report.
2. Write its verdict to `{artifact_dir}/report_assets/report_fact_check.json` with `ok`, `findings` (its violations, empty when `ok` is true), and `report_sha256`, the SHA-256 of the exact `REPORT.md` bytes it reviewed.
3. On `ok: false`, spawn a fresh `ammo-report-writer` with the findings, then a fresh fact-checker on the new report.

Accept the report only when `report_fact_check.json` holds `ok: true` and its `report_sha256` matches the current `REPORT.md`. That file and the fact-checker exchange are the audit record; add no state field or other sidecar.

## Audit Gates

At `T_AUDIT_S1`, `T_AUDIT_S2`, `T_AUDIT_S45`, and `T_AUDIT_S67`, follow `orchestration/audit-protocol.md`: § Dispatch spawns the auditor, and § Repair and Re-audit handles its findings. `references/audit-invariants.md` owns the stage-specific questions, and they reach the auditor at the prescribed time.

An auditor writes only its verdict file, and a delegate writes only the path its caller assigns. Independent audit is the semantic gate; hook validation is only its mechanical backstop.

## State Management

`schemas/state.schema.json` owns the closed state shape and enums. `scripts/transitions.json` owns legal transitions. Do not restate or invent enum values in prose. Keep these stage values exactly:

`1_baseline` -> `2_bottleneck_mining` -> `3_debate` -> `4_5_parallel_tracks` -> `6_integration` -> `7_campaign_eval` -> `7b_report`.

At each real transition:

- set the current stage and its matching `started_at`/`completed_at` timestamps;
- write per-track gate, verdict, and metric fields when their primary artifacts land;
- write the typed `selected_candidates` contract and debate enrichment at selection;
- write mining enrichment from the structured `f_e2e` table after the Stage 2 report, before `T_AUDIT_S2` (§ Stage 2);
- keep provenance, attempts, audit records, exhaustion memory, shipped identities, the environment contract, and the direct cumulative speedup.

Use `scripts/ammo_state.py` for state operations and validation.

## Pause and Resume

Pause only when the user asks, when preflight finds a gap, or when the audit escalation path requires it. A pause keeps state and artifacts; it does not turn unfinished tracks into failures.

To resume, the user invokes the skill again in the same engine checkout. The `.ammo/active` marker names the campaign, and `state.json` says where it stands. On resume:

1. Read this file, `state.json`, and the active round's primary artifacts.
2. Confirm `campaign.status`, `campaign.current_round`, and `campaign.current_stage`.
3. Inspect GPU leases and clear only stale leases that this campaign id holds from a run that crashed.
4. Reconcile debate or track state with on-disk artifacts; never infer completion from narrative alone.
5. Reuse stable agent/task identities where the harness still holds them, and resume from the last verified gate.
6. If the campaign is terminal, re-enter Stage 7b when `REPORT.md` is absent or `report_assets/report_fact_check.json` lacks `ok: true` with a `report_sha256` that matches `REPORT.md`.

Repair malformed state through the schema/backfill path in `scripts/ammo_state.py`. Never rebuild it from guesses. Full artifact reconciliation lives in `orchestration/parallel-tracks.md`.

## Failure and Escalation

- Ordinary technical failure: investigate, fix it if the evidence says it is fixable, and continue the same stage.
- Unexplained correctness, activation, measurement, or integration failure: spawn the investigator as § Lead Role describes.
- Critical evidence or safety failure: halt the current stage, preserve the evidence, and resolve it before you advance.
- Escalate to the user only for missing authority or an unrecoverable conflict, never an ordinary engineering fork.

Record campaign-scoped blockers under `{artifact_dir}/blockers/`.

## Authority Index

Read only what the active task requires.

| Topic | Authority |
|---|---|
| Workload/configuration | `references/workload-invocation.md` |
| Artifact paths and authority | `references/artifact-layout.md` |
| Campaign environment names | `references/campaign-env.md` |
| Harness tools, spawn, and messaging | `references/harness-binding.md` |
| Accelerator ownership/isolation | `references/gpu-pool.md` |
| Profiling | `references/nsys-profiling-guide.md` |
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

Run each helper as `<root>/scripts/ammo <script> [args]`.

- `new_target.py`: scaffold target and state.
- `preflight_pumice.py`: check the pre-built engine before any GPU work.
- `run_pumice_grpc_bench.py`: the authoritative baseline, opt, profiling, correctness, and integration runner. Every v1 target sets `bench.runner: pumice_grpc_bench`. `references/artifact-layout.md` § Bench Runner gives the invocation.
- `check_projection_accuracy.py`: append `## Projection Accuracy` diagnostics.
- `gpu_reservation.py status --table` / `gpu_reservation.py force-clear --all --session-id <campaign_id>`: inspect leases, or clear the stale leases of this campaign after a crash.
- `ammo_state.py`: validate, update, transition, and backfill campaign state.

Inside a track worktree, run the engine's Python through the worktree `.venv/bin/python`. Never modify a helper just to get past a failed semantic gate.
