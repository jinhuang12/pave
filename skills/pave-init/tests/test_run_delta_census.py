#!/usr/bin/env python3
"""Regression tests for scripts/run_delta_census.py.

Builds a tmp run tree: a fake run state, an audit checkpoint sidecar, a write
log with two generations, a graph, a decisions file, a lead-log file and a memory
dir; then asserts every output field of the census, the 15-line summary plus its
excerpts, the numbered-artifact families, read-only write-log records, default
discovery of the approvals inputs, hash-suffixed re-cuts against the strict
`<stem>-rN.<ext>` sub-count, the top-40 bound on the two text scans, the no-sidecar
case, the regex node reader, the read-only promise, and the 2 s budget on a
10k-record log.

The artifact names follow a real run: numbered (`039-grant-...md`), so the family
is the token after the sequence number.

Run: python3 skills/pave-init/tests/test_run_delta_census.py  (stdlib only)
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "run_delta_census.py"

import run_delta_census as census_mod  # noqa: E402

CP_AT = "2026-09-10T10:00:00Z"
BEFORE = "2026-09-10T09:00:00Z"
AFTER = "2026-09-10T11:00:00Z"
LATER = "2026-09-10T12:00:00Z"

GRAPH_MAPPING = """pave:
  version: 0.3.0
  name: census_fixture
  nodes:
    scope_next_increment:
      intent: execute
      purpose: pick one
    review_increment:
      intent: judge
      purpose: check it
  edges:
  - id: scope_to_review
    from: scope_next_increment
    to: review_increment
"""

GRAPH_LIST = """pave:
  nodes:
  - id: scope_next_increment
    intent: execute
  - id: review_increment
    intent: judge
  edges:
  - id: scope_to_review
"""

DECISIONS = """# Decisions

## §1 Kickoff
Nothing about files here.

## §2 Build increments as scripts
The lead cuts each build as `build-NNN.py` and re-cuts as build-NNN-r2.py.
Second line of the section.
Third line of the section.
Fourth line must not appear.

## §3 Grants
Every hardware run needs a grant file under leases/.
"""

LEAD_LOG = """§100 lane report
§101 the reviewer caught a defect in build-017
§102 fix landed in build-017-r2.py
§103 unrelated
§104 unrelated
§105 unrelated
§106 grant 019 issued
§107 unrelated
§108 unrelated
§109 red result on grant 020, see leases
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def row(node: str, note: str = "ok") -> dict:
    return {"node": node, "outcome": "done", "at": BEFORE, "evidence": [], "note": note}


def rec(at: str, path: str, agent_type=None, tool="Write", written=None) -> str:
    record = {"at": at, "session_id": "s1", "agent_id": None if agent_type is None else "a1",
              "agent_type": agent_type, "tool": tool, "path": path}
    if written is not None:
        record["written"] = written
        record["size"] = 12
        record["mtime"] = 1789000000.0
    return json.dumps(record)


