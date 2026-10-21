---
name: experimental-neuron-autoport-compiler-debugging-vllm-neuron
description: |
  Debug BRINGUP / first-compile failures when porting a NEW model architecture to the
  vllm-neuron / torch-xla Trainium2 backend: a graph that will NOT trace, lower, verify,
  fit, schedule, or produce correct output — NOT tuning a model that already runs (that is
  experimental-neuron-perf-compiler-artifact-debugging). Covers the overloaded "compilation
  failed with 70" (read the per-rank log-neuron-cc.txt for the real NCC_ code), internal
  compiler errors, f64 literal legalization, the _pad Simplifier ICE, NKI kernel-contract
  failures (head_dim>128), good-vs-bad fxgraph clustering, layer-count bisection +
  single-layer repro, comparing against a working sibling model, and compile-cache
  poisoning. Reuses the perf skill's flag-lifting, penguin recompile, and crash_triage.
  Triggers: "porting", "bringup", "new model won't compile", "compilation failed with 70",
  "error 70", "layer-count bisection", "minimal repro", "unsupported op", "_pad ICE",
  "f64 not supported", "head_dim > 128", "MoE bringup", "stale NEFF".
---

# Debugging a New-Architecture Bring-up via Compiler Artifacts

You are porting an architecture the Neuron stack has **never seen**. Its novel op
patterns — MLA/CSA attention, MoE einsum experts + EP collectives, GatedDeltaNet
recurrence, short-conv mixers, Sinkhorn / hyper-connections, exotic head dims
(256/512), FP8 KV, muP scaling constants — will trip lowering, kernel-contract, and
resource assertions that no already-ported dense model ever exercises. This skill is
the ordered attack plan.

The top of this file gives the mental model, the one cardinal rule, and the decision
tree. Everything else — per-stage narrative, the new error codes, the architecture
risk table, the isolation techniques — lives in `references/`. **Pick the reference
from the decision tree; don't load them all.**

## This skill vs. the perf skill — know which one you're in

| | **Perf skill** (`experimental-neuron-perf-compiler-artifact-debugging`) | **This skill (port / bring-up)** |
|---|---|---|
| Precondition | A valid NEFF exists and **runs** | Nothing compiles / lowers / runs yet |
| Question | "Why is this valid graph slow / too big / redundant?" | "Why won't this op trace / lower / verify / fit / produce correct output?" |
| Primary artifact | NTFF profile parquet, penguin per-pass timing | `log-neuron-cc.txt` NCC_ code, `fxgraph.txt`, raw `graph.hlo` proto |
| Typical fix | kernel selection, DMA overlap, opt level | model-code rewrite, dtype typing, config/bucket/parallelism change |

**Everything the perf skill teaches about *reading* artifacts still applies** — the
5-stage pipeline, lifting flags from `command.txt`, recompiling with
`--internal-compiler-debug-mode=penguin`, and its
[`crash_triage.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/crash_triage.md),
[`hlo_convert.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/hlo_convert.md),
[`penguin_anatomy.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/penguin_anatomy.md),
[`source_map.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/source_map.md),
[`inspection_recipes.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/inspection_recipes.md),
[`error_codes.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/error_codes.md),
[`kv_cache_memory_model.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/kv_cache_memory_model.md),
[`kv_cache_budgeting.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/kv_cache_budgeting.md).
**Do not restate them; load them when a step below points you there.** This skill adds
the *porting-specific ordering, localization, new error codes, and workarounds* the
perf skill does not cover. Upstream, `neuron-framework-autoport-vllm-neuron` generates
the model and hands off to this skill when the smoke-test compile fails.

## THE cardinal rule: "error 70" is overloaded — never react to it

`neuronx-cc compilation failed with 70` (and the driver "error 70") in stdout / the
vLLM worker log tells you **nothing**. Exit code 70 multiplexes every failure class:

- internal compiler bug (`ITIN902`, `INAS001`, `ISCH719`, `ISMP902`)
- resource ceiling (`EOOM002`/`EXSP001` HBM, `IGCA037` SBUF, `INLA001` 8-LUT)
- kernel-contract violation (`INKI016`)
- dtype legalization (`ESPP004` f64)
- unlowerable op (`IBTN006`, `EVRF029`)

