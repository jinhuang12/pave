#!/usr/bin/env python3
"""Regression tests for scripts/validate_run_state.py.

Covers the stdlib fallback's parity with jsonschema on the cheap assertions
(required, minLength, enum, const), the warn-only maxLength contract, the
derived unenforced-keyword list, and --frontier's dependency contract.

Run: python3 skills/pave-init/tests/test_validate_run_state.py
Stdlib only. Passes with or without jsonschema installed: the stdlib-mode
cases block the import in-process, so the fallback branch always runs.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_run_state as vrs  # noqa: E402

VALIDATOR = SCRIPTS / "validate_run_state.py"
SCHEMA = json.loads(vrs.SCHEMA_PATH.read_text(encoding="utf-8"))
_MISSING = object()


def importable(name):
    try:
        __import__(name)
    except ImportError:
        return False
    return True


HAVE_JSONSCHEMA = importable("jsonschema")
HAVE_FRONTIER_DEPS = HAVE_JSONSCHEMA and importable("yaml")


def valid_state():
    """A complete, valid run state (mirrors tests/test_hooks.sh write_state)."""
    return {
        "run_identity": {"run_id": "test-run"},
        "target_system": "demo system",
        "planning_workspace": None,
        "generated_skill_name": "demo-workflow",
        "generated_skill_output": None,
        "requirements_status": "approved",
        "fitness_verdict": "fit",
        "fitness_override": None,
        "exploration_lenses": ["structure"],
        "explorer_results": [{"lens": "structure"}],
        "frontier_entries": None,
        "boundary_review_results": [],
        "approval_bundle_revisions": 0,
        "plan_review_rounds": 0,
        "user_plan_approval": None,
        "build_units": None,
        "validation_results": None,
        "final_review_rounds": 0,
        "forward_test_result": None,
        "revision_ledger_state": None,
        "terminal_classification": None,
        "traversal_history": [{"node": "interview_system", "outcome": "requirements_ready"}],
    }


@contextlib.contextmanager
def stdlib_mode():
    """Force the fallback branch: a None entry in sys.modules makes `import jsonschema` raise ImportError."""
    saved = sys.modules.get("jsonschema", _MISSING)
    sys.modules["jsonschema"] = None
    try:
        yield
    finally:
        if saved is _MISSING:
            del sys.modules["jsonschema"]
        else:
            sys.modules["jsonschema"] = saved


@contextlib.contextmanager
def schema_override(schema):
    """Point the validator at a temp copy of the schema for one call."""
    original = vrs.SCHEMA_PATH
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run-state.schema.json"
        path.write_text(json.dumps(schema), encoding="utf-8")
        vrs.SCHEMA_PATH = path
        try:
            yield
        finally:
            vrs.SCHEMA_PATH = original


def run_in_process(state, force_stdlib=True):
    """Validate `state` in-process; return (exit code, captured stdout)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "run-state.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        out = io.StringIO()
        ctx = stdlib_mode() if force_stdlib else contextlib.nullcontext()
        with ctx, contextlib.redirect_stdout(out):
            rc = vrs.validate_run_state(path)
    return rc, out.getvalue()


def run_cli(*args):
    """Run the validator as a subprocess under this interpreter; return (rc, stdout)."""
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), *args], capture_output=True, text=True, check=False
    )
    return proc.returncode, proc.stdout + proc.stderr


