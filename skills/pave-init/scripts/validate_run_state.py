#!/usr/bin/env python3
"""Validate pave-init persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>
  validate_run_state.py --frontier <path/to/planning/frontier.yaml>

Run-state mode: full validation uses the jsonschema package when importable.
Without it, falls back to a stdlib check of required keys, traversal-entry
shape, and every maxLength cap the schema declares, so run state stays
checkable on a bare python3 (this mode must never fail closed for a missing
dependency; the graph validators own that behavior). The basic mode names the
keywords it does not enforce - an unenforced keyword is announced, never a
silent pass (references/pave-spec.md section 8.1).

Both modes also emit non-fatal WARN lines: a whole-file size past the declared
escape-hatch threshold (compaction advice, never a refused write), and a
recorded path-typed field that resolves to nothing on disk (state points,
artifacts prove - a pointer to nothing is a defect to fix or an artifact still
to land).

Frontier mode: validates planning/frontier.yaml against $defs.frontier and
each dispatched entry's draft fragment against $defs.fragment, then applies
the hand rules a shape schema cannot express (references/planning-layout.md):
  1. no two entries share a draft path (one path per dispatch);
  2. every entry past `pending` names a draft path;
  3. a fragment never re-authors its dispatched node (frozen fields live in
     the parent draft; reference, never copy);
  4. no mapping under a fragment's extensions.x_planning carries an 'id' key
     in the lead-owned conflict namespace (c<N>) - those ids are lead-assigned
     in the frontier register; node-local labels (e1, n2, ...) are fine.
This mode is Stage 3 planning tooling and fails closed (exit 2) without
pyyaml + jsonschema, like validate_pave.py.

Exit codes: 0 valid, 1 invalid, 2 usage or dependency or schema error.
"""

import json
import re
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "run-state.schema.json"

# Statuses at or past dispatch: a draft path exists for these by contract.
DISPATCHED_STATUSES = {"pending_dispatched", "planned", "reviewed", "stale"}
# Statuses whose draft file must already be on disk (a planner returned).
RETURNED_STATUSES = {"planned", "reviewed", "stale"}

# Whole-file warn threshold for the schema's declared escape hatch (`notes`),
# per references/pave-spec.md section 8.1. The value is declared in the schema's
# root and $defs.frontier descriptions; keep the two in sync. Warns only -
# names the compaction action, never refuses the write.
WHOLE_FILE_WARN_BYTES = 131072

# Keywords the stdlib fallback does not enforce. Announced in the mode string
# so an unenforced keyword is loud, never a silent pass.
BASIC_MODE_UNENFORCED = "type/enum/additionalProperties/pattern/minimum"


def load_schema():
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read schema {SCHEMA_PATH}: {exc}")
        return None


def enforce_caps(schema_node, instance, path, problems):
    """Stdlib enforcement of the schema's maxLength caps (pave-spec section 8.1).

    Walks properties/items in step with the instance so every declared cap is
    checked without jsonschema. A capped field that overflows names the fix:
    move the content to an artifact and cite its path.
    """
    if not isinstance(schema_node, dict):
        return
    if isinstance(instance, str):
        cap = schema_node.get("maxLength")
        if isinstance(cap, int) and len(instance) > cap:
            problems.append(
                f"{path or '<root>'}: {len(instance)} chars exceeds maxLength {cap}"
                " - move the content to an artifact and cite its path"
            )
        return
    if isinstance(instance, dict):
        props = schema_node.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in instance:
                    enforce_caps(sub, instance[key], f"{path}.{key}" if path else key, problems)
    elif isinstance(instance, list):
        items = schema_node.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                enforce_caps(items, item, f"{path}[{i}]", problems)


