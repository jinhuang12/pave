#!/usr/bin/env python3
"""Router run-reading modes, the write-log hook, and their registration.

Drives hooks/pre_tool_use_router.py and hooks/write_log.py via subprocess with
JSON payloads on stdin against a temporary project root: a run marker, a
schema-valid run state at artifacts/run/run-state.json, a `.lead-session`
sidecar, an evolution root carrying a workflow.pave.yaml with one deny glob,
and an increments/ directory. Mirrors tests/test_hooks.sh for the existing
guards (one case each). Run: <python> tests/test_runtime_bindings.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ROUTER = PLUGIN_ROOT / "hooks" / "pre_tool_use_router.py"
WRITE_LOG = PLUGIN_ROOT / "hooks" / "write_log.py"
sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import runtime_bindings as rb  # noqa: E402

ACTIVE_STATE: dict[str, object] = {
    "workflow_identity": {"run_id": "runtime-bindings-test"},
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

GRAPH = """pave:
  version: 0.3.0
  name: t
  runtime_bindings:
    deny:
      - glob: "increments/build-*.py"
        bound_to: [lead]
        reason: "Build scripts are seat work; the lead ordering them is the form under audit."
        remedy: "Brief a seat with the script's purpose and let the seat write it."
        created_by: "cp-1"
    caps:
      - family_glob: "leases/*-grant-*.md"
        max_per_checkpoint: 20
        created_by: "cp-1"
  nodes: []
