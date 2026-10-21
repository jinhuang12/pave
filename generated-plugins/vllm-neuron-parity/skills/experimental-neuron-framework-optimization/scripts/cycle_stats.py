#!/usr/bin/env python3
"""cycle_stats.py — skill-owned robust cycle reducer for the kernel-loop oracle.

A SELF-CONTAINED clone of the pieces of nkilib_testing's metrics_collector that turn a set of
profiled executions into a robust cycle number:
  * `_parse_show_session_json_files` — read CycleCount per NeuronCore across every
    show_session_*.json in an inference-artifact directory;
  * `_iqr_filtered_average` — Interquartile-Range outlier filtering + average, with the QCD
    (Quartile Coefficient of Dispersion = IQR/median) as a dispersion read.

Why clone instead of import: measure.py must run under any interpreter (it is invoked with
``--shadow ''`` so it does not depend on a repo-local test harness), and the loop's oracle must not
silently change if the installed harness changes. This module has NO third-party dependency
(pure-stdlib percentile, so it works without numpy) and no nkilib import, so the reducer the loop
trusts is versioned in the skill next to measure.py.

Rationale for the reduction (the reason this file exists): the on-device ``CycleCount`` is a REAL
profiled-execution measurement and varies run-to-run; a single sample (or a max over one exec's
graphs) can read many percent off, so a lone low draw reads as a phantom win and a lone high draw
hides a real one. IQR-filtered average over MANY profiled execs (one bring-up, N traces) rejects the
outliers a single sample would bank. Pair with multi-exec profiling so N samples come from one run.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path


def _percentile(sorted_vals, q):
    """Linear-interpolation percentile (matches numpy's default 'linear' method), pure stdlib.
    `sorted_vals` must be ascending; `q` in [0,100]."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    rank = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return float(sorted_vals[lo]) + (float(sorted_vals[hi]) - float(sorted_vals[lo])) * frac


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    if n % 2:
        return float(s[n // 2])
    return (float(s[n // 2 - 1]) + float(s[n // 2])) / 2.0


def iqr_filtered_average(cycle_list):
    """Clone of the harness reducer: drop IQR outliers, average the rest.

    Returns (filtered_average, outliers_count, qcd, filtered_values). qcd = IQR/median*100
    (Quartile Coefficient of Dispersion) — a scale-free dispersion read; a large qcd means the
    samples are too noisy to resolve a small win and you should profile more execs.
    """
    vals = [float(x) for x in cycle_list if isinstance(x, (int, float))]
    if not vals:
        return None, 0, -1.0, []
    if len(vals) < 4:
        # too few points for a meaningful IQR; median is the robust central value
        med = _median(vals)
        return med, 0, 0.0, vals
    s = sorted(vals)
    q1, q3, med = _percentile(s, 25), _percentile(s, 75), _median(s)
    iqr = q3 - q1
    qcd = ((iqr / med) * 100.0) if med and med > 0 else -1.0
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    filtered = [x for x in s if lo <= x <= hi]
    outliers = len(s) - len(filtered)
    avg = (sum(filtered) / len(filtered)) if filtered else (sum(s) / len(s))
    return avg, outliers, qcd, filtered


def parse_show_sessions(paths):
    """Clone of _parse_show_session_json_files: return {nc_id: [CycleCount, ...]} across all files.

    Each show_session_*.json is one profiled execution; within it, NeffNodes[*].NodeInfo.Graphs[*]
    carry CycleCount per NeuronCore. Collecting across files gives per-core samples over execs.
    """
    per_core = {}
    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
        except Exception:
            continue
        for node in data.get("NeffNodes", data.get("neff_nodes", []) or []):
            ni = node.get("NodeInfo") or node.get("node_info") or {}
            nc = ni.get("NC", ni.get("nc", 0))
            for g in (ni.get("Graphs") or ni.get("graphs") or []):
                c = g.get("CycleCount", g.get("cycle_count"))
                if isinstance(c, (int, float)):
                    per_core.setdefault(nc, []).append(float(c))
    return per_core


def robust_cycle_count(paths):
    """The oracle number for a set of profiled executions.

    Per NeuronCore, IQR-filter-average its per-exec CycleCounts; the kernel's cycle cost is the MAX
    across cores (the slowest core bounds the synchronized step — same spatial reduction the old
    single-file 'max across graphs' used, but now robust over execs). Returns a dict:
      {cycles(int|None, the max-core IQR-avg), n_samples, cycles_min, cycles_max, cycles_spread
       ((max-min)/cycles over ALL samples), qcd_max (worst per-core dispersion), per_core{...}}.
    """
    per_core = parse_show_sessions(paths)
    all_samples = [c for lst in per_core.values() for c in lst]
    if not all_samples:
        return {"cycles": None, "n_samples": 0, "cycles_min": None, "cycles_max": None,
                "cycles_spread": None, "qcd_max": None, "per_core": {}}
    core_avgs, qcds, summary = [], [], {}
    for nc, lst in sorted(per_core.items()):
        avg, outliers, qcd, filt = iqr_filtered_average(lst)
        core_avgs.append(avg)
        qcds.append(qcd)
        summary[str(nc)] = {"iqr_avg": int(avg) if avg is not None else None,
                            "n": len(lst), "outliers": outliers, "qcd": round(qcd, 4),
                            "samples": [int(x) for x in lst]}
    cycles = int(max(core_avgs)) if core_avgs else None
    lo, hi = min(all_samples), max(all_samples)
    spread = round((hi - lo) / cycles, 4) if cycles else None
    return {
        "cycles": cycles,
        "n_samples": len(all_samples),
        "cycles_min": int(lo),
        "cycles_max": int(hi),
        "cycles_spread": spread,
        "qcd_max": round(max(qcds), 4) if qcds else None,
        "per_core": summary,
    }


def find_show_sessions(repo, out_dir):
    """ALL out-*/infer_result/show_session_*.json under <repo>/<out_dir> (each = one profiled exec)."""
    base = Path(repo) / out_dir
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir()):
        if d.name.startswith("out-") and (d / "infer_result").is_dir():
            out.extend(sorted((d / "infer_result").glob("show_session_*.json")))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Robust cycle reduction over show_session_*.json")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out-dir", default="neuron_test_output")
    a = ap.parse_args()
    files = find_show_sessions(a.repo, a.out_dir)
    print(json.dumps({"files": [str(f) for f in files], **robust_cycle_count(files)}, indent=2))
