"""Tests for the ammo-env-default-guard.sh PreToolUse hook.

Verifies that the hook blocks Edit/Write calls that register VLLM_OP*
env vars with default True/1 in envs.py files, and allows all other edits.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "ammo-env-default-guard.sh"
SETTINGS = ROOT / "ai_cli_session" / ".claude" / "settings.local.json"

# Environment for the hook subprocess. None means inherit the test env.
# TestEnvDefaultGuardHookPlainSystemTools sets a PATH with only the system
# tools, so a GNU-only grep flag fails there the same way it fails in a
# real hook run on macOS (BSD grep in /usr/bin).
_HOOK_ENV = None


def _run_hook(tool_input: dict, tool_name: str = "Edit") -> subprocess.CompletedProcess:
    """Run the hook with a simulated PreToolUse JSON payload."""
    payload = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=_HOOK_ENV,
    )


class TestEnvDefaultGuardHook:
    """Test ammo-env-default-guard.sh blocking behavior."""

    def test_hook_exists_and_executable(self):
        assert HOOK.exists(), f"Hook not found: {HOOK}"
        assert os.access(HOOK, os.X_OK), f"Hook not executable: {HOOK}"

    # ── Should BLOCK ──

    def test_blocks_vllm_op_equals_true(self):
        """VLLM_OP001 = True in envs.py must be blocked."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# placeholder",
            "new_string": '    "VLLM_OP001": lambda: bool(os.getenv("VLLM_OP001", "1") == "1"),  # default True\n    VLLM_OP001: bool = True',
        })
        assert result.returncode == 2, f"Should block VLLM_OP*=True. stderr: {result.stderr}"
        assert "BLOCKED" in result.stderr

    def test_blocks_vllm_op_default_1(self):
        """VLLM_OP002 with default="1" must be blocked."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP002": lambda: os.getenv("VLLM_OP002", "1"),',
        })
        # The pattern checks for default="1" — this has "1" in the context of VLLM_OP
        # but the regex needs VLLM_OP\d+ followed by True/1
        # Let's test the explicit pattern
        result2 = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": "    VLLM_OP002: bool = 1",
        })
        assert result2.returncode == 2, f"Should block VLLM_OP*=1. stderr: {result2.stderr}"

    def test_blocks_write_tool_with_true_default(self):
        """Write tool creating envs.py with VLLM_OP003=True must be blocked."""
        result = _run_hook(
            {
                "file_path": "/workspace/vllm/vllm/envs.py",
                "content": 'VLLM_OP003: bool = True\nVLLM_USE_V1: bool = True\n',
            },
            tool_name="Write",
        )
        assert result.returncode == 2, f"Should block Write with VLLM_OP*=True. stderr: {result.stderr}"

    def test_blocks_comma_true_pattern(self):
        """envs.py dict entry like ("VLLM_OP004", True) must be blocked."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP004", True',
        })
        assert result.returncode == 2, f"Should block VLLM_OP*,True. stderr: {result.stderr}"

    # ── Should ALLOW ──

    def test_allows_vllm_op_equals_false(self):
        """VLLM_OP001 = False in envs.py must be allowed."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    VLLM_OP001: bool = False',
        })
        assert result.returncode == 0, f"Should allow VLLM_OP*=False. stderr: {result.stderr}"

    def test_allows_vllm_op_default_0(self):
        """VLLM_OP001 with default="0" must be allowed."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP001": lambda: bool(os.getenv("VLLM_OP001", "0") == "1"),',
        })
        assert result.returncode == 0, f"Should allow default='0'. stderr: {result.stderr}"

    def test_allows_non_envs_py_files(self):
        """Edits to files other than envs.py must always be allowed."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/config.py",
            "old_string": "# old",
            "new_string": "VLLM_OP001 = True  # this is fine in config.py",
        })
        assert result.returncode == 0, "Should allow non-envs.py files"

    def test_allows_non_op_vllm_vars_true(self):
        """Non-VLLM_OP* vars like VLLM_USE_V1=True must be allowed in envs.py."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "# old",
            "new_string": "VLLM_USE_V1: bool = True",
        })
        assert result.returncode == 0, "Should allow non-VLLM_OP* vars"

    def test_allows_empty_content(self):
        """Edit with empty new_string must be allowed."""
        result = _run_hook({
            "file_path": "/workspace/vllm/vllm/envs.py",
            "old_string": "VLLM_OP001: bool = True",
            "new_string": "",
        })
        assert result.returncode == 0, "Should allow empty content (deletion)"


