# experimental-neuron-framework-benchmark-vllm — tests

This directory (`skills/experimental-neuron-framework-benchmark-vllm/tests/`) holds the
skill's pytest unit tests, co-located inside the skill so it stays self-contained. The
AIM agent-benchmark scenarios that used to live alongside them have moved to
NeuronAgenticDevelopmentTests (see section 2).

## 1. pytest unit tests (`test_*.py`, `conftest.py`) — outside the default build

Tier-1 unit tests for the deliverables scripts' pure logic (parsing, labeling,
tokens/$ math). They need `pandas`, which the Brazil build fleet does not have, so
they sit outside `pyproject.toml`'s `testpaths` and are **not** collected
by `brazil-build release`. Run them manually against a venv with pandas:

```
pip install pandas tabulate pytest
python -m pytest skills/experimental-neuron-framework-benchmark-vllm/tests -q
```

This mirrors the `neuron-parallelism-planner` skill, which likewise keeps
its unit tests inside the skill dir and out of the default build (same reason).

## 2. AIM agent-benchmark scenarios — moved to NeuronAgenticDevelopmentTests

The AIM agent-benchmark scenario (`bench-config-generation.json`, the four
`bench-config-*` config-generation invariants for the
`experimental-neuron-framework-benchmark-vllm-agent`) **no longer lives here**. It was
migrated to the **NeuronAgenticDevelopmentTests** package, alongside the other agent
evals, so it runs through `asbx-benchmark` with the Pass@1 >= 0.8 gate instead of only
gating a local `brazil-build benchmark` (the CI build fleet has no `kiro-cli`). This
matches the package convention: *AIM benchmark scenarios live in NeuronAgenticDevelopmentTests;
a skill's own pytest unit tests live inside the skill dir in NeuronAgenticDevelopment.*

To run or edit those scenarios, see `NeuronAgenticDevelopmentTests`:
`tests/experimental-neuron-framework-benchmark-vllm-agent/bench-config-generation.json`,
registered in that package's `tests/registry.json` and `AGENT_SUBSETS`. Run with
`brazil-build integ --addopts="-k benchmark-vllm"` from that package (needs an AL2023
host with `node` + `kiro-cli`).
