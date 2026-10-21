# NKI Performance Engineering on Trainium2 / Trainium3

## An Expert Primer — V1, with Field Lessons and an Applied Cross-Reference

---

### About sourcing

Everything here comes from AWS's public Neuron documentation (NKI architecture guides for Trn2/Trn3, the NKI Performance Optimizations deep-dive, the `nki.isa` and `nki.compiler` API references, the DMA/DGE/access-pattern deep-dives, and the `nki-samples` and NKI Library kernels), plus — in §§9–10 — public JAX/Pallas documentation, accelerator-architecture literature, and a published production case study, plus — in §12 — a second-hand engineering-program report used as a symptom catalogue, plus mechanical reasoning derived from those primitives. The labels used throughout are:

- **[doc]** — stated in AWS documentation. Numbers you can quote.
- **[derived]** — arithmetic or logic following from documented primitives. Reproducible on paper; check the algebra yourself.
- **[heuristic]** — practitioner rule of thumb. Directionally useful, not a specification. Verify on your workload.
- **[doc-jax]** — stated in JAX/Pallas documentation (§9). Authoritative *for TPU*; an analogy, not a specification, when applied to Trainium.
- **[lit]** — peer-reviewed or arXiv accelerator-architecture literature (§10). Establishes vocabulary and structure; absolute numbers rarely transfer.
- **[case]** — the SGLang-JAX / LMSYS Ling-2.6-1T production case study (§10.4). Measured on TPU v7x; the *methodology* transfers, the numbers do not.
- **[field]** — measured first-hand during a multi-session optimization campaign on a large dense decoder-only transformer served on a single Trn2 instance (§11). Model identity, absolute latencies and business targets are deliberately omitted; what is reported is the *mechanism*, the *failure mode*, and the *relative* effect. Treat these as strong practitioner evidence from one workload, not as specifications.
- **[prog]** — reported status from a separate, larger engineering program optimizing an open-weights sparse-MoE model on Trn3-class hardware against a GPU price-per-token target (§12). This is **second-hand**: the observations are that program's, not measured here. Identities, owners and business targets are omitted; what is kept is the symptom and the shape of the configuration, because the value of §12 is the *diagnosis* the rest of this book can offer for those symptoms.

I have no privileged or internal Trainium data, and no introspective access to hardware I run on. Where the docs are silent — exact instruction latencies in cycles, engine-specific pipeline depths, DMA arbitration policy — I say so rather than inventing figures. In a performance document, a confidently wrong constant is worse than an acknowledged gap.

---

## 1. The Cost Model

Optimization is bottleneck identification plus bottleneck removal, repeated. So the first thing to build is a model precise enough to predict which of the seven resources is binding *before* you profile, and to tell you when you're done.

### 1.1 The seven resources

A NeuronCore kernel's wall time is the max over seven largely independent resources, plus the serialization penalties between them:

```
T_kernel ≈ max( T_PE, T_vector, T_scalar, T_gpsimd, T_DMA, T_CC, T_sync ) + stalls
```

- **T_PE** — Tensor Engine occupancy: matmuls and transposes.
- **T_vector** — Vector Engine: elementwise, free-axis reductions, shuffles.
- **T_scalar** — Scalar Engine: activations, PSUM evacuation, scale/bias.
- **T_gpsimd** — GpSimd: custom ops, iota, some gathers, memsets.
- **T_DMA** — HBM↔SBUF traffic, descriptor-limited or bandwidth-limited.
- **T_CC** — Collective Communication Engines (CC-Cores): cross-core and cross-chip traffic. Zero for single-core kernels; frequently dominant for multi-core ones. See §8.
- **T_sync** — semaphore/queue bookkeeping; rarely binding but shows as gaps.

The `+ stalls` term is where most real kernels lose their performance, and it is *not* a property of any single engine — it is a property of your dependency graph. A kernel where every engine is 40% busy and nothing overlaps is a scheduling failure, not a throughput failure. Distinguishing "engine is saturated" from "engine is waiting" is the single most important diagnostic skill on this architecture.

Sections 1–7 develop the single-core case, where T_CC is zero. Section 8 adds collectives, where it usually isn't.

### 1.2 Tensor Engine time

The Tensor Engine is a 128×128 systolic array of multiply-accumulate PEs [doc]. `nisa.nc_matmul` takes a **stationary** operand of at most [K≤128 partitions, M≤128 free], a **moving** operand of at most [K≤128, N≤512 free], and writes [M partitions, N free] FP32 into one PSUM bank [doc]. The contraction dimension K sits on the partition axis of *both* inputs [doc].

Cycle cost of one instruction [derived], from systolic geometry:

```
cycles(nc_matmul) ≈ N + (K − 1) + (M − 1) + fixed_overhead
```

N cycles of useful streaming, wrapped in skew delays proportional to K (row skew) and M (column drain). The docs do not publish `fixed_overhead` or the exact clock, so use this for *ratios*, not absolute latency.

Efficiency, the number that matters:

```
η_PE = (K/128) · (M/128) · N/(N + K + M − 2)
```

Three independent factors, all multiplicative:

| K | M | N | η_PE | note |
|---|---|---|---|---|
| 128 | 128 | 512 | **0.67** | canonical full tile |
| 128 | 128 | 2048* | 0.89 | *chained matmuls, same stationary |
| 128 | 128 | 64 | 0.20 | short stream — pipeline-dominated |
| 64 | 64 | 512 | 0.19 | half rows, half cols |
| 32 | 32 | 512 | 0.05 | small-matmul catastrophe |
| 128 | 96 | 512 | 0.50 | the "128*96" profiler signature [doc] |

That last row is worth memorizing because the profiler shows it to you literally: the docs describe a MultiplyMoving instruction whose operand reads `128*96`, meaning it uses 128 rows and 96 columns of the array, underutilizing TensorE [doc]. When you see partition counts below 128 in the instruction detail view, you are reading η_PE directly off the screen.

**The chaining subtlety** [derived]: the N/(N+K+M−2) term assumes an isolated instruction. Consecutive `nc_matmul`s against the *same* stationary tensor keep the array primed — one instruction's drain overlaps the next's fill — so a chain of J matmuls approaches `J·N/(J·N + K + M − 2)`. This is why "one matmul with N=512" and "four matmuls with N=128 sharing a stationary operand" are *not* equivalent, and why loop order (§5.3) is a first-class optimization rather than a stylistic choice.

### 1.3 Data types and the effective array shape

Trn2 (NeuronCore-v3) dense TensorE throughput: **158 TFLOPS FP8, 79 TFLOPS BF16/FP16/TF32, 20 TFLOPS FP32**, plus **316 TFLOPS sparse** for FP8/BF16/FP16/TF32 [doc]. FP8 matmul (including FP8_E4M3 and FP8_E5M2 formats) runs at double BF16 throughput [doc].

Trn3 (NeuronCore-v4) delivers **315 MXFP8/MXFP4 TFLOPS** [doc], and here the mechanism deserves precision because it changes how you tile. The docs are explicit: an MX matmul runs at **4× the throughput of a BF16/FP16 matmul**, achieved by *quadrupling the maximum contraction dimension from 128 to 512, effectively presenting a 512×128 systolic array to the programmer* — while under the hood the array remains a 128×128 grid, with each PE performing four pairs of MX multiplications and accumulating the four results per cycle [doc].

This is not cosmetic. It rewrites the efficiency formula for MX types [derived]:

```
η_PE(MX) = (K/512) · (M/128) · N/(N + K/4 + M − 2)
```

Your K-tiling changes from 128-sized chunks to **512-sized chunks**. A kernel tiled for BF16 and naively recompiled for MXFP8 will use one quarter of the available contraction depth and leave 75% of the throughput on the table. Trn3 also supports structured sparsity at up to 315 TFLOPS FP16/BF16/TF32, with patterns 4:16, 4:12, 4:8, 2:8, 2:4, 1:4, 1:2 [doc]. MXFP8×MXFP4 mixed-operand matmul is allowed [doc]; `nisa.nc_matmul_mx` and `nisa.quantize_mx` are the relevant APIs.

**Practical dtype ladder** [derived from the throughput figures]:

```
FP32     1×      ← numerically fragile paths only
TF32     ~4×
BF16/FP16 ~4×    ← default
FP8      ~8×     (Trn2: 158 TFLOPS)
MXFP8/4  ~16×    (Trn3: 315 TFLOPS, 4× BF16)
```

PSUM accumulates in FP32 regardless of input dtype [doc], so narrowing inputs does not degrade the summation itself — the accuracy cost is entirely in operand quantization. That asymmetry is what makes aggressive input narrowing viable on this architecture.

### 1.4 Vector and Scalar Engine time

Vector Engines deliver **1.2 TFLOPS FP32** [doc] and handle FP8, FP16, BF16, TF32, FP32, INT8, INT16, INT32 [doc]. The critical performance feature on Trn2+ is **VectorE performance mode**: for BF16/FP16 data, instruction throughput is **doubled or quadrupled** relative to NeuronCore-v2 depending on instruction type, with `nisa.tensor_copy` and `nisa.tensor_scalar` seeing up to **4×** when input and output tensors meet the conditions [doc]. Computation precision is unchanged — everything still computes in FP32 internally [doc]. Programmers do not enable this explicitly; **VectorE detects the conditions and enables performance mode automatically in hardware** [doc]. The mechanism leverages a shared memory bus between VectorE and GpSimdE [doc].

The engineering consequence [derived]: performance mode is a *layout-and-dtype* optimization, not an API call. You get 2–4× on the vector engine by ensuring 16-bit dtypes and access patterns that qualify — and you can lose it silently by leaving a tensor in FP32 or in PSUM. Since there is no flag to check, the profiler's instruction duration is your only feedback signal.

A generation caveat worth flagging: the doc language for the 2×/4× lift is phrased relative to *NeuronCore-v2*. If you are comparing a Trn2 kernel against a Trn2 baseline, the relevant question is whether performance mode is engaging at all, not the multiplier versus an older chip.

**Minimum efficient free size** [doc]: NeuronCore compute engines typically require **at least 512 elements per partition** in the source tensor to be efficient. The docs walk a case where ScalarE is busy with many back-to-back activation instructions whose access pattern reads `fp32@20580[1,1,1][1,1,1]` — 3D strides and 3D shape, i.e. one element — and the fix is to increase the free dimension size of instruction input tiles [doc]. This is the accelerator analogue of loop overhead dominating a scalar loop body.

Scalar Engine delivers **1.2 TFLOPS FP32** [doc] and is purpose-built for operations where every output element depends on exactly one input element [doc].

### 1.5 DMA time

This is where the documentation is unusually concrete, and where most memory-bound kernels are lost.

**Engine count** [doc]: each NeuronCore-v3 is typically paired with **16 DMA engines** to drive its SBUF bandwidth.

**Worked example from the docs** [doc]: a DMA copy of a packed [128, 512] HBM tensor into a [par_dim(128), 512] SBUF tile. The HBM tensor is 128 chunks of 512 contiguous elements laid back to back; the minor dimension (512) maps to the SBUF free dimension, the major to partitions. With 16 engines, **each DMA engine moves 128/16 = 8 chunks of 512 elements**. In a 128×128 BF16 profile, each engine handles 8 partition lanes, and each DMA operation is **2 KiB (8 rows × 128 elements × 2 B)** [doc].

**Saturation point** [doc]: at **4096 free bytes**, DMA bandwidth is nearly saturated. Small frequent transfers incur significant initiation overhead and are latency-bound; larger transfers amortize it.

**The utilization threshold** [doc]: the docs define the memory-bound symptom precisely — DMA engines "busy" but at **bandwidth utilization below 60%**, with compute engines relatively idle. That 60% figure is your go/no-go gate for whether §6 techniques apply.

Cost model [derived from the above]:

```
T_DMA ≈ n_descriptors · t_setup  +  bytes / BW_effective
BW_effective ≈ BW_peak · f(contiguous_bytes_per_burst)
```

with f rising steeply to ~1 near 4 KiB per transfer. The practitioner target frequently cited is **≥512 contiguous bytes per partition per descriptor** as a floor and **≥4 KiB free-dimension bytes** for saturation [doc for 4096; heuristic for the 512 floor].

**The dtype trap** [derived]: DMA efficiency depends on *bytes*, not elements. A tile with free dimension 512 is 1024 B in BF16 but 2048 B in FP32 — so narrowing dtype, which helps every compute engine, simultaneously *halves* your per-descriptor byte count and can push a marginal transfer below the efficiency knee. Narrow dtypes need proportionally larger free dimensions to hold DMA utilization constant. This is a real and counterintuitive interaction: the same change that gives you 4× on TensorE can cost you 2× on DMA if the free dimension isn't grown to compensate.

**DMA transpose** [doc]: Trn2 supports bit-accurate DMA transposition for 2-byte and 4-byte dtypes. Where a plain DMA copy has each engine reading an independent 512-element slice, **DMA transpose requires all 16 engines to work cooperatively** — they should write into a single [par_dim(128), 16] SBUF tile in parallel, with the 16 free-dimension elements contiguous [doc]. The transposed source is [512, 128], whose most-minor dimension becomes the SBUF partition dimension [doc]. `nisa.dma_transpose` exposes this. The cooperative requirement explains why DMA transpose has a different — and more fragile — performance profile than DMA copy: you are depending on all 16 engines staying in lockstep.

### 1.6 The roofline, and why Trn3 raises the bar

For a canonical inner iteration (K=128, M=128, N=512, BF16) [derived]:

```
work    = 2 · 128 · 128 · 512          = 16.78 MFLOP
operands= (128·128 + 128·512) · 2 B    = 160 KiB
intensity ≈ 102 FLOP/byte  (against SBUF)
```

At Trn2's 79 TFLOPS BF16, sustaining the array needs ≈ **0.77 TB/s** of operand delivery [derived]. Against SBUF that is comfortable; against HBM it is only achievable with tile reuse.

Now run it for Trn3 MXFP8 [derived]. Compute is 4× higher, and MX matmul's K extends to 512, so a full instruction is K=512, M=128, N=512:

```
work    = 2 · 512 · 128 · 512          = 67.1 MFLOP
operands= (512·128 + 512·512) · 1 B    ≈ 320 KiB  (plus MX scale metadata)
intensity ≈ 205 FLOP/byte
```

Required operand bandwidth rises roughly proportionally with the throughput gain. **The arithmetic intensity you must achieve to stay compute-bound goes up on Trn3, not down.** Every technique in §5–§7 that increases reuse or reduces off-chip traffic becomes *more* load-bearing on the newer chip, and a kernel that was comfortably compute-bound on Trn2 can become memory-bound on Trn3 without a single line changing. This is the single most important strategic point in this document.
---

## 2. Memory: SBUF, PSUM, and Allocation Control

### 2.1 Capacities and structure

| | Trn2 (NC-v3) | Trn3 (NC-v4) |
|---|---|---|
| SBUF | 28 MiB = 128 × 224 KiB [doc] | 32 MiB = 128 × 256 KiB [doc] |
| PSUM | 2 MiB [doc] | 2 MiB (unchanged) [doc] |
| PSUM structure | 128 partitions × 8 banks × 2 KiB [doc] | same |
| HBM | 96 GB | 144 GB HBM3e, ~4.9 TB/s [doc] |

The **2 KiB PSUM bank = 512 FP32 elements = exactly one maximal matmul output tile** [doc]. This is the load-bearing constant of the whole architecture: it is why the moving free dimension caps at 512, why `nisa.nc_matmul` output is [M, N≤512], and why PSUM allocation is bank-granular. A PSUM tile **cannot cross bank boundaries**, so free-dimension size per tile maxes at 2 KiB [doc].

Note PSUM did **not** grow on Trn3 while TensorE got ~4× faster [doc]. The accumulator-to-compute ratio therefore *fell by 4×* [derived]. Eight banks that comfortably covered a Trn2 pipeline are proportionally scarcer on Trn3 — making PSUM eviction (§5.4) the most likely new bottleneck on the newer part. If you port one thing consciously, port your drain strategy.

### 2.2 Tiles, blocks, and par_dim

A NKI tensor in SBUF/PSUM must have one dimension mapped to the physical partition dimension, labelled `nl.par_dim`; any tensor whose first dimension is the partition dimension is a **tile**, the unit compute APIs operate on; remaining dimensions after P are **free (F)**; any dimension *before* P is a **block (B)** dimension [doc].

Block dimensions are **logical** [doc]. The compiler inspects the code and uses a heuristic-driven allocator to decide how many *physical* tiles back each logical tensor, with the goal of achieving instruction parallelism across engines while minimizing on-chip memory use [doc]. This is the single most important thing to understand about NKI memory: **writing `(16, par_dim(128), 512)` does not guarantee 16 physical buffers.** It expresses intent; the allocator decides. When your pipeline needs N buffers live simultaneously and the allocator gives you fewer, you get silent serialization — engines waiting on buffer reuse, with no error and no warning.

### 2.3 Direct allocation: the escape hatch

When the allocator's heuristic loses, `nki.compiler` (conventionally `ncc`) lets you bypass it entirely.

**`ncc.sbuf.mod_alloc`** — modulo allocation. For a tensor of logical shape `(8, par_dim(128), 512)` with two physical tiles, logical tiles 0,2,4,6 share one physical tile and 1,3,5,7 share the other [doc]. Parameters: `base_addr` (byte offset within each partition), `base_partition` (must be 0 in current versions), `num_par_tiles` (physical tiles along partitions — only applicable for tiles using fewer than 64 partitions per ISA constraints), `num_free_tiles` (physical tiles along the free dimension) [doc].

**`ncc.sbuf.alloc(func)`** — full control. `func(idx, pdim_size, fdim_size)` returns `(start_partition, byte_addr)` [doc]. Rules [doc]:

- The number of integers in `idx` matches the tensor's block dimension count.
- `fdim_size` is the total free-dimension size **in bytes** (e.g. `4*32*sizeof(bfloat16)` = 256).
- If `64 < pdim_size <= 128`, `start_partition` **must be 0**.
- `byte_addr` must be aligned to `nki.language.constants.sbuf_min_align`.
- Valid `byte_addr` range excludes a compiler-reserved region — on NC-v2 that was 192 KiB − 16 KiB reserved; **check the current architecture guide for your target's partition size and reserved amount rather than assuming**.

**`ncc.psum.alloc(func)`** — `func` returns `(bank_id, start_partition, byte_addr)` [doc]. `fdim_size` cannot exceed 2 KiB (one bank), `base_addr` and `base_partition` must currently be 0 [doc]. `ncc.psum.mod_alloc` takes `base_bank`, `num_bank_tiles`, `num_free_tiles` [doc].

**Hard constraint** [doc]: you **cannot mix automatic allocation (`auto_alloc`) and direct allocation APIs in the same kernel.** It is all-or-nothing per kernel. This makes direct allocation a significant commitment, not an incremental tweak — plan for it to be a deliberate rewrite of a kernel you have already proven correct.

**Related compiler controls** [doc]: `nki.compiler.enable_stack_allocator` (must be used together with `skip_middle_end_transformations`), `nki.compiler.force_auto_alloc` (ignores any direct allocation in the kernel), and `nki.compiler.multi_buffer(factor=2)` which creates a multi-buffering directive — often the cheapest way to get the buffering of §5.2 without hand-allocating everything.

**Recommended sequence** [heuristic]: (1) correct kernel under automatic allocation; (2) profile; (3) if the profile shows engines stalled on buffer reuse rather than on real data dependencies, try `multi_buffer` first; (4) only if that fails, convert the whole kernel to direct allocation and hand-place the hot multi-buffered tensors and PSUM banks.

---

## 3. The Instruction Set, By Optimization Value

`nki.language` (`nl`) is the productivity layer — the compiler picks engines and instructions. `nki.isa` (`nisa`) maps approximately 1:1 onto machine instructions with explicit engine selection [doc]. The documented workflow: **use `nl` for your first working version, then move to `nisa` for the performance-critical path** [doc/heuristic].

What follows is organized by what each instruction *buys you*, because the mapping from task to instruction is where most performance is won or lost. Knowing that `nisa.activation_reduce` exists is often the difference between a three-instruction inner loop and a ten-instruction one.

### 3.1 The single highest-value technique: instruction fusion

The Performance Guide states this outright and quantifies it [doc]: combining instructions via low-level `nki.isa` APIs leverages the **deep pipelined stages within the VectorE and ScalarE datapaths**, increasing hardware utilization per instruction and reducing instruction count. The documented example:

```python
# impl 1 — three instructions, three passes over the data
scaled  = nl.multiply(data, scale)
shifted = nl.add(scaled, bias)
exp     = nl.exp(shifted)

# impl 2 — one instruction, one pass
exp = nisa.activation(op=nl.exp, data=data, scale=scale, bias=bias)
```

These are functionally equivalent, and **impl 2 is 3× faster in latency** by touching the input data only once and running multiply, add, and exp in a pipeline [doc].

That 3× is not an exotic result — it is the *typical* return on fusing a chain that the high-level API expresses as separate ops. Elementwise-heavy kernels are usually full of these.

