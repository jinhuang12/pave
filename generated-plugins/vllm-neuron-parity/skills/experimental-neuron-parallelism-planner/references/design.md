# Parallelism-planner engine — design (`scripts/planner/`)

The chain, one concern per step:

- **§1 Aggregation** — raw per-model quantities (weights / FLOPs / KV), as fixed by shapes and
  dtypes. Knows **nothing** about TP/PP/CP/DP/EP — no divisors, no `world`, no `min(TP, n_kv)`.
- **§2 Sharding** — how each aggregate divides across devices: one divisor per `(class, type)`
  key. The only layer that knows the parallelism axes.
- **§3 Cost** — combine aggregate ÷ divisor with the operating point (`S`, `bs`, regime) into
  per-device totals, then a per-component roofline step time.
- **§4 Sweeping and selection** — sweep the config space to a per-bucket landscape, then
  select a recommended config under a serving objective.

The **Implementation** section (after §4) maps these steps onto the `scripts/planner/`
modules.

---

## Section 1 — Aggregations

The aggregation layer answers one question per quantity: *how much of it does the model
have, before any device split?* It emits **structured, segmented totals** rather than
scalars, so the sharding layer can later attach a divisor to each piece without the
aggregation layer having to know what a divisor is.

The unit of every aggregate is a **segment**. At minimum a segment is `(multiplicity,
unit)`; some classes carry extra *intrinsic* fields (KV's sequence cap, a FLOP term's
context flag), defined per subsection:

```
multiplicity : how many identical copies exist in the model (a layer count, or 1)
unit         : the per-copy quantity (params for weights; bytes/token for KV;
               FLOPs/token for compute)
```

A quantity is a list of segments, grouped by **class**, and each class is segmented along
**one** type-axis natural to it. The aggregation layer never multiplies a segment by a
divisor; it exposes `(multiplicity, unit, …)` and the segment's **key** (class + type
label), which is the handle the sharding layer uses.

**Scope of Section 1 (applies to every subsection below).** The aggregation layer owns only
the segment structure and its per-copy/per-token units. It is out of scope here to describe:
divisors, the `min(TP, n_kv)` replication ceiling, CP token-sharding, `world`, or the
prefill/decode regime — all of which are Section 2 (sharding). Evaluation-time inputs (`S`,
`bs`, regime → `n_tok`) are applied to these aggregates *after* Section 1, not baked into
the units. Norm (RMSNorm) params and biases are omitted throughout (negligible; not a class).

Two type-axes exist, and they are **independent partitions of the layer set** — not one
shared group index (see 1.1):

- **attention-type** — sliding vs global vs MLA (differ in `n_kv_heads`, `head_dim`,
  `kv_seq_cap`, MLA latent width).
- **ffn-type** — dense vs routed-MoE vs dense+MoE-parallel (differ in intermediate width,
  expert count).

### 1.1 — Weight class by layer type

Resident weight params decompose into **five classes**. Each class is a list of segments;
the segment's type label is drawn from the axis that class actually varies along (attention
classes vary along attention-type; FFN classes along ffn-type; `embed` is a singleton).

| class            | type-axis        | unit (per-copy params)                          |
|------------------|------------------|-------------------------------------------------|
| `attn_qo`        | attention-type   | `q_proj + o_proj` (or full MLA block)           |
| `attn_kv`        | attention-type   | `k_proj + v_proj` (0 for MLA; `k_eq_v` drops v) |
| `dense_mlp`      | ffn-type         | `3 · hidden · dense_intermediate` (SwiGLU; incl. folded shared experts) |
| `routed_experts` | ffn-type         | `3 · hidden · moe_intermediate · num_experts`   |
| `embed`          | — (singleton)    | `vocab · hidden` (×2 if untied)                 |

Per-class total = `Σ_segment multiplicity · unit`. Model total = `Σ_class` of those.

**Why two independent type-axes and not one group index.** Attention structure and FFN
structure vary along **different** partitions of the layer set, so a single shared index
cannot represent both without distortion:

- **gemma** varies attention (50 sliding + 10 global) but has a **uniform** FFN. A shared
  index would duplicate the FFN: `dense_mlp = [(50, dense), (10, dense)]` — split along an
  axis the FFN does not vary on. The correct aggregate is `[(60, dense)]`.
- **deepseek** (`first_k_dense_replace`) varies FFN but has **uniform** attention. A shared
  index would over-segment attention instead.
- A model that varies **both** (mixed attention *and* a split FFN) is a product of two
  partitions that one index cannot express at all.

Independent axes handle all three: each class segments along its own partition, gemma's FFN
collapses to one segment, and the mixed case falls out for free (two independent lists, no
product enumeration).

**Where the multiplicities come from.**

- **Attention classes** — the per-attention-type layer counts (`n_sliding`, `n_global`, …),
  read from `layer_types`.
- **FFN classes** — the per-ffn-type layer counts, on the FFN's **own** count axis,
  independent of the attention partition. A uniform-FFN model is a single segment spanning
  all layers.
- **`embed`** — always one segment, multiplicity 1.

**Corrections belong in the unit.** Two model-specific weight adjustments are properties of
the *aggregate*, so they are folded into the relevant class's `unit`, not applied as a
separate step:

- **tied embed / lm_head table** — folded into `embed` (counted once when tied, twice when
  untied). lm_head is not a sixth class; it is the same `vocab · hidden` table the embedding
  owns under tying.
- **phantom global v_proj (`attention_k_eq_v`)** — on global layers V aliases K, so there is
  no `v_proj`; the `attn_kv` unit for the global attention-type segment omits it. This is a
  unit definition charged to `attn_kv`, the class it belongs to.

### 1.2 — KV cache by layer type

KV is a **single class, segmented along the attention-type axis only** — it is purely an
attention-side quantity (no FFN axis, no embed, no Q/O split). It reuses the **same
attention-type partition** as the `attn_kv` weight class.

A KV segment carries more than a weight segment, because the cache scales with sequence
length and batch:

```
KVSegment = (multiplicity, unit_per_token, kv_seq_cap)
    multiplicity   : layer count of that attention-type       (= AttnGroup.count)
    unit_per_token : per-token-per-layer KV bytes
                     = 2 · n_kv_heads · head_dim · kv_elem_bytes   (GQA/MHA)
                     = kv_cache_dim · kv_elem_bytes                (MLA: one latent; no ×2, no n_kv)
    kv_seq_cap     : window (sliding) or None (full-context / global)
```

