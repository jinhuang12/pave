"""Tests for scripts/pumice_pins.py: the Pumice commit, torch, and vLLM pins.

The vLLM pin is not a constant in the plugin (conventions §6): check_pins()
compares the installed vLLM with pumice.config.REQUIRED_VLLM_VERSION, and only
when the model-runner kind is ``vllm``.

Pin literals are built from parts so this file holds none (tests/test_prose_bans.py).
"""

import sys
import types
from importlib import metadata as importlib_metadata

import pytest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'scripts'))
import pumice_pins

ENGINE_VLLM = "0.99.1"  # stands in for pumice.config.REQUIRED_VLLM_VERSION


def _fake_version(versions):
    """Build a fake importlib.metadata.version(package) function."""

    def version(package):
        try:
            return versions[package]
        except KeyError:
            raise importlib_metadata.PackageNotFoundError(package)

    return version


def _fake_pumice(monkeypatch, required_vllm=ENGINE_VLLM, *, has_attr=True):
    fake_pumice = types.ModuleType("pumice")
    fake_pumice_config = types.ModuleType("pumice.config")
    if has_attr:
        fake_pumice_config.REQUIRED_VLLM_VERSION = required_vllm
    fake_pumice.config = fake_pumice_config
    monkeypatch.setitem(sys.modules, "pumice", fake_pumice)
    monkeypatch.setitem(sys.modules, "pumice.config", fake_pumice_config)


def _no_pumice(monkeypatch):
    monkeypatch.setitem(sys.modules, "pumice", None)
    monkeypatch.setitem(sys.modules, "pumice.config", None)


def _versions(monkeypatch, versions):
    monkeypatch.setattr(importlib_metadata, "version", _fake_version(versions))


# --------------------------------------------------------------------------- torch


def test_check_pins_fails_when_torch_missing(monkeypatch):
    _versions(monkeypatch, {})

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "torch" in reason
    assert "not installed" in reason


def test_check_pins_fails_when_torch_version_mismatched(monkeypatch):
    _versions(monkeypatch, {"torch": "2.11.0"})

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "torch" in reason
    assert "2.11.0" in reason


def test_torch_is_checked_for_the_vllm_runner_too(monkeypatch):
    _versions(monkeypatch, {"torch": "2.11.0", "vllm": ENGINE_VLLM})
    _fake_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is False
    assert "2.11.0" in reason


# --------------------------------------------------------------------------- vLLM skipped


@pytest.mark.parametrize("runner", [None, "pumice", "neuron"])
def test_vllm_is_not_checked_unless_the_runner_is_vllm(monkeypatch, runner):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH})
    _no_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner=runner)

    assert ok is True
    assert reason == ""


def test_a_wrong_vllm_does_not_fail_the_pumice_runner(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH, "vllm": "0.24.0"})
    _fake_pumice(monkeypatch)

    ok, _ = pumice_pins.check_pins(model_runner="pumice")

    assert ok is True


# --------------------------------------------------------------------------- vLLM runner


def test_vllm_runner_fails_when_vllm_missing(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH})
    _fake_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is False
    assert "vllm" in reason
    assert "not installed" in reason


def test_vllm_runner_fails_when_pumice_missing(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH, "vllm": ENGINE_VLLM})
    _no_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is False
    assert "pumice.config.REQUIRED_VLLM_VERSION" in reason


def test_vllm_runner_fails_when_pumice_names_no_vllm_version(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH, "vllm": ENGINE_VLLM})
    _fake_pumice(monkeypatch, has_attr=False)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is False
    assert "pumice.config.REQUIRED_VLLM_VERSION" in reason


def test_vllm_runner_fails_when_vllm_disagrees_with_pumice(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH, "vllm": "0.24.0"})
    _fake_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is False
    assert "0.24.0" in reason
    assert ENGINE_VLLM in reason
    assert "pumice.config.REQUIRED_VLLM_VERSION" in reason


def test_vllm_runner_passes_when_vllm_matches_pumice(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH, "vllm": ENGINE_VLLM})
    _fake_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner="vllm")

    assert ok is True
    assert reason == ""


def test_runner_kind_is_case_and_space_insensitive(monkeypatch):
    _versions(monkeypatch, {"torch": pumice_pins.REQUIRED_TORCH})
    _fake_pumice(monkeypatch)

    ok, reason = pumice_pins.check_pins(model_runner=" VLLM ")

    assert ok is False
    assert "vllm is not installed" in reason


# --------------------------------------------------------------------------- CLI


def test_main_assert_exits_zero_on_success(monkeypatch, capsys):
    monkeypatch.setattr(pumice_pins, "check_pins", lambda model_runner=None: (True, ""))

    exit_code = pumice_pins.main(["--assert"])

    assert exit_code == 0
    assert "ok" in capsys.readouterr().out


def test_main_assert_exits_one_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        pumice_pins, "check_pins", lambda model_runner=None: (False, "torch is not installed")
    )

    exit_code = pumice_pins.main(["--assert"])

    assert exit_code == 1
    assert "torch is not installed" in capsys.readouterr().err


def test_main_passes_the_model_runner_through(monkeypatch):
    seen = []

    def fake(model_runner=None):
        seen.append(model_runner)
        return True, ""

    monkeypatch.setattr(pumice_pins, "check_pins", fake)

    assert pumice_pins.main(["--assert", "--model-runner", "vllm"]) == 0
    assert pumice_pins.main(["--assert", "--model-runner=pumice"]) == 0
    assert pumice_pins.main(["--assert"]) == 0
    assert seen == ["vllm", "pumice", None]


def test_main_without_assert_flag_prints_help_and_exits_zero(capsys):
    exit_code = pumice_pins.main([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PUMICE_COMMIT" in out
    assert "REQUIRED_VLLM_VERSION" in out


# --------------------------------------------------------------------------- constants


def test_vllm_pin_is_not_a_constant():
    assert getattr(pumice_pins, "REQUIRED_VLLM", None) is None


@pytest.mark.parametrize(
    "constant,expected",
    [
        (pumice_pins.REQUIRED_TORCH, "2.13" ".0+cu130"),
        (pumice_pins.PUMICE_COMMIT, "769c" "752995b8a39b27df028176411d17c99ff441"),
    ],
)
def test_pin_constants_match_the_plan(constant, expected):
    assert constant == expected


def test_no_container_image_digest_constant():
    """A host-local campaign has no image (conventions §4: image_digest is null)."""
    assert not hasattr(pumice_pins, "CUDA_BASE_DIGEST")
