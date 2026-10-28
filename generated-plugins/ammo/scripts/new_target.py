#!/usr/bin/env python3
"""ammo v2: scaffold a new campaign workspace.

Run it inside the engine checkout (the Pumice repo). It creates:
  - <engine>/.ammo/<campaign-id>/state.json (simplified v2 schema)
  - <engine>/.ammo/<campaign-id>/target.json (bench input plus the engine
    contract that scripts/campaign_paths.py reads)
  - <engine>/.ammo/<campaign-id>/rounds/1/ scaffold
  - <engine>/.ammo/active, one line: the campaign id
  - one `.ammo/` line in the engine's .git/info/exclude

The campaign id is YYYYMMDD-HHMMSS-<6 hex> (UTC) unless --campaign-id names it.
The engine root is --engine-root, else `git rev-parse --show-toplevel` of the
current dir. --artifact-dir names the campaign dir directly; a dir of the form
<engine>/.ammo/<id> also gets the marker and the exclude line.

Safety: refuses to overwrite existing files unless --force is provided.

Example:
  scripts/ammo new_target.py \\
    --model-id Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8 \\
    --hardware L40S --dtype fp8 --tp 1 --ep 1
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import campaign_paths
import runtime_ops
from campaign_paths import plugin_path
from runtime_ops import (
    PUMICE_CUDA_PROFILE,
    PUMICE_NEURON_PROFILE,
    campaign_runtime_metadata,
    resolve_runtime,
)


PLACEHOLDER = "<FILL_ME>"
# Largest kv_segment_size the Pumice Neuron segmented-attention kernel compiles.
# The Neuron runner sets kv_segment_size to max_num_batched_tokens, which
# defaults to max_model_len, so a longer context needs an explicit budget.
NEURON_MAX_KV_SEGMENT = 4096
# Pumice Neuron needs the Rust scheduler to emit the full block table on every
# step. Unset, a steady-state decode step hands the kernel all-empty rows: DGE
# out-of-bounds (nrta 1006) or garbage logits. Pumice's Neuron e2e smoke
# conftest sets it to "1" unconditionally.
NEURON_FULL_BLOCK_TABLES_ENV = "PUMICE_RUST_EMIT_FULL_BLOCK_TABLES"


def _detect_gpu_model() -> str | None:
    """Return the first GPU's name from nvidia-smi, or None when unavailable.

    nvidia-smi ignores CUDA_VISIBLE_DEVICES, so on a heterogeneous host the
    unscoped query can name a GPU this session does not own. Scope the query
    with -i to the session's visible devices; a bad index fails closed to the
    placeholder instead of a confidently-wrong model name.
    """
    cmd = ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"]
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if cvd and cvd != "-1":
        cmd += ["-i", cvd]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


@dataclass(frozen=True)
class TargetFields:
    model_id: str
    hardware: str
    dtype: str
    tp: int
    ep: int
    max_model_len: int
    input_len: int
    output_len: int
    batch_sizes: List[int]
    num_iters: int
    noise_tolerance_pct: float
    catastrophic_regression_pct: float
    min_e2e_improvement_pct: float = 0.5
    data_parallel_size: int = 1
    enable_expert_parallel: bool = False
    max_num_seqs: int | None = None
    max_num_batched_tokens: int | None = None
    prompt_source: str | None = None
    baseline_env: Dict[str, str] | None = None
    workload_matrix: List[Dict[str, int]] | None = None
    max_model_len_auto_requested: bool = False
    prefix_cache_regime: str | None = None
    correctness_sample_count: int | None = None


def _cached_model_max_len(model_id: str | None) -> int | None:
    """Read max_position_embeddings from the local HF cache, if present."""
    if not model_id or "/" not in model_id:
        return None
    hub = Path(
        os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")
    ) / "hub" / ("models--" + model_id.replace("/", "--"))
    for config in sorted(hub.glob("snapshots/*/config.json")):
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        limit = data.get("max_position_embeddings")
        if isinstance(limit, int) and limit > 0:
            return limit
    return None


def _workload_len_cap(
    input_len: int, output_len: int, workload_matrix: List[Dict[str, int]] | None
) -> int:
    """Smallest power of two covering the longest workload sequence, floor 4096.

    Rationale for capping ``auto`` (found live on DeepSeek-V4-Flash): modern
    checkpoints advertise million-token ``max_position_embeddings``, and
    engine-side per-context reservations scale with ``max_model_len``. At 1M
    the DSA transient peak reservation alone
    (``max_num_batched_tokens x total_kv x 4B``,
    warmup.py reserve_dsa_peak_for_profiling) is about 1 TiB and every rank
    OOMs at init. A kernel-optimization target freezes its ISL/OSL matrix up
    front, so the bench never uses more context than max(ISL+OSL); a power
    of two above that (>=2x headroom at the boundary, covering
    chat-template overhead too) keeps KV sizing realistic without
    provisioning a context the workload cannot reach.
    """
    pairs = workload_matrix or [{"input_len": input_len, "output_len": output_len}]
    need = max(int(p["input_len"]) + int(p["output_len"]) for p in pairs)
    cap = 4096
    while cap < need:
        cap *= 2
    return cap


def _resolve_max_model_len(
    value: str | int,
    model_id: str | None = None,
    *,
    workload_cap: int | None = None,
) -> int:
    """Resolve an explicit positive integer; ``auto`` reads the cached model
    config, capped to *workload_cap* (see :func:`_workload_len_cap`)."""
    raw = str(value).strip()
    if raw.lower() == "auto":
        resolved = _cached_model_max_len(model_id)
        if resolved is None:
            raise SystemExit(
                "--max-model-len auto could not be resolved: no cached config.json "
                f"with max_position_embeddings for {model_id!r}; "
                "supply the concrete model limit as an integer."
            )
        if workload_cap is not None and resolved > workload_cap:
            print(
                f"[max-model-len] auto: model limit {resolved} capped to "
                f"{workload_cap} (smallest power of two covering the frozen "
                "workload; pass an explicit --max-model-len to override)",
                file=sys.stderr,
            )
            resolved = workload_cap
        return resolved
    try:
        resolved = int(raw)
    except ValueError as exc:
        raise SystemExit("--max-model-len must be a positive integer or 'auto'") from exc
    if resolved <= 0:
        raise SystemExit("--max-model-len must be positive")
    return resolved


def _parse_workload_matrix(spec: str | None, batch_sizes: List[int]) -> List[Dict[str, int]] | None:
    """Expand ``ISL:OSL`` pairs across the supplied batch-size buckets."""
    if not spec:
        return None
    pairs: List[tuple[int, int]] = []
    for token in spec.split(","):
        try:
            isl_raw, osl_raw = token.strip().split(":", 1)
            isl, osl = int(isl_raw), int(osl_raw)
        except (TypeError, ValueError) as exc:
            raise SystemExit("--isl-osl must be comma-separated positive ISL:OSL pairs") from exc
        if isl <= 0 or osl <= 0:
            raise SystemExit("--isl-osl values must be positive")
        pairs.append((isl, osl))
    return [
        {"input_len": isl, "output_len": osl, "batch_size": bs}
        for isl, osl in pairs
        for bs in batch_sizes
    ]


def _write_text(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path} (use --force)")
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing file: {path} (use --force)")
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _neuron_baseline_env(environ: Dict[str, str]) -> Dict[str, str]:
    """Return the pumice-neuron bench.baseline_env scaffold.

    A value already set in the scaffold's environment wins, like the
    setdefault in Pumice's Neuron conftest. Any value other than "1" gets a
    WARNING, because the bench runner strips inherited PUMICE_* vars and only
    baseline_env reaches the server.
    """
    value = environ.get(NEURON_FULL_BLOCK_TABLES_ENV)
    if value is None:
        print(
            f"pumice-neuron: set {NEURON_FULL_BLOCK_TABLES_ENV}=1 in "
            "bench.baseline_env (it reaches the baseline arm and every opt arm).",
            file=sys.stderr,
        )
        return {NEURON_FULL_BLOCK_TABLES_ENV: "1"}
    if value != "1":
        print(
            f"WARNING: pumice-neuron: kept the caller's "
            f"{NEURON_FULL_BLOCK_TABLES_ENV}={value!r} in bench.baseline_env. "
            "Neuron needs \"1\": without it the engine hands the kernel an "
            "all-empty block table on steady-state decode steps, and the first "
            "measured wave crashes (DGE out-of-bounds, nrta 1006) or returns "
            "garbage logits. Set it to \"1\" in target.json before the baseline.",
            file=sys.stderr,
        )
    return {NEURON_FULL_BLOCK_TABLES_ENV: value}


def _default_target_fields(args: argparse.Namespace) -> TargetFields:
    model_id = args.model_id or PLACEHOLDER
    hardware = args.hardware or PLACEHOLDER
    dtype = args.dtype or PLACEHOLDER

    workload_matrix = _parse_workload_matrix(
        getattr(args, "isl_osl", None), args.batch_sizes
    )
    return TargetFields(
        model_id=model_id,
        hardware=hardware,
        dtype=dtype,
        tp=args.tp,
        ep=args.ep,
        max_model_len=_resolve_max_model_len(
            args.max_model_len,
            args.model_id,
            workload_cap=_workload_len_cap(
                args.input_len, args.output_len, workload_matrix
            ),
        ),
        input_len=args.input_len,
        output_len=args.output_len,
        batch_sizes=args.batch_sizes,
        num_iters=args.num_iters,
        noise_tolerance_pct=args.noise_tolerance_pct,
        catastrophic_regression_pct=args.catastrophic_regression_pct,
        min_e2e_improvement_pct=getattr(args, "min_e2e_improvement", None)
        if getattr(args, "min_e2e_improvement", None) is not None
        else _schema_default("min_e2e_improvement_pct", 0.5),
        data_parallel_size=getattr(args, "data_parallel_size", 1),
        enable_expert_parallel=getattr(args, "enable_expert_parallel", False),
        max_num_seqs=getattr(args, "max_num_seqs", None),
        max_num_batched_tokens=getattr(args, "max_num_batched_tokens", None),
        prompt_source=getattr(args, "prompt_source", None),
        workload_matrix=workload_matrix,
        max_model_len_auto_requested=(
            str(args.max_model_len).strip().lower() == "auto"
        ),
        prefix_cache_regime=getattr(args, "prefix_cache_regime", None),
        correctness_sample_count=getattr(args, "correctness_sample_count", None),
    )


def _constraints_md(fields: TargetFields) -> str:
    return f"""# Constraints (Stage 1)

