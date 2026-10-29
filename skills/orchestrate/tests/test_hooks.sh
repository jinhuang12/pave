#!/usr/bin/env bash
# Invariant tests for the orchestrate lead hooks (spec
# docs/superpowers/specs/2026-10-05-orchestrate-in-pave-plugin-design.md,
# sections 5, 7 and 8). Tests behaviour, not wording:
#   discovery: silent with no marker, for a session that owns no run, when the
#              marker's log is missing, after CLOSE, and for a session id that
#              is not a plain name; a marker under $PWD counts when
#              CLAUDE_PROJECT_DIR has none
#   restate:   SessionStart compact and resume emit one additionalContext that
#              names the open subtasks, derived in file order: a RESPAWN-ed
#              replacement (either RESPAWN form) is open until its DONE, a
#              fix-round PLAN after a whole-goal REVIEW REVISE reopens its
#              worker, a RESPAWN of an unlogged worker does not hide its later
#              PLAN, and "none" when every subtask is DONE or no PLAN exists;
#              startup, clear, and any other event stay silent; never throttled
#   release:   a lead log write names the finished agents; other commands are silent
#   speed-up:  a lead log write asks the speed-up question, at most once per interval
#   registration: every orchestrate hook is plugin-level
#   fail open: an unparsable payload, empty stdin, a non-object payload, and a
#              missing interpreter leave the hook silent with exit 0
#
# Self-contained: fixture logs are written by hand inside a mktemp sandbox;
# no real log or marker is touched. The release hook reads them through
# orchestrate_log.py releasable.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
RESTATE_HOOK="$SKILL/hooks/goal_restate.sh"
RELEASE_HOOK="$SKILL/hooks/release_reminder.sh"
SPEEDUP_HOOK="$SKILL/hooks/speedup_nudge.sh"
COMMON="$SKILL/hooks/_hook_common.sh"

PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"
export CLAUDE_PROJECT_DIR="$WORK/proj with space"   # regression: roots with spaces
LOGDIR="$CLAUDE_PROJECT_DIR/.orchestrate"
mkdir -p "$LOGDIR" "$WORK/elsewhere"
cd "$WORK/elsewhere" || exit 1   # the $PWD root holds no marker unless a test writes one
unset ORCHESTRATE_PYTHON 2>/dev/null || true

report() { # name ok detail
  if [ "$2" = "1" ]; then
    PASS=$((PASS + 1)); echo "PASS  $1"
  else
    FAIL=$((FAIL + 1)); echo "FAIL  $1  ($3)"
  fi
}

# run_hook HOOK PAYLOAD -- sets RC, OUT (stdout), ERR (stderr).
run_hook() {
  printf '%s' "$2" | bash "$1" >"$WORK/out" 2>"$WORK/err"; RC=$?
  OUT="$(cat "$WORK/out")"; ERR="$(cat "$WORK/err")"
}
silent() { [ "$RC" = "0" ] && [ -z "$OUT" ] && [ -z "$ERR" ]; }
has_out() { printf '%s' "$OUT" | grep -q -- "$1"; }

restate_payload() { # $1 session, $2 hook_event_name, $3 source ("" to omit)
  if [ -n "${3:-}" ]; then
    printf '{"session_id":"%s","hook_event_name":"%s","source":"%s"}' "$1" "$2" "$3"
  else
    printf '{"session_id":"%s","hook_event_name":"%s"}' "$1" "$2"
  fi
}
compact() { restate_payload "$1" SessionStart compact; }

# new_log NAME -- stdin is the event block; writes the header and prints the path.
new_log() {
  local path="$LOGDIR/$1.md"
  { printf '# orchestrate run %s\ngoal: make all 22 textkit tests pass without editing tests/\n\n' "$1"; cat; } > "$path"
  printf '%s\n' "$path"
}
own() { # $1 session, $2 log path -- writes the session's ownership marker
  printf '%s\n' "$2" > "$LOGDIR/active-$1"
}
# open_is TEXT -- the last run emitted additionalContext naming exactly these open subtasks
open_is() { [ "$RC" = "0" ] && [ -z "$ERR" ] && has_out "Open in the log now: $1"; }

