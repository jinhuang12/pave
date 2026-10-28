#!/bin/bash
# _ammo_msg_identity.sh — source-able helper for the AMMO message hooks:
# ammo-msg-check.sh, the reader. The sender mirror, ammo-msg-mirror.sh, used
# it too; that hook is not part of this plugin (hooks/README.md), and the
# namespace rules below still say what any channel writer must match.
#
# ── NAMESPACE KEY (sender and reader MUST agree) ─────────────────────────────
# The side channel is namespaced by the LEAD SESSION ID:
#   root    = ${AMMO_MSG_MIRROR_DIR:-/tmp/ammo-msg-mirror-<uid>}
#   channel = <root>/<lead sid>/<recipient>.jsonl
#   cursor  = <root>/.cursor-<lead sid>-<agent>.ts
# A teammate reads the lead sid from its own spawn argv (--parent-session-id);
# the lead uses its own hook-input session_id. Neither side consults a team
# config, so a stale or missing teams/*/config.json can no longer split the
# channel (on 2.1.231 every config's leadSessionId had no live session and the
# config-keyed lead match failed on every call). Team NAME is still resolved,
# but only for the secondary real-inbox source and for debug output.
#
# ── Measured contract, Claude Code 2.1.281 ───────────────────────────────────
# Container ammo-server-pumice-cuda (g6e-cdd), 2026-09-24, live tmux lead plus
# one named teammate; raw captures under logs/pumice-verify/20260924-0320/hooks/.
#   * Named teammates are SEPARATE processes (team config backendType "tmux",
#     parent pid = the tmux server), exec'd through CLAUDE_CODE_TEAMMATE_COMMAND:
#       /usr/bin/claude --agent-id <name>@<team> --agent-name <name>
#           --team-name session-<sid8> --agent-color <c>
#           --parent-session-id <lead sid> --agent-type <type> [--model <m>]
#   * Teammate hook stdin: its OWN session_id, its OWN top-level transcript
#     (<config>/projects/<proj>/<own sid>.jsonl; every line carries agentName
#     and teamName), agent_type = the TYPE (e.g. general-purpose), NO agent_id.
#   * Lead hook stdin: own session_id, own transcript, no agent_type, no
#     agent_id. Its argv carries none of the teammate flags.
#   * In-process (Task-tool) subagents of EITHER process: stdin carries the
#     PARENT's session_id and transcript_path, agent_type = type, and an
#     agent_id (17 hex chars) that the host process does not carry in argv. On
#     the pre-port hooks such a subagent resolved as its parent (transcript
#     agentName) and ran the parent's cursor logic.
#   * teams/session-<sid8>/config.json: leadSessionId equalled the live lead sid
#     in this run; members[].sessionId is null (cannot identify a teammate).
#   * The real inbox <teams>/<team>/inboxes/<agent>.json is pruned within ~0.9 s
#     of a send even while the recipient is mid tool call (20 Hz sampler:
#     non-empty 11:53:24.639 -> 11:53:25.505 UTC, then [] again). The mirror
#     channel is therefore the only mid-turn source; the inbox stays secondary.
#
# Usage:
#   HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   source "$HELPER_DIR/_ammo_msg_identity.sh"
#   ammo_msg_resolve_identity "$INPUT"
#
# ammo_msg_resolve_identity sets (always defined, possibly empty):
#   AGENT_NAME            mailbox name ("team-lead" for the orchestrator)
#   TEAM_NAME             team the agent belongs to (inbox path only)
#   RESOLVED_TRANSCRIPT   transcript path after EnterWorktree repair
#   TRANSCRIPT_IS_OWN     1 when RESOLVED_TRANSCRIPT is the agent's own
#   TEAMS_ROOT            ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/teams
#   AMMO_MSG_NS           namespace key = lead session id (see above)
#   AMMO_MSG_IS_LEAD      1 for the lead process, else 0
#   AMMO_MSG_OWN_SID      this process's own session id (hook input)
#   AMMO_MSG_HOOK_AGENT_ID  hook-input .agent_id (set for in-process subagents)
#   AMMO_MSG_ARGV_AGENT_ID  own --agent-id from argv (empty for the lead)
#   AMMO_MSG_ARGV_AGENT_TYPE own --agent-type from argv (empty for the lead)
#   AMMO_MSG_INPROC       1 when the hook fired inside an in-process subagent:
#                         hook agent_id is set and differs from the argv id
#   AMMO_MSG_ID_SOURCE    which rung decided: argv|argv-lead|transcript|
#                         sidecar|legacy|agent_type|config|none
#
# Identity precedence:
#   1. Spawn argv of the nearest ancestor claude process (live state, cannot
#      go stale): --agent-name => teammate, no --agent-name => lead.
#   2. Fallbacks, used ONLY when no claude ancestor argv is readable (harness
#      runs, unknown runtimes): transcript-head agentName/teamName, the
#      agent-<id>.meta.json sidecar (CC 2.1.19x), the legacy first-message
#      regex, hook-input .agent_type carrying an eligible NAME (2.1.198),
#      _ammo_is_lead + newest team config whose leadSessionId matches, then a
#      member scan across team configs for TEAM_NAME.
#
# Contract: functions never exit and never write stdout; callers own those
# decisions. Callers may define dbg() for debug lines; a no-op fallback is
# provided. Safe to source multiple times.

