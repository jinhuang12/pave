#!/usr/bin/env python3
"""Bind AMMO logical task paths to exact native Codex V1 thread identities."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path


class AgentBindingError(ValueError):
    pass


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def _uuid(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise AgentBindingError("A native Codex thread UUID is required.") from None


def root_thread_id(payload: dict) -> str:
    return _uuid(payload.get("session_id") or payload.get("sessionId") or os.getenv("CODEX_SESSION_ID"))


def caller_thread_id(payload: dict) -> str:
    return _uuid(payload.get("agent_id") or payload.get("agentId") or root_thread_id(payload))


def tool_input(payload: dict) -> dict:
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    return value if isinstance(value, dict) else {}


def dispatch_metadata(payload: dict) -> dict | None:
    inputs = tool_input(payload)
    message = str(inputs.get("message") or "")
    for item in inputs.get("items") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            message += "\n" + str(item.get("text") or "")
    lines = re.findall(r"(?m)^\s*AMMO_TASK\s*=\s*(\{[^\n]*\})\s*$", message)
    if not lines:
        return None
    if len(lines) != 1:
        raise AgentBindingError("Each AMMO spawn requires exactly one AMMO_TASK metadata line.")
    try:
        metadata = json.loads(lines[0])
    except ValueError:
        raise AgentBindingError("AMMO_TASK must contain valid JSON.") from None
    if not isinstance(metadata, dict) or not re.fullmatch(r"[a-z0-9_]+", str(metadata.get("task_name") or "")):
        raise AgentBindingError("AMMO_TASK.task_name must be one lowercase task-path segment.")
    return metadata


def spawned_thread_id(payload: dict) -> str | None:
    result = payload.get("tool_response", payload.get("toolResponse"))
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict) or not result.get("agent_id"):
        return None
    return _uuid(result["agent_id"])


@contextmanager
def _database(path: Path):
    path = path.expanduser().resolve()
    if not path.is_file():
        raise AgentBindingError("Codex's native thread index is missing; agent ownership cannot be verified.")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        yield connection
    except sqlite3.Error:
        raise AgentBindingError("Codex's native thread index cannot verify this agent binding.") from None
    finally:
        connection.close()


def verify_lineage(db: Path, root: str, child: str, *, parent: str | None = None) -> None:
    """Require native ancestry, never a matching name or a recent transcript."""
    root, child = _uuid(root), _uuid(child)
    if parent is not None:
        parent = _uuid(parent)
    with _database(db) as connection:
        if not connection.execute("SELECT 1 FROM threads WHERE id = ?", (root,)).fetchone():
            raise AgentBindingError("The campaign root is absent from Codex's thread index.")
        current = child
        visited = set()
        while current != root:
            if current in visited or len(visited) >= 32:
                raise AgentBindingError("Codex agent ancestry is cyclic or exceeds the supported depth.")
            visited.add(current)
            row = connection.execute(
                "SELECT parent_thread_id FROM thread_spawn_edges WHERE child_thread_id = ?", (current,)
            ).fetchone()
            if not row or not row[0]:
                raise AgentBindingError("The agent has no verified native parent edge to this campaign.")
            if current == child and parent is not None and row[0] != parent:
                raise AgentBindingError("The spawn result does not belong to the calling parent.")
            current = str(row[0])
        if parent is not None and child == root:
            raise AgentBindingError("A campaign root cannot be registered as its own child.")


def _read_registry(home: Path) -> dict:
    path = home / "ammo-agent-registry.json"
    if not path.exists():
        return {"version": 1, "bindings": {}}
    try:
        data = json.loads(path.read_text())
        if data.get("version") != 1 or not isinstance(data.get("bindings"), dict):
            raise ValueError
        return data
    except (OSError, ValueError, AttributeError):
        raise AgentBindingError("The AMMO agent registry cannot be read. Restore its exact saved binding.") from None


def binding_for(home: Path, root: str, agent: str, *, verify: bool = True) -> dict | None:
    root, agent = _uuid(root), _uuid(agent)
    binding = _read_registry(home)["bindings"].get(agent)
    if binding is None:
        return None
    if (binding.get("root_thread_id") != root
            or binding.get("server_session_id", "") != os.getenv("AMMO_SESSION_ID", "")):
        raise AgentBindingError("The agent registry entry belongs to another session.")
    if verify:
        verify_lineage(home / "state_5.sqlite", root, agent, parent=binding.get("parent_thread_id"))
    return binding


def validate_dispatch(home: Path, payload: dict) -> dict:
    metadata = dispatch_metadata(payload)
    if metadata is None:
        raise AgentBindingError("V1 AMMO spawns require an AMMO_TASK JSON line; read references/codex-runtime.md.")
    root = root_thread_id(payload)
    parent = caller_thread_id(payload)
    verify_lineage(home / "state_5.sqlite", root, parent)
    parent_binding = binding_for(home, root, parent) if parent != root else None
    if parent != root and parent_binding is None:
        raise AgentBindingError("The parent task is not registered yet. Wait before dispatching a child.")
    parent_path = parent_binding["task_path"] if parent_binding else "/root"
    task_path = parent_path + "/" + metadata["task_name"]
    artifact = Path(str(metadata.get("artifact_dir") or "")).expanduser()
    if not artifact.is_absolute():
        raise AgentBindingError("AMMO_TASK.artifact_dir must be an absolute campaign path.")
    artifact = artifact.resolve()
    try:
        state = json.loads((artifact / "state.json").read_text())
    except (OSError, ValueError):
        raise AgentBindingError("The dispatch campaign state cannot be read.") from None
    if state.get("codex_thread_id") != root:
        raise AgentBindingError("The dispatch campaign does not belong to this Codex root.")
    server = os.getenv("AMMO_SESSION_ID", "")
    if server and state.get("session_id") != server:
        raise AgentBindingError("The dispatch campaign does not belong to this AMMO session.")
    expected_note = artifact / "recovery" / root / task_path.lstrip("/") / "context.md"
    supplied_note = Path(str(metadata.get("recovery_note_path") or ""))
    if not supplied_note.is_absolute() or supplied_note.resolve() != expected_note:
        raise AgentBindingError(f"Use the exact assigned recovery note: {expected_note}")
    return {
        "server_session_id": server, "root_thread_id": root, "parent_thread_id": parent,
        "task_name": metadata["task_name"], "task_path": task_path,
        "role": str(tool_input(payload).get("agent_type") or ""),
        "artifact_dir": str(artifact), "recovery_note_path": str(expected_note),
        "tool_use_id": str(payload.get("tool_use_id") or payload.get("toolUseId") or ""),
    }


def register_spawn(home: Path, payload: dict) -> dict | None:
    agent = spawned_thread_id(payload)
    if agent is None:
        return None  # Failed spawns have no identity to register.
    binding = validate_dispatch(home, payload)
    if not binding["tool_use_id"]:
        raise AgentBindingError("The native spawn completion has no tool-use ID.")
    verify_lineage(home / "state_5.sqlite", binding["root_thread_id"], agent, parent=binding["parent_thread_id"])
    binding["agent_id"] = agent
    home.mkdir(parents=True, exist_ok=True)
    with (home / "ammo-agent-registry.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _read_registry(home)
        existing = data["bindings"].get(agent)
        if existing is not None and existing != binding:
            raise AgentBindingError("This native agent already has a different AMMO binding.")
        for other_id, other in data["bindings"].items():
            if other_id == agent or other.get("root_thread_id") != binding["root_thread_id"]:
                continue
            if (other.get("task_path") == binding["task_path"] or
                    (other.get("parent_thread_id") == binding["parent_thread_id"]
                     and other.get("tool_use_id") == binding["tool_use_id"])):
                raise AgentBindingError("This task name or spawn operation already belongs to another native agent.")
        data["bindings"][agent] = binding
        path = home / "ammo-agent-registry.json"
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
        os.replace(temp, path)
    return binding


def list_bindings(home: Path, root: str) -> list[dict]:
    results = []
    for agent, record in _read_registry(home)["bindings"].items():
        if record.get("root_thread_id") != root:
            continue
        binding = dict(binding_for(home, root, agent))
        with _database(home / "state_5.sqlite") as connection:
            row = connection.execute("SELECT status FROM thread_spawn_edges WHERE child_thread_id = ?", (agent,)).fetchone()
        binding["native_status"] = row[0] if row else "unknown"
        results.append(binding)
    return sorted(results, key=lambda item: item["task_path"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list"])
    parser.add_argument("--root-thread-id", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(list_bindings(codex_home(), _uuid(args.root_thread_id)), indent=2))
    except AgentBindingError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
