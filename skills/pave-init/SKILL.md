---
name: pave-init
description: >-
  Create a PAVE-based Claude Code workflow plugin (a lead skill plus
  registered role agents) from a goal the user states,
  e.g. "/pave-init turn X into a workflow skill". Manual-only - use ONLY when
  the user explicitly invokes /pave-init. Do not trigger implicitly from
  requests like "make a workflow" or "turn this into a skill"; this skill
  runs a long multi-stage campaign (goal clarification, subagent exploration,
  formal PAVE YAML graph, adversarial review, skill construction) that the
  user must opt into by name. Invoking it REGISTERS THREE HOOKS from this
  frontmatter - a stop-alignment check that blocks at most one stop in
  three while a run is active, a throttled run-state staleness reminder,
  and a non-blocking planning-layout warning - which live and die with the
  skill; nothing registers silently.
compatibility: >-
  Hooks require the Claude Code hooks runtime (Stop and PostToolUse
  frontmatter hooks; script paths resolve via CLAUDE_SKILL_DIR, else
  CLAUDE_PLUGIN_ROOT/skills/pave-init) plus bash and python3 (stdlib
  only). Worker roles require this plugin's registered agent types
  (pave-init:*). PAVE
  validation additionally needs pyyaml and jsonschema and fails closed
  without them. Without the hooks runtime, the pair degrades to the Resume
  and run-state duties stated in this file.
hooks:
  PostToolUse:
    - matcher: "Bash|Write|Edit"
      hooks:
        - type: command
          command: "d=\"${CLAUDE_SKILL_DIR:-${CLAUDE_PLUGIN_ROOT}/skills/pave-init}\"; \"$d\"/hooks/state_staleness_reminder.sh"
    - matcher: "Write|Edit"
      hooks:
        - type: command
          command: "d=\"${CLAUDE_SKILL_DIR:-${CLAUDE_PLUGIN_ROOT}/skills/pave-init}\"; \"$d\"/hooks/planning-layout-warn.sh"
  Stop:
    - hooks:
        - type: command
          command: "d=\"${CLAUDE_SKILL_DIR:-${CLAUDE_PLUGIN_ROOT}/skills/pave-init}\"; \"$d\"/hooks/stop_alignment_check.sh"
---

# PAVE Init

Turn a system or engineering process into a validated PAVE Graph Profile and a Claude Code plugin: a lead workflow skill plus its roles as registered agents. The user's stated goal drives the run: clarify it until it is designable, then design. Treat the approved PAVE YAML as the workflow authority.

This skill is manual-only. Do not start it from an implicit match.

## Authority

Resolve conflicts in this order:

1. Explicit user decisions and approvals from the current run.
2. The approved graph: `workflow.draft.pave.yaml` once approved as `v0`, or the frozen active `history/vN/workflow.pave.yaml` an update run starts from (`references/pave-revisions.md`).
3. `references/pave-yaml.md` and `references/pave.schema.json`.
4. The approved `skill-package-plan.md` and `traceability.md`.
5. This file, active orchestration file, and role contracts.

Do not change graph meaning during skill generation. Return to planning and user approval when implementation exposes a semantic gap.

The approved graph is the design-time authority. When a generated skill defines its own live run state (a `state.json` or equivalent), the generated skill must state the handoff: the graph governs design and review; the run's own persisted state governs an active run.

## Required resources

Read only the resource needed for the active stage:

| Stage | Read |
|---|---|
| Goal and fitness | `orchestration/interview-and-fitness.md` |
| Exploration and graph plan | `orchestration/explore-and-plan.md`, `references/planning-layout.md` |
| Plan or skill review | `orchestration/review-and-build.md` |
| Skill construction | `orchestration/review-and-build.md` |
| Clean-room test | `orchestration/review-and-build.md` |
| User approval gates, delivered README/VERSION | `references/approval-briefs.md` |
| PAVE concepts | `references/pave-yaml.md` for fields and validity; load `references/pave-spec.md` — the design language: node, outcome, evidence, guard, edge, control endpoint, node sizing, patterns, smells — for any design choice; load `references/pave-composition.md` only when a plan composes nodes into child profiles |
| Technique selection | `references/technique-selection.md` — when a plan weighs debate, an advisory monitor, a stage audit, or other supervision: what each costs and when it hurts |
| Revisions and evolution | `references/pave-revisions.md` — at delivery (v0 manifest), on an update run (lineage), and whenever the plan records an `evolving` tier |

