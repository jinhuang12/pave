#!/usr/bin/env python3
"""Every prose document in this package has a pinned line ceiling.

Why this test exists: the defects this workflow keeps finding in itself are
accretion defects, and a cap that lives only in prose is not a cap. The rule
is the same one the run applies to its own artifacts
(`references/artifact-layout.md` §4.12): growth is deliberate. So a release
that grows a document past its ceiling must raise the ceiling here, in the
same change, with the reason in the VERSION entry.

Two duties:
  1. No listed document exceeds its ceiling.
  2. Every prose document in the package is listed. A new reference or a new
     seat contract is unpinned until someone chooses its ceiling, and an
     unpinned document is a test failure, not a silent addition.

Run: python3 -m pytest tests/test_document_ceilings.py -q
"""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent

# path relative to the package root -> ceiling in lines.
CEILINGS: dict[str, int] = {
    "README.md": 700,
    "skills/vllm-neuron-parity/SKILL.md": 500,
    "references/artifact-layout.md": 500,
    "references/collision-ranking.md": 160,
    "references/measurement-pitfalls.md": 200,
    "references/patch-mechanism-inventory.md": 180,
    "references/toolchain-evidence-pitfalls.md": 400,
    "agents/adjudicator.md": 150,
    "agents/adversarial-reviewer.md": 240,
    "agents/implementer.md": 380,
    "agents/investigator.md": 200,
    "agents/measurer.md": 190,
    "agents/rederiver.md": 165,
}

# Globs that must be fully covered by CEILINGS.
COVERED = ("README.md", "references/*.md", "agents/*.md", "skills/*/SKILL.md")


def lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


class DocumentCeilingTests(unittest.TestCase):
    def test_no_document_exceeds_its_ceiling(self) -> None:
        over = []
        for rel, cap in sorted(CEILINGS.items()):
            path = ROOT / rel
            self.assertTrue(path.is_file(), f"pinned document missing: {rel}")
            count = lines(path)
            if count > cap:
                over.append(f"{rel}: {count} lines > ceiling {cap}")
        self.assertEqual(
            over,
            [],
            "raise the ceiling in this file, in the same change, with the "
            "reason in VERSION:\n  " + "\n  ".join(over),
        )

    def test_every_prose_document_is_pinned(self) -> None:
        found: set[str] = set()
        for pattern in COVERED:
            for path in ROOT.glob(pattern):
                found.add(path.relative_to(ROOT).as_posix())
        unpinned = sorted(found - set(CEILINGS))
        self.assertEqual(
            unpinned,
            [],
            f"unpinned prose document(s): {unpinned} — add a ceiling above",
        )
        stale = sorted(set(CEILINGS) - found)
        self.assertEqual(stale, [], f"ceiling for a document that is gone: {stale}")


if __name__ == "__main__":
    unittest.main()
