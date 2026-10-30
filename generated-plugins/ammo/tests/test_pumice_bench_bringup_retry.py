#!/usr/bin/env python3
"""Tests for the one automatic bring-up retry in run_pumice_grpc_bench.py.

At TP bring-up on Neuron, some workers can fail to attach rank 0's
TP-broadcast shared-memory segment with
``FileNotFoundError: [Errno 2] No such file or directory: '/psm_<hash>'``.
WorkerProcessMonitor then calls ``os._exit(1)`` and the whole server dies
before it is healthy. The root cause is unknown. The lead's instruction was:
retry the launch once; if it recurs, stop and report with both server logs.

Covers:
- the matcher is true on both real /psm_ excerpts and false on the real
  deterministic kv_segment crash and the interleaved warmup crash
- one retry, then healthy: recorded on the SAME launches entry
- a second failure exits and names both server logs
- a health timeout never retries
- a non-matching exit never retries
- an exit after bring-up (during a timed wave) never retries
- a DP cohort stops and restarts its secondaries on the retry

Fixtures are real log lines from the neuron-run2 harness
(/Users/jinhun/logs/pumice-verify/20260924-0320/neuron-run2/):
- EXCERPT_125300: server-log-excerpt-125300.txt lines 2-3 and 45-82.
- EXCERPT_125132: the grep hits (log lines 111-143) in
  shm-investigation-125500.txt. That grep did not capture the Traceback
  header or frame lines, so those 17 lines are the identical frames from
  EXCERPT_125300 (same code path, same line numbers).
- CRASH_125733_KV_SEGMENT: baseline-125733Z-kv-segment-crash.log lines
  29-67, 98-102, 104 and 169 (line 103 is one very long PyErr repr, left out).
- INTERLEAVED_124909: shm-investigation-125500.txt lines 21-71, a warmup
  crash whose two tracebacks interleave. That log also held /psm_ errors
  later, so it shows why only the FIRST traceback counts.
"""

from __future__ import annotations

import json
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
from test_pumice_bench_smoke import _make_artifact, _make_repo  # noqa: E402


# ---------------------------------------------------------------------------
# Real log fixtures
# ---------------------------------------------------------------------------

EXCERPT_125300 = r"""
=== launch 2026-09-24T12:51:39.046299+00:00 ===
/home/ubuntu/pumice/.venv/bin/python /home/ubuntu/pumice/rust_grpc_server.py --model /hf/gpt-oss-20b --port 43309 --max-model-len 8192 --tensor-parallel-size 8 --metrics-port 59895 --model-runner pumice --max-num-seqs 8
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=4, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=0, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=6, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=3, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=7, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=2, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=5, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:51 INFO pumice.engine.neuron_model_runner: Gloo distributed initialized: global_rank=1, world_size=8, tp_size=8, dp_size=1, init_method=tcp://localhost:42687
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=2, world_size=8, model=/hf/gpt-oss-20b
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
Process SpawnProcess-2:
Traceback (most recent call last):
  File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
    self.run()
  File "/usr/lib/python3.12/multiprocessing/process.py", line 108, in run
    self._target(*self._args, **self._kwargs)
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 788, in __init__
    self._tp_broadcast_mq = MessageQueue.create_from_handle(mq_handle, reader_rank)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/shm_broadcast.py", line 168, in create_from_handle
    self.buffer = ShmRingBuffer(*handle.buffer_handle)
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/shm_broadcast.py", line 78, in __init__
    self.shared_memory = shared_memory.SharedMemory(name=name)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/multiprocessing/shared_memory.py", line 104, in __init__
    self._fd = _posixshmem.shm_open(
               ^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '/psm_b229102d'
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=3, world_size=8, model=/hf/gpt-oss-20b
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=1, world_size=8, model=/hf/gpt-oss-20b
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
09-24 12:51:59 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
Process SpawnProcess-3:
Process SpawnProcess-1:
Traceback (most recent call last):
  File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
    self.run()
"""


