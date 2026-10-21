---
name: experimental-neuron-perf-compiler-artifact-debugging
description: |
  Debug performance issues in framework-compiled (vllm-neuron / torch-xla / jax) Neuron
  workloads via compiler artifacts: HLO (proto/text), penguin IR, per-pass git history,
  and compile logs. Use for: redundant DMA / must-alias bugs, compiler-version diffs,
  internal compiler crashes (NCC_IMPR901, NCC_EVRF*, MaskPropagation), penguin↔HLO↔model
  source mapping, and KV cache memory / concurrency / framework↔compiler handshake
  (why HLO parameter count is not a memory budget; sizing num_gpu_blocks_override).
  Triggers: "TiledOffloadedMemCpy", "redundant KV copy", "compiler crashed on HLO",
  "compare compilers", "must-alias not honored", "hlo_convert", "penguin IR diff",
  "NCC_IMPR901", "NCC_EVRF", "KV concurrency cap", "num_gpu_blocks_override",
  "max_num_seqs throttled", "VLLM_NEURON_DISABLE_INPUT_DEDUP", "NCC_IXRO002",
  "Undefined SB Memloc", "deconcat", "all-PyTorch graph", "LNC=2 partitioner",
  "modular-flow-mac-threshold", "kernel_call_count: 0".
---

# Debugging Performance Issues via Compiler Artifacts

This skill turns penguin debug dumps and HLO text into actionable performance
fixes. The artifacts capture **what the compiler did**, while a runtime profile
captures **what the device did**. Together they let you attribute time to a
specific HLO op, lowering decision, or pass.

The top of this file gives you the mental model and the artifact-generation
commands. Everything else — how to read each artifact, which knobs to turn,
which workflow to run — lives in `references/`. **Pick the reference file
from the decision tree below; don't load all of them at once.**

## Read this first: the compilation pipeline

Most debugging confusion comes from mis-attributing a finding to the wrong
stage. Memorize this five-step pipeline before reaching for any specific
reference file:

```
  user model.py (PyTorch nn.Module)
        │
        ▼  (1) torch.compile + dynamo trace
  FX GraphModule  ─── one placeholder per UNIQUE runtime tensor
        │            (dedup by (storage_id, shape, stride, dtype) at
        │             vllm_neuron/compile/backend.py — N model attrs that
        │             alias the same buffer collapse to 1 placeholder)
        ▼  (2) vllm-neuron compile backend
  fxgraph.txt     ─── deduped FX graph dumped to disk next to graph.hlo
  example_inputs.txt   input fingerprints captured at trace time
        │
        ▼  (3) torch_xla lowering (FX → HLO)
  graph.hlo (proto)    ─── one HLO parameter per FX placeholder
                           input_output_alias = must-alias contract,
                           the ONLY parameter-aliasing annotation HLO has
        │
        ▼  (4) neuronx-cc Tensorizer (HLO → penguin IR partitions)
  penguin-sgNNNN/penguin.py  ─── one sg dir per partition,
                                 git-tracked one commit per pass
  debug_info_*.dbg_sgNNN
        │
        ▼  (5) WalrusDriver backend (penguin IR → BIR → NEFF)
  log-neuron-cc.txt    ─── PSUM/SB/DRAM allocator output,
                           "DRAM hwm after allocation: <bytes>",
                           kernel scheduling, spill counts
  graph.neff           ─── final binary loaded by NRT runtime
```

| Stage | Produces | Reference for details |
|-------|----------|-----------------------|
| (1) **dynamo trace** | FX placeholder per unique runtime tensor. Logs `Input dedup: removed N duplicate placeholder(s)` at `vllm_neuron/compile/backend.py:124`. | [kv_cache_memory_model.md](references/kv_cache_memory_model.md) for why N model attrs become 1 placeholder. |
| (2) **vllm-neuron backend** | `fxgraph.txt`, `example_inputs.txt`, `command.txt` next to `graph.hlo`. | Quick start below for `command.txt` flag-lifting. |
| (3) **torch_xla lowering** | `graph.hlo` (proto). | [hlo_convert.md](references/hlo_convert.md) to read it; [inspection_recipes.md](references/inspection_recipes.md) for one-liners. |
| (4) **Tensorizer** | `penguin-sg*/penguin.py`, `debug_info_*.dbg_sg*`. | [penguin_anatomy.md](references/penguin_anatomy.md) for what each file means; [crash_triage.md](references/crash_triage.md) for which sg crashed. |
| (5) **WalrusDriver** | Allocator output, kernel scheduling, BIR, NEFF. | [kv_cache_budgeting.md](references/kv_cache_budgeting.md) for `DRAM hwm` accounting. |

