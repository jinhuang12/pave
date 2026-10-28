#!/usr/bin/env python3
"""Freshness-gate and env-hygiene tests for run_pumice_grpc_bench.py.

Decision record: docs/PUMICE_MIGRATION.md D3 (stale-binary hazard) + D5
(_sanitize_engine_op_env).

Why these two are the highest-value tests in the suite:

* Pumice's scheduler, block manager, spec-decode orchestration and confdb
  backend selection are all COMPILED (Rust -> PyO3 extension + pumice-server
  binary). A source or confdb edit does not take effect until `cargo build`
  runs, and no PYTHONPATH trick can shortcut it. Without this gate a campaign
  can measure the OLD binary and attribute its numbers to the NEW code — a
  silently wrong result, which is worse than a crash.
* Optimization flags are `PUMICE_*` env vars, so a leftover flag in the shell
  silently contaminates a measurement. The sanitizer is the only thing standing
  between a promoted flag from round N and a bogus A/B in round N+1.

All filesystem work happens in pytest tmp_path; no real Pumice checkout needed.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_pumice_grpc_bench as bench  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Pumice checkouts
# ---------------------------------------------------------------------------


def _touch(path: Path, mtime: float | None = None, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _make_repo(
    tmp_path: Path,
    *,
    source_mtime: float,
    artifact_mtime: float | None,
    artifact_names: tuple[str, ...] = ("libpumice_rust.so", "pumice-server"),
    confdb_mtime: float | None = None,
) -> Path:
    """Build a fake Pumice checkout with controlled mtimes."""
    repo = tmp_path / "pumice"
    _touch(repo / "pumice-rust" / "src" / "engine.rs", source_mtime)
    _touch(repo / "pumice-rust" / "src" / "scheduler.rs", source_mtime)
    _touch(
        repo / "pumice-rust" / "confdb" / "gpt_oss.toml",
        confdb_mtime if confdb_mtime is not None else source_mtime,
    )
    if artifact_mtime is not None:
        for name in artifact_names:
            _touch(repo / "pumice-rust" / "target" / "release" / name, artifact_mtime)
    return repo


_T0 = 1_700_000_000.0


class TestFreshnessGate:
    def test_fresh_build_passes(self, tmp_path):
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 100)
        report = bench.check_engine_freshness(repo)
        assert report.ok is True
        assert report.reason == "fresh"
        assert report.sources_scanned >= 3

    def test_stale_build_refused(self, tmp_path):
        repo = _make_repo(tmp_path, source_mtime=_T0 + 100, artifact_mtime=_T0)
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "stale_artifact"
        assert "cargo build --release" in report.detail
        assert "pumice-stage-native.sh" in report.detail
        assert "--no-default-features" not in report.detail
        assert report.newest_source.endswith((".rs", ".toml"))

    def test_confdb_edit_alone_marks_stale(self, tmp_path):
        """confdb TOMLs are embedded at compile time by build.rs.

        Editing gpt_oss.toml without rebuilding leaves the OLD backend choice
        baked into the binary — exactly as stale as editing a .rs file.
        """
        repo = _make_repo(
            tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 50, confdb_mtime=_T0 + 100
        )
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "stale_artifact"
        assert report.newest_source.endswith("gpt_oss.toml")

    def test_oldest_artifact_decides_not_newest(self, tmp_path):
        """A rebuilt .so must not mask a stale pumice-server binary."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 100)
        stale_binary = repo / "pumice-rust" / "target" / "release" / "pumice-server"
        os.utime(stale_binary, (_T0 - 100, _T0 - 100))
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "stale_artifact"
        assert report.oldest_artifact.endswith("pumice-server")

    def test_missing_artifact_refused(self, tmp_path):
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "missing_artifact"
        assert "cargo build --release" in report.detail

    def test_missing_sources_refused_not_silently_passed(self, tmp_path):
        """No pumice-rust/src means we cannot prove what is under test."""
        empty = tmp_path / "not-pumice"
        empty.mkdir()
        report = bench.check_engine_freshness(empty)
        assert report.ok is False
        assert report.reason == "missing_sources"
        assert "--allow-stale-engine" in report.detail

    def test_cargo_target_dir_searched(self, tmp_path):
        """Sessions build with CARGO_TARGET_DIR=/workspace/cargo-target/<id> (D3)."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        shared = tmp_path / "cargo-target"
        _touch(shared / "release" / "libpumice_rust.so", _T0 + 100)
        _touch(shared / "release" / "pumice-server", _T0 + 100)
        report = bench.check_engine_freshness(repo, cargo_target_dir=shared)
        assert report.ok is True

    def test_toplevel_editable_so_counts_as_artifact(self, tmp_path):
        """`pip install -e .` can drop pumice_rust*.so at the repo root."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        _touch(repo / "pumice_rust.cpython-312-x86_64-linux-gnu.so", _T0 + 100)
        report = bench.check_engine_freshness(repo)
        assert report.ok is True

    def test_equal_mtime_is_fresh(self, tmp_path):
        """Boundary: a build finishing in the same second as the edit is accepted."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0)
        assert bench.check_engine_freshness(repo).ok is True

    def test_report_is_json_serializable(self, tmp_path):
        import json

        repo = _make_repo(tmp_path, source_mtime=_T0 + 100, artifact_mtime=_T0)
        payload = bench.check_engine_freshness(repo).to_dict()
        json.dumps(payload)  # must not raise
        assert set(payload) >= {
            "ok",
            "reason",
            "detail",
            "newest_source",
            "oldest_artifact",
            "sources_scanned",
            "artifacts",
        }

    def test_real_edit_then_rebuild_sequence(self, tmp_path):
        """Simulate the actual agent loop: edit -> stale -> rebuild -> fresh."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 10)
        assert bench.check_engine_freshness(repo).ok is True

        time.sleep(0.01)
        _touch(repo / "pumice-rust" / "src" / "engine.rs", time.time())
        assert bench.check_engine_freshness(repo).reason == "stale_artifact"

        rebuilt = time.time() + 1
        for name in ("libpumice_rust.so", "pumice-server"):
            os.utime(repo / "pumice-rust" / "target" / "release" / name, (rebuilt, rebuilt))
        assert bench.check_engine_freshness(repo).ok is True


