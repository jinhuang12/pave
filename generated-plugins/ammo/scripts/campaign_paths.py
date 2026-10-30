#!/usr/bin/env python3
"""The one place that knows where AMMO things are (conventions §2-§4).

A campaign lives at <engine>/.ammo/<campaign-id>/. The marker <engine>/.ammo/active
holds the campaign id. AMMO_ARTIFACT_DIR, when set, names the campaign dir directly.
references/campaign-env.md lists every environment name this module derives.

CLI (stdlib only, so any python3 can run it):
    campaign_paths.py env [--start DIR] [--hook]   print `export NAME=value` lines
    campaign_paths.py find [--start DIR]           print the campaign dir
    campaign_paths.py python [--start DIR]         print the python to use
    campaign_paths.py in-campaign PATH             exit 0 when PATH is inside the campaign
`--hook` reads the hook input JSON on stdin, starts from its `cwd`, then
CLAUDE_PROJECT_DIR, then the current dir, and never writes stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional

MARKER = Path(".ammo") / "active"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DEFAULT_GPU_RES_DIR = "/tmp/ammo_gpu_res"
APPLIED_ENV = "AMMO_APPLIED_ENV"
_RESTORABLE = re.compile(r"AMMO_[A-Z0-9_]+|CARGO_TARGET_DIR")

#: Third-party modules the plugin's own scripts import. scripts/ammo runs every
#: script with the engine python, so the engine venv must carry them. The engine
#: owns pumice, vllm, torch and the rest; preflight_pumice.py checks both sets.
PLUGIN_PYTHON_DEPS = ("httpx", "jsonschema", "psutil", "pydantic", "yaml")
#: The pip name where it differs from the import name.
_PIP_NAMES = {"yaml": "pyyaml"}


def plugin_pip_names(names=None) -> list:
    """pip names for the import names (default: PLUGIN_PYTHON_DEPS), in order."""
    return [_PIP_NAMES.get(name, name) for name in (PLUGIN_PYTHON_DEPS if names is None else names)]


def plugin_deps_hint(python, names=None) -> str:
    """The install command for the plugin packages, in the pip form and the uv form."""
    packages = " ".join(plugin_pip_names(names))
    return (f"`{python} -m pip install {packages}` "
            f"(a uv venv without pip: `uv pip install --python {python} {packages}`)")


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(data: Mapping, key: str) -> Mapping:
    value = data.get(key)
    return value if isinstance(value, Mapping) else {}


def find_campaign_dir(start: Optional[Path] = None, env: Optional[Mapping] = None) -> Optional[Path]:
    """AMMO_ARTIFACT_DIR, else walk up from start (default cwd) to the first .ammo/active."""
    env = os.environ if env is None else env
    explicit = (env.get("AMMO_ARTIFACT_DIR") or "").strip()
    if explicit:
        return Path(os.path.abspath(os.path.expanduser(explicit)))
    try:
        here = Path(start if start is not None else os.getcwd()).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    for directory in (here, *here.parents):
        marker = directory / MARKER
        if not marker.is_file():
            continue
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        cid = lines[0].strip() if lines else ""
        if not _ID.fullmatch(cid) or cid in (".", ".."):
            return None
        campaign = directory / ".ammo" / cid
        return campaign if campaign.is_dir() else None
    return None


def campaign_id(campaign_dir) -> str:
    return Path(campaign_dir).name


def engine_root(campaign_dir) -> Path:
    return Path(campaign_dir).parent.parent


def plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent


def plugin_path(*parts: str) -> Path:
    return plugin_root().joinpath(*parts)


def _engine(campaign_dir, target: Mapping) -> Path:
    root = _section(target, "engine").get("root")
    return Path(root).expanduser() if isinstance(root, str) and root else engine_root(campaign_dir)


def _venv_python(venv: str, engine: Path) -> Path:
    path = Path(venv).expanduser()
    path = path if path.is_absolute() else engine / path
    return path if path.is_file() else path / "bin" / "python"


def _is_exe(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _git_dirs(directory: str) -> Optional[tuple]:
    """(toplevel, common dir) of the git worktree at directory, both realpaths, else None."""
    try:
        out = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--show-toplevel", "--git-common-dir"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = out.stdout.splitlines()
    if out.returncode != 0 or len(lines) != 2:
        return None
    return os.path.realpath(lines[0]), os.path.realpath(os.path.join(directory, lines[1]))


def _track_worktree(engine: Path) -> Optional[Path]:
    """The cwd's git toplevel when it is another worktree of the engine's repository, else None.

    Same repository means the same git common dir. That holds when the engine is a plain
    clone and when the engine is itself a linked worktree of some other checkout.
    """
    here = _git_dirs(os.getcwd())
    if here is None or here[0] == os.path.realpath(engine):
        return None
    there = _git_dirs(str(engine))
    if there is None or there[1] != here[1]:
        return None
    return Path(here[0])


def python_exe(campaign_dir=None) -> Path:
    """Track worktree .venv, else target.json engine.venv, else AMMO_PYTHON,
    else <engine>/.venv/bin/python, else python3.

    The first candidate applies when the cwd is inside another git worktree of the
    engine's repository (a track worktree): the bench runner starts the server with sys.executable,
    so a track must run its own build, not the engine's.
    """
    candidates = []
    target = _load_json(Path(campaign_dir) / "target.json") if campaign_dir else {}
    engine = _engine(campaign_dir, target) if campaign_dir else None
    track = _track_worktree(engine) if engine is not None else None
    if track is not None:
        candidates.append(track / ".venv" / "bin" / "python")
    venv = _section(target, "engine").get("venv")
    if engine is not None and isinstance(venv, str) and venv:
        candidates.append(_venv_python(venv, engine))
    if os.environ.get("AMMO_PYTHON"):
        candidates.append(Path(os.environ["AMMO_PYTHON"]).expanduser())
    if engine is not None:
        candidates.append(engine / ".venv" / "bin" / "python")
    for candidate in candidates:
        if _is_exe(candidate):
            return candidate
    return Path(shutil.which("python3") or sys.executable)


def in_campaign(path, campaign_dir) -> bool:
    """Realpath containment: the one path classifier."""
    if not campaign_dir:
        return False
    try:
        inner = os.path.realpath(os.path.expanduser(str(path)))
        outer = os.path.realpath(os.path.expanduser(str(campaign_dir)))
        return os.path.commonpath([inner, outer]) == outer
    except (OSError, ValueError):
        return False


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and str(number) == str(value).strip() else None


def campaign_env(campaign_dir) -> dict:
    """Every section 1 name of references/campaign-env.md, derived from target.json."""
    campaign = Path(campaign_dir)
    target = _load_json(campaign / "target.json")
    engine = _engine(campaign, target)
    engine_section = _section(target, "engine")
    runtime = _section(target, "runtime")
    venv = engine_section.get("venv")
    python = _venv_python(venv, engine) if isinstance(venv, str) and venv else engine / ".venv" / "bin" / "python"
    env = {
        "AMMO_ARTIFACT_DIR": str(campaign),
        "AMMO_SESSION_ID": campaign_id(campaign),
        "AMMO_ENGINE_ROOT": str(engine),
        "AMMO_RUNTIME_SOURCE_DIR": str(engine),
        "AMMO_PYTHON": str(python),
        "CARGO_TARGET_DIR": str(engine / "target"),
        "AMMO_GPU_RES_DIR": DEFAULT_GPU_RES_DIR,
        "AMMO_RUNTIME_PROFILE": str(runtime.get("profile") or "pumice-cuda"),
        "AMMO_ACCELERATOR_KIND": str(runtime.get("accelerator_kind") or "cuda"),
    }
    tp = _positive_int(_section(target, "target").get("tp"))
    if tp:
        dp = _section(target, "target").get("dp")
        if dp is None:
            dp = _section(_load_json(campaign / "state.json"), "target").get("dp")
        env["AMMO_TP_SIZE"] = str(tp)
        env["AMMO_DP_SIZE"] = str(_positive_int(dp) or 1)
    fingerprint = runtime.get("stack_fingerprint")
    if isinstance(fingerprint, Mapping):
        env["AMMO_STACK_FINGERPRINT"] = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    name = runtime.get("accelerator_name")
    family = runtime.get("instance_family") or name
    if family:
        env["AMMO_INSTANCE_FAMILY"] = str(family)
    if name:
        env["AMMO_ACCELERATOR_NAME"] = str(name)
    manifest = _section(target, "serving_manifest")
    if manifest.get("path"):
        env["AMMO_SERVING_SETUP"] = "pd"
        env["AMMO_SERVING_MANIFEST"] = str(manifest["path"])
        env["AMMO_SERVING_MANIFEST_SHA256"] = str(manifest.get("sha256") or "")
    return env


def _fill(env: MutableMapping, campaign: Path) -> dict:
    """Set each derived name that is unset or empty. Return name -> prior value (None when unset)."""
    prior = {}
    for key, value in campaign_env(campaign).items():
        if not env.get(key):
            prior[key] = env.get(key)
            env[key] = value
    return prior


def _applied(env: Mapping) -> dict:
    try:
        record = json.loads(env.get(APPLIED_ENV) or "{}")
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def apply_env(env: MutableMapping = os.environ) -> Optional[Path]:
    """Find the campaign, then set each derived name that is unset or empty. Explicit env wins.

    Each name it sets goes into AMMO_APPLIED_ENV with its prior value, so unapply_env() can
    undo it. A name already in that record keeps its first prior value.
    """
    campaign = find_campaign_dir(env=env)
    if campaign is not None:
        prior = _fill(env, campaign)
        if prior:
            record = _applied(env)
            for key, value in prior.items():
                record.setdefault(key, value)
            env[APPLIED_ENV] = json.dumps(record, sort_keys=True)
    return campaign


def unapply_env(env: MutableMapping = os.environ) -> None:
    """Undo apply_env(): give each name in AMMO_APPLIED_ENV its prior value back, then drop the record.

    A name that was unset is removed. Without a usable record nothing else changes. Only
    names that apply_env() can set (AMMO_*, CARGO_TARGET_DIR) are touched.
    """
    record = _applied(env)
    env.pop(APPLIED_ENV, None)
    for key, value in record.items():
        if key == APPLIED_ENV or not _RESTORABLE.fullmatch(key):
            continue
        if value is None:
            env.pop(key, None)
        elif isinstance(value, str):
            env[key] = value


# --------------------------------------------------------------------------- CLI


def _hook_starts(stdin_text: str) -> list:
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except ValueError:
        payload = {}
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return [s for s in (cwd, os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()) if isinstance(s, str) and s]


def _find_cli(args) -> Optional[Path]:
    starts = _hook_starts(sys.stdin.read()) if args.hook else [args.start or os.getcwd()]
    for start in starts:
        found = find_campaign_dir(Path(start))
        if found is not None:
            return found
    if not args.hook:
        print(
            f"campaign_paths: no campaign. AMMO_ARTIFACT_DIR is not set and no .ammo/active "
            f"marker was found walking up from {Path(starts[0]).resolve()}",
            file=sys.stderr,
        )
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="AMMO campaign path resolution")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("env", "find", "python"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--start", help="walk up from this dir (default: cwd)")
        cmd.add_argument("--hook", action="store_true", help="read hook input JSON on stdin; stay silent")
    check = sub.add_parser("in-campaign")
    check.add_argument("path")
    args = parser.parse_args(argv)

    if args.cmd == "in-campaign":
        return 0 if in_campaign(args.path, find_campaign_dir()) else 1
    campaign = _find_cli(args)
    if args.cmd == "python":
        print(python_exe(campaign))
        return 0
    if campaign is None:
        return 1
    if args.cmd == "find":
        print(campaign)
        return 0
    env = dict(os.environ)
    _fill(env, campaign)
    for key in campaign_env(campaign):
        print(f"export {key}={shlex.quote(env[key])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
