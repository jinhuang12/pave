# Workflow revisions

The revision contract for generated workflows. This is packaging policy, not a PAVE primitive: the five core primitives and the graph schema are unchanged, and no `pave:` field encodes revision state.

## Why revisions exist

A freshly planned graph is reviewed but untried. Revisions keep plan-time judgment and usage evidence separate: drafts are editable and carry no execution authority; frozen revisions are immutable, digest-pinned, and record the evidence that stood behind them when they froze.

## Versions

| Version | Meaning |
|---|---|
| `v0` | The reviewed, user-approved draft — `workflow.draft.pave.yaml`. Editable. Implicit when the manifest records a draft, no active revision, and no history. |
| `v1` | First immutable revision, frozen immediately before the first real execution. |
| `v2+` | Immutable successor revisions produced from usage evidence, discovered scope, or an approved design change. |

Version numbers record succession, not quality. User approval authorizes a bounded release; it does not prove the workflow succeeds in its domain. Usage evidence and known gaps stay explicit in each revision record.

`pave-init` delivery produces `v0`, never `v1`. Clean-room forward testing exercises the draft but is validation evidence only — it does not create `v1` and does not claim real-use evidence. The first real execution forces the freeze. The actor that freezes is whoever is about to execute: the generated skill's lead (evolving tier) or a fresh pave-init update run.

## Workspace structure

```text
<run-workspace>/
  workflow.draft.pave.yaml     # editable draft (v0), plus child *.draft.pave.yaml
  workflow-manifest.yaml       # draft record, active revision (or null), digests
  binding-revisions.yaml       # append-only, evolving tier: rule-9 binding revisions
  history/
    v1/
      workflow.pave.yaml       # frozen canonical root
      <child>.pave.yaml        # frozen child profiles
      revision.yaml
    v2/
      ...
```

At delivery the manifest records the approved `v0` draft and `active_revision: null`; `history/` does not exist yet. Frozen revision files must be regular, independent files. Reject symlinks and hard links in every revision path, so a frozen file can never alias the mutable draft or another revision.

## revision.yaml

```yaml
revision: 1
predecessor: null            # the prior revision number; null only for v1
derived_from: null           # set only on a rollback revision: the older revision its content came from
frozen_at_stage: release
bundle_digest: sha256:...    # digest over the sorted per-file digest records in this directory
evidence_basis:
  plan_evidence: verified    # or provisional, for an idea-only system
  usage_evidence: none       # none | clean_room | field — what evidence existed when this froze
semantic_diff: null          # v2+: what changed from the predecessor and why, in graph terms
```

`plan_evidence` carries what the Stage 3 prose marks `provisional`: an idea-only system freezes with `plan_evidence: provisional`, so the marker travels with the revision instead of living only in planning prose.

## Rules

1. **Draft authority.** `workflow.draft.pave.yaml` is the planning and approval subject. Review gates and user approval apply to the draft; approval designates it `v0`. Builders implement against the approved `v0` draft. The draft is never executed directly against a real target.
2. **Freeze before real execution.** `scripts/freeze_revision.py freeze` copies the draft (or, for rollback, an older frozen revision) into the next `history/vN/`, writes `revision.yaml`, computes digests, and sets the manifest's `active_revision`. Whoever is about to run the workflow for real freezes first and pins the run to that revision and bundle digest.
3. **Immutability.** Nothing edits a `history/vN/` bundle after freeze, and nothing changes an active run's revision mid-run. A change of any kind is a successor revision. `scripts/freeze_revision.py verify` detects mutation by digest mismatch.
4. **Successor revisions.** A successor starts its draft from the active revision, replans the narrowest affected boundary through the normal review gates, records a `semantic_diff` and `predecessor` link, and freezes the next `history/vN/`. Lineage is a linear chain: every revision's `predecessor` is the revision before it.
5. **Append-only rollback.** `active_revision` never moves backward. A rollback creates a new successor whose content is derived from the selected older revision (`freeze --from-revision N`), with `derived_from` and the reason recorded. History stays linear and the rollback is itself a recorded decision.
6. **Honesty.** Record `usage_evidence` as what actually existed at freeze time: `none` or `clean_room` for a first release, `field` only for a successor built from real usage.

