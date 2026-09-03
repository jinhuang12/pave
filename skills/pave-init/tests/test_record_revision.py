#!/usr/bin/env python3
"""Regression tests for scripts/record_revision.py.

Covers the ledger contract end to end on a tiny valid PAVE graph: init, a graph
landing, a binding landing that moves the digest, pin routing (current, graph
landed, binding landed), the unrecorded-edit and interrupted-landing failures,
rollback, symlink rejection, and install.

Run: python3 skills/pave-init/tests/test_record_revision.py
Needs pyyaml and jsonschema (the landing path validates the graph through
scripts/validate_pave.py) plus the git command-line tool; skips without them.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "record_revision.py"
GRAPH = """pave:
  version: "0.3.0"
  name: record_revision_fixture
  status: draft
  purpose: Do one unit of work and record the honest result.
  entrypoints: [do_work]

  roles:
    lead:
      purpose: Execute the work and record the honest result.

  evidence: {}
  checks:
    result_is_recorded:
      style: mechanical
      question: Does the recorded result match the evidence the run produced?
      evaluated_by: lead
      on_failure_route: rejected

  nodes:
    do_work:
      intent: execute
      purpose: Produce the one result this workflow exists for.
      roles: [lead]
      outcomes:
        done: {}

  edges:
    - id: done_to_accepted
      from: do_work.done
      to: accepted
      checks: [result_is_recorded]

  control_endpoints:
    accepted:
      kind: terminal
      meaning: The result satisfies the declared purpose.
      terminal_status: accepted
    rejected:
      kind: terminal
      meaning: The result does not satisfy the declared purpose.
      terminal_status: closed_unaccepted

  state: {}
"""
PREAMBLE = {
    "kind": "graph",
    "semantic_diff": "State the work node's result in the terms the check reads.",
    "envelope_check": "unchanged",
    "plan_evidence": "verified",
    "usage_evidence": "none",
    "changelog_entry": "Sharpen the work node's purpose.",
}


def importable(name):
    try:
        __import__(name)
    except ImportError:
        return False
    return True


HAVE_DEPS = importable("yaml") and importable("jsonschema") and bool(shutil.which("git"))
if importable("yaml"):
    import record_revision as rr  # noqa: E402
    import yaml  # noqa: E402


def run(*args):
    """Run the script as a subprocess; return (exit code, stdout + stderr)."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args]],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


