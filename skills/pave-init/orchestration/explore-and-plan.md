# Explore and plan

## Contents

1. Select evidence lenses
2. Dispatch explorers
3. Verify and synthesize
4. Plan the Graph Profile
5. Build the approval bundle

## 1. Select evidence lenses

Choose two to four lenses that can run independently. Use only lenses relevant to the approved requirements.

Default lenses:

1. System structure and current behavior.
2. Evidence, validation, and acceptance.
3. Current workflow, state, recovery, and resume.
4. Runtime, tools, resources, and enforcement.

State one bounded question for each lens. Define exact repository paths or research scope and required evidence.

## 2. Dispatch explorers

Spawn all explorers in one message with the Agent tool so they run concurrently. Explorers are one-shot; give no `name`. Use `subagent_type: pave-init:system-explorer` for every lens — its role contract, model, and effort ride on the agent definition.

Each prompt must include:

- the approved requirements path;
- one question;
- exact search scope;
- required primary evidence format;
- the assigned report path (absolute, `exploration/<lens>.md` under the run workspace);
- permission boundary;
- instruction to write only the assigned report path and edit nothing else.

Explorers run in parallel and blind to each other. Use authoritative local sources first; browse primary or official sources when local evidence is insufficient. Ask before private-system access or mutation.

## 3. Verify and synthesize

Each explorer persists its own report to its assigned `exploration/<lens>.md` and returns only the path and a short summary. As each returns, confirm the report exists on disk at the assigned path — the file, not the reply, is the evidence that survives a compaction between explorer return and synthesis. Subagents do not write approval artifacts.

After all lenses return, read each persisted report and independently check load-bearing claims — verification reads the persisted file, never the returned summary. Reject adjacent answers, missing citations, and conclusions stronger than their evidence.

Write `system-map.md`:

```markdown
# System map - <workflow>

## Target and boundaries
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

## 4. Plan the Graph Profile

The lead runs this procedure itself; node planners own one node each. Every planner dispatch is the same job at any depth — the root is just the first node in the queue. One planner elaborating a whole descendant graph in one context produces its weakest design where the graph is deepest; the queue keeps every context bounded while the lead holds what local planning loses: cross-branch compatibility, shared-state ownership, and global invariants.

### 4.1 Freeze the root node contract

From approved `requirements.md` and verified `system-map.md`, write the root node's five-part contract (`references/pave-spec.md` §5.1): purpose with its out-of-scope line, inputs, effects and authority limits, outcomes with the definition of done, roles — plus shared-state ownership and global budgets. It changes only by returning to Stage 1 approval. Run-wide artifacts are the lead's, not a planner's: draft the enforcement record, the evolution tier, and the state contract yourself, starting here and finalized at assembly.

When the goal ports or re-designs an existing system, the source binds behavior — acceptance, invariants, failure modes — never graph shape. Freeze the contract in behavior terms and let planners re-derive structure from the goal; a source's module or sub-workflow boundaries carry no design authority of their own.

### 4.2 Open the planning queue

Write `planning/frontier.yaml` with one entry: the root node, `status: pending`. `schemas/run-state.schema.json` `$defs.frontier` is the single authority for the file's shape and the entry lifecycle — do not restate its field list in prose — and `references/planning-layout.md` carries write ownership, precedence, and the prohibited patterns. Check the file with `scripts/validate_run_state.py --frontier` whenever the queue changes state.

Every node a planner frames enters the queue — atomic predictions included, because a prediction is not a verdict; each node's own dispatch settles it. Derive new entries by reading `extensions.x_planning.elaboration` in the planner's draft file on disk — never from its reply text; the reply is a notification, the draft is the source.

### 4.3 Dispatch the queue

Order entries by risk and dependency: a node others depend on, or whose failure would force reframing its siblings, goes first. Dispatch one node planner per entry as `subagent_type: pave-init:node-planner`; independent entries dispatch concurrently in one message. The agent definition carries model and effort — do not override them per dispatch.

Each brief contains: the node's frozen contract, the chain of ancestor purposes, read-only sibling interfaces, the system map, the framing planner's prediction and rationale, the relevant references (`pave-yaml.md`, `pave-spec.md`, `pave-composition.md`, `planning-layout.md`) resolved under the pave-init installation path, and the draft path to write. Mint the draft path fresh for this dispatch — never a path any earlier dispatch used, even a dead one's — record it in the entry, and mark the entry `pending_dispatched` (`references/planning-layout.md`). Give planners the Agent tool: a planner decides for itself whether it needs more evidence and spawns read-only explorers with one bounded question each. Planners never change ancestor or sibling interfaces; they report conflicts — without ids: the lead assigns `c<N>` ids in the frontier's conflict register.

On return, verify the draft against the node's contract, run `scripts/validate_run_state.py --frontier`, and mark the entry `planned`. Send the return to the planning reviewer as one unit — every return is reviewed before its verdict solidifies (reviewer mechanics in `orchestration/review-and-build.md` §1). On PASS, mark the entry `reviewed` and append the return's framed children to the queue as `pending`. On REVISE, re-dispatch that node's planner with the findings.

### 4.4 Resynchronize on conflict

Resolve an interface conflict at the parent level yourself: judge the smallest change that resolves it, apply it to the affected drafts, and mark every `reviewed` entry that depends on the changed interface `stale`. Redispatch stale entries with the updated contract.

A conflict that changes the root contract itself — goal, acceptance, effects, authority — exceeds planning authority: route to the user, not around them.

### 4.5 Close the queue

The queue closes when every entry is `reviewed`. A node that cannot close after bounded attempts is honest exhaustion: record why, and route the run to a blocked or replan outcome. Never present a bundle while an entry is silently open. A root the first dispatch confirms atomic closes the queue after one entry — the flow must cost nothing when the graph is simple.

### 4.6 Assemble

1. Merge the node drafts into one flat PAVE root `workflow.draft.pave.yaml` — the editable `v0` subject that delivery later freezes per `references/pave-revisions.md`. Decomposition lineage flattens into one graph; add a child `*.draft.pave.yaml` only for a subgraph whose packaging met a §9.12.1 condition. Strip the `x_planning` extension block; the marks are planning state, not graph meaning.
2. Run the global simplicity pass: remove every element — including any child profile — whose absence changes no required routing, authority, evidence, recovery, or acceptance.
3. Mark runtime bindings (section 4.8). Bindings need the whole-subgraph view, which is why planners do not mark them.
4. Reconcile model and effort assignments across nodes; keep the verify-retry escalation rule uniform (a check that fails twice at assigned effort retries once one tier up before the failure edge).
5. Finalize the run-wide enforcement record in `skill-package-plan.md`: merge the node-local entries planners proposed with your own run-wide entries; deduplicate guards proposed for the same prohibition.
6. Validate the root with `scripts/validate_pave.py` — it follows composition references and validates every child profile and boundary. Then send the assembled whole bundle to the same planning reviewer as the whole-graph round.

### 4.7 Design boundaries

Check these before review:

- One primary PEER intent per node.
- Outcomes describe results and do not encode destinations.
- Every nonterminal outcome routes somewhere useful.
- Edge checks state consequential transition conditions.
- Competing routes from one outcome are mutually exclusive.
- Dynamic fan-out has stable instance identity and an explicit join.
- Closure and acceptance are distinct.
- Recovery loops preserve attempt history and define exhaustion.
- A budget counter with more than one increment route either gets per-route counters or an explicit shared-budget note — otherwise one route silently spends another's attempts.
- Domain extensions do not change core meaning.
- Missing runtime capability is declared, not invented.
- Exactly one outcome per node means success and carries a definition of done settled on world-produced evidence.
- Every dispatched node has an `x_planning.elaboration` verdict with a falsifiable rationale the evidence supports; every framed child has a prediction with its rationale.
- Every child profile records the §9.12.1 packaging condition it meets, its terminal map is total, and no edge crosses a profile boundary.
- Every run-wide prohibition and costly-transition guard has a recorded enforcement strength with a reason a stronger rung is unnecessary.

### 4.8 Runtime bindings

A subgraph is script-eligible when it contains no user-approval gate, no `pause` endpoint waiting on a human, and no `return` endpoint. For each maximal script-eligible subgraph, recommend one binding:

- `lead`: the lead orchestrates with Agent tool calls. The default.
- `workflow_script`: the generated skill compiles the subgraph to one Workflow tool script. Recommend this only when the subgraph has real fan-out or loops — enough agent traffic that routing it through the lead's context would be wasteful. A two-agent hop does not qualify.

A `workflow_script` binding never changes graph meaning: the YAML stays the authority (compile mapping in the `pave-init:skill-builder` agent definition, plugin `agents/skill-builder.md`). Record each recommendation with its subgraph node list and rationale in `skill-package-plan.md`.

### 4.9 Boundaries of this procedure

- Partial planning artifacts under `planning/` are working state. Never present them as executable PAVE profiles, include them in the approval bundle, or let file existence imply approval.
- The lead assembles and integrates; it does not redesign a node itself. A defective return goes back to a node-planner dispatch.
- On resume mid-stage, read `planning/frontier.yaml`; every entry not `reviewed` is open work.

## 5. Build the approval bundle

Give every edge a stable `id`. Write `traceability.md` with one row for every role, evidence definition, check, node, edge, control endpoint, and the state and completion contracts. Child-profile objects use qualified identifiers (`parent_node/child_id`), and every composed node gets one `realization` row:

```markdown
| Type | ID | Planned implementation | Authority or purpose |
|---|---|---|---|
| node | inspect_target | SKILL.md#inspect-target | Establish the current system facts |
| realization | port_model | port-model.pave.yaml | Child profile realizing port_model |
| node | port_model/freeze_contract | SKILL.md#port-stage | Freeze the port contract |
```

Write `skill-package-plan.md` with:

- final directory tree — a plugin package: `.claude-plugin/plugin.json`, role agents under `agents/`, the lead skill under `skills/<workflow-name>/`;
- one owner for every file;
- parallel build units with no overlapping files;
- graph IDs implemented by each unit;
- copied, condensed, generated, and excluded resources;
- runtime bindings: each script-eligible subgraph with its recommended binding and rationale;
- the profile dependency tree and the §9.12.1 packaging condition each child profile meets, when packaging is used;
- the enforcement record: each run-wide prohibition and costly-transition guard with its chosen strength, the reason a stronger rung is unnecessary, and — for each planned hook — its event, matcher, script, actor scope with its mechanism (frontmatter placement, lead-only event, or identity gate), and decline path; plus the evidence-gameability judgment for every node whose success evidence the doer produces — gameable or not, with the hardening choice (`references/pave-spec.md` §9.14.1);
- the evolution tier (`static` or `evolving`) with its reason, per `references/pave-revisions.md`;
- scripts and tests required by mechanical checks;
- runtime dependencies and installation boundary;
- clean-room forward-test prompt — for a manual-only generated skill the prompt names it explicitly (`/<workflow-name>:<workflow-name> <request>`), because a bare prompt may not load a manual-only skill in a headless session.

The bundle is presentable only after the planning frontier is closed or honestly exhausted. Do not start skill construction before material review and whole-bundle user approval.
