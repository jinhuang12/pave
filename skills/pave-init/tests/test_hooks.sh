#!/usr/bin/env bash
# Invariant tests for pave-init's lead-alignment hook pair (the Stage 6
# step 4 duty in SKILL.md, applied to this package itself). Tests the
# invariants from references/lead-alignment-hooks.md, not the wording:
#   stop:      first stop blocks (exit 2), the next STOP_EVERY-1 stops pass
#              (default 3), the stop after that blocks again,
#              stop_hook_active short-circuits, terminal runs are silent,
#              no-run sessions are silent
#   staleness: fresh state is silent, stale state fires additionalContext
#              once, throttles within the window, skips subagent payloads,
#              stays silent on terminal runs
#   layout:    every allowlisted planning/ path is silent for the lead, an
#              undeclared path warns, a lead-owned path warns when a
#              subagent writes it, and a terminal run is silent
#   reader:    a run-workspace .md write reminds (lead and subagent alike),
#              throttles to every Nth write, and stays silent for exempt
#              working-state directories, non-markdown writes, and terminal
#              runs; an over-cap document is named with its size once per
#              session and file even when the throttle holds, and the cap
#              follows PAVE_INIT_CAP_LINES
#   registration: the two subagent-facing hooks are in the plugin's
#              hooks/hooks.json (a skill-frontmatter hook never sees a
#              subagent's write); the frontmatter keeps only the lead-only pair
# Also: validate_run_state.py passes a well-formed instance and fails a
# broken one.
#
# Self-contained: everything runs inside a mktemp sandbox; no real run
# state, markers, or throttle files are touched.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SKILL="$(cd "$HERE/.." && pwd)"
STOP_HOOK="$SKILL/hooks/stop_alignment_check.sh"
STALE_HOOK="$SKILL/hooks/state_staleness_reminder.sh"
LAYOUT_HOOK="$SKILL/hooks/planning-layout-warn.sh"
READER_HOOK="$SKILL/hooks/write_for_reader.sh"
VALIDATOR="$SKILL/scripts/validate_run_state.py"

PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"
export CLAUDE_PROJECT_DIR="$WORK/proj with space"   # regression: roots with spaces
mkdir -p "$CLAUDE_PROJECT_DIR/.pave/demo-workflow"
unset PAVE_INIT_PYTHON PAVE_INIT_STALE_SECONDS PAVE_INIT_STOP_EVERY 2>/dev/null || true

STATE="$CLAUDE_PROJECT_DIR/.pave/demo-workflow/run-state.json"
RUN_MARKER="$CLAUDE_PROJECT_DIR/.pave-init-run"

report() { # name ok detail
  if [ "$2" = "1" ]; then
    PASS=$((PASS + 1)); echo "PASS  $1"
  else
    FAIL=$((FAIL + 1)); echo "FAIL  $1  ($3)"
  fi
}

write_state() { # $1 = terminal status ("" for active), $2 = complete|minimal
  python3 - "$STATE" "$1" "$2" <<'PY'
import json, sys
path, status, shape = sys.argv[1], sys.argv[2], sys.argv[3]
state = {
    "run_identity": {"run_id": "test-run"},
    "target_system": "demo system",
    "planning_workspace": ".pave/demo-workflow",
    "generated_skill_name": "demo-workflow",
    "generated_skill_output": None,
    "requirements_status": "approved",
    "fitness_verdict": "fit",
    "fitness_override": None,
    "exploration_lenses": ["structure"],
    "explorer_results": [{"lens": "structure", "artifact": "exploration/structure.md"}],
    "frontier_entries": None,
    "boundary_review_results": [],
    "approval_bundle_revisions": 0,
    "plan_review_rounds": 0,
    "user_plan_approval": None,
    "build_units": None,
    "validation_results": None,
    "final_review_rounds": 0,
    "forward_test_result": None,
    "delivery_manifest_state": None,
    "terminal_classification": {"status": status} if status else None,
    "traversal_history": [{"node": "interview_system", "outcome": "requirements_ready"}],
}
if shape == "minimal":
    for key in ("requirements_status", "traversal_history"):
        state.pop(key, None)
json.dump(state, open(path, "w"))
PY
  printf '%s\n' "$STATE" > "$RUN_MARKER"
}

stop_payload() { # $1 session, $2 stop_hook_active (0/1)
  if [ "$2" = "1" ]; then
    printf '{"session_id":"%s","stop_hook_active":true}' "$1"
  else
    printf '{"session_id":"%s"}' "$1"
  fi
}

post_payload() { # $1 session, $2 agent_type ("" for lead)
  if [ -n "$2" ]; then
    printf '{"session_id":"%s","agent_type":"%s","tool_name":"Bash"}' "$1" "$2"
  else
    printf '{"session_id":"%s","tool_name":"Bash"}' "$1"
  fi
}

# --- stop_alignment_check -------------------------------------------------

write_state "" complete