if ! declare -F ammo_msg_resolve_identity >/dev/null 2>&1; then

if ! declare -F dbg >/dev/null 2>&1; then
    dbg() { :; }
fi

_AMMO_MSG_HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$_AMMO_MSG_HELPER_DIR/_ammo_is_lead.sh" 2>/dev/null || true

# ── Platform shims ──────────────────────────────────────────────────────────
# Session images are Linux (GNU coreutils, flock present). The BSD spellings
# are kept because they are cheap and let the harness run on a developer Mac.
case "$(uname -s 2>/dev/null)" in
    Darwin|*BSD*) _AMMO_MSG_BSD=1 ;;
    *)            _AMMO_MSG_BSD="" ;;
esac
ammo_msg_stat_owner() {
    if [ -n "$_AMMO_MSG_BSD" ]; then stat -Lf '%u' "$1" 2>/dev/null
    else stat -Lc '%u' "$1" 2>/dev/null; fi
}
ammo_msg_stat_mode() {
    if [ -n "$_AMMO_MSG_BSD" ]; then stat -Lf '%OLp' "$1" 2>/dev/null
    else stat -Lc '%a' "$1" 2>/dev/null; fi
}
ammo_msg_stat_size() {
    if [ -n "$_AMMO_MSG_BSD" ]; then stat -Lf '%z' "$1" 2>/dev/null
    else stat -Lc '%s' "$1" 2>/dev/null; fi
}
# ISO-8601 UTC stamp for epoch seconds $1.
ammo_msg_iso_from_epoch() {
    if [ -n "$_AMMO_MSG_BSD" ]; then date -u -r "$1" +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null
    else date -u -d "@$1" +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null; fi
}
# ISO-8601 UTC stamp for now with milliseconds. Millisecond precision orders
# two sends inside one second; the reader sorts and compares cursors on this
# string. BSD date has no %3N and emits a literal "3N", so prefer perl there.
ammo_msg_iso_now_ms() {
    local ts
    if [ -z "$_AMMO_MSG_BSD" ]; then
        ts=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ 2>/dev/null)
        case "$ts" in *N*|'') ;; *) printf '%s' "$ts"; return 0 ;; esac
    fi
    ts=$(perl -MTime::HiRes=time -e 'my $t=time; my @g=gmtime(int $t);
        printf "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
        $g[5]+1900,$g[4]+1,$g[3],$g[2],$g[1],$g[0],($t-int $t)*1000;' 2>/dev/null)
    case "$ts" in
        [0-9][0-9][0-9][0-9]-*) printf '%s' "$ts" ;;
        *) date -u +%Y-%m-%dT%H:%M:%S.000Z 2>/dev/null ;;
    esac
}

