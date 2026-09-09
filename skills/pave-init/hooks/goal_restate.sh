#!/usr/bin/env bash
# goal_restate -- SessionStart (resume|compact) and SubagentStart, observing
# (rung: socratic reinjection; always exit 0).
#
# The moments an agent's context is rebuilt -- a session resumed, a context
# compacted, a seat started from a brief -- are the moments the goal is most
# likely lost: a compaction summary replaces it, a resume reconstructs it, a
# seat has only its brief. This hook asks the agent to state the goal and its
# reason from the record before acting, and to name the fewest steps toward
# it. An agent that has just lost its context has also lost its attachment to
# the process it was running, so this is the cheapest moment to reconcile the
# goal and to cut ceremony. It asks; it never injects the goal text -- the
# record wins over any hook.
#
# Lead (SessionStart resume|compact): the run's goal is recorded in
# <workspace>/run-contract.md (SKILL.md, Run workspace); the question points
# there. Seat (SubagentStart): the goal is the brief; the question names the
# same record so the seat can say whether its brief serves the run's goal.
#
# Registered in the plugin's hooks/hooks.json, not the skill's frontmatter:
# SubagentStart must reach every seat, and one registration serves both
# events.
#
# Silent exit 0 when: interpreter missing, payload unparsable, the event is
# neither of the two, a SessionStart source other than resume|compact
# (defensive -- the matcher already excludes startup and clear, where no goal
# exists yet), no marker-discovered run state, or the run records a terminal
# status.
#
# Decline path (hook runtime unavailable): the Resume section's reconciliation
# duty in SKILL.md for the lead; the brief-reading duty in each role contract
# for seats.
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init goal_restate]"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] || exit 0
[ -f "$FOUND_STATE" ] || exit 0
# Marker-authoritative: a scan hit may be an abandoned run or another
# session's; asking about a run this session does not own is noise.
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0

# The payload travels via a temp file: `python - <<heredoc` owns stdin for
# the script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/pave-init-restate.XXXXXX" 2>/dev/null)" || exit 0
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
        state = json.load(handle) or {}
except Exception:
    sys.exit(0)  # fail open: no payload or state, nothing to ask

# A run that recorded a terminal status is over, marker or not (SKILL.md, Run
# workspace). Same gate as the stop check and the reader reminder.
terminal = state.get("terminal_classification")
if isinstance(terminal, dict) and terminal.get("status"):
    sys.exit(0)

# The goal record: run-contract.md holds the goal as invoked (SKILL.md, Run
# workspace); a run that has not written it yet is pointed at run state.
contract = os.path.join(os.path.dirname(state_path), "run-contract.md")
record = contract if os.path.isfile(contract) else state_path

event = str(payload.get("hook_event_name") or "")
if event == "SessionStart":
    source = str(payload.get("source") or "")
    if source not in ("resume", "compact"):
        sys.exit(0)  # startup or clear: no goal exists yet
    text = (
        f"{tag} Your context was just rebuilt ({source}). Before acting, in a "
        f"few lines: (1) the goal and its reason as recorded in {record} -- the "
        "file wins over memory; (2) the declared next step and the fewest after "
        "it; for each, what breaks if you skip it -- cut any with no answer (a "
        "seat for a fact knowable from disk, a re-gate of a recorded approval, a "
        "lap with no new evidence). A needed cut the graph forbids is a graph "
        "defect: record it and route it to pave-evolve."
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
