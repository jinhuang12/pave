#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <worktree-path-or-name> [main-repo]" >&2
  exit 2
fi

INPUT_PATH="$1"
# The engine checkout and its python come from the resolution layer
# (target.json engine.root and engine.venv, conventions sections 2-4). Without a
# campaign: the git toplevel of the current dir and its .venv.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMPAIGN_PATHS="$SCRIPT_DIR/../../scripts/campaign_paths.py"
if [[ -f "$CAMPAIGN_PATHS" ]] && campaign_env="$(python3 "$CAMPAIGN_PATHS" env 2>/dev/null)"; then
  eval "$campaign_env"
fi
MAIN_REPO="${2:-${AMMO_ENGINE_ROOT:-$(git rev-parse --show-toplevel)}}"
MAIN_REPO="$(cd "$MAIN_REPO" && pwd)"

if [[ "$INPUT_PATH" == */* ]]; then
  WORKTREE_PATH="$INPUT_PATH"
else
  WORKTREE_PATH="$MAIN_REPO/.codex/worktrees/$INPUT_PATH"
fi

if [[ ! -d "$WORKTREE_PATH" ]]; then
  exit 0
fi

BRANCH_NAME="$(git -C "$WORKTREE_PATH" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"

if ! git -C "$WORKTREE_PATH" diff --quiet 2>/dev/null || ! git -C "$WORKTREE_PATH" diff --cached --quiet 2>/dev/null; then
  echo "Warning: worktree has uncommitted changes: $WORKTREE_PATH" >&2
fi

git -C "$MAIN_REPO" worktree remove --force "$WORKTREE_PATH" >/dev/null 2>&1 || {
  rm -rf "$WORKTREE_PATH"
  git -C "$MAIN_REPO" worktree prune >/dev/null 2>&1 || true
}

if [[ "$BRANCH_NAME" == ammo/* || "$BRANCH_NAME" == worktree-* ]]; then
  git -C "$MAIN_REPO" branch -D "$BRANCH_NAME" >/dev/null 2>&1 || true
fi
