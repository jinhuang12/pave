#!/usr/bin/env python3
"""Measure a living document against its declared cap.

`references/artifact-layout.md` section 4.12: each living document declares a
cap — default 400 lines and 60 KB, both. This script is the one check_tool
that reports a document's size, so seats, the reviewer, the hook, and tests
all read the same numbers instead of each counting their own way.

Usage:
  measure_artifact.py PATH [PATH ...] [--cap-lines N] [--cap-bytes N]
                      [--baseline PATH] [--json] [--strict]
  measure_artifact.py --classify PATH [PATH ...] [--json]
  measure_artifact.py --tree DIR [--since ISO-DATE] [--json]

One line per document: lines, bytes, cap, OVER CAP when it is, the longest
line, the H2 section count, and the narration-marker count (revision
references, "previously", "superseded", "until now", DISCLOSED — words that
mark history kept inline instead of at its evidence path). `--baseline`
adds the delta against another copy. `--json` prints the full record.
`--strict` exits 1 when any document is over its cap, for hooks and CI.

`--classify` reads source files (.py, .sh) and splits their lines into
code, comment, docstring, and blank, plus the leading header block — the
batch review records these for a changeset's added files (section 4.12,
increments row). `--tree` walks one working-state directory such as
`increments/` and reports its files, bytes, round-suffixed names (-rN),
superseded revisions (N below the stem's max), and header lines; `--since`
limits it to files modified on or after a date, for "since the last batch".

Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import statistics
import sys
import tokenize
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CAP_LINES = 400
DEFAULT_CAP_BYTES = 60 * 1024

NARRATION_PATTERNS = (
    ("revision_ref", re.compile(r"\b(?:rev(?:ision)?\s*\d+|r\d{2,})\b", re.IGNORECASE)),
    ("disclosed", re.compile(r"\bDISCLOSED\b")),
    ("until_now", re.compile(r"\buntil now\b", re.IGNORECASE)),
    ("previously", re.compile(r"\bpreviously\b", re.IGNORECASE)),
    ("superseded", re.compile(r"\bsuperseded\b", re.IGNORECASE)),
)


def measure(
    path: str | Path,
    cap_lines: int = DEFAULT_CAP_LINES,
    cap_bytes: int = DEFAULT_CAP_BYTES,
    baseline: str | Path | None = None,
) -> dict:
    """Return the size record for one document."""
    data = Path(path).read_bytes()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()

    sections: list[dict] = []
    current: dict | None = None
    for number, line in enumerate(lines, 1):
        if line.startswith("## "):
            current = {"heading": line[3:].strip(), "start_line": number, "lines": 0, "bytes": 0}
            sections.append(current)
        if current is not None:
            current["lines"] += 1
            current["bytes"] += len(line.encode("utf-8")) + 1

    result = {
        "path": str(path),
        "lines": len(lines),
        "bytes": len(data),
        "longest_line": max((len(line) for line in lines), default=0),
        "cap_lines": cap_lines,
        "cap_bytes": cap_bytes,
        "over_cap": len(lines) > cap_lines or len(data) > cap_bytes,
        "sections": sections,
        "narration_markers": {name: len(pattern.findall(text)) for name, pattern in NARRATION_PATTERNS},
    }
    if baseline is not None:
        base = measure(baseline, cap_lines, cap_bytes)
        result["baseline"] = {
            "path": str(baseline),
            "lines": base["lines"],
            "bytes": base["bytes"],
            "delta_lines": result["lines"] - base["lines"],
            "delta_bytes": result["bytes"] - base["bytes"],
        }
    return result


def render(result: dict) -> str:
    """One plain line a reviewer can paste into a round's findings record."""
    flag = " OVER CAP" if result["over_cap"] else ""
    line = (
        f"{result['path']}: {result['lines']} lines, {result['bytes']} bytes "
        f"(cap {result['cap_lines']} lines / {result['cap_bytes']} bytes){flag}; "
        f"longest line {result['longest_line']}; sections {len(result['sections'])}; "
        f"narration markers {sum(result['narration_markers'].values())}"
    )
    if "baseline" in result:
        base = result["baseline"]
        line += f"; vs baseline {base['delta_lines']:+d} lines / {base['delta_bytes']:+d} bytes"
    return line


RETRY_SUFFIX = re.compile(r"^(?P<stem>.*?)-r(?P<n>\d+)[a-z]?(?=[-.]|$)(?P<rest>.*)$")


def header_lines(path: str | Path) -> int:
    """Lines of the leading shebang, comment, blank, and docstring block before code.

    Same rule the write-for-reader hook applies to increments/ writes, so a
    seat, the hook, and the batch review count a header the same way.
    """
    ext = Path(path).suffix.lower()
    count = 0
    in_doc = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                s = line.strip()
                if in_doc:
                    count += 1
                    if in_doc in s:
                        in_doc = None
                    continue
                if not s or s.startswith("#"):
                    count += 1
                    continue
                opening = re.match(r"[rRbBuU]{0,2}(\"\"\"|''')", s) if ext == ".py" else None
                if opening:
                    count += 1
                    quote = opening.group(1)
                    if quote not in s[opening.end():]:
                        in_doc = quote
                    continue
                break
    except OSError:
        return 0
    return count


