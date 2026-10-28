#!/bin/bash
set -euo pipefail

# WorktreeRemove hook for the Pumice engine.
# Cleans up a worktree and its associated git branch.
#
# Claude Code runs this hook for a worktree that worktree-create-with-build.sh
# made, counts the worktree as removed when the hook exits 0, and never
# deletes the branch itself. So the hook removes the worktree in and out of a
# campaign (_find_campaign.sh):
#   - outside a campaign: the plain worktree; its worktree-<name> branch is
#     deleted.
#   - inside a campaign: the track worktree; its session/<id>/ammo-<name>
#     branch is kept, because the track's commits live there. The campaign
#     also gives the engine root as the last place to find the main repo.
#
# Input (stdin JSON): { worktree_path, ... }
# All output goes to stderr (git's too: `>&2 2>&1`, never `2>&1 >&2`, which
# sends git's stderr to stdout). Stdout stays empty.

INPUT=$(cat)
WORKTREE_PATH=$(echo "$INPUT" | jq -r '.worktree_path // empty')
[ -z "$WORKTREE_PATH" ] && exit 0
[ ! -d "$WORKTREE_PATH" ] && exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HOOK_DIR/_find_campaign.sh"
IN_CAMPAIGN=1
_find_campaign "$INPUT" || IN_CAMPAIGN=0

MAIN_REPO="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$MAIN_REPO" ]; then
    MAIN_REPO=$(git -C "$WORKTREE_PATH" rev-parse --path-format=absolute --git-common-dir 2>/dev/null | sed 's|/\.git$||') || MAIN_REPO=""
fi
if [ -z "$MAIN_REPO" ] && [ "$IN_CAMPAIGN" = 1 ]; then
    MAIN_REPO="${AMMO_ENGINE_ROOT:-}"
fi

WORKTREE_NAME=$(basename "$WORKTREE_PATH")
echo "Removing worktree '$WORKTREE_NAME'..." >&2

# Detect actual branch before removal (may be ammo/* or worktree-*)
BRANCH_NAME=$(git -C "$WORKTREE_PATH" rev-parse --abbrev-ref HEAD 2>/dev/null || true)

# Warn about uncommitted changes
if ! git -C "$WORKTREE_PATH" diff --quiet 2>/dev/null || \
   ! git -C "$WORKTREE_PATH" diff --cached --quiet 2>/dev/null; then
    echo "WARN: Worktree has uncommitted changes" >&2
fi

# Remove the worktree
git -C "$MAIN_REPO" worktree remove --force "$WORKTREE_PATH" >&2 2>&1 || {
    rm -rf "$WORKTREE_PATH" 2>/dev/null || true
    git -C "$MAIN_REPO" worktree prune >&2 2>&1 || true
}

# Delete the branch (only worktree-* and ammo/* branches, never main/master)
if [[ "$BRANCH_NAME" == worktree-* || "$BRANCH_NAME" == ammo/* ]]; then
    git -C "$MAIN_REPO" branch -D "$BRANCH_NAME" >&2 2>&1 && \
        echo "Deleted branch $BRANCH_NAME" >&2 || true
fi

echo "Done." >&2
