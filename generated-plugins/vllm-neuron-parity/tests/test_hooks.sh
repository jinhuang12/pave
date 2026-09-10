#!/usr/bin/env bash
# Invariant tests for three vllm-neuron-parity hooks:
#   - skills/vllm-neuron-parity/hooks/write-for-reader.sh
#   - skills/vllm-neuron-parity/hooks/graph_edit_guard.sh
#   - skills/vllm-neuron-parity/hooks/goal-restate.sh
#
# What is tested for write-for-reader (behavior, not wording):
#   - a markdown write under the run workspace (parent of the run-state
#     directory) reminds; the workspace is artifacts/, not artifacts/run/
#   - throttle: 1st write reminds, 2nd and 3rd stay silent, 4th reminds;
#     a different session reminds at once
#   - exempt working-state components (attempts, index, ...) stay silent;
#     a campaign NAMED like one (campaigns/index/...) is not exempt
#   - run/backlog/ is NOT exempt and reminds
#   - every reminder is exactly one PostToolUse hookSpecificOutput JSON object
#   - non-markdown writes, missing marker, terminal runs, and writes outside
#     the workspace stay silent
#   - garbage stdin exits 0 with empty stdout
#   - Edit payloads (file_path + old_string/new_string) count like Write
#   - increments/ is not exempt: .py/.sh/.md/.txt writes there get the
#     increments sentence (cap, edit in place, delete superseded); a lap
#     suffix, over-cap file, or over-cap header bypasses the throttle once
#     per file; .out there and .py outside increments/ stay silent
#
# What is tested for graph_edit_guard: it denies (exit 2) a direct Edit or
# Write of a live *.pave.yaml or of revisions.yaml only when a revisions.yaml
# sits beside the target and no .landing marker does; it fails open on every
# input it cannot read; it has no subagent exemption; and hooks/hooks.json
# registers it, so subagent edits are seen too.
#
# What is tested for goal-restate: SessionStart resume and compact ask, startup
# is silent; SubagentStart asks about the brief; the active campaign's
# DECISIONS.md is named when present; a sidecar naming another session
# silences it while a lead-spawned seat is asked; terminal runs, a missing
# marker, unrelated events, and garbage stdin are silent; hooks/hooks.json
# registers both events.
#
# Self-contained: everything runs inside a mktemp sandbox with its own
# TMPDIR, so throttle counters start clean on every run. No real run state,
# marker, counter, or evolution root is touched. Exits 1 on any failure.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN="$(cd "$HERE/.." && pwd)"
READER_HOOK="$PLUGIN/skills/vllm-neuron-parity/hooks/write-for-reader.sh"
GUARD_HOOK="$PLUGIN/skills/vllm-neuron-parity/hooks/graph_edit_guard.sh"

PASS=0
FAIL=0

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export TMPDIR="$WORK/tmp"
mkdir -p "$TMPDIR"
export CLAUDE_PROJECT_DIR="$WORK/proj with space"   # regression: roots with spaces
unset CODEX_PROJECT_DIR VLLM_NEURON_PARITY_PYTHON VLLM_NEURON_PARITY_READER_EVERY 2>/dev/null || true

ROOT="$CLAUDE_PROJECT_DIR"
ARTIFACTS="$ROOT/artifacts"
STATE="$ARTIFACTS/run/run-state.json"
MARKER="$ROOT/.vllm-neuron-parity-run"
mkdir -p "$ARTIFACTS/run"

report() { # name ok detail
  if [ "$2" = "1" ]; then
    PASS=$((PASS + 1)); echo "PASS  $1"
  else
    FAIL=$((FAIL + 1)); echo "FAIL  $1  ($3)"
  fi
}

write_state() { # $1 = terminal status ("" for active)
  python3 - "$STATE" "$1" <<'PY'
import json, sys
path, status = sys.argv[1], sys.argv[2]
state = {
    "workflow_identity": {"run_id": "test-run"},
    "active_node_runs": [{"node": "scan_upstream_delta", "campaign": "run"}],
    "completed_outcomes": [],
    "terminal_classification": None,
}
if status:
    state["terminal_classification"] = {"status": status}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY
}

