"""Tests for scripts/measure_artifact.py — the one size instrument."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "measure_artifact.py"
sys.path.insert(0, str(SCRIPT.parent))

import measure_artifact as ma  # noqa: E402

DOC = (
    "# Plan\n"
    "\n"
    "## Scope\n"
    "one\n"
    "two\n"
    "## History\n"
    "Revision 12 superseded the DISCLOSED table; previously it read otherwise.\n"
)


class MeasureArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc = Path(self.tmp.name) / "plan.md"
        self.doc.write_text(DOC, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_counts_lines_bytes_sections_and_markers(self) -> None:
        result = ma.measure(self.doc)
        self.assertEqual(result["lines"], 7)
        self.assertEqual(result["bytes"], len(DOC.encode("utf-8")))
        self.assertEqual([s["heading"] for s in result["sections"]], ["Scope", "History"])
        self.assertEqual(result["sections"][0]["lines"], 3)
        self.assertEqual(result["sections"][1]["start_line"], 6)
        markers = result["narration_markers"]
        self.assertEqual(markers["revision_ref"], 1)
        self.assertEqual(markers["superseded"], 1)
        self.assertEqual(markers["disclosed"], 1)
        self.assertEqual(markers["previously"], 1)
        self.assertEqual(markers["until_now"], 0)
        self.assertEqual(len(result["sha256"]), 64)
        self.assertFalse(result["over_cap"])

    def test_over_cap_on_either_axis(self) -> None:
        self.assertTrue(ma.measure(self.doc, cap_lines=6)["over_cap"])
        self.assertTrue(ma.measure(self.doc, cap_bytes=10)["over_cap"])
        self.assertFalse(ma.measure(self.doc, cap_lines=7, cap_bytes=len(DOC.encode()))["over_cap"])

    def test_baseline_delta(self) -> None:
        base = Path(self.tmp.name) / "base.md"
        base.write_text("# Plan\n## Scope\none\n", encoding="utf-8")
        result = ma.measure(self.doc, baseline=base)
        self.assertEqual(result["baseline"]["delta_lines"], 4)
        self.assertIn("+4 lines", ma.render(result))

    def test_cli_strict_exit_and_json(self) -> None:
        ok = subprocess.run([sys.executable, str(SCRIPT), str(self.doc)], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0)
        self.assertIn("7 lines", ok.stdout)
        self.assertNotIn("OVER CAP", ok.stdout)

        over = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.doc), "--cap-lines", "3", "--strict"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(over.returncode, 1)
        self.assertIn("OVER CAP", over.stdout)

        lenient = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.doc), "--cap-lines", "3"], capture_output=True, text=True
        )
        self.assertEqual(lenient.returncode, 0)

        as_json = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.doc), "--json"], capture_output=True, text=True
        )
        record = json.loads(as_json.stdout)
        self.assertEqual(record["lines"], 7)
        self.assertEqual(record["cap_lines"], ma.DEFAULT_CAP_LINES)


if __name__ == "__main__":
    unittest.main()
