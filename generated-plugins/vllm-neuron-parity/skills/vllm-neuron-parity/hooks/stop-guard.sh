#!/usr/bin/env bash
# LEAD-ALIGNMENT PAIR (1 of 2) -- stop-alignment socratic check.
# Supports P12 (emit only declared outcomes; traverse only declared edges) by
# re-presenting the run position at the highest-risk context-decay moment. The
# questions (heredoc below) carry every lead duty no other moment re-asks; the
# lead answers only on a hit, else "lgtm" -- a retrospective at every firing is
# itself ceremony.
#
# Stop hooks have no non-blocking channel (additionalContext is dropped), so the
# questions can only be delivered by blocking ONCE (exit 2). A session-keyed
# cooldown marker then lets the next STOP_EVERY-1 stops pass silently and the
# stop after that nudges again -- the circuit breaker that makes an infinite
# stop loop impossible.
#
# Silent when stopping is correct: no marker-discovered run state, or the run's
# terminal_classification is set. Lead-only by two gates: the lead-session
# sidecar AND the payload's agent identity fields -- a lead-spawned subagent
# carries the LEAD's session id in its Stop payload, so the sidecar alone
# cannot exclude it (observed 2026-08-31; ledgered in the evolution root's
# revision record).
#
# Decline path (hook runtime unavailable): degrades to the lead's resume duty in
# SKILL.md "Run state and resume".
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
TAG="[stop-guard]"
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
if not isinstance(payload, dict):      # we could not read
    print("PARSE_FAIL"); sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print("1" if payload.get("stop_hook_active") else "0")
print(find(payload, "agent_type").replace("\n", " "))
print(find(payload, "agent_id").replace("\n", " "))
' 2>/dev/null || true)"
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0  # fail open
SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"
[ -n "$SESSION_ID" ] || SESSION_ID=default
[ "$(printf '%s\n' "$FIELDS" | sed -n 3p)" = "1" ] && exit 0    # never loop
# Identity gate half 1: subagents inherit the lead session id, so the sidecar
# below cannot exclude them -- exclude on the payload's own identity fields.
case "$(printf '%s\n' "$FIELDS" | sed -n 4p)" in ""|main|lead|root|primary) ;; *) exit 0 ;; esac
[ -n "$(printf '%s\n' "$FIELDS" | sed -n 5p)" ] && exit 0   # subagent: not the lead

# --- run-state discovery (marker-authoritative) -----------------------------
# The lead writes .vllm-neuron-parity-run at run start: one line holding the
# absolute path of the live run-state.json. A scan hit is NOT ownership
# evidence -- a newest-by-mtime match may belong to an abandoned run or another
# session, so this hook acts only on a marker hit.
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

STOP_EVERY="${VLLM_NEURON_PARITY_STOP_EVERY:-3}"
case "$STOP_EVERY" in *[!0-9]*|''|0|1) STOP_EVERY=3 ;; esac  # breaker needs >=2
COOLDOWN="${TMPDIR:-/tmp}/vllm-neuron-parity-stop-nudged-${SESSION_ID}"
if [ -f "$COOLDOWN" ]; then
  LEFT="$(head -n 1 "$COOLDOWN" 2>/dev/null)"
  case "$LEFT" in *[!0-9]*|'') LEFT=1 ;; esac
  if [ "$LEFT" -gt 1 ]; then
    printf '%s\n' "$((LEFT - 1))" > "$COOLDOWN" 2>/dev/null || true
  else
    rm -f "$COOLDOWN"
  fi
  exit 0
fi

SUMMARY="$("$PY" - "$FOUND_STATE" <<'PYEOF' 2>/dev/null
import json, os, sys, time
path = sys.argv[1]
try:
    state = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)          # unparsable state is validate_run_state.py business
if not isinstance(state, dict):
    sys.exit(0)
terminal = state.get("terminal_classification")
settled = bool(terminal.get("status") or terminal.get("classification")) \
    if isinstance(terminal, dict) else bool(terminal)
if settled:
    print("TERMINAL"); sys.exit(0)


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
if not names:
    names = ["<none recorded>"]
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
print("ACTIVE")
print(run_id)
print(", ".join(names[:4]))
print(last_txt)
print(int((time.time() - os.path.getmtime(path)) / 60))
PYEOF
)" || exit 0
[ "$(printf '%s\n' "$SUMMARY" | sed -n 1p)" = "ACTIVE" ] || exit 0
RUN_ID="$(printf '%s\n' "$SUMMARY" | sed -n 2p)"
ACTIVE_NODES="$(printf '%s\n' "$SUMMARY" | sed -n 3p)"
LAST="$(printf '%s\n' "$SUMMARY" | sed -n 4p)"
AGE_MIN="$(printf '%s\n' "$SUMMARY" | sed -n 5p)"

printf '%s\n' "$((STOP_EVERY - 1))" > "$COOLDOWN" 2>/dev/null || true
cat >&2 <<EOF
$TAG Active run $RUN_ID (state $FOUND_STATE): active_node_runs=$ACTIVE_NODES,
last recorded outcome $LAST, run state last written ${AGE_MIN} min ago.

You decided to stop. Socratic check -- answer only where you find an issue;
otherwise reply "lgtm" and stop again. A pending user decision at gate 1, 2,
or 3, a retained custom-agent thread, a recorded pause (run_paused): all lgtm.
The next $((STOP_EVERY - 1)) stops pass before this fires again.
  1. Next practical step toward the approved goal, and why -- a DECLARED
     outcome and edge from $ACTIVE_NODES ("Lead routing", workflow.pave.yaml)
     or a graph change you will propose; never an invented edge (P12).
  2. Since the last check, any ceremony -- a seat a lead-run check settles, a
     lap with no new world evidence, an agent for what disk already answers?
     Cut it. One that recurs is a graph defect: pause the run, record the
     evidence in run state plus one section of the standing review record
     (never a new file), and route it to the pave-evolve seats (the pave-init
     plugin's skills/pave-evolve/SKILL.md; agent types
     pave-init:workflow-updater and pave-init:update-reviewer) -- the updater
     drafts, the reviewer passes, you land it after the user approves
     (landing: user) and continue on it (Evolution contract rule 7). Never draft it yourself;
     never edit the live graph outside a landing.
  3. Landed work the next lap builds on that no review has seen?
  4. About to ask the user something a recorded approval already covers, or to
     decide something that is theirs?
  5. Anything routing depends on that lives only in your context, not in run
     state? You are the single writer (P10) -- write it now.
  6. Idle custom-agent threads or sub-agents? Retire the seat now.
EOF
exit 2