**Before any diagnosis, open the per-rank `log-neuron-cc.txt` and read the real
`NCC_*` code and its source location** (`grep -a`; the detail is often only in the
per-rank log, not stdout). A bare alphanumeric token in a log (`EXRJ671`, `narwhal`,
cache-hash fragments) is **not** a diagnostic — only `NCC_*`/`EXXX` codes emitted
*with a source location* are real.

```bash
# useless alone:
grep -a 'compilation failed with 70' <run.out>
# the real code (per-rank log), tallied:
grep -a -iE 'NCC_[IE][A-Z]+[0-9]+' <compile_cache>/*/dev0_*.rank*/log-neuron-cc.txt \
  | sort | uniq -c | sort -rn
```

`scripts/classify_compile_failure.sh` does both of the above plus clusters the cache.

Three hazards recur across *every* port, before you even trust a signal:
- **Latent bugs surface on cache-miss.** Editing the model changes the graph hash →
  fresh recompile → exposes latent bugs in *shared* infra (sampler `pad` ops) a stable
  model's cache-hit hid. The culprit is often not your code.
- **`make_fx` / CPU compile lie.** f64 weak-scalars and pad patterns are invisible in
  `fxgraph.txt`; they only materialize at HLO lowering on a real device. A clean CPU
  compile is **not** proof of device compilability.
