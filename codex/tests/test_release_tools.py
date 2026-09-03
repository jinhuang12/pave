from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock

import yaml

from codex import preflight
from scripts import build_packages
from scripts import stamp_version


REPO_ROOT = Path(__file__).resolve().parents[2]
PENDING_ROLES = ("update-reviewer", "workflow-updater")
PAVE_EVOLVE_TEMPLATE = "{{FRONTMATTER}}\n\n{{DISPATCH_CONTRACT}}\n"
EVOLVE_SKILL_ENTRY = """
[skills.pave-evolve]
template = "sources/pave-evolve/SKILL.md.tmpl"
output = "{output}"

[skills.pave-evolve.slots]
FRONTMATTER = '''---
name: pave-evolve
description: Fixture stub for the successor-proposal lead procedure.
---'''
DISPATCH_CONTRACT = '''Fixture dispatch contract.'''
"""
ROLE_TEMPLATE = """---
name: {role}
description: Fixture stub contract used until the real role source lands.
---

Work only on the assigned proposal and return the result to the parent.
"""
CLAUDE_ROLE_ENTRY = """
[roles.{role}]
sandbox_mode = "read-only"
model = "inherit"
effort = "inherit"
runtime = '''Fixture runtime text for {role}.'''
"""
CODEX_ROLE_ENTRY = """
[roles.{role}]
sandbox_mode = "read-only"
model = "inherit"
reasoning_effort = "inherit"
runtime = '''Fixture runtime text for {role}.'''
"""
EVOLVE_OUTPUTS = {
    "claude": "skills/pave-evolve/SKILL.md",
    "codex": "codex/skills/pave-evolve/SKILL.md",
}


def stage_pending_sources(root: Path) -> None:
    """Complete a copied tree with any 2.5.0 source that is not landed yet.

    Each stub is written only when the real file or binding table is absent, so a
    fixture exercises the generator both before and after the sources land.
    """
    template = root / "sources" / "pave-evolve" / "SKILL.md.tmpl"
    if not template.is_file():
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(PAVE_EVOLVE_TEMPLATE, encoding="utf-8")
    for role in PENDING_ROLES:
        source = root / "sources" / "roles" / f"{role}.md.tmpl"
        if not source.is_file():
            source.write_text(ROLE_TEMPLATE.format(role=role), encoding="utf-8")
    for harness, output in EVOLVE_OUTPUTS.items():
        path = root / "sources" / "bindings" / f"{harness}.toml"
        text = path.read_text(encoding="utf-8")
        additions = ""
        if "[skills.pave-evolve]" not in text:
            additions += EVOLVE_SKILL_ENTRY.format(output=output)
        role_entry = CLAUDE_ROLE_ENTRY if harness == "claude" else CODEX_ROLE_ENTRY
        for role in PENDING_ROLES:
            if f"[roles.{role}]" not in text:
                additions += role_entry.format(role=role)
        if additions:
            path.write_text(text.rstrip("\n") + "\n" + additions, encoding="utf-8")


def generated_digest(path: Path) -> str:
    """Return the source-sha256 a generated file was stamped with."""
    text = path.read_text(encoding="utf-8")
    marker = build_packages.GENERATED_MARKER
    if marker not in text:
        raise AssertionError(f"{path} carries no generated marker")
    return text.split(marker, 1)[1].split()[0]


def markdown_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise AssertionError(f"{path} has no closed frontmatter")
    frontmatter = text[4:].split("\n---\n", 1)[0]
    parsed = yaml.safe_load(frontmatter)
    if not isinstance(parsed, dict):
        raise AssertionError(f"{path} frontmatter is not a mapping")
    return parsed


def write_rollout(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def assistant_message(text: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def spawn_records(
    thread_id: str,
    parent_thread_id: str | None,
    depth: int,
    agent_role: str,
    call_id: str,
    agent_type: str,
    message: str,
    child_id: str,
) -> list[dict]:
    return [
        {
            "type": "session_meta",
            "payload": {
                "id": thread_id,
                "parent_thread_id": parent_thread_id,
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_thread_id,
                            "depth": depth,
                            "agent_role": agent_role,
                        }
                    }
                },
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": call_id,
                "arguments": json.dumps(
                    {"agent_type": agent_type, "message": message}
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"agent_id": child_id}),
            },
        },
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]