**The frontier between stages (1) and (3) is where most KV-cache and
memory-model confusion lives** — same `torch.Tensor` can appear N times in
the model attribute tree but become a single HLO parameter after dedup, and
HLO parameter count is *not* a memory budget. See
[references/kv_cache_memory_model.md](references/kv_cache_memory_model.md).

**The frontier between stages (3) and (5) is where most compile-bug and
performance debugging lives** — same HLO, different compilers, different
allocator/scheduler choices. The penguin sg git history makes that
transition observable.

## Prerequisites

| Tool | Source | Notes |
|------|--------|-------|
| `neuronx-cc` | venv (any 2.x release) | The compiler binary. |
| `hlo_convert` | venv (compiler-bundled) | At `<venv>/lib/python3.10/site-packages/neuronxcc/starfish/bin/hlo_convert` (not on PATH; some `0.0.x` dev wheels strip it — re-roll to a release tagged build). |
| `python -c "from neuronxcc.thirdparty_libs.xla.service import hlo_pb2"` | venv | Fallback for HLO proto inspection when `hlo_convert` is absent. See [inspection_recipes.md](references/inspection_recipes.md). |
| `git log --oneline` (in `penguin-sg*/` dirs) | nothing extra | Each pass is committed; bisecting passes is just `git log`. |

## Quick start: regenerate artifacts for a failing or regressed compile

For a workload that crashed or regressed:

1. Locate the failing/regressed graph's `graph.hlo` (in `compile_cache/<hash>/`
   or in a per-rank work dir like `dev0_15.rank0/`).
2. Stage it into a fresh dir under `/mnt/data/` (do **not** rerun in the cache;
   penguin output litters the cwd unless `cwd=compiler_workdir` is set).
3. **Lift the original compile flags from `command.txt`** (next subsection).
4. Compile with `--internal-compiler-debug-mode=penguin` to dump
   `penguin-sg*/` subdirs and `penguin.py.*` IR snapshots.
5. Pick a workflow from the Decision tree below and load the matching
   reference.

### Lift flags from `command.txt`

Every cache dir has a `command.txt` capturing the exact `neuronx-cc compile`
invocation the framework used. Read it and copy every flag *except* the input
path, output path, and logfile path:

```bash
cat <compile_cache>/<hash>/dev0_*.rank0/command.txt
# Example output:
#   /…/neuronx-cc compile /…/graph.hlo \
#     --framework XLA --target trn2 \
#     --output /…/graph_<hash>.neff \
#     --logfile /…/log-neuron-cc.txt \
#     --auto-cast=none --verbose=35 -O1 \
#     '--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=10 --experimental-unsafe-fp8e4m3fn-as-fp8e4m3' \
#     --internal-backend-options=--enable-verifier=false
```

**Quoting matters.** The `--internal-hlo2tensorizer-options=...` value is a
single string with embedded spaces; keep it inside one shell-quoted argument.
Splitting it produces `NCC_EARG002: Illegal argument(s) - the following
argument(s) are unrecognized: --experimental-unsafe-fp8e4m3fn-as-fp8e4m3`.

Common framework-emitted flags worth preserving:

| Flag | Why | Symptom if dropped |
|------|-----|--------------------|
| `--internal-hlo2tensorizer-options=…--experimental-unsafe-fp8e4m3fn-as-fp8e4m3` | FP8 KV cache models on trn1/trn2 | `NCC_EVRF051: Data type F8E4M3FN is not supported on TRN1/TRN2` |
| `--internal-hlo2tensorizer-options=…--modular-flow-mac-threshold=10` | Tunes when sub-graph fusion happens | Crash may move to a different pass / sg |
| `--auto-cast=none` | Inhibits implicit fp32→bf16 casts | Numerical drift between debug & prod runs |
| `--internal-backend-options=--enable-verifier=false` | Skips verifier pass (already disabled in prod) | Defensive verifier-only assertion that the prod run never sees |

### Recompile with penguin debug

```bash
DBG=/mnt/data/penguin_dbg_<run_name>
mkdir -p $DBG && cp <graph.hlo> $DBG/
cd $DBG
# Take the flags from command.txt — do NOT hand-write the list.
neuronx-cc compile graph.hlo \
    --framework XLA --target trn2 \
    --output graph.neff \
    --logfile log-neuron-cc.txt \
    --auto-cast=none --verbose=35 -O1 \
    '--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=10 --experimental-unsafe-fp8e4m3fn-as-fp8e4m3' \
    --internal-backend-options=--enable-verifier=false \
    --internal-compiler-debug-mode=penguin
```

