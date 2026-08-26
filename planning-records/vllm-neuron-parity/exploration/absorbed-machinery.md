# Absorbed-machinery exploration — vllm-neuron-parity Stage 2

## 1. Question investigated

What machinery, state contracts, hooks, graph structures, and hard lessons
from the two absorbed port engines — `skills/vllm-neuron-feature-port` (live)
and the model-port/autoport domain (`skills/neuron-framework-autoport*`,
`model_port_campaigns/`) — and their campaign histories must the new
`vllm-neuron-parity` plugin carry forward, and what demonstrably failed?

Note on method: this investigation ran directly (read-only), not through a
background fork — a fork dispatched for the autoport/model-port side returned
only a status update, not findings, and a mid-run network error (ENOTFOUND)
interrupted the session; both the feature-port half and the redone
autoport/model-port half below were completed directly against primary
artifacts, so every citation is first-hand.

## 2. Evidence inventory

Read in full or by targeted grep/python parse (all paths under
`/Users/jinhun/GitHub/NeuronAgenticDevelopment` unless noted):

- `skills/vllm-neuron-feature-port/SKILL.md`, `workflow.pave.yaml`,
  `schemas/run-state.schema.json`, `hooks/*.sh`, `hooks/irreversible-action-deny.json`,
  `agents/port-implementer.md`, `agents/port-reviewer.md`,
  `references/artifact-layout.md`, `references/delegation.md`,
  `references/contribution-checklist.md`, `tests/test_hooks.sh`.
- `.pave/vllm-neuron-feature-port/requirements.md`, `run-state.json`, and
  `prior-run-2026-08-12/{system-map.md,workflow.pave.yaml,skill-package-plan.md,traceability.md,reviews/*.md}`.
