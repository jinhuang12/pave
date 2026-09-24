#!/usr/bin/env python3
"""Validate pave-init persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>
  validate_run_state.py --planning-queue <path/to/planning/planning-queue.yaml>

Run-state mode: full validation uses the jsonschema package when importable.
Without it, falls back to a stdlib walk of the schema tree in step with the
instance that enforces required, minLength, enum, and const wherever they
occur (nested objects and array items included), so an empty run_id or a bad
enum fails on a bare python3 exactly as it does with jsonschema. This mode
must never fail closed for a missing dependency; the graph validators own that
behavior. The basic mode derives the keywords it does not enforce from the
schema itself and names them in its mode string - an unenforced keyword is
announced, never a silent pass (references/pave-spec.md section 8.1).

Run-state mode, with or without jsonschema, also emits non-fatal WARN lines: a
maxLength cap overflow (names the fix: move the content to an artifact and
cite its path), a whole-file size past
the declared escape-hatch threshold (compaction advice, never a refused write),
and a recorded path-typed field that resolves to nothing on disk (state points,
artifacts prove - a pointer to nothing is a defect to fix or an artifact still
to apply).

Planning queue mode: validates planning/planning-queue.yaml against $defs.planning_queue and
each returned entry's draft node_draft (planned, reviewed, stale) against $defs.node_draft,
then applies the hand rules a shape schema cannot express (references/planning-layout.md):
  1. no two entries share a draft path (one path per dispatch);
  2. every entry past `pending` names a draft path;
  3. a node_draft never re-authors its dispatched node (frozen fields live in
     the parent draft; reference, never copy);
  4. no mapping under a node_draft's extensions.x_planning carries an 'id' key
     in the lead-owned conflict namespace (c<N>) - those ids are lead-assigned
     in the planning_queue register; node-local labels (e1, n2, ...) are fine.
A pending_dispatched entry's draft is a planner's file in flight and is never
read: concurrent planners write beside each other, and a sibling's half-written
file must not fail the queue check.
This mode is Stage 3 planning tooling and fails closed (exit 2) without
pyyaml + jsonschema, like validate_pave.py. Per-entry maxLength caps stay
errors here (references/pave-spec.md section 8.1): content over a cap moves
to an artifact cited by path.

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
# root and $defs.planning_queue descriptions; keep the two in sync. Warns only -
# names the compaction action, never refuses the write.
WHOLE_FILE_WARN_BYTES = 131072

# Schema keywords that assert nothing on their own: annotations, and the
# applicators the stdlib walk descends through to reach nested assertions.
NON_ASSERTION_KEYWORDS = {
    "$schema",
    "$id",
    "$comment",
    "$defs",
    "title",
    "description",
    "default",
    "examples",
    "properties",
    "items",
}
# Assertions the stdlib fallback enforces itself. maxLength warns; the rest fail.
BASIC_MODE_ENFORCED = {"required", "minLength", "enum", "const", "maxLength"}


def load_schema():
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read schema {SCHEMA_PATH}: {exc}")
        return None


def schema_keywords(schema_node, found=None):
    """Collect every keyword in the schema tree the run-state walk can reach.

    Descends properties, items, and a schema-valued additionalProperties.
    Skips $defs: those shapes serve --planning-queue, which fails closed without
    jsonschema and so never runs on the stdlib path.
    """
    if found is None:
        found = set()
    if not isinstance(schema_node, dict):
        return found
    found.update(schema_node)
    props = schema_node.get("properties")
    if isinstance(props, dict):
        for sub in props.values():
            schema_keywords(sub, found)
    for key in ("items", "additionalProperties"):
        if isinstance(schema_node.get(key), dict):
            schema_keywords(schema_node[key], found)
    return found


def unenforced_keywords(schema):
    """Keywords the schema uses that the stdlib fallback leaves to jsonschema."""
    return sorted(schema_keywords(schema) - NON_ASSERTION_KEYWORDS - BASIC_MODE_ENFORCED)


def cap_warning(where, length, cap):
    return (
        f"{where}: {length} chars exceeds maxLength {cap}"
        " - move the content to an artifact and cite its path"
    )


def stdlib_validate(schema_node, instance, path, problems, warnings):
    """Stdlib enforcement of the schema's cheap assertions (pave-spec section 8.1).

    Walks properties/items in step with the instance so every occurrence is
    checked without jsonschema: required, minLength, enum, and const fail;
    maxLength warns and names the fix. Anything outside BASIC_MODE_ENFORCED is
    left to jsonschema and announced by the caller.
    """
    if not isinstance(schema_node, dict):
        return
    where = path or "<root>"
    if "const" in schema_node and instance != schema_node["const"]:
        problems.append(f"{where}: {instance!r} is not the constant {schema_node['const']!r}")
    if "enum" in schema_node and instance not in schema_node["enum"]:
        problems.append(f"{where}: {instance!r} is not one of {schema_node['enum']}")
    if isinstance(instance, str):
        floor = schema_node.get("minLength")
        if isinstance(floor, int) and len(instance) < floor:
            problems.append(f"{where}: {len(instance)} chars is under minLength {floor}")
        cap = schema_node.get("maxLength")
        if isinstance(cap, int) and len(instance) > cap:
            warnings.append(cap_warning(where, len(instance), cap))
    elif isinstance(instance, dict):
        prefix = f"{path}: " if path else ""
        problems.extend(
            f"{prefix}missing required field: {key}"
            for key in schema_node.get("required", [])
            if key not in instance
        )
        props = schema_node.get("properties")
        if isinstance(props, dict):
            for key, sub in props.items():
                if key in instance:
                    child = f"{path}.{key}" if path else key
                    stdlib_validate(sub, instance[key], child, problems, warnings)
    elif isinstance(instance, list):
        items = schema_node.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                stdlib_validate(items, item, f"{path}[{i}]", problems, warnings)


def iter_path_fields(state):
    """Yield (label, value) for every recorded path-typed run-state field."""
    for label in (
        "planning_workspace",
        "generated_skill_output",
        "planning_queue_entries",
        "validation_results",
        "forward_test_result",
    ):
        value = state.get(label)
        if isinstance(value, str) and value:
            yield label, value
    approval = state.get("user_plan_approval")
    if isinstance(approval, dict) and isinstance(approval.get("recorded_at"), str):
        yield "user_plan_approval.recorded_at", approval["recorded_at"]
    for field in ("explorer_results", "node_plan_reviews"):
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
    an artifact applies, and the traversal contract - not this helper - decides
    when evidence must exist.
    """
    base = state_path.resolve().parent
    for label, value in iter_path_fields(state):
        candidate = Path(value)
        candidates = [candidate] if candidate.is_absolute() else [base / value, Path(value)]
        if not any(c.exists() for c in candidates):
            warnings.append(
                f"{label}: recorded path resolves nowhere ({value})"
                " - fix the pointer or apply the artifact"
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

    if not isinstance(state, dict):
        print("FAIL: top level is not an object")
        return 1

    problems = []
    warnings = []
    try:
        import jsonschema

        validator = jsonschema.Draft7Validator(schema)
        for error in validator.iter_errors(state):
            where = "/".join(str(p) for p in error.absolute_path) or "<root>"
            if error.validator == "maxLength" and isinstance(error.instance, str):
                warnings.append(cap_warning(where, len(error.instance), error.validator_value))
            else:
                problems.append(f"{where}: {error.message}")
        mode = "full (jsonschema; maxLength caps warn)"
    except ImportError:
        # Traversal-entry shape: the schema walk below checks required and
        # minLength on each entry; only the not-an-object cases need a hand check.
        history = state.get("traversal_history")
        if history is not None and not isinstance(history, list):
            problems.append("traversal_history: not a list")
        elif isinstance(history, list):
            problems.extend(
                f"traversal_history[{i}]: not an object"
                for i, entry in enumerate(history)
                if not isinstance(entry, dict)
            )
        stdlib_validate(schema, state, "", problems, warnings)
        unenforced = "/".join(unenforced_keywords(schema)) or "none"
        mode = (
            "basic (stdlib: required/minLength/enum/const enforced, maxLength caps warn;"
            f" NOT enforced: {unenforced} - install jsonschema for full validation)"
        )

    check_file_size(state_path, warnings)
    check_recorded_paths(state, state_path, warnings)
    return report(problems, mode, state_path, warnings)


def schema_for(defs_schema: dict, name: str) -> dict:
    """Build a standalone root schema for one $defs entry so #/$defs refs resolve."""
    root = dict(defs_schema["$defs"][name])
    root["$defs"] = defs_schema["$defs"]
    return root


def resolve_draft(queue_path: Path, draft: str) -> Path:
    """Draft paths are workspace-relative (e.g. planning/x.draft.pave.yaml)."""
    candidates = [
        Path(draft),
        queue_path.parent.parent / draft,
        queue_path.parent / draft,
    ]
    for candidate in candidates:
        if candidate.is_absolute() and candidate.exists():
            return candidate
        if not candidate.is_absolute() and candidate.exists():
            return candidate
    return queue_path.parent.parent / draft


CONFLICT_ID = re.compile(r"^c[0-9]+$")


def scan_for_id_keys(node, path, problems, draft_label):
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            if key == "id" and isinstance(value, str) and CONFLICT_ID.match(value):
                problems.append(
                    f"{draft_label}: minted conflict id '{value}' at"
                    f" extensions.x_planning.{path or '<root>'} - the c<N> namespace is"
                    " lead-assigned in planning-queue.yaml's conflict list; report the"
                    " conflict without an id, or use a node-local label (n1, e1, ...)"
                )
            scan_for_id_keys(value, here, problems, draft_label)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            scan_for_id_keys(item, f"{path}[{i}]", problems, draft_label)


def validate_node_draft(entry_key, entry, draft_path, node_draft, validator, problems):
    label = f"entries.{entry_key} node_draft ({draft_path.name})"
    for error in validator.iter_errors(node_draft):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{label}: {where}: {error.message}")
    pave = node_draft.get("pave") if isinstance(node_draft, dict) else None
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


def validate_planning_queue(queue_path: Path) -> int:
    try:
        import jsonschema
        import yaml
    except ImportError as exc:
        print(
            f"ERROR: planning_queue mode needs pyyaml and jsonschema ({exc}); this is Stage 3"
            " planning tooling and fails closed like validate_pave.py"
        )
        return 2

    schema = load_schema()
    if schema is None:
        return 2

    try:
        planning_queue = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: cannot parse {queue_path}: {exc}")
        return 1

    problems = []
    queue_validator = jsonschema.Draft7Validator(schema_for(schema, "planning_queue"))
    draft_validator = jsonschema.Draft7Validator(schema_for(schema, "node_draft"))
    for error in queue_validator.iter_errors(planning_queue):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{where}: {error.message}")

    entries = planning_queue.get("entries") if isinstance(planning_queue, dict) else None
    entries = entries if isinstance(entries, dict) else {}

    drafts_seen = {}
    drafts_checked = 0
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
        if status not in RETURNED_STATUSES:
            continue  # in flight: the planner may be mid-write; read it once the lead marks it planned
        contract = entry.get("contract") or ""
        if "#" not in contract:
            full_profiles_skipped += 1  # root plan: validate_pave.py owns full graph_files
            continue
        resolved = resolve_draft(queue_path, draft)
        if not resolved.exists():
            if status in RETURNED_STATUSES:
                problems.append(
                    f"entries.{key}: status '{status}' but draft file not found at"
                    f" {resolved} - a returned entry's draft must be on disk"
                )
            continue
        try:
            node_draft = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            problems.append(f"entries.{key}: cannot parse node_draft {resolved}: {exc}")
            continue
        drafts_checked += 1
        validate_node_draft(key, entry, resolved, node_draft, draft_validator, problems)

    mode = (
        f"planning_queue ({len(entries)} entries, {drafts_checked} node_drafts checked,"
        f" {full_profiles_skipped} full-graph_file drafts left to validate_pave.py)"
    )
    warnings = []
    check_file_size(queue_path, warnings)
    return report(problems, mode, queue_path, warnings)


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
    if len(args) == 2 and args[0] == "--planning-queue":
        return validate_planning_queue(Path(args[1]))
    if len(args) == 1 and args[0] != "--planning-queue":
        return validate_run_state(Path(args[0]))
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
