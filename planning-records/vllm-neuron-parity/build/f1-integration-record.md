# F1 integration record — flat graph assembly

Lead working state. First assembly pass 2026-08-25. Product:
`workflow.draft.pave.yaml` (31 nodes, 89 edges, 12 checks, 24 evidence,
11 state fields; validate_pave.py PASS).

## Mechanism

- `build/assemble_flat_graph.py` merges `planning/root.v7.draft.pave.yaml`
  + the five parent fragments (latest revisions per
  `build/assembly-overlay.yaml#parent_fragments`) and applies the overlay.
- RE-RUNNABLE: fragment revisions land automatically on the next run.
  The pending measure-parent v4 second touch (A1/A2/A5 budget landing)
  requires only re-running the script and re-validating.
- The script enforces referential closure before writing: every edge
  endpoint, outcome route, check reference, evidence reference, node
  reachability, and check on_failure_route must resolve.

## Lead-mint inventory (register provenance in overlay rationales)

- Item 24: preconditions fan-out entry + `preflight_record_complete`
  check; `reports_insufficient` fan-out over derived `deficient_targets`;
  `scan_entry_id` state field (external runtime dependency).
- Item 25: design internal edges (screen-first re-entries, route-
  conditional matrix edge, prereg join, record_incomplete owning-sibling
  edges + exhaustion door under the layout-pinned predicate); four new
  design evidence ids; comparator commitment as `state_effects` on
  `preregistration_to_record` (31(b)).
- Item 26: measure internal edges per the booked routing (inbound ->
  child 1; four blocked terminals -> rederive_approach; both re-entries
  -> child 3; defect loops under the shared two-tier budget);
  `procedures_smoke_verified` (27(p)-scoped wording) + `revision_stamped`
  checks; three measurement evidence ids; inherited-check retargets
  (evidence_stable_before_verdict -> stabilize, measurer_not_adjudicator
  -> runs + producer-scope wording) = checklist A8 discharged here.
- Item 31(a): `design_entry_id` state field (minting duty); socratic
  guard `pin_infeasibility_socratic_guard` on the pin_infeasible edge
  (question verbatim from screen v2 :209-213; bound declared in
  rationale). 31 addendum: `comparators_preregistered` failure re-homes
  to preregister_acceptance (re-emission); `design_approved_by_user`
  failure re-homes to screen.
- c9 grounding: both scope_exceeded valves -> rederive_approach.
- c10: rationale on `unrecoverable_host_to_reacquisition`.
- 27(i): downstream catch on `restored_host_to_venv_reverify`.
- Hardware children inherit no instance_per from root (root parent had
  none - hardware runs inside the campaign flow, keyed by lease record).

## Deliberate deferrals

- Budget magnitudes/tier wording in measure child contract text: rides
  the v4 second touch (A1), then re-run.
- F2 layout reference, F3 enforcement record, F4 built references: next
  build units; edge rationales cite them where they bind.
- Item 35 gate-2 sweep (F8) and key-pair checks (F9): bundle round.
