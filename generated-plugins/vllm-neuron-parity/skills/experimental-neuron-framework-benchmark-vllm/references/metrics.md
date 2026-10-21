# result.json metric reference

`vllm bench serve --save-result` writes a `result.json` per run. Fields used:

| Field | Meaning |
|---|---|
| `completed` | requests that finished. **Must equal `num_prompts`.** `0` or `<num_prompts` = broken run, not a fast one. |
| `failed` | failed requests (should be 0) |
| `num_prompts`, `max_concurrency`, `request_rate`, `burstiness` | the workload knobs (record them) |
| `duration` | wall-clock seconds of the benchmark phase |
| `total_input_tokens`, `total_output_tokens` | token counts processed |
| `output_throughput` | **output tok/s** — generated tokens per second (the headline serving number) |
| `total_token_throughput` | (input+output) tok/s |
| `request_throughput` | completed requests / second |
| `mean_ttft_ms`, `median_ttft_ms`, `p99_ttft_ms` | time-to-first-token |
| `mean_tpot_ms`, `p99_tpot_ms` | time-per-output-token (decode step latency) |
| `mean_itl_ms`, `p99_itl_ms` | inter-token latency |
| `model_id` | the served model — **verify it matches the model you intended** (guards against a wrong-file mix-up when consolidating) |

## Validation rules before trusting a number

1. `completed == num_prompts` and `failed == 0`.
2. `model_id` inside the JSON matches the expected HF id.
3. The serve + bench config matches the fixed workload (or the deviation is recorded).

## Derived metrics for cross-platform comparison

- **Per-stream decode tok/s** ≈ `1000 / mean_tpot_ms` — the single-stream decode rate,
  the closest comparable to a batch-1 / single-stream decode number from another
  source (e.g. NXDI).
- **Per-accelerator throughput** = `output_throughput / num_devices` (devices = GPUs
  for GPU, NeuronCores = TP degree for Neuron). Use this when accelerator counts differ.
- **Per-instance** = the whole-node number at the TP that uses the whole node.

## Quick read of a result.json

```bash
python3 -c "import json;d=json.load(open('result.json'));print('completed=%s out=%.1f req=%.2f tot=%.1f ttft=%.1f tpot=%.1f itl=%.1f'%(d['completed'],d['output_throughput'],d['request_throughput'],d['total_token_throughput'],d['mean_ttft_ms'],d['mean_tpot_ms'],d['mean_itl_ms']))"
```
