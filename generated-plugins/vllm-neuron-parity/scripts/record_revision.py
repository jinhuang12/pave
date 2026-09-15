#!/usr/bin/env python3
"""Record and verify workflow revisions in a revision folder.

A root holds one live canonical graph — workflow.pave.yaml plus any child
<name>.pave.yaml beside it — and one append-only revision_log, revisions.yaml. Entry 0
is the delivered graph; every successor is a unified-diff patch under history/,
applied by appending an entry. A delivered package carrying entry 0 and no
patches is a valid root too, so verify runs on a package and on an installed
project root alike. kind (graph | run_setup | pin) is declared by the proposer,
never inferred from digests: a run-setup revision moves the live digest as well,
because implementations live in the YAML. The pinned bundle is the newest graph or
run-setup entry, the active graph revision is the last graph entry, and pin
entries are informational: a pin never closes an audit cycle. A .applying marker
exists only while apply, pin, or rollback runs; verify reports a leftover marker
as an interrupted apply step, distinct from an unrecorded edit (the live digest
moved, no entry explains it).

A proposal whose user_only_changes is pending proposes but never
applies: apply refuses it until the user's approval is recorded verbatim. apply
records who drafted the patch in written_by (workflow-updater when the
--proposal path, size and mtime match a hook_record in the --hook-records sidecar's
hook_recorded_writes; unverified otherwise). propose and apply refuse a
write_limits blocked path pattern that matches a path the graph itself declares under
evidence, state, produces or consumes — matched the way the runtime guard matches
it, against the whole path and every trailing-component suffix, so `increments/*`
is refused against a declared campaigns/<c>/increments/ directory.

Requires pyyaml and the git command-line tool (git apply, git diff --no-index).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
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
REVISION_LOG = "revisions.yaml"
MARKER = ".applying"
ROOT_GRAPH = "workflow.pave.yaml"
BUNDLE_KINDS = ("graph", "run_setup")
DIFF_START = ("diff --git ", "--- ")
ENTRY_FIELDS = (
    "revision", "kind", "applied_at", "digest_before", "digest_after", "semantic_diff",
    "approval", "user_only_changes", "plan_evidence", "usage_evidence", "review", "written_by",
    "patch", "commit", "derived_from", "run_id",
)
PREAMBLE_FIELDS = (
    "kind", "semantic_diff", "user_only_changes", "plan_evidence", "usage_evidence",
    "changelog_entry",
)
ENUMS = {
    "kind": BUNDLE_KINDS,
    "user_only_changes": ("none", "approved", "pending"),
    "plan_evidence": ("verified", "provisional"),
    "usage_evidence": ("none", "clean_room", "field"),
}
PENDING = "pending"
PENDING_MESSAGE = ("a user-only change awaits the user's approval; re-propose with"
                   " approved and the approval verbatim")
PIN_HELP = "append the pin entry for a run: informational; never closes an audit cycle"
# Keys whose string values may declare a path relative to the run workspace.
DECLARING_KEYS = ("evidence", "state", "produces", "consumes")
PATH_LIKE = re.compile(r"[^\s/]\S*")


class Refusal(ValueError):
    """A refusal with its own exit code: 3 = user_only_changes pending, 4 = glob on a declared path."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


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
        raise ValueError(f"{root / ROOT_GRAPH} not found; not a revision folder")
    for path in files.values():
        check_regular(path)
    return files


def live_digest(root: Path) -> str:
    return bundle_digest({n: file_digest(p) for n, p in graph_files(root).items()})


def read_revision_log(root: Path) -> list:
    path = root / REVISION_LOG
    if not path.is_file():
        raise ValueError(f"{path} not found; not a revision folder")
    document = yaml.safe_load(path.read_text()) or {}
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{path}: expected a non-empty entries list")
    return entries


def write_revision_log(root: Path, entries: list):
    (root / REVISION_LOG).write_text(yaml.safe_dump({"entries": entries}, sort_keys=False))


def head_entry(entries: list) -> dict:
    """The newest graph or run-setup entry: the pinned bundle."""
    bundle = [e for e in entries if e.get("kind") in BUNDLE_KINDS]
    if not bundle:
        raise ValueError(f"{REVISION_LOG}: no graph or run-setup entry")
    return bundle[-1]