ERR="$(stop_payload s1 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "Why did you stop" && ok=1
report "stop: first stop blocks with socratic questions" "$ok" "rc=$RC"

ERR="$(stop_payload s1 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: 2nd stop within cooldown passes silently" "$ok" "rc=$RC err=$ERR"

ERR="$(stop_payload s1 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: 3rd stop within cooldown passes silently" "$ok" "rc=$RC err=$ERR"

ERR="$(stop_payload s1 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "Why did you stop" && ok=1
report "stop: cooldown spent, 4th stop blocks again" "$ok" "rc=$RC"
rm -f "$TMPDIR/pave-init-stop-nudged-s1"   # reset for later tests

ERR="$(stop_payload s2 1 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: stop_hook_active short-circuits" "$ok" "rc=$RC"

write_state "accepted" complete
ERR="$(stop_payload s3 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: terminal run is silent" "$ok" "rc=$RC"

rm -f "$STATE" "$RUN_MARKER"
ERR="$(stop_payload s4 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: no run state is silent" "$ok" "rc=$RC"

write_state "" complete
rm -f "$RUN_MARKER"   # state exists but no ownership marker: scan-only
ERR="$(stop_payload s5 0 | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: scan-discovered state without marker is silent" "$ok" "rc=$RC err=$ERR"

printf '%s\n' "$STATE" > "$RUN_MARKER"
ERR="$(printf 'not json' | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: unparsable payload fails open" "$ok" "rc=$RC err=$ERR"

ERR="$(bash "$STOP_HOOK" </dev/null 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: empty stdin fails open" "$ok" "rc=$RC err=$ERR"

# --- state_staleness_reminder ---------------------------------------------

write_state "" complete
OUT="$(post_payload t1 "" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: fresh state is silent" "$ok" "rc=$RC out=$OUT"

touch -t 202001010000 "$STATE"
OUT="$(post_payload t2 "" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "additionalContext" \
  && printf '%s' "$OUT" | grep -q "not recorded" && ok=1
report "staleness: stale state fires additionalContext" "$ok" "rc=$RC out=$OUT"

OUT="$(post_payload t2 "" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: throttled within the window" "$ok" "rc=$RC out=$OUT"

OUT="$(post_payload t3 "Explore" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: subagent payload is skipped" "$ok" "rc=$RC out=$OUT"

write_state "accepted" complete
touch -t 202001010000 "$STATE"
OUT="$(post_payload t4 "" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: stale terminal run is silent" "$ok" "rc=$RC out=$OUT"

write_state "" complete
touch -t 202001010000 "$STATE"
rm -f "$RUN_MARKER"   # stale state but no ownership marker: scan-only
OUT="$(post_payload t5 "" | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: scan-discovered state without marker is silent" "$ok" "rc=$RC out=$OUT"

printf '%s\n' "$STATE" > "$RUN_MARKER"
OUT="$(printf 'not json' | bash "$STALE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "staleness: unparsable payload fails open" "$ok" "rc=$RC out=$OUT"

# --- validate_run_state.py --------------------------------------------------

write_state "" complete
OUT="$(python3 "$VALIDATOR" "$STATE" 2>&1)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "^PASS" && ok=1
report "validator: well-formed instance passes" "$ok" "rc=$RC out=$OUT"

write_state "" minimal
OUT="$(python3 "$VALIDATOR" "$STATE" 2>&1)"; RC=$?
ok=0; [ "$RC" = "1" ] && printf '%s' "$OUT" | grep -q "missing required field" && ok=1
report "validator: missing required fields fail" "$ok" "rc=$RC out=$OUT"

# --- planning-layout-warn ---------------------------------------------------
# Allowlist regression: root-contract.md is lead-owned planning state
# (references/planning-layout.md). It was missing from the allowlist, so
# every correct write to it warned -- 10 of 14 firings in a real run.

write_state "" complete
printf '%s\n' "$STATE" > "$RUN_MARKER"
PLANNING="$(dirname "$STATE")/planning"
mkdir -p "$PLANNING"

layout_payload() { # $1 = filename, $2 = agent_type ("" for lead), $3 = content
  python3 - "$PLANNING/$1" "$2" "${3:-}" <<'PY'
import json, sys
path, agent, content = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}
if agent:
    payload["agent_type"] = agent
print(json.dumps(payload))
PY
}

OUT="$(layout_payload root-contract.md "" | bash "$LAYOUT_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "layout: lead writing root-contract.md is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(layout_payload frontier.yaml "" | bash "$LAYOUT_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "layout: lead writing frontier.yaml is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(layout_payload assembly-checklist.md "" | bash "$LAYOUT_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "matches no allowed planning/ pattern" && ok=1
report "layout: an undeclared planning/ path warns" "$ok" "rc=$RC out=$OUT"

