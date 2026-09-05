from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AGENT_NAMES = {
    "vllm-neuron-parity:adjudicator",
    "vllm-neuron-parity:adversarial-reviewer",
    "vllm-neuron-parity:implementer",
    "vllm-neuron-parity:investigator",
    "vllm-neuron-parity:measurer",
    "vllm-neuron-parity:rederiver",
}
ACTIVE_STATE: dict[str, object] = {
    "workflow_identity": {"run_id": "codex-port-test"},
    "pinned_release": None,
    "requested_targets": [],
    "instance_roster": [],
    "cross_run_artifact_refs": {},
    "ranked_backlog": None,
    "approved_campaigns": [],
    "campaign_states": {},
    "campaign_target_pins": {},
    "scheduling_holds": [],
    "comparator_registrations": {},
    "hardware_attempt_counts": {},
    "hardware_lease_record": None,
    "gate_approval_records": [],
    "active_node_runs": [],
    "completed_outcomes": [],
    "evidence_references": {},
    "open_questions": [],
    "terminal_classification": None,
    "scan_entry_id": None,
    "design_entry_id": None,
}


class PackageStructureTests(unittest.TestCase):
    def test_manifest_is_native_and_paths_resolve(self) -> None:
        path = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], PLUGIN_ROOT.name)
        self.assertEqual(manifest["version"], "1.5.1")
        self.assertNotIn("hooks", manifest)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertTrue((PLUGIN_ROOT / manifest["skills"]).is_dir())
        self.assertEqual(manifest["interface"]["displayName"], "vLLM-Neuron Parity")

    def test_hook_config_registers_exactly_eight_controls(self) -> None:
        # One hooks.json serves both harnesses: Codex sets CLAUDE_PLUGIN_ROOT
        # for compatibility, so every command resolves under that variable.
        data = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
        hooks = data["hooks"]
        self.assertEqual(set(hooks), {"PreToolUse", "PostToolUse", "Stop"})
        handlers = [
            handler
            for groups in hooks.values()
            for group in groups
            for handler in group["hooks"]
        ]
        self.assertEqual(len(handlers), 8)
        self.assertTrue(all(handler["type"] == "command" for handler in handlers))
        self.assertTrue(
            all("${CLAUDE_PLUGIN_ROOT}" in handler["command"] for handler in handlers)
        )
        pre_commands = "\n".join(
            handler["command"] for handler in hooks["PreToolUse"][0]["hooks"]
        )
        self.assertIn("pre_tool_use_router.py", pre_commands)
        matchers = {group.get("matcher") for group in hooks["PreToolUse"]}
        self.assertEqual(matchers, {"Bash", "Agent|Task", "Edit|Write|MultiEdit"})
        self.assertIn(
            "graph_edit_guard.sh",
            "\n".join(handler["command"] for handler in handlers),
        )
        post_commands = "\n".join(
            handler["command"]
            for group in hooks["PostToolUse"]
            for handler in group["hooks"]
        )
        self.assertIn("state-staleness-reminder.sh", post_commands)
        self.assertIn("write-for-reader.sh", post_commands)
        # Every registered script ships in the package.
        for handler in handlers:
            command = handler["command"]
            start = command.index("${CLAUDE_PLUGIN_ROOT}/") + len("${CLAUDE_PLUGIN_ROOT}/")
            end = command.index('"', start)
            self.assertTrue((PLUGIN_ROOT / command[start:end]).is_file(), command)

    def test_custom_agents_parse_and_cover_roles(self) -> None:
        paths = sorted(
            (PLUGIN_ROOT / "codex" / "agents").glob("vllm_neuron_parity_*.toml")
        )
        self.assertEqual(len(paths), 6)
        names: set[str] = set()
        for path in paths:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            for field in ("name", "description", "developer_instructions"):
                self.assertTrue(data.get(field), (path, field))
            self.assertIn(data["model"], {"gpt-5.6-sol", "gpt-5.6-terra"})
            self.assertIn(data["model_reasoning_effort"], {"medium", "high", "xhigh"})
            self.assertIn(data["sandbox_mode"], {"read-only", "workspace-write"})
            contract = data["developer_instructions"]
            self.assertIn("VLLM_NEURON_PARITY_PLUGIN_ROOT", contract)
            self.assertIn("VLLM_NEURON_PARITY_EVOLUTION_ROOT", contract)
            self.assertIn("complete Codex role contract", contract)
            for legacy in ("SendMessage", "named teammate", " opus", " fable", " sonnet"):
                self.assertNotIn(legacy, contract)
            names.add(data["name"])
        self.assertEqual(names, AGENT_NAMES)

    def test_lead_uses_codex_runtime_binding(self) -> None:
        skill = (
            PLUGIN_ROOT / "skills" / "vllm-neuron-parity" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("$vllm-neuron-parity:vllm-neuron-parity", skill)
        self.assertIn("spawn_agent", skill)
        self.assertIn("followup_task", skill)
        self.assertIn("wait_agent", skill)
        self.assertIn("interrupt_agent", skill)
        self.assertIn("hooks/hooks.json", skill)
        self.assertIn(
            "VLLM_NEURON_PARITY_EVOLUTION_ROOT: <absolute project-local evolution",
            skill,
        )
        # The Claude Code tool map sits beside the Codex names ("Harness").
        for claude_tool in ("SendMessage", "TaskStop", "ListAgents"):
            self.assertIn(claude_tool, skill)
        self.assertNotIn("skill-frontmatter hooks", skill)


class InstallerTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(PLUGIN_ROOT / "codex" / "install_agents.py"), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_install_check_and_uninstall_are_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            installed = self._run("--project", str(project))
            self.assertEqual(installed.returncode, 0, installed.stderr)
            target = project / ".codex" / "agents"
            self.assertEqual(len(list(target.glob("vllm_neuron_parity_*.toml"))), 6)
            checked = self._run("--project", str(project), "--check")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            removed = self._run("--project", str(project), "--uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(target.exists())

    def test_installer_refuses_unowned_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            target = project / ".codex" / "agents"
            target.mkdir(parents=True)
            conflict = target / "vllm_neuron_parity_investigator.toml"
            conflict.write_text("user-owned\n", encoding="utf-8")
            result = self._run("--project", str(project))
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to overwrite", result.stderr)
            self.assertEqual(conflict.read_text(encoding="utf-8"), "user-owned\n")

    def test_user_scope_respects_codex_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / "custom-codex-home"
            env = os.environ.copy()
            env["CODEX_HOME"] = str(codex_home)
            result = subprocess.run(
                [
                    "python3",
                    str(PLUGIN_ROOT / "codex" / "install_agents.py"),
                    "--user",
                ],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                len(list((codex_home / "agents").glob("vllm_neuron_parity_*.toml"))),
                6,
            )


class HookSmokeTests(unittest.TestCase):
    def _run_guard(
        self, guard: str, command: str, state: dict[str, object] | None
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            if state is not None:
                state_path = project / "run-state.json"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                (project / ".vllm-neuron-parity-run").write_text(
                    str(state_path) + "\n", encoding="utf-8"
                )
            payload = {
                "session_id": "codex-port-test",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": str(project),
            }
            env = os.environ.copy()
            env["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
            return subprocess.run(
                [
                    "python3",
                    str(PLUGIN_ROOT / "hooks" / "pre_tool_use_router.py"),
                    guard,
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=False,
                cwd=project,
                env=env,
            )

    def _run_alignment_hook(
        self, name: str, command: str
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            payload = {
                "session_id": "codex-port-test",
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "cwd": temp,
            }
            return subprocess.run(
                [
                    str(
                        PLUGIN_ROOT
                        / "skills"
                        / "vllm-neuron-parity"
                        / "hooks"
                        / name
                    )
                ],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                check=False,
                cwd=temp,
            )

    def test_blocking_guards_refuse_prohibited_commands(self) -> None:
        cases = {
            "protected-branch": "git push origin HEAD:main",
            "compile-cache": "rm -rf ~/.cache/vllm/neuron/compile_cache",
            "venv-opt": "cp -a /opt/venv /tmp/copied-venv",
        }
        for guard, command in cases.items():
            with self.subTest(guard=guard):
                result = self._run_guard(
                    guard, command, dict(ACTIVE_STATE)
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("BLOCKED", result.stderr)

    def test_compile_cache_guard_covers_the_shared_kernel_cache(self) -> None:
        # The kernel toolchain writes /var/tmp/nki-intermediate-cache outside
        # every cache root the run can set, and it can hold another tenant's
        # artifacts: a delete is refused, a rename aside is not.
        blocked = self._run_guard(
            "compile-cache",
            "rm -rf /var/tmp/nki-intermediate-cache",
            dict(ACTIVE_STATE),
        )
        self.assertEqual(blocked.returncode, 2, blocked.stderr)
        self.assertIn("rename it aside", blocked.stderr)
        allowed = self._run_guard(
            "compile-cache",
            "mv /var/tmp/nki-intermediate-cache /var/tmp/nki-cache.aside",
            dict(ACTIVE_STATE),
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_blocking_guards_allow_inactive_and_terminal_runs(self) -> None:
        cases = {
            "protected-branch": "git push origin HEAD:main",
            "compile-cache": "rm -rf ~/.cache/vllm/neuron/compile_cache",
            "venv-opt": "cp -a /opt/venv /tmp/copied-venv",
        }
        for guard, command in cases.items():
            terminal_state = dict(ACTIVE_STATE)
            terminal_state["terminal_classification"] = {
                "status": "accepted",
                "endpoint": "done",
            }
            for state in (None, terminal_state, {"terminal_classification": None}):
                with self.subTest(guard=guard, state=state):
                    result = self._run_guard(guard, command, state)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_blocking_guards_allow_schema_invalid_marker(self) -> None:
        invalid = dict(ACTIVE_STATE)
        invalid["workflow_identity"] = "not-an-object"
        result = self._run_guard(
            "protected-branch", "git push origin HEAD:main", invalid
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_blocking_guards_allow_benign_command_during_active_run(self) -> None:
        for guard in ("protected-branch", "compile-cache", "venv-opt"):
            with self.subTest(guard=guard):
                result = self._run_guard(
                    guard,
                    "git status --short",
                    dict(ACTIVE_STATE),
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_alignment_hooks_are_silent_without_run_marker(self) -> None:
        for hook in ("state-staleness-reminder.sh", "stop-guard.sh"):
            with self.subTest(hook=hook):
                result = self._run_alignment_hook(hook, "git status --short")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")


class RevisionLedgerTests(unittest.TestCase):
    """The package root is itself an evolution root: one live graph, one ledger.

    scripts/record_revision.py replaces the old workspace initializer -- a
    project root is seeded with `install <root> --from <plugin-root>` and
    checked with `verify <root>`. The tool needs pyyaml, so it runs under the
    interpreter running these tests, not a bare python3.
    """

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "record_revision.py"), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_packaged_root_verifies(self) -> None:
        # Head-agnostic on purpose: a pinned revision number would break at
        # every future landing, which is a test that fails for being correct.
        # The invariant is that verify agrees with the ledger's last entry.
        import yaml

        result = self._run("verify", str(PLUGIN_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = yaml.safe_load((PLUGIN_ROOT / "revisions.yaml").read_text())["entries"]
        head = entries[-1]
        self.assertIn(f"revision {head['revision']}", result.stdout)
        self.assertIn(head["digest_after"], result.stdout)

    def test_install_seeds_a_project_root_that_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "evo"
            installed = self._run("install", str(project), "--from", str(PLUGIN_ROOT))
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(
                (project / "workflow.pave.yaml").read_bytes(),
                (PLUGIN_ROOT / "workflow.pave.yaml").read_bytes(),
            )
            checked = self._run("verify", str(project))
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_packaged_ledger_holds_the_delivered_graph_and_the_lean_successors(self) -> None:
        import yaml

        entries = yaml.safe_load((PLUGIN_ROOT / "revisions.yaml").read_text())["entries"]
        # The ledger is append-only and numbered from the delivered graph, so
        # these hold at every head. A landed revision adds an entry; it never
        # renumbers, reorders, or rewrites one.
        revisions = [entry["revision"] for entry in entries]
        self.assertEqual(revisions, list(range(len(entries))))
        self.assertGreaterEqual(len(entries), 5)
        self.assertEqual(entries[0]["kind"], "graph")
        for entry in entries:
            self.assertIn(entry["kind"], {"graph", "binding"})
        self.assertIsNone(entries[0]["digest_before"])
        self.assertTrue(entries[0]["digest_after"].startswith("sha256:"))
        # Each landing chains from the previous head.
        for later in range(1, len(entries)):
            self.assertEqual(entries[later]["digest_before"], entries[later - 1]["digest_after"])
        # History that cannot move: the delivered lean successors 1-4.
        self.assertEqual(
            [entry["kind"] for entry in entries[:5]],
            ["graph", "graph", "binding", "graph", "binding"],
        )
        # Revision 2 is the field root's live graph (the 1.3.x root the README migrates).
        self.assertEqual(
            entries[2]["digest_after"],
            "sha256:e9f063e2cde9752a0f530c4ceb9fe425873df2777f5c9dfa4135bc209e444e7c",
        )
        # Revision 4 is the gate-2 loop bound and its instruments.
        self.assertEqual(
            entries[4]["digest_after"],
            "sha256:a81bb97d1b6842cdaee8ea85b3325304e02a44d551180fc2b4cc50e598b613b3",
        )
        # Every landing keeps its patch, so any revision can be read back.
        for entry in entries[1:]:
            self.assertTrue(
                (PLUGIN_ROOT / "history" / f"v{entry['revision']}.patch").is_file()
            )
        # The manifest and the freeze script the ledger replaced are gone.
        self.assertFalse((PLUGIN_ROOT / "workflow-manifest.yaml").exists())
        self.assertFalse((PLUGIN_ROOT / "scripts" / "freeze_revision.py").exists())
        self.assertFalse(
            (PLUGIN_ROOT / "codex" / "init_evolution_workspace.py").exists()
        )


if __name__ == "__main__":
    unittest.main()
