#!/usr/bin/env python3
"""Validation fixture and semantic diff for orchestrated Stage 2 mining.

Loads the live ammo workflow.pave.yaml, applies the Stage 2 changes in place,
and writes two files next to this script:

  workflow.parent-draft.pave.yaml   the modified graph, for validate_pave.py
                                    (it resolves the child graph file)
  stage2.semantic.patch             unified diff of the unmodified and the
                                    modified graph through the same dumper,
                                    so only the meaning changes show

The landing patch (history/v2.patch) is written against the live file text by
the implementation wave through /pave-evolve; this script is not that patch.

Usage:
    python3 make_parent_draft.py [--plugin-root PATH]
"""
from __future__ import annotations

import argparse
import copy
import difflib
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE.parents[3] / "generated-plugins" / "ammo"
CHILD_FILE = "workflow.stage2-mining.pave.yaml"
STAGE = "stage_2_bottleneck_mining"
AUDIT = "audit_s2"


def build(parent: dict) -> dict:
    g = copy.deepcopy(parent)
    p = g["pave"]

    node = p["nodes"][STAGE]
    node["purpose"] = (
        f"Stage 2 as a composed node: the lead opens {CHILD_FILE} and runs it "
        "(the family wave through the pave-init orchestrate skill, the other "
        "seats the normal AMMO way); T_AUDIT_S2 runs inside the child. In "
        "round 1 the evidence is the Stage 1 baseline and attribution; after a "
        "material SHIP, the prior round's Stage 6 integration result plus the "
        "attribution that still applies; after invalidated mining, the same "
        "measured baseline. No recapture. The lead writes the mining enrichment "
        "to state.json from the child's accepted terminal."
    )
    node["outcomes"] = {
        "pass": {
            "meaning": "The child reached mining_accepted: the Stage 2 verdict "
            "file holds Overall: PASS.",
            "required_evidence": ["bottleneck_analysis", "audit_verdict"],
        },
        "escalated": {
            "meaning": "The child reached mining_escalated: blockers remain at "
            "the audit cycle cap; campaign-scoped pause.",
            "required_evidence": ["audit_verdict"],
        },
        "user_authority_needed": {
            "meaning": "The child reached mining_needs_user.",
            "required_evidence": ["blocker_record"],
        },
    }
    del p["nodes"][AUDIT]

    def mentions_audit(e: dict) -> bool:
        return AUDIT in str(e.get("from")) or AUDIT in str(e.get("to"))

    kept = []
    for e in p["edges"]:
        if e.get("id") == "mining_retry":
            continue  # the child owns the retry loop
        if not mentions_audit(e):
            kept.append(e)
            continue
        if e["from"] in (f"{AUDIT}.pass", f"{AUDIT}.escalated"):
            outcome = e["from"].split(".")[1]
            e["from"] = f"{STAGE}.{outcome}"
            e["id"] = e["id"].replace(AUDIT, "mining")
            if outcome == "pass":
                e["checks"] = ["mining_enrichment_recorded"] + e["checks"]
            kept.append(e)
        # mining_to_audit_s2, blocked, investigate: the child owns them
    p["edges"] = kept

    chk = p["checks"]["audit_s2_passed"]
    chk["question"] += (
        " At Stage 2 the stamp comes from the auditor dispatched inside the "
        "child run; the parent reads it the same way."
    )
    chk["on_failure_route"] = STAGE

    av = p["evidence"]["audit_verdict"]
    av["produced_by"] = [STAGE if n == AUDIT else n for n in av["produced_by"]]
    av["subject"] += " For Stage 2 the file is produced inside the child run and exported."

    ext = p["extensions"]
    ext["required"] = ["ammo", "composition"]
    ext["composition"] = {
        "version": "1.0.0",
        "implementations": {
            STAGE: {
                "kind": "child_graph_file",
                "graph_file": CHILD_FILE,
                "inputs": {
                    "round": "state.campaign_state.campaign.current_round",
                    "parent_node_run": f"state.active_node_runs.{STAGE}",
                },
                "evidence_exports": [
                    {"child": "bottleneck_analysis", "parent": "bottleneck_analysis"},
                    {"child": "audit_verdict", "parent": "audit_verdict"},
                    {"child": "mining_blocker", "parent": "blocker_record"},
                ],
                "child_outcome_map": {
                    "mining_accepted": "pass",
                    "mining_escalated": "escalated",
                    "mining_needs_user": "user_authority_needed",
                },
            }
        },
    }
    ammo = ext["ammo"]
    ammo["audit_nodes"]["stage_2"] = STAGE
    tokens = ammo["outcome_tokens"]
    for k in [k for k in tokens if k.startswith(f"{AUDIT}.")]:
        del tokens[k]
    tokens[f"{STAGE}.pass"] = "PASS"

    dangling = [e["id"] for e in p["edges"] if mentions_audit(e)]
    dangling += [k for k in tokens if k.startswith(f"{AUDIT}.")]
    dangling += [n for n in av["produced_by"] if n == AUDIT]
    dangling += [v for v in ammo["audit_nodes"].values() if v == AUDIT]
    assert AUDIT not in p["nodes"] and not dangling, f"dangling {AUDIT}: {dangling}"
    return g


def dump(g: dict) -> str:
    return yaml.safe_dump(g, sort_keys=False, width=110, allow_unicode=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plugin-root", type=Path, default=DEFAULT_ROOT)
    args = ap.parse_args()
    parent = yaml.safe_load((args.plugin_root / "workflow.pave.yaml").read_text())
    before, after = dump(parent), dump(build(parent))
    (HERE / "workflow.parent-draft.pave.yaml").write_text(after)
    patch = difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        "a/workflow.pave.yaml", "b/workflow.pave.yaml",
    )
    (HERE / "stage2.semantic.patch").write_text("".join(patch))
    print("wrote workflow.parent-draft.pave.yaml and stage2.semantic.patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
