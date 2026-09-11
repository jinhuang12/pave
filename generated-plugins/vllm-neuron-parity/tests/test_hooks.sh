#!/usr/bin/env bash
# Invariant tests for four vllm-neuron-parity hooks:
#   - skills/vllm-neuron-parity/hooks/write-for-reader.sh
#   - skills/vllm-neuron-parity/hooks/graph_edit_guard.sh
#   - skills/vllm-neuron-parity/hooks/goal-restate.sh
#   - skills/vllm-neuron-parity/hooks/stop-guard.sh
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
# What is tested for stop-guard: the block text names every active seat with
# the mandatory reply form and carries no "lgtm"; the audit branch fires DUE at
# the outcome threshold (declared nodes only; node: lead never counts) or at
# the write-log bytes threshold, writes the checkpoint sidecar, and its brief
# carries the dispatch line; OPEN passes; a ledger entry newer than the
# checkpoint with drafted_by: workflow-updater closes the cycle and resets the
# counters; a pin entry never closes; a no-change findings record closes only
# with review: PASS when the trend rose; a stalled OPEN cycle is named unless
# the newest proposal awaits the user's approval; a graph landed since the
# run's pin is named with the rule-1 route unless move_declined stands; no
# evolution root means no audit text and no sidecar; a pave-init older than
# 2.6.0 gets one degraded line; the cooldown still passes the next two stops;
# no marker, a subagent, stop_hook_active, and a terminal run pass; a firing
# exits within 5 s.
#
# Self-contained: everything runs inside a mktemp sandbox with its own
# TMPDIR and HOME, so throttle counters and the pave-init version lookup start
# clean on every run. No real run state, marker, counter, or evolution root is
# touched. Exits 1 on any failure.

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

# --- stop-guard ------------------------------------------------------------
# Lead-only Stop hook: blocks at most one stop in STOP_EVERY with the seat
# lines and the imperatives; its audit branch shares the cooldown and speaks
# only when an evolution root exists. HOME points into the sandbox so the
# pave-init version check reads a fake plugin cache, never the real one.

STOP_HOOK="$PLUGIN/skills/vllm-neuron-parity/hooks/stop-guard.sh"
export HOME="$WORK/home"
CACHE="$HOME/.claude/plugins/cache/jinhuang12-plugins/pave-init"
mkdir -p "$CACHE/2.6.0/skills/pave-init"
printf 'version: 2.6.0\n\n## changelog\n' > "$CACHE/2.6.0/skills/pave-init/VERSION"
EVO_ROOT="$ROOT/.vllm-neuron-parity/evolution"
SIDECAR="$STATE.audit-checkpoint.json"
unset VLLM_NEURON_PARITY_AUDIT_EVERY VLLM_NEURON_PARITY_AUDIT_BYTES VLLM_NEURON_PARITY_AUDIT_STALL VLLM_NEURON_PARITY_STOP_EVERY 2>/dev/null || true
rm -f "$STATE.lead-session" "$SIDECAR" "$STATE".audit-census-* "$STATE".write-log*.jsonl
rm -rf "$EVO_ROOT"

stop_state() { # $1 = declared outcomes count, $2 = terminal status ("" for active)
  python3 - "$STATE" "$1" "${2:-}" <<'PY'
import json, sys
path, count, status = sys.argv[1], int(sys.argv[2]), sys.argv[3]
state = {
    "workflow_identity": {"run_id": "test-run"},
    "active_node_runs": [
        {"node": "scan_upstream_delta", "campaign": "run", "seat": "lane-alpha", "started": "2026-09-10T18:00:00Z"},
        {"node": "cost_targets", "seat": "lane-beta"},
    ],
    "completed_outcomes": [{"node": "scan_upstream_delta", "outcome": "delta_ready"}] * count
                          + [{"node": "lead", "outcome": "pseudo"}] * 7,   # never counts
    "terminal_classification": None,
}
if status:
    state["terminal_classification"] = {"status": status}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY
}