class StdlibAssertions(unittest.TestCase):
    def test_valid_state_passes_and_names_the_mode(self):
        rc, out = run_in_process(valid_state())
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS (basic (stdlib", out)

    def test_empty_run_id_fails(self):
        state = valid_state()
        state["run_identity"]["run_id"] = ""
        rc, out = run_in_process(state)
        self.assertEqual(rc, 1, out)
        self.assertIn("run_identity.run_id: 0 chars is under minLength 1", out)

    def test_bad_enum_fails(self):
        state = valid_state()
        state["requirements_status"] = "maybe"
        rc, out = run_in_process(state)
        self.assertEqual(rc, 1, out)
        self.assertIn("requirements_status: 'maybe' is not one of", out)

    def test_nested_required_fails(self):
        state = valid_state()
        state["run_identity"] = {}
        state["traversal_history"] = [{"node": "interview_system"}]
        rc, out = run_in_process(state)
        self.assertEqual(rc, 1, out)
        self.assertIn("run_identity: missing required field: run_id", out)
        self.assertIn("traversal_history[0]: missing required field: outcome", out)

    def test_root_required_message_keeps_hooks_test_wording(self):
        state = valid_state()
        del state["requirements_status"]
        rc, out = run_in_process(state)
        self.assertEqual(rc, 1, out)
        self.assertIn("missing required field: requirements_status", out)

    def test_const_fails_when_the_schema_declares_one(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["target_system"]["const"] = "demo system"
        state = valid_state()
        state["target_system"] = "other system"
        with schema_override(schema):
            rc, out = run_in_process(state)
        self.assertEqual(rc, 1, out)
        self.assertIn("target_system: 'other system' is not the constant 'demo system'", out)

    def test_cap_overflow_is_a_warning_with_exit_zero(self):
        state = valid_state()
        state["traversal_history"][0]["outcome"] = "o" * 201
        rc, out = run_in_process(state)
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN: traversal_history[0].outcome: 201 chars exceeds maxLength 200", out)


class CapContractBothModes(unittest.TestCase):
    def test_cli_cap_overflow_warns_and_exits_zero(self):
        state = valid_state()
        state["explorer_results"][0]["lens"] = "l" * 201
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run-state.json"
            path.write_text(json.dumps(state), encoding="utf-8")
            rc, out = run_cli(str(path))
        self.assertEqual(rc, 0, out)
        self.assertIn("WARN:", out)
        self.assertIn("201 chars exceeds maxLength 200", out)

    @unittest.skipUnless(HAVE_JSONSCHEMA, "parity check needs jsonschema")
    def test_stdlib_and_jsonschema_agree_on_exit_codes(self):
        cases = []
        broken = valid_state()
        broken["run_identity"]["run_id"] = ""
        cases.append(broken)
        broken = valid_state()
        broken["fitness_verdict"] = "unsure"
        cases.append(broken)
        broken = valid_state()
        broken["traversal_history"] = [{"outcome": "x"}]
        cases.append(broken)
        capped = valid_state()
        capped["terminal_classification"] = {"status": "s" * 201}
        cases.append(capped)
        cases.append(valid_state())
        for state in cases:
            rc_stdlib, _ = run_in_process(state, force_stdlib=True)
            rc_full, _ = run_in_process(state, force_stdlib=False)
            self.assertEqual(rc_stdlib, rc_full, json.dumps(state))


class UnenforcedKeywordList(unittest.TestCase):
    def test_derived_list_excludes_enforced_and_annotation_keywords(self):
        unenforced = set(vrs.unenforced_keywords(SCHEMA))
        self.assertFalse(unenforced & vrs.BASIC_MODE_ENFORCED, unenforced)
        self.assertFalse(unenforced & vrs.NON_ASSERTION_KEYWORDS, unenforced)
        self.assertTrue(unenforced <= vrs.schema_keywords(SCHEMA))
        self.assertIn("type", unenforced)

    def test_mode_string_names_every_unenforced_keyword(self):
        rc, out = run_in_process(valid_state())
        self.assertEqual(rc, 0, out)
        for keyword in vrs.unenforced_keywords(SCHEMA):
            self.assertIn(keyword, out)

    def test_injected_unknown_keyword_is_named_in_output(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["exploration_lenses"]["minItems"] = 1
        schema["properties"]["traversal_history"]["items"]["properties"]["at"]["format"] = "date-time"
        with schema_override(schema):
            rc, out = run_in_process(valid_state())
        self.assertEqual(rc, 0, out)
        self.assertIn("minItems", out)
        self.assertIn("format", out)
        self.assertIn("NOT enforced:", out)


class FrontierMode(unittest.TestCase):
    FRONTIER = "entries:\n  root:\n    status: pending\n    contract: planning/root-contract.md\n"

    def test_minimal_valid_frontier(self):
        with tempfile.TemporaryDirectory() as tmp:
            planning = Path(tmp) / "planning"
            planning.mkdir()
            frontier = planning / "frontier.yaml"
            frontier.write_text(self.FRONTIER, encoding="utf-8")
            rc, out = run_cli("--frontier", str(frontier))
        if HAVE_FRONTIER_DEPS:
            self.assertEqual(rc, 0, out)
            self.assertIn("PASS (frontier (1 entries, 0 fragments checked", out)
        else:
            self.assertEqual(rc, 2, out)
            self.assertIn("fails closed", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
