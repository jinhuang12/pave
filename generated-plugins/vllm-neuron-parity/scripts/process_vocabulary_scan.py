#!/usr/bin/env python3
"""Scan a branch diff's added lines and added paths for process vocabulary.

`changeset_complete` component six: shipped source and tests carry none of
the run's own scheduling words. The word list lives here, as the NxDI import
pattern lives with its scan: the run's minted identifiers (increment and
block ids, decision and ruling references, round and attempt numbers) and
the workflow's process nouns (plan block, ruling, lease, grant, seat,
campaign). A word enters the list only when it is absent from the base's
product vocabulary at the pin; "lane" is a kernel term and is not here.

Usage:
  process_vocabulary_scan.py --repo DIR --base REV --tip REV [--json]
                             [--paths PATHSPEC ...]
  process_vocabulary_scan.py --list

Scope is the added lines of `git diff -U0 BASE TIP` plus the paths of files
the diff adds or renames, read as words so `test_campaign_pins.py` hits.
Context and removed lines never count, so upstream text is out of scope;
LICENSE, NOTICE and COPYING files are skipped because legal text says
"grant". Before the scan, a built-in control line runs through the same
patterns and every pattern must hit; a zero without a firing control is not
evidence. Exit 0 on zero hits, 1 on any hit, 2 when the control does not
fire or git fails. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

PATTERNS: tuple[str, ...] = (
    # the run's minted identifiers
    r"\binc-[a-z0-9]+-\d+\b",
    r"\bincrements? -?\d{2,}\b",
    r"\bblock -\d+\b",
    r"§\s?\d+",
    r"(?-i:\bDECISIONS(?:\.md)?\b)",
    r"(?-i:\bLEAD-LOG(?:\.md)?\b)",
    r"(?-i:\bD-[A-Z]{2,}[A-Z0-9-]*\b)",
    r"\bapprovals/",
    r"\bround \d+\b",
    r"\battempt \d+\b",
    r"\bgrant \d+\b",
    # the workflow's process nouns
    r"\bplan blocks?\b",
    r"\brulings?\b(?!\s+out\b)",
    r"\bleases?\b",
    r"\bgrants?\b",
    r"\bseats?\b",
    r"\bcampaigns?\b",
)

# Every pattern class fires on this line; the scan refuses to run otherwise.
CONTROL_LINE = (
    "plan block §12 of campaign inc-x-001 (increment 110, block -7): ruling 3, "
    "lease 4, grant 5, seat 6, round 2, attempt 9, DECISIONS LEAD-LOG "
    "D-ONE approvals/ file"
)


@dataclass
class Hit:
    path: str
    line: int  # 0 for a path hit
    pattern: str
    match: str
    text: str


def compile_patterns(patterns: tuple[str, ...] = PATTERNS) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def control_fires(regexes: list[re.Pattern[str]], line: str = CONTROL_LINE) -> int:
    """How many patterns hit the control line; every pattern must."""
    return sum(1 for rx in regexes if rx.search(line))


def scan_text(path: str, lines: list[tuple[int, str]], regexes: list[re.Pattern[str]]) -> list[Hit]:
    hits: list[Hit] = []
    for number, text in lines:
        for rx in regexes:
            m = rx.search(text)
            if m:
                hits.append(Hit(path, number, rx.pattern, m.group(0), text.strip()[:160]))
                break
    return hits


def is_license_text(path: str) -> bool:
    """License and notice files say "grant" as legal text, not as a process word."""
    name = path.rsplit("/", 1)[-1].upper()
    return name.startswith(("LICENSE", "NOTICE", "COPYING"))


def path_words(path: str) -> str:
    """A path as words: separators become spaces so word boundaries hold."""
    return re.sub(r"[_/.]+", " ", path)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    ).stdout


def added_lines(diff: str) -> dict[str, list[tuple[int, str]]]:
    """Added lines per path from a unified diff, numbered on the tip side."""
    out: dict[str, list[tuple[int, str]]] = {}
    path = ""
    line = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:]
            path = path[2:] if path.startswith("b/") else path
            out.setdefault(path, [])
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 1
        elif raw.startswith("+") and path:
            out[path].append((line, raw[1:]))
            line += 1
        elif raw.startswith(" "):
            line += 1
    return {p: ls for p, ls in out.items() if p != "/dev/null"}


def scan_repo(
    repo: str, base: str, tip: str, paths: list[str], regexes: list[re.Pattern[str]]
) -> dict:
    spec = ["--", *paths] if paths else []
    diff = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", base, tip, *spec)
    added = added_lines(diff)
    new_paths = [
        row.split("\t")[-1]
        for row in _git(repo, "diff", "--name-status", "--diff-filter=AR", base, tip, *spec).splitlines()
        if row
    ]
    hits: list[Hit] = []
    for p in new_paths:
        hits += [Hit(p, 0, h.pattern, h.match, p) for h in scan_text(p, [(0, path_words(p))], regexes)]
    for p, lines in added.items():
        if is_license_text(p):
            continue
        hits += scan_text(p, lines, regexes)
    dirty = [row for row in _git(repo, "status", "--porcelain").splitlines() if row]
    return {
        "base": _git(repo, "rev-parse", "--short", base).strip(),
        "tip": _git(repo, "rev-parse", "--short", tip).strip(),
        "added_lines": sum(len(v) for v in added.values()),
        "files": len(added),
        "added_paths": len(new_paths),
        "tree_dirty_entries": len(dirty),
        "hits": [asdict(h) for h in hits],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base")
    ap.add_argument("--tip", default="HEAD")
    ap.add_argument("--paths", nargs="*", default=[], help="git pathspecs to limit the diff")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true", help="print the word list and exit")
    args = ap.parse_args(argv)
    regexes = compile_patterns()
    if args.list:
        print("\n".join(PATTERNS))
        return 0
    if not args.base:
        ap.error("--base is required")
    fired = control_fires(regexes)
    if not regexes or fired != len(regexes):
        print(f"process-vocabulary scan: control did not fire ({fired} of {len(regexes)} patterns); refusing", file=sys.stderr)
        return 2
    try:
        result = scan_repo(args.repo, args.base, args.tip, args.paths, regexes)
    except subprocess.CalledProcessError as exc:
        print(f"process-vocabulary scan: git failed: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    result["control_fired"] = f"{fired} of {len(regexes)} patterns"
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        for h in result["hits"]:
            where = f"{h['path']}:{h['line']}" if h["line"] else f"{h['path']} (path)"
            print(f"{where}: [{h['match']}] {h['text']}")
        print(
            f"process-vocabulary scan: {len(result['hits'])} hits over {result['added_lines']} added lines "
            f"in {result['files']} files and {result['added_paths']} added paths, {result['base']}..{result['tip']}, "
            f"tree dirty entries {result['tree_dirty_entries']}, control fired ({result['control_fired']})"
        )
    return 1 if result["hits"] else 0


if __name__ == "__main__":
    sys.exit(main())
