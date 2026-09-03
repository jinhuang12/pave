#!/usr/bin/env python3
"""Record and verify workflow revisions in an evolution root.

A root holds one live canonical graph — workflow.pave.yaml plus any child
<name>.pave.yaml beside it — and one append-only ledger, revisions.yaml. Entry 0
is the delivered graph; every successor is a unified-diff patch under history/,
landed by appending an entry. A delivered package carrying entry 0 and no
patches is a valid root too, so verify runs on a package and on an installed
project root alike. kind (graph | binding | pin) is declared by the proposer,
never inferred from digests: a binding revision moves the live digest as well,
because instruments live in the YAML. The pinned bundle is the newest graph or
binding entry, the active graph revision is the last graph entry, and pin
entries are informational. A .landing marker exists only while land, pin, or
rollback runs; verify reports a leftover marker as an interrupted landing,
distinct from an unrecorded edit (the live digest moved, no entry explains it).

Requires pyyaml and the git command-line tool (git apply, git diff --no-index).
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("FAIL: pyyaml is required", file=sys.stderr)
    sys.exit(2)

VALIDATOR = Path(__file__).resolve().parent / "validate_pave.py"
LEDGER = "revisions.yaml"
MARKER = ".landing"
ROOT_GRAPH = "workflow.pave.yaml"
BUNDLE_KINDS = ("graph", "binding")
DIFF_START = ("diff --git ", "--- ")
ENTRY_FIELDS = (
    "revision", "kind", "landed_at", "digest_before", "digest_after", "semantic_diff",
    "approval", "envelope_check", "plan_evidence", "usage_evidence", "review", "patch",
    "commit", "derived_from", "run_id",
)
PREAMBLE_FIELDS = (
    "kind", "semantic_diff", "envelope_check", "plan_evidence", "usage_evidence",
    "changelog_entry",
)
ENUMS = {
    "kind": BUNDLE_KINDS,
    "envelope_check": ("unchanged", "changed_with_approval"),
    "plan_evidence": ("verified", "provisional"),
    "usage_evidence": ("none", "clean_room", "field"),
}


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


def graph_files(root: Path) -> dict:
    """Map graph file name -> path, rejecting symlinks and hard links."""
    files = {path.name: path for path in sorted(root.glob("*.pave.yaml"))}
    if ROOT_GRAPH not in files:
        raise ValueError(f"{root / ROOT_GRAPH} not found; not an evolution root")
    for path in files.values():
        check_regular(path)
    return files


def live_digest(root: Path) -> str:
    return bundle_digest({n: file_digest(p) for n, p in graph_files(root).items()})


def read_ledger(root: Path) -> list:
    path = root / LEDGER
    if not path.is_file():
        raise ValueError(f"{path} not found; not an evolution root")
    document = yaml.safe_load(path.read_text()) or {}
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty entries list")
    return entries


def write_ledger(root: Path, entries: list):
    (root / LEDGER).write_text(yaml.safe_dump({"entries": entries}, sort_keys=False))


def head_entry(entries: list) -> dict:
    """The newest graph or binding entry: the pinned bundle."""
    bundle = [e for e in entries if e.get("kind") in BUNDLE_KINDS]
    if not bundle:
        raise ValueError(f"{LEDGER}: no graph or binding entry")
    return bundle[-1]


def make_entry(**fields) -> dict:
    """One ledger entry, every field present in a fixed order, unset fields null."""
    fields["landed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {name: fields.get(name) for name in ENTRY_FIELDS}


def split_patch(text: str) -> tuple[str, str]:
    """Split a proposal into its YAML preamble and the unified diff."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(DIFF_START):
            return "".join(lines[:index]), "".join(lines[index:])
    raise ValueError("the proposal holds no unified diff (no 'diff --git ' or '--- ' line)")


