#!/bin/bash
# Test harness for worktree-remove-cleanup.sh (WorktreeRemove hook).
# Run: bash hooks/tests/test-worktree-remove-cleanup.sh
#
# Claude Code counts the worktree as removed when this hook exits 0, and it
# never deletes the branch itself. So the hook removes the worktree in and
# out of a campaign. Checks:
#   1. Outside a campaign: the plain worktree (branch worktree-<name>) is
#      removed, its branch is deleted, exit 0, stdout empty.
#   2. In a campaign: the track worktree (branch session/<id>/ammo-<name>) is
#      removed and its branch is kept (the track's commits live there).
#   3. A dir git does not know as a worktree is still removed, and git's
#      error goes to stderr, never stdout.
#   4. A missing worktree_path or a path that does not exist: exit 0, silent.
#
# REPO is a git repository; CAMP is an engine root with .ammo/active = camp1.

set -uo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/worktree-remove-cleanup.sh}"
PASS=0
FAIL=0
TOTAL=0

TMPDIR=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

new_repo() {
    git init -q "$1" && git -C "$1" commit -q --allow-empty -m init
}
REPO="$TMPDIR/repo"
CAMP="$TMPDIR/engine"
new_repo "$REPO"
new_repo "$CAMP"
mkdir -p "$CAMP/.ammo/camp1"
printf 'camp1\n' > "$CAMP/.ammo/active"

check() {
    local name="$1" ok="$2" detail="${3:-}"
    TOTAL=$((TOTAL + 1))
    if [ "$ok" = yes ]; then
        echo "  PASS [$TOTAL]: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $name $detail"
        FAIL=$((FAIL + 1))
    fi
}

# run_hook PROJECT_DIR WORKTREE_PATH: runs from PROJECT_DIR with
# CLAUDE_PROJECT_DIR set to it; leaves RC, OUT, ERR.
run_hook() {
    local proj="$1" wt="$2" json
    json=$(jq -nc --arg cwd "$proj" --arg p "$wt" \
        '{session_id:"s1",cwd:$cwd,hook_event_name:"WorktreeRemove",worktree_path:$p}')
    RC=0
    printf '%s' "$json" | (cd "$proj" && CLAUDE_PROJECT_DIR="$proj" bash "$HOOK") \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || RC=$?
    OUT=$(cat "$TMPDIR/out")
    ERR=$(cat "$TMPDIR/err")
}
has_branch() { git -C "$1" show-ref --verify --quiet "refs/heads/$2"; }
yn() { if "$@"; then echo yes; else echo no; fi; }

echo "== 1. outside a campaign: plain worktree =="
WT1="$REPO/.claude/worktrees/plain"
git -C "$REPO" worktree add -q -b worktree-plain "$WT1" HEAD
run_hook "$REPO" "$WT1"
check "1a exit 0" "$(yn [ "$RC" -eq 0 ])" "rc=$RC err=$ERR"
check "1b stdout empty" "$(yn [ -z "$OUT" ])" "out=$OUT"
check "1c worktree dir removed" "$(yn [ ! -e "$WT1" ])"
check "1d branch worktree-plain deleted" "$(yn eval '! has_branch "$REPO" worktree-plain')"
check "1e git no longer lists the worktree" \
    "$(yn eval '! git -C "$REPO" worktree list --porcelain | grep -qF "$WT1"')"

echo "== 2. in a campaign: track worktree =="
WT2="$CAMP/.claude/worktrees/op1"
git -C "$CAMP" worktree add -q -b session/s1/ammo-op1 "$WT2" HEAD
run_hook "$CAMP" "$WT2"
check "2a exit 0" "$(yn [ "$RC" -eq 0 ])" "rc=$RC err=$ERR"
check "2b stdout empty" "$(yn [ -z "$OUT" ])" "out=$OUT"
check "2c track worktree dir removed" "$(yn [ ! -e "$WT2" ])"
check "2d track branch kept" "$(yn has_branch "$CAMP" session/s1/ammo-op1)"

echo "== 3. a dir git does not know =="
WT3="$REPO/.claude/worktrees/stray"
mkdir -p "$WT3"
echo x > "$WT3/f"
run_hook "$REPO" "$WT3"
check "3a exit 0" "$(yn [ "$RC" -eq 0 ])" "rc=$RC err=$ERR"
check "3b stdout empty (git's error goes to stderr)" "$(yn [ -z "$OUT" ])" "out=$OUT"
check "3c stray dir removed" "$(yn [ ! -e "$WT3" ])"

echo "== 4. nothing to remove =="
run_hook "$REPO" "$REPO/.claude/worktrees/missing"
check "4a missing path: exit 0, silent" "$(yn eval '[ "$RC" -eq 0 ] && [ -z "$OUT" ]')" "rc=$RC out=$OUT"
json=$(jq -nc --arg cwd "$REPO" '{session_id:"s1",cwd:$cwd,hook_event_name:"WorktreeRemove"}')
RC=0
OUT=$(printf '%s' "$json" | (cd "$REPO" && bash "$HOOK") 2>/dev/null) || RC=$?
check "4b no worktree_path: exit 0, silent" "$(yn eval '[ "$RC" -eq 0 ] && [ -z "$OUT" ]')" "rc=$RC out=$OUT"

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
[ "$FAIL" -eq 0 ]
