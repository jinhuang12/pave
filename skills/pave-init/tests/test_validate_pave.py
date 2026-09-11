#!/usr/bin/env python3
"""Regression tests for the runtime_bindings block in scripts/validate_pave.py.

Covers: a graph with no block or an empty block passes; a valid deny plus caps
block passes; a deny glob or cap family that matches a declared evidence or
state path is rejected and both names appear; a glob the runtime guard would widen
onto a declared directory through its trailing components is rejected too;
bound_to other than [lead] is rejected; an extra property is rejected; the CLI
exits non-zero with one line per violation.

Run: python3 skills/pave-init/tests/test_validate_pave.py
Needs pyyaml and jsonschema; skips without them.
"""

from __future__ import annotations

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "validate_pave.py"

try:
    import yaml
    from validate_pave import glob_covers, validate_document
except ImportError:  # pragma: no cover - dependency gate
    yaml = None
    glob_covers = None
    validate_document = None

GRAPH = {
    "pave": {
        "version": "0.3.0",
        "name": "bindings_fixture",
        "purpose": "Produce a reviewed candidate.",
        "entrypoints": ["do_work"],
        "roles": {"worker": {"purpose": "Do the bounded work."}},
        "evidence": {
            "build_record": {
                "kind": "observation",
                "produced_by": "do_work",
                "artifact": "increments/build-01.py",
            },
        },
        "checks": {},
        "nodes": {
            "do_work": {
                "intent": "execute",
                "purpose": "Do the work.",
                "roles": ["worker"],
                "produces": ["build_record"],
                "outcomes": {"done": {"meaning": "Work complete."}},
            },
        },
        "edges": [
            {"id": "done_to_accepted", "from": "do_work.done", "to": "accepted"},
        ],
        "control_endpoints": {
            "accepted": {"kind": "terminal", "meaning": "Accepted."},
        },
        "state": {
            "required": ["completed_outcomes"],
            "fields": {"grants": {"path": "leases/grant-log.md"}},
        },
    }
}

DENY = {
    "glob": "scratch/lead-*.md",
    "bound_to": ["lead"],
    "reason": "Lead-authored scratch was never read.",
    "remedy": "Write the fact into run state instead.",
    "created_by": "cp-20260910T193405Z",
}
CAP = {
    "family_glob": "leases/*-grant-*.md",
    "max_per_checkpoint": 20,
    "created_by": "cp-20260910T193405Z",
}


INCREMENTS_DIR = "artifacts/campaigns/parity/increments/"


def graph_with(bindings: object | None) -> dict:
    document = copy.deepcopy(GRAPH)
    if bindings is not None:
        document["pave"]["runtime_bindings"] = bindings
    return document


def graph_declaring_a_directory(bindings: object) -> dict:
    """A real campaign declares its increments as a directory, not one file."""
    document = graph_with(bindings)
    document["pave"]["evidence"]["build_record"]["artifact"] = INCREMENTS_DIR
    return document


