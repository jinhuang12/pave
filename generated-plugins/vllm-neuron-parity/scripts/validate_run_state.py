#!/usr/bin/env python3
"""Validate a vllm-neuron-parity run's persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>

The schema is the single shape authority for run state; this script only
applies it. Validation uses the jsonschema package when importable. A
dependency-free Draft 7 subset validator covers every keyword used by this
schema on a bare python3. A schema keyword the subset does not implement is a
loud failure (exit 1), never a silent pass. The graph validator
scripts/validate_pave.py keeps its separate fail-closed dependency contract.

Length caps (pave-spec section 8.1) - OBSERVING rung this release:
  Every per-entry free-text field in the schema declares a maxLength. In both
  modes a cap violation is a WARNING, not an error: the validator prints one
  "WARN: <json path>: <n> chars > cap <cap>" line per violation and still
  exits 0. Top-level `notes` is the one unconstrained escape hatch; it is
  counted by a whole-file warn threshold (WHOLE_FILE_WARN_BYTES) instead.
  Flip caps to errors in the next version once a run starts clean.

Path-typed fields (artifact paths and directory pointers) are resolved
against the project root - the parent of the artifacts/ directory that holds
the state file - and a target that resolves nowhere is a WARNING with the fix
named (fix the pointer or land the artifact). Evidence list entries are only
resolved when they look like paths; evidence keys, prose, and URLs are left
alone.

Exit codes: 0 valid (warnings allowed), 1 invalid, 2 usage or schema error.
"""

import json
import re
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
    "maxLength",
    "maximum",
    "minLength",
    "minimum",
    "oneOf",
    "properties",
    "required",
    "title",
    "type",
}

# Whole-file warn threshold for the schema's declared escape hatch (`notes`).
# Mirrors pave-init (pave-spec section 8.1). Warns only; never refuses a write.
WHOLE_FILE_WARN_BYTES = 131072

# Cross-run refs the schema declares may legitimately be absent on disk.
OPTIONAL_CROSS_RUN_REFS = {"failure_fingerprints"}

_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


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


def _cap_warning(path, length, cap):
    return f"{path}: {length} chars > cap {cap}"


def _unsupported_keywords(schema, path):
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYS
    if not unsupported:
        return []
    rendered = ", ".join(sorted(unsupported))
    return [f"{path}: validator does not support schema keyword(s): {rendered}"]


def _scan_schema_keywords(schema, path="<root>"):
    """Report every schema keyword the stdlib subset does not implement.

    Walks the whole schema tree, not only the branches the instance visits,
    so an unenforceable keyword is loud even when no entry carries that
    field yet (pave-spec section 8.1: announced, never a silent pass).
    """
    if not isinstance(schema, dict):
        return []
    problems = _unsupported_keywords(schema, path)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for key, sub in properties.items():
            problems.extend(_scan_schema_keywords(sub, f"{path}/{key}"))
    if isinstance(schema.get("items"), dict):
        problems.extend(_scan_schema_keywords(schema["items"], f"{path}/[]"))
    if isinstance(schema.get("additionalProperties"), dict):
        problems.extend(_scan_schema_keywords(schema["additionalProperties"], f"{path}/*"))
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        for i, branch in enumerate(branches):
            problems.extend(_scan_schema_keywords(branch, f"{path}<oneOf{i}>"))
    return problems