EXCERPT_125132 = r"""
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=1, world_size=8, model=/hf/gpt-oss-20b
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=4, world_size=8, model=/hf/gpt-oss-20b
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=5, world_size=8, model=/hf/gpt-oss-20b
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
09-24 12:50:27 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
09-24 12:50:37 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=2, world_size=8, model=/hf/gpt-oss-20b
09-24 12:50:37 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
Process SpawnProcess-2:
Traceback (most recent call last):
  File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
    self.run()
  File "/usr/lib/python3.12/multiprocessing/process.py", line 108, in run
    self._target(*self._args, **self._kwargs)
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 788, in __init__
    self._tp_broadcast_mq = MessageQueue.create_from_handle(mq_handle, reader_rank)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/shm_broadcast.py", line 168, in create_from_handle
    self.buffer = ShmRingBuffer(*handle.buffer_handle)
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/shm_broadcast.py", line 78, in __init__
    self.shared_memory = shared_memory.SharedMemory(name=name)
                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.12/multiprocessing/shared_memory.py", line 104, in __init__
    self._fd = _posixshmem.shm_open(
               ^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '/psm_85011cb4'
09-24 12:50:37 INFO pumice.engine.neuron_model_runner: neuron_inference_core initialized for Pumice: rank=3, world_size=8, model=/hf/gpt-oss-20b
09-24 12:50:37 INFO pumice.engine.neuron_model_runner: Segmented prefill enabled: kv_segment_size=8192, buckets=[8192]
Process SpawnProcess-3:
"""


CRASH_125733_KV_SEGMENT = r"""
    raise ValueError(

Set TORCHDYNAMO_VERBOSE=1 for the internal stack trace (please do this especially if you're reporting a bug to PyTorch). For even more developer context, set TORCH_LOGS="+dynamo"

Process SpawnProcess-1:
torch._dynamo.exc.ObservedValueErrorError: raised exception ExceptionVariable(<class 'ValueError'>)

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
    self.run()
  File "/usr/lib/python3.12/multiprocessing/process.py", line 108, in run
    self._target(*self._args, **self._kwargs)
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 791, in __init__
    self._full_init()        # Signal completion
    ^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 832, in _full_init
    self._warmup()
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 1303, in _warmup
    parallel_warmup(
  File "/home/ubuntu/pumice/src/pumice/neuron_inference_core/compile/warmup_orchestrator.py", line 70, in parallel_warmup
    extract_fn()
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 1471, in _extract_all_graphs
    _ = self.capture_backend_model(**model_kwargs)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/eval_frame.py", line 472, in __call__
    return super().__call__(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/nn/modules/module.py", line 1779, in _wrapped_call_impl
    return self._call_impl(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/nn/modules/module.py", line 1790, in _call_impl
    return forward_call(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/eval_frame.py", line 1034, in compile_wrapper
    raise e.with_traceback(None) from e.__cause__  # User compiler error
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
torch._dynamo.exc.Unsupported: Observed exception
[rank0]: Traceback (most recent call last):
[rank0]:   File "/home/ubuntu/pumice/rust_grpc_server.py", line 287, in <module>
[rank0]:     main()
[rank0]:   File "/home/ubuntu/pumice/rust_grpc_server.py", line 282, in main
[rank0]:     pumice_rust.start_server(config)
Process SpawnProcess-4:
ERROR:pumice.vllm_adapter.multinode:TP worker SpawnProcess-1 exited unexpectedly with code 1; terminating local worker group
"""


INTERLEAVED_124909 = r"""
09-24 12:29:32 INFO pumice.engine.neuron_model_runner: Starting warmup: prefill_buckets=[8192], decode_buckets=[8], recompile_limit=64
09-24 12:29:32 INFO pumice.engine.neuron_model_runner: Using PARALLEL warmup orchestrator (capture_backend_model available, rank=6)
09-24 12:29:32 INFO pumice.engine.neuron_model_runner: Extracting prefill graph: bucket_size=8192
Process SpawnProcess-2:
Process SpawnProcess-3:
Traceback (most recent call last):
  File "/usr/lib/python3.12/multiprocessing/process.py", line 314, in _bootstrap
    self.run()
  File "/usr/lib/python3.12/multiprocessing/process.py", line 108, in run
    self._target(*self._args, **self._kwargs)
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 791, in __init__
    self._full_init()        # Signal completion
    ^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 832, in _full_init
    self._warmup()
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 1303, in _warmup
    parallel_warmup(
  File "/home/ubuntu/pumice/src/pumice/neuron_inference_core/compile/warmup_orchestrator.py", line 70, in parallel_warmup
    extract_fn()
  File "/home/ubuntu/pumice/src/pumice/engine/neuron_model_runner.py", line 1471, in _extract_all_graphs
    _ = self.capture_backend_model(**model_kwargs)
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/eval_frame.py", line 472, in __call__
    return super().__call__(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/nn/modules/module.py", line 1779, in _wrapped_call_impl
    return self._call_impl(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/nn/modules/module.py", line 1790, in _call_impl
    return forward_call(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/eval_frame.py", line 1024, in compile_wrapper
    return fn(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py", line 2316, in __call__
    result = self._torchdynamo_orig_backend(
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py", line 729, in __call__
    result = _compile(
             ^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py", line 1827, in _compile
    guarded_code, tracer_output = compile_inner(code, one_graph, hooks)
                                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_utils_internal.py", line 96, in wrapper_function
    return function(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^
Traceback (most recent call last):
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py", line 1500, in compile_inner
    return _compile_inner(code, one_graph, hooks)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/ubuntu/neuron-venv/lib/python3.12/site-packages/torch/_dynamo/convert_frame.py", line 1534, in _compile_inner
"""


