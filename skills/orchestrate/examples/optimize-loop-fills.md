# Using optimize-loop.md

`optimize-loop.md` turns orchestrate into a hill-climb loop for any system
that runs on NVIDIA GPUs and has a correctness gate and one north-star
metric. Pass its path and your slot values in one message. The lead reads
the template, fills the slots from your message, and asks once about any it
cannot fill.

Find the template under the plugin root, at
`skills/orchestrate/examples/optimize-loop.md`. For an installed plugin the
root is `~/.claude/plugins/cache/jinhuang12-plugins/pave-init/<version>/`;
for a checkout it is the repository. The installed path changes with every
version, so copy the template out first if you want to keep a run's text
fixed.

## Ready-to-paste invocation (vLLM)

Replace each `<...>`, then paste. On Codex, start with
`$pave-init:orchestrate` instead.

```
/pave-init:orchestrate Run the optimization loop in <path>/optimize-loop.md.
Fill its slots with these values. Ask me only about a slot I left out.

SYSTEM: vLLM at <checkout>, commit <sha>
HARDWARE: <n>x <GPU type>, <interconnect>
GPU_IDS: <0,1,2,3>
TARGET: model <model> at <path>, <dtype>, engine args: <args> (CUDA graphs and torch.compile on)
OPERATING_POINTS: batch sizes <1, 8, 32>; input <n> tokens; output <m> tokens
WORLD_SIZE: <the TP size>
PHASES: prefill, then decode steps
METRIC_NAME: E2E request latency, ms, lower is better
METRIC_COMMAND: vllm bench latency --model <path> <engine args> --batch-size <B> --input-len <n> --output-len <m> --num-iters-warmup 5 --num-iters 20 --output-json <file>
GATE_NAME: GSM8K accuracy, higher is better
GATE_COMMAND: lm_eval --model vllm --model_args pretrained=<path>,<engine args> --tasks gsm8k --num_fewshot 5 --batch_size auto --output_path <dir>
SCOPE: config and flags, kernels, engine code, numerics
BUILD: git worktree, a new venv, pip install -e . (precompiled wheels when no C++ changes)
FLOOR_GAP_PCT: <10>
BACKSTOP_PCT: <2>
```

Slots with a default may be left out: `METRIC_RUNS`, `GATE_RUNS`,
`LEASE_TOOL`, `RUN_DIR`, `BRANCH`. `BOUND_HINTS` is optional. To reuse one
set of values, save them in a file and send
`/pave-init:orchestrate Run the loop in <path>/optimize-loop.md with the slots in <file>`.

## Example fill: LLM inference (vLLM)

| Slot | Value |
|---|---|
| `{{SYSTEM}}` | vLLM at `<checkout>`, commit `<sha>` |
| `{{TARGET}}` | model `<model>` at `<dtype>`, frozen engine args (TP, max model length, CUDA graphs, compile config) |
| `{{OPERATING_POINTS}}` | batch sizes `<1, 8, 32>`, input `<n>` tokens, output `<m>` tokens |
| `{{WORLD_SIZE}}` | the TP size |
| `{{PHASES}}` | prefill, then decode steps |
| `{{METRIC_NAME}}` | E2E request latency, ms, lower is better |
| `{{METRIC_COMMAND}}` | `vllm bench latency --model <path> <engine args> --batch-size <B> --input-len <n> --output-len <m> --num-iters-warmup 5 --num-iters 20 --output-json <file>` |
| `{{GATE_NAME}}` | GSM8K accuracy, higher is better |
| `{{GATE_COMMAND}}` | `lm_eval --model vllm --model_args pretrained=<path>,<engine args> --tasks gsm8k --num_fewshot 5 --batch_size auto --output_path <dir>` |
| `{{SCOPE}}` | config and flags, kernels, engine code, numerics |
| `{{BUILD}}` | git worktree, a new venv, `pip install -e .` (precompiled wheels when no C++ changes) |
| `{{BOUND_HINTS}}` | decode step: max(active weight bytes + KV bytes read / HBM bandwidth, FLOPs / peak); E2E = prefill + output length x decode step |

nsys for vLLM needs `--trace-fork-before-exec=true --cuda-graph-trace=node`,
because the engine runs in a child process and uses CUDA graphs.

## Example fill: model training (PyTorch)

| Slot | Value |
|---|---|
| `{{SYSTEM}}` | training script `<repo>/train.py`, commit `<sha>` |
| `{{TARGET}}` | model `<config>`, global batch `<b>`, sequence length `<s>`, precision `<bf16>` |
| `{{OPERATING_POINTS}}` | the target above; one point |
| `{{WORLD_SIZE}}` | `<n>` GPUs |
| `{{PHASES}}` | data load wait, forward, backward (with the all-reduce it hides), exposed all-reduce, optimizer step |
| `{{METRIC_NAME}}` | step time, ms, lower is better (mean of steps 50-150) |
| `{{METRIC_COMMAND}}` | `torchrun --nproc-per-node <n> train.py <args>`, 150 steps, step times logged to a file |
| `{{GATE_NAME}}` | loss after 500 steps from a fixed checkpoint and seed, lower is better |
| `{{GATE_COMMAND}}` | `torchrun ... train.py <args>`, resumed from `<ckpt>`, seed 0, 500 steps, final loss logged |
| `{{SCOPE}}` | config and flags, kernels, model code, communication settings |
| `{{BUILD}}` | git worktree and a new venv |
| `{{BOUND_HINTS}}` | step FLOPs (6 x params x tokens) / peak, plus gradient all-reduce bytes / link bandwidth when it cannot overlap |
