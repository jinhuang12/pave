#!/usr/bin/env bash
# planning-layout-warn -- PostToolUse (Write|Edit), observing
# (rung: socratic reinjection; always exit 0).
#
# The planning workspace's layout rules (references/planning-layout.md) are
# followed until their prose leaves the context window. This hook re-injects
# them at the moment they are broken: when a write lands under the active
# run's planning/ directory at a path matching no allowed pattern, when a
# subagent writes a lead-owned file (frontier.yaml or root-contract.md), or
# when a draft's written content mints a lead-owned conflict id (c<N>). It
# warns via non-blocking additionalContext and never blocks: layout drift is
# detectable and cheap to repair, so an observing rung is sufficient --
# content checks beyond the c<N> heuristic belong to
# validate_run_state.py --frontier.
#
# Registered in the plugin's hooks/hooks.json, not the skill's frontmatter: a
# skill-frontmatter hook fires only for the agent that invoked the skill and
# never sees a subagent's write (measured on Claude Code 2.1.258) -- and a
# node planner's write is exactly what this hook watches.
#
# Silent exit 0 when: interpreter missing, payload unparsable, no marker-
# discovered run state, the run records a terminal status (the abandon path
# sets it and may leave the marker), write outside that run's planning/
# directory, or the write conforms.
#
# Decline path (hook runtime unavailable): degrades to the layout duties in
# references/planning-layout.md.
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init planning-layout-warn]"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] || exit 0
[ -f "$FOUND_STATE" ] || exit 0
# Marker-authoritative: a scan hit may be an abandoned run or another
# session's; warning about a run this session does not own is noise.
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0

PLANNING_DIR="$(dirname "$FOUND_STATE")/planning"

# The payload travels via a temp file: `python - <<heredoc` owns stdin for
# the script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/pave-init-layout-warn.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0

"$PY" - "$PLANNING_DIR" "$TAG" "$PAYLOAD_FILE" "$FOUND_STATE" <<'PYEOF' 2>/dev/null || exit 0
import json
import re
import sys
from pathlib import Path

planning_dir, tag, payload_file, state_path = sys.argv[1:5]

try:
    with open(payload_file, encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    sys.exit(0)  # fail open: no payload, nothing to judge

# A run that recorded a terminal status is over, marker or not: the abandon
# path sets the status and may leave the marker in place (SKILL.md, Run
# workspace). Same gate as the stop check and the staleness reminder.
try:
    with open(state_path, encoding="utf-8") as handle:
        terminal = (json.load(handle) or {}).get("terminal_classification")
except Exception:
    terminal = None  # unparsable state is validate_run_state.py business
if isinstance(terminal, dict) and terminal.get("status"):
    sys.exit(0)


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


tool_input = payload.get("tool_input") or {}
file_path = tool_input.get("file_path") or ""
if not file_path:
    sys.exit(0)

try:
    target = Path(file_path).resolve()
    planning = Path(planning_dir).resolve()
    relative = target.relative_to(planning)
except Exception:
    sys.exit(0)  # outside the active run's planning/ directory

is_subagent = bool(find(payload, "agent_type") not in ("", "main", "lead", "root", "primary")
                   or find(payload, "agent_id"))

warnings = []

name = str(relative)
if name in ("frontier.yaml", "root-contract.md"):
    if is_subagent:
        warnings.append(
            f"a subagent wrote planning/{name} - the lead is its only "
            "writer; planners write exactly the one draft path their brief "
            "names and report everything else in their reply"
        )
elif re.fullmatch(r"[A-Za-z0-9_.-]+\.draft\.pave\.yaml", name):
    content = tool_input.get("content") or tool_input.get("new_string") or ""
    if re.search(r"^\s*-?\s*id:\s*['\"]?c[0-9]+['\"]?\s*$", content, re.MULTILINE):
        warnings.append(
            "this draft mints a conflict id in the lead-owned c<N> namespace - "
            "conflict ids are lead-assigned in frontier.yaml's register; report "
            "the conflict without an id or use a node-local label (n1, e1, ...)"
        )
else:
    warnings.append(
        f"'{name}' matches no allowed planning/ pattern (root-contract.md, "
        "frontier.yaml, or *.draft.pave.yaml) - the layout and its write "
        "ownership are in references/planning-layout.md"
    )

if not warnings:
    sys.exit(0)

text = (
    f"{tag} {'; '.join(warnings)}. This is a warning, not a block: verify with "
    "scripts/validate_run_state.py --frontier and repair the layout before "
    "routing on this write."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    }
}))
PYEOF

exit 0
