# Port bring-up error & crash taxonomy (new NCC codes)

The bring-up companion to the perf skill's
[`error_codes.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/error_codes.md),
which covers the public `NCC_E*` set (EARG/EUOC/EVRF*/ESPP047 fp8/EVRF056 gather/
EBIR023 mlp>4096/EOOM*/EBVF030) and only one internal code (`NCC_IMPR901`). **Every
code below is new** or carries a port-specific nuance the perf table lacks. Internal
`NCC_I*` codes generally mean an internal pass crash; unlike the perf table's "file a
ticket", most have a **model-code workaround** during a port.

Read the real code from the per-rank `log-neuron-cc.txt` first (SKILL.md cardinal rule).
Each row: code → stage → trigger → action → fix class.

| Code | Stage | Trigger | Action | Class |
|------|-------|---------|--------|-------|
| **`NCC_ITIN902`** | Tensorizer TensorInitialization/DotTransform | Non-contiguous 4D reshape/transpose/reduction stride chains (MoE einsum, per-head ops). Also **`AffineIV`**: in-graph square `[B,S]` one-hot via `zeros().scatter_` fails at **bs≥4** (compiles at small batch). | Flatten 4D→3D/2D + `.contiguous()`; precompute one-hot/index host-side (`index_select`/`index_copy`); explicit per-expert matmul loop not einsum; or hide op in an NKI kernel. | model-fixable |
| **`NCC_IBCG901`** | Tensorizer BIRCodeGenLoop | "Too many strides" — >4 (5-level) stride nesting on small tensors (`[64,4,4]`): Sinkhorn row/col normalize, 4D divides/RoPE (splitting RoPE across full head_dim=512), reduce+broadcast+divide, in-place `setitem`. | Keep tensors ≤3D + `.contiguous()`; sum+divide→softmax; `setitem`→`torch.cat` of contiguous halves; split RoPE onto the last `rope_dim` (64) only; avoid `torch.diag_embed` under compile. ⚠️ Sometimes appears as log **noise** (44×/87×, no failure) — correlate with an actual `failed`/`exit 70` line. | model-fixable |
| **`NCC_ISMP902`** | Tensorizer Simplifier (Pelican) | "Transformation error on operator: `_pad`" → `RAUW failed ... useEmpty()` ICE. *Any* op lowering to XLA `pad`: `F.pad`, `torch.cat` of zeros, `nn.Conv1d(padding=)`, edge slice-assign `x[...,-1:]=v` / `x[...,0]=v`, clamp-index+gather. | Eliminate all pad patterns: arange one-hot + `torch.where`; topk+gather→one-hot select+reduce-sum; `F.pad`→`torch.cat`-of-zeros. Localize via `scripts/scan_hlo.py pad` + penguin sg dumps + identity stubs. | **compiler bug** (workaround in model/shared code) |
| **`NCC_ESPP004`** | torch_xla→HLO (`StableHLOToPythonPrinter.cc:824`) | *In perf table as generic dtype.* **Port nuance:** bare Python float literals, `isfinite`/`isinf` (+inf), `masked_fill(float)`, weak `full_like` → f64 HLO constants **INVISIBLE in `fxgraph.txt`/`make_fx`** (weak-scalar dtype preserved); only materialize on ~7-min device recompile. Seen 60–80×. | `scripts/scan_hlo.py f64` → source lines; wrap **every** literal `torch.full((),v,dtype=w.dtype)`; inf→`-1e30`; audit whole file at once. See dtype_layout_legalization.md. | model-fixable |
| **`NCC_INLA001`** | (a) WalrusDriver `lower_act.cpp:348`; (b) tensorizer BIR verifier | **Two failures share this code.** (a) ">8 activation tables" — hardware has exactly 8 transcendental-LUT slots; DSv4 mix `exp,log1p,logistic,rsqrt,sqrt,cos,sin,power` = 8; **batch-dependent** (B=64 fails, B=32 passes, identical op set). (b) "Invalid access of N partitions starting at partition K, Offset:128" — illegal partition-axis PSUM read from an NKI matmul fold. | (a) `scripts/scan_hlo.py act`; drop one table via **`x*x` NOT `.square()`/`.pow(2)`** (all lower to `power`); drop dead scoring; `relu`→comparison+mux; avoid 4D. (b) PSUM/consumer reads start at partition 0; revert the fold. ⚠️ **Validate against the FULL-graph O1 compile — isolated `compile_nki` passes ("GDN trap").** | model/framework-fixable |
| **`NCC_IGCA037`** | tensorizer / WalrusDriver codegen | "... too big for SB, requires N bytes" — SBUF overflow inside an NKI kernel. **262144 B = 256KB = 64K f32** threshold recurs (compressor preloaded `(128,4096)`; Gemma4 decode kernel packed `[512,512]` at head_dim=512). Reports NKI Source Kernel file:line. | Stream per-tile via `nisa.dma_copy` inside the loop; `nl.affine_range`→`nl.sequential_range`; smaller d-tiles; or route offending layers to torch SDPA. | model-fixable (kernel authoring/selection) |
| **`NCC_INKI016`** | dynamo→tensorizer NKI validation | Kernel-validation family: `head_dim > 128` (Gemma4 256/512, Qwen3.5 256); "Gate projection clamp only supported in TKG mode"; "output_len must be divisible by f_len" (non-power-of-2 prefill bucket 384); FP8 KV prior/active dtype mismatch. | Newer nkilib + bump `MAX_HEAD_DIM` in **both** `attention_cte.py` & `attention_segmented_cte.py` (or manual matmul attention / d256 kernel); force `ComputationMode.DECODE/TKG`; power-of-2 buckets; cast actives to `float8_e4m3`. Beware silent torch fallback masking a disengaged kernel. See nki_kernel_bringup.md. | model/config-fixable |
| **`NCC_IGCA090`** | tensorizer NKI (MoE) | `skip_store_router_logits=True` unsupported in `moe_block_tkg` (surfaces wrapped as an `INKI016`). | Keep `skip_store_router_logits=False`. | config-fixable |
| **`NCC_IBTN006`** | backend | Op has no neuronx-cc lowering path (33× on GDN index/gather/weight-build). | Hoist the op out of the compiled graph (host-side compute) or replace with a lowerable equivalent. `neuronx-cc list-operators --framework XLA` to confirm support. | model-fixable |
| **`NCC_ISCH719`** | WalrusDriver SubgraphForkPass.post_sched (`sched.cpp:359`) | "topological order violations" — scheduler cycle. Triggers: **any scalar multiply** in a large graph (`cos*scalar` attention_scaling, ~9459 HLO insts); an NKI kernel with `operand_output_aliases` repartitioning 1→14 sgs (a `CopyPredicated` + its predicate split across an O1 `--partition` boundary → unbreakable anti-dep cycle, crash lands in *unrelated* MoE sgs 5/9); certain EP-collective/extra-output topologies at **4 layers**. | Kill scalar multiplies (fold into YaRN freq / apply post-RoPE on tensor dims); remove extra graph outputs (`csa_stacked`); use **6 reduced layers not 4**; **compile at -O2** (`NEURON_CC_OPTLEVEL=2`; `mac-threshold` & `--enable-anti-dependence-reduction` do NOT help). ⚠️ O2 was **4.4× slower** for DSv4 MoE. `--retry_failed_compilation` doesn't recover. | **compiler bug** (workaround only) |
| **`NCC_INAS001`** | tensorizer | Internal error **wrapping a bogus nested code** ("error code IGAA901 does not exist"). Batch/shape-dependent; from `torch.topk` in decode (MoE routing) + an NKI HC Sinkhorn kernel. | Single `scatter_` with full `[N,K]` index (not looped); remove/replace the NKI kernel; check if the op is even reachable (indexer topk was dead code). | **compiler bug** (workaround) |
| **`IGAA901`** (bare) | tensorizer | Compiler error `torch.topk` triggers in the DSv4 indexer path (may appear inside `INAS001`). | Replace `torch.topk` with iterative argmax + `-inf` mask. Treat any topk/sort on trn2 as suspect (see `EVRF029`). | **compiler bug** (workaround) |
| **`NCC_EVRF029`** | torch_xla→tensorizer | "HLO sort unsupported" on trn2 — `torch.topk` lowers to `sort` (new MoE sigmoid router top-k). Distinct from perf table's `EVRF013` int-topk. | Iterative argmax + `torch.full_like(-inf)` scatter-masking loop. | model-fixable |
| **`NCC_EOOM002` / `NCC_EXSP001`** | WalrusDriver DRAM_Allocator | *In perf table as generic HBM.* **Port nuance:** the dominant term is often **I/O double-counting** — head_dim>128 forces a torch-SDPA fallback making every layer's K & V graph I/O (read `index_select` + write `index_put_` = counted twice, ≈42GB); split-KV declares K/V independently (27.38GB). Compile-time-only; device residency is fine. | Read the I/O-vs-scratchpad breakdown line; **SWA-skip** when `chunk_size≥sliding_window`; `VLLM_NEURON_KV_GMU_BUDGET_CAP_FRACTION` (~0.12 Gemma4-31B; 0.05 + `VLLM_NEURON_MIN_KV_BUDGET_GIB=0.1` MoE); `KVCacheTensor.shared_by` to alias split KV; power-of-2 prefill chunk. See nki_kernel_bringup.md + perf skill's kv_cache_*.md. | model/config-fixable |
| **`NCC_ITEN404`** | dynamo/config tiling | Context bucket size 256 trips the tiling path (Qwen3.5 known issue). | Avoid context/kv bucket 256; pick a bucket that doesn't hit ITEN. | config-fixable |
| **`NCC_IBIR297`** | tensorizer BIR (custom d256 attn) | Both SBUF `tensor_tensor` operands must share the same partition base/start. | Allocate RoPE/tensor_tensor workspace buffers at the same partition start (row 0). | model-fixable (kernel authoring) |
| **`NCC_ISCH715`** | tensorizer scheduler (custom d256 attn) | PSUM accumulation conflict when PSUM allocations are reused across GQA iterations. | Allocate PSUM per-GQA-iteration (compiler-managed) instead of reusing. | model-fixable (kernel authoring) |
| **`NCC_EMOD019`** | hlo2penguin front-end (`main.cc:658`) | "Input file not found: graph.hlo". | Run from the dir containing `graph.hlo` or pass an absolute path. | config-fixable (invocation) |
| **`NCC_EOCP001`** | tensorizer fp8 | `--experimental-unsafe-fp8e4m3fn-as-fp8e4m3` on trn3+ (native OCP) conflicts with NKI kernels emitting OCP fp8. | Gate that flag to **trn2 only** (`backend.py:696-699`); omit on trn3+. | config-fixable |

## Symptom-class entry points (when no op is named)

During a port the code is often hidden behind a bare `70`, a `WorkerProc` exception, or
a segfault. Start at what you *saw*:

- **Worker prints `compilation failed with 70`, no op** — only *new-feature* graphs
  fail while dense baselines compile. Do the good-vs-bad fxgraph diff (porting_workflow
  §4); the ops unique to the bad cluster name the feature; open that dir's `command.txt`
  + `log-neuron-cc.txt` for the real code.
- **`WorkerProc hit an exception` / `Parallel trace fork failed`** — NKI OOB at compile:
  `Out-of-bound access for tensor 'unnamed' on dimension 0: index range [0,127] exceed
  dimension size of 16`. A shared MoE kernel tiles the token dim in 128-row tiles; a
  tiny cross-DP EP decode batch (DP16×bs1 = 16 tokens) under-fills tile 0. **Zero-pad
  the token dim to a multiple of `block_size` (256)** with `torch.cat`-of-zeros (pad rows
  carry zero affinity, numerically inert), run, slice `[:T]`.
- **Segfault, no Python error** — data-dependent slice / `.item()`; porting_workflow §1.

## The port triage verdict

**True compiler bugs (workaround only; escalate per crash_triage.md Step 7):**
`ISCH719` (→ -O2, accept perf cost), `INAS001`/bare `IGAA901` (→ restructure top-k),
`ISMP902` `_pad` ICE (→ eliminate pad patterns), distributed-argmax `AllGather`
non-zero-dim mis-opt (→ transpose-before-gather), `INLA001`(b) isolated-vs-full-graph
divergence (verifier gap).

**True tooling/runtime bugs (not model, not neuronx-cc lowering):** NKI cache poisoning
(`.done` on failed compiles), NFS cache URL silent rejection + `$USER`-unexpanded path,
framework alias-propagation gap dropping in-place NKI KV writes, `SbufManager(use_auto_alloc=True)`
silently spilling to HBM, CCOM cross-node bootstrap hang world>64, offline
`LLM(optimization_level=O2)` silently lowering to O1.

**Everything else is model/config-fixable** — the overwhelming majority. Recurring levers,
in priority order: (1) keep tensors ≤3D + `.contiguous()`, flatten 4D; (2) wrap every
scalar literal, use `x*x` not `.square()`; (3) eliminate data-dependent shapes/`.item()`;
(4) precompute one-hots/indices host-side, hoist unlowerable ops off-graph; (5) pick the
right kernel for the arch (head_dim, TKG-vs-CTE, pad token dim, power-of-2 buckets);
(6) config budget knobs (`KV_GMU_BUDGET_CAP_FRACTION`, SWA-skip, split-KV `shared_by`,
timeouts); (7) iteration hygiene every loop.