write_evo() { # a minimal evolution root: nodes mapping + edges list, empty-ish ledger
  mkdir -p "$EVO_ROOT/proposals"
  cat > "$EVO_ROOT/workflow.pave.yaml" <<'YAML'
pave:
  nodes:
    scan_upstream_delta:
      intent: execute
    cost_targets:
      intent: execute
  edges:
  - id: delta_to_costing
    from: scan_upstream_delta.delta_ready
YAML
  cat > "$EVO_ROOT/revisions.yaml" <<'YAML'
entries:
- revision: 0
  kind: graph
  landed_at: '2026-09-03T19:12:09Z'
  drafted_by: null
YAML
}

write_sidecar() { # $1 = state, $2 = outcomes_at, $3 = at, [$4 = findings_record path], [$5 = review], [$6 = stamped proposal path]
  python3 - "$SIDECAR" "$1" "$2" "$3" "${4:-}" "${5:-}" "${6:-}" <<'PY'
import json, os, sys
path, state, outcomes_at, at, findings, review, stamp = sys.argv[1:8]
stamps = []
if stamp:
    st = os.stat(stamp)
    stamps.append({"path": stamp, "size": st.st_size, "mtime": st.st_mtime})
fstat = None
if findings and os.path.exists(findings):
    fs = os.stat(findings)
    fstat = {"size": fs.st_size, "mtime": fs.st_mtime}
doc = {"checkpoint_id": "cp-20260910T000000Z", "at": at, "outcomes_at": int(outcomes_at),
       "bytes_at": 0, "state": state, "findings_record": findings or None, "findings_stat": fstat,
       "stamped_proposals": stamps, "review": review or None, "closed_by": None}
json.dump(doc, open(path, "w"), indent=1)
PY
}

sidecar_field() { # $1 = field
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]))' "$SIDECAR" "$1" 2>/dev/null
}

stop_payload() { # $1 = session, $2 = agent_id ("" for lead), $3 = stop_hook_active (1|"")
  python3 - "$1" "${2:-}" "${3:-}" <<'PY'
import json, sys
session, agent, active = sys.argv[1:4]
payload = {"hook_event_name": "Stop", "session_id": session, "stop_hook_active": bool(active)}
if agent:
    payload["agent_id"] = agent
    payload["agent_type"] = "vllm-neuron-parity:investigator"
print(json.dumps(payload))
PY
}

run_stop() { # $1 = session, $2 = agent_id, $3 = stop_hook_active; prints stderr, returns rc
  stop_payload "$1" "${2:-}" "${3:-}" | bash "$STOP_HOOK" 2>&1 >/dev/null
}

DISPATCH="Forward this block verbatim to pave-init:workflow-updater in audit mode (checkpoint id, census path, evolution root, pave-init root); add nothing you compose."

# S1. the hook text carries no "lgtm" (case-insensitive)
ok=0; ! grep -qi "lgtm" "$STOP_HOOK" && ok=1
report "stop: no lgtm anywhere in the hook" "$ok" "$(grep -ni lgtm "$STOP_HOOK" | head -3)"

# S2. no evolution root: the standard block, per-seat lines, no audit text, no sidecar
stop_state 100
write_marker
ERR="$(run_stop st1)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "lane-alpha on scan_upstream_delta\[run\]" \
  && printf '%s' "$ERR" | grep -q "lane-beta on cost_targets" \
  && printf '%s' "$ERR" | grep -q "waits on <grant | frozen value | design change | seat working | nothing>; next act:" \
  && printf '%s' "$ERR" | grep -q '"nothing" means act before you stop' && ok=1
