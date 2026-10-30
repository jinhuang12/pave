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
#
# Bash path tokens: a state.json token that names the campaign's state.json is
# validated. A token that names an existing state.json elsewhere is another
# file. A token that does not resolve (a shell variable such as $A/state.json,
# or a relative name that does not exist at the tool cwd) makes the hook
# validate the found campaign's own <campaign dir>/state.json, because the
# command may have written it. A live lead wrote every state change as
# `S="<plugin>/scripts/ammo ammo_state.py"; $S set --state $A/state.json`.
#
# ammo_state.py writers are derived from the engine, not listed here: every
# subcommand in its build_parser except the read-only STATE_READ_VERBS.
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

# The ammo_state.py subcommands that never write state. Every other
# subcommand in the engine's build_parser counts as a writer, so a new
# subcommand is a writer until it is listed here. The harness checks this
# list against the engine's own write calls (python ast).
STATE_READ_VERBS=" get validate next-step "

# _state_cli_writes CMD: true when CMD calls an ammo_state.py writer. The verb
# may follow the script name or a variable that holds it ($S set ...), so any
# whole word after the first ammo_state.py counts.
_state_cli_writes() {
    local engine="$PLUGIN_ROOT/scripts/ammo_state.py" verb words
    [ -f "$engine" ] || return 1
    words=" $(printf '%s' "${1#*ammo_state.py}" | tr -cs 'A-Za-z0-9_-' ' ') "
    while IFS= read -r verb; do
        [ -n "$verb" ] || continue
        case "$STATE_READ_VERBS" in *" $verb "*) continue ;; esac
        case "$words" in *" $verb "*) return 0 ;; esac
    done < <(grep -oE 'add_parser\("[a-z][a-z0-9-]*"' "$engine" | sed -E 's/^add_parser\("//; s/"$//')
    return 1
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
    # first-party writer (it may name the campaign by --artifact-dir only).
    case "$COMMAND" in
        *state.json*) ;;
        *'ammo_state.py'*) ;;
        *'reconcile_track_state.py'*) ;;
        *) exit 0;;
    esac

    # First-party writers: an ammo_state.py writer subcommand, or
    # reconcile_track_state.py --write, the canonical per-track lead write
    # path (parallel-tracks.md § Track State Reconciliation). The Codex twin
    # (post_tool_use_guard.py:_first_party_state_paths) covers both.
    FIRST_PARTY=false
    case "$COMMAND" in
        *'ammo_state.py'*) if _state_cli_writes "$COMMAND"; then FIRST_PARTY=true; fi ;;
    esac
    case "$COMMAND" in
        *'reconcile_track_state.py'*'--write'*) FIRST_PARTY=true ;;
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
    esac
    [ "$FIRST_PARTY" = "true" ] && LOOKS_LIKE_WRITE=true
    [ "$LOOKS_LIKE_WRITE" = "false" ] && exit 0

    # Outside a campaign there is no campaign state.json to validate.
    _find_campaign "$INPUT" || exit 0

    # Path tokens: every state.json path in the command (absolute, relative to
    # the tool cwd, or behind a variable such as $A, ${A}, $AMMO_ARTIFACT_DIR;
    # path chars include those inside quotes, as in open('p/state.json','w')).
    # A first-party writer's --artifact-dir X adds X/state.json (the Codex
    # twin's `_resolve_payload_path(...) / "state.json"`); both `--artifact-dir X`
    # and `--artifact-dir=X`, optionally quoted.
    _TOKENS=$(printf '%s\n' "$COMMAND" | grep -oE '(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)?[A-Za-z0-9_./-]*state\.json' || true)
    UNRESOLVED=0
    if [ "$FIRST_PARTY" = "true" ]; then
        while IFS= read -r _AD; do
            [ -n "$_AD" ] || continue
            _AD=$(printf '%s' "$_AD" | sed -E 's/^--artifact-dir[= ]+["'"'"']?//')
            if [ -z "$_AD" ]; then
                UNRESOLVED=1    # a value the regex cannot read, such as $(...)
            else
                _TOKENS="$_TOKENS
${_AD%/}/state.json"
            fi
        done < <(printf '%s\n' "$COMMAND" | grep -oE -- '--artifact-dir[= ]+["'"'"']?(\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)?[A-Za-z0-9_./-]*' || true)
    fi

    # Classify each token whose file name is exactly state.json:
    #   inside the campaign                -> EXTRACTED (the first one wins)
    #   an absolute path outside it        -> another file, skipped
    #   a relative path that exists        -> another file, skipped
    #   a variable; a relative path that does not exist at the tool cwd (the
    #   command may cd first); or exactly /state.json (its prefix was lost to
    #   a quote, a brace or $(...), as in "$A"/state.json) -> UNRESOLVED
    EXTRACTED=""
    HAS_TOKEN=false
    while IFS= read -r _tok; do
        [ -n "$_tok" ] || continue
        _cand=$(_abs "$_tok")
        [ "${_cand##*/}" = "state.json" ] || continue
        HAS_TOKEN=true
        case "$_cand" in
            *'$'*|*'{'*|*'}'*) UNRESOLVED=1; continue ;;
        esac
        if [ "$_tok" = "/state.json" ]; then UNRESOLVED=1; continue; fi
        if _campaign_state "$_cand"; then EXTRACTED="$_cand"; break; fi
        case "$_tok" in
            /*) continue ;;
        esac
        if [ ! -e "$_cand" ]; then UNRESOLVED=1; fi
    done <<< "$_TOKENS"

    # No token names the campaign's state.json. When a token does not resolve,
    # or a first-party writer named no path, the command may have written the
    # campaign's state: validate the found campaign's own state.json. A token
    # that does not resolve wins over an outside file in the same command (a
    # restore such as `cp /backup/state.json $A/state.json`). Otherwise every
    # token names another file: an unrelated write. Bail.
    if [ -z "$EXTRACTED" ]; then
        if [ "$UNRESOLVED" = 1 ]; then
            EXTRACTED="${AMMO_ARTIFACT_DIR%/}/state.json"
        elif [ "$FIRST_PARTY" = "true" ] && [ "$HAS_TOKEN" = "false" ]; then
            EXTRACTED="${AMMO_ARTIFACT_DIR%/}/state.json"
        fi
    fi
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
