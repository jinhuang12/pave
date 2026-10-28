#!/bin/bash
# Test harness for the compaction pair: ammo-precompact.sh (PreCompact) and
# ammo-postcompact.sh (SessionStart).
# Run: bash .claude/hooks/test-ammo-compaction.sh
#
# The pair carries the campaign across a compaction, so both documents must be
# valid JSON for every state value an agent can write. Cases:
#   1. Plain state → valid checkpoint + valid briefing
#   2. round_summary with newline + double-quote + backslash (the reported bug)
#   3. model / team_name with a newline and a quote
#   4. Missing parallel_tracks → track_count stays numeric
#   5. Corrupt state.json → both hooks still emit valid JSON
#   6. No AMMO state → empty briefing document
#   7. Atomic write leaves no temp file behind
#   8. Symlink checkpoint is refused
#   9. Broken jq → both hooks still emit valid JSON
#  10. Teammate fire (.agent_type set) → empty briefing, checkpoint stays
#  11. .agent_id + .agent_type (tool-event shape) → empty briefing
#  11b. .agent_id alone (defensive) → empty briefing
#  12. Lead fire → lead briefing, checkpoint left for PreCompact to rewrite
#  13. In-process subagent fire (same input as the lead) → briefing opens with
#      the "ignore if spawned" note, and a later lead fire still gets the
#      checkpoint
#  14. Stale checkpoint → state.json briefing, not the compaction briefing

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PRE="$HERE/ammo-precompact.sh"
POST="$HERE/ammo-postcompact.sh"
PASS=0
FAIL=0
TOTAL=0

TMPROOT=$(mktemp -d)
cleanup() { rm -rf "$TMPROOT"; }
trap cleanup EXIT

# ─────────────────────────────────────────────
# new_sandbox: fresh CLAUDE_PROJECT_DIR with one artifact dir
# echoes the sandbox root
# ─────────────────────────────────────────────
new_sandbox() {
    local sb
    sb=$(mktemp -d "$TMPROOT/sb.XXXXXX")
    mkdir -p "$sb/kernel_opt_artifacts/tgt"
    echo "$sb"
}

STATE_REL="kernel_opt_artifacts/tgt/state.json"
CHECKPOINT_REL="kernel_opt_artifacts/tgt/compaction_checkpoint.json"

# ─────────────────────────────────────────────
# write_state: build a state.json with the given model / team / summary.
#   $1 = sandbox  $2 = model  $3 = team_name  $4 = round_summary
#   $5 = (optional) "no_tracks" to omit parallel_tracks entirely
# ─────────────────────────────────────────────
write_state() {
    local sb="$1" model="$2" team="$3" summary="$4" mode="${5:-}"
    MODEL="$model" TEAM="$team" SUMMARY="$summary" MODE="$mode" \
    python3 - "$sb/$STATE_REL" <<'PY'
import json, os, sys
rnd = {"team_name": os.environ["TEAM"], "round_summary": os.environ["SUMMARY"],
       "status": "in_progress"}
if os.environ.get("MODE") != "no_tracks":
    rnd["parallel_tracks"] = {"tracks": [{"id": "op-001"}, {"id": "op-002"}]}
doc = {"target": {"model_id": os.environ["MODEL"]},
       "campaign": {"current_round": 1, "current_stage": "implement",
                    "status": "in_progress", "cumulative_speedup_vs_round1": 1.23,
                    "rounds": [rnd]}}
with open(sys.argv[1], "w") as fh:
    json.dump(doc, fh, indent=2)
PY
}

report() {
    local ok="$1" name="$2" detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    if [ "$ok" = "1" ]; then
        PASS=$((PASS + 1))
        echo "  PASS: $name"
    else
        FAIL=$((FAIL + 1))
        echo "  FAIL: $name ${detail:+— $detail}"
    fi
}

