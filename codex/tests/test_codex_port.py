from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest
from unittest import mock

from codex import install_agents
from codex.hooks import post_tool_use_router


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_NAMES = {
    "pave_init_forward_tester",
    "pave_init_material_reviewer",
    "pave_init_node_planner",
    "pave_init_research_delegate",
    "pave_init_skill_builder",
    "pave_init_system_explorer",
}


@contextlib.contextmanager
def changed_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class PackageStructureTests(unittest.TestCase):
    def test_plugin_manifest_paths_exist(self) -> None:
        manifest_path = REPO_ROOT / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "pave-init")
        claude_manifest = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["version"], claude_manifest["version"])
        for key in ("skills", "hooks"):
            value = manifest[key]
            self.assertTrue(value.startswith("./"), (key, value))
            resolved = (REPO_ROOT / value).resolve()
            self.assertTrue(resolved.is_relative_to(REPO_ROOT.resolve()))
            self.assertTrue(resolved.exists(), resolved)

    def test_hook_config_has_expected_events_and_commands(self) -> None:
        path = REPO_ROOT / "codex" / "hooks" / "hooks.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        self.assertEqual(set(hooks), {"PostToolUse", "Stop"})
        commands: list[str] = []
        for groups in hooks.values():
            for group in groups:
                for handler in group["hooks"]:
                    self.assertEqual(handler["type"], "command")
                    commands.append(handler["command"])
        self.assertTrue(all("${PLUGIN_ROOT}" in command for command in commands))
        joined = "\n".join(commands)
        self.assertIn("post_tool_use_router.py\" staleness", joined)
        self.assertIn("post_tool_use_router.py\" layout", joined)
        self.assertIn("stop_alignment_check.sh", joined)
        self.assertNotIn("subagent_activity.py", joined)

    def test_custom_agents_parse_and_cover_every_role(self) -> None:
        files = sorted((REPO_ROOT / "codex" / "agents").glob("*.toml"))
        self.assertEqual(len(files), 6)
        names: set[str] = set()
        for path in files:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            for field in ("name", "description", "developer_instructions"):
                self.assertTrue(data.get(field), (path, field))
            self.assertEqual(path.stem, data["name"])
            self.assertIn(data.get("sandbox_mode"), {"read-only", "workspace-write"})
            self.assertIn("PAVE_PLUGIN_ROOT", data["developer_instructions"])
            self.assertIn("complete role contract", data["developer_instructions"])
            self.assertNotIn("runtime-binding.md", data["developer_instructions"])
            self.assertNotIn("read `<root>/agents/", data["developer_instructions"])
            names.add(data["name"])
        self.assertEqual(names, AGENT_NAMES)

    def test_generated_native_skills_and_roles_are_current(self) -> None:
        result = subprocess.run(
            ["python3", str(REPO_ROOT / "scripts" / "build_packages.py"), "--check"],
            text=True,
            capture_output=True,
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        claude = (REPO_ROOT / "skills" / "pave-init" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        codex = (
            REPO_ROOT / "codex" / "skills" / "pave-init" / "SKILL.md"
        ).read_text(encoding="utf-8")
        for heading in (
            "## Authority",
            "## Required resources",
            "## Run workspace",
            "## Multi-agent contract",
            "## Stage 1: Goal and fitness",
            "## Stage 6: Validate, review, and forward-test",
            "## Final delivery",
            "## Resume",
        ):
            self.assertIn(heading, claude)
            self.assertIn(heading, codex)
        self.assertIn("name: pave-init", codex)
        self.assertIn("Manual-only", codex)
        self.assertNotIn("runtime-binding.md", codex)
        self.assertNotIn("Read `<root>/skills/pave-init/SKILL.md`", codex)
        self.assertNotIn("Claude Code", codex)

    def test_binding_schema_and_source_inventory_are_complete(self) -> None:
        schema = json.loads(
            (REPO_ROOT / "sources" / "bindings" / "schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["version"]["const"], 1)
        role_sources = sorted((REPO_ROOT / "sources" / "roles").glob("*.md.tmpl"))
        self.assertEqual(len(role_sources), 6)
        self.assertEqual(
            {path.name for path in (REPO_ROOT / "sources" / "bindings").glob("*.toml")},
            {"claude.toml", "codex.toml"},
        )

    def test_check_rejects_missing_binding_and_orphaned_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            clone = Path(temp) / "repo"
            shutil.copytree(
                REPO_ROOT,
                clone,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            codex_binding = clone / "sources" / "bindings" / "codex.toml"
            original_binding = codex_binding.read_text(encoding="utf-8")
            codex_binding.unlink()
            missing = subprocess.run(
                ["python3", str(clone / "scripts" / "build_packages.py"), "--check"],
                text=True,
                capture_output=True,
                cwd=clone,
                check=False,
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("binding files differ", missing.stderr)

            codex_binding.write_text(original_binding, encoding="utf-8")
            orphan = clone / "codex" / "agents" / "pave_init_retired.toml"
            orphan.write_text(
                "# Generated by scripts/build_packages.py; source-sha256: retired\n",
                encoding="utf-8",
            )
            stale = subprocess.run(
                ["python3", str(clone / "scripts" / "build_packages.py"), "--check"],
                text=True,
                capture_output=True,
                cwd=clone,
                check=False,
            )
            self.assertEqual(stale.returncode, 1)
            self.assertIn("ORPHAN codex/agents/pave_init_retired.toml", stale.stderr)

            orphan.unlink()
            shared = clone / "skills" / "pave-init" / "orchestration" / "review-and-build.md"
            shared.write_text(
                shared.read_text(encoding="utf-8")
                + "\nUse spawn_agent with pave_init_skill_builder, then run codex exec.\n",
                encoding="utf-8",
            )
            leaked = subprocess.run(
                ["python3", str(clone / "scripts" / "build_packages.py"), "--check"],
                text=True,
                capture_output=True,
                cwd=clone,
                check=False,
            )
            self.assertEqual(leaked.returncode, 2)
            self.assertIn("harness mechanics remain in shared runtime sources", leaked.stderr)


class PatchAdapterTests(unittest.TestCase):
    def test_extracts_multi_file_add_update_delete_and_move(self) -> None:
        patch = """*** Begin Patch
*** Add File: planning/a.draft.pave.yaml
+id: n1
+kind: node
*** Update File: planning/frontier.yaml
@@
-old
+new
*** Delete File: planning/obsolete.draft.pave.yaml
*** Update File: planning/a.draft.pave.yaml
*** Move to: planning/b.draft.pave.yaml
@@
-old: value
+id: c7
*** End Patch
"""
        sections = post_tool_use_router.extract_patch_sections(patch)
        self.assertEqual(
            sections,
            [
                ("planning/a.draft.pave.yaml", "id: n1\nkind: node\nid: c7\n"),
                ("planning/frontier.yaml", "new\n"),
                ("planning/obsolete.draft.pave.yaml", ""),
                ("planning/b.draft.pave.yaml", "id: c7\n"),
            ],
        )

    def test_direct_subagent_identity_survives_expansion(self) -> None:
        payload = {
            "session_id": "s",
            "agent_id": "a1",
            "agent_type": "pave_init_node_planner",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: x\n+hello\n*** End Patch"
            },
        }
        adapted = post_tool_use_router._canonical_layout_payloads(payload)
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0]["agent_id"], "a1")
        self.assertEqual(adapted[0]["agent_type"], "pave_init_node_planner")
        self.assertEqual(adapted[0]["tool_input"]["file_path"], "x")
        self.assertEqual(adapted[0]["tool_input"]["content"], "hello\n")

    def test_identity_free_call_reaches_canonical_layout_policy(self) -> None:
        payload = {
            "session_id": "s",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: x\n+hello\n*** End Patch"
            },
        }
        adapted = post_tool_use_router._canonical_layout_payloads(payload)
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0]["tool_input"]["file_path"], "x")
        self.assertFalse(hasattr(post_tool_use_router, "active_agent_ids"))

    def test_staleness_delegates_caller_identity_to_canonical_policy(self) -> None:
        direct = {
            "session_id": "s",
            "agent_id": "a1",
            "agent_type": "pave_init_node_planner",
        }
        lead = {"session_id": "s"}
        with mock.patch.object(
            post_tool_use_router, "_run_canonical", return_value=(0, "", "")
        ) as run:
            post_tool_use_router._run_staleness(direct)
            post_tool_use_router._run_staleness(lead)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[1], direct)
            self.assertEqual(run.call_args_list[1].args[1], lead)


