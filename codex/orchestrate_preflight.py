#!/usr/bin/env python3
"""Check orchestrate's installed files; the lead must also check live Codex tools."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codex.preflight import PreflightError, require_current_agents

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = (
    "codex/skills/orchestrate/SKILL.md",
    "codex/skills/orchestrate/references/protocol.md",
    "codex/skills/orchestrate/references/subtask.md",
    "codex/skills/orchestrate/references/model-seats.md",
    "skills/orchestrate/scripts/orchestrate_log.py",
    "skills/orchestrate/hooks/goal_restate.sh",
)


def check(project: Path, codex_home: Path) -> dict[str, str]:
    for relative in RESOURCES:
        path = ROOT / relative
        if not path.is_file() or not os.access(path, os.R_OK):
            raise PreflightError(f"missing or unreadable resource: {path}")
    scope = require_current_agents(project / ".codex" / "agents", codex_home / "agents")
    return {
        "files": "current",
        "agent_scope": scope,
        "runtime": "not verified: check live V2 tools, named roles, model access, and available slots",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    try:
        result = check(project, codex_home)
    except (PreflightError, OSError, ValueError) as error:
        print(f"orchestrate file check failed: {error}", file=sys.stderr)
        print(f"Install the reviewed roles explicitly: python3 {ROOT}/codex/install_agents.py --project {project}", file=sys.stderr)
        print("Restart Codex after installation; project roles require a trusted project.", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