`references/pave-init.pave.yaml` is this skill's own canonical graph, and `references/pave-init-traceability.md` maps each of its objects to the file that realizes it. Read them only when routing is ambiguous — an unexpected outcome, a recovery choice, or a resume. A linear happy-path run does not need them. The graph alone carries the recovery routing; when one of these outcomes occurs, route it as declared:

| Outcome | Action |
|---|---|
| `output_conflict` (initialize) | The intended workspace or output path is occupied by content this run does not own — pause for the user rather than overwrite. |
| `critical_gap` (exploration) / `planning_blocked` (synthesis) | Evidence is too thin to design on — pause for the user with the exact gap. |
| `contradiction_requires_research` (synthesis) | A load-bearing contradiction survives verification — re-dispatch a bounded explorer at it, do not average it away. |
| `fitness_changed` (root skeleton) | Planning surfaced facts that invalidate the fitness verdict — return to assessment before planning further. |
| `integration_conflict_found` (assembly) | Merged boundaries conflict — resynchronize the skeleton; never patch one boundary locally. |
| `boundary_revision_required` (boundary review) | A REVISE verdict on one boundary — re-dispatch that boundary's node planner with the findings. |
| `semantic_change_required` (either repair node) | A repair cannot stay within approved graph meaning — return to resynchronize/replan and user approval, never patch silently. |
| `repair_blocked` (either repair node) | Repair rounds are exhausted or stuck — pause for the user with the open findings. |

PAVE validation requires Python 3 with the `jsonschema` and `pyyaml` packages and fails closed without them; Stage 1 verifies the runtime. Do not install dependencies without user approval.

## Run workspace

`<workflow-name>` is the generated-skill name confirmed in Stage 1. For a repository target, create:

```text
<repository>/.pave/<workflow-name>/
```

For a target without a repository, or when the repository must stay unmodified (read-only access, a clean tree the user does not want touched), ask for an output directory instead of writing into the repository.

Keep these run artifacts in the run workspace:

```text
run-state.json
run-contract.md
requirements.md
system-map.md
exploration/
workflow.draft.pave.yaml
traceability.md
skill-package-plan.md
workflow-manifest.yaml
history/
reviews/
assembly-checklist.md
build/
```

`assembly-checklist.md` and `build/` are lead working state, never part of the
approval bundle: the checklist tracks what each build unit must integrate, and
`build/` holds the per-unit integration records the lead writes while assembling
Stage 5. Both are free-form — record the paths a run actually uses in
`run-state.json` so a resume can find them.

`run-state.json` is the run's compact position-and-history record:

- The lead writes it at initialization with the fields the graph's state contract requires. It appends one traversal entry (node, outcome, evidence paths) at every checkpoint moment that contract names. The file points at artifacts; it does not duplicate them.
- `schemas/run-state.schema.json` is the single authority for its shape — do not restate the field list in prose. `scripts/validate_run_state.py` checks an instance.
- At initialization, also record the run contract (goal as invoked, workspace, output boundary) in `run-contract.md`.
- Write `<repository>/.pave-init-run` — one line, the absolute path of the live `run-state.json` — so the lead-alignment hooks can find the active run. The marker is the hooks' only ownership evidence: they stay silent without it. Keep it out of version control (`.git/info/exclude`).
- Remove the marker at terminal close. When the user abandons a run or pauses it indefinitely, set `terminal_classification.status` (for example `abandoned`) so the pair goes silent; clear the status again on a real resume.
- When the run writes to an output directory instead of the repository (read-only target), the marker cannot be written. Record in `run-state.json` and `run-contract.md` that the lead-alignment pair is degraded to the prose duties in this file — that is the recorded omission, never a silent one.