- **The compile cache poisons silently** (see §8 / `cache_and_iteration_hygiene.md`).
  Recompile into a fresh cache root you own before believing any "regression"; never
  clear a shared compile cache (the plugin's P2).

## Attack order — the porting decision tree

Attack in pipeline order. **A failure at an early stage masks everything downstream,
so always resolve the earliest failing stage first.** Full per-stage narrative,
commands, and the localization playbook are in
[porting_workflow.md](references/porting_workflow.md); the new error codes are in
[port_error_codes.md](references/port_error_codes.md).

```
START: new architecture fails
   │
   ├─▶ (0) Does the venv even run the code you edited?  Editable-install / worktree
   │        pointing at the wrong tree → phantom "unrecognized config" / edits with no
   │        effect. Fresh cache root you own (never clear a shared one, P2); kill only your
   │        own pids. → §0, cache_and_iteration_hygiene.md
   │
   ├─▶ (1) Does it TRACE?  (Dynamo, before any HLO)  torch._dynamo.exc.Unsupported /
   │        native SEGFAULT in torch_xla lowering / recompile guard. Causes: .item(),
   │        data-dependent slice, Tensor used as slice bound, SymInt loop count,
   │        skipped decode warmup, xla::unselect in-place slice. → §1
   │
   ├─▶ (2) Does it LOWER at all?  (torch_xla → HLO, neuronx-cc front-end)
   │        │
   │        ├─ UNSUPPORTED OP: "op does not lower" (IBTN006), topk/sort (EVRF029),
   │        │    _pad Simplifier ICE (ISMP902). Triage via `neuronx-cc list-operators`;
   │        │    hoist op host-side / replace idiom. → §2, porting_workflow.md
   │        │
   │        └─ DTYPE / LAYOUT: f64 literals (ESPP004 — INVISIBLE in fxgraph.txt, parse
   │             the HLO proto), 4D stride chains (ITIN902), >4 stride nesting (IBCG901).
   │             Type every scalar; keep tensors ≤3D + .contiguous(). → dtype_layout_legalization.md
   │
   ├─▶ (3) KERNEL CONTRACT / DOES IT FIT?  (NKI validation + WalrusDriver allocator)
   │        INKI016 (head_dim>128, TKG-vs-CTE, FP8, bucket), IGCA037 (SBUF),
   │        INLA001 (8 activation-LUT slots), EOOM002/EXSP001 (compile-time HBM;
   │        head_dim>128 turns KV into graph I/O). → nki_kernel_bringup.md, port_error_codes.md
   │
   ├─▶ (4) ISOLATE THE NEW LAYER TYPE.  Only some EP/TP workers fail. Cluster the
   │        compile cache by NEFF-present vs fxgraph line count, then diff good-vs-bad
   │        fxgraph op histograms → the ops unique to the bad cluster name the feature.
   │        Cross-check the arch risk table. → §"localization", arch_feature_risk.md
   │
   ├─▶ (5) MINIMAL REPRO.  Layer-count bisection (4/6/8/16) + single-layer / identity
   │        stub. If the error persists with the suspect layer stubbed, the culprit is
   │        SHARED INFRA (sampler), not your layer. → §5 + crash_triage.md bisection
   │
   ├─▶ (6) COMPARE vs a WORKING SIBLING.  Diff subgraph counts, opcode histograms,
   │        replica_groups, and how a reference EP model gates a shared kernel. Confirms
   │        compiler-bug vs model-bug. → sibling_compare.md
   │
   ├─▶ (7) Compiles but WRONG OUTPUT / RUNTIME crash.  garbled text (distributed
   │        argmax), status=101 (dynamic output shape / debug-flag NEFF inflation),
   │        status=5 DGE OOB (PAD_SLOT_ID=-1), status=1206 fabric. → §7
   │
   └─▶ (8) COMPILER/TOOLING BUG or "fix didn't take".  Cache poisoning (HLO changed
            but fx cache key didn't), ISCH719, distributed-argmax mis-opt. Try -O2,
            restructure the op, recompile into a fresh cache root — then escalate a ticket. → §8, port_error_codes.md
```

## Two localization techniques that define porting

1. **Cluster the compile cache, then diff good-vs-bad `fxgraph.txt` op histograms.**
   In a big EP/TP run only *some* workers fail. Inventory each cache dir by NEFF-present
   vs `fxgraph.txt` line count to cluster passing vs failing graphs, then diff the
   `call_function[target=...]` histograms of a good vs a bad graph. The ops unique to the
   bad cluster (einsum, softplus, silu, zeros_like/setitem, deep getitem chains) **are**
   the offending new layer type. This is the porting analog of the perf skill's profile
   diff. `scripts/classify_compile_failure.sh` automates the clustering.

2. **Layer-count bisection + single-layer / identity-stub repro.** Reduce to 4/6/8/16
   layers to find the crash threshold (points at the offending layer type — note **4 can
   fail where 6 compiles** for ISCH719 EP-collective topologies); stub the suspect layer
   to identity to prove whether it is really the source — several times the real culprit
   was **shared infra** (the sampler's `pad` ops) exposed only because the port forced a
   fresh recompile. See `crash_triage.md`'s layer-count bisection and
   `scripts/layer_bisect.sh`.

## When to reach past the FX graph

- **`make_fx` / CPU compile lie about f64.** Parse `graph.hlo` proto directly for
  `element_type==12` (F64) — `scripts/scan_hlo.py f64`. See `dtype_layout_legalization.md`.
- **`.square()` / `.pow(2)` / `.pow(2.0)` all lower to HLO `power`; only `x*x` lowers to
  `multiply`.** Verify by counting HLO opcodes (`scripts/scan_hlo.py act`), never the
  Python source form. Matters for the 8-LUT `INLA001` ceiling.
- **Isolated `compile_nki` passes while the full-graph O1 verifier rejects (the "GDN
  trap").** Always validate a new NKI kernel against the whole-graph compile — see
  `nki_kernel_bringup.md`.

## When to escalate a compiler ticket

Follow `crash_triage.md`'s Step 7 packaging. In a port, escalate **only after** ruling
out: (a) it's your model code (§1–§3), (b) it's a resource ceiling with a documented
action (`port_error_codes.md`), (c) it's cache poisoning (§8). A genuine compiler bug is
confirmed when the *same HLO / byte-identical op set* fails at one opt level or batch
size and succeeds at another (`sibling_compare.md`). Carry the O2/-O1 perf tradeoff note
(O2 was 4.4× slower for one MoE model) into the ticket.
