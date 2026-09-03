# Workflow revisions

The revision contract for generated workflows and for pave-init itself. This is packaging policy, not a PAVE primitive: the five core primitives and the graph schema are unchanged, and no `pave:` field encodes revision state.

## Why revisions exist

A freshly planned graph is reviewed but untried. Revisions keep plan-time judgment and usage evidence separate: one live graph that runs execute, one append-only ledger that records what stood behind every change to it, and one patch per change so any revision can be read back or restored. Nothing is a copy the editing actor can silently re-digest — a copy-and-digest convention has no defence against the actor that holds the pen.

## The evolution root

```text
<evolution-root>/
  workflow.pave.yaml       # the live canonical root, plus any child <name>.pave.yaml
  revisions.yaml           # append-only ledger; entry 0 is the delivered graph
  history/
    v1.patch               # preamble + unified diff that landed revision 1
    v2.patch
  .landing                 # exists only while a landing, pin, or rollback is in progress
```

The delivered package is itself a valid root: entry 0, no patches. A project's root is separate — an installed package is never the live file — and the generated lead names it, one per project; `scripts/record_revision.py install` seeds it from the package before the first real run. Keep the root in version control, so a landing's `commit` is an identifier the lead cannot mint (`references/pave-spec.md` §5.3.1). Graph files must be regular, independent files; the tool rejects symlinks and hard links so the live graph can never alias another copy.

## The ledger entry

```yaml
entries:
  - revision: 3
    kind: graph                 # graph | binding — declared by the proposer, confirmed by review; the tool alone writes pin entries
    landed_at: 2026-09-03T10:12:00Z
    digest_before: sha256:...   # the head this landed on
    digest_after: sha256:...    # bundle digest over the sorted per-file digests of the graph files
    semantic_diff: what changed from the predecessor and why, in graph terms
    approval: "approve revision 3 sha256:..."   # verbatim, or null when the envelope held
    envelope_check: unchanged   # unchanged | changed_with_approval
    plan_evidence: verified     # or provisional, for an idea-only system
    usage_evidence: field       # none | clean_room | field — what existed when this landed
    review: PASS after 2 rounds
    patch: history/v3.patch
    commit: 9f3c1e2             # null when the root is untracked or the landing did not commit
    derived_from: null          # rollback entries only: the revision whose content came back
    run_id: null                # pin entries only
```

`scripts/record_revision.py` writes every entry with exactly these fields; a preamble may set only `kind`, `semantic_diff`, `envelope_check`, `plan_evidence`, `usage_evidence`, `changelog_entry`, `approval`, and `review` — the tool refuses any other key, so no proposer mints a `commit` or a digest — and the changelog entry stays in the patch preamble, not the ledger. With `--commit`, `land` commits the graph, the patch, and the ledger, then writes the hash into the entry — so the ledger carries one uncommitted line until the next commit; that is the price of recording an identifier the file cannot contain before it exists.

`kind` is a declaration, never a digest inference: a `binding` revision — seat, model, effort, or instrument at a node (`references/pave-spec.md` §2.1) — can move the live digest, because check instruments live in the YAML. The active graph revision is the last `graph` entry; the pinned bundle is the newest `graph` or `binding` entry; a `pin` entry records the first real run of a revision (run id, date) and never moves the digest. Revisions are named by number plus digest, never digest alone — a rollback's `digest_after` equals an older entry's. `plan_evidence` carries what Stage 3 marks `provisional`, so the marker travels with the revision instead of living only in planning prose.

## Rules

- **Draft authority.** During a pave-init run, `workflow.draft.pave.yaml` is the planning and approval subject. Review gates and user approval apply to the draft; builders implement against the approved draft; delivery lands its content as revision 0. The draft is never executed directly against a real target.
- **Land, never edit.** Every change to a live graph is a landing: `scripts/record_revision.py land` takes the `.landing` lock, applies the reviewed patch, validates, digests, appends the entry, and releases. Between landings the live digest equals the ledger head's `digest_after`; `verify` proves it, and distinguishes an unrecorded edit from an interrupted landing (`.landing` present, naming the revision that was landing: restore the root from version control, remove the marker by hand, then `verify` — no command finishes a crashed landing, which is one reason the root is tracked). Whoever is about to run pins the run to a revision number and digest in run state.
- **One instrument.** The pave-evolve seats draft and review every successor — for a generated workflow and for pave-init itself (`skills/pave-evolve/SKILL.md` under the plugin root): a workflow-updater writes the proposal `history/v<N>.patch` — a preamble declaring `kind`, the semantic diff, the envelope check, plan and usage evidence, and a changelog entry, then the unified diff against the head — an update-reviewer passes it, and the lead lands it. A lead never drafts its own graph's successor from a run-filled context, and self-review does not qualify. The why in the semantic diff names the gap the predecessor missed and states why the change holds for runs in general — a successor ships to every future run, so a fix shaped to one run's particulars degrades all the others; a reason that cannot be stated apart from the triggering run is a review finding.
- **Append-only rollback.** The ledger never loses an entry and the head never moves backward. `rollback --to N` reverse-applies the patches down to revision N and lands the result as a new entry with `derived_from: N` and the reason recorded. A rollback is a landing: the lead runs it under rule 6's approval rule, the reason is its semantic diff, and it needs no new review because its content is a revision that already passed one.
- **Honesty.** Record `usage_evidence` as what actually existed at landing: `none` or `clean_room` for a delivery, `field` only for a successor built from real usage. Clean-room forward testing is validation evidence only — it creates no entry.
- **pave-init itself.** pave-init's live graph is `references/pave-init.pave.yaml` in its source repository. It has no evolution root, and its landing is a release: the proposal is a patch against the sources at a path the lead names outside the package, and the build, the tests, and the changelog entry are its ledger. Its head is the source commit the proposal names as its base in the first line of `semantic_diff` (the tool never reads a pave-init preamble); the substitute for `verify` is a clean `git status --porcelain` over `sources/` and `skills/pave-init/` plus `scripts/build_packages.py --check` exit 0 at that commit, and the substitute for `propose` is `git apply --check` in a scratch worktree followed by the build, the tests, and the ratchet. Its landing is the user's decision, always.

