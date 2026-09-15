# Collision-surface ranking

Which `vllm-neuron` files two concurrent increments are most likely to both
edit. Use it for two decisions: may two increments run in parallel, and which
file surface a planned change touches. Rank follows collision probability, not
file size. Line numbers shift on every vLLM pin bump, so this file names
methods, never lines: derive lines with grep at the current pin.

## Ranked surfaces

| Rank | Path | Why it collides | Parallel rule |
|---|---|---|---|
| 1 | `vllm_neuron/vllm/worker/neuron_model_runner.py` | One ~8,000-line class, `NeuronModelRunner`, holds every runtime subsystem (loading, warmup/compile, `execute_model`, spec decode, KV and encoder cache, capture). Any two runtime changes meet here. | Single writer. Parallel only when both increments name disjoint method clusters up front; `execute_model` is single-writer always. |
| 2 | `vllm_neuron/vllm/platform.py` | `NeuronPlatform` funnels config mutation, request validation, quantization gating, DCP/DP validation, and attention-backend selection; most features add a validator here. | Adding a new method is parallel-safe; the one-line registrations in `check_and_update_config` and `validate_request` are applied by one hand or sequenced. |
| 3 | `vllm_neuron/vllm/worker/neuron_worker.py` | `NeuronWorker` plus module-level distributed-bootstrap helpers; device init, parallelism degree, and lifecycle changes land here. | Serialize increments that both touch init or bootstrap; side-by-side helpers rarely conflict. |
| 4 | `vllm_neuron/vllm/core/scheduler.py` | Sole scheduling hook: `NeuronScheduler` and `NeuronAsyncScheduler` subclass upstream. | Serialize only when both increments are scheduling-class. |

Safe surface: `vllm_neuron/vllm/kv_connector/` subclasses upstream connector
ABCs with no entanglement in the runner. Put KV-transfer and disaggregation
work there when it can live there; it turns a rank-1 collision into an
isolated edit.

None of the four ranked files has test coverage; the repo's only tests are
`test/unit/spec_decode/`.

## Rules

1. Declare scope first. Before an increment starts, list the files and named
   methods it will edit. Two in-flight scopes that intersect on a rank-1 or
   rank-2 file are ordered, never merged later.
2. Serialize on the runner unless rule 1 shows disjoint method clusters.
3. Read only the named slices. Index the file first, then read the ranges you
   will edit:
   ```
   grep -n "^class \|    def " vllm_neuron/vllm/worker/neuron_model_runner.py
   ```
4. Before merging, diff the increment's hunks (`git diff --unified=0`) against
   every other in-flight scope. An undeclared hunk in a rank-1 or rank-2 file
   stops the merge for review.
5. Bring tests with every increment that edits a ranked file; nothing else
   catches concurrent-edit breakage.

All paths are relative to the `vllm-neuron` repository root.
