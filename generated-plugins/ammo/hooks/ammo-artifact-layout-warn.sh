#!/bin/bash
# PostToolUse hook — non-blocking warning when files land outside the
# canonical AMMO V2 artifact layout under the campaign dir.
#
# Reference: references/artifact-layout.md
#
# Matchers (hooks/hooks.json): Write, Edit, Bash.
#   - Write/Edit: extracts tool_input.file_path
#   - Bash: scans the command for path tokens that name the campaign dir: the
#     campaign_root form from scripts/artifact_layout.json (for example an
#     engine-relative token), or a $AMMO_ARTIFACT_DIR / ${AMMO_ARTIFACT_DIR}
#     prefix.
#
# Campaign scope (_find_campaign.sh): a path is checked only when it resolves
# inside the campaign dir (ammo_in_campaign), and is checked relative to it.
# Outside a campaign the hook exits 0 and prints nothing.
#
# Non-blocking: only emits additionalContext warnings. Never returns
# {"decision": "block"} — layout drift is an organizational hazard, not a
# correctness violation.
#
# Fail-open: any internal error (jq missing, malformed JSON) exits 0 silently.
set -euo pipefail
trap 'exit 0' ERR

if ! command -v jq &>/dev/null; then exit 0; fi

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // ""' 2>/dev/null) || true
[ -z "$TOOL" ] && exit 0
case "$TOOL" in
    Write|Edit|MultiEdit|Bash) ;;
    *) exit 0 ;;
esac

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$HOOK_DIR/.." && pwd)"
source "$HOOK_DIR/_find_campaign.sh"
_find_campaign "$INPUT" || exit 0
HOOK_CWD=$(echo "$INPUT" | jq -r '.cwd // ""' 2>/dev/null) || HOOK_CWD=""
REL_BASE="${HOOK_CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"

# Allowed regex patterns relative to {artifact_dir}/, DERIVED from the shared
# manifest scripts/artifact_layout.json. No path fact is restated
# here: the manifest owns every template, this hook owns detection only. Adding
# a slot is a one-file edit, and the Codex twin reads the same manifest.
#
# Emitted per slot: an exact `^...$` pattern for a file template, a
# `^...(/|$)` prefix pattern for a directory template (trailing `/`), plus an
# exact pattern for every proper directory ancestor, so a bare `rounds/3/sweeps`
# token reads as a directory reference and not as drift.
#
# The first output line is "ROOT <regex>": the campaign_root key compiled the
# same way. The Bash extractor uses it to find campaign path tokens, so the
# root name is read from the manifest and never restated here.
#
# Fail-open and LOUD: a missing or corrupt manifest (or one without the
# campaign_root key) disables checking with a stderr diagnostic. It never
# degrades to an empty allowlist, which would warn on every correct write.
LAYOUT_MANIFEST="$PLUGIN_ROOT/scripts/artifact_layout.json"

PATTERN_TEXT=$(AMMO_LAYOUT_MANIFEST="$LAYOUT_MANIFEST" python3 -c '
import json, os, sys

_ERE_SPECIAL = set(r".^$*+?()[]{}|\\")


def ere_escape(text):
    return "".join("\\" + c if c in _ERE_SPECIAL else c for c in text)


def compile_template(template, tokens, placeholders):
    out, i = [], 0
    while i < len(template):
        for token in tokens:
            if template.startswith(token, i):
                out.append(placeholders[token])
                i += len(token)
                break
        else:
            out.append(ere_escape(template[i]))
            i += 1
    return "".join(out)


manifest = json.load(open(os.environ["AMMO_LAYOUT_MANIFEST"], encoding="utf-8"))
placeholders = manifest["placeholders"]
tokens = sorted(placeholders, key=len, reverse=True)
slots = manifest["slots"]
if not slots:
    raise SystemExit("empty slots")

root = manifest["campaign_root"]
if not isinstance(root, str) or not root.strip("/"):
    raise SystemExit("no campaign_root")
sys.stdout.write("ROOT " + compile_template(root, tokens, placeholders) + "\n")

templates = [slot["path_template"] for slot in slots.values()]
templates += manifest.get("open_dirs", [])

patterns, ancestors = [], set()
for template in templates:
    body = compile_template(template.rstrip("/"), tokens, placeholders)
    patterns.append("^" + body + ("(/|$)" if template.endswith("/") else "$"))
    segments = template.strip("/").split("/")
    for cut in range(1, len(segments)):
        prefix = "/".join(segments[:cut])
        ancestors.add("^" + compile_template(prefix, tokens, placeholders) + "$")
for pattern in patterns + sorted(ancestors):
    sys.stdout.write(pattern + "\n")
' 2>/dev/null) || PATTERN_TEXT=""

if [ -z "$PATTERN_TEXT" ]; then
    echo "LAYOUT WARN HOOK DISABLED: cannot load layout manifest ${LAYOUT_MANIFEST}. Layout drift is unchecked until it is restored." >&2
    exit 0
fi

ROOT_RE=""
ALLOWED_PATTERNS=()
while IFS= read -r line; do
    case "$line" in
        "ROOT "*) ROOT_RE="${line#ROOT }" ;;
        ?*) ALLOWED_PATTERNS+=("$line") ;;
    esac
