# Traceability — vllm-neuron-parity

Maps every object in `workflow.draft.pave.yaml` (31 nodes, 89 edges,
12 checks, 24 evidence ids, 5 control endpoints) to the package element
that realizes it (`skill-package-plan.md` §1). Contract-text authority
for each node: root.v7 (13 atomic nodes) or its parent fragment
(18 children) — merged mechanically by `build/assemble_flat_graph.py`
per `build/assembly-overlay.yaml`.

## Nodes -> realization

| Node(s) | Realized by | Seat |
|---|---|---|
| verify_run_preconditions | SKILL.md Stage 1 (intake + preflight dispatch) | investigator (opus, medium) |
| trace_target_delta, assemble_delta_report | SKILL.md Stage 2 (delta scan) | investigator (opus, high / medium) |
| cost_routes_and_rank_backlog | SKILL.md Stage 2 (costing) | investigator (opus, high) |
| review_route_verdicts | SKILL.md gate-1 review round | adversarial-reviewer (opus, high) |
| assemble_kickoff_contracts | SKILL.md gate 1 (user approval + fan-out) | lead |
| screen_pin_and_progress, draft_increment_plan, assemble_regression_matrix, preregister_acceptance, assemble_design_record | SKILL.md Stage 3 (design campaign; design-entry id minted at entry) | investigator / implementer (opus) |
| review_campaign_design | SKILL.md gate-2 review round | adversarial-reviewer (opus, high) |
| scope_next_increment, realize_increment, record_changeset | SKILL.md Stage 4 (implementation loop) | implementer (opus, high) |
| review_implementation | SKILL.md pre-hardware review | adversarial-reviewer (opus, high) |
| acquire_hardware_lease, replicate_campaign_venv, execute_attempt_loop, recover_leased_host | SKILL.md Stage 5 (hardware attempts; lease records lead-written) | implementer (opus; xhigh on attempt loop) |
| realize_measurement_procedures, capture_baseline_reference, run_candidate_measurements, stabilize_and_package_evidence | SKILL.md Stage 6 (measurement) | measurer (opus; sonnet at stabilize) |
| adjudicate_results | SKILL.md Stage 7 (adjudication) | adjudicator (opus, high) |
| review_measurement_verdict | SKILL.md verdict review | adversarial-reviewer (opus, high) |
| prepare_pr, review_pr_evidence | SKILL.md Stage 8 (PR package + review) | implementer / adversarial-reviewer (opus) |
| rederive_approach | SKILL.md re-derivation stage | rederiver (fable, xhigh — user-directed pin 2026-08-26) |
| close_campaign, verify_run_closure | SKILL.md gate 3 + run closure | lead + adjudicator |

## Checks -> realization

| Check | Style | Realized by |
|---|---|---|
| preflight_record_complete | mechanical | lead procedure in SKILL.md (transcript-per-precondition scan) |
| kickoff_contract_approved, design_approved_by_user | reviewed/user | gate procedures in SKILL.md; verbatim approval records in run state |
| scheduling_holds_recorded | mechanical | lead procedure (surface-overlap derivation) |
| pin_infeasibility_socratic_guard | socratic | lead evaluation procedure (never the note author) |
| comparators_preregistered | mechanical | lead timestamp comparison vs registration record (layout §4.5) |
| impl_commit_is_reviewed | mechanical | lead commit-equality test against the review findings record |
| evidence_stable_before_verdict, measurer_not_adjudicator | mechanical | lead procedures (re-read stability per design-record count/spacing; producer-set identity per item 26 scope) |
| procedures_smoke_verified, revision_stamped | mechanical | lead procedures over layout §4.9 / §4.6 shapes |
| closure_evidence_settled | mechanical | lead PR-URL/world-evidence verification at gate 3 |

## Edges, endpoints, state, extension policy

- All 89 edges + 5 control endpoints -> SKILL.md lead routing; the
  canonical graph `workflow.pave.yaml` is the routing authority the
  lead re-reads on resume.
- Fan-out edges (approved_campaigns, requested_targets,
  deficient_targets, discrepant_campaigns) -> lead dispatch loops.
- state (19+ required fields incl. scan_entry_id, design_entry_id) ->
  `schemas/run-state.schema.json` + `scripts/validate_run_state.py`;
  derived fields (deficient_targets, discrepant_campaigns, all budget
  and allowance counts) are computed from artifacts, never stored.
- Run-wide prohibitions -> hooks + procedures per
  `build/enforcement-record.md` (P1-P3 hooks in
  `skills/vllm-neuron-parity/hooks/`; the lead-alignment pair likewise).
- Shape authorities cited by contract text -> `references/
  artifact-layout.md` (single authority; §4 pinned shapes).
- Domain knowledge cited by DoD/hardening text ->
  `references/measurement-pitfalls.md`, `references/
  patch-mechanism-inventory.md`, `references/collision-ranking.md`.
- Evolving tier -> `workflow-manifest.yaml`, `history/`,
  `scripts/freeze_revision.py`, evolution contract in SKILL.md.
