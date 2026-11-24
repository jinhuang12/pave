# PAVE Init for Antigravity CLI

This directory is the self-contained native Antigravity CLI (`agy`) plugin package for PAVE Init.

## Phase 1 scope

Phase 1 ships the **`orchestrate`** skill bound to Antigravity's native **`swarm`** primitive, together with its three custom role agents and three lifecycle hooks:

- **Skill:** `skills/orchestrate/SKILL.md` (kept under Antigravity's 24,000-byte skill-body cap via `references/execution-and-close.md`)
- **Agents:**
  - `agents/orchestrate-worker.md` (`model: "pro"`) — read by `worker-N` swarm members at startup
  - `agents/adversarial-investigator.md` (`model: "inherit"`) — read by `da-M` swarm members at startup, also usable standalone
  - `agents/research-delegate.md` (`model: "flash"`) — dispatched off-board via `invoke_subagent` for read-only lookups
- **Hooks (`hooks.json` + `hooks/ag_hook_adapter.py`):**
  - `SessionStart`: `goal_restate.sh` (restate open run goal, user rules, `orchestrate_log.py --runtime antigravity status`, and `swarm(action: "status")`)
  - `PostToolUse` (`run_command`) → `PostInvocation`: `speedup_nudge.sh` (at most once a minute after a lead run-log write)
  - `PostToolUse` (`write_file|edit_file|write_to_file|replace_file_content`) → `PostInvocation`: `maintainer_nudge.sh` (at most once a minute inside `orchestrate-worker` conversations)

`pave-init` and `pave-evolve` will be added in a later phase so no truncated or incomplete skills load in Antigravity CLI.

## Regenerating

The generated files under `antigravity/skills/` and `antigravity/agents/` are materialized from `sources/` and `skills/orchestrate/` by `scripts/build_packages.py`:

```bash
python3 scripts/build_packages.py --check
python3 scripts/build_packages.py --force
```

## Validate and install

```bash
agy plugin validate --strict ./antigravity
agy plugin install ./antigravity
```

In a fresh `agy` session, run `/hooks` to inspect the registered `orchestrate` hooks and invoke `/orchestrate <task>` to run an orchestrated swarm.
