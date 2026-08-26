#!/usr/bin/env python3
"""Test that workflow.pave.yaml passes scripts/validate_pave.py.

Run from anywhere:
  uv run --no-project --with jsonschema --with pyyaml python \
    plugins/vllm-neuron-parity/tests/test_workflow_pave.py

Checks: the canonical graph validates (exit 0) and reports the approved
topology - 31 nodes, 89 edges, 5 control endpoints. A change in any of those
counts means the graph changed; the graph's meaning is frozen, so a count
change is a finding, not a test update.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_pave.py"
GRAPH = ROOT / "workflow.pave.yaml"
EXPECTED = "31 nodes, 89 edges, 5 control endpoints"


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), str(GRAPH)],
        capture_output=True,
        text=True,
    )
    output = (proc.stdout + proc.stderr).strip()
    for line in output.splitlines():
        print(f"      {line}")

    problems = []
    if proc.returncode != 0:
        problems.append(f"validate_pave.py exit {proc.returncode}, expected 0")
    if EXPECTED not in output:
        problems.append(f"expected topology '{EXPECTED}' not reported")

    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASS: workflow.pave.yaml validates with {EXPECTED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
