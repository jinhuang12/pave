"""Preflight checks inside a session worktree (found on g6e-cdd, 2026-09-24).

Session b003fb98 ran ``preflight_pumice.py`` from
``<worktree>/scripts`` and got two spurious FAILs:

* ``pumice pins``: the worktree has no ``shared/`` directory; the pins file
  is installed at ``/usr/local/share/ammo/pumice_pins.py``.
* ``server flags``: ``pumice-server`` (the Rust binary on PATH) answers
  ``Unknown argument: --help``; the runner's launcher is
  ``rust_grpc_server.py``, whose argparse lists every flag.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_PREFLIGHT = _SCRIPTS_DIR / "preflight_pumice.py"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_spec = importlib.util.spec_from_file_location("preflight_pumice_session", str(_PREFLIGHT))
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight_pumice_session"] = preflight
_spec.loader.exec_module(preflight)


_PINS_STUB = (
    "REQUIRED_VLLM = '0.29.0'\n"
    "def check_pins():\n"
    "    return True, 'stub pins match'\n"
)


class TestPinsFallback:
    def test_installed_pins_file_env_override_wins(self, tmp_path, monkeypatch):
        pins = tmp_path / "pins.py"
        pins.write_text(_PINS_STUB)
        monkeypatch.setenv("AMMO_PUMICE_PINS_FILE", str(pins))
        assert preflight._installed_pins_file() == pins

    def test_installed_pins_file_uses_fallback_list(self, tmp_path, monkeypatch):
        pins = tmp_path / "share" / "pumice_pins.py"
        pins.parent.mkdir()
        pins.write_text(_PINS_STUB)
        monkeypatch.delenv("AMMO_PUMICE_PINS_FILE", raising=False)
        monkeypatch.setattr(preflight, "PINS_FILE_FALLBACKS", (str(pins),))
        assert preflight._installed_pins_file() == pins

    def test_installed_pins_file_none_when_nothing_readable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMMO_PUMICE_PINS_FILE", raising=False)
        monkeypatch.setattr(
            preflight, "PINS_FILE_FALLBACKS", (str(tmp_path / "missing.py"),)
        )
        assert preflight._installed_pins_file() is None

    def test_check_pins_passes_from_installed_file(self, tmp_path, monkeypatch):
        """No shared/ up the tree (the test file is under _ammo_shared/tests,
        so the parent walk finds nothing) -> the installed file is used."""
        pins = tmp_path / "pumice_pins.py"
        pins.write_text(_PINS_STUB)
        monkeypatch.setenv("AMMO_ACCELERATOR_KIND", "cuda")
        monkeypatch.setenv("AMMO_PUMICE_PINS_FILE", str(pins))
        # Make sure the parent walk cannot find a repo shared/pumice_pins.py.
        # Pretend the script lives in a session worktree with no shared/.
        monkeypatch.setattr(
            preflight, "__file__", str(tmp_path / "wt" / "scripts" / "preflight_pumice.py")
        )
        name, ok, detail = preflight._check_pins()
        assert name == "pumice pins"
        assert ok, detail
        assert detail == "stub pins match"

    def test_check_pins_names_fallbacks_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMMO_ACCELERATOR_KIND", "cuda")
        monkeypatch.delenv("AMMO_PUMICE_PINS_FILE", raising=False)
        monkeypatch.setattr(
            preflight, "PINS_FILE_FALLBACKS", (str(tmp_path / "absent.py"),)
        )
        # Pretend the script lives in a session worktree with no shared/.
        monkeypatch.setattr(
            preflight, "__file__", str(tmp_path / "wt" / "scripts" / "preflight_pumice.py")
        )
        _, ok, detail = preflight._check_pins()
        assert not ok
        assert "absent.py" in detail
        assert "AMMO_PUMICE_PINS_FILE" in detail


class TestServerFlagsProbe:
    def test_probe_prefers_env_exe(self, monkeypatch):
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", "/x/pumice-server")
        argv, label = preflight._server_probe_argv()
        assert argv == ["/x/pumice-server", "--help"]
        assert label == "/x/pumice-server"

    def test_probe_uses_repo_launcher_under_this_python(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMMO_PUMICE_SERVER_EXE", raising=False)
        (tmp_path / "rust_grpc_server.py").write_text("")
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(tmp_path))
        argv, label = preflight._server_probe_argv()
        assert argv[0] == sys.executable
        assert argv[1] == str(tmp_path / "rust_grpc_server.py")
        assert argv[2] == "--help"
        assert label.endswith("rust_grpc_server.py")

    def test_probe_falls_back_to_binary_without_repo(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AMMO_PUMICE_SERVER_EXE", raising=False)
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(preflight, "_find_repo_root", lambda: None)
        argv, label = preflight._server_probe_argv()
        assert argv == ["pumice-server", "--help"]
        assert label == "pumice-server"

    def test_unknown_argument_falls_back_to_main_rs(self, tmp_path, monkeypatch):
        """The Rust binary has no --help; main.rs literals answer instead."""
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", "/x/pumice-server")
        main_rs = tmp_path / "pumice-rust" / "src" / "main.rs"
        main_rs.parent.mkdir(parents=True)
        main_rs.write_text(
            "\n".join(f'"{flag}" => {{}}' for flag in preflight.SERVER_FLAGS)
        )
        (tmp_path / "rust_grpc_server.py").write_text("")
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(tmp_path))

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(
                argv, 1, stdout="", stderr="Unknown argument: --help\n"
            )

        monkeypatch.setattr(preflight.subprocess, "run", fake_run)
        name, ok, detail = preflight._check_server_flags()
        assert name == "server flags"
        assert ok, detail
        assert "main.rs" in detail

    def test_missing_flag_is_reported_with_source(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", "/x/launcher")
        present = [f for f in preflight.SERVER_FLAGS if f != "--metrics-port"]

        def fake_run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, stdout=" ".join(present), stderr="")

        monkeypatch.setattr(preflight.subprocess, "run", fake_run)
        _, ok, detail = preflight._check_server_flags()
        assert not ok
        assert "--metrics-port" in detail
        assert "/x/launcher --help" in detail


class TestFindRepoRoot:
    def test_stamped_source_dir_wins(self, tmp_path, monkeypatch):
        (tmp_path / "rust_grpc_server.py").write_text("")
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(tmp_path))
        assert preflight._find_repo_root() == tmp_path

    def test_walks_up_from_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "rust_grpc_server.py").write_text("")
        sub = tmp_path / "kernel_opt_artifacts" / "x"
        sub.mkdir(parents=True)
        monkeypatch.delenv("AMMO_RUNTIME_SOURCE_DIR", raising=False)
        monkeypatch.chdir(sub)
        assert preflight._find_repo_root() == tmp_path