def read_proposal(path: Path) -> tuple[dict, str]:
    if not path.is_file():
        raise ValueError(f"{path} not found")
    head, diff = split_patch(path.read_text())
    preamble = yaml.safe_load(head) if head.strip() else None
    if not isinstance(preamble, dict):
        raise ValueError(f"{path}: the preamble before the diff must be a YAML mapping")
    # Only the proposer's own declarations may enter through the preamble: every
    # other entry field (commit, digests, revision, derived_from, run_id, ...) is
    # an identifier the tool derives, never one the proposer can mint.
    minted = sorted(set(preamble) - set(PREAMBLE_FIELDS) - {"approval", "review"})
    if minted:
        raise ValueError(f"{path}: the preamble may not set {', '.join(minted)}; the tool writes those")
    for field in PREAMBLE_FIELDS:
        if field not in preamble:
            raise ValueError(f"{path}: the preamble declares no {field}")
    for field, allowed in ENUMS.items():
        if preamble[field] not in allowed:
            raise ValueError(f"{path}: preamble {field} must be one of {', '.join(allowed)}")
    for field in ("semantic_diff", "changelog_entry"):
        if not (isinstance(preamble[field], str) and preamble[field].strip()):
            raise ValueError(f"{path}: preamble {field} must be a non-empty string")
    return preamble, diff


def git(arguments: list, cwd: Path, ok=(0,)) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(["git", *arguments], cwd=str(cwd), capture_output=True, text=True)
    except FileNotFoundError:
        raise ValueError("the git command-line tool is required")
    if proc.returncode not in ok:
        raise ValueError(f"git {arguments[0]} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def apply_diff(diff: str, cwd: Path, reverse: bool = False):
    flags = ["-R"] if reverse else []
    # Inside a git work tree, `git apply` reads patch paths relative to the top
    # level and silently skips files outside the current directory, so a root
    # below the top level would "land" nothing. Re-anchor the paths at the root.
    prefix = subprocess.run(["git", "rev-parse", "--show-prefix"], cwd=str(cwd),
                            capture_output=True, text=True)
    if prefix.returncode == 0 and prefix.stdout.strip():
        flags.append(f"--directory={prefix.stdout.strip().rstrip('/')}")
    handle = tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False)
    try:
        handle.write(diff if diff.endswith("\n") else diff + "\n")
        handle.close()
        git(["apply", "--check", *flags, handle.name], cwd)
        git(["apply", *flags, handle.name], cwd)
    finally:
        Path(handle.name).unlink(missing_ok=True)


