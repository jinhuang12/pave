#!/bin/bash
# Test harness for ammo-pretool-guard.sh (PreToolUse hook)
# Run: bash hooks/tests/test-ammo-pretool-guard.sh
#
# Tests:
#   Bug S7a — Explicit CVD bypass: CUDA_VISIBLE_DEVICES=0 should NOT skip reservation check
#   Bug S7b — Prefixed vllm/torchrun detection: CUDA_VISIBLE_DEVICES=0 vllm bench latency
#   Regression — Existing functionality preserved
#   Campaign scope — silent outside a campaign; one-shot key falls back to the
#   hook input's session_id; worktree venv guard names `import pumice`
#
# TMPDIR is an engine root: .ammo/active names campaign test_target. Hooks
# run with CLAUDE_PROJECT_DIR=$TMPDIR, so _find_campaign walks to the marker.

set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

# AMMO_TEST_HOOK lets a reviewer aim this harness at an ARCHIVED copy of the
# hook to see which cases the archived version fails. Used to demonstrate
# red->green for the fast-path-bypass cases below.
HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/ammo-pretool-guard.sh}"
PASS=0
FAIL=0
TOTAL=0

# Create temporary test environment
TMPDIR=$(mktemp -d)
cd "$TMPDIR"
ARTIFACT_DIR="$TMPDIR/.ammo/test_target"
mkdir -p "$ARTIFACT_DIR"
printf 'test_target\n' > "$TMPDIR/.ammo/active"
NOCAMP=$(mktemp -d)

# Minimal state.json. The campaign is found through the marker; the hook does
# not parse state. Seeded with v2 shape for consistency with the rest of the suite.
cat > "$ARTIFACT_DIR/state.json" << 'EOF'
{
  "target": {"model_id": "test-model", "hardware": "H100", "dtype": "bf16", "tp": 1, "ep": 1, "component": "auto"},
  "session_id": null,
  "gpu_resources": {"gpu_count": 1, "gpu_model": "NVIDIA H100", "memory_total_gib": 80.0, "cuda_visible_devices": "0"},
  "campaign": {
    "schema_version": "3.0",
    "status": "active",
    "current_round": 1,
    "current_stage": "1_baseline",
    "config": {"min_e2e_improvement_pct": 1.0, "noise_tolerance_pct": 0.5, "catastrophic_regression_pct": 5.0},
    "rounds": [
      {
        "round_id": 1,
        "status": "IN_PROGRESS",
        "baseline": {"started_at": null, "completed_at": null},
        "bottleneck_mining": {"started_at": null, "completed_at": null, "top_bottleneck_share_pct": null},
        "debate": {"started_at": null, "completed_at": null, "candidates": [], "rounds_completed": 0, "max_rounds": 4, "selected_winners": []},
        "parallel_tracks": {"started_at": null, "completed_at": null, "tracks": {}},
        "integration": {"started_at": null, "completed_at": null, "status": "pending"},
        "campaign_eval": {"started_at": null, "completed_at": null}
      }
    ]
  }
}
EOF

GPU_RES_DIR="$TMPDIR/gpu_res"
mkdir -p "$GPU_RES_DIR"

cleanup() {
    rm -rf "$TMPDIR" "$NOCAMP"
    rm -f "$TMPDIR/hook-stderr"
}
trap cleanup EXIT

run_test() {
    local test_name="$1"
    local expected_exit="$2"
    local command_str="$3"
    local actual_exit=0

    TOTAL=$((TOTAL + 1))
    # Clean warned flag before each test
    rm -f "$GPU_RES_DIR/.warned_"* 2>/dev/null || true

    # Build hook input JSON (PreToolUse Bash)
    local json_input
    json_input=$(jq -n --arg cmd "$command_str" '{
        hook_event_name: "PreToolUse",
        tool_name: "Bash",
        tool_input: { command: $cmd }
    }')

    echo "$json_input" | env \
        HOME="$TMPDIR" \
        CLAUDE_PROJECT_DIR="$TMPDIR" \
        CLAUDE_SESSION_ID="test-session" \
        AMMO_GPU_RES_DIR="$GPU_RES_DIR" \
        bash "$HOOK" 2>"$TMPDIR/hook-stderr" || actual_exit=$?

    if [ "$actual_exit" -eq "$expected_exit" ]; then
        echo "  PASS [$TOTAL]: $test_name (exit=$actual_exit)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $test_name (expected=$expected_exit, got=$actual_exit)"
        echo "        stderr: $(head -3 "$TMPDIR/hook-stderr" 2>/dev/null || echo '(none)')"
        FAIL=$((FAIL + 1))
    fi
}