## Generated-skill evolution

A generated skill may carry its own revision machinery — but only when its workflow's lifetime justifies the fixed cost. The planner records one evolution tier in `skill-package-plan.md` with a reason, at the root dispatch, next to the enforcement record. A tier wrong-sized in either direction is a review finding, exactly like wrong-sized enforcement.

### Tier: static

The default. For a workflow that runs once, or rarely, against a target that a fresh pave-init update run can re-plan when it drifts. The generated skill ships the approved canonical YAML and no revision machinery — no manifest, no history, no freeze script; the run workspace's manifest still records the `v0` lineage for a later update run.

Mid-run mis-sizing uses the graph's own declared routes: `scope_exceeded` or `replan_required` back to a plan node with an exhaustion bound. When reality departs from the graph in a way no declared outcome covers, the generated lead emits no invented outcome and traverses no invented edge. It pauses the run, preserves the discovery as evidence, and reports that the workflow needs a pave-init update run. That is the whole contract.

### Tier: evolving

Only for a long-lived workflow that is re-run repeatedly and expected to absorb usage evidence between runs. The reason must state what usage loop makes drift likely and why waiting for a manual update run is insufficient. An evolving-tier skill ships the workspace structure from this file (draft, manifest, `history/`) plus the freeze script, and its generated lead follows the evolution contract:

1. **Freeze and pin.** Before the first real execution, freeze `v1` from the approved `v0` draft. Every run records and verifies the manifest's active revision and bundle digest at start, and uses that frozen bundle. A mid-run manifest change does not move a running instance.
2. **Declared routes first.** Unexpected evidence that fits a declared `scope_exceeded` or `replan_required` outcome takes that route. Evolution machinery is not a bypass for routes the graph already has.
3. **Block honestly.** When no declared outcome fits: no invented outcome, no invented edge. Pause or block the run and preserve the discovery as evidence with its provenance.
4. **Successor draft.** Start a new draft from the active revision, replan only the narrowest affected boundary, and record a semantic diff — what changed from the predecessor and why, in graph terms. The why must name the gap the predecessor's plan missed and state why the change holds for runs in general — a successor ships to every future run, so a fix shaped to one run's particulars degrades all the others. A reason that cannot be stated apart from the triggering run is a review finding.
5. **Review.** The successor passes independent material review before freezing. Self-review does not qualify.
6. **Authority envelope.** The skill may freeze a successor autonomously only when everything in this envelope is unchanged: root goal and acceptance, allowed and forbidden effects, external access, public parent and sibling interfaces, evidence and check strength (never weaker; rule 9 bounds what counts as weaker when only the instrument changes), state authority, and approved budgets. A change to any of these requires explicit user approval before the freeze. Record the envelope check in the successor's `revision.yaml`.
7. **Linked run.** The paused run does not resume on the new revision. Close it honestly, then start a successor run pinned to the new revision, linked to its predecessor's run identity and evidence.
8. **Usage ledger.** At each run's terminal close, the lead derives a usage record from the run's event history — per boundary: traversals, seats dispatched, and any seat whose question was already settled by verified evidence at dispatch time — stored at a declared path beside the run's evidence; the generated skill's state or layout reference names it. A successor draft reads its predecessors' usage records before replanning, and its semantic diff states what they changed or why they changed nothing; a diff silent on recorded field evidence is a finding at rule 5's review. Actuals exceeding the plan's declared expectations — loop bounds, or the approved budgets already in the authority envelope — are a successor trigger surfaced at close: the ledger adds no mid-run gate, and a bound the graph itself enforces still routes mid-run per rule 2.
9. **Binding revisions.** Runtime-binding changes — seat, model, effort, or re-entry instrument (full seat, cheaper seat, or lead-run mechanical check) — do not change graph meaning and may ship without a graph successor as a user-approved binding revision, appended to `binding-revisions.yaml` beside the manifest (what changed, from what to what, the approval, and the envelope check), provided the envelope holds. Same check, cheaper instrument, mechanical evidence still produced is not a weakening of check strength; dropping a check is.

An active graph is never edited in place, in either tier. The difference is only who makes the successor: an update run of pave-init (static) or the generated skill's own lead within the envelope (evolving).
