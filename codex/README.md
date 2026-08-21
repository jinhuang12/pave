# PAVE Init for Codex CLI

This directory is a Codex Runtime Binding for the canonical PAVE Init
meta-skill. It does not fork the workflow prose. The Codex lead skill reads the
canonical files under `skills/pave-init/` and applies only the platform
substitutions in `runtime-binding.md`.

That structure follows PAVE's own layer model:

- the PAVE design language and Graph Profile stay unchanged;
- the Codex files provide a different Runtime Binding; and
- each live run still writes the same Workflow Run state and evidence.

## Package contents

```text
.codex-plugin/plugin.json             Codex plugin manifest
codex/skills/pave-init/SKILL.md       manual-only Codex lead loader
codex/runtime-binding.md              exact semantic substitution contract
codex/agents/*.toml                   custom-agent adapters for canonical roles
codex/install_agents.py               explicit project/user agent installer
codex/hooks/hooks.json                Codex lifecycle registration
codex/hooks/post_tool_use_router.py   apply_patch wire adapter
codex/tests/test_codex_port.py        stdlib validation and hook tests
```

The canonical role prose remains in `agents/*.md`. Each custom-agent adapter
loads its matching role body before work. This prevents two platform copies of
review, evidence, and planning policy from drifting.

## Install for one project

1. Install or enable this repository as a local Codex plugin. The manifest is
   `.codex-plugin/plugin.json`.
2. Install the required custom agents into the target project:

   ```bash
   python3 codex/install_agents.py --project /path/to/target-repository
   ```

3. Restart Codex in the target project. Codex loads custom-agent TOML files at
   session start.
4. Open `/hooks`, review the three PAVE controls, and trust them when their
   paths and commands match this package.
5. Invoke the skill explicitly:

   ```text
   $pave-init turn <system> into a workflow skill
   ```

Selecting `pave-init` through `/skills` is also an explicit invocation. A
plain request such as “make a workflow” does not start this manual-only
campaign.

## Install custom agents for the user

```bash
python3 codex/install_agents.py --user
```

User-scoped agents apply to every Codex project. Project scope is safer when
you use PAVE only in selected repositories.

Check or remove the installed files:

```bash
python3 codex/install_agents.py --project /path/to/repo --check
python3 codex/install_agents.py --project /path/to/repo --uninstall
```

The installer owns only the six `pave_init_*.toml` files listed in its manifest.
It refuses to overwrite or remove an unowned modified file unless you pass
`--force`.

## What changed from Claude Code

The complete table is in `runtime-binding.md`. The load-bearing changes are:

- `$pave-init` replaces `/pave-init`.
- `.codex-plugin/plugin.json` replaces `.claude-plugin/plugin.json`.
- Codex custom-agent TOML files replace registered Markdown agent manifests.
- Codex subagent spawning and thread steering replace `Agent`,
  `subagent_type`, and `SendMessage`.
- Plain-text bounded questions replace `AskUserQuestion`; explicit approval
  remains mandatory.
- Role reasoning effort is preserved, but Claude model names are not copied.
- Plugin-level `hooks/hooks.json` replaces skill-frontmatter hook registration.
- `apply_patch` needs a path/content adapter for the planning-layout hook.
- Direct Codex caller identity is preserved so the canonical hooks remain the
  only authority for lead-versus-worker policy.
- `codex_exec_script` is the compiled-subgraph equivalent of a Claude
  `Workflow` script.
- A clean-room test installs the package and runs `codex exec --ephemeral
  --sandbox workspace-write`, rather than using Claude's `--plugin-dir` mode.

No node, edge, review threshold, evidence rule, approval gate, repair budget,
terminal status, or revision rule changes.

## Hooks

The Codex plugin registers:

1. A throttled run-state staleness reminder after Bash or file-edit tools.
2. A non-blocking planning-layout warning after file edits.
3. A Socratic stop-alignment check with the canonical cooldown.

The canonical Bash scripts remain the policy implementations. The Codex Python
file adapts only the `apply_patch` wire format and preserves caller identity.

Codex requires explicit trust for non-managed hooks. A disabled or untrusted
hook is a recorded enforcement degradation. The canonical resume, checkpoint,
and validation duties remain the fallback.

## Validate

From the repository root:

```bash
python3 -m unittest codex.tests.test_codex_port
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool codex/hooks/hooks.json >/dev/null
bash -n skills/pave-init/hooks/*.sh
```

The unit suite checks plugin and hook structure, custom-agent TOML, installer
safety, apply-patch expansion, caller-identity preservation, and integration
with the canonical PAVE hooks.

## Clean-room forward test

Create a temporary git repository. Install the generated plugin and its custom
agents there. Then run from that repository:

```bash
codex exec --ephemeral --sandbox workspace-write \
  '$pave-init <representative approved request>'
```

Do not use the user's live target as the clean room. A run that cannot load the
plugin, custom agents, hooks, or write permissions is degraded evidence. It is
not a clean pass.

## Known runtime limitation

### Custom-agent selection

Codex documents custom agents under `.codex/agents/*.toml`, but some
runtime and GPT-5.6 combinations have exposed a spawn tool with only a
task label and prompt. A matching `task_name` does not prove that the
custom-agent TOML, sandbox, effort, or developer instructions loaded.
PAVE Init therefore fails closed when it cannot select a role by name. It
does not replace the material reviewer or another registered role with a
generic child and report an equivalent pass.

### PostToolUse identity

Current Codex source attaches `agent_id` and `agent_type` to `PostToolUse`
events from spawned workers. Root calls omit them. The adapter preserves these
fields and delegates all lead-versus-worker decisions to the canonical hooks.

A runtime that omits worker identity cannot preserve the identity-sensitive
parts of the policy: a worker can receive the lead-only staleness reminder, and
its `frontier.yaml` write can look lead-owned. Record that runtime as degraded
instead of claiming equivalent hook enforcement.