# run_raw PROJECT_DIR JSON [ENV-ARGS...]: run the hook from PROJECT_DIR with
# an explicit input; ENV-ARGS go first to env (-u NAME, NAME=value). Sets
# actual_exit; stdout/stderr land in $TMPDIR/hook-{stdout,stderr}.
run_raw() {
    local proj="$1" json="$2"
    shift 2
    actual_exit=0
    rm -f "$GPU_RES_DIR/.warned_"* 2>/dev/null || true
    echo "$json" | (cd "$proj" && env "$@" HOME="$TMPDIR" CLAUDE_PROJECT_DIR="$proj" AMMO_GPU_RES_DIR="$GPU_RES_DIR" \
        bash "$HOOK") >"$TMPDIR/hook-stdout" 2>"$TMPDIR/hook-stderr" || actual_exit=$?
}

check() {
    TOTAL=$((TOTAL + 1))
    if [ "$2" = "yes" ]; then
        echo "  PASS [$TOTAL]: $1"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $1 (exit=$actual_exit)"
        echo "        stdout: $(head -3 "$TMPDIR/hook-stdout" 2>/dev/null)"
        echo "        stderr: $(head -3 "$TMPDIR/hook-stderr" 2>/dev/null)"
        FAIL=$((FAIL + 1))
    fi
}

# ══════════════════════════════════════════════════
echo "== Bug S7a: Explicit CVD bypass — should trigger reservation warning =="
# ══════════════════════════════════════════════════

# These commands hardcode CUDA_VISIBLE_DEVICES=0 without using gpu_reservation.py.
# They should trigger the one-shot warning (exit 2), NOT pass through silently (exit 0).

run_test "CUDA_VISIBLE_DEVICES=0 python benchmark.py → warn" 2 \
    'CUDA_VISIBLE_DEVICES=0 python benchmark.py --model foo --gpu'

run_test "CUDA_VISIBLE_DEVICES=0 ncu → warn" 2 \
    'CUDA_VISIBLE_DEVICES=0 ncu --set full python test_kernel.py'

run_test "CUDA_VISIBLE_DEVICES=1 python torch script → warn" 2 \
    'CUDA_VISIBLE_DEVICES=1 python run_cuda_test.py'

# ══════════════════════════════════════════════════
echo ""
echo "== Bug S7b: Prefixed vllm/torchrun — must be detected as GPU command =="
# ══════════════════════════════════════════════════

# vllm/torchrun with env prefix should be detected (the old ^\s* anchor missed these)
run_test "CUDA_VISIBLE_DEVICES=0 vllm bench latency → warn" 2 \
    'CUDA_VISIBLE_DEVICES=0 vllm bench latency --model /path/to/model'

run_test "CUDA_VISIBLE_DEVICES=0 torchrun script.py → warn" 2 \
    'CUDA_VISIBLE_DEVICES=0 torchrun --nproc_per_node=1 train.py'

run_test "FOO=bar vllm serve model → warn" 2 \
    'FOO=bar vllm serve /path/to/model --tp 1'

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Reservation pattern still allowed =="
# ══════════════════════════════════════════════════

run_test "Reservation pattern → allow" 0 \
    'CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1) && CUDA_VISIBLE_DEVICES=$CVD python benchmark.py'

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Empty CVD still allowed =="
# ══════════════════════════════════════════════════

run_test "CUDA_VISIBLE_DEVICES=\"\" python script → allow" 0 \
    'CUDA_VISIBLE_DEVICES="" python benchmark.py --cuda --no-gpu'

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Non-GPU commands pass through =="
# ══════════════════════════════════════════════════

run_test "grep command → allow" 0 \
    'grep -r "kernel" .'

run_test "cat file → allow" 0 \
    'cat state.json'

run_test "git log → allow" 0 \
    'git log --oneline -10'

run_test "ls command → allow" 0 \
    'ls -la'

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Bare vllm/torchrun still detected =="
# ══════════════════════════════════════════════════

