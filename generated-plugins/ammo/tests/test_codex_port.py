"""Package and Codex port checks for the ammo plugin.

Covers the two manifests and the marketplace entry, the ten Codex custom
agents and their installer, the harness-neutral lead skill, the harness
binding table, and the revision folder that README step 7 seeds.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = PLUGIN_ROOT / "agents"
CODEX_AGENTS_DIR = PLUGIN_ROOT / "codex" / "agents"
INSTALLER = PLUGIN_ROOT / "codex" / "install_agents.py"
LEAD_SKILL = PLUGIN_ROOT / "skills" / "ammo" / "SKILL.md"
HARNESS_BINDING = PLUGIN_ROOT / "references" / "harness-binding.md"

# Codex-only alias TOMLs and the role file each one reads.
CODEX_ALIASES = {
    "ammo-implementer": "ammo-impl-champion",
    "ammo-report": "ammo-report-writer",
}
# Roles whose Claude role file has no lead-skill read step.
SKILL_EXEMPT_ROLES = {"ammo-delegate", "ammo-report-writer"}
# Tool names of either harness. The lead skill names none of them;
# references/harness-binding.md owns the mapping.
HARNESS_TOOL_NAMES = (
    "spawn_agent", "followup_task", "wait_agent", "interrupt_agent",
    "send_input", "close_agent", "resume_agent", "list_agents", "send_message",
    "fork_turns", "fork_context", "multi_agent_v1", "exec_command",
    "write_stdin", "apply_patch",
    "SendMessage", "TaskStop", "ListAgents", "TeamCreate", "TeamDelete",
    "run_in_background", "subagent_type", "Agent tool", "Agent(",
    "EnterWorktree",
)
# Frontmatter fields that Claude Code ignores on plugin agents.
PLUGIN_AGENT_IGNORED_FIELDS = ("hooks", "mcpServers", "permissionMode")


def _declared_version() -> str:
    first = (PLUGIN_ROOT / "VERSION").read_text(encoding="utf-8").splitlines()[0]
    return first.partition(":")[2].strip()


def _role_stems() -> list[str]:
    return sorted(path.stem for path in AGENTS_DIR.glob("*.md"))


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path}: no frontmatter"
    return match.group(1)


def _load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _installer_module():
    spec = importlib.util.spec_from_file_location("ammo_install_agents", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackageStructureTests(unittest.TestCase):
    def test_manifests_are_native_and_carry_the_declared_version(self) -> None:
        declared = _declared_version()
        self.assertRegex(declared, r"^\d+\.\d+\.\d+$")
        codex = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for manifest in (codex, claude):
            self.assertEqual(manifest["name"], "ammo")
            self.assertEqual(manifest["version"], declared)
        # Codex event names differ from hooks/hooks.json, so Codex registers
        # its own file through the manifest key.
        self.assertEqual(codex["hooks"], "./codex/hooks.json")
        self.assertTrue((PLUGIN_ROOT / codex["hooks"]).is_file())
        self.assertEqual(codex["skills"], "./skills/")
        self.assertTrue((PLUGIN_ROOT / codex["skills"]).is_dir())
        self.assertEqual(codex["interface"]["displayName"], "AMMO")
        for skill in ("ammo", "ammo-report"):
            self.assertTrue((PLUGIN_ROOT / "skills" / skill / "SKILL.md").is_file(), skill)

    def test_claude_compatibility_names_every_hook_event_and_role(self) -> None:
        # The Claude Code manifest schema has no compatibility key, and
        # `claude plugin validate` warns on it, so the text lives in the lead
        # skill's metadata.compatibility.
        claude = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertNotIn("compatibility", claude)
        compatibility = " ".join(_frontmatter(LEAD_SKILL).partition("compatibility: >-\n")[2].split())
        events = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        for event in events:
            self.assertIn(event, compatibility)
        for stem in _role_stems():
            self.assertIn(f"ammo:{stem}", compatibility)
        # The investigator role comes from the pave-init plugin.
        self.assertIn("pave-init:adversarial-investigator", compatibility)
        self.assertNotIn("ammo:ammo-investigator", compatibility)
        self.assertIn("Python 3.11", compatibility)

    def test_marketplace_lists_the_plugin(self) -> None:
        marketplace = PLUGIN_ROOT.parents[1] / ".claude-plugin" / "marketplace.json"
        if not marketplace.is_file():
            self.skipTest("plugin is not inside the pave repo")
        plugins = json.loads(marketplace.read_text(encoding="utf-8"))["plugins"]
        entries = [entry for entry in plugins if entry["name"] == "ammo"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], "./generated-plugins/ammo")
        self.assertEqual(entries[0]["version"], _declared_version())
        self.assertEqual((marketplace.parents[1] / entries[0]["source"]).resolve(), PLUGIN_ROOT)

    def test_plugin_agents_carry_no_ignored_frontmatter(self) -> None:
        for path in sorted(AGENTS_DIR.glob("*.md")):
            frontmatter = _frontmatter(path)
            for field in PLUGIN_AGENT_IGNORED_FIELDS:
                self.assertIsNone(
                    re.search(rf"(?m)^{field}:", frontmatter), f"{path.name}: {field}"
                )
        # The auditor's Phase 2 delivery and stop check moved to hooks.json.
        events = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        commands = json.dumps(events)
        self.assertIn("inject-audit-phase2.sh", commands)
        self.assertIn("audit-phase2-guard.sh", commands)


class CustomAgentTests(unittest.TestCase):
    def test_custom_agents_parse_and_bootstrap_from_the_plugin_root(self) -> None:
        paths = sorted(CODEX_AGENTS_DIR.glob("ammo-*.toml"))
        self.assertEqual(len(paths), 10)
        for path in paths:
            data = _load_toml(path)
            for field in ("name", "description", "developer_instructions"):
                self.assertTrue(data.get(field), (path.name, field))
            self.assertEqual(data["name"], f"ammo:{path.stem}")
            # Roles keep the root agent's model and set only their effort.
            self.assertNotIn("model", data)
            self.assertNotIn("model_provider", data)
            self.assertIn(data["model_reasoning_effort"], {"medium", "high", "xhigh"})
            contract = data["developer_instructions"]
            role = CODEX_ALIASES.get(path.stem, path.stem)
            for phrase in (
                "runtime bootstrap only",
                "Do not add requirements from this TOML",
                "`plugin_root:`",
                f"agents/{role}.md",
                "references/harness-binding.md",
                "references/context-recovery.md under the plugin root",
            ):
                self.assertIn(phrase, contract, (path.name, phrase))
            # A TOML sends its role to the lead skill only when the Claude
            # role file does. The delegate and the report writer do not.
            if role in SKILL_EXEMPT_ROLES:
                self.assertNotIn("skills/ammo/SKILL.md", contract, path.name)
            else:
                self.assertIn("skills/ammo/SKILL.md", contract, path.name)
            if role == "ammo-report-writer":
                self.assertIn("skills/ammo-report/SKILL.md", contract, path.name)
            for legacy in ("skill root", ".codex/skills", "server", "session"):
                self.assertNotIn(legacy, contract, (path.name, legacy))

    def test_every_role_file_has_a_toml_twin(self) -> None:
        stems = _role_stems()
        self.assertEqual(len(stems), 8)
        for stem in stems:
            twin = CODEX_AGENTS_DIR / f"{stem}.toml"
            self.assertTrue(twin.is_file(), f"agents/{stem}.md has no codex/agents/{stem}.toml")
            self.assertIn(f"agents/{stem}.md", _load_toml(twin)["developer_instructions"])
        # Every TOML reads a role file that exists; only the aliases share one.
        for path in sorted(CODEX_AGENTS_DIR.glob("ammo-*.toml")):
            role = CODEX_ALIASES.get(path.stem, path.stem)
            self.assertIn(role, stems, path.name)

    def test_installer_expects_exactly_the_shipped_agents(self) -> None:
        module = _installer_module()
        self.assertEqual(module.MANIFEST_NAME, ".ammo-codex-agents.json")
        shipped = {path.name: f"ammo:{path.stem}" for path in CODEX_AGENTS_DIR.glob("ammo-*.toml")}
        self.assertEqual(module.EXPECTED_AGENTS, shipped)

    def test_lead_skill_names_no_harness_tool(self) -> None:
        skill = LEAD_SKILL.read_text(encoding="utf-8")
        frontmatter = _frontmatter(LEAD_SKILL)
        self.assertRegex(frontmatter, r"(?m)^name: ammo$")
        self.assertIn("Manual-only: use only when the user invokes /ammo:ammo.", frontmatter)
        self.assertRegex(frontmatter, r"(?m)^metadata:\n  compatibility:")
        self.assertIsNone(re.search(r"(?m)^hooks:", frontmatter))
        self.assertIn("$ammo:ammo", skill)
        self.assertIn("hooks/hooks.json", skill)
        self.assertIn("`references/harness-binding.md`", skill)
        for tool_name in HARNESS_TOOL_NAMES:
            self.assertNotIn(tool_name, skill)

    def test_harness_binding_maps_every_role_address(self) -> None:
        binding = HARNESS_BINDING.read_text(encoding="utf-8")
        for stem in _role_stems():
            self.assertIn(f"| `agents/{stem}.md` | `ammo:{stem}` |", binding)
        for alias in CODEX_ALIASES:
            self.assertIn(f"`ammo:{alias}`", binding)
        # Both Codex modes and both harnesses have a column.
        self.assertIn("| Operation | Claude Code | Codex V2 | Codex V1 |", binding)


class InstallerTests(unittest.TestCase):
    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), *args],
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_install_check_and_uninstall_are_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            installed = self._run("--project", str(project))
            self.assertEqual(installed.returncode, 0, installed.stderr)
            target = project / ".codex" / "agents"
            self.assertEqual(len(list(target.glob("ammo-*.toml"))), 10)
            self.assertTrue((target / ".ammo-codex-agents.json").is_file())
            checked = self._run("--project", str(project), "--check")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertIn("10 AMMO custom agents are current", checked.stdout)
            removed = self._run("--project", str(project), "--uninstall")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(target.exists())

    def test_check_fails_on_a_changed_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            self.assertEqual(self._run("--project", str(project)).returncode, 0)
            changed = project / ".codex" / "agents" / "ammo-auditor.toml"
            changed.write_text(changed.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8")
            checked = self._run("--project", str(project), "--check")
            self.assertEqual(checked.returncode, 1)
            self.assertIn("ammo-auditor.toml", checked.stderr)

    def test_installer_refuses_unowned_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            target = project / ".codex" / "agents"
            target.mkdir(parents=True)
            conflict = target / "ammo-resolver.toml"
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
            result = self._run("--user", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list((codex_home / "agents").glob("ammo-*.toml"))), 10)


class RevisionFolderTests(unittest.TestCase):
    """The package root is a revision folder: one live graph and its revision log.

    README step 7 seeds a checkout's `.ammo/evolution` with
    `record_revision.py install <root> --from <plugin-root>` and checks it with
    `verify <root>`. The tool needs pyyaml, so it runs under the interpreter
    that runs these tests.
    """

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PLUGIN_ROOT / "scripts" / "record_revision.py"), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_packaged_root_verifies_at_the_log_head(self) -> None:
        import yaml

        result = self._run("verify", str(PLUGIN_ROOT))
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = yaml.safe_load((PLUGIN_ROOT / "revisions.yaml").read_text(encoding="utf-8"))["entries"]
        head = entries[-1]
        self.assertIn(f"revision {head['revision']}", result.stdout)
        self.assertIn(head["digest_after"], result.stdout)

    def test_install_seeds_a_folder_that_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / ".ammo" / "evolution"
            installed = self._run("install", str(folder), "--from", str(PLUGIN_ROOT))
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(
                (folder / "workflow.pave.yaml").read_bytes(),
                (PLUGIN_ROOT / "workflow.pave.yaml").read_bytes(),
            )
            checked = self._run("verify", str(folder))
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_revision_log_is_append_only_from_the_delivered_graph(self) -> None:
        import yaml

        entries = yaml.safe_load((PLUGIN_ROOT / "revisions.yaml").read_text(encoding="utf-8"))["entries"]
        self.assertEqual([entry["revision"] for entry in entries], list(range(len(entries))))
        self.assertEqual(entries[0]["kind"], "graph")
        self.assertIsNone(entries[0]["digest_before"])
        self.assertTrue(entries[0]["digest_after"].startswith("sha256:"))
        for entry in entries:
            self.assertIn(entry["kind"], {"graph", "run_setup"})
        for later in range(1, len(entries)):
            self.assertEqual(entries[later]["digest_before"], entries[later - 1]["digest_after"])
            self.assertTrue((PLUGIN_ROOT / "history" / f"v{entries[later]['revision']}.patch").is_file())


if __name__ == "__main__":
    unittest.main()