# ── Advisory lock fallback ──────────────────────────────────────────────────
# flock(1) is present in the session images; the mkdir lock is the fallback for
# hosts without it. mkdir of an existing directory fails atomically on every
# POSIX filesystem, so exactly one contender wins. A lock whose recorded owner
# pid is gone is broken rather than waited on.
ammo_msg_lock_acquire() { # $1 = lock dir, $2 = timeout seconds (default 5)
    local d="${1:-}" t="${2:-5}" tries=0 limit owner
    [ -n "$d" ] || return 1
    limit=$(( t * 20 ))
    if [ -d "$d" ]; then
        owner=$(cat "$d/pid" 2>/dev/null)
        if [ -z "$owner" ] || ! kill -0 "$owner" 2>/dev/null; then
            rm -rf "$d" 2>/dev/null || true
        fi
    fi
    while ! mkdir "$d" 2>/dev/null; do
        tries=$(( tries + 1 ))
        [ "$tries" -ge "$limit" ] && return 1
        sleep 0.05 2>/dev/null || sleep 1
    done
    printf '%s' "$$" > "$d/pid" 2>/dev/null || true
    return 0
}
ammo_msg_lock_release() {
    [ -n "${1:-}" ] && rm -rf "$1" 2>/dev/null
    return 0
}

# ── Spawn argv of the nearest ancestor claude process ───────────────────────
# Linux reads /proc/<pid>/cmdline (NUL-separated, exactly tokenised). Without
# /proc fall back to `ps -o args=`, which joins argv with spaces; every value
# read here (--agent-name, --agent-id, --team-name, --parent-session-id,
# --agent-type) is space-free. Match on argv[0]'s basename, never a substring
# search for "claude": an intermediate shell's command line can embed paths
# like /home/<user>/.claude/... and would false-positive.
ammo_msg_proc_argv() {
    local pid="${1:-}"
    [ -n "$pid" ] || return 1
    if [ -r "/proc/$pid/cmdline" ]; then
        tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null
    else
        ps -o args= -p "$pid" 2>/dev/null | head -1 | tr ' ' '\n'
    fi
}
ammo_msg_ancestor_claude_argv() {
    local pid="${1:-$PPID}" depth=0 argv argv0 base argv1 base1
    while [ -n "$pid" ] && [ "$pid" != "0" ] && [ "$pid" != "1" ] && [ "$depth" -lt 8 ]; do
        argv=$(ammo_msg_proc_argv "$pid")
        if [ -n "$argv" ]; then
            argv0=$(printf '%s\n' "$argv" | head -1)
            base="${argv0##*/}"
            case "$base" in
                claude|claude.exe)
                    printf '%s\n' "$argv"; return 0 ;;
                node|bun)
                    # A JS-launched CLI: argv[1] is the script (claude / cli.js).
                    argv1=$(printf '%s\n' "$argv" | sed -n 2p)
                    base1="${argv1##*/}"
                    case "$base1" in
                        claude|claude.exe|cli.js) printf '%s\n' "$argv"; return 0 ;;
                    esac ;;
            esac
        fi
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        depth=$((depth + 1))
    done
    return 1
}

# Strip anything that could escape a path component. Applied to every value
# that becomes a directory or file name — recipient names arrive from
# model-authored tool input, so "../../etc/x" must not steer a write.
ammo_msg_sanitize() {
    local s="${1:-}"
    s="${s//[^A-Za-z0-9._@-]/_}"
    s="${s//../_}"
    printf '%s' "${s:0:96}"
}

# Team names additionally get Claude Code's own dot→dash mangling so both
# hooks land on one canonical directory regardless of which form they saw.
ammo_msg_sanitize_team() {
    local t="${1:-}"
    t=$(printf '%s' "$t" | tr '.' '-')
    ammo_msg_sanitize "$t"
}