class TestServingArtifactSemantics:
    """The gate compares the artifact that will actually SERVE, not the oldest
    file found anywhere. Staged venv extension > release-dir artifacts >
    repo-root editable leftovers."""

    _STAGED = ".venv/lib/python3.12/site-packages/pumice_rust.cpython-312-x86_64-linux-gnu.so"

    def test_staged_extension_is_the_serving_artifact(self, tmp_path):
        """A fresh staged .so passes even when target/release is stale —
        `import pumice_rust` loads the staged copy, not the cargo output."""
        repo = _make_repo(tmp_path, source_mtime=_T0 + 100, artifact_mtime=_T0)
        _touch(repo / self._STAGED, _T0 + 200)
        report = bench.check_engine_freshness(repo)
        assert report.ok is True
        assert report.oldest_artifact.endswith(".so")
        assert "site-packages" in report.oldest_artifact

    def test_stale_staged_extension_refused_despite_fresh_build(self, tmp_path):
        """Rebuilt but never re-staged: server imports the OLD code."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 100)
        _touch(repo / self._STAGED, _T0 + 50)
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "stale_staging"
        assert "Re-stage" in report.detail

    def test_stale_staging_slop_tolerates_copy_timestamp(self, tmp_path):
        """cp within ~1s of the build must not trip the rebuilt-not-restaged check."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 100)
        _touch(repo / self._STAGED, _T0 + 99.5)
        assert bench.check_engine_freshness(repo).ok is True

    def test_env_cargo_target_dir_discovered(self, tmp_path, monkeypatch):
        """Session builds export CARGO_TARGET_DIR (D3); the gate must see it
        without the caller threading the path through explicitly."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        shared = tmp_path / "cargo-target"
        _touch(shared / "release" / "libpumice_rust.so", _T0 + 100)
        monkeypatch.setenv("CARGO_TARGET_DIR", str(shared))
        report = bench.check_engine_freshness(repo)
        assert report.ok is True

    def test_explicit_cargo_target_dir_wins_over_env(self, tmp_path, monkeypatch):
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        good = tmp_path / "good-target"
        _touch(good / "release" / "libpumice_rust.so", _T0 + 100)
        monkeypatch.setenv("CARGO_TARGET_DIR", str(tmp_path / "nonexistent"))
        report = bench.check_engine_freshness(repo, cargo_target_dir=good)
        assert report.ok is True

    def test_historical_editable_so_cannot_permanently_fail_gate(self, tmp_path):
        """Regression: the OLD gate keyed on the oldest artifact anywhere, so a
        hook-copied historical pumice_rust*.so at the repo root failed every
        sweep forever no matter how many rebuilds happened."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 100)
        _touch(repo / "pumice_rust.cpython-312-x86_64-linux-gnu.so", _T0 - 9999)
        report = bench.check_engine_freshness(repo)
        assert report.ok is True

    def test_missing_artifact_detail_includes_restage_command(self, tmp_path):
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=None)
        report = bench.check_engine_freshness(repo)
        assert report.reason == "missing_artifact"
        assert "cargo build --release" in report.detail
        assert "site-packages" in report.detail  # re-staging step spelled out
        assert f"pumice-stage-native.sh {repo}/.venv/bin/python {repo}" in report.detail
        assert "patch.crates-io" in report.detail


