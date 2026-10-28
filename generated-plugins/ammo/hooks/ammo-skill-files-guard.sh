#!/usr/bin/env bash
# PreToolUse/Edit|Write|MultiEdit|NotebookEdit — BLOCK subagents from editing
# orchestrator-owned plugin files.
#
# Protected: one prefix, the plugin root (the realpath of the dir that holds
# this hooks/ dir). It holds references/, orchestration/, agents/, skills/,
# hooks/, and the Mechanical Authorities scripts/ and schemas/. The file path
# is resolved first: a relative path against the tool call's cwd, then `..`
# and symlinks through realpath. The engine's own scripts/ or schemas/ dirs
# are the subagent's work and stay editable.
#
# audit-invariants.md § Mechanical Authorities names scripts/ and schemas/ as
# the two locations that decide whether a track passes. A subagent that can
# rewrite the state engine, the validation-gate verifier, or state.schema.json
# can pass itself. The team lead is not a subagent and stays exempt, so a real
# fix still lands in one turn.
#
# Campaign scope (_find_campaign.sh): outside a campaign the hook exits 0 and
# prints nothing.
#
# Orchestrator (team-lead) is exempt — it can modify these files freely.
# Subagents get a deny with a message telling them to surface the change upstream.
set -euo pipefail
trap 'exit 0' ERR

command -v jq >/dev/null 2>&1 || exit 0

INPUT=$(cat)

# Extract file path (Edit/Write/MultiEdit/NotebookEdit all use file_path).
FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' 2>/dev/null) || exit 0
[ -z "$FILE_PATH" ] && exit 0

HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HELPER_DIR/.." && pwd -P)"
source "$HELPER_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0

# Resolve the target: relative to the tool call's cwd, then realpath.
HOOK_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // ""' 2>/dev/null) || HOOK_CWD=""
REAL=$(python3 -c 'import os, sys; print(os.path.realpath(os.path.join(sys.argv[2], os.path.expanduser(sys.argv[1]))))' \
    "$FILE_PATH" "${HOOK_CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}" 2>/dev/null) || exit 0
[ -n "$REAL" ] || exit 0

# Only gate paths inside the plugin root.
case "$REAL" in
    "$PLUGIN_ROOT"/*) ;;
    *) exit 0 ;;
esac

# Orchestrator check — source the shared helper.
source "$HELPER_DIR/_ammo_is_lead.sh"

if _ammo_is_lead "$INPUT"; then
    exit 0
fi

# Subagent → deny.
jq -nc --arg path "$FILE_PATH" --arg root "$PLUGIN_ROOT" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: "deny",
    permissionDecisionReason: ("Subagents may not edit \($path). Files inside the AMMO plugin (\($root): references/, orchestration/, agents/, skills/, hooks/, scripts/, schemas/) are orchestrator-owned. scripts/ and schemas/ are the Mechanical Authorities that decide whether your own work passes, so editing them from inside a track is never the fix. Surface the proposed change to the orchestrator via SendMessage instead.")
  }
}'
exit 0