# Root of the side channel. Per-uid so two users never share a directory.
# Sessions in one server container share /tmp; channels are namespaced by the
# lead session id below the root, which is unique per session.
ammo_msg_mirror_root() {
    printf '%s' "${AMMO_MSG_MIRROR_DIR:-/tmp/ammo-msg-mirror-$(id -u 2>/dev/null || echo 0)}"
}

# Create the root 0700 and REFUSE to use it unless we own it and it is a real
# directory. The path is predictable, so another local user could pre-create
# it wide open and read or forge messages; a later chmod would fail with
# EPERM. Validate instead of assuming. Prints the root on success.
ammo_msg_ensure_root() {
    local root owner mode
    root=$(ammo_msg_mirror_root)
    [ -n "$root" ] || return 1
    install -d -m 700 "$root" 2>/dev/null || mkdir -p "$root" 2>/dev/null || return 1
    [ -L "$root" ] && return 1
    [ -d "$root" ] || return 1
    owner=$(ammo_msg_stat_owner "$root") || return 1
    [ -n "$owner" ] || return 1
    [ "$owner" = "$(id -u 2>/dev/null)" ] || return 1
    mode=$(ammo_msg_stat_mode "$root") || return 1
    [ -n "$mode" ] || return 1
    case "$mode" in
        700) ;;
        *) chmod 700 "$root" 2>/dev/null || return 1 ;;
    esac
    printf '%s' "$root"
}

# Channel file for (namespace, agent). Pure string; creates nothing.
ammo_msg_channel_file() {
    local ns agent
    ns=$(ammo_msg_sanitize "${1:-}")
    agent=$(ammo_msg_sanitize "${2:-}")
    { [ -n "$ns" ] && [ -n "$agent" ]; } || return 1
    printf '%s/%s/%s.jsonl' "$(ammo_msg_mirror_root)" "$ns" "$agent"
}

# Dedup cursor for (namespace, agent). Lives inside the validated 0700 root
# (not bare /tmp) so a planted file cannot mute an agent or force replays; the
# name starts with .cursor- so the mirror's TTL sweep leaves it alone.
ammo_msg_cursor_file() {
    local ns agent
    ns=$(ammo_msg_sanitize "${1:-}")
    agent=$(ammo_msg_sanitize "${2:-}")
    { [ -n "$ns" ] && [ -n "$agent" ]; } || return 1
    printf '%s/.cursor-%s-%s.ts' "$(ammo_msg_mirror_root)" "$ns" "$agent"
}

# True only when path exists, is a regular file, is not a symlink, and we own
# it. Used before trusting any channel or cursor file.
ammo_msg_own_file() {
    local p="${1:-}"
    [ -n "$p" ] || return 1
    [ -L "$p" ] && return 1
    [ -f "$p" ] || return 1
    [ -O "$p" ] || return 1
    return 0
}

# ── Eligibility ─────────────────────────────────────────────────────────────
# Who receives mid-turn injection. Sources (2026-09-24):
#   skills/ammo/SKILL.md:65             only ammo-champion, ammo-impl-champion and
#                                       ammo-transcript-monitor are team members
#   orchestration/parallel-tracks.md:34-35,77
#                                       impl-champion-{op_id}, monitor-impl-champion-{op_id}
#   hooks/ammo-monitor-reminder.sh:42   monitor-<champion name>
#   hooks/ammo-team-spawn-guard.sh:75   examples champion-1, impl-op001, monitor-champion-1
#   debate rounds                       champion-1..4, champion-001
# $1 = agent name, $2 = argv --agent-type (authoritative for the role when set).
# A plugin agent type carries the plugin prefix (ammo:ammo-champion); it is
# stripped before the match.
ammo_msg_eligible() {
    local name="${1:-}" atype="${2:-}"
    atype="${atype#ammo:}"
    case "$atype" in
        ammo-champion|ammo-impl-champion|ammo-transcript-monitor) return 0 ;;
    esac
    case "$name" in
        team-lead|champion-*|impl-champion-*|impl-*|monitor-champion-*|monitor-impl-champion-*|monitor-*|*champion*|*monitor*) return 0 ;;
    esac
    return 1
}

