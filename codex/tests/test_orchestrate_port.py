from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from codex import install_agents, orchestrate_preflight
from codex.preflight import PreflightError
from scripts import build_packages

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "skills/orchestrate/scripts/orchestrate_log.py"


class OrchestratePortTests(unittest.TestCase):
    def test_build_includes_both_skills_and_both_role_pairs(self):
        outputs = build_packages.build_outputs()
        for relative in (
            "skills/orchestrate/SKILL.md",
            "codex/skills/orchestrate/SKILL.md",
            "agents/orchestrate-worker.md",
            "agents/adversarial-investigator.md",
            "codex/agents/pave_init_orchestrate_worker.toml",
            "codex/agents/pave_init_adversarial_investigator.toml",
            "skills/orchestrate/references/protocol.md",
            "skills/orchestrate/references/subtask.md",
            "skills/orchestrate/references/model-seats.md",
            "codex/skills/orchestrate/references/protocol.md",
            "codex/skills/orchestrate/references/subtask.md",
            "codex/skills/orchestrate/references/model-seats.md",
        ):
            self.assertEqual((ROOT / relative).read_text(), outputs[ROOT / relative])
        for filename, name in (
            ("pave_init_orchestrate_worker.toml", "pave-init:orchestrate-worker"),
            ("pave_init_adversarial_investigator.toml", "pave-init:adversarial-investigator"),
        ):
            data = tomllib.loads(outputs[ROOT / "codex/agents" / filename])
            self.assertEqual(data["name"], name)
            self.assertEqual(install_agents.EXPECTED_AGENTS[filename], name)

    def test_native_references_and_skill_examples_do_not_leak_between_platforms(self):
        outputs = build_packages.build_outputs()
        for name in ("protocol", "subtask", "model-seats"):
            claude = outputs[ROOT / f"skills/orchestrate/references/{name}.md"]
            codex = outputs[ROOT / f"codex/skills/orchestrate/references/{name}.md"]
            for foreign in ("followup_task", "spawn_agent", "list_agents", "gpt-6"):
                self.assertNotIn(foreign, claude)
            for foreign in ("SendMessage", "ListAgents", "TaskStop", "superpowers:", "sonnet", "fable", "opus"):
                self.assertNotIn(foreign, codex)
            self.assertNotIn("harness-specific:", claude + codex)
            self.assertNotIn("runtime's", codex)
        worker = tomllib.loads(outputs[ROOT / "codex/agents/pave_init_orchestrate_worker.toml"])
        skill = outputs[ROOT / "codex/skills/orchestrate/SKILL.md"]
        self.assertNotIn("superpowers:", worker["developer_instructions"] + skill)
        self.assertNotIn("unavailable skills", worker["developer_instructions"] + skill)

    def test_missing_reference_slot_fails_generation(self):
        load_binding = build_packages.load_binding
        def missing_slot(path, schema):
            binding = load_binding(path, schema)
            del binding["skills"]["orchestrate"]["references"]["protocol"]["DELIVERY"]
            return binding
        with mock.patch.object(build_packages, "load_binding", side_effect=missing_slot):
            with self.assertRaisesRegex(build_packages.BuildError, "invalid placeholders"):
                build_packages.build_outputs()

    def test_resume_hook_prints_executable_commands_for_both_platforms(self):
        config = json.loads((ROOT / "codex/hooks/hooks.json").read_text())
        codex_command = config["hooks"]["SessionStart"][0]["hooks"][0]["command"].replace("${PLUGIN_ROOT}", str(ROOT))
        hook_path = ROOT / "skills/orchestrate/hooks/goal_restate.sh"
        for runtime, roster in (("claude", "ListAgents"), ("codex", "list_agents")):
            with self.subTest(runtime=runtime), tempfile.TemporaryDirectory(prefix="orchestrate command ") as tmp:
                project = Path(tmp).resolve()
                env = dict(os.environ, CODEX_THREAD_ID="codex-owner", CLAUDE_CODE_SESSION_ID="claude-owner",
                           ORCHESTRATE_PYTHON=sys.executable)
                shell_bin = project / "shell-bin"
                shell_bin.mkdir()
                for name in ("bash", "dirname", "cat", "sed", "head", "awk", "paste"):
                    executable = shutil.which(name)
                    self.assertIsNotNone(executable)
                    (shell_bin / name).symlink_to(executable)
                env["PATH"] = str(shell_bin)  # Only ORCHESTRATE_PYTHON can run Python here.
                created = subprocess.run([sys.executable, str(LOG), "--runtime", runtime, "init",
                                          "--goal", f"resume {runtime}", "--cwd", str(project)],
                                         env=env, capture_output=True, text=True)
                self.assertEqual(created.returncode, 0, created.stderr)
                payload = {"session_id": f"{runtime}-owner", "hook_event_name": "SessionStart",
                           "source": "compact", "cwd": str(project)}
                command = codex_command if runtime == "codex" else shlex.quote(str(hook_path))
                result = subprocess.run(["bash", "-c", command], input=json.dumps(payload), env=env,
                                        cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                printed = re.search(r"\(2\) run `([^`]+)`", context)
                self.assertIsNotNone(printed)
                args = shlex.split(printed.group(1))
                status = subprocess.run(args, env=env, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(status.returncode, 0, status.stderr)
                self.assertIn(f"goal: resume {runtime}", status.stdout)
                self.assertIn(f"`{roster}`", context)
                self.assertNotIn("`ListAgents`" if runtime == "codex" else "`list_agents`", context)

    def test_installed_file_check_does_not_claim_runtime_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            home = Path(tmp) / "home"
            with self.assertRaises(PreflightError):
                orchestrate_preflight.check(project, home)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(install_agents.install(project / ".codex/agents", False), 0)
            result = orchestrate_preflight.check(project, home)
            self.assertEqual(result["agent_scope"], "project")
            self.assertEqual(result["files"], "current")
            self.assertTrue(result["runtime"].startswith("not verified"))
            role = project / ".codex/agents/pave_init_orchestrate_worker.toml"
            role.write_text(role.read_text() + "\n# local change\n")
            with self.assertRaises(PreflightError):
                orchestrate_preflight.check(project, home)

    def test_file_check_rejects_missing_shared_resource(self):
        with mock.patch.object(orchestrate_preflight, "RESOURCES", ("missing-resource",)):
            with self.assertRaisesRegex(PreflightError, "missing or unreadable"):
                orchestrate_preflight.check(ROOT, ROOT)

    def test_codex_log_and_registered_resume_hook_share_ownership(self):
        config = json.loads((ROOT / "codex/hooks/hooks.json").read_text())
        registration = config["hooks"]["SessionStart"][0]
        self.assertEqual(registration["matcher"], "resume|compact")
        command = registration["hooks"][0]["command"].replace("${PLUGIN_ROOT}", str(ROOT))
        with tempfile.TemporaryDirectory(prefix="orchestrate cwd ") as tmp:
            project = Path(tmp).resolve()
            env = dict(os.environ, CODEX_THREAD_ID="codex-test", CLAUDE_CODE_SESSION_ID="inherited",
                       ORCHESTRATE_PYTHON=sys.executable)
            env.pop("CLAUDE_PROJECT_DIR", None)
            def log(*args):
                return subprocess.run([sys.executable, str(LOG), "--runtime", "codex", *args,
                                       "--cwd", str(project)], env=env, capture_output=True, text=True)
            def hook(source="resume", **fields):
                payload = {"hook_event_name": "SessionStart", "source": source,
                           "session_id": "codex-test", "cwd": str(project), **fields}
                return subprocess.run(["bash", "-c", command], input=json.dumps(payload),
                                      env=env, cwd=ROOT, capture_output=True, text=True)
            result = log("init", "--goal", "restore this task", "--plan", "worker-1 -> da-1 | owned.py")
            self.assertEqual(result.returncode, 0, result.stderr)
            for source in ("resume", "compact"):
                result = hook(source)
                self.assertEqual(result.returncode, 0, result.stderr)
                context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("worker-1", context)
                self.assertIn(str(project), context)
            self.assertEqual(hook(session_id="unrelated").stdout, "")
            self.assertEqual(hook(agent_id="child").stdout, "")
            self.assertEqual(hook("startup").stdout, "")
            self.assertEqual(log("append", "CLOSE", "lead", "incomplete | test complete").returncode, 0)
            self.assertEqual(hook().stdout, "")


if __name__ == "__main__":
    unittest.main()
