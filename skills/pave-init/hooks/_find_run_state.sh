#!/usr/bin/env bash
# _find_run_state.sh -- sourceable run-state discovery shared by the two
# lead-alignment hooks.
#
# Usage:
#   HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
#   . "$HOOK_DIR/_find_run_state.sh"
#   find_run_state
#   # -> FOUND_STATE       path of run-state.json, or "" when none exists
#   # -> FOUND_STATE_LABEL "active run" (marker hit) or the stale-scan label
#
# Preference order:
#   1. The active-run marker `<root>/.pave-init-run` -- a one-line file
#      holding the absolute path of the live run-state.json, written by the
#      lead at run start (SKILL.md, Run workspace). The target must exist to
#      count.
#   2. Fallback: the newest `.pave/*/run-state.json` by mtime, labeled as
#      possibly belonging to a different run.
#
# Roots searched, most specific first: CLAUDE_PROJECT_DIR (set by Claude
# Code for hook processes), then $PWD. The installed skill directory is
# never a root: run artifacts never live inside the pave-init package.
#
# Contract: never exits, never writes stdout/stderr (hooks reserve stdout
# for JSON). Safe to source more than once.

if ! declare -F find_run_state >/dev/null 2>&1; then

find_run_state() {
  FOUND_STATE=""
  FOUND_STATE_LABEL="active run"
  FOUND_STATE_VIA=""

  # Quoted iteration: root paths may contain spaces.
  local root marker candidate
  for root in "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
    [ -n "$root" ] || continue
    marker="$root/.pave-init-run"
    if [ -f "$marker" ]; then
      candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        FOUND_STATE="$candidate"
        FOUND_STATE_VIA="marker"
        return 0
      fi
    fi
  done

  # Scan fallback: NOT ownership evidence. The hooks act only on
  # FOUND_STATE_VIA="marker" -- a glob hit may belong to an abandoned run or
  # a different session, and blocking on it would misfire. The scan exists
  # for lead-driven resume discovery, where judgment applies.
  FOUND_STATE_LABEL="newest run workspace by mtime — may not be this run"
  for root in "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
    [ -n "$root" ] || continue
    [ -d "$root/.pave" ] || continue
    candidate="$(ls -t "$root"/.pave/*/run-state.json 2>/dev/null | head -n 1)"
    if [ -n "$candidate" ]; then
      FOUND_STATE="$candidate"
      FOUND_STATE_VIA="scan"
      return 0
    fi
  done

  return 0
}

fi
