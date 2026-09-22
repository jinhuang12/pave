"""Tests for scripts/process_vocabulary_scan.py — changeset component six."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "process_vocabulary_scan.py"
sys.path.insert(0, str(SCRIPT.parent))

import process_vocabulary_scan as pvs  # noqa: E402

BASE_KERNEL = (
    "# lane 0 = position 2k, lane 1 = position 2k+1\n"
    "def pack(tokens):\n"
    "    # OOB-skipped lanes fall through the pre-increment path\n"
    "    return tokens  # stride-128 blocks, release the lock afterwards\n"
)
TIP_CLEAN = BASE_KERNEL + "    # increments the counter by one, ruling out a stale read\n"
TIP_RESIDUE = (
    "# plan block 14 said the seat owns this lease\n"
    "def attention():\n"
    "    return 0  # campaign inc-glm53f-045, round 2, attempt 9\n"
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def make_repo(tmp: Path, tip_files: dict[str, str], remove_line: bool = False) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    (repo / "kernel.py").write_text(BASE_KERNEL + ("# grant 7 to be removed\n" if remove_line else ""))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "tag", "base")
    for name, text in tip_files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    if tip_files:
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "tip")
    return repo


def run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--base", "base", "--tip", "HEAD", *extra],
        capture_output=True,
        text=True,
    )


class ProcessVocabularyScanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_control_line_fires_every_pattern(self) -> None:
        regexes = pvs.compile_patterns()
        self.assertEqual(pvs.control_fires(regexes), len(regexes))

    def test_base_vocabulary_does_not_hit(self) -> None:
        """Kernel words at the base (lane, pre-increment, stride-128 blocks, release) stay clean."""
        repo = make_repo(self.tmp, {"kernel.py": TIP_CLEAN})
        proc = run(repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("0 hits over 1 added lines", proc.stdout)  # "ruling out" is not the process noun
        self.assertIn("control fired", proc.stdout)

    def test_process_residue_hits_and_exits_one(self) -> None:
        repo = make_repo(self.tmp, {"attn.py": TIP_RESIDUE})
        proc = run(repo, "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        record = json.loads(proc.stdout)
        matched = {h["match"].lower() for h in record["hits"]}
        self.assertEqual(matched, {"plan block", "inc-glm53f-045"})  # one hit per line, first pattern wins
        self.assertEqual(record["files"], 1)
        self.assertEqual(record["added_paths"], 1)
        self.assertEqual(record["control_fired"], f"{len(pvs.PATTERNS)} of {len(pvs.PATTERNS)} patterns")
        self.assertEqual({h["line"] for h in record["hits"]}, {1, 3})

    def test_added_paths_are_read_as_words(self) -> None:
        """`test_campaign_pins.py` and `inc-glm53f-045/` hit on the path alone; clean content."""
        clean = "def test_ok():\n    assert True\n"
        repo = make_repo(self.tmp, {"test/test_campaign_pins.py": clean, "inc-glm53f-045/probe.py": clean})
        proc = run(repo, "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        hits = json.loads(proc.stdout)["hits"]
        self.assertEqual(sorted((h["path"], h["line"], h["match"]) for h in hits),
                         [("inc-glm53f-045/probe.py", 0, "inc-glm53f-045"), ("test/test_campaign_pins.py", 0, "campaign")])

    def test_removed_lines_are_out_of_scope(self) -> None:
        """A process word on a removed line never counts; only tip-side added lines do."""
        repo = make_repo(self.tmp, {"kernel.py": TIP_CLEAN}, remove_line=True)
        proc = run(repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_renamed_path_is_scanned(self) -> None:
        """A file renamed into a process-named path hits on the path (diff filter A and R)."""
        repo = make_repo(self.tmp, {})
        git(repo, "mv", "kernel.py", "test_campaign_pins.py")
        git(repo, "commit", "-q", "-m", "rename")
        proc = run(repo, "--json")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertEqual([(h["path"], h["line"]) for h in json.loads(proc.stdout)["hits"]], [("test_campaign_pins.py", 0)])

    def test_license_text_is_skipped(self) -> None:
        repo = make_repo(self.tmp, {"vendor/LICENSE": "a. Grant of Rights. You are granted a license.\n"})
        proc = run(repo)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_paths_limit_the_diff(self) -> None:
        repo = make_repo(self.tmp, {"attn.py": TIP_RESIDUE, "docs/notes.md": "clean\n"})
        proc = run(repo, "--paths", "docs")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_broken_control_refuses_with_exit_two(self) -> None:
        regexes = pvs.compile_patterns(("\\bnever-in-control\\b",))
        self.assertEqual(pvs.control_fires(regexes), 0)
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import process_vocabulary_scan as p; "
             "p.PATTERNS = ('\\\\bnever-in-control\\\\b',); p.compile_patterns.__defaults__ = (p.PATTERNS,); "
             "sys.exit(p.main(['--repo', '.', '--base', 'HEAD~1']))" % str(SCRIPT.parent)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("control did not fire", proc.stderr)
        empty = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import process_vocabulary_scan as p; "
             "p.compile_patterns.__defaults__ = ((),); sys.exit(p.main(['--repo', '.', '--base', 'HEAD~1']))" % str(SCRIPT.parent)],
            capture_output=True, text=True,
        )
        self.assertEqual(empty.returncode, 2, empty.stderr)  # an empty list is not a firing control

    def test_list_prints_the_word_list(self) -> None:
        proc = subprocess.run([sys.executable, str(SCRIPT), "--list"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("lane", proc.stdout)
        self.assertIn("campaign", proc.stdout)


if __name__ == "__main__":
    unittest.main()