HEALTHY_BODY = '{"_aws":{},"spec_acceptance_rate":55.0}\n'


# ---------------------------------------------------------------------------
# The pure matcher
# ---------------------------------------------------------------------------


class TestPsmAttachRaceMatcher:
    def test_true_on_real_excerpt_125300(self):
        assert bench.is_psm_attach_race(EXCERPT_125300) is True

    def test_true_on_real_excerpt_125132(self):
        assert bench.is_psm_attach_race(EXCERPT_125132) is True

    def test_false_on_real_deterministic_kv_segment_crash(self):
        assert bench.is_psm_attach_race(CRASH_125733_KV_SEGMENT) is False

    def test_false_on_real_interleaved_warmup_crash(self):
        assert bench.is_psm_attach_race(INTERLEAVED_124909) is False

    def test_false_when_psm_is_not_the_first_traceback(self):
        """A /psm_ failure after another crash is a victim, not the cause."""
        text = CRASH_125733_KV_SEGMENT + "\n" + EXCERPT_125300.split("\n", 3)[3]
        assert "/psm_" in text
        assert bench.is_psm_attach_race(text) is False

    def test_false_without_any_traceback(self):
        assert bench.is_psm_attach_race("") is False
        assert bench.is_psm_attach_race(HEALTHY_BODY) is False

    def test_only_the_latest_launch_counts(self):
        """The log opens in append mode, so an older launch's race is ignored."""
        text = EXCERPT_125300 + "\n=== launch later ===\ncmd\n" + CRASH_125733_KV_SEGMENT
        assert bench.is_psm_attach_race(text) is False
        text = CRASH_125733_KV_SEGMENT + EXCERPT_125300
        assert bench.is_psm_attach_race(text) is True


# ---------------------------------------------------------------------------
# End-to-end through main() with a scripted fake server
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_engine(monkeypatch):
    """A FakeServer whose rank-0 launches follow a script of modes.

    Modes: "ok" (healthy), "psm" (exits, log holds the /psm_ race), "crash"
    (exits, log holds the kv_segment crash), "timeout" (never healthy, log
    holds the race text), "wave_exit" (healthy, then exits during the first
    timed wave). The REAL
    ``ServerHandle.wait_healthy`` runs, so the exit and timeout paths are the
    production ones. DP secondaries are always "ok".
    """
    state = {"plan": [], "events": []}
    bodies = {
        "ok": HEALTHY_BODY,
        "psm": EXCERPT_125300,
        "crash": CRASH_125733_KV_SEGMENT,
        # The race text is in the log, so only the exit-vs-timeout check
        # keeps a timeout from being retried.
        "timeout": EXCERPT_125300,
        "wave_exit": EXCERPT_125300,
    }

    class FakeServer(bench.ServerHandle):
        def start(self):
            secondary = ".dp" in self.log_path.name
            self.mode = "ok" if secondary else state["plan"].pop(0)
            self.start_unix = 1000.0 + len(state["events"])
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode, like the real start(): a relaunch that did not
            # move the failed log aside would leave the race text in it.
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n=== launch fake-{len(state['events'])} ===\n")
                fh.write(bodies[self.mode])
            state["events"].append(("start", self.log_path.name))

        def connect(self):
            self.pb2 = _MockPb2
            never = self.mode == "timeout"
            self.stub = _MockStub(healthy_after=10**9 if never else 0)
            if self.mode == "wave_exit":
                log_path = self.log_path

                def _die(request, timeout=None):
                    raise bench.ServerExitedBeforeHealthy(f"exited. See {log_path}")

                self.stub.Generate = _die

        def poll(self):
            return 1 if self.mode in ("psm", "crash") else None

        def wait_healthy(self, *, timeout_s, poll_s=2.0, on_wait=None):
            return super().wait_healthy(
                timeout_s=min(timeout_s, 0.2), poll_s=0.01, on_wait=on_wait
            )

        def stop(self, **kwargs):
            state["events"].append(("stop", self.log_path.name))

    monkeypatch.setattr(bench, "ServerHandle", FakeServer)
    monkeypatch.setattr(bench, "resolve_server_exe", lambda spec, root: ["pumice-server"])
    monkeypatch.setattr(bench, "_load_tokenizer", lambda model_id: _MockTokenizer())
    return state


