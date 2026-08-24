# Review and build

## Contents

1. Material-only review
2. User approval
3. Parallel construction
4. Integration validation
5. Final review
6. Clean-room forward test

## 1. Material-only review

Start the gate's `pave-material-reviewer` through the active harness mechanism and retain that exact reviewer identity. Its scope, materiality, and severity contract is its native role prompt — the brief carries only the approved goal, the gate's artifacts, and the installation path. Continue the same reviewer through repair rounds so it keeps its earlier findings and repair history in context. Use a fresh reviewer at the final skill gate. Retire each reviewer when its gate closes.

The plan gate opens early: start its reviewer when the first planning boundary closes, not when the bundle is assembled. Send each closed boundary to that retained reviewer through the active harness continuation mechanism as one unit — frozen parent contract, child graph, uncertainty ledger, ladder justification, contribution statement — so cross-boundary conflicts surface before deeper elaboration, and the reviewer arrives at the whole-bundle round with every boundary already in context. A boundary marked `stale` by a skeleton resynchronization reopens only that boundary's review; per the reviewer's reuse rule, unchanged boundary judgments stand. The whole-bundle review is the same gate, same reviewer, continued.

If the native `pave-material-reviewer` role is unavailable, the plugin is not installed correctly — pause. Do not substitute a default worker and claim the adversarial gate passed.

Start every brief — initial and every repair round — with the approved goal statement from `requirements.md`: the reviewer anchors every severity judgment to it. Do not restate the materiality and proportionality warnings; they are the reviewer's system prompt, pinned for its whole life.

Plan-review scope:

- approved requirements are preserved;
- graph references and routes are valid;
- authority and independence are explicit;
- evidence can support the claims assigned to it;
- recovery and completion do not fail open;
- package plan can implement the graph without hidden policy;
- the enforcement record sizes each prohibition honestly — neither prose for a violation that outlives its prose, nor a blocking hook without misfire-proof detection;
- the evolution tier matches the workflow's real lifetime — neither evolution machinery on a one-shot workflow nor a bare `static` on a repeatedly re-run one;
- the plan approval brief (`reviews/plan-brief.md`) matches the bundle it renders, per the rendered-view rule in `references/approval-briefs.md`: the brief is what the user actually reads at the gate.

When the plan composes nodes into child profiles, structure the brief into decomposition-boundary units — for each composed node: the parent contract, the child graph, the uncertainty ledger, the ladder justification, and the contribution statement, reviewed together as one unit. The same single reviewer covers all boundaries in one gate; never spawn per-node reviewers. Instruct the reviewer to detect findings at the nearest sub-goal but rate severity against the approved root goal, and to treat an unfalsifiable decomposition justification as a finding.

Final-review scope:

- description and manual-invocation behavior;
- graph-to-skill traceability;
- resolved paths and role references;
- no contradiction between lead, roles, orchestration, schemas, and scripts;
- no silent topology or acceptance changes;
- helpers enforce only the mechanical rules they claim;
- the delivered `README.md` and `VERSION` match the shipped package per `references/approval-briefs.md` — a rendered-view claim the package does not support, a workflow visual that shows undeclared nodes or edges, or a README section that restates a contract as new authority instead of linking it, is a finding.

The lead reads every cited location and applies the same goal test the reviewer was given: does this finding prevent or materially impair the approved goal? Classify each finding:

- `FIX`: verified, and it prevents or materially impairs the approved goal. Inconvenience is not a reason to defer a goal-impacting finding.
- `DEFER`: verified but orthogonal to the goal or outside the approved package.
- `FALSE_POSITIVE`: unsupported, preference-only, speculative, or based on an unapproved requirement.

Before choosing `FIX`, confirm that the failure is plausible, affects an approved requirement, and is not already caught by a later required gate before harm. Do not add a new subsystem for theoretical hardening.

Only verified `BLOCKING` or `HIGH` findings stop the gate. Record review rounds under `reviews/`.

## 2. User approval

After the plan reviewer passes, present the reviewer-verified plan approval brief (`reviews/plan-brief.md`, rendered per `references/approval-briefs.md`) in full in the conversation. The user decides from the brief and drills into raw bundle artifacts only through its appendix links. The brief's sections carry the decisions the user is making:

- intro: fitness verdict or override, and what approving authorizes;
- workflow summary and visual: the at-a-glance stage diagram, then important topology choices in one faithful Mermaid diagram per profile;
- file structure: the generated package tree;
- agents and hooks tables: authority rules, enforcement record, runtime bindings (including any `workflow_script` recommendation);
- tradeoffs and open decisions: extensions, runtime dependencies, known evidence gaps.

Then ask one bounded approval question through the active harness mechanism. Its approval option must state that it approves the complete bundle. A request for changes returns to the narrowest affected planning node, then repeats review — re-render the brief after the repair, never patch it by hand.

## 3. Parallel construction

After approval, derive build tasks from `skill-package-plan.md`.

Safe fan-out rules:

- one builder owns one non-overlapping file set;
- a child profile and its realization mapping belong to one builder, never split;
- shared files have one writer;
- every task lists graph IDs and output paths;
- builders receive the approved YAML and only necessary context;
- builders cannot change graph meaning;
- semantic gaps return to the lead instead of being guessed — including any need to flatten, bypass, or change a composition boundary.

