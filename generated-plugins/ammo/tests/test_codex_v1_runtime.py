"""Exercise V1 ownership and hook ordering using native-shaped payloads."""

import json
import sqlite3
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_hook_semantics import (
    ROOT, _active_stage1_artifact, _audit_dispatch_message, _env, _gate,
    _hook_repo, _run_hook,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codex" / "scripts"))
import codex_agent_registry as registry
from resolve_codex_transcript import resolve_transcript


@pytest.fixture
def native(tmp_path, monkeypatch):
    repo = _hook_repo(tmp_path)
    artifact = _active_stage1_artifact(repo)
    root = str(uuid.uuid4())
    home = repo / "codex_home"
    home.mkdir()
    db = home / "state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, agent_path TEXT, created_at_ms INTEGER);
        CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT PRIMARY KEY, status TEXT);
        """)
        conn.execute("INSERT INTO threads VALUES (?, '', NULL, 1)", (root,))
    state = json.loads((artifact / "state.json").read_text())
    state["codex_thread_id"] = root
    state["campaign"]["rounds"][0]["parallel_tracks"]["tracks"] = {"OP-001": {"status": "IN_PROGRESS"}}
    (artifact / "state.json").write_text(json.dumps(state))
    env = _env(repo, artifact, AMMO_CODEX_AGENT_MODE="v1", CODEX_HOME=str(home), CODEX_SESSION_ID=root)
    monkeypatch.setenv("AMMO_SESSION_ID", "sess-test")

    def spawn(name="worker_r1_a1", role="ammo-investigator", parent=None, parent_path="/root"):
        child = str(uuid.uuid4())
        transcript = home / f"{child}.jsonl"
        transcript.write_text(json.dumps({"type": "session_meta", "payload": {"id": child}}) + "\n")
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO threads VALUES (?, ?, NULL, 2)", (child, str(transcript)))
            conn.execute("INSERT INTO thread_spawn_edges VALUES (?, ?, 'open')", (parent or root, child))
        metadata = {"task_name": name, "artifact_dir": str(artifact),
                    "recovery_note_path": str(artifact / "recovery" / root / parent_path.lstrip("/") / name / "context.md")}
        payload = {"cwd": str(repo), "session_id": root, "hook_event_name": "PostToolUse",
                   "tool_name": "spawn_agent", "tool_use_id": str(uuid.uuid4()),
                   "tool_input": {"agent_type": role, "fork_context": False,
                                  "message": "AMMO_TASK=" + json.dumps(metadata)},
                   "tool_response": json.dumps({"agent_id": child, "nickname": "Native name"})}
        if parent:
            payload.update(agent_id=parent, agent_type="ammo-investigator")
        return child, payload

    return repo, artifact, root, home, env, spawn


def test_spawn_replay_and_concurrent_delivery_keep_one_exact_binding(native):
    _, _, root, home, _, spawn = native
    child, payload = spawn()
    with ThreadPoolExecutor(max_workers=4) as pool:
        bindings = list(pool.map(lambda _: registry.register_spawn(home, payload), range(4)))
    assert all(record == bindings[0] for record in bindings)
    assert registry.binding_for(home, root, child)["task_path"] == "/root/worker_r1_a1"
    assert len(registry.list_bindings(home, root)) == 1
    _, duplicate = spawn()
    with pytest.raises(registry.AgentBindingError, match="already belongs"):
        registry.register_spawn(home, duplicate)


def test_nested_dispatch_uses_bound_parent_and_exact_uuid_transcript(native):
    _, _, root, home, _, spawn = native
    parent, payload = spawn()
    child, nested = spawn("delegate_r1", parent=parent, parent_path="/root/worker_r1_a1")
    with pytest.raises(registry.AgentBindingError, match="parent task is not registered"):
        registry.register_spawn(home, nested)
    registry.register_spawn(home, payload)
    binding = registry.register_spawn(home, nested)
    assert binding["task_path"] == "/root/worker_r1_a1/delegate_r1"
    result = resolve_transcript(home / "state_5.sqlite", root, agent_id=child)
    assert result["target_rollout_id"] == child
    assert result["collision_candidate_paths"] == [str(home / f"{child}.jsonl")]
    assert child != parent


@pytest.mark.parametrize("mismatch", ["root", "parent", "note", "artifact", "operation"])
def test_registration_rejects_conflicting_ownership(native, mismatch):
    _, artifact, root, home, _, spawn = native
    child, payload = spawn()
    if mismatch == "root":
        payload["session_id"] = str(uuid.uuid4())
    elif mismatch == "parent":
        parent, parent_payload = spawn("other_parent")
        registry.register_spawn(home, parent_payload)
        payload["agent_id"] = parent
    elif mismatch in ("note", "artifact"):
        metadata = registry.dispatch_metadata(payload)
        metadata["recovery_note_path" if mismatch == "note" else "artifact_dir"] = str(artifact.parent / "other")
        payload["tool_input"]["message"] = "AMMO_TASK=" + json.dumps(metadata)
    else:
        payload.pop("tool_use_id")
    with pytest.raises(registry.AgentBindingError):
        registry.register_spawn(home, payload)
    assert registry.binding_for(home, root, child) is None


def test_closed_and_reopened_native_thread_retains_same_binding(native):
    _, _, root, home, _, spawn = native
    child, payload = spawn()
    binding = registry.register_spawn(home, payload)
    for status in ("closed", "open"):
        with sqlite3.connect(home / "state_5.sqlite") as conn:
            conn.execute("UPDATE thread_spawn_edges SET status = ? WHERE child_thread_id = ?", (status, child))
        assert registry.list_bindings(home, root)[0]["native_status"] == status
        assert registry.binding_for(home, root, child) == binding


# `ammo:<role>` is the agent address on both harnesses (references/harness-binding.md).
@pytest.mark.parametrize("role", ["ammo-investigator", "ammo:ammo-investigator"])
def test_unbound_child_can_read_but_cannot_mutate_before_parent_completion(native, role):
    repo, _, root, home, env, spawn = native
    child, payload = spawn(role=role)
    child_payload = {"cwd": str(repo), "session_id": root, "agent_id": child,
                     "agent_type": role, "hook_event_name": "SubagentStart"}
    start = _run_hook("post_tool_use_guard.py", child_payload, env)
    assert "registration" in start.stdout.lower(), start.stderr
    pre = {**child_payload, "hook_event_name": "PreToolUse", "tool_name": "Bash",
           "tool_input": {"command": "cat skills/ammo/SKILL.md"}}
    assert '"decision": "block"' not in _run_hook("pre_tool_use_guard.py", pre, env).stdout
    pre["tool_input"] = {"command": "touch candidate.txt"}
    assert "registration is pending" in _run_hook("pre_tool_use_guard.py", pre, env).stdout
    post = _run_hook("post_tool_use_guard.py", payload, env)
    assert "registered" in post.stdout.lower(), post.stderr
    assert registry.binding_for(home, root, child)
    assert "registration is pending" not in _run_hook("pre_tool_use_guard.py", pre, env).stdout


def test_prefixed_spawn_role_is_validated_before_dispatch(native):
    _, _, _, _, env, spawn = native
    _, payload = spawn(role="ammo:ammo-investigator")
    payload["tool_input"]["message"] = "Investigate OP-001."  # no AMMO_TASK line
    pre = _run_hook("pre_tool_use_guard.py", dict(payload, hook_event_name="PreToolUse"), env)
    assert "AMMO V1 agent binding must be resolved" in pre.stdout, pre.stdout + pre.stderr


def test_child_first_implementer_and_monitor_pair_by_native_ids(native):
    repo, _, root, _, env, spawn = native
    implementer, payload = spawn("implementer_r1_op_001", "ammo-implementer")
    for child, role in [(implementer, "ammo-implementer")]:
        result = _run_hook("post_tool_use_guard.py", {"cwd": str(repo), "session_id": root,
            "hook_event_name": "SubagentStart", "agent_id": child, "agent_type": role}, env)
        assert '"decision": "block"' not in result.stdout, result.stderr
    post = _run_hook("post_tool_use_guard.py", payload, env)
    assert "AMMO MONITOR REMINDER" in post.stdout, post.stderr
    monitor, monitor_payload = spawn("monitor_r1_op_001", "ammo-transcript-monitor")
    pre = _run_hook("pre_tool_use_guard.py", dict(monitor_payload, hook_event_name="PreToolUse"), env)
    assert '"decision": "block"' not in pre.stdout, pre.stdout + pre.stderr
    result = _run_hook("post_tool_use_guard.py", monitor_payload, env)
    assert '"decision": "block"' not in result.stdout, result.stdout + result.stderr
    ledgers = list((repo / "gpu_res").glob("codex_monitor_pairs_*.json"))
    pairs = json.loads(ledgers[0].read_text())["pending"]
    pair = next(iter(pairs.values())) if isinstance(pairs, dict) else pairs[0]
    assert pair["implementer_agent_id"] == implementer
    assert pair["monitor_agent_id"] == monitor
    assert pair["status"] == "active"


def test_v1_audit_start_is_root_owned_and_replay_safe(native):
    _, artifact, _, _, env, spawn = native
    _, payload = spawn("audit_stage_2_r1_c2", "ammo-auditor")
    payload["tool_input"]["message"] += "\n" + _audit_dispatch_message(artifact)
    result = _run_hook("post_tool_use_guard.py", payload, env)
    assert "AMMO AUDIT STARTED" in result.stdout, result.stdout + result.stderr
    gate = _gate(artifact, "stage_2")
    _run_hook("post_tool_use_guard.py", payload, env)
    assert _gate(artifact, "stage_2") == gate
    assert "passed_at" not in gate


@pytest.mark.parametrize("mode", ["v1", "v2"])
@pytest.mark.parametrize("source", ["compact", "resume"])
def test_managed_recovery_injects_selected_execution_guide(native, mode, source):
    repo, _, root, _, env, _ = native
    env["AMMO_CODEX_AGENT_MODE"] = mode
    result = _run_hook("session_start.py", {"cwd": str(repo), "session_id": root, "source": source}, env)
    guide = (ROOT / f"references/codex-runtime-{mode}.md").read_text()
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert guide in context