The per-token unit and the `kv_seq_cap` are all §1 owns; turning them into a cache total
(× tokens cached × batch) is the operating-point step in §3 (see `eff_ctx`).

Three properties to keep straight:

- **`S` and `bs` are evaluation-time inputs, not part of the unit.** The unit is per-token;
  `S`/`bs` are applied when a cell is evaluated.
- **`kv_seq_cap` rides on the segment.** It is intrinsic (the window is what makes a sliding
  segment stop growing with context), so — unlike `S`/`bs` — it travels with the segment.
- **`kv_elem_bytes` is a unit property.** fp8-KV with bf16 weights is common, so KV bytes
  use `kv_dtype_bytes` if set, else `dtype_bytes`, folded into `unit_per_token`.

**MLA changes the unit's formula, not the segmentation.** Same attention-type axis; the
segment computes `unit_per_token` from the latent width (`kv_cache_dim`) instead of
`2 · n_kv · head_dim`. The segment key stays `(attn_kv, attention-type)`.

### 1.3 — FLOPs by class

FLOPs are **not** an independent aggregation the way weights and KV are. Each FLOP component
is produced by a specific weight class (the projection matmuls) or borrows one (the
weightless attention-score term), so FLOPs decompose along the **same classes and the same
two type-axes** as weights. This lets one per-class structure carry weights and FLOPs
together, rather than FLOPs inventing a separate partition.

All units below are **per token, per layer** (factor 2 = one multiply-add). A FLOP segment
mirrors a weight segment plus a per-token unit and a context flag:

```
FlopSegment = (multiplicity, unit_per_token, class_key, ctx_scaled)
    multiplicity   : layer count of that type   (= the producing weight class's multiplicity)
    unit_per_token : per-token-per-layer FLOPs
    class_key      : which class this compute belongs to (groups it with the matching weights)
    ctx_scaled     : True  -> scales with eff_ctx (see §3)   [attention scores]
                     False -> per-token flat                 [projections, FFN]
```

**Per-class FLOP terms:**

| class_key             | component                | unit_per_token                                    | ctx_scaled |
|-----------------------|--------------------------|---------------------------------------------------|-----------|
| `attn_qo`             | Q proj + O proj          | `2 · attn_qo_params` = `4·hidden·n_q·head_dim`     | no |
| `attn_qo` (borrowed)  | attention scores QK^T+PV | `4 · n_q · head_dim` (per cached token)           | **yes** |
| `attn_kv`             | K proj + V proj          | `2 · attn_kv_params` = `4·hidden·n_kv·head_dim`    | no |
| `dense_mlp`           | gate + up + down         | `6 · hidden · dense_intermediate`                 | no |
| `routed_experts`      | k active experts         | `6 · hidden · moe_intermediate · experts_per_tok` | no |
| `embed` (lm_head)     | logit projection         | `2 · vocab · hidden` (over sampled positions)     | no |

Turning these per-token units into a FLOP total (× tokens, and × `eff_ctx` for `ctx_scaled`
segments) is the operating-point step in §3.

**Salient points:**

- **Each projection FLOP is `2 × (its weight class's params)`.** Q/O, K/V, dense MLP, and
  routed experts each have a FLOP term that is exactly twice the resident params of the
  matching weight class — same shape, so they group under the same `class_key`. This is the
  property that lets weights and FLOPs share one segment structure.
- **Attention scores are the one weightless term.** No params, so no weight class of its
  own; it is a FLOP-only segment keyed to `attn_qo` (per-query-head work). It is the only
  projection-side term that scales with `eff_ctx`, so sliding segments stop growing —
  `ctx_scaled` is the FLOP analogue of KV's `kv_seq_cap`.
- **K/V-proj FLOPs use the real K/V params**, inheriting the `k_eq_v` v_proj drop once 1.1
  folds it into the `attn_kv` unit (see reconciliation).
- **`embed`/lm_head is a matmul like any other:** `2 · vocab · hidden` FLOPs, keyed to the
  `embed` class. It is computed over the **sampled positions** (~1 token/sequence in decode;
  the prompt in prefill if all positions are projected), so its token multiplier is the
  sampled-token count, not `n_tok`. No special-casing beyond that.
- **MLA:** Q/O collapses to the whole MLA block, the K/V-proj term is 0 (folded), and the
  score term uses `score_dim = qk_head_dim + v_head_dim` in place of `2·head_dim`.

**Validation — the oracle is ground truth.** The shape-exact meta-device FLOP counter (the
oracle) counts every matmul the model actually issues. The FLOP segment sums must match it:
the full attention block (Q/O + K/V + scores), dense/MoE FFN, and the **lm_head logit
projection** over the sampled positions. Counting all of these — including lm_head, and
dropping the aliased v_proj on `k_eq_v` global layers (§1.1) — is what makes the segment
totals match the oracle to the arithmetic.

---

## Section 2 — Sharding

The sharding layer takes the Section 1 segments and answers: *how is the workload divided
across ranks?* It emits a **divisor per `(class, type)` key**; per-device = aggregate /
divisor. This is the only layer that knows about TP/CP/DP/EP.

**Scope.**

In scope:
- **TP (SP folded in)** — SP is folded into TP (prefill-only, comm-neutral, activation-only,
  no separate divisor); the notation marks that it rides the TP group rather than being a
  distinct axis.
- **CP, DP, EP** divisors.
- **Regime** (prefill/decode) that modulates them.

Out of scope:
- **PP** — not implemented by vllm-neuron. *Working expectation (to validate when PP lands):*
  PP partitions layers into stages, so the per-layer TP/CP sharding logic stays **identical**
  on every stage; `world` scales by PP (`world = TP·PP·DP`) and per-device weight/KV/FLOP
  divide by PP (fewer layers/stage). Only the **throughput/latency** calc changes (latency
  ×PP, pipelined throughput + P2P comm). Assumes PP splits identical layer sequences;
  heterogeneous (mixed-attention) stage boundaries would need per-stage layer assignment.
- **Attention / component-level DP sharding** — planned future work (not yet modeled).
- **Vision-encoder parallelism** — planned future work (not yet modeled).

**EP vs no-EP split.** The two cases are structurally different and are designed separately:

- **No EP** (dense models, or MoE without `--enable-expert-parallel`): DP is a *free outer
  loop* — replicas are fully independent (no cross-DP collectives), so per-device entitlement
  is DP-invariant and DP only multiplies throughput / collapses footprint. The live axes are
  **TP and CP**.