def validate_graph(root: Path):
    proc = subprocess.run([sys.executable, str(VALIDATOR), str(root / ROOT_GRAPH)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(f"the graph does not validate: {(proc.stdout + proc.stderr).strip()}")


def snapshot(root: Path) -> dict:
    """Remember every file a landing may rewrite, so a caught failure can undo it."""
    saved = {path.name: path.read_bytes() for path in root.glob("*.pave.yaml")}
    if (root / LEDGER).is_file():
        saved[LEDGER] = (root / LEDGER).read_bytes()
    return saved


def restore(root: Path, saved: dict):
    for name, data in saved.items():
        (root / name).write_bytes(data)
    for path in root.glob("*.pave.yaml"):
        if path.name not in saved:
            path.unlink()


def take_marker(root: Path, revision: int) -> Path:
    marker = root / MARKER
    if marker.exists():
        raise ValueError("landing interrupted: restore the root from version control, remove .landing, then verify")
    marker.write_text(f"{revision}\n")
    return marker


def check_chain(root: Path) -> tuple[list, str]:
    """Verify the marker, the digest chain, the patch files, and the live digest."""
    if (root / MARKER).exists():
        raise ValueError("landing interrupted: restore the root from version control, remove .landing, then verify")
    entries = read_ledger(root)
    live = live_digest(root)
    previous = None
    for entry in entries:
        kind, revision = entry.get("kind"), entry.get("revision")
        before, after = entry.get("digest_before"), entry.get("digest_after")
        if kind in BUNDLE_KINDS and previous is None:
            if before is not None:
                raise ValueError(f"revision {revision}: the first entry has no predecessor,"
                                 " so digest_before must be null")
        elif kind in BUNDLE_KINDS:
            if before != previous["digest_after"] or revision != previous["revision"] + 1:
                raise ValueError(f"revision {revision} does not continue revision"
                                 f" {previous['revision']} by number and digest")
            if not entry.get("patch") or not (root / entry["patch"]).is_file():
                raise ValueError(f"revision {revision}: patch {entry.get('patch')!r} is missing")
        elif kind == "pin":
            pinned = previous["digest_after"] if previous else None
            if previous is None or revision != previous["revision"] or (before, after) != (pinned, pinned):
                raise ValueError(f"pin entry for revision {revision}: a pin records the pinned"
                                 " revision and its digest on both sides")
        else:
            raise ValueError(f"revision {revision}: unknown kind {kind!r}")
        if kind in BUNDLE_KINDS:
            previous = entry
    if previous is None:
        raise ValueError(f"{LEDGER}: no graph or binding entry")
    if live != previous["digest_after"]:
        raise ValueError(f"unrecorded edit: the live digest {live} is not revision"
                         f" {previous['revision']} digest_after")
    return entries, live


def init(args) -> int:
    root = Path(args.root)
    if (root / LEDGER).exists():
        raise ValueError(f"{root / LEDGER} already exists; a root is initialised once")
    files = graph_files(root)
    digest = bundle_digest({n: file_digest(p) for n, p in files.items()})
    write_ledger(root, [make_entry(
        revision=0, kind="graph", digest_after=digest, approval=args.approval,
        plan_evidence=args.plan_evidence, usage_evidence=args.usage_evidence,
    )])
    print(f"PASS: {root} starts at revision 0 ({len(files)} graph file(s)) {digest}")
    return 0


def install(args) -> int:
    root, source = Path(args.root), Path(args.from_root)
    if root.exists() and not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    if root.is_dir() and any(root.iterdir()):
        raise ValueError(f"{root} is not empty; install targets a new or empty directory")
    if not (source / LEDGER).is_file():
        raise ValueError(f"{source} is not an evolution root: no {LEDGER}")
    names = list(graph_files(source)) + [LEDGER]
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(source / name, root / name)
    if (source / "history").is_dir():
        shutil.copytree(source / "history", root / "history")
    entries, digest = check_chain(root)
    revision = head_entry(entries)["revision"]
    print(f"PASS: installed revision {revision} from {source} into {root} {digest}")
    return 0


def propose(args) -> int:
    root = Path(args.root)
    preamble, diff = read_proposal(Path(args.patch))
    files = graph_files(root)
    scratch = Path(tempfile.mkdtemp(prefix="pave-propose-"))
    try:
        for name, path in files.items():
            shutil.copyfile(path, scratch / name)
        apply_diff(diff, scratch)
        validate_graph(scratch)
        digest = live_digest(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"PASS: the proposal applies to {root} and validates; digest_after {digest}")
    for field in PREAMBLE_FIELDS:
        print(f"  {field}: {preamble[field]}")
    return 0


def commit_landing(root: Path, revision: int, patch: str) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root),
                          capture_output=True, text=True)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        print(f"WARN: {root} is not inside a git work tree; nothing committed", file=sys.stderr)
        return None
    names = [path.name for path in sorted(root.glob("*.pave.yaml"))] + [LEDGER, patch]
    git(["add", "--", *names], root)
    git(["commit", "-m", f"land revision {revision}"], root)
    return git(["rev-parse", "HEAD"], root).stdout.strip()


