#!/bin/bash
# PreToolUse hook — AMMO engine env-var guard (VLLM_* and PUMICE_*).
#
# Fires on Edit and Write tool calls. Two rules run on the new content:
#
# VLLM_* rule (envs.py edits only). Blocks an edit that registers a campaign
# VLLM_* gating flag with a truthy default. This prevents cross-track
# contamination where a prior round's gating flag silently activates stale
# optimizations during a later sweep. A campaign gating flag must default to
# False/0/"0"; the sweep harness enables it explicitly via opt_env in
# target.json.
#
# PUMICE_* rule (.py and .rs edits). Pumice owns its engine env names. Blocks an
# edit that:
#   - reads a PUMICE_* env name that is NOT in the allowlist (a new engine env),
#     or
#   - sets a default for a PUMICE_* name that IS in the allowlist (a default
#     change to a known engine env).
# The allowlist is ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt,
# copied next to this hook by scripts/render_ammo_variants.py. A PUMICE_* token
# is only inspected when it appears in an env-access idiom (os.getenv,
# os.environ, env::var, std::env), so the _PUMICE_MODELS registry dict in
# src/pumice/models/registry.py is never flagged.
#
# NAME-AGNOSTIC VLLM match (was VLLM_OP\d+ only). impl-track-rules.md § Env Flag
# Naming (PR-Ready) FORBIDS the VLLM_OP<n> shape and mandates descriptive
# VLLM_<SCOPE>_<MECHANISM>[_<ARCH>] names, so the old regex fired only on names
# a Stage 4-5 audit already treats as BLOCKING and missed every conforming one.
# Live campaign artifacts carry both shapes (VLLM_OP002_FUSED_GDN_EPILOGUE next
# to VLLM_QWEN3_5_FP8_MLP_GATE_UP_SM89), so the match is now
# VLLM_[A-Z0-9_]{3,} minus an explicit allowlist.
#
# ALLOWLIST — infra-legit VLLM_* names that are NOT campaign gating flags and DO
# legitimately default truthy. Two rules:
#   1. Platform/infra prefixes (ROCM/XPU/CPU/TPU/XLA/ZENTORCH/RAY/LOG/...): an
#      NVIDIA campaign never gates on these, and upstream defaults several on.
#   2. An explicit name list, seeded by scanning the real vLLM envs.py in the
#      session image for `getenv(KEY, "1"|True)` registrations, plus the
#      infra/provisioning keys that appear in campaign artifacts
#      (VLLM_USE_PRECOMPILED, VLLM_CACHE_ROOT, VLLM_ATTENTION_BACKEND, ...) and
#      the VLLM_* names in the allowlist file.
# Anything not on the list counts as a campaign flag. That direction is the safe
# one: a false block costs the champion one turn and names the flag, while a
# false allow silently corrupts an A/B arm.
#
# Exit 0 = allow, Exit 2 = block (stderr fed back to agent).
set -euo pipefail

if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)

# Extract file path from Edit or Write tool input
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null) || true
[ -z "$FILE_PATH" ] && exit 0

# Classify the target. VLLM_* runs on envs.py only; PUMICE_* runs on any .py or
# .rs file. Anything else is out of scope.
_IS_ENVS_PY=false
_IS_PY=false
_IS_RS=false
case "$FILE_PATH" in
    */envs.py) _IS_ENVS_PY=true; _IS_PY=true ;;
    *.py) _IS_PY=true ;;
    *.rs) _IS_RS=true ;;
esac
if [ "$_IS_PY" = "false" ] && [ "$_IS_RS" = "false" ]; then
    exit 0
fi

# Extract the content being written
# Edit tool: new_string field; Write tool: content field
NEW_CONTENT=$(echo "$INPUT" | jq -r '.tool_input.new_string // .tool_input.content // empty' 2>/dev/null) || true
[ -z "$NEW_CONTENT" ] && exit 0

