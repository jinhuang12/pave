#!/bin/bash
# PreToolUse hook — AMMO mid-turn message injection (non-blocking).
#
# Problem: SendMessage delivery is queued between turns. Champions and
# monitors are deaf during long tool execution chains (GPU benchmarks, E2E
# sweeps). Measured 2.1.281 (2026-09-24, real container): a codeword sent at
# 11:53:24 reached the busy recipient at its next tool call (11:54:45), 8 s
# before the native turn-boundary delivery (11:54:53).
#
# Approach: inject undelivered messages as additionalContext BEFORE each tool
# call. Never deny — agents keep working with full awareness.
#
# Sources (merged, deduped):
#   1. The sender-side mirror channel <root>/<lead sid>/<me>.jsonl. The REPL
#      poller never touches it, so it is the source that carries mid-turn
#      messages. Its writer, ammo-msg-mirror.sh (PostToolUse on SendMessage),
#      is not part of this plugin (hooks/README.md, dropped hooks), so the
#      channel is empty unless another writer fills it. Then, on 2.1.202+,
#      this hook has no mid-turn source and messages arrive at the next turn
#      boundary as Claude Code delivers them.
#   2. The real team inbox <teams>/<team>/inboxes/<me>.json. Dead on 2.1.202+
#      (pruned ~0.9 s after every send even while the recipient is inside a
#      tool call; measured again on 2.1.281). Kept as a harmless secondary
#      source for older CC versions.
#
# Campaign scope (_find_campaign.sh): outside a campaign the hook exits 0 and
# prints nothing, before any identity work.
#
# Identity + namespace: see _ammo_msg_identity.sh.
# The namespace key is the LEAD SESSION ID, read from this process's own spawn
# argv (teammates) or from the hook input (lead). No team config is consulted
# on the primary path.
#
# In-process subagents (Task tool) run INSIDE their parent's process and share
# its session_id, so they hit this hook too. They are skipped: hook-input
# agent_id set and different from the argv --agent-id. Without the skip a
# champion's ammo-delegate consumed the champion's message and advanced the
# cursor, and the champion never saw it mid-turn (measured 2.1.281).
#
# Dedup: cutoff = max(last native <teammate-message> delivery in the agent's
# own transcript tail, last injected timestamp in the cursor file). Budget is
# spent on WHOLE messages, oldest first; the cursor advances only to the newest
# message actually included, so a message that did not fit arrives on the next
# tool call instead of being dropped. The read-decide-advance sequence runs
# under a file lock so parallel tool calls cannot double-inject.
#
# Eligibility: team-lead, champion-*, impl-champion-*, impl-*, monitor-*,
# *champion*, *monitor*, or an argv --agent-type of ammo-champion,
# ammo-impl-champion, ammo-transcript-monitor, bare or with the plugin's
# ammo: prefix (see ammo_msg_eligible).
# Applies to all wired tool calls. Fail-open (exit 0 on any error).
set -euo pipefail
trap 'exit 0' ERR

DEBUG="${AMMO_MSG_CHECK_DEBUG:-}"
DLOG="/tmp/ammo-msg-check-debug.log"
dbg() { [ -n "$DEBUG" ] && echo "[$(date +%T)] $*" >> "$DLOG" || true; }

if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)

# ── Parse hook input ──
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null) || TOOL_NAME=""
[ -z "$TOOL_NAME" ] && exit 0

# ── Campaign scope ──
HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HELPER_DIR/_find_campaign.sh" 2>/dev/null || exit 0
_find_campaign "$INPUT" || exit 0

# ── Identity ──
# shellcheck source=/dev/null
source "$HELPER_DIR/_ammo_msg_identity.sh" 2>/dev/null || exit 0
ammo_msg_resolve_identity "$INPUT"