class TestNewestEngineSource:
    def test_scans_rs_and_toml_and_build_rs(self, tmp_path):
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "src" / "a.rs", _T0)
        _touch(repo / "pumice-rust" / "src" / "nested" / "b.rs", _T0 + 1)
        _touch(repo / "pumice-rust" / "confdb" / "x.toml", _T0 + 2)
        _touch(repo / "pumice-rust" / "build.rs", _T0 + 3)
        _touch(repo / "pumice-rust" / "Cargo.toml", _T0 + 4)
        path, mtime, count = bench.newest_engine_source(repo)
        assert count == 5
        assert mtime == pytest.approx(_T0 + 4)
        assert path.name == "Cargo.toml"

    def test_top_level_cargo_toml_scanned(self, tmp_path):
        """WS4.5: the repo-root Cargo.toml is a source too (patch pins live there)."""
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "src" / "a.rs", _T0)
        _touch(repo / "pumice-rust" / "Cargo.toml", _T0 + 1)
        _touch(repo / "Cargo.toml", _T0 + 100)
        path, mtime, count = bench.newest_engine_source(repo)
        assert count == 3
        assert mtime == pytest.approx(_T0 + 100)
        assert path == repo / "Cargo.toml"

    def test_cargo_home_config_included(self, tmp_path, monkeypatch):
        """WS4.5: $CARGO_HOME/config.toml is a source — the patch.crates-io row."""
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "src" / "a.rs", _T0)
        cargo_home = tmp_path / "cargo-home"
        _touch(cargo_home / "config.toml", _T0 + 500)
        monkeypatch.setenv("CARGO_HOME", str(cargo_home))
        path, mtime, count = bench.newest_engine_source(repo)
        assert count == 2
        assert mtime == pytest.approx(_T0 + 500)
        assert path == cargo_home / "config.toml"

    def test_cargo_home_unset_does_not_scan_default(self, tmp_path, monkeypatch):
        """No CARGO_HOME means no ~/.cargo/config.toml scan; false-positives forbidden."""
        monkeypatch.delenv("CARGO_HOME", raising=False)
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "src" / "a.rs", _T0)
        _, _, count = bench.newest_engine_source(repo)
        assert count == 1

    def test_cargo_home_config_edit_marks_stale(self, tmp_path, monkeypatch):
        """The whole point of WS4.5: an edit to $CARGO_HOME/config.toml fails the gate."""
        repo = _make_repo(tmp_path, source_mtime=_T0, artifact_mtime=_T0 + 10)
        cargo_home = tmp_path / "cargo-home"
        _touch(cargo_home / "config.toml", _T0 + 1000)
        monkeypatch.setenv("CARGO_HOME", str(cargo_home))
        report = bench.check_engine_freshness(repo)
        assert report.ok is False
        assert report.reason == "stale_artifact"
        assert Path(report.newest_source).name == "config.toml"

    def test_ignores_python_sources(self, tmp_path):
        """Python-layer edits are live via PYTHONPATH; they must not trip the gate."""
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "src" / "a.rs", _T0)
        _touch(repo / "src" / "pumice" / "layers" / "fused_moe.py", _T0 + 9999)
        path, mtime, count = bench.newest_engine_source(repo)
        assert count == 1
        assert path.name == "a.rs"

    def test_empty_tree_returns_none(self, tmp_path):
        assert bench.newest_engine_source(tmp_path) == (None, None, 0)


