# NKI kernel & memory-fit bring-up (contract, SBUF, head_dim, the GDN trap)

Covers the stage-3/4/5 walls a fresh architecture hits once it lowers: NKI
kernel-validation (`INKI016`), SBUF overflow (`IGCA037`), the 8-activation-LUT ceiling
(`INLA001`), compile-time HBM (`EOOM002`/`EXSP001`), and NKI slice legality. Codes are
tabulated in [port_error_codes.md](port_error_codes.md); this file is the how-to.

---

## head_dim > 128 — the master gotcha (`NCC_INKI016`)

Stock CTE/TKG/segmented-attention kernels cap `MAX_HEAD_DIM=128`. Gemma4 (256 SWA / 512
global) and Qwen3.5 full-attn (256) exceed it. `NCC_INKI016 we do not support head_dim >
128, got head dim 256`.

**Two traps:**
1. The cap lives in the **installed nkilib VALIDATOR**, not the source you're reading —
   reconcile installed vs on-disk version (a source showing `_MAX_HEAD_DIM=512` can still
   be rejected by an older installed validator).
2. A **"successful" compile can hide a DISENGAGED kernel:** `flash_attention` silently
   falls back to torch SDPA unless you bump `MAX_HEAD_DIM` in **both**
   `attention_cte.py` and `attention_segmented_cte.py`. Verify engagement via profile.

**Levers:** newer nkilib + the two-file cap bump; or manual matmul attention (same path
Gemma4 decode already uses); or a bespoke d256/d512 kernel. Route only the offending
layers to torch SDPA (e.g. Gemma4 global decode).

**Cascade:** head_dim>128 → torch-SDPA fallback → KV cache becomes graph I/O → compile-time
`NCC_EOOM002` (see below) → a bespoke d512 NKI kernel then overflows SBUF (`NCC_IGCA037`).

Other `INKI016` variants: "Gate projection clamp only supported in TKG mode" (AUTO picked
CTE at low T → force `ComputationMode.DECODE/TKG`); "output_len must be divisible by
f_len" (non-power-of-2 prefill bucket 384 → use 256/512); FP8 KV mismatch (`k_prior/v_prior`
float8_e4m3, `k_active/v_active` bf16 → cast actives to `float8_e4m3`).

---

## MoE kernel selection & tiny-batch OOB

- **Variant for dtype/router:** `NF.moe_cte` (bf16, any token count, external router);
  `NF.moe_tkg` (MXFP4/trn3-only — `NotImplementedError` on bf16/trn2); `NF.moe_block_tkg`
  (bf16, internal router; `skip_store_router_logits=True` unsupported → `NCC_IGCA090`).
- **Alignment guard gap:** `moe_block_tkg._can_use_kernel` checks `H%256` but **not
  `I%128`**; a small per-rank intermediate (`I_TP=512/8=64`) passes the guard then
  DGE-scatters OOB. `moe_cte._can_use_moe_cte_kernel` correctly guards `I_TP<128 → fallback`.
- **Tiny cross-DP EP decode batch:** `moe_cte`/`build_blockwise_mapping` tile the token dim
  in 128-row tiles grouped into `block_size=256` blocks; a DP16×bs1 = 16-token decode
  under-fills tile 0 → `Out-of-bound ... index range [0,127] exceed dimension size of 16`.
  In-tree EP references (e.g. `gpt_oss`) used `moe_cte` only in prefill. **Zero-pad the
  token dim to a multiple of `block_size`** with `torch.cat`-of-zeros (pad rows have zero
  affinity, inert), run, slice `[:T]`. Pad to `block_size`, not 128, for whole blocks.

---

## SBUF overflow inside an NKI kernel — `NCC_IGCA037`

`... too big for SB, requires 262144 bytes` (256KB = 64K f32 recurs). A fresh kernel
preloads a too-large live set (whole `(128, W*D)=(128,4096)` window; `[512,512]`
intermediate at head_dim=512). Get the exact NKI file:line from the log:
`(DMACopy: ...)Source Kernel "..." File "....py", line N`. **Fix:** stream per-tile via
`nisa.dma_copy` inside the loop instead of preloading; smaller d-tiles/head-packing; or
route the offending layers to a different backend. (Swapping `nl.affine_range` for
`nl.sequential_range` does nothing: in NKI 0.7 both are aliases of Python `range`.)

