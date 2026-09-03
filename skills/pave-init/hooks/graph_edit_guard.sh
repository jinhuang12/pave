#!/usr/bin/env bash
# graph_edit_guard -- PreToolUse (Edit|Write|MultiEdit), blocking
# (rung: blocking guard; exit 2 denies, every other path exits 0).
#
# A delivered workflow keeps one live canonical graph (`workflow.pave.yaml`
# plus any child `<name>.pave.yaml` beside it) and one append-only ledger
# (`revisions.yaml`). The graph is landed by `record_revision.py land` from a
# reviewed patch, so the ledger's digests describe the file on disk. A direct
# Edit or Write on either file skips the patch, the review, and the ledger
# entry at once: the digests stop describing the graph -- or are rewritten to
# match an unreviewed one -- and no reader can tell which revision is running.
#
# Blocking rung (references/lead-alignment-hooks.md, hook doctrine): the
# violation has occurred in the field -- a lead amended its pinned graph in
# place -- it is costly, and it is precisely path-detectable, because the
# `revisions.yaml` beside the target is what makes the directory an evolution
# root, so the guard cannot misfire on any other path.
#
# Denies (one paragraph on stderr, exit 2) only when all three hold:
#   1. the target's basename ends in `.pave.yaml`, or is `revisions.yaml`,
#   2. a file named `revisions.yaml` already sits in the same directory,
#   3. no `.landing` marker sits in that directory.
# The `.landing` marker is written by `record_revision.py land`, `pin`, and
# `rollback` for the length of the operation; the tool writes through Bash,
# which this event never sees, so its own writes pass either way. Creating a
# ledger where none exists (`init`) is not an edit of a pinned record.
#
# No agent identity exemption: the guard fires for the lead and for every
# subagent. The actor a prohibition has to survive is the one that never read
# the lead skill, so an `agent_id` in the payload changes nothing here.
#
# Bash-tool edits (`sed -i`, a shell redirect) are not path-detectable at this
# event: the payload carries a command string, not a file path. Bash therefore
# stays at the observing rung, and `record_revision.py verify` at run start and
# resume catches it -- an unrecorded edit shows up as a live digest that does
# not match the head entry's `digest_after`.
#
# Registration -- a generated plugin's hooks/hooks.json, so subagent edits are
# seen too (a skill-frontmatter hook fires only for the agent that invoked the
# skill):
#
#   { "hooks": { "PreToolUse": [ { "matcher": "Edit|Write|MultiEdit",
#       "hooks": [ { "type": "command",
#         "command": "\"${CLAUDE_PLUGIN_ROOT}/skills/<workflow-name>/hooks/graph_edit_guard.sh\"",
#         "timeout": 10 } ] } ] } }
#
# pave-init itself registers nothing: the package has no evolution root, and
# its own graph (references/pave-init.pave.yaml) is landed by the release.
#
# Silent exit 0 when: interpreter missing, payload unparsable or empty, no
# `tool_input.file_path`, the target is neither a `.pave.yaml` nor the ledger,
# no `revisions.yaml` beside it, or a `.landing` marker is present. A blocking
# hook must never act on input it cannot read.
#
# Decline path (hook runtime unavailable): degrades to the prose prohibition
# in the evolution contract plus `record_revision.py verify` at start and
# resume, which reports the unrecorded edit after the fact.
#
# Interpreter: python3 by default; override with PAVE_INIT_PYTHON.

set -uo pipefail

PY="${PAVE_INIT_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)
TAG="[pave-init graph_edit_guard]"

PAYLOAD="$(cat 2>/dev/null || true)"

command -v "$PY" >/dev/null 2>&1 || exit 0

# The payload travels via a temp file: `python - <<heredoc` owns stdin for
# the script itself, so piping the payload there would silently lose it.
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/pave-init-graph-guard.XXXXXX" 2>/dev/null)" || exit 0
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0

# The decision is printed, never acted on here: a python traceback then leaves
# no verdict line and the guard falls through to exit 0.
DECISION="$("$PY" - "$PAYLOAD_FILE" <<'PYEOF' 2>/dev/null
import json
import sys
from pathlib import Path

payload_file = sys.argv[1]

try:
    with open(payload_file, encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    sys.exit(0)  # fail open: no payload, nothing to judge
if not isinstance(payload, dict):
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
if not isinstance(file_path, str) or not file_path:
    sys.exit(0)  # Edit, Write and MultiEdit all carry file_path; nothing else to judge

# Resolve the target itself when it exists, else its parent plus the basename,
# so a graph about to be created inside a symlinked root still resolves.
try:
    target = Path(file_path)
    resolved = target.resolve() if target.exists() else target.parent.resolve() / target.name
except Exception:
    sys.exit(0)

name = resolved.name
if not (name.endswith(".pave.yaml") or name == "revisions.yaml"):
    sys.exit(0)

root = resolved.parent
# The ledger beside the graph is what makes this directory an evolution root.
if not (root / "revisions.yaml").is_file():
    sys.exit(0)
# A landing is in progress: record_revision.py owns the graph until it clears.
if (root / ".landing").exists():
    sys.exit(0)

print("DENY")
print(resolved)
PYEOF
)" || exit 0

[ "$(printf '%s\n' "$DECISION" | sed -n 1p)" = "DENY" ] || exit 0
GRAPH="$(printf '%s\n' "$DECISION" | sed -n 2p)"

cat >&2 <<EOF
$TAG Denied: $GRAPH is part of an evolution root's pinned record -- the live canonical graph and the revisions.yaml beside it -- and no .landing marker is present. Both are written only by \`record_revision.py land\`, \`pin\`, and \`rollback\` from a reviewed patch (history/v<N>.patch), never edited directly, so the ledger's digests keep describing the graph on disk: write the successor as a patch with its preamble, have the update reviewer pass it, then land it. If a landing was interrupted, run \`record_revision.py verify\` first and restore what it reports from version control before touching either file.
EOF
exit 2
