# Traceability — vllm-neuron-parity

Maps every object in `workflow.pave.yaml` (31 nodes, 89 edges,
12 checks, 24 evidence ids, 5 control endpoints) to the package element
that realizes it. One row per graph object, machine-checked by
`scripts/validate_traceability.py` (pave-init) with
`--skill-dir plugins/vllm-neuron-parity/skills/vllm-neuron-parity`.
Per that validator's path contract: non-agent paths resolve inside the
skill directory, `agents/*` rows resolve at the plugin root. The
approved plugin layout places references/, schemas/, scripts/, and the
canonical graph at the plugin root — outside the skill directory — so
rows realized by those artifacts cite their in-skill citer (`SKILL.md`)
and carry the true plugin-root path in the note column. The graph stays
the authority for meaning.

Reformatted 2026-08-26 at Stage 5 integration into the validator's row
format (content unchanged); the approved human-format original is
archived at `planning/archive/traceability-human-format-superseded-2026-08-26.md`.
Contract-text authority per node: root.v7 (13 atomic nodes) or its
parent fragment (18 children), merged by `build/assemble_flat_graph.py`.

Hooks note (unchanged from the approved table): run-wide prohibitions
realize as hooks + procedures per `build/enforcement-record.md` —
P1-P3 blocking hooks plus the lead-alignment pair in `hooks/` inside
the skill directory. Evolving tier realizes as `workflow-manifest.yaml`,
`history/`, `scripts/freeze_revision.py` (plugin root), and the
evolution contract in SKILL.md.

