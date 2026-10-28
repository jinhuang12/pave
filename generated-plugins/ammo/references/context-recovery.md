# Codex Context Recovery

## Native agent dispatch

Read `codex/RUNTIME.md` in the plugin for the native Codex operations and handles. Every AMMO custom role starts with independent context. Every logical task name is a unique lowercase path segment containing only `a-z`, `0-9`, and `_`. Put the campaign round and a bounded attempt/task suffix in any name that can recur. When a business identifier such as `OP-001` names a track, derive `task_slug = re.sub(r"[^a-z0-9_]+", "_", op_id.lower()).strip("_")` for the task name, and keep the raw `op_id` in state, prompts, monitor ledgers, and artifact paths. Logical paths start at `/root`; each child appends its task name to its parent's logical path. V2 routes by that path; V1 routes by the UUID bound to it in the registry. `ammo-champion`, `ammo-implementer`, and `ammo-transcript-monitor` may be reused while their round remains active. `ammo-researcher`, `ammo-auditor`, `ammo-investigator`, `ammo-delegate`, `ammo-report`, and `ammo-resolver` are normally fresh bounded tasks. `state.json.team_name` is descriptive bookkeeping, never a Codex runtime handle.

Read this at child dispatch and after context reset. Codex restores the role bootstrap, but the detailed assignment must be recoverable.
The same file contract applies with experimental context management or ordinary compaction. Native history tools are optional.

## Assigned note

The parent supplies `recovery_note_path` as this exact absolute path:

`{artifact_dir}/recovery/{root_thread_id}/{task_path_without_leading_slash}/context.md`

Use `state.json.codex_thread_id` for the root identity and the logical AMMO task path for the task identity. Record its exact native handle separately; V1 UUIDs never replace the logical path in a note location.
Each child may write only its own assigned note, in addition to its normal role outputs.
This exception does not grant source, state, evidence, or verdict ownership.
Fresh audit tasks use fresh task names and separate notes; they do not inherit an earlier auditor's note.
Audit task names use `audit_{stage}_r{round}_c{cycle}`, with an optional unique suffix.
This route lets the hook record audit start from V2 task names or V1 dispatch metadata.

For a new task, read the role and initialize the note with:

- The exact assignment and later amendments, role, parent/task paths, campaign/root identity, round, and raw `op_id` when assigned.
- The assigned worktree and output paths, evidence pointers, verified progress, open questions, and next action.
- Exact agent handles needed for follow-up; monitor target, offset-file, and queue pointers when applicable.
- For auditors, the assigned verdict path and any hook-delivered Phase 2 activation record. Do not copy a hidden checklist into the note.

For a follow-up, update the existing note when those facts change. Immediately save a yielded command's process handle, exact command, worktree, and opaque GPU owner.
Do this before another action or a voluntary reset. Preserve amendments until the parent explicitly supersedes them.

## Resume the task

1. Reread the entry skill for shared constraints and your own role. Lead-only instructions do not change your role.
2. Read the exact assigned note. If the path or assignment is missing or mismatched, request its exact replacement from the parent.
3. Reconcile with live state, actual process status, evidence files, monitor offsets, and the auditor's current hook activation record.
4. Continue only the remaining assigned work. Check an existing process before launching another run.

Do not select a note or campaign by file age. A note never establishes a gate pass, changes the frozen target, or authorizes Phase 2.
An auditor may resume Phase 2 only after confirming its own Phase 1 verdict and the matching hook-issued activation record.
If that record is unavailable, ask the parent to restore the exact hook context; do not infer permission from the note.
Monitor offset files remain the authority for the last consumed transcript position.

## Tracked commands

Use `exec_command` with `tty=false` for long commands. Save the returned handle and poll it with empty `write_stdin` input.
Use bounded waits so parent and monitor messages can arrive. Interrupt only an owned process, then verify that it exited.
Do not start a second own GPU run while the first remains active. Other agents may use separately reserved GPUs.
Keep the GPU lease valid for the full run; `gpu-pool.md` owns lease duration and release rules.

Return a final response only after each owned process completes or is interrupted and confirmed exited.
`SubagentStop` releases the child owner; it does not check process liveness. The non-TTY rule is an agent instruction, not a hook restriction.
