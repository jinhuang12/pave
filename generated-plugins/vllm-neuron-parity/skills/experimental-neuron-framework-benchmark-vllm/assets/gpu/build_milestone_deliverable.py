#!/usr/bin/env python3
"""Milestone deliverable — Trainium throughput as a % of GPU at equal interactivity.

Produces the HEADLINE number from the "Neuron Agentic Optimization: Performance
Milestones" doc: **the percentage of GPU per-server throughput that Trainium
achieves at the SAME interactivity (per-user output rate)**, and which milestone
bar (Table 3) that percentage clears.

WHAT IT CONSUMES
----------------
One or more tidy `all_cells.csv` files as written by build_deliverables.py — one
per platform (typically one Trainium csv + one GPU csv). The columns this tool
reads from all_cells.csv (verified against build_deliverables.py parse_cell):
    otps          box-level per-server output throughput (tok/s)  [Y axis]
                  NB: the milestone doc calls this "output_throughput"; the tidy
                  csv column is literally `otps` (parse_cell reads the raw
                  result.json `output_throughput` field into the `otps` column).
                  For safety this tool also accepts a column literally named
                  `output_throughput` if `otps` is absent.
    per_user_otps per-user output rate = otps/conc (tok/s/user)   [X axis]
                  This is the milestone doc's "interactivity".
    shape         "isl/osl" workload string (e.g. "8192/1024")
    instance      EC2/box label (p5, p5en, b200, b300, trn2, …)
    conc          concurrency (annotation only)
    sharding      TP×DP[×EP] label (annotation only)
    itps          per-server input throughput (tok/s) — OPTIONAL, cost only
    usable        bool saturation guard — OPTIONAL (falls back to otps>0 / fail_frac)
    fail_frac     failure fraction — OPTIONAL fallback usability filter
all_cells.csv does NOT carry a `baseline` or `platform` column (build_deliverables
reads baseline/platform from regime_meta.json but does not propagate them to the
tidy csv), so the milestone serving-optimization baseline is taken as a CLI flag
(--baseline). See the "GPU baseline" note in the module docstring's tail.

DOC DEFINITIONS IMPLEMENTED (Performance Milestones doc, "Methodology")
-----------------------------------------------------------------------
* Comparison is SINGLE-INSTANCE / single-box: no disaggregated inference (DI),
  per the doc's Q1 answer. Throughput is read as PER-INSTANCE, i.e. this script
  ASSUMES every cell's sharding fills its box, and does NOT normalize by the cores
  or GPUs a sharding actually used — there is no per-chip column to divide by.
  That assumption is ENFORCED on the GPU side (sweep_bench.py rejects any sharding
  with tp*dp != gpu_count) but only WARNED about on the Neuron side, where
  tp*dp <= logical cores is legal (neuron_sweep._box_fill_warning). So a partial-box
  Trainium cell (e.g. tp8_dp1 = 8 of trn2.48xlarge's 64 logical cores) enters the
  frontier as if it were a whole box and UNDERSTATES Trainium — the error direction
  that quietly deflates milestone_pct. Feed this script box-filling shardings only,
  or state the core budget alongside the number.
* Each platform's operating envelope is its PARETO FRONTIER of throughput
  (Y = otps, tok/s/server) vs interactivity (X = per_user_otps, tok/s/user):
  the non-dominated upper-right hull over ALL shardings × concurrencies. (Same
  frontier concept as build_deliverables.py `_pareto_frontier`.)
* The milestone metric compares the two platforms AT EQUAL INTERACTIVITY: for
  each Trainium frontier point at interactivity X, we linearly interpolate the
  GPU frontier's throughput at that same X and take
        milestone_pct = 100 * tput_trn(X) / tput_gpu_interp(X).
  Only the interactivity range where BOTH frontiers are defined is used — no
  extrapolation. Trn points outside the GPU curve's X-range are reported as
  "out of GPU range" and excluded from the % summary.

MILESTONE BARS (Performance Milestones doc, Table 3)
----------------------------------------------------
    >= 130%  High  (leadership)         — Trainium leads GPU at equal interactivity
    >= 100%  Mid   (parity)             — Trainium matches GPU throughput
    >=  60%  Low   (cost-parity-without-PPAs)
                                        — at trn2's public list price, ~60% of GPU
                                          throughput reaches $/token parity even
                                          before any price/perf adjustments (PPAs)
    <  60%   below low bar

COST ANNOTATION (optional)
--------------------------
If BOTH instances are priced in build_cost_deliverables.PRICE_PER_HR, the tool
adds tokens/$ (total = input+output tok/s × 3600 / $/hr, whole box, public list
price) at the representative compared point, and states the doc's "≈60% ⇒ cost
parity without PPAs" reading. If either instance is unpriced, cost is SKIPPED
with a warning (never a crash).

EXAMPLE
-------
    python build_milestone_deliverable.py \
        --trn ./trn_deliverables/all_cells.csv --trn-instance trn2 \
        --gpu ./gpu_deliverables/all_cells.csv --gpu-instance p5 \
        --baseline controlled \
        --model-name "Llama-3.1-70B" \
        --at-interactivity 30 \
        --out ./deliverables/milestone

OUTPUTS (in --out)
------------------
    milestone_<shape>_<baseline>.md   per-shape table + methodology header
    milestone_<shape>_<baseline>.png  both frontiers overlaid, Trn points marked,
                                      GPU interpolation drawn as drop-lines
    milestone_summary.csv             tidy: model, shape, baseline, instance_trn,
                                      instance_gpu, interactivity, tput_trn,
                                      tput_gpu_interp, milestone_pct, bar

Deterministic, no network. stdlib + pandas/matplotlib/numpy/tabulate only.

GPU baseline identification: the GPU driver was not changed to carry the
serving-optimization baseline, so GPU regime_meta.json has no `baseline` key and
neither csv carries one. This tool therefore takes the baseline as the CLI flag
`--baseline` and applies it to BOTH platforms (the comparison is only valid
feature-matched). It MAY additionally be read from regime_meta.json when present,
but the csv is the only input here, so CLI is authoritative.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# --- milestone bars (Performance Milestones doc, Table 3) -------------------
# Ordered high→low; first threshold met wins. Impact wording matches the doc.
MILESTONE_BARS = [
    (130.0, "High (leadership)"),
    (100.0, "Mid (parity)"),
    (60.0, "Low (cost-parity-without-PPAs)"),
]
BELOW_BAR = "below low bar"

FAIL_THRESHOLD = 0.05  # matches build_deliverables.FAIL_THRESHOLD

# Dataviz palette (references/palette.md categorical slots — light mode). Trn and
# GPU get distinct, CVD-safe leading slots; the drop-lines use muted ink.
COLOR_TRN = "#2a78d6"   # slot 1 blue
COLOR_GPU = "#eb6834"   # slot 2 orange
COLOR_DROP = "#898781"  # muted axis/label ink
COLOR_INTERP = "#4a3aa7"  # slot 7 violet (GPU interp markers)


def bar_for_pct(pct: float) -> str:
    """Assign the milestone bar for a throughput percentage (Table 3)."""
    for thresh, label in MILESTONE_BARS:
        if pct >= thresh:
            return label
    return BELOW_BAR


# --- price table (reuse build_cost_deliverables) ----------------------------

def _load_price_table() -> tuple[dict, dict]:
    """Return (PRICE_PER_HR, GPU_LABEL) from build_cost_deliverables, importing it
    from this script's own directory. Falls back to empty dicts (cost simply
    skipped) if the module can't be imported — never fatal."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from build_cost_deliverables import PRICE_PER_HR, GPU_LABEL  # type: ignore
        return dict(PRICE_PER_HR), dict(GPU_LABEL)
    except Exception as e:  # pragma: no cover - defensive
        print(f"  (cost annotation unavailable — could not import "
              f"build_cost_deliverables: {e})")
        return {}, {}