@unittest.skipUnless(HAVE_DEPS, "needs pyyaml, jsonschema, and git")
class RecordRevision(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pave-ledger-test-"))
        self.root = self.tmp / "root"
        self.root.mkdir()
        (self.root / "workflow.pave.yaml").write_text(GRAPH)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # helpers

    def init_root(self, root=None):
        rc, out = run("init", root or self.root, "--plan-evidence", "verified",
                      "--approval", "user: ship it")
        self.assertEqual(rc, 0, out)
        return out

    def ledger(self, root=None):
        path = (root or self.root) / "revisions.yaml"
        return yaml.safe_load(path.read_text())["entries"]

    def write_proposal(self, revision, replacements, **overrides):
        """Write history/vN.patch: a declared preamble plus a real unified diff."""
        scratch = Path(tempfile.mkdtemp(prefix="pave-proposal-"))
        for side in ("a", "b"):
            (scratch / side).mkdir()
        for path in sorted(self.root.glob("*.pave.yaml")):
            text = path.read_text()
            (scratch / "a" / path.name).write_text(text)
            for old, new in replacements.items():
                self.assertIn(old, text, f"{path.name} has no {old!r} to change")
                text = text.replace(old, new)
            (scratch / "b" / path.name).write_text(text)
        diff = subprocess.run(
            ["git", "diff", "--no-index", "--src-prefix=", "--dst-prefix=", "a", "b"],
            cwd=str(scratch), capture_output=True, text=True, check=False,
        ).stdout
        shutil.rmtree(scratch, ignore_errors=True)
        self.assertTrue(diff.strip(), "the replacements produced no diff")
        patch = self.root / "history" / f"v{revision}.patch"
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(yaml.safe_dump(dict(PREAMBLE, **overrides), sort_keys=False) + diff)
        return patch

    def land_graph_change(self, revision, replacements, **overrides):
        self.write_proposal(revision, replacements, **overrides)
        rc, out = run("land", self.root, revision, "--review", "material review: clean, 2 rounds")
        self.assertEqual(rc, 0, out)
        return out

    # cases

    def test_init_then_verify_passes(self):
        self.init_root()
        entry = self.ledger()[0]
        self.assertEqual(entry["revision"], 0)
        self.assertEqual(entry["kind"], "graph")
        self.assertIsNone(entry["digest_before"])
        self.assertIsNone(entry["patch"])
        self.assertIsNone(entry["semantic_diff"])
        self.assertIsNone(entry["review"])
        self.assertEqual(entry["approval"], "user: ship it")
        self.assertTrue(entry["digest_after"].startswith("sha256:"))
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS:", out)

    def test_bundle_digest_keeps_the_documented_formula(self):
        """sha256 over the sorted 'name\\0file-digest\\n' records — the 2.4 formula, kept so old digests stay comparable."""
        self.init_root()
        path = self.root / "workflow.pave.yaml"
        self.assertEqual(rr.file_digest(path),
                         "sha256:" + hashlib.sha256(GRAPH.encode()).hexdigest())
        record = f"workflow.pave.yaml\0{rr.file_digest(path)}\n"
        self.assertEqual(self.ledger()[0]["digest_after"],
                         "sha256:" + hashlib.sha256(record.encode()).hexdigest())

    def test_second_init_is_refused(self):
        self.init_root()
        rc, out = run("init", self.root, "--plan-evidence", "verified", "--approval", "again")
        self.assertEqual(rc, 1, out)
        self.assertIn("already exists", out)

    def test_graph_patch_proposes_lands_and_verifies(self):
        self.init_root()
        before = self.ledger()[0]["digest_after"]
        change = {"purpose: Produce the one result this workflow exists for.":
                  "purpose: Produce and record the one result this workflow exists for."}
        patch = self.write_proposal(1, change)
        rc, out = run("propose", self.root, "--patch", patch)
        self.assertEqual(rc, 0, out)
        self.assertIn("digest_after sha256:", out)
        self.assertIn("changelog_entry:", out)
        self.assertEqual(self.ledger()[0]["digest_after"], before, "propose touched the root")
        rc, out = run("land", self.root, 1, "--review", "material review: clean, 2 rounds")
        self.assertEqual(rc, 0, out)
        entry = self.ledger()[1]
        self.assertEqual(entry["revision"], 1)
        self.assertEqual(entry["kind"], "graph")
        self.assertEqual(entry["digest_before"], before)
        self.assertNotEqual(entry["digest_after"], before)
        self.assertEqual(entry["semantic_diff"], PREAMBLE["semantic_diff"])
        self.assertEqual(entry["envelope_check"], "unchanged")
        self.assertEqual(entry["plan_evidence"], "verified")
        self.assertEqual(entry["usage_evidence"], "none")
        self.assertEqual(entry["review"], "material review: clean, 2 rounds")
        self.assertEqual(entry["patch"], "history/v1.patch")
        self.assertNotIn("changelog_entry", entry)
        self.assertFalse((self.root / ".landing").exists())
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)

    def test_preamble_cannot_mint_tool_written_fields(self):
        """A proposer may declare its own six fields plus approval and review; commit, digests,
        derived_from and run_id are the tool's to write, so a preamble carrying one is refused."""
        self.init_root()
        change = {"purpose: Produce the one result this workflow exists for.":
                  "purpose: Produce and record the one result this workflow exists for."}
        self.write_proposal(1, change, commit="9f3c1e2")
        rc, out = run("land", self.root, 1, "--review", "material review: clean, 1 round")
        self.assertEqual(rc, 1, out)
        self.assertIn("may not set commit", out)
        self.assertEqual(len(self.ledger()), 1, "a refused proposal must not land")
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)

    def test_unrecorded_edit_fails_verify(self):
        self.init_root()
        path = self.root / "workflow.pave.yaml"
        path.write_text(path.read_text() + "# edited outside a landing\n")
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("unrecorded edit", out)

    def test_binding_patch_lands_as_binding_and_moves_the_digest(self):
        self.init_root()
        before = self.ledger()[0]["digest_after"]
        self.land_graph_change(
            1, {"style: mechanical": "style: reviewed"}, kind="binding",
            semantic_diff="Same check, a reviewed instrument; no graph meaning changes.",
            changelog_entry="Move result_is_recorded to a reviewed instrument.",
        )
        entry = self.ledger()[1]
        self.assertEqual(entry["kind"], "binding")
        self.assertNotEqual(entry["digest_after"], before)
        self.assertIn("style: reviewed", (self.root / "workflow.pave.yaml").read_text())
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)

    def test_pin_then_verify_routes_on_what_landed_since(self):
        self.init_root()
        digest = self.ledger()[0]["digest_after"]
        rc, out = run("pin", self.root, "--run-id", "run-alpha")
        self.assertEqual(rc, 0, out)
        pin = self.ledger()[1]
        self.assertEqual(pin["kind"], "pin")
        self.assertEqual(pin["revision"], 0)
        self.assertEqual(pin["run_id"], "run-alpha")
        self.assertEqual((pin["digest_before"], pin["digest_after"]), (digest, digest))
        rc, out = run("verify", self.root, "--pinned-revision", 0, "--pinned-digest", digest)
        self.assertEqual(rc, 0, out)
        self.assertIn("PASS: current", out)
        self.land_graph_change(
            1, {"style: mechanical": "style: reviewed"}, kind="binding",
            semantic_diff="Same check, a reviewed instrument.",
            changelog_entry="Move result_is_recorded to a reviewed instrument.",
        )
        rc, out = run("verify", self.root, "--pinned-revision", 0, "--pinned-digest", digest)
        self.assertEqual(rc, 4, out)
        self.assertIn("ROUTE: binding landed since pin (revision 1)", out)
        self.land_graph_change(2, {"status: draft": "status: active"})
        rc, out = run("verify", self.root, "--pinned-revision", 0, "--pinned-digest", digest)
        self.assertEqual(rc, 3, out)
        self.assertIn("ROUTE: graph landed since pin (revision 2)", out)

    def test_stray_landing_marker_blocks_verify_and_pin(self):
        self.init_root()
        self.write_proposal(1, {"status: draft": "status: active"})
        (self.root / ".landing").write_text("1\n")
        for command in (("verify", self.root), ("pin", self.root, "--run-id", "run-alpha"),
                        ("land", self.root, 1)):
            rc, out = run(*command)
            self.assertEqual(rc, 1, out)
            self.assertIn("landing interrupted", out)

    def test_a_landing_that_fails_validation_restores_the_root(self):
        self.init_root()
        before = (self.root / "workflow.pave.yaml").read_text()
        ledger = self.ledger()
        self.write_proposal(1, {"      intent: execute": "      intent: execute\n      oops: x"})
        rc, out = run("land", self.root, 1)
        self.assertEqual(rc, 1, out)
        self.assertIn("does not validate", out)
        self.assertEqual((self.root / "workflow.pave.yaml").read_text(), before)
        self.assertEqual(self.ledger(), ledger)
        self.assertFalse((self.root / ".landing").exists())
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)

    def test_rollback_appends_a_derived_entry(self):
        self.init_root()
        target = self.ledger()[0]["digest_after"]
        self.land_graph_change(1, {"status: draft": "status: active"})
        rc, out = run("rollback", self.root, "--to", 0, "--approval", "user: revert it",
                      "--semantic-diff", "Restore revision 0: the successor broke the run.")
        self.assertEqual(rc, 0, out)
        entry = self.ledger()[2]
        self.assertEqual(entry["revision"], 2)
        self.assertEqual(entry["kind"], "graph")
        self.assertEqual(entry["derived_from"], 0)
        self.assertEqual(entry["digest_after"], target)
        self.assertEqual(entry["approval"], "user: revert it")
        self.assertEqual(entry["patch"], "history/v2.patch")
        self.assertIn("status: draft", (self.root / "workflow.pave.yaml").read_text())
        self.assertFalse((self.root / ".landing").exists())
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)

    def test_land_applies_the_patch_to_a_root_below_a_git_top_level(self):
        """A delivered package or a project's evolution root usually sits inside a
        git work tree below its top level. There `git apply` reads patch paths from
        the top level and silently skips files outside the current directory, so
        without re-anchoring, a landing appended an entry whose digest_after equalled
        digest_before and the graph never changed (vllm-neuron-parity 1.4.0)."""
        proc = subprocess.run(["git", "init", "-q", str(self.tmp)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.root = self.tmp / "generated" / "package"
        self.root.mkdir(parents=True)
        (self.root / "workflow.pave.yaml").write_text(GRAPH)
        self.init_root()
        before = self.ledger()[0]["digest_after"]
        self.land_graph_change(1, {"status: draft": "status: active"})
        self.assertIn("status: active", (self.root / "workflow.pave.yaml").read_text())
        entry = self.ledger()[1]
        self.assertNotEqual(entry["digest_after"], before)
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 0, out)
        rc, out = run("rollback", self.root, "--to", 0, "--approval", "user: revert it",
                      "--semantic-diff", "Restore revision 0.")
        self.assertEqual(rc, 0, out)
        self.assertIn("status: draft", (self.root / "workflow.pave.yaml").read_text())

    def test_symlinked_graph_file_fails(self):
        self.init_root()
        path = self.root / "workflow.pave.yaml"
        real = self.tmp / "elsewhere.yaml"
        real.write_text(path.read_text())
        path.unlink()
        path.symlink_to(real)
        rc, out = run("verify", self.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("symlink not allowed", out)

    def test_install_copies_a_package_root_and_verifies_it(self):
        self.init_root()
        self.land_graph_change(1, {"status: draft": "status: active"})
        target = self.tmp / "installed"
        rc, out = run("install", target, "--from", self.root)
        self.assertEqual(rc, 0, out)
        self.assertIn("installed revision 1", out)
        self.assertEqual(self.ledger(target), self.ledger())
        rc, out = run("verify", target)
        self.assertEqual(rc, 0, out)
        rc, out = run("install", target, "--from", self.root)
        self.assertEqual(rc, 1, out)
        self.assertIn("is not empty", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
