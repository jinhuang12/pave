#!/usr/bin/env bash
# goal_restate -- SessionStart (resume|compact) hook for the orchestrate lead
# (level: socratic reminder; always exit 0).
#
# A resumed session or a compacted context is the moment the orchestrator is
# most likely to have lost the goal and the state of its subtasks, and to
# start inventing state or doing worker tasks itself. This hook asks the lead
# to rebuild both from the run log before acting: the goal from the log's
# header, the subtasks from the log script's `status` and the live roster from
# ListAgents, each open subtask and what it waits on, and the fewest steps to
# CLOSE. It names the subtasks the log shows open (open state is derived in
# file order, so a fix-round PLAN reopens its worker) but never injects the
# goal text -- the log wins over any hook. Never throttled: each rebuild is
# one firing.
#
# Silent exit 0 when: no interpreter, unparsable payload, an event other than
# SessionStart, a source other than resume|compact (startup and clear have no
# run to restate; clear also rotates the session id), a subagent payload, no
# run owned by this session (_hook_common.sh, find_owned_run), or the run is
# closed. Lead-only: SessionStart fires only in the main session.
#
# Decline path (hook runtime unavailable): the Resume section of SKILL.md.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
TAG="[orchestrate goal_restate]"

hook_read_payload
[ "$HOOK_EVENT" = "SessionStart" ] || exit 0
case "$HOOK_SOURCE" in resume|compact) ;; *) exit 0 ;; esac
find_owned_run || exit 0

LOG_SCRIPT="$(cd "$HOOK_DIR/.." && pwd)/scripts/orchestrate_log.py"
OPEN="$(log_open_subtasks "$RUN_LOG" | paste -sd ' ' - | sed 's/ /, /g')"

TEXT="$TAG Your context was just rebuilt ($HOOK_SOURCE). This session owns an open orchestrate run; its log is $RUN_LOG and the file wins over memory. Before acting, in a few lines:
(1) the goal, as written on the log's goal: line;
(2) run \`$ORCH_PY \"$LOG_SCRIPT\" status\` and ListAgents;
(3) each open subtask, and what it waits on (Open in the log now: ${OPEN:-none});
(4) the fewest steps from here to CLOSE.
You orchestrate, you do not implement."

emit_additional_context SessionStart "$TEXT"
exit 0
