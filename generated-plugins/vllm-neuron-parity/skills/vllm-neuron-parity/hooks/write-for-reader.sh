#!/usr/bin/env bash
# write-for-reader -- PostToolUse hook, matcher Write|Edit.
# Registered in hooks/hooks.json. Advisory only: it always exits 0, it never
# makes a permission decision, and it speaks only through additionalContext.
#
# Why: the lead skill's "Write for the reader" paragraph is followed only
# while its text is still in the context window. After that, agents drift
# back to identifier chains, pasted checker output, and short notes no
# stranger can read. This hook repeats the duty at the moment it applies:
# when any actor (lead or subagent) writes a markdown file inside the active
# run workspace, outside the exempt working-state directories, it reminds.
#
# Workspace: the PARENT of the run-state directory. With state at
# artifacts/run/run-state.json the workspace is artifacts/, so campaign
# documents (artifacts/campaigns/...) are covered. A plain dirname(state)
# would be artifacts/run/ and would miss every campaign document.
#
# Exempt (working state written for the next agent, not a person): any path
# component named attempts, measurements, increments, intake-preflight, or
# index. The campaign-name position (campaigns/<name>/...) is never tested,
# so a campaign named "index" still reminds. Everything else under the
# workspace reminds (run/delta/<t>/report.md, run/backlog/, campaigns/*/design/,
# kickoff/, approvals/, verdicts/, rederivations/, pr/, closure/, reviews/).
#
# Throttle: the 1st matching write in a session reminds, then every Nth
# after (VLLM_NEURON_PARITY_READER_EVERY, default 3). The counter lives at
# ${TMPDIR:-/tmp}/vllm-neuron-parity-reader/<session_id>-<checksum of the
# full state path>. The state directory basename is always "run", so the key
# is the checksum of the full path, never the basename.
#
# Cap notice (references/artifact-layout.md section 4.12): when the written
# document is over its cap (VLLM_NEURON_PARITY_CAP_LINES, default 400;
# VLLM_NEURON_PARITY_CAP_BYTES, default 61440) the reminder names the size and
# the deletion-lap duty. That sentence bypasses the throttle once per session
# and file, so the first over-cap write is never silently swallowed. The cap
# binds living documents only; the hook cannot tell a write-once record or a
# transcript from a plan by path, so the sentence says which class to shrink
# and the writer classifies.
#
# Run discovery is marker-only: .vllm-neuron-parity-run at a candidate root
# (CODEX_PROJECT_DIR, CLAUDE_PROJECT_DIR, payload cwd, PWD). Its first line
# is the absolute path of run-state.json. No scan fallback. A run whose
# terminal_classification.status is set is inactive, so the hook is silent.
#
# Silent exit 0 when: interpreter missing, payload unparsable, no marker, no
# state file, state unparsable, run terminal, write is not markdown, write
# is outside the workspace, write is in an exempt directory, or the throttle
# window holds.
#
# Decline path (hook runtime unavailable): the prose duty in SKILL.md stands.
#
# Interpreter: python3 by default; override with VLLM_NEURON_PARITY_PYTHON.

set -uo pipefail

PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[vllm-neuron-parity write-for-reader]"
EVERY="${VLLM_NEURON_PARITY_READER_EVERY:-3}"
CAP_LINES="${VLLM_NEURON_PARITY_CAP_LINES:-400}"
CAP_BYTES="${VLLM_NEURON_PARITY_CAP_BYTES:-61440}"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

# The payload travels via a temp file: `python - <<heredoc` owns stdin for
# the script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/vllm-neuron-parity-reader.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0

"$PY" - "$TAG" "$PAYLOAD_FILE" "$EVERY" \
  "${CODEX_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}" "${PWD:-}" \
  "$CAP_LINES" "$CAP_BYTES" <<'PYEOF' 2>/dev/null || exit 0
import json
import os
import re
import sys
import tempfile
import zlib
from pathlib import Path

tag, payload_file, every_raw = sys.argv[1], sys.argv[2], sys.argv[3]
codex_root, claude_root, pwd_root = sys.argv[4], sys.argv[5], sys.argv[6]
cap_lines_raw, cap_bytes_raw = sys.argv[7], sys.argv[8]

try:
    every = max(1, int(every_raw))
except ValueError:
    every = 3
try:
    cap_lines, cap_bytes = max(1, int(cap_lines_raw)), max(1, int(cap_bytes_raw))
except ValueError:
    cap_lines, cap_bytes = 400, 61440

