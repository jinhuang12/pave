#!/usr/bin/env python3
"""Tests for the observing-rung length caps, whole-file warn, and path checks
in scripts/validate_run_state.py (pave-spec section 8.1).

Run from the plugin root:
  python3 -m unittest tests.test_run_state_schema tests.test_validate_run_state_caps

Every subprocess case runs twice: once with `python -S` (no site-packages, so
the stdlib fallback is forced - the production path on a bare python3) and
once in the interpreter's default mode (jsonschema when importable). Cases
that need jsonschema itself are skipped when it is not importable.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "scripts" / "validate_run_state.py"
SCHEMA = ROOT / "schemas" / "run-state.schema.json"

sys.path.insert(0, str(ROOT / "scripts"))
import validate_run_state as vrs  # noqa: E402

STDLIB_MODE = "full schema subset (stdlib; maxLength caps warn-only)"
JSONSCHEMA_MODE = "full (jsonschema; maxLength caps warn-only)"

MINIMAL = {
    "workflow_identity": {"run_id": "run-0001"},
    "pinned_release": None,
    "requested_targets": [],
    "instance_roster": [],
    "cross_run_artifact_refs": {},
    "ranked_backlog": None,
    "approved_campaigns": [],
    "campaign_states": {},
    "campaign_target_pins": {},
    "scheduling_holds": [],
    "comparator_registrations": {},
    "hardware_attempt_counts": {},
    "hardware_lease_record": None,
    "gate_approval_records": [],
    "active_node_runs": [],
    "completed_outcomes": [],
    "evidence_references": {},
    "open_questions": [],
    "terminal_classification": None,
    "scan_entry_id": None,
    "design_entry_id": None,
}

# Path-typed fields: no cap by design; the validator resolves them instead.
PATH_FIELDS_WITHOUT_CAP = {
    "cross_run_artifact_refs.parity_scorecard",
    "cross_run_artifact_refs.backlog",
    "cross_run_artifact_refs.debt_ledger",
    "cross_run_artifact_refs.failure_fingerprints",
    "ranked_backlog",
    "approved_campaigns[].kickoff_contract",
    "campaign_states.*",
    "comparator_registrations.*.artifact",
    "hardware_lease_record.record_dir",
    "gate_approval_records[].artifact",
    "evidence_references.*<oneOf0>",
    "evidence_references.*<oneOf1>[]",
}


def has_jsonschema() -> bool:
    try:
        import jsonschema  # noqa: F401
    except ImportError:
        return False
    return True


def modes():
    """(label, stdlib_flag, expected mode string) for each interpreter mode to run."""
    yield "stdlib", True, STDLIB_MODE
    yield "default", False, JSONSCHEMA_MODE if has_jsonschema() else STDLIB_MODE


def write_state(tmp: str, state: dict, layout: str = "artifacts/run/run-state.json") -> Path:
    path = Path(tmp) / layout
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def run_validator(state_path: Path, *, stdlib: bool):
    command = [sys.executable]
    if stdlib:
        command.append("-S")
    command.extend([str(VALIDATOR), str(state_path)])
    env = os.environ.copy()
    if stdlib:
        env.pop("PYTHONPATH", None)
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def warn_lines(output: str):
    return [line for line in output.splitlines() if line.startswith("WARN: ")]


class CapsAreWarnings(unittest.TestCase):
    def test_valid_small_state_passes_with_zero_warnings(self):
        for label, stdlib, mode in modes():
            with self.subTest(mode=label), tempfile.TemporaryDirectory() as tmp:
                path = write_state(tmp, MINIMAL)
                code, out = run_validator(path, stdlib=stdlib)
                self.assertEqual(code, 0, out)
                self.assertEqual(warn_lines(out), [], out)
                self.assertEqual(out.strip().splitlines()[-1], f"PASS ({mode}): {path} — 0 warning(s)")

    def test_cap_violation_is_warning_not_error(self):
        state = json.loads(json.dumps(MINIMAL))
        state["completed_outcomes"] = [{"node": "n", "outcome": "ok", "note": "x" * 501}]
        state["open_questions"] = [{"question": "q", "status": "s" * 201}]
        state["instance_roster"] = [{"instance": "trn2", "note": "r" * 301}]
        state["gate_approval_records"] = [{"gate": 1, "artifact": "/", "decision": "d" * 2001}]
        expected = {
            "WARN: <root>/completed_outcomes/0/note: 501 chars > cap 500",
            "WARN: <root>/open_questions/0/status: 201 chars > cap 200",
            "WARN: <root>/instance_roster/0/note: 301 chars > cap 300",
            "WARN: <root>/gate_approval_records/0/decision: 2001 chars > cap 2000",
        }
        for label, stdlib, mode in modes():
            with self.subTest(mode=label), tempfile.TemporaryDirectory() as tmp:
                path = write_state(tmp, state)
                code, out = run_validator(path, stdlib=stdlib)
                self.assertEqual(code, 0, out)
                self.assertNotIn("FAIL", out)
                self.assertEqual(set(warn_lines(out)), expected, out)
                self.assertEqual(out.strip().splitlines()[-1], f"PASS ({mode}): {path} — 4 warning(s)")

    def test_cap_in_additional_properties_map_and_at_root(self):
        state = json.loads(json.dumps(MINIMAL))
        state["campaign_target_pins"] = {"c1": "p" * 201}
        state["scan_entry_id"] = "s" * 201
        expected = {
            "WARN: <root>/campaign_target_pins/c1: 201 chars > cap 200",
            "WARN: <root>/scan_entry_id: 201 chars > cap 200",
        }
        for label, stdlib, _ in modes():
            with self.subTest(mode=label), tempfile.TemporaryDirectory() as tmp:
                path = write_state(tmp, state)
                code, out = run_validator(path, stdlib=stdlib)
                self.assertEqual(code, 0, out)
                self.assertEqual(set(warn_lines(out)), expected, out)

    def test_real_error_still_fails_and_keeps_warnings_visible(self):
        state = json.loads(json.dumps(MINIMAL))
        state["completed_outcomes"] = [{"node": "n", "outcome": "ok", "note": "x" * 501}]
        del state["pinned_release"]
        for label, stdlib, _ in modes():
            with self.subTest(mode=label), tempfile.TemporaryDirectory() as tmp:
                path = write_state(tmp, state)
                code, out = run_validator(path, stdlib=stdlib)
                self.assertEqual(code, 1, out)
                self.assertTrue(out.startswith("FAIL ("), out)
                self.assertIn("WARN: <root>/completed_outcomes/0/note: 501 chars > cap 500", out)

    @unittest.skipUnless(has_jsonschema(), "jsonschema not importable in this interpreter")
    def test_jsonschema_mode_partitions_maxlength_errors_into_warnings(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        state = json.loads(json.dumps(MINIMAL))
        state["open_questions"] = [{"question": "q" * 501, "status": "ok"}]
        warnings = []
        problems = vrs._jsonschema_validate(state, schema, warnings)
        self.assertEqual(problems, [])
        self.assertEqual(warnings, ["<root>/open_questions/0/question: 501 chars > cap 500"])


class UnknownKeywordIsLoud(unittest.TestCase):
    def test_maxlength_is_a_supported_keyword(self):
        self.assertIn("maxLength", vrs.SUPPORTED_SCHEMA_KEYS)

    def test_stdlib_rejects_unknown_keyword(self):
        schema = {"type": "object", "properties": {"a": {"type": "string", "pattern": "^x$"}}}
        problems = vrs._stdlib_validate({"a": "x"}, schema)
        self.assertEqual(problems, ["<root>/a: validator does not support schema keyword(s): pattern"])

    def test_schema_prescan_is_loud_for_fields_absent_from_the_instance(self):
        # The instance walk only visits fields the state carries; the pre-scan covers the
        # whole schema tree so an unenforceable keyword never passes silently.
        schema = {
            "type": "object",
            "properties": {
                "absent": {"type": "string", "format": "date-time"},
                "list": {"type": "array", "items": {"type": "string", "pattern": "^x$"}},
                "map": {"type": "object", "additionalProperties": {"const": 1}},
                "either": {"oneOf": [{"type": "string"}, {"type": "array", "uniqueItems": True}]},
            },
        }
        self.assertEqual(vrs._stdlib_validate({}, schema), [])
        self.assertEqual(
            vrs._scan_schema_keywords(schema),
            [
                "<root>/absent: validator does not support schema keyword(s): format",
                "<root>/list/[]: validator does not support schema keyword(s): pattern",
                "<root>/map/*: validator does not support schema keyword(s): const",
                "<root>/either<oneOf1>: validator does not support schema keyword(s): uniqueItems",
            ],
        )

    def test_shipped_schema_uses_only_supported_keywords(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(vrs._scan_schema_keywords(schema), [])

    def test_unknown_keyword_in_schema_file_exits_1_in_stdlib_mode(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        schema["properties"]["workflow_identity"]["properties"]["started"]["format"] = "date-time"
        saved_schema_path = vrs.SCHEMA_PATH
        saved_module = sys.modules.get("jsonschema", "<absent>")
        with tempfile.TemporaryDirectory() as tmp:
            schema_path = Path(tmp) / "schema.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            state_path = write_state(tmp, MINIMAL)
            vrs.SCHEMA_PATH = schema_path
            sys.modules["jsonschema"] = None  # force the stdlib fallback in-process
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    code = vrs.validate_run_state(state_path)
            finally:
                vrs.SCHEMA_PATH = saved_schema_path
                if saved_module == "<absent>":
                    del sys.modules["jsonschema"]
                else:
                    sys.modules["jsonschema"] = saved_module
        out = buffer.getvalue()
        self.assertEqual(code, 1, out)
        self.assertIn("validator does not support schema keyword(s): format", out)


class WholeFileWarn(unittest.TestCase):
    def test_threshold_matches_pave_init(self):
        self.assertEqual(vrs.WHOLE_FILE_WARN_BYTES, 131072)

    def test_whole_file_warn_triggers_and_names_notes(self):
        state = json.loads(json.dumps(MINIMAL))
        state["notes"] = "n" * (vrs.WHOLE_FILE_WARN_BYTES + 1)
        for label, stdlib, mode in modes():
            with self.subTest(mode=label), tempfile.TemporaryDirectory() as tmp:
                path = write_state(tmp, state)
                size = path.stat().st_size
                code, out = run_validator(path, stdlib=stdlib)
                self.assertEqual(code, 0, out)
                warns = warn_lines(out)
                self.assertEqual(len(warns), 1, out)
                self.assertTrue(
                    warns[0].startswith(
                        f"WARN: run-state.json is {size} bytes > whole-file warn threshold 131072"
                    ),
                    warns[0],
                )
                self.assertIn("'notes' escape hatch", warns[0])
                self.assertNotIn("chars > cap", out)  # notes itself carries no cap
                self.assertEqual(out.strip().splitlines()[-1], f"PASS ({mode}): {path} — 1 warning(s)")

    def test_small_file_does_not_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_state(tmp, MINIMAL)
            warnings = []
            vrs.check_file_size(path, warnings)
            self.assertEqual(warnings, [])


class RecordedPaths(unittest.TestCase):
    def test_project_root_is_parent_of_artifacts_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_state(tmp, MINIMAL)
            self.assertEqual(vrs.project_root_for(path), Path(tmp).resolve())

    def test_project_root_falls_back_to_state_dir_without_artifacts_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_state(tmp, MINIMAL, layout="state/run-state.json")
            self.assertEqual(vrs.project_root_for(path), (Path(tmp) / "state").resolve())

    def test_looks_like_path_heuristic(self):
        self.assertTrue(vrs.looks_like_path("artifacts/run/delta/report.md"))
        self.assertTrue(vrs.looks_like_path("increments/scope-lap-040.md"))
        self.assertTrue(vrs.looks_like_path("/abs/path/file.md"))
        self.assertFalse(vrs.looks_like_path("delta_report"))  # evidence key
        self.assertFalse(vrs.looks_like_path("see the design record / section 3"))  # prose
        self.assertFalse(vrs.looks_like_path("https://example.com/a/b"))  # URL
        self.assertFalse(vrs.looks_like_path(""))
        self.assertFalse(vrs.looks_like_path(None))

    def test_missing_path_target_warns_and_existing_targets_do_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "artifacts" / "run" / "backlog").mkdir(parents=True)
            (root / "artifacts" / "run" / "backlog" / "ranked.md").write_text("x", encoding="utf-8")
            (root / "artifacts" / "run" / "delta").mkdir(parents=True)
            (root / "artifacts" / "campaigns" / "c1").mkdir(parents=True)
            absolute_existing = str((root / "artifacts" / "run" / "backlog" / "ranked.md").resolve())

            state = json.loads(json.dumps(MINIMAL))
            state["ranked_backlog"] = "artifacts/run/backlog/ranked.md"  # exists
            state["campaign_states"] = {"c1": "artifacts/campaigns/c1/"}  # exists (dir)
            state["cross_run_artifact_refs"] = {
                "backlog": "artifacts/cross-run/backlog.yaml",  # missing -> warn
                "failure_fingerprints": "artifacts/cross-run/ff.yaml",  # missing, legitimate -> no warn
            }
            state["gate_approval_records"] = [
                {"gate": 1, "artifact": "artifacts/campaigns/c1/approvals/gate1.md"},  # missing -> warn
                {"gate": 2, "artifact": absolute_existing},  # absolute, exists
            ]
            state["evidence_references"] = {
                "delta_report": "artifacts/run/delta/",  # exists
                "records": ["artifacts/run/delta/", "artifacts/run/missing-record.md"],  # 2nd missing -> warn
            }
            state["completed_outcomes"] = [
                {
                    "node": "n",
                    "outcome": "ok",
                    "evidence": [
                        "delta_report",  # key -> skipped
                        "see the design record for details",  # prose -> skipped
                        "https://example.com/x/y",  # URL -> skipped
                        "artifacts/run/delta/",  # exists
                        "increments/scope-lap-040.md",  # campaign-relative pointer -> warn
                    ],
                }
            ]
            state["open_questions"] = [
                {"question": "q", "evidence": ["route_costing_and_backlog", "artifacts/run/nope.md"]}  # 2nd -> warn
            ]
            expected = {
                "WARN: <root>/cross_run_artifact_refs/backlog: recorded path resolves nowhere"
                " (artifacts/cross-run/backlog.yaml) - fix the pointer or land the artifact",
                "WARN: <root>/gate_approval_records/0/artifact: recorded path resolves nowhere"
                " (artifacts/campaigns/c1/approvals/gate1.md) - fix the pointer or land the artifact",
                "WARN: <root>/evidence_references/records/1: recorded path resolves nowhere"
                " (artifacts/run/missing-record.md) - fix the pointer or land the artifact",
                "WARN: <root>/completed_outcomes/0/evidence/4: recorded path resolves nowhere"
                " (increments/scope-lap-040.md) - fix the pointer or land the artifact",
                "WARN: <root>/open_questions/0/evidence/1: recorded path resolves nowhere"
                " (artifacts/run/nope.md) - fix the pointer or land the artifact",
            }
            path = write_state(tmp, state)
            for label, stdlib, mode in modes():
                with self.subTest(mode=label):
                    code, out = run_validator(path, stdlib=stdlib)
                    self.assertEqual(code, 0, out)
                    self.assertEqual(set(warn_lines(out)), expected, out)
                    self.assertEqual(out.strip().splitlines()[-1], f"PASS ({mode}): {path} — 5 warning(s)")

    def test_path_check_is_independent_of_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = json.loads(json.dumps(MINIMAL))
            state["ranked_backlog"] = "artifacts/run/ranked.md"
            path = write_state(tmp, state)
            (Path(tmp) / "artifacts" / "run" / "ranked.md").write_text("x", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(VALIDATOR), str(path)],
                capture_output=True,
                text=True,
                cwd="/",
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertEqual(warn_lines(proc.stdout), [], proc.stdout)


class SchemaCapCoverage(unittest.TestCase):
    """Every non-enum string field declares maxLength unless it is a declared path."""

    @staticmethod
    def _walk(node, path, capped, uncapped):
        if not isinstance(node, dict):
            return
        declared = node.get("type")
        types = declared if isinstance(declared, list) else [declared]
        if "string" in types and "enum" not in node:
            (capped if "maxLength" in node else uncapped).add(path)
        for key, sub in node.get("properties", {}).items():
            SchemaCapCoverage._walk(sub, f"{path}.{key}" if path else key, capped, uncapped)
        if isinstance(node.get("items"), dict):
            SchemaCapCoverage._walk(node["items"], path + "[]", capped, uncapped)
        if isinstance(node.get("additionalProperties"), dict):
            SchemaCapCoverage._walk(node["additionalProperties"], path + ".*", capped, uncapped)
        for i, branch in enumerate(node.get("oneOf", [])):
            SchemaCapCoverage._walk(branch, f"{path}<oneOf{i}>", capped, uncapped)

    def test_every_free_text_string_field_declares_a_cap(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        capped, uncapped = set(), set()
        self._walk(schema, "", capped, uncapped)
        self.assertEqual(uncapped, PATH_FIELDS_WITHOUT_CAP)
        self.assertGreater(len(capped), 40)
        for field, cap in (
            ("completed_outcomes[].note", 500),
            ("open_questions[].question", 500),
            ("open_questions[].status", 200),
            ("gate_approval_records[].decision", 2000),
            ("completed_outcomes[].evidence[]", 300),
            ("instance_roster[].note", 300),
            ("scheduling_holds[].predicted_surface[]", 300),
        ):
            with self.subTest(field=field):
                self.assertIn(field, capped)
                node = schema
                for part in field.replace("[]", ".[]").split("."):
                    node = node["items"] if part == "[]" else node["properties"][part]
                self.assertEqual(node["maxLength"], cap)

    def test_notes_is_the_only_unconstrained_escape_hatch(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        notes = schema["properties"]["notes"]
        self.assertEqual(set(notes), {"description"})
        self.assertIn("Intentionally unconstrained", notes["description"])
        self.assertIn("131072", notes["description"])
        self.assertIn("131072", schema["description"])


if __name__ == "__main__":
    unittest.main()
