"""Host-local preflight: the checks behind the graph's preflight_passed question.

workflow.pave.yaml asks whether preflight_pumice.py exits 0 against the engine
root and model path in target.json, with the runtime profile pumice-cuda, the
engine commit and pins matching scripts/pumice_pins.py, pumice and pumice_rust
importable from the recorded venv, and at least one GPU visible. One test
class per fact, plus the scaffold fingerprint (conventions §4).
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_PREFLIGHT = _SCRIPTS_DIR / "preflight_pumice.py"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
_spec = importlib.util.spec_from_file_location("preflight_pumice_host", str(_PREFLIGHT))
preflight = importlib.util.module_from_spec(_spec)
sys.modules["preflight_pumice_host"] = preflight
_spec.loader.exec_module(preflight)

#: Every name campaign_paths.campaign_env() can derive (references/campaign-env.md §1).
_CAMPAIGN_ENV_NAMES = (
    "AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR",
    "AMMO_PYTHON", "CARGO_TARGET_DIR", "AMMO_GPU_RES_DIR", "AMMO_RUNTIME_PROFILE",
    "AMMO_ACCELERATOR_KIND", "AMMO_TP_SIZE", "AMMO_DP_SIZE", "AMMO_STACK_FINGERPRINT",
    "AMMO_INSTANCE_FAMILY", "AMMO_ACCELERATOR_NAME", "AMMO_SERVING_SETUP",
    "AMMO_SERVING_MANIFEST", "AMMO_SERVING_MANIFEST_SHA256",
)

L40S = {"index": "0", "name": "NVIDIA L40S", "compute_cap": "8.9", "driver_version": "580.95.05"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Start without campaign names; restore all of os.environ afterwards.

    apply_env() and main() write os.environ directly, and monkeypatch.delenv
    does not record a name that was absent, so snapshot the whole mapping.
    """
    saved = dict(os.environ)
    for name in _CAMPAIGN_ENV_NAMES + ("CUDA_VISIBLE_DEVICES", "PUMICE_USE_VLLM_RUNNER"):
        os.environ.pop(name, None)
    monkeypatch.chdir(tmp_path)
    yield
    os.environ.clear()
    os.environ.update(saved)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _engine(tmp_path: Path, commits: int = 1) -> tuple[Path, list[str]]:
    """A Pumice-shaped git checkout with *commits* commits; returns (root, shas)."""
    root = tmp_path / "pumice"
    root.mkdir()
    (root / "rust_grpc_server.py").write_text("", encoding="utf-8")
    _git(root, "init", "-q")
    shas = []
    for i in range(commits):
        (root / f"f{i}").write_text(str(i), encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"c{i}")
        shas.append(_git(root, "rev-parse", "HEAD"))
    return root, shas


def _campaign(engine: Path, target: dict, cid: str = "20261028-000000-abcdef") -> Path:
    campaign = engine / ".ammo" / cid
    campaign.mkdir(parents=True)
    (engine / ".ammo" / "active").write_text(cid + "\n", encoding="utf-8")
    (campaign / "target.json").write_text(json.dumps(target), encoding="utf-8")
    return campaign


# --------------------------------------------------------------------------- pins module


class TestSiblingPinsModule:
    def test_pins_come_from_the_sibling_module(self):
        assert Path(preflight.pumice_pins.__file__).resolve() == _SCRIPTS_DIR / "pumice_pins.py"

    def test_the_pins_file_search_is_gone(self):
        assert not hasattr(preflight, "PINS_FILE_FALLBACKS")
        assert not hasattr(preflight, "_installed_pins_file")

    def test_check_pins_passes_the_runner_kind(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            preflight.pumice_pins, "check_pins",
            lambda model_runner=None: seen.append(model_runner) or (True, ""),
        )
        _, ok, detail = preflight._check_pins("pumice", "confdb")
        assert ok
        assert seen == ["pumice"]
        assert "pumice" in detail

    def test_unknown_runner_kind_checks_vllm_too(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            preflight.pumice_pins, "check_pins",
            lambda model_runner=None: seen.append(model_runner) or (True, ""),
        )
        _, ok, detail = preflight._check_pins(None, "no model path")
        assert ok
        assert seen == ["vllm"]
        assert "no model path" in detail

    def test_pins_failure_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            preflight.pumice_pins, "check_pins",
            lambda model_runner=None: (False, "vllm 0.1 does not match"),
        )
        _, ok, detail = preflight._check_pins("vllm", "confdb")
        assert not ok
        assert "vllm 0.1 does not match" in detail


