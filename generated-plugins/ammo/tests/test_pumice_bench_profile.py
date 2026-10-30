#!/usr/bin/env python3
"""Wave/profile_meta, prompt construction and mocked-gRPC tests.

Decision record: docs/PUMICE_MIGRATION.md D5.

Two things are being protected here.

1. **Capture identity.** The retired `.codex/.../sitecustomize.py` monkeypatched
   `vllm.profiler.wrapper` to stamp an `AMMO_CAPTURE_V1|...` identity into the
   server's NVTX ranges. Pumice has no such class, and the bench client is a
   SEPARATE PROCESS from the server, so client-side NVTX cannot appear in the
   server's nsys timeline at all. The replacement is `profile_meta.waves[]`:
   wall-clock boundaries per wave plus the StartProfile/StopProfile window,
   correlated against the capture range offline. If those boundaries are wrong
   or missing, an nsys report cannot be attributed to a workload — so they are
   tested as a contract.

2. **The gRPC drive loop**, exercised against mock stubs. No server, no GPU, no
   network: the runner imports `grpc` / `pumice_pb` / `transformers` lazily
   inside functions specifically so this file runs on a bare interpreter.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_pumice_grpc_bench as bench  # noqa: E402


# ---------------------------------------------------------------------------
# Mock gRPC surface (mirrors pumice-rust/proto/pumice_engine.proto)
# ---------------------------------------------------------------------------


class _Chunk:
    def __init__(self, token_ids):
        self.token_ids = list(token_ids)
        self.prompt_tokens = 0
        self.completion_tokens = len(self.token_ids)


class _Complete:
    def __init__(self, output_ids, finish_reason="length"):
        self.output_ids = list(output_ids)
        self.finish_reason = finish_reason
        self.prompt_tokens = 0
        self.completion_tokens = len(self.output_ids)


class _Response:
    """GenerateResponse's `oneof response { chunk | complete }`."""

    def __init__(self, *, chunk=None, complete=None):
        self.chunk = chunk
        self.complete = complete

    def HasField(self, name):  # noqa: N802 - protobuf API name
        return getattr(self, name) is not None


class _MockPb2:
    """Stand-in for pumice_pb.pumice_engine_pb2."""

    class TokenizedInput:
        def __init__(self, input_ids):
            self.input_ids = list(input_ids)

    class SamplingParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class GenerateRequest:
        def __init__(self, request_id, tokenized, sampling_params, stream):
            self.request_id = request_id
            self.tokenized = tokenized
            self.sampling_params = sampling_params
            self.stream = stream

    class HealthCheckRequest:
        pass

    class StartProfileRequest:
        pass

    class StopProfileRequest:
        pass


class _MockStub:
    """Records every RPC so ordering can be asserted."""

    def __init__(self, *, output_len=4, chunk_size=1, healthy_after=0, fail=None):
        self.output_len = output_len
        self.chunk_size = chunk_size
        self.healthy_after = healthy_after
        self.fail = fail
        self.calls: list[str] = []
        self.requests: list[object] = []
        self._health_calls = 0

    def Generate(self, request, timeout=None):  # noqa: N802
        self.calls.append("Generate")
        self.requests.append(request)
        if self.fail:
            raise RuntimeError(self.fail)
        emitted: list[int] = []
        n = int(request.sampling_params.max_tokens)
        while len(emitted) < n:
            batch = list(range(len(emitted), min(n, len(emitted) + self.chunk_size)))
            emitted.extend(batch)
            yield _Response(chunk=_Chunk(batch))
        yield _Response(complete=_Complete(emitted))

    def HealthCheck(self, request, timeout=None):  # noqa: N802
        self.calls.append("HealthCheck")
        self._health_calls += 1
        healthy = self._health_calls > self.healthy_after
        return types.SimpleNamespace(healthy=healthy, message="" if healthy else "loading")

    def StartProfile(self, request, timeout=None):  # noqa: N802
        self.calls.append("StartProfile")
        return types.SimpleNamespace(success=True, message="")

    def StopProfile(self, request, timeout=None):  # noqa: N802
        self.calls.append("StopProfile")
        return types.SimpleNamespace(success=True, message="")