dbg "tool=$TOOL_NAME agent=${AGENT_NAME:-<none>} team=${TEAM_NAME:-<none>} ns=${AMMO_MSG_NS:0:8} lead=$AMMO_MSG_IS_LEAD src=$AMMO_MSG_ID_SOURCE own=${AMMO_MSG_OWN_SID:0:8} hook_aid=${AMMO_MSG_HOOK_AGENT_ID:-<none>} argv_aid=${AMMO_MSG_ARGV_AGENT_ID:-<none>}"

# ── Skip in-process subagents ──
if [ "$AMMO_MSG_INPROC" = "1" ]; then
    dbg "skip: in-process subagent (hook agent_id=$AMMO_MSG_HOOK_AGENT_ID != argv agent_id=${AMMO_MSG_ARGV_AGENT_ID:-<none>})"
    exit 0
fi

[ -z "$AGENT_NAME" ] && exit 0
[ -z "$AMMO_MSG_NS" ] && { dbg "skip: no namespace for $AGENT_NAME"; exit 0; }

# ── Eligibility filter ──
if ! ammo_msg_eligible "$AGENT_NAME" "$AMMO_MSG_ARGV_AGENT_TYPE"; then
    dbg "skip: agentName $AGENT_NAME not in eligible set"
    exit 0
fi

# ── Locate the two message sources ──
ROOT=$(ammo_msg_ensure_root) || exit 0
MIRROR_FILE=$(ammo_msg_channel_file "$AMMO_MSG_NS" "$AGENT_NAME" 2>/dev/null) || MIRROR_FILE=""
HAVE_MIRROR=0
if [ -n "$MIRROR_FILE" ] && [ -s "$MIRROR_FILE" ] && ammo_msg_own_file "$MIRROR_FILE"; then
    HAVE_MIRROR=1
fi

MY_INBOX=""
if [ -n "$TEAM_NAME" ]; then
    # Claude Code sanitizes team dir names (dots → dashes), so try both.
    TEAM_DIR="$TEAMS_ROOT/$TEAM_NAME"
    if [ ! -d "$TEAM_DIR" ]; then
        TEAM_DIR="$TEAMS_ROOT/$(ammo_msg_sanitize_team "$TEAM_NAME")"
    fi
    MY_INBOX="$TEAM_DIR/inboxes/$AGENT_NAME.json"
fi
# FAST PATH: no channel and no inbox => nothing to do before any transcript work.
if [ "$HAVE_MIRROR" != "1" ] && { [ -z "$MY_INBOX" ] || [ ! -f "$MY_INBOX" ]; }; then
    exit 0
fi

# ── Resolve the agent's OWN transcript for delivery detection ──
# On 2.1.281 the hook input carries the agent's own transcript (teammates and
# lead). CC 2.1.19x passed the LEAD transcript for in-process teammates, whose
# own transcript lives at <lead>/subagents/agent-<id>.jsonl; locate it via the
# meta.json sidecars (newest wins). If no own transcript is found, use NO
# delivery cutoff rather than the lead's.
DELIVERY_TRANSCRIPT="$RESOLVED_TRANSCRIPT"
if [ "$AGENT_NAME" != "team-lead" ] && [ "$TRANSCRIPT_IS_OWN" != "1" ]; then
    case "$(basename "$RESOLVED_TRANSCRIPT" 2>/dev/null)" in
        agent-*.jsonl) ;;
        *)
            DELIVERY_TRANSCRIPT=""
            SUB_DIR="${RESOLVED_TRANSCRIPT%.jsonl}/subagents"
            if [ -d "$SUB_DIR" ]; then
                own=$(ls -t "$SUB_DIR"/agent-*.meta.json 2>/dev/null | while read -r mf; do
                    mname=$(jq -r '.name // .agentType // empty' "$mf" 2>/dev/null) || continue
                    if [ "$mname" = "$AGENT_NAME" ] && [ -f "${mf%.meta.json}.jsonl" ]; then
                        echo "${mf%.meta.json}.jsonl"
                        break
                    fi
                done) || own=""
                if [ -n "$own" ] && [ -f "$own" ]; then
                    DELIVERY_TRANSCRIPT="$own"
                    dbg "delivery transcript → own subagent transcript: $own"
                else
                    dbg "own transcript not found — no delivery cutoff"
                fi
            fi
            ;;
    esac