try:
    with open(payload_file, encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    sys.exit(0)  # fail open: no payload, nothing to judge
if not isinstance(payload, dict):
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
if not isinstance(tool_input, dict):
    sys.exit(0)
file_path = tool_input.get("file_path") or ""
if not isinstance(file_path, str) or not file_path.endswith(".md"):
    sys.exit(0)

# --- run-state discovery (marker only) --------------------------------------
payload_cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
state_path = None
for root in (codex_root, claude_root, payload_cwd, pwd_root):
    if not root:
        continue
    marker = os.path.join(root, ".vllm-neuron-parity-run")
    if not os.path.isfile(marker):
        continue
    try:
        with open(marker, encoding="utf-8") as handle:
            first = handle.readline().strip()
    except Exception:
        continue
    if first and os.path.isfile(first):
        state_path = first
        break
if state_path is None:
    sys.exit(0)

# A run with a settled terminal is inactive: nothing to remind about.
try:
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
except Exception:
    sys.exit(0)
if not isinstance(state, dict):
    sys.exit(0)
terminal = state.get("terminal_classification")
if isinstance(terminal, dict):
    settled = bool(terminal.get("status") or terminal.get("classification"))
else:
    settled = bool(terminal)
if settled:
    sys.exit(0)

# --- workspace = parent of the run-state directory --------------------------
try:
    state_resolved = Path(state_path).resolve()
    root = state_resolved.parent.parent
    target = Path(file_path).resolve()
    relative = target.relative_to(root)
except Exception:
    sys.exit(0)  # outside the active run workspace

EXEMPT = {"attempts", "measurements", "increments", "intake-preflight", "index"}
# A campaign may carry any name, so the campaign-name position
# (campaigns/<name>/...) is never tested against the exempt set.
parts = list(relative.parts)
if len(parts) >= 2 and parts[0] == "campaigns":
    tested = parts[:1] + parts[2:]
else:
    tested = parts
if any(part in EXEMPT for part in tested):
    sys.exit(0)

# --- cap (references/artifact-layout.md section 4.12): measure what was written
try:
    size = target.stat().st_size
    with open(target, "rb") as handle:
        lines = sum(1 for _ in handle)
except Exception:
    size, lines = 0, 0  # unreadable target: nothing to measure
over_cap = lines > cap_lines or size > cap_bytes

# --- throttle ---------------------------------------------------------------
session = str(payload.get("session_id") or "global") or "global"
session = re.sub(r"[^A-Za-z0-9._-]", "_", session)
state_key = zlib.crc32(str(state_resolved).encode("utf-8")) & 0xFFFFFFFF
counter_dir = Path(tempfile.gettempdir()) / "vllm-neuron-parity-reader"
first_over_cap = False
try:
    counter_dir.mkdir(parents=True, exist_ok=True)
    counter = counter_dir / f"{session}-{state_key}"
    try:
        count = int(counter.read_text().strip() or 0)
    except Exception:
        count = 0
    count += 1
    counter.write_text(str(count))
    if over_cap:
        # Once per session and file: the first over-cap write is never swallowed
        # by the throttle; later ones ride the normal window.
        file_key = zlib.crc32(str(target).encode("utf-8")) & 0xFFFFFFFF
        marker = counter_dir / f"{session}-{state_key}-{file_key}.overcap"
        first_over_cap = not marker.exists()
        marker.write_text(str(lines))
except Exception:
    count = 1  # counter unavailable: remind rather than stay silent forever

if (count - 1) % every != 0 and not first_over_cap:
    sys.exit(0)

text = (
    f"{tag} You just wrote a document a person will read ({relative}). "
    "Re-read it as a stranger and re-phrase in concise simple plain english "
    "where it fails: lead each entry with one sentence that says what "
    "happened and why; an identifier is a pointer, not a noun - pair it with "
    "its plain name at first use (\"the block FP8 target (`t-012`)\"); "
    "digests, counts, and checker output belong in run state or the check's "
    "own log, cited in one line, never mixed into the narrative. A reader "
    "must learn what happened, what changed, and what is still open in one "
    "pass (the lead skill's 'Write for the reader' paragraph)."
)
if over_cap:
    text += (
        f" This document is {lines} lines and {-(-size // 1024)} KB, over its cap of "
        f"{cap_lines} lines / {cap_bytes // 1024} KB for a living document "
        "(references/artifact-layout.md section 4.12). If it is a living document "
        "- edited in place, current state only - run a deletion lap before the "
        "next review lap: collapse landed increments to ledger rows, move frozen "
        "values to the registration record, and drop narration. A write-once "
        "record, an append-only record, or a transcript sits outside the cap: "
        "leave it."
    )
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    }
}))
PYEOF

exit 0
