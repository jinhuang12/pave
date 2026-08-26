#!/usr/bin/env python3
"""Freeze and verify immutable workflow revisions.

freeze: copy the approved draft (or, with --from-revision, an older frozen
revision for append-only rollback) into history/vN/, write revision.yaml with
per-file and bundle digests, and set the manifest's active_revision.

verify: recompute digests for a frozen revision, reject symlinks and hard
links, and fail on any mismatch.

active_revision never moves backward: rollback is a new successor derived
from an older revision, recorded via derived_from.
"""
import argparse
import hashlib
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: pyyaml is required", file=sys.stderr)
    sys.exit(2)


def check_regular(path: Path):
    if path.is_symlink():
        raise ValueError(f"symlink not allowed in a revision: {path}")
    if path.stat().st_nlink > 1:
        raise ValueError(f"hard link not allowed in a revision: {path}")


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_digest(files: dict) -> str:
    joined = "".join(f"{name}\0{digest}\n" for name, digest in sorted(files.items()))
    return "sha256:" + hashlib.sha256(joined.encode()).hexdigest()


def latest_revision(history: Path) -> int:
    revs = [int(p.name[1:]) for p in history.glob("v*") if p.name[1:].isdigit()]
    return max(revs) if revs else 0


def collect_sources(args, workspace: Path, history: Path) -> dict:
    """Map frozen-name -> source path, from the draft or an older revision."""
    if args.from_revision:
        src_dir = history / f"v{args.from_revision}"
        if not (src_dir / "revision.yaml").is_file():
            raise ValueError(f"--from-revision: {src_dir} is not a frozen revision")
        record = yaml.safe_load((src_dir / "revision.yaml").read_text())
        return {name: src_dir / name for name in record.get("files", {})}
    draft = workspace / "workflow.draft.pave.yaml"
    if not draft.is_file():
        raise ValueError(f"{draft} not found")
    sources = {"workflow.pave.yaml": draft}
    for child in sorted(workspace.glob("*.draft.pave.yaml")):
        if child.name != "workflow.draft.pave.yaml":
            sources[child.name.replace(".draft.pave.yaml", ".pave.yaml")] = child
    return sources


def freeze(args) -> int:
    workspace = Path(args.workspace)
    history = workspace / "history"
    prior = latest_revision(history)
    if args.from_revision and args.from_revision >= max(prior, 1):
        pass  # deriving from the latest is legal, just unusual
    revision = prior + 1
    rev_dir = history / f"v{revision}"
    if rev_dir.exists():
        print(f"FAIL: {rev_dir} already exists", file=sys.stderr)
        return 1
    if args.from_revision and not args.semantic_diff:
        print("FAIL: a rollback revision requires --semantic-diff stating the reason",
              file=sys.stderr)
        return 1
    if revision > 1 and not args.from_revision and not args.semantic_diff:
        print("FAIL: a successor revision requires --semantic-diff", file=sys.stderr)
        return 1

    try:
        sources = collect_sources(args, workspace, history)
        for src in sources.values():
            check_regular(src)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    rev_dir.mkdir(parents=True)
    frozen = {}
    for name, src in sources.items():
        shutil.copyfile(src, rev_dir / name)
        frozen[name] = file_digest(rev_dir / name)

    record = {
        "revision": revision,
        "predecessor": prior if prior else None,
        "derived_from": args.from_revision,
        "frozen_at_stage": "release",
        "files": frozen,
        "bundle_digest": bundle_digest(frozen),
        "evidence_basis": {
            "plan_evidence": args.plan_evidence,
            "usage_evidence": args.usage_evidence,
        },
        "semantic_diff": args.semantic_diff,
    }
    (rev_dir / "revision.yaml").write_text(yaml.safe_dump(record, sort_keys=False))

    manifest = {
        "active_revision": revision,
        "bundle_digest": record["bundle_digest"],
        "history_dir": "history",
    }
    (workspace / "workflow-manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False)
    )
    origin = f" derived from v{args.from_revision}" if args.from_revision else ""
    print(f"PASS: froze v{revision}{origin} ({len(frozen)} file(s)), active_revision=v{revision}")
    return 0


def verify(args) -> int:
    rev_dir = Path(args.revision_dir)
    record_path = rev_dir / "revision.yaml"
    if not record_path.is_file():
        print(f"FAIL: {record_path} not found", file=sys.stderr)
        return 1
    record = yaml.safe_load(record_path.read_text())
    errors = []
    for name, recorded in record.get("files", {}).items():
        path = rev_dir / name
        if not path.is_file():
            errors.append(f"missing frozen file: {name}")
            continue
        try:
            check_regular(path)
        except ValueError as e:
            errors.append(str(e))
        if file_digest(path) != recorded:
            errors.append(f"digest mismatch: {name}")
    if bundle_digest(record.get("files", {})) != record.get("bundle_digest"):
        errors.append("bundle digest mismatch")
    manifest_path = rev_dir.parent.parent / "workflow-manifest.yaml"
    if manifest_path.is_file():
        manifest = yaml.safe_load(manifest_path.read_text())
        active = manifest.get("active_revision")
        newest = latest_revision(rev_dir.parent)
        if active is not None and active != newest:
            errors.append(
                f"manifest active_revision v{active} is not the newest revision v{newest}; "
                "history must be append-only"
            )
    if errors:
        print(f"FAIL {rev_dir}:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"PASS: v{record['revision']} is intact")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    f = sub.add_parser("freeze", help="freeze the next revision")
    f.add_argument("workspace", help="run workspace containing workflow.draft.pave.yaml")
    f.add_argument("--plan-evidence", choices=["verified", "provisional"], required=True)
    f.add_argument(
        "--usage-evidence", choices=["none", "clean_room", "field"], required=True
    )
    f.add_argument("--semantic-diff", default=None,
                   help="v2+: what changed from the predecessor and why")
    f.add_argument("--from-revision", type=int, default=None,
                   help="rollback: derive content from this older frozen revision")
    f.set_defaults(func=freeze)

    v = sub.add_parser("verify", help="verify a frozen revision directory")
    v.add_argument("revision_dir", help="history/vN directory")
    v.set_defaults(func=verify)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
