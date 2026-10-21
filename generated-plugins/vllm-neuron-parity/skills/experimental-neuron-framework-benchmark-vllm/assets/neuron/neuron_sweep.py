#!/usr/bin/env python3
"""Config-driven vLLM-Neuron serving sweep — the Trainium peer of the GPU sweep_bench.py.

Runs INSIDE a Kaizen pod (Desktop or workload), where the pod itself is the serving
container. For every model x regime x shape x (tp,dp) x concurrency cell in
neuron_bench_config.json this:

  - builds a vLLM-NEURON `vllm serve` argv (TP x DP + `--additional-config` carrying
    the merged neuron_config: compiled buckets + precision/quant + spec/EP/CP),
  - drives IPA's framework-agnostic core — `start_vllm_server` / `stop_vllm_server`
    (kaizen.py) + `run_bench` (primitives) — through a `KaizenServer` handle, so the
    SAME four-file output layout (result.json + metrics_*.txt + run_manifest.json)
    the GPU sweep produces lands here too, ingestible by build_deliverables.py
    UNCHANGED (IPA's `server_tag(tp, dp)` emits `tp<N>` at dp=1 and `tp<N>_dp<M>`
    above it, and build_deliverables' _PATH_RE already parses the optional `_dp<M>`),
  - writes a regime_meta.json per <key>__<regime> run dir (deliverables read it),
  - keeps a resume ledger (state.json at the results root) so a re-run skips PASSED
    cells — point --results-root at a DURABLE FSx path for cross-workload resume.

WHY NOT PYTEST (unlike the GPU side): the GPU sweep rides IPA's pytest conftest for
the --transport plumbing and CUDA server pool. On Neuron the pod IS the server, so we
use IPA's documented library seam (kaizen.py's `start_vllm_server`/`run_one_cell`
pattern) directly and skip the CUDA-specific fixtures entirely.

THE THREE TRAINIUM DIVERGENCES this driver encodes (see SKILL.md §B):
  1. FIXED-SHAPE BUCKETING. Neuron compiles a NEFF per bucket. We derive
     num_batched_tokens_buckets from the shapes' ISLs and num_seqs_buckets from the
     concurrencies, compile ALL of them into ONE server per (model,regime,tp,dp,ep),
     and reuse that server across the whole shape x conc sweep — so multi-minute
     compile is paid ONCE per sharding, not per cell. An off-bucket request pads up;
     an over-max request is rejected — hence the fit-check + max_model_len sizing.
  2. SHARDING DOES NOT HAVE TO FILL THE BOX. Trainium sweeps the same {tp,dp} axis as
     GPU (`--tensor-parallel-size` x `--data-parallel-size`, dp>1 = replica DP, one
     endpoint, vLLM-Neuron load-balancing across dp engines and coordinating per-step
     token counts in NeuronModelRunner._get_dp_padding). What DIVERGES is the
     constraint: GPU REQUIRES tp*dp == gpu_count ("every run must fill the box"),
     Trainium only requires tp*dp <= logical cores. A tp8_dp1 server occupies 8 of
     trn2.48xlarge's 64 logical cores and leaves 56 IDLE — legal, but then the cell's
     throughput is NOT a whole-box number (see _box_fill_warning + SKILL.md §C).
     EP/CP degrees still live in neuron_config; note the plugin requires
     ep_degree == tp*dp whenever EP is on AND dp>1 (see parallelism_error).
  3. UP-FRONT FIT-CHECK. A non-fitting cell on Neuron costs MINUTES of compile before
     it OOMs at load — not a cheap skip like GPU. So we reject an unfittable bucket
     set at parse time, before a single core is provisioned.

Run (inside the pod)::

    python3 neuron_sweep.py --config neuron_bench_config.json \\
        --results-root "$FSX_TEAM_SHARED_RW/<alias>/neuron-results"

    python3 neuron_sweep.py --config c.json --results-root /out --collect-only  # preview cells
    python3 neuron_sweep.py --config c.json --results-root /out -k "bf16 and tp32"  # subset

Then `kaizen get-artifact` the results tree out and run build_deliverables.py --multi
--instance trn2 (trn2 is unpriced → cost deliverable skips it; throughput/latency are fine).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# IPA core — imported from the bench image (per the "import IPA in the bench image"
# decision). All of these are framework-AGNOSTIC: kaizen.py spawns `vllm serve` as a
# subprocess and drives run_bench through a KaizenServer; the only GPU-specific code
# in that path (_log_gpu_memory via nvidia-smi) is best-effort and no-ops on Neuron.
from inference_perf_analyzer.orchestrator.kaizen import (
    SERVER_PORT,
    KaizenServer,
    stop_vllm_server,
)
from inference_perf_analyzer.orchestrator.paths import out_dir, server_tag
from inference_perf_analyzer.orchestrator.primitives.bench import run_bench
from inference_perf_analyzer.orchestrator.sweep_state import (
    SweepState,
    config_signature,
)

import subprocess
import time
import urllib.request

log = logging.getLogger("neuron_sweep")


# --- server start (LOCAL-FILE stdout — NOT IPA's start_vllm_server) ----------
# IPA's start_vllm_server spawns the server with stdout=subprocess.PIPE and never
# drains it while polling /health. On Neuron the first server start does a MULTI-MINUTE
# NEFF compile that writes far more than the ~64KB pipe buffer to stdout, so the pipe
# fills and the server DEADLOCKS on pipe_write (confirmed on the first trn2 run: main
# thread stuck in pipe_write, 0 forward progress). So we start the server ourselves and
# redirect stdout+stderr to a LOCAL pod file (never a pipe, never the S3-backed FSx home
# whose write latency also stalls the writer). Teardown reuses IPA's stop_vllm_server.

def start_server_to_file(argv: List[str], *, port: int, timeout_s: int,
                         log_path: Path,
                         extra_env: Optional[Dict[str, str]] = None) -> subprocess.Popen:
    """Spawn `vllm serve` with stdout+stderr → log_path (a LOCAL file), poll /health.

    Raises RuntimeError if the process exits or timeout_s elapses before /health is 200.
    The open file object is intentionally leaked to the child (closed on process exit);
    we keep the server's log on disk so a compile that emits MBs never blocks on a pipe.

    `extra_env` is layered onto the inherited environment for the child only (used to
    point the server at a pre-warmed remote NEFF cache — see build_server_env).
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_path, "wb")
    child_env = None
    if extra_env:
        child_env = dict(os.environ)
        child_env.update(extra_env)
        log.info("server env += %s", {k: v for k, v in extra_env.items()})
    log.info("Spawning vLLM server (stdout→%s): %s", log_path, " ".join(argv[:6]) + " …")
    proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT,
                            start_new_session=True, env=child_env)
    health = f"http://localhost:{port}/health"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            # Capture a DEEP tail: vLLM v1 prints the engine-core worker's real error
            # (NRT/OOM/arch) then a long "Engine core initialization failed. See root
            # cause above" Python traceback — the root cause is often >100 lines up. This
            # tail is embedded into the FSx sweep.log (survives pod death; /tmp does not).
            tail = _tail_file(log_path, 250)
            raise RuntimeError(f"vLLM (pid={proc.pid}) exited rc={rc} before healthy. "
                               f"Last log lines:\n{tail}")
        try:
            with urllib.request.urlopen(health, timeout=5) as r:
                if r.status == 200:
                    log.info("server healthy on port %d (pid=%d)", port, proc.pid)
                    return proc
        except Exception:  # noqa: BLE001 — not up yet
            pass
        time.sleep(10)
    tail = _tail_file(log_path, 250)
    stop_vllm_server(proc, port=port)
    raise RuntimeError(f"vLLM (pid={proc.pid}) not healthy within {timeout_s}s. "
                       f"Last log lines:\n{tail}")


def _server_healthy(port: int, timeout_s: float = 5.0) -> bool:
    """Quick /health probe — True iff the server answers 200. Used as a pre-cell wedge
    guard (a rescheduled pod can leave the server pid alive but not serving)."""
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=timeout_s) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _tail_file(path: Path, n: int) -> str:
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:  # noqa: BLE001
        return "(no log)"


