# Apply spec: re-entry economics, evolution capture, alignment hooks

> **APPLIED 2026-08-28** — Parts A + H landed in the pave repo; rebuilt with
> `build_packages.py --force` (generated SKILL.md, agents/*.md, codex TOMLs
> current); hooks test 23/23 PASS; `VERSION` and both `plugin.json` bumped
> 2.2.9 → 2.3.0; hooks.json restored (step 0).
>
> **Part C APPLIED 2026-08-28 (later same day)** after its wording review
> round (fable adversarial-investigator): verdict REVISE with 5 HIGH
> integration findings, all verified byte-exact on disk, all fixed by the
> reviewer's final-form text, which is what shipped — NOT the drafted C1/C2
> below. Deltas from the draft: (F1) "seven-rule" count-coupling dropped at
> SKILL.md.tmpl + skill-builder.md.tmpl; (F2) the binding record got a real
> mechanism — append-only `binding-revisions.yaml` beside the manifest,
> added to the workspace structure (the manifest itself is rewritten
> wholesale by freeze_revision.py); (F3) rule 6's "(never weaker)" edited to
> point at rule 9's bound, not append-only; (F4) mid-run scope fixed — the
> ledger adds no mid-run gate but graph-enforced bounds still route mid-run
> per rule 2, and "loop bounds" no longer misattributed to the envelope;
> (F5) enforcement anchored — the semantic diff must engage usage records,
> checkable at rule 5's review; (F7) "dispatch instrument" renamed
> "re-entry instrument" with inline gloss. Rebuilt, seven-rule sweep CLEAN,
> hooks test 23/23, BUILD CURRENT. Read the Part C section below as the
> pre-review draft; the shipped text is in `pave-revisions.md` rules 8–9.
>
> Part A/H apply-time deviations, all recorded here:
> 1. A2 placed after the §2.1 "What evidence a decision requires?" line, not
>    after the "How many workers" line — the spec's slot would have broken
>    the "The last pair matters most" referent that follows the list.
> 2. A8's companion was merged into explore-and-plan.md's EXISTING
>    "clean-room forward-test prompt" contents item (the spec's INSERT would
>    have duplicated it).
> 3. A 7th "stronger rung" restatement site missed by the lockstep list —
>    the §11 Enforcement card question at pave-spec.md:1268 — got the
>    companion line "For a rung with standing cost, why is the next cheaper
>    rung insufficient?" under the assessor's all-sites-lockstep rule.
> 4. H3's paragraph placed after the rewritten active/latent paragraph
>    (H2), not directly after the Three-uses list — latent rules must be
>    defined before "latent-rule reinjection" uses the term.

Provenance: GLM-5.3-Flash parity run investigation (run-20260827), 2026-08-28.
Adversarially reviewed: 4 assessor rounds (fable adversarial-investigator) + harness
verification (claude-code-guide, doc-cited). Status per part:

- Part A (9 edits) — REVIEWED, apply as written.
- Part H (5 edits, one file) — REVIEWED, apply as written. H-fix1/H-fix2 are
  mandatory defect fixes in existing text (dead PreCompact channel).
- Part C (2 edits) — claims verified, WORDING NOT YET REVIEWED. Run one assessor
  round on the text below before applying.

Build rule: `agents/*.md`, `skills/pave-init/SKILL.md`, and `codex/agents/*.toml`
are GENERATED. Edit `sources/roles/*.md.tmpl` and `sources/pave-init/SKILL.md.tmpl`,
then rebuild with `python3 scripts/build_packages.py`. Files under
`skills/pave-init/references/` and `skills/pave-init/orchestration/` are edited
directly. Phrase-coupling checked: no script parses any touched phrase.

Anchor status: every OLD string below was verified byte-exact on disk
2026-08-28 (confirmed lines: pave-spec.md:260/:97/:811-812/:1019-1021;
SKILL.md.tmpl:119/:177; node-planner.md.tmpl:31/:43;
pave-material-reviewer.md.tmpl:22/:57/:73/:93; explore-and-plan.md:127/:165;
review-and-build.md:128; lead-alignment-hooks.md:22/:30/:33/:39).
Caution: pave-spec.md prose is hard-wrapped — OLD blocks there span line
breaks, so match them flattened, not with single-line grep.

---

## Part A — re-entry economics (reviewed)

### A1. Price loops, not the first pass
File: `skills/pave-init/references/pave-spec.md` §4.11 (~line 260)

OLD:
```
- Compare the cost of the added structure with the risk it removes.
```
NEW:
```
- Compare the cost of the added structure with the risk it removes — priced
  over the traversals its loops and re-entry edges can carry, not the first
  pass alone.
```

### A2. Instrument is a binding question, chosen per traversal
File: `skills/pave-init/references/pave-spec.md` §2.1, in the layer-question
list, after `- How many workers run one node? Runtime Binding.`

INSERT:
```
- Which instrument answers a node on re-entry — full seat, cheaper seat, or a
  lead-run mechanical check? Runtime Binding: declared at design time, chosen
  per traversal.
```

### A3. The delta question, as a repair-record duty
File: `skills/pave-init/references/pave-spec.md` §9.8 (~lines 811-812)

OLD:
```
Record each repair: the finding, the change, the evidence the change
invalidated, and the result.
```
NEW:
```
Record each repair: the finding, the change, the evidence the change
invalidated, and the result — and the evidence it did not invalidate: what
stays settled is the cheap path for every node the repair re-enters. Scale
the re-entered node's instrument to that record: an unchanged,
already-verified fact settles mechanically; a changed input to a judgment
gets fresh eyes.
```

### A4. Root cause: repair edges land at the resolving node
File: `skills/pave-init/references/pave-spec.md` §9.8, immediately after the
first routing code block (the `Insufficient evidence -> Explore` table).

INSERT:
```
The same rule sizes the landing: land a repair edge at the node that resolves
the finding, not at the boundary entrance — an entry-point landing
re-traverses siblings whose inputs the repair never touched.
```

### A5. "Why not cheaper" — 6-site lockstep (spec + 5 restatements)
All six change in ONE pass or the record stays one-directional.

A5.1 `skills/pave-init/references/pave-spec.md` §9.14.1 (~lines 1019-1021)
OLD:
```
For every run-wide prohibition and every guard on a costly transition,
record two things: the strength chosen, and the reason a stronger rung is
unnecessary.
```
NEW:
```
For every run-wide prohibition and every guard on a costly transition,
record two things: the strength chosen, and the reason the neighboring rungs
are wrong — the stronger unnecessary, and, where the chosen rung carries
standing cost (a dispatched agent, a repeated run, an always-on control),
the cheaper insufficient to catch the defect it names.
```

A5.2 `sources/pave-init/SKILL.md.tmpl`, Stage 3 enforcement-record sentence
OLD: `names its strength and why a stronger rung is unnecessary`
NEW: `names its strength and why the neighboring rungs are wrong — stronger
unnecessary; for a rung with standing cost, cheaper insufficient`

A5.3 `sources/roles/node-planner.md.tmpl`, step 4
OLD: `with the reason a stronger rung is unnecessary;`
NEW: `with the reason a stronger rung is unnecessary and — for a rung with
standing cost — a cheaper one insufficient;`

A5.4 `sources/roles/pave-material-reviewer.md.tmpl`, Enforcement review, first
sentence (line 73)
OLD: `names its strength and the reason a stronger rung is unnecessary, and every node`
NEW: `names its strength and the reason the neighboring rungs are wrong —
stronger unnecessary; for a rung with standing cost, cheaper insufficient —
and every node`

A5.5 `skills/pave-init/orchestration/explore-and-plan.md` (~line 127)
OLD: `has a recorded enforcement strength with a reason a stronger rung is unnecessary.`
NEW: `has a recorded enforcement strength with a reason a stronger rung is
unnecessary and — for a rung with standing cost — a cheaper one insufficient.`

A5.6 `skills/pave-init/orchestration/explore-and-plan.md` (~line 165,
skill-package-plan contents list)
OLD: `with its chosen strength, the reason a stronger rung is unnecessary, and — for each planned hook —`
NEW: `with its chosen strength, the reason a stronger rung is unnecessary
(and, for a rung with standing cost, a cheaper one insufficient), and — for
each planned hook —`

### A6. Reviewer: cost is material and can reach HIGH
File: `sources/roles/pave-material-reviewer.md.tmpl` (three sub-edits; rebuild after)

A6.1 Materiality rule — after `a finding is material only if it prevents or
materially impairs that goal.` INSERT:
```
The run's operating budget is part of that goal: structure whose recurring
cost is out of proportion to the risk it retires — a re-check re-run at full
strength when its declared inputs cannot have changed, a loop priced only at
its first pass — materially impairs the goal. Judge it per check, not as a
portfolio: weigh the chance the check's inputs changed times the cost of a
defect slipping past it, against the cost of running the check. The answer
flips per check — when inputs cannot have changed, the cheapest sufficient
instrument wins outright; when a change is credible and a miss is costly,
full strength is justified outright. There is no middle setting to average
into: a seat sized to the cheap half of a mixed check is the worst of both —
a check that bundles a mechanical half with a judgment half gets split into
two checks, never averaged into one medium seat.
```

A6.2 Severity list
OLD:
```
- `HIGH`: A credible defect can materially change routing, authority, evidence, acceptance, or generated-skill behavior.
```
NEW:
```
- `HIGH`: A credible defect can materially change routing, authority, evidence, acceptance, generated-skill behavior, or the run's cost out of proportion to the risk retired.
```

A6.3 Planner-return checklist, the `Are the children routed:` bullet — append
before its closing `?`:
```
— and does every re-entry edge answer the delta question: what could have
changed since the target node last ran, and what is the cheap path when
nothing did
```

### A7. Planner: effort pins state re-entry behavior
File: `sources/roles/node-planner.md.tmpl`, "Model and effort", last paragraph

OLD:
```
Assignments are per role, and the builder realizes them through the active harness's role configuration; inside a script binding they apply to each node invocation. A role that genuinely needs two tiers on different nodes is two roles.
```
NEW:
```
Assignments are per role, and the builder realizes them through the active harness's role configuration; inside a script binding they apply to each node invocation. A role that genuinely needs two tiers on different nodes is two roles. An effort assignment describes first-entry workload unless it says otherwise: a node any re-entry edge targets also states its re-entry instrument — what settles the node when the repair record shows its inputs unchanged, down to a lead-run mechanical check — so re-entries are never silently priced at first-entry effort.
```

### A8. Forward test exercises a repair edge, or records the gap
File: `skills/pave-init/orchestration/review-and-build.md` §6, new paragraph
after the `Do not tell the tester the expected answer…` paragraph:
```
When the graph declares repair loops, prefer a representative prompt that
forces at least one bounded repair edge — a happy-path-only pass never prices
the loop traffic. When no affordable prompt reaches one, record loop traffic
as untested in `reviews/forward-test.md`: a recorded gap, never a silent
clean pass.
```
Companion (prompt is frozen at Stage 4, so the duty must land where the plan
is authored): `skills/pave-init/orchestration/explore-and-plan.md`
skill-package-plan contents list (~line 162), INSERT item:
```
- the forward-test representative prompt, forcing at least one bounded repair
  edge when affordable;
```

### A9. Generated leads inherit the dispatch rule
File: `sources/pave-init/SKILL.md.tmpl`, Stage 5 "Map graph elements" list,
after `- Nodes map to lead stages or role procedures. Several nodes may use one role.`

INSERT:
```
- Write the dispatch rule into every generated lead: traversal is mandatory,
  dispatch is not. A re-check whose outcome is mechanically knowable from
  already-verified on-disk evidence is settled by the lead directly, with the
  outcome and declared edge still recorded and the record carrying the
  check's command and output — never the lead's bare conclusion. Fresh seats
  are reserved for genuinely new work or judgment; a check the graph forbids
  the lead to judge (author/evaluator separation) keeps its seat. Phrase the
  generated role-work prohibition to carve this out: mechanical re-check
  settlement is lead routing work, not role work.
```

---

## Part H — lead-alignment-hooks.md (reviewed; one file)

File for all five: `skills/pave-init/references/lead-alignment-hooks.md`

### H-fix1 (MANDATORY — dead channel). "Why this pair exists" section
OLD:
```
Role reinjection on user events (UserPromptSubmit, PreCompact, SessionStart) covers most of a run.
```
NEW:
```
Role reinjection on user events (UserPromptSubmit, SessionStart — including its compact matcher; PreCompact cannot inject, its output is discarded) covers most of a run.
```

### H-fix2 (MANDATORY). Scoping mechanism 2
OLD:
```
2. **Lead-only events.** User prompt submit, compaction, and session start fire only in the main session. Bind role reinjection there.
```
NEW:
```
2. **Lead-only events.** User prompt submit and session start — including SessionStart's compact matcher, the only event that can inject content after compaction — fire only in the main session. Bind role reinjection there.
```

### H1. Role-reinjection payload carries standing directives + the cap fact
"Three uses" item 2 —
OLD:
```
2. **Role reinjection.** A hook re-injects the lead contract and the current node on a schedule — for example, on user-prompt submit or after compaction — so the orchestrator keeps orchestrating: route, dispatch, verify, and never do worker tasks itself.
```
NEW:
```
2. **Role reinjection.** A hook re-injects the lead contract, the current node, and any standing user directives the run's state records on a schedule — for example, on user-prompt submit or after compaction (SessionStart, compact matcher) — so the orchestrator keeps orchestrating: route, dispatch, verify, and never do worker tasks itself. Size the payload against what actually decays: conversation content and prior hook injections are summarized away, and native skill-body re-injection is capped per skill (~5k tokens), so a generated lead longer than the cap loses its tail sections first.
```

### H2. Active/latent reconciliation (unblocks worker reinjection)
OLD:
```
A worker whose own role contract decays within its node is mis-sized work — a node-sizing finding (`pave-spec.md` §9.12), not a reinjection target.
```
NEW:
```
A worker whose active duty — its own definition of done — decays within its node is mis-sized work: a node-sizing finding (`pave-spec.md` §9.12), not a reinjection target. Its latent standing rules — prohibitions orthogonal to the work in front of it — are different: compaction preserves active focus and summarizes away latent rules and prior hook injections alike. Target reminder machinery by latent-rule count times blast radius, never by wall-clock length: a long single-focus builder needs none; a seat carrying several rarely-exercised prohibitions over irreversible surfaces qualifies.
```

### H3. Two derived applications (one new paragraph, after the "Three uses" list)
INSERT:
```
Two derived applications, both plan-time options recorded in the enforcement
record — one entry naming prose and reinjection as rungs of the same rule
(persistence after decay, not a second gate):

1. **Dispatch-time check.** A `PreToolUse` hook on the agent-spawn tool,
   advisory only and edge-triggered: it fires only when run state already
   records a completed traversal of the target node (a re-entry dispatch) and
   asks whether the seat's question is already settled by verified on-disk
   evidence. Every-spawn firing is wallpaper. The advisory must ride
   `additionalContext` — a reason attached to an allow decision reaches the
   user, not the model — and any throttle is the hook's own counter file.
2. **Latent-rule reinjection for rule-heavy seats.** Re-present a seat's
   standing prohibitions when it touches the matching tool class, throttled
   per window — tool events fire inside subagents, so this survives seat-side
   compaction no post-compaction event covers. Where the harness cannot gate
   on seat identity, ship run-wide tool-class reminders armed by the run
   marker and record the narrowing as a degradation.
```

---

## Part C — evolution capture (DRAFT — one assessor round before applying)

File: `skills/pave-init/references/pave-revisions.md`, evolving-tier contract
(the generated lead copies this contract, so it propagates at next generation).

### C1. Usage ledger + successor read duty + non-blockage trigger
APPEND after rule 7:
```
8. **Usage ledger.** At each run's terminal close, the lead derives a usage
   record from the run's event history — per boundary: traversals, seats
   dispatched, and any seat whose question was already settled by verified
   evidence at dispatch time — stored beside the run's evidence. A successor
   draft reads its predecessors' usage records before replanning; ignoring
   recorded field evidence is a review finding. Actuals exceeding the plan's
   declared expectations (loop bounds, approved budgets — already in the
   authority envelope) are a successor trigger surfaced at close, never a
   mid-run block.
```

### C2. Binding revision lane
APPEND after C1's rule 8:
```
9. **Binding revisions.** Runtime-binding changes — seat, model, effort,
   dispatch instrument — do not change graph meaning and may ship as a
   recorded, user-approved binding revision in the lineage without a graph
   successor, provided the envelope holds. Same check, cheaper instrument,
   mechanical evidence still produced is not a weakening of check strength;
   dropping a check is.
```

---

## Apply procedure

0. Housekeeping: `generated-plugins/vllm-neuron-parity/hooks/hooks.json` —
   RESOLVED 2026-08-29: the deletion is intentional (user decision, recorded
   here per the "restore or record why" rule); an earlier apply-time restore
   of it was reverted and the deletion is committed with this change set.
1. Apply Part A + Part H (Part C only after its review round).
2. Rebuild generated files: `python3 scripts/build_packages.py`
   (regenerates `agents/*.md`, `skills/pave-init/SKILL.md`, `codex/agents/*.toml`).
3. Validate: run the repo test suite (`pytest skills/pave-init/tests` or repo
   equivalent) and the `skill-creator:skill-creator` quick validator against
   `skills/pave-init`; confirm no stale `stronger rung is unnecessary`-only
   phrasing remains at the 6 lockstep sites
   (`grep -rn "stronger rung is unnecessary" sources/ skills/ | grep -v cheaper`).
4. Bump `skills/pave-init/VERSION` (2.2.9 → 2.3.0) and note the change set.
5. Ships to FUTURE generations only — existing generated plugins keep their
   pinned copies; vllm-neuron-parity picks these up via a pave-init update run
   or its own successor revision.

## Deliberately NOT included (reviewed and rejected/dropped)

- H1 blocking variant (block-once dispatch gate): REJECTED — predicate not
  precisely detectable; PreToolUse has an advisory channel, so the stop-guard
  block-budget justification does not transfer.
- Governance ratio with a declared multiple: dropped — ungroundable constant;
  §4.11 (as rewritten) carries the qualitative rule.
- Seat-side post-compaction hook: dropped — undocumented channel; H3's
  tool-event reinjection covers the need compaction-immune.
- "Expected traversals" as expectation: renamed to bound-priced (§9.8 bounds
  are worst-case counters).
- Waste metric "output changed no downstream decision": corrected to "question
  already settled by verified evidence at dispatch time" (C1) — the original
  counts passing guards as waste.