write_marker() {
  printf '%s\n' "$STATE" > "$MARKER"
}

payload() { # $1 = absolute target path, $2 = session, $3 = tool (Write|Edit), $4 = cwd ("" to omit)
  python3 - "$1" "$2" "${3:-Write}" "${4:-}" <<'PY'
import json, sys
path, session, tool, cwd = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
if tool == "Edit":
    tool_input = {"file_path": path, "old_string": "a", "new_string": "b"}
else:
    tool_input = {"file_path": path, "content": "x"}
payload = {"session_id": session, "tool_name": tool, "tool_input": tool_input}
if cwd:
    payload["cwd"] = cwd
print(json.dumps(payload))
PY
}

run_hook() { # $1 = target, $2 = session, $3 = tool, $4 = cwd
  payload "$1" "$2" "${3:-Write}" "${4:-}" | bash "$READER_HOOK" 2>/dev/null
}

reminds() { # $1 = hook stdout; true when it is exactly one PostToolUse hookSpecificOutput carrying the duty
  printf '%s' "$1" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
assert set(doc) == {"hookSpecificOutput"}, doc
hook = doc["hookSpecificOutput"]
assert hook["hookEventName"] == "PostToolUse", hook
assert "plain english" in hook["additionalContext"], hook
' 2>/dev/null
}

write_state ""
write_marker

DESIGN="$ARTIFACTS/campaigns/c1/design/record.md"

# 1. first .md write under campaigns/ reminds (workspace is artifacts/, not artifacts/run/)
OUT="$(run_hook "$DESIGN" s1)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: first campaigns/ .md write reminds" "$ok" "rc=$RC out=$OUT"

# 2. 2nd and 3rd writes in the same session are silent
OUT="$(run_hook "$DESIGN" s1)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: 2nd write same session is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(run_hook "$DESIGN" s1)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: 3rd write same session is silent" "$ok" "rc=$RC out=$OUT"

# 3. 4th write reminds again
OUT="$(run_hook "$DESIGN" s1)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: 4th write same session reminds" "$ok" "rc=$RC out=$OUT"

# 4. a different session reminds at once
OUT="$(run_hook "$DESIGN" s2)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: different session reminds immediately" "$ok" "rc=$RC out=$OUT"

# 5. exempt: attempts/
OUT="$(run_hook "$ARTIFACTS/campaigns/c1/attempts/a.md" s3)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: campaigns/*/attempts/ write is silent" "$ok" "rc=$RC out=$OUT"

# 6. exempt: index/ deep under run/delta/
OUT="$(run_hook "$ARTIFACTS/run/delta/index/current/i.md" s3)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: run/delta/index/ write is silent" "$ok" "rc=$RC out=$OUT"

# 7. run/backlog/ is not exempt: reminds (s3 has no counted writes yet)
OUT="$(run_hook "$ARTIFACTS/run/backlog/b.md" s3)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: run/backlog/ write reminds" "$ok" "rc=$RC out=$OUT"

# 8. non-markdown is silent
OUT="$(run_hook "$ARTIFACTS/campaigns/c1/design/record.yaml" s4)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: .yaml write is silent" "$ok" "rc=$RC out=$OUT"

# 9. no marker => silent
rm -f "$MARKER"
OUT="$(run_hook "$DESIGN" s5)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: no marker is silent" "$ok" "rc=$RC out=$OUT"
write_marker

# 10. terminal run => silent
write_state accepted
OUT="$(run_hook "$DESIGN" s6)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: terminal_classification.status set is silent" "$ok" "rc=$RC out=$OUT"
write_state ""

# 11. outside artifacts/ => silent
OUT="$(run_hook "$ROOT/README.md" s7)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: <root>/README.md outside workspace is silent" "$ok" "rc=$RC out=$OUT"

# 12. garbage stdin => exit 0, empty stdout
OUT="$(printf 'not json at all' | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: garbage stdin exits 0 with empty stdout" "$ok" "rc=$RC out=$OUT"