def land(args) -> int:
    root = Path(args.root)
    patch = f"history/v{args.revision}.patch"
    preamble, diff = read_proposal(root / patch)
    entries, digest_before = check_chain(root)
    head = head_entry(entries)
    if args.revision != head["revision"] + 1:
        raise ValueError(f"revision {args.revision} does not follow the pinned revision"
                         f" {head['revision']}; land v{head['revision'] + 1}")
    saved = snapshot(root)
    marker = take_marker(root, args.revision)
    try:
        apply_diff(diff, root)
        validate_graph(root)
        digest_after = live_digest(root)
        if digest_after == digest_before:
            raise ValueError("the patch changed no graph file; nothing to land")
        fields = dict(preamble, revision=args.revision, digest_before=digest_before,
                      digest_after=digest_after, patch=patch)
        fields["approval"] = args.approval or preamble.get("approval")
        fields["review"] = args.review or preamble.get("review")
        entry = make_entry(**fields)
        entries.append(entry)
        write_ledger(root, entries)
        if args.commit:
            entry["commit"] = commit_landing(root, args.revision, patch)
            write_ledger(root, entries)
    except (ValueError, OSError):
        restore(root, saved)
        marker.unlink(missing_ok=True)
        raise
    marker.unlink(missing_ok=True)
    print(f"PASS: landed revision {args.revision} (kind {entry['kind']}) {entry['digest_after']}")
    return 0


def pin(args) -> int:
    root = Path(args.root)
    entries, live = check_chain(root)
    head = head_entry(entries)
    marker = take_marker(root, head["revision"])
    try:
        entries.append(make_entry(revision=head["revision"], kind="pin", digest_before=live,
                                  digest_after=live, run_id=args.run_id))
        write_ledger(root, entries)
    finally:
        marker.unlink(missing_ok=True)
    print(f"PASS: pinned run {args.run_id} to revision {head['revision']} {live}")
    return 0


def verify(args) -> int:
    root = Path(args.root)
    entries, digest = check_chain(root)
    head = head_entry(entries)
    if args.pinned_revision is None and args.pinned_digest is None:
        print(f"PASS: {root} is intact at revision {head['revision']}"
              f" ({len(entries)} ledger entries) {digest}")
        return 0
    if args.pinned_revision is None or args.pinned_digest is None:
        raise ValueError("--pinned-revision and --pinned-digest go together")
    match = next((e for e in entries if e.get("kind") in BUNDLE_KINDS
                  and e.get("revision") == args.pinned_revision), None)
    if match is None:
        raise ValueError(f"the pin names revision {args.pinned_revision}; the ledger records"
                         " no graph or binding entry for it")
    if match["digest_after"] != args.pinned_digest:
        raise ValueError(f"the pinned digest is not revision {args.pinned_revision} digest_after")
    newer = [e for e in entries if e.get("kind") in BUNDLE_KINDS
             and e["revision"] > args.pinned_revision]
    if not newer:
        print("PASS: current")
        return 0
    graphs = [e["revision"] for e in newer if e["kind"] == "graph"]
    if graphs:
        print(f"ROUTE: graph landed since pin (revision {max(graphs)})")
        return 3
    print(f"ROUTE: binding landed since pin (revision {max(e['revision'] for e in newer)})")
    return 4


