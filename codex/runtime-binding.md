# PAVE Init — Codex CLI runtime binding

This file binds the canonical PAVE Init workflow to Codex CLI. It changes
execution technology. It does not change the PAVE Graph Profile, the review
policy, the evidence standard, the user approval gates, or the stopping rules.

The canonical source remains:

- `skills/pave-init/SKILL.md`;
- `skills/pave-init/orchestration/`;
- `skills/pave-init/references/`;
- `agents/`;
- `skills/pave-init/references/pave-init.pave.yaml`.

Apply this binding recursively whenever those files name a Claude Code
mechanism. Keep all other prose and behavior unchanged.

## 1. Authority and scope

Use this precedence for a Codex run:

1. Explicit user decisions and approvals from the current run.
2. The approved or frozen PAVE graph, as stated by the canonical lead skill.
3. PAVE meaning and schema references.
4. The approved package plan and traceability record.
5. The canonical PAVE Init skill, orchestration files, and role contracts.
6. This file for **runtime-specific substitutions only**.

This file may replace an invocation token, dispatch primitive, file format,
hook registration, model label, or test command. It may not change a node,
outcome, edge, guard, role authority, evidence requirement, retry budget, or
approval meaning.

## 2. Exact semantic substitutions

| Canonical Claude term | Codex CLI equivalent | Required behavior |
|---|---|---|
| Explicit `/pave-init` invocation | Explicit `$pave-init` invocation, or explicit selection through `/skills` | Manual-only. A natural-language request that does not select the skill is not consent to start the campaign. |
| `Claude Code plugin` | `Codex plugin` | Use `.codex-plugin/plugin.json`. Bundle the lead under `skills/<workflow-name>/`. |
| `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` | Keep one stable kebab-case plugin name. The manifest points to `skills/` and, when hooks ship, `hooks/hooks.json`. |
| Registered Markdown agents in `agents/<role>.md` | Project- or user-scoped custom agents in `.codex/agents/<role>.toml` or `~/.codex/agents/<role>.toml` | Each TOML has `name`, `description`, and `developer_instructions`. The supplied PAVE adapters load the canonical role body before work. Missing required custom agents is a dependency failure, not permission to substitute a generic worker. |
| `Agent` tool | Codex subagent orchestration / `spawn_agent` | Spawn the named custom agent. Give it a complete task brief and the absolute `PAVE_PLUGIN_ROOT`. Independent workers start in one parent instruction so they can run concurrently. |
| `subagent_type: pave-init:<role>` | Custom-agent name `pave_init_<role>` | Use the mapping in §3. |
| One-shot agent with no `name` | One new custom-agent thread that receives no later task | Close or retire the thread after its terminal result. |
| Named background reviewer | One retained custom-agent thread | Keep the same thread for the whole gate. Do not replace it during repairs and call the new thread the same reviewer. |
| `SendMessage` | Ask Codex to steer the existing retained subagent thread | Send repair evidence and the next bounded review request to that exact thread. |
| `run_in_background: true` | Start the subagent and retain its thread without forcing an immediate join | Continue only work that does not depend on its result. Wait at the graph’s declared join or gate. |
| `AskUserQuestion` | A plain-text bounded question in the main thread | Present two to four clear options when the decision is bounded. Require an explicit user response. Never infer approval from silence, a file, or a vague “continue.” |
| `sonnet`, `opus`, or `fable` model labels | Inherit the active Codex model and preserve the role’s reasoning effort | Do not pin a stale model slug. Use `medium`, `high`, or `xhigh` as mapped in §4. If the active model does not support the requested effort, use its highest supported effort and record the substitution. |
| `isolation: worktree` | Codex git worktree isolation | Use one worktree per independent writer when the target is a git repository. Outside git, enforce the approved non-overlapping file contract. |
| Skill-frontmatter `hooks` | Plugin-level `hooks/hooks.json`, referenced by `.codex-plugin/plugin.json` | Codex does not use skill frontmatter to register lifecycle hooks. Keep scripts visible, trust-reviewed, marker-gated, and disclosed in the skill description. |
| `CLAUDE_PLUGIN_ROOT` / `CLAUDE_SKILL_DIR` | `PLUGIN_ROOT` for plugin hooks; an absolute skill path in model instructions | Codex also exposes Claude compatibility variables to plugin hook processes, but new code uses `PLUGIN_ROOT`. |
| Claude `Write` / `Edit` payload | Codex `apply_patch` payload | Codex reports patch text in `tool_input.command`. `codex/hooks/post_tool_use_router.py` expands it into the canonical path/content shape. |
| Lead/worker identity on `PostToolUse` | Direct Codex `agent_id` and `agent_type` fields | Current Codex source attaches these fields to spawned-worker events and omits them for root calls. Preserve them and let the canonical hooks apply their identity gates. A runtime that omits worker identity is degraded. See §6. |
| Claude `Workflow` tool script | `codex_exec_script` or lead-driven subagents | See §5. The PAVE graph remains the authority. |
| `plugin-dev` structure/agent helpers | `$plugin-creator` when installed, plus direct checks against current Codex plugin and custom-agent formats | Absence of `$plugin-creator` does not waive structural validation. |
| System `skill-creator` quick validator | `$skill-creator` validator when installed, plus local frontmatter and path tests | Record a missing optional validator. Do not waive graph, traceability, script, hook, or generated test failures. |
| `claude -p ... --plugin-dir ... --permission-mode bypassPermissions` | Install the plugin and custom agents into the clean-room project, then run `codex exec --ephemeral --sandbox workspace-write "<prompt>"` | Run only in the temporary workspace. A start failure, permission failure, timeout, or unresolved custom agent is degraded evidence, not a clean pass. |
| `claude --plugin-dir <package-root>` delivery line | Install the package as a local Codex plugin, install its custom agents, review hooks with `/hooks`, then invoke `$<workflow-name>` | Do not invent a `--plugin-dir` flag for Codex. |

