"""Static contract for AMMO's automatic CUDA/Neuron role routing.

The runtime binding is additive.  These checks pin the automatic selection,
the serving-level adapter, and the role-to-skill map without changing AMMO's
stage, gate, verdict, or stop-policy authorities.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# A Neuron-path contract: v1 ships pumice-cuda only, and conventions §6
# deselects `neuron` by default.
pytestmark = pytest.mark.neuron


# The plugin root. It ships the Claude dialect: role prompts in agents/, the
# lead skill in skills/ammo/ (the Codex roles are TOML under codex/agents/).
AMMO = Path(__file__).resolve().parents[1]
SKILL = AMMO / "skills" / "ammo" / "SKILL.md"
ROLE_ROOT = AMMO / "agents"
ROLE_FILES = {
    "researcher": "ammo-researcher.md",
    "champion": "ammo-champion.md",
    "implementer": "ammo-impl-champion.md",
    "monitor": "ammo-transcript-monitor.md",
    "auditor": "ammo-auditor.md",
    "delegate": "ammo-delegate.md",
}
# The vendored Neuron skills a Neuron campaign may name. Prompt text may name
# only these (the server's NEURON_CAMPAIGN_SKILL_DIRS list).
SHIPPED_NEURON_SKILLS = {
    "neuron-nki-docs",
    "neuron-nki-writing",
    "neuron-nki-debugging",
    "neuron-nki-profiling",
    "neuron-nki-profile-querying",
    "neuron-explorer-profile-schema",
    "experimental-neuron-autoport-compiler-debugging-vllm-neuron",
    "experimental-neuron-hardware-entitlement",
    "experimental-neuron-infra-runtime-troubleshooting",
    "experimental-neuron-memory-system",
    "experimental-neuron-parallelism-planner",
    "experimental-neuron-perf-compiler-artifact-debugging",
}
BINDING_SECTIONS = (
    "## Researcher",
    "## Champion",
    "## Implementer",
    "## Monitor",
    "## Auditor",
    "## Delegate",
)
# Vendored skill names only. `neuron-explorer` alone is the vendor tool.
_SKILL_NAME = re.compile(
    r"`(neuron-nki-[a-z-]+|neuron-explorer-profile-schema|experimental-neuron-[a-z0-9-]+)`"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _role(name: str) -> str:
    return _text(ROLE_ROOT / ROLE_FILES[name])


def _binding() -> str:
    return _text(AMMO / "references" / "accelerator-binding.md")


# R1: every role file carries this one line and no other Neuron routing.
NEURON_POINTER = (
    "On Neuron, which v1 does not run, follow "
    "`references/accelerator-binding.md` § {section}."
)
ROLE_SECTIONS = {
    "researcher": "Researcher",
    "champion": "Champion",
    "implementer": "Implementer",
    "monitor": "Monitor",
    "auditor": "Auditor",
    "delegate": "Delegate",
}


def test_single_entrypoint_selects_the_server_runtime_automatically() -> None:
    entry = _text(SKILL)
    assert "AMMO_RUNTIME_PROFILE" in entry
    assert "Do not ask the user to choose a backend" in entry
    assert "`vllm bench serve` is diagnostic only" in entry
    assert "profiler-free" in entry
    # The legacy names and the runtime adapter live in the binding (R1).
    binding = _binding()
    assert "`vllm-cuda`" in binding
    assert "`vllm-neuron`" in binding
    assert "scripts/runtime_ops.py" in binding


def test_cuda_profiler_contract_remains_explicit() -> None:
    corpus = "\n".join(
        [_text(SKILL)] + [_role(name) for name in ROLE_FILES]
    )
    for marker in ("Nsys", "NCU", "CUDA graphs"):
        assert marker in corpus
    assert "vllm-cuda" in _binding()


def test_neuron_roles_route_through_the_serving_adapter() -> None:
    assert (AMMO / "references" / "accelerator-binding.md").is_file()
    for name, section in ROLE_SECTIONS.items():
        assert NEURON_POINTER.format(section=section) in _role(name), name


def test_all_six_vendored_skills_are_reachable_from_existing_roles() -> None:
    # The delegate points to its binding section, which names the six skills.
    corpus = _binding().split("## Delegate", 1)[1]
    required = {
        "neuron-nki-docs",
        "neuron-nki-writing",
        "neuron-nki-debugging",
        "neuron-nki-profiling",
        "neuron-nki-profile-querying",
        "neuron-explorer-profile-schema",
    }
    assert required <= {name for name in required if name in corpus}


def test_binding_has_a_section_for_every_neuron_role() -> None:
    binding = _text(AMMO / "references" / "accelerator-binding.md")
    for heading in BINDING_SECTIONS:
        assert heading in binding, heading
    # Each section names at least one shipped skill, so a role that reads its
    # section learns which skill to load.
    for start, end in zip(BINDING_SECTIONS, BINDING_SECTIONS[1:] + ("\x00",)):
        section = binding.split(start, 1)[1].split(end, 1)[0]
        assert _SKILL_NAME.search(section), start


def test_binding_says_how_to_load_a_skill() -> None:
    binding = _text(AMMO / "references" / "accelerator-binding.md")
    assert "Skill tool" in binding
    assert "`$<name>`" in binding
    # The server's session setup that pruned vendored Neuron skills is gone
    # (conventions §8), so the binding no longer states it.


def test_prompt_text_names_only_shipped_neuron_skills() -> None:
    binding = _text(AMMO / "references" / "accelerator-binding.md")
    corpus = "\n".join([binding, _text(SKILL)] + [_role(name) for name in ROLE_FILES])
    named = set(_SKILL_NAME.findall(corpus))
    assert named <= SHIPPED_NEURON_SKILLS, sorted(named - SHIPPED_NEURON_SKILLS)
    # Every shipped skill is routed somewhere in the binding, so nothing ships
    # that no role is told about.
    assert set(_SKILL_NAME.findall(binding)) == SHIPPED_NEURON_SKILLS


def test_champion_and_monitor_route_to_their_binding_sections() -> None:
    binding = _binding()
    champion = binding.split("## Champion", 1)[1].split("## Implementer", 1)[0]
    monitor = binding.split("## Monitor", 1)[1].split("## Auditor", 1)[0]
    assert "Reuse the Stage 1 profile" in champion
    assert "A champion who cites Nsys has the wrong runtime" in monitor
    assert "Nsys, NCU, and CUDA graphs do not exist on Neuron" in binding


def test_vendored_neuron_guidance_cannot_override_ammo_policy() -> None:
    entry = _text(SKILL)
    binding = _binding()
    for marker in (
        "seven stages",
        "five binding gates",
        "GSM8K",
        "end-to-end speedup",
        "verdict",
        "stop logic",
    ):
        assert marker in entry
    assert "AMMO's target, reservation, environment, and validation contracts win" in binding
    assert "must not select cores, change LNC, create a venv, or install packages" in binding


def test_neuron_profiling_stays_separate_from_authoritative_timing() -> None:
    researcher = _role("researcher")
    implementer = _role("implementer")
    binding = _binding()
    assert "scripts/run_vllm_bench_latency_sweep.py --accelerator-profile" not in researcher
    assert "Use `scripts/run_pumice_grpc_bench.py` for every AMMO baseline" in binding
    assert "`scripts/runtime_ops.py capture-neff" in binding
    assert "use `--accelerator-profile`" not in binding
    assert "`scripts/runtime_ops.py` with `--accelerator-profile`" not in researcher
    assert "NEFF/bucket" in binding
    assert "absence of fallback" in binding
    assert "Profiled latency" in implementer
    assert "never enters the E2E verdict" in implementer


def test_higher_authority_references_bind_each_runtime_without_new_gates() -> None:
    validation = _text(AMMO / "references" / "validation-defaults.md")
    binding = _binding()

    # Legacy names stay only in the binding and the scripts (R1).
    assert "vllm-cuda" in binding
    assert "vllm-neuron" in binding

    assert "CUDA graphs" in validation
    # Neuron gate evidence, reservation, and timing authority (moved from
    # validation-defaults.md, audit-invariants.md, and gpu-pool.md).
    assert "expected NEFF/bucket" in binding
    assert "absence of fallback" in binding
    assert "plugin/NEFF-bucket compiled boundary" in binding
    assert "authoritative profiler-free E2E timing" in binding
    assert "NEURON_VISIBLE_DEVICES" in binding
    assert "NEURON_RT_VISIBLE_CORES" in binding
    assert "one logical NeuronCore" in binding
    assert "Do not ask the user to choose a backend" in binding
    assert "report an LNC error to the lead" in binding
    # The scaffold facts moved from workload-invocation.md.
    assert "llama3, gpt_oss, qwen3_moe, and qwen3_vl" in binding
    assert "kv_segment_size=8192 is not supported" in binding


def test_role_guidance_does_not_apply_cuda_evidence_rules_to_neuron() -> None:
    researcher = _role("researcher")
    binding = _binding()

    # Stage 1 takes CUDA commands with no runtime qualifier; the Neuron route
    # is the binding's standalone neuron-explorer chain.
    assert "Take the commands from `references/nsys-profiling-guide.md` § Required Stage 1 Commands" in researcher
    assert "On `vllm-neuron`, use `--accelerator-profile` for NeuronProfiler" not in researcher
    assert "Attribution on Neuron is a standalone `neuron-explorer` chain, not a runner flag" in binding
    assert "`scripts/runtime_ops.py normalize`" in binding
    # Stage 2 mining: the Neuron clauses moved to the binding (R1).
    assert "On `vllm-neuron`, query NeuronProfiler/neuron-explorer" not in researcher
    assert "Use this template on `vllm-cuda`" not in researcher
    assert "replace `SM generation` with the NeuronCore generation" in binding
    assert "`neuron-explorer-profile-schema` for version-matched fields and units" in binding
