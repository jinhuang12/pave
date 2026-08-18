# PAVE Workflow Definition YAML

**Specification version:** 0.3.0  
**Status:** Draft normative specification  
**Schema:** [`pave.schema.json`](pave.schema.json)

## Contents

1. Purpose
2. Normative words
3. Validation boundary
4. PAVE layers
5. Document root
6. Identifiers and namespaces
7. Role
8. Evidence definition
9. Check — styles, results, `on_failure_route`
10. Node
11. Outcome
12. Edge — destinations, fan-out, routing rule
13. Control endpoint — kinds, `terminal_status`
14. State contract
15. Extensions
16. Graph validity rules
17. Runtime execution contract
18. Versioning

## 1. Purpose

This document defines the YAML format for a PAVE workflow and its graph rules. The graph contains work nodes, outcomes, evidence definitions, checks, edges, roles, state requirements, and control endpoints.

The format is runtime-independent. A scheduler can execute it. A human team can also follow it. This document fixes syntax and validity; the [PAVE specification](pave-spec.md) defines the meaning behind every field — node, outcome, evidence, guard, edge, control endpoint, node sizing, and the reusable patterns.

## 2. Normative words

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** state requirements.

- **MUST** means that a valid PAVE definition requires the rule.
- **MUST NOT** means that a valid PAVE definition prohibits the rule.
- **SHOULD** means that authors need a documented reason to omit the rule.
- **MAY** means that the rule is optional.

## 3. Validation boundary

PAVE uses two forms of validation.

### 3.1 Definition validation

Definition validation is mechanical: does the YAML match the schema, does every reference (role, edge source outcome, edge destination) exist, and does every nonterminal outcome have a route? A definition that fails these checks is not a valid PAVE 0.3.0 workflow definition.

### 3.2 Workflow review

Workflow review uses human or LLM judgment: does the graph solve the intended problem, is the evidence strong enough, are roles sufficiently independent, and are recovery and completion rules useful?

Structural validity does not prove workflow quality. The PAVE guide defines the Socratic review method.

## 4. PAVE layers

PAVE separates workflow meaning from execution technology.

| Layer | Concrete representation | Responsibility |
|---|---|---|
| Design guide | `pave-spec.md` | Meaning, principles, patterns, and review questions. |
| Graph Profile | One `*.pave.yaml` file | The formal workflow graph and domain policy. |
| Runtime Binding | Runtime code or a declared extension | Actor assignment, persistence, hooks, scheduling, and resource isolation. |
| Workflow Run | Runtime state and evidence artifacts | One execution of the Graph Profile. |

The YAML document is the Graph Profile. Sections 5 through 14 define where each core concept lives in the `pave` mapping.

## 5. Document root

A PAVE file MUST contain one top-level `pave` mapping.

```yaml
pave:
  version: 0.3.0
  name: repair_compile_failure
  purpose: Find and repair the cause of a compile failure.
  entrypoints: [inspect_failure]
  roles: {}
  evidence: {}
  checks: {}
  nodes: {}
  edges: []
  control_endpoints: {}
  state: {}
```

The following root fields are required.

| Field | Type | Meaning |
|---|---|---|
| `version` | string | PAVE YAML specification version. It MUST be `0.3.0`. |
| `name` | identifier | Stable workflow identifier. |
| `purpose` | string | Result that the workflow is designed to produce. |
| `entrypoints` | identifier list | Nodes that open when a new workflow run starts. |
| `roles` | mapping | Role definitions, keyed by role identifier. |
| `evidence` | mapping | Evidence definitions, keyed by evidence identifier. |
| `checks` | mapping | Transition check definitions, keyed by check identifier. |
| `nodes` | mapping | Node definitions, keyed by node identifier. |
| `edges` | list | Directed transitions from outcomes to destinations. |
| `control_endpoints` | mapping | Pause, join, return, control, and terminal destinations. |
| `state` | mapping | Information that a runtime must preserve. |

The root MAY contain `extensions`. Domain-specific sections SHOULD be placed under this mapping. The root MAY also contain these standard descriptive fields: `status`, `scope`, `principles`, `completion`.