def rollback(args) -> int:
    root = Path(args.root)
    entries, digest_before = check_chain(root)
    head = head_entry(entries)
    target = next((e for e in entries if e.get("kind") in BUNDLE_KINDS
                   and e.get("revision") == args.to), None)
    if target is None:
        raise ValueError(f"the ledger records no graph or binding revision {args.to}")
    undone = [e for e in entries if e.get("kind") in BUNDLE_KINDS and e["revision"] > args.to]
    if not undone:
        raise ValueError(f"revision {args.to} is already the pinned revision; nothing to undo")
    revision = head["revision"] + 1
    kind = "graph" if any(e["kind"] == "graph" for e in undone) else "binding"
    patch = f"history/v{revision}.patch"
    saved = snapshot(root)
    marker = take_marker(root, revision)
    scratch = Path(tempfile.mkdtemp(prefix="pave-rollback-"))
    try:
        for side in ("a", "b"):
            (scratch / side).mkdir()
        for name, data in saved.items():
            if name != LEDGER:
                (scratch / "a" / name).write_bytes(data)
        for entry in reversed(undone):
            apply_diff(split_patch((root / entry["patch"]).read_text())[1], root, reverse=True)
        validate_graph(root)
        digest_after = live_digest(root)
        if digest_after != target["digest_after"]:
            raise ValueError(f"the reverse-applied digest {digest_after} is not revision"
                             f" {args.to} digest_after; the patch chain is inconsistent")
        for name, path in graph_files(root).items():
            shutil.copyfile(path, scratch / "b" / name)
        forward = git(["diff", "--no-index", "--src-prefix=", "--dst-prefix=", "a", "b"],
                      scratch, ok=(0, 1)).stdout
        if not forward.strip():
            raise ValueError("the rollback changes nothing; there is no revision to record")
        fields = dict(
            revision=revision, kind=kind, digest_before=digest_before, digest_after=digest_after,
            semantic_diff=args.semantic_diff, approval=args.approval,
            envelope_check="changed_with_approval", plan_evidence=target.get("plan_evidence"),
            usage_evidence=target.get("usage_evidence"), patch=patch, derived_from=args.to,
        )
        preamble = {field: fields.get(field) for field in PREAMBLE_FIELDS}
        preamble["changelog_entry"] = f"Roll back to revision {args.to}."
        (root / "history").mkdir(exist_ok=True)
        (root / patch).write_text(yaml.safe_dump(preamble, sort_keys=False) + forward)
        entries.append(make_entry(**fields))
        write_ledger(root, entries)
    except (ValueError, OSError):
        restore(root, saved)
        (root / patch).unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    marker.unlink(missing_ok=True)
    print(f"PASS: landed revision {revision} (kind {kind}) back to revision {args.to}"
          f" {digest_after}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write revisions.yaml with entry 0 beside a delivered graph")
    p.add_argument("root", help="the evolution root: holds workflow.pave.yaml and no ledger yet")
    p.add_argument("--plan-evidence", choices=["verified", "provisional"], required=True)
    p.add_argument("--approval", required=True, help="the approval that authorized delivery, verbatim")
    p.add_argument("--usage-evidence", choices=["none", "clean_room"], default="none")
    p.set_defaults(func=init)
    p = sub.add_parser("install", help="copy a package root into a project root and verify it")
    p.add_argument("root", help="a nonexistent or empty destination directory")
    p.add_argument("--from", dest="from_root", required=True, help="the package root to copy")
    p.set_defaults(func=install)
    p = sub.add_parser("propose", help="check a proposal against a root without touching the root")
    p.add_argument("root")
    p.add_argument("--patch", required=True, help="the proposal: YAML preamble then unified diff")
    p.set_defaults(func=propose)
    p = sub.add_parser("land", help="apply history/vN.patch and append its ledger entry")
    p.add_argument("root")
    p.add_argument("revision", type=int, help="N: the successor revision number")
    p.add_argument("--approval", default=None, help="overrides the preamble's approval")
    p.add_argument("--review", default=None, help="the review verdict and rounds")
    p.add_argument("--commit", action="store_true", help="also git add and git commit the landing")
    p.set_defaults(func=land)
    p = sub.add_parser("pin", help="append the informational pin entry for a run")
    p.add_argument("root")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=pin)
    p = sub.add_parser("verify", help="check the live graph against the ledger, and a run's pin")
    p.add_argument("root")
    p.add_argument("--pinned-revision", type=int, default=None)
    p.add_argument("--pinned-digest", default=None)
    p.set_defaults(func=verify)
    p = sub.add_parser("rollback", help="reverse-apply down to revision N and land the result")
    p.add_argument("root")
    p.add_argument("--to", type=int, required=True)
    p.add_argument("--approval", required=True, help="the approval for the rollback, verbatim")
    p.add_argument("--semantic-diff", required=True, help="what the rollback restores and why")
    p.set_defaults(func=rollback)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (ValueError, OSError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
