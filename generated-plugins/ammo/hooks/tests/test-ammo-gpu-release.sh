#!/bin/bash
# Test harness for ammo-gpu-release.sh (PostToolUse hook).
# Run: bash hooks/tests/test-ammo-gpu-release.sh
#
# Validates that the session_id extraction regex handles:
#   1. `--no-auto-release` present → release skipped (space form and retry loop)
#   2. Canonical with trailing ')': `$(reserve ... --session-id foo)` (no following flag)
#   3. Equals form: `--session-id=foo`
#   4. Reserve without `--no-auto-release` (with following flag) → released
#   5. No reserve in command → no release attempt
#   6. Colon ids: `--session-id parent:child` releases exactly `parent:child`
#   7. Two-space form: `--session-id  foo` releases foo, not $CLAUDE_SESSION_ID
#   8. Campaign scope: outside a campaign nothing is released and stdout is
#      empty; a reserve without --session-id releases the campaign id
#
# The engine fixture is $TMPDIR/engine with .ammo/active = camp1; the hook
# input carries cwd so _find_campaign walks to it.

set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/ammo-gpu-release.sh}"
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
HOOK_CWD="$ENGINE"

cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

# Seed a state.json with four held GPUs by distinct session ids so we can
# observe which ones the hook releases.
seed_state() {
    cat > "$GPU_RES_DIR/state.json" <<EOF
{
  "gpus": {
    "0": {"session_id": "sess_foo", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "1": {"session_id": "sess_bar", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "2": {"session_id": "t2-baseline", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "3": {"session_id": "stage2_ncu_123", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "4": {"session_id": "parent", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "5": {"session_id": "parent:child", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""},
    "6": {"session_id": "camp1", "reserved_at": "2026-04-16T19:00:00Z", "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": ""}
  },
  "gpu_count": 7,
  "audit": []
}
EOF
}

# Invoke the hook with the given command; return the session_ids still held.
# $2 sets CLAUDE_SESSION_ID (the extraction fallback). It defaults to the
# unseeded "env-session"; pass a seeded id to prove the fallback stays unused.
run_hook() {
    local cmd="$1"
    local env_sid="${2:-env-session}"
    local json
    json=$(jq -n --arg cmd "$cmd" --arg cwd "$HOOK_CWD" '{
        hook_event_name: "PostToolUse",
        tool_name: "Bash",
        cwd: $cwd,
        tool_input: {command: $cmd}
    }')
    AMMO_GPU_RES_DIR="$GPU_RES_DIR" \
        CLAUDE_SESSION_ID="$env_sid" \
        bash "$HOOK" <<< "$json" >"$TMPDIR/hook-stdout" 2>/dev/null || true
}

assert_stdout_empty() {
    TOTAL=$((TOTAL + 1))
    if [ ! -s "$TMPDIR/hook-stdout" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $1 (stdout empty)"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $1 (stdout: $(cat "$TMPDIR/hook-stdout"))"
    fi
}

assert_released() {
    local name="$1" expected_sid="$2"
    TOTAL=$((TOTAL + 1))
    # After release, the gpu entry owned by expected_sid should be null.
    local still_held
    still_held=$(jq -r --arg s "$expected_sid" \
        '.gpus | to_entries | map(select(.value != null and .value.session_id == $s)) | length' \
        "$GPU_RES_DIR/state.json")
    if [ "$still_held" = "0" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $name (released $expected_sid)"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $name (expected $expected_sid released, still held)"
    fi
}

assert_not_released() {
    local name="$1" sid="$2"
    TOTAL=$((TOTAL + 1))
    local still_held
    still_held=$(jq -r --arg s "$sid" \
        '.gpus | to_entries | map(select(.value != null and .value.session_id == $s)) | length' \
        "$GPU_RES_DIR/state.json")
    if [ "$still_held" != "0" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $name ($sid preserved)"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $name ($sid was unexpectedly released)"
    fi
}

echo "=== --no-auto-release present → release skipped (space-form) ==="
seed_state
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id sess_foo --no-auto-release) && CUDA_VISIBLE_DEVICES=$CVD echo ok'
assert_not_released "no-auto-release space-form" "sess_foo"

echo "=== Reserve without --no-auto-release (with following flag) → released ==="
seed_state
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id sess_foo --lease-minutes 30) && CUDA_VISIBLE_DEVICES=$CVD echo ok'
assert_released "space-form without no-auto-release" "sess_foo"

echo "=== Two-space form (--session-id  foo) → argv id released, env id kept ==="
seed_state
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id  sess_foo) && echo ok' "sess_bar"
assert_released "two-space form releases argv id" "sess_foo"
assert_not_released "two-space form leaves CLAUDE_SESSION_ID lease" "sess_bar"

echo "=== Canonical \$(...) pattern (trailing paren, no following flag) ==="
seed_state
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id sess_bar) && echo done'
assert_released "space-form with trailing paren" "sess_bar"

echo "=== Equals form (--session-id=foo) ==="
seed_state
run_hook 'python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id=t2-baseline'
assert_released "equals-form" "t2-baseline"

echo "=== Retry loop form with --no-auto-release → release skipped ==="
seed_state
run_hook 'for i in $(seq 1 60); do CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id stage2_ncu_123 --no-auto-release 2>/dev/null) && break; done'
assert_not_released "retry loop with no-auto-release" "stage2_ncu_123"

echo "=== Colon id (space form): parent:child releases exactly parent:child ==="
seed_state
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id parent:child) && echo ok'
assert_released "colon id space-form" "parent:child"
assert_not_released "colon id space-form leaves parent" "parent"

echo "=== Colon id (equals form): --session-id=parent:child ==="
seed_state
run_hook 'python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id=parent:child'
assert_released "colon id equals-form" "parent:child"
assert_not_released "colon id equals-form leaves parent" "parent"

echo "=== No reserve in command → no release ==="
seed_state
run_hook 'python -c "import torch; print(torch.cuda.is_available())"'
assert_not_released "non-reserve command" "sess_foo"

echo "=== Reserve without --session-id → campaign id released, env id kept ==="
seed_state
run_hook 'CVD=$(scripts/ammo gpu_reservation.py reserve --num-gpus 1) && echo ok' "sess_bar"
assert_released "no --session-id releases the campaign id" "camp1"
assert_not_released "no --session-id leaves CLAUDE_SESSION_ID lease" "sess_bar"
assert_stdout_empty "in-campaign release"

echo "=== Outside a campaign → nothing released, stdout empty ==="
seed_state
rm -f "$GPU_RES_DIR/release.log"
HOOK_CWD="$TMPDIR/elsewhere"
run_hook 'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1 --session-id sess_foo) && echo ok'
HOOK_CWD="$ENGINE"
assert_not_released "no campaign leaves the lease" "sess_foo"
assert_stdout_empty "no campaign"
TOTAL=$((TOTAL + 1))
if [ ! -e "$GPU_RES_DIR/release.log" ]; then
    PASS=$((PASS + 1)); echo "  PASS: no campaign writes no release attempt"
else
    FAIL=$((FAIL + 1)); echo "  FAIL: no campaign wrote $(cat "$GPU_RES_DIR/release.log")"
fi

echo ""
echo "=== Summary ==="
echo "$PASS / $TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
    echo "FAILED: $FAIL"
    exit 1
fi
exit 0
