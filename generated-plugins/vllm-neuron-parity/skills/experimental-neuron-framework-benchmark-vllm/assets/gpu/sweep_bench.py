"""Config-driven GPU serving sweep on InferencePerfAnalyzer's orchestrator.

Replaces the old bash run_bench.sh / bench_one.sh. For every
model x regime x shape x sharding(tp,dp) x concurrency cell in bench_config.json
this drives IPA's transport-agnostic server pool + `run_bench`, writes result.json
in the IPA path layout (ingestible by build_deliverables.py), applies a
failure-rate guard, and records the cell in a resume ledger.

Rides IPA's blessed orchestration seams so we inherit, for free:
  - **--transport {kaizen,docker}** (default kaizen): the SAME sweep runs inside a
    Kaizen pod (Kaizen provisions/tears down the GPU) OR via `docker run` on a local
    GPU box. We don't hand-roll docker. Picked at runtime via the pytest flag.
  - **resume ledger** (state.json at the results root): a cell that already PASSED is
    skipped before its server even starts, so a sweep that hit its time budget (or was
    paused to claim the node) resumes without re-running finished work. Point
    ORCHESTRATOR_RESULTS_ROOT at a DURABLE path (e.g. FSx) for cross-workload resume.

Axes:
  - concurrency + DP(tp,dp) — the sweep the fixed `--max-concurrency 16`, TP-only
    harness couldn't do.
  - REGIME — precision/quant x speculative-decoding in one run on ONE image, so
    spec-vs-nospec (and quant-vs-bf16) is engine-matched. Each (model,regime) writes
    `<results>/<key>__<regime>/…` + a regime_meta.json for the deliverables.

Run — Kaizen (default): submit a workload whose command is (see SKILL.md §A3):
    export ORCHESTRATOR_RESULTS_ROOT=$FSX_TEAM_SHARED_RW/<alias>/results
    export BENCH_CONFIG=<path>/bench_config.json KAIZEN_IMAGE=<baked-image>
    python3 -m pytest sweep_bench.py -o addopts= --transport=kaizen -v

Run — local docker box (inside the venv where IPA + pytest are pip-installed):
    export ORCHESTRATOR_RESULTS_ROOT=/scratch/results BENCH_CONFIG=<path>/bench_config.json
    pytest sweep_bench.py --transport=docker -v
    pytest sweep_bench.py --transport=docker -v -k "fp8_spec and tp4_dp2 and c16"
    pytest sweep_bench.py --collect-only -q                 # preview cells, no run

Then sync ORCHESTRATOR_RESULTS_ROOT back and run build_deliverables.py --multi.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from inference_perf_analyzer.orchestrator import SweepEntry, out_dir, run_bench, server_tag


# --- config load ------------------------------------------------------------

CONFIG_PATH = Path(os.environ.get("BENCH_CONFIG", "bench_config.json"))
if not CONFIG_PATH.exists():
    raise SystemExit(
        f"bench config not found: {CONFIG_PATH} — set BENCH_CONFIG or run from the "
        "dir holding bench_config.json (copy bench_config.example.json to start)."
    )
CFG = json.loads(CONFIG_PATH.read_text())

# IMAGE / DOCKER_ENV are read by IPA's server_handle_pool from THIS module (module
# attrs, not per-call args). ONE image for every regime => engine-matched comparison.
IMAGE = CFG["image"]
DOCKER_ENV = dict(CFG.get("docker_env", {}))   # docker transport only; kaizen sets env in the workload cmd
# HF_TOKEN forwarding (docker transport): IPA only injects THIS module's DOCKER_ENV
# into the vLLM container — it does NOT forward the pytest process's env — so a bare
# `HF_TOKEN=… pytest …` never reaches the server (fatal for GATED models: HF 401 at
# download → server never starts). Forward it here from the environment into the
# container env. Never logged/printed. (Config docker_env wins if it set HF_TOKEN.)
for _tok in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
    if os.environ.get(_tok) and _tok not in DOCKER_ENV:
        DOCKER_ENV[_tok] = os.environ[_tok]
GPU_COUNT = int(CFG["gpu_count"])
FAIL_THRESHOLD = float(CFG.get("fail_threshold", 0.05))
WORKLOAD = CFG.get("workload", {})
SERVER = CFG.get("server", {})
REGIMES = CFG.get("regimes") or [{"name": "bf16", "prec": "bf16"}]
BENCH_TIMEOUT_S = int(CFG.get("bench_timeout_s", 7200))

# max ISL across the whole sweep — used for the scheduler-step size below.
_MAX_ISL = max((int(s["isl"]) for s in CFG["shapes"]), default=8192)

# Ledger identity (read by IPA's sweep_state fixture). MODEL/PREFIX label the sweep;
# SERVE_EXTRA_FLAGS carries the FULL config signature so ANY meaningful edit
# (a regime's flags, a shape, the dataset) changes the ledger signature → the old
# ledger is archived and a fresh one starts, so two configs' numbers never mix.
MODEL = CFG["models"][0]["hf_id"] if CFG.get("models") else "multi"
PREFIX = CFG.get("sweep_name", CONFIG_PATH.stem)
SERVE_EXTRA_FLAGS = [json.dumps(
    {k: CFG.get(k) for k in
     ("models", "regimes", "shapes", "shardings", "concurrencies",
      "concs_by_shape", "workload", "server")},
    sort_keys=True, separators=(",", ":"))]


def _shardings_for(model: dict) -> list[tuple[int, int, bool]]:
    # Per-model shardings override the global; the global is optional (a config may
    # set shardings ONLY per-model — e.g. a large MoE where the fitting shardings
    # differ by precision). `.get(...) or ...` avoids eagerly demanding CFG["shardings"].
    raw = model.get("shardings") or CFG.get("shardings")
    if not raw:
        raise SystemExit(
            f"no shardings for model {model.get('key', '?')}: set a global "
            "'shardings' or a per-model 'shardings'. Each {tp,dp} must have "
            f"tp*dp == gpu_count ({GPU_COUNT})."
        )
    out = []
    for s in raw:
        tp, dp = int(s["tp"]), int(s.get("dp", 1))
        if tp * dp != GPU_COUNT:
            raise SystemExit(
                f"sharding tp{tp}_dp{dp} for model {model['key']} multiplies to "
                f"{tp * dp}, not gpu_count={GPU_COUNT}. Every run must fill the box."
            )
        # EP (expert parallelism) is a per-sharding axis for MoE models: set
        # "ep": true|false on a sharding entry to SWEEP it (two cells at the same
        # tp/dp — one with --enable-expert-parallel, one without). When "ep" is
        # unset, fall back to the model-level `moe` flag: dense models never get
        # EP; MoE-only-flag configs (older Qwen/Gemma-MoE) still get --enable-
        # expert-parallel on every sharding (argv unchanged). Note: MoE-only-flag
        # configs will now write to `<key>__<regime>_ep/` and produce cell_ids
        # with an `_ep` suffix; the sweep_state ledger keys on both the sweep's
        # config signature (SERVE_EXTRA_FLAGS) and the cell_id, so this is a
        # LEDGER-INVALIDATING change on first re-run — expected, mid-sweep resume
        # of an old ledger will not carry over cells across this upgrade.
        # EP shards the 256+ experts across tp*dp ranks; attention stays TP+DP.
        ep = s.get("ep")
        ep = bool(model.get("moe")) if ep is None else bool(ep)
        out.append((tp, dp, ep))
    return out


def _concs_for(model: dict, isl: int, osl: int) -> list[int]:
    per_model = model.get("concs_by_shape") or CFG.get("concs_by_shape") or {}
    key = f"{isl},{osl}"
    if key in per_model:
        return list(per_model[key])
    return list(model.get("concurrencies", CFG["concurrencies"]))


def _range_ratio_for(isl: int, osl: int) -> float:
    """random_range_ratio for a shape. Per-shape override via
    workload.range_ratio_by_shape (keyed 'isl,osl') lets you drop jitter to 0 on a
    near-context-window shape while keeping the native-production 0.8 elsewhere —
    the 'lower R for that shape' long-context remedy. Falls back to the global."""
    by_shape = (WORKLOAD.get("range_ratio_by_shape") or {})
    key = f"{isl},{osl}"
    if key in by_shape:
        return float(by_shape[key])
    return float(WORKLOAD.get("random_range_ratio", 0.8))


def _regimes_for(model: dict) -> list[dict]:
    """Regimes to run for a model. A model may restrict to a subset by name
    (e.g. a model with no quantized checkpoint skips the fp8 regimes)."""
    names = model.get("regimes")
    if names is None:
        return REGIMES
    by_name = {r["name"]: r for r in REGIMES}
    return [by_name[n] for n in names]


def _hf_id(model: dict, regime: dict) -> str:
    """Effective checkpoint for (model, regime). Quantized WEIGHTS are benchmarked
    by pointing at a pre-quantized HF checkpoint here (vLLM auto-detects the method);
    `hf_id_by_regime` maps a regime name to that checkpoint. Falls back to the base
    checkpoint (+ an optional online `regime.quantization` flag)."""
    return model.get("hf_id_by_regime", {}).get(regime["name"], model["hf_id"])


def _len_buffer(isl: int) -> int:
    """Graduated max-model-len headroom (matches the E2E config doc / InferenceX):
    small prompts need little slack, long-context needs more."""
    if isl <= 1000:
        return 128
    if isl <= 10000:
        return 512
    return 1024


def _max_num_batched_tokens() -> int:
    """Scheduler-step size. Standardized so prefill of the largest ISL lands in one
    step (not artificially fragmented) while the cap still bounds batching:
    max(2*max_isl, 8192) capped at 16384 (per the E2E config doc). Overridable via
    server.max_num_batched_tokens."""
    v = SERVER.get("max_num_batched_tokens")
    if v not in (None, "auto"):
        return int(v)
    return min(max(2 * _MAX_ISL, 8192), 16384)


# --- build the flat cell list (model x regime x sharding-EP x SweepEntry) -----
# A cell is (model, regime, ep, SweepEntry). `ep` rides ALONGSIDE SweepEntry
# rather than inside it — SweepEntry is IPA's own dataclass (tp/dp/isl/osl/conc)
# and we don't extend it; EP only affects our server argv + labels, not IPA's
# server-pool identity beyond the argv it already keys on.

def _build_sweep() -> list[tuple[dict, dict, bool, SweepEntry]]:
    cells = []
    for model in CFG["models"]:
        for regime in _regimes_for(model):
            for shape in CFG["shapes"]:
                isl, osl = int(shape["isl"]), int(shape["osl"])
                for tp, dp, ep in _shardings_for(model):
                    for conc in _concs_for(model, isl, osl):
                        cells.append(
                            (model, regime, ep,
                             SweepEntry(tp=tp, isl=isl, osl=osl, conc=conc, dp=dp))
                        )
    return cells


SWEEP = _build_sweep()


def _ep_tag(ep: bool) -> str:
    # Only append an EP marker when EP is ON, so configs that never sweep EP
    # (dense models) keep their old cell-ids / run-dirs unchanged.
    return "_ep" if ep else ""


def _cell_id(cell: tuple[dict, dict, bool, SweepEntry]) -> str:
    model, regime, ep, e = cell
    return (f"{model['key']}_{regime['name']}_{server_tag(e.tp, e.dp)}{_ep_tag(ep)}"
            f"_isl{e.isl}_osl{e.osl}_c{e.conc}")


# IPA's sweep_state fixture seeds the full matrix via module `_id(e)` over `SWEEP`.
_id = _cell_id


# --- server argv (a LIST — IPA's pool builds the transport prefix itself) ----

def _kv_cache_dtype(regime: dict) -> str:
    return regime.get("kv_cache_dtype", SERVER.get("kv_cache_dtype", "auto"))


def _server_argv(model: dict, regime: dict, tp: int, dp: int, ep: bool,
                 max_model_len: int, max_num_seqs: int) -> list[str]:
    """`vllm serve …` as an argv list. IPA's server_handle_pool runs this directly
    (kaizen subprocess) or wraps it for `docker run` (docker transport) — so NO
    `exec`/quoting here; the pool handles that. Model MUST be argv[2] (the pool
    extracts it and detects the framework from the command)."""
    argv = [
        "vllm", "serve", _hf_id(model, regime),
        "--host", "0.0.0.0", "--port", "9000",
        "--tensor-parallel-size", str(tp),
        "--max-model-len", str(max_model_len),
        # >= the max concurrency this server is driven at, so high-conc cells run
        # truly concurrently instead of queueing. Constant across a shape so the
        # pool reuses the server across the concurrency sweep (argv unchanged).
        "--max-num-seqs", str(max_num_seqs),
        # Standardized scheduler-step size so prefill of the largest ISL is one step
        # and the step size is identical across models (one fewer comparison DOF).
        "--max-num-batched-tokens", str(_max_num_batched_tokens()),
        "--gpu-memory-utilization", str(SERVER.get("gpu_memory_utilization", 0.9)),
        "--trust-remote-code",
        "--no-enable-log-requests",
        "--seed", "0",
    ]
    # KV-cache block size. Some models' recipes require a specific stride (e.g.
    # DeepSeek-V4's CSA path wants --block-size 256); without emitting it here a
    # config-level `server.block_size` would be silently dropped and vLLM would use
    # its default (16). Only emit when set, so models that don't care keep the default.
    if SERVER.get("block_size"):
        argv += ["--block-size", str(SERVER["block_size"])]
    # MoE expert parallelism. Now a per-sharding SWEEP axis (see _shardings_for):
    # `ep` is decided per {tp,dp} entry, defaulting to the model's `moe` flag when
    # the entry omits it. When ON, --enable-expert-parallel shards the 256+ experts
    # across tp*dp ranks (Mixtral, Qwen3-235B, DeepSeek-V4, gpt-oss, …); attention
    # stays TP+DP. Sweeping ep on/off at a fixed tp/dp isolates EP's effect on fit
    # and throughput. Dense models pass ep=False and omit the flag entirely.
    if ep:
        argv += ["--enable-expert-parallel"]
    # Long-context past the model's native window needs RoPE scaling (YaRN etc.).
    # Pass it through per-model when a shape's max_model_len exceeds the native
    # context, e.g. rope_scaling: {"rope_type":"yarn","factor":4.0,
    # "original_max_position_embeddings":32768}.
    if model.get("rope_scaling"):
        argv += ["--rope-scaling", json.dumps(model["rope_scaling"])]
    # DP=1-only compilation flags. Some vLLM compilation passes (notably
    # `fuse_allreduce_rms` + `eliminate_noops` on Gemma4 v0.20.0-cu130) HANG
    # when data-parallel-size > 1, but pay off single-server. Model-level
    # `compilation_config_dp1` is a dict passed through as JSON to
    # `--compilation-config` for dp==1 cells ONLY; dp>1 cells omit it. If your
    # image handles the flag with DP just fine, use `compilation_config` (below)
    # to apply it unconditionally. See bf16-run manifest for the concrete case.
    if dp == 1 and model.get("compilation_config_dp1"):
        argv += ["--compilation-config", json.dumps(model["compilation_config_dp1"])]
    elif model.get("compilation_config"):
        argv += ["--compilation-config", json.dumps(model["compilation_config"])]
    # Native-production config (E2E config doc): APC + chunked prefill ON by default —
    # what real deployments run. Both togglable in server.*.
    argv += (["--enable-prefix-caching"] if SERVER.get("enable_prefix_caching", True)
             else ["--no-enable-prefix-caching"])
    if SERVER.get("enable_chunked_prefill", True):
        argv += ["--enable-chunked-prefill"]
    # Quantization: FP8/AWQ/GPTQ WEIGHTS. Preferred = pre-quantized checkpoint (via
    # hf_id_by_regime → auto-detected, no flag). `regime.quantization` forces online
    # quant / names a method. Independent of KV.
    if regime.get("quantization"):
        argv += ["--quantization", str(regime["quantization"])]
    kvd = _kv_cache_dtype(regime)
    if kvd != "auto":
        argv += ["--kv-cache-dtype", str(kvd)]
    # Speculative decoding — verbatim vLLM --speculative-config; same image as the
    # no-spec regime, so the spec-vs-nospec comparison is engine-matched.
    if regime.get("speculative"):
        argv += ["--speculative-config", json.dumps(regime["speculative"])]
    if dp > 1:
        argv += ["--data-parallel-size", str(dp)]
    # Safetensors load strategy. Default (lazy mmap) auto-detects NFS and switches
    # to prefetch there; it's optimal for most models on most filesystems. But on
    # certain large-MoE + POSIX/ext4 shard layouts, the default's random-read pattern
    # deadlocks — DeepSeek V4-Flash on ext4 /scratch was the concrete case (see vLLM
    # issue #40988). Diagnostic signature: all workers idle in `hrtimer_nanosleep`
    # after "Using FP8 indexer cache for Lightning Indexer" (or an equivalent post-
    # kernel-selection point), GPU util 0%, per-worker `read_bytes: 0`, and no
    # "Loading safetensors checkpoint shards" line ever prints. Set model-level
    # `safetensors_prefetch: true` to force `--safetensors-load-strategy prefetch`;
    # V4-Flash on ext4 goes from 60+ min hang → 40s weight load per rank with this.
    if model.get("safetensors_prefetch"):
        argv += ["--safetensors-load-strategy", "prefetch"]
    # `x or y or []` (not .get(k, default)): a config with "extra_flags": null has the
    # key present, so .get returns None and iterating it raises TypeError. Matches the
    # `x or y or []` pattern used elsewhere in this file (_shardings_for / _concs_for).
    argv += [str(f) for f in (regime.get("extra_flags") or SERVER.get("extra_flags") or [])]
    return argv


# --- regime marker (so build_deliverables.py can attribute the run's regime) --

def _write_regime_meta(run_dir: Path, model: dict, regime: dict, *, ep: bool = False):
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "regime_meta.json"
    if meta.exists():
        return
    meta.write_text(json.dumps({
        "regime_name": regime["name"],
        # `prec` + `spec` form build_deliverables' regime_key = f"{prec}_{'spec'|'nospec'}".
        "prec": regime.get("prec", regime["name"]),
        "spec": bool(regime.get("speculative")),
        "quantization": regime.get("quantization"),
        "kv_cache_dtype": _kv_cache_dtype(regime),
        "hf_id": _hf_id(model, regime),
        "model_key": model["key"],
        "ep": ep,
    }, indent=2))


# --- the sweep test ----------------------------------------------------------

@pytest.mark.parametrize("cell", SWEEP, ids=_cell_id)
def test_sweep(cell, results_root: Path, server_handle_pool, sweep_state):
    model, regime, ep, e = cell
    cid = _cell_id(cell)

    # Each (model, regime, ep) is its own run dir; the ep suffix keeps EP-on/off
    # results separate so build_deliverables can attribute them. Dense models never
    # get the suffix (ep is always False when moe is off), so old paths unchanged.
    run_dir = results_root / f"{model['key']}__{regime['name']}{_ep_tag(ep)}"
    _write_regime_meta(run_dir, model, regime, ep=ep)

    # Resume: skip a cell that already PASSED (no server start). Requires a durable
    # ORCHESTRATOR_RESULTS_ROOT for cross-workload resume.
    if sweep_state.is_done(cid):
        pytest.skip("already passed (resume ledger)")

    # Default matches the config/docs: sharegpt is the native-production primary
    # workload (see SKILL.md "Config philosophy" and bench_config.example.json).
    dataset = WORKLOAD.get("dataset", "sharegpt")
    if dataset == "random":
        # `vllm bench serve --random-range-ratio R` jitters the input length UP TO
        # +R (e.g. R=0.8 → an ISL up to 1.8×isl). max_model_len must cover the
        # LONGEST jittered request or those cells overflow the window and fail — so
        # size from the jittered max, not nominal isl. (The old fixed 128/1024 buffer
        # overflowed at long context under jitter — see the Gemma run-#1 note.)
        rr = _range_ratio_for(e.isl, e.osl)
        jittered_isl = int(round(e.isl * (1.0 + max(rr, 0.0))))
        max_model_len = jittered_isl + e.osl + _len_buffer(jittered_isl)
    else:  # sharegpt: lengths come from the dataset, size from seq_len
        max_model_len = int(WORKLOAD.get("seq_len", 4096))
    # max-num-seqs: vLLM default 256 (E2E config doc) but never below the max conc
    # this shape is driven at, else high-conc cells queue against the cap.
    max_num_seqs = max(int(SERVER.get("max_num_seqs", 256)),
                       max(_concs_for(model, e.isl, e.osl)))

    server_argv = _server_argv(model, regime, tp=e.tp, dp=e.dp, ep=ep,
                               max_model_len=max_model_len, max_num_seqs=max_num_seqs)
    # Server start can FAIL to fit (OOM loading weights/KV) — common for large models
    # at low TP. IPA raises on start failure; catch it so the cell records FAILED and
    # SKIPs (not an unhandled ERROR that aborts the sweep). Every concurrency of this
    # (regime, sharding) will hit the same start failure and skip likewise — cheap,
    # but NOT free (each still tries to start a server), so prune non-fitting shardings
    # up front rather than relying on this. See SKILL.md "Sizing for large / MoE models".
    try:
        srv = server_handle_pool.get(server_argv)   # kaizen OR docker per --transport
    except Exception as exc:  # noqa: BLE001 — IPA start failures are RuntimeError/ConnectionError
        sweep_state.record(cid, status="failed", error=f"server start: {exc}")
        pytest.skip(f"server did not start (likely OOM / won't fit at this sharding): {exc}")

    # Floors (E2E config doc): num-prompts = max(conc*5, 50) so even conc=1 reaches
    # steady state; num-warmups = max(conc*2, 20) to clear CUDA-graph capture / JIT.
    n_prompts = max(e.conc * int(WORKLOAD.get("num_prompts_per_conc", 5)),
                    int(WORKLOAD.get("min_prompts", 50)))
    n_warmups = max(e.conc * int(WORKLOAD.get("warmups_per_conc", 2)),
                    int(WORKLOAD.get("min_warmups", 20)))
    bench_args = [
        "--num-prompts", str(n_prompts),
        "--max-concurrency", str(e.conc),
        "--num-warmups", str(n_warmups),
    ]
    if dataset == "random":
        bench_args += [
            "--dataset-name", "random",
            "--random-input-len", str(e.isl),
            "--random-output-len", str(e.osl),
            # Native-production default 0.8 (E2E config doc): realistic length spread.
            # Set 0.0 only for a strict matched-config run.
            "--random-range-ratio", str(_range_ratio_for(e.isl, e.osl)),
        ]
        if WORKLOAD.get("ignore_eos", True):
            bench_args += ["--ignore-eos"]
    else:  # sharegpt: variable lengths — NOT comparable to a bucketed Neuron run.
        bench_args += ["--dataset-name", "sharegpt",
                       "--dataset-path", str(WORKLOAD.get(
                           "dataset_path", "/scratch/sharegpt.json"))]
    if "temperature" in WORKLOAD:
        bench_args += ["--temperature", str(WORKLOAD["temperature"])]
    # --save-detailed retains per-request ITLs, input/output lens, ttfts,
    # start_times, generated_texts, errors in result.json (otherwise vLLM
    # deletes them post-run to save space). ON BY DEFAULT — the raw ITL arrays
    # are how downstream deliverables get p50/p99 distributions, per-request
    # TPOT (mean of a request's ITLs, matching vLLM's own tpot definition),
    # and the 10%-trimmed-mean TPOT that filters out post-saturation tail
    # requests. Cheap in disk (a few MB/cell) vs the analysis flexibility it
    # buys. Opt out with `"save_detailed": false` if disk is genuinely scarce.
    if CFG.get("save_detailed", True):
        bench_args += ["--save-detailed"]

    result = run_bench(
        srv,
        out_dir(run_dir, model["key"], e.isl, e.osl, e.tp, e.conc, dp=e.dp),
        bench_args=bench_args,
        timeout_s=BENCH_TIMEOUT_S,
    )
    if not result.success:
        # Record FAILED so resume RE-RUNS it (a transient error shouldn't be permanent).
        sweep_state.record(cid, status="failed",
                           error=result.data.get("error") or "bench failed")
        pytest.skip(result.data.get("exception") or result.data.get("error")
                    or "bench failed")

    # Failure-rate guard: a cell that "succeeded" but dropped a large fraction of
    # requests (KV exhaustion / saturation on thin-TP shardings at high conc) is not
    # a trustworthy point. result.json is still written + synced; this only excludes
    # it from clean results. Recorded as PASSED (a real, terminal outcome — the cell
    # DID run to completion at a saturating load) so resume doesn't re-run it; the
    # failed-fraction is captured for the downstream `usable` filter.
    completed = int(result.data.get("completed", 0) or 0)
    failed = int(result.data.get("failed", 0) or 0)
    total = completed + failed
    fail_frac = (failed / total) if total else 1.0
    sweep_state.record(cid, status="passed",
                       fail_frac=round(fail_frac, 3),
                       otps=result.data.get("output_throughput"))
    if fail_frac > FAIL_THRESHOLD:
        pytest.skip(
            f"high failure rate: {failed}/{total} ({fail_frac:.0%}) — likely "
            f"saturation/KV-exhaustion, not a clean data point"
        )
