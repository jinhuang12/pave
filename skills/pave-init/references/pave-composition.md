# PAVE Composition Contract

**Contract version:** 1.0.0
**Applies to:** PAVE YAML 0.3.0
**Schema:** [`pave-composition.schema.json`](pave-composition.schema.json)

## Contents

1. Purpose
2. Normative words
3. Definition and run
4. Declaration
5. Terminal map
6. Boundary rules
7. Authority delegation
8. Limits and enforcement strength
9. Verifiable outcomes
10. Contribution
11. State and resume
12. Runtime Binding responsibilities
13. Validation

## 1. Purpose

This document defines how one PAVE node delegates its work to a child Graph Profile.

Composition is a Runtime Binding. It states who performs a node's work. It does not change what the node means. Node, Outcome, Edge, Evidence, and Guard semantics from [`pave-yaml.md`](pave-yaml.md) apply unchanged at every depth.

The composed node keeps its full external contract: intent, purpose, roles, declared outcomes, consumes, and produces. The child profile is a private implementation of that contract. A parent edge never changes because a node gains or loses a child profile.

## 2. Normative words

**MUST**, **MUST NOT**, **SHOULD**, and **MAY** carry the same meaning as in `pave-yaml.md`.

## 3. Definition and run

A node definition states a goal: the result the node is responsible for. The `purpose` field carries this goal. A node run is one attempt to achieve that goal. A composed node run executes one child workflow run and ends by emitting exactly one of its own declared outcomes.

## 4. Declaration

Composition lives under the root `extensions` mapping. A profile that uses it MUST list it as required:

```yaml
extensions:
  required: [composition]
  composition:
    version: "1.0.0"
    realizations:
      port_model:
        kind: child_profile
        profile: port-model.pave.yaml

        inputs:
          target: state.frozen_target
          incumbent: state.pinned_incumbent

        evidence_exports:
          - child: return_bundle
            parent: port_return

        terminal_map:
          child_accepted: candidate_ready
          child_blocked: port_blocked
          child_exhausted: port_exhausted

        delegated_effects:
          - modify_candidate
          - run_validation

        limits:
          max_child_runs: 16
```

A runtime that does not understand the `composition` extension MUST stop before execution, per the required-extension rule in `pave-yaml.md` section 15.

### 4.1 Realization fields

Each key under `realizations` MUST name a declared node in the same profile.

| Field | Required | Meaning |
|---|---|---|
| `kind` | yes | MUST be `child_profile`. |
| `profile` | yes | Path to the child profile, relative to the parent profile file. |
| `profile_digest` | conditional | `sha256:` pin of the child file. See 4.2. |
| `entrypoint` | no | One child node to open instead of the child's declared entrypoints. When present, it MUST name a child node. |
| `inputs` | no | Mapping from child state field to parent state path. |
| `evidence_exports` | no | List of `{child, parent}` pairs. See 4.3. |
| `terminal_map` | yes | Mapping from child terminal endpoint identifier to parent outcome code. See 5. |
| `delegated_effects` | no | Effects the child may use. See 7. |
| `limits` | no | Attempt and resource budgets. See 8. |

### 4.2 Profile pinning

`profile_digest` is required only when the child profile lives outside the generated package — a reused or shared profile that other parties can change. For a child profile authored in the same package, the digest is optional: the package's approval gate freezes parent and child together, and a mechanical pin would only churn on every co-authored repair round.

When a digest is present, a validator MUST compare it against the referenced file and fail on mismatch.

### 4.3 Evidence exports

Each export names one child evidence definition (`child`) and one parent evidence definition (`parent`). The exported evidence keeps its original provenance: producer node, subject, environment, and time stay those of the child run. Export re-labels access; it does not re-author the evidence.

## 5. Terminal map

The terminal map is the only crossing between child and parent.

1. Every key MUST name a `terminal` control endpoint declared in the child profile.
2. Every value MUST name a declared outcome of the composed parent node.
3. Every child terminal endpoint MUST appear as a key. A child profile with a terminal that cannot map onto the parent contract needs a different parent outcome set or a different boundary, not an unmapped terminal.
4. A key maps to exactly one value. A value MAY receive several keys.
5. When the child run reaches a terminal endpoint, the parent node run emits the mapped outcome. Parent edge evaluation then proceeds exactly as `pave-yaml.md` section 12.3 defines.

### 5.1 Exactly one terminal

A node run emits exactly one outcome, so a child run MUST reach exactly one terminal endpoint. A child profile with concurrent branches MUST route them through a `join` endpoint before any terminal. A child run that reaches an unmapped terminal, or whose return is ambiguous or malformed, fails closed: the parent run is blocked, not routed.