Dispatch all `skill-builder` workers together through the active harness role mechanism; the native role definition carries model and effort. When the output directory is inside a git repository, give each builder a separate worktree through the active harness isolation mechanism so parallel writes cannot conflict; the lead integrates those worktrees. Outside a repository, the non-overlapping file contract is the isolation.

Generated packages use the active harness package shape from the lead and `skill-builder` role contracts: a native manifest, a lead skill under `skills/<workflow-name>/`, native role definitions when authority or context differs, explicit user gates, and retained-reviewer continuity where the graph requires them. The plugin name, lead-skill directory, and role prefix use one `<workflow-name>`, so separate builders cannot diverge. Every generated role `description` carries its dispatched by the lead only warning and rejects implicit triggering. For a manual-only generated skill, put the explicit-invocation prohibition in its `description`.

When the approved enforcement record plans hooks, the package ships each hook script under the lead skill's `skills/<workflow-name>/hooks/` and registers it at the recorded native placement and actor scope, per the hook doctrine in `references/lead-alignment-hooks.md` §Hook doctrine. Declare the hook runtime dependency through the active harness compatibility metadata and say in the generated skill's `description` that it registers hooks — a skill's contents must not surprise the user who invokes it. Never register anything silently.

A generated skill whose graph composes nodes instructs its lead to orchestrate each child profile itself, with the Runtime Binding duties of `references/pave-composition.md` section 12: open the child run, hold the parent pending, apply the terminal map, fail closed on an ambiguous return. Scripts never nest across profile boundaries.

For each approved `workflow_script` binding, the assigned builder compiles the subgraph to one harness-native script per its native role contract, applying the plan's per-node model, reasoning effort, and sandbox assignments verbatim. The generated `SKILL.md` must state both bindings: run the script when its runtime is available, otherwise the lead runs the same subgraph through concurrent native role dispatch. The graph, not the script, stays the authority. A generated skill that runs multi-agent orchestration must say so in its `description`, so the user opts in by invoking it.

The lead integrates builder outputs and removes placeholders or unused directories.

## 4. Integration validation

Run:

1. The plugin structure check: use the active harness's plugin validator when available (manifest validity, component discovery, path references). Otherwise check the manifest and layout against current harness documentation and record the substitution in `reviews/validation.md`.
2. The active harness skill-quality validator named in the lead skill on the generated skill. Its frontmatter allow-list can lag the runtime's current keys: when the only failure is an unexpected `hooks` key that the approved enforcement record placed there, record the validator lag and continue. Every other failure blocks.
3. `scripts/validate_pave.py` on the generated canonical graph; it follows composition references and validates every child profile, terminal map, and boundary in the tree.
4. `scripts/validate_traceability.py` on the canonical graph, traceability table, and generated skill root, including qualified child-profile rows and one `realization` row per composed node.
5. Generated script and schema tests.
6. For each generated Workflow script: confirm the `meta` block is a pure literal, every compiled node's outcome enum matches the graph's declared outcome codes, and every non-compiled destination (user gate, `return` endpoint) is handed back to the lead rather than handled inside the script.
7. For each shipped hook: run its script against a passing and a failing input, confirm its frontmatter or fragment registration matches the recorded placement and actor scope, and — when a settings fragment ships — confirm it references only scripts that exist and the generated `SKILL.md` carries the consent gate and decline path.
8. Evolution tier: for `static`, confirm no revision machinery shipped and the generated lead carries the pause-and-report contract; for `evolving`, run the shipped freeze script's `freeze` and `verify` against a scratch draft, confirm tampering fails verification, and confirm the generated lead states the authority envelope and its user-approval boundary.
9. Search for TODO placeholders, broken relative references, and unapproved auxiliary files.
10. Confirm the delivered docs exist and are current: `README.md` rendered from the approved bundle and updated to what was actually built, `VERSION` seeded at `1.0.0` (or appended on an update run). Content accuracy is the final reviewer's scope; this step checks presence and section completeness per `references/approval-briefs.md`.

Validation proves package coherence. It does not prove that domain judgment is correct.

## 5. Final review

Spawn a fresh named material reviewer with the approved goal statement and absolute paths to the integrated skill, approved plan bundle, and comparison sources.

Fix verified `BLOCKING` and `HIGH` findings. Continue the same reviewer through the active harness mechanism. Stop and reconsider after four rounds with recurring material findings.

## 6. Clean-room forward test

Create a temporary output directory. Spawn a fresh one-shot forward tester with no `model` or `effort` override — the test must predict how the skill behaves at real session defaults. Give it only:

- the generated plugin's package root;
- representative prompt approved in `skill-package-plan.md`;
- temporary output directory;
- no-live-mutation rule.

A generated plugin's role agents resolve only through a complete harness installation. The tester drives the packaged workflow through the fresh headless launch defined in its native role contract, from the temporary output directory, and inspects output and emitted artifacts. The no-live-mutation rule and temporary directory are the guard. Run the session in the background and poll because a long workflow can outlive one foreground timeout. A session that cannot start, times out, cannot resolve a role agent, or has required writes denied is degraded evidence, never a silent clean pass; only then may the tester follow the lead skill directly and record why native dispatch could not run.

Do not tell the tester the expected answer or suspected defect. Inspect its artifacts after completion. Repair only transferable skill defects, then repeat affected validation and final review.

After all gates pass, deliver automatically. No third user approval is required.
