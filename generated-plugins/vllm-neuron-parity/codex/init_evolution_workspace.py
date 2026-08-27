#!/usr/bin/env python3
"""Initialize durable project-local revision state from the packaged v0 graph."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


PLUGIN_NAME = "vllm-neuron-parity"
STATE_DIR = ".vllm-neuron-parity/evolution"
ORIGIN_NAME = ".codex-evolution-origin.json"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _workspace(project: Path) -> Path:
    return project / STATE_DIR


def _seed_manifest(digest: str) -> str:
    return (
        "# Durable project-local revision lineage for vllm-neuron-parity.\n"
        "draft: workflow.draft.pave.yaml\n"
        f"draft_digest: {digest}\n"
        "draft_version: v0\n"
        "draft_status: approved\n"
        "active_revision: null\n"
        "bundle_digest: null\n"
        "history_dir: history\n"
    )


def check_workspace(project: Path) -> tuple[bool, str]:
    plugin_root = _plugin_root()
    workspace = _workspace(project)
    graph = plugin_root / "workflow.pave.yaml"
    origin_path = workspace / ORIGIN_NAME
    manifest_path = workspace / "workflow-manifest.yaml"
    history = workspace / "history"
    try:
        origin = json.loads(origin_path.read_text(encoding="utf-8"))
        manifest = manifest_path.read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        return False, f"missing or invalid owned evolution workspace: {exc}"
    seed_digest = _sha256(graph)
    if origin.get("plugin") != PLUGIN_NAME or origin.get("seed_digest") != seed_digest:
        return False, "evolution workspace origin does not match this package v0"
    if not history.is_dir():
        return False, "evolution workspace history/ is missing"

    active_match = re.search(r"^active_revision:\s*(\S+)\s*$", manifest, re.MULTILINE)
    if not active_match:
        return False, "workflow-manifest.yaml has no active_revision"
    active = active_match.group(1)
    if active == "null":
        draft = workspace / "workflow.draft.pave.yaml"
        if not draft.is_file() or _sha256(draft) != seed_digest:
            return False, "unfrozen v0 draft does not match the packaged approved graph"
    elif active.isdigit():
        revision = history / f"v{active}"
        if not (revision / "workflow.pave.yaml").is_file() or not (
            revision / "revision.yaml"
        ).is_file():
            return False, f"active revision v{active} is incomplete"
    else:
        return False, f"invalid active_revision value: {active}"
    return True, str(workspace)


def initialize(project: Path) -> int:
    workspace = _workspace(project)
    if workspace.exists():
        ok, detail = check_workspace(project)
        if ok:
            print(f"PASS: evolution workspace already initialized: {detail}")
            return 0
        print(f"FAIL: refusing to overwrite {workspace}: {detail}", file=sys.stderr)
        return 2

    plugin_root = _plugin_root()
    graph = plugin_root / "workflow.pave.yaml"
    seed_digest = _sha256(graph)
    parent = workspace.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".evolution.", dir=parent))
    try:
        (stage / "history").mkdir()
        shutil.copyfile(graph, stage / "workflow.draft.pave.yaml")
        (stage / "workflow-manifest.yaml").write_text(
            _seed_manifest(seed_digest), encoding="utf-8"
        )
        (stage / ORIGIN_NAME).write_text(
            json.dumps(
                {
                    "format": 1,
                    "plugin": PLUGIN_NAME,
                    "seed_digest": seed_digest,
                    "packaged_manifest": "workflow-manifest.yaml",
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(stage, workspace)
    except (OSError, ValueError) as exc:
        shutil.rmtree(stage, ignore_errors=True)
        print(f"FAIL: could not initialize {workspace}: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: initialized durable evolution workspace: {workspace}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.getcwd(), help="Target project root.")
    parser.add_argument("--check", action="store_true", help="Verify existing state.")
    args = parser.parse_args(argv)
    project = Path(args.project).expanduser().resolve()
    if args.check:
        ok, detail = check_workspace(project)
        print(("PASS: " if ok else "FAIL: ") + detail, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    return initialize(project)


if __name__ == "__main__":
    raise SystemExit(main())