**The fusion catalogue** — instructions that do more than one thing per pass:

| Instruction | Fuses | Engine |
|---|---|---|
| `nisa.activation` | `f(scale·x + bias)`, one pass | Scalar |
| `nisa.activation_reduce` | activation **+ free-axis reduction** | Scalar |
| `nisa.tensor_scalar` | two scalar ops (`op0`, `op1`) against a tile | Vector |
| `nisa.tensor_scalar_reduce` | tensor_scalar **+ reduction** | Vector |
| `nisa.scalar_tensor_tensor` | scalar op then tensor-tensor op | Vector |
| `nisa.tensor_tensor_scan` | fused scan across tiles | Vector |
| `nisa.tensor_scalar_cumulative` | cumulative variant | Vector |
| `nisa.select_reduce` | select **+ reduce** | Vector |
| `nisa.dma_compute` | compute during the DMA itself | DMA |

`nisa.activation_reduce` deserves special note: it performs the activation *and* a reduction along the free dimension of the result at **small additional performance cost**, returning the reduction in-place into a SBUF/PSUM tile with the same partition-axis size as the input and one element per partition [doc]. The hardware backs this with **128 registers on the scalar engine for storing reduction results, one per partition** [doc]. On NeuronCore-v2 the `reduce_op` had to be `nl.add` [doc]; Trn3's Activation2 generalizes this considerably (§4.2).

Softmax is the canonical beneficiary: `max` → `exp(x − max)` → `sum` collapses from many passes into roughly two fused instructions, one of which also produces the normalizing sum for free.

### 3.2 Matmul family

- **`nisa.nc_matmul`** — the core. Stationary [K≤128, M≤128], moving [K≤128, N≤512], FP32 output to PSUM [doc].
- **`nisa.nc_matmul_mx`** — MXFP8/MXFP4 with in-instruction dequantization by the MX scales, output FP32 or BF16, 4× BF16 throughput, contraction dimension to 512 [doc].
- **`nisa.quantize_mx`** — produce MX-format tensors with their scales.
- **`nisa.nc_transpose`** — transpose on TensorE (against an identity matrix). `nisa.sb_transpose` and `nl.matmul(..., transpose_x=False)` also lower here [doc].
- **`nisa.matmul_perf_mode`** — performance-mode selector including `double_row`; note **`double_row` cannot be combined with column tiling** [doc].

**PE tiling** is the answer to small matmuls and is more capable than commonly assumed [doc]: **row tiling** slices the 128 PE rows into 2×64 (NC-v2+) or **4×32 (NC-v3+)**; **column tiling** slices the 128 columns the same way; row and column tile sizes are set **independently** via the `tile_size` field as a `(row_size, col_size)` tuple [doc]. Multiple `nc_matmul` instructions with stationary tiles smaller than [128,128] then run **in parallel** [doc].

Worked recovery [derived]: a K=32, M=32 matmul alone gives η_PE ≈ (32/128)·(32/128) = **6.25%** geometric occupancy. With 4×32 row tiling and 4×32 column tiling, up to 16 such independent matmuls occupy the array simultaneously — recovering toward **full occupancy**, a ~16× improvement on the geometric term. For MoE expert slices, small-head-dim attention, and grouped/batched GEMMs, this is frequently the difference between a kernel that is worth writing and one that is not.

### 3.3 Reduction family — and the cross-partition problem

Free-axis reductions are cheap; **cross-partition reductions are not**, because partitions are physically separate lanes.

- **`nisa.tensor_reduce`** — free-axis reduce, one instruction, all 128 lanes in parallel.
- **`nisa.tensor_partition_reduce`** — cross-partition reduce (the direct but slower path).
- **TensorE ones-vector matmul** — the fast cross-partition path.

The docs make the strategic version of this point explicitly, and it is one of the most valuable single sentences in the Performance Guide [doc]: to minimize intermediate transposes in an operator chain like **linear_layer → RMSNorm → linear_layer**, map the first linear layer's **weight tensor to the stationary operand** and **leverage TensorE to perform the cross-partition summation for RMSNorm**.

Unpack why that works [derived]: the matmul output arrives with M on partitions. If RMSNorm must reduce over the hidden dimension and hidden landed on partitions, you would need either a transpose or a slow partition-reduce. By choosing which operand is stationary, you control which axis of the *output* lands on partitions — and by using TensorE (already the engine holding the data) for the summation, you pay nothing extra on the vector engine. Layout choice, operand-role choice, and reduction cost are one coupled decision, not three.

The docs also note the layout question for RMSNorm can go either `[4×hidden_size, seq_len]` or `[seq_len, 4×hidden_size]`, and **the choice should be made based on the surrounding operators** [doc] — i.e., optimize the chain, never the op.

- **`nisa.bn_stats` / `nisa.bn_aggr`** — two-phase batch-norm statistics, avoiding a hand-rolled multi-pass mean/variance.
- **`nisa.max8`, `nisa.nc_find_index8`, `nisa.nc_match_replace8`** — top-k style primitives. `nc_match_replace8` is the workhorse for iterative top-k (find max, replace it, repeat) and is dramatically cheaper than a sort.

### 3.4 Data movement and layout

- **`nisa.nc_stream_shuffle`** — cross-partition shuffle; the general lane-permutation primitive.
- **`nisa.dma_transpose`** — the cooperative 16-engine HBM↔SBUF transpose of §1.5.
- **`nl.load_transpose2d`** — transposed load.
- **`nisa.local_gather`** — gather within SBUF.
- **`nisa.nc_n_gather`** — note the naming: **the `n` indicates the instruction corresponds to `n` groups of underlying ISA instructions, where `n = ceil(elems_per_partition / 512)`** [doc]. This is a rare case where the API name encodes its own cost model — a gather over a wide free dimension is silently several instructions. The docs note `nisa.dma_copy` as an alternative gather path [doc].
- **`nisa.tensor_copy` / `tensor_copy_predicated`** — copies, and the primary beneficiaries of VectorE 4× performance mode [doc].

### 3.5 Control, selection, and the rest

- **`nisa.affine_select`, `range_select`, `sequence_bounds`** — masking and ragged-edge handling without branches. Most compute APIs also accept a `mask=` argument.
- **`nisa.iota`, `nisa.memset`, `nisa.dropout`, `nisa.rng`/`rand2`/`set_rng_seed`** — generators. Note memset's hazard in §5.1.
- **`nisa.nonzero_with_count`** — data-dependent compaction.
- **`nisa.register_alloc/load/move/store`, `nisa.VirtualRegister`** — scalar register file for control values, letting you compute indices without a round trip through a compute engine.
- **`nisa.core_barrier`, `nisa.sendrecv`** — cross-core synchronization and transfer.
- **`nki.collectives`** — `all_reduce`, `all_gather`, `reduce_scatter`, `all_to_all`, `collective_permute` and the implicit variants, usable *inside* a kernel. `collective_permute_implicit_reduce` in particular allows fusing communication with reduction, which is the basis for overlapping collectives with compute rather than sequencing them.
- **`nisa.dge_mode`, `nisa.oob_mode`, `nisa.dma_engine`, `nisa.engine`, `nisa.reduce_cmd`** — the parameter enums that give you explicit control over descriptor generation, out-of-bounds behavior, and **which engine executes an instruction**. `engine=` is the load-balancing lever of §5.4.
- **`nisa.get_nc_version()` / `nisa.nc_version`** — target query for generation-portable kernels.
---

## 4. Trainium3-Specific Features

### 4.1 MX matmul: retile your K loop

Covered mechanically in §1.3. The engineering checklist for adopting MX types:

1. **Retile K to 512, not 128.** MX matmul quadruples the maximum contraction dimension [doc]; a BF16-shaped K loop wastes 3/4 of it.
2. **Quantize with `nisa.quantize_mx`**, and account for MX scale metadata in your SBUF budget — block-scaled formats carry a shared exponent per block, which is extra bytes to move and hold.
3. **Choose the output dtype deliberately** — MX matmul writes FP32 *or* BF16 [doc]. BF16 output halves PSUM eviction traffic, which matters more on Trn3 than Trn2 because of the accumulator ratio noted in §2.1.
4. **Mixed MXFP8 × MXFP4 is legal** [doc] — a natural fit for weights-in-FP4 / activations-in-FP8 schemes.
5. **Validate accuracy per layer.** 4-bit mantissas with block scaling are viable for many layers and not for others; this is an empirical question about your model, not a hardware question.

### 4.2 Activation2

Trn3 introduces the **Activation2** instruction, which generalizes Activation [doc]:

| | Activation (Trn2) | Activation2 (Trn3) |
|---|---|---|
| Scale | multiply only | multiply, **or disabled** |
| Bias | add only | add, **subtract**, or **disabled** |
| Reduce | `add` only | **add, max, min, absmax, absmin** |

Three consequences that change kernel structure [derived]:

- **Softmax loses an instruction.** `exp(x − max)` needs bias *subtraction*; on Trn2 you negate the max first (an extra vector op or a sign fold upstream). Activation2 subtracts directly.
- **Max-reductions can migrate to ScalarE.** The row-max in softmax, and `absmax` for dynamic quantization scale computation, previously belonged to VectorE. Moving them to ScalarE is a direct rebalancing lever precisely when VectorE is the bottleneck — which the docs identify as the common case in attention (§7).
- **`absmax` in particular is the quantization primitive.** Computing per-tile scaling factors for FP8/MX quantization fused into an activation pass, rather than as a separate reduction, is a meaningful win in quantized inference kernels.

### 4.3 Indirect addressing

NeuronCore-v4 introduces an **indirect addressing mode for SBUF/PSUM across all compute engines (TensorE, VectorE, ScalarE, GpSimdE)** [doc] — per-element computed addressing, i.e. native `a[idx[i]]` within on-chip memory. The docs are explicit that **SBUF/PSUM indirect access for compute engines does not have `nki.isa` API support at this time** [doc].

Treat it as *forthcoming*: it is the right long-term answer for gather-shaped access (MoE token routing, KV-cache paging, sparse attention) that currently forces either a GpSimd detour or a layout compromise. Do not design a kernel around it until the API lands; do keep it in mind when deciding how much to contort a layout today.

### 4.4 Other Trn3 deltas

- **SBUF 28 → 32 MiB** [doc] — ~14% more room before allocation pressure forces direct allocation.
- **PSUM unchanged at 2 MiB** [doc] — the scarcity noted in §2.1.
- **Vector Engine additions**: NC-v4 adds new Vector Engine features beyond NC-v3 [doc]; consult the current Trn3 architecture guide for the specifics, as this area is actively evolving.
- **Near-memory accumulation via DMA** — read-modify-write in the copy path, enabling single-pass scatter-accumulate.
- **Structured sparsity** at up to 315 TFLOPS with the pattern list in §1.3 [doc].

---

## 5. The Optimization Playbook

Ordered by expected value: how often each is the true bottleneck × how large the win.

### 5.1 Eliminate false dependencies

Before any throughput work, remove serialization that isn't real. NKI uses a sequential programming model where operations run in written order, but **the compiler may reorder operations that don't depend on each other** [doc] — so your dependency graph *is* your schedule.

Three recurring false dependencies:

**Memset before an accumulation chain.** The first `nc_matmul` of a chain carries a start-of-accumulation flag that resets the PSUM bank as part of the instruction. An explicit memset wastes an instruction on another engine *and* creates a cross-engine dependency, so TensorE now waits on whichever engine ran the memset. Never zero a PSUM bank you're about to accumulate into.

**Buffer aliasing from allocator decisions.** Logical block dimensions do not guarantee physical buffers (§2.2). Two loop iterations you believe are independent may share a physical tile.

**`sequential_range` where `affine_range` would do.** `nl.affine_range` declares no loop-carried dependency and licenses cross-iteration overlap; `nl.sequential_range` forbids it. The PSUM `+=` accumulation is the blessed exception inside `affine_range` — hardware accumulation, not a dependency to serialize on. Also available: `nl.static_range` (unrolled at trace time) and `nl.dynamic_range` (see the NKI Dynamic Loops deep-dive for data-dependent trip counts).

### 5.2 Build the engine pipeline

This is the technique the docs spend the most space on, and the one with the largest wins in real kernels.

The documented diagnosis [doc]: the first instruction after an idle gap has a **semaphore wait condition naming a compute engine** — Vector, Scalar, GpSimd, Tensor. The example given is a `TENSOR_TENSOR` instruction on VectorE waiting for `S[4]` (Scalar) to reach 36, meaning VectorE was waiting for ScalarE to finish. The prescribed fix [doc]: when a sequence of operators spans different engines, **slice the computation so the engines process tiles of the original operator in pipeline fashion**.

For `X → op0(ScalarE) → Y → op1(VectorE) → Z` on a large tensor, running op0 on all of X and then op1 on all of Y leaves each engine idle half the time. Tiling X so op1 starts on tile *i* while op0 runs tile *i+1* overlaps them. **Choosing the tile size is the programmer's job and requires iterating with neuron-profile** [doc] — there is no analytic shortcut, because the optimum depends on per-instruction overheads that aren't published.

The canonical multi-engine case is attention [doc]: beyond fusing `matmul_0(Q,K) → softmax → matmul_1(softmax_out, V)` into one kernel to minimize spilling, you must **form a complex engine pipeline across Tensor/Scalar/Vector/GpSimd** to maximize utilization. In steady state:

```
       Tile i+1 : [ QKᵀ matmul        ] ← TensorE
       Tile i   : [ softmax           ] ← ScalarE + VectorE
       Tile i−1 : [ scores·V matmul   ] ← TensorE
```

TensorE works on two different matmuls (from different tiles) while Scalar/Vector handle the softmax of the tile between them. Three tiles in flight is the minimum for full overlap of a three-stage chain [derived].

**Pipelining requires buffers.** Overlap needs space: iteration *i+1*'s load cannot land in the buffer iteration *i* is reading. Block dimensions of 2–4 with `i % nbuf` indexing, or `nki.compiler.multi_buffer(factor=...)`, or direct allocation. `affine_range` grants permission; buffers grant possibility; you need both. The profiler signature of the missing buffer is unmistakable: DMA and TensorE alternating strictly, never simultaneous.

### 5.3 Maximize TensorE efficiency

From §1.2, three multiplicative terms — attack whichever is worst:

**K and M (geometric occupancy).** Fill the array. Where the problem is intrinsically small, use PE tiling (§3.2) — 4×32 row and column tiling on NC-v3+, set independently, recovering up to ~16× on 32×32 matmuls [derived].

**N (stream length).** Push toward 512, and chain matmuls sharing a stationary operand so drains overlap fills. Structure loop nests for **stationary reuse**: each stationary load costs on the order of a grid-height of cycles, so hold it fixed and stream maximum moving data before swapping. For transformer inference this is the batch-size-dependent choice of weights-stationary versus activations-stationary.

**dtype.** The ladder in §1.3. On Trn3, MX types with K retiled to 512.

**Transposes.** They occupy TensorE, competing with your matmuls. The docs distinguish two root causes [doc]: **IO transposes** on kernel input/output tensors (which must reside in device memory), and internal ones. The strategic fix is the §3.3 chain-level insight — choose operand roles so the layout comes out right, rather than transposing to fix it afterward.

### 5.4 Balance the PSUM drain

At high TensorE throughput a full PSUM bank (128 partitions × 2 KiB = 256 KiB) is produced every few microseconds, and banks recycle only after eviction. With 8 banks, drain rate gates matmul rate.

- **Fuse the drain with real work.** `nisa.activation` reading PSUM applies your nonlinearity *while* copying out; `f=identity` for a plain copy. Nothing is cheaper than work you were going to do anyway.
- **Split across ScalarE and VectorE** using explicit `engine=` on `nisa` calls. Both read PSUM; using one leaves the other idle.
- **On Trn3, use Activation2's fused min/max/absmax** to move reduction work onto ScalarE (§4.2).
- **Narrow the output.** MX matmul's BF16 output option halves eviction bytes [doc].

This technique rises in importance on Trn3 for the compounding reason in §2.1: 4× compute against unchanged PSUM.

### 5.5 Fuse instructions

§3.1. The documented 3× on a three-op chain [doc]. Audit every elementwise sequence in your inner loop against the fusion catalogue. This is usually the highest ratio of win-to-effort in the entire playbook, because it requires no restructuring — just replacing `nl` sequences with the right `nisa` call.

### 5.6 Fix DMA access patterns

Applies when DMA engines are busy at **<60% bandwidth utilization** [doc].

- **Grow free-dimension bytes toward 4 KiB** [doc], remembering the dtype trap (§1.5): narrower dtypes need proportionally larger free dimensions.
- **Increase per-instruction free size** — the docs' `[1,1,1][1,1,1]` access pattern pathology, fixed by tiling for ≥512 elements/partition [doc].
- **Coalesce many small loads** into fewer large ones and slice in SBUF.
- **Never fight layout with DMA.** Do transposes on TensorE, or use `nisa.dma_transpose` with its cooperative 16-engine pattern [doc], or fix the producer's layout.
- **Consult the DGE deep-dive** for descriptor generation modes (`nisa.dge_mode`) when descriptor count rather than bytes is binding.

### 5.7 Exploit VectorE performance mode

2–4× on `tensor_copy` and `tensor_scalar` for BF16/FP16 with qualifying access patterns, automatic in hardware [doc]. Since there is no explicit enable and no flag to query, the only way to confirm you have it is instruction duration in the profiler. Practically: keep hot vector operands 16-bit, unit-stride, and in SBUF rather than PSUM.

### 5.8 Take over allocation

§2.3. Last resort, all-or-nothing per kernel, after `multi_buffer` has failed.

### 5.9 Overlap collectives with compute

For multi-core kernels this frequently outranks everything above — an unoverlapped collective is pure added latency, and CC-Cores run in parallel with compute by design [doc]. See §8, and note that §8.5's DMA-ring arbitration contention can silently undo the DMA work of §5.6.

---

## 6. Profiling: The Diagnostic Loop

### 6.1 Tools

- **`nki.simulate_kernel`** — CPU functional simulation; correctness harness and the only way to see `nl.device_print`.
- **`nki.benchmark`** — device latency percentiles.
- **`neuron-profile` / Neuron Explorer** — the per-engine timeline. Neuron Explorer adds Hierarchy, Source Code, Summary, Memory, and AI Recommendation viewers, and can export to Perfetto.
- **`os.environ["NEURON_PLATFORM_TARGET_OVERRIDE"] = "trn2"`** (or `"trn3"`) for targeting; `NEURON_FRAMEWORK_DEBUG=1` and `NEURON_RT_ENABLE_DGE_NOTIFICATIONS=1` surface DMA detail [doc].

### 6.2 The semaphore-name decision tree

This is the documented method, and it is the fastest path from "there's a gap" to "here's the fix" [doc]:

**Find an idle gap on the bottleneck engine. Hover the first instruction after the gap. Read its semaphore wait condition.**

| Semaphore name | Meaning | Go to |
|---|---|---|
| `Vector`, `Scalar`, `GpSimd`, `Tensor` | waiting on another **compute engine** [doc] | §5.2 pipeline; §5.4 rebalance |
| Starts with **`q`** (e.g. `qSyncIO0`, `qGpSimdDynamic`) | waiting on **DMA** [doc] | §5.6 patterns; §5.2 buffering |

The docs give a worked instance from the Mamba tutorial [doc]: VectorE completely idle; the first instruction after the gap waits on `S[22]` to reach 240, set by `qSyncIO0` activity — meaning VectorE was waiting for input tensors to load. Hovering the `qSyncIO0` activity during the idle period **names the exact input tensor from your NKI source** that was being DMA'd. That is a direct line from a timeline gap to a variable in your code.

### 6.3 Reading instruction detail

- **Operand shapes reveal η_PE directly.** A MultiplyMoving (matmul) instruction reading `128*96` uses 128 rows and 96 columns of the array [doc] — you are looking at 50% column occupancy on screen.
- **Access patterns reveal free-size pathology.** `fp32@20580[1,1,1][1,1,1]` is 3D strides and 3D shape in FP32 elements [doc] — a one-element instruction, the §5.6 fix.
- **DMA op size confirms engine spread.** In the 128×128 BF16 example, each of 16 engines does one 2 KiB operation (8 rows × 128 elements × 2 B) [doc]. Fewer active engines than expected means your access pattern isn't spreading across them.
- **DMA trigger tracing** connects a transfer back to the instruction that triggered it and its preceding dependencies [doc].

### 6.4 Triage table

| Profile signature | Diagnosis | Fix |
|---|---|---|
| TensorE ~100% busy | Compute-bound — done, or change dtype/algorithm | §5.3, §4.1 |
| TensorE gapped, DMA busy but <60% BW | Fragmented descriptors | §5.6 |
| TensorE gapped, DMA busy at high BW | Genuinely memory-bound | Increase reuse; retile; §5.2 |
| TensorE gapped, Vector/Scalar saturated | Drain or elementwise bottleneck | §5.4, §5.5 |
| DMA and TensorE strictly alternating | Missing multi-buffering | §5.2 |
| All engines partly busy, none saturated | Serialization | §5.1, §5.2 |
| Many tiny instructions on one engine | Free dimension too small | §5.6, §5.5 |
| Matmul operands show <128 partitions | Geometric underutilization | §5.3, PE tiling |