def iter_path_fields(state):
    """Yield (label, value) for every recorded path-typed run-state field."""
    for label in (
        "planning_workspace",
        "generated_skill_output",
        "frontier_entries",
        "validation_results",
        "forward_test_result",
    ):
        value = state.get(label)
        if isinstance(value, str) and value:
            yield label, value
    approval = state.get("user_plan_approval")
    if isinstance(approval, dict) and isinstance(approval.get("recorded_at"), str):
        yield "user_plan_approval.recorded_at", approval["recorded_at"]
    for field in ("explorer_results", "boundary_review_results"):
        entries = state.get(field)
        if isinstance(entries, list):
            for i, entry in enumerate(entries):
                if isinstance(entry, dict) and isinstance(entry.get("artifact"), str):
                    yield f"{field}[{i}].artifact", entry["artifact"]
    history = state.get("traversal_history")
    if isinstance(history, list):
        for i, entry in enumerate(history):
            if not isinstance(entry, dict):
                continue
            evidence = entry.get("evidence")
            if isinstance(evidence, list):
                for j, item in enumerate(evidence):
                    if isinstance(item, str) and item:
                        yield f"traversal_history[{i}].evidence[{j}]", item


def check_recorded_paths(state, state_path: Path, warnings):
    """WARN on recorded paths that resolve nowhere (state points, artifacts prove).

    Relative paths resolve against the run workspace (the state file's parent)
    and the current directory. Warns only: a run may validate mid-flight before
    an artifact lands, and the traversal contract - not this helper - decides
    when evidence must exist.
    """
    base = state_path.resolve().parent
    for label, value in iter_path_fields(state):
        candidate = Path(value)
        candidates = [candidate] if candidate.is_absolute() else [base / value, Path(value)]
        if not any(c.exists() for c in candidates):
            warnings.append(
                f"{label}: recorded path resolves nowhere ({value})"
                " - fix the pointer or land the artifact"
            )


def check_file_size(path: Path, warnings):
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size > WHOLE_FILE_WARN_BYTES:
        warnings.append(
            f"{path.name} is {size} bytes (warn threshold {WHOLE_FILE_WARN_BYTES})"
            " - compact the escape hatch: prune resolved notes, move prose to"
            " artifacts and cite paths. This warns; it never refuses the write."
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

    problems = []
    if not isinstance(state, dict):
        print("FAIL: top level is not an object")
        return 1

    problems.extend(
        f"missing required field: {key}"
        for key in schema.get("required", [])
        if key not in state
    )

    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        problems.extend(
            f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
            for error in validator.iter_errors(state)
        )
        mode = "full (jsonschema)"
    except ImportError:
        history = state.get("traversal_history")
        if history is not None:
            if not isinstance(history, list):
                problems.append("traversal_history: not a list")
            else:
                problems.extend(
                    f"traversal_history[{i}]: missing node/outcome"
                    for i, entry in enumerate(history)
                    if not (isinstance(entry, dict) and entry.get("node") and entry.get("outcome"))
                )
        enforce_caps(schema, state, "", problems)
        mode = (
            "basic (stdlib: required keys, traversal shape, maxLength caps"
            f" enforced; NOT enforced: {BASIC_MODE_UNENFORCED} -"
            " install jsonschema for full validation)"
        )

    warnings = []
    check_file_size(state_path, warnings)
    check_recorded_paths(state, state_path, warnings)
    return report(problems, mode, state_path, warnings)


def schema_for(defs_schema: dict, name: str) -> dict:
    """Build a standalone root schema for one $defs entry so #/$defs refs resolve."""
    root = dict(defs_schema["$defs"][name])
    root["$defs"] = defs_schema["$defs"]
    return root


def resolve_draft(frontier_path: Path, draft: str) -> Path:
    """Draft paths are workspace-relative (e.g. planning/x.draft.pave.yaml)."""
    candidates = [
        Path(draft),
        frontier_path.parent.parent / draft,
        frontier_path.parent / draft,
    ]
    for candidate in candidates:
        if candidate.is_absolute() and candidate.exists():
            return candidate
        if not candidate.is_absolute() and candidate.exists():
            return candidate
    return frontier_path.parent.parent / draft


CONFLICT_ID = re.compile(r"^c[0-9]+$")


def scan_for_id_keys(node, path, problems, fragment_label):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key == "id" and isinstance(value, str) and CONFLICT_ID.match(value):
                problems.append(
                    f"{fragment_label}: minted conflict id '{value}' at"
                    f" extensions.x_planning.{path or '<root>'} - the c<N> namespace is"
                    " lead-assigned in frontier.yaml's conflict register; report the"
                    " conflict without an id, or use a node-local label (n1, e1, ...)"
                )
            scan_for_id_keys(value, here, problems, fragment_label)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            scan_for_id_keys(item, f"{path}[{i}]", problems, fragment_label)


def validate_fragment(entry_key, entry, fragment_path, fragment, validator, problems):
    label = f"entries.{entry_key} fragment ({fragment_path.name})"
    for error in validator.iter_errors(fragment):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{label}: {where}: {error.message}")
    pave = fragment.get("pave") if isinstance(fragment, dict) else None
    if not isinstance(pave, dict):
        return

    x_planning = {}
    extensions = pave.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get("x_planning"), dict):
        x_planning = extensions["x_planning"]

    dispatched = x_planning.get("dispatched_node") or pave.get("name")
    nodes = pave.get("nodes")
    if dispatched and isinstance(nodes, dict) and dispatched in nodes:
        problems.append(
            f"{label}: re-authors its dispatched node '{dispatched}' under nodes -"
            f" its five-part contract is frozen in the parent draft ({entry.get('contract')});"
            " reference it via extensions.x_planning, never copy"
            " (references/planning-layout.md)"
        )

    scan_for_id_keys(x_planning, "", problems, label)