PLAN3='- 12:03 PLAN worker-1 -> da-1 | textkit/slug.py | pytest tests/test_slug.py | one agent: one module
- 12:03 PLAN worker-2 -> da-1 | textkit/duration.py | pytest tests/test_duration.py | one agent: same
- 12:03 PLAN worker-3 -> da-1 | textkit/roman.py | pytest tests/test_roman.py | one agent: same'
DONE12='- 12:06 DONE worker-1 r1 PASS
- 12:07 DONE worker-2 r2 PASS | r1 REVISE 1 HIGH duration.py:8'
RESPAWN34='- 12:09 RESPAWN worker-3 -> worker-4 | bedrock 503, nothing written'
DONE4='- 12:12 DONE worker-4 r1 PASS'
INTEGRATE='- 12:13 INTEGRATE 22/22 green; only 3 module files changed'
REVIEW='- 12:14 REVIEW whole-goal da-1 PASS'
CLOSE='- 12:15 CLOSE accepted | 3 modules, 1 respawn, 0 deferred'

OPEN_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" | new_log open)"
RESPAWN_OPEN_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" "$RESPAWN34" | new_log respawn-open)"
ALL_DONE_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" "$RESPAWN34" "$DONE4" | new_log all-done)"
CLOSED_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" "$RESPAWN34" "$DONE4" "$INTEGRATE" "$REVIEW" "$CLOSE" "" | new_log closed)"
NO_PLAN_LOG="$(printf '' | new_log no-plan)"
# Fix round (spec section 6, Step 6): a whole-goal REVISE is followed by one
# PLAN line per owning worker, which reopens a subtask that already has a DONE.
FIX_OPEN_LOG="$(printf '%s\n' '- 12:03 PLAN worker-1 -> da-1 | textkit/slug.py | pytest tests/test_slug.py | one agent: one module' \
  '- 12:06 DONE worker-1 r1 PASS' "$INTEGRATE" '- 12:14 REVIEW whole-goal da-1 REVISE 1 HIGH slug.py:12' \
  '- 12:15 PLAN worker-1 -> da-1 | fix: slug.py:12 drops unicode | pytest tests/test_slug.py' | new_log fix-open)"
FIX_DONE_LOG="$(printf '%s\n' "$(cat "$FIX_OPEN_LOG")" '- 12:20 DONE worker-1 r2 PASS | r1 PASS; r2 PASS' | new_log fix-done)"
LEAD_RESPAWN_OPEN_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" \
  '- 12:09 RESPAWN team-lead worker-3 -> worker-8 | 503 after retries' | new_log lead-respawn-open)"
LEAD_RESPAWN_DONE_LOG="$(printf '%s\n' "$PLAN3" "$DONE12" \
  '- 12:09 RESPAWN team-lead worker-3 -> worker-8 | 503 after retries' \
  '- 12:12 DONE worker-8 r1 PASS' | new_log lead-respawn-done)"
# A PLAN line can be lost (an append that failed twice); a RESPAWN of that
# unlogged worker must not hide a later PLAN of the same name.
GHOST_LOG="$(printf '%s\n' '- 12:03 PLAN worker-1 -> da-1 | a.py | pytest a' '- 12:05 DONE worker-1 r1 PASS' \
  '- 12:06 RESPAWN team-lead ghost -> worker-9 | 503' '- 12:08 DONE worker-9 r1 PASS' \
  '- 12:09 PLAN ghost -> da-1 | b.py | pytest b' | new_log ghost)"

# --- syntax ------------------------------------------------------------------

for f in "$COMMON" "$RESTATE_HOOK" "$RELEASE_HOOK" "$SPEEDUP_HOOK"; do
  ok=0; [ -f "$f" ] && bash -n "$f" 2>/dev/null && ok=1
  report "syntax: bash -n $(basename "$f")" "$ok" "missing or syntax error"
