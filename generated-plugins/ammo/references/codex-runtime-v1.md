# Codex V1 execution guide

This guide applies when Codex uses Amazon Bedrock and the native
`multi_agent_v1` tools. Keep the AMMO stages, role ownership, evidence gates,
and recovery rules unchanged.

## Dispatch and identity

Read `references/context-recovery.md` before dispatch. It owns task names. A
logical task path, such as `/root/implementer_r1_op_001`,
names AMMO records; the native runtime handle is the UUID returned by spawn.

Call the native tool directly. Spawn each role at its plugin address,
`ammo:<role>`. Each AMMO custom-role spawn uses a fresh context. Start its
message with the `plugin_root:` line, then exactly one `AMMO_TASK=` JSON line.
The `pave-init:adversarial-investigator` spawn carries the same two lines, or
the V1 hook refuses it. Use absolute artifact and recovery paths; keep the raw `op_id` in the
assignment and state.

```text
multi_agent_v1.spawn_agent(
  agent_type="ammo:ammo-implementer",
  fork_context=false,
  message='plugin_root: /absolute/plugin\n'
          'AMMO_TASK={"task_name":"implementer_r1_op_001","artifact_dir":"/absolute/campaign","recovery_note_path":"/absolute/campaign/recovery/ROOT_UUID/root/implementer_r1_op_001/context.md"}\n'
          'artifact_dir: /absolute/campaign\nop_id: OP-001\n<bounded assignment, worktree, and evidence paths>'
)
```

V1 has no `task_name` or `fork_turns` argument. The parent spawn-result hook
binds the logical task to the returned UUID and its verified native parent.
A child may start before that hook returns: read the role and wait for binding;
do not write campaign files, reserve accelerators, or dispatch more work yet.
The hook rejects those operations until registration succeeds.

The same rule applies to every AMMO role, including nested delegates. Derive a
nested recovery path from its parent's logical path. Read the registry after a
reset; never reconstruct native handles from a nickname or file age:

```sh
<plugin-root>/scripts/ammo codex/scripts/codex_agent_registry.py list --root-thread-id ROOT_UUID
```

The registry records exact native IDs, logical paths, parent IDs, role, and note
paths. A task name cannot be assigned to a second native agent. Use a new suffix
for a fresh independent task. Keep native UUIDs in agent-handle fields and
`implementer_rollout_id`; keep logical paths in recovery notes.

## Messaging, waiting, and release

| Intent | Native V1 operation |
|---|---|
| Start or steer existing work | `multi_agent_v1.send_input(target=UUID, message=TEXT, interrupt=false)` |
| Wait for named agents | `multi_agent_v1.wait_agent(targets=[UUID], timeout_ms=10000)` |
| Release an agent after evidence and process checks | `multi_agent_v1.close_agent(target=UUID)` |
| Reopen a closed agent before a follow-up | `multi_agent_v1.resume_agent(id=UUID)`, then `send_input` |

`send_input` starts an idle agent or queues work for a running agent. It is not
a passive mailbox. Put passive monitor findings in AMMO's intervention queue;
the existing hooks deliver and require acknowledgement of blocking records.
Return completed work normally. Use `send_input` only for intentional work or
steering. There is no V2 `send_message`, `followup_task`, or `list_agents` tool.

V1 completed agents retain a slot until closed. Follow
`references/shutdown-protocol.md`, then close each released agent. Closing a
parent also closes its descendants: reconcile their evidence and owned
processes first. Close is never proof that a benchmark finished or a gate passed.
After reopening, read the exact assigned recovery note before new work.

## Audit dispatch

Use `task_name="audit_{stage}_r{round}_c{cycle}"` inside the `AMMO_TASK` metadata
line, with a fresh suffix when needed. Use `agent_type="ammo:ammo-auditor"` and
`fork_context=false`. The rest of the message holds only these assignment lines
from `orchestration/audit-protocol.md`:

```text
plugin_root: /absolute/plugin
task: audit_gate
artifact_dir: /absolute/campaign
stage: stage_1
round: 1
cycle: 1
recovery_note_path: /absolute/campaign/recovery/ROOT_UUID/root/audit_stage_1_r1_c1/context.md
```

Do not preload Phase 2. Audit started-at stamping and Phase 2 activation remain
hook-owned. Resolve monitor transcripts by exact UUID with
`<plugin-root>/scripts/ammo codex/scripts/resolve_codex_transcript.py --campaign-session-id ROOT_UUID --agent-id UUID`.

## Context and capacity

Bedrock uses ordinary compaction. The same file recovery contract applies after
compaction and process restart. Read this guide again with the role and
assigned note. Do not change the provider or agent mode in an active campaign.
The V1 settings in `codex/RUNTIME.md` allow 16 open descendants across the
tree, to depth 3. Close finished bounded tasks before those limits prevent
required audit delegates.