fi

# ── Merge mirror + inbox into one message array ──
# Streamed (no --argjson) so a large channel cannot hit the kernel argv
# limit. Dedup by (from, text): the same message can sit in both sources
# for a moment, and their timestamps differ (CC stamp vs mirror stamp).
MERGED=$( {
    if [ -n "$MY_INBOX" ] && [ -f "$MY_INBOX" ]; then
        jq -c 'if type == "array" then .[] else empty end' "$MY_INBOX" 2>/dev/null || true
    fi
    if [ "$HAVE_MIRROR" = "1" ]; then
        jq -c -R 'try fromjson | select(type == "object")' "$MIRROR_FILE" 2>/dev/null || true
    fi
    true
} | jq -c -s 'unique_by([(.from // ""), ((.text // "") | tostring)]) | sort_by(.timestamp // "")' 2>/dev/null) || MERGED='[]'
[ -n "$MERGED" ] || MERGED='[]'

# ── Self-identity forms for the .from filter ──
# CC writes .from inconsistently across versions: plain name ("champion-1"),
# qualified agentId ("champion-1@session-abc"), or the raw transcript hash id.
SELF_ID="$AGENT_NAME"
[ -n "$TEAM_NAME" ] && SELF_ID="$AGENT_NAME@$TEAM_NAME"
RAW_ID=""
case "$(basename "${DELIVERY_TRANSCRIPT:-}" 2>/dev/null)" in
    agent-*.jsonl) RAW_ID=$(basename "$DELIVERY_TRANSCRIPT" .jsonl); RAW_ID="${RAW_ID#agent-}";;
esac

