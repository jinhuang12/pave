#!/usr/bin/env bash
# release_reminder -- PostToolUse (Bash) hook for the Claude Code orchestrate
# lead (level: reminder; always exit 0).
#
# A finished teammate keeps its agent slot until the lead stops it, and leads
# forget. After each lead command that writes the run log (orchestrate_log.py
# init or append), this hook asks `orchestrate_log.py releasable` which agents
# are finished and not yet logged as released, and names them with the stop
# call and the RELEASE line to log. It repeats on every log write until the
# list is empty. The release rule lives in that script, so the hook and the
# lead's own `releasable` call agree.
#
# Silent exit 0 when: no interpreter, unparsable payload, not a Bash command
# that wrote the log, a subagent payload, no open run owned by this session
# (_hook_common.sh, find_owned_run), or nothing to release. Claude only: a
# finished Codex thread needs no close.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
TAG="[orchestrate release_reminder]"

hook_read_payload
[ "$TOOL_NAME" = "Bash" ] || exit 0
lead_wrote_log || exit 0
find_owned_run || exit 0

LOG_SCRIPT="$(cd "$HOOK_DIR/.." && pwd)/scripts/orchestrate_log.py"
NAMES="$(CLAUDE_CODE_SESSION_ID="$SESSION_ID" "$ORCH_PY" "$LOG_SCRIPT" --runtime claude releasable \
  --cwd "$RUN_ROOT" 2>/dev/null | paste -sd ' ' - | sed 's/ /, /g')"
[ -n "$NAMES" ] || exit 0
RELEASE_COMMAND="$("$ORCH_PY" -c '
import shlex, sys
q = shlex.quote
print(" ".join(q(s) for s in [sys.executable, sys.argv[1], "--runtime", "claude", "append", "RELEASE"]),
      "<name> --cwd", q(sys.argv[2]))
' "$LOG_SCRIPT" "$RUN_ROOT" 2>/dev/null)" || exit 0

TEXT="$TAG These agents are finished and still hold agent slots: $NAMES.
Stop each one now with \`TaskStop\` (task_id: its name), then log it: \`$RELEASE_COMMAND\`.
A later fix round goes to a fresh worker under the next unused index."

emit_additional_context PostToolUse "$TEXT"
exit 0
