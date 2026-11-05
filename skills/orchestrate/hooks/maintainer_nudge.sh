#!/usr/bin/env bash
# maintainer_nudge -- PostToolUse (Edit|Write|MultiEdit|NotebookEdit) hook for
# orchestrate workers (level: socratic reminder; always exit 0; Claude Code only).
#
# A worker that edits a file may drift from what the project's maintainer would
# merge. After an edit, this hook asks the worker whether the maintainer would
# merge what it is building, by the bar in its instructions, and tells it how
# to act on the answer around review. The worker decides; the hook never blocks.
# It keys on the agent type, because a plugin hook runs inside every teammate
# and each teammate has its own session id: only the worker seat is nudged, not
# the lead and not the reviewer (pave-init:adversarial-investigator).
#
# At most once per ORCHESTRATE_SEAT_SECONDS (default 60; a non-number falls
# back to 60) per agent. The last firing time is kept in
# ${TMPDIR:-/tmp}/orchestrate-seat/<session id>-<agent id, or "main">.
#
# Usage: maintainer_nudge.sh   (no arguments)
#
# Silent exit 0 when: no interpreter, unparsable payload, not a PostToolUse
# event, an agent type other than pave-init:orchestrate-worker (the lead has
# none; a bare orchestrate-worker is another workflow's agent, without this
# bar), a tool other than the four edit
# tools (the registered matcher already filters; the script does not rely on
# it), a session id or agent id that is not a plain name (it is spliced into a
# file name), inside the interval, or the firing time cannot be recorded.
# Writes nothing to stderr.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
TAG="[orchestrate maintainer_nudge]"

hook_read_payload
[ "${HOOK_EVENT:-}" = "PostToolUse" ] || exit 0
case "${AGENT_TYPE:-}" in pave-init:orchestrate-worker) ;; *) exit 0 ;; esac
case "${TOOL_NAME:-}" in Edit|Write|MultiEdit|NotebookEdit) ;; *) exit 0 ;; esac
case "${SESSION_ID:-}" in ''|*[!A-Za-z0-9._-]*) exit 0 ;; esac
case "${AGENT_ID:-}" in *[!A-Za-z0-9._-]*) exit 0 ;; esac

INTERVAL="${ORCHESTRATE_SEAT_SECONDS:-60}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=60 ;; esac
STAMP_DIR="${TMPDIR:-/tmp}/orchestrate-seat"
mkdir -p "$STAMP_DIR" 2>/dev/null || exit 0
STAMP="$STAMP_DIR/${SESSION_ID}-${AGENT_ID:-main}"
NOW="$(date +%s)"
LAST="$(head -n 1 "$STAMP" 2>/dev/null || true)"
case "$LAST" in ''|*[!0-9]*|????????????*) LAST=0 ;; esac   # not a number, or too long to compare
[ $((NOW - 10#$LAST)) -ge "$INTERVAL" ] 2>/dev/null || exit 0
{ printf '%s\n' "$NOW" > "$STAMP"; } 2>/dev/null || exit 0

TEXT="$TAG Would this project's maintainer merge what you are building, by the bar in your instructions? Before your first review request, fix a gap you see. After a REVISE or a PASS, change only what the findings or notes need, and name any other gap in your next brief or DONE report."

emit_additional_context PostToolUse "$TEXT"
exit 0