### 6.5 Method

The documented flow from the community optimization walkthrough is worth adopting wholesale [heuristic, from a third-party bootcamp writeup]: **profile a single tile first** (`num_tiles=1`). One tile makes every instruction in the timeline traceable to a line in your kernel; multi-tile profiles are much harder to read. Get the single-tile instruction sequence optimal, *then* build the multi-tile pipeline.

Re-profile after every change. On a seven-resource asynchronous machine, predictions about which fix mattered are wrong more often than on any CPU — including predictions made from a model as carefully constructed as §1.
---

## 7. Worked Case Studies

### 7.1 Fused attention: the canonical multi-engine pipeline

The structure — `QKᵀ → softmax → scores·V` — is the reference case in the docs [doc], and its failure mode is stereotyped: after basic optimization the profiler shows dense matmul activity in the projections but **gaps in the attention section, with bursts of matmuls interspersed with clusters of small vector operations**, TensorE idle during the vector bursts [third-party bootcamp writeup, consistent with the documented pipeline guidance].

The diagnosis is always the same: softmax is elementwise-heavy, lands mostly on VectorE, and nothing overlaps it.

**The fix stack, in order:**

1. **Fuse the softmax chain** (§5.5). Row-max, `exp(x − max)`, and the normalizing sum collapse into roughly two fused instructions via `nisa.activation_reduce` — activation plus free-axis reduction in one pass, using the 128 per-partition scalar registers for the result [doc]. On Trn3, Activation2 subtracts the max directly and can compute the max itself with a `max` reduce [doc], removing another instruction and moving it off VectorE.
2. **Rebalance across engines** (§5.4). Split what remains between ScalarE and VectorE with explicit `engine=`. The docs' own framing: VectorE oversubscribed blocks everything else.
3. **Build the three-tile pipeline** (§5.2). QKᵀ for tile *i+1* on TensorE, softmax for tile *i* on Scalar/Vector, scores·V for tile *i−1* on TensorE.
4. **Provide the buffers** (§2.3). Three tiles in flight means three live buffer sets; the allocator does not know tile *i−1* is still in use. This is precisely where `multi_buffer` or direct allocation becomes necessary.
5. **Handle small head dimensions with PE tiling** (§3.2). head_dim 64 or 32 gives 25% or 6% geometric occupancy; 4×32 row/column tiling packs multiple heads into the array concurrently.

### 7.2 linear → RMSNorm → linear: layout as a chain-level decision

The documented insight [doc]: map the first linear layer's **weight tensor to the stationary operand** and **use TensorE for RMSNorm's cross-partition summation**, minimizing intermediate transposes.

Reasoning [derived]:

- RMSNorm reduces over hidden. If hidden lands on partitions, you need a transpose or a slow `tensor_partition_reduce`.
- Operand roles determine output layout: output partitions come from the stationary operand's M dimension.
- Choosing weights-as-stationary therefore places the *right* axis on partitions for what follows.
- And the reduction itself becomes a ones-vector matmul on TensorE — an engine already holding the data, costing nothing on VectorE.

One decision (operand role) simultaneously fixes layout, eliminates a transpose, and offloads a reduction. The docs reinforce that the `[4×hidden, seq]` versus `[seq, 4×hidden]` choice for RMSNorm should be driven by **surrounding operators** [doc]. Optimize chains, never ops.

### 7.3 Porting a BF16 Trn2 kernel to Trn3 MXFP8

A checklist that is easy to get wrong [derived from §1.3, §2.1, §4]:

1. **Retile K from 128 to 512.** The most common porting miss — MX quadruples the contraction dimension [doc].
2. **Re-examine the drain.** 4× compute, unchanged PSUM (§2.1). Consider BF16 matmul output to halve eviction bytes.
3. **Re-examine DMA free sizes.** MXFP8 is 1 byte; a free dimension sized for BF16 now moves half the bytes per descriptor, potentially dropping below the 4 KiB knee [doc + derived].
4. **Budget for MX scale metadata** in SBUF and DMA.
5. **Adopt Activation2** where you had negate-then-add or VectorE max-reductions (§4.2).
6. **Recompute the roofline.** Required arithmetic intensity roughly doubles (§1.6); a Trn2-compute-bound kernel may now be memory-bound, which changes which section of this playbook applies.
7. **Validate accuracy per layer**, not per model.

### 7.4 A worked η_PE recovery, end to end

MoE expert GEMM, 32 tokens routed to an expert, hidden 4096, intermediate 11008, BF16.

*Naive* [derived]: tile as M=32 (tokens), K=128 chunks of hidden, N=512 chunks of intermediate.
```
η_PE = (128/128)·(32/128)·512/(512+128+32−2) = 1.0 · 0.25 · 0.764 ≈ 19%
```

*With 4×32 column tiling* [derived]: four expert slices (or four token groups) share the array's columns.
```
column occupancy 0.25 → ~1.0   ⇒  η_PE ≈ 76%
```

*Plus chaining across the K loop* [derived]: 32 chained matmuls sharing stationary weights, effective stream 32×512:
```
η_PE ≈ 1.0 · 1.0 · 16384/(16384+158) ≈ 99% geometric+pipeline
```

Roughly 4× from tiling and a further ~1.3× from chaining — before any dtype change. Then MXFP8 on Trn3 multiplies the throughput at that efficiency by ~4× again, *provided* K is retiled to 512 (§7.3, item 1). These compose multiplicatively, which is why attacking η_PE before reaching for a faster dtype is the right order.

---

---

## 8. Collectives and Multi-Core Kernels

Everything up to here optimizes a single (logical) NeuronCore. Real workloads shard across cores and chips, and at that point communication usually becomes the binding constraint. This section covers the seventh resource, the `nki.collectives` API, the contention mechanics that make collectives interact badly with your DMA-optimized kernel, and the overlap patterns that fix it.

### 8.1 CC-Cores: the resource the §1 model omitted

Trainium devices have **dedicated Collective Communication Engines (CC-Cores)** that move data between NeuronCores and between Trainium chips, handling AllReduce, ReduceScatter, AllGather and All2All **while core computation continues processing in parallel** [doc].

This changes the cost model from §1.1 [derived]:

```
T_kernel ≈ max( T_PE, T_vector, T_scalar, T_gpsimd, T_DMA, T_CC, T_sync ) + stalls
```

The word "parallel" in the documentation is the whole opportunity: a collective that is not overlapped with compute is pure added latency, and one that is overlapped is close to free until it saturates the interconnect. **The default state of an unoptimized multi-core kernel is fully serialized compute and communication** — which is exactly the §5.2 pipelining problem, one level up.

Note the important asymmetry: CC-Cores are a *distinct* resource from the 16 DMA engines, but they are not independent of them — §8.5 covers the contention.

### 8.2 The collectives cost model

The docs state the structure plainly [doc]: **execution time of a collective = latency + data transfer time.**

For a ring algorithm over R ranks moving S bytes [derived, standard ring analysis consistent with the documented algorithm set]:

```
T_ring(all_reduce)     ≈ 2(R−1)·α  +  2·(R−1)/R · S/B
T_ring(all_gather)     ≈  (R−1)·α  +    (R−1)/R · S/B
T_ring(reduce_scatter) ≈  (R−1)·α  +    (R−1)/R · S/B
```

with α the per-hop latency and B the per-link bandwidth. Two consequences you can act on:

- **Small tensors are latency-bound**: the `(R−1)·α` term dominates, and it grows *linearly in rank count*. This is why algorithm choice matters (§8.3).
- **Large tensors are bandwidth-bound**: the transfer term approaches `S/B` asymptotically, and ring is near-optimal.

The crossover between these regimes is what selects your algorithm, and it is workload- and topology-specific — measure it with `nccom-test`, which now includes **fine-grained collective communication support** [doc].

### 8.3 Algorithms and topology

The runtime implements four intra-node algorithms, each optimized for different message sizes and latency requirements [doc]:

| Algorithm | Character | Best for |
|---|---|---|
| **Ring** | R−1 hops, bandwidth-optimal | large tensors |
| **Mesh** | direct all-pairs | small tensors, low latency |
| **KangaRing** | Neuron-specific ring variant | tuned middle ground |
| **RDH** (Recursive Doubling-Halving) | log₂(R) steps | latency-sensitive, power-of-2 ranks |

These run over the **2D Torus topology** of Trainium chips, connected by intra-chip and chip-to-chip **NeuronLink** interconnects, and leverage hardware features like **duplication to minimize memory bandwidth pressure** [doc].

The RDH-versus-Ring tradeoff is the classic one [derived]: RDH's `log₂(R)·α` latency term beats Ring's `(R−1)·α` badly at high rank counts — at R=32, that is 5 hops versus 31 — while Ring wins on bandwidth for large payloads. **Small tensors at high tensor-parallel degree are where naive ring collectives hurt most**, and that is exactly the regime of token-generation inference.

### 8.4 The `nki.collectives` API

Available operations [doc]: `all_reduce`, `all_gather`, `reduce_scatter`, `all_to_all`, `all_to_all_v`, `collective_permute`, `collective_permute_implicit`, `collective_permute_implicit_reduce`, `collective_permute_implicit_current_processing_rank_id`, `rank_id`, and `ReplicaGroup`.

**`all_reduce(srcs, dsts, replica_group, op)`** [doc]:

- `srcs` / `dsts` are **lists** of tensors, supporting **coalesced collective communication** — multiple tensors reduced in a single collective for improved efficiency.
- `op` is `nl.add`, `nl.minimum`, or `nl.maximum`.
- Tensors may reside on **HBM or SBUF**, but **mixing memory spaces is not supported** — all on one or all on the other.
- **Coalescing is only supported when tensors are on HBM.**

That last pair of constraints is a real design fork [derived]. The SBUF path avoids an HBM round trip, which is a large latency win for small tensors; the HBM path allows coalescing many small tensors into one collective, which is a large win when you have many of them. You cannot have both, and you must choose per collective. For token-generation inference with several small per-layer reductions, coalescing on HBM usually wins on aggregate; for a single latency-critical reduction, SBUF usually wins.

**`collective_permute_implicit_reduce(srcs0_by_channel, srcs1_by_channel, dsts_by_channel, replica_group, op, channel_ids=[0])`** [doc] is the fused primitive worth understanding in detail, because it is the building block of overlapped ring collectives. Each rank reduces its local sources with `op(srcs0[i], srcs1[i])`, **sends the result to its successor, and receives its predecessor's reduced result** into `dsts[i]` — combining permute and reduction in one operation. Since sources and destinations are implicit in the ring, `collective_permute_implicit_current_processing_rank_id()` tells you whose data you are currently holding [doc].

**Channels** [doc]: multiple channels enable **overlapping communication with concurrent data transfers**. The maximum is **4 channels for replica groups containing all devices inside a node, and 2 for other supported replica groups**. Each channel's inner list must contain **exactly one tensor** — coalescing is not supported on this API.

Channel count is a directly actionable knob [derived]: with a single channel, ring steps serialize; with 4, you have four concurrent transfers in flight and can hide roughly 4× more communication behind compute. If you are writing a hand-rolled ring collective and using one channel, you are leaving up to 4× on the table — but only if your replica group spans a whole node.

**LNC context** [doc]: `nki.collectives` arrived alongside LNC multi-core support (LNC=2), letting kernels leverage multiple NeuronCores within a logical core. The compiler tracks `shared_hbm` tensors and canonicalizes LNC kernel outputs; `private_hbm` declares tensors private to a single NeuronCore. Getting `shared_hbm` versus `private_hbm` right is a correctness matter first and a performance matter second — a tensor that should be private but is shared creates cross-core traffic you never asked for.

### 8.5 CC/DMA contention — the failure mode that surprises people

This is the most operationally important thing in this section, and it is documented with unusual specificity.

**The mechanism** [doc]: the Neuron compiler generates PseudoDmaTrigger (PDMAT) instructions and descriptors in the NEFF; the runtime translates them into hardware WRITE operations and constructs hardware DMA rings. **DMA engines process descriptors from up to 16 DMA rings in HBM using round-robin arbitration, and arbitration transitions between rings only at packet boundaries.** Therefore: **DMA rings with smaller packet sizes are more susceptible to resource starvation, and increasing packet size elevates processing priority.**

**The control** [doc]: `NEURON_RT_DBG_DMA_PACKETIZATION_SIZE` sets packet size during DMA ring construction. The **default is 4 KiB, the empirically determined minimum for DMA/HBM efficiency.**

Two things follow. First, that 4 KiB default is the *same* number as the DMA saturation point in §1.5 — the whole system is tuned around a 4 KiB granularity, and transfers below it are penalized twice: once in raw bandwidth, once in arbitration priority.

Second — and this is the counterintuitive part [derived] — **because arbitration only switches at packet boundaries, a ring with large packets can starve a ring with small packets.** Your carefully optimized compute DMA and a background collective are competing rings. If the collective uses larger packets, it wins arbitration, and your compute stalls waiting for operand loads even though "the collective is supposed to be overlapped." The static prioritization mechanism the docs describe is exactly this knob used deliberately [doc].

**The documented scheduling pathology** [doc] is worth quoting in structure because it generalizes: current compiler heuristics schedule FSDP AllGather operations **collectively at the earliest possible execution point**, since they depend only on subsequent computation within their layer. This **creates resource contention with critical TP communication operations, decreasing end-to-end performance even when multi-stream CC is available.** The better approach is to **proactively perform FSDP AllGather for a given layer during execution of the preceding layer** — and performance analysis confirms FSDP AllGather for attention layers then overlaps with computation in the preceding MLP layers, specifically in the window between TP AllGather and ReduceScatter [doc].

The general lesson [derived]: **"issue communication as early as possible" is the wrong heuristic when multiple collectives contend.** What you want is communication placed in a *specific* compute window where the resources it needs are idle — which is a scheduling decision only you can make, because it depends on the structure of the layer sequence.

### 8.6 Overlap patterns

**Pattern 1 — Fine-grained ring with double buffering.** The NKI Library's `fine_grained_allgather` kernel is the reference implementation [doc]: fine-grained ring-based all-gather across ranks along the row dimension, using **ring-based collective permute with double buffering to overlap communication and data movement**, with **both SBUF and HBM communication paths and automatic selection based on tensor size** [doc].

Signature and constraints [doc]: `fine_grained_allgather(lhs, tp_degree, num_groups, force_hbm_cc=False)`; `lhs` is `[m, K]` row-sharded across ranks; `tp_degree` must be **even**, supported values **4, 8, 16, 32, 64, 128**; `M` must be divisible by `(RANK_N * LNC_N * CHANNEL_N)`; **TRN2 only** at time of writing.

Note that the automatic SBUF/HBM path selection is the §8.4 tradeoff being made for you by tensor size, with `force_hbm_cc` as the manual override. That divisibility constraint on M is also a real tiling constraint that must propagate back into your kernel's shape choices.

**Pattern 2 — Fused collective + compute (FGCC).** The **FGCC** kernel implements **fused all-gather and matrix multiplication** — "Fine-Grained Gather Collective Compute" — enabling **ring-based communication with compute overlap on Trn2** [doc].

This is the strongest form of the technique [derived]: rather than overlapping a collective with *unrelated* compute, decompose the collective into ring steps and consume each arriving shard with a matmul immediately. Step *i*'s matmul runs while step *i+1*'s shard is in flight. For TP AllGather → matmul — the single most common pattern in tensor-parallel inference and training — this converts the collective from added latency into something largely hidden behind the GEMM it feeds. The general recipe:

```
for step in ring_steps:
    launch collective_permute for shard(step+1)     # CC-Cores
    matmul( shard(step), weights )                  # TensorE
    # accumulate into PSUM across steps (§5.4 drain rules still apply)
```

**Pattern 3 — SBUF-to-SBUF all-gather**, with **two variants for small and large tensors** [doc], avoiding the HBM round trip entirely for on-chip-resident data.

**Pattern 4 — Ring attention / context parallelism.** The NKI Library implements **ring attention forward using `attention_cte` with HBM I/O and online softmax reduction for context parallelism**, and **ring attention backward using collective permute operations** [doc]. Online softmax is what makes this work: it allows combining partial attention results as KV blocks arrive from ring neighbors without needing all of them present, so communication and attention compute pipeline naturally.

**Pattern 5 — MoE collectives.** The library adds kernels for **MoE training collectives** and **indexed gather/scatter** [doc], plus `Top-K Reduce` for **MoE output gathering with LNC sharding** [doc]. Expert parallelism is all-to-all-shaped, which is the hardest pattern to overlap; start from these rather than rolling your own.

### 8.7 Profiling collectives

Extending the §6.2 semaphore method:

| Signature | Diagnosis | Fix |
|---|---|---|
| Compute engines idle, CC active, no overlap | Serialized comm/compute | §8.6 patterns; fuse or pipeline |
| Compute stalled on DMA while collective runs | **Ring arbitration starvation** [doc] | §8.5; adjust packet size |
| Collective time ≫ `S/B` prediction | Latency-bound | Fewer/larger collectives; coalesce; consider RDH |
| Multiple collectives contending | Early-issue heuristic pathology [doc] | §8.5; place comm in a specific compute window |
| Single channel on a ring collective | Under-parallel transfer | Use up to 4 channels [doc] |
| Ranks arrive at collective at different times | **Load imbalance** — the collective shows the *slowest* rank | Fix the imbalance, not the collective |

That last row deserves emphasis [heuristic]: a collective is a synchronization point, so any per-rank compute imbalance upstream appears as collective time in the profile. Optimizing the collective is then pure wasted effort. Always check arrival skew across ranks before concluding communication is the bottleneck — this is the most common misdiagnosis in multi-core profiling.

`nccom-test` benchmarks collectives standalone, including fine-grained ones [doc]; use it to establish the achievable floor before attributing time to communication in a full kernel.

### 8.8 Multi-core checklist

- [ ] Collectives overlapped with compute, not serialized (§8.6)
- [ ] Fused collective+compute (FGCC-style) where the pattern is AllGather→matmul
- [ ] SBUF vs HBM path chosen deliberately; coalescing used when on HBM [doc]
- [ ] Up to 4 channels used for intra-node replica groups [doc]
- [ ] Packet-size contention checked when compute DMA stalls during collectives [doc]
- [ ] Collectives placed in a specific compute window, not issued as early as possible [doc]
- [ ] `shared_hbm` vs `private_hbm` correct for every cross-core tensor [doc]
- [ ] Rank arrival skew checked before blaming communication
- [ ] `nccom-test` floor established for the collectives in use
- [ ] Tiling satisfies kernel divisibility constraints (e.g. `M % (RANK_N·LNC_N·CHANNEL_N) == 0`) [doc]
---

## 9. Cross-Architecture: What Transfers from TPU/Pallas

TPU has been a production systolic-array machine since 2015, and its kernel-programming layer (Pallas, lowering through the Mosaic compiler) solves the same problems NKI solves. The architectures are close enough that a large fraction of TPU kernel technique transfers directly — and the places where they *diverge* are the most instructive parts, because they show you which of your NKI habits are fundamental and which are Trainium-specific.

This section is about stealing good ideas, not about porting code. §10 continues in the same spirit with the academic literature and a production case study. Everything here is labelled by transfer strength:

- **→ Direct** — the same technique, different API names.
- **→ Adapted** — the idea transfers, the mechanism differs.
- **→ Contrast** — TPU does it differently in a way that teaches you something about Trainium.

### 9.1 The architectural mapping

| Trainium / NKI | TPU / Pallas | Notes |
|---|---|---|
| Tensor Engine (128×128 PE array) | **MXU** (128×128 systolic array) | Same primitive, same size [doc-jax] |
| Vector Engine | **VPU** | Elementwise/reduction unit |
| GpSimd Engine | (scalar unit + XLU) | TPU splits differently |
| — | **XLU** | Dedicated transpose/permute unit; Trainium does transposes on TensorE |
| SBUF (28–32 MiB, 128 partitions) | **VMEM** (16 MiB+, up to 64 MiB on TPU7x) [doc-jax] | Both software-managed vector SRAM |
| PSUM (2 MiB, 8 banks) | (accumulation in VREGs / output refs) | **Trainium exposes accumulators explicitly; TPU does not** |
| — | **SMEM** + SREGs | Separate scalar memory — Trainium has `nisa.register_*` but no distinct scalar memory space |
| — | **VREGs** (8×128 for 32-bit) [doc-jax] | Trainium engines stream from SBUF; no user-visible vector register file |
| Partition dim (128) + free dims | Last two dims → **sublanes (8) × lanes (128)** [doc-jax] | *Both machines punish you for ignoring the trailing-dimension structure* |
| DMA engines (16/core) | DMA subunits | Same async prefetch role |
| CC-Cores, NeuronLink, 2D Torus | ICI, 2D/3D Torus | Same topology family |
| `nki.jit` → NKI Compiler → NeuronISA | `pallas_call` → Mosaic → VLIW | Both bypass the graph compiler |

