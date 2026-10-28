#!/usr/bin/env python3
"""Contract tests for run_pumice_grpc_bench.py: CLI, target.json, output schema.

Decision record: docs/PUMICE_MIGRATION.md D5.

These are the tests that protect the DOWNSTREAM CONSUMERS. The bench runner's
output shape is read by verify_validation_gates.py, generate_validation_report.py,
ammo_state.py and eval/scripts/parse_artifacts.py, none of which know or care
which engine produced the numbers — so the schema is a hard contract and is
asserted here against a golden dict.

Pure functions only: no gRPC, no server, no GPU, no network. The runner imports
``grpc`` / ``pumice_pb`` / ``transformers`` lazily inside functions precisely so
this file runs on a bare interpreter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_pumice_grpc_bench as bench  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------


def _target_spec(**overrides) -> dict:
    """A minimal valid target.json payload."""
    spec = {
        "target": {
            "model_id": "/models/gpt-oss-120b",
            "dtype": "mxfp4",
            "tp": 1,
            "ep": 1,
            "max_model_len": 8192,
        },
        "workload": {
            "input_len": 1024,
            "output_len": 512,
            "batch_sizes": [1, 8],
            "num_iters": 5,
        },
        "bench": {
            "runner": "pumice_grpc_bench",
            "server_cmd": "pumice-server",
            "extra_args": ["--enable-chunked-prefill"],
            "baseline_extra_args": [],
            "opt_extra_args": [],
            "baseline_env": {},
            "opt_env": {"PUMICE_MY_OPT": "1"},
            "baseline_label": "baseline",
            "opt_label": "opt",
            "fastpath_evidence": {
                "baseline": {"require_patterns": [], "forbid_patterns": []},
                "opt": {"require_patterns": ["fast path"], "forbid_patterns": []},
            },
        },
    }
    for dotted, value in overrides.items():
        node = spec
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        if value is _DELETE:
            node.pop(parts[-1], None)
        else:
            node[parts[-1]] = value
    return spec


class _Delete:
    pass


_DELETE = _Delete()


def _wave(index, latency, *, warmup=False, profiled=False, pf=None, dc=None, t0=1000.0):
    return bench.WaveRecord(
        index=index,
        label="baseline",
        tag="bs8",
        batch_size=8,
        input_len=1024,
        output_len=512,
        warmup=warmup,
        profiled=profiled,
        start_unix=t0,
        end_unix=t0 + latency,
        latency_s=latency,
        num_requests=8,
        num_errors=0,
        prefill_mean_s=pf,
        decode_mean_s=dc,
        completion_tokens=8 * 512,
    )


# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    def test_minimal_invocation_defaults(self):
        args = bench.parse_args(["--artifact-dir", "/tmp/a"])
        assert args.artifact_dir == "/tmp/a"
        assert args.selected_labels == ["baseline"]
        assert args.num_launches == 1
        assert args.warmup_waves == 1
        # Unset on the CLI: main() resolves it against bench.prompt_source.
        assert args.prompt_source is None
        assert args.overwrite is False
        assert args.nsys_profile is False
        assert args.allow_stale_engine is False

    def test_health_timeout_defaults_to_1800s_for_first_launch_jit(self):
        """D5: first launch on a fresh FlashInfer cache JITs for ~11 minutes."""
        args = bench.parse_args(["--artifact-dir", "/tmp/a"])
        assert args.health_timeout_s == 1800

    def test_temperature_defaults_to_zero(self):
        """A/B parity requires greedy decode; a nonzero default would add noise."""
        assert bench.parse_args(["--artifact-dir", "/tmp/a"]).temperature == 0.0

    def test_round_and_slot_parsed(self):
        args = bench.parse_args(
            ["--artifact-dir", "/tmp/a", "--round", "3", "--slot", "opt/op007"]
        )
        assert args.round == 3
        assert args.slot == "opt/op007"

    @pytest.mark.parametrize(
        "labels,expected",
        [
            ("baseline", ["baseline"]),
            ("opt", ["opt"]),
            ("baseline,opt", ["baseline", "opt"]),
            (" baseline , opt ", ["baseline", "opt"]),
        ],
    )
    def test_labels_parsing(self, labels, expected):
        args = bench.parse_args(["--artifact-dir", "/tmp/a", "--labels", labels])
        assert args.selected_labels == expected

    def test_unknown_label_rejected(self):
        with pytest.raises(SystemExit, match="--labels"):
            bench.parse_args(["--artifact-dir", "/tmp/a", "--labels", "baseline,turbo"])

    def test_empty_labels_rejected(self):
        with pytest.raises(SystemExit, match="--labels"):
            bench.parse_args(["--artifact-dir", "/tmp/a", "--labels", " , "])

    @pytest.mark.parametrize(
        "flags,match",
        [
            (["--num-launches", "0"], "--num-launches"),
            (["--warmup-waves", "-1"], "--warmup-waves"),
            (["--profile-waves", "0"], "--profile-waves"),
            (["--health-timeout-s", "0"], "--health-timeout-s"),
            (["--temperature", "-0.5"], "--temperature"),
            (["--nsys-trace", "cuda-sw"], "--nsys-trace requires --nsys-profile"),
            (["--nsys-extra-flags=--stats=true"], "--nsys-extra-flags requires"),
            (["--correctness-num-questions", "-1"], "--correctness-num-questions"),
            (["--correctness-tolerance-pct", "-1"], "--correctness-tolerance-pct"),
            (["--correctness-max-tokens", "0"], "--correctness-max-tokens must be > 0"),
            (["--correctness-max-tokens", "-5"], "--correctness-max-tokens must be > 0"),
        ],
    )
    def test_invalid_combinations_rejected(self, flags, match):
        with pytest.raises(SystemExit, match=match):
            bench.parse_args(["--artifact-dir", "/tmp/a", *flags])

    def test_correctness_max_tokens_defaults_to_1024(self):
        """Default unchanged so no other campaign shifts (constraints.md L850)."""
        args = bench.parse_args(["--artifact-dir", "/tmp/a"])
        assert args.correctness_max_tokens == 1024

    def test_correctness_max_tokens_parsed(self):
        args = bench.parse_args(["--artifact-dir", "/tmp/a", "--correctness-max-tokens", "4096"])
        assert args.correctness_max_tokens == 4096

    def test_correctness_max_tokens_reaches_correctness_phase(self):
        """The CLI arg must be forwarded to the sole run_correctness_phase call.

        Regression guard for the dead parameter: run_correctness_phase has always
        had a max_tokens kwarg, but the call site in run_label_launch never passed
        it, so the CLI value could never take effect.
        """
        import inspect

        src = inspect.getsource(bench.run_label_launch)
        assert "max_tokens=args.correctness_max_tokens" in src
        # run_correctness_phase still exposes the parameter it receives.
        assert "max_tokens" in inspect.signature(bench.run_correctness_phase).parameters

    def test_capture_and_verify_are_mutually_exclusive(self):
        with pytest.raises(SystemExit, match="mutually exclusive"):
            bench.parse_args(
                [
                    "--artifact-dir", "/tmp/a",
                    "--capture-golden-refs",
                    "--verify-correctness",
                    "--baseline-from", "/tmp/b",
                ]
            )

    def test_verify_correctness_requires_baseline_from(self):
        with pytest.raises(SystemExit, match="requires --baseline-from"):
            bench.parse_args(["--artifact-dir", "/tmp/a", "--verify-correctness"])

    def test_random_prompts_rejected_for_correctness(self):
        """GSM8K scoring needs the real corpus; random tokens would score ~0."""
        with pytest.raises(SystemExit, match="random"):
            bench.parse_args(
                [
                    "--artifact-dir", "/tmp/a",
                    "--capture-golden-refs",
                    "--prompt-source", "random",
                ]
            )

    def test_nsys_profile_accepts_blackwell_trace(self):
        args = bench.parse_args(
            ["--artifact-dir", "/tmp/a", "--nsys-profile", "--nsys-trace", "cuda-sw"]
        )
        assert args.nsys_profile is True
        assert args.nsys_trace == "cuda-sw"


# ---------------------------------------------------------------------------
# target.json parsing
# ---------------------------------------------------------------------------


class TestParseTargetSpec:
    def test_valid_spec_round_trips(self):
        tgt = bench.parse_target_spec(_target_spec())
        assert tgt.model_id == "/models/gpt-oss-120b"
        assert tgt.tp == 1 and tgt.ep == 1 and tgt.max_model_len == 8192
        assert tgt.num_iters == 5
        assert [b["batch_size"] for b in tgt.buckets] == [1, 8]
        assert tgt.opt_env == {"PUMICE_MY_OPT": "1"}
        assert tgt.opt_require == ("fast path",)
        assert tgt.runner_deprecated is False

    def test_num_iters_propagates_onto_every_bucket(self):
        tgt = bench.parse_target_spec(_target_spec())
        assert all(b["num_iters"] == 5 for b in tgt.buckets)

    def test_default_runner_required(self):
        with pytest.raises(SystemExit, match="Unsupported bench.runner"):
            bench.parse_target_spec(_target_spec(**{"bench.runner": "llmperf_grpc"}))

    def test_legacy_runner_flagged_not_rejected(self):
        """Pre-migration target.json must still load (deprecated, not broken)."""
        tgt = bench.parse_target_spec(
            _target_spec(**{"bench.runner": "vllm_bench_latency"})
        )
        assert tgt.runner_deprecated is True
        assert tgt.model_id == "/models/gpt-oss-120b"

    def test_placeholder_opt_env_key_rejected(self):
        with pytest.raises(SystemExit, match="placeholder"):
            bench.parse_target_spec(
                _target_spec(**{"bench.opt_env": {"<ENABLE_FLAG>": "1"}})
            )

    def test_placeholder_opt_env_tolerated_when_opt_arm_not_selected(self, capsys):
        # new_target.py scaffolds opt_env as {"<ENABLE_FLAG>": "1"}; the real flag
        # is only known in Stage 5. A Stage 1 `--labels baseline` run must load.
        tgt = bench.parse_target_spec(
            _target_spec(**{"bench.opt_env": {"<ENABLE_FLAG>": "1", "PUMICE_KEEP": "1"}}),
            require_opt_env=False,
        )
        assert tgt.opt_env == {"PUMICE_KEEP": "1"}
        assert "placeholder" in capsys.readouterr().err

    def test_placeholder_opt_env_still_rejected_when_required(self):
        with pytest.raises(SystemExit, match="placeholder"):
            bench.parse_target_spec(
                _target_spec(**{"bench.opt_env": {"<ENABLE_FLAG>": "1"}}),
                require_opt_env=True,
            )

    def test_bucket_input_len_below_template_overhead_fails_fast(self):
        # gpt-oss chat template overhead is 67 tokens; the 64/512 default bucket
        # must be rejected before the server launch, not after the golden refs.
        shape = bench.ChatTemplateShape(prefix=tuple(range(60)), suffix=tuple(range(7)))
        buckets = [
            {"input_len": 64, "output_len": 512, "batch_size": 1},
            {"input_len": 128, "output_len": 512, "batch_size": 8},
        ]
        with pytest.raises(SystemExit, match=r"input_len \[64\].*overhead is 67"):
            bench.check_buckets_fit_template(buckets, shape)
        bench.check_buckets_fit_template(buckets[1:], shape)  # 128 > 67: ok
        bench.check_buckets_fit_template(buckets, None)  # --prompt-source raw: ok

    def test_placeholder_model_id_rejected(self):
        with pytest.raises(SystemExit, match="placeholder"):
            bench.parse_target_spec(_target_spec(**{"target.model_id": "<FILL_ME>"}))

    def test_missing_required_field_rejected(self):
        with pytest.raises(SystemExit, match="target.max_model_len"):
            bench.parse_target_spec(_target_spec(**{"target.max_model_len": _DELETE}))

    def test_bucket_exceeding_max_model_len_rejected(self):
        with pytest.raises(SystemExit, match="max_model_len"):
            bench.parse_target_spec(
                _target_spec(
                    **{"workload.input_len": 8000, "workload.output_len": 1000}
                )
            )

    def test_zero_num_iters_rejected(self):
        with pytest.raises(SystemExit, match="num_iters"):
            bench.parse_target_spec(_target_spec(**{"workload.num_iters": 0}))

    def test_workload_matrix_form_supported(self):
        spec = _target_spec()
        spec["workload"] = {
            "workload_matrix": [
                {"input_len": 128, "output_len": 256, "batch_size": 1},
                {"input_len": 1024, "output_len": 512, "batch_size": 8},
            ],
            "num_iters": 3,
        }
        tgt = bench.parse_target_spec(spec)
        assert len(tgt.buckets) == 2
        assert tgt.buckets[0]["input_len"] == 128

    def test_duplicate_matrix_bucket_rejected(self):
        spec = _target_spec()
        entry = {"input_len": 128, "output_len": 256, "batch_size": 1}
        spec["workload"] = {"workload_matrix": [entry, dict(entry)], "num_iters": 3}
        with pytest.raises(SystemExit, match="Duplicate bucket"):
            bench.parse_target_spec(spec)

    def test_artifact_dir_mismatch_rejected(self, tmp_path):
        spec = _target_spec()
        spec["artifact_dir"] = str(tmp_path / "elsewhere")
        with pytest.raises(SystemExit, match="does not match"):
            bench.parse_target_spec(spec, artifact_dir=tmp_path / "here")

    def test_artifact_dir_match_accepted(self, tmp_path):
        spec = _target_spec()
        spec["artifact_dir"] = str(tmp_path)
        assert bench.parse_target_spec(spec, artifact_dir=tmp_path).tp == 1


class TestBucketTagging:
    def test_homogeneous_buckets_use_bs_tag(self):
        buckets = [
            {"input_len": 64, "output_len": 512, "batch_size": bs} for bs in (1, 8)
        ]
        assert [bench._bucket_file_tag(b, buckets) for b in buckets] == ["bs1", "bs8"]

    def test_heterogeneous_buckets_use_il_ol_bs_tag(self):
        buckets = [
            {"input_len": 64, "output_len": 512, "batch_size": 1},
            {"input_len": 128, "output_len": 256, "batch_size": 8},
        ]
        assert [bench._bucket_file_tag(b, buckets) for b in buckets] == [
            "il64_ol512_bs1",
            "il128_ol256_bs8",
        ]


# ---------------------------------------------------------------------------
# Output JSON schema — the downstream contract
# ---------------------------------------------------------------------------


class TestRawBucketSchema:
    """summarize_waves() output IS the vLLM-era per-bucket JSON schema."""

    def test_golden_schema_exact(self):
        waves = [
            _wave(1, 9.0, warmup=True),
            _wave(2, 10.0),
            _wave(3, 12.0),
            _wave(4, 14.0),
        ]
        raw = bench.summarize_waves(waves)
        assert raw == {
            "avg_latency": 12.0,
            "latencies": [10.0, 12.0, 14.0],
            "percentiles": {
                "10": 10.4,
                "25": 11.0,
                "50": 12.0,
                "75": 13.0,
                "99": 13.96,
                "90": 13.6,
            },
        }

    def test_percentile_keys_are_string_integers(self):
        """_metrics_from_bench_json maps "50" -> p50_s; non-digit keys are dropped."""
        raw = bench.summarize_waves([_wave(1, 1.0), _wave(2, 2.0)])
        assert set(raw["percentiles"]) == {"10", "25", "50", "75", "90", "99"}
        assert all(k.isdigit() for k in raw["percentiles"])

    def test_warmup_waves_excluded_from_latencies(self):
        raw = bench.summarize_waves(
            [_wave(1, 100.0, warmup=True), _wave(2, 1.0), _wave(3, 3.0)]
        )
        assert raw["latencies"] == [1.0, 3.0]
        assert raw["avg_latency"] == 2.0

    def test_all_warmup_raises(self):
        with pytest.raises(SystemExit, match="no measured"):
            bench.summarize_waves([_wave(1, 1.0, warmup=True)])

    def test_phase_split_emitted_when_available(self):
        waves = [_wave(i, 10.0, pf=0.2, dc=9.8) for i in (1, 2, 3)]
        raw = bench.summarize_waves(waves)
        assert raw["prefill_avg_s"] == pytest.approx(0.2)
        assert raw["decode_avg_s"] == pytest.approx(9.8)
        assert raw["decode_share_of_e2e"] == pytest.approx(9.8 / 10.0)
        assert raw["num_request_metric_samples"] == 3
        # Length-matched series feed the phase Welch in the report generator.
        assert len(raw["prefill_iter_means_s"]) == len(raw["latencies"])
        assert len(raw["decode_iter_means_s"]) == len(raw["latencies"])

    def test_phase_keys_omitted_when_unavailable(self):
        raw = bench.summarize_waves([_wave(1, 1.0), _wave(2, 2.0)])
        for key in (
            "prefill_avg_s",
            "decode_avg_s",
            "decode_share_of_e2e",
            "prefill_iter_means_s",
        ):
            assert key not in raw

    def test_partial_phase_series_dropped_not_shortened(self):
        """A misaligned series would give the phase Welch a different n than e2e."""
        waves = [_wave(1, 10.0, pf=0.2, dc=9.8), _wave(2, 10.0), _wave(3, 10.0)]
        raw = bench.summarize_waves(waves)
        assert raw["num_request_metric_samples"] == 1
        assert "prefill_iter_means_s" not in raw

    def test_itl_block_additive(self):
        raw = bench.summarize_waves([_wave(1, 1.0), _wave(2, 2.0)], itls=[0.004, 0.006])
        assert raw["itl"]["num_samples"] == 2
        assert raw["itl"]["p50_s"] == pytest.approx(0.005)

    def test_spec_decode_block_omitted_when_none(self):
        raw = bench.summarize_waves([_wave(1, 1.0), _wave(2, 2.0)], spec_decode=None)
        assert "spec_decode" not in raw

    def test_spec_decode_block_attached_when_present(self):
        block = {"draft_acceptance_rate": 0.55}
        raw = bench.summarize_waves([_wave(1, 1.0), _wave(2, 2.0)], spec_decode=block)
        assert raw["spec_decode"] == block


class TestMetricsFromBenchJson:
    def test_percentiles_become_pN_s_keys(self):
        metrics = bench._metrics_from_bench_json(
            {"avg_latency": 2.0, "percentiles": {"50": 1.9, "90": 2.4}}
        )
        assert metrics == {"avg_s": 2.0, "p50_s": 1.9, "p90_s": 2.4}

    def test_non_numeric_ignored(self):
        assert bench._metrics_from_bench_json({"avg_latency": "slow"}) == {}

    def test_error_payload_yields_empty_metrics(self):
        assert bench._metrics_from_bench_json({"error": "boom"}) == {}

    def test_phase_keys_passed_through(self):
        metrics = bench._metrics_from_bench_json(
            {"avg_latency": 1.0, "prefill_avg_s": 0.1, "decode_share_of_e2e": 0.9}
        )
        assert metrics["prefill_avg_s"] == 0.1
        assert metrics["decode_share_of_e2e"] == 0.9


class TestResultsHeaderSchema:
    def test_gate_verified_keys_present(self):
        """verify_validation_gates.py validates each of these envelope keys."""
        args = bench.parse_args(["--artifact-dir", "/tmp/a"])
        header = bench.build_results_header(
            bench.parse_target_spec(_target_spec()),
            args=args,
            target_path=Path("/art/target.json"),
            out_root=Path("/art/rounds/1/sweeps/baseline"),
            out_name="rounds/1/sweeps/baseline",
            server_exe=["pumice-server"],
            freshness_payload={"ok": True, "reason": "fresh"},
            generated_at="2026-07-27T00:00:00+00:00",
        )
        assert header["execution_mode"] == "grpc_server_sweep"
        assert header["runner"] == "pumice_grpc_bench"
        assert header["target_json"] == "/art/target.json"
        assert header["out_dir"] == "/art/rounds/1/sweeps/baseline"
        assert header["bench"]["baseline_label"] == "baseline"
        assert header["bench"]["opt_label"] == "opt"
        assert header["results"] == []
        assert header["engine_freshness"]["ok"] is True

    @pytest.mark.xfail(
        raises=AttributeError,
        reason=(
            "verify_validation_gates.py on main accepts only inproc_sweep. "
            "The CANONICAL_EXECUTION_MODES set with grpc_server_sweep is not "
            "ported yet (4ae2414a hunk; owner of verify_validation_gates.py)."
        ),
    )
    def test_execution_mode_accepted_by_gate_verifier(self):
        import verify_validation_gates as gates

        assert bench.EXECUTION_MODE in gates.CANONICAL_EXECUTION_MODES
        # The retired runner's mode stays accepted so pre-migration artifacts verify.
        assert "inproc_sweep" in gates.CANONICAL_EXECUTION_MODES

    def test_workload_block_records_measurement_config(self):
        args = bench.parse_args(
            ["--artifact-dir", "/tmp/a", "--warmup-waves", "3", "--prompt-source", "raw"]
        )
        header = bench.build_results_header(
            bench.parse_target_spec(_target_spec()),
            args=args,
            target_path=Path("/art/target.json"),
            out_root=Path("/art/out"),
            out_name="out",
            server_exe=["pumice-server"],
            freshness_payload={},
        )
        assert header["workload"]["warmup_waves"] == 3
        assert header["workload"]["prompt_source"] == "raw"
        assert header["workload"]["temperature"] == 0.0
        assert header["workload"]["num_iters"] == 5


class TestFinalizeRow:
    def _entries(self, b_avg, o_avg, **extra):
        return (
            {"avg_s": b_avg, **extra.get("baseline", {})},
            {"avg_s": o_avg, **extra.get("opt", {})},
        )

    def test_speedup_and_improvement_identities(self):
        """verify_validation_gates.py recomputes these and rejects mismatches."""
        b, o = self._entries(2.0, 1.6)
        row = bench.finalize_row(
            {"batch_size": 8},
            baseline_entry=b,
            opt_entry=o,
            baseline_raw={},
            opt_raw={},
            output_len=512,
            batch_size=8,
        )
        assert row["speedup"] == pytest.approx(2.0 / 1.6)
        assert row["improvement_pct"] == pytest.approx(20.0)

    def test_missing_arm_omits_speedup(self):
        b, o = self._entries(None, 1.6)
        row = bench.finalize_row(
            {},
            baseline_entry=b,
            opt_entry=o,
            baseline_raw={},
            opt_raw={},
            output_len=512,
            batch_size=8,
        )
        assert "speedup" not in row
        assert "improvement_pct" not in row

    def test_welch_significance_attached(self):
        row = bench.finalize_row(
            {},
            baseline_entry={"avg_s": 2.0},
            opt_entry={"avg_s": 1.0},
            baseline_raw={"latencies": [2.0, 2.01, 1.99, 2.0]},
            opt_raw={"latencies": [1.0, 1.01, 0.99, 1.0]},
            output_len=512,
            batch_size=8,
        )
        assert row["significance"]["method"] == "welch_within_launch"
        assert row["significance"]["significant"] is True

    def test_welch_omitted_below_two_samples(self):
        row = bench.finalize_row(
            {},
            baseline_entry={"avg_s": 2.0},
            opt_entry={"avg_s": 1.0},
            baseline_raw={"latencies": [2.0]},
            opt_raw={"latencies": [1.0]},
            output_len=512,
            batch_size=8,
        )
        assert "significance" not in row

    def test_throughput_uses_measured_decode_time(self):
        row = bench.finalize_row(
            {},
            baseline_entry={"avg_s": 2.0, "decode_avg_s": 1.0},
            opt_entry={"avg_s": 1.0, "decode_avg_s": 0.5},
            baseline_raw={},
            opt_raw={},
            output_len=11,
            batch_size=4,
        )
        assert row["otps_gain_pct"] == pytest.approx(100.0)
        assert row["tpot_improvement_pct"] == pytest.approx(50.0)

    def test_noise_flag_only_with_multi_launch_aggregates(self):
        row = bench.finalize_row(
            {},
            baseline_entry={
                "avg_s": 1.0,
                "aggregate": {"mean_latency": 1.0, "stddev_cross_launch": 0.5},
            },
            opt_entry={
                "avg_s": 0.99,
                "aggregate": {"mean_latency": 0.99, "stddev_cross_launch": 0.5},
            },
            baseline_raw={},
            opt_raw={},
            output_len=512,
            batch_size=8,
        )
        assert row["noise"] is True

    def test_noise_flag_absent_single_launch(self):
        row = bench.finalize_row(
            {},
            baseline_entry={"avg_s": 1.0},
            opt_entry={"avg_s": 0.9},
            baseline_raw={},
            opt_raw={},
            output_len=512,
            batch_size=8,
        )
        assert "noise" not in row


class TestLabelResultEntry:
    def test_gate_required_keys(self):
        entry = bench._build_label_result_entry(
            cmd=["pumice-server", "--model", "m"],
            env_overrides={"PUMICE_MY_OPT": "1"},
            metrics={"avg_s": 1.5, "p50_s": 1.4},
            log_rel="logs/opt_bs8.log",
            output_json_rel="json/opt_bs8.json",
            runner_json_rel="json/opt_bs8.runner.json",
            ok=True,
            returncode=0,
            evidence_status="pass",
            evidence={"ok": True, "require_hits": [], "require_miss": [], "forbid_hits": []},
            timing={"start_time": "t0", "end_time": "t1", "duration_s": 1.0},
        )
        assert entry["ok"] is True
        assert entry["returncode"] == 0
        assert entry["avg_s"] == 1.5
        assert entry["output_json"] == "json/opt_bs8.json"
        assert entry["runner_json"] == "json/opt_bs8.runner.json"
        assert entry["fastpath_evidence"]["status"] == "pass"
        # Single-launch entries must NOT carry launches/aggregate.
        assert "launches" not in entry
        assert "aggregate" not in entry

    def test_aggregate_overrides_avg_s(self):
        entry = bench._build_label_result_entry(
            cmd=[],
            env_overrides={},
            metrics={"avg_s": 1.5},
            log_rel="l",
            output_json_rel="o",
            runner_json_rel="r",
            ok=True,
            returncode=0,
            evidence_status="not_configured",
            evidence={"ok": True},
            timing={},
            launches=[{"launch_id": 2, "warmup": False, "avg_latency": 2.0}],
            aggregate={"mean_latency": 2.0, "stddev_cross_launch": 0.0},
        )
        assert entry["avg_s"] == 2.0


class TestAggregateLaunches:
    def test_warmup_launch_discarded(self):
        agg = bench._aggregate_launches(
            [
                {"launch_id": 1, "warmup": True, "avg_latency": 99.0},
                {"launch_id": 2, "warmup": False, "avg_latency": 1.0},
                {"launch_id": 3, "warmup": False, "avg_latency": 3.0},
            ]
        )
        assert agg["num_measured_launches"] == 2
        assert agg["mean_latency"] == pytest.approx(2.0)
        assert agg["p50"] == pytest.approx(2.0)

    def test_single_measured_launch_has_zero_stddev(self):
        agg = bench._aggregate_launches(
            [{"launch_id": 1, "warmup": False, "avg_latency": 5.0}]
        )
        assert agg["stddev_cross_launch"] == 0.0

    def test_all_warmup_returns_none(self):
        assert bench._aggregate_launches(
            [{"launch_id": 1, "warmup": True, "avg_latency": 5.0}]
        ) is None


class TestPathResolution:
    def _artifact(self, tmp_path, current_round=1):
        artifact = tmp_path / "artifact"
        (artifact / "rounds" / str(current_round) / "sweeps").mkdir(parents=True)
        (artifact / "state.json").write_text(
            json.dumps({"campaign": {"current_round": current_round}}), encoding="utf-8"
        )
        return artifact

    def test_round_and_slot(self, tmp_path):
        artifact = self._artifact(tmp_path)
        args = bench.parse_args(
            ["--artifact-dir", str(artifact), "--round", "1", "--slot", "opt/op007"]
        )
        assert bench._resolve_sweep_out_name(args, artifact) == "rounds/1/sweeps/opt/op007"

    def test_slot_only_reads_current_round(self, tmp_path):
        artifact = self._artifact(tmp_path, current_round=3)
        args = bench.parse_args(["--artifact-dir", str(artifact), "--slot", "baseline"])
        assert bench._resolve_sweep_out_name(args, artifact) == "rounds/3/sweeps/baseline"

    def test_round_without_slot_rejected(self, tmp_path):
        artifact = self._artifact(tmp_path)
        args = bench.parse_args(["--artifact-dir", str(artifact), "--round", "2"])
        with pytest.raises(SystemExit, match="--round requires --slot"):
            bench._resolve_sweep_out_name(args, artifact)

    def test_custom_out_name_rejected_on_v2_layout(self, tmp_path):
        artifact = self._artifact(tmp_path)
        args = bench.parse_args(
            ["--artifact-dir", str(artifact), "--out-name", "somewhere_else"]
        )
        with pytest.raises(SystemExit, match="legacy-only"):
            bench._resolve_sweep_out_name(args, artifact)

    def test_legacy_layout_uses_out_name(self, tmp_path):
        artifact = tmp_path / "legacy"
        artifact.mkdir()
        args = bench.parse_args(
            ["--artifact-dir", str(artifact), "--out-name", "e2e_latency_combined"]
        )
        assert bench._resolve_sweep_out_name(args, artifact) == "e2e_latency_combined"

    def test_slot_on_legacy_layout_rejected(self, tmp_path):
        artifact = tmp_path / "legacy"
        artifact.mkdir()
        args = bench.parse_args(["--artifact-dir", str(artifact), "--slot", "baseline"])
        with pytest.raises(SystemExit, match="rounds/ does not exist"):
            bench._resolve_sweep_out_name(args, artifact)

    def test_existing_output_archived_into_round_archive(self, tmp_path):
        artifact = self._artifact(tmp_path)
        out = artifact / "rounds" / "1" / "sweeps" / "baseline"
        out.mkdir(parents=True, exist_ok=True)
        (out / "old.json").write_text("{}", encoding="utf-8")
        bench._prepare_out_root(
            artifact_dir=artifact, out_name="rounds/1/sweeps/baseline", overwrite=False
        )
        archives = list((artifact / "rounds" / "1" / "_archive").glob("baseline_*"))
        assert len(archives) == 1
        assert (archives[0] / "old.json").exists()
        assert not (out / "old.json").exists()

    def test_overwrite_discards_prior_evidence(self, tmp_path):
        artifact = self._artifact(tmp_path)
        out = artifact / "rounds" / "1" / "sweeps" / "baseline"
        out.mkdir(parents=True, exist_ok=True)
        (out / "old.json").write_text("{}", encoding="utf-8")
        bench._prepare_out_root(
            artifact_dir=artifact, out_name="rounds/1/sweeps/baseline", overwrite=True
        )
        assert not (out / "old.json").exists()
        assert not (artifact / "rounds" / "1" / "_archive").exists() or not list(
            (artifact / "rounds" / "1" / "_archive").iterdir()
        )


# ---------------------------------------------------------------------------
# AMMO-owned stubs (scripts/pumice_pb)
# ---------------------------------------------------------------------------


class TestOwnedStubs:
    """The runner loads its own stubs, made from pumice-rust/proto.

    The shipped Python proto comments out ``prompt_tokens_cached``. The last
    two tests need grpcio and protobuf, so they skip on a bare interpreter.
    """

    def test_stub_files_exist_without_grpc(self):
        pb_dir = _SCRIPTS_DIR / "pumice_pb"
        for name in ("__init__.py", "pumice_engine_pb2.py", "pumice_engine_pb2_grpc.py"):
            assert (pb_dir / name).is_file(), name
        grpc_src = (pb_dir / "pumice_engine_pb2_grpc.py").read_text(encoding="utf-8")
        assert "from . import pumice_engine_pb2 as pumice__engine__pb2" in grpc_src

    def test_loader_returns_owned_stubs_with_prompt_tokens_cached(self):
        pytest.importorskip("grpc")
        pytest.importorskip("google.protobuf")
        _grpc, pb2, pb2_grpc = bench._load_pumice_grpc()
        assert pb2.__name__ == "pumice_pb.pumice_engine_pb2"
        assert pb2.DESCRIPTOR.package == "pumice.grpc.engine"
        complete = pb2.GenerateComplete.DESCRIPTOR.fields_by_name["prompt_tokens_cached"]
        assert complete.number == 7
        chunk = pb2.GenerateStreamChunk.DESCRIPTOR.fields_by_name["prompt_tokens_cached"]
        assert chunk.number == 6
        assert hasattr(pb2_grpc, "PumiceEngineStub")

    def test_runner_request_fields_exist_in_owned_proto(self):
        """Every field the runner sets on a request must exist in the proto."""
        pytest.importorskip("grpc")
        pytest.importorskip("google.protobuf")
        _grpc, pb2, _pb2_grpc = bench._load_pumice_grpc()
        # The same constructor calls as _drive_one_request; an unknown field
        # name raises ValueError here.
        pb2.GenerateRequest(
            request_id="r0",
            tokenized=pb2.TokenizedInput(input_ids=[1, 2, 3]),
            sampling_params=pb2.SamplingParams(
                temperature=0.0, max_tokens=4, ignore_eos=True
            ),
            stream=True,
        )
        response = pb2.GenerateResponse(
            complete=pb2.GenerateComplete(
                finish_reason="length", completion_tokens=4, prompt_tokens_cached=2
            )
        )
        assert response.HasField("complete")
        assert response.complete.prompt_tokens_cached == 2
        chunk = pb2.GenerateResponse(chunk=pb2.GenerateStreamChunk(token_ids=[5]))
        assert chunk.HasField("chunk") and list(chunk.chunk.token_ids) == [5]
        assert "output_ids" in pb2.GenerateComplete.DESCRIPTOR.fields_by_name
        service = pb2.DESCRIPTOR.services_by_name["PumiceEngine"]
        assert {"Generate", "HealthCheck", "StartProfile", "StopProfile"} <= {
            m.name for m in service.methods
        }


class TestCorrectnessBitExactDiagnostic:
    """WS4.8: bit-exact block reports token-id parity for DP wide-EP.

    Verdict is 1pp accuracy (unchanged); this block is diagnostic only.
    """

    def _q(self, ids):
        return {
            "token_ids": list(ids),
            "logprobs": [],
            "text": "",
            "num_tokens": len(ids),
            "prompt_index": 0,
        }

    def test_all_tokens_match_reports_bit_exact_true(self):
        golden = [self._q([10, 20]), self._q([30, 40])]
        opt = [self._q([10, 20]), self._q([30, 40])]
        result = bench._compare_correctness(
            golden_refs=golden, opt_outputs=opt,
            labels=[42, 99], baseline_preds=[42, 99], opt_preds=[42, 99],
        )
        assert result["diagnostics"]["bit_exact"] is True
        assert result["diagnostics"]["bit_exact_matches"] == 2
        assert result["diagnostics"]["bit_exact_ratio_pct"] == 100.0

    def test_one_token_diverges_reports_bit_exact_false(self):
        golden = [self._q([10, 20]), self._q([30, 40])]
        opt = [self._q([10, 20]), self._q([30, 41])]  # second question diverges
        result = bench._compare_correctness(
            golden_refs=golden, opt_outputs=opt,
            labels=[42, 99], baseline_preds=[42, 99], opt_preds=[42, 99],
        )
        # Verdict stays PASS because accuracy is unchanged; the bit-exact
        # block flips to False. This is the DP wide-EP surface: numerics
        # drifted without moving the 1pp gate.
        assert result["verdict"] == "PASS"
        assert result["diagnostics"]["bit_exact"] is False
        assert result["diagnostics"]["bit_exact_matches"] == 1
        assert result["diagnostics"]["bit_exact_ratio_pct"] == 50.0

    def test_all_diverge_reports_zero_matches(self):
        golden = [self._q([10]), self._q([20])]
        opt = [self._q([11]), self._q([21])]
        result = bench._compare_correctness(
            golden_refs=golden, opt_outputs=opt,
            labels=[42, 99], baseline_preds=[42, 99], opt_preds=[42, 99],
        )
        assert result["diagnostics"]["bit_exact_matches"] == 0
        assert result["diagnostics"]["bit_exact_ratio_pct"] == 0.0

    def test_bit_exact_is_diagnostic_only_no_verdict_change(self):
        """Bit-exact is INFORMATIONAL — never promoted into the pass/fail path."""
        golden = [self._q([10]), self._q([20])]
        opt = [self._q([99]), self._q([88])]  # bit-different
        result = bench._compare_correctness(
            golden_refs=golden, opt_outputs=opt,
            labels=[42, 99], baseline_preds=[42, 99], opt_preds=[42, 99],
            tolerance_pct=1.0,
        )
        # Both arms score correct on the (unrelated) preds, so 1pp gate passes.
        assert result["verdict"] == "PASS"
        assert result["diagnostics"]["bit_exact"] is False

    def test_default_tolerance_is_1pp(self):
        golden = [self._q([i]) for i in range(100)]
        opt = [self._q([i]) for i in range(100)]
        # 99/100 baseline, 98/100 opt = 1pp drop; must PASS at 1pp tolerance.
        labels = list(range(100))
        baseline_preds = list(range(100))
        baseline_preds[0] = -1  # one wrong
        opt_preds = list(range(100))
        opt_preds[0] = -1
        opt_preds[1] = -1  # one more wrong
        result = bench._compare_correctness(
            golden_refs=golden, opt_outputs=opt,
            labels=labels, baseline_preds=baseline_preds, opt_preds=opt_preds,
        )
        assert result["tolerance_pct"] == 1.0
        assert result["verdict"] == "PASS"


class TestPrefixCacheRegimePin:
    """WS4.4: pin the regime through ``build_server_cmd`` on both arms."""

    def test_pin_on_appends_enable_prefix_caching(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"], model_id="/m", port=5005,
            max_model_len=4096, max_num_seqs=8, tensor_parallel_size=1,
            prefix_cache_regime="on",
        )
        assert cmd.count("--enable-prefix-caching") == 1
        assert "--no-enable-prefix-caching" not in cmd

    def test_pin_off_appends_no_enable_prefix_caching(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"], model_id="/m", port=5005,
            max_model_len=4096, max_num_seqs=8, tensor_parallel_size=1,
            prefix_cache_regime="off",
        )
        assert cmd.count("--no-enable-prefix-caching") == 1
        assert "--enable-prefix-caching" not in cmd

    def test_pin_strips_conflicting_extra_flag(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"], model_id="/m", port=5005,
            max_model_len=4096, max_num_seqs=8, tensor_parallel_size=1,
            prefix_cache_regime="on",
            extra_args=("--no-enable-prefix-caching",),
        )
        assert cmd.count("--enable-prefix-caching") == 1
        assert "--no-enable-prefix-caching" not in cmd

    def test_metrics_port_flag_present(self):
        cmd = bench.build_server_cmd(
            server_exe=["pumice-server"], model_id="/m", port=5005,
            max_model_len=4096, max_num_seqs=8, tensor_parallel_size=1,
            metrics_port=9101,
        )
        assert cmd[cmd.index("--metrics-port") + 1] == "9101"

    def test_regime_field_parsed_from_target(self):
        tgt = bench.parse_target_spec(_target_spec(**{
            "target.prefix_cache_regime": "on",
        }))
        assert tgt.prefix_cache_regime == "on"

    def test_regime_absent_is_optional(self):
        tgt = bench.parse_target_spec(_target_spec())
        assert tgt.prefix_cache_regime is None

    def test_regime_invalid_value_rejected(self):
        with pytest.raises(SystemExit, match="prefix_cache_regime"):
            bench.parse_target_spec(_target_spec(**{
                "target.prefix_cache_regime": "sometimes",
            }))

    def test_prompt_source_parsed_from_target(self):
        tgt = bench.parse_target_spec(_target_spec(**{"bench.prompt_source": "raw"}))
        assert tgt.prompt_source == "raw"

    def test_prompt_source_absent_is_optional(self):
        assert bench.parse_target_spec(_target_spec()).prompt_source is None

    def test_prompt_source_random_cannot_be_frozen(self):
        with pytest.raises(SystemExit, match="bench.prompt_source"):
            bench.parse_target_spec(_target_spec(**{"bench.prompt_source": "random"}))

    def test_prompt_source_resolution_order(self):
        # CLI flag wins, then target.json, then the gsm8k default.
        assert bench.resolve_prompt_source("gsm8k", "raw") == "gsm8k"
        assert bench.resolve_prompt_source(None, "raw") == "raw"
        assert bench.resolve_prompt_source(None, None) == "gsm8k"

    def test_cli_prompt_source_defaults_to_unset(self):
        args = bench.parse_args([
            "--artifact-dir", "/tmp/x", "--round", "1", "--slot", "baseline",
            "--labels", "baseline",
        ])
        assert args.prompt_source is None


class TestPrefixCacheRegimeVerify:
    """WS4.4: three per-arm checks + fail-closed on mismatched-arm fixture."""

    def _log(self, regime: str) -> str:
        return f"[server] Prefix caching launch override: confdb -> {regime}\n"

    def test_all_checks_pass_when_arms_agree(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--enable-prefix-caching"],
            opt_argv=["pumice-server", "--enable-prefix-caching"],
            baseline_log=self._log("on"),
            opt_log=self._log("on"),
            pinned_regime="on",
            baseline_counters={"hits": 100, "queries": 100},
            opt_counters={"hits": 100, "queries": 100},
        )
        assert report["failures"] == []
        assert report["regime_mismatch"] is False

    def test_flag_missing_from_argv_fails_closed(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--enable-prefix-caching"],
            opt_argv=["pumice-server"],
            baseline_log=self._log("on"),
            opt_log=self._log("on"),
            pinned_regime="on",
        )
        assert report["regime_mismatch"] is True
        assert any("opt argv" in f for f in report["failures"])

    def test_missing_override_log_line_fails_closed(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--enable-prefix-caching"],
            opt_argv=["pumice-server", "--enable-prefix-caching"],
            baseline_log="startup...\n",
            opt_log=self._log("on"),
            pinned_regime="on",
        )
        assert report["regime_mismatch"] is True
        assert any("baseline server log" in f for f in report["failures"])

    def test_counter_ratio_divergence_fails_closed(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--enable-prefix-caching"],
            opt_argv=["pumice-server", "--enable-prefix-caching"],
            baseline_log=self._log("on"),
            opt_log=self._log("on"),
            pinned_regime="on",
            baseline_counters={"hits": 0, "queries": 1000},
            opt_counters={"hits": 969, "queries": 1000},
        )
        assert report["regime_mismatch"] is True
        assert any("hit ratio" in f for f in report["failures"])

    def test_zero_queries_when_off_passes(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--no-enable-prefix-caching"],
            opt_argv=["pumice-server", "--no-enable-prefix-caching"],
            baseline_log=self._log("off"),
            opt_log=self._log("off"),
            pinned_regime="off",
            baseline_counters={"hits": 0, "queries": 0},
            opt_counters={"hits": 0, "queries": 0},
        )
        assert report["failures"] == []

    def test_nonzero_queries_when_off_fails_closed(self):
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--no-enable-prefix-caching"],
            opt_argv=["pumice-server", "--no-enable-prefix-caching"],
            baseline_log=self._log("off"),
            opt_log=self._log("off"),
            pinned_regime="off",
            baseline_counters={"hits": 0, "queries": 42},
            opt_counters={"hits": 0, "queries": 0},
        )
        assert report["regime_mismatch"] is True
        assert any("nonzero" in f for f in report["failures"])

    def test_mismatched_arm_fixture_fails_closed(self):
        """One arm enables prefix cache, the other disables — the whole point."""
        report = bench.verify_prefix_cache_regime(
            baseline_argv=["pumice-server", "--no-enable-prefix-caching"],
            opt_argv=["pumice-server", "--enable-prefix-caching"],
            baseline_log=self._log("off"),
            opt_log=self._log("on"),
            pinned_regime=None,
            baseline_counters={"hits": 0, "queries": 0},
            opt_counters={"hits": 969, "queries": 1000},
        )
        assert report["regime_mismatch"] is True
        assert report["failures"], "mismatched-arm fixture must fail closed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
