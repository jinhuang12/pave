#!/bin/bash
# PostToolUse hook — validates state.json against schemas/state.schema.json.
#
# Fires on Write|Edit (file_path match) AND on Bash (command-string detection).
# Delegates schema + cross-field validation to scripts/ammo_state.py (validate
# --emit hook), run under the campaign python (ammo_python). Blocks via
# decision:block on violations.
#
# Campaign scope (_find_campaign.sh): only a state.json INSIDE the campaign dir
# (<engine>/.ammo/<campaign-id>/, realpath containment via ammo_in_campaign) is
# validated. The schema is the plugin's own schemas/state.schema.json. Outside
# a campaign the hook exits 0 and prints nothing.
set -euo pipefail
trap 'exit 0' ERR

if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HOOK_DIR/.." && pwd)"
source "$HOOK_DIR/_find_campaign.sh"

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null) || true
HOOK_CWD=$(echo "$INPUT" | jq -r '.cwd // ""' 2>/dev/null) || HOOK_CWD=""
# Relative paths in a command resolve against the tool call's cwd.
REL_BASE="${HOOK_CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"

# _abs PATH: expand a leading $AMMO_ARTIFACT_DIR / ${AMMO_ARTIFACT_DIR} and make
# a relative path absolute against REL_BASE. Pure text; no filesystem access.
_abs() {
    local p="$1"
    case "$p" in
        '${AMMO_ARTIFACT_DIR}'*) p="${AMMO_ARTIFACT_DIR:-}${p#'${AMMO_ARTIFACT_DIR}'}" ;;
        '$AMMO_ARTIFACT_DIR'*)   p="${AMMO_ARTIFACT_DIR:-}${p#'$AMMO_ARTIFACT_DIR'}" ;;
    esac
    case "$p" in
        /*) printf '%s' "$p" ;;
        *)  printf '%s/%s' "${REL_BASE%/}" "$p" ;;
    esac
}

# _campaign_state PATH: true when PATH is a state.json inside the campaign.
_campaign_state() {
    [ "${1##*/}" = "state.json" ] && ammo_in_campaign "$1"
}

# No python3 means no campaign lookup (the helper is fail-open). The
# fail-closed branch below must still fire for a write to a campaign's own
# state.json, so this one case reads the marker contract directly:
# <engine>/.ammo/active names <id>, and the file is <engine>/.ammo/<id>/state.json.
# Parameter expansion only: a scrubbed PATH may lack basename and dirname.
_marker_state_without_python() {
    local d="${1%/*}" e
    e="${d%/*}"
    [ "${1##*/}" = "state.json" ] || return 1
    [ "${e##*/}" = ".ammo" ] || return 1
    [ -f "$e/active" ] || return 1
    [ "$(head -n 1 "$e/active" 2>/dev/null)" = "${d##*/}" ]
}