- **EP** (`--enable-expert-parallel`): DP is *not* free — MoE layers exchange tokens across
  DP via cross-DP collectives, so DP changes per-device expert sharding (`world = TP·DP`).
  This case is designed in a later subsection.

### 2.1 — No EP: DP outer loop, TP as world, CP inside TP

`world = TP` (one replica). DP replicates that replica to fill the server; EP off, PP=1.

**DP — free outer loop.** Each DP replica is an independent copy: identical weights, identical
per-device entitlement, no cross-replica traffic during inference. So DP is not a divisor on
any class — it is a throughput/footprint multiplier applied *after* the per-device cost is
known. The planner collapses it into the per-node-throughput metric
(`otps · cores_per_node / cores`).

**Worked layout — a 64-core node at DP=8, TP=8, CP=2.** `world = TP = 8` (one replica);
`DP=8` replicas fill the 64 cores (`DP · TP = 64`). CP=2 is carved *inside* each replica's
TP=8, splitting it into `CP=2` sequence-shards × `TP/CP = 4` head-shards — it adds no cores.

```
64-core node = 8 DP replicas × (TP=8 replica)

  DP0: [ r0  r1  r2  r3  r4  r5  r6  r7 ]   ← one TP=8 replica
  DP1: [ r8  r9  r10 r11 r12 r13 r14 r15]
  DP2: [ r16 ... ... ... ... ... ... r23]
  DP3: [ r24 ... ... ... ... ... ... r31]
  DP4: [ r32 ... ... ... ... ... ... r39]
  DP5: [ r40 ... ... ... ... ... ... r47]
  DP6: [ r48 ... ... ... ... ... ... r55]
  DP7: [ r56 ... ... ... ... ... ... r63]

  inside one TP=8 replica (e.g. DP0), CP=2 carves the 8 ranks:
    CP shard 0 (seq half A):  [ r0 r1 r2 r3 ]   ← TP/CP = 4 head-shards
    CP shard 1 (seq half B):  [ r4 r5 r6 r7 ]   ← TP/CP = 4 head-shards
```

The 8 DP replicas are **identical and independent** — they scale throughput for free (no
cross-replica traffic, per-device cost unchanged). So from here we **focus on one replica**
(one TP=8 / CP=2 block) and design the TP/CP sharding within it; DP just multiplies the
result across the node.

#### 2.1.1 — Prefill CP (`apply_prefill_dcp`): the 2-D head × CP grid

Within one TP=8 / CP=2 replica, prefill DCP arranges the ranks as a **2-D grid**:
`TP/CP = 4` head-shards (the `dcp_tp_group`) × `CP = 2` groups (the `cp_kv_group`). The
head-shard axis shards the attention weights; the CP axis shards the sequence and KV cache.

Worked example with **n_q = 8, n_kv = 2** (so the head-shard axis `TP/CP = 4` holds
`n_q/(TP/CP) = 2` Q heads per rank, and `TP/CP = 4 > n_kv = 2` → KV replicates on the head
axis; GQA ratio 4, so Q0–Q3→KV0 and Q4–Q7→KV1):

```
                    CP axis (sequence / KV-cache split)
                 ┌─────────────────────┬─────────────────────┐
                 │   CP group 0        │   CP group 1        │
                 │   tokens A          │   tokens B          │
                 │   (r0–r3)           │   (r4–r7)           │
  ┌──────────────┼─────────────────────┼─────────────────────┤
  │ head-shard 0 │ r0                  │ r4                  │
  │              │  Q heads: Q0,Q1     │  Q heads: Q0,Q1     │  ← Q0,Q1 replicated across CP
  │              │  KV head: KV0       │  KV head: KV0       │
  │              │  KV$: KV0 · tokA    │  KV$: KV0 · tokB    │
  ├──────────────┼─────────────────────┼─────────────────────┤
  │ head-shard 1 │ r1                  │ r5                  │
  │              │  Q heads: Q2,Q3     │  Q heads: Q2,Q3     │  ← Q2,Q3 replicated across CP
  │              │  KV head: KV0 (rep) │  KV head: KV0 (rep) │  ← KV0 replicated on head axis
  │              │  KV$: KV0 · tokA    │  KV$: KV0 · tokB    │
  ├──────────────┼─────────────────────┼─────────────────────┤
  │ head-shard 2 │ r2                  │ r6                  │
  │              │  Q heads: Q4,Q5     │  Q heads: Q4,Q5     │
  │              │  KV head: KV1       │  KV head: KV1       │
  │              │  KV$: KV1 · tokA    │  KV$: KV1 · tokB    │
  ├──────────────┼─────────────────────┼─────────────────────┤
  │ head-shard 3 │ r3                  │ r7                  │
  │              │  Q heads: Q6,Q7     │  Q heads: Q6,Q7     │
  │              │  KV head: KV1 (rep) │  KV head: KV1 (rep) │  ← KV1 replicated on head axis
  │              │  KV$: KV1 · tokA    │  KV$: KV1 · tokB    │
  └──────────────┴─────────────────────┴─────────────────────┘

  head-shard axis (dcp_tp_group, size TP/CP)  →  shards Q/O + attention weights
  CP axis (cp_kv_group, size CP)              →  shards the sequence + KV cache
```

Reading the grid:

- **Q weights** — tile the head-shard axis (`n_q/(TP/CP)` heads per rank; 2 here),
  **replicated across the CP axis** (both columns hold the same Q heads). Sharded on head,
  replicated on CP.
- **K / V weights** — only `n_kv` distinct heads, so they shard down the head axis to `n_kv`
  then **replicate on the head axis** (`(TP/CP)/n_kv` copies), and are **also replicated
  across the CP axis**. Replicated on **both** axes.
- **KV cache** — the same KV heads sit in both CP columns, but each column stores a
  **different token slice** (tokens A vs B). The CP axis is what genuinely splits the cache;
  the head axis only picks *which* KV head a rank holds.
- **O weights** — not drawn (to keep the grid readable), but O is row-parallel and shards by
  the **same query heads as Q**: each rank owns the O columns for exactly its Q heads. So O
  follows Q identically — sharded on the head axis, replicated across CP — and shares Q's
  divisor throughout.

**Deriving the divisors** (per-device = aggregate / divisor). Let `H = TP/CP` be the
head-shard-axis size.

