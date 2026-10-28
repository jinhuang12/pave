#!/usr/bin/env python3
"""Run production-parity E2E latency sweeps against a Pumice gRPC server.

Replaces the vLLM-era ``run_vllm_bench_latency_sweep.py``. Pumice is a SERVER
(``pumice-server`` binary, or ``python rust_grpc_server.py``) reached over gRPC —
there is no in-process ``LLM`` class, no ``bench latency`` subcommand, and no
``vllm:*`` Prometheus counters. So the measurement shape changes from
"load the model once in-process and sweep batch sizes" to
"launch the server once per label, then drive N concurrent-request waves per
bucket over the ``Generate`` streaming RPC".

The OUTPUT contract is unchanged, deliberately: every downstream consumer
(``verify_validation_gates.py``, ``generate_validation_report.py``,
``ammo_state.py``, ``eval/scripts/parse_artifacts.py``) keeps working.

Inputs
------
Reads a JSON target spec (default: ``{artifact_dir}/target.json``) created by
``scripts/new_target.py``.

Minimal expected schema::

  {
    "artifact_dir": "...",
    "target": {"model_id": "...", "dtype": "mxfp4", "tp": 1, "ep": 1,
               "max_model_len": 8192},
    "workload": {"input_len": 1024, "output_len": 512,
                 "batch_sizes": [1, 8, 32], "num_iters": 10},
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
      "fastpath_evidence": {"opt": {"require_patterns": [], "forbid_patterns": []}}
    }
  }

Measurement model
-----------------
One *wave* == one benchmark iteration == ``batch_size`` requests submitted
concurrently over the ``Generate`` streaming RPC. The wave latency is
``max(complete_ts) - min(submit_ts)``, i.e. batch wall-clock time — the same
quantity ``vllm bench latency`` reported per iteration, so historical
``avg_latency`` numbers remain conceptually comparable (absolute values are
NOT: different engine, different client).

Per-request timings additionally give the prefill/decode split for free:

* ``prefill_s = first_token_ts - submit_ts``  (TTFT)
* ``decode_s  = last_token_ts  - first_token_ts``

which populate ``prefill_avg_s`` / ``decode_avg_s`` / ``decode_share_of_e2e``
exactly like the vLLM-era ``RequestOutput.metrics`` harvest did.

Prompts are built client-side with HF ``AutoTokenizer`` + the model's chat
template at ``temperature=0`` (Pumice takes pre-tokenized
``TokenizedInput.input_ids``). Content tokens come from the bundled GSM8K
corpus in deterministic crc32-seeded windows, so both A/B arms see
byte-identical prompts and speculative-decode acceptance stays faithful.

A/B methodology
---------------
Baseline and optimized arms differ ONLY by environment variables
(``bench.baseline_env`` / ``bench.opt_env``) and per-label server flags. Before
each arm's server launch the inherited environment is sanitized with
``_sanitize_engine_op_env`` (strips ``^PUMICE_`` and ``^VLLM_`` except the
image-level ``ENGINE_INFRA_ENV`` names), then the arm's own vars are re-added — so a stale flag from a previous round cannot
silently contaminate a measurement. See ``references/impl-track-rules.md``.

Confdb / Rust-source changes require a rebuild, which env vars cannot express.
That is what the freshness gate below is for.

Stale-binary freshness gate (decision D3/D5)
--------------------------------------------
Pumice's scheduler, block manager, spec-decode orchestration and backend
selection (confdb) all live in Rust and are compiled into the PyO3 extension
plus the ``pumice-server`` binary. ``PYTHONPATH`` tricks cannot make a source
edit take effect. So before measuring anything this runner compares the newest
mtime under ``pumice-rust/src/**`` and ``pumice-rust/confdb/*.toml`` against the
built artifacts and REFUSES to run when the binary is older. Override only with
``--allow-stale-engine`` (which stamps the output JSON so the evidence is
self-incriminating).

Profiling
---------
``--nsys-profile`` wraps the SERVER process in::

  nsys profile --capture-range=cudaProfilerApi --capture-range-end=stop ...

and brackets the profiled waves with the ``StartProfile`` / ``StopProfile``
RPCs (they set the pending cudaProfilerStart/Stop flags the engine loop
consumes). Client-side NVTX cannot appear in the server's timeline, so the
vLLM-era ``context_N(M)_generation_R(G)`` NVTX contract is replaced by
wall-clock wave boundaries recorded in the output JSON under
``profile_meta.waves[]``, correlated against the capture window offline.

Outputs
-------
Writes into ``{artifact_dir}/{out_name}/``::

    - e2e_latency_results.json / e2e_latency_results.md
    - logs/{label}_{tag}.log            (client-side driver log)
    - logs/{label}.server.log           (server stdout/stderr, incl. EMF lines)
    - logs/{label}.bringup-failed.server.log (only after a /psm_ bring-up retry)
    - json/{label}_{tag}.json           (raw per-bucket bench JSON)
    - json/{label}_{tag}.runner.json    (per-bucket runner status)
    - status/{label}.json               (heartbeat + current phase)
    - nsys/{label}.nsys-rep             (only with --nsys-profile)

Usage
-----
  .venv/bin/python scripts/run_pumice_grpc_bench.py \\
      --artifact-dir <dir> --round 1 --slot baseline --labels baseline
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import fcntl
import json
import math
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

PLACEHOLDER = "<FILL_ME>"

#: ``bench.runner`` value this script implements.
RUNNER_NAME = "pumice_grpc_bench"
#: Legacy vLLM-era runner name. Still accepted (loudly deprecated) so that
#: target.json files scaffolded before the Pumice migration keep loading.
LEGACY_RUNNER_NAME = "vllm_bench_latency"

#: Written into ``e2e_latency_results.json``. ``verify_validation_gates.py``
#: accepts this alongside the legacy ``inproc_sweep``.
EXECUTION_MODE = "grpc_server_sweep"

#: Percentile keys emitted in the raw per-bucket JSON. String-integer keys are
#: the downstream contract: ``_metrics_from_bench_json`` maps ``"50"`` -> ``p50_s``.
PERCENTILES: Tuple[int, ...] = (10, 25, 50, 75, 90, 99)

#: Bundled GSM8K corpus, co-located with the skill (no network in sessions).
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_GSM8K_FULL_PATH = _DATA_DIR / "gsm8k_full.json"
_GSM8K_SUBSET_PATH = _DATA_DIR / "gsm8k_subset.json"

#: Sentinel for "no parseable numeric answer" in GSM8K scoring.
_INVALID_ANSWER = -9999999

#: Rust/confdb inputs whose mtime the freshness gate compares against the build.
_ENGINE_SOURCE_GLOBS: Tuple[str, ...] = (
    "pumice-rust/src/**/*.rs",
    "pumice-rust/confdb/*.toml",
    "pumice-rust/build.rs",
    "pumice-rust/Cargo.toml",
    "Cargo.toml",
)

#: Built artifacts the freshness gate looks for, relative to a target dir.
_ENGINE_ARTIFACT_NAMES: Tuple[str, ...] = (
    "libpumice_rust.so",
    "libpumice_rust.dylib",
    "pumice_rust.so",
    "pumice-server",
)


# ---------------------------------------------------------------------------
# Tiny IO helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_run_id() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H%M%SZ")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"File not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON {path}: {exc}")


# ---------------------------------------------------------------------------
# Stack fingerprint (measurement-time provenance)
# ---------------------------------------------------------------------------


def capture_stack_fingerprint(repo_root: Path) -> Dict[str, Any]:
    """Return the runtime/source fingerprint for the run that starts now.

    ``runtime_ops.stack_fingerprint`` records git HEAD when it is called, so
    the runner calls it at measurement time.

    ``repo_root`` is the engine tree the freshness gate checked. In the normal
    case the runner starts in that tree, so ``repo_root`` equals the current
    directory and ``execution_source.cwd_git`` already records its HEAD. Adding
    it to ``sources`` too would make this header disagree with the fingerprint
    the documented ``runtime_ops.py fingerprint`` proof tool builds (that tool
    cannot add a source dir), which would fail the Gate 5.3a check that
    compares the activation proof to this header on ``sources``. So join
    ``repo_root`` to ``sources`` only when an explicit ``--repo-root`` points
    somewhere other than the current directory, where nothing else records it.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from runtime_ops import resolve_runtime

    extra: Tuple[Path, ...] = (
        () if repo_root.resolve() == Path.cwd().resolve() else (repo_root,)
    )
    try:
        return resolve_runtime().stack_fingerprint(extra_source_dirs=extra)
    except ValueError as exc:
        raise SystemExit(f"REFUSING TO MEASURE: stack fingerprint failed: {exc}")


@contextmanager
def _state_lock(state_path: Path):
    """Exclusive lock on ``<state.json>.lock``, the file ammo_state.py and
    reconcile_track_state.py lock, so this write cannot lose a concurrent one."""
    handle = open(str(state_path.parent / (state_path.name + ".lock")), "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


def record_baseline_stack_fingerprint(
    artifact_dir: Path, fingerprint: Dict[str, Any]
) -> Optional[str]:
    """Replace ``state.json`` ``runtime.stack_fingerprint`` with *fingerprint*.

    new_target.py writes the first copy at scaffold time. The worktree can get
    new commits before the Stage 1 baseline runs, so a successful baseline
    stores the fingerprint it measured with. The gates compare a proof to the
    state copy only on fields that stay fixed for a session (profile, packages,
    server stack, source pins), so the swap keeps those checks valid. The
    fingerprint keeps its own digest; nothing here edits a field.

    Returns a log line, or ``None`` when state.json has no ``runtime`` object.
    """
    state_path = artifact_dir / "state.json"
    if not state_path.is_file():
        return None
    with _state_lock(state_path):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"WARNING: state.json not updated with the baseline stack fingerprint: {exc}"
        runtime = state.get("runtime") if isinstance(state, dict) else None
        if not isinstance(runtime, dict):
            return None
        previous = runtime.get("stack_fingerprint")
        runtime["stack_fingerprint"] = fingerprint
        fd, tmp_name = tempfile.mkstemp(dir=str(state_path.parent), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2)
                handle.write("\n")
            os.replace(tmp_name, state_path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    old_digest = previous.get("digest") if isinstance(previous, dict) else None
    return (
        "[stack] state.json runtime.stack_fingerprint now records this baseline "
        f"({old_digest} -> {fingerprint.get('digest')})"
    )


def _read_json_or_empty(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _format_cmd_for_md(cmd: Sequence[str], env_overrides: Dict[str, str]) -> str:
    env_prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(env_overrides.items()))
    cmd_str = " ".join(shlex.quote(str(x)) for x in cmd)
    return (env_prefix + " " + cmd_str).strip()


# ---------------------------------------------------------------------------
# target.json validation
# ---------------------------------------------------------------------------


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped == PLACEHOLDER or (stripped.startswith("<") and stripped.endswith(">"))


def _require(obj: Dict[str, Any], key: str, ctx: str) -> Any:
    if key not in obj:
        raise SystemExit(f"Missing required field: {ctx}.{key}")
    val = obj[key]
    if _is_placeholder(val):
        raise SystemExit(f"Field still placeholder: {ctx}.{key}={val!r}")
    return val


def _require_int(obj: Dict[str, Any], key: str, ctx: str) -> int:
    val = _require(obj, key, ctx)
    if not isinstance(val, int) or isinstance(val, bool):
        raise SystemExit(f"Expected int for {ctx}.{key}, got {type(val).__name__}")
    return val


def _require_list_int(obj: Dict[str, Any], key: str, ctx: str) -> List[int]:
    val = _require(obj, key, ctx)
    if not isinstance(val, list) or not val or not all(
        isinstance(x, int) and not isinstance(x, bool) for x in val
    ):
        raise SystemExit(f"Expected non-empty list[int] for {ctx}.{key}, got {val!r}")
    return val


def _maybe_list_str(obj: Dict[str, Any], key: str) -> List[str]:
    val = obj.get(key, [])
    if val is None:
        return []
    if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
        raise SystemExit(f"Expected list[str] for bench.{key}, got {val!r}")
    return list(val)


def _require_env_dict(
    bench: Dict[str, Any], key: str, *, allow_placeholder: bool = False
) -> Dict[str, str]:
    """Validate ``bench.<key>`` as a ``dict[str,str]`` of env overrides.

    Placeholder keys/values (``<ENABLE_FLAG>`` etc.) are fatal by default. With
    ``allow_placeholder=True`` they are dropped with a stderr WARNING instead:
    ``new_target.py`` scaffolds ``opt_env`` as ``{"<ENABLE_FLAG>": "1"}`` and the
    real flag is only known in Stage 5, so a Stage 1 ``--labels baseline`` run
    must not be blocked by an arm it never launches.
    """
    val = bench.get(key, {}) or {}
    if not isinstance(val, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in val.items()
    ):
        raise SystemExit(f"bench.{key} must be a dict[str,str]")
    kept: Dict[str, str] = {}
    for k, v in val.items():
        if _is_placeholder(k) or _is_placeholder(v):
            if allow_placeholder:
                print(
                    f"WARNING: bench.{key} entry {k!r}={v!r} is still a scaffold "
                    "placeholder; ignored because the opt arm is not selected. "
                    "Fill it before running --labels opt.",
                    file=sys.stderr,
                )
                continue
            if _is_placeholder(k):
                raise SystemExit(
                    f"bench.{key} key is still a placeholder: {k!r}. Replace it with the "
                    "actual environment variable name (e.g. PUMICE_MY_OPT=1)."
                )
            raise SystemExit(f"bench.{key}[{k!r}] value is still a placeholder: {v!r}")
        kept[k] = v
    return kept


def _expand_workload_to_buckets(workload: Dict[str, Any]) -> List[Dict[str, int]]:
    """Normalize a workload spec into ``[{input_len, output_len, batch_size}, ...]``.

    Accepts the ``workload_matrix`` form (list of dicts, heterogeneous ISL/OSL)
    and the legacy flat ``{input_len, output_len, batch_sizes}`` form.
    """
    matrix = workload.get("workload_matrix")
    if matrix is not None:
        if not isinstance(matrix, list) or not matrix:
            raise SystemExit("workload.workload_matrix must be a non-empty list")
        required = {"input_len", "output_len", "batch_size"}
        buckets: List[Dict[str, int]] = []
        seen: set = set()
        for i, entry in enumerate(matrix):
            if not isinstance(entry, dict):
                raise SystemExit(
                    f"workload.workload_matrix[{i}] must be a dict, got {type(entry).__name__}"
                )
            missing = required - set(entry)
            if missing:
                raise SystemExit(f"workload.workload_matrix[{i}] missing keys: {sorted(missing)}")
            bucket = {
                "input_len": int(entry["input_len"]),
                "output_len": int(entry["output_len"]),
                "batch_size": int(entry["batch_size"]),
            }
            key = (bucket["input_len"], bucket["output_len"], bucket["batch_size"])
            if key in seen:
                raise SystemExit(
                    "Duplicate bucket in workload.workload_matrix: "
                    f"input_len={bucket['input_len']}, output_len={bucket['output_len']}, "
                    f"batch_size={bucket['batch_size']}"
                )
            seen.add(key)
            buckets.append(bucket)
        return buckets

    input_len = workload.get("input_len")
    output_len = workload.get("output_len")
    batch_sizes = workload.get("batch_sizes")
    if input_len is None or output_len is None or batch_sizes is None:
        raise SystemExit(
            "workload must have input_len, output_len and batch_sizes (or workload_matrix)"
        )
    return [
        {"input_len": int(input_len), "output_len": int(output_len), "batch_size": int(bs)}
        for bs in batch_sizes
    ]


#: Valid values for ``target.prefix_cache_regime``. WS4.4 pin-then-verify.
_PREFIX_CACHE_REGIMES: Tuple[str, ...] = ("on", "off")
#: Values ``target.json`` may freeze in ``bench.prompt_source``. ``random`` is
#: deliberately absent: it is a debugging mode, never evidence.
_FROZEN_PROMPT_SOURCES: Tuple[str, ...] = ("gsm8k", "raw")
DEFAULT_PROMPT_SOURCE = "gsm8k"

#: gRPC-server flag name for prefix-cache metrics port. The Rust engine's
#: PrometheusExporter binds to this port and exposes ``prefix_cache_hits_total``
#: and ``prefix_cache_queries_total`` counters (pumice-rust/src/metrics.rs).
_METRICS_PORT_FLAG = "--metrics-port"

#: Log-line prefix the server prints when a CLI regime override wins over the
#: confdb ``prefix_caching`` key. Every arm's server_log MUST contain one row.
_PREFIX_CACHE_OVERRIDE_TAG = "Prefix caching launch override:"


@dataclass(frozen=True)
class TargetSpec:
    """Validated ``target.json`` content. Produced by :func:`parse_target_spec`."""

    model_id: str
    tp: int
    ep: int
    max_model_len: int
    buckets: Tuple[Dict[str, int], ...]
    num_iters: int
    server_cmd_spec: Any
    extra_args: Tuple[str, ...]
    baseline_extra_args: Tuple[str, ...]
    opt_extra_args: Tuple[str, ...]
    baseline_env: Dict[str, str]
    opt_env: Dict[str, str]
    baseline_label: str
    opt_label: str
    fastpath_evidence: Dict[str, Any]
    baseline_require: Tuple[str, ...]
    baseline_forbid: Tuple[str, ...]
    opt_require: Tuple[str, ...]
    opt_forbid: Tuple[str, ...]
    runner_deprecated: bool
    raw_workload: Dict[str, Any]
    prefix_cache_regime: Optional[str] = None
    correctness_sample_count: Optional[int] = None
    prompt_source: Optional[str] = None


def parse_target_spec(
    spec: Dict[str, Any],
    *,
    artifact_dir: Optional[Path] = None,
    require_opt_env: bool = True,
) -> TargetSpec:
    """Validate a ``target.json`` payload into a :class:`TargetSpec`.

    Pure (no filesystem beyond the optional ``artifact_dir`` cross-check, no
    subprocesses) so the whole contract is unit-testable. Raises ``SystemExit``
    with an actionable message on any violation — the agent-facing contract is
    "fail before the expensive model load, not mid-bucket".

    ``require_opt_env=False`` (baseline-only invocations) tolerates the
    ``new_target.py`` scaffold placeholder in ``bench.opt_env``: the entry is
    dropped with a warning instead of aborting a run that never uses it.
    """
    if artifact_dir is not None:
        declared = spec.get("artifact_dir")
        if isinstance(declared, str) and declared:
            if Path(declared).expanduser().resolve() != Path(artifact_dir).resolve():
                raise SystemExit(
                    f"target.json artifact_dir ({declared}) does not match "
                    f"--artifact-dir ({artifact_dir}). Fix target.json or pass a "
                    "matching --artifact-dir."
                )

    target = _require(spec, "target", "root")
    if not isinstance(target, dict):
        raise SystemExit("root.target must be an object")
    model_id = _require(target, "model_id", "target")
    tp = _require_int(target, "tp", "target")
    ep = _require_int(target, "ep", "target")
    max_model_len = _require_int(target, "max_model_len", "target")
    regime_raw = target.get("prefix_cache_regime")
    if regime_raw is None:
        prefix_cache_regime: Optional[str] = None
    else:
        if not isinstance(regime_raw, str) or regime_raw not in _PREFIX_CACHE_REGIMES:
            raise SystemExit(
                "target.prefix_cache_regime must be one of "
                f"{_PREFIX_CACHE_REGIMES!r} when set, got {regime_raw!r}"
            )
        prefix_cache_regime = regime_raw
    corr_raw = target.get("correctness_sample_count")
    if corr_raw is None:
        correctness_sample_count: Optional[int] = None
    else:
        if not isinstance(corr_raw, int) or isinstance(corr_raw, bool) or corr_raw < 0:
            raise SystemExit(
                "target.correctness_sample_count must be a non-negative int, "
                f"got {corr_raw!r}"
            )
        correctness_sample_count = corr_raw

    workload = _require(spec, "workload", "root")
    if not isinstance(workload, dict):
        raise SystemExit("root.workload must be an object")
    if "workload_matrix" not in workload:
        _require_int(workload, "input_len", "workload")
        _require_int(workload, "output_len", "workload")
        _require_list_int(workload, "batch_sizes", "workload")
    buckets = _expand_workload_to_buckets(workload)
    num_iters = _require_int(workload, "num_iters", "workload")
    if num_iters < 1:
        raise SystemExit(f"workload.num_iters must be >= 1, got {num_iters}")
    for bucket in buckets:
        bucket["num_iters"] = num_iters
    _validate_buckets_model_len(buckets, max_model_len)

    bench = _require(spec, "bench", "root")
    if not isinstance(bench, dict):
        raise SystemExit("root.bench must be an object")
    runner = _require(bench, "runner", "bench")
    deprecated = runner == LEGACY_RUNNER_NAME
    if not deprecated and runner != RUNNER_NAME:
        raise SystemExit(f"Unsupported bench.runner: {runner!r} (expected {RUNNER_NAME!r})")

    fpe = bench.get("fastpath_evidence") or {}
    if not isinstance(fpe, dict):
        fpe = {}

    def _evidence(label: str) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        cfg = fpe.get(label) or {}
        if not isinstance(cfg, dict):
            return ((), ())
        req = cfg.get("require_patterns") or []
        forb = cfg.get("forbid_patterns") or []
        for name, value in (("require_patterns", req), ("forbid_patterns", forb)):
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise SystemExit(f"bench.fastpath_evidence.{label}.{name} must be list[str]")
        return (tuple(req), tuple(forb))

    baseline_require, baseline_forbid = _evidence("baseline")
    opt_require, opt_forbid = _evidence("opt")

    prompt_raw = bench.get("prompt_source")
    if prompt_raw is None:
        prompt_source: Optional[str] = None
    else:
        if not isinstance(prompt_raw, str) or prompt_raw not in _FROZEN_PROMPT_SOURCES:
            raise SystemExit(
                "bench.prompt_source must be one of "
                f"{_FROZEN_PROMPT_SOURCES!r} when set, got {prompt_raw!r}"
            )
        prompt_source = prompt_raw

    return TargetSpec(
        model_id=model_id,
        tp=tp,
        ep=ep,
        max_model_len=max_model_len,
        buckets=tuple(buckets),
        num_iters=num_iters,
        # `vllm_cmd` is accepted as a deprecated alias so pre-migration
        # target.json files still load (they name the vLLM CLI, which we ignore
        # in favour of the Pumice default).
        server_cmd_spec=(bench.get("server_cmd") or "pumice-server"),
        extra_args=tuple(_maybe_list_str(bench, "extra_args")),
        baseline_extra_args=tuple(_maybe_list_str(bench, "baseline_extra_args")),
        opt_extra_args=tuple(_maybe_list_str(bench, "opt_extra_args")),
        baseline_env=_require_env_dict(bench, "baseline_env"),
        opt_env=_require_env_dict(bench, "opt_env", allow_placeholder=not require_opt_env),
        baseline_label=str(bench.get("baseline_label", "baseline")),
        opt_label=str(bench.get("opt_label", "opt")),
        fastpath_evidence=fpe,
        baseline_require=baseline_require,
        baseline_forbid=baseline_forbid,
        opt_require=opt_require,
        opt_forbid=opt_forbid,
        runner_deprecated=deprecated,
        raw_workload=workload,
        prefix_cache_regime=prefix_cache_regime,
        correctness_sample_count=correctness_sample_count,
        prompt_source=prompt_source,
    )


def _bucket_file_tag(bucket: Dict[str, int], all_buckets: Sequence[Dict[str, int]]) -> str:
    """``bs{BS}`` for homogeneous sweeps, ``il{IL}_ol{OL}_bs{BS}`` otherwise."""
    il_ol = {(b["input_len"], b["output_len"]) for b in all_buckets}
    if len(il_ol) <= 1:
        return f"bs{bucket['batch_size']}"
    return f"il{bucket['input_len']}_ol{bucket['output_len']}_bs{bucket['batch_size']}"


def _validate_buckets_model_len(buckets: Sequence[Dict[str, int]], max_model_len: int) -> None:
    for b in buckets:
        total = b["input_len"] + b["output_len"]
        if total > max_model_len:
            raise SystemExit(
                f"Bucket (input_len={b['input_len']}, output_len={b['output_len']}, "
                f"batch_size={b['batch_size']}) requires {total} tokens but "
                f"max_model_len={max_model_len}"
            )


# ---------------------------------------------------------------------------
# Artifact layout v2: round/slot path resolution
# ---------------------------------------------------------------------------


def _is_v2_layout(artifact_dir: Path) -> bool:
    return (artifact_dir / "rounds").is_dir()


def _read_current_round_from_state(artifact_dir: Path) -> int:
    try:
        state = json.loads((artifact_dir / "state.json").read_text(encoding="utf-8"))
        return int(state["campaign"]["current_round"])
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError):
        return 1


def _resolve_sweep_out_name(args: Any, artifact_dir: Path) -> str:
    """Resolve the sweep output dir (relative to ``artifact_dir``).

    ``--round N --slot SLOT`` -> ``rounds/{N}/sweeps/{SLOT}``. ``--slot`` alone
    reads ``campaign.current_round`` from state.json. A custom ``--out-name`` on
    a v2 layout is a hard error (it would hide evidence outside the round tree).
    """
    round_ = getattr(args, "round", None)
    slot = getattr(args, "slot", None)

    if round_ is not None and slot is not None:
        return f"rounds/{round_}/sweeps/{slot}"
    if round_ is not None and slot is None:
        raise SystemExit(
            "ERROR: --round requires --slot. Pass --slot baseline | profiling | "
            "opt/{op_id} | opt_correctness/{op_id} | opt_profiling/{op_id} | "
            "integration | integration_profiling | golden_capture "
            "(see references/artifact-layout.md)."
        )
    if slot is not None:
        if not _is_v2_layout(artifact_dir):
            raise SystemExit(
                f"ERROR: --slot was passed but {artifact_dir}/rounds/ does not exist. "
                "Either run new_target.py to scaffold v2, or omit --slot for legacy mode."
            )
        return f"rounds/{_read_current_round_from_state(artifact_dir)}/sweeps/{slot}"

    out_name = getattr(args, "out_name", "e2e_latency")
    if _is_v2_layout(artifact_dir) and out_name != "e2e_latency":
        raise SystemExit(
            "ERROR: --out-name is legacy-only. This artifact dir uses the v2 "
            "rounds/ layout; pass --round/--slot instead "
            "(see references/artifact-layout.md)."
        )
    return out_name


def _is_gate_slot(slot: Optional[str]) -> bool:
    """Return whether a V2 slot contributes binding Stage 5/6 evidence."""
    return bool(slot) and (
        slot == "integration"
        or slot == "integration_profiling"
        or slot.startswith("opt/")
        or slot.startswith("opt_correctness/")
        or slot.startswith("opt_profiling/")
    )


def enforce_gate_slot_invocation(args: argparse.Namespace) -> List[str]:
    """Reject / warn on gate-slot misinvocations. Returns warning strings.

    Stage 5 sibling slots and integration feed SHIP verdicts, so the runner
    enforces gate-quality invocation instead of trusting the caller's flags:

    1. A clean timing gate compares the optimized arm against the accepted
       Stage 1 baseline. Re-running a baseline arm from the worktree risks
       executing the optimized path through the editable install, contaminating
       both arms — so ``--labels opt`` without ``--baseline-from`` warns.
    2. ``--num-launches >= 2`` on a gate slot is REJECTED, not warned: gate
       significance comes from the within-launch Welch test on per-iteration
       latencies (the row's ``significance`` field), which a single launch
       already provides. Extra launches relaunch the server per launch and add
       wall-clock without changing the verdict. This is the known agent
       misinvocation pattern.
    """
    slot = getattr(args, "slot", None)
    is_profiling_run = bool(getattr(args, "nsys_profile", False))
    # Baseline slots must never be profiled: nsys overhead inside the
    # baseline arm silently inflates the reference every later gate compares
    # against. Hard error (matches the retired vLLM runner's guard and the
    # Codex-side _profiles_official_sweep hook).
    if slot and (slot == "baseline" or slot.startswith("baseline/")):
        if is_profiling_run or getattr(args, "nsys_extra_flags", ""):
            raise SystemExit(
                f"ERROR: --slot {slot} cannot be combined with profiling flags "
                "(--nsys-profile/--nsys-extra-flags). The Stage 1 baseline is "
                "the reference arm for every later gate; profiler overhead in "
                "it would bias all downstream comparisons. Profile via an "
                "opt_profiling/ or integration_profiling slot instead."
            )
    if not _is_gate_slot(slot):
        return []
    warnings: List[str] = []
    is_timing_gate = bool(
        (slot == "integration" or slot.startswith("opt/"))
        and not is_profiling_run
        and not getattr(args, "verify_correctness", False)
    )
    if (
        is_timing_gate
        and getattr(args, "selected_labels", []) == ["opt"]
        and not getattr(args, "baseline_from", None)
    ):
        warnings.append(
            f"clean timing --slot {slot} with --labels opt has no --baseline-from; "
            "the Gate 5.3b comparison requires the Stage 1 baseline reference."
        )
    if int(getattr(args, "num_launches", 1)) >= 2:
        raise SystemExit(
            f"ERROR: --num-launches {args.num_launches} is not allowed with "
            f"--slot {slot}. Gate sweeps use a single launch; statistical "
            "significance is computed within-launch from per-iteration latencies "
            "(row 'significance' field). Multi-launch would relaunch the server "
            "per launch and add wall-clock without changing the verdict."
        )
    return warnings


def _pick_archive_path(path: Path, run_id: str) -> Path:
    base = path.with_name(f"{path.name}_{run_id}")
    if not base.exists():
        return base
    for i in range(1, 1000):
        cand = path.with_name(f"{path.name}_{run_id}:{i}")
        if not cand.exists():
            return cand
    raise SystemExit(f"Failed to pick unique archive name for {path}")


def _pick_v2_archive_path(artifact_dir: Path, out_name: str, run_id: str) -> Optional[Path]:
    parts = Path(out_name).parts
    if len(parts) >= 4 and parts[0] == "rounds" and parts[2] == "sweeps":
        archive_dir = artifact_dir / "rounds" / parts[1] / "_archive"
        slot_name = "_".join(parts[3:]) or "unknown"
        base = archive_dir / f"{slot_name}_{run_id}"
        if not base.exists():
            return base
        for i in range(1, 1000):
            cand = archive_dir / f"{slot_name}_{run_id}:{i}"
            if not cand.exists():
                return cand
        raise SystemExit(f"Failed to pick unique v2 archive name under {archive_dir}")
    return None


def _prepare_out_root(*, artifact_dir: Path, out_name: str, overwrite: bool) -> Path:
    out_root = artifact_dir / out_name
    if out_root.exists() and any(out_root.iterdir()):
        if overwrite:
            shutil.rmtree(out_root, ignore_errors=True)
        else:
            run_id = _utc_run_id()
            archived = _pick_v2_archive_path(artifact_dir, out_name, run_id) or _pick_archive_path(
                out_root, run_id
            )
            print(f"Archiving existing output dir: {out_root} -> {archived}")
            archived.parent.mkdir(parents=True, exist_ok=True)
            out_root.replace(archived)
    out_root.mkdir(parents=True, exist_ok=True)
    return out_root


# ---------------------------------------------------------------------------
# Environment hygiene
# ---------------------------------------------------------------------------

_ENGINE_ENV_PREFIX_RE = re.compile(r"^(PUMICE|VLLM)_")

# Platform and build identifiers that the AMMO images set as ENV. They say
# WHICH engine build runs, not HOW it is tuned, so the sanitizer always keeps
# them. They are reserved: never use them as A/B flags.
#   PUMICE_PLATFORM=neuron (Dockerfile.pumice-neuron): Pumice's config.py and
#     pumice-rust/src/config.rs read it at startup to select Neuron. Without it
#     Pumice probes torch_neuronx, then falls back to the vLLM runner, which the
#     Neuron image does not have, and the server panics (found live on trn2-1).
#   PUMICE_VLLM_RUNNER_ONLY=1 (Dockerfile.pumice-cuda): Pumice's setup.py reads
#     it to skip the Pumice-owned CUDA extensions. The server does not read it
#     at runtime; keeping it makes the server env match the image, so any
#     reinstall from that env builds the same runner-only package.
# Left out on purpose: PUMICE_USE_VLLM_RUNNER, PUMICE_ORCHESTRATOR and
# PUMICE_NEURON_CONFIG select behavior an A/B could test; the path and port
# names (PUMICE_NCCL_SO_PATH, PUMICE_FLASH_ATTN_SRC_DIR, PUMICE_NCCL_PORT,
# PUMICE_VLLM_DIST_PORT) are not set by any image, so an arm that needs one
# declares it in baseline_env; PUMICE_CACHE_ROOT is set by
# _inject_fresh_cache_env.
ENGINE_INFRA_ENV: "frozenset[str]" = frozenset({"PUMICE_PLATFORM", "PUMICE_VLLM_RUNNER_ONLY"})


def _sanitize_engine_op_env(
    env: Dict[str, str],
    preserve_keys: "frozenset[str] | set[str]" = frozenset(),
) -> Dict[str, str]:
    """Strip stale engine flag env vars to prevent cross-track contamination.

    Pumice's no-rebuild A/B mechanism is the ``PUMICE_*`` env surface (the
    analogue of the vLLM-era ``VLLM_*`` flags). After a round ships, its
    ``opt_env`` keys are promoted into ``target.json:bench.baseline_env``, so
    the live set of "optimization flags" includes campaign-defined names that
    match no numeric pattern. The sanitizer therefore drops EVERY var matching
    ``^PUMICE_`` or ``^VLLM_`` (the latter for hygiene — a vLLM-era shell or a
    leftover wrapper can still export them) unless the name is in
    *preserve_keys* or in :data:`ENGINE_INFRA_ENV`.

    :data:`ENGINE_INFRA_ENV` names (``PUMICE_PLATFORM``,
    ``PUMICE_VLLM_RUNNER_ONLY``) are image-level platform and build identifiers,
    not optimization flags, so they always survive, even with an empty
    *preserve_keys*. Stripping ``PUMICE_PLATFORM=neuron`` made the Neuron server
    panic at init.

    *preserve_keys* is normally ``baseline_env.keys() | opt_env.keys()`` for the
    arm being measured. Everything else was inherited from the shell or a prior
    process and would silently contaminate the measurement.

    Non-engine vars (``CUDA_VISIBLE_DEVICES``, ``HF_HOME``, ``PATH``, ...) pass
    through untouched. Does not mutate *env*.
    """
    preserve = set(preserve_keys) | ENGINE_INFRA_ENV
    return {
        k: v
        for k, v in env.items()
        if not _ENGINE_ENV_PREFIX_RE.match(k) or k in preserve
    }


def _inject_fresh_cache_env(env: Dict[str, str], sweep_cache_root: Path) -> Dict[str, str]:
    """Point Pumice/Triton/Inductor compile caches at a per-sweep directory.

    Must run AFTER ``_sanitize_engine_op_env`` — ``PUMICE_CACHE_ROOT`` matches
    the sanitizer's own prefix and would otherwise be stripped right back out.

    Do NOT set ``PUMICE_DISABLE_COMPILE_CACHE=1``: saving must still work within
    the run so launches 2..N amortize compile.
    """
    env = dict(env)
    env["PUMICE_CACHE_ROOT"] = str(sweep_cache_root)
    env["TRITON_CACHE_DIR"] = str(sweep_cache_root / "triton_cache")
    env["TORCHINDUCTOR_CACHE_DIR"] = str(sweep_cache_root / "inductor_cache")
    return env


def _ensure_worktree_pythonpath(env: Dict[str, str], cwd: Optional[str] = None) -> Dict[str, str]:
    """Prepend the worktree's Python package root to ``PYTHONPATH``.

    Pumice uses a src layout (``src/pumice/``), so the entry that makes
    ``import pumice`` resolve to THIS worktree rather than the editable install
    in the main repo is ``{cwd}/src`` when it exists, else ``{cwd}``.

    NOTE: this only covers the Python GPU layer. A change under
    ``pumice-rust/src/`` or ``pumice-rust/confdb/`` needs
    ``cargo build --release``; ``PYTHONPATH`` cannot shortcut it. That is what
    :func:`check_engine_freshness` exists to enforce.
    """
    env = dict(env)
    root = Path(cwd or os.getcwd())
    entry = str(root / "src") if (root / "src").is_dir() else str(root)
    existing = env.get("PYTHONPATH", "")
    if existing:
        parts = existing.split(os.pathsep)
        if parts[0] != entry:
            env["PYTHONPATH"] = entry + os.pathsep + existing
    else:
        env["PYTHONPATH"] = entry
    return env


# ---------------------------------------------------------------------------
# Stale-binary freshness gate (decision D3 / D5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessReport:
    """Outcome of the stale-engine check. Serializable into the output JSON."""

    ok: bool
    reason: str
    detail: str
    newest_source: Optional[str] = None
    newest_source_mtime: Optional[float] = None
    oldest_artifact: Optional[str] = None
    oldest_artifact_mtime: Optional[float] = None
    sources_scanned: int = 0
    artifacts: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "detail": self.detail,
            "newest_source": self.newest_source,
            "newest_source_mtime": self.newest_source_mtime,
            "oldest_artifact": self.oldest_artifact,
            "oldest_artifact_mtime": self.oldest_artifact_mtime,
            "sources_scanned": self.sources_scanned,
            "artifacts": list(self.artifacts),
        }


