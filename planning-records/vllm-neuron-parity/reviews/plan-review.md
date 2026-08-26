# Plan review record — vllm-neuron-parity

Retained reviewer: teammate `plan-reviewer` (pave-init:pave-material-reviewer),
spawned 2026-08-25 at the first closed boundary per review-and-build §1.
Continued across all rounds; lead mediates between reviewer and node planners.

## Round 1 — ROOT boundary — 2026-08-25 — VERDICT: REVISE

Subject: `planning/root.v3.draft.pave.yaml` against frozen
`planning/root-contract.md` (2026-08-25 amendment) + system-map.md +
requirements.md. Reviewer ran 5 research workers; every finding carries
path:line evidence verified first-hand; cycle set cross-checked mechanically
(networkx.simple_cycles).

### Findings and lead disposition

| # | Sev | Finding (location) | Disposition |
|---|-----|--------------------|-------------|
| 1 | HIGH | Route B (pin upgrade) costed but not executable: regression-matrix gate exists only in prose (scope.in:27, cost node purpose:271); `adjudicate_results`/`review_measurement_verdict` hard-wired to correctness+perf gates only; both predicted atomic so no child supplies the path; `pinned_release` single-pin state + `prepare_pr` "clean diff against pinned release branch" cannot represent an upgrade campaign. Reachable via gate-1 election AND `rederive_approach.revised_approach`. | FIX, option (a): route-B acceptance variant in `adjudicate_results.purpose`; regression-matrix consequence in `review_measurement_verdict` outcomes; `design_campaign` owns matrix assembly (new construction per system-map:225-228); per-campaign target-pin state field. Option (b) = scope cut needing a user decision — rejected, route B is in the approved goal verbatim. |
| 2 | HIGH | Conflict-aware scheduling declared in scope, no owner: predicted file surface produced twice (cost:272-273, design:325-326), consumed by nobody; fan-out edge carries only the gate-1 check; no scheduling state field; lease is per-host not campaign-level. Upgrade-exclusivity leg compounds finding 1. | FIX: lead scheduling authority in `roles.lead.purpose`; one state field (predicted surfaces + serialization holds); hold condition on fan-out edge or entry guard on `implement_increments`. One mechanism, both legs. |
| 3 | HIGH | Gate 2 has no owner: `gate_approval_record.produced_by` lists `review_campaign_design`, whose only role is `adversarial_reviewer` (no lead/user), purpose disclaims presentation, neither outcome requires the record. Violates contract:113-115 (exactly 3 user gates) + :174 (lead records approvals verbatim; artifact existence ≠ approval). | FIX, cheapest form per §4.11: add `lead`+`user` to `review_campaign_design.roles`; add present-gate-2-and-record-verbatim clause to purpose; require `gate_approval_record` in `design_sound.required_evidence`. |
| 4 | HIGH | Four review-repair loops unbounded (neither counter nor operator-controlled stop, pave-spec:804-809): impl⇄review (670/682), adjudicate⇄verdict-review (708/723), pr⇄pr-review (726/735), and pre-gate-1 cost⇄route-review (636/645). loop_bounds:945-948 claims routing to `rederive_approach` that no edge from those producers declares. None spends hardware attempts → tier-2 breaker never counts them. P-EAGLE precedent: ~8 re-entries real. | FIX: declare what the ledger already assumes — no-progress/repeated-identical-findings outcome on each of the four producing nodes with an edge to `rederive_approach`; pre-kickoff loop gets an equivalent stop to the user or `run_paused`. No attempt caps introduced (contract:178 intact — `rederive_approach` lands on operator-controlled gates). |
| 5 | LOW | Re-baseline follow-up "scorecard rows re-verified against the new pin, never carried forward as passed" (requirements:72-76 UR) absent from contract and graph (`verify_run_preconditions`:227-228 says only "loaded"). | FIX: one clause in `verify_run_preconditions.purpose`. |
| 6 | LOW | `close_campaign.consumes` mismatch (lead-flagged, reviewer confirmed not a deadlock — consumes is declarative): infeasible-design and lost-hardware paths supply none of the four consumed items; `infeasible_at_pin` promises evidence but declares no `required_evidence`. | FIX: add `campaign_design_record` to `close_campaign.consumes`; give `infeasible_at_pin` a `required_evidence`. |
| 7 | LOW | (a) Baseline-skew control (GPU-side vLLM version + known cross-version differences in the evidence bundle, requirements:118-121 UR — codified remedy for the wrong-baseline incident) named nowhere; (b) `evidence_gameability` ledger covers 6/16 evidence items, omitting `run_closure_verdict`. | FIX both: name skew control in `measurement_artifacts.subject`; extend gameability ledger to at least `run_closure_verdict` (complete remainder in Stage-3 enforcement record). |

### Rejected hypotheses (reviewer checked, disproved — do not re-litigate)

Pin-generic text violated; sizing rationales unfalsifiable (all 5 checked
against primary evidence); a 4th user gate / measurer adjudicating; completion
fails open; `await_remaining_closures` deadlock; debt-ledger absorption missing
(covered by per-target delta scan).

### Residual risks (nonblocking, carried to assembly)

1. Terminal semantics: all-campaigns-blocked run satisfies `run_closed_complete`
   → `run_complete`/`accepted`. Faithful to contract:134-137 — Stage-1
   territory if the user objects, not a graph defect. Surface to user at the
   plan-approval gate brief.
2. Join continuation: `await_remaining_closures` needs the conventional
   `resume_at: verify_run_closure` at assembly (field is convention, not schema).
3. Forbidden effect #6 (benchmark provisioning STOP) rides on role text +
   scope.out only — enforcement record must pin it.
4. `close_campaign` is instance_per yet writes all four cross-run single-writer
   artifacts — artifact-layout reference must settle write ownership/precedence.
5. "deepseek carry-over" baked into reusable check text :179 — contract-mandated
   now; flag for the evolving-tier revision path.

### Round-1 routing

`review_boundary.boundary_revision_required` → repair round to the SAME
retained planner (teammate root-planner) on fresh path
`planning/root.v4.draft.pave.yaml` (no-reused-paths rule). Reviewer standing by
on its thread for re-verification against these findings.

## Round 2 — ROOT boundary (v4 re-verification) — 2026-08-25 — VERDICT: REVISE

Subject: `planning/root.v4.draft.pave.yaml`, diffed line-by-line against v3 by
the reviewer. Scope check PASSES: 47 edges = 42 + the 5 new finding-4/1 edges;
two undeclared touches (`rederive_approach` purpose + consumes) judged
in-service-of-finding-4, not drift; pin-genericity survives; no new sibling
routing ambiguity.

Round-1 closure: findings 2, 3, 5, 6, 7 fully CLOSED on disk (reviewer also
confirmed the gate-2 record+check pairing is correct enforcement, not §4.11
double-gating). Findings 1 and 4 partially closed — residues below.

### Findings and lead disposition

| # | Sev | Finding (location in v4) | Disposition |
|---|-----|--------------------------|-------------|
| A | HIGH | Route-B fix closed the middle, not the ends. (i) No success outcome for an upgrade campaign: `pass_confirmed`:554-556 still "Both gates passed"; `correctness_shortfall_confirmed`/`no_benefit_confirmed` equally backport-only; `prepare_pr` reachable only from `pass_confirmed` — a PASSING upgrade campaign has no route to a PR. (ii) Gate-1 completeness (`kickoff_contract_approved`:181-185, `assemble_kickoff_contracts.purpose`:322-325) never requires the upgrade base or the regression-matrix criteria that `adjudicate_results`:526 and `prepare_pr`:579-582/`campaign_target_pins` now read — consumers with no producer/gate. | FIX: (i) generalize `pass_confirmed.meaning` to "every kickoff-declared acceptance verdict for this campaign's route passed"; (ii) add upgrade base + regression-matrix criteria to the check question and the assemble purpose. |
| B | HIGH | Design loop still unbounded: `design_campaign` outcomes remain only `design_ready`/`infeasible_at_pin`; rewritten loop_bounds:1027-1029 claims a gate-2 exit that is unreachable while looping (gate 2 presents only on `design_sound`, which requires no material finding standing). Same failure mode as round-1 finding 4; correction already applied 3× elsewhere. | FIX: add `no_progress` to `design_campaign.outcomes` edged to `rederive_approach`; amend the loop_bounds bullet to name that edge. |
| C | LOW | The three new no-progress detectors don't consume `adversarial_review_findings` — the findings history their outcome definitions compare (impl:391, adjudicate:529, prepare_pr:585). Declarative only, but it is finding B's input side. | FIX: add `adversarial_review_findings` to those three consumes lists. |
| D | LOW | Design-time refined file surface never updates `scheduling_holds` — sole consumer check sits on the fan-out edge, evaluated before any design exists. Backlog-level prediction catches the common case; lead role carries standing authority. | FIX: state in `scheduling_holds` meaning (and design_campaign purpose) that holds are re-derived when a design refines the surface. |
| E | LOW | `run_paused.meaning`:829-831 doesn't cover the new `stalled_costing_to_pause` inbound (stalled intake loop). Endpoint text drift; classification honest. | FIX: extend the meaning. |

### Round-2 routing

`review_boundary.boundary_revision_required` → repair round 2 to retained
root-planner on fresh path `planning/root.v5.draft.pave.yaml`; v4 kept as
provenance. Reviewer re-verifies A–E only.

## Round 3 — ROOT boundary (v5 re-verification) — 2026-08-25 — VERDICT: REVISE

Subject: `planning/root.v5.draft.pave.yaml`. A(ii), B, C, D, E CLOSED on disk;
declared touch-up judged correct and in scope; counts (18/48/5) and
pin-genericity re-confirmed; scope clean. Reviewer explicitly rejected adding a
per-refinement mechanical check for D as over-enforcement.

### Finding and lead disposition

| # | Sev | Finding (location in v5) | Disposition |
|---|-----|--------------------------|-------------|
| F | HIGH | v5 asserts two acceptance scopes for upgrade routes: `adjudicate_results.purpose`:533-535 route-scopes the two gates (backport) + matrix (upgrade), but the new `pass_confirmed.meaning`:564-567 demands correctness + perf + matrix for upgrade routes; `correctness_shortfall_confirmed` and `no_benefit_confirmed` are reachable for upgrade routes, whose no-benefit scorecard cell (`measured-not-viable-at-pin`) is meaningless for a campaign that exists to leave the pin. The approved UR (requirements.md:178-179) says upgrade routes gate on the regression matrix INSTEAD — the reading v5 added is the one the UR excludes. | FIX, reviewer option 1: route-scope `pass_confirmed` exactly as `adjudicate_results.purpose` already is, and route-scope `correctness_shortfall_confirmed` + `no_benefit_confirmed` so neither is reachable for an upgrade route. Option 2 (all three gates for upgrades) would widen approved acceptance criteria — a Stage-1 user decision; not taken because the UR's "instead" already settles the question. Lead will surface the settled reading to the user in the plan brief. |

### Residual risks (reviewer's running list)

6. (NEW, round 3) All four repair-detector nodes consume
   `adversarial_review_findings`, which does not exist on their first lap —
   benign (consumes is declarative, never a runtime precondition), but the
   BUILD STAGE must be told, or a builder may treat it as a first-lap blocker.
   Carry into skill-package-plan.md / builder briefs.

Residuals 1–5 stand unchanged.

### Round-3 routing

`review_boundary.boundary_revision_required` → repair round 3 to retained
root-planner on fresh path `planning/root.v6.draft.pave.yaml`; v5 kept as
provenance. Reviewer re-verifies F only; expects PASS next pass.

## Round 4 — ROOT boundary (v6 re-verification) — 2026-08-25 — VERDICT: PASS

Finding F CLOSED on disk: one acceptance scope per route everywhere
("instead" reading, requirements.md:178-179). Reviewer re-tested the outcome
set per route (exhaustive + mutually exclusive on both routes) and probed the
correct-but-slower-upgrade hole — still expressible via regression_confirmed →
repair → no_progress → rederive_approach → gate 3; does not fail open. Scope
clean: diff = header + the four F texts; nothing from rounds 1–3 regressed.

### Carried finding

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| G | LOW | Three measurement-side texts still backport-worded (`measure_against_baseline.purpose`:505, `measurement_artifacts.subject`:146, `design_campaign.purpose`:350 "both gates") — one round upstream of the F fix. Not blocking: both owning nodes are predicted decompose, info exists elsewhere, gate 2 shows the per-route plan pre-spend. | FIX at child dispatch: carried verbatim into the design_campaign and measure_against_baseline planner briefs — generalize to "the kickoff-declared acceptance procedures for this campaign's route". Parent-text edit lands at resynchronization/assembly. |

### Boundary close-out

Four rounds: 7 + 5 + 1 findings, all FIX, all verified closed on disk; no
finding re-raised after rejection, none withdrawn. Root skeleton preserves the
policed requirements: exactly 3 user-owned gates, measurer-never-adjudicates,
two-tier breaker with no attempt caps, every loop with an operator-controlled
stop, both routes executable and closable, forbidden effects distributed,
pin-generic, §5.3.1 evidence on success outcomes.

Reviewer residuals 1–6 (nonblocking) carried to assembly — authoritative list
in the Round-3 section plus round 4's restatement. Root marked `reviewed`;
18 children enqueued; per protocol the reviewer receives every child planner
return, carrying per-node judgments forward.

## Child boundaries

Same retained reviewer; one subsection per boundary, rounds appended as they
close. Verdict solidifies only at PASS.

### cost_routes_and_rank_backlog — round 1 — 2026-08-25 — VERDICT: REVISE

Subject: `planning/cost_routes_and_rank_backlog.draft.pave.yaml` (np-costing)
against `planning/root.v6.draft.pave.yaml#cost_routes_and_rank_backlog`.
Atomic verdict NOT challenged; volume/re-authoring/authority/pin hypotheses
checked and refuted by the reviewer.

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| H1 | HIGH | Criteria-precede-costing ordering is anchored only in the doer's own output directory (`artifacts/run/backlog/`), so the rung-1 leg is a self-report (pave-spec:433-438); retrofitted vs pre-registered criteria indistinguishable to `review_route_verdicts`; wrong-baseline incident (system-map:151-153) is this exact class, and the re-cost loop is when criteria drift. | FIX: persist the costing-criteria registration (or digest) into lead-owned run state before any per-target costing record, mirroring `comparators_preregistered`/`comparator_registrations`. Rung-1 hardening, not a second gate; fragment's §4.11 rebuttal of a blocking mechanism survives. Also tighten the enforcement-record paraphrase of rung 2 ("a check the doer does not run", not "…the doer's judgment does not control"). |
| H2 | LOW | `costing_stalled` fires only on identical finding fingerprints — defeatable by a finding set drifting one item per lap; tier-2 breaker cannot help (counts hardware attempts, loop is pre-gate-1). | FIX: one clause — also fire when the finding set does not materially shrink across two consecutive re-entries. No attempt caps introduced. |
| H3 | LOW | Miscitation: "spec 9.2" cited for author-written-criteria-graded-by-independent-judge; the rule is §5.3.1 (9.2 is only backlinked). Carried into the enforcement record as authority. | FIX: correct the citation. |

Also from this round: c1 and c2 resolutions judged adequate (c1 batches with
round-2 finding C's sweep — reviewer self-flagged that `costing_stalled` was
missed in C); debt-ledger absorption re-homed to `verify_run_preconditions`
(reviewer corrected its own round-1 "covered by delta scan" call — true only
for requested targets), extending the scorecard re-verification clause;
resynchronization tidy: align `backlog_ranked.meaning` with the child DoD's
predicted-file-surface part when applying c1.

Routing: REVISE → findings to the SAME retained planner np-costing, fresh
path `planning/cost_routes_and_rank_backlog.v2.draft.pave.yaml`; v1 kept as
provenance. Reviewer expects PASS after H1.

### Seven-boundary batch — round 1 — 2026-08-25 — 4 PASS / 3 REVISE

Subjects: the seven fragments forwarded 2026-08-25, each against its
`root.v6.draft.pave.yaml#<node>` contract.

| Boundary | Verdict | Key finding |
|---|---|---|
| review_route_verdicts | PASS | Reopened by lead post-verdict for the carried-H1 rubric amendment (below) |
| review_campaign_design | PASS | Two-authorities rejection well-supported; gate-2-decline detour preserves the closure-record invariant |
| review_measurement_verdict | PASS | c4 = explicit consumes add confirmed; route-scoped cells match repaired v6 cell-by-cell |
| rederive_approach | PASS | Lead two-outcome reading CONFIRMED; third outcome tested and declined; c3 adequate + one LOW |
| prepare_pr | REVISE | F-PR HIGH (below) |
| review_pr_evidence | REVISE | F-PR HIGH, second location |
| verify_run_closure | REVISE | c5 HIGH — option (a) inadequate, take (b) |

#### F-PR (HIGH) — revision-currency predicate unsatisfiable after history tidy

