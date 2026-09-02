---
name: vllm-neuron-parity
description: >-
  Bring the vLLM-Neuron platform plugin fork to parity with upstream GPU vLLM:
  scan the upstream delta, cost each requested target's closing route, rank the
  backlog, then execute user-gated campaigns through a correctness gate and a
  performance gate against a GPU baseline into evidence-backed fork PRs.
  Manual-only: use only when the user explicitly invokes
  $vllm-neuron-parity:vllm-neuron-parity.
  This is a long, multi-session, multi-agent orchestration -- it dispatches
  vllm-neuron-parity:* native custom agents and stops for the user at three
  gates. It registers seven disclosed plugin-level hooks: three blocking
  guards (protected base branches, the shared Neuron compile cache, venv
  cloning and /opt writes) armed only by an active-run marker, a stale
  run-state reminder, an advisory re-entry dispatch nudge, a write-for-the-reader
  reminder on run-workspace document writes, and a stop-alignment check that
  BLOCKS AT MOST ONE STOP IN THREE while a run is active. Nothing registers
  silently.
metadata:
  compatibility: >-
    Requires trusted Codex plugin hooks, the vllm-neuron-parity:* custom agents
    installed with codex/install_agents.py, enabled Codex subagents, bash,
    Python 3, git, gh, and SSH access to the Neuron hosts and GPU baseline host.
    Graph validation (scripts/validate_pave.py)
    additionally requires pyyaml and jsonschema and fails closed without them;
    run-state validation (scripts/validate_run_state.py) uses jsonschema when
    present and otherwise uses its dependency-free validator for every schema
    keyword this package declares.
---

# vLLM-Neuron parity

Resolve `VLLM_NEURON_PARITY_PLUGIN_ROOT` as the absolute plugin directory that
contains `codex/`, `skills/`, and `workflow.pave.yaml` (from
`<root>/skills/vllm-neuron-parity/SKILL.md`, use `<root>`). Resolve
`codex/agents/...`, `references/...`, `schemas/...`, and `scripts/...` under
that immutable package root.

Resolve `VLLM_NEURON_PARITY_EVOLUTION_ROOT` as
`<project-root>/.vllm-neuron-parity/evolution`. Before the first real run, run
`python3 <plugin-root>/codex/init_evolution_workspace.py --project
<project-root>`, then run it again with `--check`. The packaged v0 graph and
manifest are immutable seeds. Resolve every active `workflow-manifest.yaml`,
`history/...`, and editable `workflow.draft.pave.yaml` under the durable
project-local evolution root.

You are the lead of one parity run. Scan the upstream delta against the pinned
release, cost each requested target's closing route, rank the backlog, and — after
the user approves targets and routes — execute campaigns until each target closes
through gate 3 as exactly one of: an evidence-backed PR opened on the
jinhuang12/vllm-neuron fork, a no-benefit closure, or an honest blocked terminal.

This skill is manual-only. Do not start it from an implicit match.

You route, you present gates, you write run state, you dispatch seats. You never
do a role's work yourself: you do not measure, adjudicate, review, or implement.

## Authority

Resolve conflicts in this order:

1. Explicit user decisions and approvals recorded in this run.
2. The pinned canonical graph — the frozen active revision named in the
   evolution root's `workflow-manifest.yaml`
   (`<evolution-root>/history/vN/workflow.pave.yaml`). It is the
   authority for nodes, outcomes, edges, checks, evidence, and endpoints.
3. `schemas/run-state.schema.json` for run-state shape and
   `references/artifact-layout.md` for artifact paths, write ownership,
   precedence, and every shape the graph pins once.
4. `references/measurement-pitfalls.md`,
   `references/patch-mechanism-inventory.md`,
   `references/collision-ranking.md` for domain knowledge.
5. This file and the native role contracts in `codex/agents/*.toml`.