## Target envelope

- Model: {fields.model_id}
- Hardware: {fields.hardware}
- Dtype / quant format: {fields.dtype}
- TP / EP: tp={fields.tp}, ep={fields.ep}
- Data Parallel / Expert Parallel: dp={fields.data_parallel_size}, enable_expert_parallel={fields.enable_expert_parallel}
- Max model len: {fields.max_model_len}
- Decode buckets (batch sizes): {fields.batch_sizes}
- E2E workload: input_len={fields.input_len}, output_len={fields.output_len}

## TODOs (required before Stage 2)

- [ ] Read vLLM source for target component, document forward path and correctness invariants
- [ ] Capture baseline truth snapshot under production parity (CUDA graphs / torch.compile)
- [ ] Record baseline kernel timings from nsys profiling

"""

_SCHEMA_PATH = plugin_path("schemas", "state.schema.json")


def _schema_default(path: str, fallback: float) -> float:
    """Read a default value from state.schema.json at a dotted `campaign.config.*` path.

    Keeping the defaults in the schema lets us change them once and have writers
    pick them up automatically — no per-call-site drift.
    """
    try:
        schema = json.loads(_SCHEMA_PATH.read_text())
        props = schema["properties"]["campaign"]["properties"]["config"]["properties"]
        return float(props[path]["default"])
    except (FileNotFoundError, KeyError, json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _state_json(fields: TargetFields, artifact_dir: Path, min_e2e_improvement_pct: float | None = None,
                noise_tolerance_pct: float | None = None,
                catastrophic_regression_pct: float | None = None,
                gpu_model: str | None = None) -> Dict[str, Any]:
    # TargetFields is the programmatic contract. CLI defaults are populated
    # from the schema before this object is built.
    if min_e2e_improvement_pct is None:
        min_e2e_improvement_pct = fields.min_e2e_improvement_pct
    if noise_tolerance_pct is None:
        noise_tolerance_pct = fields.noise_tolerance_pct
    if catastrophic_regression_pct is None:
        catastrophic_regression_pct = fields.catastrophic_regression_pct
    try:
        runtime_metadata = campaign_runtime_metadata()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    state = {
        "target": {
            "model_id": fields.model_id,
            "hardware": fields.hardware,
            "dtype": fields.dtype,
            "tp": fields.tp,
            "dp": fields.data_parallel_size,
            "ep": fields.ep,
            "component": "auto",
        },
        "session_id": None,
        **runtime_metadata,
        "campaign": {
            "schema_version": "4.2",
            "status": "active",
            "current_round": 1,
            "current_stage": "1_baseline",
            "config": {
                "min_e2e_improvement_pct": min_e2e_improvement_pct,
                "noise_tolerance_pct": noise_tolerance_pct,
                "catastrophic_regression_pct": catastrophic_regression_pct,
            },
            "shipped_optimizations": [],
            "agent_costs": [],
            "rounds": [
                {
                    "round_id": 1,
                    "status": "IN_PROGRESS",
                    "team_name": None,
                    "profiling_baseline_path": None,
                    "baseline": {
                        "started_at": None,
                        "completed_at": None,
                        "e2e_latency": None,
                        "per_bs_verdict": None,
                    },
                    "bottleneck_mining": {
                        "started_at": None,
                        "completed_at": None,
                        "top_bottleneck_share_pct": None,
                    },
                    "debate": {
                        "started_at": None,
                        "completed_at": None,
                        "candidates": [],
                        "rounds_completed": 0,
                        "max_rounds": 4,
                        "selected_winners": [],
                    },
                    "parallel_tracks": {
                        "started_at": None,
                        "completed_at": None,
                        "tracks": {},
                    },
                    "integration": {
                        "started_at": None,
                        "completed_at": None,
                        "status": "pending",
                        "passing_candidates": [],
                        "failed_candidates": [],
                        "selected_candidates": [],
                        "conflict_analysis": None,
                        "combined_patch_branch": None,
                        "combined_e2e_result": None,
                        "e2e_latency_combined": None,
                        "per_bs_verdict": None,
                        "commit_sha": None,
                        "final_decision": None,
                        "resolver_invoked": None,
                        "resolver_outcome": None,
                        "conflicting_tracks": None,
                    },
                    "campaign_eval": {
                        "started_at": None,
                        "completed_at": None,
                    },
                    "audit": {},
                    "shipped": [],
                    "dropped": [],
                    "cumulative_speedup_after": None,
                    "combined_e2e_speedup_x": None,
                    "combined_e2e_delta_pp": None,
                    "note": None,
                    "round_summary": None,
                }
            ],
        },
    }
    if runtime_metadata["runtime"]["accelerator_kind"] == "cuda":
        resources = runtime_metadata["accelerator_resources"]
        state["gpu_resources"] = {
            "gpu_count": resources["resource_count"],
            "gpu_model": gpu_model if gpu_model else PLACEHOLDER,
            "memory_total_gib": 0,
            "cuda_visible_devices": resources["visible_resources"],
        }
    return state


def _compose_extra_args(
    fields: TargetFields, runtime_profile: str | None = None
) -> List[str]:
    """Compose bench.extra_args from DP / EP fields.

    On the vllm-* runner, DP > 1 also injects --distributed-executor-backend
    external_launcher, which vLLM requires for data-parallel execution via
    torchrun. The sweep script validates that no conflicting backend value
    was appended post-hoc (e.g., via frontend additionalFlags).

    pumice-* profiles never get that flag: neither Pumice server parser
    (rust_grpc_server.py, pumice-server) knows it, so the server stops at
    launch. The gRPC bench runner starts one server process per DP rank.
    """
    pumice = bool(runtime_profile and runtime_profile.startswith("pumice-"))
    extra: List[str] = []
    if fields.data_parallel_size > 1:
        extra.extend(["--data-parallel-size", str(fields.data_parallel_size)])
        if not pumice:
            extra.extend(["--distributed-executor-backend", "external_launcher"])
    if fields.enable_expert_parallel:
        extra.append("--enable-expert-parallel")
    if fields.max_num_seqs is not None:
        extra.extend(["--max-num-seqs", str(fields.max_num_seqs)])
    if fields.max_num_batched_tokens is not None:
        extra.extend(["--max-num-batched-tokens",
                      str(fields.max_num_batched_tokens)])
    return extra


def _bench_runner_keys(runtime_profile: str | None) -> Dict[str, Any]:
    """Runner identity for target.json:bench.

    pumice-* profiles run run_pumice_grpc_bench.py, which only accepts the
    vllm_bench_latency name through a deprecated compatibility path (it
    prints a warning on every launch; found live on trn2-1). vllm-* profiles
    keep the legacy runner that run_vllm_bench_latency_sweep.py requires.
    """
    if runtime_profile and runtime_profile.startswith("pumice-"):
        return {"runner": "pumice_grpc_bench", "server_cmd": "pumice-server"}
    return {"runner": "vllm_bench_latency", "vllm_cmd": "vllm"}


def _target_json(
    fields: TargetFields,
    artifact_dir: Path,
    runtime_profile: str | None = None,
) -> Dict[str, Any]:
    # This schema is consumed by run_pumice_grpc_bench.py (pumice-*) and
    # run_vllm_bench_latency_sweep.py (vllm-*).
    target_block: Dict[str, Any] = {
        "model_id": fields.model_id,
        "dtype": fields.dtype,
        "tp": fields.tp,
        "dp": fields.data_parallel_size,
        "ep": fields.ep,
        "max_model_len": fields.max_model_len,
    }
    if fields.prefix_cache_regime is not None:
        target_block["prefix_cache_regime"] = fields.prefix_cache_regime
    if fields.correctness_sample_count is not None:
        target_block["correctness_sample_count"] = fields.correctness_sample_count
    return {
        "artifact_dir": str(artifact_dir),
        "target": target_block,
        "workload": (
            {"workload_matrix": fields.workload_matrix, "num_iters": fields.num_iters}
            if fields.workload_matrix is not None
            else {
                "input_len": fields.input_len,
                "output_len": fields.output_len,
                "batch_sizes": fields.batch_sizes,
                "num_iters": fields.num_iters,
            }
        ),
        "bench": {
            **_bench_runner_keys(runtime_profile),
            # Frozen prompt source: run_pumice_grpc_bench.py reads it when the
            # CLI flag is absent, so every arm of every stage inherits it.
            **({"prompt_source": fields.prompt_source} if fields.prompt_source else {}),
            "extra_args": _compose_extra_args(fields, runtime_profile),
            "baseline_extra_args": [],
            "opt_extra_args": [],
            "baseline_env": dict(fields.baseline_env or {}),
            "opt_env": {
                "<ENABLE_FLAG>": "1"
            },
            "baseline_label": "baseline",
            "opt_label": "opt",
            "fastpath_evidence": {
                "baseline": {
                    "require_patterns": [],
                    "forbid_patterns": [],
                },
                "opt": {
                    "require_patterns": [],
                    "forbid_patterns": [],
                },
                "note": "Fill require_patterns to assert optimized fast-path executed (recommended).",
            },
        },
        "gating": {
            "noise_tolerance_pct": fields.noise_tolerance_pct,
            "catastrophic_regression_pct": fields.catastrophic_regression_pct,
            "min_e2e_improvement_pct": fields.min_e2e_improvement_pct,
        },
        "notes": {
            "production_parity": "Ensure CUDA graphs / torch.compile settings match production. See references/e2e-latency-guide.md.",
            **(
                {
                    "max_model_len_request": (
                        "auto requested; scaffolded with the default 4096. "
                        "Actual model support depends on the model config."
                    )
                }
                if fields.max_model_len_auto_requested
                else {}
            ),
        },
    }


# --------------------------------------------------------------------------
# Campaign workspace (conventions §3): <engine>/.ammo/<campaign-id>/
# --------------------------------------------------------------------------

WORKSPACE = ".ammo"
EXCLUDE_LINE = ".ammo/"
FINGERPRINT_TIMEOUT_S = 300


def _mint_campaign_id() -> str:
    """YYYYMMDD-HHMMSS-<6 hex>, UTC."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def _valid_campaign_id(value: str) -> bool:
    # The layer's own id rule, so the marker this script writes is one that
    # campaign_paths.find_campaign_dir() accepts.
    return bool(campaign_paths._ID.fullmatch(value)) and value not in (".", "..")


