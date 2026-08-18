#!/usr/bin/env python3
"""Install PAVE Init custom agents into Codex project or user scope.

Codex custom-agent files live in ``.codex/agents`` or ``~/.codex/agents``;
they are not part of the documented plugin manifest.  This explicit installer
copies only the six PAVE files, records their hashes, refuses unsafe overwrite,
and can remove only files it still owns.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

try:
    import tomllib
except ImportError as exc:  # pragma: no cover - Python 3.11+ is expected.
    raise SystemExit("Python 3.11+ is required (missing tomllib).") from exc

MANIFEST_NAME = ".pave-init-codex-agents.json"
REQUIRED_FIELDS = ("name", "description", "developer_instructions")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_dir() -> Path:
    return Path(__file__).resolve().parent / "agents"


def _target_dir(args: argparse.Namespace) -> Path:
    if args.user:
        return Path.home() / ".codex" / "agents"
    project = Path(args.project or os.getcwd()).expanduser().resolve()
    return project / ".codex" / "agents"


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    missing = [field for field in REQUIRED_FIELDS if not data.get(field)]
    if missing:
        raise ValueError(f"{path}: missing {', '.join(missing)}")
    return data


def _sources() -> list[Path]:
    source = _source_dir()
    paths = sorted(source.glob("pave_init_*.toml"))
    if not paths:
        raise FileNotFoundError(f"no PAVE agent TOMLs under {source}")
    names: set[str] = set()
    for path in paths:
        data = _load_toml(path)
        name = str(data["name"])
        if name in names:
            raise ValueError(f"duplicate custom-agent name: {name}")
        names.add(name)
    return paths


def _read_manifest(target: Path) -> dict[str, Any]:
    path = target / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError):
        raise ValueError(f"invalid ownership manifest: {path}")
    return data if isinstance(data, dict) else {}


def _write_atomic(path: Path, source: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def install(target: Path, force: bool) -> int:
    sources = _sources()
    target.mkdir(parents=True, exist_ok=True)
    prior = _read_manifest(target)
    prior_files = prior.get("files", {}) if isinstance(prior.get("files"), dict) else {}

    planned: list[tuple[Path, Path, str]] = []
    for source in sources:
        destination = target / source.name
        source_hash = _sha256(source)
        if destination.exists():
            current_hash = _sha256(destination)
            if current_hash == source_hash:
                planned.append((source, destination, source_hash))
                continue
            owned_hash = prior_files.get(source.name)
            safely_owned = isinstance(owned_hash, str) and owned_hash == current_hash
            if not force and not safely_owned:
                print(
                    f"refusing to overwrite unowned or modified file: {destination}",
                    file=sys.stderr,
                )
                return 2
        planned.append((source, destination, source_hash))

    for source, destination, _ in planned:
        if not destination.exists() or _sha256(destination) != _sha256(source):
            _write_atomic(destination, source)

    manifest = {
        "format": 1,
        "source": str(_source_dir()),
        "files": {destination.name: digest for _, destination, digest in planned},
    }
    manifest_path = target / MANIFEST_NAME
    temp = manifest_path.with_suffix(f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, manifest_path)

    print(f"installed {len(planned)} PAVE custom agents in {target}")
    print("restart Codex so it reloads custom-agent configuration")
    return 0


def check(target: Path) -> int:
    sources = _sources()
    missing_or_changed: list[str] = []
    for source in sources:
        destination = target / source.name
        if not destination.is_file() or _sha256(destination) != _sha256(source):
            missing_or_changed.append(source.name)
    if missing_or_changed:
        print("not installed or changed: " + ", ".join(missing_or_changed), file=sys.stderr)
        return 1
    print(f"PAVE custom agents are current in {target}")
    return 0


def uninstall(target: Path, force: bool) -> int:
    manifest = _read_manifest(target)
    files = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
    if not files:
        print(f"no PAVE ownership manifest in {target}", file=sys.stderr)
        return 1

    blocked: list[str] = []
    removable: list[Path] = []
    for name, expected_hash in files.items():
        path = target / name
        if not path.exists():
            continue
        current_hash = _sha256(path)
        if not force and current_hash != expected_hash:
            blocked.append(name)
        else:
            removable.append(path)

    # Preflight the full uninstall.  Do not leave a half-removed agent set when
    # one owned file was edited after installation.
    if blocked:
        print(
            "refusing to remove modified files: " + ", ".join(sorted(blocked)),
            file=sys.stderr,
        )
        return 2

    for path in removable:
        path.unlink()

    (target / MANIFEST_NAME).unlink(missing_ok=True)
    try:
        target.rmdir()
    except OSError:
        pass
    print(f"removed PAVE custom agents from {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--project", help="Project root. Default: current directory.")
    scope.add_argument("--user", action="store_true", help="Install under ~/.codex/agents.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Verify installed files.")
    action.add_argument("--uninstall", action="store_true", help="Remove owned files.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace or remove conflicting files. Use only after review.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = _target_dir(args)
    try:
        if args.check:
            return check(target)
        if args.uninstall:
            return uninstall(target, force=args.force)
        return install(target, force=args.force)
    except (OSError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