# 13. Edit payload reminds
OUT="$(run_hook "$DESIGN" s8 Edit)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: Edit payload reminds" "$ok" "rc=$RC out=$OUT"

# 14. marker found via payload cwd when no project-dir env is set and PWD has no marker
SAVED_PROJECT_DIR="$CLAUDE_PROJECT_DIR"
unset CLAUDE_PROJECT_DIR
OUT="$(cd "$WORK" && payload "$DESIGN" s9 Write "$ROOT" | bash "$READER_HOOK" 2>/dev/null)"; RC=$?
export CLAUDE_PROJECT_DIR="$SAVED_PROJECT_DIR"
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: marker via payload cwd reminds" "$ok" "rc=$RC out=$OUT"

# 15. a campaign named like an exempt component is not exempt: the campaign-name position is never tested
OUT="$(run_hook "$ARTIFACTS/campaigns/index/design/record.md" s10)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ok=1
report "reader: campaigns/index/design/ write reminds (campaign name not tested)" "$ok" "rc=$RC out=$OUT"

# 16-18. cap notice (references/artifact-layout.md section 4.12): an over-cap
# document is named with its size once per session and file, past the throttle
PLAN="$ARTIFACTS/campaigns/c1/design/current/increment-plan.md"
mkdir -p "$(dirname "$PLAN")"
python3 -c 'import sys; open(sys.argv[1], "w").write("line\n" * 450)' "$PLAN"
OUT="$(run_hook "$DESIGN" s11)"   # 1st write of the session: consumes the window
OUT="$(run_hook "$PLAN" s11)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && printf '%s' "$OUT" | grep -q "450 lines" \
  && printf '%s' "$OUT" | grep -q "over its cap" && ok=1
report "reader: over-cap document is named past the throttle" "$ok" "rc=$RC out=$OUT"

OUT="$(run_hook "$PLAN" s11)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: over-cap notice fires once per session and file" "$ok" "rc=$RC out=$OUT"

OUT="$(payload "$PLAN" s12 | VLLM_NEURON_PARITY_CAP_LINES=1000 bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && reminds "$OUT" && ! printf '%s' "$OUT" | grep -q "over its cap" && ok=1
report "reader: cap follows VLLM_NEURON_PARITY_CAP_LINES" "$ok" "rc=$RC out=$OUT"
rm -f "$PLAN"

# 19-25. increments/ (references/artifact-layout.md section 4.12, parity 1.5.4):
# .py/.sh/.md/.txt writes there get the increments sentence; a lap suffix, an
# over-cap file, or an over-cap header bypasses the throttle once per file;
# world-produced output (.out) and non-.md writes elsewhere stay silent.
INC="$ARTIFACTS/campaigns/c1/increments"
mkdir -p "$INC"
incr_reminds() { # $1 = hook stdout, $2 = substring the sentence must carry
  printf '%s' "$1" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
assert set(doc) == {"hookSpecificOutput"}, doc
hook = doc["hookSpecificOutput"]
assert hook["hookEventName"] == "PostToolUse", hook
assert "increments" in hook["additionalContext"] and sys.argv[1] in hook["additionalContext"], hook
' "$2" 2>/dev/null
}

printf '#!/bin/bash\n# one line\necho hi\n' > "$INC/accept-001-host.sh"
OUT="$(run_hook "$INC/accept-001-host.sh" s13)"; RC=$?
ok=0; [ "$RC" = "0" ] && incr_reminds "$OUT" "edited in place" && ! printf '%s' "$OUT" | grep -q "lap suffix (-rN)" && ok=1
report "reader: increments/ .sh write gets the increments sentence" "$ok" "rc=$RC out=$OUT"

OUT="$(run_hook "$INC/accept-001-host.sh" s13)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: 2nd clean increments/ write rides the throttle" "$ok" "rc=$RC out=$OUT"

printf '#!/bin/bash\necho hi\n' > "$INC/accept-001-host-r3.sh"
OUT="$(run_hook "$INC/accept-001-host-r3.sh" s13)"; RC=$?
ok=0; [ "$RC" = "0" ] && incr_reminds "$OUT" "lap suffix (-rN)" && ok=1
report "reader: a lap-suffixed increments/ name is named past the throttle" "$ok" "rc=$RC out=$OUT"

