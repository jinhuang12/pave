# Harness Binding

This file maps each agent operation in the campaign to the tool or idiom of
each harness. The lead skill, the orchestration files, and this file are the
only places that choose a harness. Stage, gate, audit, and role names are the
same on every harness.

Role files and some orchestration examples show the Claude Code form. On Codex,
read each Claude Code tool name in them through the table below.

## Role Addresses

Every AMMO role spawns at its plugin address `ammo:<role>`, for example
`ammo:ammo-auditor`. The investigator comes from the pave-init plugin and keeps
its own address. The address is `subagent_type` on Claude Code and
`agent_type` on Codex.

| Role file | Claude Code address | Codex addresses |
|---|---|---|
| `agents/ammo-researcher.md` | `ammo:ammo-researcher` | `ammo:ammo-researcher` |
| `agents/ammo-champion.md` | `ammo:ammo-champion` | `ammo:ammo-champion` |
| `agents/ammo-impl-champion.md` | `ammo:ammo-impl-champion` | `ammo:ammo-impl-champion`, `ammo:ammo-implementer` |
| `agents/ammo-transcript-monitor.md` | `ammo:ammo-transcript-monitor` | `ammo:ammo-transcript-monitor` |
| `agents/ammo-auditor.md` | `ammo:ammo-auditor` | `ammo:ammo-auditor` |
| `agents/ammo-delegate.md` | `ammo:ammo-delegate` | `ammo:ammo-delegate` |
| pave-init plugin | `pave-init:adversarial-investigator` | `pave-init:adversarial-investigator` |
| `agents/ammo-resolver.md` | `ammo:ammo-resolver` | `ammo:ammo-resolver` |
| `agents/ammo-report-writer.md` | `ammo:ammo-report-writer` | `ammo:ammo-report-writer`, `ammo:ammo-report` |

This plugin installs ten Codex agents, and pave-init's installer adds the
investigator. The two aliases read the same role file as the name beside them.
Use either name. Do not create a second agent for one task.

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
| Track worktree | champion calls `EnterWorktree` (Claude Code Notes) | lead creates it first | lead creates it first |
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

## Stage 2 Family Wave

Stage 2 has two seats of its own, the family worker and the family
reviewer (`orchestration/mining-orchestration.md`). Each harness binds
them as follows:

| Seat | Claude Code | Codex V2 | Codex V1 |
|---|---|---|---|
| Run the wave | the pave-init orchestrate skill | `$pave-init:orchestrate`, pave-init's Codex port | the lead, spawning the two seats below |
| Family worker | named `pave-init:orchestrate-worker` teammate | `pave-init:orchestrate-worker` | one-shot `ammo:ammo-researcher` with `task_type: mining_dossier` and the worker packet in `context` |
| Family reviewer | named `pave-init:adversarial-investigator` teammate | `pave-init:adversarial-investigator` | a fresh one-shot `pave-init:adversarial-investigator` for each round |

The orchestrate port needs the V2 agent tools, so V1 binds the seats to
one-shot agents. On V1 the lead gives the reviewer the dossier and the packet,
and sends any REVISE findings to a fresh researcher with the same packet.
On V1 the researcher leaves the status line off. After the reviewer's PASS,
or after the fourth round without one, the lead sends a fresh researcher the
same packet and the final verdict, and that researcher writes only the status
line (`references/mining-dossier.md` § 7. Status Line).

On every harness the outcomes are the same, and the limit is four rounds. A
dossier with no PASS after four rounds ends `UNGROUNDED: <reason>`.

## Claude Code Notes

- A one-shot role (researcher, auditor, delegate, investigator, resolver,
  report writer) spawns with `subagent_type` and `prompt` only. Its final
  message returns to the agent that spawned it. A `name` would make it a team
  member, and its answer would go to the lead.
- A team member (champion, impl-champion, transcript monitor) spawns with a
  `name` and `run_in_background=True`. It joins the lead's implicit team. Do
  not pass `team_name`. Address it by that name.
- A team member may spawn one-shot delegates with `run_in_background=True`,
  so it can end its turn and receive monitor and lead messages while they run
  (`references/champion-common-patterns.md`). A one-shot role never passes
  that flag. It issues all its delegate spawns in one message and waits for
  their reports.
- Track worktree order. The lead spawns the impl-champion as a named team
  member and does not pass `isolation` on the spawn call, because a call-level
  `isolation` makes the agent a plain subagent instead of a teammate. A named
  teammate ignores the `isolation: worktree` frontmatter and starts in the
  lead's working directory (code.claude.com/docs/en/sub-agents, § Subagent
  names). So the champion's first action is
  `EnterWorktree({"name": "{op_id}-<short-desc>"})`. That call fires
  `WorktreeCreate`, and `worktree-create-with-build.sh` creates the track
  branch and the built worktree under the engine root's `.claude/worktrees/`.
  When the champion already starts under `.claude/worktrees/` (a run where the
  frontmatter applied), it skips `EnterWorktree`: inside an isolated subagent
  only the `path` form of that tool exists (code.claude.com/docs/en/tools-reference,
  EnterWorktree).
- A member stops only when it returns a structured `shutdown_response`.
  `references/shutdown-protocol.md` owns that exchange.
- The champion's transcript is under
  `$CLAUDE_CONFIG_DIR/projects/<slug>/`, default `~/.claude/projects/<slug>/`.
  The slug is the engine root path with each character other than a letter or
  a digit replaced by `-`.

## Codex Notes

- `references/codex-runtime.md` selects the guide for the agent mode:
  `references/codex-runtime-v2.md` or `references/codex-runtime-v1.md`. The
  guide owns the V1 `AMMO_TASK` line and binding wait, the V1 registry,
  release, audit dispatch, transcript lookup, and capacity.
  `references/context-recovery.md` owns task names, recovery notes, and
  tracked commands.
- Every spawn message starts with the `plugin_root:` line. A V1 message puts
  its `AMMO_TASK=` line next.
- The lead creates each track worktree before it spawns the impl-champion, with
  the worktree script that `codex/RUNTIME.md` names. The worktree goes under
  the engine root's `.codex/worktrees/`. Pass its absolute path in the spawn
  message.
- Interrupt or close is never proof that work finished or that a gate passed.