class TestDiscoverEngineArtifacts:
    def test_deduplicates_and_returns_existing_only(self, tmp_path):
        repo = tmp_path / "pumice"
        _touch(repo / "pumice-rust" / "target" / "release" / "pumice-server", _T0)
        found = bench.discover_engine_artifacts(repo)
        assert len(found) == 1
        assert found[0].name == "pumice-server"

    def test_none_found_is_empty_list(self, tmp_path):
        assert bench.discover_engine_artifacts(tmp_path) == []


class TestDiscoverStagedExtensions:
    def test_finds_so_in_venv_site_packages(self, tmp_path):
        repo = tmp_path / "pumice"
        so = (repo / ".venv" / "lib" / "python3.12" / "site-packages"
              / "pumice_rust.cpython-312-x86_64-linux-gnu.so")
        _touch(so, _T0)
        found = bench.discover_staged_extensions(repo)
        assert found == [so]

    def test_ignores_unstaged_repos(self, tmp_path):
        assert bench.discover_staged_extensions(tmp_path) == []


# ---------------------------------------------------------------------------
# Env hygiene
# ---------------------------------------------------------------------------


class TestSanitizeEngineOpEnv:
    def test_strips_pumice_prefix(self):
        env = {"PUMICE_STALE_FLAG": "1", "CUDA_VISIBLE_DEVICES": "0"}
        assert bench._sanitize_engine_op_env(env) == {"CUDA_VISIBLE_DEVICES": "0"}

    def test_strips_vllm_prefix_for_hygiene(self):
        """A pre-migration shell or wrapper can still export VLLM_* vars."""
        env = {"VLLM_OP001": "1", "VLLM_ATTENTION_BACKEND": "FLASH_ATTN", "PATH": "/usr/bin"}
        assert bench._sanitize_engine_op_env(env) == {"PATH": "/usr/bin"}

    def test_preserve_keys_survive(self):
        env = {"PUMICE_SHIPPED": "1", "PUMICE_STALE": "1", "PUMICE_NEW_OPT": "1"}
        out = bench._sanitize_engine_op_env(
            env, preserve_keys={"PUMICE_SHIPPED", "PUMICE_NEW_OPT"}
        )
        assert out == {"PUMICE_SHIPPED": "1", "PUMICE_NEW_OPT": "1"}

    def test_non_engine_vars_untouched(self):
        env = {
            "HF_HOME": "/hf",
            "TRITON_CACHE_DIR": "/t",
            "LD_LIBRARY_PATH": "/l",
            "PUMICEISH_NOT_A_FLAG": "1",
        }
        assert bench._sanitize_engine_op_env(env) == env

    def test_prefix_match_is_anchored_with_underscore(self):
        """`PUMICEFOO` must NOT be stripped — only the `PUMICE_` namespace is."""
        env = {"PUMICEFOO": "1", "MYPUMICE_X": "1", "PUMICE_X": "1"}
        out = bench._sanitize_engine_op_env(env)
        assert out == {"PUMICEFOO": "1", "MYPUMICE_X": "1"}

    def test_input_not_mutated(self):
        env = {"PUMICE_X": "1", "PATH": "/bin"}
        snapshot = dict(env)
        bench._sanitize_engine_op_env(env)
        assert env == snapshot

    def test_infra_constant_contents(self):
        assert bench.ENGINE_INFRA_ENV == frozenset(
            {"PUMICE_PLATFORM", "PUMICE_VLLM_RUNNER_ONLY"}
        )

    def test_infra_keys_survive_empty_preserve_set(self):
        """Image-level platform names must survive: stripping
        PUMICE_PLATFORM=neuron made the Neuron server panic at init."""
        env = {
            "PUMICE_PLATFORM": "neuron",
            "PUMICE_VLLM_RUNNER_ONLY": "1",
            "PATH": "/bin",
        }
        assert bench._sanitize_engine_op_env(env, preserve_keys=frozenset()) == env

    def test_optimization_flag_still_stripped_next_to_infra_keys(self):
        env = {
            "PUMICE_PLATFORM": "neuron",
            "PUMICE_VLLM_RUNNER_ONLY": "1",
            "PUMICE_ENABLE_ALLREDUCE_FUSION": "1",
            "PUMICE_USE_VLLM_RUNNER": "1",
        }
        out = bench._sanitize_engine_op_env(env)
        assert out == {"PUMICE_PLATFORM": "neuron", "PUMICE_VLLM_RUNNER_ONLY": "1"}

    def test_preserve_keys_still_work_with_infra_keys(self):
        env = {
            "PUMICE_PLATFORM": "neuron",
            "PUMICE_SHIPPED": "1",
            "PUMICE_STALE": "1",
        }
        out = bench._sanitize_engine_op_env(env, preserve_keys={"PUMICE_SHIPPED"})
        assert out == {"PUMICE_PLATFORM": "neuron", "PUMICE_SHIPPED": "1"}


