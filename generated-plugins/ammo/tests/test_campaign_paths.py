"""Tests for scripts/campaign_paths.py, the one resolution layer (conventions §2-§4)."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

R = Path(__file__).resolve().parents[1]
SCRIPTS = R / "scripts"
sys.path.insert(0, str(SCRIPTS))

import campaign_paths as cp  # noqa: E402

CID = "20261028-120000-abc123"
ENV_DOC = R / "references" / "campaign-env.md"


# --------------------------------------------------------------------------- helpers


def make_engine(root: Path, cid: str = CID, target: dict | None = None) -> tuple[Path, Path]:
    """Create <root>/.ammo/active -> cid and <root>/.ammo/<cid>/target.json."""
    campaign = root / ".ammo" / cid
    campaign.mkdir(parents=True)
    (root / ".ammo" / "active").write_text(cid + "\n", encoding="utf-8")
    if target is not None:
        (campaign / "target.json").write_text(json.dumps(target), encoding="utf-8")
    return root, campaign


def fake_exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def injected_names() -> set[str]:
    """First-column names of section 1 ("Injected names") of campaign-env.md."""
    names: set[str] = set()
    in_section = False
    for line in ENV_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.lower().startswith("## 1.")
            continue
        if in_section and line.startswith("|"):
            first = line.strip().strip("|").split("|")[0]
            names.update(re.findall(r"`([A-Z][A-Z0-9_]+)`", first))
    return names


def full_target(engine: Path) -> dict:
    return {
        "engine": {"root": str(engine), "venv": str(engine / ".venv")},
        "runtime": {
            "profile": "pumice-cuda",
            "accelerator_kind": "cuda",
            "accelerator_name": "NVIDIA L40S",
            "stack_fingerprint": {"engine_commit": "abc", "image_digest": None},
        },
        "target": {"model_id": "m", "tp": 2, "dp": 2},
        "serving_manifest": {"path": str(engine / "manifest.json"), "sha256": "f" * 64},
    }


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("AMMO_") or name in ("CARGO_TARGET_DIR", "CLAUDE_PROJECT_DIR"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# --------------------------------------------------------------------------- find_campaign_dir


def test_artifact_dir_env_overrides_marker(tmp_path, clean_env):
    engine, _ = make_engine(tmp_path / "engine")
    other = tmp_path / "elsewhere" / "camp"
    found = cp.find_campaign_dir(engine, env={"AMMO_ARTIFACT_DIR": str(other)})
    assert found == other


def test_artifact_dir_env_from_os_environ(tmp_path, clean_env):
    other = tmp_path / "camp"
    clean_env.setenv("AMMO_ARTIFACT_DIR", str(other))
    assert cp.find_campaign_dir(tmp_path) == other


def test_walk_from_engine_root(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    assert cp.find_campaign_dir(engine, env={}) == campaign.resolve()


def test_walk_from_nested_dir(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    deep = engine / "src" / "pumice" / "engine"
    deep.mkdir(parents=True)
    assert cp.find_campaign_dir(deep, env={}) == campaign.resolve()


def test_walk_from_track_worktree_child(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    child = engine / ".claude" / "worktrees" / "op-001-r1" / "pumice-rust" / "src"
    child.mkdir(parents=True)
    assert cp.find_campaign_dir(child, env={}) == campaign.resolve()


def test_default_start_is_cwd(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    clean_env.chdir(engine)
    assert cp.find_campaign_dir() == campaign.resolve()


def test_not_found_returns_none(tmp_path, clean_env):
    lonely = tmp_path / "no-campaign" / "here"
    lonely.mkdir(parents=True)
    assert cp.find_campaign_dir(lonely, env={}) is None


@pytest.mark.parametrize("content", ["", "\n", "../escape", "a/b", ".", ".."])
def test_bad_marker_returns_none(tmp_path, clean_env, content):
    engine, _ = make_engine(tmp_path / "engine")
    (engine / ".ammo" / "active").write_text(content, encoding="utf-8")
    assert cp.find_campaign_dir(engine, env={}) is None


def test_marker_to_missing_dir_returns_none(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    campaign.rmdir()
    assert cp.find_campaign_dir(engine, env={}) is None


def test_nearest_marker_wins(tmp_path, clean_env):
    outer, _ = make_engine(tmp_path / "outer", cid="outer-1")
    inner, inner_campaign = make_engine(outer / "inner", cid="inner-1")
    assert cp.find_campaign_dir(inner, env={}) == inner_campaign.resolve()


# --------------------------------------------------------------------------- small accessors


def test_campaign_id_and_engine_root(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    assert cp.campaign_id(campaign) == CID
    assert cp.engine_root(campaign) == engine


def test_plugin_root_and_path():
    assert cp.plugin_root() == R
    assert cp.plugin_path("schemas", "state.schema.json") == R / "schemas" / "state.schema.json"


# --------------------------------------------------------------------------- in_campaign


def test_in_campaign_true_for_file_inside(tmp_path):
    _, campaign = make_engine(tmp_path / "engine")
    assert cp.in_campaign(campaign / "state.json", campaign)
    assert cp.in_campaign(campaign / "rounds" / "1" / "not-yet-written.json", campaign)
    assert cp.in_campaign(campaign, campaign)


def test_in_campaign_false_outside_and_prefix_sibling(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    sibling = engine / ".ammo" / (CID + "x")
    sibling.mkdir()
    assert not cp.in_campaign(engine / "src" / "x.py", campaign)
    assert not cp.in_campaign(sibling / "state.json", campaign)
    assert not cp.in_campaign(campaign / ".." / (CID + "x") / "state.json", campaign)


def test_in_campaign_none_campaign_is_false(tmp_path):
    assert not cp.in_campaign(tmp_path / "x", None)


def test_in_campaign_symlink_out_of_campaign_is_false(tmp_path):
    _, campaign = make_engine(tmp_path / "engine")
    outside = tmp_path / "outside"
    outside.mkdir()
    (campaign / "link").symlink_to(outside, target_is_directory=True)
    assert not cp.in_campaign(campaign / "link" / "state.json", campaign)


def test_in_campaign_symlink_into_campaign_is_true(tmp_path):
    _, campaign = make_engine(tmp_path / "engine")
    alias = tmp_path / "alias"
    alias.symlink_to(campaign, target_is_directory=True)
    assert cp.in_campaign(alias / "state.json", campaign)
    assert cp.in_campaign(campaign / "state.json", alias)


# --------------------------------------------------------------------------- python_exe


def test_python_exe_prefers_target_venv(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine", target={})
    venv_py = fake_exe(tmp_path / "venv" / "bin" / "python")
    env_py = fake_exe(tmp_path / "envpy" / "python")
    fake_exe(engine / ".venv" / "bin" / "python")
    (campaign / "target.json").write_text(json.dumps({"engine": {"venv": str(tmp_path / "venv")}}))
    clean_env.setenv("AMMO_PYTHON", str(env_py))
    assert cp.python_exe(campaign) == venv_py


def test_python_exe_falls_to_ammo_python(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine", target={"engine": {"venv": str(tmp_path / "missing")}})
    env_py = fake_exe(tmp_path / "envpy" / "python")
    fake_exe(engine / ".venv" / "bin" / "python")
    clean_env.setenv("AMMO_PYTHON", str(env_py))
    assert cp.python_exe(campaign) == env_py


def test_python_exe_falls_to_engine_venv(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine", target={})
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    assert cp.python_exe(campaign) == engine_py


def test_python_exe_last_resort_is_python3(tmp_path, clean_env):
    _, campaign = make_engine(tmp_path / "engine", target={})
    expected = Path(shutil.which("python3") or sys.executable)
    assert cp.python_exe(campaign) == expected
    assert cp.python_exe(None) == expected


def test_python_exe_without_campaign_uses_ammo_python(tmp_path, clean_env):
    env_py = fake_exe(tmp_path / "envpy" / "python")
    clean_env.setenv("AMMO_PYTHON", str(env_py))
    assert cp.python_exe(None) == env_py


def git(*args, cwd):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def git_engine(tmp_path: Path, target: dict | None = None) -> tuple[Path, Path, Path]:
    """An engine git repo with a campaign and a linked track worktree under .claude/worktrees."""
    engine, campaign = make_engine(tmp_path / "engine", target=target)
    git("init", "-q", cwd=engine)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=engine)
    worktree = engine / ".claude" / "worktrees" / "op-001"
    git("worktree", "add", "-q", "-b", "op-001", str(worktree), cwd=engine)
    return engine, campaign, worktree


def test_python_exe_prefers_track_worktree_venv(tmp_path, clean_env):
    engine, campaign, worktree = git_engine(tmp_path)
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    track_py = fake_exe(worktree / ".venv" / "bin" / "python")
    (worktree / "src").mkdir()
    clean_env.chdir(worktree / "src")
    assert cp.python_exe(campaign).resolve() == track_py.resolve()
    clean_env.chdir(engine)
    assert cp.python_exe(campaign) == engine_py


def test_track_worktree_venv_beats_target_venv_and_ammo_python(tmp_path, clean_env):
    engine, campaign, worktree = git_engine(tmp_path)
    fake_exe(engine / ".venv" / "bin" / "python")  # at the engine root, engine.venv must still win
    venv_py = fake_exe(tmp_path / "venv" / "bin" / "python")
    (campaign / "target.json").write_text(json.dumps({"engine": {"venv": str(tmp_path / "venv")}}))
    clean_env.setenv("AMMO_PYTHON", str(fake_exe(tmp_path / "envpy" / "python")))
    track_py = fake_exe(worktree / ".venv" / "bin" / "python")
    clean_env.chdir(worktree)
    assert cp.python_exe(campaign).resolve() == track_py.resolve()
    clean_env.chdir(engine)
    assert cp.python_exe(campaign) == venv_py


def test_track_worktree_without_venv_falls_through(tmp_path, clean_env):
    engine, campaign, worktree = git_engine(tmp_path)
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    clean_env.chdir(worktree)
    assert cp.python_exe(campaign) == engine_py


def test_unrelated_repo_venv_is_not_used(tmp_path, clean_env):
    engine, campaign, _ = git_engine(tmp_path)
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    other = tmp_path / "other"
    other.mkdir()
    git("init", "-q", cwd=other)
    fake_exe(other / ".venv" / "bin" / "python")
    clean_env.chdir(other)
    assert cp.python_exe(campaign) == engine_py


def test_track_of_an_engine_that_is_itself_a_linked_worktree(tmp_path, clean_env):
    # Every linked worktree shares the main repo's common dir, which here is outside the engine.
    main = tmp_path / "main"
    main.mkdir()
    git("init", "-q", cwd=main)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=main)
    engine = tmp_path / "engine"
    git("worktree", "add", "-q", "-b", "engine", str(engine), cwd=main)
    _, campaign = make_engine(engine)
    worktree = engine / ".claude" / "worktrees" / "op-001"
    git("worktree", "add", "-q", "-b", "op-001", str(worktree), cwd=engine)
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    track_py = fake_exe(worktree / ".venv" / "bin" / "python")
    fake_exe(main / ".venv" / "bin" / "python")
    clean_env.chdir(worktree)
    assert cp.python_exe(campaign).resolve() == track_py.resolve()
    clean_env.chdir(engine)
    assert cp.python_exe(campaign) == engine_py


def test_codex_track_worktree_is_detected(tmp_path, clean_env):
    engine, campaign, _ = git_engine(tmp_path)
    codex_track = engine / ".codex" / "worktrees" / "op-002"
    git("worktree", "add", "-q", "-b", "op-002", str(codex_track), cwd=engine)
    fake_exe(engine / ".venv" / "bin" / "python")
    track_py = fake_exe(codex_track / ".venv" / "bin" / "python")
    clean_env.chdir(codex_track)
    assert cp.python_exe(campaign).resolve() == track_py.resolve()


def test_any_linked_worktree_of_the_engine_repo_counts(tmp_path, clean_env):
    # Same git repository as the engine, so its own build is the one to run.
    engine, campaign, _ = git_engine(tmp_path)
    sibling = tmp_path / "sibling"
    git("worktree", "add", "-q", "-b", "sibling", str(sibling), cwd=engine)
    fake_exe(engine / ".venv" / "bin" / "python")
    sibling_py = fake_exe(sibling / ".venv" / "bin" / "python")
    clean_env.chdir(sibling)
    assert cp.python_exe(campaign).resolve() == sibling_py.resolve()


def test_nested_independent_repo_is_not_used(tmp_path, clean_env):
    engine, campaign, _ = git_engine(tmp_path)
    nested = engine / "third_party" / "lib"
    nested.mkdir(parents=True)
    git("init", "-q", cwd=nested)
    engine_py = fake_exe(engine / ".venv" / "bin" / "python")
    fake_exe(nested / ".venv" / "bin" / "python")
    clean_env.chdir(nested)
    assert cp.python_exe(campaign) == engine_py


def test_no_worktree_check_without_campaign(tmp_path, clean_env):
    _, _, worktree = git_engine(tmp_path)
    fake_exe(worktree / ".venv" / "bin" / "python")
    clean_env.chdir(worktree)
    assert cp.python_exe(None) == Path(shutil.which("python3") or sys.executable)


def test_launcher_runs_track_worktree_python(tmp_path):
    engine, campaign, worktree = git_engine(tmp_path)
    fake_exe(engine / ".venv" / "bin" / "python", "#!/bin/sh\necho engine\n")
    fake_exe(worktree / ".venv" / "bin" / "python", '#!/bin/sh\necho "track $AMMO_SESSION_ID"\n')
    probe = tmp_path / "probe.py"
    probe.write_text("print('unused')\n")
    proc = run([str(SCRIPTS / "ammo"), str(probe)], cwd=worktree)
    assert (proc.returncode, proc.stdout.strip()) == (0, f"track {CID}"), proc.stderr
    proc = run([str(SCRIPTS / "ammo"), str(probe)], cwd=engine)
    assert (proc.returncode, proc.stdout.strip()) == (0, "engine"), proc.stderr


# --------------------------------------------------------------------------- campaign_env


def test_campaign_env_emits_every_injected_name(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    (campaign / "target.json").write_text(json.dumps(full_target(engine)))
    names = injected_names()
    assert len(names) >= 17, names
    assert set(cp.campaign_env(campaign)) == names


def test_campaign_env_values(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    (campaign / "target.json").write_text(json.dumps(full_target(engine)))
    env = cp.campaign_env(campaign)
    assert env["AMMO_ARTIFACT_DIR"] == str(campaign)
    assert env["AMMO_SESSION_ID"] == CID
    assert env["AMMO_ENGINE_ROOT"] == env["AMMO_RUNTIME_SOURCE_DIR"] == str(engine)
    assert env["AMMO_PYTHON"] == str(engine / ".venv" / "bin" / "python")
    assert env["CARGO_TARGET_DIR"] == str(engine / "target")
    assert env["AMMO_GPU_RES_DIR"] == "/tmp/ammo_gpu_res"
    assert env["AMMO_RUNTIME_PROFILE"] == "pumice-cuda"
    assert env["AMMO_ACCELERATOR_KIND"] == "cuda"
    assert env["AMMO_TP_SIZE"] == "2" and env["AMMO_DP_SIZE"] == "2"
    assert json.loads(env["AMMO_STACK_FINGERPRINT"]) == {"engine_commit": "abc", "image_digest": None}
    assert env["AMMO_INSTANCE_FAMILY"] == env["AMMO_ACCELERATOR_NAME"] == "NVIDIA L40S"
    assert env["AMMO_SERVING_SETUP"] == "pd"
    assert env["AMMO_SERVING_MANIFEST"] == str(engine / "manifest.json")
    assert env["AMMO_SERVING_MANIFEST_SHA256"] == "f" * 64
    assert all(isinstance(v, str) for v in env.values())


def test_campaign_env_engine_root_from_target(tmp_path, clean_env):
    _, campaign = make_engine(tmp_path / "engine")
    real = tmp_path / "real-engine"
    (campaign / "target.json").write_text(json.dumps({"engine": {"root": str(real)}}))
    env = cp.campaign_env(campaign)
    assert env["AMMO_ENGINE_ROOT"] == env["AMMO_RUNTIME_SOURCE_DIR"] == str(real)
    assert env["CARGO_TARGET_DIR"] == str(real / "target")


def test_campaign_env_defaults_without_target_json(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    env = cp.campaign_env(campaign)
    assert env["AMMO_RUNTIME_PROFILE"] == "pumice-cuda"
    assert env["AMMO_ACCELERATOR_KIND"] == "cuda"
    assert env["AMMO_ENGINE_ROOT"] == str(engine)
    optional = {
        "AMMO_TP_SIZE", "AMMO_DP_SIZE", "AMMO_STACK_FINGERPRINT", "AMMO_INSTANCE_FAMILY",
        "AMMO_ACCELERATOR_NAME", "AMMO_SERVING_SETUP", "AMMO_SERVING_MANIFEST",
        "AMMO_SERVING_MANIFEST_SHA256",
    }
    assert not optional & set(env)


def test_campaign_env_omits_parallelism_when_tp_zero(tmp_path, clean_env):
    _, campaign = make_engine(tmp_path / "engine", target={"target": {"tp": 0, "dp": 2}})
    env = cp.campaign_env(campaign)
    assert "AMMO_TP_SIZE" not in env and "AMMO_DP_SIZE" not in env


def test_campaign_env_dp_defaults_to_state_then_one(tmp_path, clean_env):
    _, campaign = make_engine(tmp_path / "engine", target={"target": {"tp": 4}})
    assert cp.campaign_env(campaign)["AMMO_DP_SIZE"] == "1"
    (campaign / "state.json").write_text(json.dumps({"target": {"dp": 3}}))
    assert cp.campaign_env(campaign)["AMMO_DP_SIZE"] == "3"


# --------------------------------------------------------------------------- apply_env


def test_apply_env_setdefault_explicit_wins(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine", target=full_target(tmp_path / "engine"))
    clean_env.chdir(engine)
    env = {"AMMO_GPU_RES_DIR": "/custom/res", "AMMO_SESSION_ID": "explicit-id", "AMMO_TP_SIZE": ""}
    found = cp.apply_env(env)
    assert found == campaign.resolve()
    assert env["AMMO_GPU_RES_DIR"] == "/custom/res"
    assert env["AMMO_SESSION_ID"] == "explicit-id"
    assert env["AMMO_TP_SIZE"] == "2"  # an empty value counts as unset
    assert env["AMMO_ARTIFACT_DIR"] == str(campaign.resolve())


def test_apply_env_defaults_to_os_environ(tmp_path, clean_env):
    engine, campaign = make_engine(tmp_path / "engine")
    clean_env.chdir(engine)
    saved = dict(os.environ)
    try:
        assert cp.apply_env() == campaign.resolve()
        assert os.environ["AMMO_SESSION_ID"] == CID
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_apply_env_not_found_leaves_env_alone(tmp_path, clean_env):
    clean_env.chdir(tmp_path)
    env = {"PATH": "/bin"}
    assert cp.apply_env(env) is None
    assert env == {"PATH": "/bin"}


# --------------------------------------------------------------------------- unapply_env

# A kept name (explicit value), an overwritten name (empty counts as unset), and
# every other derived name unset.
BEFORE = {"PATH": "/bin", "AMMO_GPU_RES_DIR": "/custom/res", "AMMO_TP_SIZE": "", "AMMO_SERVING_MANIFEST": ""}


def full_engine(tmp_path, clean_env) -> Path:
    engine, campaign = make_engine(tmp_path / "engine", target=full_target(tmp_path / "engine"))
    clean_env.chdir(engine)
    return campaign


def test_apply_env_records_each_name_it_sets(tmp_path, clean_env):
    campaign = full_engine(tmp_path, clean_env)
    env = dict(BEFORE)
    cp.apply_env(env)
    record = json.loads(env["AMMO_APPLIED_ENV"])
    assert record["AMMO_TP_SIZE"] == "" and record["AMMO_SERVING_MANIFEST"] == ""
    assert record["AMMO_PYTHON"] is None and record["AMMO_ARTIFACT_DIR"] is None
    assert set(record) == set(cp.campaign_env(campaign)) - {"AMMO_GPU_RES_DIR"}


def test_unapply_env_restores_set_overwritten_and_unset_names(tmp_path, clean_env):
    full_engine(tmp_path, clean_env)
    env = dict(BEFORE)
    cp.apply_env(env)
    assert env["AMMO_SERVING_MANIFEST"] and env["AMMO_TP_SIZE"] == "2" and env["AMMO_PYTHON"]
    cp.unapply_env(env)
    assert env == BEFORE


def test_apply_and_unapply_are_idempotent(tmp_path, clean_env):
    full_engine(tmp_path, clean_env)
    env = dict(BEFORE)
    cp.apply_env(env)
    first = dict(env)
    cp.apply_env(env)
    assert env == first
    cp.unapply_env(env)
    assert env == BEFORE
    cp.unapply_env(env)
    assert env == BEFORE


def test_a_later_apply_keeps_the_first_prior_value(tmp_path, clean_env):
    full_engine(tmp_path, clean_env)
    env = {"PATH": "/bin"}
    cp.apply_env(env)
    env["AMMO_PYTHON"] = ""
    cp.apply_env(env)
    assert json.loads(env["AMMO_APPLIED_ENV"])["AMMO_PYTHON"] is None
    cp.unapply_env(env)
    assert env == {"PATH": "/bin"}


def test_unapply_env_defaults_to_os_environ(tmp_path, clean_env):
    full_engine(tmp_path, clean_env)
    clean_env.setenv("AMMO_TP_SIZE", "")
    saved = dict(os.environ)
    try:
        cp.apply_env()
        assert os.environ["AMMO_SESSION_ID"] == CID and os.environ["AMMO_TP_SIZE"] == "2"
        cp.unapply_env()
        assert dict(os.environ) == saved
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.mark.parametrize("record", [
    None,
    "not json",
    "[1]",
    '{"AMMO_SESSION_ID": 3}',
    '{"PATH": null, "LD_PRELOAD": "/x.so", "AMMO_APPLIED_ENV": "x"}',
])
def test_unapply_env_without_a_usable_record_changes_nothing_else(record):
    env = {"PATH": "/bin", "AMMO_SESSION_ID": "kept"}
    if record is not None:
        env["AMMO_APPLIED_ENV"] = record
    cp.unapply_env(env)
    assert env == {"PATH": "/bin", "AMMO_SESSION_ID": "kept"}


# --------------------------------------------------------------------------- CLI and launcher


def run(argv, cwd, env=None, stdin=None):
    base = {k: v for k, v in os.environ.items() if not k.startswith("AMMO_") and k != "CLAUDE_PROJECT_DIR"}
    base.update(env or {})
    return subprocess.run(argv, cwd=cwd, env=base, input=stdin, capture_output=True, text=True, timeout=60)


EXPORT_LINE = re.compile(r"^export [A-Z_][A-Z0-9_]*=\S.*$")


def test_cli_env_prints_export_lines_only(tmp_path):
    engine, campaign = make_engine(tmp_path / "my engine", target=full_target(tmp_path / "my engine"))
    proc = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "env"], cwd=engine)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines and all(EXPORT_LINE.match(line) for line in lines), proc.stdout
    script = proc.stdout + '\nprintf "%s\\n" "$AMMO_ARTIFACT_DIR" "$AMMO_STACK_FINGERPRINT"\n'
    shown = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True).stdout.splitlines()
    assert shown[0] == str(campaign.resolve())
    assert json.loads(shown[1])["engine_commit"] == "abc"


def test_cli_env_keeps_explicit_values(tmp_path):
    engine, _ = make_engine(tmp_path / "engine")
    proc = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "env"], cwd=engine, env={"AMMO_GPU_RES_DIR": "/x y"})
    assert "export AMMO_GPU_RES_DIR='/x y'" in proc.stdout.splitlines()


def test_cli_env_outside_campaign_fails_closed(tmp_path):
    proc = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "env"], cwd=tmp_path)
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert ".ammo/active" in proc.stderr and str(tmp_path.resolve()) in proc.stderr


def test_cli_env_hook_mode_uses_payload_cwd_and_is_silent(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    payload = json.dumps({"cwd": str(engine), "session_id": "s"})
    proc = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "env", "--hook"], cwd=tmp_path, stdin=payload)
    assert proc.returncode == 0
    assert f"export AMMO_ARTIFACT_DIR={campaign.resolve()}" in proc.stdout.splitlines()
    for bad in ("not json", json.dumps({"cwd": str(tmp_path)}), ""):
        miss = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "env", "--hook"], cwd=tmp_path, stdin=bad)
        assert miss.stdout == "" and miss.stderr == ""


def test_cli_hook_mode_falls_back_to_project_dir(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    payload = json.dumps({"cwd": str(tmp_path)})
    proc = run(
        [sys.executable, str(SCRIPTS / "campaign_paths.py"), "env", "--hook"],
        cwd=tmp_path, env={"CLAUDE_PROJECT_DIR": str(engine)}, stdin=payload,
    )
    assert f"export AMMO_ARTIFACT_DIR={campaign.resolve()}" in proc.stdout.splitlines()


def test_cli_in_campaign(tmp_path):
    _, campaign = make_engine(tmp_path / "engine")
    env = {"AMMO_ARTIFACT_DIR": str(campaign)}
    inside = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "in-campaign", str(campaign / "state.json")], tmp_path, env)
    outside = run([sys.executable, str(SCRIPTS / "campaign_paths.py"), "in-campaign", str(tmp_path)], tmp_path, env)
    assert (inside.returncode, inside.stdout) == (0, "")
    assert (outside.returncode, outside.stdout) == (1, "")


def test_launcher_help_from_any_cwd(tmp_path):
    proc = run([str(SCRIPTS / "ammo"), "--help"], cwd=tmp_path)
    assert proc.returncode == 0
    assert "ammo <script>" in proc.stdout


def test_launcher_runs_sibling_from_any_cwd(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    child = engine / ".claude" / "worktrees" / "t1"
    child.mkdir(parents=True)
    proc = run([str(SCRIPTS / "ammo"), "campaign_paths.py", "env"], cwd=child)
    assert proc.returncode == 0, proc.stderr
    assert f"export AMMO_SESSION_ID={CID}" in proc.stdout.splitlines()


def test_launcher_uses_campaign_python_and_env(tmp_path):
    engine, campaign = make_engine(tmp_path / "engine")
    marker = tmp_path / "ran-with"
    fake_exe(
        engine / ".venv" / "bin" / "python",
        f'#!/bin/sh\necho "$AMMO_SESSION_ID $1" > "{marker}"\n',
    )
    probe = tmp_path / "probe.py"
    probe.write_text("print('unused')\n")
    proc = run([str(SCRIPTS / "ammo"), str(probe)], cwd=engine)
    assert proc.returncode == 0, proc.stderr
    assert marker.read_text().split() == [CID, str(probe)]


def test_launcher_child_can_unapply_the_campaign_env(tmp_path):
    engine, _ = make_engine(tmp_path / "engine", target=full_target(tmp_path / "engine"))
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import campaign_paths\n"
        "seen = sorted(k for k in os.environ if k.startswith('AMMO_'))\n"
        "campaign_paths.unapply_env()\n"
        "left = {k: v for k, v in os.environ.items() if k.startswith('AMMO_') or k == 'CARGO_TARGET_DIR'}\n"
        "print(json.dumps({'seen': seen, 'left': left}))\n"
    )
    keep = {"AMMO_GPU_RES_DIR": "/custom/res", "CARGO_TARGET_DIR": ""}
    proc = run([str(SCRIPTS / "ammo"), str(probe)], cwd=engine, env=keep)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert {"AMMO_SERVING_MANIFEST", "AMMO_PYTHON", "AMMO_APPLIED_ENV"} <= set(out["seen"])
    assert out["left"] == keep


def test_launcher_relative_path_falls_back_to_plugin_root(tmp_path):
    proc = run([str(SCRIPTS / "ammo"), "scripts/campaign_paths.py", "find", "--start", str(tmp_path)], cwd=tmp_path)
    assert proc.returncode == 1 and "no campaign" in proc.stderr, proc.stderr


def test_launcher_unknown_script(tmp_path):
    proc = run([str(SCRIPTS / "ammo"), "no_such_script.py"], cwd=tmp_path)
    assert proc.returncode == 2
    assert "no_such_script.py" in proc.stderr


# --------------------------------------------------------------------------- tests/conftest.py


def test_conftest_deselects_neuron_and_gpu_by_default(tmp_path):
    shutil.copy(R / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_marks.py").write_text(
        "import pytest\n"
        "def test_plain(): pass\n"
        "@pytest.mark.neuron\ndef test_neuron(): pass\n"
        "@pytest.mark.requires_gpu\ndef test_gpu(): pass\n"
    )

    def outcome(*extra):
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "error::pytest.PytestUnknownMarkWarning", *extra],
            cwd=tmp_path, capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, proc.stdout.strip().splitlines()[-1]

    code, summary = outcome()
    assert code == 0 and "1 passed, 2 deselected" in summary, summary
    code, summary = outcome("-m", "requires_gpu")
    assert code == 0 and "1 passed, 2 deselected" in summary, summary
    code, summary = outcome("-m", "neuron or requires_gpu or not neuron")
    assert code == 0 and "3 passed" in summary, summary


# --------------------------------------------------------------------------- plugin python deps

#: Third-party imports the engine stack owns. Preflight checks pumice and
#: pumice_rust; the rest come with Pumice's own dependencies.
ENGINE_OWNED = frozenset({
    "pumice", "pumice_pb", "vllm", "torch", "transformers", "grpc", "numpy", "huggingface_hub",
})
#: Imports behind an ImportError fallback: runtime_ops reads a Neuron parquet
#: profile with pyarrow, else pandas, else it names both.
OPTIONAL = frozenset({"pyarrow", "pandas"})


def _top_imports(source: str) -> set:
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _third_party_imports() -> set:
    """Top-level imports in scripts/*.py, scripts/ammo and the inline Python of hooks/*.sh."""
    local = {path.stem for path in SCRIPTS.glob("*.py")}
    found = set()
    for path in [*sorted(SCRIPTS.glob("*.py")), SCRIPTS / "ammo"]:
        found |= _top_imports(path.read_text(encoding="utf-8"))
    for path in sorted((R / "hooks").glob("*.sh")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n\s*\1\b", text, re.S):
            found |= _top_imports(match.group(2))
        for match in re.finditer(r"python3?\S*\s+-c\s+(['\"])(.*?)(?<!\\)\1", text, re.S):
            found |= _top_imports(match.group(2))
    return {name for name in found
            if name not in sys.stdlib_module_names and name not in local and name != "__future__"}


def test_plugin_python_deps_match_the_imports():
    found = _third_party_imports()
    assert set(cp.PLUGIN_PYTHON_DEPS) == found - ENGINE_OWNED - OPTIONAL
    assert not (ENGINE_OWNED | OPTIONAL) - found, "stale exclusion: no script imports it"
    assert list(cp.PLUGIN_PYTHON_DEPS) == sorted(set(cp.PLUGIN_PYTHON_DEPS))


def test_plugin_pip_names_use_the_distribution_name():
    assert cp.plugin_pip_names() == ["httpx", "jsonschema", "psutil", "pydantic", "pyyaml"]
    assert cp.plugin_pip_names(["yaml"]) == ["pyyaml"]


def test_install_hint_gives_pip_and_uv_forms():
    hint = cp.plugin_deps_hint("/e/.venv/bin/python", ["yaml", "jsonschema"])
    assert "/e/.venv/bin/python -m pip install pyyaml jsonschema" in hint
    assert "uv pip install --python /e/.venv/bin/python pyyaml jsonschema" in hint


def test_readme_lists_every_plugin_python_dep():
    readme = (R / "README.md").read_text(encoding="utf-8")
    assert "Python packages the plugin needs in the engine venv" in readme
    for name in cp.plugin_pip_names():
        assert f"`{name}`" in readme, name
    assert f"<checkout>/.venv/bin/python -m pip install {' '.join(cp.plugin_pip_names())}" in readme