prepare_pr's activity 2 squashes the branch (rewrites SHAs); DoD (d) demands
acceptance-bearing artifacts name the exact final revision; those artifacts
are upstream and stable by the node's own assumption. Branch (i): currency
pass → `evidence_gap_found` → real re-measurement spend → squash → same
mismatch; the no_progress detector keys only on review findings, so the loop
is unbounded hardware spend. Branch (ii): review_pr_evidence raises it →
identical findings → no_progress → rederive, whose option set cannot repair
an artifact-immutability contradiction → honest close_out for a PASSING
campaign. Either way no PR opens — defeats the delivery clause.
Disposition FIX (both fragments): predicate becomes CONTENT identity —
`git diff <measured-revision> <final-branch-head>` empty, transcript
persisted; git-issued identifiers keep rung-1 provenance; a genuinely stale
measurement still fails. Riders: the recommended pr_package_to_pr_review
edge check must adopt the fixed predicate; prepare_pr's no_progress extended
to count identical evidence_gap laps (reviewer residual 4).

#### c5 (HIGH) — verify_run_closure needs a declared non-success outcome

A discrepant claimed closure has no live campaign instance; the join holds
forever (livelock the planner itself named), and option (a) rests recovery
on the parent's own declared open question (root.v6:1089-1091). Disposition
FIX = option (b), lead-decided: outcome `closure_unverified`
(required_evidence [run_closure_verdict]) + edge to close_campaign (reuses
closure_evidence_settled.on_failure_route's declared destination) + a
loop_bounds line stating gate 3's user stop bounds the new cycle (no attempt
caps). Parent edit lands at resynchronization via c5; fragment repair to
np-run-closure on v2.

#### Carried H1 corroboration

review_route_verdicts' rubric (categories a–d) has no criteria-precedence
category, so costing's claimed rung-3 backstop does not test the H1 property.
Composed fix: (1) costing v2's run-state registration (primary, delivered);
(2) rubric category added via lead-dispatched amendment to np-route-review on
`review_route_verdicts.v2.draft.pave.yaml` — reopens the PASS for a
delta-only re-verify.

#### Conflict dispositions from this round

c3 adequate (LOW folded at assembly: lead completeness check must require
each rederivation record to cite prior records — the §9.8 bound's enforced
input; re-run validate_pave.py after the self-consumes edit). c4 adequate as
the EXPLICIT consumes add. c5 resolved in principle as option (b).

#### Rejected hypotheses (reviewer-tested, do not re-litigate)

rederive⇄design supercycle unbounded (monotone exhaustion forces gate 3);
gate-2 user abandonment unrouted (detour preserves closure-record
invariant); two-authorities split at review_campaign_design; route-scope
cell mismatch vs v6.

#### Residual risks from this batch (nonblocking, carried to assembly)

7. No v6 node clearly writes a run-level capability/authority-loss record
   for `no_route_remains` — parent-level producer question.
8. review_measurement_verdict f4's traversal cross-check is prose about lead
   behavior, not enforcement (outbound edges carry no `checks:` field).
9. Two roles write `artifacts/reviews/<campaign>/` (reviewer stream + gate-2
   objection mirror) — name ownership in the artifact-layout reference.
10. prepare_pr's evidence_gap stall detector (folded into the F-PR repair as
    a rider — verify at re-review).
11. Reviewer-identity separation for review_pr_evidence /
    review_measurement_verdict rests on the runtime binding — consider the
    measurer_not_adjudicator declared-check precedent for reviewer seats.

Routing: three repairs to their SAME retained planners on fresh v2 paths
(np-prepare-pr, np-pr-review, np-run-closure) + amendment to np-route-review;
reviewer re-verifies deltas against these findings, not fresh reviews.

### Five-boundary batch — round 1 — 2026-08-25 — 5 PASS (+ c5 fourth part)

Subjects: the four fable-respawn fragments + review_implementation.v3, each
against its `root.v6.draft.pave.yaml#<node>` contract.

| Boundary | Verdict | Notes |
|---|---|---|
| verify_run_preconditions.v2 | PASS | Debt-ledger = parent resync item, not a finding; inputs_missing widening confirmed needed |
| assemble_kickoff_contracts.v2 | PASS | Flagged gate-1 routing judged sufficient (recorded, not deferred to whole-plan) |
| adjudicate_results.v2 | PASS | Registration anchor = H1 idiom, third in-graph precedent; consumes LOW batched into c4 |
| close_campaign.v2 | PASS | Self-evaluation challenge accepted; validity load-bearing on c5(b); exposed c5's 4th part |
| review_implementation.v3 | PASS | Strongest of batch; edge-check LOW accepted → c6 |

#### c5 amendment (reviewer self-correction, urgent) — FOURTH part

Option (b) as sent could not converge: lap 1 already executed a closure, so
each `closure_unverified` re-entry writes another closure_record and the
"exactly one closure record" DoD fails harder every lap. Part (4): re-entry
supersession semantics in the assembly artifact-layout reference — prior
closure_record + gate_approval_record archived to a declared path; "exactly
one" reads the CURRENT record. Reviewer verified: re-entry re-executes the
SAME closure type (no forbidden-effect breach); gate-3 re-presentation
matches the declined_closure precedent (contract counts gate identities, not
interactions). c5 register updated; np-run-closure addendum sent mid-repair.

#### LOW dispositions (lead)

- adjudicate_results consumes omits kickoff_contract_record its DoD quotes →
  batched into c4 (symmetry with review_measurement_verdict).
- review_measurement_verdict rubric gains the unadjudicable-as-written
  clause + the measurement-hash anchor in its committed findings shape →
  amendment to np-verdict-review on v2 (boundary reopened).
- close_campaign trap PR-path required set names the pass_confirmed findings
  record directly → amendment to np-close-2 on v3 (boundary reopened).
- review_implementation edge check accepted → registered c6
  (reviewed_impl_to_hardware: checkout == findings-record stamp, lead-
  evaluated, on_failure_route review_implementation).
- inputs_missing widening + run_aborted verbatim-declination preservation +
  debt-ledger TWO-PLACE duty (parent purpose AND fragment activity) →
  resync items in frontier notes.
- Binding producer/detector key interfaces: review triple carried into
  np-implement's dispatch as fixed interface; reviewer verifies all three
  key pairs at the whole-plan gate.
- Enforcement record must carry the real CPU-mode evidence chain
  (provenance → non-doer re-check → review), not review alone — root's
  gameability line understates the child design.

#### Residual risks from this batch (nonblocking, carried to assembly)

12. An (e) "not reviewable as one unit" finding has no exact location —
    optional anchor: state increment count + name unmapped increments.
13. run_aborted is a single-entry terminal; preserving the verbatim
    declination is what keeps it honest for the costing-rejection case.

### cost_routes_and_rank_backlog — round 2 — 2026-08-25 — VERDICT: PASS

Boundary CLOSED. H1/H2/H3 all verified closed on disk against the reviewer's
original evidence. Load-bearing detail: the digest match GATES
`backlog_ranked` in activity 6 (not just DoD prose); the false
artifact-sequence gameability leg is deleted. H2's doer-judged "materially
shrunk" soft spot accepted — the finding set lives in the reviewer-written
artifact, and the loop is pre-gate-1. Scope clean (justification
byte-identical; only finding repairs + declared bookkeeping moved).

n25 decision: SIBLING run-state field, not a `comparator_registrations`
meaning extension — an intake registration trivially precedes every
measurement artifact, so a shared field could mask a missing comparator
registration in the comparators check's enumeration. Assembly item: define
the registration record shape ONCE (subject, digest, timestamp).

Reviewer self-downgrade on the record: the carried-H1 rubric corroboration
no longer supports a HIGH once the registration is mechanical and gates the
outcome — category (e) is defense-in-depth only; it must not be booked as
closing a HIGH nor hold the plan gate.

### review_pr_evidence — round 2 — 2026-08-25 — VERDICT: PASS (delta)

F-PR closed at this location; scope byte-identical outside declared deltas.
LOW → v3 micro-amendment: DoD phrase "revision-hash match" still carries the
superseded SHA framing; becomes "revision content-identity diff". F-PR
remains HALF closed until prepare_pr v2 verifies (the dangerous half: its
evidence_gap branch burns measurement spend).

### review_route_verdicts — round 2 — 2026-08-25 — VERDICT: PASS (delta)

Category (e) precisely coherent with costing v2 (digest match + timestamp
precedence + narrowing case; run-state registration named as the
world-anchored source). Narrowed-after-results correctly left as judgment —
inherent pre-registration residual everywhere, no false mechanical claim.
LOW → v3 micro-amendment: add the matching evidence question for (e).

### Process rule (reviewer, adopted for all subsequent repair briefs)

Both delta repairs landed the cited line correctly and each left exactly one
sibling text describing the old property. Every repair brief must instruct
propagating the change to EVERY text in the fragment that references the
property (DoD, activities, evidence questions, enforcement), not only the
cited line. Recorded as resync/process item 10 in frontier notes.

### Five-unit batch — 2026-08-25 — 4 PASS / 1 REVISE

| Unit | Verdict | Key point |
|---|---|---|
| verify_run_closure.v2 | PASS | All four c5 parts verified; unsuperseded-record clause credited beyond spec; fan-out edge form added to c5 |
| prepare_pr.v2 | PASS | F-PR fully CLOSED; findings-record shape → layout reference (single authority, both cite); one LOW (unresolvable-revision) |
| close_campaign.v3 | PASS | Both deltas verified; LOW (half-recorded compliance basis) DROPPED — facts live in c5 + this record |
| review_measurement_verdict.v2 | PASS | Category (h) closes waiver hole; fingerprint pair #2 RESOLVED |
| implement_increments | REVISE | Decompose sound; one HIGH (internal livelock), one LOW (outcome precedence) |

#### implement_increments HIGH — two internal loops invisible to every stall detector

