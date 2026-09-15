# Explore and plan

## Contents

1. Select evidence angles
2. Dispatch explorers
3. Verify and synthesize
4. Plan the graph file
5. Build the approval bundle

## 1. Select evidence angles

Choose two to four angles that can run independently. Use only angles relevant to the approved requirements.

Default angles:

1. System structure and current behavior.
2. Evidence, validation, and acceptance.
3. Current workflow, state, recovery, and resume.
4. Runtime, tools, resources, and enforcement.

State one bounded question for each angle. Define exact repository paths or research scope and required evidence.

## 2. Dispatch explorers

Dispatch all explorers together through the active harness role mechanism so they run concurrently. Explorers are one-shot. Use the `system-explorer` role for every angle; its role contract, model, and effort ride on the native role definition.

Each prompt must include:

- the approved requirements path;
- one question;
- exact search scope;
- required primary evidence format;
- the assigned report path (absolute, `exploration/<angle>.md` under the run workspace);
- permission limits;
- instruction to write only the assigned report path and edit nothing else.

Explorers run in parallel and blind to each other. Use authoritative local sources first; browse primary or official sources when local evidence is insufficient. Ask before private-system access or mutation.

## 3. Verify and synthesize

Each explorer persists its own report to its assigned `exploration/<angle>.md` and returns only the path and a short summary. As each returns, confirm the report exists on disk at the assigned path — the file, not the reply, survives a compaction. Subagents do not write approval artifacts.

After all angles return, read each persisted report and independently check decisive claims — verification reads the persisted file, never the returned summary. Reject adjacent answers, missing citations, and conclusions stronger than their evidence.

Write `system-map.md`:

```markdown
# System map - <workflow>

## Target and limits
## Components and authorities
## Current work sequence
## Evidence flow
## State and persistence
## Failure and recovery
## Concurrency and resources
## Acceptance and closure
## Contradictions
## Missing capabilities
## Graph implications
```

For a conceptual system, label the map `provisional`. Keep assumptions visible.

## 4. Plan the graph file

The lead runs this procedure itself; node planners own one node each. Every planner dispatch is the same job at any depth — the root is just the first node in the queue. One planner elaborating a whole descendant graph in one context designs worst where the graph is deepest; the queue keeps every context bounded while the lead holds cross-branch compatibility, shared-state ownership, and global invariants.

### 4.1 Freeze the root node contract

From approved `requirements.md` and verified `system-map.md`, write the root node's five-part contract (`references/pave-spec.md` §5.1): purpose with its out-of-scope line, inputs, effects and authority limits, outcomes with the definition of done, roles — plus shared-state ownership and global budgets. It changes only by returning to Stage 1 approval. Run-wide artifacts are the lead's, not a planner's: draft the enforcement table, the revision record (`approval`, `usage_log`), and the state contract yourself, starting here and finalized at assembly.

When the goal ports or re-designs an existing system, the source binds behavior — acceptance, invariants, failure modes — never graph shape. Freeze the contract in behavior terms and let planners re-derive structure from the goal; a source's module or sub-workflow splits carry no design authority of their own.

### 4.2 Open the planning queue

Write `planning/planning-queue.yaml` with one entry: the root node, `status: pending`. `schemas/run-state.schema.json` `$defs.planning_queue` is the single authority for the file's shape and the entry lifecycle — do not restate its field list in prose — and `references/planning-layout.md` carries write ownership, precedence, and the prohibited patterns. Check the file with `scripts/validate_run_state.py --planning-queue` whenever the queue changes state.

Every node a planner frames enters the queue — atomic predictions included, because a prediction is not a verdict; each node's own dispatch decides it. Derive new entries by reading `extensions.x_planning.elaboration` in the planner's draft file on disk — never from its reply text; the reply is a notification, the draft is the source.

### 4.3 Dispatch the queue

Order entries by risk and dependency: a node others depend on, or whose failure would force reframing its siblings, goes first. Dispatch one `node-planner` per entry; dispatch independent entries together. The native role definition carries model and effort — do not override them per dispatch.