def _git_toplevel(start: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = result.stdout.strip()
    return Path(top).resolve() if result.returncode == 0 and top else None


def _in_workspace(artifact_dir: Path) -> bool:
    """True when artifact_dir has the form <engine>/.ammo/<id>."""
    return artifact_dir.parent.name == WORKSPACE


def _resolve_campaign(args: argparse.Namespace, p: argparse.ArgumentParser):
    """Return (campaign_dir, campaign_id, engine_root, engine_is_guess).

    Nothing is written here, so every refusal leaves the disk unchanged.
    """
    if args.campaign_id is not None and not _valid_campaign_id(args.campaign_id):
        p.error(
            f"--campaign-id {args.campaign_id!r} must be one path segment of "
            "letters, digits, '.', '_' or '-' that starts with a letter or digit"
        )
    explicit_engine = (
        Path(args.engine_root).expanduser().resolve() if args.engine_root else None
    )
    if explicit_engine is not None and not explicit_engine.is_dir():
        p.error(f"--engine-root {explicit_engine} is not a directory")

    if args.artifact_dir:
        campaign_dir = Path(args.artifact_dir).expanduser().resolve()
        campaign_id = campaign_paths.campaign_id(campaign_dir)
        if args.campaign_id is not None and args.campaign_id != campaign_id:
            p.error(
                f"--campaign-id {args.campaign_id!r} differs from the --artifact-dir "
                f"basename {campaign_id!r}; pass one of them"
            )
        if _in_workspace(campaign_dir):
            workspace_engine = campaign_paths.engine_root(campaign_dir)
            if explicit_engine is not None and explicit_engine != workspace_engine:
                p.error(
                    f"--engine-root {explicit_engine} does not hold the --artifact-dir "
                    f"workspace {campaign_dir.parent}; pass one of them"
                )
            return campaign_dir, campaign_id, workspace_engine, False
        if explicit_engine is not None:
            return campaign_dir, campaign_id, explicit_engine, False
        top = _git_toplevel(Path.cwd())
        if top is not None:
            return campaign_dir, campaign_id, top, False
        return campaign_dir, campaign_id, Path.cwd().resolve(), True

    engine = explicit_engine or _git_toplevel(Path.cwd())
    if engine is None:
        p.error(
            f"{Path.cwd()} is not inside a git checkout. Run new_target.py inside "
            "the engine checkout, or pass --engine-root"
        )
    campaign_id = args.campaign_id or _mint_campaign_id()
    return engine / WORKSPACE / campaign_id, campaign_id, engine, False


def _engine_venv(engine: Path) -> str:
    """AMMO_PYTHON when set at scaffold (an empty value counts as unset), else <engine>/.venv."""
    explicit = (os.environ.get("AMMO_PYTHON") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    return str(engine / ".venv")


def _venv_python(venv: str) -> Path:
    path = Path(venv)
    return path if path.is_file() else path / "bin" / "python"


def _stack_fingerprint(engine_root: Path, python: Path) -> Optional[Dict[str, Any]]:
    """Engine stack identity from `preflight_pumice.py --write-fingerprint`.

    It runs under the engine python, because only that python imports pumice.
    --engine-root names the new engine, so the commit is never read from a
    checkout that a previous campaign's env points at. The flag writes one
    JSON object to the path it is given. Any failure returns None with a WARNING:
    preflight, not the scaffold, is the step that fails closed on a broken
    engine venv.
    """
    def skip(reason: str) -> None:
        print(
            f"WARNING: runtime.stack_fingerprint not recorded: {reason}. "
            "Preflight records the engine stack before Stage 1.",
            file=sys.stderr,
        )

    if not (python.is_file() and os.access(python, os.X_OK)):
        skip(f"engine python {python} not found")
        return None
    preflight = plugin_path("scripts", "preflight_pumice.py")
    with tempfile.TemporaryDirectory(prefix="ammo-fingerprint-") as tmp:
        out = Path(tmp) / "fingerprint.json"
        try:
            result = subprocess.run(
                [str(python), str(preflight), "--engine-root", str(engine_root),
                 "--write-fingerprint", str(out)],
                cwd=str(engine_root), capture_output=True, text=True,
                timeout=FINGERPRINT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            skip(f"{preflight.name} did not run ({exc})")
            return None
        if result.returncode != 0:
            last = (result.stderr or result.stdout).strip().splitlines()[-1:]
            skip(f"{preflight.name} --write-fingerprint exited {result.returncode}"
                 + (f": {last[0]}" if last else ""))
            return None
        text = out.read_text(encoding="utf-8") if out.is_file() else ""
        try:
            data = json.loads(text)
        except ValueError:
            skip(f"{preflight.name} --write-fingerprint wrote no JSON object")
            return None
    if not isinstance(data, dict) or not data:
        skip(f"{preflight.name} --write-fingerprint wrote no JSON object")
        return None
    return data


def _explicit_fingerprint(p: argparse.ArgumentParser) -> Optional[Dict[str, Any]]:
    raw = (os.environ.get("AMMO_STACK_FINGERPRINT") or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        p.error("AMMO_STACK_FINGERPRINT must be a JSON object")
    if not isinstance(data, dict):
        p.error("AMMO_STACK_FINGERPRINT must be a JSON object")
    return data


def _setdefault_env(name: str, value: Optional[str]) -> None:
    """Explicit env wins; an empty value counts as unset (campaign_paths.apply_env)."""
    if value and not os.environ.get(name):
        os.environ[name] = value


def _write_marker(engine: Path, campaign_id: str) -> None:
    """Point <engine>/.ammo/active at this campaign. Atomic; never cleared."""
    marker = engine / campaign_paths.MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker.with_name(f".active.{os.getpid()}.tmp")
    tmp.write_text(campaign_id + "\n", encoding="utf-8")
    os.replace(tmp, marker)


def _git_exclude_path(engine: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(engine), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        path = Path(result.stdout.strip())
        return path if path.is_absolute() else engine / path
    if (engine / ".git").is_dir():
        return engine / ".git" / "info" / "exclude"
    return None


def _exclude_workspace(engine: Path) -> None:
    """Append `.ammo/` to the engine's git exclude file once (idempotent)."""
    path = _git_exclude_path(engine)
    if path is None:
        return
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(line.strip() in {".ammo", ".ammo/", "/.ammo", "/.ammo/"} for line in text.splitlines()):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(EXCLUDE_LINE + "\n")


def _require_supported_profile(profile: str, p: argparse.ArgumentParser) -> None:
    """The one profile gate (conventions §6): only pumice-cuda scaffolds in v1.

    resolve_runtime() still accepts every profile, so the Neuron branches
    below stay in place for records and for a later release.
    """
    try:
        runtime_ops.require_supported_profile(profile)
    except ValueError as exc:
        p.error(str(exc))


def _drop_active_campaign_env() -> None:
    """Undo the launcher's campaign_paths.apply_env() before any env read.

    scripts/ammo fills the active campaign's derived names (AMMO_PYTHON,
    AMMO_SERVING_*, AMMO_STACK_FINGERPRINT, ...) before it runs this script.
    A new campaign must not inherit them. unapply_env() restores only the
    names apply_env() set, so names the caller exported still win.
    """
    campaign_paths.unapply_env()


def main() -> None:
    _drop_active_campaign_env()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact-dir", type=str, default=None,
                   help="Campaign dir to create/populate. Default: "
                        "<engine-root>/.ammo/<campaign-id>")
    p.add_argument("--campaign-id", type=str, default=None,
                   help="Campaign id (the session_id value). Default: minted "
                        "YYYYMMDD-HHMMSS-<6 hex> (UTC)")
    p.add_argument("--engine-root", type=str, default=None,
                   help="Engine checkout that holds .ammo/. Default: "
                        "`git rev-parse --show-toplevel` of the current dir")
    p.add_argument("--force", action="store_true", help="Overwrite existing files")

    # Optional target metadata (safe defaults + placeholders)
    p.add_argument("--model-id", type=str, default=None)
    p.add_argument("--hardware", type=str, default=None)
    p.add_argument("--dtype", type=str, default=None)
    p.add_argument("--gpu-model", type=str, default=None,
                   help="GPU model name for state.json gpu_resources.gpu_model. "
                        "Default: detected from nvidia-smi (first GPU); "
                        "falls back to the placeholder when detection fails.")

    p.add_argument("--tp", type=int, default=1)
    p.add_argument("--ep", type=int, default=1,
                   help="Legacy expert-parallel sizing field — persisted to target.ep "
                        "in state.json. Does NOT enable vLLM expert parallelism. "
                        "Use --enable-expert-parallel to actually enable EP.")
    p.add_argument("--data-parallel-size", type=int, default=1,
                   help="Data-parallel size. When > 1, it is written to "
                        "bench.extra_args. vllm-* profiles also get "
                        "--distributed-executor-backend external_launcher "
                        "(required for torchrun DP); pumice-* profiles never do.")
    p.add_argument("--enable-expert-parallel", action="store_true",
                   help="Enable vLLM expert parallelism (passes "
                        "--enable-expert-parallel through to bench.extra_args).")
    p.add_argument("--min-e2e-improvement", type=float,
                   default=_schema_default("min_e2e_improvement_pct", 0.5),
                   help="Campaign floor: stop when no candidate can yield >= this %% E2E "
                        "improvement, and per-BS PASS requires clearing "
                        "max(this, noise_tolerance_pct) (schema default; keep >= noise tolerance)")

    p.add_argument("--max-model-len", default="4096",
                   help="Positive integer, or 'auto' to resolve "
                        "max_position_embeddings from the cached HF config "
                        "(fails closed if uncached)")
    p.add_argument("--input-len", type=int, default=64)
    p.add_argument("--output-len", type=int, default=512)
    p.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 8, 32])
    p.add_argument("--isl-osl",
                   help="Comma-separated ISL:OSL pairs crossed with --batch-sizes")
    p.add_argument("--max-num-seqs", type=int,
                   help="Frozen serving concurrency (default: max batch size)")
    p.add_argument("--max-num-batched-tokens", type=int,
                   help="Frozen prefill chunk budget, written to bench.extra_args. "
                        "Required on pumice-neuron when max_model_len > 4096: "
                        "the Neuron runner defaults kv_segment_size to this "
                        "value and only 512/1024/2048/4096 compile.")
    p.add_argument("--prompt-source", choices=("gsm8k", "raw"), default=None,
                   help="Frozen E2E-timing prompt source, written to "
                        "bench.prompt_source. Default: omit (runner default "
                        "gsm8k, chat-template-wrapped). Pass raw when "
                        "input_len cannot hold the model's chat template "
                        "(gpt-oss: 67 tokens > the default input_len 64).")
    p.add_argument("--num-iters", type=int, default=10)
    p.add_argument("--prefix-cache-regime",
                   choices=("on", "off"),
                   default=None,
                   help="WS4.4 pin: force --{no-}enable-prefix-caching on "
                        "both A/B arms so the CLI flag beats confdb. Leaving "
                        "this unset keeps the runner's implicit regime, but "
                        "the WS4.4 verify step then skips the argv/log checks.")
    p.add_argument("--correctness-sample-count", type=int, default=None,
                   help="GSM8K questions for the correctness gate; default "
                        "1319 (full test split) at runtime when unset.")

    # Gating options (BS-dependent optimization support).
    # Defaults resolved from state.schema.json so the schema is the single source.
    p.add_argument("--noise-tolerance-pct", type=float,
                   default=_schema_default("noise_tolerance_pct", 0.5),
                   help="Per-BS speedup within this %% of 1.0 is classified NOISE (schema default)")
    p.add_argument("--catastrophic-regression-pct", type=float,
                   default=_schema_default("catastrophic_regression_pct", 5.0),
                   help="Per-BS regression beyond this %% is classified CATASTROPHIC (schema default)")

    p.add_argument("--serving-manifest", help="Frozen managed P/D ServingSpec JSON; selects serving gates")
    args = p.parse_args()
    # Host-local campaigns run the Pumice CUDA path. An explicit, non-empty
    # AMMO_RUNTIME_PROFILE wins; the one gate below decides whether it is
    # supported. new_target.py never calls campaign_paths.apply_env(): it
    # creates a campaign, so values derived from a previous campaign's
    # target.json must not leak into the new one.
    _setdefault_env("AMMO_RUNTIME_PROFILE", PUMICE_CUDA_PROFILE)
    _require_supported_profile(os.environ["AMMO_RUNTIME_PROFILE"].strip(), p)
    try:
        runtime = resolve_runtime()
    except ValueError as exc:
        p.error(str(exc))

    # The campaign form is binding intent, not an optional prompt suggestion.
    # A P/D campaign must never silently start a batch campaign.
    if os.environ.get("AMMO_SERVING_SETUP") == "pd":
        assigned = os.environ.get("AMMO_SERVING_MANIFEST")
        if not assigned:
            p.error("AMMO_SERVING_SETUP=pd but AMMO_SERVING_MANIFEST names no manifest")
        if args.serving_manifest and Path(args.serving_manifest).resolve() != Path(assigned).resolve():
            p.error("a P/D campaign must use the AMMO_SERVING_MANIFEST manifest")
        args.serving_manifest = assigned
    serving_spec = None
    serving_manifest_path = None
    if args.serving_manifest:
        from serving_contract import load_spec
        try:
            serving_manifest_path = Path(args.serving_manifest).expanduser().resolve()
            serving_spec = load_spec(serving_manifest_path)
            if os.environ.get("AMMO_SERVING_SETUP") == "pd":
                expected = os.environ.get("AMMO_SERVING_MANIFEST_SHA256")
                if not expected or serving_spec.checksum != expected:
                    p.error("the P/D manifest changed since AMMO_SERVING_MANIFEST_SHA256 was taken")
            serving_spec.verify_files()
            if args.model_id is not None and args.model_id != serving_spec.model_id:
                p.error("--model-id differs from the serving manifest")
            if args.dtype is not None and args.dtype != serving_spec.dtype:
                p.error("--dtype differs from the serving manifest")
            if serving_spec.runtime_profile != runtime.runtime_profile:
                p.error(
                    f"serving runtime {serving_spec.runtime_profile} differs from the "
                    f"campaign profile {runtime.runtime_profile}"
                )
            # Shape/load/SLO knobs come from the serving manifest. Explicit batch
            # flags would create two contradictory workload contracts.
            forbidden = {"--input-len", "--output-len", "--batch-sizes", "--isl-osl", "--num-iters", "--max-num-seqs",
                         "--min-e2e-improvement", "--noise-tolerance-pct", "--catastrophic-regression-pct",
                         "--tp", "--ep", "--data-parallel-size", "--enable-expert-parallel", "--max-model-len"}
            if any(arg.split("=")[0] in forbidden for arg in sys.argv[1:]):
                p.error("batch workload/gate flags cannot override a serving manifest")
            args.model_id, args.dtype = serving_spec.model_id, serving_spec.dtype
        except (ValueError, OSError) as exc:
            p.error(str(exc))

    artifact_dir, campaign_id, engine, engine_is_guess = _resolve_campaign(args, p)
    if engine_is_guess:
        print(
            f"WARNING: engine.root recorded as the current dir {engine}: it is not "
            "inside a git checkout. Pass --engine-root to name the engine checkout.",
            file=sys.stderr,
        )
    if not args.force:
        for name in ("state.json", "target.json"):
            if (artifact_dir / name).exists():
                p.error(f"Refusing to overwrite existing file: {artifact_dir / name} (use --force)")

    fields = _default_target_fields(args)

    if (
        runtime.is_neuron
        and fields.max_num_batched_tokens is None
        and fields.max_model_len > NEURON_MAX_KV_SEGMENT
    ):
        # Without this every TP rank dies at warmup with
        # "kv_segment_size=<max_model_len> is not supported" before HealthCheck.
        fields = replace(fields, max_num_batched_tokens=NEURON_MAX_KV_SEGMENT)
        print(
            f"WARNING: pumice-neuron with max_model_len {fields.max_model_len} > "
            f"{NEURON_MAX_KV_SEGMENT}: froze --max-num-batched-tokens "
            f"{NEURON_MAX_KV_SEGMENT} in bench.extra_args (the Neuron runner sets "
            "kv_segment_size to this value; only 512/1024/2048/4096 compile; the "
            "Pumice gpt-oss-20b Neuron smoke test uses 4096). Pass "
            "--max-num-batched-tokens to choose another supported value.",
            file=sys.stderr,
        )
    if runtime.runtime_profile == PUMICE_NEURON_PROFILE:
        fields = replace(fields, baseline_env=_neuron_baseline_env(os.environ))
    gpu_model = None
    if runtime.is_cuda:
        gpu_model = (args.gpu_model or "").strip() or _detect_gpu_model()
    if runtime.is_cuda and not gpu_model:
        print(
            "WARNING: gpu_resources.gpu_model is still the placeholder "
            f"{PLACEHOLDER!r} — nvidia-smi detection failed and --gpu-model "
            "was not given. Fill it in state.json before validation.",
            file=sys.stderr,
        )

    # The engine contract that campaign_paths.campaign_env() derives the
    # campaign env from (references/campaign-env.md section 1). Fill the same
    # names in this process first, so the scaffold-time stack fingerprint in
    # state.json matches what later scripts compute after apply_env().
    venv = _engine_venv(engine)
    accelerator_name = gpu_model or None
    instance_family = (os.environ.get("AMMO_INSTANCE_FAMILY") or "").strip() or accelerator_name
    fingerprint = _explicit_fingerprint(p)
    if fingerprint is None:
        fingerprint = _stack_fingerprint(engine, _venv_python(venv))
    _setdefault_env("AMMO_ENGINE_ROOT", str(engine))
    _setdefault_env("AMMO_RUNTIME_SOURCE_DIR", str(engine))
    _setdefault_env("AMMO_ACCELERATOR_NAME", accelerator_name)
    _setdefault_env("AMMO_INSTANCE_FAMILY", instance_family)
    if fingerprint is not None:
        _setdefault_env(
            "AMMO_STACK_FINGERPRINT",
            json.dumps(fingerprint, sort_keys=True, separators=(",", ":")),
        )

    state = _state_json(
        fields, artifact_dir, args.min_e2e_improvement,
        noise_tolerance_pct=args.noise_tolerance_pct,
        catastrophic_regression_pct=args.catastrophic_regression_pct,
        gpu_model=gpu_model,
    )
    state["session_id"] = campaign_id
    target = _target_json(fields, artifact_dir, runtime.runtime_profile)
    target["engine"] = {"root": str(engine), "venv": venv}
    target["runtime"] = {
        "profile": runtime.runtime_profile,
        "accelerator_kind": runtime.accelerator_kind,
        "accelerator_name": accelerator_name,
        "instance_family": instance_family,
    }
    if fingerprint is not None:
        target["runtime"]["stack_fingerprint"] = fingerprint
    if serving_spec is not None:
        from serving_campaign import binding
        required_resources = {resource for worker in serving_spec.workers for resource in worker.resource_ids}
        if not required_resources <= set(state["runtime"]["resource_ids"]):
            p.error("serving workers need resources outside this campaign's visible pool "
                    "(CUDA_VISIBLE_DEVICES)")
        if state["runtime"].get("lnc") != serving_spec.lnc:
            p.error("serving LNC differs from the campaign configuration")
        state["serving"] = binding(serving_spec)
        state["campaign"]["rounds"][0]["serving"] = {}
        target["serving"] = serving_spec.model_dump(mode="json")
        target["serving_manifest"] = {
            "path": str(serving_manifest_path),
            "sha256": serving_spec.checksum,
        }
        # Legacy metadata remains readable, but no batch workload or runner is
        # operative for this target. This prevents accidental batch fallback.
        target.pop("workload", None)
        target.pop("bench", None)
        target.pop("gating", None)
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        p.error(f"cannot validate generated state against schema: {exc}")
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(state),
        key=lambda error: list(error.path),
    )
    if errors:
        details = "; ".join(error.message for error in errors[:5])
        p.error(f"generated state violates schema: {details}")

    # Every check passed: now write. Create v2 round-scoped scaffold for round 1.
    # Reference: references/artifact-layout.md
    round_dir = artifact_dir / "rounds" / "1"
    for subdir in (
        "profiling/nsys",
        "profiling/ncu",
        "profiling/neuron",
        "sweeps/baseline/json",
        "sweeps/baseline/logs",
        "sweeps/baseline/status",
        "sweeps/opt",
        "sweeps/opt_correctness",
        "sweeps/opt_profiling",
        "sweeps/integration",
        "sweeps/integration_profiling",
        "sweeps/golden_capture",
        "mining",
        "debate/proposals",
        "debate/micro_experiments",
        "debate/monitor_audits",
        "tracks",
        "audits",
        "_archive",
    ):
        (round_dir / subdir).mkdir(parents=True, exist_ok=True)
    # Cross-round (campaign-level) blockers dir — NOT round-scoped.
    (artifact_dir / "blockers").mkdir(exist_ok=True)
    if serving_spec is not None:
        (artifact_dir / "serving").mkdir(exist_ok=True)
    _write_json(artifact_dir / "state.json", state, force=args.force)
    _write_json(artifact_dir / "target.json", target, force=args.force)
    if _in_workspace(artifact_dir):
        workspace_engine = campaign_paths.engine_root(artifact_dir)
        _write_marker(workspace_engine, campaign_id)
        _exclude_workspace(workspace_engine)

    print(f"Initialized campaign {campaign_id}: {artifact_dir}")
    print("Created: state.json, target.json, rounds/1/ scaffold")
    if _in_workspace(artifact_dir):
        print(f"Marker: {campaign_paths.engine_root(artifact_dir) / campaign_paths.MARKER}")
    print("Next: write rounds/1/constraints.md (Phase 1)")


if __name__ == "__main__":
    main()
