#!/bin/bash
# Test harness for ammo-skill-files-guard.sh (PreToolUse Edit|Write|MultiEdit|
# NotebookEdit). Run: bash hooks/tests/test-ammo-skill-files-guard.sh
#
# Contract: a SUBAGENT may not edit a file inside the plugin root (the realpath
# of the dir that holds hooks/); the team LEAD may. The hook denies via a
# permissionDecision:"deny" JSON body on stdout and always exits 0 (PreToolUse
# deny convention for this hook, distinct from the exit-2 convention used by
# the two Bash guards). Outside a campaign the hook is silent.
#
# The plugin root holds references/, orchestration/, agents/, skills/, hooks/,
# and the audit-invariants.md "Mechanical Authorities" scripts/ and schemas/:
# the state engine, the gate verifier, and the schema a track is judged by. A
# subagent that can rewrite them can pass itself.
#
# The engine fixture is $TMPDIR/engine with .ammo/active = tgt. The engine has
# its own scripts/ and schemas/ dirs: those are the subagent's work, not the
# plugin, so they stay editable.
#
# AMMO_TEST_HOOK aims this harness at another copy of the hook. That copy must
# sit in a plugin tree (hooks/ beside scripts/), because the protected prefix
# is the copy's own plugin root.
set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/ammo-skill-files-guard.sh}"
PLUGIN="$(cd "$(dirname "$HOOK")/.." && pwd -P)"
PASS=0
FAIL=0
TOTAL=0

TMPDIR=$(mktemp -d)
NOCAMP=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR" "$NOCAMP" 2>/dev/null || true; }
trap cleanup EXIT
cd "$TMPDIR"

ENGINE="$(cd "$TMPDIR" && pwd -P)/engine"
mkdir -p "$ENGINE/.ammo/tgt/rounds/1" "$ENGINE/scripts" "$ENGINE/schemas" "$ENGINE/src/pumice"
printf 'tgt\n' > "$ENGINE/.ammo/active"
# A symlink inside the engine that points into the plugin.
ln -s "$PLUGIN/scripts" "$ENGINE/plugin-scripts"

