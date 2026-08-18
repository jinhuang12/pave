#!/usr/bin/env python3
"""Validate pave-init persisted state against schemas/run-state.schema.json.

Usage:
  validate_run_state.py <path/to/run-state.json>
  validate_run_state.py --frontier <path/to/planning/frontier.yaml>

Run-state mode: full validation uses the jsonschema package when importable.
Without it, falls back to a stdlib check of required keys and traversal-entry
shape so run state stays checkable on a bare python3 (this mode must never
fail closed for a missing dependency; the graph validators own that behavior).

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
        mode = "basic (stdlib only; install jsonschema for full validation)"

    return report(problems, mode, state_path)


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
    return report(problems, mode, frontier_path)


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
    if len(args) == 2 and args[0] == "--frontier":
        return validate_frontier(Path(args[1]))
    if len(args) == 1 and args[0] != "--frontier":
        return validate_run_state(Path(args[0]))
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main())
