#!/bin/bash
# SessionStart hook for AMMO orchestrator
# Injects resume context after compaction. The briefing is for the lead.
#
# The matcher in hooks/hooks.json is empty, so this hook fires on every
# SessionStart in every process: the lead, named teammates, and in-process
# subagents of the lead when they compact.
#
# Outside a campaign (no <engine>/.ammo/active marker, no AMMO_ARTIFACT_DIR;
# see _find_campaign.sh) the hook exits 0 and prints nothing: the plugin can
# be enabled in every project, and a SessionStart there must stay untouched.
#
# Who can be told apart (Claude Code 2.1.281):
#   - Teammates run in their own process with --agent-type, so SessionStart
#     input carries .agent_type. _ammo_is_lead returns NOT lead (L1) and the
#     hook emits the empty document.
#   - In-process subagents (auditor, delegate, investigator spawned without a
#     name) fire SessionStart "compact" with the same input shape as the
#     lead: no .agent_id, no .agent_type, the lead's session_id and
#     transcript_path. The hook cannot tell them from the lead. Two rules
#     make that fire safe: the briefing opens with a line telling a spawned
#     agent to ignore it, and the hook never deletes the checkpoint, so a
#     subagent fire cannot take it from the lead. ammo-precompact.sh
#     rewrites the checkpoint at each PreCompact, and this hook reads it only
#     while it is fresh (CHECKPOINT_MAX_AGE_MIN).
#   - .agent_id is checked as a defensive extra. Tool events carry it for
#     in-process subagents; SessionStart on 2.1.281 does not.

# Read the hook input once so both the identity check and any downstream
# consumer can inspect it. `[ -t 0 ]` guards the interactive case where
# there is no piped stdin (test harnesses invoke the hook that way).
INPUT=""
if [ ! -t 0 ]; then
    INPUT=$(cat)
fi

emit_empty() {
    printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":""}}'
}

HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HELPER_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0
SKILL_MD="$(cd "$HELPER_DIR/.." && pwd)/skills/ammo/SKILL.md"
# shellcheck source=_ammo_is_lead.sh
if [ -f "$HELPER_DIR/_ammo_is_lead.sh" ]; then
    source "$HELPER_DIR/_ammo_is_lead.sh"
fi

# Defensive .agent_id check (see header). The helper does not read agent_id.
if command -v jq >/dev/null 2>&1; then
    AGENT_ID=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null || true)
    if [ -n "$AGENT_ID" ]; then
        emit_empty
        exit 0
    fi
fi

if declare -F _ammo_is_lead >/dev/null 2>&1; then
    if ! _ammo_is_lead "$INPUT"; then
        emit_empty
        exit 0
    fi
fi

# emit_context: print one hookSpecificOutput document, or the empty document
# when jq failed. Never print partial JSON; the harness never sees a half doc.
emit_context() {
    local doc
    if doc=$(jq -n "$@" 2>/dev/null) && [ -n "$doc" ]; then
        printf '%s\n' "$doc"
    else
        emit_empty
    fi
}

# First line of every briefing. An in-process subagent gets this briefing
# after its own compaction and must keep its task.
SPAWNED_NOTE="**This message is for the AMMO lead only.** If another agent spawned you with a task (for example an auditor, delegate, investigator, or champion), ignore this message and continue that task. Do not act as the lead."

# Clean up stale GPU reservation warning flags from previous sessions.
# Preserve the current session's flag so post-compaction restarts don't
# re-trigger the one-shot CVD warning. The key is the one
# ammo-pretool-guard.sh writes: CLAUDE_SESSION_ID, else the hook input's
# session_id, else the campaign id.
_WARN_SID="${CLAUDE_SESSION_ID:-$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)}"
_WARN_SID="${_WARN_SID:-${AMMO_SESSION_ID:-}}"
_GRD="${AMMO_GPU_RES_DIR:-/tmp/ammo_gpu_res}"
if [ -n "$_WARN_SID" ]; then
    for _f in "$_GRD"/.warned_*; do
        [ -f "$_f" ] || continue
        [ "$_f" = "$_GRD/.warned_${_WARN_SID}" ] && continue
        rm -f "$_f"
    done
fi

# Read only a fresh checkpoint. PreCompact writes it just before the
# compaction that fires this hook, so an old one is from an earlier
# compaction and the state.json branch below is more accurate.
CHECKPOINT_MAX_AGE_MIN=30
CHECKPOINT_FILES=$(find "$AMMO_ARTIFACT_DIR" -maxdepth 1 -name "compaction_checkpoint.json" -mmin "-$CHECKPOINT_MAX_AGE_MIN" 2>/dev/null | head -1)