class CensusTree:
    """One tmp run tree per test."""

    def __init__(self) -> None:
        # resolve(): macOS puts tmp under a /var -> /private/var symlink; the census resolves --state
        self.tmp = Path(tempfile.mkdtemp(prefix="census-")).resolve()
        self.ws = self.tmp / "artifacts"
        self.run = self.ws / "run"
        self.state = self.run / "run-state.json"
        self.evo = self.tmp / "evolution"
        self.external = self.tmp / "scratch" / "opusaudit"
        self.memory = self.tmp / "memory"
        self.decisions = self.ws / "approvals" / "DECISIONS.md"
        self.log = self.ws / "approvals" / "lead-log.md"
        # the locations the census discovers when no flag names them
        self.campaign_decisions = self.ws / "campaigns" / "c1" / "approvals" / "DECISIONS.md"
        self.campaign_log = self.ws / "campaigns" / "c1" / "approvals" / "lead-log.md"

        write(self.evo / "workflow.pave.yaml", GRAPH_MAPPING)
        outcomes = [
            row("scope_next_increment"),
            row("review_increment"),
            row("lead", "x" * 600),                 # pseudo-row: never counts, note over cap
            row("scope_next_increment", "y" * 501),  # declared, note over cap
            row("not_a_node"),                       # undeclared: never counts
            row("review_increment"),
            row("scope_next_increment"),
        ]
        write(self.state, json.dumps({"completed_outcomes": outcomes, "notes": []}))
        write(
            Path(str(self.state) + ".audit-checkpoint.json"),
            json.dumps({"checkpoint_id": "cp-20260910T100000Z", "at": CP_AT, "outcomes_at": 2,
                        "bytes_at": 10, "state": "DUE"}),
        )
        # files the log names
        write(self.ws / "campaigns" / "increments" / "build-017.py", "a" * 100)
        write(self.ws / "campaigns" / "increments" / "build-017-r2.py", "b" * 200)
        write(self.ws / "campaigns" / "increments" / "check-043-r3-20260910T120000Z.out", "c" * 50)
        write(self.ws / "leases" / "039-grant-trn2-lease.md", "d" * 10)
        write(self.external / "note.txt", "e" * 7)
        write(self.ws / "070-release-earlier.txt", "f" * 1000)
        old = [rec(BEFORE, str(self.ws / "070-release-earlier.txt")),
               rec(AFTER, str(self.ws / "campaigns" / "increments" / "build-017.py"))]
        live = [
            rec(AFTER, str(self.ws / "campaigns" / "increments" / "build-017-r2.py"), "pave-init:implementer"),
            rec(AFTER, str(self.ws / "campaigns" / "increments" / "build-017-r2.py"), "pave-init:implementer"),
            rec(LATER, str(self.ws / "campaigns" / "increments" / "check-043-r3-20260910T120000Z.out"),
                "vllm-neuron-parity:implementer", "Bash"),
            rec(LATER, str(self.ws / "leases" / "039-grant-trn2-lease.md")),
            rec(LATER, str(self.external / "note.txt"), None, "Bash"),
            rec(LATER, str(self.ws / "118-plan-block-proposal.md")),
            rec(LATER, "/tmp/stray.txt", None, "Bash"),
            "not json at all",
        ]
        write(Path(str(self.state) + ".write-log.1.jsonl"), "\n".join(old) + "\n")
        write(Path(str(self.state) + ".write-log.jsonl"), "\n".join(live) + "\n")
        write(self.decisions, DECISIONS)
        write(self.log, LEAD_LOG)
        write(self.campaign_decisions, DECISIONS)
        write(self.campaign_log, LEAD_LOG)
        write(self.memory / "old.md", "old")
        write(self.memory / "new.md", "new")
        stale = census_mod.parse_at(BEFORE)
        os.utime(self.memory / "old.md", (stale, stale))
        fresh = census_mod.parse_at(LATER)
        os.utime(self.memory / "new.md", (fresh, fresh))

    def args(self, *extra: str) -> list[str]:
        return ["--state", str(self.state), "--evolution-root", str(self.evo),
                "--memory-dir", str(self.memory), "--log", str(self.log),
                "--decisions", str(self.decisions), *extra]

    def bare(self, *extra: str) -> list[str]:
        """No --log / --decisions unless the caller adds them."""
        return ["--state", str(self.state), "--evolution-root", str(self.evo), *extra]

    def listing(self) -> list[tuple[str, int, float]]:
        out = []
        for dirpath, _d, files in os.walk(self.tmp):
            for f in files:
                p = Path(dirpath) / f
                st = p.stat()
                out.append((str(p), st.st_size, st.st_mtime))
        return sorted(out)


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class TestCensusJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tree = CensusTree()
        cls.before = cls.tree.listing()
        proc = run_cli(cls.tree.args("--json", "--since-checkpoint"))
        assert proc.returncode == 0, proc.stderr
        cls.out = json.loads(proc.stdout)
        cls.after = cls.tree.listing()

    def test_read_only(self):
        self.assertEqual(self.before, self.after)

    def test_paths_and_nodes(self):
        self.assertEqual(self.out["workspace_root"], str(self.tree.ws))
        self.assertEqual(self.out["graph"], str(self.tree.evo / "workflow.pave.yaml"))
        self.assertEqual(self.out["declared_nodes"], 2)
        self.assertIn(self.out["node_source"], ("pyyaml", "regex"))

    def test_checkpoint(self):
        cp = self.out["checkpoint"]
        self.assertEqual(cp["checkpoint_id"], "cp-20260910T100000Z")
        self.assertEqual(cp["at"], CP_AT)
        self.assertEqual(cp["outcomes_at"], 2)
        self.assertTrue(self.out["since_checkpoint"])

    def test_outcomes_since_declared_only(self):
        # 5 declared rows total (lead + not_a_node excluded), 2 at checkpoint -> 3 since
        self.assertEqual(self.out["outcomes"]["rows_total"], 7)
        self.assertEqual(self.out["outcomes"]["declared_total"], 5)
        self.assertEqual(self.out["outcomes"]["since"], 3)

    def test_files_since(self):
        fs = self.out["files_since"]
        # records after CP_AT: 1 from the rotated file + 7 from the live file (bad line dropped)
        self.assertEqual(fs["records"], 8)
        self.assertEqual(fs["distinct"], 7)          # build-017-r2.py logged twice
        self.assertEqual(fs["existing"], 5)          # 118-plan-block-proposal.md and /tmp/stray.txt absent
        self.assertEqual(fs["read_only"], 0)         # no record says written: false
        # numbered names: the family is the token after the sequence number
        self.assertEqual(fs["by_family"],
                         {"build": 2, "check": 1, "grant": 1, "note": 1, "plan": 1, "stray": 1})
        self.assertEqual(fs["by_agent_type"],
                         {"lead": 5, "pave-init:implementer": 1, "vllm-neuron-parity:implementer": 1})
        self.assertEqual(fs["by_root"][str(self.tree.ws)], 5)
        self.assertEqual(fs["by_root"][str(self.tree.external)], 1)
        self.assertEqual(fs["by_root"]["/tmp"], 1)
        self.assertEqual(
            [os.path.basename(p) for p in fs["lap_suffixed"]],
            ["build-017-r2.py", "check-043-r3-20260910T120000Z.out"],
        )
        self.assertEqual(fs["lap_suffixed_strict"], 1)  # only build-017-r2.py is <stem>-rN.<ext>

    def test_bytes_since(self):
        # build-017.py 100 + build-017-r2.py 200 + check 50 + grant 10 + note 7;
        # 070-release-earlier.txt is logged before the checkpoint `at`
        self.assertEqual(self.out["bytes_since"], 367)

    def test_inputs_record_the_files_read(self):
        # the flags win over discovery when they are given
        self.assertEqual(self.out["inputs"],
                         {"decisions": str(self.tree.decisions), "log": str(self.tree.log)})

    def test_run_state(self):
        self.assertEqual(self.out["run_state"]["bytes"], self.tree.state.stat().st_size)
        self.assertEqual(self.out["run_state"]["notes_over_500"], 2)

    def test_scan_roots_never_bare_tmp(self):
        roots = self.out["scan_roots"]
        self.assertIn(str(self.tree.ws), roots)
        self.assertIn(str(self.tree.external), roots)
        self.assertNotIn("/tmp", roots)
        self.assertNotIn("/private/tmp", roots)
        tmpdir = os.environ.get("TMPDIR")
        if tmpdir:
            self.assertNotIn(tmpdir.rstrip("/"), roots)

    def test_memory_changed(self):
        self.assertEqual(self.out["memory_changed"], [str(self.tree.memory / "new.md")])

    def test_cited_by(self):
        # "build" appears on §101 (caught/defect line) and §102 (within 3 lines);
        # "grant" on §106 (3 lines before §109 "red") and §109; "check"/"note" never.
        self.assertEqual(self.out["cited_by"], {"build": 2, "grant": 2})

    def test_creating_sections(self):
        cs = self.out["creating_sections"]
        self.assertEqual(cs["build"], [
            "## §2 Build increments as scripts",
            "The lead cuts each build as `build-NNN.py` and re-cuts as build-NNN-r2.py.",
            "Second line of the section.",
            "Third line of the section.",
        ])
        self.assertEqual(cs["grant"][0], "## §3 Grants")
        self.assertEqual(len(cs["grant"]), 2)
        self.assertNotIn("check", cs)

    def test_trend(self):
        self.assertAlmostEqual(self.out["trend"]["bytes_per_outcome"], 367 / 3, places=1)


