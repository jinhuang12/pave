#!/usr/bin/env python3
"""Install vLLM-Neuron parity custom agents into Codex project or user scope."""

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


MANIFEST_NAME = ".vllm-neuron-parity-codex-agents.json"
REQUIRED_FIELDS = ("name", "description", "developer_instructions")
EXPECTED_AGENTS = {
    "vllm_neuron_parity_adjudicator.toml": "vllm-neuron-parity:adjudicator",
    "vllm_neuron_parity_adversarial_reviewer.toml": (
        "vllm-neuron-parity:adversarial-reviewer"
    ),
    "vllm_neuron_parity_implementer.toml": "vllm-neuron-parity:implementer",
    "vllm_neuron_parity_investigator.toml": "vllm-neuron-parity:investigator",
    "vllm_neuron_parity_measurer.toml": "vllm-neuron-parity:measurer",
    "vllm_neuron_parity_rederiver.toml": "vllm-neuron-parity:rederiver",
}


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
        configured = os.environ.get("CODEX_HOME")
        codex_home = (
            Path(configured).expanduser().resolve()
            if configured
            else Path.home() / ".codex"
        )
        return codex_home / "agents"
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
    paths = [source / name for name in EXPECTED_AGENTS]
    missing = [path.name for path in paths if not path.is_file()]
    actual = {path.name for path in source.glob("vllm_neuron_parity_*.toml")}
    expected = {path.name for path in paths}
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise FileNotFoundError(
            f"invalid vLLM-Neuron parity agent source set under {source}: "
            f"missing={missing}, unexpected={unexpected}"
        )
    for path in paths:
        data = _load_toml(path)
        expected_name = EXPECTED_AGENTS[path.name]
        if data["name"] != expected_name:
            raise ValueError(
                f"{path}: custom-agent name must be {expected_name!r}, "
                f"got {data['name']!r}"
            )
    return paths


def _read_manifest(target: Path) -> dict[str, Any]:
    path = target / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid ownership manifest: {path}") from exc
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

    for source, destination, source_hash in planned:
        if not destination.exists() or _sha256(destination) != source_hash:
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

    print(f"installed {len(planned)} vLLM-Neuron parity custom agents in {target}")
    print("restart Codex so it reloads custom-agent configuration")
    return 0


def check(target: Path) -> int:
    missing_or_changed: list[str] = []
    for source in _sources():
        destination = target / source.name
        if not destination.is_file() or _sha256(destination) != _sha256(source):
            missing_or_changed.append(source.name)
    if missing_or_changed:
        print("not installed or changed: " + ", ".join(missing_or_changed), file=sys.stderr)
        return 1
    print(f"vLLM-Neuron parity custom agents are current in {target}")
    return 0


def uninstall(target: Path, force: bool) -> int:
    manifest = _read_manifest(target)
    files = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
    if not files:
        print(f"no vLLM-Neuron parity ownership manifest in {target}", file=sys.stderr)
        return 1

    blocked: list[str] = []
    removable: list[Path] = []
    for name, expected_hash in files.items():
        path = target / name
        if not path.exists():
            continue
        if not force and _sha256(path) != expected_hash:
            blocked.append(name)
        else:
            removable.append(path)
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
    print(f"removed vLLM-Neuron parity custom agents from {target}")
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