An extension that remains at the root MUST use an `x_` prefix. Unknown fields without this prefix are invalid.

## 6. Identifiers and namespaces

An identifier MUST match this expression:

```text
^[a-z][a-z0-9_]*$
```

Role, evidence, check, node, and control endpoint identifiers use separate namespaces. Node and control endpoint identifiers MUST be unique when combined. This rule makes edge destinations unambiguous.

Identifiers are read by the humans who approve the graph, not only by the validator. A node identifier SHOULD name its action in plain words, verb first (`record_baseline`, `repair_build` — not `phase2`, `rbm`, or a source-system codename), and an outcome code SHOULD state what happened (`parity_missed`, not `error2`). A reader who knows nothing else should be able to guess from the identifier alone what the node does and what the outcome means. Sequence numbers do not belong in identifiers: edges carry the order, loops make numbers lie, and the approval brief renders an at-a-glance happy-path diagram for readers who want the sequence at a glance.

An edge source uses the format `node_id.outcome_code`. An outcome code MUST follow the same identifier rule.

## 7. Role

A Role defines responsibility and authority. It is not an actor identity.

```yaml
roles:
  reviewer:
    purpose: Judge whether the candidate satisfies the acceptance conditions.
    authority: authoritative
    permitted_intents: [review]
    permitted_effects: [write_judgment_evidence]
    forbidden_effects: [modify_candidate]
    independence:
      - must not author the candidate under review
```

Required fields: `purpose`.

Optional standard fields: `authority`, `permitted_intents`, `permitted_effects`, `forbidden_effects`, `independence`.

Every role referenced by a node or check MUST exist in `roles`.

## 8. Evidence definition

An Evidence definition describes durable information that nodes produce or use.

```yaml
evidence:
  compiler_log:
    kind: observation
    produced_by: compile_candidate
    subject: candidate_revision
    artifact: artifacts/compiler.log
```

Required fields: `kind`, `produced_by`.

`kind` MUST be `observation`, `derivation`, `judgment`, or a domain-specific identifier. `produced_by` MUST name one node or a list of nodes. Every named node MUST exist.

Optional standard fields include: `subject`, `sources`, `artifact` or `artifacts`, `scope` or `required_scope`, `freshness`, `stale_after`, or `refresh_after`, `authority`.

These definitions describe evidence types or named artifacts. Runtime evidence instances SHOULD also record the workflow run, node run, producer, subject revision, environment, and creation time when those fields affect meaning.

## 9. Check

A Check defines a condition that controls an edge. A Check becomes a Guard when an edge references it. `checks` is the YAML field name. Guard is the graph-semantics term.

```yaml
checks:
  evidence_is_current:
    style: socratic
    question: Does the evidence apply to the current candidate and environment?
    evaluated_by: reviewer
```

Required fields: `style`, `question`.

`style` MUST be one of: `reflective`, `socratic`, `reviewed`, `mechanical`.

Optional standard fields include: `evaluated_by`, `requires`, `guidance`, `on_failure`, `on_failure_route`.

A check evaluation has one of four results:

- `pass`: The condition is satisfied.
- `fail`: Available information contradicts the condition.
- `blocked`: Required evidence, capability, or authority is unavailable.
- `error`: The check could not complete.

Only `pass` makes an edge eligible. A check can use LLM judgment. `mechanical` is not required unless the workflow profile selects it.

`on_failure` states in prose what a failure means. When a check guards an outcome's only edge, a failure is a designed stop, so that check MUST also name `on_failure_route`: a declared node or control endpoint the run takes instead. A terminal endpoint used as a route MUST declare `terminal_status` (§13). `scripts/validate_pave.py` enforces both.

## 10. Node

A Node defines one bounded unit of work. Its `purpose` is the node's goal: the result the node is responsible for. A node run is one attempt to achieve that goal.

A Node is one work contract regardless of how its work is performed. A Runtime Binding MAY realize a node through a child Graph Profile; the [composition contract](pave-composition.md) defines that binding. Child execution does not alter Node, Outcome, Edge, Evidence, or Guard semantics, and parent edges never cross into a child profile.