def _prefix_hit_rate(cell_dir: Path) -> Optional[float]:
    """Prefix-cache hit rate for a cell, parsed from run_bench's metrics_after.txt.

    This is the PROOF a cell's TTFT is cold (hit≈0) vs served from a warm prefix cache
    (the Devstral footgun: a reused APC-on server + fixed-seed prompts makes cells 2+ warm,
    silently understating TTFT). We surface it per cell so an APC-on number is auditable.
    vLLM exposes `vllm:gpu_prefix_cache_hit_rate` (gauge) on both platforms (the metric
    name is CUDA-flavored but the plugin reports it on Neuron too); fall back to a
    hits/queries ratio if only the counters are present. None if not found (APC off / no metric).
    """
    txt = _tail_file(cell_dir / "metrics_after.txt", 100000)
    if txt == "(no log)":
        return None
    hits = queries = None
    for line in txt.splitlines():
        if line.startswith("#"):
            continue
        try:
            name, val = line.rsplit(" ", 1)
            v = float(val)
        except ValueError:
            continue
        base = name.split("{", 1)[0]
        if base.endswith("gpu_prefix_cache_hit_rate") or base.endswith("prefix_cache_hit_rate"):
            return round(v, 4)
        if base.endswith("gpu_prefix_cache_hits_total") or base.endswith("prefix_cache_hits_total"):
            hits = (hits or 0.0) + v
        elif base.endswith("gpu_prefix_cache_queries_total") or base.endswith("prefix_cache_queries_total"):
            queries = (queries or 0.0) + v
    if queries:
        return round(hits / queries, 4) if hits is not None else 0.0
    return None

# trn2.48xlarge: 16 chips x 128 physical cores, LNC=2 -> 64 LOGICAL cores. A tp beyond
# this can't be scheduled. Keyed by instance so a future trn3 just adds a row.
_LOGICAL_CORES = {
    "trn2.48xlarge": 64,
    "trn1.32xlarge": 32,
}
# Approx per-chip HBM (GB) for the coarse fit-check. Deliberately conservative — the
# check is a guard against obviously-unfittable bucket sets, not an exact planner.
_HBM_GB_PER_CORE = {
    "trn2.48xlarge": 24.0,   # ~96 GB/chip over 4 logical cores/chip (LNC=2, 2 chips? — see note)
    "trn1.32xlarge": 16.0,
}
# Prefill-bucket stride: ISLs are padded UP to a multiple of this before being used as
# a compiled prefill bucket, so nearby ISLs share a NEFF (fewer compiles).
_PREFILL_STRIDE = 512
# Supported vLLM-Neuron chunked-prefill sizes (the plugin's bucket_utils accepts only
# these for --max-num-batched-tokens; a long prefill is CHUNKED into steps of this size,
# so num_batched_tokens_buckets are chunk sizes ≤8192, not raw ISLs).
_NEURON_CHUNK_SIZES = [512, 1024, 2048, 4096, 8192]


# --- config load ------------------------------------------------------------


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(
            f"neuron bench config not found: {path} — copy "
            "neuron_bench_config.example.json to start."
        )
    cfg = json.loads(path.read_text())
    # Drop the _comment/_field helper keys so they never leak into logic.
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


# --- axis resolution --------------------------------------------------------


def _shardings(cfg: Dict[str, Any],
               model: Dict[str, Any]) -> List[Tuple[int, int, Optional[bool]]]:
    """The sharding axis for a model: a list of (tp, dp, ep_override) triples.

    Two accepted config shapes, in precedence order:
      1. `shardings`: [{"tp": 16, "dp": 4}, {"tp": 64, "dp": 1, "ep": true}] — the SAME
         key and shape as the GPU driver's `shardings`, so one authored sweep plan ports
         across platforms. `dp` defaults to 1. `ep` is optional and, when present,
         OVERRIDES the `ep_list` sweep for that entry only (per-sharding EP, exactly like
         GPU); when absent the entry is crossed with `ep_list` as before.
      2. `tp_list`: [32, 16, 8] — the original Neuron-only key. Kept for BACKWARD
         COMPATIBILITY: each entry becomes (tp, 1, None), so every existing
         neuron_bench_config.json keeps producing byte-identical cell-ids and paths.

    WHAT dp MEANS HERE: vLLM-Neuron supports vLLM's `--data-parallel-size`. dp>1 loads a
    FULL model replica per DP rank, each replica owning its own disjoint window of tp
    logical cores (the plugin computes tp_global_ranks = range(dp_rank*tp,
    (dp_rank+1)*tp) and a global world of tp*dp), with one endpoint in front and the
    engine load-balancing requests across replicas + synchronizing per-step batch sizes
    (NeuronModelRunner._get_dp_padding). So DP buys throughput at the same per-user
    latency; it does NOT shrink per-replica weight bytes the way TP does.

    DIVERGENCE FROM GPU: the GPU driver REQUIRES tp*dp == gpu_count ("every run must fill
    the box"). Trainium only requires tp*dp <= logical cores — an under-filling sharding
    is LEGAL here (it just idles cores) because "what does a fraction of the box do?" is a
    real Trainium question. run_sweep warns on every under-filling sharding so its
    throughput is never mistaken for a whole-box number (see _box_fill_warning).
    """
    raw = model.get("shardings") or cfg.get("shardings")
    if raw:
        out: List[Tuple[int, int, Optional[bool]]] = []
        for s in raw:
            tp, dp = int(s["tp"]), int(s.get("dp", 1))
            if tp < 1 or dp < 1:
                raise SystemExit(
                    f"sharding {s!r} for model {model.get('key', '?')}: tp and dp must "
                    "both be >= 1.")
            ep = s.get("ep")
            out.append((tp, dp, None if ep is None else bool(ep)))
        return out
    raw = model.get("tp_list") or cfg.get("tp_list")
    if raw:
        return [(int(t), 1, None) for t in raw]
    raise SystemExit(
        f"no sharding axis for model {model.get('key', '?')}: set `shardings` "
        '(e.g. [{"tp": 16, "dp": 4}], per-model or global) or the legacy `tp_list`.'
    )


def _ep_values(cfg: Dict[str, Any], model: Dict[str, Any]) -> List[bool]:
    """Expert-parallel values to SWEEP for a model — the Neuron peer of the GPU
    driver's per-sharding `ep` axis. `ep_list` (per-model or global) lets you compare
    EP on vs off as separate cells (e.g. [true, false]); when unset, defaults to a
    single value = the model's `moe` flag (MoE→EP on, dense→off), so dense models are
    unaffected. Only meaningful for MoE models."""
    raw = model.get("ep_list")
    if raw is None:
        raw = cfg.get("ep_list")
    if raw is None:
        return [bool(model.get("moe"))]
    return [bool(x) for x in raw]


def _ep_tag(ep: bool) -> str:
    # Match the GPU driver: only append the marker when EP is ON, so dense models and
    # existing MoE-flag configs keep their cell-ids / run-dirs unchanged.
    return "_ep" if ep else ""


def _concs_for(cfg: Dict[str, Any], model: Dict[str, Any], isl: int, osl: int) -> List[int]:
    per = model.get("concs_by_shape") or cfg.get("concs_by_shape") or {}
    key = f"{isl},{osl}"
    if key in per:
        return [int(c) for c in per[key]]
    return [int(c) for c in model.get("concurrencies", cfg["concurrencies"])]


def _regimes_for(cfg: Dict[str, Any], model: Dict[str, Any]) -> List[Dict[str, Any]]:
    regimes = cfg.get("regimes") or [{"name": "bf16", "prec": "bf16",
                                      "neuron_config": {"quantization": "bf16"}}]
    names = model.get("regimes")
    if names is not None:
        by_name = {r["name"]: r for r in regimes}
        regimes = [by_name[n] for n in names]
    # Controlled baseline (Performance Milestones doc, PRIMARY) forbids spec-decode —
    # a spec regime under 'controlled' is a config error, not a silent downgrade.
    if _baseline(cfg) == "controlled":
        spec = [r["name"] for r in regimes if r.get("speculative")]
        if spec:
            raise SystemExit(
                f"baseline=controlled forbids speculative decoding, but regime(s) "
                f"{spec} set 'speculative'. The controlled milestone baseline is "
                "APC+chunked+spec all OFF. Move spec regimes to a baseline=production "
                "run, or drop 'speculative' from them.")
    return regimes


def _baseline(cfg: Dict[str, Any]) -> str:
    b = cfg.get("baseline", "controlled")
    if b not in ("controlled", "production"):
        raise SystemExit(f"baseline must be 'controlled' or 'production', got {b!r}")
    return b


def apc_on_for(cfg: Dict[str, Any]) -> bool:
    """Whether APC (prefix caching) is actually ON for this run — production baseline AND
    the server config opting in. Single source of truth for both the serve argv and the
    prefix-cache footgun guard."""
    return (_baseline(cfg) == "production"
            and bool(cfg.get("server", {}).get("enable_prefix_caching", False)))


def _hf_id(model: Dict[str, Any], regime: Dict[str, Any]) -> str:
    """The HF id that labels this (model, regime) — ALWAYS required, even when weights
    are served from a local checkpoint dir: it is the bench client's `--model` and the
    server's `--served-model-name`, and the deliverables/ledger key on it."""
    return model.get("hf_id_by_regime", {}).get(regime["name"], model["hf_id"])


