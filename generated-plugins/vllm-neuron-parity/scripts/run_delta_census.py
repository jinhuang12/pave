#!/usr/bin/env python3
"""Census of what a run wrote since its last audit checkpoint. Read-only.

Inputs: --state <run-state.json> (reads it, its sidecar <state>.audit-checkpoint.json
for `outcomes_at` and `at`, and the write log <state>.write-log.jsonl plus the one
rotated generation <state>.write-log.1.jsonl); --evolution-root <dir> (node ids from
its workflow.pave.yaml unless --graph names another); optional --memory-dir, --log
(a lead or decision log for cited-by tallies), --decisions (creating-section excerpts), --json,
--since-checkpoint. The caller names the workflow's declared decision record with
--decisions (and a separate log with --log; cited-by falls back to the decision
record); with neither, every act is unattributed and the summary says so. The paths
used appear under `inputs`.
A write-log record carrying `written: false` (the hook stat'ed the path and no write
landed) is reported as files_since.read_only and counted nowhere else; a record
without the field is a write. The cited-by and creating-section scans cover the top
40 families by count, so a log with hundreds of families stays fast. lap_suffixed
counts every `-rN` re-cut however the name continues; lap_suffixed_strict counts only
the `<stem>-rN.<ext>` form the runtime no-recut guard matches. Scans only directories
present in the write log plus the run workspace root (parent of the state file's
directory); never a bare /tmp or $TMPDIR. Writes nothing, hashes nothing; sizes come from os.stat. Stdlib only. Exit 0.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

SIDECAR_SUFFIX = ".audit-checkpoint.json"
WRITE_LOG_SUFFIX = ".write-log.jsonl"
WRITE_LOG_ROTATED_SUFFIX = ".write-log.1.jsonl"
ROOT_GRAPH = "workflow.pave.yaml"
NOTE_CAP = 500
CITE_RE = re.compile(r"\b(caught|found|defect|red)\b", re.IGNORECASE)
CITE_WINDOW = 3
LAP_RE = re.compile(r"-r\d+(?=[-.]|$)")
LAP_STRICT_RE = re.compile(r"-r\d+\.[A-Za-z0-9]+$")
NODE_ITEM_RE = re.compile(r"^\s*-\s*id:\s*(\S+)")
SEQ_TOKEN = re.compile(r"\d+[a-z]?")
EXCERPT_CAP = 200
EXCERPT_LINES = 8
TOP_FAMILIES = 40
BARE_TMP = {"/tmp", "/private/tmp", "/var/tmp", "/"}


# --------------------------------------------------------------------------- io
def read_json(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_lines(path: Path | None) -> list[str]:
    if not path or not path.is_file():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def parse_at(value) -> float | None:
    """ISO-8601 'Z' or offset stamp -> epoch seconds; None when unreadable."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_write_log(state: Path) -> list[dict]:
    """Rotated generation first, then the live file; malformed lines skipped."""
    records: list[dict] = []
    base = str(state)
    for path in (Path(base + WRITE_LOG_ROTATED_SUFFIX), Path(base + WRITE_LOG_SUFFIX)):
        if not path.is_file():
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and isinstance(rec.get("path"), str):
                        records.append(rec)
        except OSError:
            continue
    return records


