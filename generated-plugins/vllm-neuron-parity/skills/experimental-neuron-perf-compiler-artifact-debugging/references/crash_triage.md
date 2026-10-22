# Triaging a compiler internal error

When the compiler aborts with `[INTERNAL_ERROR]` (e.g. `NCC_IMPR901`,
`NCC_EVRF*`, MaskPropagation/LoopFusion assert, etc.), the goal is to:

1. Identify which **subgraph (sg)** crashed.
2. Identify which **pass** crashed.
3. Identify the **HLO op** the crashing IR represents.
4. (Optional) Reduce to a minimal repro.

## Step 1 — locate the failing artifact

The crash leaves a partial cache dir at `compile_cache/<hash>/dev0_<N>.rank0/`
with `graph.hlo`, `command.txt`, and `log-neuron-cc.txt`. Stage to a fresh
location:

```bash
FAILED_HASH=<hash>
DBG=/mnt/data/penguin_dbg_${FAILED_HASH}
mkdir -p $DBG
cp <compile_cache>/$FAILED_HASH/dev0_*.rank0/graph.hlo $DBG/
```

Native route (default since vllm-neuron 0.29): the crash leaves nothing in `compile_cache/`. Rerun the failing compile with `TORCH_NEURONX_PRESERVE_COMPILATION_ARTIFACTS=1` and `TORCH_NEURONX_DEBUG_DIR` set to a fresh dir you own; the kept `neuronx-cc/<hash>/` holds `module.mlir` (the StableHLO compiler input) and `log-neuron-cc.txt`. Stage `module.mlir` instead of `graph.hlo` and recompile it with every flag from the kept `compiler_args.txt` ([compile_route.md](compile_route.md)); the penguin steps below are the same.

## Step 2 — re-run with penguin debug

**Lift the flags from `command.txt`** — do NOT hand-write the compile
invocation. The framework passes options the user can't easily guess
(`--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=10
--experimental-unsafe-fp8e4m3fn-as-fp8e4m3` for FP8 KV cache models, etc.).
Dropping them produces a *different* failure (`NCC_EVRF051: F8E4M3FN not
supported`, `NCC_EARG002: unrecognized argument`) that masks the real bug.

```bash
cat <compile_cache>/$FAILED_HASH/dev0_*.rank0/command.txt
```

Copy every flag from `command.txt` *except* the input/output/logfile paths,
preserving shell quoting on `--internal-hlo2tensorizer-options=...` (it's
one argument with embedded spaces — splitting it breaks the flag parser).
Then append `--internal-compiler-debug-mode=penguin`:

```bash
cd $DBG
# Flags below come from command.txt — adapt them to YOUR run:
neuronx-cc compile graph.hlo \
    --framework XLA --target trn2 \
    --output graph.neff \
    --logfile log-neuron-cc.txt \
    --auto-cast=none --verbose=35 -O1 \
    '--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=10 --experimental-unsafe-fp8e4m3fn-as-fp8e4m3' \
    --internal-backend-options=--enable-verifier=false \
    --internal-compiler-debug-mode=penguin
```

Will crash again. Penguin output up to the crash point is preserved.

## Step 3 — identify the crashing sg

Look at which `debug_info_penguin.dbg_sgNNNN` files **didn't** get written:

```bash
ls $DBG/debug_info_penguin*    # list those that succeeded
ls -d $DBG/penguin-sg*         # list all sgs
# The sg dir without a matching debug_info_penguin.* is the crashing one.
```

Cross-check by searching the log for the assert and which sg was running:

```bash
grep -B30 "INTERNAL_ERROR\|Assertion failed" log-neuron-cc.txt \
    | grep -oE "sg[0-9]+/Tensorizer/[A-Za-z]+" | tail -5
```

## Step 4 — pinpoint the failing pass

Each pass commits its IR into the sg's git repo. The list of completed passes
is `git log` in the sg dir; the failing pass is the **next one after HEAD**.

```bash
cd $DBG/penguin-sgNNNN              # the crashing sg
git log --oneline | head -5         # most recent successful passes
```

The pass that **was running but didn't commit** is the one in the log just
after the last `git log` head:

```bash
LAST_OK=$(cd $DBG/penguin-sgNNNN && git log --oneline -1 --format='%s')
echo "Last successful pass: $LAST_OK"
grep -A2 "sg<NNNN>/Tensorizer/$LAST_OK]: .*finished" log-neuron-cc.txt | tail -5
# The next "Running <X>" line is the crashing pass.
```

## Step 5 — diff against a known-good compile

