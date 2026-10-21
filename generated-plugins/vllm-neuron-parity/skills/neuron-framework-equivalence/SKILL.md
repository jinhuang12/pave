---
name: neuron-framework-equivalence
description: Verifies functional equivalence between two implementations of the same model using a hierarchical 8-stage algorithm. Orchestrates model tree comparison, component-level R-ratio testing, E2E logit comparison, and distributional checks. Use when porting models between frameworks, hardware targets, or precision regimes — e.g., HuggingFace to NxDI, PyTorch to ONNX, GPU to Neuron, FP32 to BF16, or any source-target pair.
---

# Model Equivalence Framework

Verify and diagnose functional equivalence between a **source** (reference) and a **target** (ported) implementation of the same model.

## Scope: causal-LM / token-generation models only — does NOT apply to pooling/embedding models

This entire pipeline (token/logit teacher-forcing, `TARGET_CAUSAL_CLASS`/`ForCausalLM`
wrapper, R-ratio on logits) is built around models with a **sampler and a token-generation
output** (decode-step models). It has no defined behavior for a **pooling/embedding
model** (`--runner pooling` in vLLM, no `lm_head`/sampler, output is a fixed-size embedding
vector, no next-token distribution to compare).

**Do not force-fit a pooling model into this skill** (e.g. by looking for a
`ForCausalLM`-shaped class that doesn't exist, or trying to compare "logits" that the
model never produces). Real incident: `generic-encoder-only-model`
(`LlamaBidirectionalModel`, mean-pool + L2-normalize head, prefill-only, no KV cache) has
no causal-LM class and no logits — attempting this skill's standard flow on it has no
valid inputs to provide for `TARGET_CAUSAL_CLASS`.

**For a pooling/embedding model, use embedding cosine similarity against a trusted
reference instead:**
1. Run the target model (target framework/hardware/precision) and the source/reference
   model (typically HF CPU-eager, bf16) on the same set of diverse prompts (aim for
   several: short factual, technical, narrative, code — different enough to exercise
   different attention/pooling behavior).
2. Compute `cos(embedding_target, embedding_reference)` per prompt.
3. Threshold: **> 0.999 is a pass** for a well-ported bf16 model (0.9999x is typical for a
   correct port; worst-case cosine in the 0.999925–0.999965 range was measured as PASS on
   a real 8B model across TP=1 and TP=16+compiler-flag configs — tensor-parallel sharding
   and scheduler flags should not measurably move this number if the port is correct).
4. Also check a **cross-prompt discriminative** cosine (embeddings of two different,
   unrelated prompts against each other) — this should be well below the same-prompt
   cross-implementation cosine (e.g. ~0.28–0.30 vs ~0.9999). A pooling bug that collapses
   all outputs toward a constant vector would still show a plausible same-prompt cosine
   in isolation but fail this cross-check.
5. If the model supports batched/packed prefill (multiple sequences in one forward pass),
   also verify **no cross-sequence leakage**: cosine between a sequence's embedding
   computed solo vs. computed packed alongside other sequences should be ~1.000000. This
   catches bidirectional-attention bugs where padding or a neighboring sequence leaks into
   a query's attention window.

This is a lighter-weight, model-specific script, not this skill's 8-stage pipeline — do
not attempt to route it through `run_stage0.py`/`run_stage5.py`/etc., which all assume a
`ForCausalLM` class exists.

## Required Inputs

Before starting, collect these from the user. Ask for any missing ones.

