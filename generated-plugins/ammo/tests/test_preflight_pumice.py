#!/usr/bin/env python3
"""Unit tests for preflight_pumice.py.

Every check is designed to import + introspect only, so this suite runs on a
bare interpreter with no ``pumice`` install.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_PREFLIGHT = _SCRIPTS_DIR / "preflight_pumice.py"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_spec = importlib.util.spec_from_file_location("preflight_pumice", str(_PREFLIGHT))
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight_pumice"] = preflight
_spec.loader.exec_module(preflight)


class TestCouplingTables:
    def test_generate_request_lists_stream_flag(self):
        assert "stream" in preflight.PROTO_FIELDS["GenerateRequest"]

    def test_complete_message_lists_prompt_tokens_cached(self):
        assert "prompt_tokens_cached" in preflight.PROTO_FIELDS["GenerateComplete"]

    def test_proto_methods_cover_start_stop_profile(self):
        for name in ("Generate", "HealthCheck", "StartProfile", "StopProfile"):
            assert name in preflight.PROTO_METHODS

    def test_server_flags_include_metrics_port(self):
        assert "--metrics-port" in preflight.SERVER_FLAGS

    def test_server_flags_include_both_prefix_cache_pins(self):
        for flag in ("--enable-prefix-caching", "--no-enable-prefix-caching"):
            assert flag in preflight.SERVER_FLAGS

    def test_emf_metric_names_include_prefix_cache_hit_rate(self):
        assert "prefix_cache_hit_rate" in preflight.EMF_METRIC_NAMES

    def test_prom_counter_names_include_hits_and_queries(self):
        assert "prefix_cache_hits_total" in preflight.PROM_COUNTER_NAMES
        assert "prefix_cache_queries_total" in preflight.PROM_COUNTER_NAMES


class TestModelPathCheck:
    def test_none_returns_ok(self):
        label, ok, detail = preflight._check_model_path(None)
        assert ok and "skipped" in detail

    def test_missing_dir_fails(self, tmp_path):
        label, ok, detail = preflight._check_model_path(str(tmp_path / "nope"))
        assert not ok

    def test_dir_without_config_fails(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        _, ok, detail = preflight._check_model_path(str(d))
        assert not ok
        assert "config.json" in detail

    def test_valid_dir_passes(self, tmp_path):
        d = tmp_path / "m"
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
        (d / "tokenizer.json").write_text("{}", encoding="utf-8")
        _, ok, _ = preflight._check_model_path(str(d))
        assert ok


class TestPinsSkipOnNeuron:
    def test_neuron_kind_skips_vllm_pin(self, monkeypatch):
        monkeypatch.setenv("AMMO_ACCELERATOR_KIND", "neuron")
        _, ok, detail = preflight._check_pins()
        assert ok and "neuron" in detail.lower()


class TestCmbApiPatchCheck:
    def test_missing_cargo_home_fails(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CARGO_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))  # ~/.cargo is the default
        _, ok, detail = preflight._check_cmb_api_patch()
        assert not ok and "CARGO_HOME" in detail

    def test_missing_row_fails(self, monkeypatch, tmp_path):
        (tmp_path / "config.toml").write_text("[net]\n", encoding="utf-8")
        monkeypatch.setenv("CARGO_HOME", str(tmp_path))
        _, ok, detail = preflight._check_cmb_api_patch()
        assert not ok
        assert "amzn-mantle-cmb-api" in detail or "patch.crates-io" in detail

    def test_valid_config_passes(self, monkeypatch, tmp_path):
        (tmp_path / "config.toml").write_text(
            "[patch.crates-io]\n"
            'amzn-mantle-cmb-api = { path = "/home/ubuntu/AWSMantle/share/common/cmb-api" }\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("CARGO_HOME", str(tmp_path))
        _, ok, _ = preflight._check_cmb_api_patch()
        assert ok


class TestMainReturnsCodeOne:
    def test_missing_pumice_returns_1(self, monkeypatch):
        # Force pumice import to fail; other checks likely also fail on a
        # bare interpreter — the whole point is exit code 1.
        monkeypatch.delenv("CARGO_HOME", raising=False)
        rc = preflight.main(["--skip-server-flags"])
        assert rc == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestServerFlagsFallback:
    """pumice-server has no --help (found live on trn2-1)."""

    def _fake_exe(self, tmp_path, text):
        exe = tmp_path / "pumice-server"
        exe.write_text(f"#!/bin/sh\necho '{text}'\n")
        exe.chmod(0o755)
        return exe

    def _main_rs(self, root, flags):
        src = root / "pumice-rust" / "src"
        src.mkdir(parents=True)
        arms = "\n".join(f'            "{f}" => {{ i += 1; }}' for f in flags)
        (src / "main.rs").write_text(f"fn parse_args() {{\n{arms}\n}}\n")

    def test_falls_back_to_parser_source(self, tmp_path, monkeypatch):
        exe = self._fake_exe(tmp_path, "Unknown argument: --help")
        wt = tmp_path / "wt"
        self._main_rs(wt, preflight.SERVER_FLAGS)
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", str(exe))
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(wt))
        label, ok, detail = preflight._check_server_flags()
        assert ok, detail
        assert "main.rs" in detail

    def test_source_missing_flag_fails(self, tmp_path, monkeypatch):
        exe = self._fake_exe(tmp_path, "Unknown argument: --help")
        wt = tmp_path / "wt"
        self._main_rs(wt, [f for f in preflight.SERVER_FLAGS if f != "--metrics-port"])
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", str(exe))
        monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(wt))
        label, ok, detail = preflight._check_server_flags()
        assert not ok
        assert "--metrics-port" in detail

    def test_help_text_still_accepted(self, tmp_path, monkeypatch):
        exe = self._fake_exe(tmp_path, " ".join(preflight.SERVER_FLAGS))
        monkeypatch.setenv("AMMO_PUMICE_SERVER_EXE", str(exe))
        monkeypatch.delenv("AMMO_RUNTIME_SOURCE_DIR", raising=False)
        label, ok, detail = preflight._check_server_flags()
        assert ok, detail