# --------------------------------------------------------------------------- profile gate


class TestRuntimeProfile:
    def test_default_profile_passes(self):
        _, ok, detail = preflight._check_runtime_profile()
        assert ok, detail
        assert "pumice-cuda" in detail

    def test_other_profile_fails_naming_it(self, monkeypatch):
        monkeypatch.setenv("AMMO_RUNTIME_PROFILE", "pumice-neuron")
        _, ok, detail = preflight._check_runtime_profile()
        assert not ok
        assert "pumice-neuron" in detail

    def test_the_gate_is_runtime_ops(self, monkeypatch):
        def refuse(profile=None, env=None):
            raise ValueError("refused by the gate")

        monkeypatch.setattr(preflight.runtime_ops, "require_supported_profile", refuse)
        _, ok, detail = preflight._check_runtime_profile()
        assert not ok
        assert detail == "refused by the gate"


# --------------------------------------------------------------------------- engine root and commit


class TestEngineCommit:
    def test_head_at_the_pin_passes(self, tmp_path, monkeypatch):
        root, shas = _engine(tmp_path)
        monkeypatch.setattr(preflight.pumice_pins, "PUMICE_COMMIT", shas[0])
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_engine_commit()
        assert ok, detail
        assert "pinned commit" in detail

    def test_head_on_top_of_the_pin_passes(self, tmp_path, monkeypatch):
        root, shas = _engine(tmp_path, commits=2)
        monkeypatch.setattr(preflight.pumice_pins, "PUMICE_COMMIT", shas[0])
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_engine_commit()
        assert ok, detail
        assert "descends" in detail

    def test_head_behind_the_pin_fails(self, tmp_path, monkeypatch):
        root, shas = _engine(tmp_path, commits=2)
        _git(root, "checkout", "-q", "--detach", shas[0])
        monkeypatch.setattr(preflight.pumice_pins, "PUMICE_COMMIT", shas[1])
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_engine_commit()
        assert not ok
        assert shas[1] in detail
        assert shas[0][:12] in detail

    def test_pin_missing_from_the_checkout_fails(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        monkeypatch.setattr(preflight.pumice_pins, "PUMICE_COMMIT", "f" * 40)
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_engine_commit()
        assert not ok
        assert "f" * 40 in detail
        assert "fetch" in detail

    def test_no_checkout_fails_naming_the_search(self, tmp_path):
        _, ok, detail = preflight._check_engine_commit()
        assert not ok
        assert "rust_grpc_server.py" in detail
        assert "--engine-root" in detail

    def test_not_a_git_checkout_fails(self, tmp_path, monkeypatch):
        root = tmp_path / "plain"
        root.mkdir()
        (root / "rust_grpc_server.py").write_text("", encoding="utf-8")
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_engine_commit()
        assert not ok
        assert "git" in detail

    def test_engine_root_env_wins_over_the_cwd_walk(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "rust_grpc_server.py").write_text("", encoding="utf-8")
        monkeypatch.chdir(other)
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        assert preflight._find_repo_root() == root

    def test_explicit_root_wins_over_env(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(tmp_path / "nowhere"))
        assert preflight._find_repo_root(root) == root


# --------------------------------------------------------------------------- venv and imports


class TestEngineVenv:
    def _venv(self, base: Path) -> Path:
        python = base / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("", encoding="utf-8")
        return python

    def test_no_recorded_venv_passes(self):
        _, ok, detail = preflight._check_engine_venv()
        assert ok, detail

    def test_recorded_venv_is_this_interpreter(self, tmp_path, monkeypatch):
        python = self._venv(tmp_path / "venv")
        monkeypatch.setenv("AMMO_PYTHON", str(python))
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
        _, ok, detail = preflight._check_engine_venv()
        assert ok, detail

    def test_other_interpreter_fails_naming_the_recorded_python(self, tmp_path, monkeypatch):
        python = self._venv(tmp_path / "venv")
        monkeypatch.setenv("AMMO_PYTHON", str(python))
        monkeypatch.setattr(sys, "prefix", str(tmp_path / "elsewhere"))
        _, ok, detail = preflight._check_engine_venv()
        assert not ok
        assert str(python) in detail

    def test_missing_recorded_python_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AMMO_PYTHON", str(tmp_path / "gone" / "bin" / "python"))
        _, ok, detail = preflight._check_engine_venv()
        assert not ok
        assert "does not exist" in detail

    def test_track_worktree_venv_passes(self, tmp_path, monkeypatch):
        """A track runs preflight in its worktree with the worktree .venv."""
        python = self._venv(tmp_path / "venv")
        worktree, _ = _engine(tmp_path)
        self._venv(worktree / ".venv")
        monkeypatch.setenv("AMMO_PYTHON", str(python))
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(worktree))
        monkeypatch.setattr(sys, "prefix", str(worktree / ".venv"))
        _, ok, detail = preflight._check_engine_venv()
        assert ok, detail


