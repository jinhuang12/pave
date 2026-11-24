#!/usr/bin/env python3
"""Adapt Antigravity CLI lifecycle hook payloads to the canonical orchestrate hooks.

Three wire-level differences between Antigravity and Claude/Codex require an adapter:

1. Antigravity sends camelCase protojson payloads (`conversationId`, `hookEventName`,
   `cwd`, `workspacePaths`, `transcriptPath`, `toolCall.name`, `toolCall.args`).
2. Antigravity's `PostToolUse` expects `{}` on stdout and does not inject context.
   Context injection uses `injectSteps` on `SessionStart` / `PostInvocation`.
   This adapter runs the canonical `PostToolUse` hooks (`speedup_nudge.sh`,
   `maintainer_nudge.sh`), spools any emitted `additionalContext` per conversation,
   and drains it atomically on `PostInvocation`.
3. Antigravity's common hook payload does not guarantee an `agent_type` field for
   swarm workers. For `maintainer_nudge`, the adapter checks explicit agent metadata
   first and otherwise inspects only the first non-empty line of the conversation's
   initial `USER_INPUT` in `transcriptPath` for `SUBTASK worker-` (stripping optional
   `<USER_REQUEST>` tags and leading `@<name>` swarm mentions) while excluding any
   conversation that owns a coordinator run marker.

All hooks are observing controls. Any adapter error fails open: exits 0, prints `{}`,
and writes nothing to stderr.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any

SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SUBTASK_LINE = re.compile(r"^SUBTASK\s+worker-[A-Za-z0-9._-]+")
MENTION_PREFIX = re.compile(r"^@[a-z0-9][a-z0-9_-]*[:,\s]*(.*)$")
EDIT_TOOL_MAP = {
    "write_file": "Write",
    "write_to_file": "Write",
    "edit_file": "Edit",
    "replace_file_content": "Edit",
}
WORKER_AGENT_TYPES = {"orchestrate-worker", "pave-init:orchestrate-worker"}


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    # <plugin_root>/hooks/ag_hook_adapter.py
    return Path(__file__).resolve().parents[1]


def _canonical_hook(name: str) -> Path:
    return _plugin_root() / "skills" / "orchestrate" / "hooks" / name


def _pending_dir() -> Path:
    configured = os.environ.get("ORCHESTRATE_AG_PENDING_DIR", "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return Path(tempfile.gettempdir()) / "orchestrate-ag-pending"


def _role_cache_dir() -> Path:
    configured = os.environ.get("ORCHESTRATE_AG_ROLE_DIR", "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return Path(tempfile.gettempdir()) / "orchestrate-ag-roles"


def _marker_dir() -> Path:
    configured = os.environ.get("ORCHESTRATE_MARKER_DIR", "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return Path.home() / ".orchestrate"


def _load_payload(stream: Any = sys.stdin) -> dict[str, Any]:
    try:
        data = json.load(stream)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_cwd(payload: dict[str, Any]) -> str:
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    workspaces = payload.get("workspacePaths")
    if isinstance(workspaces, list):
        for entry in workspaces:
            if isinstance(entry, str) and entry.strip():
                return entry.strip()
    return ""


def _extract_conversation_id(payload: dict[str, Any]) -> str:
    raw = payload.get("conversationId") or payload.get("session_id") or ""
    if not isinstance(raw, str):
        return ""
    value = raw.strip()
    return value if SAFE_ID.fullmatch(value) else ""


def _is_lead_session(conv_id: str, cwd: str) -> bool:
    candidates = [_marker_dir() / f"active-{conv_id}"]
    if cwd:
        candidates.append(Path(cwd) / ".orchestrate" / f"active-{conv_id}")
    return any(path.is_file() for path in candidates)


def _first_user_brief_line(transcript_path: str) -> str:
    path = Path(transcript_path)
    if not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for idx, raw_line in enumerate(handle):
                if idx >= 20:
                    break
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    record = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "USER_INPUT":
                    continue
                content = record.get("content")
                if not isinstance(content, str):
                    return ""
                text = content.strip()
                if text.startswith("<USER_REQUEST>"):
                    text = text[len("<USER_REQUEST>") :].strip()
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if not lines:
                    return ""
                first = lines[0]
                mention = MENTION_PREFIX.match(first)
                if mention:
                    remainder = mention.group(1).strip()
                    if remainder:
                        return remainder
                    return lines[1] if len(lines) > 1 else ""
                return first
    except OSError:
        return ""
    return ""


def _is_orchestrate_worker(payload: dict[str, Any], conv_id: str, cwd: str) -> bool:
    if not conv_id or _is_lead_session(conv_id, cwd):
        return False

    for key in ("agentType", "subagentType", "agentName", "agent_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip() in WORKER_AGENT_TYPES

    cache_file = _role_cache_dir() / conv_id
    try:
        if cache_file.is_file():
            cached = cache_file.read_text(encoding="utf-8").strip()
            if cached in ("worker", "other"):
                return cached == "worker"
    except OSError:
        pass

    transcript = payload.get("transcriptPath")
    if not isinstance(transcript, str) or not transcript.strip():
        return False

    first_line = _first_user_brief_line(transcript.strip())
    is_worker = bool(SUBTASK_LINE.match(first_line))
    if first_line:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("worker\n" if is_worker else "other\n", encoding="utf-8")
        except OSError:
            pass
    return is_worker


def _run_canonical_hook(
    hook_name: str,
    canonical_payload: dict[str, Any],
    args: tuple[str, ...] = (),
) -> str:
    script = _canonical_hook(hook_name)
    if not script.is_file():
        return ""
    env = dict(os.environ)
    env.setdefault("ORCHESTRATE_PYTHON", sys.executable)
    result = subprocess.run(
        ["bash", str(script), *args],
        input=json.dumps(canonical_payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_plugin_root()),
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    try:
        parsed = json.loads(result.stdout)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    specific = parsed.get("hookSpecificOutput")
    if not isinstance(specific, dict):
        return ""
    text = specific.get("additionalContext")
    return text if isinstance(text, str) and text.strip() else ""


def _spool_message(conv_id: str, text: str) -> None:
    if not conv_id or not text:
        return
    directory = _pending_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{conv_id}.jsonl"
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")


def _drain_messages(conv_id: str) -> list[str]:
    if not conv_id:
        return []
    directory = _pending_dir()
    target = directory / f"{conv_id}.jsonl"
    if not target.is_file():
        return []
    claimed = directory / f".{conv_id}.{os.getpid()}.{time.time_ns()}.claimed"
    try:
        os.replace(target, claimed)
    except FileNotFoundError:
        return []
    messages: list[str] = []
    try:
        for line in claimed.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"].strip():
                messages.append(item["text"])
    finally:
        try:
            claimed.unlink()
        except OSError:
            pass
    return messages


def _inject_response(messages: list[str]) -> dict[str, Any]:
    if not messages:
        return {}
    return {
        "injectSteps": [
            {"systemMessage": {"systemMessage": message}} for message in messages
        ]
    }


def handle_session_start(payload: dict[str, Any]) -> dict[str, Any]:
    conv_id = _extract_conversation_id(payload)
    if not conv_id:
        return {}
    canonical = {
        "session_id": conv_id,
        "hook_event_name": "SessionStart",
        "source": "resume",
        "cwd": _extract_cwd(payload),
    }
    text = _run_canonical_hook("goal_restate.sh", canonical, ("antigravity",))
    return _inject_response([text] if text else [])


def handle_post_tool_speedup(payload: dict[str, Any]) -> dict[str, Any]:
    conv_id = _extract_conversation_id(payload)
    if not conv_id:
        return {}
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {}
    if tool_call.get("name") != "run_command":
        return {}
    args = tool_call.get("args")
    command = args.get("CommandLine") if isinstance(args, dict) else None
    if not isinstance(command, str) or not command.strip():
        return {}
    canonical = {
        "session_id": conv_id,
        "hook_event_name": "PostToolUse",
        "cwd": _extract_cwd(payload),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    text = _run_canonical_hook("speedup_nudge.sh", canonical, ("antigravity",))
    if text:
        _spool_message(conv_id, text)
    return {}


def handle_post_tool_maintainer(payload: dict[str, Any]) -> dict[str, Any]:
    conv_id = _extract_conversation_id(payload)
    if not conv_id:
        return {}
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {}
    canonical_tool = EDIT_TOOL_MAP.get(str(tool_call.get("name") or ""))
    if not canonical_tool:
        return {}
    cwd = _extract_cwd(payload)
    if not _is_orchestrate_worker(payload, conv_id, cwd):
        return {}
    canonical = {
        "session_id": conv_id,
        "hook_event_name": "PostToolUse",
        "cwd": cwd,
        "agent_type": "pave-init:orchestrate-worker",
        "agent_id": "",
        "tool_name": canonical_tool,
    }
    text = _run_canonical_hook("maintainer_nudge.sh", canonical)
    if text:
        _spool_message(conv_id, text)
    return {}


def handle_post_invocation(payload: dict[str, Any]) -> dict[str, Any]:
    conv_id = _extract_conversation_id(payload)
    if not conv_id:
        return {}
    return _inject_response(_drain_messages(conv_id))


HANDLERS = {
    "session_start": handle_session_start,
    "post_tool_speedup": handle_post_tool_speedup,
    "post_tool_maintainer": handle_post_tool_maintainer,
    "post_invocation": handle_post_invocation,
}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1 or args[0] not in HANDLERS:
            print("{}")
            return 0
        payload = _load_payload()
        response = HANDLERS[args[0]](payload)
        print(json.dumps(response, ensure_ascii=False))
    except Exception:  # noqa: BLE001 — observing hooks always fail open
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