def _stdlib_validate(value, schema, path="<root>", warnings=None):
    """Validate the Draft 7 keyword subset used by run-state.schema.json.

    Returns the list of problems (errors). maxLength violations are appended
    to `warnings` instead, so a cap overflow never becomes an exit 1 on the
    observing rung.
    """
    if warnings is None:
        warnings = []
    problems = _unsupported_keywords(schema, path)
    if problems:
        return problems
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        matched = []
        for branch in branches:
            branch_warnings = []
            if not _stdlib_validate(value, branch, path, branch_warnings):
                matched.append(branch_warnings)
        if len(matched) != 1:
            problems.append(f"{path}: does not match exactly one allowed shape")
        else:
            warnings.extend(matched[0])
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
        cap = schema.get("maxLength")
        if isinstance(cap, int) and len(value) > cap:
            warnings.append(_cap_warning(path, len(value), cap))

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            problems.append(f"{path}: value is less than {minimum}")
        if isinstance(maximum, (int, float)) and value > maximum:
            problems.append(f"{path}: value is greater than {maximum}")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            problems.extend(
                _stdlib_validate(item, schema["items"], f"{path}/{index}", warnings)
            )

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
                    _stdlib_validate(value[key], additional, f"{path}/{key}", warnings)
                )
        for key in sorted(set(value) & set(properties)):
            problems.extend(
                _stdlib_validate(value[key], properties[key], f"{path}/{key}", warnings)
            )
    return problems


def _render_path(parts):
    """Render a jsonschema absolute_path deque in the stdlib '<root>/a/0/b' style."""
    tail = "/".join(str(p) for p in parts)
    return f"<root>/{tail}" if tail else "<root>"


def _jsonschema_validate(state, schema, warnings):
    """Run jsonschema and split maxLength errors into warnings (observing rung)."""
    import jsonschema

    problems = []
    validator = jsonschema.Draft7Validator(schema)
    for error in validator.iter_errors(state):
        if error.validator == "maxLength" and isinstance(error.instance, str):
            warnings.append(
                _cap_warning(_render_path(error.absolute_path), len(error.instance), error.validator_value)
            )
            continue
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{where}: {error.message}")
    return problems


def load_schema():
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read schema {SCHEMA_PATH}: {exc}")
        return None


def looks_like_path(text):
    """True for an evidence entry that reads as a file path, not a key, prose, or URL."""
    if not isinstance(text, str) or "/" not in text:
        return False
    if any(ch.isspace() for ch in text):
        return False
    return not _URL_SCHEME.match(text)


def _strings(value):
    """Yield (index, item) for a string or a list of strings; index is None for a bare string."""
    if isinstance(value, str):
        yield None, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, str):
                yield i, item


def iter_path_fields(state):
    """Yield (json path, value) for every field the schema declares as a path or pointer.

    Declared paths (checked whenever they are non-empty strings):
      ranked_backlog; cross_run_artifact_refs.* except failure_fingerprints
      (the schema says an absent fingerprint file is legitimate);
      approved_campaigns[].kickoff_contract; campaign_states.*;
      comparator_registrations.*.artifact; hardware_lease_record.record_dir;
      gate_approval_records[].artifact; evidence_references.* (string or list).
    Evidence lists (checked only for entries that look like paths):
      completed_outcomes[].evidence[]; open_questions[].evidence[].
    """
    value = state.get("ranked_backlog")
    if isinstance(value, str) and value:
        yield "<root>/ranked_backlog", value

    refs = state.get("cross_run_artifact_refs")
    if isinstance(refs, dict):
        for key in sorted(refs):
            if key in OPTIONAL_CROSS_RUN_REFS:
                continue
            if isinstance(refs[key], str) and refs[key]:
                yield f"<root>/cross_run_artifact_refs/{key}", refs[key]

    campaigns = state.get("approved_campaigns")
    if isinstance(campaigns, list):
        for i, entry in enumerate(campaigns):
            if isinstance(entry, dict):
                target = entry.get("kickoff_contract")
                if isinstance(target, str) and target:
                    yield f"<root>/approved_campaigns/{i}/kickoff_contract", target

    states = state.get("campaign_states")
    if isinstance(states, dict):
        for key in sorted(states):
            if isinstance(states[key], str) and states[key]:
                yield f"<root>/campaign_states/{key}", states[key]

    registrations = state.get("comparator_registrations")
    if isinstance(registrations, dict):
        for key in sorted(registrations):
            entry = registrations[key]
            if isinstance(entry, dict) and isinstance(entry.get("artifact"), str) and entry["artifact"]:
                yield f"<root>/comparator_registrations/{key}/artifact", entry["artifact"]

    lease = state.get("hardware_lease_record")
    if isinstance(lease, dict):
        record_dir = lease.get("record_dir")
        if isinstance(record_dir, str) and record_dir:
            yield "<root>/hardware_lease_record/record_dir", record_dir

    gates = state.get("gate_approval_records")
    if isinstance(gates, list):
        for i, entry in enumerate(gates):
            if isinstance(entry, dict) and isinstance(entry.get("artifact"), str) and entry["artifact"]:
                yield f"<root>/gate_approval_records/{i}/artifact", entry["artifact"]

    evidence = state.get("evidence_references")
    if isinstance(evidence, dict):
        for key in sorted(evidence):
            for index, item in _strings(evidence[key]):
                if item:
                    suffix = "" if index is None else f"/{index}"
                    yield f"<root>/evidence_references/{key}{suffix}", item

    for field in ("completed_outcomes", "open_questions"):
        entries = state.get(field)
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            for j, item in _strings(entry.get("evidence")):
                if looks_like_path(item):
                    yield f"<root>/{field}/{i}/evidence/{j}", item


