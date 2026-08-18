#!/usr/bin/env bash
# stop_alignment_check -- Stop hook (rung: socratic reinjection).
#
# The pave-init lead is a long-horizon agent and the recorded failure cause
# in the field is context decay, not disobedience. A Stop while an active,
# non-terminal run exists is the highest-risk decay moment: the run silently
# stalls at its resume point and no user event fires to re-inject anything.
# This hook asks the socratic questions ("why did you stop, and is that the
# aligned action?") instead of commanding continuation -- valid stops are
# common (a pending user decision, waiting on a background reviewer, a
# recorded pause_for_user_authority).
#
# Stop hooks have NO non-blocking channel: additionalContext is dropped, so
# the questions can only be delivered by blocking once (exit 2). A cooldown
# counter keeps that advisory rare:
#   1st Stop with an active run: write a session-keyed countdown marker,
#       exit 2 with the questions.
#   next STOP_EVERY-1 Stops: decrement the marker, exit 0. Stop passes.
#   Marker spent -> the next Stop nudges again.
# Default STOP_EVERY=3 (at most one nudge per 3 stops); override with
# PAVE_INIT_STOP_EVERY (minimum 2 -- the breaker needs one free pass).
# stop_hook_active in the payload also short-circuits, so this cannot loop.
#
# Silent exit 0 when: no interpreter, unparsable payload, no run state found,
# or terminal_classification.status is set (the run is closed; stopping is
# correct). Lead-only by construction: Stop never fires inside a subagent
# (that event is SubagentStop, which this skill does not register).
#
# Decline path (hook runtime unavailable): degrades to the Resume section's
# reconciliation duty in SKILL.md.
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init stop_alignment_check]"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys
try:
    payload = json.loads(sys.stdin.read())
except Exception:
    print("PARSE_FAIL")
    sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print("1" if payload.get("stop_hook_active") else "0")
' 2>/dev/null || true)"

# Fail open on an unparsable or empty payload: a blocking hook must never
# act on input it cannot read.
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0

SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"
STOP_ACTIVE="$(printf '%s\n' "$FIELDS" | sed -n 3p)"
[ -n "$SESSION_ID" ] || SESSION_ID="default"

# Continuation already caused by a stop hook this cycle: never loop.
[ "$STOP_ACTIVE" = "1" ] && exit 0

. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] || exit 0
[ -f "$FOUND_STATE" ] || exit 0
# Marker-authoritative: a scan hit may be an abandoned run or another
# session's; blocking on it would misfire, so only the marker counts.
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0

# Cooldown counter: after a nudge, the next STOP_EVERY-1 stops pass.
STOP_EVERY="${PAVE_INIT_STOP_EVERY:-3}"
case "$STOP_EVERY" in *[!0-9]*|''|0|1) STOP_EVERY=3 ;; esac
MARKER="${TMPDIR:-/tmp}/pave-init-stop-nudged-${SESSION_ID}"
if [ -f "$MARKER" ]; then
  LEFT="$(head -n 1 "$MARKER" 2>/dev/null)"
  case "$LEFT" in *[!0-9]*|'') LEFT=1 ;; esac
  if [ "$LEFT" -gt 1 ]; then
    printf '%s\n' "$((LEFT - 1))" > "$MARKER" 2>/dev/null || true
  else
    rm -f "$MARKER"
  fi
  exit 0
fi

SUMMARY="$("$PY" - "$FOUND_STATE" <<'PYEOF' 2>/dev/null
import json, os, sys, time

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
except Exception:
    sys.exit(0)  # unparsable state is validate_run_state.py business

terminal = state.get("terminal_classification")
if isinstance(terminal, dict) and terminal.get("status"):
    print("TERMINAL")
    sys.exit(0)

identity = state.get("run_identity") or {}
history = [e for e in (state.get("traversal_history") or []) if isinstance(e, dict)]
last = history[-1] if history else {}
age_min = int((time.time() - os.path.getmtime(path)) / 60)
print("ACTIVE")
print(identity.get("run_id", "<unset>"))
print("%s.%s" % (last.get("node", "<none>"), last.get("outcome", "<none>")))
print(age_min)
PYEOF
)" || exit 0

STATUS="$(printf '%s\n' "$SUMMARY" | sed -n 1p)"
[ "$STATUS" = "ACTIVE" ] || exit 0

RUN_ID="$(printf '%s\n' "$SUMMARY" | sed -n 2p)"
LAST="$(printf '%s\n' "$SUMMARY" | sed -n 3p)"
AGE_MIN="$(printf '%s\n' "$SUMMARY" | sed -n 4p)"

printf '%s\n' "$((STOP_EVERY - 1))" > "$MARKER" 2>/dev/null || true

cat >&2 <<EOF
$TAG Active pave-init run $RUN_ID ($FOUND_STATE_LABEL): last traversal $LAST, run state last written ${AGE_MIN} min ago.

You decided to stop. Socratic check -- answer to yourself, then act:
  a. Why did you stop, and is stopping the most aligned action in the current
     state?
  b. If not, which DECLARED next action advances the run after $LAST?
     Re-read the edges in references/pave-init.pave.yaml; emit only declared
     outcomes and traverse only declared edges.
  c. Are any subagents or teammates done or no longer needed? Retire them
     now -- an idle agent costs tokens, and a late message from one can
     derail the run.

Valid reasons to stop exist: a pending user decision or approval gate, a
background reviewer still working, a recorded pause, a closed terminal. If
stop is the right call, stop again -- the next $((STOP_EVERY - 1)) stops
pass through before this check fires again.
EOF
exit 2
