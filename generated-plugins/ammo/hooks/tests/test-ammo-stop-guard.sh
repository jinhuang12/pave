#!/bin/bash
# Test harness for ammo-stop-guard.sh (Stop hook)
# Run: bash hooks/tests/test-ammo-stop-guard.sh
#
# v2 state.json shape: round-centric. All seed payloads nest per-round
# state (debate, parallel_tracks, integration) under campaign.rounds[N-1].
#
# Tests:
#   Bug 1 — Role filtering: only lead/solo orchestrator should get nudges
#   Regression — Existing functionality preserved
#   Campaign scope — no .ammo/active marker means no campaign: silent pass
#
# The temp dir is an engine root: .ammo/active names the campaign id and the
# campaign dir is .ammo/test_target/ (CLAUDE_PROJECT_DIR is the engine root).

set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK="$(cd "$(dirname "$0")/.." && pwd)/ammo-stop-guard.sh"
PASS=0
FAIL=0
TOTAL=0

# Create temporary test environment
TMPDIR=$(mktemp -d)
cd "$TMPDIR"
ARTIFACT_DIR="$TMPDIR/.ammo/test_target"
mkdir -p "$ARTIFACT_DIR"
printf 'test_target\n' > "$TMPDIR/.ammo/active"

cleanup() {
    rm -rf "$TMPDIR"
    rm -f "$TMPDIR/hook-stderr"
    rm -f "/tmp/ammo-stop-nudged-test-session"
    rm -f "/tmp/ammo-stop-nudged-unknown"
    rm -f "/tmp/ammo-stop-nudged-lead-session-123"
    rm -f "/tmp/ammo-stop-nudged-teammate-session-1"
    rm -f /tmp/test-transcript-*.jsonl
}
trap cleanup EXIT

run_test() {
    local test_name="$1"
    local expected_exit="$2"
    local json_input="$3"
    local actual_exit=0

    TOTAL=$((TOTAL + 1))
    # Clean marker files before each test
    rm -f /tmp/ammo-stop-nudged-* 2>/dev/null || true

    echo "$json_input" | env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$TMPDIR" bash "$HOOK" 2>"$TMPDIR/hook-stderr" || actual_exit=$?

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  PASS [$TOTAL]: $test_name (exit=$actual_exit)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $test_name (expected=$expected_exit, got=$actual_exit)"
        echo "        stderr: $(cat "$TMPDIR/hook-stderr" 2>/dev/null || echo '(none)')"
        FAIL=$((FAIL + 1))
    fi
}

# ── v2 state-seed helpers ──
# _default_round IDX STATUS  → emit a round JSON object (used for rounds[] array).
_default_round() {
    local rid="$1"
    local status="${2:-IN_PROGRESS}"
    cat <<JSON
{
  "round_id": $rid,
  "status": "$status",
  "team_name": null,
  "profiling_baseline_path": null,
  "baseline": {"started_at": null, "completed_at": null, "e2e_latency": null, "per_bs_verdict": null},
  "bottleneck_mining": {"started_at": null, "completed_at": null, "top_bottleneck_share_pct": null},
  "debate": {"started_at": null, "completed_at": null, "candidates": [], "rounds_completed": 0, "max_rounds": 4, "selected_winners": []},
  "parallel_tracks": {"started_at": null, "completed_at": null, "tracks": {}},
  "integration": {"started_at": null, "completed_at": null, "status": "pending", "passing_candidates": [], "failed_candidates": [], "selected_candidates": [], "conflict_analysis": null, "combined_patch_branch": null, "combined_e2e_result": null, "e2e_latency_combined": null, "per_bs_verdict": null, "commit_sha": null, "final_decision": null, "resolver_invoked": null, "resolver_outcome": null, "conflicting_tracks": null},
  "campaign_eval": {"started_at": null, "completed_at": null},
  "shipped": [],
  "dropped": [],
  "cumulative_speedup_after": null,
  "combined_e2e_speedup_x": null,
  "combined_e2e_delta_pp": null,
  "note": null,
  "round_summary": null
}
JSON
}