`workflow.draft.pave.yaml` (with any child `*.draft.pave.yaml`) is the editable planning subject — reviewed and approved as `v0`, it carries no execution authority of its own (`references/pave-revisions.md`; the v0 delivery and v1 freeze rules are in Final delivery below). Never write run artifacts into the installed `pave-init` skill.

## Multi-agent contract

Every worker role is a registered agent type from this plugin: its role contract is its system prompt, loaded by the harness on every dispatch, so a brief carries only task context — never a role file to read. Resolve this skill's absolute installation path before dispatch; briefs still name it so workers can resolve `references/...`, `schemas/...`, and `scripts/...` citations. Dispatch every worker with the Agent tool. Two dispatch modes exist:

- **One-shot subagents** — explorers (`pave-init:system-explorer`), planners (`pave-init:node-planner`), builders (`pave-init:skill-builder`), and forward testers (`pave-init:forward-tester`). Spawn with `subagent_type` and a complete prompt; no `name`. Spawn independent workers in one message so they run concurrently.
- **Named reviewers** — each review gate gets one `pave-init:pave-material-reviewer` spawned with a unique `name` and `run_in_background: true`. Repair rounds at the same gate continue that same reviewer through `SendMessage`, so it keeps its own findings in context. Start a fresh identity for the final-skill gate. Never re-brief a new agent mid-gate and present it as the same reviewer.

Model and effort ride on the agent definitions (explorer `sonnet`/`medium`, planner `opus`/`high`, reviewer `opus`/`high`, builder `opus`/`medium`, forward tester unpinned — the clean-room test must predict behavior at real session defaults). One per-dispatch override remains: escalate the planner for the root node and any high-risk node to the session's top model (`fable` where enabled) via the Agent tool's `model` parameter. Frontier capacity is scarce and shared; pinning the top model on every worker causes 429 throttling that kills long runs, so reserve the escalation for the one seat where judgment compounds.

If a `pave-init:*` agent type is unavailable, the plugin is not installed correctly — pause and report the missing dependency. Do not substitute an ordinary worker and claim equivalent review.

Use `AskUserQuestion` for bounded user decisions and approval gates. Use free text for open-ended discovery answers. If `AskUserQuestion` is unavailable, ask the same questions in plain text and hold approvals to the same explicit-response standard. Never treat a tool-forced selection as approval of anything the option label did not state.

## Stage 1: Goal and fitness

Product: one clear goal statement the rest of the run serves, its requirements in `requirements.md`, and a PAVE fitness verdict — all explicitly approved by the user. The lead runs the whole stage itself per `orchestration/interview-and-fitness.md`: establish the goal, close sufficiency gaps, write requirements, judge fitness, render the requirements brief, and collect approval.

Record the approval decision in `run-state.json` (`requirements_status`, `fitness_verdict`).

## Stage 2: Independent exploration

Product: verified exploration reports and `system-map.md`. After requirements approval, assess the complexity of the requirements/overall goal & select two to four independent evidence lenses and dispatch explorers per `orchestration/explore-and-plan.md` §§1–3. Each explorer persists its own report into `exploration/`; the lead verifies load-bearing claims from the persisted files and writes the map. Subagents do not write approval artifacts.

## Stage 3: Concrete graph plan