Each brief contains: the node's frozen contract, the chain of ancestor purposes, read-only sibling interfaces, the system map, the framing planner's prediction and rationale, the relevant references (`pave-yaml.md`, `pave-spec.md`, `pave-composition.md`, `planning-layout.md`) resolved under the pave-init installation path, and the draft path to write. Mint the draft path fresh for this dispatch — never a path any earlier dispatch used, even a dead one's — record it in the entry, and mark the entry `pending_dispatched` (`references/planning-layout.md`). A planner decides for itself whether it needs more evidence and dispatches read-only research workers through its native role contract, one bounded question each. Planners never change ancestor or sibling interfaces; they report conflicts — without ids: the lead assigns `c<N>` ids in the planning queue's conflict list.

On return, verify the draft against the node's contract, run `scripts/validate_run_state.py --planning-queue`, and mark the entry `planned`. Send the return to the planning reviewer as one unit — every return is reviewed before its verdict solidifies (reviewer mechanics in `orchestration/review-and-build.md` §1). On PASS, mark the entry `reviewed` and append the return's framed children to the queue as `pending`. On REVISE, re-dispatch that node's planner with the findings.

### 4.4 Resynchronize on conflict

Resolve an interface conflict at the parent level yourself: judge the smallest change that resolves it, apply it to the affected drafts, and mark every `reviewed` entry that depends on the changed interface `stale`. Redispatch stale entries with the updated contract.

A conflict that changes the root contract itself — goal, acceptance, effects, authority — exceeds planning authority: route to the user, not around them.

### 4.5 Close the queue

The queue closes when every entry is `reviewed`. A node that cannot close after bounded attempts is honest exhaustion: record why, and route the run to a blocked or replan outcome. Never present a bundle while an entry is silently open. A root the first dispatch confirms atomic closes the queue after one entry — the flow must cost nothing when the graph is simple.

### 4.6 Assemble

1. Merge the node drafts into one flat PAVE root `workflow.draft.pave.yaml` — the editable planning subject that delivery later applies as revision 0 per `references/pave-revisions.md`. Decomposition lineage flattens into one graph; add a child `*.draft.pave.yaml` only for a subgraph whose packaging met a §9.12.1 condition. Strip the `x_planning` extension block; the marks are planning state, not graph meaning — each node's implementation line goes into `traceability.md` and the enforcement table (section 5).
2. Run the global trim pass: remove every element — including any child graph file, and any seat on a node whose common path a lead-run check decides (`references/pave-spec.md` §2.1) — whose absence changes no required routing, authority, evidence, recovery, or acceptance.
3. Mark run setup (section 4.8). Run setup needs the whole-subgraph view, so planners do not mark it.
4. Reconcile model and effort assignments across nodes — one value per role; a role that needs two tiers is two roles.
5. Finalize the run-wide enforcement table in `skill-package-plan.md`: merge the node-local entries planners proposed with your own run-wide entries; deduplicate guards proposed for the same prohibition.
6. Validate the root with `scripts/validate_pave.py` — it follows composition references and validates every child graph file and crossing. Then send the assembled whole bundle to the same planning reviewer as the whole-graph round.

### 4.7 Design checks

Check these before review:

- One primary PEER intent per node.
- Outcomes describe results and do not encode destinations.
- Every nonterminal outcome routes somewhere useful.
- Edge checks state consequential transition conditions.
- Competing routes from one outcome are mutually exclusive.
- Dynamic fan-out has stable instance identity and an explicit join.
- Closure and acceptance are distinct.
- Recovery loops preserve attempt history and define exhaustion; a repair loop sits at the node that resolves the finding, declares its bound — and, on a design node, its text-only-round bound — and counts recurrence by defect class, repair-introduced among them, not site identity (`references/pave-spec.md` §9.8).
- A review node's outcomes partition findings by the repair class its graph routes; a bookkeeping defect reaches a lead edit, never the design route (`references/pave-spec.md` §9.8, §9.4).
- A loop that produces work later rounds build on carries its review inside the loop, on a declared batch cadence; a review after the loop is not the gate for the rounds inside it (`references/pave-spec.md` §9.4).
- A plan node that feeds an expensive execute node carries the pre-dispatch check duty — cheap half of the acceptance discharged pre-dispatch, unresolved-referent counts recorded — and a design node names which inputs are world artifacts on disk and which are premises (`references/pave-spec.md` §9.2).
- A budget counter with more than one increment route either gets per-route counters or an explicit shared-budget note — otherwise one route silently spends another's attempts.
- Domain extensions do not change core meaning.
- Missing runtime capability is declared, not invented.
- Exactly one outcome per node means success and carries a definition of done decided on external evidence.
- Every dispatched node has an `x_planning.elaboration` verdict with a falsifiable rationale the evidence supports and its implementation line (`references/pave-spec.md` §2.1); every framed child has a prediction with its rationale.
- Every child graph file records the §9.12.1 packaging condition it meets, its child outcome map is total, and no edge crosses a graph-file boundary.
- Every run-wide prohibition, costly-transition guard, and dispatched seat has a recorded enforcement strength with a reason a stronger enforcement level is unnecessary and — for an enforcement level with standing cost — a cheaper one insufficient.

