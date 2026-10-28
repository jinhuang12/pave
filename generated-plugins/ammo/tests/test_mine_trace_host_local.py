"""mine_trace.py finds the campaign through campaign_paths when the config omits artifact_dir.

Conventions §2: apply_env() at the top of main(). The mining config may leave
out artifact_dir; the campaign dir (AMMO_ARTIFACT_DIR) is then the base for
relative paths and for the default output dir.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent / "scripts"

_helpers_spec = importlib.util.spec_from_file_location(
    "_mine_trace_test_helpers", str(_TESTS_DIR / "test_mine_trace.py")
)
helpers = importlib.util.module_from_spec(_helpers_spec)
_helpers_spec.loader.exec_module(helpers)
mine_trace = helpers.mine_trace

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


def _campaign_with_trace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (engine, campaign, config path) with a valid one-trace mining input."""
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    profiling = campaign / "rounds" / "1" / "profiling"
    profiling.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    (campaign / "target.json").write_text("{}", encoding="utf-8")
    step = [(0, 1_000, "target_kernel", 0, 111, 1)]
    helpers.write_sqlite(profiling / "t.sqlite", helpers.transient_plus_step(step))
    sweep = helpers.sweep_json(decode_avg_s=1.0e-6 * 511, tpot_s=1.0e-6)
    (campaign / "e2e_latency_results.json").write_text(json.dumps(sweep), encoding="utf-8")
    config = {
        "round": 1,
        "arm": "baseline",
        "e2e_results": "e2e_latency_results.json",
        "segmentation": {"policy": "max_gap"},
        "traces": [{"bs": 32, "sqlite": "rounds/1/profiling/t.sqlite"}],
        "families": [{"label": "target", "symbol_patterns": ["target_kernel"]}],
    }
    config_path = campaign / "rounds" / "1" / "mine_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return engine, campaign, config_path


def test_config_without_artifact_dir_uses_the_campaign_dir(tmp_path, monkeypatch):
    engine, campaign, config_path = _campaign_with_trace(tmp_path)
    worktree = engine / ".claude" / "worktrees" / "track-a"
    worktree.mkdir(parents=True)
    monkeypatch.chdir(worktree)

    rc = mine_trace.main(["--config", str(config_path)])

    assert rc == 0
    mining = campaign / "rounds" / "1" / "mining"
    assert (mining / "mined.json").is_file()
    assert (mining / "tables.md").is_file()
    assert os.environ["AMMO_ARTIFACT_DIR"] == str(campaign)


def test_config_artifact_dir_wins_over_the_campaign(tmp_path, monkeypatch):
    engine, campaign, config_path = _campaign_with_trace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    for name in ("e2e_latency_results.json",):
        (other / name).write_bytes((campaign / name).read_bytes())
    (other / "rounds" / "1" / "profiling").mkdir(parents=True)
    (other / "rounds" / "1" / "profiling" / "t.sqlite").write_bytes(
        (campaign / "rounds" / "1" / "profiling" / "t.sqlite").read_bytes()
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["artifact_dir"] = str(other)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.chdir(engine)

    rc = mine_trace.main(["--config", str(config_path)])

    assert rc == 0
    assert (other / "rounds" / "1" / "mining" / "mined.json").is_file()
    assert not (campaign / "rounds" / "1" / "mining").exists()


def test_apply_env_is_the_first_statement_of_main():
    source = (_SCRIPTS_DIR / "mine_trace.py").read_text(encoding="utf-8")
    body = source.split("\ndef main(", 1)[1].split("\n", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == "campaign_paths.apply_env()"