**Silent-spill variant:** `SbufManager(use_auto_alloc=True)` silently spills an
over-budget SBUF working set to HBM (28.4GB/step at B=8) with **no error** — flip to
`use_auto_alloc=False` to turn the silent spill into a visible compile-time OOM you can act on.

---

## The 8-activation-LUT ceiling — `NCC_INLA001` (a)

NeuronCore has **exactly 8** transcendental-LUT slots (`walrus/lower_act/src/lower_act.cpp:348`).
DSv4's `exp, log1p(softplus), logistic(sigmoid), rsqrt, sqrt, cos, sin, power` = 8. It is
**batch-dependent**: B=64 co-schedules all 8 into one region and overflows where B=32
passes with a byte-identical op set. **Diagnose:** `scripts/scan_hlo.py act graph.hlo`.
**Fix:** drop one table — the `power` table (RMSNorm variance) is removable by writing
`x*x` not `.square()`/`.pow(2)` (see dtype_layout_legalization.md); also drop dead
scoring paths, `relu`→comparison+mux, avoid 4D.

---

## The "GDN trap" — isolated probe passes, full-graph verifier rejects — `NCC_INLA001` (b)

A kernel micro-optimization (a 3→2 matmul fold) made the 2nd matmul read PSUM starting at
**partition 1 (offset 128)** instead of partition 0. An isolated `compile_nki` probe
**PASSES**; only the **full-graph O1 BIR verifier REJECTS** it
(`Invalid access of 1 partitions starting at partition 1, Offset:128`). **Never trust the
isolated probe — validate against the whole-graph compile.** Fix: ensure PSUM/consumer
reads start at partition 0; revert the fold.

**NKI slice legality (when writing the kernel):** free-axis (column) slices are legal only
as `nc_matmul` operands; a free-axis slice into a vector-engine op is an "unsupported
expression"; a partition-axis slice starting at offset>0 feeding a consumer is an illegal
partition access (`INLA001`). Related custom-d256 codes: `NCC_IBIR297` (both `tensor_tensor`
operands must share partition base) and `NCC_ISCH715` (allocate PSUM per-GQA-iteration).

---

## Compile-time HBM OOM — `NCC_EOOM002` / `NCC_EXSP001`

Compile-time only (device residency is fine). Read the breakdown line, e.g.
`44.94GB ... 27.38GB I/O tensors, 31.11GB internal (scratchpad)`. **If I/O dominates**,
the cause is usually **not scratchpad**:

- **head_dim>128 SDPA fallback** promotes every layer's K and V cache to graph I/O (read
  `index_select`, write `index_put_` ⇒ double-counted; Gemma4 hit 41.6GB). **Split-KV**
  declares K and V as independent I/O (double-counts 27.38GB).
- Inspect `example_inputs.txt` for oversized KV inputs (e.g. `(217055,2,16,256)`); check
  `fxgraph.txt` layer count for a **stale 1-layer cache** producing a phantom 217K-block OOM.

**Fixes:**
- **SWA-skip:** when `chunk_size ≥ sliding_window`, SWA layers skip the prior-cache read
  (14GB→3.4GB scratchpad) — a general lever.
- Online/streaming segmented softmax instead of materializing `[H,T,T]`.
- `VLLM_NEURON_KV_GMU_BUDGET_CAP_FRACTION` (~0.12 Gemma4-31B; 0.05 + `VLLM_NEURON_MIN_KV_BUDGET_GIB=0.1`
  for the MoE variant) to cap KV blocks.
- Split-KV: `KVCacheTensor.shared_by` to alias the halves so they don't double-count.
- Reduce prefill chunk to a power-of-2 that fits (weights 20.27GB leave ~3.73GB → chunk ≤ ~2048).
- `SUPPORTED_KV_SEGMENT_SIZES={512,1024,2048,4096,8192}` is a hard NKI cap (16384 unavailable).

See the perf skill's
[`kv_cache_memory_model.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/kv_cache_memory_model.md)
and
[`kv_cache_budgeting.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/kv_cache_budgeting.md)
for the accounting model. This is model/config-fixable, not a compiler bug.
