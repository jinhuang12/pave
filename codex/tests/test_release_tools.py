from __future__ import annotations

import contextlib
import io
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


def persisted_message(role: str, text: str, content_kind: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "content": [{"type": "input_text", "text": text}],
            "internal_chat_message_metadata_passthrough": {
                "content_item_kinds": [content_kind]
            },
        },
    }


def session_meta_record(
    thread_id: str,
    parent_thread_id: str | None,
    root_session_id: str,
    agent_path: str | None = None,
    agent_role: str | None = None,
    depth: int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "id": thread_id,
        "session_id": root_session_id,
        "parent_thread_id": parent_thread_id,
        "multi_agent_version": "v2",
        "source": "exec",
    }
    if agent_path is not None and agent_role is not None and depth is not None:
        payload.update(
            {
                "agent_path": agent_path,
                "agent_role": agent_role,
                "source": {
                    "subagent": {
                        "thread_spawn": {
                            "parent_thread_id": parent_thread_id,
                            "depth": depth,
                            "agent_path": agent_path,
                            "agent_role": agent_role,
                        }
                    }
                },
            }
        )
    return {"type": "session_meta", "payload": payload}


def turn_context_record(turn_id: str, role: str | None = None) -> dict:
    if role is None:
        model = "gpt-5.6-sol"
        effort = "high"
        sandbox_mode = "read-only"
    else:
        config = preflight.load_role_config(role)
        model = config["model"]
        effort = config["model_reasoning_effort"]
        sandbox_mode = config["sandbox_mode"]
    return {
        "type": "turn_context",
        "payload": {
            "turn_id": turn_id,
            "model": model,
            "effort": effort,
            "sandbox_policy": {"type": sandbox_mode},
            "multi_agent_version": "v2",
        },
    }


def task_complete_record(turn_id: str, message: str, error: object = None) -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "turn_id": turn_id,
            "last_agent_message": message,
            "error": error,
        },
    }


def spawn_records(
    call_id: str,
    agent_type: str,
    task_name: str,
    task_path: str,
    *,
    fork_turns: str = "none",
) -> list[dict]:
    return [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": call_id,
                "arguments": json.dumps(
                    {
                        "agent_type": agent_type,
                        "task_name": task_name,
                        "fork_turns": fork_turns,
                        "message": "opaque fixture brief",
                    }
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"task_name": task_path}),
            },
        },
    ]


