#!/usr/bin/env python3
"""Gate Codex blocking guards on an active vLLM-Neuron parity run marker."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


SCRIPTS = {
    "protected-branch": "protected-branch-guard.sh",
    "compile-cache": "compile-cache-guard.sh",
    "venv-opt": "venv-opt-guard.sh",
}


def _load_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_roots(payload: dict[str, Any]) -> list[Path]:
    values = [
        os.environ.get("CODEX_PROJECT_DIR"),
        os.environ.get("CLAUDE_PROJECT_DIR"),
        payload.get("cwd"),
        os.getcwd(),
    ]
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _terminal_is_settled(state: dict[str, Any]) -> bool:
    terminal = state.get("terminal_classification")
    if isinstance(terminal, dict):
        return bool(terminal.get("status") or terminal.get("classification"))
    return bool(terminal)


def _state_is_valid(state_path: Path) -> bool:
    validator = _plugin_root() / "scripts" / "validate_run_state.py"
    if not validator.is_file():
        return False
    try:
        completed = subprocess.run(
            [sys.executable, str(validator), str(state_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _has_active_run(payload: dict[str, Any]) -> bool:
    for root in _candidate_roots(payload):
        marker = root / ".vllm-neuron-parity-run"
        try:
            state_path = Path(marker.read_text(encoding="utf-8").splitlines()[0].strip())
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (IndexError, OSError, TypeError, ValueError):
            continue
        if (
            isinstance(state, dict)
            and _state_is_valid(state_path)
            and not _terminal_is_settled(state)
        ):
            return True
    return False


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guard", choices=tuple(SCRIPTS))
    args = parser.parse_args(argv)

    raw = sys.stdin.read()
    payload = _load_payload(raw)
    if not payload or not _has_active_run(payload):
        return 0

    script = (
        _plugin_root()
        / "skills"
        / "vllm-neuron-parity"
        / "hooks"
        / SCRIPTS[args.guard]
    )
    if not script.is_file():
        return 0
    cwd = payload.get("cwd")
    run_cwd = cwd if isinstance(cwd, str) and Path(cwd).is_dir() else os.getcwd()
    try:
        completed = subprocess.run(
            [str(script)],
            input=raw,
            text=True,
            capture_output=True,
            check=False,
            cwd=run_cwd,
            env=os.environ.copy(),
        )
    except OSError:
        return 0
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