class TestCensusSummaryAndVariants(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = CensusTree()

    def test_plain_summary_15_lines_exit_0(self):
        proc = run_cli(self.tree.args())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.rstrip("\n").splitlines()
        summary_lines = [line for line in lines if not line.startswith("  ")]
        self.assertLessEqual(len(summary_lines), 15)
        self.assertTrue(lines[0].startswith("census: "))
        self.assertIn("cp-20260910T100000Z", proc.stdout)
        self.assertIn("declared outcomes since: 3 of 5", proc.stdout)
        self.assertIn("bytes since: 367", proc.stdout)
        self.assertIn("0 read-only", proc.stdout)
        self.assertIn("trend: 122.3 bytes per declared outcome", proc.stdout)

    def test_plain_output_carries_excerpts_and_cited_by(self):
        """The DUE brief is this plain output, so the evidence prints under the counts."""
        proc = run_cli(self.tree.args())
        excerpt = [line for line in proc.stdout.splitlines() if line.startswith("  ")]
        self.assertEqual(excerpt[0],
                         "  build: ## §2 Build increments as scripts | The lead cuts each build as"
                         " `build-NNN.py` and re-cuts as build-NNN-r2.py.")
        self.assertEqual(excerpt[1], "  grant: ## §3 Grants | Every hardware run needs a grant file under leases/.")
        self.assertEqual(excerpt[-1], "  cited-by: build=2, grant=2")
        self.assertLessEqual(len(excerpt), 9)
        for line in excerpt:
            self.assertLessEqual(len(line), 200)

    def test_decisions_alone_feeds_both_scans(self):
        """--decisions without --log: cited-by falls back to the decision record; no campaign
        path is ever guessed (a campaigns/*/approvals pair on disk is not read)."""
        proc = run_cli(self.tree.bare("--json", "--decisions", str(self.tree.decisions)))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["inputs"], {"decisions": str(self.tree.decisions),
                                         "log": str(self.tree.decisions)})
        self.assertIn("build", out["creating_sections"])
        plain = run_cli(self.tree.bare("--decisions", str(self.tree.decisions)))
        self.assertIn("  build: ## §2 Build increments as scripts | ", plain.stdout)

    def test_no_inputs_means_unattributed_and_says_so(self):
        proc = run_cli(self.tree.bare("--json"))
        out = json.loads(proc.stdout)
        self.assertEqual(out["inputs"], {"decisions": None, "log": None})
        self.assertEqual(out["cited_by"], {})
        self.assertEqual(out["creating_sections"], {})
        plain = run_cli(self.tree.bare())
        self.assertIn("cited-by: unattributed (no --log or --decisions given)", plain.stdout)
        plain = run_cli(self.tree.bare())
        self.assertEqual([line for line in plain.stdout.splitlines() if line.startswith("  ")], [])

    def test_read_only_records_are_excluded(self):
        """`written: false` means the hook stat'ed the path and no write landed: the
        record is reported as read_only and counted nowhere else."""
        probe = self.tree.ws / "campaigns" / "increments" / "probe-900.py"
        write(probe, "p" * 400)
        log = Path(str(self.tree.state) + ".write-log.jsonl")
        lines = log.read_text(encoding="utf-8").rstrip("\n").splitlines()
        lines.append(rec(LATER, str(probe), None, "Bash", written=False))
        lines.append(rec(LATER, str(self.tree.ws / "probe-901-absent.py"),
                         "pave-init:implementer", "Bash", written=False))
        lines.append(rec(LATER, str(self.tree.ws / "leases" / "039-grant-trn2-lease.md"),
                         None, "Write", written=True))
        write(log, "\n".join(lines) + "\n")
        proc = run_cli(self.tree.args("--json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        fs = json.loads(proc.stdout)["files_since"]
        self.assertEqual(fs["read_only"], 2)
        self.assertEqual(fs["records"], 9)   # the 8 counted before plus the written: true one
        self.assertEqual(fs["distinct"], 7)  # the grant lease was already logged
        self.assertNotIn("probe", fs["by_family"])
        self.assertEqual(fs["by_agent_type"].get("pave-init:implementer"), 1)
        self.assertEqual(json.loads(proc.stdout)["bytes_since"], 367)  # probe-900.py's 400 bytes never count

    def test_no_sidecar_counts_everything(self):
        Path(str(self.tree.state) + ".audit-checkpoint.json").unlink()
        proc = run_cli(self.tree.args("--json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIsNone(out["checkpoint"])
        self.assertFalse(out["since_checkpoint"])
        self.assertEqual(out["outcomes"]["since"], 5)
        self.assertEqual(out["files_since"]["records"], 9)
        self.assertEqual(out["bytes_since"], 1367)
        self.assertEqual(len(out["memory_changed"]), 2)

    def test_graph_override_list_form(self):
        alt = self.tree.tmp / "alt.pave.yaml"
        write(alt, GRAPH_LIST)
        proc = run_cli(self.tree.args("--json", "--graph", str(alt)))
        out = json.loads(proc.stdout)
        self.assertEqual(out["declared_nodes"], 2)
        self.assertEqual(out["graph"], str(alt))

    def test_regex_fallback_matches_both_forms(self):
        real_import = builtins.__import__

        def no_yaml(name, *a, **k):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *a, **k)

        builtins.__import__ = no_yaml
        try:
            ids, src = census_mod.declared_nodes(self.tree.evo / "workflow.pave.yaml")
            alt = self.tree.tmp / "alt.pave.yaml"
            write(alt, GRAPH_LIST)
            ids2, src2 = census_mod.declared_nodes(alt)
        finally:
            builtins.__import__ = real_import
        self.assertEqual((src, src2), ("regex", "regex"))
        self.assertEqual(ids, ["scope_next_increment", "review_increment"])
        self.assertEqual(ids2, ["scope_next_increment", "review_increment"])

    def test_missing_graph_declares_nothing(self):
        proc = run_cli(self.tree.args("--json", "--graph", str(self.tree.tmp / "absent.yaml")))
        out = json.loads(proc.stdout)
        self.assertEqual(out["node_source"], "missing")
        self.assertEqual(out["outcomes"]["since"], 0)

    def test_helpers(self):
        self.assertEqual(census_mod.family_of("/x/build-017-r2.py"), "build")
        self.assertEqual(census_mod.family_of("/x/run-state.json"), "run")
        self.assertEqual(census_mod.family_of("/x/README.md"), "README")
        self.assertEqual(census_mod.family_of("/x/.hidden-file"), ".hidden")
        # numbered artifacts: the sequence number is a counter, not a family
        self.assertEqual(census_mod.family_of("/x/039-grant-trn2-cache-warm.md"), "grant")
        self.assertEqual(census_mod.family_of("/x/070-release-notes.md"), "release")
        self.assertEqual(census_mod.family_of("/x/118-plan-block-proposal-r2.md"), "plan")
        self.assertEqual(census_mod.family_of("/x/12a-grant-followup.md"), "grant")
        self.assertEqual(census_mod.family_of("/x/007.txt"), "007")
        self.assertTrue(census_mod.counted({"path": "p"}))
        self.assertTrue(census_mod.counted({"path": "p", "written": True}))
        self.assertFalse(census_mod.counted({"path": "p", "written": False}))
        self.assertTrue(census_mod.is_lap_suffixed("a/build-1-r2.py"))
        self.assertTrue(census_mod.is_lap_suffixed("a/check-r10-20260910T120000Z.out"))
        self.assertTrue(census_mod.is_lap_suffixed("a/check-r10-20260910.tar.gz"))
        # a hash or a word after the lap is still a re-cut
        self.assertTrue(census_mod.is_lap_suffixed("a/launch-113-r5-a5b82c73.sh"))
        self.assertTrue(census_mod.is_lap_suffixed("a/plan-block-r2-final.md"))
        self.assertTrue(census_mod.is_lap_suffixed("a/build-017-r2"))
        self.assertFalse(census_mod.is_lap_suffixed("a/build-r2x.py"))
        self.assertFalse(census_mod.is_lap_suffixed("a/order-r.py"))
        # strict: the <stem>-rN.<ext> form the runtime no-recut guard matches
        self.assertTrue(census_mod.is_lap_strict("a/build-017-r2.py"))
        self.assertFalse(census_mod.is_lap_strict("a/launch-113-r5-a5b82c73.sh"))
        self.assertFalse(census_mod.is_lap_strict("a/plan-block-r2-final.md"))
        self.assertFalse(census_mod.is_lap_strict("a/check-r10-20260910.tar.gz"))
        self.assertFalse(census_mod.is_lap_strict("a/build-017-r2"))

    def test_hash_suffixed_recut_counts_as_a_lap_but_not_as_strict(self):
        """A launcher re-cut carries the source hash after the lap; it is still a re-cut."""
        launcher = self.tree.ws / "campaigns" / "increments" / "launch-113-r5-a5b82c73.sh"
        write(launcher, "l" * 20)
        log = Path(str(self.tree.state) + ".write-log.jsonl")
        lines = log.read_text(encoding="utf-8").rstrip("\n").splitlines()
        lines.append(rec(LATER, str(launcher), None, "Bash"))
        write(log, "\n".join(lines) + "\n")
        fs = json.loads(run_cli(self.tree.args("--json")).stdout)["files_since"]
        self.assertIn("launch-113-r5-a5b82c73.sh", [os.path.basename(p) for p in fs["lap_suffixed"]])
        self.assertEqual(len(fs["lap_suffixed"]), 3)
        self.assertEqual(fs["lap_suffixed_strict"], 1)

    def test_only_the_top_families_are_scanned_for_text(self):
        """The cited-by and creating-section scans are per family, so they stop at the
        top 40 by count: a family ranked below that is never looked up in either file."""
        lines = []
        for index in range(150):
            for copy_index in range(2 if index < 40 else 1):
                path = self.tree.ws / "campaigns" / f"{index:03d}-fam{index:03d}-item-{copy_index}.out"
                lines.append(rec(AFTER, str(path), None, "Bash"))
        write(Path(str(self.tree.state) + ".write-log.jsonl"), "\n".join(lines) + "\n")
        inside, outside = "fam005", "fam100"
        write(self.tree.log, f"§1 the reviewer caught a defect in {inside}\n§2 and one in {outside}\n")
        write(self.tree.decisions,
              f"## §1 About {inside}\nThe first line about it.\n\n## §2 About {outside}\nAnd about it.\n")
        out = json.loads(run_cli(self.tree.args("--json")).stdout)
        self.assertEqual(len(out["files_since"]["by_family"]), 151)  # + build from generation 1
        self.assertIn(inside, out["cited_by"])
        self.assertNotIn(outside, out["cited_by"])
        self.assertIn(inside, out["creating_sections"])
        self.assertNotIn(outside, out["creating_sections"])
        self.assertLessEqual(len(out["cited_by"]), 40)
        self.assertLessEqual(len(out["creating_sections"]), 40)

    def test_ten_thousand_records_under_two_seconds(self):
        lines = []
        for i in range(10_000):
            p = self.tree.ws / "campaigns" / f"{i:03d}-fam{i % 50}-item.out"
            if i % 10 == 0:
                write(p, "z" * (i % 300))
            lines.append(rec(AFTER, str(p), f"agent-{i % 7}", "Bash"))
        write(Path(str(self.tree.state) + ".write-log.jsonl"), "\n".join(lines) + "\n")
        start = time.monotonic()
        proc = run_cli(self.tree.args("--json"))
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["files_since"]["records"], 10_001)  # + build-017.py from generation 1
        self.assertEqual(len(out["files_since"]["by_family"]), 51)
        self.assertLess(elapsed, 2.0, f"census took {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main(verbosity=1)
