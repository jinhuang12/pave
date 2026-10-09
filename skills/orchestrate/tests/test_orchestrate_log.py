#!/usr/bin/env python3
"""Tests for skills/orchestrate/scripts/orchestrate_log.py (spec section 4.1).

Each test runs the script as a subprocess in a temporary directory with its own
CLAUDE_CODE_SESSION_ID, so no test touches the real .orchestrate/ or session.

Run: python3 -m unittest discover -s skills/orchestrate/tests -p "test_orchestrate_log.py" -q
"""

from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "orchestrate_log.py"
EVENT_LINE = re.compile(r"^- \d\d:\d\d (\w+) (\S+)(?: (.*))?$")
PLAN1 = "worker-1 -> da-1 | textkit/slug.py | pytest tests/test_slug.py | one agent: one module"
PLAN2 = "worker-2 -> da-1 | textkit/roman.py | pytest tests/test_roman.py | one agent: same"


class OrchestrateLogTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.dir = self.root / ".orchestrate"

    def tearDown(self):
        self._tmp.cleanup()

    def run_log(self, *args, sid="sess-a", stdin=None, cwd=True, runtime="auto", extra_env=None):
        env = dict(os.environ)
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("CODEX_THREAD_ID", None)
        if sid is not None:
            env["CODEX_THREAD_ID" if runtime == "codex" else "CLAUDE_CODE_SESSION_ID"] = sid
        env.update(extra_env or {})
        argv = [sys.executable, str(SCRIPT), "--runtime", runtime, *args]
        if cwd:
            argv += ["--cwd", str(self.root)]
        return subprocess.run(argv, input=stdin, capture_output=True, text=True,
                              env=env, cwd=self.root)

    def marker(self, sid="sess-a"):
        return self.dir / f"active-{sid}"

    def init(self, *plans, sid="sess-a", goal="make all tests pass"):
        args = ["init", "--goal", goal]
        for plan in plans:
            args += ["--plan", plan]
        res = self.run_log(*args, sid=sid)
        self.assertEqual(res.returncode, 0, res.stderr)
        return Path(res.stdout.strip())

    def assertUtcClock(self, hhmm):
        now = dt.datetime.now(dt.timezone.utc)
        minutes = int(hhmm[:2]) * 60 + int(hhmm[3:])
        drift = (now.hour * 60 + now.minute - minutes) % 1440
        self.assertLessEqual(min(drift, 1440 - drift), 2, hhmm)

    def lines(self, log):
        return log.read_text(encoding="utf-8").splitlines()

    # init ---------------------------------------------------------------

    def test_init_writes_gitignore_header_plan_lines_and_marker(self):
        log = self.init(PLAN1, PLAN2)
        self.assertEqual((self.dir / ".gitignore").read_text(), "*\n")
        self.assertTrue(log.is_absolute())
        self.assertEqual(log.parent, self.dir)
        self.assertRegex(log.name, r"^\d{4}-\d\d-\d\d-\d{6}\.md$")
        started = dt.datetime.strptime(log.stem, "%Y-%m-%d-%H%M%S").replace(tzinfo=dt.timezone.utc)
        self.assertLess(abs((dt.datetime.now(dt.timezone.utc) - started).total_seconds()), 120)
        lines = self.lines(log)
        self.assertEqual(lines[0], f"# orchestrate run {log.stem}")
        self.assertEqual(lines[1], "goal: make all tests pass")
        self.assertEqual(lines[2], "")
        self.assertEqual(len(lines), 5)
        for line, plan in zip(lines[3:], (PLAN1, PLAN2)):
            match = EVENT_LINE.match(line)
            self.assertIsNotNone(match, line)
            self.assertEqual(line.split(" ", 2)[2], f"PLAN {plan}")
            self.assertUtcClock(line[2:7])
        self.assertEqual(self.marker().read_text(encoding="utf-8").strip(), str(log))

    def test_init_defaults_to_process_cwd(self):
        res = self.run_log("init", "--goal", "g", cwd=False)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(Path(res.stdout.strip()).parent, self.dir)

    def test_second_init_in_same_session_refuses_and_prints_close_command(self):
        log = self.init(PLAN1)
        res = self.run_log("init", "--goal", "another goal")
        self.assertEqual(res.returncode, 1)
        out = res.stdout + res.stderr
        self.assertIn(str(log), out)
        self.assertIn(f'python3 {SCRIPT} --runtime claude append CLOSE <actor> "incomplete | <reason>"', out)
        self.assertEqual(len(list(self.dir.glob("*.md"))), 1)
        self.assertEqual(self.marker().read_text(encoding="utf-8").strip(), str(log))

    def test_init_after_close_starts_a_new_run(self):
        first = self.init()
        self.assertEqual(self.run_log("append", "CLOSE", "lead", "accepted | done").returncode, 0)
        second = self.init(goal="next goal")
        self.assertNotEqual(first, second)
        self.assertEqual(self.marker().read_text(encoding="utf-8").strip(), str(second))

    def test_another_session_can_init_in_the_same_cwd(self):
        a = self.init(sid="sess-a")
        b = self.init(sid="sess-b")
        self.assertNotEqual(a, b)
        self.assertEqual(self.marker("sess-b").read_text(encoding="utf-8").strip(), str(b))

    # append -------------------------------------------------------------

    def test_append_non_close_prints_nothing(self):
        self.init(PLAN1)
        res = self.run_log("append", "DONE", "worker-1", "r1 PASS")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "")

    def test_append_writes_one_event_line(self):
        log = self.init(PLAN1)
        res = self.run_log("append", "DONE", "worker-1", "r1 PASS")
        self.assertEqual(res.returncode, 0, res.stderr)
        last = self.lines(log)[-1]
        self.assertRegex(last, r"^- \d\d:\d\d DONE worker-1 r1 PASS$")

    def test_append_rejects_unknown_event(self):
        log = self.init(PLAN1)
        before = self.lines(log)
        res = self.run_log("append", "FINISHED", "worker-1", "r1 PASS")
        self.assertEqual(res.returncode, 1)
        self.assertIn("FINISHED", res.stderr)
        self.assertEqual(self.lines(log), before)

    def test_append_rejects_close_without_stopping_word(self):
        log = self.init(PLAN1)
        before = self.lines(log)
        for text in ("all good", "acceptedish | x", ""):
            res = self.run_log("append", "CLOSE", "lead", text)
            self.assertEqual(res.returncode, 1, text)
            self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertEqual(self.lines(log), before)
        self.assertTrue(self.marker().exists())

    def test_append_close_writes_line_and_removes_marker(self):
        log = self.init(PLAN1)
        res = self.run_log("append", "CLOSE", "lead", "accepted | 1 module, 0 deferred")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(self.lines(log)[-1], r"^- \d\d:\d\d CLOSE lead accepted \| 1 module, 0 deferred$")
        self.assertFalse(self.marker().exists())
        self.assertIn("release live team agents", res.stdout)
        res = self.run_log("append", "DONE", "worker-1", "r1 PASS")
        self.assertEqual(res.returncode, 1)

    def test_every_stopping_word_closes(self):
        for i, word in enumerate(("accepted", "closed_unaccepted", "blocked", "incomplete", "exhausted")):
            sid = f"sess-{i}"
            self.init(sid=sid)
            res = self.run_log("append", "CLOSE", "lead", f"{word} | why", sid=sid)
            self.assertEqual(res.returncode, 0, (word, res.stderr))
            self.assertFalse(self.marker(sid).exists(), word)

    def test_append_without_open_run_refuses(self):
        res = self.run_log("append", "DONE", "worker-1", "r1 PASS")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)

    def test_append_does_not_touch_another_sessions_run(self):
        log = self.init(PLAN1, sid="sess-a")
        before = self.lines(log)
        res = self.run_log("append", "DONE", "worker-1", "r1 PASS", sid="sess-b")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(self.lines(log), before)

    def test_append_respawn_text_starting_with_arrow(self):
        log = self.init(PLAN1)
        res = self.run_log("append", "RESPAWN", "worker-1", "-> worker-3 | bedrock 503, nothing written")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertRegex(self.lines(log)[-1], r"^- \d\d:\d\d RESPAWN worker-1 -> worker-3 \| bedrock 503, nothing written$")

    def test_append_flattens_newlines_into_one_line(self):
        log = self.init(PLAN1)
        res = self.run_log("append", "BLOCKED", "worker-1", "first\n- 12:00 CLOSE lead accepted")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(len(self.lines(log)), 5)
        self.assertTrue(self.marker().exists())

    def test_append_stdin_appends_several_lines(self):
        log = self.init(PLAN1)
        batch = "PLAN worker-5 -> da-1 | a.py | test a | one agent: one file\n\nPLAN worker-6 -> da-1 | b.py | test b | one agent: same\nINTEGRATE lead 22/22 green\n"
        res = self.run_log("append", "--stdin", stdin=batch)
        self.assertEqual(res.returncode, 0, res.stderr)
        tail = self.lines(log)[-3:]
        self.assertRegex(tail[0], r"^- \d\d:\d\d PLAN worker-5 -> da-1 \| a\.py \| test a \| one agent: one file$")
        self.assertRegex(tail[1], r"^- \d\d:\d\d PLAN worker-6 -> da-1 \| b\.py")
        self.assertRegex(tail[2], r"^- \d\d:\d\d INTEGRATE lead 22/22 green$")

    def test_append_stdin_with_one_bad_line_appends_nothing(self):
        log = self.init(PLAN1)
        before = self.lines(log)
        res = self.run_log("append", "--stdin", stdin="DONE worker-1 r1 PASS\nNOPE lead x\n")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(self.lines(log), before)

    def test_append_stdin_refuses_empty_input(self):
        log = self.init(PLAN1)
        before = self.lines(log)
        res = self.run_log("append", "--stdin", stdin="\n\n")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertEqual(self.lines(log), before)

    def test_append_stdin_refuses_close_before_the_last_line(self):
        log = self.init(PLAN1)
        before = self.lines(log)
        res = self.run_log("append", "--stdin", stdin="CLOSE lead accepted | x\nDONE worker-1 r1 PASS\n")
        self.assertEqual(res.returncode, 1)
        self.assertEqual(self.lines(log), before)
        self.assertTrue(self.marker().exists())

    def test_append_stdin_and_positionals_together_is_usage_error(self):
        self.init(PLAN1)
        res = self.run_log("append", "--stdin", "DONE", "worker-1", "r1 PASS", stdin="")
        self.assertEqual(res.returncode, 2)

    def test_append_stdin_close_removes_marker(self):
        self.init(PLAN1)
        res = self.run_log("append", "--stdin", stdin="DONE worker-1 r1 PASS\nCLOSE lead accepted | done\n")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse(self.marker().exists())
        self.assertIn("release live team agents", res.stdout)

    # status -------------------------------------------------------------

    def test_status_derives_done_open_and_replaced(self):
        log = self.init(PLAN1, PLAN2, "worker-7 -> da-1 | c.py | test c | one agent: same")
        for args in (("DONE", "worker-1", "r2 PASS | r1 REVISE 1 HIGH slug.py:8"),
                     ("RESPAWN", "worker-2", "-> worker-4 | bedrock 503, nothing written"),
                     ("RESPAWN", "worker-7", "-> worker-8 | stuck"),
                     ("DONE", "worker-8", "r1 PASS")):
            self.assertEqual(self.run_log("append", *args).returncode, 0)
        res = self.run_log("status")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), [
            "goal: make all tests pass",
            "worker-1 -> da-1 — DONE r2 PASS | r1 REVISE 1 HIGH slug.py:8",
            "worker-4 -> da-1 — open, replaced worker-2",
            "    -> da-1 | textkit/roman.py | pytest tests/test_roman.py | one agent: same",
            "worker-8 -> da-1 — DONE r1 PASS",
            f"last: {self.lines(log)[-1]}",
        ])

    def status_lines(self):
        res = self.run_log("status")
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout.splitlines()

    def test_status_plan_after_done_reopens_with_latest_plan_text(self):
        log = self.init(PLAN1)
        fix = "-> da-2 | fix: slug.py:8 drops the trailing dash | pytest tests/test_slug.py"
        for args in (("DONE", "worker-1", "r1 PASS"),
                     ("REVIEW", "whole-goal", "da-1 REVISE 1 HIGH slug.py:8"),
                     ("PLAN", "worker-1", fix)):
            self.assertEqual(self.run_log("append", *args).returncode, 0)
        self.assertEqual(self.status_lines(), [
            "goal: make all tests pass",
            "worker-1 -> da-2 — open",
            f"    {fix}",
            f"last: {self.lines(log)[-1]}",
        ])
        self.assertEqual(self.run_log("append", "DONE", "worker-1", "r2 PASS | r1 REVISE 1 HIGH slug.py:8").returncode, 0)
        self.assertEqual(self.status_lines()[1:2], ["worker-1 -> da-2 — DONE r2 PASS | r1 REVISE 1 HIGH slug.py:8"])

    def test_status_replaced_only_when_latest_opener_is_respawn(self):
        self.init(PLAN2)
        for args in (("RESPAWN", "worker-2", "-> worker-4 | stuck"),
                     ("DONE", "worker-4", "r1 PASS"),
                     ("PLAN", "worker-4", "-> da-1 | fix: roman.py:3 | pytest tests/test_roman.py")):
            self.assertEqual(self.run_log("append", *args).returncode, 0)
        self.assertEqual(self.status_lines()[1:3], [
            "worker-4 -> da-1 — open",
            "    -> da-1 | fix: roman.py:3 | pytest tests/test_roman.py",
        ])

    def test_status_respawn_after_reopen_closes_old_even_with_earlier_done(self):
        self.init(PLAN1)
        for args in (("DONE", "worker-1", "r1 PASS"),
                     ("PLAN", "worker-1", "-> da-1 | fix: slug.py:8 | pytest tests/test_slug.py"),
                     ("RESPAWN", "team-lead", "worker-1 -> worker-5 | bedrock 503")):
            self.assertEqual(self.run_log("append", *args).returncode, 0)
        self.assertEqual(self.status_lines()[1:-1], [
            "worker-5 -> da-1 — open, replaced worker-1",
            "    -> da-1 | fix: slug.py:8 | pytest tests/test_slug.py",
        ])
        self.assertEqual(self.run_log("append", "DONE", "worker-5", "r1 PASS").returncode, 0)
        self.assertEqual(self.status_lines()[1:-1], ["worker-5 -> da-1 — DONE r1 PASS"])

    def test_status_open_subtask_keeps_plan_text(self):
        self.init("worker-1 slug.py | textkit/slug.py | pytest")
        res = self.run_log("status")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines()[1:3], [
            "worker-1 slug.py — open",
            "    slug.py | textkit/slug.py | pytest",
        ])

    def test_status_with_no_run_exits_1(self):
        res = self.run_log("status")
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("adopt", res.stdout + res.stderr)

    def test_status_names_orphaned_log_to_adopt(self):
        log = self.init(PLAN1, sid="old-session")
        self.marker("old-session").unlink()
        res = self.run_log("status", sid="new-session")
        self.assertEqual(res.returncode, 1)
        self.assertIn(f"no run for this session; adopt with: python3 {SCRIPT} --runtime claude adopt {log}",
                      res.stdout + res.stderr)

    def test_status_does_not_offer_another_live_sessions_log(self):
        self.init(PLAN1, sid="other-session")
        res = self.run_log("status", sid="new-session")
        self.assertEqual(res.returncode, 1)
        self.assertNotIn("adopt", res.stdout + res.stderr)

    # adopt --------------------------------------------------------------

    def test_adopt_repoints_marker_to_this_session(self):
        log = self.init(PLAN1, sid="old-session")
        res = self.run_log("adopt", str(log), sid="new-session")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.marker("new-session").read_text(encoding="utf-8").strip(), str(log))
        self.assertEqual(self.run_log("append", "DONE", "worker-1", "r1 PASS", sid="new-session").returncode, 0)
        self.assertEqual(self.run_log("status", sid="new-session").returncode, 0)

    def test_adopt_accepts_path_relative_to_cwd(self):
        log = self.init(sid="old-session")
        res = self.run_log("adopt", f".orchestrate/{log.name}", sid="new-session")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(self.marker("new-session").read_text(encoding="utf-8").strip(), str(log))

    def test_adopt_refuses_closed_log(self):
        log = self.init(sid="old-session")
        self.assertEqual(self.run_log("append", "CLOSE", "lead", "incomplete | abandoned", sid="old-session").returncode, 0)
        res = self.run_log("adopt", str(log), sid="new-session")
        self.assertEqual(res.returncode, 1)
        self.assertFalse(self.marker("new-session").exists())

    def test_adopt_refuses_missing_log(self):
        res = self.run_log("adopt", str(self.root / "nope.md"))
        self.assertEqual(res.returncode, 1)
        self.assertFalse(self.marker().exists())

    # session id ---------------------------------------------------------

    def test_codex_run_lifecycle_and_session_isolation(self):
        result = self.run_log("init", "--goal", "codex goal", "--plan", PLAN1,
                              sid="thread-a", runtime="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        log = Path(result.stdout.strip())
        self.assertEqual(self.marker("thread-a").read_text().strip(), str(log))
        self.assertEqual(self.run_log("status", sid="thread-b", runtime="codex").returncode, 1)
        self.assertIn("worker-1", self.run_log("status", sid="thread-a", runtime="codex").stdout)
        result = self.run_log("append", "DONE", "worker-1", "r1 PASS", sid="thread-a", runtime="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_log("append", "CLOSE", "lead", "accepted | done", sid="thread-a", runtime="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker("thread-a").exists())

    def test_auto_selects_codex_when_it_is_the_only_session(self):
        result = self.run_log("init", "--goal", "g", sid=None, extra_env={"CODEX_THREAD_ID": "thread-auto"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker("thread-auto").exists())

    def test_explicit_runtime_ignores_other_runtime_session(self):
        result = self.run_log("init", "--goal", "g", sid="thread-right", runtime="codex",
                              extra_env={"CLAUDE_CODE_SESSION_ID": "inherited-wrong"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker("thread-right").exists())
        self.assertFalse(self.marker("inherited-wrong").exists())

    def test_auto_refuses_ambiguous_runtime(self):
        result = self.run_log("init", "--goal", "g", extra_env={"CODEX_THREAD_ID": "nested"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("--runtime", result.stderr)
        self.assertFalse(self.dir.exists())

    def test_session_id_cannot_escape_marker_directory(self):
        for runtime in ("claude", "codex"):
            result = self.run_log("init", "--goal", "g", sid="../bad", runtime=runtime)
            self.assertEqual(result.returncode, 2)
        self.assertFalse(self.dir.exists())

    def test_missing_session_id_exits_2(self):
        for args in (("init", "--goal", "g"), ("append", "DONE", "w", "x"), ("status",), ("adopt", "x.md")):
            res = self.run_log(*args, sid=None)
            self.assertEqual(res.returncode, 2, args)
            self.assertIn("CLAUDE_CODE_SESSION_ID", res.stderr)
            self.assertEqual(len(res.stderr.strip().splitlines()), 1, res.stderr)
        self.assertFalse(self.dir.exists())


if __name__ == "__main__":
    unittest.main()
