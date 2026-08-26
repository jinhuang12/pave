#!/usr/bin/env python3
"""Validate a vllm-neuron-parity run's persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>

The schema is the single shape authority for run state; this script only
applies it. Full validation uses the jsonschema package when importable.
Without it, falls back to a stdlib check of the required keys and of the
completed_outcomes entry shape, so run state stays checkable on a bare
python3 (this must never fail closed for a missing dependency; the graph
validator scripts/validate_pave.py owns fail-closed behavior).

Exit codes: 0 valid, 1 invalid, 2 usage or schema error.
"""

import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "run-state.schema.json"


def load_schema():
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read schema {SCHEMA_PATH}: {exc}")
        return None


def validate_run_state(state_path: Path) -> int:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report any parse/IO failure
        print(f"FAIL: cannot parse {state_path}: {exc}")
        return 1

    schema = load_schema()
    if schema is None:
        return 2

    if not isinstance(state, dict):
        print("FAIL: top level is not an object")
        return 1

    problems = [
        f"missing required field: {key}"
        for key in schema.get("required", [])
        if key not in state
    ]

    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        problems.extend(
            f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
            for error in validator.iter_errors(state)
        )
        mode = "full (jsonschema)"
    except ImportError:
        declared = set(schema.get("properties", {}))
        problems.extend(
            f"undeclared field: {key}" for key in sorted(set(state) - declared)
        )
        history = state.get("completed_outcomes")
        if history is not None:
            if not isinstance(history, list):
                problems.append("completed_outcomes: not a list")
            else:
                problems.extend(
                    f"completed_outcomes[{i}]: missing node/outcome"
                    for i, entry in enumerate(history)
                    if not (isinstance(entry, dict) and entry.get("node") and entry.get("outcome"))
                )
        mode = "basic (stdlib only; install jsonschema for full validation)"

    return report(problems, mode, state_path)


def report(problems, mode, subject) -> int:
    seen = set()
    unique = [p for p in problems if not (p in seen or seen.add(p))]
    if unique:
        print(f"FAIL ({mode}): {len(unique)} problem(s) in {subject}")
        for problem in unique:
            print(f"  - {problem}")
        return 1
    print(f"PASS ({mode}): {subject}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1:
        print(__doc__.strip())
        return 2
    return validate_run_state(Path(args[0]))


if __name__ == "__main__":
    sys.exit(main())
