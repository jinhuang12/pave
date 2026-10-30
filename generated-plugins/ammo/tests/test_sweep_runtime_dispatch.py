#!/usr/bin/env python3
"""Focused CPU tests for the sweep runtime-dispatch seam."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def test_accelerator_profile_routes_to_existing_cuda_profiler():
    from run_vllm_bench_latency_sweep import _resolve_profile_route
    from runtime_ops import resolve_runtime

    route = _resolve_profile_route(
        resolve_runtime({}), accelerator_profile=True,
        nsys_profile=False, neuron_profile=False,
    )

    assert route == (True, False)


def test_accelerator_profile_routes_to_neuron_profiler():
    from run_vllm_bench_latency_sweep import _resolve_profile_route
    from runtime_ops import resolve_runtime

    route = _resolve_profile_route(
        resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}),
        accelerator_profile=True, nsys_profile=False, neuron_profile=False,
    )

    assert route == (False, True)


@pytest.mark.parametrize(
    "env,nsys_profile,neuron_profile,match",
    [
        ({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}, True, False, "--nsys-profile"),
        ({}, False, True, "--neuron-profile"),
    ],
)
def test_explicit_backend_profiler_mismatch_fails_closed(
    env, nsys_profile, neuron_profile, match
):
    from run_vllm_bench_latency_sweep import _resolve_profile_route
    from runtime_ops import resolve_runtime

    with pytest.raises(SystemExit, match=match):
        _resolve_profile_route(
            resolve_runtime(env), accelerator_profile=False,
            nsys_profile=nsys_profile, neuron_profile=neuron_profile,
        )


def test_activation_artifact_is_backend_neutral_and_non_timing(tmp_path):
    from run_vllm_bench_latency_sweep import _write_activation_proof
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})
    raw_profile = tmp_path / "profiling" / "neuron" / "opt" / "profile.ntff"
    raw_profile.parent.mkdir(parents=True)
    raw_profile.write_bytes(b"neuron-profile")
    path = _write_activation_proof(
        out_root=tmp_path,
        runtime=runtime,
        profiler_active=True,
        stack_fingerprint={"digest": "sha256:abc"},
        arms={
            "opt": {
                "fastpath_evidence": {
                    "status": "pass", "hits": ["custom_kernel active"]
                },
                "runtime_activation": {
                    "status": "pass", "hits": 4,
                    "checks": {"plugin_execution": True},
                },
                "raw_artifacts": [str(raw_profile.relative_to(tmp_path))],
            }
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path == tmp_path / "activation_proof.json"
    assert payload["schema"] == "ammo_activation_proof/1"
    assert payload["runtime_profile"] == "vllm-neuron"
    assert payload["accelerator_kind"] == "neuron"
    assert payload["profiler_route"] == "neuron-profiler"
    assert payload["timing_admissible"] is False
    assert payload["status"] == "PASS"
    assert payload["hits"] >= 1
    primary = payload["arms"]["opt"]["primary_artifacts"]
    assert len(primary) == 1
    assert primary[0]["kind"] == "neuron-profile"
    assert primary[0]["path"] == str(raw_profile.relative_to(tmp_path))
    assert primary[0]["size_bytes"] == len(b"neuron-profile")
    assert primary[0]["sha256"].startswith("sha256:")


def test_neuron_activation_artifact_cannot_pass_without_primary_artifact(tmp_path):
    from run_vllm_bench_latency_sweep import _write_activation_proof
    from runtime_ops import resolve_runtime

    path = _write_activation_proof(
        out_root=tmp_path,
        runtime=resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}),
        profiler_active=True,
        stack_fingerprint={"digest": "sha256:abc"},
        arms={
            "opt": {
                "fastpath_evidence": {"status": "pass", "hits": 1},
                "runtime_activation": {"status": "pass", "hits": 4},
                "raw_artifacts": ["logs/only-a-text-log.log"],
            }
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] != "PASS"
    assert payload["arms"]["opt"]["primary_artifacts"] == []


def test_neuron_activation_rejects_arbitrary_non_profile_file(tmp_path):
    from run_vllm_bench_latency_sweep import _write_activation_proof
    from runtime_ops import resolve_runtime

    arbitrary = tmp_path / "profiling" / "neuron" / "opt" / "notes.json"
    arbitrary.parent.mkdir(parents=True)
    arbitrary.write_text('{"looks": "real"}', encoding="utf-8")

    path = _write_activation_proof(
        out_root=tmp_path,
        runtime=resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}),
        profiler_active=True,
        stack_fingerprint={"digest": "sha256:abc"},
        arms={
            "opt": {
                "fastpath_evidence": {"status": "pass", "hits": 1},
                "runtime_activation": {"status": "pass", "hits": 4},
                "raw_artifacts": [str(arbitrary.relative_to(tmp_path))],
            }
        },
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "FAIL"
    assert payload["arms"]["opt"]["primary_artifacts"] == []


def test_v2_neuron_profile_outside_sweep_dir_is_recorded_absolute(tmp_path):
    from run_vllm_bench_latency_sweep import (
        _primary_artifact_records,
        _v2_profiling_dir,
    )

    out_root = (
        tmp_path / "rounds" / "1" / "sweeps" / "opt_profiling" / "op_test"
    )
    out_root.mkdir(parents=True)
    profile = _v2_profiling_dir(out_root, "neuron") / "candidate" / "trace.ntff"
    profile.parent.mkdir(parents=True)
    profile.write_bytes(b"trace")

    records = _primary_artifact_records(
        out_root, {"raw_artifacts": [str(profile.resolve())]}
    )

    assert records[0]["path"] == str(profile.resolve())
    assert records[0]["kind"] == "neuron-profile"


def test_activation_artifact_is_not_written_for_clean_timing(tmp_path):
    from run_vllm_bench_latency_sweep import _write_activation_proof
    from runtime_ops import resolve_runtime

    path = _write_activation_proof(
        out_root=tmp_path,
        runtime=resolve_runtime({}),
        profiler_active=False,
        stack_fingerprint={"digest": "sha256:abc"},
        arms={},
    )

    assert path is None
    assert not (tmp_path / "activation_proof.json").exists()


def test_parent_injects_byte_identical_model_binding_into_both_arm_envs():
    from run_vllm_bench_latency_sweep import (
        MODEL_LOAD_BINDING_ENV,
        _inject_model_load_binding,
    )

    raw = '{"schema":"ammo_model_load_binding/1","value":"same"}'
    baseline = _inject_model_load_binding({"ARM": "baseline"}, raw)
    opt = _inject_model_load_binding({"ARM": "opt"}, raw)

    assert baseline[MODEL_LOAD_BINDING_ENV] == raw
    assert opt[MODEL_LOAD_BINDING_ENV] == raw
    assert baseline[MODEL_LOAD_BINDING_ENV] is opt[MODEL_LOAD_BINDING_ENV]


def test_cuda_parent_scrubs_stale_neuron_model_binding():
    from run_vllm_bench_latency_sweep import (
        MODEL_LOAD_BINDING_ENV,
        _inject_model_load_binding,
    )

    env = {"KEEP": "1", MODEL_LOAD_BINDING_ENV: "stale-neuron-binding"}

    assert _inject_model_load_binding(env, None) == {"KEEP": "1"}
    assert env[MODEL_LOAD_BINDING_ENV] == "stale-neuron-binding"


def test_child_applies_local_load_target_only_to_engine_args():
    from types import SimpleNamespace

    from run_vllm_bench_latency_sweep import (
        _apply_model_load_binding_to_engine_args,
    )

    requested = "zai-org/GLM-5.2-FP8"
    engine_args = SimpleNamespace(
        model=requested,
        revision="mutable-ref",
        tokenizer=requested,
        tokenizer_revision="mutable-ref",
    )
    binding = {
        "requested_model_id": requested,
        "load_target": "/cache/snapshots/" + "a" * 40,
        "requires_local_checkpoint": True,
        "model_snapshot": {"resolved_revision": "a" * 40},
    }

    _apply_model_load_binding_to_engine_args(engine_args, binding)

    assert engine_args.model == binding["load_target"]
    assert engine_args.revision is None
    assert engine_args.tokenizer == binding["load_target"]
    assert engine_args.tokenizer_revision is None
    assert binding["requested_model_id"] == requested


def test_child_pins_non_adapter_model_revision_without_replacing_model_id():
    from types import SimpleNamespace

    from run_vllm_bench_latency_sweep import (
        _apply_model_load_binding_to_engine_args,
    )

    commit = "f" * 40
    engine_args = SimpleNamespace(
        model="openai/gpt-oss-20b",
        revision=None,
        tokenizer=None,
        tokenizer_revision=None,
    )
    binding = {
        "requested_model_id": "openai/gpt-oss-20b",
        "load_target": "openai/gpt-oss-20b",
        "requires_local_checkpoint": False,
        "model_snapshot": {"resolved_revision": commit},
    }

    _apply_model_load_binding_to_engine_args(engine_args, binding)

    assert engine_args.model == "openai/gpt-oss-20b"
    assert engine_args.revision == commit
    assert engine_args.tokenizer is None
    assert engine_args.tokenizer_revision == commit
