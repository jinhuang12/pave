# KV cache memory model: hybrid grouping, sharing layers, framework↔HLO handshake

The conceptual model behind vllm-neuron's KV cache. Read this when you need
to reason about *why* memory looks the way it does — before reaching for
knobs in [kv_cache_budgeting.md](kv_cache_budgeting.md).

## The mental model: three layers of sharing

The KV cache exists at three distinct layers, and conflating them is the
single biggest source of wasted optimization effort. Knowing which layer
you are at when you make a change determines whether the change actually
saves memory.

| Layer | What's shared | Where it's expressed |
|-------|---------------|----------------------|
| **(1) Byte buffers** — actual HBM allocations | One `torch.zeros(int8)` per `KVCacheTensor` bucket. For Gemma4 60-layer this is **10 buffers × 128 MiB = 1.25 GiB per rank** regardless of attention spec heterogeneity. | `KVCacheTensor.shared_by` in `vllm/v1/kv_cache_interface.py:451`; allocator loop in `vllm_neuron/vllm/worker/neuron_model_runner.py:5202-5215`. |
| **(2) Python tensor objects** — typed views into byte buffers | Per-layer `view(spec.dtype).view(kv_shape)` produces 60 distinct Python objects (different `id()`) but only 2 unique `(storage_id, shape, stride, dtype)` fingerprints per bucket (one per spec — SWA vs Global). | `kv_caches[layer_name] = [typed_tensor[0], typed_tensor[1]]` at `neuron_model_runner.py:5277`. |
| **(3) FX placeholders → HLO parameters** — what the compiler sees | After `_detect_duplicate_inputs` runs, exactly one HLO parameter per unique fingerprint at layer (2). For Gemma4: 40 parameters (2 per bucket × 10 buckets × K/V). | `vllm_neuron/compile/backend.py:50-128`. |

**The runtime allocator (layer 1) is what sets HBM usage**, not layer (3).
Multiple HLO parameters can — and routinely do — receive the same `data_ptr`
at execute time. The compiler treats them as independent for scheduling, but
they share storage.

This means:

- **HLO parameter count is not a memory budget**. A `sum(parameter.size_bytes)`
  pass over the HLO sees `40 × 64 MiB = 2.5 GiB` for Gemma4 baseline. The
  real HBM is **1.25 GiB**.
- **"Reduce parameter count to save memory" doesn't work.** The bytes are
  already shared at layer (1). Changing layer (3) only re-bookkeeps; it
  doesn't free anything.
- **The truth is only visible at the runtime allocator.** Tools that
  introspect the HLO (`hlo_convert`, parameter walks, must-alias counters)
  systematically overestimate memory.

### Verifying the handshake from logs

Two ways to observe layer (3) ↔ runtime mapping:

```bash
# Pre-KV vs post-warmup HBM (per-rank, from worker probe):
grep "Neuron HBM:" <bench.log> | sort -u | head
#   3.83 GiB used   ← weights only, after load_weights, before KV alloc
#   6.82 GiB used   ← post-warmup; delta = KV bytes + activation scratchpad
#   (post-warmup probe is at vllm_neuron/vllm/worker/neuron_worker.py:897)
```

```bash
# FX-graph dedup result (= layer (2) → layer (3) handshake):
grep "Input dedup:" <bench.log> | sort -u | head
#   "removed 80 duplicate placeholder(s) (975 -> 895)"
#   For Gemma4 hybrid: ~3K placeholders dedup to K (5 SWA layers per bucket).
#   (dedup pass at vllm_neuron/compile/backend.py:50-128)
```

If the post-warmup HBM matches expectation but the HLO parameter count
suggests "more memory should be needed," it's because the dedup pass at
layer (3) is bridging the layer (1) ↔ layer (2) sharing into the parameter
list.

### What HLO `must-alias` does and doesn't express

```hlo
HloModule X, input_output_alias={ {1}: (8, {}, must-alias), ... }
```