class TestPumiceRustImport:
    def test_importable_passes(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pumice_rust", types.ModuleType("pumice_rust"))
        _, ok, detail = preflight._check_pumice_rust_import()
        assert ok, detail

    def test_missing_fails_naming_the_build(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pumice_rust", None)
        _, ok, detail = preflight._check_pumice_rust_import()
        assert not ok
        assert "pumice_rust" in detail
        assert "cargo" in detail


# --------------------------------------------------------------------------- GPU


class TestGpuVisible:
    def test_one_gpu_passes(self, monkeypatch):
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [L40S])
        _, ok, detail = preflight._check_gpu()
        assert ok, detail
        assert "NVIDIA L40S" in detail

    def test_no_nvidia_smi_fails(self, monkeypatch):
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: None)
        _, ok, detail = preflight._check_gpu()
        assert not ok
        assert "nvidia-smi" in detail

    def test_empty_list_fails(self, monkeypatch):
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [])
        _, ok, _ = preflight._check_gpu()
        assert not ok

    @pytest.mark.parametrize("value", ["", "-1"])
    def test_cuda_visible_devices_hiding_every_gpu_fails(self, monkeypatch, value):
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [L40S])
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", value)
        _, ok, detail = preflight._check_gpu()
        assert not ok
        assert "CUDA_VISIBLE_DEVICES" in detail

    def test_parses_nvidia_smi_csv(self, monkeypatch):
        text = "0, NVIDIA L40S, 8.9, 580.95.05\n1, NVIDIA L40S, 8.9, 580.95.05\n"
        monkeypatch.setattr(preflight, "_run_text", lambda argv, cwd=None: text)
        rows = preflight._nvidia_smi_rows()
        assert rows == [L40S, dict(L40S, index="1")]

    def test_compute_capability_follows_cuda_visible_devices(self, monkeypatch):
        rows = [dict(L40S), dict(L40S, index="3", compute_cap="9.0")]
        assert preflight._compute_capability(rows) == (8, 9)
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3")
        assert preflight._compute_capability(rows) == (9, 0)


# --------------------------------------------------------------------------- runner kind