### 4.8 Run setup

A subgraph is script-eligible when it contains no user-approval gate, no `pause` endpoint waiting on a human, and no `return` endpoint. For each maximal script-eligible subgraph, recommend one binding:

- `lead`: the lead orchestrates through the active harness role-dispatch mechanism. The default.
- `workflow_script`: the generated skill compiles the subgraph to one harness-native workflow script. Recommend this only when the subgraph has real fan-out or loops — enough agent traffic that routing it through the lead's context would be wasteful. A two-agent hop does not qualify.

A `workflow_script` binding never changes graph meaning: the YAML stays the authority (compile mapping in the native `skill-builder` role contract). Record each recommendation with its subgraph node list and rationale in `skill-package-plan.md`.

### 4.9 Limits of this procedure

- Partial planning artifacts under `planning/` are working state. Never present them as executable PAVE graph files, include them in the approval bundle, or let file existence imply approval.
- The lead assembles and integrates; it does not redesign a node itself. A defective return goes back to a node-planner dispatch.
- On resume mid-stage, read `planning/planning-queue.yaml`; every entry not `reviewed` is open work.

## 5. Build the approval bundle

Give every edge a stable `id`. Write `traceability.md` with one row for every role, evidence definition, check, node, edge, control node, and the state and completion contracts; a node row's planned implementation names the lead stage for a lead-run check or the role contract for a seat (`references/pave-spec.md` §2.1). Child graph file objects use qualified identifiers (`parent_node/child_id`), and every composed node gets one `implementation` row:

```markdown
| Type | ID | Planned implementation | Authority or purpose |
|---|---|---|---|
| node | inspect_target | SKILL.md#inspect-target | Establish the current system facts |
| implementation | port_model | port-model.pave.yaml | Child graph file implementing port_model |
| node | port_model/freeze_contract | SKILL.md#port-stage | Freeze the port contract |
```

Write `skill-package-plan.md` with:

- final directory tree — the active harness's native plugin manifest and role layout, plus the lead skill under `skills/<workflow-name>/`;
- one owner for every file;
- parallel build units with no overlapping files;
- graph IDs implemented by each unit;
- copied, condensed, generated, and excluded resources;
- run setup: each script-eligible subgraph with its recommended binding and rationale;
- the graph file dependency tree and the §9.12.1 packaging condition each child graph file meets, when packaging is used;
- the enforcement table: each run-wide prohibition, costly-transition guard, and dispatched seat (`references/pave-spec.md` §9.14.1) with its chosen strength, the reason a stronger enforcement level is unnecessary (and, for an enforcement level with standing cost, a cheaper one insufficient), and — for each planned hook — its event, matcher, script, actor scope with its harness-native placement or caller check, and decline path; plus the evidence-fakeability judgment for every node whose success evidence the doer produces — fakeable or not, with the hardening choice (`references/pave-spec.md` §9.14.1);
- the revision record: `approval` and `usage_log` with their reasons, or the one-session omission, per `references/pave-revisions.md`;
- scripts and tests required by mechanical checks;
- runtime dependencies and installation limits;
- clean-room forward-test prompt — when the graph declares repair loops, prefer a prompt that forces at least one bounded repair edge, because a happy-path-only pass never prices the loop traffic; for a manual-only generated skill the prompt names it explicitly (`/<workflow-name>:<workflow-name> <request>`), because a bare prompt may not load a manual-only skill in a headless session.

The bundle is presentable only after the planning queue is closed or honestly exhausted. Do not start skill construction before material review and whole-bundle user approval.
