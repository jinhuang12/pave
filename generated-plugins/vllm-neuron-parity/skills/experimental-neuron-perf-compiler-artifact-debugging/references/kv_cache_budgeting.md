# KV cache budgeting & concurrency tuning

How to size `num_gpu_blocks_override`, `max_num_seqs`, `max_model_len`, and
related KV-cache parameters in vllm-neuron. The formula is non-trivial in
hybrid (mixed-attention) models because of how vLLM forms KV cache groups —
read [kv_cache_memory_model.md](kv_cache_memory_model.md) **first** for the
conceptual model (three sharing layers, framework↔HLO handshake), then
come back here for the formula and knobs.

## When to use this reference

- The KV concurrency cap (`Maximum concurrency: Yx`) is throttling
  throughput.
- You're sizing `num_gpu_blocks_override` for a target concurrency.
- Throughput dropped after raising `num_gpu_blocks_override` past a
  threshold, and you need to know why.
- A hybrid-attention model has lower-than-expected concurrency for its
  layer count.

## Why this matters for performance

vLLM caps **effective concurrency** based on KV cache budget. When the cap is
below `max_num_seqs`, throughput is gated by the cap, not by the GPU/Neuron
compute. Symptom: 60-layer Gemma4 31B at TP=16 batch=64 ran at:

| `num_gpu_blocks_override` | Effective concurrency cap | tok/s |
|--------------------------:|--------------------------:|------:|
| 2048 | 8 | 321 |
| 4096 | 17 | 611 |
| 8192 | 35 | 1112 |
| 16384 | 64 (capped by `max_num_seqs`) | 1886 |
| 32768 | 64 (capped by `max_num_seqs`) | 1673 |

Throughput scales nearly linearly with the concurrency cap, then drops above 16k
because the larger KV pool starts hurting per-step compile/runtime costs without
adding more concurrency.

The cap is computed by
`vllm.v1.core.kv_cache_utils.get_max_concurrency_for_kv_cache_config`.

## The concurrency cap formula

From `vllm/v1/core/kv_cache_utils.py:802`:

```python
def get_max_concurrency_for_kv_cache_config(vllm_config, kv_cache_config) -> float:
    num_layer_per_group = max(
        len(g.layer_names) for g in kv_cache_config.kv_cache_groups
    )
    max_memory_usage_per_request = num_layer_per_group * sum(
        spec.max_memory_usage_bytes(vllm_config)
        for spec in (g.kv_cache_spec for g in kv_cache_config.kv_cache_groups)
    )
    memory_per_block = (
        kv_cache_config.kv_cache_groups[0].kv_cache_spec.page_size_bytes
        * num_layer_per_group
    )
    num_block_per_request = cdiv(max_memory_usage_per_request, memory_per_block)
    max_concurrency = kv_cache_config.num_blocks / num_block_per_request
    return max_concurrency
```