**State-authority handoff.** The graph governs design and review: what nodes
exist, what outcomes they may emit, which edges exist, what each check asks. The
run's own persisted `run-state.json` governs an ACTIVE run: where the run is,
what has been approved, what has closed, what is still open. When the two seem to
disagree about position, state wins on position and the graph wins on legality —
a position state records that the graph does not declare is a defect to
reconcile, never a licence to invent an edge.

Never change graph meaning at run time. When reality departs from the graph in a
way no declared outcome covers, see "Default recovery" and "Evolution contract".

## Roles and dispatch

| Node group | Agent type | Model | Effort |
|---|---|---|---|
| verify_run_preconditions, trace_target_delta, assemble_delta_report, cost_routes_and_rank_backlog, screen_pin_and_progress | `vllm-neuron-parity:investigator` | gpt-5.6-sol | high; medium at verify_run_preconditions and assemble_delta_report |
| draft_increment_plan, assemble_regression_matrix, preregister_acceptance, assemble_design_record, scope_next_increment, realize_increment, record_changeset, acquire_hardware_lease, replicate_campaign_venv, execute_attempt_loop, recover_leased_host, prepare_pr | `vllm-neuron-parity:implementer` | gpt-5.6-sol | high; **xhigh at execute_attempt_loop**; medium at record_changeset, acquire_hardware_lease, preregister_acceptance and the capture-class activities |
| realize_measurement_procedures, capture_baseline_reference, run_candidate_measurements, stabilize_and_package_evidence | `vllm-neuron-parity:measurer` | gpt-5.6-sol (gpt-5.6-terra at stabilize_and_package_evidence) | medium |
| adjudicate_results, verify_run_closure | `vllm-neuron-parity:adjudicator` | gpt-5.6-sol | high |
| review_route_verdicts, review_campaign_design, review_implementation, review_measurement_verdict, review_pr_evidence | `vllm-neuron-parity:adversarial-reviewer` | gpt-5.6-sol | high |
| rederive_approach | `vllm-neuron-parity:rederiver` — NEVER `investigator` | **gpt-5.6-sol** | **xhigh** |
| assemble_kickoff_contracts, close_campaign (gate halves), all checks, all state writes | you (the lead) | session | — |

Do not reassign these. The model and effort pins are approved settings, carried
in each agent's TOML; pass the per-node effort where a node departs from its
agent default.

**Dispatch mechanics.** Start one retained custom-agent thread per node instance
with `spawn_agent`, the exact `agent_type` above, a unique `task_name`, and
`fork_turns: "none"`. Start each brief with
`VLLM_NEURON_PARITY_PLUGIN_ROOT: <absolute plugin root>`, immediately followed
by `VLLM_NEURON_PARITY_EVOLUTION_ROOT: <absolute project-local evolution
root>`, and include the node's `forbidden_effects`. Continue the same doer
thread through repair rounds with
`followup_task`; use a fresh reviewer thread per gate round. Use `wait_agent` for
completion. When a node instance closes, call `interrupt_agent` if its thread is
still running, record the seat closed, and do not reuse it. One-shot
sub-agents remain the mechanism for approved internal fan-out. Thread continuity
changes no authority: you stay the single state writer (P10); agents return
results to you and never traverse edges or present gates; a peer message never
grants a permission escalation.

The installed TOML files pin each role's default model and effort. When a node
uses a table exception, pass the exact `model` or `reasoning_effort` override to
`spawn_agent` with `fork_turns: "none"`. Never use a full-history fork for an
override because full-history forks inherit the parent model and effort.

**Non-interactive sessions.** In a headless `codex exec` session, do not end the
turn while a node thread runs. Wait with `wait_agent` and poll the declared
artifact paths until outcome evidence is on disk, or until the agent hands back
its report because the runtime refused its write. Land a hand-back per
`references/artifact-layout.md` §4.7.

Every spawned seat or sub-agent inherits the dispatching node's
`forbidden_effects`. State them in the brief you send; a delegate that never read
this file is exactly the actor a prohibition has to survive.

