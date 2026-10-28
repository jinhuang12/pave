#!/usr/bin/env python3
"""Host-local campaign scaffold: new_target.py creates the campaign workspace.

conventions §3: a campaign lives at <engine>/.ammo/<campaign-id>/. The marker
<engine>/.ammo/active holds the campaign id, and .ammo/ is listed once in the
engine's .git/info/exclude. target.json records the engine contract that
scripts/campaign_paths.py reads back (references/campaign-env.md).

Every scaffold here runs from a plain git checkout with no AMMO_* environment,
the way a user runs it inside a Pumice checkout.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
_NEW_TARGET = _SCRIPTS / "new_target.py"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import campaign_paths  # noqa: E402

_spec = importlib.util.spec_from_file_location("new_target_campaign", str(_NEW_TARGET))
new_target = importlib.util.module_from_spec(_spec)
sys.modules["new_target_campaign"] = new_target
_spec.loader.exec_module(new_target)

_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_BASE_ARGS = ["--model-id", "test-model", "--hardware", "L40S", "--dtype", "bf16",
              "--gpu-model", "NVIDIA L40S"]


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def _plain_env(**extra: str) -> dict:
    """The caller's environment with every AMMO_* name and GPU binding removed."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AMMO_") and k not in ("CUDA_VISIBLE_DEVICES",)}
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env.update(extra)
    return env


def _scaffold(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_NEW_TARGET), *_BASE_ARGS, *args],
        cwd=str(cwd), capture_output=True, text=True,
        env=_plain_env() if env is None else env,
    )


def _marker_id(engine: Path) -> str:
    lines = (engine / ".ammo" / "active").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
    return lines[0]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# workspace, marker, exclude
# ---------------------------------------------------------------------------


