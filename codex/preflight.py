#!/usr/bin/env python3
"""Prove that the installed PAVE Init artifact can run nested Codex V1 agents."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from typing import Any

try:
    from codex import install_agents
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from codex import install_agents


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "codex" / "skills" / "pave-init" / "SKILL.md"
SOURCE_HASH = re.compile(r"source-sha256:\s*([0-9a-f]{64})")
PARENT_AGENT = "pave-init:pave-material-reviewer"
DELEGATE_AGENT = "pave-init:research-delegate"


class PreflightError(RuntimeError):
    pass


def generated_source_hash(path: Path = SKILL_PATH) -> str:
    match = SOURCE_HASH.search(path.read_text(encoding="utf-8")[:4096])
    if not match:
        raise PreflightError(f"generated source hash missing from {path}")
    return match.group(1)


def load_rollout(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise PreflightError(f"malformed rollout {path}:{line_number}") from error
            if not isinstance(record, dict):
                raise PreflightError(f"invalid rollout record {path}:{line_number}")
            records.append(record)
    except OSError as error:
        raise PreflightError(f"cannot read rollout {path}: {error}") from error
    if not records:
        raise PreflightError(f"empty rollout {path}")
    return records


def find_rollout(codex_home: Path, thread_id: str) -> Path:
    matches = list((codex_home / "sessions").rglob(f"*{thread_id}.jsonl"))
    if len(matches) != 1:
        raise PreflightError(
            f"expected one rollout for {thread_id}, found {len(matches)}"
        )
    return matches[0]


def session_meta(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            return record["payload"]
    raise PreflightError("rollout has no session metadata")


def task_completed(records: list[dict[str, Any]], label: str) -> None:
    terminal_events = [
        record["payload"]
        for record in records
        if record.get("type") == "event_msg"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "task_complete"
    ]
    if len(terminal_events) != 1:
        raise PreflightError(f"{label} task did not complete")
    if terminal_events[0].get("error") is not None:
        raise PreflightError(f"{label} task completed with an error")


def agent_messages(records: list[dict[str, Any]]) -> str:
    messages: list[str] = []
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        if payload.get("type") == "agent_message":
            value = payload.get("message", payload.get("text", ""))
            if isinstance(value, str):
                messages.append(value)
            continue
        if payload.get("type") != "message" or payload.get("role") != "assistant":
            continue
        content = payload.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "output_text":
                continue
            text = item.get("text")
            if isinstance(text, str):
                messages.append(text)
    return "\n".join(messages)


def parse_json_value(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise PreflightError(f"invalid JSON in {label}") from error
    if not isinstance(value, dict):
        raise PreflightError(f"invalid object in {label}")
    return value


def spawn_link(
    records: list[dict[str, Any]], expected_agent: str, label: str
) -> tuple[str, str]:
    calls: list[tuple[str, dict[str, Any]]] = []
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        if payload.get("type") == "function_call" and payload.get("name") == "spawn_agent":
            arguments = parse_json_value(payload.get("arguments"), f"{label} spawn arguments")
            if arguments.get("agent_type") == expected_agent:
                call_id = payload.get("call_id")
                if isinstance(call_id, str):
                    calls.append((call_id, arguments))
    if len(calls) != 1:
        raise PreflightError(f"expected one {label} spawn for {expected_agent}")
    call_id, arguments = calls[0]
    outputs = []
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        if payload.get("type") == "function_call_output" and payload.get("call_id") == call_id:
            outputs.append(parse_json_value(payload.get("output"), f"{label} spawn output"))
    if len(outputs) != 1 or not isinstance(outputs[0].get("agent_id"), str):
        raise PreflightError(f"{label} spawn has no linked agent id")
    message = arguments.get("message", "")
    if not isinstance(message, str):
        raise PreflightError(f"{label} spawn message is invalid")
    return outputs[0]["agent_id"], message


def verify_child_meta(
    records: list[dict[str, Any]], thread_id: str, parent_id: str, depth: int, role: str
) -> None:
    meta = session_meta(records)
    if meta.get("id") != thread_id or meta.get("parent_thread_id") != parent_id:
        raise PreflightError(f"{role} parent link is invalid")
    spawn = meta.get("source", {}).get("subagent", {}).get("thread_spawn", {})
    if (
        spawn.get("parent_thread_id") != parent_id
        or spawn.get("depth") != depth
        or spawn.get("agent_role") != role
    ):
        raise PreflightError(f"{role} spawn metadata is invalid")


def verify_rollout_chain(
    codex_home: Path, root_thread_id: str, expected_source_hash: str, nonce: str
) -> dict[str, str]:
    root_records = load_rollout(find_rollout(codex_home, root_thread_id))
    task_completed(root_records, "root")
    parent_id, parent_brief = spawn_link(root_records, PARENT_AGENT, "parent")
    if f"source-sha256: {expected_source_hash}" not in parent_brief:
        raise PreflightError("parent brief has the wrong generated source hash")
    if nonce not in parent_brief:
        raise PreflightError("parent brief has no preflight nonce")

    parent_records = load_rollout(find_rollout(codex_home, parent_id))
    verify_child_meta(parent_records, parent_id, root_thread_id, 1, PARENT_AGENT)
    task_completed(parent_records, "parent")
    delegate_id, delegate_brief = spawn_link(
        parent_records, DELEGATE_AGENT, "research delegate"
    )
    if nonce not in delegate_brief:
        raise PreflightError("research delegate brief has no preflight nonce")

    delegate_records = load_rollout(find_rollout(codex_home, delegate_id))
    verify_child_meta(delegate_records, delegate_id, parent_id, 2, DELEGATE_AGENT)
    task_completed(delegate_records, "research delegate")
    if nonce not in agent_messages(delegate_records):
        raise PreflightError("research delegate did not return the preflight nonce")
    if nonce not in agent_messages(root_records):
        raise PreflightError("root did not return the preflight nonce")
    return {"parent_thread_id": parent_id, "delegate_thread_id": delegate_id}


def prompt(nonce: str) -> str:
    return f"""Use $pave-init:pave-init only for its Codex V1 nested-agent release preflight.
