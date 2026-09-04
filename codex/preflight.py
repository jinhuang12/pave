#!/usr/bin/env python3
"""Prove that the installed PAVE Init artifact can run nested Codex V2 agents."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tomllib
from typing import Any

try:
    from codex import install_agents
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from codex import install_agents


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "codex" / "skills" / "pave-init" / "SKILL.md"
SOURCE_HASH = re.compile(r"source-sha256:\s*([0-9a-f]{64})")
LOCAL_TASK_NAME = re.compile(r"[a-z0-9_]+")
ROOT_TASK_PATH = "/root"
PARENT_AGENT = "pave-init:pave-material-reviewer"
DELEGATE_AGENT = "pave-init:research-delegate"
ROLE_FILES = {
    PARENT_AGENT: ROOT / "codex" / "agents" / "pave_init_material_reviewer.toml",
    DELEGATE_AGENT: ROOT / "codex" / "agents" / "pave_init_research_delegate.toml",
}
SELECTED_SKILL_KIND = "skills.selected_skill_instructions"
DEVELOPER_INSTRUCTIONS_KIND = "generic.developer_instructions"


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
    except (OSError, UnicodeError) as error:
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
    matches = [
        record["payload"]
        for record in records
        if record.get("type") == "session_meta"
        and isinstance(record.get("payload"), dict)
    ]
    if len(matches) != 1:
        raise PreflightError(f"rollout needs one session metadata record, found {len(matches)}")
    return matches[0]


def turn_contexts(records: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    contexts = [
        record["payload"]
        for record in records
        if record.get("type") == "turn_context"
        and isinstance(record.get("payload"), dict)
    ]
    turn_ids = [context.get("turn_id") for context in contexts]
    if any(not isinstance(turn_id, str) or not turn_id for turn_id in turn_ids):
        raise PreflightError(f"{label} has an invalid turn context")
    if len(set(turn_ids)) != len(turn_ids):
        raise PreflightError(f"{label} has duplicate turn contexts")
    return contexts


def completed_turns(
    records: list[dict[str, Any]], label: str, expected_turns: int
) -> list[dict[str, Any]]:
    contexts = turn_contexts(records, label)
    terminal_events = [
        record["payload"]
        for record in records
        if record.get("type") == "event_msg"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "task_complete"
    ]
    if len(contexts) != expected_turns or len(terminal_events) != expected_turns:
        raise PreflightError(
            f"{label} needs {expected_turns} completed turn(s), "
            f"found {len(contexts)} context(s) and {len(terminal_events)} completion(s)"
        )
    context_ids = {context["turn_id"] for context in contexts}
    completion_ids = {event.get("turn_id") for event in terminal_events}
    if len(completion_ids) != expected_turns or completion_ids != context_ids:
        raise PreflightError(f"{label} completion links are invalid")
    if any(event.get("error") is not None for event in terminal_events):
        raise PreflightError(f"{label} task completed with an error")
    return terminal_events


def require_nonce(
    terminal_events: list[dict[str, Any]], nonce: str, label: str
) -> None:
    for event in terminal_events:
        message = event.get("last_agent_message")
        if not isinstance(message, str) or message.strip() != nonce:
            raise PreflightError(f"{label} did not return its preflight nonce")


def parse_json_value(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise PreflightError(f"invalid JSON in {label}") from error
    if not isinstance(value, dict):
        raise PreflightError(f"invalid object in {label}")
    return value


def message_items(
    records: list[dict[str, Any]], role: str, content_kind: str
) -> list[str]:
    matches: list[str] = []
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, dict)
            or payload.get("type") != "message"
            or payload.get("role") != role
        ):
            continue
        content = payload.get("content")
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        kinds = metadata.get("content_item_kinds") if isinstance(metadata, dict) else None
        if not isinstance(content, list) or not isinstance(kinds, list):
            continue
        if len(content) != len(kinds):
            if content_kind in kinds:
                raise PreflightError(f"malformed persisted {content_kind} record")
            continue
        for item, kind in zip(content, kinds, strict=True):
            if kind != content_kind:
                continue
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise PreflightError(f"malformed persisted {content_kind} item")
            matches.append(item["text"])
    return matches


def verify_selected_skill(records: list[dict[str, Any]], expected_source_hash: str) -> None:
    skill_records = [
        text
        for text in message_items(records, "user", SELECTED_SKILL_KIND)
        if "<name>pave-init:pave-init</name>" in text
    ]
    if len(skill_records) != 1:
        raise PreflightError(
            f"expected one injected PAVE Init skill record, found {len(skill_records)}"
        )
    hashes = SOURCE_HASH.findall(skill_records[0])
    if hashes != [expected_source_hash]:
        raise PreflightError("injected PAVE Init skill has the wrong generated source hash")


def spawn_task_path(
    records: list[dict[str, Any]],
    expected_agent: str,
    local_task_name: str,
    parent_task_path: str,
    label: str,
) -> str:
    if not LOCAL_TASK_NAME.fullmatch(local_task_name):
        raise PreflightError(f"invalid local task name for {label}")
    calls: list[str] = []
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, dict)
            or payload.get("type") != "function_call"
            or payload.get("name") != "spawn_agent"
        ):
            continue
        arguments = parse_json_value(payload.get("arguments"), f"{label} spawn arguments")
        if (
            arguments.get("agent_type") == expected_agent
            and arguments.get("task_name") == local_task_name
            and arguments.get("fork_turns") == "none"
        ):
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                calls.append(call_id)
    if len(calls) != 1:
        raise PreflightError(f"expected one strict V2 {label} spawn for {expected_agent}")

    outputs: list[dict[str, Any]] = []
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") == "response_item"
            and isinstance(payload, dict)
            and payload.get("type") == "function_call_output"
            and payload.get("call_id") == calls[0]
        ):
            outputs.append(parse_json_value(payload.get("output"), f"{label} spawn output"))
    if len(outputs) != 1:
        raise PreflightError(f"{label} spawn needs one V2 task-path output")
    if "agent_id" in outputs[0]:
        raise PreflightError(f"{label} spawn returned a V1 agent id")
    task_path = outputs[0].get("task_name")
    expected_path = f"{parent_task_path}/{local_task_name}"
    if task_path != expected_path:
        raise PreflightError(
            f"{label} spawn returned invalid task path {task_path!r}; expected {expected_path!r}"
        )
    return expected_path


def forbid_spawn(records: list[dict[str, Any]], agent_type: str, label: str) -> None:
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, dict)
            or payload.get("type") != "function_call"
            or payload.get("name") != "spawn_agent"
        ):
            continue
        arguments = parse_json_value(payload.get("arguments"), f"{label} spawn arguments")
        if arguments.get("agent_type") == agent_type:
            raise PreflightError(f"{label} must not spawn {agent_type}")


def verify_followup(records: list[dict[str, Any]], reviewer_path: str) -> None:
    calls: list[str] = []
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, dict)
            or payload.get("type") != "function_call"
            or payload.get("name") != "followup_task"
        ):
            continue
        arguments = parse_json_value(payload.get("arguments"), "reviewer follow-up arguments")
        if arguments.get("target") == reviewer_path:
            call_id = payload.get("call_id")
            if isinstance(call_id, str):
                calls.append(call_id)
    if len(calls) != 1:
        raise PreflightError("expected one reviewer followup_task on the canonical task path")
    outputs = [
        record
        for record in records
        if record.get("type") == "response_item"
        and isinstance(record.get("payload"), dict)
        and record["payload"].get("type") == "function_call_output"
        and record["payload"].get("call_id") == calls[0]
    ]
    if len(outputs) != 1:
        raise PreflightError("reviewer followup_task has no unique result")


def resolve_child_rollout(
    search_dir: Path,
    root_session_id: str,
    parent_thread_id: str,
    agent_path: str,
    role: str,
    depth: int,
) -> tuple[Path, list[dict[str, Any]], str]:
    tokens = [value.encode() for value in (parent_thread_id, agent_path, role)]
    matches: list[tuple[Path, list[dict[str, Any]], str]] = []
    try:
        paths = list(search_dir.rglob("*.jsonl"))
    except OSError as error:
        raise PreflightError(f"cannot scan rollout directory {search_dir}: {error}") from error
    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise PreflightError(f"cannot read rollout candidate {path}: {error}") from error
        if not all(token in raw for token in tokens):
            continue
        records = load_rollout(path)
        meta = session_meta(records)
        source = meta.get("source")
        subagent = source.get("subagent") if isinstance(source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if not isinstance(spawn, dict):
            continue
        child_id = meta.get("id")
        if (
            isinstance(child_id, str)
            and meta.get("session_id") == root_session_id
            and meta.get("parent_thread_id") == parent_thread_id
            and meta.get("agent_path") == agent_path
            and meta.get("agent_role") == role
            and meta.get("multi_agent_version") == "v2"
            and spawn.get("parent_thread_id") == parent_thread_id
            and spawn.get("agent_path") == agent_path
            and spawn.get("agent_role") == role
            and spawn.get("depth") == depth
        ):
            matches.append((path, records, child_id))
    if len(matches) != 1:
        raise PreflightError(
            f"expected one V2 rollout for {agent_path}, found {len(matches)}"
        )
    return matches[0]


def load_role_config(role: str) -> dict[str, Any]:
    path = ROLE_FILES[role]
    try:
        with path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PreflightError(f"cannot load generated role {path}: {error}") from error
    return config


def verify_role_runtime(
    records: list[dict[str, Any]], role: str, expected_turns: int
) -> None:
    config = load_role_config(role)
    persisted = message_items(records, "developer", DEVELOPER_INSTRUCTIONS_KIND)
    if persisted != [config.get("developer_instructions")]:
        raise PreflightError(f"{role} persisted developer instructions are stale")
    contexts = turn_contexts(records, role)
    if len(contexts) != expected_turns:
        raise PreflightError(f"{role} has the wrong persisted turn count")
    for context in contexts:
        sandbox = context.get("sandbox_policy")
        observed_sandbox = sandbox.get("type") if isinstance(sandbox, dict) else None
        if context.get("multi_agent_version") != "v2":
            raise PreflightError(f"{role} persisted a non-V2 turn")
        if context.get("model") != config.get("model"):
            raise PreflightError(f"{role} persisted the wrong model")
        if context.get("effort") != config.get("model_reasoning_effort"):
            raise PreflightError(f"{role} persisted the wrong reasoning effort")
        if observed_sandbox != config.get("sandbox_mode"):
            raise PreflightError(f"{role} persisted the wrong sandbox")


def _pave_agent_files(target: Path) -> set[Path]:
    expected_names = set(install_agents.EXPECTED_AGENTS)
    expected_roles = set(install_agents.EXPECTED_AGENTS.values())
    matches: set[Path] = set()
    for path in target.glob("*.toml"):
        if path.name in expected_names or path.name.startswith("pave_init_"):
            matches.add(path)
            continue
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        if data.get("name") in expected_roles:
            matches.add(path)
    return matches


def _scope_has_pave_footprint(target: Path) -> bool:
    return (target / install_agents.MANIFEST_NAME).exists() or bool(_pave_agent_files(target))


def require_current_agents(project_agents: Path, user_agents: Path) -> str:
    expected_names = set(install_agents.EXPECTED_AGENTS)
    if _scope_has_pave_footprint(project_agents):
        project_files = _pave_agent_files(project_agents)
        if (
            {path.name for path in project_files} != expected_names
            or not install_agents.is_current(project_agents)
        ):
            raise PreflightError("project PAVE agents shadow the user scope but are not current")
        return "project"
    user_files = _pave_agent_files(user_agents)
    if (
        {path.name for path in user_files} != expected_names
        or not install_agents.is_current(user_agents)
    ):
        raise PreflightError("user PAVE custom agents are not current")
    return "user"


def verify_rollout_chain(
    codex_home: Path,
    root_thread_id: str,
    expected_source_hash: str,
    root_nonce: str,
    reviewer_nonce: str,
    delegate_nonce: str,
    reviewer_task_name: str,
    delegate_task_name: str,
) -> dict[str, str | int]:
    if len({root_nonce, reviewer_nonce, delegate_nonce}) != 3:
        raise PreflightError("preflight nonces must be distinct")
    root_path = find_rollout(codex_home, root_thread_id)
    root_records = load_rollout(root_path)
    root_meta = session_meta(root_records)
    if (
        root_meta.get("id") != root_thread_id
        or root_meta.get("session_id") != root_thread_id
        or root_meta.get("parent_thread_id") is not None
        or root_meta.get("multi_agent_version") != "v2"
    ):
        raise PreflightError("root V2 session metadata is invalid")
    verify_selected_skill(root_records, expected_source_hash)
    root_turns = completed_turns(root_records, "root", 1)
    require_nonce(root_turns, root_nonce, "root")
    forbid_spawn(root_records, DELEGATE_AGENT, "root")

    reviewer_path = spawn_task_path(
        root_records,
        PARENT_AGENT,
        reviewer_task_name,
        ROOT_TASK_PATH,
        "reviewer",
    )
    verify_followup(root_records, reviewer_path)
    _, reviewer_records, reviewer_id = resolve_child_rollout(
        root_path.parent,
        root_thread_id,
        root_thread_id,
        reviewer_path,
        PARENT_AGENT,
        1,
    )
    verify_role_runtime(reviewer_records, PARENT_AGENT, 2)
    reviewer_turns = completed_turns(reviewer_records, "reviewer", 2)
    require_nonce(reviewer_turns, reviewer_nonce, "reviewer")

    delegate_path = spawn_task_path(
        reviewer_records,
        DELEGATE_AGENT,
        delegate_task_name,
        reviewer_path,
        "research delegate",
    )
    _, delegate_records, delegate_id = resolve_child_rollout(
        root_path.parent,
        root_thread_id,
        reviewer_id,
        delegate_path,
        DELEGATE_AGENT,
        2,
    )
    verify_role_runtime(delegate_records, DELEGATE_AGENT, 1)
    delegate_turns = completed_turns(delegate_records, "research delegate", 1)
    require_nonce(delegate_turns, delegate_nonce, "research delegate")

    return {
        "parent_thread_id": reviewer_id,
        "delegate_thread_id": delegate_id,
        "multi_agent_version": "v2",
        "reviewer_turns": 2,
    }


def prompt(
    root_nonce: str,
    reviewer_nonce: str,
    delegate_nonce: str,
    reviewer_task_name: str,
    delegate_task_name: str,
) -> str:
    return f"""Use $pave-init:pave-init only as the selected skill for this Codex V2 release proof.
