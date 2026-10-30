"""Preflight check "plugin python deps": the engine venv carries the plugin's packages.

scripts/ammo runs every plugin script with the engine python, so the engine
venv must import campaign_paths.PLUGIN_PYTHON_DEPS as well as pumice. The
check names every missing package and the command that installs it.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_PREFLIGHT = _SCRIPTS_DIR / "preflight_pumice.py"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_spec = importlib.util.spec_from_file_location("preflight_pumice_plugin_deps", str(_PREFLIGHT))
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight_pumice_plugin_deps"] = preflight
_spec.loader.exec_module(preflight)

import campaign_paths  # noqa: E402


def _without(monkeypatch, *missing: str) -> None:
    """Make `import <name>` raise ImportError for each name, as in a bare venv."""
    for name in missing:
        monkeypatch.setitem(sys.modules, name, None)


def test_all_present_passes():
    label, ok, detail = preflight._check_plugin_python_deps()
    assert label == "plugin python deps"
    assert ok, detail
    for name in campaign_paths.PLUGIN_PYTHON_DEPS:
        assert name in detail


def test_missing_packages_fail_naming_each_and_the_install_command(monkeypatch):
    _without(monkeypatch, "jsonschema", "yaml")
    label, ok, detail = preflight._check_plugin_python_deps()
    assert not ok
    assert "jsonschema" in detail and "yaml" in detail
    assert "httpx" not in detail.split("install")[0]
    assert f"{sys.executable} -m pip install jsonschema pyyaml" in detail
    assert f"uv pip install --python {sys.executable} jsonschema pyyaml" in detail


def test_a_broken_install_counts_as_missing(monkeypatch):
    real = importlib.import_module

    def broken(name, *args, **kwargs):
        if name == "psutil":
            raise OSError("bad shared library")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(preflight.importlib, "import_module", broken)
    label, ok, detail = preflight._check_plugin_python_deps()
    assert not ok
    assert "psutil" in detail and "bad shared library" in detail


def test_the_check_runs_in_main_before_the_pins_check():
    assert "_check_plugin_python_deps" in preflight.CHECKS
    assert preflight.CHECKS.index("_check_plugin_python_deps") > preflight.CHECKS.index(
        "_check_pumice_imports")


def test_main_reports_the_missing_packages(monkeypatch, capsys, tmp_path):
    """The failure reaches the preflight output and the exit code, beside the other checks."""
    _without(monkeypatch, "jsonschema")
    monkeypatch.chdir(tmp_path)
    for name in list(preflight.CHECKS) + list(preflight.LATE_CHECKS):
        if name != "_check_plugin_python_deps":
            monkeypatch.setattr(preflight, name, lambda *a, label=name: (label, True, "ok"))
    monkeypatch.setattr(preflight, "_check_pins", lambda *a: ("pins", True, "ok"))
    monkeypatch.setattr(preflight, "_check_model_path", lambda *a: ("model path", True, "ok"))
    monkeypatch.setattr(preflight.campaign_paths, "apply_env", lambda *a, **k: None)
    assert preflight.main(["--skip-server-flags"]) == 1
    out = capsys.readouterr().out
    assert "FAIL plugin python deps" in out
    assert "jsonschema" in out