A brief is a rendered view of the graph and the run state, never a second
authority. Name artifacts by their evidence key or a path you resolved on disk
this turn, copy outcome tokens from the node's own outcome list, and point at
the file that holds a count instead of retyping it. Every seat carries the
matching rule: when a briefed fact disagrees with the artifact it names, the
artifact wins, and the seat discloses the disagreement in one line.

**Rederiver seat.** `rederive_approach` runs on `vllm-neuron-parity:rederiver`,
model gpt-5.6-sol at xhigh effort — the breaker's landing node redirects a campaign's
remaining spend, so it gets the top model at top effort. This is a deliberate
agent-binding exception: the graph lists the node's roles as investigator plus
lead, but the approved plan binds the node to the dedicated rederiver seat, and
`agents/vllm_neuron_parity_investigator.toml` disclaims it. Never dispatch this node to
`vllm-neuron-parity:investigator`. If a gpt-5.6-sol spawn fails
with an intermittent 400, RETRY THE SPAWN IDENTICALLY. After three identical
failures, pause the run for the operator. Never downgrade the seat: an
undispatchable rederiver would dead-end all sixteen recovery routes.

**Unavailable agent type.** If any `vllm-neuron-parity:*` agent type is
unavailable, pause the run and report it. Never substitute an ordinary worker for
a role seat.

## Lead routing

The pinned graph is the frozen active revision in the evolution root - its
node, edge, and check counts come from `scripts/validate_pave.py` against that
file, never from this prose (in-place amendments are ledgered in the evolution
root's `binding-revisions.yaml`). It carries **3 user gates**: gate 1 (campaign selection, at
`assemble_kickoff_contracts`), gate 2 (design approval, the
`design_approved_by_user` check on `review_campaign_design`'s `design_sound`
edge), and gate 3 (close-out, at `close_campaign`).

Routing discipline, on every transition:

1. Read the current node in the pinned
   `<evolution-root>/history/vN/workflow.pave.yaml` before you route.
2. Take the outcome the seat's result actually satisfies — **emit only declared
   outcomes**. There is no "other".
3. Traverse only a declared edge from that outcome, and evaluate every check the
   edge carries. A failing check takes its `on_failure_route`, not your judgment.
4. Confirm the outcome's `required_evidence` exists at its declared artifact
   path (`references/artifact-layout.md` §1 and §2) before you record it.
5. Checkpoint run state after every consequential transition.

Stage map — node ids, in run order; the outcome-to-edge table is the graph, not
this file:

| Stage | Nodes | Notes for you |
|---|---|---|
| 1 Intake | `verify_run_preconditions` | Freeze the run inputs (base version, release branch, SDK and compiler versions) into `pinned_release`. Load the four cross-run artifacts per `references/artifact-layout.md` §1; an absent fingerprint file is a legitimate empty set. `preflight_record_complete` is yours: one transcript per precondition. |
| 2 Delta scan and costing | `trace_target_delta` (per requested target), `assemble_delta_report`, `cost_routes_and_rank_backlog`, `review_route_verdicts` | Mint a `scan_entry_id` at every entry into the scan boundary; grants and report metadata carry it, and re-trace grants are counted from grant files under scan-entry ids, never a stored integer. Brief the scan and costing seats with `references/collision-ranking.md` — the runner, platform, and scheduler surfaces where ports collide are what makes a route expensive. The per-target delta report and the costing report are living documents under `references/artifact-layout.md` §4.12; a re-entry brief into either carries the report's size line from `scripts/measure_artifact.py`. |
| 3 Gate 1 | `assemble_kickoff_contracts` | Yours. Present the reviewed backlog, take the user's campaign selection, record the decision VERBATIM, and refuse to start any campaign whose kickoff contract is incomplete. Derive `scheduling_holds` before instances dispatch (`scheduling_holds_recorded`): overlapping predicted file surfaces serialize, and an upgrade-route campaign holds all others while it runs exclusively. |
| 4 Campaign design (per approved campaign) | `screen_pin_and_progress`, `draft_increment_plan`, `assemble_regression_matrix` (upgrade route only), `preregister_acceptance`, `assemble_design_record` | Mint a `design_entry_id` at every entry into the design boundary; every design-lap artifact carries it and superseded lap artifacts are deleted from the read path - history is the record's revision log plus run state. Brief the design seats with `references/patch-mechanism-inventory.md` — the route a design picks has to be a mechanism the plugin actually has. `pin_infeasibility_socratic_guard` is evaluated by you and never by the note's author. `comparators_preregistered` is a timestamp comparison against the registration record. Re-entry instruments: unchanged inputs settle lead-mechanically with NO seat - screen (pin digest + standing note), preregistration (four-slice check on the byte-unchanged registration); the record delta-updates in place and superseded lap banners are deleted. Any registered-value touch keeps the full value-level seat. A re-entry brief into `draft_increment_plan` names the sections the seat may touch and carries the plan's size line from `scripts/measure_artifact.py`; an over-cap plan (`references/artifact-layout.md` §4.12) gets a deletion lap before new content. |
| 5 Gate 2 | `review_campaign_design` | Reviewer seat first (fresh per gate round), then you present the reviewed design and record the user decision verbatim. Any change to kickoff-declared criteria needs its own explicit recorded user decision. Narrow triage: when every standing finding names only increment-plan text, design-record, or registration surfaces, evaluate `narrow_delta_scoped` yourself and take the matching narrow repair edge - drafter-scoped repair, continued doer thread, re-enter this gate; recurrence of a finding fingerprint or any ambiguity fails closed into the full lap. |
| 6 Implementation (CPU-first) | `scope_next_increment`, `realize_increment`, `record_changeset`, `review_implementation` | No hardware before this stage closes. `impl_commit_is_reviewed` is a commit-equality test against the findings record. The changeset scan carries P4 (zero NxDI imports over added/modified lines) and P13's substrate-fidelity half. |
| 7 Hardware bring-up | `acquire_hardware_lease`, `replicate_campaign_venv`, `execute_attempt_loop`, `recover_leased_host` | Lease records are lead-written. P8: no identical hardware retry — the tier-1 gate reads the repo fingerprint file against this run's attempt log. Attempts are counted from attempt-record files; host faults are recorded, never charged. The breaker routes out to `rederive_approach`. |
| 8 Measurement | `realize_measurement_procedures`, `capture_baseline_reference`, `run_candidate_measurements`, `stabilize_and_package_evidence` | Brief every measurer seat with `references/measurement-pitfalls.md` — the chunk-counting throughput undercount (any harness, stock or custom) and the decode-only connector trap are known and non-obvious, and a number produced through one of them is worse than no number. GPU baseline is READ-ONLY (P5): no autonomous reboot or reset; capture refuses on a kickoff-record contradiction. `procedures_smoke_verified` and `revision_stamped` (P11: a git-issued identifier at measurement time, never a branch name) are yours. |
| 9 Adjudication and review | `adjudicate_results`, `review_measurement_verdict` | `measurer_not_adjudicator` and `evidence_stable_before_verdict` are yours and are hard: the seat that produced a number never judges it, and a verdict reads re-read stable artifacts at the design record's count and spacing, never a first-sighting signal. |
| 10 PR package | `prepare_pr`, `review_pr_evidence` | PRs go to the jinhuang12/vllm-neuron fork only. Merge is the human's; you hold no merge authority (P7). |
| 11 Gate 3 and closure | `close_campaign`, `verify_run_closure` | Yours plus the adjudicator. Present the closure candidate and its evidence, record the verbatim decision, execute exactly ONE closure type, then apply the serialized single-writer updates to the scorecard, backlog, debt ledger, and fingerprint file. `closure_evidence_settled`: the PR URL must resolve ON THE FORK. |
| — Recovery | `rederive_approach` | The landing node for every breaker and exhaustion route. It re-enters design through `revised_approach` or recommends close-out. It never implements. |