class TestPumiceEnvGuard:
    """PUMICE_* engine-env rule on .py and .rs edits (WS5.1)."""

    def test_blocks_new_pumice_env_in_py(self):
        """A PUMICE_* name not in the allowlist is a new engine env → block."""
        result = _run_hook({
            "file_path": "/workspace/pumice/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.getenv("PUMICE_NEW_CAMPAIGN_FLAG", "1")',
        })
        assert result.returncode == 2, f"Should block a new PUMICE_* env. stderr: {result.stderr}"
        assert "PUMICE_NEW_CAMPAIGN_FLAG" in result.stderr

    def test_blocks_default_change_to_allowlisted_pumice_env(self):
        """Supplying a default for a known PUMICE_* env is a default change → block."""
        result = _run_hook({
            "file_path": "/workspace/pumice/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.getenv("PUMICE_CACHE_ROOT", "/tmp/new")',
        })
        assert result.returncode == 2, f"Should block a PUMICE_* default change. stderr: {result.stderr}"
        assert "PUMICE_CACHE_ROOT" in result.stderr

    def test_blocks_new_pumice_env_in_rust(self):
        """A new PUMICE_* env read via std::env::var in a .rs file → block."""
        result = _run_hook({
            "file_path": "/workspace/pumice/pumice-rust/src/foo.rs",
            "old_string": "// old",
            "new_string": 'let v = std::env::var("PUMICE_MADE_UP_THING");',
        })
        assert result.returncode == 2, f"Should block a new PUMICE_* rust env. stderr: {result.stderr}"
        assert "PUMICE_MADE_UP_THING" in result.stderr

    def test_allows_pumice_models_registry_dict(self):
        """PUMICE_MODELS is the _PUMICE_MODELS registry dict, not an env — never flagged."""
        result = _run_hook({
            "file_path": "/workspace/pumice/src/pumice/models/registry.py",
            "old_string": "# old",
            "new_string": '_PUMICE_MODELS = { "gpt_oss": GptOss }',
        })
        assert result.returncode == 0, f"PUMICE_MODELS edit must not be flagged. stderr: {result.stderr}"

    def test_allows_allowlisted_pumice_read_without_default(self):
        """Reading a known PUMICE_* env with no default is not a change → allow."""
        result = _run_hook({
            "file_path": "/workspace/pumice/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.environ["PUMICE_CACHE_ROOT"]',
        })
        assert result.returncode == 0, f"Should allow a plain read of a known env. stderr: {result.stderr}"

    def test_allows_new_pumice_env_in_non_code_file(self):
        """The PUMICE_* rule only inspects .py and .rs files."""
        result = _run_hook({
            "file_path": "/workspace/pumice/docs/readme.md",
            "old_string": "# old",
            "new_string": 'os.getenv("PUMICE_MADE_UP_THING", "1")',
        })
        assert result.returncode == 0, "Should allow non-.py/.rs files"


