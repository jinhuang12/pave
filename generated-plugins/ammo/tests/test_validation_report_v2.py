#!/usr/bin/env python3
"""Regression tests for round/slot E2E lookup in generate_validation_report.py."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_GEN = _SCRIPTS_DIR / "generate_validation_report.py"
_EVIDENCE = _SCRIPTS_DIR / "create_evidence_template.py"
_AMMO_DIR = Path(__file__).resolve().parent.parent


def _e2e_payload(speedup: float = 1.05) -> str:
    return json.dumps({
        "model_id": "test/model",
        "tp": 1,
        "max_model_len": 4096,
        "workload": {"input_len": 64, "output_len": 64, "num_iters": 1},
        "results": [{
            "batch_size": 1,
            "speedup": speedup,
            "improvement_pct": (speedup - 1) * 100,
            "baseline": {
                "avg_s": 1.0,
                "cmd": ["python", "bench.py", "--label", "baseline"],
                "env_overrides": {"CUDA_VISIBLE_DEVICES": "0"},
            },
            "opt": {
                "avg_s": 1.0 / speedup,
                "cmd": ["python", "bench.py", "--label", "opt"],
                "env_overrides": {"CUDA_VISIBLE_DEVICES": "0"},
            },
        }],
        "bench": {"baseline_label": "baseline", "opt_label": "opt"},
    })


def _stack_fingerprint(source_commit: str = "a" * 40) -> dict:
    image_digest = "sha256:" + "1" * 64
    payload = {
        "runtime_profile": "vllm-neuron",
        "accelerator_kind": "neuron",
        "packages": {"vllm": "0.24.0", "vllm-neuron": "0.24.0.1.1.0"},
        "sources": {"/runtime/source": {"commit": source_commit, "dirty": False}},
        "environment": {
            "AMMO_RUNTIME_PROFILE": "vllm-neuron",
            "AMMO_STACK_IMAGE_DIGEST": image_digest,
            "VLLM_CACHE_ROOT": "/dynamic/cache",
        },
        "source_environment": {"AMMO_RUNTIME_SOURCE_COMMIT": source_commit},
        "execution_source": {
            "cwd": "/runtime/source",
            "cwd_git": {"commit": source_commit, "dirty": False},
            "modules": {},
        },
        "server_stack_fingerprint": {
            "base_image_digest": image_digest,
            "vllm_neuron": "0.24.0.1.1.0",
            "vllm_neuron_source_commit": source_commit,
        },
    }
    digest = hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()
    return {"digest": "sha256:" + digest, **payload}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_GEN), *args], capture_output=True, text=True)


def test_e2e_guide_tool_selection_preserves_stage1_and_stage6_control_shape():
    guide = (_AMMO_DIR / "references" / "e2e-latency-guide.md").read_text(encoding="utf-8")
    table = guide.split("## Tool Selection", 1)[1].split("For all measurements", 1)[0]

    assert "Stage 1 (baseline)" in table
    assert "--round {N} --slot baseline --labels baseline --capture-golden-refs" in table
    assert "Stage 6 (integration)" in table
    assert "--round {N} --slot integration --labels opt --baseline-from $STAGE1_DIR --fresh-cache" in table
    assert "two or more tracks pass" in table
    assert "single unchanged passer uses the short-circuit" in table

    recording = guide.split("## Recording results", 1)[1]
    assert "{artifact_dir}/rounds/{CR}/tracks/{op_id}/validation_results.md" in recording
    assert "{artifact_dir}/rounds/{CR}/sweeps/integration/e2e_latency_results.json" in recording
    assert "integration_validation.md" not in recording
    assert "{artifact_dir}/validation_results.md" not in recording


def test_validation_defaults_use_round_scoped_validation_results_path():
    defaults = (_AMMO_DIR / "references" / "validation-defaults.md").read_text(encoding="utf-8")

    assert "{artifact_dir}/rounds/{N}/tracks/{op_id}/validation_results.md" in defaults
    assert "{artifact_dir}/validation_results.md" not in defaults


def test_round_and_slot_resolves_e2e_path(tmp_path: Path):
    artifact = tmp_path / "v2_artifact"
    rd = artifact / "rounds" / "1" / "sweeps" / "baseline"
    rd.mkdir(parents=True)
    (rd / "e2e_latency_results.json").write_text(_e2e_payload(speedup=1.10), encoding="utf-8")

    result = _run(["--artifact-dir", str(artifact), "--round", "1", "--slot", "baseline"])

    assert result.returncode == 0, f"failed: {result.stdout} / {result.stderr}"
    out = (artifact / "validation_results.md").read_text(encoding="utf-8")
    assert "1.1" in out or "10%" in out
    assert "Repro (baseline example):" in out
    assert "CUDA_VISIBLE_DEVICES=0 python bench.py --label baseline" in out
    assert "Repro (optimized example):" in out
    assert "CUDA_VISIBLE_DEVICES=0 python bench.py --label opt" in out


def test_legacy_path_still_works(tmp_path: Path):
    artifact = tmp_path / "v1_artifact"
    e2e_dir = artifact / "e2e_latency"
    e2e_dir.mkdir(parents=True)
    (e2e_dir / "e2e_latency_results.json").write_text(_e2e_payload(speedup=1.20), encoding="utf-8")

    result = _run(["--artifact-dir", str(artifact)])

    assert result.returncode == 0, f"failed: {result.stdout} / {result.stderr}"
    out = (artifact / "validation_results.md").read_text(encoding="utf-8")
    assert "1.2" in out or "20%" in out
    assert "Model snapshot:" not in out


def test_report_renders_model_snapshot_only_when_present(tmp_path: Path):
    artifact = tmp_path / "snapshot_report"
    e2e_dir = artifact / "e2e_latency"
    e2e_dir.mkdir(parents=True)
    payload = json.loads(_e2e_payload())
    payload["model_snapshot"] = {
        "schema": "ammo_model_snapshot/1",
        "model_id": "test/model",
        "source_type": "huggingface",
        "identity": "hf:test/model@" + "a" * 40,
        "resolved_revision": "a" * 40,
    }
    (e2e_dir / "e2e_latency_results.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    result = _run(["--artifact-dir", str(artifact)])

    assert result.returncode == 0, result.stderr
    out = (artifact / "validation_results.md").read_text(encoding="utf-8")
    assert "Model snapshot: `hf:test/model@" in out
    summary = json.loads(
        (artifact / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["model_snapshot"]["resolved_revision"] == "a" * 40


def test_artifact_dir_mode_reports_malformed_json_without_traceback(tmp_path: Path):
    artifact = tmp_path / "bad_artifact"
    e2e_dir = artifact / "e2e_latency"
    e2e_dir.mkdir(parents=True)
    (e2e_dir / "e2e_latency_results.json").write_text("{bad json", encoding="utf-8")

    result = _run(["--artifact-dir", str(artifact)])

    assert result.returncode != 0
    assert "Failed to parse JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_create_evidence_template_defaults_to_current_round_track_path(tmp_path: Path):
    artifact = tmp_path / "v2_artifact"
    (artifact / "rounds" / "2").mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps({"campaign": {"current_round": 2, "rounds": [{}, {}]}}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_EVIDENCE),
            "--artifact-dir",
            str(artifact),
            "--track-id",
            "op007",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"failed: {result.stdout} / {result.stderr}"
    assert (artifact / "rounds" / "2" / "tracks" / "op007" / "evidence.json").exists()
    assert not (artifact / "tracks" / "op007" / "evidence.json").exists()


def test_artifact_dir_track_report_defaults_to_current_round_track_path(tmp_path: Path):
    artifact = tmp_path / "v2_artifact"
    track_dir = artifact / "rounds" / "2" / "tracks" / "op007"
    track_dir.mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps({"campaign": {"current_round": 2, "rounds": [{}, {}]}}),
        encoding="utf-8",
    )
    (track_dir / "evidence.json").write_text(
        json.dumps({"track_id": "op007", "baseline_source": {"citation": "stage1"}}),
        encoding="utf-8",
    )

    result = _run(["--artifact-dir", str(artifact), "--track", "op007"])

    assert result.returncode == 0, f"failed: {result.stdout} / {result.stderr}"
    assert (track_dir / "validation_results.md").exists()
    assert not (artifact / "tracks" / "op007" / "validation_results.md").exists()


def test_evidence_template_defaults_legacy_runtime_to_cuda(tmp_path: Path):
    artifact = tmp_path / "cuda_artifact"
    artifact.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(_EVIDENCE),
            "--output",
            str(artifact / "evidence.json"),
            "--track-id",
            "op_cuda",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads((artifact / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["runtime_profile"] == "vllm-cuda"
    assert evidence["kernel_bench"]["measured_under_cuda_graphs"] is False
    assert "measured_under_production_runtime" not in evidence["kernel_bench"]


def test_evidence_template_infers_neuron_runtime_from_state(tmp_path: Path):
    artifact = tmp_path / "neuron_artifact"
    (artifact / "rounds" / "1").mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps(
            {
                "runtime": {"runtime_profile": "vllm-neuron"},
                "campaign": {"current_round": 1, "rounds": [{}]},
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(_EVIDENCE),
            "--artifact-dir",
            str(artifact),
            "--track-id",
            "op_neuron",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    evidence_path = (
        artifact / "rounds" / "1" / "tracks" / "op_neuron" / "evidence.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["runtime_profile"] == "vllm-neuron"
    assert evidence["kernel_bench"]["measured_under_production_runtime"] is False
    assert "measured_under_cuda_graphs" not in evidence["kernel_bench"]


def test_neuron_report_renders_runtime_bound_gate_5_2_and_5_3a(tmp_path: Path):
    artifact = tmp_path / "neuron_report"
    track_id = "op_neuron"
    track_dir = artifact / "rounds" / "1" / "tracks" / track_id
    e2e_dir = artifact / "rounds" / "1" / "sweeps" / "opt" / track_id
    proof_dir = (
        artifact / "rounds" / "1" / "sweeps" / "opt_profiling" / track_id
    )
    track_dir.mkdir(parents=True)
    e2e_dir.mkdir(parents=True)
    proof_dir.mkdir(parents=True)
    stack = _stack_fingerprint()
    (artifact / "state.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "runtime_profile": "vllm-neuron",
                    "stack_fingerprint": stack,
                },
                "campaign": {"current_round": 1, "rounds": [{}]},
            }
        ),
        encoding="utf-8",
    )
    (track_dir / "evidence.json").write_text(
        json.dumps(
            {
                "track_id": track_id,
                "runtime_profile": "vllm-neuron",
                "correctness": {"status": "PASS", "method": "torch.allclose"},
                "kernel_bench": {
                    "status": "PASS",
                    "weighted_speedup": 1.05,
                    "measured_under_production_runtime": True,
                },
            }
        ),
        encoding="utf-8",
    )
    e2e = json.loads(_e2e_payload())
    e2e["stack_fingerprint"] = stack
    (e2e_dir / "e2e_latency_results.json").write_text(
        json.dumps(e2e), encoding="utf-8"
    )
    proof = {
        "schema": "ammo_activation_proof/1",
        "runtime_profile": "vllm-neuron",
        "accelerator_kind": "neuron",
        "profiler_route": "neuron-profiler",
        "timing_admissible": False,
        "status": "PASS",
        "hits": 3,
        "stack_fingerprint": stack,
        "arms": {
            "opt": {
                "status": "PASS",
                "hits": 3,
                "primary_artifacts": [
                    {
                        "kind": "neuron-profile",
                        "path": "profile.ntff",
                        "size_bytes": len(b"neuron-profile"),
                        "sha256": "sha256:"
                        + hashlib.sha256(b"neuron-profile").hexdigest(),
                    }
                ],
            }
        },
    }
    (proof_dir / "profile.ntff").write_bytes(b"neuron-profile")
    (proof_dir / "activation_proof.json").write_text(
        json.dumps(proof), encoding="utf-8"
    )

    result = _run(["--artifact-dir", str(artifact), "--track", track_id])

    assert result.returncode == 0, result.stderr
    report = (track_dir / "validation_results.md").read_text(encoding="utf-8")
    summary = json.loads(
        (track_dir / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert "Runtime profile: `vllm-neuron`" in report
    assert "## Kernel perf (production runtime)" in report
    assert "Measured under production runtime: `True`" in report
    assert "## Gate 5.3a activation proof" in report
    assert "Status: `PASS`" in report
    assert summary["runtime_profile"] == "vllm-neuron"
    assert summary["activation_gate"]["pass"] is True
    assert summary["activation_gate"]["primary_artifacts_verified"] is True
    assert summary["activation_gate"]["stack_fingerprint_verified"] is True

    (proof_dir / "profile.ntff").write_bytes(b"tampered")
    result = _run(["--artifact-dir", str(artifact), "--track", track_id])

    assert result.returncode == 0, result.stderr
    summary = json.loads(
        (track_dir / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["activation_gate"]["pass"] is False
    assert summary["activation_gate"]["primary_artifacts_verified"] is False

    (proof_dir / "profile.ntff").write_bytes(b"neuron-profile")
    proof["stack_fingerprint"] = _stack_fingerprint("b" * 40)
    (proof_dir / "activation_proof.json").write_text(
        json.dumps(proof), encoding="utf-8"
    )
    result = _run(["--artifact-dir", str(artifact), "--track", track_id])

    assert result.returncode == 0, result.stderr
    summary = json.loads(
        (track_dir / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["activation_gate"]["pass"] is False
    assert summary["activation_gate"]["stack_fingerprint_verified"] is False

    proof["stack_fingerprint"] = stack
    proof["profiler_route"] = "nsys"
    (proof_dir / "activation_proof.json").write_text(
        json.dumps(proof), encoding="utf-8"
    )
    result = _run(["--artifact-dir", str(artifact), "--track", track_id])

    assert result.returncode == 0, result.stderr
    summary = json.loads(
        (track_dir / "validation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["activation_gate"]["status"] == "FAIL"
    assert summary["activation_gate"]["pass"] is False


def test_report_rejects_explicit_null_evidence_runtime(tmp_path: Path):
    artifact = tmp_path / "null_runtime"
    track_id = "op_null"
    track_dir = artifact / "rounds" / "1" / "tracks" / track_id
    track_dir.mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps(
            {
                "runtime": {"runtime_profile": "vllm-cuda"},
                "campaign": {"current_round": 1, "rounds": [{}]},
            }
        ),
        encoding="utf-8",
    )
    (track_dir / "evidence.json").write_text(
        json.dumps({"track_id": track_id, "runtime_profile": None}),
        encoding="utf-8",
    )

    result = _run(["--artifact-dir", str(artifact), "--track", track_id])

    assert result.returncode != 0
    assert "Unsupported evidence runtime profile" in result.stderr