`input_output_alias` is the **only** parameter-aliasing annotation in HLO.
Format: one *output* tied to one *input*. There is **no** `input_input_alias`
or `output_output_alias`. Specifically:

- HLO is silent about input↔input aliasing. Two parameters with the same
  `data_ptr` at runtime — the compiler does not know.
- The compiler must conservatively assume independent buffers for
  scheduling. It cannot, e.g., elide a redundant load through a second
  parameter that happens to alias a first.
- The runtime is responsible for must-alias correctness when input
  aliasing exists. The dedup pass at compile/backend.py is what makes
  this safe by ensuring the HLO sees one parameter per unique view.

## Hybrid models: how grouping creates the buckets

A `KVCacheGroupSpec` = `(layer_names, kv_cache_spec)`. Layers in the same
group share a block table. Different groups need different specs because
attention type, sliding window, head_dim, or kv_heads differ.

The vllm-neuron runner emits one spec per layer (`get_kv_cache_spec()` in
`neuron_model_runner.py`). vLLM then groups them via
`_get_kv_cache_groups_uniform_page_size` (`kv_cache_utils.py:959`) which
**enforces two invariants**:

1. **Uniform page size** — all groups must have the same `page_size_bytes`.
   If specs naturally differ, `unify_kv_cache_spec_page_size` inflates the
   smaller spec by **doubling its `block_size`** until pages match.
2. **Uniform layer count** — all groups must have the same number of
   layers. If a model has `m` of one spec and `n` of another, vLLM splits
   the larger into sub-groups so every group has `min(m, n)` layers (or
   `max(m, n)` if `max < 1.5 × min`, to avoid excessive padding).

### Block index space (shared) vs. physical KV buffers (per-group)

vLLM's KV cache uses a **paged** layout. The block-table allocator hands
out integer indices in `[0, num_gpu_blocks)`. Picking integer `k`:

- Reserves `swa_buffer[layer_i, k, :, :, :]` in **every** SWA-group layer.
- Reserves `global_buffer[layer_j, k, :, :, :]` in **every** Global-group
  layer.

So **one block-index allocation reserves memory in all groups at the same
`k`**. The block-index space is a single resource pool shared across
groups. The physical KV memory is per-group.

### Concrete grouping example: Gemma4 60L (TP=16, fp8)

Gemma4 has 50 SWA + 10 Global layers. Page sizes naturally differ
(SWA head_dim=256 → 8192 B; Global head_dim=512 → 16384 B), so vLLM:

1. **Unifies pages**: doubles SWA's `block_size` from 16 to 32 → SWA's
   `page_size_bytes` becomes `2 × 32 × 1 × 256 = 16384 B` (matches Global).
   Note: only the SWA spec's block_size changes, **not** the global
   `cache_config.block_size`.
2. **Splits into uniform groups**: 50 SWA / 10 Global → group_size = 10 →
   **6 groups of 10 layers**: 5 SWA sub-groups + 1 Global sub-group.

Final group layout:

| Group | Spec | block_size | page_size_bytes | tokens per block |
|-------|------|-----------:|----------------:|-----------------:|
| SWA-bucket-0..4 | SlidingWindowSpec | 32 | 16384 | 32 |
| Global-bucket-0 | FullAttentionSpec | 16 | 16384 | 16 |

Both have 16384-byte blocks, but **different tokens-per-block** because of
different head sizes.

### Why hybrid models pay a multiplicative penalty

For Gemma4 (50 SWA + 10 Global with 5:1 pattern): 6 groups × 10 layers each.
A request "uses" 10 layers' worth of memory per group × 6 groups = 60
layers' worth. Same as a uniform 60-layer model — except now you also pay
for the page-size unification (SWA's `block_size` doubles, so SWA is
allocated 2× more token-slots than its sliding window actually uses).