# ─────────────────────────────────────────────
# run_pair: run precompact then postcompact in $1; assert both JSON docs parse.
#   $2 = case name
#   $3 = (optional) hook input JSON piped to postcompact stdin. Omit or pass
#        "" to use the empty stdin the lead sees.
# A copy of the checkpoint is kept in $sb/checkpoint.snapshot.json. The briefing lands in $sb/out.json.
# ─────────────────────────────────────────────
run_pair() {
    local sb="$1" name="$2" post_input="${3:-}" rc=0
    CLAUDE_PROJECT_DIR="$sb" bash "$PRE" </dev/null >/dev/null 2>&1 || rc=$?
    report "$([ "$rc" = "0" ] && echo 1 || echo 0)" "$name: precompact exit 0" "exit $rc"

    cp "$sb/$CHECKPOINT_REL" "$sb/checkpoint.snapshot.json" 2>/dev/null || true
    if jq empty "$sb/$CHECKPOINT_REL" 2>/dev/null; then
        report 1 "$name: checkpoint is valid JSON"
    else
        report 0 "$name: checkpoint is valid JSON" "$(head -5 "$sb/$CHECKPOINT_REL" 2>/dev/null)"
    fi

    rc=0
    if [ -n "$post_input" ]; then
        CLAUDE_PROJECT_DIR="$sb" bash "$POST" >"$sb/out.json" 2>/dev/null <<<"$post_input" || rc=$?
    else
        CLAUDE_PROJECT_DIR="$sb" bash "$POST" </dev/null >"$sb/out.json" 2>/dev/null || rc=$?
    fi
    report "$([ "$rc" = "0" ] && echo 1 || echo 0)" "$name: postcompact exit 0" "exit $rc"

    if jq empty "$sb/out.json" 2>/dev/null; then
        report 1 "$name: briefing is valid JSON"
    else
        report 0 "$name: briefing is valid JSON" "$(head -5 "$sb/out.json")"
    fi
}

echo "=== Case 1: plain state ==="
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Round one shipped one optimization."
run_pair "$SB" "plain"
SUMMARY_OUT=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out.json")
case "$SUMMARY_OUT" in
    *"Round one shipped one optimization."*) report 1 "plain: summary reaches briefing" ;;
    *) report 0 "plain: summary reaches briefing" "$SUMMARY_OUT" ;;
esac

echo "=== Case 2: round_summary with newline + double-quote + backslash ==="
SB=$(new_sandbox)
ADVERSARIAL='Line one of the summary.
Line two says "quoted" and holds a backslash \ plus a tab	here.'
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "$ADVERSARIAL"
run_pair "$SB" "adversarial summary"
BRIEF=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out.json")
case "$BRIEF" in
    *'Line two says "quoted" and holds a backslash \ '*)
        report 1 "adversarial summary: text survives round-trip" ;;
    *) report 0 "adversarial summary: text survives round-trip" "$BRIEF" ;;
esac

echo "=== Case 3: model + team_name with newline and quote ==="
SB=$(new_sandbox)
write_state "$SB" 'Qwen "Q3"
32B \ fork' 'team
-r1' "Plain summary."
run_pair "$SB" "adversarial model/team"
MODEL_OUT=$(jq -r '.model' "$SB/checkpoint.snapshot.json")
case "$MODEL_OUT" in
    *'Qwen "Q3"'*) report 1 "adversarial model/team: model survives checkpoint" ;;
    *) report 0 "adversarial model/team: model survives checkpoint" "$MODEL_OUT" ;;
esac

echo "=== Case 4: no parallel_tracks → numeric track_count ==="
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "No tracks yet." "no_tracks"
run_pair "$SB" "no tracks"
TC_TYPE=$(jq -r '.track_count | type' "$SB/checkpoint.snapshot.json")
report "$([ "$TC_TYPE" = "number" ] && echo 1 || echo 0)" "no tracks: track_count is a number" "type=$TC_TYPE"

echo "=== Case 5: corrupt state.json ==="
SB=$(new_sandbox)
printf '{ this is not json' > "$SB/$STATE_REL"
run_pair "$SB" "corrupt state"

echo "=== Case 6: no AMMO state → empty briefing ==="
SB=$(mktemp -d "$TMPROOT/sb.XXXXXX")
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "no state: precompact exit 0" "exit $RC"
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" </dev/null >"$SB/out.json" 2>/dev/null || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "no state: postcompact exit 0" "exit $RC"
if jq -e '.hookSpecificOutput.additionalContext == ""' "$SB/out.json" >/dev/null 2>&1; then
    report 1 "no state: briefing is the empty document"
else
    report 0 "no state: briefing is the empty document" "$(head -5 "$SB/out.json")"
fi

echo "=== Case 7: atomic write leaves no temp file ==="
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Atomic write check."
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
LEFTOVER=$(find "$SB/kernel_opt_artifacts/tgt" -name '.compaction_checkpoint.json.*' | wc -l)
report "$([ "$LEFTOVER" = "0" ] && echo 1 || echo 0)" "atomic write: no temp file left" "found $LEFTOVER"

echo "=== Case 8: symlink checkpoint is refused ==="
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Symlink refusal check."
VICTIM="$SB/victim.json"
printf '{"keep":"me"}' > "$VICTIM"
ln -s "$VICTIM" "$SB/$CHECKPOINT_REL"
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
if [ "$(cat "$VICTIM")" = '{"keep":"me"}' ]; then
    report 1 "symlink: target left untouched"
