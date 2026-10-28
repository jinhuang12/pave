"""scripts/pumice-stage-native.sh: rebuild and stage the engine from a host-local worktree.

A fresh track worktree has no compiled ``_ops`` kernels (git ignores ``*.so``),
so the script copies them from the engine checkout. It finds that checkout
from AMMO_ENGINE_ROOT, else from the worktree's git common dir. These tests
use a real temp git repo with a linked worktree and fake ``uv``, ``cargo`` and
venv python, so no Rust toolchain or GPU is needed.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_STAGE = _SCRIPTS_DIR / "pumice-stage-native.sh"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import campaign_paths  # noqa: E402
import run_pumice_grpc_bench as bench  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="bash and git are required",
)

_EXT_SUFFIX = ".cpython-311-x86_64-linux-gnu.so"


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    )


def _engine_with_track(tmp_path: Path) -> tuple[Path, Path]:
    """An engine checkout with a compiled _ops kernel and a linked track worktree."""
    engine = tmp_path / "engine"
    (engine / "pumice-rust").mkdir(parents=True)
    (engine / "pumice-rust" / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
    (engine / ".gitignore").write_text("*.so\n", encoding="utf-8")
    _git(engine, "init", "-q")
    _git(engine, "add", ".")
    _git(engine, "commit", "-q", "-m", "seed")
    ops = engine / "src" / "pumice" / "_ops"
    ops.mkdir(parents=True)
    (ops / "engine_kernel.so").write_bytes(b"engine")
    track = tmp_path / "tracks" / "track-a"
    _git(engine, "worktree", "add", "-q", str(track))
    return engine, track


def _fakes(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (venv python, fake bin dir, record dir)."""
    record = tmp_path / "record"
    record.mkdir()
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.11" / "site-packages"
    python = _executable(
        venv / "bin" / "python",
        "#!/bin/sh\n"
        'case "$2" in\n'
        f'  *EXT_SUFFIX*) echo "{_EXT_SUFFIX}" ;;\n'
        f'  *purelib*) echo "{site}" ;;\n'
        '  *) echo "import check ok" ;;\n'
        "esac\n",
    )
    fake_bin = tmp_path / "bin"
    _executable(fake_bin / "uv", f'#!/bin/sh\necho "$@" > {record}/uv.argv\n')
    _executable(
        fake_bin / "cargo",
        "#!/bin/sh\n"
        f'echo "$@" > {record}/cargo.argv\n'
        f'printf "%s" "${{PYO3_PYTHON:-}}" > {record}/cargo.pyo3\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--manifest-path" ]; then manifest="$2"; fi\n'
        "  shift\n"
        "done\n"
        'src="$(dirname "$(dirname "$manifest")")"\n'
        'mkdir -p "$src/target/release"\n'
        'printf built > "$src/target/release/libpumice_rust.so"\n',
    )
    return python, fake_bin, record


def _env(fake_bin: Path, **extra: str) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("AMMO_ENGINE_ROOT", "CARGO_TARGET_DIR", "PYO3_PYTHON", "ENGINE_BASE_REPO")
    }
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env.update(extra)
    return env


def _stage(python: Path, src: Path, env: dict, *flags: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(_STAGE), str(python), str(src), *flags],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_linked_worktree_finds_the_engine_ops_without_env(tmp_path):
    engine, track = _engine_with_track(tmp_path)
    python, fake_bin, record = _fakes(tmp_path)

    result = _stage(python, track, _env(fake_bin), "--features", "nixl-stub cmb")

    assert result.returncode == 0, result.stderr
    assert (track / "src" / "pumice" / "_ops" / "engine_kernel.so").read_bytes() == b"engine"
    assert str(engine.resolve() / "src" / "pumice" / "_ops") in result.stdout
    staged = tmp_path / "venv" / "lib" / "python3.11" / "site-packages" / f"pumice_rust{_EXT_SUFFIX}"
    assert staged.read_bytes() == b"built"
    assert (record / "cargo.argv").read_text().split() == [
        "build", "--release", "--manifest-path", str(track / "pumice-rust" / "Cargo.toml"),
        "--features", "nixl-stub", "cmb",
    ]
    assert (record / "uv.argv").read_text().split() == [
        "pip", "install", "--python", str(python), "-e", str(track), "--no-deps",
    ]


def test_engine_root_env_wins_over_git(tmp_path):
    engine, track = _engine_with_track(tmp_path)
    other = tmp_path / "other-engine"
    (other / "src" / "pumice" / "_ops").mkdir(parents=True)
    (other / "src" / "pumice" / "_ops" / "other_kernel.so").write_bytes(b"other")
    python, fake_bin, _ = _fakes(tmp_path)

    result = _stage(python, track, _env(fake_bin, AMMO_ENGINE_ROOT=str(other)))

    assert result.returncode == 0, result.stderr
    ops = track / "src" / "pumice" / "_ops"
    assert (ops / "other_kernel.so").is_file()
    assert not (ops / "engine_kernel.so").exists()


def test_the_build_uses_the_venv_python_for_pyo3(tmp_path):
    _, track = _engine_with_track(tmp_path)
    python, fake_bin, record = _fakes(tmp_path)

    result = _stage(python, track, _env(fake_bin))

    assert result.returncode == 0, result.stderr
    assert (record / "cargo.pyo3").read_text() == str(python)


def test_no_engine_root_fails_and_names_the_env_name(tmp_path):
    src = tmp_path / "plain"
    (src / "pumice-rust").mkdir(parents=True)
    python, fake_bin, _ = _fakes(tmp_path)

    result = _stage(python, src, _env(fake_bin, GIT_CEILING_DIRECTORIES=str(tmp_path)))

    assert result.returncode != 0
    assert "AMMO_ENGINE_ROOT" in result.stdout + result.stderr


def test_script_names_no_server_paths():
    text = _STAGE.read_text(encoding="utf-8")
    assert "/usr/local/bin" not in text
    assert "/workspace" not in text
    assert "ENGINE_BASE_REPO" not in text


def test_rebuild_hint_names_the_plugin_launcher(tmp_path):
    hint = bench._rebuild_hint(tmp_path)
    launcher = campaign_paths.plugin_path("scripts", "ammo")
    assert f"{launcher} pumice-stage-native.sh {sys.executable} {tmp_path}" in hint
    assert "/usr/local/bin" not in hint
