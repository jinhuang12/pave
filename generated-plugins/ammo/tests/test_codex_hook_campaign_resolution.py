"""The Codex hooks find the campaign through the resolution layer.

`codex/hooks/common.py::active_artifact_dir` resolves AMMO_ARTIFACT_DIR, else
the `<engine>/.ammo/active` marker that `scripts/campaign_paths.py` walks up to.
The marker names one campaign dir, so a copy of state.json elsewhere can never
count as a second campaign.

Background: the artifact layout sanctions `rounds/{N}/_archive/{slot}_{timestamp}/`
as a quarantine slot, and on 2026-09-26 a Codex lead copied `state.json` into
`rounds/1/_archive/scaffold_initial/`. The old resolver globbed every state.json
under the campaign root, saw the copy as a second campaign with the same
identity, and raised "multiple campaigns match" on every call. Because the
PreToolUse, Stop, and PreCompact hooks share the resolver, the session could
neither run a command, finish a turn, nor compact (session 858af077 on trn2-1,
16 blocks, 327 Stop re-prompts).

These tests pin: archived and nested copies never resolve, identity still
binds, an explicit binding still wins and still refuses a conflict, two engines
on one host resolve their own campaigns, and outside a campaign every Codex
hook exits 0 with empty stdout (conventions section 7).
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "codex" / "hooks"
CAMPAIGN_ID = "20261028-120000-abc123"
CODEX_THREAD = "thr-01a0dd4c"
PAYLOAD = {"session_id": CODEX_THREAD}


@pytest.fixture(scope="module")
def common():
    spec = importlib.util.spec_from_file_location(
        "codex_hook_common_under_test", HOOKS / "common.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state_json(session_id=CAMPAIGN_ID, thread=CODEX_THREAD):
    return json.dumps(
        {
            "session_id": session_id,
            "codex_thread_id": thread,
            "campaign": {
                "status": "active",
                "current_round": 1,
                "current_stage": "1_baseline",
                "rounds": [{"round_id": 1}],
            },
        }
    )


def _write_state(directory, **identity):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state.json").write_text(_state_json(**identity), encoding="utf-8")
    return directory


def _campaign(engine, cid=CAMPAIGN_ID, state=True, **identity):
    """<engine>/.ammo/<cid>/ plus the marker that names it."""
    campaign = engine / ".ammo" / cid
    campaign.mkdir(parents=True, exist_ok=True)
    (engine / ".ammo" / "active").write_text(cid + "\n", encoding="utf-8")
    if state:
        _write_state(campaign, session_id=cid, **identity)
    return campaign


@pytest.fixture
def engine(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    engine.mkdir()
    monkeypatch.delenv("AMMO_ARTIFACT_DIR", raising=False)
    monkeypatch.setenv("AMMO_SESSION_ID", CAMPAIGN_ID)
    return engine


@pytest.mark.unit
class TestActiveArtifactDir:
    def test_archived_copy_is_not_a_second_campaign(self, common, engine):
        live = _campaign(engine)
        _write_state(live / "rounds" / "1" / "_archive" / "scaffold_initial")

        assert common.active_artifact_dir(engine, PAYLOAD) == live

    def test_nested_copy_below_a_campaign_root_is_ignored(self, common, engine):
        live = _campaign(engine)
        _write_state(live / "recovery" / CODEX_THREAD / "snapshot")

        assert common.active_artifact_dir(engine, PAYLOAD) == live

    def test_archived_copy_alone_never_resolves(self, common, engine):
        # The live state is gone: the quarantine copy must not become the
        # campaign, or every hook would write into _archive/.
        campaign = _campaign(engine, state=False)
        _write_state(campaign / "rounds" / "1" / "_archive" / "scaffold_initial")

        assert common.active_artifact_dir(engine, PAYLOAD) is None

    def test_marker_resolves_from_a_dir_below_the_engine(self, common, engine):
        live = _campaign(engine)
        worktree = engine / ".codex" / "worktrees" / "op001"
        worktree.mkdir(parents=True)

        assert common.active_artifact_dir(worktree, PAYLOAD) == live

    def test_marker_campaign_with_another_identity_does_not_bind(self, common, engine, monkeypatch):
        _campaign(engine)
        monkeypatch.setenv("AMMO_SESSION_ID", "some-other-campaign")
        assert common.active_artifact_dir(engine, PAYLOAD) is None

        monkeypatch.setenv("AMMO_SESSION_ID", CAMPAIGN_ID)
        assert common.active_artifact_dir(engine, {"session_id": "another-thread"}) is None

    def test_marker_id_is_the_identity_when_the_env_has_none(self, common, engine, monkeypatch):
        # conventions section 3: AMMO_SESSION_ID readers fall back to the marker id.
        live = _campaign(engine, thread=None)
        monkeypatch.delenv("AMMO_SESSION_ID")

        assert common.active_artifact_dir(engine, PAYLOAD) == live

    def test_explicit_artifact_dir_still_wins(self, common, engine, monkeypatch):
        _campaign(engine)
        other = _write_state(engine / ".ammo" / "other", session_id=CAMPAIGN_ID)
        monkeypatch.setenv("AMMO_ARTIFACT_DIR", str(other))

        assert common.active_artifact_dir(engine, PAYLOAD) == other

    def test_explicit_artifact_dir_with_another_identity_raises(self, common, engine, monkeypatch):
        other = _write_state(engine / ".ammo" / "other", session_id="someone-else")
        monkeypatch.setenv("AMMO_ARTIFACT_DIR", str(other))

        with pytest.raises(common.CampaignResolutionError, match="conflicts"):
            common.active_artifact_dir(engine, PAYLOAD)

    def test_two_engines_on_one_host_resolve_their_own_campaigns(self, common, tmp_path, monkeypatch):
        monkeypatch.delenv("AMMO_ARTIFACT_DIR", raising=False)
        monkeypatch.delenv("AMMO_SESSION_ID", raising=False)
        first = _campaign(tmp_path / "engine-a", cid="20261028-000000-aaaaaa", thread=None)
        second = _campaign(tmp_path / "engine-b", cid="20261028-000000-bbbbbb", thread=None)

        assert common.active_artifact_dir(tmp_path / "engine-a", PAYLOAD) == first
        assert common.active_artifact_dir(tmp_path / "engine-b", PAYLOAD) == second

    def test_no_marker_and_no_env_is_no_campaign(self, common, tmp_path, monkeypatch):
        monkeypatch.delenv("AMMO_ARTIFACT_DIR", raising=False)
        assert common.active_artifact_dir(tmp_path, PAYLOAD) is None


@pytest.mark.unit
class TestFindRepoRoot:
    def test_engine_root_and_campaign_env_come_from_the_marker(self, common, tmp_path):
        engine = tmp_path / "engine"
        _campaign(engine)
        deep = engine / "src" / "pumice"
        deep.mkdir(parents=True)
        with mock.patch.dict(os.environ):
            for name in ("AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "AMMO_GPU_RES_DIR",
                         "AMMO_RUNTIME_PROFILE", "AMMO_ENGINE_ROOT"):
                os.environ.pop(name, None)
            assert common.find_repo_root(str(deep)) == engine.resolve()
            assert os.environ["AMMO_SESSION_ID"] == CAMPAIGN_ID
            assert os.environ["AMMO_GPU_RES_DIR"] == "/tmp/ammo_gpu_res"
            assert os.environ["AMMO_RUNTIME_PROFILE"] == "pumice-cuda"
            assert os.environ["AMMO_ENGINE_ROOT"] == str(engine.resolve())
            # The marker stays a non-explicit binding: no AMMO_ARTIFACT_DIR fill.
            assert "AMMO_ARTIFACT_DIR" not in os.environ

    def test_explicit_env_wins_over_the_marker_fill(self, common, tmp_path):
        engine = tmp_path / "engine"
        _campaign(engine)
        with mock.patch.dict(os.environ, {"AMMO_SESSION_ID": "explicit", "AMMO_GPU_RES_DIR": "/tmp/x"}):
            os.environ.pop("AMMO_ARTIFACT_DIR", None)
            common.find_repo_root(str(engine))
            assert os.environ["AMMO_SESSION_ID"] == "explicit"
            assert os.environ["AMMO_GPU_RES_DIR"] == "/tmp/x"

    def test_outside_a_campaign_nothing_is_filled(self, common, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        with mock.patch.dict(os.environ):
            for name in ("AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID"):
                os.environ.pop(name, None)
            common.find_repo_root(str(plain))
            assert "AMMO_SESSION_ID" not in os.environ


def _clean_env(tmp_path, **overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AMMO_", "CODEX_"))
    }
    env["AMMO_GPU_RES_DIR"] = str(tmp_path / "gpu_res")
    env.update(overrides)
    return env


def _run(script, payload, cwd, env):
    return subprocess.run(
        [sys.executable, str(HOOKS / script)],
        input=json.dumps(payload),
        env=env,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _pretool(cwd, command):
    return {
        "cwd": str(cwd),
        "session_id": CODEX_THREAD,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "hook_event_name": "PreToolUse",
    }


# A mutating command, so the guard cannot take the read-only inspection path
# that already tolerates a resolution error.
MUTATING = "touch scratch.txt"


@pytest.mark.unit
class TestPreToolUseGuardEndToEnd:
    def test_guard_does_not_block_on_an_archived_copy(self, tmp_path):
        engine = tmp_path / "engine"
        live = _campaign(engine)
        _write_state(live / "rounds" / "1" / "_archive" / "scaffold_initial")

        result = _run("pre_tool_use_guard.py", _pretool(engine, MUTATING), engine, _clean_env(tmp_path))

        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr, result.stderr
        assert '"deny"' not in result.stdout, result.stdout

    def test_guard_still_blocks_a_conflicting_binding(self, tmp_path):
        # Negative control: proves the command above is not on the inspection
        # fast path, so the first test's pass is the resolver's doing.
        engine = tmp_path / "engine"
        _campaign(engine)
        other = _write_state(engine / ".ammo" / "other", session_id="someone-else")
        env = _clean_env(tmp_path, AMMO_ARTIFACT_DIR=str(other), AMMO_SESSION_ID=CAMPAIGN_ID)

        result = _run("pre_tool_use_guard.py", _pretool(engine, MUTATING), engine, env)

        assert "Traceback" not in result.stderr, result.stderr
        assert "conflicts" in result.stdout, result.stdout
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


OUTSIDE_PAYLOADS = [
    ("session_start.py", {"hook_event_name": "SessionStart", "source": "resume"}),
    ("pre_compact.py", {"hook_event_name": "PreCompact", "trigger": "auto"}),
    ("pre_tool_use_guard.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": MUTATING}}),
    ("pre_tool_use_guard.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": "pip install foo"}}),
    ("pre_tool_use_guard.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                               "tool_input": {"command": "nsys profile python bench.py"}}),
    ("post_tool_use_guard.py", {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                "tool_input": {"command": "echo hi > state.json"}}),
    ("post_tool_use_guard.py", {"hook_event_name": "SubagentStart", "agent_id": "child",
                                "tool_input": {"agent_type": "ammo-implementer"}}),
    ("stop_gate_guard.py", {"hook_event_name": "Stop"}),
]


@pytest.mark.unit
@pytest.mark.parametrize("script,payload", OUTSIDE_PAYLOADS, ids=lambda v: v if isinstance(v, str) else "")
def test_every_codex_hook_is_silent_outside_a_campaign(tmp_path, script, payload):
    plain = tmp_path / "not-a-campaign"
    plain.mkdir()
    payload = dict(payload, cwd=str(plain), session_id=CODEX_THREAD)

    result = _run(script, payload, plain, _clean_env(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert not (tmp_path / "gpu_res").exists(), "a hook wrote host state outside a campaign"