class TestFreshCacheEnv:
    def test_cache_dirs_injected(self, tmp_path):
        out = bench._inject_fresh_cache_env({}, tmp_path / "cache")
        assert out["PUMICE_CACHE_ROOT"] == str(tmp_path / "cache")
        assert out["TRITON_CACHE_DIR"] == str(tmp_path / "cache" / "triton_cache")
        assert out["TORCHINDUCTOR_CACHE_DIR"] == str(tmp_path / "cache" / "inductor_cache")

    def test_does_not_disable_compile_cache(self):
        """Within a run, compile-cache SAVE must still work (launch 2+ warm)."""
        out = bench._inject_fresh_cache_env({}, Path("/tmp/c"))
        assert "PUMICE_DISABLE_COMPILE_CACHE" not in out

    def test_survives_sanitizer_when_applied_after(self, tmp_path):
        """Ordering guard: PUMICE_CACHE_ROOT matches the sanitizer's own prefix."""
        env = bench._sanitize_engine_op_env({"PATH": "/bin"})
        env = bench._inject_fresh_cache_env(env, tmp_path / "cache")
        assert "PUMICE_CACHE_ROOT" in env

    def test_preserves_unrelated_vars(self, tmp_path):
        out = bench._inject_fresh_cache_env(
            {"PYTHONPATH": "/foo", "UNRELATED": "bar"}, tmp_path / "cache"
        )
        assert out["PYTHONPATH"] == "/foo"
        assert out["UNRELATED"] == "bar"

    def test_overrides_an_inherited_cache_root(self, tmp_path):
        """A leftover PUMICE_CACHE_ROOT from the shell must not win: the point of
        --fresh-cache is that THIS sweep owns the compile cache."""
        out = bench._inject_fresh_cache_env(
            {"PUMICE_CACHE_ROOT": "/already/set"}, tmp_path / "cache"
        )
        assert out["PUMICE_CACHE_ROOT"] == str(tmp_path / "cache")

    def test_does_not_mutate_input(self, tmp_path):
        env = {"PYTHONPATH": "/foo"}
        bench._inject_fresh_cache_env(env, tmp_path / "cache")
        assert "PUMICE_CACHE_ROOT" not in env


