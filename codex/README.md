# PAVE Init for Codex CLI

This directory contains the native Codex package for PAVE Init. One shared,
stage-oriented source under `sources/` generates the complete Claude Code and
Codex lead skills and role definitions. The installed Codex skill does not load
or translate the Claude skill at run time.

That structure follows PAVE's own layer model:

- the PAVE design language and Graph Profile stay unchanged;
- the Codex files provide a different Runtime Binding; and
- each live run still writes the same Workflow Run state and evidence.

## Package contents

```text
.codex-plugin/plugin.json             Codex plugin manifest
codex/skills/pave-init/SKILL.md       generated native Codex lead skill
codex/agents/*.toml                   generated native custom-agent contracts
codex/install_agents.py               explicit project/user agent installer
codex/hooks/hooks.json                Codex lifecycle registration
codex/hooks/post_tool_use_router.py   apply_patch wire adapter
codex/tests/test_codex_port.py        stdlib validation and hook tests
sources/pave-init/SKILL.md.tmpl       shared workflow source
sources/roles/*.md.tmpl               shared role sources
sources/bindings/*.toml               narrow harness mechanics
scripts/build_packages.py             deterministic materializer and drift check
```

The committed native outputs are installation artifacts and say `DO NOT EDIT`.
Change the shared sources or binding records, run
`python3 scripts/build_packages.py --check`, inspect the diff, then run
`python3 scripts/build_packages.py --force`. A normal build refuses to replace
any differing generated file, so a hand edit cannot disappear silently.

## Install for one project

1. Install or enable this repository as a local Codex plugin. The manifest is
   `.codex-plugin/plugin.json`.
2. Trust the target project before relying on project-scoped Codex files.
   Accept the Codex trust prompt, or add the absolute target path to
   `~/.codex/config.toml`:

   ```toml
   [projects."/absolute/path/to/target-repository"]
   trust_level = "trusted"
   ```

   Codex ignores project-scoped `.codex/` configuration, hooks, rules, and
   custom agents while the project is untrusted. Trust only repositories you
   have reviewed.
3. Configure Codex V2 at project scope in `.codex/config.toml`:

   ```toml
   [features]
   multi_agent = true

   [features.multi_agent_v2]
   enabled = true

   [agents]
   enabled = true
   max_concurrent_threads_per_session = 16
   ```

   This gives each session 16 child slots and 17 total V2 slots. Remove
   `agents.max_depth`; it applies only to V1.

   Codex layers user, project, profile, and command-line configuration. The live
   preflight below is the authority for the effective runtime.
4. Install the required custom agents into the target project:

   ```bash
   python3 codex/install_agents.py --project /path/to/target-repository
   ```

5. Restart Codex in the target project. Codex loads custom-agent TOML files at
   session start.
6. Open `/hooks`, review the three PAVE controls, and trust them when their
   paths and commands match this package.
7. Run the effective-config preflight before the first campaign:

   ```bash
   python3 codex/preflight.py \
     --project /path/to/target-repository \
     --evidence-dir /tmp/pave-init-preflight
   ```

   This command must prove `root → pave-init:pave-material-reviewer →
   pave-init:research-delegate` from the three persisted Codex thread records.
   A model-written success message is not enough.
8. Invoke the skill explicitly:

   ```text
   $pave-init:pave-init turn <system> into a workflow skill
   ```

Selecting `pave-init:pave-init` through `/skills` is also an explicit invocation. A
plain request such as “make a workflow” does not start this manual-only
campaign.

## Install custom agents for the user

```bash
python3 codex/install_agents.py --user
```

User-scoped agents apply to every Codex project. Project scope is safer when
you use PAVE only in selected repositories. Put the same V2 settings under
`[features]` and `[agents]` in `$CODEX_HOME/config.toml` before user installation.

Check or remove the installed files:

```bash
python3 codex/install_agents.py --project /path/to/repo --check
python3 codex/install_agents.py --project /path/to/repo --uninstall
```

The installer owns only the eight safe file names `pave_init_*.toml` listed in
its manifest. Each file declares its namespaced `pave-init:<role>` runtime
name. The installer refuses to overwrite or remove an unowned modified file
unless you pass `--force`.

## What changed from Claude Code

The narrow harness binding is `sources/bindings/codex.toml`. Its load-bearing
differences are:

- `$pave-init:pave-init` replaces `/pave-init`.
- `.codex-plugin/plugin.json` replaces `.claude-plugin/plugin.json`.
- Codex custom-agent TOML files replace registered Markdown agent manifests.
- Codex agent tools replace `Agent`, `subagent_type`, and `SendMessage`:
  `spawn_agent` starts a named thread, `followup_task` continues a retained
  thread, `wait_agent` waits for it, and `interrupt_agent` stops a thread that
  still runs after its node instance closed. A finished thread needs no close.
- Plain-text bounded questions replace `AskUserQuestion`; explicit approval
  remains mandatory.
- Role reasoning effort is preserved. Claude `fable` and `opus` map to
  `gpt-5.6-sol`; Claude `sonnet` maps to `gpt-5.6-terra`.
- Nested dispatch uses Codex V2 with 16 child slots and 17 total slots.
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
python3 -m unittest codex.tests.test_release_tools
python3 scripts/build_packages.py --check
python3 scripts/stamp_version.py --check
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool codex/hooks/hooks.json >/dev/null
bash -n skills/pave-init/hooks/*.sh
```

The unit suites check plugin and hook structure, custom-agent model and effort
mapping, nested-rollout proof, release stamps, installer safety, apply-patch
expansion, caller-identity preservation, and integration with the canonical
PAVE hooks.

## Clean-room forward test

Create a temporary git repository. Install the current generated plugin and its
custom agents there. Then run the release proof from this repository:

```bash
python3 codex/preflight.py \
  --release \
  --project /path/to/temporary-repository \
  --evidence-dir /tmp/pave-init-release-preflight
```

`--release` forces Codex V2, 16 child slots, and trust for only the exact test
project in that process. It does not change durable user configuration. Do not
use the user's live target as the clean room. A run that cannot load the current
source hash, resolve both named agents, prove the canonical depth-2 task path,
and complete two turns on one reviewer thread is a release failure.

## Known runtime limitation

### Custom-agent selection and sandbox provenance

PAVE Init fails closed unless each V2 spawn returns one canonical task path and
the child rollout persists the expected role instructions, model, effort, and
effective sandbox. A project that contains any PAVE agent file must contain the
complete current eight-file set; this prevents stale project roles from
shadowing current user roles.

Codex 0.153.2 preserves the parent permission state when it applies a custom
role. The read-only release probe therefore proves the effective read-only
sandbox for the reviewer and delegate, but it cannot prove that their TOML
`sandbox_mode` caused that state. PAVE Init keeps every declared role sandbox
unchanged and records this Codex runtime limit instead of claiming provenance.

### PostToolUse identity

Current Codex source attaches `agent_id` and `agent_type` to `PostToolUse`
events from spawned workers. Root calls omit them. V2 task paths do not replace
this hook identity. The adapter preserves these fields and delegates all
lead-versus-worker decisions to the canonical hooks.

A runtime that omits worker identity cannot preserve the identity-sensitive
parts of the policy: a worker can receive the lead-only staleness reminder, and
its `frontier.yaml` write can look lead-owned. Record that runtime as degraded
instead of claiming equivalent hook enforcement.