# --- Bash detection path ---
# When fired on Bash, tool_input has .command not .file_path.
# Detect state.json writes by inspecting the command string.
if [ -z "$FILE_PATH" ]; then
    # Skip if the Bash command failed (write likely didn't complete)
    TOOL_STATUS=$(echo "$INPUT" | jq -r '.tool_response.status // "success"' 2>/dev/null) || true
    [ "$TOOL_STATUS" = "error" ] && exit 0

    COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null) || true
    [ -z "$COMMAND" ] && exit 0

    # Fast filter: bail unless the command names state.json, or names a
    # first-party writer that resolves the path itself from --artifact-dir.
    case "$COMMAND" in
        *state.json*) ;;
        *'reconcile_track_state.py'*) ;;
        *) exit 0;;
    esac

    # Exclude read-only commands (cat, grep, jq without redirect, head, tail, etc.)
    # Only proceed if the command looks like a write (>, mv, tee, open(...,'w'), json.dump)
    LOOKS_LIKE_WRITE=false
    case "$COMMAND" in
        *'> '*state.json*|*'>>'*state.json*) LOOKS_LIKE_WRITE=true;;
        *'mv '*state.json*|*'mv '*'state.json'*) LOOKS_LIKE_WRITE=true;;
        *'tee '*state.json*) LOOKS_LIKE_WRITE=true;;
        *"open("*state.json*"'w'"*) LOOKS_LIKE_WRITE=true;;
        *"open("*state.json*'"w"'*) LOOKS_LIKE_WRITE=true;;
        *'open('*state.json*'\"w\"'*) LOOKS_LIKE_WRITE=true;;
        *'json.dump'*state.json*) LOOKS_LIKE_WRITE=true;;
        *'json.dump'*'state.json'*) LOOKS_LIKE_WRITE=true;;
        *'write_text'*state.json*|*'write_bytes'*state.json*) LOOKS_LIKE_WRITE=true;;
        *'sed -i'*state.json*) LOOKS_LIKE_WRITE=true;;
        *'cp '*state.json*) LOOKS_LIKE_WRITE=true;;
        # Idioms below match anywhere in the command (the *state.json* fast
        # filter above already guarantees the command touches state.json).
        # Ordered patterns like *'json.dump'*state.json* miss the common
        # path-in-a-variable form (path='...state.json' BEFORE json.dump(s,tmp));
        # a live campaign wrote invalid state through exactly that gap via
        # tempfile.NamedTemporaryFile + os.replace.
        *'json.dump'*) LOOKS_LIKE_WRITE=true;;
        *'os.replace('*|*'os.rename('*) LOOKS_LIKE_WRITE=true;;
        *'NamedTemporaryFile'*|*'mkstemp'*) LOOKS_LIKE_WRITE=true;;
        *'shutil.move'*|*'shutil.copy'*) LOOKS_LIKE_WRITE=true;;
        *'write_text'*|*'write_bytes'*) LOOKS_LIKE_WRITE=true;;
        *'ammo_state.py'*' set '*|*'ammo_state.py'*' advance'*|*'ammo_state.py'*' enrich'*|*'ammo_state.py'*' backfill'*) LOOKS_LIKE_WRITE=true;;
        # reconcile_track_state.py --write is the canonical per-track lead write
        # path (parallel-tracks.md § Track State Reconciliation). It reached
        # state.json completely unvalidated until this arm existed; the Codex
        # twin (post_tool_use_guard.py:_first_party_state_paths) already had it.
        *'reconcile_track_state.py'*'--write'*) LOOKS_LIKE_WRITE=true;;
    esac
    [ "$LOOKS_LIKE_WRITE" = "false" ] && exit 0

    # Outside a campaign there is no campaign state.json to validate.
    _find_campaign "$INPUT" || exit 0

    # Extract the actual target path from the command: the first state.json
    # token (absolute, relative to the tool cwd, or $AMMO_ARTIFACT_DIR-prefixed)
    # that resolves inside the campaign dir. Path chars include those inside
    # quotes (Python open('path/state.json','w')).
    EXTRACTED=""
    while IFS= read -r _tok; do
        [ -n "$_tok" ] || continue
        _cand=$(_abs "$_tok")
        if _campaign_state "$_cand"; then EXTRACTED="$_cand"; break; fi
    done < <(printf '%s\n' "$COMMAND" | grep -oE '(\$\{?AMMO_ARTIFACT_DIR\}?)?[A-Za-z0-9_./-]*state\.json' || true)

    # reconcile_track_state.py takes --artifact-dir, not a state.json path, so
    # derive state.json from the dir (matching the Codex twin's
    # `_resolve_payload_path(...) / "state.json"`). Both `--artifact-dir X` and
    # `--artifact-dir=X` spellings.
    if [ -z "$EXTRACTED" ]; then
        case "$COMMAND" in
            *'reconcile_track_state.py'*)
                _AD=$(echo "$COMMAND" | grep -oE -- '--artifact-dir[= ]+(\$\{?AMMO_ARTIFACT_DIR\}?)?[A-Za-z0-9_./-]*' | head -1 | sed -E 's/^--artifact-dir[= ]+//') || true
                if [ -n "$_AD" ]; then
                    _cand="$(_abs "$_AD")"
                    _cand="${_cand%/}/state.json"
                    _campaign_state "$_cand" && EXTRACTED="$_cand"
                fi
                ;;
        esac
    fi

    # Command mentions state.json + looks like a write but names no state.json
    # inside the campaign: an unrelated state.json write. Bail.
    [ -n "$EXTRACTED" ] || exit 0
    FILE_PATH="$EXTRACTED"
    [ -f "$FILE_PATH" ] || exit 0
else
    # --- Write/Edit path ---
    [ "${FILE_PATH##*/}" = "state.json" ] || exit 0
    [ -f "$FILE_PATH" ] || exit 0
    if command -v python3 &>/dev/null; then
        # The tool cwd finds the campaign; a write from elsewhere into a
        # campaign's dir finds it from the file's own dir.
        _find_campaign "$INPUT" \
            || _find_campaign "$(jq -cn --arg c "$(dirname "$FILE_PATH")" '{cwd: $c}')" \
            || exit 0
        _campaign_state "$FILE_PATH" || exit 0
    else
        _marker_state_without_python "$FILE_PATH" || exit 0
    fi
fi

# The schema is the plugin's own. A missing schema FILE stays fail-open (the
# deliberate vanished-schema carve-out; see the fail-closed note below).
SCHEMA="$PLUGIN_ROOT/schemas/state.schema.json"
[ -f "$SCHEMA" ] || exit 0