| Type | Identifier | Realized by | Note |
|---|---|---|---|
| role | lead | SKILL.md | the lead skill itself; single state writer |
| role | user | SKILL.md | gate procedures; approvals recorded verbatim in run state |
| role | investigator | agents/investigator.md | registered agent type vllm-neuron-parity:investigator |
| role | implementer | agents/implementer.md | registered agent type vllm-neuron-parity:implementer |
| role | measurer | agents/measurer.md | registered agent type vllm-neuron-parity:measurer |
| role | adjudicator | agents/adjudicator.md | registered agent type vllm-neuron-parity:adjudicator |
| role | adversarial_reviewer | agents/adversarial-reviewer.md | registered agent type vllm-neuron-parity:adversarial-reviewer |
| role | delegate_dispatcher | SKILL.md | guardrailed delegate-skill dispatch wrapper; forbidden effects inherit into every spawn |
| evidence | run_input_and_preflight_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | delta_report | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | route_costing_and_backlog | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | kickoff_contract_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | gate_approval_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | campaign_design_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | implementation_changeset | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | hardware_attempt_log | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | rederivation_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | measurement_artifacts | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | adjudication_verdict | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | adversarial_review_findings | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | pr_evidence_package | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | closure_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | run_closure_verdict | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | pin_feasibility_note | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | increment_plan_draft | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | regression_matrix_draft | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | acceptance_preregistration | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | increment_lap_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | increment_evidence_records | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | measurement_procedure_record | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | baseline_reference_capture | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| evidence | candidate_measurement_runs | SKILL.md | shape authority: references/artifact-layout.md at plugin root; produced at the graph-declared path |
| check | kickoff_contract_approved | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | scheduling_holds_recorded | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | design_approved_by_user | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | comparators_preregistered | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | evidence_stable_before_verdict | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | measurer_not_adjudicator | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | impl_commit_is_reviewed | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | closure_evidence_settled | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | preflight_record_complete | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | pin_infeasibility_socratic_guard | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | procedures_smoke_verified | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| check | revision_stamped | SKILL.md | check procedure in the lead (mechanical/reviewed/socratic per graph declaration) |
| node | verify_run_preconditions | SKILL.md <br/> agents/investigator.md | Stage 1 intake + preflight |
| node | cost_routes_and_rank_backlog | SKILL.md <br/> agents/investigator.md | Stage 2 costing |
| node | review_route_verdicts | SKILL.md <br/> agents/adversarial-reviewer.md | gate-1 review round |
| node | assemble_kickoff_contracts | SKILL.md | gate 1 - user approval + fan-out |
| node | review_campaign_design | SKILL.md <br/> agents/adversarial-reviewer.md | gate-2 review round |
| node | review_implementation | SKILL.md <br/> agents/adversarial-reviewer.md | pre-hardware review |
| node | rederive_approach | SKILL.md <br/> agents/rederiver.md | re-derivation stage (dedicated rederiver seat, fable/xhigh) |
| node | adjudicate_results | SKILL.md <br/> agents/adjudicator.md | Stage 7 adjudication |
| node | review_measurement_verdict | SKILL.md <br/> agents/adversarial-reviewer.md | verdict review |
| node | prepare_pr | SKILL.md <br/> agents/implementer.md | Stage 8 PR package |
| node | review_pr_evidence | SKILL.md <br/> agents/adversarial-reviewer.md | Stage 8 PR review |
| node | close_campaign | SKILL.md | gate 3 + campaign closure |
| node | verify_run_closure | SKILL.md <br/> agents/adjudicator.md | run-closure verification |
| node | trace_target_delta | SKILL.md <br/> agents/investigator.md | Stage 2 delta scan |
| node | assemble_delta_report | SKILL.md <br/> agents/investigator.md | Stage 2 delta scan |
| node | screen_pin_and_progress | SKILL.md <br/> agents/investigator.md | Stage 3 design screen |
| node | draft_increment_plan | SKILL.md <br/> agents/implementer.md | Stage 3 design |
| node | assemble_regression_matrix | SKILL.md <br/> agents/implementer.md | Stage 3 design |
| node | preregister_acceptance | SKILL.md <br/> agents/implementer.md | Stage 3 design |
| node | assemble_design_record | SKILL.md <br/> agents/implementer.md | Stage 3 design |
| node | scope_next_increment | SKILL.md <br/> agents/implementer.md | Stage 4 implementation loop |
| node | realize_increment | SKILL.md <br/> agents/implementer.md | Stage 4 implementation loop |
| node | record_changeset | SKILL.md <br/> agents/implementer.md | Stage 4 implementation loop |
| node | acquire_hardware_lease | SKILL.md <br/> agents/implementer.md | Stage 5 hardware attempts |
| node | replicate_campaign_venv | SKILL.md <br/> agents/implementer.md | Stage 5 hardware attempts |
| node | execute_attempt_loop | SKILL.md <br/> agents/implementer.md | Stage 5 hardware attempts |
| node | recover_leased_host | SKILL.md <br/> agents/implementer.md | Stage 5 hardware attempts |
| node | realize_measurement_procedures | SKILL.md <br/> agents/measurer.md | Stage 6 measurement |
| node | capture_baseline_reference | SKILL.md <br/> agents/measurer.md | Stage 6 measurement |
| node | run_candidate_measurements | SKILL.md <br/> agents/measurer.md | Stage 6 measurement |
| node | stabilize_and_package_evidence | SKILL.md <br/> agents/measurer.md | Stage 6 measurement |
| edge | preconditions_to_delta_scan | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | missing_inputs_to_pause | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | delta_to_costing | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unreachable_sources_to_pause | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | backlog_to_route_review | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | costing_gap_to_delta_scan | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | stalled_costing_to_pause | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | sound_verdicts_to_kickoff | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | route_findings_to_recosting | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | approved_campaigns_fan_out | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | no_campaigns_to_abort | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | design_to_design_review | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | infeasible_design_to_closeout | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | sound_design_to_implementation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | design_findings_to_redesign | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | increments_to_impl_review | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | design_defect_to_redesign | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | implementation_scope_to_redesign | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | impl_no_progress_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | reviewed_impl_to_hardware | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | impl_findings_to_repair | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | serving_candidate_to_measurement | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | breaker_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | lost_hardware_to_closeout | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | revised_approach_to_redesign | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | rederived_closeout_to_gate | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | measurements_to_adjudication | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | verdict_to_verdict_review | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unstable_evidence_to_remeasure | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | adjudication_no_progress_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | confirmed_pass_to_pr_prep | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | confirmed_shortfall_to_repair | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | confirmed_no_benefit_to_closeout | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | confirmed_regression_to_repair | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | verdict_findings_to_readjudication | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | pr_package_to_pr_review | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | evidence_gap_to_remeasure | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | pr_no_progress_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | reviewed_pr_to_closeout | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | pr_findings_to_package_repair | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | closure_to_run_verification | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | declined_closure_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unverified_closure_to_closeout | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | verified_run_to_complete | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | open_campaigns_to_join | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | resumable_stop_to_pause | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | no_route_to_blocked | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | trace_traced_to_assembly | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | trace_unreachable_to_assembly | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | insufficient_reports_to_retrace | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | screen_to_plan_draft | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | screen_infeasibility_to_record | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | screen_exhaustion_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | plan_to_matrix_upgrade_route | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | plan_to_preregistration | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | plan_scope_valve_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | matrix_to_preregistration | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | matrix_blocked_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | matrix_scope_valve_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | preregistration_to_record | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unadjudicable_criteria_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | record_gap_to_screen | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | record_gap_to_plan_draft | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | record_gap_to_matrix | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | record_gap_to_preregistration | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | record_gap_exhaustion_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | selected_to_realization | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | passed_to_next_scoping | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | stuck_to_rescoping | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | contradiction_to_redesign | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | plan_satisfied_to_recording | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | coverage_gap_to_rescoping | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | lease_to_venv | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | venv_to_attempts | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | replication_dead_end_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | venv_host_fault_to_recovery | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | attempt_host_fault_to_recovery | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | restored_host_to_venv_reverify | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unrecoverable_host_to_reacquisition | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | procedures_to_baseline_capture | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unrealizable_procedure_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | reference_to_candidate_runs | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unusable_baseline_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | runs_to_stabilization | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | procedure_defect_to_realization | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | reference_defect_to_recapture | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | serving_exhaustion_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | collection_defect_to_reruns | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| edge | unproducible_measurement_to_rederivation | SKILL.md | lead routing; workflow.pave.yaml (plugin root) is the routing authority |
| endpoint | run_complete | SKILL.md | terminal/pause handling incl. run-state terminal entries |
| endpoint | run_paused | SKILL.md | terminal/pause handling incl. run-state terminal entries |
| endpoint | run_blocked | SKILL.md | terminal/pause handling incl. run-state terminal entries |
| endpoint | run_aborted | SKILL.md | terminal/pause handling incl. run-state terminal entries |
| endpoint | await_remaining_closures | SKILL.md | terminal/pause handling incl. run-state terminal entries |
| contract | state | SKILL.md | shape authority: schemas/run-state.schema.json + scripts/validate_run_state.py at plugin root; 21 required fields; derived fields recomputed from artifacts, never stored |
| contract | completion | SKILL.md | run-closure procedure; closure_evidence_settled at gate 3 |