The single most important structural difference: **NKI exposes PSUM as an addressable accumulator memory with explicit banks, and TPU does not.** Everything in §5.4 (drain balancing, bank budgeting) has no Pallas analogue — TPU accumulates into the output ref and lets Mosaic handle it. Conversely, **TPU exposes a vector register file (VREGs) and a scalar memory (SMEM) that NKI does not.** Each machine hands you a different slice of the hierarchy.

### 9.2 Kernel libraries worth reading

These are production, heavily-tuned kernels for the same problems you're solving. Reading them is the fastest way to absorb a decade of systolic-array technique.

**`jax/experimental/pallas/ops/tpu/`** — the reference collection in the JAX repo:

| Kernel | Why read it |
|---|---|
| `flash_attention.py` | The baseline fused-attention structure |
| `splash_attention/` | **Sparse** flash attention; block-skipping via masks. The most sophisticated attention kernel in the open |
| `paged_attention/` | KV-cache paging — non-contiguous page gather |
| `megablox/` (GMM) | **MoE grouped matmul** driven by host-built routing metadata |
| `flash_attention` backward | Recompute-vs-store tradeoffs in a fused backward pass |

**Tokamax** (`github.com/openxla/tokamax`) — an OpenXLA kernel library for **both GPU and TPU**, built on JAX/Pallas, providing state-of-the-art implementations *plus tooling to build and autotune your own custom kernels*. The autotuning tooling is the part worth studying; §9.6 covers why.

**MaxText** — production Pallas usage in a real LLM training/serving stack, including Splash Attention, paged/ragged serving attention, and Megablox GMM for MoE.

**The Ragged Paged Attention (RPA) paper and kernel** — a recent, unusually well-documented case study. Three innovations, all of which have NKI analogues: fine-grained tiling for raggedness, a **KV-update-fused pipeline** that hides KV cache update latency behind attention compute, and **distribution-aware compilation** dispatching specialized kernels by sequence-length regime.

**`jax-ml/scaling-book`** ("How To Scale Your Model") — not kernels, but the best available treatment of roofline reasoning on systolic machines. §9.7.

### 9.3 → Direct transfers

**Online softmax / flash attention.** The reason flash attention exists is IO-awareness, not GPU-specific tricks. A useful data point: naive attention on TPU v6e compiles to 10,245 VLIW bundles moving **526 MB** through HBM; Splash Attention with block_size=512 compiles to 1,651 bundles moving **14 MB** — a ~37× traffic reduction. The analysis of *why* XLA can't close that gap on its own is the transferable lesson: XLA fuses `QKᵀ + mask + reduce_max`, then `exp + reduce_sum`, then `normalize + SV` — real fusion — but **the attention matrix still gets written to HBM between fusions, because standard softmax is inherently multi-pass**. Compilers fuse along dataflow edges; they cannot restructure your algorithm into a streaming formulation.

That is exactly the argument for hand-writing NKI kernels, and it generalizes: **the wins available to a kernel author are algorithmic restructurings the compiler is not permitted to make.** Look for multi-pass algorithms that have a streaming reformulation.

**Double buffering.** Identical concept, identical payoff (§5.2). Pallas's distributed tutorial teaches it explicitly alongside bidirectional-bandwidth optimization and nested pipelining.

**Block-sparse skipping.** Splash Attention's causal/sparse block skipping — don't compute blocks the mask zeroes out — maps directly onto NKI: use `nisa.affine_select`/`range_select` and skip tiles entirely rather than computing and masking. The 2D grid gives the compiler visibility into the iteration pattern; in NKI the equivalent is expressing the skip in your loop structure so the instruction is never issued.

**Accumulate in FP32, inputs narrow.** Pallas guidance is `jnp.dot(..., preferred_element_type=jnp.float32)` even for bf16 inputs. Same principle as PSUM's unconditional FP32 accumulation (§1.3) — and the same warning applies in reverse on TPU: *32-bit operands get rounded to bf16 unless you explicitly request float32 precision*. Know your platform's default.

**MoE via host-built routing metadata.** Megablox GMM has the CPU build the token→expert routing plan, then the kernel consumes it. This sidesteps device-side data-dependent control flow entirely — directly applicable to NKI MoE kernels, and cheaper than `nisa.nonzero_with_count` gymnastics when the host can do the work.

**Distribution-aware kernel specialization.** RPA dispatches different kernels by sequence-length regime rather than writing one kernel that handles all cases adequately. In NKI, compile-time specialization is free — Python control flow resolves at trace time (§specialization), so *only the taken branch is included in the specialized program*. Write three good kernels, not one mediocre one.

### 9.4 → Adapted transfers

**The trailing-dimension tiling discipline.** This is the deepest structural parallel and the one most worth internalizing.

TPU rule [doc-jax]: computation happens on 2D vector registers of **8×128** for 32-bit values; the last two array dimensions map to sublanes and lanes. Consequences the JAX docs state explicitly:

- **Block shapes**: the last two dimensions must be **divisible by 8 and 128 respectively**, or equal the array's full dimension.
- **All vector computation is padded up to tile size.** Adding two 1×1 arrays costs as much as adding two 8×128 arrays. Adding two 8×128×1×1 arrays is **1024× as expensive** as two 8×128 arrays, because the shape pads to 8×128×8×128.
- Singleton dimensions in the last two axes are wasteful — they occupy one element of a full tile.
- Reductions, reshapes, and transposes involving the last two axes are expensive; on other axes they are free.

Every one of these has an NKI counterpart, and the underlying cause is the same — *a fixed hardware tile shape that your logical shape gets padded into*:

| TPU (8×128 VREG tile) | NKI (128 partitions × free) |
|---|---|
| Last two dims divisible by 8, 128 | Partition dim ≤128, ideally =128; free ≥512 elem (§1.4) |
| 1×1 op costs as much as 8×128 | A `[1,1,1][1,1,1]` access pattern costs a full instruction (§5.6) |
| Padding to tile makes tiny ops catastrophic | Small K/M leave PE rows/columns physically idle (§1.2) |
| Reductions on last two axes expensive | Cross-partition reductions expensive (§3.3) |
| Reshape free except on last two axes | Layout changes cheap except across partitions |

**The lesson that transfers**: on both machines, *the shape of your data relative to the hardware tile is the primary performance variable*, and cheap-looking operations on badly-shaped data are not cheap. The TPU docs quantify it more starkly than the Neuron docs do (1024× for a pathological shape), which makes them useful for building intuition you then apply to NKI.

**Explicit operation cost tiers.** The Pallas docs publish a cost table [doc-jax] that NKI's docs do not:

| Cost | Operations |
|---|---|
| 🟢 cheap | `+`, `-`, `*`, `max`, `min`, `where`, `abs`, bitwise, shifts, comparisons, type casts |
| 🌕 medium | `/`, `//`, `%`, `exp`, `tanh`, `pow` |
| 🔴 expensive | `sin`, `cos` |

