#!/usr/bin/env bash
# _hook_common.sh -- sourceable skeleton for the orchestrate lead hook
# (goal_restate.sh); written so a second hook can source it unchanged.
#
# It carries the parts that do not change between hooks: the payload parse
# with a parse-fail sentinel and fail-open exit, the session-scoped run
# discovery, the run-log readers, and the JSON additionalContext emitter. Each hook keeps only its trigger check,
# its question text, and its exit code.
#
# Usage (from a hook, after `set -uo pipefail`):
#   HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$HOOK_DIR/_hook_common.sh" 2>/dev/null || exit 0
#   hook_read_payload            # exits 0 on any read or parse failure
#   find_owned_run || exit 0     # sets RUN_LOG
#
# Contract: never writes stdout or stderr (hooks reserve stdout for JSON and
# stderr for the stop text). Only hook_read_payload exits, and only with 0.
# Safe to source more than once.
#
# Run log (spec section 4): <root>/.orchestrate/<run-id>.md, append-only,
# written by the orchestrator through scripts/orchestrate_log.py. Event lines
# are `- HH:MM EVENT <actor> <free text>`; the readers below parse exactly the
# lines that script's LINE pattern parses, so the hooks and `status` agree.
#
# Interpreter: python3 by default; override with ORCHESTRATE_PYTHON.

if ! declare -F hook_read_payload >/dev/null 2>&1; then

ORCH_PY="${ORCHESTRATE_PYTHON:-python3}"
ORCH_PY="${ORCH_PY/#\~\//$HOME/}"   # expand a leading ~/ (env blocks do not tilde-expand)

# hook_read_payload -- read the hook payload from stdin and set SESSION_ID,
# STOP_ACTIVE (1/0), AGENT_ID, AGENT_TYPE, HOOK_EVENT, HOOK_SOURCE.
# Exits 0 when the interpreter is missing or the payload is not a JSON
# object: a hook must never act on input it could not read, and a failed
# parse must not look like a minimal valid payload.
hook_read_payload() {
  local payload fields
  payload="$(cat 2>/dev/null || true)"
  command -v "$ORCH_PY" >/dev/null 2>&1 || exit 0
  fields="$(printf '%s' "$payload" | "$ORCH_PY" -c '
import json, sys
try:
    payload = json.loads(sys.stdin.read())
except Exception:
    payload = None
if not isinstance(payload, dict):
    print("PARSE_FAIL")
    sys.exit(0)
def field(key):
    value = payload.get(key)
    return "" if value is None else str(value).replace("\r", " ").replace("\n", " ")
lines = ["OK", field("session_id"), "1" if payload.get("stop_hook_active") else "0",
         field("agent_id"), field("agent_type"), field("hook_event_name"), field("source")]
print("\n".join(lines))
' 2>/dev/null || true)"
  [ "$(printf '%s\n' "$fields" | sed -n 1p)" = "OK" ] || exit 0
  SESSION_ID="$(printf '%s\n' "$fields" | sed -n 2p)"
  STOP_ACTIVE="$(printf '%s\n' "$fields" | sed -n 3p)"
  AGENT_ID="$(printf '%s\n' "$fields" | sed -n 4p)"
  AGENT_TYPE="$(printf '%s\n' "$fields" | sed -n 5p)"   # parsed for completeness; never a seat signal alone
  HOOK_EVENT="$(printf '%s\n' "$fields" | sed -n 6p)"
  HOOK_SOURCE="$(printf '%s\n' "$fields" | sed -n 7p)"
}

# find_owned_run -- set RUN_LOG to the open run this session owns and return
# 0; return 1 with RUN_LOG="" otherwise.
# Marker-authoritative and session-scoped (spec section 5): for root in
# CLAUDE_PROJECT_DIR then $PWD, read `$root/.orchestrate/active-<session_id>`,
# whose first line is the absolute log path. No marker, a missing or
# unreadable log, or a closed run: no run. A payload carrying agent_id is a
# subagent and owns no run; an empty session id, or one that is not a plain
# name, owns no run either (it is spliced into file names).
find_owned_run() {
  RUN_LOG=""
  [ -z "${AGENT_ID:-}" ] || return 1
  case "${SESSION_ID:-}" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  # Quoted iteration: root paths may contain spaces.
  local root marker candidate
  for root in "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
    [ -n "$root" ] || continue
    marker="$root/.orchestrate/active-$SESSION_ID"
    [ -f "$marker" ] || continue
    candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -n "$candidate" ] && [ -f "$candidate" ] && [ -r "$candidate" ] || continue
    log_is_closed "$candidate" && continue
    RUN_LOG="$candidate"
    return 0
  done
  return 1
}

# Event lines exactly as orchestrate_log.py parses them:
# `^- \d\d:\d\d (\S+) (\S+) ?(.*)$` -> $3 is the event, $4 the actor.
_ORCH_EVENT_LINE='^- [0-9][0-9]:[0-9][0-9] [^[:space:]]+ [^[:space:]]+'

# log_is_closed LOG -- 0 when the last non-empty line is a CLOSE event.
log_is_closed() {
  awk -v re="$_ORCH_EVENT_LINE" '
    { sub(/\r$/, "") }
    NF { last = $0 }
    END { split(last, f, " "); exit !(last ~ re && f[3] == "CLOSE") }
  ' "$1" 2>/dev/null
}

# _log_subtasks LOG MODE -- MODE "open" prints each open subtask, one per line,
# in first-PLAN order; MODE "count" prints how many subtasks the run has now
# (every worker ever opened, minus the ones a RESPAWN replaced).
# Open state is derived in file order (spec section 4): `PLAN <w>` opens w;
# `RESPAWN <old> -> <new>` closes old and opens new; `DONE <w>` closes w. A
# worker is open when its latest such line opens it, so a fix-round PLAN after
# a whole-goal REVIEW REVISE reopens a worker that already has a DONE.
# Nothing else is derived.
# A RESPAWN names `<old> -> <new>` after the event word in either form:
# `RESPAWN worker-3 -> worker-4 | ...` (the spec's example) or
# `RESPAWN team-lead worker-3 -> worker-4 | ...` (SKILL.md's command). As in
# orchestrate_log.py, the first `X -> Y` after the event word wins.
_log_subtasks() {
  awk -v re="$_ORCH_EVENT_LINE" -v mode="$2" '
    function open_(w) { if (!(w in state)) order[++n] = w; state[w] = "open" }
    { sub(/\r$/, "") }
    $0 !~ re { next }
    $3 == "PLAN" { open_($4) }
    $3 == "RESPAWN" {
      for (i = 5; i < NF; i++) if ($i == "->") break
      if (i < NF) {
        from = $(i - 1); to = $(i + 1)
        if (from in state) state[from] = "replaced"
        open_(to)
      }
    }
    $3 == "DONE" && ($4 in state) { state[$4] = "done" }
    END {
      for (i = 1; i <= n; i++) {
        w = order[i]
        if (state[w] == "replaced") continue
        if (mode == "open" && state[w] == "open") print w
      }
    }
  ' "$1" 2>/dev/null
}
log_open_subtasks() { _log_subtasks "$1" open; }

# emit_additional_context EVENT TEXT -- print one hookSpecificOutput JSON
# object on stdout; prints nothing if the interpreter fails.
emit_additional_context() {
  "$ORCH_PY" -c '
import json, sys
print(json.dumps({"hookSpecificOutput": {"hookEventName": sys.argv[1], "additionalContext": sys.argv[2]}}))
' "$1" "$2" 2>/dev/null || true
}

fi
