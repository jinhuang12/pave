"""CLI: run the parallelism planner on a run-spec and print the plan.

    python3 -m planner configs/granite4-8b-run.json
    PARALLELISM_PLANNER_RUNSPEC=configs/gemma4-a4b-run.json python3 -m planner

Prints, per bucket: the selected config (serving mode from the run-spec) with its SLA
verdicts, and the full decode landscape. The serving mode, prefill TTFT floor, and decode ITL
ceiling come from the run-spec (build_bundle); CLI flags override them.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # scripts/ on path (siblings: entitlement_formulas)

import json

from planner.build import build_bundle, cell
from planner.plan import plan, sweep_results, GRID_AXES, DISABLED_AXES, set_disabled_axes
from planner.shard import ShardConfig
from planner.build import ep_default_enabled


def _label(t) -> str:
    s = f"TP{t['TP']}"
    if t.get("CP", 1) > 1:
        s += f" CP{t['CP']}"
    if t.get("DP", 1) > 1:
        s += f" DP{t['DP']}"
    return s


def _run_cell(b, args) -> None:
    """--cell mode: evaluate ONE (config, bucket, bs, regime) and print its per-(component,
    layer-type) by_type breakdown -- the same table `planner.build.cell(...)['by_type']`
    produces, exposed on the CLI. This is the entitlement to compare against a single
    on-hardware run at the SAME (TP, CP, DP, bs, context) operating point (rather than the
    sweep's max-feasible-bs pick). Nothing is computed here that cell() doesn't already return.
    """
    ep = ep_default_enabled(b.arch) and "EP" not in DISABLED_AXES
    cfg = ShardConfig(TP=args.tp, CP=args.cp, DP=args.dp, ep_enabled=ep)
    c = cell(b, cfg, args.ctx, args.bs, args.prefill)
    regime = c["regime"]
    otps = (args.bs * 1e3 / c["step_ms"]) if c["step_ms"] > 0 else 0.0
    N = b.hw.cores_per_node
    tput_node = otps * N / cfg.world if cfg.world else 0.0

    print(f"=== {b.raw.get('model', args.runspec)} on {b.raw.get('hardware_name','hw')} "
          f"| cell TP{cfg.TP} CP{cfg.CP} DP{cfg.DP} bs{args.bs} ctx{args.ctx // 1024}k "
          f"regime={regime} ===")
    print(f"cores(world)={cfg.world}  step_ms={c['step_ms']:.3f}  comm_ms={c['comm_ms']:.3f}  "
          f"binding={c['binding']}")
    if not args.prefill:
        print(f"decode otps={otps:.0f}/replica  tput/node(x{N // cfg.world if cfg.world else 0})="
              f"{tput_node:.0f}")
    print()
    hdr = ("%-20s %6s %10s %10s %9s %8s %8s %8s  %s"
           % ("component/type", "layers", "W_MB", "KV_MB", "FLOP_G",
              "load_ms", "flop_ms", "time_ms", "bind"))
    print(hdr)
    print("-" * len(hdr))
    for k, v in c["by_type"].items():
        print("%-20s %6d %10.2f %10.1f %9.3f %8.3f %8.3f %8.3f  %s"
              % (k, v["layers"], v["weight_bytes"] / 1e6, v["kv_bytes"] / 1e6,
                 v["flops"] / 1e9, v["load_ms"], v["flop_ms"], v["time_ms"], v["bound"]))
    print("-" * len(hdr))
    print("%-20s %6s %10.2f %10.1f %9.3f"
          % ("model total", "", c["per_device_weight_bytes"] / 1e6,
             c["per_device_kv_bytes"] / 1e6, c["per_device_flops"] / 1e9))


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallelism planner (segment engine).")
    ap.add_argument("runspec", nargs="?",
                    default=os.environ.get("PARALLELISM_PLANNER_RUNSPEC",
                                           os.path.join(HERE, "..", "configs",
                                                        "gemma4-31b-run.json")),
                    help="path to a run-spec JSON (or set PARALLELISM_PLANNER_RUNSPEC).")
    ap.add_argument("--serving", choices=("colocated", "disaggregated"), default=None,
                    help="override the run-spec serving mode.")
    ap.add_argument("--prefill-itps-floor", type=float, default=None,
                    help="override the prefill TTFT SLA (input-tokens/s floor).")
    ap.add_argument("--prefill-ttft-ceiling-ms", type=float, default=None,
                    help="override the prefill TTFT SLA as a flat ceiling (ms) applied at every "
                         "context. Mutually exclusive with --prefill-itps-floor.")
    ap.add_argument("--decode-itl-ceiling-ms", type=float, default=None,
                    help="override the decode ITL ceiling (ms).")
    ap.add_argument("--usable-fraction", type=float, default=None,
                    help="override the usable HBM fraction (per-core memory budget = "
                         "capacity x fraction; default 0.90). Lower it to model a tighter "
                         "budget. Precedence: this flag > run-spec usable_hbm_fraction > "
                         "hardware JSON > 0.90.")
    ap.add_argument("--prefill-allow-kv-replication", action="store_true",
                    help="allow disaggregated prefill targets that REPLICATE the KV cache "
                         "(TP > min KV-head count); off by default (replication shrinks the "
                         "prefix-cache budget for no cache benefit).")
    # Per-axis enable/disable (TP/CP/DP/EP). A disabled axis is pinned to degree 1 (EP: off),
    # so it never appears in a planned config -- e.g. --disable-cp for TP-only planning.
    # Overrides the PARALLELISM_PLANNER_DISABLE_AXES env default. (PP is out of scope, no flag.)
    for ax in GRID_AXES:
        g = ap.add_mutually_exclusive_group()
        g.add_argument("--disable-%s" % ax.lower(), dest="disable_%s" % ax.lower(),
                       action="store_true", default=None,
                       help="exclude %s from the parallelism grid (pin to 1)." % ax)
        g.add_argument("--enable-%s" % ax.lower(), dest="disable_%s" % ax.lower(),
                       action="store_false", default=None,
                       help="allow %s in the parallelism grid." % ax)
    # --cell: evaluate a SINGLE (config, bs, context) operating point and print its by_type
    # breakdown, then exit -- the entitlement to compare against one on-hardware run at that
    # exact point. Skips the sweep. Uses --tp/--cp/--dp/--bs/--ctx/--prefill below.
    ap.add_argument("--cell", action="store_true",
                    help="evaluate ONE (config, bs, context) cell and print its per-(component,"
                         " layer-type) by_type breakdown, then exit (no sweep). Pin the point "
                         "with --tp/--cp/--dp/--bs/--ctx/--prefill.")
    ap.add_argument("--tp", type=int, default=8, help="[--cell] tensor-parallel degree.")
    ap.add_argument("--cp", type=int, default=1, help="[--cell] context-parallel degree.")
    ap.add_argument("--dp", type=int, default=1, help="[--cell] data-parallel degree.")
    ap.add_argument("--bs", type=int, default=16, help="[--cell] batch size.")
    ap.add_argument("--ctx", type=int, default=8192, help="[--cell] context length (tokens).")
    ap.add_argument("--prefill", action="store_true",
                    help="[--cell] evaluate the prefill regime (default: decode).")
    ap.add_argument("--emit", action="store_true",
                    help="write the JSON planner artifacts (pareto_frontier / target_config / "
                         "prefill_target_config / entitlement) to PARALLELISM_PLANNER_OUTDIR.")
    ap.add_argument("--visualize", action="store_true",
                    help="write the roofline / Pareto / prefill figures (PNG) to "
                         "PARALLELISM_PLANNER_OUTDIR (needs matplotlib).")
    args = ap.parse_args()

    # Apply per-axis flags on top of the env-derived default set (None = leave at default).
    disabled = set(DISABLED_AXES)
    for ax in GRID_AXES:
        val = getattr(args, "disable_%s" % ax.lower(), None)
        if val is True:
            disabled.add(ax)
        elif val is False:
            disabled.discard(ax)
    set_disabled_axes(disabled)

    b = build_bundle(args.runspec)
    # CLI override of the usable HBM fraction (highest precedence). build_bundle already
    # applied any run-spec / hardware-JSON value onto b.hw.usable_fraction.
    if args.usable_fraction is not None:
        b.hw.usable_fraction = args.usable_fraction

    # --cell: single-point entitlement breakdown, then exit (no sweep).
    if args.cell:
        _run_cell(b, args)
        return

    serving = args.serving or b.serving
    if args.prefill_itps_floor is not None and args.prefill_ttft_ceiling_ms is not None:
        ap.error("set only one of --prefill-itps-floor or --prefill-ttft-ceiling-ms.")
    # Flat TTFT ceiling: CLI flag > run-spec (b.prefill_ttft_ceiling_ms), unless a floor is
    # given on the CLI (which selects the context-scaled ceiling instead).
    if args.prefill_itps_floor is not None:
        itps, ttft_ceil = args.prefill_itps_floor, None
    elif args.prefill_ttft_ceiling_ms is not None:
        itps, ttft_ceil = b.prefill_itps_floor, args.prefill_ttft_ceiling_ms
    else:
        itps, ttft_ceil = b.prefill_itps_floor, b.prefill_ttft_ceiling_ms
    itl = args.decode_itl_ceiling_ms if args.decode_itl_ceiling_ms is not None else b.decode_itl_ceiling_ms
    p = plan(b, serving=serving, prefill_itps_floor=itps, decode_itl_ceiling_ms=itl,
             prefill_ttft_ceiling_ms=ttft_ceil,
             prefill_allow_kv_replication=args.prefill_allow_kv_replication)

    hw_name = b.raw.get("hardware_name", "hw")
    ttft_sla = (f"TTFT ceiling={ttft_ceil:.0f} ms" if ttft_ceil is not None
                else f"TTFT floor={itps:.0f} ITPS")
    print(f"=== {b.raw.get('model', args.runspec)} on {hw_name} "
          f"[serving={serving}, {ttft_sla}, ITL ceiling={itl:.0f} ms, "
          f"usable HBM={b.hw.usable_fraction:.0%}] ===")
    print(f"grid={p['grid_size']} configs, buckets={[s // 1024 for s in p['buckets']]}k\n")

    for S in p["buckets"]:
        print(f"--- {S // 1024}k ---")
        if serving == "colocated":
            t = p["selected"][S]
            if t:
                print(f"  SELECTED {_label(t):14} cores={t['cores']:<3} "
                      f"TTFT={t['ttft_ms'] / 1000:.2f}s ITL={t['step_ms']:.0f}ms "
                      f"sla_met={t['sla_met']} tput/node={t['tput_per_node']:.0f}")
        else:
            d = p["decode_pool"][S]
            pr = p["prefill_pool"][S]
            print(f"  DECODE  {_label(d):14} cores={d['cores']:<3} ITL={d['step_ms']:.0f}ms "
                  f"tput/node={d['tput_per_node']:.0f}")
            print(f"  PREFILL {_label(pr):14} cores={pr['cores']:<3} "
                  f"TTFT={pr['ttft_ms'] / 1000:.2f}s sla_met={pr['sla_met']}")
        for r in p["decode_grid"][S]:
            print(f"      {_label(r):14} cores={r['cores']:<3} bs={r['max_bs']:<4} "
                  f"ITL={r['step_ms']:6.1f}ms tput/node={r['tput_per_node']:>9.0f} "
                  f"bind={r['binding']}")
        print()

    # Full-sweep dump: the complete decode + prefill cell dicts at max-feasible bs, written
    # to PARALLELISM_PLANNER_OUTDIR (the run's artifact directory), else the current
    # directory - never this skill's own directory (same shape as the old sweep.py::main).
    sr = sweep_results(b)
    sr_dir = os.environ.get("PARALLELISM_PLANNER_OUTDIR") or os.getcwd()
    os.makedirs(sr_dir, exist_ok=True)
    sr_path = os.path.join(sr_dir, "sweep_results.json")
    with open(sr_path, "w") as f:
        json.dump(sr, f, indent=2)
    print(f"wrote {os.path.relpath(sr_path)} "
          f"({len(sr['decode'])} decode + {len(sr['prefill'])} prefill cells)")

    if args.emit:
        from planner.emit import emit
        for path in emit(b, p):
            print(f"wrote {os.path.relpath(path)}")

    if args.visualize:
        from planner.visualize import visualize
        for path in visualize(b, p):
            print(f"wrote {os.path.relpath(path)}")


if __name__ == "__main__":
    main()
