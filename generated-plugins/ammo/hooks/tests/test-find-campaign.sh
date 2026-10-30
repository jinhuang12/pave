#!/usr/bin/env bash
# Harness for hooks/_find_campaign.sh (conventions §2 and §7).
#
# Cases: env override, marker walk from the engine root, walk from a track
# worktree child, no campaign; stdout and stderr stay empty on every failure
# path; the declare -F guard; the helper never exits the sourcing shell;
# ammo_in_campaign and ammo_python.
#
# Usage: bash hooks/tests/test-find-campaign.sh    (exit 1 on any failure)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$HERE/../_find_campaign.sh"
PASS=0
FAIL=0
TMP="$(mktemp -d "${TMPDIR:-/tmp}/test-find-campaign.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
TMP="$(cd "$TMP" && pwd -P)"

ok()   { PASS=$((PASS + 1)); echo "  PASS  $1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  FAIL  $1: $2"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected [$3] got [$2]"; fi; }

CID="20261028-120000-abc123"
ENGINE="$TMP/engine with space"
CAMPAIGN="$ENGINE/.ammo/$CID"
mkdir -p "$CAMPAIGN" "$ENGINE/.claude/worktrees/op-001/pumice-rust/src" "$TMP/none"
printf '%s\n' "$CID" > "$ENGINE/.ammo/active"
printf '{"engine": {"root": "%s"}, "target": {"tp": 2}}\n' "$ENGINE" > "$CAMPAIGN/target.json"

# Run a snippet in a clean bash that sourced the helper. No AMMO_* names leak in.
# Usage: in_shell [NAME=value ...] -- 'snippet'
in_shell() {
    local -a extra=()
    while [ "$1" != "--" ]; do extra+=("$1"); shift; done
    shift
    env -i PATH="$PATH" HOME="$TMP" TMPDIR="${TMPDIR:-/tmp}" ${extra[@]+"${extra[@]}"} \
        bash -c "set -euo pipefail; source \"\$1\"; $1" _ "$HELPER"
}
payload() { printf '{"cwd": "%s", "session_id": "s1"}' "$1"; }

echo "test-find-campaign.sh"

# 1. env override
OUT=$(in_shell "AMMO_ARTIFACT_DIR=$CAMPAIGN" -- '_find_campaign "{}"; echo "rc=$? dir=$AMMO_ARTIFACT_DIR sid=$AMMO_SESSION_ID"' 2>&1)
check "env override" "$OUT" "rc=0 dir=$CAMPAIGN sid=$CID"

# 2. walk from the engine root
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE")'; echo \"rc=\$? dir=\$AMMO_ARTIFACT_DIR tp=\$AMMO_TP_SIZE\"" 2>&1)
check "walk from engine root" "$OUT" "rc=0 dir=$CAMPAIGN tp=2"

# 3. walk from a track worktree child
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE/.claude/worktrees/op-001/pumice-rust/src")'; echo \"rc=\$? root=\$AMMO_ENGINE_ROOT\"" 2>&1)
check "walk from worktree child" "$OUT" "rc=0 root=$ENGINE"

# 3b. CLAUDE_PROJECT_DIR is the second start when the payload cwd misses
OUT=$(in_shell "CLAUDE_PROJECT_DIR=$ENGINE" -- "_find_campaign '$(payload "$TMP/none")' && echo \"dir=\$AMMO_ARTIFACT_DIR\"" 2>&1)
check "falls back to CLAUDE_PROJECT_DIR" "$OUT" "dir=$CAMPAIGN"

# 4. none
OUT=$(cd "$TMP/none" && in_shell -- "_find_campaign '$(payload "$TMP/none")' || echo \"rc=\$? dir=[\${AMMO_ARTIFACT_DIR:-}]\"" 2>&1)
check "no campaign returns 1" "$OUT" "rc=1 dir=[]"