If the same HLO compiled in a previous compiler version, run penguin compile
with that compiler in a sibling dir:

```bash
DBG_OK=/mnt/data/penguin_dbg_${FAILED_HASH}_oldcc
mkdir -p $DBG_OK && cp $DBG/graph.hlo $DBG_OK/
# (install the working compiler version here)
cd $DBG_OK && neuronx-cc compile graph.hlo ... --internal-compiler-debug-mode=penguin
```

The interesting comparison is the **IR commit just before the crashing pass**
in both. In OK, that's `git show <prev_pass>:penguin.py`; in FAIL, it's the
current `penguin.py` (since FAIL didn't commit the crashing pass output).

```bash
# Extract pre-pass IR snapshots
cd $DBG/penguin-sgNNNN
PREV_OK=$(cd $DBG_OK/penguin-sgNNNN && git log --oneline | grep -B1 "<crashing_pass_name>" | head -1 | awk '{print $1}')
git show HEAD:penguin.py > /tmp/fail_pre.py
(cd $DBG_OK/penguin-sgNNNN && git show $PREV_OK:penguin.py) > /tmp/ok_pre.py

# Normalize ids/parents for diff
sed -E 's/id=[0-9]+/id=N/g; s/parent=v[0-9]+/parent=vN/g; s/v[0-9]+ = /vN = /g' /tmp/fail_pre.py > /tmp/fail_norm.py
sed -E 's/id=[0-9]+/id=N/g; s/parent=v[0-9]+/parent=vN/g; s/v[0-9]+ = /vN = /g' /tmp/ok_pre.py > /tmp/ok_norm.py
diff -u /tmp/ok_norm.py /tmp/fail_norm.py | less
```

Look for differences in:
- Loop nest structure (new compiler often fuses or splits Loop nodes)
- Tensor shape changes (e.g. `(64, 2, 256)` → `(64, 256)` from dim collapse)
- Op category counts (`grep -oE 'm[0-9]+\.\w+'`)

## Step 6 — map the IR back to model code

Take a `tensor_op_name` from the diverging IR region, e.g. `_cosine.4502`,
and trace it. See [source_map.md](source_map.md).

## Step 7 — package for support

```bash
cd $(dirname $DBG)
tar czf ${FAILED_HASH}_repro.tar.gz $(basename $DBG)/
```

Include in ticket:
- `graph.hlo` (the failing input)
- `command.txt` (exact compile invocation)
- `log-neuron-cc.txt` (assertion + stack)
- `penguin-sgNNNN/penguin.py` for both crashing sg AND a working sibling sg
- One-paragraph note: which sg, which pass, which framework op (HLO id) is
  on the diverging IR line

## Common assertions and meanings

| Assertion | Likely cause |
|-----------|--------------|
| `NCC_IMPR901: Need to split to perfect loopnest` | An earlier pass (LoopFusion / Delinearization) produced an imperfect loop nest; MaskPropagation can't handle it. Look for collapsed dim or fused producers in pre-pass IR. |
| `NCC_EVRF*` (verifier) | Compile pass produced ill-typed IR. Disable verifier with `--internal-backend-options=--enable-verifier=false` to confirm the assertion is purely defensive. |
| `Assertion failed: False` (generic) | Concurrent-futures wrapper of an inner assert — search log above for the real assertion. |
| `MemoryAllocation*` | SBUF spill / live-range overflow — look at `LiveRangeReport_PostHloPart.txt`. |

## Documented `NCC_E*` codes — check first

Before treating an `[INTERNAL_ERROR]` as a compiler bug, look up the code in
[error_codes.md](error_codes.md). Codes like `EOOM001` (HBM exceeded) or
`EVRF007` (instruction count exceeded) signal a **budget** breach with a
documented user-side fix (reduce batch, add TP/PP) — no ticket needed.

A ticket is appropriate when:
1. The assertion has **no** `NCC_E*` code (e.g. `NCC_IMPR901`, `Assertion failed: False`).
2. The code has a documented action but applying it breaks the model.
3. The same HLO compiles fine on a previous compiler version.

## Layer-count bisection (when crash depends on model size)

If a 4-layer reduction works and 60-layer crashes, bisect with intermediate
layer counts:

```bash
for L in 4 6 8 16 32; do
    HF_OVERRIDES="{\"text_config\": {\"num_hidden_layers\": $L}}" \
      <bench script> 2>&1 | grep -E "Throughput|MaskPropagation|INTERNAL_ERROR" | head -2
done
```

The crash threshold often points at the offending layer type (e.g. global vs
sliding attention in Gemma4) — only one layer of the offending type may be
needed to trigger.
