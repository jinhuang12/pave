#!/bin/bash
# PreToolUse hook — AMMO campaign parity reminders and accelerator pool guard.
#
# N1/N4 warnings: Warns (but does NOT block) when a Bash command contains
# patterns that violate AMMO non-negotiables.
#
# Accelerator pool guard: Blocks ONCE per session if a CUDA or Neuron command
# lacks the matching reservation pattern.
# Subsequent commands without the pattern are allowed through.
#
# Campaign scope (_find_campaign.sh): outside a campaign the hook exits 0 and
# prints nothing. The classifier is this plugin's scripts/hook_cmd_classify.py,
# run through ammo_python. The one-shot flags are keyed by the Claude session
# (CLAUDE_SESSION_ID, else the hook input's session_id, else the campaign id);
# ammo-postcompact.sh keeps the current key's flag.
set -euo pipefail
if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)
# Try both field names (Claude Code docs ambiguous: tool_input vs input)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // .input.command // empty' 2>/dev/null) || true
[ -z "$COMMAND" ] && exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HOOK_DIR/.." && pwd)"
source "$HOOK_DIR/_find_campaign.sh"
# Outside a campaign there is nothing to guard.
_find_campaign "$INPUT" || exit 0

CLASSIFY="$PLUGIN_ROOT/scripts/hook_cmd_classify.py"
RESERVE="$PLUGIN_ROOT/scripts/ammo gpu_reservation.py reserve"
GUARD_SID="${CLAUDE_SESSION_ID:-$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)}"
GUARD_SID="${GUARD_SID:-${AMMO_SESSION_ID:-}}"

# _is_inspection_only — true only when EVERY segment of $COMMAND is
# inspection-class. The old test was an ANCHORED regex on the start of the
# command string, so a single inspection prefix disabled the rest of this hook:
# `cat notes.md && vllm bench latency ...` took the fast path, silencing both
# hard blocks below (worktree venv + GPU pool). One python3 exec per hook call.
#
# Fail-CLOSED on a missing classifier or interpreter: no fast path, so the
# command falls through to the guard logic. That is the safe direction — the
# guards themselves stay one-shot and advisory-first.
_is_inspection_only() {
    [ -f "$CLASSIFY" ] || return 1
    command -v python3 &>/dev/null || return 1
    ammo_python hook_cmd_classify.py --mode readonly "$1" >/dev/null 2>&1
}

_is_accelerator_heavy() {
    if [ -f "$CLASSIFY" ] && command -v python3 &>/dev/null; then
        ammo_python hook_cmd_classify.py --mode accelerator "$1" >/dev/null 2>&1
        return $?
    fi
    # Partial-restore fallback. Keep the historical CUDA patterns and include
    # the Neuron execution/profile entrypoints plus the Pumice engine entry
    # points, gRPC bench runner, and `cargo build`. Keep this list in step with
    # _ACCELERATOR_EXECUTABLES in hook_cmd_classify.py. This path is advisory only.
    echo "$1" | grep -Eq '(^|[[:space:];|&])(nsys|ncu|torchrun|vllm|pumice-server|rust_grpc_server\.py|run_pumice_grpc_bench\.py|neuron-profile|neuron-profiler|neuron-explorer|nki)([[:space:]]|$)' \
        || echo "$1" | grep -Eq '(^|[[:space:];|&])cargo[[:space:]]+build([[:space:]]|$)'
}

# Neuron detection keys first on AMMO_ACCELERATOR_KIND (set by the Pumice
# images); the profile-name checks keep both the legacy vllm-neuron and the
# pumice-neuron aliases working.
_IS_NEURON_RUNTIME=false
if [ "${AMMO_ACCELERATOR_KIND:-}" = "neuron" ] || \
   [ "${AMMO_RUNTIME_PROFILE:-}" = "pumice-neuron" ] || \
   [ "${AMMO_RUNTIME_PROFILE:-}" = "vllm-neuron" ] || \
   [ -n "${NEURON_VISIBLE_DEVICES+x}" ] || \
   [ -n "${NEURON_RT_VISIBLE_CORES+x}" ]; then
    _IS_NEURON_RUNTIME=true
fi

# Suppress noisy warnings on read-only / inspection commands
if _is_inspection_only "$COMMAND"; then
    exit 0
fi

