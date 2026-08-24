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


REPO_ROOT = Path(__file__).resolve().parents[2]


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
        self.assertEqual(marketplace["plugins"][0]["version"], version)
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


class BuildSafetyTests(unittest.TestCase):
    def copy_repo(self, target: Path) -> None:
        shutil.copytree(
            REPO_ROOT,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
        )

    def run_build(self, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(root / "scripts" / "build_packages.py"), *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )

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

    def test_reference_marker_does_not_create_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self.copy_repo(root)
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
