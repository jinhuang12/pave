# Harness Binding

This file maps each agent operation in the campaign to the tool or idiom of
each harness. The lead skill, the orchestration files, and this file are the
only places that choose a harness. Stage, gate, audit, and role names are the
same on every harness.

Role files and some orchestration examples show the Claude Code form. On Codex,
read each Claude Code tool name in them through the table below.

## Role Addresses

Every role spawns at its plugin address `ammo:<role>`, for example
`ammo:ammo-auditor`. The address is `subagent_type` on Claude Code and
`agent_type` on Codex.

| Role file | Claude Code address | Codex addresses |
|---|---|---|
| `agents/ammo-researcher.md` | `ammo:ammo-researcher` | `ammo:ammo-researcher` |
| `agents/ammo-champion.md` | `ammo:ammo-champion` | `ammo:ammo-champion` |
| `agents/ammo-impl-champion.md` | `ammo:ammo-impl-champion` | `ammo:ammo-impl-champion`, `ammo:ammo-implementer` |
| `agents/ammo-transcript-monitor.md` | `ammo:ammo-transcript-monitor` | `ammo:ammo-transcript-monitor` |
| `agents/ammo-auditor.md` | `ammo:ammo-auditor` | `ammo:ammo-auditor` |
| `agents/ammo-delegate.md` | `ammo:ammo-delegate` | `ammo:ammo-delegate` |
| `agents/ammo-investigator.md` | `ammo:ammo-investigator` | `ammo:ammo-investigator` |
| `agents/ammo-resolver.md` | `ammo:ammo-resolver` | `ammo:ammo-resolver` |
| `agents/ammo-report-writer.md` | `ammo:ammo-report-writer` | `ammo:ammo-report-writer`, `ammo:ammo-report` |

Codex installs eleven agents. The two aliases read the same role file as the
name beside them. Use either name. Do not create a second agent for one task.

## Operations

Codex V2 is the subscription mode with the `agents` tools. Codex V1 is the
Bedrock mode with the `multi_agent_v1` tools. `AMMO_CODEX_AGENT_MODE=v1`
selects V1. Do not change the mode in an active campaign.

| Operation | Claude Code | Codex V2 | Codex V1 |
|---|---|---|---|
| Invoke the lead skill | `/ammo:ammo` | `$ammo:ammo` | `$ammo:ammo` |
| Spawn a one-shot role | `Agent(subagent_type=..., prompt=...)`, no `name` | `agents.spawn_agent(task_name=..., agent_type=..., fork_turns="none", message=...)` | `multi_agent_v1.spawn_agent(agent_type=..., fork_context=false, message=...)` |
| Spawn a named team member | `Agent` with `name` and `run_in_background=True` | spawn with a reusable `task_name` | spawn, then keep the returned UUID |
| Team | lead's implicit team, no TeamCreate | none | none |
| Track worktree | `isolation: worktree` frontmatter | lead creates it first | lead creates it first |
| Start work on a reused agent | `SendMessage` | `agents.followup_task` | `multi_agent_v1.send_input(target=UUID, message=..., interrupt=false)` |
| Send information only | `SendMessage` | `agents.send_message` | intervention queue |
| Wait for an agent | completion notice | bounded `agents.wait_agent` | `multi_agent_v1.wait_agent(targets=[UUID], timeout_ms=10000)` |
| Ask a member to stop | `shutdown_request` message | `agents.interrupt_agent` if still running | `multi_agent_v1.close_agent(target=UUID)` |
| Hard stop | `TaskStop` on the name | `agents.interrupt_agent` | `multi_agent_v1.close_agent(target=UUID)` |
| Reopen a closed agent | not needed | not needed | `multi_agent_v1.resume_agent(id=UUID)` |
| Roster after a reset | `ListAgents` | `agents.list_agents` | the agent registry |
| Return a result | final message | final message | final message |
| Long command | Bash with `run_in_background` | `exec_command` with `tty=false` | `exec_command` with `tty=false` |
| Edit a file | `Edit` or `Write` | `apply_patch` | `apply_patch` |
| Champion transcript | Claude Code projects dir | resolve by task path | resolve by UUID |
| Recovery note | not used | `recovery_note_path` | `recovery_note_path` |
| Audit start stamp | `ammo-audit-start-stamp.sh` | Codex post-tool hook | Codex post-tool hook |

## Claude Code Notes

- A one-shot role (researcher, auditor, delegate, investigator, resolver,
  report writer) spawns with `subagent_type` and `prompt` only. Its final
  message returns to the agent that spawned it. A `name` would make it a team
  member, and its answer would go to the lead.
- A team member (champion, impl-champion, transcript monitor) spawns with a
  `name` and `run_in_background=True`. It joins the lead's implicit team. Do
  not pass `team_name`. Address it by that name.
- The impl-champion's `isolation: worktree` frontmatter plus the
  `WorktreeCreate` hook (`worktree-create-with-build.sh`) create the track
  branch and worktree under the engine root's `.claude/worktrees/`.
- A member stops only when it returns a structured `shutdown_response`.
  `references/shutdown-protocol.md` owns that exchange.
- The champion's transcript is under
  `$CLAUDE_CONFIG_DIR/projects/<slug>/`, default `~/.claude/projects/<slug>/`.
  The slug is the engine root path with each character other than a letter or
  a digit replaced by `-`.

## Codex Notes

- `references/codex-runtime.md` selects the guide for the agent mode:
  `references/codex-runtime-v2.md` or `references/codex-runtime-v1.md`. The
  guide owns task names, the V1 `AMMO_TASK` line and binding wait, the V1
  registry, release, audit dispatch, transcript lookup, and capacity.
  `references/context-recovery.md` owns recovery notes and tracked commands.
- Every spawn message starts with the `plugin_root:` line. A V1 message puts
  its `AMMO_TASK=` line next.
- The lead creates each track worktree before it spawns the impl-champion, with
  the worktree script that `codex/RUNTIME.md` names. The worktree goes under
  the engine root's `.codex/worktrees/`. Pass its absolute path in the spawn
  message.
- Interrupt or close is never proof that work finished or that a gate passed.
