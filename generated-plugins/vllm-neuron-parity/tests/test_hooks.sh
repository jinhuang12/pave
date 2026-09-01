#!/usr/bin/env bash
# Invariant tests for the vllm-neuron-parity write-for-reader hook
# (skills/vllm-neuron-parity/hooks/write-for-reader.sh).
#
# What is tested (behavior, not wording):
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
#
# Self-contained: everything runs inside a mktemp sandbox with its own
# TMPDIR, so throttle counters start clean on every run. No real run state,
# marker, or counter is touched. Exits 1 on any failure.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN="$(cd "$HERE/.." && pwd)"
READER_HOOK="$PLUGIN/skills/vllm-neuron-parity/hooks/write-for-reader.sh"

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
    "active_node_runs": [{"node": "scan_upstream_delta", "instance": "run"}],
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

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = "0" ] || exit 1
exit 0