# leadSessionId recorded in the team config for team $1 (raw or dot→dash dir).
_ammo_msg_config_lead_sid() {
    local team="${1:-}" cfg
    [ -n "$team" ] || return 1
    for cfg in "$TEAMS_ROOT/$team/config.json" "$TEAMS_ROOT/$(ammo_msg_sanitize_team "$team")/config.json"; do
        if [ -f "$cfg" ]; then
            jq -r '.leadSessionId // empty' "$cfg" 2>/dev/null
            return 0
        fi
    done
    return 1
}

ammo_msg_resolve_identity() {
    local _input="${1:-}"
    AGENT_NAME=""
    TEAM_NAME=""
    RESOLVED_TRANSCRIPT=""
    TRANSCRIPT_IS_OWN=0
    TEAMS_ROOT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/teams"
    AMMO_MSG_NS=""
    AMMO_MSG_IS_LEAD=0
    AMMO_MSG_OWN_SID=""
    AMMO_MSG_HOOK_AGENT_ID=""
    AMMO_MSG_ARGV_AGENT_ID=""
    AMMO_MSG_ARGV_AGENT_TYPE=""
    AMMO_MSG_INPROC=0
    AMMO_MSG_ID_SOURCE="none"

    command -v jq >/dev/null 2>&1 || return 0

    local TRANSCRIPT_PATH AGENT_TYPE SESSION_ID
    TRANSCRIPT_PATH=$(printf '%s' "$_input" | jq -r '.transcript_path // ""' 2>/dev/null) || TRANSCRIPT_PATH=""
    AGENT_TYPE=$(printf '%s' "$_input" | jq -r '.agent_type // ""' 2>/dev/null) || AGENT_TYPE=""
    SESSION_ID=$(printf '%s' "$_input" | jq -r '.session_id // ""' 2>/dev/null) || SESSION_ID=""
    AMMO_MSG_HOOK_AGENT_ID=$(printf '%s' "$_input" | jq -r '.agent_id // ""' 2>/dev/null) || AMMO_MSG_HOOK_AGENT_ID=""
    AMMO_MSG_OWN_SID=$(ammo_msg_sanitize "$SESSION_ID")

    RESOLVED_TRANSCRIPT="$TRANSCRIPT_PATH"

    # ── EnterWorktree breaks transcript_path — try parent ──
    if [ -n "$TRANSCRIPT_PATH" ] && [ ! -f "$TRANSCRIPT_PATH" ]; then
        local PARENT_DIR PARENT_TRANSCRIPT
        PARENT_DIR=$(echo "$(dirname "$TRANSCRIPT_PATH")" | sed 's/--claude-worktrees-[^/]*//')
        PARENT_TRANSCRIPT="$PARENT_DIR/$(basename "$TRANSCRIPT_PATH")"
        if [ -f "$PARENT_TRANSCRIPT" ]; then
            dbg "worktree-fix: using parent transcript: $PARENT_TRANSCRIPT"
            RESOLVED_TRANSCRIPT="$PARENT_TRANSCRIPT"
        fi
    fi

    # ── Rung 1: spawn argv of the nearest ancestor claude process ──
    local argv="" prev="" a_name="" a_team="" a_psid="" a_aid="" a_type="" argv_found=0
    argv=$(ammo_msg_ancestor_claude_argv 2>/dev/null) || argv=""
    if [ -n "$argv" ]; then
        argv_found=1
        while IFS= read -r tok; do
            case "$prev" in
                --agent-name)        a_name="$tok" ;;
                --team-name)         a_team="$tok" ;;
                --parent-session-id) a_psid="$tok" ;;
                --agent-id)          a_aid="$tok" ;;
                --agent-type)        a_type="$tok" ;;
            esac
            prev="$tok"
        done <<EOF
