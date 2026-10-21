#!/usr/bin/env python3
"""Cost deliverables — tokens per dollar at on-demand list price.

Reads the tidy `all_cells.csv` produced by build_deliverables.py (no re-sync needed)
and emits per-instance-hour cost artifacts.

**Primary metric is tokens/$ (higher = more throughput per dollar).** The columns
`tok_per_$_total` and `tok_per_$_out` are the number of tokens (input+output or
output-only) the box produces for one dollar of on-demand rent. Every table also
carries `$/hr` (instance on-demand rate) so readers can back-of-envelope alternate
scenarios without re-deriving.

USAGE (after build_deliverables.py has written all_cells.csv):
    python build_cost_deliverables.py <deliverables>/all_cells.csv --out <deliverables>/cost

  build_deliverables.py must have been run with an `--instance` whose rate is in
  PRICE_PER_HR below (p5/p5en/b200/b300/g6e.12xlarge/g6e.48xlarge). An UNPRICED
  instance is DROPPED with a warning (not a hard error) — a priced instance in the
  same run still produces output; add the missing rate to PRICE_PER_HR to include it.
  To cost a Neuron/trn2 run, add e.g. "trn2": <trn2.48xlarge $/hr>. Note: cross-instance
  BAR/PARETO plots only render when >=2 priced instances share a regime×shape, and a
  GPU-vs-Neuron chart is only valid if both ran the SAME dataset (use random, not
  sharegpt — see SKILL.md). Pass --model-name to label titles (else derived from run_id).

  PRICES BELOW ARE A SNAPSHOT (us-east-1, Linux, confirmed 2026-07-02). They drift —
  re-verify against current AWS on-demand pricing before quoting any $ number.

Emits:

  1. cost_cells.csv              — every usable cell + price, total-tok/s, tokens/$ (total & output)
  2. cost_summary.md / .csv      — max achievable tokens/$ per (instance, regime, shape)
  3. costbar_<regime>_<shape>.png — cross-instance tokens/$ bars (only multi-instance regime×shape)
  4. costpareto_<regime>_<shape>.png — cost/latency frontier: per-user rate (x) vs tokens/$ (y)

WHY $/instance-hour (not $/chip-hour like the H200 Quip doc):
  Every sharding we ran (tp8 / tp4_dp2 / tp2_dp4 / tp1_dp8) sums to all 8 GPUs, so
  every run occupies the WHOLE instance regardless of sharding. There is no
  fractional-chip / bin-packing assumption to defend (unlike the Quip §5.3 model);
  $/instance-hour is the natural, honest denominator and sidesteps DP-normalization.

METHOD:
  tokens/$ (total) = total_tok/s * 3600 / instance_$_per_hr
  total_tok/s = otps + itps  (output + input; matches the Quip doc's total-token basis)
  Within one instance type price is constant, so tokens/$ is just a monotone rescale
  of throughput — the NEW signal is entirely CROSS-INSTANCE (prices differ).

PRICES: AWS on-demand list, us-east-1, Linux (confirmed 2026-07-02 via instances.vantage.sh):
  p5.48xlarge   (8x H100) $55.040/hr
  p5en.48xlarge (8x H200) $63.296/hr
  p6-b200.48xlarge (8x B200) $113.933/hr
  p6-b300.48xlarge (8x B300) $142.416/hr

CAVEATS carried through (see build_deliverables.py):
  - B300 fp8_spec throughput is a LOWER BOUND (sm_100 kernels fwd-compat on sm_103),
    so B300 $/1M is an UPPER BOUND — its real cost-efficiency is at least this good.
  - spec regimes use a different vLLM engine than no-spec; compare within-regime only.
  - b200 (bf16_nospec) and b300 (fp8_spec) ran DIFFERENT regimes — never on one chart.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

# On-demand list, us-east-1, Linux. $/instance-hour (whole box). Keys match the
# --instance label passed to build_deliverables.py. DATED SNAPSHOT (2026-07) — drift;
# re-verify before quoting. Add a rate here for any instance you benchmark.
PRICE_PER_HR = {
    "p5": 55.040,              # 8x H100
    "p5en": 63.296,            # 8x H200
    "b200": 113.933,           # 8x B200 (p6-b200.48xlarge)
    "b300": 142.416,           # 8x B300 (p6-b300.48xlarge)
    "g6e.12xlarge": 10.493,    # 4x L40S
    "g6e.48xlarge": 30.131,    # 8x L40S
    # trn2.48xlarge (16x Trainium2). NOTE: this is the CAPACITY-BLOCK public rate
    # ($35.7608/instance-hr, us-east-2, confirmed 2026-07-29 via aws.amazon.com/ec2/
    # capacityblocks/pricing) — the plain on-demand list rate was not reliably
    # published at snapshot time (instances.vantage.sh returned an obviously-corrupt
    # $0.125). The milestone doc's cost basis is "public list price"; if/when a plain
    # on-demand trn2 rate is published, REPLACE this and drop the capacity-block note.
    # Milestone % (throughput-at-equal-interactivity) is price-INDEPENDENT and unaffected.
    "trn2": 35.7608,           # 16x Trainium2 (trn2.48xlarge) — capacity-block list, see note
    "trn2.48xlarge": 35.7608,  # alias: Neuron runs record the full instance-type string
    "p5.48xlarge": 55.040,     # alias: GPU runs record the full instance-type string (8x H100)
    "p5en.48xlarge": 63.296,   # alias (8x H200)
    # p5e.48xlarge (8x H200 141GB). SAME CAVEAT AS trn2, and stronger: the AWS public
    # price list has NO on-demand rate for p5e in any commercial region (the pricing API
    # returns a single us-east-2 record at $0.0000, confirmed 2026-08-09) — p5e is
    # effectively capacity-block-only. The rate below is the REAL price paid for a p5e
    # block: $714.01 for the 2026-08-09T20:37Z->2026-08-10T11:30Z window (14.883 h) =
    # $47.974/instance-hour. Defensible, but a CAPACITY-BLOCK rate — not on-demand list,
    # so it is not apples-to-apples with the p5/p5en on-demand rates above (blocks are
    # prepaid/non-refundable and cheaper per hour). It IS apples-to-apples with the trn2
    # rate above, which is also a capacity-block rate — so a p5e-vs-trn2 tokens/$ chart
    # is block-vs-block and internally consistent. For a strict on-demand 8x H200
    # denominator, use p5en ($63.296) and say which basis you used.
    "p5e": 47.974,
    "p5e.48xlarge": 47.974,    # alias
}
GPU_LABEL = {"p5": "H100", "p5en": "H200", "b200": "B200", "b300": "B300",
             "g6e.12xlarge": "L40S", "g6e.48xlarge": "L40S",
             "trn2": "Trn2", "trn2.48xlarge": "Trn2",
             "p5e": "H200", "p5e.48xlarge": "H200",
             "p5.48xlarge": "H100", "p5en.48xlarge": "H200"}

# Model label for plot/table titles (set from --model-name or derived from run_id).
MODEL_NAME = "Model"

COST_CAVEATS = {
    "b300": ("B300 tokens/$ is a LOWER BOUND: fp8 kernels are sm_100 (B200-targeted) "
             "running forward-compat on sm_103, so B300 throughput is a lower bound "
             "and its true cost-efficiency is at least this high."),
}


def load(csv: Path) -> pd.DataFrame:
    df = pd.read_csv(csv)
    df = df[df["usable"]].copy()
    df["price_hr"] = df["instance"].map(PRICE_PER_HR)
    if df["price_hr"].isna().any():
        missing = sorted(df.loc[df["price_hr"].isna(), "instance"].unique())
        # Degrade gracefully: DROP unpriced instances with a loud warning rather than
        # hard-erroring. A priced instance in the same run still produces cost output;
        # add the missing rate to PRICE_PER_HR (above) to include it.
        print(f"WARNING: no price for instance(s) {missing} — dropping them from cost "
              f"deliverables. Add their on-demand rate to PRICE_PER_HR to include them.")
        df = df[df["price_hr"].notna()].copy()
        if df.empty:
            raise SystemExit(
                f"No priced instances in the data (saw only {missing}). Add a rate "
                "to PRICE_PER_HR in build_cost_deliverables.py, then re-run.")
    df["total_tps"] = df["otps"] + df["itps"]           # output + input tok/s (box-level)
    # tokens per dollar at on-demand list, whole instance. Higher = more throughput per $.
    df["tok_per_dollar_total"] = df["total_tps"] * 3600 / df["price_hr"]
    df["tok_per_dollar_out"] = df["otps"] * 3600 / df["price_hr"]
    return df


def write_cost_cells(df: pd.DataFrame, out: Path):
    cols = ["instance", "regime_key", "regime", "shape", "sharding", "conc",
            "price_hr", "otps", "itps", "total_tps",
            "tok_per_dollar_total", "tok_per_dollar_out",
            "tpot_p50", "per_user_otps", "ttft_p50"]
    # Sort by tokens/$ DESC within each group (best-first).
    t = df[cols].copy().sort_values(
        ["regime_key", "shape", "instance", "tok_per_dollar_total"],
        ascending=[True, True, True, False],
    )
    for c in ("tok_per_dollar_total", "tok_per_dollar_out"):
        t[c] = t[c].round(0).astype(int)  # tokens are integers; drop decimals
    t["total_tps"] = t["total_tps"].round(0).astype(int)
    t["price_hr"] = t["price_hr"].round(3)
    t.to_csv(out / "cost_cells.csv", index=False)
    print(f"  cost_cells.csv ({len(t)} usable cells)")


def best_cost_table(df: pd.DataFrame) -> pd.DataFrame:
    """Max achievable tokens/$ (total) per (instance, regime, shape) + the winning cell,
    plus a flag for whether that max sits at the highest swept concurrency (=> the
    true max may lie beyond our sweep, so this is a lower bound on efficiency)."""
    rows = []
    for (inst, rk, shp), g in df.groupby(["instance", "regime_key", "shape"]):
        b = g.loc[g["tok_per_dollar_total"].idxmax()]
        max_conc = g["conc"].max()
        rows.append({
            "instance": inst, "regime_key": rk, "shape": shp,
            "price_hr": round(b["price_hr"], 3),
            "max_tok_per_dollar_total": int(round(b["tok_per_dollar_total"])),
            "tok_per_dollar_out_at_max": int(round(b["tok_per_dollar_out"])),
            "at_sharding": b["sharding"], "at_conc": int(b["conc"]),
            "total_tps": int(round(b["total_tps"])),
            "tpot_p50_ms": b["tpot_p50"],
            "max_at_max_swept_conc": bool(b["conc"] == max_conc),
        })
    return pd.DataFrame(rows).sort_values(
        ["regime_key", "shape", "max_tok_per_dollar_total"],
        ascending=[True, True, False],
    )


def write_summary(mc: pd.DataFrame, out: Path):
    mc.to_csv(out / "cost_summary.csv", index=False)
    lines = [f"# {MODEL_NAME} — maximum tokens/$ by instance (on-demand list, whole box)",
             "",
             "**tokens/$** = tokens produced per one dollar of on-demand rent, at the "
             "throughput-optimal cell (higher = more efficient). Uses **total tokens** "
             "(input+output). Formula: `total_tok/s × 3600 / $/hr`.",
             "",
             "`$/hr` is the whole-instance on-demand list price (us-east-1, Linux; "
             "snapshot from build_cost_deliverables.py PRICE_PER_HR — re-verify before quoting).",
             "",
             "`max_at_max_swept_conc=True` ⇒ the most-efficient cell is at the top of the "
             "concurrency sweep, so the true maximum may be higher (this is a lower bound "
             "on the best achievable efficiency).", ""]
    for rk, g in mc.groupby("regime_key"):
        lines.append(f"## {rk}")
        show = g.drop(columns=["regime_key"]).rename(columns={
            "price_hr": "$/hr",
            "max_tok_per_dollar_total": "tokens/$ (total)",
            "tok_per_dollar_out_at_max": "tokens/$ (output)",
            "total_tps": "total tok/s", "tpot_p50_ms": "TPOT p50 (ms)",
            "max_at_max_swept_conc": "max@max-conc?"})
        lines.append(show.to_markdown(index=False))
        insts = set(g["instance"])
        for inst in sorted(insts):
            if inst in COST_CAVEATS:
                lines.append(f"\n> **{inst}:** {COST_CAVEATS[inst]}")
        lines.append("")
    (out / "cost_summary.md").write_text("\n".join(lines) + "\n")
    print(f"  cost_summary.md / .csv ({len(mc)} instance×regime×shape rows)")


def write_cost_bars(mc: pd.DataFrame, out: Path):
    """Cross-instance tokens/$ bars, per regime×shape, only where >=2 instances ran it."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = 0
    for (rk, shp), g in mc.groupby(["regime_key", "shape"]):
        if g["instance"].nunique() < 2:
            continue
        g = g.sort_values("instance")
        insts = list(g["instance"])
        vals = list(g["max_tok_per_dollar_total"])
        prices = list(g["price_hr"])
        # Two-line labels: instance + GPU + hourly rate — the price context readers want
        labels = [f"{i}\n({GPU_LABEL[i]}, ${p:.2f}/hr)" for i, p in zip(insts, prices)]
        fig, ax = plt.subplots(figsize=(1.6 * len(insts) + 2, 5))
        bars = ax.bar(labels, vals, color=["#4C72B0", "#55A868", "#C44E52", "#8172B3"][:len(insts)])
        for b, v, inst in zip(bars, vals, insts):
            # Compact tokens/$ label (e.g. 1.97M for 1,970,000).
            tag = f"{v/1e6:.2f}M" if v >= 1e6 else f"{v/1e3:.0f}K"
            if inst == "b300":
                tag += "\n(lower bound)"
            ax.annotate(tag, (b.get_x() + b.get_width() / 2, v), ha="center",
                        va="bottom", fontsize=8)
        ax.set_ylabel("Tokens per dollar (input+output; box-level, on-demand list)")
        ax.set_title(f"{MODEL_NAME} ({rk}) — {shp} in/out tokens\n"
                     "Best achievable tokens/$ per box (on-demand list); higher = better")
        ax.grid(True, axis="y", alpha=0.3)
        ax.margins(y=0.15)
        tag = f"{rk}_{shp.replace('/', '_')}"
        fig.savefig(out / f"costbar_{tag}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        made += 1
        print(f"  costbar_{tag}.png ({len(insts)} instances)")
    if not made:
        print("  (no multi-instance regime×shape combos for cost bars)")


def _efficiency_frontier(rows: list[dict]) -> list[dict]:
    """Upper envelope on (per_user_otps ↑ better, tok_per_dollar_total ↑ better).
    Keep a cell only if no other cell is at least as fast AND at least as efficient.
    This is the achievable efficiency/latency frontier — the TOP-right hull —
    NOT the raw per-conc scatter (whose idle-box low-conc points collapse efficiency)."""
    keep = []
    for p in rows:
        dominated = any(
            q is not p
            and q["per_user_otps"] >= p["per_user_otps"]
            and q["tok_per_dollar_total"] >= p["tok_per_dollar_total"]
            and (q["per_user_otps"] > p["per_user_otps"]
                 or q["tok_per_dollar_total"] > p["tok_per_dollar_total"])
            for q in rows
        )
        if not dominated:
            keep.append(p)
    return sorted(keep, key=lambda r: r["per_user_otps"])


def write_cost_pareto(df: pd.DataFrame, out: Path):
    """Efficiency/latency frontier: x = per-user output rate (higher=faster per request),
    y = tokens/$ total (higher=more efficient). One ENVELOPE curve per instance (the
    achievable top-right frontier over all shardings×concs, not the raw scatter).
    Desirable corner is TOP-right (fast AND efficient). Only regime×shape with >=2
    instances. Faint dots show the underlying cells for context."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    made = 0
    for (rk, shp), g in df.groupby(["regime_key", "shape"]):
        if g["instance"].nunique() < 2:
            continue
        fig, ax = plt.subplots(figsize=(8, 6))
        # Solid, high-contrast color per instance; line at full opacity, scatter faint.
        inst_colors = {"p5": "#1f6feb", "p5en": "#d62728", "b200": "#2ca02c",
                       "b300": "#9467bd"}
        for inst, s in g.groupby("instance"):
            color = inst_colors.get(inst, "#333333")
            # faint scatter of all cells for context (light, so it doesn't fight the curve)
            ax.scatter(s["per_user_otps"], s["tok_per_dollar_total"], s=10,
                       color=color, alpha=0.15, label="_nolegend_")
            front = _efficiency_frontier(s.to_dict("records"))
            xs = [r["per_user_otps"] for r in front]
            ys = [r["tok_per_dollar_total"] for r in front]
            # solid, full-opacity envelope line so it's clearly visible
            ax.plot(xs, ys, marker="o", color=color, linewidth=2.2, markersize=6,
                    label=f"{inst} ({GPU_LABEL[inst]}, ${s['price_hr'].iloc[0]:.2f}/hr)")
            for r in front:  # label envelope vertices with concurrency
                ax.annotate(f"c={int(r['conc'])}",
                            (r["per_user_otps"], r["tok_per_dollar_total"]),
                            fontsize=6, alpha=0.75, xytext=(3, 3), textcoords="offset points")
        ax.set_xlabel("Per-user output rate (output tok/s per request) — higher = faster")
        ax.set_ylabel("Tokens per dollar (total; box-level, on-demand list) — higher = more efficient")
        # y starts at 0 (never negative). Max frontier drives the top; scatter can dip
        # below 0 but is clipped by the axis floor.
        env_ys = []
        for inst, s in g.groupby("instance"):
            env_ys += [r["tok_per_dollar_total"] for r in
                       _efficiency_frontier(s.to_dict("records"))]
        if env_ys:
            ax.set_ylim(0, max(env_ys) * 1.15)
        note = "\n(B300 curve is a lower bound on efficiency)" if rk == "fp8_spec" and \
               "b300" in set(g["instance"]) else ""
        ax.set_title(f"{MODEL_NAME} ({rk}) — {shp} in/out tokens\n"
                     f"Efficiency/latency frontier (envelope over shardings×conc); "
                     f"top-right = fast & efficient{note}")
        ax.legend(title="instance")
        ax.grid(True, alpha=0.3)
        tag = f"{rk}_{shp.replace('/', '_')}"
        fig.savefig(out / f"costpareto_{tag}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        made += 1
        print(f"  costpareto_{tag}.png")
    if not made:
        print("  (no multi-instance regime×shape combos for cost Pareto)")


def write_cost_throughput(df: pd.DataFrame, out: Path):
    """Per-BOX view: x = per-box output throughput (output tok/s, whole instance),
    y = tokens/$ total. For a fixed shape+instance, tok/$ = total_tps × 3600 / $/hr —
    a straight line through the origin whose SLOPE is 3600/$/hr (cheaper box = steeper).
    What it reveals that the bars don't:
      - slope ranking = value-per-throughput at any point on the x-axis
      - each box's REACH — the rightmost point is its max sustained throughput, which
        is also its max tok/$ (== the value in the cost bars).
    Only regime×shape with >=2 instances."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    inst_colors = {"p5": "#1f6feb", "p5en": "#d62728", "b200": "#2ca02c",
                   "b300": "#9467bd"}
    made = 0
    for (rk, shp), g in df.groupby(["regime_key", "shape"]):
        if g["instance"].nunique() < 2:
            continue
        fig, ax = plt.subplots(figsize=(8, 6))
        max_effs = []
        for inst, s in g.groupby("instance"):
            s = s.sort_values("otps")
            color = inst_colors.get(inst, "#333333")
            price = s["price_hr"].iloc[0]
            ax.plot(s["otps"], s["tok_per_dollar_total"], marker="o", color=color,
                    linewidth=2.2, markersize=5,
                    label=f"{inst} ({GPU_LABEL[inst]}, ${price:.2f}/hr)")
            # mark + label the max-throughput (== max-efficiency) endpoint per box
            b = s.loc[s["otps"].idxmax()]
            max_effs.append(b["tok_per_dollar_total"])
            v = b["tok_per_dollar_total"]
            v_tag = f"{v/1e6:.2f}M" if v >= 1e6 else f"{v/1e3:.0f}K"
            ax.annotate(f"c={int(b['conc'])}  {v_tag} tok/$",
                        (b["otps"], b["tok_per_dollar_total"]),
                        fontsize=7, color=color,
                        xytext=(4, -2), textcoords="offset points",
                        fontweight="bold")
        if max_effs:
            ax.set_ylim(0, max(max_effs) * 1.15)
        ax.set_xlabel("Per-box output throughput (output tok/s, whole instance) "
                      "— higher = more capacity")
        ax.set_ylabel("Tokens per dollar (total; on-demand list) — higher = more efficient")
        note = "\n(B300 is a lower bound — throughput is a lower bound)" \
            if rk == "fp8_spec" and "b300" in set(g["instance"]) else ""
        ax.set_title(f"{MODEL_NAME} ({rk}) — {shp} in/out tokens\n"
                     f"tokens/$ vs per-box throughput; each box ends at its max reach "
                     f"(= its most efficient cell){note}")
        ax.legend(title="instance")
        ax.grid(True, alpha=0.3)
        tag = f"{rk}_{shp.replace('/', '_')}"
        fig.savefig(out / f"costtput_{tag}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        made += 1
        print(f"  costtput_{tag}.png")
    if not made:
        print("  (no multi-instance regime×shape combos for cost-vs-throughput)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cells_csv", type=Path, help="path to all_cells.csv")
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir (default: alongside cells_csv in ./cost)")
    ap.add_argument("--model-name", default=None,
                    help="model label for titles (e.g. 'Qwen3-235B-A22B'); default "
                         "derives from the run_id, else 'Model'.")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    out = args.out or (args.cells_csv.parent / "cost")
    out.mkdir(parents=True, exist_ok=True)

    df = load(args.cells_csv)

    global MODEL_NAME
    if args.model_name:
        MODEL_NAME = args.model_name
    elif "run_id" in df.columns:
        keys = {str(r).split("__", 1)[0] for r in df["run_id"]}
        MODEL_NAME = keys.pop() if len(keys) == 1 else (
            "/".join(sorted(keys)) if keys else "Model")
    print(f"Loaded {len(df)} usable cells; instances {sorted(df['instance'].unique())}")
    print("Cost cells:")
    write_cost_cells(df, out)
    mc = best_cost_table(df)
    print("Summary:")
    write_summary(mc, out)
    if not args.no_plots:
        print("Cost bars (cross-instance):")
        write_cost_bars(mc, out)
        print("Cost/latency Pareto (x = per-user rate):")
        write_cost_pareto(df, out)
        print("Cost vs per-box throughput (x = per-box tok/s):")
        write_cost_throughput(df, out)
    print(f"\nDone → {out}/")


if __name__ == "__main__":
    main()