- **Q/O weights** — sharded across the `H` head-shards, replicated across `CP`. Distinct
  copies = `H`.
  → **`qo_div = TP/CP`**
- **K/V weights** — sharded to `min(H, n_kv)` on the head axis (replicated `H/min(H,n_kv)`
  beyond that), replicated across `CP`. Distinct copies = `min(H, n_kv) = min(TP/CP, n_kv)`.
  → **`kv_weight_div = min(TP/CP, n_kv)`**
- **KV cache** — head divisor `min(TP/CP, n_kv)` (same as KV weights — cache holds one entry
  per distinct KV head a rank owns) × sequence divisor `CP` (the token split).
  → **`kv_cache_div = min(TP/CP, n_kv) · CP`**

**Why KV weight and KV cache divide differently — the CP factor.** Take KV head KV0 in the
example. From the grid it lives on **4 ranks**: r0, r1 (head-axis replication — only `n_kv=2`
distinct KV heads across `H=4` head-shards, so 2 ranks share each) and r4, r5 (CP-axis
replication — each CP group holds the full KV-head set). That is the weight replication
factor `CP · H/min(H,n_kv) = TP/min(TP/CP,n_kv) = 4` — 4 identical *weight* copies.

But those 4 ranks do **not** hold the same *cache*: r0,r1 store token-slice A while r4,r5
store token-slice B — the CP axis splits tokens. So along the CP axis the KV weight is pure
replication, but the KV cache on those ranks is different. Hence the CP factor is absent from
the weight divisor (`kv_weight_div = min(TP/CP, n_kv)`) but present in the cache divisor
(`kv_cache_div = min(TP/CP, n_kv) · CP`): same ranks, same KV head — replicated weight, split
cache. CP "wastes" KV weight memory to buy KV-cache sharding.

#### 2.1.2 — Decode CP: full-TP weights, CP splits only the cache

Decode CP is structurally different from prefill DCP: **CP does not re-shard the weights** —
attention shards by *full TP*, and CP only splits the KV cache along the sequence. DCP pairs
consecutive ranks into `TP/CP` replica sets of `CP` ranks each (`cp_rank = tp_rank % CP`);
ranks in a set hold the **same (replicated) KV head** and **own different token slices**.
Decode requires `TP > n_kv` and `CP ≤ TP // n_kv`.

Worked example with **n_q = 8, n_kv = 2**, TP=8, CP=2 (full-TP query sharding → one Q head
per rank; GQA ratio 4, so Q0–Q3→KV0, Q4–Q7→KV1):

```
                       CP axis (sequence / KV-cache split)
                    ┌──────────────────┬──────────────────┐
                    │  cp_rank 0       │  cp_rank 1       │
                    │  even tokens     │  odd tokens      │
  ┌─────────────────┼──────────────────┼──────────────────┤
  │ replica set 0   │ r0               │ r1               │
  │  (KV0)          │  Q head: Q0      │  Q head: Q1      │  ← distinct Q heads (full-TP shard)
  │                 │  KV head: KV0    │  KV head: KV0    │  ← same KV head, split by tokens
  │                 │  KV$: KV0·evenT  │  KV$: KV0·oddT   │
  ├─────────────────┼──────────────────┼──────────────────┤
  │ replica set 1   │ r2               │ r3               │
  │  (KV0)          │  Q head: Q2      │  Q head: Q3      │
  │                 │  KV head: KV0    │  KV head: KV0    │  ← KV0 replicated again (this set)
  │                 │  KV$: KV0·evenT  │  KV$: KV0·oddT   │
  ├─────────────────┼──────────────────┼──────────────────┤
  │ replica set 2   │ r4               │ r5               │
  │  (KV1)          │  Q head: Q4      │  Q head: Q5      │
  │                 │  KV head: KV1    │  KV head: KV1    │
  │                 │  KV$: KV1·evenT  │  KV$: KV1·oddT   │
  ├─────────────────┼──────────────────┼──────────────────┤
  │ replica set 3   │ r6               │ r7               │
  │  (KV1)          │  Q head: Q6      │  Q head: Q7      │
  │                 │  KV head: KV1    │  KV head: KV1    │
  │                 │  KV$: KV1·evenT  │  KV$: KV1·oddT   │
  └─────────────────┴──────────────────┴──────────────────┘

  full TP (all 8 ranks)  →  shards Q/O over distinct heads (CP does NOT re-shard)
  CP axis (within a set) →  splits the sequence + KV cache; replicates the KV head
```

(Q is AllGathered across the CP set at attention time so each rank can attend over the
gathered heads against its local KV slice, combined by LSE — but the *stored/projected*
weight is still one Q head per rank. The gather is runtime traffic, not a change to the
weight shard.)

Reading the grid — the contrast with prefill DCP:

- **Q / O weights** — sharded across **full TP** into distinct heads; **not replicated
  across CP** (r0 and r1 hold *different* Q heads Q0, Q1). Prefill replicated Q0 across the
  CP column; decode does not.
- **K / V weights** — sharded to `min(TP, n_kv)` on the full-TP head axis, then replicated
  `TP/n_kv` ways (KV0 on r0–r3, KV1 on r4–r7). CP does **not** enter the weight shard.
- **KV cache** — within each replica set the CP axis splits tokens (even/odd), so the cache
  divides by the head divisor **and** CP.

**Deriving the divisors:**

- **Q/O weights** — full-TP head shard, CP does not touch them.
  → **`qo_div = TP`**
- **K/V weights** — sharded to `min(TP, n_kv)`, replicated beyond; CP does not touch them.
  → **`kv_weight_div = min(TP, n_kv)`**
- **KV cache** — head divisor `min(TP, n_kv)` × sequence divisor `CP` (bounded `CP ≤ TP//n_kv`,
  which the decode constraint guarantees).
  → **`kv_cache_div = min(TP, n_kv) · CP`**

**Prefill vs decode — the one structural difference.** The KV-cache divisor is the *same
shape* in both (`min(head-axis, n_kv) · CP`); what differs is **whether CP carves the weight
head axis**:

| divisor         | prefill DCP (`apply_prefill_dcp`) | decode CP           |
|-----------------|-----------------------------------|---------------------|
| `qo_div`        | `TP/CP`                           | `TP`                |
| `kv_weight_div` | `min(TP/CP, n_kv)`                | `min(TP, n_kv)`     |
| `kv_cache_div`  | `min(TP/CP, n_kv) · CP`           | `min(TP, n_kv) · CP`|