class TestModelRunnerKind:
    def _model(self, tmp_path: Path, arch: str = "GptOssForCausalLM") -> Path:
        model = tmp_path / "model"
        model.mkdir()
        (model / "config.json").write_text(json.dumps({"architectures": [arch]}), encoding="utf-8")
        return model

    def _confdb(self, monkeypatch, settings):
        seen = []
        confdb = types.ModuleType("pumice.models.confdb")

        def load_model_config(architectures, compute_capability, hf_config=None, quantization=None):
            seen.append((list(architectures), tuple(compute_capability)))
            return dict(settings)

        confdb.load_model_config = load_model_config
        monkeypatch.setitem(sys.modules, "pumice", types.ModuleType("pumice"))
        monkeypatch.setitem(sys.modules, "pumice.models", types.ModuleType("pumice.models"))
        monkeypatch.setitem(sys.modules, "pumice.models.confdb", confdb)
        return seen

    def test_explicit_flag_wins(self, tmp_path, monkeypatch):
        self._confdb(monkeypatch, {"model_runner": "vllm"})
        kind, how = preflight._resolve_model_runner(
            self._model(tmp_path), ["--model-runner", "pumice"], {}, [L40S]
        )
        assert kind == "pumice"
        assert "--model-runner" in how

    def test_confdb_row_decides(self, tmp_path, monkeypatch):
        seen = self._confdb(monkeypatch, {"model_runner": "vllm"})
        monkeypatch.setenv("PUMICE_USE_VLLM_RUNNER", "0")
        kind, how = preflight._resolve_model_runner(self._model(tmp_path), [], {}, [L40S])
        assert kind == "vllm"
        assert "confdb" in how
        assert seen == [(["GptOssForCausalLM"], (8, 9))]

    def test_env_applies_without_a_confdb_row(self, tmp_path, monkeypatch):
        self._confdb(monkeypatch, {})
        kind, how = preflight._resolve_model_runner(
            self._model(tmp_path), [], {"PUMICE_USE_VLLM_RUNNER": "1"}, [L40S]
        )
        assert kind == "vllm"
        assert "PUMICE_USE_VLLM_RUNNER" in how

    def test_default_is_pumice(self, tmp_path, monkeypatch):
        self._confdb(monkeypatch, {})
        kind, _ = preflight._resolve_model_runner(self._model(tmp_path), [], {}, [L40S])
        assert kind == "pumice"

    def test_unknown_without_model_dir(self):
        kind, how = preflight._resolve_model_runner(None, [], {}, [L40S])
        assert kind is None
        assert "model" in how

    def test_unknown_without_gpu(self, tmp_path, monkeypatch):
        self._confdb(monkeypatch, {"model_runner": "vllm"})
        kind, how = preflight._resolve_model_runner(self._model(tmp_path), [], {}, None)
        assert kind is None
        assert "compute capability" in how

    def test_unknown_when_confdb_cannot_load(self, tmp_path, monkeypatch):
        monkeypatch.setitem(sys.modules, "pumice.models.confdb", None)
        kind, how = preflight._resolve_model_runner(self._model(tmp_path), [], {}, [L40S])
        assert kind is None
        assert "confdb" in how


# --------------------------------------------------------------------------- cargo config


