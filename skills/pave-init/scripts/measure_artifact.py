#!/usr/bin/env python3
"""Measure a living document against its document-budget cap.

`references/pave-spec.md` section 8.4: each living document declares a cap —
default 400 lines and 60 KB, both. This script is the one instrument that
reports a document's size, so seats, reviewers, hooks, and tests all read the
same numbers instead of each counting their own way.

Usage:
  measure_artifact.py PATH [PATH ...] [--cap-lines N] [--cap-bytes N]
                      [--baseline PATH] [--json] [--strict]

One line per document: lines, bytes, cap, OVER CAP when it is, the longest
line, the H2 section count, and the narration-marker count (revision
references, "previously", "superseded", "until now", DISCLOSED — words that
mark history kept inline instead of at its evidence path). `--baseline`
adds the delta against another copy. `--json` prints the full record.
`--strict` exits 1 when any document is over its cap, for hooks and CI.

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
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
        "sha256": hashlib.sha256(data).hexdigest(),
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
    """One plain line a reviewer can paste into a round report."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="+", help="documents to measure")
    parser.add_argument("--cap-lines", type=int, default=DEFAULT_CAP_LINES)
    parser.add_argument("--cap-bytes", type=int, default=DEFAULT_CAP_BYTES)
    parser.add_argument("--baseline", help="another copy to report the delta against")
    parser.add_argument("--json", action="store_true", help="print the full record as JSON")
    parser.add_argument("--strict", action="store_true", help="exit 1 when any document is over its cap")
    args = parser.parse_args(argv)

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
