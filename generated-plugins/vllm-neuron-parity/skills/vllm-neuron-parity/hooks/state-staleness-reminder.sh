#!/usr/bin/env bash
# LEAD-ALIGNMENT PAIR (2 of 2) -- state-staleness socratic reminder.
# Supports P12 (declared outcomes and edges only) and P10 (the lead is the
# single writer of run state) across long autonomous stretches where no user
# event fires and nothing re-injects the run position.
#
# OBSERVING: always exits 0 and speaks only through additionalContext. Fires
# when the run-state file has not been written for STALE_SECONDS while tools
# keep running; throttled to once per window per session; silent for subagent
# payloads (only the lead can act on it) and for terminal runs.
#
# Decline path (hook runtime unavailable): degrades to the checkpoint duty in
# SKILL.md "Run state and resume" (checkpoint after every consequential
# transition).
set -uo pipefail
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
TAG="[state-staleness-reminder]"
STALE_SECONDS="${VLLM_NEURON_PARITY_STALE_SECONDS:-900}"
case "$STALE_SECONDS" in *[!0-9]*|'') STALE_SECONDS=900 ;; esac
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys
def find(node, key):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], (str, int)):
            return str(node[key])
        for v in node.values():
            f = find(v, key)
            if f:
                return f
    elif isinstance(node, list):
        for item in node:
            f = find(item, key)
            if f:
                return f
    return ""
try:
    payload = json.loads(sys.stdin.read())
except Exception:
    print("PARSE_FAIL"); sys.exit(0)   # distinct sentinel: never act on input
if not isinstance(payload, dict):
    print("PARSE_FAIL"); sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print(find(payload, "agent_type").replace("\n", " "))
print(find(payload, "agent_id").replace("\n", " "))
' 2>/dev/null || true)"
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0  # fail open
SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"
[ -n "$SESSION_ID" ] || SESSION_ID=default
# Identity gate: only the lead holds the state-write duty this reminder names.
case "$(printf '%s\n' "$FIELDS" | sed -n 3p)" in ""|main|lead|root|primary) ;; *) exit 0 ;; esac
[ -n "$(printf '%s\n' "$FIELDS" | sed -n 4p)" ] && exit 0   # subagent: cannot act

# --- run-state discovery (marker-authoritative; see stop-guard.sh) ----------
FOUND_STATE=""
FOUND_VIA=""
for root in "${CODEX_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
  [ -n "$root" ] || continue
  marker="$root/.vllm-neuron-parity-run"
  [ -f "$marker" ] || continue
  candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  if [ -n "$candidate" ] && [ -f "$candidate" ]; then
    FOUND_STATE="$candidate"; FOUND_VIA="marker"; break
  fi
done
[ "$FOUND_VIA" = "marker" ] || exit 0

# --- lead-session identity gate ---------------------------------------------
# This pair is lead-only, but every full session in the project (teammates,
# scratch sessions) fires the same events. The lead records its session id in
# the sidecar <run-state>.lead-session (one line); when the sidecar exists and
# names a different session, stay silent. No sidecar = fail open (pre-gate
# behavior) so a run without one keeps its coverage.
LEAD_FILE="${FOUND_STATE}.lead-session"
if [ -f "$LEAD_FILE" ]; then
  LEAD_ID="$(head -n 1 "$LEAD_FILE" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$LEAD_ID" ] && [ "$SESSION_ID" != "$LEAD_ID" ]; then
    exit 0
  fi
fi

NOW="$(date +%s)"
STATE_MTIME="$(stat -f %m "$FOUND_STATE" 2>/dev/null || stat -c %Y "$FOUND_STATE" 2>/dev/null || echo "$NOW")"
[ $(( NOW - STATE_MTIME )) -ge "$STALE_SECONDS" ] || exit 0
THROTTLE="${TMPDIR:-/tmp}/vllm-neuron-parity-stale-nudged-${SESSION_ID}"
if [ -f "$THROTTLE" ]; then
  LAST_NUDGE="$(stat -f %m "$THROTTLE" 2>/dev/null || stat -c %Y "$THROTTLE" 2>/dev/null || echo 0)"
  [ $(( NOW - LAST_NUDGE )) -ge "$STALE_SECONDS" ] || exit 0
fi
touch "$THROTTLE" 2>/dev/null || true

"$PY" - "$FOUND_STATE" "$TAG" "$(( NOW - STATE_MTIME ))" "$STALE_SECONDS" <<'PYEOF' 2>/dev/null || exit 0
import json, sys
path, tag, age, window = sys.argv[1:5]
try:
    state = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)
if not isinstance(state, dict):
    sys.exit(0)
terminal = state.get("terminal_classification")
settled = bool(terminal.get("status") or terminal.get("classification")) \
    if isinstance(terminal, dict) else bool(terminal)
if settled:
    sys.exit(0)


def label(entry, *keys):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for k in keys:
            if entry.get(k):
                return str(entry[k])
    return ""


names = []
for e in (state.get("active_node_runs") or []):
    node = label(e, "node", "node_id", "name")
    inst = label(e, "instance", "campaign", "target")
    if node or inst:
        names.append("%s[%s]" % (node, inst) if inst else node)
done = list(state.get("completed_outcomes") or [])
last = done[-1] if done else None
last_txt = "<none>"
if last is not None:
    node = label(last, "node", "node_id", "name")
    outcome = label(last, "outcome", "result")
    last_txt = ("%s.%s" % (node or "<node?>", outcome or "<outcome?>")
                if (node or outcome) else str(last)[:80])
run_id = label(state.get("workflow_identity") or {},
               "run_id", "run_identity", "id") or "<unset>"
text = (
    "%s Run state last written %d min ago (run %s; state %s). It records "
    "active_node_runs=%s and last outcome %s. Socratic check: which node "
    "instance are you ACTUALLY in right now, and has an outcome occurred since "
    "that entry that is not recorded? If yes, checkpoint the traversal now per "
    "the state-write protocol -- declared outcome, declared edge, evidence "
    "indexed at its declared artifact path -- and remember you are the single "
    "writer (P10). If you are mid-node with nothing to record, continue; this "
    "fires at most once per %d min."
    % (tag, int(age) // 60, run_id, path, ", ".join(names[:4]) or "<none recorded>",
       last_txt, int(window) // 60)
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                         "additionalContext": text}}))
PYEOF
exit 0
