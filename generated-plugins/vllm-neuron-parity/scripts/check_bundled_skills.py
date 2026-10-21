#!/usr/bin/env python3
"""Run-start presence check for the Neuron skills bundled in this plugin.

A campaign seat loads these skills by their plugin address
(vllm-neuron-parity:<name>). If one is missing or was edited in place, the
seat reads different guidance from the one this release shipped. This check
runs once at verify_run_preconditions, before any other work.

It checks two things against the md5 list that ships with the plugin:
  1. Every skill in BUNDLED_SKILLS has its SKILL.md listed and present.
  2. Every file the list names under those skills is present and its md5
     matches.
Files that are present but not listed (for example __pycache__ directories,
or sweep_results.json, which the planner CLI writes beside its scripts) do not
fail the check.

Output: one line per problem, then exit 1; or one "ok" line, then exit 0.
Standard library only.

Usage:
    python3 scripts/check_bundled_skills.py [PLUGIN_ROOT] [--md5-file PATH]

PLUGIN_ROOT defaults to the parent of this script's directory. The md5 file
defaults to PLUGIN_ROOT/skills_md5.txt, in `md5sum -c` format with paths
relative to PLUGIN_ROOT.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import sys

BUNDLED_SKILLS = (
    "neuron-parallelism-planner",
    "neuron-nki-writing",
    "neuron-nki-docs",
    "neuron-nki-debugging",
    "experimental-neuron-autoport-compiler-debugging-vllm-neuron",
    "neuron-framework-autoport-vllm-neuron",
    "neuron-framework-equivalence",
    "experimental-neuron-framework-benchmark-vllm",
    "experimental-neuron-infra-runtime-troubleshooting",
    "experimental-neuron-perf-compiler-artifact-debugging",
    "neuron-nki-profiling",
    "neuron-nki-profile-querying",
    "neuron-framework-profiling-vllm-neuron",
    "neuron-framework-profile-analysis-vllm-neuron",
    "experimental-neuron-framework-optimization",
)

# md5sum output: 32 hex digits, a space, then " " (text) or "*" (binary), then the path.
LINE = re.compile(r"^([0-9a-f]{32}) [ *](.+)$")


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_list(md5_file: Path, problems: list[str]) -> dict[str, str]:
    """Return {relative path: md5}; append a problem line for each bad entry."""
    try:
        text = md5_file.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"missing: {md5_file} ({exc.strerror})")
        return {}
    listed: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = LINE.match(line)
        if not match:
            problems.append(f"malformed: {md5_file.name} line {number}")
            continue
        digest, rel = match.groups()
        parts = PurePosixPath(rel).parts
        if rel.startswith("/") or ".." in parts or len(parts) < 3 or parts[0] != "skills":
            problems.append(f"unsafe or foreign path: {md5_file.name} line {number}: {rel}")
            continue
        if parts[1] not in BUNDLED_SKILLS:
            problems.append(f"unknown skill: {md5_file.name} line {number}: {rel}")
            continue
        listed[rel] = digest
    return listed


def check(root: Path, md5_file: Path) -> list[str]:
    problems: list[str] = []
    listed = read_list(md5_file, problems)
    if not listed and problems:
        return problems
    for name in BUNDLED_SKILLS:
        skill_md = f"skills/{name}/SKILL.md"
        if skill_md not in listed:
            problems.append(f"unlisted: {skill_md}")
        if not (root / skill_md).is_file():
            problems.append(f"missing: {skill_md}")
    for rel, expected in sorted(listed.items()):
        path = root / rel
        if not path.is_file():
            if not rel.endswith("/SKILL.md"):  # a missing SKILL.md is already reported
                problems.append(f"missing: {rel}")
            continue
        if file_md5(path) != expected:
            problems.append(f"changed: {rel}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", type=Path,
                        default=Path(__file__).resolve().parent.parent,
                        help="plugin root (default: parent of scripts/)")
    parser.add_argument("--md5-file", type=Path, default=None,
                        help="md5 list (default: ROOT/skills_md5.txt)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    md5_file = args.md5_file or root / "skills_md5.txt"
    problems = check(root, md5_file)
    for line in problems:
        print(line)
    if problems:
        print(f"FAIL: {len(problems)} problem(s) under {root}")
        return 1
    count = len(read_list(md5_file, []))
    print(f"ok: {len(BUNDLED_SKILLS)} skills, {count} files match {md5_file.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
