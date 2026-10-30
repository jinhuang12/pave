#!/usr/bin/env python3
"""Regression tests for round-scoped new_target.py scaffold."""

from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_NEW_TARGET = _SCRIPTS_DIR / "new_target.py"
_SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "state.schema.json"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_spec = importlib.util.spec_from_file_location("new_target", str(_NEW_TARGET))
new_target = importlib.util.module_from_spec(_spec)
sys.modules["new_target"] = new_target
_spec.loader.exec_module(new_target)


@pytest.fixture
def scaffolded_artifact_dir(tmp_path: Path) -> Path:
    artifact_dir = tmp_path / "artifact"
    cmd = [
        sys.executable,
        str(_NEW_TARGET),
        "--artifact-dir", str(artifact_dir),
        "--model-id", "test-model",
        "--hardware", "H100",
        "--dtype", "fp8",
        "--tp", "1",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, f"new_target.py failed: {result.stdout} / {result.stderr}"
    return artifact_dir


def test_scaffold_emits_schema_4_1(scaffolded_artifact_dir: Path):
    state = json.loads((scaffolded_artifact_dir / "state.json").read_text(encoding="utf-8"))

    assert state["campaign"]["schema_version"] == "4.2"


def test_state_schema_defines_v4_1_contract_blocks():
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    campaign = schema["properties"]["campaign"]["properties"]
    config = campaign["config"]["properties"]

    assert "4.1" in campaign["schema_version"]["enum"]
    assert "optimization_category" in schema["$defs"]
    assert schema["$defs"]["optimization_category"]["enum"] == [
        "kernel_replacement",
        "kernel_fusion",
        "dispatch_optimization",
        "custom_kernel",
        "weight_layout_transform",
        "compute_graph_pass",
        "execution_pipeline_restructuring",
        "communication_strategy",
        "attention_kv_layout",
    ]
    assert config["min_e2e_improvement_pct"]["default"] == 0.5
    assert "lossless_e2e_deflation_factor" not in config
    assert "lossy_quant_e2e_deflation_factor" not in config
    assert "ceiling_detection" not in config
    assert "decision_tree" not in config
    assert "routing_recommendation" not in config


def test_scaffold_uses_current_min_e2e_default_without_deflation(scaffolded_artifact_dir: Path):
    state = json.loads((scaffolded_artifact_dir / "state.json").read_text(encoding="utf-8"))

    config = state["campaign"]["config"]
    assert config["min_e2e_improvement_pct"] == 0.5
    assert "lossless_e2e_deflation_factor" not in config
    assert "lossy_quant_e2e_deflation_factor" not in config


def test_target_json_carries_schema_derived_minimum(scaffolded_artifact_dir: Path):
    target = json.loads((scaffolded_artifact_dir / "target.json").read_text(encoding="utf-8"))

    assert target["gating"]["min_e2e_improvement_pct"] == 0.5


def test_scaffold_freezes_max_num_batched_tokens(tmp_path: Path):
    """Neuron needs an explicit prefill chunk budget: the Pumice Neuron runner
    defaults kv_segment_size to max_num_batched_tokens (= max_model_len when
    the flag is omitted) and only 512/1024/2048/4096 compile."""
    artifact_dir = tmp_path / "neuron"
    result = subprocess.run(
        [
            sys.executable,
            str(_NEW_TARGET),
            "--artifact-dir",
            str(artifact_dir),
            "--model-id",
            "test-model",
            "--max-model-len",
            "8192",
            "--max-num-seqs",
            "8",
            "--max-num-batched-tokens",
            "4096",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["extra_args"] == [
        "--max-num-seqs", "8", "--max-num-batched-tokens", "4096",
    ]


def test_scaffold_omits_max_num_batched_tokens_by_default(tmp_path: Path):
    artifact_dir = tmp_path / "plain"
    result = subprocess.run(
        [sys.executable, str(_NEW_TARGET), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert "--max-num-batched-tokens" not in target["bench"]["extra_args"]


# v1 scaffolds only pumice-cuda: new_target.py calls
# runtime_ops.require_supported_profile(), which refuses every other profile
# (conventions §6). The Neuron branches of new_target.py stay in place, so the
# `neuron` tests below keep them tested: this shim opens the gate for the
# child process and then runs the unchanged script. They are deselected by
# default (tests/conftest.py); run them with `-m neuron`.
_NEURON_GATE_SHIM = (
    "import os, runpy, sys; "
    "sys.path.insert(0, os.path.dirname(sys.argv[1])); "
    "import runtime_ops; "
    "runtime_ops.require_supported_profile = lambda profile=None, env=None: profile; "
    "sys.argv = sys.argv[1:]; "
    "runpy.run_path(sys.argv[0], run_name='__main__')"
)


def _neuron_cmd() -> list[str]:
    return [sys.executable, "-c", _NEURON_GATE_SHIM, str(_NEW_TARGET)]


def _neuron_env() -> dict[str, str]:
    env = dict(os.environ)
    env["AMMO_RUNTIME_PROFILE"] = "pumice-neuron"
    env["NEURON_RT_VISIBLE_CORES"] = "0-7"
    env.pop("PUMICE_RUST_EMIT_FULL_BLOCK_TABLES", None)
    return env


@pytest.mark.neuron
def test_neuron_scaffold_freezes_prefill_budget_when_context_exceeds_kernel_max(tmp_path: Path):
    """max_model_len 8192 on pumice-neuron with no flag: the scaffold must freeze
    4096 itself (the 2026-09-24 trn2 campaign lost three launches to
    kv_segment_size=8192) and say so on stderr."""
    artifact_dir = tmp_path / "neuron-auto"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--max-model-len", "8192", "--tp", "8"],
        capture_output=True, text=True, env=_neuron_env(),
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    extra = target["bench"]["extra_args"]
    assert extra[extra.index("--max-num-batched-tokens") + 1] == "4096"
    assert "froze --max-num-batched-tokens 4096" in result.stderr


@pytest.mark.neuron
def test_neuron_scaffold_keeps_explicit_prefill_budget(tmp_path: Path):
    artifact_dir = tmp_path / "neuron-explicit"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--max-model-len", "8192",
         "--max-num-batched-tokens", "2048"],
        capture_output=True, text=True, env=_neuron_env(),
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    extra = target["bench"]["extra_args"]
    assert extra[extra.index("--max-num-batched-tokens") + 1] == "2048"
    assert "froze" not in result.stderr


@pytest.mark.neuron
def test_neuron_scaffold_leaves_short_context_alone(tmp_path: Path):
    artifact_dir = tmp_path / "neuron-short"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--max-model-len", "4096"],
        capture_output=True, text=True, env=_neuron_env(),
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert "--max-num-batched-tokens" not in target["bench"]["extra_args"]


_FULL_BT = "PUMICE_RUST_EMIT_FULL_BLOCK_TABLES"


@pytest.mark.neuron
def test_neuron_scaffold_freezes_full_block_tables(tmp_path: Path):
    """Without this var the Neuron engine hands the kernel an all-empty block
    table on steady-state decode, and the first measured wave crashes (the
    2026-09-24 trn2 campaign, Defect 5)."""
    artifact_dir = tmp_path / "neuron-bt"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--tp", "8"],
        capture_output=True, text=True, env=_neuron_env(),
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["baseline_env"] == {_FULL_BT: "1"}
    assert f"pumice-neuron: set {_FULL_BT}=1 in bench.baseline_env" in result.stderr
    assert "WARNING: pumice-neuron: kept" not in result.stderr


@pytest.mark.neuron
def test_neuron_scaffold_keeps_explicit_full_block_tables_and_warns(tmp_path: Path):
    artifact_dir = tmp_path / "neuron-bt-off"
    env = _neuron_env()
    env[_FULL_BT] = "0"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--tp", "8"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["baseline_env"] == {_FULL_BT: "0"}
    assert f"WARNING: pumice-neuron: kept the caller's {_FULL_BT}='0'" in result.stderr
    assert 'Neuron needs "1"' in result.stderr


@pytest.mark.neuron
def test_neuron_scaffold_explicit_one_needs_no_warning(tmp_path: Path):
    artifact_dir = tmp_path / "neuron-bt-on"
    env = _neuron_env()
    env[_FULL_BT] = "1"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--tp", "8"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["baseline_env"] == {_FULL_BT: "1"}
    assert "WARNING: pumice-neuron" not in result.stderr


@pytest.mark.parametrize("profile", ["vllm-cuda", "pumice-neuron", "vllm-neuron", "vllm-tpu"])
def test_scaffold_refuses_every_profile_but_pumice_cuda(tmp_path: Path, profile):
    env = dict(os.environ)
    env["AMMO_RUNTIME_PROFILE"] = profile
    env["NEURON_RT_VISIBLE_CORES"] = "0-7"
    artifact_dir = tmp_path / f"refused-{profile}"
    result = _run_new_target(artifact_dir, [], env=env)
    assert result.returncode != 0
    assert profile in result.stderr
    assert not artifact_dir.exists()


@pytest.mark.parametrize("profile", [None, "", "pumice-cuda"])
def test_cuda_scaffold_never_sets_full_block_tables(tmp_path: Path, profile):
    env = dict(os.environ)
    env.pop("AMMO_RUNTIME_PROFILE", None)
    if profile is not None:
        env["AMMO_RUNTIME_PROFILE"] = profile
    env[_FULL_BT] = "1"
    artifact_dir = tmp_path / f"cuda-bt-{profile}"
    result = _run_new_target(artifact_dir, [], env=env)
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["baseline_env"] == {}
    assert _FULL_BT not in result.stderr


@pytest.mark.neuron
def test_neuron_full_block_tables_reach_both_bench_arms(tmp_path: Path):
    """run_pumice_grpc_bench strips inherited PUMICE_* vars, so only
    bench.baseline_env reaches the server. It must land in the baseline arm and
    in the opt arm, and a stale shell value must not win."""
    import dataclasses

    import run_pumice_grpc_bench as bench

    artifact_dir = tmp_path / "neuron-bt-arms"
    result = subprocess.run(
        [*_neuron_cmd(), "--artifact-dir", str(artifact_dir),
         "--model-id", "test-model", "--tp", "8"],
        capture_output=True, text=True, env=_neuron_env(),
    )
    assert result.returncode == 0, result.stderr
    spec = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    tgt = bench.parse_target_spec(spec, require_opt_env=False)
    tgt = dataclasses.replace(tgt, opt_env={"PUMICE_MY_OPT": "1"})
    runs = bench.build_run_specs(tgt, inherited_env={_FULL_BT: "0", "PATH": "/bin"})
    assert runs["baseline"].env[_FULL_BT] == "1"
    assert runs["opt"].env[_FULL_BT] == "1"
    assert runs["opt"].env["PUMICE_MY_OPT"] == "1"


def test_cuda_scaffold_never_adds_prefill_budget(tmp_path: Path):
    artifact_dir = tmp_path / "cuda-long"
    result = _run_new_target(artifact_dir, ["--max-model-len", "8192"])
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert "--max-num-batched-tokens" not in target["bench"]["extra_args"]


def test_scaffold_freezes_prompt_source_when_given(tmp_path: Path):
    artifact_dir = tmp_path / "raw"
    result = _run_new_target(artifact_dir, ["--prompt-source", "raw"])
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["prompt_source"] == "raw"


def test_scaffold_omits_prompt_source_by_default(tmp_path: Path):
    artifact_dir = tmp_path / "default-prompt"
    result = _run_new_target(artifact_dir, [])
    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert "prompt_source" not in target["bench"]


def test_scaffold_rejects_random_prompt_source(tmp_path: Path):
    result = _run_new_target(tmp_path / "rnd", ["--prompt-source", "random"])
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


def test_scaffold_freezes_max_num_seqs_and_multi_workload(tmp_path: Path):
    artifact_dir = tmp_path / "matrix"
    result = subprocess.run(
        [
            sys.executable,
            str(_NEW_TARGET),
            "--artifact-dir",
            str(artifact_dir),
            "--model-id",
            "test-model",
            "--batch-sizes",
            "1",
            "8",
            "--isl-osl",
            "64:512,2048:256",
            "--max-num-seqs",
            "32",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    target = json.loads((artifact_dir / "target.json").read_text(encoding="utf-8"))
    assert target["bench"]["extra_args"][-2:] == ["--max-num-seqs", "32"]
    assert target["workload"]["workload_matrix"] == [
        {"input_len": 64, "output_len": 512, "batch_size": 1},
        {"input_len": 64, "output_len": 512, "batch_size": 8},
        {"input_len": 2048, "output_len": 256, "batch_size": 1},
        {"input_len": 2048, "output_len": 256, "batch_size": 8},
    ]


def test_scaffold_rejects_unresolved_auto_before_writing(tmp_path: Path):
    artifact_dir = tmp_path / "auto"
    result = subprocess.run(
        [
            sys.executable,
            str(_NEW_TARGET),
            "--artifact-dir",
            str(artifact_dir),
            "--model-id",
            "definitely-not-cached/model",
            "--max-model-len",
            "auto",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "supply the concrete model limit" in result.stderr
    assert not (artifact_dir / "state.json").exists()


def test_programmatic_state_and_target_use_same_target_fields_threshold(tmp_path: Path):
    fields = new_target.TargetFields(
        model_id="model",
        hardware="H100",
        dtype="fp8",
        tp=1,
        ep=1,
        max_model_len=4096,
        input_len=64,
        output_len=128,
        batch_sizes=[1, 8],
        num_iters=10,
        noise_tolerance_pct=0.75,
        catastrophic_regression_pct=4.0,
        min_e2e_improvement_pct=1.25,
    )

    state = new_target._state_json(fields, tmp_path)
    target = new_target._target_json(fields, tmp_path)

    assert state["campaign"]["config"]["min_e2e_improvement_pct"] == 1.25
    assert target["gating"]["min_e2e_improvement_pct"] == 1.25


def test_scaffold_rejects_schema_invalid_threshold_before_writing(tmp_path: Path):
    artifact_dir = tmp_path / "invalid"

    result = subprocess.run(
        [
            sys.executable,
            str(_NEW_TARGET),
            "--artifact-dir",
            str(artifact_dir),
            "--min-e2e-improvement",
            "-1",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "violates schema" in result.stderr
    assert not (artifact_dir / "state.json").exists()


def test_scaffold_uses_claude_num_iters_default(scaffolded_artifact_dir: Path):
    target = json.loads((scaffolded_artifact_dir / "target.json").read_text(encoding="utf-8"))

    assert target["workload"]["num_iters"] == 10


def test_scaffold_creates_rounds_1_dir(scaffolded_artifact_dir: Path):
    assert (scaffolded_artifact_dir / "rounds" / "1").is_dir()


def test_scaffold_creates_profiling_subdirs(scaffolded_artifact_dir: Path):
    base = scaffolded_artifact_dir / "rounds" / "1" / "profiling"
    for sub in ("nsys", "ncu", "neuron"):
        assert (base / sub).is_dir(), f"missing rounds/1/profiling/{sub}/"
    assert not (base / "probe").exists()
    assert not (base / "torch_profile").exists()


def test_scaffold_creates_sweeps_subdirs(scaffolded_artifact_dir: Path):
    sweeps = scaffolded_artifact_dir / "rounds" / "1" / "sweeps"
    for sub in ("json", "logs", "status"):
        assert (sweeps / "baseline" / sub).is_dir(), f"missing baseline/{sub}/"
    for slot in ("opt", "integration", "golden_capture"):
        assert (sweeps / slot).is_dir(), f"missing sweeps/{slot}/"


def test_scaffold_creates_debate_subdirs(scaffolded_artifact_dir: Path):
    base = scaffolded_artifact_dir / "rounds" / "1" / "debate"
    for sub in ("proposals", "micro_experiments", "monitor_audits"):
        assert (base / sub).is_dir(), f"missing debate/{sub}/"


def test_scaffold_creates_round_scoped_work_dirs(scaffolded_artifact_dir: Path):
    for sub in ("mining", "tracks", "audits", "_archive"):
        assert (scaffolded_artifact_dir / "rounds" / "1" / sub).is_dir()


def test_scaffold_creates_blockers_at_root(scaffolded_artifact_dir: Path):
    assert (scaffolded_artifact_dir / "blockers").is_dir()
    assert not (scaffolded_artifact_dir / "rounds" / "1" / "blockers").exists()


def test_scaffold_no_legacy_dirs(scaffolded_artifact_dir: Path):
    for legacy in ("investigation", "runs", "nsys", "e2e_latency", "monitoring", "tracks", "debate", "mining", "audits"):
        assert not (scaffolded_artifact_dir / legacy).exists(), (
            f"legacy root dir {legacy}/ should not be scaffolded"
        )


def test_no_constraints_md_at_root(scaffolded_artifact_dir: Path):
    assert not (scaffolded_artifact_dir / "constraints.md").exists()


def _run_new_target(artifact_dir: Path, extra_args: list[str], env: dict[str, str] | None = None):
    cmd = [
        sys.executable,
        str(_NEW_TARGET),
        "--artifact-dir", str(artifact_dir),
        "--model-id", "test-model",
        "--hardware", "H100",
        "--dtype", "fp8",
        "--tp", "1",
        *extra_args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_scaffold_all_flags_emits_no_placeholder(tmp_path: Path):
    artifact_dir = tmp_path / "full"
    result = _run_new_target(artifact_dir, ["--gpu-model", "NVIDIA H100 80GB HBM3"])

    assert result.returncode == 0, result.stderr
    state_text = (artifact_dir / "state.json").read_text(encoding="utf-8")
    assert "<FILL_ME>" not in state_text
    state = json.loads(state_text)
    assert state["gpu_resources"]["gpu_model"] == "NVIDIA H100 80GB HBM3"


def test_scaffold_detects_gpu_model_from_nvidia_smi(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_smi = fake_bin / "nvidia-smi"
    fake_smi.write_text("#!/bin/sh\necho 'NVIDIA L40S'\n", encoding="utf-8")
    fake_smi.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    artifact_dir = tmp_path / "detected"
    result = _run_new_target(artifact_dir, [], env=env)

    assert result.returncode == 0, result.stderr
    state_text = (artifact_dir / "state.json").read_text(encoding="utf-8")
    assert "<FILL_ME>" not in state_text
    state = json.loads(state_text)
    assert state["gpu_resources"]["gpu_model"] == "NVIDIA L40S"


def test_scaffold_warns_and_keeps_placeholder_without_detection(tmp_path: Path):
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)

    artifact_dir = tmp_path / "undetected"
    result = _run_new_target(artifact_dir, [], env=env)

    assert result.returncode == 0, result.stderr
    state = json.loads((artifact_dir / "state.json").read_text(encoding="utf-8"))
    assert state["gpu_resources"]["gpu_model"] == "<FILL_ME>"
    assert "gpu_model" in result.stderr
    assert "WARNING" in result.stderr


def test_scaffold_detection_scopes_query_to_cuda_visible_devices(tmp_path: Path):
    # nvidia-smi ignores CUDA_VISIBLE_DEVICES, so on a heterogeneous host an
    # unscoped query names a GPU the session does not own. The fake below
    # models GPU0/1 = A100, GPU2/3 = H100 and honors -i scoping.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_smi = fake_bin / "nvidia-smi"
    fake_smi.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"-i 2,3"*) echo "NVIDIA H100 80GB HBM3"; echo "NVIDIA H100 80GB HBM3";;\n'
        '  *"-i"*) echo "bad device index" >&2; exit 6;;\n'
        '  *) echo "NVIDIA A100-SXM4-40GB"; echo "NVIDIA A100-SXM4-40GB";\n'
        '     echo "NVIDIA H100 80GB HBM3"; echo "NVIDIA H100 80GB HBM3";;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_smi.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CUDA_VISIBLE_DEVICES"] = "2,3"

    artifact_dir = tmp_path / "scoped"
    result = _run_new_target(artifact_dir, [], env=env)

    assert result.returncode == 0, result.stderr
    state = json.loads((artifact_dir / "state.json").read_text(encoding="utf-8"))
    assert state["gpu_resources"]["gpu_model"] == "NVIDIA H100 80GB HBM3"


def test_scaffold_bad_visible_devices_falls_back_to_placeholder(tmp_path: Path):
    # A CVD the driver rejects must fail closed to the placeholder (caught by
    # verify_validation_gates.py), never to a confidently-wrong model name.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_smi = fake_bin / "nvidia-smi"
    fake_smi.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"-i"*) echo "No devices were found" >&2; exit 6;;\n'
        '  *) echo "NVIDIA A100-SXM4-40GB";;\n'
        "esac\n",
        encoding="utf-8",
    )
    fake_smi.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CUDA_VISIBLE_DEVICES"] = "99"

    artifact_dir = tmp_path / "badcvd"
    result = _run_new_target(artifact_dir, [], env=env)

    assert result.returncode == 0, result.stderr
    state = json.loads((artifact_dir / "state.json").read_text(encoding="utf-8"))
    assert state["gpu_resources"]["gpu_model"] == "<FILL_ME>"
    assert "WARNING" in result.stderr


def test_scaffold_whitespace_gpu_model_flag_falls_through(tmp_path: Path):
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)

    artifact_dir = tmp_path / "wsflag"
    result = _run_new_target(artifact_dir, ["--gpu-model", "   "], env=env)

    assert result.returncode == 0, result.stderr
    state = json.loads((artifact_dir / "state.json").read_text(encoding="utf-8"))
    assert state["gpu_resources"]["gpu_model"] == "<FILL_ME>"
    assert "WARNING" in result.stderr


# ---------------------------------------------------------------------------
# WS4.6: --max-model-len cap (b3d9fa6b re-port) + Pumice target fields
# ---------------------------------------------------------------------------


class TestWorkloadLenCap:
    def test_cap_is_power_of_two_covering_workload(self):
        cap = new_target._workload_len_cap(8192, 512, None)
        assert cap == 16384

    def test_cap_floor_is_4096(self):
        cap = new_target._workload_len_cap(64, 64, None)
        assert cap == 4096

    def test_cap_reads_matrix_pairs(self):
        matrix = [
            {"input_len": 1024, "output_len": 128, "batch_size": 1},
            {"input_len": 4096, "output_len": 512, "batch_size": 4},
        ]
        cap = new_target._workload_len_cap(64, 64, matrix)
        assert cap == 8192


class TestScaffoldEmitsPumiceFields(object):
    def test_prefix_cache_regime_written(self, tmp_path: Path):
        artifact_dir = tmp_path / "pin"
        cmd = [
            sys.executable, str(_NEW_TARGET),
            "--artifact-dir", str(artifact_dir),
            "--model-id", "test-model", "--hardware", "L40S",
            "--dtype", "fp8", "--tp", "1",
            "--prefix-cache-regime", "off",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        target = json.loads((artifact_dir / "target.json").read_text())
        assert target["target"]["prefix_cache_regime"] == "off"

    def test_correctness_sample_count_written(self, tmp_path: Path):
        artifact_dir = tmp_path / "csc"
        cmd = [
            sys.executable, str(_NEW_TARGET),
            "--artifact-dir", str(artifact_dir),
            "--model-id", "test-model", "--hardware", "L40S",
            "--dtype", "fp8", "--tp", "1",
            "--correctness-sample-count", "200",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        target = json.loads((artifact_dir / "target.json").read_text())
        assert target["target"]["correctness_sample_count"] == 200

    def test_absent_fields_are_omitted(self, tmp_path: Path):
        artifact_dir = tmp_path / "omit"
        cmd = [
            sys.executable, str(_NEW_TARGET),
            "--artifact-dir", str(artifact_dir),
            "--model-id", "test-model", "--hardware", "L40S",
            "--dtype", "fp8", "--tp", "1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        target = json.loads((artifact_dir / "target.json").read_text())
        assert "prefix_cache_regime" not in target["target"]
        assert "correctness_sample_count" not in target["target"]

    def test_explicit_max_model_len_never_capped(self, tmp_path: Path):
        artifact_dir = tmp_path / "nocap"
        cmd = [
            sys.executable, str(_NEW_TARGET),
            "--artifact-dir", str(artifact_dir),
            "--model-id", "test-model", "--hardware", "L40S",
            "--dtype", "fp8", "--tp", "1",
            "--max-model-len", "200000",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        target = json.loads((artifact_dir / "target.json").read_text())
        assert target["target"]["max_model_len"] == 200000
