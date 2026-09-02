#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "skills" / "pave-init" / "VERSION"
README_PATH = ROOT / "skills" / "pave-init" / "README.md"
PLUGIN_MANIFEST_PATH = ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_PATH = ROOT / ".claude-plugin" / "marketplace.json"
JSON_PATHS = (
    PLUGIN_MANIFEST_PATH,
    ROOT / ".codex-plugin" / "plugin.json",
    MARKETPLACE_PATH,
)
VERSION_LINE = re.compile(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$")
README_LINE = re.compile(r"^Version: `[^`]+`$", re.MULTILINE)


class StampError(RuntimeError):
    pass


def authoritative_version() -> str:
    first = VERSION_PATH.read_text(encoding="utf-8").splitlines()[0]
    match = VERSION_LINE.fullmatch(first)
    if not match:
        raise StampError(f"invalid version authority: {VERSION_PATH}")
    return match.group(1)


def plugin_name() -> str:
    data = json.loads(PLUGIN_MANIFEST_PATH.read_text(encoding="utf-8"))
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        raise StampError(f"missing plugin name: {PLUGIN_MANIFEST_PATH}")
    return name


def stamp_marketplace(data: dict, name: str, version: str, path: Path) -> None:
    # The marketplace also lists generated plugins that own their own
    # versions, so stamp only the entry named after this plugin manifest.
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise StampError(f"expected a marketplace plugin list: {path}")
    matches = [
        plugin
        for plugin in plugins
        if isinstance(plugin, dict) and plugin.get("name") == name
    ]
    if len(matches) != 1:
        raise StampError(
            f"expected one marketplace entry named {name!r}, "
            f"found {len(matches)}: {path}"
        )
    matches[0]["version"] = version


def expected_files(version: str) -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    name = plugin_name()
    for path in JSON_PATHS:
        data = json.loads(path.read_text(encoding="utf-8"))
        if path == MARKETPLACE_PATH:
            stamp_marketplace(data, name, version, path)
        else:
            data["version"] = version
        outputs[path] = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    readme = README_PATH.read_text(encoding="utf-8")
    if len(README_LINE.findall(readme)) != 1:
        raise StampError(f"expected one README version line: {README_PATH}")
    outputs[README_PATH] = README_LINE.sub(f"Version: `{version}`", readme)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync PAVE Init release stamps")
    parser.add_argument("--check", action="store_true", help="fail on stale stamps")
    args = parser.parse_args()
    try:
        outputs = expected_files(authoritative_version())
        stale = [path for path, text in outputs.items() if path.read_text() != text]
        if args.check:
            for path in stale:
                print(f"STALE {path.relative_to(ROOT)}", file=sys.stderr)
            return 1 if stale else 0
        for path in stale:
            path.write_text(outputs[path], encoding="utf-8")
            print(path.relative_to(ROOT))
        return 0
    except (OSError, ValueError, TypeError, StampError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