python3 -c 'import sys; open(sys.argv[1], "w").write("#!/usr/bin/env python3\n" + "# why this exists\n" * 30 + "print(1)\n")' "$INC/build-002.py"
OUT="$(run_hook "$INC/build-002.py" s13)"; RC=$?
ok=0; [ "$RC" = "0" ] && incr_reminds "$OUT" "31 lines against a cap of 20" && ok=1
report "reader: a 31-line increments/ header is named past the throttle" "$ok" "rc=$RC out=$OUT"

python3 -c 'import sys; open(sys.argv[1], "w").write("row\n" * 250)' "$INC/evidence-003.md"
OUT="$(run_hook "$INC/evidence-003.md" s13)"; RC=$?
ok=0; [ "$RC" = "0" ] && incr_reminds "$OUT" "over the 200-line cap" && ! printf '%s' "$OUT" | grep -q "plain english" && ok=1
report "reader: a 250-line increments/ record is over cap, no prose duty" "$ok" "rc=$RC out=$OUT"

OUT="$(payload "$INC/evidence-003.md" s14 | VLLM_NEURON_PARITY_INCR_CAP_LINES=300 bash "$READER_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && incr_reminds "$OUT" "300 lines" && ! printf '%s' "$OUT" | grep -q "over the" && ok=1
report "reader: increments cap follows VLLM_NEURON_PARITY_INCR_CAP_LINES" "$ok" "rc=$RC out=$OUT"

printf 'transcript\n' > "$INC/accept-001-host.out"
OUT="$(run_hook "$INC/accept-001-host.out" s15)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: increments/ .out write is silent" "$ok" "rc=$RC out=$OUT"

OUT="$(run_hook "$ARTIFACTS/campaigns/c1/design/helper.py" s15)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "reader: .py write outside increments/ is silent" "$ok" "rc=$RC out=$OUT"
rm -rf "$INC"

# --- graph_edit_guard --------------------------------------------------------
# The live canonical graph is landed by record_revision.py from a reviewed
# patch, never edited directly. The guard is path-only: a revisions.yaml beside
# the target marks the directory an evolution root, and a .landing marker means
# the landing tool owns the graph right now. No identity exemption -- the actor
# a prohibition has to survive is the one that never read the lead skill.

EVO="$ROOT/evo root"   # regression: roots with spaces
mkdir -p "$EVO" "$WORK/no-ledger"
: > "$EVO/revisions.yaml"
: > "$EVO/workflow.pave.yaml"
: > "$EVO/child.pave.yaml"
: > "$EVO/README.md"
: > "$WORK/no-ledger/workflow.pave.yaml"

guard_payload() { # $1 = file_path, $2 = tool (Write|Edit), $3 = agent_id ("" for lead)
  python3 - "$1" "$2" "${3:-}" <<'PY'
import json, sys
path, tool, agent = sys.argv[1], sys.argv[2], sys.argv[3]
edit = {"file_path": path}
if tool == "Write":
    edit["content"] = "x"
else:
    edit["old_string"], edit["new_string"] = "a", "b"
payload = {"session_id": "g1", "tool_name": tool, "tool_input": edit}
if agent:
    payload["agent_id"] = agent
    payload["agent_type"] = "vllm-neuron-parity-campaign-implementer"
print(json.dumps(payload))
PY
}

run_guard() { # $1 = file_path, $2 = tool, $3 = agent_id; prints stderr, returns the hook's rc
  guard_payload "$1" "$2" "${3:-}" | bash "$GUARD_HOOK" 2>&1 >/dev/null
}

ERR="$(run_guard "$EVO/workflow.pave.yaml" Edit)"; RC=$?
ok=0; [ "$RC" = "2" ] && [ -n "$ERR" ] \
  && printf '%s' "$ERR" | grep -q "record_revision.py land" && ok=1