Loops `coverage_gap_found → scope` and `increment_stuck → scope` cannot
satisfy `no_new_route`'s findings-history conjunct (neither writes to
artifacts/reviews/), and gap sub-classes (b) uncovered-diff-work and (c)
import-scan-hit receive CPU-command acceptance that cannot verify the gap —
so `increment_passed` emits while the gap stands and the campaign burns laps
silently with no operator stop ((c) is the run's own forbidden effect). The
loop-bound entry claiming three reachable stops is unfalsifiable as written.
FIX, two clauses on scope_next_increment (no new node/outcome/state):
(1) a coverage-gap work item's acceptance is the recomputed gap check that
found it (extends the fragment's own repackaging idiom); (2) the no-progress
detector's inputs extend to the lap records' own gap/stuck entries — copying
prepare_pr.v2's internally-originated stall detector shape. LOW: one
precedence line over scope's five outcomes (no_new_route vs plan_exceeds_node
can co-hold), mirroring verify_run_closure.v2's precedence rule.
Repair → SAME planner np-implement, fresh path implement_increments.v2.

#### Confirmations and carries out of this batch

- All three child sizing predictions confirmed (realize's conditional
  atomicity praised as the better shape; valve reachable from inside).
- design_campaign sharpening expectations CONFIRMED + the
  plan_unrealizable_as_designed refusal — carried into np-design's dispatch.
- np-measure carry (dispatched): measurement_artifacts records the git
  revision measured, a git-issued identifier, measurer-side only.
- LOW on both PR-side fragments: content-identity predicate must require
  rev-parse --verify + diff exit 0 (empty stdout alone passes on an
  unresolvable object) → prepare_pr v3, review_pr_evidence v4.
- Class-(e) absorption independently corroborates F-PR: post-measurement
  repackaging rewrites history legally; SHA identity would have failed
  correct packages.
- Fingerprint pairs: #2 resolved (this batch), #4 resolved by construction
  (review triple adopted verbatim); #1 needs the layout-reference pin;
  #3 verify costing detector text at the whole-plan round.

#### Residuals added (nonblocking)

14. increment-acceptance rung accuracy for the merged enforcement record
    (command exit = rung 1; test adequacy = rung 2 via review — fragment
    disclosed, not hidden).
15. realize_increment's added /opt + venv-clone prohibition reflected into
    the merged enforcement record (narrowing, safe).

### Batch — 2026-08-25 (evening) — scan PASS, two closures, pair #3, consolidation

| Unit | Verdict | Key point |
|---|---|---|
| scan_upstream_delta (2nd DECOMPOSE) | PASS | Entailment holds; 6 LOWs (4 in-fragment → v2 amendment, 2 land outside); children enqueued |
| review_route_verdicts.v3 | PASS | Boundary CLOSED; stealth-narrowing residual DROPPED (defense-in-depth-only category); np-route-review retired |
| review_pr_evidence.v3 | PASS | Zero revision-hash hits; carried exit-code LOW = exactly what v4 fixes |
| Fingerprint pair #3 | CLOSED, no finding | Fingerprint inputs unspecified BUT leg 2 (no-material-shrink) is fingerprint-independent — stop cannot fire never |

#### scan_upstream_delta — the six LOWs

In-fragment (batched as one v2 micro-amendment to np-delta-scan):
L1 split rationale at :185-191 is wrong-but-plausible — all three recorded
warning signs are fan-out-satisfiable; the valid reason is MIXED INSTANCE
SCOPE (per-target existential + run-level universal cannot share one node's
instance scope), already present at :16-17/:77-78. L2 :228-229 mislabels an
automatic edge as the operator stop (real stop: costing_stalled →
run_paused). L3 no precedence among assemble_delta_report's three outcomes
(sources_unreachable vs reports_insufficient co-hold, route oppositely).
L4 parent's "sufficient for costing" never reconciled with recorded residual
gaps — honest gap recording SELECTS delta_mapped.
Outside the fragment: costing_stalled scope widening to evidence-gap
re-entries (resync item 18, new-evidence NOT a costing reopen — the
scan↔costing gap cycle produces fresh evidence each lap so neither existing
stall leg fires); assemble_delta_report seat separation as a binding item
(item 19, review_pr_evidence.v2 pattern).
Credited: rubric-predates-instances satisfied BY CONSTRUCTION (seven
sections frozen in target_traced's graph text) — cleanest §5.3.1 rung-2
instance in the run. Counter opinion adopted: derive the re-trace count from
per-target event files carrying scan-entry ids; never a stored integer
(write-authority, drift, and reset logic all favor derivation) — item 22.
Rejected hypotheses on record: post-bound delta_mapped self-contradiction
(disproved by the :104-105 parenthetical), child authority excess,
doc-claim-verification node, target-rescoping child.
Reviewer bound mislabel note: the re-trace bound is a per-scan-entry
anti-thrash counter, not an attempt cap — targets are never abandoned;
costing re-entry regrants the re-trace. §9.8 compliant.

#### Standing rule (third occurrence) — 3+-outcome precedence

Any node with 3+ outcomes whose meanings can co-hold must declare
precedence. Seen in verify_run_closure (fixed via c5), implement_increments
(LOW pending), scan_upstream_delta (L3). Now in every planner brief and a
resync check — frontier notes item 20.

#### Pair #3 closure + consolidated findings-record shape (item 17)

Pair #3 (review_route_verdicts → costing): producer enumerates
(v2 f1:142-149), consumer's fingerprint inputs unnamed
(costing v2:191-197) — real drift risk, BUT the loop bound's second leg
(finding set not materially shrunk across two consecutive re-entries,
H2's addition) is a property of the set, not the fingerprint, so an
over-sensitive fingerprint degrades leg 1 only. Adequately bounded;
resync-level note, not a §9.8 finding. H2 bought more than it was aimed at.
CONSOLIDATION replacing four pairwise pins: ONE artifact-layout entry with
five fields (stable per-finding label, cited location, defect class,
required change, measurement content hash(es) — the last for
review_measurement_verdict only); all four producer/consumer pairs CITE it,
each consumer declares its detector-key subset. Reviewer verifies five
fields + four citers at assembly. All four fingerprint pairs now resolved
or specified.

#### review_route_verdicts.v3 — closure detail

New (e) evidence question verified at :111-114 (7 total); covers 2 of 3
(e)-clauses. Residual stealth form (honest digest, fewer criteria applied)
DROPPED per reviewer: sub-gap inside a defense-in-depth-only category, not
material by construction, gate 1 immediately downstream. Optional closing
clause if the file ever reopens: "and does each per-target costing record
apply every registered criterion?"

#### Attribution correction

The category-(h) adjudicability warning in np-design's carry package books
as LEAD-ADDED, reviewer-ENDORSED (attacks the (h) path at its source). The
reviewer confirmed only the sharpening expectations and the
plan_unrealizable_as_designed refusal clause as its carries.

#### Mechanical note (lead)

np-implement's v2 delivery failed the lead parse gate (plain multi-line
scalar with ': ' on a continuation line, line 345) — mechanics-only fix
requested on fresh v3 path; v2 retained on disk for the reviewer's
substance-identity diff. Reviewer will also scope-check v2's +21% size
growth against the repair brief.
RECONCILED same day: the failing parse caught a mid-repair snapshot
(14:07, 30362 bytes); np-implement fixed three such defects during the
repair itself; final v2 (14:09:39, 31029 bytes,
md5 bb76d7acab94b685b6062808589059bf — matches the reviewer's pinned
baseline) parses clean. v3 is byte-identical to final v2 outside the
header comment block (lead non-comment diff: IDENTICAL).

### PR-side closures — 2026-08-25 (evening) — F-PR chain fully settled

| Unit | Verdict | Key point |
|---|---|---|
| prepare_pr.v3 (round 3) | PASS — boundary CLOSED | Exit-code predicate verified correct, not just present |
| review_pr_evidence.v4 (round 4) | PASS — boundary CLOSED | Same predicate, both transcripts embedded in findings record |

Reviewer verified three properties beyond presence: (1) sibling-coupling
holds — producer-side and reviewer-side predicate texts identical in
substance (same two conjuncts, same order, both persist both transcripts);
(2) both fragments record the MECHANISM, not just the fix ("empty stdout
alone proves nothing — a bad object errors to stderr while leaving stdout
empty") — a fix carrying its own falsifier resists later "simplification";
adopted as a repair-brief habit; (3) emptiness-only sweep clean in both
files, SHA negations intact — neither repair undid the other. F-PR
(SHA→content identity) plus its exit-code rider: fully settled at both
locations. Retired: np-prepare-pr, np-pr-review.

implement_increments.v3 status: lead mechanical pass complete (parse,
schema, 3 children, no minted c-ids, pin-generic; v2/v3 substance
identity verified); cleared to the reviewer for round-2 re-verification
of the two HIGH clauses + precedence LOW + growth scope check.

### implement_increments round 2 — 2026-08-25 — PASS, HIGH closed

Substance identity independently verified (reviewer's own comment-stripped
diff: zero differences; md5 baselines matched; mid-repair-snapshot
explanation accepted). Growth scope: all 11 hunks traced to brief items —
"what an earned +21% looks like."

HIGH clause (a) CLOSED: per-sub-class acceptance ((a) own CPU command /
(b) evidence-index recomputation / (c) import-scan re-run) — reviewer
re-ran its original failure construction; a gap-(c) repair item can no
longer emit increment_passed with NxDI contamination standing. The
false-pass mechanism is eliminated. dod_ladder keeps rung 1 via
"fixed by the gap's identity in the lap record, not chosen by the doer."

HIGH clause (b) substantively closed; ONE LOW residual: detector input
(2) conjunct 1 ("evidence directory unchanged since that lap") is false
on exactly the laps it must catch — realize_increment persists the
GROWING investigation record into that directory on every stuck lap.
Literal reading: the stuck leg never fires. Fix (one phrase, v4): scope
conjunct 1 to "no new PASSING increment evidence record for that item."
Downgrade rationale: clause (a) makes the loop fail VISIBLY per lap
(silent livelock eliminated); conjunct 2 is unaffected and correct;
precedence gives two honest judgment exits. Root cause recorded: the
prepare_pr.v2 idiom was sound at its origin (prepare_pr writes no
per-lap records) and needed ADAPTING, not copying — propagation-habit
lesson: an idiom's soundness is context-dependent.

Precedence LOW CLOSED (top-two orderings carry their reasons;
edges_prose false disjointness claim corrected with precedent cited;
binding_review_interface protects pair #4 triple keying).

Non-filed residual, on record with the reviewer's two-standards
rationale: enforcement calls no_progress→rederive_approach an
"operator-controlled stop" — wording only; unlike scan's :228 mislabel
it conceals no gap (rederive reaches real stops via the
monotone-exhaustion argument verified earlier this run).

Disposition: v4 micro-amendment to np-implement (sole delta + inline
falsifier). Children DISPATCH CLEARED by the reviewer before v4 (LOW
touches only parent detector prose): np-scope / np-realize / np-record
dispatched against the v3 child bodies. np-trace 400-failed at spawn and
was respawned identically on fable per the standing directive.

### scan_upstream_delta round 2 — 2026-08-25 — PASS, boundary CLOSED

All four LOWs closed; scope clean (11 hunks traced). Two beyond-text
additions judged EARNED: the exactly-one-outcome activity bullet
(precedence must be enacted somewhere), and sources_unreachable gaining
"and no re-trace remains outstanding" — the precedence made intrinsic to
the outcome rather than asserted alongside it.

Notable verifications: L1 restated better than the finding (mixed
instance scope forced two nodes; fan-out realizes the per-target one),
with the rejected three-warning-signs argument KEPT as an explicit
concession — the honest form that prevents quiet reuse of discarded
reasoning. L2 relabeled at all three sites WITHOUT overclaiming ("not
authored here" twice; item-18 widening still owed at resync). L3
ordering independently derived by the reviewer before reading and
matched (cheap automatic re-trace spent before consuming a human
interaction; pause report arrives more complete); starvation check
passes — the per-entry re-trace bound decreases monotonically, so
reports_insufficient cannot starve lower-precedence outcomes. L4 closed
across eight sites including selects-not-blocks on delta_mapped,
retiring round 1's dead-end probe explicitly.

Forward residual (NOT a finding, carried to np-trace's brief): L4's
blessed residual-gap path creates a mild incentive to record an
unavailable REQUIRED source as a gap and keep moving — converting a
designed run-pause into a silent proceed. The "requires" discriminator
in source_unreachable is load-bearing and must not soften in the child
fragment; the reviewer pre-announced it will check that seam.

Retired: np-delta-scan. Second fable 400 this session: np-realize failed
at spawn (zero writes), respawned identically per standing directive as
np-realize-2 (name registry held the failed seat).

### implement_increments round 3 (v4) — 2026-08-25 — PASS, boundary FULLY CLOSED

The run's first decompose is done: HIGH closed at v2/v3, LOW at v4.
Reviewer verified all 5 hunks / 4 sites; child-body stability confirmed
independently (no hunks across the realize/record spans — the v4
contract re-anchor was safe); the directory-unchanged idiom is fully
retired, surviving only as its own warning (same shape as the
SHA-identity retirement).

Beyond-spec credit: np-implement's "for that item" scoping closes a hole
the finding never named — without it, a passing record for ANY increment
would reset a stuck item's stall, so progress on increment 7 would
indefinitely mask increment 3 being stuck (the mixed-progress case most
likely in a real campaign). Conjunct 2 kept deliberately: near-subsumed
but catches partial multi-part acceptance advance; harmless as an AND.

Process notes kept: (1) np-implement credited for resolving the lead's
contradictory brief toward the propagation rule rather than the literal
instruction — the desired planner behavior under conflicting guidance;
(2) "Do not restore the original idiom" is the right guard shape — the
next editor who notices the fragments disagree finds the reason instead
of harmonizing back to the broken form; (3) reviewer watch item:
np-scope's return will restate the detector predicate — it will be
checked against v4's corrected wording (stale text = delta, not fresh
finding, since the contract amendment may have crossed its planning).

Retired: np-implement.

### design_campaign round 1 — 2026-08-25 — REVISE (2 HIGH, 3 LOW)

Node structure SOUND: five children all earned (§4.11 inflation
hypothesis tested and rejected — preregister_acceptance the most
strongly earned: separation from measurement IS its purpose); entailment
complete including the easily-dropped venv/lease component; the
preregistration hardening credited as the run's best H1-pattern instance
(doer artifact + lead run-state timestamp + existing
comparators_preregistered check). All three reviewer carries landed
verbatim. Both HIGHs live in the routing/loop-bounds the fragment
defers to the lead — legitimate deferral, wrong recommendations.

HIGH-1: the suggested record_incomplete lap counter (3) asserts an
outcome the parent meaning refutes — a converging design repairing a
DIFFERENT component each lap produces new design evidence every lap, so
at exhaustion neither no_progress disjunct (repeated identical findings
/ no new design evidence) holds; a converging design would be sent to
first-principles re-derivation. Fix: the implement_increments.v4
detector idiom (same named gap + same owning sibling + no new draft
artifact since that lap). Also restores loop-bound consistency between
the run's two repair loops.

HIGH-2: scope_exceeded — the declared sizing valve BOTH child atomicity
predictions rest on — has no route and realizes no parent outcome; a
firing valve strands the campaign. Fix: map to parent no_progress and
name it in routing (consistent with matrix_blocked /
criteria_unadjudicable).

LOWs: L1 three children (screen/matrix/record) with 3+ co-holdable
outcomes lack precedence — divergent pair pin_infeasible vs
progress_exhausted routes to different campaign fates; L2 gameability
record covers 2 of 5 children — pin_feasibility_note and
regression_matrix_draft need entries + per-child rungs (false
infeasibility is the expensive direction); L3 preregister's consumes
lists regression_matrix_draft which never exists on backport routes —
close_campaign.v2 entry_paths_note superset idiom.

Record-accuracy correction: TWO refusal clauses exist and both landed —
increment-acceptance refusal (downstream refuses rather than invents)
and kickoff-criteria refusal (criteria_unadjudicable surfaces, never
rewords). The lead's earlier framing conflated them.

Rejected hypotheses kept: record_ready-before-commitment gap correctly
relies on comparators_preregistered (§9.14.2 — no duplicate gate);
matrix atomicity-uncertain is honest conditional sizing (depends on
HIGH-2's fix).

Disposition: repair to SAME planner np-design on design_campaign.v2 —
scope HIGH-1 + HIGH-2 + L2 in parent prose only, child bodies now a
hard byte-identity constraint; L1 + L3 DELEGATED to the child planners'
own realizations. Five children dispatched in parallel per the
reviewer's explicit clearance (np-screen, np-draft-plan, np-matrix,
np-prereg, np-design-record) with briefs carrying the delegated
findings and corrected routing as context.

### record_changeset round 1 — 2026-08-25 — VERDICT: PASS (1 LOW)

All four lead claims verified against the file; byte-identity claim
verified by live diff on the extracted v3-vs-v4 block (empty output).

Two beyond-spec credits: (1) import-scan false-pass mode closed — "an
empty match set and a scan over nothing look identical"; explicit hit
count + verified revision required (:84-89), same family as the
reviewer's own git-diff exit-status finding. (2) Complete-gap-set
emission (:106-108) is LOAD-BEARING for the v4 detector's HIGH fix:
one-gap-at-a-time emission would make consecutive gap SETS never
match, reopening the unbounded gap loop by a new mechanism — the
planner found and closed a defeat the reviewer had not spotted.

Verified, no action: rung-1 correctly argued ("no conjunct rests on
the assembler's judgment"); two outcomes genuinely complementary
(item-20 rule discharged with reason); no outcomes: key — correct for
an atomic child fragment; sharpening beyond the frozen contract earned
(evaluation discipline for the same predicate, not new conjuncts).

Explicit non-reopen ruling: prepare_pr.v3's single-endpoint rev-parse
is NOT defective — its head endpoint failing would make git diff exit
nonzero, already caught by its exit-status-0 requirement; and each
node's discipline fits its inputs (record_changeset diffs a runtime-
resolved ref name, prepare_pr a recorded hash). No change either side.

LOW (accepted): completeness stated, not verifiable — nothing persisted
lets a reader tell a complete gap set from a truncated one; only
symptom would be the silent unbounded loop. Fix in the node's own
idiom: extend the import scan's explicit-count discipline to the
evidence-index and diff-coverage gap counts, making emission
completeness recomputable from the persisted record.

Disposition: micro-amendment to SAME planner np-record on fresh path
record_changeset.v2 (v1 provenance). Boundary closes on the v2 delta.

### run_hardware_attempts round 1 — 2026-08-25 — REVISE (1 HIGH, 4 LOW)

Entailment clean: four purpose clauses map 1:1 onto the four children,
each settling on distinct world evidence; lease → venv → attempts
precondition chain means no run exists where all children succeed and
the parent DoD fails.

c7 ENDORSED as the lead proposed: tier-1 exhaustion widening of
breaker_tripped is FORCED, not optional — with every materially
different attempt fingerprinted the legal move set is empty; identical
retry is a parent forbidden effect (root.v6:466), stalling is
livelock, halting into rederive_approach is semantically exact. Not a
no-attempt-caps breach: exhaustion of the legal move set, not a count
limit. Correct planner protocol (raised without minting an id).

Blocking identity precondition AFFIRMED as textbook §9.14.1 strongest
rung: likely (markers drift, hosts reused), costly AND irreversible
(reboot destroys another campaign's in-flight work), precisely
detectable (string match vs lease record), NO downstream gate catches
it. Reviewer would have flagged its absence. Companion tier-1
observing decision also correct (semantic condition, poorly
pattern-matchable — a blocking hook would misfire).

HIGH — fault/recovery flap loop unbounded. Cycle host_faulted →
recover → host_restored → probes → attempts → host_faulted evades all
three declared bounds: tier-2 counter (fragment's own :301-303
"no attempt toward the target" principle argues host-faulted attempts
don't count); roster bound ("once per fault episode" — episode reset
NEVER DEFINED, successful recovery resets the allowance);
hardware_unavailable (a flapping host is recoverable each time). No
user gate inside the loop; burns the run's most expensive resource.
Fix: define the fault-episode reset — re-fault after successful
recovery = recovery allowance exhausted → host_unrecoverable, lease
released for roster replacement; flapping then bounded by the finite
roster, hardware_unavailable reached honestly. REJECTED alternative on
record: counting host-faults against the attempt budget lets a bad
host trip the breaker and force rederivation of a good approach
(false-outcome defect).

LOW-1: early-exhaustion disjunct is a doer's negative claim — record
must enumerate the attempted configuration space + why no material
variation remains (falsifiable by rederive_approach); breaker_tripped
gains an evidence_gameability entry alongside the c7 widening.
LOW-2: (a) boot-identifier-ONLY delta expected when a logged reboot
precedes it (stale boot id would hard-stop a CORRECT host);
instance-id/hostname stay hard stops. (b) pre-action mismatch had no
declared outcome — LEAD DECISION: widen host_unrecoverable to include
refused-to-act with verbatim mismatch record (same minimal-widening
shape as c7).
LOW-3: item-20 precedence lines — execute_attempt_loop (tenth-failure
vs host-fault co-hold, materially different routes) and
replicate_campaign_venv (mixed disk-exhaustion/build-failure case).
LOW-4: fingerprint horizon split is the FIFTH producer/consumer key
pair and guards a parent forbidden effect (identical retry); cite the
item-17 consolidated layout entry, never restate; format-mismatch
falsifier inline. Item 17 updated to five pairs.

Disposition: repair to SAME planner np-hardware on fresh path
run_hardware_attempts.v2 (v1 provenance). CHILDREN HELD until v2 lands
— HIGH + LOW-2(b) touch child bodies (recover_leased_host outcomes,
fault-episode semantics); unlike design_campaign, no parallel child
dispatch.

### assemble_delta_report round 1 — 2026-08-25 — VERDICT: PASS (1 LOW)

The lead's i6 challenge: set reading AFFIRMED with a stronger proof
than the fragment records — the frozen activities order both writes
("persist the diff output" scan v2:96-97; "persist a per-target
verdict" :100), so a narrow two-file reading of allowed_effects would
forbid two frozen activities' only realization: self-refuting on the
parent's own text. Both frozen forbidden clauses (directory-scoped
write bound; report-body carve-out) would be redundant under a
file-enumerative reading. Fragment should cite :96-97/:100 in i6 at
resync (item 24).

Rejected hypothesis on record (nearly a HIGH): the metadata escape
hatch — fabricate a report-hash change via metadata to burn the bound
without a re-trace — dies because allowed_effects is a positive
exhaustive enumeration and metadata is not an assembly record under
any reading. The same clause carries the i6 reading and closes this.

LOW (item 24 append): pin report-hash SCOPE and report-write OWNERSHIP
in the SAME layout entry so "hash changed ⇒ a tracer wrote" reads off
the reference. Residual deliberately NOT filed: a missing scan-entry
id is a dispatch-brief defect halting before work (harness horizon) —
distinguished from recover_leased_host's in-execution mismatch.

Credits: strict-order precedence with transcript evidence question
(the item-20 model answer); derived counter with "no reset logic
exists to get wrong"; stale-verdict guard; index supersession by
citation; §9.14.2 no-blocking-rung correctly argued.

### measure_against_baseline round 1 — 2026-08-25 — REVISE (1 HIGH, 4 LOW)

Entailment holds (bundles_stable settles measurements_recorded
near-verbatim, strengthened to a re-read test; comparator prohibition
propagated to all four children; baseline access narrowed to child 2).
Measured-revision carry verified consistent with the closed PR chain.

HIGH: a defective PROCEDURE has no return route. Both parent re-entry
edges land on child 3, which executes fixed invocations and may not
alter comparators — re-entry for a wrong-property measurement
reproduces the same inadequate number. Child 3's outcomes carry no
procedure-defect route to child 1; the smoke gate cannot catch
wrong-property measurement by design (the fragment names two concrete
instances and its own model_effort note admits "a wrong procedure
silently corrupts every downstream number"). Reviewer verified no
earlier gate catches it. Fix: procedure_defect_found on child 3 →
realize_measurement_procedures, sharing the per-measurement repair
budget, exhausting into declared_measurement_unproducible →
measurement_blocked. Mirrors the existing reference-side route.

LOW-1: repair counter proposed as a stored campaign-state integer with
no writer — derive from child-4 defect records by path (item-22
precedent); lead adopted derive. LOW-2: evidence_stable_before_verdict
and measurer_not_adjudicator still route failures to the decomposed
parent — retarget at resync (stability → child 4; adjudicator-identity
→ child 3 fresh seat); item 26. LOW-3: child 3 precedence line (four
outcomes with the HIGH), in contract text per item 20's where-it-lands
clause; child 4 verified not needing one. LOW-4: :314-316 restates the
identity predicate in pre-repair bare-empty-diff form — cite the
repaired two-conjunct predicate instead.

Rejected hypothesis: measured_revision under "campaign-state additions"
self-disambiguates as artifact-homed ("as recorded per run record and
per bundle") — no single-writer conflict.

Credits: measurer_identity four-way producer scope (the load-bearing
closure); route_scoping conditional-in-effect-not-structure;
smoke-evidence exclusion preserving comparators_preregistered;
re-entry-as-resume-points instinct right (the HIGH is that it covered
two of three defect directions); sizing rests on four distinct
recovery modes, checked not waived.

Disposition: repair to SAME planner np-measure on fresh path
measure_against_baseline.v2. CHILDREN HELD (HIGH touches child 3's
outcome set).

### trace_target_delta round 1 — 2026-08-25 — VERDICT: PASS (1 LOW)

Pre-announced requires-discriminator seam CLOSED. Three attacks all
die: fabricated transcripts need world-issued identifiers (not
mintable); omitted transcripts are rubric-deficient; and the real
attack — never declaring an unavailable source required — dies because
the frozen activity list PINS each section's sources, so an A1 section
whose clone was unreachable cannot carry the required clone-revision
transcript and the absence is visible. A2's conditional case covered:
dead-ends must carry API revisions an unreachable gh cannot yield.
The discriminator is anchored in the activity list, not doer judgment
— reviewer recommends adding that sentence at resync (stronger than
the recorded defense; item 27).

LOW (item 27): the load-bearing sizing citation misattributes — the
~10-target figure is §3.4 not §6; "time-box artifacts" overstates (one
of seven gaps; the rest are scope/access limits); "not context
exhaustion" is an unsupported negative (phrase absent from the
source). Verdict SURVIVES on grounds 2–3 (§9.12 volume-alone rule;
honest-residual-gaps graceful degradation — the carrying argument).
Three other lens-report citations verified precise.

Credits: two-tier A1 claim verified at §3.4; mechanical
runner-entanglement check verified verbatim; premise-correction
precedent correctly characterized; per-route event accounting prevents
evidence-gap re-entries exhausting assembly's bound; honest fail-open
admission on the skipped event file.

### realize_increment round 1 — 2026-08-25 — VERDICT: PASS (1 LOW)

Reviewer independently extracted and diffed the v3/v4 realize blocks:
75 lines each, IDENTICAL — the planner's staleness self-check is true
and the v3 frozen reference survives the v4 re-base.

Precedence credited as the non-obvious right ordering:
evidence_contradicts_design > increment_passed ("a green transcript
reached by deviating is not a pass") — the naive order would admit
deviate-pass-report-success.

LOW: the precedence lives only in x_planning, which planning-layout.md
scopes to planning verdicts — a runtime emission rule is node behavior
and belongs in contract text. Relocate at resync (item 27); item 20
gains the general "where it lands" clause.

Credits: run-wide prohibitions restated without node-local blocking
duplicate (strand-risk reason given); advisory monitor skipped with
technique-selection cost reasoning; all three frozen properties carry
enforcement entries; rung-1 acceptance never doer-chosen;
fresh-context binding carries its own falsifier and is registered for
assembly.

### assemble_regression_matrix round 1 — 2026-08-25 — VERDICT: PASS (2 LOW) [plan-reviewer-2]

First verdict from the second retained reviewer (item 28). Sizing
verdict independently fact-checked against four primary sources and
holds; runtime_binding_first confirmed a real check, not a formality.

LOW-1: the delegated L1 precedence is realized in substance (including
the read-in-full honesty rule) but lives only in x_planning, which the
built artifact cannot see (empirically confirmed: a built workflow
carries no x_planning). Rated LOW because both co-holdable outcomes
feed the same parent no_progress — a mislabel changes record content,
not routing; the residual harm (partial-read suspicion escalated to a
user-facing criteria-change proposal) is operator-gated and costs one
lap. Correction at resync: fold matrix_blocked-first-with-annex and
the planner's own honesty rule verbatim into the frozen contract text.
Bookkeeping consequence registered (item 27): contract-text precedence
for all three delegated children needs parent-level edits — child
bodies are byte-frozen.

LOW-2: both why_not_stronger rungs lean on a gate-2 re-run that
review_campaign_design a2 does not require (its lookup reads are
optional). Failure mode: a paraphrased framework threshold is not
kickoff-drift, passes gate 2, freezes at preregistration. LOW because
verbatim quotes against a read-only pinned repo are refutable by
repo-wide grep. Correction at resync: one a2 clause for upgrade
routes (row-set re-run + character-exact spot-check).

Five rejected hypotheses on record, including the candidate HIGH
(row-format anchors are precision, not the rung-1 carrier) and a
valve-counter refusal (adding one would be over-enforcement per §9.8).
Residuals: stale thin-suite premise (ADDENDUM: upstream ships zero
tests — item 27, hit by two planners in opposite directions); 9-vs-13
debugger count; cosmetic rule_order id; v1 contract reference.
blocked_requires_settled_evidence credited as the honest-blocker
idiom worth promoting to the standing-rule set.

### run_hardware_attempts round 2 (v2) — 2026-08-25 — PASS, boundary CLOSED

Reviewer diffed v2-vs-v1 normalized rather than trusting the delta map;
map proved honest, no unreported changes. All five findings resolved,
three better than specified. The HIGH fix is completed in a place the
reviewer missed: acquire_hardware_lease.no_host_available widened to
allowance-exhausted — without it a spent host would still look
leasable and the flap could restart through the front door. Bound
verified: one recovery per host, finite roster, recoveries ≤ roster
size, then hardware_unavailable honestly. LOW-2(a)+(b) landed together
with the matching forbidden_effects exception (else the prohibition
would contradict the allowance). LOW-3 precedence in contract text
(item-20 where-it-lands satisfied, diff-confirmed), with a nuance the
reviewer had not considered: host_faulted wins the threshold-reaching
attempt but the count stands, and a new pre-attempt tier-2 check
activity re-trips the breaker on resume — no lost breaker. LOW-4
deferred to item 17 with the divergent-normalization falsifier. c7
affirmed-with-marker. Four children RELEASED: dispatched to np-lease,
np-venv, np-attempt-loop, np-recover; review seat plan-reviewer.

### record_changeset round 2 (v2) — 2026-08-25 — PASS, boundary CLOSED

The count-discipline repair verified, and it closes an evasion the
reviewer did not specify: truncating counts and list together is
caught by re-running the three instruments at recorded revisions —
all three counts verified genuinely re-derivable (scan persists
command + revision; index derives from two non-doer artifacts; diff
coverage runs at rev-parse-verified endpoints). Bonus:
outcome_disjointness now three integers, mechanical where it was
interpretive. Delta confined to theme across ~13 sites. np-record
retired.

### design_campaign round 2 (v2) — 2026-08-25 — REVISE (2 HIGH, both one-clause)

Child bodies confirmed byte-identical by diff; delta map honest; L2
resolved (entry 1 — false infeasibility as the expensive direction —
the sharpest).

HIGH-1(v2): the replacement exhaustion condition conjoins the parent's
disjunction ("repeated identical review findings OR no new design
evidence") and its literal reading is unbounded — a sibling producing
any new draft each lap while never fixing gap X never exhausts, with
gate 2 after design_ready so the user never sees it spin. Second
fragment to drop a qualifier copying the v4 idiom (item 29). Fix:
recurrence of the named gap plus absence of a GAP-CLOSING artifact
FOR THAT GAP.

HIGH-2(v2): scope_exceeded → no_progress is the right destination
through the wrong door — the parent meaning refuses sizing-valve
traffic (fires lap 1 with abundant evidence). c7's shape, asserted as
settled instead of surfaced. Lead minted c9 (widen parent no_progress
at resync); v3 states the mapping contingent on c9 with the falsifier
inline.

Disposition: repair to SAME planner np-design on design_campaign.v3;
child bodies stay byte-identical.

### screen_pin_and_progress round 1 — 2026-08-25 — REVISE (1 HIGH, 2 LOW) [plan-reviewer-2]

c8 CONFIRMED REAL; limb 1 minimal and endorsed (reviewer independently
derived the forced ping-pong — screen_passed requires naming the
lever, which exists only in rederivation_record — and rejected two
smaller fixes). HIGH-1: the divergent-pair precedence is x_planning-
only, and this pair's routes diverge (close-out vs re-derivation) — a
routing-exclusivity gap in the shipped graph; HIGH here vs LOW on
matrix because matrix's co-hold feeds one parent outcome. The frozen
screen node is the only child with no activities key, so the resync
contract-text edit is owed regardless; the planner's repair books the
landing as an explicit interface_needs entry. LOW-1: c8 limb 2
rejected — design-revision stamps on findings records (already
committed by review_campaign_design) settle evidence movement; limb 2
would create the cross-sibling versioned-directory hazard; c8
narrowed to consumes + rederivation_record only. LOW-2: the socratic
guard's re-screen route declares its bound in the guard itself (the
ambiguity rule, stated where the graph sees it).

Five rejected hypotheses on record, including guard sizing confirmed
correct per §9.14.1 (campaign-ending transition, reviewed rung, judge
not author). Credits: the detector realization ("gap identity is
judged on the finding's named subject, not its prose" — lifted into
the standing-rule set); the planner's own-inputs evidence question
surfaced c8. Repair to SAME planner np-screen on v2; re-verify by
plan-reviewer-2.

### measure_against_baseline round 2 (v2) — 2026-08-25 — PASS (1 new LOW)

All five round-1 repairs verified sharpest-form: the HIGH exactly as
specified (procedure_defect_found on child 3 → child 1, comparator
FROZEN inside the repair loop, re-derivation an explicit recorded
event; child 2 re-captures only comparisons whose procedure changed;
shared budget exhausts into declared_measurement_unproducible); LOW-1
count-by-path with the allowed-effects falsifier; LOW-3 four-outcome
precedence echoed in purpose + all outcome meanings; LOW-4
cite-over-paraphrase with the false-pass reason named.

NEW LOW (lead-adopted): the stated budget condition "three repair
passes per measurement without new evidence" disagrees with a
derivation that counts ALL defect records per measurement — derive the
count by (measurement, defect identity) pair. Carried verbatim into
all four child briefs; item 26 second append.

Disposition: boundary CLOSED. Four children released and dispatched
(np-mproc, np-mbase, np-mruns, np-mstab). np-measure retired.

### scope_next_increment round 1 — 2026-08-25 — PASS (1 LOW)

Full atomic review after the earlier pre-announced v4 detector check
(both qualifiers at :31 and :201). Rung-1 DoD confirmed; all four
contract properties frozen; family 2 written to the v4 corrected
conjunct from the start; deliberate detector boundary (CPU-pass-but-
measurement-fail cycles outside, bounded by breaker/rederivation)
accepted as correct silence.

LOW (lead-adopted, item 27(g)): the family-1 satisfiability note
(evidence records exist only on pass; stuck laps write investigation
records) is a deliberate plan-scoping asymmetry — land its one-
sentence reason beside v4's parenthetical at resync.

Disposition: boundary CLOSED; implement_increments boundary now FULLY
closed. np-scope retired.

### assemble_design_record round 1 — 2026-08-25 — REVISE (1 HIGH, 2 LOW) [plan-reviewer-2]

HIGH: design-lap identity. The boundary has four re-entry edges but
every artifact is keyed by campaign alone — cross-lap reads are
ambiguous or silently overwritten. Closed by lead decision (item
31a): a design-entry id minted at each design_campaign entry
(scan_entry_id mirror), stamped into every design-lap artifact; the
assembler refuses to run without the current id (binding contract
text) plus the layout ask (id field + archive path for superseded
laps). LOW-1: precedence placement folds at resync. LOW-2: the
entry-paths superset note must land on BOTH suppression paths.

Relay addendum: :14-20 affirmatively CLAIMS L1 discharge while the
precedence sits x_planning-only — registered CLAIMED-NOT-LANDED in
the item-27(h) resync audit; frozen outcome meanings already force
exclusivity, so severity stays LOW.

Disposition: repair to SAME planner np-design-record on
assemble_design_record.v2 (plus cross-boundary additions: two
pre-realized checklist rows contingent on the item-27(e) parent
edit; venv/lease row split; d6 strengthened to "committed" per item
31b). Re-verify by plan-reviewer-2 same-thread.

### preregister_acceptance round 1 — 2026-08-25 — REVISE (1 HIGH, 4 LOW) [plan-reviewer-2]

HIGH: shipped-package citation — the DoD/hardening cites .pave
run-workspace paths (exploration/*.md, system-map.md) that do not
ship in the generated plugin. Closed by lead decision (item 30):
load-bearing exploration findings ship as BUILT domain references;
the node cites built paths generically and books the interface need.
LOW-1: route/lap-conditional inputs purpose clause is parent contract
text (item 27(e)). LOW-2: re-anchor pitfall citations to built-
reference sections, not exploration line numbers. LOW-3: stable
matrix row id (item 27(e)). LOW-4: commitment-timing conflict with
design_record d6 DECIDED for prereg's shape (item 31b) — the lead
commits on consuming acceptance_preregistered; d6 strengthens to
"committed". Two-actor shape protected verbatim.

Disposition: repair to SAME planner np-prereg on
preregister_acceptance.v2; re-verify by plan-reviewer-2 same-thread.

### draft_increment_plan round 1 — 2026-08-25 — REVISE (3 HIGH, 2 LOW) [plan-reviewer-2]

HIGH-1: the ledger fact at :322-327 is INVERTED per system-map
ADDENDUM item 1 (upstream ships zero test/ci at both versions; the 7
spec-decode files are fork-only P-EAGLE artifacts) — it asks a repair
of parent text that is already correct. Strike + replace with the
addendum finding; disambiguate :286-292 as fork-side; no parent
prose edit. HIGH-2: runtime DoD cites the .pave exploration corpus —
closed by item 30 (cite the built patch-mechanism inventory
reference). HIGH-3: assembler checklist two rows short (patch-
decision register, coverage trace) — originates in the PARENT frozen
purpose, rides item 27(e); the sibling assembler pre-realizes the
rows; :351-356 stays as a citation. LOW-1: split the merged
venv/lease row. LOW-2: correct the enforcement overstatement.

Reviewer's 27(c) reading CONFIRMED (the frozen purpose is right; the
ledger fact cited superseded body text).

Disposition: repair to SAME planner np-draft-plan on
draft_increment_plan.v2; re-verify by plan-reviewer-2 same-thread.

### design_campaign round 3 (v3) — 2026-08-25 — PASS. Boundary closes.

HIGH-1(v2) resolved by CITATION: the predicate ("same named gap
recurs AND no gap-closing artifact FOR THAT GAP since that lap") is
pinned once in the artifact-layout reference and cited, not re-typed
a third time. When it fires, the parent's disjunct 1 necessarily
holds, so exhaustion never asserts a state the frozen meaning
refuses; the reviewer's round-2 argument survives as the falsifier
for the superseded form. HIGH-2(v2) resolved as surfaced-not-
asserted: the mapping is contingent on c9, under conflicts,
explicitly never minted by the planner, with the lap-1 falsifier
inline. progress_exhausted confirmed not dropped.

Reviewer note adopted into item 29 second amendment: define
"gap-closing" by the completeness-checklist re-run (instrument-
determined), never the owning sibling's assertion.

Disposition: boundary CLOSED (third decompose closed). np-design
retired. All five children active in their own entries.

### recover_leased_host round 1 — 2026-08-25 — PASS (1 LOW)

All v2 contract elements realized, nothing added; item 20 handled
including the frozen-contract refusal to add outcomes from a child.
LOW (item 27(i)): the DoD states rung 1 with doer-written-artifact
binding unconditionally, but where no boot identifier exists the
binding is the weaker downstream re-execution catch — state the rung
conditionally. Rejected hypothesis on record: a forged reboot log
cannot admit a wrong host (the legitimate-delta exception is
boot-identifier-ONLY; instance-id/hostname stay hard stops).
Recovery-record shape = item 17 sixth pair.

Disposition: boundary CLOSED. np-recover retired.

### acquire_hardware_lease round 1 — 2026-08-25 — PASS (no findings)

c10 CONFIRMED by independent reviewer verification and endorsed as
written (the re-grant loop is the second-order residue of the
reviewer's own round-2 flap fix; the disqualifier list describes the
terminal condition, not the selection activity). Credits:
unconditional provenance binding on the lead-minted grant reference;
g1 correctly declines the strongest rung with a stated sibling
contrast; the vacuous-match hole caught unprompted; positive
enumeration applied to no_host_available unasked; the i3
run-wide/per-campaign scope asymmetry right and non-obvious.
Lease-record shape = item 17 seventh pair.

Disposition: boundary CLOSED. np-lease retired.

### screen_pin_and_progress re-verify (v2) — 2026-08-25 — PASS (0/0/0) [plan-reviewer-2]

All three round-1 findings resolved, no regressions. HIGH-1 booked in
the strongest available form: a booking entry opens interface_needs
with verbatim landing text for all three pieces, states its own
lead-owned-edit limit, carries a falsifier on the booking itself,
and propagates the correction to every site that previously implied
otherwise. LOW-1's replacement mechanism verified sound
(design-revision stamp comparison, source claim re-confirmed against
review_campaign_design on disk); LOW-2's guard bound declared in the
guard text. The "most recent findings entry" reading deliberately
not reopened (immaterial; should not be repaired).

Bookkeeping (adopted): the HIGH-1 landing stays OPEN against item
27(e) until the parent contract-text edit is on disk; plan-reviewer-2
verifies the three pieces in the parent revision when it lands.

Disposition: boundary CLOSED. np-screen retired.

### replicate_campaign_venv round 1 — 2026-08-25 — REVISE (3 HIGH, 0 LOW) [plan-reviewer-2]

HIGH-1: the freeze-SCREENING step (r5, well-grounded on observed
contamination) lives only in x_planning while the frozen activity
text says "replay the freeze" verbatim, and the fragment never books
the landing — a doer replays a contaminated baseline (false
replication_failed, or the placeholder installs over the editable
install). Fix: land r5's wording verbatim at the parent replay site
(item 27(k)); the v2 books it screen-shape. HIGH-2: the declared
venv retry bound does not exist — no threshold read in the node, the
only tier-2 check is unreachable mid-loop, yet the parent names this
node a budget-spending route; §9.8 fails on both branches. Fix: the
failure-classification protocol reads persisted per-route counts
before any retry and exits replication_failed at threshold (item
27(l)). HIGH-3: venv_ready hardening leg (b) claims a cross-check on
run_input_and_preflight_record, which is in nobody's consumes — lead
decision (cheap option): clause (2) restated metadata-intrinsic,
intake match demoted to corroboration, leg (b) dropped from the
claim.

Cross-boundary: replication dead ends route to breaker_tripped whose
strict ten-count meaning refuses them — adopted as the c7 amendment
(one widening closes ten-count, tier-1 exhaustion, venv dead end).
Worktree ownership decided (node owns worktree creation; widening
rides item 27(l)). Three rejected hypotheses on record.

Disposition: repair to SAME planner np-venv on
replicate_campaign_venv.v2; re-verify by plan-reviewer-2 same-thread.

### execute_attempt_loop round 1 — 2026-08-25 — REVISE (1 HIGH, 1 LOW) [plan-reviewer-2]

The attempt_accounting reading itself CONFIRMED (reconciles binding
items 3+4; limb (b) matches frozen text; split disjoint and
decidable). HIGH-1: limb (a) — host-caused failures never
budget-counted — is contradicted by frozen text twice ("Count every
attempt") and host_faulted's meaning never exempts the faulted
attempt; its only home is x_planning. Shipped as-is, ten host flaps
trip tier-2 with zero approach evidence — the exact false-outcome
defect the parent's rejected alternative names. Fix: three-piece
contract-text landing (purpose qualifier; activities unless-clause;
host_faulted not-charged + no-rollback clause, piece 3 mandatory),
booked screen-shape in the v2; parent edit rides item 27(m). LOW-1:
the repo-tracked fingerprint file has no resolvable location in the
approved graph — tier-1 silently degrades to its within-run half;
fix: layout entry names the concrete path + absent-file semantics.
Root pattern flagged as item 33 (four cross-run artifacts undeclared
in the evidence block). Three rejected hypotheses on record.

Disposition: repair to SAME planner np-attempt-loop on
execute_attempt_loop.v2; re-verify by plan-reviewer-2 same-thread.

### assemble_design_record re-verify (v2) — 2026-08-25 — PASS (2 new LOW) [plan-reviewer-2]

Round-1 HIGH resolved in substance and passes the citation test:
design_entry_identity carries the refuse-to-run precondition,
per-artifact stamping, current-id detector scoping, archive rule,
and the late-archival falsifier — and is cited at every touched site
(DoD, infeasibility variant, detector, evidence question, fourth
gameability direction, enforcement, layout booking). Round-1 LOWs
resolved.

NEW LOW-1 (adopted): the precondition + detector id-scoping fold
into contract text is carried as its own frontier item (27(n)) — the
fragment's item 5 ("no other change rides it") would steer resync
past it. NEW LOW-2 (adopted, item 31 addendum): d7's
commitment-absent gap is exempt from the detector key space,
whichever routing home item 4 gets — a lead bookkeeping slip must
never force approach re-derivation. d10/d11 stay contingent on
27(e), which stays open as its own item. d7 + comparators_
preregistered confirmed not §4.11 double-gating.

Disposition: boundary CLOSED. np-design-record retired.

### preregister_acceptance re-verify (v2) — 2026-08-25 — PASS (0/0/0) [plan-reviewer-2]

All five round-1 findings resolved and mechanically verified: zero
line-number citations remain (case-insensitive greps recorded);
every runtime-contract-bearing citation re-points to a BUILT
reference and enumerates the required sections, including the
wrong-baseline incident precedent; the dangling-section falsifier
lands at :445-449; the two-actor shape is unweakened (falsifier
verbatim — the mechanical check compares timestamps, not
authorship). LOW-1/LOW-3 correctly booked as item-27 riders; LOW-4
settled in commitment_timing with the d6 strengthening named.

Residual (not a finding): contract text carries the literal
`references/<topic>.md` placeholder — booked as item 34(a), a
final-gate grep of the built package for `<topic>` and any
surviving `.pave/` path.

Disposition: boundary CLOSED. np-prereg retired.

### draft_increment_plan re-verify (v2) — 2026-08-25 — PASS (1 new LOW) [plan-reviewer-2]

All 3 HIGH + 2 LOW resolved. HIGH-1: the v1 ledger fact is STRUCK,
the frozen purpose confirmed correct as written, and the planner
withdrew its own repair request — the ADDENDUM propagated into a
stronger activity-3 duty (overlay is the campaign's only regression
safety net) and the CPU-mode evidence question. HIGH-2 cites the
built inventory with ADDENDUM items 2-3 folded (register binds to
the inventory, never a frozen count; private-index precondition
HARD with a named silent-placeholder detection check). HIGH-3
recast as an item-27 citation. LOW-2 fixed the right sentence —
three per-direction enforcement claims with honest costs; the
planner's v3 offer is not needed.

NEW LOW-1 (adopted, rider on item 27(e)): :186 reads as though the
reviewer's coverage-trace re-check already exists — grep of the
review_campaign_design fragment finds zero coverage/trace hits.
Reword to BOOKED, and keep 27(e) + the reviewer-brief re-check note
as LINKED items so dropping one cannot silently reopen the
narrowing escape path. Correctly-absent noted: v2 dropped the
explicit item-20 non-trigger discussion — right, two outcomes.

Disposition: boundary CLOSED. np-draft-plan retired.

### run_candidate_measurements — 2026-08-25 — PASS (2 LOW) [plan-reviewer]

Atomic verdict holds; sizing citations verified against primary
text (one imprecision cuts the node's way). Reading (a)
pair-keying-as-layout: mechanism CONFIRMED — path encoding makes
the frozen count-by-path rule and the pair count the same
operation; relabeling hardening adequate (content-derived identity,
child-1 falsification, child-4 proliferation check). BUT the frozen
"per-measurement repair count" phrase contradicts the keying on its
face -> LOW-1: one-phrase substitution at the
low1_derived_repair_count landing sites + the parent loop_bounds
defining sentence (KEYING-CONTINGENT — held on c13). Reviewer books
an EIGHTH item-17 pair: defect-record path pattern +
identity-derivation rule (held on c13). Reading (b)
comparator-indicting forward completion: HALF confirmed (forward
routing = execute-never-choose, correct) / HALF unbacked — no
downstream consumer must read the record; child 4's bundle fields
lack a defect-record link while the budget-spent case IS wired
through -> LOW-2 = item 27(o), reviewer-owned miss at the measure
v2 boundary. Three rejected hypotheses on record (reference
re-capture laundering; serving_exhausted burying a co-held defect —
dead on write-ordering; budget_spent_routing_check over-enforcement
— justified).

Disposition: boundary CLOSED. np-mruns retired. Both LOWs ride the
resync bundle.

### realize_measurement_procedures — 2026-08-25 — REVISE (1 HIGH, 2 LOW) [plan-reviewer]

ATOMIC CONFIRMED after a held-open census check (17 files / 8,682
lines flat, verified directly; the reviewer's leading
two-codebase-conflation hypothesis tested and KILLED — the fragment
scopes pinned-release vs local-checkout claims precisely).

HIGH-1 (contract-level — LEAD's fix, item 27(p)): the smoke gate
("recorded before any measured run") and the mandated
re-smoke-after-repair are jointly unsatisfiable once measured runs
exist. Strict reading: unbounded livelock that spends NO budget (a
check failure writes no defect record — no exhaustion, no operator
stop; the one loop in the boundary with no bound of either §9.8
kind). Lenient reading: hollows the acceptance predicate. Fix:
scope the temporal predicate to the revision in force ("recorded
before any measured run THAT CITES THAT PROCEDURE REVISION") at 4
frozen sites; the check stays pure arithmetic (join keys already
required). Fragment echo sites :247-248 + :279-281 ride the repair.
Archiving alternative considered and rejected (collides with defect-
trail persistence). LOW-2: serving loss during smoke — retry half
adopted; stall half REJECTED as less-designed than child 3's
serving_exhausted; extend procedure_unrealizable's meaning by the
serving-re-establishment disjunct = item 27(q); fragment adds the
second evidence branch. Standing note: exclusivity is not
exhaustiveness — ask every remaining fragment for BOTH. LOW-3: the
tier-docs claim is file-listing-grade sourced to a DELETED temp
clone — move facts->assumptions, widen falsifier (a) to
thin-or-absent docs. Stage-4 sweep booked as item 34(b).

Disposition: repair to SAME planner np-mproc on
realize_measurement_procedures.v2 (fragment sites + LOW-2 branch +
LOW-3; pair-key text UNTOUCHED pending c13); contract sites ride
item 27(p)/(q).

### capture_baseline_reference — 2026-08-25 — REVISE (2 HIGH) [plan-reviewer-2]

HIGH-1: g4's falsifier argues FOR pair keying with false reasoning
— the frozen bound is "three repair passes per declared measurement
WITHOUT NEW EVIDENCE"; three distinct defects are three passes each
carrying new evidence, consuming ZERO budget under per-measurement
keying, so the miscount g4 cites cannot occur. Strike the
falsifier, fix the pair leaks (g3 :234-235, i3 :348-351) — and the
keying is run-wide (all four sibling briefs), so the correction is
run-wide -> folded into c13. HIGH-2 (lead-routed = item 27(t)):
this node's frozen re-entry rule (re-capture only comparisons whose
procedure changed) is underivable from child 1's frozen contract —
nothing requires revision versioning or touched-comparison naming;
honest re-capture-all then FAILS this node's own settlement
(changed reference file, no matching trigger) = deadlock. Fix lands
in realize_measurement_procedures' frozen text; np-mproc's fragment
already realizes it. c11 CONFIRMED + refinement: the
durable-host-state definition must name cache writes explicitly
(i1's falsifier covers it by name). c12 CONFIRMED (smallest change
+ destination right; outcome-name misdescription noted as
residual). Checked-and-clean list on record (skip handling earned;
consumes complete here; item 20 correctly not triggered; g1/g2
hardenings right; atomic verdict sound).

Disposition: REVISE booked; repair HELD on c13 (HIGH-1
keying-contingent; HIGH-2 is lead resync work). np-mbase retained.

### stabilize_and_package_evidence — 2026-08-25 — REVISE (3 HIGH) [plan-reviewer-2]

HIGH-1: the pair keying contradicts the frozen per-measurement
bound at four sites ("pair"/"defect identity" absent from the whole
parent) and loosens it without limit — N x 3 passes with nothing
bounding N; the fragment's own gameability entry hardens only
free-typed identities, not genuine variation in the failed
property. Reviewer asked for the establishing record; lead settled
provenance: item 26 second append (adopted at the measure v2 PASS,
never landed in contract text) -> substance folded into c13.
HIGH-2 (PROMOTED; reviewer overturns its own prior low3 judgment on
new cross-measurement evidence): measurement X fresh in-budget
defect + measurement Y budget-spent => both outcome meanings true,
no frozen tie-breaker, fates diverge, no downstream catch,
small-fast chooser. Fix: declared_measurement_unproducible settles
only when NO declared measurement retains budget = item 27(r),
INDEPENDENT of c13. HIGH-3: the frozen contract mandates procedure
links but measurement_procedure_record is absent from frozen
consumes — one token at :192-193 = item 27(s). Question (c)
answered: read-count home = campaign design record; declare the
minimum re-read SPACING with the count; assemble_design_record
checklist row; natural author preregister_acceptance = item 27(u).
Checked-and-clean: both lead-minted proposed_checks earned; DoD
rung 1 genuine; meaning-fit verified.

Disposition: REVISE booked; repair HELD on c13 (HIGH-1
keying-contingent; HIGH-2/3 are lead resync work). np-mstab
retained.

### CONFLICT c13 — 2026-08-25 — cross-reviewer, pair keying [lead]

plan-reviewer (adopter of item 26's second append) re-confirmed the
pair-keying mechanism on run_candidate_measurements and asks only
the frozen-text substitution; plan-reviewer-2 filed HIGHs against
the keying on both its boundaries (the no-new-evidence qualifier
already does the job; nothing bounds N identities per measurement —
the designed stop becomes unreachable). Provenance settled (adopted
item, never landed in contract text). Substance open: cross-briefs
sent to both reviewers with the opposing evidence verbatim; lead
adjudicates on their positions. Candidate resolutions recorded in
the c13 frontier entry. Held on c13: np-mstab + np-mbase repairs,
mruns LOW-1 substitution, the EIGHTH item-17 pair.

### CONFLICT c13 — position 1/2 (plan-reviewer) — 2026-08-25

Argument 1 CONCEDED in full: the frozen no-new-evidence qualifier
already delivers everything the pair key was invented for; the
adopter withdraws its own item as a propagated false positive.
Argument 2 REJECTED as a case for reverting: per-measurement +
qualifier is identically unbounded under the same input (every
new-property lap zero-charges, the count never reaches three), so
resolution (1) fixes nothing. ROOT CAUSE named: the parent
loop_bounds sentence specifies two different numbers for one budget
— a judgment-filtered BOUND (":396-397 without new evidence") and a
mechanically unfiltered DERIVATION (":400-405 count event files by
path", which counts every record). Each reviewer anchored on one
clause; both failure modes are real under their respective readings.

RECOMMENDED (resolution-3 variant): a TWO-TIER bound, both tiers
settling the existing declared_measurement_unproducible — no new
outcome, route, or state field. Fast tier: design_campaign.v3's
approved exhaustion-predicate shape (same defect recurs AND no
defect-closing evidence FOR THAT DEFECT since the lap that named
it). Backstop tier: an absolute per-measurement pass ceiling,
charged on every pass regardless of new evidence, derived exactly
as the frozen derivation clause already reads (count all
defect-record files by path) — the derivation becomes honest as the
backstop instead of contradicting the bound. Precedent: the
approved two-tier hardware breaker. Judgment burden of the
qualifier declared acceptable ONLY with the backstop (the deciding
seat is under standing pressure to find novelty; a filter that
favors the doer is not a bound — no novelty argument reduces a
count of files on disk). Heaviest failure mode: the unbounded loop
(irreversible leased-host spend, no operator-visible stop past
gate 2) over recoverable premature exhaustion.

Held-item dispositions: LOW-1 substitution WITHDRAWN (amend
loop_bounds to the two-tier text, not pair language); EIGHTH pair
SURVIVES NARROWED (single-source defect-record shape for re-entry
targeting + the recurrence predicate; identity-derivation no longer
budget-load-bearing); mbase g4 falsifier strike CONCEDED with a
condition (strike as part of resolving the ambiguity; once the
backstop lands, premature exhaustion at the ceiling is possible
again and a false-exhaustion falsifier is worth re-verifying in
some form). Consequential shrinks: mruns defect_identity_stability
entry + assembly item, and mproc's verbatim-pair-key copying at
:209-221, reduce to "revision entries name the defect they repair".

Awaiting position 2/2 (plan-reviewer-2); lead adjudicates on both.

### replicate_campaign_venv re-verify (v2) — 2026-08-25 — PASS (0/0/0) [plan-reviewer-2]

All three HIGHs resolved. Booking discipline ADOPTED as the register
standard: need / landing_site / clause_verbatim /
falsifier_if_unbooked / status as separate fields. HIGH-1: r5
wording verbatim incl. the logging sentence, targeted at the parent
replay site, falsifier names both failure directions. HIGH-2:
realized in-node AND booked — threshold read is a real mechanism,
replication_failed gained the declared threshold disjunct,
mixed-cause precedence preserved, propagated to evidence questions
+ a new enforcement guard; the fragment states plainly the in-node
realization does not ship. HIGH-3: clause (2) genuinely
metadata-intrinsic; intake demoted to corroboration; leg (b)
dropped with the lesson stated as a rule. Boundary CLOSED. np-venv
retired.

### execute_attempt_loop re-verify (v2) — 2026-08-25 — PASS (0/0/0) [plan-reviewer-2]

HIGH-1: three pieces verbatim with sites, piece 3 not-optional with
reason; falsifier_on_booking covers both directions and adds one
the reviewer had not stated (piece 3 absent -> breaker never
re-trips on resume). BATTERY CORRECTION on record: the
stale-comfort grep is not zero hits — :201-207 carries the REPAIRED
sentence (comfort scoped to precedence with the counting-exemption
pointer); do not falsify the claim on that line. LOW-1 fixed on
both axes; the operational absent-file determination is the
auditable half. Boundary CLOSED. np-attempt-loop retired.

### CONFLICT c13 — position 2/2 + ADJUDICATION — 2026-08-25 [lead]

Position 2/2 (plan-reviewer-2): concedes the builder failure mode
is REAL — the "without new evidence" qualifier is x_planning-ONLY
(:397 sits in extensions; neither outcome meaning nor the
activities line carries it, and no contract text states the number
three), its own where-it-lands rule applied against itself. The
missing piece is the QUALIFIER, not a partition. The novelty
judgment is already MECHANIZED as a recorded NEW-relative-to-prior
field — file arithmetic in both designs, so the judgment-burden
dichotomy dissolves. Pair keying keeps the filter anyway; option
(2) = option (1) + three extra parts; §9.14.2 favors (1).

ADJUDICATION (lead synthesis — both concessions accepted, both
residuals closed): (i) budget keying reverts to PER-MEASUREMENT;
(ii) qualifier + magnitude + keying land in CONTRACT TEXT at the
three enumerated sites + loop_bounds (also closes the undeclared
§9.8 counter gap reviewer-2 flagged); (iii) reviewer-1's UNFILTERED
BACKSTOP lands as tier 2 — suggested NINE total passes per
measurement (3x the filtered budget), counted by path regardless of
new evidence; closes the unbounded branch under genuine per-lap
novelty that option (1) alone leaves open (reviewer-1's
counterfactual, unrebutted); the frozen derivation clause becomes
the backstop's honest derivation; (iv) pair keying struck run-wide
(open fragments via the released repairs; closed fragments mruns +
mproc amended at resync to "revision entries name the defect they
repair"); (v) EIGHTH pair booked narrowed (re-entry targeting +
recurrence/novelty predicate; no budget-key role); (vi) g4
falsifier struck now, with reviewer-1's condition booked: at
resync, re-verify whether an at-ceiling false-exhaustion falsifier
returns in some form. Full text: item 26 fifth append + the c13
conflict entry. Repairs released: np-mstab + np-mbase on v2 paths.

### CONFLICT c13 — acceptance round — 2026-08-25 [both reviewers]

Both reviewers accepted the adjudication without re-litigation.
Reviewer-1 named reviewer-2's x_planning-only container check as
the settling fact and adopted it as a standing habit; its
acceptance carries one re-verify condition (booked, item 26 sixth
append (c)): the NEW-relative-to-prior field must be reader-
recomputable, not a writer assertion — else tier 1 is advisory and
must be recorded as such. It also pinned the one-instrument-two-
predicates shape for the layout entry (both tiers count the same
directory; tier 1 filters on the novelty field). Reviewer-2
conceded the backstop was necessary (its own edit set walked
through by the genuine-novelty counterfactual) and caught the
material gap in the adjudication as issued: the landing set omitted
run_candidate_measurements, the SECOND budget reader (five contract
sites) — without the extension, reviewer-1's LOW-1 survives at runs
while being fixed at stabilize. Landing set extended (item 26 sixth
append (a)); at-ceiling behavior rides runs' existing :128-130
forward-completion clause (d); reviewer-2's reachability near-miss
recorded as a checked rejected hypothesis (e). Reviewer-1 has
pulled the item 34(b) plugin-024-delta sweep forward and is running
it now.

### realize_measurement_procedures re-verify (v2) — 2026-08-25 — PASS [plan-reviewer]

Verified by sentence-level normalized diff of v1 against v2, not
the delta map. HIGH-1 resolved at all FOUR fragment echo sites (the
planner's propagation grep found two beyond the named pair — the
right method), with the WHY recorded so a later editor cannot
silently regress it; parent sites booked, not edited. Fourth parent
site ruled INTO the 27(p) sweep via a three-category container
split (contract text / minted-from planning text / pure planning
echo — the last lands anyway to keep the fragment's own falsifier
grep usable). LOW-2 resolved stronger than asked (per-disjunct
enumeration makes the negative claim reader-falsifiable). LOW-3
resolved and improved: the census carries a bearing clause stating
the honest ground on which the atomic verdict survives a docs
failure. c13 shrink resolved with lineage discipline (supersession
described, not erased); assembly item 2 makes the two field jobs
non-substitutable in layout text. Frozen pieces: substance
confirmed by diff everywhere bytes changed. One residual
deliberately not filed — smoke-check ordering join's run-record
side — booked as a one-clause layout note at assembly.

Disposition: boundary CLOSED. np-mproc retired.

### Item 34(b) sweep — 2026-08-25 — COMPLETE, essentially clean [plan-reviewer]

Every plugin-024-delta.md citation in the planning directory
classified against the report's own epistemic self-labels. np-mproc
v1 was the only real listing-grade-as-fact defect; v2 fixed it. One
residual (citation hygiene on a true fact): the regression-matrix
thresholds-in-code clause cites three non-supporting sources while
the uncited validation-and-acceptance.md:57-61 proves it — one-line
re-source at resync. The reviewer killed its own sweep's headline
after reading both sources (doc-location vs code-location are
different propositions). Model citation practice identified (matrix
:55-59: list-only evidence for volume-only conclusions). Stage-4
narrowings booked: unread-heavy findings are unused rather than
misused, and the system-map ADDENDUM intermediary channel folds
into the rendered-view pass. Landing-set sweep still running.

Addendum (2026-08-25): the mproc v2 PASS is confirmed on a fully
examined diff. The reviewer self-caught that its first pass
windowed the diff (head/tail over 195 lines, 15 middle lines
unexamined) and read the gap before confirming — nothing new in it;
the gap contained regression protection (the full HIGH-1 failure
scenario recorded inline in the rationale). Both post-forward
citation lines were already inside the diffed state. Process
correction adopted by the reviewer: no verdict off a windowed diff
without confirming window coverage — third instance this round of
the assumed-total-enumeration error, now closed. Landing-set sweep
output will arrive split for item 27(h)'s audit format.

### stabilize_and_package_evidence re-verify (v2) — 2026-08-25 — PASS [plan-reviewer-2]

Rider question answered: the new-evidence field is
reader-RECOMPUTABLE — a list of files diffable against the prior
record's set from two on-disk artifacts, neither trusted; the
novelty-inflation entry claims inflation cannot UNBOUND (not cannot
happen), answering reviewer-1's counterfactual structurally. HIGH-1:
pair grep zero, two-tier per-measurement throughout, honest
citation-not-claim on frozen text. HIGH-2: fixed in the sentence
("NO declared measurement retains repair budget"), booked as
shippable contract prose at a line-specific site — the reviewer's
own acknowledge-only hypothesis disproved. HIGH-3: booked, v1 claim
retracted by name. Residual flagged: resync works from the REGISTER
(nine-site landing), not the fragment's three-site enumeration.
Boundary CLOSED. np-mstab retired.

### capture_baseline_reference re-verify (v2) — 2026-08-25 — PASS [plan-reviewer-2]

g4's false falsifier struck in full (reasoning absent, not
relabeled); pair residue zero; 27(t) booked with a real site and
the np-mproc dependency named; c11 cache-write refinement landed in
three places by name. Credit on record: np-mbase's propagation
sweep caught two echoes beyond the reviewer's cited sites.
Boundary CLOSED. np-mbase retired.

### STAGE-3 REVIEW COMPLETE — 2026-08-25

All 37 boundaries reviewed. Zero open findings on reviewer-2's nine
units. Item 35 minted from reviewer-2's process note: at the
whole-graph round, mechanically confirm every register-booked rule
is IN the assembled workflow.pave.yaml (four REVISEs this run
shared the x_planning-vs-contract shape; the built file carries no
x_planning, so only that sweep catches a dropped landing).
Outstanding pre-assembly: reviewer-1's landing-set completeness
sweep; then the resynchronization bundle.

### Whole-bundle round — 2026-08-25 — [plan-reviewer-2] gate-2 sweep: REVISE -> repaired

19/19 booked clauses PRESENT (two ruled N/A: x_planning-only, stripped
at build, substance ships in contract text). ONE HIGH via the citer
re-check: pair-1 key divergence — layout §4.2 pair 1 said
(label, location, defect class) while the shipped graph keys on
(increment id + surface + defect class) citing a non-shipping planning
file, and review_implementation declared no shape. LEAD RULING: the
coarse triple is the deliberate binding (v5 binding_review_interface;
the no-new-evidence qualifier guards the false-fire direction) — the
LAYOUT end was amended. Six-piece repair landed (layout §4.1/§4.2/§4.3,
producer obligation in root.v7 second touch d, v5 citation repoint,
§4.4 wording + two overlay rationale strips = the two LOWs). MEDIUM
(sweep existence-only) fixed: f8_sweep now 72 presence + 10 exact-count
+ 5 negative rules. Both amendment spot-checks PASS. R2 re-verify of
pair-1 both ends PENDING.

### Whole-bundle round — 2026-08-25 — [plan-reviewer] brief-vs-bundle + F9 + 34(b) + D9: REVISE -> repaired

Diagrams fully faithful (all 89 edges, zero fabrications); ADDENDUM
channel verified additive; 27(g) and root loop_bounds touch (c) PASS.
Seven findings, all dispositioned FIX, all landed same day:
- HIGH-1 rederiver model:fable undispatchable here -> override removed,
  seat inherits session model (plan :27/:57, traceability :28, brief §4).
- HIGH-2 brief §6 omitted the system-map LOAD-BEARING GAP -> "What you
  accept" now leads with acceptance-machinery-is-new-construction.
- MEDIUM-1 item 19's booked seat-binding had not shipped (frozen rubric
  only) -> binding landed as booked in scan v2 rubric activity
  (assembled :804). Measurer/adjudicator half already PASS (item 26).
  Register hygiene: "item 13" does not exist — lead assignment error.
- LOW-1 scope_next_increment.plan_satisfied now binds
  increment_evidence_records (evidence another seat wrote).
- LOW-2 brief §5 states totals (12 prohibitions, 6 guards, strongest
  shown). LOW-3 legend explains gate 2 as edge check. LOW-4 header
  self-certification dated + pointed at this file.
Rejected/killed claims (7) and residual risks recorded in reviewer
message; WISH x4 carried to Stage 5. Re-assembled: 31/89 validate PASS,
sweep 72+10+5 ALL PRESENT. R1 re-verify PENDING.

### Re-verify round — 2026-08-26 — pair-1 CLOSED, positional set 12/12, three new LOW + one MEDIUM all fixed

R2 accepted the pair-1 ruling and verified all six repair pieces
("this node restates neither" credited as cite-not-restate made
explicit). R2's new MEDIUM — limb 1 dropped PASSING against the §4.3
pin, silently inert in the stuck case — fixed: "passing" at both
limb-1 sites; count rule pins all four limb statements. R2's unrated
question (same triple recurring while acceptance keeps passing) RULED:
limb 1 fires — the no-new-evidence anchor is the answering record, so
a re-raised triple with nothing newer satisfies the conjunct; the
anchor clause now ships in no_new_route's meaning (doer-reviewer
disagreement loops route to re-derivation, never spin behind gate 2).
R1: positional 12/12 PASS with two tag corrections (27(h) home =
assemble_design_record; 27(r) both halves in stabilize); D9 corrected
to five evidence-free outcomes and ruled PASS, no finding; W1 pair-1
PASS with the calibration that the consumer end was already correct.
R1 LOW-5/6/7 fixed: autoport classification + runner-default boundary
landed in verify_run_preconditions purpose; last two version suffixes
stripped, .v1-.v7 negative rules added. Assembled 31/89 PASS; sweep
75 presence + 11 count + 12 negative ALL PRESENT.

### R1 final re-verify — 2026-08-26 — PASS 7/7; lane clear

All seven bundle-round repairs verified at current citations (HIGH-1
credited for environment-scoped constraint framing; LOW-2/LOW-3
verified as true claims, not just present text). Item 13 closed as
nonexistent. LOW-5/6/7 dispositions: FIX, landed prior round (R1's
re-check crossed with the fix). LOW-8 (new): the HIGH-1 fix text
overclaimed "inherits session top tier" — fixed by dropping the claim
in plan/traceability/brief ("inherits the session model by design").
R1's lane clear for render; awaiting R2's four-limb count re-verify.

### R1 HIGH-3 — 2026-08-26 — anchor divergence graph vs layout §4.3: fixed layout-side

The limb-1 anchor repair (R2 MEDIUM) landed only in the graph; layout
§4.3 still anchored the qualifier to the naming lap, specifying the
opposite behavior on the recur-after-answer lap — and §4.3 is the
declared authority pair 1 routes the qualifier to. Fixed layout-only:
shape line anchor-parameterized with "the anchor is part of the pin";
impl binding = answering-record anchor with the fire-on-re-raise
consequence; design binding = naming-lap anchor with rationale; pair-1
falsifier direction 2 states the narrowed protection as deliberate.
Sweep 78+11+12 ALL PRESENT. Sixth partial-landing instance; rule
recorded: a §4-pinned predicate change touches two files by definition.

### R2 HIGH (limb-1 item scoping) — 2026-08-26 — fixed both layers

R2 accepted the anchor ruling and the four-limb PASSING fix, then
found the next layer: limb 1 lacked "for that item" — unscoped it
fires never; scoped it contradicts the falsifier prose claiming the
qualifier eliminates the converging-work false fire. Fixed: item
scoping at both limb-1 sites (count == 4); both falsifier homes
(§4.2 pair 1, §4.3 falsifier note) now state the qualifier NARROWS,
never eliminates — the post-answer residual fire is deliberate,
bounded at one visible re-derivation round. R1's asymmetry rationale
added to §4.3 (passing vs finding-closed are different predicates).
R2's LOW: "passing evidence records exist only on pass" (one word).
Also this round: R1 confirmed HIGH-3 against pre-fix mtimes (crossed);
layout fix already on disk matches R1's spec including leave-§4.8-alone.
Assembled 31/89 PASS; sweep 78 presence + 13 count + 12 negative ALL
PRESENT.

### WHOLE-BUNDLE GATE CLOSED — 2026-08-26 — PASS, both reviewers

R2 CONFIRM CLEAR: three-element table (passing / for-that-item /
anchor) complete at all four limb statements; full-phrase count 4;
all count rules green at predicted numbers; verified against mtime
21:12:46 twice. R1 lane closed prior round (three HIGHs, one MEDIUM,
eight LOWs all fixed and verified; positional 13/13; D9 PASS).
R2 residual note adjudicated no-finding (limb-2 naming-lap anchor +
fourth conjunct declared at citer only — endorsed by §4.3's own rule).
No BLOCKING or HIGH outstanding anywhere. Render authorized; user
approval next, to be recorded verbatim in reviews/user-plan-approval.md.

### R1 LOW-9 (post-gate) — 2026-08-26 — FIX landed, layout-only

R1, re-verifying the anchor text at mtime 21:12, PASSED its lane and
filed one LOW: §4.3's impl-binding pin is written for limb 1 only
(findings history; anchor = answering record) while the graph's
detector has two limbs — limb 2 (the boundary's own lap records; key =
named gap or stuck increment) anchors on the NAMING LAP, so the pin's
absolute "never the lap that named it" literally contradicts the graph
it pins. The graph is right; the pin was under-scoped. Disposition:
FIX (one clause, per R1's own spec) — §4.3 impl binding now ends "That
pin binds limb 1 alone…" naming limb 2's key, its naming-lap anchor,
and why it is safe (key and clearing evidence settled by the same
instrument, the item's declared acceptance — no answered-yet-recurring
state can exist). Layout-only; graph, brief, and all four limb
statements untouched. Sweep +1 rule ("binds limb 1 alone"):
79+13+12 ALL PRESENT, counts unchanged. Sent to R2 per their standing
§4.3 re-verify boundary; R1 notified. Gate verdict unchanged (LOW
gates nothing); brief already rendered, user approval pending.

Addendum (same round): R1 verified LOW-9 PASS on sight (mtime 21:22)
and offered one unfiled cleanup — the design binding's "correct here
and only here" became false once the new clause put limb 2 on the same
naming-lap anchor. Fixed immediately (mtime 21:25): "correct here, and
on the detector's lap-records limb for the same reason" (R1's wording).
Two words of semantics, zero routing effect; counted phrases and the
"no answering event exists for gaps" presence rule undisturbed; sweep
79+13+12 ALL PRESENT. Delta sent to R2 mid-read to prevent a crossing.
R1 lane final: nothing outstanding, retained for the final-skill gate.

Addendum 2 (same round): R2 CONFIRM on all three LOW-9 asks — (a)
limb-2 anchor semantics match graph :1154/:1223, (b) "never" scoped
without weakening limb 1 (count still 1, inside limb 1's own
sentence), (c) no contradiction with the adjudicated residual note.
R2 additionally verified the clause's premise holds for BOTH limb-2
sub-keys (stuck: complementary outcomes of one instrument; named gap:
the detecting check IS the item's declared acceptance per graph
:1138-1141) and that the d7 exemption is what keeps the premise true
— same-instrument is designed, not incidental. CROSSING (ninth): R2's
read was mtime 21:22:50, predating the 21:25 "only here" cleanup, so
delta item (d) remained owed. R2's own suggested phrase landed
verbatim (mtime 21:27): "and a coverage gap cannot recur against the
very check that is its acceptance" — closes the illustration for the
gap sub-key. Sweep 79+13+12 ALL PRESENT. One bounded R2 confirm
requested for (d) + the phrase. PROCESS NOTE (R2, carry to Stage 5
W2 list): an adjudication that lives only in review correspondence is
lost at the next round — pin rulings into the artifact they rule.

Addendum 3 (same round): R2 SPLIT delta item (d) at mtime 21:25 — the
anchor cross-reference AGREES with limb 2, but "for the same reason"
imported this bullet's rationale ("no answering event exists") to a
site where it is FALSE: limb 2's keys CAN receive answering events (a
passing record on a stuck increment), and its safety ground is the
LOW-9 clause's same-instrument argument, not answering-event absence.
Failure path R2 named: a literal reader concludes there is nothing for
the passing-record conjunct to check and drops it at :1153-1154 /
:1222-1223. Fixed with R2's two words (mtime 21:28): "on the ground
given above." Sweep 79+13+12 ALL PRESENT. R2's read crossed the 21:27
phrase landing (tenth crossing) — one combined confirm requested: the
two-word fix + the gap-sub-key phrase. R1 verified the 21:25 cleanup
and closed their lane, with a self-correction booked: a line-based
grep on a hard-wrapped reference returned a false miss for a phrase
straddling a wrap — flatten first, exactly as the sweep does.
STAGE-5 CARRIES (both booked to W9): (1) R1 — any presence check
against pinned references must flatten whitespace first; (2) R2 —
rationale prose in a pinned reference needs the same citer-agreement
check as the predicate it explains (three seam-claim instances this
chain: PASSING fix left scope, scope fix left "never", "only here"
fix introduced "same reason").

Addendum 4 (same round): message-direction crossings untangled on the
record. Correction to addendum 3: R2's (d) read WAS at 21:25:43 (their
stat line is the authoritative read record) — what crossed was their
LOW travelling toward me while my fix-landed notice travelled toward
them; the fix ("on the ground given above", their exact words) was on
disk at 21:28:53 before their re-statement arrived. R2 CONFIRMED
sentence 2 (gap-sub-key phrase) against primary evidence :1138-1141.
R1 independently confirmed the same premise for ALL THREE gap classes
(two instrument-identical; record-gap not instrument-identical but its
acceptance OUTPUT is exactly what the detector reads — passing still
implies the detection cannot re-fire). R1 RESIDUAL booked, no change
requested: the d7 exemption is LOAD-BEARING for two rationales — the
design binding's "no answering event exists for gaps" is true only
BECAUSE d7 removes the commitment-absent gap (the one genuine
answered-yet-recurring state) from the key space, and limb 2's
rationale inherits the dependency by analogy. Narrowing or removing d7
would silently falsify both rationales and reopen the naming-lap
anchor question on both sides — a future d7 touch is a three-bullet
edit, never local. STAGE-5 CARRIES added to W9 set: (4) R2 — check a
delta notice against the reviewer's reported stat mtime before
assuming a stale read; (5) R1 — d7 coupling above.

### LOW-9 TAIL ROUND CLOSED — 2026-08-26 — PASS, both reviewers

R2 CONFIRM both sentences at mtime 21:28:53, VERDICT PASS, lane closed
for good: "on the ground given above" 1 / "for the same reason" 0 /
gaps antecedent 1 and scoped to gaps; gap-sub-key phrase 1 resting on
:1138-1141; all counts at table values; graph unchanged. LOG
CORRECTION per R2: sentence 2 was CONFIRMED-THEN (at 21:27:25, in
their previous message) not confirmed-now — the phrase was verified
BEFORE the two-word fix landed on top of it; addendum 3's "unseen"
was wrong, tenth crossing, third in the same direction. R1 verified
the same fix independently, checked R2's reasoning before conceding
(the right discipline), and OWNED the root cause: the "same reason"
wording verified that "only here" was stale but never verified the
pointed-at reason transfers to the limb it was extended to —
introduced while fixing a smaller instance of the same class.
STAGE-5 CARRY #6 (R1): a two-word edit to a rationale still changes
what the sentence claims — it needs the same antecedent check as new
text. R2's closing observation, recorded: every defect in this
five-round chain was a TRUE clause that had not reached all of its
referencing sites — never a wrong idea; propagation, not conception,
is the failure mode, and the W9 rules should be applied MECHANICALLY
at build, not left to a reader noticing. R1: the two-lane structure
earned its cost — each reviewer caught an error the other authored.
Both lanes clear. Nothing open anywhere. User approval remains the
only open item of Stage 4.

Addendum 5 (closing nuances, R2): sentence 1 was confirmed ONCE (the
21:28:53 confirm in their PASS message), not twice — the last exchange
was a same-bytes re-confirm, eleventh crossing, thread closed. On
mechanism for the record-gap class: R1's formulation is the recorded
one — the acceptance OUTPUT is exactly what the detector reads
(passing produces the artifact whose absence was the gap, so the check
cannot then find it); R2's "same instrument" holds by the graph's own
declaration at :1138-1141, and both readings converge, so the shipped
phrase needs no change. d7 is now load-bearing THREE ways (the
exemption itself, the design binding's naming-lap rationale, limb 2's
rationale by analogy) — the three-bullet-edit guard at W9 carry #5 is
recorded AHEAD of the fact, unlike the five propagation failures this
boundary hit. R2 stays live on the §4.2 pair-1 / §4.3 / limb-statement
boundary through Stage 5; re-verifies against the three-element table.

### R1 brief color delta — 2026-08-26 — PASS + LOW-10 fixed

R1 PASS on all three asks: styling strictly tail-appended in all 7
blocks (zero content lines after the first styling line); all 31 node
ids resolve to the graph, endpoints match control_endpoints; §2.3 and
§2.6 re-verified edge-by-edge with dashed indices independently
re-derived — both match. §4 investigator wording CONFIRMED correct
(screen_pin_and_progress roles [investigator]); the old attribution to
implementer was the error. Recorded inference: byte-identity of the
other five blocks is inferred (untracked file, no prior copy), backed
by tail-placement + graph-faithfulness. LOW-10: the legend claimed
colors derive from "the graph's first-listed role" — false at
rederive_approach ([investigator, lead] but red for the dedicated
rederiver AGENT seat) and verify_run_preconditions ([lead,
investigator] but blue for the investigating seat). Colors right, rule
over-claimed — same false-as-written standard as the "same reason"
LOW. FIXED: legend now names both deliberate exceptions; gray row
narrowed to stage-level pointers; pointer color rule disclosed in the
legend (single-node pointer carries the target's seat color); dashed
gloss widened to routes into or out of re-derivation. R1's category
check on §2.6 indices 15/16 recorded (true category, incomplete gloss
— now fixed by the same edit).

Addendum (LOW-10 closure): R1 verified all four sub-fixes at 07:51:35,
including edge-by-edge re-derivation of §2.4 (the widened gloss's new
claim — recover_leased_host loop dashed BOTH directions, indices
4/7 in and 8/9 out; no_host_available correctly solid as a forward
exit; rederive_approach's out-edges dashed at its only real-node
appearance). PASS scope recorded precisely: §2.3/§2.4/§2.6
edge-verified with independently re-derived indices; all seven blocks
verified for tail-only styling, node-id resolution, endpoint match;
remaining four blocks' content lines rest on the recorded inference.
Brief color amendment fully closed. Both lanes clear; user approval
remains the only open Stage-4 item.

### NKI amendment round — R2 CONFIRM (boundary), R1 primary in flight

R2 CONFIRM at assembly 08:22:16/125,370 (layout untouched 21:28:53):
producer obligation verbatim; appended substrate sentence correctly
ORDERED (after the obligation, so "material finding" is a defined term
with a mandated shape when used — the citer/authority seam avoided by
construction); all continuity counts hold (triple 3/2, for-that-item
4, full-phrase 4, bare form 0). Substantive finding in the amendment's
favor: substrate findings inherit the §4.1 shape and binding triple BY
CONSTRUCTION (routes into the existing material-finding channel), and
the rule's likeliest failure mode — a torch fallback passes CPU
acceptance more readily than an NKI kernel while the substrate finding
stands — is exactly the passing-vs-finding-closed disagreement case
the answering-record anchor was hardened for: identical triple
re-raised after a passing repair fires no_new_route. No new detector
machinery needed. R2 UNRATED POINTER (outside their lane, forwarded to
R1's ask (a)): the reviewed rung should be checked deliberately —
once designs DECLARE kernel-class increments, a declaration-scoped
mechanical scan (torch-op implementations on declared kernel-class
surfaces) becomes possible at low marginal cost atop the existing
import-scan machinery; whether to strengthen is the enforcement
record's call. R2 lane closed, nothing owed.

### R1 NKI-amendment review — REVISE — all three findings fixed

R1 verdict REVISE (post connection-drop resume): HIGH-4, MEDIUM-2,
LOW-11. HIGH-4 (the finding that mattered): every enforcement site was
scoped to the doer's own kernel-class classification — an increment
never LABELED kernel-class produced no declaration, passed the
completeness self-check (no substrate line to fail), gave gate 2
nothing to contradict, and left review_implementation nothing to match
against; the user's own DSA-indexer example ships as torch with every
gate green. Enforcement scoped to a doer-controlled classification is
a self-report wearing a rubric (§5.3.1); the design's own
anti-omission device (:1085 patch-register none-declaration) had not
been carried over. FIXED, R1's three clauses: (1) every increment
records a substrate decision (explicit non-kernel-class when not
kernel-class) - silent classification cannot exist; (2) substrate
register required by the completeness self-check, mirroring :1085;
(3) both rubrics challenge the classification itself. MEDIUM-2:
P13's why-not-stronger did not survive - R1 inverted R2's fidelity
predicate from absence-of-torch (false-fires on kernel boundary
plumbing) to PRESENCE-of-NKI on the doer's own declaration (zero NKI
usage in a declared-NKI increment = exact contradiction, precisely
detectable, rides record_changeset's existing scan machinery). FIXED:
substrate-fidelity check + transcript (fourth world-produced
component) + coverage-gap class (d) + both work-item enumeration homes
propagated (W9 applied); P13 rewritten as SPLIT rung (mechanical
presence + fidelity, reviewed classification) with the residual named
(wrong classification surviving both reviews; bounded by gate-2 user
approval). R1's caveat recorded: the mechanical fidelity check
CONCENTRATES the escape incentive on non-declaration - which is why
c1/c2 rank above it. LOW-11: NKI increments name the simulator-path
CPU-mode acceptance route (NKI_SIMULATOR=1; the pin gutted nki/ and
deleted nki_cpu_sim - the design names its harness). (b)-(d) answered:
escape was non-declaration (closed); propagation audit found the
self-check omission (folded into HIGH-4); requirements recording
"adequate, better than minimum". Re-assembled 31/89 PASS; sweep
89+16+12 ALL PRESENT. USER CONTEXT to surface: nki/ gutted at pin -
more work qualifies as "not already provided" than the rule's wording
suggests. R1 re-verify + R2 boundary confirm dispatched.

### R1 NKI-amendment re-verify — PASS on all three items

R1 PASS (read 08:49:41 graph / 08:49:09 enforcement record; post-fix
assembly, crossing resolved). (1) HIGH-4 closed: R1 re-ran their own
failure scenario — the bypass path is now "lie on the record" against
two independent adversarial rubrics with the register presented at
gate 2; c2's register requirement is instrument-determined per layout
§4.3 (a real check, not a reminder); residual (wrong classification
surviving both reviews) ruled irreducible at the reviewed rung, not a
gap. (2) P13 split honest, all four elements: split stated; why-not-
stronger preserves R2's boundary-plumbing counter-consideration as the
REASON for the presence-predicate shape; §9.14.1 precision claim
matches graph :1352-1356; concentration caveat + residual both named
("bounded" ruled the honest word — user unlikely to overturn a
kernel-class judgment, row does not overclaim). (3) Class-(d)
propagation complete: three enumeration homes (:1392-1393, :1169-1170,
:1275) + two producing sites (:1352, :1378); seven substrate-fidelity
occurrences, none stray; both count corrections independently
confirmed on R1's flattened read (non-kernel-class declaration == 4;
hit sites :1170/:1392 only). R1 PROCESS NOTE booked to W9: the
class-(d) two-home propagation and the line-wrap count catch were the
first PROACTIVE applications of the partial-landing rules (after ten
reactive rounds) — the W9 rule set started paying. R1 lane closed on
the amendment; idle, retained for final-skill gate. R2 boundary
confirm #2 still in flight — gate ask waits on it.

### R2 boundary confirm #2 — CONFIRM (a)(b)(c); rung pointer withdrawn; LOW-12 filed and fixed

R2 CONFIRM at 08:49:41/127,034. (c) four limb statements byte-intact
(full phrase == 4 at :1179/:1183/:1243/:1252, shifted +29 lines, text
unchanged; for-that-item == 4, triple == 3, bare form == 0; layout
untouched at 21:28:53). (a) producer obligation verbatim at :479-483,
still PRECEDES both substrate sentences; rung parenthetical gone;
classification-escape sentence routes through "material finding too"
so it inherits §4.1 shape + binding triple by construction. R2's
structural ruling: the two limbs are COMPLEMENTARY, not double-gated
(§4.11) — classification escapes enter limb 1 (reviewer-raised,
triple-keyed), fidelity hits enter limb 2 (mechanical, gap-keyed);
disjoint by construction because the mechanical check keys on the
declaration and therefore cannot audit it — exactly HIGH-4's hole,
which is why the split is right rather than redundant. (b) class (d)
is the STRONGEST of the four gap classes on instrument identity:
detection instrument = acceptance instrument (the check re-run), so
§4.3's "cannot recur against the very check that is its acceptance"
holds by construction, naming-lap anchor safe, no caveat needed
(unlike the record-gap class, only instrument-equivalent). R2's rung
pointer WITHDRAWN: the principle's justification defeats it — torch is
legitimate glue, so a scan sees torch but not whether it does kernel
work; a blocking scan misfires on every legitimate use (§9.14.1 rules
against). NEW LOW-12: after the MEDIUM-2 split, the root principle
still stated the rung as reviewed-only — understatement, nothing
routes differently, but a builder implementing from the principle
builds review-only and drops class (d) (same incomplete-landing shape:
split landed in three nodes, not in the declaring principle). FIXED
same round with R2's prescribed clause: "(split rung - mechanical
substrate-fidelity check for items declared kernel-class; reviewed
rung for the classification itself, since no mechanical scan can tell
kernel-class torch from legitimate glue)" — colon→dash forced by YAML
plain-scalar rules; reason-clause now attaches unambiguously to the
reviewed leg. R2's flagged grep done: skill-package-plan.md CLEAN
(cites "P1-P13 with rungs" pointing at the enforcement record, never
restates the rung); enforcement-record P13 already split (R1 verified
this round). Re-assembled 31/89 validate PASS; sweep +1 rule =
90+16+12 ALL PRESENT. R2 bounded confirm on the clause in flight; LOW
does not gate approval. Both lanes otherwise CLOSED — approval ask
presented.

### LOW-12 confirm — R2 CONFIRM, round fully closed

R2 CONFIRM at 08:58:33/127,159 (layout untouched 21:28:53): prescribed
string matches as one unit (count 1); guarded-set continuity clean
(full phrase 4, triple 3, restates-neither 1, reviewed-rung 1).
Deviation (a) ruled faithful and forced (YAML). Deviation (b) ruled
BETTER than R2's own wording, with R2's self-finding on record: their
em-dash version let the reason clause govern the mechanical leg, where
"no mechanical scan can tell" is FALSE (a scan CAN check a
declared-kernel-class item — that is why the leg is mechanical); the
same antecedent-ambiguity defect they filed against "for the same
reason" one round earlier, in their own prescription. W9 AMENDMENT
(R2): the antecedent/citer-agreement check applies to
REVIEWER-PRESCRIBED prose, not just planner-written prose — a
prescription is a rationale sentence like any other. LOW-12 caveat
discharged (enforcement home never reviewed-only). R2 DECLINED
observation, closing the clause family: the bullet's lead-in "checked
at the design and implementation review gates" is imprecise-as-summary
(the mechanical leg lives in record_changeset, not a review gate;
hits REACH the gates as class (d)) — true-but-imprecise, parenthetical
supplies accurate placement, nothing routes off it, NOT rated, no
correction requested; flagged only to mark the well dry. Lane closed,
nothing owed, LOW never gated. GATE STATE: both reviewer lanes CLOSED
on all amendments; user approval is the only open item.

### R1 delta verify on the two user-directed plan touches — §7 PASS, §6 MEDIUM-3 fixed

R1: §7 verbatim-confirmed against e626694 fetched directly (2.2.9 =
strict tightening of installed :80; rule-5 cross-ref true at :81; the
"2.2.8 predates it" parenthetical ruled exactly the guard a builder
needs). Composition (b) PASS: default loop runs INSIDE node meaning
BEFORE concluding no declared outcome fits; rule 3 governs after —
§9.8's pause exit is literally rule 3's instruction; consistent with
P12. MEDIUM-3 (fidelity was the point of the touch): my step-3
paraphrase moved the spec's tier boundary in BOTH directions —
tier 2 lost "or several candidates", tier 3's trigger dropped
"many...real trade-offs" and lost "a cause that resists localization"
entirely, and the selection rule "prefer correctness, then
simplicity" vanished; net effect, the lead would fan out parallel
investigation for any multi-candidate failure (over-spend in a
budget-disciplined run) while the genuinely hard single-candidate
case had no heavy route at all. FIXED verbatim per pave-spec
:828-835; step-2 priors gained "the failure text searched in public
sources". R1 LOW (their message says LOW-12 — collides with R2's
LOW-12, booked as LOW-13): §7 obligation had no owning unit under a
one-writer-per-file rule — FIXED "Unit-1 obligation" with the
unit-1/unit-3 split stated (SKILL.md owns contract text; unit 3 ships
workspace machinery). Non-finding on record: step-2 triad omitted
from §6 but ships in the graph (increment_stuck). W9 EXTENSION
(twelfth instance): a VERBATIM-carry ask gets a flattened diff
against the source before landing — paraphrase is how this class
enters. Re-verify dispatched; nothing blocks approval (R1 explicit).

### R1 re-verify on MEDIUM-3 + LOW-13 — PASS, all lanes clear

R1 PASS at plan mtime 09:52:25: step 3 drift-free (word-by-word vs
:829-835; the two named deviations faithful; dropping "(§9.4)" ruled
correct by the touch's own dangling-reference logic), priors clause
faithful (:823-827, four priors in source order, both structural
halves), Unit-1 tag = single greppable owner (:106/:142; partition
matches §6's unit list verbatim; one-writer satisfied). LOW-13
renumber accepted. Residual on record, explicitly not a finding: §6's
"only" is a deliberate sharpening — scoped by "Two early exits" to
recovery-loop bailout (the user's exact concern), not the graph's
declared pause/blocked routes, which the shipped graph governs. GATE
STATE: every reviewer lane closed on every amendment (color, NKI +
HIGH-4/MEDIUM-2/LOW-11/LOW-12, default-recovery + 2.2.9 evolution
touches + MEDIUM-3/LOW-13). User approval is the only open item.

### Two user-directed amendments — rederiver fable/xhigh pin + teammate dispatch

(1) User declared the fable dispatch failure FIXED and directed
"update rederiver to fable/xhigh" — this reverses R1's HIGH-1 fix ON
NEW INFORMATION, recorded as such, not silently: every home now says
user-directed pin 2026-08-26 with the supersession named (plan :27,
plan §2 row, traceability, brief §4 row). The HIGH-1 hazard is still
honored by the retained fallback: an intermittent spawn 400 gets an
identical retry, never a downgrade, because an undispatchable seat
dead-ends all sixteen recovery routes — the failure mode R1 named is
now mitigated by retry policy instead of by avoiding the pin.
(2) "per node agents or main agent for a node should be team mates" —
dispatch mechanics paragraph added (plan §2 + brief §4): primary seat
per node instance = named background teammate, SendMessage-
continuable, retired at node close; one-shot subagents remain for
internal fan-out; authority explicitly unchanged (lead single-writer
P10, no edge traversal or gate presentation by teammates, forbidden-
effects inheritance intact, no peer permission escalation). Graph
untouched by both — packaging policy. R1 bounded delta verify
dispatched; approval remains the only gate item.

### R1 delta verify (pin + teammates) — PASS x6; MEDIUM-4 + LOW-14 fixed; USER APPROVAL

R1 PASS on all six asks (reversal honest at :57; four homes flipped,
zero inherit-session residue — the :1684 suspicion self-disproved as
chronological-log supersession; sixteen-routes rationale preserved in
both prose homes; authority claims match P10/P12; plan/brief drift
direction safe; fresh-seat rule governs as the more specific).
MEDIUM-4 (the substantive one): forbidden-effects inheritance was
asserted for teammates but its only named MECHANISM was scoped
"sub-agent" (plan delegate row + enforcement P2 rationale) — a
builder could build the wrapper on the sub-agent path only, dropping
teammate primary seats to prose-rung containment. FIXED both homes
with R1's phrase: "every spawned seat, teammate or sub-agent,
inherits the dispatching node's forbidden_effects" / P2 "intercepts
every spawned seat the hook scope might miss, teammate or sub-agent".
LOW-14: retry-identically had no declared stop (§9.8's own bound
requirement, now carried in §6) — FIXED both homes: "after three
identical failures, pause for the operator rather than substituting";
never-downgrade absolute. Nits landed: brief gains the
peer-escalation clause; "(doer seats; reviewer seats stay fresh per
gate round)" in plan + brief. Sweep 90+16+12 ALL PRESENT post
enforce-touch. R1 residuals on record: pin validity = deployment
property the bundle cannot verify (user owns it); final-skill gate
will check the wrapper covers BOTH spawn paths (R1 self-booked).
USER APPROVED mid-round ("approve plan", verbatim in
reviews/user-plan-approval.md) — R1 had pre-ruled neither finding
blocks. STAGE 4 CLOSED. Stage 5 opens on four build units; R1
retained for final-skill gate; R2 retained for §4.2/§4.3 boundary.

### R1 post-approval confirm — all four landings closed; final-gate list booked

R1 CONFIRM: MEDIUM-4 at three homes (brief :372 picked up unprescribed
— ruled the right call), zero narrow-scope residue; LOW-14 both homes;
both nits. Approval chronology corroborated by mtime sequence (fixes
predate the record) — gate artifact ruled honest. Placement note (no
severity): plan/brief attach the reviewer-freshness parenthetical to
different antecedents; plan (build authority) is the precise one;
booked for final gate, no change to the approved rendered view. W9
promotion (R1 self-disclosure, third wrapped-prose near-miss):
flatten-first is now the DEFAULT instrument for multi-word phrase
checks on wrapped Markdown — grep only single tokens. Final-gate
bookings (R1 will re-verify against own record; fresh partner
reviews): wrapper-covers-both-spawn-paths in built code; §6/§7 Unit-1
texts greppable in shipped SKILL.md; three-failure pause in rederiver
contract; substrate none-declaration device intact. R1 lane CLOSED
until final-skill gate.

## Final gate — R1 (retained plan reviewer) own-lane re-verify, 2026-08-26

Verdict: 4/4 bookings PASS; P1 residual ruled ACCEPTABLE at the
blocking rung; nothing in R1's lane blocks delivery.

1. MEDIUM-4 (wrapper covers both spawn paths): PASS at build layer —
   SKILL.md:111-113 prescribed wording + stronger operational duty;
   P2 row :320 de-scoped; all three blocking hooks state run-wide
   actor coverage in their own headers.
2. §6/§7 fidelity: PASS both. §9.8 loop at SKILL.md:231-257 re-diffed
   at the MEDIUM-3 site — all three tiers with both qualifiers, four
   priors, both exits intact; :253-257 adds a correct
   does-NOT-authorize composition note. Rule 4 at :276-283 faithful to
   e626694; Unit 1's reported delta is a grammatically-required
   lowercase that originated in the approved plan text, so Unit 1
   shipped the approved text exactly. Rule 5 verbatim at :284-285.
3. Rederiver contract: PASS — fable/xhigh in agents/rederiver.md:4-5 +
   SKILL.md:92; retry-identically / three-failure pause / never
   downgrade at SKILL.md:121-124 and rederiver.md:65-71; HIGH-1
   supersession disclosed as a deliberate agent-binding exception at
   :117-120, acknowledged by investigator.md:96-97.
4. Substrate none-declaration device: PASS all five parts —
   workflow.pave.yaml:1097 and :1354-1357 spot-verified by R1 itself;
   :47-53, :932-938, :454-458/:486-491 worker-verified with quotes;
   restated without contradiction in implementer.md and
   adversarial-reviewer.md. HIGH-4's silent-omission escape closed in
   built code.
5. P1 bare-push residual: ACCEPTABLE — fail-open is correct on the
   blocking rung (false fire is the worse error; R1 would reject a
   fail-closed fix). New optional finding LOW-15, does not block:
   (a) missing-cwd path passes cwd=None so the branch resolves in the
   hook's own working directory — can false-allow AND false-block,
   contradicting the fail-open comment at :134-135; fix = skip the
   check when cwd is absent. (b) push.default=upstream unhandled — a
   bare push on a tracking branch can write a protected ref while the
   hook sees the local name; optional hardening = also match
   `git rev-parse --abbrev-ref '@{push}'`, same fail-open semantics.
   R1 sizing rationale: both need non-default git config or a missing
   payload field, and P1 keeps contract-text + next-gate layers.

Traceability reformat: no problem in R1's lane (moved a citation
subject, not a claim). Residual risks carried into delivery recorded:
fable-pin deployment property (mitigated, two homes), P13 reviewed-half
wrong-classification bound by gate-2 user approval, LOW-15(a)/(b).

Lead disposition: LOW-15(a)/(b) held for joint disposition with the
fresh final reviewer's verdict — no edits to the package while the
whole-package review is in flight.

## Final gate — fresh reviewer (final-reviewer) round 1, 2026-08-26

Verdict: REVISE — 1 HIGH, 3 MEDIUM, 1 LOW, nothing BLOCKING. All five
classified FIX; all prose-narrow. Reviewer independently re-ran
validators/tests/hooks, confirmed graph byte-identity to v0, verified
~80 domain citations against the fork, confirmed all seven booked open
items satisfied or ruled, and rejected four of its own workers'
hypotheses on evidence (P13 device intact; "mechanical = script"
misread; traceability reformat lossless; rederiver binding a disclosed
exception, not a defect).

Findings and lead fixes (all landed 2026-08-26):
- HIGH-1 freeze-target error: SKILL.md freeze paragraph said stage/run
  in the RUN WORKSPACE; lineage lives at the PLUGIN ROOT (manifest +
  history/ ship there; freeze writes into the dir passed). Following
  it literally strands active_revision: null and lineage never
  accumulates. FIX: paragraph rewritten — target named as the plugin
  root, run-workspace explicitly forbidden with the failure mode,
  staged-draft cleanup after passing verify added.
- MEDIUM-1 misattributed pitfall: measurement-pitfalls.md blamed
  `vllm bench serve` for the chunk-count undercount; the cited
  campaign evidence locates it in the campaign's own custom streaming
  harness (stock tool set aside for unrelated reasons). FIX: rule
  retitled to chunk-derived throughput from ANY harness; trap restated
  as evidenced; stock-tool internals marked unverified at this pin;
  SKILL.md stage-8 echo corrected.
- MEDIUM-2 README stop-guard mischaracterization ("once per run
  position" — invented qualifier; counter is session-keyed, re-arms
  every third stop). FIX: all three README spots now carry SKILL.md:14
  wording (blocks at most one stop in three while a run is active).
- MEDIUM-3 README hid the accepted P1 fail-open residual; reviewer
  REPRODUCED the gap (cd/-C directory-change shapes evade the bare-push
  arm; hook comment characterized the residual incompletely). FIX:
  README P1 row carries the disclosed residual; hook comment widened
  to name cd/-C and push.default shapes. Behavior deliberately NOT
  made fail-closed (both reviewers concur).
- LOW-1 README wildcards overclaimed protection ("release-0.24.*");
  hook is an exact-match set. FIX: exact names in the README row.
- R1's LOW-15(a) landed in the same pass: bare-push arm now SKIPS
  resolution when the payload carries no cwd (previously resolved in
  the hook process's own directory — wrong-repo evidence, could
  false-block). LOW-15(b) (@{push} resolution) NOT landed: reviewers
  diverge on hardening shape (R1: resolve @{push}; final reviewer:
  refuse bare push). Both sized it optional; deferred to a future
  package revision rather than picking a side at the gate.

Post-fix verification (lead, world-produced): bash -n clean; hook
behavior 6/6 (bare push cwd-on-mainline exit 2; bare push no-cwd exit
0 [new]; feature refspec 0; force-main 2; benign 0; delete-main 2).
Run-workspace record corrections from reviewer's bookkeeping notes:
file count 30→29, reformat "content unchanged" restated precisely.

Residual risks accepted into delivery (reviewer-supported,
nonblocking): P1 directory-change evasion (disclosed in README + hook);
PROTECTED set hard-codes two release strings while pins are
invocation-time inputs (matches approved enforcement record — a
re-baselined run must extend the set or accept contract-text coverage);
three-failure fable pause is prose-only (approved so); schema notes
field bounded by single-writer; LOW-15(b).

Sent back to final-reviewer for re-verify against its findings.

## Final gate — fresh reviewer re-verify (round 2), 2026-08-26: PASS

All five findings CLOSED on re-verified evidence; gate closed from the
fresh reviewer's lane. Reviewer re-ran the hook matrix itself (11/11,
including a regression guard isolating that the bare-push arm still
fires with cwd present) and re-confirmed package integrity after the
edits: graph sha unchanged (byte-identical v0), validate_pave 31/89/5,
both tests PASS, frontmatter intact, residue zero, 29 files. On the
deferred hardening the reviewer CONCURS with deferral and WITHDRAWS its
refuse-bare-push suggestion (neither candidate is complete; disclosure
landed; false fire is the worse error on the blocking rung). Residuals
carried forward unchanged, all now disclosed. One non-finding noted
for a future touch of the freeze paragraph: the closing verify command
omits the history/vN argument (argparse errors clearly; no action).

Gate state: R1 lane closed (4/4 PASS + P1 ruling, LOW-15(a) landed,
LOW-15(b) deferred); fresh-reviewer lane closed (5/5 CLOSED). No
BLOCKING or HIGH findings open. Next: clean-room forward test.

## R1 sign-off, 2026-08-26 — three residuals handed to the first real run

1. LOW-15(b) push.default: trigger is operator git config the run does
   not control (checkout -b off a protected base + push.default=
   upstream); disclosure is the mitigation.
2. Fable pin: the package cannot verify the deployment property.
   Lead's live spawn check (2026-08-26) passed on a general-purpose
   fable agent, narrowing but not discharging this — the registered
   vllm-neuron-parity:rederiver type resolves only in a full plugin
   installation, so first real dispatch of rederive_approach remains
   the moment of truth; the three-failure pause makes that safe.
3. P13 reviewed half: wrong substrate classification surviving both
   gates, bounded by gate-2 user approval; R1's on-record warning —
   the rule bites harder than it reads.
Process note adopted: flatten wrapped Markdown BEFORE any multi-word
phrase check (default instrument, not fallback) — line-based grep on
hard-wrapped prose produces false regressions.

## Final-review delta round (post-forward-test repairs, 2026-08-26, reviewer: final-reviewer-2, fresh)

Scope: the three forward-test repairs + reviews/forward-test.md only (full
final review previously closed PASS above).

Parallel re-validation (steps 1, 2, 9, 10 repeated on the doc-only delta):

- plugin-dev:plugin-validator — PASS. No structural/hook/agent/path
  regressions; graph digest unchanged (9b61e2…8cf263); both test scripts
  exit 0. Three new minor findings: (1) compatibility strings in SKILL.md
  frontmatter and plugin.json still carried the old fail-closed claim
  (= final-reviewer-2's HIGH, fixed below); (2) tests/__pycache__ residue
  recreated by the validator's own test run (deleted); (3) pre-existing:
  Evolution rule 2 cited `replan_required`, undeclared in the graph
  (fixed to `plan_unrealizable_as_designed`, the declared analogue).
- plugin-dev:skill-reviewer — PASS. New Non-interactive-sessions paragraph
  contradicts nothing (dispatch, resume, stop-guard, gates all checked);
  paths resolve. One optional non-finding suggestion (headless-gate
  drain-then-pause half-sentence) — disclosed to final-reviewer-2, not
  applied.

Round 1 — REVISE (1 HIGH, 2 MEDIUM, 1 LOW; 6 hypotheses rejected with
evidence; all findings verified by the reviewer empirically):

- HIGH: repair 3 fixed only 1 of 3 copies of the fail-closed claim;
  SKILL.md frontmatter `compatibility` and plugin.json `compatibility`
  (both outranking README in the skill's authority order) still claimed
  run-state validation fails closed. FIXED: split wording (validate_pave
  fails closed; validate_run_state full only with jsonschema, labeled
  stdlib fallback otherwise) propagated to both; flattened-phrase sweep
  over all .md/.json/.yaml = zero survivors; plugin.json valid JSON,
  frontmatter parses.
- MEDIUM: §4.7 lead-transcription path recorded provenance nowhere
  nameable. FIXED: transcription block in report metadata naming the seat
  and citing the hand-back (one citation per transcribed report, preflight
  precedent); run-state home named = `notes`.
- MEDIUM: repair 1's polling exit condition unreachable on the refused-
  write path repair 2 documents. FIXED: hand-back exit condition +
  §4.7 cross-reference appended to the SKILL.md paragraph.
- LOW: artifact-layout.md §2 blanket no-stray-writes sentence left
  unqualified. FIXED: "(except a landing path §2 names)".

Round 2 (re-verify) — pending; recorded when the reviewer returns.

Round 2 (re-verify) — PASS. All four findings resolved, verified against
primary evidence (scripts' exit behavior, parsed JSON/frontmatter,
flattened zero-survivor sweep). Reviewer notes for the record: the §4.7
provenance fix is self-sealing (the hand-back citation lands inside the
report-hash scope) and the `notes` home is schema-real; the adjacent
`plan_unrealizable_as_designed` fix verified against the graph, plus the
reviewer's own sweep of all 71 backticked snake_case identifiers in
SKILL.md — every one resolves (five resolve to workflow-manifest.yaml /
freeze_revision.py, as they should). The skill-reviewer's optional
drain-then-pause suggestion ruled NOT MATERIAL (no seat is in flight at a
gate presentation by design; forward test demonstrated the clean gate
stop) — declined under proportionality. Residuals on record, non-blocking:
`notes`-holds-paths-not-content reading (foreclosed by schema:5),
dropped-connection vs turn-end adjacency in SKILL.md, README not
advertising headless, prior residuals unchanged. Graph untouched
(mtime-verified). GATE CLOSED. Reviewer final-reviewer-2 retired after
this round.
