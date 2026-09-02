"""pave-init's own documents against pinned ceilings — a ratchet.

`references/pave-spec.md` section 8.4 binds pave-init itself: a spec that
only grows is the defect it warns generated workflows about. Every standing
document below has a (lines, bytes) ceiling pinned at its current size.

- A ceiling may fall freely: shrink a document, lower its ceiling.
- Raise a ceiling only in the same commit that needs the growth, so every
  growth is a deliberate decision recorded in the diff, never drift.
- A ceiling more than 10% above the document is stale — lower it.

`VERSION` is excluded on purpose: it is the append-only changelog, history
by design. Measurement comes from `scripts/measure_artifact.py`, the one
instrument every seat, hook, and test shares.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]  # repository root: skills/pave-init/tests -> repo
sys.path.insert(0, str(HERE.parent / "scripts"))

import measure_artifact as ma  # noqa: E402

# path (repo-relative) -> (max lines, max bytes). Pinned 2026-09-02 (2.4.2).
CEILINGS = {
    "skills/pave-init/SKILL.md": (266, 33459),
    "skills/pave-init/README.md": (395, 22230),
    "skills/pave-init/references/approval-briefs.md": (71, 9273),
    "skills/pave-init/references/lead-alignment-hooks.md": (362, 27555),
    "skills/pave-init/references/pave-composition.md": (186, 10814),
    "skills/pave-init/references/pave-init-traceability.md": (145, 17586),
    "skills/pave-init/references/pave-revisions.md": (88, 10207),
    "skills/pave-init/references/pave-spec.md": (1641, 71010),
    "skills/pave-init/references/pave-yaml.md": (431, 17816),
    "skills/pave-init/references/planning-layout.md": (52, 4116),
    "skills/pave-init/references/technique-selection.md": (202, 9954),
    "skills/pave-init/orchestration/explore-and-plan.md": (172, 14121),
    "skills/pave-init/orchestration/interview-and-fitness.md": (131, 6948),
    "skills/pave-init/orchestration/review-and-build.md": (134, 14096),
    "sources/roles/forward-tester.md.tmpl": (21, 1783),
    "sources/roles/node-planner.md.tmpl": (72, 12091),
    "sources/roles/pave-material-reviewer.md.tmpl": (115, 18473),
    "sources/roles/research-delegate.md.tmpl": (63, 2682),
    "sources/roles/skill-builder.md.tmpl": (44, 4624),
    "sources/roles/system-explorer.md.tmpl": (23, 1536),
}

SLACK = 0.10


class DocBudgetTests(unittest.TestCase):
    def measurements(self) -> list[tuple[str, dict]]:
        rows = []
        for rel, (max_lines, max_bytes) in CEILINGS.items():
            path = ROOT / rel
            self.assertTrue(path.is_file(), f"{rel} is missing — update CEILINGS when a document moves")
            rows.append((rel, ma.measure(path, cap_lines=max_lines, cap_bytes=max_bytes)))
        return rows

    def test_every_document_is_within_its_ceiling(self) -> None:
        over = [
            f"  {rel}: {r['lines']}/{r['cap_lines']} lines, {r['bytes']}/{r['cap_bytes']} bytes"
            for rel, r in self.measurements()
            if r["over_cap"]
        ]
        self.assertEqual(
            over,
            [],
            "over its pinned ceiling (pave-spec section 8.4). Shrink the document, or raise "
            "the ceiling in this same commit as a deliberate decision:\n" + "\n".join(over),
        )

    def test_ceilings_are_not_stale(self) -> None:
        stale = [
            f"  {rel}: {r['lines']} lines vs ceiling {r['cap_lines']}, {r['bytes']} bytes vs {r['cap_bytes']}"
            for rel, r in self.measurements()
            if r["lines"] < r["cap_lines"] * (1 - SLACK) or r["bytes"] < r["cap_bytes"] * (1 - SLACK)
        ]
        self.assertEqual(stale, [], "ceiling is more than 10% above the document — lower it:\n" + "\n".join(stale))


if __name__ == "__main__":
    unittest.main()
