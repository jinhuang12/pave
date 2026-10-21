---
name: experimental-neuron-framework-benchmark-vllm
description: Benchmark LLM serving throughput/latency with `vllm bench serve` on Trainium (vLLM-Neuron via Kaizen) and on NVIDIA GPU (upstream vLLM), using one identical workload so the two are directly comparable. Use when measuring or comparing tok/s, TTFT, TPOT, or ITL for a model.
allowed-tools: Read Write Edit Bash Grep Glob
metadata:
  version: 1.0.0
  tags: [benchmark, vllm, vllm-bench, trainium, trn2, gpu, throughput, latency, ttft, tpot, kaizen]
---

# vLLM Benchmarking (Neuron + GPU)

Measure serving performance of a model with the **upstream `vllm bench serve`** harness, on Trainium (vLLM-Neuron) and/or NVIDIA GPU (stock upstream vLLM), with a single identical workload so the numbers compare apples-to-apples.

## What this measures

`vllm bench serve` drives a running OpenAI-compatible vLLM server and reports:
- **output_throughput** (tok/s) and **total_token_throughput** (tok/s) — aggregate, per server
- **request_throughput** (req/s)
- **TTFT** — time to first token (mean / median / p99 ms)
- **TPOT** — time per output token (mean / p99 ms)
- **ITL** — inter-token latency (mean / p99 ms)
- **completed** / **failed** — requests that succeeded / dropped (a cell with a high failed fraction is a saturated point, not a fast one)

The same `vllm bench serve` client runs on both platforms; only the *server backend* differs (Neuron kernels vs CUDA kernels). That is what makes Neuron-vs-GPU comparisons valid.

## Sweep, don't pin: the operating point is not a single number

A single concurrency/sharding is one dot, not a curve — it can't find the
saturation knee or the throughput-optimal sharding. The GPU side therefore
**sweeps two axes** and reports the Pareto frontier:

- **concurrency** — centered on the real per-replica slot counts (production is
  16/24/32, coffee ~64 — see `references/profiling-targets.md`), overshooting the
  busiest by a doubling so the knee is *bracketed*. Profile AT the configured slot
  count, not idle. Cells past real capacity trip the failure guard and SKIP (cheap);
  undershooting silently truncates the headline peak (costly).
- **sharding (TP×DP)** — every `{tp, dp}` with `tp*dp == gpu_count`, so each run
  fills the whole box. This is the DP axis: `{tp:4, dp:2}` = two TP4 replicas
  behind one endpoint. TP-only (leaving GPUs idle) under-reports box throughput
  and can't answer "best way to shard on N GPUs" — so we always fill the box and
  report **per-instance** numbers. **But prune shardings the model can't fit —
  see "Sizing for large / MoE models".**

### Sizing for large / MoE models (do this BEFORE you provision)

The default 4-way sharding sweep (`tp8, tp4_dp2, tp2_dp4, tp1_dp8`) assumes the
model fits at every TP. For a large model it won't, and **a non-fitting sharding is
NOT free**: the server OOMs at load, the cell is skipped (not a hard error — the
sweep continues), but you've already provisioned the box and each doomed cell still
*attempts* a server start. On Kaizen that can burn a whole workload timeout hitting
OOM. So do the fit math up front and only list shardings that fit:

- **Per-GPU weight bytes ≈ (params × bytes/param) ÷ TP** — divide by **TP only, NOT
  tp×dp**. DP *replicates* the whole model in each replica, so only tensor-parallelism
  reduces per-GPU weight memory; a `tp4_dp2` config still holds a full TP4 shard on
  each of its 8 GPUs (¼ of the weights), same as `tp4_dp1`. bf16 = 2 B/param, fp8 =
  1 B/param. Weights must leave headroom under the card's HBM (80 GB on H100/H200) at
  `gpu_memory_utilization` for the KV cache — rule of thumb keep weights ≲ 70% of
  HBM/GPU (≈56 GB on an 80 GB card) so KV has room; a shard near that line fits but
  its KV pool is tight, so it saturates at lower concurrency.
- **Worked example — Qwen3-235B (235B total):** bf16 ≈ 470 GB → per-GPU 470÷TP:
  58.8 GB at tp8 (fits, but ~73% of HBM — KV is tight), 117 GB at tp4 (**OOM**) →
  **bf16 runs tp8 only**. fp8 ≈ 235 GB → 29 GB at tp8, 59 GB at tp4 (fits), 117 GB at
  tp2 (OOM) → **fp8 runs tp8 and tp4_dp2** (both use a TP≥4 shard = ≤59 GB/GPU). So
  list only those.
- **Shardings are keyed per-MODEL, not per-regime.** When the fitting shardings
  differ by precision (as above), express each precision as its **own model entry** —
  same `key`+`hf_id` (or the fp8 checkpoint), restricted with `regimes: [...]` and its
  own `shardings: [...]`. (One `models[]` entry can't carry two different sharding
  sets for two regimes.)
- **MoE models need `moe: true`** on the model entry → adds `--enable-expert-parallel`
  (ep = tp×dp). Required for Mixtral / Qwen3-235B / gpt-oss; without it experts aren't
  sharded and the model may not fit or serves incorrectly. Dense models omit it.
  **Fit math uses TOTAL params, not active** — expert-parallel shards the *full*
  expert set across ranks, so all 235B of Qwen3-235B live in HBM (spread over the
  box), not just the 22B active per token; size against total ÷ TP as above. Also
  confirm the pinned image's vLLM registers the MoE arch (e.g. `Qwen3MoeForCausalLM`)
  before provisioning — an unregistered arch fails at server start.
