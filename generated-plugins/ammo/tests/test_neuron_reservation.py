#!/usr/bin/env python3
"""Neuron binding tests for the backward-compatible AMMO reservation CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import gpu_reservation  # noqa: E402


@pytest.mark.parametrize(
    "visible,expected",
    [
        ("0-7", list(range(8))),
        ("32-39", list(range(32, 40))),
        ("12", [12]),
        ("12-12", [12]),
    ],
)
def test_neuron_visible_device_range_is_parsed_as_host_core_ids(monkeypatch, visible, expected):
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", visible)
    assert gpu_reservation._discover_session_neuron_cores() == expected


@pytest.mark.parametrize("visible", ["", "-1", "7-3", "0,2", "0-3,8-11", "abc"])
def test_neuron_visible_device_range_rejects_missing_or_non_contiguous_values(
    monkeypatch, visible
):
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", visible)
    assert gpu_reservation._discover_session_neuron_cores() == []


def test_neuron_runtime_visibility_is_a_legacy_standalone_fallback(monkeypatch):
    monkeypatch.delenv("NEURON_VISIBLE_DEVICES", raising=False)
    monkeypatch.setenv("NEURON_RT_VISIBLE_CORES", "24-31")

    assert gpu_reservation._discover_session_neuron_cores() == list(range(24, 32))


def test_neuron_visible_devices_wins_over_worker_local_runtime_visibility(monkeypatch):
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", "32-39")
    monkeypatch.setenv("NEURON_RT_VISIBLE_CORES", "35")

    assert gpu_reservation._discover_session_neuron_cores() == list(range(32, 40))


def test_single_neuron_core_uses_runtime_single_id_syntax(monkeypatch):
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-neuron")
    assert gpu_reservation._format_visibility([12]) == "12"


def test_neuron_binding_initializes_metadata_and_contiguous_pool(tmp_path, monkeypatch):
    state_dir = tmp_path / "neuron-res"
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-neuron")
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", "16-23")
    with mock.patch.object(gpu_reservation, "STATE_DIR", state_dir):
        state = gpu_reservation.read_state()

    assert state["accelerator_kind"] == "neuron"
    assert state["resource_unit"] == "neuron_core"
    assert state["resource_count"] == 8
    assert "gpus" not in state
    assert sorted(map(int, state["resources"])) == list(range(16, 24))


def test_neuron_reservations_are_disjoint_contiguous_and_owner_scoped(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "neuron-res"
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-neuron")
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", "32-47")
    with mock.patch.object(gpu_reservation, "STATE_DIR", state_dir):
        first = gpu_reservation.reserve(8, "agent-a", auto_release=False)
        second = gpu_reservation.reserve(8, "agent-b", auto_release=False)
        assert first == list(range(32, 40))
        assert second == list(range(40, 48))
        assert not set(first) & set(second)
        assert gpu_reservation.release_by_session("agent-a") == first
        state = gpu_reservation.read_state()

    assert all(state["resources"][str(core)] is None for core in first)
    assert all(
        state["resources"][str(core)]["session_id"] == "agent-b"
        for core in second
    )


def test_neuron_cli_prints_runtime_visible_contiguous_range(tmp_path):
    script = _SCRIPTS_DIR / "gpu_reservation.py"
    env = os.environ.copy()
    env.update(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "AMMO_GPU_RES_DIR": str(tmp_path / "neuron-res"),
            "AMMO_SESSION_ID": "session-neuron",
            "NEURON_VISIBLE_DEVICES": "48-63",
            "NEURON_LOGICAL_NC_CONFIG": "2",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "reserve",
            "--num-resources",
            "8",
            "--no-auto-release",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "48-55"
    state = json.loads((tmp_path / "neuron-res" / "state.json").read_text())
    assert state["accelerator_kind"] == "neuron"
    assert state["lnc"] == 2
    assert all(
        state["resources"][str(core)]["session_id"] == "session-neuron"
        for core in range(48, 56)
    )


def test_neuron_cli_accepts_legacy_num_gpus_flag(tmp_path):
    script = _SCRIPTS_DIR / "gpu_reservation.py"
    env = os.environ.copy()
    env.update(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "AMMO_GPU_RES_DIR": str(tmp_path / "neuron-res"),
            "AMMO_SESSION_ID": "legacy-client",
            "NEURON_VISIBLE_DEVICES": "16-23",
        }
    )

    result = subprocess.run(
        [sys.executable, str(script), "reserve", "--num-gpus", "4"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "16-19"


def test_neuron_cli_release_handles_one_owner_with_disjoint_holds(tmp_path):
    script = _SCRIPTS_DIR / "gpu_reservation.py"
    env = os.environ.copy()
    env.update(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "AMMO_GPU_RES_DIR": str(tmp_path / "neuron-res"),
            "NEURON_VISIBLE_DEVICES": "0-3",
        }
    )

    def reserve(session_id: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(script),
                "reserve",
                "--num-resources",
                "1",
                "--session-id",
                session_id,
                "--no-auto-release",
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env=env,
        )

    assert reserve("owner-a").returncode == 0
    assert reserve("owner-b").returncode == 0
    assert reserve("owner-a").returncode == 0

    released = subprocess.run(
        [
            sys.executable,
            str(script),
            "release-session",
            "--session-id",
            "owner-a",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=env,
    )

    assert released.returncode == 0, released.stderr
    assert "Released logical NeuronCores: 0,2" in released.stderr
    state = json.loads((tmp_path / "neuron-res" / "state.json").read_text())
    assert state["resources"]["0"] is None
    assert state["resources"]["1"]["session_id"] == "owner-b"
    assert state["resources"]["2"] is None


def test_cuda_cli_output_and_state_shape_remain_compatible(tmp_path):
    script = _SCRIPTS_DIR / "gpu_reservation.py"
    env = os.environ.copy()
    env.pop("AMMO_RUNTIME_PROFILE", None)
    env.pop("AMMO_ACCELERATOR_KIND", None)
    env.pop("NEURON_VISIBLE_DEVICES", None)
    env.pop("NEURON_RT_VISIBLE_CORES", None)
    env.update(
        {
            "AMMO_GPU_RES_DIR": str(tmp_path / "cuda-res"),
            "AMMO_SESSION_ID": "session-cuda",
            "CUDA_VISIBLE_DEVICES": "4,5,6,7",
        }
    )
    result = subprocess.run(
        [sys.executable, str(script), "reserve", "--num-gpus", "2"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "4,5"
    state = json.loads((tmp_path / "cuda-res" / "state.json").read_text())
    assert state["gpu_count"] == 4
    assert sorted(map(int, state["gpus"])) == [4, 5, 6, 7]
