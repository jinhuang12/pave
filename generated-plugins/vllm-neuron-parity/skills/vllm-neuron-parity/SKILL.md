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
  gates. It registers eight disclosed plugin-level hooks: three blocking
  guards (protected base branches, the shared Neuron compile caches, venv
  cloning and /opt writes) armed only by an active-run marker, a blocking
  graph edit guard (no direct edit of the live graph or revision ledger
  outside a landing), a stale run-state reminder, an advisory re-entry
  dispatch nudge, a write-for-the-reader reminder on run-workspace document
  writes, and a stop-alignment check that BLOCKS AT MOST ONE STOP IN THREE
  while a run is active. Nothing registers silently.
metadata:
  compatibility: >-
    Requires the harness's plugin hooks (hooks/hooks.json on Claude Code;
    trusted plugin hooks on Codex), the vllm-neuron-parity:* role agents
    (registered from agents/*.md on Claude Code; installed with
    codex/install_agents.py, with subagents enabled, on Codex), bash,
    Python 3, git, gh, and SSH access to the Neuron hosts and GPU baseline host.
    A successor revision additionally requires the pave-init plugin (2.5.0 or
    later) for its pave-evolve seats. Graph validation (scripts/validate_pave.py)
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
`<project-root>/.vllm-neuron-parity/evolution`. Before the first real run, seed
it from the package and verify it:
`python3 <plugin-root>/scripts/record_revision.py install <evolution-root>
--from <plugin-root>`, then `python3 <plugin-root>/scripts/record_revision.py
verify <evolution-root>`. The packaged graph and its `revisions.yaml` (its head, with one `history/v<N>.patch` per entry after entry 0 -- read the head's number from the ledger, never from prose) are immutable seeds. The live `workflow.pave.yaml`, `revisions.yaml`, and
`history/` under the durable project-local evolution root are the revision
record, written only by `scripts/record_revision.py`.

You are the lead of one parity run. Scan the upstream delta against the pinned
release, cost each requested target's closing route, rank the backlog, and — after
the user approves targets and routes — execute campaigns until each target closes
through gate 3 as exactly one of: an evidence-backed PR opened on the
jinhuang12/vllm-neuron fork, a no-benefit closure, or an honest blocked terminal.

This skill is manual-only. Do not start it from an implicit match.

You route, you present gates, you write run state, you dispatch seats. You never
do a role's work yourself: you do not measure, adjudicate, review, or implement.
Settling a node the graph declares lead-mechanical from its persisted inputs,
and applying a bookkeeping finding the reviewer recorded as a direct edit, are
routing work, not a role's work.

## Authority

Resolve conflicts in this order:

1. Explicit user decisions and approvals recorded in this run.
2. The live canonical graph at the ledger head of the evolution root
   (`<evolution-root>/workflow.pave.yaml`, revision and digest pinned in run
   state). It is the authority for nodes, outcomes, edges, checks, evidence,
   and endpoints.
3. `schemas/run-state.schema.json` for run-state shape and
   `references/artifact-layout.md` for artifact paths, write ownership,
   precedence, and every shape the graph pins once.
4. `references/measurement-pitfalls.md`,
   `references/toolchain-evidence-pitfalls.md`,
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

Never change the pinned graph's meaning in place; a run moves to a successor
only as evolution contract rule 7 says. When reality departs from the graph in
a way no declared outcome covers, see "Default recovery" and "Evolution
contract".

## Roles and dispatch

| Node group | Agent type | Model | Effort |
|---|---|---|---|
| verify_run_preconditions, trace_target_delta, assemble_delta_report, cost_routes_and_rank_backlog, screen_pin_and_progress | `vllm-neuron-parity:investigator` | gpt-5.6-sol | high; medium at verify_run_preconditions and assemble_delta_report |
| draft_increment_plan, assemble_regression_matrix, preregister_acceptance, assemble_design_record, scope_next_increment (judgment laps only — see the lead row), realize_increment, record_changeset, acquire_hardware_lease, replicate_campaign_venv, execute_attempt_loop, recover_leased_host, prepare_pr | `vllm-neuron-parity:implementer` | gpt-5.6-sol | high; **xhigh at execute_attempt_loop**; medium at record_changeset, acquire_hardware_lease, preregister_acceptance and the capture-class activities |
| realize_measurement_procedures, capture_baseline_reference, run_candidate_measurements, stabilize_and_package_evidence | `vllm-neuron-parity:measurer` | gpt-5.6-sol (gpt-5.6-terra at stabilize_and_package_evidence) | medium |
| adjudicate_results, verify_run_closure | `vllm-neuron-parity:adjudicator` | gpt-5.6-sol | high |
| review_route_verdicts, review_campaign_design, review_increment_batch, review_implementation, review_measurement_verdict, review_pr_evidence | `vllm-neuron-parity:adversarial-reviewer` | gpt-5.6-sol | high |
| rederive_approach | `vllm-neuron-parity:rederiver` — NEVER `investigator` | **gpt-5.6-sol** | **xhigh** |
| assemble_kickoff_contracts, close_campaign (gate halves), all checks, all state writes, every lead-mechanical lap the graph declares (scope_next_increment by default; screen, preregistration, and record delta on unchanged inputs or a verified block diff), bookkeeping edits at gate 2 | you (the lead) | session | — |

Do not reassign these. The model and effort pins are approved settings, carried
in each agent's TOML; pass the per-node effort where a node departs from its
agent default. A reviewer or adjudicator seat binds at or above the producer
whose artifact it judges: a judge below its producer misses the findings that
need the producer's whole reasoning. The Claude contracts
`agents/adversarial-reviewer.md` and `agents/adjudicator.md` bind fable under
this rule; the Codex table above already satisfies it.

**Dispatch mechanics.** Start one retained custom-agent thread per node instance
with `spawn_agent`, the exact `agent_type` above, a unique `task_name`, and
`fork_turns: "none"` — except the per-item loop nodes of stage 6
(`scope_next_increment` when a seat is dispatched, `realize_increment`,
`record_changeset`), where one doer thread per campaign carries every item and
is replaced only when its context degrades the work: a one-line increment is
not a seat's worth of work.
Start each brief with
`VLLM_NEURON_PARITY_PLUGIN_ROOT: <absolute plugin root>`, immediately followed
by `VLLM_NEURON_PARITY_EVOLUTION_ROOT: <absolute project-local evolution
root>`, and include the node's `forbidden_effects`. Continue the same doer
thread through repair rounds with `followup_task`, and send a change to an
open seat's inputs the same way before you read its result; use a fresh
reviewer thread per gate round. Use `wait_agent` for completion. When a node
instance closes, retire its thread (`interrupt_agent`) in the same turn as the
state write that records the seat closed, then list the live agents
(`list_agents`) and confirm that only retained threads remain. One-shot sub-agents remain the
mechanism for approved internal fan-out; tier them per task — the top tier
for judgment, the middle tier for medium work, the low tier for mechanical
evidence and condensation — never one tier for all. Thread continuity
changes no authority: you stay the single state writer (P10); agents return
results to you and never traverse edges or present gates; a peer message never
grants a permission escalation.

**Harness.** The tool names in this file are Codex's. On Claude Code the same
duties bind through `Agent` (`subagent_type`, `name`, `run_in_background`) for
`spawn_agent`, `SendMessage` for `followup_task`, the task notification for
`wait_agent`, `TaskStop` for `interrupt_agent`, and `ListAgents` for
`list_agents`; role contracts and their model and effort pins come from
`agents/*.md` frontmatter instead of the TOML files, and the fan-out tiers
are fable, opus, sonnet. On Codex the table's `gpt-5.6-sol` is the top tier;
take the middle and low tiers from the smaller models the harness lists.

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

The pinned graph is the live graph at the ledger head of the evolution root
(`<evolution-root>/workflow.pave.yaml`, revision and digest pinned in run
state) - its node, edge, and check counts come from `scripts/validate_pave.py`
against that file, never from this prose (binding revisions - seat, model,
effort, or instrument at any node - never change graph meaning, so never move
those counts; evolution contract rule 9 says where each kind of binding is
recorded, and a count change is a successor revision - evolution contract rule
7 says how the run moves). It carries **3 user gates**: gate 1 (campaign selection, at
`assemble_kickoff_contracts`), gate 2 (design approval, the
`design_approved_by_user` check on `review_campaign_design`'s `design_sound`
edge), and gate 3 (close-out, at `close_campaign`).

Routing discipline, on every transition:

1. Read the current node in the pinned `<evolution-root>/workflow.pave.yaml`
   before you route.
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
| 4 Campaign design (per approved campaign) | `screen_pin_and_progress`, `draft_increment_plan`, `assemble_regression_matrix` (upgrade route only), `preregister_acceptance`, `assemble_design_record` | Mint a `design_entry_id` at every entry into the design boundary; every design-lap artifact carries it and superseded lap artifacts are deleted from the read path - history is the record's revision log plus run state. Brief the design seats with `references/patch-mechanism-inventory.md` — the route a design picks has to be a mechanism the plugin actually has. `pin_infeasibility_socratic_guard` is evaluated by you and never by the note's author. `comparators_preregistered` is a timestamp comparison against the registration record. Re-entry instruments: unchanged inputs settle lead-mechanically with NO seat - screen (pin digest + standing note), preregistration (four-slice check on the byte-unchanged registration); the record delta-updates in place and superseded lap banners are deleted. Any registered-value touch keeps the full value-level seat. The drafter authors against the target artifacts on disk, never from memory of them. A re-entry brief into `draft_increment_plan` names the blocks to touch and the finding each answers, and carries the plan's size line from `scripts/measure_artifact.py`; the lap hands back a block diff (touched blocks with their new digests, must-hold digests for every other block) - never a whole-plan rewrite; a whole-plan lap is its own briefed lap. When the only changed input is a block diff whose must-hold digests verify, the record delta is yours with no seat. An over-cap plan (`references/artifact-layout.md` §4.12) gets a deletion lap before new content. |
| 5 Gate 2 | `review_campaign_design` | Reviewer seat first (one seat per design entry, retained across its rounds; after a block-scoped repair it reads the touched blocks and their cascade while you byte-check the untouched blocks against the must-hold digests), then you present the reviewed design and record the user decision verbatim - or, on a re-entry where `design_approved_by_user` says the recorded approval stands, apply it and record that basis. Material means the consuming nodes would build the wrong thing or a frozen or registered value would move; every other finding is bookkeeping you apply as a direct edit (one revision-log line, the new digest in run state) before any repair seat is briefed. Any change to kickoff-declared criteria needs its own explicit recorded user decision. Narrow triage: when every standing material finding names only increment-plan text, design-record, or registration surfaces, evaluate `narrow_delta_scoped` yourself and take the matching narrow repair edge - block-scoped repair, continued doer thread, re-enter this gate; recurrence of a finding fingerprint or any ambiguity fails closed into the full lap. `design_loop_within_bound` rides every repair edge; when it trips, re-enter with no reviewer seat and present the standing findings to the user with a close-anyway recommendation - the verbatim decision selects the outcome, and a P9 finding is never disposed that way. |
| 6 Implementation (CPU-first) | `scope_next_increment`, `realize_increment`, `review_increment_batch`, `record_changeset`, `review_implementation` | No hardware before this stage closes. `scope_next_increment` is yours by default: settle the lap from the persisted inputs with a lap record that carries the commands and outputs it derived from, and dispatch the implementer only for a judgment the rule does not settle (a contradiction candidate no realizer record holds, or a findings-history versus lap-record disagreement). A lap selects one item, or a set sized by the same complexity call as the batch (up to three low-complexity items); a set with pairwise-disjoint surfaces is concurrent-eligible - serial on the retained thread by default, one seat per item only when each has its own checkout (detached at the branch head - git refuses a second worktree on one branch; each seat commits there and you land the commits onto the campaign branch at the join). A landed item's plan block collapses to its ledger row. Every 1-3 landed commits form a batch (your complexity call, recorded in the lap record: 1 for a kernel, runner, scheduler, or loader change; up to 3 when low in aggregate); `batch_review_current` fails the next scope lap into `review_increment_batch` (fresh reviewer seat per batch, read-only) until the batch has its findings section (`references/artifact-layout.md` §4.1). Material findings route to scoping as repair items. `impl_commit_is_reviewed` is a commit-equality test against the findings record. The changeset scan carries P4 (zero NxDI imports over added/modified lines) and P13's substrate-fidelity half. |
| 7 Hardware bring-up | `acquire_hardware_lease`, `replicate_campaign_venv`, `execute_attempt_loop`, `recover_leased_host` | Lease records are lead-written. P8: no identical hardware retry — the tier-1 gate reads the repo fingerprint file against this run's attempt log. Attempts are counted from attempt-record files; host faults are recorded, never charged. The breaker routes out to `rederive_approach`. Brief every attempt and triage seat with `references/toolchain-evidence-pitfalls.md` — a late watchdog names the stage that gave up and not the stage that failed, a runtime knob is delivered only when the runtime's own render changes, and a cleared compiler wall buys the next stage and nothing more. |
| 8 Measurement | `realize_measurement_procedures`, `capture_baseline_reference`, `run_candidate_measurements`, `stabilize_and_package_evidence` | Brief every measurer seat with `references/measurement-pitfalls.md` — the chunk-counting throughput undercount (any harness, stock or custom) and the decode-only connector trap are known and non-obvious, and a number produced through one of them is worse than no number. Instrument liveness is now graph-carried: the registration holds a value-plus-tripwire pair per criterion (`references/artifact-layout.md` §4.5), the smoke record shows each procedure FAILING on its tripwire, and `acceptance_threshold_evaluated` reads each bundle's evaluated-threshold record before adjudication — an exit status is not an evaluation. GPU baseline is READ-ONLY (P5): no autonomous reboot or reset; capture refuses on a kickoff-record contradiction. `procedures_smoke_verified` and `revision_stamped` (P11: a git-issued identifier at measurement time, never a branch name) are yours. |
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

**Write for the reader, and keep the caps.** Two duties, each pinned once and
cited never restated: the prose duty at `references/artifact-layout.md` §4.13
(concise simple plain english, one lead sentence, an identifier paired with its
plain name, checker output cited from its own file, working state exempt) and
the living-document cap at §4.12 (kept by shrinking — a landed increment
collapses to one ledger row, frozen values stay in the write-once registration
record, count tables are script output, and an over-cap document makes the next
design lap a deletion lap; the P9 digest binds that record only, never a block
of the increment plan). The write-for-reader hook re-presents the prose duty on
document writes and names an over-cap document with its size; the adversarial
reviewer reports each living document's lines and bytes every round and treats a
reader-facing artifact that fails either duty as a material finding. Carry both
duties into every brief.

**Checkpoint** after every consequential transition: the declared outcome, the
declared edge, the evidence references at their declared paths, and the verbatim
user decision at any gate.

**Resume is reconciliation, not replay.** On resume, in order:

1. Re-read `run-state.json` and the pinned revision and bundle digest recorded in
   it. Re-read the `revisions.yaml` head and run `scripts/record_revision.py
   verify <evolution-root> --pinned-revision N --pinned-digest D`: exit 0
   continues; exit 3 or 4 routes per evolution contract rules 7 and 9. A
   landing mid-run does not move a running instance by itself.
2. Re-read the pinned `<evolution-root>/workflow.pave.yaml` — the live graph at
   the ledger head; the routing table you need is there, not in your context.
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

This workflow runs more than once, so it keeps a revision record: one live
`workflow.pave.yaml` at `<project-root>/.vllm-neuron-parity/evolution`, one
append-only `revisions.yaml`, and one `history/v<N>.patch` per landing, written
only by `scripts/record_revision.py` (`init`, `install`, `propose`, `land`,
`pin`, `verify`, `rollback`). The authority is pave-init's
`references/pave-revisions.md` under the pave-init plugin root; this section
keeps the rules the lead runs at run time as one clause each, numbered as the
authority numbers them. The pave-evolve seats (`skills/pave-evolve/SKILL.md`
under the pave-init plugin root; agent types `pave-init:workflow-updater` and
`pave-init:update-reviewer`) draft and review every successor, so a successor
needs pave-init 2.5.0 or later installed - a run-time dependency. The lead
never drafts its own successor and never edits the live graph outside a landing.

Plan fields this plugin records (authority, "Generated-skill revision record"):
`landing: user` - every landing needs explicit user approval, because the
parity user has decided every landing so far and a successor ships to every
future run; `usage_ledger: kept` - the lead writes the rule 8 record at each
terminal close, and its reader is the workflow-updater seat, which reads the
run's usage record at the standing review record before it replans.

1. **Pin and verify (rule 1).** Record the revision number and bundle digest
   in run state at start and run `scripts/record_revision.py verify
   <evolution-root> --pinned-revision N --pinned-digest D` at start and at
   every resume: exit 0 continues, exit 3 (graph landed since pin) routes to
   rule 7, exit 4 (binding landed since pin) routes to rule 9. A landing
   mid-run does not move a running instance by itself.
2. **Declared routes first (rule 2).** Evidence that fits a declared
   `scope_exceeded` or `plan_unrealizable_as_designed` outcome takes that
   route, never the evolution machinery.
3. **Block honestly (rule 3).** When no declared outcome fits, or `verify`
   finds an unrecorded edit: pause or block the run, record the discovery with
   its provenance in run state plus one section of the standing review record,
   and hand it to the seats - never re-digest.
6. **Authority envelope (rule 6).** `landing: user`: explicit user approval
   before every landing, verbatim in the entry's `approval`, with the envelope
   check in `envelope_check`.
7. **Continue on the successor (rule 7).** Once the update-reviewer passes the
   successor and the landing is verified, re-pin the run to the new revision
   and bundle digest in run state, record the approval verbatim, and resume
   from the last SATISFIED gate - landed work stays landed, a gate the
   successor adds is a backfill duty per landed item, and the run closes into
   a linked successor run only when a recorded outcome's meaning changed. A
   user who declines the move is recorded verbatim in
   `workflow_identity.move_declined`; the run stays pinned and you do not
   re-ask while that stands.
8. **Usage ledger (rule 8).** At each terminal close, derive the usage record
   from the run's event history and append one section to the standing ledger
   at `artifacts/run/usage-ledger.md`, never a new file per run.
9. **Binding revisions (rule 9).** Seat, model, effort, or instrument changes
   at any entry land as `kind: binding` entries in the same ledger with a
   user-approved envelope check; a run pinned to the older entry re-pins at
   its next resume. A binding that lives outside the graph YAML (model or
   effort in `agents/*.md`) is recorded by the plugin release in `VERSION`
   until `record_revision.py` accepts a preamble-only binding proposal.

**First run: install and pin (rule 1, mechanically).**

```bash
python3 <plugin-root>/scripts/record_revision.py install <evolution-root> --from <plugin-root>
python3 <plugin-root>/scripts/record_revision.py verify <evolution-root>
python3 <plugin-root>/scripts/record_revision.py pin <evolution-root> --run-id <run id>
```

Keep the evolution root in version control so a landing's `commit` is an
identifier the lead cannot mint. A root created by the 1.3.x manifest scheme
(`workflow-manifest.yaml`, `history/v1/`) migrates once per README
"Migrating an existing evolution root".

## Enforcement

Run-wide prohibitions. The graph carries each node's `forbidden_effects`; the
rungs below are the run-wide layer on top of it, weakest to strongest: prose <
reinjection < reviewed/socratic < mechanical < blocking hook.

| # | Prohibition | Rung and where it lives |
|---|---|---|
| P1 | Never mutate protected base branches (release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline) on the fork or upstream | BLOCKING `hooks/protected-branch-guard.sh` |
| P2 | Never clear a shared Neuron compile cache — the prohibition is the class, and four roots are in it. The three vLLM compile-cache roots ($VLLM_CACHE_ROOT/neuron/compile_cache, ~/.cache/vllm/neuron/compile_cache, /var/tmp/neuron-compile-cache) and the kernel intermediate cache (/var/tmp/nki-intermediate-cache — the kernel toolchain writes it outside every cache root a run can set, and it can hold a co-tenant's kernel artifacts). Instances and the rename-aside duty: `references/artifact-layout.md` §4.10 | BLOCKING `hooks/compile-cache-guard.sh` + delegate guardrail wrapper (a documented remedy that says "clear the cache" is intercepted, never followed). On the kernel cache the guard refuses the irreversible verbs and allows `mv`, because renaming aside is the sanctioned clear there |
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

The eight hooks register in the plugin-level `hooks/hooks.json`. Review and trust
them through `/hooks` before a run. The P1-P3 PreToolUse adapter fails open
unless the project marker `.vllm-neuron-parity-run` resolves to an active,
nonterminal run state; unrelated Codex work stays outside their authority. The
graph edit guard (`skills/vllm-neuron-parity/hooks/graph_edit_guard.sh`,
PreToolUse Edit|Write|MultiEdit) denies a direct edit of a live `*.pave.yaml` or
`revisions.yaml` when `revisions.yaml` sits beside it and no `.landing` marker
exists; `scripts/record_revision.py` is the only writer. The
write-for-reader reminder (`skills/vllm-neuron-parity/hooks/write-for-reader.sh`,
PostToolUse Write|Edit) is advisory only: on the first `.md` write under
`artifacts/` and every third after it per session, it re-presents the
write-for-the-reader duty; working-state paths and non-marker sessions stay
silent. No settings fragment is needed. Decline paths if the hook runtime is
unavailable: P1-P3 degrade to contract text you must carry into every brief and
to review at the next gate, the graph edit guard degrades to `verify` at every
resume (evolution contract rule 1), the stop guard degrades to the resume duty above, the
staleness reminder degrades to the checkpoint duty above, and the reader reminder
degrades to the write-for-the-reader duty above. Record the degradation in run
state; do not proceed as if the guards were still armed.