class _MockTokenizer:
    """Chat template with a fixed 3-token prefix and 2-token suffix."""

    name_or_path = "mock/model"
    PREFIX = [1, 2, 3]
    SUFFIX = [98, 99]

    def encode(self, text, add_special_tokens=True):
        return [200 + (i % 50) for i in range(len(text.split()))]

    def decode(self, ids, skip_special_tokens=False):
        return " ".join(str(i) for i in ids)

    def apply_chat_template(self, messages, add_generation_prompt=True, tokenize=True):
        content = messages[0]["content"]
        body = self.encode(content, add_special_tokens=False)
        return list(self.PREFIX) + body + list(self.SUFFIX)


# ---------------------------------------------------------------------------
# profile_meta
# ---------------------------------------------------------------------------


def _wave(index, *, t0, latency, warmup=False, profiled=False):
    return bench.WaveRecord(
        index=index,
        label="opt",
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
        completion_tokens=4096,
    )


class TestProfileMeta:
    def test_wave_boundaries_recorded(self):
        waves = [
            _wave(1, t0=1000.0, latency=2.0, warmup=True),
            _wave(2, t0=1002.5, latency=2.0, profiled=True),
            _wave(3, t0=1005.0, latency=2.0),
        ]
        meta = bench.build_profile_meta(
            waves=waves,
            nsys_report_rel="nsys/opt.nsys-rep",
            server_start_unix=990.0,
            profile_start_unix=1002.0,
            profile_stop_unix=1008.0,
        )
        assert meta["schema"] == "AMMO_CAPTURE_V2"
        assert meta["nsys_report"] == "nsys/opt.nsys-rep"
        assert meta["profile_start_unix"] == 1002.0
        assert meta["profile_stop_unix"] == 1008.0
        assert meta["profile_window_s"] == pytest.approx(6.0)
        assert meta["profiled_wave_count"] == 1
        assert len(meta["waves"]) == 3

    def test_every_wave_carries_correlatable_boundaries(self):
        """Offline correlation needs start/end in BOTH unix and ISO form."""
        meta = bench.build_profile_meta(
            waves=[_wave(1, t0=1000.0, latency=2.0)],
            nsys_report_rel=None,
            server_start_unix=990.0,
            profile_start_unix=None,
            profile_stop_unix=None,
        )
        entry = meta["waves"][0]
        assert entry["start_unix"] == 1000.0
        assert entry["end_unix"] == 1002.0
        assert entry["duration_s"] == pytest.approx(2.0)
        assert entry["start_iso"].endswith("+00:00")
        assert entry["end_iso"].endswith("+00:00")

    def test_wave_identity_fields_present(self):
        """Replaces the deleted NVTX `context_N(M)_generation_R(G)` contract."""
        meta = bench.build_profile_meta(
            waves=[_wave(7, t0=1000.0, latency=1.0, profiled=True)],
            nsys_report_rel="nsys/opt.nsys-rep",
            server_start_unix=990.0,
            profile_start_unix=999.0,
            profile_stop_unix=1002.0,
        )
        entry = meta["waves"][0]
        for key in (
            "index",
            "label",
            "tag",
            "batch_size",
            "input_len",
            "output_len",
            "warmup",
            "profiled",
            "num_requests",
            "completion_tokens",
        ):
            assert key in entry, key
        assert entry["index"] == 7
        assert entry["profiled"] is True

    def test_window_keys_omitted_without_profiling(self):
        meta = bench.build_profile_meta(
            waves=[_wave(1, t0=1000.0, latency=1.0)],
            nsys_report_rel=None,
            server_start_unix=990.0,
            profile_start_unix=None,
            profile_stop_unix=None,
        )
        assert "profile_window_s" not in meta
        assert "profiled_wave_count" not in meta

    def test_capture_mechanism_names_both_halves_when_profiled(self):
        """With an active nsys capture the mechanism names both halves."""
        meta = bench.build_profile_meta(
            waves=[_wave(1, t0=1000.0, latency=1.0, profiled=True)],
            nsys_report_rel="nsys/opt.nsys-rep",
            server_start_unix=990.0,
            profile_start_unix=999.0,
            profile_stop_unix=1002.0,
        )
        assert "StartProfile" in meta["capture_mechanism"] or "start_stop" in (
            meta["capture_mechanism"]
        )
        assert "cudaProfilerApi" in meta["capture_mechanism"]

    def test_capture_mechanism_no_profiler_without_nsys(self):
        """A timing-only run must NOT carry a CUDA-profiler label (Neuron bug)."""
        meta = bench.build_profile_meta(
            waves=[_wave(1, t0=1000.0, latency=1.0)],
            nsys_report_rel=None,
            server_start_unix=990.0,
            profile_start_unix=None,
            profile_stop_unix=None,
        )
        assert meta["capture_mechanism"] == bench.NO_PROFILE_CAPTURE_MECHANISM
        assert "cudaProfilerApi" not in meta["capture_mechanism"]

    def test_capture_mechanism_explicit_override_wins(self):
        """An explicit capture_mechanism is recorded verbatim."""
        meta = bench.build_profile_meta(
            waves=[],
            nsys_report_rel=None,
            server_start_unix=None,
            profile_start_unix=None,
            profile_stop_unix=None,
            capture_mechanism="custom_mechanism",
        )
        assert meta["capture_mechanism"] == "custom_mechanism"

    def test_json_serializable(self):
        import json

        meta = bench.build_profile_meta(
            waves=[_wave(1, t0=1000.0, latency=1.0, profiled=True)],
            nsys_report_rel="nsys/x.nsys-rep",
            server_start_unix=990.0,
            profile_start_unix=999.0,
            profile_stop_unix=1002.0,
        )
        json.dumps(meta)