# write_state OUT_PATH  (reads stdin as jq mutation expression applied to base v2 state)
# Usage: write_state "$ARTIFACT_DIR/state.json" '<jq_expr>'
write_state() {
    local out="$1"
    local expr="${2:-.}"
    local tmp_base
    tmp_base=$(mktemp)
    cat > "$tmp_base" <<JSON
{
  "target": {"model_id": "test-model", "hardware": "H100", "dtype": "bf16", "tp": 1, "dp": 1, "ep": 1, "component": "auto"},
  "session_id": null,
  "gpu_resources": {"gpu_count": 1, "gpu_model": "NVIDIA H100", "memory_total_gib": 80.0, "cuda_visible_devices": "0"},
  "campaign": {
    "schema_version": "4.0",
    "status": "active",
    "current_round": 1,
    "current_stage": "1_baseline",
    "config": {"min_e2e_improvement_pct": 1.0, "noise_tolerance_pct": 0.5, "catastrophic_regression_pct": 5.0},
    "cumulative_speedup_vs_round1": 1.0,
    "round_1_baseline_latency_s": null,
    "shipped_optimizations": [],
    "agent_costs": [],
    "rounds": [$(_default_round 1 IN_PROGRESS)]
  }
}
JSON
    jq "$expr" "$tmp_base" > "$out"
    rm -f "$tmp_base"
}

# ══════════════════════════════════════════════════
echo "== Bug 1: Role Filtering — Teammates must be silenced =="
# ══════════════════════════════════════════════════

# Setup: active campaign at stage 7 (nudge-worthy).
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

# Setup team config: lead is "lead-session-123"
mkdir -p "$TMPDIR/.claude/teams/ammo-round-2"
cat > "$TMPDIR/.claude/teams/ammo-round-2/config.json" << 'EOF'
{
  "leadSessionId": "lead-session-123",
  "members": [
    {"name": "impl-champion-op003", "sessionId": "teammate-session-1"},
    {"name": "impl-validator-op003", "sessionId": "teammate-session-2"}
  ]
}
EOF

# Test 1: Teammate with agent_type in JSON → should NOT get nudged (exit 0)
run_test "Teammate (agent_type=implementor) silenced" 0 \
    '{"session_id": "lead-session-123", "agent_type": "implementor", "agent_id": "uuid-1"}'

# Test 2: Teammate with different agent type → also silenced
run_test "Teammate (agent_type=verifier) silenced" 0 \
    '{"session_id": "lead-session-123", "agent_type": "verifier", "agent_id": "uuid-2"}'

# Test 3: Lead (no agent_type) → should get nudged (exit 2)
run_test "Lead (no agent_type) gets stage-7 nudge" 2 \
    '{"session_id": "lead-session-123"}'

# Test 4: Teammate receiving Stage 7 report nag
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "campaign_complete" |
  .campaign.current_round = 3 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 SHIPPED)"',
    '"$(_default_round 3 EXHAUSTED)"'
  ]
'

run_test "Teammate silenced for Stage 7 report nag" 0 \
    '{"session_id": "lead-session-123", "agent_type": "general-purpose", "agent_id": "uuid-4"}'

# Test 5: Lead should get Stage 7 report nag (no REPORT.md)
run_test "Lead gets Stage 7 report nag (no REPORT.md)" 2 \
    '{"session_id": "lead-session-123"}'

# The nudge names the plugin address of the report writer.
TOTAL=$((TOTAL + 1))
if grep -qF "subagent_type=ammo:ammo-report-writer" "$TMPDIR/hook-stderr"; then
    echo "  PASS [$TOTAL]: Stage 7 nudge names subagent_type=ammo:ammo-report-writer"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Stage 7 nudge lacks the plugin address: $(head -c 300 "$TMPDIR/hook-stderr")"
    FAIL=$((FAIL + 1))
fi
TOTAL=$((TOTAL + 1))
if grep -qF "report_fact_check.json" "$TMPDIR/hook-stderr"; then
    echo "  PASS [$TOTAL]: Stage 7 report nudge names the fact-check gate"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Stage 7 report nudge lacks the fact-check gate: $(head -c 300 "$TMPDIR/hook-stderr")"
    FAIL=$((FAIL + 1))
fi

# Test 6: Solo orchestrator (no team configs) → should still get nudged
rm -rf "$TMPDIR/.claude/teams"
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

run_test "Solo orchestrator (no teams) gets nudge" 2 \
    '{"session_id": "unknown"}'

# ══════════════════════════════════════════════════
echo ""
echo "== Bug 3: Tmux teammate detection (no agent_type, Docker paths) =="
# ══════════════════════════════════════════════════