def _cargo_home_config_path() -> Optional[Path]:
    """Return the ``$CARGO_HOME/config.toml`` path, if the env var is set.

    Editing this file (the [patch.crates-io] row for ``amzn-mantle-cmb-api``
    lives here — WS2.2 / WS2.4) shifts the resolved dependency graph, so a
    stale build after that edit is exactly the same trap as a stale build
    after a ``pumice-rust/src/*.rs`` edit. The gate reads whichever config
    file cargo would read at build time: ``$CARGO_HOME/config.toml`` when the
    var is set. When ``CARGO_HOME`` is unset the gate does not scan
    ``~/.cargo/config.toml`` because a build outside the session images
    might not use it, and a false-positive stale here would refuse valid
    runs.
    """
    home = os.environ.get("CARGO_HOME")
    if not home:
        return None
    return Path(home) / "config.toml"


def newest_engine_source(repo_root: Path) -> Tuple[Optional[Path], Optional[float], int]:
    """Return ``(path, mtime, count)`` of the newest Rust/confdb input.

    Scans ``pumice-rust/src/**/*.rs``, ``pumice-rust/confdb/*.toml``,
    ``pumice-rust/build.rs``, both ``Cargo.toml`` files (repo root and
    ``pumice-rust/``) and — when ``CARGO_HOME`` is set — the cargo config
    at ``$CARGO_HOME/config.toml``. confdb TOMLs are embedded at compile
    time by ``build.rs``, so editing one without rebuilding is exactly as
    stale as editing a ``.rs`` file. The cargo config is what carries the
    ``[patch.crates-io]`` row for ``amzn-mantle-cmb-api`` (WS2.2 / WS2.4)
    and re-runs the resolver on the next build.
    """
    newest_path: Optional[Path] = None
    newest_mtime: Optional[float] = None
    count = 0
    for pattern in _ENGINE_SOURCE_GLOBS:
        for path in sorted(repo_root.glob(pattern)):
            if not path.is_file():
                continue
            count += 1
            mtime = path.stat().st_mtime
            if newest_mtime is None or mtime > newest_mtime:
                newest_mtime, newest_path = mtime, path
    cargo_cfg = _cargo_home_config_path()
    if cargo_cfg is not None and cargo_cfg.is_file():
        count += 1
        mtime = cargo_cfg.stat().st_mtime
        if newest_mtime is None or mtime > newest_mtime:
            newest_mtime, newest_path = mtime, cargo_cfg
    return newest_path, newest_mtime, count


def discover_staged_extensions(repo_root: Path) -> List[Path]:
    """Find the STAGED ``pumice_rust`` extension(s) in session venvs.

    ``pumice-stage-native.sh`` (run by the session bootstrap and after every
    Rust or confdb edit) copies ``libpumice_rust.so`` out of the cargo target
    dir into the worktree venv's site-packages under its importable
    ``pumice_rust<EXT_SUFFIX>`` name. THAT copy — not anything under ``target/release`` — is what
    ``import pumice_rust`` actually loads when the engine serves, so the
    freshness gate must treat it as the artifact under test.
    """
    staged: List[Path] = []
    for pattern in (
        ".venv/lib/python*/site-packages/pumice_rust*.so",
        ".venv/lib/python*/site-packages/pumice_rust*.dylib",
    ):
        staged.extend(p for p in sorted(repo_root.glob(pattern)) if p.is_file())
    return staged


def discover_engine_artifacts(
    repo_root: Path, *, cargo_target_dir: Optional[Path] = None
) -> List[Path]:
    """Find built Pumice engine artifacts (PyO3 extension + server binary).

    Looks in ``cargo_target_dir/release`` (explicit arg, else the
    ``$CARGO_TARGET_DIR`` env var — the D3 session bootstrap builds OUTSIDE
    the worktree, e.g. ``/workspace/cargo-target/<session_id>``), then
    ``pumice-rust/target/release`` and ``target/release`` in-worktree
    fallbacks, plus a top-level ``pumice_rust*.so`` dropped by an editable
    install. Returns existing files only, de-duplicated, in discovery order.
    """
    if cargo_target_dir is None:
        env_ctd = os.environ.get("CARGO_TARGET_DIR")
        if env_ctd:
            cargo_target_dir = Path(env_ctd)

    search_dirs: List[Path] = []
    if cargo_target_dir is not None:
        search_dirs.append(Path(cargo_target_dir) / "release")
    search_dirs.append(repo_root / "pumice-rust" / "target" / "release")
    search_dirs.append(repo_root / "target" / "release")

    found: List[Path] = []
    seen: set = set()

    def _add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not path.is_file():
            return
        seen.add(resolved)
        found.append(path)

    for directory in search_dirs:
        for name in _ENGINE_ARTIFACT_NAMES:
            _add(directory / name)
    for path in sorted(repo_root.glob("pumice_rust*.so")):
        _add(path)
    return found


def _rebuild_hint(repo_root: Path) -> str:
    """The rebuild-and-stage command a freshness failure tells the agent to run.

    ``pumice-stage-native.sh`` (installed to ``/usr/local/bin`` in the session
    images) runs ``cargo build --release`` and then copies the new
    ``libpumice_rust.so`` into the venv site-packages. A plain ``cargo build``
    does not stage, so the server would keep loading the old extension.
    """
    return (
        f"  CUDA:   pumice-stage-native.sh {repo_root}/.venv/bin/python {repo_root} "
        '--features "nixl-stub cmb"\n'
        f"  Neuron: pumice-stage-native.sh {repo_root}/.venv/bin/python {repo_root} "
        "--features nixl-stub\n"
        "The CUDA features keep the P/D path compiled in, as in the image build. "
        "The script runs cargo build --release "
        "and then copies libpumice_rust.so into the venv site-packages as "
        "pumice_rust<EXT_SUFFIX>. Outside the session image, first add the "
        "[patch.crates-io] entry for amzn-mantle-cmb-api to "
        "$CARGO_HOME/config.toml (see references/impl-track-rules.md)."
    )


def check_engine_freshness(
    repo_root: Path,
    *,
    cargo_target_dir: Optional[Path] = None,
    extra_artifacts: Sequence[Path] = (),
) -> FreshnessReport:
    """Refuse to measure a Pumice build older than its Rust/confdb sources.

    Returns a :class:`FreshnessReport`; the caller decides whether to abort. The
    check is conservative on purpose:

    * no ``pumice-rust/src`` at all -> ``missing_sources`` (NOT ok): we cannot
      prove the engine under test matches the tree, so we refuse rather than
      silently measure something unknown.
    * no built or staged artifact found -> ``missing_artifact`` (NOT ok).
    * the artifact that will actually SERVE predates the newest source ->
      ``stale_artifact`` (NOT ok). The serving artifact is the staged
      ``pumice_rust<EXT_SUFFIX>`` in the session venv's site-packages when one
      exists (that is what ``import pumice_rust`` loads); otherwise the newest
      built library. Comparing the serving artifact — not the oldest file
      found anywhere — means a hook-copied historical ``.so`` lying around the
      worktree cannot permanently fail the gate.
    * a built library in the cargo target dir is NEWER than the staged
      ``.so`` -> ``stale_staging`` (NOT ok): someone rebuilt but did not
      re-stage, so the server would import the OLD code while the tree and
      target dir look new.
    """
    repo_root = Path(repo_root)
    if not (repo_root / "pumice-rust" / "src").is_dir():
        return FreshnessReport(
            ok=False,
            reason="missing_sources",
            detail=(
                f"{repo_root}/pumice-rust/src does not exist, so engine freshness cannot "
                "be proven. Run the sweep from the Pumice worktree root, pass "
                "--repo-root <pumice checkout>, or --allow-stale-engine to override."
            ),
        )

    newest_path, newest_mtime, count = newest_engine_source(repo_root)
    built = list(discover_engine_artifacts(repo_root, cargo_target_dir=cargo_target_dir))
    built += [Path(p) for p in extra_artifacts if Path(p).is_file()]
    staged = discover_staged_extensions(repo_root)
    artifacts = staged + [p for p in built if p not in staged]
    artifact_names = tuple(str(p) for p in artifacts)

    if not artifacts:
        env_ctd = os.environ.get("CARGO_TARGET_DIR")
        searched = [
            f"{repo_root}/.venv/lib/python*/site-packages (staged pumice_rust extension)",
            f"{repo_root}/pumice-rust/target/release",
            f"{repo_root}/target/release",
        ]
        if cargo_target_dir is not None:
            searched.insert(1, f"{cargo_target_dir}/release")
        elif env_ctd:
            searched.insert(1, f"{env_ctd}/release ($CARGO_TARGET_DIR)")
        return FreshnessReport(
            ok=False,
            reason="missing_artifact",
            detail=(
                "No built or staged Pumice engine artifact found (looked for "
                f"{', '.join(_ENGINE_ARTIFACT_NAMES)} under: "
                + "; ".join(searched)
                + "). Build and stage first:\n"
                + _rebuild_hint(repo_root)
            ),
            newest_source=str(newest_path) if newest_path else None,
            newest_source_mtime=newest_mtime,
            sources_scanned=count,
        )

    # The artifact under test: the staged extension when present (that file is
    # what `import pumice_rust` loads). With no staged copy the bench launches
    # straight from a release dir (resolve_server_exe prefers
    # target/release/pumice-server), so the OLDEST release-dir artifact is the
    # conservative gate — a rebuilt .so must not mask a stale pumice-server
    # binary. Repo-root editable .so leftovers only count when they are the
    # only artifact, so a hook-copied historical copy cannot permanently fail
    # the gate.
    release_built = [p for p in built if p.parent.name == "release"]
    if staged:
        serving_path = max(staged, key=lambda p: p.stat().st_mtime)
    elif release_built:
        serving_path = min(release_built, key=lambda p: p.stat().st_mtime)
    else:
        serving_path = max(built, key=lambda p: p.stat().st_mtime)
    serving_mtime = serving_path.stat().st_mtime

    # Rebuilt-but-not-restaged: a built lib newer than the staged copy means
    # the server would import old code while the target dir looks fresh.
    if staged and built:
        newest_built = max(built, key=lambda p: p.stat().st_mtime)
        built_mtime = newest_built.stat().st_mtime
        if built_mtime > serving_mtime + 1.0:  # 1s slop for copy timestamps
            return FreshnessReport(
                ok=False,
                reason="stale_staging",
                detail=(
                    f"Built library {newest_built} is "
                    f"{built_mtime - serving_mtime:.0f}s newer than the staged "
                    f"extension {serving_path}. The server imports the STAGED "
                    "copy, so measuring now would attribute the old build's "
                    "numbers to the new code. Re-stage:\n"
                    f"  cp {newest_built} {serving_path}"
                ),
                newest_source=str(newest_path) if newest_path else None,
                newest_source_mtime=newest_mtime,
                oldest_artifact=str(serving_path),
                oldest_artifact_mtime=serving_mtime,
                sources_scanned=count,
                artifacts=artifact_names,
            )

    if newest_mtime is not None and serving_mtime < newest_mtime:
        lag = newest_mtime - serving_mtime
        return FreshnessReport(
            ok=False,
            reason="stale_artifact",
            detail=(
                f"Engine artifact {serving_path} is {lag:.0f}s older than "
                f"{newest_path}. Pumice's scheduler / block manager / spec-decode "
                "orchestration and confdb backend selection are compiled in — a "
                "source or confdb edit does NOT take effect until you rebuild, so "
                "measuring now would attribute the OLD binary's numbers to the NEW "
                "code. Rebuild and re-stage:\n"
                + _rebuild_hint(repo_root)
            ),
            newest_source=str(newest_path),
            newest_source_mtime=newest_mtime,
            oldest_artifact=str(serving_path),
            oldest_artifact_mtime=serving_mtime,
            sources_scanned=count,
            artifacts=artifact_names,
        )

    return FreshnessReport(
        ok=True,
        reason="fresh",
        detail=(
            f"serving artifact {serving_path.name} newer than {count} tracked "
            f"source(s); {len(artifacts)} artifact(s) discovered"
        ),
        newest_source=str(newest_path) if newest_path else None,
        newest_source_mtime=newest_mtime,
        oldest_artifact=str(serving_path),
        oldest_artifact_mtime=serving_mtime,
        sources_scanned=count,
        artifacts=artifact_names,
    )


