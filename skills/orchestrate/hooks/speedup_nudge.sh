#!/usr/bin/env bash
# speedup_nudge -- PostToolUse (Bash) hook for the orchestrate lead
# (level: socratic reminder; always exit 0).
#
# A run is slow when work that could overlap waits in line. When the lead
# writes the run log (orchestrate_log.py init or append) and the new last line
# is PLAN, DONE, BLOCKED, ESCALATE, RESPAWN, or INTEGRATE, the plan just
# changed, so this hook asks whether anything else can overlap, run in
# parallel, or be cut. At most once per ORCHESTRATE_SPEEDUP_SECONDS (default
# 600) per run; the last firing time is kept beside the log in
# <run-id>.speedup.
#
# Silent exit 0 when: no interpreter, unparsable payload, not a shell command
# that wrote the log, a subagent payload, no open run owned by this session
# (_hook_common.sh, find_owned_run), another last event, inside the interval,
# or the firing time cannot be recorded.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
TAG="[orchestrate speedup_nudge]"

hook_read_payload
lead_wrote_log || exit 0
find_owned_run || exit 0
case "$(log_last_event "$RUN_LOG")" in PLAN|DONE|BLOCKED|ESCALATE|RESPAWN|INTEGRATE) ;; *) exit 0 ;; esac

INTERVAL="${ORCHESTRATE_SPEEDUP_SECONDS:-600}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=600 ;; esac
STAMP="${RUN_LOG%.md}.speedup"
NOW="$(date +%s)"
LAST="$(head -n 1 "$STAMP" 2>/dev/null || true)"
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
[ $((NOW - LAST)) -ge "$INTERVAL" ] || exit 0
printf '%s\n' "$NOW" > "$STAMP" 2>/dev/null || exit 0

OPEN="$(log_open_subtasks "$RUN_LOG" | paste -sd ' ' - | sed 's/ /, /g')"
TEXT="$TAG Anything else we can overlap, parallelize, or cut to speed this whole process up, or is it already optimal? (Open now: ${OPEN:-none}.)
Answer in one line before you go on: \"optimal\", or the change you will make now."

emit_additional_context PostToolUse "$TEXT"
exit 0