class TestFreshCacheArg:
    """The CLI surface of --fresh-cache (default OFF).

    Ported from the vLLM-era `test_sweep_fresh_cache.py`; the flag and its
    contract carry over unchanged, only the injected var names differ
    (``PUMICE_CACHE_ROOT`` for the engine's own cache, plus Triton and Inductor).
    """

    def test_default_false(self):
        assert bench.parse_args(["--artifact-dir", "/tmp/x"]).fresh_cache is False

    def test_flag_sets_true(self):
        args = bench.parse_args(["--artifact-dir", "/tmp/x", "--fresh-cache"])
        assert args.fresh_cache is True

    def test_help_states_the_compile_cost(self, capsys):
        """A caller must learn from --help that the first launch pays full
        compile, or they read a cold-cache launch as a regression."""
        with pytest.raises(SystemExit):
            bench.parse_args(["--help"])
        out = capsys.readouterr().out
        assert "--fresh-cache" in out
        assert "compile" in out.lower()


class TestWorktreePythonpath:
    def test_src_layout_preferred(self, tmp_path):
        (tmp_path / "src" / "pumice").mkdir(parents=True)
        out = bench._ensure_worktree_pythonpath({}, str(tmp_path))
        assert out["PYTHONPATH"] == str(tmp_path / "src")

    def test_falls_back_to_root_without_src(self, tmp_path):
        out = bench._ensure_worktree_pythonpath({}, str(tmp_path))
        assert out["PYTHONPATH"] == str(tmp_path)

    def test_prepends_to_existing(self, tmp_path):
        (tmp_path / "src").mkdir()
        out = bench._ensure_worktree_pythonpath({"PYTHONPATH": "/other"}, str(tmp_path))
        assert out["PYTHONPATH"] == f"{tmp_path / 'src'}{os.pathsep}/other"

    def test_idempotent(self, tmp_path):
        (tmp_path / "src").mkdir()
        first = bench._ensure_worktree_pythonpath({}, str(tmp_path))
        second = bench._ensure_worktree_pythonpath(first, str(tmp_path))
        assert second["PYTHONPATH"] == first["PYTHONPATH"]


class TestBuildRunSpecs:
    def _tgt(self, **bench_overrides):
        spec = {
            "target": {
                "model_id": "/m",
                "tp": 1,
                "ep": 1,
                "max_model_len": 4096,
            },
            "workload": {
                "input_len": 64,
                "output_len": 512,
                "batch_sizes": [1],
                "num_iters": 3,
            },
            "bench": {
                "runner": "pumice_grpc_bench",
                "extra_args": ["--enable-chunked-prefill"],
                "baseline_extra_args": ["--no-compile"],
                "opt_extra_args": [],
                "baseline_env": {"PUMICE_SHIPPED": "1"},
                "opt_env": {"PUMICE_NEW_OPT": "1"},
                **bench_overrides,
            },
        }
        return bench.parse_target_spec(spec)

    def test_stale_flag_stripped_from_both_arms(self):
        runs = bench.build_run_specs(
            self._tgt(),
            inherited_env={"PUMICE_STALE_FROM_ROUND_1": "1", "PATH": "/bin"},
        )
        for arm in runs.values():
            assert "PUMICE_STALE_FROM_ROUND_1" not in arm.env
            assert arm.env["PATH"] == "/bin"

    def test_baseline_arm_lacks_opt_flag(self):
        """The whole point of the A/B: baseline must not see the opt flag."""
        runs = bench.build_run_specs(self._tgt(), inherited_env={"PUMICE_NEW_OPT": "1"})
        assert "PUMICE_NEW_OPT" not in runs["baseline"].env
        assert runs["opt"].env["PUMICE_NEW_OPT"] == "1"

    def test_image_platform_env_reaches_both_arms(self):
        """PUMICE_PLATFORM from the Neuron image ENV must reach both servers
        without the arm declaring it in baseline_env."""
        runs = bench.build_run_specs(
            self._tgt(), inherited_env={"PUMICE_PLATFORM": "neuron", "PUMICE_STALE": "1"}
        )
        for label in ("baseline", "opt"):
            assert runs[label].env["PUMICE_PLATFORM"] == "neuron"
            assert "PUMICE_STALE" not in runs[label].env

    def test_promoted_baseline_flag_set_on_both_arms(self):
        """After a round ships, its flag lives in baseline_env for BOTH arms."""
        runs = bench.build_run_specs(self._tgt(), inherited_env={})
        assert runs["baseline"].env["PUMICE_SHIPPED"] == "1"
        assert runs["opt"].env["PUMICE_SHIPPED"] == "1"

    def test_opt_env_wins_over_baseline_env_on_collision(self):
        tgt = self._tgt(
            baseline_env={"PUMICE_X": "0"}, opt_env={"PUMICE_X": "1"}
        )
        runs = bench.build_run_specs(tgt, inherited_env={})
        assert runs["baseline"].env["PUMICE_X"] == "0"
        assert runs["opt"].env["PUMICE_X"] == "1"

    def test_per_label_server_args_composed(self):
        runs = bench.build_run_specs(self._tgt(), inherited_env={})
        assert runs["baseline"].server_args == ["--enable-chunked-prefill", "--no-compile"]
        assert runs["opt"].server_args == ["--enable-chunked-prefill"]

    def test_env_overrides_recorded_for_evidence(self):
        """`env_overrides` is what lands in the results JSON per arm."""
        runs = bench.build_run_specs(self._tgt(), inherited_env={"PATH": "/bin"})
        assert runs["baseline"].env_overrides == {"PUMICE_SHIPPED": "1"}
        assert runs["opt"].env_overrides == {
            "PUMICE_SHIPPED": "1",
            "PUMICE_NEW_OPT": "1",
        }
        assert "PATH" not in runs["opt"].env_overrides