# ---------------------------------------------------------------------------
# Server launch
# ---------------------------------------------------------------------------


def _bench_exe_tokens(spec: Any) -> List[str]:
    """Normalize a server-command spec into an argv prefix."""
    if isinstance(spec, list) and all(isinstance(x, str) for x in spec):
        tokens = list(spec)
    elif isinstance(spec, str):
        tokens = shlex.split(spec)
    else:
        raise SystemExit(f"bench.server_cmd must be a string or list[str], got {spec!r}")
    if not tokens:
        raise SystemExit("bench.server_cmd is empty")
    return tokens


def resolve_server_exe(spec: Any, repo_root: Path) -> List[str]:
    """Resolve the Pumice server launcher argv prefix.

    The default spec ``pumice-server`` resolves to the worktree's own engine
    first: the release binary built in the worktree, then ``python
    rust_grpc_server.py`` under this interpreter (the session venv, whose
    ``pumice_rust`` is the worktree's cdylib; decision D3 / Option A). A
    ``pumice-server`` found on PATH is the LAST resort: in the session image
    that is ``/opt/pumice-venv/bin/pumice-server``, the base engine's binary,
    which builds its own ``sys.path`` from its location and would run the
    unmodified engine on both arms of every A/B. An operator who wants the
    binary sets ``bench.server_cmd`` to its path explicitly.
    """
    tokens = _bench_exe_tokens(spec)
    if tokens[0] != "pumice-server":
        if tokens[0] in ("python", "python3") and len(tokens) == 1:
            tokens = [sys.executable]
        return tokens
    local_bin = repo_root / "target" / "release" / "pumice-server"
    if local_bin.is_file():
        return [str(local_bin)] + tokens[1:]
    launcher = repo_root / "rust_grpc_server.py"
    if launcher.is_file():
        return [sys.executable, str(launcher)] + tokens[1:]
    if shutil.which("pumice-server"):
        return tokens
    raise SystemExit(
        f"Cannot find a Pumice server launcher: {local_bin} does not exist, "
        f"{launcher} does not exist, and 'pumice-server' is not on PATH. Build "
        "the binary or set bench.server_cmd in target.json."
    )


def _has_flag(args: Sequence[str], flag: str) -> bool:
    return any(a == flag or a.startswith(flag + "=") for a in args)


def _flag_value(args: Sequence[str], flag: str) -> Optional[str]:
    """Value of ``--flag value`` or ``--flag=value`` in *args*, else None."""
    for i, arg in enumerate(args):
        if arg == flag:
            return args[i + 1] if i + 1 < len(args) else None
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def _replace_flag_value(args: Sequence[str], flag: str, value: str) -> List[str]:
    """Return *args* with ``flag``'s value replaced (or appended if absent)."""
    out = list(args)
    for i, arg in enumerate(out):
        if arg == flag and i + 1 < len(out):
            out[i + 1] = value
            return out
        if arg.startswith(flag + "="):
            out[i] = f"{flag}={value}"
            return out
    return out + [flag, value]


def build_server_cmd(
    *,
    server_exe: Sequence[str],
    model_id: str,
    port: int,
    max_model_len: int,
    max_num_seqs: int,
    tensor_parallel_size: int,
    metrics_port: Optional[int] = None,
    prefix_cache_regime: Optional[str] = None,
    extra_args: Sequence[str] = (),
    model_runner: Optional[str] = None,
) -> List[str]:
    """Compose the ``pumice-server`` / ``rust_grpc_server.py`` argv.

    ``model_runner`` adds ``--model-runner <kind>`` unless *extra_args* already
    names one (see :func:`default_model_runner`).

    Flags already present in *extra_args* are NOT re-emitted, so an operator can
    override any default from ``target.json:bench.extra_args``.

    ``metrics_port`` opens the Prometheus counters
    (``prefix_cache_hits_total`` / ``prefix_cache_queries_total``) that WS4.4's
    per-arm verify step scrapes; pass ``None`` to disable. ``prefix_cache_regime``
    forces one of ``--enable-prefix-caching`` / ``--no-enable-prefix-caching``
    onto BOTH arms so the CLI flag beats the confdb ``prefix_caching`` key.
    """
    cmd = list(server_exe)
    defaults: List[Tuple[str, str]] = [
        ("--model", str(model_id)),
        ("--port", str(port)),
        ("--max-model-len", str(max_model_len)),
        ("--tensor-parallel-size", str(tensor_parallel_size)),
        ("--max-num-seqs", str(max_num_seqs)),
    ]
    if metrics_port is not None:
        defaults.append((_METRICS_PORT_FLAG, str(int(metrics_port))))
    if model_runner:
        defaults.append(("--model-runner", str(model_runner)))
    for flag, value in defaults:
        if not _has_flag(extra_args, flag):
            cmd += [flag, value]
    if prefix_cache_regime is not None:
        pin_flag = (
            "--enable-prefix-caching"
            if prefix_cache_regime == "on"
            else "--no-enable-prefix-caching"
        )
        # The pin ALWAYS wins over anything in ``extra_args`` — that is the
        # WS4.4 contract ("the flag beats confdb"). Strip both spellings from
        # ``extra_args`` first so a target.json that also names one does not
        # silently emit two conflicting flags, then append the pinned form.
        cleaned: List[str] = []
        skip_next = False
        for tok in extra_args:
            if skip_next:
                skip_next = False
                continue
            if tok in ("--enable-prefix-caching", "--no-enable-prefix-caching"):
                continue
            if tok in ("--prefix-caching",):
                skip_next = True
                continue
            cleaned.append(tok)
        cmd += cleaned
        cmd.append(pin_flag)
    else:
        cmd += list(extra_args)
    return cmd


