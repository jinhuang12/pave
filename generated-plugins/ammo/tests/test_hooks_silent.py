"""Outside a campaign every hooks.json command exits 0 with empty stdout (conventions §7, §9).

Each registered command runs from a temp dir with no .ammo/ and a clean
environment (no AMMO_* names), once per tool its matcher names. WorktreeCreate
cannot decline, so it runs in a temp git repo and must print one path to a
dir that exists (a plain git worktree).

Linux only: the hook harnesses use flock and GNU stat. Run it on g6e-cdd.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="hooks are judged on Linux (flock, GNU stat); run this test on g6e-cdd",
)

R = Path(__file__).resolve().parents[1]
HOOKS_JSON = R / "hooks" / "hooks.json"

TOOL_INPUTS = {
    "Bash": [
        {"command": "echo hello", "description": "x"},
        {"command": "pip install requests", "description": "x"},
        {"command": "python3 -m vllm.entrypoints.openai.api_server --model m", "description": "x"},
    ],
    "Write": [
        {"file_path": "{cwd}/notes.md", "content": "x"},
        {"file_path": "{cwd}/rounds/1/audits/stage_1.md", "content": "# verdict\n"},
        {"file_path": "{cwd}/state.json", "content": "{}"},
    ],
    "Edit": [{"file_path": "{cwd}/notes.md", "old_string": "a", "new_string": "b"}],
    "MultiEdit": [{"file_path": "{cwd}/notes.md", "edits": [{"old_string": "a", "new_string": "b"}]}],
    "NotebookEdit": [{"notebook_path": "{cwd}/n.ipynb", "new_source": "x"}],
    "Agent": [
        {"subagent_type": "ammo:ammo-champion", "prompt": "x", "description": "x"},
        {"subagent_type": "ammo:ammo-delegate", "name": "helper", "prompt": "x", "description": "x"},
    ],
    "SendMessage": [{"to": "team-lead", "message": "hello"}],
    "TaskCreate": [{"subject": "x", "description": "x"}],
    "TaskUpdate": [{"taskId": "1", "status": "completed"}],
    "TaskStop": [{"task_id": "1"}],
    "EnterWorktree": [{"name": "silent-wt"}],
}


def _cases():
    if not HOOKS_JSON.is_file():
        return [pytest.param(None, None, None, None, id="hooks.json-missing")]
    cases = []
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for event, groups in data["hooks"].items():
        for group in groups:
            matcher = group.get("matcher") or ""
            for hook in group["hooks"]:
                script = hook["command"].rsplit("/", 1)[-1].strip('"')
                if event in ("PreToolUse", "PostToolUse"):
                    for tool in matcher.split("|"):
                        for n, tool_input in enumerate(TOOL_INPUTS.get(tool, [{}])):
                            cases.append(pytest.param(event, matcher, hook, (tool, tool_input), id=f"{event}-{script}-{tool}-{n}"))
                else:
                    cases.append(pytest.param(event, matcher, hook, None, id=f"{event}-{script}"))
    return cases


def _fill(value, cwd: Path):
    if isinstance(value, str):
        return value.replace("{cwd}", str(cwd))
    if isinstance(value, list):
        return [_fill(v, cwd) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, cwd) for k, v in value.items()}
    return value


def _payload(event: str, matcher: str, tool, cwd: Path) -> dict:
    payload = {
        "session_id": f"silent-{uuid.uuid4().hex[:12]}",
        "transcript_path": str(cwd / "transcript.jsonl"),
        "cwd": str(cwd),
        "hook_event_name": event,
        "permission_mode": "default",
    }
    if tool is not None:
        payload["tool_name"], payload["tool_input"] = tool[0], _fill(tool[1], cwd)
        if event == "PostToolUse":
            payload["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
    elif event == "SessionStart":
        payload["source"] = "compact"
    elif event == "PreCompact":
        payload.update(trigger="auto", custom_instructions="")
    elif event in ("Stop", "SubagentStop"):
        payload["stop_hook_active"] = False
        if event == "SubagentStop":
            payload.update(agent_id="a1", agent_type="ammo:ammo-auditor" if matcher else "general-purpose")
    elif event == "WorktreeCreate":
        payload["name"] = "silent-wt"
    elif event == "WorktreeRemove":
        payload["worktree_path"] = str(cwd / ".claude" / "worktrees" / "silent-wt")
    return payload


def _run(command: str, payload: dict, cwd: Path, home: Path, timeout: int):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_PLUGIN_ROOT": str(R),
        "CLAUDE_PROJECT_DIR": str(cwd),
    }
    return subprocess.run(
        ["/bin/sh", "-c", command], cwd=cwd, env=env, input=json.dumps(payload),
        capture_output=True, text=True, timeout=timeout + 5,
    )


@pytest.mark.parametrize("event,matcher,hook,tool", _cases())
def test_hook_silent_outside_campaign(tmp_path, event, matcher, hook, tool):
    assert hook is not None, f"{HOOKS_JSON} is missing"
    cwd, home = tmp_path / "work", tmp_path / "home"
    cwd.mkdir()
    home.mkdir()
    if event == "WorktreeCreate":
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=cwd, check=True,
        )
    proc = _run(hook["command"], _payload(event, matcher, tool, cwd), cwd, home, int(hook.get("timeout") or 60))
    assert not (cwd / ".ammo").exists() and not (tmp_path / ".ammo").exists()
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr: {proc.stderr[-800:]}"
    if event == "WorktreeCreate":
        lines = proc.stdout.strip().splitlines()
        assert len(lines) == 1 and Path(lines[0]).is_dir(), f"want one worktree path, got {proc.stdout!r}"
    else:
        assert proc.stdout.strip() == "", f"stdout not empty: {proc.stdout[:400]!r}"
