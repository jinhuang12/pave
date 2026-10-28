"""Banned tokens anywhere under the plugin (conventions §9 and §6).

Every token below is built from parts, so this file does not hold one.
Exceptions, exactly as the conventions state:
- The em dash is banned in `.md` files only.
- The four byte-identical PAVE tool copies (PAVE_COPIES) are exempt from every
  ban: they must stay `cmp`-identical to their source (ruled 2026-10-28).
- Table rows (lines that start with `|`) of `hooks/README.md` and `README.md`
  may hold the non-pin tokens: those are the migration tables.
- Pin literals may appear only in `scripts/pumice_pins.py` and
  `scripts/pumice_pb/README.md` (and in planning-records/, outside the plugin).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

R = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
SKIP_NAMES = {".DS_Store"}

# Conventions §9: plain tokens, matched as substrings, case-sensitive.
TOKENS = {
    "server terminal": "tt" "yd",
    "web UI": "LIGHT" "GRID",
    "multi-pod": "cross" "-pod",
    "session store": "/data" "/sessions",
    "image venv": "/opt" "/pumice-venv",
    "neuron venv": "/opt" "/neuron-venv",
    "image share dir": "/usr/local/share" "/ammo",
    "server shared dir": "/app" "/shared",
    "container workspace": "/work" "space/",
    "old campaign root": "kernel_opt" "_artifacts",
    "claude skill dir": ".claude/skills" "/ammo",
    "codex skill dir": ".codex/skills" "/ammo",
    "claude schemas dir": ".claude" "/schemas",
    "claude agents dir": ".claude" "/agents",
    "em dash": "\u2014",
}
MIGRATION_TABLE_FILES = {"hooks/README.md", "README.md"}
MD_ONLY = {"em dash"}
PAVE_COPIES = {
    "scripts/validate_pave.py",
    "scripts/record_revision.py",
    "references/pave.schema.json",
    "references/pave-composition.schema.json",
}

# Conventions §6: pin literals of the old and new Pumice pins, the vLLM pins,
# and the torch pin (both Pumice pins require the same torch).
PINS = {
    "old pumice commit": re.escape("9d12" "6713"),
    "new pumice commit": re.escape("769c" "7529"),
    "old vllm pin": r"(?<![\d.])0\.30\.0(?!\d)",
    "new vllm pin": r"(?<![\d.])0\.32\.0(?!\d)",
    "torch pin": r"(?<![\d.])2\.13\.0(?!\d)",
}
PIN_FILES = {"scripts/pumice_pins.py", "scripts/pumice_pb/README.md"}


def text_files():
    for path in sorted(R.rglob("*")):
        rel = path.relative_to(R)
        if not path.is_file() or SKIP_DIRS & set(rel.parts) or path.name in SKIP_NAMES:
            continue
        try:
            yield rel.as_posix(), path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


FILES = list(text_files())


def flagged(rel: str, line: str, label: str) -> bool:
    """True when this line of the file at plugin-relative path `rel` breaks rule `label`."""
    if rel in PAVE_COPIES:
        return False
    if label in PINS:
        return bool(re.search(PINS[label], line)) and rel not in PIN_FILES
    if TOKENS[label] not in line:
        return False
    if label in MD_ONLY and not rel.endswith(".md"):
        return False
    return not (rel in MIGRATION_TABLE_FILES and line.lstrip().startswith("|"))


def hits(label: str) -> list[str]:
    return [
        f"{rel}:{number}"
        for rel, text in FILES
        for number, line in enumerate(text.splitlines(), 1)
        if flagged(rel, line, label)
    ]


@pytest.mark.parametrize("label", sorted(TOKENS))
def test_no_banned_token(label):
    found = hits(label)
    assert not found, f"{len(found)} lines hold the {label} token, first: {found[:10]}"


@pytest.mark.parametrize("label", sorted(PINS))
def test_pin_literal_only_in_pin_files(label):
    found = hits(label)
    assert not found, f"{len(found)} lines hold the {label}, first: {found[:10]}"


def test_this_file_holds_no_banned_token():
    me = Path(__file__).read_text(encoding="utf-8")
    assert not [t for t in TOKENS.values() if t in me]
    assert not [p for p in PINS.values() if re.search(p, me)]


# --------------------------------------------------------------------------- the rules themselves

EM = TOKENS["em dash"]
WS = TOKENS["container workspace"]
OLD_VLLM = "0." "30.0"


def test_rule_em_dash_applies_to_md_only():
    assert flagged("references/guide.md", f"a {EM} b", "em dash")
    assert not flagged("scripts/tool.py", f"# a {EM} b", "em dash")
    assert not flagged("hooks/x.sh", f"# a {EM} b", "em dash")


def test_rule_other_tokens_apply_to_every_file_type():
    assert flagged("scripts/tool.py", f"p = '{WS}x'", "container workspace")
    assert flagged("scripts/tool.py", f"v = '{OLD_VLLM}'", "old vllm pin")


@pytest.mark.parametrize("rel", sorted(PAVE_COPIES))
def test_rule_pave_tool_copies_are_exempt(rel):
    assert not flagged(rel, f"a {EM} b {WS}", "em dash")
    assert not flagged(rel, f"{WS}x", "container workspace")
    assert not flagged(rel, OLD_VLLM, "old vllm pin")


def test_rule_migration_table_rows_only():
    assert not flagged("README.md", f"| old | {WS}x |", "container workspace")
    assert flagged("README.md", f"see {WS}x", "container workspace")
    assert flagged("README.md", f"| pin | {OLD_VLLM} |", "old vllm pin")


def test_rule_pin_files_may_hold_pins():
    assert not flagged("scripts/pumice_pins.py", f'REQUIRED = "{OLD_VLLM}"', "old vllm pin")
    assert flagged("scripts/pumice_pins.py", f"x = '{WS}'", "container workspace")