Product: a validated draft graph and the complete approval bundle. Plan across bounded contexts per `orchestration/explore-and-plan.md` §4: freeze the root node contract, open the planning queue with the root as its first entry, and dispatch one node planner per entry — every dispatch is the same job at any depth, and a planner spawns its own read-only explorers when its judgment needs more evidence. Resynchronize conflicts at the parent level, assemble the flat graph, and run the global simplicity pass. Every planner return goes to the planning reviewer before its verdict solidifies (Stage 4's reviewer, spawned early). `planning/` is working state, never part of the approval bundle; its shape authority is `schemas/run-state.schema.json` (`$defs.frontier`, `$defs.fragment`), its ownership and precedence rules are `references/planning-layout.md`, and `scripts/validate_run_state.py --frontier` checks both.

Require the simplest graph that achieves the approved goal, per `references/pave-spec.md` §4.11. Require the enforcement record: every run-wide prohibition and costly-transition guard in `skill-package-plan.md` names its strength and why a stronger rung is unnecessary (`references/pave-spec.md` §9.14.1).

The enforcement record carries one lead-alignment entry by default: the standard hook pair from `references/lead-alignment-hooks.md`, which covers the two windows user-event reinjection cannot see. Omitting the pair is a planning decision like any other: record which of that reference's omission conditions applies, never leave it out silently.

Size every node with the one-agent test from `references/pave-spec.md` §9.12, applied recursively: a feasibility judgment — can one agent achieve this goal and settle its definition of done in one bounded context — recorded as one falsifiable line for either verdict. The warning signs there hint; none gates, and no contract checklist answers the question. A split adds child nodes to the same graph (§9.12.1); a child profile is packaging, justified only by a §9.12.1 condition and the contract in `references/pave-composition.md`. When the goal ports an existing system, the source binds behavior, never graph shape (§9.12.2). Every definition of done follows the evidence ladder in §5.3.1. `skill-package-plan.md` records the profile dependency tree when packaging is used.

Require the complete approval bundle:

- `requirements.md`
- `system-map.md`
- `workflow.draft.pave.yaml`
- `traceability.md`
- `skill-package-plan.md`

The YAML must define concrete roles, evidence, checks, nodes, outcomes, edges, state, control endpoints, and extension policy. Mark an idea-only system as `provisional` and record assumptions and missing evidence.

Run:

```bash
python3 <pave-init>/scripts/validate_pave.py <run-workspace>/workflow.draft.pave.yaml
```

Do not present an invalid graph for approval.

## Stage 4: Material-only plan review

The plan gate's named reviewer already exists — Stage 3 spawns it at the first closed boundary. The whole-bundle review continues that same reviewer via `SendMessage`, as do repair rounds at this gate. If Stage 3 produced an empty frontier, spawn it fresh here. Reviewer mechanics, the plan-review scope, and the `FIX` / `DEFER` / `FALSE_POSITIVE` classification of findings: `orchestration/review-and-build.md` §1; the materiality and proportionality warnings live in the reviewer's system prompt, not in briefs. Only verified `BLOCKING` or `HIGH` findings prevent approval.

Record each review round's verdict and disposition in `reviews/plan-review.md` as it closes; the round counter in `run-state.json` (`plan_review_rounds`) is what keeps the repair loop bounded across a compaction.

For the whole-bundle round, render the plan approval brief per `references/approval-briefs.md` to `reviews/plan-brief.md` and submit it with the bundle — the reviewer verifies the brief against the bundle. After review passes, present the brief in full in the conversation, then require an explicit response such as `approve plan`. Do not treat silence or partial approval as approval. Record the user's explicit response verbatim in `reviews/user-plan-approval.md` — resume reads approval from that record, never from the bundle's existence.

## Stage 5: Build the approved skill

After plan approval, derive independent build units from `skill-package-plan.md` and spawn builders per `orchestration/review-and-build.md` §3 (non-overlapping files, one writer for shared files, `isolation: worktree` inside a git repository).

The delivered unit is a Claude Code plugin — the same shape as pave-init itself. "The generated skill" in this file and its references always means the lead skill inside that plugin. Generated packages may include:

- the plugin manifest (`.claude-plugin/plugin.json`: name, description, version); the plugin name is `<workflow-name>` — one string serving as the manifest `name`, the lead skill's directory name, and the `subagent_type:` prefix;
- one lead `SKILL.md`, under `skills/<workflow-name>/`;
- role contracts as registered agents under the plugin root's `agents/`, when authority or context differs;
- orchestration files for complex stage behavior;
- domain references;
- the canonical PAVE YAML;
- state or artifact schemas;
- mechanical helpers and focused tests;
- hook scripts under the lead skill's `skills/<workflow-name>/hooks/`, registered in the generated skill's frontmatter per the approved enforcement record, plus one consent-gated settings fragment only for rules frontmatter cannot carry;
- for an approved `evolving` tier only: the revision workspace (draft, manifest, `history/`), a copy of `scripts/freeze_revision.py`, and the evolution contract from `references/pave-revisions.md` written into the generated lead;
- `README.md` and `VERSION`, rendered by the lead at integration per `references/approval-briefs.md` — both rendered views of the shipped package, never a second authority.

Create only files required by the approved graph plus the two delivered docs above. Do not create an installation guide or duplicated policy — a README section that restates a contract instead of linking it is duplicated policy. Do not add redundant enforcement: use one existing gate when it already provides enough confidence.

A long-horizon workflow's working memory is the file system, not the context window. Anything routing depends on must live in persisted run state: the current position, the event history (every traversal, decision, and measured dead end, so a later round can tell "untried" from "closed"), and an index of evidence. State stays compact and points; artifacts prove — bulky evidence lives at its declared path, never inline. Resume is reconciliation: reread state, check it against the artifacts on disk, and continue from the last satisfied gate. Write that resume duty into every generated lead. None of this applies to a workflow that lives and dies inside one session — there a state file is ceremony the simplicity pass should delete.

Map graph elements as follows:

- Roles map to registered plugin agents (`agents/<role>.md`) only when they need distinct authority or context. The generated lead dispatches them with `subagent_type: <workflow-name>:<role>` — the same contract as this skill's own Multi-agent contract: the role contract is the agent's system prompt, so briefs carry only task context, never a role file to read. When a required agent type is unavailable, the generated lead pauses and reports that the plugin is not installed; it never substitutes an ordinary worker.
- Nodes map to lead stages or role procedures. Several nodes may use one role.
- Mechanical checks map to scripts or runtime enforcement when feasible.
- A prohibition that must hold after its prose leaves context maps to a hook per the approved enforcement record: observing guard or role reinjection by default, blocking only with the record's justification. Register per the hook doctrine in `references/lead-alignment-hooks.md` §Hook doctrine — frontmatter placement at the recorded actor scope; a settings fragment ships only behind the generated skill's run-start consent gate, never silently.
- Socratic and reviewed checks map to review procedures.
- Edges map to lead routing.
- The lead's long-horizon routing duty maps to the standard lead-alignment hook pair from `references/lead-alignment-hooks.md`, adapted to the generated skill's run-state location and terminal signal — unless the enforcement record carries a recorded omission condition. The generated `description` discloses the pair's blocks-a-stop-once behavior.
- State that must survive a session boundary maps to one machine-checkable schema as the single authority for its shape, plus a validation helper — prose never restates the field list. State that lives and dies inside one session stays in prose.
- When more than one role writes artifacts, render the graph's declared evidence paths into one layout reference: the tree, write ownership per role, which-file-wins precedence, and where non-indexed intermediates go. When a repair round re-enters a node, the declared evidence path keeps only the current revision's output and superseded files move to a declared archive path — the layout reference names both. The graph stays the path authority — the reference adds only what the graph cannot carry (ownership, precedence, archive, scratch). A single-writer workflow with a handful of artifacts skips it.
- A subgraph with an approved `workflow_script` binding maps to one generated Workflow tool script per the compile mapping in the `pave-init:skill-builder` agent definition (plugin `agents/skill-builder.md`), with lead-driven Agent calls as the documented fallback when that tool is unavailable.
- A composed node maps to lead orchestration of its child profile: the lead opens the child run, holds the parent pending, and applies the terminal map (`references/pave-composition.md` §12). A script covers one profile only; scripts never nest across profile boundaries.
- The approved evolution tier maps per `references/pave-revisions.md`: `static` ships the approved canonical YAML and the pause-and-report contract; `evolving` additionally ships the revision workspace, freeze script, and the seven-rule evolution contract with its authority envelope — its generated lead freezes `v1` before the first real execution. Never ship evolution machinery the record does not name.

If an output folder exists, inspect it and propose an update. When the target's run workspace carries a `workflow-manifest.yaml`, this is an update run: verify the active revision with `scripts/freeze_revision.py verify` (or start from the recorded `v0` draft when nothing has frozen yet), start `workflow.draft.pave.yaml` from that bundle, and replan only the narrowest affected boundary through the normal Stage 3–4 gates. Record what changed and why in the new draft's manifest entry for the eventual freeze's `--semantic-diff`. Never overwrite unapproved existing content, and never edit `history/`.

## Stage 6: Validate, review, and forward-test

Procedures for every step: `orchestration/review-and-build.md` §§4–6.

1. Run the integration validation battery (`orchestration/review-and-build.md` §4): the plugin structure check, the system `skill-creator` quick validator, `scripts/validate_pave.py`, `scripts/validate_traceability.py`, generated helper tests, script and hook and evolution-tier checks, and the delivered-docs presence check. When the lead-alignment hook pair ships, its tests must exercise the invariants in `references/lead-alignment-hooks.md` §Invariants any adaptation must preserve. Record the outputs in `reviews/validation.md`.
2. Spawn a fresh named material reviewer for the integrated skill with the same evidence standard as plan review. Fix verified `BLOCKING` and `HIGH` findings and resubmit via `SendMessage` until it passes. Record each round in `reviews/final-skill-review.md` (`final_review_rounds` in run state keeps the loop bounded).
3. Run the clean-room forward test (`orchestration/review-and-build.md` §6) and record the result in `reviews/forward-test.md`. Repair and repeat affected gates when the test exposes a transferable defect.

Auto-complete after these gates pass. Do not ask for another approval.

## Final delivery

Delivery ships `v0`, never `v1`: write `workflow-manifest.yaml` recording the approved draft with no active revision, and copy the approved canonical YAML into the generated skill. Clean-room testing exercised the draft; it creates no revision and claims no real-use evidence. `v1` freezes immediately before the first real execution — by the generated lead (evolving tier) or a pave-init update run (static tier) — via `scripts/freeze_revision.py freeze`. Never edit a frozen `history/` bundle, and never move `active_revision` backward: rollback is a new successor derived from the older revision (`--from-revision`), recorded as its own decision.

The generated `README.md` and `VERSION` carry the deep-dive; the delivery report in conversation stays short and points at them:

- generated plugin path, with `README.md` as the entry point for what was built;
- the one-line install: `claude --plugin-dir <package-root>` loads it for a session; a marketplace install makes it permanent;
- canonical PAVE YAML path, `v0` manifest state, and the v1 freeze boundary (who freezes, and when);
- PAVE, skill, and traceability validation results;
- adversarial-review rounds and disposition of findings;
- clean-room forward-test result;
- known gaps and runtime dependencies.

Do not claim executable fidelity for domain behavior that lacks an implementation or evidence source.

## Resume

On resume, read `run-state.json` first — discover it via the `.pave-init-run` marker or, failing that, the newest `.pave/*/run-state.json` — and check it with `scripts/validate_run_state.py`. A scan hit is not ownership: before reconciling, confirm the discovered state's `planning_workspace` is the workspace actually being resumed, and rewrite the marker once confirmed. Resume is reconciliation, not inference: verify the recorded traversal history and approval records against the artifacts on disk (`reviews/user-plan-approval.md` and the other `reviews/` records are the approval evidence — never infer approval from the mere existence of a planning artifact), then traverse `resume_from_checkpoint` in `references/pave-init.pave.yaml` and reopen the node or edge after the last satisfied check in the recorded traversal history. When state and artifacts disagree, record the reconciliation as its own traversal entry before continuing. A missing or invalid `run-state.json` in an otherwise-populated workspace is itself a finding: rebuild it from the artifacts, mark rebuilt fields as reconstructed, and treat every unproven approval as not given.

A resume that lands mid-Stage-3 also reads `planning/frontier.yaml`: every entry not `reviewed` is open work, and nothing under `planning/` is approved regardless of how complete it looks.
