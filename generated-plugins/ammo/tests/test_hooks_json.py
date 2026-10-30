"""hooks/hooks.json is generated, holds the conventions §7 keep list, and nothing else."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

R = Path(__file__).resolve().parents[1]
HOOKS_JSON = R / "hooks" / "hooks.json"
GENERATOR = Path(
    os.environ.get("HOOKS_JSON_GENERATOR")
    or R.parent.parent / "planning-records" / "ammo" / "gen_hooks_json.py"
)
PREFIX = "${CLAUDE_PLUGIN_ROOT}/hooks/"

# Conventions §7. team-spawn-guard stays: it enforces the SKILL.md
# team-member-vs-subagent contract (its header records the reason).
REGISTERED = {
    "ammo-pretool-guard.sh", "ammo-skill-files-guard.sh",
    "ammo-env-default-guard.sh", "worktree-create-with-build.sh", "worktree-remove-cleanup.sh",
    "ammo-precompact.sh", "ammo-postcompact.sh", "ammo-stop-guard.sh", "ammo-subagent-release.sh",
    "ammo-gpu-release.sh", "ammo-monitor-reminder.sh", "ammo-audit-start-stamp.sh",
    "ammo-state-validate.sh", "ammo-next-step-reminder.sh", "ammo-artifact-layout-warn.sh",
    "audit-phase2-guard.sh", "inject-audit-phase2.sh", "ammo-team-spawn-guard.sh",
}
SOURCED_HELPERS = {"_ammo_is_lead.sh", "_find_campaign.sh"}
DROPPED = {
    "ammo-venv-python-guard.sh", "ammo-pip-guard.sh", "ammo-agent-cwd-guard.sh",
    "ammo-msg-mirror.sh", "ammo-msg-check.sh", "ammo-statusline.sh",
}


def test_dropped_message_hooks_are_gone():
    # The msg-check hook and its identity helper went with msg-mirror.
    for name in ("ammo-msg-check.sh", "_ammo_msg_identity.sh", "tests/test-ammo-msg-check.sh"):
        assert not (R / "hooks" / name).exists(), name


def load() -> dict:
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def registrations(data: dict):
    for event, groups in data["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                yield event, group.get("matcher"), hook


def script_names(data: dict) -> list[str]:
    names = []
    for _, _, hook in registrations(data):
        names += re.findall(re.escape(PREFIX) + r"([\w.-]+)", hook["command"])
    return names


def test_regenerates_byte_for_byte(tmp_path):
    if not GENERATOR.is_file():
        pytest.skip(f"generator not shipped with the plugin: {GENERATOR}")
    out = tmp_path / "hooks.json"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(out)], capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 3:
        pytest.skip(proc.stderr.strip())
    assert proc.returncode == 0, proc.stderr
    assert out.read_bytes() == HOOKS_JSON.read_bytes(), "hooks.json is stale: rerun gen_hooks_json.py"


def test_top_level_shape():
    data = load()
    assert set(data) == {"description", "hooks"}
    assert isinstance(data["description"], str) and data["description"].strip()
    for _, _, hook in registrations(data):
        assert hook["type"] == "command"


def test_every_registered_keep_hook_appears():
    assert REGISTERED <= set(script_names(load()))


def test_no_dropped_hook_or_statusline():
    text = HOOKS_JSON.read_text(encoding="utf-8")
    for name in DROPPED:
        assert name not in text
    assert "statusLine" not in text and "statusline" not in text


def test_only_keep_list_is_registered():
    assert set(script_names(load())) == REGISTERED
    assert not SOURCED_HELPERS & set(script_names(load()))


def test_commands_use_plugin_root_only():
    for _, _, hook in registrations(load()):
        assert "CLAUDE_PROJECT_DIR" not in hook["command"]
        assert ".claude/hooks" not in hook["command"]
        assert re.fullmatch(r'"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/[\w.-]+"', hook["command"]), hook["command"]


def test_every_command_path_exists():
    missing = sorted({n for n in script_names(load()) if not (R / "hooks" / n).is_file()})
    assert not missing, f"hooks.json names files that are not in hooks/: {missing}"


def test_sourced_helpers_exist():
    missing = sorted(n for n in SOURCED_HELPERS if not (R / "hooks" / n).is_file())
    assert not missing, missing


def test_auditor_hooks_moved_from_agent_frontmatter():
    # Plugin agents ignore `hooks` frontmatter, so the auditor's two hooks live
    # here. Its frontmatter Stop hook runs as SubagentStop, scoped by agent type.
    found = {(event, matcher, re.sub(r".*/", "", hook["command"]).strip('"')) for event, matcher, hook in registrations(load())}
    assert ("PostToolUse", "Write", "inject-audit-phase2.sh") in found
    assert ("SubagentStop", "^(ammo:)?ammo-auditor$", "audit-phase2-guard.sh") in found
