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

# path (repo-relative) -> (max lines, max bytes). Pinned 2026-09-03 (2.5.0):
# deliberate growth for the update path (four nodes, ten edges, two seats) and
# the ledger contract; pave-material-reviewer shrank through the shared fragment.
# 2026-09-09: stop-hook question 7 (plain words to the user) grows the hooks
# reference by one template question; the spec grows 24 bytes for the same rule.
# 2026-09-09: the goal-restatement hook (SessionStart resume|compact +
# SubagentStart) adds one template and one table row to the hooks reference,
# one enforcement-record sentence and one mapping bullet to SKILL.md, and one
# tree line to the README; review then added the template's run-state fallback
# line and the restatement's decline path and test invariant to the reference.
# 2026-09-09 (2.5.4): the spec gains the working-state rule in 8.4, the
# instrument-by-commit rewrite of 9.10 and the digest-subject rule 9.14.3;
# SKILL.md gains the evidence-bound and digest mapping bullets; the revisions,
# briefs, reviewer and planner texts each gain one clause of the same rule.
# 2026-09-10 (2.6.0): the audit checkpoint adds its branch, sidecar, write log
# and two write guards to the hooks reference, the binding-revision clause to
# pave-revisions, audit mode to the updater, item 19 to the reviewer, and the
# land flags to pave-evolve; SKILL.md gains the hook-pair sentence.
# 2026-09-11 (2.6.1): the no-strand re-cut rule's declared scope gains shell
# commands and the parked-marker sibling in the hooks reference.
CEILINGS = {
    "skills/pave-init/SKILL.md": (267, 38092),
    "skills/pave-init/README.md": (448, 26136),
    "skills/pave-evolve/SKILL.md": (72, 8744),
    "skills/pave-init/references/approval-briefs.md": (72, 9502),
    "skills/pave-init/references/lead-alignment-hooks.md": (479, 47944),
    "skills/pave-init/references/pave-composition.md": (186, 10814),
    "skills/pave-init/references/pave-init-traceability.md": (169, 21886),
    "skills/pave-init/references/pave-revisions.md": (81, 16811),
    "skills/pave-init/references/pave-spec.md": (1723, 76571),
    "skills/pave-init/references/pave-yaml.md": (431, 17816),
    "skills/pave-init/references/planning-layout.md": (52, 4116),
    "skills/pave-init/references/technique-selection.md": (202, 9954),
    "skills/pave-init/orchestration/explore-and-plan.md": (174, 15313),
    "skills/pave-init/orchestration/interview-and-fitness.md": (131, 6948),
    "skills/pave-init/orchestration/review-and-build.md": (134, 14400),
    "sources/fragments/reviewer-core.md": (21, 3264),
    "sources/roles/forward-tester.md.tmpl": (21, 1783),
    "sources/roles/node-planner.md.tmpl": (72, 12800),
    "sources/roles/pave-material-reviewer.md.tmpl": (95, 15858),
    "sources/roles/research-delegate.md.tmpl": (63, 2682),
    "sources/roles/skill-builder.md.tmpl": (44, 4700),
    "sources/roles/system-explorer.md.tmpl": (23, 1536),
    "sources/roles/update-reviewer.md.tmpl": (73, 8984),
    "sources/roles/workflow-updater.md.tmpl": (77, 11158),
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