def test_scaffold_mints_campaign_id_and_writes_workspace(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    result = _scaffold(engine)
    assert result.returncode == 0, result.stderr

    cid = _marker_id(engine)
    assert _ID_RE.match(cid), cid
    campaign = engine / ".ammo" / cid
    assert (campaign / "target.json").is_file()
    assert (campaign / "state.json").is_file()
    assert (campaign / "rounds" / "1").is_dir()
    assert _read(campaign / "state.json")["session_id"] == cid


def test_scaffold_from_a_subdirectory_uses_the_checkout_root(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    sub = engine / "src" / "pumice"
    sub.mkdir(parents=True)
    result = _scaffold(sub)
    assert result.returncode == 0, result.stderr
    assert (engine / ".ammo" / _marker_id(engine) / "state.json").is_file()
    assert not (sub / ".ammo").exists()


def test_exclude_lists_ammo_once_after_two_runs(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    first = _scaffold(engine)
    assert first.returncode == 0, first.stderr
    first_id = _marker_id(engine)
    second = _scaffold(engine)
    assert second.returncode == 0, second.stderr
    second_id = _marker_id(engine)

    assert first_id != second_id
    assert (engine / ".ammo" / first_id / "state.json").is_file()
    exclude = (engine / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".ammo/") == 1, exclude
    status = subprocess.run(["git", "-C", str(engine), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    assert ".ammo" not in status


def test_campaign_id_flag_names_the_workspace(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    result = _scaffold(engine, "--campaign-id", "gptoss-l40s-1")
    assert result.returncode == 0, result.stderr
    assert _marker_id(engine) == "gptoss-l40s-1"
    state = _read(engine / ".ammo" / "gptoss-l40s-1" / "state.json")
    assert state["session_id"] == "gptoss-l40s-1"


@pytest.mark.parametrize("bad", ["../escape", "a/b", ".", "-lead"])
def test_campaign_id_flag_rejects_a_path(tmp_path, bad):
    engine = _git_repo(tmp_path / "pumice")
    result = _scaffold(engine, f"--campaign-id={bad}")
    assert result.returncode != 0
    assert "--campaign-id" in result.stderr
    assert not (engine / ".ammo").exists()


def test_existing_campaign_id_is_not_overwritten(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    assert _scaffold(engine, "--campaign-id", "c1").returncode == 0
    state_path = engine / ".ammo" / "c1" / "state.json"
    before = state_path.read_text(encoding="utf-8")
    again = _scaffold(engine, "--campaign-id", "c1")
    assert again.returncode != 0
    assert "--force" in again.stderr
    assert state_path.read_text(encoding="utf-8") == before


def test_engine_root_flag_works_outside_a_git_checkout(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _scaffold(elsewhere, "--engine-root", str(engine))
    assert result.returncode == 0, result.stderr
    cid = _marker_id(engine)
    assert _read(engine / ".ammo" / cid / "target.json")["engine"]["root"] == str(engine)
    assert not (elsewhere / ".ammo").exists()


def test_no_engine_root_outside_git_fails_closed(tmp_path):
    elsewhere = tmp_path / "not-a-checkout"
    elsewhere.mkdir()
    result = _scaffold(elsewhere)
    assert result.returncode != 0
    assert "--engine-root" in result.stderr
    assert not (elsewhere / ".ammo").exists()


def test_artifact_dir_under_ammo_writes_the_marker(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    campaign = engine / ".ammo" / "explicit-1"
    result = _scaffold(tmp_path, "--artifact-dir", str(campaign))
    assert result.returncode == 0, result.stderr
    assert _marker_id(engine) == "explicit-1"
    assert _read(campaign / "target.json")["engine"]["root"] == str(engine)
    assert _read(campaign / "state.json")["session_id"] == "explicit-1"


def test_artifact_dir_outside_ammo_writes_no_marker(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    artifact = tmp_path / "scratch" / "art"
    result = _scaffold(engine, "--artifact-dir", str(artifact))
    assert result.returncode == 0, result.stderr
    assert (artifact / "state.json").is_file()
    assert not (engine / ".ammo").exists()
    assert not (tmp_path / "scratch" / ".ammo").exists()
    assert _read(artifact / "target.json")["engine"]["root"] == str(engine)


def test_engine_root_must_hold_a_workspace_artifact_dir(tmp_path):
    """One engine per campaign: the marker and engine.root name the same checkout."""
    engine = _git_repo(tmp_path / "pumice")
    other = _git_repo(tmp_path / "other")
    result = _scaffold(engine, "--artifact-dir", str(other / ".ammo" / "c1"),
                       "--engine-root", str(engine))
    assert result.returncode != 0
    assert "--engine-root" in result.stderr
    assert not (engine / ".ammo").exists()
    assert not (other / ".ammo").exists()


def test_campaign_id_flag_must_match_artifact_dir(tmp_path):
    engine = _git_repo(tmp_path / "pumice")
    result = _scaffold(engine, "--artifact-dir", str(engine / ".ammo" / "a"),
                       "--campaign-id", "b")
    assert result.returncode != 0
    assert "--campaign-id" in result.stderr
    assert not (engine / ".ammo").exists()


# ---------------------------------------------------------------------------
# target.json engine contract (references/campaign-env.md section 1)
# ---------------------------------------------------------------------------


def _scaffold_campaign(tmp_path, *args, env=None):
    engine = _git_repo(tmp_path / "pumice")
    result = _scaffold(engine, *args, env=env)
    assert result.returncode == 0, result.stderr
    campaign = engine / ".ammo" / _marker_id(engine)
    return engine, campaign, _read(campaign / "target.json"), _read(campaign / "state.json")


def test_target_json_records_the_engine_contract(tmp_path):
    engine, _, target, state = _scaffold_campaign(tmp_path, "--tp", "2",
                                                  "--data-parallel-size", "2")
    assert target["engine"] == {"root": str(engine), "venv": str(engine / ".venv")}
    runtime = target["runtime"]
    assert runtime["profile"] == "pumice-cuda"
    assert runtime["accelerator_kind"] == "cuda"
    assert runtime["accelerator_name"] == "NVIDIA L40S"
    assert runtime["instance_family"] == "NVIDIA L40S"
    assert target["target"]["tp"] == 2
    assert target["target"]["dp"] == 2
    assert "serving_manifest" not in target
    assert state["runtime"]["runtime_profile"] == "pumice-cuda"
    assert state["runtime"]["accelerator_kind"] == "cuda"
    assert state["runtime"]["instance_family"] == "NVIDIA L40S"


def test_instance_family_env_wins_over_the_gpu_name(tmp_path):
    _, _, target, state = _scaffold_campaign(
        tmp_path, env=_plain_env(AMMO_INSTANCE_FAMILY="g6e"))
    assert target["runtime"]["instance_family"] == "g6e"
    assert target["runtime"]["accelerator_name"] == "NVIDIA L40S"
    assert state["runtime"]["instance_family"] == "g6e"


def test_engine_venv_comes_from_ammo_python_when_set(tmp_path):
    python = tmp_path / "venvs" / "pumice" / "bin" / "python"
    _, _, target, _ = _scaffold_campaign(tmp_path, env=_plain_env(AMMO_PYTHON=str(python)))
    assert target["engine"]["venv"] == str(python)


def test_empty_ammo_python_counts_as_unset(tmp_path):
    engine, _, target, _ = _scaffold_campaign(tmp_path, env=_plain_env(AMMO_PYTHON=""))
    assert target["engine"]["venv"] == str(engine / ".venv")


def test_pumice_launch_args_carry_no_distributed_executor_backend(tmp_path):
    _, _, target, _ = _scaffold_campaign(tmp_path, "--tp", "1", "--data-parallel-size", "2",
                                         "--enable-expert-parallel")
    bench = target["bench"]
    assert bench["runner"] == "pumice_grpc_bench"
    assert bench["extra_args"] == ["--data-parallel-size", "2", "--enable-expert-parallel"]
    every_arg = bench["extra_args"] + bench["baseline_extra_args"] + bench["opt_extra_args"]
    assert "--distributed-executor-backend" not in every_arg
    assert "external_launcher" not in json.dumps(target)


def test_campaign_layer_reads_the_scaffold(tmp_path):
    """The seam: what new_target.py writes is what campaign_paths.py derives."""
    engine, campaign, _, _ = _scaffold_campaign(tmp_path, "--tp", "2",
                                                "--data-parallel-size", "2")
    worktree = engine / ".claude" / "worktrees" / "op001"
    worktree.mkdir(parents=True)
    assert campaign_paths.find_campaign_dir(worktree, env={}) == campaign
    env = campaign_paths.campaign_env(campaign)
    assert env["AMMO_SESSION_ID"] == campaign.name
    assert env["AMMO_ENGINE_ROOT"] == str(engine)
    assert env["AMMO_PYTHON"] == str(engine / ".venv" / "bin" / "python")
    assert env["AMMO_RUNTIME_PROFILE"] == "pumice-cuda"
    assert env["AMMO_ACCELERATOR_KIND"] == "cuda"
    assert env["AMMO_TP_SIZE"] == "2"
    assert env["AMMO_DP_SIZE"] == "2"
    assert env["AMMO_ACCELERATOR_NAME"] == "NVIDIA L40S"
    assert env["AMMO_INSTANCE_FAMILY"] == "NVIDIA L40S"
    assert "AMMO_SERVING_SETUP" not in env


def test_serving_manifest_fills_path_and_sha256(tmp_path, spec):
    from serving_contract import ServingSpec

    data = spec.model_dump(mode="json")
    data["runtime_profile"] = "pumice-cuda"
    pumice_spec = ServingSpec.model_validate(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    engine = _git_repo(tmp_path / "pumice")
    result = subprocess.run(
        [sys.executable, str(_NEW_TARGET), "--serving-manifest", str(manifest),
         "--hardware", "L40S", "--gpu-model", "NVIDIA L40S"],
        cwd=str(engine), capture_output=True, text=True,
        env=_plain_env(CUDA_VISIBLE_DEVICES="0,1"),
    )
    assert result.returncode == 0, result.stderr
    campaign = engine / ".ammo" / _marker_id(engine)
    target = _read(campaign / "target.json")
    assert target["serving_manifest"] == {
        "path": str(manifest.resolve()),
        "sha256": pumice_spec.checksum,
    }
    env = campaign_paths.campaign_env(campaign)
    assert env["AMMO_SERVING_SETUP"] == "pd"
    assert env["AMMO_SERVING_MANIFEST"] == str(manifest.resolve())
    assert env["AMMO_SERVING_MANIFEST_SHA256"] == pumice_spec.checksum


# ---------------------------------------------------------------------------
# launcher: a new scaffold never inherits the active campaign's derived env
# ---------------------------------------------------------------------------

_LAUNCHER = _SCRIPTS / "ammo"


def _engine_with_venv(path: Path) -> Path:
    """A git checkout whose .venv/bin/python runs this test's interpreter."""
    engine = _git_repo(path)
    python = engine / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    return engine


def _launch(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """`scripts/ammo new_target.py ...`: the launcher runs apply_env() first."""
    return subprocess.run(
        [sys.executable, str(_LAUNCHER), "new_target.py", *args],
        cwd=str(cwd), capture_output=True, text=True,
        env=_plain_env() if env is None else env,
    )


def test_scaffold_in_another_engine_records_that_engine(tmp_path):
    engine_a = _engine_with_venv(tmp_path / "engine-a")
    engine_b = _git_repo(tmp_path / "engine-b")
    first = _scaffold(engine_a, "--gpu-model", "NVIDIA H100", "--campaign-id", "c1")
    assert first.returncode == 0, first.stderr

    second = _launch(engine_a, *_BASE_ARGS, "--engine-root", str(engine_b))
    assert second.returncode == 0, second.stderr
    campaign = engine_b / ".ammo" / _marker_id(engine_b)
    target = _read(campaign / "target.json")
    assert target["engine"] == {"root": str(engine_b), "venv": str(engine_b / ".venv")}
    assert target["runtime"]["accelerator_name"] == "NVIDIA L40S"
    assert target["runtime"]["instance_family"] == "NVIDIA L40S"
    assert "stack_fingerprint" not in target["runtime"]
    assert _marker_id(engine_a) == "c1"


def test_batch_scaffold_after_a_pd_campaign_is_not_bound_to_its_manifest(tmp_path, spec):
    data = spec.model_dump(mode="json")
    data["runtime_profile"] = "pumice-cuda"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    engine = _engine_with_venv(tmp_path / "pumice")
    env = _plain_env(CUDA_VISIBLE_DEVICES="0,1")
    # A P/D scaffold takes no batch workload flags, so it does not use _BASE_ARGS.
    first = subprocess.run(
        [sys.executable, str(_NEW_TARGET), "--serving-manifest", str(manifest),
         "--hardware", "L40S", "--gpu-model", "NVIDIA L40S", "--campaign-id", "pd1"],
        cwd=str(engine), capture_output=True, text=True, env=env,
    )
    assert first.returncode == 0, first.stderr
    assert "serving_manifest" in _read(engine / ".ammo" / "pd1" / "target.json")

    second = _launch(engine, *_BASE_ARGS, "--campaign-id", "batch1", env=env)
    assert second.returncode == 0, second.stderr
    assert _marker_id(engine) == "batch1"
    target = _read(engine / ".ammo" / "batch1" / "target.json")
    assert "serving_manifest" not in target
    assert target["bench"]["runner"] == "pumice_grpc_bench"


# ---------------------------------------------------------------------------
# in-process: the profile gate and the stack fingerprint
# ---------------------------------------------------------------------------


@pytest.fixture
def in_process(tmp_path, monkeypatch):
    """Run new_target.main() in this process with a restored environment."""
    engine = _git_repo(tmp_path / "pumice")
    monkeypatch.chdir(engine)

    def run(*args: str) -> int:
        monkeypatch.setattr(sys, "argv", ["new_target.py", *_BASE_ARGS, *args])
        try:
            new_target.main()
        except SystemExit as exc:
            return int(exc.code or 0) if not isinstance(exc.code, str) else 1
        return 0

    with mock.patch.dict(os.environ, clear=False):
        for name in [k for k in os.environ if k.startswith("AMMO_")]:
            del os.environ[name]
        os.environ["CUDA_VISIBLE_DEVICES"] = "0"
        yield engine, run


def test_scaffold_calls_the_profile_gate_with_the_recorded_profile(in_process, monkeypatch):
    engine, run = in_process
    seen = []
    monkeypatch.setattr(new_target.runtime_ops, "require_supported_profile",
                        lambda profile: seen.append(profile), raising=False)
    assert run() == 0
    assert seen == ["pumice-cuda"]


def test_main_drops_the_active_campaign_env_before_reading_env(in_process, monkeypatch):
    """unapply_env() runs first, so a leaked P/D binding never reaches the guard."""
    engine, run = in_process
    os.environ["AMMO_SERVING_SETUP"] = "pd"
    os.environ["AMMO_SERVING_MANIFEST"] = str(engine / "old-manifest.json")
    calls = []

    def unapply(env=os.environ):
        calls.append(dict(env))
        for name in ("AMMO_SERVING_SETUP", "AMMO_SERVING_MANIFEST"):
            env.pop(name, None)

    monkeypatch.setattr(new_target.campaign_paths, "unapply_env", unapply)
    assert run() == 0
    assert len(calls) == 1 and calls[0]["AMMO_SERVING_SETUP"] == "pd"
    target = _read(engine / ".ammo" / _marker_id(engine) / "target.json")
    assert "serving_manifest" not in target


def test_a_refused_profile_writes_nothing(in_process, monkeypatch):
    engine, run = in_process

    def refuse(profile):
        raise ValueError(f"unsupported profile {profile}")

    monkeypatch.setattr(new_target.runtime_ops, "require_supported_profile", refuse,
                        raising=False)
    assert run() != 0
    assert not (engine / ".ammo").exists()


def test_stack_fingerprint_reaches_target_and_state(in_process, monkeypatch):
    engine, run = in_process
    fingerprint = {"pumice_commit": "a" * 40, "torch": "x", "image_digest": None}
    calls = []

    def fake(engine_root, python):
        calls.append((engine_root, python))
        return fingerprint

    monkeypatch.setattr(new_target, "_stack_fingerprint", fake)
    assert run() == 0
    campaign = engine / ".ammo" / _marker_id(engine)
    target = _read(campaign / "target.json")
    state = _read(campaign / "state.json")
    assert calls == [(engine, engine / ".venv" / "bin" / "python")]
    assert target["runtime"]["stack_fingerprint"] == fingerprint
    assert state["runtime"]["stack_fingerprint"]["server_stack_fingerprint"] == fingerprint
    env = campaign_paths.campaign_env(campaign)
    assert json.loads(env["AMMO_STACK_FINGERPRINT"]) == fingerprint


def test_missing_fingerprint_still_scaffolds(in_process, monkeypatch):
    """Preflight, not the scaffold, fails closed on a broken engine venv."""
    engine, run = in_process
    monkeypatch.setattr(new_target, "_stack_fingerprint", lambda engine_root, python: None)
    assert run() == 0
    target = _read(engine / ".ammo" / _marker_id(engine) / "target.json")
    assert "stack_fingerprint" not in target["runtime"]


def test_stack_fingerprint_skips_a_missing_engine_python(tmp_path, capsys):
    assert new_target._stack_fingerprint(tmp_path, tmp_path / "no" / "python") is None
    assert "stack_fingerprint" in capsys.readouterr().err


def test_stack_fingerprint_runs_preflight_on_the_new_engine(tmp_path, monkeypatch):
    """The seam with preflight_pumice.py --write-fingerprint: the object names
    the commit of the engine being scaffolded, not a checkout found elsewhere."""
    import preflight_pumice

    engine = _git_repo(tmp_path / "pumice")
    subprocess.run(["git", "-C", str(engine), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    head = subprocess.run(["git", "-C", str(engine), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    for name in [k for k in os.environ if k.startswith("AMMO_")]:
        monkeypatch.delenv(name)
    fingerprint = new_target._stack_fingerprint(engine, Path(sys.executable))
    assert fingerprint is not None
    assert tuple(fingerprint) == preflight_pumice.FINGERPRINT_KEYS
    assert fingerprint["pumice_commit"] == head


@pytest.mark.requires_gpu
def test_stack_fingerprint_from_a_real_engine_venv():
    """Integration: needs a Pumice checkout and its venv (AMMO_ENGINE_ROOT, AMMO_PYTHON)."""
    engine = os.environ.get("AMMO_ENGINE_ROOT")
    python = os.environ.get("AMMO_PYTHON")
    if not engine or not python:
        pytest.skip("set AMMO_ENGINE_ROOT and AMMO_PYTHON to a real Pumice checkout")
    fingerprint = new_target._stack_fingerprint(Path(engine), Path(python))
    assert isinstance(fingerprint, dict) and fingerprint


# Fixtures for the serving case, shared with the serving suite.
from test_ammo_serving import source, spec  # noqa: E402,F401