Penguin output lands beside `graph.hlo`: `penguin-sg0000/`, `penguin-sg0001/`,
… (one per partition), plus per-sg `penguin.py.0000NN`, `debug_info_*.dbg_sgNNN`,
`all_metrics.csv`, `LiveRangeReport_PostHloPart.txt`. See
[penguin_anatomy.md](references/penguin_anatomy.md) for what each file
contains.

## Decision tree — pick a workflow

Once you have artifacts, pick the reference file matching your symptom.
Each reference is self-contained: it has commands, grep recipes, and the
specific signals to look for.

| Symptom | Workflow |
|---------|----------|
| Compile log has a documented `NCC_E*` code (e.g. `EOOM001`, `EVRF007`) | [error_codes.md](references/error_codes.md) — apply the user-side action first |
| Compiler **crashed** with internal error (`NCC_IMPR901` etc., or no code) | [crash_triage.md](references/crash_triage.md) |
| Same HLO, **different throughput** between two compilers | [compiler_diff.md](references/compiler_diff.md) |
| Suspect **redundant DMA / KV cache copy** | [dma_redundancy.md](references/dma_redundancy.md) |
| Need to **map penguin IR op → model.py** | [source_map.md](references/source_map.md) |
| Just want to **read the HLO** (proto → text, or proto-only fallback) | [hlo_convert.md](references/hlo_convert.md) |
| Want to **understand penguin file layout, IR conventions, dialects** (Legacy vs MLIR-affine) | [penguin_anatomy.md](references/penguin_anatomy.md) |
| Need a **one-liner for HLO/penguin** (count `must-alias`, find crashing sg, detect `TiledOffloadedMemCpy`, op-class histograms) — also covers common pitfalls | [inspection_recipes.md](references/inspection_recipes.md) |
| Reason about **why HBM looks the way it does** — three sharing layers, hybrid grouping, `must-alias` semantics, framework↔HLO handshake | [kv_cache_memory_model.md](references/kv_cache_memory_model.md) |
| Tune **KV cache concurrency / `num_gpu_blocks_override` / `max_num_seqs`** when throughput is throttled by the cap | [kv_cache_budgeting.md](references/kv_cache_budgeting.md) |

**Order:** if the log shows an `NCC_E*` code, check
[error_codes.md](references/error_codes.md) **first**. Most are fixable from
the user side (dtype, shape, parallelism config). Only escalate to
crash_triage / file a compiler ticket once the documented workarounds
don't apply.

## Do not accept a root-cause claim without primary evidence

If this skill's work is delegated to a subagent (or if you are reviewing another
agent's findings), a root-cause conclusion for a compiler error is only actionable
if it comes with the primary evidence, not just a narrative summary:

- The exact `command.txt` / compile invocation used.
- The exact `log-neuron-cc.txt` (or relevant excerpt) showing the failure.
- For claims of "compiles but produces wrong results" — the actual on-device
  equivalence output (cosine similarity number, not just "corruption suspected").
- For claims that a fix "requires new kernels" or "is not fixable by a flag" — proof
  that the flag-sweep in [error_codes.md](references/error_codes.md) /
  [crash_triage.md](references/crash_triage.md) (e.g. sweeping
  `--modular-flow-mac-threshold`, checking `--internal-backend-options`) was actually
  attempted on the real `graph.hlo`, not skipped in favor of a plausible-sounding
  theory.

A conclusion of "unfixable, needs new kernels / a compiler ticket" is exactly the
kind of high-cost claim (it redirects effort to a much larger, different task) that
must be re-verified against artifacts before acting on it. Re-run the check yourself
if the evidence isn't attached — do not build subsequent work on an unverified claim.

**For mystery `'NoneType' object is not callable` / `has no attribute` TypeErrors**
surfacing deep inside `torch.dynamo`/FakeTensorMode dispatch or similar layered
Python frameworks — these are frequently a silently-swallowed
`except ImportError: x = None` several frames away from the crash site, not a
genuinely missing dispatch handler. See
[silent_none_degradation.md](references/silent_none_degradation.md) for the
verification procedure and a worked example before accepting either "no handler
exists" or "needs new kernel code" as the fix.