# Delegate ALL validation (schema + cross-field Stage-6 guard + audit gates +
# new-round-start gate) to the python state engine. The engine emits compact
# block JSON on a violation (silent on pass) and always exits 0; we echo its
# output so the PostToolUse hook surfaces the decision:block to Claude Code.
#
# The engine is the plugin's own scripts/ammo_state.py, resolved from THIS
# hook's dir (never from the cwd or the campaign tree, which agents write).
# ammo_python runs it under the campaign python: target.json engine.venv, else
# AMMO_PYTHON, else the venv in the engine root, else python3. A python without
# jsonschema makes the engine block (fail-closed, below).
ENGINE="$PLUGIN_ROOT/scripts/ammo_state.py"

# P4#6 FAIL-CLOSED POINT (DEFAULT-ON): if python3 or the engine is unavailable,
# validation CANNOT run. Emit a decision:block so the PostToolUse hook surfaces
# it (exit-code is NOT the enforcement mechanism — the block JSON is). Escape
# hatch AMMO_VALIDATE_FAIL_OPEN=1 reverts to the legacy fail-open for degraded
# environments. NOTE: this is the python3/engine-missing branch only — the
# missing-schema-FILE fast-bail at the `[ -f "$SCHEMA" ] || exit 0` line above
# stays fail-open (deliberate vanished-schema carve-out).
if ! command -v python3 &>/dev/null || [ ! -f "$ENGINE" ]; then
    if [ "${AMMO_VALIDATE_FAIL_OPEN:-}" = "1" ]; then exit 0; fi
    REASON="state.json validation could not run (python3 or the AMMO state engine is unavailable) — restore python3 and the campaign venv, or set AMMO_VALIDATE_FAIL_OPEN=1 to bypass (degraded)."
    jq -cn --arg r "$REASON" '{"decision":"block","reason":$r,"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":$r}}'
    exit 0
fi

# ── PREV_STATE snapshot: THIS hook owns it ──
# ORDERING CONTRACT (one sentence): this blocking validator reads
# /tmp/ammo-state-prev-<SID>.json as the PRE-write baseline, then OVERWRITES it
# with the post-write state after the engine has run.
#
# The snapshot write used to live in ammo-next-step-reminder.sh, an ADVISORY
# hook. That made a blocking tier/scope gate's grandfathering baseline depend on
# an advisory hook's side effect and on the order of the PostToolUse array. The
# blocking hook now owns both halves, so the contract holds no matter what else
# is registered. The Codex twin keeps the same read-then-write order
# (post_tool_use_guard.py:693-700 reads, then writes after validate).
#
# The gates enforce only NEW or MODIFIED scoreboard/candidate entries. An absent
# or stale snapshot makes the engine grandfather every pre-existing entry (never
# a false block); enforcement of a NEW bad write works once the snapshot exists.
# AMMO_REMINDER_STATE_DIR overrides the snapshot root (same env name the Codex
# twin uses). Test harnesses set it so runs are isolated from each other and
# from a live session's /tmp state.
SESSION_ID="${CLAUDE_SESSION_ID:-$(echo "$INPUT" | jq -r '.session_id // "default"' 2>/dev/null)}"
PREV_STATE="${AMMO_REMINDER_STATE_DIR:-/tmp}/ammo-state-prev-${SESSION_ID}.json"
PREV_ARGS=()
[ -f "$PREV_STATE" ] && PREV_ARGS=(--prev "$PREV_STATE")

# Engine present: delegate. When the jsonschema LIB is missing or the state
# JSON won't parse, the engine itself emits decision:block (fail-closed,
# default-on, honoring AMMO_VALIDATE_FAIL_OPEN). --fail-closed documents intent
# (default is already on). We echo the engine's stdout so the block surfaces.
OUT=$(ammo_python ammo_state.py validate --state "$FILE_PATH" --schema "$SCHEMA" "${PREV_ARGS[@]}" --emit hook --fail-closed 2>/dev/null) || true

# Refresh the snapshot AFTER validate, so at validate time it still held the
# pre-write state. Written atomically (tmp + mv) so a concurrent reader never
# sees a half-copied file. Best-effort: a snapshot failure must not turn into a
# block, it only makes the NEXT validate grandfather more entries.
if [ -f "$FILE_PATH" ]; then
    cp "$FILE_PATH" "${PREV_STATE}.tmp.$$" 2>/dev/null \
        && mv -f "${PREV_STATE}.tmp.$$" "$PREV_STATE" 2>/dev/null \
        || rm -f "${PREV_STATE}.tmp.$$" 2>/dev/null || true
fi

[ -n "$OUT" ] && echo "$OUT"
exit 0