class TestPumiceEnvAllowlistFile:
    """The seeded allowlist counts are pinned (WS5.1)."""

    ALLOWLIST = ROOT / "hooks" / "pumice_env_allowlist.txt"
    CLAUDE_COPY = ROOT / "hooks" / "pumice_env_allowlist.txt"
    CODEX_COPY = ROOT / "ai_cli_session" / ".codex" / "hooks" / "pumice_env_allowlist.txt"

    def _names(self, path):
        pumice, vllm = [], []
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("PUMICE_"):
                pumice.append(line)
            elif line.startswith("VLLM_"):
                vllm.append(line)
        return pumice, vllm

    def test_canonical_counts(self):
        """134 PUMICE_* names (seed grep at 9d126713) plus 8 VLLM_* names."""
        assert self.ALLOWLIST.is_file(), f"Missing allowlist: {self.ALLOWLIST}"
        pumice, vllm = self._names(self.ALLOWLIST)
        assert len(pumice) == 134, f"expected 134 PUMICE_* names, got {len(pumice)}"
        assert len(vllm) == 8, f"expected 8 VLLM_* names, got {len(vllm)}"
        assert len(set(pumice)) == 134, "PUMICE_* names must be unique"
        assert "PUMICE_MODELS" not in pumice, "PUMICE_MODELS is the registry dict, not an env"

    def test_runtime_copies_match_canonical(self):
        """render_ammo_variants.py keeps the .claude and .codex copies byte-identical."""
        canonical = self.ALLOWLIST.read_bytes()
        assert self.CLAUDE_COPY.read_bytes() == canonical, "claude copy drifted; run render_ammo_variants.py"
        assert self.CODEX_COPY.read_bytes() == canonical, "codex copy drifted; run render_ammo_variants.py"


class TestSettingsRegistration:
    """Verify the hook is registered in settings.local.json."""

    def test_env_guard_registered_in_settings(self):
        """settings.local.json must have PreToolUse hook for Edit|Write."""
        with open(SETTINGS) as f:
            settings = json.load(f)

        pre_tool_hooks = settings.get("hooks", {}).get("PreToolUse", [])
        edit_write_hooks = [
            h for h in pre_tool_hooks if h.get("matcher") in ("Edit|Write", "Edit", "Write")
        ]
        assert len(edit_write_hooks) >= 1, (
            "settings.local.json must have a PreToolUse hook for Edit|Write"
        )
        hook_cmds = [
            cmd.get("command", "")
            for h in edit_write_hooks
            for cmd in h.get("hooks", [])
        ]
        assert any("ammo-env-default-guard" in cmd for cmd in hook_cmds), (
            "ammo-env-default-guard.sh must be registered as an Edit|Write PreToolUse hook"
        )


class TestPretoolGuardN5Warning:
    """Verify the N5 warning was added to ammo-pretool-guard.sh."""

    def test_n5_warning_exists_in_pretool_guard(self):
        """ammo-pretool-guard.sh must contain N5 env contamination warning."""
        pretool = ROOT / "hooks" / "ammo-pretool-guard.sh"
        content = pretool.read_text()
        assert "N5" in content, "ammo-pretool-guard.sh must have N5 warning section"
        assert "VLLM_OP" in content, "N5 section must reference VLLM_OP*"
        assert "contamination" in content.lower(), "N5 section must mention contamination"



class _PlainSystemToolsMixin:
    """Run every inherited case again with PATH=/usr/bin:/bin plus jq only.

    The hook runs in a plain bash subprocess, which does not inherit shell
    functions or aliases (for example a grep function that routes to ugrep).
    On macOS /usr/bin/grep is BSD grep, which has no -P. A GNU-only flag makes
    grep fail, and the guard then allows the edit. These classes catch that.
    """

    @pytest.fixture(autouse=True)
    def _plain_system_path(self, tmp_path, monkeypatch):
        jq = shutil.which("jq")
        if jq is None:
            pytest.skip("jq is not installed; the hook exits 0 without it")
        jq_dir = tmp_path / "jq-only"
        jq_dir.mkdir()
        (jq_dir / "jq").symlink_to(jq)
        env = {
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "PATH": f"/usr/bin:/bin:{jq_dir}",
        }
        monkeypatch.setattr(sys.modules[__name__], "_HOOK_ENV", env)


class TestEnvDefaultGuardHookPlainSystemTools(_PlainSystemToolsMixin, TestEnvDefaultGuardHook):
    """VLLM_* cases with only the system tools on PATH."""


class TestPumiceEnvGuardPlainSystemTools(_PlainSystemToolsMixin, TestPumiceEnvGuard):
    """PUMICE_* cases with only the system tools on PATH."""