## 6. Boundary rules

1. Edges MUST NOT cross a profile boundary in either direction. Child edges route child nodes; parent edges route parent nodes. The terminal map is the only connection.
2. Child nodes read parent information only through declared `inputs` and their own `consumes`. No implicit shared state.
3. Identifiers are scoped per profile. A child node identifier never collides with a parent identifier because no reference crosses the boundary.
4. A child profile MUST NOT reference its own ancestors. Profile references form a tree, not a cycle. A validator MUST reject reference cycles.
5. Composition depth MUST NOT exceed 2: a root profile, its children, and their children. The stopping rule for decomposition is the one-agent test, not this cap — with `activities` inside atomic nodes and runtime fan-out absorbing labor, a goal reaches passing leaves within two levels; a design that appears to need a third level has a mis-drawn boundary to redraw, and each extra level multiplies review and resume cost.

## 7. Authority delegation

The child workflow acts under the parent node's authority, narrowed by `delegated_effects`:

- When `delegated_effects` is present, every effect a child node uses MUST appear in that list, and the list MUST be a subset of the parent node's `allowed_effects` when the parent declares one. The validator enforces the subset clause mechanically; whether child nodes stay inside the delegated list is a plan-review question.
- When `delegated_effects` is absent, the child inherits the parent node's effect boundary unchanged.
- A child node MUST NOT hold authority the parent node does not hold.

## 8. Limits and enforcement strength

`limits` MAY declare `max_child_runs` and other budget fields. Budgets apply across all descendants of the realization.

Declared limits are validated structurally but enforced by instruction and review, not by a mechanical runtime: the executing lead honors them, and the review gate checks the plan against them. Do not read a declared limit as a mechanical guarantee. When a limit violation would be costly and simple to detect, the generated skill SHOULD add a mechanical check in its own validation step.

## 9. Verifiable outcomes

Every outcome named as a `terminal_map` value MUST declare `required_evidence`, and each acceptance-bearing outcome anywhere in the tree SHOULD do the same. This keeps the boundary auditable: an independent reviewer can judge the child's claim from the exported evidence without re-running the child.

Do not extend this rule to every outcome. Control-flow outcomes with self-explanatory codes stay legal without evidence, per `pave-yaml.md` section 11. Mandatory evidence everywhere trains planners to invent artifacts, which dilutes the evidence that matters.

## 10. Contribution

Each composed node's plan records how its child profile's purpose serves the parent purpose, one level at a time. Repeated up the tree, this rule yields traceability to the root goal.

Contribution statements are descriptive. They do not select edges, authorize transitions, prove the parent outcome, or replace evidence and independent judgment. A negative result can contribute: disproving an approach is valid support for a `replan_required` or `exhausted` outcome.

## 11. State and resume

The state contract of `pave-yaml.md` section 14 extends as follows:

- Every child node run SHOULD record its parent node-run chain, so a resume can locate the active path through the tree.
- Parent state and child state stay scoped. The child sees parent values only through `inputs`; the parent sees child results only through evidence exports and the terminal map.
- The parent node run stays pending while its child run is active. Pause and resume of the parent suspends and restores the child run with it.

## 12. Runtime Binding responsibilities

The executing runtime — for a generated Claude Code skill, the lead agent — owns:

- opening the child run when the parent node run starts;
- passing `inputs`;
- holding the parent run pending until one child terminal is reached;
- applying the terminal map and emitting the parent outcome;
- failing closed on ambiguous or invalid child returns;
- honoring declared limits and depth bounds.

One profile is one orchestration boundary. When a subgraph binds to a generated Workflow tool script, the script covers nodes of one profile only; a composed node inside a script is handed back to the lead, which orchestrates the child profile itself. Scripts never nest.

## 13. Validation

`scripts/validate_pave.py` validates composition when the extension is present:

1. The composition block passes `pave-composition.schema.json`.
2. Every realization key names a declared node.
3. Every referenced child profile resolves, parses, and passes full PAVE validation itself.
4. A present `profile_digest` matches the child file.
5. A present `entrypoint` names a child node.
6. Every terminal-map key names a child terminal endpoint; every child terminal endpoint is mapped; every terminal-map value names a declared parent outcome.
7. Every evidence-export pair names a declared child evidence definition and a declared parent evidence definition.
8. Every mapped parent outcome declares `required_evidence`.
9. Profile references contain no cycle and do not exceed depth 2.
10. Every `delegated_effects` entry appears in the parent node's `allowed_effects` when the parent declares one.

Structural validation proves the boundary is coherent. It does not prove that decomposition is justified or that child evidence supports the parent claim. That judgment belongs to plan review.