report "guard: editing the live graph in an evolution root is denied" "$ok" "rc=$RC err=$ERR"

: > "$EVO/.landing"
ERR="$(run_guard "$EVO/workflow.pave.yaml" Edit)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: a landing in progress passes" "$ok" "rc=$RC err=$ERR"
rm -f "$EVO/.landing"

ERR="$(run_guard "$EVO/child.pave.yaml" Write)"; RC=$?
ok=0; [ "$RC" = "2" ] && [ -n "$ERR" ] && ok=1
report "guard: a child graph beside the ledger is guarded too" "$ok" "rc=$RC err=$ERR"

ERR="$(run_guard "$WORK/no-ledger/workflow.pave.yaml" Edit)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: a .pave.yaml with no ledger beside it passes" "$ok" "rc=$RC err=$ERR"

ERR="$(run_guard "$EVO/README.md" Write)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: a non-graph path in the root passes" "$ok" "rc=$RC err=$ERR"

ERR="$(run_guard "$EVO/revisions.yaml" Edit)"; RC=$?
ok=0; [ "$RC" = "2" ] && [ -n "$ERR" ] && ok=1
report "guard: editing the ledger itself is denied" "$ok" "rc=$RC err=$ERR"

: > "$EVO/.landing"
ERR="$(run_guard "$EVO/revisions.yaml" Edit)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: the ledger under a landing in progress passes" "$ok" "rc=$RC err=$ERR"
rm -f "$EVO/.landing"

ERR="$(run_guard "$WORK/no-ledger/revisions.yaml" Write)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: creating a ledger where none exists passes" "$ok" "rc=$RC err=$ERR"

ERR="$(printf '{"tool_name":"Edit","tool_input":{}}' | bash "$GUARD_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: a payload without file_path passes" "$ok" "rc=$RC err=$ERR"

ERR="$(run_guard "$EVO/workflow.pave.yaml" Edit sub-1)"; RC=$?
ok=0; [ "$RC" = "2" ] && [ -n "$ERR" ] && ok=1
report "guard: a subagent editing the live graph is denied too" "$ok" "rc=$RC err=$ERR"

ERR="$(printf 'not json' | bash "$GUARD_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "guard: unparsable payload fails open" "$ok" "rc=$RC err=$ERR"

# The guard must sit in hooks/hooks.json, not skill frontmatter: a
# frontmatter hook fires only for the agent that invoked the skill.
python3 - "$PLUGIN" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
hooks = json.loads((Path(sys.argv[1]) / "hooks" / "hooks.json").read_text())
entries = [e for e in hooks["hooks"]["PreToolUse"] if e.get("matcher") == "Edit|Write|MultiEdit"]
commands = [h["command"] for e in entries for h in e["hooks"]]
assert any("graph_edit_guard.sh" in c for c in commands), commands
PY
ok=0; [ "$?" = "0" ] && ok=1
report "guard: registered in hooks/hooks.json under PreToolUse Edit|Write|MultiEdit" "$ok" "see hooks/hooks.json"

# --- goal-restate ------------------------------------------------------------
# Asks the lead (SessionStart resume|compact) and every seat (SubagentStart) to
# state the goal from the record and the fewest steps before acting. Advisory
# only; marker- and lead-session-gated; silent on terminal runs.

RESTATE_HOOK="$PLUGIN/skills/vllm-neuron-parity/hooks/goal-restate.sh"
write_state ""
write_marker
rm -f "$STATE.lead-session"

restate_payload() { # $1 = hook_event_name, $2 = source ("" to omit), $3 = session, $4 = agent_id ("" for lead)
  python3 - "$1" "${2:-}" "${3:-r1}" "${4:-}" <<'PY'
import json, sys
event, source, session, agent = sys.argv[1:5]
payload = {"hook_event_name": event, "session_id": session, "cwd": "/tmp"}
if source:
    payload["source"] = source
if agent:
    payload["agent_id"] = agent
    payload["agent_type"] = "vllm-neuron-parity:investigator"
print(json.dumps(payload))
PY
}