# 5. silent on every failure path (stdout and stderr both empty)
silent_case() {
    local label="$1" snippet="$2"
    local out
    out=$(cd "$TMP/none" && in_shell -- "$snippet" 2>&1)
    check "silent: $label" "$out" ""
}
silent_case "no campaign"        "_find_campaign '$(payload "$TMP/none")' || true"
silent_case "malformed input"    "_find_campaign 'not json {' || true"
silent_case "empty input"        "_find_campaign '' || true"
silent_case "no argument"        "_find_campaign || true"
silent_case "stale env dir"      "AMMO_ARTIFACT_DIR='$TMP/gone' _find_campaign '{}' || true"
OUT=$(in_shell "AMMO_ARTIFACT_DIR=$TMP/gone" -- "_find_campaign '{}' || echo rc=\$?" 2>&1)
check "stale env dir returns 1" "$OUT" "rc=1"
mkdir -p "$TMP/badmarker/.ammo" && printf '../escape\n' > "$TMP/badmarker/.ammo/active"
silent_case "bad marker"         "_find_campaign '$(payload "$TMP/badmarker")' || true"
OUT=$(in_shell -- "_find_campaign '$(payload "$TMP/badmarker")' || echo rc=\$?" 2>&1)
check "bad marker returns 1" "$OUT" "rc=1"
mkdir -p "$TMP/nopy" && ln -sf "$(command -v bash)" "$TMP/nopy/bash" && ln -sf "$(command -v env)" "$TMP/nopy/env"
OUT=$(cd "$TMP/none" && env -i PATH="$TMP/nopy" HOME="$TMP" "$TMP/nopy/bash" -c \
    "set -euo pipefail; source \"\$1\"; _find_campaign '$(payload "$ENGINE")' || echo rc=\$?" _ "$HELPER" 2>&1)
check "no python3 on PATH fails open" "$OUT" "rc=1"
silent_case "in_campaign without campaign" "ammo_in_campaign '$CAMPAIGN/state.json' || true"
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE")'" 2>&1)
check "silent: success path" "$OUT" ""

# 6. declare -F guard: a second source does not redefine the functions
OUT=$(in_shell -- "declare -F _find_campaign ammo_in_campaign ammo_python >/dev/null && echo defined; _find_campaign() { echo kept; }; source \"\$1\"; _find_campaign" 2>&1)
check "declare -F guard" "$OUT" "defined
kept"

# 7. never exits the sourcing shell, even under set -e
OUT=$(in_shell -- "_find_campaign 'garbage' || true; ammo_in_campaign /etc || true; echo alive" 2>&1)
check "never exit" "$OUT" "alive"

# 8. ammo_in_campaign
ln -s "$TMP/none" "$CAMPAIGN/escape"
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE")'
  ammo_in_campaign \"\$AMMO_ARTIFACT_DIR/rounds/1/x.json\" && echo in
  ammo_in_campaign '$ENGINE/src/x.py' || echo out
  ammo_in_campaign \"\$AMMO_ARTIFACT_DIR/escape/x\" || echo symlink-out
  ammo_in_campaign '' || echo empty-out" 2>&1)
check "ammo_in_campaign" "$OUT" "in
out
symlink-out
empty-out"

# 9. ammo_python runs a plugin script under the campaign python
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE")'; cd '$TMP/none'; ammo_python campaign_paths.py find" 2>&1)
check "ammo_python runs a sibling script" "$OUT" "$CAMPAIGN"
mkdir -p "$ENGINE/.venv/bin"
printf '#!/bin/sh\necho "venv-python $AMMO_SESSION_ID"\n' > "$ENGINE/.venv/bin/python"
chmod +x "$ENGINE/.venv/bin/python"
OUT=$(in_shell -- "_find_campaign '$(payload "$ENGINE")'; ammo_python campaign_paths.py find" 2>&1)
check "ammo_python uses the campaign venv" "$OUT" "venv-python $CID"

echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
