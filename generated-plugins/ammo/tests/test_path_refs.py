"""Every scripts/x, references/x, schemas/x, orchestration/x, data/x token resolves (conventions §9).

Scope: every .md, .toml, and hooks.json file under the plugin.
A token is a plugin reference when it starts the path, or follows
${CLAUDE_PLUGIN_ROOT}/, ./ or ../, or follows a plugin top-level dir (codex/,
tests/, skills/, ...). It resolves against the plugin root; a ./ or ../ token
resolves against the file's own dir. A token inside some other tree
(<engine>/scripts/x, src/pumice/data/x, a URL) is not a plugin reference.
Placeholders (<name>, {a,b}, *) resolve when one file matches the pattern.
A token can name a file or a dir.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

R = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
SEGMENT = r"(?:scripts|references|schemas|orchestration|data)"
PART = r"[\w.*<>{}$-]+"
TOKEN = re.compile(
    r"(?<![\w.${}<>~*/:@-])"
    rf"(?P<prefix>(?:{PART}/)*?)"
    rf"(?P<ref>{SEGMENT}/{PART}(?:/{PART})*)"
)
ROOT_VARS = ("${CLAUDE_PLUGIN_ROOT}/", "$CLAUDE_PLUGIN_ROOT/")
PLACEHOLDER = re.compile(r"<[^>/]*>|\{[^}/]*\}|\$\{?\w+\}?")


def scanned_files() -> list[Path]:
    files = []
    for path in sorted(R.rglob("*")):
        rel = path.relative_to(R)
        if not path.is_file() or SKIP_DIRS & set(rel.parts):
            continue
        if path.suffix in (".md", ".toml") or path.name == "hooks.json":
            files.append(path)
    return files


def resolves(base: Path, ref: str) -> bool:
    ref = ref.rstrip(".,;:")
    if not ref:
        return True
    pattern = PLACEHOLDER.sub("*", ref)
    if "*" in pattern:
        return any(base.glob(pattern))
    return (base / ref).exists()


def unresolved(path: Path) -> list[str]:
    top = {p.name for p in R.iterdir()}
    bad = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        for match in TOKEN.finditer(line):
            prefix, ref = match.group("prefix"), match.group("ref")
            for var in ROOT_VARS:
                if var in prefix:
                    prefix = prefix.split(var, 1)[1]
            if prefix.startswith(("./", "../")):
                ok = resolves(path.parent, prefix + ref)
            elif not prefix:
                ok = resolves(R, ref)
            elif prefix.split("/", 1)[0] in top:
                ok = resolves(R, prefix + ref)
            else:
                continue
            if not ok:
                bad.append(f"{number}: {prefix}{ref}")
    return bad


FILES = scanned_files()


def test_scope_is_not_empty():
    assert FILES, "no .md, .toml, or hooks.json file under the plugin"


@pytest.mark.parametrize("path", FILES, ids=[p.relative_to(R).as_posix() for p in FILES])
def test_path_tokens_resolve(path):
    bad = unresolved(path)
    assert not bad, f"{len(bad)} unresolved: {bad[:15]}"
