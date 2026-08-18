#!/usr/bin/env python3
"""Validate PAVE graph-to-skill traceability rows and referenced files.

When the workflow declares the composition extension, referenced child profiles
are discovered and their graph objects require rows under qualified identifiers
(realization node id + "/" + child identifier), plus one "realization" row per
composed node.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("traceability validation unavailable: install the Python pyyaml package", file=sys.stderr)
    raise SystemExit(2)


MAPPING_TYPE_TO_SECTION = {
    "role": "roles",
    "evidence": "evidence",
    "check": "checks",
    "node": "nodes",
    "endpoint": "control_endpoints",
}
VALID_TYPES = set(MAPPING_TYPE_TO_SECTION) | {"edge", "contract", "realization"}


def load_pave(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(f"cannot parse workflow YAML: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("pave"), dict):
        raise ValueError("workflow must contain a pave mapping")
    return value["pave"]


def realizations(pave: dict) -> dict:
    extensions = pave.get("extensions")
    if not isinstance(extensions, dict):
        return {}
    composition = extensions.get("composition")
    if not isinstance(composition, dict):
        return {}
    value = composition.get("realizations")
    return value if isinstance(value, dict) else {}


def clean_cell(value: str) -> str:
    return value.strip().strip("`").strip()


def parse_rows(path: Path) -> tuple[dict[tuple[str, str], list[str]], list[str]]:
    rows: dict[tuple[str, str], list[str]] = {}
    errors: list[str] = []

    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [clean_cell(cell) for cell in line[1:-1].split("|")]
        if len(cells) < 3:
            continue
        row_type, identifier, implementation = cells[:3]
        if row_type.lower() == "type" or re.fullmatch(r"-+", row_type):
            continue
        row_type = row_type.lower()
        if row_type not in VALID_TYPES:
            errors.append(f"line {line_number}: unknown traceability type {row_type!r}")
            continue
        key = (row_type, identifier)
        if key in rows:
            errors.append(f"line {line_number}: duplicate row {row_type}:{identifier}")
            continue
        file_refs = [
            clean_cell(item)
            for item in re.split(r"\s*<br\s*/?>\s*", implementation)
            if clean_cell(item)
        ]
        if not file_refs:
            errors.append(f"line {line_number}: {row_type}:{identifier} has no implementation file")
        rows[key] = file_refs

    return rows, errors


def expected_rows(
    pave: dict,
    workflow_path: Path,
    prefix: str = "",
    ancestors: frozenset[Path] = frozenset(),
) -> set[tuple[str, str]]:
    expected: set[tuple[str, str]] = set()

    def qualified(identifier: str) -> str:
        return f"{prefix}{identifier}"

    for row_type, section in MAPPING_TYPE_TO_SECTION.items():
        entries = pave.get(section, {})
        if not isinstance(entries, dict):
            raise ValueError(f"pave.{section} must be a mapping")
        expected.update((row_type, qualified(str(identifier))) for identifier in entries)

    edges = pave.get("edges", [])
    if not isinstance(edges, list):
        raise ValueError("pave.edges must be a list")
    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"pave.edges[{index}] must be a mapping")
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id:
            raise ValueError(f"pave.edges[{index}] needs a stable id for traceability")
        if edge_id in edge_ids:
            raise ValueError(f"duplicate edge id: {edge_id}")
        edge_ids.add(edge_id)
        expected.add(("edge", qualified(edge_id)))

    for contract_name in ("state", "completion"):
        if contract_name not in pave:
            raise ValueError(f"pave.{contract_name} is required for traceability")
        expected.add(("contract", qualified(contract_name)))

    for node_id, realization in realizations(pave).items():
        if not isinstance(realization, dict):
            raise ValueError(f"composition realization {node_id} must be a mapping")
        expected.add(("realization", qualified(str(node_id))))
        profile_ref = realization.get("profile")
        if not isinstance(profile_ref, str) or not profile_ref:
            raise ValueError(f"composition realization {node_id} needs a profile path")
        child_path = (workflow_path.parent / profile_ref).resolve()
        if child_path in ancestors or child_path == workflow_path.resolve():
            raise ValueError(
                f"composition realization {node_id} creates a profile reference cycle: {profile_ref}"
            )
        if not child_path.is_file():
            raise ValueError(
                f"composition realization {node_id} references missing profile {profile_ref}"
            )
        child_pave = load_pave(child_path)
        expected.update(
            expected_rows(
                child_pave,
                child_path,
                prefix=f"{qualified(str(node_id))}/",
                ancestors=ancestors | {workflow_path.resolve()},
            )
        )
    return expected


def validate_paths(
    rows: dict[tuple[str, str], list[str]], skill_dir: Path
) -> list[str]:
    errors: list[str] = []
    resolved_root = skill_dir.resolve()
    # agents/ rows are registered agent types and resolve at the plugin root
    # (skill lives at <plugin>/skills/<name>/), per the traceability table intro.
    plugin_root = resolved_root.parent.parent

    for key, references in rows.items():
        for reference in references:
            file_part = reference.split("#", 1)[0].strip()
            candidate = Path(file_part)
            if candidate.is_absolute():
                errors.append(f"{key[0]}:{key[1]} uses absolute implementation path {file_part}")
                continue
            if candidate.parts and candidate.parts[0] == "agents":
                resolved = (plugin_root / candidate).resolve()
                try:
                    resolved.relative_to(plugin_root)
                except ValueError:
                    errors.append(f"{key[0]}:{key[1]} escapes plugin root: {file_part}")
                    continue
                if not resolved.is_file():
                    errors.append(
                        f"{key[0]}:{key[1]} references missing agent definition {file_part} "
                        f"(expected at plugin root: {plugin_root / candidate})"
                    )
                continue
            resolved = (skill_dir / candidate).resolve()
            try:
                resolved.relative_to(resolved_root)
            except ValueError:
                errors.append(f"{key[0]}:{key[1]} escapes skill root: {file_part}")
                continue
            if not resolved.is_file():
                errors.append(f"{key[0]}:{key[1]} references missing file {file_part}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate PAVE roles, evidence, checks, nodes, edges, endpoints, state, and completion against skill files."
    )
    parser.add_argument("--workflow", required=True, type=Path)
    parser.add_argument("--traceability", required=True, type=Path)
    parser.add_argument("--skill-dir", required=True, type=Path)
    args = parser.parse_args()

    errors: list[str] = []
    for label, path in (
        ("workflow", args.workflow),
        ("traceability", args.traceability),
        ("skill directory", args.skill_dir),
    ):
        if not path.exists():
            errors.append(f"{label} does not exist: {path}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    try:
        pave = load_pave(args.workflow)
        rows, parse_errors = parse_rows(args.traceability)
        expected = expected_rows(pave, args.workflow.resolve())
    except (OSError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    errors.extend(parse_errors)
    actual = set(rows)
    for row_type, identifier in sorted(expected - actual):
        errors.append(f"missing row {row_type}:{identifier}")
    for row_type, identifier in sorted(actual - expected):
        errors.append(f"unknown row {row_type}:{identifier}")
    errors.extend(validate_paths(rows, args.skill_dir))

    if errors:
        print(f"FAIL traceability: {len(errors)} error(s)", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1

    counts = {
        row_type: sum(1 for candidate_type, _ in expected if candidate_type == row_type)
        for row_type in sorted(VALID_TYPES)
    }
    summary = ", ".join(f"{count} {row_type}s" for row_type, count in counts.items())
    print(f"PASS traceability: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
