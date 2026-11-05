"""Every copy of the worker's review brief carries the maintainer-bar lines."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COPIES = (
    "agents/orchestrate-worker.md",
    "skills/orchestrate/references/protocol.md",
    "codex/agents/pave_init_orchestrate_worker.toml",
    "codex/skills/orchestrate/references/protocol.md",
)
LINES = ("**User goal:**", "**Speed:**", "**Simplest:**")


class BriefLines(unittest.TestCase):
    def test_every_copy_carries_the_three_lines(self):
        for rel in COPIES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for line in LINES:
                with self.subTest(copy=rel, line=line):
                    self.assertIn(line, text)


if __name__ == "__main__":
    unittest.main()