# ── Newest non-self message timestamp ──
LAST_INBOX_TS=$(printf '%s' "$MERGED" | jq -r --arg me "$AGENT_NAME" --arg meid "$SELF_ID" --arg meraw "$RAW_ID" '
    [.[] | select(.from != $me and .from != $meid and ($meraw == "" or .from != $meraw)) | .timestamp // empty]
    | map(select(. != null and . != ""))
    | if length == 0 then "" else max end
' 2>/dev/null) || exit 0
[ -z "$LAST_INBOX_TS" ] && exit 0
dbg "last_inbox_ts=$LAST_INBOX_TS"

# ── Last NATIVE delivery timestamp from the agent's own transcript ──
# Bounded tail: transcripts grow to hundreds of MB. The grep is a cheap
# prefilter; tool results and meta entries can quote the tag verbatim, so
# require a real user-turn entry (no toolUseResult, not meta) whose message
# text (string or text blocks) contains the tag.
LAST_DELIVERY_TS=""
if [ -n "${DELIVERY_TRANSCRIPT:-}" ] && [ -f "$DELIVERY_TRANSCRIPT" ]; then
    LAST_DELIVERY_TS=$(tail -c 4000000 "$DELIVERY_TRANSCRIPT" 2>/dev/null \
        | grep -F '<teammate-message teammate_id=' 2>/dev/null \
        | tail -n 40 \
        | jq -R -r 'try fromjson
            | select(.type == "user" and (has("toolUseResult") | not) and (.isMeta != true))
            | select(
                (if (.message.content | type) == "string" then .message.content
                 else ([.message.content[]? | select((.type // "") == "text") | .text // ""] | join("\n")) end)
                | contains("<teammate-message teammate_id="))
            | .timestamp // empty' 2>/dev/null \
        | tail -1) || LAST_DELIVERY_TS=""
fi
dbg "last_delivery_ts=${LAST_DELIVERY_TS:-(none)}"

# ── Cursor file inside the validated 0700 root ──
umask 077
CURSOR=$(ammo_msg_cursor_file "$AMMO_MSG_NS" "$AGENT_NAME" 2>/dev/null) || exit 0

# ── If all messages delivered natively, clean up the cursor and exit ──
if [ -n "$LAST_DELIVERY_TS" ]; then
    if [ "$LAST_INBOX_TS" \< "$LAST_DELIVERY_TS" ] || [ "$LAST_INBOX_TS" = "$LAST_DELIVERY_TS" ]; then
        dbg "all delivered: inbox_ts=$LAST_INBOX_TS <= delivery_ts=$LAST_DELIVERY_TS — cleaning cursor"
        rm -f "$CURSOR" 2>/dev/null || true
        exit 0
    fi
fi

# ── Cursor read + injection build + cursor write under ONE lock ──
# Parallel tool calls fire this hook concurrently; unsynchronised, every one
# reads the same stale cursor and injects the same batch. flock where present
# (session images), mkdir lock otherwise; failure to lock is not fatal.
LOCK_D=""
if command -v flock >/dev/null 2>&1; then
    exec 8>"$CURSOR.lock" 2>/dev/null || true
    flock -w 5 8 2>/dev/null || true
else
    LOCK_D="$CURSOR.lock.d"
    ammo_msg_lock_acquire "$LOCK_D" 5 || LOCK_D=""
    [ -n "$LOCK_D" ] && trap 'ammo_msg_lock_release "$LOCK_D"' EXIT
fi

LAST_INJECTED_TS=""
if [ -e "$CURSOR" ]; then
    if ammo_msg_own_file "$CURSOR"; then
        LAST_INJECTED_TS=$(cat "$CURSOR" 2>/dev/null) || LAST_INJECTED_TS=""
    else
        rm -f "$CURSOR" 2>/dev/null || true
    fi
fi
# A corrupt cursor must not become a permanent cutoff: accept only ISO-8601.
case "$LAST_INJECTED_TS" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*) ;;
    *) LAST_INJECTED_TS="" ;;
esac

# ── Cutoff: inject messages newer than max(delivery_ts, injected_ts) ──
CUTOFF=""
if [ -n "$LAST_DELIVERY_TS" ] && [ -n "$LAST_INJECTED_TS" ]; then
    if [ "$LAST_DELIVERY_TS" \> "$LAST_INJECTED_TS" ]; then CUTOFF="$LAST_DELIVERY_TS"
    else CUTOFF="$LAST_INJECTED_TS"; fi
elif [ -n "$LAST_DELIVERY_TS" ]; then CUTOFF="$LAST_DELIVERY_TS"
elif [ -n "$LAST_INJECTED_TS" ]; then CUTOFF="$LAST_INJECTED_TS"
fi

# Never replay ancient history: a stale channel from a previous run must not
# dump old chatter into a fresh context. Same knob as the mirror's TTL sweep;
# 0 disables the floor (harness fixtures use fixed dates).
MAX_AGE_S="${AMMO_MSG_MIRROR_MAX_AGE_S:-7200}"
case "$MAX_AGE_S" in ''|*[!0-9]*) MAX_AGE_S=7200 ;; esac
if [ "$MAX_AGE_S" -gt 0 ]; then
    FLOOR=$(ammo_msg_iso_from_epoch "$(( $(date -u +%s) - MAX_AGE_S ))" || echo "")
    if [ -n "$FLOOR" ] && { [ -z "$CUTOFF" ] || [ "$CUTOFF" \< "$FLOOR" ]; }; then
        CUTOFF="$FLOOR"
    fi
fi
dbg "cutoff=${CUTOFF:-(none)} (delivery=${LAST_DELIVERY_TS:-} injected=${LAST_INJECTED_TS:-})"

# ── Build injection ──
# neutralize: message text is model-authored; a literal
# "</injected-teammate-message>" or "<system-reminder>" inside it would break
# out of the wrapper and let a teammate forge system-level context. Only the
# structural tag names are escaped — "a<b" or "List<int>" pass untouched.
# Budget: WHOLE messages, oldest first, within 24KB; the cursor advances only
# to the newest included message. A single message longer than the budget is
# capped with a note (the full text still arrives at the turn boundary).
MAX_CHARS=24000
RESULT=$(printf '%s' "$MERGED" | jq -c --arg me "$AGENT_NAME" --arg meid "$SELF_ID" --arg meraw "$RAW_ID" --arg cutoff "${CUTOFF:-}" --argjson max "$MAX_CHARS" '
    def neutralize: tostring
        | gsub("<(?=/?(?:injected-teammate-message|teammate-message|system-reminder|system|human|assistant)(?:[>\\s/]|$))"; "&lt;");
    def fromlabel: (.from // "unknown") | tostring | gsub("[\"<>]"; "_");
    def blk: "<injected-teammate-message from=\"\(fromlabel)\" ts=\"\(.timestamp // "unknown")\">\n\((.text // "") | neutralize)\n</injected-teammate-message>";
    [ .[] | select(.from != $me and .from != $meid and ($meraw == "" or .from != $meraw))
          | select($cutoff == "" or ((.timestamp // "") > $cutoff)) ]
    | sort_by(.timestamp // "")
    | map({ts: (.timestamp // ""), b: blk}) as $blocks
    | if ($blocks | length) == 0 then empty else
        (reduce $blocks[] as $e ({k: 0, len: 0, stop: false};
            if .stop then .
            else (($e.b | length) + 2) as $l
                 | if (.len + $l) <= $max
                   then {k: (.k + 1), len: (.len + $l), stop: false}
                   else {k: .k, len: .len, stop: true} end
            end) | .k) as $fit
        | (if $fit == 0 then 1 else $fit end) as $keep
        | $blocks[:$keep] as $kept
        | (($blocks | length) - $keep) as $held
        | ($kept | map(.b) | join("\n\n")) as $body
        | (if ($body | length) > $max
           then ($body[:$max] + "\n\n[... this one message is too long to show in full here; its full text arrives at your turn boundary]")
           else $body end) as $capped
        | { newest: (($kept | map(.ts) | max) // ""),
            count: ($kept | length),
            held: $held,
            context: ("\($kept | length) mid-turn teammate message(s) injected below, oldest first. These are being delivered early so you have full context while working. When these same messages arrive again at your turn boundary, IGNORE the duplicates — you already have the content.\n\n"
                      + $capped
                      + (if $held > 0 then "\n\n[\($held) newer message(s) did not fit; they arrive on your next tool call.]" else "" end)) }
      end
' 2>/dev/null) || exit 0

[ -n "$RESULT" ] || { dbg "no new messages after cutoff"; exit 0; }

NEWEST=$(printf '%s' "$RESULT" | jq -r '.newest // ""' 2>/dev/null) || exit 0
COUNT=$(printf '%s' "$RESULT" | jq -r '.count // 0' 2>/dev/null) || COUNT=0
HELD=$(printf '%s' "$RESULT" | jq -r '.held // 0' 2>/dev/null) || HELD=0
[ -n "$NEWEST" ] && printf '%s' "$NEWEST" > "$CURSOR" 2>/dev/null || true
if [ -n "$LOCK_D" ]; then
    ammo_msg_lock_release "$LOCK_D"; LOCK_D=""; trap - EXIT
else
    flock -u 8 2>/dev/null || true
fi

INJECT_OUTPUT=$(printf '%s' "$RESULT" | jq -c '{hookSpecificOutput: {hookEventName: "PreToolUse", additionalContext: .context}}' 2>/dev/null) || exit 0
[ -n "$INJECT_OUTPUT" ] || exit 0
dbg "injected: agent=$AGENT_NAME count=$COUNT held=$HELD ns=${AMMO_MSG_NS:0:8} cursor=$NEWEST bytes=${#INJECT_OUTPUT}"

# ── Emit injection (non-blocking — no permissionDecision) ──
echo "$INJECT_OUTPUT"
exit 0
