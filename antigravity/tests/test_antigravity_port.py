from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts import build_packages, stamp_version

ROOT = Path(__file__).resolve().parents[2]
AG_ROOT = ROOT / "antigravity"
ADAPTER = AG_ROOT / "hooks" / "ag_hook_adapter.py"
LOG = AG_ROOT / "skills" / "orchestrate" / "scripts" / "orchestrate_log.py"


class AntigravityPackageTests(unittest.TestCase):
    def test_build_outputs_are_current_and_self_contained(self) -> None:
        outputs = build_packages.build_outputs()
        expected = (
            "antigravity/skills/orchestrate/SKILL.md",
            "antigravity/skills/orchestrate/references/execution-and-close.md",
            "antigravity/skills/orchestrate/references/protocol.md",
            "antigravity/skills/orchestrate/references/subtask.md",
            "antigravity/skills/orchestrate/references/model-seats.md",
            "antigravity/agents/orchestrate-worker.md",
            "antigravity/agents/adversarial-investigator.md",
            "antigravity/agents/research-delegate.md",
            "antigravity/skills/orchestrate/scripts/orchestrate_log.py",
            "antigravity/skills/orchestrate/hooks/_hook_common.sh",
            "antigravity/skills/orchestrate/hooks/goal_restate.sh",
            "antigravity/skills/orchestrate/hooks/speedup_nudge.sh",
            "antigravity/skills/orchestrate/hooks/maintainer_nudge.sh",
        )
        for relative in expected:
            path = ROOT / relative
            self.assertIn(path, outputs, relative)
            self.assertEqual(path.read_text(encoding="utf-8"), outputs[path], relative)

    def test_skill_body_stays_under_antigravity_24kb_cap(self) -> None:
        raw = (AG_ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
        body = raw.split("\n---\n", 1)[1]
        self.assertLessEqual(
            len(body.encode("utf-8")),
            build_packages.ANTIGRAVITY_SKILL_MAX_BODY_BYTES,
        )
        self.assertIn("references/execution-and-close.md", body)
        continuation = (
            AG_ROOT / "skills" / "orchestrate" / "references" / "execution-and-close.md"
        ).read_text(encoding="utf-8")
        for heading in (
            "## Step 4: Monitor and broker",
            "## Step 5: Integrate",
            "## Step 6: Final review",
            "## Step 7: Report and shut down",
            "## Resume",
            "## Hooks",
        ):
            self.assertIn(heading, continuation)

    def test_no_foreign_harness_mechanics_leak(self) -> None:
        outputs = build_packages.build_outputs()
        ag_files = (
            "antigravity/skills/orchestrate/SKILL.md",
            "antigravity/skills/orchestrate/references/execution-and-close.md",
            "antigravity/skills/orchestrate/references/protocol.md",
            "antigravity/skills/orchestrate/references/subtask.md",
            "antigravity/skills/orchestrate/references/model-seats.md",
            "antigravity/agents/orchestrate-worker.md",
            "antigravity/agents/adversarial-investigator.md",
            "antigravity/agents/research-delegate.md",
        )
        for rel in ag_files:
            text = outputs[ROOT / rel]
            for foreign in (
                "SendMessage",
                "ListAgents",
                "TaskStop",
                "followup_task",
                "spawn_agent",
                "list_agents",
                "gpt-6",
                "superpowers:",
                "fable",
                "opus",
                "sonnet",
                "harness-specific:",
            ):
                self.assertNotIn(foreign, text, (rel, foreign))

        for rel in (
            "skills/orchestrate/SKILL.md",
            "skills/orchestrate/references/protocol.md",
            "codex/skills/orchestrate/SKILL.md",
            "codex/skills/orchestrate/references/protocol.md",
        ):
            text = outputs[ROOT / rel]
            for foreign in ("invoke_subagent", "manage_subagents", "ag_hook_adapter"):
                self.assertNotIn(foreign, text, (rel, foreign))

    def test_manifest_and_hooks_json_match_antigravity_contract(self) -> None:
        manifest = json.loads((AG_ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "pave-init")
        self.assertEqual(manifest["version"], stamp_version.authoritative_version())

        hooks_cfg = json.loads((AG_ROOT / "hooks.json").read_text(encoding="utf-8"))
        self.assertIn("orchestrate", hooks_cfg)
        spec = hooks_cfg["orchestrate"]
        self.assertEqual(set(spec), {"SessionStart", "PostToolUse", "PostInvocation"})
        # Flat arrays for SessionStart and PostInvocation
        for flat_event in ("SessionStart", "PostInvocation"):
            self.assertIsInstance(spec[flat_event], list)
            self.assertTrue(all("command" in h and "matcher" not in h for h in spec[flat_event]))
        # Grouped matcher/hooks arrays for PostToolUse
        self.assertIsInstance(spec["PostToolUse"], list)
        self.assertTrue(all("matcher" in g and "hooks" in g for g in spec["PostToolUse"]))

    def test_agy_plugin_validate_strict_passes(self) -> None:
        agy = shutil.which("agy")
        if not agy:
            self.skipTest("agy CLI not installed in this environment")
        result = subprocess.run(
            [agy, "plugin", "validate", "--strict", str(AG_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + "\n" + result.stderr)
        self.assertIn("skills 2", result.stdout)
        self.assertIn("agents 3", result.stdout)
        self.assertIn("hooks 1", result.stdout)


class AntigravityHookAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ag orchestrate ")
        self.tmp = Path(self._tmp.name).resolve()
        self.project = self.tmp / "proj dir"
        self.project.mkdir()
        self.markers = self.tmp / "markers"
        self.pending = self.tmp / "pending"
        self.roles = self.tmp / "roles"
        self.tmpdir = self.tmp / "tmp"
        self.tmpdir.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _base_env(self, conv_id: str | None = "conv-lead-1", **extra: str) -> dict[str, str]:
        env = dict(
            os.environ,
            ORCHESTRATE_PYTHON=sys.executable,
            ORCHESTRATE_MARKER_DIR=str(self.markers),
            ORCHESTRATE_AG_PENDING_DIR=str(self.pending),
            ORCHESTRATE_AG_ROLE_DIR=str(self.roles),
            TMPDIR=str(self.tmpdir),
        )
        env.pop("CLAUDE_CODE_SESSION_ID", None)
        env.pop("CODEX_THREAD_ID", None)
        env.pop("CLAUDE_PROJECT_DIR", None)
        if conv_id is not None:
            env["ANTIGRAVITY_CONVERSATION_ID"] = conv_id
        else:
            env.pop("ANTIGRAVITY_CONVERSATION_ID", None)
        env.update(extra)
        return env

    def _run_log(self, *args: str, conv_id: str = "conv-lead-1") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(LOG), "--runtime", "antigravity", *args, "--cwd", str(self.project)],
            env=self._base_env(conv_id),
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_adapter(
        self,
        mode: str,
        payload: dict | str,
        *,
        conv_id: str | None = "conv-lead-1",
        **extra_env: str,
    ) -> tuple[int, dict, str]:
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        res = subprocess.run(
            [sys.executable, str(ADAPTER), mode],
            input=raw,
            env=self._base_env(conv_id, **extra_env),
            cwd=str(AG_ROOT),  # Antigravity executes plugin hooks with cwd == plugin root
            capture_output=True,
            text=True,
            check=False,
        )
        parsed = json.loads(res.stdout) if res.stdout.strip() else {}
        return res.returncode, parsed, res.stderr

    def _write_transcript(self, name: str, first_user_content: str) -> Path:
        path = self.tmp / f"{name}.jsonl"
        step = {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "status": "DONE",
            "content": first_user_content,
        }
        path.write_text(json.dumps(step) + "\n", encoding="utf-8")
        return path

    def test_session_start_goal_restate_lifecycle(self) -> None:
        # Fresh conversation with no active run stays silent
        rc, out, err = self._run_adapter(
            "session_start",
            {"conversationId": "conv-lead-1", "hookEventName": "SessionStart", "cwd": str(self.project)},
        )
        self.assertEqual((rc, out, err), (0, {}, ""))

        # Start an orchestrated run owned by conv-lead-1
        created = self._run_log(
            "init",
            "--goal",
            "ship antigravity swarm",
            "--plan",
            "worker-1 -> da-1 | a.py | pytest",
        )
        self.assertEqual(created.returncode, 0, created.stderr)

        # Resuming conv-lead-1 injects goal restate with executable status command & swarm roster
        rc, out, err = self._run_adapter(
            "session_start",
            {"conversationId": "conv-lead-1", "hookEventName": "SessionStart", "cwd": str(self.project)},
        )
        self.assertEqual((rc, err), (0, ""))
        steps = out.get("injectSteps") or []
        self.assertEqual(len(steps), 1)
        text = steps[0]["systemMessage"]["systemMessage"]
        self.assertIn("worker-1", text)
        self.assertIn('`swarm(action: "status")`', text)
        match = re.search(r"\(2\) run `([^`]+)`", text)
        self.assertIsNotNone(match, text)
        status = subprocess.run(
            shlex.split(match.group(1)),
            env=self._base_env("conv-lead-1"),
            cwd=str(AG_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("goal: ship antigravity swarm", status.stdout)

        # Unrelated conversation stays silent
        rc, out, err = self._run_adapter(
            "session_start",
            {"conversationId": "conv-other", "hookEventName": "SessionStart", "cwd": str(self.project)},
            conv_id="conv-other",
        )
        self.assertEqual((rc, out, err), (0, {}, ""))

        # After CLOSE, conv-lead-1 stays silent
        closed = self._run_log("append", "CLOSE", "team-lead", "accepted | done")
        self.assertEqual(closed.returncode, 0, closed.stderr)
        rc, out, err = self._run_adapter(
            "session_start",
            {"conversationId": "conv-lead-1", "hookEventName": "SessionStart", "cwd": str(self.project)},
        )
        self.assertEqual((rc, out, err), (0, {}, ""))

    def test_speedup_nudge_spools_on_post_tool_use_and_drains_on_post_invocation(self) -> None:
        self.assertEqual(
            self._run_log(
                "init",
                "--goal",
                "parallelize modules",
                "--plan",
                "worker-1 -> da-1 | mod.py | pytest",
            ).returncode,
            0,
        )

        # Non-log shell command stays silent
        rc, out, err = self._run_adapter(
            "post_tool_speedup",
            {
                "conversationId": "conv-lead-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}},
            },
        )
        self.assertEqual((rc, out, err), (0, {}, ""))
        _, drained, _ = self._run_adapter("post_invocation", {"conversationId": "conv-lead-1"})
        self.assertEqual(drained, {})

        # Log write triggers speedup nudge -> spooled -> drained once on PostInvocation
        rc, out, err = self._run_adapter(
            "post_tool_speedup",
            {
                "conversationId": "conv-lead-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "toolCall": {
                    "name": "run_command",
                    "args": {
                        "CommandLine": f"python3 {LOG} --runtime antigravity append PLAN worker-2 '-> da-1 | b.py'"
                    },
                },
            },
        )
        self.assertEqual((rc, out, err), (0, {}, ""))

        rc, drained, err = self._run_adapter("post_invocation", {"conversationId": "conv-lead-1"})
        self.assertEqual((rc, err), (0, ""))
        steps = drained.get("injectSteps") or []
        self.assertEqual(len(steps), 1)
        msg = steps[0]["systemMessage"]["systemMessage"]
        self.assertIn("[orchestrate speedup_nudge]", msg)
        self.assertIn('swarm(action: "kill"', msg)

        # Second drain is empty (no duplicate injection)
        _, drained_again, _ = self._run_adapter("post_invocation", {"conversationId": "conv-lead-1"})
        self.assertEqual(drained_again, {})

        # Immediate second log write is throttled by default 60s interval
        self._run_adapter(
            "post_tool_speedup",
            {
                "conversationId": "conv-lead-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "toolCall": {
                    "name": "run_command",
                    "args": {"CommandLine": f"python3 {LOG} --runtime antigravity append DONE worker-1 'r1 PASS'"},
                },
            },
        )
        _, throttled, _ = self._run_adapter("post_invocation", {"conversationId": "conv-lead-1"})
        self.assertEqual(throttled, {})

    def test_maintainer_nudge_targets_only_swarm_workers_not_da_or_lead(self) -> None:
        self.assertEqual(self._run_log("init", "--goal", "keep bar high").returncode, 0)

        worker_transcript = self._write_transcript(
            "worker-1",
            "<USER_REQUEST>\n@worker-1\nSUBTASK worker-1\n\nOrchestrator: @coordinator\n</USER_REQUEST>",
        )
        da_transcript = self._write_transcript(
            "da-1",
            "<USER_REQUEST>\n@da-1\nYou are da-1, the adversarial reviewer for a worker team.\n\n"
            "SUBTASK CHECK worker-1\nSUBTASK worker-1\n</USER_REQUEST>",
        )
        lead_transcript = self._write_transcript(
            "lead",
            "<USER_REQUEST>\nSUBTASK worker-99 (even if user typed this, lead owns marker)\n</USER_REQUEST>",
        )

        # Worker edit fires and drains via PostInvocation
        rc, out, err = self._run_adapter(
            "post_tool_maintainer",
            {
                "conversationId": "conv-worker-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "transcriptPath": str(worker_transcript),
                "toolCall": {"name": "edit_file", "args": {"Path": str(self.project / "a.py")}},
            },
            conv_id="conv-worker-1",
        )
        self.assertEqual((rc, out, err), (0, {}, ""))
        rc, drained, err = self._run_adapter(
            "post_invocation",
            {"conversationId": "conv-worker-1"},
            conv_id="conv-worker-1",
        )
        self.assertEqual((rc, err), (0, ""))
        steps = drained.get("injectSteps") or []
        self.assertEqual(len(steps), 1)
        self.assertIn("[orchestrate maintainer_nudge]", steps[0]["systemMessage"]["systemMessage"])

        # DA edit (even with embedded SUBTASK CHECK worker-1) stays silent
        self._run_adapter(
            "post_tool_maintainer",
            {
                "conversationId": "conv-da-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "transcriptPath": str(da_transcript),
                "toolCall": {"name": "write_file", "args": {"Path": str(self.project / "notes.md")}},
            },
            conv_id="conv-da-1",
        )
        _, da_drained, _ = self._run_adapter(
            "post_invocation", {"conversationId": "conv-da-1"}, conv_id="conv-da-1"
        )
        self.assertEqual(da_drained, {})

        # Lead owning active marker stays silent
        self._run_adapter(
            "post_tool_maintainer",
            {
                "conversationId": "conv-lead-1",
                "hookEventName": "PostToolUse",
                "cwd": str(self.project),
                "transcriptPath": str(lead_transcript),
                "toolCall": {"name": "write_file", "args": {"Path": str(self.project / "x.md")}},
            },
            conv_id="conv-lead-1",
        )
        _, lead_drained, _ = self._run_adapter(
            "post_invocation", {"conversationId": "conv-lead-1"}, conv_id="conv-lead-1"
        )
        self.assertEqual(lead_drained, {})

    def test_adapter_fails_open_on_bad_inputs(self) -> None:
        for mode in ("session_start", "post_tool_speedup", "post_tool_maintainer", "post_invocation", "unknown"):
            for raw in ("", "{not-json", "[]", '{"conversationId": "../escape"}'):
                rc, out, err = self._run_adapter(mode, raw)
                self.assertEqual((rc, out, err), (0, {}, ""), (mode, raw))


if __name__ == "__main__":
    unittest.main()
