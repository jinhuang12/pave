# AMMO hooks for Claude Code

This dir holds the Claude Code hooks of the AMMO plugin. `hooks.json` registers them. A hook acts only inside an AMMO campaign. Outside a campaign it exits 0 and prints nothing.

## Campaign scope

A campaign is the dir `<engine>/.ammo/<campaign-id>/`. The `.ammo/active` marker or `AMMO_ARTIFACT_DIR` names it. `references/campaign-env.md` lists every name the campaign sets.

Every hook uses the helper `_find_campaign.sh` in the same way:

```bash
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0          # not in a campaign: stay silent
ammo_in_campaign "$FILE_PATH" && ...       # the one path classifier
ammo_python ammo_state.py validate ...     # a plugin script, campaign python
```

- `_find_campaign` looks up from the hook input `cwd`, then from `CLAUDE_PROJECT_DIR`, then from the current dir. An `AMMO_ARTIFACT_DIR` that is set wins. On a hit it exports the campaign names that are not set yet.
- `ammo_in_campaign PATH` is true when PATH resolves inside the campaign dir.
- `ammo_python SCRIPT` runs `scripts/ammo`, which runs a plugin script with the campaign python.
- No hook names the old artifact root or guesses the engine python from a fixed path. The state schema is `${CLAUDE_PLUGIN_ROOT}/schemas/state.schema.json`.

Two hooks must act outside a campaign too, because Claude Code gives them no way to decline:

- WorktreeCreate: Claude Code needs a worktree path. Outside a campaign the hook adds a plain git worktree on branch `worktree-<name>` and prints its path. Inside a campaign it builds a track worktree.
- WorktreeRemove: Claude Code counts the worktree as removed when the hook exits 0, and it never deletes the branch. So the hook removes the worktree in and out of a campaign. It deletes a `worktree-<name>` branch and keeps a track branch.

`tests/test_hooks_silent.py` runs every registered command outside a campaign. Each one must exit 0 with empty stdout. WorktreeCreate must print one worktree path.

## Kept hooks

The intent of each hook comes from the ammo-server intent map (`docs/ammo-intent-map.json`, key `map.hooks`). `hooks.json` holds the exact matchers.

| Hook | Event | Matcher | Intent |
|---|---|---|---|
| `ammo-team-spawn-guard.sh` | PreToolUse | Agent | Deny a team-member spawn without a name |
| `ammo-pretool-guard.sh` | PreToolUse | Bash | Block commands that break measurement integrity |
| `ammo-skill-files-guard.sh` | PreToolUse | Edit, Write, MultiEdit, NotebookEdit | Deny subagent edits under the plugin root |
| `ammo-env-default-guard.sh` | PreToolUse | Edit, Write | Block a new engine flag with a truthy default |
| `worktree-create-with-build.sh` | PreToolUse | EnterWorktree | Build the track worktree when the tool is called |
| `worktree-create-with-build.sh` | WorktreeCreate | any | Track worktree with build and venv; plain one outside |
| `ammo-precompact.sh` | PreCompact | any | Save campaign facts from `state.json` before compaction |
| `ammo-postcompact.sh` | SessionStart | any | Re-orient the lead after compaction or a fresh start |
| `ammo-stop-guard.sh` | Stop | any | Stop the lead from ending its turn too early |
| `ammo-subagent-release.sh` | SubagentStop | any | Release the GPU reservations of a departing subagent |
| `audit-phase2-guard.sh` | SubagentStop | `^(ammo:)?ammo-auditor$` | Hold the auditor until its Phase 2 is written |
| `ammo-gpu-release.sh` | PostToolUse | Bash | Release Layer-2 GPU reservations after a command |
| `ammo-monitor-reminder.sh` | PostToolUse | Agent | Pair each impl-champion with a transcript monitor |
| `ammo-audit-start-stamp.sh` | PostToolUse | Agent | Stamp the audit start when the lead spawns an auditor |
| `ammo-state-validate.sh` | PostToolUse | Write, Edit, Bash | Validate `state.json` against the schema and invariants |
| `ammo-next-step-reminder.sh` | PostToolUse | Bash, Write, Edit | Keep the lead on the stage ladder |
| `ammo-artifact-layout-warn.sh` | PostToolUse | Write, Edit, Bash | Warn when a file lands outside the artifact layout |
| `inject-audit-phase2.sh` | PostToolUse | Write | Give the auditor its checklist after Phase 1 |
| `worktree-remove-cleanup.sh` | WorktreeRemove | any | Remove a worktree and its plain branch |

These files have no registration of their own:

| File | Role |
|---|---|
| `_find_campaign.sh` | Campaign discovery for every hook |
| `_ammo_is_lead.sh` | One answer to "lead or subagent?" |
| `pumice_env_allowlist.txt` | Engine env names both env guards accept |

