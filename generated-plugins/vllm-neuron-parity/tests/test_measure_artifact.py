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
        self.assertNotIn("sha256", result)
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

    def test_classify_python_and_shell(self) -> None:
        py = Path(self.tmp.name) / "mod.py"
        py.write_text(
            '#!/usr/bin/env python3\n'
            '"""Module docstring\n'
            'second line\n'
            '"""\n'
            '\n'
            '# a comment\n'
            'x = 1  # trailing comment counts as code\n'
            '\n'
            'def f():\n'
            '    """one-line docstring"""\n'
            '    return x\n',
            encoding="utf-8",
        )
        result = ma.classify(py)
        self.assertEqual(result["lines"], 11)
        self.assertEqual(result["docstring"], 4)
        self.assertEqual(result["comment"], 2)  # shebang + '# a comment'
        self.assertEqual(result["blank"], 2)
        self.assertEqual(result["code"], 3)
        self.assertEqual(result["header_lines"], 6)  # shebang, 3 docstring lines, blank, comment
        self.assertAlmostEqual(result["prose_share"], 6 / 11, places=3)

        sh = Path(self.tmp.name) / "run.sh"
        sh.write_text("#!/bin/bash\n# why\n\necho hi  # trailing\n", encoding="utf-8")
        result = ma.classify(sh)
        self.assertEqual((result["code"], result["comment"], result["blank"]), (1, 2, 1))
        self.assertEqual(result["header_lines"], 3)
        self.assertIn("prose share", ma.render_classify(result))

    def test_header_lines_docstring_edge_cases(self) -> None:
        raw = Path(self.tmp.name) / "raw.py"
        raw.write_text('r"""Regex helper."""  # note\n\nimport re\n', encoding="utf-8")
        self.assertEqual(ma.header_lines(raw), 2)  # prefixed one-line docstring + trailing comment, blank
        multi = Path(self.tmp.name) / "multi.py"
        multi.write_text("'''Doc\nmore'''  # trailing\nx = 1\n", encoding="utf-8")
        self.assertEqual(ma.header_lines(multi), 2)  # closing quote mid-line ends the block

    def test_tree_counts_laps_superseded_and_headers(self) -> None:
        inc = Path(self.tmp.name) / "increments"
        inc.mkdir()
        (inc / "accept-001-host-r1.sh").write_text("#!/bin/bash\n" + "# h\n" * 30 + "echo\n")
        (inc / "accept-001-host-r2.sh").write_text("#!/bin/bash\necho\n")
        (inc / "accept-001-host-r2.out").write_text("ran\n")
        (inc / "evidence-001.md").write_text("# e\n")
        (inc / "build-002-r4.py").write_text("print(1)\n")
        result = ma.tree(inc)
        self.assertEqual(result["files"], 5)
        self.assertEqual(result["lap_suffixed"], 4)   # r1.sh, r2.sh, r2.out, r4.py
        self.assertEqual(result["superseded"], 1)     # r1.sh below r2.sh; .out and .py stems stand alone
        self.assertEqual(result["header_lines_total"], 31 + 1 + 0)
        self.assertEqual(result["header_lines_median"], 1)
        self.assertEqual(result["by_ext"][".sh"], 2)
        self.assertIn("1 superseded", ma.render_tree(result))
        self.assertEqual(ma.tree(inc, since="2999-01-01")["files"], 0)

    def test_cli_classify_and_tree(self) -> None:
        inc = Path(self.tmp.name) / "inc"
        inc.mkdir()
        (inc / "a-r1.sh").write_text("echo\n")
        py = inc / "b.py"
        py.write_text("# c\nx = 1\n")
        out = subprocess.run([sys.executable, str(SCRIPT), "--classify", str(py)], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertIn("code 1, comment 1", out.stdout)
        out = subprocess.run([sys.executable, str(SCRIPT), "--tree", str(inc), "--json"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(json.loads(out.stdout)["lap_suffixed"], 1)
        two = subprocess.run([sys.executable, str(SCRIPT), "--tree", str(inc), str(py)], capture_output=True, text=True)
        self.assertNotEqual(two.returncode, 0)


if __name__ == "__main__":
    unittest.main()