class TestNsysCommand:
    def test_capture_range_armed_on_cuda_profiler_api(self):
        """D5: nsys must record only between StartProfile and StopProfile."""
        cmd = bench.build_nsys_cmd(
            ["pumice-server", "--model", "/m"], report_path=Path("/out/nsys/opt")
        )
        assert cmd[:2] == ["nsys", "profile"]
        assert "--capture-range=cudaProfilerApi" in cmd
        assert "--capture-range-end=stop" in cmd
        assert cmd[-3:] == ["pumice-server", "--model", "/m"]

    def test_report_path_passed(self):
        cmd = bench.build_nsys_cmd(["pumice-server"], report_path=Path("/out/nsys/opt"))
        assert cmd[cmd.index("-o") + 1] == "/out/nsys/opt"

    def test_cuda_graph_trace_mode(self):
        cmd = bench.build_nsys_cmd(
            ["pumice-server"], report_path=Path("/o"), cuda_graph_trace="graph"
        )
        assert "--cuda-graph-trace=graph" in cmd

    def test_blackwell_software_trace(self):
        """cuda-sw avoids hardware-tracing stalls during CUDA-graph replay."""
        cmd = bench.build_nsys_cmd(
            ["pumice-server"], report_path=Path("/o"), trace="cuda-sw"
        )
        assert any(flag.startswith("--trace=") for flag in cmd)
        assert "--cuda-memory-usage=false" in cmd

    def test_extra_flags_precede_the_server_argv(self):
        cmd = bench.build_nsys_cmd(
            ["pumice-server", "--model", "/m"],
            report_path=Path("/o"),
            extra_flags=["--stats=true"],
        )
        assert cmd.index("--stats=true") < cmd.index("pumice-server")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    def test_chat_template_shape_detected(self):
        shape = bench.measure_chat_template_shape(_MockTokenizer(), "one two three")
        assert list(shape.prefix) == _MockTokenizer.PREFIX
        assert list(shape.suffix) == _MockTokenizer.SUFFIX
        assert shape.overhead == 5

    def test_prompts_are_exactly_input_len(self):
        shape = bench.measure_chat_template_shape(_MockTokenizer(), "one two three")
        prompts = bench.build_prompt_ids(
            shape=shape,
            corpus_tokens=list(range(1000)),
            input_len=64,
            output_len=128,
            batch_size=4,
        )
        assert len(prompts) == 4
        assert all(len(p) == 64 for p in prompts)

    def test_chat_template_prefix_and_suffix_preserved(self):
        """The server must see the same prompt shape a real client sends."""
        shape = bench.measure_chat_template_shape(_MockTokenizer(), "one two three")
        prompt = bench.build_prompt_ids(
            shape=shape,
            corpus_tokens=list(range(1000)),
            input_len=32,
            output_len=8,
            batch_size=1,
        )[0]
        assert prompt[: len(shape.prefix)] == list(shape.prefix)
        assert prompt[-len(shape.suffix):] == list(shape.suffix)

    def test_prompts_deterministic_across_calls(self):
        """Both A/B arms must see byte-identical prompts (crc32 seeding)."""
        args = dict(
            shape=None, corpus_tokens=list(range(1000)), input_len=32, output_len=8,
            batch_size=4,
        )
        assert bench.build_prompt_ids(**args) == bench.build_prompt_ids(**args)

    def test_prompts_differ_across_batch_slots(self):
        """N identical prompts would give prefix caching an unrealistic free ride."""
        prompts = bench.build_prompt_ids(
            shape=None,
            corpus_tokens=list(range(1000)),
            input_len=32,
            output_len=8,
            batch_size=4,
        )
        assert len({tuple(p) for p in prompts}) == 4

    def test_prompts_differ_across_workloads(self):
        a = bench.build_prompt_ids(
            shape=None, corpus_tokens=list(range(1000)), input_len=32,
            output_len=8, batch_size=1,
        )
        b = bench.build_prompt_ids(
            shape=None, corpus_tokens=list(range(1000)), input_len=32,
            output_len=16, batch_size=1,
        )
        assert a != b

    def test_corpus_wraps_when_shorter_than_input_len(self):
        prompt = bench.build_prompt_ids(
            shape=None, corpus_tokens=[7, 8, 9], input_len=10,
            output_len=4, batch_size=1,
        )[0]
        assert len(prompt) == 10
        assert set(prompt) <= {7, 8, 9}

    def test_input_len_below_template_overhead_is_fatal(self):
        shape = bench.measure_chat_template_shape(_MockTokenizer(), "one two three")
        with pytest.raises(SystemExit, match="too small"):
            bench.build_prompt_ids(
                shape=shape, corpus_tokens=list(range(100)), input_len=4,
                output_len=8, batch_size=1,
            )

    def test_random_prompts_deterministic_and_sized(self):
        a = bench.build_random_prompt_ids(input_len=16, output_len=8, batch_size=3)
        b = bench.build_random_prompt_ids(input_len=16, output_len=8, batch_size=3)
        assert a == b
        assert all(len(row) == 16 for row in a)
        assert len({tuple(row) for row in a}) == 3

    def test_timing_seed_is_hashseed_independent(self):
        """crc32, not builtin hash() — DP ranks are separate processes."""
        assert bench.timing_prompt_seed(64, 512, 8) == bench.timing_prompt_seed(64, 512, 8)
        assert bench.timing_prompt_seed(64, 512, 8) != bench.timing_prompt_seed(64, 512, 16)

    def test_bundled_corpus_loads(self):
        """Fail-loud contract: sessions have no network, the corpus must be bundled."""
        text = bench.load_timing_corpus_text()
        assert "Question:" in text and "Answer:" in text
        assert len(text) > 100_000

    def test_missing_corpus_is_fatal(self, tmp_path):
        with pytest.raises(SystemExit, match="re-bundle"):
            bench.load_timing_corpus_text(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Mocked gRPC drive loop
# ---------------------------------------------------------------------------


class TestDriveWave:
    def test_wave_latency_is_batch_wall_clock(self):
        stub = _MockStub()
        results, latency, t0, t1 = bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1, 2, 3]] * 4,
            output_len=8,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        assert len(results) == 4
        assert stub.calls.count("Generate") == 4
        assert latency > 0
        assert t1 >= t0

    def test_sampling_params_match_ab_contract(self):
        stub = _MockStub()
        bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1, 2, 3]],
            output_len=8,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        params = stub.requests[0].sampling_params
        assert params.temperature == 0.0
        assert params.max_tokens == 8
        # ignore_eos pins the decode length so every wave has identical shape.
        assert params.ignore_eos is True

    def test_pretokenized_input_used(self):
        """Pumice takes TokenizedInput.input_ids — never a raw text prompt."""
        stub = _MockStub()
        bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[11, 22, 33]],
            output_len=4,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        assert stub.requests[0].tokenized.input_ids == [11, 22, 33]

    def test_ttft_and_decode_split_measured(self):
        stub = _MockStub(chunk_size=1)
        results, _, _, _ = bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1, 2, 3]],
            output_len=6,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        r = results[0]
        assert r.first_token_t is not None
        assert r.prefill_s is not None and r.prefill_s >= 0
        assert r.decode_s is not None and r.decode_s >= 0
        assert r.completion_tokens == 6
        assert r.error is None

    def test_itl_samples_amortized_over_multi_token_chunks(self):
        """Spec decode emits bursts; one ITL per chunk would understate the count."""
        stub = _MockStub(chunk_size=3)
        results, _, _, _ = bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1, 2, 3]],
            output_len=9,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        # 9 tokens in 3-token chunks: first chunk sets TTFT, remaining 6 give ITLs.
        assert len(results[0].itls) == 6

    def test_rpc_error_captured_not_raised(self):
        stub = _MockStub(fail="UNAVAILABLE")
        results, _, _, _ = bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1, 2, 3]],
            output_len=4,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        assert results[0].error is not None
        assert "UNAVAILABLE" in results[0].error

    def test_empty_prompt_list_yields_zero_latency(self):
        results, latency, _, _ = bench.drive_wave(
            stub=_MockStub(),
            pb2=_MockPb2,
            prompts=[],
            output_len=4,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="w1",
        )
        assert results == []
        assert latency == 0.0

    def test_request_ids_unique_within_wave(self):
        stub = _MockStub()
        bench.drive_wave(
            stub=stub,
            pb2=_MockPb2,
            prompts=[[1]] * 5,
            output_len=2,
            temperature=0.0,
            ignore_eos=True,
            timeout_s=30,
            wave_id="opt-bs8-l1-w2",
        )
        ids = [r.request_id for r in stub.requests]
        assert len(set(ids)) == 5
        assert all(i.startswith("opt-bs8-l1-w2-r") for i in ids)