# ── Allowlist file (PUMICE_* known engine envs + infra-legit VLLM_* names) ──
# scripts/render_ammo_variants.py copies the canonical
# ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt next to this hook.
# Prefer the co-located copy; fall back to the canonical path in a dev checkout.
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLOWLIST_FILE=""
for _cand in \
    "$HOOK_DIR/pumice_env_allowlist.txt" \
    "$HOOK_DIR/../../_ammo_shared/hooks/pumice_env_allowlist.txt"; do
    if [ -f "$_cand" ]; then ALLOWLIST_FILE="$_cand"; break; fi
done

# Names on the allowlist, comments and blanks stripped.
ALLOWLIST_NAMES=""
if [ -n "$ALLOWLIST_FILE" ]; then
    ALLOWLIST_NAMES=$(sed -E 's/[[:space:]]+#.*$//' "$ALLOWLIST_FILE" \
        | grep -E '^(PUMICE|VLLM)_[A-Z0-9_]{3,}$' || true)
fi

# Non-NVIDIA platform families and the infra prefixes — never campaign flags.
ALLOW_PREFIX_RE='^VLLM_(ROCM|XPU|CPU|TPU|XLA|ZENTORCH|NEURON|HPU|CI|TEST|DOCKER|SERVER|API|LOG|LOGGING|USAGE|RAY|NIXL|MOONCAKE|PLUGIN|TRACE|TARGET|MAIN|SYSTEM|PROCESS|CONFIG|CACHE|ASSETS)_'

# Explicit infra-legit names. Seeded from the truthy-default registrations in
# the real vLLM envs.py plus the provisioning keys seen in campaign artifacts.
# The VLLM_* names from the allowlist file are appended below.
ALLOW_NAMES='
VLLM_ALLOW_CHUNKED_LOCAL_ATTN_WITH_HYBRID_KV_CACHE
VLLM_ALLOW_INSECURE_SERIALIZATION
VLLM_ALLOW_LONG_MAX_MODEL_LEN
VLLM_ALLOW_RUNTIME_LORA_UPDATING
VLLM_ALLREDUCE_USE_SYMM_MEM
VLLM_ATTENTION_BACKEND
VLLM_BATCH_INVARIANT
VLLM_BLOCKSCALE_FP8_GEMM_FLASHINFER
VLLM_COMPILE
VLLM_CONFIGURE_LOGGING
VLLM_DISABLE_COMPILE_CACHE
VLLM_DP_RANK
VLLM_DP_SIZE
VLLM_ENABLE_INDUCTOR_COORDINATE_DESCENT_TUNING
VLLM_ENABLE_INDUCTOR_MAX_AUTOTUNE
VLLM_ENABLE_PREGRAD_PASSES
VLLM_ENABLE_V1_MULTIPROCESSING
VLLM_ENFORCE_STRICT_TOOL_CALLING
VLLM_FLASHINFER_WORKSPACE_BUFFER_SIZE
VLLM_KV_CACHE_LAYOUT
VLLM_KV_EVENTS_USE_INT_BLOCK_HASHES
VLLM_MEDIA_URL_ALLOW_REDIRECTS
VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS
VLLM_MLA_DISABLE
VLLM_MXFP8_EMULATION_DEQUANT_AT_LOAD
VLLM_NO_USAGE_STATS
VLLM_SKIP_P2P_CHECK
VLLM_TOOL_JSON_ERROR_AUTOMATIC_RETRY
VLLM_TOOL_PARSE_REGEX_TIMEOUT_SECONDS
VLLM_TORCH_COMPILE_LEVEL
VLLM_USE_AOT_COMPILE
VLLM_USE_BYTECODE_HOOK
VLLM_USE_DEEP_GEMM
VLLM_USE_DEEP_GEMM_E8M0
VLLM_USE_DEEP_GEMM_TMA_ALIGNED_SCALES
VLLM_USE_FUSED_MOE_GROUPED_TOPK
VLLM_USE_LAYERNAME
VLLM_USE_PRECOMPILED
VLLM_USE_STANDALONE_COMPILE
VLLM_USE_V1
VLLM_WORKER_MULTIPROC_METHOD
'
# Append the VLLM_* names carried in the allowlist file.
if [ -n "$ALLOWLIST_NAMES" ]; then
    _vllm_from_file=$(printf '%s\n' "$ALLOWLIST_NAMES" | grep -E '^VLLM_' || true)
    [ -n "$_vllm_from_file" ] && ALLOW_NAMES="$ALLOW_NAMES