report "stop: block names every active seat with the mandatory reply form" "$ok" "rc=$RC err=$ERR"
ok=0; ! printf '%s' "$ERR" | grep -qi "lgtm" && ! printf '%s' "$ERR" | grep -q "AUDIT" && [ ! -f "$SIDECAR" ] && ok=1
report "stop: silent audit without an evolution root (no AUDIT text, no sidecar)" "$ok" "err=$ERR"
ok=0; printf '%s' "$ERR" | grep -q "Retire every idle seat now" \
  && printf '%s' "$ERR" | grep -q "single writer (P10)" \
  && printf '%s' "$ERR" | grep -q "declared edge (P12)" \
  && printf '%s' "$ERR" | grep -q "batch review" \
  && printf '%s' "$ERR" | grep -q "plain words" && ok=1
report "stop: reminders are present as imperatives" "$ok" "err=$ERR"

# S3. cooldown: the next two stops pass, the fourth fires again
ERR="$(run_stop st1)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: 2nd stop in the session passes (cooldown)" "$ok" "rc=$RC err=$ERR"
ERR="$(run_stop st1)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: 3rd stop passes" "$ok" "rc=$RC err=$ERR"
ERR="$(run_stop st1)"; RC=$?
ok=0; [ "$RC" = "2" ] && ok=1
report "stop: 4th stop fires again" "$ok" "rc=$RC"
# a lead-typed count in the marker is clamped to N-1: two silent stops, then it fires
printf '999999\n' > "${TMPDIR:-/tmp}/vllm-neuron-parity-stop-nudged-st1"
ERR="$(run_stop st1)"; RC1=$?; ERR="$(run_stop st1)"; RC2=$?; ERR="$(run_stop st1)"; RC3=$?
ok=0; [ "$RC1" = "0" ] && [ "$RC2" = "0" ] && [ "$RC3" = "2" ] && ok=1
report "stop: a marker holding 999999 buys at most N-1 silent stops (clamped)" "$ok" "rc=$RC1,$RC2,$RC3"

# S3b. a planted sidecar the hook could never have written (future outcomes_at) is reseeded, not trusted
write_evo
write_sidecar IDLE 1000000000 "2026-09-10T00:00:00Z"
stop_state 10
ERR="$(run_stop st3b)"; RC=$?
ok=0; [ "$(sidecar_field outcomes_at)" = "10" ] && [ "$(sidecar_field closed_by)" = "seeded" ] && ok=1
report "stop: a planted sidecar with outcomes_at beyond the run is reseeded from the real count" "$ok" "rc=$RC sidecar=$(cat "$SIDECAR" 2>/dev/null)"
rm -f "$SIDECAR"
# S3c. an OPEN or DUE cycle is never erased by a shrunken count (a graph write): clamp and say so
write_sidecar DUE 1000000000 "2026-09-10T00:00:00Z"
stop_state 10
ERR="$(run_stop st3c)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT COUNTER DISAGREEMENT" && printf '%s' "$ERR" | grep -q "AUDIT DUE" \
  && [ "$(sidecar_field state)" = "DUE" ] && [ "$(sidecar_field checkpoint_id)" = "cp-20260910T000000Z" ] && ok=1
report "stop: a DUE cycle survives a baseline above the run count (disagreement named, brief re-presented)" "$ok" "rc=$RC err=$ERR"
rm -f "$SIDECAR"

# S4. DUE below the threshold does not fire; at the threshold it fires and writes the sidecar
write_evo
rm -f "$SIDECAR"
stop_state 39
ERR="$(run_stop st2)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT DUE" && ok=1
report "stop: 39 declared outcomes (+7 lead rows) is below the audit threshold" "$ok" "rc=$RC err=$ERR"
ok=0; [ -f "$SIDECAR" ] && [ "$(sidecar_field state)" = "IDLE" ] && [ "$(sidecar_field outcomes_at)" = "39" ] \
  && [ "$(sidecar_field closed_by)" = "seeded" ] && ! printf '%s' "$ERR" | grep -q "AUDIT" && ok=1
report "stop: first sight seeds an IDLE baseline at the current counts and passes" "$ok" "sidecar=$(cat "$SIDECAR" 2>/dev/null)"
write_sidecar IDLE 0 "2026-09-10T00:00:00Z"

