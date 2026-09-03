#!/usr/bin/env python3
"""Test that workflow.pave.yaml passes scripts/validate_pave.py.

Run from anywhere:
  uv run --no-project --with jsonschema --with pyyaml python \
    plugins/vllm-neuron-parity/tests/test_workflow_pave.py

Checks: the canonical graph validates (exit 0) and reports the approved
topology - 32 nodes, 95 edges, 5 control endpoints. A change in any of those
counts means the graph changed; the graph's meaning is frozen, so a count
change is a finding, not a test update.

Also checks that every "N nodes ... M edges" phrase in README.md, and in the
newest VERSION entry, matches the validator's counts - the rendered views
must not drift from the graph they render.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_pave.py"
GRAPH = ROOT / "workflow.pave.yaml"
EXPECTED = "32 nodes, 95 edges, 5 control endpoints"
VALIDATOR_COUNTS = re.compile(r"(\d+) nodes, (\d+) edges, (\d+) control endpoints")
DOC_COUNTS = re.compile(r"(\d+) nodes(?:,| and) (\d+) edges")


def newest_version_entry(text: str) -> str:
    """The VERSION text from the first `## <version>` heading to the second one."""
    headings = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    if len(headings) >= 2:
        return text[headings[0]:headings[1]]
    return text


def doc_count_problems(nodes: str, edges: str) -> list[str]:
    problems = []
    docs = {
        "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
        "VERSION (newest entry)": newest_version_entry(
            (ROOT / "VERSION").read_text(encoding="utf-8")
        ),
    }
    for name, text in docs.items():
        for match in DOC_COUNTS.finditer(text):
            if (match.group(1), match.group(2)) != (nodes, edges):
                problems.append(
                    f"{name} says '{match.group(0)}', validator says "
                    f"{nodes} nodes, {edges} edges"
                )
    return problems


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
    counts = VALIDATOR_COUNTS.search(output)
    if counts is None:
        problems.append("validator output carries no node/edge counts")
    else:
        problems.extend(doc_count_problems(counts.group(1), counts.group(2)))

    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"PASS: workflow.pave.yaml validates with {EXPECTED}; README and VERSION counts match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
