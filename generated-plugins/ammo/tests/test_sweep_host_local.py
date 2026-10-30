"""run_vllm_bench_latency_sweep.py resolves env and plugin files through campaign_paths.

The script stays shipped because verify_validation_gates.py and these tests
import it. Its changes are env and path resolution only (conventions §2).
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
import run_vllm_bench_latency_sweep as sweep  # noqa: E402

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


def test_main_fills_the_campaign_env_first(tmp_path, monkeypatch):
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    (campaign / "target.json").write_text(
        json.dumps({"target": {"tp": 2, "dp": 1}}), encoding="utf-8"
    )
    monkeypatch.chdir(engine)
    monkeypatch.setattr(sys, "argv", ["run_vllm_bench_latency_sweep.py", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        sweep.main()

    assert excinfo.value.code == 0
    assert os.environ["AMMO_ARTIFACT_DIR"] == str(campaign)
    assert os.environ["AMMO_SESSION_ID"] == "c1"
    assert os.environ["AMMO_GPU_RES_DIR"] == "/tmp/ammo_gpu_res"
    assert os.environ["AMMO_TP_SIZE"] == "2"


def test_apply_env_is_the_first_statement_of_main():
    source = (_SCRIPTS_DIR / "run_vllm_bench_latency_sweep.py").read_text(encoding="utf-8")
    body = source.split("\ndef main(", 1)[1].split("\n", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == "campaign_paths.apply_env()"


def test_gsm8k_data_resolves_through_plugin_path():
    assert sweep._GSM8K_FULL_PATH == campaign_paths.plugin_path("data", "gsm8k_full.json")
    assert sweep._GSM8K_SUBSET_PATH == campaign_paths.plugin_path("data", "gsm8k_subset.json")
    assert sweep._GSM8K_FULL_PATH.is_file()


def test_retired_gpu_lock_dir_warning_stays():
    source = (_SCRIPTS_DIR / "run_vllm_bench_latency_sweep.py").read_text(encoding="utf-8")
    assert "Old GPU lock files found at /tmp/ammo_gpu_locks/" in source