| Input | Description | Example |
|-------|-------------|---------|
| `SOURCE_MODEL_PATH` | Path to source model weights (HF format) | `/path/to/hf_models/Qwen3-0.6B` |
| `COMPILED_MODEL_PATH` | Path to compiled target model | `/path/to/neuron_models/Qwen3-0.6B` |
| `TARGET_MODELING_FILE` | Path to target's modeling .py file | `/path/to/modeling_qwen3.py` |
| `TARGET_INNER_CLASS` | Inner model class (extends NeuronBaseModel) | `NeuronQwen3Model` |
| `TARGET_CAUSAL_CLASS` | ForCausalLM wrapper class | `NeuronQwen3ForCausalLM` |
| `TARGET_CONFIG_CLASS` | InferenceConfig class | `Qwen3InferenceConfig` |
| `VENV` | Path to Python venv with torch + neuronx | `/opt/aws_neuronx_venv_pytorch_2_8_nxd_inference` |
| `MODEL_VALIDATION_DIR` | Path to model_validation package | `/path/to/NeuroborosFoundations/model_validation` |
| `NEUROBOROS_SRC` | Path to the NeuroborosFoundations `src` tree the CPU templates import (set as an environment variable) | `/path/to/NeuroborosFoundations/src` |
| `EXP_DIR` | Experiment output directory | `agent_artifacts/equiv_qwen3` |
| `VLLM_NEURON_DIR` | **vLLM-Neuron targets only.** Project root of the `private-vllm-neuron` editable install | `/path/to/private-vllm-neuron` |

Set `SCRIPTS_DIR` to the absolute path of this skill's `scripts/` directory.

## vLLM-Neuron Targets: Version Pin + PYTHONPATH

This skill's `vllm_neuron` adapter is **pinned to vLLM `0.24.0`** and the **vLLM-Neuron plugin `0.24.0`** line (two independently-versioned packages; the pins live in `scripts/adapters/vllm_neuron.py` as `PINNED_VLLM_VERSION` and `PINNED_VLLM_NEURON_VERSION`). When the target stack is vLLM-Neuron:

- **Version check is automatic.** `get_adapter()` calls `adapter.check_environment()` immediately after construction. If `vllm_neuron` is not importable, the run **exits early with a clear message** — no cryptic mid-stage `AssertionError: Current vLLM config is not set.` If the installed `vllm` or `vllm-neuron` plugin is not on the `0.24` line, it prints a **compatibility note** and proceeds: the adapter was verified on `0.24`, so a result on another line stands only once a stage passes on it.
- **Editable install must be on PYTHONPATH.** `vllm_neuron` is a local editable install, and vLLM's plugin entry point (`vllm_neuron:register`) imports it. Prepend `VLLM_NEURON_DIR` to `PYTHONPATH` on **every** stage command for vLLM-Neuron targets:

  ```bash
  PYTHONPATH={VLLM_NEURON_DIR}:{SCRIPTS_DIR} python3 {SCRIPTS_DIR}/run_stage0.py ...
  ```

  For stages that also need `MODEL_VALIDATION_DIR`, chain all three:
  `PYTHONPATH={VLLM_NEURON_DIR}:{MODEL_VALIDATION_DIR}:{SCRIPTS_DIR} python3 ...`

## Default Test Prompts

If the user does not provide test data, the bundled scripts (`run_stage5.py`, `run_teacher_forced_comparison.py`) use built-in defaults. For manual testing or other contexts, use these 5 prompts:

1. `"The future of artificial intelligence is"` — short, common topic
2. `"In a groundbreaking discovery, scientists at"` — medium, factual
3. `"Once upon a time in a land far away, there"` — narrative style
4. `"The mathematical proof demonstrates that for all"` — technical/mathematical
5. `"def fibonacci(n):\n    '''Return the nth"` — code completion

## Prerequisites

Before proceeding past Stage 0, confirm:

- [ ] Environment ready — venv activated, `torch` and `neuronx` importable
- [ ] Both model trees built successfully (Stage 0 output exists)
- [ ] `component_mapping.json` exists and covers all components
- [ ] Component test files written (for Stage 2)
- [ ] For device stages (5+): model compiled and runnable on Neuron

## CRITICAL RULES

1. **Do NOT write your own scripts.** Run the bundled scripts in `scripts/` directly.
2. **The ONLY files you create are:** `component_mapping.json` (Step 1) and `test_NN_*.py` test files (Step 3).
3. **Do NOT write wrapper scripts, helper scripts, or any .py files** other than the test files.
4. **Follow stage order strictly.** Do NOT skip ahead or reorder.
5. **Show full output** from every script run. Do not summarize or truncate.

## BEHAVIORAL CONSTRAINTS

### During Stages 0–3, 5–7: You are a TEST RUNNER