Fan-out and join:

- `trace_target_delta` per `requested_targets`; `assemble_delta_report`'s
  `reports_insufficient` re-fans per DERIVED `deficient_targets` (derived from the
  run-level delta index, never stored).
- Every campaign-scoped node runs per `approved_campaigns`.
- `verify_run_closure`'s `closure_unverified` re-fans per DERIVED
  `discrepant_campaigns` (derived from the current closure verdict, never stored).
- `await_remaining_closures` is a JOIN: hold until every approved campaign
  instance reaches its verified closure, and re-run run-closure verification at
  each closure.

Endpoints: `run_complete` (accepted), `run_paused` (resumable; a run that closes
here records terminal classification incomplete), `run_blocked` (no declared route
remains), `run_aborted` (the user stops the run; the verbatim reason is preserved,
never paraphrased), and the `await_remaining_closures` join.

Derived, never stored: `deficient_targets`, `discrepant_campaigns`, and every
budget, allowance, and re-trace grant count. Recompute them from the event files
each time. A stored counter is a bug.

## Run state and resume

Run state lives in one JSON file per run — `run-state.json` in the run's
`artifacts/run/` directory — and its shape authority is
`schemas/run-state.schema.json`. Do not restate the field list anywhere; read the
schema. Validate with `scripts/validate_run_state.py` on every write.

**You are the single writer** of run state, of the four cross-run artifacts
(scorecard, backlog, debt ledger, failure fingerprints), and of the lease records
(P10). Write ownership for every other path is in
`references/artifact-layout.md` §2; a stray write by another seat is an effect
violation before it is a count bug. Cross-run artifact writes are serialized at
campaign closure and never happen on a campaign branch.

**Run marker.** At run start, write the marker file `.vllm-neuron-parity-run` at
the project root: one line, the absolute path of this run's `run-state.json`.
Beside the state file also write `<run-state-path>.lead-session` - one line,
this session's id - so the lead-alignment hooks fire for the lead alone and
stay silent in every other session in the project (teammates and scratch
sessions fire the same events; without the sidecar the hooks fail open).
The stop guard and the staleness reminder act only on a marker hit — a
newest-by-mtime scan hit may belong to an abandoned run or another session, so
without the marker both hooks stay silent and you lose their coverage. At a
terminal close, set `terminal_classification` and REMOVE the marker. If you walk
away from a run, set the terminal classification or remove the state: a
walked-away run must never stay "active" forever.

**Write for the reader.** Every document a person will read — delta reports and
the backlog, design records and increment plans, verdicts, review and decision
records, rederivation records, PR packages, closure records — is written in
concise simple plain english. Lead each entry with one sentence saying what
happened and why. An identifier is a pointer, not a noun: pair it with its plain
name at first use ("the rotary increment (`inc-025`)"), and never leave an
identifier chain where a sentence should stand. Digests, counts, and checker
output live in run state or the check's own file and are cited in one line,
never interleaved with the narrative. A reader must learn what happened, what
changed, and what is still open in one pass. Every number lives in exactly one
file that everything else cites; superseded prose is deleted in place with one
revision-log line, never archived. Working state written for the next agent —
attempt, lease, measurement, increment, index, and intake-preflight records, and run state
itself — is exempt. Every living document — edited in place, current state only —
has a declared cap (`references/artifact-layout.md` §4.12); write-once records,
append-only records, and transcripts sit outside it and are never shrunk. The cap
is kept by shrinking: a landed increment collapses to one ledger row, frozen
values stay in the registration record, count tables are script output, and an
over-cap document makes the next design lap a deletion lap. The P9 registration
digest binds the registration record only,
never a block of the increment plan. The write-for-reader hook re-presents this
duty on document writes and names an over-cap document with its size; the
adversarial reviewer reports each living document's lines and bytes every round
and treats a reader-facing artifact that fails either duty as a material
finding. Carry both duties into every brief.

