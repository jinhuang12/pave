"""Every AMMO_* name read in scripts/, hooks/, codex/ has a row in references/campaign-env.md.

The scan matches the inventory grep of conventions §4: every `AMMO_[A-Z0-9_]+`
token in every text file under the scanned dirs. Set ENV_INVENTORY_DIRS (paths
joined by os.pathsep) to scan other dirs, for example the ammo-server source.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

R = Path(__file__).resolve().parents[1]
ENV_DOC = R / "references" / "campaign-env.md"
TOKEN = re.compile(r"AMMO_[A-Z0-9_]+")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def scan_dirs() -> list[Path]:
    raw = os.environ.get("ENV_INVENTORY_DIRS")
    if raw:
        return [Path(p) for p in raw.split(os.pathsep) if p]
    return [R / "scripts", R / "hooks", R / "codex"]


def read_names(dirs: list[Path]) -> dict[str, set[str]]:
    """name -> files that mention it."""
    found: dict[str, set[str]] = {}
    for base in dirs:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or SKIP_DIRS & set(path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for name in TOKEN.findall(text):
                found.setdefault(name, set()).add(str(path))
    return found


def table_rows() -> dict[str, list[str]]:
    """section heading -> first-column names of its table rows."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in ENV_DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current and line.startswith("|") and not re.match(r"^\|\s*-", line):
            first = line.strip().strip("|").split("|")[0]
            sections[current] += re.findall(r"`([A-Z][A-Z0-9_]+)`", first)
    return sections


def documented() -> set[str]:
    return {name for names in table_rows().values() for name in names}


def dropped() -> set[str]:
    return {n for title, names in table_rows().items() if title.lower().startswith("4.") for n in names}


def test_doc_has_the_four_sections():
    titles = [title.split(" ", 1)[0] for title in table_rows()]
    assert titles == ["1.", "2.", "3.", "4."], titles


def test_each_name_has_one_row():
    names = [n for rows in table_rows().values() for n in rows]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    assert not duplicates, duplicates


def test_scan_dirs_exist_and_find_names():
    dirs = scan_dirs()
    missing = [str(d) for d in dirs if not d.is_dir()]
    if missing and len(missing) == len(dirs):
        pytest.fail(f"no inventory dir exists: {missing}")
    assert read_names([d for d in dirs if d.is_dir()]), "the scan found no AMMO_* name"


def test_every_read_name_is_documented():
    found = read_names([d for d in scan_dirs() if d.is_dir()])
    missing = {name: sorted(paths)[:3] for name, paths in found.items() if name not in documented()}
    assert not missing, f"add a row to references/campaign-env.md for: {missing}"


def test_dropped_names_are_not_read():
    found = read_names([d for d in scan_dirs() if d.is_dir()])
    still_read = {name: sorted(found[name])[:3] for name in dropped() if name in found}
    assert not still_read, f"names listed as dropped are still read: {still_read}"
