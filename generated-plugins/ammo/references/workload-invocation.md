# Workload Invocation: `new_target.py` Details

For `target.json.serving`, use `serving-campaign.md` for measurement and objective policy. The batch-only invocation and instrument rules below apply to other targets. Accelerator ownership and runtime binding remain unchanged.

This file is the supplementary detail for §Invocation in SKILL.md. Read it when
you scaffold a campaign with `new_target.py` and you need the flag a user
parameter maps to, the default when the user gives none, or the reason a flag
behaves in a surprising way. The verbose examples and the edge-case explanations
live here, not in SKILL.md, so the orchestrator does not read them cold.

## Parameter Table

| Parameter | Prompt format (extract from user) | `new_target.py` flag | Default (if user omits) |
|-----------|----------------------------------|---------------------|------------------------|
| Batch sizes | `--batch-sizes 1 8 32` | `--batch-sizes 1 8 32` | `[1, 8, 32]` |
| Max model length | `--max-model-len=auto` or `--max-model-len=65536` | same value; `auto` resolves from the cached model config (fails closed if uncached) | `4096` (omit flag for default) |
| Input/output lengths | `--input-len=<N> --output-len=<N>` | `--input-len <N> --output-len <N>` | `64` / `512` (only if user doesn't specify) |
| Max num sequences | `--max-num-seqs=32` | `--max-num-seqs 32` | omit (Pumice server default) |
| Prefill chunk budget | `--max-num-batched-tokens=4096` | `--max-num-batched-tokens 4096` | omit on CUDA; on `pumice-neuron` the scaffold freezes `4096` when `max_model_len > 4096` (see Prefill Chunk Budget) |
| Prompt source | `--prompt-source=raw` | `--prompt-source raw` | omit (`gsm8k`, chat-template-wrapped); freeze `raw` when `input_len` cannot hold the chat template (see Prompt Source) |
| Multi ISL/OSL pairs | `--isl-osl=64:512,2048:256` | same value | N/A |
| Data parallel size | `--data-parallel-size 2` | `--data-parallel-size 2` | `1` (single replica) |
| Expert parallelism | `--enable-expert-parallel` | `--enable-expert-parallel` | off |

Batch sizes define the decode buckets for all profiling and validation throughout the campaign.

## Model and Hardware Support (check before you scaffold)

Pumice does not run every model on every GPU. Check both before you scaffold.
A bad `--model-id` or `--hardware` gives a target that you cannot measure.

- **Architectures** (HF `architectures[0]`, from `src/pumice/models/registry.py`
  at Pumice 9d126713): `GptOssForCausalLM`, `Gemma4ForCausalLM`,
  `Gemma4ForConditionalGeneration`, `DeepseekV4ForCausalLM`,
  `LagunaForCausalLM`, `GlmMoeDsaForCausalLM`. Draft heads:
  `Eagle3ForCausalLM`, `Gemma4AssistantForCausalLM`.
- **Hardware**: confdb resolves backends per SM family. `sm90` is H100/H200,
  `sm100` is B200 and B300, `sm120` is consumer Blackwell. A model with no
  section for the family of the GPU fails at server startup. The session
  image adds an AMMO-local `[sm80]` row for gpt-oss only, so gpt-oss boots on
  L40S (sm_89). Rows from that host are "harness validation only".
- **Model path**: pass a plain model directory that holds `config.json`, for
  example `/hf/gpt-oss-20b`. The Rust loader rejects the HF cache layout.
- **Neuron** (`pumice-neuron`): the Pumice Neuron registry is llama3, gpt_oss,
  qwen3_moe, and qwen3_vl. TP must be a multiple of the LNC. Spec-decode and
  grammar tracks are off on Neuron.

If the user names a model or GPU outside these sets, stop and tell the user.
Do not scaffold a target that cannot produce a Stage 1 baseline.

## Canonical Invocation

```bash
.venv/bin/python scripts/new_target.py \
  --artifact-dir .ammo/{model}_{hardware}_{dtype}_tp{tp} \
  --model-id <MODEL_ID> --hardware <HW> --dtype <DTYPE> --tp <TP> \
  [--batch-sizes <BATCH_SIZES>] \
  [--input-len <INPUT_LEN> --output-len <OUTPUT_LEN>] \
  [--max-model-len <MAX_MODEL_LEN>] \
  [--max-num-seqs <MAX_NUM_SEQS>] \
  [--max-num-batched-tokens <MAX_NUM_BATCHED_TOKENS>] \
  [--isl-osl <ISL:OSL,...>] \
  [--data-parallel-size <DP_SIZE>] \
  [--enable-expert-parallel]
```

Take `<INPUT_LEN>` and `<OUTPUT_LEN>` from the user's prompt. If the user
specifies neither, omit both flags; the scaffold then freezes the 64/512
decode-heavy workload.

## Sequence Lengths and the Workload Matrix

One ISL/OSL pair is the common case: pass `--input-len` and `--output-len`
straight to `new_target.py`.

For more than one pair (`--isl-osl=`), pass the comma-separated pairs straight
to the flag. `new_target.py` crosses those pairs with every batch size and
freezes the result as `workload_matrix`. Do not mutate `target.json` after
scaffolding.

```json
"workload": {
    "workload_matrix": [
        {"input_len": 64, "output_len": 512, "batch_size": 1},
        {"input_len": 64, "output_len": 512, "batch_size": 8},
        {"input_len": 2048, "output_len": 256, "batch_size": 1},
        {"input_len": 2048, "output_len": 256, "batch_size": 8}
    ],
    "num_iters": 10
}
```

## Max Model Length (`--max-model-len`)

`--max-model-len=auto` resolves `max_position_embeddings` from the locally
cached HF model config. If no cached config exists, the scaffold fails closed
before it writes anything. Supply the concrete model limit as an integer
instead. With `auto`, `target.json` also records the request in
`notes.max_model_len_request`, so later stages do not mistake the resolved value
for a hand-picked limit. Omit the flag and the scaffold freezes its default
`4096`.

## Serving Concurrency (`--max-num-seqs`)

Pass an explicit `--max-num-seqs` value to `new_target.py` only when the user
requests one. The scaffold records it in `bench.extra_args`:

```json
"bench": {
    "extra_args": ["--max-num-seqs", "32"],
    ...
}
```

Omit the flag and `extra_args` stays empty for that knob, so the default
serving concurrency of the Pumice server applies. The scaffold does not invent
a value.

## Prefill Chunk Budget (`--max-num-batched-tokens`)

On CUDA, omit the flag unless the user sets it; the Pumice server default applies.

On `pumice-neuron`, `new_target.py` freezes `--max-num-batched-tokens 4096` in
`bench.extra_args` on its own when `max_model_len` is greater than 4096 and the
flag is absent, and prints a WARNING that says so. Pass the flag to choose
another supported value. The Neuron runner
(`src/pumice/engine/neuron_model_runner.py`) sets `kv_segment_size` to
`max_num_batched_tokens` when that value equals `max_model_len`, and the
segmented attention kernel (`neuron_inference_core/functional/attention/
attention_segmented_cte.py`) compiles only `kv_segment_size` in
`{512, 1024, 2048, 4096}`. With `--max-model-len 8192` and no
`--max-num-batched-tokens`, every TP rank dies at warmup with
`ValueError: kv_segment_size=8192 is not supported`, before `HealthCheck`.
The Pumice gpt-oss-20b Neuron smoke test uses `--max-num-batched-tokens 4096`.
Record the resolved value from the `NeuronModelRunner config:` log line in
`constraints.md`. A prefill of `batch x input_len` tokens at or under the
budget is unaffected by the cap.

## Prompt Source (`--prompt-source`)

The runner builds each E2E timing prompt from the bundled corpus. With the
default `gsm8k` source it wraps the window in the model's chat template, so the
template header and footer take part of `input_len`. gpt-oss needs 67 tokens
for that, more than the default `input_len` of 64, and the runner fails fast
before the server launch: `workload input_len [64] is too small for this
model's chat template (template overhead is 67 tokens)`. Both 2026-09-24
gpt-oss-20b campaigns (L40S and trn2) lost one launch to this.

Fix it once at scaffold time: `new_target.py --prompt-source raw` writes
`bench.prompt_source: "raw"` to `target.json`, and the runner uses that value
whenever its own `--prompt-source` flag is absent, so Stage 1, Stage 5 and
Stage 6 arms all measure the same prompt shape. Raising `input_len` above the
overhead is the other valid fix when the user did not pin it. `raw` changes
only the timing prompts (corpus windows sized exactly to `input_len`, no
template). The correctness phase (`--capture-golden-refs`,
`--verify-correctness`) always builds real GSM8K questions through the chat
template, whatever the prompt source. Note the choice in `constraints.md`;
`random` cannot be frozen (debug only, not evidence).

## Backends Are Not Flags

confdb resolves the attention backend, MoE backend, KV-cache layout, block size,
and runner kind from `(architecture, compute capability, dtype, quantization)`.
Do not put `--attention-backend`, `--moe-backend`, or `--model-runner` in
`bench.extra_args`: that changes the configuration under test. Record the
RESOLVED values in `constraints.md`. Read them from the server log line
`Backends from confdb`, or resolve them directly:

```bash
.venv/bin/python -c "import pumice_rust; print(pumice_rust.resolve_config(['<Arch>'], (9, 0), '<quant>'))"
```

A backend change is a confdb edit. A confdb edit is a rebuild-required change;
see `references/impl-track-rules.md`.

## Prefix-Cache Regime

`target.json` records one prefix-cache regime for the campaign. The runner
passes the same `--enable-prefix-caching` or `--no-enable-prefix-caching` flag
to both arms, and the flag overrides confdb. Do not put a prefix-cache flag in
`bench.extra_args` by hand. See § Prefix-cache regime in
`references/e2e-latency-guide.md`.

## Expert Parallelism (`--ep` vs `--enable-expert-parallel`)

`--enable-expert-parallel` is the flag that turns on expert parallelism.
`--ep N` (integer) does not: it is a legacy sizing field persisted to
`target.ep` in `state.json`, and it is not passed through to the server
command. Most MoE workloads should leave `--ep 1` (default) and rely on
`--enable-expert-parallel` plus the TP size.

## Data Parallelism (`--data-parallel-size`)

The Rust `pumice-rust/src/dp_coordinator.rs` coordinates Pumice data
parallelism over `--data-parallel-port`. There is **one server process for
each `--data-parallel-rank`**, and global rank = `dp_rank * tp_size + tp_rank`.
The runner launches the whole DP cohort. There is no torchrun and no
`--distributed-executor-backend external_launcher`. That flag was a vLLM
requirement and has no Pumice equivalent, so do not put it in
`bench.extra_args`. The world size for GPU reservation is `TP x DP`
(`references/gpu-pool.md`).

For an MoE model, `dp_size > 1` also means that the cross-DP MoE collective
runs in lockstep on every step. An idle replica runs a dummy forward. The
target does not express this, but the per-bucket latency of a DP sweep
includes the lockstep barrier.

## Pumice Preflight

Run `.venv/bin/python scripts/preflight_pumice.py` once at
campaign start and again after any engine change. It needs no GPU. It checks the
proto fields, server flags, and metric names that the runner reads, the
`shared/pumice_pins.py` versions when the runner kind is `vllm`, the cmb-api
patch in `$CARGO_HOME/config.toml`, and that the model path is a plain
directory with `config.json`. Exit 1 lists each item to fix before you
measure.