def project_root_for(state_path: Path) -> Path:
    """The parent of the nearest artifacts/ ancestor; else the state file's directory."""
    resolved = state_path.resolve()
    for parent in resolved.parents:
        if parent.name == "artifacts":
            return parent.parent
    return resolved.parent


def check_recorded_paths(state, state_path: Path, warnings):
    """WARN on recorded paths that resolve nowhere (state points, artifacts prove).

    Relative paths resolve against the project root only, so the result does
    not depend on the caller's working directory. Warns only: a run may
    validate mid-flight before an artifact lands.
    """
    root = project_root_for(state_path)
    for label, value in iter_path_fields(state):
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / value
        if not candidate.exists():
            warnings.append(
                f"{label}: recorded path resolves nowhere ({value})"
                " - fix the pointer or land the artifact"
            )


def check_file_size(state_path: Path, warnings):
    try:
        size = state_path.stat().st_size
    except OSError:
        return
    if size > WHOLE_FILE_WARN_BYTES:
        warnings.append(
            f"{state_path.name} is {size} bytes > whole-file warn threshold"
            f" {WHOLE_FILE_WARN_BYTES} - the bulk is usually long entry notes"
            " and the 'notes' escape hatch (see the cap warnings above): move"
            " prose into artifacts and cite their paths, prune resolved notes."
            " This warns; it never refuses the write."
        )


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
    warnings = []

    try:
        import jsonschema  # noqa: F401 - probe only; _jsonschema_validate imports it again
    except ImportError:
        problems.extend(_scan_schema_keywords(schema))
        problems.extend(_stdlib_validate(state, schema, "<root>", warnings))
        mode = "full schema subset (stdlib; maxLength caps warn-only)"
    else:
        problems.extend(_jsonschema_validate(state, schema, warnings))
        mode = "full (jsonschema; maxLength caps warn-only)"

    check_file_size(state_path, warnings)
    check_recorded_paths(state, state_path, warnings)
    return report(problems, mode, state_path, warnings)


def report(problems, mode, subject, warnings=()) -> int:
    seen = set()
    unique = [p for p in problems if not (p in seen or seen.add(p))]
    warnings = list(warnings)
    if unique:
        print(f"FAIL ({mode}): {len(unique)} problem(s) in {subject}")
        for problem in unique:
            print(f"  - {problem}")
        for warning in warnings:
            print(f"WARN: {warning}")
        return 1
    for warning in warnings:
        print(f"WARN: {warning}")
    print(f"PASS ({mode}): {subject} — {len(warnings)} warning(s)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1:
        print(__doc__.strip())
        return 2
    return validate_run_state(Path(args[0]))


if __name__ == "__main__":
    sys.exit(main())