# --- loading / usability ----------------------------------------------------

def _throughput_col(df: pd.DataFrame) -> str:
    """The box-level throughput column: `otps` in the tidy csv; accept the doc's
    `output_throughput` name as a fallback."""
    if "otps" in df.columns:
        return "otps"
    if "output_throughput" in df.columns:
        return "output_throughput"
    raise SystemExit(
        "all_cells.csv has neither an `otps` nor `output_throughput` column — "
        "is this a build_deliverables.py output?")


def load_platform(csv: Path, instance: str | None, label: str) -> pd.DataFrame:
    """Read one platform's all_cells.csv, select its instance rows, keep usable
    cells, and normalize the columns this tool needs (tput, x, shape)."""
    if not csv.exists():
        raise SystemExit(f"{label} csv not found: {csv}")
    df = pd.read_csv(csv)
    if df.empty:
        raise SystemExit(f"{label} csv is empty: {csv}")

    tcol = _throughput_col(df)
    if "per_user_otps" not in df.columns:
        raise SystemExit(
            f"{label} csv missing `per_user_otps` (interactivity) column: {csv}")
    if "shape" not in df.columns:
        raise SystemExit(f"{label} csv missing `shape` column: {csv}")

    # Instance selection. If the file carries an `instance` column and the caller
    # named one, filter to it; otherwise take the whole file as this platform.
    if instance is not None and "instance" in df.columns:
        have = sorted(df["instance"].astype(str).unique())
        sel = df[df["instance"].astype(str) == str(instance)].copy()
        if sel.empty:
            raise SystemExit(
                f"{label}: no rows with instance == {instance!r} in {csv} "
                f"(instances present: {have}).")
        df = sel
    else:
        if instance is not None and "instance" not in df.columns:
            print(f"  ({label}: no `instance` column in {csv} — treating the "
                  f"whole file as instance {instance!r})")
        df = df.copy()
        if "instance" not in df.columns:
            df["instance"] = instance if instance is not None else label

    # Usability filter — reuse build_deliverables' `usable` when present; else
    # drop non-positive throughput and (if available) high fail_frac cells.
    n0 = len(df)
    if "usable" in df.columns:
        df = df[df["usable"].astype(bool)].copy()
    else:
        df = df[pd.to_numeric(df[tcol], errors="coerce").fillna(0) > 0].copy()
        if "fail_frac" in df.columns:
            df = df[pd.to_numeric(df["fail_frac"], errors="coerce").fillna(1.0)
                    <= FAIL_THRESHOLD].copy()
    # Guard against zero/negative throughput even when `usable` was trusted.
    df = df[pd.to_numeric(df[tcol], errors="coerce").fillna(0) > 0].copy()
    df["_tput"] = pd.to_numeric(df[tcol], errors="coerce")
    df["_x"] = pd.to_numeric(df["per_user_otps"], errors="coerce")
    df = df[df["_x"] > 0].copy()
    n1 = len(df)
    if n1 == 0:
        raise SystemExit(f"{label}: no usable cells after filtering ({n0} rows read).")
    print(f"  {label}: {n1}/{n0} usable cells, shapes {sorted(df['shape'].unique())}")
    return df


