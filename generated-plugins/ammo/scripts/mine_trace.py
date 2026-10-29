#!/usr/bin/env python3
"""Mine the Stage-2 dilution and component tables from trace intervals + sweep JSON.

This script is the sole owner of every mined NUMBER. The agent owns judgment
only, and supplies it through `mine_config.json`: segmentation policy, family
labels and symbol patterns, trace-to-BS mapping, and physical ceilings.

Outputs (round-scoped, never hand-edited):
  rounds/{N}/mining/mined.json  — schema `mine_trace/1`, machine-readable
  rounds/{N}/mining/tables.md   — the two mandated markdown tables

Fails loud (exit 1, no output written) on: a missing kernel table or required
column, `decode_busy` outside [0.20,1.0], merged busy > span, a non-exhaustive
family partition beyond `allow_residual`, an unresolvable decode step count.

Warns (in mined.json `warnings[]` and on stderr) on: a stored share that
disagrees with the re-derived share, a marginal delimiter separation ratio, a
per-rank busy spread above 0.02, an absent `tpot_s`, mixed nsys versions.

Config (agent judgment only):

    {
      "round": 1,
      "artifact_dir": "<campaign artifact dir; default AMMO_ARTIFACT_DIR>",
      "arm": "baseline",
      "e2e_results": "rounds/1/sweeps/baseline/e2e_latency_results.json",
      "segmentation": {"policy": "max_gap"},
      "traces": [{"bs": 32, "sqlite": "rounds/1/profiling/nsys/x.sqlite",
                  "label": "step 512"}],
      "families": [{"label": "dense_proj_GEMM",
                    "symbol_patterns": ["ampere_bf16_s16816gemm"],
                    "physical_ceiling": 1.619, "prefill_active": true}],
      "allow_residual": 0.0
    }

`round` absent: the round comes from state.json `campaign.current_round` in
artifact_dir, as run_pumice_grpc_bench.py does for `--slot`; with no readable
state.json it is 1, with a WARN.

Family selection (mined.json `selection` and `selected_families`): rank every
family by its highest `f_e2e` across buckets, ties by label; keep the families
whose highest `f_e2e` is at or above the floor; keep at most five of them. The
floor is state.json `campaign.config.min_e2e_improvement_pct` in artifact_dir,
as a share (0.5 means 0.005): a family below it cannot clear the campaign's win
floor even if removed whole. With no readable value the floor is the schema
default 0.5%, with a WARN. The cap of five plus the cross-cutting slice fills
one orchestrate wave of six workers. Neither is configurable: a config
`selection` block is FATAL, because it would let the config choose which
families get dossiers. `selection` records the floor, the cap, the eligible
families, and `dropped_by_cap`; a dropped family keeps its tables.md rows.
The cross-cutting slice is always added on top, under the reserved label in
`selection.cross_cutting` (`cross_cutting`, so a config family with that label
is FATAL). `__RESIDUAL__` is never selected. Each selected
entry carries `chain`: the family's kernels in one decode step (the mined
window of the bucket where its `f_e2e` is highest), one item per symbol in
trace order of first launch, with `count`, `sum_ns`, `mean_ns`, and
`first_offset_ns` from the window start.

Per-bucket slices (subcommand `slice`): cut each bucket out of the one full
export so mining reads one sqlite per batch size.

    scripts/ammo mine_trace.py slice --config rounds/1/mining/mine_config.json
        [--sqlite rounds/1/profiling/nsys/baseline.sqlite]
        [--profile-meta rounds/1/sweeps/profiling/e2e_latency_results.json]
        [--label baseline] [--bs 8 ...] [--delimiter SYMBOL]
        [--full-batch-symbol SYMBOL] [--out-dir rounds/1/profiling/nsys]

The bucket window spans its waves with `profiled: true` and `warmup: false` in
`profile_meta.waves[]`, mapped from wall-clock seconds to trace ns through the
export's `TARGET_INFO_SESSION_START_TIME.utcEpochNs`. On each rank (deviceId,
globalPid) the slice ends just before that rank's last delimiter launch inside
that window, so the rank's final step is closed by its next step's start:
mining reads a slice from its last delimiter to the slice end as one decode
step. Every rank must hold the same number of delimiter launches in the
window, else FATAL. The delimiter is `segmentation.symbol` of a
`delimiter_symbol` config (a `max_gap` config is refused), or
`--delimiter` without a config. With a full-batch symbol (flag or
`segmentation.full_batch_symbol`), the slice instead ends after the last
closed step holding a launch of that symbol whose `gridX` equals the batch
size, so a ragged tail of steps that sampled fewer sequences drops out.
Every table of the export is kept. The kernel table mining reads is cut per
rank; any other table with a `start` column keeps the rows with window start
<= start < cut (the earliest rank's cut), a table with a `timestamp` column
keeps window start <= timestamp < cut, and every other table is copied whole.
Rows with an `end` column also need end <= window end. Writes
`{label}_bs{BS}.sqlite` and `{label}_slices.json` (windows, cuts, delimiter and
kernel counts) to the out dir; the full export is opened read-only.

Neuron traces use the same config with ``events_json`` in place of ``sqlite``.
Create the normalized ``ammo_intervals/1`` file with ``runtime_ops.py
normalize``. The interval math and every downstream AMMO criterion are shared.

Usage:
    scripts/ammo mine_trace.py --config rounds/1/mining/mine_config.json
    scripts/ammo mine_trace.py --config <cfg> --out-json <path> --out-md <path>

main() fills the campaign env through campaign_paths.apply_env() first, so a
config without artifact_dir resolves its relative paths from the campaign dir.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import campaign_paths  # noqa: E402

SCHEMA = "mine_trace/1"
KERNEL_TABLES = ("CUPTI_ACTIVITY_KIND_KERNEL", "CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL")
REQUIRED_COLUMNS = (
    "start",
    "end",
    "deviceId",
    "streamId",
    "globalPid",
    "demangledName",
    "graphId",
)
GENERATED_HEADER = "<!-- GENERATED by mine_trace.py — DO NOT EDIT NUMBERS BY HAND -->"
DECODE_BUSY_MIN = 0.20
DECODE_BUSY_MAX = 1.0
DEFAULT_MIN_SEPARATION_RATIO = 2.0
DEFAULT_STEP_COUNT_TOLERANCE_PCT = 2.0
MAX_RANK_BUSY_SPREAD = 0.02
CLOSURE_TOLERANCE = 0.02
RESIDUAL_LABEL = "__RESIDUAL__"
DEFAULT_MIN_E2E_IMPROVEMENT_PCT = 0.5  # schemas/state.schema.json default
SELECTION_MAX_FAMILIES = 5
CROSS_CUTTING_LABEL = "cross_cutting"
SESSION_START_TABLE = "TARGET_INFO_SESSION_START_TIME"


class Fatal(Exception):
    """A condition that makes the mined numbers unsafe to publish."""


# ---------------------------------------------------------------------------
# sqlite reading
# ---------------------------------------------------------------------------

def load_kernels(db_path):
    """Return (rows, meta, kernel_table). Rows are ordered by start time.

    Each row: (start, end, symbol, deviceId, streamId, globalPid, graphId).
    """
    path = Path(db_path)
    if not path.is_file():
        raise Fatal(f"{db_path}: sqlite export not found")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        table = next((t for t in KERNEL_TABLES if t in tables), None)
        if table is None:
            raise Fatal(
                f"{db_path}: no CUPTI kernel table "
                f"(want one of {', '.join(KERNEL_TABLES)}; found {len(tables)} tables)"
            )
        if "StringIds" not in tables:
            raise Fatal(f"{db_path}: StringIds table missing; cannot resolve kernel symbols")
        columns = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        missing = [c for c in REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise Fatal(f"{db_path}: {table} lacks required column(s) {', '.join(missing)}")
        meta = {}
        if "META_DATA_EXPORT" in tables:
            meta = dict(con.execute("SELECT name, value FROM META_DATA_EXPORT"))
        rows = con.execute(
            f"SELECT k.start, k.end, s.value, k.deviceId, k.streamId, k.globalPid, k.graphId "
            f"FROM {table} k JOIN StringIds s ON s.id = k.demangledName ORDER BY k.start, k.end"
        ).fetchall()
    finally:
        con.close()
    if len(rows) < 2:
        raise Fatal(f"{db_path}: {len(rows)} kernel rows; too few to segment a step")
    return rows, meta, table


def load_normalized_events(events_path):
    """Load ``ammo_intervals/1`` rows emitted by runtime_ops.py.

    The returned tuple is deliberately identical to ``load_kernels`` so the
    existing interval-union and Stage-2 calculations do not fork by backend.
    """
    path = Path(events_path)
    if not path.is_file():
        raise Fatal(f"{events_path}: normalized event JSON not found")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Fatal(f"{events_path}: invalid normalized event JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "ammo_intervals/1":
        raise Fatal(f"{events_path}: expected schema ammo_intervals/1")
    events = payload.get("events")
    if not isinstance(events, list):
        raise Fatal(f"{events_path}: events must be a list")
    rows = []
    required = (
        "start_ns", "end_ns", "symbol", "device_id", "stream_id",
        "process_id", "graph_id",
    )
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise Fatal(f"{events_path}: event {index} must be an object")
        missing = [key for key in required if key not in event]
        if missing:
            raise Fatal(
                f"{events_path}: event {index} lacks required field(s) "
                f"{', '.join(missing)}"
            )
        start, end = event["start_ns"], event["end_ns"]
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise Fatal(f"{events_path}: event {index} timestamps must be numeric")
        rows.append(
            (
                int(start),
                int(end),
                str(event["symbol"]),
                event["device_id"],
                event["stream_id"],
                event["process_id"],
                event["graph_id"],
            )
        )
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    if len(rows) < 2:
        raise Fatal(f"{events_path}: {len(rows)} event rows; too few to segment a step")
    profiler = payload.get("profiler") if isinstance(payload.get("profiler"), dict) else {}
    meta = {
        "PROFILER_KIND": profiler.get("product") or "neuron-explorer",
        "EXPORT_PRODUCT_VERSION": profiler.get("product_version"),
        "EXPORT_SCHEMA_VERSION": profiler.get("schema_version"),
        "RUNTIME_PROFILE": payload.get("runtime_profile"),
        "SOURCE": payload.get("source"),
    }
    return rows, meta, "AMMO_NORMALIZED_INTERVALS"


def load_trace_rows(trace):
    """Dispatch a trace descriptor to its native reader."""
    if not isinstance(trace, dict):
        raise Fatal("trace descriptor must be an object")
    if trace.get("sqlite"):
        return load_kernels(trace["sqlite"])
    if trace.get("events_json"):
        return load_normalized_events(trace["events_json"])
    raise Fatal("trace needs sqlite (CUDA) or events_json (Neuron)")


def _trace_source(trace):
    return str(trace.get("sqlite") or trace.get("events_json") or "<unknown>")


# ---------------------------------------------------------------------------
# interval math
# ---------------------------------------------------------------------------

def merged_busy(intervals):
    """Union of [start, end) intervals in ns. NEVER sums concurrent kernels."""
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start > cur_end:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    return total + (cur_end - cur_start)


def duration_sum(intervals):
    return sum(end - start for start, end in intervals)


def check_window_integrity(source, intervals, busy_ns, span_ns):
    """Reject a window whose interval math cannot describe real GPU work."""
    negative = [iv for iv in intervals if iv[1] < iv[0]]
    if negative:
        raise Fatal(
            f"{source}: {len(negative)} kernel row(s) have end < start "
            f"(first {negative[0]}); the export is corrupt"
        )
    if busy_ns > span_ns:
        raise Fatal(f"{source}: merged busy {busy_ns} ns exceeds window span {span_ns} ns")


# ---------------------------------------------------------------------------
# segmentation (policy is agent judgment; the report is mechanical)
# ---------------------------------------------------------------------------

def segment(rows, segmentation):
    """Return (window_start_index, delimiter_report). Deterministic per policy."""
    policy = (segmentation or {}).get("policy")
    if policy == "max_gap":
        gaps = sorted(
            ((rows[i][0] - rows[i - 1][1], i) for i in range(1, len(rows))),
            key=lambda g: (-g[0], g[1]),
        )
        gap_ns, index = gaps[0]
        next_gap_ns = gaps[1][0] if len(gaps) > 1 else None
        ratio = (gap_ns / next_gap_ns) if next_gap_ns else None
        return index, {
            "policy": "max_gap",
            "gap_ns": gap_ns,
            "at_index": index,
            "next_gap_ns": next_gap_ns,
            "separation_ratio": ratio,
            "first_kernel_after": rows[index][2],
        }
    if policy == "delimiter_symbol":
        symbol = (segmentation or {}).get("symbol")
        if not symbol:
            raise Fatal("segmentation.policy=delimiter_symbol requires segmentation.symbol")
        hits = [i for i, r in enumerate(rows) if symbol in r[2]]
        if not hits:
            raise Fatal(f"segmentation delimiter symbol {symbol!r} absent from trace")
        index = hits[-1]
        return index, {
            "policy": "delimiter_symbol",
            "symbol": symbol,
            "occurrences": len(hits),
            "at_index": index,
            "separation_ratio": None,
            "first_kernel_after": rows[index][2],
        }
    raise Fatal(f"unknown segmentation policy {policy!r} (want max_gap or delimiter_symbol)")


# ---------------------------------------------------------------------------
# family partition (labels and patterns are agent judgment; assignment is not)
# ---------------------------------------------------------------------------

def compile_families(families):
    if not families:
        raise Fatal("config.families is empty; a partition needs at least one family")
    compiled = []
    for entry in families:
        label = entry.get("label")
        patterns = entry.get("symbol_patterns") or []
        if not label:
            raise Fatal("every config.families entry needs a label")
        if label == CROSS_CUTTING_LABEL:
            raise Fatal(
                f"family label {label!r} is reserved for the cross-cutting slice dossier; "
                f"rename the family"
            )
        if not patterns:
            raise Fatal(f"family {label!r} has no symbol_patterns")
        try:
            regexes = [re.compile(p) for p in patterns]
        except re.error as exc:
            raise Fatal(f"family {label!r} has an invalid regex: {exc}")
        compiled.append({
            "label": label,
            "regexes": regexes,
            "physical_ceiling": entry.get("physical_ceiling"),
            "prefill_active": entry.get("prefill_active"),
        })
    return compiled


def assign_symbols(rows, families):
    """Exhaustive symbol -> family map. First matching family wins."""
    assignment = {}
    residual = []
    for row in rows:
        symbol = row[2]
        if symbol in assignment:
            continue
        label = None
        for family in families:
            if any(rx.search(symbol) for rx in family["regexes"]):
                label = family["label"]
                break
        assignment[symbol] = label
        if label is None:
            residual.append(symbol)
    return assignment, residual


# ---------------------------------------------------------------------------
# per-trace mining
# ---------------------------------------------------------------------------

def mine_one_trace(trace, families, segmentation, warnings):
    source = _trace_source(trace)
    rows, meta, table = load_trace_rows(trace)
    index, delimiter = segment(rows, segmentation)
    window = rows[index:]
    if not window:
        raise Fatal(f"{trace['sqlite']}: segmentation left an empty window")

    intervals = [(r[0], r[1]) for r in window]
    busy_ns = merged_busy(intervals)
    sum_ns = duration_sum(intervals)
    span_ns = max(e for _, e in intervals) - min(s for s, _ in intervals)
    check_window_integrity(source, intervals, busy_ns, span_ns)

    per_rank = {}
    ranks = {}
    for row in window:
        ranks.setdefault((row[3], row[5]), []).append((row[0], row[1]))
    for (device_id, global_pid), rank_intervals in sorted(ranks.items()):
        rank_busy = merged_busy(rank_intervals)
        rank_span = (
            max(e for _, e in rank_intervals) - min(s for s, _ in rank_intervals)
        )
        per_rank[f"dev{device_id}_pid{global_pid}"] = {
            "merged_busy_ns": rank_busy,
            "sum_ns": duration_sum(rank_intervals),
            "span_ns": rank_span,
            "n_kernels": len(rank_intervals),
            "idle_ns": rank_span - rank_busy,
            "decode_busy_in_window": (rank_busy / rank_span) if rank_span else None,
        }
    rank_busy_values = [
        v["decode_busy_in_window"] for v in per_rank.values()
        if v["decode_busy_in_window"] is not None
    ]
    spread = (max(rank_busy_values) - min(rank_busy_values)) if len(rank_busy_values) > 1 else 0.0
    if spread > MAX_RANK_BUSY_SPREAD:
        warnings.append(
            f"{source}: per-rank busy spread {spread:.4f} exceeds "
            f"{MAX_RANK_BUSY_SPREAD}; check the region boundary for a leaked warmup gap"
        )

    assignment, residual = assign_symbols(window, families)
    grouped = {}
    for row in window:
        grouped.setdefault(assignment[row[2]] or RESIDUAL_LABEL, []).append(row)
    family_rows = {}
    for label, rows_in in grouped.items():
        fam_intervals = [(r[0], r[1]) for r in rows_in]
        family_rows[label] = {
            "merged_busy_ns": merged_busy(fam_intervals),
            "sum_ns": duration_sum(fam_intervals),
            "n_kernels": len(rows_in),
            "in_graph_ns": sum(r[1] - r[0] for r in rows_in if r[6] is not None),
            "symbols": sorted({r[2] for r in rows_in}),
        }
    residual_sum_ns = family_rows.get(RESIDUAL_LABEL, {}).get("sum_ns", 0)
    assigned_sum_ns = sum_ns - residual_sum_ns
    chains = {label: family_chain(rows_in, window[0][0]) for label, rows_in in grouped.items()}

    separation_ratio = delimiter.get("separation_ratio")
    min_ratio = segmentation.get("min_separation_ratio", DEFAULT_MIN_SEPARATION_RATIO)
    if separation_ratio is not None and separation_ratio < min_ratio:
        warnings.append(
            f"{source}: delimiter separation_ratio {separation_ratio:.2f} is below "
            f"{min_ratio}; the steady-state window is marginal — justify it in the artifact"
        )

    return {
        # ``sqlite`` remains as a read-compatibility alias for consumers that
        # predate generic trace sources. New consumers should read ``source``.
        "sqlite": source,
        "source": source,
        "source_kind": "sqlite" if trace.get("sqlite") else "events_json",
        "label": trace.get("label"),
        "kernel_table": table,
        "profiler_kind": meta.get("PROFILER_KIND") or "nsys",
        "nsys_product_version": meta.get("EXPORT_PRODUCT_VERSION"),
        "nsys_schema_version": meta.get("EXPORT_SCHEMA_VERSION"),
        "n_kernels_trace": len(rows),
        "n_kernels_window": len(window),
        "delimiter": delimiter,
        "merged_busy_ns": busy_ns,
        "sum_ns": sum_ns,
        "overlap_ns": sum_ns - busy_ns,
        "span_ns": span_ns,
        "measured_idle_ns": span_ns - busy_ns,
        "busy_over_span": busy_ns / span_ns if span_ns else None,
        "sum_over_span": sum_ns / span_ns if span_ns else None,
        "in_graph_fraction": (
            sum(r[1] - r[0] for r in window if r[6] is not None) / sum_ns if sum_ns else None
        ),
        "per_rank": per_rank,
        "per_rank_busy_spread": spread,
        "families": family_rows,
        "residual_symbols": residual,
        "residual_pct": 100.0 * residual_sum_ns / sum_ns if sum_ns else 0.0,
        "partition_coverage": assigned_sum_ns / sum_ns if sum_ns else 0.0,
        # Private: build() moves the selected families' chains into
        # selected_families and drops this key before mined.json is written.
        "_chains": chains,
    }


def family_chain(rows_in, window_start):
    """One family's kernels in one step window, one item per symbol in trace order.

    ``rows_in`` is ordered by start time, so dict insertion order is the order
    of each symbol's first launch.
    """
    by_symbol = {}
    for row in rows_in:
        entry = by_symbol.get(row[2])
        if entry is None:
            entry = by_symbol[row[2]] = {
                "symbol": row[2], "count": 0, "sum_ns": 0,
                "first_offset_ns": row[0] - window_start,
            }
        entry["count"] += 1
        entry["sum_ns"] += row[1] - row[0]
    kernels = [
        {
            "symbol": e["symbol"],
            "count": e["count"],
            "sum_ns": e["sum_ns"],
            "mean_ns": e["sum_ns"] / e["count"],
            "first_offset_ns": e["first_offset_ns"],
        }
        for e in by_symbol.values()
    ]
    return {
        "n_ranks": len({(r[3], r[5]) for r in rows_in}),
        "n_kernels": len(rows_in),
        "sum_ns": duration_sum([(r[0], r[1]) for r in rows_in]),
        "kernels": kernels,
    }


# ---------------------------------------------------------------------------
# step count ladder
# ---------------------------------------------------------------------------

def resolve_step_count(bs, metrics, result, sweep, config, warnings):
    """Return (n_decode_steps, source, candidates). FATAL when nothing resolves."""
    decode_avg_s = metrics["decode_avg_s"]
    num_iters = (
        config.get("num_iters")
        or (sweep.get("workload") or {}).get("num_iters")
        or result.get("num_iters")
    )
    output_len = result.get("output_len") or (sweep.get("workload") or {}).get("output_len")
    spec = metrics.get("spec_decode") or {}
    tpot_s = metrics.get("tpot_s")

    candidates = {}
    if spec.get("num_drafts") and num_iters:
        candidates["spec_decode.num_drafts"] = spec["num_drafts"] / (num_iters * bs)
    if isinstance(tpot_s, (int, float)) and tpot_s > 0:
        candidates["decode_avg_s/tpot_s"] = decode_avg_s / tpot_s
    if output_len and output_len > 1:
        candidates["output_len-1"] = float(output_len - 1)

    for source in ("spec_decode.num_drafts", "decode_avg_s/tpot_s", "output_len-1"):
        if source not in candidates:
            continue
        if source == "output_len-1":
            warnings.append(
                f"bs={bs}: tpot_s absent and no spec_decode counters; n_decode_steps from "
                f"output_len-1 assumes one output token per step"
            )
        if source == "decode_avg_s/tpot_s" and "output_len-1" in candidates:
            tolerance = config.get("step_count_tolerance_pct", DEFAULT_STEP_COUNT_TOLERANCE_PCT)
            a, b = candidates[source], candidates["output_len-1"]
            if b and abs(a - b) / b * 100.0 > tolerance:
                raise Fatal(
                    f"bs={bs}: step-count rungs disagree — decode_avg_s/tpot_s={a:.3f} vs "
                    f"output_len-1={b:.3f} (> {tolerance}%); resolve the production step count"
                )
        return candidates[source], source, candidates
    raise Fatal(
        f"bs={bs}: cannot derive the decode step count (no spec_decode counters, no tpot_s, "
        f"no output_len)"
    )


# ---------------------------------------------------------------------------
# per-BS mining
# ---------------------------------------------------------------------------

def _mean(values):
    return sum(values) / len(values) if values else 0.0


def mine_batch_size(bs, traces, metrics, result, sweep, config, families, warnings):
    avg_s = metrics.get("avg_s")
    decode_avg_s = metrics.get("decode_avg_s")
    prefill_avg_s = metrics.get("prefill_avg_s")
    if not avg_s or not decode_avg_s:
        raise Fatal(f"bs={bs}: arm has no avg_s/decode_avg_s; it cannot supply phase timing")

    # Denominator rule, mechanized: both shares divide the authoritative wall.
    decode_share_of_e2e = decode_avg_s / avg_s
    prefill_share = (avg_s - decode_avg_s) / avg_s
    phase_sum_share = (
        decode_avg_s / (prefill_avg_s + decode_avg_s) if prefill_avg_s is not None else None
    )
    stored = metrics.get("decode_share_of_e2e")
    if stored is not None and abs(stored - decode_share_of_e2e) > 1e-9:
        detail = f"; phase-sum value is {phase_sum_share:.9f}" if phase_sum_share else ""
        warnings.append(
            f"bs={bs}: stored decode_share_of_e2e={stored:.9f} disagrees with the re-derived "
            f"wall share {decode_share_of_e2e:.9f}{detail}; the re-derived value is used"
        )

    depths = [mine_one_trace(t, families, config.get("segmentation") or {}, warnings) for t in traces]
    mean_busy_ns = _mean([d["merged_busy_ns"] for d in depths])
    mean_sum_ns = _mean([d["sum_ns"] for d in depths])

    residual_pct = _mean([d["residual_pct"] for d in depths])
    allow_residual = float(config.get("allow_residual") or 0.0)
    if residual_pct > allow_residual:
        unmatched = sorted({s for d in depths for s in d["residual_symbols"]})
        raise Fatal(
            f"bs={bs}: family partition is not exhaustive — {residual_pct:.3f}% of window kernel "
            f"time is unassigned (allow_residual={allow_residual}); "
            f"{len(unmatched)} unmatched symbol(s), first: {unmatched[:3]}"
        )
    if residual_pct > 0:
        warnings.append(
            f"bs={bs}: family partition leaves {residual_pct:.3f}% of window kernel time in "
            f"{RESIDUAL_LABEL}; disclose it in the artifact"
        )

    n_steps, step_source, step_candidates = resolve_step_count(
        bs, metrics, result, sweep, config, warnings
    )
    decode_kernel_s = (mean_busy_ns / 1e9) * n_steps
    decode_busy = decode_kernel_s / decode_avg_s
    if not DECODE_BUSY_MIN <= decode_busy <= DECODE_BUSY_MAX:
        raise Fatal(
            f"bs={bs}: decode_busy={decode_busy:.4f} outside [{DECODE_BUSY_MIN},{DECODE_BUSY_MAX}] "
            f"(decode_kernel_s={decode_kernel_s:.4f}, decode_wall_s={decode_avg_s:.4f}, "
            f"n_decode_steps={n_steps:.3f} via {step_source}) — a measurement or scaling defect"
        )
    multiplier = decode_busy * decode_share_of_e2e
    if multiplier > decode_share_of_e2e + 1e-12:
        raise Fatal(
            f"bs={bs}: ceiling violated — decode_busy x decode_share_of_e2e={multiplier:.6f} "
            f"exceeds decode_avg_s/avg_s={decode_share_of_e2e:.6f}"
        )
    inter_kernel_share = (1.0 - decode_busy) * decode_share_of_e2e
    other_share = 1.0 - prefill_share - multiplier - inter_kernel_share
    if abs(other_share) >= CLOSURE_TOLERANCE:
        raise Fatal(
            f"bs={bs}: four-slice closure residual {other_share:.6f} exceeds "
            f"{CLOSURE_TOLERANCE}; the shares are wrongly denominated"
        )

    labels = sorted({label for d in depths for label in d["families"]})

    def _fam_mean(label, key):
        return _mean([d["families"].get(label, {}).get(key, 0) for d in depths])

    family_total = sum(_fam_mean(label, "merged_busy_ns") for label in labels)
    if family_total <= 0:
        raise Fatal(f"bs={bs}: family partition has zero busy time")
    ceilings = {f["label"]: f.get("physical_ceiling") for f in families}
    prefill_flags = {f["label"]: f.get("prefill_active") for f in families}
    family_out = []
    for label in labels:
        busy_ns = _fam_mean(label, "merged_busy_ns")
        fam_sum_ns = _fam_mean(label, "sum_ns")
        f_decode = busy_ns / family_total
        f_e2e = f_decode * multiplier
        ceiling = ceilings.get(label)
        family_out.append({
            "label": label,
            "f_decode": f_decode,
            "f_e2e": f_e2e,
            "busy_ns": busy_ns,
            "sum_ns": fam_sum_ns,
            "n_kernels_per_step": _fam_mean(label, "n_kernels"),
            "in_graph_fraction": (
                _fam_mean(label, "in_graph_ns") / fam_sum_ns if fam_sum_ns else None
            ),
            "physical_ceiling": ceiling,
            "addressable_e2e": (f_e2e * (1.0 - 1.0 / ceiling)) if ceiling else None,
            "prefill_active": prefill_flags.get(label),
            "per_depth_busy_ns": [
                d["families"].get(label, {}).get("merged_busy_ns", 0) for d in depths
            ],
            "symbols": sorted(
                {s for d in depths for s in d["families"].get(label, {}).get("symbols", [])}
            ),
        })
    family_out.sort(key=lambda f: -f["f_e2e"])

    return {
        "bs": bs,
        "trace": depths[-1]["sqlite"],
        "traces": [d["sqlite"] for d in depths],
        "avg_s": avg_s,
        "decode_avg_s": decode_avg_s,
        "prefill_avg_s": prefill_avg_s,
        "tpot_s": metrics.get("tpot_s"),
        "n_decode_steps": n_steps,
        "step_count_source": step_source,
        "step_count_candidates": step_candidates,
        "mean_merged_busy_per_step_s": mean_busy_ns / 1e9,
        "mean_sum_per_step_s": mean_sum_ns / 1e9,
        "decode_kernel_s": decode_kernel_s,
        "decode_busy": decode_busy,
        "decode_share_of_e2e": decode_share_of_e2e,
        "decode_share_of_phase_sum": phase_sum_share,
        "stored_decode_share_of_e2e": stored,
        "decode_kernel_share": multiplier,
        "inter_kernel_share": inter_kernel_share,
        "prefill_share": prefill_share,
        "prefill_avg_share": (prefill_avg_s / avg_s) if prefill_avg_s is not None else None,
        "ramp_residual_share": (
            (avg_s - decode_avg_s - prefill_avg_s) / avg_s if prefill_avg_s is not None else None
        ),
        "other_share": other_share,
        "f_e2e_multiplier": multiplier,
        "in_graph_fraction": _mean([d["in_graph_fraction"] or 0.0 for d in depths]),
        "partition_coverage": _mean([d["partition_coverage"] for d in depths]),
        "residual_pct": residual_pct,
        "residual_symbols": sorted({s for d in depths for s in d["residual_symbols"]}),
        "families": family_out,
        "depths": depths,
    }


# ---------------------------------------------------------------------------
# family selection (rule, floor source, and cap are fixed here)
# ---------------------------------------------------------------------------

def selection_floor(artifact_dir, warnings):
    """state.json campaign.config.min_e2e_improvement_pct as a share; else the default."""
    state_path = Path(artifact_dir) / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pct = state["campaign"]["config"]["min_e2e_improvement_pct"]
    except (OSError, KeyError, json.JSONDecodeError, TypeError):
        pct = None
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or pct < 0:
        warnings.append(
            f"selection floor: {state_path} has no usable campaign.config."
            f"min_e2e_improvement_pct; using the schema default "
            f"{DEFAULT_MIN_E2E_IMPROVEMENT_PCT}%"
        )
        pct = DEFAULT_MIN_E2E_IMPROVEMENT_PCT
    return pct / 100.0


def select_families(per_bs, config, warnings):
    """Return (selection, selected_families) by the rule in the module docstring."""
    if "selection" in config:
        raise Fatal(
            "config.selection is not accepted: the floor is state.json "
            "campaign.config.min_e2e_improvement_pct and the cap is "
            f"{SELECTION_MAX_FAMILIES}; remove the block"
        )
    floor = selection_floor(_artifact_dir(config), warnings)
    cap = SELECTION_MAX_FAMILIES
    patterns = {f.get("label"): f.get("symbol_patterns") for f in config.get("families") or []}
    best = {}
    by_bs = {}
    for row in per_bs:  # ascending bs, so a tie goes to the larger bucket
        for fam in row["families"]:
            label = fam["label"]
            if label == RESIDUAL_LABEL:
                continue
            by_bs.setdefault(label, {})[str(row["bs"])] = fam["f_e2e"]
            if label not in best or fam["f_e2e"] >= best[label][0]:
                best[label] = (fam["f_e2e"], row, fam)
    ranked = sorted(best, key=lambda label: (-best[label][0], label))
    eligible = [label for label in ranked if best[label][0] >= floor]
    if not eligible:
        warnings.append(
            f"no family reaches the selection floor {floor}; only the cross-cutting "
            f"slice is selected"
        )
    selected = []
    for rank, label in enumerate(eligible[:cap], start=1):
        f_e2e, row, fam = best[label]
        depth = next(d for d in reversed(row["depths"]) if label in d["_chains"])
        selected.append({
            "rank": rank,
            "label": label,
            "f_e2e": f_e2e,
            "bs": row["bs"],
            "f_e2e_by_bs": by_bs[label],
            "addressable_e2e": fam["addressable_e2e"],
            "physical_ceiling": fam["physical_ceiling"],
            "symbol_patterns": patterns.get(label),
            "chain": {"bs": row["bs"], "source": depth["source"], **depth["_chains"][label]},
        })
    selection = {
        "rule": (
            "rank families by their highest f_e2e across buckets (ties by label); keep "
            "f_e2e >= f_e2e_floor (state.json campaign.config.min_e2e_improvement_pct "
            "/ 100); keep at most max_families; always add the cross-cutting slice"
        ),
        "f_e2e_floor": floor,
        "max_families": cap,
        "eligible": eligible,
        "dropped_by_cap": eligible[cap:],
        "cross_cutting": CROSS_CUTTING_LABEL,
    }
    return selection, selected


# ---------------------------------------------------------------------------
# markdown emission
# ---------------------------------------------------------------------------

def _cell(value, digits):
    return "n/a" if value is None else f"{value:.{digits}f}"


def render_tables(per_bs, config):
    lines = [
        GENERATED_HEADER,
        "",
        "## Workload Dilution (per BS)",
        "",
        "| BS | total_e2e_s | prefill_s | decode_wall_s | decode_kernel_s | decode_busy "
        "| decode_share_of_e2e | inter_kernel_share | prefill_share |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in per_bs:
        lines.append(
            f"| {row['bs']} | {row['avg_s']:.4f} | {_cell(row['prefill_avg_s'], 4)} "
            f"| {row['decode_avg_s']:.4f} | {row['decode_kernel_s']:.4f} "
            f"| {row['decode_busy']:.4f} | {row['decode_share_of_e2e']:.4f} "
            f"| {row['inter_kernel_share']:.5f} | {row['prefill_share']:.4f} |"
        )
    # `decode_frac` is machine-read by ammo_state.parse_mining_md for the mining
    # enrichment; emit it here so no agent transcribes it. Largest BS = the
    # production-representative bucket.
    largest = per_bs[-1]
    lines += [
        "",
        f"`decode_frac = {largest['decode_share_of_e2e']:.4f}` (BS={largest['bs']}, "
        f"= decode_avg_s / avg_s).",
    ]
    lines += [
        "",
        "## Top Components (by f_e2e)",
        "",
        "<!-- physical_ceiling comes from mine_config.json families[].physical_ceiling; "
        "a family without a grounded roofline stays (disclosed). -->",
        "",
        "| Component | BS | decode-graph % | f_e2e | physical_ceiling "
        "| f_e2e x (1-1/ceiling) | prefill-active? |",
        "|---|---|---|---|---|---|---|",
    ]
    prefill_ceiling = config.get("prefill_physical_ceiling")
    for row in per_bs:
        for fam in row["families"]:
            ceiling = fam["physical_ceiling"]
            addressable = fam["addressable_e2e"]
            flag = fam["prefill_active"]
            lines.append(
                f"| {fam['label']} | {row['bs']} | {fam['f_decode'] * 100:.2f} "
                f"| {fam['f_e2e']:.4f} "
                f"| {f'{ceiling:.3f}x' if ceiling else '(disclosed)'} "
                f"| {f'{addressable:.4f}' if addressable is not None else '(disclosed)'} "
                f"| {'Yes' if flag else ('No' if flag is not None else '(agent)')} |"
            )
        lines.append(
            f"| **inter_kernel_slack** | {row['bs']} | — | {row['inter_kernel_share']:.5f} "
            f"| 1.000x | {row['inter_kernel_share']:.5f} | — |"
        )
        addressable_prefill = (
            f"{row['prefill_share'] * (1.0 - 1.0 / prefill_ceiling):.4f}"
            if prefill_ceiling
            else "(disclosed)"
        )
        lines.append(
            f"| **prefill (all)** | {row['bs']} | — | {row['prefill_share']:.4f} "
            f"| {f'{prefill_ceiling:.3f}x' if prefill_ceiling else '(disclosed)'} "
            f"| {addressable_prefill} | (is prefill) |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

def _resolve(base, value):
    path = Path(value)
    return path if path.is_absolute() else (Path(base) / path)


def _artifact_dir(config):
    """config artifact_dir, else the campaign dir (AMMO_ARTIFACT_DIR), else the cwd."""
    return config.get("artifact_dir") or os.environ.get("AMMO_ARTIFACT_DIR") or "."


def build(config, warnings):
    artifact_dir = _artifact_dir(config)
    families = compile_families(config.get("families"))
    sweep_path = _resolve(
        artifact_dir, config.get("e2e_results") or config.get("e2e_latency_results") or ""
    )
    if not sweep_path.is_file():
        raise Fatal(f"e2e_results not found: {sweep_path}")
    sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
    arm = config.get("arm") or "baseline"

    by_bs = {}
    for trace in config.get("traces") or []:
        if "bs" not in trace or not (trace.get("sqlite") or trace.get("events_json")):
            raise Fatal(
                "every config.traces entry needs bs and one of sqlite or events_json"
            )
        if trace.get("sqlite") and trace.get("events_json"):
            raise Fatal("a config.traces entry cannot set both sqlite and events_json")
        resolved = dict(trace)
        source_key = "sqlite" if trace.get("sqlite") else "events_json"
        resolved[source_key] = str(_resolve(artifact_dir, trace[source_key]))
        by_bs.setdefault(int(trace["bs"]), []).append(resolved)
    if not by_bs:
        raise Fatal("config.traces is empty; nothing to mine")

    results = {}
    for result in sweep.get("results") or []:
        if "batch_size" in result:
            results[int(result["batch_size"])] = result

    per_bs = []
    for bs in sorted(by_bs):
        result = results.get(bs)
        if result is None:
            raise Fatal(f"bs={bs} has traces but no matching result in {sweep_path}")
        metrics = dict(result.get(arm) or {})
        for key, value in (result.get(arm) or {}).get("metrics", {}).items():
            metrics.setdefault(key, value)
        per_bs.append(
            mine_batch_size(bs, by_bs[bs], metrics, result, sweep, config, families, warnings)
        )
    selection, selected_families = select_families(per_bs, config, warnings)
    for row in per_bs:
        for depth in row["depths"]:
            depth.pop("_chains", None)

    trace_provenance = [
        {
            "bs": row["bs"],
            "sqlite": depth["sqlite"],
            "source": depth["source"],
            "source_kind": depth["source_kind"],
            "label": depth["label"],
            "kernel_table": depth["kernel_table"],
            "profiler_kind": depth["profiler_kind"],
            "nsys_product_version": depth["nsys_product_version"],
            "nsys_schema_version": depth["nsys_schema_version"],
            "n_kernels_trace": depth["n_kernels_trace"],
        }
        for row in per_bs
        for depth in row["depths"]
    ]

    def _unique(key):
        values = sorted({t[key] for t in trace_provenance if t[key] is not None})
        if not values:
            return None
        if len(values) > 1:
            warnings.append(f"traces mix {key} values: {', '.join(values)}")
            return ", ".join(values)
        return values[0]

    kernel_tables = sorted({t["kernel_table"] for t in trace_provenance})
    return {
        "schema": SCHEMA,
        "provenance": {
            "profiler_kind": _unique("profiler_kind"),
            "nsys_product_version": _unique("nsys_product_version"),
            "export_schema_version": _unique("nsys_schema_version"),
            "kernel_table": ", ".join(kernel_tables),
            "traces": trace_provenance,
        },
        "config_echo": config,
        "e2e_results": str(sweep_path),
        "arm": arm,
        "selection": selection,
        "selected_families": selected_families,
        "per_bs": per_bs,
        "warnings": warnings,
    }


def resolve_round(config, artifact_dir, warnings):
    """config round, else state.json campaign.current_round, else 1 with a WARN."""
    if config.get("round") is not None:
        return int(config["round"])
    state_path = Path(artifact_dir) / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return int(state["campaign"]["current_round"])
    except (OSError, KeyError, json.JSONDecodeError, TypeError, ValueError):
        warnings.append(
            f"round absent from the config and {state_path} has no readable "
            f"campaign.current_round; using rounds/1"
        )
        return 1


# ---------------------------------------------------------------------------
# per-bucket slicer (subcommand `slice`)
# ---------------------------------------------------------------------------

def _one_setting(flag_value, config_value, flag, key):
    if flag_value and config_value and flag_value != config_value:
        raise Fatal(f"{flag} {flag_value!r} disagrees with mine_config.json {key} {config_value!r}")
    return flag_value or config_value


def _to_trace_ns(unix_s, session_start_ns):
    exact = Decimal(str(unix_s)) * 1_000_000_000
    return int(exact.to_integral_value(rounding=ROUND_HALF_EVEN)) - session_start_ns


def _profiled_waves(meta_path):
    """Batch size -> its waves with profiled true and warmup false."""
    if not meta_path.is_file():
        raise Fatal(f"{meta_path}: profile_meta JSON not found")
    doc = json.loads(meta_path.read_text(encoding="utf-8"), parse_float=Decimal)
    meta = doc.get("profile_meta", doc) if isinstance(doc, dict) else None
    waves = meta.get("waves") if isinstance(meta, dict) else None
    if not isinstance(waves, list):
        raise Fatal(f"{meta_path}: no profile_meta.waves[] list")
    by_bs = {}
    for wave in waves:
        if isinstance(wave, dict) and wave.get("profiled") is True and wave.get("warmup") is False:
            by_bs.setdefault(int(wave["batch_size"]), []).append(wave)
    if not by_bs:
        raise Fatal(f"{meta_path}: no wave has profiled true and warmup false")
    return by_bs


def _timed_columns(con, table):
    columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    if "start" in columns:
        return "start", "end" in columns
    if "timestamp" in columns:
        return "timestamp", False
    return None, False


def _rank_name(rank):
    return f"dev{rank[0]}_pid{rank[1]}"


def plan_slice(con, kernel_table, bs, waves, session_start_ns, delimiter, full_batch_symbol):
    """Return the cut for one bucket: window, per-rank cuts, and counts.

    Steps are counted per rank (deviceId, globalPid), the key mine_one_trace
    uses for per_rank. TP ranks run the same steps a few us apart, so a cut at
    one global time would leave a leading rank's open-step delimiter in the
    slice, and the miner would read only the inter-rank skew as the step.
    """
    window_start = min(_to_trace_ns(w["start_unix"], session_start_ns) for w in waves)
    window_end = max(_to_trace_ns(w["end_unix"], session_start_ns) for w in waves)
    if not 0 <= window_start < window_end:
        raise Fatal(
            f"bs={bs}: wave window [{window_start}, {window_end}] ns is not inside the "
            f"capture; check the session start against profile_meta"
        )
    grid = ", k.gridX" if full_batch_symbol else ""
    try:
        rows = con.execute(
            f"SELECT k.start, k.end, s.value, k.deviceId, k.globalPid{grid} "
            f"FROM {kernel_table} k JOIN StringIds s ON s.id = k.demangledName "
            f"WHERE k.start >= ? AND k.end <= ? ORDER BY k.start, k.end",
            (window_start, window_end),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise Fatal(f"bs={bs}: cannot read {kernel_table}: {exc}") from exc
    by_rank = {}
    for row in rows:
        by_rank.setdefault((row[3], row[4]), []).append(row)
    hits = {
        rank: [i for i, r in enumerate(rank_rows) if delimiter in r[2]]
        for rank, rank_rows in by_rank.items()
    }
    counts = {_rank_name(rank): len(h) for rank, h in sorted(hits.items())}
    if len(set(counts.values())) > 1:
        raise Fatal(
            f"bs={bs}: ranks disagree on the number of delimiter {delimiter!r} launches "
            f"inside the wave window ({counts}); every rank must run the same steps"
        )
    n_hits = next(iter(counts.values()), 0)
    if n_hits < 2:
        raise Fatal(
            f"bs={bs}: {n_hits} launch(es) of delimiter {delimiter!r} per rank inside the "
            f"wave window; a closed step needs two"
        )
    # Index of the delimiter that opens the first step the slice drops.
    end = n_hits - 1
    if full_batch_symbol:
        for j in range(n_hits - 2, -1, -1):
            if any(
                full_batch_symbol in r[2] and r[5] == bs
                for rank, rank_rows in by_rank.items()
                for r in rank_rows[hits[rank][j]:hits[rank][j + 1]]
            ):
                end = j + 1
                break
        else:
            raise Fatal(
                f"bs={bs}: no closed step holds a {full_batch_symbol!r} launch with gridX={bs}"
            )
    rank_cuts = {rank: by_rank[rank][hits[rank][end]][0] for rank in by_rank}
    kept = [r for r in rows if r[0] < rank_cuts[(r[3], r[4])]]
    return {
        "bs": bs,
        "waves": [int(w["index"]) for w in waves],
        "wave_window_ns": [window_start, window_end],
        "cut_ns": min(rank_cuts.values()),
        "rank_cuts_ns": {_rank_name(rank): cut for rank, cut in sorted(rank_cuts.items())},
        "_rank_cuts": rank_cuts,
        "delimiter_launches": sum(
            1 for rank, h in hits.items() for i in h if by_rank[rank][i][0] < rank_cuts[rank]
        ),
        "n_kernels": len(kept),
        "sum_ns": duration_sum([(r[0], r[1]) for r in kept]),
        "dropped_tail_kernels": len(rows) - len(kept),
    }


def write_slice(full_path, dest, kernel_table, window_start, window_end, cut_ns, rank_cuts):
    """Copy the export; keep each timed table's rows inside the window, before the cut.

    The mined kernel table is cut per rank at ``rank_cuts``; every other timed
    table at ``cut_ns``, the earliest rank's cut.
    """
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{full_path}?mode=ro", uri=True)
    out = sqlite3.connect(str(tmp))
    try:
        src.backup(out)
        tables = [r[0] for r in out.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            time_col, has_end = _timed_columns(out, table)
            if time_col is None:
                continue
            keep = f'"{time_col}" >= ?'
            params = [window_start]
            if table == kernel_table:
                per_rank = []
                for (device, pid), cut in sorted(rank_cuts.items()):
                    per_rank.append('("deviceId" = ? AND "globalPid" = ? AND "start" < ?)')
                    params += [device, pid, cut]
                keep += f" AND ({' OR '.join(per_rank)})"
            else:
                keep += f' AND "{time_col}" < ?'
                params.append(cut_ns)
            if has_end:
                keep += ' AND ("end" IS NULL OR "end" <= ?)'
                params.append(window_end)
            out.execute(f'DELETE FROM "{table}" WHERE NOT ({keep})', params)
        out.commit()
        out.execute("VACUUM")
    finally:
        out.close()
        src.close()
    os.replace(tmp, dest)


def build_slices(args, config, warnings):
    artifact_dir = Path(_artifact_dir(config))
    segmentation = config.get("segmentation") or {}
    if args.config and segmentation.get("policy") != "delimiter_symbol":
        raise Fatal(
            f"mine_config.json segmentation.policy is {segmentation.get('policy')!r}; a slice "
            f"holds many decode steps, so mining it needs policy delimiter_symbol, and the "
            f"slicer cuts at that symbol"
        )
    delimiter = _one_setting(args.delimiter, segmentation.get("symbol"),
                             "--delimiter", "segmentation.symbol")
    if not delimiter:
        raise Fatal("slice needs a step delimiter: segmentation.symbol in --config, or --delimiter")
    full_batch_symbol = _one_setting(args.full_batch_symbol,
                                     segmentation.get("full_batch_symbol"),
                                     "--full-batch-symbol", "segmentation.full_batch_symbol")
    rounds_dir = None
    if not (args.sqlite and args.profile_meta and args.out_dir):
        rounds_dir = artifact_dir / "rounds" / str(resolve_round(config, artifact_dir, warnings))
    nsys_dir = rounds_dir / "profiling" / "nsys" if rounds_dir else None
    full_path = Path(args.sqlite) if args.sqlite else nsys_dir / f"{args.label}.sqlite"
    meta_path = (Path(args.profile_meta) if args.profile_meta
                 else rounds_dir / "sweeps" / "profiling" / "e2e_latency_results.json")
    out_dir = Path(args.out_dir) if args.out_dir else nsys_dir
    if not full_path.is_file():
        raise Fatal(f"{full_path}: full sqlite export not found")

    waves_by_bs = _profiled_waves(meta_path)
    wanted = sorted(set(args.bs)) if args.bs else sorted(waves_by_bs)
    missing = [bs for bs in wanted if bs not in waves_by_bs]
    if missing:
        raise Fatal(f"{meta_path}: no profiled, non-warmup wave for bs {missing}")

    con = sqlite3.connect(f"file:{full_path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        kernel_table = next((t for t in KERNEL_TABLES if t in tables), None)
        if kernel_table is None or "StringIds" not in tables:
            raise Fatal(f"{full_path}: needs a CUPTI kernel table and StringIds")
        if SESSION_START_TABLE not in tables:
            raise Fatal(
                f"{full_path}: no {SESSION_START_TABLE}; cannot map profile_meta wall clock "
                f"to trace time"
            )
        row = con.execute(f"SELECT utcEpochNs FROM {SESSION_START_TABLE}").fetchone()
        if not row or row[0] is None:
            raise Fatal(f"{full_path}: {SESSION_START_TABLE} has no utcEpochNs")
        session_start_ns = int(row[0])
        plans = [
            plan_slice(con, kernel_table, bs, waves_by_bs[bs], session_start_ns, delimiter,
                       full_batch_symbol)
            for bs in wanted
        ]
    finally:
        con.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    for plan in plans:
        dest = out_dir / f"{args.label}_bs{plan['bs']}.sqlite"
        if dest.resolve() == full_path.resolve():
            raise Fatal(f"{dest}: refusing to overwrite the full export")
        write_slice(full_path, dest, kernel_table, plan["wave_window_ns"][0],
                    plan["wave_window_ns"][1], plan["cut_ns"], plan.pop("_rank_cuts"))
        plan["output"] = str(dest)
    record = {
        "schema": "mine_trace_slices/1",
        "source": str(full_path),
        "profile_meta": str(meta_path),
        "session_start_ns": session_start_ns,
        "delimiter": delimiter,
        "full_batch_symbol": full_batch_symbol,
        "rule": (
            "window = the bucket's profiled, non-warmup waves; on each rank (deviceId, "
            "globalPid) the kernel cut is the start of that rank's delimiter launch that "
            "opens the step after the last kept closed step; every other table with start "
            "keeps window start <= start < cut_ns (the earliest rank cut), and end <= "
            "window end; a table with timestamp keeps window start <= timestamp < cut_ns; "
            "other tables are copied whole"
        ),
        "slices": plans,
        "warnings": warnings,
    }
    record_path = out_dir / f"{args.label}_slices.json"
    record_path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record, record_path


def slice_main(argv):
    parser = argparse.ArgumentParser(
        prog="mine_trace.py slice",
        description="Cut one sqlite per bucket from the full export, ending at a complete step.",
    )
    parser.add_argument("--config", help="mine_config.json: artifact_dir, round, segmentation")
    parser.add_argument("--sqlite", help="full export; default {artifact_dir}/rounds/{N}/"
                        "profiling/nsys/{label}.sqlite")
    parser.add_argument("--profile-meta", help="JSON holding profile_meta; default "
                        "{artifact_dir}/rounds/{N}/sweeps/profiling/e2e_latency_results.json")
    parser.add_argument("--label", default="baseline", help="capture label (default baseline)")
    parser.add_argument("--bs", type=int, action="append",
                        help="bucket to cut; repeatable; default every profiled bucket")
    parser.add_argument("--delimiter", help="step delimiter symbol when there is no --config")
    parser.add_argument("--full-batch-symbol",
                        help="end after the last closed step holding this symbol with gridX == BS")
    parser.add_argument("--out-dir", help="default {artifact_dir}/rounds/{N}/profiling/nsys")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
    warnings = []
    try:
        record, record_path = build_slices(args, config, warnings)
    except Fatal as exc:
        for warning in warnings:
            print(f"WARN: {warning}", file=sys.stderr)
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    for plan in record["slices"]:
        print(
            f"bs={plan['bs']}: waves {plan['waves']} cut at {plan['cut_ns']} ns, "
            f"{plan['delimiter_launches']} delimiter launches, {plan['n_kernels']} kernels "
            f"({plan['dropped_tail_kernels']} dropped from the tail) -> {plan['output']}"
        )
    print(f"wrote {record_path}")
    return 0


def main(argv=None):
    campaign_paths.apply_env()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "slice":
        return slice_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="mine_config.json (agent judgment)")
    parser.add_argument("--out-json", help="default: {artifact_dir}/rounds/{N}/mining/mined.json")
    parser.add_argument("--out-md", help="default: {artifact_dir}/rounds/{N}/mining/tables.md")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    warnings = []
    try:
        mined = build(config, warnings)
    except Fatal as exc:
        for warning in warnings:
            print(f"WARN: {warning}", file=sys.stderr)
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    if args.out_json and args.out_md:
        mining_dir = None
    else:
        artifact_dir = _artifact_dir(config)
        mining_dir = Path(artifact_dir) / "rounds" / str(
            resolve_round(config, artifact_dir, warnings)
        ) / "mining"
    out_json = Path(args.out_json) if args.out_json else mining_dir / "mined.json"
    out_md = Path(args.out_md) if args.out_md else mining_dir / "tables.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(mined, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    out_md.write_text(render_tables(mined["per_bs"], config), encoding="utf-8")

    for warning in warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