Also: **the hardware generally only supports elementwise computation using 32-bit types**; lower-precision operands should be upcast before elementwise ops. Trainium differs here (VectorE's 16-bit performance mode, §5.7) — but the *category* ordering, transcendentals cost more than arithmetic and trig costs most, is architecture-independent and worth assuming until measured otherwise.

**Reduction and broadcast axis ordering** [doc-jax]: reductions over the **last** dimension are slowest, second-to-last faster, leading dimensions fastest. Broadcasting has the same profile. Adapted to NKI: the analogous hierarchy is free-dim reduce (cheap) → cross-partition reduce (expensive), and the practice is the same — *choose your layout so reductions land on the cheap axis* (§3.3, §5.6).

**Scalar prefetch for irregular access.** TPU places control data in SMEM and uses `PrefetchScalarGridSpec(num_scalar_prefetch=n)` so the first n arguments live in scalar memory and can drive block-sparse index computation. NKI has no separate scalar memory, but `nisa.register_alloc/load/move/store` and `VirtualRegister` fill the same role: **compute indices and control values in registers, not by round-tripping through a compute engine.** If you are burning VectorE instructions to compute offsets, this is the fix.

**Fusing transposes into the matmul.** `lax.dot_general` can fuse transposition of the last two dimensions into the operation [doc-jax]. NKI's analogue is `nl.matmul(..., transpose_x=True)` and the operand-role strategy of §3.3 — same goal, avoid materializing a transpose, different mechanism.

**Nested pipelining and bidirectional rings.** The Pallas distributed tutorial covers double-buffering, **bi-directional bandwidth optimization**, and nested pipelining while building `ppermute`, `all_gather`, `psum`, and `psum_scatter` from remote DMA primitives. Bidirectional ring is the notable transfer: on a full-duplex ring, sending both directions halves the step count. NKI's `collective_permute_implicit` is ring-shaped, and the channel mechanism (up to 4, §8.4) is how you express the concurrency — worth checking whether your replica group supports bidirectional flow before accepting `(R−1)` steps.

**Fused MoE with communication overlap.** A recent production case study: an SGLang-JAX Pallas kernel fusing scatter + expert FFN + gather for a 1T-parameter MoE, cutting prefill latency from 5.16 ms to 2.42 ms. The stated ideal steady state is precisely the multi-engine pipeline of §5.2 extended to collectives: *while the MXU computes expert i, HBM-DMA prefetches expert i+1's weights, ICI-out sends the next batch of routed tokens, ICI-in receives the previous batch of outputs, and the VPU handles scale and layout work from the prior matmul.* Translate MXU→TensorE, HBM-DMA→DMA engines, ICI→CC-Cores, VPU→VectorE and you have an NKI MoE kernel design. The article's diagnosis also matches §8.5's: once the work crosses multiple op or collective boundaries, **the graph compiler cannot reliably co-schedule interconnect DMA, memory DMA, matrix, and vector work into one hand-scheduled pipeline** — which is the argument for putting the whole thing in one kernel.

### 9.5 → Contrasts worth understanding

**Automatic vs manual pipelining.** This is the biggest philosophical divergence. Pallas's `pallas_call(grid=..., in_specs=[BlockSpec(...)])` *generates the pipeline for you*: you declare how to chunk inputs, and the compiler emits the HBM→VMEM prefetch, double-buffering, and overlap. NKI makes you build it by hand with `affine_range`, block dimensions, and `multi_buffer`.

Neither is strictly better — Pallas trades control for boilerplate reduction; NKI trades boilerplate for control. But the *ideas* embedded in BlockSpec are worth importing:

- **Declare the chunking, derive the loop.** Even in NKI, deciding your tile shapes first and mechanically deriving the loop nest from them produces cleaner kernels than the reverse.
- **Put the reduction axis last.** Pallas *requires* the reduction grid axis be the last one, because the output window doesn't vary along it, so the output ref can serve as the accumulator. The NKI analogue: your K loop should be innermost so a single PSUM bank accumulates across it (§4.3). Pallas encodes as a hard rule what NKI leaves as a convention you can violate.

**Sequential grid semantics.** TPU processes the grid **sequentially in lexicographic order**, which unlocks two things [doc-jax]: consecutive grid indices using the same input slice **skip the redundant HBM transfer**, and multiple invocations may write the same output slice **without race conditions** (provided they're consecutive). NKI's `affine_range` asserts the opposite — independence — to license overlap.

The transferable insight: **revisit-skipping is a real optimization you must do manually in NKI.** If your loop nest reloads the same stationary weight tile on consecutive iterations, that reload is pure waste; hoist it. Pallas gets this for free from sequential semantics; you have to see it yourself.

**Megacore / `dimension_semantics`.** Pallas opts into multi-core by annotating grid axes as `"parallel"` or `"arbitrary"`, with only parallel axes partitionable across cores. Two warnings from the docs transfer verbatim to NKI's LNC sharding: **partitioning a 2-core device often gives less than 2×** when body instances have highly varying cost (all expensive steps landing on one core leaves the other idle — the load-imbalance problem of §8.7), and the compiler **prefers partitioning leading grid axes of a size that is a multiple of the core count**.

**Transposes have a dedicated unit (XLU) on TPU.** On Trainium they consume TensorE, competing with your matmuls (§5.3). This makes transpose avoidance *more* important on Trainium than on TPU — a case where you should not naively copy TPU kernel structure.

**Explicit accumulator banks.** NKI's PSUM has no Pallas analogue. §5.4's drain balancing is genuinely Trainium-specific work with no TPU literature to borrow from — and conversely, TPU kernels won't warn you about it.

**Loop unrolling limits.** Pallas fully unrolls `fori_loop`/`for_loop` at compile time, so the docs advise keeping trip counts small [doc-jax]. NKI's `static_range` does the same, with `affine_range`/`sequential_range`/`dynamic_range` as the non-unrolled alternatives. Same hazard, more options on the NKI side.

### 9.6 Autotuning: the practice NKI users under-adopt

MaxText's Pallas guidance is blunt and worth adopting wholesale: **performance is a function of interacting hyperparameters, chiefly block shapes. Build a small harness to systematically run the kernel across block sizes, record throughput and latency for each, and let data — not rules of thumb — pick the winners.** For automation, they point at `tune-jax`; Tokamax ships autotuning tooling as a first-class feature.

This maps cleanly onto NKI. Your tunable space:

```
tile_M, tile_N, tile_K          — η_PE (§1.2), and K=512 vs 128 for MX (§4.1)
free-dim size                   — DMA efficiency (§5.6) and instruction efficiency (§1.4)
buffer count (multi_buffer)     — pipeline depth (§5.2)
engine assignment (engine=)     — drain balance (§5.4)
dtype                           — the ladder (§1.3)
PE tiling config (row, col)     — small-matmul occupancy (§3.2)
collective channels             — comm overlap (§8.4)
```

The §5.2 admonition that *choosing the tile size is the programmer's job and requires iterating with neuron-profile* is a statement that this space must be searched. Searching it by hand, one config at a time, is the default NKI workflow and it is leaving performance on the table. Build the harness. `nki.benchmark` gives you the measurement primitive; the loop around it is a few dozen lines.

### 9.7 Roofline discipline from the scaling literature

`How To Scale Your Model` is the best systolic-array performance text available, and two of its results are directly load-bearing for NKI work.

**On-chip residency changes the roofline.** VMEM bandwidth is roughly **22× HBM bandwidth**, so an MXU operation reading from VMEM needs an arithmetic intensity of only **10–20** to hit peak FLOPs — versus the hundreds required against HBM. The consequence stated in the book: *if we can fit our weights into VMEM instead of HBM, our matrix multiplications can be FLOPs-bound at much smaller batch sizes*, and algorithms with fundamentally lower arithmetic intensity can still be efficient.

Translate to Trainium [derived]: **weights resident in SBUF rather than streamed from HBM collapse the arithmetic intensity you need.** This is the strongest available argument for SBUF-residency as a *design goal* rather than an incidental optimization — and it becomes more important on Trn3, where §1.6 showed required intensity roughly doubling. Concretely: a decode kernel that keeps a layer's weights in SBUF across many token steps operates in a completely different roofline regime than one that reloads them.

**Critical batch size.** The book's inference chapter gives `B_crit` — the per-chip batch size at which you cross from memory-bound to compute-bound — as typically **120 or 240** on TPU, with the takeaway that any per-chip batch above it maximizes generation throughput, and that for MoE the HBM roofline increases by a factor of `E/k` (experts over activated experts), pushing the threshold to ~1920 tokens in their worked example.

The transferable method [derived]: compute *your* `B_crit` from Trainium's numbers rather than importing theirs. `B_crit ≈ (peak FLOPs) / (HBM bandwidth) × bytes-per-param`. Below it you are memory-bound and should optimize weight movement; above it you are compute-bound and §5.3 applies. On Trn3 with ~4× compute against HBM3e bandwidth, `B_crit` moves up — the same directional conclusion as §1.6, reached independently.

The book also flags a caveat that matches Neuron's own docs: the assumption that HBM reads perfectly overlap FLOPs is unrealistic because compilers are fallible, and **for sharded models, XLA often fails to efficiently overlap communication with the matmuls themselves**. That is the same failure §8.5 documents on Neuron, observed on a different stack — strong evidence it is intrinsic to the problem rather than a defect of either compiler.

### 9.8 A reading order

If you want to systematically absorb this:

1. **`scaling-book` Part 1 (Rooflines) and Part 2 (How to Think About TPUs)** — the conceptual foundation, ~2 hours.
2. **Pallas "Writing TPU kernels" details page** — the layout/tiling/cost rules of §9.4. Read it as a specification of *what a systolic machine punishes*.
3. **`flash_attention.py`, then `splash_attention/`** — baseline then sophisticated. Diff them mentally against your NKI attention kernel.
4. **`megablox` GMM** — if you touch MoE.
5. **Pallas distributed tutorial** — double buffering, bidirectional rings, nested pipelining; read alongside §8.
6. **The RPA paper** — a complete modern case study with an explicit fused-pipeline argument.
7. **Tokamax** — for the autotuning tooling patterns of §9.6.

A caution on transfer: the shared substrate is the 128×128 systolic array, software-managed on-chip SRAM, async DMA, and torus-connected collectives. The divergences — PSUM banks, VREG/SMEM, XLU, automatic vs manual pipelining, partition-dim vs sublane/lane structure — are where blind copying produces wrong answers. Use TPU material for *principles and algorithms*; use the Neuron docs for *mechanism and constants*.
---

## 10. Academic Foundations and a Production Case Study

Two bodies of work sit adjacent to NKI and are underused by NKI practitioners. The first is thirty years of systolic-array architecture research, which gives you a *vocabulary and a formal search space* for what you have been doing by intuition. The second is a recent, unusually transparent production kernel-optimization writeup from the SGLang-JAX team, which is the best worked example available of the methodology in §6 applied end to end. This section covers both and translates them into NKI terms.

Labels here: **[lit]** for peer-reviewed or arXiv literature, **[case]** for the SGLang-JAX/LMSYS case study, alongside the earlier **[doc]** / **[derived]** / **[heuristic]** / **[doc-jax]**.

### 10.1 The dataflow taxonomy — and where Trainium sits

The canonical classification comes from Eyeriss [lit], which categorized spatial accelerators by *which data structure each PE holds locally*, because that determines the reuse pattern and hence the energy:

| Dataflow | What stays in the PE | What streams | Optimizes |
|---|---|---|---|
| **Weight Stationary (WS)** | weights | activations + partial sums | weight reuse; minimizes weight refetch energy |
| **Input Stationary (IS)** | activations | weights + partial sums | activation reuse |
| **Output Stationary (OS)** | partial sums | weights + activations | psum movement; each PE accumulates a full output |
| **Row Stationary (RS)** | a row-strip of all three | — | balanced reuse; needs large per-PE register files |
| **No Local Reuse (NLR)** | nothing | everything | area; everything reads a global buffer |

WS and IS are together called **operand-stationary** [lit]: one operand matrix is programmed into the PEs while the other streams, with partial sums propagating to neighbors. In OS, both operands stream and each PE accumulates locally, so results are written to memory only after the whole computation completes — *reducing register-file accesses*.

**Where Trainium sits, and why it matters** [derived]: `nc_matmul`'s stationary/moving operand pair is a textbook **weight-stationary** array. But Trainium is a *hybrid*: partial sums do not merely propagate to a neighbor and exit — they land in PSUM, which supports in-memory read-modify-write accumulation across a chain of matmuls (§4.3). That accumulation-in-place is the defining property of **output-stationary** dataflow, implemented one level *outside* the array rather than inside the PEs.

This hybrid has a direct practical consequence. The academic literature's well-known WS weakness is that partial sums must move; OS's weakness is that both operands must stream. Trainium gets the WS benefit (each stationary weight is reused across the whole N-length stream) *and* the OS benefit (partial sums accumulate in place across the K loop without moving) — at the cost of a finite, scarce accumulator resource. **§5.4's drain balancing is the price of Trainium's hybrid dataflow**, and PSUM bank pressure is a structural consequence of the design point, not an implementation wart. That framing also explains why the problem worsens on Trn3 (§2.1): the array got 4× faster and the OS-side resource did not grow.

The taxonomy also names a choice you make per-kernel. §5.3's "weights-stationary versus activations-stationary depending on batch size" is choosing between **WS and IS at runtime**, and the literature's framing is useful: pick whichever operand has the higher reuse factor for your shape. At batch 1 decode, weights dominate reuse; at large batch prefill, activations may.

**Hybrid Stationary (HS)** [lit] — reconfiguring the dataflow per layer rather than fixing it — is exactly what a NKI kernel author does by choosing operand roles per operator (§3.3, §7.2). The literature validates that this is worth doing; NKI just makes you do it by hand.

### 10.2 Map-space exploration: a formal frame for what you're tuning

The most directly useful academic contribution is the formalization of the *mapping* (or *map space*) — the complete set of decisions in placing a workload on a spatial accelerator. The standard decomposition [lit]:

1. **Tile sizes** — bounds and aspect ratios of data tiles from each operand tensor, *per level of the buffer hierarchy*.
2. **Loop order** — the iteration order at each tiling level.
3. **Loop parallelization** — which tensor dimensions are spatially partitioned across PEs.

Choices (2) and (3) together are what the field calls **dataflow**; choice (1) is tiling. This is worth internalizing because it says something NKI's docs never state explicitly: **you are searching a structured space with three orthogonal axes, at every level of the hierarchy.** Mapped onto NKI:

| Map-space axis | NKI decision | Section |
|---|---|---|
| Tile sizes @ PE level | K, M, N per `nc_matmul`; PE tiling config | §1.2, §3.2 |
| Tile sizes @ SBUF level | tile shapes, block dims, buffer counts | §2.2, §5.2 |
| Tile sizes @ PSUM level | bank budget, accumulation grouping | §4.3, §5.4 |
| Tile sizes @ HBM level | DMA free-dim bytes, descriptor shape | §1.5, §5.6 |
| Loop order | K innermost for PSUM accumulation; stationary reuse ordering | §4.3, §5.3 |
| Loop parallelization | partition-dim assignment; SPMD/LNC sharding | §2.2, §8 |

The analytical frameworks are worth knowing by name even if you never run them, because their *cost models* are the formalized version of §1:

- **Timeloop** [lit] — describes architectures with a generic template, mappings in a loop-nest representation, and enumerates feasible mappings by combining workload and architectural constraints, then models data transfers and arithmetic per mapping. Supports exhaustive or random-sampling search.
- **MAESTRO** [lit] — a *data-centric* representation using directives to describe mappings compactly; analyzes reuse and emits 20+ statistics (latency, energy, throughput). Focuses on scheduling search for a *fixed* dataflow — which is the NKI situation exactly, since your dataflow is fixed by the hardware.
- **Interstellar** [lit] — uses Halide's scheduling language to analyze accelerators; coarse-grained energy model with heuristic search-space pruning.
- **ZigZag** [lit] — greater architectural-template flexibility and a wider space of valid mappings.
- **SCALE-Sim** [lit] — systolic-array-specific cycle modeling; explicitly models OS/WS/IS.

**What to take from this** [derived]: three things. First, the vocabulary — "I am searching a map space" is a more productive framing than "I am tweaking tile sizes." Second, the observation that **tile sizes must be chosen per memory level and the levels interact** — a fact §7.3 rediscovered empirically when narrowing dtype for TensorE broke DMA efficiency. Third, the search strategies: MAESTRO-style fixed-dataflow scheduling search is the right model for NKI autotuning (§9.6), and the literature's consistent finding that **incomplete schedule search leads to permanently suboptimal results** is a strong argument for building the harness.

A cautionary note on transfer [heuristic]: these tools model *convolutional* accelerators with per-PE register files and often FPGA/ASIC targets. Their absolute numbers do not apply to Trainium. The *structure* of the search space does.

### 10.3 SCALE-Sim's regime analysis, and why your intuition breaks at small sizes

A validation study of SCALE-Sim against real TPUs [lit] partitions GEMM shapes into three regimes with distinct behavior, which maps cleanly onto the η_PE model of §1.2:

| Regime | Matrix dims vs array | Dominant effect |
|---|---|---|
| **Small** (32–128) | comparable to or smaller than array | **under-utilization; pipeline fill/drain dominate** |
| **Medium** (128–1024) | moderately exceeds array | steady-state systolic execution is the determinant |
| **Large** (1024–4096) | much larger | **tiling, accumulation, and scheduling decisions dominate** |

The reported prediction accuracy tells you where models are trustworthy: errors are **smallest in the medium regime**, where utilization is high and behavior matches the idealized systolic model. In the small regime, under-utilization and pipeline effects introduce greater relative variability. In the large regime, variance comes from **compiler tiling decisions, layout transformations, and memory bandwidth / on-chip buffering limits** — factors outside a compute-centric model.

This is a direct statement about the reliability of §1's cost model [derived]:

- **Medium regime**: trust η_PE. Your predictions will be close.
- **Small regime** (decode, small head dims, MoE expert slices): the formula still ranks options correctly but absolute predictions degrade. This is where PE tiling (§3.2) and profiling matter most.
- **Large regime**: η_PE stops being the binding term entirely — you are in §5.2 (pipelining), §5.6 (DMA), and §2.3 (allocation) territory. The model that predicts your kernel is the *movement* model, not the array model.

Knowing which regime you are in tells you which section of this primer to read.

### 10.4 The SGLang-JAX Ling-2.6-1T case study

This is the most valuable single artifact in this section: a complete, numerically transparent optimization of a fused MoE kernel on TPU v7x, from a naive-but-fused V1 to a V2 that **cut MoE prefill latency 53% (5.16 → 2.42 ms)** [case]. Everything in it transfers structurally to NKI. The setup: Ling-2.6-1T (1T sparse MoE, 63B activated, 256 routed experts, top-8 routing plus one shared expert, per-channel fp8 weights), 16 chips as 32 devices, `ep=32`, 2×2×4 torus.

#### 10.4.1 Build the lower-bound model before optimizing

Before touching the kernel they computed three independent lower bounds per device [case]:

```
Compute:   824.6 GFLOP / 2307 TFLOP/s              = 0.36 ms
ICI (fp8): 33.5 MB scatter+gather, hop-adjusted    ≈ 0.67 ms
HBM:       402 MB expert weights / 3.69 TB/s       = 0.11 ms  (×4 tiles ⇒ 0.44 ms)
```

The measured production trace was **2.42 ms — about 7× the compute bound**. The conclusion they draw is the whole methodology in one line: *"The optimization target is not to reduce FFN FLOPs. It is to hide token routing, weight prefetch, and fp8 reorder behind the routed compute window."*

**The NKI translation** [derived]: compute the same three bounds before optimizing any Trainium kernel — TensorE FLOPs (§1.2/§1.3), CC-Core bytes with a hop factor (§8.2), and DMA bytes (§1.5). The *ratio* between measured time and the max of those bounds tells you whether you have a compute problem or a scheduling problem, and the *identity* of the largest bound tells you what to hide behind what. The ICI hop-adjustment detail is worth copying exactly: on a 2×2×4 torus the average random destination is ~2 hops (0.5 + 0.5 + 1.0), which **doubles** the naive injection-bandwidth bound. Trainium's 2D torus (§8.3) needs the same correction, and skipping it makes your collective look inexplicably slow.

#### 10.4.2 Fusion creates a scheduling space — that is the point

Their framing of why a hand-written kernel was necessary [case]: once scatter, expert FFN, HBM weight movement, fp8 layout work, and gather cross multiple op or collective boundaries, the graph compiler cannot reliably place interconnect-DMA, memory-DMA, matrix, and vector work into one hand-scheduled pipeline. And crucially: ***"Fusion is not primarily about reducing op count; it creates a scheduling space where dependent stages can be manually arranged."***

That sentence is the best available justification for §5.2 and §8.6. The ideal steady state they name [case] — MXU computes expert *i*, HBM-DMA prefetches expert *i+1*'s weights, ICI-out sends the next token batch, ICI-in receives the previous outputs, VPU handles scale/layout from the prior matmul — is a **five-way** engine pipeline. Translated: TensorE, DMA, CC-out, CC-in, VectorE. That is precisely the seven-resource model of §1.1 being scheduled by hand.

#### 10.4.3 V1 → V2: tensor lifetime, not tile size

The single most transferable technical lesson [case]. V1 sliced the hidden dimension to fit VMEM, cycling through slices. V2 **changed tensor lifetimes**: routed tokens, gate/up intermediates, and the output accumulator stay *resident* in VMEM across the FFN loop, while only the weights stream through double buffers.

The measured structural deltas:

| Per expert | V1 | V2 | Effect |
|---|---|---|---|
| FFN1 dot | effective K ≈ 256 | fp8 chunk K ≈ 2048 | **K ~8× larger** |
| W2 output | narrow slice (`bd2=512`) | ~4096 hidden channels | **N ~16× larger** |
| Token staging | 128 small stagings | ~4 full-hidden stagings | **~32× fewer** |
| FFN2 accumulator | partials spill/reload through HBM | accumulates in VMEM | **HBM read-modify-write mostly disappears** |

They also note explicitly that **simply increasing V1's tile sizes would not work**, because larger hidden tiles also enlarge weight buffers, token staging, and partial-output staging, hitting the VMEM ceiling — and V1 would still cycle through hidden slices without making tokens and accumulators resident.

**NKI translation** [derived]: this is §5.3 (bigger K and N) and §5.4 (stop spilling accumulators) achieved *not* by tuning tile parameters but by **re-deciding which tensors live in SBUF for the duration and which stream**. Ask of any NKI kernel: *which of my tensors are resident, which stream, and is that assignment right?* The pathology V1 exhibits — a partial-output accumulator round-tripping through HBM — has an exact Trainium analogue in evicting PSUM to SBUF and reloading it, or in spilling an SBUF accumulator to HBM because the allocator ran out of room. The fix is the same: make the accumulator resident, stream the weights.

Note also that this is §9.7's arithmetic-intensity argument in production form: on-chip residency of the *accumulator and tokens* rather than the weights, but the same principle — keep the thing you touch repeatedly on-chip, stream the thing you touch once.

#### 10.4.4 Quantization granularity is a kernel-structure decision

A subtle and highly transferable point [case]. With **per-block** fp8 quantization, the scale depends on the K block:

```
out[m,n] = sum_k A[m,k] * W[k,n] * scale[block(k),n]
```

The scale cannot be hoisted out of the reduction, so **K must be split into blocks**: each block does a small dot, multiplies by its scale, and accumulates — turning one large GEMM into many small GEMMs with vector work interleaved. With **per-channel** quantization:

```
out[m,n] = (sum_k A[m,k] * W[k,n]) * scale[n]
```

the scale applies *after* the reduction, preserving the full-K dot.

**This is a first-order NKI concern** [derived], and arguably more severe on Trainium. Splitting K into blocks directly attacks the (K/128) — or (K/512) for MX (§4.1) — term in η_PE, *and* inserts VectorE work between matmuls that breaks the accumulation chain of §4.3, *and* adds PSUM pressure. A quantization scheme chosen for accuracy reasons by the model team can cost you the entire contraction-dimension optimization. **Check your MX/FP8 scale granularity before tiling K**: per-channel (or per-tensor) scales let you use the full contraction depth; per-block scales along K do not.

#### 10.4.5 Reduce bytes before optimizing the collective

Activation quantization from bf16 to fp8 before scatter halved the routed payload, and the in-kernel scatter stage fell from **1.39 ms to 0.65 ms** [case] — matching the ICI lower-bound prediction almost exactly.

They also tested the obvious algorithmic alternative and it *lost*: **flat all-to-all beat hierarchical all-to-all** (fp8: 1.34 ms vs 1.88 ms), because relaying the same payload through multiple stages adds staging buffers, synchronization boundaries, and **nearly doubles total bytes moved**. Their conclusion [case]: *"The practical lever on the communication side is therefore not a more complex routing algorithm, but fewer bytes and better overlap."*

**NKI translation** [derived]: before reaching for RDH-vs-Ring algorithm selection (§8.3), quantize the payload. Halving bytes is a guaranteed ~2× on the transfer term; a cleverer algorithm may not be. And for collectives that are bandwidth-bound rather than latency-bound, hierarchical decomposition can be actively harmful.

#### 10.4.6 Overlap the local work that doesn't need communication

The shared expert costs **0.159 ms of compute but adds only 0.068 ms (2.7%) to the critical path** [case], because it needs no cross-chip dispatch — all its tokens are local — so it runs inside the scatter window.

**NKI translation** [derived]: audit your kernel for work with no collective dependency and schedule it *into* the communication shadow. In a Trainium MoE or TP kernel, the shared-expert path, RoPE, norms, and quantization-scale computation are all candidates. This is §8.6's overlap principle applied to compute you already have rather than compute you restructure.

#### 10.4.7 The LLO instruction histogram: the most instructive table here

After all the overlap work, the routed compute window was still ~68% of total latency — but the bottleneck was **not** matmul. Dumping the compiler's low-level instruction stream gave [case]:

| Instruction | Count | Role |
|---|---|---|
| `vselect` | 50,880 | sublane select / blend |
| `vbitcast` | 46,566 | fp8 sub-word reinterpretation |
| `vcombine` | 36,380 | sublane merge |
| `vpack_format` | 34,368 | MXU input packing |
| `slane` | 29,960 | sublane movement |
| `vunpack` | 25,600 | fp8 unpack |
| `matmul_data_format` | 25,600 | format conversion before MXU |
| `vrot` | 21,524 | lane rotation |
| `vmatres` / `vmatprep` | 17,408 / 10,240 | MXU drain / feed |
| `vslreplicate` | 6,032 | scale broadcast |
| **`vmatmul`** | **4,096** | **actual matrix multiply** |

**Roughly 4,096 real matmul instructions against ~300,000 instructions of layout, packing, and format conversion — a ratio near 1:75.**

This is the most sobering number in this document, and the generalizable lesson is [derived]: **in a heavily quantized kernel, the matrix unit is a minority stakeholder in the instruction stream.** All the η_PE optimization in §5.3 addresses the 4,096; the other 300,000 are §5.5 (fusion), §5.7 (vector performance mode), and layout decisions.

The NKI parallels are direct. Trainium's equivalents of that instruction population are `nisa.tensor_copy`, `nc_stream_shuffle`, `tensor_scalar` scale broadcasts, dtype conversions, and PSUM drain traffic. Two concrete implications:

1. **Count your instructions, not just your FLOPs.** The `neuron-profile` instruction view gives per-engine instruction counts; the ratio of matmul instructions to everything else is a diagnostic the Neuron docs never suggest computing, and it immediately tells you whether you are optimizing the right engine.
2. **Layout and quantization format choices dominate quantized kernels.** Their stated next step — align model quantization with hardware-native execution formats — is exactly the Trn3 MXFP8/MXFP4 argument (§4.1): using a format the array consumes natively eliminates the pack/unpack population rather than optimizing it.

#### 10.4.8 The tile sweep — empirical, non-monotonic, near a cliff

Their published sweep [case]:

| `bts` / `btc` | latency | VMEM |
|---|---|---|
| **160 / 80** | **2.42 ms** | 47 MB |
| 160 / 160 | 2.44 ms | 47 MB |
| 128 / 128 | 3.12 ms | 44 MB |
| 256 / 128 | 3.19 ms | 54 MB |
| 256 / 256 | 3.23 ms | 54 MB |
| 384 / 128 | **OOM** | 62 MB |

Three things worth noting [derived]. The optimum is **not** the largest tile that fits — 256/128 uses more on-chip memory and is 32% *slower*. The landscape is **non-monotonic**, so hill-climbing from a bad start can strand you. And the good configuration sits **one step from an OOM cliff**, which is why an automated sweep beats manual exploration: you need to probe the boundary, and probing it by hand is tedious enough that people stop early.

This is the empirical justification for §9.6. Note also they report VMEM alongside latency — track SBUF/PSUM occupancy in your NKI sweeps for the same reason.

#### 10.4.9 What was left on the table

Their remaining bottleneck list [case] is a useful checklist of things that survive good kernel work: ICI limited by torus topology and contention; HBM weight reads requiring double-buffering to hide; **fp8 packing and scale handling keeping the MXU waiting for data to take shape**; and VMEM capacity capping tile size and overlap-buffer count. Every one has a Trainium analogue, and the last two are the ones most likely to surprise a NKI author who has optimized only matmul shape.

### 10.5 The Trainium serving-stack context

For context on where hand-written NKI fits into a serving stack [doc]: NKI kernels are already in your path whether you write them or not — the Neuron compiler automatically inserts optimized NKI kernels for common operations during compilation, and models served via the vLLM Neuron plugin or NxD Inference already include them. The NKI Library recently expanded from 7 to 16 documented kernel APIs, with **FP8 and MX quantization support added across QKV, MLP, and Output Projection kernels** [doc]. SGLang has been brought to Neuron as well, in the third-party Mini-SGLang-Neuron project benchmarked against vLLM-Neuron.

The practical consequence [heuristic]: before writing a kernel, check whether the NKI Library already has it — those kernels implement this playbook and are maintained against new hardware generations. Hand-write when your fusion opportunity crosses boundaries the library kernels don't cross, which is precisely the §10.4.2 argument.

### 10.6 Consolidated additions to the method

What §10 changes about how to work:

1. **Compute three lower bounds first** — compute, interconnect (hop-adjusted), memory — and compare against measurement before optimizing anything (§10.4.1).
2. **Name your regime** (small / medium / large, §10.3) to know which cost model to trust and which section applies.
3. **Frame tuning as map-space search** over tile sizes × loop order × parallelization, *per memory level* (§10.2).
4. **Audit tensor lifetimes** — resident versus streaming — before tuning tile sizes (§10.4.3).
5. **Check quantization scale granularity** before choosing K tiling (§10.4.4).
6. **Reduce collective bytes before choosing a collective algorithm** (§10.4.5).
7. **Schedule communication-independent compute into the communication shadow** (§10.4.6).
8. **Compute your matmul-to-total instruction ratio** — if it's 1:75, matmul shape is not your problem (§10.4.7).
9. **Sweep tiles automatically, record on-chip memory alongside latency, and probe the OOM boundary** (§10.4.8).
## 11. Field Lessons from a Bring-Up and Optimization Campaign

Sections 1–10 are mostly *what the machine can do*. This section is *what actually happened* when the playbook above was applied end-to-end, over many sessions, to one large dense decoder-only transformer served on a single Trainium2 instance under a production inference stack.

Everything here is labelled **[field]**. Model identity, absolute latencies, and business targets are omitted deliberately; what is preserved is the mechanism, the failure mode, and the relative effect. The value is not the numbers — it is that several of these lessons are *not derivable* from the documentation, and three of them contradict conclusions a careful reader of §§1–10 would reasonably draw.

Shape conventions used below, so the lessons read concretely without identifying anything: `H` = model hidden size, `I` = FFN intermediate size, `L` = layer count (order 10¹–10²), `TP` = tensor-parallel degree, `B` = decode batch, and per-rank quantities written `I/TP` etc. The workload has two distinct regimes — a **context/prefill** pass over a long token block, and a **token-generation/decode** step processing one token per sequence — served by two architecturally different kernel families. That prefill/decode split drives much of what follows.

### 11.1 The single highest-yield pattern: library capability that is not wired up at your shape

**This produced more measured wins than every micro-optimization attempted, combined.** [field]

Production kernel libraries are written to be general. Their APIs accumulate optional parameters — fused normalization, fused positional embedding, native grouped-query handling, quantization modes — that a given caller may simply never pass. The caller is then *correct but slow*, and nothing in any profile says "you left a parameter unset."

Three independent instances, all found by reading the kernel's own signature against the caller's call site:

| Found | Mechanism | Effect |
|---|---|---|
| Fused positional embedding | The projection kernel accepted precomputed cos/sin caches and a `fused_rope` flag; the caller applied rotation as a *separate* pass afterwards | removed one full elementwise pass per layer on the prefill path |
| Fused pre-normalization | Both the projection and FFN kernels accepted a `norm_type` + gamma parameter; the caller ran normalization as a standalone framework op before each call, `2·L` times per forward | removed the standalone norm from the decode critical path |
| Native grouped-query attention | The attention kernel supported `batch_q=G, batch_kv=1` internally; the caller materialized `k.repeat_interleave(G)` / `v.repeat_interleave(G)` before the call | removed a G-fold physical replication of K/V |

The third is the most instructive because the waste was in *framework glue*, not in any kernel, and no kernel-level profile would ever attribute it. A caller replicating a tensor G-fold so that a kernel which already handles the broadcast internally can consume it is pure data movement that exists only because two layers of the stack each assumed the other wouldn't do it.

**The generalizable procedure** [field]:

1. For every kernel on your critical path, read its **full signature and docstring** — not the example call.
2. Diff the parameter list against what your caller actually passes. Every defaulted parameter is a question: *is the default right for me, or merely right for someone?*
3. Pay special attention to parameters that fuse an adjacent operation (norm, activation, positional embedding, quantization, residual add) or that declare a structural property of your data (head grouping, layout, transposition, cache format).
4. Look immediately *before and after* the call for framework ops that the kernel could absorb. A `repeat_interleave`, `transpose`, `reshape`, or elementwise op adjacent to a kernel call is a candidate for elimination, not a fact of life.

This is the practical form of §3.1's fusion argument, but pointed at a different target: not "fuse these three `nl` ops into one `nisa` op," rather "the fused instruction already exists inside a kernel you are already calling, and you are not asking for it."

### 11.2 Fusion can regress — the new-synchronization-boundary failure mode

**This contradicts the natural reading of §3.1 and §5.5, and cost a full measurement cycle to discover.** [field]

The fused-normalization lever above was applied uniformly to both regimes. Decode improved. **Prefill regressed by ~15%** — far more than the norm had cost in the first place.

The mechanism [field, mechanism inferred from profile structure]: in the unfused baseline, the standalone normalization sat in a window where the compiler could schedule it against surrounding collective and DMA work. Pulling it *inside* the kernel created a hard ordering constraint — the preceding collective had to fully complete before the kernel (and therefore its internal norm) could begin. The fused version removed exposed elementwise work and added a serialization point, and at prefill's shapes the serialization cost more.

Three durable lessons:

- **Exposed-time ranking identifies removal *candidates*, not outcomes.** A profile telling you an operation costs X ms of exposed time is telling you the upper bound of what removing it can save. It says nothing about what the removal *introduces*. Fusion changes the dependency graph, and §1.1's `+ stalls` term is a property of that graph.
- **Gate per regime, not per kernel.** The fix was not to abandon the lever but to apply it to the decode path only. Any optimization whose benefit depends on surrounding schedule slack should be independently switchable per execution regime, because prefill and decode have completely different slack profiles.
- **A lever that helps one regime and hurts another will read as "no change" if you only measure aggregate throughput.** Measure both regimes separately, always.

### 11.3 Composition is not additive, in both directions

Levers were measured individually and then stacked. Neither naive assumption held. [field]

**Sub-additive.** Two prefill levers touching nominally different sub-kernels (one in the projection, one in attention) measured −A and −B alone; stacked they delivered noticeably less than −(A+B). The most likely reading is that both were consuming the same pool of compiler scheduling slack in the same region — the first one to run claims it, and the second finds less to reclaim.

**Super-additive / strictly conditional.** A weight-buffering fix measured **≈0% standalone** and delivered a solid gain **only when a second, unrelated-looking lever was also enabled**. The second lever enlarged the kernel's instruction graph enough that a pre-existing stall became *fully exposed*; below that threshold the compiler's own scheduling slack had been hiding it. Measured alone, the fix looked worthless and would have been discarded.

**Consequences for method** [field]:

- **Never discard a lever on a standalone null.** Re-test it in combination with the other levers you intend to ship. Stalls that are hidden by slack become real when slack is consumed elsewhere.
- **Never ship a stack you have only measured as parts.** Measure the exact combination you intend to enable.
- Report both the individual and the combined number. The difference is information about your schedule, not measurement noise — though check the noise floor before interpreting it (see §11.15).

### 11.4 Buffer aliasing and the WAR barrier

The most mechanically transferable single finding of the campaign. [field]

A fused FFN kernel loaded its two parallel projection weights (gate and up) through a shared **ring buffer of weight tiles**, with the buffer depth capped by a constant of the form `min(4, available)`. At the subject shape each projection needed 3 tiles, so:

```
required per projection = 3
ring capacity           = 4
gate consumes tiles     [0, 1, 2]
up's base index         = 3 % 4 = 3
up consumes tiles       [3, 0, 1]   ← 0 and 1 ALIAS gate's tiles
```

Because `up`'s DMA would overwrite tiles that `gate`'s matmul was still reading, the compiler inserted **write-after-read barriers**. In a DMA-bound kernel that is a straight pipeline bubble: the weight-DMA stream stalls waiting on TensorE reads.

Raising the cap to `2 × required` gave the two projections disjoint address ranges, eliminated the barriers, and yielded a real throughput gain — at the cost of running the on-chip buffer to ~98% occupancy with only a few KiB spare.

**Generalization** [field]: any time two consumers share a ring buffer, the safe depth is **`2 × max_required_per_consumer`**, not a fixed constant. A cap that is merely ≥ one consumer's requirement silently aliases as soon as a second consumer's base index wraps into the first's live range. Check this explicitly for gate/up-style parallel projections, double-buffered weight streams, and any producer/consumer pair sharing a modulo-indexed pool (§2.3's `mod_alloc` is exactly this construct made explicit).

The profiler signature is §6.4's "DMA and TensorE strictly alternating" — but the *cause* here is not a missing buffer count in your code, it is an aliasing constant buried in a library's tiling-parameter module. Worth grepping for.

### 11.5 Read bandwidth correctly: burst rate, not average rate

A profile reported ≈**407 GB/s** achieved against a ≈716 GB/s peak — 57%, comfortably inside §5.6's "<60% ⇒ fix your access patterns" trigger. Considerable effort went into descriptor and layout work on that basis, and **all of it was null**. [field]

The resolution: DMA was only **active 66%** of the step. During active bursts the actual rate was

```
407 GB/s ÷ 0.66 ≈ 616 GB/s ≈ 86% of peak
```

The transfers were already near-optimal. The deficit was not bandwidth efficiency at all — it was **DMA-inactive time**: inter-kernel gaps, collective waits, and cross-kernel serialization. No descriptor, burst-size, or layout change could have touched it, and indeed none did.

**The correction to §5.6's diagnostic** [field]:

```
average_BW   = bytes / total_time          ← what tools usually report
burst_BW     = bytes / DMA_active_time     ← what tells you if transfers are efficient
```

Compute **both**. Only `burst_BW` diagnoses access-pattern quality; the gap between them measures scheduling, not efficiency. A kernel at 57% average and 86% burst is a **§5.2 pipelining problem wearing a §5.6 costume**, and the two fixes have nothing in common. This single confusion consumed more wasted effort in the campaign than any other.

Corollary: apply the same decomposition to every engine before concluding it is saturated. "60% busy" is meaningless without knowing whether the other 40% is idle-waiting or genuinely unneeded.

### 11.6 Quantized collectives: concat-shaped yes, reduce-shaped no

Narrowing a collective's payload dtype is the highest-leverage collective optimization available (§10.4.5: halve the bytes, halve the transfer term). It worked — but only for one of the two collectives in the layer. [field]

| Collective | Shape of the operation | FP8 payload |
|---|---|---|
| AllGather | **concatenation** — bytes are moved and placed, never combined | **worked**; measurable win, bit-exact output |
| ReduceScatter / AllReduce | **summation** — the engine must perform arithmetic on the payload | **failed**: produced NaN, and the compiler rejected the narrow float type outright for this op |

The rule [field]: **a collective can carry any dtype it merely *moves*, and only supported-arithmetic dtypes for values it must *combine*.** Gather/permute/all-to-all are movement; reduce-family collectives are compute. Check your interconnect's supported reduction dtypes before designing a quantized-collective scheme, and expect the asymmetry to be permanent rather than a gap to be filled.

This also caps the technique: in an `AllGather → matmul → ReduceScatter` layer the AllGather half can be narrowed and the ReduceScatter half cannot, so the achievable saving is roughly half of what a naive bytes-halving estimate predicts.

### 11.7 Hard ISA and hardware limits worth knowing before you design around them

Three limits discovered by hitting them, each of which had shaped a design that then had to be abandoned. [field]

**Sub-byte-dtype transpose is not always bit-accurate.** §1.5 documents DMA transpose for 2-byte and 4-byte dtypes. A design fed **1-byte** quantized data through an on-chip transpose with an interleaved access pattern. It was **correct in functional simulation and produced garbage on device.** The fix was to keep the transpose in 16-bit and quantize *after* it.

Two lessons: (a) the 2-/4-byte scope in the documentation is a real boundary, not an incidental omission — treat 1-byte transposition as unsupported for layout-sensitive patterns unless you have verified it on hardware; (b) **functional simulation does not validate layout-sensitive or dtype-punning operations.** `nki.simulate_kernel` checks your arithmetic, not the hardware's data path. Any optimization touching transposes, packing, interleaving, or sub-byte types must be validated on device before you believe it.

**Transpose output free dimension is capped.** An attempt to widen an on-chip transpose's output free dimension beyond 128 was rejected as a hardware constraint. Transposes do not obey the "grow the free dimension toward 512" rule that applies to matmul moving operands — plan tiling around the narrower cap.

**Quantization scale granularity constrains K tiling.** Independently rediscovered, exactly as §10.4.4 predicts: per-tensor/per-channel scales permit a full-depth contraction; a scale that varies along the contraction axis forces K to be split and interleaved with vector work. **Confirm scale granularity before designing your K loop**, because this is a model-side decision that silently sets a ceiling on your η_PE.

### 11.8 Getting an in-kernel collective to actually execute

§8.4 and §8.6 describe in-kernel collectives as the route to real compute/communication overlap. Making one run inside a compiled inference graph took three separate failures to diagnose, and the resolution generalizes. [field]

Two prior attempts had established a belief — held for several sessions and written into internal documentation — that in-kernel collectives were simply **not viable on this stack for inference**. One fused collective+matmul kernel was rejected at graph-execution time with an "invalid schedule" status; one standalone ring collective **deadlocked on device**, hanging until killed. Both failures were real and reproducible.

**That belief was wrong.** A minimal in-kernel all-reduce was eventually made to run correctly across all ranks, verified numerically against a reference. The two earlier failures were *specific integration bugs*, not a platform limitation. Three requirements, all of which must hold simultaneously [field]:

1. **Stage collective I/O through an internal shared buffer.** A tensor that is simultaneously a graph input/output *and* a collective operand is rejected. Copy from the graph-level tensor into a kernel-internal shared buffer, collective on that, copy out. This is the user-space form of the "use intermediate I/O for collectives" flag that exists elsewhere in the stack.
2. **Bootstrap the collective communicator before first graph execution.** Absent this the collective's init fails with an opaque "unknown failure" status. In a production serving stack this bootstrap usually already happens for the framework's own collectives — verify and reuse it rather than duplicating it with a hardcoded endpoint.
3. **Define the kernel in its own module file.** A kernel defined inline in the harness failed to resolve module-level imports (the replica-group symbol) — a tracer closure limitation, not a collective issue.

**The meta-lesson is the important one** [field]: an *unexplained* failure — especially a hang or an opaque status code — is not evidence that a capability is unavailable. It is evidence that you have one unresolved integration bug. Two such failures, from two different mechanisms, were enough to establish a false consensus that then went unchallenged for weeks. Budget one deliberate, minimal, cheap reproduction *before* concluding a documented capability doesn't work: the decisive experiment here was a few dozen lines that reduced a small tensor across ranks and checked the sum, and it invalidated weeks of accumulated assumption.

### 11.9 Making in-kernel writes to persistent state propagate

A fused multi-layer kernel must update persistent state (an attention KV cache) in place. Getting those writes to survive to the next invocation is a framework-graph problem, and it has sharp edges. [field]

The framework provides an aliasing pass that surfaces kernel-declared operand↔output aliases as graph-level input/output aliasing, so the runtime reuses the input buffer and in-place writes persist. It works — it correctly detected all `2·L` cache mutations in one attempt. But three constraints govern whether it fires:

**Basic indexing preserves the alias chain; advanced indexing does not.** The pass follows an alias chain from the kernel argument back to a root graph input, and it recognizes view-producing ops — `select`, `slice`, `narrow`, `unbind`, `split`, `view`, `reshape`, `squeeze`, `unsqueeze`, `expand`, `flatten`, `as_strided`. It does **not** recognize tensor/advanced indexing — correctly, because `t[index_tensor]` is a genuine copy in the host framework's semantics, not a view. A kernel handed an advanced-indexed tensor writes into a copy, and the writes vanish silently at the next step.

The observable symptom is distinctive and worth memorizing: **the first few generated tokens are correct, then output diverges and often falls into a short repeating cycle** — the model is reading only the state that existed before the first in-kernel write. A short correctness check passes; the bug lives past token ~3. **Any correctness test for persistent-state changes must generate enough steps to exercise state accumulation** — a handful of tokens is not enough.

Also breaking the chain: `stack`, `clone`, `contiguous`, and any op that materializes.

**The alias must match the root tensor's shape exactly.** Attempting to alias a `[N, …]` slice of a `[L, …]` pool failed with an element-count mismatch: the pass resolves the alias to the **root** tensor, and the graph-level aliasing then requires exact shape agreement. So sub-slice aliasing of a larger pool is structurally unavailable. If you want per-group aliasing, allocate **per-group pools whose shapes match what the kernel receives**, rather than slicing one big pool.

**Multiple writers to one root are handled — check that they are.** The pass implements a "latest write" cursor that chains successive writers (`w₀ → w₁ → … → wₙ`) and rewires downstream readers to the most recent write, so multiple kernel invocations aliasing the same root serialize correctly. Verify this fired by inspecting the emitted alias map rather than assuming.

**Verify the alias map, don't infer it.** In one attempt the persistent pool was allocated correctly and logged correctly, yet graph I/O accounting showed the pool counted **twice** (once as input, once as output) — i.e. the alias had *not* collapsed the pair, and total I/O nearly doubled. The alias map contained the wrong number of entries pointing at unexpected indices. **Read the emitted alias map and the compiler's I/O accounting; a pool that is allocated and passed is not necessarily aliased.**

Root-cause note with wide applicability: the reason the unified pool initially failed to take effect at all was that it was allocated on one object in the module hierarchy while the code path that used it tested for the attribute on a *different* object — so the intended path silently never executed and fell through to the per-tensor path. Add an explicit assertion or log that the intended path was taken. A silent fall-through to a working-but-slower path is nearly undetectable, and here it masqueraded as an out-of-memory failure.

### 11.10 In-kernel loop fusion: the accumulator you didn't budget for

Fusing `N` transformer layers into one kernel by looping inside the kernel ran out of on-chip/graph memory in a way that is not obvious from §2.1's capacity table. [field]

At `N` = full layer count, the compile failed with peak memory far above the device limit, decomposed as a large **I/O** term *plus* a large **internal scratchpad** term. The scratchpad scaled with the loop trip count: the compiler pre-allocated intermediates for **all** iterations rather than reusing buffers across them.

Two consequences [field]:

- **In-kernel layer fusion does not scale by simply raising the trip count.** Expect scratchpad to grow with iterations until the compiler's buffer reuse across loop iterations improves. Plan for a *group* size, tuned empirically, not "fuse everything."
- **Grouping interacts with §11.9's shape-match constraint.** Fusing in groups of `N` while aliasing slices of one `L`-sized pool is exactly the combination that the aliasing pass rejects. The coherent design is **per-group pools** whose shapes match the group kernel's operands — which simultaneously satisfies the alias constraint and reduces per-kernel scratchpad. That the two constraints have a common solution is not obvious from either one alone.

More generally: when a fused design fails on memory, **decompose the reported total into I/O versus scratchpad before reacting.** They have different causes and different fixes, and optimizing the wrong term is free of effect.

### 11.11 Compute/collective overlap: what the compiler will and will not do for you

§8.5 documents scheduling pathologies around collectives. The campaign's measurements sharpen the picture for *inference* tensor parallelism specifically. [field]

Measured: **essentially zero overlap between the tensor engine and collective execution** — the two were serialized, with collectives accounting for a substantial fifth of the decode step and about a third of prefill. An independently captured profile put the decode step at roughly **4× the largest of its three lower bounds** (§10.4.1's method), confirming a scheduling problem rather than a bandwidth or compute problem.

Four independent layers of blocking were identified [field]:

1. **The scheduler will not interleave compute with an in-flight collective** for this class of graph, at any available optimization setting. Multiple scheduler flags were tried; none broke the true `AllGather → matmul → ReduceScatter` dependency chain.
2. **Reduce-family collectives are *deliberately excluded*** from the compiler's fine-grained collective-fusion and hoisting passes — i.e. inference TP collectives are by design given no automatic overlap. This is worth knowing because it means waiting for a compiler upgrade is not a plan.
3 & 4. In-kernel collectives (the manual route) failed twice for integration reasons — **and those turned out to be fixable** (§11.8).

**The strategic conclusion** [field]: on this class of workload, compute/collective overlap is available *only* by placing the collective inside a kernel you control, where the intra-kernel scheduler can see both. Layers 1 and 2 above are not going to hand it to you. That makes §8.6's fused patterns not merely an optimization but the *only* mechanism — which justifies a substantially larger engineering investment in them than their position in §5's ordering suggests.

An honest note on outcome: even with the collective successfully running inside a kernel, the first working variant **regressed**, because the achievable split point forced an extra round trip through device memory to re-load the intermediate the collective consumed. Full-layer fusion (which removes that round trip) is the configuration that should win, and it remained blocked by §11.9/§11.10 constraints at the end of the campaign. **Partial fusion can be worse than no fusion**: if your fusion boundary forces a spill that the unfused version avoided, the overlap you gained may not pay for the traffic you added. Cost the round trip before choosing a split point.

### 11.12 Logical core configuration is a collective-topology decision

Platforms that let you present physical cores as one logical core (or not) appear to offer a simple sharding tradeoff. Measured, it is dominated by a second-order effect. [field]

Running at the finer-grained configuration — twice as many logical cores, each with half the resources, at twice the tensor-parallel degree for the same silicon — was substantially **worse** for the context/prefill pass, and roughly neutral for decode.

Attribution [field]: about two thirds of the regression came from **collective cost roughly doubling**, because the coarser configuration performs a free, implicit reduction *within* each pair of physical cores before any cross-core collective, and the finer configuration forfeits that and must move the data explicitly. The remaining third came from **matmul efficiency loss** — halving per-rank shard widths directly attacks the `M/128` term of §1.2.

Generalizations worth carrying [field]:

- **Doubling TP degree does not merely double collective count; it can remove a free local reduction.** Cost the topology change, not just the shard arithmetic.
- **Finer sharding shrinks per-rank tiles.** At the point where per-rank dimensions fall below the array's 128, you are trading collective bandwidth for η_PE and losing both.
- Enabling a non-default core configuration required patching several layers (a runtime-library behaviour, hardcoded grid constants at every kernel launch site, and compiler-argument propagation). **Non-default hardware configurations carry an integration tax that is easy to underestimate** — budget it before committing to an experiment, and gate it so the default path stays byte-identical.

### 11.13 Bucket boundaries, divisibility, and capacity cliffs

Three shape-related traps, all of which produced confusing symptoms before being understood. [field]

**Alignment of the model-length parameter had a ~2× effect on decode throughput.** A length that was not a multiple of a platform-preferred granularity produced roughly half the throughput of an aligned one. This was initially mistaken for a hardware ceiling and quoted as such. **Sweep alignment of every length/bucket parameter before reporting any ceiling.**

**Kernels impose divisibility constraints that propagate back into serving configuration.** A mask-generation kernel required the prior-context length to be a multiple of the partition size; certain context values therefore failed at graph-build time with an arithmetic assertion. Constraints of this class (§8.6 notes an analogous `M % (RANK·LNC·CHANNEL) == 0`) are not merely kernel-internal — they restrict the bucket set you may serve, and they must be discovered before you publish a configuration.

**Raising batch past the cache capacity limit collapses throughput.** Increasing decode batch beyond the point where the state cache could hold that many concurrent sequences caused per-step time to nearly triple. The scheduler was silently admitting fewer sequences than requested and contaminating the measurement. **Find and record your concurrency cap, and verify from the scheduler's own accounting that the batch you asked for is the batch you got.** Throughput-versus-batch is not monotonic, and the cliff is sharp — the same non-monotonic, near-a-cliff landscape §10.4.8 reports for tile sweeps.

Only a small set of TP degrees was even legal, constrained by head-count and sequence-length divisibility; of those, the smallest ran out of memory and the largest was throughput-poor. **Enumerate the legal configuration set from divisibility first**, then measure only inside it — this converts an open-ended search into a handful of experiments.

### 11.14 Correctness methodology for gated kernel optimizations

Kernel-level optimizations that are supposed to be mathematically equivalent need a correctness bar sharp enough to catch real bugs and calibrated enough not to reject harmless reassociation. What the campaign converged on [field]:

**Gate everything, default off, baseline byte-identical when unset.** This is the single most valuable discipline adopted. It permits aggressive experimentation on a shared tree, allows stacking combinations, and — critically — makes "is this change responsible?" answerable by flipping one variable. It also survived a long campaign in which many library files were edited: a periodic all-gates-off regression check repeatedly confirmed the default path was intact.

**Two-tier correctness.** A synthetic prompt of sequential token IDs with forced generation is a fast, stable regression signal, but it drives the model into a degenerate repeated-token regime where argmax ties are common — so a single flipped token there may be reassociation noise rather than a bug. Real-content prompts exercise a normal probability landscape. The bar used: **token-identical on real content**, with any synthetic-only divergence documented explicitly rather than hidden or hand-waved. One lever showed exactly this split — identical on all real prompts, one deterministic token difference on the synthetic degenerate tail — and reporting both is what made it trustworthy.

**Generate enough tokens.** §11.9's persistent-state bug appears only after several steps. Short checks are actively misleading.

**Distinguish "token-identical" from "coherent."** Coherent output is not evidence of correctness for a change that should be bit-exact. Say which you have.

**Determinism check before diagnosis.** Reproduce any divergence at least twice. A difference that does not reproduce is a race or environmental, and diagnosing it as a numerical issue wastes the cycle.

### 11.15 Experimental hygiene on shared accelerator hardware

Unglamorous, and it cost more wall-clock time than any technical problem. [field]

- **One device job at a time.** Concurrent jobs interfere and produce unreproducible numbers. If multiple agents or people share a box, use an explicit lock — and make it **self-healing**, because a job that dies while holding a lock blocks everything behind it. This happened twice, and in both cases the queued work waited for hours having done nothing.
- **A timeout wrapper kills its direct child, not the process tree.** Multi-process serving stacks leave orphaned workers holding the device. Always sweep for and kill survivors **by exact PID** after any timeout or kill. Never pattern-kill on a broad name — on a shared box that destroys other people's work.
- **Use a fresh cache root you own for a fresh build**, one per concurrent activity; never clear a shared compile cache (the vllm-neuron-parity plugin's P2). A stale-cache race produced spurious "kernel file does not exist" failures that look like code bugs. Equally: do not wipe a shared cache another job may be mid-build on.
- **Establish the noise floor before interpreting small deltas.** Repeated identical runs spanned enough to swallow a genuine 1–2% effect. Report sample counts and spreads, not single numbers. One lever's most reliable benefit turned out to be a *variance* reduction (an order of magnitude tighter spread), which a single-sample methodology would never have seen.
- **Read device time from profiles and wall time from harnesses, and don't mix them.** They differ by fixed overheads, and mixing them across comparisons produces phantom regressions.
- **Record the exact configuration with every number.** Two throughput figures for the "same" configuration differed materially because of differing context lengths and cache-budget fractions. Two numbers that are not comparable are worse than one number.

### 11.16 A bring-up test pyramid for kernels and ported models

Bringing a model onto this stack, the layered test structure that proved worthwhile [field], cheapest first:

| Level | Scope | Runs where |
|---|---|---|
| 1 | Unit: weight loaders (including quantized/sharded/transposed paths), positional-embedding tables, normalization | host/CPU |
| 2 | Module: attention and FFN blocks, compared against a reference implementation at **production width** | host/CPU |
| 3 | End-to-end logit validation against a golden reference | device |
| 4 | Task-accuracy benchmark with calibrated thresholds | device |

Findings from actually running it [field]:

- **Levels 1–2 ran entirely on host at full production width**, needing no accelerator. This is a large practical win when device time is contended: most correctness bugs are catchable without hardware.
- **At TP=1 the ported block was bit-identical to the reference; all divergence appeared only with tensor parallelism**, and grew with world size. The cause was **accumulation order in the reduced-precision cross-rank reduction**, not a model bug. Therefore: **error tolerances must scale with world size**, and a fixed tolerance either fails spuriously at high TP or is uselessly loose at low TP. Document the measured per-world-size numbers next to the tolerance so the next person knows which is which.
- **Fused megakernels often have no host-side fallback**, so the decode path may be untestable at levels 1–2 and must be covered by level-3+ or by generation-based checks (§11.14).
- A reference implementation from the model's own upstream library may be **unusable** (unregistered config, broken imports). Substituting the nearest architecturally-identical reference family is legitimate — *if you document the substitution*.
- Be honest about which levels actually ran. A pyramid missing its primary correctness gate is not a validated model, and recording that plainly is more useful than a green summary.

### 11.17 Consolidated method additions

What §11 adds to the method of §9.6 and §10.6:

1. **Audit kernel API surfaces against your call sites** before writing any new kernel code. Unset fused-operation parameters and caller-side data preparation the kernel could absorb are the highest-yield, lowest-risk wins available (§11.1).
2. **Compute both average and burst bandwidth.** Only burst rate diagnoses access patterns; the gap diagnoses scheduling (§11.5). Do this before acting on any "<60% utilization" signal.
3. **Measure every lever in both execution regimes**, and gate per regime. Fusion that helps one can hurt the other (§11.2).
4. **Re-test standalone nulls in combination**, and measure the exact stack you intend to ship. Hidden stalls surface when slack is consumed elsewhere (§11.3).
5. **Check shared ring-buffer depth against `2 × per-consumer requirement`** for aliasing-induced WAR barriers (§11.4).
6. **Verify emitted alias maps and I/O accounting**, and assert that intended code paths were actually taken. Silent fall-through to a slower path reads as a memory error (§11.9).
7. **Decompose memory failures into I/O versus scratchpad** before reacting (§11.10).
8. **Validate anything layout-, transpose-, packing-, or sub-byte-dtype-related on device.** Functional simulation does not cover it (§11.7).
9. **Treat an unexplained hang or opaque error as one unresolved integration bug**, not as evidence a capability is unavailable — and spend one cheap minimal reproduction to find out which (§11.8).
10. **Enumerate the legal configuration set from divisibility constraints first**, then measure inside it. Sweep alignment before quoting any ceiling (§11.13).
11. **Verify the batch you asked for is the batch you got**, and locate the capacity cliff (§11.13).
12. **Gate every optimization, default off, baseline byte-identical**, and re-verify the default periodically during long campaigns (§11.14).
13. **Establish the noise floor; report spreads and sample counts.** Track variance as a result in its own right (§11.15).
14. **Re-derive inherited quantitative claims from source.** Over one campaign, three separate documented claims — including two of the author's own, and one that had hardened into multi-session consensus — were falsified by reading the code. Prior work is a reliable record of *what was tried*; its *numbers and reasoning* decay. Cite or don't claim (§11.8, §11.5).
15. **Write the negative results down, with mechanism.** Roughly half the campaign's value was a well-evidenced map of what does not work and precisely why — which is what let later sessions skip guaranteed-null experiments instead of rediscovering them. A rigorous null with a cited mechanism is a durable asset; an undocumented one gets repeated.

---

## 12. Applied Cross-Reference: A Large Sparse-MoE Serving Program

§11 is one workload examined closely. This section is a *second* workload examined at arm's length — a much larger engineering program optimizing an open-weights ~120B sparse-MoE model (top-k routed experts in MXFP4, mixed sliding-window and full attention, disaggregated prefill/decode, block KV cache, prefix caching, speculative decoding) on a 64-core Trn3-class server, against a per-token cost target set by an 8-GPU comparator.

Its value here is threefold. First, it is an **independent corroboration** of several §11 findings on a completely different model class and hardware generation, which raises confidence that those mechanisms are architectural rather than workload-specific. Second, it reports several symptoms for which the earlier sections contain a **specific diagnosis the program had not yet applied** — and enumerating those is a concrete demonstration that the diagnostic method in §6, §8.7, §10.4.1 and §11.5 is worth running. Third, one of its load-bearing strategic assumptions turns out to have a **derivable problem** (§12.4) that follows directly from §4.1's MX contraction-depth mechanism.

Everything attributed to the program is **[prog]** — second-hand status, not measured here. Everything that reasons about it from documented primitives is **[derived]**.

### 12.1 The configuration, and why it stresses different parts of the machine

The reported shape [prog]: decode at tensor-parallel 8 × data-parallel 8–16 × expert-parallel 64–128, decode batches from 128 to 1024, prefill at TP2–TP8 with EP2–EP4, segmented prefill for long inputs, 128k maximum context served through context-length bucketing, and a mix of logical-core configurations (coarse for prefill, fine for decode, bridged by disaggregated KV transfer). Reported gaps against the comparator: decode roughly 3.2× off the price-parity target, prefill roughly 1.6× off, both measured at an 8k-input / 1k-output operating point.

Three structural differences from §11's workload, each of which moves the binding resource [derived]:

- **Sparse routing on wide expert parallelism.** With 128 experts at EP64–EP128, each rank owns one or two experts. Every layer therefore contains an all-to-all-shaped dispatch and combine — the hardest collective pattern to overlap (§8.6, Pattern 5) — and per-rank expert GEMMs whose token counts are small and data-dependent, which is the small-regime of §10.3 where η_PE predictions degrade and PE tiling (§3.2) matters most.
- **MXFP4 weights with MX-format compute on Trn3.** This puts §4.1's K→512 retiling requirement and §10.4.4's scale-granularity constraint directly on the critical path, rather than as a future porting concern.
- **A quadratic-versus-linear FLOPS mix that shifts with context.** The program's own analysis reports attention rising from ~35% of total FLOPS at 8k context to ~79% at 100k, and identifies a ~2× BF16 dense-FLOPS deficit against the comparator as the binding constraint at long context, with parity lost around 35k. That is a legitimate first-principles argument, and §12.4 examines the proposed remedy.

### 12.2 Symptoms with a diagnosis already in this book

Each row is a reported symptom [prog] paired with the section that explains it. This table is the practical payload of §12.

| Reported symptom | Diagnosis | Section |
|---|---|---|
| Per-module utilization figures in the single digits to high twenties, used to size the optimization gap | These are **average**-rate figures. Only `bytes ÷ engine-active-time` diagnoses access-pattern quality; the average/burst gap measures scheduling instead | §11.5 |
| AllGather and ReduceScatter in prefill regressing 200 µs → 700 µs / 1.2 ms, attributed to "DMA changes during the collectives" | DMA engines arbitrate round-robin across up to 16 rings and **switch only at packet boundaries**, so a large-packet ring starves a small-packet one. `NEURON_RT_DBG_DMA_PACKETIZATION_SIZE` (default 4 KiB) is the deliberate prioritization lever | §8.5 |
| Workstream to "explore DMA/CC overlap using DMA QoS"; separate item to "switch ReduceScatter to All-to-All to reduce DMA contention" | Same arbitration mechanism. Note the A2A switch has a **second, independent** justification the program did not cite — see the movement/reduce asymmetry below | §8.5, §11.6 |
| All-to-all measured at >200 µs in-profile against a ~47 µs standalone collective benchmark | A collective is a synchronization point, so upstream per-rank imbalance appears *as* collective time. Establish the `nccom-test` floor (done), then attribute the delta to **arrival skew and contention**, not to the collective | §8.7 |
| "Core skew in the MoE section costing 20 µs"; a separate unowned "DMA skew" workstream | Same mechanism, named. Skew is the thing to fix; the collective is where it becomes visible | §8.7 |
| One config "bottlenecked by global allocation and scheduling inefficiency, with no proven path forward" | On Trn3 the array got ~4× faster while PSUM did **not** grow, so the accumulator-to-compute ratio fell 4×. Drain scheduling is a structural consequence of the hybrid WS/OS dataflow, not an implementation wart — which reframes "no path forward" as a specific, bounded problem: fuse the drain into real work and split it across ScalarE/VectorE | §2.1, §5.4, §10.1 |
| Repeated instruction-count work: "reduce instruction count", I-cache fetch bound, instruction-fetch gaps, PE prefetch size raised to accommodate row tiling | Compute the **matmul-to-total instruction ratio** from the profile. A published production case found ~4,096 real matmuls against ~300,000 layout/pack/format instructions — 1:75. If your ratio is anywhere near that, matmul shape is not the problem and §5.5/§5.7 are | §10.4.7 |
| Doubling the MoE weight prefetch buffer "yielded a large amount of spill and worse performance"; separately, "weight is only prefetched for 1 of the 2 experts on each rank" | Classic shared-ring-buffer aliasing. Safe depth is `2 × max_required_per_consumer`, not a constant; a cap merely ≥ one consumer's requirement aliases as soon as the second consumer's base index wraps, and the compiler inserts WAR barriers that stall the weight DMA stream | §11.4 |
| Router + permute taking ~70 µs where <40 µs is expected; insufficient MoE prefetch costing ~60 µs | Audit **tensor lifetime** before tile size. The reference MoE result (53% latency cut) came from making routed tokens and the output accumulator *resident* while streaming only weights — and the report is explicit that merely enlarging tiles would not have worked | §10.4.3 |
| Fused RMSNorm + Router "not showing perf improvement as expected" | Fusion can regress by creating a **new synchronization boundary**: pulling work inside a kernel forces the preceding collective to fully complete first. Measured elsewhere as a 15% prefill regression from a fusion that helped decode. Gate per regime | §11.2 |
| Gap between kernel device profile (241 ms) and system profile (317 ms); dashboard showing 573 ms where 285 ms was expected | Do not mix device time and wall time across comparisons, and record the full configuration with every number. The program separately discovered that prefill device time is **data-dependent** — which is the same lesson arriving the hard way | §11.15 |
| Concurrency sweeps across batch 128 → 1024 | Verify the batch the scheduler **admitted** equals the batch requested; throughput-versus-batch is non-monotonic with a sharp cliff once the KV budget cannot hold the requested concurrency | §11.13 |
| Segment-size and context-bucket tuning for long context | Sweep **alignment** of every length/bucket parameter before quoting any ceiling; a misaligned model length was measured at roughly half the throughput of an aligned one. Kernel divisibility constraints also restrict which buckets are servable at all | §11.13 |
| Many levers measured individually, then stacked | Composition is non-additive in both directions: two levers touching the same scheduling slack are sub-additive, and a lever measuring ≈0% alone can deliver only once a second lever consumes the slack that was hiding its stall. Never discard a standalone null; never ship a stack measured only as parts | §11.3 |

### 12.3 Two levers the program has, whose second justification it hasn't cited

**Quantized collectives are asymmetric (§11.6).** The program's plan to switch ReduceScatter → All-to-All is motivated by DMA contention. There is a stronger reason: a collective can carry **any** dtype it merely *moves*, and only supported-arithmetic dtypes for values it must *combine*. AllGather, all-to-all and permute are movement-shaped and can carry an FP8 payload; reduce-family collectives are compute-shaped, and a narrow float payload was rejected outright by the compiler and produced NaN when forced. So the A2A switch does not merely relieve arbitration pressure — it **unlocks halving the payload bytes**, which is the highest-leverage collective optimization available (§10.4.5). Conversely this caps the technique: in an `AllGather → matmul → ReduceScatter` layer only the gather half is narrowable.

**Reduce bytes before choosing an algorithm (§10.4.5, §8.2–8.3).** The program is investing in collective *algorithm* and topology work. Two documented results should be priced in first. Quantizing an activation payload before dispatch halved bytes and nearly halved the measured scatter stage, matching its bandwidth lower bound. And a hierarchical all-to-all *lost* to a flat one, because relaying the same payload through staging buffers nearly doubles total bytes moved. Separately, wide expert parallelism is the regime where naive ring collectives hurt most [derived]: the ring latency term grows as `(R−1)·α` while RDH grows as `log₂(R)·α`, so at EP64–EP128 with small per-rank payloads the algorithm choice is worth more than at low rank counts — and the payload is small precisely because the experts are numerous.

A related [derived] point on the program's open "is wide EP still correct at all concurrencies?" question, which it notes GPUs answer with EP1–EP2: wide EP shrinks the per-rank expert GEMM in both token count and expert count simultaneously. At 128 experts and EP128, each rank holds one expert, so the `M/128` term of η_PE is set by a single expert's slice and the token dimension by that expert's routed share of the batch. The §11.12 mechanism generalizes — finer sharding trades collective bandwidth for array occupancy and can lose both. The legal-configuration-set discipline of §11.13 applies: enumerate what divisibility permits, then measure inside it, rather than sweeping openly.

### 12.4 The MX contraction-depth trap in attention

This is the one place where the program's strategy has a derivable problem, and it follows in three steps from documented mechanism.

The program's long-context plan rests on FP8 attention: running SDPA in a narrow float format to erase the ~2× BF16 dense-FLOPS deficit against the comparator [prog]. On Trn3 the natural way to get that is MX-format matmul. But §1.3/§4.1 are explicit about *how* Trn3 achieves 4× BF16 throughput: not by widening the array, but by **quadrupling the maximum contraction dimension from 128 to 512**, presenting a logical 512×128 array while each physical PE accumulates four MX products per cycle [doc]. Hence:

```
η_PE(BF16) = (K/128) · (M/128) · N/(N + K + M − 2)
η_PE(MX)   = (K/512) · (M/128) · N/(N + K/4 + M − 2)
```

Now apply that to attention's two matmuls [derived]. The `QKᵀ` contraction is over **head dimension** — 64 in this model class. The `PV` contraction is over **key positions**, which can be tiled at 512 or more.

For `QKᵀ` with K = 64, ignoring the shared M and stream terms, the geometric contraction factor is:

```
BF16:  K/128 = 64/128 = 0.50
MX:    K/512 = 64/512 = 0.125
```

MX offers 4× the peak rate at one quarter the contraction occupancy. **Net gain on QKᵀ: approximately 1.0× — no gain at all.** For `PV`, where K can be 512, MX reaches full contraction occupancy and the 4× is available.

Three consequences:

1. **The FP8-attention FLOPS argument is at best half-strength unless QKᵀ is restructured.** The remedies are the ones in §3.2 and §5.3: pack several heads' contractions into the array's depth so the effective K approaches 512, or use PE tiling (4×32 row and column on NC-v3+, sizes set independently) to run multiple small-K matmuls concurrently. Note the documented restriction that `double_row` performance mode **cannot** be combined with column tiling [doc] — a real constraint when both levers are wanted at once. Whether the packing is expressible for the program's layouts is an empirical question; the point is that it is a *required* work item, not an incidental optimization, and it should be costed before the FP8-versus-BF16 decision is taken on FLOPS grounds.
2. **Non-MX static FP8 may behave differently.** On Trn2, plain FP8 matmul is 2× BF16 without any contraction-depth change [doc], so it does not carry this trap — it also does not carry the 4×. The program should establish which format its "FP8 attention" actually means before projecting from a peak number.
3. **Separate the two FP8 wins, because they bind in different regimes** [derived]. FP8 *KV cache* is a **bandwidth** win and pays off at decode, where attention is byte-bound reading the cache — the program's own module table shows full attention at ~40 MB per layer, which is a memory-bound signature. FP8 *attention compute* is a **FLOPS** win and pays off at prefill and long context, where attention is genuinely arithmetic-bound. They are independent decisions with independent accuracy exposures, and conflating them into one "FP8 attention" go/no-go obscures that the decode half is both easier and less contingent on the K-packing work above.

One further caution from §11.7: the 1-byte-dtype transpose path was measured as **correct in functional simulation and garbage on device** for a layout-sensitive interleaved pattern, and the documented DMA-transpose support covers 2- and 4-byte dtypes. Any FP8/MXFP4 attention path involving transposition, packing or sub-byte punning must be validated on hardware, and the program's packed-FP8 layout work sits exactly in that hazard zone.

### 12.5 Independent corroboration of §11

Four §11 findings recur in the program's reports on a different model class and a newer chip, which is the strongest available evidence they are architectural [derived from the pairing]:

**Logical-core configuration is a collective-topology decision (§11.12).** §11 measured the finer-grained configuration as substantially worse for prefill and roughly neutral for decode, with about two thirds of the regression from collective cost roughly doubling — because the coarser configuration performs a free implicit reduction *within* each physical-core pair that the finer one forfeits — and the remaining third from halved per-rank shard widths attacking `M/128`. The program independently reports the fine configuration as ~10% better for decode MoE, keeps prefill on the coarse configuration, bridges them with disaggregated KV transfer, and separately discovers that its all-to-all-v collective **requires** the coarse configuration. That is the same tradeoff surfacing as a hard dependency. The actionable form: measure the collective delta explicitly rather than assuming the compute win carries, and expect an integration tax across runtime, kernel launch sites and compiler argument propagation when enabling a non-default configuration.

**Library capability not wired up at the caller (§11.1).** §11 found this to be the single highest-yield pattern — fused positional embedding, fused pre-normalization and native grouped-query handling all present in kernel signatures and unset by callers. The program's own backlog is populated with the same shape of item: passing routing arity and expert-parallel degree into a kernel so it can choose a better static block count, enabling expert weight skipping, integrating in-kernel cache update, removing a transpose that existed only because two layers disagreed about layout, removing a caller-side mask materialization. This is not a coincidence of two codebases; it is what happens whenever a general kernel library meets a specific caller. The procedure in §11.1 — diff the full signature against the call site, then inspect the ops immediately before and after the call — is worth running as a scheduled audit rather than an opportunistic one.

**Compute/collective overlap must be bought inside a kernel (§11.11).** §11 measured essentially zero TensorE/CC overlap and found reduce-family collectives *deliberately excluded* from the compiler's fine-grained fusion and hoisting passes, with no scheduler flag breaking the `AllGather → matmul → ReduceScatter` chain. The program has independently converged on hand-scheduled multi-layer megakernels as its primary decode lever, which is the correct response — and §11.11 supplies the missing justification for why that investment is not optional and why waiting on compiler scheduling is not a plan. Two warnings attach. **Partial fusion can be worse than none**: §11's first working in-kernel-collective variant regressed because the achievable split point forced an extra device-memory round trip for the intermediate the collective consumed. Cost that round trip before choosing a megakernel boundary. And **in-kernel loop fusion does not scale by raising the trip count** (§11.10): the compiler pre-allocated scratchpad for all iterations rather than reusing across them, so a design validated at 8 layers should be expected to hit a memory wall well before full depth. Plan a tuned *group* size, and note that per-group persistent-state pools whose shapes match the group kernel's operands simultaneously satisfy the scratchpad pressure and the exact-shape requirement of the aliasing pass (§11.9) — a coincidence worth exploiting rather than rediscovering.

**An unexplained failure is one integration bug (§11.8).** §11 documents a false consensus, held for weeks and written into internal documentation, that in-kernel collectives were unavailable — founded on two real, reproducible failures (an "invalid schedule" rejection and an on-device deadlock) that were both integration bugs. Three requirements resolved it: stage collective I/O through a kernel-internal shared buffer, bootstrap the communicator before first graph execution, and define the kernel in its own module file. The program carries at least one belief of the same shape — that its all-to-all-v collective cannot run in the fine-grained core configuration, currently blocking a sharding path — and its own notes record no response to the query rather than a diagnosed cause. That is the §11.8 signature exactly. One deliberate minimal reproduction is cheap relative to redesigning around the constraint.

### 12.6 Per-request work elision for mixed-length batches

The program identifies a capability gap it says does not exist today and must be built [prog]: decode attention runs at a single context length for the whole batch, so a short request pays the batch's longest context. Existing mechanisms — graph bucketing, and a runtime cap to the batch-level maximum — both reduce the ceiling to the batch max and neither helps *within* a batch. Skipping DMA for inactive blocks saves bandwidth but the arithmetic still runs at full size.

There is established prior art for exactly this, catalogued in §9 [doc-jax, derived]:

- **Block-level skipping driven by host-computed metadata.** The sparse flash-attention family skips blocks the mask zeroes out rather than computing and masking them, and the grouped-matmul MoE kernel has the **host** build the routing plan which the kernel then consumes — sidestepping device-side data-dependent control flow entirely. For per-request context bounds this is the natural structure: the scheduler already knows every request's length, so it can emit a per-request tile schedule, and the kernel's loop structure simply never issues the skipped tiles. The saving is then arithmetic, not just DMA.
- **Scalar/register-held control values.** TPU places this control data in a scalar memory and prefetches it; NKI's analogue is the scalar register file (`nisa.register_alloc/load/move/store`, `nisa.VirtualRegister`, §3.5) — compute indices and bounds there rather than round-tripping through a compute engine. Also relevant: `nisa.sequence_bounds`, `range_select` and `affine_select` for ragged edges without branches.
- **Distribution-aware specialization.** Rather than one kernel that handles every length mix adequately, dispatch specialized kernels per length regime; Python control flow resolves at trace time, so only the taken branch enters the compiled program. Three good kernels beat one mediocre one.
- **Do not design around Trn3 indirect addressing yet.** NeuronCore-v4 adds an indirect SBUF/PSUM addressing mode across all compute engines, which is the right long-term primitive for this, but it has **no `nki.isa` API support at this time** [doc].

The program's in-flight "sequence packing with tensor indirection" work is the same idea; the value of the above is that it names the reference implementations to read before building, and the §11.13 warning that whatever divisibility constraints the resulting kernel imposes will propagate back into the servable bucket set.

### 12.7 The bound the program is missing

Its per-module analysis carries a compute bound (peak-FLOPS time per module) and a memory bound (bytes ÷ peak bandwidth per module) — which is two thirds of the §10.4.1 method and better instrumented than most. What is absent is a **hop-adjusted interconnect bound per module** [derived].

This matters more here than in most workloads, for two reasons. The program's own reporting puts collectives at over a third of prefill layer time and roughly a fifth of decode, and separately projects collectives exceeding 36% of end-to-end layer time at low batch once other optimizations land — so it is plausibly the largest of the three bounds at the operating points that matter, and it is the one term with no floor computed. And the hop correction is not a rounding error: on a torus, the average random destination is multiple hops, which in the reference case study **doubled** the naive injection-bandwidth bound. Omitting it makes a collective look inexplicably slow against a bound that was never achievable.

The complete recipe, from §10.4.1: compute all three floors, take the max, and divide measured time by it. The *ratio* says whether you have a bandwidth problem or a scheduling problem; the *identity of the largest floor* says what to hide behind what. §11 measured a decode step at ~4× the largest of its three bounds and correctly concluded scheduling; the reference case study measured ~7× compute-bound and reached the same conclusion. A program reporting a 3.2× decode gap without that decomposition cannot yet distinguish "our kernels are inefficient" from "our kernels are fine and nothing overlaps".

### 12.8 Consolidated method additions

What §12 adds to §9.6, §10.6 and §11.17:

1. **Compute the interconnect floor, hop-adjusted, per module** — not just compute and memory floors. On a torus the average destination is multiple hops and the naive injection bound can be off by 2× (§12.7).
2. **Before projecting from a narrow-dtype peak number, check that your contraction dimension can use it.** MX's 4× comes from K→512; a matmul whose contraction is a head dimension of 64 gets none of it without head packing or PE tiling (§12.4).
3. **Separate bandwidth-motivated quantization from FLOPS-motivated quantization.** They bind in different regimes (decode vs prefill/long-context), carry different accuracy exposure, and should be decided independently (§12.4).
4. **Narrow the payload of movement-shaped collectives; check arithmetic-dtype support before assuming you can narrow a reduce** (§11.6) — and price a bytes reduction before investing in a cleverer algorithm (§12.3).
5. **At high shard counts, cost the topology change and the array occupancy together.** Wide parallelism shrinks per-rank tiles and lengthens ring latency simultaneously; enumerate the legal configuration set from divisibility first (§12.3).
6. **When a program-level gap is quoted as a single multiplier, demand the three-floor decomposition behind it.** "3.2× off target" is not yet a diagnosis (§12.7).

---

## 13. Generation Portability

Do not hardcode constants. Query with `nisa.get_nc_version()` and use `nl.tile_size.*` symbols rather than literal 128/512 [doc]. What genuinely needs per-generation branching:

| Concern | Portable? | Note |
|---|---|---|
| Tile shapes (128/512 BF16) | Yes via `nl.tile_size` | but K=512 for MX is a real structural difference |
| SBUF byte offsets in direct allocation | **No** | 224 vs 256 KiB partitions [doc] |
| MXFP8/MXFP4 | **No** | Trn3 only [doc] |
| Activation2 | **No** | Trn3 only [doc] |
| Indirect addressing | N/A | no `nki.isa` support yet [doc] |
| PE tiling granularity | Partially | 2×64 on NC-v2+, 4×32 on NC-v3+ [doc] |
| VectorE performance mode | Yes | automatic in hardware [doc] |
| Everything in §5 structurally | Yes | the playbook is generation-independent |
| `nki.collectives` API | Yes | but some library kernels are TRN2-only [doc] |
| Channel limits (4 intra-node / 2) | Yes | verify per replica group [doc] |

---

## 14. Pre-Flight Checklist

**Correctness**
- [ ] Passes `nki.simulate_kernel` against a NumPy reference
- [ ] Edge tiles handled by `mask=` / `affine_select`, not branches

**TensorE**
- [ ] K and M at 128 (or 512 for MX K); PE tiling where they can't be
- [ ] N at 512; matmuls chained per stationary load
- [ ] Narrowest acceptable dtype; MX K retiled to 512 on Trn3
- [ ] **Any projected gain from a narrow dtype re-derived with your actual K.** MX's 4× is a contraction-depth mechanism; a head-dimension contraction of 64 realizes none of it without head packing or PE tiling (§12.4)
- [ ] Transposes eliminated by operand-role choice, not added to fix layout

**PSUM**
- [ ] No memset before accumulation chains
- [ ] Drain fused with real work (activation, scale, residual)
- [ ] Drain split across ScalarE and VectorE via `engine=`
- [ ] Bank budget explicit; narrow output dtype where possible

**Vector / Scalar**
- [ ] Every elementwise chain checked against the fusion catalogue (§3.1)
- [ ] ≥512 elements/partition per instruction
- [ ] 16-bit, unit-stride, SBUF-resident operands for performance mode
- [ ] Cross-partition reductions on TensorE, not `tensor_partition_reduce`

**DMA**
- [ ] Free-dimension bytes ≥4 KiB (recheck after any dtype narrowing)
- [ ] Bandwidth utilization >60% in the profiler
- [ ] All 16 engines active on large transfers
- [ ] Transposes on TensorE or `dma_transpose`, never via strided loads

**Pipeline**
- [ ] `affine_range` wherever dependencies allow
- [ ] Buffers actually multi-buffered (`multi_buffer` or direct allocation)
- [ ] Three tiles in flight for three-stage chains
- [ ] No semaphore waits naming another compute engine in steady state

**Method (see §9.6, §10.6, §11.17, §12.8)**
- [ ] Three lower bounds computed (compute / interconnect hop-adjusted / memory) before optimizing
- [ ] The interconnect floor actually hop-adjusted, and computed **per module** — not omitted because compute and memory floors were easier (§12.7)
- [ ] Any headline gap multiplier decomposed against those floors before it is used to size a work plan (§12.7)
- [ ] Regime identified (small / medium / large) so the right cost model is trusted
- [ ] Tensor lifetimes audited — resident vs streaming — before tuning tile sizes
- [ ] Quantization scale granularity checked before choosing K tiling
- [ ] Bandwidth-motivated and FLOPS-motivated quantization decided separately; they bind in different regimes (§12.4)
- [ ] Matmul-to-total instruction ratio computed from the profile
- [ ] Block/tile sizes chosen by a systematic sweep, not by rule of thumb
- [ ] Revisit-skipping checked: no reloading a tile a consecutive iteration already has
- [ ] Weight SBUF-residency evaluated against the arithmetic-intensity argument (§9.7)

**Collectives (multi-core only — see §8.8 for the full list)**
- [ ] Communication overlapped with compute, ideally fused (FGCC-style)
- [ ] SBUF vs HBM path and coalescing chosen deliberately
- [ ] Channels used (up to 4 intra-node)
- [ ] Packet-size contention ruled out when compute DMA stalls
- [ ] Rank arrival skew checked before blaming the collective
- [ ] Payload dtype narrowed **only** on movement-shaped collectives; reduce-family checked for arithmetic dtype support (§11.6)
- [ ] A bytes reduction priced before investing in a cleverer collective algorithm or topology (§12.3)

**Integration and API surface (§11.1, §11.9)**
- [ ] Every critical-path kernel's full signature diffed against your call site; no fused-operation parameter left unset by accident
- [ ] No caller-side data preparation (replicate / transpose / reshape / elementwise) that the kernel could absorb
- [ ] Emitted alias map and I/O accounting inspected — persistent-state aliasing actually collapsed, not merely requested
- [ ] Explicit assertion or log that the intended (fast) code path was taken, so silent fall-through can't hide
- [ ] Only view-producing ops (`select`/`slice`/`narrow`/`view`/…) between a persistent buffer and the kernel argument — no advanced indexing, `stack`, `clone`, or `contiguous`

**Measurement integrity (§11.5, §11.15)**
- [ ] **Burst bandwidth** (bytes ÷ DMA-active time) computed, not just average, before acting on any utilization figure
- [ ] Both execution regimes (context/prefill and token-generation/decode) measured separately for every lever
- [ ] Standalone nulls re-tested in combination; the exact shipping stack measured as a stack
- [ ] Noise floor established; spreads and sample counts reported; variance tracked as a result
- [ ] Device time and wall time not mixed across comparisons; full configuration recorded with every number
- [ ] Legal configuration set enumerated from divisibility constraints; length/bucket alignment swept before quoting a ceiling
- [ ] Requested batch confirmed against scheduler accounting; capacity cliff located

**Correctness of "equivalent" changes (§11.14)**
- [ ] Change gated, default off, baseline byte-identical when unset — and default re-verified periodically
- [ ] Real-content correctness, not only a synthetic prompt; any synthetic-only divergence documented, not hidden
- [ ] Enough generation steps to exercise persistent-state accumulation (not 3–8 tokens)
- [ ] Any divergence reproduced twice before being diagnosed as numerical
- [ ] Anything layout / transpose / packing / sub-byte-dtype validated **on device**, not only in functional simulation

---

## 15. References

**Primary — architecture**
- Trainium2 Architecture Guide for NKI — `nki/guides/architecture/trainium2_arch.html`
- Trainium3 Architecture Guide for NKI — `nki/guides/architecture/trainium3_arch.html`
- NeuronCore-v3 / v4 Architecture — `about-neuron/arch/neuron-hardware/neuron-core-v3.html`, `…-v4.html`

**Primary — performance**
- NKI Performance Optimizations — `nki/deep-dives/nki_perf_guide.html` *(the single most important page; semaphore method, fusion 3×, <60% DMA threshold, pipelining)*
- MXFP8/4 Matrix Multiplication — `nki/deep-dives/mxfp-matmul.html`
- NKI Access Patterns — `nki/deep-dives/nki-aps.html`
- Descriptor Generation Engine (DGE) — `nki/deep-dives/nki-dge.html`
- DMA Bandwidth Guide — `nki/deep-dives/nki-dma-bandwidth-guide.html`
- NKI Dynamic Loops — `nki/deep-dives/nki-dynamic-loops.html`
- About the NKI Compiler — `nki/deep-dives/nki-compiler.html`

**Primary — collectives and multi-core**
- Intra-node Collective Communications — `neuron-runtime/explore/intranode-collective-comm.html` *(Ring/Mesh/KangaRing/RDH, 2D Torus, latency+transfer model)*
- Inter-node Collectives — `neuron-runtime/explore/internode-collective-comm.html`
- Compute-Communication Overlap in Neuron — `neuron-runtime/explore/compute-comm-overlap.html` *(DMA ring arbitration, packetization size, FSDP/TP scheduling pathology)*
- `nki.collectives` API reference
- Logical NeuronCore configuration — `about-neuron/arch/neuron-features/logical-neuroncore-config.html`
- LNC Overview — `nki/get-started/about/lnc.html`
- `nccom-test` User Guide — collective benchmarking, incl. fine-grained

**Primary — API**
- `nki.isa` reference — worth reading end to end once, like a new ISA manual
- `nki.compiler` — `sbuf.alloc`, `sbuf.mod_alloc`, `psum.alloc`, `psum.mod_alloc`, `multi_buffer`, `enable_stack_allocator`, `force_auto_alloc`
- Direct Allocation Developer Guide
- Use NKI Scheduling APIs — `nki/guides/how-to-scheduling-apis.html`
- Introduction to DMA with NKI — `nki/get-started/about/nki-dma-overview.html`

**Cross-architecture (see §9)**
- `jax-ml/scaling-book` — "How To Scale Your Model": rooflines, TPU internals, inference math
- Pallas TPU docs — "Writing TPU kernels with Pallas" (layout/tiling/cost rules), TPU Pipelining, Distributed Computing in Pallas, Scalar Prefetch and Block-Sparse Computation, Core-specific Programming
- `jax/experimental/pallas/ops/tpu/` — flash_attention, splash_attention, paged_attention, megablox
- `openxla/tokamax` — GPU+TPU kernel library with autotuning tooling
- MaxText Pallas optimization guide — autotuning methodology, production kernel inventory
- Ragged Paged Attention (arXiv) — modern fused-pipeline case study
- TPU architecture papers: A Domain-Specific Supercomputer for Training DNNs; TPUv2/v3 design process; Ten Lessons from TPUv4i; TPU v4

**Academic / systolic-array literature (§10)**
- Chen et al., *Eyeriss* — the WS/IS/OS/RS/NLR dataflow taxonomy
- Parashar et al., *Timeloop: A Systematic Approach to DNN Accelerator Evaluation* (ISPASS 2019)
- Kwon et al., *MAESTRO: A Data-Centric Approach to Understand Reuse, Performance, and Hardware Cost of DNN Mappings*
- Yang et al., *Interstellar: Using Halide's Scheduling Language to Analyze DNN Accelerators* (ASPLOS 2020)
- *ZigZag* — extended architectural templates and mapping space
- Samajdar et al., *SCALE-Sim: Systolic CNN Accelerator Simulator* (arXiv 1811.02883), plus the SCALE-Sim TPU validation study for the small/medium/large regime analysis
- *Demystifying Map Space Exploration for NPUs* (arXiv 2210.03731) — the tile/order/parallelization decomposition
- Jouppi et al., *In-Datacenter Performance Analysis of a Tensor Processing Unit* (ISCA 2017) — the original TPU roofline

**Production case study (§10.4)**
- LMSYS/SGLang-JAX, *Optimizing Ling-2.6-1T on TPU: Hiding MoE Data Movement Behind Compute with One Pallas Kernel* — cost-model-first methodology, V1→V2 lifetime redesign, LLO instruction histogram, tile sweep
- `sgl-project/sglang-jax` — the Fused MoE V1/V2 kernels
- `vllm-project/tpu-inference` — the original Fused MoE V1

**Field-lessons provenance (§11)**
- Distilled from a multi-session optimization and bring-up campaign on a large dense decoder-only transformer served on a single Trn2 instance under a production inference stack: ~20 discrete optimization levers (delivered wins, hardware-blocked attempts, and rigorous nulls), whole-model prefill/decode profiling, a logical-core-configuration study, an in-kernel-collective enablement effort, and a four-level bring-up test pyramid. Model identity, absolute latencies and targets are intentionally omitted; §11 reports mechanisms, failure modes and relative effects only.

**Program cross-reference provenance (§12)**
- A performance-strategy RFC plus ~8 weeks of weekly status for an engineering program optimizing an open-weights ~120B sparse-MoE model on a 64-core Trn3-class server against an 8-GPU price-per-token target: per-module utilization tables at an 8k/1k operating point, prefill and decode workstream reports (attention, MoE, collectives, framework, long context, speculation), and its own decision log. Identities, owners, absolute business targets and internal links are omitted. §12 treats it as a **second-hand catalogue of symptoms** and supplies the diagnosis from §§1–11; the one novel analytical contribution (§12.4) is [derived] from documented MX mechanism, not reported by the program.

**Kernels to read**
- `aws-neuron/nki-samples` — matmul (documented as reaching ~98% HFU), fused self-attention, fused Mamba
- NKI Library — Attention CTE/TKG, MoE CTE/TKG, QKV, RMSNorm-Quant, RoPE, Cumsum, Top-K Reduce, Blockwise MM Backward. These are production kernels implementing this playbook; the design spec pages (e.g. RMSNorm-Quant) show the reasoning.
- **Collective kernels**: `fine_grained_allgather` (`nkilib/experimental/collectives/fg_allgather.py`), FGCC (fused all-gather + matmul), SBUF-to-SBUF All-Gather, ring attention forward/backward, MoE training collectives, indexed gather/scatter.
- `nki.language.SbufManager` (allocator) and `TensorView` kernel utilities

**Tutorials**
- Matrix Multiplication; Fused Mamba (excellent profiling walkthrough); Transpose2D; Introduction to NKI Kernel Optimization

---

## Appendix: Constants

| Constant | Trn2 | Trn3 |
|---|---|---|
| PE array | 128×128 [doc] | 128×128 physical; **512×128 logical for MX** [doc] |
| Stationary max | [128, 128] | [128,128]; K→512 for MX |
| Moving max | [128, 512] | [512, 512] for MX |
| PSUM bank | 2 KiB = 512 FP32 [doc] | same |
| PSUM banks/partition | 8 [doc] | 8 |
| SBUF partition | 224 KiB [doc] | 256 KiB [doc] |
| DMA engines/core | 16 [doc] | check current guide |
| DMA saturation | ~4096 free bytes [doc] | same order |
| DMA "bad" threshold | <60% BW util [doc] | same |
| Min efficient free size | ~512 elem/partition [doc] | same |
| PE tiling | 2×64, 4×32 [doc] | 4×32 [doc] |
| BF16 dense | 79 TFLOPS [doc] | higher; 315 w/ sparsity [doc] |
| FP8 dense | 158 TFLOPS [doc] | 315 MXFP8 [doc] |
| FP32 dense | 20 TFLOPS [doc] | ~4× slower than 16-bit |
| Vector engine | 1.2 TFLOPS FP32 [doc] | 1.2 TFLOPS FP32 [doc] |
| CC channels max | 4 intra-node / 2 other [doc] | verify per guide |
| DMA rings arbitrated | up to 16, round-robin [doc] | same |
| DMA packetization default | 4 KiB [doc] | 4 KiB [doc] |
| Interconnect | NeuronLink, 2D Torus [doc] | NeuronLink-v4 |

**Formulas** [derived]

```
η_PE(BF16) = (K/128)·(M/128)·N/(N + K + M − 2)
η_PE(MX)   = (K/512)·(M/128)·N/(N + K/4 + M − 2)

# MX pays off only if K can reach 512. For attention (§12.4):
#   QKᵀ contraction K = d_head (e.g. 64) ⇒ MX contraction factor 0.125 vs BF16's 0.50
#                                        ⇒ 4× peak × (0.125/0.50) ≈ 1.0× net, i.e. no gain
#   PV  contraction K = key positions (tileable to 512) ⇒ full 4× available
# Remedy: pack multiple heads into the contraction depth, or PE-tile (4×32 on NC-v3+).
T_DMA      ≈ n_desc·t_setup + bytes/BW_eff,  BW_eff ↑ steeply to ~4 KiB/transfer
T_kernel   ≈ max(T_PE, T_vec, T_scalar, T_gpsimd, T_DMA, T_CC, T_sync) + stalls

T_collective        ≈ latency + transfer                       [doc, structure]
T_ring(all_reduce)  ≈ 2(R−1)·α + 2·(R−1)/R · S/B               [derived]
T_ring(all_gather)  ≈  (R−1)·α +   (R−1)/R · S/B               [derived]
T_RDH latency term  ≈ log₂(R)·α   ⇒ wins over ring at high R    [derived]
```