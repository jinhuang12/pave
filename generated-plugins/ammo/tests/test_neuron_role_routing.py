"""Static contract for AMMO's automatic CUDA/Neuron role routing.

The runtime binding is additive.  These checks pin the automatic selection,
the serving-level adapter, and the role-to-skill map without changing AMMO's
stage, gate, verdict, or stop-policy authorities.
"""

from __future__ import annotations

import re
from pathlib import Path


AMMO = Path(__file__).resolve().parents[1]
CLI_ROOT = AMMO.parents[1]
IS_CODEX = CLI_ROOT.name == ".codex"
ROLE_ROOT = AMMO / "agents" if IS_CODEX else CLI_ROOT / "agents"
ROLE_FILES = {
    "researcher": "ammo-researcher.md",
    "champion": "ammo-champion.md",
    "implementer": "ammo-implementer.md" if IS_CODEX else "ammo-impl-champion.md",
    "monitor": "ammo-transcript-monitor.md",
    "investigator": "ammo-investigator.md",
    "auditor": "ammo-auditor.md",
    "delegate": "ammo-delegate.md",
}
# The vendored Neuron skills a Neuron session ships. Session setup removes every
# other vendored Neuron skill (orchestration/cli_tool_manager.py
# NEURON_CAMPAIGN_SKILL_DIRS), so prompt text may name only these.
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
    "## Investigator",
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


def test_single_entrypoint_selects_the_server_runtime_automatically() -> None:
    entry = _text(AMMO / "SKILL.md")
    assert "AMMO_RUNTIME_PROFILE" in entry
    assert "`vllm-cuda`" in entry
    assert "`vllm-neuron`" in entry
    assert "Do not ask the user to choose a backend" in entry
    assert "scripts/runtime_ops.py" in entry
    assert "`vllm bench serve` is diagnostic only" in entry
    assert "profiler-free" in entry


def test_cuda_profiler_contract_remains_explicit() -> None:
    corpus = "\n".join(
        [_text(AMMO / "SKILL.md")] + [_role(name) for name in ROLE_FILES]
    )
    for marker in ("Nsys", "NCU", "CUDA graphs", "vllm-cuda"):
        assert marker in corpus


def test_neuron_roles_route_through_the_serving_adapter() -> None:
    assert (AMMO / "references" / "accelerator-binding.md").is_file()
    for name in ROLE_FILES:
        text = _role(name)
        assert "vllm-neuron" in text, name
        assert "scripts/runtime_ops.py" in text, name


def test_all_six_vendored_skills_are_reachable_from_existing_roles() -> None:
    corpus = "\n".join(_role(name) for name in ROLE_FILES)
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
    assert "Session setup removes the other vendored Neuron skills" in binding


def test_prompt_text_names_only_shipped_neuron_skills() -> None:
    binding = _text(AMMO / "references" / "accelerator-binding.md")
    corpus = "\n".join([binding, _text(AMMO / "SKILL.md")] + [_role(name) for name in ROLE_FILES])
    named = set(_SKILL_NAME.findall(corpus))
    assert named <= SHIPPED_NEURON_SKILLS, sorted(named - SHIPPED_NEURON_SKILLS)
    # Every shipped skill is routed somewhere in the binding, so nothing ships
    # that no role is told about.
    assert set(_SKILL_NAME.findall(binding)) == SHIPPED_NEURON_SKILLS


def test_champion_and_monitor_route_to_their_binding_sections() -> None:
    champion = _role("champion")
    monitor = _role("monitor")
    assert "`references/accelerator-binding.md` § Champion" in champion
    assert "load the Neuron skills it names" in champion
    assert "§ Monitor" in monitor
    assert "load the Neuron skills it names" in monitor
    assert "Nsys does not exist" in monitor


def test_vendored_neuron_guidance_cannot_override_ammo_policy() -> None:
    entry = _text(AMMO / "SKILL.md")
    implementer = _role("implementer")
    for marker in (
        "seven stages",
        "five binding gates",
        "GSM8K",
        "end-to-end speedup",
        "verdict",
        "stop logic",
    ):
        assert marker in entry
    assert "AMMO's target, reservation, environment, and validation contracts win" in implementer
    assert "must not select cores, change LNC, create a venv, or install packages" in implementer


def test_neuron_profiling_stays_separate_from_authoritative_timing() -> None:
    researcher = _role("researcher")
    implementer = _role("implementer")
    binding = _text(AMMO / "references" / "accelerator-binding.md")
    assert "scripts/run_vllm_bench_latency_sweep.py --accelerator-profile" not in researcher
    assert "scripts/run_pumice_grpc_bench.py` for timing" in researcher
    assert "`scripts/runtime_ops.py capture-neff" in binding
    assert "use `--accelerator-profile`" not in binding
    assert "`scripts/runtime_ops.py` with `--accelerator-profile`" not in researcher
    assert "NEFF/bucket" in binding
    assert "absence of fallback" in binding
    assert "Profiled latency" in implementer
    assert "never enters the E2E verdict" in implementer


def test_higher_authority_references_bind_each_runtime_without_new_gates() -> None:
    validation = _text(AMMO / "references" / "validation-defaults.md")
    audit = _text(AMMO / "references" / "audit-invariants.md")
    pool = _text(AMMO / "references" / "gpu-pool.md")

    for document in (validation, audit, pool):
        assert "vllm-cuda" in document
        assert "vllm-neuron" in document

    assert "CUDA graphs" in validation
    assert "expected NEFF/bucket" in validation
    assert "absence of fallback" in validation
    assert "profiler-free timing authority" in audit
    assert "NEURON_VISIBLE_DEVICES" in pool
    assert "NEURON_RT_VISIBLE_CORES" in pool
    assert "do not select a backend or change LNC" in pool


def test_role_guidance_does_not_apply_cuda_evidence_rules_to_neuron() -> None:
    researcher = _role("researcher")
    implementer = _role("implementer")
    delegate = _role("delegate")

    assert "On `vllm-cuda`, take commands from `references/nsys-profiling-guide.md`" in researcher
    assert "On `vllm-neuron`, use `--accelerator-profile` for NeuronProfiler" not in researcher
    assert "On `vllm-neuron`, the runner has no profiler flag" in researcher
    assert "On `vllm-neuron`, query NeuronProfiler/neuron-explorer" in researcher
    assert "Use this template on `vllm-cuda`; on `vllm-neuron`" in researcher
    assert "Take the commands from `references/nsys-profiling-guide.md`" not in researcher
    assert "Match the profiler evidence to the claim. Nsys supports" not in researcher

    assert "CUDA graphs/compile behavior on `vllm-cuda`" in implementer
    assert "plugin/NEFF-bucket compiled boundary" in implementer
    assert "On `vllm-cuda`, include CUDA-graph settings" in implementer
    assert "On `vllm-neuron`, use NeuronProfiler/neuron-explorer" in implementer
    assert "Use production-parity CUDA graphs/compile behavior" not in implementer
    assert "target compile and CUDA-graph settings" not in implementer

    assert "On `vllm-cuda`, use Nsys" in delegate
    assert "On `vllm-neuron`, use NeuronProfiler/neuron-explorer" in delegate
    assert "one logical NeuronCore on Neuron" in delegate
    assert "Match the profiler to the claim: Nsys" not in delegate
    assert "for every GPU command" not in delegate
    assert "Use one GPU for kernel experiments" not in delegate