else
    report 0 "symlink: target left untouched" "$(cat "$VICTIM")"
fi

echo "=== Case 9: broken jq → both hooks still emit valid JSON ==="
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Broken jq check."
FAKEBIN="$SB/fakebin"
mkdir -p "$FAKEBIN"
printf '#!/bin/sh\nexit 127\n' > "$FAKEBIN/jq"
chmod +x "$FAKEBIN/jq"
RC=0
CLAUDE_PROJECT_DIR="$SB" PATH="$FAKEBIN:$PATH" bash "$PRE" </dev/null >/dev/null 2>&1 || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "broken jq: precompact exit 0" "exit $RC"
RC=0
CLAUDE_PROJECT_DIR="$SB" PATH="$FAKEBIN:$PATH" bash "$POST" </dev/null >"$SB/out.json" 2>/dev/null || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "broken jq: postcompact exit 0" "exit $RC"
if jq empty "$SB/out.json" 2>/dev/null; then
    report 1 "broken jq: briefing is valid JSON"
else
    report 0 "broken jq: briefing is valid JSON" "$(head -3 "$SB/out.json")"
fi

echo "=== Case 10: non-lead fire — .agent_type set → empty briefing, checkpoint stays ==="
# The lead posts a checkpoint at PreCompact time. Teammates and in-process
# subagents also fire SessionStart; they must not consume the checkpoint
# and must not see the "You Are The AMMO Lead Orchestrator" briefing.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Non-lead fire check."
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" >"$SB/out.json" 2>/dev/null <<<'{"agent_type":"ammo-auditor","session_id":"s-non-lead"}' || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "non-lead agent_type: postcompact exit 0" "exit $RC"
if jq -e '.hookSpecificOutput.additionalContext == ""' "$SB/out.json" >/dev/null 2>&1; then
    report 1 "non-lead agent_type: briefing is the empty document"
else
    report 0 "non-lead agent_type: briefing is the empty document" "$(head -3 "$SB/out.json")"
fi
if [ -f "$SB/$CHECKPOINT_REL" ]; then
    report 1 "non-lead agent_type: checkpoint left in place for the real lead"
else
    report 0 "non-lead agent_type: checkpoint left in place for the real lead" "checkpoint was deleted"
fi

echo "=== Case 11: non-lead fire — .agent_id + .agent_type (tool-event shape) → empty briefing, checkpoint stays ==="
# Tool events carry both fields for in-process subagents. SessionStart on
# CC 2.1.281 sends neither for them (see Case 13); this locks the gate if a
# later version adds them.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "In-process subagent check."
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" >"$SB/out.json" 2>/dev/null <<<'{"agent_id":"agent-af197bf","agent_type":"ammo-auditor","session_id":"s-inproc"}' || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "non-lead agent_id: postcompact exit 0" "exit $RC"
if jq -e '.hookSpecificOutput.additionalContext == ""' "$SB/out.json" >/dev/null 2>&1; then
    report 1 "non-lead agent_id: briefing is the empty document"
else
    report 0 "non-lead agent_id: briefing is the empty document" "$(head -3 "$SB/out.json")"
fi
if [ -f "$SB/$CHECKPOINT_REL" ]; then
    report 1 "non-lead agent_id: checkpoint left in place for the real lead"
else
    report 0 "non-lead agent_id: checkpoint left in place for the real lead" "checkpoint was deleted"
fi

echo "=== Case 11b: defensive — .agent_id alone (no agent_type) → empty briefing ==="
# CC 2.1.281 does not send this shape on SessionStart. The case locks the
# hook's own .agent_id check, which the helper (L1 reads agent_type) lacks.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Agent-id-only check."
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" >"$SB/out.json" 2>/dev/null <<<'{"agent_id":"agent-adbd6bc","session_id":"s-idonly"}' || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "agent_id-only: postcompact exit 0" "exit $RC"
if jq -e '.hookSpecificOutput.additionalContext == ""' "$SB/out.json" >/dev/null 2>&1; then
    report 1 "agent_id-only: briefing is the empty document"
else
    report 0 "agent_id-only: briefing is the empty document" "$(head -3 "$SB/out.json")"
fi
if [ -f "$SB/$CHECKPOINT_REL" ]; then
    report 1 "agent_id-only: checkpoint left in place for the real lead"
else
    report 0 "agent_id-only: checkpoint left in place for the real lead" "checkpoint was deleted"