- `.pave/vllm-neuron-parity/requirements.md`, `reviews/requirements-brief.md` (the
  already-approved Stage 1 record for the new plugin — used to check which
  lessons below are already written into requirements vs. still open).
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/{run-state.json,contribution-report.md}`,
  `feature_port_campaigns/spec-decode-economics-2026-08-11/{run-state.json,FINDINGS.md}`.
- `skills/neuron-framework-autoport/`, `skills/neuron-framework-autoport-vllm-neuron/` (contents).
- `model_port_campaigns/_workflow_state/neuron-framework-model-port-vllm-neuron/{static-release.yaml,release/v1/workflow.pave.yaml,release/v1/workflows/*.pave.yaml}`.
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/` (campaign-state.json.lock, campaign-state-resume{1,2,3}.json, artifacts/{failures,recovery,events}/).
- `model_port_campaigns/_contaminated/glm-5-2-trn2-1-concurrency-32-nxdi-era-20260806T153500Z/` (campaign-state.json, invocation.json).
- `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/campaign-state.json` (11k+ lines; parsed with python, not fully read).
- `.codex-stage6-block-fp8-hardware-command.txt`, `.codex-stage6-block-fp8-hardware-gate.py` (repo root).
- `python3 -m json.load` runs to reproduce the corrupt-file and file-size claims directly, and one live run of `scripts/validate_state.py --strict --graph` against a real campaign file.

Not read: `model_port_campaigns/_contaminated/.../scripts/*.py` bodies, `deepseek-v4.../child-runs/` contents beyond a listing, and `campaign-state-resume3.json`'s `traversal_history` array contents (16.5 MB of it — only its aggregate byte size per top-level key was measured, per the brief's read-only/no-blowup intent).

## 3. Findings with citations

### 3.1 Feature-port graph inventory (live, frozen v0)

`skills/vllm-neuron-feature-port/workflow.pave.yaml`: **20 nodes**, **59 edges**
(counted by `- id:` lines under `edges:`), **5 terminals** plus **3 non-terminal
control endpoints**.

Node names (`workflow.pave.yaml:277-745`): `intake_feature`, `inventory_gaps`,
`preflight_environment`, `confirm_feature`, `analyze_feature_layer` (fan-out per
lens), `assess_feasibility`, `design_port`, `approve_design`,
`implement_increment`, `compile_smoke`, `debug_failure`, `serve_e2e`,
`validate_correctness`, `benchmark_benefit`, `adjudicate_benefit`,
`investigate_benefit_anomaly`, `decide_non_benefit_closure`,
`request_criterion_change`, `check_contribution`, `repair_contribution`.

Control endpoints (`workflow.pave.yaml:1024-1093`): `research_join` (join),
`paused_for_user` (pause), `resume_after_criterion_decision` (return);
terminals `accepted`, `closed_correct_no_benefit`, `blocked`, `exhausted`,
`closed_unaccepted`.

Evolution tier: **static** — one frozen file, no revision machinery
(`workflow.pave.yaml:1227-1241`, `SKILL.md:92-102`).

### 3.2 Feature-port hook enforcement inventory

Five hooks register from `SKILL.md`'s frontmatter (`SKILL.md:27-56`); a sixth
mechanism, H4, is a **consent-gated settings fragment**, never a silent
registration.

| id | event | matcher | rung | file |
|---|---|---|---|---|
| H1 `validate_state_on_write` | PostToolUse | `Write\|Edit` | observing | `hooks/validate_state_on_write.sh` |
| H2 `routing_contract_reinjection` | UserPromptSubmit/PreCompact/SessionStart | n/a | role reinjection | `hooks/routing_contract_reinjection.sh` |
| H3 `contamination_and_premature_edit_guard` | PostToolUse | `Edit\|Write\|Bash` | observing | `hooks/contamination_and_premature_edit_guard.sh` |
| H5 `stop_alignment_check` | Stop | n/a | socratic, blocks once | `hooks/stop_alignment_check.sh` |
| H6 `state_staleness_reminder` | PostToolUse | `Bash\|Write\|Edit` | socratic reinjection | `hooks/state_staleness_reminder.sh` |
| H4 `irreversible_action_deny` | PreToolUse (consent-gated) | `Bash` | **blocking** — the only blocking rung | `hooks/irreversible-action-deny.json` + `hooks/deny_main_mutation.sh` |

Exact H4 deny patterns (`hooks/irreversible-action-deny.json:5-35`):
```
Bash(git push) / Bash(git push:*)
Bash(gh pr create) / Bash(gh pr create:*)
Bash(git checkout main:*) / mainline:* / origin/main:* / origin/mainline:*
Bash(git switch main:*) / mainline:*
Bash(git merge main:*) / mainline:* / origin/main:* / origin/mainline:*
Bash(git reset main:*) / mainline:* / --hard main:* / --hard mainline:* / --hard origin/main:* / --hard origin/mainline:*
Bash(git rebase main:*) / mainline:*
Bash(rm /var/tmp/neuron-compile-cache:*) / -r ...:* / -rf ...:*
Bash(rm -rf /var/tmp/nxd_cache:*)
Bash(rm -rf ~/neuron-compile-cache:*) / ~/neuron_cache:*
Bash(rm -rf $NEURON_COMPILE_CACHE_URL:*)
```
Plus a **branch-aware PreToolUse script** (`hooks/deny_main_mutation.sh:93-183`)
that a static list cannot express: it denies `git push`/`gh pr create`
unconditionally; denies any git write-verb (`checkout|switch|reset|merge|rebase|branch -f/-d/-D/-m`)
when a `main`/`mainline` token appears anywhere in the command
(`deny_main_mutation.sh:93-117`); denies compile-cache-path deletes by regex
(`:99-125`); and for **bare** `git commit`/`git merge` (no explicit branch
token) runs `git branch --show-current` in the target checkout and denies
only if it returns `main`/`mainline` (`:127-181`). It explicitly documents the
**ssh-wrapped exception**: an explicit `main|mainline` token or cache-delete
inside an ssh payload is still denied (statically detectable), but a *bare*
commit/merge inside an ssh payload is not blocked — the guard will not guess
the remote branch, it only prints an advisory (`deny_main_mutation.sh:23-26,
132-138`). This ssh-wrapped-forms precedent is exactly what the new
requirements.md cites verbatim (`requirements.md:82`, "irreversible-action
prohibitions carry over as blocking hooks, including ssh-wrapped forms").

The authoritative *why-this-rung* record for all nine run-wide prohibitions
(P1–P9) lives in the abandoned redesign workspace, not in the live skill:
`.pave/vllm-neuron-feature-port/prior-run-2026-08-12/skill-package-plan.md:88-105`.
It tabulates each prohibition against a rung (mechanical / observing /
blocking / role-reinjection / role contract) with an explicit "why not
stronger" rationale, e.g. P3/P4 (main-mutation, cache-clear, PR push/open) earn
blocking because they meet "all four bars: likely, costly beyond the run,
irreversible before any later gate, precisely detectable"; P1 (NxDI) and P5
(premature edit) stay observing because a blocking match on ssh heredocs would
misfire and strand a hardware session; P7 (no invented outcomes/edges) is
tied explicitly to "the 25-undeclared-edge field failure was context decay."
This table is a **reusable enforcement-rung template** the new plugin should
re-derive from, not re-litigate from scratch — the new requirements.md already
promises to ("Stage 3 enforcement record must justify each rung",
`requirements.md:186-187`).

### 3.3 Feature-port state-contract shape

`schemas/run-state.schema.json` required top-level fields (verified by
`python3 -c "json.load(...)"`, matches `SKILL.md:184-236`):
`run_identity, neuron_host, target_feature, pinned_upstream_tag,
environment_record_ref, anatomy_lenses, design_revision, current_increment,
loop_rounds, benefit_record, criterion_changes, evidence_index,
traversal_history, restart_from, terminal_classification`. Write protocol:
whole-document rewrite to temp file, schema-validate, then replace; append/splice
forbidden (`SKILL.md:189-196`, `workflow.pave.yaml:1118-1123`). Sole writer:
the lead; H1 reports (never blocks) a non-lead writer identity
(`hooks/validate_state_on_write.sh:116-130`).

**This schema is stricter than what real campaigns ever produced.** Both real
run-state files predate it and use an older, looser shape:
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json` keys:
  `run_identity` (a bare string, not an object), `attempt_budgets`,
  `failure_fingerprints`, `completed_outcomes` — **none of `loop_rounds`,
  `benefit_record`, or `criterion_changes` exist in this file**, all three of
  which the current schema requires. Running the current validator against it
  (`python3 skills/vllm-neuron-feature-port/scripts/validate_state.py
  feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json --strict
  --graph skills/vllm-neuron-feature-port/workflow.pave.yaml`) fails closed with
  `FAIL (strict): jsonschema is NOT installed` in this environment — confirming
  the documented fail-closed behavior (`validate_state_on_write.sh:20`,
  `SKILL.md:136-138`) but also confirming the tooling dependency is fragile: a
  missing `jsonschema` package silently degraded past validation for the real
  campaigns that ran without it, which is exactly the failure `skill-package-plan.md`
  independently documents (§3.5 below).
- `attempt_budgets` in the p-eagle file already implements the current
  schema's "per-fingerprint bound" concept *before* the schema formalized it:
  `{"initial": 999, "remaining": 999, "per_fingerprint_retry_bound": 3, "note":
  "User directed unlimited attempt budget; 999 is the sentinel. Per-fingerprint
  bound kept finite so identical failures still escalate instead of looping."}`
  paired with one recorded `failure_fingerprints` entry naming the exact
  assertion line (`eagle3_model.py:479 assert num_hidden_layers==1`). This is a
  genuine field-tested precedent for the new requirements' "two-tier no-progress
  breaker... (1) failure-fingerprint no-identical-retry" (`requirements.md:141-142`).

### 3.4 Abandoned redesign workspace — reusable input (`.pave/vllm-neuron-feature-port/`)

Confirmed abandoned, not merely inferred: `run-state.json`'s
`terminal_classification` = `{"status": "abandoned", "reason": "Superseded
2026-08-25 by the vllm-neuron-parity run (user decision locked in
vllm-neuron-parity-goal-brief.md); materials retained as exploration input."}`
(`.pave/vllm-neuron-feature-port/run-state.json`). The live workspace holds
**two** redesign attempts: the archived `prior-run-2026-08-12/` (approved v0,
never shipped as the delivered `skills/` package) and a top-level second
attempt (`requirements.md`, `run-contract.md`, `workflow-manifest.yaml`,
`workflow.draft.pave.yaml`) that itself never finished before being superseded.

`prior-run-2026-08-12/system-map.md` is the single most information-dense
reusable artifact — it is a **lead-verified forensic diff** between the
approved graph and the shipped skill, and between the shipped skill and real
campaign traversal:

- Drift with no review trail: "Shipped graph `skills/.../workflow.pave.yaml`
  (721 lines) has DRIFTED from the approved workspace copy (708 lines)...
  Commit 92991ce ('P-EAGLE run hardening') added these post-hoc from campaign
  learnings without Stage 3-4 gates" (`system-map.md:24-31`).
- **The 25-undeclared-edge finding, with its exact count and location**:
  "Declared: 16 nodes, 39 edges, 6 control endpoints (validator PASS on both
  copies). Real p-eagle traversal: 55 entries, using 25 undeclared edge names
  (LEAD-VERIFIED), concentrated at `benchmark_benefit` and around the
  `accepted` terminal... `benchmark_benefit` (2 declared outcomes) absorbed a
  ~19-transition shadow subgraph: baseline correction, workload generalization
  (chat, GSM8K), external adversarial cross-check, 3-round root-cause
  fan-out." (`system-map.md:60-66`). Note the **graph size at the time of this
  finding (16 nodes/39 edges) is smaller than the current live graph
  (20/59)** — the live graph grew specifically to absorb some of that shadow
  subgraph, but the finding's mechanism (real campaigns invent transitions
  faster than the graph declares them) is the durable lesson, independent of
  node count.
- **The wrong-baseline incident, named and located** (`system-map.md:87-93`):
  "Benefit gating is the verified material hole: `benefit_shown` was first
  granted vs sequential-EAGLE3 (not no-spec/feature-off; caught by user
  challenge, not by `benefit_evidence_current`)... Final close:
  `terminal_classification.status = 'accepted'` with net-negative-vs-no-spec
  recorded only in free-text note/open_risks." This is directly corroborated
  in the primary campaign record itself:
  `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json:450`:
  *"USER FLAG: 'something is wrong — think about the high level goal and the
  results.' Lead concurs; entry 51's benefit_shown outcome used WRONG
  BASELINES. The claim 'benefit holds in the batched/offline regime' compared
  fixed parallel (92.7 tok/s) against the DEFECTIVE parallel (127.9) and
  SEQUENTIAL (40.0) — never against NO-SPEC at that config, which was never
  measured."* This is precisely the incident the new requirements.md calls
  the "wrong-baseline precedent" (`requirements.md:68,132,183`) and hardens
  against with "Kickoff-declared metrics/thresholds/methods change ONLY by
  explicit user decision recorded in run state" and "the agent that measured a
  number never adjudicates it."
- **Schema/prose contradiction, independently confirmed**: "Contradiction
  (LEAD-VERIFIED): SKILL.md + YAML declare 15 required state fields;
  `schemas/run-state.schema.json` and `scripts/validate_state.py` require only
  7... Real p-eagle run-state fails full jsonschema validation on ~15 grounds...
  The ordinary degraded path (no jsonschema installed) silently passed it."
  (`skill-package-plan.md`-adjacent finding at `system-map.md:96-99`,
  cross-checked directly above in §3.3).
- **The corrupt-JSON incident, independently reproduced**: "Economics
  run-state is NOT VALID JSON from line 90 (LEAD-VERIFIED): naive append
  splice after `traversal_history` closes" (`system-map.md:99-101`). I
  reproduced this directly: `python3 -c "json.load(open('feature_port_campaigns/spec-decode-economics-2026-08-11/run-state.json'))"`
  raises `json.decoder.JSONDecodeError: Expecting property name enclosed in
  double quotes: line 90 column 5 (char 23820)`. Reading the file confirms the
  mechanism: `traversal_history`'s closing `]` appears mid-file
  (`spec-decode-economics-2026-08-11/run-state.json`, line ~89), followed
  immediately by more `{ "node": ... }` entries with **no enclosing array or
  key** — a literal append-splice past the array close, exactly the
  "never append or splice" violation the write protocol warns about
  (`SKILL.md:193-194`: *"that is how a run-state file became invalid JSON in
  the field"*). A second, independent corruption sits in the same file: its
  `restart_from` field reads `"increment_implement"` — the reverse of the
  graph's real node name `implement_increment` — an invented/misspelled node
  name that `validate_state.py`'s graph check exists specifically to catch.
- **Bookkeeping decay**: "`completed_outcomes` (11) and `evidence_index` (6)
  froze at the design gate while `traversal_history` grew to 55;
  `design_revision` stayed 1 while revision 2 was approved" (`system-map.md:101-104`).

`skill-package-plan.md:88-105` (quoted in full structure in §3.2) is the
enforcement-rung authority the live `workflow.pave.yaml`'s
`enforcement.record` field points to (`workflow.pave.yaml:1243-1247`: "The
authoritative enforcement record... lives in skill-package-plan.md") — but the
live skill only ships hooks for a subset (H1/H2/H3/H5/H6 plus consent-gated
H4); P2 (NxDI-era readthedocs source) and P8 (role-reservation self-policing)
are documented as deliberately **not** hookable ("no tool-event signature
exists for 'who judged what'... a hook cannot detect these precisely and
would misfire", `skill-package-plan.md` P8 row) and rely on role contracts and
socratic checks instead. This is a legitimate design choice, not an omission,
but the new plugin's Stage 3 enforcement record should re-justify it rather
than assume it silently carries over.

`traceability.md` (`prior-run-2026-08-12/traceability.md:1-30`) is a complete
role/evidence-to-file mapping with zero composed nodes — useful as a template
for the new plugin's own traceability artifact, not itself reusable content
(it maps to file paths specific to the old skill).

Reviews (`prior-run-2026-08-12/reviews/final-skill-review-r1.md`): repeated
`CONFIRMED` verdicts on specific repairs (byte-identical graph, reviewer-half
independence at `agents/port-reviewer.md:348`, terminal-classification write
path at `scripts/validate_state.py:722-771`) — no unresolved REFUTED findings
were located in this file; the review record reads as a clean gate, and the
redesign's abandonment is attributable to the later, separate
`vllm-neuron-parity` scope decision (per `run-state.json`'s stated reason),
not to a caught defect in this reviewed bundle.

### 3.5 Autoport/model-port domain — graph and state-contract shape

`skills/neuron-framework-autoport/` and `skills/neuron-framework-autoport-vllm-neuron/`
ship **no `workflow.pave.yaml`** (confirmed: `find ... -iname "*.yaml"` returns
nothing under either skill directory) — these are prose-driven SKILL.md
packages, not PAVE-graph packages, in their `skills/` form. The actual PAVE
graph for this domain is a **released, digest-addressed bundle** living
outside `skills/`, at
`model_port_campaigns/_workflow_state/neuron-framework-model-port-vllm-neuron/`:

- `static-release.yaml` records `bundle_digest` (sha256), `package_identity.digest`,
  `release_authority: {actor_id: direct-user, approval_record: "2026-08-12 user
  request: run a new evidence-backed port campaign for
  deepseek-ai/DeepSeek-V4-Flash-0731 on trn2-1", authority_type: direct_user}`,
  and `resolved_profiles` naming six files: the root `workflow.pave.yaml` plus
  five **sub-workflows** — `workflows/authorize-and-lease.pave.yaml`,
  `diagnose-failure.pave.yaml`, `execute-work-item.pave.yaml`,
  `propose-port-plan.pave.yaml`, `verify-integrated-candidate.pave.yaml`.
- Root graph node count (`release/v1/workflow.pave.yaml`, 1458 lines): **18
  nodes** — `initialize_campaign, inventory_environment, resolve_campaign_input,
  freeze_contract, extract_incumbent_contract, propose_port_plan,
  criticize_port_plan, resolve_conflict, authorize_and_lease, execute_work_item,
  monitor_work_item, diagnose_failure, rollback_candidate, integrate_candidate,
  verify_integrated_candidate, audit_semantics, compute_completion,
  write_final_report** — **80 edges** (`grep -c "^\s*- id:"`), and terminals/control
  endpoints `resume_from_checkpoint, retry_from_failure_context,
  required_work_join, pause_for_authority_or_resource, accepted,
  closed_unaccepted, blocked, incomplete, exhausted` (9, one more terminal kind
  — `incomplete` — than feature-port's 5).
- Sub-workflow node counts: `authorize-and-lease.pave.yaml` 7 nodes
  (`validate_effect_request, evaluate_effect_authority,
  verify_resource_ownership, verify_resource_entitlement,
  assemble_authorization_decision, review_authorization_boundary,
  commit_authorization_return`); `diagnose-failure.pave.yaml` 4 nodes;
  `execute-work-item.pave.yaml` 4 nodes; `propose-port-plan.pave.yaml` 3 nodes;
  `verify-integrated-candidate.pave.yaml` 8 nodes. Total across all six files:
  **44 nodes**, more than double feature-port's 20, organized as **one root
  graph composing named sub-workflows** — a structurally different pattern
  from feature-port's single flat file.

This is direct precedent for two things the new `requirements.md` already
asks for: (a) a **released/digest-addressed workflow bundle** matching the
"evolving tier... ships revision workspace, freeze script" requirement
(`requirements.md:27-29`), and (b) **modular sub-workflow composition**
matching "shared intake and shared back-end" across the two campaign types
(`requirements.md:39-40`) — `authorize-and-lease.pave.yaml`'s
`verify_resource_ownership`/`verify_resource_entitlement` nodes are a working
precedent for the new plugin's "exclusive hardware-queue lease with
pre-action identity re-verification" (`requirements.md:75-76`).

`diagnose-failure.pave.yaml` (`workflows/diagnose-failure.pave.yaml:9,36,41,47,65,156`)
and the root graph (`workflow.pave.yaml:280,282,337,520,988,1172,1305,1414`)
both carry a fully-specified failure-fingerprint mechanism — "Fingerprint a
failure, classify its cause, account for attempt and resource budgets, and
select a learning-bearing recovery" (`workflow.pave.yaml:520`) — that
**predates and is more elaborate than** feature-port's `attempt_budgets`/
`failure_fingerprints` pair (§3.3). This confirms the fingerprint/breaker
concept is a genuine cross-engine precedent, not something feature-port
invented alone.

### 3.6 Autoport/model-port state contract: campaign-state.json, locks, leases, monitors

`model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/campaign-state.json`
top-level keys (35, via `python3 json.load` + `list(d.keys())`):
`acceptance_contract, active_node_runs, artifact_pointers,
attempt_and_resource_budgets, attempts, binding_mode, campaign_identity,
canonical_checkpoint, check_results, child_runs, completed_outcomes,
continuity, effect_authorizations, evidence_index, evidence_invalidations,
failure_fingerprints, gap_matrix, incumbent_capabilities,
integration_transactions, inventory_snapshot, model_identity_and_type,
open_questions_and_blockers, pending_work_items, profile_version,
projections, reconciliation_transactions, reopen_at,
repository_paths_and_revisions, required_work_items, resource_leases,
selected_plan, target_neuron_variant, terminal_classification,
traversal_history, unresolved_conflicts, user_authority,
verification_manifests, work_assignments, workflow_binding, workflow_run_id`.
This is far richer than feature-port's flat 15-field schema — it separately
tracks resource leases, effect authorizations (a capability/permission
ledger distinct from run routing), child runs (nested sub-workflow
invocations), evidence invalidations as a first-class list (not just a
staleness flag), and reconciliation/integration transactions.

`model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/`
confirms a real **file-locking mechanism**: `campaign-state.json.lock` exists
alongside `campaign-state.json`, plus three numbered resume snapshots
(`campaign-state-resume1.json` 376,707 bytes, `-resume2.json` 338,272 bytes,
`-resume3.json` **19,174,734 bytes** — the 19.2 MB file named in the brief,
confirmed via `ls -la`). Per-key size breakdown of `resume3.json`
(`python3` measuring `len(json.dumps(v))` per top-level key) shows the bloat
is concentrated almost entirely in one field: `traversal_history` alone is
**16,573,967 bytes** of the 19,174,734-byte file (86%); the next largest keys
are `evidence_index` (313,047 bytes) and `effect_authorizations` (104,703
bytes) — three orders of magnitude smaller. This confirms the bloat mechanism
is **unbounded, uncompacted growth of the traversal/history log** across
resume cycles, not a single large artifact accidentally embedded. This is the
exact failure the new requirements.md's "Schema-validated appends and
compaction for run state (UR, history-derived)" (`requirements.md:152`) is
written to prevent — "history-derived" is an accurate label for this finding.

The same campaign's `artifacts/{failures,recovery,events}/` directories hold
one JSON file per named failure/retry/decision event (e.g.
`stage3-resume1-attention-numeric-drift.json`,
`stage3-resume2-attempt3.json`, `stage3-user-authorized-resume.json`) — an
append-only event-log pattern distinct from feature-port's single
`evidence_index` list, and distinct from the monolithic `traversal_history`
array whose unbounded growth caused the 19.2 MB file above. This suggests the
bloat is not inherent to event-logging as a pattern, but specifically to
**inlining** the full log into one JSON document that gets rewritten wholesale
on every resume, rather than one-file-per-event plus an index.

### 3.7 The NxDI-era quarantine

`model_port_campaigns/_contaminated/glm-5-2-trn2-1-concurrency-32-nxdi-era-20260806T153500Z/`
is a full campaign directory (state, artifacts, scripts, events) moved under a
`_contaminated/` root, named `...-nxdi-era-...`. Its `campaign-state.json`
records, in every `environment_fingerprint` entry (e.g. lines 2941, 2957,
2973...): `"trn2-1-neuron-2.33.10-driver-2.29.0-compiler-2.26.6360-vllm-neuron-0.5.3-nxdi-0.10.18399"`
— the environment fingerprint itself names an installed
`neuronx_distributed_inference` version (`nxdi-0.10.18399`), i.e. the campaign
ran in an environment where NxDI was present and (per the directory name and
quarantine location) apparently used, predating the plugin's NxDI-free
architecture. No campaign-state field or artifact I found states the
quarantine reason in prose (no `"contaminat"`/`"quarantine"` string literal
inside the state file itself); the evidence for *why* it was quarantined is
structural — the fingerprinted NxDI package version plus the directory's
placement under `_contaminated/` and its `nxdi-era` name-tag, set by whoever
performed the quarantine outside the campaign's own event log. Treat the
*mechanism* of quarantine (move the whole directory tree under a `_contaminated/`
root, keep it, don't delete it) as the confirmed carry-forward artifact; treat
the *textual justification* as inferred from context, not directly quoted,
since no first-person note states it.

### 3.8 The deepseek-v4-flash-0731 pause at attempt 12

`model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/campaign-state.json`:
`terminal_classification` is `null` and `reopen_at` is `"authorize_and_lease"`
— the campaign is not closed, it is parked mid-graph. `open_questions_and_blockers`
records two live blockers from attempts 9 and 10:
```
{"blocker_id": "trn2-2-attempt9-backend-cap8-memory", "kind": "implementation",
 "reason": "Attempt 9 proved that eight concurrent large Neuron compiler jobs
 exhaust the 2 TiB host. Preserve the cache and apply the recovery decision
 before another runtime retry."}
{"blocker_id": "trn2-2-attempt10-neff-load-allocation", "kind": "implementation",
 "reason": "Attempt 10 proved the cap-2 compiler bound and finalized one
 additional NEFF, then Neuron returned status 4 Allocation Failure while
 loading a compiled model during prefill warmup. Preserve the cache and
 diagnose per-rank runtime allocation before another retry."}
```
Attempt 12 itself is recorded as an **authorization/lease event**, not a
hardware failure: `evidence_index` entries
`evidence-attempt12-runtime-authority-1`, `-lease-1`, `-review-1`,
`-precommit-recheck-1` (lines 662-665) and `effect_authorizations` entries
`authorization-attempt12-runtime-scoped-compile-and-test-1`,
`authorization-attempt12-runtime-lease-owned-runtime-execution-1`,
`authorization-attempt12-runtime-write-verification-artifacts-1` (lines
7721-7747), each carrying `"user_approval_id":
"user-request-20260814-continue-as-lead-attempt12-runtime"` — i.e. the user
explicitly re-authorized the lead to continue running attempt 12 as a
follow-on to the attempts-9/10 hardware exhaustion, and the run paused at the
`authorize_and_lease` sub-workflow waiting on that lease/authority chain
rather than terminating. This matches the requirements.md's "fate of the
paused deepseek-v4-flash-0731 campaign" open question (`requirements.md:205`)
— confirmed still genuinely open, not resolved by anything in this state
file; the new plugin's exploration/planning stages should treat this as a
real unresolved carry-over, not merely a historical curiosity.

### 3.9 Out-of-band hardware handoff (`.codex-stage6-*`)

Two files sit at the repo root, outside any run/campaign directory:
`.codex-stage6-block-fp8-hardware-command.txt` (a one-line ssh-activation +
env-var + `python gate.py` command targeting
`/home/ubuntu/glm-5-2-vllm-neuron-port/.../stage6/attempt4/gates/block-fp8-hardware/gate.py`
on the remote host) and `.codex-stage6-block-fp8-hardware-gate.py` (a
193-line standalone hardware-correctness gate script: hashes its own source,
scans `sys.modules` for NxDI before and after activation and raises if found,
runs a block-FP8 kernel on `torch.compile(..., backend="vllm_neuron")`,
checks `max_abs_error <= 0.25` and `mean_abs_error <= 0.03`, writes
`result.json`). This is a **complete, self-contained hardware gate written
to the repo root for out-of-band execution** — i.e., the lead prepared a
command and a script for something else (a different agent/session, "codex")
to run on hardware over ssh manually, rather than the workflow itself driving
that ssh session and gate in-band. This is the exact anti-pattern the new
requirements.md names and forbids: "Hardware gates run in-workflow over SSH —
no out-of-band handoff files (UR)" (`requirements.md:153`), and lists these
two files by name as the "OF" (observed fact) precedent
(`requirements.md:131-132`). Worth noting for the new plugin: this gate
script itself is well-built (source hash, NxDI-import scan, numeric
tolerances, structured JSON emission) — the *content* of a hardware gate like
this is reusable design, only the *out-of-band delivery mechanism* is the
demonstrated failure.

## 4. Contradictions and stale documentation

1. **Schema vs. real campaign shape** (§3.3): the current
   `schemas/run-state.schema.json` requires `loop_rounds`, `benefit_record`,
   `criterion_changes` — none of which exist in the only two real feature-port
   campaign state files on disk. Either the schema was tightened after both
   campaigns ran (most likely, given `system-map.md`'s "15 vs 7 required
   fields" finding at `system-map.md:96-99` shows this contradiction was
   already flagged once) or the schema was never validated against real
   history before being frozen. Either way: **do not assume the shipped
   schema was ever exercised against a real run.**
2. **`workflow.pave.yaml` enforcement-record pointer is stale by directory**:
   the live `workflow.pave.yaml:1243-1247` says the enforcement record "lives
   in skill-package-plan.md", but that file only exists in the abandoned
   `.pave/vllm-neuron-feature-port/prior-run-2026-08-12/` workspace, not
   anywhere under `skills/vllm-neuron-feature-port/`. A reader of the shipped
   skill alone cannot find the file the skill itself points to.
3. **Graph size at the time of the 25-undeclared-edge finding (16n/39e) is
   smaller than the currently shipped graph (20n/59e)** — `system-map.md`'s
   count and the live `workflow.pave.yaml`'s count are both correct, for
   different points in time; do not conflate them as the same measurement.
4. **`restart_from: "increment_implement"`** in the corrupt
   `spec-decode-economics-2026-08-11/run-state.json` is a reversed/invented
   node name (the real node is `implement_increment`) sitting inside a file
   that is *also* independently corrupt JSON from line 90 — i.e. this one
   campaign exhibits both failure modes (parse-breaking append-splice, and a
   node-name the graph never declared) simultaneously, which is stronger
   evidence for H1/H2's necessity than either failure alone would be.
5. **No first-person quarantine rationale** for the `_contaminated/` GLM-5.2
   campaign (§3.7) — the evidence is structural (fingerprint + naming +
   location), not a quoted decision record. Anyone citing "the contamination
   incident" should be precise that the *cause* is inferred, not read
   verbatim from an artifact.

## 5. Graph implications

(Observations only — synthesis and the actual new graph belong to the planner.)

- The autoport/model-port domain's root-graph-plus-named-sub-workflows
  pattern (§3.5) is a working precedent for exactly the "shared intake, shared
  back-end, two campaign types" shape the approved requirements.md already
  commits to (`requirements.md:39-40`) — it is not a novel design problem, it
  has been built and run once already (deepseek-v4-flash-0731, GLM-5.2) at
  this scale (44 nodes across 6 files, 18+80 edges on the root alone).
- Feature-port's single-flat-file static graph (20 nodes, 59 edges, no
  sub-workflows) is the *simpler* precedent and matches feature-port's
  narrower single-campaign-type scope; it is not the shape to imitate for a
  two-campaign-type, evolving-tier plugin — the autoport shape is the closer
  structural ancestor for that.
- Both engines independently converged on a fingerprint-based no-identical-retry
  mechanism (feature-port's `attempt_budgets.per_fingerprint_retry_bound`;
  autoport's dedicated `diagnose-failure.pave.yaml` sub-workflow) — this
  convergence is itself evidence the mechanism generalizes across both
  campaign types, supporting the new requirements' single "failure-fingerprint
  no-identical-retry" tier (`requirements.md:141`).
- The autoport `authorize-and-lease` sub-workflow's `verify_resource_ownership`
  / `verify_resource_entitlement` split is a concrete, already-built
  decomposition of "exclusive hardware-queue lease with pre-action identity
  re-verification" (`requirements.md:75-76`) — worth reading in full during
  Stage 3/4 rather than re-deriving from zero.
- The state-bloat root cause (§3.6: one field, `traversal_history`, growing
  unbounded and being rewritten whole on every resume) argues for whatever
  compaction mechanism the new plugin adopts to target *that specific field
  shape* — an ever-growing append-only log inlined into a document that gets
  rewritten in full — rather than a generic "big file" concern.

## 6. Remaining evidence gaps

- I did not open `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/child-runs/`
  contents (only listed the directory) — if Stage 3/4 needs the exact shape of
  nested sub-workflow runs, read those files directly.
- I did not read the `_contaminated/glm-5-2.../scripts/*.py` bodies (the
  `render_*_checkpoint.py` files) — these might encode the checkpoint/rendering
  convention behind the rich campaign-state shape; unopened, so their content
  is unverified.
- No artifact anywhere states the NxDI quarantine's decision rationale in
  first person (§3.7, §4.5) — if that matters for Stage 3's contamination-gate
  design, it may need to come from the user directly rather than from repo
  evidence.
- I did not locate a **separate** wrong-baseline incident inside the
  autoport/model-port domain specifically — the one confirmed incident
  (§3.4) is in the feature-port lineage (P-EAGLE). I did not find contrary
  evidence that autoport/model-port has its own independent instance, but
  I also did not exhaustively grep every `model_port_campaigns/*/artifacts/`
  file for baseline-mismatch language beyond the deepseek and GLM-5.2
  campaigns already covered above — treat "not found elsewhere" as
  incomplete-negative, not confirmed-absent.
- The two-file lock mechanism (`campaign-state.json.lock`) was confirmed to
  exist but its *locking protocol* (advisory? PID-based? what happens on
  stale-lock recovery?) was not read from its contents — only its presence
  was checked.