# Test A — Docker path: CLAUDE_CONFIG_DIR differs from HOME
# Setup: team config at CLAUDE_CONFIG_DIR, NOT at HOME
DOCKER_CONFIG_DIR="$TMPDIR/claude-config"
mkdir -p "$DOCKER_CONFIG_DIR/teams/ammo-round-2"
cat > "$DOCKER_CONFIG_DIR/teams/ammo-round-2/config.json" << 'TCEOF'
{"leadSessionId": "lead-session-123", "members": [{"name": "impl-champion-op003", "sessionId": "teammate-session-1"}]}
TCEOF
FAKE_HOME=$(mktemp -d)  # empty HOME with no .claude/teams/

# Campaign state: stage 7 active (would nudge orchestrator)
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

TOTAL=$((TOTAL + 1))
rm -f /tmp/ammo-stop-nudged-* 2>/dev/null || true
actual_exit=0
echo '{"session_id": "teammate-session-1"}' | \
    env HOME="$FAKE_HOME" CLAUDE_PROJECT_DIR="$TMPDIR" CLAUDE_CONFIG_DIR="$DOCKER_CONFIG_DIR" \
    bash "$HOOK" 2>/dev/null || actual_exit=$?
if [ "$actual_exit" -eq 0 ]; then
    echo "  PASS [$TOTAL]: Docker path — teammate detected via CLAUDE_CONFIG_DIR"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Docker path — teammate detected via CLAUDE_CONFIG_DIR (expected=0, got=$actual_exit)"
    FAIL=$((FAIL + 1))
fi
rm -rf "$FAKE_HOME"

# Remove team configs — force transcript-only detection
rm -rf "$TMPDIR/.claude/teams" "$TMPDIR/claude-config"

# Create fake transcript JSONL for Test B
cat > /tmp/test-transcript-B.jsonl << 'EOF'
{"type":"summary","agentName":"impl-champion-op003","teamName":"ammo-round-2","timestamp":"2026-04-09T10:00:00Z"}
{"type":"user","message":{"role":"user","content":"implement op003"}}
EOF

# Campaign: stage 7 active (would nudge orchestrator)
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

run_test "Tmux teammate detected via transcript agentName" 0 \
    '{"session_id": "teammate-session-1", "transcript_path": "/tmp/test-transcript-B.jsonl"}'

# Test C — Orchestrator transcript has NO agentName (negative guard)
cat > /tmp/test-transcript-C.jsonl << 'EOF'
{"type":"user","message":{"role":"user","content":"start campaign"}}
{"type":"assistant","message":{"role":"assistant","content":"starting..."}}
EOF

run_test "Orchestrator transcript (no agentName) still gets nudge" 2 \
    '{"session_id": "lead-session-123", "transcript_path": "/tmp/test-transcript-C.jsonl"}'

# Test D — Team-lead transcript must NOT be silenced
cat > /tmp/test-transcript-D.jsonl << 'EOF'
{"type":"summary","agentName":"team-lead","teamName":"ammo-round-2","timestamp":"2026-04-09T10:00:00Z"}
{"type":"user","message":{"role":"user","content":"orchestrate campaign"}}
EOF

run_test "Team-lead transcript still gets nudge (not silenced)" 2 \
    '{"session_id": "lead-session-123", "transcript_path": "/tmp/test-transcript-D.jsonl"}'

# Restore team config for remaining tests
mkdir -p "$TMPDIR/.claude/teams/ammo-round-2"
cat > "$TMPDIR/.claude/teams/ammo-round-2/config.json" << 'EOF'
{
  "leadSessionId": "lead-session-123",
  "members": [
    {"name": "impl-champion-op003", "sessionId": "teammate-session-1"}
  ]
}
EOF

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Existing correct behavior preserved =="
# ══════════════════════════════════════════════════

# Test 13: No campaign artifacts → silent pass
rm -f "$ARTIFACT_DIR/state.json"

run_test "No state.json → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

# write_fact_check OK SHA: write report_assets/report_fact_check.json.
# The SHA comes from python3 here, so the test does not reuse the hook's hash code.
report_sha() {
    python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"
}
write_fact_check() {
    mkdir -p "${ARTIFACT_DIR}/report_assets"
    jq -n --argjson ok "$1" --arg sha "$2" '{ok: $ok, findings: [], report_sha256: $sha}' \
        > "${ARTIFACT_DIR}/report_assets/report_fact_check.json"
}
expect_stderr() {
    local label="$1" needle="$2"
    TOTAL=$((TOTAL + 1))
    if grep -qF -- "$needle" "$TMPDIR/hook-stderr"; then
        echo "  PASS [$TOTAL]: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $label: $(head -c 300 "$TMPDIR/hook-stderr")"
        FAIL=$((FAIL + 1))
    fi
}

