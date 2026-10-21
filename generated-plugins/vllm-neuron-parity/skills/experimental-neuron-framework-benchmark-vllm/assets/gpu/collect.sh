#!/usr/bin/env bash
# Consolidate the sweep's result.json files into a quick table. sweep_bench.py
# writes: <results>/<key>__<regime>/<key>_<isl>_<osl>_<tag>/conc_<C>/result.json
# (tag = tp<N> or tp<N>_dp<M>) — TWO dir levels under <results>, so walk recursively.
#
# This is a quick on-box sanity table only. For the real deliverables (per-shape
# metric tables, Pareto frontiers over the sharding x concurrency grid, coverage
# report, and the cost plots), sync the results back and run build_deliverables.py
# locally — that is the tool that understands the full sweep.
set -uo pipefail
RES="${1:-/scratch/results}"
python3 - "$RES" <<'PY'
import json, os, re, sys, glob
res = sys.argv[1]
# <key>_<isl>_<osl>_tp<N>[_dp<M>][_ep<E>]/conc_<C>/result.json
# `[^/]+` (not `.+`) for the key: the layout is TWO dir levels
# (<key>__<regime>/<key>_<isl>_..._<tag>/conc_<C>), so a greedy `.+` would cross the
# `/` and swallow "<key>__<regime>/<key>" into the key column. Restrict to one path
# component. `_ep<E>` handles the expert-parallel tag (tp8_ep8 etc.).
pat = re.compile(r"([^/]+)_(\d+)_(\d+)_tp(\d+)(?:_dp(\d+))?(?:_ep\d+)?/conc_(\d+)/result\.json$")
rows = []
for f in sorted(glob.glob(os.path.join(res, "**", "conc_*", "result.json"), recursive=True)):
    rel = os.path.relpath(f, res)
    m = pat.search(rel)
    if not m:
        continue
    key, isl, osl, tp, dp, conc = m.groups()
    dp = dp or "1"
    try:
        d = json.load(open(f))
    except Exception as e:
        print(f"  parse error {rel}: {e}"); continue
    completed = int(d.get("completed", 0) or 0)
    failed = int(d.get("failed", 0) or 0)
    total = completed + failed
    fail_pct = (100.0 * failed / total) if total else 100.0
    rows.append(dict(
        key=key, shape=f"{isl}/{osl}", tag=f"tp{tp}_dp{dp}" if dp != "1" else f"tp{tp}",
        conc=int(conc), completed=completed, fail_pct=fail_pct,
        otps=d.get("output_throughput", 0) or 0,
        total_tps=d.get("total_token_throughput", 0) or 0,
        ttft=d.get("median_ttft_ms", 0) or 0,
        tpot=d.get("median_tpot_ms", 0) or 0,
    ))
if not rows:
    print(f"No result.json found under {res} in the expected "
          f"<key>_<isl>_<osl>_<tag>/conc_<C>/ layout.")
    sys.exit(0)
hdr = (f"{'model':10} {'shape':10} {'sharding':10} {'conc':>5} {'compl':>6} "
       f"{'fail%':>6} {'out tok/s':>10} {'total tok/s':>11} {'TTFT ms':>8} {'TPOT ms':>8}")
print(hdr); print("-" * len(hdr))
for r in sorted(rows, key=lambda x: (x["key"], x["shape"], x["tag"], x["conc"])):
    flag = "  <-- FAILED>5%" if r["fail_pct"] > 5 else ""
    print(f"{r['key']:10} {r['shape']:10} {r['tag']:10} {r['conc']:>5} "
          f"{r['completed']:>6} {r['fail_pct']:>5.0f}% {r['otps']:>10.1f} "
          f"{r['total_tps']:>11.1f} {r['ttft']:>8.1f} {r['tpot']:>8.1f}{flag}")
print(f"\n{len(rows)} cells. Cells flagged FAILED>5% are excluded from clean results.")
print("For full deliverables (Pareto, coverage, cost plots), run build_deliverables.py "
      "on the synced results locally.")
PY