stop_state 40
START_NS="$(python3 -c 'import time; print(time.time())')"
ERR="$(run_stop st3)"; RC=$?
ELAPSED="$(python3 -c 'import sys, time; print(int(time.time() - float(sys.argv[1])))' "$START_NS")"
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT DUE: checkpoint cp-[0-9]*T[0-9]*Z" && ok=1
report "stop: DUE fires at 40 declared outcomes" "$ok" "rc=$RC err=$ERR"
ok=0; [ -f "$SIDECAR" ] && [ "$(sidecar_field state)" = "DUE" ] && [ "$(sidecar_field outcomes_at)" = "40" ] \
  && [ "$(sidecar_field checkpoint_id)" = "$(printf '%s' "$ERR" | sed -n 's/.*AUDIT DUE: checkpoint \(cp-[0-9TZ]*\).*/\1/p' | head -1)" ] && ok=1
report "stop: DUE writes the sidecar (state DUE, outcomes_at 40, same checkpoint id)" "$ok" "sidecar=$(cat "$SIDECAR" 2>/dev/null)"
ok=0; printf '%s' "$ERR" | grep -qF "$DISPATCH" && ok=1
report "stop: the DUE block carries the dispatch line verbatim" "$ok" "err=$ERR"
ok=0; printf '%s' "$ERR" | grep -q "^evolution root: .*/evolution$" && printf '%s' "$ERR" | grep -q "^pave-init root: " && ok=1
report "stop: the DUE block names the evolution root and the pave-init root" "$ok" "err=$ERR"
ok=0; printf '%s' "$ERR" | grep -q "^census: " && { printf '%s' "$ERR" | grep -q "raw counts: 40 declared-node outcomes" || printf '%s' "$ERR" | grep -q "declared outcomes since: 40"; } && ok=1
report "stop: the DUE block names the census path and the outcome count (census script when shipped, else raw counts)" "$ok" "err=$ERR"
ok=0; [ "$ELAPSED" -lt 5 ] && ok=1
report "stop: a firing exits within 5 s" "$ok" "elapsed=${ELAPSED}s"

# S5. OPEN passes: no DUE, no STALL, sidecar untouched
write_sidecar OPEN 40 "2026-09-10T00:00:00Z"
stop_state 45
BEFORE="$(cat "$SIDECAR")"
ERR="$(run_stop st4)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT" && [ "$(cat "$SIDECAR")" = "$BEFORE" ] && ok=1
report "stop: an OPEN cycle passes the audit branch and leaves the sidecar alone" "$ok" "rc=$RC err=$ERR"

# S6. STALL: OPEN with 10 more declared outcomes and no close is named; a pending-approval proposal exempts it
stop_state 50
ERR="$(run_stop st5)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT STALL: checkpoint cp-20260910T000000Z" \
  && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: a stalled OPEN cycle is named" "$ok" "rc=$RC err=$ERR"

printf 'kind: graph\nenvelope_check: changed_pending_approval\n--- a/workflow.pave.yaml\n' > "$EVO_ROOT/proposals/cp-20260910T000000Z-graph.patch"
ERR="$(run_stop st6a)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT STALL" && ok=1
report "stop: a pending-approval file the router never stamped (lead-typed) exempts nothing" "$ok" "rc=$RC err=$ERR"
write_sidecar OPEN 40 "2026-09-10T00:00:00Z" "" "" "$EVO_ROOT/proposals/cp-20260910T000000Z-graph.patch"
ERR="$(run_stop st6)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT STALL" && ok=1
report "stop: a STAMPED proposal awaiting the user's approval exempts the stall" "$ok" "rc=$RC err=$ERR"
rm -f "$EVO_ROOT/proposals/cp-20260910T000000Z-graph.patch"