OUT="$(layout_payload root-contract.md node-planner | bash "$LAYOUT_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "a subagent wrote planning/root-contract.md" && ok=1
report "layout: a subagent writing root-contract.md warns" "$ok" "rc=$RC out=$OUT"

write_state abandoned complete
OUT="$(layout_payload assembly-checklist.md "" | bash "$LAYOUT_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "layout: terminal run is silent" "$ok" "rc=$RC out=$OUT"

# --- write_for_reader --------------------------------------------------------
# The write-for-the-reader duty (references/pave-spec.md section 8.5): remind
# on reader-facing run-workspace document writes by any actor, throttled to
# the 1st write and every Nth after; exempt working-state directories and
# non-markdown writes stay silent.

write_state "" complete
WORKSPACE="$(dirname "$STATE")"

reader_payload() { # $1 = workspace-relative path, $2 = session, $3 = agent_type ("" for lead)
  python3 - "$WORKSPACE/$1" "$2" "$3" <<'PY'
import json, sys
path, session, agent = sys.argv[1], sys.argv[2], sys.argv[3]
payload = {"session_id": session, "tool_name": "Write",
           "tool_input": {"file_path": path, "content": "x"}}
if agent:
    payload["agent_type"] = agent
print(json.dumps(payload))
PY
}

OUT="$(reader_payload requirements.md r1 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "plain english" && ok=1
report "reader: first workspace .md write reminds" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload system-map.md r1 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: 2nd write in the window is throttled" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload traceability.md r1 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: 3rd write in the window is throttled" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload requirements.md r1 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "plain english" && ok=1
report "reader: throttle spent, 4th write reminds again" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload system-map.md r2 node-planner | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "plain english" && ok=1
report "reader: a subagent's write reminds too" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload planning/scratch.md r3 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: exempt planning/ write is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload exploration/lens.md r3 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: exempt exploration/ write is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload notes.txt r3 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: non-markdown write is silent" "$ok" "rc=$RC out=$OUT"

write_state abandoned complete
OUT="$(reader_payload requirements.md r5 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: terminal run is silent" "$ok" "rc=$RC out=$OUT"
write_state "" complete

rm -f "$RUN_MARKER"
OUT="$(reader_payload requirements.md r4 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: scan-discovered state without marker is silent" "$ok" "rc=$RC out=$OUT"

# Document budget (references/pave-spec.md section 8.4): a document over its
# cap is named with its size, once per session and file, past the throttle.
write_state "" complete
OUT="$(reader_payload requirements.md r6 "" | bash "$READER_HOOK" 2>/dev/null)"   # 1st write: consumes the window
python3 -c 'import sys; open(sys.argv[1], "w").write("line\n" * 450)' "$WORKSPACE/design-plan.md"
OUT="$(reader_payload design-plan.md r6 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "450 lines" \
  && printf '%s' "$OUT" | grep -q "over its cap" && ok=1
report "reader: over-cap document is named past the throttle" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload design-plan.md r6 "" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: over-cap warning fires once per session and file" "$ok" "rc=$RC out=$OUT"

OUT="$(reader_payload design-plan.md r7 "" | PAVE_INIT_CAP_LINES=1000 bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && printf '%s' "$OUT" | grep -q "plain english" \
  && ! printf '%s' "$OUT" | grep -q "over its cap" && ok=1
report "reader: cap follows PAVE_INIT_CAP_LINES" "$ok" "rc=$RC out=$OUT"
rm -f "$WORKSPACE/design-plan.md"

# --- registration ------------------------------------------------------------
# A skill-frontmatter hook fires only for the agent that invoked the skill
# (measured on Claude Code 2.1.258), so the two hooks that watch subagent
# writes must be registered in the plugin's hooks/hooks.json, and the
# frontmatter must keep only the lead-only pair.
PLUGIN_ROOT="$(cd "$SKILL/../.." && pwd)"
REG="$(python3 - "$PLUGIN_ROOT" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
try:
    data = json.load(open(root / "hooks" / "hooks.json"))
    cmds = [h["command"] for groups in data["hooks"].values() for g in groups for h in g["hooks"]]
except Exception:
    cmds = []
plugin_ok = any("planning-layout-warn.sh" in c for c in cmds) and any("write_for_reader.sh" in c for c in cmds)
fm = (root / "skills" / "pave-init" / "SKILL.md").read_text(encoding="utf-8").split("\n---", 1)[0]
fm_ok = ("planning-layout-warn.sh" not in fm and "write_for_reader.sh" not in fm
         and "stop_alignment_check.sh" in fm and "state_staleness_reminder.sh" in fm)
print(int(plugin_ok), int(fm_ok))
PY
)"
ok=0; [ "${REG%% *}" = "1" ] && ok=1
report "registration: subagent-facing hooks are in plugin hooks.json" "$ok" "reg=$REG"
ok=0; [ "${REG##* }" = "1" ] && ok=1
report "registration: frontmatter keeps only the lead-only pair" "$ok" "reg=$REG"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