class TestCmbApiPatchHostLocal:
    PATCH = '[patch.crates-io]\namzn-mantle-cmb-api = { path = "/src/cmb-api" }\n'

    def test_default_cargo_home_is_home_dot_cargo(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CARGO_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".cargo").mkdir()
        (tmp_path / ".cargo" / "config.toml").write_text(self.PATCH, encoding="utf-8")
        _, ok, detail = preflight._check_cmb_api_patch()
        assert ok, detail

    def test_engine_cargo_config_counts(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        (root / ".cargo").mkdir()
        (root / ".cargo" / "config.toml").write_text(self.PATCH, encoding="utf-8")
        monkeypatch.setenv("CARGO_HOME", str(tmp_path / "empty-cargo-home"))
        monkeypatch.setenv("AMMO_ENGINE_ROOT", str(root))
        _, ok, detail = preflight._check_cmb_api_patch()
        assert ok, detail

    def test_nothing_found_names_each_place(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CARGO_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        _, ok, detail = preflight._check_cmb_api_patch()
        assert not ok
        assert str(tmp_path / ".cargo" / "config.toml") in detail
        assert "CARGO_HOME" in detail


# --------------------------------------------------------------------------- fingerprint


class TestStackFingerprint:
    def test_write_fingerprint_records_the_engine_and_runs_no_check(self, tmp_path, monkeypatch):
        root, shas = _engine(tmp_path)
        out = tmp_path / "fp.json"
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [L40S])
        fake_lib = tmp_path / "lib"
        fake_lib.mkdir()
        (fake_lib / "pumice_rust.py").write_text("BUILD = 1\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(fake_lib))
        monkeypatch.delitem(sys.modules, "pumice_rust", raising=False)
        monkeypatch.setattr(preflight, "_check_runtime_profile", lambda: pytest.fail("ran a check"))

        rc = preflight.main(["--engine-root", str(root), "--write-fingerprint", str(out)])

        assert rc == 0
        fp = json.loads(out.read_text(encoding="utf-8"))
        assert set(fp) == set(preflight.FINGERPRINT_KEYS)
        assert fp["pumice_commit"] == shas[0]
        assert fp["image_digest"] is None
        assert fp["gpu_name"] == "NVIDIA L40S"
        assert fp["nvidia_driver"] == "580.95.05"
        assert fp["pumice_rust_build_id"].startswith("sha256:")
        assert len(fp["pumice_rust_build_id"]) == len("sha256:") + 64

    def test_unknown_values_are_null(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: None)
        monkeypatch.setitem(sys.modules, "pumice_rust", None)
        rc = preflight.main(["--engine-root", str(tmp_path / "none"), "--write-fingerprint", "-"])
        assert rc == 0
        fp = json.loads(capsys.readouterr().out)
        assert fp["pumice_commit"] is None
        assert fp["pumice_rust_build_id"] is None
        assert fp["gpu_name"] is None
        assert fp["nvidia_driver"] is None
        assert fp["image_digest"] is None

    def test_fingerprint_feeds_the_runtime_fingerprint(self, tmp_path, monkeypatch):
        """campaign_paths turns target.json runtime.stack_fingerprint into
        AMMO_STACK_FINGERPRINT; runtime_ops embeds it as server_stack_fingerprint."""
        import campaign_paths
        import runtime_ops

        root, shas = _engine(tmp_path)
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [L40S])
        fp = preflight.stack_fingerprint(root)
        campaign = _campaign(root, {"runtime": {"profile": "pumice-cuda", "stack_fingerprint": fp}})
        env = campaign_paths.campaign_env(campaign)
        monkeypatch.setattr(runtime_ops, "_git_fingerprint", lambda path: None)
        payload = runtime_ops.resolve_runtime(env).stack_fingerprint(env)
        assert payload["server_stack_fingerprint"]["pumice_commit"] == shas[0]


# --------------------------------------------------------------------------- main


class TestMainInsideCampaign:
    def _all_ok(self, monkeypatch, record):
        def ok(label):
            return lambda *a, **k: (label, True, "ok")

        for name in ("_check_runtime_profile", "_check_engine_commit", "_check_engine_venv",
                     "_check_pumice_imports", "_check_pumice_rust_import", "_check_proto",
                     "_check_server_flags", "_check_cmb_api_patch", "_check_gpu"):
            monkeypatch.setattr(preflight, name, ok(name))
        monkeypatch.setattr(preflight, "_nvidia_smi_rows", lambda: [L40S])

        def model_path(path):
            record["model"] = path
            return ("model path", True, str(path))

        def pins(kind=None, how=""):
            record["runner"] = (kind, how)
            return ("pumice pins", True, "ok")

        monkeypatch.setattr(preflight, "_check_model_path", model_path)
        monkeypatch.setattr(preflight, "_check_pins", pins)

    def test_engine_root_and_model_path_come_from_target_json(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        model = tmp_path / "gpt-oss"
        model.mkdir()
        _campaign(root, {
            "engine": {"root": str(root)},
            "target": {"model_id": str(model)},
            "bench": {"extra_args": ["--model-runner", "vllm"]},
        })
        monkeypatch.chdir(root)
        record = {}
        self._all_ok(monkeypatch, record)

        assert preflight.main([]) == 0

        assert os.environ["AMMO_ENGINE_ROOT"] == str(root)
        assert record["model"] == str(model)
        assert record["runner"][0] == "vllm"

    def test_flags_win_over_target_json(self, tmp_path, monkeypatch):
        root, _ = _engine(tmp_path)
        _campaign(root, {"target": {"model_id": "/elsewhere"}})
        other = tmp_path / "other-root"
        model = tmp_path / "flag-model"
        monkeypatch.chdir(root)
        record = {}
        self._all_ok(monkeypatch, record)

        preflight.main(["--engine-root", str(other), "--model-path", str(model)])

        assert os.environ["AMMO_ENGINE_ROOT"] == str(other.resolve())
        assert os.environ["AMMO_RUNTIME_SOURCE_DIR"] == str(other.resolve())
        assert record["model"] == str(model)

    def test_any_failure_exits_one(self, tmp_path, monkeypatch, capsys):
        record = {}
        self._all_ok(monkeypatch, record)
        monkeypatch.setattr(preflight, "_check_gpu", lambda: ("GPU", False, "no GPU is visible"))
        assert preflight.main([]) == 1
        assert "FAIL GPU: no GPU is visible" in capsys.readouterr().out

    def test_apply_env_is_the_first_call(self):
        source = _PREFLIGHT.read_text(encoding="utf-8")
        body = source.split("def main(", 1)[1].split("\n", 1)[1]
        first = next(line.strip() for line in body.splitlines() if line.strip())
        assert first == "campaign = campaign_paths.apply_env()"