# --- pareto frontier (ported from build_deliverables._pareto_frontier) ------

def pareto_frontier(rows: list[dict]) -> list[dict]:
    """Non-dominated upper-right envelope on (_x, _tput); higher is better on both.
    Returns points sorted by interactivity (_x) ascending. Ports the concept of
    build_deliverables.py _pareto_frontier (which uses per_user_otps/otps)."""
    front = []
    for p in rows:
        dominated = any(
            q is not p
            and q["_tput"] >= p["_tput"] and q["_x"] >= p["_x"]
            and (q["_tput"] > p["_tput"] or q["_x"] > p["_x"])
            for q in rows
        )
        if not dominated:
            front.append(p)
    front.sort(key=lambda r: r["_x"])
    # Dedupe on identical interactivity: keep the highest-throughput point so the
    # curve is a strictly increasing function of X (np.interp needs monotone xp).
    deduped: list[dict] = []
    for p in front:
        if deduped and abs(p["_x"] - deduped[-1]["_x"]) < 1e-12:
            if p["_tput"] > deduped[-1]["_tput"]:
                deduped[-1] = p
        else:
            deduped.append(p)
    return deduped


def _frontier_for_shape(df: pd.DataFrame, shape: str) -> list[dict]:
    g = df[df["shape"] == shape]
    if g.empty:
        return []
    keep_cols = [c for c in ("_x", "_tput", "conc", "sharding", "itps",
                             "instance") if c in g.columns]
    return pareto_frontier(g[keep_cols].to_dict("records"))


# --- core comparison --------------------------------------------------------

