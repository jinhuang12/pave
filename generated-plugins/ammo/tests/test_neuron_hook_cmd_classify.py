"""Accelerator command-classification pins for CUDA and AWS Neuron."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import hook_cmd_classify as classify  # noqa: E402


@pytest.mark.parametrize(
    "command",
    [
        "neuron-profile capture -- python bench.py",
        "neuron-profiler profile python bench.py",
        "neuron-explorer capture -n model.neff -s profile.ntff",
        "nki run kernel.py",
        "python run_nki_kernel.py",
        "python -m torch_neuronx.trace model.py",
        "python benchmark_neff.py",
        "NEURON_VISIBLE_DEVICES=0-7 vllm serve model --tensor-parallel-size 8",
        "jq -r .model target.json | xargs neuron-profile capture",
        "jq -r .model target.json | xargs -n 1 neuron-profile capture",
        'bash -lc "neuron-profile capture -- python bench.py"',
        "sudo neuron-explorer capture -n model.neff -s profile.ntff",
        "sudo -u session_user neuron-explorer capture -n model.neff -s profile.ntff",
        "timeout 60 neuron-profile capture -- python bench.py",
        "time ncu --set full python bench.py",
    ],
)
def test_neuron_execution_and_profile_commands_are_accelerator_heavy(command):
    assert classify.command_invokes_accelerator_heavy_tool(command) is True


@pytest.mark.parametrize(
    "command",
    [
        "neuron-ls --json-output",
        "rg 'neuron-profile|nki|neuron-explorer' docs",
        "cat profile_summary.json",
    ],
)
def test_neuron_inspection_commands_do_not_require_a_reservation(command):
    assert classify.command_invokes_accelerator_heavy_tool(command) is False


def test_legacy_gpu_classifier_alias_keeps_cuda_and_gains_neuron():
    assert classify.command_invokes_gpu_heavy_tool("ncu python bench.py") is True
    assert classify.command_invokes_gpu_heavy_tool("neuron-profile capture") is True


def test_accelerator_cli_mode_exposes_shared_verdict(capsys):
    assert classify.main(["--mode", "accelerator", "neuron-profile capture"]) == 0
    assert capsys.readouterr().out.strip() == "ACCELERATOR"
    assert classify.main(["--mode", "accelerator", "neuron-ls --json-output"]) == 1
    assert capsys.readouterr().out.strip() == "NOT_ACCELERATOR"
