# Codex V2 execution guide

This guide applies when Codex uses a ChatGPT subscription and the native
`agents` tools. Keep the AMMO stages, role ownership, evidence gates, and
recovery rules unchanged.

## Dispatch and identity

Read `references/context-recovery.md` before dispatch. It owns task names.
Every AMMO custom-role spawn uses `fork_turns="none"` for independent context and
a `task_name` from that file. Call native tools directly, outside execution cells. Spawn
each role at its plugin address, `ammo:<role>`. Start each message with the
`plugin_root:` line and give the campaign dir on an `artifact_dir:` line.

```text
agents.spawn_agent(
  task_name="implementer_r1_op_001",
  agent_type="ammo:ammo-implementer",
  fork_turns="none",
  message="plugin_root: /absolute/plugin\nartifact_dir: /absolute/campaign\nop_id: OP-001\nrecovery_note_path: /absolute/campaign/recovery/ROOT_UUID/root/implementer_r1_op_001/context.md\n<bounded assignment>"
)
```

Keep the raw `op_id` in state, messages, and artifact paths. Use canonical
absolute task paths for parent-to-child and sibling messages, such as
`/root/implementer_r1_op_001`. Child-local descendants can use relative names.
Use `agents.list_agents` to reconcile native tasks after a reset. Never create
a second agent for an existing task path.

## Messaging, waiting, and release

- Start another bounded task on a reused agent with `agents.followup_task`.
- Deliver information without starting a turn with `agents.send_message`.
- Wait for updates with bounded `agents.wait_agent` calls.
- After the evidence and process checks in `references/shutdown-protocol.md`,
  use `agents.interrupt_agent` to stop an agent that is still running.
- A completed or idle V2 agent needs no close operation. It remains available
  for a later follow-up. Remove it from the active-round mapping after evidence
  reconciliation. Interruption alone is never proof of completed work.

## Audit dispatch

Use a fresh `ammo:ammo-auditor` with
`task_name="audit_{stage}_r{round}_c{cycle}"`, `fork_turns="none"`, and only
the assignment lines in `orchestration/audit-protocol.md` plus
`recovery_note_path`. An optional unique suffix may follow the cycle. The
public task name carries the stage, round, and cycle when Codex encrypts the
message. Do not preload Phase 2.

Resolve monitor transcripts with the exact canonical task path:
`<plugin-root>/scripts/ammo codex/scripts/resolve_codex_transcript.py --campaign-session-id ROOT_UUID --agent-path /root/TASK`.
Keep that path as the native routing handle and record the returned rollout UUID
in `implementer_rollout_id`.

## Context and capacity

Subscription mode keeps the experimental context-management settings that
`codex/RUNTIME.md` lists. The file recovery contract also works with ordinary
compaction. Read this guide again with the role and assigned note after a
reset. Do not change the provider or agent mode in an active campaign. The V2
settings allow 17 concurrent threads, the root included.
