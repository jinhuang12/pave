"""Archived or nested copies of state.json must not count as a second campaign.

`.codex/hooks/common.py::active_artifact_dir` discovers the campaign by globbing
`kernel_opt_artifacts/**/state.json`. The artifact layout sanctions
`rounds/{N}/_archive/{slot}_{timestamp}/` as a quarantine slot, and on 2026-09-26
a Codex lead copied `state.json` into `rounds/1/_archive/scaffold_initial/`.
The copy carried the same identity, so the resolver raised "multiple campaigns
match" on every call. Because the PreToolUse, Stop, and PreCompact hooks share
the resolver, the session could neither run a command, finish a turn, nor
compact (session 858af077 on trn2-1, 16 blocks, 327 Stop re-prompts).

These tests pin the rule: only a `state.json` at the top of its own subtree,
outside `_archive/`, is a live campaign. Two real campaigns with one identity
must still raise, so the ambiguity guard keeps its purpose.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODEX_SRC = ROOT / "codex"
SERVER_SESSION = "srv-858af077"
CODEX_THREAD = "thr-01a0dd4c"


@pytest.fixture(scope="module")
def common():
    spec = importlib.util.spec_from_file_location(
        "codex_hook_common_under_test", CODEX_SRC / "hooks" / "common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state_json():
    return json.dumps(
        {
            "session_id": SERVER_SESSION,
            "codex_thread_id": CODEX_THREAD,
            "campaign": {
                "status": "active",
                "current_round": 1,
                "current_stage": "1_baseline",
                "rounds": [{"round_id": 1}],
            },
        }
    )


def _write_state(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(_state_json(), encoding="utf-8")
    return directory


@pytest.fixture
def repo(tmp_path, monkeypatch):
    repo = tmp_path / "worktree"
    repo.mkdir()
    monkeypatch.setenv("AMMO_SESSION_ID", SERVER_SESSION)
    monkeypatch.delenv("AMMO_ARTIFACT_DIR", raising=False)
    return repo


PAYLOAD = {"session_id": CODEX_THREAD}


@pytest.mark.unit
class TestActiveArtifactDir:
    def test_archived_copy_is_not_a_second_campaign(self, common, repo):
        live = _write_state(repo / "kernel_opt_artifacts" / "gpt-oss-20b_trn2_bf16_tp8")
        _write_state(live / "rounds" / "1" / "_archive" / "scaffold_initial")

        assert common.active_artifact_dir(repo, PAYLOAD) == live

    def test_nested_copy_below_a_campaign_root_is_ignored(self, common, repo):
        live = _write_state(repo / "kernel_opt_artifacts" / "target")
        _write_state(live / "recovery" / CODEX_THREAD / "snapshot")

        assert common.active_artifact_dir(repo, PAYLOAD) == live

    def test_archived_copy_alone_never_resolves(self, common, repo):
        # The live state is gone: the quarantine copy must not become the campaign,
        # or every hook would write into _archive/.
        target = repo / "kernel_opt_artifacts" / "target"
        _write_state(target / "rounds" / "1" / "_archive" / "scaffold_initial")

        assert common.active_artifact_dir(repo, PAYLOAD) is None

    def test_two_real_campaigns_with_one_identity_still_raise(self, common, repo):
        _write_state(repo / "kernel_opt_artifacts" / "a")
        _write_state(repo / "kernel_opt_artifacts" / "b")

        with pytest.raises(common.CampaignResolutionError, match="multiple campaigns"):
            common.active_artifact_dir(repo, PAYLOAD)

    def test_explicit_artifact_dir_still_wins(self, common, repo, monkeypatch):
        live = _write_state(repo / "kernel_opt_artifacts" / "target")
        _write_state(live / "rounds" / "1" / "_archive" / "scaffold_initial")
        monkeypatch.setenv("AMMO_ARTIFACT_DIR", str(live))

        assert common.active_artifact_dir(repo, PAYLOAD) == live


def _clean_env(**overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AMMO_") and key != "CODEX_SESSION_ID"
    }
    env.update(overrides)
    return env


@pytest.fixture
def hook_repo(tmp_path):
    """A worktree with the real hooks and classifier, so find_repo_root lands here."""
    repo = tmp_path / "worktree"
    for rel in (Path("hooks"), Path("skills") / "ammo" / "scripts"):
        shutil.copytree(
            CODEX_SRC / rel,
            repo / ".codex" / rel,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    return repo


def _run_pretool(repo, command):
    payload = {
        "cwd": str(repo),
        "session_id": CODEX_THREAD,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "hook_event_name": "PreToolUse",
    }
    return subprocess.run(
        [sys.executable, str(repo / ".codex" / "hooks" / "pre_tool_use_guard.py")],
        input=json.dumps(payload),
        env=_clean_env(AMMO_SESSION_ID=SERVER_SESSION),
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


# A mutating command, so the guard cannot take the read-only inspection path
# that already tolerates a resolution error.
MUTATING = "touch scratch.txt"


@pytest.mark.unit
class TestPreToolUseGuardEndToEnd:
    def test_guard_does_not_block_on_an_archived_copy(self, hook_repo):
        live = _write_state(hook_repo / "kernel_opt_artifacts" / "gpt-oss-20b_trn2_bf16_tp8")
        _write_state(live / "rounds" / "1" / "_archive" / "scaffold_initial")

        result = _run_pretool(hook_repo, MUTATING)

        assert "Traceback" not in result.stderr, result.stderr
        assert "multiple campaigns" not in result.stdout, result.stdout

    def test_guard_still_blocks_two_real_campaigns(self, hook_repo):
        # Negative control: proves the command above is not on the inspection
        # fast path, so the first test's pass is the resolver's doing.
        _write_state(hook_repo / "kernel_opt_artifacts" / "a")
        _write_state(hook_repo / "kernel_opt_artifacts" / "b")

        result = _run_pretool(hook_repo, MUTATING)

        assert "Traceback" not in result.stderr, result.stderr
        assert "multiple campaigns" in result.stdout, result.stdout
        assert '"permissionDecision": "deny"' in result.stdout or '"permissionDecision":"deny"' in result.stdout