done
ok=0; [ -x "$RESTATE_HOOK" ] && [ -x "$RELEASE_HOOK" ] && [ -x "$SPEEDUP_HOOK" ] && ok=1
report "syntax: the hooks are executable (hooks.json runs them directly)" "$ok" "mode"

# --- discovery -----------------------------------------------------------------

run_hook "$RESTATE_HOOK" "$(compact d1)"
ok=0; silent && ok=1
report "discovery: silent with no marker" "$ok" "rc=$RC out=$OUT"

own d-owner "$ALL_DONE_LOG"
run_hook "$RESTATE_HOOK" "$(compact d-other)"
ok=0; silent && ok=1
report "discovery: silent for a session that owns no run" "$ok" "rc=$RC out=$OUT"

own d2 "$LOGDIR/gone.md"
run_hook "$RESTATE_HOOK" "$(restate_payload d2 SessionStart resume)"
ok=0; silent && ok=1
report "discovery: silent when the marker's log is missing" "$ok" "rc=$RC out=$OUT"

own d3 "$CLOSED_LOG"
run_hook "$RESTATE_HOOK" "$(compact d3)"
ok=0; silent && ok=1
report "discovery: silent after CLOSE (trailing blank line too)" "$ok" "rc=$RC out=$OUT"

mkdir -p "$LOGDIR/active-sub"   # an unchecked id "sub/x" would resolve to this marker
printf '%s\n' "$ALL_DONE_LOG" > "$LOGDIR/active-sub/x"
run_hook "$RESTATE_HOOK" '{"session_id":"sub/x","hook_event_name":"SessionStart","source":"compact"}'
ok=0; silent && ok=1
report "discovery: a session id that is not a plain name is silent" "$ok" "rc=$RC out=$OUT"

PWD_PROJ="$WORK/pwd proj"
mkdir -p "$PWD_PROJ/.orchestrate"
printf '%s\n' "$PLAN3" "$DONE12" "$RESPAWN34" > "$PWD_PROJ/.orchestrate/run.md"
printf '%s\n' "$PWD_PROJ/.orchestrate/run.md" > "$PWD_PROJ/.orchestrate/active-d5"
RC=x; OUT=""; ERR=""
( cd "$PWD_PROJ" && unset CLAUDE_PROJECT_DIR && printf '%s' "$(compact d5)" | bash "$RESTATE_HOOK" >"$WORK/out" 2>"$WORK/err" ); RC=$?
OUT="$(cat "$WORK/out")"; ERR="$(cat "$WORK/err")"
ok=0; open_is "worker-4" && ok=1
report "discovery: a marker under \$PWD counts when CLAUDE_PROJECT_DIR has none" "$ok" "rc=$RC out=$OUT"

# --- restate: shape -----------------------------------------------------------------

own r1 "$OPEN_LOG"
run_hook "$RESTATE_HOOK" "$(compact r1)"
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && has_out '"hookEventName": "SessionStart"' \
  && has_out '"additionalContext"' && has_out "$OPEN_LOG" && ok=1
report "restate: compact emits additionalContext naming the log" "$ok" "rc=$RC out=$OUT"
ok=0; has_out "status" && has_out "live agents" && has_out "open subtask" && has_out "CLOSE" \
  && has_out "goal" && has_out "ou orchestrate, you do not implement" \
  && has_out "orchestrate_log.py" && has_out "\[orchestrate goal_restate\]" && ok=1
report "restate: text asks for goal, status, live agents, open subtasks, fewest steps to CLOSE" "$ok" "out=$OUT"
ok=0; printf '%s' "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["hookSpecificOutput"]["hookEventName"]=="SessionStart"; assert d["hookSpecificOutput"]["additionalContext"]' 2>/dev/null && ok=1
report "restate: output is one valid JSON object" "$ok" "out=$OUT"
ok=0; open_is "worker-3" && ok=1
report "restate: names the open subtasks computed from the log" "$ok" "out=$OUT"

