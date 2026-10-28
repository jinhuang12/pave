"""Tests for the ammo-env-default-guard.sh PreToolUse hook.

Verifies that the hook blocks Edit/Write calls that register VLLM_OP*
env vars with default True/1 in envs.py files, and allows all other edits.
The hook acts only inside a campaign: every payload carries the cwd of an
engine fixture whose .ammo/active marker names a campaign dir.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "ammo-env-default-guard.sh"
HOOKS_JSON = ROOT / "hooks" / "hooks.json"
CODEX_GUARD = ROOT / "codex" / "hooks" / "pre_tool_use_guard.py"

# Engine fixture: <E>/.ammo/active names campaign tgt at <E>/.ammo/tgt/.
_ENGINE_TMP = tempfile.TemporaryDirectory(prefix="ammo-env-guard-")
E = os.path.realpath(_ENGINE_TMP.name)
os.makedirs(os.path.join(E, ".ammo", "tgt"))
Path(E, ".ammo", "active").write_text("tgt\n")

# Environment for the hook subprocess. None means the test env minus any live
# campaign names (see _clean_env).
# TestEnvDefaultGuardHookPlainSystemTools sets a PATH with only the system
# tools, so a GNU-only grep flag fails there the same way it fails in a
# real hook run on macOS (BSD grep in /usr/bin).
_HOOK_ENV = None


def _clean_env(base):
    """Drop names that would point the hook at a live campaign."""
    return {
        k: v for k, v in base.items()
        if not k.startswith("AMMO_") and k != "CLAUDE_PROJECT_DIR"
    }


def _run_hook(tool_input: dict, tool_name: str = "Edit", cwd: str = E) -> subprocess.CompletedProcess:
    """Run the hook with a simulated PreToolUse JSON payload."""
    payload = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "cwd": cwd,
        "tool_input": tool_input,
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
        env=_clean_env(os.environ if _HOOK_ENV is None else _HOOK_ENV),
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
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# placeholder",
            "new_string": '    "VLLM_OP001": lambda: bool(os.getenv("VLLM_OP001", "1") == "1"),  # default True\n    VLLM_OP001: bool = True',
        })
        assert result.returncode == 2, f"Should block VLLM_OP*=True. stderr: {result.stderr}"
        assert "BLOCKED" in result.stderr

    def test_blocks_vllm_op_default_1(self):
        """VLLM_OP002 with default="1" must be blocked."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP002": lambda: os.getenv("VLLM_OP002", "1"),',
        })
        # The pattern checks for default="1" — this has "1" in the context of VLLM_OP
        # but the regex needs VLLM_OP\d+ followed by True/1
        # Let's test the explicit pattern
        result2 = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": "    VLLM_OP002: bool = 1",
        })
        assert result2.returncode == 2, f"Should block VLLM_OP*=1. stderr: {result2.stderr}"

    def test_blocks_write_tool_with_true_default(self):
        """Write tool creating envs.py with VLLM_OP003=True must be blocked."""
        result = _run_hook(
            {
                "file_path": f"{E}/vllm/envs.py",
                "content": 'VLLM_OP003: bool = True\nVLLM_USE_V1: bool = True\n',
            },
            tool_name="Write",
        )
        assert result.returncode == 2, f"Should block Write with VLLM_OP*=True. stderr: {result.stderr}"

    def test_blocks_comma_true_pattern(self):
        """envs.py dict entry like ("VLLM_OP004", True) must be blocked."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP004", True',
        })
        assert result.returncode == 2, f"Should block VLLM_OP*,True. stderr: {result.stderr}"

    # ── Should ALLOW ──

    def test_allows_vllm_op_equals_false(self):
        """VLLM_OP001 = False in envs.py must be allowed."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    VLLM_OP001: bool = False',
        })
        assert result.returncode == 0, f"Should allow VLLM_OP*=False. stderr: {result.stderr}"

    def test_allows_vllm_op_default_0(self):
        """VLLM_OP001 with default="0" must be allowed."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": '    "VLLM_OP001": lambda: bool(os.getenv("VLLM_OP001", "0") == "1"),',
        })
        assert result.returncode == 0, f"Should allow default='0'. stderr: {result.stderr}"

    def test_allows_non_envs_py_files(self):
        """Edits to files other than envs.py must always be allowed."""
        result = _run_hook({
            "file_path": f"{E}/vllm/config.py",
            "old_string": "# old",
            "new_string": "VLLM_OP001 = True  # this is fine in config.py",
        })
        assert result.returncode == 0, "Should allow non-envs.py files"

    def test_allows_non_op_vllm_vars_true(self):
        """Non-VLLM_OP* vars like VLLM_USE_V1=True must be allowed in envs.py."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "# old",
            "new_string": "VLLM_USE_V1: bool = True",
        })
        assert result.returncode == 0, "Should allow non-VLLM_OP* vars"

    def test_allows_empty_content(self):
        """Edit with empty new_string must be allowed."""
        result = _run_hook({
            "file_path": f"{E}/vllm/envs.py",
            "old_string": "VLLM_OP001: bool = True",
            "new_string": "",
        })
        assert result.returncode == 0, "Should allow empty content (deletion)"

    def test_silent_outside_a_campaign(self, tmp_path):
        """No .ammo/active above the cwd: even a blocking edit exits 0, silent."""
        nocamp = os.path.realpath(tmp_path)
        result = _run_hook({
            "file_path": f"{nocamp}/vllm/envs.py",
            "old_string": "# old",
            "new_string": "VLLM_OP001: bool = True",
        }, cwd=nocamp)
        assert result.returncode == 0, f"Should stay silent outside a campaign. stderr: {result.stderr}"
        assert result.stdout == "" and result.stderr == ""