# run_test:
#   $1 name
#   $2 expected verdict: DENY | ALLOW
#   $3 file_path
#   $4 identity: subagent | lead
#   $5 (optional) hook input cwd, default the engine root
#   $6 (optional) CLAUDE_PROJECT_DIR, default the engine root
run_test() {
    local name="$1" expect="$2" path="$3" identity="$4" cwd="${5:-$ENGINE}" proj="${6:-$ENGINE}"
    TOTAL=$((TOTAL + 1))

    local payload
    if [ "$identity" = "subagent" ]; then
        payload=$(jq -nc --arg p "$path" --arg c "$cwd" \
            '{tool_name:"Edit",session_id:"s1",cwd:$c,agent_type:"ammo:ammo-impl-champion",tool_input:{file_path:$p}}')
    else
        payload=$(jq -nc --arg p "$path" --arg c "$cwd" \
            '{tool_name:"Edit",session_id:"s1",cwd:$c,tool_input:{file_path:$p}}')
    fi

    local rc=0
    # HOME points at an empty dir so the lead case has no teams/ to scan.
    echo "$payload" | (cd "$cwd" && env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$proj" bash "$HOOK") \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?

    local got="ALLOW"
    if grep -q '"permissionDecision": *"deny"' "$TMPDIR/out" 2>/dev/null; then
        got="DENY"
    fi

    local ok=true
    [ "$got" = "$expect" ] || ok=false
    # The hook must always exit 0: the JSON body is the enforcement channel.
    [ "$rc" -eq 0 ] || ok=false
    # A deny body must be valid JSON, or Claude Code ignores it.
    if [ "$got" = "DENY" ]; then
        jq . "$TMPDIR/out" >/dev/null 2>&1 || ok=false
    else
        # An allow is silent.
        [ ! -s "$TMPDIR/out" ] || ok=false
    fi

    if [ "$ok" = "true" ]; then
        echo "  PASS [$TOTAL]: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $name (expected=$expect got=$got exit=$rc)"
        echo "        path: $path"
        echo "        out:  $(head -c 200 "$TMPDIR/out" 2>/dev/null || echo '(none)')"
        FAIL=$((FAIL + 1))
    fi
}

echo "== Subagent DENY: files inside the plugin root =="
run_test "references/ md → DENY" DENY "$PLUGIN/references/audit-invariants.md" subagent
run_test "orchestration/ md → DENY" DENY "$PLUGIN/orchestration/parallel-tracks.md" subagent
run_test "agents/ md → DENY" DENY "$PLUGIN/agents/ammo-impl-champion.md" subagent
run_test "skills/ammo/SKILL.md → DENY" DENY "$PLUGIN/skills/ammo/SKILL.md" subagent
run_test "hooks/ script → DENY" DENY "$PLUGIN/hooks/ammo-stop-guard.sh" subagent
run_test "tests/ under the plugin → DENY" DENY "$PLUGIN/tests/test_ammo_state.py" subagent

echo ""; echo "== Subagent DENY: Mechanical Authorities =="
run_test "scripts/ammo_state.py → DENY (state engine)" DENY \
    "$PLUGIN/scripts/ammo_state.py" subagent
run_test "scripts/verify_validation_gates.py → DENY (gate verifier)" DENY \
    "$PLUGIN/scripts/verify_validation_gates.py" subagent
run_test "scripts/reconcile_track_state.py → DENY" DENY \
    "$PLUGIN/scripts/reconcile_track_state.py" subagent
run_test "schemas/state.schema.json → DENY (the schema it is judged by)" DENY \
    "$PLUGIN/schemas/state.schema.json" subagent
run_test "relative path from a cwd in the plugin → DENY" DENY \
    "schemas/state.schema.json" subagent "$PLUGIN"
run_test "dot-dot path that lands in the plugin → DENY" DENY \
    "$PLUGIN/hooks/../scripts/ammo_state.py" subagent
run_test "symlink in the engine that points into the plugin → DENY" DENY \
    "$ENGINE/plugin-scripts/ammo_state.py" subagent

echo ""; echo "== Lead ALLOW: the same protected paths =="
run_test "lead edits scripts/ammo_state.py → ALLOW" ALLOW \
    "$PLUGIN/scripts/ammo_state.py" lead
run_test "lead edits schemas/state.schema.json → ALLOW" ALLOW \
    "$PLUGIN/schemas/state.schema.json" lead
run_test "lead edits references/ md → ALLOW" ALLOW \
    "$PLUGIN/references/audit-invariants.md" lead

echo ""; echo "== ALLOW: paths outside the plugin root =="
run_test "subagent edits Pumice source → ALLOW" ALLOW \
    "$ENGINE/src/pumice/layer.py" subagent
run_test "subagent edits the engine's own scripts/ → ALLOW" ALLOW \
    "$ENGINE/scripts/bench.py" subagent
run_test "subagent edits the engine's own schemas/ → ALLOW" ALLOW \
    "$ENGINE/schemas/x.json" subagent
run_test "relative schemas/ path from the engine cwd → ALLOW" ALLOW \
    "schemas/x.json" subagent
run_test "subagent edits its own artifacts → ALLOW" ALLOW \
    "$ENGINE/.ammo/tgt/rounds/1/notes.md" subagent
run_test "subagent edits state.json → ALLOW (state-validate owns that)" ALLOW \
    "$ENGINE/.ammo/tgt/state.json" subagent
run_test "a sibling dir whose name extends the plugin root → ALLOW" ALLOW \
    "${PLUGIN}-other/scripts/x.py" subagent

echo ""; echo "== Campaign scope =="
run_test "no campaign: subagent edits the plugin → silent ALLOW" ALLOW \
    "$PLUGIN/scripts/ammo_state.py" subagent "$NOCAMP" "$NOCAMP"

echo ""; echo "== Fail-open =="
TOTAL=$((TOTAL + 1))
rc=0
echo '{"tool_name":"Edit","tool_input":{}}' | bash "$HOOK" >"$TMPDIR/out" 2>&1 || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ]; then
    echo "  PASS [$TOTAL]: no file_path → ALLOW, silent"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: no file_path → expected silent exit 0, got exit=$rc"
    FAIL=$((FAIL + 1))
fi

TOTAL=$((TOTAL + 1))
MIN_PATH="$TMPDIR/minbin-nojq"
mkdir -p "$MIN_PATH"
for t in bash sh cat echo env printf dirname basename test rm; do
    real=$(command -v "$t" 2>/dev/null || true)
    [ -n "$real" ] && ln -sf "$real" "$MIN_PATH/$t"
done
rc=0
jq -nc --arg p "$PLUGIN/schemas/state.schema.json" --arg c "$ENGINE" \
    '{tool_name:"Edit",cwd:$c,agent_type:"ammo:ammo-impl-champion",tool_input:{file_path:$p}}' \
    | env PATH="$MIN_PATH" bash "$HOOK" >"$TMPDIR/out" 2>&1 || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ]; then
    echo "  PASS [$TOTAL]: jq unavailable → ALLOW (fail-open), silent"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: jq unavailable → expected silent exit 0, got $rc"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
