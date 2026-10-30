"""ncu_sanity.sh and ncu_sanity_driver.py resolve the campaign through campaign_paths.

The driver reuses the bench runner's server launch, so it runs under the
engine venv. ncu_sanity.sh picks that python through `campaign_paths.py python`
when the cwd has no ./.venv. No GPU is needed: fake `timeout` and `ncu` record
the command line instead of profiling.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

_CAMPAIGN_ENV_NAMES = (
    "AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR",
    "AMMO_PYTHON", "CARGO_TARGET_DIR", "AMMO_GPU_RES_DIR", "AMMO_RUNTIME_PROFILE",
    "AMMO_ACCELERATOR_KIND", "AMMO_TP_SIZE", "AMMO_DP_SIZE", "AMMO_STACK_FINGERPRINT",
    "AMMO_INSTANCE_FAMILY", "AMMO_ACCELERATOR_NAME", "AMMO_SERVING_SETUP",
    "AMMO_SERVING_MANIFEST", "AMMO_SERVING_MANIFEST_SHA256",
)


@pytest.fixture(autouse=True)
def _restore_environ():
    """main() runs apply_env(), which writes os.environ; put it back afterwards."""
    saved = dict(os.environ)
    for name in _CAMPAIGN_ENV_NAMES:
        os.environ.pop(name, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _load_driver():
    spec = importlib.util.spec_from_file_location(
        "ncu_sanity_driver_under_test", str(_SCRIPTS_DIR / "ncu_sanity_driver.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _campaign(tmp_path: Path, target: dict) -> tuple[Path, Path]:
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    (campaign / "target.json").write_text(json.dumps(target), encoding="utf-8")
    return engine, campaign


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_driver_main_fills_the_campaign_env_first(tmp_path, monkeypatch):
    engine, campaign = _campaign(tmp_path, {"target": {"tp": 4}})
    monkeypatch.chdir(engine)
    monkeypatch.setattr(sys, "argv", ["ncu_sanity_driver.py", "--help"])
    driver = _load_driver()

    with pytest.raises(SystemExit) as excinfo:
        driver.main()

    assert excinfo.value.code == 0
    assert os.environ["AMMO_ARTIFACT_DIR"] == str(campaign)
    assert os.environ["AMMO_TP_SIZE"] == "4"


def test_driver_apply_env_is_the_first_statement_of_main():
    source = (_SCRIPTS_DIR / "ncu_sanity_driver.py").read_text(encoding="utf-8")
    body = source.split("\ndef main(", 1)[1].split("\n", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == "campaign_paths.apply_env()"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_wrapper_runs_the_driver_under_the_campaign_python(tmp_path):
    venv_python = _executable(tmp_path / "venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    engine, campaign = _campaign(
        tmp_path, {"engine": {"root": None, "venv": str(tmp_path / "venv")}}
    )
    workdir = campaign / "rounds" / "1"
    workdir.mkdir(parents=True)
    record = tmp_path / "timeout.argv"
    fake_bin = tmp_path / "bin"
    _executable(
        fake_bin / "timeout",
        f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {record}\n",
    )
    env = {k: v for k, v in os.environ.items() if k not in _CAMPAIGN_ENV_NAMES}
    env.update(
        PATH=f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
        CSV_OUT=str(tmp_path / "ncu" / "sanity.csv"),
        TARGET_JSON=str(campaign / "target.json"),
        BATCH_SIZE="8",
    )

    result = subprocess.run(
        ["bash", str(_SCRIPTS_DIR / "ncu_sanity.sh"), "gemm", "flash_fwd"],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    argv = record.read_text(encoding="utf-8").splitlines()
    driver_at = argv.index(str(_SCRIPTS_DIR / "ncu_sanity_driver.py"))
    assert Path(argv[driver_at - 1]).resolve() == venv_python.resolve()
    assert "regex:gemm|flash_fwd" in argv


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_wrapper_prefers_the_cwd_venv(tmp_path):
    engine, campaign = _campaign(tmp_path, {})
    _executable(engine / ".venv" / "bin" / "python", "#!/bin/sh\nexit 0\n")
    record = tmp_path / "timeout.argv"
    fake_bin = tmp_path / "bin"
    _executable(
        fake_bin / "timeout",
        f"#!/bin/sh\nfor a in \"$@\"; do printf '%s\\n' \"$a\"; done > {record}\n",
    )
    env = {k: v for k, v in os.environ.items() if k not in _CAMPAIGN_ENV_NAMES}
    env.update(
        PATH=f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
        CSV_OUT=str(tmp_path / "sanity.csv"),
        TARGET_JSON=str(campaign / "target.json"),
        BATCH_SIZE="8",
    )

    result = subprocess.run(
        ["bash", str(_SCRIPTS_DIR / "ncu_sanity.sh"), "gemm"],
        cwd=engine, env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr
    argv = record.read_text(encoding="utf-8").splitlines()
    driver_at = argv.index(str(_SCRIPTS_DIR / "ncu_sanity_driver.py"))
    assert argv[driver_at - 1] == "./.venv/bin/python"