# N1: CUDA production-parity reminders. Neuron Gate 5.3a checks NEFF/bucket
# compilation and fallback evidence instead of CUDA graph flags.
if [ "$_IS_NEURON_RUNTIME" = "false" ]; then
    if echo "$COMMAND" | grep -qP 'TORCH_COMPILE_DISABLE\s*=\s*1'; then
        echo "AMMO REMINDER: TORCH_COMPILE_DISABLE=1 detected. AMMO non-negotiable N1 requires production parity (CUDA graphs + torch.compile). If this is a false positive (e.g., documentation or search), ignore this warning." >&2
    fi
    if echo "$COMMAND" | grep -qP '(--|=)enforce[_-]eager'; then
        echo "AMMO REMINDER: --enforce-eager detected. AMMO non-negotiable N1 requires CUDA graphs to be enabled. If this is a false positive (e.g., documentation or search), ignore this warning." >&2
    fi
    if echo "$COMMAND" | grep -qP 'VLLM_TORCH_COMPILE_LEVEL\s*=\s*[01](\s|$|")'; then
        echo "AMMO REMINDER: VLLM_TORCH_COMPILE_LEVEL < 2 detected. AMMO non-negotiable N1 requires torch.compile level ≥ 2. If this is a false positive (e.g., documentation or search), ignore this warning." >&2
    fi
fi

# N4: Sweep script mandate reminder
if echo "$COMMAND" | grep -qP 'vllm\s+bench\s+latency' && \
   ! echo "$COMMAND" | grep -q 'run_vllm_bench_latency_sweep'; then
    echo "AMMO REMINDER: Raw 'vllm bench latency' detected. AMMO non-negotiable N4 requires using the sweep script:" >&2
    echo "  $PLUGIN_ROOT/scripts/ammo run_vllm_bench_latency_sweep.py --artifact-dir \"\$AMMO_ARTIFACT_DIR\"" >&2
    echo "If this is a false positive (e.g., documentation or search), ignore this warning." >&2
fi

# N5: VLLM_OP* env contamination warning (warn-only, not blocking)
# Catches ad-hoc commands like `VLLM_OP001=1 python benchmark.py`.
# The sweep script's _sanitize_vllm_op_env() is the hard gate.
if echo "$COMMAND" | grep -qP 'VLLM_OP\d+=\s*(1|True|true)\b' && \
   ! echo "$COMMAND" | grep -q 'run_vllm_bench_latency_sweep'; then
    echo "AMMO REMINDER: VLLM_OP* env var set to True/1 detected in command." >&2
    echo "  Cross-track contamination prevention: VLLM_OP* flags must default to off." >&2
    echo "  The sweep script sanitizes VLLM_OP* from inherited env automatically." >&2
    echo "  If setting for a specific benchmark, use opt_env in target.json instead." >&2
fi

# ── Worktree venv activation guard ──
# When the agent's cwd is inside .claude/worktrees/<name>/, any python/pip/pytest
# invocation MUST run under that worktree's .venv. The engine's venv has an
# editable install that resolves `import pumice` to the ENGINE checkout's
# source tree — silently invalidating all profiling/benchmarks for the
# isolated op worktree. One-shot block per session; trust agent judgment after.
# Runs before the GPU-pool guard so a venv violation on a GPU command surfaces
# the more fundamental issue (wrong source tree) rather than being masked.
CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
case "$CWD" in
    */.claude/worktrees/*)
        WORKTREE_ROOT=$(echo "$CWD" | sed -E 's#(.*/\.claude/worktrees/[^/]+).*#\1#')
        EXPECTED_VENV="$WORKTREE_ROOT/.venv"

        if echo "$COMMAND" | grep -qP '\b(python3?|pytest|pip|uv)\b'; then
            _VENV_OK=false
            echo "$COMMAND" | grep -qF "source $EXPECTED_VENV/bin/activate" && _VENV_OK=true
            echo "$COMMAND" | grep -qF "$EXPECTED_VENV/bin/python"          && _VENV_OK=true
            echo "$COMMAND" | grep -qF "$EXPECTED_VENV/bin/pytest"          && _VENV_OK=true
            echo "$COMMAND" | grep -qF "$EXPECTED_VENV/bin/pip"             && _VENV_OK=true
            # Exempt: bare `import pumice` / `import vllm` check (used to verify activation worked)
            if echo "$COMMAND" | grep -qP '^\s*python3?\s+-c\s+["\x27]import\s+(pumice|vllm)["\x27;\s]*(print\((pumice|vllm)\.__file__\))?\s*["\x27]?\s*$'; then
                _VENV_OK=true
            fi

            _SID_RAW="$GUARD_SID"
            if [ "$_VENV_OK" = "false" ] && [ -n "$_SID_RAW" ]; then
                _GRD="${AMMO_GPU_RES_DIR:-/tmp/ammo_gpu_res}"
                mkdir -p "$_GRD"
                WARNED_VENV_FLAG="$_GRD/.warned_venv_${_SID_RAW}"
                if [ ! -f "$WARNED_VENV_FLAG" ]; then
                    touch "$WARNED_VENV_FLAG"
                    cat >&2 <<EOF