# S7. CLOSED via a ledger entry newer than the checkpoint drafted by the updater, whose patch bytes
# equal a proposal the router stamped in the sidecar; a pin appended after it does not hide it
mkdir -p "$EVO_ROOT/proposals" "$EVO_ROOT/history"
printf 'kind: binding\n--- a/workflow.pave.yaml\n+++ b/workflow.pave.yaml\n' > "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
cp "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch" "$EVO_ROOT/history/v1.patch"
write_sidecar OPEN 40 "2026-09-10T00:00:00Z" "" "" "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
cat >> "$EVO_ROOT/revisions.yaml" <<'YAML'
- revision: 1
  kind: binding
  landed_at: '2026-09-10T12:00:00Z'
  review: PASS
  drafted_by: workflow-updater
  patch: history/v1.patch
- revision: 1
  kind: pin
  landed_at: '2026-09-10T12:01:00Z'
  run_id: test-run
YAML
ERR="$(run_stop st7)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "IDLE" ] \
  && [ "$(sidecar_field outcomes_at)" = "50" ] && printf '%s' "$(sidecar_field closed_by)" | grep -q "^ledger:" && ok=1
report "stop: a stamped updater-drafted ledger entry closes the cycle (a later pin does not hide it) and resets the counters" "$ok" "rc=$RC err=$ERR sidecar=$(cat "$SIDECAR")"

# S7b. the same entry shape without a matching stamp in the sidecar (a lead-typed --stamps file) never closes
write_sidecar OPEN 40 "2026-09-10T00:00:00Z"
ERR="$(run_stop st7b)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: an updater-drafted entry whose patch no router stamp vouches for does not close" "$ok" "rc=$RC err=$ERR"
printf 'kind: binding\n+forged\n' > "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
write_sidecar OPEN 40 "2026-09-10T00:00:00Z" "" "" "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
ERR="$(run_stop st7c)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: a stamped proposal whose bytes differ from the landed patch does not close" "$ok" "rc=$RC err=$ERR"
cp "$EVO_ROOT/history/v1.patch" "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
write_sidecar OPEN 40 "2026-09-10T00:00:00Z" "" "" "$EVO_ROOT/proposals/cp-20260910T000000Z-binding.patch"
ERR="$(run_stop st7d)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "IDLE" ] && ok=1
report "stop: restoring the stamped bytes closes it" "$ok" "rc=$RC err=$ERR"

# S8. after the reset, 50 outcomes are not DUE again; 90 are
ERR="$(run_stop st8)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT" && ok=1
report "stop: fresh counters after the close, nothing due" "$ok" "rc=$RC err=$ERR"
stop_state 90
ERR="$(run_stop st9)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT DUE" && [ "$(sidecar_field state)" = "DUE" ] \
  && [ "$(sidecar_field checkpoint_id)" != "cp-20260910T000000Z" ] && ok=1
report "stop: the next threshold mints a new checkpoint" "$ok" "rc=$RC err=$ERR"

# S8b. the bytes fallback: an IDLE cycle with few outcomes but AUDIT_BYTES written since the checkpoint is DUE
write_sidecar IDLE 90 "2026-09-10T13:00:00Z"
printf '%0200d\n' 0 > "$ARTIFACTS/run/big-scratch.txt"      # 201 bytes, logged below
printf '{"at": "2026-09-10T14:00:00Z", "session_id": "s", "agent_id": null, "agent_type": null, "tool": "Write", "path": "%s"}\n' \
  "$ARTIFACTS/run/big-scratch.txt" > "$STATE.write-log.jsonl"
stop_state 91
ERR="$(VLLM_NEURON_PARITY_AUDIT_BYTES=100 run_stop st9b)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT DUE" && [ "$(sidecar_field state)" = "DUE" ] \
  && [ "$(sidecar_field bytes_at)" = "201" ] && ok=1
report "stop: bytes written since the checkpoint over AUDIT_BYTES is DUE (1 outcome, 201 bytes)" "$ok" "rc=$RC err=$ERR sidecar=$(cat "$SIDECAR" 2>/dev/null)"
rm -f "$STATE.write-log.jsonl" "$ARTIFACTS/run/big-scratch.txt"

