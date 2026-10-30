"""run_pumice_grpc_bench.py resolves env and plugin files through campaign_paths.

Conventions §2: apply_env() at the top of main(), plugin_path() for plugin files.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import campaign_paths  # noqa: E402
import run_pumice_grpc_bench as bench  # noqa: E402

_CAMPAIGN_ENV_NAMES = (
    "AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR",
    "AMMO_PYTHON", "CARGO_TARGET_DIR", "AMMO_GPU_RES_DIR", "AMMO_RUNTIME_PROFILE",
    "AMMO_ACCELERATOR_KIND", "AMMO_TP_SIZE", "AMMO_DP_SIZE", "AMMO_STACK_FINGERPRINT",
    "AMMO_INSTANCE_FAMILY", "AMMO_ACCELERATOR_NAME", "AMMO_SERVING_SETUP",
    "AMMO_SERVING_MANIFEST", "AMMO_SERVING_MANIFEST_SHA256",
)


class _Stop(Exception):
    pass


@pytest.fixture(autouse=True)
def _restore_environ():
    """main() runs apply_env(), which writes os.environ; put it back afterwards."""
    saved = dict(os.environ)
    for name in _CAMPAIGN_ENV_NAMES:
        os.environ.pop(name, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


def test_main_fills_the_campaign_env_before_parsing(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    fingerprint = {"pumice_commit": "a" * 40, "image_digest": None}
    (campaign / "target.json").write_text(
        json.dumps({"runtime": {"profile": "pumice-cuda", "stack_fingerprint": fingerprint}}),
        encoding="utf-8",
    )
    worktree = engine / ".claude" / "worktrees" / "track-a"
    worktree.mkdir(parents=True)
    monkeypatch.chdir(worktree)
    seen = {}

    def fake_parse_args(argv):
        seen.update({name: os.environ.get(name) for name in _CAMPAIGN_ENV_NAMES})
        raise _Stop

    monkeypatch.setattr(bench, "parse_args", fake_parse_args)
    with pytest.raises(_Stop):
        bench.main(["--artifact-dir", str(campaign)])

    assert seen["AMMO_ARTIFACT_DIR"] == str(campaign)
    assert seen["AMMO_RUNTIME_SOURCE_DIR"] == str(engine)
    assert seen["CARGO_TARGET_DIR"] == str(engine / "target")
    assert seen["AMMO_GPU_RES_DIR"] == "/tmp/ammo_gpu_res"
    assert json.loads(seen["AMMO_STACK_FINGERPRINT"]) == fingerprint


def test_explicit_env_wins_over_the_layer(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    (campaign / "target.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(engine)
    monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "my-target"))
    seen = {}

    def fake_parse_args(argv):
        seen["CARGO_TARGET_DIR"] = os.environ.get("CARGO_TARGET_DIR")
        raise _Stop

    monkeypatch.setattr(bench, "parse_args", fake_parse_args)
    with pytest.raises(_Stop):
        bench.main([])
    assert seen["CARGO_TARGET_DIR"] == str(tmp_path / "my-target")


def test_apply_env_is_the_first_statement_of_main():
    source = (_SCRIPTS_DIR / "run_pumice_grpc_bench.py").read_text(encoding="utf-8")
    body = source.split("\ndef main(", 1)[1].split("\n", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == "campaign_paths.apply_env()"


def test_gsm8k_data_resolves_through_plugin_path():
    assert bench._GSM8K_FULL_PATH == campaign_paths.plugin_path("data", "gsm8k_full.json")
    assert bench._GSM8K_SUBSET_PATH == campaign_paths.plugin_path("data", "gsm8k_subset.json")
    assert bench._GSM8K_FULL_PATH.is_file()
    assert bench._GSM8K_SUBSET_PATH.is_file()


def test_rebuild_hint_names_this_interpreter_not_a_repo_venv(tmp_path):
    hint = bench._rebuild_hint(tmp_path)
    assert sys.executable in hint
    assert f"{tmp_path}/.venv/bin/python" not in hint