$_vllm_from_file"
fi

# One truthy-default registration per matched LINE. Names are then extracted
# from that line and filtered by the allowlist, so the block message can say
# WHICH flag is at fault. The last alternative is the getenv/environ.get idiom
# the Codex twin already carried.
#
# Every pattern in this hook is POSIX ERE (grep -E). Do not use grep -P. The
# BSD grep in /usr/bin on macOS has no -P. A failed grep reads as "no match",
# so the guard then allows every edit.
#
# The assignment is a SINGLE `=`: the char before it must not be `=`, `!`,
# `<` or `>`. The canonical default-OFF spelling `os.getenv(KEY,"0") == "1"`
# ends in `== "1"`, and a bare `=\s*"1"` matched it and blocked the correct
# code. The char after `=` is never `=`, because each alternative needs a
# space or a truthy literal next.
_Q="[\"']"
_WS='[[:space:]]'
_END='([^[:alnum:]_]|$)'
_TRUTHY="(True${_END}|1${_END}|${_Q}1${_Q})"
_NAME='VLLM_[A-Z0-9_]{3,}'
TRUTHY_RE="${_NAME}([^#]*[^=!<>#])?=${_WS}*${_TRUTHY}"
TRUTHY_RE="${TRUTHY_RE}|${_NAME}[^#]*,${_WS}*True${_END}"
TRUTHY_RE="${TRUTHY_RE}|${_NAME}[^#]*default${_WS}*=${_WS}*${_TRUTHY}"
TRUTHY_RE="${TRUTHY_RE}|default${_WS}*=${_WS}*${_TRUTHY}[^#]*${_NAME}"
TRUTHY_RE="${TRUTHY_RE}|os\.(environ\.get|getenv)\(${_WS}*${_Q}${_NAME}${_Q}${_WS}*,${_WS}*${_Q}?(1|True)${_Q}?${_WS}*\)"

# Strip comments BEFORE matching: a name mentioned in prose is documentation,
# not a registration, and `# VLLM_X = True` in a diff that turns a flag OFF
# must not read as turning it on. Only a `#` at line start or after whitespace
# opens a comment, so `"a#b"` inside a string literal survives.
CONTENT_NO_COMMENTS=$(printf '%s\n' "$NEW_CONTENT" | sed -E 's/(^|[[:space:]])#.*$//')

OFFENDERS=""
_add_offender() {
    case "$OFFENDERS" in
        *" $1 "*) ;;
        *) OFFENDERS="$OFFENDERS $1 " ;;
    esac
}

# ── VLLM_* branch (envs.py only) ──
if [ "$_IS_ENVS_PY" = "true" ]; then
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        while IFS= read -r _name; do
            [ -z "$_name" ] && continue
            echo "$_name" | grep -qE "$ALLOW_PREFIX_RE" && continue
            printf '%s\n' "$ALLOW_NAMES" | grep -qxF "$_name" && continue
            _add_offender "$_name"
        done < <(printf '%s\n' "$_line" | grep -oE 'VLLM_[A-Z0-9_]{3,}' | sort -u)
    done < <(printf '%s\n' "$CONTENT_NO_COMMENTS" | grep -E "$TRUTHY_RE" || true)
fi