# ------------------------------------------------------------------- declared nodes
def declared_nodes(graph: Path) -> tuple[list[str], str]:
    """Node ids of the graph. PyYAML when importable, else a line scanner over the
    `nodes:` block that accepts both the mapping form (keys) and `- id:` items."""
    if not graph.is_file():
        return [], "missing"
    try:
        import yaml  # type: ignore

        with graph.open(encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        nodes = (doc or {}).get("pave", doc or {}).get("nodes")
        if isinstance(nodes, dict):
            return [str(k) for k in nodes], "pyyaml"
        if isinstance(nodes, list):
            return [str(n.get("id")) for n in nodes if isinstance(n, dict) and n.get("id")], "pyyaml"
        return [], "pyyaml"
    except Exception:  # noqa: BLE001 - fall back to the regex reader
        pass
    ids: list[str] = []
    block_indent = None
    key_indent = None
    list_form = False
    for raw in read_lines(graph):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        is_item = raw.lstrip().startswith("-")
        if block_indent is None:
            if raw.strip() == "nodes:":
                block_indent = indent
            continue
        if indent < block_indent or (indent == block_indent and not is_item):
            break
        m = NODE_ITEM_RE.match(raw)
        if m and (key_indent is None or indent <= key_indent):
            list_form = True
            ids.append(m.group(1).strip("'\""))
            continue
        if list_form:
            continue
        if key_indent is None:
            key_indent = indent
        if indent == key_indent and raw.rstrip().endswith(":") and not is_item:
            ids.append(raw.strip()[:-1].strip("'\""))
    return ids, "regex"


# ------------------------------------------------------------------- helpers
def family_of(path: str) -> str:
    """The first basename token that is not a bare sequence number.

    Real runs number their artifacts (`039-grant-trn2.md`, `118-plan-block.md`), so
    the leading token counts files rather than naming a family; skip every leading
    counter token and take the next one. A name with nothing after its number
    (`007.txt`) keeps the number as its family.
    """
    base = os.path.basename(path.rstrip("/"))
    stem = base
    if "." in stem[1:]:
        stem = stem[: stem[1:].index(".") + 1]
    tokens = stem.split("-")
    for index, token in enumerate(tokens):
        if index == len(tokens) - 1 or not SEQ_TOKEN.fullmatch(token):
            return token or base
    return stem or base


def counted(rec: dict) -> bool:
    """False only when the write log says the call wrote nothing (`written: false`)."""
    return rec.get("written") is not False


def is_lap_suffixed(path: str) -> bool:
    """A re-cut name: `-rN` closed by `-`, `.` or the end of the basename, so a hash or
    word after the lap (`launch-113-r5-a5b82c73.sh`, `plan-block-r2-final.md`) counts."""
    return bool(LAP_RE.search(os.path.basename(path)))


def is_lap_strict(path: str) -> bool:
    """The `<stem>-rN.<ext>` form the runtime no-recut guard matches, and nothing else."""
    return bool(LAP_STRICT_RE.search(os.path.basename(path)))


def under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def mention_re(family: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(family) + r"(?![A-Za-z0-9_])")


def cited_by(lines: list[str], families: list[str]) -> dict[str, int]:
    """Lines within CITE_WINDOW of a caught|found|defect|red line that mention the family."""
    if not lines or not families:
        return {}
    hot = [False] * len(lines)
    for i, line in enumerate(lines):
        if CITE_RE.search(line):
            lo, hi = max(0, i - CITE_WINDOW), min(len(lines), i + CITE_WINDOW + 1)
            for j in range(lo, hi):
                hot[j] = True
    hot_lines = [line for line, on in zip(lines, hot) if on]
    out: dict[str, int] = {}
    for fam in families:
        pat = mention_re(fam)
        n = sum(1 for line in hot_lines if pat.search(line))
        if n:
            out[fam] = n
    return out


def creating_sections(lines: list[str], families: list[str]) -> dict[str, list[str]]:
    """First `## ` section mentioning each family: header + its first 3 non-blank lines."""
    if not lines or not families:
        return {}
    sections: list[tuple[str, list[str]]] = []
    header, body = None, []
    for line in lines:
        if line.startswith("## "):
            if header is not None:
                sections.append((header, body))
            header, body = line.rstrip(), []
        elif header is not None:
            body.append(line)
    if header is not None:
        sections.append((header, body))
    out: dict[str, list[str]] = {}
    for fam in families:
        pat = mention_re(fam)
        for head, text in sections:
            if pat.search(head) or any(pat.search(t) for t in text):
                first = [t.rstrip() for t in text if t.strip()][:3]
                out[fam] = [head] + first
                break
    return out


def memory_changed(memory_dir: Path | None, since: float | None) -> list[str]:
    if not memory_dir or not memory_dir.is_dir():
        return []
    changed = []
    for dirpath, _dirs, files in os.walk(memory_dir):
        for name in files:
            p = os.path.join(dirpath, name)
            try:
                mtime = os.stat(p).st_mtime
            except OSError:
                continue
            if since is None or mtime > since:
                changed.append(p)
    return sorted(changed)


def scan_roots_from(records: list[dict], workspace_root: str) -> list[str]:
    """Distinct write-log directories outside the workspace root, plus the root
    itself. Bare /tmp and $TMPDIR are never roots."""
    banned = set(BARE_TMP)
    for env in ("TMPDIR", "TMP", "TEMP"):
        val = os.environ.get(env)
        if val:
            banned.add(os.path.realpath(val).rstrip("/") or "/")
            banned.add(val.rstrip("/") or "/")
    roots = {workspace_root}
    for rec in records:
        d = os.path.dirname(rec["path"].rstrip("/"))
        if not d or d in banned or os.path.realpath(d) in banned:
            continue
        if under(d, workspace_root):
            continue
        roots.add(d)
    return sorted(roots)


def root_of(path: str, roots: list[str]) -> str:
    best = ""
    for r in roots:
        if under(path, r) and len(r) > len(best):
            best = r
    return best or os.path.dirname(path) or "/"


# ------------------------------------------------------------------- census
def census(args: argparse.Namespace) -> OrderedDict:
    state = Path(args.state).resolve()
    workspace_root = str(state.parent.parent)
    evolution_root = Path(args.evolution_root).resolve()
    graph = Path(args.graph).resolve() if args.graph else evolution_root / ROOT_GRAPH

    state_doc = read_json(state) or {}
    sidecar = read_json(Path(str(state) + SIDECAR_SUFFIX))
    checkpoint = None
    since_at: float | None = None
    outcomes_at = 0
    if isinstance(sidecar, dict):
        checkpoint = OrderedDict(
            checkpoint_id=sidecar.get("checkpoint_id"),
            at=sidecar.get("at"),
            outcomes_at=int(sidecar.get("outcomes_at") or 0),
            bytes_at=sidecar.get("bytes_at"),
            state=sidecar.get("state"),
        )
        since_at = parse_at(sidecar.get("at"))
        outcomes_at = checkpoint["outcomes_at"]

    nodes, node_source = declared_nodes(graph)
    node_set = set(nodes)
    rows = state_doc.get("completed_outcomes") or []
    declared_rows = [r for r in rows if isinstance(r, dict) and r.get("node") in node_set]
    outcomes_since = max(0, len(declared_rows) - outcomes_at)
    notes_over_cap = sum(
        1 for r in rows if isinstance(r, dict) and isinstance(r.get("note"), str) and len(r["note"]) > NOTE_CAP
    )
    try:
        state_bytes = state.stat().st_size
    except OSError:
        state_bytes = 0

    all_records = read_write_log(state)
    if since_at is None:
        windowed = all_records
    else:
        windowed = [r for r in all_records if (parse_at(r.get("at")) or 0.0) > since_at]
    records = [r for r in windowed if counted(r)]
    read_only = len(windowed) - len(records)

    roots = scan_roots_from(all_records, workspace_root)
    by_family: Counter = Counter()
    by_root: Counter = Counter()
    by_agent: Counter = Counter()
    lap: list[str] = []
    lap_strict = 0
    seen: set[str] = set()
    for rec in records:
        path = rec["path"]
        if path in seen:
            continue
        seen.add(path)
        by_family[family_of(path)] += 1
        by_root[root_of(path, roots)] += 1
        by_agent[str(rec.get("agent_type") or "lead")] += 1
        if is_lap_suffixed(path):
            lap.append(path)
            lap_strict += is_lap_strict(path)

    bytes_since = 0
    existing = 0
    for path in seen:
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isdir(path):
            existing += 1
            bytes_since += st.st_size

    families = sorted(by_family, key=lambda f: (-by_family[f], f))
    # The two text scans are per family, so a log with hundreds of families would
    # re-read the inputs hundreds of times; only the top families carry a brief.
    top_families = families[:TOP_FAMILIES]
    trend = bytes_since / max(1, outcomes_since)

    out = OrderedDict()
    out["state"] = str(state)
    out["workspace_root"] = workspace_root
    out["evolution_root"] = str(evolution_root)
    out["graph"] = str(graph)
    out["node_source"] = node_source
    out["declared_nodes"] = len(nodes)
    out["checkpoint"] = checkpoint
    out["since_checkpoint"] = checkpoint is not None
    out["outcomes"] = OrderedDict(
        declared_total=len(declared_rows), rows_total=len(rows), since=outcomes_since
    )
    out["files_since"] = OrderedDict(
        records=len(records),
        distinct=len(seen),
        existing=existing,
        read_only=read_only,
        by_family=OrderedDict((f, by_family[f]) for f in families),
        by_root=OrderedDict(sorted(by_root.items(), key=lambda kv: (-kv[1], kv[0]))),
        by_agent_type=OrderedDict(sorted(by_agent.items(), key=lambda kv: (-kv[1], kv[0]))),
        lap_suffixed=sorted(lap),
        lap_suffixed_strict=lap_strict,
    )
    out["bytes_since"] = bytes_since
    out["run_state"] = OrderedDict(bytes=state_bytes, notes_over_500=notes_over_cap)
    out["scan_roots"] = roots
    out["memory_changed"] = memory_changed(Path(args.memory_dir) if args.memory_dir else None, since_at)
    decisions_path = args.decisions
    log_path = args.log or decisions_path             # cited-by falls back to the decision record
    out["inputs"] = OrderedDict(decisions=decisions_path, log=log_path)
    out["cited_by"] = cited_by(read_lines(Path(log_path) if log_path else None), top_families)
    out["creating_sections"] = creating_sections(
        read_lines(Path(decisions_path) if decisions_path else None), top_families
    )
    out["trend"] = OrderedDict(bytes_per_outcome=round(trend, 1))
    return out


def summary(c: OrderedDict) -> str:
    """At most 15 plain lines."""
    cp = c["checkpoint"] or {}
    fs = c["files_since"]

    def top(d: dict, n: int = 5) -> str:
        items = list(d.items())[:n]
        return ", ".join(f"{k}={v}" for k, v in items) if items else "none"

    lines = [
        f"census: {c['state']}",
        f"checkpoint: {cp.get('checkpoint_id') or 'none'} at {cp.get('at') or 'run start'} "
        f"(outcomes_at {cp.get('outcomes_at', 0)})",
        f"declared nodes: {c['declared_nodes']} ({c['node_source']}); "
        f"declared outcomes since: {c['outcomes']['since']} of {c['outcomes']['declared_total']}",
        f"files since: {fs['distinct']} distinct ({fs['records']} records, {fs['existing']} exist, "
        f"{fs['read_only']} read-only); bytes since: {c['bytes_since']}",
        f"by family: {top(fs['by_family'], 8)}",
        f"by root: {top(fs['by_root'])}",
        f"by agent_type: {top(fs['by_agent_type'])}",
        f"lap-suffixed: {len(fs['lap_suffixed'])} ({fs['lap_suffixed_strict']} strict)"
        + (" e.g. " + os.path.basename(fs["lap_suffixed"][0]) if fs["lap_suffixed"] else ""),
        f"run state: {c['run_state']['bytes']} bytes, {c['run_state']['notes_over_500']} notes over 500 chars",
        f"memory changed: {len(c['memory_changed'])}",
        f"cited-by: {top(c['cited_by']) if c['inputs']['log'] else 'unattributed (no --log or --decisions given)'}",
        f"creating sections: {len(c['creating_sections'])} families matched",
        f"scan roots: {len(c['scan_roots'])}",
        f"trend: {c['trend']['bytes_per_outcome']} bytes per declared outcome",
    ]
    return "\n".join(lines[:15])


def excerpts(c: OrderedDict) -> str:
    """The evidence under the counts: one creating-section excerpt per top family
    (at most 8), then the cited-by tally. Empty when neither input matched."""
    sections = c["creating_sections"]
    lines: list[str] = []
    for family in c["files_since"]["by_family"]:
        entry = sections.get(family)
        if not entry:
            continue
        first = entry[1].strip() if len(entry) > 1 else ""
        lines.append(f"  {family}: {entry[0].strip()} | {first}"[:EXCERPT_CAP])
        if len(lines) == EXCERPT_LINES:
            break
    cites = list(c["cited_by"].items())[:EXCERPT_LINES]
    if cites:
        lines.append(("  cited-by: " + ", ".join(f"{k}={v}" for k, v in cites))[:EXCERPT_CAP])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", required=True, help="run-state.json path")
    p.add_argument("--evolution-root", required=True, help="dir holding workflow.pave.yaml")
    p.add_argument("--graph", help="graph YAML (default: <evolution-root>/workflow.pave.yaml)")
    p.add_argument("--memory-dir", help="memory root; files with mtime after the checkpoint are listed")
    p.add_argument(
        "--log",
        help="lead or decision log scanned for cited-by tallies"
        " (default: the --decisions file; with neither, every act is unattributed)",
    )
    p.add_argument(
        "--decisions",
        help="the workflow's declared decision record, for creating-section excerpts (default: none)",
    )
    p.add_argument(
        "--json", action="store_true", help="emit JSON instead of the summary plus its excerpts"
    )
    p.add_argument(
        "--since-checkpoint",
        action="store_true",
        help="scope to the audit checkpoint sidecar (the default whenever the sidecar exists)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = census(args)
    if args.json:
        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
    else:
        print(summary(result))
        detail = excerpts(result)
        if detail:
            print(detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
