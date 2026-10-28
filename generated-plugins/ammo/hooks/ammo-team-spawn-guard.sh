#!/bin/bash
# PreToolUse hook — gate Agent spawns under the CC 2.1.281 implicit-team model.
#
# Two shapes of routing bug hide behind the `name` parameter:
#
# 1. Member types with NO name are unaddressable. Champions, impl-champions
#    and transcript-monitors join the session's single implicit team by
#    spawning with a `name`. Without one the lead cannot address them via
#    SendMessage({to: name}) or send the shutdown_request handshake, so the
#    guard has always required a non-empty name for these three types.
#
# 2. One-shot subagent types spawned WITH a name silently become teammates.
#    A named Agent spawn joins the caller's team as a member; its final
#    report routes to team-lead (the session lead), not to the caller. That
#    is exactly the delegate-report leak the trn2 harness triage observed
#    (constraints.md lines 1443-1470, 1795-1801, 1902-1920): auditors and
#    investigators — themselves in-process subagents of the lead — spawned
#    `ammo-delegate` with a name and never received the answers. The lead
#    saw them mid-turn instead.
#
# SKILL.md ("Team-member vs subagent contract", ~line 65) already declares
# `ammo-delegate`, `ammo-researcher`, `ammo-auditor`, `ammo-investigator`,
# `ammo-report-writer` and `ammo-resolver` to be one-shot subagents that
# must be spawned with `subagent_type` + `prompt` only, no `name`. This
# guard is the mechanical backstop for that contract.
#
# Rules:
#   - Member type (ammo-champion / ammo-impl-champion / ammo-transcript-monitor)
#     with empty `name` → DENY (unaddressable).
#   - Member type WITH a name → ALLOW (forms/joins the implicit team).
#   - `ammo-delegate` with a non-empty `name` → DENY, regardless of caller.
#     The contract has no delegate-with-a-name case and the trn2 evidence
#     shows every named delegate spawn leaked its report to the lead.
#   - `ammo-researcher`, `ammo-auditor`, `ammo-investigator`,
#     `ammo-report-writer`, `ammo-resolver` with a non-empty `name`, when the
#     caller is a subagent (`.agent_type` set) → DENY. Same routing bug
#     class; the trn2 evidence only recorded the delegate variant because
#     that is the one subagents actually invoked, but any named one-shot
#     spawned from a subagent leaks its report to the lead. The lead itself
#     may still spawn these types with a name (e.g. eval-pipeline named
#     helpers), so the rule only fires when the caller is a subagent.
#   - Any other `subagent_type` → ALLOW (general-purpose named helpers such
#     as the eval pipeline's causal-analyzer / transcript-grader are
#     legitimate and observed only from the lead).
#
# Deny format: {"hookSpecificOutput":{"hookEventName":"PreToolUse",
#              "permissionDecision":"deny","permissionDecisionReason":"..."}}
# Always exits 0 (the JSON drives the decision). Fail-open on any error.
set -euo pipefail
trap 'exit 0' ERR

if ! command -v jq >/dev/null 2>&1; then
    exit 0
fi

INPUT=$(cat)

# CALLER_IS_SUBAGENT: .agent_type at top level is populated when the hook
# fires from inside a subagent process. Empty on the orchestrator (lead).
# CC 2.1.281 also populates .agent_id on in-process subagents; either is a
# positive subagent signal.
AGENT_TYPE=$(echo "$INPUT" | jq -r '.agent_type // ""' 2>/dev/null) || exit 0
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // ""' 2>/dev/null) || exit 0
CALLER_IS_SUBAGENT=0
if [ -n "$AGENT_TYPE" ] || [ -n "$AGENT_ID" ]; then
    CALLER_IS_SUBAGENT=1
fi

SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // ""' 2>/dev/null) || exit 0
NAME=$(echo "$INPUT" | jq -r '.tool_input.name // ""' 2>/dev/null) || exit 0

emit_deny() {
    local reason="$1"
    jq -c -n --arg reason "$reason" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
        }
    }'
    exit 0
}

case "$SUBAGENT_TYPE" in
    ammo-champion|ammo-impl-champion|ammo-transcript-monitor)
        # Member types: only the lead spawns these. Inside a subagent the
        # rule is skipped so a delegate/investigator that quotes a member
        # type in unrelated prose is not blocked by a hypothetical spawn.
        if [ "$CALLER_IS_SUBAGENT" = "1" ]; then
            exit 0
        fi
        if [ -z "$NAME" ]; then
            emit_deny "AMMO spawn-guard: $SUBAGENT_TYPE must spawn with a non-empty tool_input.name so it is addressable via SendMessage({to: <name>}) and can receive the shutdown_request handshake. Add a descriptive name (e.g. 'champion-1', 'impl-op001', 'monitor-champion-1') and re-spawn. Do NOT pass team_name — the session has a single implicit team and team_name is ignored."
        fi
        exit 0
        ;;
    ammo-delegate)
        # SKILL.md contract: a delegate is a one-shot subagent, spawned with
        # subagent_type + prompt only. A named ammo-delegate joins the
        # caller's team and its final report routes to team-lead instead of
        # back to the caller (the observed trn2 harness bug). Deny for every
        # caller — the lead already omits the name in all evidence.
        if [ -n "$NAME" ]; then
            emit_deny "AMMO spawn-guard: ammo-delegate must spawn without tool_input.name so its final report returns to you. A named ammo-delegate joins the session's implicit team and its answer routes to the lead instead. Re-spawn with subagent_type='ammo-delegate' and prompt=... only; keep the identifier in the prompt if you need one for citation."
        fi
        exit 0
        ;;
    ammo-researcher|ammo-auditor|ammo-investigator|ammo-report-writer|ammo-resolver)
        # SKILL.md contract classes these as one-shot subagents. The lead
        # may still legitimately name them (rare, but not blocked), but a
        # subagent that names one hits the same routing leak as the delegate
        # case: the report routes to team-lead, not back to the subagent
        # that spawned it. The trn2 evidence only captured the delegate
        # variant because subagents only invoked delegates by name; the
        # failure mode is the same class for the whole set.
        if [ "$CALLER_IS_SUBAGENT" = "1" ] && [ -n "$NAME" ]; then
            emit_deny "AMMO spawn-guard: $SUBAGENT_TYPE spawned from inside a subagent must not carry tool_input.name. A named one-shot subagent joins the session's implicit team and its answer routes to the lead instead of back to you. Re-spawn with subagent_type='$SUBAGENT_TYPE' and prompt=... only."
        fi
        exit 0
        ;;
    *)
        # Every other type is either an out-of-scope AMMO role or a named,
        # addressable helper (eval pipeline causal-analyzer, transcript-grader).
        # ALLOW unconditionally.
        exit 0
        ;;
esac
