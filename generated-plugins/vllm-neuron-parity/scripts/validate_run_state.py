#!/usr/bin/env python3
"""Validate a vllm-neuron-parity run's persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>

The schema is the single shape authority for run state; this script only
applies it. Validation uses the jsonschema package when importable. A
dependency-free Draft 7 subset validator covers every keyword used by this
schema on a bare python3. The graph validator scripts/validate_pave.py keeps
its separate fail-closed dependency contract.

Exit codes: 0 valid, 1 invalid, 2 usage or schema error.
"""

import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "run-state.schema.json"
SUPPORTED_SCHEMA_KEYS = {
    "$id",
    "$schema",
    "additionalProperties",
    "description",
    "enum",
    "items",
    "maximum",
    "minLength",
    "minimum",
    "oneOf",
    "properties",
    "required",
    "title",
    "type",
}


def _type_matches(value, expected):
    checks = {
        "array": lambda item: isinstance(item, list),
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "null": lambda item: item is None,
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "string": lambda item: isinstance(item, str),
    }
    return expected in checks and checks[expected](value)


def _stdlib_validate(value, schema, path="<root>"):
    """Validate the Draft 7 keyword subset used by run-state.schema.json."""
    problems = []
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYS
    if unsupported:
        rendered = ", ".join(sorted(unsupported))
        return [f"{path}: validator does not support schema keyword(s): {rendered}"]
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        matches = [not _stdlib_validate(value, branch, path) for branch in branches]
        if sum(matches) != 1:
            problems.append(f"{path}: does not match exactly one allowed shape")
        return problems

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected is not None and not any(
        _type_matches(value, item) for item in expected_types
    ):
        rendered = ", ".join(str(item) for item in expected_types)
        return [f"{path}: expected type {rendered}"]

    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: value is not in the allowed enum")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(value) < minimum:
            problems.append(f"{path}: string is shorter than {minimum}")

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            problems.append(f"{path}: value is less than {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            problems.append(f"{path}: value is greater than {maximum}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            problems.extend(_stdlib_validate(item, schema["items"], f"{path}/{index}"))

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        problems.extend(
            f"{path}: missing required field: {key}"
            for key in required
            if key not in value
        )
        extras = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False:
            problems.extend(f"{path}: undeclared field: {key}" for key in sorted(extras))
        elif isinstance(additional, dict):
            for key in sorted(extras):
                problems.extend(
                    _stdlib_validate(value[key], additional, f"{path}/{key}")
                )
        for key in sorted(set(value) & set(properties)):
            problems.extend(
                _stdlib_validate(value[key], properties[key], f"{path}/{key}")
            )
    return problems


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

    problems = []

    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        problems.extend(
            f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
            for error in validator.iter_errors(state)
        )
        mode = "full (jsonschema)"
    except ImportError:
        problems.extend(_stdlib_validate(state, schema))
        mode = "full schema subset (stdlib)"

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