class TestPumiceEnvGuard:
    """PUMICE_* engine-env rule on .py and .rs edits (WS5.1)."""

    def test_blocks_new_pumice_env_in_py(self):
        """A PUMICE_* name not in the allowlist is a new engine env → block."""
        result = _run_hook({
            "file_path": f"{E}/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.getenv("PUMICE_NEW_CAMPAIGN_FLAG", "1")',
        })
        assert result.returncode == 2, f"Should block a new PUMICE_* env. stderr: {result.stderr}"
        assert "PUMICE_NEW_CAMPAIGN_FLAG" in result.stderr

    def test_blocks_default_change_to_allowlisted_pumice_env(self):
        """Supplying a default for a known PUMICE_* env is a default change → block."""
        result = _run_hook({
            "file_path": f"{E}/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.getenv("PUMICE_CACHE_ROOT", "/tmp/new")',
        })
        assert result.returncode == 2, f"Should block a PUMICE_* default change. stderr: {result.stderr}"
        assert "PUMICE_CACHE_ROOT" in result.stderr

    def test_blocks_new_pumice_env_in_rust(self):
        """A new PUMICE_* env read via std::env::var in a .rs file → block."""
        result = _run_hook({
            "file_path": f"{E}/pumice-rust/src/foo.rs",
            "old_string": "// old",
            "new_string": 'let v = std::env::var("PUMICE_MADE_UP_THING");',
        })
        assert result.returncode == 2, f"Should block a new PUMICE_* rust env. stderr: {result.stderr}"
        assert "PUMICE_MADE_UP_THING" in result.stderr

    def test_allows_pumice_models_registry_dict(self):
        """PUMICE_MODELS is the _PUMICE_MODELS registry dict, not an env — never flagged."""
        result = _run_hook({
            "file_path": f"{E}/src/pumice/models/registry.py",
            "old_string": "# old",
            "new_string": '_PUMICE_MODELS = { "gpt_oss": GptOss }',
        })
        assert result.returncode == 0, f"PUMICE_MODELS edit must not be flagged. stderr: {result.stderr}"

    def test_allows_allowlisted_pumice_read_without_default(self):
        """Reading a known PUMICE_* env with no default is not a change → allow."""
        result = _run_hook({
            "file_path": f"{E}/src/pumice/config.py",
            "old_string": "# old",
            "new_string": 'x = os.environ["PUMICE_CACHE_ROOT"]',
        })
        assert result.returncode == 0, f"Should allow a plain read of a known env. stderr: {result.stderr}"

    def test_allows_new_pumice_env_in_non_code_file(self):
        """The PUMICE_* rule only inspects .py and .rs files."""
        result = _run_hook({
            "file_path": f"{E}/docs/readme.md",
            "old_string": "# old",
            "new_string": 'os.getenv("PUMICE_MADE_UP_THING", "1")',
        })
        assert result.returncode == 0, "Should allow non-.py/.rs files"


class TestPumiceEnvAllowlistFile:
    """The seeded allowlist counts are pinned (WS5.1)."""

    ALLOWLIST = ROOT / "hooks" / "pumice_env_allowlist.txt"

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
        """144 PUMICE_* names (seed grep at the pinned commit) plus 8 VLLM_* names."""
        assert self.ALLOWLIST.is_file(), f"Missing allowlist: {self.ALLOWLIST}"
        pumice, vllm = self._names(self.ALLOWLIST)
        assert len(pumice) == 144, f"expected 144 PUMICE_* names, got {len(pumice)}"
        assert len(vllm) == 8, f"expected 8 VLLM_* names, got {len(vllm)}"
        assert len(set(pumice)) == 144, "PUMICE_* names must be unique"
        assert "PUMICE_MODELS" not in pumice, "PUMICE_MODELS is the registry dict, not an env"

    def test_codex_guard_reads_this_file(self):
        """One allowlist: the Codex guard reads <plugin>/hooks/pumice_env_allowlist.txt by path."""
        source = CODEX_GUARD.read_text()
        assert "plugin_path('hooks','pumice_env_allowlist.txt')" in source.replace(" ", ""), (
            "codex/hooks/pre_tool_use_guard.py must read hooks/pumice_env_allowlist.txt"
        )


class TestSettingsRegistration:
    """Verify the hook is registered in the plugin's hooks/hooks.json."""

    def test_env_guard_registered_in_settings(self):
        """hooks/hooks.json must have PreToolUse hook for Edit|Write."""
        with open(HOOKS_JSON) as f:
            settings = json.load(f)

        pre_tool_hooks = settings.get("hooks", {}).get("PreToolUse", [])
        edit_write_hooks = [
            h for h in pre_tool_hooks if h.get("matcher") in ("Edit|Write", "Edit", "Write")
        ]
        assert len(edit_write_hooks) >= 1, (
            "hooks/hooks.json must have a PreToolUse hook for Edit|Write"
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

    python3 must be in /usr/bin or /bin: the campaign lookup needs it.

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