def followup_records(call_id: str, task_path: str) -> list[dict]:
    return [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "followup_task",
                "call_id": call_id,
                "arguments": json.dumps(
                    {"target": task_path, "message": "opaque fixture follow-up"}
                ),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": "",
            },
        },
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

    def test_v2_concurrency_and_generated_model_doctrine_are_explicit(self) -> None:
        binding = (REPO_ROOT / "sources" / "bindings" / "codex.toml").read_text(
            encoding="utf-8"
        )
        self.assertIn("features.multi_agent_v2", binding)
        self.assertIn("max_concurrent_threads_per_session = 16", binding)
        self.assertIn("17 total V2 slots", binding)
        self.assertIn("Remove `agents.max_depth`", binding)
        self.assertNotIn("features.multi_agent_v2 = false", binding)
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
    SOURCE_HASH = "a" * 64
    ROOT_NONCE = "root-nonce"
    REVIEWER_NONCE = "reviewer-nonce"
    DELEGATE_NONCE = "delegate-nonce"
    REVIEWER_TASK = "pave_v2_preflight_reviewer_fixture"
    DELEGATE_TASK = "pave_v2_preflight_delegate_fixture"
    REVIEWER_PATH = f"/root/{REVIEWER_TASK}"
    DELEGATE_PATH = f"{REVIEWER_PATH}/{DELEGATE_TASK}"

    def rollout_path(self, home: Path, thread_id: str) -> Path:
        return home / "sessions" / "2026" / "09" / "04" / f"rollout-test-{thread_id}.jsonl"

    def records(self, home: Path, thread_id: str) -> list[dict]:
        return [
            json.loads(line)
            for line in self.rollout_path(home, thread_id).read_text(encoding="utf-8").splitlines()
        ]

    def make_chain(self, home: Path, source_hash: str | None = None) -> str:
        source_hash = source_hash or self.SOURCE_HASH
        root_id = "root-thread"
        parent_id = "reviewer-thread"
        delegate_id = "delegate-thread"
        reviewer_config = preflight.load_role_config(preflight.PARENT_AGENT)
        delegate_config = preflight.load_role_config(preflight.DELEGATE_AGENT)

        root_records = [
            session_meta_record(root_id, None, root_id),
            persisted_message(
                "user",
                "<skill>\n<name>pave-init:pave-init</name>\n"
                f"<!-- source-sha256: {source_hash} -->\n</skill>",
                preflight.SELECTED_SKILL_KIND,
            ),
            *spawn_records(
                "root-spawn",
                preflight.PARENT_AGENT,
                self.REVIEWER_TASK,
                self.REVIEWER_PATH,
            ),
            *followup_records("reviewer-followup", self.REVIEWER_PATH),
            turn_context_record("root-turn"),
            task_complete_record("root-turn", self.ROOT_NONCE),
        ]
        parent_records = [
            session_meta_record(
                parent_id,
                root_id,
                root_id,
                self.REVIEWER_PATH,
                preflight.PARENT_AGENT,
                1,
            ),
            persisted_message(
                "developer",
                reviewer_config["developer_instructions"],
                preflight.DEVELOPER_INSTRUCTIONS_KIND,
            ),
            *spawn_records(
                "delegate-spawn",
                preflight.DELEGATE_AGENT,
                self.DELEGATE_TASK,
                self.DELEGATE_PATH,
            ),
            turn_context_record("reviewer-turn-1", preflight.PARENT_AGENT),
            task_complete_record("reviewer-turn-1", self.REVIEWER_NONCE),
            turn_context_record("reviewer-turn-2", preflight.PARENT_AGENT),
            task_complete_record("reviewer-turn-2", self.REVIEWER_NONCE),
        ]
        delegate_records = [
            session_meta_record(
                delegate_id,
                parent_id,
                root_id,
                self.DELEGATE_PATH,
                preflight.DELEGATE_AGENT,
                2,
            ),
            persisted_message(
                "developer",
                delegate_config["developer_instructions"],
                preflight.DEVELOPER_INSTRUCTIONS_KIND,
            ),
            turn_context_record("delegate-turn", preflight.DELEGATE_AGENT),
            task_complete_record("delegate-turn", self.DELEGATE_NONCE),
        ]
        write_rollout(self.rollout_path(home, root_id), root_records)
        write_rollout(self.rollout_path(home, parent_id), parent_records)
        write_rollout(self.rollout_path(home, delegate_id), delegate_records)
        return root_id

    def verify(self, home: Path, source_hash: str | None = None) -> dict[str, str | int]:
        return preflight.verify_rollout_chain(
            home,
            "root-thread",
            source_hash or self.SOURCE_HASH,
            self.ROOT_NONCE,
            self.REVIEWER_NONCE,
            self.DELEGATE_NONCE,
            self.REVIEWER_TASK,
            self.DELEGATE_TASK,
        )

    def test_complete_persisted_v2_chain_and_reviewer_continuity_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            result = self.verify(home)
            self.assertEqual(result["parent_thread_id"], "reviewer-thread")
            self.assertEqual(result["delegate_thread_id"], "delegate-thread")
            self.assertEqual(result["multi_agent_version"], "v2")
            self.assertEqual(result["reviewer_turns"], 2)

    def test_v1_spawn_output_and_noncanonical_v2_spawn_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            root = self.rollout_path(home, "root-thread")
            records = self.records(home, "root-thread")
            output = next(
                record
                for record in records
                if record.get("payload", {}).get("call_id") == "root-spawn"
                and record.get("payload", {}).get("type") == "function_call_output"
            )
            output["payload"]["output"] = json.dumps({"agent_id": "legacy-id"})
            write_rollout(root, records)
            with self.assertRaisesRegex(preflight.PreflightError, "V1 agent id"):
                self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            root = self.rollout_path(home, "root-thread")
            records = self.records(home, "root-thread")
            call = next(
                record
                for record in records
                if record.get("payload", {}).get("call_id") == "root-spawn"
                and record.get("payload", {}).get("type") == "function_call"
            )
            arguments = json.loads(call["payload"]["arguments"])
            arguments["fork_turns"] = "all"
            call["payload"]["arguments"] = json.dumps(arguments)
            write_rollout(root, records)
            with self.assertRaisesRegex(preflight.PreflightError, "strict V2 reviewer spawn"):
                self.verify(home)

    def test_missing_or_wrong_injected_skill_record_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home, "b" * 64)
            with self.assertRaisesRegex(preflight.PreflightError, "source hash"):
                self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            root = self.rollout_path(home, "root-thread")
            records = [
                record
                for record in self.records(home, "root-thread")
                if preflight.SELECTED_SKILL_KIND
                not in record.get("payload", {})
                .get("internal_chat_message_metadata_passthrough", {})
                .get("content_item_kinds", [])
            ]
            write_rollout(root, records)
            with self.assertRaisesRegex(preflight.PreflightError, "injected PAVE Init skill"):
                self.verify(home)

    def test_echo_without_rollouts_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with self.assertRaisesRegex(preflight.PreflightError, "rollout"):
                preflight.verify_rollout_chain(
                    home,
                    "root-thread",
                    self.SOURCE_HASH,
                    self.ROOT_NONCE,
                    self.REVIEWER_NONCE,
                    self.DELEGATE_NONCE,
                    self.REVIEWER_TASK,
                    self.DELEGATE_TASK,
                )

    def test_malformed_candidate_and_incomplete_reviewer_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            delegate = self.rollout_path(home, "delegate-thread")
            delegate.write_text(
                json.dumps(
                    {
                        "parent_thread_id": "reviewer-thread",
                        "agent_path": self.DELEGATE_PATH,
                        "agent_role": preflight.DELEGATE_AGENT,
                    }
                )
                + "\n{not-json}\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(preflight.PreflightError, "malformed rollout"):
                self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            reviewer = self.rollout_path(home, "reviewer-thread")
            records = self.records(home, "reviewer-thread")
            write_rollout(
                reviewer,
                [
                    record
                    for record in records
                    if record.get("payload", {}).get("turn_id") != "reviewer-turn-2"
                ],
            )
            with self.assertRaisesRegex(preflight.PreflightError, "turn count"):
                self.verify(home)

    def test_top_level_and_nested_v2_metadata_are_both_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            delegate = self.rollout_path(home, "delegate-thread")
            records = self.records(home, "delegate-thread")
            records[0]["payload"]["multi_agent_version"] = "v1"
            write_rollout(delegate, records)
            with self.assertRaisesRegex(preflight.PreflightError, "expected one V2 rollout"):
                self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            delegate = self.rollout_path(home, "delegate-thread")
            records = self.records(home, "delegate-thread")
            records[0]["payload"]["source"]["subagent"]["thread_spawn"]["depth"] = 1
            write_rollout(delegate, records)
            with self.assertRaisesRegex(preflight.PreflightError, "expected one V2 rollout"):
                self.verify(home)

    def test_root_session_lineage_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            root = self.rollout_path(home, "root-thread")
            records = self.records(home, "root-thread")
            records[0]["payload"]["session_id"] = "wrong-root-session"
            write_rollout(root, records)
            with self.assertRaisesRegex(preflight.PreflightError, "root V2 session metadata"):
                self.verify(home)

        for thread_id in ("reviewer-thread", "delegate-thread"):
            with self.subTest(thread_id=thread_id), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                self.make_chain(home)
                path = self.rollout_path(home, thread_id)
                records = self.records(home, thread_id)
                records[0]["payload"]["session_id"] = "wrong-root-session"
                write_rollout(path, records)
                with self.assertRaisesRegex(preflight.PreflightError, "expected one V2 rollout"):
                    self.verify(home)

    def test_duplicate_child_link_and_failed_turn_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            duplicate = self.records(home, "delegate-thread")
            duplicate[0]["payload"]["id"] = "duplicate-delegate-thread"
            write_rollout(self.rollout_path(home, "duplicate-delegate-thread"), duplicate)
            with self.assertRaisesRegex(preflight.PreflightError, "found 2"):
                self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            delegate = self.rollout_path(home, "delegate-thread")
            records = self.records(home, "delegate-thread")
            records[-1]["payload"]["error"] = {
                "message": "child request failed",
                "codex_error_info": "other",
            }
            write_rollout(delegate, records)
            with self.assertRaisesRegex(preflight.PreflightError, "completed with an error"):
                self.verify(home)

    def test_role_content_model_effort_and_sandbox_must_match(self) -> None:
        mutations = {
            "developer instructions": lambda records: records[1]["payload"]["content"][0].update(
                {"text": "stale role"}
            ),
            "model": lambda records: records[4]["payload"].update({"model": "stale-model"}),
            "reasoning effort": lambda records: records[4]["payload"].update(
                {"effort": "low"}
            ),
            "sandbox": lambda records: records[4]["payload"].update(
                {"sandbox_policy": {"type": "workspace-write"}}
            ),
        }
        for message, mutate in mutations.items():
            with self.subTest(field=message), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                self.make_chain(home)
                reviewer = self.rollout_path(home, "reviewer-thread")
                records = self.records(home, "reviewer-thread")
                mutate(records)
                write_rollout(reviewer, records)
                with self.assertRaisesRegex(preflight.PreflightError, message):
                    self.verify(home)

    def test_nonce_routes_and_followup_target_must_be_complete(self) -> None:
        actors = {
            "root": ("root-thread", self.ROOT_NONCE),
            "reviewer": ("reviewer-thread", self.REVIEWER_NONCE),
            "research delegate": ("delegate-thread", self.DELEGATE_NONCE),
        }
        for label, (thread_id, nonce) in actors.items():
            with self.subTest(actor=label), tempfile.TemporaryDirectory() as temp:
                home = Path(temp)
                self.make_chain(home)
                path = self.rollout_path(home, thread_id)
                records = self.records(home, thread_id)
                completion = next(
                    record
                    for record in records
                    if record.get("payload", {}).get("type") == "task_complete"
                )
                completion["payload"]["last_agent_message"] = f"wrong-{nonce}"
                write_rollout(path, records)
                with self.assertRaisesRegex(preflight.PreflightError, label):
                    self.verify(home)

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_chain(home)
            root = self.rollout_path(home, "root-thread")
            records = self.records(home, "root-thread")
            followup = next(
                record
                for record in records
                if record.get("payload", {}).get("call_id") == "reviewer-followup"
                and record.get("payload", {}).get("type") == "function_call"
            )
            followup["payload"]["arguments"] = json.dumps(
                {"target": "/root/fresh-reviewer", "message": "wrong thread"}
            )
            write_rollout(root, records)
            with self.assertRaisesRegex(preflight.PreflightError, "followup_task"):
                self.verify(home)

    def test_project_pave_footprint_must_be_complete_and_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_agents = root / "project" / ".codex" / "agents"
            user_agents = root / "home" / "agents"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.install_agents.install(user_agents, force=False), 0)
            self.assertEqual(
                preflight.require_current_agents(project_agents, user_agents), "user"
            )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.install_agents.install(project_agents, force=False), 0)
            stale = project_agents / "pave_init_skill_builder.toml"
            stale.write_text(
                stale.read_text(encoding="utf-8") + "\n# stale project shadow\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(preflight.PreflightError, "project PAVE agents"):
                preflight.require_current_agents(project_agents, user_agents)

    def test_release_mode_forces_v2_slots_and_exact_project_trust(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            evidence = root / "evidence"
            project.mkdir()
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {"type": "thread.started", "thread_id": "root-thread"}
                )
                + "\n",
                stderr="",
            )
            chain = {
                "parent_thread_id": "reviewer-thread",
                "delegate_thread_id": "delegate-thread",
                "multi_agent_version": "v2",
                "reviewer_turns": 2,
            }
            with (
                mock.patch.object(preflight, "require_current_agents", return_value="project"),
                mock.patch.object(preflight, "generated_source_hash", return_value=self.SOURCE_HASH),
                mock.patch.object(preflight, "verify_rollout_chain", return_value=chain),
                mock.patch.object(preflight.subprocess, "run", return_value=completed) as run,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(
                    preflight.main(
                        [
                            "--release",
                            "--project",
                            str(project),
                            "--evidence-dir",
                            str(evidence),
                        ]
                    ),
                    0,
                )
            command = run.call_args.args[0]
            self.assertIn("features.multi_agent=true", command)
            self.assertIn("features.multi_agent_v2.enabled=true", command)
            self.assertIn("agents.enabled=true", command)
            self.assertIn("agents.max_concurrent_threads_per_session=16", command)
            self.assertIn(preflight.exact_project_trust_override(project), command)
            self.assertNotIn("agents.max_depth=2", command)
            self.assertNotIn("features.multi_agent_v2=false", command)


if __name__ == "__main__":
    unittest.main()