Prefill DCP re-shards the attention weights onto the `TP/CP` head axis (replicated CP×);
decode keeps weights on full TP and spends CP purely on cache sharding. Dense MLP / embed /
lm_head shard by full TP in both regimes (CP never touches them).

### 2.2 — EP: DP folds into world, experts shard across it

The defining change from the no-EP case is the **world size**. With EP off, `world = TP` and
DP is a free outer loop (2.1). With EP on, the routed experts shard across **`world = TP·DP`**
— so DP is no longer a free replica: it *widens* expert sharding and pulls MoE collectives
across DP replicas. Everything non-expert (attention, dense MLP, embed, lm_head) still shards
by **TP** and is replicated across DP, exactly as before.

Worked layout — **TP=4, DP=2 → world = 8**:

```
world = TP·DP = 4·2 = 8      (EP: DP is part of world, not a free replica)

              TP = 4  (attention / dense / embed / lm_head shard here, per replica)
              ─────────────────────────────
  DP0:   [ r0   r1   r2   r3 ]
  DP1:   [ r4   r5   r6   r7 ]

  attention / dense MLP / embed / lm_head : shard across TP=4, REPLICATED across DP
  routed experts                          : shard across world = TP·DP = 8
```

The routed experts split that `world = 8` as a **2-D grid `ep_degree × ep_tp = world`**:

- **`ep_degree`** — the expert-dim split (`num_local_experts = num_experts / ep_degree`).
- **`ep_tp = world / ep_degree`** — the intermediate-dim split *within* an EP partition.

Two operating points:

```
default full EP:  ep_degree = world = 8,  ep_tp = 1
  → each rank holds num_experts/8 whole experts at FULL intermediate

override e.g. ep_degree = 4, ep_tp = 2   (grid 4 × 2 = 8)
  ┌──────────────┬──────────────────┬──────────────────┐
  │              │  ep_tp 0         │  ep_tp 1         │
  ├──────────────┼──────────────────┼──────────────────┤
  │ EP part 0    │ E0,E1 inter[0:½] │ E0,E1 inter[½:]  │
  │ EP part 1    │ E2,E3 inter[0:½] │ E2,E3 inter[½:]  │
  │ EP part 2    │ E4,E5 inter[0:½] │ E4,E5 inter[½:]  │
  │ EP part 3    │ E6,E7 inter[0:½] │ E6,E7 inter[½:]  │
  └──────────────┴──────────────────┴──────────────────┘
     experts split ep_degree=4 ways (rows) × intermediate ep_tp=2 ways (cols)
```

Either way the routed-expert divisor is the product `ep_degree · ep_tp = world`. Tokens are
**not** routed to a core subset: every rank holds all tokens, runs its local experts, and a
single all-reduce over full TP combines the partial sums (unrouted tokens contribute zero).

**Divisors.** Per-device = aggregate / divisor. `world = TP·DP`; `ep_tp = world / ep_degree`.
The only EP-sharded class is `routed_experts`; everything else is the 2.1 divisor with DP as
pure replication (per-device divisor stays TP-based, not TP·DP).

| class            | divisor                              | EP / DP / CP interaction |
|------------------|--------------------------------------|--------------------------|
| `routed_experts` | **`world = TP·DP = ep_degree·ep_tp`**| the one EP-sharded class; DP widens it |
| `dense_mlp`      | `TP`                                 | full TP (framework-prescribed); replicated across DP and across `ep_degree` |
| `attn_qo`        | `TP` (dec) / `TP/CP` (prefill-DCP)   | EP-independent; replicated across DP; CP as in 2.1 |
| `attn_kv` weight | `min(TP, n_kv)` / `min(TP/CP, n_kv)` | EP-independent; replicated across DP |
| `kv_cache`       | `min(TP, n_kv)·CP` / `min(TP/CP, n_kv)·CP` | EP-independent; replicated across DP |
| `embed` / `lm_head` | `TP`                              | EP-independent; replicated across DP |

**Coexistence with CP.** EP and CP are orthogonal — different classes, different axes. CP
shards the attention sequence / KV cache (and, in prefill-DCP, the attention weight head
axis) and is **carved from TP** (adds no cores). EP shards the routed experts across
`world = TP·DP`. Because CP adds no cores, it does not change `world`, so the expert grid
`ep_degree · ep_tp = world` is identical whether CP=1 or CP>1. The two compose without
interaction; they only both matter at once on a long-context MoE model.

**Open — PP as an outer loop around TP·DP.** PP is **not implemented by vllm-neuron**, so its
interaction with EP is a design expectation to validate if/when PP lands. The working
expectation: PP partitions layers into stages (outer loop), and since a layer's experts
cannot span stages that do not hold that layer, experts should shard per-stage across
**`TP·DP`**. Verify, when PP exists, that: (a) the effective per-layer expert divisor is
`TP·DP` (PP does not enter the expert grid), and (b) PP leaves every divisor in the grid
above unchanged — acting purely as a layer-partition outer loop, exactly as in the no-EP case
(2.1 scope note). If so, the whole EP grid is PP-invariant and PP only scales layer count per
stage + pipelining throughput/latency.

### 2.3 — FLOP divisors: reuse the weight-class divisors

FLOPs reuse the **same divisor per `class_key`** as the weights, by construction: §1.3
showed each projection FLOP term is `2 × (its weight class's params)` — same shape, same
shard — so it divides by the same divisor. No new divisors are introduced; two evaluation
factors are layered on top.

| FLOP term          | class_key        | divisor (= weight divisor) | extra factor |
|--------------------|------------------|----------------------------|--------------|
| Q/O projection     | `attn_qo`        | `TP` / `TP/CP` (prefill-DCP)| × `cp_g` (prefill) |
| attention scores   | `attn_qo` (borrowed) | `TP` / `TP/CP`         | × `cp_g` (prefill) |
| K/V projection     | `attn_kv`        | `min(TP, n_kv)` / `min(TP/CP, n_kv)` | × `cp_g` (prefill) |
| dense MLP          | `dense_mlp`      | `TP`                       | × `cp_g` (prefill) |
| routed experts     | `routed_experts` | `world = TP·DP`            | × `cp_g` (prefill) |

Two factors on top of the reused weight divisor:

- **Token split `cp_g` (prefill only).** Prefill shards the sequence/token compute CP ways
  per group; decode is one token, so `cp_g = 1`. This is the FLOP-side counterpart of KV's
  cache split — CP divides *compute* in prefill, not just cache. (SWA groups: `cp_g = 1`, per
  the 2.1 convention.)