def default_model_runner(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the runner kind to pin on this accelerator, or None.

    On Neuron, Pumice's confdb has no Neuron section: ``apply_model_config``
    defaults the compute capability to (9, 0) when CUDA is absent, so a model
    whose ``[sm90]`` row sets ``model_runner = "vllm"`` (gpt_oss at 9d126713)
    selects the vLLM runner, and the Neuron image has no vLLM. The server then
    panics at init with "--model-runner=vllm requires vLLM in this environment"
    (found live on trn2-1). The explicit flag beats both
    ``PUMICE_USE_VLLM_RUNNER`` and confdb, so pin the built-in runner there.
    """
    env = os.environ if env is None else env
    kind = (env.get("AMMO_ACCELERATOR_KIND") or "").strip().lower()
    platform = (env.get("PUMICE_PLATFORM") or "").strip().lower()
    if kind == "neuron" or platform == "neuron":
        return "pumice"
    return None


def build_nsys_cmd(
    server_cmd: Sequence[str],
    *,
    report_path: Path,
    trace: str = "cuda",
    cuda_graph_trace: str = "node",
    extra_flags: Sequence[str] = (),
) -> List[str]:
    """Wrap *server_cmd* in ``nsys profile`` armed on the cudaProfilerApi range.

    ``--capture-range=cudaProfilerApi`` + ``--capture-range-end=stop`` means nsys
    records only between the ``StartProfile`` and ``StopProfile`` RPCs (which set
    the engine loop's pending cudaProfilerStart/Stop flags). ``trace='cuda-sw'``
    is mandatory on Blackwell to avoid hardware-tracing stalls during CUDA-graph
    replay.
    """
    # `cuda` / `cuda-sw` are literal nsys trace backend names — pass through
    # verbatim, exactly like the old sweep runner's `--trace={trace},nvtx`.
    # Remapping cuda-sw to "cuda,...,cudnn" (an earlier bug) silently kept the
    # HARDWARE CUDA tracing backend, reintroducing the Blackwell graph-replay
    # stalls that cuda-sw exists to avoid.
    trace_spec = f"{trace},nvtx,osrt"
    cmd = [
        "nsys",
        "profile",
        "--capture-range=cudaProfilerApi",
        "--capture-range-end=stop",
        f"--trace={trace_spec}",
        f"--cuda-graph-trace={cuda_graph_trace}",
        "--force-overwrite=true",
        "-o",
        str(report_path),
    ]
    if trace == "cuda-sw":
        cmd.append("--cuda-memory-usage=false")
    cmd += list(extra_flags)
    return cmd + list(server_cmd)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_local_model_path(model_id: str) -> str:
    """Resolve *model_id* to a local directory for Pumice's ``--model``.

    Pumice's Rust loader reads ``<model>/tokenizer.json`` straight from disk —
    unlike vLLM there is NO HuggingFace-id resolution or download at startup
    (``async_llm.rs`` panics with ``Failed to load tokenizer from
    <hf-id>/tokenizer.json``). ``target.json`` may carry either a local path or
    an HF repo id; an HF id is resolved against the local HF cache
    (``$HF_HOME/hub``, same layout ``new_target.py`` reads). Refuses to guess:
    an unresolvable id aborts the sweep naming both attempted locations —
    downloading 100GB+ mid-sweep is never what a timing run wants.
    """
    if os.path.isabs(model_id) or os.path.isdir(model_id):
        return model_id
    hub = (
        Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
        / "hub"
        / ("models--" + model_id.replace("/", "--"))
    )
    snapshots = sorted(
        hub.glob("snapshots/*/config.json"), key=lambda c: c.stat().st_mtime
    )
    if snapshots:
        return str(snapshots[-1].parent)
    raise SystemExit(
        f"model_id {model_id!r} is neither a local directory nor a cached HF "
        f"snapshot (looked in {hub}/snapshots). Pumice does not download models "
        "at startup — download the checkpoint first (hf download / hf_transfer) "
        "or put its local path in target.json."
    )


def plan_dp_cohort(
    *,
    server_cmd: Sequence[str],
    env: Dict[str, str],
    tp: int,
    port_factory: Callable[[], int] = _pick_free_port,
) -> Optional[Tuple[List[str], Dict[str, str], List[Tuple[List[str], Dict[str, str], int]]]]:
    """Split a ``--data-parallel-size N`` launch into its per-rank processes.

    Pumice's DP contract (README "Parallelism: TP / EP / DP", coordinated by
    ``pumice-rust/src/dp_coordinator.rs``): **one server process per
    ``--data-parallel-rank``**, each with disjoint ``CUDA_VISIBLE_DEVICES``,
    rendezvousing on shared coordinator ports. A single process launched with
    ``--data-parallel-size N`` and no peers sits at the rendezvous forever —
    weights never load, health never turns, and the sweep dies on timeout with
    no error in the log.

    Returns ``(rank0_cmd, rank0_env, secondaries)`` where each secondary is
    ``(cmd, env, grpc_port)`` for ranks 1..N-1, or ``None`` when there is no
    cohort to plan: ``dp_size <= 1``, or ``--data-parallel-rank`` is already
    pinned in the argv (an operator orchestrating ranks manually).

    Rank *i* gets devices ``visible[i*tp:(i+1)*tp]`` from the inherited
    ``CUDA_VISIBLE_DEVICES`` (or ``0..tp*dp-1`` when unset), its own gRPC
    ``--port``, and identical coordinator flags. Coordinator ports default to
    fresh free ports (NOT Pumice's fixed 13345/13346) so a stale cohort from a
    killed sweep cannot collide with the next launch.
    """
    argv = list(server_cmd)
    dp_size = int(_flag_value(argv, "--data-parallel-size") or 1)
    if dp_size <= 1 or _has_flag(argv, "--data-parallel-rank"):
        return None
    world = tp * dp_size
    visible = env.get("CUDA_VISIBLE_DEVICES")
    devices = (
        [d for d in visible.split(",") if d]
        if visible
        else [str(i) for i in range(world)]
    )
    if len(devices) < world:
        raise SystemExit(
            f"--data-parallel-size {dp_size} with TP={tp} needs {world} GPUs but "
            f"CUDA_VISIBLE_DEVICES={visible!r} exposes only {len(devices)}. "
            "Reserve TP*DP GPUs before the sweep (references/gpu-pool.md)."
        )
    shared = [
        ("--data-parallel-address", _flag_value(argv, "--data-parallel-address") or "127.0.0.1"),
        ("--data-parallel-port", _flag_value(argv, "--data-parallel-port") or str(port_factory())),
        ("--data-parallel-store-port", _flag_value(argv, "--data-parallel-store-port") or str(port_factory())),
    ]

    def _rank_cmd(rank: int) -> List[str]:
        out = _replace_flag_value(argv, "--data-parallel-rank", str(rank))
        for flag, value in shared:
            out = _replace_flag_value(out, flag, value)
        return out

    def _rank_env(rank: int) -> Dict[str, str]:
        rank_env = dict(env)
        rank_env["CUDA_VISIBLE_DEVICES"] = ",".join(devices[rank * tp:(rank + 1) * tp])
        return rank_env

    secondaries: List[Tuple[List[str], Dict[str, str], int]] = []
    for rank in range(1, dp_size):
        grpc_port = port_factory()
        cmd = _replace_flag_value(_rank_cmd(rank), "--port", str(grpc_port))
        secondaries.append((cmd, _rank_env(rank), grpc_port))
    return _rank_cmd(0), _rank_env(0), secondaries


# ---------------------------------------------------------------------------
# gRPC client seam (lazy import so unit tests need no pumice install)
# ---------------------------------------------------------------------------


def _load_pumice_grpc():
    """Import ``grpc`` + the AMMO-owned Pumice stubs in ``scripts/pumice_pb``.

    The stubs come from ``pumice-rust/proto/pumice_engine.proto`` (see
    ``pumice_pb/README.md``). The Python proto in the ``pumice`` wheel comments
    out ``prompt_tokens_cached``, so the runner does not import
    ``pumice.grpc``. The wire contract is the same: both files declare the
    ``pumice.grpc.engine`` package and the ``PumiceEngine`` service.

    Imported lazily inside a function (not at module scope) so that arg parsing,
    schema emission, env sanitization and the freshness gate stay unit-testable
    on a host without ``grpc`` or ``protobuf``.
    """
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import grpc  # noqa: PLC0415
        from pumice_pb import pumice_engine_pb2 as pb2  # noqa: PLC0415
        from pumice_pb import pumice_engine_pb2_grpc as pb2_grpc  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            f"Cannot import grpc or the pumice_pb stubs ({exc}). Run this script "
            "with the session venv's python (.venv/bin/python). That venv must "
            "have grpcio>=1.80.0 and protobuf>=6.31.1."
        )
    return grpc, pb2, pb2_grpc


def _load_tokenizer(model_id: str):
    try:
        from transformers import AutoTokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(f"transformers is required for client-side tokenization ({exc})")
    return AutoTokenizer.from_pretrained(model_id, use_fast=True)


# ---------------------------------------------------------------------------
# Prompt construction (client-side tokenization + chat template)
# ---------------------------------------------------------------------------

_TIMING_CORPUS_CACHE: Dict[str, List[int]] = {}


def load_timing_corpus_text(path: Optional[Path] = None) -> str:
    """Concatenate the bundled GSM8K question+answer text into one corpus.

    Real text (not uniform-random token IDs) is required for faithful
    speculative-decode acceptance: on random tokens the draft and target models
    never agree, acceptance collapses to ~0 and the measurement understates
    throughput by 30-50%.
    """
    corpus_path = Path(path) if path is not None else _GSM8K_FULL_PATH
    if not corpus_path.exists():
        raise SystemExit(
            f"Bundled GSM8K corpus required for real-prompt timing not found at "
            f"{corpus_path}. AMMO sessions cannot download from GitHub — re-bundle "
            "the file, or pass --prompt-source random for tests/debugging only."
        )
    data = json.loads(corpus_path.read_text(encoding="utf-8"))
    items = list(data.get("train", [])) + list(data.get("test", []))
    return "\n\n".join(
        f"Question: {item['question']}\nAnswer: {item['answer']}" for item in items
    )


def load_timing_corpus_tokens(tokenizer, *, path: Optional[Path] = None) -> List[int]:
    """Tokenize the corpus once per tokenizer name (module-level cache)."""
    if tokenizer is None or not hasattr(tokenizer, "encode"):
        raise SystemExit("Real-prompt timing requires a tokenizer with .encode()")
    key = getattr(tokenizer, "name_or_path", None)
    if not isinstance(key, str) or not key:
        key = f"<{type(tokenizer).__module__}.{type(tokenizer).__name__}>"
    cached = _TIMING_CORPUS_CACHE.get(key)
    if cached is not None:
        return cached
    text = load_timing_corpus_text(path)
    try:
        ids = list(tokenizer.encode(text, add_special_tokens=False))
    except TypeError:
        ids = list(tokenizer.encode(text))
    if not ids:
        raise SystemExit("GSM8K timing corpus tokenized to an empty list")
    _TIMING_CORPUS_CACHE[key] = ids
    return ids


def timing_prompt_seed(input_len: int, output_len: int, batch_size: int) -> int:
    """Deterministic, PYTHONHASHSEED-independent seed for prompt windows.

    ``zlib.crc32`` (not builtin ``hash()``, which is salted per process) so both
    A/B arms, every launch and every round derive byte-identical prompts for the
    same workload — removing prompt variance from the comparison entirely.
    """
    return zlib.crc32(f"{input_len}:{output_len}:{batch_size}".encode("utf-8"))


def _corpus_window(corpus_tokens: Sequence[int], start: int, length: int) -> List[int]:
    n = len(corpus_tokens)
    if n == 0:
        raise SystemExit("timing corpus is empty; cannot build prompt windows")
    return [int(corpus_tokens[(start + k) % n]) for k in range(max(0, length))]


def common_prefix_len(a: Sequence[int], b: Sequence[int]) -> int:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return i


def common_suffix_len(a: Sequence[int], b: Sequence[int], *, budget: int) -> int:
    limit = min(len(a), len(b), max(0, budget))
    i = 0
    while i < limit and a[len(a) - 1 - i] == b[len(b) - 1 - i]:
        i += 1
    return i


@dataclass(frozen=True)
class ChatTemplateShape:
    """Where a chat template puts user content, in token space.

    ``prefix`` is the template header (BOS, system/user role markers), ``suffix``
    is the footer plus the assistant-turn generation prompt. Content tokens go
    in between, so a prompt of an exact length is
    ``prefix + <input_len - len(prefix) - len(suffix) content tokens> + suffix``.
    """

    prefix: Tuple[int, ...]
    suffix: Tuple[int, ...]

    @property
    def overhead(self) -> int:
        return len(self.prefix) + len(self.suffix)


def _apply_chat_template_ids(tokenizer, content: str) -> List[int]:
    result = tokenizer.apply_chat_template(
        [{"role": "user", "content": content}],
        add_generation_prompt=True,
        tokenize=True,
    )
    if hasattr(result, "input_ids"):
        result = result.input_ids
    return [int(x) for x in result]


def measure_chat_template_shape(tokenizer, probe_text: str) -> ChatTemplateShape:
    """Derive the chat template's token-space prefix/suffix around user content.

    Templates the empty message and a probe message, then takes their longest
    common prefix and (budget-capped) longest common suffix. Everything between
    is the content region — no sentinel string, no assumption about how the
    tokenizer merges the role markers.
    """
    empty_ids = _apply_chat_template_ids(tokenizer, "")
    filled_ids = _apply_chat_template_ids(tokenizer, probe_text)
    cp = common_prefix_len(empty_ids, filled_ids)
    budget = min(len(empty_ids), len(filled_ids)) - cp
    cs = common_suffix_len(empty_ids, filled_ids, budget=budget)
    return ChatTemplateShape(prefix=tuple(filled_ids[:cp]), suffix=tuple(filled_ids[len(filled_ids) - cs:]) if cs else ())


def build_random_prompt_ids(
    *, input_len: int, output_len: int, batch_size: int, vocab_ceiling: int = 10000
) -> List[List[int]]:
    """Legacy uniform-random token IDs (tests/debug only).

    Kept because it needs no tokenizer and no bundled corpus, which makes it the
    only prompt source usable in a smoke test. It is NOT valid campaign
    evidence: on random tokens the draft and target models never agree, so
    speculative-decode acceptance collapses to ~0.

    Deterministic (seeded from the workload) so a debug run is reproducible.
    """
    if input_len <= 0:
        raise SystemExit(f"input_len must be positive, got {input_len}")
    seed = timing_prompt_seed(input_len, output_len, batch_size)
    prompts: List[List[int]] = []
    for i in range(batch_size):
        state = (seed + i * 2654435761) & 0xFFFFFFFF
        row: List[int] = []
        for _ in range(input_len):
            # xorshift32 — no numpy dependency, stable across interpreters.
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            state &= 0xFFFFFFFF
            row.append(state % vocab_ceiling)
        prompts.append(row)
    return prompts


def build_prompt_ids(
    *,
    shape: Optional[ChatTemplateShape],
    corpus_tokens: Sequence[int],
    input_len: int,
    output_len: int,
    batch_size: int,
) -> List[List[int]]:
    """Build ``batch_size`` prompts of EXACTLY ``input_len`` token IDs each.

    With a ``shape`` the chat template's header/footer are preserved verbatim and
    only the content region is sized — the server sees the same prompt shape a
    real client sends. Without one (``--prompt-source raw``) the window is used
    directly.
    """
    if input_len <= 0:
        raise SystemExit(f"input_len must be positive, got {input_len}")
    overhead = shape.overhead if shape else 0
    content_len = input_len - overhead
    if content_len < 1:
        raise SystemExit(
            f"input_len={input_len} is too small for this model's chat template "
            f"(template overhead is {overhead} tokens). Raise workload.input_len "
            f"above {overhead}, or freeze --prompt-source raw (new_target.py "
            "--prompt-source raw writes bench.prompt_source so every arm inherits it)."
        )
    seed = timing_prompt_seed(input_len, output_len, batch_size)
    prompts: List[List[int]] = []
    for i in range(batch_size):
        # Stride the window start per request so a batch is not N copies of one
        # prompt (which would give prefix caching an unrealistic free ride).
        start = (seed + i * 4093) % max(1, len(corpus_tokens))
        middle = _corpus_window(corpus_tokens, start, content_len)
        ids = (list(shape.prefix) + middle + list(shape.suffix)) if shape else middle
        if len(ids) != input_len:  # pragma: no cover - defensive
            raise SystemExit(
                f"internal error: built prompt of {len(ids)} tokens, expected {input_len}"
            )
        prompts.append(ids)
    return prompts


def resolve_prompt_source(cli_value: Optional[str], target_value: Optional[str]) -> str:
    """Frozen prompt source: an explicit CLI flag wins, then ``bench.prompt_source``
    from ``target.json``, then ``gsm8k``. The target field exists so Stage 1, 5
    and 6 arms inherit the same choice instead of each invocation re-passing
    ``--prompt-source raw`` (gpt-oss: template overhead 67 > input_len 64)."""
    if cli_value:
        return cli_value
    if target_value:
        return target_value
    return DEFAULT_PROMPT_SOURCE


def check_buckets_fit_template(
    buckets: Sequence[Dict[str, int]], shape: Optional[ChatTemplateShape]
) -> None:
    """Fail fast when any bucket's ``input_len`` cannot hold the chat template.

    Same rule as :func:`build_prompt_ids`, checked for every bucket up front so a
    too-short workload dies before the server launch and the correctness phase
    (a 64-token gpt-oss bucket used to fail only after a 745 s golden-ref run).
    """
    overhead = shape.overhead if shape else 0
    bad = sorted({int(b["input_len"]) for b in buckets if int(b["input_len"]) - overhead < 1})
    if bad:
        raise SystemExit(
            f"workload input_len {bad} is too small for this model's chat template "
            f"(template overhead is {overhead} tokens). Raise workload.input_len "
            f"above {overhead}, or freeze --prompt-source raw (new_target.py "
            "--prompt-source raw writes bench.prompt_source so every arm inherits it)."
        )


# ---------------------------------------------------------------------------
# Load driver
# ---------------------------------------------------------------------------


@dataclass
class RequestResult:
    """One ``Generate`` stream's client-observed timings."""

    request_id: str
    submit_t: float
    first_token_t: Optional[float]
    complete_t: float
    completion_tokens: int
    finish_reason: str
    itls: List[float] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def e2e_s(self) -> float:
        return self.complete_t - self.submit_t

    @property
    def prefill_s(self) -> Optional[float]:
        if self.first_token_t is None:
            return None
        return self.first_token_t - self.submit_t

    @property
    def decode_s(self) -> Optional[float]:
        if self.first_token_t is None:
            return None
        return self.complete_t - self.first_token_t


@dataclass
class WaveRecord:
    """One benchmark iteration (a wave of concurrent requests)."""

    index: int
    label: str
    tag: str
    batch_size: int
    input_len: int
    output_len: int
    warmup: bool
    profiled: bool
    start_unix: float
    end_unix: float
    latency_s: float
    num_requests: int
    num_errors: int
    prefill_mean_s: Optional[float] = None
    decode_mean_s: Optional[float] = None
    completion_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "tag": self.tag,
            "batch_size": self.batch_size,
            "input_len": self.input_len,
            "output_len": self.output_len,
            "warmup": self.warmup,
            "profiled": self.profiled,
            "num_requests": self.num_requests,
            "num_errors": self.num_errors,
            "completion_tokens": self.completion_tokens,
            "start_unix": self.start_unix,
            "end_unix": self.end_unix,
            "start_iso": datetime.fromtimestamp(self.start_unix, timezone.utc).isoformat(),
            "end_iso": datetime.fromtimestamp(self.end_unix, timezone.utc).isoformat(),
            "duration_s": self.latency_s,
        }


def _drive_one_request(
    *,
    stub,
    pb2,
    request_id: str,
    prompt_ids: Sequence[int],
    max_tokens: int,
    temperature: float,
    ignore_eos: bool,
    timeout_s: float,
) -> RequestResult:
    """Drive a single streaming ``Generate`` call and time it."""
    request = pb2.GenerateRequest(
        request_id=request_id,
        tokenized=pb2.TokenizedInput(input_ids=list(prompt_ids)),
        sampling_params=pb2.SamplingParams(
            temperature=temperature,
            max_tokens=int(max_tokens),
            ignore_eos=bool(ignore_eos),
        ),
        stream=True,
    )
    submit_t = time.perf_counter()
    first_token_t: Optional[float] = None
    prev_token_t: Optional[float] = None
    itls: List[float] = []
    completion_tokens = 0
    finish_reason = ""
    error: Optional[str] = None
    try:
        for response in stub.Generate(request, timeout=timeout_s):
            now = time.perf_counter()
            if response.HasField("chunk"):
                n_new = len(response.chunk.token_ids)
                if n_new == 0:
                    continue
                if first_token_t is None:
                    first_token_t = now
                elif prev_token_t is not None:
                    # Amortize a multi-token chunk (spec decode emits bursts).
                    itls.extend([(now - prev_token_t) / n_new] * n_new)
                prev_token_t = now
                completion_tokens += n_new
            elif response.HasField("complete"):
                finish_reason = response.complete.finish_reason
                if response.complete.completion_tokens:
                    completion_tokens = int(response.complete.completion_tokens)
                elif response.complete.output_ids:
                    completion_tokens = len(response.complete.output_ids)
    except Exception as exc:  # grpc.RpcError and friends
        error = f"{type(exc).__name__}: {exc}"
    complete_t = time.perf_counter()
    return RequestResult(
        request_id=request_id,
        submit_t=submit_t,
        first_token_t=first_token_t,
        complete_t=complete_t,
        completion_tokens=completion_tokens,
        finish_reason=finish_reason,
        itls=itls,
        error=error,
    )


def drive_wave(
    *,
    stub,
    pb2,
    prompts: Sequence[Sequence[int]],
    output_len: int,
    temperature: float,
    ignore_eos: bool,
    timeout_s: float,
    wave_id: str,
    executor: Optional[ThreadPoolExecutor] = None,
) -> Tuple[List[RequestResult], float, float, float]:
    """Submit all *prompts* concurrently; return ``(results, latency, t0, t1)``.

    ``latency`` is batch wall-clock time — ``max(complete) - min(submit)`` — the
    same quantity the vLLM-era runner recorded per iteration, so the
    ``latencies[]`` array keeps its meaning.
    """
    own_executor = executor is None
    pool = executor or ThreadPoolExecutor(max_workers=max(1, len(prompts)))
    start_unix = time.time()
    try:
        futures = [
            pool.submit(
                _drive_one_request,
                stub=stub,
                pb2=pb2,
                request_id=f"{wave_id}-r{i}",
                prompt_ids=prompt,
                max_tokens=output_len,
                temperature=temperature,
                ignore_eos=ignore_eos,
                timeout_s=timeout_s,
            )
            for i, prompt in enumerate(prompts)
        ]
        results = [f.result() for f in futures]
    finally:
        if own_executor:
            pool.shutdown(wait=True)
    end_unix = time.time()
    if not results:
        return [], 0.0, start_unix, end_unix
    latency = max(r.complete_t for r in results) - min(r.submit_t for r in results)
    return results, latency, start_unix, end_unix


def _mean(values: Sequence[float]) -> Optional[float]:
    vals = [float(v) for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (matches numpy's default method)."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * q
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def summarize_waves(
    waves: Sequence[WaveRecord],
    *,
    itls: Sequence[float] = (),
    spec_decode: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the raw per-bucket bench JSON from measured (non-warmup) waves.

    This is THE downstream contract. Required keys::

        {"avg_latency": float,
         "latencies": [float, ...],
         "percentiles": {"10": f, "25": f, "50": f, "75": f, "90": f, "99": f}}

    ``verify_validation_gates.py`` cross-checks ``avg_latency`` against the
    row's ``avg_s``; the statistics layer keys off ``latencies``. Everything
    else below is additive and omitted when unavailable.
    """
    measured = [w for w in waves if not w.warmup]
    latencies = [float(w.latency_s) for w in measured]
    if not latencies:
        raise SystemExit("summarize_waves: no measured (non-warmup) waves")
    ordered = sorted(latencies)
    raw: Dict[str, Any] = {
        "avg_latency": sum(latencies) / len(latencies),
        "latencies": latencies,
        "percentiles": {str(p): percentile(ordered, p / 100.0) for p in PERCENTILES},
    }

    prefills = [w.prefill_mean_s for w in measured if w.prefill_mean_s is not None]
    decodes = [w.decode_mean_s for w in measured if w.decode_mean_s is not None]
    if prefills and decodes:
        pf_avg = sum(prefills) / len(prefills)
        dc_avg = sum(decodes) / len(decodes)
        raw["prefill_avg_s"] = pf_avg
        raw["decode_avg_s"] = dc_avg
        raw["prefill_p50_s"] = percentile(sorted(prefills), 0.5)
        raw["decode_p50_s"] = percentile(sorted(decodes), 0.5)
        total = pf_avg + dc_avg
        if total > 0:
            raw["decode_share_of_e2e"] = dc_avg / total
        raw["num_request_metric_samples"] = len(prefills)
        # Length-matched per-iteration phase means feed the phase-level Welch
        # test in generate_validation_report.py. A partial series is dropped
        # rather than silently shortened.
        if len(prefills) == len(latencies) and len(decodes) == len(latencies):
            raw["prefill_iter_means_s"] = [float(x) for x in prefills]
            raw["decode_iter_means_s"] = [float(x) for x in decodes]

    itl_vals = [float(x) for x in itls if isinstance(x, (int, float))]
    if itl_vals:
        ordered_itl = sorted(itl_vals)
        raw["itl"] = {
            "avg_s": sum(itl_vals) / len(itl_vals),
            "p50_s": percentile(ordered_itl, 0.50),
            "p90_s": percentile(ordered_itl, 0.90),
            "p99_s": percentile(ordered_itl, 0.99),
            "num_samples": len(itl_vals),
        }

    errors = sum(w.num_errors for w in measured)
    if errors:
        raw["num_request_errors"] = errors
    if spec_decode:
        raw["spec_decode"] = spec_decode
    return raw


# ---------------------------------------------------------------------------
# Statistics + result-row assembly (schema-compatible with the vLLM-era runner)
# ---------------------------------------------------------------------------


def _metrics_from_bench_json(obj: Dict[str, Any]) -> Dict[str, float]:
    """Convert a raw per-bucket bench JSON into the row's ``metrics`` dict."""
    out: Dict[str, float] = {}
    if not isinstance(obj, dict):
        return out
    avg = obj.get("avg_latency")
    if isinstance(avg, (int, float)) and not isinstance(avg, bool):
        out["avg_s"] = float(avg)
    percentiles = obj.get("percentiles")
    if isinstance(percentiles, dict):
        for k, v in percentiles.items():
            if isinstance(k, str) and k.isdigit() and isinstance(v, (int, float)):
                out[f"p{k}_s"] = float(v)
    for key in (
        "prefill_avg_s",
        "decode_avg_s",
        "prefill_p50_s",
        "decode_p50_s",
        "decode_share_of_e2e",
        "prefix_cache_hit_rate_pct",
    ):
        v = obj.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[key] = float(v)
    return out


def _aggregate_launches(launches: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Cross-launch mean/stddev/p50/p90/p99 over measured launches.

    ``stddev_cross_launch`` is a POPULATION stddev so a single measured launch
    deterministically yields 0.0 rather than raising.
    """
    measured: List[float] = []
    prefills: List[float] = []
    decodes: List[float] = []
    for entry in launches or []:
        if entry.get("warmup"):
            continue
        val = entry.get("avg_latency")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            measured.append(float(val))
        pf = entry.get("prefill_avg_s")
        if isinstance(pf, (int, float)):
            prefills.append(float(pf))
        dc = entry.get("decode_avg_s")
        if isinstance(dc, (int, float)):
            decodes.append(float(dc))

    n = len(measured)
    if n == 0:
        return None
    mean_latency = sum(measured) / n
    stddev = 0.0 if n == 1 else math.sqrt(sum((x - mean_latency) ** 2 for x in measured) / n)
    out: Dict[str, Any] = {
        "mean_latency": mean_latency,
        "stddev_cross_launch": stddev,
        "p50": percentile(sorted(measured), 0.50),
        "p90": percentile(sorted(measured), 0.90),
        "p99": percentile(sorted(measured), 0.99),
        "num_measured_launches": n,
    }
    if prefills:
        out["prefill_avg_s"] = sum(prefills) / len(prefills)
    if decodes:
        out["decode_avg_s"] = sum(decodes) / len(decodes)
    pf_avg, dc_avg = out.get("prefill_avg_s"), out.get("decode_avg_s")
    if isinstance(pf_avg, (int, float)) and isinstance(dc_avg, (int, float)):
        total = pf_avg + dc_avg
        if total > 0:
            out["decode_share_of_e2e"] = dc_avg / total
    return out


def _welch_significance(
    baseline_lats: Optional[Sequence[Any]], opt_lats: Optional[Sequence[Any]]
) -> Optional[Dict[str, Any]]:
    """Within-launch Welch check on per-iteration latencies.

    ``significant`` is True when |t| >= 2.0 (~95% two-sided at default
    num_iters). Iterations share one warm server, so this UNDERESTIMATES
    cross-launch variance; it is paired with the PASS floor in
    generate_validation_report.py. Returns None with < 2 samples per side.
    """
    b = [float(x) for x in (baseline_lats or []) if isinstance(x, (int, float))]
    o = [float(x) for x in (opt_lats or []) if isinstance(x, (int, float))]
    nb, no = len(b), len(o)
    if nb < 2 or no < 2:
        return None
    mb, mo = sum(b) / nb, sum(o) / no
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    vo = sum((x - mo) ** 2 for x in o) / (no - 1)
    se = math.sqrt(vb / nb + vo / no)
    delta = mb - mo
    if se == 0.0:
        t_stat: Optional[float] = None
        significant = delta != 0.0
    else:
        t_stat = delta / se
        significant = abs(t_stat) >= 2.0
    return {
        "method": "welch_within_launch",
        "n_baseline": nb,
        "n_opt": no,
        "mean_baseline_s": mb,
        "mean_opt_s": mo,
        "stderr_s": se,
        "t_stat": t_stat,
        "significant": significant,
    }


def _row_phase_significance(baseline_raw: Any, opt_raw: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for phase, key in (("prefill", "prefill_iter_means_s"), ("decode", "decode_iter_means_s")):
        sig = _welch_significance(
            baseline_raw.get(key) if isinstance(baseline_raw, dict) else None,
            opt_raw.get(key) if isinstance(opt_raw, dict) else None,
        )
        if sig is not None:
            out[phase] = sig
    return out


def _compute_noise_flag(
    baseline_mean: float, baseline_stddev: float, opt_mean: float, opt_stddev: float
) -> bool:
    """True when |delta| < 2 * max(stddev) — indistinguishable from run noise."""
    delta = abs(baseline_mean - opt_mean)
    threshold = 2.0 * max(baseline_stddev, opt_stddev)
    if threshold == 0.0:
        return delta == 0.0
    return delta < threshold


def _compute_token_throughput(
    entry: Dict[str, Any], output_len: int, batch_size: int
) -> Dict[str, Any]:
    """Derive OTPS + TPOT, preferring measured decode time over gross E2E."""
    if not isinstance(entry, dict):
        return {}
    out: Dict[str, Any] = {}
    decode_avg_s = entry.get("decode_avg_s")
    if (
        isinstance(decode_avg_s, (int, float))
        and decode_avg_s > 0
        and isinstance(output_len, int)
        and output_len > 1
    ):
        decode_tokens = output_len - 1
        out["tpot_s"] = decode_avg_s / decode_tokens
        out["otps"] = batch_size * decode_tokens / decode_avg_s
        out["throughput_method"] = "decode_metrics"
        return out
    avg_s = entry.get("avg_s")
    if (
        isinstance(avg_s, (int, float))
        and avg_s > 0
        and isinstance(output_len, int)
        and output_len > 0
    ):
        out["otps"] = batch_size * output_len / avg_s
        out["throughput_method"] = "gross_e2e"
    return out


def _check_patterns(text: str, require: Sequence[str], forbid: Sequence[str]) -> Dict[str, Any]:
    req_hits, req_miss = [], []
    for pat in require:
        (req_hits if re.search(pat, text) else req_miss).append(pat)
    forb_hits = [pat for pat in forbid if re.search(pat, text)]
    return {
        "ok": not req_miss and not forb_hits,
        "require_hits": req_hits,
        "require_miss": req_miss,
        "forbid_hits": forb_hits,
    }


def _status_from_evidence(evidence: Dict[str, Any], patterns_configured: bool) -> str:
    if not patterns_configured:
        return "not_configured"
    return "pass" if evidence.get("ok") else "fail"


def _build_label_result_entry(
    *,
    cmd: Sequence[str],
    env_overrides: Dict[str, str],
    metrics: Dict[str, float],
    log_rel: str,
    output_json_rel: str,
    runner_json_rel: str,
    ok: Any,
    returncode: int,
    evidence_status: str,
    evidence: Dict[str, Any],
    timing: Dict[str, Any],
    launches: Optional[List[Dict[str, Any]]] = None,
    aggregate: Optional[Dict[str, Any]] = None,
    spec_decode: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble one label's entry for a ``results[]`` row.

    Schema-identical to the vLLM-era runner's ``_build_label_result_entry`` —
    ``verify_validation_gates.py`` reads ``ok`` / ``returncode`` / ``avg_s`` /
    ``output_json`` / ``runner_json`` and the phase keys off this dict.
    """
    entry: Dict[str, Any] = {
        "ok": ok,
        "returncode": returncode,
        "cmd": list(cmd),
        "env_overrides": dict(env_overrides),
        "metrics": dict(metrics),
        "avg_s": metrics.get("avg_s") if isinstance(metrics, dict) else None,
        "fastpath_evidence": {**evidence, "status": evidence_status},
        "log": log_rel,
        "output_json": output_json_rel,
        "runner_json": runner_json_rel,
        "timing": timing,
    }
    if isinstance(metrics, dict):
        for key in (
            "prefill_avg_s",
            "decode_avg_s",
            "prefill_p50_s",
            "decode_p50_s",
            "decode_share_of_e2e",
        ):
            v = metrics.get(key)
            if isinstance(v, (int, float)):
                entry[key] = float(v)
    if launches is not None:
        entry["launches"] = launches
    if aggregate is not None:
        entry["aggregate"] = aggregate
        if isinstance(aggregate.get("mean_latency"), (int, float)):
            entry["avg_s"] = float(aggregate["mean_latency"])
        for key in ("prefill_avg_s", "decode_avg_s", "decode_share_of_e2e"):
            v = aggregate.get(key)
            if isinstance(v, (int, float)):
                entry[key] = float(v)
    if spec_decode is not None:
        entry["spec_decode"] = spec_decode
    return entry


def finalize_row(
    row: Dict[str, Any],
    *,
    baseline_entry: Dict[str, Any],
    opt_entry: Dict[str, Any],
    baseline_raw: Dict[str, Any],
    opt_raw: Dict[str, Any],
    output_len: int,
    batch_size: int,
) -> Dict[str, Any]:
    """Attach the A/B derived metrics to one ``results[]`` row, in place.

    Adds ``speedup`` / ``improvement_pct`` (the identities
    ``verify_validation_gates.py`` recomputes and cross-checks), OTPS/TPOT deltas,
    within-launch and phase-level Welch significance, and the multi-launch NOISE
    flag. Every key is omitted when its inputs are missing or non-finite, so a
    failed arm produces a schema-valid row rather than nulls that look like data.
    """
    b_avg = baseline_entry.get("avg_s")
    o_avg = opt_entry.get("avg_s")
    if isinstance(b_avg, (int, float)) and isinstance(o_avg, (int, float)) and o_avg > 0:
        row["speedup"] = b_avg / o_avg
        row["improvement_pct"] = (b_avg - o_avg) / b_avg * 100.0 if b_avg else None

    b_tp = _compute_token_throughput(baseline_entry, output_len, batch_size)
    o_tp = _compute_token_throughput(opt_entry, output_len, batch_size)
    baseline_entry.update(b_tp)
    opt_entry.update(o_tp)
    b_otps, o_otps = b_tp.get("otps"), o_tp.get("otps")
    if isinstance(b_otps, (int, float)) and isinstance(o_otps, (int, float)) and b_otps > 0:
        row["otps_gain_pct"] = (o_otps - b_otps) / b_otps * 100.0
    b_tpot, o_tpot = b_tp.get("tpot_s"), o_tp.get("tpot_s")
    if isinstance(b_tpot, (int, float)) and isinstance(o_tpot, (int, float)) and b_tpot > 0:
        row["tpot_improvement_pct"] = (b_tpot - o_tpot) / b_tpot * 100.0

    sig = _welch_significance(
        baseline_raw.get("latencies") if isinstance(baseline_raw, dict) else None,
        opt_raw.get("latencies") if isinstance(opt_raw, dict) else None,
    )
    if sig is not None:
        row["significance"] = sig
    phase_sig = _row_phase_significance(baseline_raw, opt_raw)
    if phase_sig:
        row["phase_significance"] = phase_sig

    b_agg = baseline_entry.get("aggregate")
    o_agg = opt_entry.get("aggregate")
    if (
        isinstance(b_agg, dict)
        and isinstance(o_agg, dict)
        and isinstance(b_agg.get("mean_latency"), (int, float))
        and isinstance(o_agg.get("mean_latency"), (int, float))
    ):
        row["noise"] = _compute_noise_flag(
            float(b_agg["mean_latency"]),
            float(b_agg.get("stddev_cross_launch", 0.0)),
            float(o_agg["mean_latency"]),
            float(o_agg.get("stddev_cross_launch", 0.0)),
        )

    # Prefix-cache regime parity. Pumice cannot disable prefix caching, so
    # arms whose measured hit rates diverge were not benchmarked under the
    # same regime and the speedup is not attributable to the mechanism.
    b_pc = baseline_entry.get("prefix_cache_hit_rate_pct")
    o_pc = opt_entry.get("prefix_cache_hit_rate_pct")
    if b_pc is None and isinstance(baseline_raw, dict):
        b_pc = baseline_raw.get("prefix_cache_hit_rate_pct")
    if o_pc is None and isinstance(opt_raw, dict):
        o_pc = opt_raw.get("prefix_cache_hit_rate_pct")
    if isinstance(b_pc, (int, float)) and isinstance(o_pc, (int, float)):
        gap_pp = abs(float(b_pc) - float(o_pc))
        row["prefix_cache_parity"] = {
            "baseline_hit_rate_pct": float(b_pc),
            "opt_hit_rate_pct": float(o_pc),
            "gap_pp": gap_pp,
            "tolerance_pp": PREFIX_CACHE_PARITY_TOLERANCE_PP,
            "regime_mismatch": gap_pp > PREFIX_CACHE_PARITY_TOLERANCE_PP,
        }
    return row


# ---------------------------------------------------------------------------
# Server-side metrics: EMF lines on the server's stdout
# ---------------------------------------------------------------------------


def parse_emf_spec_metrics(log_text: str) -> Optional[Dict[str, Any]]:
    """Extract spec-decode acceptance from the server's EMF metric lines.

    Pumice's metrics daemon emits one CloudWatch Embedded Metric Format JSON
    document per 5s tick on stdout, including the ``spec_acceptance_rate``
    gauge. That replaces the vLLM-era ``llm.get_metrics()`` scrape of
    ``vllm:spec_decode_*`` counters.

    Returns ``None`` for vanilla-decode targets (no gauge present) so the
    ``spec_decode`` block stays omit-not-null. Never raises.
    """
    rates: List[float] = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{") or "_aws" not in line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        rate = doc.get("spec_acceptance_rate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            rates.append(float(rate))
    if not rates:
        return None
    nonzero = [r for r in rates if r > 0.0]
    sampled = nonzero or rates
    # Pumice's gauge is a PERCENT (metrics.rs: pct(spec_accepted_delta,
    # spec_draft_delta), logged as "spec accept {:.1}%"). Normalize to the
    # 0-1 rate every downstream consumer of draft_acceptance_rate expects,
    # and record the units explicitly so nobody has to re-derive this.
    mean_pct = sum(sampled) / len(sampled)
    return {
        "source": "pumice_emf_metrics",
        "metric": "spec_acceptance_rate",
        "draft_acceptance_rate": mean_pct / 100.0,
        "draft_acceptance_rate_units": "fraction_0_to_1",
        "gauge_mean_percent": mean_pct,
        "num_samples": len(sampled),
        "num_ticks": len(rates),
    }


def parse_emf_prefix_cache_rate(log_text: str) -> Optional[float]:
    """Mean ``prefix_cache_hit_rate`` (percent, 0-100) from the server's EMF ticks.

    Pumice's BlockManager always prefix-caches — there is no disable flag — so
    the benchmark cannot pin the cache regime, only measure it. A/B arms whose
    hit rates diverge are not comparable (dsv4flash OP-007 measured a fake +29%
    from a 96.9%-cached opt arm vs a 0%-cached frozen baseline); the parity
    check in ``finalize_row`` consumes this value. Returns ``None`` when no
    gauge sample is present (e.g. log truncated). Never raises.
    """
    rates: List[float] = []
    for line in log_text.splitlines():
        line = line.strip()
        if not line.startswith("{") or "_aws" not in line:
            continue
        try:
            doc = json.loads(line)
        except json.JSONDecodeError:
            continue
        rate = doc.get("prefix_cache_hit_rate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            rates.append(float(rate))
    if not rates:
        return None
    return sum(rates) / len(rates)


#: Max tolerated |baseline - opt| prefix-cache hit-rate gap, in percentage
#: points. Above this the A/B row is stamped regime_mismatch and the speedup
#: is not attributable to the mechanism. 5pp passes normal jitter around a
#: shared regime (both ~0% or both high) while catching the failure that
#: motivated the check (96.9 vs 0.0).
PREFIX_CACHE_PARITY_TOLERANCE_PP = 5.0

#: WS4.4 counter-based check tolerance. Compares hit ratios from the two arms'
#: Prometheus scrapes (``prefix_cache_hits_total`` / ``prefix_cache_queries_total``)
#: within 1pp. Tighter than the EMF gauge parity check above because scraped
#: counters accumulate over the whole run (no per-tick noise).
PREFIX_CACHE_COUNTER_TOLERANCE_PP = 1.0


def _prefix_cache_flag_in_argv(argv: Sequence[str]) -> Optional[str]:
    """Return ``"on"`` / ``"off"`` when *argv* carries an explicit regime flag."""
    for tok in argv:
        if tok == "--enable-prefix-caching":
            return "on"
        if tok == "--no-enable-prefix-caching":
            return "off"
    return None


def _launch_override_in_log(log_text: str) -> Optional[str]:
    """Return the ``X -> Y`` tail of the first launch-override log line, else None.

    The server prints ``Prefix caching launch override: <confdb> -> <cli>`` when
    the CLI regime beats the confdb default. WS4.4 (b): every arm's log MUST
    carry this line — otherwise we cannot prove the CLI flag reached the
    engine.
    """
    for line in log_text.splitlines():
        idx = line.find(_PREFIX_CACHE_OVERRIDE_TAG)
        if idx >= 0:
            return line[idx + len(_PREFIX_CACHE_OVERRIDE_TAG):].strip()
    return None


def scrape_prefix_cache_counters(
    metrics_port: Optional[int], *, host: str = "127.0.0.1", timeout_s: float = 5.0
) -> Optional[Dict[str, int]]:
    """Scrape ``prefix_cache_hits_total`` / ``prefix_cache_queries_total``.

    Reads the Prometheus text exposition served by the Pumice engine on the
    ``--metrics-port`` port. Returns ``{"hits": int, "queries": int}`` on
    success, ``None`` when the port is unset, the endpoint is unreachable, or
    the counters are absent (e.g. an older server build). Never raises: a
    missing counter is evidence-of-absence but not a crash reason.
    """
    if metrics_port is None:
        return None
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://{host}:{int(metrics_port)}/metrics", timeout=float(timeout_s)
        ) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None
    hits: Optional[int] = None
    queries: Optional[int] = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        try:
            value = float(parts[-1])
        except ValueError:
            continue
        if name == "prefix_cache_hits_total":
            hits = int(value)
        elif name == "prefix_cache_queries_total":
            queries = int(value)
    if hits is None or queries is None:
        return None
    return {"hits": hits, "queries": queries}


def verify_prefix_cache_regime(
    *,
    baseline_argv: Sequence[str],
    opt_argv: Sequence[str],
    baseline_log: str,
    opt_log: str,
    pinned_regime: Optional[str],
    baseline_counters: Optional[Dict[str, int]] = None,
    opt_counters: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Run the WS4.4 three-check per-arm verification.

    Returns a dict::

        {
            "pinned_regime": "on" | "off" | None,
            "flag_present": {"baseline": bool, "opt": bool},
            "override_logged": {"baseline": bool, "opt": bool},
            "counters": {"baseline": {...}, "opt": {...}},
            "hit_ratio_pp": float | None,
            "regime_mismatch": bool,
            "failures": [str, ...],
        }

    A mismatched-arm fixture (one argv has ``--enable-prefix-caching`` and the
    other does not, or the counter ratios diverge beyond 1pp / are non-zero
    when the pin is "off") produces a non-empty ``failures`` list; callers
    ``fail closed`` on it (WS4.4 last sentence).
    """
    b_arm = _prefix_cache_flag_in_argv(baseline_argv)
    o_arm = _prefix_cache_flag_in_argv(opt_argv)
    b_log = _launch_override_in_log(baseline_log)
    o_log = _launch_override_in_log(opt_log)
    failures: List[str] = []

    # Check (a): both argvs carry the flag, matching the pin (when set).
    if pinned_regime is not None:
        if b_arm != pinned_regime:
            failures.append(
                f"baseline argv does not carry the pinned "
                f"--{'enable' if pinned_regime == 'on' else 'no-enable'}-prefix-caching flag"
            )
        if o_arm != pinned_regime:
            failures.append(
                f"opt argv does not carry the pinned "
                f"--{'enable' if pinned_regime == 'on' else 'no-enable'}-prefix-caching flag"
            )
    elif b_arm != o_arm:
        failures.append(
            f"prefix-cache regime differs between arms (baseline={b_arm!r}, opt={o_arm!r})"
        )

    # Check (b): the launch-override log line reached both server logs.
    override_logged_b = b_log is not None
    override_logged_o = o_log is not None
    if pinned_regime is not None:
        if not override_logged_b:
            failures.append(
                f"baseline server log missing {_PREFIX_CACHE_OVERRIDE_TAG!r}"
            )
        if not override_logged_o:
            failures.append(
                f"opt server log missing {_PREFIX_CACHE_OVERRIDE_TAG!r}"
            )

    # Check (c): counter-based ratio comparison from the --metrics-port scrape.
    hit_ratio_pp: Optional[float] = None
    if isinstance(baseline_counters, dict) and isinstance(opt_counters, dict):
        b_q = int(baseline_counters.get("queries") or 0)
        o_q = int(opt_counters.get("queries") or 0)
        b_h = int(baseline_counters.get("hits") or 0)
        o_h = int(opt_counters.get("hits") or 0)
        if pinned_regime == "off":
            # With caching pinned off both arms MUST report zero queries; any
            # nonzero query is the very failure the pin exists to prevent.
            if b_q != 0 or o_q != 0:
                failures.append(
                    "prefix-cache counters nonzero with regime pinned off "
                    f"(baseline_queries={b_q}, opt_queries={o_q})"
                )
        else:
            if b_q == 0 and o_q == 0:
                hit_ratio_pp = 0.0
            else:
                b_ratio = (100.0 * b_h / b_q) if b_q > 0 else 0.0
                o_ratio = (100.0 * o_h / o_q) if o_q > 0 else 0.0
                hit_ratio_pp = abs(b_ratio - o_ratio)
                if hit_ratio_pp > PREFIX_CACHE_COUNTER_TOLERANCE_PP:
                    failures.append(
                        "prefix-cache hit ratio diverges beyond "
                        f"{PREFIX_CACHE_COUNTER_TOLERANCE_PP:.1f}pp "
                        f"(baseline={b_ratio:.2f}%, opt={o_ratio:.2f}%)"
                    )

    return {
        "pinned_regime": pinned_regime,
        "flag_present": {"baseline": b_arm is not None, "opt": o_arm is not None},
        "flag_value": {"baseline": b_arm, "opt": o_arm},
        "override_logged": {"baseline": override_logged_b, "opt": override_logged_o},
        "override_detail": {"baseline": b_log, "opt": o_log},
        "counters": {"baseline": baseline_counters, "opt": opt_counters},
        "hit_ratio_pp": hit_ratio_pp,
        "regime_mismatch": bool(failures),
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Correctness (Gate 5.1b) — GSM8K accuracy comparator
# ---------------------------------------------------------------------------
#
# Ported verbatim from the retired vLLM-era runner: every function below is
# engine-agnostic (pure list/dict arithmetic over already-generated outputs and
# the bundled GSM8K corpus). The GENERATION half is engine-specific and lives in
# run_correctness_phase() further down, which drives the same Pumice Generate
# RPC the timing loop uses.
#
# `verify_validation_gates.py` reads the emitted json/correctness_verdict.json
# (gate == "5.1b", num_questions, baseline_correct_count,
# optimized_correct_count), and eval/scripts/parse_artifacts.py regex-parses the
# `message` string — so both the dict keys and the message format are contracts.


def _verdict_to_code(verdict: Dict[str, Any]) -> int:
    """Map a correctness verdict dict to an exit-code integer for DP broadcast.

    Codes: 0=PASS, 3=FAIL, 4=infrastructure error (or malformed verdict).
    ``infrastructure_error=True`` always maps to 4 regardless of the verdict
    string — the collector should treat infra issues as highest-severity.
    """
    if not isinstance(verdict, dict) or "verdict" not in verdict:
        return 4
    if verdict.get("infrastructure_error"):
        return 4
    return 0 if verdict.get("verdict") == "PASS" else 3


def _load_gsm8k_data(num_questions: int) -> Tuple[List[dict], List[dict]]:
    """Load bundled GSM8K data — fail-loud if the requested split is missing.

    Two-tier lookup:
      * ``num_questions <= 200``: prefer ``gsm8k_subset.json`` (200 test items).
      * Otherwise: require ``gsm8k_full.json`` (1319 test items). No GitHub
        download fallback — AMMO sessions run in a sandbox with no outbound
        network access, so a silent fallback is structurally broken.

    Raises:
        FileNotFoundError: when the bundled file needed for ``num_questions``
            does not exist. The message names the expected path so the fix is
            obvious (re-bundle the file).
    """
    if num_questions <= 200 and _GSM8K_SUBSET_PATH.exists():
        with open(_GSM8K_SUBSET_PATH) as f:
            data = json.load(f)
        return data["train"], data["test"][:num_questions]
    if _GSM8K_FULL_PATH.exists():
        with open(_GSM8K_FULL_PATH) as f:
            data = json.load(f)
        test = data["test"]
        if num_questions > len(test):
            raise FileNotFoundError(
                f"Requested {num_questions} GSM8K questions but bundled "
                f"{_GSM8K_FULL_PATH} only contains {len(test)} test items. "
                "Re-bundle the dataset or reduce --correctness-num-questions."
            )
        return data["train"], test[:num_questions]
    raise FileNotFoundError(
        f"No bundled GSM8K data covers {num_questions} questions. "
        f"Expected {_GSM8K_FULL_PATH} (for num_questions>200) or "
        f"{_GSM8K_SUBSET_PATH} (for num_questions<=200). "
        "AMMO sessions cannot download from GitHub — re-bundle the file."
    )


def _get_answer_value(answer_str: str) -> int:
    answer_str = answer_str.replace(",", "")
    numbers = re.findall(r"\d+", answer_str)
    if len(numbers) < 1:
        return _INVALID_ANSWER
    try:
        return ast.literal_eval(numbers[-1])
    except SyntaxError:
        return _INVALID_ANSWER


def _build_gsm8k_prompts(
    num_questions: int = 200, num_shots: int = 5
) -> Tuple[List[str], List[int]]:
    """Build few-shot GSM8K prompts and ground-truth labels."""
    if num_questions == 0:
        return [], []
    train_data, test_data = _load_gsm8k_data(num_questions)
    num_questions = min(num_questions, len(test_data))
    few_shot_examples = ""
    for i in range(min(num_shots, len(train_data))):
        few_shot_examples += (
            f"Question: {train_data[i]['question']}\n"
            f"Answer: {train_data[i]['answer']}\n\n"
        )
    prompts, labels = [], []
    for i in range(num_questions):
        prompts.append(few_shot_examples + f"Question: {test_data[i]['question']}\nAnswer:")
        labels.append(_get_answer_value(test_data[i]["answer"]))
    assert all(label != _INVALID_ANSWER for label in labels), "Some labels are invalid"
    return prompts, labels


# Stop strings of the few-shot GSM8K prompt. The vLLM-era runner passed these as
# SamplingParams(stop=...) so the model could not run on into a new
# "Question:" and bury its answer under a later integer (the scorer takes the
# LAST integer). Pumice's SamplingParams has no stop field, so the runner cuts
# the decoded text at the first stop string instead; vLLM also drops the stop
# string itself from the output. Golden refs record the list they were scored
# with, and a verify run reuses that list, so old refs (no "stop" key) stay
# comparable.
_GSM8K_STOP_STRINGS: Tuple[str, ...] = ("Question", "Assistant:", "<|separator|>")


def _truncate_at_stop(text: str, stops: Sequence[str]) -> str:
    """Return ``text`` up to (not including) the earliest stop string."""
    cut = len(text)
    for stop in stops:
        if not stop:
            continue
        idx = text.find(stop)
        if 0 <= idx < cut:
            cut = idx
    return text[:cut]


def _score_gsm8k_predictions(
    texts: Sequence[str], labels: Sequence[int]
) -> Tuple[List[int], float]:
    """Score decoded GSM8K completions. Returns ``(predictions, accuracy)``.

    Takes plain decoded strings — the vLLM-era version reached into
    ``RequestOutput.outputs[0].text``, which has no Pumice analogue (the gRPC
    ``Generate`` stream yields token IDs the client detokenizes itself).
    """
    preds = [_get_answer_value(text or "") for text in texts]
    correct = sum(1 for p, label in zip(preds, labels) if p == label)
    accuracy = correct / len(labels) if labels else 0.0
    return preds, accuracy


def _format_correctness_message(
    verdict: str,
    *,
    opt_acc: float,
    opt_correct_count: int,
    n: int,
    threshold: float,
    baseline_acc: float,
    tolerance_pct: float,
) -> str:
    """Build the human-readable PASS/FAIL verdict message for Gate 5.1b.

    Matches the format parsed by eval/scripts/parse_artifacts.py Pattern 0:
      "{PASS|FAIL}: opt_accuracy X% (A/B) {>=|<} threshold Y% "
      "(baseline Z% - Tpp tolerance)"
    """
    op = ">=" if verdict == "PASS" else "<"
    return (
        f"{verdict}: opt_accuracy {opt_acc * 100:.1f}% "
        f"({opt_correct_count}/{n}) {op} threshold {threshold * 100:.1f}% "
        f"(baseline {baseline_acc * 100:.1f}% - "
        f"{tolerance_pct:.1f}pp tolerance)"
    )


def _compare_correctness(
    *,
    golden_refs: List[Dict[str, Any]],
    opt_outputs: List[Dict[str, Any]],
    labels: Optional[List[int]],
    baseline_preds: Optional[List[int]],
    opt_preds: Optional[List[int]],
    tolerance_pct: float = 1.0,
) -> Dict[str, Any]:
    """Compare optimized outputs against golden references (Gate 5.1b v2).

    Verdict: ``opt_accuracy >= baseline_accuracy - tolerance_pct/100``.
    ``tolerance_pct=0.0`` recovers the legacy strict behavior; the default
    ``1.0`` pp allows ~1% drift (e.g. ~13 questions at N=1319) before FAIL.
    Token-level data is computed as diagnostics only (never affects verdict).
    """
    tolerance_pct = float(tolerance_pct)
    num_questions = min(len(golden_refs), len(opt_outputs))

    # ---- Edge case: empty question set ----
    if num_questions == 0:
        return {
            "gate": "5.1b", "verdict": "FAIL",
            "num_questions": 0,
            "baseline_accuracy": 0.0, "optimized_accuracy": 0.0,
            "accuracy_delta": 0.0,
            "baseline_correct_count": 0, "optimized_correct_count": 0,
            "questions_lost": [], "questions_gained": [],
            "tolerance_pct": tolerance_pct,
            "threshold": 0.0,
            "message": "FAIL: no questions to compare (num_questions=0)",
            "diagnostics": {
                "divergent_questions": 0,
                "first_divergence_positions_p50": -1,
                "first_divergence_positions_p95": -1,
                "churn_rate": 0.0,
                "note": "Token-level data is informational only and does not affect the verdict.",
            },
        }

    # ---- Edge case: accuracy gate requires labels + preds ----
    if labels is None or baseline_preds is None or opt_preds is None:
        return {
            "gate": "5.1b", "verdict": "FAIL",
            "num_questions": num_questions,
            "baseline_accuracy": 0.0, "optimized_accuracy": 0.0,
            "accuracy_delta": 0.0,
            "baseline_correct_count": 0, "optimized_correct_count": 0,
            "questions_lost": [], "questions_gained": [],
            "tolerance_pct": tolerance_pct,
            "threshold": 0.0,
            "message": "FAIL: accuracy gate requires labels, baseline_preds, and opt_preds",
            "diagnostics": {
                "divergent_questions": 0,
                "first_divergence_positions_p50": -1,
                "first_divergence_positions_p95": -1,
                "churn_rate": 0.0,
                "note": "Token-level data is informational only and does not affect the verdict.",
            },
            "_error": "Accuracy gate requires labels, baseline_preds, and opt_preds.",
        }

    # ---- Accuracy computation ----
    n = min(len(labels), len(baseline_preds), len(opt_preds), num_questions)
    labels_t = labels[:n]
    baseline_preds_t = baseline_preds[:n]
    opt_preds_t = opt_preds[:n]

    baseline_correct = {i for i, (p, l) in enumerate(zip(baseline_preds_t, labels_t)) if p == l}
    opt_correct = {i for i, (p, l) in enumerate(zip(opt_preds_t, labels_t)) if p == l}
    questions_lost = sorted(baseline_correct - opt_correct)
    questions_gained = sorted(opt_correct - baseline_correct)

    baseline_acc = len(baseline_correct) / n
    opt_acc = len(opt_correct) / n

    # ---- Baseline accuracy floor ----
    if len(baseline_correct) == 0:
        return {
            "gate": "5.1b", "verdict": "FAIL",
            "num_questions": n,
            # UNrounded: verify_validation_gates.py recomputes these from the
            # raw counts and compares with math.isclose(abs_tol=1e-9) — any
            # rounding here fails the gate's tamper check.
            "baseline_accuracy": 0.0, "optimized_accuracy": opt_acc,
            "accuracy_delta": opt_acc,
            "baseline_correct_count": 0,
            "optimized_correct_count": len(opt_correct),
            "questions_lost": [], "questions_gained": questions_gained,
            "tolerance_pct": tolerance_pct,
            # Gate recompute: threshold == baseline_accuracy - tolerance/100.
            "threshold": 0.0 - (tolerance_pct / 100.0),
            "message": "FAIL: baseline accuracy is 0% (infrastructure error)",
            "infrastructure_error": True,
            "infrastructure_message": "Baseline accuracy is 0% — model cannot solve any GSM8K questions; environment suspect.",
            "diagnostics": {
                "divergent_questions": 0,
                "first_divergence_positions_p50": -1,
                "first_divergence_positions_p95": -1,
                "churn_rate": 0.0,
                "note": "Token-level data is informational only and does not affect the verdict.",
            },
        }

    # ---- Verdict: opt_accuracy >= baseline_accuracy - tolerance_pct/100 ----
    threshold = baseline_acc - (tolerance_pct / 100.0)
    verdict = "PASS" if opt_acc >= threshold else "FAIL"

    # ---- Token-level diagnostics (informational only) ----
    first_divergence_positions: List[int] = []
    divergent_questions = 0
    all_empty = True
    # WS4.8 bit-exact diagnostic. A DP wide-EP run must produce the same
    # token IDs on every rank order — if it does not, numerics changed
    # between arms and any downstream speedup number is suspect. Counted
    # here but NEVER promoted to the verdict (verdict stays 1pp accuracy).
    bit_exact_matches = 0

    for q in range(n):
        b_ids = golden_refs[q]["token_ids"]
        o_ids = opt_outputs[q]["token_ids"]
        if b_ids or o_ids:
            all_empty = False
        if list(b_ids) == list(o_ids):
            bit_exact_matches += 1
        min_len = min(len(b_ids), len(o_ids))
        first_div = next((p for p in range(min_len) if b_ids[p] != o_ids[p]), -1)
        if first_div == -1 and len(b_ids) != len(o_ids):
            first_div = min_len
        if first_div >= 0:
            divergent_questions += 1
            first_divergence_positions.append(first_div)

    # All outputs empty → override to FAIL regardless of accuracy
    if all_empty and n > 0:
        verdict = "FAIL"

    # p50/p95 of first_divergence_positions (across divergent questions only)
    if first_divergence_positions:
        sorted_pos = sorted(first_divergence_positions)
        p50_idx = max(0, int(len(sorted_pos) * 0.50) - 1)
        p95_idx = max(0, int(len(sorted_pos) * 0.95) - 1)
        fdp_p50 = sorted_pos[p50_idx]
        fdp_p95 = sorted_pos[p95_idx]
    else:
        fdp_p50 = -1
        fdp_p95 = -1

    churn_rate = round((len(questions_lost) + len(questions_gained)) / n, 4) if n else 0.0

    message = _format_correctness_message(
        verdict,
        opt_acc=opt_acc,
        opt_correct_count=len(opt_correct),
        n=n,
        threshold=threshold,
        baseline_acc=baseline_acc,
        tolerance_pct=tolerance_pct,
    )

    return {
        "gate": "5.1b",
        "verdict": verdict,
        "num_questions": n,
        # UNrounded on purpose: verify_validation_gates.py recomputes each of
        # these from the raw counts (count/questions, delta, baseline - tol/100)
        # and compares with math.isclose(abs_tol=1e-9). Rounding to 4dp made
        # every real accuracy (e.g. 1043/1319) fail the gate's tamper check.
        "baseline_accuracy": baseline_acc,
        "optimized_accuracy": opt_acc,
        "accuracy_delta": opt_acc - baseline_acc,
        "baseline_correct_count": len(baseline_correct),
        "optimized_correct_count": len(opt_correct),
        "questions_lost": questions_lost,
        "questions_gained": questions_gained,
        "tolerance_pct": tolerance_pct,
        "threshold": threshold,
        "message": message,
        "diagnostics": {
            "divergent_questions": divergent_questions,
            "first_divergence_positions_p50": fdp_p50,
            "first_divergence_positions_p95": fdp_p95,
            "churn_rate": churn_rate,
            # WS4.8 bit-exact block. All-match tells a DP wide-EP integrator
            # that numerics are unchanged; any bit_exact_matches < n is a
            # signal that the promoted arm re-orders reductions and every
            # projected speedup must be re-checked against the noise floor.
            "bit_exact_matches": bit_exact_matches,
            "bit_exact": bit_exact_matches == n and n > 0,
            "bit_exact_ratio_pct": round(100.0 * bit_exact_matches / n, 2) if n else 0.0,
            "note": "Token-level data is informational only and does not affect the verdict.",
        },
        "_diagnostic_notes": "p50/p95 computed across questions where first_divergence_pos >= 0; set to -1 if no questions diverge. churn_rate = (questions_lost + questions_gained) / num_questions. bit_exact_matches counts questions whose baseline and opt token_ids are identical (WS4.8 DP wide-EP diagnostic).",
    }


def _render_md_table(rows: List[Dict[str, Any]], baseline_label: str, opt_label: str) -> str:
    # Determine if rows are heterogeneous (mixed IL/OL values).
    il_ol_set: set = set()
    for r in rows:
        il = r.get("input_len")
        ol = r.get("output_len")
        if il is not None and ol is not None:
            il_ol_set.add((il, ol))
    heterogeneous = len(il_ol_set) > 1

    # OTPS/TPOT columns are shown only when at least one row carries the
    # derived throughput data, so legacy runs render the original table.
    has_throughput = any(
        isinstance(r.get(opt_label, {}).get("otps"), (int, float))
        or isinstance(r.get("otps_gain_pct"), (int, float))
        for r in rows
    )
    tput_hdr = f" {opt_label} OTPS (tok/s) | OTPS Δ | {opt_label} TPOT (ms) | TPOT Δ |" if has_throughput else ""
    tput_sep = "---:|---:|---:|---:|" if has_throughput else ""

    if heterogeneous:
        header = f"| Input Len | Output Len | Batch Size | {baseline_label} avg (s) | {opt_label} avg (s) | Speedup | Improvement |{tput_hdr} Fast-path evidence |"
        sep = f"|---:|---:|---:|---:|---:|---:|---:|{tput_sep}---|"
    else:
        header = f"| Batch Size | {baseline_label} avg (s) | {opt_label} avg (s) | Speedup | Improvement |{tput_hdr} Fast-path evidence |"
        sep = f"|---:|---:|---:|---:|---:|{tput_sep}---|"

    lines = [header, sep]
    for r in rows:
        bs = r["batch_size"]
        b = r.get(baseline_label, {})
        o = r.get(opt_label, {})
        b_avg = b.get("avg_s")
        o_avg = o.get("avg_s")
        speedup = r.get("speedup")
        improve = r.get("improvement_pct")
        evidence = o.get("fastpath_evidence", {}).get("status", "unknown")

        def fmt(x: Any) -> str:
            if x is None:
                return ""
            if isinstance(x, (int, float)):
                return f"{x:.6g}"
            return str(x)

        # Throughput cells: opt OTPS (tok/s), OTPS gain %, opt TPOT (ms), TPOT improvement %.
        tput_cells = ""
        if has_throughput:
            o_otps = o.get("otps")
            o_tpot_s = o.get("tpot_s")
            o_tpot_ms = o_tpot_s * 1000.0 if isinstance(o_tpot_s, (int, float)) else None
            otps_gain = r.get("otps_gain_pct")
            tpot_improve = r.get("tpot_improvement_pct")
            tput_cells = f" {fmt(o_otps)} | {fmt(otps_gain)}% | {fmt(o_tpot_ms)} | {fmt(tpot_improve)}% |"

        if heterogeneous:
            il = r.get("input_len", "")
            ol = r.get("output_len", "")
            lines.append(
                f"| {il} | {ol} | {bs} | {fmt(b_avg)} | {fmt(o_avg)} | {fmt(speedup)}x | {fmt(improve)}% |{tput_cells} {evidence} |"
            )
        else:
            lines.append(
                f"| {bs} | {fmt(b_avg)} | {fmt(o_avg)} | {fmt(speedup)}x | {fmt(improve)}% |{tput_cells} {evidence} |"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# profile_meta
# ---------------------------------------------------------------------------


#: ``capture_mechanism`` value for a run whose waves were bracketed by the
#: StartProfile/StopProfile RPCs under an ``nsys`` capture (the CUDA path).
NSYS_CAPTURE_MECHANISM = "grpc_start_stop_profile+nsys_cudaProfilerApi"
#: ``capture_mechanism`` value for a timing-only run with no profiler attached
#: (every wave ``profiled: false``, ``nsys_report: null``). Neuron runs and any
#: run without ``--nsys-profile`` land here — the CUDA label would be a lie.
NO_PROFILE_CAPTURE_MECHANISM = "none_timing_only_no_profiler"


def build_profile_meta(
    *,
    waves: Sequence[WaveRecord],
    nsys_report_rel: Optional[str],
    server_start_unix: Optional[float],
    profile_start_unix: Optional[float],
    profile_stop_unix: Optional[float],
    capture_mechanism: Optional[str] = None,
) -> Dict[str, Any]:
    """Wall-clock capture identity for offline correlation with the nsys report.

    Client-side NVTX cannot appear in the SERVER's nsys timeline (separate
    process), and Pumice has no ``vllm.profiler.wrapper`` analogue to inject
    per-capture NVTX names into. So the identity that the deleted
    ``sitecustomize.py`` monkeypatch used to stamp into NVTX ranges now lives
    here: every wave's wall-clock boundaries plus the StartProfile/StopProfile
    window, correlated against the capture range offline.

    ``capture_mechanism`` records what actually ran. When the caller does not
    pass one it is derived from the capture window: the CUDA
    StartProfile/StopProfile + nsys label only when an nsys capture was active
    (an ``nsys_report_rel`` plus a StartProfile/StopProfile window), otherwise
    an explicit no-profiler value. This stops a timing-only Neuron run from
    carrying a CUDA-profiler label the run never used.
    """
    if capture_mechanism is None:
        nsys_active = (
            nsys_report_rel is not None
            and profile_start_unix is not None
            and profile_stop_unix is not None
        )
        capture_mechanism = (
            NSYS_CAPTURE_MECHANISM if nsys_active else NO_PROFILE_CAPTURE_MECHANISM
        )
    meta: Dict[str, Any] = {
        "schema": "AMMO_CAPTURE_V2",
        "capture_mechanism": capture_mechanism,
        "nsys_report": nsys_report_rel,
        "server_start_unix": server_start_unix,
        "profile_start_unix": profile_start_unix,
        "profile_stop_unix": profile_stop_unix,
        "waves": [w.to_dict() for w in waves],
    }
    if profile_start_unix is not None and profile_stop_unix is not None:
        meta["profile_window_s"] = profile_stop_unix - profile_start_unix
        meta["profiled_wave_count"] = sum(1 for w in waves if w.profiled)
    return meta


# ---------------------------------------------------------------------------
# Server process supervision
# ---------------------------------------------------------------------------


class ServerExitedBeforeHealthy(SystemExit):
    """The server process exited before ``HealthCheck`` reported healthy.

    A ``SystemExit`` subclass, so every caller that did not opt in sees the
    same exit as before. ``run_label_launch`` catches it to tell a bring-up
    crash apart from a health timeout.
    """


# The one bring-up crash the runner retries. At TP bring-up a worker can fail
# to attach rank 0's TP-broadcast shared-memory segment; WorkerProcessMonitor
# then calls os._exit(1) and the whole server dies before it is healthy. The
# root cause is unknown and the crash is not deterministic, so one relaunch is
# the documented mitigation. A second failure stops the run.
PSM_ATTACH_RACE_REASON = "psm_attach_race"
_PSM_ATTACH_RACE_LINE = "FileNotFoundError: [Errno 2] No such file or directory: '/psm_"
_TRACEBACK_HEADER = "Traceback (most recent call last):"
_LAUNCH_MARKER = "\n=== launch "


def is_psm_attach_race(log_text: str) -> bool:
    """True when the FIRST traceback of the latest launch is the /psm_ race.

    Only the text after the last ``=== launch`` marker (written by
    ``ServerHandle.start``) is read, because the log opens in append mode. The
    first ``Traceback`` block's frame lines are indented; the first line that
    is not indented is its exception line, and it must start with the
    ``FileNotFoundError ... '/psm_`` text. Any other first traceback, an
    interleaved second traceback, or no traceback at all returns False.
    """
    marker = log_text.rfind(_LAUNCH_MARKER)
    section = log_text[marker:] if marker >= 0 else log_text
    lines = section.splitlines()
    for idx, line in enumerate(lines):
        if not line.rstrip().endswith(_TRACEBACK_HEADER):
            continue
        for follow in lines[idx + 1:]:
            if follow[:1] in (" ", "\t"):
                continue
            return follow.startswith(_PSM_ATTACH_RACE_LINE)
        return False
    return False


class ServerHandle:
    """A launched Pumice server subprocess plus its gRPC channel/stub.

    Launched with ``start_new_session=True`` so ``killpg`` reaps the whole
    group: Pumice spawns worker PROCESSES for TP ranks 1..N-1 and one process
    per data-parallel rank, and they inherit the session leader.
    """

    def __init__(
        self,
        *,
        cmd: Sequence[str],
        env: Dict[str, str],
        port: int,
        log_path: Path,
        cwd: Optional[str] = None,
    ) -> None:
        self.cmd = list(cmd)
        self.env = dict(env)
        self.port = int(port)
        self.log_path = Path(log_path)
        self.cwd = cwd
        self.proc: Optional[subprocess.Popen] = None
        self.channel = None
        self.stub = None
        self.pb2 = None
        self.start_unix: Optional[float] = None
        self._log_fh = None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "a", buffering=1, encoding="utf-8")
        self._log_fh.write(f"\n=== launch {_utc_now().isoformat()} ===\n")
        self._log_fh.write(_format_cmd_for_md(self.cmd, {}) + "\n")
        self._log_fh.flush()
        self.start_unix = time.time()
        self.proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            self.cmd,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            env=self.env,
            cwd=self.cwd,
            start_new_session=True,
        )

    def connect(self) -> None:
        grpc, pb2, pb2_grpc = _load_pumice_grpc()
        self.pb2 = pb2
        self.channel = grpc.insecure_channel(
            f"localhost:{self.port}",
            options=[
                ("grpc.max_receive_message_length", 256 * 1024 * 1024),
                ("grpc.max_send_message_length", 256 * 1024 * 1024),
            ],
        )
        self.stub = pb2_grpc.PumiceEngineStub(self.channel)

    def wait_healthy(self, *, timeout_s: float, poll_s: float = 2.0, on_wait=None) -> None:
        """Poll ``HealthCheck`` until the server reports healthy.

        The timeout is generous by default (1800s): on a fresh FlashInfer cache
        the first launch on sm_100a JIT-compiles ``fused_moe_trtllm_sm100``,
        which takes ~11 minutes wall. Subsequent launches hit ninja's
        up-to-date check and drop back under 30s.
        """
        assert self.stub is not None and self.pb2 is not None, "connect() first"
        deadline = time.monotonic() + float(timeout_s)
        last_error = "no attempt made"
        while time.monotonic() < deadline:
            rc = self.poll()
            if rc is not None:
                raise ServerExitedBeforeHealthy(
                    f"Pumice server exited with returncode={rc} before becoming "
                    f"healthy. See {self.log_path}"
                )
            try:
                resp = self.stub.HealthCheck(self.pb2.HealthCheckRequest(), timeout=5)
                if getattr(resp, "healthy", False):
                    return
                last_error = f"unhealthy: {getattr(resp, 'message', '')}"
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if on_wait is not None:
                on_wait(last_error)
            time.sleep(poll_s)
        raise SystemExit(
            f"Pumice server did not become healthy within {timeout_s:.0f}s "
            f"(last: {last_error}). See {self.log_path}. First launch on a fresh "
            "FlashInfer cache can JIT for ~11 min — raise --health-timeout-s if "
            "that is expected."
        )

    def poll(self) -> Optional[int]:
        return None if self.proc is None else self.proc.poll()

    def start_profile(self) -> float:
        assert self.stub is not None and self.pb2 is not None
        self.stub.StartProfile(self.pb2.StartProfileRequest(), timeout=60)
        return time.time()

    def stop_profile(self) -> float:
        assert self.stub is not None and self.pb2 is not None
        self.stub.StopProfile(self.pb2.StopProfileRequest(), timeout=300)
        return time.time()

    def read_log(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def stop(self, *, grace_s: float = 30.0) -> None:
        """SIGTERM the process group, then SIGKILL what survives."""
        if self.channel is not None:
            try:
                self.channel.close()
            except Exception:
                pass
            self.channel = None
            self.stub = None
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    self.proc.terminate()
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        self.proc.kill()
                    except OSError:
                        pass
                try:
                    self.proc.wait(timeout=grace_s)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            finally:
                self._log_fh = None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct the CLI. Pure — safe to call in tests."""
    p = argparse.ArgumentParser(
        prog="run_pumice_grpc_bench.py",
        description="E2E latency sweep against a Pumice gRPC server.",
    )
    p.add_argument("--artifact-dir", type=str, required=True)
    p.add_argument(
        "--target-json",
        type=str,
        default=None,
        help="Path to target.json (default: {artifact_dir}/target.json)",
    )
    p.add_argument(
        "--out-name",
        type=str,
        default="e2e_latency",
        help="(LEGACY) Output dir under {artifact_dir}. On v2 layouts use --round/--slot.",
    )
    p.add_argument(
        "--round",
        type=int,
        default=None,
        help="Campaign round (1-indexed). With --slot resolves to rounds/{N}/sweeps/{SLOT}/.",
    )
    p.add_argument(
        "--slot",
        type=str,
        default=None,
        help="Sweep slot under rounds/{N}/sweeps/: baseline | opt/{op_id} | integration | ...",
    )
    p.add_argument(
        "--labels",
        type=str,
        default="baseline",
        help=(
            "Comma-separated labels to run: 'baseline', 'opt' or 'baseline,opt'. "
            "Stage 5 validation uses '--labels opt --baseline-from <stage1_dir>' — "
            "never re-run a baseline from the worktree."
        ),
    )
    p.add_argument(
        "--baseline-from",
        type=str,
        default=None,
        help="Accepted Stage 1 baseline sweep dir to import baseline timing from.",
    )
    p.add_argument(
        "--baseline-from-arm",
        type=str,
        choices=["baseline", "opt"],
        default="baseline",
        help=(
            "Which arm of --baseline-from is imported as the comparator. The import "
            "is by literal filename {prefix}_{tag}.json, so 'baseline' (default) reads "
            "that directory's baseline arm and 'opt' reads its optimized arm. Round >= 2 "
            "integration sweeps whose --baseline-from is a pre-SHIP directory must pass "
            "'opt' to compare against the promoted mainline instead of the stale "
            "pre-SHIP baseline."
        ),
    )
    p.add_argument(
        "--num-launches",
        type=int,
        default=1,
        help=(
            "Server launches per label (default 1). With N>=2, launch 1 is a "
            "discarded warmup and cross-launch mean/stddev/p50/p90/p99 come from "
            "launches 2..N."
        ),
    )
    p.add_argument(
        "--warmup-waves",
        type=int,
        default=1,
        help="Discarded warmup waves per bucket before the measured ones (default 1).",
    )
    p.add_argument(
        "--repo-root",
        type=str,
        default=None,
        help="Pumice checkout root for the freshness gate (default: CWD).",
    )
    p.add_argument(
        "--allow-stale-engine",
        action="store_true",
        default=False,
        help=(
            "Measure even when the built engine is older than pumice-rust sources "
            "or cannot be located. Stamps freshness.overridden=true into the "
            "output JSON — the evidence records that the run is unsound."
        ),
    )
    p.add_argument(
        "--health-timeout-s",
        type=int,
        default=1800,
        help=(
            "Seconds to wait for the HealthCheck RPC after launching the server "
            "(default 1800). A fresh FlashInfer cache JIT-compiles "
            "fused_moe_trtllm_sm100 on sm_100a for ~11 min on first launch."
        ),
    )
    p.add_argument(
        "--request-timeout-s",
        type=int,
        default=1200,
        help="Per-request Generate RPC deadline (default 1200).",
    )
    p.add_argument("--port", type=int, default=0, help="gRPC port (0 = pick a free one)")
    p.add_argument("--timeout-s", type=int, default=1800, help="Wall-clock budget per bucket")
    p.add_argument("--heartbeat-s", type=int, default=30, help="Heartbeat interval when quiet")
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the existing output dir instead of archiving it",
    )
    p.add_argument(
        "--require-fastpath",
        action="store_true",
        help="Fail if the opt arm's fast-path evidence patterns do not pass",
    )
    p.add_argument(
        "--fresh-cache",
        action="store_true",
        default=False,
        help=(
            "Isolate PUMICE_CACHE_ROOT / Triton / Inductor caches per sweep. The "
            "first launch pays full compile; the dir is removed after success."
        ),
    )
    p.add_argument(
        "--prompt-source",
        choices=["gsm8k", "raw", "random"],
        default=None,
        help=(
            "Prompt content: 'gsm8k' (bundled corpus windows wrapped in "
            "the model's chat template), 'raw' (corpus windows, no template), "
            "'random' (uniform token IDs; tests/debug only — collapses spec-decode "
            "acceptance and is NOT valid evidence). Default: bench.prompt_source "
            "from target.json, else gsm8k."
        ),
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (default 0.0 = greedy, required for A/B parity)",
    )
    p.add_argument(
        "--nsys-profile",
        action="store_true",
        default=False,
        help=(
            "Wrap the server in nsys profile --capture-range=cudaProfilerApi and "
            "bracket the profiled waves with StartProfile/StopProfile."
        ),
    )
    p.add_argument(
        "--nsys-trace",
        choices=["cuda", "cuda-sw"],
        default="cuda",
        help="Use 'cuda-sw' on Blackwell (B200/B300) to avoid graph-replay stalls.",
    )
    p.add_argument(
        "--nsys-mode",
        choices=["node", "graph"],
        default="node",
        help="nsys --cuda-graph-trace mode ('node' is the Stage 2 ranking default)",
    )
    p.add_argument(
        "--nsys-extra-flags",
        type=str,
        default="",
        help="Extra flags appended to nsys profile (e.g. '--stats=true')",
    )
    p.add_argument(
        "--profile-waves",
        type=int,
        default=1,
        help="Measured waves per bucket inside the StartProfile/StopProfile window",
    )
    p.add_argument(
        "--capture-golden-refs",
        action="store_true",
        default=False,
        help="Stage 1: greedy GSM8K decode; save golden refs to json/golden_refs.json",
    )
    p.add_argument(
        "--verify-correctness",
        action="store_true",
        default=False,
        help=(
            "Stage 5 (Gate 5.1b): compare GSM8K accuracy against golden refs and "
            "exit nonzero on FAIL. Requires --baseline-from to import golden_refs.json."
        ),
    )
    p.add_argument(
        "--correctness-num-questions",
        type=int,
        default=1319,
        help="GSM8K questions for the correctness phase (default 1319 = full test split)",
    )
    p.add_argument(
        "--correctness-tolerance-pct",
        type=float,
        default=1.0,
        help=(
            "Allowed accuracy drop in percentage points (default 1.0). PASS iff "
            "opt_accuracy >= baseline_accuracy - tolerance/100. 0.0 = strict."
        ),
    )
    p.add_argument(
        "--correctness-max-tokens",
        type=int,
        default=1024,
        help=(
            "Max new tokens per GSM8K completion in the correctness phase "
            "(default 1024, must be > 0). Baseline capture and opt verify MUST "
            "use the same value: the golden-ref metadata records it and a verify "
            "run hard-fails on a mismatch."
        ),
    )
    return p


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse + cross-validate CLI args. Raises ``SystemExit`` on bad combos."""
    args = build_arg_parser().parse_args(argv)
    if args.num_launches < 1:
        raise SystemExit(f"--num-launches must be >= 1, got {args.num_launches}")
    if args.warmup_waves < 0:
        raise SystemExit(f"--warmup-waves must be >= 0, got {args.warmup_waves}")
    if args.profile_waves < 1:
        raise SystemExit(f"--profile-waves must be >= 1, got {args.profile_waves}")
    if args.health_timeout_s <= 0:
        raise SystemExit(f"--health-timeout-s must be positive, got {args.health_timeout_s}")
    if args.temperature < 0.0:
        raise SystemExit(f"--temperature must be >= 0, got {args.temperature}")
    labels = [x.strip() for x in str(args.labels).split(",") if x.strip()]
    if not labels or any(x not in ("baseline", "opt") for x in labels):
        raise SystemExit(
            f"--labels must be a comma-separated subset of 'baseline,opt', got {args.labels!r}"
        )
    args.selected_labels = labels
    if args.nsys_trace == "cuda-sw" and not args.nsys_profile:
        raise SystemExit("--nsys-trace requires --nsys-profile")
    if args.nsys_extra_flags and not args.nsys_profile:
        raise SystemExit("--nsys-extra-flags requires --nsys-profile")
    if args.capture_golden_refs and args.verify_correctness:
        raise SystemExit(
            "--capture-golden-refs and --verify-correctness are mutually exclusive "
            "(capture is Stage 1 on the baseline; verify is Stage 5 on the opt arm)"
        )
    if args.verify_correctness and not args.baseline_from:
        raise SystemExit(
            "--verify-correctness requires --baseline-from (to import golden_refs.json "
            "from the accepted Stage 1 baseline)"
        )
    if args.correctness_num_questions < 0:
        raise SystemExit(
            f"--correctness-num-questions must be >= 0, got {args.correctness_num_questions}"
        )
    if args.correctness_tolerance_pct < 0.0:
        raise SystemExit(
            "--correctness-tolerance-pct must be >= 0, got "
            f"{args.correctness_tolerance_pct}"
        )
    if args.correctness_max_tokens <= 0:
        raise SystemExit(
            f"--correctness-max-tokens must be > 0, got {args.correctness_max_tokens}"
        )
    if (args.capture_golden_refs or args.verify_correctness) and args.prompt_source == "random":
        raise SystemExit(
            "--prompt-source random cannot be combined with the correctness phase: "
            "GSM8K scoring needs the bundled corpus and a real tokenizer."
        )
    return args


# ---------------------------------------------------------------------------
# Sweep orchestration
# ---------------------------------------------------------------------------


@dataclass
class RunSpec:
    """One A/B arm: a label plus the exact env and server flags it runs with."""

    label: str
    env: Dict[str, str]
    server_args: List[str]
    env_overrides: Dict[str, str]
    require_patterns: List[str]
    forbid_patterns: List[str]


def build_run_specs(
    tgt: TargetSpec,
    *,
    inherited_env: Dict[str, str],
    sweep_cache_root: Optional[Path] = None,
    worktree_root: Optional[str] = None,
) -> Dict[str, RunSpec]:
    """Build the two A/B arms' exact environments and server flags.

    Order matters and is load-bearing:

    1. sanitize (drop every inherited ``PUMICE_*`` / ``VLLM_*`` except the arm's
       own declared keys and the ``ENGINE_INFRA_ENV`` platform names) — otherwise a leftover flag from a previous round
       silently contaminates the measurement;
    2. inject the per-sweep compile-cache dirs (AFTER sanitize, because
       ``PUMICE_CACHE_ROOT`` matches the sanitizer's own prefix);
    3. prepend the worktree to ``PYTHONPATH`` so the agent's Python-layer edits
       are live;
    4. layer the arm's declared vars on top. The opt arm gets
       ``baseline_env`` first, then ``opt_env`` — a shipped flag promoted into
       baseline_env stays set, and the round's experimental flag wins on
       collision.

    Pure: the caller passes the inherited environment, so tests do not touch
    ``os.environ``.
    """
    preserve_baseline = set(tgt.baseline_env)
    preserve_opt = preserve_baseline | set(tgt.opt_env)
    base_baseline = _sanitize_engine_op_env(inherited_env, preserve_keys=preserve_baseline)
    base_opt = _sanitize_engine_op_env(inherited_env, preserve_keys=preserve_opt)
    if sweep_cache_root is not None:
        base_baseline = _inject_fresh_cache_env(base_baseline, sweep_cache_root)
        base_opt = _inject_fresh_cache_env(base_opt, sweep_cache_root)
    base_baseline = _ensure_worktree_pythonpath(base_baseline, worktree_root)
    base_opt = _ensure_worktree_pythonpath(base_opt, worktree_root)
    return {
        "baseline": RunSpec(
            label=tgt.baseline_label,
            env={**base_baseline, **tgt.baseline_env},
            server_args=list(tgt.extra_args) + list(tgt.baseline_extra_args),
            env_overrides=dict(tgt.baseline_env),
            require_patterns=list(tgt.baseline_require),
            forbid_patterns=list(tgt.baseline_forbid),
        ),
        "opt": RunSpec(
            label=tgt.opt_label,
            env={**base_opt, **tgt.baseline_env, **tgt.opt_env},
            server_args=list(tgt.extra_args) + list(tgt.opt_extra_args),
            env_overrides={**tgt.baseline_env, **tgt.opt_env},
            require_patterns=list(tgt.opt_require),
            forbid_patterns=list(tgt.opt_forbid),
        ),
    }


#: GPU labels the L40S harness runs on. Rows for these GPUs get stamped
#: ``harness validation only`` so downstream reports never quote L40S numbers
#: as performance evidence. Per plan section 0: "results are harness validation
#: only" on g6e-cdd.
_L40S_LABELS: Tuple[str, ...] = ("L40S", "L40s", "l40s")

#: WS4.6 label attached to results-header + rows when GPU is L40S.
_L40S_HARNESS_LABEL = "harness validation only"


def _detect_host_stamp() -> Dict[str, Any]:
    """Return ``{host, gpu, torch_version, vllm_version}`` best-effort.

    Any component that cannot be read is ``None`` (never raises). ``vllm_version``
    is ``None`` on Neuron (no vLLM there); on CUDA it comes from the pumice
    engine's embedded runner. ``torch_version`` reads ``importlib.metadata`` so a
    machine with no ``torch`` install still stamps the row instead of crashing.
    """
    import socket

    host: Optional[str] = None
    try:
        host = socket.gethostname() or None
    except Exception:
        host = None

    gpu: Optional[str] = None
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            lines = [x.strip() for x in result.stdout.splitlines() if x.strip()]
            gpu = lines[0] if lines else None
    except (OSError, subprocess.SubprocessError):
        gpu = None

    def _mod_version(name: str) -> Optional[str]:
        try:
            import importlib.metadata as md

            return md.version(name)
        except Exception:
            return None

    return {
        "host": host,
        "gpu": gpu,
        "torch_version": _mod_version("torch"),
        "vllm_version": _mod_version("vllm"),
    }


def _stamp_row_environment(
    row: Dict[str, Any], host_stamp: Dict[str, Any], model_runner: str
) -> None:
    """Copy WS4.6 host / gpu / model_runner / *_version fields onto *row*.

    Also stamps ``harness_label`` on L40S rows: the L40S host is
    ``harness validation only`` and no report should quote its numbers as
    performance evidence. In-place.
    """
    row["host"] = host_stamp.get("host")
    row["gpu"] = host_stamp.get("gpu")
    row["model_runner"] = model_runner
    row["torch_version"] = host_stamp.get("torch_version")
    row["vllm_version"] = None if model_runner == "neuron" else host_stamp.get("vllm_version")
    gpu = (host_stamp.get("gpu") or "")
    if any(tag in gpu for tag in _L40S_LABELS):
        row["harness_label"] = _L40S_HARNESS_LABEL


def build_results_header(
    tgt: TargetSpec,
    *,
    args: argparse.Namespace,
    target_path: Path,
    out_root: Path,
    out_name: str,
    server_exe: Sequence[str],
    freshness_payload: Dict[str, Any],
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ``e2e_latency_results.json`` envelope (everything but ``results``).

    ``verify_validation_gates.py`` validates ``execution_mode``, ``out_dir``,
    ``target_json``, ``baseline_source`` and ``bench.{baseline,opt}_label`` from
    this envelope, so the key names are a hard contract — do not rename them
    without updating the gate verifier in lockstep.
    """
    return {
        "generated_at": generated_at or _utc_now().isoformat(),
        "target_json": str(target_path),
        "out_dir": str(out_root),
        "out_name": out_name,
        "execution_mode": EXECUTION_MODE,
        "runner": RUNNER_NAME,
        "model_id": tgt.model_id,
        "tp": tgt.tp,
        "ep": tgt.ep,
        "max_model_len": tgt.max_model_len,
        "engine_freshness": dict(freshness_payload),
        "workload": {
            "input_len": tgt.raw_workload.get("input_len"),
            "output_len": tgt.raw_workload.get("output_len"),
            "num_iters": tgt.num_iters,
            "batch_sizes": tgt.raw_workload.get("batch_sizes"),
            "buckets": [
                {k: v for k, v in b.items() if k != "num_iters"} for b in tgt.buckets
            ],
            "warmup_waves": args.warmup_waves,
            "prompt_source": args.prompt_source,
            "temperature": args.temperature,
        },
        "bench": {
            "runner": RUNNER_NAME,
            "server_exe": list(server_exe),
            "extra_args": list(tgt.extra_args),
            "baseline_extra_args": list(tgt.baseline_extra_args),
            "opt_extra_args": list(tgt.opt_extra_args),
            "baseline_env": dict(tgt.baseline_env),
            "opt_env": dict(tgt.opt_env),
            "baseline_label": tgt.baseline_label,
            "opt_label": tgt.opt_label,
            "fastpath_evidence": tgt.fastpath_evidence,
        },
        "results": [],
    }


def _resolve_max_num_seqs(buckets: Sequence[Dict[str, int]], server_args: Sequence[str]) -> int:
    if _has_flag(server_args, "--max-num-seqs"):
        for i, tok in enumerate(server_args):
            if tok == "--max-num-seqs" and i + 1 < len(server_args):
                return int(server_args[i + 1])
            if tok.startswith("--max-num-seqs="):
                return int(tok.split("=", 1)[1])
    return max(int(b["batch_size"]) for b in buckets)


def _write_status(path: Path, payload: Dict[str, Any]) -> None:
    payload = {**payload, "updated_at": _utc_now().isoformat()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_label_launch(
    *,
    run: RunSpec,
    launch_id: int,
    is_warmup_launch: bool,
    buckets: Sequence[Dict[str, int]],
    all_buckets: Sequence[Dict[str, int]],
    model_id: str,
    tp: int,
    max_model_len: int,
    server_exe: Sequence[str],
    args: argparse.Namespace,
    out_root: Path,
    logs_dir: Path,
    json_dir: Path,
    status_dir: Path,
    nsys_dir: Optional[Path],
    run_correctness: bool = False,
    tokenizer_factory: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Launch one server for *run* and sweep every bucket over it.

    When *run_correctness* is set (and the corresponding CLI flags are on), the
    Gate 5.1b GSM8K phase runs on this same warm server before the timing waves.

    *tokenizer_factory* is resolved at call time (not bound as a default) so a
    test can substitute a stub tokenizer without ``transformers`` installed.

    Returns a dict with ``buckets`` (per-tag raw/status/waves), the launch's
    ``waves``, the profile window boundaries and the correctness exit code.
    """
    if tokenizer_factory is None:
        tokenizer_factory = _load_tokenizer
    suffix = f".launch{launch_id}" if launch_id > 1 or args.num_launches > 1 else ""
    server_log = logs_dir / f"{run.label}{suffix}.server.log"
    status_path = status_dir / f"{run.label}.json"
    port = args.port or _pick_free_port()
    metrics_port = _pick_free_port()
    max_num_seqs = _resolve_max_num_seqs(buckets, run.server_args)

    server_cmd = build_server_cmd(
        server_exe=server_exe,
        model_id=model_id,
        port=port,
        max_model_len=max_model_len,
        max_num_seqs=max_num_seqs,
        tensor_parallel_size=tp,
        metrics_port=metrics_port,
        prefix_cache_regime=getattr(args, "prefix_cache_regime", None),
        extra_args=run.server_args,
        model_runner=default_model_runner(),
    )
    # DP cohort: --data-parallel-size N means N processes, one per rank —
    # a lone rank 0 hangs at the DP rendezvous forever (see plan_dp_cohort).
    # Only rank 0 is health-checked, profiled and driven; secondaries are
    # supervised for lifecycle only.
    rank0_env = run.env
    dp_secondaries: List[ServerHandle] = []
    dp_plan = plan_dp_cohort(server_cmd=server_cmd, env=run.env, tp=tp)
    if dp_plan is not None:
        server_cmd, rank0_env, secondaries = dp_plan
        for rank, (sec_cmd, sec_env, sec_port) in enumerate(secondaries, start=1):
            dp_secondaries.append(
                ServerHandle(
                    cmd=sec_cmd,
                    env=sec_env,
                    port=sec_port,
                    log_path=logs_dir / f"{run.label}{suffix}.dp{rank}.server.log",
                    cwd=None,
                )
            )
        print(
            f"[dp] planned cohort of {len(dp_secondaries) + 1} processes "
            f"(one per --data-parallel-rank); rank 0 on port {port}"
        )
    nsys_report: Optional[Path] = None
    launch_cmd = list(server_cmd)
    if args.nsys_profile and nsys_dir is not None:
        nsys_report = nsys_dir / f"{run.label}{suffix}"
        launch_cmd = build_nsys_cmd(
            server_cmd,
            report_path=nsys_report,
            trace=args.nsys_trace,
            cuda_graph_trace=args.nsys_mode,
            extra_flags=shlex.split(args.nsys_extra_flags or ""),
        )

    _write_status(
        status_path,
        {
            "phase": "server_launch",
            "label": run.label,
            "launch_id": launch_id,
            "warmup_launch": is_warmup_launch,
            "port": port,
            "cmd": launch_cmd,
        },
    )
    print(f"\n=== {run.label} launch {launch_id} (port {port}) ===")
    print(_format_cmd_for_md(launch_cmd, run.env_overrides))

    server = ServerHandle(
        cmd=launch_cmd, env=rank0_env, port=port, log_path=server_log, cwd=None
    )
    results: Dict[str, Any] = {}
    profile_start: Optional[float] = None
    profile_stop: Optional[float] = None
    all_waves: List[WaveRecord] = []
    correctness_code = 0
    prefix_cache_counters: Optional[Dict[str, int]] = None
    bringup_retry: Optional[Dict[str, Any]] = None

    # Prompt shape needs only the tokenizer, so resolve it and validate every
    # bucket BEFORE paying for the server launch and the correctness phase.
    tokenizer = None
    corpus_tokens: List[int] = []
    shape: Optional[ChatTemplateShape] = None
    if args.prompt_source in ("gsm8k", "raw"):
        tokenizer = tokenizer_factory(model_id)
        corpus_tokens = load_timing_corpus_tokens(tokenizer)
        if args.prompt_source == "gsm8k":
            shape = measure_chat_template_shape(
                tokenizer, "Question: warmup\nAnswer: warmup"
            )
        check_buckets_fit_template(buckets, shape)

    for secondary in dp_secondaries:
        secondary.start()
    server.start()
    try:
        server.connect()
        wait_started = time.monotonic()

        def _on_wait(last: str) -> None:
            waited = time.monotonic() - wait_started
            if waited > 0 and int(waited) % max(1, args.heartbeat_s) < 2:
                print(f"[health] waiting {waited:.0f}s for {run.label} server ({last})")

        try:
            server.wait_healthy(timeout_s=args.health_timeout_s, on_wait=_on_wait)
        except ServerExitedBeforeHealthy:
            # Retry only the /psm_ attach race, only once, and only here:
            # no correctness phase or timed wave has started yet.
            if not is_psm_attach_race(server.read_log()):
                raise
            server.stop()
            for secondary in dp_secondaries:
                secondary.stop()
            # The log opens in append mode and per_arm_verify reads the whole
            # file, so move the failed launch aside before the relaunch.
            failed_log = logs_dir / f"{run.label}{suffix}.bringup-failed.server.log"
            server_log.replace(failed_log)
            bringup_retry = {
                "bringup_retries": 1,
                "bringup_retry_reason": PSM_ATTACH_RACE_REASON,
                "bringup_failed_server_log": str(failed_log),
            }
            print(
                f"[health] {run.label} server died at bring-up on the /psm_ "
                f"shared-memory race; relaunching once. Failed log: {failed_log}"
            )
            _write_status(
                status_path,
                {"phase": "server_relaunch", "label": run.label,
                 "launch_id": launch_id, "reason": PSM_ATTACH_RACE_REASON,
                 "failed_server_log": str(failed_log)},
            )
            for secondary in dp_secondaries:
                secondary.start()
            server.start()
            server.connect()
            wait_started = time.monotonic()
            try:
                server.wait_healthy(timeout_s=args.health_timeout_s, on_wait=_on_wait)
            except SystemExit as exc:
                raise SystemExit(
                    f"{exc} The first launch died on the /psm_ shared-memory "
                    f"race and one retry also failed. Server logs: first "
                    f"{failed_log}, retry {server_log}"
                ) from None
        print(f"[health] {run.label} server healthy after "
              f"{time.monotonic() - wait_started:.1f}s")

        # Correctness runs BEFORE the timing waves so a FAIL short-circuits
        # without paying for the whole sweep, and so the timing loop's warm
        # server state is not perturbed by a different sampling config.
        if run_correctness and (args.capture_golden_refs or args.verify_correctness):
            if tokenizer is None:
                tokenizer = tokenizer_factory(model_id)
            correctness_code = run_correctness_phase(
                server=server,
                tokenizer=tokenizer,
                json_dir=json_dir,
                num_questions=args.correctness_num_questions,
                tolerance_pct=args.correctness_tolerance_pct,
                capture_golden_refs=args.capture_golden_refs,
                verify_correctness=args.verify_correctness,
                request_timeout_s=args.request_timeout_s,
                max_tokens=args.correctness_max_tokens,
                # Cap at the server's --max-num-seqs: requests beyond it just
                # queue in the scheduler, and a target whose largest bucket is
                # small would otherwise sit at 32-deep queues that distort
                # per-request timeouts.
                concurrency=min(32, max_num_seqs),
            )
            _write_status(
                status_path,
                {"phase": "correctness_done", "label": run.label,
                 "launch_id": launch_id, "code": correctness_code},
            )

        if args.nsys_profile:
            profile_start = server.start_profile()
            print(f"[nsys] StartProfile at {profile_start:.3f}")

        for bucket in buckets:
            tag = _bucket_file_tag(bucket, all_buckets)
            bs = int(bucket["batch_size"])
            il = int(bucket["input_len"])
            ol = int(bucket["output_len"])
            bucket_log = logs_dir / f"{run.label}_{tag}.log"
            start_dt = _utc_now()
            status: Dict[str, Any] = {
                "ok": False,
                "returncode": 1,
                "label": run.label,
                "batch_size": bs,
                "input_len": il,
                "output_len": ol,
                "launch_id": launch_id,
                "start_time": start_dt.isoformat(),
                "nsys_profile": bool(args.nsys_profile),
                "port": port,
            }
            lines: List[str] = [
                f"=== pumice grpc bench ({run.label}) {tag} ===",
                _format_cmd_for_md(launch_cmd, run.env_overrides),
                f"input_len={il} output_len={ol} batch_size={bs} "
                f"num_iters={bucket['num_iters']} warmup_waves={args.warmup_waves}",
            ]
            _write_status(
                status_path,
                {"phase": "bucket_start", "label": run.label, "tag": tag,
                 "launch_id": launch_id, "batch_size": bs},
            )
            try:
                if args.prompt_source == "random":
                    prompts = build_random_prompt_ids(
                        input_len=il, output_len=ol, batch_size=bs
                    )
                else:
                    prompts = build_prompt_ids(
                        shape=shape,
                        corpus_tokens=corpus_tokens,
                        input_len=il,
                        output_len=ol,
                        batch_size=bs,
                    )

                deadline = time.monotonic() + float(args.timeout_s)
                waves: List[WaveRecord] = []
                itls: List[float] = []
                total_waves = args.warmup_waves + int(bucket["num_iters"])
                with ThreadPoolExecutor(max_workers=max(1, bs)) as pool:
                    for wave_idx in range(1, total_waves + 1):
                        if time.monotonic() > deadline:
                            raise TimeoutError(
                                f"bucket {tag} exceeded --timeout-s={args.timeout_s} "
                                f"after {wave_idx - 1} wave(s)"
                            )
                        warm = wave_idx <= args.warmup_waves
                        measured_idx = wave_idx - args.warmup_waves
                        profiled = bool(
                            args.nsys_profile and not warm and measured_idx <= args.profile_waves
                        )
                        req_results, latency, t0, t1 = drive_wave(
                            stub=server.stub,
                            pb2=server.pb2,
                            prompts=prompts,
                            output_len=ol,
                            temperature=args.temperature,
                            ignore_eos=True,
                            timeout_s=args.request_timeout_s,
                            wave_id=f"{run.label}-{tag}-l{launch_id}-w{wave_idx}",
                            executor=pool,
                        )
                        errors = [r for r in req_results if r.error]
                        if errors and not warm:
                            raise RuntimeError(
                                f"{len(errors)}/{len(req_results)} requests failed in "
                                f"wave {wave_idx}: {errors[0].error}"
                            )
                        record = WaveRecord(
                            index=wave_idx,
                            label=run.label,
                            tag=tag,
                            batch_size=bs,
                            input_len=il,
                            output_len=ol,
                            warmup=warm,
                            profiled=profiled,
                            start_unix=t0,
                            end_unix=t1,
                            latency_s=latency,
                            num_requests=len(req_results),
                            num_errors=len(errors),
                            prefill_mean_s=_mean(
                                [r.prefill_s for r in req_results if r.prefill_s is not None]
                            ),
                            decode_mean_s=_mean(
                                [r.decode_s for r in req_results if r.decode_s is not None]
                            ),
                            completion_tokens=sum(r.completion_tokens for r in req_results),
                        )
                        waves.append(record)
                        if not warm:
                            for r in req_results:
                                itls.extend(r.itls)
                        lines.append(
                            f"wave {wave_idx}/{total_waves} "
                            f"({'warmup' if warm else 'measured'}): {latency:.4f}s"
                        )
                        _write_status(
                            status_path,
                            {"phase": "benchmark", "label": run.label, "tag": tag,
                             "launch_id": launch_id, "wave": wave_idx,
                             "total_waves": total_waves, "latency_s": latency},
                        )

                server_log_text = server.read_log()
                spec_block = parse_emf_spec_metrics(server_log_text)
                raw = summarize_waves(waves, itls=itls, spec_decode=spec_block)
                pc_rate = parse_emf_prefix_cache_rate(server_log_text)
                if pc_rate is not None:
                    raw["prefix_cache_hit_rate_pct"] = pc_rate
                lines.append(f"Avg latency: {raw['avg_latency']} seconds")
                for key, value in sorted(raw["percentiles"].items(), key=lambda kv: int(kv[0])):
                    lines.append(f"{key}% percentile latency: {value} seconds")
                end_dt = _utc_now()
                status.update(
                    {
                        "ok": True,
                        "returncode": 0,
                        "end_time": end_dt.isoformat(),
                        "duration_s": (end_dt - start_dt).total_seconds(),
                    }
                )
                results[tag] = {"raw": raw, "status": status, "waves": waves}
                all_waves.extend(waves)
                _write_status(
                    status_path,
                    {"phase": "bucket_done", "label": run.label, "tag": tag,
                     "launch_id": launch_id, "ok": True},
                )
            except Exception as exc:
                end_dt = _utc_now()
                status.update(
                    {
                        "ok": False,
                        "returncode": 1,
                        "error": f"{type(exc).__name__}: {exc}",
                        "end_time": end_dt.isoformat(),
                        "duration_s": (end_dt - start_dt).total_seconds(),
                        "traceback": traceback.format_exc(),
                    }
                )
                lines.append(f"ERROR: {status['error']}")
                lines.append(status["traceback"])
                results[tag] = {"raw": {"error": status["error"]}, "status": status, "waves": []}
                _write_status(
                    status_path,
                    {"phase": "bucket_failed", "label": run.label, "tag": tag,
                     "launch_id": launch_id, "ok": False, "error": status["error"]},
                )
            finally:
                _write_text(bucket_log, "\n".join(lines) + "\n")
                # A SystemExit raised inside the try skips `except Exception`,
                # so results[tag] may be unset; a KeyError here would mask it.
                if tag in results:
                    _write_json(json_dir / f"{run.label}_{tag}.json", results[tag]["raw"])
                    _write_json(json_dir / f"{run.label}_{tag}.runner.json", results[tag]["status"])

        if args.nsys_profile:
            profile_stop = server.stop_profile()
            print(f"[nsys] StopProfile at {profile_stop:.3f}")

        # WS4.4 per-arm counter scrape. Must run BEFORE ``server.stop()`` — the
        # ``/metrics`` endpoint dies with the process. Never fails the launch:
        # ``verify_prefix_cache_regime`` treats ``None`` as evidence-of-absence.
        prefix_cache_counters = scrape_prefix_cache_counters(metrics_port)
    finally:
        server.stop()
        for secondary in dp_secondaries:
            secondary.stop()

    return {
        "buckets": results,
        "waves": all_waves,
        "server_cmd": launch_cmd,
        "server_start_unix": server.start_unix,
        "profile_start_unix": profile_start,
        "profile_stop_unix": profile_stop,
        "nsys_report": str(nsys_report) + ".nsys-rep" if nsys_report else None,
        "server_log": str(server_log),
        "correctness_code": correctness_code,
        "metrics_port": metrics_port,
        "prefix_cache_counters": prefix_cache_counters,
        "bringup_retry": bringup_retry,
    }


def run_correctness_phase(
    *,
    server: ServerHandle,
    tokenizer,
    json_dir: Path,
    num_questions: int,
    tolerance_pct: float,
    capture_golden_refs: bool,
    verify_correctness: bool,
    request_timeout_s: float,
    max_tokens: int = 1024,
    concurrency: int = 32,
    log: Callable[[str], None] = print,
) -> int:
    """Gate 5.1b: greedy GSM8K decode over gRPC, then capture or compare.

    Stage 1 (``capture_golden_refs``) writes ``json/golden_refs.json`` with the
    baseline predictions + labels. Stage 5 (``verify_correctness``) reads those
    refs, writes ``json/opt_outputs.json`` and ``json/correctness_verdict.json``,
    and returns the exit code ``_verdict_to_code`` derives.

    Greedy means ``temperature=0`` and ``ignore_eos=False`` — unlike the timing
    loop, the model must be allowed to stop, otherwise every completion runs to
    ``max_tokens`` and the answer is buried in padding.

    Returns 0 on PASS / capture-only, 3 on FAIL, 4 on infrastructure error.
    """
    if not (capture_golden_refs or verify_correctness):
        return 0
    json_dir.mkdir(parents=True, exist_ok=True)
    prompts, labels = _build_gsm8k_prompts(num_questions=num_questions)
    log(f"[correctness] GSM8K greedy decode: {len(prompts)} question(s)")

    prompt_ids = [
        [int(x) for x in tokenizer.encode(p, add_special_tokens=True)] for p in prompts
    ]
    started = time.time()
    results: List[RequestResult] = []
    texts: List[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(prompt_ids)))) as pool:
        futures = [
            pool.submit(
                _drive_one_request_capture,
                stub=server.stub,
                pb2=server.pb2,
                request_id=f"correctness-{i}",
                prompt_ids=ids,
                max_tokens=max_tokens,
                timeout_s=request_timeout_s,
            )
            for i, ids in enumerate(prompt_ids)
        ]
        for future in futures:
            result, token_ids = future.result()
            results.append(result)
            texts.append(tokenizer.decode(token_ids, skip_special_tokens=True))
    duration = time.time() - started
    log(f"[correctness] Generation done in {duration:.1f}s")

    errored = [r for r in results if r.error]
    if errored:
        log(f"[correctness] ERROR: {len(errored)} request(s) failed: {errored[0].error}")
        return 4

    golden_path = json_dir / "golden_refs.json"
    if capture_golden_refs:
        stops: List[str] = list(_GSM8K_STOP_STRINGS)
    else:
        # Score exactly as the golden refs were scored. Refs captured before
        # stop-string truncation have no "stop" key and mean "no truncation".
        prior_meta = (_read_json_or_empty(golden_path).get("metadata") or {}) if golden_path.exists() else {}
        stops = [str(x) for x in (prior_meta.get("stop") or [])]
        if golden_path.exists() and "stop" not in prior_meta:
            log(
                "[correctness] NOTE: golden refs have no 'stop' list (captured "
                "before stop-string truncation); scoring without truncation to match."
            )
    texts = [_truncate_at_stop(text, stops) for text in texts]

    serialized = [
        {
            "prompt_index": i,
            "token_ids": [
                int(x) for x in tokenizer.encode(text, add_special_tokens=False)
            ],
            "text": text,
            "logprobs": [],
            "num_tokens": results[i].completion_tokens,
        }
        for i, text in enumerate(texts)
    ]
    preds, accuracy = _score_gsm8k_predictions(texts, labels)

    if capture_golden_refs:
        _write_json(
            golden_path,
            {
                "metadata": {
                    "num_questions": len(prompts),
                    "num_shots": 5,
                    "max_tokens": max_tokens,
                    "stop": stops,
                    "gsm8k_accuracy": round(accuracy, 4),
                    "capture_duration_s": round(duration, 2),
                    "baseline_preds": preds,
                    "labels": labels,
                    "tolerance_pct": float(tolerance_pct),
                    "engine": "pumice",
                },
                "outputs": serialized,
            },
        )
        log(f"[correctness] Golden refs saved to {golden_path} (accuracy={accuracy:.4f})")

    if not verify_correctness:
        return 0

    if not golden_path.exists():
        log(f"[correctness] ERROR: golden_refs.json not found at {golden_path}")
        return 4
    golden = _read_json_or_empty(golden_path)
    meta = golden.get("metadata") or {}
    golden_nq = meta.get("num_questions")
    if golden_nq is not None and golden_nq != num_questions:
        log(
            f"[correctness] ERROR: golden refs num_questions={golden_nq} != current "
            f"{num_questions}. Re-capture with matching --correctness-num-questions."
        )
        return 4
    golden_mt = meta.get("max_tokens")
    if golden_mt is not None and golden_mt != max_tokens:
        log(
            f"[correctness] ERROR: golden refs max_tokens={golden_mt} != current "
            f"{max_tokens}. Re-capture the golden refs."
        )
        return 4

    _write_json(json_dir / "opt_outputs.json", {"outputs": serialized})
    verdict = _compare_correctness(
        golden_refs=golden.get("outputs") or [],
        opt_outputs=serialized,
        labels=meta.get("labels"),
        baseline_preds=meta.get("baseline_preds"),
        opt_preds=preds,
        tolerance_pct=tolerance_pct,
    )
    verdict["duration_s"] = round(duration, 2)
    verdict_path = json_dir / "correctness_verdict.json"
    _write_json(verdict_path, verdict)
    log(f"[correctness] Verdict: {verdict['verdict']} -> {verdict_path}")
    if verdict.get("infrastructure_error"):
        log(f"[correctness] {verdict.get('infrastructure_message', 'Infrastructure error')}")
    return _verdict_to_code(verdict)


def _drive_one_request_capture(
    *,
    stub,
    pb2,
    request_id: str,
    prompt_ids: Sequence[int],
    max_tokens: int,
    timeout_s: float,
) -> Tuple[RequestResult, List[int]]:
    """Greedy single request that also returns the generated token IDs.

    Separate from :func:`_drive_one_request` because the timing loop deliberately
    discards output tokens (and forces ``ignore_eos=True`` to pin the decode
    length), whereas correctness needs the actual completion and must let the
    model stop at EOS.
    """
    request = pb2.GenerateRequest(
        request_id=request_id,
        tokenized=pb2.TokenizedInput(input_ids=list(prompt_ids)),
        sampling_params=pb2.SamplingParams(
            temperature=0.0, max_tokens=int(max_tokens), ignore_eos=False
        ),
        stream=True,
    )
    submit_t = time.perf_counter()
    first_token_t: Optional[float] = None
    token_ids: List[int] = []
    finish_reason = ""
    error: Optional[str] = None
    try:
        for response in stub.Generate(request, timeout=timeout_s):
            if response.HasField("chunk"):
                new_ids = list(response.chunk.token_ids)
                if new_ids and first_token_t is None:
                    first_token_t = time.perf_counter()
                token_ids.extend(int(x) for x in new_ids)
            elif response.HasField("complete"):
                finish_reason = response.complete.finish_reason
                if response.complete.output_ids:
                    token_ids = [int(x) for x in response.complete.output_ids]
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    complete_t = time.perf_counter()
    return (
        RequestResult(
            request_id=request_id,
            submit_t=submit_t,
            first_token_t=first_token_t,
            complete_t=complete_t,
            completion_tokens=len(token_ids),
            finish_reason=finish_reason,
            error=error,
        ),
        token_ids,
    )


def _import_baseline_artifacts(
    *,
    baseline_from: Path,
    baseline_label: str,
    tags: Sequence[str],
    json_dir: Path,
    logs_dir: Path,
    import_label: Optional[str] = None,
) -> int:
    """Copy an accepted Stage 1 baseline's per-bucket artifacts into this sweep.

    Fails loudly when a bucket the current sweep plans to report has no baseline
    counterpart — a silently-missing baseline would render the row's speedup null
    while still looking like a complete gate artifact.

    *import_label* is the filename prefix READ from ``baseline_from`` and defaults
    to *baseline_label*. ``--baseline-from-arm opt`` sets it to the opt label so a
    round >= 2 integration sweep compares against the promoted arm; the files are
    always WRITTEN under *baseline_label*, which is what results collection reads.
    """
    read_label = import_label or baseline_label
    src_json = baseline_from / "json"
    if not src_json.is_dir():
        raise SystemExit(
            f"--baseline-from has no json/ subdirectory: {baseline_from}. Expected a "
            f"Stage 1 output directory with json/{read_label}_<tag>.json files."
        )
    src_logs = baseline_from / "logs"
    imported = 0
    missing: List[str] = []
    for tag in tags:
        raw_src = src_json / f"{read_label}_{tag}.json"
        if raw_src.exists():
            shutil.copy2(raw_src, json_dir / f"{baseline_label}_{tag}.json")
            imported += 1
        else:
            missing.append(tag)
        runner_src = src_json / f"{read_label}_{tag}.runner.json"
        if runner_src.exists():
            shutil.copy2(runner_src, json_dir / f"{baseline_label}_{tag}.runner.json")
        log_src = src_logs / f"{read_label}_{tag}.log"
        if log_src.exists():
            shutil.copy2(log_src, logs_dir / f"{baseline_label}_{tag}.log")
    if missing:
        raise SystemExit(
            f"--baseline-from is missing baseline data for {len(missing)} bucket(s): "
            f"{missing}. Looked in {src_json} for {read_label}_<tag>.json files."
        )
    return imported


def _render_md(all_runs: Dict[str, Any], baseline_label: str, opt_label: str) -> str:
    """Render ``e2e_latency_results.md``: run provenance + the A/B table."""
    rows = all_runs.get("results") or []
    freshness = all_runs.get("engine_freshness") or {}
    workload = all_runs.get("workload") or {}
    lines: List[str] = [
        "# E2E Latency Sweep (Pumice gRPC)",
        "",
        f"Generated: {all_runs.get('generated_at')} (UTC)",
        "",
        f"- Model: {all_runs.get('model_id')}",
        f"- tp={all_runs.get('tp')} ep={all_runs.get('ep')} "
        f"max_model_len={all_runs.get('max_model_len')}",
        f"- Execution mode: {all_runs.get('execution_mode')} "
        f"(runner: {all_runs.get('runner')})",
        f"- Prompt source: {workload.get('prompt_source')} "
        f"temperature={workload.get('temperature')} "
        f"warmup_waves={workload.get('warmup_waves')}",
        f"- Engine freshness: {freshness.get('reason', 'unknown')} "
        f"(overridden={freshness.get('overridden', False)})",
        "",
    ]
    if freshness.get("overridden"):
        lines += [
            "> WARNING: the engine freshness gate was overridden with "
            "`--allow-stale-engine`. These numbers may describe a build that "
            "predates the current pumice-rust sources and are NOT valid gate "
            "evidence.",
            "",
        ]
    profile_meta = all_runs.get("profile_meta")
    if isinstance(profile_meta, dict):
        lines += [
            f"- nsys report: {profile_meta.get('nsys_report')}",
            f"- Profile window: {profile_meta.get('profile_window_s')}s over "
            f"{profile_meta.get('profiled_wave_count')} wave(s); "
            f"see `profile_meta.waves[]` in the JSON for wall-clock boundaries.",
            "",
        ]
    lines.append(_render_md_table(rows, baseline_label, opt_label))
    for warning in all_runs.get("warnings") or []:
        lines.append(f"> WARNING: {warning}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    gate_warnings = enforce_gate_slot_invocation(args)
    for warning in gate_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    artifact_dir = Path(args.artifact_dir).expanduser().resolve()
    if not artifact_dir.is_dir():
        raise SystemExit(f"--artifact-dir does not exist: {artifact_dir}")
    if args.baseline_from:
        baseline_dir = Path(args.baseline_from).expanduser().resolve()
        if not baseline_dir.is_dir():
            raise SystemExit(
                f"--baseline-from path does not exist or is not a directory: {baseline_dir}"
            )
    target_path = (
        Path(args.target_json).expanduser().resolve()
        if args.target_json
        else artifact_dir / "target.json"
    )
    tgt = parse_target_spec(
        _read_json(target_path),
        artifact_dir=artifact_dir,
        # A Stage 1 `--labels baseline` run never launches the opt arm, so the
        # scaffold's `<ENABLE_FLAG>` placeholder in bench.opt_env must not block it.
        require_opt_env=("opt" in args.selected_labels),
    )
    # WS4.4 pin: propagate the target-declared regime into args so every arm's
    # build_server_cmd() call adds the same --{no-}enable-prefix-caching flag.
    args.prefix_cache_regime = tgt.prefix_cache_regime
    args.prompt_source = resolve_prompt_source(args.prompt_source, tgt.prompt_source)
    if tgt.runner_deprecated:
        print(
            f"WARNING: bench.runner={LEGACY_RUNNER_NAME!r} is the deprecated vLLM-era "
            f"name. Rewrite target.json with runner={RUNNER_NAME!r} (and "
            "bench.server_cmd) — this compatibility path will be removed.",
            file=sys.stderr,
        )

    # Pumice's Rust loader takes a DIRECTORY, not an HF id (no startup
    # download); resolve HF ids against the local cache before any launch.
    model_id = _resolve_local_model_path(tgt.model_id)
    if model_id != tgt.model_id:
        print(f"[model] resolved {tgt.model_id!r} -> {model_id}")
    tp, ep, max_model_len = tgt.tp, tgt.ep, tgt.max_model_len
    buckets = [dict(b) for b in tgt.buckets]
    num_iters = tgt.num_iters
    baseline_label, opt_label = tgt.baseline_label, tgt.opt_label
    baseline_env, opt_env = tgt.baseline_env, tgt.opt_env
    # --draft-model takes the same directory contract as --model (spec decode
    # loads it through the same Rust path). MTP targets often carry the target
    # checkpoint's HF id here (same-checkpoint drafting). TargetSpec is frozen,
    # so rebuild it — build_run_specs reads tgt.extra_args downstream.
    draft = _flag_value(tgt.extra_args, "--draft-model")
    if draft:
        resolved_draft = _resolve_local_model_path(draft)
        if resolved_draft != draft:
            tgt = dataclasses.replace(
                tgt,
                extra_args=tuple(
                    _replace_flag_value(tgt.extra_args, "--draft-model", resolved_draft)
                ),
            )
            print(f"[model] resolved draft {draft!r} -> {resolved_draft}")
    extra_args = list(tgt.extra_args)
    baseline_extra_args = list(tgt.baseline_extra_args)
    opt_extra_args = list(tgt.opt_extra_args)
    workload = tgt.raw_workload

    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd()
    server_exe = resolve_server_exe(tgt.server_cmd_spec, repo_root)

    # ---- Freshness gate (decision D3/D5): never measure a stale engine. ----
    freshness = check_engine_freshness(repo_root)
    freshness_payload = freshness.to_dict()
    freshness_payload["overridden"] = bool(args.allow_stale_engine)
    freshness_payload["repo_root"] = str(repo_root)
    if not freshness.ok:
        if not args.allow_stale_engine:
            raise SystemExit(
                "REFUSING TO MEASURE: engine freshness check failed "
                f"({freshness.reason}).\n\n{freshness.detail}\n\n"
                "Pass --allow-stale-engine only when you accept that the numbers "
                "will not describe the current tree."
            )
        print(
            f"WARNING: engine freshness check failed ({freshness.reason}) but "
            "--allow-stale-engine was passed; results are NOT sound evidence.\n"
            f"{freshness.detail}",
            file=sys.stderr,
        )

    # Captured now, not at scaffold time: the worktree HEAD may have moved.
    stack_fingerprint = capture_stack_fingerprint(repo_root)

    # ---- Output layout ----
    resolved_out_name = _resolve_sweep_out_name(args, artifact_dir)
    out_root = _prepare_out_root(
        artifact_dir=artifact_dir, out_name=resolved_out_name, overwrite=args.overwrite
    )
    logs_dir = out_root / "logs"
    json_dir = out_root / "json"
    status_dir = out_root / "status"
    for directory in (logs_dir, json_dir, status_dir):
        directory.mkdir(parents=True, exist_ok=True)
    nsys_dir: Optional[Path] = None
    if args.nsys_profile:
        nsys_dir = out_root / "nsys"
        nsys_dir.mkdir(parents=True, exist_ok=True)
        print(
            "WARNING: nsys profiling adds overhead — latency results from this run "
            "should not be used for performance comparison.",
            file=sys.stderr,
        )

    sweep_cache_root: Optional[Path] = None
    if args.fresh_cache:
        sweep_cache_root = out_root / "cache" / _utc_now().strftime("%Y%m%dT%H%M%SZ")
        (sweep_cache_root / "triton_cache").mkdir(parents=True, exist_ok=True)
        (sweep_cache_root / "inductor_cache").mkdir(parents=True, exist_ok=True)
        print(f"[fresh-cache] isolating compile caches at {sweep_cache_root}")

    runs = build_run_specs(
        tgt,
        inherited_env=dict(os.environ),
        sweep_cache_root=sweep_cache_root,
        worktree_root=None,
    )

    all_runs = build_results_header(
        tgt,
        args=args,
        target_path=target_path,
        out_root=out_root,
        out_name=resolved_out_name,
        server_exe=server_exe,
        freshness_payload=freshness_payload,
    )
    all_runs["stack_fingerprint"] = stack_fingerprint
    # WS4.6 host/gpu/runner stamping. Derived once so every row + the header
    # carry the same identity — reports that quote a row alone can still tell
    # which host / GPU / runner produced the number.
    _accel = (os.environ.get("AMMO_ACCELERATOR_KIND") or "").strip().lower()
    _model_runner = "neuron" if _accel == "neuron" else "vllm"
    _host_stamp = _detect_host_stamp()
    all_runs.update(
        {
            "host": _host_stamp.get("host"),
            "gpu": _host_stamp.get("gpu"),
            "model_runner": _model_runner,
            "torch_version": _host_stamp.get("torch_version"),
            "vllm_version": None if _model_runner == "neuron" else _host_stamp.get("vllm_version"),
        }
    )
    _gpu = (_host_stamp.get("gpu") or "")
    if any(tag in _gpu for tag in _L40S_LABELS):
        all_runs["harness_label"] = _L40S_HARNESS_LABEL

    warnings: List[str] = list(gate_warnings)

    # --baseline-from imports by literal filename, so the comparator identity is
    # the filename prefix, not the directory. --baseline-from-arm selects which
    # arm of that directory is read; 'baseline' (default) keeps baseline_label.
    baseline_from_arm = getattr(args, "baseline_from_arm", "baseline") or "baseline"
    import_label = opt_label if baseline_from_arm == "opt" else baseline_label

    baseline_from: Optional[Path] = None
    if "opt" in args.selected_labels and "baseline" not in args.selected_labels:
        if args.baseline_from:
            baseline_from = Path(args.baseline_from).expanduser().resolve()
            all_runs["baseline_source"] = str(baseline_from)
            if baseline_from_arm != "baseline":
                # Additive and default-absent: a non-default comparator arm must
                # be visible in the artifact, not only in the run log.
                all_runs["baseline_source_arm"] = baseline_from_arm
            imported = _import_baseline_artifacts(
                baseline_from=baseline_from,
                baseline_label=baseline_label,
                tags=[_bucket_file_tag(b, buckets) for b in buckets],
                json_dir=json_dir,
                logs_dir=logs_dir,
                import_label=import_label,
            )
            print(
                f"Imported {imported} baseline artifact(s) from {baseline_from} "
                f"(--baseline-from-arm {baseline_from_arm}, filename prefix "
                f"{import_label!r})"
            )
            # Non-default comparator arm disclosure. The imported rows land under
            # baseline_label, so the .md table shows an ordinary "baseline" column
            # and hides the arm identity. An auditor who reads only the .md must
            # still see which arm became the comparator.
            if baseline_from_arm != "baseline":
                msg = (
                    f"comparator imported from arm {baseline_from_arm!r} of "
                    f"{baseline_from} (--baseline-from-arm); the "
                    f"{baseline_label!r} column holds that arm's numbers, read by "
                    f"filename prefix {import_label!r}."
                )
                warnings.append(msg)
                print("WARNING: " + msg, file=sys.stderr)
        else:
            all_runs["baseline_source"] = "none"
            warnings.append(
                "No baseline reference provided (--baseline-from not set); "
                "speedup calculations unavailable."
            )
            print(
                "WARNING: --labels opt without --baseline-from; baseline data will be "
                "null in results. Pass --baseline-from <stage1_dir>.",
                file=sys.stderr,
            )

    # A non-default comparator arm acts only inside the import path above, which
    # needs --baseline-from together with --labels opt and no baseline label.
    # Every other invocation drops the flag, so disclose the drop instead of
    # letting the caller believe the promoted arm was the comparator.
    if baseline_from_arm != "baseline" and baseline_from is None:
        msg = (
            f"--baseline-from-arm {baseline_from_arm} was IGNORED: the flag only "
            "selects which arm of --baseline-from is imported, and this run "
            "imports no comparator (import requires --baseline-from with "
            "--labels opt and no baseline label). The comparator arm is "
            "unchanged."
        )
        warnings.append(msg)
        print("WARNING: " + msg, file=sys.stderr)

    if args.verify_correctness and baseline_from is not None:
        golden_src = baseline_from / "json" / "golden_refs.json"
        if not golden_src.exists():
            raise SystemExit(
                f"--verify-correctness requires golden_refs.json but {golden_src} "
                "does not exist. Capture it in Stage 1 with --capture-golden-refs."
            )
        shutil.copy2(golden_src, json_dir / "golden_refs.json")
        print(f"Imported golden refs from {golden_src}")

    # ---- Execute: label x launch x bucket ----
    total_launches = max(1, int(args.num_launches))
    per_label_launches: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    launch_meta: Dict[str, List[Dict[str, Any]]] = {}
    # WS4.4 pin-then-verify state, keyed by label_key ("baseline"|"opt"). Each
    # arm records the last MEASURED launch's argv, server-log text and scraped
    # counters — the last measured launch is the one the row's numbers came
    # from, so that is the launch the regime verification must examine.
    per_arm_verify: Dict[str, Dict[str, Any]] = {}
    correctness_code = 0
    # The correctness phase runs exactly once, on the first MEASURED launch of
    # the last selected label — capture belongs to the baseline arm (Stage 1) and
    # verification to the opt arm (Stage 5), which is the label being run.
    correctness_label = args.selected_labels[-1]
    for label_key in args.selected_labels:
        run = runs[label_key]
        for launch_id in range(1, total_launches + 1):
            is_warmup_launch = total_launches >= 2 and launch_id == 1
            if total_launches >= 2:
                print(
                    f"\n=== Launch {launch_id}/{total_launches} for {run.label} "
                    f"({'WARMUP (discarded)' if is_warmup_launch else 'MEASURED'}) ==="
                )
            outcome = run_label_launch(
                run=run,
                launch_id=launch_id,
                is_warmup_launch=is_warmup_launch,
                buckets=buckets,
                all_buckets=buckets,
                model_id=model_id,
                tp=tp,
                max_model_len=max_model_len,
                server_exe=server_exe,
                args=args,
                out_root=out_root,
                logs_dir=logs_dir,
                json_dir=json_dir,
                status_dir=status_dir,
                nsys_dir=nsys_dir,
                run_correctness=(
                    label_key == correctness_label and not is_warmup_launch
                    and launch_id == (2 if total_launches >= 2 else 1)
                ),
            )
            correctness_code = max(correctness_code, int(outcome.get("correctness_code", 0)))
            if not is_warmup_launch:
                server_log_path = Path(outcome["server_log"])
                try:
                    server_log_text = server_log_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    server_log_text = ""
                per_arm_verify[label_key] = {
                    "argv": list(outcome["server_cmd"]),
                    "server_log": server_log_text,
                    "counters": outcome.get("prefix_cache_counters"),
                    "metrics_port": outcome.get("metrics_port"),
                    "label": run.label,
                }
            launch_meta.setdefault(run.label, []).append(
                {
                    "launch_id": launch_id,
                    "warmup": is_warmup_launch,
                    "server_cmd": outcome["server_cmd"],
                    "server_log": str(Path(outcome["server_log"]).relative_to(out_root)),
                    "profile_meta": build_profile_meta(
                        waves=outcome["waves"],
                        nsys_report_rel=(
                            str(Path(outcome["nsys_report"]).relative_to(out_root))
                            if outcome["nsys_report"]
                            else None
                        ),
                        server_start_unix=outcome["server_start_unix"],
                        profile_start_unix=outcome["profile_start_unix"],
                        profile_stop_unix=outcome["profile_stop_unix"],
                    ),
                }
            )
            # A bring-up retry is recorded on this same entry, not as a new
            # one, so metas[-1] still names the healthy launch.
            retry = outcome.get("bringup_retry")
            if retry:
                launch_meta[run.label][-1].update(
                    {
                        **retry,
                        "bringup_failed_server_log": str(
                            Path(retry["bringup_failed_server_log"]).relative_to(out_root)
                        ),
                    }
                )
            label_map = per_label_launches.setdefault(run.label, {})
            for tag, payload in outcome["buckets"].items():
                raw = payload["raw"]
                label_map.setdefault(tag, []).append(
                    {
                        "launch_id": launch_id,
                        "warmup": is_warmup_launch,
                        "avg_latency": raw.get("avg_latency"),
                        "latencies": raw.get("latencies"),
                        "prefill_avg_s": raw.get("prefill_avg_s"),
                        "decode_avg_s": raw.get("decode_avg_s"),
                    }
                )
                if total_launches >= 2:
                    src = json_dir / f"{run.label}_{tag}.json"
                    if src.exists():
                        shutil.copy2(src, src.with_name(f"{src.stem}.launch{launch_id}.json"))

    # ---- Assemble rows ----
    rows: List[Dict[str, Any]] = []
    for bucket in buckets:
        tag = _bucket_file_tag(bucket, buckets)
        bs, il, ol = (
            int(bucket["batch_size"]),
            int(bucket["input_len"]),
            int(bucket["output_len"]),
        )
        row: Dict[str, Any] = {"batch_size": bs, "input_len": il, "output_len": ol}
        _stamp_row_environment(row, _host_stamp, _model_runner)
        entries: Dict[str, Dict[str, Any]] = {}
        raws: Dict[str, Dict[str, Any]] = {}
        for label_key, label in (("baseline", baseline_label), ("opt", opt_label)):
            raw_path = json_dir / f"{label}_{tag}.json"
            runner_path = json_dir / f"{label}_{tag}.runner.json"
            log_path = logs_dir / f"{label}_{tag}.log"
            raw = _read_json_or_empty(raw_path)
            raws[label_key] = raw
            status = _read_json_or_empty(runner_path) or {"ok": None}
            log_text = (
                log_path.read_text(encoding="utf-8", errors="replace")
                if log_path.exists()
                else ""
            )
            run = runs[label_key]
            evidence = _check_patterns(log_text, run.require_patterns, run.forbid_patterns)
            configured = bool(run.require_patterns or run.forbid_patterns)
            launches = per_label_launches.get(label, {}).get(tag) if total_launches >= 2 else None
            aggregate = _aggregate_launches(launches) if launches else None
            entry = _build_label_result_entry(
                cmd=(launch_meta.get(label) or [{}])[-1].get("server_cmd", []),
                env_overrides=run.env_overrides,
                metrics=_metrics_from_bench_json(raw),
                log_rel=str(log_path.relative_to(out_root)),
                output_json_rel=str(raw_path.relative_to(out_root)),
                runner_json_rel=str(runner_path.relative_to(out_root)),
                ok=status.get("ok"),
                returncode=0 if status.get("ok") else 1,
                evidence_status=_status_from_evidence(evidence, configured),
                evidence=evidence,
                timing={
                    "start_time": status.get("start_time"),
                    "end_time": status.get("end_time"),
                    "duration_s": status.get("duration_s"),
                },
                launches=launches,
                aggregate=aggregate,
                spec_decode=raw.get("spec_decode") if isinstance(raw, dict) else None,
            )
            entries[label_key] = entry
            row[label] = entry

        finalize_row(
            row,
            baseline_entry=entries["baseline"],
            opt_entry=entries["opt"],
            baseline_raw=raws["baseline"],
            opt_raw=raws["opt"],
            output_len=ol,
            batch_size=bs,
        )
        rows.append(row)

        if args.require_fastpath:
            opt_evidence = entries["opt"].get("fastpath_evidence") or {}
            if runs["opt"].require_patterns and not opt_evidence.get("ok"):
                raise SystemExit(
                    f"Fast-path evidence FAILED for opt at {tag}. "
                    f"Missing={opt_evidence.get('require_miss')}, "
                    f"forbidden_hits={opt_evidence.get('forbid_hits')}. "
                    f"See {logs_dir / f'{opt_label}_{tag}.log'}"
                )

    # WS4.4 pin-then-verify. Run every time both arms produced a measured
    # launch — a single-arm sweep (e.g. --labels opt with imported baseline)
    # cannot check regime parity because the imported side did not record
    # counters. Fail closed when any check trips.
    if {"baseline", "opt"}.issubset(set(per_arm_verify)):
        regime_report = verify_prefix_cache_regime(
            baseline_argv=per_arm_verify["baseline"]["argv"],
            opt_argv=per_arm_verify["opt"]["argv"],
            baseline_log=per_arm_verify["baseline"]["server_log"],
            opt_log=per_arm_verify["opt"]["server_log"],
            pinned_regime=tgt.prefix_cache_regime,
            baseline_counters=per_arm_verify["baseline"]["counters"],
            opt_counters=per_arm_verify["opt"]["counters"],
        )
        all_runs["prefix_cache_regime"] = regime_report
        if regime_report["failures"]:
            joined = "; ".join(regime_report["failures"])
            print(
                "ERROR: prefix-cache regime verification failed: " + joined,
                file=sys.stderr,
            )
            all_runs["results"] = rows
            all_runs["launches"] = launch_meta
            _write_json(out_root / "e2e_latency_results.json", all_runs)
            _write_text(
                out_root / "e2e_latency_results.md",
                _render_md(all_runs, baseline_label, opt_label),
            )
            raise SystemExit(
                "REFUSING TO CERTIFY — prefix-cache regime verification failed. "
                f"{joined}. See {out_root / 'e2e_latency_results.json'}."
            )
    elif tgt.prefix_cache_regime is not None:
        # Single-arm sweep with a pinned regime still runs the argv + log
        # checks on whichever arm ran, so an unpinned launch is caught early.
        for label_key, arm in per_arm_verify.items():
            report = verify_prefix_cache_regime(
                baseline_argv=arm["argv"],
                opt_argv=arm["argv"],
                baseline_log=arm["server_log"],
                opt_log=arm["server_log"],
                pinned_regime=tgt.prefix_cache_regime,
                baseline_counters=arm["counters"],
                opt_counters=arm["counters"],
            )
            all_runs.setdefault("prefix_cache_regime", {})[label_key] = report

    all_runs["results"] = rows
    all_runs["launches"] = launch_meta
    if launch_meta:
        # Promote the last launch's capture identity to the top level so the
        # profiling consumer does not have to walk per-label launch lists.
        last_label = args.selected_labels[-1]
        label = runs[last_label].label
        metas = launch_meta.get(label) or []
        if metas:
            all_runs["profile_meta"] = metas[-1]["profile_meta"]
    if warnings:
        all_runs["warnings"] = warnings

    _write_json(out_root / "e2e_latency_results.json", all_runs)
    _write_text(out_root / "e2e_latency_results.md", _render_md(all_runs, baseline_label, opt_label))

    if sweep_cache_root is not None and sweep_cache_root.exists():
        shutil.rmtree(sweep_cache_root.parent, ignore_errors=True)

    print(f"\nWrote: {out_root / 'e2e_latency_results.json'}")
    print(f"Wrote: {out_root / 'e2e_latency_results.md'}")

    # Only the arms this invocation actually executed can fail here; an
    # unrun arm legitimately has ok=None (e.g. --labels opt without a baseline).
    executed_labels = {runs[key].label for key in args.selected_labels}
    if baseline_from is not None:
        executed_labels.add(baseline_label)
    failed = [
        (row.get("batch_size"), label)
        for row in rows
        for label in sorted(executed_labels)
        if isinstance(row.get(label), dict) and row[label].get("ok") is not True
    ]
    if failed:
        print(f"ERROR: {len(failed)} bucket(s) did not succeed: {failed}", file=sys.stderr)
        return 2
    if correctness_code != 0:
        # 3 = accuracy FAIL, 4 = infrastructure error. Propagated verbatim so the
        # caller can distinguish "the optimization broke the model" from "the
        # correctness harness could not run at all".
        print(
            f"ERROR: correctness phase returned {correctness_code} "
            f"(see {json_dir / 'correctness_verdict.json'})",
            file=sys.stderr,
        )
        return correctness_code
    if getattr(args, "slot", None) == "baseline" and "baseline" in args.selected_labels:
        message = record_baseline_stack_fingerprint(artifact_dir, stack_fingerprint)
        if message:
            print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
