#!/bin/bash
# PostToolUse hook — AMMO accelerator pool auto-release.
# Detects the reservation pattern in completed commands and releases
# all accelerator resources held by this session.
#
# Campaign scope (_find_campaign.sh): outside a campaign the hook exits 0 and
# prints nothing. The release runs scripts/gpu_reservation.py from this
# plugin through ammo_python.
set -euo pipefail
if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // .input.command // empty' 2>/dev/null) || true
[ -z "$COMMAND" ] && exit 0

# Only release for commands that used the reservation pattern.
# Pre-filter: match `gpu_reservation.py reserve` regardless of path prefix
# (`scripts/ammo gpu_reservation.py reserve`, `python /abs/scripts/gpu_reservation.py reserve`, etc.)
if ! echo "$COMMAND" | grep -Eq 'gpu_reservation\.py[[:space:]]+reserve([[:space:]]|$)'; then
    exit 0
fi

# Honor --no-auto-release: the reservation is lifecycle-bound, so skip release
# entirely. Same whole-command containment check as the Codex twin
# (codex/hooks/post_tool_use_guard.py _release_reserved_gpus) — keep in parity.
if echo "$COMMAND" | grep -qF -- '--no-auto-release'; then
    exit 0
fi

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0

# Extract ALL --session-id values from the command (supports both `--session-id foo`
# and `--session-id=foo` forms). Bounded char class stops at shell metachars like
# `)`, `&`, `;`, `"`, `'`, so wrapping in `$(...)` doesn't corrupt the id.
# The Python pattern is byte-identical to the Codex twin's regex
# (post_tool_use_guard.py `_reservation_session_ids`); keep them in sync:
#   - separator `(?:=|\s+)` consumes one `=` or a run of spaces, so
#     `--session-id  foo` (two spaces) extracts `foo` on both runtimes. A
#     fixed-width lookbehind takes exactly one separator and would miss it,
#     then fall through to CLAUDE_SESSION_ID and release the wrong session.
#   - valid id chars: alphanumerics, underscore, dot, colon, at, slash, plus, dash.
#   - no match cap: the Codex twin releases every match, so this must too.
SESSION_IDS=$(printf '%s' "$COMMAND" | python3 -c '
import re, sys
matches = re.findall(r"--session-id(?:=|\s+)([A-Za-z0-9_.:@/+\-]+)", sys.stdin.read())
print("\n".join(dict.fromkeys(matches)))
' 2>/dev/null) || true
# No --session-id: the reserve defaulted to AMMO_SESSION_ID (the campaign id,
# set by _find_campaign), so release that, as the Codex twin does.
if [ -z "$SESSION_IDS" ]; then
    SESSION_IDS="${AMMO_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
fi
[ -z "$SESSION_IDS" ] && exit 0

GPU_RES_DIR="${AMMO_GPU_RES_DIR:-/tmp/ammo_gpu_res}"
GPU_STATE="$GPU_RES_DIR/state.json"
[ -f "$GPU_STATE" ] || exit 0

# Audit log — every release attempt is recorded so leak investigations can
# distinguish "hook never fired" from "hook fired but extraction missed the id".
RELEASE_LOG="$GPU_RES_DIR/release.log"
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
mkdir -p "$GPU_RES_DIR"

# Release every extracted session id. Multiple matches can appear legitimately
# if one Bash tool call wraps multiple reserves; release them all.
# Use a while-read loop so newline-separated ids are handled safely.
while IFS= read -r SID; do
    [ -z "$SID" ] && continue
    printf '%s hook=PostToolUse sid=%s ' "$TS" "$SID" >> "$RELEASE_LOG"
    if ammo_python gpu_reservation.py release-session --session-id "$SID" >>"$RELEASE_LOG" 2>&1; then
        printf ' ok\n' >> "$RELEASE_LOG"
    else
        printf ' fail\n' >> "$RELEASE_LOG"
        if [ "${AMMO_ACCELERATOR_KIND:-}" = "neuron" ] || \
           [ "${AMMO_RUNTIME_PROFILE:-}" = "vllm-neuron" ] || \
           [ -n "${NEURON_VISIBLE_DEVICES+x}" ] || \
           [ -n "${NEURON_RT_VISIBLE_CORES+x}" ]; then
            echo "AMMO accelerator PostToolUse: release failed for $SID — will expire via lease." >&2
        else
            echo "AMMO GPU PostToolUse: release failed for $SID — will expire via lease." >&2
        fi
    fi
done <<< "$SESSION_IDS"

exit 0