# S9. an old ledger entry without the updater stamp does not close (pin never closes)
write_sidecar OPEN 90 "2026-09-10T13:00:00Z"
cat >> "$EVO_ROOT/revisions.yaml" <<'YAML'
- revision: 1
  kind: pin
  landed_at: '2026-09-10T14:00:00Z'
  run_id: test-run
YAML
stop_state 92
ERR="$(run_stop st10)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: a pin entry never closes the cycle" "$ok" "rc=$RC err=$ERR"

# S10. CLOSED via the findings record: the router stamped the updater's record (OPEN + findings_record),
# it says no_change_warranted, the trend rose, and the router stamped the reviewer's review: PASS
FINDINGS="$EVO_ROOT/streamlining-findings.md"
printf 'checkpoint: cp-20260910T000000Z\n\noutcome: no_change_warranted\n\n## Trend\ncp-20260910T000000A | 40 | 100 | 2.5\ncp-20260910T000000Z | 50 | 400 | 8.0\nreview: PASS\n' > "$FINDINGS"
write_sidecar OPEN 90 "2026-09-10T13:00:00Z" "$FINDINGS" PASS
ERR="$(run_stop st11)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "IDLE" ] && ok=1
report "stop: a stamped no-change findings record with the reviewer's stamped PASS closes the cycle" "$ok" "rc=$RC err=$ERR"
write_sidecar OPEN 90 "2026-09-10T13:00:00Z" "$FINDINGS" PASS
printf 'outcome: no_change_warranted\n' >> "$FINDINGS"
ERR="$(run_stop st11b)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: a stamped record changed since the stamp (any actor, any write shape) cannot close" "$ok" "rc=$RC err=$ERR"
write_sidecar OPEN 90 "2026-09-10T13:00:00Z" "$FINDINGS"
ERR="$(run_stop st12)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "OPEN" ] && ok=1
report "stop: a rising trend needs the reviewer's STAMPED review: PASS (the text alone does not count)" "$ok" "rc=$RC err=$ERR"
write_sidecar DUE 90 "2026-09-10T13:00:00Z" "" PASS
ERR="$(run_stop st12b)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && [ "$(sidecar_field state)" = "DUE" ] && ok=1
report "stop: a findings record the router never stamped OPEN cannot close (lead-typed record)" "$ok" "rc=$RC err=$ERR"
printf 'checkpoint: cp-20260910T000000Z\n\noutcome: no_change_warranted\n\n## Trend\ncp-20260910T000000A | 40 | 400 | 8.0\ncp-20260910T000000Z | 50 | 100 | 2.5\n' > "$FINDINGS"
write_sidecar OPEN 90 "2026-09-10T13:00:00Z" "$FINDINGS"
ERR="$(run_stop st12c)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT CLOSED" && ok=1
report "stop: a stamped no-change record with a falling trend closes without a reviewer" "$ok" "rc=$RC err=$ERR"
rm -f "$EVO_ROOT/streamlining-findings.md"

# S11. degraded: pave-init older than 2.6.0 gets one line and no sidecar write
rm -rf "$CACHE/2.6.0"; mkdir -p "$CACHE/2.5.4/skills/pave-init"
printf 'version: 2.5.4\n' > "$CACHE/2.5.4/skills/pave-init/VERSION"
rm -f "$SIDECAR"
stop_state 60
ERR="$(run_stop st13)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "AUDIT DEGRADED: pave-init 2.5.4" && [ ! -f "$SIDECAR" ] && ok=1
report "stop: pave-init below 2.6.0 prints one degraded line and writes nothing" "$ok" "rc=$RC err=$ERR"
rm -rf "$CACHE/2.5.4"; mkdir -p "$CACHE/2.6.0/skills/pave-init"
printf 'version: 2.6.0\n' > "$CACHE/2.6.0/skills/pave-init/VERSION"

