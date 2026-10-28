#!/usr/bin/env bash
# PostToolUse hook — injects Phase 2 (checklist verification) after auditor writes Phase 1.
#
# Fires on Write. Detects verdict file writes (rounds/N/audits/stage_*.md),
# uses decision:block to inject Phase 2 instructions (the only channel that
# reliably delivers context to subagents in Claude Code <=2.1.144).
# NOTE: PostToolUse "block" is informational only — the Write already succeeded.
# One-shot guard prevents re-firing after Phase 2 appends to the same file.
set -euo pipefail

if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)

FILE_PATH=$(jq -r '.tool_input.file_path // empty' <<<"$INPUT")

# Only verdict files (rounds/N/audits/stage_*.md)
case "$FILE_PATH" in
  */rounds/*/audits/stage_*.md) ;;
  *) exit 0 ;;
esac

# ── SENTINEL NAME CONTRACT (shared with audit-phase2-guard.sh) ──
# The sentinel path is EXACTLY:
#
#   ${SENTINEL_PREFIX}${SESSION_ID}__${AGENT_ID:-noagent}__${VERDICT_BASE}
#
# with VERDICT_BASE = basename of the verdict file without its .md suffix.
# The three fields are joined by a DOUBLE underscore, which is the whole point:
# the guard has to recover VERDICT_BASE from the filename, and a single `_` is
# ambiguous because session ids and agent ids both contain `_`. The guard used
# to strip only "${SESSION_ID}_", so VERDICT_BASE always kept an AG9_/noagent_
# prefix and its `find -path "*/audits/${VERDICT_BASE}.md"` never matched — the
# Stop block could never fire. Both hooks now split on `__`.
#
# Any change to this expression must land in BOTH hooks in the same edit;
# test-ammo-audit-phase2.sh asserts the pair agrees.
SENTINEL_PREFIX="/tmp/ammo_audit_phase2_injected_"
SESSION_ID=$(jq -r '.session_id // "unknown"' <<<"$INPUT")
AGENT_ID=$(jq -r '.agent_id // empty' <<<"$INPUT")
VERDICT_BASE=$(basename "$FILE_PATH" .md)
SENTINEL="${SENTINEL_PREFIX}${SESSION_ID}__${AGENT_ID:-noagent}__${VERDICT_BASE}"
if [[ -e "$SENTINEL" ]]; then exit 0; fi
touch "$SENTINEL"

# Discover invariants path. Prefer CLAUDE_PROJECT_DIR; fallback to dirname walk-up.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" ]] && [[ -f "${CLAUDE_PROJECT_DIR}/references/audit-invariants.md" ]]; then
  INVARIANTS="${CLAUDE_PROJECT_DIR}/references/audit-invariants.md"
else
  # Walk up 6 levels: stage_*.md -> audits -> N -> rounds -> target -> kernel_opt_artifacts -> worktree
  WORKTREE=$(dirname "$(dirname "$(dirname "$(dirname "$(dirname "$(dirname "$FILE_PATH")")")")")")
  INVARIANTS="${WORKTREE}/references/audit-invariants.md"
fi

# If invariants file doesn't exist, skip injection
[[ ! -f "$INVARIANTS" ]] && exit 0

jq -n --arg reason "Audit Phase 2 hook: the Phase 1 verdict file is on disk. This block delivers the Phase 2 instructions; it is not a tool error.

Read ${INVARIANTS} and complete Phase 2 as your role's section \"Phase 2 - Checklist Verification\" specifies: Pre-Check, the section for your stage, then Cross-Artifact Checks; delegate fanout for non-inline evidence; reconcile both phases; state residual risks; append \"## Phase 2\" and the final Overall verdict to this same file with Edit. The worst severity across both phases decides the verdict, and Phase 2 cannot downgrade a Phase 1 BLOCKING finding." \
  '{
    decision: "block",
    reason: $reason
  }'