Read the generated source-sha256 marker from the loaded PAVE Init skill. You may
use a read-only shell command to read that installed skill file. Spawn the
named {PARENT_AGENT} agent. Include that marker and nonce {nonce} in its brief.
Require it to spawn the named {DELEGATE_AGENT} agent at depth 2 and ask that
delegate to return the nonce. Return the same nonce only after both named spawns
complete. Do not start a campaign or modify files."""


def parse_root_thread(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "thread.started" and isinstance(record.get("thread_id"), str):
            return record["thread_id"]
    raise PreflightError("codex exec did not report a root thread id")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Trusted git project used for the probe")
    parser.add_argument("--codex", default="codex", help="Codex executable")
    parser.add_argument("--evidence-dir", required=True, help="Directory for JSONL evidence")
    parser.add_argument(
        "--release",
        action="store_true",
        help="force Codex V1 and agents.max_depth=2 for artifact release proof",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    evidence = Path(args.evidence_dir).expanduser().resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    stdout_path = evidence / "codex-preflight.jsonl"
    stderr_path = evidence / "codex-preflight.stderr"
    try:
        project_agents = project / ".codex" / "agents"
        user_agents = codex_home / "agents"
        if not install_agents.is_current(project_agents) and not install_agents.is_current(
            user_agents
        ):
            raise PreflightError("project or user custom agents are not current")
        source_hash = generated_source_hash()
        nonce = secrets.token_hex(16)
        command = [
            args.codex,
            "exec",
            "--json",
            "--sandbox",
            "read-only",
            "--dangerously-bypass-hook-trust",
            "-C",
            str(project),
        ]
        if args.release:
            command.extend(
                ["-c", "features.multi_agent_v2=false", "-c", "agents.max_depth=2"]
            )
        command.append(prompt(nonce))
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        root_id = parse_root_thread(result.stdout)
        if result.returncode != 0:
            raise PreflightError(f"codex exec failed with exit {result.returncode}")
        chain = verify_rollout_chain(codex_home, root_id, source_hash, nonce)
        print(json.dumps({"root_thread_id": root_id, **chain}, indent=2))
        return 0
    except (OSError, PreflightError) as error:
        print(f"PREFLIGHT FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