class TestServerHandleProfileBracketing:
    def _handle(self, stub):
        handle = bench.ServerHandle(
            cmd=["pumice-server"], env={}, port=1, log_path=Path("/dev/null")
        )
        handle.stub = stub
        handle.pb2 = _MockPb2
        return handle

    def test_health_polling_retries_until_ready(self):
        stub = _MockStub(healthy_after=2)
        self._handle(stub).wait_healthy(timeout_s=10, poll_s=0.001)
        assert stub.calls.count("HealthCheck") == 3

    def test_health_timeout_raises_with_actionable_message(self):
        stub = _MockStub(healthy_after=10_000)
        with pytest.raises(SystemExit, match="did not become healthy"):
            self._handle(stub).wait_healthy(timeout_s=0.05, poll_s=0.001)

    def test_start_stop_profile_rpcs_bracket_the_window(self):
        stub = _MockStub()
        handle = self._handle(stub)
        start = handle.start_profile()
        stop = handle.stop_profile()
        assert stub.calls == ["StartProfile", "StopProfile"]
        assert stop >= start

    def test_early_server_exit_detected_during_health_wait(self):
        stub = _MockStub(healthy_after=10_000)
        handle = self._handle(stub)
        handle.proc = types.SimpleNamespace(poll=lambda: 1, pid=1234)
        with pytest.raises(SystemExit, match="returncode=1"):
            handle.wait_healthy(timeout_s=5, poll_s=0.001)