run_test "vllm bench latency (bare) → warn" 2 \
    'vllm bench latency --model /path/to/model'

run_test "torchrun script (bare) → warn" 2 \
    'torchrun --nproc_per_node=1 train.py'

# ══════════════════════════════════════════════════
echo ""
echo "== Regression: Import checks exempted =="
# ══════════════════════════════════════════════════

run_test "python -c 'import torch' → allow" 0 \
    "python -c 'import torch'"

# ══════════════════════════════════════════════════
echo ""
echo "== Bypass: an inspection PREFIX must not disable the guard =="
# ══════════════════════════════════════════════════
# The read-only fast path used an ANCHORED regex on the start of the command
# string, so ANY inspection prefix reached `exit 0` before the GPU-pool and
# worktree-venv blocks ran. Every case below is exit 0 on the pre-fix hook.
# Now the fast path requires EVERY segment to be inspection-class
# (hook_cmd_classify.py --mode readonly).

run_test "cat && vllm bench latency → warn (prefix must not license the tail)" 2 \
    'cat notes.md && vllm bench latency --model /m'

run_test "echo; nsys profile → warn (glued ; separator)" 2 \
    'echo starting; nsys profile python bench.py'

run_test "true && nvidia-smi --query-compute → warn" 2 \
    'true && nvidia-smi --query-compute-apps=pid --format=csv'

run_test "grep && sweep script → warn" 2 \
    'grep -q model target.json && python run_bench_kernel.py'

run_test "jq | torchrun → warn (pipe segment)" 2 \
    'jq -r .model target.json | xargs torchrun train.py'

# The fast path must still fire when the command really is all-inspection,
# including multi-segment pipelines.
run_test "cat | grep | wc → allow (every segment inspection-class)" 0 \
    'cat state.json | grep status | wc -l'

run_test "ls && git status → allow (both segments inspection-class)" 0 \
    'ls .ammo && git status'

# ══════════════════════════════════════════════════
echo ""
echo "== Campaign scope =="
# ══════════════════════════════════════════════════

# No .ammo/active above the project dir or the input cwd: silent, exit 0,
# no one-shot flag written.
run_raw "$NOCAMP" "$(jq -n --arg c "$NOCAMP" '{hook_event_name:"PreToolUse",tool_name:"Bash",cwd:$c,tool_input:{command:"vllm bench latency --model /m"}}')" \
    CLAUDE_SESSION_ID=test-session
check "no campaign: GPU command without reservation → exit 0, silent" \
    "$([ "$actual_exit" -eq 0 ] && [ ! -s "$TMPDIR/hook-stdout" ] && [ ! -s "$TMPDIR/hook-stderr" ] && echo yes || echo no)"
check "no campaign: no one-shot flag written" \
    "$(ls "$GPU_RES_DIR"/.warned_* >/dev/null 2>&1 && echo no || echo yes)"

# No CLAUDE_SESSION_ID in env: the one-shot key is the hook input's session_id.
run_raw "$TMPDIR" "$(jq -n '{hook_event_name:"PreToolUse",tool_name:"Bash",session_id:"payload-sid",tool_input:{command:"vllm bench latency --model /m"}}')" \
    -u CLAUDE_SESSION_ID
check "no CLAUDE_SESSION_ID: pool guard still fires (exit 2)" \
    "$([ "$actual_exit" -eq 2 ] && echo yes || echo no)"
check "no CLAUDE_SESSION_ID: flag keyed by input session_id" \
    "$([ -f "$GPU_RES_DIR/.warned_payload-sid" ] && echo yes || echo no)"
check "pool guard message names the plugin's reserve command" \
    "$(grep -qF "$(cd "$(dirname "$HOOK")/.." && pwd)/scripts/ammo gpu_reservation.py reserve" "$TMPDIR/hook-stderr" && echo yes || echo no)"

# Worktree venv guard: python in a track worktree without its venv → one-shot
# block that tells the agent to check `import pumice`.
WT="$TMPDIR/.claude/worktrees/op001"
mkdir -p "$WT"
run_raw "$TMPDIR" "$(jq -n --arg c "$WT" '{hook_event_name:"PreToolUse",tool_name:"Bash",cwd:$c,tool_input:{command:"python analyze.py"}}')" \
    CLAUDE_SESSION_ID=test-session