# Test 14: Terminal campaign + REPORT.md + accepted fact-check → silent pass
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "campaign_complete" |
  .campaign.current_round = 3 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 SHIPPED)"',
    '"$(_default_round 3 EXHAUSTED)"'
  ]
'
echo "# Optimization Report" > "${ARTIFACT_DIR}/REPORT.md"
write_fact_check true "$(report_sha "${ARTIFACT_DIR}/REPORT.md")"

run_test "Terminal campaign + REPORT.md + accepted fact-check → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

# Test 14b: REPORT.md without a fact-check file → fact-check nudge
rm -rf "${ARTIFACT_DIR}/report_assets"
run_test "Terminal campaign + REPORT.md, no fact-check → nudge" 2 \
    '{"session_id": "lead-session-123"}'
expect_stderr "Unchecked report nudge names report_fact_check.json" "report_assets/report_fact_check.json"
expect_stderr "Unchecked report nudge names the fact-check assignment" "ammo-delegate"

# Test 14c: fact-check bound to older report bytes → nudge
write_fact_check true "$(report_sha "${ARTIFACT_DIR}/REPORT.md")"
echo "# Edited after the check" >> "${ARTIFACT_DIR}/REPORT.md"
run_test "Terminal campaign + stale fact-check SHA → nudge" 2 \
    '{"session_id": "lead-session-123"}'

# Test 14d: fact-check rejected the current report → nudge
write_fact_check false "$(report_sha "${ARTIFACT_DIR}/REPORT.md")"
run_test "Terminal campaign + fact-check ok=false → nudge" 2 \
    '{"session_id": "lead-session-123"}'

# Test 14e: campaign_exhausted with an accepted report → silent pass
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "campaign_exhausted" |
  .campaign.current_round = 3 |
  .campaign.current_stage = "7_campaign_eval"
'
write_fact_check true "$(report_sha "${ARTIFACT_DIR}/REPORT.md")"
run_test "campaign_exhausted + accepted fact-check → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

rm -rf "${ARTIFACT_DIR}/REPORT.md" "${ARTIFACT_DIR}/report_assets"

# Test 15: Paused campaign → silent pass
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "paused" |
  .campaign.current_round = 2 |
  .campaign.current_stage = "4_5_parallel_tracks" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

run_test "Paused campaign → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

# Test 16: Stages 4-5 → silent pass (no nudge since overlap logic removed)
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 1 |
  .campaign.current_stage = "4_5_parallel_tracks" |
  .campaign.rounds = [
    ('"$(_default_round 1 IN_PROGRESS)"' | .parallel_tracks.tracks = {"op001": {"status": "IN_PROGRESS", "verdict": null}})
  ]
'

run_test "Round 1 implementation → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

# Test 17: Stage 7 active → lead gets threshold-check nudge
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

run_test "Stage 7 active → lead gets threshold-check nudge" 2 \
    '{"session_id": "lead-session-123"}'

# Test 18: Stage 7b → lead gets report nudge
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "campaign_complete" |
  .campaign.current_round = 2 |
  .campaign.current_stage = "7b_report" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 EXHAUSTED)"'
  ]
'

run_test "Stage 7b → lead gets report nudge" 2 \
    '{"session_id": "lead-session-123"}'

# The nudge names the plugin address of the report writer.
TOTAL=$((TOTAL + 1))
if grep -qF "subagent_type=ammo:ammo-report-writer" "$TMPDIR/hook-stderr"; then
    echo "  PASS [$TOTAL]: Stage 7b nudge names subagent_type=ammo:ammo-report-writer"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Stage 7b nudge lacks the plugin address: $(head -c 300 "$TMPDIR/hook-stderr")"
    FAIL=$((FAIL + 1))
fi
TOTAL=$((TOTAL + 1))
if grep -qF "report_fact_check.json" "$TMPDIR/hook-stderr"; then
    echo "  PASS [$TOTAL]: Stage 7b nudge names the fact-check gate"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Stage 7b nudge lacks the fact-check gate: $(head -c 300 "$TMPDIR/hook-stderr")"
    FAIL=$((FAIL + 1))
fi

# Test 19-20: Circuit breaker — second stop attempt passes through
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

