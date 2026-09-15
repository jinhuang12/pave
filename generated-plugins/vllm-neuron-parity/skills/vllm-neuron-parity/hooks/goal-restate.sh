#!/usr/bin/env bash
# goal-restate -- SessionStart (resume|compact) and SubagentStart, observing
# (level: socratic reminder; always exit 0). Adapted from the pave-init
# template (lead-hooks.md, hooks/goal_restate.sh).
#
# The moments an agent's context is rebuilt -- a session resumed, a context
# compacted, a seat started from a brief -- are the moments the goal is most
# likely lost, and the moments the agent is freest of the process it was
# running. This hook asks the agent to state the goal and its reason from the
# record before acting and to name the fewest steps toward it. It asks; it
# never injects the goal text -- the record wins over any hook.
#
# Lead (SessionStart resume|compact): the goal as invoked is run-state.json
# (requested_targets, approved_campaigns); each active campaign's approved
# design and user decisions are campaigns/<campaign>/approvals/DECISIONS.md
# (references/artifact-layout.md section 1). Seat (SubagentStart): the goal is
# the brief; the question names the same records so the seat can say whether
# its brief serves them.
#
# Registered in hooks/hooks.json: SubagentStart must reach every seat, and one
# registration serves both events. Lead-session gate as in stop-guard.sh: a
# sidecar naming another session means this session does not own the run. A
# lead-spawned seat carries the lead's session id, so it passes; another
# session's seats do not. No sidecar = fail open.
#
# Silent exit 0 when: interpreter missing, payload unparsable, no
# marker-discovered run state, the sidecar names another session, the run is
# terminal, the event is neither of the two, or a SessionStart source other
# than resume|compact (startup and clear: no goal exists yet).
#
# Decline path (hook runtime unavailable): the lead's resume duty in SKILL.md
# "Run state and resume"; the brief in each role contract for seats.
set -uo pipefail
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
TAG="[goal-restate]"
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0

# --- run-state discovery (marker-authoritative; see stop-guard.sh) ----------
FOUND_STATE=""
FOUND_VIA=""
# DECISIONS §1052/§1053 (2026-09-14): try each seed root and then every parent
# up to / -- a seat whose cwd is a campaign subdirectory must still find the
# marker at the project root.
for seed in "${CODEX_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
  [ -n "$seed" ] || continue
  root="$seed"
  while :; do
    marker="$root/.vllm-neuron-parity-run"
    if [ -f "$marker" ]; then
      candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        FOUND_STATE="$candidate"; FOUND_VIA="marker"; break 2
      fi
    fi
    parent="$(dirname "$root")"
    [ "$parent" != "$root" ] || break
    root="$parent"
  done
done
[ "$FOUND_VIA" = "marker" ] || exit 0

# The payload travels via a temp file: `python - <<heredoc` owns stdin for the
# script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/vllm-neuron-parity-restate.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0

"$PY" - "$FOUND_STATE" "$TAG" "$PAYLOAD_FILE" <<'PYEOF' 2>/dev/null || exit 0
import json
import os
import sys

state_path, tag, payload_file = sys.argv[1:4]

try:
    with open(payload_file, encoding="utf-8") as handle:
        payload = json.load(handle)
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
except Exception:
    sys.exit(0)  # fail open: no payload or state, nothing to ask
if not isinstance(payload, dict) or not isinstance(state, dict):
    sys.exit(0)

# Lead-session gate (see stop-guard.sh): when the sidecar exists and names a
# different session, this session does not own the run -- stay silent.
lead_file = state_path + ".lead-session"
if os.path.isfile(lead_file):
    try:
        with open(lead_file, encoding="utf-8") as handle:
            lead_id = handle.readline().strip()
    except Exception:
        lead_id = ""
    session = str(payload.get("session_id") or "default")
    if lead_id and session != lead_id:
        sys.exit(0)

# A run that recorded a final status is over, marker or not.
terminal = state.get("final_status")
decided = bool(terminal.get("status") or terminal.get("classification")) \
    if isinstance(terminal, dict) else bool(terminal)
if decided:
    sys.exit(0)

# Goal records: run state (requested_targets, approved_campaigns) and each
# active campaign's approvals/DECISIONS.md where one exists.
artifacts = os.path.dirname(os.path.dirname(state_path))
records = [state_path]
for entry in state.get("active_node_runs") or []:
    if not isinstance(entry, dict):
        continue
    inst = entry.get("campaign")
    if not inst:
        continue
    decisions = os.path.join(artifacts, "campaigns", str(inst), "approvals", "DECISIONS.md")
    if os.path.isfile(decisions) and decisions not in records:
        records.append(decisions)
record = " and ".join(records)

event = str(payload.get("hook_event_name") or "")
if event == "SessionStart":
    source = str(payload.get("source") or "")
    if source not in ("resume", "compact"):
        sys.exit(0)  # startup or clear: no goal exists yet
    text = (
        f"{tag} Your context was just rebuilt ({source}). Before acting, in a "
        f"few lines: (1) the goal and its reason as recorded in {record} -- the "
        "files win over memory; (2) the declared next step and the fewest after "
        "it; for each, what breaks if you skip it -- cut any with no answer (a "
        "seat for a fact knowable from disk, a re-gate of a recorded approval, a "
        "round with no new evidence). A needed cut the graph forbids is a graph "
        "defect: record it and route it to the pave-evolve seats (stop check, "
        "question 2)."
    )
elif event == "SubagentStart":
    text = (
        f"{tag} Before acting, in a few lines: the goal of your brief, why it "
        f"serves the run's goal (recorded in {record}), and the fewest steps "
        "that produce the evidence the brief asks for. Add none the brief does "
        "not need; if the brief gives no reason, say so in your report."
    )
else:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": text,
    }
}))
PYEOF

exit 0
