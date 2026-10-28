#!/usr/bin/env python3
"""Campaign scaffold compatibility for CUDA and vLLM-Neuron."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator


_SKILL_ROOT = Path(__file__).resolve().parent.parent
_NEW_TARGET = _SKILL_ROOT / "scripts" / "new_target.py"
_SCHEMA = Path(__file__).resolve().parents[3] / "schemas" / "state.schema.json"
if str(_NEW_TARGET.parent) not in sys.path:
    sys.path.insert(0, str(_NEW_TARGET.parent))
_spec = importlib.util.spec_from_file_location("new_target_runtime", str(_NEW_TARGET))
new_target = importlib.util.module_from_spec(_spec)
sys.modules["new_target_runtime"] = new_target
_spec.loader.exec_module(new_target)


def _fields():
    return new_target.TargetFields(
        model_id="model", hardware="accelerator", dtype="bf16", tp=8, ep=1,
        max_model_len=4096, input_len=64, output_len=128, batch_sizes=[1],
        num_iters=10, noise_tolerance_pct=1.0,
        catastrophic_regression_pct=5.0,
    )


def _validator():
    return Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def test_legacy_environment_defaults_to_cuda_and_keeps_gpu_resources(monkeypatch, tmp_path):
    monkeypatch.delenv("AMMO_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")

    state = new_target._state_json(_fields(), tmp_path, gpu_model="B200")

    assert state["runtime"]["runtime_profile"] == "vllm-cuda"
    assert state["runtime"]["accelerator_kind"] == "cuda"
    assert state["runtime"]["resource_unit"] == "gpu"
    assert state["runtime"]["resource_count"] == 2
    assert state["runtime"]["resource_ids"] == [2, 3]
    assert state["runtime"]["lnc"] is None
    assert state["runtime"]["stack_fingerprint"]["digest"].startswith("sha256:")
    assert state["accelerator_resources"] == {
        "accelerator_kind": "cuda",
        "resource_unit": "gpu",
        "resource_count": 2,
        "resource_ids": [2, 3],
        "visible_resources": "2,3",
    }
    assert state["gpu_resources"]["gpu_count"] == 2
    assert state["gpu_resources"]["cuda_visible_devices"] == "2,3"
    assert list(_validator().iter_errors(state)) == []


def test_neuron_environment_records_logical_cores_without_fake_gpu_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-neuron")
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", "8-15")
    monkeypatch.setenv("NEURON_LOGICAL_NC_CONFIG", "2")
    monkeypatch.setenv("AMMO_INSTANCE_FAMILY", "trn2")
    monkeypatch.setenv(
        "AMMO_STACK_FINGERPRINT",
        json.dumps({"vllm": "0.24.0", "neuron_sdk": "2.32"}),
    )

    state = new_target._state_json(_fields(), tmp_path)

    runtime = state["runtime"]
    assert runtime["runtime_profile"] == "vllm-neuron"
    assert runtime["accelerator_kind"] == "neuron"
    assert runtime["resource_unit"] == "neuron_core"
    assert runtime["resource_count"] == 8
    assert runtime["resource_ids"] == list(range(8, 16))
    assert runtime["instance_family"] == "trn2"
    assert runtime["lnc"] == 2
    assert runtime["stack_fingerprint"]["server_stack_fingerprint"] == {
        "vllm": "0.24.0", "neuron_sdk": "2.32"
    }
    assert state["accelerator_resources"] == {
        "accelerator_kind": "neuron",
        "resource_unit": "neuron_core",
        "resource_count": 8,
        "resource_ids": list(range(8, 16)),
        "visible_resources": "8-15",
    }
    assert "gpu_resources" not in state
    assert list(_validator().iter_errors(state)) == []


def test_legacy_gpu_only_state_still_validates(monkeypatch, tmp_path):
    monkeypatch.delenv("AMMO_RUNTIME_PROFILE", raising=False)
    state = new_target._state_json(_fields(), tmp_path, gpu_model="B200")
    state.pop("runtime")
    state.pop("accelerator_resources")

    assert list(_validator().iter_errors(state)) == []


def test_unknown_runtime_profile_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-tpu")

    try:
        new_target._state_json(_fields(), tmp_path)
    except SystemExit as exc:
        assert "AMMO_RUNTIME_PROFILE" in str(exc)
    else:
        raise AssertionError("unknown runtime profile was accepted")


def test_schema_rejects_cross_bound_neuron_runtime_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "vllm-neuron")
    monkeypatch.setenv("NEURON_VISIBLE_DEVICES", "0-7")
    state = new_target._state_json(_fields(), tmp_path)

    mutations = []
    wrong_runtime_kind = deepcopy(state)
    wrong_runtime_kind["runtime"]["accelerator_kind"] = "cuda"
    mutations.append(wrong_runtime_kind)
    wrong_runtime_unit = deepcopy(state)
    wrong_runtime_unit["runtime"]["resource_unit"] = "gpu"
    mutations.append(wrong_runtime_unit)
    wrong_generic_kind = deepcopy(state)
    wrong_generic_kind["accelerator_resources"]["accelerator_kind"] = "cuda"
    mutations.append(wrong_generic_kind)
    fake_gpu = deepcopy(state)
    fake_gpu["gpu_resources"] = {
        "gpu_count": 8,
        "gpu_model": "fake",
        "memory_total_gib": 0,
        "cuda_visible_devices": "0,1,2,3,4,5,6,7",
    }
    mutations.append(fake_gpu)

    for invalid in mutations:
        assert list(_validator().iter_errors(invalid)), invalid


def test_schema_rejects_cross_bound_cuda_runtime_fields(monkeypatch, tmp_path):
    monkeypatch.delenv("AMMO_RUNTIME_PROFILE", raising=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0,1")
    state = new_target._state_json(_fields(), tmp_path, gpu_model="B200")

    mutations = []
    wrong_runtime_kind = deepcopy(state)
    wrong_runtime_kind["runtime"]["accelerator_kind"] = "neuron"
    mutations.append(wrong_runtime_kind)
    wrong_runtime_unit = deepcopy(state)
    wrong_runtime_unit["runtime"]["resource_unit"] = "neuron_core"
    mutations.append(wrong_runtime_unit)
    wrong_generic_unit = deepcopy(state)
    wrong_generic_unit["accelerator_resources"]["resource_unit"] = "neuron_core"
    mutations.append(wrong_generic_unit)
    missing_gpu = deepcopy(state)
    missing_gpu.pop("gpu_resources")
    mutations.append(missing_gpu)

    for invalid in mutations:
        assert list(_validator().iter_errors(invalid)), invalid


def test_target_json_baseline_env_defaults_empty_and_carries_fields(tmp_path):
    empty = new_target._target_json(_fields(), tmp_path, "pumice-cuda")
    assert empty["bench"]["baseline_env"] == {}
    fields = replace(
        _fields(), baseline_env={"PUMICE_RUST_EMIT_FULL_BLOCK_TABLES": "1"}
    )
    target = new_target._target_json(fields, tmp_path, "pumice-neuron")
    assert target["bench"]["baseline_env"] == {
        "PUMICE_RUST_EMIT_FULL_BLOCK_TABLES": "1"
    }
    assert target["bench"]["opt_env"] == {"<ENABLE_FLAG>": "1"}


def test_neuron_baseline_env_default_and_caller_value(capsys):
    assert new_target._neuron_baseline_env({}) == {
        "PUMICE_RUST_EMIT_FULL_BLOCK_TABLES": "1"
    }
    assert "WARNING" not in capsys.readouterr().err
    assert new_target._neuron_baseline_env(
        {"PUMICE_RUST_EMIT_FULL_BLOCK_TABLES": "0"}
    ) == {"PUMICE_RUST_EMIT_FULL_BLOCK_TABLES": "0"}
    assert 'Neuron needs "1"' in capsys.readouterr().err
