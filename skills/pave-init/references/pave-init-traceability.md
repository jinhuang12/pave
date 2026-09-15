# PAVE Init traceability

Each row maps one canonical graph object to its owning skill file. Several graph objects can share one implementation file.

Native role outputs are generated from `sources/roles/`. Every other path resolves inside this skill directory; the update path's lead procedure lives in the sibling pave-evolve skill, out of the validator's reach, so its rows anchor to the revision reference and the revision log script here.

| Type | ID | Implementation | Authority or purpose |
|---|---|---|---|
| role | lead | SKILL.md<br>references/pave-revisions.md | Run state, routing, integration, delivery, and applying reviewed successors |
| role | requirements_interviewer | orchestration/interview-and-fitness.md | Adaptive requirements gathering |
| role | system_explorer | agents/system-explorer.md | Bounded primary-evidence investigation |
| role | node_planner | agents/node-planner.md | One-node planning authorship |
| role | material_reviewer | agents/pave-material-reviewer.md | Evidence-backed material review |
| role | skill_builder | agents/skill-builder.md | Scoped package construction |
| role | forward_tester | agents/forward-tester.md | Clean-room skill use |
| role | workflow_updater | agents/workflow-updater.md | Successor proposal authorship; never applies, never writes run state |
| role | update_reviewer | agents/update-reviewer.md | Material-only review of a successor proposal; independent of the updater and the lead |
| role | user_authority | orchestration/interview-and-fitness.md<br>orchestration/review-and-build.md<br>references/pave-revisions.md | Requirements, override, plan approval, and successor approval when the apply step requires it |
| evidence | run_contract | SKILL.md | Run identity and output boundary |
| evidence | requirements_record | orchestration/interview-and-fitness.md | Approved requirements record |
| evidence | fitness_decision | orchestration/interview-and-fitness.md | PAVE fitness judgment |
| evidence | fitness_override_record | orchestration/interview-and-fitness.md | Explicit override after a not-fit verdict |
| evidence | exploration_reports | SKILL.md<br>agents/system-explorer.md<br>orchestration/explore-and-plan.md | Per-angle evidence reports, persisted to exploration/<angle>.md by its explorer |
| evidence | system_map | orchestration/explore-and-plan.md | Verified system synthesis |
| evidence | root_contract | orchestration/explore-and-plan.md<br>references/planning-layout.md | The root's frozen five-part contract at planning/root-contract.md, lead-written before the queue opens (working state); the root's own draft is a node_plan_drafts instance |
| evidence | planning_queue_record | orchestration/explore-and-plan.md<br>schemas/run-state.schema.json<br>scripts/validate_run_state.py<br>references/planning-layout.md<br>hooks/planning-layout-warn.sh | Planning queue, opened with the root and grown at every reviewed return; shape per `$defs.planning_queue`/`$defs.node_draft`, checked by `--planning-queue`, ownership per the layout reference, drift warned by the hook |
| evidence | node_plan_drafts | agents/node-planner.md<br>orchestration/explore-and-plan.md<br>references/planning-layout.md | One node draft per node plan at planning/<node>.draft.pave.yaml, the root's first; a redispatch mints a new path |
| evidence | node_plan_reviews | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md<br>schemas/run-state.schema.json | Verdict per node plan as a run-state `node_plan_reviews` entry plus the queue entry's status; no per-node file |
| evidence | workflow_definition | references/pave-yaml.md<br>references/pave-composition.md<br>orchestration/explore-and-plan.md | Canonical PAVE graph, root plus justified child graph files |
| evidence | traceability_record | orchestration/explore-and-plan.md | Graph-to-skill mapping |
| evidence | package_plan | orchestration/explore-and-plan.md | File ownership and build units |
| evidence | plan_review | SKILL.md<br>agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Plan-gate verdict, recorded in reviews/plan-review.md |
| evidence | plan_approval | SKILL.md<br>orchestration/review-and-build.md<br>references/approval-briefs.md | Explicit whole-bundle approval, recorded in reviews/user-plan-approval.md; presented via the reviewer-verified brief at reviews/plan-brief.md |
| evidence | build_unit_results | SKILL.md<br>agents/skill-builder.md<br>orchestration/review-and-build.md | Per-builder terminal result; the lead's integration records under build/ are working state deleted or ignored at close |
| evidence | generated_skill_package | agents/skill-builder.md<br>orchestration/review-and-build.md | Integrated generated skill |
| evidence | validation_results | SKILL.md<br>scripts/validate_pave.py<br>scripts/validate_traceability.py | Mechanical validation records, recorded in reviews/validation.md |
| evidence | final_skill_review | SKILL.md<br>agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Integrated-skill verdict, recorded in reviews/final-skill-review.md |
| evidence | forward_test_result | SKILL.md<br>agents/forward-tester.md<br>orchestration/review-and-build.md | Clean-room behavior evidence, recorded in reviews/forward-test.md |
| evidence | revision_log | references/pave-revisions.md<br>scripts/record_revision.py | Append-only revisions.yaml beside the live graph; entry 0 at delivery, one entry per apply step or pin |
| evidence | successor_proposal | agents/workflow-updater.md<br>references/pave-revisions.md<br>scripts/record_revision.py | history/v<N>.patch: declared preamble then the unified diff against the live graph |
| evidence | update_review | agents/update-reviewer.md<br>references/pave-revisions.md | Verdict and round count, written into the applied entry's review field; no standing file |
| check | manually_invoked | SKILL.md | Explicit invocation gate |
| check | requirements_complete | orchestration/interview-and-fitness.md | Adaptive interview completion |
| check | requirements_and_fit_approved | orchestration/interview-and-fitness.md<br>references/approval-briefs.md | Approval for fit or fit-with-gaps, presented via reviews/requirements-brief.md |
| check | fitness_override_approved | orchestration/interview-and-fitness.md | Explicit authority to continue after not-fit |
| check | exploration_coverage_complete | orchestration/explore-and-plan.md | All selected angles accounted for |
| check | root_plan_complete | orchestration/explore-and-plan.md | Root contract frozen, run-wide records drafted, queue open with the root as its only entry |
| check | node_plan_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Per-node plan review gate |
| check | planning_queue_closed | orchestration/explore-and-plan.md | All planning queue entries reviewed or exhausted |
| check | pave_definition_valid | scripts/validate_pave.py | Schema, graph structure, references, and composition boundaries |
| check | plan_material_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Material-only plan gate |
| check | whole_bundle_approved | orchestration/review-and-build.md<br>references/approval-briefs.md | Explicit plan approval, decided from the reviewer-verified plan brief |
| check | all_build_units_complete | orchestration/review-and-build.md | Parallel build join |
| check | integrated_validation_passed | orchestration/review-and-build.md | Package validation gate |
| check | final_material_review_passed | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md | Material-only final gate |
| check | forward_test_acceptable | agents/forward-tester.md<br>orchestration/review-and-build.md | Transferable-defect gate |
| check | revision_log_valid | SKILL.md<br>scripts/record_revision.py | Delivery gate: `record_revision.py verify` passes on the package root and the revision log holds entry 0 only |
| check | update_review_material | agents/update-reviewer.md<br>references/pave-revisions.md | Material-only successor gate; declared kind survives the graph-or-setup test |
| check | update_review_rounds_remain | references/pave-revisions.md<br>references/pave-init.pave.yaml | Three-round bound on the successor review loop, read from the lead's plan_review_rounds counter |
| check | successor_approved | references/pave-revisions.md<br>references/pave-init.pave.yaml | Verbatim approval when a user-only change was touched or the plan says `approval: always`; the recorded check otherwise |
| check | apply_verified | scripts/record_revision.py<br>references/pave-revisions.md | Post-apply verify and head-digest match |
| node | initialize_run | SKILL.md | Establish run workspace and output limits |
| node | interview_system | SKILL.md<br>orchestration/interview-and-fitness.md | Gather requirements |
| node | assess_pave_fitness | orchestration/interview-and-fitness.md | Judge suitability without scoring |
| node | approve_requirements_and_fit | orchestration/interview-and-fitness.md | Approve fit or fit-with-gaps |
| node | approve_fitness_override | orchestration/interview-and-fitness.md | Explicitly override not-fit |
| node | explore_system | agents/system-explorer.md<br>orchestration/explore-and-plan.md | Independent evidence fan-out |
| node | synthesize_exploration | orchestration/explore-and-plan.md | Verify and combine findings |
| node | plan_root | orchestration/explore-and-plan.md<br>references/pave-spec.md | Freeze the root contract and open the queue with the root as its first entry |
| node | plan_node | agents/node-planner.md<br>orchestration/explore-and-plan.md | Plan one queue entry, the root included, at any depth |
| node | review_node_plan | agents/pave-material-reviewer.md<br>orchestration/review-and-build.md<br>orchestration/explore-and-plan.md | Judge one node plan; on a pass the lead enqueues its framed children |
| node | fix_root_conflict | orchestration/explore-and-plan.md | Resolve interface conflicts, mark stale node plans |
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
| node | finalize_delivery | SKILL.md<br>references/pave-revisions.md<br>scripts/record_revision.py | Write revision log entry 0 with `record_revision.py init`, then automatic final delivery |
| node | draft_successor | agents/workflow-updater.md<br>references/pave-revisions.md | Draft one successor proposal from recorded evidence; the update path's entrypoint |
| node | review_successor | agents/update-reviewer.md<br>references/pave-revisions.md | Material review of the proposal and its declared kind; the lead records verdict and round |
| node | approve_successor | references/pave-revisions.md | Explicit user approval when the apply step requires it |
| node | apply_revision | scripts/record_revision.py<br>references/pave-revisions.md | Lock, apply, validate, append, re-pin the calling run, release |
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
| edge | synthesis_ready_to_root_plan | orchestration/explore-and-plan.md | Plan root plan from complete exploration |
| edge | root_plan_ready_to_planning_queue | orchestration/explore-and-plan.md | Fan out over the live planning queue, root first |
| edge | root_plan_evidence_gap_to_explore | orchestration/explore-and-plan.md | Gather missing root plan evidence |
| edge | root_plan_fitness_changed_to_assess | SKILL.md<br>references/pave-init.pave.yaml | Reassess changed fitness |
| edge | node_plan_planned_to_review | orchestration/review-and-build.md | Review a closed node plan |
| edge | node_plan_conflict_to_resync | orchestration/explore-and-plan.md | Resolve an interface conflict |
| edge | node_plan_evidence_gap_to_explore | orchestration/explore-and-plan.md | Gather missing node plan evidence |
| edge | node_plan_passed_to_join | orchestration/explore-and-plan.md | Join a reviewed node plan once its children are enqueued |
| edge | node_plan_revision_to_replan | SKILL.md<br>references/pave-init.pave.yaml | Replan a rejected node plan |
| edge | resync_done_to_elaborate | orchestration/explore-and-plan.md | Redispatch stale node plans |
| edge | resync_root_change_to_pause | orchestration/explore-and-plan.md | Route root-contract changes to the user |
| edge | resync_exhausted_to_pause | orchestration/explore-and-plan.md | Pause an exhausted planning queue |
| edge | assembly_ready_to_review | orchestration/explore-and-plan.md<br>orchestration/review-and-build.md | Submit assembled bundle for whole-bundle review |
| edge | assembly_conflict_to_resync | SKILL.md<br>references/pave-init.pave.yaml | Return an assembly conflict to resynchronization |
| edge | synthesis_contradiction_to_explore | SKILL.md<br>orchestration/explore-and-plan.md | Investigate a contradiction |
| edge | synthesis_blocked_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause blocked synthesis |
| edge | plan_review_pass_to_approval | orchestration/review-and-build.md | Request user approval after review |
| edge | plan_review_revision_to_repair | orchestration/review-and-build.md | Repair a verified plan defect |
| edge | plan_repair_ready_to_review | orchestration/review-and-build.md | Reuse the reviewer in the same gate |
| edge | plan_repair_semantic_change_to_resync | SKILL.md<br>references/pave-init.pave.yaml | Return a semantic change to the root plan |
| edge | plan_repair_blocked_to_pause | SKILL.md<br>references/pave-init.pave.yaml | Pause a blocked repair |
| edge | plan_approved_to_build | orchestration/review-and-build.md | Start approved build units |
| edge | plan_revision_to_resync | orchestration/review-and-build.md | Apply user-requested revision at the narrowest node plan |
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
| edge | draft_ready_to_review | references/pave-revisions.md<br>references/pave-init.pave.yaml | Submit a drafted proposal for material review |
| edge | draft_needs_user_approval_to_pause | references/pave-revisions.md<br>references/pave-init.pave.yaml | Route a needs-user-approval change to the user |
| edge | draft_no_change_to_close | references/pave-revisions.md<br>references/pave-init.pave.yaml | Close honestly when the evidence warrants no change |
| edge | successor_review_pass_to_apply | references/pave-revisions.md<br>references/pave-init.pave.yaml | Apply a passed proposal; successor_approved routes to the user gate when needed |
| edge | successor_revision_to_draft | references/pave-revisions.md<br>references/pave-init.pave.yaml | Redraft a rejected proposal within the three-round bound |
| edge | successor_approved_to_apply | references/pave-revisions.md<br>references/pave-init.pave.yaml | Apply after explicit verbatim approval |
| edge | successor_approval_revision_to_draft | references/pave-revisions.md<br>references/pave-init.pave.yaml | Apply a user-requested revision to the proposal |
| edge | successor_rejected_to_close | references/pave-revisions.md<br>references/pave-init.pave.yaml | Close a rejected proposal; nothing applied |
| edge | apply_complete | scripts/record_revision.py<br>references/pave-init.pave.yaml | Enter revision_applied after verify and digest match |
| edge | apply_failed_to_pause | references/pave-revisions.md<br>references/pave-init.pave.yaml | Pause an interrupted or failed apply step for the user |
| endpoint | resume_from_checkpoint | SKILL.md<br>references/pave-init.pave.yaml | Return through persisted traversal history |
| endpoint | wait_for_exploration_join | orchestration/explore-and-plan.md | Exploration terminal barrier |
| endpoint | wait_for_planning_queue_join | orchestration/explore-and-plan.md | Planning queue terminal barrier; re-evaluates as the queue grows |
| endpoint | wait_for_build_join | orchestration/review-and-build.md | Build terminal barrier |
| endpoint | pause_for_user_authority | SKILL.md | Resumable missing-authority state |
| endpoint | closed_unaccepted | SKILL.md | User stop or plan rejection |
| endpoint | complete | SKILL.md | Validated and tested skill delivery |
| endpoint | revision_applied | references/pave-revisions.md<br>references/pave-init.pave.yaml | Reviewed, approved successor applied and the requester re-pinned |
| endpoint | closed_no_change | references/pave-revisions.md<br>references/pave-init.pave.yaml | No revision warranted, or proposal rejected; nothing applied |
| contract | state | SKILL.md<br>schemas/run-state.schema.json<br>scripts/validate_run_state.py | Persistent state and checkpoint ownership; run-state.json implemented per the Run workspace protocol |
| contract | completion | SKILL.md<br>references/pave-init.pave.yaml | Accepted and closed terminal conditions |