@unittest.skipIf(validate_document is None, "pyyaml and jsonschema are required")
class RuntimeBindingsTests(unittest.TestCase):
    def test_no_block_passes(self) -> None:
        self.assertEqual(validate_document(graph_with(None)), [])

    def test_empty_block_passes(self) -> None:
        self.assertEqual(validate_document(graph_with({})), [])
        self.assertEqual(validate_document(graph_with({"deny": [], "caps": []})), [])

    def test_valid_deny_and_caps_pass(self) -> None:
        errors = validate_document(graph_with({"deny": [DENY], "caps": [CAP]}))
        self.assertEqual(errors, [])

    def test_deny_glob_matching_declared_evidence_path_rejected(self) -> None:
        deny = dict(DENY, glob="increments/build-*.py")
        errors = validate_document(graph_with({"deny": [deny]}))
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith("pave.runtime_bindings.deny[0].glob:"), errors)
        self.assertIn("increments/build-*.py", errors[0])
        self.assertIn("increments/build-01.py", errors[0])

    def test_cap_family_matching_declared_state_path_rejected(self) -> None:
        cap = dict(CAP, family_glob="leases/*.md")
        errors = validate_document(graph_with({"caps": [cap]}))
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith("pave.runtime_bindings.caps[0].family_glob:"), errors)
        self.assertIn("leases/grant-log.md", errors[0])

    def test_glob_covers_matches_the_runtime_guard(self) -> None:
        """The guard matches a glob against the workspace-relative path and every
        trailing-component suffix; an absolute glob never reaches a relative path."""
        for pattern in ("increments/*", "campaigns/*/increments/*", "*", INCREMENTS_DIR,
                        "parity/increments/*", "artifacts/campaigns/*/increments"):
            with self.subTest(covers=pattern):
                self.assertTrue(glob_covers(INCREMENTS_DIR, pattern))
        for pattern in ("increments/build-*.py", "/tmp/opusaudit/*.py", "/increments/*",
                        "scratch/lead-*.md", "leases/*.md"):
            with self.subTest(clear=pattern):
                self.assertFalse(glob_covers(INCREMENTS_DIR, pattern))

    def test_deny_glob_the_guard_would_widen_onto_a_declared_directory_rejected(self) -> None:
        for pattern in ("increments/*", "campaigns/*/increments/*", "*"):
            with self.subTest(glob=pattern):
                errors = validate_document(graph_declaring_a_directory({"deny": [dict(DENY, glob=pattern)]}))
                widened = [e for e in errors if e.startswith("pave.runtime_bindings.deny[0].glob:")]
                self.assertTrue(widened, errors)
                self.assertIn(INCREMENTS_DIR, " ".join(widened))
                self.assertIn(pattern, widened[0])

    def test_cap_family_glob_the_guard_would_widen_rejected(self) -> None:
        errors = validate_document(graph_declaring_a_directory({"caps": [dict(CAP, family_glob="increments/*")]}))
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith("pave.runtime_bindings.caps[0].family_glob:"), errors)
        self.assertIn(INCREMENTS_DIR, errors[0])

    def test_glob_off_the_declared_tree_still_passes(self) -> None:
        """The fix must not deny every binding: a glob the guard cannot widen onto a
        declared path passes, and an absolute glob outside the workspace passes."""
        for pattern in ("increments/build-*.py", "/tmp/opusaudit/*.py", "scratch/lead-*.md"):
            with self.subTest(glob=pattern):
                errors = validate_document(graph_declaring_a_directory({"deny": [dict(DENY, glob=pattern)]}))
                self.assertEqual(errors, [])

    def test_bound_to_other_than_lead_rejected(self) -> None:
        for bound_to in (["seat"], ["lead", "seat"], [], "lead"):
            with self.subTest(bound_to=bound_to):
                deny = dict(DENY, bound_to=bound_to)
                errors = validate_document(graph_with({"deny": [deny]}))
                self.assertTrue(errors, "expected a rejection")
                self.assertTrue(
                    any(e.startswith("pave.runtime_bindings.deny[0].bound_to: must be exactly [lead]") for e in errors),
                    errors,
                )

    def test_extra_property_rejected(self) -> None:
        cases = {
            "block": {"deny": [DENY], "owner": "lead"},
            "deny": {"deny": [dict(DENY, severity="high")]},
            "cap": {"caps": [dict(CAP, note="x")]},
        }
        for label, bindings in cases.items():
            with self.subTest(level=label):
                errors = validate_document(graph_with(bindings))
                self.assertTrue(any("Additional properties are not allowed" in e for e in errors), errors)

    def test_schema_rejects_bad_scalars(self) -> None:
        errors = validate_document(graph_with({"deny": [dict(DENY, glob="")]}))
        self.assertTrue(any("deny.0.glob" in e for e in errors), errors)
        errors = validate_document(graph_with({"caps": [dict(CAP, max_per_checkpoint=0)]}))
        self.assertTrue(any("max_per_checkpoint" in e for e in errors), errors)
        errors = validate_document(graph_with({"deny": [{k: v for k, v in DENY.items() if k != "remedy"}]}))
        self.assertTrue(any("'remedy' is a required property" in e for e in errors), errors)

    def test_cli_exit_code_and_one_line_per_violation(self) -> None:
        deny = dict(DENY, glob="increments/build-*.py", bound_to=["seat"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workflow.pave.yaml"
            path.write_text(yaml.safe_dump(graph_with({"deny": [deny]})))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, result.stderr)
            lines = result.stderr.splitlines()
            self.assertTrue(lines[0].startswith(f"FAIL {path}: "), lines)
            violations = [line for line in lines[1:] if line.startswith("  pave.runtime_bindings")]
            self.assertTrue(any("bound_to" in line for line in violations), lines)
            self.assertTrue(any("increments/build-01.py" in line for line in violations), lines)

            path.write_text(yaml.safe_dump(graph_with({"deny": [DENY], "caps": [CAP]})))
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith(f"PASS {path}: 1 nodes, 1 edges, 1 control endpoints"), result.stdout)


if __name__ == "__main__":
    unittest.main()