```yaml
nodes:
  inspect_failure:
    intent: explore
    purpose: Determine why the candidate does not compile.
    roles: [investigator]
    consumes: [compiler_log]
    produces: [root_cause_report]
    outcomes:
      simple_fix_found:
        meaning: A bounded repair is available.
      redesign_required:
        meaning: The current design cannot satisfy the target.
      cause_unresolved:
        meaning: Current evidence is insufficient.
```

Required fields: `intent`, `purpose`, `roles`, `outcomes`.

`intent` MUST be one PEER value: `plan`, `explore`, `execute`, `review`.

`roles` MUST contain at least one declared role. `outcomes` MUST contain at least one outcome. A node run MUST emit exactly one declared outcome.

Optional standard fields include: `consumes`, `produces`, `activities`, `allowed_effects`, `forbidden_effects`, `review`, `instance_per`.

`consumes` and `produces` contain workflow artifact identifiers. They MAY refer to entries in `evidence`. They MAY also name domain inputs, state values, or intermediate artifacts.

`instance_per` names a declared state collection. It marks a node whose runs are created one per collection item, matching the fan-out destination rule in section 12.2. The runtime MUST preserve a stable identity for each item and node run.

A node MUST NOT carry fields outside this contract. The validator rejects unknown node fields so that an unsupported construct fails closed instead of passing unvalidated.

## 11. Outcome

An Outcome states how a node run ended.

```yaml
outcomes:
  candidate_ready:
    meaning: The candidate is ready for independent review.
    required_evidence: [candidate_revision]
```

An outcome definition MAY be empty when its code is self-explanatory. Standard fields include: `meaning`, `required_evidence`, `data_schema`.

An outcome MUST describe the result of work. It MUST NOT encode or invoke its destination. Edges own routing.

Each nonterminal outcome MUST appear as an edge source at least once.

## 12. Edge

An Edge routes one node outcome to another node or to a control endpoint.

```yaml
edges:
  - from: inspect_failure.simple_fix_found
    to: apply_fix
    checks: [evidence_is_current]
    rationale: Apply the bounded repair to the current candidate.
```

Required fields: `from`, `to`.

Optional standard fields include: `id`, `checks`, `rationale`, `state_effects`.

Every check reference MUST exist.

### 12.1 Single destination

A string destination MUST name one declared node or control endpoint.

```yaml
to: apply_fix
```

### 12.2 Dynamic fan-out

A fan-out destination opens one node run for each item in a runtime collection.

```yaml
to:
  fan_out: implement_track
  for_each: selected_candidate
  pair_each_with: monitor_track
```

`fan_out` and `pair_each_with` MUST name declared nodes. `for_each` MUST name a state field or collection defined by the profile.

The runtime MUST preserve a stable identity for each fan-out item and node run.

### 12.3 Routing rule

After a node emits an outcome, the runtime evaluates all edges with that source.

- If one edge is eligible, the runtime traverses it.
- If no edge is eligible, the run is blocked unless profile policy gives an explicit route.
- If several edges are eligible, the runtime MUST stop with a routing error.
- A workflow MUST use a fan-out destination for intentional multi-destination traversal.

Checks on edges that share one source SHOULD be mutually exclusive.

## 13. Control endpoint

A Control Endpoint is a named destination handled by the workflow control plane.

```yaml
control_endpoints:
  pause_external:
    kind: pause
    meaning: Wait for required hardware while preserving resumable state.

  complete:
    kind: terminal
    meaning: End the workflow after the final status is recorded.
```

Required fields: `kind`, `meaning`.

`kind` MUST be one of:

- `pause`: Suspend the workflow and preserve resumable state.
- `join`: Wait for a declared set of node runs.
- `return`: Resume a previously recorded node or edge context.
- `control`: Perform profile-defined control-plane behavior.
- `terminal`: Close the workflow run.