# S11b. rule-1 route: a real ledger (installed from the plugin root) pinned at revision 0 names the landed graph
rm -rf "$EVO_ROOT" "$SIDECAR"
python3 "$PLUGIN/scripts/record_revision.py" install "$EVO_ROOT" --from "$PLUGIN" >/dev/null 2>&1
DIGEST0="$(python3 - "$EVO_ROOT/revisions.yaml" <<'PY'
import re, sys
first = re.split(r"(?m)^-\s+revision:", open(sys.argv[1]).read())[1]
print(re.search(r"digest_after:\s*[\x27\"]?(sha256:[0-9a-f]+)", first).group(1))
PY
)"
pin_state() { # $1 = move_declined text ("" for null)
  python3 - "$STATE" "$DIGEST0" "${1:-}" <<'PY'
import json, sys
path, digest, declined = sys.argv[1:4]
state = json.load(open(path))
state["workflow_identity"] = {"run_id": "test-run", "active_revision": 0, "bundle_digest": digest,
                              "move_declined": declined or None}
json.dump(state, open(path, "w"))
PY
}
stop_state 5
pin_state
ERR="$(run_stop st19)"; RC=$?
ok=0; [ "$RC" = "2" ] && printf '%s' "$ERR" | grep -q "GRAPH LANDED SINCE PIN (rule 1" \
  && printf '%s' "$ERR" | grep -q "move_declined" && [ "$(sidecar_field state)" = "IDLE" ] && ok=1
report "stop: a graph landed since the pin is named with the rule-1 route (first sight still seeds)" "$ok" "rc=$RC err=$ERR"
pin_state "user 2026-09-10: stay on revision 0"
ERR="$(run_stop st20)"; RC=$?
ok=0; [ "$RC" = "2" ] && ! printf '%s' "$ERR" | grep -q "GRAPH LANDED SINCE PIN" && ok=1
report "stop: a standing move_declined silences the rule-1 route" "$ok" "rc=$RC err=$ERR"
rm -rf "$EVO_ROOT"; write_evo

# S12. gates: stop_hook_active, a subagent, a terminal run, another session, no marker all pass
ERR="$(run_stop st14 "" 1)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: stop_hook_active passes (never loops)" "$ok" "rc=$RC err=$ERR"
ERR="$(run_stop st15 seat-9)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: a subagent payload passes" "$ok" "rc=$RC err=$ERR"
printf 'the-lead\n' > "$STATE.lead-session"
ERR="$(run_stop st16)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: another session passes when the sidecar names the lead" "$ok" "rc=$RC err=$ERR"
rm -f "$STATE.lead-session"
stop_state 60 accepted
ERR="$(run_stop st17)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: a terminal run passes" "$ok" "rc=$RC err=$ERR"
stop_state 60
rm -f "$MARKER"
ERR="$(run_stop st18)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: no marker passes" "$ok" "rc=$RC err=$ERR"
write_marker
ERR="$(printf 'not json' | bash "$STOP_HOOK" 2>&1 >/dev/null)"; RC=$?
ok=0; [ "$RC" = "0" ] && [ -z "$ERR" ] && ok=1
report "stop: unparsable payload fails open" "$ok" "rc=$RC err=$ERR"

# S13. registered in hooks/hooks.json under Stop
python3 - "$PLUGIN" <<'PY' 2>/dev/null
import json, sys
from pathlib import Path
hooks = json.loads((Path(sys.argv[1]) / "hooks" / "hooks.json").read_text())["hooks"]
commands = [h["command"] for e in hooks["Stop"] for h in e["hooks"]]
assert any("stop-guard.sh" in c for c in commands), commands
PY
ok=0; [ "$?" = "0" ] && ok=1
report "stop: registered in hooks/hooks.json under Stop" "$ok" "see hooks/hooks.json"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ] || exit 1
exit 0