## Generated-skill revision record

Every generated workflow that runs more than once ships the root above: `revisions.yaml` with entry 0 beside its canonical YAML, a copy of `scripts/record_revision.py`, the graph edit guard (`hooks/graph_edit_guard.sh`, denying a direct edit of the live graph or the ledger outside a landing) where the harness can run a pre-write hook, and the evolution contract below as one-clause rules with pointers in its lead. A workflow that lives and dies inside one session ships none of it — a ledger nobody reads is state ceremony the simplicity pass deletes — and records that omission once in the enforcement record. A ledger shipped to a one-session workflow, or omitted from a multi-run one, is a review finding, exactly like wrong-sized enforcement.

The planner records two fields in `skill-package-plan.md` at the root dispatch, beside the enforcement record, each with a reason:

- `landing: envelope | user` — whether the lead may land a successor without a user gate when the authority envelope of rule 6 below is unchanged, or whether every landing needs explicit user approval. `envelope` requires the plan to state the envelope it will check.
- `usage_ledger: kept | omitted` — whether the lead writes the rule 8 usage record at each terminal close. `kept` names the successor reader that will act on it; `omitted` names why no usage loop exists.

### Evolution contract

The generated lead carries these rules as one-clause applications; this file is the authority.

1. **Pin and verify.** Before the first real run, `install` the package root into the project's evolution root and append a `pin` entry (run id, date). Every run records its revision number and bundle digest in run state at start and verifies them with `scripts/record_revision.py verify --pinned-revision --pinned-digest` at start and at every resume. `current` continues; `graph landed since pin` routes to rule 7; `binding landed since pin` routes to rule 9. A landing mid-run does not move a running instance by itself.
2. **Declared routes first.** Unexpected evidence that fits a declared `scope_exceeded` or `replan_required` outcome takes that route. Evolution machinery is not a bypass for routes the graph already has.
3. **Block honestly.** When no declared outcome fits: no invented outcome, no invented edge. Pause or block the run, preserve the discovery as evidence with its provenance, and hand it to the seats. An unrecorded edit found by `verify` is the same case: block, record, hand over — never re-digest.
4. **Successor proposal.** Drafted by the workflow-updater from the live graph at the head, replanning only the narrowest affected boundary. Batch by pause: one proposal per pause covering every defect recorded since the last landing, not one per defect.
5. **Review.** The update-reviewer passes the proposal before it lands. Self-review does not qualify, and the lead that lands is never the reviewer.
6. **Authority envelope.** A successor lands without a user gate only when the plan says `landing: envelope` and everything in this envelope is unchanged: root goal and acceptance, allowed and forbidden effects, external access, public parent and sibling interfaces, evidence and check strength (never weaker; rule 9 bounds what counts as weaker when only the instrument changes), state authority, and approved budgets. A change to any of these, or `landing: user`, requires explicit user approval before the landing, recorded verbatim in the entry's `approval`; the envelope check is recorded in `envelope_check` either way.
7. **Continue on the successor.** A run paused on a defect in its own graph continues on the successor once rule 5's review passes and the landing is verified: the lead re-pins the run to the new revision and bundle digest in run state, records the approval that authorized it verbatim, and resumes from its last satisfied gate — landed work stays landed. Where the successor adds a gate that landed work never passed, the run records a backfill duty per landed item and discharges it before those items are promoted; it never marks the new gate passed for them. Only a successor that changes what a recorded outcome meant — the acceptance a landed item passed, an effect the run already had — closes the run honestly and starts a linked successor run pinned to the new revision, carrying its predecessor's run identity and evidence. A run that declines to move records the decline in run state; `verify` treats a recorded decline as satisfied and does not re-ask at every resume.
8. **Usage ledger.** When the plan says `usage_ledger: kept`: at each run's terminal close, the lead derives a usage record from the run's event history — per boundary: traversals, seats dispatched, each seat's outcome distribution and wall-clock over those traversals, and any seat whose question was already settled by verified evidence at dispatch time — appended to one standing usage ledger beside the run's evidence, one short section per run close, never a new file per run (the document budget, `references/pave-spec.md` §8.4); the generated skill's state or layout reference names it. A successor proposal reads its predecessors' usage records before replanning — a seat whose priced judgment never fired across a run's traversals is a lead-run-instrument candidate — and its semantic diff states what they changed or why they changed nothing; a proposal silent on recorded field evidence is a finding at rule 5's review. Actuals exceeding the plan's declared expectations — loop bounds, or the approved budgets already in the authority envelope — are a successor trigger surfaced at close: the ledger adds no mid-run gate, and a bound the graph itself enforces still routes mid-run per rule 2.
9. **Binding revisions.** Runtime-binding changes — seat, model, effort, or instrument at any entry (`references/pave-spec.md` §2.1) — do not change graph meaning and land as `kind: binding` entries in the same ledger with a user-approved envelope check; a run pinned to the older entry re-pins to the new digest at its next resume. The change's node scope is read from run state's recorded traversals, never from a premise about which stage ran. Same check, cheaper instrument, mechanical evidence still produced is not a weakening of check strength; dropping a check is.

An active graph is never edited in place. The pave-evolve seats draft and review every successor; only who lands differs — the generated lead within its envelope, the user-approved landing outside it, or a pave-init release for pave-init itself.