# ── PUMICE_* branch (.py and .rs) ──
# A PUMICE_* name is inspected only where it sits in an env-access idiom, so the
# _PUMICE_MODELS registry dict (never read via getenv/env::var) is not flagged.
PUMICE_OFFENDERS_NEW=""
PUMICE_OFFENDERS_DEFAULT=""
if [ "$_IS_PY" = "true" ] || [ "$_IS_RS" = "true" ]; then
    # Env-access idiom on the line: python getenv/environ, rust env::var/std::env.
    _ACCESSOR_RE='(getenv|environ|env::var|env::var_os|std::env|env![[:space:]]*\()'
    while IFS= read -r _line; do
        [ -z "$_line" ] && continue
        echo "$_line" | grep -qE "$_ACCESSOR_RE" || continue
        while IFS= read -r _name; do
            [ -z "$_name" ] && continue
            if printf '%s\n' "$ALLOWLIST_NAMES" | grep -qxF "$_name"; then
                # Known engine env. Flag only when this line supplies a default:
                # python getenv/get with a 2nd arg, or a rust unwrap_or* fallback.
                if echo "$_line" | grep -qE "${_Q}${_name}${_Q}[[:space:]]*,"; then
                    case "$PUMICE_OFFENDERS_DEFAULT" in *" $_name "*) ;; *) PUMICE_OFFENDERS_DEFAULT="$PUMICE_OFFENDERS_DEFAULT $_name " ;; esac
                elif echo "$_line" | grep -qE 'unwrap_or(_else|_default)?[[:space:]]*\('; then
                    case "$PUMICE_OFFENDERS_DEFAULT" in *" $_name "*) ;; *) PUMICE_OFFENDERS_DEFAULT="$PUMICE_OFFENDERS_DEFAULT $_name " ;; esac
                fi
            else
                # Unknown engine env — a new PUMICE_* name.
                case "$PUMICE_OFFENDERS_NEW" in *" $_name "*) ;; *) PUMICE_OFFENDERS_NEW="$PUMICE_OFFENDERS_NEW $_name " ;; esac
            fi
        done < <(printf '%s\n' "$_line" | grep -oE "${_Q}PUMICE_[A-Z0-9_]{3,}${_Q}" | tr -d "\"'" | sort -u)
    done < <(printf '%s\n' "$CONTENT_NO_COMMENTS")
fi

if [ -n "${OFFENDERS// /}" ]; then
    {
        echo "BLOCKED: campaign VLLM_* env var registered with a truthy default."
        echo ""
        echo "Flag(s):$OFFENDERS"
        echo ""
        cat <<'EOF'
Cross-track contamination prevention requires every campaign gating flag to
default to False (or 0/"0"). The E2E sweep harness enables them explicitly via
opt_env in target.json; a truthy default activates the path precisely when the
key is ABSENT, which is exactly the baseline arm.

Fix: change the default to False (or 0/"0"). Examples:
  VLLM_MOE_TWO_STREAM: bool = False
  "VLLM_MOE_TWO_STREAM": lambda: bool(int(os.getenv("VLLM_MOE_TWO_STREAM", "0"))),

If this flag is upstream vLLM infrastructure rather than a campaign gating flag
(and so legitimately defaults on), add its name to ALLOW_NAMES in this hook and
say why in the same edit.

See: integration-logic.md § Promotion and Finalization
     impl-track-rules.md § Env Flag Naming (PR-Ready)
EOF
    } >&2
    exit 2
fi

if [ -n "${PUMICE_OFFENDERS_NEW// /}" ] || [ -n "${PUMICE_OFFENDERS_DEFAULT// /}" ]; then
    {
        echo "BLOCKED: PUMICE_* engine env change."
        echo ""
        [ -n "${PUMICE_OFFENDERS_NEW// /}" ] && echo "New env(s) not in the allowlist:$PUMICE_OFFENDERS_NEW"
        [ -n "${PUMICE_OFFENDERS_DEFAULT// /}" ] && echo "Default change to allowlisted env(s):$PUMICE_OFFENDERS_DEFAULT"
        echo ""
        cat <<'EOF'
Pumice owns its engine env names and their defaults. AMMO must not invent a new
PUMICE_* env, nor change the default of a known one, inside a campaign — either
silently changes the runtime the A/B arms measure.

Fix:
  - New env: use an env name Pumice already reads, or surface the need to the
    lead so the Pumice team owns it. The known names are in
    _ammo_shared/hooks/pumice_env_allowlist.txt (regenerate at the next Pumice
    pin with the seed grep in that file's header).
  - Default change: leave the Pumice default alone; set the value per arm via
    opt_env in target.json instead.
EOF
    } >&2
    exit 2
fi

exit 0
