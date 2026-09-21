#!/usr/bin/env python3
"""Tests for scripts/validate_run_state.py against schemas/run-state.schema.json.

Run from anywhere:
  uv run --no-project --with jsonschema --with pyyaml python \
    plugins/vllm-neuron-parity/tests/test_run_state_schema.py

Checks: a minimal valid instance is accepted (exit 0); an instance missing a
required field is rejected (exit 1); an instance carrying an undeclared extra
field is rejected (exit 1).
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_run_state.py"
SCHEMA = ROOT / "schemas" / "run-state.schema.json"

MINIMAL = {
    "workflow_identity": {"run_id": "run-0001"},
    "pinned_release": None,
    "requested_targets": [],
    "instance_roster": [],
    "cross_run_artifact_refs": {},
    "ranked_backlog": None,
    "approved_campaigns": [],
    "campaign_states": {},
    "campaign_target_pins": {},
    "scheduling_holds": [],
    "comparator_registrations": {},
    "hardware_attempt_counts": {},
    "hardware_lease_record": None,
    "gate_approval_records": [],
    "active_node_runs": [],
    "completed_outcomes": [],
    "evidence_references": {},
    "open_questions": [],
    "final_status": None,
}


def run_case(label: str, instance: dict, expected: int, *, no_site: bool = False) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run-state.json"
        path.write_text(json.dumps(instance, indent=2), encoding="utf-8")
        command = [sys.executable]
        if no_site:
            command.append("-S")
        command.extend([str(VALIDATOR), str(path)])
        env = os.environ.copy()
        if no_site:
            env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
        )
    ok = proc.returncode == expected
    print(f"[{'ok' if ok else 'FAIL'}] {label}: exit {proc.returncode} (expected {expected})")
    for line in (proc.stdout + proc.stderr).strip().splitlines():
        print(f"      {line}")
    return ok


def main() -> int:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    results = []

    required = schema["required"]
    results.append(
        (
            "required list mirrors the 19 state fields",
            len(required) == 19 and len(set(required)) == 19,
        )
    )
    results.append(("additionalProperties is false", schema.get("additionalProperties") is False))
    results.append(
        (
            "minimal instance covers exactly the required keys",
            set(MINIMAL) == set(required),
        )
    )
    for label, ok in results:
        print(f"[{'ok' if ok else 'FAIL'}] {label}")

    cases = [run_case("minimal valid instance accepted", MINIMAL, 0)]
    cases.append(
        run_case(
            "stdlib accepts minimal valid instance",
            MINIMAL,
            0,
            no_site=True,
        )
    )

    missing = dict(MINIMAL)
    del missing["pinned_release"]
    cases.append(run_case("missing required field rejected", missing, 1))

    extra = dict(MINIMAL)
    extra["undeclared_field"] = "nope"
    cases.append(run_case("undeclared extra field rejected", extra, 1))

    nested_invalid = dict(MINIMAL)
    nested_invalid["workflow_identity"] = "not-an-object"
    cases.append(
        run_case(
            "stdlib rejects invalid nested type",
            nested_invalid,
            1,
            no_site=True,
        )
    )

    failures = [label for label, ok in results if not ok] + [
        "validator case" for ok in cases if not ok
    ]
    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: run-state schema and validator behave as declared")
    return 0


class RunStateSchemaChecks(unittest.TestCase):
    """So the suite runs these checks too: as a bare script this file was collected
    by no runner, and the required-field count drifted from the schema unseen."""

    def test_schema_and_validator_behave_as_declared(self) -> None:
        self.assertEqual(main(), 0)


if __name__ == "__main__":
    sys.exit(main())
