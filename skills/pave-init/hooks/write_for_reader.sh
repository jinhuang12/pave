#!/usr/bin/env bash
# write_for_reader -- PostToolUse (Write|Edit), observing
# (rung: reinjection; always exit 0).
#
# The write-for-the-reader duty (references/pave-spec.md section 8.5) is
# followed until its prose leaves the context window, and agents then drift
# back to identifier chains, inlined checker output, and telegraphic notes no
# stranger can parse. This hook re-injects the duty at the moment it binds:
# when any actor -- lead or subagent -- lands a markdown write inside the
# active run workspace, outside the exempt working-state directories
# (planning/, build/, exploration/), it reminds via non-blocking
# additionalContext. Throttled: the 1st matching write in a session reminds,
# then every Nth after (PAVE_INIT_READER_EVERY, default 3).
#
# Registered in the plugin's hooks/hooks.json, not the skill's frontmatter: a
# skill-frontmatter hook fires only for the agent that invoked the skill and
# never sees a subagent's write (measured on Claude Code 2.1.258).
#
# Silent exit 0 when: interpreter missing, payload unparsable, no
# marker-discovered run state, the run records a terminal status (the abandon
# path sets it and may leave the marker), the write is not markdown, the write is
# outside the workspace or inside an exempt directory, or the throttle
# window holds.
#
# Decline path (hook runtime unavailable): degrades to the section 8.5
# prose duty carried in SKILL.md and the role contracts.
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init write-for-reader]"
EVERY="${PAVE_INIT_READER_EVERY:-3}"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] || exit 0
[ -f "$FOUND_STATE" ] || exit 0
# Marker-authoritative: a scan hit may be an abandoned run or another
# session's; reminding about a run this session does not own is noise.
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0

WORKSPACE="$(dirname "$FOUND_STATE")"

# The payload travels via a temp file: `python - <<heredoc` owns stdin for
# the script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/pave-init-reader.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0

"$PY" - "$WORKSPACE" "$TAG" "$PAYLOAD_FILE" "$EVERY" "$FOUND_STATE" <<'PYEOF' 2>/dev/null || exit 0
import json
import os
import sys
import tempfile
from pathlib import Path

workspace, tag, payload_file, every_raw, state_path = sys.argv[1:6]

try:
    every = max(1, int(every_raw))
except ValueError:
    every = 3

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

tool_input = payload.get("tool_input") or {}
file_path = tool_input.get("file_path") or ""
if not file_path or not file_path.endswith(".md"):
    sys.exit(0)

try:
    target = Path(file_path).resolve()
    root = Path(workspace).resolve()
    relative = target.relative_to(root)
except Exception:
    sys.exit(0)  # outside the active run workspace

# Working state written for the next agent, not a person (section 8.5's
# exemption): planning queue and drafts, per-unit build records, explorer
# evidence reports the lead verifies and distills.
if relative.parts and relative.parts[0] in ("planning", "build", "exploration"):
    sys.exit(0)

session = str(payload.get("session_id") or "global") or "global"
counter_dir = Path(tempfile.gettempdir()) / "pave-init-reader"
try:
    counter_dir.mkdir(parents=True, exist_ok=True)
    counter = counter_dir / f"{session}-{root.name}"
    try:
        count = int(counter.read_text().strip() or 0)
    except Exception:
        count = 0
    count += 1
    counter.write_text(str(count))
except Exception:
    count = 1  # counter unavailable: remind rather than stay silent forever

if (count - 1) % every != 0:
    sys.exit(0)

text = (
    f"{tag} You just wrote a document a person will read ({relative}). "
    "Re-read it as a stranger and re-phrase in concise simple plain english "
    "where it fails: lead each entry with one sentence saying what happened "
    "and why; an identifier is a pointer, not a noun - pair it with its "
    "plain name at first use (\"the rotary increment (`inc-025`)\"); "
    "digests, counts, and checker output belong in run state or the check's "
    "own log, cited in one line, never interleaved with the narrative. A "
    "reader must learn what happened, what changed, and what is still open "
    "in one pass (references/pave-spec.md section 8.5)."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    }
}))
PYEOF

exit 0