AMMO WORKTREE VENV: Python command in op worktree without activating the worktree-local venv.

You are in: $CWD
Expected venv: $EXPECTED_VENV

The engine's venv has an editable install that resolves 'import pumice'
to the ENGINE checkout's source tree — NOT your isolated edits. Running profiling
or benchmarks under the wrong venv silently invalidates the results.

Fix: prefix your command with
  source $EXPECTED_VENV/bin/activate &&

Or invoke python by absolute path: $EXPECTED_VENV/bin/python ...

Verify once after activation:
  python -c "import pumice; print(pumice.__file__)"
  # The path MUST contain '/.claude/worktrees/'

(This block fires only once per session.)
EOF
                    exit 2
                fi
            fi
        fi
        ;;
esac

# ── Accelerator Pool Pattern Guard ──
# Detect CUDA/Neuron execution commands that do not use the reservation pattern.
# One-shot warning per session (same mechanism as ammo-stop-guard.sh).

if ! _is_accelerator_heavy "$COMMAND"; then
    exit 0
fi

# If command uses the reservation pattern, allow through
if echo "$COMMAND" | grep -q 'gpu_reservation.py reserve'; then
    exit 0
fi

# Explicit CUDA_VISIBLE_DEVICES=<digits> WITHOUT reservation is a bypass —
# fall through to the one-shot warning so agents learn to use the reservation pattern.

# CUDA supports an explicit empty no-device value. Neuron has no documented
# empty visibility sentinel; an empty or unset Neuron value may expose all
# cores, so it must still pass through the reservation guard.
if echo "$COMMAND" | grep -Eq "CUDA_VISIBLE_DEVICES=(\"\"|'')"; then
    exit 0
fi

# Accelerator command without reservation pattern — one-shot warning
SESSION_ID="$GUARD_SID"
if [ -z "$SESSION_ID" ]; then
    exit 0  # No session ID — fail-open
fi

GPU_RES_DIR="${AMMO_GPU_RES_DIR:-/tmp/ammo_gpu_res}"
WARNED_FLAG="${GPU_RES_DIR}/.warned_${SESSION_ID}"
mkdir -p "$GPU_RES_DIR"

if [ ! -f "$WARNED_FLAG" ]; then
    touch "$WARNED_FLAG"
    if [ "$_IS_NEURON_RUNTIME" = "true" ]; then
        cat >&2 <<EOF
AMMO ACCELERATOR POOL: Neuron command detected without reservation.

Use the accelerator pool pattern before Neuron execution or profiling:

  NVD=\$($RESERVE --num-resources N) && NEURON_VISIBLE_DEVICES=\$NVD <your_vllm_or_profile_command>

For a direct single-process NKI/runtime command only, assign the same printed
range to NEURON_RT_VISIBLE_CORES. Do not use an empty Neuron visibility value
as a no-core sentinel.
(This warning fires only once per session.)
EOF
    else
        cat >&2 <<EOF
AMMO GPU POOL: GPU command detected without reservation.

Use the GPU pool pattern to acquire GPUs before running GPU commands:

  CVD=\$($RESERVE --num-gpus N) && CUDA_VISIBLE_DEVICES=\$CVD <your_command>

Or set CUDA_VISIBLE_DEVICES="" if no GPU is needed.
(This warning fires only once per session.)
EOF
    fi
    exit 2
else
    exit 0  # Already warned — trust agent judgment
fi

exit 0