run_hook "$RESTATE_HOOK" "$(restate_payload r1 SessionStart resume)"
ok=0; [ "$RC" = "0" ] && has_out '"additionalContext"' && has_out "(resume)" && ok=1
report "restate: resume emits additionalContext" "$ok" "rc=$RC out=$OUT"

run_hook "$RESTATE_HOOK" "$(compact r1)"
ok=0; [ "$RC" = "0" ] && has_out '"additionalContext"' && ok=1
report "restate: never throttled (a second compact fires again)" "$ok" "rc=$RC out=$OUT"

run_hook "$RESTATE_HOOK" "$(restate_payload r1 SessionStart startup)"
ok=0; silent && ok=1
report "restate: startup is silent" "$ok" "rc=$RC out=$OUT"

run_hook "$RESTATE_HOOK" "$(restate_payload r1 SessionStart clear)"
ok=0; silent && ok=1
report "restate: clear is silent" "$ok" "rc=$RC out=$OUT"

run_hook "$RESTATE_HOOK" "$(restate_payload r1 Stop)"
ok=0; silent && ok=1
report "restate: an unrelated event is silent" "$ok" "rc=$RC out=$OUT"

run_hook "$RESTATE_HOOK" "$(restate_payload r1 SubagentStart)"
ok=0; silent && ok=1
report "restate: SubagentStart is silent" "$ok" "rc=$RC out=$OUT"

# --- restate: open subtasks are derived in file order ---------------------------------

own o1 "$ALL_DONE_LOG"
run_hook "$RESTATE_HOOK" "$(compact o1)"
ok=0; open_is "none" && ok=1
report "restate: says none when every subtask is DONE" "$ok" "rc=$RC out=$OUT"

own o2 "$NO_PLAN_LOG"
run_hook "$RESTATE_HOOK" "$(compact o2)"
ok=0; open_is "none" && ok=1
report "restate: says none before any PLAN line" "$ok" "rc=$RC out=$OUT"

own o3 "$RESPAWN_OPEN_LOG"
run_hook "$RESTATE_HOOK" "$(compact o3)"
ok=0; open_is "worker-4" && ok=1
report "restate: a RESPAWN-ed replacement is open until its DONE" "$ok" "rc=$RC out=$OUT"

own o4 "$LEAD_RESPAWN_OPEN_LOG"
run_hook "$RESTATE_HOOK" "$(compact o4)"
ok=0; open_is "worker-8" && ok=1
report "restate: a replacement logged as 'RESPAWN team-lead old -> new' is open" "$ok" "rc=$RC out=$OUT"

own o5 "$LEAD_RESPAWN_DONE_LOG"
run_hook "$RESTATE_HOOK" "$(compact o5)"
ok=0; open_is "none" && ok=1
report "restate: a 'RESPAWN team-lead old -> new' replacement that is DONE closes the subtask" "$ok" "rc=$RC out=$OUT"

own o6 "$FIX_OPEN_LOG"
run_hook "$RESTATE_HOOK" "$(compact o6)"
ok=0; open_is "worker-1" && ok=1
report "restate: a fix-round PLAN after REVIEW REVISE reopens a DONE worker" "$ok" "rc=$RC out=$OUT"

own o7 "$FIX_DONE_LOG"
run_hook "$RESTATE_HOOK" "$(compact o7)"
ok=0; open_is "none" && ok=1
report "restate: the fix DONE closes the reopened worker" "$ok" "rc=$RC out=$OUT"

own o8 "$GHOST_LOG"
run_hook "$RESTATE_HOOK" "$(compact o8)"
ok=0; open_is "ghost" && ok=1
report "restate: a RESPAWN of an unlogged worker does not hide its later PLAN" "$ok" "rc=$RC out=$OUT"

# --- post-log-write reminders (release_reminder.sh, speedup_nudge.sh) --------------

