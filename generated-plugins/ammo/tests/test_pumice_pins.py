"""Tests for shared/pumice_pins.py: the vLLM and torch version pins."""

import sys
import types
from importlib import metadata as importlib_metadata

import pytest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'scripts'))
import pumice_pins


def _fake_version(versions):
    """Build a fake importlib.metadata.version(package) function."""

    def version(package):
        try:
            return versions[package]
        except KeyError:
            raise importlib_metadata.PackageNotFoundError(package)

    return version


def test_check_pins_fails_when_vllm_missing(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata, "version", _fake_version({"torch": pumice_pins.REQUIRED_TORCH})
    )

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "vllm" in reason
    assert "not installed" in reason


def test_check_pins_fails_when_vllm_version_mismatched(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        _fake_version({"vllm": "0.24.0", "torch": pumice_pins.REQUIRED_TORCH}),
    )

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "vllm" in reason
    assert "0.24.0" in reason


def test_check_pins_fails_when_torch_missing(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata, "version", _fake_version({"vllm": pumice_pins.REQUIRED_VLLM})
    )

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "torch" in reason
    assert "not installed" in reason


def test_check_pins_fails_when_torch_version_mismatched(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        _fake_version({"vllm": pumice_pins.REQUIRED_VLLM, "torch": "2.11.0"}),
    )

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "torch" in reason
    assert "2.11.0" in reason


def test_check_pins_passes_when_versions_match_and_pumice_absent(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        _fake_version(
            {"vllm": pumice_pins.REQUIRED_VLLM, "torch": pumice_pins.REQUIRED_TORCH}
        ),
    )
    monkeypatch.setitem(sys.modules, "pumice", None)
    monkeypatch.setitem(sys.modules, "pumice.config", None)

    ok, reason = pumice_pins.check_pins()

    assert ok is True
    assert reason == ""


def test_check_pins_passes_when_pumice_agrees(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        _fake_version(
            {"vllm": pumice_pins.REQUIRED_VLLM, "torch": pumice_pins.REQUIRED_TORCH}
        ),
    )

    fake_pumice = types.ModuleType("pumice")
    fake_pumice_config = types.ModuleType("pumice.config")
    fake_pumice_config.REQUIRED_VLLM_VERSION = pumice_pins.REQUIRED_VLLM
    fake_pumice.config = fake_pumice_config
    monkeypatch.setitem(sys.modules, "pumice", fake_pumice)
    monkeypatch.setitem(sys.modules, "pumice.config", fake_pumice_config)

    ok, reason = pumice_pins.check_pins()

    assert ok is True
    assert reason == ""


def test_check_pins_fails_when_pumice_disagrees(monkeypatch):
    monkeypatch.setattr(
        importlib_metadata,
        "version",
        _fake_version(
            {"vllm": pumice_pins.REQUIRED_VLLM, "torch": pumice_pins.REQUIRED_TORCH}
        ),
    )

    fake_pumice = types.ModuleType("pumice")
    fake_pumice_config = types.ModuleType("pumice.config")
    fake_pumice_config.REQUIRED_VLLM_VERSION = "0.24.0"
    fake_pumice.config = fake_pumice_config
    monkeypatch.setitem(sys.modules, "pumice", fake_pumice)
    monkeypatch.setitem(sys.modules, "pumice.config", fake_pumice_config)

    ok, reason = pumice_pins.check_pins()

    assert ok is False
    assert "pumice.config.REQUIRED_VLLM_VERSION" in reason


def test_main_assert_exits_zero_on_success(monkeypatch, capsys):
    monkeypatch.setattr(pumice_pins, "check_pins", lambda: (True, ""))

    exit_code = pumice_pins.main(["--assert"])

    assert exit_code == 0
    assert "ok" in capsys.readouterr().out


def test_main_assert_exits_one_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(pumice_pins, "check_pins", lambda: (False, "vllm is not installed"))

    exit_code = pumice_pins.main(["--assert"])

    assert exit_code == 1
    assert "vllm is not installed" in capsys.readouterr().err


def test_main_without_assert_flag_prints_help_and_exits_zero(capsys):
    exit_code = pumice_pins.main([])

    assert exit_code == 0
    assert "REQUIRED_VLLM" in capsys.readouterr().out


@pytest.mark.parametrize(
    "constant,expected",
    [
        (pumice_pins.REQUIRED_VLLM, "0.30.0"),
        (pumice_pins.REQUIRED_TORCH, "2.13.0+cu130"),
        (
            pumice_pins.CUDA_BASE_DIGEST,
            "sha256:970ca980451d9b6590e0e507502b341551ff8de5d09713b142972d14f40cb79f",
        ),
        (pumice_pins.PUMICE_COMMIT, "9d126713c03dcc88883b85c3d3a857b89982c957"),
    ],
)
def test_pin_constants_match_the_plan(constant, expected):
    assert constant == expected