def compare_shape(trn_front: list[dict], gpu_front: list[dict]) -> tuple[list[dict], dict]:
    """For each Trn frontier point, interpolate GPU throughput at the same
    interactivity (within the GPU curve's X-range only) and compute milestone_pct.
    Returns (rows, meta) where meta carries the overlap range + a status message
    when the curves can't be compared."""
    meta: dict = {"overlap": False, "message": ""}
    if len(gpu_front) < 2:
        meta["message"] = ("GPU frontier has fewer than 2 points — cannot "
                           "interpolate a curve.")
        return [], meta
    if not trn_front:
        meta["message"] = "Trainium frontier is empty."
        return [], meta

    gpu_x = np.array([r["_x"] for r in gpu_front], dtype=float)
    gpu_y = np.array([r["_tput"] for r in gpu_front], dtype=float)
    gpu_itps = np.array([float(r.get("itps") or 0.0) for r in gpu_front], dtype=float)
    gx_lo, gx_hi = float(gpu_x.min()), float(gpu_x.max())

    trn_x = np.array([r["_x"] for r in trn_front], dtype=float)
    tx_lo, tx_hi = float(trn_x.min()), float(trn_x.max())
    if tx_hi < gx_lo or tx_lo > gx_hi:
        meta["message"] = (
            f"No interactivity overlap: Trainium spans [{tx_lo:.2f}, {tx_hi:.2f}] "
            f"tok/s/user, GPU spans [{gx_lo:.2f}, {gx_hi:.2f}] — cannot compare "
            "at equal interactivity.")
        meta["trn_range"] = (tx_lo, tx_hi)
        meta["gpu_range"] = (gx_lo, gx_hi)
        return [], meta

    rows = []
    for p in trn_front:
        x = p["_x"]
        in_range = gx_lo <= x <= gx_hi
        if in_range:
            gpu_tput = float(np.interp(x, gpu_x, gpu_y))
            gpu_in = float(np.interp(x, gpu_x, gpu_itps))
            pct = 100.0 * p["_tput"] / gpu_tput if gpu_tput > 0 else float("nan")
            bar = bar_for_pct(pct) if gpu_tput > 0 else "n/a"
        else:
            gpu_tput = float("nan")
            gpu_in = float("nan")
            pct = float("nan")
            bar = "out of GPU range"
        rows.append({
            "interactivity": x,
            "tput_trn": p["_tput"],
            "tput_gpu_interp": gpu_tput,
            "gpu_itps_interp": gpu_in,
            "trn_itps": float(p.get("itps") or 0.0),
            "milestone_pct": pct,
            "bar": bar,
            "in_range": in_range,
            "conc": p.get("conc"),
            "sharding": p.get("sharding"),
        })
    overlap_lo = max(tx_lo, gx_lo)
    overlap_hi = min(tx_hi, gx_hi)
    meta.update({"overlap": True, "gpu_x_range": (gx_lo, gx_hi),
                 "trn_x_range": (tx_lo, tx_hi),
                 "overlap_range": (overlap_lo, overlap_hi),
                 "n_in_range": sum(1 for r in rows if r["in_range"])})
    if meta["n_in_range"] == 0:
        meta["message"] = ("Trainium frontier points all fall outside the GPU "
                           "curve's interactivity range — no equal-interactivity "
                           "comparison possible.")
    return rows, meta


