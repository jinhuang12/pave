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