def make_entry(**fields) -> dict:
    """One revision_log entry, every field present in a fixed order, unset fields null."""
    fields["applied_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
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
    # below the top level would "apply" nothing. Re-anchor the paths at the root.
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


def declared_paths(node, under: bool = False) -> set:
    """Every string under an evidence/state/produces/consumes key that looks like a relative path."""
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found |= declared_paths(value, under or key in DECLARING_KEYS)
    elif isinstance(node, list):
        for value in node:
            found |= declared_paths(value, under)
    elif under and isinstance(node, str) and PATH_LIKE.fullmatch(node):
        if "/" in node or re.search(r"\.[A-Za-z0-9]+$", node):
            found.add(node)
    return found


def glob_covers(declared: str, pattern: str) -> bool:
    """True when the runtime guard would match `pattern` against the declared path.

    The guard matches a glob against the workspace-relative path and against every
    trailing-component suffix of it, so `increments/*` reaches a declared
    `campaigns/c1/increments/`. A pattern starting with `/` is workspace-absolute and
    never covers a relative declared path. Kept in step with the same helper in
    validate_pave.py; neither script imports the other.
    """
    if pattern.startswith("/"):
        return False
    candidates = {declared, declared.rstrip("/")}
    for text in tuple(candidates):
        parts = text.split("/")
        candidates.update("/".join(parts[index:]) for index in range(len(parts)))
    return any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates)


def check_deny_globs(root: Path):
    """Refuse (exit 4) a write_limits blocked path pattern that reaches a path the bundle
    declares, matched the way the runtime guard matches it (see glob_covers)."""
    documents = [yaml.safe_load(path.read_text()) or {} for path in graph_files(root).values()]
    paths = set().union(*(declared_paths(document) for document in documents))
    for document in documents:
        pave = document.get("pave") if isinstance(document, dict) else None
        for rule in ((pave or {}).get("write_limits") or {}).get("deny") or []:
            glob = rule.get("glob") if isinstance(rule, dict) else None
            if not isinstance(glob, str):
                continue
            for declared in sorted(paths):
                if glob_covers(declared, glob):
                    raise Refusal(f"write_limits blocked path pattern {glob!r} matches the declared path"
                                  f" {declared!r}; a run-setup entry never denies what the graph declares", 4)