- **Nothing else.** The `ctx_scaled` factor on the attention-score term (× `eff_ctx`) is an
  *aggregation* property (§1.3), already baked into the segment's `unit_per_token` before any
  divisor — it is not a sharding factor.

Consistency check on the score term: in prefill-DCP the scores divide by
`qo_div · cp_g = (TP/CP)·CP = TP` — i.e. full-TP shares the score work, which is correct
(scores are per-query-head, and the query heads span full TP once CP tokens are recombined).
In decode, `qo_div · cp_g = TP · 1 = TP`. So the score term lands on `TP` in both regimes,
via different routes.

So the FLOP layer adds **no divisor of its own** — it reuses each class's weight divisor and
multiplies in `cp_g` for prefill. This is the payoff of keying FLOPs to the weight classes in
§1.3: one divisor table serves both.

---

## Section 3 — Per-config cost

Section 3 is the component that composes §1 and §2 into the **cost of one config**. §1 gives
each segment its `(multiplicity, unit, …)`; §2 gives a `divisor` per `(class, type)` key;
Section 3 combines them — plus the **operating point** (`S`, `bs`, regime) that neither §1 nor
§2 owns — into per-device totals (§3.1), then a step time whose headline outputs are the
per-config **latency metrics**: decode ITL and prefill **TTFT** (§3.2). No new sharding or
aggregation logic — it multiplies, sums, and applies the roofline. This is the per-config
metric set §4 sweeps and selects over.

**Operating-point inputs** (supplied here, not in §1/§2): `S` (context), `bs` (batch),
`regime` (decode/prefill → `n_tok = bs` or `S·bs`).

**Canonical derived quantity — `eff_ctx`** (defined once; referenced everywhere): the number
of tokens a segment actually caches/attends over,

```
eff_ctx = min(S, cap)      # cap = the segment's kv_seq_cap (§1 intrinsic); None ⇒ full S
```

An uncapped (full-context) segment has `eff_ctx = S`; a capped segment saturates at its
window. All later uses of `eff_ctx` in this doc refer to this definition.

### 3.1 — Scaling up: segment → layer-type → full-model component

**Step 1 — per-segment per-device value.** One uniform form; the eval factors are gated by
segment type (weights carry none; KV/FLOP carry the per-token factors):

```
weight segment:   multiplicity · unit          / divisor
kv segment:       multiplicity · unit_per_token / divisor · eff_ctx · bs
flop segment:     multiplicity · unit_per_token / divisor · [eff_ctx if ctx_scaled] · n_tok
```

`multiplicity` is the layer count of that segment's type, so a single segment value **already
spans all layers of its type** (e.g. all layers of a given attention- or ffn-type). No
separate per-layer loop is needed — the layer count is folded into the segment.

**What scales what.** The eval factors are not per-segment special cases; they apply by
segment *kind*:

- **Per-copy (weights):** `mult / divisor`. Independent of `S` and `bs` — resident params do
  not grow with sequence or batch.
- **Per-token, sequence-scaled (KV cache, attention scores):** `mult / divisor · eff_ctx · bs`.
  The segment's `cap` (a §1 intrinsic) is the *only* thing that makes this conditional — an
  uncapped type scales with full `S`, a capped type saturates at its window.
- **Per-token, sequence-flat (FLOP projections, FFN):** `mult / divisor · n_tok`
  (`n_tok = bs` decode, `S·bs` prefill). Scales with tokens processed, not with `eff_ctx`.

So `multiplicity` and `bs` are uniform multipliers on their respective kinds; `eff_ctx` is the
single factor gated by the segment (via its cap). Nothing here is sharding — it is the
operating point applied on top of §2's divisor.

**Step 2 — sum segments within a component → per-layer-type totals.** Group the segments by
model component and sum:

- **Attention**, per attention-type (sliding / global / MLA): sum `attn_qo` + `attn_kv`
  weight segments (→ weight bytes), the attention-score flop segment (→ FLOPs), and the
  `kv_cache` segment (→ KV bytes) for that type. Yields a `(weight, kv, flop)` triple **per
  attention-type**.
- **FFN**, per ffn-type (dense / routed-MoE / parallel dense+MoE): sum `dense_mlp` +
  `routed_experts` weight segments (→ weight bytes) and their flop segments (→ FLOPs); FFN has
  no KV. Yields a `(weight, kv=0, flop)` triple **per ffn-type**.

Because the two type-axes are independent (§1.1), attention-types and ffn-types are summed on
their own partitions — a mixed-attention / uniform-FFN model produces several attention-type
triples but one FFN triple, with no cross-product.

**Step 3 — sum across types → full-model component totals.** Sum the per-type triples into
three model-level components:

| component  | rolls up                                   | weight bytes | KV bytes | FLOPs |
|------------|--------------------------------------------|:---:|:---:|:---:|
| **attention** | all attention-type triples (sliding + global + …) | ✓ | ✓ | ✓ |
| **FFN**       | all ffn-type triples (dense + routed + …)  | ✓ | — | ✓ |
| **lm_head**   | the `embed` singleton (tied embed / lm_head table) | ✓ | — | ✓ |

Section 3 emits a **separate `(weight_bytes, kv_bytes, flops)` triple per component** —
attention, FFN, lm_head. The full-model per-device figure for any one quantity is the sum of
the three components' contributions (KV = attention only; lm_head FLOPs are the logit
projection `2·vocab·hidden` over the sampled positions, §1.3).

**Why keep the three components separate** rather than one grand total: the cost model
downstream binds on them differently — attention carries the KV-read term that dominates
long-context decode, FFN carries the weight-read / expert term, lm_head is the tied table
that streams every step. The per-component split is what lets the binding-constraint analysis
attribute a step to the right term.

### 3.2 — Cost model: per-component roofline, then serialize

§3.1 gives each component a per-device `(weight_bytes, kv_bytes, flops)` triple. §3.2 turns
those into a step time. Two hardware constants: HBM bandwidth `BW` (bytes/s) and tensor-engine
peak `TE` (flops/s).

**Within a component — a roofline `max`.** A component's HBM loads (weights + KV) and its
tensor-engine compute run **concurrently** on the device (the engine computes while the next
tile streams from HBM), so the component's time is the larger of the two, not their sum:

```
load_time(c)  = (weight_bytes(c) + kv_bytes(c)) / BW
flop_time(c)  = flops(c) / TE
time(c)       = max(load_time(c), flop_time(c))
```

`load_time` reads **weights and KV together** — both must stream from HBM each step, so they
sum into one load term (not a `max` between them). This is the roofline: a component is
memory-bound when `load_time > flop_time` (its arithmetic intensity sits left of the ridge)
and compute-bound otherwise. The weight-vs-KV split is **diagnostic only** (is the load
weight-dominated or KV-dominated?), not two separate roofline terms — e.g. attention at
long-context decode is load-bound with KV dominating that load; FFN at small batch is
load-bound with weights dominating; prefill is typically compute-bound.

**Across components — additive, not pipelined.** Attention, FFN, and lm_head are separated by
**collectives** (each component ends in an all-reduce / reduce-scatter that must complete before
the next begins), so they **cannot overlap** — a component's compute cannot start until the
prior component's collective has delivered its input. The collectives themselves are also
exposed between components. So the step is the **sum**:

```
step_time = time(attention) + time(FFN) + time(lm_head) + comm_time
```

where `comm_time` is the collective cost (§2's collectives: TP all-reduce per component, CP
K/V ring, EP all-to-all). A single global `max(weight_load, kv_load, matmul, comm)` would
implicitly assume everything overlaps, which the inter-component collective barriers forbid.
The correct shape is **`max` within a component, `+` across components (and comms)**.

Roofline, then, at the step level:

```
step_time = Σ_c max(load_time(c), flop_time(c))  +  comm_time
            └──────────── per-component roofline ────────────┘
```

Consequences worth stating:

- **Each component reports its own binding term** — `load` (weights + KV, summed) vs `flop`
  — so the step's binding analysis is per-component, not one global winner. Weight-vs-KV is a
  sub-attribution *within* the load term, not a separate binder.
- **comm is additive**, not hidden under a `max` — because the collectives sit on the
  critical path between components and are not pipelined against compute in the current stack,
  so their time is exposed and adds to the step.

_Latency vs throughput (batch, PP pipelining) and the DP per-node normalization build on
`step_time` next._

---

## Section 4 — Sweeping and selection

§3 gives the cost of one config. Section 4 is the outer loop: **sweep** the config × bucket
space into a per-bucket landscape (§4.1), then **select** a recommended config from it (§4.2).

### 4.1 — Sweep

The sweep enumerates candidate configs and evaluates each (via §3) at every context bucket
and its max-feasible batch, producing the per-bucket landscape §4.2 selects from. Two axes to
the enumeration: the **parallelism grid** (`TP × CP × DP`, EP folded in per §2.2, PP out of
scope) and the **context buckets** (a config-derived set of context lengths).

**Fixed-core-budget sweep (planned, not implemented).** Today the grid enumerates each axis
independently and lets `world = TP·DP` float. The intended model is instead a **fixed core
budget**: given a target device count `C` (e.g. one node = 64, or `k` nodes), sweep only the
configs that *fill* `C` — i.e. all `(TP, CP, DP[, ep_degree])` with `TP·DP = C` (CP carved
from TP, adds no cores). This is **necessary for EP**: with EP on, DP is part of `world`
(experts shard across `TP·DP`), so you cannot scale throughput by wrapping DP replicas around
a fixed EP deployment the way you can for a dense model — the replica *is* the whole `world`.
So the honest question for an MoE deployment is "given `C` cores, which `(TP, DP, ep_degree)`
partition of `C` serves best," a budget-partition sweep, not an independent-axis grid with a
free DP multiplier. For dense models the two coincide (DP is a free outer loop, §2.1), so the
budget framing is EP's requirement and dense's convenience. **Marked planned; the current
sweep is the independent-axis grid.**

### 4.2 — Selection

Selection is always **gate then rank**: SLA ceilings decide which configs are *valid*, and a
throughput objective *ranks* the valid set. Two SLA ceilings, one per phase, both policy
knobs (not entitlement results):

- **`prefill_itps_floor`** → the prefill **TTFT ceiling**, a constant input-token-throughput
  floor giving a per-bucket budget linear in context: `ttft_ceiling(S) = S / itps_floor`.
- **`decode_itl_ceiling_ms`** → the decode **ITL ceiling**, the max acceptable inter-token
  latency (per-step time). A per-bucket ceiling (may loosen with context, since long-context
  serving tolerates higher ITL).

**Why gate, not rank, on latency.** Both TTFT and ITL are latency SLAs — a config either
meets them or it doesn't; being *faster* than the SLA is not more valuable. What we actually
want to maximize among acceptable configs is **serving efficiency: per-node throughput**
(`otps · cores_per_node / cores`). So latency ceilings gate; throughput ranks. (This is also
why ITL can't be the *ranking* metric: at max-feasible batch decode ITL is ~flat and
HBM-bound, so ranking on it selects on noise — but as a *gate* it correctly excludes configs
whose ITL is unacceptable.)

The two ceilings map to the two phases, so which apply depends on what the config serves:

- **Colocated** — one replica serves **both** phases, so a config is valid only if it meets
  **both** ceilings: `ttft(bs=1) ≤ ttft_ceiling(S)` **and** `decode_itl(max bs) ≤
  itl_ceiling(S)`. Among the valid set, pick **max per-node throughput** (tie → fewer cores).
  Replication allowed (no separate prefix-cache pool to protect).
- **Disaggregated** — two independent pools, each gated only by the ceiling of the phase it
  serves:
  - **decode pool** — serves decode only → gated by the **ITL ceiling** alone (no TTFT). Among
    the ITL-valid set, pick **max per-node throughput**, scaled out via DP.
  - **prefill pool** — serves prefill only → gated by the **TTFT ceiling** alone. Pick the
    smallest *non-replicating* footprint (TP ≤ min KV heads) that meets it, so replication
    doesn't shrink the prefix cache.

**Unreachable ceiling.** If no config satisfies a phase's gate at a bucket, fall back to the
config closest to admissible for that phase (lowest TTFT for prefill, lowest ITL for decode)
and flag `sla_met = False`, rather than returning nothing.

Both modes expose the **full decode landscape** (every fitting config at max batch) so the
gated-and-ranked pick is visible against everything it was chosen from, not hiding the rest.

---

# Implementation