`ammo-team-spawn-guard.sh` stays because it enforces the team-member vs subagent contract of `skills/ammo/SKILL.md`. Its header gives the reason.

The plugin agent names are `ammo:<role>`. Every hook strips an optional `ammo:` prefix before it matches a role.

## Dropped hooks

| Hook | Reason |
|---|---|
| `ammo-venv-python-guard.sh` | Matched the server skill path; `scripts/ammo` now picks the campaign python |
| `ammo-pip-guard.sh` | The user builds and owns the engine venv; preflight checks it |
| `ammo-agent-cwd-guard.sh` | Keyed on the server session root; the drift reminder stays |
| `ammo-msg-mirror.sh` | Copied every SendMessage into a side channel; campaign hooks only |
| `ammo-msg-check.sh` | Its channel has no writer; Claude Code delivers messages at turn boundaries |
| `ammo-statusline.sh` | Status line display for the server terminal; enforces nothing |

More detail on each reason:

- `ammo-venv-python-guard.sh` blocked a sweep script that ran under a bare `python`. Its match was the script path of the server install. The plugin runs its scripts through `scripts/ammo`, and the engine python is the `engine.venv` that `target.json` records. `ammo-pretool-guard.sh` names the `scripts/ammo` form in its sweep message.
- `ammo-pip-guard.sh` denied every package install in the session. On a host the user builds Pumice and its venv before the campaign, and preflight checks them. The agent prompts still say: never install packages.
- `ammo-agent-cwd-guard.sh` denied an Agent spawn when the lead's cwd was not `CLAUDE_PROJECT_DIR`, the server session root. On a host the project dir need not be the engine root. `ammo-next-step-reminder.sh` still warns the lead when its cwd is inside a track worktree.
- `ammo-msg-mirror.sh` ran on every SendMessage, in a campaign or not. The plugin ships campaign hooks only.
- `ammo-statusline.sh` is a status line command, not an event hook. It showed session facts in the server terminal.
- `ammo-msg-check.sh` injected undelivered team messages before a tool call. It read the side channel that `ammo-msg-mirror.sh` wrote, and the team inbox. After msg-mirror went, nothing writes the channel. On Claude Code 2.1.202 and later the team inbox is emptied within about one second of each send, so the hook had nothing to read. Claude Code delivers each message at the next turn boundary. Its helper `_ammo_msg_identity.sh` and its harness went with it.

## How `hooks.json` is made

Do not edit `hooks.json` by hand. The pave repo generates it with `planning-records/ammo/gen_hooks_json.py` from the ammo-server hook registrations:

1. Keep a hook on the keep list, skip a hook on the drop list, and stop on any other hook, so a new source hook forces a decision.
2. Change each command to `"${CLAUDE_PLUGIN_ROOT}/hooks/<script>"`.
3. Remove groups that are left empty. The source status line block is not a hook and is not read.
4. Add the auditor's two hooks. They were in the auditor agent's frontmatter, and Claude Code ignores hook frontmatter in plugin agents. The frontmatter Stop hook runs as SubagentStop, and its matcher keeps it on the auditor.

`python3 planning-records/ammo/gen_hooks_json.py --check` exits 1 when `hooks.json` differs. `tests/test_hooks_json.py` regenerates the file and compares.

## Codex

The Codex hooks are Python files under `codex/hooks/`. They register in `codex/hooks.json`, which `.codex-plugin/plugin.json` names. Codex uses other events and matchers (SubagentStart, PreCompact, and tool matchers such as `apply_patch`, `spawn_agent` and `mcp__.*`), so the two runtimes do not share one registration file.

The Codex hooks follow the same scope rule: outside a campaign every Codex hook exits 0 with empty stdout.

The two runtimes share `pumice_env_allowlist.txt`: `ammo-env-default-guard.sh` and `codex/hooks/pre_tool_use_guard.py` both read it from this dir.

Two Codex twins are dropped with their Claude Code hooks: the package-install deny (twin of `ammo-pip-guard.sh`) and the sweep-script venv guard (twin of `ammo-venv-python-guard.sh`). The Codex worktree venv guard stays. It is the twin of the kept `ammo-pretool-guard.sh`.

## Tests

- `hooks/tests/test-*.sh`: one harness per hook. Run `for t in hooks/tests/test-*.sh; do bash "$t"; done` from the plugin root. Linux is the judge: some harnesses need `flock` or GNU `stat`, which macOS does not have.
- `tests/test_hooks_silent.py`: every registered command is silent outside a campaign. Linux only.
- `tests/test_hooks_json.py`: `hooks.json` matches the generator, and every command path exists.
- `tests/test_env_default_guard_hook.py` and `tests/test_worktree_hook.py`: the env guard and the worktree hook.
