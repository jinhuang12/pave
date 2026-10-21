# Comparing two compiler versions on the same HLO

When the same HLO produces different throughput on compiler A vs B, the goal is
to attribute the delta to specific lowering changes (or rule out misattribution
like clock skew, cache effects, NEFF format differences).

## Step 1 — same HLO, two compilers

The HLO content **does not depend on the compiler**, only on the framework
(torch_xla / jax). So you can take any cache hash compiled by one compiler and
re-compile with another.

```bash
SRC_HLO=<compile_cache>/<hash>/graph.hlo  # or .../dev0_*.rank0/graph.hlo

DBG_A=/mnt/data/penguin_<run>_compilerA
DBG_B=/mnt/data/penguin_<run>_compilerB
mkdir -p $DBG_A $DBG_B
cp $SRC_HLO $DBG_A/graph.hlo
cp $SRC_HLO $DBG_B/graph.hlo
```

For each, install the relevant compiler (`pip install --force-reinstall --no-deps
<wheel>.whl`) then compile:

```bash
cd $DBG_X && neuronx-cc compile graph.hlo \
    --framework XLA --target trn2 --output graph.neff \
    --logfile log-neuron-cc.txt --auto-cast=none --verbose=35 -O1 \
    --internal-backend-options=--enable-verifier=false \
    --internal-compiler-debug-mode=penguin
```

## Step 2 — high-level structural diff

```bash
echo "=== sg counts ==="
echo "A: $(ls -d $DBG_A/penguin-sg* 2>/dev/null | wc -l)"
echo "B: $(ls -d $DBG_B/penguin-sg* 2>/dev/null | wc -l)"

echo "=== penguin file types per sg ==="
for d in $DBG_A $DBG_B; do
    echo "--- $d ---"
    for sg in $d/penguin-sg*/; do
        files=$(ls $sg | tr '\n' ',' | sed 's/,$//')
        lines=$(wc -l $sg/penguin.py 2>/dev/null | awk '{print $1}')
        echo "  $(basename $sg): files=[$files], penguin.py=$lines lines"
    done
done

echo "=== TiledOffloadedMemCpy count (the must-alias bug indicator) ==="
echo "A: $(grep -c 'TiledOffloadedMemCpy' $DBG_A/penguin-sg*/penguin.py | awk -F: '{s+=$2} END{print s}')"
echo "B: $(grep -c 'TiledOffloadedMemCpy' $DBG_B/penguin-sg*/penguin.py | awk -F: '{s+=$2} END{print s}')"
```

## Step 3 — per-sg op-class breakdown

If a sg's IR uses different op classes between compilers, that sg uses a
different lowering pipeline (e.g. legacy Penguin vs MLIR-affine).

```bash
for sg in penguin-sg0000 penguin-sg0001 penguin-sg0002 penguin-sg0003; do
    echo "=== $sg ==="
    for d in $DBG_A $DBG_B; do
        if [ -f $d/$sg/penguin.py ]; then
            echo "--- $(basename $d) ---"
            grep -oE '\bm[0-9]+\.\w+\b' $d/$sg/penguin.py | sort | uniq -c | sort -rn | head -8
        fi
    done
done
```

Look for telltale dialect signals:

| Op | Means |
|----|-------|
| `m6.NeuronIndicesAP`, `m2.Loop`, `m5.NeuronSBTensor`, `m11.ALUOpcode` | Legacy Penguin/Starfish IR |
| `m3.AffineLoad`, `m3.AffineStore`, `m1.Loop`, `m5.UnaryOp`, `m5.BinaryOp` | MLIR-affine IR |
| `m9.ExternalNativeNkiKlirKernel` | An NKI kernel was inlined (e.g. MLP, attention) — the kernel bytes are external |
| `m12.AllReduceOp`, `m12.ReduceScatterOp` | Cross-rank collective |

## Step 4 — pre-MaskProp IR diff (when one fails)

If A succeeds and B crashes (or vice versa), compare the IR snapshot just
before the failing pass. See [crash_triage.md](crash_triage.md) Step 5.

## Step 5 — runtime confirmation

The IR diff predicts what should happen at runtime. Confirm with a profile.
Required artifacts: `bench_results.json` + ingested `Summary.parquet` per
NEFF.

```python
import os, glob, pandas as pd
NE = os.path.expanduser("~/.local/share/neuron-profile/profiles/global")

def find_decode(prefix):
    for c in sorted(glob.glob(f"{NE}/{prefix}_*_session_0@latest")):
        s = pd.read_parquet(f"{c}/Summary.parquet").iloc[0]
        if float(s['hardware_flops']) < 1e11:   # decode (vs prefill)
            return c
    return None

A = find_decode("<run_name_A>")
B = find_decode("<run_name_B>")

cols = [
    'total_time', 'dma_active_time', 'dma_transfer_total_bytes',
    'tensor_engine_active_time', 'gpsimd_engine_active_time',
    'vector_engine_active_time', 'scalar_engine_active_time',
    'cc_op_active_time', 'hardware_flops',
    'hbm_read_bytes', 'hbm_write_bytes',
    'mfu_estimated_percent', 'mbu_estimated_percent',
]
sa = pd.read_parquet(f"{A}/Summary.parquet").iloc[0]
sb = pd.read_parquet(f"{B}/Summary.parquet").iloc[0]
for c in cols:
    a, b = float(sa[c]), float(sb[c])
    print(f"  {c:42}  A={a:>14.3f}  B={b:>14.3f}  Δ%={100*(b-a)/a if a else 0:+6.1f}%")
```

### What to look for

| Metric change | Interpretation |
|---------------|---------------|
| `hbm_write_bytes` -50%+ with no math change | Redundant copy was eliminated (look for `TiledOffloadedMemCpy` count drop in IR) |
| `dma_active_time` ↓, `tensor_engine_active_time` ≈ same | DMA bottleneck relieved (good) |
| `vector_engine_active_time` ↑ a lot, `scalar_engine_active_time` ↓ | Lowering changed which engine carries small ALU ops |
| `transpose_flops` change | Layout / contiguity rewrite (`TransformLayoutPass`) |
| `mbu_estimated_percent` ↓ even though throughput ↑ | Counter-intuitive but normal: peak-BW × wall is the denominator; less wasted traffic shrinks numerator faster. |

## Step 6 — write up the attribution

Tabulate IR-level change ↔ Summary-level change ↔ throughput:

```
1. <IR change>: e.g. "TiledOffloadedMemCpy count: 8 → 0 in sg0000+sg0001"
2. <runtime confirmation>: "hbm_write_bytes: 555 MB → 18 MB (-96.6%)"
3. <throughput contribution>: "≈80% of the 9745 → 10803 tok/s improvement"
```

A real example walkthrough is in
[CC258074_VS_OLD_PROFILE_ANALYSIS.md from session memory] — uses the same
must-alias detection, byte-counting, and engine-rebalance attribution flow.

## Pitfalls

- **Same version string ≠ same binary.** Two compiler wheels can label themselves
  `0.0.0.0+abc123` but ship different binaries. Cache won't notice. Use
  `VLLM_NEURON_DISABLE_COMPILE_CACHE=1` for the test.
- **NEFF size diff is not a perf signal by itself.** Smaller NEFFs can be slower
  (less in-NEFF specialization → more runtime fallback) and bigger ones can be
  faster (kernels inlined).
- **`hardware_flops` should be identical** for the same HLO. If it isn't,
  there's a bug — check `transpose_flops` separately; some passes can fold
  transposes which legitimately reduces hw_flops.