The design above (§1–§4) is realized as a small package where each module is one layer of the
pipeline and the `Segment` is the object that flows through them. The package reuses
`Arch`/`AttnGroup`/`MLAParams`/`arch_from_hf`/`HW` and the topology-aware `comm_total_ms` from
`entitlement_formulas.py` — one parse of the model config, so the aggregation and the oracle
cannot diverge.

```
planner/segments.py   §1 aggregation   Segment; build_weight/kv/flop_segments(arch) -> per-class lists
planner/shard.py      §2 sharding      weight/kv_cache/flop_divisor(cls, cfg, regime) -> divisor per key
planner/cost.py       §3 totals+cost   per_device_totals(segments, cfg, op) -> component triples;
                                        step_time(triples, hw, comm) -> roofline + serialize
planner/plan.py       §4 sweep+select  build_grid, max_feasible_bs, select_buckets; colocated / disaggregated
planner/build.py      wiring           build_bundle(run-spec) -> (Arch, HW, segments, meta, serving); cell(...)
planner/test_planner.py                regression + unit tests
```

## The segment as the spine

One dataclass carries a piece of the model through all three layers. It holds §1 facts only;
§2 attaches a divisor by key; §3 multiplies in the operating point.

```python
@dataclass(frozen=True)
class Segment:
    quantity: str        # "weight" | "kv" | "flop"
    cls: str             # attn_qo | attn_kv | attn_score | dense_mlp | routed_experts | embed
    layer_type: str      # attention-type or ffn-type label (the segment's partition key)
    multiplicity: int    # layer count of that type (spans all its layers)
    unit: float          # per-copy (weight) or per-token (kv/flop) quantity
    cap: int | None = None      # kv_seq_cap; drives eff_ctx (kv + ctx_scaled flop)
    ctx_scaled: bool = False    # flop scores: scales with eff_ctx
    sampled_only: bool = False  # lm_head flop: token count = sampled positions, not n_tok
```

`build_weight_segments` / `build_kv_segments` / `build_flop_segments` emit a `list[Segment]`
per quantity — the §1.1 / §1.2 / §1.3 tables in code. The `(cls, layer_type)` pair is the
**key** §2 shards and §3 groups on; nothing else crosses the boundary. Segments that share a
full key (same class, type, unit) are merged by summing multiplicity, so a uniform-FFN model
collapses its per-layer FFN into one segment.

## Sharding keyed to the segment

`shard.py` is the single owner of all divisor rules, exposed as per-key functions so §3 loops
uniformly:

```python
def weight_divisor(cls, cfg, regime, n_kv=0, is_mla=False) -> int
def kv_cache_divisor(cfg, regime, cap, n_kv, is_mla) -> int
def flop_divisor(cls, cfg, regime, cap=None, n_kv=0, is_mla=False) -> int
```

`ShardConfig = (TP, CP, DP, ep_enabled, ep_degree)` (PP is out of scope, §2). These encode, in
one place: attention `TP` vs prefill-DCP `TP/CP`; the `min(head_tp, n_kv)` KV ceiling; `cp_g`
(capped types → 1); `dense_mlp = TP`; `routed_experts = world`; `embed = TP`. FLOP divisors
reuse the weight divisor per class and multiply in `cp_g` for prefill (§2.3).

## Cost: the §3 pipeline in two functions

```python
def per_device_totals(weight_segs, kv_segs, flop_segs, cfg, op, meta, dtype_bytes)
        -> dict[component -> Triple]:
    # op = OperatingPoint(S, bs, regime); Triple = (weight_bytes, kv_bytes, flops)
    # per segment: mult * unit / divisor(key, cfg, regime), times the eval factors gated by
    # quantity (§3.1 "what scales what"): weights none; kv * eff_ctx(cap, S) * bs;
    # flop * (eff_ctx if ctx_scaled) * n_tok (or sampled tokens if sampled_only).
    # summed into attention / ffn / lm_head components. meta = {attn_type: (n_kv, is_mla)}.

def step_time(triples, hw, comm_ms) -> StepCost:
    # per component: max((weight+kv)/BW, flop/TE)   -- roofline, load = weights+KV summed
    # step = sum(component times) + comm_ms          -- serialized, not pipelined (§3.2)
    # StepCost carries per-component load/flop times + binding attribution.
```

`eff_ctx = min(S, cap)` is the single canonical helper (§3 preamble). `comm_ms` comes from the
topology-aware collective model (TP all-reduce / CP ring / EP all-to-all), added, not `max`-ed.

## Sweep + selection (`plan.py`)

- **Sweep** — enumerates the `(TP, CP, DP[, ep_degree])` grid and picks batch;
  `max_feasible_bs` reads the weight+KV triple to solve the HBM ceiling. No cost logic lives
  here — it calls `cell` / `step_time`.
- **Selection** — `plan(bundle, serving, prefill_itps_floor)` produces the §4 targets:
  colocated emits one TTFT-selected config per bucket (with its decode row), disaggregated
  emits separate decode-capacity + prefill targets. Both expose the full decode landscape.

## How the orthogonal concerns plug in

- **Oracle validation** — because §1 is pure per-model aggregation, `build_*_segments(arch)`
  summed to totals is exactly what the meta-device oracle ground-truths: `sum(segments) ==
  oracle` (§1.3, lm_head included). The single-source-of-truth `Arch` is shared, so a config
  fact cannot drift between the two sides.
- **Speculative decoding** — orthogonal: amortizes a `StepCost` over accepted tokens; consumes
  the `Triple`/`StepCost` without touching §1/§2/§3.
- **DP / PP** — DP is the free outer multiplier on `step_time` (per-node throughput) in the
  non-EP case, and folds into `world` at the divisor layer in the EP case. PP is out of scope;
  its nesting is case-dependent — non-EP inner to DP, outer to TP/CP (`DP > PP > TP/CP`); EP
  outer to the `TP·DP` expert grid (a layer's experts cannot span stages).

## Open items

- **SWA-under-CP** is a convention (capped-cache type → `cp_g = 1`); no supported model
  currently combines a sliding-window cache with CP, so it is unverified against vllm-neuron.
- **dense-MLP-under-EP = full TP** is the framework prescription; a model that co-locates its
  dense/shared path on `ep_tp` instead would need a per-model flag.
- **PP** — validate the case-dependent nesting above when PP is supported.
- **comm model** is a coarse stand-in (collective algorithm / bandwidth tiers / per-collective
  latency floor unverified); it feeds `step_time` as an additive term and is the subject of a
  separate comms audit.
