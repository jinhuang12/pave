# Porting bring-up: end-to-end compiler-debug workflow

The per-stage narrative behind the decision tree in `SKILL.md`. Each section is the
"what to check, how to check, how to fix" for one stage. **Resolve the earliest
failing stage first.** New error codes referenced here are defined in
[port_error_codes.md](port_error_codes.md); dtype/layout and NKI detail live in
[dtype_layout_legalization.md](dtype_layout_legalization.md) and
[nki_kernel_bringup.md](nki_kernel_bringup.md).

Ground truth for every stage: **read the real `NCC_` code in the per-rank
`log-neuron-cc.txt` before acting** (SKILL.md cardinal rule). Then stage the failing
`graph.hlo`, lift flags from `command.txt`, and recompile with
`--internal-compiler-debug-mode=penguin` exactly as the perf skill's Quick Start and
[`crash_triage.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/crash_triage.md)
describe.

---

## §0 — Environment: is the code you edited the code that runs?

Before trusting any compiler signal, confirm running-code == edited-code. During a
port you register new HF configs / model archs across git worktrees; an editable-install
finder pointing at the wrong tree yields phantom "unrecognized config" failures and edits
with no effect.

- Symptom: model won't register/load, or fixes have no effect.
- Check: `python -c "import vllm_neuron; print(vllm_neuron.__file__)"` — confirm the path.
- Fix: dedicated worktree + hardlink-cloned venv (`cp -al`), repoint the editable finder.
- Also point the run at a fresh compile-cache root you own and stop orphan workers your
  own job started, by pid, first — never clear a shared cache (the plugin's P2); see
  [cache_and_iteration_hygiene.md](cache_and_iteration_hygiene.md). Exclude this
  before compiler debugging can be trusted.

---

## §1 — Does it TRACE? (Dynamo, before any HLO)

The graph must be *captured* before it can be lowered. Bring-up code adapted from
HF / NxDI tracing implementations routinely breaks `fullgraph=True` capture. These are
all **invisible in CPU eager** — they manifest only when the fresh architecture is
lowered on-device.

| Signal | Root cause | Fix |
|--------|-----------|-----|
| Native **segfault** inside `gm(*xla_placeholders)` in `convert_fx_to_hlo` (`vllm_neuron/compile/hlo.py`) | `.item()` or data-dependent slice (`block_table[0, :num_prior_blocks]`, `cached_seq_len.reshape([]).to(int64)`) forces materialization on placeholder XLA tensors → `ExecuteComputation` on device-less placeholders | Fixed-size gather over a **compile-time-constant** bound (`max_prior_blocks = block_table.shape[1]`) + mask invalid positions with tensor arithmetic; no `.item()`, no data-dependent shapes |
| `torch._dynamo.exc.Unsupported: Dynamic slicing with Tensor arguments` | A tensor value used as a slice bound (variable-name collision reusing an int name for a tensor accumulator) | Rename so the slice bound is the static int again |
| `range(num_chunks)` untraceable | Loop trip count derived from symbolic seq_len (SymInt) | Statically resolve / hardcode the chunk count for the prefill bucket |
| `Error while lowering: xla::unselect ... rank of the operand and the padding configuration do not match ... FATAL` | In-place slice/index write inside a traced recurrence (`attn[...,i,:i]=...`, `core_attn_out[:,:,i]=...`), ported from an NxDI impl that tolerated in-place writes | Rewrite **fully out-of-place** (e.g. Neumann-series inverse via repeated squaring; collect per-chunk outputs in a list then `torch.stack`) |
| Recompile guard `input_ids size mismatch expected 4096 actual 1` | `VLLM_NEURON_SKIP_DECODE_WARMUP=1` skipped decode-graph compile | Enable decode warmup so the `(1, ctx)` decode graph compiles alongside prefill |
| Trace-time `AttributeError` (e.g. `self.dtype`) | A new code path reads an attribute the old path never set; `py_compile` won't catch it | Assign the attribute (`self.dtype = config.torch_dtype`) |

**Localize a segfault with FX-node bisection:** subclass
`torch.fx.interpreter.Interpreter`, log every Nth `run_node` (op/target/name) to a
*flushed* file, run a 1-layer model, find the last node before the crash, then increase
granularity around it. Confirm by forcing single-shot prefill
(`max_model_len == max_num_batched_tokens`). Revert the instrumentation afterward.

```python
# temporary, inside vllm_neuron/compile/hlo.py convert_fx_to_hlo:
class _DebugInterp(torch.fx.interpreter.Interpreter):
    _n = 0
    def run_node(self, n):
        _DebugInterp._n += 1
        with open(f"/tmp/hlo_debug_{os.getpid()}.log", "a") as f:
            f.write(f"N{_DebugInterp._n} op={n.op} tgt={n.target} name={n.name}\n")
        return super().run_node(n)
# with torch_neuronx.contexts.lowering(): _DebugInterp(gm).run(*xla_placeholders)
```

---

## §2 — Does it LOWER at all? (torch_xla → HLO / neuronx-cc front-end)

The op must have a lowering path and legal dtypes.

### Unsupported ops — the `list-operators` triage flow

When the log says an op does not lower (`NCC_IBTN006`, or the perf table's
`EUOC002`/`EVRF001`), first list what the target actually supports, then decide
hoist-vs-replace:

```bash
neuronx-cc list-operators --framework XLA          # canonical supported-op list
neuronx-cc list-operators --framework XLA | grep -i sort   # e.g. is HLO sort present?
grep -a -c NCC_IBTN006 <log-neuron-cc.txt>          # tally how many ops don't lower
```

- **`NCC_IBTN006` — op has no lowering** (seen 33× on GDN index/gather/weight-build).
  **Hoist the op host-side** (run `index_select` / weight-builds outside the compiled
  region; precompute one-hots/indices) or replace with a lowerable equivalent.
- **`NCC_EVRF029` — HLO sort unsupported on trn2 → `torch.topk` fails/unreliable.**
  Reimplement top-k as an iterative argmax + `torch.full_like(-inf)` masking loop:
  `for _ in range(k): idx = scores.argmax(-1, keepdim=True); ...; scores.scatter_(-1, idx, neg)`.
  (Distinct from the perf table's `EVRF013` int-topk.)
- **`NCC_ISMP902` — `_pad` Simplifier/Pelican ICE.** *Any* op that lowers to XLA `pad`
  trips it (`F.pad`, `torch.cat` of constant-zeros, `nn.Conv1d(padding=)`, edge
  slice-assign `x[...,-1:]=v` / `x[...,0]=v`, clamp-index+gather). Full elimination
  recipe + HLO-proto localization in [dtype_layout_legalization.md](dtype_layout_legalization.md).

### dtype legalization — the invisible one

`NCC_ESPP004 f64 dtype is not supported` comes from bare Python float literals and
inf-producing ops, is **INVISIBLE in `fxgraph.txt` / `make_fx` / CPU-compile**, and
only materializes at HLO lowering. Full workflow (HLO-proto scan, whole-file audit,
typed-tensor fix, the `x*x` vs `.square()` trap) is in
[dtype_layout_legalization.md](dtype_layout_legalization.md).

---

## §3 — KERNEL CONTRACT + DOES IT FIT?

The op lowers, but its access pattern, kernel contract, or memory footprint is rejected.
Stride/layout (`ITIN902`, `IBCG901`) → [dtype_layout_legalization.md](dtype_layout_legalization.md).
NKI kernel contract (`INKI016` head_dim>128, TKG-vs-CTE, FP8, buckets), SBUF
(`IGCA037`), the 8-activation-LUT ceiling (`INLA001`), and compile-time HBM
(`EOOM002`/`EXSP001`, incl. the head_dim>128-turns-KV-into-graph-I/O trap) →
[nki_kernel_bringup.md](nki_kernel_bringup.md) and [port_error_codes.md](port_error_codes.md).

---

## §4 — ISOLATE THE NEW LAYER TYPE (good-vs-bad fxgraph diff)

When only some EP/TP workers fail, cluster then diff (or run
`scripts/classify_compile_failure.sh`):

```bash
# 1) Cluster: which graphs produced a NEFF vs not, and their FX size
for d in <compile_cache>/*/; do name=$(basename $d); \
  neff=$(ls $d*/*.neff 2>/dev/null | wc -l); fx=$(wc -l < $d/*/fxgraph.txt 2>/dev/null); \
  echo "$name neff=$neff fx_lines=$fx"; done | sort
# e.g. 8 graphs @16520 lines compiled, 56 @23410 failed (the MoE layers)

# 2) Diff op histograms of a good vs a bad graph
grep -oP '= call_function\[target=\K[^\]]+' $GOOD/fxgraph.txt | sort | uniq -c | sort -rn > /tmp/good.hist
grep -oP '= call_function\[target=\K[^\]]+' $BAD/fxgraph.txt  | sort | uniq -c | sort -rn > /tmp/bad.hist
diff /tmp/good.hist /tmp/bad.hist
# ops unique to BAD (einsum, silu, softplus, zeros_like/setitem, deep getitem/mul chains)
# = the offending new layer type
```

Map the offending feature to its likely compiler stressor in
[arch_feature_risk.md](arch_feature_risk.md).

---

## §5 — MINIMAL REPRO (layer-count bisection + identity stub)

Use the perf skill's
[`crash_triage.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/crash_triage.md)
layer-count bisection (`scripts/layer_bisect.sh` wraps it). Two port-specific rules:

- **4 layers can fail where 6 compiles** (ISCH719 EP-collective/extra-output topology).
  Use **6 reduced layers, not 4** for a bring-up smoke config.
- **Stub the suspect layer to identity.** If `_pad` / the error *persists*, the culprit
  is **shared infra** (the sampler), not your layer. This overturned a wrong MoE
  hypothesis in two ports — the `_pad` came from `functional/sampling.py`.

---

## §6 — COMPARE vs a WORKING SIBLING

When you suspect a compiler bug rather than a model bug, diff against a known-good
sibling model's artifacts — subgraph counts, opcode histograms, `replica_groups`,
kernel-gating. Full recipe in [sibling_compare.md](sibling_compare.md).

---

## §7 — Compiles but WRONG OUTPUT / RUNTIME crash

The NEFF exists but the model is wrong or won't schedule. **These are the most
dangerous port bugs — invisible in single-request smoke tests.**

- **Garbled / rank-0-only text at batch>1** → distributed-argmax `AllGather` mis-opt:
  the compiler collapses the `AllGather+Slice+Concatenate` from `all_gather_tensor`
  with `gather_dim != 0` on a `[B,1]` tensor (B>1) so only rank 0's vocab shard
  contributes ("high level" not "high-level"). **Fix:** transpose `[B,1]→[1,B]`, gather
  on dim 0, transpose the `[32,B]` result back. Catch with an equivalence suite
  comparing output across shards. *True compiler bug, worked around by HLO-shape transform.*
- **`nrta status=101` "Failed to schedule neff execution"** — disambiguate three causes:
  1. **Dynamic output shape** — `torch.cat` of a variable-length list (new CSA 3-tuple
     return / `csa_packed`). XLA/NEFF requires static output shapes → fixed-size buffers /
     fixed-arity return.
  2. **Debug-flag NEFF inflation** — `XLA_IR_DEBUG` / `XLA_HLO_DEBUG` /
     `NEURON_FRAMEWORK_DEBUG` embed metadata that pushes a near-ceiling NEFF past HBM →
     remove them, bump the cache name, recompile lean.
  3. **`NEURON_RT_INSPECT_DEVICE_PROFILE=1`** reserves HBM capture buffers → disable
     during the run; profile compile-cache NEFFs separately.
- **`nrta status=5` / DGE out-of-bound** — `PAD_SLOT_ID=-1` → `-1//block_size` negative
  index into in-graph `index_put_`/`index_select` (surfaces only when a torch-SDPA
  fallback exposes paged cache access at graph level). `clamp(min=0)` stops the OOB but
  **corrupts block0 KV** (all padding writes to block0/pos0 → degenerate output). Use
  `torch.where(slot>=0, slot, garbage_slot)` with
  `garbage_slot=(k_cache.shape[0]-1)*block_size` (last, scheduler-unused block).
- **`Collectives barrier failure` / `NRT_NETWORK_PROXY_FAILURE` / `status=1206`** —
  runtime/fabric, **NOT a compiler bug**. Warmup passing does *not* prove a real request
  runs. Verify collective topology from the **compiled `replica_groups`**
  (`grep -aoE '\[\[[0-9, ]+\]\]' <run.out> | sort -u`), not Python group names
  ("narwhal" is the NEFF/model name, not a communicator). A CCOM bootstrap ceiling
  (world>64) is a runtime-team issue.

---

## §8 — "My fix didn't take" / compiler bug

Before believing a landed fix regressed, **recompile into a fresh cache root you own**
(never clear a shared cache, the plugin's P2) — the signature
port hazard, because only during a port does the *same fx-graph* get recompiled
repeatedly with *different HLO* as pad/f64/kernel fixes land. Full detail (literal-`$USER`
trap, `.done`-poisoned entries, NFS silent rejection, orphan kill) in
[cache_and_iteration_hygiene.md](cache_and_iteration_hygiene.md).

Genuine compiler bugs to work around, not fix in model code: `NCC_ISCH719`
(topological-order / CopyPredicated anti-dep cycle → **-O2**, accept the perf cost),
`NCC_INAS001` / bare `IGAA901` (topk internal error → restructure / remove dead code),
`NCC_ISMP902` `_pad` ICE, distributed-argmax `AllGather` mis-opt. See
[port_error_codes.md](port_error_codes.md).

---

## Quick-reference command index

```bash
# real NCC code (exit 70 is useless) — per-rank log, grep -a
grep -a -iE 'NCC_[IE][A-Z]+[0-9]+' <cache>/*/dev0_*.rank*/log-neuron-cc.txt | sort | uniq -c | sort -rn
# exact flags to lift for penguin recompile
cat <cache>/<hash>/dev0_*.rank0/command.txt
# cluster good-vs-bad graphs + tally codes (this skill's script)
bash scripts/classify_compile_failure.sh <cache> <run.out>
# f64 localization (invisible in fxgraph); pad ops; transcendental table count
python3 scripts/scan_hlo.py f64  graph.hlo
python3 scripts/scan_hlo.py pad  graph.hlo
python3 scripts/scan_hlo.py act  graph.hlo
# verify pow/square/x*x lowering (XLA route; native: grep the kept stablehlo for ' power'/' multiply')
python -c "import torch,torch_xla,torch_xla.core.xla_model as xm; d=xm.xla_device(); x=torch.randn(8,16,device=d); h=torch_xla._XLAC._get_xla_tensors_hlo([x.pow(2).sum()]); print('power',h.count(' power('),'multiply',h.count(' multiply('))"
# which subgraph holds a given NKI kernel (proves the kernel's own sg passed)
for sg in penguin-sg*/; do grep -q kv_cache_scatter "$sg/penguin.py" && echo "$sg HAS it"; done
# verify collective topology from compiled graph (not Python group names)
grep -aoE '\[\[[0-9, ]+\]\]' <run.out> | sort -u
# distinguish fresh compile vs stale cache hit
grep -aE 'cache hits|compiling|cache key|Falling back to /tmp' <run.out>
```