restates() { # $1 = hook stdout, $2 = expected hookEventName, $3 = substring the text must carry
  printf '%s' "$1" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
assert set(doc) == {"hookSpecificOutput"}, doc
hook = doc["hookSpecificOutput"]
assert hook["hookEventName"] == sys.argv[1], hook
assert "goal" in hook["additionalContext"] and sys.argv[2] in hook["additionalContext"], hook
' "$2" "$3" 2>/dev/null
}

# R1. resume with a marker and no sidecar names run state
OUT="$(restate_payload SessionStart resume | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && restates "$OUT" SessionStart "$STATE" && ok=1
report "restate: SessionStart resume asks for the goal from run state" "$ok" "rc=$RC out=$OUT"

# R2. compact fires too
OUT="$(restate_payload SessionStart compact | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && restates "$OUT" SessionStart "compact" && ok=1
report "restate: SessionStart compact asks" "$ok" "rc=$RC out=$OUT"

# R3. startup is silent (no goal exists yet)
OUT="$(restate_payload SessionStart startup | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: SessionStart startup is silent" "$ok" "rc=$RC out=$OUT"

# R4. SubagentStart asks the seat about its brief
OUT="$(restate_payload SubagentStart "" r1 seat-1 | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && restates "$OUT" SubagentStart "brief" && ok=1
report "restate: SubagentStart asks the seat for its brief's goal" "$ok" "rc=$RC out=$OUT"

# R5. the active campaign's DECISIONS.md is named when it exists (active_node_runs[].campaign)
mkdir -p "$ARTIFACTS/campaigns/run/approvals"
: > "$ARTIFACTS/campaigns/run/approvals/DECISIONS.md"
OUT="$(restate_payload SessionStart resume | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && restates "$OUT" SessionStart "campaigns/run/approvals/DECISIONS.md" && ok=1
report "restate: names the active campaign's DECISIONS.md" "$ok" "rc=$RC out=$OUT"
rm -rf "$ARTIFACTS/campaigns/run"

# R6. sidecar naming another session => silent; the lead's own session => asks
printf 'lead-session\n' > "$STATE.lead-session"
OUT="$(restate_payload SessionStart resume "" other | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: another session is silent when the sidecar names the lead" "$ok" "rc=$RC out=$OUT"
OUT="$(restate_payload SubagentStart "" lead-session seat-2 | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && restates "$OUT" SubagentStart "brief" && ok=1
report "restate: a lead-spawned seat (lead session id) is asked" "$ok" "rc=$RC out=$OUT"
rm -f "$STATE.lead-session"

# R7. terminal run => silent
write_state accepted
OUT="$(restate_payload SessionStart resume | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: terminal run is silent" "$ok" "rc=$RC out=$OUT"
write_state ""

# R8. no marker => silent
rm -f "$MARKER"
OUT="$(restate_payload SessionStart resume | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: no marker is silent" "$ok" "rc=$RC out=$OUT"
write_marker

# R9. an unrelated event => silent
OUT="$(restate_payload PostToolUse | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: unrelated event is silent" "$ok" "rc=$RC out=$OUT"

# R10. garbage stdin => exit 0, empty stdout
OUT="$(printf 'not json' | bash "$RESTATE_HOOK" 2>/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$OUT" ] && ok=1
report "restate: unparsable payload fails open" "$ok" "rc=$RC out=$OUT"

# R11. registered at plugin level for both events
python3 - "$PLUGIN" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
hooks = json.loads((Path(sys.argv[1]) / "hooks" / "hooks.json").read_text())["hooks"]
ss = [h["command"] for e in hooks["SessionStart"] if e.get("matcher") == "resume|compact" for h in e["hooks"]]
sa = [h["command"] for e in hooks["SubagentStart"] for h in e["hooks"]]
assert any("goal-restate.sh" in c for c in ss), ss
assert any("goal-restate.sh" in c for c in sa), sa
PY
ok=0; [ "$?" = "0" ] && ok=1
report "restate: registered in hooks/hooks.json (SessionStart resume|compact, SubagentStart)" "$ok" "see hooks/hooks.json"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ] || exit 1
exit 0
