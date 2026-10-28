#!/bin/bash
# Stop hook — AMMO orchestrator continuation nudge.
#
# Fires for the ORCHESTRATOR (not teammates) when campaign artifacts exist
# and work remains. Exits silently when:
#   - No campaign (_find_campaign.sh finds no .ammo/active marker and no
#     AMMO_ARTIFACT_DIR), or the campaign dir has no state.json
#   - Campaign is terminal (complete/exhausted) AND REPORT.md exists
#
# Nudges at:
#   - Stage 7 (active): proceed to next round or set terminal status + 7b
#   - Stage 7 (terminal, no report): spawn report subagent (closes the 7b gap)
#   - Stage 7b: spawn report subagent
#
# Teammates are excluded via the agent_type JSON field (present for subagents,
# absent for the lead) with a fallback to session_id / team-config check.
#
# Uses file-based one-shot circuit breaker (keyed by session_id):
#   1st stop attempt: create marker file, nudge with stage-specific prompt
#   2nd stop attempt: marker file exists → allow through
set -euo pipefail
if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)

# ── Outside a campaign: allow the stop, say nothing ──
HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HELPER_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0

# ── Skip for subagents/teammates ──
# Delegated to the shared _ammo_is_lead helper. Contract: returns 0 for the
# lead orchestrator (or solo orchestrator fail-open), 1 for teammates /
# subagents. See _ammo_is_lead.sh for precedence details.
source "$HELPER_DIR/_ammo_is_lead.sh"
if ! _ammo_is_lead "$INPUT"; then
    exit 0
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)

# ── Circuit breaker ──
MARKER="/tmp/ammo-stop-nudged-${SESSION_ID}"
if [ -f "$MARKER" ]; then
    rm -f "$MARKER"
    exit 0
fi

# ── Campaign state ──
ARTIFACT_DIR="${AMMO_ARTIFACT_DIR%/}/"
STATE_FILE="${ARTIFACT_DIR}state.json"
[ -f "$STATE_FILE" ] || exit 0  # campaign dir without state yet

# ── Fully done check: terminal status + report exists → nothing left to do ──
STATUS=$(jq -r '.campaign.status // empty' "$STATE_FILE" 2>/dev/null)
if [[ "$STATUS" != "active" ]] && [[ -f "${ARTIFACT_DIR}REPORT.md" ]]; then
    exit 0
fi

# ── Paused check: user explicitly paused → allow stop ──
if [[ "$STATUS" = "paused" ]]; then
    exit 0
fi

STAGE=$(jq -r '.campaign.current_stage // "unknown"' "$STATE_FILE" 2>/dev/null)
ROUND=$(jq -r '.campaign.current_round // 1' "$STATE_FILE" 2>/dev/null)

# Stale-state reminders live in ammo-prompt-reminder.sh (UserPromptSubmit).
# Stop hooks cannot inject non-blocking context (hookSpecificOutput is
# silently dropped, and decision:block forces continuation).

# ── Stage-specific nudge ──
# Only nudge at stages where the orchestrator should keep going.
NUDGE=""
case "$STAGE" in
    7_campaign_eval*)
        if [ "$STATUS" = "active" ]; then
            NUDGE="You are at Stage 7 (Campaign Evaluation) and the campaign is still active, so keep going without asking the user.
Run the mechanical stop check from SKILL.md Stage 7: compare the current production baseline's bottleneck_mining.top_addressable_e2e_pct with campaign.config.min_e2e_improvement_pct in state.json.
- At or above the floor: open the next round and continue (mine the promoted baseline after a SHIP, pivot technology after EXHAUSTED).
- Below the floor: set campaign_complete or campaign_exhausted per SKILL.md, drain the roster, then run Stage 7b (spawn a fresh ammo-report-writer)."
        else
            # Terminal status but no REPORT.md (we passed the fully-done check above)
            NUDGE="Campaign is $STATUS but REPORT.md has not been generated.
Run Stage 7b now: spawn a fresh ammo-report-writer (subagent_type=ammo:ammo-report-writer) with artifact_dir=${ARTIFACT_DIR}; it reads skills/ammo-report/SKILL.md itself."
        fi
        ;;
    7b_report*|*report_gen*)
        NUDGE="You are at Stage 7b (Report Generation). Spawn a fresh ammo-report-writer (subagent_type=ammo:ammo-report-writer) with the artifact_dir now; the session cannot end until REPORT.md exists."
        ;;
esac

if [ -n "$NUDGE" ] && jq -e '.serving != null' "$STATE_FILE" >/dev/null 2>&1; then
    NUDGE="$NUDGE
This is a serving campaign. Use the objective-bound policy in references/serving-campaign.md: top_addressable_objective_pct and serving.min_improvement_pct, not the batch E2E formula. Missing bounds require investigation, not termination. The existing audit and fresh report requirements still apply."
fi
[ -z "$NUDGE" ] && exit 0

touch "$MARKER"

cat >&2 <<EOF
AMMO: Campaign at stage $STAGE, status $STATUS, round $ROUND.

$NUDGE
EOF
exit 2