- **Record** failures (component name, R-ratio, error message) and **continue** to the next test
- Do NOT investigate why a test failed
- Do NOT read source code to understand root causes
- Do NOT write or modify patches, fixes, or workarounds
- Do NOT re-run a failed test with different parameters hoping it passes
- Do NOT skip device validation or mark results as "Pending"
- Do NOT override `num_hidden_layers` for E2E tests — E2E means full model
- Do NOT apply model-specific fixes from past experiments — only use generic framework knowledge

### During Stage 4 ONLY: You are a DEBUGGER

- This is the ONLY stage where you read source code and write patches
- All fixes go in standalone monkey-patch files — never modify the original port
- Fix bottom-up (components before E2E) and inside-out (CPU before device)
- See [references/debug-orchestration.md](references/debug-orchestration.md) for the escalation workflow

## Workflow

Execute in this exact order. Each step has a single command to run.

> **vLLM-Neuron targets:** prepend `{VLLM_NEURON_DIR}:` to the `PYTHONPATH` of every command below (e.g. `PYTHONPATH={VLLM_NEURON_DIR}:{SCRIPTS_DIR} python3 ...`). The adapter's automatic version check prints a compatibility note if `vllm` is off the pinned `0.24` line. See [vLLM-Neuron Targets](#vllm-neuron-targets-version-pin--pythonpath) above.

### Step 1: Stage 0 — Build Model Trees

```bash
source {VENV}/bin/activate
PYTHONPATH={SCRIPTS_DIR} python3 {SCRIPTS_DIR}/run_stage0.py \
  --source-model-path {SOURCE_MODEL_PATH} \
  --target-model-path {SOURCE_MODEL_PATH} \
  --target-module-file {TARGET_MODELING_FILE} \
  --target-inner-class {TARGET_INNER_CLASS} \
  --target-config-class {TARGET_CONFIG_CLASS} \
  --output-dir {EXP_DIR}/model_tree
```

Then compare the two printed trees and build `{EXP_DIR}/component_mapping.json`.
See [STAGE0.md](STAGE0.md) for mapping instructions and [references/mapping_example.json](references/mapping_example.json) for format.

Then run class divergence detection:

```bash
python3 {SCRIPTS_DIR}/detect_class_divergence.py \
  --target-module-file {TARGET_MODELING_FILE} \
  --output {EXP_DIR}/class_divergence_report.json
```

This identifies components that use different classes on CPU vs device (e.g., `LlamaRMSNorm` on CPU, `CustomRMSNorm` on device). These require dual tests in Stage 2.

### Step 2: Stage 1 — Smoke Test

```bash
PYTHONPATH={MODEL_VALIDATION_DIR} python3 {SCRIPTS_DIR}/run_stage1.py \
  --model-path {SOURCE_MODEL_PATH} \
  --compiled-model-path {COMPILED_MODEL_PATH} \
  --model-class {TARGET_MODELING_FILE}:{TARGET_CAUSAL_CLASS} \
  --config-class {TARGET_MODELING_FILE}:{TARGET_CONFIG_CLASS} \
  --num-tokens 32 \
  --output {EXP_DIR}/results/stage1.json
```

**Decision:** If token match < 50% → catastrophic failure, proceed to Step 3 for localization. Otherwise continue.

### Step 3: Stage 2 — Component Tests (YOU WRITE THESE)

1. Copy `{SCRIPTS_DIR}/tensor_compare.py` into `{EXP_DIR}/tests/`
2. Create `{EXP_DIR}/tests/conftest.py` from [templates/conftest_template.py](templates/conftest_template.py) — fill in model constants from `config.json`
3. Write `test_NN_*.py` files for each mapped component. See [STAGE2.md](STAGE2.md) for the pattern and [templates/test_template.py](templates/test_template.py) for the template.
4. Run:

```bash
NXD_CPU_MODE=1 python3 {SCRIPTS_DIR}/run_stage2.py \
  --tests-dir {EXP_DIR}/tests \
  --tau-r 1.2 \
  --output {EXP_DIR}/results/stage2.json
```

**Decision:** If all R < 1.2 → proceed to Step 4. If any fail → run Stage 3:

```bash
python3 {SCRIPTS_DIR}/run_stage3.py \
  --stage2-output {EXP_DIR}/results/stage2.json \
  --output {EXP_DIR}/results/stage3.json
```

Then debug and patch (see [STAGE4.md](STAGE4.md)). Re-run Stage 2 until all pass.

### Step 4: Stages 5+6 — Teacher-Forced E2E + Distributional + Semantic

This single script covers Stage 5 (E2E R-ratio), Stage 6 Condition B (cosine), and Stage 6 Condition C (KL) — all under proper teacher forcing.

```bash
PYTHONPATH={MODEL_VALIDATION_DIR}:{SCRIPTS_DIR} python3 {SCRIPTS_DIR}/run_teacher_forced_comparison.py \
  --model-path {SOURCE_MODEL_PATH} \
  --compiled-model-path {COMPILED_MODEL_PATH} \
  --model-class {TARGET_MODELING_FILE}:{TARGET_CAUSAL_CLASS} \
  --config-class {TARGET_MODELING_FILE}:{TARGET_CONFIG_CLASS} \
  --num-tokens 32 \
  --output {EXP_DIR}/results/teacher_forced.json
```

### Step 5: Stage 7 — Downstream Eval (optional)

```bash
python3 {SCRIPTS_DIR}/run_stage7.py \
  --bench-config {EXP_DIR}/bench_config.yaml \
  --output-dir {EXP_DIR}/results/stage7
```

See [STAGE7.md](STAGE7.md) for bench config format.

### Step 6: Generate Report

Aggregate all stage results into `{EXP_DIR}/EQUIVALENCE_REPORT.md`.
See [references/report-template.md](references/report-template.md) for the structure and completion gate.

**Do NOT generate the report until all stages have concrete results.** No "Pending" sections.

## R-Ratio

```
R = ||target - source_fp32||_F / (||source_lowprec - source_fp32||_F + ε)
```

| R | Meaning |
|---|---------|
| ≈ 1.0 | Healthy — matches precision baseline |
| > 1.2 | Bug — excess divergence |
| < 1.0 | Over-precision — extra `.float()` calls |

## Verdict

```
PASS ⟺ Stage 1 acceptable ∧ Stage 2 all R < 1.2
      ∧ Stages 5+6 E2E R consistent ∧ Condition B(θ) ∧ Condition C(δ)
      ∧ Stage 7 no regressions
```

## Resources

- [STAGE0.md](STAGE0.md) — Tree building + component mapping
- [STAGE2.md](STAGE2.md) — How to write component tests
- [STAGE4.md](STAGE4.md) — Debug/patch workflow
- [STAGE5.md](STAGE5.md) — E2E comparison details
- [STAGE6.md](STAGE6.md) — Condition B/C details
- [templates/](templates/) — conftest and test templates
- [references/](references/) — structural diffs, mapping example, equiv-concept, QQ plots
- [references/debugging-case-study-gptoss.md](references/debugging-case-study-gptoss.md) — Complete worked example from GPT-OSS 20B
- [references/device-component-debugging.md](references/device-component-debugging.md) — XLA-compatible patch patterns for device debugging
- [references/device-e2e-debugging.md](references/device-e2e-debugging.md) — 1-layer isolation and device E2E fix-compile-verify cycle
- [references/cpu-e2e-debugging.md](references/cpu-e2e-debugging.md) — CPU E2E with mp.spawn, TP>1, bias restoration
- [references/dump-tensors.md](references/dump-tensors.md) — Intermediate tensor capture methodology
- [references/report-template.md](references/report-template.md) — EQUIVALENCE_REPORT.md structure and completion gate
- [references/debug-orchestration.md](references/debug-orchestration.md) — 5-stage debugging escalation workflow with stage gates and subagent delegation
- [references/enable-model-run.md](references/enable-model-run.md) — Device compilation workflow, troubleshooting, and BF16 gloo fix
- [references/adapter-contract.md](references/adapter-contract.md) — 5-method adapter interface for adding new serving stacks
- [references/vllm-neuron-adaptation.md](references/vllm-neuron-adaptation.md) — vLLM-Neuron weight transpositions, forward signature, TP detection, KV cache shapes
