#!/usr/bin/env bash
# speedup_nudge -- PostToolUse (Bash) hook for the orchestrate lead
# (level: socratic reminder; always exit 0).
#
# A run is slow when work that could overlap waits in line. When the lead
# writes the run log (orchestrate_log.py init or append) and the new last line
# is PLAN, DONE, BLOCKED, ESCALATE, RESPAWN, or INTEGRATE, the plan just
# changed, so this hook asks whether anything else can overlap, run in
# parallel, or be cut. It also asks the lead to give work it lacks the
# context for to a helper or a worker instead of doing it itself, which users
# otherwise ask for by hand. In Claude Code it also asks the lead to stop any
# agent it no longer needs: a finished teammate keeps its agent slot until the
# lead stops it. The lead decides which ones, because a DONE worker may still be
# needed. At most once per ORCHESTRATE_SPEEDUP_SECONDS (default 60) per run;
# the last firing time is kept beside the log in <run-id>.speedup.
#
# Usage: speedup_nudge.sh [claude|codex]   (default claude)
#
# Silent exit 0 when: no interpreter, unparsable payload, not a shell command
# that wrote the log, a subagent payload, no open run owned by this session
# (_hook_common.sh, find_owned_run), another last event, inside the interval,
# or the firing time cannot be recorded.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
TAG="[orchestrate speedup_nudge]"
RUNTIME="${1:-claude}"

hook_read_payload
lead_wrote_log || exit 0
find_owned_run || exit 0
case "$(log_last_event "$RUN_LOG")" in PLAN|DONE|BLOCKED|ESCALATE|RESPAWN|INTEGRATE) ;; *) exit 0 ;; esac

INTERVAL="${ORCHESTRATE_SPEEDUP_SECONDS:-60}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=60 ;; esac
STAMP="${RUN_LOG%.md}.speedup"
NOW="$(date +%s)"
LAST="$(head -n 1 "$STAMP" 2>/dev/null || true)"
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
[ $((NOW - LAST)) -ge "$INTERVAL" ] || exit 0
{ printf '%s\n' "$NOW" > "$STAMP"; } 2>/dev/null || exit 0

OPEN="$(log_open_subtasks "$RUN_LOG" | paste -sd ' ' - | sed 's/ /, /g')"
RELEASE=""
[ "$RUNTIME" = "codex" ] || RELEASE="
Also stop any agent you no longer need with \`TaskStop\`, then log \`append RELEASE <name>\`."
TEXT="$TAG Anything else we can overlap, parallelize, or cut to speed this whole process up, or is it already optimal? (Open now: ${OPEN:-none}.)
Answer in one line before you go on: \"optimal\", or the change you will make now.
If your next step needs facts you do not have, send a helper to find them, and give any change to files to a worker. Your context holds the whole run, and long reads crowd it out. Look facts up yourself only when one or two reads confirm them.$RELEASE"

emit_additional_context PostToolUse "$TEXT"
exit 0