For the conceptual reason `kv_cache_groups` exist and how they're
constructed, see
[kv_cache_memory_model.md](kv_cache_memory_model.md#hybrid-models-how-grouping-creates-the-buckets).

### Per-spec `max_memory_usage_bytes`

| Spec | Formula | Source |
|------|---------|--------|
| `FullAttentionSpec` | `cdiv(max_model_len, block_size) × page_size_bytes` | `kv_cache_interface.py:113-121` |
| `SlidingWindowSpec` | `(cdiv(min(window-1+max_num_batched_tokens, max_model_len), block_size) + 1) × page_size_bytes` | `kv_cache_interface.py:247-251` |
| `ChunkedLocalAttentionSpec` | `cdiv(min(chunk_size+max_num_batched_tokens, max_model_len), block_size) × page_size_bytes` | `kv_cache_interface.py:232-244` |

### `page_size_bytes` (`AttentionSpec.real_page_size_bytes`)

```python
2 × block_size × num_kv_heads × head_size × dtype_bytes
```

(or `page_size_padded` if explicitly set for runtime alignment)

## Gemma4 worked example

Configuration: TP=16, fp8 KV, `max_model_len=1024, block_size=16,
max_num_batched_tokens=1024, num_gpu_blocks_override=8192`.

After grouping (per the hybrid-page-unification path described in
[kv_cache_memory_model.md](kv_cache_memory_model.md#concrete-grouping-example-gemma4-60l-tp16-fp8)):

- `num_layer_per_group = 10`
- 6 groups: 5× SWA sub-groups, 1× Global sub-group
- All groups: `page_size_bytes = 16384`
- SWA sub-group `block_size = 32`, Global `block_size = 16`

### Per-spec memory per request

```
SWA spec:
  num_tokens = min(513-1+1024, 1024) = 1024
  blocks_per_layer = cdiv(1024, 32) + 1 = 33
  max_mem = 33 × 16384 = 540,672 bytes

Global spec:
  blocks_per_layer = cdiv(1024, 16) = 64
  max_mem = 64 × 16384 = 1,048,576 bytes
```

### Plug into the formula

```
sum(per_spec) = 5×SWA + 1×Global = 5×540,672 + 1,048,576 = 3,751,936
max_mem_per_request = num_layer_per_group × sum = 10 × 3,751,936 = 37,519,360
memory_per_block    = groups[0].page_size × num_layer_per_group = 16384 × 10 = 163,840
num_block_per_request = cdiv(37,519,360, 163,840) = 230
max_concurrency       = 8192 / 230 = 35.6 ✓ (vLLM logged 35.77)
```

### "Why does the formula multiply `num_layer_per_group × sum`?"

This is non-obvious. The key invariant: **all groups have the same layer
count** after grouping. So `num_layer_per_group × sum(per_spec_per_group)`
== `total bytes for one request across all groups`. Each group contributes
`num_layer_per_group × spec.max_memory_usage_bytes` bytes. Summing over groups
and pulling out the common factor gives the formula.

The factor `num_layer_per_group` cancels in the division by `memory_per_block`
(which is also `× num_layer_per_group`), so the formula simplifies to:

```
num_block_per_request = cdiv(sum(per_spec_max_memory), groups[0].page_size_bytes)
```

This is mathematically equivalent and easier to reason about.

## Engineering knobs and their effects

### `num_gpu_blocks_override`

Sets the block-index pool size directly. Larger pool → larger
`max_concurrency` (linearly). Larger pool → larger physical KV reservation.

For a target concurrency `C` at a given config:

```
num_gpu_blocks_override ≈ C × num_block_per_request
                        = C × ceil(max_model_len / finest_block_size)
                          × num_groups_after_grouping
                          × adjustment_for_sliding_window
```

For Gemma4: `≈ C × 230` blocks. To get `C = 64`, need ≥ 14720; vLLM auto-derives
about that, but if you override low (≤ 8192) you cap concurrency below
`max_num_seqs`.

### `max_num_seqs`

Hard cap on concurrent sequences regardless of KV pool. The effective
concurrency = `min(max_num_seqs, kv_concurrency_cap)`.

If your KV cap is `8` but `max_num_seqs=64`, you'll see the WARNING:

> max_num_seqs (64) exceeds worst-case KV cache capacity (8 concurrent requests
> at max_model_len=1024, assuming no prefix cache hits). Effective concurrency
> will be capped at 8 to prevent KV cache exhaustion and request preemption.

(`scheduler.py:236` in vllm-neuron)

### `max_model_len`

Reduces `num_block_per_request` proportionally. Halving from 1024 to 512
roughly halves the block budget, doubling `max_concurrency` for the same
`num_gpu_blocks_override`. Only viable if the workload doesn't actually need
the longer context.

### `block_size` (CacheConfig)

The global default for layers without unification. Smaller `block_size`
finer-grained allocation, less fragmentation, but more block-table indexing
overhead. For hybrid models, vLLM may override this per-group via
unification.

### `VLLM_NEURON_KV_GMU_BUDGET_CAP_FRACTION`

vLLM-Neuron-only env var (default 0.30). Caps automatic KV pool sizing at
`fraction × gpu_memory_utilization × total_hbm`. **No effect when
`num_gpu_blocks_override` is explicitly set.**

### Disable hybrid KV cache manager

Pass `--disable-hybrid-kv-cache-manager` to flatten all attention types into
`FullAttentionSpec` (`unify_hybrid_kv_cache_specs`, `kv_cache_utils.py:1160`).
Loses sliding-window optimizations (no block clipping), but simpler accounting.

### `VLLM_NEURON_DISABLE_INPUT_DEDUP`

vLLM-Neuron-only env var (default `0`). When set to `1`, the compile backend
skips the FX-graph input-deduplication pass at
`vllm_neuron/compile/backend.py:_detect_duplicate_inputs`. Every per-layer view
of a shared KV byte buffer becomes its own HLO parameter — for hybrid
SWA+Global models that's ~3× more KV parameter handles per NEFF.

This is the right knob when you suspect the FX-dedup is over-collapsing
storage-aliased views and forcing the compiler to schedule unnecessarily
serial scatter chains on the consolidated parameter. Empirical evidence on
Gemma4 60-layer @ TP=16, fp8, 8192 blocks:

| Configuration | KV params per NEFF | Post-warmup HBM | Output tok/s |
|---------------|-------------------:|----------------:|-------------:|
| dedup ON (default) | 40 | 6.82 GiB | 549.34 |
| dedup OFF | **120** | **6.32 GiB** | **584.72** |

Dedup-off gave **+6% throughput and –500 MiB scratchpad** without changing
the runtime KV byte count (still 1.25 GiB). For the conceptual reason
this works without changing memory, see the three-layer sharing model in
[kv_cache_memory_model.md](kv_cache_memory_model.md#the-mental-model-three-layers-of-sharing).

## Diagnostic checks (look for these in logs)

| Log message | Where | Implication |
|-------------|-------|-------------|
| `Maximum concurrency for X tokens per request: Yx` | `kv_cache_utils.py:1324` | The computed cap. If `Y < max_num_seqs`, cap will throttle throughput. |
| `Overriding num_gpu_blocks=X with num_gpu_blocks_override=Y` | `kv_cache_utils.py:829` | Confirms the override took effect. |
| `max_num_seqs exceeds worst-case KV cache capacity (X concurrent requests)` | vllm-neuron `scheduler.py:236` | Strong throughput limiter. Raise blocks or lower `max_model_len`. |
| `Prefill deferred: X running requests already at KV cache concurrency limit` | `scheduler.py:433` | The cap is actively serializing requests. |
| `Hybrid KV cache manager is disabled` | `kv_cache_utils.py:1175` | All layers got flattened to FullAttention. |
| `Add X padding layers, may waste at most Y% KV cache memory` | `kv_cache_utils.py:1059` | Group-padding overhead from the layer-count uniformity invariant. |
| `Input dedup: removed N duplicate placeholder(s) (M -> K)` | `vllm_neuron/compile/backend.py:124` | FX-level placeholder dedup. For hybrid models with shared KV, expect `M ≈ 3K` (5+ SWA layers per bucket collapse to 1 placeholder). |
| `Input dedup skipped (VLLM_NEURON_DISABLE_INPUT_DEDUP=1)` | `vllm_neuron/compile/backend.py:567` | Confirms the dedup-disable env var is in effect. Per-layer typed views become independent HLO parameters. |
| `Neuron HBM: X GiB used, Y GiB free` | `vllm_neuron/vllm/worker/neuron_worker.py:589` | Pre-KV (after weight load) and post-warmup (after KV alloc + warmup compile). Diff = KV bytes + activation scratchpad. |

## Decision flow for tuning

1. **Run the workload, find `Maximum concurrency: Yx` in the log.**
2. **If Y ≥ max_num_seqs**: KV is fine; tune elsewhere (engine, compile, kernel).
3. **If Y < max_num_seqs**:
   - Check `num_gpu_blocks_override`. If set explicitly, raise it.
   - If on auto: increase `gpu_memory_utilization` and/or
     `VLLM_NEURON_KV_GMU_BUDGET_CAP_FRACTION` (Neuron only).
4. **If concurrency reaches `max_num_seqs` but throughput is still below ideal**:
   - Per-step compute or DMA may be the bottleneck.
   - Look at decode-NEFF profile, not the KV cap.
5. **If raising `num_gpu_blocks_override` past a threshold REDUCES throughput**:
   - Larger KV pool is bloating compile artifacts or degrading runtime cache.
   - Find the smallest `num_gpu_blocks_override` that lifts the cap to
     `max_num_seqs` and stop there.
6. **If a hybrid-attention model is slower than its layer count suggests**:
   - Diff the FX-dedup behaviour by setting `VLLM_NEURON_DISABLE_INPUT_DEDUP=1`.
   - Compare post-warmup HBM and per-NEFF `DRAM hwm` (see
     [crash_triage.md](crash_triage.md) for how to extract `DRAM hwm`).

## Quick estimation formulas

### Total KV memory per request at full max_model_len

```
bytes_per_token = Σ over groups: layers_in_group × 2 × kv_heads × head_size × dtype_bytes
bytes_per_request = max_model_len × bytes_per_token
```

For Gemma4 60L TP=16 fp8:
- SWA: 50 layers × 2 × 1 × 256 × 1 = 25,600 B/token
- Global: 10 layers × 2 × 1 × 512 × 1 = 10,240 B/token
- Total: 35,840 B/token → 35,840 × 1024 = 36.7 MiB/request at full context

### Required `num_gpu_blocks_override` for target concurrency

The simplified equivalent of vLLM's formula:

```
num_block_per_request = cdiv(sum(per_spec_max_memory_after_unification), unified_page_size)
num_gpu_blocks_required = target_concurrency × num_block_per_request
```

For Gemma4 60L: `num_block_per_request ≈ 230`, so to support 64 concurrent
requests you need ≥ 14720 blocks. Round up to 16384 for slack.

### Validation against logs

If `Maximum concurrency: Yx` doesn't match your hand-computed value, check:

1. Is `unify_kv_cache_spec_page_size` running? (Different page sizes among
   specs trigger it.)
2. Is `unify_hybrid_kv_cache_specs` running? (Disable-hybrid path flattens
   all SWA → Full.)
3. Is `page_size_padded` set on any spec? (Backend-specific alignment.)
4. What is the actual `num_layer_per_group` (i.e., the post-grouping group
   size)? Use `min(layer_counts_per_spec)` or `max(layer_counts_per_spec)`
   depending on whether `max < 1.5 × min`.

## Source code references — concurrency formula

All paths relative to `vllm/v1/core/kv_cache_utils.py` unless noted.

| Function / location | Purpose |
|---------------------|---------|
| `get_max_concurrency_for_kv_cache_config` (line 802) | Top-level cap formula. |
| `max_memory_usage_bytes` (line 656) | Sum per-spec memory. |
| `vllm/v1/kv_cache_interface.py:65-87` | `AttentionSpec.real_page_size_bytes` definition. |
| `vllm/v1/kv_cache_interface.py:113-121` | `FullAttentionSpec.max_memory_usage_bytes`. |
| `vllm/v1/kv_cache_interface.py:247-251` | `SlidingWindowSpec.max_memory_usage_bytes`. |

For grouping internals (`get_kv_cache_groups`,
`unify_hybrid_kv_cache_specs`, etc.), see
[kv_cache_memory_model.md](kv_cache_memory_model.md#source-code-references--hybrid-grouping).

For storage-sharing internals (`KVCacheTensor.shared_by`, the runner
allocator, the FX dedup pass), see
[kv_cache_memory_model.md](kv_cache_memory_model.md#source-code-references--sharing-layers).

For vllm-neuron-specific scheduler enforcement: `vllm_neuron/vllm/core/scheduler.py:215-248`.