def _weights_source(model: Dict[str, Any], regime: Dict[str, Any]) -> Tuple[str, bool]:
    """Resolve WHERE weights load from → (serve_path, is_local).

    Weights and the HF-id label are separate: `checkpoint_path` (a local/FSx dir already
    staged into the pod) is served in place of downloading from HF, but `hf_id` is still
    the served-model-name so the bench client's `--model <hf_id>` matches. Per-regime
    override via `checkpoint_path_by_regime` (e.g. an fp8-converted dir for the fp8 regime).
    Returns (path_or_id_to_serve, is_local_checkpoint).
    """
    ckpt = (model.get("checkpoint_path_by_regime", {}).get(regime["name"])
            or model.get("checkpoint_path"))
    if ckpt:
        return str(ckpt), True
    return _hf_id(model, regime), False


def _all_isls(cfg: Dict[str, Any]) -> List[int]:
    return sorted({int(s["isl"]) for s in cfg["shapes"]})


def _all_concs(cfg: Dict[str, Any], model: Dict[str, Any]) -> List[int]:
    seen = set()
    for s in cfg["shapes"]:
        for c in _concs_for(cfg, model, int(s["isl"]), int(s["osl"])):
            seen.add(int(c))
    return sorted(seen)


def _len_buffer(isl: int) -> int:
    if isl <= 1000:
        return 128
    if isl <= 10000:
        return 512
    return 1024


def _max_model_len(cfg: Dict[str, Any]) -> int:
    server = cfg.get("server", {})
    if server.get("max_model_len"):
        return int(server["max_model_len"])
    worst = max(int(s["isl"]) + int(s["osl"]) for s in cfg["shapes"])
    return worst + _len_buffer(max(int(s["isl"]) for s in cfg["shapes"]))


def _max_num_seqs(cfg: Dict[str, Any], model: Dict[str, Any]) -> int:
    server = cfg.get("server", {})
    concs = _all_concs(cfg, model)
    floor = max(concs) if concs else 1
    if server.get("max_num_seqs"):
        return max(int(server["max_num_seqs"]), floor)
    return floor


# --- bucket derivation ------------------------------------------------------