class TestServerCommand:
    def test_defaults_composed(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"],
            model_id="/models/m",
            port=50051,
            max_model_len=8192,
            max_num_seqs=32,
            tensor_parallel_size=2,
        )
        assert cmd[0] == "pumice-server"
        assert "--model" in cmd and "/models/m" in cmd
        assert cmd[cmd.index("--port") + 1] == "50051"
        assert cmd[cmd.index("--tensor-parallel-size") + 1] == "2"
        assert cmd[cmd.index("--max-num-seqs") + 1] == "32"

    def test_extra_args_override_defaults_without_duplication(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"],
            model_id="/models/m",
            port=50051,
            max_model_len=8192,
            max_num_seqs=32,
            tensor_parallel_size=1,
            extra_args=["--max-num-seqs", "64", "--enable-expert-parallel"],
        )
        assert cmd.count("--max-num-seqs") == 1
        assert cmd[cmd.index("--max-num-seqs") + 1] == "64"
        assert "--enable-expert-parallel" in cmd

    def test_equals_form_extra_arg_also_suppresses_default(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"],
            model_id="/models/m",
            port=1,
            max_model_len=1,
            max_num_seqs=1,
            tensor_parallel_size=1,
            extra_args=["--max-model-len=4096"],
        )
        assert "--max-model-len" not in cmd
        assert "--max-model-len=4096" in cmd

    def test_neuron_pins_builtin_model_runner(self):
        # trn2-1: confdb [sm90] model_runner="vllm" applied on Neuron.
        assert bench.default_model_runner({"AMMO_ACCELERATOR_KIND": "neuron"}) == "pumice"
        assert bench.default_model_runner({"PUMICE_PLATFORM": "neuron"}) == "pumice"
        assert bench.default_model_runner({"AMMO_ACCELERATOR_KIND": "cuda"}) is None
        cmd = bench.build_server_cmd(
            server_exe=["python", "rust_grpc_server.py"],
            model_id="/hf/gpt-oss-20b",
            port=1,
            max_model_len=8192,
            max_num_seqs=8,
            tensor_parallel_size=8,
            model_runner="pumice",
        )
        assert cmd[cmd.index("--model-runner") + 1] == "pumice"

    def test_explicit_model_runner_in_extra_args_wins(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"],
            model_id="/m",
            port=1,
            max_model_len=1,
            max_num_seqs=1,
            tensor_parallel_size=1,
            extra_args=["--model-runner", "vllm"],
            model_runner="pumice",
        )
        assert cmd.count("--model-runner") == 1
        assert cmd[cmd.index("--model-runner") + 1] == "vllm"

    def test_max_num_seqs_defaults_to_largest_batch(self):
        buckets = [{"batch_size": 1}, {"batch_size": 32}, {"batch_size": 8}]
        assert bench._resolve_max_num_seqs(buckets, []) == 32

    def test_max_num_seqs_respects_explicit_flag(self):
        buckets = [{"batch_size": 32}]
        assert bench._resolve_max_num_seqs(buckets, ["--max-num-seqs", "64"]) == 64
        assert bench._resolve_max_num_seqs(buckets, ["--max-num-seqs=16"]) == 16

    def test_resolve_server_exe_falls_back_to_launcher_script(self, tmp_path):
        (tmp_path / "rust_grpc_server.py").write_text("", encoding="utf-8")
        exe = bench.resolve_server_exe("pumice-server", tmp_path)
        assert exe[0] == sys.executable
        assert exe[1].endswith("rust_grpc_server.py")

    def test_resolve_server_exe_prefers_local_release_binary(self, tmp_path):
        binary = tmp_path / "target" / "release" / "pumice-server"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
        (tmp_path / "rust_grpc_server.py").write_text("", encoding="utf-8")
        assert bench.resolve_server_exe("pumice-server", tmp_path) == [str(binary)]

    def test_resolve_server_exe_prefers_worktree_launcher_over_path(
        self, tmp_path, monkeypatch
    ):
        # The engine venv's pumice-server on PATH is the image build; a
        # track's Rust rebuild only restages pumice_rust into the venv.
        (tmp_path / "rust_grpc_server.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(
            bench.shutil, "which", lambda name: "/opt/neuron-venv/bin/pumice-server"
        )
        exe = bench.resolve_server_exe("pumice-server", tmp_path)
        assert exe[0] == sys.executable
        assert exe[1].endswith("rust_grpc_server.py")

    def test_resolve_server_exe_uses_path_binary_as_last_resort(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            bench.shutil, "which", lambda name: "/usr/local/bin/pumice-server"
        )
        assert bench.resolve_server_exe("pumice-server", tmp_path) == [
            "pumice-server"
        ]

    def test_resolve_server_exe_no_launcher_is_fatal(self, tmp_path):
        with pytest.raises(SystemExit, match="Cannot find a Pumice server launcher"):
            bench.resolve_server_exe("pumice-server", tmp_path)

    def test_resolve_server_exe_path_binary_loses_to_worktree_launcher(
        self, tmp_path, monkeypatch
    ):
        """g6e-cdd 2026-09-24: /opt/pumice-venv/bin/pumice-server (the base
        engine) is on PATH in every session; it must not win over the
        worktree's rust_grpc_server.py or both A/B arms run the base engine."""
        (tmp_path / "rust_grpc_server.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(
            bench.shutil, "which", lambda name: "/opt/pumice-venv/bin/pumice-server"
        )
        exe = bench.resolve_server_exe("pumice-server", tmp_path)
        assert exe == [sys.executable, str(tmp_path / "rust_grpc_server.py")]

    def test_resolve_server_exe_path_binary_is_last_resort(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            bench.shutil, "which", lambda name: "/opt/pumice-venv/bin/pumice-server"
        )
        assert bench.resolve_server_exe("pumice-server", tmp_path) == ["pumice-server"]

    def test_explicit_list_spec_passes_through(self, tmp_path):
        assert bench.resolve_server_exe(
            ["python", "rust_grpc_server.py"], tmp_path
        ) == ["python", "rust_grpc_server.py"]

    def test_empty_spec_rejected(self, tmp_path):
        with pytest.raises(SystemExit, match="empty"):
            bench.resolve_server_exe("", tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