def validate_graph(root: Path):
    proc = subprocess.run([sys.executable, str(VALIDATOR), str(root / ROOT_GRAPH)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ValueError(f"the graph does not validate: {(proc.stdout + proc.stderr).strip()}")


def snapshot(root: Path) -> dict:
    """Remember every file an apply step may rewrite, so a caught failure can undo it."""
    saved = {path.name: path.read_bytes() for path in root.glob("*.pave.yaml")}
    if (root / REVISION_LOG).is_file():
        saved[REVISION_LOG] = (root / REVISION_LOG).read_bytes()
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
        raise ValueError("applying interrupted: restore the root from version control, remove .applying, then verify")
    marker.write_text(f"{revision}\n")
    return marker


def check_chain(root: Path) -> tuple[list, str]:
    """Verify the marker, the digest chain, the patch files, and the live digest."""
    if (root / MARKER).exists():
        raise ValueError("applying interrupted: restore the root from version control, remove .applying, then verify")
    entries = read_revision_log(root)
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
        raise ValueError(f"{REVISION_LOG}: no graph or run-setup entry")
    if live != previous["digest_after"]:
        raise ValueError(f"unrecorded edit: the live digest {live} is not revision"
                         f" {previous['revision']} digest_after")
    return entries, live


def init(args) -> int:
    root = Path(args.root)
    if (root / REVISION_LOG).exists():
        raise ValueError(f"{root / REVISION_LOG} already exists; a root is initialised once")
    files = graph_files(root)
    digest = bundle_digest({n: file_digest(p) for n, p in files.items()})
    write_revision_log(root, [make_entry(
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
    if not (source / REVISION_LOG).is_file():
        raise ValueError(f"{source} is not a revision folder: no {REVISION_LOG}")
    names = list(graph_files(source)) + [REVISION_LOG]
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
        check_deny_globs(scratch)
        validate_graph(scratch)
        digest = live_digest(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f"PASS: the proposal applies to {root} and validates; digest_after {digest}")
    for field in PREAMBLE_FIELDS:
        print(f"  {field}: {preamble[field]}")
    return 0


def commit_apply(root: Path, revision: int, patch: str) -> str | None:
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(root),
                          capture_output=True, text=True)
    if proc.returncode != 0 or proc.stdout.strip() != "true":
        print(f"WARN: {root} is not inside a git work tree; nothing committed", file=sys.stderr)
        return None
    names = [path.name for path in sorted(root.glob("*.pave.yaml"))] + [REVISION_LOG, patch]
    git(["add", "--", *names], root)
    git(["commit", "-m", f"apply revision {revision}"], root)
    return git(["rev-parse", "HEAD"], root).stdout.strip()


def written_by(proposal: Path | None, hook_records: Path | None) -> str:
    """workflow-updater when the proposal's path, size and mtime match a hook hook_record; else unverified."""
    if proposal is None or hook_records is None:
        return "unverified"
    if not hook_records.is_file():
        raise ValueError(f"{hook_records} not found; the hook records sidecar is hook-written")
    try:
        sidecar = json.loads(hook_records.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f"{hook_records}: not JSON ({error})")
    stat = proposal.stat()
    for hook_record in (sidecar.get("hook_recorded_writes") if isinstance(sidecar, dict) else None) or []:
        if not isinstance(hook_record, dict):
            continue
        same_path = Path(str(hook_record.get("path", ""))).resolve() == proposal.resolve()
        same_size = hook_record.get("size") == stat.st_size
        same_mtime = isinstance(hook_record.get("mtime"), (int, float)) and abs(hook_record["mtime"] - stat.st_mtime) < 1e-6
        if same_path and same_size and same_mtime:
            return "workflow-updater"
    return "unverified"


def stage_proposal(root: Path, patch: str, proposal: Path) -> bool:
    """Copy the proposal to history/vN.patch; True when this call created the copy."""
    if not proposal.is_file():
        raise ValueError(f"{proposal} not found")
    target = root / patch
    if target.exists():
        if target.read_bytes() == proposal.read_bytes():
            return False
        raise ValueError(f"{target} already exists and differs from {proposal}; remove one")
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(proposal, target)
    return True


def apply(args) -> int:
    root = Path(args.root)
    patch = f"history/v{args.revision}.patch"
    proposal = Path(args.proposal) if args.proposal else None
    hook_records = Path(args.hook_records) if args.hook_records else None
    preamble, diff = read_proposal(proposal or root / patch)
    if preamble["user_only_changes"] == PENDING:
        raise Refusal(PENDING_MESSAGE, 3)
    drafter = written_by(proposal, hook_records)
    entries, digest_before = check_chain(root)
    head = head_entry(entries)
    if args.revision != head["revision"] + 1:
        raise ValueError(f"revision {args.revision} does not follow the pinned revision"
                         f" {head['revision']}; apply v{head['revision'] + 1}")
    saved = snapshot(root)
    marker = take_marker(root, args.revision)
    copied = False
    try:
        if proposal is not None:
            copied = stage_proposal(root, patch, proposal)
        apply_diff(diff, root)
        check_deny_globs(root)
        validate_graph(root)
        digest_after = live_digest(root)
        if digest_after == digest_before:
            raise ValueError("the patch changed no graph file; nothing to apply")
        fields = dict(preamble, revision=args.revision, digest_before=digest_before,
                      digest_after=digest_after, patch=patch, written_by=drafter)
        fields["approval"] = args.approval or preamble.get("approval")
        fields["review"] = args.review or preamble.get("review")
        entry = make_entry(**fields)
        entries.append(entry)
        write_revision_log(root, entries)
        if args.commit:
            entry["commit"] = commit_apply(root, args.revision, patch)
            write_revision_log(root, entries)
    except (ValueError, OSError):
        restore(root, saved)
        if copied:
            (root / patch).unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        raise
    marker.unlink(missing_ok=True)
    print(f"PASS: applied revision {args.revision} (kind {entry['kind']}) {entry['digest_after']}")
    return 0


def pin(args) -> int:
    """Append the pin entry for a run: informational; never closes an audit cycle."""
    root = Path(args.root)
    entries, live = check_chain(root)
    head = head_entry(entries)
    marker = take_marker(root, head["revision"])
    try:
        entries.append(make_entry(revision=head["revision"], kind="pin", digest_before=live,
                                  digest_after=live, run_id=args.run_id))
        write_revision_log(root, entries)
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
              f" ({len(entries)} revision_log entries) {digest}")
        return 0
    if args.pinned_revision is None or args.pinned_digest is None:
        raise ValueError("--pinned-revision and --pinned-digest go together")
    match = next((e for e in entries if e.get("kind") in BUNDLE_KINDS
                  and e.get("revision") == args.pinned_revision), None)
    if match is None:
        raise ValueError(f"the pin names revision {args.pinned_revision}; the revision_log records"
                         " no graph or run-setup entry for it")
    if match["digest_after"] != args.pinned_digest:
        raise ValueError(f"the pinned digest is not revision {args.pinned_revision} digest_after")
    newer = [e for e in entries if e.get("kind") in BUNDLE_KINDS
             and e["revision"] > args.pinned_revision]
    if not newer:
        print("PASS: current")
        return 0
    graphs = [e["revision"] for e in newer if e["kind"] == "graph"]
    if graphs:
        print(f"ROUTE: graph applied since pin (revision {max(graphs)})")
        return 3
    print(f"ROUTE: run setup applied since pin (revision {max(e['revision'] for e in newer)})")
    return 4


def rollback(args) -> int:
    root = Path(args.root)
    entries, digest_before = check_chain(root)
    head = head_entry(entries)
    target = next((e for e in entries if e.get("kind") in BUNDLE_KINDS
                   and e.get("revision") == args.to), None)
    if target is None:
        raise ValueError(f"the revision_log records no graph or run-setup revision {args.to}")
    undone = [e for e in entries if e.get("kind") in BUNDLE_KINDS and e["revision"] > args.to]
    if not undone:
        raise ValueError(f"revision {args.to} is already the pinned revision; nothing to undo")
    revision = head["revision"] + 1
    kind = "graph" if any(e["kind"] == "graph" for e in undone) else "run_setup"
    patch = f"history/v{revision}.patch"
    saved = snapshot(root)
    marker = take_marker(root, revision)
    scratch = Path(tempfile.mkdtemp(prefix="pave-rollback-"))
    try:
        for side in ("a", "b"):
            (scratch / side).mkdir()
        for name, data in saved.items():
            if name != REVISION_LOG:
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
            user_only_changes="approved", plan_evidence=target.get("plan_evidence"),
            usage_evidence=target.get("usage_evidence"), patch=patch, derived_from=args.to,
        )
        preamble = {field: fields.get(field) for field in PREAMBLE_FIELDS}
        preamble["changelog_entry"] = f"Roll back to revision {args.to}."
        (root / "history").mkdir(exist_ok=True)
        (root / patch).write_text(yaml.safe_dump(preamble, sort_keys=False) + forward)
        entries.append(make_entry(**fields))
        write_revision_log(root, entries)
    except (ValueError, OSError):
        restore(root, saved)
        (root / patch).unlink(missing_ok=True)
        marker.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    marker.unlink(missing_ok=True)
    print(f"PASS: applied revision {revision} (kind {kind}) back to revision {args.to}"
          f" {digest_after}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="write revisions.yaml with entry 0 beside a delivered graph")
    p.add_argument("root", help="the revision folder: holds workflow.pave.yaml and no revision log yet")
    p.add_argument("--plan-evidence", choices=["verified", "provisional"], required=True)
    p.add_argument("--approval", required=True, help="the approval that authorized delivery, verbatim")
    p.add_argument("--usage-evidence", choices=["none", "clean_room"], default="none")
    p.set_defaults(func=init)
    p = sub.add_parser("install", help="copy a package root into a project root and verify it")
    p.add_argument("root", help="a nonexistent or empty destination directory")
    p.add_argument("--from", dest="from_root", required=True, help="the package root to copy")
    p.set_defaults(func=install)
    p = sub.add_parser("propose", help="check a proposal against a root without touching the root:"
                       " exit 4 when a blocked path pattern matches a declared path")
    p.add_argument("root")
    p.add_argument("--patch", required=True, help="the proposal: YAML preamble then unified diff")
    p.set_defaults(func=propose)
    p = sub.add_parser("apply", help="apply history/vN.patch and append its revision log entry; exit 3"
                       f" when user_only_changes is {PENDING}")
    p.add_argument("root")
    p.add_argument("revision", type=int, help="N: the successor revision number")
    p.add_argument("--proposal", default=None,
                   help="a proposal file to copy to history/vN.patch before applying")
    p.add_argument("--hook-records", default=None,
                   help="the hook-written sidecar whose hook_recorded_writes decide written_by:"
                        " workflow-updater when --proposal path, size and mtime match; else unverified")
    p.add_argument("--approval", default=None, help="overrides the preamble's approval")
    p.add_argument("--review", default=None, help="the review verdict and rounds")
    p.add_argument("--commit", action="store_true", help="also git add and git commit the apply step")
    p.set_defaults(func=apply)
    p = sub.add_parser("pin", help=PIN_HELP, description=PIN_HELP)
    p.add_argument("root")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=pin)
    p = sub.add_parser("verify", help="check the live graph against the revision log, and a run's pin")
    p.add_argument("root")
    p.add_argument("--pinned-revision", type=int, default=None)
    p.add_argument("--pinned-digest", default=None)
    p.set_defaults(func=verify)
    p = sub.add_parser("rollback", help="reverse-apply down to revision N and apply the result")
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
        return getattr(error, "code", 1)


if __name__ == "__main__":
    sys.exit(main())