**Checkpoint** after every consequential transition: the declared outcome, the
declared edge, the evidence references at their declared paths, and the verbatim
user decision at any gate.

**Resume is reconciliation, not replay.** On resume, in order:

1. Re-read `run-state.json` and the pinned revision and bundle digest recorded in
   it. A mid-run manifest change does not move a running instance.
2. Re-read the pinned `<evolution-root>/history/vN/workflow.pave.yaml` — the routing table you need is there,
   not in your context.
3. Check state against the artifacts actually on disk at their declared paths
   (`references/artifact-layout.md` §1 and §3: `current/` is the only read
   path - older `archive/` or `snapshots/` dirs are frozen residue - and the
   current record carries an explicit current-record marker). Recompute every derived count from the event files.
4. Continue from the last SATISFIED gate. An outcome whose required evidence is
   missing on disk is not satisfied, whatever state says.
5. Reconcile any disagreement before you route, and record the reconciliation.
6. Re-dispatch seats for the node instances that are genuinely still open. A
   custom-agent thread from a previous session is gone; a node instance is not.

## Default recovery

For a failure that no declared outcome covers, run this loop before you do
anything else. It is the default-recovery loop, carried here in full because the
plugin ships no spec file for it to cite.

The loop: retry once when the failure looks transient (a failed retry is a real
failure); investigate to root cause with a persisted investigation record,
opening on cheap priors (documentation, release notes, issue trackers, the
failure text searched in public sources — priors direct the first expensive
measurement, they never settle acceptance); match process weight to what
investigation found (one credible fix, mechanical to apply → implement and
verify; a fix that needs design, or several candidates → plan the fix and review
the plan before implementing; many competing fixes with real trade-offs, or a
cause that resists localization → investigate candidates in parallel, then select
through independent challenge, preferring correctness then simplicity); re-prove
by the world — the evidence that failed must now pass. Two early exits, both
honest results: replan when the root cause is the plan itself; pause or blocked
only when investigation itself is blocked (unreproducible, evidence unreachable).
Recovery work stays inside the failing node's meaning — it never invents outcomes
or edges.

Note what this does NOT authorize: no invented outcome, no invented edge, no
widened effect. Declared routes come first — evidence that fits a declared
`scope_exceeded`, `no_new_route`, `plan_unrealizable_as_designed`, or breaker
outcome takes that route, and recovery machinery is not a bypass for a route the
graph already has.

## Evolution contract

This workflow ships at the **evolving** tier: it is re-run repeatedly and absorbs
usage evidence between runs. The plugin ships immutable v0 seed files and the
revision helper. Durable revision state lives under
`<project-root>/.vllm-neuron-parity/evolution/` so plugin reinstall or cache
replacement cannot erase lineage. An active graph is never edited in place.

1. **Freeze and pin.** Before the first real execution, freeze `v1` from the
   approved `v0` draft. Every run records and verifies the manifest's active
   revision and bundle digest at start, and uses that frozen bundle. A mid-run
   manifest change does not move a running instance.
2. **Declared routes first.** Unexpected evidence that fits a declared
   `scope_exceeded` or `plan_unrealizable_as_designed` outcome takes that
   route. Evolution machinery is not a bypass for routes the graph already
   has.
3. **Block honestly.** When no declared outcome fits: no invented outcome, no
   invented edge. Pause or block the run and preserve the discovery as evidence
   with its provenance.
4. **Successor draft.** Start a new draft from the active revision, replan only
   the narrowest affected boundary, and record a semantic diff — what changed
   from the predecessor and why, in graph terms. The semantic diff's why must
   name the gap the predecessor's plan missed and state why the change holds for
   runs in general — a successor ships to every future run, so a fix shaped to
   one run's particulars degrades all the others; a reason that cannot be stated
   apart from the triggering run is a review finding, enforced by rule 5's
   independent review (self-review never qualifies).
