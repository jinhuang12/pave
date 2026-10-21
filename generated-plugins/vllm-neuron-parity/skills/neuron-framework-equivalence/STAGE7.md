# Stage 7: Downstream Task Evaluation (CODE)

> **Your role:** You are a benchmark runner. Run the bundled script, record scores, and report pass/fail per task. Do NOT investigate why a task regresses — just report the delta and recommend Stage 4 if regressions exceed tolerance.

Confirm the port remains usable for production workloads using industry-standard benchmarks.

> **vLLM-Neuron targets:** this script loads the adapter, so its automatic `check_environment()` runs first and **exits early with an `EnvironmentError`** on a missing `vllm_neuron` import, and prints a compatibility note on a `vllm`/`vllm-neuron` version skew. The exit is intended fail-fast behavior, not a skill bug — fix the environment and re-run. Details in [STAGE0.md](STAGE0.md) and SKILL.md.

## Run

```bash
python3 scripts/run_stage7.py \
  --bench-config ${EXP_DIR}/bench_config.yaml \
  --output-dir ${EXP_DIR}/results/stage7 \
  --tolerance 0.02
```

Or run `neuron_bench` directly:

```bash
python -m neuron_bench.run --config ${EXP_DIR}/bench_config.yaml --hf-baseline
```

Requires `neuron_bench/` on PYTHONPATH.

## Bench Config Format

```yaml
model:
  model_class: "path/to/modeling.py:NeuronXxxForCausalLM"
  config_class: "path/to/modeling.py:XxxInferenceConfig"
  model_path: "/path/to/hf_model"
  compiled_model_path: "/path/to/compiled_model"

benchmarks:
  lm_eval:
    accuracy:
      tasks: ["gsm8k_cot", "mmlu_pro"]
      limit: 200
      use_chat: true

run_hf_baseline: true
```

## What It Does

- Runs lm_eval tasks (MMLU, HellaSwag, GSM8K, etc.) on both HF and Neuron models
- Compares scores with per-task tolerance bands (default 3-5 percentage points)
- Reports pass/fail per task and overall

## Pass Criteria

Score regression ≤ 2 percentage points on all top-level tasks.

## Interpretation

- All tasks within tolerance → port is production-ready
- Math/reasoning tasks fail but knowledge tasks pass → precision-sensitive computation affected
- All tasks fail → fundamental porting issue, go back to Stage 2
