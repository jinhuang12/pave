# Granite-4.1-8B — Parallelism Planner Walkthrough (trn2)

Planning a serving config for Granite-4.1-8B on trn2 with the
`experimental-neuron-parallelism-planner` skill: derive the model architecture, validate the
analytic engine against the meta-device oracle, then sweep configs to pick an operating point.
On-hardware benchmarking/profiling of the chosen config follows in
`GRANITE_BENCHMARK_WALKTHROUGH.md`.

Run-spec: `scripts/configs/granite4-8b-run.json` (points at the pristine HF `config.json` +
trn2 constants). Interpreter: `python3` with `torch` (meta device, CPU) for the oracle.

---

## Step 1 — Architecture: verify supported, enable if not

Following the skill's "build an oracle for a new model" procedure, four places can need
adjustment: (1) `arch_from_hf` parse, (2) `entitlement_formulas.py` weight/FLOP terms,
(3) `oracle_model.py` structure, (4) `validate_oracle.py` cross-check. Granite needed a change
in only (1); (2)-(3) were already covered, and (4) came out clean.

### 1a. Parse the architecture (`arch_from_hf`)

The run-spec points at the pristine HF config and `arch_from_hf` parses it. Granite's
config (`.artifacts/models/granite-4.1-8b/config.json`):

| HF field | value |
|---|---|
| `architectures` | `GraniteForCausalLM` |
| `hidden_size` | 4096 |
| `intermediate_size` | 12800 |
| `num_hidden_layers` | 40 |
| `num_attention_heads` | 32 |
| `num_key_value_heads` | 8 |
| `head_dim` | **absent** |
| `vocab_size` | 100352 |
| `max_position_embeddings` | 131072 |
| `tie_word_embeddings` | true |
| `torch_dtype` | bfloat16 |
| `layer_types` / `num_experts` | absent |

Granite omits `head_dim`, so `arch_from_hf` needed one addition: derive it when absent
(`hidden_size // num_attention_heads` = 128) — the only enablement change required.

With no `layer_types`, routed experts, or `kv_lora_rank`, Granite parses as one uniform
full-attention group with a dense FFN (GQA). Derived `Arch`:

| property | value |
|---|---|
| layers | 40 (one full-attention group) |
| hidden size | 4096 |
| query / KV heads | 32 / 8 (head_dim 128) |
| attention | full context (no sliding window), no MLA |
| FFN | dense, intermediate 12800 (no routed experts) |
| vocab | 100352, tied embeddings |
| dtype | bf16 |
| **total weights** | **16.76 GB** (7.97 B block params × 2 B + 0.82 GB tied embed/lm_head) |

### 1b. Confirm the oracle implements this arch

`OracleModel` builds generically from `Arch.attn_groups` + the dense/MoE switch — a uniform
dense-GQA arch is already covered, so no oracle module change was needed for Granite.

### 1c. Verify engine correctness against the oracle

To confirm the engine's counts before trusting a sweep, `validate_oracle.py` diffs the
analytic engine against the **meta-device oracle** (a shape-exact `torch` module built from the
same `Arch`, run under a per-aten FLOP counter) per segment.

```bash
cd <parallelism-planner>/scripts
PARALLELISM_PLANNER_RUNSPEC=configs/granite4-8b-run.json python3 validate_oracle.py
```

Output:

```
model: granite-4.1-8B (dense)  layers=40  dtype_bytes=2  k_eq_v=False  by=type

=== decode S4096 bs8 ===
segment                     oracle FLOP    analytic FLOP      d%    oracle wt(MB)  analytic wt(MB)      d%
attention/global         48,318,382,080   48,318,382,080   +0.0%           3355.5           3355.4   +0.0%
ffn/dense               100,663,296,000  100,663,296,000   +0.0%          12582.9          12582.9   +0.0%
lm_head                   6,576,668,672    6,576,668,672   +0.0%            822.1            822.1   +0.0%
TOTAL                   155,558,346,752  155,558,346,752   +0.0%          16760.5          16760.4   +0.0%

=== prefill S2048 bs1 ===
segment                     oracle FLOP    analytic FLOP      d%    oracle wt(MB)  analytic wt(MB)      d%
attention/global       9,620,726,743,040 9,620,726,743,040   +0.0%           3355.5           3355.4   +0.0%
ffn/dense              25,769,803,776,000 25,769,803,776,000   +0.0%          12582.9          12582.9   +0.0%
lm_head                     822,083,584      822,083,584   +0.0%            822.1            822.1   +0.0%
TOTAL                  35,391,352,602,624 35,391,352,602,624   +0.0%          16760.5          16760.4   +0.0%
```

### Reading the result

- **Weights: exact (+0.0%)** on every segment. The analytic `corrected_weight_bytes` matches
  the oracle (residual is RMSNorm params the engine omits — negligible at this hidden size).