This is the structural reason hybrid SWA+Global models have lower
concurrency caps than their layer count alone would suggest. See
[kv_cache_budgeting.md](kv_cache_budgeting.md) for the formula and tuning
flow.

### SWA over-provisioning under unification

After SWA's `block_size` is doubled to match Global's page size, each SWA
block holds 32 tokens. A 1024-token request gets 32 SWA blocks per layer ×
32 tokens = 1024 token-slots. Of those, only the last 513 (the sliding
window) are ever read by attention — the older slots sit reserved but unused
in steady state.

This isn't pure waste because of chunked-prefill semantics, but it is a real
inefficiency that shows up as inflated `num_block_per_request` in the
concurrency formula.

## When `VLLM_NEURON_DISABLE_INPUT_DEDUP=1` is the right answer

Default is correct for most models. Consider this knob (documented in
[kv_cache_budgeting.md](kv_cache_budgeting.md#vllm_neuron_disable_input_dedup))
when:

1. Hybrid attention model (SWA + Global, MLA + Full, etc.) where dedup
   collapses 5+ layers per parameter.
2. Compile log shows long `scatter→transpose→scatter→…→scatter` chains in
   the prefill HLO (depth ≥ 4).
3. Per-NEFF compile-log `DRAM hwm` is high relative to baseline weight
   memory (see [crash_triage.md](crash_triage.md) for how to extract).
4. You suspect serialization across SWA chain layers is bottlenecking
   decode throughput.

The dedup-off path is identical from a correctness standpoint — same byte
buffers, same `must-alias` per-parameter contract, same KV updates land in
HBM. It only changes how the compiler sees the parameter list.

## Source code references — sharing layers

| Path | Purpose |
|------|---------|
| `vllm/v1/kv_cache_interface.py:451-459` | `KVCacheTensor.shared_by` — the bucket → layer-name list that defines storage sharing at the byte-buffer level (layer 1). |
| `vllm_neuron/vllm/worker/neuron_model_runner.py:5202-5215` | Where `torch.zeros(int8)` allocates one byte buffer per `KVCacheTensor` (10 buffers for Gemma4 baseline) and binds it to all layers in `shared_by`. **The actual HBM allocation point.** |
| `vllm_neuron/compile/backend.py:50-128` | `_detect_duplicate_inputs` + `_dedup_fx_graph`. The layer-(2)→(3) bridge that determines HLO parameter count from unique runtime fingerprints. |
| `vllm_neuron/compile/backend.py:566-571` | The dedup gate (skipped via `VLLM_NEURON_DISABLE_INPUT_DEDUP=1`). |
| `vllm_neuron/vllm/worker/neuron_worker.py:897` | Post-warmup HBM probe (logs `Neuron HBM: X GiB used`). |

## Source code references — hybrid grouping

All paths relative to `vllm/v1/core/kv_cache_utils.py` unless noted.

| Function / location | Purpose |
|---------------------|---------|
| `get_kv_cache_groups` (line 1222) | Group construction dispatcher. |
| `unify_hybrid_kv_cache_specs` (line 1160) | Flatten hybrid → uniform full attention (when HMA disabled). |
| `unify_kv_cache_spec_page_size` (line 914) | Inflate smaller specs' `block_size` to match max page size. |
| `_get_kv_cache_groups_uniform_page_size` (line 959) | Split layers into uniform-size groups. |
| `_get_kv_cache_groups_uniform_spec` (line ~880) | Single-group path for fully uniform models. |
| `_get_kv_cache_groups_uniform_type` | Single-group path when all layers are same attention type but different sizes. |
| `vllm/v1/kv_cache_interface.py:65-87` | `AttentionSpec.real_page_size_bytes` definition. |
| `vllm/v1/kv_cache_interface.py:113-121` | `FullAttentionSpec.max_memory_usage_bytes`. |
| `vllm/v1/kv_cache_interface.py:247-251` | `SlidingWindowSpec.max_memory_usage_bytes`. |