def _docstring_lines(src: str) -> set[int]:
    lines: set[int] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                and isinstance(first.value.value, str):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def classify(path: str | Path) -> dict:
    """Split one source file's lines into code, comment, docstring, and blank.

    .py files use the tokenizer and the AST (a docstring is the leading string
    of a module, class, or function); .sh and other files count `#` lines as
    comments. A code line carrying a trailing comment counts as code.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    src_lines = text.splitlines()
    total = len(src_lines)
    ext = Path(path).suffix.lower()
    counts = {"code": 0, "comment": 0, "docstring": 0, "blank": 0}
    if ext == ".py":
        doc = _docstring_lines(text)
        comment_lines: set[int] = set()
        code_lines: set[int] = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type == tokenize.COMMENT:
                    comment_lines.add(tok.start[0])
                elif tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                                  tokenize.ENDMARKER, tokenize.ENCODING):
                    continue
                else:
                    code_lines.update(range(tok.start[0], tok.end[0] + 1))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
        for number, line in enumerate(src_lines, 1):
            if number in doc:
                counts["docstring"] += 1
            elif number in comment_lines and number not in code_lines:
                counts["comment"] += 1
            elif not line.strip():
                counts["blank"] += 1
            else:
                counts["code"] += 1
    else:
        for line in src_lines:
            s = line.strip()
            if not s:
                counts["blank"] += 1
            elif s.startswith("#"):
                counts["comment"] += 1
            else:
                counts["code"] += 1
    prose = counts["comment"] + counts["docstring"]
    return {
        "path": str(path),
        "lines": total,
        **counts,
        "prose_share": round(prose / total, 3) if total else 0.0,
        "header_lines": header_lines(path),
    }


def render_classify(result: dict) -> str:
    return (
        f"{result['path']}: {result['lines']} lines; code {result['code']}, comment {result['comment']}, "
        f"docstring {result['docstring']}, blank {result['blank']}; prose share {result['prose_share']:.0%}; "
        f"header {result['header_lines']} lines"
    )


def tree(directory: str | Path, since: str | None = None) -> dict:
    """Write report of one working-state directory: files, bytes, round suffixes, headers."""
    root = Path(directory)
    cutoff = None
    if since:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        cutoff = parsed.timestamp()
    files = [p for p in root.iterdir() if p.is_file()] if root.is_dir() else []
    if cutoff is not None:
        files = [p for p in files if p.stat().st_mtime >= cutoff]
    groups: dict[tuple[str, str], list[int]] = {}
    round_suffixed = 0
    for p in files:
        match = RETRY_SUFFIX.match(p.name)
        if match:
            round_suffixed += 1
            groups.setdefault((match.group("stem"), match.group("rest")), []).append(int(match.group("n")))
    superseded = sum(len(ns) - 1 for ns in groups.values() if len(ns) > 1)
    headers = [header_lines(p) for p in files if p.suffix.lower() in (".py", ".sh")]
    by_ext: dict[str, int] = {}
    for p in files:
        by_ext[p.suffix.lower() or "(none)"] = by_ext.get(p.suffix.lower() or "(none)", 0) + 1
    return {
        "path": str(root),
        "since": since,
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "round_suffixed": round_suffixed,
        "superseded": superseded,
        "header_lines_total": sum(headers),
        "header_lines_median": statistics.median(headers) if headers else 0,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
    }


def render_tree(result: dict) -> str:
    since = f" since {result['since']}" if result["since"] else ""
    return (
        f"{result['path']}{since}: {result['files']} files, {result['bytes']} bytes; "
        f"{result['round_suffixed']} round-suffixed names, {result['superseded']} superseded revisions; "
        f"script headers {result['header_lines_total']} lines total, median {result['header_lines_median']:g}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="documents to measure (or source files with --classify, one directory with --tree)")
    parser.add_argument("--cap-lines", type=int, default=DEFAULT_CAP_LINES)
    parser.add_argument("--cap-bytes", type=int, default=DEFAULT_CAP_BYTES)
    parser.add_argument("--baseline", help="another copy to report the delta against")
    parser.add_argument("--json", action="store_true", help="print the full record as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any document is over its cap")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--classify", action="store_true", help="split source files into code, comment, docstring, blank")
    mode.add_argument("--tree", action="store_true", help="write_report one working-state directory")
    parser.add_argument("--since", help="with --tree: only files modified on or after this ISO date")
    args = parser.parse_args(argv)

    if args.tree:
        if len(args.paths) != 1:
            parser.error("--tree takes exactly one directory")
        if args.since:
            try:
                datetime.fromisoformat(args.since.replace("Z", "+00:00"))
            except ValueError:
                parser.error(f"--since takes an ISO date (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ), got {args.since!r}")
        result = tree(args.paths[0], args.since)
        print(json.dumps(result, indent=2) if args.json else render_tree(result))
        return 0
    if args.classify:
        results = [classify(path) for path in args.paths]
        if args.json:
            print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
        else:
            for result in results:
                print(render_classify(result))
        return 0

    results = [measure(path, args.cap_lines, args.cap_bytes, args.baseline) for path in args.paths]
    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2))
    else:
        for result in results:
            print(render(result))
    if args.strict and any(result["over_cap"] for result in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
