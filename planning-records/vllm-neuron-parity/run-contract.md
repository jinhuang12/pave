# Run contract — vllm-neuron-parity (pave-init run 2026-08-25)

## Goal as invoked

`/pave-init:pave-init vllm-neuron-parity-goal-brief.md` — build one PAVE workflow
plugin that brings the vLLM-Neuron platform plugin
(/Users/jinhun/GitHub/vllm-neuron) to parity with upstream vLLM on NVIDIA GPUs:
gap scan → per-target route analysis (backport default / pin-upgrade fallback) →
ranked backlog → user-gated campaigns (feature ports, model ports) → correctness
+ perf gates against a GPU baseline → evidence-backed PRs on the jinhuang12 fork.
Authority for requirements: the brief at
`/Users/jinhun/GitHub/NeuronAgenticDevelopment/vllm-neuron-parity-goal-brief.md`
(v3, 2026-08-24, adversarially reviewed 2026-08-25).

## Workspace

`/Users/jinhun/GitHub/NeuronAgenticDevelopment/.pave/vllm-neuron-parity/`

## Output boundary

- Generated plugin: `/Users/jinhun/GitHub/NeuronAgenticDevelopment/plugins/vllm-neuron-parity/` (did not exist at initialization).
- This run writes only inside the workspace and the output path above, plus the
  repo-root `.pave-init-run` marker (git-excluded).
- `/Users/jinhun/GitHub/vllm-neuron` is read-only evidence for this run.
- The superseded run `.pave/vllm-neuron-feature-port/` is read-only input; its
  only mutation was the terminal `abandoned` classification recorded at
  initialization.

## Stage-1 decisions (2026-08-25)

1. Validation runtime: `uv run --no-project --with pyyaml --with jsonschema python` (approved alternate; no persistent installs).
2. Plugin name: `vllm-neuron-parity`.
3. Output location: `plugins/` in this repo.
4. Evolution tier: **evolving** (departs from the prior skill's static tier).