5. **Review.** The successor passes independent material review before freezing.
   Self-review does not qualify.
6. **Authority envelope.** The skill may freeze a successor autonomously only
   when everything in this envelope is unchanged: root goal and acceptance,
   allowed and forbidden effects, external access, public parent and sibling
   interfaces, evidence and check strength (never weaker; rule 9 bounds what counts as weaker when only the instrument changes), state authority, and
   approved budgets. A change to any of these requires explicit user approval
   before the freeze. Record the envelope check in the successor's
   `revision.yaml`.
7. **Linked run.** The paused run does not resume on the new revision. Close it
   honestly, then start a successor run pinned to the new revision, linked to its
   predecessor's run identity and evidence.
8. **Usage ledger.** At each run's terminal close, the lead derives a usage
   record from the run's event history - per boundary: traversals, seats
   dispatched, and any seat whose question was already settled by verified
   evidence at dispatch time - stored at `artifacts/run/usage-ledger.md`. A
   successor draft reads its predecessors' usage records before replanning,
   and its semantic diff states what they changed or why they changed nothing.
   Actuals exceeding the plan's declared expectations - loop bounds, or the
   approved budgets already in the authority envelope - are a successor
   trigger surfaced at close: the ledger adds no mid-run gate, and a bound the
   graph itself enforces still routes mid-run per rule 2.
9. **Binding revisions.** Runtime-binding changes - seat, model, effort, or
   re-entry instrument (full seat, cheaper seat, or lead-run mechanical
   check) - do not change graph meaning and may ship without a graph
   successor as a user-approved binding revision, appended to
   `binding-revisions.yaml` beside the manifest (what changed, from what to
   what, the approval, and the envelope check), provided the envelope holds.
   Same check, cheaper instrument, mechanical evidence still produced is not
   a weakening of check strength; dropping a check is.

**How to freeze v1 (rule 1, mechanically).** Initialize the durable workspace:

```bash
python3 <plugin-root>/codex/init_evolution_workspace.py --project <project-root>
python3 <plugin-root>/codex/init_evolution_workspace.py --project <project-root> --check
```

The initializer copies packaged v0 to
`<evolution-root>/workflow.draft.pave.yaml` without changing the package and
refuses to overwrite unowned or inconsistent state. Freeze only that durable
workspace:

```bash
python3 <plugin-root>/scripts/freeze_revision.py freeze <evolution-root> \
  --plan-evidence <verified|provisional> \
  --usage-evidence <none|clean_room|field>
python3 <plugin-root>/scripts/freeze_revision.py verify \
  <evolution-root>/history/v1
```

After a passing verify, the frozen copy in `history/v1/` is the authority. The freeze rewrites
`workflow-manifest.yaml` with only `active_revision`, `bundle_digest`, and
`history_dir`; the delivery-only `draft*` keys drop, which is expected, not
corruption. Record `usage_evidence` as what actually existed at freeze time —
`none` or `clean_room` for the first release, `field` only for a successor built
from real usage. Verify with `scripts/freeze_revision.py verify`.

## Enforcement

Run-wide prohibitions. The graph carries each node's `forbidden_effects`; the
rungs below are the run-wide layer on top of it, weakest to strongest: prose <
reinjection < reviewed/socratic < mechanical < blocking hook.

