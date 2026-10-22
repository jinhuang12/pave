#!/bin/bash
# Classify a bring-up compile failure: find the REAL NCC_ code behind "error 70",
# and cluster the compile cache into passing vs failing graphs so you can diff
# good-vs-bad fxgraph op histograms (the core porting localization move).
#
# Usage: classify_compile_failure.sh <compile_cache_dir> [worker_run.out]
#
# Example:
#   classify_compile_failure.sh /tmp/vllm_neuron_wdir_$USER/neuron/compile_cache slurm-30247.out
# XLA route only. On the native route, follow the compiler-artifacts skill's
# references/compile_route.md (../experimental-neuron-perf-compiler-artifact-debugging/).
#
# Prints:
#   1. tally of real NCC_ codes across per-rank log-neuron-cc.txt (exit 70 is useless alone)
#   2. any runtime status= codes seen in the worker log
#   3. per-graph cluster: NEFF-present vs fxgraph.txt line count (splits pass from fail)

set -uo pipefail

CACHE="${1:-}"
RUNOUT="${2:-}"

if [ -z "$CACHE" ] || [ ! -d "$CACHE" ]; then
    echo "Usage: $0 <compile_cache_dir> [worker_run.out]" >&2
    exit 1
fi

echo "=== 1. Real NCC_ codes (per-rank log-neuron-cc.txt) — exit 70 is overloaded ==="
# grep -a: logs are often flagged binary. Only NCC_ codes with a source location are real.
find "$CACHE" -name 'log-neuron-cc.txt' -print0 2>/dev/null \
  | xargs -0 grep -a -hoE 'NCC_[IE][A-Z]+[0-9]+' 2>/dev/null \
  | sort | uniq -c | sort -rn
echo "(none found above? the fatal code may be a bare token — open the newest log directly)"
echo "  newest: $(find "$CACHE" -name 'log-neuron-cc.txt' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)"
echo

if [ -n "$RUNOUT" ] && [ -f "$RUNOUT" ]; then
    echo "=== 2. Runtime status / fabric codes in worker log ==="
    grep -a -oE 'status=[0-9]+|NRT_[A-Z_]+|DGE out-of-bound|Collectives barrier failure' "$RUNOUT" \
      | sort | uniq -c | sort -rn
    echo "--- cache freshness (fresh compile vs stale/poisoned hit) ---"
    grep -aE 'cache hits|compiling|cache key|Falling back to /tmp' "$RUNOUT" | tail -5
    echo
fi

echo "=== 3. Cluster graphs: NEFF-present vs fxgraph.txt line count ==="
echo "(passing graphs produced a .neff; failing ones cluster at a different fx size)"
for d in "$CACHE"/*/; do
    name=$(basename "$d")
    neff=$(find "$d" -maxdepth 2 -name '*.neff' 2>/dev/null | wc -l)
    fx=$(wc -l < "$(find "$d" -maxdepth 2 -name fxgraph.txt 2>/dev/null | head -1)" 2>/dev/null || echo 0)
    printf '%-40s neff=%s fx_lines=%s\n' "$name" "$neff" "$fx"
done | sort -t= -k3 -n

cat <<'EOF'

=== NEXT: diff good-vs-bad fxgraph op histograms to name the offending feature ===
  GOOD=<cache>/<hash_with_neff>/dev0_*.rank0
  BAD=<cache>/<hash_no_neff>/dev0_*.rank0
  grep -oP '= call_function\[target=\K[^\]]+' $GOOD/fxgraph.txt | sort | uniq -c | sort -rn > /tmp/good.hist
  grep -oP '= call_function\[target=\K[^\]]+' $BAD/fxgraph.txt  | sort | uniq -c | sort -rn > /tmp/bad.hist
  diff /tmp/good.hist /tmp/bad.hist   # ops unique to BAD = the new layer type (see arch_feature_risk.md)
EOF