$argv
EOF
    fi
    AMMO_MSG_ARGV_AGENT_ID="$a_aid"
    AMMO_MSG_ARGV_AGENT_TYPE="$a_type"

    # In-process subagent: the hook reports an agent_id that this process does
    # not carry on its command line. The test is a MISMATCH, not mere presence,
    # so it holds either way round: a teammate's own fires carry no agent_id
    # (measured) and would still match if Claude Code ever set it to the argv id.
    if [ -n "$AMMO_MSG_HOOK_AGENT_ID" ] && [ "$AMMO_MSG_HOOK_AGENT_ID" != "$AMMO_MSG_ARGV_AGENT_ID" ]; then
        AMMO_MSG_INPROC=1
    fi

    if [ "$argv_found" = "1" ]; then
        if [ -n "$a_name" ]; then
            AGENT_NAME=$(ammo_msg_sanitize "$a_name")
            TEAM_NAME="$a_team"
            AMMO_MSG_NS=$(ammo_msg_sanitize "$a_psid")
            AMMO_MSG_ID_SOURCE="argv"
            dbg "argv: teammate name=$AGENT_NAME team=$TEAM_NAME parent=${a_psid:0:8} type=$a_type"
        elif [ -z "$AGENT_TYPE" ] || [ "$AMMO_MSG_INPROC" = "1" ]; then
            # No --agent-name => the main process => the lead. An in-process
            # subagent of the lead lands here too (its argv IS the lead's); the
            # caller skips it via AMMO_MSG_INPROC, so its identity stays the
            # lead's rather than some guessed name.
            AGENT_NAME="team-lead"
            AMMO_MSG_IS_LEAD=1
            AMMO_MSG_NS="$AMMO_MSG_OWN_SID"
            AMMO_MSG_ID_SOURCE="argv-lead"
            dbg "argv: lead sid=${AMMO_MSG_OWN_SID:0:8}"
        else
            # claude ancestor without --agent-name but the hook says agent_type
            # and no agent_id: an unknown shape. Fall through to the fallbacks
            # rather than claim the lead's mailbox.
            dbg "argv: claude ancestor without --agent-name but agent_type=$AGENT_TYPE; using fallbacks"
        fi
    fi

    # ── Rung 2: fallbacks (no readable claude ancestor argv) ──
    if [ -z "$AGENT_NAME" ] && [ -n "$RESOLVED_TRANSCRIPT" ] && [ -f "$RESOLVED_TRANSCRIPT" ]; then
        local result
        result=$(head -5 "$RESOLVED_TRANSCRIPT" 2>/dev/null | jq -rs '
            first(.[] | select(.agentName) | [.agentName, .teamName // ""] | @tsv)
        ' 2>/dev/null) || true
        if [ -n "$result" ]; then
            IFS=$'\t' read -r AGENT_NAME TEAM_NAME <<< "$result"
            TRANSCRIPT_IS_OWN=1
            AMMO_MSG_ID_SOURCE="transcript"
        fi

        # Fallback A: meta.json sidecar (CC 2.1.19x in-process teammates).
        if [ -z "$AGENT_NAME" ]; then
            local META_FILE meta_result META_NAME META_TEAM
            META_FILE="${RESOLVED_TRANSCRIPT%.jsonl}.meta.json"
            if [ -f "$META_FILE" ]; then
                meta_result=$(jq -r '
                    [(.name // .agentType // ""), (.teamName // "")] | @tsv
                ' "$META_FILE" 2>/dev/null) || true
                if [ -n "$meta_result" ]; then
                    IFS=$'\t' read -r META_NAME META_TEAM <<< "$meta_result"
                    if [ -n "$META_NAME" ]; then
                        AGENT_NAME="$META_NAME"
                        [ -z "$TEAM_NAME" ] && TEAM_NAME="$META_TEAM"
                        TRANSCRIPT_IS_OWN=1
                        AMMO_MSG_ID_SOURCE="sidecar"
                        dbg "meta.json sidecar: agent=$AGENT_NAME team=$TEAM_NAME"
                    fi
                fi
            fi
        fi

        # Fallback B: legacy content regex (pre-2.1.19x tmux teammates).
        if [ -z "$AGENT_NAME" ]; then
            local FALLBACK
            FALLBACK=$(head -1 "$RESOLVED_TRANSCRIPT" 2>/dev/null | jq -r '
                .message.content // "" |
                capture("You are (?<name>(champion|impl-champion)-\\S+) in the AMMO") |
                .name
            ' 2>/dev/null) || true
            if [ -n "$FALLBACK" ]; then
                AGENT_NAME="$FALLBACK"
                TRANSCRIPT_IS_OWN=1
                AMMO_MSG_ID_SOURCE="legacy"
                dbg "content-based fallback: agent=$AGENT_NAME"
            fi
        fi
    fi

    # Fallback C: hook-input agent_type carries the teammate NAME (CC 2.1.198
    # in-process teammates). Accept only an eligible NAME shape; on 2.1.281
    # agent_type is the TYPE (general-purpose, ammo-champion, ...) and an
    # ammo-* type must never be mistaken for a mailbox name.
    if [ -z "$AGENT_NAME" ] && [ -n "$AGENT_TYPE" ]; then
        case "$AGENT_TYPE" in
            ammo-*) ;;
            champion-*|impl-*|monitor-*)
                AGENT_NAME="$AGENT_TYPE"
                AMMO_MSG_ID_SOURCE="agent_type"
                dbg "agent_type fallback: agent=$AGENT_NAME"
                ;;
        esac
    fi

    # Orchestrator detection via _ammo_is_lead + team config (fallback only).
    # Newest matching config wins: AMMO rounds reuse the lead session id.
    if [ -z "$AGENT_NAME" ] && [ -n "$SESSION_ID" ] && [ "$argv_found" != "1" ]; then
        if declare -F _ammo_is_lead >/dev/null 2>&1 && _ammo_is_lead "$_input"; then
            local _candidate_team="" _best_mtime=-1 tdir lead_sid _mt _tname
            for tdir in "$TEAMS_ROOT"/*/; do
                [ -f "$tdir/config.json" ] || continue
                lead_sid=$(jq -r '.leadSessionId // empty' "$tdir/config.json" 2>/dev/null) || continue
                if [ "$lead_sid" = "$SESSION_ID" ]; then
                    _mt=$(stat -c %Y "$tdir/config.json" 2>/dev/null || stat -f %m "$tdir/config.json" 2>/dev/null) || _mt=0
                    if [ "$_mt" -gt "$_best_mtime" ] 2>/dev/null; then
                        _tname=$(jq -r '.name // empty' "$tdir/config.json" 2>/dev/null) || _tname=""
                        [ -z "$_tname" ] && _tname=$(basename "${tdir%/}")
                        _candidate_team="$_tname"
                        _best_mtime="$_mt"
                    fi
                fi
            done
            if [ -n "$_candidate_team" ]; then
                AGENT_NAME="team-lead"
                TEAM_NAME="$_candidate_team"
                AMMO_MSG_IS_LEAD=1
                AMMO_MSG_ID_SOURCE="config"
                dbg "orchestrator via helper + team-config match → team=$TEAM_NAME"
            fi
        fi
    fi

    # ── Team name completion ──
    # Lead: the implicit team is session-<first 8 of the lead sid>. Prefer a
    # config dir that exists, then the newest config whose leadSessionId
    # matches, then the implicit name itself (the inbox path is secondary).
    if [ "$AMMO_MSG_IS_LEAD" = "1" ] && [ -z "$TEAM_NAME" ] && [ -n "$SESSION_ID" ]; then
        local implicit="session-${SESSION_ID:0:8}" tdir3 _lead3 _mt3 _best3=-1 _tn3
        if [ -d "$TEAMS_ROOT/$implicit" ]; then
            TEAM_NAME="$implicit"
        elif [ -d "$TEAMS_ROOT" ]; then
            for tdir3 in "$TEAMS_ROOT"/*/; do
                [ -f "$tdir3/config.json" ] || continue
                _lead3=$(jq -r '.leadSessionId // empty' "$tdir3/config.json" 2>/dev/null) || continue
                [ "$_lead3" = "$SESSION_ID" ] || continue
                _mt3=$(stat -c %Y "$tdir3/config.json" 2>/dev/null || stat -f %m "$tdir3/config.json" 2>/dev/null) || _mt3=0
                if [ "$_mt3" -gt "$_best3" ] 2>/dev/null; then
                    _tn3=$(jq -r '.name // empty' "$tdir3/config.json" 2>/dev/null) || _tn3=""
                    [ -z "$_tn3" ] && _tn3=$(basename "${tdir3%/}")
                    TEAM_NAME="$_tn3"; _best3="$_mt3"
                fi
            done
        fi
        [ -z "$TEAM_NAME" ] && TEAM_NAME="$implicit"
    fi
    # Teammate without a team name: scan team configs for a member with that name.
    if [ -n "$AGENT_NAME" ] && [ -z "$TEAM_NAME" ] && [ -d "$TEAMS_ROOT" ]; then
        local tdir2
        for tdir2 in "$TEAMS_ROOT"/*/; do
            [ -f "$tdir2/config.json" ] || continue
            if jq -e --arg n "$AGENT_NAME" '.members[]? | select(.name == $n)' "$tdir2/config.json" >/dev/null 2>&1; then
                TEAM_NAME=$(jq -r '.name // empty' "$tdir2/config.json" 2>/dev/null) || true
                [ -z "$TEAM_NAME" ] && TEAM_NAME=$(basename "${tdir2%/}")
                dbg "broader team fallback: agent=$AGENT_NAME → team=$TEAM_NAME"
                break
            fi
        done
    fi

    # ── Namespace completion (fallback paths only; argv set it above) ──
    if [ -z "$AMMO_MSG_NS" ] && [ -n "$AGENT_NAME" ]; then
        if [ "$AMMO_MSG_IS_LEAD" = "1" ]; then
            AMMO_MSG_NS="$AMMO_MSG_OWN_SID"
        else
            local _cfg_lead=""
            _cfg_lead=$(_ammo_msg_config_lead_sid "$TEAM_NAME" 2>/dev/null) || _cfg_lead=""
            if [ -n "$_cfg_lead" ]; then
                AMMO_MSG_NS=$(ammo_msg_sanitize "$_cfg_lead")
            elif [ -n "$TEAM_NAME" ]; then
                # No config to translate the team into its lead sid: key on the
                # team name so both sides at least agree on SOMETHING.
                AMMO_MSG_NS=$(ammo_msg_sanitize_team "$TEAM_NAME")
            fi
        fi
    fi

    # Own transcript: the head names us, or the file is this process's own
    # top-level session transcript (2.1.281 teammates and the lead). Only when
    # the identity came from the process itself (argv) or resolved to the lead:
    # on the 2.1.198 shape (Fallback C, agent_type=NAME) the hook hands an
    # in-process teammate the LEAD's session_id and transcript, so a matching
    # basename proves nothing and the caller must reroute via the sidecars.
    if [ "$TRANSCRIPT_IS_OWN" != "1" ] && [ -n "$RESOLVED_TRANSCRIPT" ] && [ -n "$SESSION_ID" ] \
       && [ "$AMMO_MSG_INPROC" != "1" ] \
       && { [ "$AMMO_MSG_IS_LEAD" = "1" ] || [ "$AMMO_MSG_ID_SOURCE" = "argv" ] || [ "$AMMO_MSG_ID_SOURCE" = "argv-lead" ]; } \
       && [ "$(basename "$RESOLVED_TRANSCRIPT" 2>/dev/null)" = "$SESSION_ID.jsonl" ]; then
        TRANSCRIPT_IS_OWN=1
    fi

    return 0
}

fi  # declare -F guard