def _run(tmp_path, plan, state, **bench_overrides):
    state["plan"].extend(plan)
    artifact = _make_artifact(tmp_path, **bench_overrides)
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
    return rc, artifact / "rounds" / "1" / "sweeps" / "baseline"


def _starts(state):
    return [name for kind, name in state["events"] if kind == "start"]


class TestBringupRetry:
    def test_psm_race_retries_once_then_healthy(self, tmp_path, scripted_engine):
        rc, out_dir = _run(tmp_path, ["psm", "ok"], scripted_engine)
        assert rc == 0
        assert _starts(scripted_engine) == ["baseline.server.log"] * 2
        # The failed launch was stopped before the relaunch.
        assert scripted_engine["events"][:3] == [
            ("start", "baseline.server.log"),
            ("stop", "baseline.server.log"),
            ("start", "baseline.server.log"),
        ]
        logs = out_dir / "logs"
        failed = logs / "baseline.bringup-failed.server.log"
        assert "/psm_b229102d" in failed.read_text()
        # The healthy launch's log holds only the healthy launch, so the
        # regime verification that reads it sees no race text.
        live = (logs / "baseline.server.log").read_text()
        assert "/psm_" not in live and "Traceback" not in live

        results = json.loads((out_dir / "e2e_latency_results.json").read_text())
        metas = results["launches"]["baseline"]
        assert len(metas) == 1, "the retry must not add a launches entry"
        meta = metas[0]
        assert meta["bringup_retries"] == 1
        assert meta["bringup_retry_reason"] == "psm_attach_race"
        assert meta["bringup_failed_server_log"] == "logs/baseline.bringup-failed.server.log"
        assert meta["server_log"] == "logs/baseline.server.log"
        assert results["results"][0]["baseline"]["ok"] is True

    def test_healthy_launch_records_no_retry_fields(self, tmp_path, scripted_engine):
        rc, out_dir = _run(tmp_path, ["ok"], scripted_engine)
        assert rc == 0
        meta = json.loads((out_dir / "e2e_latency_results.json").read_text())[
            "launches"]["baseline"][0]
        assert not any(key.startswith("bringup_") for key in meta)
        assert not (out_dir / "logs" / "baseline.bringup-failed.server.log").exists()

    def test_second_failure_exits_naming_both_logs(self, tmp_path, scripted_engine):
        with pytest.raises(SystemExit) as info:
            _run(tmp_path, ["psm", "psm"], scripted_engine)
        message = str(info.value)
        assert "returncode=1 before becoming healthy" in message
        assert "baseline.bringup-failed.server.log" in message
        assert message.count("baseline.server.log") >= 2
        assert len(_starts(scripted_engine)) == 2, "at most one retry"

    def test_retry_that_times_out_exits_naming_both_logs(self, tmp_path, scripted_engine):
        with pytest.raises(SystemExit) as info:
            _run(tmp_path, ["psm", "timeout"], scripted_engine)
        message = str(info.value)
        assert "did not become healthy" in message
        assert "baseline.bringup-failed.server.log" in message
        assert len(_starts(scripted_engine)) == 2

    def test_timeout_never_retries(self, tmp_path, scripted_engine):
        with pytest.raises(SystemExit, match="did not become healthy") as info:
            _run(tmp_path, ["timeout"], scripted_engine)
        assert "bringup-failed" not in str(info.value)
        assert len(_starts(scripted_engine)) == 1

    def test_non_matching_exit_never_retries(self, tmp_path, scripted_engine):
        with pytest.raises(bench.ServerExitedBeforeHealthy) as info:
            _run(tmp_path, ["crash"], scripted_engine)
        assert "bringup-failed" not in str(info.value)
        assert len(_starts(scripted_engine)) == 1
        logs = tmp_path / "artifact" / "rounds" / "1" / "sweeps" / "baseline" / "logs"
        assert not (logs / "baseline.bringup-failed.server.log").exists()

    def test_exit_after_bringup_never_retries(self, tmp_path, scripted_engine):
        """Once a timed wave has started, even the race text is not retried."""
        with pytest.raises(SystemExit):
            _run(tmp_path, ["wave_exit"], scripted_engine)
        assert len(_starts(scripted_engine)) == 1

    def test_dp_secondaries_stop_and_restart(self, tmp_path, scripted_engine):
        rc, _ = _run(
            tmp_path, ["psm", "ok"], scripted_engine,
            extra_args=["--data-parallel-size", "2"],
        )
        assert rc == 0
        events = scripted_engine["events"]
        sec, rank0 = "baseline.dp1.server.log", "baseline.server.log"
        assert events[:6] == [
            ("start", sec),
            ("start", rank0),
            ("stop", rank0),
            ("stop", sec),
            ("start", sec),
            ("start", rank0),
        ]