def representative(rows: list[dict], at_x: float | None) -> dict | None:
    """Pick a representative in-range point: the one nearest --at-interactivity if
    given, else the median-interactivity in-range point."""
    inr = [r for r in rows if r["in_range"] and not np.isnan(r["milestone_pct"])]
    if not inr:
        return None
    if at_x is not None:
        return min(inr, key=lambda r: abs(r["interactivity"] - at_x))
    xs = sorted(inr, key=lambda r: r["interactivity"])
    return xs[len(xs) // 2]


# --- outputs ----------------------------------------------------------------

def _fmt(v, n=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:.{n}f}"


def write_md(out: Path, shape: str, baseline: str, model: str,
             trn_inst: str, gpu_inst: str, rows: list[dict], meta: dict,
             rep: dict | None, at_x: float | None, cost_note: str) -> None:
    shp_tag = shape.replace("/", "_")
    lines = [
        f"# {model} — Trainium throughput at equal interactivity vs GPU",
        f"## Workload {shape} in/out tokens · baseline `{baseline}`",
        "",
        f"- **Comparison:** single-instance / single-box — NO disaggregated "
        f"inference (per the Performance Milestones doc, Q1). Trainium = "
        f"`{trn_inst}`, GPU = `{gpu_inst}`.",
        f"- **Serving-optimization baseline:** `{baseline}` (applied to BOTH "
        f"platforms; the comparison is only valid feature-matched).",
        f"- **Shape:** {shape} (input/output tokens per request).",
        "- **Definition:** each platform's Pareto frontier of throughput "
        "(tok/s/server, Y) vs interactivity (tok/s/user, X); the two are compared "
        "at EQUAL interactivity by linear interpolation of the GPU frontier. "
        "milestone_pct = 100 × tput_trn(X) / tput_gpu_interp(X). No extrapolation.",
        "- **Cost basis:** public list price, whole instance "
        "(build_cost_deliverables.PRICE_PER_HR).",
        "- **Milestone bars (Table 3):** ≥130% High (leadership) · ≥100% Mid "
        "(parity) · ≥60% Low (cost-parity-without-PPAs) · <60% below low bar.",
        "",
    ]
    if not meta.get("overlap") or meta.get("n_in_range", 0) == 0:
        lines.append(f"> **Cannot compare at equal interactivity:** "
                     f"{meta.get('message', 'no overlap')}")
        if rows:
            lines.append("")
            lines.append("Trainium frontier points (all out of GPU range):")
            lines.append("")
            df = pd.DataFrame([{
                "interactivity (tok/s/user)": _fmt(r["interactivity"], 2),
                "Trn tput (tok/s/server)": _fmt(r["tput_trn"], 1),
                "GPU tput (interp)": "out of range",
                "milestone %": "—",
                "bar": r["bar"],
            } for r in rows])
            lines.append(df.to_markdown(index=False))
        (out / f"milestone_{shp_tag}_{baseline}.md").write_text("\n".join(lines) + "\n")
        return

    in_pcts = [r["milestone_pct"] for r in rows
               if r["in_range"] and not np.isnan(r["milestone_pct"])]
    lo, hi = meta["overlap_range"]
    summary = (f"**Overlap interactivity range:** [{lo:.2f}, {hi:.2f}] tok/s/user "
               f"({meta['n_in_range']} Trainium frontier points in range).")
    lines.append(summary)
    lines.append("")
    if in_pcts:
        lines.append(f"- milestone_pct across overlap — min **{min(in_pcts):.1f}%**, "
                     f"median **{float(np.median(in_pcts)):.1f}%**, "
                     f"max **{max(in_pcts):.1f}%**")
    if rep is not None:
        at_desc = (f"nearest to requested {at_x:g} tok/s/user"
                   if at_x is not None else "median in-range interactivity")
        lines.append(
            f"- **Representative point** ({at_desc}): at interactivity "
            f"**{rep['interactivity']:.2f} tok/s/user**, Trainium "
            f"**{rep['tput_trn']:.0f}** vs GPU-interp **{rep['tput_gpu_interp']:.0f}** "
            f"tok/s/server ⇒ **{rep['milestone_pct']:.1f}%** — bar: **{rep['bar']}**")
    if cost_note:
        lines.append(f"- {cost_note}")
    lines.append("")

    tbl = pd.DataFrame([{
        "interactivity (tok/s/user)": _fmt(r["interactivity"], 2),
        "Trn tput (tok/s/server)": _fmt(r["tput_trn"], 1),
        "GPU tput interp (tok/s/server)": (_fmt(r["tput_gpu_interp"], 1)
                                           if r["in_range"] else "out of range"),
        "milestone %": (_fmt(r["milestone_pct"], 1) if r["in_range"] else "—"),
        "bar": r["bar"],
        "conc": r.get("conc"),
        "sharding": r.get("sharding"),
    } for r in rows])
    lines.append(tbl.to_markdown(index=False))
    lines.append("")
    lines.append("_Rows marked 'out of GPU range' are Trainium frontier points "
                 "beyond the GPU curve's interactivity span; excluded from the % "
                 "summary (no extrapolation), listed for completeness._")
    (out / f"milestone_{shp_tag}_{baseline}.md").write_text("\n".join(lines) + "\n")


def write_plot(out: Path, shape: str, baseline: str, model: str,
               trn_inst: str, gpu_inst: str,
               trn_front: list[dict], gpu_front: list[dict],
               rows: list[dict], meta: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    shp_tag = shape.replace("/", "_")
    fig, ax = plt.subplots(figsize=(9, 6.5))

    if gpu_front:
        gx = [r["_x"] for r in gpu_front]
        gy = [r["_tput"] for r in gpu_front]
        ax.plot(gx, gy, marker="s", color=COLOR_GPU, linewidth=2.2, markersize=6,
                label=f"GPU ({gpu_inst}) frontier")
    if trn_front:
        tx = [r["_x"] for r in trn_front]
        ty = [r["_tput"] for r in trn_front]
        ax.plot(tx, ty, marker="o", color=COLOR_TRN, linewidth=2.2, markersize=6,
                label=f"Trainium ({trn_inst}) frontier")

    # Mark each in-range Trn point and draw a drop line to the GPU-interp value,
    # annotate with the milestone %.
    drew_interp = False
    for r in rows:
        if not r["in_range"] or np.isnan(r["milestone_pct"]):
            continue
        x = r["interactivity"]
        ax.plot([x, x], [r["tput_gpu_interp"], r["tput_trn"]], color=COLOR_DROP,
                linewidth=1.0, linestyle="--", alpha=0.7, zorder=1)
        ax.scatter([x], [r["tput_gpu_interp"]], color=COLOR_INTERP, s=42, zorder=3,
                   marker="v", label=None if drew_interp else "GPU throughput @ Trn interactivity (interp)")
        drew_interp = True
        ax.annotate(f"{r['milestone_pct']:.0f}%",
                    (x, max(r["tput_trn"], r["tput_gpu_interp"])),
                    fontsize=7, color="#0b0b0b", xytext=(3, 4),
                    textcoords="offset points")

    ax.set_xlabel("Interactivity — per-user output rate (output tok/s per user)")
    ax.set_ylabel("Throughput — per-server output rate (output tok/s, whole box)")
    subtitle = ("Pareto frontiers overlaid; % = Trainium throughput ÷ GPU "
                "throughput at equal interactivity")
    ax.set_title(f"{model} — {shape} in/out tokens · baseline {baseline}\n{subtitle}")
    ax.legend(title=None, fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.margins(x=0.05, y=0.08)
    fig.savefig(out / f"milestone_{shp_tag}_{baseline}.png", dpi=200,
                bbox_inches="tight")
    plt.close(fig)


def cost_annotation(rep: dict | None, trn_inst: str, gpu_inst: str,
                    prices: dict, labels: dict) -> str:
    """tokens/$ (total) at the representative compared point for both platforms,
    at public list price (whole box). Skips gracefully if either is unpriced."""
    if rep is None:
        return ""
    missing = [i for i in (trn_inst, gpu_inst) if i not in prices]
    if missing:
        print(f"  (cost skipped: no PRICE_PER_HR entry for {missing} — add a rate "
              f"to build_cost_deliverables.PRICE_PER_HR to include cost.)")
        return ""
    trn_total_tps = rep["tput_trn"] + (rep["trn_itps"] or 0.0)
    gpu_total_tps = rep["tput_gpu_interp"] + (rep["gpu_itps_interp"] or 0.0)
    trn_price = prices[trn_inst]
    gpu_price = prices[gpu_inst]
    if trn_price <= 0 or gpu_price <= 0:
        print(f"  (cost skipped: non-positive price for {trn_inst}/{gpu_inst})")
        return ""
    trn_tpd = trn_total_tps * 3600 / trn_price
    gpu_tpd = gpu_total_tps * 3600 / gpu_price
    ratio = 100.0 * trn_tpd / gpu_tpd if gpu_tpd > 0 else float("nan")
    return (f"**Cost @ representative point** (public list, whole box): "
            f"Trainium `{trn_inst}` ({labels.get(trn_inst, trn_inst)}, "
            f"${trn_price:.2f}/hr) = {trn_tpd/1e3:.0f}K tokens/$ (total) vs GPU "
            f"`{gpu_inst}` ({labels.get(gpu_inst, gpu_inst)}, ${gpu_price:.2f}/hr) "
            f"= {gpu_tpd/1e3:.0f}K tokens/$ — Trainium is {ratio:.0f}% of GPU "
            f"tokens/$. Per the doc, ≈60% of GPU throughput at equal interactivity "
            f"already reaches $/token cost parity before any price/perf adjustments "
            f"(PPAs).")


# --- main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trn", type=Path, required=True,
                    help="Trainium all_cells.csv")
    ap.add_argument("--trn-instance", default="trn2",
                    help="Trainium instance label; selects rows by the `instance` "
                         "column if present (default trn2).")
    ap.add_argument("--gpu", type=Path, required=True, help="GPU all_cells.csv")
    ap.add_argument("--gpu-instance", default="p5",
                    help="GPU instance label; selects rows by the `instance` "
                         "column if present (default p5).")
    ap.add_argument("--baseline", default="controlled",
                    choices=["controlled", "production"],
                    help="serving-optimization baseline (Performance Milestones "
                         "doc). Applied to BOTH platforms — the csv carries no "
                         "baseline column, so this is authoritative.")
    ap.add_argument("--model-name", default="Model",
                    help="model label for titles.")
    ap.add_argument("--at-interactivity", type=float, default=None,
                    help="representative interactivity (tok/s/user) to headline; "
                         "default = median in-range point.")
    ap.add_argument("--out", type=Path, default=Path("./deliverables/milestone"))
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    print("Loading platforms:")
    trn = load_platform(args.trn, args.trn_instance, "Trainium")
    gpu = load_platform(args.gpu, args.gpu_instance, "GPU")

    prices, labels = _load_price_table()

    shapes = sorted(set(trn["shape"]).union(set(gpu["shape"])))
    common = sorted(set(trn["shape"]).intersection(set(gpu["shape"])))
    only_trn = sorted(set(trn["shape"]) - set(gpu["shape"]))
    only_gpu = sorted(set(gpu["shape"]) - set(trn["shape"]))
    if only_trn:
        print(f"  (shapes only on Trainium, skipped: {only_trn})")
    if only_gpu:
        print(f"  (shapes only on GPU, skipped: {only_gpu})")
    if not common:
        raise SystemExit("No shape appears on BOTH platforms — nothing to compare. "
                         f"Trn shapes {sorted(set(trn['shape']))}, "
                         f"GPU shapes {sorted(set(gpu['shape']))}.")

    summary_rows = []
    print("Per-shape milestone comparison:")
    for shape in common:
        trn_front = _frontier_for_shape(trn, shape)
        gpu_front = _frontier_for_shape(gpu, shape)
        rows, meta = compare_shape(trn_front, gpu_front)
        rep = representative(rows, args.at_interactivity)
        cost_note = cost_annotation(rep, args.trn_instance, args.gpu_instance,
                                    prices, labels)

        write_md(args.out, shape, args.baseline, args.model_name,
                 args.trn_instance, args.gpu_instance, rows, meta, rep,
                 args.at_interactivity, cost_note)
        if not args.no_plots:
            write_plot(args.out, shape, args.baseline, args.model_name,
                       args.trn_instance, args.gpu_instance, trn_front, gpu_front,
                       rows, meta)

        for r in rows:
            summary_rows.append({
                "model": args.model_name,
                "shape": shape,
                "baseline": args.baseline,
                "instance_trn": args.trn_instance,
                "instance_gpu": args.gpu_instance,
                "interactivity": round(r["interactivity"], 4),
                "tput_trn": round(r["tput_trn"], 2),
                "tput_gpu_interp": (round(r["tput_gpu_interp"], 2)
                                    if r["in_range"] else None),
                "milestone_pct": (round(r["milestone_pct"], 2)
                                  if r["in_range"] and not np.isnan(r["milestone_pct"])
                                  else None),
                "bar": r["bar"],
            })

        if meta.get("overlap") and meta.get("n_in_range", 0) > 0 and rep is not None:
            print(f"  {shape}: representative {rep['milestone_pct']:.1f}% "
                  f"({rep['bar']}) at {rep['interactivity']:.2f} tok/s/user "
                  f"[{meta['n_in_range']} pts in overlap]")
        else:
            print(f"  {shape}: {meta.get('message', 'no comparison')}")

    if summary_rows:
        pd.DataFrame(summary_rows).to_csv(args.out / "milestone_summary.csv",
                                          index=False)
        print(f"milestone_summary.csv: {len(summary_rows)} rows")
    else:
        print("milestone_summary.csv: no rows (no comparable points)")

    print(f"\nDone → {args.out}/")


if __name__ == "__main__":
    main()