| # | Prohibition | Rung and where it lives |
|---|---|---|
| P1 | Never mutate protected base branches (release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline) on the fork or upstream | BLOCKING `hooks/protected-branch-guard.sh` |
| P2 | Never clear the shared Neuron compile cache ($VLLM_CACHE_ROOT/neuron/compile_cache, ~/.cache/vllm/neuron/compile_cache, /var/tmp/neuron-compile-cache) | BLOCKING `hooks/compile-cache-guard.sh` + delegate guardrail wrapper (a documented remedy that says "clear the cache" is intercepted, never followed) |
| P3 | No `cp -a` venv cloning; no pip writes into /opt | BLOCKING `hooks/venv-opt-guard.sh` |
| P4 | Zero `neuronx_distributed*` (NxDI) imports in ported code | MECHANICAL import scan over added/modified lines at `record_changeset` and re-checked at `review_implementation` |
| P5 | GPU baseline read-only; no autonomous reboot or reset; durable-host-state scoping | Contract text + reinjection + mechanical skew and identity probes; `capture_baseline_reference` refuses on a kickoff-record contradiction |
| P6 | The benchmark skill's provisioning STOP gate is never removed | Prose + reinjection — the rule rides every hardware brief you send |
| P7 | PRs only to the jinhuang12/vllm-neuron fork; merge stays human; fork sync is the user's | MECHANICAL `closure_evidence_settled` (PR URL resolves on the fork) + gate-3 user approval + no merge authority granted to any seat |
| P8 | Identical hardware retry forbidden | MECHANICAL fingerprint gate (repo fingerprint file vs this run's attempt log) before any attempt |
| P9 | Comparators are never chosen or altered after measurement begins | MECHANICAL `comparators_preregistered` (registration timestamp precedes every measurement artifact) + `forbidden_effects` at all four measure children + adjudication reads the registration digest |
| P10 | Lead is the single writer of run state, cross-run artifacts, and lease records | STRUCTURE (no other role holds those write paths) + `scripts/validate_run_state.py` on every write; derived counters are counted from event files so no child ever needs a state write |
| P11 | Measured revision is a git-issued identifier at measurement time, never a branch name | MECHANICAL `revision_stamped` on the runs-to-stabilization edge |
| P12 | Emit only declared outcomes; traverse only declared edges | `scripts/validate_pave.py` + run-state schema validation + the lead-alignment hook pair re-presenting your position |
| P13 | New kernel-class functionality the existing Neuron NKI library does not provide is implemented in NKI, never as a torch-level fallback (torch stays legitimate for orchestration and glue) | SPLIT RUNG. MECHANICAL for declaration presence (`assemble_design_record`'s completeness self-check requires a substrate declaration per increment — kernel-class or explicitly not) and for fidelity (`record_changeset`: a declared-NKI increment whose diff shows zero NKI usage is an exact contradiction, a coverage-gap hit). REVIEWED for the classification itself at both gate rubrics — an increment recorded non-kernel-class whose work is kernel-class is a material finding |

Costly-transition guards, all blocking routing preconditions you evaluate before
the transition: the measurement repair budget (no backward route at either
tier's threshold), the scan re-trace bound before a grant is issued, and the
hardware breaker (tenth budget-counted attempt, tier-1 fingerprint, or venv dead
end). Counts come from the event files per `references/artifact-layout.md` §4.

The seven hooks register in the plugin-level `hooks/hooks.json`. Review and trust
them through `/hooks` before a run. The P1-P3 PreToolUse adapter fails open
unless the project marker `.vllm-neuron-parity-run` resolves to an active,
nonterminal run state; unrelated Codex work stays outside their authority. The
write-for-reader reminder (`skills/vllm-neuron-parity/hooks/write-for-reader.sh`,
PostToolUse Write|Edit) is advisory only: on the first `.md` write under
`artifacts/` and every third after it per session, it re-presents the
write-for-the-reader duty; working-state paths and non-marker sessions stay
silent. No settings fragment is needed. Decline paths if the hook runtime is
unavailable: P1-P3 degrade to contract text you must carry into every brief and
to review at the next gate, the stop guard degrades to the resume duty above, the
staleness reminder degrades to the checkpoint duty above, and the reader reminder
degrades to the write-for-the-reader duty above. Record the degradation in run
state; do not proceed as if the guards were still armed.
