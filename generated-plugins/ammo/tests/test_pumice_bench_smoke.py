#!/usr/bin/env python3
"""End-to-end smoke test of run_pumice_grpc_bench.main() with a mocked server.

Decision record: docs/PUMICE_MIGRATION.md D5.

Everything below the gRPC boundary is stubbed: no real ``pumice-server``
subprocess, no GPU, no network, no ``transformers``. The point is to exercise the
whole orchestration path — target.json -> freshness gate -> output layout ->
per-arm env -> wave driving -> raw bucket JSON -> row assembly -> results JSON +
MD — so a wiring break shows up here rather than in a GPU session.

The mock classes live in ``test_pumice_bench_profile`` and mirror
``pumice-rust/proto/pumice_engine.proto`` (the source of ``scripts/pumice_pb``).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent / "scripts"
for entry in (str(_SCRIPTS_DIR), str(_TESTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import run_pumice_grpc_bench as bench  # noqa: E402
from test_pumice_bench_profile import _MockPb2, _MockStub, _MockTokenizer  # noqa: E402

_FUTURE_MTIME = 2_000_000_000.0


def _make_repo(tmp_path: Path) -> Path:
    """A fake Pumice checkout whose build is newer than its sources."""
    repo = tmp_path / "pumice"
    (repo / "pumice-rust" / "src").mkdir(parents=True)
    (repo / "pumice-rust" / "confdb").mkdir(parents=True)
    (repo / "pumice-rust" / "target" / "release").mkdir(parents=True)
    (repo / "pumice-rust" / "src" / "engine.rs").write_text("fn main(){}", encoding="utf-8")
    (repo / "pumice-rust" / "confdb" / "gpt_oss.toml").write_text("x=1", encoding="utf-8")
    (repo / "pumice-rust" / "build.rs").write_text("fn main(){}", encoding="utf-8")
    (repo / "pumice-rust" / "Cargo.toml").write_text("[package]", encoding="utf-8")
    for name in ("libpumice_rust.so", "pumice-server"):
        path = repo / "pumice-rust" / "target" / "release" / name
        path.write_text("bin", encoding="utf-8")
        os.utime(path, (_FUTURE_MTIME, _FUTURE_MTIME))
    return repo


def _make_artifact(tmp_path: Path, **bench_overrides) -> Path:
    artifact = tmp_path / "artifact"
    (artifact / "rounds" / "1" / "sweeps").mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps({"campaign": {"current_round": 1}}), encoding="utf-8"
    )
    (artifact / "target.json").write_text(
        json.dumps(
            {
                "artifact_dir": str(artifact),
                "target": {
                    "model_id": "/models/m",
                    "dtype": "mxfp4",
                    "tp": 1,
                    "ep": 1,
                    "max_model_len": 4096,
                },
                "workload": {
                    "input_len": 64,
                    "output_len": 8,
                    "batch_sizes": [2],
                    "num_iters": 3,
                },
                "bench": {
                    "runner": "pumice_grpc_bench",
                    "server_cmd": "pumice-server",
                    "extra_args": [],
                    "baseline_env": {},
                    "opt_env": {"PUMICE_X": "1"},
                    **bench_overrides,
                },
            }
        ),
        encoding="utf-8",
    )
    return artifact


@pytest.fixture
def mocked_engine(monkeypatch):
    """Replace the server lifecycle + tokenizer with in-process stubs.

    Records every launched ServerHandle so a test can assert on the per-arm env
    and argv the runner would really have used.
    """
    launched: list = []

    class FakeServer(bench.ServerHandle):
        def start(self):
            self.start_unix = 1000.0
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # One EMF tick, so the spec-decode block exercises its parse path.
            # The gauge is a PERCENT (metrics.rs pct()); the parser normalizes
            # to a 0-1 fraction.
            self.log_path.write_text(
                '{"_aws":{},"spec_acceptance_rate":55.0}\n', encoding="utf-8"
            )
            launched.append(self)

        def connect(self):
            self.pb2 = _MockPb2
            self.stub = _MockStub()

        def wait_healthy(self, **kwargs):
            return

        def stop(self, **kwargs):
            return

    monkeypatch.setattr(bench, "ServerHandle", FakeServer)
    monkeypatch.setattr(bench, "resolve_server_exe", lambda spec, root: ["pumice-server"])
    monkeypatch.setattr(bench, "_load_tokenizer", lambda model_id: _MockTokenizer())
    return launched


class TestSmokeBaselineSweep:
    def _run(self, tmp_path, mocked_engine, extra_argv=()):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "baseline",
                "--labels", "baseline",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
                "--warmup-waves", "1",
                *extra_argv,
            ]
        )
        out_dir = artifact / "rounds" / "1" / "sweeps" / "baseline"
        return rc, out_dir, json.loads((out_dir / "e2e_latency_results.json").read_text())

    def test_exits_zero_and_writes_both_artifacts(self, tmp_path, mocked_engine):
        rc, out_dir, results = self._run(tmp_path, mocked_engine)
        assert rc == 0
        assert (out_dir / "e2e_latency_results.json").is_file()
        assert (out_dir / "e2e_latency_results.md").is_file()
        assert results["execution_mode"] == "grpc_server_sweep"
        assert results["runner"] == "pumice_grpc_bench"
        assert results["engine_freshness"]["reason"] == "fresh"
        assert results["engine_freshness"]["overridden"] is False

    def test_raw_bucket_json_matches_downstream_schema(self, tmp_path, mocked_engine):
        _, out_dir, _ = self._run(tmp_path, mocked_engine)
        raw = json.loads((out_dir / "json" / "baseline_bs2.json").read_text())
        assert set(raw) >= {"avg_latency", "latencies", "percentiles"}
        assert len(raw["latencies"]) == 3  # num_iters, warmup excluded
        assert set(raw["percentiles"]) == {"10", "25", "50", "75", "90", "99"}
        assert raw["avg_latency"] == pytest.approx(
            sum(raw["latencies"]) / len(raw["latencies"])
        )

    def test_row_avg_s_agrees_with_raw_avg_latency(self, tmp_path, mocked_engine):
        """The exact cross-check verify_validation_gates.py performs."""
        _, out_dir, results = self._run(tmp_path, mocked_engine)
        raw = json.loads((out_dir / "json" / "baseline_bs2.json").read_text())
        entry = results["results"][0]["baseline"]
        assert entry["avg_s"] == pytest.approx(raw["avg_latency"], rel=1e-9)
        assert entry["ok"] is True
        assert entry["returncode"] == 0

    def test_artifact_references_are_relative_and_exist(self, tmp_path, mocked_engine):
        """The gate verifier rejects paths outside the sweep dir or symlinked."""
        _, out_dir, results = self._run(tmp_path, mocked_engine)
        entry = results["results"][0]["baseline"]
        for key in ("log", "output_json", "runner_json"):
            rel = Path(entry[key])
            assert not rel.is_absolute()
            resolved = (out_dir / rel).resolve()
            assert resolved.is_relative_to(out_dir.resolve())
            assert resolved.exists() and not resolved.is_symlink()

    def test_spec_decode_harvested_from_server_emf_log(self, tmp_path, mocked_engine):
        _, _, results = self._run(tmp_path, mocked_engine)
        block = results["results"][0]["baseline"]["spec_decode"]
        assert block["draft_acceptance_rate"] == pytest.approx(0.55)
        assert block["source"] == "pumice_emf_metrics"

    def test_phase_split_and_throughput_derived(self, tmp_path, mocked_engine):
        _, _, results = self._run(tmp_path, mocked_engine)
        entry = results["results"][0]["baseline"]
        assert entry["prefill_avg_s"] >= 0
        assert entry["decode_avg_s"] >= 0
        assert 0.0 <= entry["decode_share_of_e2e"] <= 1.0
        assert entry["throughput_method"] in ("decode_metrics", "gross_e2e")

    def test_profile_meta_records_every_wave(self, tmp_path, mocked_engine):
        _, _, results = self._run(tmp_path, mocked_engine)
        waves = results["profile_meta"]["waves"]
        # 1 warmup + 3 measured.
        assert len(waves) == 4
        assert sum(1 for w in waves if w["warmup"]) == 1
        assert all(w["end_unix"] >= w["start_unix"] for w in waves)

    def test_status_heartbeat_written(self, tmp_path, mocked_engine):
        _, out_dir, _ = self._run(tmp_path, mocked_engine)
        status = json.loads((out_dir / "status" / "baseline.json").read_text())
        assert status["phase"] in ("bucket_done", "correctness_done")
        assert "updated_at" in status

    def test_server_launched_with_derived_flags(self, tmp_path, mocked_engine):
        self._run(tmp_path, mocked_engine)
        cmd = mocked_engine[0].cmd
        assert cmd[0] == "pumice-server"
        assert cmd[cmd.index("--model") + 1] == "/models/m"
        assert cmd[cmd.index("--max-model-len") + 1] == "4096"
        # max_num_seqs defaults to the largest batch size in the sweep.
        assert cmd[cmd.index("--max-num-seqs") + 1] == "2"

    def test_baseline_arm_env_has_no_opt_flag(self, tmp_path, mocked_engine):
        self._run(tmp_path, mocked_engine)
        assert "PUMICE_X" not in mocked_engine[0].env

    def test_inherited_stale_flag_stripped_from_launch_env(
        self, tmp_path, mocked_engine, monkeypatch
    ):
        monkeypatch.setenv("PUMICE_LEFTOVER_FROM_ROUND_1", "1")
        self._run(tmp_path, mocked_engine)
        assert "PUMICE_LEFTOVER_FROM_ROUND_1" not in mocked_engine[0].env

    def test_second_run_archives_prior_evidence(self, tmp_path, mocked_engine):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        argv = [
            "--artifact-dir", str(artifact),
            "--round", "1",
            "--slot", "baseline",
            "--labels", "baseline",
            "--repo-root", str(repo),
            "--prompt-source", "raw",
        ]
        assert bench.main(argv) == 0
        assert bench.main(argv) == 0
        archives = list((artifact / "rounds" / "1" / "_archive").glob("baseline_*"))
        assert len(archives) == 1
        assert (archives[0] / "e2e_latency_results.json").exists()


class TestSmokeStaleEngineRefusal:
    def test_stale_build_aborts_before_launching_the_server(self, tmp_path, mocked_engine):
        """The whole point of the D3/D5 gate: refuse, do not measure."""
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        # Make a source NEWER than the build.
        newer = _FUTURE_MTIME + 1000
        source = repo / "pumice-rust" / "src" / "engine.rs"
        os.utime(source, (newer, newer))
        with pytest.raises(SystemExit, match="REFUSING TO MEASURE"):
            bench.main(
                [
                    "--artifact-dir", str(artifact),
                    "--round", "1",
                    "--slot", "baseline",
                    "--labels", "baseline",
                    "--repo-root", str(repo),
                    "--prompt-source", "raw",
                ]
            )
        assert mocked_engine == []

    def test_override_measures_but_stamps_the_evidence(self, tmp_path, mocked_engine):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        newer = _FUTURE_MTIME + 1000
        os.utime(repo / "pumice-rust" / "src" / "engine.rs", (newer, newer))
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "baseline",
                "--labels", "baseline",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
                "--allow-stale-engine",
            ]
        )
        assert rc == 0
        out = artifact / "rounds" / "1" / "sweeps" / "baseline"
        results = json.loads((out / "e2e_latency_results.json").read_text())
        assert results["engine_freshness"]["overridden"] is True
        assert results["engine_freshness"]["reason"] == "stale_artifact"
        # The MD report must warn loudly — a reader skimming it should not
        # mistake these numbers for gate evidence.
        assert "--allow-stale-engine" in (out / "e2e_latency_results.md").read_text()


class TestSmokeGateSlotGuards:
    def test_multi_launch_rejected_on_gate_slot(self, tmp_path, mocked_engine):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        with pytest.raises(SystemExit, match="not allowed with --slot"):
            bench.main(
                [
                    "--artifact-dir", str(artifact),
                    "--round", "1",
                    "--slot", "opt/op007",
                    "--labels", "opt",
                    "--repo-root", str(repo),
                    "--prompt-source", "raw",
                    "--num-launches", "3",
                ]
            )
        assert mocked_engine == []

    def test_opt_only_without_baseline_warns_and_records(self, tmp_path, mocked_engine):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "opt/op007",
                "--labels", "opt",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
            ]
        )
        assert rc == 0
        results = json.loads(
            (
                artifact / "rounds" / "1" / "sweeps" / "opt" / "op007"
                / "e2e_latency_results.json"
            ).read_text()
        )
        assert results["baseline_source"] == "none"
        assert any("baseline-from" in w for w in results["warnings"])
        # The unrun baseline arm must be recorded as ok=None, not ok=False.
        assert results["results"][0]["baseline"]["ok"] is None
        assert results["results"][0]["opt"]["ok"] is True


class TestSmokeMultiLaunch:
    def test_warmup_launch_discarded_and_aggregate_emitted(self, tmp_path, mocked_engine):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "baseline",
                "--labels", "baseline",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
                "--num-launches", "3",
            ]
        )
        assert rc == 0
        out = artifact / "rounds" / "1" / "sweeps" / "baseline"
        results = json.loads((out / "e2e_latency_results.json").read_text())
        entry = results["results"][0]["baseline"]
        assert len(entry["launches"]) == 3
        assert sum(1 for e in entry["launches"] if e["warmup"]) == 1
        assert entry["aggregate"]["num_measured_launches"] == 2
        # avg_s must follow the aggregate so downstream speedup math is consistent.
        assert entry["avg_s"] == pytest.approx(entry["aggregate"]["mean_latency"])
        # Per-launch raw JSON is archived so a launch is debuggable after the fact.
        assert (out / "json" / "baseline_bs2.launch1.json").exists()
        assert (out / "json" / "baseline_bs2.launch3.json").exists()
        assert len(mocked_engine) == 3


class _ScriptedTokenizer(_MockTokenizer):
    """Decodes every completion to a caller-supplied answer string.

    Lets a test drive run_correctness_phase to a real PASS/FAIL verdict without a
    model: the GSM8K labels come from the bundled corpus, and this controls what
    the "model" appears to have answered.
    """

    def __init__(self, answers):
        self._answers = list(answers)
        self._i = 0

    def decode(self, ids, skip_special_tokens=False):
        answer = self._answers[self._i % len(self._answers)]
        self._i += 1
        return f"The answer is {answer}"


class _FakeServer:
    def __init__(self):
        self.stub = _MockStub()
        self.pb2 = _MockPb2


class TestCorrectnessPhase:
    """Gate 5.1b plumbing: capture -> verify, over the same Generate RPC."""

    def _labels(self, n):
        _prompts, labels = bench._build_gsm8k_prompts(num_questions=n)
        return labels

    def test_no_flags_is_a_no_op(self, tmp_path):
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_MockTokenizer(),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=False,
            verify_correctness=False,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        assert rc == 0
        assert not list(tmp_path.iterdir())

    def test_capture_writes_golden_refs_with_labels_and_preds(self, tmp_path):
        labels = self._labels(4)
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(labels),
            json_dir=tmp_path,
            num_questions=4,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        assert rc == 0
        golden = json.loads((tmp_path / "golden_refs.json").read_text())
        meta = golden["metadata"]
        assert meta["num_questions"] == 4
        assert meta["labels"] == labels
        assert meta["baseline_preds"] == labels  # every answer correct
        assert meta["gsm8k_accuracy"] == pytest.approx(1.0)
        assert len(golden["outputs"]) == 4

    def test_verify_pass_when_accuracy_holds(self, tmp_path):
        labels = self._labels(4)
        for capture in (True, False):
            rc = bench.run_correctness_phase(
                server=_FakeServer(),
                tokenizer=_ScriptedTokenizer(labels),
                json_dir=tmp_path,
                num_questions=4,
                tolerance_pct=1.0,
                capture_golden_refs=capture,
                verify_correctness=not capture,
                request_timeout_s=5,
                max_tokens=16,
                log=lambda _m: None,
            )
            assert rc == 0
        verdict = json.loads((tmp_path / "correctness_verdict.json").read_text())
        assert verdict["gate"] == "5.1b"
        assert verdict["verdict"] == "PASS"
        assert verdict["optimized_correct_count"] == 4
        assert (tmp_path / "opt_outputs.json").exists()

    def test_verify_fail_returns_code_3(self, tmp_path):
        labels = self._labels(4)
        bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(labels),
            json_dir=tmp_path,
            num_questions=4,
            tolerance_pct=0.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        # Now every answer is wrong: accuracy drops 100pp against a 0.0 tolerance.
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer([-1]),
            json_dir=tmp_path,
            num_questions=4,
            tolerance_pct=0.0,
            capture_golden_refs=False,
            verify_correctness=True,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        assert rc == 3
        verdict = json.loads((tmp_path / "correctness_verdict.json").read_text())
        assert verdict["verdict"] == "FAIL"
        assert verdict["optimized_correct_count"] == 0

    def test_missing_golden_refs_is_infrastructure_error(self, tmp_path):
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_MockTokenizer(),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=False,
            verify_correctness=True,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        assert rc == 4

    def test_num_questions_mismatch_is_infrastructure_error(self, tmp_path):
        labels = self._labels(4)
        bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(labels),
            json_dir=tmp_path,
            num_questions=4,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(labels),
            json_dir=tmp_path,
            num_questions=6,
            tolerance_pct=1.0,
            capture_golden_refs=False,
            verify_correctness=True,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        assert rc == 4

    def test_zero_baseline_accuracy_flagged_as_infrastructure(self, tmp_path):
        """A model answering nothing correctly means the env is broken, not slow."""
        bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer([-1]),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer([-1]),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=False,
            verify_correctness=True,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        assert rc == 4
        verdict = json.loads((tmp_path / "correctness_verdict.json").read_text())
        assert verdict["infrastructure_error"] is True

    def test_greedy_non_ignore_eos_sampling_used(self, tmp_path):
        """Correctness must let the model STOP; the timing loop pins the length."""
        server = _FakeServer()
        bench.run_correctness_phase(
            server=server,
            tokenizer=_ScriptedTokenizer([1]),
            json_dir=tmp_path,
            num_questions=2,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        params = server.stub.requests[0].sampling_params
        assert params.temperature == 0.0
        assert params.ignore_eos is False
        assert params.max_tokens == 16

    def test_rpc_failure_is_infrastructure_error(self, tmp_path):
        server = _FakeServer()
        server.stub = _MockStub(fail="UNAVAILABLE")
        rc = bench.run_correctness_phase(
            server=server,
            tokenizer=_MockTokenizer(),
            json_dir=tmp_path,
            num_questions=2,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            log=lambda _m: None,
        )
        assert rc == 4
        assert not (tmp_path / "golden_refs.json").exists()


class TestResolveLocalModelPath:
    """Pumice's Rust loader takes a directory — HF ids must resolve locally.

    Found live: a DSv4 session passed the raw HF id straight to --model and
    the server panicked (async_llm.rs: Failed to load tokenizer from
    nvidia/DeepSeek-V4-Flash-NVFP4/tokenizer.json).
    """

    def test_local_directory_passes_through(self, tmp_path):
        model = tmp_path / "ckpt"
        model.mkdir()
        assert bench._resolve_local_model_path(str(model)) == str(model)

    def test_hf_id_resolves_to_cached_snapshot(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
        snap = (
            tmp_path / "hf" / "hub" / "models--org--Model"
            / "snapshots" / "abc123"
        )
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}", encoding="utf-8")
        assert bench._resolve_local_model_path("org/Model") == str(snap)

    def test_uncached_hf_id_aborts_with_both_locations(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
        with pytest.raises(SystemExit, match="neither a local directory nor"):
            bench._resolve_local_model_path("org/NotDownloaded")


class TestPlanDpCohort:
    """--data-parallel-size N is N processes; a lone rank 0 hangs at rendezvous.

    Found live: the runner launched one process with --data-parallel-size 4
    and it froze at the DP rendezvous (GPU stuck at 754MiB, weights never
    loaded) until the sweep timeout killed it.
    """

    def _cmd(self, *extra):
        return [
            "pumice-server", "--model", "/m", "--port", "50051",
            "--tensor-parallel-size", "1", *extra,
        ]

    def test_dp1_needs_no_cohort(self):
        assert bench.plan_dp_cohort(
            server_cmd=self._cmd(), env={}, tp=1
        ) is None

    def test_operator_pinned_rank_is_left_alone(self):
        cmd = self._cmd("--data-parallel-size", "4", "--data-parallel-rank", "2")
        assert bench.plan_dp_cohort(server_cmd=cmd, env={}, tp=1) is None

    def test_dp4_plans_four_ranks_with_disjoint_gpus(self):
        ports = iter(range(20000, 20100))
        cmd = self._cmd("--data-parallel-size", "4")
        rank0_cmd, rank0_env, secondaries = bench.plan_dp_cohort(
            server_cmd=cmd,
            env={"CUDA_VISIBLE_DEVICES": "0,1,2,3"},
            tp=1,
            port_factory=lambda: next(ports),
        )
        assert bench._flag_value(rank0_cmd, "--data-parallel-rank") == "0"
        assert rank0_env["CUDA_VISIBLE_DEVICES"] == "0"
        assert len(secondaries) == 3
        coord = bench._flag_value(rank0_cmd, "--data-parallel-port")
        store = bench._flag_value(rank0_cmd, "--data-parallel-store-port")
        assert coord and store and coord != store
        grpc_ports = {bench._flag_value(rank0_cmd, "--port")}
        for i, (sec_cmd, sec_env, sec_port) in enumerate(secondaries, start=1):
            assert bench._flag_value(sec_cmd, "--data-parallel-rank") == str(i)
            assert sec_env["CUDA_VISIBLE_DEVICES"] == str(i)
            # Shared coordinator, distinct gRPC port per rank.
            assert bench._flag_value(sec_cmd, "--data-parallel-port") == coord
            assert bench._flag_value(sec_cmd, "--data-parallel-store-port") == store
            assert bench._flag_value(sec_cmd, "--port") == str(sec_port)
            grpc_ports.add(str(sec_port))
        assert len(grpc_ports) == 4

    def test_tp2_dp2_splits_devices_in_tp_blocks(self):
        cmd = self._cmd("--data-parallel-size", "2")
        rank0_cmd, rank0_env, secondaries = bench.plan_dp_cohort(
            server_cmd=cmd,
            env={"CUDA_VISIBLE_DEVICES": "4,5,6,7"},
            tp=2,
            port_factory=iter(range(21000, 21100)).__next__,
        )
        assert rank0_env["CUDA_VISIBLE_DEVICES"] == "4,5"
        assert secondaries[0][1]["CUDA_VISIBLE_DEVICES"] == "6,7"

    def test_too_few_gpus_aborts(self):
        cmd = self._cmd("--data-parallel-size", "4")
        with pytest.raises(SystemExit, match="needs 4 GPUs"):
            bench.plan_dp_cohort(
                server_cmd=cmd, env={"CUDA_VISIBLE_DEVICES": "0,1"}, tp=1
            )

    def test_smoke_sweep_launches_full_cohort(self, tmp_path, mocked_engine):
        """End-to-end: a DP=2 target launches 2 processes, drives only rank 0."""
        artifact = _make_artifact(
            tmp_path, extra_args=["--data-parallel-size", "2"]
        )
        repo = _make_repo(tmp_path)
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "baseline",
                "--labels", "baseline",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
                "--warmup-waves", "1",
            ]
        )
        assert rc == 0
        assert len(mocked_engine) == 2, (
            "a --data-parallel-size 2 sweep must launch a 2-process cohort"
        )
        ranks = sorted(
            bench._flag_value(s.cmd, "--data-parallel-rank") for s in mocked_engine
        )
        assert ranks == ["0", "1"]
        # Only rank 0 is driven over gRPC; both get supervised logs.
        stubs = [s for s in mocked_engine if s.stub is not None]
        assert len(stubs) == 1


class TestWS46RowStamping:
    """WS4.6: every row + the header carry host / gpu / model_runner / *_version."""

    def _run(self, tmp_path, mocked_engine, extra_env=None):
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        rc = bench.main(
            [
                "--artifact-dir", str(artifact),
                "--round", "1",
                "--slot", "baseline",
                "--labels", "baseline",
                "--repo-root", str(repo),
                "--prompt-source", "raw",
            ]
        )
        out_dir = artifact / "rounds" / "1" / "sweeps" / "baseline"
        return rc, json.loads((out_dir / "e2e_latency_results.json").read_text())

    def test_header_carries_host_gpu_runner_torch_vllm(self, tmp_path, mocked_engine, monkeypatch):
        monkeypatch.delenv("AMMO_ACCELERATOR_KIND", raising=False)
        _, results = self._run(tmp_path, mocked_engine)
        for key in ("host", "gpu", "model_runner", "torch_version", "vllm_version"):
            assert key in results
        assert results["model_runner"] == "vllm"

    def test_every_row_carries_stamp(self, tmp_path, mocked_engine, monkeypatch):
        monkeypatch.delenv("AMMO_ACCELERATOR_KIND", raising=False)
        _, results = self._run(tmp_path, mocked_engine)
        for row in results["results"]:
            for key in ("host", "gpu", "model_runner", "torch_version", "vllm_version"):
                assert key in row

    def test_neuron_kind_sets_neuron_runner_and_null_vllm(
        self, tmp_path, mocked_engine, monkeypatch
    ):
        monkeypatch.setenv("AMMO_ACCELERATOR_KIND", "neuron")
        _, results = self._run(tmp_path, mocked_engine)
        assert results["model_runner"] == "neuron"
        assert results["vllm_version"] is None
        for row in results["results"]:
            assert row["model_runner"] == "neuron"
            assert row["vllm_version"] is None

    def test_l40s_gpu_stamps_harness_validation_only(self, tmp_path, mocked_engine, monkeypatch):
        monkeypatch.delenv("AMMO_ACCELERATOR_KIND", raising=False)

        def _fake_stamp():
            return {
                "host": "g6e-cdd",
                "gpu": "NVIDIA L40S",
                "torch_version": "2.99.0+cu999",
                "vllm_version": "0.29.0",
            }

        monkeypatch.setattr(bench, "_detect_host_stamp", _fake_stamp)
        _, results = self._run(tmp_path, mocked_engine)
        assert results["harness_label"] == "harness validation only"
        for row in results["results"]:
            assert row["harness_label"] == "harness validation only"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestCorrectnessStopStrings:
    """The vLLM-era runner stopped GSM8K decode at "Question" etc.; Pumice has no
    stop field, so the runner truncates the decoded text before scoring."""

    def test_truncate_at_earliest_stop(self):
        text = "12 apples\n#### 12\n\nQuestion: next one? 99 Assistant: 7"
        assert bench._truncate_at_stop(text, bench._GSM8K_STOP_STRINGS) == (
            "12 apples\n#### 12\n\n"
        )
        assert bench._truncate_at_stop(text, []) == text

    def test_capture_scores_before_the_run_on_and_records_stop(self, tmp_path):
        _prompts, labels = bench._build_gsm8k_prompts(num_questions=3)
        run_on = [f"{label}\n\nQuestion: more 999999" for label in labels]
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(run_on),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        assert rc == 0
        meta = json.loads((tmp_path / "golden_refs.json").read_text())["metadata"]
        assert meta["stop"] == list(bench._GSM8K_STOP_STRINGS)
        assert meta["baseline_preds"] == labels
        assert meta["gsm8k_accuracy"] == pytest.approx(1.0)

    def test_verify_against_legacy_refs_does_not_truncate(self, tmp_path):
        """Refs captured before this change have no "stop" key: scoring must
        match them, so a live campaign's Stage 5 runs stay comparable."""
        _prompts, labels = bench._build_gsm8k_prompts(num_questions=3)
        run_on = [f"{label}\n\nQuestion: more 999999" for label in labels]
        bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(run_on),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=True,
            verify_correctness=False,
            request_timeout_s=5,
            max_tokens=16,
            log=lambda _m: None,
        )
        golden_path = tmp_path / "golden_refs.json"
        golden = json.loads(golden_path.read_text())
        del golden["metadata"]["stop"]
        golden["metadata"]["baseline_preds"] = [999999] * 3
        golden["metadata"]["gsm8k_accuracy"] = 0.0
        golden_path.write_text(json.dumps(golden))
        notes = []
        rc = bench.run_correctness_phase(
            server=_FakeServer(),
            tokenizer=_ScriptedTokenizer(run_on),
            json_dir=tmp_path,
            num_questions=3,
            tolerance_pct=1.0,
            capture_golden_refs=False,
            verify_correctness=True,
            request_timeout_s=5,
            max_tokens=16,
            log=notes.append,
        )
        verdict = json.loads((tmp_path / "correctness_verdict.json").read_text())
        # Untruncated text scores the trailing 999999, so no label matches.
        assert verdict["optimized_correct_count"] == 0
        assert any("no 'stop' list" in n for n in notes)
        assert rc in (0, 4)