def validate_frontier(frontier_path: Path) -> int:
    try:
        import jsonschema
        import yaml
    except ImportError as exc:
        print(
            f"ERROR: frontier mode needs pyyaml and jsonschema ({exc}); this is Stage 3"
            " planning tooling and fails closed like validate_pave.py"
        )
        return 2

    schema = load_schema()
    if schema is None:
        return 2

    try:
        frontier = yaml.safe_load(frontier_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot parse {frontier_path}: {exc}")
        return 1

    problems = []
    frontier_validator = jsonschema.Draft7Validator(schema_for(schema, "frontier"))
    fragment_validator = jsonschema.Draft7Validator(schema_for(schema, "fragment"))
    for error in frontier_validator.iter_errors(frontier):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{where}: {error.message}")

    entries = frontier.get("entries") if isinstance(frontier, dict) else None
    entries = entries if isinstance(entries, dict) else {}

    drafts_seen = {}
    fragments_checked = 0
    full_profiles_skipped = 0
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        draft = entry.get("draft")

        if draft:
            if draft in drafts_seen:
                problems.append(
                    f"entries.{key} and entries.{drafts_seen[draft]} share draft path"
                    f" '{draft}' - one draft path per dispatch, a redispatch mints a new"
                    " path (references/planning-layout.md)"
                )
            else:
                drafts_seen[draft] = key
        elif status in DISPATCHED_STATUSES:
            problems.append(
                f"entries.{key}: status '{status}' but no draft path - the lead mints"
                " the path at dispatch and records it before the planner starts"
            )

        if not draft:
            continue
        contract = entry.get("contract") or ""
        if "#" not in contract:
            full_profiles_skipped += 1  # root skeleton: validate_pave.py owns full profiles
            continue
        resolved = resolve_draft(frontier_path, draft)
        if not resolved.exists():
            if status in RETURNED_STATUSES:
                problems.append(
                    f"entries.{key}: status '{status}' but draft file not found at"
                    f" {resolved} - a returned entry's draft must be on disk"
                )
            continue
        try:
            fragment = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"entries.{key}: cannot parse fragment {resolved}: {exc}")
            continue
        fragments_checked += 1
        validate_fragment(key, entry, resolved, fragment, fragment_validator, problems)

    mode = (
        f"frontier ({len(entries)} entries, {fragments_checked} fragments checked,"
        f" {full_profiles_skipped} full-profile drafts left to validate_pave.py)"
    )
    warnings = []
    check_file_size(frontier_path, warnings)
    return report(problems, mode, frontier_path, warnings)


def report(problems, mode, subject, warnings=()) -> int:
    seen = set()
    unique = [p for p in problems if not (p in seen or seen.add(p))]
    if unique:
        print(f"FAIL ({mode}): {len(unique)} problem(s) in {subject}")
        for problem in unique:
            print(f"  - {problem}")
        for warning in warnings:
            print(f"  WARN: {warning}")
        return 1
    print(f"PASS ({mode}): {subject}")
    for warning in warnings:
        print(f"  WARN: {warning}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--frontier":
        return validate_frontier(Path(args[1]))
    if len(args) == 1 and args[0] != "--frontier":
        return validate_run_state(Path(args[0]))
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
