#!/usr/bin/env bash
# _find_campaign.sh -- sourced campaign discovery for AMMO hooks.
#
# A thin wrapper over scripts/campaign_paths.py, the one resolution layer
# (references/campaign-env.md lists every name it sets).
#
# Usage:
#   HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$HOOK_DIR/_find_campaign.sh"
#   _find_campaign "$INPUT" || exit 0          # not in a campaign: stay silent
#   ammo_in_campaign "$FILE_PATH" && ...       # the one path classifier
#   ammo_python ammo_state.py validate ...     # a plugin script, campaign python
#
# _find_campaign "$INPUT"
#   $1 = hook input JSON. The walk starts at its .cwd, then CLAUDE_PROJECT_DIR,
#   then $PWD; AMMO_ARTIFACT_DIR, when set, wins. On a hit it exports every
#   campaign name that is not set yet and returns 0. It returns 1 when there is
#   no campaign or anything fails (fail-open: the hook then does nothing).
# ammo_in_campaign PATH
#   Returns 0 when PATH resolves inside $AMMO_ARTIFACT_DIR (realpath), else 1.
# ammo_python SCRIPT [ARGS...]
#   Runs scripts/ammo: SCRIPT is a file in scripts/ or a path. Its output is
#   the script's output.
#
# Contract (same as _ammo_is_lead.sh): never writes stdout or stderr itself,
# never calls exit, needs only bash and python3, safe to source more than once.
# Call _find_campaign in a condition (|| or if) when the hook runs set -e.

if ! declare -F _find_campaign >/dev/null 2>&1; then

_ammo_fc_src="${BASH_SOURCE[0]:-}"
case "$_ammo_fc_src" in
    */*) _ammo_fc_hooks="${_ammo_fc_src%/*}" ;;
    *)   _ammo_fc_hooks="." ;;
esac
_ammo_fc_root="$(cd "$_ammo_fc_hooks/.." 2>/dev/null && pwd)" || _ammo_fc_root=""

_find_campaign() {
    local _input="${1:-}" _out=""
    [ -n "$_ammo_fc_root" ] || return 1
    command -v python3 >/dev/null 2>&1 || return 1
    _out="$(printf '%s' "$_input" | python3 "$_ammo_fc_root/scripts/campaign_paths.py" env --hook 2>/dev/null)" || _out=""
    case "$_out" in
        "export "*) ;;
        *) return 1 ;;
    esac
    eval "$_out" >/dev/null 2>&1 || return 1
    [ -n "${AMMO_ARTIFACT_DIR:-}" ] && [ -d "${AMMO_ARTIFACT_DIR}" ] || return 1
    return 0
}

ammo_in_campaign() {
    [ -n "${1:-}" ] && [ -n "${AMMO_ARTIFACT_DIR:-}" ] || return 1
    command -v python3 >/dev/null 2>&1 || return 1
    python3 "$_ammo_fc_root/scripts/campaign_paths.py" in-campaign "$1" >/dev/null 2>&1
}

ammo_python() {
    command -v python3 >/dev/null 2>&1 || return 127
    python3 "$_ammo_fc_root/scripts/ammo" "$@"
}

fi  # declare -F guard
