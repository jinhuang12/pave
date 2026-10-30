#!/usr/bin/env python3
"""Focused tests for AMMO accelerator runtime dispatch.

The runtime adapter is an additive seam. These tests keep the existing CUDA
behavior exact while exercising the Neuron-only paths without importing torch,
torch-xla, vLLM, or Neuron packages on the CPU test host.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


#: Every name campaign_paths.campaign_env() can derive (references/campaign-env.md §1).
_CAMPAIGN_ENV_NAMES = (
    "AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR",
    "AMMO_PYTHON", "CARGO_TARGET_DIR", "AMMO_GPU_RES_DIR", "AMMO_RUNTIME_PROFILE",
    "AMMO_ACCELERATOR_KIND", "AMMO_TP_SIZE", "AMMO_DP_SIZE", "AMMO_STACK_FINGERPRINT",
    "AMMO_INSTANCE_FAMILY", "AMMO_ACCELERATOR_NAME", "AMMO_SERVING_SETUP",
    "AMMO_SERVING_MANIFEST", "AMMO_SERVING_MANIFEST_SHA256",
)


def _vendored_instruction_schema() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent
            / "ai_cli_session"
            / "_neuron_shared"
            / "skills"
            / "neuron-nki-profile-querying"
            / "references"
            / "schema"
            / "Instruction.yaml"
        )
        if candidate.is_file():
            return candidate
    raise AssertionError("vendored Neuron Explorer Instruction.yaml not found")


def test_runtime_defaults_to_cuda_for_legacy_sessions():
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({})

    assert runtime.runtime_profile == "vllm-cuda"
    assert runtime.accelerator_kind == "cuda"
    assert runtime.profiler_route == "nsys"


def test_runtime_selects_neuron_from_immutable_server_profile():
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})

    assert runtime.runtime_profile == "vllm-neuron"
    assert runtime.accelerator_kind == "neuron"
    assert runtime.profiler_route == "neuron-profiler"


def test_unknown_runtime_profile_fails_closed():
    from runtime_ops import resolve_runtime

    with pytest.raises(ValueError, match="AMMO_RUNTIME_PROFILE"):
        resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-tpu"})


def test_neuron_local_checkpoint_adapter_is_source_and_architecture_scoped():
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})
    source_commit = "41758812f2b30ca7b43ec6c2a1137c927f53c7a1"

    assert runtime.requires_local_checkpoint(
        source_commit, ["GlmMoeDsaForCausalLM"]
    ) is True
    assert runtime.requires_local_checkpoint(
        source_commit, ["GptOssForCausalLM"]
    ) is False
    assert runtime.requires_local_checkpoint(
        "d9926310ef01d7808dbb4b398abc69a816cb9474",
        ["GlmMoeDsaForCausalLM"],
    ) is False


def test_cuda_local_checkpoint_adapter_is_no_op_for_legacy_sources():
    from runtime_ops import resolve_runtime

    assert resolve_runtime({}).requires_local_checkpoint(
        "not-a-pinned-commit", ["GlmMoeDsaForCausalLM"]
    ) is False


@pytest.mark.parametrize("source_commit", ["", "41758812", "g" * 40])
def test_neuron_local_checkpoint_adapter_rejects_unpinned_source(source_commit):
    from runtime_ops import resolve_runtime

    with pytest.raises(ValueError, match="40-character"):
        resolve_runtime(
            {"AMMO_RUNTIME_PROFILE": "vllm-neuron"}
        ).requires_local_checkpoint(source_commit, ["GlmMoeDsaForCausalLM"])


def test_cuda_compile_cache_environment_is_byte_compatible(tmp_path):
    from runtime_ops import resolve_runtime

    source = {"KEEP": "1"}
    root = tmp_path / "cache"
    out = resolve_runtime({}).compile_cache_env(source, root)

    assert out == {
        "KEEP": "1",
        "VLLM_CACHE_ROOT": str(root),
        "TRITON_CACHE_DIR": str(root / "triton_cache"),
    }
    assert source == {"KEEP": "1"}


def test_neuron_compile_cache_environment_maps_vllm_root_to_neff_cache(tmp_path):
    from runtime_ops import resolve_runtime

    source = {"KEEP": "1", "TRITON_CACHE_DIR": "/stale/cuda/cache"}
    root = tmp_path / "cache"
    out = resolve_runtime(
        {"AMMO_RUNTIME_PROFILE": "vllm-neuron"}
    ).compile_cache_env(source, root)

    assert out["VLLM_CACHE_ROOT"] == str(root)
    assert out["NEURON_COMPILE_CACHE_URL"] == str(root / "neuron" / "compile_cache")
    assert "TRITON_CACHE_DIR" not in out
    assert source["TRITON_CACHE_DIR"] == "/stale/cuda/cache"


class _FakeCuda:
    def __init__(self):
        self.sync_calls = 0

    def synchronize(self):
        self.sync_calls += 1

    def is_available(self):
        return True

    def get_device_name(self, index):
        assert index == 0
        return "B200"


class _FakeTorch:
    int32 = "int32"

    def __init__(self):
        self.cuda = _FakeCuda()
        self.tensor_calls = []

    def tensor(self, value, *, dtype, device):
        self.tensor_calls.append((value, dtype, device))
        return {"value": value, "dtype": dtype, "device": device}


def test_cuda_sync_control_tensor_and_identity_preserve_existing_calls():
    from runtime_ops import resolve_runtime

    torch = _FakeTorch()
    runtime = resolve_runtime({})

    runtime.synchronize(torch)
    tensor = runtime.control_tensor(torch, [0], dtype=torch.int32)

    assert torch.cuda.sync_calls == 1
    assert tensor["device"] == "cuda"
    assert runtime.device_identity(torch)["name"] == "B200"


def test_neuron_sync_and_control_tensor_use_torch_xla_adapter(monkeypatch):
    from runtime_ops import resolve_runtime

    calls = []
    fake_xm = SimpleNamespace(
        mark_step=lambda: calls.append("mark_step"),
        wait_device_ops=lambda: calls.append("wait_device_ops"),
        xla_device=lambda: "xla:0",
        get_xla_supported_devices=lambda: ["xla:0", "xla:1"],
    )
    monkeypatch.setitem(
        sys.modules, "torch_xla.core.xla_model", fake_xm
    )
    # Python imports parents before the leaf module.
    monkeypatch.setitem(sys.modules, "torch_xla", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "torch_xla.core", SimpleNamespace())

    torch = _FakeTorch()
    runtime = resolve_runtime(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "NEURON_VISIBLE_DEVICES": "8-15",
            "NEURON_LOGICAL_NC_CONFIG": "2",
        }
    )
    runtime.synchronize(torch)
    tensor = runtime.control_tensor(torch, [0], dtype=torch.int32)
    identity = runtime.device_identity(torch)

    assert calls == ["mark_step", "wait_device_ops"]
    assert tensor["device"] == "xla:0"
    assert identity["visible_cores"] == "8-15"
    assert identity["lnc"] == "2"
    assert identity["xla_devices"] == ["xla:0", "xla:1"]


def test_neuron_engine_profiler_configuration_is_additive(tmp_path):
    from runtime_ops import resolve_runtime

    engine_args = {
        "additional_config": {"neuron_config": {"foo": "bar"}},
        "profiler_config": {"delay_iterations": 2},
    }
    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})

    configured = runtime.configure_engine_profiling(engine_args, tmp_path)

    assert configured["additional_config"]["neuron_config"] == {"foo": "bar"}
    profiler = configured["additional_config"]["neuron_profiler"]
    assert profiler["output_dir"] == str(tmp_path)
    assert profiler["activities"] == ["device_profile", "system_profile"]
    assert configured["profiler_config"] == {
        "delay_iterations": 2,
        "profiler": "cuda",
    }
    assert "neuron_profiler" not in engine_args["additional_config"]


def test_neuron_activation_proof_requires_plugin_neff_cache_and_no_fallback(tmp_path):
    from runtime_ops import resolve_runtime

    cache = tmp_path / "cache" / "neuron" / "compile_cache"
    cache.mkdir(parents=True)
    (cache / "decode_bucket.neff").write_bytes(b"neff")
    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})
    text = (
        "INFO vllm_neuron platform plugin activated\n"
        "INFO compiling decode bucket to NEFF\n"
        "INFO compile cache hit for decode bucket\n"
    )

    proof = runtime.activation_proof(text, tmp_path / "cache")

    assert proof["status"] == "pass"
    assert proof["hits"] >= 4
    assert proof["checks"] == {
        "plugin_execution": True,
        "neff_or_bucket_compilation": True,
        "compile_cache_use": True,
        "compiled_artifact_present": True,
        "fallback_absent": True,
    }


def test_neuron_activation_proof_fails_when_fallback_is_logged(tmp_path):
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})
    proof = runtime.activation_proof(
        "vllm_neuron plugin activated; falling back to CPU eager execution",
        tmp_path,
    )

    assert proof["status"] == "fail"
    assert proof["checks"]["fallback_absent"] is False


def test_neuron_activation_signals_do_not_claim_primary_artifact(tmp_path):
    from runtime_ops import resolve_runtime

    runtime = resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})
    proof = runtime.activation_proof(
        "vllm_neuron plugin activated; compiled decode bucket to NEFF; "
        "compile cache hit",
        tmp_path,
    )

    assert proof["status"] == "pass"
    assert proof["checks"]["compiled_artifact_present"] is False
    assert proof["compiled_artifact_sample"] is None


def test_runtime_fingerprint_is_stable_and_excludes_secrets(monkeypatch, tmp_path):
    import runtime_ops

    source = tmp_path / "plugin"
    source.mkdir()
    monkeypatch.setattr(runtime_ops, "_package_version", lambda name: {"vllm": "0.24.0"}.get(name))
    monkeypatch.setattr(runtime_ops, "_git_fingerprint", lambda path: {"commit": "abc", "dirty": False})
    env = {
        "AMMO_RUNTIME_PROFILE": "vllm-neuron",
        "AMMO_RUNTIME_SOURCE_DIR": str(source),
        "HF_TOKEN": "secret",
    }
    runtime = runtime_ops.resolve_runtime(env)

    first = runtime.stack_fingerprint(env)
    second = runtime.stack_fingerprint(env)

    assert first == second
    assert first["digest"].startswith("sha256:")
    assert first["packages"]["vllm"] == "0.24.0"
    assert first["sources"][str(source)]["commit"] == "abc"
    assert "secret" not in json.dumps(first)


def test_runtime_fingerprint_includes_pinned_stack_and_source_env(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(runtime_ops, "_package_version", lambda name: None)
    env = {
        "AMMO_RUNTIME_PROFILE": "vllm-neuron",
        "AMMO_STACK_FINGERPRINT": json.dumps(
            {"vllm": "0.24.0", "neuron_sdk": "2.32"}
        ),
        "AMMO_RUNTIME_SOURCE_COMMIT": "a" * 40,
    }

    fingerprint = runtime_ops.resolve_runtime(env).stack_fingerprint(env)

    assert fingerprint["server_stack_fingerprint"] == {
        "vllm": "0.24.0", "neuron_sdk": "2.32"
    }
    assert fingerprint["source_environment"] == {
        "AMMO_RUNTIME_SOURCE_COMMIT": "a" * 40
    }


def test_neuron_runtime_fingerprint_records_plugin_parent_visibility(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(runtime_ops, "_package_version", lambda name: None)
    env = {
        "AMMO_RUNTIME_PROFILE": "vllm-neuron",
        "NEURON_VISIBLE_DEVICES": "8-15",
    }

    fingerprint = runtime_ops.resolve_runtime(env).stack_fingerprint(env)

    assert fingerprint["environment"]["NEURON_VISIBLE_DEVICES"] == "8-15"
    assert "NEURON_RT_VISIBLE_CORES" not in fingerprint["environment"]


def test_runtime_fingerprint_records_actual_cwd_and_imported_vllm_source(
    monkeypatch, tmp_path
):
    import runtime_ops

    active = tmp_path / "active-op-worktree"
    active.mkdir()
    installed = tmp_path / "installed" / "vllm" / "__init__.py"
    installed.parent.mkdir(parents=True)
    installed.write_text("", encoding="utf-8")
    monkeypatch.chdir(active)
    monkeypatch.setattr(runtime_ops, "_package_version", lambda name: None)
    monkeypatch.setattr(
        runtime_ops,
        "_git_fingerprint",
        lambda path: {
            "commit": "cwd" if path == active.resolve() else "module",
            "branch": "op",
            "dirty": False,
        },
    )
    monkeypatch.setattr(
        runtime_ops,
        "_module_source_path",
        lambda name: installed if name == "vllm" else None,
    )

    fingerprint = runtime_ops.resolve_runtime({}).stack_fingerprint()

    assert fingerprint["execution_source"]["cwd"] == str(active.resolve())
    assert fingerprint["execution_source"]["cwd_git"]["commit"] == "cwd"
    assert fingerprint["execution_source"]["modules"]["vllm"]["path"] == str(
        installed.resolve()
    )
    assert fingerprint["execution_source"]["modules"]["vllm"]["git"][
        "commit"
    ] == "module"


def test_invalid_stack_fingerprint_env_fails_closed():
    from runtime_ops import resolve_runtime

    with pytest.raises(ValueError, match="AMMO_STACK_FINGERPRINT"):
        resolve_runtime(
            {
                "AMMO_RUNTIME_PROFILE": "vllm-neuron",
                "AMMO_STACK_FINGERPRINT": "not-json",
            }
        ).stack_fingerprint()


def test_extra_source_dirs_join_sources_once(monkeypatch, tmp_path):
    import runtime_ops

    session = tmp_path / "worktree"
    engine = tmp_path / "engine"
    session.mkdir()
    engine.mkdir()
    monkeypatch.setattr(runtime_ops, "_package_version", lambda name: None)
    monkeypatch.setattr(
        runtime_ops,
        "_git_fingerprint",
        lambda path: {"commit": path.name, "branch": "b", "dirty": False},
    )
    env = {"AMMO_RUNTIME_PROFILE": "pumice-neuron", "AMMO_RUNTIME_SOURCE_DIR": str(session)}
    runtime = runtime_ops.resolve_runtime(env)

    plain = runtime.stack_fingerprint(env)
    same = runtime.stack_fingerprint(env, extra_source_dirs=(session,))
    extra = runtime.stack_fingerprint(env, extra_source_dirs=(engine,))

    assert same == plain
    assert set(extra["sources"]) == {str(session.resolve()), str(engine.resolve())}
    assert extra["sources"][str(engine.resolve())]["commit"] == "engine"
    assert extra["digest"] != plain["digest"]


def test_campaign_runtime_metadata_parses_contiguous_neuron_core_range(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(
        runtime_ops.AcceleratorRuntime,
        "stack_fingerprint",
        lambda self, env=None: {"digest": "sha256:pinned"},
    )
    metadata = runtime_ops.campaign_runtime_metadata(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "AMMO_ACCELERATOR_KIND": "neuron",
            "NEURON_VISIBLE_DEVICES": "8-15",
            "NEURON_LOGICAL_NC_CONFIG": "2",
            "AMMO_INSTANCE_FAMILY": "trn2",
        }
    )

    assert metadata == {
        "runtime": {
            "runtime_profile": "vllm-neuron",
            "accelerator_kind": "neuron",
            "resource_unit": "neuron_core",
            "resource_count": 8,
            "resource_ids": list(range(8, 16)),
            "instance_family": "trn2",
            "lnc": 2,
            "stack_fingerprint": {"digest": "sha256:pinned"},
        },
        "accelerator_resources": {
            "accelerator_kind": "neuron",
            "resource_unit": "neuron_core",
            "resource_count": 8,
            "resource_ids": list(range(8, 16)),
            "visible_resources": "8-15",
        },
    }


def test_campaign_runtime_metadata_prefers_plugin_parent_visibility(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(
        runtime_ops.AcceleratorRuntime,
        "stack_fingerprint",
        lambda self, env=None: {"digest": "sha256:pinned"},
    )

    metadata = runtime_ops.campaign_runtime_metadata(
        {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "NEURON_VISIBLE_DEVICES": "8-15",
            # vLLM workers set this to one rank-local core. It must not shrink
            # the session/campaign resource identity.
            "NEURON_RT_VISIBLE_CORES": "11",
        }
    )

    assert metadata["runtime"]["resource_ids"] == list(range(8, 16))
    assert metadata["accelerator_resources"]["visible_resources"] == "8-15"


def test_campaign_runtime_metadata_defaults_legacy_cuda_to_one_gpu(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(
        runtime_ops.AcceleratorRuntime,
        "stack_fingerprint",
        lambda self, env=None: {"digest": "sha256:cuda"},
    )

    metadata = runtime_ops.campaign_runtime_metadata({})

    assert metadata["runtime"]["runtime_profile"] == "vllm-cuda"
    assert metadata["runtime"]["resource_unit"] == "gpu"
    assert metadata["runtime"]["resource_count"] == 1
    assert metadata["runtime"]["resource_ids"] == [0]
    assert metadata["accelerator_resources"]["visible_resources"] == "0"


def test_campaign_runtime_metadata_maps_cuda_uuids_to_app_local_ids(monkeypatch):
    import runtime_ops

    monkeypatch.setattr(
        runtime_ops.AcceleratorRuntime,
        "stack_fingerprint",
        lambda self, env=None: {"digest": "sha256:cuda"},
    )

    gpu = runtime_ops.campaign_runtime_metadata(
        {"CUDA_VISIBLE_DEVICES": "GPU-aaaaaaaa,GPU-bbbbbbbb"}
    )
    mig = runtime_ops.campaign_runtime_metadata(
        {
            "CUDA_VISIBLE_DEVICES": (
                "MIG-GPU-aaaaaaaa/1/0,MIG-GPU-bbbbbbbb/2/0"
            )
        }
    )

    assert gpu["runtime"]["resource_ids"] == [0, 1]
    assert gpu["accelerator_resources"]["visible_resources"] == (
        "GPU-aaaaaaaa,GPU-bbbbbbbb"
    )
    assert mig["runtime"]["resource_ids"] == [0, 1]


@pytest.mark.parametrize(
    "visible",
    [
        "0,GPU-aaaaaaaa",
        "GPU-aaaaaaaa,MIG-GPU-bbbbbbbb/1/0",
        "MIG-GPU-aaaaaaaa/1/0,1",
    ],
)
def test_campaign_runtime_metadata_rejects_mixed_cuda_visibility_scopes(visible):
    from runtime_ops import campaign_runtime_metadata

    with pytest.raises(ValueError, match="mix|scope"):
        campaign_runtime_metadata({"CUDA_VISIBLE_DEVICES": visible})


def test_neuron_event_normalization_emits_existing_interval_contract(tmp_path):
    from runtime_ops import normalize_neuron_events

    normalized = normalize_neuron_events(
        [
            {
                "start_time_ns": 100,
                "duration_ns": 25,
                "event_name": "nc_exec_running",
                "model_id": "decode_bucket_32",
                "nc_id": 7,
                "worker_gid": 41,
            },
            {
                "start_ns": 130,
                "end_ns": 170,
                "name": "tensor_engine",
                "device_id": 7,
                "pid": 41,
                "engine": "TE",
            },
        ],
        source="SystemProfileEvents.parquet",
    )

    assert normalized["schema"] == "ammo_intervals/1"
    assert normalized["runtime_profile"] == "vllm-neuron"
    assert normalized["events"] == [
        {
            "start_ns": 100,
            "end_ns": 125,
            "symbol": "decode_bucket_32",
            "device_id": 7,
            "stream_id": 0,
            "process_id": 41,
            "graph_id": "decode_bucket_32",
        },
        {
            "start_ns": 130,
            "end_ns": 170,
            "symbol": "tensor_engine",
            "device_id": 7,
            "stream_id": "TE",
            "process_id": 41,
            "graph_id": None,
        },
    ]


# The vendored Neuron Explorer schema ships with the Neuron skills bundle, which
# the plugin does not ship (requirements 13). Neuron paths are not tested in v1.
@pytest.mark.neuron
def test_neuron_event_normalization_accepts_vendored_timeline_timestamp_fields():
    from runtime_ops import normalize_neuron_events

    # This vendored upstream schema is the evidence for the field names and
    # units. Keep the fixture assertion beside the adapter case so a future
    # schema refresh cannot silently invalidate the normalization contract.
    schema_text = _vendored_instruction_schema().read_text(encoding="utf-8")
    assert "start_ts:" in schema_text
    assert "end_ts:" in schema_text
    assert schema_text.count("x-unit: nanoseconds") >= 2

    normalized = normalize_neuron_events(
        [
            {
                "start_ts": 12_345,
                "end_ts": 12_999,
                "event_name": "TENSOR_ENGINE_INSTRUCTION",
                "pcore_idx": 1,
            }
        ],
        source="Instruction.parquet",
        schema_version="vendored-Instruction.yaml",
    )

    assert normalized["events"][0]["start_ns"] == 12_345
    assert normalized["events"][0]["end_ns"] == 12_999
    assert normalized["events"][0]["device_id"] == 1


def test_neuron_instruction_opcode_is_the_canonical_interval_symbol():
    from runtime_ops import normalize_neuron_events

    normalized = normalize_neuron_events(
        [
            {
                "start_ts": 10,
                "end_ts": 20,
                "opcode": "tensor_engine.matmul",
                "pcore_idx": 3,
                "model_id": "decode_bucket_32",
            }
        ],
        source="Instruction.parquet",
    )

    assert normalized["events"][0]["symbol"] == "tensor_engine.matmul"
    assert normalized["events"][0]["device_id"] == 3


def test_neuron_active_time_row_uses_engine_label_and_pcore_idx():
    from runtime_ops import normalize_neuron_events

    # active_time sample row from neuron-explorer 2.30.10.0
    # `view --output-format json` (json_format_schema_probe.json). The only
    # descriptive field is `engine`; the start/end are `start_ts`/`end_ts`.
    normalized = normalize_neuron_events(
        [
            {
                "duration_ns": 17,
                "pcore_idx": 0,
                "engine": "cc_instruction",
                "subgraph": "sg00",
                "start_ts": 9728,
                "end_ts": 9746,
            }
        ],
        source="active_time",
    )

    event = normalized["events"][0]
    assert event["start_ns"] == 9728
    assert event["end_ns"] == 9746
    assert event["symbol"] == "cc_instruction"
    assert event["device_id"] == 0
    # `engine` is aliased for both symbol and stream_id, so a row that carries
    # only `engine` reuses that value for both. That is the intended per-engine
    # window identity, not a collision.
    assert event["stream_id"] == "cc_instruction"


def test_neuron_cc_ops_rows_use_operation_label_and_bare_timestamp():
    from runtime_ops import normalize_neuron_events

    # cc_ops sample rows from cc_ops_field_report.json. Start is bare
    # `timestamp`, duration is `duration`, and the only field that names the
    # collective is `operation` (AllReduce / AllGather / Invalid barrier).
    normalized = normalize_neuron_events(
        [
            {
                "timestamp": 10606,
                "duration": 144336,
                "operation": "Invalid",
                "type": "cc_op",
                "dtype": "BARRIER",
                "trigger_engine": "Tensor",
                "stream_id": 0,
            },
            {
                "timestamp": 162912,
                "duration": 25127,
                "operation": "AllReduce",
                "type": "cc_op",
                "dtype": "bf16",
                "trigger_engine": "GpSimd",
                "stream_id": 0,
            },
        ],
        source="cc_ops",
    )

    events = normalized["events"]
    assert events[0]["start_ns"] == 10606
    assert events[0]["end_ns"] == 10606 + 144336
    assert events[0]["symbol"] == "Invalid"
    assert events[0]["stream_id"] == 0
    assert events[1]["symbol"] == "AllReduce"


def test_neuron_instruction_row_resolves_bare_timestamp_start():
    from runtime_ops import normalize_neuron_events

    # instruction sample row from json_format_schema_probe.json. `opcode` is the
    # symbol, but the start field is bare `timestamp`, which previously failed.
    normalized = normalize_neuron_events(
        [{"opcode": "WRITE", "timestamp": 84, "duration": 72, "label": "Tensor"}],
        source="instruction",
    )

    event = normalized["events"][0]
    assert event["start_ns"] == 84
    assert event["end_ns"] == 84 + 72
    assert event["symbol"] == "WRITE"


def test_neuron_normalization_rejects_rows_missing_start_or_label():
    from runtime_ops import normalize_neuron_events

    # No start field: cc_ops shape stripped of `timestamp`.
    with pytest.raises(ValueError, match="no start timestamp"):
        normalize_neuron_events(
            [{"operation": "AllReduce", "duration": 100, "type": "cc_op"}],
            source="cc_ops",
        )

    # No label field: active_time shape stripped of `engine`.
    with pytest.raises(ValueError, match="no event or model name"):
        normalize_neuron_events(
            [{"start_ts": 10, "end_ts": 20, "subgraph": "sg00"}],
            source="active_time",
        )


def test_neuron_normalization_rejects_type_and_dtype_only_row():
    from runtime_ops import normalize_neuron_events

    # A row carrying only the cc_ops `type` ("cc_op" constant) and `dtype`
    # (payload dtype) must stay rejected: neither is a symbol alias. With no
    # start field it fails at the start check.
    with pytest.raises(ValueError, match="no start timestamp"):
        normalize_neuron_events(
            [{"type": "cc_op", "dtype": "bf16"}], source="cc_ops"
        )

    # Even given a valid start, `type` and `dtype` must not stand in for the
    # collective name, so the row still fails the label check.
    with pytest.raises(ValueError, match="no event or model name"):
        normalize_neuron_events(
            [{"timestamp": 10, "duration": 5, "type": "cc_op", "dtype": "bf16"}],
            source="cc_ops",
        )


# --------------------------------------------------------------------------- CUDA-only gate (conventions §6)


@pytest.mark.parametrize("env", [{}, {"AMMO_RUNTIME_PROFILE": ""}, {"AMMO_RUNTIME_PROFILE": " pumice-cuda "}])
def test_require_supported_profile_accepts_pumice_cuda_and_its_default(env):
    from runtime_ops import require_supported_profile

    assert require_supported_profile(env=env) == "pumice-cuda"


def test_require_supported_profile_accepts_cuda_accelerator_kind():
    from runtime_ops import require_supported_profile

    env = {"AMMO_RUNTIME_PROFILE": "pumice-cuda", "AMMO_ACCELERATOR_KIND": "cuda"}
    assert require_supported_profile(env=env) == "pumice-cuda"


@pytest.mark.parametrize("profile", ["pumice-neuron", "vllm-cuda", "vllm-neuron", "bogus"])
def test_require_supported_profile_refuses_every_other_profile(profile):
    from runtime_ops import require_supported_profile

    with pytest.raises(ValueError) as excinfo:
        require_supported_profile(env={"AMMO_RUNTIME_PROFILE": profile})
    assert repr(profile) in str(excinfo.value)
    assert "pumice-cuda" in str(excinfo.value)


def test_require_supported_profile_explicit_argument_wins_over_env():
    from runtime_ops import require_supported_profile

    env = {"AMMO_RUNTIME_PROFILE": "pumice-cuda"}
    with pytest.raises(ValueError, match="pumice-neuron"):
        require_supported_profile("pumice-neuron", env=env)
    assert require_supported_profile("pumice-cuda", env={"AMMO_RUNTIME_PROFILE": "x"}) == "pumice-cuda"


def test_require_supported_profile_refuses_a_non_cuda_accelerator_kind():
    from runtime_ops import require_supported_profile

    env = {"AMMO_RUNTIME_PROFILE": "pumice-cuda", "AMMO_ACCELERATOR_KIND": "neuron"}
    with pytest.raises(ValueError, match="AMMO_ACCELERATOR_KIND"):
        require_supported_profile(env=env)


def test_require_supported_profile_reads_os_environ_by_default(monkeypatch):
    from runtime_ops import require_supported_profile

    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "pumice-neuron")
    monkeypatch.delenv("AMMO_ACCELERATOR_KIND", raising=False)
    with pytest.raises(ValueError):
        require_supported_profile()


def test_fingerprint_cli_fills_campaign_env_through_the_layer(tmp_path, monkeypatch, capsys):
    """runtime_ops.py fingerprint calls apply_env() first, so a run inside a
    campaign records the campaign's runtime profile and engine root."""
    import runtime_ops

    engine = tmp_path / "engine"
    campaign = engine / ".ammo" / "c1"
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text("c1\n", encoding="utf-8")
    (campaign / "target.json").write_text(
        json.dumps({"runtime": {"profile": "pumice-cuda"}}), encoding="utf-8"
    )
    monkeypatch.chdir(engine)
    monkeypatch.setattr(runtime_ops, "_git_fingerprint", lambda path: None)
    # apply_env() writes os.environ directly, and monkeypatch.delenv does not
    # record an absent name, so snapshot the whole mapping and restore it.
    import os

    saved = dict(os.environ)
    try:
        for name in _CAMPAIGN_ENV_NAMES:
            os.environ.pop(name, None)
        assert runtime_ops._main(["fingerprint"]) == 0
        source_dir = os.environ.get("AMMO_RUNTIME_SOURCE_DIR")
    finally:
        os.environ.clear()
        os.environ.update(saved)

    payload = json.loads(capsys.readouterr().out)
    assert payload["runtime_profile"] == "pumice-cuda"
    assert source_dir == str(engine)


def test_apply_env_is_the_first_statement_of_main():
    source = (_SCRIPTS_DIR / "runtime_ops.py").read_text(encoding="utf-8")
    body = source.split("\ndef _main(", 1)[1].split("\n", 1)[1]
    first = next(line.strip() for line in body.splitlines() if line.strip())
    assert first == "campaign_paths.apply_env()"
