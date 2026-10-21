#!/usr/bin/env python3
"""Parse InferencePerfAnalyzer GPU sweep results into deliverables.

Walks one or more local results trees (after `aws s3 sync`), parses each cell's
result.json + its path-encoded dimensions, and emits:
  1. all_cells.csv             — the tidy table (1 row per cell), the audit trail
  2. table_<inst>_<regime>_<shape>.csv / .md  — per-(instance,regime,shape) metric tables
  3. pareto_<inst>_<regime>_<shape>.png        — per-run Pareto (one curve per sharding)
  4. overlay_<shape>_by-regime_<inst>.png       — regimes overlaid on one instance (multi)
  5. overlay_<shape>_by-instance_<regime>.png   — instances overlaid at one regime (multi)

Two modes:
  # SINGLE run — explicit labels (back-compatible with the original script):
  aws s3 sync s3://<your-bench-bucket>/<run-prefix>/results ./run-local
  python build_deliverables.py ./run-local --out ./deliverables --regime fp8 --spec-decode

  # MULTI run — point at a parent dir holding many <run-prefix>/ subdirs; instance +
  # regime are derived from each run-prefix name (see derive_run_meta). Produces the
  # per-run artifacts AND the cross-regime / cross-instance overlay Pareto frontiers:
  aws s3 sync s3://<your-bench-bucket>/<run-prefix>/results ./all-runs   # (or a curated subset)
  python build_deliverables.py ./all-runs --multi --out ./deliverables

Per-server throughput uses the RAW `output_throughput` from result.json, which is
the box-level aggregate (verified empirically) — NOT the buggy BenchmarkRecord
`output_per_server = output_tps * dp` path. Both Pareto axes are box-level units.

Per-run methodology caveats: populated from the config file's `caveats` block. See
load_caveats(). The idea is that each run's specific caveats (which vLLM version, which
drafter/k, which quantization interaction) belong in that run's config, NOT hardcoded
here — otherwise a Gemma-era caveat wrongly attaches to future DSV4/Qwen tables.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

# Cell dir path shape: <prefix>_<isl>_<osl>_tp<N>[_dp<M>]/conc_<C>/
# (prefix is regime-dependent — gemma4_31b / _fp8 / _spec / _fp8_spec — and is
#  NOT used to recover the regime; that comes from the run-dir name / CLI flags.)
_PATH_RE = re.compile(r"_(\d+)_(\d+)_tp(\d+)(?:_dp(\d+))?(?:_ep\d+)?/conc_(\d+)")

# LEGACY run-prefix naming fallback (only used when a run dir has no regime_meta.json;
# every run from the current sweep_bench.py writes one, so this is purely for
# reprocessing older runs that predate regime_meta). Convention:
#   <model>_<prec>_<spec>[_<instance>]_run<N>_<date>, e.g.
#   <model>_bf16_nospec_run2_20260615        -> bf16 nospec, instance from --instance
#   <model>_fp8_spec_p5en_run1_20260616      -> fp8  spec  p5en
# Model prefix is a wildcard (not model-specific); instance is an optional token from
# a known set. If a legacy dir doesn't match, pass --instance and rely on regime_meta,
# or re-run so regime_meta.json is written.
_RUN_RE = re.compile(
    r"_(bf16|fp8)_(nospec|spec)(?:_(p5|p5en|b200|b300|trn2))?_run(\d+)_(\d+)"
)

# Cells with failure fraction above this are flagged unusable (saturation/overflow).
FAIL_THRESHOLD = 0.05

# Model label shown in table/plot titles. Set from --model-name (default neutral so
# non-Gemma runs aren't mislabeled). A module global so the write_* helpers read it
# without threading it through every signature.
MODEL_NAME = "Model"

# Coverage expected shardings: derived from the data at report time (below) rather
# than a hardcoded constant. With EP folded into the sharding label (tp4_dp2_ep8 etc),
# a static list would false-alarm on every non-MoE run or every MoE-only run.
# `coverage_report()` uses `SHARDINGS_EXPECTED` if set; when None, it derives the
# expected set from the UNION of shardings observed in ANY shape for that (inst, regime).
SHARDINGS_EXPECTED = None  # None = derive from data

# Methodology caveats, keyed and assembled per (instance, prec, spec) in run_caveats().
# Empty by default — these are project-specific (the Gemma study had a vLLM-version
# split on spec, DSV4-Flash used DSpark k=7 on a nightly image, etc.). Set from a
# config file's `caveats` block (see load_caveats), or edit this dict for one-shot
# runs. Leaving these hardcoded would falsely apply, e.g., the Gemma "MTP k=3 on
# 0.22.1rc1" note to every future spec run.
REGIME_NOTES: dict[str, str] = {}
INSTANCE_NOTES: dict[str, str] = {}


def load_caveats(cfg: dict) -> None:
    """Populate REGIME_NOTES / INSTANCE_NOTES from a config's `caveats` block.

    cfg["caveats"] = {"regime": {"spec": "…", "fp8": "…"}, "instance": {"b300": "…"}}
    Missing keys are silently ignored (no caveat = no caveat block on the table)."""
    cav = cfg.get("caveats") or {}
    REGIME_NOTES.update(cav.get("regime") or {})
    INSTANCE_NOTES.update(cav.get("instance") or {})


def _r(v, n=2):
    return round(float(v), n) if v is not None else None


def derive_run_meta(run_name: str) -> dict | None:
    """Map a run-prefix dir name -> {prec, spec, instance, run_no, date}. None if no match."""
    m = _RUN_RE.search(run_name)
    if not m:
        return None
    prec, spec_tok, inst, run_no, date = m.groups()
    return {
        "prec": prec,
        "spec": spec_tok == "spec",
        # instance token absent in the prefix → use the caller's --instance (threaded
        # in by read_regime_meta as default_instance); no hardcoded default here.
        "instance": inst,
        "run_no": int(run_no),
        "date": date,
    }


def regime_label(prec: str, spec: bool) -> str:
    return f"{prec} + spec" if spec else f"{prec} (no spec)"


def regime_key(prec: str, spec: bool) -> str:
    # EP is deliberately NOT part of the regime: a regime is an ENGINE difference
    # (precision/quant checkpoint, spec decode), while EP is a sharding property —
    # same image, same checkpoint, different parallelism. EP-on and EP-off cells
    # therefore merge into ONE regime, and EP shows up as extra `sharding` series
    # (tp8 vs tp8_ep8, …) inside each per-run Pareto/table, exactly like TP×DP.
    return f"{prec}_{'spec' if spec else 'nospec'}"


def parse_cell(result_path: Path, *, prec: str, spec: bool, instance: str,
               run_id: str, ep: bool = False) -> dict | None:
    """Parse one result.json + its path into a flat record. None if unparseable."""
    m = _PATH_RE.search(str(result_path))
    if not m:
        return None
    isl, osl, tp = int(m.group(1)), int(m.group(2)), int(m.group(3))
    dp = int(m.group(4)) if m.group(4) else 1
    conc = int(m.group(5))

    try:
        d = json.loads(result_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    completed = int(d.get("completed", 0) or 0)
    failed = int(d.get("failed", 0) or 0)
    total = completed + failed
    fail_frac = (failed / total) if total else 1.0
    duration = float(d.get("duration", 0) or 0)
    in_tok = int(d.get("total_input_tokens", 0) or 0)

    otps = float(d.get("output_throughput", 0) or 0)          # box-level aggregate
    itps = (in_tok / duration) if duration else 0.0           # derived: input tok/s

    # EP folds into the sharding label, NOT the regime: `tp4_dp2_ep8` reads as
    # "attention TP4×DP2, experts EP8". Degree = tp*dp (vLLM EP spans all ranks).
    sharding = f"tp{tp}_dp{dp}" if dp > 1 else f"tp{tp}"
    if ep:
        sharding += f"_ep{tp * dp}"

    rec = {
        "run_id": run_id,
        "instance": instance,
        "prec": prec,
        "spec": spec,
        "ep": ep,
        "regime": regime_label(prec, spec),
        "regime_key": regime_key(prec, spec),
        "isl": isl, "osl": osl,
        "shape": f"{isl}/{osl}",
        "tp": tp, "dp": dp, "sharding": sharding, "gpus": tp * dp,
        "conc": conc,
        # throughput
        "otps": round(otps, 1),
        "itps": round(itps, 1),
        # per-user output rate (Pareto x-axis), box-level units consistent w/ otps
        "per_user_otps": round(otps / conc, 2) if conc else 0.0,
        # latency (ms)
        "ttft_p50": _r(d.get("median_ttft_ms")),
        "ttft_p99": _r(d.get("p99_ttft_ms")),
        "tpot_p50": _r(d.get("median_tpot_ms")),
        "tpot_p99": _r(d.get("p99_tpot_ms")),
        "e2e_p50": _r(d.get("median_e2el_ms")),
        "e2e_p99": _r(d.get("p99_e2el_ms")),
        # quality / provenance
        "completed": completed, "failed": failed,
        "fail_frac": round(fail_frac, 3),
        "usable": fail_frac <= FAIL_THRESHOLD,
        "in_tok_per_req": round(in_tok / total) if total else 0,
    }

    # --- Speculative-decode metrics (present only on spec/fp8_spec runs) ---
    acc_rate = d.get("spec_decode_acceptance_rate")
    if acc_rate is not None:
        per_pos = d.get("spec_decode_per_position_acceptance_rates") or []
        n_draft = int(d.get("spec_decode_num_drafts", 0) or 0)
        draft_tok = int(d.get("spec_decode_draft_tokens", 0) or 0)
        rec.update({
            "accept_rate": _r(acc_rate, 1),                 # % avg draft-token acceptance
            "accept_len": _r(d.get("spec_decode_acceptance_length")),
            "per_pos": "/".join(f"{p:.2f}" for p in per_pos) if per_pos else None,
            # k recovered from the data as a sanity check (should == NUM_SPEC_TOKENS).
            "k_spec": round(draft_tok / n_draft) if n_draft else None,
        })
    return rec


def _ttft_bar_for_isl(isl: int, by_isl: dict) -> float | None:
    """The TTFT bar for an input length: smallest ISL-key >= isl (TTFT is
    prefill-bound, so the bar scales up with input length). Falls back to the
    largest key when isl exceeds all thresholds. None if no table given."""
    if not by_isl:
        return None
    keys = sorted(int(k) for k in by_isl)
    for k in keys:
        if isl <= k:
            return float(by_isl[str(k)])
    return float(by_isl[str(keys[-1])])


def apply_targets(df: pd.DataFrame, targets: dict) -> pd.DataFrame:
    """Add PASS/FAIL columns vs the production latency bars (median TTFT/TPOT).

    targets = {"ttft_p50_ms_by_isl": {"2048": 500, ...}, "tpot_p50_ms": 13}.
    A cell PASSes only if BOTH its median TTFT clears the ISL-scaled TTFT bar AND
    its median TPOT clears the TPOT bar. Only meaningful for usable cells; unusable
    (saturated) cells get pass=<NA> so they don't read as a real pass/fail.
    """
    by_isl = targets.get("ttft_p50_ms_by_isl") or {}
    tpot_bar = targets.get("tpot_p50_ms")
    df["ttft_bar_ms"] = df["isl"].map(lambda i: _ttft_bar_for_isl(int(i), by_isl))
    df["tpot_bar_ms"] = tpot_bar
    def _row_pass(r):
        if not r["usable"]:
            return pd.NA
        ok = True
        # NOTE: ttft_bar_ms is a NUMERIC column, so a missing bar is NaN, not None
        # (pandas coerces the None from _ttft_bar_for_isl via .map). Use pd.notna,
        # not `is not None` — `NaN is not None` is True and would compare against NaN.
        if pd.notna(r["ttft_bar_ms"]) and pd.notna(r["ttft_p50"]):
            ok = ok and (r["ttft_p50"] <= r["ttft_bar_ms"])
        if tpot_bar is not None and pd.notna(r["tpot_p50"]):
            ok = ok and (r["tpot_p50"] <= tpot_bar)
        return bool(ok)
    df["pass_ttft"] = df.apply(
        lambda r: (pd.NA if not r["usable"] or pd.isna(r["ttft_bar_ms"])
                   or pd.isna(r["ttft_p50"]) else bool(r["ttft_p50"] <= r["ttft_bar_ms"])),
        axis=1)
    df["pass_tpot"] = df.apply(
        lambda r: (pd.NA if not r["usable"] or tpot_bar is None
                   or pd.isna(r["tpot_p50"]) else bool(r["tpot_p50"] <= tpot_bar)),
        axis=1)
    df["pass"] = df.apply(_row_pass, axis=1)
    return df


def build_tidy(records: list[dict]) -> pd.DataFrame:
    if not records:
        sys.exit("No parseable result.json found.")
    df = pd.DataFrame(records)
    return df.sort_values(
        ["instance", "regime_key", "isl", "osl", "dp", "tp", "conc"]
    ).reset_index(drop=True)


def _iter_cells(run_dir: Path):
    for rp in sorted(run_dir.rglob("result.json")):
        if "serverlogs" in str(rp) or "cw_metrics" in str(rp):
            continue
        yield rp


def collect_single(run_dir: Path, prec: str, spec: bool, instance: str) -> list[dict]:
    rid = run_dir.name
    recs = [parse_cell(rp, prec=prec, spec=spec, instance=instance, run_id=rid)
            for rp in _iter_cells(run_dir)]
    return [r for r in recs if r]


def read_regime_meta(run_dir: Path, default_instance: str) -> dict | None:
    """Prefer the sweep's regime_meta.json (native path for sweep_bench.py output).

    sweep_bench.py writes one `regime_meta.json` per <model>__<regime> run dir with
    {prec, spec, quantization, kv_cache_dtype, model_key}. The instance isn't in it
    (a run is one box) — it comes from --instance at collection time. Falls back to
    the legacy `_RUN_RE` prefix convention for older run dirs that predate regime_meta.
    """
    meta_file = run_dir / "regime_meta.json"
    if meta_file.exists():
        try:
            m = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return {
            "prec": m.get("prec", "bf16"),
            "spec": bool(m.get("spec", False)),
            "ep": bool(m.get("ep", False)),
            "instance": default_instance,
            "run_no": 0,
            "date": "",
        }
    # Legacy fallback: parse the run-dir name. Backfill instance with default_instance
    # when the prefix didn't carry an instance token (derive_run_meta returns None there).
    legacy = derive_run_meta(run_dir.name)
    if legacy and not legacy.get("instance"):
        legacy["instance"] = default_instance
    return legacy


def collect_multi(parent: Path, default_instance: str = "gpu") -> list[dict]:
    """Discover run subdirs under `parent`, derive meta from each, parse all cells."""
    records, skipped = [], []
    for sub in sorted(p for p in parent.iterdir() if p.is_dir()):
        meta = read_regime_meta(sub, default_instance)
        if not meta:
            skipped.append(sub.name)
            continue
        ep = bool(meta.get("ep", False))
        recs = [parse_cell(rp, prec=meta["prec"], spec=meta["spec"], ep=ep,
                           instance=meta["instance"], run_id=sub.name)
                for rp in _iter_cells(sub)]
        recs = [r for r in recs if r]
        records.extend(recs)
        ep_note = " +EP" if ep else ""
        print(f"  {sub.name}: {len(recs)} cells "
              f"[{meta['instance']}, {regime_label(meta['prec'], meta['spec'])}{ep_note}]")
    if skipped:
        print(f"  (skipped {len(skipped)} non-run dirs: {', '.join(skipped[:6])}"
              f"{' …' if len(skipped) > 6 else ''})")
    return records


# ---- per-run metric tables -------------------------------------------------

BASE_COL_UNITS = {
    "sharding": "sharding",
    "conc": "concurrency (reqs)",
    "otps": "per-server output throughput (tok/s)",
    "itps": "per-server input throughput (tok/s)",
    "ttft_p50": "TTFT p50 (ms)",
    "ttft_p99": "TTFT p99 (ms)",
    "tpot_p50": "TPOT p50 (ms)",
    "tpot_p99": "TPOT p99 (ms)",
    "e2e_p50": "E2E p50 (ms)",
    "e2e_p99": "E2E p99 (ms)",
    "completed": "completed (reqs)",
    "failed": "failed (reqs)",
}
SPEC_COL_UNITS = {
    "accept_rate": "draft acceptance rate (%)",
    "accept_len": "mean acceptance length (tok)",
    "per_pos": "per-position acceptance",
}


def run_caveats(instance: str, prec: str, spec: bool) -> list[str]:
    """Return the caveat notes to attach to this cell's table, keyed off the
    run's regime + instance. Notes come from REGIME_NOTES / INSTANCE_NOTES which
    are populated by load_caveats() from the config file's `caveats` block.
    Absent keys => no caveat (that's a valid state: not every run has one)."""
    notes = []
    if spec and "spec" in REGIME_NOTES:
        notes.append(REGIME_NOTES["spec"])
    if prec == "fp8" and "fp8" in REGIME_NOTES:
        notes.append(REGIME_NOTES["fp8"])
    if instance in INSTANCE_NOTES:
        notes.append(INSTANCE_NOTES[instance])
    return notes


def write_tables(df: pd.DataFrame, out: Path):
    for (inst, rkey), gi in df.groupby(["instance", "regime_key"], sort=False):
        spec = bool(gi["spec"].iloc[0])
        regime = gi["regime"].iloc[0]
        prec = gi["prec"].iloc[0]
        cols = dict(BASE_COL_UNITS)
        if spec:  # splice acceptance columns in after the latency block
            cols = {**BASE_COL_UNITS, **SPEC_COL_UNITS}
        for shape, g in gi.groupby("shape", sort=False):
            tag = f"{inst}_{rkey}_{shape.replace('/', '_')}"
            present = [c for c in cols if c in g.columns]
            t = g[present].rename(columns={c: cols[c] for c in present}).copy()
            t.to_csv(out / f"table_{tag}.csv", index=False)
            notes = run_caveats(inst, prec, spec)
            note_md = ("\n**Caveats:** " + " ".join(notes)) if notes else ""
            (out / f"table_{tag}.md").write_text(
                f"## {MODEL_NAME} ({regime}) on {inst} — "
                f"workload {shape} (input/output tokens per request)\n\n"
                f"_All throughput figures are per-server (one {inst} instance)._"
                f"{note_md}\n\n"
                + t.to_markdown(index=False) + "\n"
            )
            print(f"  table_{tag}.csv / .md  ({len(t)} rows)")


# ---- Pareto plots ----------------------------------------------------------

def _pareto_frontier(rows: list[dict]) -> list[dict]:
    """Non-dominated upper-right envelope on (per_user_otps, otps); higher=better both."""
    front = []
    for p in rows:
        dominated = any(
            q is not p
            and q["otps"] >= p["otps"] and q["per_user_otps"] >= p["per_user_otps"]
            and (q["otps"] > p["otps"] or q["per_user_otps"] > p["per_user_otps"])
            for q in rows
        )
        if not dominated:
            front.append(p)
    return sorted(front, key=lambda r: r["per_user_otps"])


def write_pareto(df: pd.DataFrame, out: Path):
    """Per-run plot: one curve per sharding (the sharding study)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    for (inst, rkey), gi in df.groupby(["instance", "regime_key"], sort=False):
        regime = gi["regime"].iloc[0]
        for shape, g in gi.groupby("shape", sort=False):
            usable = g[g["usable"]]
            if usable.empty:
                continue
            fig, ax = plt.subplots(figsize=(8, 6))
            for sharding, s in usable.groupby("sharding"):
                s = s.sort_values("conc")
                ax.plot(s["per_user_otps"], s["otps"], marker="o", label=sharding)
                for _, r in s.iterrows():
                    ax.annotate(f"c={int(r['conc'])}", (r["per_user_otps"], r["otps"]),
                                fontsize=7, alpha=0.6, xytext=(3, 3),
                                textcoords="offset points")
            ax.set_xlabel("Per-user output rate (output tok/s per request)")
            ax.set_ylabel("Per-server output throughput (output tok/s)")
            ax.set_title(f"{MODEL_NAME} ({regime}) on {inst} — {shape} in/out tokens\n"
                         "point labels: c = concurrency (concurrent requests)")
            ax.legend(title="sharding (TP×DP)")
            ax.grid(True, alpha=0.3)
            tag = f"{inst}_{rkey}_{shape.replace('/', '_')}"
            fig.savefig(out / f"pareto_{tag}.png", dpi=200, bbox_inches="tight")
            plt.close(fig)
            print(f"  pareto_{tag}.png  ({len(usable)} usable pts, "
                  f"{len(g) - len(usable)} excluded)")


def write_overlays(df: pd.DataFrame, out: Path):
    """Headline overlays: per shape, frontier curves enveloping all shardings.
       (a) regimes overlaid on each instance; (b) instances overlaid at each regime."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def frontier_curve(ax, sub, label):
        u = sub[sub["usable"]]
        if u.empty:
            return False
        front = _pareto_frontier(u.to_dict("records"))
        if not front:
            return False
        xs = [r["per_user_otps"] for r in front]
        ys = [r["otps"] for r in front]
        ax.plot(xs, ys, marker="o", label=label)
        for r in front:  # annotate frontier vertices with conc + winning sharding
            ax.annotate(f"c={int(r['conc'])}", (r["per_user_otps"], r["otps"]),
                        fontsize=6, alpha=0.55, xytext=(3, 3), textcoords="offset points")
        return True

    def finish(ax, title, legend_title):
        ax.set_xlabel("Per-user output rate (output tok/s per request)")
        ax.set_ylabel("Per-server output throughput (output tok/s)")
        ax.set_title(title)
        ax.legend(title=legend_title)
        ax.grid(True, alpha=0.3)

    # (a) by-regime: fix instance, overlay regimes. Also fires when only ONE
    # regime exists — it degenerates to a single-curve "envelope" plot: the
    # best-across-all-shardings frontier per shape. That single curve is the
    # canonical "what's the best you can do at each op-point" artifact — same
    # information the multi-regime overlay reports, without a comparison.
    for inst, gi in df.groupby("instance", sort=False):
        for shape, g in gi.groupby("shape", sort=False):
            fig, ax = plt.subplots(figsize=(8, 6))
            drew = False
            for regime, s in g.groupby("regime", sort=False):
                drew |= frontier_curve(ax, s, regime)
            if not drew:
                plt.close(fig); continue
            n_reg = g["regime_key"].nunique()
            subtitle = ("Pareto frontier envelope (best across all shardings, incl. EP variants)"
                        if n_reg < 2 else
                        "Pareto frontier (envelopes all shardings); regimes overlaid")
            finish(ax, f"{MODEL_NAME} on {inst} — {shape} in/out tokens\n{subtitle}",
                   "regime")
            tag = f"{shape.replace('/', '_')}_by-regime_{inst}"
            fig.savefig(out / f"overlay_{tag}.png", dpi=200, bbox_inches="tight")
            plt.close(fig)
            print(f"  overlay_{tag}.png")

    # (b) by-instance: fix regime, overlay instances (only meaningful with >=2 instances)
    for rkey, gr in df.groupby("regime_key", sort=False):
        if gr["instance"].nunique() < 2:
            continue
        regime = gr["regime"].iloc[0]
        for shape, g in gr.groupby("shape", sort=False):
            fig, ax = plt.subplots(figsize=(8, 6))
            drew = False
            for inst, s in g.groupby("instance", sort=False):
                drew |= frontier_curve(ax, s, inst)
            if not drew:
                plt.close(fig); continue
            finish(ax, f"{MODEL_NAME} ({regime}) — {shape} in/out tokens\n"
                       "Pareto frontier (envelopes all shardings); instances overlaid",
                   "instance")
            tag = f"{shape.replace('/', '_')}_by-instance_{rkey}"
            fig.savefig(out / f"overlay_{tag}.png", dpi=200, bbox_inches="tight")
            plt.close(fig)
            print(f"  overlay_{tag}.png")


def coverage_report(df: pd.DataFrame, out: Path) -> list[str]:
    """Reconcile observed cells against the expected grid, per (instance, regime, shape).

    Two gap types, both reported (silent absence -> explicit line):
      - MISSING: a (sharding, conc) that SHOULD exist but wrote no result.json. The
        expected concurrency set is inferred as the UNION of concurrencies any sharding
        of that (instance, regime, shape) reached — so a tail that timed out on one
        sharding but completed on another is caught. A sharding entirely absent (in
        SHARDINGS_EXPECTED but with zero cells) is flagged whole.
      - UNUSABLE: a cell that wrote a result.json but failed the fail-rate guard
        (saturation/overflow) — present in CSV/tables, excluded from plots.

    Inference caveat: if ALL shardings stop at the same conc (e.g. every 128k sharding
    timed out past c=64), that conc is not in the union, so it can't be flagged as
    missing — the grid's true ceiling is unknown to the parser. Such uniformly-absent
    tails are noted as a caveat line, not per-cell.
    """
    lines = ["# Coverage report — observed vs expected cells", ""]
    any_gap = False
    for (inst, rkey), gi in df.groupby(["instance", "regime_key"], sort=False):
        lines.append(f"## {inst} / {rkey}")
        for shape, g in gi.groupby("shape", sort=False):
            concs_by_shard = {sh: set(s["conc"]) for sh, s in g.groupby("sharding")}
            expected_concs = set().union(*concs_by_shard.values()) if concs_by_shard else set()
            # expected shardings: global override if set (legacy); else derive from
            # the union of shardings observed in ANY shape for this (inst, regime) —
            # so a sharding absent from one shape but present in another is caught.
            exp_shardings = SHARDINGS_EXPECTED
            if exp_shardings is None:
                exp_shardings = sorted(
                    gi["sharding"].unique(),
                    key=lambda s: (-s.count("tp"), s),  # rough sort: tp8 first
                )
            present_shards = [s for s in exp_shardings if s in concs_by_shard]
            absent_shards = [s for s in exp_shardings if s not in concs_by_shard]

            n_unusable = int((~g["usable"]).sum())
            missing = []  # (sharding, conc) present-grid holes
            for sh in present_shards:
                for c in sorted(expected_concs - concs_by_shard[sh]):
                    missing.append((sh, c))

            status = []
            if absent_shards:
                status.append(f"SHARDING ABSENT: {', '.join(absent_shards)}")
            if missing:
                status.append("MISSING cells (no result.json): "
                               + ", ".join(f"{sh}@c{c}" for sh, c in missing))
            if n_unusable:
                bad = g[~g["usable"]]
                status.append(f"UNUSABLE (fail>{FAIL_THRESHOLD:.0%}, excluded from plots): "
                              + ", ".join(f"{r.sharding}@c{r.conc}({r.fail_frac:.0%})"
                                          for r in bad.itertuples()))
            if status:
                any_gap = True
                lines.append(f"  {shape}: " + " | ".join(status))
            else:
                lines.append(f"  {shape}: OK ({len(g)} cells, grid "
                             f"{min(expected_concs)}–{max(expected_concs)})")
        lines.append("")
    if not any_gap:
        lines.append("No gaps detected: every observed sharding covers the union grid, "
                     "no unusable cells.")
    (out / "coverage.txt").write_text("\n".join(lines) + "\n")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir", type=Path,
                    help="single run dir, or parent of many run dirs with --multi")
    ap.add_argument("--out", type=Path, default=Path("./deliverables"))
    ap.add_argument("--multi", action="store_true",
                    help="treat results_dir as a parent of <run-prefix>/ subdirs; "
                         "derive instance+regime from each name")
    # single-run labels (ignored in --multi mode):
    ap.add_argument("--regime", default="bf16", choices=["bf16", "fp8"],
                    help="[single mode] weight precision regime")
    ap.add_argument("--spec-decode", action="store_true",
                    help="[single mode] set if this run used speculative decoding")
    ap.add_argument("--instance", default="p5",
                    help="EC2 instance label (p5, p5en, b200, b300). Single mode: the "
                         "run's instance. Multi mode: the box label applied to every "
                         "run dir whose regime_meta.json has no instance (a sweep runs "
                         "on one box); legacy Gemma dirs still derive it from the name.")
    ap.add_argument("--model-name", default=None,
                    help="model label for table/plot titles (e.g. 'Qwen3-235B-A22B'). "
                         "Defaults to the model_key from the runs' regime_meta.json, "
                         "or 'Model'. Prevents mislabeling non-Gemma runs.")
    ap.add_argument("--no-plots", action="store_true", help="skip PNGs (CSV/tables only)")
    ap.add_argument("--config", type=Path, default=None,
                    help="bench_config.json — if it has a 'targets' block, flag each "
                         "cell PASS/FAIL vs the production TTFT(ISL)/TPOT latency bars.")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)

    if args.multi:
        print(f"Discovering runs under {args.results_dir} …")
        records = collect_multi(args.results_dir, default_instance=args.instance)
    else:
        records = collect_single(args.results_dir, args.regime, args.spec_decode,
                                 args.instance)

    df = build_tidy(records)

    # Model label for titles: explicit --model-name, else derive from the run dirs
    # (run_id is "<key>__<regime>"; the shared key prefix is the model). Falls back
    # to "Model" so a non-Gemma run is never mislabeled.
    global MODEL_NAME
    if args.model_name:
        MODEL_NAME = args.model_name
    else:
        keys = {str(r).split("__", 1)[0] for r in df.get("run_id", [])}
        MODEL_NAME = keys.pop() if len(keys) == 1 else (
            "/".join(sorted(keys)) if keys else "Model")
    print(f"Model label for titles: {MODEL_NAME}")

    # Latency-bar PASS/FAIL vs production targets (references/profiling-targets.md).
    # AND methodology caveats — both keyed off the same config file.
    targets: dict = {}
    cfg: dict = {}
    if args.config and args.config.exists():
        try:
            cfg = json.loads(args.config.read_text()) or {}
            targets = cfg.get("targets") or {}
        except (json.JSONDecodeError, OSError) as e:
            print(f"  (couldn't read config {args.config}: {e})")
    if targets:
        df = apply_targets(df, targets)
    load_caveats(cfg)  # populates REGIME_NOTES / INSTANCE_NOTES from cfg["caveats"]

    df.to_csv(args.out / "all_cells.csv", index=False)
    n_bad = (~df["usable"]).sum()
    combos = df.groupby(["instance", "regime_key"]).ngroups
    print(f"all_cells.csv: {len(df)} cells across {combos} (instance,regime) combos "
          f"({n_bad} flagged unusable, fail>{FAIL_THRESHOLD:.0%})")

    if targets and "pass" in df.columns:
        gated = df[df["pass"].notna()]
        n_pass = int((gated["pass"] == True).sum())  # noqa: E712
        n_gate = len(gated)
        print(f"Latency bars (TTFT≤ISL-scaled, TPOT≤{targets.get('tpot_p50_ms')}ms): "
              f"{n_pass}/{n_gate} usable cells PASS")
        fails = gated[gated["pass"] == False]  # noqa: E712
        for (inst, rk, shp), g in fails.groupby(["instance", "regime_key", "shape"]):
            # report the fastest failing cell per group (the closest miss)
            b = g.loc[g["tpot_p50"].idxmin()]
            reason = []
            # pass_ttft/pass_tpot are bool-or-NA. Guard with pd.notna() FIRST: a bare
            # `pass_ttft == False` yields pd.NA when the value is NA (no TTFT bar for
            # this ISL but failed on TPOT), and the following `and` calls bool(pd.NA)
            # → "boolean value of NA is ambiguous" TypeError. pd.notna short-circuits
            # that; `not <val>` then handles numpy bools correctly (unlike `is False`).
            pass_ttft_val = b.get("pass_ttft")
            if pd.notna(pass_ttft_val) and not pass_ttft_val and pd.notna(b["ttft_bar_ms"]):
                reason.append(f"TTFT {b['ttft_p50']:.0f}>{b['ttft_bar_ms']:.0f}ms")
            pass_tpot_val = b.get("pass_tpot")
            if pd.notna(pass_tpot_val) and not pass_tpot_val:
                reason.append(f"TPOT {b['tpot_p50']:.1f}>{b['tpot_bar_ms']}ms")
            print(f"  ✗ {inst}/{rk} {shp}: best cell misses ({', '.join(reason) or 'latency bar'})")

    print("Coverage:")
    cov = coverage_report(df, args.out)
    # Echo only the lines that flag a gap (indented run/shape lines with a STATUS),
    # so silent absence surfaces in the console, not just coverage.txt.
    gap_lines = [ln for ln in cov if any(k in ln for k in
                 ("MISSING", "ABSENT", "UNUSABLE"))]
    if gap_lines:
        for ln in gap_lines:
            print(f"  ⚠ {ln.strip()}")
        print(f"  → full report: {args.out}/coverage.txt")
    else:
        print("  no gaps detected (see coverage.txt)")

    print("Tables:")
    write_tables(df, args.out)

    if not args.no_plots:
        print("Per-run Pareto:")
        write_pareto(df, args.out)
        # Always emit the frontier envelope plot (single-regime runs get the
        # "best across all shardings incl. EP variants" curve; multi-regime runs
        # get the same curve per regime overlaid). The by-instance overlay only
        # fires when there are ≥2 instances, handled inside write_overlays.
        print("Overlays:")
        write_overlays(df, args.out)

    print(f"\nDone → {args.out}/")


if __name__ == "__main__":
    main()
