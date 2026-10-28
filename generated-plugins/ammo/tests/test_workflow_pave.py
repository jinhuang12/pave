"""Tests for the shipped AMMO graph, workflow.pave.yaml.

Run from the plugin root:
  uv run --no-project --with pytest --with pyyaml --with jsonschema \
    python -m pytest tests/test_workflow_pave.py -q

The graph validates with scripts/validate_pave.py and keeps the planned
topology. A change in the node set or the counts means the graph changed, so a
failure here is a finding, not a test update. The graph must also stay in step
with scripts/transitions.json, the live state machine: every stage on the
ladder is a node, every audit gate guards the stage the audit map names, and
every Stage 7 outcome routes to the stage outcome_next_stage names. README.md
and the newest VERSION entry must quote the validator's counts.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_pave.py"
GRAPH = ROOT / "workflow.pave.yaml"
TRANSITIONS = ROOT / "scripts" / "transitions.json"

EXPECTED_COUNTS = "13 nodes, 47 edges, 5 control nodes"
EXPECTED_NODES = {
    "scaffold_target",
    "preflight_engine",
    "stage_1_baseline",
    "audit_s1",
    "stage_2_bottleneck_mining",
    "audit_s2",
    "stage_3_debate",
    "stage_4_5_parallel_tracks",
    "audit_s45",
    "stage_6_integration",
    "audit_s67",
    "stage_7_campaign_eval",
    "stage_7b_report",
}
JOIN = "all_tracks_terminal"
EXPECTED_CONTROL_NODES = {
    "campaign_complete": ("terminal", "accepted"),
    "campaign_exhausted": ("terminal", "exhausted"),
    "blocked": ("terminal", "blocked"),
    "paused_for_user": ("pause", None),
    JOIN: ("join", None),
}
GATES = {
    "5.1a": "gate_5_1a",
    "5.1b": "gate_5_1b",
    "5.2": "gate_5_2",
    "5.3a": "gate_5_3a",
    "5.3b": "gate_5_3b",
}
TRACKS = "stage_4_5_parallel_tracks"
EVAL = "stage_7_campaign_eval"
VALIDATOR_COUNTS = re.compile(r"(\d+) nodes, (\d+) edges, (\d+) control nodes")
DOC_COUNTS = re.compile(r"(\d+) nodes(?:,| and) (\d+) edges")


@pytest.fixture(scope="module")
def pave():
    return yaml.safe_load(GRAPH.read_text(encoding="utf-8"))["pave"]


@pytest.fixture(scope="module")
def transitions():
    return json.loads(TRANSITIONS.read_text(encoding="utf-8"))


def node_id(stage):
    """The PAVE node id for a stage_ladder name (ids cannot start with a digit)."""
    return "stage_" + stage


def edges_from(pave, source):
    return [edge for edge in pave["edges"] if edge["from"] == source]


def destination(edge):
    target = edge["to"]
    return target["fan_out"] if isinstance(target, dict) else target


def test_validator_passes_with_planned_counts():
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(GRAPH)],
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, output
    assert EXPECTED_COUNTS in output, output


def test_node_ids_are_exactly_the_planned_set(pave):
    assert set(pave["nodes"]) == EXPECTED_NODES
    assert pave["entrypoints"] == ["scaffold_target"]


def test_control_nodes_and_their_status(pave):
    endpoints = pave["control_nodes"]
    assert set(endpoints) == set(EXPECTED_CONTROL_NODES)
    for name, (kind, status) in EXPECTED_CONTROL_NODES.items():
        assert endpoints[name]["kind"] == kind, name
        assert endpoints[name].get("terminal_status") == status, name
    assert endpoints["paused_for_user"]["resumable"] is True


def test_join_sits_before_integration(pave, transitions):
    assert pave["control_nodes"][JOIN]["resume_at"] == "audit_s45"
    for status in transitions["track_terminal_statuses"]:
        routes = {destination(e) for e in edges_from(pave, f"{TRACKS}.{status.lower()}")}
        assert routes == {JOIN}, status
    into_integration = {
        e["from"] for e in pave["edges"] if destination(e) == "stage_6_integration"
    }
    assert "audit_s45.pass" in into_integration


def test_stage_nodes_cover_the_transitions_ladder(pave, transitions):
    ladder = transitions["stage_ladder"]
    assert ladder, "stage_ladder is empty"
    for stage in ladder:
        assert node_id(stage) in pave["nodes"], stage
    assert pave["extensions"]["ammo"]["stage_nodes"] == {s: node_id(s) for s in ladder}


def test_terminal_statuses_are_terminal_endpoints(pave, transitions):
    for status in transitions["terminal_statuses"]:
        assert pave["control_nodes"][status]["kind"] == "terminal", status
        assert status in pave["nodes"][EVAL]["outcomes"], status


def test_audits_guard_the_stages_the_audit_map_names(pave, transitions):
    audit_nodes = pave["extensions"]["ammo"]["audit_nodes"]
    for stage, key in transitions["audit_stage_key_map"].items():
        audit = audit_nodes[key]
        assert audit in pave["nodes"], key
        routes = {destination(e) for e in edges_from(pave, f"{audit}.pass")}
        assert node_id(stage) in routes, (stage, key)


def test_stage_7_outcomes_route_per_outcome_next_stage(pave, transitions):
    for token, stage in transitions["outcome_next_stage"].items():
        routes = {destination(e) for e in edges_from(pave, f"{EVAL}.{token.lower()}")}
        assert routes == {node_id(stage)}, token


def test_five_binding_gates_guard_every_passing_track(pave):
    assert pave["extensions"]["ammo"]["binding_gates"] == GATES
    for outcome in ("pass", "gated_pass"):
        (edge,) = edges_from(pave, f"{TRACKS}.{outcome}")
        assert set(GATES.values()) <= set(edge["checks"]), outcome
    for check in GATES.values():
        assert pave["checks"][check]["on_failure_route"] == TRACKS, check


def test_diluted_pass_is_pass_with_a_flag(pave):
    outcomes = pave["nodes"][TRACKS]["outcomes"]
    assert "diluted_pass" not in outcomes
    schema = outcomes["pass"]["data_schema"]
    assert schema["properties"]["diluted"]["type"] == "boolean"
    assert "DILUTED_PASS" in outcomes["pass"]["meaning"]


def test_stop_rule_is_a_guard(pave):
    for outcome in ("campaign_complete", "campaign_exhausted"):
        (edge,) = edges_from(pave, f"{EVAL}.{outcome}")
        assert "stop_rule_met" in edge["checks"], outcome
    (keep_going,) = edges_from(pave, f"{EVAL}.exhausted")
    assert "stop_rule_not_met" in keep_going["checks"]
    for check in ("stop_rule_met", "stop_rule_not_met"):
        question = pave["checks"][check]["question"]
        assert "top_addressable_e2e_pct" in question, check
        assert "min_e2e_improvement_pct" in question, check


def test_outcome_tokens_match_the_source_names(pave):
    for source, token in pave["extensions"]["ammo"]["outcome_tokens"].items():
        node, _, outcome = source.partition(".")
        assert outcome == token.lower(), source
        outcome_body = pave["nodes"][node]["outcomes"][outcome]
        assert token in outcome_body["meaning"], source


def newest_version_entry(text):
    """The VERSION text from the first `## ` heading to the second one."""
    headings = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    if len(headings) >= 2:
        return text[headings[0]:headings[1]]
    return text[headings[0]:] if headings else text


def test_readme_and_version_quote_the_validator_counts():
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(GRAPH)],
        capture_output=True,
        text=True,
    )
    counts = VALIDATOR_COUNTS.search(proc.stdout)
    assert counts, proc.stdout + proc.stderr
    nodes, edges = counts.group(1), counts.group(2)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert counts.group(0) in readme, "README.md must quote the validator's counts line"
    docs = {
        "README.md": readme,
        "VERSION (newest entry)": newest_version_entry(
            (ROOT / "VERSION").read_text(encoding="utf-8")
        ),
    }
    for name, text in docs.items():
        found = DOC_COUNTS.findall(text)
        assert found, f"{name} quotes no node and edge counts"
        for pair in found:
            assert pair == (nodes, edges), f"{name} says {pair}, validator says {(nodes, edges)}"
