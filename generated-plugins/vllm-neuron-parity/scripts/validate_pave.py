#!/usr/bin/env python3
"""Validate PAVE 0.3.0 workflow definitions: JSON Schema plus graph cross-references.

When a profile declares the composition extension, referenced child profiles are
resolved, validated recursively, and checked against the composition contract
(references/pave-composition.md).

Fails closed when a dependency (PyYAML, jsonschema) is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PAVE validation unavailable: install the Python pyyaml package", file=sys.stderr)
    raise SystemExit(2)

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("PAVE validation unavailable: install the Python jsonschema package", file=sys.stderr)
    raise SystemExit(2)

ID = re.compile(r"\A[a-z][a-z0-9_]*\Z")
X_FIELD = re.compile(r"\Ax_[a-z][a-z0-9_]*\Z")
VERSION = "0.3.0"
REQUIRED_ROOT_FIELDS = [
    "version", "name", "purpose", "entrypoints", "roles", "evidence",
    "checks", "nodes", "edges", "control_endpoints", "state",
]
OPTIONAL_ROOT_FIELDS = ["status", "scope", "principles", "completion", "extensions"]
CHECK_STYLES = ["reflective", "socratic", "reviewed", "mechanical"]
INTENTS = ["plan", "explore", "execute", "review"]
ENDPOINT_KINDS = ["pause", "join", "return", "control", "terminal"]
NODE_FIELDS = {
    "intent", "purpose", "roles", "outcomes", "consumes", "produces",
    "activities", "allowed_effects", "forbidden_effects", "review",
    "instance_per",
}
MAX_COMPOSITION_DEPTH = 2
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "pave.schema.json"
COMPOSITION_SCHEMA_PATH = SCHEMA_PATH.parent / "pave-composition.schema.json"


def identifier(value: object) -> bool:
    return isinstance(value, str) and bool(ID.match(value))


def mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def listing(value: object) -> list:
    return value if isinstance(value, list) else []


def validate_schema(document: object, schema_path: Path = SCHEMA_PATH, label: str = "schema") -> list[str]:
    try:
        schema = json.loads(schema_path.read_text())
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as error:
        return [f"{label}: validation unavailable: {error}"]
    except Exception as error:
        return [f"{label}: invalid Draft 2020-12 schema: {error}"]

    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    results = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        results.append(f"{label}: {location}: {error.message}")
    return results


def validate_document(
    document: object,
    source_path: Path | None = None,
    ancestors: frozenset[Path] = frozenset(),
    depth: int = 0,
) -> list[str]:
    errors: list[str] = []

    def add(path: str, message: str) -> None:
        errors.append(f"{path}: {message}")

    errors.extend(validate_schema(document))

    root = mapping(document)
    pave = mapping(root.get("pave"))
    if list(root.keys()) != ["pave"]:
        add("$", "must contain only the pave root")

    for field in REQUIRED_ROOT_FIELDS:
        if field not in pave:
            add("pave", f"missing required field {field}")

    if pave.get("version") != VERSION:
        add("pave.version", f"must equal {VERSION}")
    if not identifier(pave.get("name")):
        add("pave.name", "must be an identifier")
    purpose = pave.get("purpose")
    if not (isinstance(purpose, str) and purpose):
        add("pave.purpose", "must be a non-empty string")

    allowed_root_fields = set(REQUIRED_ROOT_FIELDS + OPTIONAL_ROOT_FIELDS)
    for field in pave:
        if field in allowed_root_fields or X_FIELD.match(str(field)):
            continue
        add(f"pave.{field}", "unknown root field; place domain data under extensions or use an x_ prefix")

    roles = mapping(pave.get("roles"))
    evidence = mapping(pave.get("evidence"))
    checks = mapping(pave.get("checks"))
    nodes = mapping(pave.get("nodes"))
    endpoints = mapping(pave.get("control_endpoints"))
    edges = listing(pave.get("edges"))
    state = mapping(pave.get("state"))
    state_fields = (
        listing(state.get("required"))
        + list(mapping(state.get("fields")).keys())
        + list(mapping(state.get("profile_fields")).keys())
    )

    for section, entries in (
        ("roles", roles), ("evidence", evidence), ("checks", checks),
        ("nodes", nodes), ("control_endpoints", endpoints),
    ):
        for entry_id in entries:
            if not identifier(entry_id):
                add(f"pave.{section}.{entry_id}", "key must be an identifier")

    for collision in set(nodes) & set(endpoints):
        add("pave", f"node and control endpoint collide: {collision}")

    entrypoints = listing(pave.get("entrypoints"))
    if not entrypoints:
        add("pave.entrypoints", "must contain at least one node")
    for entry_id in entrypoints:
        if entry_id not in nodes:
            add("pave.entrypoints", f"unknown node {entry_id}")

    for role_id, body in roles.items():
        body = mapping(body)
        role_purpose = body.get("purpose")
        if not (isinstance(role_purpose, str) and role_purpose):
            add(f"pave.roles.{role_id}", "missing purpose")

    for evidence_id, body in evidence.items():
        body = mapping(body)
        if not identifier(body.get("kind")):
            add(f"pave.evidence.{evidence_id}", "missing kind")
        produced_by = body.get("produced_by")
        producers = produced_by if isinstance(produced_by, list) else [produced_by]
        for producer in producers:
            if producer not in nodes:
                add(f"pave.evidence.{evidence_id}.produced_by", f"unknown node {producer!r}")

    for check_id, body in checks.items():
        body = mapping(body)
        if body.get("style") not in CHECK_STYLES:
            add(f"pave.checks.{check_id}.style", f"must be a standard check style")
        question = body.get("question")
        if not (isinstance(question, str) and question):
            add(f"pave.checks.{check_id}", "missing question")
        evaluator = body.get("evaluated_by")
        if evaluator and identifier(evaluator) and evaluator not in roles and evaluator != "runtime":
            add(f"pave.checks.{check_id}.evaluated_by", f"unknown role {evaluator}")
        route = body.get("on_failure_route")
        if route is not None:
            if route not in nodes and route not in endpoints:
                add(
                    f"pave.checks.{check_id}.on_failure_route",
                    f"unknown destination {route!r}; must name a declared node or control endpoint",
                )
            elif route in endpoints:
                endpoint = mapping(endpoints.get(route))
                if endpoint.get("kind") == "terminal" and not endpoint.get("terminal_status"):
                    add(
                        f"pave.control_endpoints.{route}",
                        f"is the on_failure_route of check {check_id} but declares no"
                        " terminal_status; a designed stop must name the classification"
                        " the run records",
                    )

    for node_id, body in nodes.items():
        body = mapping(body)
        for field in body:
            if field not in NODE_FIELDS:
                add(
                    f"pave.nodes.{node_id}.{field}",
                    "unknown node field; node-level extensions are not silently ignored",
                )
        if body.get("intent") not in INTENTS:
            add(f"pave.nodes.{node_id}.intent", f"must be one of {', '.join(INTENTS)}")
        node_purpose = body.get("purpose")
        if not (isinstance(node_purpose, str) and node_purpose):
            add(f"pave.nodes.{node_id}", "missing purpose")
        node_roles = listing(body.get("roles"))
        if not node_roles:
            add(f"pave.nodes.{node_id}.roles", "must contain at least one role")
        for role in node_roles:
            if role not in roles:
                add(f"pave.nodes.{node_id}.roles", f"unknown role {role}")
        outcomes = mapping(body.get("outcomes"))
        if not outcomes:
            add(f"pave.nodes.{node_id}.outcomes", "must contain at least one outcome")
        for outcome in outcomes:
            if not identifier(outcome):
                add(f"pave.nodes.{node_id}.outcomes.{outcome}", "key must be an identifier")
        instance_per = body.get("instance_per")
        if instance_per and (not identifier(instance_per) or instance_per not in state_fields):
            add(f"pave.nodes.{node_id}.instance_per", "must name a declared state collection")

    for endpoint_id, body in endpoints.items():
        body = mapping(body)
        if body.get("kind") not in ENDPOINT_KINDS:
            add(f"pave.control_endpoints.{endpoint_id}.kind", f"must be one of {', '.join(ENDPOINT_KINDS)}")
        meaning = body.get("meaning")
        if not (isinstance(meaning, str) and meaning):
            add(f"pave.control_endpoints.{endpoint_id}", "missing meaning")
    if not any(mapping(body).get("kind") == "terminal" for body in endpoints.values()):
        add("pave.control_endpoints", "must contain at least one terminal endpoint")

    routed: dict[str, list[str]] = {}
    outcome_edges: dict[tuple[str, str], list[int]] = {}
    edge_ids: list[str] = []
    for index, edge in enumerate(edges):
        edge = mapping(edge)
        edge_path = f"pave.edges[{index}]"
        edge_id = edge.get("id")
        if edge_id is not None:
            if not identifier(edge_id):
                add(f"{edge_path}.id", "must be an identifier")
            if edge_id in edge_ids:
                add(f"{edge_path}.id", f"duplicate edge id {edge_id}")
            edge_ids.append(edge_id)

        source = edge.get("from")
        node_id, _, outcome = str(source).partition(".")
        node_outcomes = mapping(mapping(nodes.get(node_id)).get("outcomes"))
        if node_id in nodes and outcome in node_outcomes:
            routed.setdefault(node_id, []).append(outcome)
            outcome_edges.setdefault((node_id, outcome), []).append(index)
        else:
            add(f"{edge_path}.from", f"unknown node outcome {source!r}")

        for check in listing(edge.get("checks")):
            if check not in checks:
                add(f"{edge_path}.checks", f"unknown check {check}")

        target = edge.get("to")
        if isinstance(target, str):
            if target not in nodes and target not in endpoints:
                add(f"{edge_path}.to", f"unknown destination {target}")
        elif isinstance(target, dict):
            fan_out = target.get("fan_out")
            if fan_out not in nodes:
                add(f"{edge_path}.to.fan_out", f"unknown node {fan_out!r}")
            collection = target.get("for_each")
            if not identifier(collection):
                add(f"{edge_path}.to.for_each", "must be an identifier")
            elif collection not in state_fields:
                add(f"{edge_path}.to.for_each", f"unknown state collection {collection}")
            paired = target.get("pair_each_with")
            if paired and paired not in nodes:
                add(f"{edge_path}.to.pair_each_with", f"unknown node {paired!r}")
        else:
            add(f"{edge_path}.to", "must be a destination identifier or fan-out mapping")

    for node_id, body in nodes.items():
        for outcome in mapping(mapping(body).get("outcomes")):
            if outcome not in routed.get(node_id, []):
                add(f"pave.nodes.{node_id}.outcomes.{outcome}", "has no outgoing edge")

    # A failed check on an outcome's only edge is a designed stop: it must
    # declare where the run goes instead (references/pave-yaml.md section 9).
    terminal_endpoints = sorted(
        endpoint_id for endpoint_id, body in endpoints.items()
        if mapping(body).get("kind") == "terminal"
    )
    for (node_id, outcome), indices in outcome_edges.items():
        if len(indices) != 1:
            continue
        for check_id in listing(mapping(edges[indices[0]]).get("checks")):
            check_body = mapping(checks.get(check_id))
            if check_id in checks and not check_body.get("on_failure_route"):
                add(
                    f"pave.checks.{check_id}",
                    f"guards the sole edge from {node_id}.{outcome} but declares no"
                    " on_failure_route - a failure here is a designed stop with no"
                    " destination; name a declared node or control endpoint"
                    f" (terminal endpoints: {', '.join(terminal_endpoints) or 'none declared'})",
                )

    extensions = mapping(pave.get("extensions"))
    required_extensions = listing(extensions.get("required"))
    if "composition" in extensions:
        composition = extensions.get("composition")
        if not isinstance(composition, dict) or not composition:
            add("pave.extensions.composition", "must be a non-empty mapping")
        else:
            if "composition" not in required_extensions:
                add("pave.extensions.required", "must list composition when the composition extension is used")
            errors.extend(
                validate_composition(
                    composition, pave, source_path, ancestors, depth,
                )
            )
    elif "composition" in required_extensions:
        add("pave.extensions.required", "declares composition but no composition block is present")

    return errors


def validate_composition(
    composition: dict,
    pave: dict,
    source_path: Path | None,
    ancestors: frozenset[Path],
    depth: int,
) -> list[str]:
    errors: list[str] = []

    def add(path: str, message: str) -> None:
        errors.append(f"{path}: {message}")

    errors.extend(
        validate_schema(composition, COMPOSITION_SCHEMA_PATH, "composition-schema")
    )

    nodes = mapping(pave.get("nodes"))
    evidence = mapping(pave.get("evidence"))

    for node_id, realization in mapping(composition.get("realizations")).items():
        realization = mapping(realization)
        path = f"pave.extensions.composition.realizations.{node_id}"

        node = mapping(nodes.get(node_id))
        if node_id not in nodes:
            add(path, "does not name a declared node")
            continue
        parent_outcomes = mapping(node.get("outcomes"))

        if depth + 1 > MAX_COMPOSITION_DEPTH:
            add(path, f"composition depth exceeds {MAX_COMPOSITION_DEPTH}")
            continue

        profile_ref = realization.get("profile")
        if source_path is None:
            add(f"{path}.profile", "cannot resolve child profile without a source file path")
            continue
        child_path = (source_path.parent / str(profile_ref)).resolve()
        if child_path in ancestors or child_path == source_path.resolve():
            add(f"{path}.profile", f"profile reference cycle: {profile_ref}")
            continue
        if not child_path.is_file():
            add(f"{path}.profile", f"child profile not found: {profile_ref}")
            continue

        digest = realization.get("profile_digest")
        if digest:
            actual = "sha256:" + hashlib.sha256(child_path.read_bytes()).hexdigest()
            if digest != actual:
                add(f"{path}.profile_digest", f"digest mismatch: declared {digest}, actual {actual}")

        try:
            child_document = yaml.safe_load(child_path.read_text())
        except yaml.YAMLError as error:
            add(f"{path}.profile", f"child profile YAML error: {error}")
            continue

        child_errors = validate_document(
            child_document,
            child_path,
            ancestors | {source_path.resolve()},
            depth + 1,
        )
        for child_error in child_errors:
            add(f"{path}.profile[{profile_ref}]", child_error)

        child_pave = mapping(mapping(child_document).get("pave"))
        child_nodes = mapping(child_pave.get("nodes"))
        child_endpoints = mapping(child_pave.get("control_endpoints"))
        child_evidence = mapping(child_pave.get("evidence"))
        child_terminals = {
            endpoint_id
            for endpoint_id, body in child_endpoints.items()
            if mapping(body).get("kind") == "terminal"
        }

        entrypoint = realization.get("entrypoint")
        if entrypoint and entrypoint not in child_nodes:
            add(f"{path}.entrypoint", f"unknown child node {entrypoint}")

        terminal_map = mapping(realization.get("terminal_map"))
        for child_terminal, parent_outcome in terminal_map.items():
            if child_terminal not in child_terminals:
                add(f"{path}.terminal_map.{child_terminal}", "does not name a child terminal endpoint")
            if parent_outcome not in parent_outcomes:
                add(f"{path}.terminal_map.{child_terminal}", f"unknown parent outcome {parent_outcome}")
            else:
                outcome_body = mapping(parent_outcomes.get(parent_outcome))
                if not listing(outcome_body.get("required_evidence")):
                    add(
                        f"pave.nodes.{node_id}.outcomes.{parent_outcome}",
                        "terminal-mapped outcome must declare required_evidence",
                    )
        for child_terminal in sorted(child_terminals - set(terminal_map)):
            add(f"{path}.terminal_map", f"unmapped child terminal endpoint {child_terminal}")

        for index, export in enumerate(listing(realization.get("evidence_exports"))):
            export = mapping(export)
            export_path = f"{path}.evidence_exports[{index}]"
            if export.get("child") not in child_evidence:
                add(export_path, f"unknown child evidence {export.get('child')!r}")
            if export.get("parent") not in evidence:
                add(export_path, f"unknown parent evidence {export.get('parent')!r}")

        delegated = listing(realization.get("delegated_effects"))
        allowed = listing(node.get("allowed_effects"))
        if delegated and allowed:
            for effect in delegated:
                if effect not in allowed:
                    add(
                        f"{path}.delegated_effects",
                        f"effect {effect} exceeds parent allowed_effects",
                    )

    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: validate_pave.py WORKFLOW.pave.yaml [...]", file=sys.stderr)
        return 2

    failed = False
    for path in argv:
        try:
            document = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as error:
            print(f"{path}: YAML error: {error}", file=sys.stderr)
            failed = True
            continue

        errors = validate_document(document, Path(path))
        if errors:
            failed = True
            print(f"FAIL {path}: {len(errors)} error(s)", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
        else:
            pave = mapping(mapping(document).get("pave"))
            nodes = mapping(pave.get("nodes"))
            edges = listing(pave.get("edges"))
            endpoints = mapping(pave.get("control_endpoints"))
            print(f"PASS {path}: {len(nodes)} nodes, {len(edges)} edges, {len(endpoints)} control endpoints")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