check "worktree venv: python without the worktree venv → exit 2" \
    "$([ "$actual_exit" -eq 2 ] && grep -q 'AMMO WORKTREE VENV' "$TMPDIR/hook-stderr" && echo yes || echo no)"
check "worktree venv: message checks import pumice" \
    "$(grep -q 'import pumice; print(pumice.__file__)' "$TMPDIR/hook-stderr" && echo yes || echo no)"
run_raw "$TMPDIR" "$(jq -n --arg c "$WT" '{hook_event_name:"PreToolUse",tool_name:"Bash",cwd:$c,tool_input:{command:"python -c \"import pumice; print(pumice.__file__)\""}}')" \
    CLAUDE_SESSION_ID=test-session
# The venv guard is exempt; the pool guard after it may still fire, as the
# classifier decides, so only the venv message is checked.
check "worktree venv: bare import pumice check is exempt from the venv guard" \
    "$(grep -q 'AMMO WORKTREE VENV' "$TMPDIR/hook-stderr" && echo no || echo yes)"

# ══════════════════════════════════════════════════
echo ""
echo "== N1: Pumice compile knobs warn on CUDA =="
# ══════════════════════════════════════════════════

# --no-compile and PUMICE_COMPILE_TARGET=0 turn off torch.compile on the
# Pumice runner (references/torch-compile-contract.md). The reservation
# pattern keeps the pool guard quiet, so only the N1 reminder is checked.
_RES='CVD=$(python scripts/gpu_reservation.py reserve --num-gpus 1) && CUDA_VISIBLE_DEVICES=$CVD '
run_raw "$TMPDIR" "$(jq -n --arg c "${_RES}python -m pumice.serve --model /m --no-compile" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    CLAUDE_SESSION_ID=test-session
check "N1: --no-compile → reminder, exit 0" \
    "$([ "$actual_exit" -eq 0 ] && grep -qF 'AMMO REMINDER: --no-compile detected' "$TMPDIR/hook-stderr" && echo yes || echo no)"
run_raw "$TMPDIR" "$(jq -n --arg c "PUMICE_COMPILE_TARGET=0 ${_RES}python -m pumice.serve --model /m" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    CLAUDE_SESSION_ID=test-session
check "N1: PUMICE_COMPILE_TARGET=0 → reminder, exit 0" \
    "$([ "$actual_exit" -eq 0 ] && grep -qF 'AMMO REMINDER: PUMICE_COMPILE_TARGET=0 detected' "$TMPDIR/hook-stderr" && echo yes || echo no)"
run_raw "$TMPDIR" "$(jq -n --arg c "export PUMICE_COMPILE_TARGET=\"0\"; ${_RES}python -m pumice.serve --model /m" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    CLAUDE_SESSION_ID=test-session
check "N1: export PUMICE_COMPILE_TARGET=\"0\" → reminder" \
    "$(grep -qF 'AMMO REMINDER: PUMICE_COMPILE_TARGET=0 detected' "$TMPDIR/hook-stderr" && echo yes || echo no)"
run_raw "$TMPDIR" "$(jq -n --arg c "PUMICE_COMPILE_TARGET=2 ${_RES}python -m pumice.serve --model /m --no-compile-cache" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    CLAUDE_SESSION_ID=test-session
check "N1: PUMICE_COMPILE_TARGET=2 and --no-compile-cache → no compile reminder" \
    "$(grep -qE 'AMMO REMINDER: (--no-compile|PUMICE_COMPILE_TARGET=0) detected' "$TMPDIR/hook-stderr" && echo no || echo yes)"
run_raw "$TMPDIR" "$(jq -n --arg c "${_RES}python -m pumice.serve --model /m --no-compile" '{hook_event_name:"PreToolUse",tool_name:"Bash",tool_input:{command:$c}}')" \
    CLAUDE_SESSION_ID=test-session AMMO_ACCELERATOR_KIND=neuron
check "N1: Neuron runtime → no CUDA compile reminder" \
    "$(grep -qF 'AMMO REMINDER: --no-compile detected' "$TMPDIR/hook-stderr" && echo no || echo yes)"

# ══════════════════════════════════════════════════
echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