# First stop → nudge (exit 2), creates marker
run_test "Circuit breaker: 1st stop → nudge" 2 \
    '{"session_id": "lead-session-123"}'

# Second stop — run manually to preserve the marker from 1st stop
TOTAL=$((TOTAL + 1))
rm -f /tmp/ammo-stop-nudged-* 2>/dev/null || true
# Run hook twice: first creates marker, second sees it
echo '{"session_id": "lead-session-123"}' | env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$TMPDIR" bash "$HOOK" 2>/dev/null || true
actual_exit=0
echo '{"session_id": "lead-session-123"}' | env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$TMPDIR" bash "$HOOK" 2>"$TMPDIR/hook-stderr" || actual_exit=$?
if [ "$actual_exit" -eq 0 ]; then
    echo "  PASS [$TOTAL]: Circuit breaker: 2nd stop → pass through (exit=$actual_exit)"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: Circuit breaker: 2nd stop → pass through (expected=0, got=$actual_exit)"
    echo "        stderr: $(cat "$TMPDIR/hook-stderr" 2>/dev/null || echo '(none)')"
    FAIL=$((FAIL + 1))
fi

# Test 21: Debate stage → silent pass
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "3_debate" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'

run_test "Debate stage → silent pass" 0 \
    '{"session_id": "lead-session-123"}'

# ══════════════════════════════════════════════════
echo ""
echo "== Verifier edge-case probe: CLAUDE_CONFIG_DIR == HOME/.claude =="
# ══════════════════════════════════════════════════

# Edge-case probe: when CLAUDE_CONFIG_DIR is explicitly set to the same value
# as $HOME/.claude, team detection must still work (no path divergence).
TOTAL=$((TOTAL + 1))
rm -f /tmp/ammo-stop-nudged-* 2>/dev/null || true
# State: stage 7 (would nudge orchestrator)
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.current_round = 2 |
  .campaign.current_stage = "7_campaign_eval" |
  .campaign.rounds = [
    '"$(_default_round 1 SHIPPED)"',
    '"$(_default_round 2 IN_PROGRESS)"'
  ]
'
actual_exit=0
echo '{"session_id": "teammate-session-1"}' | \
    env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$TMPDIR" CLAUDE_CONFIG_DIR="$TMPDIR/.claude" \
    bash "$HOOK" 2>/dev/null || actual_exit=$?
if [ "$actual_exit" -eq 0 ]; then
    echo "  PASS [$TOTAL]: CLAUDE_CONFIG_DIR == HOME/.claude — teammate still silenced"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: CLAUDE_CONFIG_DIR == HOME/.claude — teammate still silenced (expected=0, got=$actual_exit)"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "== Verifier edge-case probe 2: transcript_path given but file missing =="
# ══════════════════════════════════════════════════

# Edge-case: transcript_path in JSON but file doesn't exist → fail-open.
run_test "Missing transcript file → fail-open, lead still gets nudge" 2 \
    '{"session_id": "lead-session-123", "transcript_path": "/tmp/nonexistent-does-not-exist-98765.jsonl"}'

echo ""
echo "== Campaign scope: no marker → no campaign =="
# Same active Stage 7 state, but the .ammo/active marker is gone, so the
# engine root holds no campaign. The plugin is enabled in every project; a
# stop outside a campaign must never be held.
write_state "$ARTIFACT_DIR/state.json" '
  .campaign.status = "active"
  | .campaign.current_stage = "7_campaign_eval"
'
mv "$TMPDIR/.ammo/active" "$TMPDIR/.ammo/active.off"
run_test "No campaign marker → silent pass" 0 \
    '{"session_id": "lead-session-123"}'
TOTAL=$((TOTAL + 1))
rm -f /tmp/ammo-stop-nudged-* 2>/dev/null || true
NC_OUT=$(echo '{"session_id": "lead-session-123"}' | env HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$TMPDIR" bash "$HOOK" 2>&1) || true
if [ -z "$NC_OUT" ] && [ ! -e /tmp/ammo-stop-nudged-lead-session-123 ]; then
    echo "  PASS [$TOTAL]: No campaign marker → no output, no circuit-breaker marker"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: No campaign marker → expected silence, got [$NC_OUT]"
    FAIL=$((FAIL + 1))
fi
mv "$TMPDIR/.ammo/active.off" "$TMPDIR/.ammo/active"
run_test "Marker restored → lead gets the Stage 7 nudge again" 2 \
    '{"session_id": "lead-session-123"}'

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