done <<< "$PATTERN_TEXT"

if [ -z "$ROOT_RE" ]; then
    echo "LAYOUT WARN HOOK DISABLED: layout manifest ${LAYOUT_MANIFEST} has no campaign_root." >&2
    exit 0
fi

if [ "${#ALLOWED_PATTERNS[@]}" -eq 0 ]; then
    echo "LAYOUT WARN HOOK DISABLED: layout manifest ${LAYOUT_MANIFEST} produced no patterns." >&2
    exit 0
fi

# Helper: emit a warning for a single non-conforming relative path.
emit_warn() {
    local rel="$1"
    local msg
    msg="LAYOUT WARN: ${rel} is outside the canonical AMMO V2 layout. Expected: rounds/{N}/{profiling|sweeps|mining|debate|tracks|audits|_archive}/... See ${PLUGIN_ROOT}/references/artifact-layout.md § Prohibited Patterns."
    jq -c -n --arg msg "$msg" '
    {
        hookSpecificOutput: {
            hookEventName: "PostToolUse",
            additionalContext: $msg
        }
    }'
}

# Helper: resolve path tokens (one per line on stdin). A leading
# $AMMO_ARTIFACT_DIR / ${AMMO_ARTIFACT_DIR} expands to the campaign dir; a
# relative token resolves against REL_BASE (the tool call's cwd). Prints
# "<realpath>\t<path relative to the campaign dir>" per token; tokens that
# leave the campaign dir print nothing. ammo_in_campaign still decides.
resolve_paths() {
    AMMO_LAYOUT_BASE="$REL_BASE" python3 -c '
import os, sys
camp = os.path.realpath(os.environ["AMMO_ARTIFACT_DIR"])
base = os.environ["AMMO_LAYOUT_BASE"]
for raw in sys.stdin.read().splitlines():
    p = raw.strip()
    if not p:
        continue
    for var in ("${AMMO_ARTIFACT_DIR}", "$AMMO_ARTIFACT_DIR"):
        if p.startswith(var):
            p = os.environ["AMMO_ARTIFACT_DIR"] + p[len(var):]
            break
    real = os.path.realpath(os.path.join(base, os.path.expanduser(p)))
    rel = os.path.relpath(real, camp)
    if rel == "." or rel == ".." or rel.startswith("../"):
        continue
    print(real + "\t" + rel)
' 2>/dev/null || true
}

# Helper: classify one resolved path. Returns 0 (conforming) or 1 (warn-worthy).
# Prints the relative path on warning.
check_path() {
    local p="$1" rel="$2"
    ammo_in_campaign "$p" || return 0

    # Walk allowed patterns. A bare directory token — which the Bash extractor
    # below yields from ordinary `ls`/`grep` commands — matches either its own
    # directory-prefix pattern or one of the manifest-derived ancestor patterns,
    # so `rounds/1/mining` and `rounds/1/sweeps/opt` do not warn.
    rel="${rel%/}"
    for pat in "${ALLOWED_PATTERNS[@]}"; do
        if echo "$rel" | grep -Eq "$pat"; then
            return 0
        fi
    done

    emit_warn "$rel"
    return 1
}

case "$TOOL" in
    Write|Edit|MultiEdit)
        FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""' 2>/dev/null) || true
        [ -z "$FILE_PATH" ] && exit 0
        PATHS="$FILE_PATH"
        ;;
    Bash)
        CMD=$(echo "$INPUT" | jq -r '.tool_input.command // ""' 2>/dev/null) || true
        [ -z "$CMD" ] && exit 0
        # Extract candidate path tokens that name the campaign dir: the
        # campaign_root form (ROOT_RE, from the manifest) anywhere in the
        # token, or a $AMMO_ARTIFACT_DIR / ${AMMO_ARTIFACT_DIR} prefix. Dedup,
        # then resolve and check each. Quotes are handled by the tokenizer.
        PATHS=$(printf '%s' "$CMD" | AMMO_LAYOUT_ROOT_RE="$ROOT_RE" python3 -c '
import os, sys, re
text = sys.stdin.read()
root = os.environ["AMMO_LAYOUT_ROOT_RE"]
# Stop at whitespace, backticks, or quote boundaries.
stop = r"[^\s`\"'\''<>|;)]"
token = re.compile(
    r"(?:\$\{AMMO_ARTIFACT_DIR\}|\$AMMO_ARTIFACT_DIR)/" + stop + r"+"
    + r"|[^\s`\"'\''<>|;]*(?:" + root + r")" + stop + r"+"
)
seen = set()
out = []
for m in token.finditer(text):
    p = m.group(0).rstrip("/.,;:")
    if p and p not in seen:
        seen.add(p)
        out.append(p)
for p in out:
    print(p)
' 2>/dev/null) || true
        ;;
esac

if [ -n "$PATHS" ]; then
    while IFS=$'\t' read -r abs rel; do
        [ -n "$abs" ] && [ -n "$rel" ] || continue
        check_path "$abs" "$rel" || true
    done < <(printf '%s\n' "$PATHS" | resolve_paths)
fi

exit 0