bash_payload() { # $1 session, $2 command
  python3 -c 'import json,sys; print(json.dumps({"session_id": sys.argv[1], "hook_event_name": "PostToolUse",
    "tool_name": "Bash", "tool_input": {"command": sys.argv[2]}}))' "$1" "$2"
}
LOG_WRITE="python3 $SKILL/scripts/orchestrate_log.py --runtime claude append DONE worker-2 r2 PASS"

own p1 "$OPEN_LOG"
run_hook "$RELEASE_HOOK" "$(bash_payload p1 "$LOG_WRITE")"
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && has_out "worker-1, worker-2" && has_out "TaskStop" \
  && has_out "append RELEASE" && ok=1
report "release: a log write names the finished agents and how to stop and log them" "$ok" "rc=$RC out=$OUT err=$ERR"
run_hook "$RELEASE_HOOK" "$(bash_payload p1 "git status")"
ok=0; silent && ok=1
report "release: a command that does not write the log is silent" "$ok" "rc=$RC out=$OUT"

own p2 "$OPEN_LOG"
run_hook "$SPEEDUP_HOOK" "$(bash_payload p2 "$LOG_WRITE")"
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && has_out "overlap, parallelize, or cut" && has_out "worker-3" && ok=1
report "speed-up: a log write asks the speed-up question and names open subtasks" "$ok" "rc=$RC out=$OUT err=$ERR"
run_hook "$SPEEDUP_HOOK" "$(bash_payload p2 "$LOG_WRITE")"
ok=0; silent && ok=1
report "speed-up: a second log write inside the interval is silent" "$ok" "rc=$RC out=$OUT"

# --- registration ------------------------------------------------------------------
# Skill-frontmatter SessionStart hooks never run (Claude Code 2.1.312), and
# frontmatter hooks are gone after --resume, so every orchestrate hook is
# registered at plugin level.
PLUGIN_ROOT="$(cd "$SKILL/../.." && pwd)"
ok=0; python3 - "$PLUGIN_ROOT" <<'PY' && ok=1
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
def cmds(path, event):
    return " ".join(h["command"] for g in json.load(open(root / path))["hooks"].get(event, []) for h in g["hooks"])
assert "orchestrate/hooks/goal_restate.sh" in cmds("hooks/hooks.json", "SessionStart")
assert "orchestrate/hooks/release_reminder.sh" in cmds("hooks/hooks.json", "PostToolUse")
assert "orchestrate/hooks/speedup_nudge.sh" in cmds("hooks/hooks.json", "PostToolUse")
assert "orchestrate/hooks/speedup_nudge.sh" in cmds("codex/hooks/hooks.json", "PostToolUse")
front = (root / "skills/orchestrate/SKILL.md").read_text().split("\n---\n", 1)[0]
assert "\nhooks:" not in front
PY
report "registration: all orchestrate hooks are plugin-level, none in the skill frontmatter" "$ok" "see hooks.json"

# --- fail open -----------------------------------------------------------------------

own f1 "$ALL_DONE_LOG"
run_hook "$RESTATE_HOOK" 'not json'
ok=0; silent && ok=1
report "fail open: unparsable payload" "$ok" "rc=$RC out=$OUT err=$ERR"

run_hook "$RESTATE_HOOK" ''
ok=0; silent && ok=1
report "fail open: empty stdin" "$ok" "rc=$RC out=$OUT err=$ERR"

run_hook "$RESTATE_HOOK" '["f1"]'
ok=0; silent && ok=1
report "fail open: a non-object payload" "$ok" "rc=$RC out=$OUT err=$ERR"

RC=x
printf '%s' "$(compact f1)" | ORCHESTRATE_PYTHON="$WORK/no-python" bash "$RESTATE_HOOK" >"$WORK/out" 2>"$WORK/err"; RC=$?
OUT="$(cat "$WORK/out")"; ERR="$(cat "$WORK/err")"
ok=0; silent && ok=1
report "fail open: a missing interpreter" "$ok" "rc=$RC out=$OUT"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