- **Context window vs. jitter — check your longest shape against the native window.**
  Under `dataset: random`, `random_range_ratio: R` (default **0.8**) jitters input
  length up to **`isl × (1+R)`**. The sweep sizes `max-model-len` from that *jittered*
  max (so a cell won't overflow its own window) — but it can't exceed the model's
  **native context** (`max_position_embeddings`). So verify:
  `max_shape_isl × (1+R) + osl + buffer ≤ native_window`. If it doesn't fit, either
  (a) drop/​shrink that long shape, (b) lower the jitter — globally via
  `workload.random_range_ratio`, or **per-shape** via
  `workload.range_ratio_by_shape` (keyed `"isl,osl"`, e.g. `{"32768,256": 0.0}` to
  make just the 32K shape exact while others keep 0.8), or (c) set `rope_scaling` on
  the model entry (→ `--rope-scaling`) to extend the window (YaRN etc.). Example: a
  32K shape at R=0.8 needs ~59K → exceeds Qwen3's 40960 window → set
  `range_ratio_by_shape: {"32768,256": 0.0}` (→ needs ~33K, fits) or use rope_scaling.
  (buffer is 128/512/1024 by ISL.)
- **FP8 = a checkpoint, not a flag** — set `hf_id_by_regime.<fp8-regime>` to a real
  pre-quantized checkpoint and verify it exists on HF before provisioning (the
  convention is a `-FP8` suffix on the base id — e.g. `Qwen/Qwen3-235B-A22B-FP8`,
  `nvidia/Llama-3.3-70B-Instruct-FP8`; confirm the exact id on huggingface.co rather
  than assuming). vLLM auto-detects the method from the checkpoint — no flag.

This is driven by **InferencePerfAnalyzer** (IPA), whose orchestrator starts/stops
the vLLM container per sharding and runs `vllm bench serve` per concurrency,
caching the server across concurrencies and applying the failure-rate guard. See
§A for how it's installed and run.

### Regimes: precision/quantization × speculative decoding (one engine)

The `regimes` list is a first-class sweep axis alongside sharding and concurrency.
Each regime sets some of: `prec` (grouping label), `kv_cache_dtype`, `quantization`
(online weight quant), `speculative` (vLLM `--speculative-config`, verbatim),
`extra_flags`. Every regime runs on the **same `image`** — so a `bf16` vs `bf16_spec`
(or `bf16` vs `fp8`) comparison is engine-matched, not confounded by a different vLLM
build. Each `(model, regime)` writes `<results>/<key>__<regime>/…` + a
`regime_meta.json` the deliverables read to attribute the regime.

- **Quantized WEIGHTS = swap the checkpoint.** FP8/AWQ/GPTQ is usually just pointing
  at a pre-quantized HF checkpoint — vLLM auto-detects the method from its config, no
  flag. Set `models[].hf_id_by_regime.<regime>` to that checkpoint. Use
  `regime.quantization` only to force *online* quant of a full-precision checkpoint
  or to name a method the checkpoint doesn't self-declare.
- **"FP8" is three independent things:** FP8 *weights* (checkpoint), FP8 *KV cache*
  (`kv_cache_dtype: fp8`), and FP8 *attention* (may or may not engage depending on the
  model's head dims / backend). Set weights and KV explicitly; note which actually
  engaged when reporting (a head-dim that forces the Triton attention backend means
  the attention isn't FP8 even when weights+KV are).
- **Spec-decode caveat is now structural, not manual:** because all regimes share the
  image, the old "spec ran on a different vLLM build" pitfall is gone — but still
  report acceptance rate / acceptance length (the deliverables surface these
  automatically when the fields appear) so a spec speedup is attributable.
- **Spec-decode `num_speculative_tokens` (block size / k / γ) is a per-model recommendation, not a guess.** Before setting it, **find the paper's or the drafter maintainer's *deployment-recommended* value for the *target model + drafter algorithm* pair**, in that order:
  1. **The drafter's paper** (usually linked from the HF card's model repo) — search for a "deployment" / "serving" / "recipe" section, not just the training block size. Papers routinely train with one γ (e.g. `_block7` in checkpoint names) but recommend a lower γ for real deployment. Concrete case: the DSpark paper (arXiv 2607.05147) trains with γ=7 and its HF card copies that verbatim, but the paper's own DeepSeek-V4-Flash deployment explicitly says *"We configure the maximum block size to γ=5"* — γ=5 is what reproduces the paper's 60-85% per-user speedup claim on V4-Flash. Copying the HF card's k=7 without checking gave us ~40% of the paper's headline speedup because k=4..7 was drafting past the acceptance decay knee.
  2. **The vLLM serving recipe** at `recipes.vllm.ai/<hf-id>` if one exists — it's the fastest paper-tuned config to copy.
  3. **The drafter's HF model card** — usually shows the *training-time* block size, which is often too aggressive for deployment. Use only as a fallback and label the sweep as "training-block-size baseline, not deployment-tuned."
  4. **Sweep k itself** — if none of the above give a clear answer, treat k as another axis: run the smoke gate at 2-3 candidate values and pick where acceptance length × verify cost peaks. Expensive; do it only when the drafter is new.
  The general theory: per-position acceptance decays with draft depth (position 5+ is usually noise on any drafter). Above the "acceptance knee" you pay verify cost for tokens that get rejected. Deployment recommendations exist precisely because the knee has been measured; don't rediscover it accidentally.

### Config philosophy: native-production, not matched-config

Following the team's E2E config methodology, the default is **match the workload, not
the configuration**: same prompts / ISL / OSL / dataset / spec-heads across platforms,
but **each platform runs at its native production config** (APC on, chunked prefill on,
best checkpoint per device, `random_range_ratio: 0.8`). This answers *"what does each
platform actually deliver in production?"* — the API-level truth, not a sanitized run
with features disabled to force equivalence.

- **Default (native-production):** `dataset: sharegpt`, APC + chunked prefill **on**,
  `max-num-batched-tokens` standardized (16384-capped), `num-prompts`/`num-warmups`
  floored at 50/20. ShareGPT (natural-language conversational prompts) is the primary
  workload because it's the closest thing the harness has to production traffic:
  realistic length distributions, real prefix-cache hit patterns, and — critically for
  **any regime with a spec-decode drafter** — token distributions the drafter was
  trained on. **Do not benchmark spec decode on `dataset: random`**: predictive drafters
  (Eagle, MTP, DSpark, MLPSpeculator, …) train against natural text; on random tokens
  their acceptance-length collapses to ~1.0 (nothing predictable to draft), and the
  reported "spec speedup" is not the spec speedup you'll ever see in production. The
  DSpark paper's own V4-Flash headline (60-85% per-user speedup) is on natural-text
  workloads; the same drafter on random tokens is ~1 accepted-token per cycle.
  Use `dataset: random` with `random_range_ratio: 0.0` ONLY as a **strict matched-config
  second pass** when you specifically need exact ISL/OSL for GPU-vs-Neuron comparison.
- **Per-request detail (`save_detailed: true`, default ON):** `vllm bench serve` deletes
  the per-request arrays from `result.json` unless `--save-detailed` is passed. The skill
  passes it by default so every cell's `result.json` retains `itls`, `ttfts`, `output_lens`,
  `start_times`, `generated_texts`, and `errors`. This is how downstream deliverables
  compute latency distributions, per-request TPOT (`sum(ITLs) / (output_len − 1)`, matching
  vLLM's own definition), and the **10%-trimmed-mean TPOT** that filters post-saturation
  tail requests. Cost is a few MB/cell of extra S3, worth it for the analytical freedom;
  set `"save_detailed": false` in the config to opt out only if disk is genuinely scarce.
- **Strict matched-config (opt-in):** to isolate the raw kernel/hardware gap, set
  `random_range_ratio: 0.0` (exact shapes) and disable the features the other platform
  lacks (e.g. `enable_chunked_prefill: false` vs a Neuron server without it). Say so in
  the writeup — it's a different question than the default.
- **Either way:** never let a knob drift *unintentionally* between platforms; change it
  on BOTH and record it. At **c=1** the configs are effectively equivalent (the fairest
  single comparison point); at c≥1 the gap includes both hardware and whatever
  native-config features (chunked prefill, APC) each platform brings.

### System-level optimizations (what's always on, and why)

These are the serving optimizations the sweep keeps ON by default (native-production).
Each is load-bearing for the numbers — know they're active, and which are actually
comparable across platforms. All are toggleable for a strict matched-config run.

| Optimization | Default | Set by | Disable for matched-config | Notes |
|---|---|---|---|---|
| **APC (prefix caching)** | ON | `--enable-prefix-caching` (`server.enable_prefix_caching`) | `enable_prefix_caching:false` | Big benefit only shows on shared-prefix traffic → run a **sharegpt** pass to measure it; on `random` it's ~inert. Report the prefix-hit% (in `metrics_*.txt`). |
| **Chunked prefill** | ON | `--enable-chunked-prefill` (`server.enable_chunked_prefill`) | `enable_chunked_prefill:false` | Splits a long prefill across scheduler steps so decode isn't starved. GPU default; a Neuron server may lack it → disable here for a matched comparison. |
| **CUDA graphs** | ON (implicit) | vLLM default (we do **not** pass `--enforce-eager`) | add `--enforce-eager` via `server.extra_flags` | The single biggest GPU decode optimization, and it's ON by *omission* — not an explicit flag. The `num-warmups` floor (≥20) exists to clear graph capture before timing. Disable only to debug or to match a non-graph backend. |
| **max-num-batched-tokens** | `auto` → `max(2·max_isl, 8192)` cap 16384 | `server.max_num_batched_tokens` | set a fixed value | Scheduler-step size; standardized so prefill of the largest ISL lands in one step and the step size is identical across models. |
| **FP8 KV cache** | per-regime | `kv_cache_dtype:fp8` on the fp8 regime | — | Independent of FP8 *weights* and FP8 *attention* — see "Regimes". |

**Interleaved prefill:** left at vLLM's default scheduling (chunked prefill interleaves
prefill chunks with decode within a step; the skill doesn't override the policy or set
`--long-prefill-token-threshold`). If you need to study that scheduler behavior
specifically, add the relevant flag via `server.extra_flags` and note it — it's not a
default the skill manages.

> **CUDA graphs are the one silent assumption** — everything else is an explicit config
> field, but graphs are on because we *don't* pass `--enforce-eager`. If a run looks
> unexpectedly slow at decode, confirm graphs actually captured (server log) before
> chasing anything else.

## Step 0 — Preflight: gather everything up front, BEFORE spending

This run rents expensive hardware (a p5 box is ~$55/hr on-demand; a p5 capacity block
was ~$495 for 14h) and may make **irreversible, prepaid purchases**. So resolve every
input the run needs in ONE upfront exchange with the user — don't discover a missing
token or an unset spend limit halfway through a paid run. Ask about anything below you
can't confidently default:

**Must confirm with the user (can't be safely defaulted — money / secrets / account / external):**
- **Model + checkpoint** — HF id, and for FP8 the exact pre-quantized checkpoint (verify it exists on HF).
- **Spec-decode `num_speculative_tokens` — look it up, don't guess.** If a regime enables `--speculative-config`, resolve `num_speculative_tokens` from the drafter's paper/recipe first, and confirm with the user before locking it in. The HF card's number is usually the *training* block size, not the deployment recommendation (see "Regimes" section for why this matters and the DSpark k=7 vs γ=5 case).
- **AWS account + credentials** — which AWS account/profile to use, and how creds are obtained. **Nothing in this skill is hardcoded to a specific account** — the commands use `$AWS_PROFILE` / `$REGION`, which you set once (below) from the user's answer. Creds expire → refresh them BEFORE starting (see "AWS credentials"), or long runs die mid-flight with `ExpiredToken`.
- **Instance type + how to get it** — e.g. `p5.48xlarge`. On-demand first; **may I purchase a capacity block if on-demand is exhausted, and what's the $ ceiling?** (Blocks are prepaid/non-refundable — see A1. Confirm the ceiling AND the acceptable block duration.)
- **HF token source** — where the token comes from (env var / secret manager). Required for gated models; recommended even for public ones (anonymous HF pulls get rate-limited, and a 235B download is huge). Never hardcode a token file path; never echo the token. See "HF token" below.
- **S3 destination** — durable store for results+logs+deliverables. Suggest `s3://<your-bench-bucket>/<run-prefix>/` in the run's account (auto-created if missing); **confirm the exact bucket name + account with the user** — do not assume one.

> **STOP-GATE — do not spend on unresolved answers.** Every "must confirm" item above has to be answered *concretely* before you rent or purchase anything. If the user leaves one unanswered, gives a non-committal answer ("whatever's fine", "you decide", "not sure"), or declines to set a spend ceiling / capacity-block authorization, **do NOT provision, launch, or purchase** — stop and ask again for that specific item. It is always correct to halt and re-ask rather than start a paid run on an assumption. Only the "safe to default" items below may proceed on a stated default; the money / secrets / account / checkpoint items may not. Announce the resolved values back to the user before the first `aws` call that costs money.

**Set these shell vars from the answers, then use them everywhere (nothing hardcoded):**
```bash
export AWS_PROFILE=<the user's profile>          # e.g. their dev account profile
export REGION=<region>                            # the box's region
export INSTANCE_TYPE=<e.g. p5.48xlarge>           # the GPU box to rent
export BUCKET=<the confirmed S3 bucket>           # durable results store
export RUN_PREFIX=<model>_<regime>_<date>         # groups this run's artifacts
# every aws command below uses --profile "$AWS_PROFILE" --region "$REGION"
```

**Safe to default (state your choice, proceed unless corrected):**
- Sweep axes (shardings, shapes, concurrencies, regimes) — the example config's production-grounded defaults are sound; prune shardings to what fits (see "Sizing for large / MoE models").
- Native-production server config (APC + chunked prefill on, etc. — see "Config philosophy" / "System-level optimizations").
- Region/AZ (resolve from the SG), key pair, AMI.

Once these are settled, write `bench_config.json` and run the flow (A1→A4). The config detail follows.

### AWS credentials (refresh BEFORE you start)

The skill issues real AWS calls (`ec2`, `s3`) via `--profile "$AWS_PROFILE"`. Credentials
are short-lived; a multi-hour sweep will outlive a stale token and fail mid-run with
`ExpiredToken`/`RequestExpired` (this happened during development). So obtain/refresh creds
up front, and be ready to refresh again before the final S3 push + terminate:
```bash
# typical internal refresh (adjust account/role/provider to the user's setup):
ada credentials update --account=<acct-id> --provider=isengard --role=<Role> --profile="$AWS_PROFILE" --once
aws sts get-caller-identity --profile "$AWS_PROFILE"   # confirm it works before spending
```
Prefer a least-privilege role that still allows `ec2 run/terminate`, `s3`, and (if used)
`purchase-capacity-block-reservation`. The exact account/role/provider are the user's to
supply — the skill does not assume them.

## Everything is user config — gather it, don't assume

This skill is model-agnostic. **Ask the user (or infer from their request) for the run config; nothing is hardcoded.** The GPU side is driven by a single `bench_config.json` (template: `assets/gpu/bench_config.example.json`).

**Config fields to gather:**
- **models** — list of `{key, hf_id}`. `key` = short label (output-dir prefix), `hf_id` = exact HuggingFace id.
- **gpu_count** — physical GPUs on the node. Every sharding's `tp*dp` must equal this.
- **shardings** — list of `{tp, dp, ep?}` (each `tp*dp == gpu_count`). `[{tp:8,dp:1},{tp:4,dp:2},{tp:2,dp:4},{tp:1,dp:8}]` is the full TP×DP sweep on an 8-GPU box. Per-model override via `models[].shardings` (a large model that only fits at high TP). **EP is a sharding property**, not a regime: add `"ep": true|false` alongside `tp`/`dp` on each entry to sweep expert-parallelism — `ep:true` emits `--enable-expert-parallel` (shards MoE experts across all `tp*dp` ranks; on a single 8-GPU box that's always EP=8, since vLLM's flag is a boolean, not a size knob). Same image, same checkpoint, just a different parallelism decision — so the deliverables' `sharding` label carries it (`tp4_dp2_ep8` vs `tp4_dp2`) and the Pareto envelope picks the best across `tp8/tp4_dp2/…/tp8_ep8/tp4_dp2_ep8/…` at each op-point. Contrast with `regimes`, which are engine differences (precision/quant checkpoint, spec-decode drafter) and get their own run dir + Pareto curve. When `ep` is omitted, defaults to the model's `moe` flag (dense models → off; MoE models → on). This replaces the old `tp_list`.
- **regimes** — the precision/quantization × speculative-decoding axis, swept in one run (e.g. `bf16`, `fp8`, `bf16_spec`, `fp8_spec`). Each regime → its own run dir + a `regime_meta.json` marker, so the deliverables split cost/Pareto by regime. **All regimes use the same `image`**, so spec-vs-nospec and quant-vs-bf16 are engine-matched by construction. Omit the block for a single bf16 no-spec regime. See "Regimes" below.
- **shapes** — list of `{isl, osl}` (input/output tokens per request). One sweep per shape. **Defaults are production-grounded** (Bedrock request shapes — see `references/profiling-targets.md`): ISL 2K–8K primary + a 512 short point + a 32K long-context point; OSL ~128–256 + an OSL=1 classify point. For a specific model, center on its median ISL/OSL from that doc.
- **concurrencies** — the concurrency grid; per-shape override via `concs_by_shape`. **Defaults center on real per-replica slot counts (16/24/32, coffee ~64)**, not a generic geometric grid — profile at the configured slot count, not idle. Long-context shapes fit fewer, cap lower.
- **targets** — production latency bars for PASS/FAIL gating: `ttft_p50_ms_by_isl` (TTFT is prefill-bound, so the bar scales with input length) + `tpot_p50_ms` (flat, ~13 ms/tok). From `references/profiling-targets.md`. Optional — omit to skip gating.
- **workload** — `dataset` (**sharegpt default**; `random` for strict matched-config second pass — see "Config philosophy" for why sharegpt is the primary workload and why random is wrong for spec-decode regimes), `dataset_path` (default `/scratch/sharegpt.json`, staged by setup.sh), `seq_len` (max-model-len under sharegpt), `random_range_ratio` (only applies when `dataset: random`: 0.8 for realistic jitter, 0.0 for exact shapes), `num_prompts_per_conc`/`min_prompts` + `warmups_per_conc`/`min_warmups` floors (50/20), `temperature`.
- **server** — `gpu_memory_utilization`, `enable_prefix_caching`/`enable_chunked_prefill` (**on** by default — native production), `max_num_seqs` (256), `max_num_batched_tokens` (`auto` → 16384-capped), `kv_cache_dtype` (`fp8` for FP8 KV), `extra_flags`.
- **models[].compilation_config / compilation_config_dp1** — per-model `--compilation-config` JSON. Use `compilation_config` when the flag is safe for every sharding; use `compilation_config_dp1` when a compilation pass is only stable at data-parallel-size 1 (some `fuse_allreduce_rms` / `eliminate_noops` combos hang on some vLLM builds with DP>1 — the Gemma bf16 v0.20.0-cu130 case). The skill emits `--compilation-config` only for dp==1 cells when the `_dp1` variant is set; dp>1 cells omit it. Both take a dict, serialized to JSON before passing to vLLM.
- **models[].safetensors_prefetch** (bool, default false) — force `--safetensors-load-strategy prefetch`. vLLM's default (lazy mmap) auto-detects NFS and switches to prefetch there, and is optimal for most models on most filesystems, so leave this off by default. Set it to `true` when weight loading DEADLOCKS with this exact signature: all TP workers idle in `hrtimer_nanosleep` after "Using FP8 indexer cache" (or equivalent post-kernel-selection log), GPU util 0%, per-worker `read_bytes: 0`, no "Loading safetensors checkpoint shards" line ever prints. That's vLLM issue #40988 — the default's random-read pattern deadlocks on some large-MoE + POSIX/ext4 shard layouts (DeepSeek V4-Flash on ext4 `/scratch` was the concrete case; prefetch flag took load from 28+ min hang → 40s per rank). If you see the signature: add this to the model entry, done.
- **GPU instance type** — which node to rent (e.g. `p5.48xlarge` = 8× H100; `g6e.48xlarge` = 8× L40S). Drives `gpu_count`.
- **HF_TOKEN** — supply via the **environment** (`HF_TOKEN=…` on the pytest command, or a secret manager the run reads into env). Do NOT hardcode a token-file path in the skill/config and do NOT commit or echo it. Whether it's *required* depends on the model:
  - **Gated model** (Llama, some others) → **required**: no token ⇒ HF 401/403 at download ⇒ `vllm serve` never loads ⇒ every cell's server-start fails. Verify access to the exact checkpoint before provisioning.
  - **Public/ungated** (most Qwen, OLMo, …) → **not strictly required, but recommended**: anonymous HF downloads are rate-limited, and a large model (e.g. Qwen3-235B ≈ 235 GB) can throttle/429 mid-pull; an authed token avoids that.
  - The skill reads `HF_TOKEN` from the environment only — it never opens a token file. (Rotate the token if it's ever been exposed; a benchmark run puts it on a rented box.)
- For Neuron: **TP**, **instance** (`trn2.48xlarge`), and the model's **ECR bench image**.

If the user just says "benchmark model X on GPU," the example config's production-grounded shapes/concurrencies/targets are a sound default — confirm the instance type before spending money. See `references/profiling-targets.md` for how the defaults were derived and per-model shape/latency baselines.

---

## A. GPU benchmark (upstream vLLM on NVIDIA)

Stock upstream vLLM — no Neuron plugin, no custom code. Each model runs upstream's own architecture implementation. `ctrl` and any arch not registered upstream cannot run here.

**Preferred flow:** rent a GPU box yourself with `aws ec2` (A1) → set it up (A2) → run
the sweep on it with `--transport=docker` (A3). This keeps the box under your control
and is the default. Only if you **cannot get an instance directly** (A1's capacity
retries exhausted) fall back to the Kaizen transport (A3, "last resort").

### A1. Launch a GPU instance with `aws ec2`

This is the primary way to get hardware — a plain on-demand `run-instances` (or a
capacity block for scarce GPU types); terminate when done. Uses `$AWS_PROFILE` / `$REGION`
from Step 0 (nothing account-specific hardcoded). Resolve the VPC/SG + key pair from the
user's setup (`<sg-id>`, `<key-name>` are theirs to supply):

```bash
# import your existing key pair's public half into the region (one-time)
ssh-keygen -y -f ~/.ssh/<your-key>.pem > /tmp/pub.txt
aws ec2 import-key-pair --profile "$AWS_PROFILE" --region "$REGION" \
  --key-name <key-name> --public-key-material fileb:///tmp/pub.txt

# AMI: Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.11 (Amazon Linux 2023)
AMI=$(aws ec2 describe-images --profile "$AWS_PROFILE" --region "$REGION" --owners amazon \
  --filters "Name=name,Values=Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.11*Amazon Linux 2023*" \
  --query 'reverse(sort_by(Images,&CreationDate))[0].ImageId' --output text)

aws ec2 run-instances --profile "$AWS_PROFILE" --region "$REGION" \
  --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
  --key-name <key-name> --security-group-ids <sg-id> --associate-public-ip-address \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":400,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=gpu-bench}]'
```
> **Root EBS size:** 400 GB here because IPA caches HF weights under `~/.cache`
> (root EBS) — `setup.sh` symlinks that to `/scratch`, but the download still needs
> room if the symlink hasn't taken. A 235B model ≈ 235 GB; size root for
> `weights + margin` (bump higher for >250 GB models). The 28 TB `/scratch` RAID0 is
> for fast loading, not the weight download landing spot unless the symlink is in place.

**Capacity-retry ladder (exhaust this before considering Kaizen).** If
`InsufficientInstanceCapacity`: (1) retry other AZs in the region, (2) try a smaller
GPU size that still fits the model, (3) try another region (e.g. us-east-1). For
scarce large-GPU types (p5/p6-class H100/H200/Blackwell) on-demand often has no
capacity — use a **Capacity Block**:
```bash
aws ec2 describe-capacity-block-offerings --instance-type "$INSTANCE_TYPE" \
  --instance-count 1 --capacity-duration-hours 24 --profile "$AWS_PROFILE" --region "$REGION"   # find offerings
aws ec2 purchase-capacity-block --capacity-block-offering-id <cb-...> \
  --instance-platform Linux/UNIX --profile "$AWS_PROFILE" --region "$REGION"                    # SPENDS — confirm first
aws ec2 run-instances ... --instance-market-options 'MarketType=capacity-block' \
  --capacity-reservation-specification 'CapacityReservationTarget={CapacityReservationId=cr-...}'
```
> **CLI note:** the verb is `purchase-capacity-block` (NOT
> `purchase-capacity-block-reservation` — that errors "invalid choice"). And
> `describe-capacity-block-offerings` with `--capacity-duration-hours < 24` returns
> empty — **query with 24**; the results still include shorter real blocks (a 19h block
> came back from a 24h query and was the cheapest same-day option).
**Sizing the block — the cost lever (confirm with the user before buying):**
- A block bills for its **WHOLE reserved window, prepaid and non-refundable** — terminating the instance early does NOT refund it. So *duration*, not prompt teardown, is what sets the cost.
- There's a **minimum (currently 24h) and quantized durations** — you often can't buy "3h for a small test"; you're forced up to the floor. Real data point: a **p5.48xlarge 14h block was ~$494.89** (≈$35/instance-hr), so even a tiny sweep's floor cost is hundreds of dollars — factor that into the go/no-go, not just the on-demand rate.
- Estimate needed time = **image pull + weight download + per-model compile/load (5+ min each) + Σ(per-cell runtime) + margin**, then buy the **smallest offered duration that covers it**. Surface that estimate + total prepaid $ to the user for approval before purchasing.
- **Reuse an active block** you already paid for: relaunch into it with `--capacity-reservation-specification CapacityReservationTarget={CapacityReservationId=cr-...}` rather than buying a new one for the same window.

Only if the whole ladder fails (and capacity blocks aren't available/approved) fall
back to the Kaizen transport in A3. `g6e.12xlarge` (4× L40S, 184 GB) fits dense models
+ Mixtral 8x7B at TP4; large MoE like Qwen3-235B needs an 8×80GB box (p5.48xlarge) —
see "Sizing for large / MoE models".

**SSH access:** the bench host's egress IP must be in the SG. Add it:
```bash
aws ec2 authorize-security-group-ingress --profile "$AWS_PROFILE" --region "$REGION" \
  --group-id <sg-id> --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=$(curl -s checkip.amazonaws.com)/32}]"
```
User is `ec2-user`. Old SSH clients reject `-o StrictHostKeyChecking=accept-new`; use `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`.

> **Flapping corp egress:** on some networks (VPN, load-balanced egress) the observed
> egress IP shifts mid-run and the `/32` rule stops matching, dropping SSH. If SSH drops
> mid-sweep, either re-`authorize-security-group-ingress` for the new `/32`, or widen the
> CIDR to the enclosing `/22` (`$(curl -s checkip.amazonaws.com | awk -F. '{print $1"."$2"."$3".0/22"}')`).
> The sweep itself runs detached with on-box `sync_results.sh` and survives orchestrator
> SSH drops (see A3), so **the risk is losing observability, not losing data** — but if
> you need to send commands (kill/inspect/reconfig) you'll want stable ingress. Clean up
> every rule you add before terminating (leave the SG in the state you found it).

### A2. Set up the node (the default `--transport=docker` path)

This is the **default, preferred** path: you rented a GPU box in A1 and run the sweep
on it directly (`--transport=docker`). Only fall back to Kaizen if you cannot get an
instance this way (see A3).

The orchestrator (InferencePerfAnalyzer) isn't on PyPI, so clone it on your local
machine and `scp` the source onto the box alongside the skill assets:
```bash
# local: clone IPA (once)
git clone ssh://git.amazon.com:2222/pkg/InferencePerfAnalyzer /tmp/InferencePerfAnalyzer

# scp IPA source + the gpu assets to the box
scp -r /tmp/InferencePerfAnalyzer ec2-user@<ip>:~/InferencePerfAnalyzer   # HOME, not /scratch (setup.sh creates /scratch)
scp -r assets/gpu ec2-user@<ip>:~/gpu
```
Then on the node run `setup.sh` — it RAID0s the local NVMe SSDs into `/scratch`
(fast weight loading), pulls the vLLM image, creates `/scratch/bench-venv` and
`pip install -e`s IPA + pytest into it, and stages the ShareGPT dataset (the
default workload). Idempotent. See `references/gpu-setup.md`.

### A3. Run the sweep (config-driven, via pytest — pick a transport)

Write the user's config to `bench_config.json` (copy `bench_config.example.json`).
`sweep_bench.py` drives IPA over every `model × regime × shape × sharding(tp,dp,ep) ×
concurrency` cell; the server is reused across concurrencies (only a regime/sharding
change restarts it), cells past capacity trip the failure guard and SKIP, and a
**resume ledger** (`state.json` at the results root) skips already-PASSED cells so an
interrupted/timed-out sweep resumes without re-running finished work. Each
`(model, regime)` gets a run dir + `regime_meta.json`; each cell writes
`<results>/<key>__<regime>/<key>_<isl>_<osl>_<tag>/conc_<C>/result.json`.

`--transport` picks where the server runs. **Prefer `--transport=docker` on a box you
rent with `aws ec2` (A1+A2).** Kaizen is a **last resort** — use it only when you
genuinely cannot get an instance directly (see "When to fall back to Kaizen").

**Default — local docker box (rent with `aws ec2`, run in-place):** inside the bench
venv on the GPU box you set up in A1/A2. **Run all three on the box DETACHED** (each
`nohup … &`) so the run survives the orchestrator (agent/laptop/SSH) dying — the box's
own teardown becomes the only hard stop, and everything is already in S3 up to the last
sync. Launch order: log capture, then the S3 sync loop, then the sweep.
```bash
# on the box, over SSH. All three detached (survive SSH drop / orchestrator death):

# (1) server-log capture — IPA `docker rm -f`s each container, so logs die with it
#     unless captured (this cost run #1 its launch evidence).
ORCHESTRATOR_RESULTS_ROOT=/scratch/results \
  nohup bash ~/gpu/capture_logs.sh 30 >/tmp/caplog.out 2>&1 &

# (2) incremental S3 sync — pushes /scratch/results -> S3 every 5 min so data is durable
#     even if the box is torn down mid-run (needs creds ON the box — IAM instance role
#     preferred; see sync_results.sh). This is what run #3 lacked (agent-side sync died
#     with the agent, stranding cells on the box).
ORCHESTRATOR_RESULTS_ROOT=/scratch/results BUCKET="$BUCKET" RUN_PREFIX="$RUN_PREFIX" \
  AWS_PROFILE="$AWS_PROFILE" REGION="$REGION" \
  nohup bash ~/gpu/sync_results.sh 300 >/tmp/sync.out 2>&1 &

# (3) the sweep itself — DETACHED, logged to a file you can tail/reattach to.
ORCHESTRATOR_RESULTS_ROOT=/scratch/results BENCH_CONFIG=~/gpu/bench_config.json \
HF_TOKEN=<token> \
  nohup /scratch/bench-venv/bin/pytest ~/gpu/sweep_bench.py --transport=docker -v \
  >/scratch/results/sweep.log 2>&1 &

# monitor without holding the run open: tail the log, or check the resume ledger.
tail -f /scratch/results/sweep.log        # progress
cat /scratch/results/state.json | python3 -m json.tool | grep -c passed   # cells done
```
All three are detached, so you can drop the SSH session (or lose the laptop) and the
sweep keeps running + syncing on the box. The **resume ledger** (`state.json`) means a
re-launched sweep skips finished cells, so an interrupted run resumes cleanly.
`capture_logs.sh` streams each bench container's logs to `serverlogs/`;
`sync_results.sh` ships everything (results + logs) to S3 on an interval. (`--transport=docker`
must be explicit — IPA's own default is `kaizen`, so always pass the flag.) Why docker is
the default: you provision with a plain `aws ec2 run-instances` (A1), keep the box under
your control, iterate/debug over SSH, and avoid the Kaizen image-bake + workload round-trip.

> **Creds for the on-box sync:** `sync_results.sh` runs `aws s3 sync` FROM THE BOX, so
> the box needs its own credentials — best is an **IAM instance profile** attached at
> `run-instances` (`--iam-instance-profile Name=<role>` with `s3:PutObject` on the
> bucket), so there's nothing to expire. Otherwise copy short-lived creds onto the box
> (they expire — a long run may outlive them; the loop retries and recovers if refreshed).

> **Large models (e.g. 235B):** `setup.sh` bumps IPA's `_DOCKER_CMD_TIMEOUT_S` (120s →
> 900s) because `docker run -d --gpus all` on a big multi-GPU image blocks on device
> reservation longer than IPA's default cap, which otherwise fails every new server
> config (this killed run #1's tp4_dp2 + 2nd shape). If you see "server start … timed
> out after 120 seconds", the patch didn't apply — re-run setup.sh or raise
> `IPA_DOCKER_CMD_TIMEOUT_S`.

**When to fall back to Kaizen (last resort):** only if you **can't get the instance
directly** — e.g. `aws ec2 run-instances` returns `InsufficientInstanceCapacity`
across the AZs/regions you tried (A1's retry ladder exhausted), or your account can't
launch the GPU type on-demand / via a capacity block at all. Kaizen provisions the
GPU for you from a shared pool, at the cost of a one-time image bake and a
submit-and-wait round-trip. Point `ORCHESTRATOR_RESULTS_ROOT` at a **durable FSx path**
so the resume ledger survives across workloads:
```bash
kaizen start-workload --image "$KAIZEN_IMAGE" \
  --instanceType p5.48xlarge --nodeCount 1 --timeout 5400 \
  --command "export ORCHESTRATOR_RESULTS_ROOT=\$FSX_TEAM_SHARED_RW/<alias>/results \
             BENCH_CONFIG=<path>/bench_config.json KAIZEN_IMAGE=$KAIZEN_IMAGE && \
    python3 -m pytest <path>/sweep_bench.py -o addopts= --transport=kaizen -v" \
  --tail-logs --profile kaizen-access
```
(`KAIZEN_IMAGE` = a baked image with IPA+pytest pre-installed — see the IPA
image-baking guide; that bake is the main reason this path is heavier. `-o addopts=`
clears any repo-default pytest `addopts` that could break discovery — harmless no-op
otherwise.) Note Kaizen has its own capacity limits: a workload can sit in `PENDING`
and never schedule — try the other GPU instance type or retry later.

Useful subsetting (either transport, standard pytest):
```bash
pytest sweep_bench.py --collect-only -q                       # preview cells, no run
pytest sweep_bench.py --transport=docker -v -k "fp8_spec and tp4_dp2"   # one regime+sharding
```
Quick on-box sanity table: `bash collect.sh <results_root>`.

### A4. Push results to S3, then terminate

Results (and `serverlogs/`) live on the box's `/scratch` and **die on terminate**. If
you ran `sync_results.sh` (A3), most of the tree is **already in S3** — this step is just
the FINAL sync (catch cells since the last interval) + confirm + terminate. The durable
store is the **S3 bucket confirmed in Step 0** (`$BUCKET`, each run under `$RUN_PREFIX/`),
in the **same account as the box**. Create the bucket once if missing (only needed if
`sync_results.sh` didn't already, e.g. no IAM instance profile):
```bash
# create the bucket if it doesn't exist (idempotent; skip if it already exists)
aws s3api head-bucket --bucket "$BUCKET" --profile "$AWS_PROFILE" 2>/dev/null \
  || aws s3api create-bucket --bucket "$BUCKET" --profile "$AWS_PROFILE" --region "$REGION" \
       --create-bucket-configuration LocationConstraint="$REGION"
```
Final sync of the whole nested tree (incl. `serverlogs/`) box → S3, THEN terminate:
```bash
# final catch-up sync (the periodic sync_results.sh already shipped most of it)
aws s3 sync /scratch/results/ "s3://$BUCKET/$RUN_PREFIX/results/" --profile "$AWS_PROFILE"
aws ec2 terminate-instances --profile "$AWS_PROFILE" --region "$REGION" --instance-ids <iid>
```
> Confirm S3 has the cells (`aws s3 ls s3://$BUCKET/$RUN_PREFIX/results/ --recursive | grep -c result.json`)
> BEFORE terminating — this is the durable copy now, not a local dir. With `sync_results.sh`
> running, a mid-run box loss (e.g. block auto-terminate) still leaves everything up to the
> last sync interval in S3; only cells since the last tick are lost.

Then pull from S3 and produce the deliverables locally (both scripts bundled in `assets/gpu/`):
```bash
aws s3 sync "s3://$BUCKET/$RUN_PREFIX/results/" ./results/ --profile "$AWS_PROFILE"
# 0. (once) install the deliverables deps LOCALLY — NOT installed by setup.sh (which
#    runs on the GPU box). Needs pandas + matplotlib + tabulate + numpy; without
#    tabulate, pandas.to_markdown() (used to render every .md table) fails at runtime.
pip install -r assets/gpu/requirements-deliverables.txt
# 1. --multi reads each run's regime_meta.json (one dir per model×regime) and emits
#    tables + Pareto frontiers (per regime AND regime-overlay) + coverage + all_cells.csv.
#    --instance labels the box; --model-name labels titles (else derived from run_id).
python build_deliverables.py ./results/ --multi --instance <p5|p5en|g6e.48xlarge|...> \
    --model-name "<Model Label>" --out ./deliverables
# 2. cost: $/1M-token bars + cost/latency frontier, split by regime (reads step 1's csv)
python build_cost_deliverables.py ./deliverables/all_cells.csv --model-name "<Model Label>" \
    --out ./deliverables/cost
# 3. push the generated deliverables back to S3 so the run is self-contained there
aws s3 sync ./deliverables/ "s3://$BUCKET/$RUN_PREFIX/deliverables/" --profile "$AWS_PROFILE"
```
Pass `--config bench_config.json` to step 1 to gate each cell **PASS/FAIL** against
the production latency bars in the config's `targets` block (TTFT scaled by ISL,
TPOT flat) — the console prints the pass rate + the closest-miss per regime×shape,
and `all_cells.csv` gets `pass_ttft`/`pass_tpot`/`pass` columns. These bars come from
production Bedrock data (`references/profiling-targets.md`).

`--multi` attributes each cell's regime from its `regime_meta.json`, so the cost and
Pareto outputs separate bf16 / fp8 / spec cleanly. (`build_deliverables.py` also has a
single-run mode: `--regime <bf16|fp8> [--spec-decode] --instance <...>` without
`--multi`, for a one-regime run.) `all_cells.csv` is the tidy per-cell audit trail.
The cost script maps `--instance` to an on-demand price; its table covers
p5/p5en/b200/b300 and g6e.12xlarge/g6e.48xlarge. An **unpriced instance is dropped
with a warning** (not a hard error) — add its rate to `PRICE_PER_HR` to include it
(prices are a dated snapshot; re-verify before quoting $). Cross-instance cost plots
only render when ≥2 priced instances share a regime×shape.

> Always `aws s3 sync` results to S3 (and confirm) BEFORE terminating. Results on the rented box are lost on terminate and are expensive to reproduce — a ~$495 capacity block bought run #1's data, so don't lose it to a skipped upload. Terminating auto-detaches the shared SG — do not delete the SG or its rules (and remove only the SSH-ingress rule YOU added).

---

## B. Neuron benchmark (vLLM-Neuron via Kaizen)

The model must already be ported/registered in vLLM-Neuron and have a built image in ECR. The in-container runner is `assets/neuron/bench_one_model.sh` (already baked into the bringup images).

### B1. Submit

```bash
HF_TOKEN=<token> python3 assets/neuron/submit_neuron_bench.py \
  --image <ecr-uri-of-model-bench-image> \
  --model-id <HF_ID> --tp <TP> --instance trn2.48xlarge
```
This base64-encodes the in-container script (sets `MODEL_ID/TP/NUM_PROMPTS/SEQ_LEN/MAX_NUM_SEQS`, runs `bench_one_model.sh`) and submits via `kaizen start-workload`. It prints the full workload ID (`T_...`) — always record the FULL id.

### B2. Pull results

Status: `kaizen get-workload --workload-id <WID> --profile kaizen-access`. A `SUCCEEDED` status does NOT mean the bench produced numbers — always verify `completed == num_prompts`.

Results land in FSx; pull the single result.json (fast):
```bash
kaizen get-artifact --location WORKLOAD_OUTPUT_RW --workload-id <WID> \
  --source "/bench/result.json" --destination ./neuron_<MODEL>.json --profile kaizen-access
```
(For models served from a converted local checkpoint dir, the path is `/bench/<MODEL>/result.json`.) Refresh CloudWatch creds when needed: `ada credentials update --account=<kaizen-cw-acct-id> --provider=isengard --role=ReadOnly --profile=kaizen-cw --once` (the account that owns the Kaizen cluster's CloudWatch metrics — ask your Kaizen admin; same `<acct-id>` placeholder convention as Step 0).

---

## C. Comparing Neuron vs GPU (do this honestly)

- **Same workload only.** Confirm both used the identical bench + serve config.
- **State the accelerator budget.** Neuron TP8 = 8 NeuronCores; GPU TP1 = 1 GPU. These are NOT equal. Compare per-accelerator (tok/s ÷ devices), per-instance (whole box vs whole box), or each platform at its own best TP. Never quote a raw GPU/Neuron ratio as a "hardware verdict."
- **Parallelism sweet spots differ.** On GPU, small (≤2B) models peak at TP1; 3–8B scale up with TP. On Neuron, very low TP can be pathological for a given model (a single core starved → huge TTFT) — each platform has its own optimal TP. Run a TP sweep before declaring a "best" number.
- Report `completed`, the exact config, the instance/TP, and the harness version alongside every number.

---

## Common failures (and the fix)

| Symptom | Cause | Fix |
|---|---|---|
| `completed=0`, server returns 404 on `/v1/completions` | served model name ≠ bench `--model` (model served from a local path) | add `--served-model-name <HF_ID>` to the serve cmd |
| `vllm: error: unrecognized arguments: --disable-log-requests` | wrong flag for this vLLM version | use `--no-enable-log-requests` |
| bench DNS error / all requests fail with `host.docker.internal` | Linux Docker has no `host.docker.internal` | run bench with `--network host` and `--host localhost` |
| `SERVE DIED` immediately but model actually loads | readiness probe killed on parent-PID exit; `vllm serve` re-execs during multi-rank init | poll only the HTTP `/v1/models` endpoint; don't treat parent exit as death |
| `InsufficientInstanceCapacity` on launch | no on-demand capacity in that AZ | retry other AZs → smaller size → other region |
| `invalid hostPort` | port number > 65535 | keep host ports in valid range (e.g. 8010/8020/8040) |
| arch "not supported" on GPU | model arch not registered in upstream vLLM (e.g. `CTRLLMHeadModel`) | cannot benchmark on upstream GPU; note it |
| every cell of a sharding skips "server did not start (likely OOM)" | model doesn't fit at that TP — server OOMs at load; the cell is skipped so the sweep continues, but each still cost a start attempt (on Kaizen, wasted timeout) | do the fit math up front and DROP that sharding (per-model `shardings`) — see "Sizing for large / MoE models" |
| config rejected: `tp*dp != gpu_count` | a sharding doesn't fill the box | every `{tp,dp}` must satisfy `tp*dp == gpu_count` |
| MoE model serves wrong / OOMs unexpectedly | missing expert parallelism | set `moe: true` on the model entry (adds `--enable-expert-parallel`) |
| workers stuck idle after "Using FP8 indexer cache" (or equivalent post-kernel-selection log), GPU 0%, `read_bytes: 0`, "Loading safetensors checkpoint shards" never prints, no forward progress for many minutes | safetensors' default random-read pattern deadlocks on some large-MoE + POSIX/ext4 layouts (vLLM issue #40988); default auto-detect thinks "local, use lazy" | set `safetensors_prefetch: true` on the model entry — emits `--safetensors-load-strategy prefetch`, load goes from hang → tens of seconds per rank |
| high-conc cells all SKIP with "high failure rate" | real saturation / KV exhaustion past the knee | expected — the guard is bracketing the peak; the last usable conc is the ceiling for that sharding |
| Neuron `SUCCEEDED` but not trusted | SUCCEEDED ≠ benched | always gate on `completed == num_prompts` (Neuron side) |

## Running via a task-dispatching agent (Claude Code, etc.) — optional

This skill is deliberately runtime-neutral — a human operator SSH'ing into a rented box can follow it end-to-end. But when a task-dispatching agent runtime is available (Claude Code's Agent tool, for example), delegating provisioning + launch to a sub-agent is worth the setup. The pattern that survived three real runs (Gemma bf16 re-run, Qwen fp8, DSV4-Flash):

**Sub-agent scope: launch, not watch.** A sub-agent's lifetime is typically bounded (dies when the parent session ends). So its job is to *provision, set up, smoke-test, and launch the detached processes* on the box — then report and exit. It does NOT stay alive to babysit the sweep. The sweep runs detached (`nohup … & disown`) and survives sub-agent death by design, so pytest + `sync_results.sh` + `capture_logs.sh` keep going even after the sub-agent exits. Data lands in S3 continuously; the parent session picks up from S3 next time.

**Step-0 smoke gate.** Before authorizing the full spend, run **~3 cells** covering the risk axes: (1) a nospec/baseline cell to prove the vLLM image + config serve the model at all; (2) an EP-on cell if EP is being swept, to prove that code path; (3) a spec-decode cell if a spec regime is being swept, to prove the drafter loads and drafts (verify `spec_decode_acceptance_rate` in `result.json`). Sub-agent STOPS at that point and reports for go/no-go. Cheap way to catch: wrong image tag, incompatible `--speculative-config method`, incompatible `--rope-scaling` on newer vLLMs, model-load hangs (see the safetensors_prefetch row in the troubleshooting table), and OOMs at the smallest sharding-that-should-fit. Every real DSV4 launch failure this project has hit was caught here in the smoke, not in the full sweep.

**Hard-limit fences to hand to a sub-agent.** These are the ones that mattered:
- No new capacity blocks / no new instances beyond what the parent authorized. If the current block is exhausted, STOP and report — a fresh purchase is the parent's decision.
- Do NOT modify sweep_bench.py / IPA source on the box. If a config change is needed and isn't in the workspace file, STOP and report — don't improvise. (The "improvised fix" trap: two of three DSV4 attempts hit new failures after apparent fixes; letting the sub-agent iterate would have burned block time without producing data.)
- Do NOT touch teammates' instances (identify by Name tag / launch time / owner).
- Do NOT clear or rewrite the IPA `state.json` ledger. If it's stale, let IPA archive it (the signature changes and a fresh ledger is created — no cells are lost, cells that already produced result.json in S3 aren't re-run because the resume ledger inspects the on-box results tree).
- Never echo the HF token.

**Cross-session handoff file.** Sub-agent writes `~/Documents/<run>/instance.txt` on the orchestrator machine BEFORE launching anything: instance-id, capacity-reservation-id, public IP, key file path, SG id, subnet, IAM profile, S3 destination prefix, image tag, block end time, and a status line. This is what the parent session reads at the start of the next turn to reconstitute state — no relying on sub-agent memory, no relying on the parent session's own memory across compactions.

**When the sub-agent reports:** the parent reads S3 (`aws s3 ls` on the run prefix, download `state.json`) to verify the sub-agent's claims independently before deciding next steps. This has caught: sub-agent reporting "detached processes running" when they actually died on SSH close; sub-agent under-counting cells because it only looked at ledger not S3; sub-agent claiming a smoke passed when the cell's result.json wasn't actually written.

## Bundled resources

- `assets/gpu/bench_config.example.json` — the user config template (models, gpu_count, shardings TP×DP, shapes, concurrencies, workload, server, fail_threshold). **This is the thing you fill in per request.**
- `assets/gpu/sweep_bench.py` — the InferencePerfAnalyzer-driven pytest sweep. Reads `bench_config.json` and drives IPA's transport-agnostic pool over every model×**regime**×shape×sharding(tp,dp,ep)×concurrency cell (regime = precision/quant × spec-decode, all on one engine; ep = expert-parallelism on/off, folded into the sharding axis), with the server cache, failure-rate guard, and **resume ledger**, dropping a `regime_meta.json` per run dir. Runs on a **local docker box** (`--transport=docker` — the preferred path, on an instance you rent with `aws ec2`) or on **Kaizen** (`--transport=kaizen` — last resort when you can't get an instance directly; note IPA's own flag default is `kaizen`, so pass `--transport=docker` explicitly). No model is hardcoded. Replaces the old `run_bench.sh`/`bench_one.sh`.
- `assets/gpu/setup.sh` — one-time node setup (RAID0 `/scratch`, pull image, create `/scratch/bench-venv` and `pip install -e` IPA + pytest, patch IPA's docker-timeout for large models, optionally stage ShareGPT).
- `assets/gpu/capture_logs.sh` — continuous vLLM server-log capture to `serverlogs/` (docker transport). Run detached (`nohup … &`) alongside the sweep; without it IPA's `docker rm -f` destroys the logs a failed server-start needs.
- `assets/gpu/sync_results.sh` — continuous incremental `aws s3 sync` of the results tree, run detached ON THE BOX (needs box-side creds — IAM instance profile preferred). Makes a long sweep's data durable even if the orchestrator dies or the box is torn down mid-run (the gap that lost run #3's tail). Env: `BUCKET`, `RUN_PREFIX`, `AWS_PROFILE`, `REGION`, `ORCHESTRATOR_RESULTS_ROOT`.
- `assets/gpu/collect.sh` — quick on-box sanity table over the nested `result.json` layout (flags cells with >5% failures).
- `assets/gpu/build_deliverables.py` — run locally on the synced `results/`: per-shape metric tables, Pareto frontiers over the sharding×concurrency grid, coverage report, and `all_cells.csv` (the tidy per-cell audit table). Single-run mode takes `--instance` / `--regime`; `--config bench_config.json` adds PASS/FAIL columns vs the production latency bars.
- `assets/gpu/build_cost_deliverables.py` — run locally on `all_cells.csv`: **$/1M-token** cost bars (cross-instance) + cost/latency frontier, at on-demand list price. Holds a dated price table for p5/p5en/b200/b300 (`PRICE_PER_HR`) — add other instances' rates before use; re-verify prices before quoting.
- `assets/neuron/submit_neuron_bench.py` — generic Kaizen submitter (args: `--image --model-id --tp --instance ...`).
- `assets/neuron/bench_one_model.sh` — in-container Neuron runner (reads `MODEL_ID/TP/...` from env).
- `references/gpu-setup.md` — RAID0 / image / dataset detail + SSH/SG specifics.
- `references/metrics.md` — every result.json field + derived per-stream / per-accelerator metrics.
- `references/profiling-targets.md` — production Bedrock request shapes, per-model latency/concurrency baselines, and the TTFT(ISL)/TPOT pass-fail bars that seed the config defaults + `targets` block. **The "what to profile and what to beat" source.**