class InstallerTests(unittest.TestCase):
    def invoke(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            return install_agents.main(argv)

    def test_install_check_reject_modified_and_atomic_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            args = ["--project", str(project)]
            self.assertEqual(self.invoke(args), 0)
            self.assertEqual(self.invoke(args), 0)
            self.assertEqual(self.invoke(args + ["--check"]), 0)

            target = project / ".codex" / "agents"
            files = sorted(target.glob("pave_init_*.toml"))
            self.assertEqual(len(files), 6)
            modified = files[0]
            original = modified.read_text(encoding="utf-8")
            modified.write_text(original + "\n# local change\n", encoding="utf-8")

            self.assertEqual(self.invoke(args + ["--check"]), 1)
            self.assertEqual(self.invoke(args), 2)
            before = {path.name for path in target.glob("pave_init_*.toml")}
            self.assertEqual(self.invoke(args + ["--uninstall"]), 2)
            after = {path.name for path in target.glob("pave_init_*.toml")}
            self.assertEqual(before, after, "blocked uninstall must not remove other files")

            self.assertEqual(self.invoke(args + ["--uninstall", "--force"]), 0)
            self.assertFalse(target.exists())


class CanonicalHookIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hook_dir = REPO_ROOT / "skills" / "pave-init" / "hooks"
        cls.have_canonical = all(
            (cls.hook_dir / name).is_file()
            for name in (
                "_find_run_state.sh",
                "planning-layout-warn.sh",
                "state_staleness_reminder.sh",
                "stop_alignment_check.sh",
            )
        )

    def setUp(self) -> None:
        if not self.have_canonical:
            self.skipTest("canonical PAVE hook files are not present in this isolated port tree")

    def _run_router(
        self, mode: str, payload: dict[str, object], cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(REPO_ROOT / "codex" / "hooks" / "post_tool_use_router.py"),
                mode,
            ],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            cwd=cwd,
            env=env,
            check=False,
        )

    def _workspace(self, root: Path) -> tuple[Path, Path]:
        run_dir = root / ".pave" / "test-run"
        planning = run_dir / "planning"
        planning.mkdir(parents=True)
        state = run_dir / "run-state.json"
        state.write_text(
            json.dumps(
                {
                    "run_identity": {"run_id": "test-run"},
                    "traversal_history": [{"node": "n1", "outcome": "active"}],
                    "terminal_classification": {},
                }
            ),
            encoding="utf-8",
        )
        (root / ".pave-init-run").write_text(str(state) + "\n", encoding="utf-8")
        return state, planning

    def test_layout_and_staleness_wire_translation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, planning = self._workspace(root)
            env = os.environ.copy()
            env.update(
                {
                    "PLUGIN_ROOT": str(REPO_ROOT),
                    "TMPDIR": str(root / "tmp"),
                    "PAVE_INIT_STALE_SECONDS": "1",
                }
            )
            (root / "tmp").mkdir()

            bad_path = planning / "rogue.txt"
            patch = (
                "*** Begin Patch\n"
                f"*** Add File: {bad_path}\n"
                "+bad\n"
                "*** End Patch"
            )
            payload = {
                "session_id": "layout-session",
                "agent_id": "planner-1",
                "agent_type": "pave_init_node_planner",
                "tool_input": {"command": patch},
            }
            layout = self._run_router("layout", payload, root, env)
            self.assertEqual(layout.returncode, 0, layout.stderr)
            self.assertIn("matches no allowed planning/ pattern", layout.stdout)

            old = time.time() - 60
            os.utime(state, (old, old))
            stale = self._run_router(
                "staleness",
                {"session_id": "stale-session", "tool_input": {"command": "true"}},
                root,
                env,
            )
            self.assertEqual(stale.returncode, 0, stale.stderr)
            self.assertIn("run-state.json last written", stale.stdout)

    def test_identity_free_lead_edit_reaches_layout_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, planning = self._workspace(root)
            env = os.environ.copy()
            env.update({"PLUGIN_ROOT": str(REPO_ROOT), "TMPDIR": str(root / "tmp")})
            (root / "tmp").mkdir()

            bad_path = planning / "rogue.txt"
            patch = (
                "*** Begin Patch\n"
                f"*** Add File: {bad_path}\n"
                "+bad\n"
                "*** End Patch"
            )
            result = self._run_router(
                "layout",
                {"session_id": "lead-session", "tool_input": {"command": patch}},
                root,
                env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("matches no allowed planning/ pattern", result.stdout)

    def test_stop_hook_continues_once_then_accepts_active_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._workspace(root)
            env = os.environ.copy()
            env.update({"TMPDIR": str(root / "tmp")})
            (root / "tmp").mkdir()
            script = self.hook_dir / "stop_alignment_check.sh"
            first = subprocess.run(
                [str(script)],
                input=json.dumps(
                    {
                        "session_id": "stop-session",
                        "stop_hook_active": False,
                    }
                ),
                text=True,
                capture_output=True,
                cwd=root,
                env=env,
                check=False,
            )
            self.assertEqual(first.returncode, 2, first.stderr)
            self.assertIn("Socratic check", first.stderr)

            active = subprocess.run(
                [str(script)],
                input=json.dumps(
                    {
                        "session_id": "stop-session",
                        "stop_hook_active": True,
                    }
                ),
                text=True,
                capture_output=True,
                cwd=root,
                env=env,
                check=False,
            )
            self.assertEqual(active.returncode, 0, active.stderr)


if __name__ == "__main__":
    unittest.main()