## 3. Role-agent names

The PAVE Init Codex package uses these custom-agent names:

| Canonical role | Codex custom agent |
|---|---|
| `pave-init:system-explorer` | `pave_init_system_explorer` |
| `pave-init:node-planner` | `pave_init_node_planner` |
| `pave-init:pave-material-reviewer` | `pave_init_material_reviewer` |
| `pave-init:research-delegate` | `pave_init_research_delegate` |
| `pave-init:skill-builder` | `pave_init_skill_builder` |
| `pave-init:forward-tester` | `pave_init_forward_tester` |

Before the first real dispatch, confirm that the active Codex spawn tool can
select a custom agent by its declared name. Some runtime/model combinations
have exposed only `task_name`, `message`, and `fork_turns`. `task_name` is a
thread label, not proof that the custom-agent TOML applied. In that state,
pause with `blocked`; do not silently use a generic child for a role whose
independence, sandbox, or reviewer identity is part of the approved graph.

Every dispatch brief starts with:

```text
PAVE_PLUGIN_ROOT: <absolute path to the installed PAVE plugin>
```

The custom agent reads its canonical role file from that root and applies this
binding. If the root or role file is missing, it returns `DEPENDENCY_MISSING`
and does no work.

## 4. Model and reasoning-effort mapping

Keep the canonical allocation policy. Change only vendor model names.

| Role | Codex model | Reasoning effort |
|---|---|---|
| system explorer | inherit active model | `medium` |
| node planner | inherit active model | `high` |
| material reviewer | inherit active model | `high` |
| research delegate | inherit active model | `medium` |
| skill builder | inherit active model | `medium` |
| forward tester | inherit session defaults | inherit session defaults |
| root or approved high-risk planner override | active top-capability model when selectable | `xhigh`, or the highest supported effort |

Do not pin one frontier model on every worker. Preserve the canonical reason:
parallel long runs can exhaust shared capacity and fail before the graph
converges.

## 5. Runtime bindings in generated workflows

The canonical package planner chooses `lead` or `workflow_script` for a
script-eligible subgraph. In Codex, interpret them as follows.

### `lead`

The lead routes the graph through Codex custom-agent threads. It starts
independent nodes together, waits at explicit joins, retains reviewer threads
within a gate, and records every terminal result in persisted run state.

### `workflow_script` → `codex_exec_script`

Use this only when the approved plan already selected `workflow_script` and
the subgraph has enough fan-out or looping to justify a separate control
plane. Generate one deterministic Python or TypeScript script that:

1. Reads the approved PAVE subgraph and a literal node-to-agent binding.
2. Launches one `codex exec --json --ephemeral` process per eligible node,
   with the least sandbox needed by that node.
3. Requires structured output whose outcome enum is exactly the graph’s
   declared outcome codes.
4. Implements edge selection, counters, joins, and exhaustion in ordinary
   code.
5. Returns user gates, `pause`, and `return` endpoints to the lead.
6. Persists node results and evidence paths before routing.
7. Never changes the graph or nests across a composed-profile boundary.

The generated lead states the fallback: when the script or `codex exec` is
unavailable, it runs the same subgraph through custom-agent threads. It never
reports the script path as tested when only the fallback ran.

## 6. Hook binding and known wire differences

The plugin registers hooks in `codex/hooks/hooks.json`. Codex asks the user to
review and trust non-managed hook definitions. This trust step is the Codex
consent gate; installation alone is not silent authorization.

The canonical stop-alignment script is reused without semantic change. Codex
has a distinct `SubagentStop` event, so the registered `Stop` hook remains
root-only.

The two `PostToolUse` controls use adapters:

- `state_staleness_reminder.sh` remains canonical. The adapter passes direct
  Codex `agent_id` and `agent_type` fields to its identity gate. Root calls
  omit those fields; spawned-worker calls include them.
- `planning-layout-warn.sh` remains canonical. The adapter parses Codex
  `apply_patch` text into one path/content payload per file and preserves direct
  Codex caller identity in each expanded payload.

If a Codex runtime omits identity from spawned-worker `PostToolUse` events, the
canonical hooks must treat that caller as the lead. This can send a staleness
reminder to a worker and miss the worker-only `frontier.yaml` warning. Record
that runtime as degraded instead of claiming equivalent hook enforcement.

These controls remain advisory. Schema validation and the canonical prose are
the decline paths when the hook runtime is disabled, untrusted, or unable to
classify a call.

## 7. Generated Codex package shape

When Stage 5 says to build a Claude Code plugin, build this Codex shape instead:

```text
<workflow-name>/
├── .codex-plugin/
│   └── plugin.json
├── skills/
│   └── <workflow-name>/
│       ├── SKILL.md
│       ├── orchestration/        # only when approved
│       ├── references/           # only when approved
│       ├── schemas/              # only when approved
│       └── scripts/              # only when approved
├── codex/
│   ├── agents/                   # custom-agent TOML sources
│   └── install_agents.py         # consented project/user installation
├── hooks/
│   ├── hooks.json                # only when approved
│   └── ...                       # hook scripts and adapters
├── README.md
└── VERSION
```

The manifest `name`, lead skill directory, and generated custom-agent prefix
use one approved workflow name. Agent TOML files are not silently copied into
a target project. The README provides the explicit install step, and the lead
pauses when required agents are not loaded.

## 8. Validation substitutions

The integration battery keeps every canonical check and adds these Codex
checks:

1. Parse `.codex-plugin/plugin.json` and confirm every referenced path stays
   inside the package.
2. Parse every custom-agent TOML with Python `tomllib` and require `name`,
   `description`, and `developer_instructions`.
3. Confirm every required graph role maps to one custom-agent name.
4. Parse `hooks/hooks.json`; verify event names, matchers, commands, and script
   existence.
5. Test `apply_patch` expansion with multi-file add, update, delete, and rename
   fixtures.
6. Confirm worker identity survives `apply_patch` expansion and identity-free
   root payloads still reach the canonical hooks.
7. Exercise the stop hook with an active marker-owned run and confirm one
   continuation followed by its cooldown pass.
8. Search generated files for literal Claude-only launch commands,
   `.claude-plugin`, `subagent_type`, and `SendMessage`. A literal occurrence
   is allowed only inside a clearly labeled comparison or migration note.
9. Install custom agents into a temporary project and verify that a second
   install is idempotent and an unsafe overwrite fails closed.
10. Run the canonical PAVE, traceability, schema, helper, hook, evolution-tier,
    final-review, and clean-room checks unchanged in meaning.

## 9. Resume and completion

Resume, approval evidence, review continuity, repair budgets, terminal
statuses, v0 delivery, and v1 freeze timing are unchanged. The Codex port is a
Runtime Binding. It is not a new Graph Profile.
