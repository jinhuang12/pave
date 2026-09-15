"""pave-init's own documents against pinned ceilings — a size_ceiling_test.

`references/pave-spec.md` section 8.4 binds pave-init itself: a spec that
only grows is the defect it warns generated workflows about. Every standing
document below has a (lines, bytes) ceiling pinned at its current size.

- A ceiling may fall freely: shrink a document, lower its ceiling.
- Raise a ceiling only in the same commit that needs the growth, so every
  growth is a deliberate decision recorded in the diff, never drift.
- A ceiling more than 10% above the document is stale — lower it.

`VERSION` is excluded on purpose: it is the append-only changelog, history
by design. Measurement comes from `scripts/measure_artifact.py`, the one
implementation every seat, hook, and test shares.
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
# the revision_log contract; pave-material-reviewer shrank through the shared node_draft.
# 2026-09-09: stop-hook question 7 (plain words to the user) grows the hooks
# reference by one template question; the spec grows 24 bytes for the same rule.
# 2026-09-09: the goal-restatement hook (SessionStart resume|compact +
# SubagentStart) adds one template and one table row to the hooks reference,
# one enforcement-record sentence and one mapping bullet to SKILL.md, and one
# tree line to the README; review then added the template's run-state fallback
# line and the restatement's decline path and test invariant to the reference.
# 2026-09-09 (2.5.4): the spec gains the working-state rule in 8.4, the
# implementation-by-commit rewrite of 9.10 and the digest-subject rule 9.14.3;
# SKILL.md gains the evidence-bound and digest mapping bullets; the revisions,
# briefs, reviewer and planner texts each gain one clause of the same rule.
# 2026-09-10 (2.6.0): the audit checkpoint adds its branch, sidecar, write log
# and two write guards to the hooks reference, the binding-revision clause to
# pave-revisions, audit mode to the updater, item 19 to the reviewer, and the
# apply flags to pave-evolve; SKILL.md gains the hook-pair sentence.
# 2026-09-11 (2.6.1): the always-fixable retry copy rule's declared scope gains shell
# commands and the parked-marker sibling in the hooks reference.
CEILINGS = {
    "skills/pave-init/SKILL.md": (267, 38031),
    "skills/pave-init/README.md": (448, 26027),
    "skills/pave-evolve/SKILL.md": (65, 8508),
    "skills/pave-init/references/approval-briefs.md": (72, 9498),
    "skills/pave-init/references/lead-hooks.md": (479, 47886),
    "skills/pave-init/references/pave-composition.md": (186, 10792),
    "skills/pave-init/references/pave-init-traceability.md": (169, 21888),
    "skills/pave-init/references/pave-revisions.md": (81, 16744),
    "skills/pave-init/references/pave-spec.md": (1720, 76427),
    "skills/pave-init/references/pave-yaml.md": (431, 17721),
    "skills/pave-init/references/planning-layout.md": (50, 4066),
    "skills/pave-init/references/technique-selection.md": (202, 9904),
    "skills/pave-init/orchestration/explore-and-plan.md": (174, 15273),
    "skills/pave-init/orchestration/interview-and-fitness.md": (131, 6946),
    "skills/pave-init/orchestration/review-and-build.md": (134, 14386),
    "sources/fragments/reviewer-core.md": (21, 3261),
    "sources/roles/forward-tester.md.tmpl": (21, 1783),
    "sources/roles/node-planner.md.tmpl": (72, 12795),
    "sources/roles/pave-material-reviewer.md.tmpl": (91, 15549),
    "sources/roles/research-delegate.md.tmpl": (58, 2422),
    "sources/roles/skill-builder.md.tmpl": (44, 4703),
    "sources/roles/system-explorer.md.tmpl": (23, 1510),
    "sources/roles/update-reviewer.md.tmpl": (65, 8735),
    "sources/roles/workflow-updater.md.tmpl": (77, 11056),
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
