# PAVE Init traceability

Each row maps one canonical graph object to its owning skill file. Several graph objects can share one implementation file.

Paths under `agents/` resolve at the plugin root — they are registered agent types, dispatched as `pave-init:<name>`. Every other path resolves inside this skill directory.

| Type | ID | Implementation | Authority or purpose |
|---|---|---|---|
| role | lead | SKILL.md | Run state, routing, integration, and delivery |
| role | requirements_interviewer | orchestration/interview-and-fitness.md | Adaptive requirements gathering |
| role | system_explorer | agents/system-explorer.md | Bounded primary-evidence investigation |
| role | node_planner | agents/node-planner.md | One-boundary planning authorship |
| role | material_reviewer | agents/pave-material-reviewer.md | Evidence-backed material review |
| role | skill_builder | agents/skill-builder.md | Scoped package construction |
| role | forward_tester | agents/forward-tester.md | Clean-room skill use |
| role | user_authority | orchestration/interview-and-fitness.md<br>orchestration/review-and-build.md | Requirements, override, and plan approval |
| evidence | run_contract | SKILL.md | Run identity and output boundary |
| evidence | requirements_record | orchestration/interview-and-fitness.md | Approved requirements record |
| evidence | fitness_decision | orchestration/interview-and-fitness.md | PAVE fitness judgment |
| evidence | fitness_override_record | orchestration/interview-and-fitness.md | Explicit override after a not-fit verdict |
| evidence | exploration_reports | SKILL.md<br>agents/system-explorer.md<br>orchestration/explore-and-plan.md | Per-lens evidence reports, persisted to exploration/<lens>.md by its explorer |
| evidence | system_map | orchestration/explore-and-plan.md | Verified system synthesis |
| evidence | root_skeleton | orchestration/explore-and-plan.md | Frozen root contract and node interfaces |
| evidence | frontier_record | orchestration/explore-and-plan.md<br>schemas/run-state.schema.json<br>scripts/validate_run_state.py<br>references/planning-layout.md<br>hooks/planning-layout-warn.sh | Planning frontier state; shape per `$defs.frontier`/`$defs.fragment`, checked by `--frontier`, ownership per the layout reference, drift warned by the hook |
| evidence | boundary_drafts | agents/node-planner.md<br>orchestration/explore-and-plan.md | Per-boundary child profile drafts |
| evidence | boundary_reviews | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Per-boundary review verdicts |
| evidence | workflow_definition | references/pave-yaml.md<br>references/pave-composition.md<br>orchestration/explore-and-plan.md | Canonical PAVE graph, root plus justified child profiles |
| evidence | traceability_record | orchestration/explore-and-plan.md | Graph-to-skill mapping |
| evidence | package_plan | orchestration/explore-and-plan.md | File ownership and build units |
| evidence | plan_review | SKILL.md<br>agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Plan-gate verdict, recorded in reviews/plan-review.md |
| evidence | plan_approval | SKILL.md<br>orchestration/review-and-build.md<br>references/approval-briefs.md | Explicit whole-bundle approval, recorded in reviews/user-plan-approval.md; presented via the reviewer-verified brief at reviews/plan-brief.md |
| evidence | build_unit_results | agents/skill-builder.md<br>orchestration/review-and-build.md | Per-builder terminal result |
| evidence | generated_skill_package | agents/skill-builder.md<br>orchestration/review-and-build.md | Integrated generated skill |
| evidence | validation_results | SKILL.md<br>scripts/validate_pave.py<br>scripts/validate_traceability.py | Mechanical validation records, recorded in reviews/validation.md |
| evidence | final_skill_review | SKILL.md<br>agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Integrated-skill verdict, recorded in reviews/final-review.md |
| evidence | forward_test_result | SKILL.md<br>agents/forward-tester.md<br>orchestration/review-and-build.md | Clean-room behavior evidence, recorded in reviews/forward-test.md |
| evidence | delivery_manifest | references/pave-revisions.md<br>scripts/freeze_revision.py | v0 manifest record; v1 freezes before first real execution |
| check | manually_invoked | SKILL.md | Explicit invocation gate |
| check | requirements_complete | orchestration/interview-and-fitness.md | Adaptive interview completion |
| check | requirements_and_fit_approved | orchestration/interview-and-fitness.md<br>references/approval-briefs.md | Approval for fit or fit-with-gaps, presented via reviews/requirements-brief.md |
| check | fitness_override_approved | orchestration/interview-and-fitness.md | Explicit authority to continue after not-fit |
| check | exploration_coverage_complete | orchestration/explore-and-plan.md | All selected lenses accounted for |
| check | skeleton_complete | orchestration/explore-and-plan.md | Root skeleton interfaces and enforcement record present |
| check | boundary_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Per-boundary review gate |
| check | frontier_closed | orchestration/explore-and-plan.md | All frontier entries reviewed or exhausted |
| check | pave_definition_valid | scripts/validate_pave.py | Schema, graph structure, references, and composition boundaries |
| check | plan_material_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Material-only plan gate |
| check | whole_bundle_approved | orchestration/review-and-build.md<br>references/approval-briefs.md | Explicit plan approval, decided from the reviewer-verified plan brief |
| check | all_build_units_complete | orchestration/review-and-build.md | Parallel build join |
| check | integrated_validation_passed | orchestration/review-and-build.md | Package validation gate |
| check | final_material_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Material-only final gate |
| check | forward_test_acceptable | agents/forward-tester.md<br>orchestration/review-and-build.md | Transferable-defect gate |
| check | delivery_manifest_valid | SKILL.md | v0 manifest and freeze-boundary gate; lead-reviewed against the Final delivery spec (no script can verify a v0 manifest — freeze_revision.py verify requires a frozen revision) |
| node | initialize_run | SKILL.md | Establish run workspace and boundaries |
| node | interview_system | SKILL.md<br>orchestration/interview-and-fitness.md | Gather requirements |
| node | assess_pave_fitness | orchestration/interview-and-fitness.md | Judge suitability without scoring |
| node | approve_requirements_and_fit | orchestration/interview-and-fitness.md | Approve fit or fit-with-gaps |
| node | approve_fitness_override | orchestration/interview-and-fitness.md | Explicitly override not-fit |
| node | explore_system | agents/system-explorer.md<br>orchestration/explore-and-plan.md | Independent evidence fan-out |
| node | synthesize_exploration | orchestration/explore-and-plan.md | Verify and combine findings |
| node | plan_root_skeleton | agents/node-planner.md<br>orchestration/explore-and-plan.md<br>references/pave-spec.md | Freeze root contract, produce skeleton and frontier |
| node | elaborate_boundary | agents/node-planner.md<br>orchestration/explore-and-plan.md | Plan one frontier boundary |
| node | review_boundary | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Judge one boundary unit |
| node | resynchronize_skeleton | orchestration/explore-and-plan.md | Resolve interface conflicts, mark stale boundaries |
| node | assemble_graph_plan | orchestration/explore-and-plan.md | Merge, simplify, bind, produce approval bundle |
| node | review_graph_plan | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Plan adversarial review |
| node | repair_graph_plan | agents/node-planner.md<br>orchestration/review-and-build.md | Repair verified plan defects |
| node | approve_graph_plan | orchestration/review-and-build.md | Whole-bundle user approval |
| node | build_skill_component | agents/skill-builder.md<br>orchestration/review-and-build.md | Non-overlapping build fan-out |
| node | integrate_skill | SKILL.md<br>orchestration/review-and-build.md | One-writer integration |
| node | validate_integrated_skill | scripts/validate_pave.py<br>scripts/validate_traceability.py<br>orchestration/review-and-build.md | Mechanical package validation |
| node | review_integrated_skill | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Final material review |
| node | repair_integrated_skill | agents/skill-builder.md<br>orchestration/review-and-build.md | Repair verified package defects |
| node | forward_test_skill | agents/forward-tester.md<br>orchestration/review-and-build.md | Clean-room forward test |
| node | finalize_delivery | SKILL.md<br>references/pave-revisions.md | Record v0 manifest, then automatic final delivery |
| edge | initialize_ready_to_interview | SKILL.md | Start a new interview |
| edge | initialize_resume_to_checkpoint | SKILL.md | Resume after the last satisfied check |
| edge | initialize_conflict_to_pause | SKILL.md | Protect an occupied output path |
| edge | interview_ready_to_fitness | orchestration/interview-and-fitness.md | Route complete requirements to fitness review |
| edge | interview_more_answers | orchestration/interview-and-fitness.md | Continue the adaptive interview |
| edge | interview_evidence_gap | orchestration/interview-and-fitness.md | Resolve a discoverable evidence gap |
| edge | fitness_fit_to_approval | orchestration/interview-and-fitness.md | Request fit approval |
| edge | fitness_gaps_to_approval | orchestration/interview-and-fitness.md | Request fit-with-gaps approval |
| edge | fitness_not_fit_to_override | orchestration/interview-and-fitness.md | Isolate the not-fit override gate |
| edge | requirements_approved_to_exploration | orchestration/interview-and-fitness.md<br>orchestration/explore-and-plan.md | Start approved exploration |
| edge | requirements_revision_to_interview | orchestration/interview-and-fitness.md | Reopen requirements |
| edge | requirements_stopped_to_close | orchestration/interview-and-fitness.md | Close on user stop |
| edge | override_approved_to_exploration | orchestration/interview-and-fitness.md<br>orchestration/explore-and-plan.md | Start exploration after explicit override |
| edge | override_revision_to_interview | orchestration/interview-and-fitness.md | Revise the request after not-fit |
| edge | override_stopped_to_close | orchestration/interview-and-fitness.md | Close a declined override |
| edge | exploration_ready_to_join | orchestration/explore-and-plan.md | Join completed exploration |
| edge | exploration_gap_to_join | orchestration/explore-and-plan.md | Join a bounded evidence gap |
| edge | exploration_critical_gap_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause on a critical gap |
| edge | synthesis_ready_to_skeleton | orchestration/explore-and-plan.md | Plan skeleton from complete exploration |
| edge | skeleton_ready_to_frontier | orchestration/explore-and-plan.md | Fan out frontier boundaries |
| edge | skeleton_evidence_gap_to_explore | orchestration/explore-and-plan.md | Gather missing skeleton evidence |
| edge | skeleton_fitness_changed_to_assess | SKILL.md<br>references/pave-init.pave.yaml | Reassess changed fitness |
| edge | boundary_planned_to_review | orchestration/review-and-build.md | Review a closed boundary |
| edge | boundary_conflict_to_resync | orchestration/explore-and-plan.md | Resolve an interface conflict |
| edge | boundary_evidence_gap_to_explore | orchestration/explore-and-plan.md | Gather missing boundary evidence |
| edge | boundary_passed_to_join | orchestration/explore-and-plan.md | Join a reviewed boundary |
| edge | boundary_revision_to_elaborate | SKILL.md<br>references/pave-init.pave.yaml | Replan a rejected boundary |
| edge | resync_done_to_elaborate | orchestration/explore-and-plan.md | Redispatch stale boundaries |
| edge | resync_root_change_to_pause | orchestration/explore-and-plan.md | Route root-contract changes to the user |
| edge | resync_exhausted_to_pause | orchestration/explore-and-plan.md | Pause an exhausted frontier |
| edge | assembly_ready_to_review | orchestration/explore-and-plan.md<br>orchestration/review-and-build.md | Submit assembled bundle for whole-bundle review |
| edge | assembly_conflict_to_resync | SKILL.md<br>references/pave-init.pave.yaml | Return an assembly conflict to resynchronization |
| edge | synthesis_contradiction_to_explore | SKILL.md<br>orchestration/explore-and-plan.md | Investigate a contradiction |
| edge | synthesis_blocked_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause blocked synthesis |
| edge | plan_review_pass_to_approval | orchestration/review-and-build.md | Request user approval after review |
| edge | plan_review_revision_to_repair | orchestration/review-and-build.md | Repair a verified plan defect |
| edge | plan_repair_ready_to_review | orchestration/review-and-build.md | Reuse the reviewer in the same gate |
| edge | plan_repair_semantic_change_to_resync | SKILL.md<br>references/pave-init.pave.yaml | Return a semantic change to the skeleton |
| edge | plan_repair_blocked_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause a blocked repair |
| edge | plan_approved_to_build | orchestration/review-and-build.md | Start approved build units |
| edge | plan_revision_to_resync | orchestration/review-and-build.md | Apply user-requested revision at the narrowest boundary |
| edge | plan_rejected_to_close | orchestration/review-and-build.md | Close a rejected plan |
| edge | build_unit_ready_to_join | orchestration/review-and-build.md | Join a completed build unit |
| edge | build_failure_retry | orchestration/review-and-build.md | Retry a bounded build failure |
| edge | build_semantic_gap_to_resync | orchestration/review-and-build.md | Return an unapproved semantic gap |
| edge | integration_ready_to_validation | orchestration/review-and-build.md | Validate the integrated package |
| edge | integration_conflict_to_repair | orchestration/review-and-build.md | Repair an integration conflict |
| edge | integration_semantic_gap_to_resync | orchestration/review-and-build.md | Return an integration semantic gap |
| edge | validation_pass_to_final_review | orchestration/review-and-build.md | Review mechanically valid output |
| edge | validation_repair_to_repair | orchestration/review-and-build.md | Repair a validation defect |
| edge | validation_blocked_to_pause | orchestration/review-and-build.md | Pause unavailable validation |
| edge | final_review_pass_to_forward_test | orchestration/review-and-build.md | Start clean-room testing |
| edge | final_review_revision_to_repair | orchestration/review-and-build.md | Repair a material skill defect |
| edge | skill_repair_ready_to_validation | orchestration/review-and-build.md | Revalidate a repaired skill |
| edge | skill_repair_semantic_change_to_resync | SKILL.md<br>references/pave-init.pave.yaml | Return a semantic change to planning |
| edge | skill_repair_blocked_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause a blocked skill repair |
| edge | forward_pass_to_delivery | orchestration/review-and-build.md | Deliver a transferable pass |
| edge | forward_defect_to_repair | orchestration/review-and-build.md | Repair a transferable test defect |
| edge | forward_external_gap_to_delivery | orchestration/review-and-build.md | Record an external-only gap |
| edge | delivery_complete | SKILL.md | Enter accepted completion |
| edge | delivery_retry | SKILL.md | Retry failed reporting |
| endpoint | resume_from_checkpoint | SKILL.md<br>references/pave-init.pave.yaml | Return through persisted traversal history |
| endpoint | wait_for_exploration_join | orchestration/explore-and-plan.md | Exploration terminal barrier |
| endpoint | wait_for_frontier_join | orchestration/explore-and-plan.md | Frontier terminal barrier |
| endpoint | wait_for_build_join | orchestration/review-and-build.md | Build terminal barrier |
| endpoint | pause_for_user_authority | SKILL.md | Resumable missing-authority state |
| endpoint | closed_unaccepted | SKILL.md | User stop or plan rejection |
| endpoint | complete | SKILL.md | Validated and tested skill delivery |
| contract | state | SKILL.md<br>schemas/run-state.schema.json<br>scripts/validate_run_state.py | Persistent state and checkpoint ownership; run-state.json realized per the Run workspace protocol |
| contract | completion | SKILL.md<br>references/pave-init.pave.yaml | Accepted and closed terminal conditions |
