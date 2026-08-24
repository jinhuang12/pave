# Approval briefs and delivery docs

Read this at every user approval gate, and at Stage 5/6 when rendering the generated skill's `README.md` and `VERSION`. It defines the rendered documents a human decides from: their sections, their derivation, where they persist, and who checks them for drift.

## Why briefs exist

An approval gate that points the user at five raw artifacts — a formal YAML graph, a traceability table, a package plan — is not reviewable by a human in conversation. The user either approves without examination or stalls. Every approval gate therefore renders one brief: a compact document with a fixed section order, written for the person deciding, not for the validator.

## The rendered-view rule

A brief (and the delivered `README.md`) is a **rendered view, never an authority**. It is derived from the approval bundle; the bundle stays the source of truth. Three duties follow:

- The brief states nothing the underlying artifacts do not support. No new policy. No requirement that appears only in the brief.
- Every section links the raw artifact it renders, so the user can drill down.
- Drift is a review finding: the gate's material reviewer verifies the brief against the bundle. A brief claim the bundle does not support is a defect with the same severity logic as any other. The user approves knowing an adversary checked the summary they read.

## Write for the reader

Every document a human reviews or approves from — the briefs, the delivery report, the generated `README.md` — follows one rule: simple, plain English, as concise as the content allows. Short sentences. Common words. Define a technical term at first use in a few words, or do not use it. Lead each section with the point; detail follows for readers who want it. The reader decides from this document: prose they cannot grasp produces rubber-stamp approvals, which is the failure briefs exist to prevent. Concise never means incomplete — cut words, not material facts. pave-init's own `README.md` follows the same rule — it is the same kind of document.

## Stage 1: requirements brief

Rendered after the fitness verdict, before the approval question. Persist to `reviews/requirements-brief.md`, then render it **in full in the conversation** — the user must never have to open a file to review. Sections, in order:

1. **Goal** — the frozen goal statement, verbatim.
2. **What this approval covers** — one paragraph: approving here freezes the goal, the requirements, and the fitness verdict; later stages serve them.
3. **Requirements summary** — the material `USER REQUIREMENT`s and load-bearing `ASSUMPTION`s as a short table; link `requirements.md` for the full record.
4. **Fitness verdict** — verdict plus the two or three characteristics that decided it; for `not_fit`, the simpler alternative and the override's accepted risks.
5. **Open questions and gaps** — what stays unresolved into exploration.

No reviewer exists yet at this gate, so the lead self-checks the brief against `requirements.md`. The Stage 4 reviewer sees both later, and stale Stage 1 claims surface there.

## Stage 4: plan approval brief

Rendered when the approval bundle is assembled, and submitted **with** the bundle to the whole-bundle review round — the reviewer verifies the brief as part of the gate, so the summary the user reads has survived the same adversary as the plan. Persist to `reviews/plan-brief.md`, then render in full in the conversation after review passes.

Compact and scaled to the graph: target one to three pages. Sections, in order:

1. **Intro** — the goal, the fitness verdict, and two or three sentences on what the generated workflow does end to end. State plainly what approving here authorizes: building this package against this graph.
2. **Workflow summary and visual** — first, one small at-a-glance diagram: a Mermaid `flowchart LR` of the happy path as a handful of plain-language stage groupings (labels a stranger can read, stated explicitly as stage groupings and **not** graph node ids), so the reader holds the workflow's shape before any detail; loops and recovery routes stay out of it. Then one faithful Mermaid `flowchart TD` per profile, rendered from the draft YAML (see conventions below). A composed plan gets the root diagram first, then one per child profile.
3. **File structure** — the planned package tree from `skill-package-plan.md`, one line per file with a short purpose comment.
4. **Specialized agents** — one table: agent, role, key constraint (what it cannot do). Include the lead's own row.
5. **Hooks and enforcement** — one table rendered from the enforcement record: rule, rung (observing / reinjection / blocking), why that rung. Recorded omissions (for example a lead-alignment-pair omission condition) appear here, never silently.
6. **Tradeoffs and open decisions** — what the plan chose and what it gave up; evidence gaps carried forward; anything the user is accepting rather than approving.
7. **Appendix** — links to every raw bundle artifact (`requirements.md`, `system-map.md`, `workflow.draft.pave.yaml`, `traceability.md`, `skill-package-plan.md`) with one line each on what it is.

### Mermaid conventions

Render from the draft YAML mechanically — nodes and edges the graph does not declare must not appear:

- `flowchart TD`; one diagram per profile, never merged across a composition boundary.
- Graph nodes as rectangles labeled with the node id; user gates as `{{...}}` hexagons; terminal endpoints as `([...])` stadiums.
- Edges labeled with the outcome code that traverses them.
- Keep one diagram readable: past roughly 25 nodes, split by stage into linked sub-diagrams rather than shrinking the labels.

## Collecting the approval

The brief never replaces explicit approval. After rendering it in full, ask one bounded approval question through the active harness mechanism. Its approval option names exactly what is approved (the Stage 1 artifacts and verdict; the complete Stage 4 bundle). Record the response verbatim at the gate's declared path (`run-state.json` fields for Stage 1; `reviews/user-plan-approval.md` for Stage 4). A request for changes returns to the narrowest affected node; re-render the brief after repair instead of patching it by hand.

## Delivered docs: `README.md` and `VERSION`

Every generated plugin ships two rendered documents at its package root, written by the lead at integration (Stage 5), verified at the final-skill gate (Stage 6):

- **`README.md`** — a deep-dive rendered from the approved bundle, in the same section order as the plan brief: intro (what the workflow does and for whom, plus the active harness's exact installation steps), workflow summary and visual (the at-a-glance stage diagram, then the faithful Mermaid rendered under the Mermaid conventions above), file structure (the shipped tree), specialized agents table, hooks and enforcement table, and an appendix pointing at the canonical YAML, schemas, and references. It is the plan brief grown into permanent documentation — reuse the approved brief as the starting point and update it to match what was actually built.
- **`VERSION`** — the generated skill's package changelog, seeded at delivery with `version: 1.0.0` and one entry summarizing the initial capability. Update runs append entries and bump the version. Package versions are separate from the workflow's `v0`/`v1+` revision machinery: revisions track graph freezes; `VERSION` tracks what a user of the skill would notice changed.

Both are rendered views under the same rule: the canonical YAML and the skill's own contracts stay the authority, and the README restates no policy as new authority. The Stage 6 final reviewer checks both against the shipped package — README/VERSION drift is a finding. A README derived from the bundle, drift-checked at the gate, and re-rendered by update runs, is owned prose; unowned prose duplicates policy and rots, which is why a free-written README stays prohibited.

## Generated skills: the conditional default

A generated workflow with its own user approval gate over a multi-artifact bundle inherits the brief pattern: render a brief per this reference's section order (adapted to that workflow's artifacts), persist it at a declared path, present it in full, then collect explicit approval. Keyed to a structural property like every conditional default: the gate must cover more than one artifact, or one artifact a human cannot review raw (a formal schema, a generated config). Recorded omission conditions — a gate over a single human-readable artifact, or a workflow with no user gates — go in the enforcement record. Importing the pattern where an omission condition holds is over-ceremony and a review finding, same as omitting it silently where it applies.