if [ -n "$CHECKPOINT_FILES" ]; then
    STATE_DIR=$(dirname "$CHECKPOINT_FILES")
    STATE_FILE="$STATE_DIR/state.json"

    MODEL=$(jq -r '.model // "unknown"' "$CHECKPOINT_FILES" 2>/dev/null)
    STAGE=$(jq -r '.stage // "unknown"' "$CHECKPOINT_FILES" 2>/dev/null)
    TEAM_NAME=$(jq -r '.team_name // "unknown"' "$CHECKPOINT_FILES" 2>/dev/null)
    DEBATE_TEAM=$(jq -r '.debate_team // ""' "$CHECKPOINT_FILES" 2>/dev/null)
    TRACK_COUNT=$(jq -r '.track_count // 0' "$CHECKPOINT_FILES" 2>/dev/null)
    CAMPAIGN_ROUND=$(jq -r '.campaign_round // 0' "$CHECKPOINT_FILES" 2>/dev/null)
    CAMPAIGN_STATUS=$(jq -r '.campaign_status // ""' "$CHECKPOINT_FILES" 2>/dev/null)
    CUMULATIVE_SPEEDUP=$(jq -r '.cumulative_speedup // 1.0' "$CHECKPOINT_FILES" 2>/dev/null)

    # Read current state for more context (v2 shape)
    if [ -f "$STATE_FILE" ]; then
        CURRENT_STATUS=$(jq -r '.campaign.status // "unknown"' "$STATE_FILE" 2>/dev/null)
        ROUND_SUMMARY=$(jq -r '
          (.campaign.current_round) as $cr |
          .campaign.rounds[$cr - 1].round_summary // ""
        ' "$STATE_FILE" 2>/dev/null)
    else
        CURRENT_STATUS="unknown"
        ROUND_SUMMARY=""
    fi

    # Build additional resume context for v2 features.
    # EXTRA_CONTEXT holds real newlines; jq --arg escapes them for JSON.
    EXTRA_CONTEXT=""
    if [ -n "$DEBATE_TEAM" ] && [ "$DEBATE_TEAM" != "" ]; then
        EXTRA_CONTEXT="${EXTRA_CONTEXT}
6. **Team active ($DEBATE_TEAM)**: Check campaign.rounds[current_round-1] for debate/champion status."
    fi
    if [ "$TRACK_COUNT" -gt 0 ] 2>/dev/null; then
        EXTRA_CONTEXT="${EXTRA_CONTEXT}
7. **Parallel tracks ($TRACK_COUNT active)**: Check campaign.rounds[current_round-1].parallel_tracks.tracks in state.json for worktree paths and GPU assignments."
    fi
    if [ -n "$CAMPAIGN_STATUS" ] && [ "$CAMPAIGN_STATUS" != "" ]; then
        EXTRA_CONTEXT="${EXTRA_CONTEXT}
8. **Campaign loop active**: Round $CAMPAIGN_ROUND | Status: $CAMPAIGN_STATUS | Cumulative speedup: ${CUMULATIVE_SPEEDUP}x. See Campaign Loop section in SKILL.md."
    fi

    # Leave the checkpoint in place. An in-process subagent fire looks like a
    # lead fire, so deleting here could take the checkpoint from the lead.

    # Build with jq (never shell interpolation): round_summary and other state
    # values hold agent prose, so a newline, quote or backslash would make an
    # interpolated heredoc invalid JSON and silently drop the whole briefing.
    emit_context \
        --arg spawned_note "$SPAWNED_NOTE" \
        --arg state_file "$STATE_FILE" \
        --arg skill_md "$SKILL_MD" \
        --arg extra "$EXTRA_CONTEXT" \
        --arg model "$MODEL" \
        --arg stage "$STAGE" \
        --arg status "$CURRENT_STATUS" \
        --arg summary "$ROUND_SUMMARY" \
        '{
          hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: (
              "# Session Resumed After Compaction\n\n"
              + $spawned_note + "\n\n"
              + "## You Are The AMMO Lead Orchestrator\n\n"
              + "This session was compacted while orchestrating an AMMO optimization.\n\n"
              + "### Immediate Actions\n\n"
              + "1. **Read the skill**: `" + $skill_md + "`\n"
              + "2. **Load state**: `cat " + $state_file + "`\n"
              + "3. **Resume current stage** — spawn subagents as needed"
              + $extra + "\n\n"
              + "### Model: " + $model + " | Stage: " + $stage + " | Status: " + $status + "\n"
              + "### Round summary: " + $summary + "\n\n"
              + "You are the LEAD — scaffold, delegate, gate. Do not implement directly."
            )
          }
        }'

else
    # No checkpoint — check for active state
    STATE_FILES=""
    [ -f "$AMMO_ARTIFACT_DIR/state.json" ] && STATE_FILES="$AMMO_ARTIFACT_DIR/state.json"

    if [ -n "$STATE_FILES" ]; then
        MODEL=$(jq -r '.target.model_id // "unknown"' "$STATE_FILES" 2>/dev/null)
        STAGE=$(jq -r '.campaign.current_stage // "unknown"' "$STATE_FILES" 2>/dev/null)

        emit_context \
            --arg spawned_note "$SPAWNED_NOTE" \
            --arg state_file "$STATE_FILES" \
            --arg skill_md "$SKILL_MD" \
            --arg model "$MODEL" \
            --arg stage "$STAGE" \
            '{
              hookSpecificOutput: {
                hookEventName: "SessionStart",
                additionalContext: (
                  "# AMMO Optimization Detected\n\n"
                  + $spawned_note + "\n\n"
                  + "Existing optimization state at: `" + $state_file + "`\n"
                  + "Model: " + $model + " | Stage: " + $stage + "\n\n"
                  + "If continuing this optimization:\n"
                  + "- Read the skill: `" + $skill_md + "`\n"
                  + "- You are the lead orchestrator — scaffold, delegate, gate. Do not implement directly."
                )
              }
            }'
    else
        cat << 'EMPTY_EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": ""
  }
}
EMPTY_EOF
    fi
fi
