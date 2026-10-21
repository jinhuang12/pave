# Stage 1: Smoke Tests (CODE)

> **Your role:** You are a test runner. Run the bundled script, record the output, and report pass/fail. Do NOT debug failures or read source code to investigate — just record the token match rate and proceed to Stage 2.

Verify the port is alive and produces coherent output. Delegates to `model_validation`.

> **vLLM-Neuron targets:** this script loads the adapter, so its automatic `check_environment()` runs first and **exits early with an `EnvironmentError`** on a missing `vllm_neuron` import, and prints a compatibility note on a `vllm`/`vllm-neuron` version skew. The exit is intended fail-fast behavior, not a skill bug — fix the environment and re-run. Details in [STAGE0.md](STAGE0.md) and SKILL.md.

## Required Outputs

```
{EXP_DIR}/results/
└── stage1.json    # Token matching + enhanced distribution metrics
```

## Run

```bash
python3 scripts/run_stage1.py \
  --model-path ${HF_MODEL_PATH} \
  --compiled-model-path ${COMPILED_MODEL_PATH} \
  --model-class ${PORT_MODELING_FILE}:${PORT_CAUSAL_CLASS} \
  --config-class ${PORT_MODELING_FILE}:${PORT_CONFIG_CLASS} \
  --num-tokens 32 \
  --output ${EXP_DIR}/results/stage1.json
```

Requires `model_validation/` on PYTHONPATH.

## What It Does

- 10-prompt greedy token matching via `check_accuracy_with_hf_golden`
- Per-position distribution metrics via `compute_enhanced_metrics` (cosine similarity, KL divergence, top-k agreement, relative L2 error)

## Pass Criteria

Token match rate > 30% (liveness threshold). This is NOT a correctness test.

## Interpretation

- 100% match on most prompts with a few divergences → normal BF16 precision drift
- < 30% match → catastrophic failure, proceed to Stage 2 for localization
- High cosine similarity (> 0.95) with low token match → margin-sensitive divergence (expected)