def _ceil_to(v: int, stride: int) -> int:
    return ((v + stride - 1) // stride) * stride


def derive_buckets(cfg: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, List[int]]:
    """Derive the compiled NEFF bucket sets (or take explicit overrides).

    num_batched_tokens_buckets  = prefill graphs — ISLs padded up to _PREFILL_STRIDE.
    num_seqs_buckets            = decode-batch graphs — the unique concurrencies
                                  (the CONCURRENT-SEQ count, NOT seq length).
    decode_context_length_buckets = per-(batch,ctx) decode graphs — max request len.

    Explicit config `buckets.*` (non-null) wins, so a user can add/pin buckets.
    """
    buckets = cfg.get("buckets", {}) or {}
    isls = _all_isls(cfg)
    concs = _all_concs(cfg, model)
    max_len = _max_model_len(cfg)

    # Three-way semantics per bucket key:
    #   absent OR null -> AUTO-DERIVE (null is how the JSON template says "derive this"
    #                     while keeping the documented key visible),
    #   []             -> OMIT the key entirely (match a validated neuron_config that
    #                     never set it, rather than force our derived value in),
    #   [ints]         -> use verbatim.
    # A sentinel distinguishes "absent" from "[]"; `is None` folds null into auto-derive.
    _MISSING = object()

    def _cfg_bucket(name: str) -> Any:
        v = buckets.get(name, _MISSING)
        return _MISSING if v is None else v

    ntb = _cfg_bucket("num_batched_tokens_buckets")
    if ntb is _MISSING:
        # num_batched_tokens_buckets are PREFILL-CHUNK sizes, NOT raw ISLs — a long ISL
        # is served by repeated chunks of size max_num_batched_tokens (≤8192). BUT the
        # segmented-prefill kernel requires num_batched_tokens_buckets to be a SINGLE
        # bucket equal to the segment size (== max_num_batched_tokens == the chunk size):
        # "the segmented kernel currently requires the prefill bucket length to equal the
        # segment size" (bucket_utils.validate_kv_segment_size_buckets). A multi-entry
        # list [512,1024,...,8192] is REJECTED. So emit ONE chunk size — the largest
        # supported chunk an ISL needs (capped at 8192).
        max_isl = max(isls) if isls else 512
        target = _ceil_to(max_isl, _PREFILL_STRIDE)
        chunk = min((c for c in _NEURON_CHUNK_SIZES if c >= target),
                    default=_NEURON_CHUNK_SIZES[-1])
        chunk = min(chunk, _NEURON_CHUNK_SIZES[-1])  # cap at 8192
        ntb = [chunk]

    nsb = _cfg_bucket("num_seqs_buckets")
    if nsb is _MISSING:
        nsb = list(concs)

    dclb = _cfg_bucket("decode_context_length_buckets")
    if dclb is _MISSING:
        # decode_context_length_buckets = the per-request total context (isl+osl) each
        # shape decodes at, padded to a stride. The plugin treats max_model_len as an
        # IMPLICIT final fallback bucket, so every explicit entry must be STRICTLY LESS
        # than max_model_len ("decode_context_length_buckets[i] must be < max_model_len").
        # max_len carries a buffer above the largest isl+osl, so the padded contexts sit
        # below it; clamp to max_len-stride defensively in case a shape lands exactly on it.
        ceiling = max_len - _PREFILL_STRIDE
        ctxs = {min(_ceil_to(int(s["isl"]) + int(s["osl"]), _PREFILL_STRIDE), ceiling)
                for s in cfg["shapes"]}
        dclb = sorted(c for c in ctxs if c > 0)

    return {
        "num_batched_tokens_buckets": [int(x) for x in ntb],
        "num_seqs_buckets": [int(x) for x in nsb],
        "decode_context_length_buckets": [int(x) for x in dclb],
    }


# --- fit-check (BEFORE provisioning — a bad cell costs minutes of compile) ----


def _kv_bytes_per_token(model: Dict[str, Any], regime: Dict[str, Any]) -> float:
    """Coarse KV bytes/token/layer estimate. Overridable via model.kv_bytes_per_token
    for models whose config the driver can't see (it has no HF config here)."""
    if model.get("kv_bytes_per_token"):
        return float(model["kv_bytes_per_token"])
    # Fallback heuristic: assume a mid-size dense model. This is intentionally rough —
    # the check exists to catch order-of-magnitude misfits, not to plan exactly.
    # 2 (K+V) * 2 bytes (bf16) * ~8 KV-heads * 128 head-dim * ~32 layers ≈ 0.26 MB/tok.
    return 0.26e6


def _peak_kv_tokens(cfg: Dict[str, Any], model: Dict[str, Any]) -> Tuple[int, str]:
    """Peak simultaneous KV residency (tokens) per replica, and the shape that drives it.

    The sweep runs shape-by-shape, and at any instant every loaded request belongs to
    ONE cell's shape at ONE concurrency — so peak residency is the MAX OVER SHAPES of
    (max concurrency for that shape) x (that shape's context = isl+osl). It is NOT the
    cross-product of the global-max concurrency and the global-max context: concs_by_shape
    caps the long shape's concurrency precisely so those two maxima don't co-occur (e.g.
    conc=32 only on the 8k shape, conc=16 on the 32k shape — never 32 seqs at 32k).

    DELIBERATELY NOT DIVIDED BY dp. In steady state a DP replica only serves ~conc/dp of
    the requests, so /dp would be the "typical" number — but --max-num-seqs is a PER-ENGINE
    setting and we keep it at max(concurrencies) (see _max_num_seqs), so each replica still
    SIZES its KV pool for the full concurrency. Dividing here would make the check
    OPTIMISTIC and let through exactly the configs it exists to reject (compile for
    minutes, then OOM at KV allocation).
    """
    peak, which = 0, ""
    for s in cfg["shapes"]:
        isl, osl = int(s["isl"]), int(s["osl"])
        ctx = isl + osl
        max_conc = max(_concs_for(cfg, model, isl, osl))
        tokens = max_conc * ctx
        if tokens > peak:
            peak, which = tokens, f"{isl}/{osl} @ conc {max_conc}"
    return peak, which


def _effective_ep_degree(model: Dict[str, Any], regime: Dict[str, Any],
                         tp: int, dp: int) -> int:
    """The ep_degree the server will actually get — an explicit pin if there is one, else
    the driver's default of tp*dp. Mirrors build_neuron_config's precedence exactly
    (model.neuron_config_extra beats regime.neuron_config beats the default)."""
    for src in (model.get("neuron_config_extra") or {}, regime.get("neuron_config") or {}):
        if "ep_degree" in src:
            return int(src["ep_degree"])
    return tp * dp


def parallelism_error(cfg: Dict[str, Any], model: Dict[str, Any], regime: Dict[str, Any],
                      tp: int, dp: int, ep: bool) -> Optional[str]:
    """Return an error string if this (tp, dp, ep) combination is ILLEGAL, else None.

    ALWAYS RUNS — deliberately separate from fit_check, which early-returns on an unknown
    instance / skip_fit_check / num_gpu_blocks_override. These are plugin CONTRACT
    violations, not capacity estimates: the NeuronWorker raises at init, so catching them
    at parse time saves a provision plus multi-minute compile.
    """
    instance = cfg.get("instance", "trn2.48xlarge")
    cores = _LOGICAL_CORES.get(instance)
    if cores is not None and tp * dp > cores:
        return (f"{server_tag(tp, dp)} needs {tp * dp} logical cores (tp {tp} x dp {dp}) "
                f"but {instance} has {cores}. Each DP rank gets its OWN disjoint window "
                f"of tp cores (the plugin sets tp_global_ranks = range(dp_rank*tp, "
                f"(dp_rank+1)*tp) over a global world of tp*dp), so cores MULTIPLY across "
                f"dp — replicas do not share them.")
    # Plugin rule (NeuronWorker._init_neuron_distributed_environment_and_runtime): with EP
    # enabled AND dp>1, the expert-parallel degree must span the whole global world (tp*dp)
    # or the worker raises ValueError. So the dp=1-era default of ep_degree==tp becomes
    # illegal the moment dp>1 — which is why build_neuron_config now defaults it to tp*dp.
    if ep and dp > 1:
        eff = _effective_ep_degree(model, regime, tp, dp)
        if eff != tp * dp:
            return (f"{server_tag(tp, dp)} has EP on with dp={dp}, so vLLM-Neuron requires "
                    f"ep_degree == tp*dp == {tp * dp}, but the pinned ep_degree is {eff}. "
                    f"Unpin ep_degree (the driver then defaults it to tp*dp), set it to "
                    f"{tp * dp}, or use dp=1.")
    return None


def _box_fill_warning(cfg: Dict[str, Any], tp: int, dp: int) -> Optional[str]:
    """Warn when a sharding leaves cores idle. Legal on Trainium (unlike GPU, which
    enforces tp*dp == gpu_count), but then the cell's tok/s is a PARTIAL-BOX number and
    comparing it per-instance against a full-box GPU cell understates Trainium."""
    instance = cfg.get("instance", "trn2.48xlarge")
    cores = _LOGICAL_CORES.get(instance)
    if cores is None or tp * dp >= cores:
        return None
    hint = f" To fill the box, use dp={cores // tp}." if cores % tp == 0 else ""
    return (f"{server_tag(tp, dp)} uses {tp * dp} of {instance}'s {cores} logical cores — "
            f"{cores - tp * dp} sit IDLE. This is legal on Trainium (the GPU sweep instead "
            f"requires tp*dp == gpu_count), but this cell's throughput is a PARTIAL-BOX "
            f"number: do not compare it per-instance against a full-box GPU cell.{hint}")


def fit_check(cfg: Dict[str, Any], model: Dict[str, Any], regime: Dict[str, Any],
              tp: int, buckets: Dict[str, List[int]], dp: int = 1) -> Optional[str]:
    """Return an error string if (model, regime, tp, dp) obviously won't fit, else None.

    The failure mode on Neuron is: compile all buckets (minutes) THEN OOM at KV
    allocation. This coarse check rejects the clearly-unfittable up front. It is
    conservative and best-effort — pass model.kv_bytes_per_token for precision (the
    generic default assumes MHA and OVER-estimates GQA models ~2x), or set
    num_gpu_blocks_override to bypass the pool-sizing estimate entirely.

    Core-budget and EP-degree legality live in parallelism_error(), which is checked
    separately BECAUSE it must not be skippable by the escape hatches below.
    """
    instance = cfg.get("instance", "trn2.48xlarge")
    cores = _LOGICAL_CORES.get(instance)
    if cores is None:
        return None  # unknown instance — don't block, just skip the check
    if cfg.get("skip_fit_check"):
        return None  # caller wants HARDWARE (OOM / failure-guard) to decide saturation,
                     # not the coarse KV estimate — for bracketing the saturation knee.
    if cfg.get("num_gpu_blocks_override") or model.get("num_gpu_blocks_override"):
        return None  # user sized the pool explicitly — trust them

    hbm_per_core = _HBM_GB_PER_CORE.get(instance, 24.0)
    # A TP=N shard occupies N logical cores and its weights+KV are spread across ONLY
    # those N cores — NOT the whole box. So the HBM budget scales with tp, which is
    # why LOW tp is where OOM bites (fewer cores → less HBM for the same KV pool).
    # dp does NOT enter the budget on EITHER side. Numerator: each replica keeps a KV pool
    # sized for max_num_seqs, which stays at the full max(concurrencies) (see
    # _peak_kv_tokens). Denominator: a DP replica is a FULL model copy living on its own tp
    # cores, so its HBM is one tp-shard's HBM no matter how many replicas exist. Net effect:
    # the per-replica fit is IDENTICAL at every dp, which is why the check is dp-invariant —
    # dp is validated for core budget in parallelism_error() instead.
    shard_hbm_gb = hbm_per_core * tp
    peak_tokens, driver = _peak_kv_tokens(cfg, model)
    kv_bpt = _kv_bytes_per_token(model, regime)
    kv_gb = (peak_tokens * kv_bpt) / 1e9
    # leave ~40% of the shard's HBM for weights + activations + compiler scratch
    kv_ceiling = 0.60 * shard_hbm_gb
    if kv_gb > kv_ceiling:
        return (f"peak KV pool ~{kv_gb:.0f} GB/replica (driven by shape {driver}) exceeds "
                f"~{kv_ceiling:.0f} GB budget at tp={tp} (~{shard_hbm_gb:.0f} GB over "
                f"{tp} cores) on {instance}. Lower that shape's top concurrency "
                f"(concs_by_shape), shrink it, raise tp, or set num_gpu_blocks_override. "
                f"(Raising dp will NOT help — every replica sizes its own pool for the "
                f"same max_num_seqs.)")
    return None


# --- neuron_config + serve argv ---------------------------------------------


def build_neuron_config(cfg: Dict[str, Any], model: Dict[str, Any],
                        regime: Dict[str, Any], buckets: Dict[str, List[int]],
                        tp: int = 1, ep: bool = False, dp: int = 1) -> Dict[str, Any]:
    """Assemble the neuron_config dict for --additional-config.

    Precedence (low→high): derived buckets → regime.neuron_config → model.neuron_config_extra
    → spec-decode mapping. Precision/quant/EP/CP are expressed HERE (not via CUDA
    flags), matching the vLLM-Neuron plugin's --additional-config contract (the stub
    bench_one_model.sh's `{"neuron_config": {...}}` shape).
    """
    # compiled bucket sets first — but drop any whose value is an empty list, so a config
    # can OMIT a bucket key entirely (e.g. `decode_context_length_buckets: []` to match a
    # validated neuron_config that never set it, rather than force our derived value in).
    nc: Dict[str, Any] = {k: v for k, v in buckets.items() if v not in ([], None)}

    # PARITY: translate GPU-style regime keys into their neuron_config equivalents so a
    # config authored for the GPU sweep (shared shapes/regimes — the whole point of the
    # skill) applies correctly on Neuron instead of silently no-op'ing. GPU puts these
    # at the regime TOP LEVEL (regime.kv_cache_dtype / regime.quantization); we fold them
    # in UNLESS the explicit neuron_config already set them (explicit wins).
    if regime.get("quantization") and "quantization" not in (regime.get("neuron_config") or {}):
        nc["quantization"] = str(regime["quantization"])
    if regime.get("kv_cache_dtype") and "kv_cache_dtype" not in (regime.get("neuron_config") or {}):
        # FP8 KV cache: GPU sets kv_cache_dtype:fp8; the Neuron plugin's neuron_config
        # takes the same key. (If a given Neuron build names it differently, override via
        # the regime's explicit neuron_config.)
        nc["kv_cache_dtype"] = str(regime["kv_cache_dtype"])

    # Expert parallelism degree: when EP is swept ON and neither the regime's
    # neuron_config nor neuron_config_extra pins ep_degree, default it to the GLOBAL world
    # size tp*dp (shard the experts across every rank the server owns — the common case,
    # and at dp=1 this is the previous behaviour of tp, unchanged). This is not just a
    # preference at dp>1: with EP enabled the plugin REQUIRES ep_degree == tp*dp and the
    # NeuronWorker raises ValueError otherwise (parallelism_error catches a bad pin early).
    # Explicit config still wins below.
    if ep and "ep_degree" not in (regime.get("neuron_config") or {}) \
            and "ep_degree" not in (model.get("neuron_config_extra") or {}):
        nc["ep_degree"] = int(tp) * int(dp)

    nc.update(regime.get("neuron_config") or {})       # explicit neuron_config wins
    nc.update(model.get("neuron_config_extra") or {})  # per-model override wins over all

    # Spec-decode: vLLM-Neuron does NOT accept GPU's --speculative-config verbatim.
    # Map the portable regime.speculative → neuron_config speculation fields.
    spec = regime.get("speculative")
    if spec:
        k = spec.get("num_speculative_tokens")
        if k is not None:
            nc["speculation_length"] = int(k)
        nc["enable_fused_speculation"] = True
        if spec.get("model"):
            nc.setdefault("draft_model_id", spec["model"])
    return nc


def build_serve_argv(cfg: Dict[str, Any], model: Dict[str, Any], regime: Dict[str, Any],
                     tp: int, buckets: Dict[str, List[int]], *, port: int,
                     ep: bool = False, force_hf: bool = False, dp: int = 1) -> List[str]:
    """`vllm serve …` argv for a vLLM-Neuron server (TP x DP + --additional-config).

    Weights: serve a local `checkpoint_path` in place (no HF download) when set — but
    then MUST pass `--served-model-name <hf_id>` so the bench client's `--model <hf_id>`
    matches (else the server 404s / completed=0, per the skill's failure table).
    `force_hf=True` ignores the local path and serves the HF id (the fallback path)."""
    server = cfg.get("server", {})
    max_len = _max_model_len(cfg)
    max_seqs = _max_num_seqs(cfg, model)
    neuron_config = build_neuron_config(cfg, model, regime, buckets, tp=tp, ep=ep, dp=dp)
    # --max-num-batched-tokens on Neuron = the scheduler CHUNK size, and the plugin only
    # accepts a fixed set of chunk sizes: [512, 1024, 2048, 4096, 8192] (bucket_utils
    # resolve_segmented_prefill_config raises "not a supported chunked prefill size"
    # otherwise). It is NOT "the largest prefill bucket" — a long ISL (e.g. 32768) is
    # CHUNKED into ≤8192-token steps, not prefilled in one. So clamp to the largest
    # supported chunk ≤ our max prefill bucket. (When chunked prefill is fully OFF the
    # plugin instead wants last-bucket==max_num_batched_tokens, but the controlled
    # baseline still schedules prefill in these chunk sizes, so this holds either way.)
    # derive_buckets already builds num_batched_tokens_buckets as supported chunk sizes
    # with the largest last; the plugin requires max_num_batched_tokens == that last one.
    max_num_batched = max(buckets["num_batched_tokens_buckets"])
    # HARD vLLM CONSTRAINT: max_num_batched_tokens >= max_num_seqs (can't batch fewer
    # tokens than concurrent seqs). High-concurrency slices (c512, c1024) violate a 512
    # chunk → server won't start. Bump the chunk to the smallest SUPPORTED chunk size
    # (512/1024/2048/4096/8192) that is >= max_seqs, and keep the prefill bucket == it
    # (segmented-prefill single-bucket invariant). If max_seqs > 8192 (not in our ladders)
    # it's simply not servable at this chunk granularity.
    if max_seqs > max_num_batched:
        bumped = next((c for c in _NEURON_CHUNK_SIZES if c >= max_seqs), _NEURON_CHUNK_SIZES[-1])
        max_num_batched = bumped
        buckets = dict(buckets)
        buckets["num_batched_tokens_buckets"] = [bumped]
        neuron_config["num_batched_tokens_buckets"] = [bumped]

    hf_id = _hf_id(model, regime)
    serve_path, is_local = _weights_source(model, regime)
    if force_hf:
        serve_path, is_local = hf_id, False

    argv = [
        "vllm", "serve", serve_path,
        "--host", "0.0.0.0", "--port", str(port),
        "--tensor-parallel-size", str(tp),
        # NOTE: do NOT pass `--device neuron` — the vLLM-Neuron plugin selects the
        # platform via VLLM_PLUGINS=neuron (set in the pod env). This vLLM build parses
        # --device as a device *ID* and rejects the string ('Non-integer device ID
        # neuron'). The stub bench_one_model.sh likewise never passed --device.
        "--dtype", "bfloat16",
        "--max-model-len", str(max_len),
        "--max-num-seqs", str(max_seqs),
        # Must equal the last num_batched_tokens_bucket (plugin invariant, see above).
        "--max-num-batched-tokens", str(max_num_batched),
        "--no-enable-log-requests",
        "--trust-remote-code",
        "--additional-config", json.dumps({"neuron_config": neuron_config}),
    ]
    # Replica DP. Only emitted above 1 so a dp=1 sweep produces a BYTE-IDENTICAL argv to
    # before (and therefore the same NEFF cache key). vLLM-Neuron reads this through the
    # standard vLLM parallel config: NeuronWorker._init_neuron_distributed_environment_and_
    # runtime derives global_world_size = tp*dp, global_rank = dp_rank*tp + rank, and gives
    # each replica the disjoint core window range(dp_rank*tp, (dp_rank+1)*tp).
    #
    # NOTHING ELSE IS NEEDED for single-node DP, and that is what makes DP free for the
    # rest of this driver: we leave --data-parallel-backend at its default (`mp`, in-process
    # multiprocessing) and --data-parallel-size-local unset (it defaults to the global size),
    # so ONE `vllm serve` owns all dp replicas behind ONE OpenAI endpoint on ONE port. The
    # bench client, KaizenServer handle and health probe are unchanged — there is no router
    # to stand up and no second address to track. (The `ray`/external-launcher backends and
    # --data-parallel-address/-port are for MULTI-NODE DP, which this sweep does not do.)
    if dp > 1:
        argv += ["--data-parallel-size", str(dp)]
    # Local checkpoint: the served-model-name MUST be the HF id so the bench client's
    # `--model <hf_id>` resolves (a local path served without this returns 404 /
    # completed=0 — the exact failure in the skill's Common-failures table).
    if is_local:
        argv += ["--served-model-name", hf_id]
    # APC: the milestone baseline governs this. 'controlled' (primary) forces APC OFF
    # to isolate raw hardware/kernel; 'production' allows the config's server setting
    # (Neuron default still OFF — the continuous-batching scheduler lacks chunked
    # prefill and APC is opt-in). Chunked prefill is not separately togg=led here: the
    # Neuron scheduler doesn't support it, so it's OFF on Neuron under both baselines
    # (a GPU-vs-Neuron 'production' run must therefore accept chunked-prefill asymmetry
    # or disable it GPU-side — see SKILL.md §C).
    argv += (["--enable-prefix-caching"] if apc_on_for(cfg) else ["--no-enable-prefix-caching"])
    # Safetensors load strategy — PARITY with the GPU driver. Generic vLLM weight-loading
    # flag (not CUDA-specific); a large-MoE checkpoint on some filesystems deadlocks under
    # the default lazy mmap (vLLM #40988), and `prefetch` fixes it. Same per-model key as GPU.
    if model.get("safetensors_prefetch"):
        argv += ["--safetensors-load-strategy", "prefetch"]
    # RoPE scaling for long-context shapes past the model's native window (YaRN etc.) —
    # PARITY with the GPU driver, which emits --rope-scaling. Without this, a 32k shape
    # on a model whose native window is smaller (e.g. an 8k-native model) would be
    # REJECTED on Neuron while it runs on GPU — breaking "whatever runs on GPU runs on
    # Trn". Same per-model config key + JSON shape as GPU.
    if model.get("rope_scaling"):
        argv += ["--rope-scaling", json.dumps(model["rope_scaling"])]
    # KV-pool sizing (Neuron analogue of gpu_memory_utilization).
    ngbo = model.get("num_gpu_blocks_override") or cfg.get("num_gpu_blocks_override")
    if ngbo:
        argv += ["--num-gpu-blocks-override", str(int(ngbo))]
    # MoE expert parallelism — now a SWEPT axis (ep on/off as separate groups, like the
    # GPU driver). The flag carries no degree; the degree lives in neuron_config
    # (ep_degree / moe_ep_degree), which build_neuron_config injects when ep is on (below,
    # via _ep_neuron_config). `ep` defaults to model.moe when ep_list is unset.
    if ep:
        argv += ["--enable-expert-parallel"]
    argv += [str(f) for f in (regime.get("extra_flags") or server.get("extra_flags") or [])]
    return argv


def build_server_env(cfg: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, str]:
    """Env overlay for the vLLM-Neuron server — WARM-START from a pre-compiled cache.

    Opt-in: when the config sets `warm_cache_dir` (a directory the pod can read, e.g. a
    Kaizen-FSx path that a CPU-compile Batch job bridged NEFFs into via `put-artifact`),
    point the plugin's `VLLM_NEURON_REMOTE_CACHE` there. On a per-graph cache miss the
    plugin fetches from that dir before compiling on device (cache.py _fetch_from_remote).
    The cache key is content-addressed (normalized FX graph + TP replica groups + input
    shapes + SDK versions + compiler args — cache.py create_cache_hash), so a NEFF compiled
    on ANY host (incl. a CPU box, under VLLM_NEURON_CPU_COMPILE=1 which normalizes the meta
    device pre-hash) is reused as long as the SAME image/config produced it.

    `warm_cache_assert_hit: true` sets VLLM_NEURON_ASSERT_CACHE_HIT=1 so a remote miss is a
    LOUD RuntimeError instead of a silent on-device recompile — use it during validation to
    prove the bridged cache actually matches. Leaving it off lets a partial cache still help
    (hits warm-start, misses fall back to on-device compile).

    Returns {} when not opted in → server env is untouched (existing NEURON_COMPILE_CACHE_URL
    on-device-compile path is the default and is unchanged).
    """
    warm_dir = model.get("warm_cache_dir") or cfg.get("warm_cache_dir")
    if not warm_dir:
        return {}
    env = {"VLLM_NEURON_REMOTE_CACHE": str(warm_dir)}
    if model.get("warm_cache_assert_hit") or cfg.get("warm_cache_assert_hit"):
        env["VLLM_NEURON_ASSERT_CACHE_HIT"] = "1"
    return env


# --- cell matrix ------------------------------------------------------------


def _cell_id(model: Dict[str, Any], regime: Dict[str, Any], tp: int,
             isl: int, osl: int, conc: int, ep: bool = False, dp: int = 1) -> str:
    # server_tag(tp, dp) emits "tp<N>" at dp=1 and "tp<N>_dp<M>" above it — so a dp=1
    # sweep keeps byte-identical cell-ids to before, and a dp>1 cell still matches
    # build_deliverables' _PATH_RE (which parses the `_dp(\d+)` group as optional).
    # The optional _ep tag mirrors the GPU driver (the regex tolerates a
    # `(?:_ep\d+)?`-style suffix; a bare `_ep` also parses since tp/dp match before it).
    return (f"{model['key']}_{regime['name']}_{server_tag(tp, dp)}{_ep_tag(ep)}"
            f"_isl{isl}_osl{osl}_c{conc}")


def build_groups(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Group the sweep by (model, regime, tp, dp, ep) — ONE server per group (compiles all
    buckets once), then a list of (isl, osl, conc) cells that server runs.

    The (tp, dp) pairs come from `shardings` (or the legacy `tp_list`, which pins dp=1);
    `ep` is the expert-parallel axis, which a sharding entry may PIN per-entry (GPU-style
    per-sharding `ep`) and otherwise sweeps over `ep_list`, defaulting to a single value =
    model.moe when `ep_list` is unset.

    Bucket derivation is deliberately dp-INVARIANT: derive_buckets keys num_seqs_buckets on
    the raw concurrencies even though one replica only ever sees ~conc/dp seqs. So a dp>1
    server compiles a top decode-batch bucket it will never hit — wasted compile time, not
    wrong numbers. Making buckets dp-dependent would fork the NEFF cache per dp value and
    change the ledger signature for existing dp=1 runs, which costs more than it saves.
    """
    groups = []
    for model in cfg["models"]:
        for regime in _regimes_for(cfg, model):
            buckets = derive_buckets(cfg, model)
            for tp, dp, ep_pin in _shardings(cfg, model):
                eps = [ep_pin] if ep_pin is not None else _ep_values(cfg, model)
                for ep in eps:
                    cells = []
                    for shape in cfg["shapes"]:
                        isl, osl = int(shape["isl"]), int(shape["osl"])
                        for conc in _concs_for(cfg, model, isl, osl):
                            cells.append((isl, osl, conc))
                    groups.append({
                        "model": model, "regime": regime, "tp": tp, "dp": dp, "ep": ep,
                        "buckets": buckets, "cells": cells,
                    })
    return groups


# --- regime marker ----------------------------------------------------------


def write_regime_meta(run_dir: Path, model: Dict[str, Any], regime: Dict[str, Any],
                      buckets: Dict[str, List[int]], baseline: str, *, ep: bool = False) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = run_dir / "regime_meta.json"
    if meta.exists():
        return
    nc = regime.get("neuron_config") or {}
    meta.write_text(json.dumps({
        "regime_name": regime["name"],
        "prec": regime.get("prec", regime["name"]),
        "spec": bool(regime.get("speculative")),
        # build_deliverables reads quantization/kv_cache_dtype for labeling; Neuron
        # carries precision in neuron_config.quantization, surface it there. Also honor
        # a GPU-style top-level regime.quantization/kv_cache_dtype (parity).
        "quantization": nc.get("quantization") or regime.get("quantization"),
        "kv_cache_dtype": nc.get("kv_cache_dtype") or regime.get("kv_cache_dtype", "auto"),
        "hf_id": _hf_id(model, regime),
        "model_key": model["key"],
        "buckets": buckets,
        "platform": "neuron",
        # EP marker (matches the GPU driver's regime_meta ep field) so the deliverables
        # attribute EP-on vs EP-off runs separately.
        "ep": ep,
        # Milestone serving-optimization baseline (controlled=primary / production).
        # The milestone deliverable splits on this so a controlled and production run
        # never merge into one Pareto comparison.
        "baseline": baseline,
    }, indent=2))


# --- bench args (per cell) --------------------------------------------------


def _cell_seed(workload: Dict[str, Any], isl: int, osl: int, conc: int) -> int:
    """The `vllm bench serve --seed` for a cell.

    THE PREFIX-CACHE FOOTGUN: `vllm bench serve --seed` DEFAULTS to 0, so the `random`
    dataset produces the IDENTICAL prompt set every cell. With APC on + the server reused
    across the concurrency sweep (which we do, to amortize NEFF compile), every cell after
    the first is served from a WARM prefix cache — TTFT collapses to the cached value (the
    Devstral case: 798ms cold → 53ms warm at 99.8% hit) and the number is not a cold
    prefill. Two `seed_mode`s:
      - "fixed" (default; matches the milestone doc's "seed 0 both sides"): every cell uses
        `workload.seed` (0). Symmetric across GPU/Neuron (both warm identically under
        APC-on), but cells 2+ are WARM — only trustworthy when hit-rate is captured +
        reported (we now do) and APC state is matched (the `baseline` field enforces that).
      - "per_cell": seed = base + a deterministic function of (isl,osl,conc) — DIFFERENT
        prompts per cell so every cell is COLD even with APC on + server reuse, while still
        identical GPU-vs-Neuron (the formula is pure in the cell coords). Use this to get
        honest cold-prefill TTFT under APC-on without restarting the server per cell.
    """
    base = int(workload.get("seed", 0))
    if workload.get("seed_mode", "fixed") == "per_cell":
        # deterministic, platform-independent (same on GPU + Neuron for a given cell)
        return base + (isl * 1_000_003 + osl * 1_009 + conc) % 2_000_000_011
    return base


def bench_args_for(cfg: Dict[str, Any], isl: int, osl: int, conc: int) -> List[str]:
    workload = cfg.get("workload", {})
    dataset = workload.get("dataset", "random")
    n_prompts = max(conc * int(workload.get("num_prompts_per_conc", 5)),
                    int(workload.get("min_prompts", 50)))
    n_warmups = max(conc * int(workload.get("warmups_per_conc", 2)),
                    int(workload.get("min_warmups", 20)))
    args = [
        "--num-prompts", str(n_prompts),
        "--max-concurrency", str(conc),
        "--num-warmups", str(n_warmups),
        # ALWAYS pin the seed explicitly (don't rely on the bench default of 0) — see
        # _cell_seed for the prefix-cache footgun this controls.
        "--seed", str(_cell_seed(workload, isl, osl, conc)),
    ]
    if dataset == "random":
        args += [
            "--dataset-name", "random",
            "--random-input-len", str(isl),
            "--random-output-len", str(osl),
            # DEFAULT 0.0 (exact shapes) — mandatory for the Neuron-vs-GPU comparison
            # so lengths hit compiled buckets deterministically on both platforms.
            "--random-range-ratio", str(workload.get("random_range_ratio", 0.0)),
        ]
        if workload.get("ignore_eos", True):
            args += ["--ignore-eos"]
    else:  # sharegpt — Neuron-only APC study, NOT a cross-platform comparison
        args += ["--dataset-name", "sharegpt",
                 "--dataset-path", str(workload.get("dataset_path", "/tmp/sharegpt.json"))]
    if "temperature" in workload:
        args += ["--temperature", str(workload["temperature"])]
    if cfg.get("save_detailed", True):
        args += ["--save-detailed"]
    return args


# --- main sweep -------------------------------------------------------------


def _matches(cell_id: str, k: Optional[str]) -> bool:
    if not k:
        return True
    return all(tok in cell_id for tok in k.split())


def run_sweep(cfg: Dict[str, Any], results_root: Path, *, collect_only: bool,
              k: Optional[str], startup_timeout_s: int) -> int:
    results_root.mkdir(parents=True, exist_ok=True)
    image = cfg.get("image", os.environ.get("KAIZEN_IMAGE", "unknown"))
    groups = build_groups(cfg)

    # Ledger signature over the sweep INVARIANTS incl. buckets/neuron_config, so any
    # bucket edit (which changes the compiled NEFF) invalidates stale cells.
    sig = config_signature(
        image=image,
        model=cfg["models"][0]["hf_id"] if cfg.get("models") else "multi",
        max_model_len=_max_model_len(cfg),
        serve_extra_flags=[json.dumps(
            {kk: cfg.get(kk) for kk in ("models", "regimes", "shapes",
                                        "shardings", "tp_list", "concurrencies",
                                        "concs_by_shape",
                                        "buckets", "workload", "server")},
            sort_keys=True, separators=(",", ":"))],
        extra={"platform": "neuron", "instance": cfg.get("instance")},
    )
    state = SweepState.load_or_new(
        results_root, sweep=cfg.get("sweep_name", "neuron-bench"),
        image=image, signature=sig, config={"instance": cfg.get("instance")},
    )

    # Seed the full matrix as pending + validate every group up front.
    all_ids: List[str] = []
    fit_errors: List[str] = []
    for g in groups:
        tag = (f"  {g['model']['key']}/{g['regime']['name']}/"
               f"{server_tag(g['tp'], g['dp'])}{_ep_tag(g['ep'])}")
        # parallelism_error FIRST and unconditionally: it catches contract violations
        # (core budget, EP degree vs tp*dp) that fit_check's escape hatches would skip.
        for err in (parallelism_error(cfg, g["model"], g["regime"], g["tp"], g["dp"], g["ep"]),
                    fit_check(cfg, g["model"], g["regime"], g["tp"], g["buckets"], g["dp"])):
            if err:
                fit_errors.append(f"{tag}: {err}")
        warn = _box_fill_warning(cfg, g["tp"], g["dp"])
        if warn:
            log.warning("%s: %s", tag.strip(), warn)
        for (isl, osl, conc) in g["cells"]:
            all_ids.append(_cell_id(g["model"], g["regime"], g["tp"], isl, osl, conc,
                                    g["ep"], g["dp"]))
    state.seed_cells(all_ids)

    if fit_errors:
        # A fit/parallelism failure is a HARD stop (before provisioning) — on Neuron a bad
        # bucket set or an illegal world size burns minutes of compile before it fails at
        # load. Fix the config, re-run.
        log.error("PREFLIGHT FAILED for %d group(s) — refusing to provision:\n%s",
                  len(fit_errors), "\n".join(fit_errors))
        return 2

    log.info("Sweep: %d group(s) (model x regime x tp/dp x ep), %d total cells. "
             "Buckets/group:", len(groups), len(all_ids))
    for g in groups:
        log.info("  %s/%s/%s%s: prefill=%s seqs=%s ctx=%s (%d cells)",
                 g["model"]["key"], g["regime"]["name"],
                 server_tag(g["tp"], g["dp"]), _ep_tag(g["ep"]),
                 g["buckets"]["num_batched_tokens_buckets"],
                 g["buckets"]["num_seqs_buckets"],
                 g["buckets"]["decode_context_length_buckets"], len(g["cells"]))

    if collect_only:
        for g in groups:
            for (isl, osl, conc) in g["cells"]:
                cid = _cell_id(g["model"], g["regime"], g["tp"], isl, osl, conc,
                               g["ep"], g["dp"])
                if _matches(cid, k):
                    print(cid)
        return 0

    fail_threshold = float(cfg.get("fail_threshold", 0.05))
    bench_timeout = int(cfg.get("bench_timeout_s", 3600))
    n_pass = n_fail = n_skip = 0

    for g in groups:
        model, regime, tp, dp, ep, buckets = (g["model"], g["regime"], g["tp"], g["dp"],
                                              g["ep"], g["buckets"])
        gtag = f"{server_tag(tp, dp)}{_ep_tag(ep)}"
        # ep on/off goes to its own run dir (like the GPU driver's `_ep` suffix) so the
        # deliverables attribute EP-on vs EP-off separately. dp does NOT appear here — it
        # lives in the LEAF dir via out_dir(..., dp=dp), exactly as on GPU, so
        # build_deliverables reads one regime dir spanning every sharding.
        run_dir = results_root / f"{model['key']}__{regime['name']}{_ep_tag(ep)}"

        def _already_done(isl: int, osl: int, conc: int) -> bool:
            # Resume is DURABLE against ledger loss: a cell is done if the ledger says so
            # OR a valid result.json exists on disk (the ledger can get recreated fresh
            # after a pod reschedule mid-write — but the per-cell result.json on FSx is the
            # real record). Backfill the ledger from disk so the summary is accurate too.
            cid_ = _cell_id(model, regime, tp, isl, osl, conc, ep, dp)
            # "Done" ONLY means a CLEAN cell (fail_frac ≤ threshold). A cell whose
            # failures were connection-refused (server died mid-cell during pod churn)
            # gets recorded with a high fail_frac — that is NOT trustworthy data and MUST
            # be re-run in a stable window, not treated as done. So check the on-disk
            # result.json's actual failed fraction, not just "a file exists".
            rj = out_dir(run_dir, model["key"], isl, osl, tp, conc, dp=dp) / "result.json"
            try:
                if rj.exists():
                    d = json.loads(rj.read_text())
                    comp = int(d.get("completed", 0) or 0)
                    fail = int(d.get("failed", 0) or 0)
                    tot = comp + fail
                    ff = (fail / tot) if tot else 1.0
                    if comp > 0 and ff <= fail_threshold:  # a CLEAN completed bench
                        state.record(cid_, status="passed",
                                     otps=d.get("output_throughput"), completed=comp,
                                     fail_frac=round(ff, 3), resumed_from_disk=True)
                        return True
                    # dirty cell on disk → fall through and re-run it
            except Exception:  # noqa: BLE001 — partial/corrupt file → re-run
                pass
            # Ledger says passed AND clean? (a prior clean run) trust it.
            cell = state.cells.get(cid_, {})
            if cell.get("status") == "passed" and (cell.get("fail_frac") or 1.0) <= fail_threshold:
                return True
            return False

        # Which cells in this group still need running?
        pending = [(isl, osl, conc) for (isl, osl, conc) in g["cells"]
                   if _matches(_cell_id(model, regime, tp, isl, osl, conc, ep, dp), k)
                   and not _already_done(isl, osl, conc)]
        if not pending:
            log.info("group %s/%s/%s: all cells done (resume) — skipping server start",
                     model["key"], regime["name"], gtag)
            continue

        write_regime_meta(run_dir, model, regime, buckets, _baseline(cfg), ep=ep)
        hf_id = _hf_id(model, regime)
        serve_path, is_local = _weights_source(model, regime)
        # local-first + optional HF fallback: try the local checkpoint (no HF download),
        # and if it fails to start AND the model opted in (`hf_fallback: true`), retry
        # serving the HF id. The fallback is OPT-IN and LOUDLY logged — a silent fallback
        # would bench a different checkpoint than intended on a fat-fingered path.
        attempts = [False]  # force_hf flag per attempt
        if is_local and model.get("hf_fallback"):
            attempts.append(True)

        log.info("=== group %s/%s/%s: starting server — weights from %s (%s), %d replica(s) "
                 "x tp%d = %d cores, compiles %d prefill + %d decode buckets (MINUTES first "
                 "run, cached after) ===",
                 model["key"], regime["name"], gtag,
                 serve_path, "LOCAL checkpoint" if is_local else "HF hub",
                 dp, tp, tp * dp,
                 len(buckets["num_batched_tokens_buckets"]),
                 len(buckets["num_seqs_buckets"]))

        serve_log = Path("/tmp") / f"serve_{model['key']}_{regime['name']}_{gtag}.log"
        # Warm-start env (opt-in): point the server at a pre-compiled remote NEFF cache
        # (e.g. a CPU-compile Batch job's NEFFs bridged into Kaizen FSx). Empty {} = the
        # default on-device-compile path, unchanged.
        server_env = build_server_env(cfg, model)
        proc = None
        server_argv = None
        last_exc: Optional[Exception] = None
        for force_hf in attempts:
            server_argv = build_serve_argv(cfg, model, regime, tp, buckets,
                                           port=SERVER_PORT, ep=ep, force_hf=force_hf,
                                           dp=dp)
            if force_hf:
                log.warning("group %s/%s/%s: local checkpoint failed to start — "
                            "FALLING BACK to HF id %s (hf_fallback=true)",
                            model["key"], regime["name"], gtag, hf_id)
            try:
                proc = start_server_to_file(server_argv, port=SERVER_PORT,
                                            timeout_s=startup_timeout_s, log_path=serve_log,
                                            extra_env=server_env)
                break  # started
            except Exception as exc:  # noqa: BLE001 — compile/OOM/arch/missing-weights
                last_exc = exc
                continue
        if proc is None:
            # Whole group can't serve — record every pending cell FAILED (resume
            # re-tries) and move on. Fit-check should have caught OOM; a start failure
            # here is usually an unregistered arch, a bad checkpoint path, or a compile error.
            for (isl, osl, conc) in pending:
                cid = _cell_id(model, regime, tp, isl, osl, conc, ep, dp)
                state.record(cid, status="failed", error=f"server start: {last_exc}")
                n_fail += 1
            log.error("group %s/%s/%s: server did not start: %s",
                      model["key"], regime["name"], gtag, last_exc)
            continue

        try:
            handle = KaizenServer(image=image, cmd=" ".join(server_argv),
                                  port=SERVER_PORT, host_port=SERVER_PORT,
                                  model=hf_id, framework="vllm")
            for (isl, osl, conc) in pending:
                cid = _cell_id(model, regime, tp, isl, osl, conc, ep, dp)
                cell_dir = out_dir(run_dir, model["key"], isl, osl, tp, conc, dp=dp)
                # WEDGE GUARD: before each cell, confirm the server is still healthy AND
                # its process is alive. A pod reschedule can leave the server pid alive
                # but not serving (/health fails), and run_bench's client would then hang
                # forever in warmup (the observed multi-hour wedge). If unhealthy, abort
                # the group with a non-zero exit so the SUPERVISOR restarts the driver —
                # which re-establishes the server and resumes (disk-resume skips done cells).
                if proc.poll() is not None or not _server_healthy(SERVER_PORT):
                    # Server died mid-sweep (pod reschedule). Break out of the cell loop so
                    # this group ends cleanly; the OUTER group loop moves on and the driver
                    # exits non-zero → supervisor restarts it → server re-established, and
                    # disk-resume skips the cells already on disk. (Do NOT raise — a raised
                    # exception in a BATCH workload marks it terminally FAILED; a clean
                    # non-zero exit lets the in-command supervisor loop retry.)
                    log.error("cell %s: server unhealthy/dead before cell (likely pod "
                              "reschedule) — ending group; supervisor will restart", cid)
                    break
                log.info("---- cell %s ----", cid)
                # run_bench can RAISE (e.g. snapshot_metrics hits a just-died server →
                # ConnectionRefused). Catch it so one dead-server cell records failed and
                # the sweep continues, instead of crashing the driver (which marked the
                # whole batch workload FAILED — the p8k/p32k failure). A restart re-serves.
                try:
                    result = run_bench(
                        handle, cell_dir,
                        bench_args=bench_args_for(cfg, isl, osl, conc),
                        timeout_s=bench_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    state.record(cid, status="failed", error=f"run_bench raised: {exc}")
                    n_fail += 1
                    log.error("cell %s: run_bench raised (%s) — server likely died; "
                              "ending group so supervisor restarts", cid, exc)
                    break
                if not result.success:
                    state.record(cid, status="failed",
                                 error=result.data.get("error") or "bench failed")
                    n_fail += 1
                    log.error("cell %s FAILED: %s", cid, result.data.get("error"))
                    continue
                completed = int(result.data.get("completed", 0) or 0)
                failed = int(result.data.get("failed", 0) or 0)
                total = completed + failed
                fail_frac = (failed / total) if total else 1.0
                # Prefix-cache hit rate → proof the cell was cold vs warm (footgun audit).
                hit = _prefix_hit_rate(cell_dir)
                if hit is not None:
                    try:
                        (cell_dir / "prefix_hit.json").write_text(json.dumps({
                            "prefix_cache_hit_rate": hit,
                            "seed": _cell_seed(cfg.get("workload", {}), isl, osl, conc),
                            "apc_on": apc_on_for(cfg),
                            "seed_mode": cfg.get("workload", {}).get("seed_mode", "fixed"),
                        }))
                    except Exception:  # noqa: BLE001
                        pass
                state.record(cid, status="passed", fail_frac=round(fail_frac, 3),
                             otps=result.data.get("output_throughput"),
                             completed=completed, prefix_hit_rate=hit)
                # FOOTGUN GUARD: APC on + a warm cache means this cell's TTFT is NOT a
                # cold prefill. Warn loudly so the number isn't mistaken for cold. (With
                # seed_mode=per_cell this stays ~0 even under APC-on — the intended state.)
                if apc_on_for(cfg) and hit is not None and hit > 0.5:
                    log.warning("cell %s: prefix-cache hit rate %.1f%% — TTFT is WARM, not a "
                                "cold prefill (reused server + fixed seed). Set "
                                "workload.seed_mode=per_cell for cold TTFT under APC-on.",
                                cid, hit * 100)
                if fail_frac > fail_threshold:
                    n_skip += 1
                    log.warning("cell %s: high failure rate %d/%d (%.0f%%) — saturation, "
                                "excluded from clean results", cid, failed, total, fail_frac * 100)
                else:
                    n_pass += 1
                    log.info("cell %s PASSED: %s tok/s, completed=%d, prefix_hit=%s",
                             cid, result.data.get("output_throughput"), completed,
                             f"{hit*100:.0f}%" if hit is not None else "n/a")
        finally:
            stop_vllm_server(proc, port=SERVER_PORT)

    # Count cells STILL not terminally done across the whole matrix (respecting the -k
    # filter). A cell is "done" if the ledger says passed/failed OR a valid result.json
    # exists on disk. If ANY remain pending — e.g. the group broke out early on a dead
    # server — return NON-ZERO so the supervisor RETRIES. Returning 0 with cells still
    # pending is the bug that made a workload "SUCCEED" at 2/7 cells.
    # "remaining" = cells without a CLEAN result on disk (fail_frac ≤ threshold). Must use
    # the SAME clean-gate as _already_done — a dirty cell (high fail_frac from a mid-cell
    # server death) is NOT done and should be retried. (The earlier bug used only
    # completed>0, so a dirty cell counted as done → rc=0 → workload SUCCEEDED dirty.)
    # Bounded by the supervisor's 12-attempt cap: churn cells recover in a few attempts;
    # a genuinely-broken cell (e.g. APC 500s) exhausts the cap and the run stops.
    remaining = 0
    for g in groups:
        rd = results_root / f"{g['model']['key']}__{g['regime']['name']}{_ep_tag(g['ep'])}"
        for (isl, osl, conc) in g["cells"]:
            cid = _cell_id(g["model"], g["regime"], g["tp"], isl, osl, conc,
                           g["ep"], g["dp"])
            if not _matches(cid, k):
                continue
            # NOTE: a "failed" cell (timeout / server-death) is TRANSIENT churn, not
            # terminal — count it as remaining so it retries. The supervisor's 12-attempt
            # cap is what stops a genuinely-unrecoverable cell (e.g. APC 500s) from looping
            # forever. Only a CLEAN result.json on disk marks a cell truly done.
            rj = (out_dir(rd, g["model"]["key"], isl, osl, g["tp"], conc, dp=g["dp"])
                  / "result.json")
            try:
                if rj.exists():
                    d = json.loads(rj.read_text())
                    comp = int(d.get("completed", 0) or 0)
                    fail = int(d.get("failed", 0) or 0)
                    tot = comp + fail
                    ff = (fail / tot) if tot else 1.0
                    if comp > 0 and ff <= fail_threshold:
                        continue  # CLEAN → done
            except Exception:  # noqa: BLE001
                pass
            remaining += 1

    log.info("SWEEP DONE: %d passed, %d saturated(excluded), %d failed, %d STILL PENDING. "
             "Ledger: %s", n_pass, n_skip, n_fail, remaining, state.path)
    log.info("Pull results with `kaizen get-artifact` and run build_deliverables.py "
             "--multi --instance %s on %s", cfg.get("instance", "trn2"), results_root)
    # rc: 0 only when NOTHING is left to run (all cells terminal). Pending or hard-failed
    # → non-zero so the supervisor re-invokes (disk-resume skips the done ones).
    return 0 if (remaining == 0 and n_fail == 0) else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="neuron_sweep",
                                description="vLLM-Neuron serving sweep (in-pod).")
    p.add_argument("--config", type=Path, default=Path("neuron_bench_config.json"),
                   help="path to neuron_bench_config.json")
    p.add_argument("--results-root", type=Path, required=True,
                   help="results root; use a DURABLE FSx path for cross-workload resume")
    p.add_argument("--collect-only", action="store_true", help="print cells, don't run")
    p.add_argument("-k", default=None, help="only cells whose id contains ALL space-separated tokens")
    p.add_argument("--startup-timeout", type=int, default=3600,
                   help="seconds to wait for /health (INCLUDES NEFF compile on first start)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    cfg = load_config(args.config)
    return run_sweep(cfg, args.results_root, collect_only=args.collect_only,
                     k=args.k, startup_timeout_s=args.startup_timeout)


if __name__ == "__main__":
    sys.exit(main())