class ReleaseContractTests(unittest.TestCase):
    def test_role_model_and_effort_mappings(self) -> None:
        expected = {
            "pave-init:forward-tester": (None, "inherit"),
            "pave-init:pave-material-reviewer": ("gpt-5.6-sol", "high"),
            "pave-init:node-planner": ("gpt-5.6-sol", "xhigh"),
            "pave-init:research-delegate": ("gpt-5.6-terra", "high"),
            "pave-init:skill-builder": ("gpt-5.6-sol", "medium"),
            "pave-init:system-explorer": ("gpt-5.6-terra", "high"),
            "pave-init:workflow-updater": ("gpt-5.6-sol", "xhigh"),
            "pave-init:update-reviewer": ("gpt-5.6-sol", "high"),
        }
        for path in sorted((REPO_ROOT / "codex" / "agents").glob("*.toml")):
            with path.open("rb") as handle:
                data = tomllib.load(handle)
            self.assertEqual(
                (data.get("model"), data.get("model_reasoning_effort", "inherit")),
                expected[data["name"]],
                path,
            )

    def test_version_stamps_match_authority(self) -> None:
        version = (
            (REPO_ROOT / "skills" / "pave-init" / "VERSION")
            .read_text(encoding="utf-8")
            .splitlines()[0]
            .removeprefix("version:")
            .strip()
        )
        claude = json.loads(
            (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        codex = json.loads(
            (REPO_ROOT / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        marketplace = json.loads(
            (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        readme = (REPO_ROOT / "skills" / "pave-init" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(claude["version"], version)
        self.assertEqual(codex["version"], version)
        entries = [
            plugin
            for plugin in marketplace["plugins"]
            if plugin["name"] == claude["name"]
        ]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["version"], version)
        self.assertIn(f"Version: `{version}`", readme)

    def test_v1_depth_and_generated_model_doctrine_are_explicit(self) -> None:
        binding = (REPO_ROOT / "sources" / "bindings" / "codex.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("agents.max_depth = 2", binding)
        self.assertIn("features.multi_agent_v2 = false", binding)
        self.assertIn("top-capability and strong-judgment roles", binding)
        self.assertIn("`gpt-5.6-sol`", binding)
        self.assertIn("small, fast evidence roles", binding)
        self.assertIn("`gpt-5.6-terra`", binding)
        self.assertIn("Preserve the approved reasoning effort exactly", binding)

    def test_shared_doctrine_invariants_survive_generation(self) -> None:
        template = (REPO_ROOT / "sources" / "pave-init" / "SKILL.md.tmpl").read_text(
            encoding="utf-8"
        )
        planner = (REPO_ROOT / "sources" / "roles" / "node-planner.md.tmpl").read_text(
            encoding="utf-8"
        )
        review = (
            REPO_ROOT / "skills" / "pave-init" / "orchestration" / "review-and-build.md"
        ).read_text(encoding="utf-8")
        approvals = (
            REPO_ROOT / "skills" / "pave-init" / "references" / "approval-briefs.md"
        ).read_text(encoding="utf-8")
        hooks = (
            REPO_ROOT
            / "skills"
            / "pave-init"
            / "references"
            / "lead-alignment-hooks.md"
        ).read_text(encoding="utf-8")
        claude = (REPO_ROOT / "skills" / "pave-init" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        codex = (
            REPO_ROOT / "codex" / "skills" / "pave-init" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("Never re-brief a fresh reviewer mid-gate", template)
        self.assertIn("tool-forced selection", template)
        self.assertIn("observing or reinjection by default", template)
        self.assertIn("{{MODEL_RUNTIME_BINDING}}", template)
        self.assertIn("two roles", planner)
        self.assertIn("dispatched by the lead only", review)
        self.assertIn("compatibility", review)
        self.assertIn("re-render the brief after repair", approvals)
        self.assertIn('project_root="${PROJECT_ROOT:-}"', hooks)
        for skill in (claude, codex):
            self.assertIn("blocks at most one stop in three", " ".join(skill.split()))


class StampVersionTests(unittest.TestCase):
    OTHER_PLUGIN = {
        "name": "generated-plugin",
        "source": "./generated-plugins/generated-plugin",
        "description": "Owns its own version.",
        "version": "1.3.0",
    }
    PAVE_INIT_PLUGIN = {
        "name": "pave-init",
        "source": "./",
        "description": "Stamped from VERSION.",
        "version": "0.0.1",
    }

    def write_tree(self, root: Path, plugins: list[dict]) -> Path:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "skills" / "pave-init").mkdir(parents=True)
        manifest = {"name": "pave-init", "version": "0.0.1"}
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        marketplace = root / ".claude-plugin" / "marketplace.json"
        marketplace.write_text(
            json.dumps({"name": "market", "plugins": plugins}, indent=2) + "\n",
            encoding="utf-8",
        )
        (root / "skills" / "pave-init" / "VERSION").write_text(
            "version: 9.9.9\n", encoding="utf-8"
        )
        (root / "skills" / "pave-init" / "README.md").write_text(
            "# PAVE Init\n\nVersion: `0.0.1`\n", encoding="utf-8"
        )
        return marketplace

    def expected_outputs(self, root: Path) -> dict[Path, str]:
        with mock.patch.multiple(
            stamp_version,
            ROOT=root,
            VERSION_PATH=root / "skills" / "pave-init" / "VERSION",
            README_PATH=root / "skills" / "pave-init" / "README.md",
            PLUGIN_MANIFEST_PATH=root / ".claude-plugin" / "plugin.json",
            MARKETPLACE_PATH=root / ".claude-plugin" / "marketplace.json",
            JSON_PATHS=(
                root / ".claude-plugin" / "plugin.json",
                root / ".codex-plugin" / "plugin.json",
                root / ".claude-plugin" / "marketplace.json",
            ),
        ):
            return stamp_version.expected_files(stamp_version.authoritative_version())

    def test_two_plugin_marketplace_stamps_only_pave_init(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marketplace = self.write_tree(
                root, [dict(self.PAVE_INIT_PLUGIN), dict(self.OTHER_PLUGIN)]
            )
            before = marketplace.read_text(encoding="utf-8")
            outputs = self.expected_outputs(root)
            stamped = json.loads(outputs[marketplace])
            self.assertEqual(stamped["plugins"][0]["version"], "9.9.9")
            self.assertEqual(stamped["plugins"][1], self.OTHER_PLUGIN)
            other_block = json.dumps(self.OTHER_PLUGIN, indent=2).replace(
                "\n", "\n    "
            )
            self.assertIn(other_block, before)
            self.assertIn(other_block, outputs[marketplace])
            self.assertEqual(
                json.loads(outputs[root / ".claude-plugin" / "plugin.json"])["version"],
                "9.9.9",
            )

    def test_other_entry_first_is_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            marketplace = self.write_tree(
                root, [dict(self.OTHER_PLUGIN), dict(self.PAVE_INIT_PLUGIN)]
            )
            stamped = json.loads(self.expected_outputs(root)[marketplace])
            self.assertEqual(stamped["plugins"][0], self.OTHER_PLUGIN)
            self.assertEqual(stamped["plugins"][1]["version"], "9.9.9")

    def test_missing_pave_init_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_tree(root, [dict(self.OTHER_PLUGIN)])
            with self.assertRaisesRegex(
                stamp_version.StampError, "named 'pave-init', found 0"
            ):
                self.expected_outputs(root)

    def test_duplicate_pave_init_entry_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_tree(
                root, [dict(self.PAVE_INIT_PLUGIN), dict(self.PAVE_INIT_PLUGIN)]
            )
            with self.assertRaisesRegex(
                stamp_version.StampError, "named 'pave-init', found 2"
            ):
                self.expected_outputs(root)


class BuildSafetyTests(unittest.TestCase):
    def copy_repo(self, target: Path) -> None:
        shutil.copytree(
            REPO_ROOT,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
        )
        stage_pending_sources(target)

    def run_build(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(root / "scripts" / "build_packages.py"), *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

    def materialize(self, root: Path) -> subprocess.CompletedProcess[str]:
        """Write every generated output so a later --check can demand exit 0."""
        result = self.run_build(root, "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_role_template_placeholder_fails_build_and_check(self) -> None:
        for placeholder in (
            "{{UNKNOWN_SLOT}}",
            "{{weird-slot}}",
            "{{}}",
            "{{A\nB}}",
            "{{{UNKNOWN_SLOT}}}",
            "{{UNKNOWN_SLOT",
            "UNKNOWN_SLOT}}",
        ):
            with self.subTest(placeholder=placeholder), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "repo"
                self.copy_repo(root)
                role = root / "sources" / "roles" / "node-planner.md.tmpl"
                role.write_text(role.read_text() + f"\n{placeholder}\n", encoding="utf-8")
                for args in ((), ("--check",)):
                    result = self.run_build(root, *args)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("role templates do not accept placeholders", result.stderr)

    def test_skill_template_placeholder_fails_build_and_check(self) -> None:
        for placeholder in (
            "{{UNKNOWN_SLOT}}",
            "{{weird-slot}}",
            "{{}}",
            "{{A\nB}}",
            "{{{UNKNOWN_SLOT}}}",
            "{{UNKNOWN_SLOT",
            "UNKNOWN_SLOT}}",
        ):
            with self.subTest(placeholder=placeholder), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "repo"
                self.copy_repo(root)
                template = root / "sources" / "pave-init" / "SKILL.md.tmpl"
                template.write_text(
                    template.read_text(encoding="utf-8") + f"\n{placeholder}\n",
                    encoding="utf-8",
                )
                for args in (("--force",), ("--check",)):
                    result = self.run_build(root, *args)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("invalid placeholders", result.stderr)

    def test_generated_markdown_frontmatter_is_valid_yaml(self) -> None:
        paths = [
            REPO_ROOT / "skills" / "pave-init" / "SKILL.md",
            REPO_ROOT / "codex" / "skills" / "pave-init" / "SKILL.md",
            *sorted((REPO_ROOT / "agents").glob("*.md")),
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(markdown_frontmatter(path))

    def test_role_description_with_colon_stays_valid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            role = root / "sources" / "roles" / "node-planner.md.tmpl"
            role.write_text(
                role.read_text(encoding="utf-8").replace(
                    "description: ",
                    "description: Risk: ",
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_build(root, "--force")
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = markdown_frontmatter(root / "agents" / "node-planner.md")
            self.assertTrue(parsed["description"].startswith("Risk: "))
            skill = markdown_frontmatter(root / "skills" / "pave-init" / "SKILL.md")
            self.assertIn("hooks", skill)

    def test_binding_schema_rejects_invalid_binding_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            binding = root / "sources" / "bindings" / "codex.toml"
            binding.write_text(
                binding.read_text(encoding="utf-8").replace(
                    "version = 1",
                    'version = "1"',
                    1,
                ),
                encoding="utf-8",
            )
            result = self.run_build(root, "--check")
            self.assertEqual(result.returncode, 2)
            self.assertIn("schema validation failed", result.stderr)

    def test_every_skill_renders_for_every_harness(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            built = self.materialize(root)
            for relative in build_packages.GENERATED_SKILL_OUTPUTS:
                with self.subTest(output=relative):
                    self.assertIn(relative, built.stdout)
                    text = (root / relative).read_text(encoding="utf-8")
                    self.assertIn("DO NOT EDIT", text)
                    self.assertIn(build_packages.GENERATED_MARKER, text)
            claude = root / "skills" / "pave-evolve" / "SKILL.md"
            codex = root / "codex" / "skills" / "pave-evolve" / "SKILL.md"
            for path in (claude, codex):
                self.assertIn("name: pave-evolve", path.read_text(encoding="utf-8"))
            # One template, one binding per harness: same slots, different stamp.
            self.assertNotEqual(
                generated_digest(claude),
                generated_digest(codex),
            )
            self.assertEqual(self.run_build(root, "--check").returncode, 0)

    def test_binding_without_the_evolve_skill_fails_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            binding = root / "sources" / "bindings" / "claude.toml"
            binding.write_text(
                binding.read_text(encoding="utf-8").replace(
                    "[skills.pave-evolve", "[skills.pave-retired"
                ),
                encoding="utf-8",
            )
            result = self.run_build(root, "--check")
            self.assertEqual(result.returncode, 2)
            self.assertIn("missing ['pave-evolve']", result.stderr)

    def test_binding_skill_template_outside_the_table_fails_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            binding = root / "sources" / "bindings" / "claude.toml"
            binding.write_text(
                binding.read_text(encoding="utf-8").replace(
                    '"sources/pave-evolve/SKILL.md.tmpl"',
                    '"sources/roles/node-planner.md.tmpl"',
                ),
                encoding="utf-8",
            )
            result = self.run_build(root, "--check")
            self.assertEqual(result.returncode, 2)
            self.assertIn("pave-evolve template must be", result.stderr)

    def test_role_include_expands_and_restamps_every_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            fragment = root / "sources" / "fragments" / "shared-note.md"
            fragment.parent.mkdir(parents=True, exist_ok=True)
            fragment.write_text("Return only the assigned report.\n", encoding="utf-8")
            role = root / "sources" / "roles" / "node-planner.md.tmpl"
            role.write_text(
                role.read_text(encoding="utf-8")
                + "\n<!-- include: fragments/shared-note.md -->\n",
                encoding="utf-8",
            )
            claude_agent = root / "agents" / "node-planner.md"
            codex_agent = root / "codex" / "agents" / "pave_init_node_planner.toml"

            self.materialize(root)
            for path in (claude_agent, codex_agent):
                self.assertIn(
                    "Return only the assigned report.",
                    path.read_text(encoding="utf-8"),
                )
            stamps = {path: generated_digest(path) for path in (claude_agent, codex_agent)}

            fragment.write_text(
                "Return only the assigned report, then stop.\n", encoding="utf-8"
            )
            self.materialize(root)
            for path, stamp in stamps.items():
                self.assertIn("then stop.", path.read_text(encoding="utf-8"))
                self.assertNotEqual(generated_digest(path), stamp, path)

    def test_unusable_fragment_include_fails_build(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            fragments = root / "sources" / "fragments"
            fragments.mkdir(parents=True, exist_ok=True)
            (fragments / "nesting.md").write_text(
                "<!-- include: fragments/plain.md -->\n", encoding="utf-8"
            )
            (fragments / "plain.md").write_text("Plain shared sentence.\n", encoding="utf-8")
            (fragments / "braced.md").write_text("Use {{SLOT}} here.\n", encoding="utf-8")
            role = root / "sources" / "roles" / "node-planner.md.tmpl"
            original = role.read_text(encoding="utf-8")
            cases = {
                "fragments/absent.md": "not a readable file",
                "fragments/nesting.md": "nesting is not supported",
                "fragments/braced.md": "must not contain brace tokens",
                "fragments/../pave-init/SKILL.md.tmpl": "not a readable file",
                "roles/node-planner.md.tmpl": "must start with 'fragments/'",
            }
            for reference, message in cases.items():
                with self.subTest(reference=reference):
                    role.write_text(
                        f"{original}\n<!-- include: {reference} -->\n", encoding="utf-8"
                    )
                    result = self.run_build(root, "--check")
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertIn("sources/roles/node-planner.md.tmpl", result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_reference_marker_does_not_create_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            self.materialize(root)
            reference = (
                root
                / "skills"
                / "pave-init"
                / "references"
                / "approval-briefs.md"
            )
            reference.write_text(
                "<!-- Generated by scripts/build_packages.py; "
                "source-sha256: prose-example -->\n"
                + reference.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            result = self.run_build(root, "--check")
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_late_generated_marker_still_detects_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            orphan = root / "codex" / "agents" / "retired.toml"
            orphan.write_text(
                "x" * 5000
                + "\n# Generated by scripts/build_packages.py; source-sha256: late\n",
                encoding="utf-8",
            )
            result = self.run_build(root, "--check")
            self.assertEqual(result.returncode, 1)
            self.assertIn("ORPHAN codex/agents/retired.toml", result.stderr)

    def test_invalid_utf8_reports_build_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            (root / "agents" / "node-planner.md").write_bytes(b"\xff\xfe\xfd")
            result = self.run_build(root)
            self.assertEqual(result.returncode, 2)
            self.assertIn("not valid UTF-8", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_normal_build_refuses_to_overwrite_generated_hand_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
            generated = root / "agents" / "node-planner.md"
            generated.write_text(
                generated.read_text(encoding="utf-8") + "\n<!-- HAND EDIT -->\n",
                encoding="utf-8",
            )
            result = self.run_build(root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to overwrite generated files", result.stderr)
            self.assertIn("HAND EDIT", generated.read_text(encoding="utf-8"))

            forced = self.run_build(root, "--force")
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertNotIn("HAND EDIT", generated.read_text(encoding="utf-8"))

    def test_generated_outputs_warn_and_ci_checks_drift(self) -> None:
        generated = [
            REPO_ROOT / "skills" / "pave-init" / "SKILL.md",
            REPO_ROOT / "codex" / "skills" / "pave-init" / "SKILL.md",
            *sorted((REPO_ROOT / "agents").glob("*.md")),
            *sorted((REPO_ROOT / "codex" / "agents").glob("*.toml")),
        ]
        for path in generated:
            self.assertIn("DO NOT EDIT", path.read_text(encoding="utf-8")[:4096], path)
        workflow = REPO_ROOT / ".github" / "workflows" / "validate.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("build_packages.py --check", text)
        self.assertIn("unittest discover -s codex/tests", text)

    def test_atomic_write_rolls_back_all_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")
            outputs = {first: "new-first\n", second: "new-second\n"}
            real_replace = os.replace

            def fail_second_install(source: str | Path, destination: str | Path) -> None:
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    destination_path == second
                    and source_path.name.startswith(f".{second.name}.new.")
                ):
                    raise OSError("injected replace failure")
                real_replace(source, destination)

            with (
                mock.patch.object(build_packages, "ROOT", root),
                mock.patch.object(build_packages, "marked_generated_files", return_value=set()),
                mock.patch.object(
                    build_packages.os,
                    "replace",
                    side_effect=fail_second_install,
                ),
            ):
                with self.assertRaisesRegex(OSError, "injected replace failure"):
                    build_packages.write_outputs(outputs, force=True)

            self.assertEqual(first.read_text(encoding="utf-8"), "old-first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old-second\n")

    def test_backup_cleanup_failure_preserves_new_outputs_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("old-first\n", encoding="utf-8")
            second.write_text("old-second\n", encoding="utf-8")
            outputs = {first: "new-first\n", second: "new-second\n"}
            real_unlink = Path.unlink
            backup_unlinks: dict[Path, int] = {}
            injected = False

            def fail_one_backup_cleanup(
                path: Path, missing_ok: bool = False
            ) -> None:
                nonlocal injected
                if ".backup." in path.name:
                    backup_unlinks[path] = backup_unlinks.get(path, 0) + 1
                    if backup_unlinks[path] == 2 and not injected:
                        injected = True
                        raise OSError("injected backup cleanup failure")
                real_unlink(path, missing_ok=missing_ok)

            with (
                mock.patch.object(build_packages, "ROOT", root),
                mock.patch.object(build_packages, "marked_generated_files", return_value=set()),
                mock.patch.object(Path, "unlink", new=fail_one_backup_cleanup),
            ):
                with self.assertRaisesRegex(
                    build_packages.BuildError,
                    "backup cleanup was incomplete",
                ):
                    build_packages.write_outputs(outputs, force=True)

            self.assertEqual(first.read_text(encoding="utf-8"), "new-first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "new-second\n")
            backups = list(root.glob(".*.backup.*"))
            self.assertEqual(len(backups), 1)
            self.assertIn(
                backups[0].read_text(encoding="utf-8"),
                {"old-first\n", "old-second\n"},
            )


class PreflightProofTests(unittest.TestCase):
    def make_chain(self, home: Path, source_hash: str, nonce: str) -> str:
        root_id = "root-thread"
        parent_id = "parent-thread"
        delegate_id = "delegate-thread"
        sessions = home / "sessions" / "2026" / "08" / "21"
        root_records = spawn_records(
            root_id,
            None,
            0,
            "root",
            "root-call",
            "pave-init:pave-material-reviewer",
            f"source-sha256: {source_hash}; nonce: {nonce}",
            parent_id,
        )
        root_records.insert(
            -1,
            assistant_message(nonce),
        )
        parent_records = spawn_records(
            parent_id,
            root_id,
            1,
            "pave-init:pave-material-reviewer",
            "parent-call",
            "pave-init:research-delegate",
            f"return nonce {nonce}",
            delegate_id,
        )
        delegate_records = [
            {
                "type": "session_meta",
                "payload": {
                    "id": delegate_id,
                    "parent_thread_id": parent_id,
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": parent_id,
                                "depth": 2,
                                "agent_role": "pave-init:research-delegate",
                            }
                        }
                    },
                },
            },
            assistant_message(nonce),
            {"type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        write_rollout(sessions / f"rollout-test-{root_id}.jsonl", root_records)
        write_rollout(sessions / f"rollout-test-{parent_id}.jsonl", parent_records)
        write_rollout(sessions / f"rollout-test-{delegate_id}.jsonl", delegate_records)
        return root_id

    def test_complete_persisted_chain_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            result = preflight.verify_rollout_chain(
                home, root_id, "abc123", "nonce-42"
            )
            self.assertEqual(result["parent_thread_id"], "parent-thread")
            self.assertEqual(result["delegate_thread_id"], "delegate-thread")

    def test_missing_second_spawn_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            parent = next((home / "sessions").rglob("*parent-thread.jsonl"))
            records = [json.loads(line) for line in parent.read_text().splitlines()]
            write_rollout(parent, [records[0], records[-1]])
            with self.assertRaisesRegex(preflight.PreflightError, "research delegate"):
                preflight.verify_rollout_chain(home, root_id, "abc123", "nonce-42")

    def test_stale_source_hash_and_broken_parent_link_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "old-hash", "nonce-42")
            with self.assertRaisesRegex(preflight.PreflightError, "source hash"):
                preflight.verify_rollout_chain(home, root_id, "new-hash", "nonce-42")

            parent = next((home / "sessions").rglob("*parent-thread.jsonl"))
            records = [json.loads(line) for line in parent.read_text().splitlines()]
            records[0]["payload"]["parent_thread_id"] = "wrong-root"
            write_rollout(parent, records)
            with self.assertRaisesRegex(preflight.PreflightError, "parent link"):
                preflight.verify_rollout_chain(home, root_id, "old-hash", "nonce-42")

    def test_echo_without_rollouts_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with self.assertRaisesRegex(preflight.PreflightError, "rollout"):
                preflight.verify_rollout_chain(
                    home, "root-thread", "abc123", "nonce-42"
                )

    def test_malformed_and_incomplete_rollouts_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            delegate = next((home / "sessions").rglob("*delegate-thread.jsonl"))
            delegate.write_text("{not-json}\n", encoding="utf-8")
            with self.assertRaisesRegex(preflight.PreflightError, "malformed rollout"):
                preflight.verify_rollout_chain(home, root_id, "abc123", "nonce-42")

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            root = next((home / "sessions").rglob("*root-thread.jsonl"))
            records = [json.loads(line) for line in root.read_text().splitlines()]
            write_rollout(
                root,
                [
                    record
                    for record in records
                    if record.get("payload", {}).get("type") != "task_complete"
                ],
            )
            with self.assertRaisesRegex(preflight.PreflightError, "did not complete"):
                preflight.verify_rollout_chain(home, root_id, "abc123", "nonce-42")

    def test_delegate_parent_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            delegate = next((home / "sessions").rglob("*delegate-thread.jsonl"))
            records = [json.loads(line) for line in delegate.read_text().splitlines()]
            records[0]["payload"]["parent_thread_id"] = "wrong-parent"
            write_rollout(delegate, records)
            with self.assertRaisesRegex(preflight.PreflightError, "parent link"):
                preflight.verify_rollout_chain(home, root_id, "abc123", "nonce-42")

    def test_error_bearing_task_complete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root_id = self.make_chain(home, "abc123", "nonce-42")
            delegate = next((home / "sessions").rglob("*delegate-thread.jsonl"))
            records = [json.loads(line) for line in delegate.read_text().splitlines()]
            records[-1]["payload"]["error"] = {
                "message": "child request failed",
                "codex_error_info": "other",
            }
            write_rollout(delegate, records)
            with self.assertRaisesRegex(preflight.PreflightError, "completed with an error"):
                preflight.verify_rollout_chain(home, root_id, "abc123", "nonce-42")


if __name__ == "__main__":
    unittest.main()
