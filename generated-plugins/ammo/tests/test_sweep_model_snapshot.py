#!/usr/bin/env python3
"""Immutable model-snapshot evidence for Stage 1 and later sweeps."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


def _fake_llm(commit: str):
    hf_config = SimpleNamespace(_commit_hash=commit, _name_or_path="org/model")
    model_config = SimpleNamespace(hf_config=hf_config, model="org/model")
    return SimpleNamespace(vllm_config=SimpleNamespace(model_config=model_config))


GLM_SOURCE_COMMIT = "41758812f2b30ca7b43ec6c2a1137c927f53c7a1"


def _cached_config(
    root: Path,
    *,
    model_id: str = "zai-org/GLM-5.2-FP8",
    commit: str = "b" * 40,
    architecture: str = "GlmMoeDsaForCausalLM",
) -> Path:
    snapshot = (
        root
        / "hub"
        / ("models--" + model_id.replace("/", "--"))
        / "snapshots"
        / commit
    )
    snapshot.mkdir(parents=True)
    config = snapshot / "config.json"
    config.write_text(
        json.dumps({"architectures": [architecture]}), encoding="utf-8"
    )
    return config


def _neuron_runtime():
    from runtime_ops import resolve_runtime

    return resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"})


def test_cuda_model_load_binding_is_no_io_passthrough(monkeypatch):
    import run_vllm_bench_latency_sweep as sweep

    monkeypatch.setattr(
        sweep,
        "_download_hf_config",
        lambda *args, **kwargs: pytest.fail("CUDA must not resolve HF config"),
        raising=False,
    )

    assert sweep._prepare_model_load_binding(
        "org/model", sweep.resolve_runtime({}), {}
    ) is None


def test_exact_glm_adapter_materializes_one_immutable_snapshot(monkeypatch, tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    commit = "a" * 40
    config = _cached_config(tmp_path, commit=commit)
    snapshot = config.parent
    (snapshot / "model.safetensors.index.json").write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: config)

    def materialize(model_id, revision, env):
        calls.append((model_id, revision, dict(env)))
        return snapshot

    monkeypatch.setattr(sweep, "_materialize_hf_snapshot", materialize)
    env = {
        "HF_HOME": str(tmp_path),
        "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
    }

    binding = sweep._prepare_model_load_binding(
        "zai-org/GLM-5.2-FP8", _neuron_runtime(), env
    )

    assert len(calls) == 1
    assert calls[0][1] == commit
    assert binding["requested_model_id"] == "zai-org/GLM-5.2-FP8"
    assert binding["architecture"] == "GlmMoeDsaForCausalLM"
    assert binding["requires_local_checkpoint"] is True
    assert binding["load_target"] == str(snapshot.resolve())
    assert binding["model_snapshot"]["identity"] == (
        f"hf:zai-org/GLM-5.2-FP8@{commit}"
    )


def test_exact_source_non_glm_model_keeps_requested_model_id(monkeypatch, tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    commit = "c" * 40
    config = _cached_config(
        tmp_path,
        model_id="openai/gpt-oss-20b",
        commit=commit,
        architecture="GptOssForCausalLM",
    )
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: config)
    monkeypatch.setattr(
        sweep,
        "_materialize_hf_snapshot",
        lambda *args: pytest.fail("GPT-OSS does not need the GLM adapter"),
    )

    binding = sweep._prepare_model_load_binding(
        "openai/gpt-oss-20b",
        _neuron_runtime(),
        {
            "HF_HOME": str(tmp_path),
            "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
        },
    )

    assert binding["requires_local_checkpoint"] is False
    assert binding["load_target"] == "openai/gpt-oss-20b"
    assert binding["model_snapshot"]["resolved_revision"] == commit


@pytest.mark.parametrize(
    "source_commit",
    ["41758812", "z" * 40],
)
def test_neuron_binding_rejects_invalid_source_commit(
    monkeypatch, tmp_path, source_commit
):
    import run_vllm_bench_latency_sweep as sweep

    monkeypatch.setattr(
        sweep,
        "_download_hf_config",
        lambda *args: pytest.fail("invalid source must fail before HF I/O"),
        raising=False,
    )

    with pytest.raises(ValueError, match="40-character"):
        sweep._prepare_model_load_binding(
            "zai-org/GLM-5.2-FP8",
            _neuron_runtime(),
            {
                "HF_HOME": str(tmp_path),
                "AMMO_RUNTIME_SOURCE_COMMIT": source_commit,
            },
        )


def test_neuron_binding_rejects_config_outside_session_cache(monkeypatch, tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    escaped = tmp_path / "outside" / "config.json"
    escaped.parent.mkdir()
    escaped.write_text(
        json.dumps({"architectures": ["GlmMoeDsaForCausalLM"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: escaped)

    with pytest.raises(RuntimeError, match="immutable Hugging Face cache snapshot"):
        sweep._prepare_model_load_binding(
            "zai-org/GLM-5.2-FP8",
            _neuron_runtime(),
            {
                "HF_HOME": str(tmp_path),
                "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
            },
        )


@pytest.mark.parametrize(
    "config_payload",
    [{}, {"architectures": []}, {"architectures": [1]}],
)
def test_neuron_binding_rejects_unresolved_architecture(
    monkeypatch, tmp_path, config_payload
):
    import run_vllm_bench_latency_sweep as sweep

    config = _cached_config(tmp_path)
    config.write_text(json.dumps(config_payload), encoding="utf-8")
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: config)

    with pytest.raises(RuntimeError, match="architectures"):
        sweep._prepare_model_load_binding(
            "zai-org/GLM-5.2-FP8",
            _neuron_runtime(),
            {
                "HF_HOME": str(tmp_path),
                "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
            },
        )


def test_glm_binding_requires_weight_index_before_child_load(monkeypatch, tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    config = _cached_config(tmp_path)
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: config)
    monkeypatch.setattr(
        sweep, "_materialize_hf_snapshot", lambda *args: config.parent
    )

    with pytest.raises(RuntimeError, match="model.safetensors.index.json"):
        sweep._prepare_model_load_binding(
            "zai-org/GLM-5.2-FP8",
            _neuron_runtime(),
            {
                "HF_HOME": str(tmp_path),
                "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
            },
        )


def test_child_revalidates_local_binding_before_llm(monkeypatch, tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    config = _cached_config(tmp_path)
    index = config.parent / "model.safetensors.index.json"
    index.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sweep, "_download_hf_config", lambda *args: config)
    monkeypatch.setattr(
        sweep, "_materialize_hf_snapshot", lambda *args: config.parent
    )
    env = {
        "HF_HOME": str(tmp_path),
        "AMMO_RUNTIME_SOURCE_COMMIT": GLM_SOURCE_COMMIT,
    }
    binding = sweep._prepare_model_load_binding(
        "zai-org/GLM-5.2-FP8", _neuron_runtime(), env
    )
    child_env = {
        **env,
        sweep.MODEL_LOAD_BINDING_ENV: sweep._serialize_model_load_binding(binding),
    }

    index.unlink()

    with pytest.raises(RuntimeError, match="model.safetensors.index.json"):
        sweep._parse_model_load_binding(
            "zai-org/GLM-5.2-FP8", _neuron_runtime(), child_env
        )


def test_loaded_snapshot_must_match_parent_binding(tmp_path):
    import run_vllm_bench_latency_sweep as sweep

    expected = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "source_type": "huggingface",
        "resolved_revision": "d" * 40,
        "identity": "hf:org/model@" + "d" * 40,
    }
    binding = {"model_snapshot": expected}
    loaded = {**expected, "resolved_revision": "e" * 40,
              "identity": "hf:org/model@" + "e" * 40}

    with pytest.raises(RuntimeError, match="post-load snapshot mismatch"):
        sweep._validate_loaded_model_binding(binding, loaded)


def test_resolves_hf_commit_from_loaded_vllm_config():
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    commit = "a" * 40
    identity = _resolve_loaded_model_snapshot("org/model", _fake_llm(commit), {})

    assert identity == {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "source_type": "huggingface",
        "resolved_revision": commit,
        "identity": f"hf:org/model@{commit}",
    }


def test_resolves_hf_commit_from_session_cache_after_model_load(tmp_path):
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    commit = "b" * 40
    repo = tmp_path / "hub" / "models--org--model"
    (repo / "refs").mkdir(parents=True)
    (repo / "snapshots" / commit).mkdir(parents=True)
    (repo / "refs" / "main").write_text(commit + "\n", encoding="utf-8")
    llm = SimpleNamespace(vllm_config=SimpleNamespace(model_config=SimpleNamespace()))

    identity = _resolve_loaded_model_snapshot(
        "org/model", llm, {"HF_HOME": str(tmp_path)}
    )

    assert identity["resolved_revision"] == commit
    assert identity["identity"] == f"hf:org/model@{commit}"


def test_resolves_requested_hf_ref_instead_of_unrelated_main(tmp_path):
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    main_commit = "1" * 40
    release_commit = "2" * 40
    repo = tmp_path / "hub" / "models--org--model"
    (repo / "refs").mkdir(parents=True)
    (repo / "snapshots" / main_commit).mkdir(parents=True)
    (repo / "snapshots" / release_commit).mkdir(parents=True)
    (repo / "refs" / "main").write_text(main_commit + "\n", encoding="utf-8")
    (repo / "refs" / "release-1").write_text(
        release_commit + "\n", encoding="utf-8"
    )
    model_config = SimpleNamespace(revision="release-1")
    llm = SimpleNamespace(vllm_config=SimpleNamespace(model_config=model_config))

    identity = _resolve_loaded_model_snapshot(
        "org/model", llm, {"HF_HOME": str(tmp_path)}
    )

    assert identity["resolved_revision"] == release_commit


def test_unresolved_requested_hf_ref_does_not_fall_back_to_main(tmp_path):
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    main_commit = "3" * 40
    repo = tmp_path / "hub" / "models--org--model"
    (repo / "refs").mkdir(parents=True)
    (repo / "snapshots" / main_commit).mkdir(parents=True)
    (repo / "refs" / "main").write_text(main_commit + "\n", encoding="utf-8")
    model_config = SimpleNamespace(revision="missing-release")
    llm = SimpleNamespace(vllm_config=SimpleNamespace(model_config=model_config))

    identity = _resolve_loaded_model_snapshot(
        "org/model", llm, {"HF_HOME": str(tmp_path)}
    )

    assert identity is None


def test_mutable_local_model_path_is_not_claimed_as_immutable(tmp_path):
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    model = tmp_path / "local-model"
    model.mkdir()
    identity = _resolve_loaded_model_snapshot(str(model), object(), {})

    assert identity is None


def test_neuron_mutable_local_model_path_fails_closed(tmp_path):
    from run_vllm_bench_latency_sweep import _require_loaded_model_snapshot
    from runtime_ops import resolve_runtime

    model = tmp_path / "local-model"
    model.mkdir()

    with pytest.raises(RuntimeError, match="immutable Hugging Face snapshot"):
        _require_loaded_model_snapshot(
            str(model),
            object(),
            resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}),
            {},
        )


def test_hf_cache_snapshot_path_resolves_commit_identity(tmp_path):
    from run_vllm_bench_latency_sweep import _resolve_loaded_model_snapshot

    commit = "4" * 40
    snapshot = (
        tmp_path
        / "hub"
        / "models--org--model"
        / "snapshots"
        / commit
    )
    snapshot.mkdir(parents=True)

    identity = _resolve_loaded_model_snapshot(
        str(snapshot), object(), {"HF_HOME": str(tmp_path)}
    )

    assert identity == {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "requested_model_id": str(snapshot),
        "source_type": "huggingface",
        "resolved_revision": commit,
        "resolved_path": str(snapshot.resolve()),
        "identity": f"hf:org/model@{commit}",
    }


def test_neuron_hf_model_without_resolved_commit_fails_closed():
    from run_vllm_bench_latency_sweep import _require_loaded_model_snapshot
    from runtime_ops import resolve_runtime

    with pytest.raises(RuntimeError, match="immutable Hugging Face snapshot"):
        _require_loaded_model_snapshot(
            "org/model",
            object(),
            resolve_runtime({"AMMO_RUNTIME_PROFILE": "vllm-neuron"}),
            {},
        )


def test_cuda_legacy_model_without_resolved_commit_remains_conditional():
    from run_vllm_bench_latency_sweep import _require_loaded_model_snapshot
    from runtime_ops import resolve_runtime

    assert _require_loaded_model_snapshot(
        "org/model", object(), resolve_runtime({}), {}
    ) is None


def test_baseline_reuse_rejects_snapshot_mismatch():
    from run_vllm_bench_latency_sweep import (
        _validate_baseline_snapshot_compatibility,
    )

    baseline = {
        "model_snapshot": {
            "schema": "ammo_model_snapshot/1",
            "model_id": "org/model",
            "source_type": "huggingface",
            "identity": "hf:org/model@" + "a" * 40,
            "resolved_revision": "a" * 40,
        }
    }
    current = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "source_type": "huggingface",
        "identity": "hf:org/model@" + "b" * 40,
        "resolved_revision": "b" * 40,
    }

    with pytest.raises(SystemExit, match="snapshot mismatch"):
        _validate_baseline_snapshot_compatibility(
            baseline, current, runtime_profile="vllm-neuron"
        )


def test_neuron_baseline_reuse_requires_both_snapshot_identities():
    from run_vllm_bench_latency_sweep import (
        _validate_baseline_snapshot_compatibility,
    )

    with pytest.raises(SystemExit, match="requires immutable model snapshot"):
        _validate_baseline_snapshot_compatibility(
            {}, None, runtime_profile="vllm-neuron"
        )


def test_cuda_legacy_baseline_without_snapshot_remains_accepted():
    from run_vllm_bench_latency_sweep import (
        _validate_baseline_snapshot_compatibility,
    )

    assert (
        _validate_baseline_snapshot_compatibility(
            {}, None, runtime_profile="vllm-cuda"
        )
        is None
    )


def test_model_snapshot_sidecar_is_lifted_only_when_present(tmp_path):
    from run_vllm_bench_latency_sweep import _load_child_model_snapshot

    assert _load_child_model_snapshot(tmp_path, "candidate", ["bs1"]) is None

    identity = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "source_type": "huggingface",
        "identity": "hf:org/model@" + "c" * 40,
        "resolved_revision": "c" * 40,
    }
    sidecar = tmp_path / "json" / "candidate_model_snapshot.json"
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps(identity), encoding="utf-8")

    assert _load_child_model_snapshot(tmp_path, "candidate", ["bs1"]) == identity


def test_snapshot_identity_rejects_forged_identity_metadata():
    from run_vllm_bench_latency_sweep import _snapshot_identity_key

    snapshot = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "org/model",
        "source_type": "huggingface",
        "resolved_revision": "d" * 40,
        "identity": "hf:org/model@" + "e" * 40,
    }

    assert _snapshot_identity_key(snapshot) is None


def test_snapshot_identity_rejects_mutable_local_path_schema():
    from run_vllm_bench_latency_sweep import _snapshot_identity_key

    snapshot = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "/models/local",
        "source_type": "local",
        "resolved_path": "/models/local",
        "identity": "local:/models/local",
    }

    assert _snapshot_identity_key(snapshot) is None
