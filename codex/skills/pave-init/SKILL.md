---
name: pave-init
description: >-
  Create a PAVE-based Codex CLI workflow plugin from a goal the user states.
  Manual-only: use this skill only when the user explicitly invokes
  `$pave-init` or selects `pave-init` through `/skills`. It runs a long
  multi-stage campaign with goal clarification, parallel subagent exploration,
  a formal PAVE YAML graph, material-only adversarial review, plugin
  construction, validation, and clean-room forward testing. The plugin bundles
  three disclosed lifecycle controls: a stop-alignment check, a throttled
  run-state staleness reminder, and a non-blocking planning-layout warning.
---

# PAVE Init — Codex CLI

This is the Codex Runtime Binding of the canonical PAVE Init skill. It keeps
one source of workflow meaning instead of maintaining a prose fork.

## Load the canonical contract

Before doing any PAVE work:

1. Resolve `PAVE_PLUGIN_ROOT` as the absolute path three directories above
   this file: `<root>/codex/skills/pave-init/SKILL.md` → `<root>`.
2. Read `<root>/codex/runtime-binding.md`.
3. Read `<root>/skills/pave-init/SKILL.md`. Ignore that file's YAML
   frontmatter. Treat its Markdown body as the canonical lead contract.
4. Apply the Codex binding recursively to every active-stage file the
   canonical contract tells you to read.

Follow canonical prose verbatim unless `runtime-binding.md` gives an exact
platform substitution. The binding can replace only runtime mechanics. It
cannot change PAVE meaning, graph topology, authority, evidence, approval,
review, recovery, or completion.

Resolve conflicts in this order:

1. Explicit user decisions and approvals from the current run.
2. The approved or frozen PAVE graph.
3. The PAVE specification and schema references.
4. The approved package plan and traceability record.
5. The canonical PAVE Init lead, orchestration, and role contracts.
6. `codex/runtime-binding.md`, only for Codex-specific execution mechanics.
7. This loader.

## Manual invocation

Start only after one of these explicit actions:

- the user invokes `$pave-init` with or without a goal; or
- the user opens `/skills` and selects `pave-init` for this task.

A natural-language request such as “make this a workflow” is not an implicit
invocation. Do not start the campaign from description matching alone.

## Required Codex binding

Before Stage 1, verify:

- the six `pave_init_*` custom agents in `codex/runtime-binding.md` are loaded
  from project or user Codex configuration;
- the plugin root and canonical resources are readable;
- Python 3 is available;
- `yaml` and `jsonschema` import successfully, as the canonical skill
  requires; and
- the user has reviewed the plugin hooks through `/hooks`, or the run records
  the canonical degraded-enforcement path.

If required custom agents are absent, or the active `spawn_agent` surface cannot
target a custom agent by name, pause and report the install and runtime check
from `<root>/codex/README.md`. Do not put a role name in `task_name` and assume
that its TOML loaded. Do not substitute a built-in or generic worker and claim
that the registered-role or adversarial-review contract passed.

## Codex dispatch rules

Whenever the canonical skill says to dispatch a role:

- use the mapped custom-agent name in `codex/runtime-binding.md`;
- start the brief with `PAVE_PLUGIN_ROOT: <absolute root>`;
- preserve the complete canonical task context and file boundaries;
- start independent workers together so they can run concurrently;
- wait only at the graph's declared join or gate;
- retire one-shot threads after their terminal result; and
- retain one exact material-reviewer thread through every repair round at its
  gate. Ask Codex to steer that existing thread. A fresh thread is a new
  reviewer and cannot inherit the old review identity.

Use the active Codex model. Preserve the role's reasoning effort mapping.
Reserve `xhigh`, or the highest supported effort, for the root planner and an
approved high-risk planner. Do not pin one frontier model on every worker.

## User decisions

Codex has no `AskUserQuestion` tool with the same contract. Ask bounded
questions in plain text. Give two to four clear options when useful. Present
approval briefs in full. Require an explicit response that names what the user
approves. Silence, file existence, and a generic “continue” are not approval.

## Generated packages

Whenever the canonical skill says to generate a Claude Code plugin, generate
the Codex package in `codex/runtime-binding.md` §7:

- `.codex-plugin/plugin.json`;
- a lead skill under `skills/<workflow-name>/`;
- custom-agent TOML sources plus an explicit installer when distinct roles are
  required;
- plugin-level `hooks/hooks.json` when the approved enforcement record calls
  for hooks;
- approved orchestration, references, schemas, scripts, tests, README, and
  VERSION files; and
- no unapproved auxiliary files or duplicated policy.

Custom-agent installation is an explicit runtime dependency. Do not hide it
inside a hook or mutate a target project's `.codex/agents/` without consent.

When the canonical plan selects a `workflow_script` Runtime Binding, apply the
`codex_exec_script` mapping in `codex/runtime-binding.md` §5. The approved
PAVE graph remains authoritative, and the generated lead retains the
lead-driven custom-agent fallback.

## Hooks

The plugin manifest registers `codex/hooks/hooks.json`. Do not copy the
canonical skill-frontmatter hook block into a generated Codex skill.

The canonical stop script runs directly. Codex adapters preserve the two
PostToolUse controls across these wire differences:

- `apply_patch` carries patch text rather than Claude `file_path` and
  `content`; and
- PostToolUse caller identity is preserved when the runtime supplies it; a
  session-scoped activity latch fails safe when it does not.

The controls remain observing or Socratic at the same enforcement rung. Hook
trust refusal or runtime absence follows the canonical prose-and-resume
decline path and must be recorded, never hidden.

## Validation and clean-room test

Run every canonical validation gate. Add the Codex-specific battery in
`codex/runtime-binding.md` §8 and the repository tests:

```bash
python3 -m unittest codex.tests.test_codex_port
```

For the clean-room forward test, install the generated plugin and its custom
agents into a temporary project, then run from that project:

```bash
codex exec --ephemeral --sandbox workspace-write "<representative prompt>"
```

Use the explicit `$<workflow-name>` invocation for a manual-only skill. A run
that cannot load the plugin, resolve a custom agent, write its temporary
artifacts, or complete before its limit is degraded evidence. Do not report it
as a clean pass.

## Delivery

Keep the canonical v0 delivery and v1 freeze boundary unchanged. Report:

- generated Codex plugin path and README;
- explicit plugin, custom-agent, and hook-trust installation steps;
- canonical PAVE YAML and manifest state;
- validation, review, and clean-room evidence;
- known gaps and runtime dependencies; and
- any Codex binding degradation recorded during the run.

Resume, repair, review, and stopping behavior remain exactly as stated in the
canonical skill and graph.