# ---------------------------------------------------------------------------
# Server EMF metrics
# ---------------------------------------------------------------------------


class TestEmfSpecMetrics:
    def test_acceptance_rate_extracted(self):
        """The EMF gauge is a PERCENT (metrics.rs pct(): accepted/proposed*100);
        the harvested block normalizes to a 0-1 fraction for the results JSON."""
        log = "\n".join(
            [
                "starting engine loop",
                '{"_aws": {"Timestamp": 1}, "spec_acceptance_rate": 50.0}',
                '{"_aws": {"Timestamp": 2}, "spec_acceptance_rate": 70.0}',
            ]
        )
        block = bench.parse_emf_spec_metrics(log)
        assert block["draft_acceptance_rate"] == pytest.approx(0.6)
        assert block["draft_acceptance_rate_units"] == "fraction_0_to_1"
        assert block["gauge_mean_percent"] == pytest.approx(60.0)
        assert block["num_samples"] == 2
        assert block["source"] == "pumice_emf_metrics"

    def test_vanilla_decode_yields_none(self):
        """Omit-not-null: a non-spec-decode target must not get a spec block."""
        log = '{"_aws": {"Timestamp": 1}, "kv_cache_usage_percent": 12.0}'
        assert bench.parse_emf_spec_metrics(log) is None

    def test_zero_ticks_excluded_when_real_activity_exists(self):
        """Idle ticks before the draft path arms would deflate the mean."""
        log = "\n".join(
            [
                '{"_aws": {}, "spec_acceptance_rate": 0.0}',
                '{"_aws": {}, "spec_acceptance_rate": 60.0}',
            ]
        )
        block = bench.parse_emf_spec_metrics(log)
        assert block["draft_acceptance_rate"] == pytest.approx(0.6)
        assert block["num_samples"] == 1
        assert block["num_ticks"] == 2

    def test_malformed_lines_ignored(self):
        log = "\n".join(
            [
                "{not json",
                '{"_aws": {}, "spec_acceptance_rate": "n/a"}',
                '{"_aws": {}, "spec_acceptance_rate": 40.0}',
            ]
        )
        block = bench.parse_emf_spec_metrics(log)
        assert block["draft_acceptance_rate"] == pytest.approx(0.4)

    def test_empty_log_is_none(self):
        assert bench.parse_emf_spec_metrics("") is None


# ---------------------------------------------------------------------------
# Correctness comparator plumbing (Gate 5.1b)
# ---------------------------------------------------------------------------


class TestCorrectnessScoring:
    def test_scores_decoded_text_not_request_objects(self):
        """Pumice returns token IDs; the client decodes. No RequestOutput shape."""
        preds, accuracy = bench._score_gsm8k_predictions(
            ["The answer is 42", "Total: 7", "no number here"], [42, 7, 5]
        )
        assert preds[:2] == [42, 7]
        assert accuracy == pytest.approx(2 / 3)

    def test_empty_completion_scored_as_invalid(self):
        preds, accuracy = bench._score_gsm8k_predictions(["", None], [1, 2])
        assert accuracy == 0.0
        assert all(p == bench._INVALID_ANSWER for p in preds)

    def test_verdict_to_code_mapping(self):
        assert bench._verdict_to_code({"verdict": "PASS"}) == 0
        assert bench._verdict_to_code({"verdict": "FAIL"}) == 3
        assert bench._verdict_to_code({"verdict": "PASS", "infrastructure_error": True}) == 4
        assert bench._verdict_to_code({}) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