A terminal endpoint SHOULD state whether it means accepted, closed without acceptance, blocked, incomplete, or exhausted — in the `terminal_status` field (`accepted`, `closed_unaccepted`, `blocked`, `incomplete`, `exhausted`). The field is REQUIRED when the endpoint is any check's `on_failure_route` (§9).

Control endpoints do not perform PEER work. If a destination must analyze, change, or judge the system, define it as a node.

## 14. State contract

The `state` mapping defines information that the runtime must preserve. It does not contain live run state.

```yaml
state:
  required:
    - active_node_runs
    - completed_outcomes
    - evidence_references
    - traversal_history
    - terminal_classification
  freshness_rules: {}
  checkpoint: {}
```

The state contract MUST preserve enough information for an authorized actor to resume the workflow.

It SHOULD cover: workflow and profile identity, frozen target, active node runs, completed outcomes, evidence references, open questions and blockers, loop counters and exhaustion memory, traversal history, and terminal classification.

A domain profile MAY use an external JSON Schema for live state.

## 15. Extensions

PAVE permits domain-specific data. Root extensions SHOULD use the `extensions` mapping:

```yaml
extensions:
  ammo:
    audit_cycle_limit: 3
    state_schema: schemas/state.schema.json
```

Extension fields MUST NOT change the meaning of a core field.

Large profiles MAY use an `x_` root field when moving the full section under `extensions` would reduce readability:

```yaml
x_runtime_binding:
  scheduler: custom_campaign_service
```

A runtime that does not understand a required extension MUST stop before execution. It MUST NOT silently ignore that extension. Profiles SHOULD list required extension namespaces under `extensions.required` when safe execution depends on them.

One standard extension is defined: `composition`, which realizes a node through a child Graph Profile. Its contract, boundary rules, and validation are defined in [`pave-composition.md`](pave-composition.md). Composition conforms to the extension rule above because it assigns who performs a node's work while the node still emits one of its own declared outcomes.

## 16. Graph validity rules

A valid PAVE 0.3.0 definition satisfies all of these rules:

1. The document passes `pave.schema.json`.
2. Every entrypoint names a node.
3. Every node role names a declared role.
4. Every evidence producer names a declared node.
5. Every check evaluator, when it is a role identifier, names a declared role.
6. Every edge source names a declared node outcome.
7. Every edge check names a declared check.
8. Every edge destination names a node or control endpoint.
9. Every fan-out and paired node reference exists.
10. Every nonterminal outcome has at least one outgoing edge.
11. Node and control endpoint identifiers do not collide.
12. At least one terminal control endpoint exists.

The repository validator checks these cross-reference rules:

```bash
python3 <pave-init>/scripts/validate_pave.py workflow.pave.yaml
```

## 17. Runtime execution contract

A conforming runtime follows this minimum sequence:

1. Load and validate the workflow definition.
2. Create workflow state.
3. Open the declared entrypoint node runs.
4. Assign each node run to an eligible role.
5. Preserve required outputs and evidence.
6. Record exactly one declared outcome for each completed node run.
7. Evaluate edges for that outcome.
8. Traverse one eligible edge or perform declared fan-out.
9. Persist state after every consequential transition.
10. Stop only at a terminal endpoint or an explicit blocked runtime error.

The runtime MAY use agents, humans, services, hooks, scripts, or a combination.

## 18. Versioning

PAVE uses semantic versions for the YAML format.

- A patch version clarifies text or fixes a schema defect without changing valid documents.
- A minor version adds backward-compatible fields or values.
- A major version can remove or change existing meaning.

A workflow MUST declare one exact version. A runtime MUST reject unsupported versions.

Format history:

- **0.3.0** — adds `on_failure_route` (§9) and requires it on any check guarding an outcome's only edge; a terminal endpoint used as a route requires `terminal_status` (§13). This tightens validity — a document valid under 0.2.0 can fail 0.3.0 — which is a meaning change, hence the minor bump at 0.x. Migrate by setting `version: "0.3.0"` and adding the routes `scripts/validate_pave.py` flags.
- **0.2.0** — baseline of this specification.

This format version and the design-language version in `pave-spec.md` track different contracts and move independently; each document's header records the pairing it was written against.
