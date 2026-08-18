#!/usr/bin/env bash
# state_staleness_reminder -- PostToolUse (Bash|Write|Edit), observing
# (rung: socratic reinjection; always exit 0).
#
# A long autonomous stretch (many tool calls, no user prompt, no compaction)
# gets zero reinjection of the run-state duty. This hook fills that window:
# when run-state.json has not been written for STALE_SECONDS while tools keep
# running, it asks whether an outcome has occurred that was never recorded,
# via non-blocking additionalContext. It never blocks.
#
# Trigger: mtime of the discovered run-state.json older than STALE_SECONDS
# (default 900; override PAVE_INIT_STALE_SECONDS). Throttled to once per
# window per session so it nudges, not nags.
#
# Silent exit 0 when: interpreter missing, payload shows a subagent
# (agent_type / agent_id -- the lead is the sole state writer, so only the
# lead can act on this), no run state found, state fresh, throttled, state
# unparsable, or terminal_classification.status set.
#
# Decline path (hook runtime unavailable): degrades to the checkpoint duty
# in SKILL.md (Run workspace: append a traversal entry at every checkpoint
# moment the graph's state contract names).
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init state_staleness_reminder]"
STALE_SECONDS="${PAVE_INIT_STALE_SECONDS:-900}"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys

def find(node, key):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], (str, int)):
            return str(node[key])
        for value in node.values():
            found = find(value, key)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find(item, key)
            if found:
                return found
    return ""

try:
    payload = json.loads(sys.stdin.read())
except Exception:
    print("PARSE_FAIL")
    sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print(find(payload, "agent_type").replace("\n", " "))
print(find(payload, "agent_id").replace("\n", " "))
' 2>/dev/null || true)"

# Fail open on an unparsable or empty payload: without identity fields the
# subagent gate cannot be evaluated, so say nothing.
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0

SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"
AGENT_TYPE="$(printf '%s\n' "$FIELDS" | sed -n 3p)"
AGENT_ID="$(printf '%s\n' "$FIELDS" | sed -n 4p)"
[ -n "$SESSION_ID" ] || SESSION_ID="default"

# Lead-only: a subagent cannot write run state, so nudging it is noise.
case "${AGENT_TYPE:-}" in
  ""|main|lead|root|primary) ;;
  *) exit 0 ;;
esac
[ -n "${AGENT_ID:-}" ] && exit 0

. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] || exit 0
[ -f "$FOUND_STATE" ] || exit 0
# Marker-authoritative: a scan hit may be an abandoned run or another
# session's; nudging about a run this session does not own is noise.
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0

NOW="$(date +%s)"
STATE_MTIME="$(stat -c %Y "$FOUND_STATE" 2>/dev/null || stat -f %m "$FOUND_STATE" 2>/dev/null || echo "$NOW")"
case "$STATE_MTIME" in ''|*[!0-9]*) STATE_MTIME="$NOW";; esac  # a failed stat variant can still leak stdout into the capture
AGE=$(( NOW - STATE_MTIME ))
[ "$AGE" -ge "$STALE_SECONDS" ] || exit 0

# Throttle: at most one nudge per staleness window per session.
THROTTLE="${TMPDIR:-/tmp}/pave-init-stale-nudged-${SESSION_ID}"
if [ -f "$THROTTLE" ]; then
  LAST="$(stat -c %Y "$THROTTLE" 2>/dev/null || stat -f %m "$THROTTLE" 2>/dev/null || echo 0)"
  case "$LAST" in ''|*[!0-9]*) LAST=0;; esac
  [ $(( NOW - LAST )) -ge "$STALE_SECONDS" ] || exit 0
fi
touch "$THROTTLE" 2>/dev/null || true

"$PY" - "$FOUND_STATE" "$FOUND_STATE_LABEL" "$TAG" "$AGE" "$STALE_SECONDS" <<'PYEOF' 2>/dev/null || exit 0
import json, sys

path, label, tag, age, window = sys.argv[1:6]
try:
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
except Exception:
    sys.exit(0)  # unparsable state is validate_run_state.py business

terminal = state.get("terminal_classification")
if isinstance(terminal, dict) and terminal.get("status"):
    sys.exit(0)  # closed run: staleness is expected

identity = state.get("run_identity") or {}
history = [e for e in (state.get("traversal_history") or []) if isinstance(e, dict)]
last = history[-1] if history else {}
age_min = int(age) // 60
window_min = int(window) // 60

text = (
    "%s run-state.json last written %d min ago (run %s, %s; state %s). "
    "Last recorded traversal: %s.%s. "
    "Socratic check: which node are you ACTUALLY in right now, and has any "
    "outcome occurred since that entry that is not recorded? If yes, append "
    "the traversal entry now, update the affected state fields, validate with "
    "scripts/validate_run_state.py, and persist any due artifacts at their "
    "declared paths (reviews/*.md). If you are "
    "mid-node with nothing to record, continue; this reminder fires at most "
    "once per %d min."
    % (
        tag, age_min, identity.get("run_id", "<unset>"), label, path,
        last.get("node", "<none>"), last.get("outcome", "<none>"),
        window_min,
    )
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    }
}))
PYEOF

exit 0