- **FLOPs: exact (+0.0%)** on every segment, decode and prefill.
- **KV bytes:** the oracle reports 0 (meta-device SDPA decomposes into bmm+softmax, so the
  counter's fused-attention hook never fires); the analytic `kv_bytes_total` is the authority
  for KV. The oracle ground-truths FLOPs and weight bytes.

**Verdict: clean.** Weights and FLOPs match the oracle to +0.0% across attention / ffn / lm_head,
decode and prefill. The engine looks trustworthy to sweep for Granite.

> The oracle validates **engine ⇄ oracle** consistency (both built from the same `Arch`), not
> **arch ⇄ reality**. Because they share one parse, a mis-parsed arch would read +0.0% while
> still being wrong. Guard for that separately: the corrected weight total (16.76 GB, ~8.4 B
> params incl. tied embeddings) is consistent with the model card's "8B", so the parse is sound.

---

## Step 2 — Sweep and select a config

### 2.1 — Decisions

Choices this plan commits to (the rest is engine-derived):

- **Serving mode:** disaggregated inference (DI) — separate decode and prefill pools.
- **SLAs:** operator policy knobs (not arch-derived), left at defaults — prefill TTFT floor
  30000 ITPS, decode ITL ceiling 50 ms. Under DI the two are applied to *separate* pools: the
  decode pool gates on ITL only, the prefill pool on TTFT only (colocated would require both at
  once on one config).
- **Parallelism axes** (dense model):
  - **DP = 1** in the grid — with no MoE, DP replicas are independent, so DP is a free
    multiplier used to fill the node (folded into the per-node throughput metric), not swept.
  - **PP = 1** — not implemented by vllm-neuron.
  - **CP** — swept for completeness (the engine costs it correctly), **but not selectable
    here**: vllm-neuron supports DCP, yet our Granite model.py path is TP+SP only, so a CP>1
    pick can't actually be run. Kept in the landscape, excluded from the final choice.
  - **EP** — N/A (no routed experts).
  - Leaves **TP** (and CP, for completeness) as the swept axes.
- **Objective:** not yet defined — so rather than commit to a single pick, look at the
  **ITL / per-node-throughput Pareto frontier** and choose from it once an objective is set.
- **Batch / memory:** the same curve lets us vary batch size along the frontier, trading
  throughput for latency and potentially relieving memory pressure (smaller batch → less KV).

### 2.2 — Sweep the 8k landscape (ITL vs throughput vs batch)

Full grid at 8k, each config at its max-feasible batch (per-node throughput = otps ×
cores_per_node / cores; ITL = decode step time). CP configs shown but **not selectable**
(see 2.1); batch-size evolution is left to the visualization.

| config | cores | bs (max) | ITL ms | tput/node | binding |
|---|--:|--:|--:|--:|---|
| TP4 | 4 | 56 | 33.4 | 26,853 | kv_load |
| TP4 CP2 / CP4 | 4 | 56 | 33.4 | ~26,850 | kv_load |
| TP8 | 8 | 124 | 35.4 | **28,044** | kv_load |
| TP8 CP2 / CP4 / CP8 | 8 | 124 | 35.4 | ~28,035 | kv_load |
| TP16 | 16 | 130 | 37.4 | 13,908 | kv_load |
| TP16 CP2 / CP4 / CP8 | 16 | 262 | 40.7 | ~25,770 | kv_load |
| TP32 | 32 | 134 | 41.5 | 6,460 | comm |
| TP32 CP4 / CP8 | 32 | 538 | 48.9 | ~21,990 | kv_load |

Reading the max-batch table:
- **Per-node throughput peaks at TP8 (28,044), then falls with more cores** — TP4 close behind
  (26,853), TP16 and TP32 much lower (13,908 / 6,460). Past TP8 = `n_kv` (8), the KV cache
  **replicates** (TP16 → 2×, TP32 → 4×), so extra cores hold redundant KV instead of buying
  throughput — the runnable picks favor the small replica.
- **ITL grows with TP** at max batch (33.4 → 35.4 → 37.4 → 41.5 ms across TP4→32), because
  max-feasible batch grows with cores.
- **CP rows match their TP row** (TP8 CP2/4/8 ≈ 28,035 ≈ TP8's 28,044) — CP appears to buy no
  throughput at 8k. This is counter-intuitive and unexpected; **WIP — needs a closer look**
  (also not runnable regardless).
- **Binding:** all cells `kv_load` except TP32 (`comm`).

All cells clear the 50 ms ITL ceiling, so under DI the decode pool's max-per-node-throughput
pick is **TP8** — the point the benchmark step probes on hardware.

### 2.3 — Visualization: the interactivity Pareto frontier

`python3 -m planner configs/granite4-8b-run.json --visualize` writes the per-node interactivity
Pareto (`pareto_per_node_<model>.png`) — ITL vs per-node throughput, one facet per bucket, each
config swept over batch size below its max:

![per-node interactivity Pareto](granite_runs/planner_viz/pareto_per_node_granite_4_1_8b_dense.png)

Reading the 8k facet (top-left): the frontier (colored, bs-labeled) is **all TP8** — it
dominates TP4 (red, only touches the frontier at its max-bs corner) and the higher-TP configs
(grey, well inside). Two things the walkthrough relies on are visible directly:
- **The frontier is the ITL ↔ per-node-throughput trade** — no single point is "best" until an
  objective is set; you pick where on the curve to sit.
- **Moving along the frontier = varying batch** (bs1 top-left → bs124 bottom-right on the TP8
  curve): lower batch → lower ITL and less KV (memory relief), higher batch → more throughput.
  This is the same curve referenced in 2.1.

(Grey curves are the other configs' batch sweeps; TP16/TP32 sit inside the TP8 frontier —
consistent with the KV-replication falloff past TP8 noted above.)

## Step 3 — Cost-model breakdown for a fixed config

### 3.1 — Decisions

- **Config:** pick a Pareto-optimal point from the 2.3 frontier — **TP8 × bs32**. It sits on
  the TP8 frontier but below the max-batch corner (max-feasible bs at TP8/8k is 124), so it
  leaves real memory headroom: peak HBM ≈ **7.5 GB/core of 23.2 usable (32%)**, vs 99% at
  bs124. Reasonable throughput without pinning the KV budget. (bs above 16 did not immediately work
  for profiling, so the following sections use bs16 — though we would typically pick a higher
  value along the Pareto frontier.)
- **Regime:** decode (the bound phase).
- **Granularity:** per-(component, layer-type), from the planner's `--cell` `by_type` output.

### 3.2 — Breakdown

```bash
python3 -m planner configs/granite4-8b-run.json --cell --tp 8 --cp 1 --bs 16 --ctx 8192
```

`cores=8  step_ms=8.54  comm_ms=1.86  binding=attention:kv_load` → decode **1,874 otps/replica**,
per-node (×8 DP) **14,991**.

Granite has just three keys: uniform full-attention + a uniform dense FFN + the lm_head
singleton (no sliding/global split, no MoE, no MLA — so no extra attention- or ffn-types).
Weight and KV columns show bytes with their HBM-load time; `time` = max(load, flop); `binds`
names the load terms (weight_load + kv_load), **bold** = the larger.

**Per segment type** (one segment of each type):

| component / type | weight (load) | KV (load) | FLOP (compute) | time ms | binds |
|---|--:|--:|--:|--:|---|
| attention / global | 10.5 MB (0.015 ms) | 67.1 MB (0.094 ms) | 0.436 G (0.006 ms) | 0.109 | weight_load + **kv_load** |
| ffn / dense | 39.3 MB (0.055 ms) | 0 | 0.629 G (0.008 ms) | 0.055 | **weight_load** |
| lm_head (×1) | 102.8 MB (0.144 ms) | 0 | 1.644 G (0.021 ms) | 0.144 | **weight_load** |
| all_reduce | | | | 0.023 | — |

**Full model** (all layers of each type summed):

| component / type | layers | weight (load) | KV (load) | FLOP (compute) | time ms | binds |
|---|--:|--:|--:|--:|--:|---|
| attention / global | 40 | 419.4 MB (0.59 ms) | 2,684.4 MB (3.75 ms) | 17.45 G (0.22 ms) | 4.34 | weight_load + **kv_load** |
| ffn / dense | 40 | 1,572.9 MB (2.20 ms) | 0 | 25.17 G (0.32 ms) | 2.20 | **weight_load** |
| lm_head / – | 1 | 102.8 MB (0.14 ms) | 0 | 1.64 G (0.02 ms) | 0.14 | **weight_load** |
| comm | | | | | 1.86 | — |
| **step** | | 2,095.0 MB | 2,684.4 MB | 44.26 G | **8.54** | attention:kv_load |

Reading it:
- **Attention KV-read dominates** — its 3.75 ms KV load is the bulk of the 4.34 ms attention
  time and of the 8.54 ms step. Per-device KV is 2.68 GB,
  streamed every decode step; the 0.42 GB of attention weights (0.59 ms load) are minor beside
  it. This is the KV-bound regime the frontier lives in.
- **FFN** stays at 2.20 ms (weight-load bound, weights fixed with batch); **lm_head** negligible.
- **comm** 1.86 ms (~22% of the step).
- vs bs16 (step 8.54 ms, 1,874 otps): bs32 ~1.4× the throughput (2,566 otps) at ~1.5× the ITL —
  diminishing return, since decode is already KV-bound past bs16.

## Step 4 — Compare against a real run

The entitlement above is a lower bound; this step checks it against hardware.

> Config sidenote: §3 selected **TP8 × bs64** from the frontier, but a >bs16 run wasn't
> immediately workable on hardware (bs64 exceeds the KV pool at 8k, and forcing it larger hit a
> compile wall), so this comparison **starts at bs16** — the point that runs and profiles
> cleanly. Higher-batch points to follow.

### 4.1 — Benchmark + DP node scaling

TP8/bs16/8k, `vllm bench serve` against a live `vllm serve` instance. Decode-only via TPOT
(= request latency − TTFT). Single replica, then scaled across the node via independent DP
replicas (dense model → no cross-replica collectives).

Common env (default 10s RPC timeout is too short for the uncached 40-layer compile):

```bash
export VLLM_RPC_TIMEOUT=1800000 NEURON_SKIP_EFA_AFFINITY=1
CKPT=~/GemmaTune/.artifacts/models/granite-4.1-8b
```

Serve (DP1 shown; DP3/DP8 add `--data-parallel-size 3` / `8` and widen NEURON_VISIBLE_DEVICES to
24 / 64 logical cores):

```bash
NEURON_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
vllm serve $CKPT --max-model-len 8192 --max-num-batched-tokens 8192 --max-num-seqs 16 \
  --tensor-parallel-size 8 --port 8000 \
  --additional-config '{"neuron_config": {"on_device_sampling_config": {"all_greedy": "true"}, "num_batched_tokens_buckets": [8192], "num_seqs_buckets": [16]}}'
```

Benchmark (concurrency = 16 × replicas: 16 / 48 / 128; prompts = 8× concurrency to stay
saturated):

```bash
vllm bench serve --backend vllm --host localhost --port 8000 --model $CKPT \
  --dataset-name random --random-input-len 7680 --random-output-len 512 \
  --max-concurrency 16 --num-prompts 128 \
  --save-result --result-dir ~/GemmaTune/granite_runs/benchserve --result-filename dp1.json
```

| deployment | cores | measured tok/s | planner entitlement | measured / planner | median TPOT (meas / planner step) |
|---|--:|--:|--:|--:|--:|
| 1 replica (DP1) | 8 | 618 | 1,874 otps | 0.33× | 20.25 ms / 8.54 ms |
| DP3 | 24 | 1,847 | 5,622 (3×) | 0.33× | 20.29 ms |
| DP8 (full node) | 64 | 4,489 | 14,991 (8×) | 0.30× | 20.81 ms |

- **Throughput:** measured ~0.30–0.33× the planner entitlement at every DP degree — the same
  ratio across the node, so DP scaling itself is faithful (DP8 = 7.26× the single replica, ~9%
  under ideal 8×); the offset is per-replica.
- **TPOT** stays flat as replicas are added (per-replica decode unchanged) at ~20 ms, vs the
  planner's 8.54 ms step.

> Method note: the live-server `/start_profile` + `vllm bench serve` path used here is newer than
> the `experimental-neuron-framework-profiling-vllm-neuron` skill's current NEFF-capture
> walkthrough — **WIP to update the skill with this method**.

### 4.2 — Planner at reduced layers (to match the profiled proxy)

The full 40-layer model + profiler buffers exceed HBM, so the device profile is captured on a
**reduced-layer proxy**. The planner has no layer-count override — a reduced-layer model is a
different model, so it needs its **own run-spec** (`granite4-8b-4L-run.json`, an HF config with a
reduced `num_hidden_layers`):

```bash
python3 -m planner configs/granite4-8b-4L-run.json --cell --tp 8 --cp 1 --bs 16 --ctx 8192
```

Expected per-device workload (TP8/bs16/8k, 4-layer); per-segment-type expectations in §3.2's
"Per segment type" table:

| component / type | layers | weight (load) | KV (load) | FLOP (compute) | time ms | binds |
|---|--:|--:|--:|--:|--:|---|
| attention / global | 4 | 41.9 MB (0.06 ms) | 268.4 MB (0.37 ms) | 1.745 G (0.02 ms) | 0.433 | weight_load + **kv_load** |
| ffn / dense | 4 | 157.3 MB (0.22 ms) | 0 | 2.517 G (0.03 ms) | 0.220 | **weight_load** |
| lm_head / – | 1 | 102.8 MB (0.14 ms) | 0 | 1.644 G (0.02 ms) | 0.144 | **weight_load** |
| all_reduce | | | | | 0.023 | — |
| comm | | | | | 0.186 | — |
| **step** | | 302.0 MB | 268.4 MB | 5.906 G | **0.983** | attention:kv_load |

### 4.3 — Profile vs expectation (per-section)

Device profile on the reduced-layer proxy (TP8, bs16, 8k, steady-state decode), sectioned by
collective boundaries, per-segment average vs the per-layer entitlement.

Serve with the profiler enabled (`delay_iterations` skips warmup+prefill; `max_iterations` bounds
the capture to a few steady-state decode steps so the NTFF stays small):

```bash
NEURON_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
vllm serve $CKPT --max-model-len 8192 --max-num-batched-tokens 8192 --max-num-seqs 16 \
  --tensor-parallel-size 8 --port 8000 \
  --hf-overrides '{"num_hidden_layers": 6}' \
  --profiler-config '{"profiler": "cuda", "delay_iterations": 40, "max_iterations": 5}' \
  --additional-config '{"neuron_config": {"on_device_sampling_config": {"all_greedy": "true"}, "num_batched_tokens_buckets": [8192], "num_seqs_buckets": [16]}, "neuron_profiler": {"activities": ["device_profile","system_profile"], "output_dir": ".../nrt_profile"}}'
```

Profile a decode-saturated window, then stop:

```bash
curl -X POST http://localhost:8000/start_profile
vllm bench serve --backend vllm --host localhost --port 8000 --model $CKPT \
  --dataset-name random --random-input-len 7680 --random-output-len 512 \
  --max-concurrency 16 --num-prompts 16
curl -X POST http://localhost:8000/stop_profile
```

#### 4.3a — Sequential segment-by-segment breakdown

The decode NEFF in execution order: each compute segment followed by the collective that ends it
(embedding AR, then per layer the attention/global AR + ffn/dense AR, then the lm_head
AllGathers). Segments are labeled by **component × layer_type** — here `attention/global`,
`ffn/dense`, `lm_head/-` (Granite has one attention-type and one ffn-type). Same shape as the
§3.2 planner breakdown — DMA bytes (with load time), matmul FLOP (with compute time):

| # | segment (component/type) | DMA (load) | FLOP (compute) | dur ms | exp dur ms |
|--:|---|--:|--:|--:|--:|
| 0 | embed | 8.76 MB (0.012 ms) | 0.02 G (0.000 ms) | 0.296 | — |
| | AllReduce[0] 131072B | | | 0.032 | 0.023 |
| 1 | L0 attention/global | 72.62 MB (0.101 ms) | 0.49 G (0.006 ms) | 0.215 | 0.109 |
| | AllReduce[1] 131072B | | | 0.023 | 0.023 |
| 2 | L0 ffn/dense | 47.21 MB (0.066 ms) | 0.66 G (0.008 ms) | 0.131 | 0.055 |
| | AllReduce[2] 131072B | | | 0.027 | 0.023 |
| 3 | L1 attention/global | 74.73 MB (0.104 ms) | 0.49 G (0.006 ms) | 0.202 | 0.109 |
| | AllReduce[3] 131072B | | | 0.024 | 0.023 |
| 4 | L1 ffn/dense | 47.26 MB (0.066 ms) | 0.66 G (0.008 ms) | 0.130 | 0.055 |
| | AllReduce[4] 131072B | | | 0.028 | 0.023 |
| 5 | L2 attention/global | 74.73 MB (0.104 ms) | 0.49 G (0.006 ms) | 0.202 | 0.109 |
| | AllReduce[5] 131072B | | | 0.023 | 0.023 |
| 6 | L2 ffn/dense | 47.26 MB (0.066 ms) | 0.66 G (0.008 ms) | 0.132 | 0.055 |
| | AllReduce[6] 131072B | | | 0.026 | 0.023 |
| 7 | L3 attention/global | 74.73 MB (0.104 ms) | 0.49 G (0.006 ms) | 0.202 | 0.109 |
| | AllReduce[7] 131072B | | | 0.024 | 0.023 |
| 8 | L3 ffn/dense | 65.11 MB (0.091 ms) | 0.65 G (0.008 ms) | 0.146 | 0.055 |
| | AllReduce[8] 131072B | | | 0.035 | 0.023 |
| 9 | lm_head/- | 66.19 MB (0.092 ms) | 14.89 G (0.188 ms) | 0.234 | 0.144 |
| | AllGather[9] 401408→3211264B | | | 0.037 | — |
| 10-12 | sampling tail | 8.03 MB (0.011 ms) | 0.00 G (0.000 ms) | 0.053 | — |
| | **decode step total** | **586.6 MB** | **19.47 G** | **2.239** | **0.983** |

#### 4.3b — Per-segment-type mean vs expected

Average the like segments (all `attention/global`, all `ffn/dense`) into a per-layer mean and lay
it beside the per-layer entitlement (§3.2), in the same column shape as the §3.2 tables —
weight/KV/FLOP with their load/compute times. `exp` = planner expected, `meas` = profiled mean.

> TODO (formatting): fold the two tables below into one — expected and measured side by side per
> column (weight/KV/FLOP/time) with the ratio inline — rather than two separate tables. Cleaner to
> read the divergence per cell.

**Expected** (per segment type, from §4.2's reduced-layer `--cell`):

| component / type | weight (load) | KV (load) | FLOP (compute) | time ms | binds |
|---|--:|--:|--:|--:|---|
| attention / global | 10.5 MB (0.015 ms) | 67.1 MB (0.094 ms) | 0.436 G (0.006 ms) | 0.109 | weight_load + **kv_load** |
| ffn / dense | 39.3 MB (0.055 ms) | 0 | 0.629 G (0.008 ms) | 0.055 | **weight_load** |
| lm_head / – | 102.8 MB (0.144 ms) | 0 | 1.644 G (0.021 ms) | 0.144 | **weight_load** |

**Measured** (per-segment-type mean):

| component / type | weight+KV (load) | FLOP (compute) | DMA active ms | TE active ms | time ms | meas/exp (DMA) | meas/exp (FLOP) | meas/exp (time) |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| attention / global | 74.2 MB (0.104 ms) | 0.49 G (0.006 ms) | 0.160 | 0.072 | 0.207 | 0.96× | 1.12× | 1.90× |
| ffn / dense | 51.7 MB (0.072 ms) | 0.66 G (0.008 ms) | 0.110 | 0.047 | 0.135 | 1.32× | 1.05× | 2.45× |
| lm_head / – | 66.2 MB (0.092 ms) | 14.89 G (0.188 ms) | 0.145 | 0.203 | 0.234 | 0.64× | 9.1× | 1.63× |

> Note: the planner assumes a full-context KV read (eff_ctx = the full bucket S) for every
> decode token. With causal masking a token at an earlier position attends only to the tokens
> before it, so it loads a bit less KV than the full bucket — the planner does not discount this,
> so its attention load is an upper bound for any token before the last position.

> Note: at bs16 attention and ffn are memory-bound (DMA active > TE active: attention
> 0.160 > 0.072, ffn 0.110 > 0.047 ms); lm_head is compute-bound (TE 0.203 > DMA 0.145). A
> memory-bound segment is limited by achieved memory bandwidth (MBU), which is harder to drive
> to peak than tensor-engine utilization — so these segments are the less-efficient ones.

> Method note: per-section attribution follows the
> `experimental-neuron-framework-profile-analysis-vllm-neuron` skill — **WIP to get the
> collective-segmented (attention / MLP / lm_head) breakdown wired into the skill**.

The values above are bs16. **Higher batch sizes are still blocked**: the serve-time profiler
(`vllm serve --profiler-config`) fails to compile the reduced-layer proxy at bs>16 with
`[NCC_IGCA037] ... too big for SB, requires 393216 bytes` — an SBUF tile overflow in
`norm_tkg_utils.py:397` via `moe_block_tkg_wrapper`. So bs32/bs64 profiles (the higher points on
the frontier §3.1 would normally pick) are not yet captured.

To avoid that bug, the bs16 profile above used a **different profiling flow** — NEFF-capture
(`experimental-neuron-framework-profiling-vllm-neuron`, Mode A) instead of the serve-time
profiler: emit the NEFFs with `vllm bench throughput`, then replay+capture each NEFF with
`neuron-explorer capture` (instruments the already-compiled NEFF, so it does not hit the
compile-time SBUF overflow). Exact commands:

```bash
# 1. emit prefill+decode NEFFs (bf16, greedy; no fp8/MTP)
NEURON_RT_ENABLE_DGE_NOTIFICATIONS=0 NEURON_VISIBLE_DEVICES=<8 LNCs> \
  ./references/run_profile.sh granite_bf16_bs16_8k_4layer_tp8 \
    --model $CKPT --max-model-len 8192 --max-num-batched-tokens 8192 --max-num-seqs 16 \
    --tensor-parallel-size 8 --hf-overrides '{"num_hidden_layers": 4}' \
    --additional-config '{"neuron_config": {"on_device_sampling_config": {"all_greedy": "true"}, "num_batched_tokens_buckets": [8192], "num_seqs_buckets": [16]}}' \
    --dataset-name random --random-input-len 7680 --random-output-len 512 --num-prompts 16

# 2. capture the device trace for each NEFF (TP=8 collectives)
for neff in $PROFILE_DIR/capture/*.neff; do
  ID=$(basename "$neff" | sed 's/neff_\(.*\)_vnc_0\.neff/\1/'); mkdir -p $PROFILE_DIR/capture/$ID
  NEURON_RT_ENABLE_DGE_NOTIFICATIONS=1 neuron-explorer capture -n "$neff" \
    -s "$PROFILE_DIR/capture/$ID/profile.ntff" --profile-nth-exec=2 --enable-dge-notifs \
    --collectives-workers-per-node=8 --collectives-worker-count=8
done

# 3. ingest, then segment by collective boundaries (per experimental-neuron-framework-profile-analysis-vllm-neuron)
neuron-explorer view -d $PROFILE_DIR/capture_minimal --data-path ~/.local/share/neuron-profile \
  --display-name granite_bf16_bs16_8k_4layer_tp8 --ingest-only
```

#### 4.3c — Planner per-segment type at bs64 (is ffn more compute-bound?)

Higher batch was not profilable (see above), but the planner shows how the load/compute balance
shifts. Per segment type at TP8/bs64/8k decode (`--cell --bs 64`); `load ms` = (weight+KV)/BW,
`flop ms` = FLOP/TE, `time` = max:

| component / type | weight (load) | KV (load) | FLOP (compute) | load ms | flop ms | time ms | AI (FLOP/byte) | binds |
|---|--:|--:|--:|--:|--:|--:|--:|---|
| attention / global | 10.5 MB (0.015 ms) | 268.4 MB (0.375 ms) | 1.745 G (0.022 ms) | 0.390 | 0.022 | 0.390 | 6.26 | **kv_load** |
| ffn / dense | 39.3 MB (0.055 ms) | 0 | 2.517 G (0.032 ms) | 0.055 | 0.032 | 0.055 | 64.05 | **weight_load** |
| lm_head / – | 102.8 MB (0.144 ms) | 0 | 6.577 G (0.083 ms) | 0.144 | 0.083 | 0.144 | 63.98 | **weight_load** |

`step_ms=20.34  comm_ms=2.41  binding=attention:kv_load` → 3,147 otps/replica, per-node 25,176.

Arithmetic intensity (FLOP / loaded byte) per segment, bs16 vs bs64:

| component / type | AI bs16 | AI bs64 |
|---|--:|--:|
| attention / global | 5.62 | 6.26 |
| ffn / dense | 16.01 | 64.05 |
| lm_head / – | 15.99 | 63.98 |

ffn and lm_head AI scale ~4× with the 4× batch (FLOP grows with batch, loaded weights fixed);
attention AI is ~flat (its KV load grows with batch alongside its FLOP). ffn remains
weight-load-bound at bs64 (load 0.055 ms > flop 0.032 ms).