"""

LEAD = {"session_id": "lead-1"}
TEAMMATE = {"session_id": "mate-1"}
SEAT = {"session_id": "lead-1", "agent_id": "seat-1", "agent_type": "vllm-neuron-parity:implementer"}
UPDATER = {"session_id": "lead-1", "agent_id": "upd-1", "agent_type": "pave-init:workflow-updater"}
REVIEWER = {"session_id": "lead-1", "agent_id": "rev-1", "agent_type": "pave-init:update-reviewer"}
LEAD_WITH_AGENT = {"session_id": "lead-1", "agent_type": "my-lead-profile"}  # `claude --agent`: no agent_id


class RunTree:
    def __init__(self, temp: str) -> None:
        self.project = Path(temp).resolve() / "proj with space"
        self.workspace = self.project / "artifacts"
        self.state = self.workspace / "run" / "run-state.json"
        self.state.parent.mkdir(parents=True)
        self.state.write_text(json.dumps(ACTIVE_STATE), encoding="utf-8")
        (self.project / ".vllm-neuron-parity-run").write_text(f"{self.state}\n", encoding="utf-8")
        self.state.with_name(self.state.name + ".lead-session").write_text("lead-1\n", encoding="utf-8")
        self.evolution = self.project / ".vllm-neuron-parity" / "evolution"
        self.evolution.mkdir(parents=True)
        (self.evolution / "workflow.pave.yaml").write_text(GRAPH, encoding="utf-8")
        (self.evolution / "revisions.yaml").write_text("entries: []\n", encoding="utf-8")
        self.increments = self.workspace / "campaigns" / "c1" / "increments"
        self.increments.mkdir(parents=True)
        (self.workspace / "campaigns" / "c1" / "design").mkdir()
        self.sidecar = self.state.with_name(self.state.name + ".audit-checkpoint.json")
        self.write_log = self.state.with_name(self.state.name + ".write-log.jsonl")

    def env(self, codex: bool = False) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("CODEX_PROJECT_DIR", None)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        if codex:
            env["CODEX_PROJECT_DIR"] = str(self.project)
        env["PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        return env

    def run(self, script: Path, mode: str | None, payload: dict | str, codex: bool = False) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, str(script)] + ([mode] if mode else [])
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            argv, input=raw, text=True, capture_output=True, check=False,
            cwd=self.project, env=self.env(codex), timeout=30,
        )

    def write_payload(self, actor: dict, path: Path, tool: str = "Write") -> dict:
        tool_input = {"file_path": str(path)}
        tool_input.update({"content": "x"} if tool == "Write" else {"old_string": "a", "new_string": "b"})
        return {**actor, "tool_name": tool, "tool_input": tool_input, "cwd": str(self.project)}

    def bash_payload(self, actor: dict, command: str, cwd: Path | None = None) -> dict:
        return {**actor, "tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or self.project)}

    def write_sidecar(self, **overrides: object) -> dict:
        sidecar = {
            "checkpoint_id": "cp-1", "at": "2026-09-10T19:34:05Z", "outcomes_at": 0, "bytes_at": 0,
            "state": "DUE", "findings_record": None, "stamped_proposals": [], "closed_by": None,
        }
        sidecar.update(overrides)
        self.sidecar.write_text(json.dumps(sidecar), encoding="utf-8")
        return sidecar


def advisory(result: subprocess.CompletedProcess[str]) -> str:
    doc = json.loads(result.stdout)
    assert set(doc) == {"hookSpecificOutput"}, doc
    assert doc["hookSpecificOutput"]["hookEventName"] == "PreToolUse", doc
    return doc["hookSpecificOutput"]["additionalContext"]


class RouterModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.tree = RunTree(self._temp.name)
        self.denied = self.tree.increments / "build-x.py"

    def tearDown(self) -> None:
        self._temp.cleanup()

    # --- runtime-bindings ---------------------------------------------------

    def test_lead_write_to_denied_path_is_blocked_with_remedy(self) -> None:
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.denied))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Remedy: Brief a seat", result.stderr)
        self.assertIn("Reason: Build scripts", result.stderr)
        self.assertIn("increments/build-*.py", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_seat_write_to_denied_path_passes_with_advisory(self) -> None:
        for actor in (SEAT, TEAMMATE):
            with self.subTest(actor=actor):
                result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(actor, self.denied))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Build scripts are seat work", advisory(result))

    def test_lead_bash_naming_denied_path_is_blocked(self) -> None:
        payload = self.tree.bash_payload(LEAD, "python3 increments/build-x.py", cwd=self.tree.increments.parent)
        result = self.tree.run(ROUTER, "runtime-bindings", payload)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Remedy:", result.stderr)
        # An absolute token blocks too; an unrelated command passes.
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.bash_payload(LEAD, f"cat > '{self.denied}'"))
        self.assertEqual(result.returncode, 2, result.stderr)
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.bash_payload(LEAD, "git status"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lead_write_to_undenied_path_passes(self) -> None:
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.tree.increments / "evidence-1.md"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_landing_marker_fails_open_for_lead(self) -> None:
        (self.tree.evolution / ".landing").write_text("", encoding="utf-8")
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.denied))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_graph_parse_error_fails_open(self) -> None:
        (self.tree.evolution / "workflow.pave.yaml").write_text("pave:\n  runtime_bindings:\n    deny: [\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.denied))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_minimal_parser_reads_the_block_without_pyyaml(self) -> None:
        parsed = rb.parse_runtime_bindings(GRAPH, use_yaml=False)
        self.assertEqual(parsed["deny"][0]["glob"], "increments/build-*.py")
        self.assertEqual(parsed["deny"][0]["bound_to"], ["lead"])
        self.assertEqual(parsed["deny"][0]["created_by"], "cp-1")
        self.assertEqual(parsed["caps"][0]["max_per_checkpoint"], 20)
        self.assertEqual(rb.parse_runtime_bindings(GRAPH, use_yaml=True), parsed)
        self.assertEqual(rb.parse_runtime_bindings("pave:\n  name: t\n", use_yaml=False), {"deny": [], "caps": []})
        with self.assertRaises(ValueError):
            rb.parse_runtime_bindings("runtime_bindings:\n  deny: scalar\n", use_yaml=False)

    # --- no-recut -------------------------------------------------------------

    def test_no_recut_blocks_lap_suffix_beside_same_stem_under_increments(self) -> None:
        (self.tree.increments / "foo.md").write_text("x\n", encoding="utf-8")
        for actor in (LEAD, SEAT):
            with self.subTest(actor=actor):
                result = self.tree.run(ROUTER, "no-recut", self.tree.write_payload(actor, self.tree.increments / "foo-r2.md"))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("edit the existing file", result.stderr)
                self.assertIn("never -rN", result.stderr)
        # A -rM sibling counts as the same stem too; a different extension does not.
        (self.tree.increments / "bar-r1.sh").write_text("x\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "no-recut", self.tree.write_payload(LEAD, self.tree.increments / "bar-r2.sh"))
        self.assertEqual(result.returncode, 2, result.stderr)
        result = self.tree.run(ROUTER, "no-recut", self.tree.write_payload(LEAD, self.tree.increments / "bar-r2.py"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_recut_passes_outside_increments_and_in_place(self) -> None:
        design = self.tree.workspace / "campaigns" / "c1" / "design"
        (design / "foo.md").write_text("x\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "no-recut", self.tree.write_payload(LEAD, design / "foo-r2.md"))
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.tree.increments / "foo.md").write_text("x\n", encoding="utf-8")
        (self.tree.increments / "foo-r2.md").write_text("x\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "no-recut", self.tree.write_payload(LEAD, self.tree.increments / "foo-r2.md", "Edit"))
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- audit-sidecar --------------------------------------------------------

    def test_lead_writing_hook_owned_files_is_blocked(self) -> None:
        for target in (self.tree.sidecar, self.tree.write_log, self.tree.write_log.with_name(self.tree.write_log.name.replace(".jsonl", ".1.jsonl"))):
            with self.subTest(target=target.name):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, target))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("hook-owned", result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"echo x > '{self.tree.sidecar}'"))
        self.assertEqual(result.returncode, 2, result.stderr)
        for actor in (SEAT, UPDATER, REVIEWER):     # hook-owned files: no actor writes them
            result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(actor, self.tree.sidecar))
            self.assertEqual(result.returncode, 2, result.stderr)
        marker = Path(tempfile.gettempdir()) / (rb.COOLDOWN_PREFIX + "lead-1")
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"echo 999999 > '{marker}'"))
        self.assertEqual(result.returncode, 2, result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, self.tree.state))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_lead_session_accepts_only_the_writers_own_id(self) -> None:
        lead_file = self.tree.state.with_name(self.tree.state.name + ".lead-session")
        own = dict(LEAD, tool_name="Write", tool_input={"file_path": str(lead_file), "content": "lead-1\n"})
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", own).returncode, 0)
        edit = dict(LEAD, tool_name="Edit", tool_input={"file_path": str(lead_file), "old_string": "1", "new_string": "lead-1"})
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", edit).returncode, 2)     # result would be lead-lead-1
        foreign = dict(LEAD, tool_name="Write", tool_input={"file_path": str(lead_file), "content": "bogus\n"})
        result = self.tree.run(ROUTER, "audit-sidecar", foreign)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("own id", result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"echo bogus > '{lead_file}'"))
        self.assertEqual(result.returncode, 2, result.stderr)
        mate = dict(TEAMMATE, tool_name="Write", tool_input={"file_path": str(lead_file), "content": "lead-1\n"})
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", mate).returncode, 2)   # not its own id
        resumed = {"session_id": "lead-2", "tool_name": "Write", "tool_input": {"file_path": str(lead_file), "content": "lead-2"}}
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", resumed).returncode, 0)  # a resumed lead re-claims

    def test_role_names_match_exactly_including_codex_forms(self) -> None:
        findings = self.tree.evolution / rb.FINDINGS_NAME
        findings.write_text("checkpoint: cp-1\n", encoding="utf-8")
        for fake in ("workflow-updater", "my-workflow-updater", "update-reviewer", "x-update-reviewer"):
            with self.subTest(agent_type=fake):
                seat = {"session_id": "lead-1", "agent_id": "z-1", "agent_type": fake}
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(seat, findings))
                self.assertEqual(result.returncode, 2, result.stderr)
        codex = {"session_id": "lead-1", "agent_id": "u-1", "agent_type": "pave_init_workflow_updater"}
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(codex, findings)).returncode, 2)  # flat name on Claude: a seat
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(codex, findings), codex=True).returncode, 0)
        self.tree.sidecar.write_text(json.dumps({"checkpoint_id": "cp-1", "state": "DUE", "stamped_proposals": []}), encoding="utf-8")
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(codex, findings), codex=True)
        self.assertEqual(json.loads(self.tree.sidecar.read_text())["state"], "OPEN")

    def test_live_graph_and_ledger_change_only_through_a_landing(self) -> None:
        graph = self.tree.evolution / "workflow.pave.yaml"
        ledger = self.tree.evolution / "revisions.yaml"
        for actor in (LEAD, SEAT, UPDATER):
            for command in (f"cp /tmp/x.yaml '{graph}'", f"tee '{ledger}' < /tmp/x", f"echo x >> '{graph}'",
                            f"sed -i 's/a/b/' '{ledger}'", f"mv /tmp/x.yaml '{graph}'"):
                with self.subTest(actor=actor.get("agent_type", "lead"), command=command[:20]):
                    result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(actor, command))
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("record_revision.py land", result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"cat '{graph}' && cp '{graph}' /tmp/copy.yaml"))
        self.assertEqual(result.returncode, 0, result.stderr)               # reading and copying OUT pass
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(
            LEAD, f"python3 scripts/record_revision.py land '{self.tree.evolution}' 9 --proposal p.patch --stamps '{self.tree.sidecar}'"))
        self.assertEqual(result.returncode, 0, result.stderr)               # the landing command is a mention
        (self.tree.evolution / ".landing").write_text("", encoding="utf-8")
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"cp /tmp/x.yaml '{graph}'"))
        self.assertEqual(result.returncode, 0, result.stderr)               # inside a landing window
        (self.tree.evolution / ".landing").unlink()
        proposal = self.tree.evolution / "proposals" / "cp-1-graph.patch"
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"cp /tmp/x.patch '{proposal}'"))
        self.assertEqual(result.returncode, 0, result.stderr)               # proposals/ is not the ledger surface
        evo = self.tree.evolution
        for command in (f"cp /tmp/fake/workflow.pave.yaml '{evo}/'", f"cp /tmp/fake/workflow.pave.yaml '{evo}'",
                        f"mv /tmp/fake/workflow.pave.yaml '{evo}/'", f"rsync -a /tmp/fake/ '{evo}/'",
                        f"cp -R /tmp/fake/. '{evo}/'", f"mv '{evo}' '{evo}.bak'", f"rm -rf '{evo}'",
                        f"mv '{graph}' /tmp/g.yaml", f"mv '{ledger}' '{ledger}.bak'"):
            with self.subTest(command=command[:28]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 2, command)             # directory destinations and moves away
        for command in (f"mv '{self.tree.sidecar}' /tmp/x.json", f"mv '{self.tree.sidecar}' '{self.tree.sidecar}.bak'",
                        f"mv '{self.tree.write_log}' /tmp/w.jsonl"):
            with self.subTest(command=command[:28]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 2, command)             # a move destroys its source
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"cp '{self.tree.sidecar}' /tmp/x.json"))
        self.assertEqual(result.returncode, 0, result.stderr)               # copying OUT reads the source

    def test_links_to_protected_files_are_the_protected_files(self) -> None:
        run_dir = self.tree.state.parent
        dangling = run_dir / "notes.json"
        # creating a dangling link whose target names the sidecar is itself a write of the sidecar
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(
            LEAD, f"ln -s {self.tree.sidecar.name} '{dangling}'"))
        self.assertEqual(result.returncode, 2, result.stderr)
        os.symlink(self.tree.sidecar.name, dangling)             # force it; the Write through it is still denied
        self.assertFalse(self.tree.sidecar.exists())
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, dangling))
        self.assertEqual(result.returncode, 2, result.stderr)
        dangling.unlink()
        self.tree.sidecar.write_text("{}", encoding="utf-8")
        for command in (f"cd '{run_dir}' && ln {self.tree.sidecar.name} hl.json",
                        f"ln {self.tree.sidecar.name[:-5]}* hl.json"):
            with self.subTest(command=command[:30]):
                payload = self.tree.bash_payload(LEAD, command)
                payload["cwd"] = str(run_dir) if command.startswith("ln") else str(self.tree.project)
                result = self.tree.run(ROUTER, "audit-sidecar", payload)
                self.assertEqual(result.returncode, 2, result.stderr)
        hard = run_dir / "hl.json"
        os.link(self.tree.sidecar, hard)                          # force it; the Write through it is still denied
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, hard))
        self.assertEqual(result.returncode, 2, result.stderr)
        hard.unlink()
        relative = dict(LEAD, tool_name="Write", tool_input={"file_path": self.tree.sidecar.name, "content": "{}"}, cwd=str(run_dir))
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", relative).returncode, 2)

    def test_case_aliased_spellings_are_the_same_protected_paths(self) -> None:
        """On a case-insensitive volume RUN-STATE.JSON.AUDIT-CHECKPOINT.JSON is the sidecar."""
        alias_dir = Path(str(self.tree.state.parent).upper())
        if not alias_dir.exists():
            self.skipTest("case-sensitive volume: no alias to test")
        self.tree.sidecar.write_text("{}", encoding="utf-8")
        for name in (self.tree.sidecar.name, self.tree.state.name + ".lead-session"):
            with self.subTest(name=name):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, alias_dir / name.upper()))
                self.assertEqual(result.returncode, 2, result.stderr)
        findings_alias = Path(str(self.tree.evolution).upper()) / rb.FINDINGS_NAME.upper()
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, findings_alias))
        self.assertEqual(result.returncode, 2, result.stderr)
        marker = Path(tempfile.gettempdir()) / (rb.COOLDOWN_PREFIX.upper() + "lead-1")
        self.assertEqual(self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, marker)).returncode, 2)
        aliased_glob_target = Path(str(self.tree.increments).upper()) / "BUILD-X.PY"
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, aliased_glob_target))
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_lead_mentioning_hook_owned_files_in_bash_passes_writing_them_blocks(self) -> None:
        land = (f"python3 scripts/record_revision.py land '{self.tree.evolution}' 9 "
                f"--proposal '{self.tree.evolution}/proposals/cp-1-binding.patch' --stamps '{self.tree.sidecar}'")
        for command in (land, f"cat '{self.tree.sidecar}'", f"grep foo < '{self.tree.write_log}'"):
            with self.subTest(command=command[:40]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 0, result.stderr)
        for command in (f"echo x >> '{self.tree.sidecar}'", f"cp a.json '{self.tree.sidecar}'",
                        f"tee '{self.tree.write_log}' < a", f"sed -i 's/a/b/' '{self.tree.sidecar}'",
                        f"cat a && rm -f '{self.tree.sidecar}'", f"dd if=/dev/zero of='{self.tree.sidecar}' count=1"):
            with self.subTest(command=command[:40]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 2, command)

    def test_lead_never_writes_the_findings_record(self) -> None:
        findings = self.tree.evolution / rb.FINDINGS_NAME
        for tool in ("Write", "Edit", "MultiEdit"):
            with self.subTest(tool=tool):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, findings, tool))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("workflow-updater writes it", result.stderr)
        for command in (f"echo 'no_change_warranted' >> '{findings}'", f"cp draft.md '{findings}'",
                        f"FOO=1 BAR=2 dd if=x of='{findings}'"):
            with self.subTest(command=command[:40]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 2, command)
        for actor in (UPDATER, REVIEWER):
            with self.subTest(actor=actor["agent_type"]):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(actor, findings, "Edit"))
                self.assertEqual(result.returncode, 0, result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(LEAD, f"cat '{findings}'"))
        self.assertEqual(result.returncode, 0, result.stderr)
        # no other seat either: a generic subagent, a teammate, or a lead whose session no longer
        # matches .lead-session (flipped or resumed) all read as seats and are denied the record
        generic = {"session_id": "lead-1", "agent_id": "gp-1", "agent_type": "general-purpose"}
        for actor in (SEAT, TEAMMATE, generic):
            with self.subTest(actor=str(actor)):
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(actor, findings, "Edit"))
                self.assertEqual(result.returncode, 2, result.stderr)
                result = self.tree.run(ROUTER, "audit-sidecar", self.tree.bash_payload(actor, f"echo x >> '{findings}'"))
                self.assertEqual(result.returncode, 2, result.stderr)
        self.tree.state.with_name(self.tree.state.name + ".lead-session").write_text("someone-else\n")
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(LEAD, findings))
        self.assertEqual(result.returncode, 2, result.stderr)
        result = self.tree.run(ROUTER, "audit-sidecar", self.tree.write_payload(SEAT, self.tree.sidecar))
        self.assertEqual(result.returncode, 2, result.stderr)      # hook-owned files: no actor writes them

    def test_agent_type_alone_is_still_the_lead(self) -> None:
        target = self.tree.increments / "build-x.py"
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD_WITH_AGENT, target))
        self.assertEqual(result.returncode, 2, result.stderr)
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(TEAMMATE, target))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_absolute_glob_matches_outside_the_workspace(self) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="opusaudit-"))
        try:
            graph = GRAPH.replace('glob: "increments/build-*.py"', f'glob: "{scratch}/*.py"')
            (self.tree.evolution / "workflow.pave.yaml").write_text(graph, encoding="utf-8")
            result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, scratch / "ledger_write_017.py"))
            self.assertEqual(result.returncode, 2, result.stderr)
            result = self.tree.run(ROUTER, "runtime-bindings",
                                   self.tree.bash_payload(LEAD, f"python3 {scratch}/ledger_write_017.py"))
            self.assertEqual(result.returncode, 2, result.stderr)
            result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.tree.increments / "build-x.py"))
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            (self.tree.evolution / "workflow.pave.yaml").write_text(GRAPH, encoding="utf-8")

    def test_run_state_sidecars_and_ledger_are_never_denied(self) -> None:
        graph = GRAPH.replace('glob: "increments/build-*.py"', 'glob: "*"')
        (self.tree.evolution / "workflow.pave.yaml").write_text(graph, encoding="utf-8")
        try:
            for target in (self.tree.state, self.tree.state.with_name(self.tree.state.name + ".lead-session"),
                           self.tree.evolution / "revisions.yaml", self.tree.evolution / "workflow.pave.yaml",
                           self.tree.evolution / "history" / "v9.patch"):
                with self.subTest(target=target.name):
                    result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, target))
                    self.assertEqual(result.returncode, 0, result.stderr)
            result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.tree.increments / "any.md"))
            self.assertEqual(result.returncode, 2, result.stderr)
        finally:
            (self.tree.evolution / "workflow.pave.yaml").write_text(GRAPH, encoding="utf-8")

    def test_reviewer_review_line_is_stamped_lead_typing_is_not(self) -> None:
        self.tree.write_sidecar(state="OPEN")
        findings = self.tree.evolution / rb.FINDINGS_NAME
        findings.write_text("checkpoint: cp-1\n\noutcome: no_change_warranted\n\nreview: PASS\n", encoding="utf-8")
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(LEAD, findings))
        self.assertIsNone(json.loads(self.tree.sidecar.read_text()).get("review"))
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(REVIEWER, findings, "Edit"))
        self.assertEqual(json.loads(self.tree.sidecar.read_text())["review"], "PASS")
        findings.write_text("checkpoint: cp-1\n\nreview: REVISE\n", encoding="utf-8")
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(REVIEWER, findings, "Edit"))
        self.assertEqual(json.loads(self.tree.sidecar.read_text())["review"], "REVISE")

    # --- audit-stamp ----------------------------------------------------------

    def test_updater_findings_write_flips_sidecar_open(self) -> None:
        self.tree.write_sidecar()
        findings = self.tree.evolution / rb.FINDINGS_NAME
        findings.write_text("checkpoint: cp-1\n\n## Trend\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(LEAD, findings))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.tree.sidecar.read_text())["state"], "DUE")
        result = self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(UPDATER, findings))
        stamped = json.loads(self.tree.sidecar.read_text())
        self.assertEqual(stamped.get("findings_stat", {}).get("size"), findings.stat().st_size)
        self.assertEqual(result.returncode, 0, result.stderr)
        sidecar = json.loads(self.tree.sidecar.read_text())
        self.assertEqual(sidecar["state"], "OPEN")
        self.assertEqual(sidecar["findings_record"], str(findings))
        # Another checkpoint id on the first line does not open this one.
        self.tree.write_sidecar()
        findings.write_text("checkpoint: cp-0\n", encoding="utf-8")
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(UPDATER, findings))
        self.assertEqual(json.loads(self.tree.sidecar.read_text())["state"], "DUE")

    def test_updater_proposal_write_is_stamped(self) -> None:
        self.tree.write_sidecar()
        proposals = self.tree.evolution / "proposals"
        proposals.mkdir()
        patch = proposals / "cp-1-binding.patch"
        patch.write_text("kind: binding\n---\n--- a\n+++ b\n", encoding="utf-8")
        result = self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(UPDATER, patch))
        self.assertEqual(result.returncode, 0, result.stderr)
        stamps = json.loads(self.tree.sidecar.read_text())["stamped_proposals"]
        self.assertEqual(len(stamps), 1)
        self.assertEqual(stamps[0]["path"], str(patch))
        self.assertEqual(stamps[0]["size"], patch.stat().st_size)
        self.assertAlmostEqual(stamps[0]["mtime"], patch.stat().st_mtime)
        # A second write of the same file replaces the stamp; a lead write adds none.
        patch.write_text("kind: binding\n---\n--- a\n+++ b\n+x\n", encoding="utf-8")
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(UPDATER, patch))
        stamps = json.loads(self.tree.sidecar.read_text())["stamped_proposals"]
        self.assertEqual([s["size"] for s in stamps], [patch.stat().st_size])
        self.tree.run(ROUTER, "audit-stamp", self.tree.write_payload(LEAD, proposals / "cp-1-graph.patch"))
        self.assertEqual(len(json.loads(self.tree.sidecar.read_text())["stamped_proposals"]), 1)

    # --- fail open --------------------------------------------------------------

    def test_new_modes_fail_open_on_garbage_and_without_marker(self) -> None:
        for mode in ("runtime-bindings", "no-recut", "audit-sidecar", "audit-stamp"):
            with self.subTest(mode=mode, case="garbage"):
                result = self.tree.run(ROUTER, mode, "not json")
                self.assertEqual((result.returncode, result.stdout), (0, ""))
        (self.tree.project / ".vllm-neuron-parity-run").unlink()
        (self.tree.increments / "foo.md").write_text("x\n", encoding="utf-8")
        for mode, payload in (
            ("runtime-bindings", self.tree.write_payload(LEAD, self.denied)),
            ("no-recut", self.tree.write_payload(LEAD, self.tree.increments / "foo-r2.md")),
            ("audit-sidecar", self.tree.write_payload(LEAD, self.tree.sidecar)),
        ):
            with self.subTest(mode=mode, case="no marker"):
                result = self.tree.run(ROUTER, mode, payload)
                self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_terminal_run_passes(self) -> None:
        state = dict(ACTIVE_STATE)
        state["terminal_classification"] = {"status": "accepted", "endpoint": "done"}
        self.tree.state.write_text(json.dumps(state), encoding="utf-8")
        result = self.tree.run(ROUTER, "runtime-bindings", self.tree.write_payload(LEAD, self.denied))
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- existing guard modes (one case each, as in tests/test_hooks.sh) -------

    def test_existing_guard_modes_still_block_and_pass(self) -> None:
        cases = {
            "protected-branch": "git push origin HEAD:main",
            "compile-cache": "rm -rf ~/.cache/vllm/neuron/compile_cache",
            "venv-opt": "cp -a /opt/venv /tmp/copied-venv",
        }
        for guard, command in cases.items():
            with self.subTest(guard=guard):
                result = self.tree.run(ROUTER, guard, self.tree.bash_payload(LEAD, command))
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("BLOCKED", result.stderr)
                result = self.tree.run(ROUTER, guard, self.tree.bash_payload(LEAD, "git status"))
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_router_stays_under_400_lines(self) -> None:
        self.assertLess(len(ROUTER.read_text(encoding="utf-8").splitlines()), 400)


class WriteLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.tree = RunTree(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.tree.write_log.read_text(encoding="utf-8").splitlines()]

    def test_write_path_is_logged_with_actor_fields(self) -> None:
        target = self.tree.increments / "evidence-1.md"
        result = self.tree.run(WRITE_LOG, None, self.tree.write_payload(SEAT, target))
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        records = self.records()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["path"], str(target))
        self.assertEqual(record["tool"], "Write")
        self.assertEqual((record["session_id"], record["agent_id"], record["agent_type"]), ("lead-1", "seat-1", SEAT["agent_type"]))
        self.assertRegex(record["at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.tree.run(WRITE_LOG, None, self.tree.write_payload(LEAD, target, "Edit"))
        self.assertEqual([r["agent_id"] for r in self.records()], ["seat-1", None])

    def test_bash_argv_paths_are_logged_and_non_paths_ignored(self) -> None:
        existing = self.tree.workspace / "notes.txt"
        existing.write_text("x\n", encoding="utf-8")
        command = (
            f"python3 increments/build-x.py --out=increments/out.log notes.txt 2>&1 "
            f">'{self.tree.workspace}/campaigns/c1/design/log.txt' | grep -v foo && git status https://example.com/x"
        )
        result = self.tree.run(WRITE_LOG, None, self.tree.bash_payload(LEAD, command, cwd=self.tree.increments.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        # notes.txt is not at cwd (campaigns/c1), so it is not a path here.
        paths = {r["path"] for r in self.records()}
        self.assertEqual(paths, {
            str(self.tree.increments / "build-x.py"),
            str(self.tree.increments / "out.log"),
            str(self.tree.workspace / "campaigns" / "c1" / "design" / "log.txt"),
        })
        self.assertTrue(all(r["tool"] == "Bash" for r in self.records()))
        result = self.tree.run(WRITE_LOG, None, self.tree.bash_payload(LEAD, "cat notes.txt", cwd=self.tree.workspace))
        self.assertIn(str(existing), {r["path"] for r in self.records()})

    def test_written_flag_separates_writes_from_mentions(self) -> None:
        fresh = self.tree.workspace / "fresh.txt"
        fresh.write_text("new\n", encoding="utf-8")
        old = self.tree.workspace / "old.out"
        old.write_text("old\n", encoding="utf-8")
        os.utime(old, (1_600_000_000, 1_600_000_000))
        self.tree.run(WRITE_LOG, None, self.tree.bash_payload(LEAD, f"cat '{fresh}' '{old}' '{self.tree.workspace}/missing.txt'", cwd=self.tree.workspace))
        by_path = {Path(r["path"]).name: r for r in self.records()}
        self.assertTrue(by_path["fresh.txt"]["written"])
        self.assertEqual(by_path["fresh.txt"]["size"], 4)
        self.assertFalse(by_path["old.out"]["written"])
        self.assertAlmostEqual(by_path["old.out"]["mtime"], 1_600_000_000)
        self.assertFalse(by_path["missing.txt"]["written"])
        self.assertIsNone(by_path["missing.txt"]["size"])
        self.tree.run(WRITE_LOG, None, self.tree.write_payload(LEAD, old, "Edit"))
        self.assertTrue(self.records()[-1]["written"])

    def test_silent_without_marker_and_on_garbage(self) -> None:
        result = self.tree.run(WRITE_LOG, None, "not json")
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        (self.tree.project / ".vllm-neuron-parity-run").unlink()
        result = self.tree.run(WRITE_LOG, None, self.tree.write_payload(LEAD, self.tree.increments / "a.md"))
        self.assertEqual((result.returncode, result.stdout), (0, ""))
        self.assertFalse(self.tree.write_log.exists())

    def test_rotation_keeps_one_generation(self) -> None:
        self.tree.write_log.write_bytes(b"x" * (rb.WRITE_LOG_ROTATE_BYTES + 1))
        self.tree.run(WRITE_LOG, None, self.tree.write_payload(LEAD, self.tree.increments / "a.md"))
        rotated = self.tree.write_log.with_name(self.tree.write_log.name.replace(".jsonl", ".1.jsonl"))
        self.assertTrue(rotated.exists())
        self.assertEqual(len(self.records()), 1)


class RegistrationTests(unittest.TestCase):
    def test_hooks_json_registers_the_new_modes(self) -> None:
        hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

        def commands(event: str, matcher: str) -> list[str]:
            return [h["command"] for g in hooks[event] if g.get("matcher") == matcher for h in g["hooks"]]

        pre_bash = commands("PreToolUse", "Bash")
        for mode in ("protected-branch", "compile-cache", "venv-opt", "runtime-bindings", "audit-sidecar"):
            self.assertTrue(any(c.endswith(f"pre_tool_use_router.py\" {mode}") for c in pre_bash), mode)
        pre_edit = commands("PreToolUse", "Edit|Write|MultiEdit")
        self.assertTrue(any("graph_edit_guard.sh" in c for c in pre_edit))
        for mode in ("runtime-bindings", "no-recut", "audit-sidecar"):
            self.assertTrue(any(c.endswith(f"pre_tool_use_router.py\" {mode}") for c in pre_edit), mode)
        self.assertTrue(any(c.endswith("pre_tool_use_router.py\" audit-stamp") for c in commands("PostToolUse", "Write|Edit|MultiEdit")))
        self.assertTrue(any("hooks/write_log.py" in c for c in commands("PostToolUse", "Bash|Write|Edit|MultiEdit")))
        for group in hooks["PreToolUse"] + hooks["PostToolUse"]:
            for handler in group["hooks"]:
                command = handler["command"]
                start = command.index("${CLAUDE_PLUGIN_ROOT}/") + len("${CLAUDE_PLUGIN_ROOT}/")
                self.assertTrue((PLUGIN_ROOT / command[start:command.index('"', start)]).is_file(), command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