Do not start its campaign, run its own preflight, or modify files. The root must
spawn exactly one {PARENT_AGENT} with task_name {reviewer_task_name} and
fork_turns \"none\". The reviewer, not the root, must spawn exactly one
{DELEGATE_AGENT} with task_name {delegate_task_name} and fork_turns \"none\".
The delegate must return exactly {delegate_nonce}. After the delegate completes,
the reviewer's first turn must return exactly {reviewer_nonce}. Wait for that
turn, then continue the same reviewer through followup_task on its canonical
task path and require its second turn to return exactly {reviewer_nonce}. Return
exactly {root_nonce} only after both reviewer turns and the nested delegate
complete. Do not spawn the delegate from the root."""


def parse_root_thread(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "thread.started" and isinstance(record.get("thread_id"), str):
            return record["thread_id"]
    raise PreflightError("codex exec did not report a root thread id")


def exact_project_trust_override(project: Path) -> str:
    quoted_path = json.dumps(str(project), ensure_ascii=False)
    return f"projects={{{quoted_path}={{trust_level=\"trusted\"}}}}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Trusted git project used for the probe")
    parser.add_argument("--codex", default="codex", help="Codex executable")
    parser.add_argument("--evidence-dir", required=True, help="Directory for JSONL evidence")
    parser.add_argument(
        "--release",
        action="store_true",
        help="force Codex V2, 16 child slots, and exact process-local project trust",
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
        agent_scope = require_current_agents(
            project / ".codex" / "agents", codex_home / "agents"
        )
        source_hash = generated_source_hash()
        root_nonce = secrets.token_hex(16)
        reviewer_nonce = secrets.token_hex(16)
        delegate_nonce = secrets.token_hex(16)
        task_suffix = secrets.token_hex(6)
        reviewer_task_name = f"pave_v2_preflight_reviewer_{task_suffix}"
        delegate_task_name = f"pave_v2_preflight_delegate_{task_suffix}"
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
                [
                    "-c",
                    "features.multi_agent=true",
                    "-c",
                    "features.multi_agent_v2.enabled=true",
                    "-c",
                    "agents.enabled=true",
                    "-c",
                    "agents.max_concurrent_threads_per_session=16",
                    "-c",
                    exact_project_trust_override(project),
                ]
            )
        command.append(
            prompt(
                root_nonce,
                reviewer_nonce,
                delegate_nonce,
                reviewer_task_name,
                delegate_task_name,
            )
        )
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        root_id = parse_root_thread(result.stdout)
        if result.returncode != 0:
            raise PreflightError(f"codex exec failed with exit {result.returncode}")
        chain = verify_rollout_chain(
            codex_home,
            root_id,
            source_hash,
            root_nonce,
            reviewer_nonce,
            delegate_nonce,
            reviewer_task_name,
            delegate_task_name,
        )
        print(
            json.dumps(
                {"root_thread_id": root_id, "agent_scope": agent_scope, **chain},
                indent=2,
            )
        )
        return 0
    except (OSError, PreflightError) as error:
        print(f"PREFLIGHT FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
