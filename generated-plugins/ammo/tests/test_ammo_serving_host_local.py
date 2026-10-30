"""Host-local P/D serving: campaign env, the host-wide lease table, the wheel row.

CPU-only. The deployment reservation runs against the real gpu_reservation
module with its pool discovery patched, so no GPU is touched.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from test_ammo_serving import source, spec  # noqa: F401  (fixtures)
import gpu_reservation
import run_serving_sweep
import serving_benchmark_adapter as adapter
from deployment_ops import reserve_deployment
from serving_contract import ContractError, ServingSpec

# Built from parts: pin literals live only in the pin files (tests/test_prose_bans.py).
PINNED_VLLM = "0." "32.0"


def test_wheel_table_qualifies_the_pinned_vllm_release():
    serve_blob, request_blob = adapter.WHEEL_BLOBS[PINNED_VLLM]
    assert re.fullmatch(r"[0-9a-f]{40}", serve_blob)
    assert re.fullmatch(r"[0-9a-f]{40}", request_blob)
    assert serve_blob != request_blob


def test_verify_wheel_still_rejects_an_unqualified_release(tmp_path):
    root = tmp_path / "site-packages"
    files = {name: root / rel for name, rel in (
        ("serve", "vllm/benchmarks/serve.py"),
        ("request", "vllm/benchmarks/lib/endpoint_request_func.py"),
        ("vllm", "vllm/__init__.py"))}
    for path in files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    mods = {name: SimpleNamespace(__file__=str(path)) for name, path in files.items()}
    mods["vllm"].__version__ = "9.9.9"
    with pytest.raises(ContractError, match="unsupported benchmark wheel version"):
        adapter.verify_wheel(mods["serve"], mods["request"], mods["vllm"], root)


@pytest.fixture
def host_table(tmp_path, monkeypatch):
    """Real lease table in a temp dir; the host pool is GPUs 0-3."""
    state_dir = tmp_path / "res"
    monkeypatch.setattr(gpu_reservation, "STATE_DIR", state_dir)
    monkeypatch.setattr(gpu_reservation, "_discover_session_gpus", lambda: [0, 1, 2, 3])
    monkeypatch.setitem(__import__("sys").modules, "gpu_reservation", gpu_reservation)
    for name in ("CUDA_VISIBLE_DEVICES", "AMMO_RUNTIME_PROFILE", "AMMO_ACCELERATOR_KIND",
                 "NEURON_VISIBLE_DEVICES", "NEURON_RT_VISIBLE_CORES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AMMO_SESSION_ID", "20261028-000000-abc123")
    return state_dir


def _pumice(spec):  # noqa: F811
    data = spec.model_dump()
    data["runtime_profile"] = "pumice-cuda"
    return ServingSpec.model_validate(data)


def test_deployment_defaults_to_the_campaign_profile_and_shared_table(spec, host_table):  # noqa: F811
    owner = "20261028-000000-abc123:pd:unique"
    with reserve_deployment(_pumice(spec), owner):
        state = json.loads((host_table / "state.json").read_text())
        held = {k: v for k, v in state["gpus"].items() if v}
        assert sorted(held) == ["0", "1"]
        assert {v["session_id"] for v in held.values()} == {owner}
        assert {v["release_policy"] for v in held.values()} == {"confirmed_cleanup"}
    state = json.loads((host_table / "state.json").read_text())
    assert not any(state["gpus"].values())


def test_deployment_refuses_gpus_the_caller_cannot_see(spec, host_table, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")  # workers need GPUs 0 and 1
    with pytest.raises(ContractError, match="outside its session"):
        with reserve_deployment(_pumice(spec), "x:pd:unique"):
            pytest.fail("a GPU outside CUDA_VISIBLE_DEVICES must not be reserved")
    assert not (host_table / "state.json").exists()


def test_deployment_needs_a_campaign_id(spec, host_table, monkeypatch):  # noqa: F811
    monkeypatch.delenv("AMMO_SESSION_ID")
    with pytest.raises(ContractError, match="campaign"):
        with reserve_deployment(_pumice(spec), "x:pd:unique"):
            pytest.fail("no campaign id, no reservation")


def test_sweep_main_fills_the_campaign_env_from_the_marker(tmp_path, monkeypatch):
    cid = "20261028-120000-def456"
    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / cid
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text(cid + "\n")
    monkeypatch.chdir(engine)
    with mock.patch.dict(os.environ):
        for name in ("AMMO_SESSION_ID", "AMMO_ARTIFACT_DIR", "AMMO_RUNTIME_PROFILE", "AMMO_GPU_RES_DIR"):
            os.environ.pop(name, None)
        with pytest.raises(ContractError, match="target.json"):
            run_serving_sweep.main(["--artifact-dir", str(campaign), "--kind", "verify"])
        assert os.environ["AMMO_SESSION_ID"] == cid
        assert os.environ["AMMO_RUNTIME_PROFILE"] == "pumice-cuda"
        assert os.environ["AMMO_GPU_RES_DIR"] == "/tmp/ammo_gpu_res"
