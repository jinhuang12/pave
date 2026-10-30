#!/bin/bash
# Test harness for ammo-subagent-release.sh (SubagentStop hook).
# Run: bash hooks/tests/test-ammo-subagent-release.sh
#
# Checks:
#   1. In a campaign, a departing subagent releases <session>:<agent> and its
#      descendants, plus the bare <session>; siblings and the campaign id stay.
#   2. No agent_id: only the bare <session> is released.
#   3. Outside a campaign: nothing is released, no release log, empty stdout.
#
# The engine fixture is $TMPDIR/engine with .ammo/active = camp1; the hook
# input carries cwd so _find_campaign walks to it.

set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR CLAUDE_SESSION_ID

HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/ammo-subagent-release.sh}"
PASS=0
FAIL=0
TOTAL=0

TMPDIR=$(mktemp -d)
cd "$TMPDIR"
GPU_RES_DIR="$TMPDIR/gpu_res"
mkdir -p "$GPU_RES_DIR"
ENGINE="$TMPDIR/engine"
mkdir -p "$ENGINE/.ammo/camp1" "$TMPDIR/elsewhere"
printf 'camp1\n' > "$ENGINE/.ammo/active"

cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

seed_state() {
    rm -f "$GPU_RES_DIR/release.log"
    cat > "$GPU_RES_DIR/state.json" <<EOF
{
  "gpus": {
    "0": {"session_id": "S", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "1": {"session_id": "S:A", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "2": {"session_id": "S:A:grandchild", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "3": {"session_id": "S:B", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "4": {"session_id": "camp1", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""}
  },
  "gpu_count": 5,
  "audit": []
}
EOF
}

# run_hook CWD AGENT_ID
run_hook() {
    local cwd="$1" agent="$2" json
    json=$(jq -n --arg cwd "$cwd" --arg a "$agent" '{
        hook_event_name: "SubagentStop",
        session_id: "S",
        agent_id: $a,
        agent_type: "ammo:ammo-champion",
        cwd: $cwd
    }')
    AMMO_GPU_RES_DIR="$GPU_RES_DIR" bash "$HOOK" <<< "$json" >"$TMPDIR/hook-stdout" 2>/dev/null || true
}

held() {
    jq -r --arg s "$1" \
        '.gpus | to_entries | map(select(.value != null and .value.session_id == $s)) | length' \
        "$GPU_RES_DIR/state.json"
}

check() {
    local name="$1" ok="$2"
    TOTAL=$((TOTAL + 1))
    if [ "$ok" = "yes" ]; then
        PASS=$((PASS + 1)); echo "  PASS: $name"
    else
        FAIL=$((FAIL + 1)); echo "  FAIL: $name"
    fi
}
released() { [ "$(held "$1")" = "0" ] && echo yes || echo no; }
kept() { [ "$(held "$1")" != "0" ] && echo yes || echo no; }
empty_stdout() { [ ! -s "$TMPDIR/hook-stdout" ] && echo yes || echo no; }

echo "=== In campaign, agent_id A ==="
seed_state
run_hook "$ENGINE" "A"
check "S:A released" "$(released S:A)"
check "S:A:grandchild released (include-children)" "$(released S:A:grandchild)"
check "bare S released" "$(released S)"
check "sibling S:B kept" "$(kept S:B)"
check "campaign id lease kept" "$(kept camp1)"
check "stdout empty" "$(empty_stdout)"

echo "=== In campaign, no agent_id ==="
seed_state
run_hook "$ENGINE" ""
check "bare S released" "$(released S)"
check "S:A kept (no children sweep on the bare id)" "$(kept S:A)"

echo "=== Outside a campaign ==="
seed_state
run_hook "$TMPDIR/elsewhere" "A"
check "S:A kept" "$(kept S:A)"
check "S kept" "$(kept S)"
check "no release log" "$([ ! -e "$GPU_RES_DIR/release.log" ] && echo yes || echo no)"
check "stdout empty" "$(empty_stdout)"

echo ""
echo "=== Summary ==="
echo "$PASS / $TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
    echo "FAILED: $FAIL"
    exit 1
fi
exit 0
