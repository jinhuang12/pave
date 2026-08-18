#!/usr/bin/env python3
"""Adapt Codex PostToolUse payloads to the canonical PAVE hooks.

Two wire-level differences require an adapter:

* Current Codex source schemas carry optional subagent identity on normal
  tool hooks, while some release documentation omits those fields.
  ``subagent_activity.py`` supplies a fail-safe session latch for runtimes that
  omit them.
* Codex reports file edits as ``apply_patch`` with the patch text in
  ``tool_input.command``.  The canonical Claude hook expects one ``file_path``
  plus written ``content`` or ``new_string``.  This module expands one patch
  into one canonical payload per affected file and combines the warnings.

The hooks are observing controls.  Any adapter failure exits 0 and emits no
context; the canonical prose and validators remain the decline path.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable

try:
    from .subagent_activity import active_agent_ids
except ImportError:  # Executed as a script rather than a package module.
    from subagent_activity import active_agent_ids  # type: ignore

_PATCH_FILE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$")
_MOVE_TO = re.compile(r"^\*\*\* Move to: (.+?)\s*$")


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # <root>/codex/hooks/post_tool_use_router.py
    return Path(__file__).resolve().parents[2]


def _load_payload(stream: Any = sys.stdin) -> dict[str, Any]:
    try:
        data = json.load(stream)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def extract_patch_sections(command: str) -> list[tuple[str, str]]:
    """Return ``(path, added_text)`` pairs from an apply_patch command.

    The parser intentionally ignores context and removed lines.  The canonical
    planning hook needs only the target path and newly written text for its
    current checks.  A rename produces entries for both the old and new paths.
    """

    if not command:
        return []

    lines = command.splitlines()
    sections: list[tuple[str, str]] = []
    current_path: str | None = None
    current_kind: str | None = None
    added: list[str] = []
    moved_to: str | None = None

    def flush() -> None:
        nonlocal current_path, current_kind, added, moved_to
        if current_path:
            text = "\n".join(added)
            if added:
                text += "\n"
            sections.append((current_path, text))
            if moved_to and moved_to != current_path:
                sections.append((moved_to, text))
        current_path = None
        current_kind = None
        added = []
        moved_to = None

    for line in lines:
        match = _PATCH_FILE.match(line)
        if match:
            flush()
            current_kind, current_path = match.group(1), match.group(2).strip()
            continue
        move = _MOVE_TO.match(line)
        if move and current_path:
            moved_to = move.group(1).strip()
            continue
        if line.startswith("*** End Patch"):
            flush()
            continue
        if not current_path or current_kind == "Delete":
            continue
        # A literal added line starts with one '+'.  Skip diff metadata if a
        # future producer emits it inside a section.
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])

    flush()

    # Keep first occurrence order while merging repeated sections for one file.
    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for path, text in sections:
        if path not in merged:
            merged[path] = []
            order.append(path)
        if text:
            merged[path].append(text)
    return [(path, "".join(merged[path])) for path in order]


def _canonical_script(name: str) -> Path:
    return _plugin_root() / "skills" / "pave-init" / "hooks" / name


def _run_canonical(script: Path, payload: dict[str, Any]) -> tuple[int, str, str]:
    if not script.is_file():
        return 0, "", ""
    try:
        completed = subprocess.run(
            [str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            check=False,
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
    except OSError:
        return 0, "", ""
    return completed.returncode, completed.stdout, completed.stderr


def _context_from_output(stdout: str) -> str:
    if not stdout.strip():
        return ""
    try:
        data = json.loads(stdout)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    specific = data.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return ""
    context = specific.get("additionalContext")
    return str(context) if context else ""


def _emit_context(parts: Iterable[str]) -> None:
    clean = [part.strip() for part in parts if part and part.strip()]
    if not clean:
        return
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "\n\n".join(clean),
                }
            }
        )
    )


def _payload_has_subagent_identity(payload: dict[str, Any]) -> bool:
    agent_id = str(payload.get("agent_id") or "")
    agent_type = str(payload.get("agent_type") or "")
    return bool(agent_id or agent_type)


def _run_staleness(payload: dict[str, Any]) -> int:
    session_id = str(payload.get("session_id") or "")
    # Current Codex hook schemas include optional agent_id/agent_type on normal
    # tool hooks.  The canonical script consumes those fields directly.  The
    # activity latch is only a fail-safe for a runtime that omits them: while a
    # PAVE worker is active, silence this lead-only reminder rather than send a
    # sole-writer duty to an unknown caller.
    if not _payload_has_subagent_identity(payload) and active_agent_ids(session_id):
        return 0

    _, stdout, _ = _run_canonical(
        _canonical_script("state_staleness_reminder.sh"), payload
    )
    if stdout:
        sys.stdout.write(stdout)
    return 0


def _canonical_layout_payloads(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []

    direct_path = tool_input.get("file_path")
    if direct_path:
        return [payload]

    command = tool_input.get("command") or tool_input.get("patch") or ""
    if not isinstance(command, str):
        return []

    sections = extract_patch_sections(command)
    if not sections:
        return []

    session_id = str(payload.get("session_id") or "")
    active = sorted(active_agent_ids(session_id))
    direct_identity = _payload_has_subagent_identity(payload)
    # On a legacy runtime with no caller identity, an active-worker interval is
    # ambiguous.  Skip this advisory check instead of inventing worker identity
    # and producing a false sole-writer warning against a concurrent lead edit.
    if active and not direct_identity:
        return []

    adapted: list[dict[str, Any]] = []
    for file_path, content in sections:
        item = copy.deepcopy(payload)
        item["tool_input"] = {
            "file_path": file_path,
            "content": content,
            "new_string": content,
        }
        # copy.deepcopy preserves direct agent_id/agent_type fields from the
        # Codex payload.  The canonical layout script applies its own identity
        # gate, so no synthetic role assignment is needed.
        adapted.append(item)
    return adapted


def _run_layout(payload: dict[str, Any]) -> int:
    contexts: list[str] = []
    script = _canonical_script("planning-layout-warn.sh")
    for item in _canonical_layout_payloads(payload):
        _, stdout, _ = _run_canonical(script, item)
        context = _context_from_output(stdout)
        if context:
            contexts.append(context)
    _emit_context(contexts)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("staleness", "layout"))
    args = parser.parse_args(argv)
    payload = _load_payload()
    if not payload:
        return 0
    if args.mode == "staleness":
        return _run_staleness(payload)
    return _run_layout(payload)


if __name__ == "__main__":
    raise SystemExit(main())