fi

echo "=== Case 12: lead fire — no agent fields on stdin → briefing is delivered ==="
# The real lead's SessionStart fires without .agent_type / .agent_id.
# Verify the resume briefing still lands so the lead can pick up its work.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Lead resume briefing check."
run_pair "$SB" "lead resume" '{"session_id":"s-lead","source":"compact"}'
BRIEF=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out.json")
case "$BRIEF" in
    *"You Are The AMMO Lead Orchestrator"*)
        report 1 "lead resume: briefing carries the lead header" ;;
    *)
        report 0 "lead resume: briefing carries the lead header" "$BRIEF" ;;
esac
if [ -f "$SB/$CHECKPOINT_REL" ]; then
    report 1 "lead resume: checkpoint left for PreCompact to rewrite"
else
    report 0 "lead resume: checkpoint left for PreCompact to rewrite" "checkpoint was deleted"
fi

echo "=== Case 13: in-process subagent fire (lead-identical input) → safe briefing, lead still resumes ==="
# CC 2.1.281 builds SessionStart "compact" input for an in-process subagent
# with the lead's session_id and transcript_path and no agent fields, so the
# hook cannot tell it from the lead. The briefing must tell a spawned agent
# to ignore it, and the checkpoint must survive for the lead's own fire.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Subagent compaction check."
SUBAGENT_INPUT='{"session_id":"lead-sid","transcript_path":"/nonexistent","hook_event_name":"SessionStart","source":"compact","model":"claude-opus-5-5"}'
run_pair "$SB" "in-process subagent" "$SUBAGENT_INPUT"
BRIEF=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out.json")
case "$BRIEF" in
    *"If another agent spawned you with a task"*"ignore this message and continue that task"*)
        report 1 "in-process subagent: briefing tells a spawned agent to ignore it" ;;
    *)
        report 0 "in-process subagent: briefing tells a spawned agent to ignore it" "$BRIEF" ;;
esac
FIRST_LINE=$(printf '%s\n' "$BRIEF" | sed -n 3p)
case "$FIRST_LINE" in
    *"This message is for the AMMO lead only."*)
        report 1 "in-process subagent: note comes before the lead header" ;;
    *)
        report 0 "in-process subagent: note comes before the lead header" "$FIRST_LINE" ;;
esac
if [ -f "$SB/$CHECKPOINT_REL" ]; then
    report 1 "in-process subagent: checkpoint left in place"
else
    report 0 "in-process subagent: checkpoint left in place" "checkpoint was deleted"
fi
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" >"$SB/out2.json" 2>/dev/null <<<'{"session_id":"lead-sid","source":"compact"}' || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "lead after subagent: postcompact exit 0" "exit $RC"
BRIEF=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out2.json" 2>/dev/null || true)
case "$BRIEF" in
    *"# Session Resumed After Compaction"*"You Are The AMMO Lead Orchestrator"*)
        report 1 "lead after subagent: lead still gets the compaction briefing" ;;
    *)
        report 0 "lead after subagent: lead still gets the compaction briefing" "$BRIEF" ;;
esac

echo "=== Case 14: stale checkpoint → state.json briefing ==="
# The hook no longer deletes the checkpoint, so it reads one only while it is
# fresh. An old checkpoint is from an earlier compaction.
SB=$(new_sandbox)
write_state "$SB" "Qwen/Qwen3-32B" "team-r1" "Stale checkpoint check."
CLAUDE_PROJECT_DIR="$SB" bash "$PRE" </dev/null >/dev/null 2>&1 || true
touch -t 202001010000 "$SB/$CHECKPOINT_REL"
RC=0
CLAUDE_PROJECT_DIR="$SB" bash "$POST" </dev/null >"$SB/out.json" 2>/dev/null || RC=$?
report "$([ "$RC" = "0" ] && echo 1 || echo 0)" "stale checkpoint: postcompact exit 0" "exit $RC"
BRIEF=$(jq -r '.hookSpecificOutput.additionalContext' "$SB/out.json" 2>/dev/null || true)
case "$BRIEF" in
    *"# AMMO Optimization Detected"*"This message is for the AMMO lead only."*)
        report 1 "stale checkpoint: state.json briefing with the lead-only note" ;;
    *)
        report 0 "stale checkpoint: state.json briefing with the lead-only note" "$BRIEF" ;;
esac

echo ""
echo "=== Summary ==="
echo "$PASS / $TOTAL passed"
if [ "$FAIL" -gt 0 ]; then
    echo "FAILED: $FAIL"
    exit 1
fi
exit 0
