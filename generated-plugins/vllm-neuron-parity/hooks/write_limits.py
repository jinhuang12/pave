#!/usr/bin/env python3
"""Shared helpers for the parity plugin's PreToolUse router and write-log hook.

Stdlib only. Everything here is a pure function or a small file read; the
hooks decide what to print and how to exit. Every helper fails OPEN: an input
it cannot read yields "no run", "no limits", or "no paths", never an error
that could strand a write.

Run discovery (marker only, same candidates as the other hooks): the file
`.vllm-neuron-parity-run` at CODEX_PROJECT_DIR, CLAUDE_PROJECT_DIR, the
payload cwd, the process cwd, or any parent directory of those (DECISIONS
§1052/§1053); its first line is the run-state path. The
workspace root is the parent of the run-state directory (artifacts/ for
artifacts/run/run-state.json); the revision folder is
`<project-root>/.vllm-neuron-parity/evolution` beside the marker.

Actor identity: a payload carrying `agent_id` (a subagent) or a session other
than the `<state>.lead-session` sidecar (a teammate) is a seat - an updater or a
reviewer only when its `agent_type` is exactly the plugin-registered name
(`pave-init:workflow-updater` / `pave_init_workflow_updater`, `pave-init:update-reviewer`
/ `pave_init_update_reviewer`, the flat forms only under the Codex harness); a
look-alike name the lead could register mid-run is a plain seat. Everything else
is the lead. `agent_type` alone never makes a seat: a lead started with `--agent`
carries one. The lead-session sidecar accepts only a Write whose content is the
writing session's own id. Protected paths are compared by on-disk spelling
(true_case) and casefolded basename, so a case-aliased name never slips past.

Limits: `write_limits.deny` / `.caps` from `<revision-folder>/workflow.pave.yaml`
at the top level or under `pave:`. PyYAML is used when importable; otherwise a
tolerant indent parser reads exactly this block (mappings, `- ` items, quoted
scalars, `[a, b]` flow lists). A parse error, or a `.applying` marker in the
revision folder, yields no limits.

Glob matching: a relative blocked path pattern is matched (fnmatchcase) against the
target's workspace-relative path and every trailing component suffix of it, so
`increments/build-*.py` matches `campaigns/c1/increments/build-x.py`; paths
outside the workspace never match a relative glob. A glob starting with `/` is
matched against the absolute path, so a scratch name_group outside the workspace
(`/tmp/<dir>/*.py`) can be cut. The run-state file, its hook-owned sidecars and
the revision folder's revision_log, graph and history are never denied: their own
guards own them.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any

MARKER = ".vllm-neuron-parity-run"
REVISION_FOLDER_REL = Path(".vllm-neuron-parity") / "evolution"
FINDINGS_NAME = "audit-findings.md"
PROPOSALS_DIR = "proposals"
RETRY_SUFFIX = re.compile(r"^(?P<stem>.+)-r\d+(?P<ext>\.[A-Za-z0-9]+)$")
WRITE_LOG_ROTATE_BYTES = 20 * 1024 * 1024
_REDIRECT_PREFIX = re.compile(r"^(?:\d?>>?|&>>?|<)+")
_TOKEN_TRIM = "\"'`;,()"


# --- payload and run discovery ---------------------------------------------


def load_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def candidate_roots(payload: dict[str, Any]) -> list[Path]:
    values = [
        os.environ.get("CODEX_PROJECT_DIR"),
        os.environ.get("CLAUDE_PROJECT_DIR"),
        payload.get("cwd"),
        os.getcwd(),
    ]
    roots: list[Path] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        # DECISIONS §1052/§1053 (2026-09-14): each candidate and then every
        # parent up to the filesystem root -- a seat whose cwd is a campaign
        # subdirectory must still find the marker at the project root.
        for candidate in (root, *root.parents):
            if candidate not in roots:
                roots.append(candidate)
    return roots


def terminal_is_settled(state: dict[str, Any]) -> bool:
    terminal = state.get("final_status")
    if isinstance(terminal, dict):
        return bool(terminal.get("status") or terminal.get("classification"))
    return bool(terminal)


@dataclass
class RunContext:
    project_root: Path
    state_path: Path
    state: dict[str, Any]

    @property
    def workspace_root(self) -> Path:
        return self.state_path.parent.parent

    @property
    def revision_folder(self) -> Path:
        return self.project_root / REVISION_FOLDER_REL

    @property
    def sidecar_path(self) -> Path:
        return self.state_path.with_name(self.state_path.name + ".audit-checkpoint.json")

    @property
    def write_log_path(self) -> Path:
        return self.state_path.with_name(self.state_path.name + ".write-log.jsonl")

    @property
    def lead_session(self) -> str | None:
        lead_file = self.state_path.with_name(self.state_path.name + ".lead-session")
        try:
            value = lead_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None


def discover_run(payload: dict[str, Any]) -> RunContext | None:
    """First marker whose state parses and is not terminal, else None."""
    for root in candidate_roots(payload):
        marker = root / MARKER
        try:
            first = marker.read_text(encoding="utf-8").splitlines()[0].strip()
            state_path = true_case(Path(first).expanduser().resolve())
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (IndexError, OSError, TypeError, ValueError):
            continue
        if isinstance(state, dict) and not terminal_is_settled(state):
            return RunContext(project_root=root, state_path=state_path, state=state)
    return None


# --- actor identity ----------------------------------------------------------


def actor(payload: dict[str, Any], run: RunContext) -> str:
    """'lead', 'seat', 'updater', or 'reviewer' (the last two are seats)."""
    agent_type = payload.get("agent_type")
    lead = run.lead_session
    session = payload.get("session_id")
    is_seat = bool(payload.get("agent_id")) or bool(
        lead and isinstance(session, str) and session != lead
    )
    if not is_seat:
        return "lead"
    codex = bool(os.environ.get("CODEX_PROJECT_DIR"))
    if agent_type in UPDATER_TYPES and (":" in agent_type or codex):
        return "updater"
    if agent_type in REVIEWER_TYPES and (":" in agent_type or codex):
        return "reviewer"
    return "seat"


UPDATER_TYPES = frozenset({"pave-init:workflow-updater", "pave_init_workflow_updater"})
REVIEWER_TYPES = frozenset({"pave-init:update-reviewer", "pave_init_update_reviewer"})
COOLDOWN_PREFIX = "vllm-neuron-parity-stop-nudged-"


def is_lead_session_file(path: Path, run: RunContext) -> bool:
    return same_file(path, run.state_path.with_name(run.state_path.name + ".lead-session"))


def lead_session_content_ok(payload: dict[str, Any]) -> bool:
    """True only for a Write whose whole content is the writing session's own id (the
    SKILL duty: one line, this session's id). Edit and MultiEdit are refused: their
    result depends on the file, so a replacement text equal to the id can still leave
    a file that names no session. Bash shapes: False."""
    session = payload.get("session_id")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if not isinstance(session, str) or not session.strip() or payload.get("tool_name") != "Write":
        return False
    content = tool_input.get("content")
    return isinstance(content, str) and content.strip() == session.strip()


# --- write_limits block --------------------------------------------------


def _strip_scalar(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_strip_scalar(part) for part in inner.split(",")] if inner else []
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if text in ("null", "~", ""):
        return None
    return text


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    """Parse the lines at `indent` starting at `index`; return (value, next index)."""
    if index >= len(lines):
        return None, index
    first = lines[index].strip()
    is_list = first.startswith("- ") or first == "-"
    result: Any = [] if is_list else {}
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        current = len(raw) - len(raw.lstrip(" "))
        if current < indent:
            break
        if current > indent:
            raise ValueError("unexpected indent")
        if is_list:
            if not (stripped.startswith("- ") or stripped == "-"):
                raise ValueError("mixed list and mapping")
            body = stripped[1:].strip()
            if not body:
                value, index = _parse_block(lines, index + 1, _next_indent(lines, index + 1))
                result.append(value)
                continue
            # Inline mapping item: treat "- key: value" as a mapping whose
            # remaining keys sit at indent + 2 on the following lines.
            lines[index] = " " * (indent + 2) + body
            value, index = _parse_block(lines, index, indent + 2)
            result.append(value)
            continue
        if stripped.startswith("- "):
            raise ValueError("mixed mapping and list")
        key, sep, rest = stripped.partition(":")
        if not sep:
            raise ValueError("no key")
        rest = rest.split(" #", 1)[0].strip()
        if rest:
            result[key.strip()] = _strip_scalar(rest)
            index += 1
            continue
        child_indent = _next_indent(lines, index + 1)
        if child_indent <= indent:
            result[key.strip()] = None
            index += 1
            continue
        value, index = _parse_block(lines, index + 1, child_indent)
        result[key.strip()] = value
    return result, index


def _next_indent(lines: list[str], index: int) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            return len(lines[index]) - len(lines[index].lstrip(" "))
        index += 1
    return -1


def _parse_minimal(text: str) -> dict[str, Any] | None:
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        if raw.strip() != "write_limits:":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        child_indent = _next_indent(lines, index + 1)
        if child_indent <= indent:
            return {}
        block = list(lines)
        value, _ = _parse_block(block, index + 1, child_indent)
        return value if isinstance(value, dict) else None
    return {}


def parse_write_limits(text: str, use_yaml: bool = True) -> dict[str, list[dict[str, Any]]]:
    """Return {'deny': [...], 'caps': [...]} or raise ValueError on any parse problem."""
    block: Any = None
    if use_yaml:
        try:
            import yaml  # type: ignore
        except ImportError:
            use_yaml = False
    if use_yaml:
        doc = yaml.safe_load(text)  # type: ignore[name-defined]
        if isinstance(doc, dict):
            block = doc.get("write_limits")
            if block is None and isinstance(doc.get("pave"), dict):
                block = doc["pave"].get("write_limits")
    else:
        block = _parse_minimal(text)
    if block is None:
        return {"deny": [], "caps": []}
    if not isinstance(block, dict):
        raise ValueError("write_limits is not a mapping")
    out: dict[str, list[dict[str, Any]]] = {}
    for key in ("deny", "caps"):
        items = block.get(key) or []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError(f"write_limits.{key} is not a list of mappings")
        out[key] = items
    return out


def load_limits(run: RunContext) -> dict[str, list[dict[str, Any]]]:
    """Limits at the newest revision; empty (fail OPEN) on parse error or during an apply step."""
    empty: dict[str, list[dict[str, Any]]] = {"deny": [], "caps": []}
    root = run.revision_folder
    if (root / ".applying").exists():
        return empty
    try:
        text = (root / "workflow.pave.yaml").read_text(encoding="utf-8")
        return parse_write_limits(text)
    except Exception:
        return empty


def glob_matches(rel_posix: str, glob: str) -> bool:
    """Suffix match, case-insensitive: a case-aliased spelling must not slip past a glob."""
    parts = rel_posix.casefold().split("/")
    glob = glob.casefold()
    return any(fnmatchcase("/".join(parts[i:]), glob) for i in range(len(parts)))


def never_denied(path: Path, run: RunContext) -> bool:
    """Run state, its sidecars, and the revision folder's revision_log, graph and history."""
    if same_name(path, run.state_path) or is_hook_owned(path, run) or is_lead_session_file(path, run):
        return True
    evolution = true_case(run.revision_folder.resolve())
    try:
        rel = path.relative_to(evolution).as_posix().casefold()
    except ValueError:
        return False
    return rel in ("revisions.yaml", ".applying") or rel.endswith(".pave.yaml") or rel.startswith("history/")


def _absolute_glob_forms(glob: str) -> set[str]:
    """The glob as written and with its literal directory prefix resolved through
    symlinks (/tmp -> /private/tmp on macOS), since target paths are resolved."""
    forms = {glob}
    head = re.split(r"[*?\[]", glob, maxsplit=1)[0]
    prefix = head[: head.rfind("/") + 1] if "/" in head else ""
    if prefix:
        try:
            real = os.path.realpath(prefix.rstrip("/") or "/")
            forms.add(real.rstrip("/") + "/" + glob[len(prefix):])
        except OSError:
            pass
    return forms


def matching_deny(path: Path, run: RunContext, deny: list[dict[str, Any]]) -> dict[str, Any] | None:
    if never_denied(path, run):
        return None
    abs_posix = path.as_posix()
    try:
        rel = path.relative_to(true_case(run.workspace_root.resolve())).as_posix()
    except ValueError:
        rel = None
    for entry in deny:
        glob = entry.get("glob")
        if not (isinstance(glob, str) and glob):
            continue
        if glob.startswith("/"):
            if any(fnmatchcase(abs_posix.casefold(), g.casefold()) for g in _absolute_glob_forms(glob)):
                return entry
        elif rel is not None and glob_matches(rel, glob):
            return entry
    return None


# --- target paths ------------------------------------------------------------


def true_case(path: Path) -> Path:
    """The on-disk spelling of every existing component. A case-insensitive volume (the
    macOS default) aliases RUN-STATE.JSON to run-state.json; a protected-path check that
    compares typed strings would let the alias through. Missing components keep their
    typed spelling (the basename compare casefolds them, see same_name)."""
    parts = path.parts
    if not parts:
        return path
    cur = Path(parts[0])
    for comp in parts[1:]:
        cand = cur / comp
        try:
            if cand.exists() or cand.is_symlink():
                names = os.listdir(cur)
                if comp not in names:
                    comp = next((n for n in names if n.casefold() == comp.casefold()), comp)
        except OSError:
            pass
        cur = cur / comp
    return cur


def is_revision_log_surface(path: Path, run: RunContext) -> bool:
    """<revision-folder>/*.pave.yaml or revisions.yaml, or the revision folder itself (a
    move, a recursive copy over it, a removal): written only by an apply step."""
    evolution = true_case(run.revision_folder.resolve())
    if same_file(path, evolution):
        return True
    name = path.name.casefold()
    return path.parent == evolution and (name.endswith(".pave.yaml") or name == "revisions.yaml")


def same_name(a: Path, b: Path) -> bool:
    """Same directory (both true-cased) and the same basename up to case."""
    return a.parent == b.parent and a.name.casefold() == b.name.casefold()


def normalize(path: Path) -> Path:
    """Resolve the path when it exists, else its parent plus the basename; then the
    on-disk spelling of every existing component."""
    try:                                             # a dangling symlink resolves to its target too
        resolved = path.resolve() if (path.exists() or path.is_symlink()) else path.parent.resolve() / path.name
    except OSError:
        return path
    return true_case(resolved)


def same_file(a: Path, b: Path) -> bool:
    """The same inode (hard link, any symlink) when both exist, else the same name."""
    try:
        if a.exists() and b.exists() and os.path.samefile(a, b):
            return True
    except OSError:
        pass
    return same_name(a, b)


def bash_path_tokens(command: str, cwd: str | None) -> list[Path]:
    """Whitespace tokens of a shell command that name a path (see module doc)."""
    out: list[Path] = []
    base = Path(cwd) if isinstance(cwd, str) and cwd and os.path.isdir(cwd) else None
    try:
        tokens = shlex.split(command, comments=True, posix=True)
    except ValueError:  # unbalanced quoting: fall back to plain whitespace tokens
        tokens = command.split()
    for raw in tokens:
        tok = _REDIRECT_PREFIX.sub("", raw).strip(_TOKEN_TRIM)
        if "=" in tok and not tok.startswith(("/", ".", "~")):
            tok = tok.split("=", 1)[1].strip(_TOKEN_TRIM)
        if not tok or tok in (".", "..") or tok.startswith("-") or "://" in tok:
            continue
        if tok.startswith("~"):
            tok = os.path.expanduser(tok)
        if os.path.isabs(tok):
            candidate = Path(tok)
        elif base is not None and ("/" in tok or (base / tok).exists()):
            candidate = base / tok
        else:
            continue
        if str(candidate).startswith("/dev/"):
            continue
        if not (candidate.exists() or candidate.parent.exists()):
            continue
        resolved = normalize(candidate)
        if resolved not in out:
            out.append(resolved)
    return out


_WRITERS = {"tee", "cp", "mv", "rm", "truncate", "dd", "install", "ln", "mkdir", "touch",
            "rsync", "shred", "chmod", "chown", "unlink", "rmdir"}
_WRITE_REDIRECT = re.compile(r"^(?:\d?>>?|&>>?)")
_SEGMENT_SPLIT = re.compile(r"\s*(?:\|\||&&|;|\||&)\s*")


def bash_write_tokens(command: str, cwd: str | None) -> list[Path]:
    """Paths a shell command WRITES: redirect targets, and every path argument of a
    writer command (tee, cp, mv, rm, ..., `sed -i`, `python -c`-free scripts excluded).
    A path merely mentioned (an argument to cat, grep, python3 <script> ...) is not a write."""
    import glob as _glob
    out: list[Path] = []
    base = cwd or os.getcwd()
    for segment in _SEGMENT_SPLIT.split(command):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, comments=True, posix=True)
        except ValueError:
            tokens = segment.split()
        words = list(tokens)
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words.pop(0)                             # leading env assignments only
        head = os.path.basename(words[0].lstrip("(")) if words else ""   # `(cd X && ...` tracks X too
        if head in ("sudo", "env", "nohup", "time", "command") and len(words) > 1:
            head = os.path.basename(words[1])
        if head == "cd":                             # later segments resolve against the new directory
            target = words[1] if len(words) > 1 else os.path.expanduser("~")
            base = os.path.normpath(os.path.join(base, os.path.expanduser(target)))
            continue
        cwd = base
        writer = head in _WRITERS or (head == "sed" and any(t.startswith("-i") for t in words))
        redirected = [t for t in tokens if _WRITE_REDIRECT.match(t) and t not in (">", ">>", "&>", "&>>")]
        # a redirect written as two tokens (`> file`) names the file in the next token
        for i, t in enumerate(tokens):
            if t in (">", ">>", "&>", "&>>") or re.fullmatch(r"\d>>?", t):
                if i + 1 < len(tokens):
                    redirected.append(">" + tokens[i + 1])
        raw = words[1:]
        if head == "dd":                             # dd writes only its of= target
            args = [t[3:] for t in raw if t.startswith("of=")]
        elif head in ("cp", "install", "rsync") and len(raw) >= 2:
            args = [raw[-1]]                         # the destination is the write; sources are reads
        else:                                        # mv destroys its source: every argument is a write
            args = [re.sub(r"^[A-Za-z_]+=", "", t) for t in raw]
        if head in ("cp", "mv", "install", "rsync") and len(raw) >= 2:
            dest = raw[-1]
            dest_abs = dest if os.path.isabs(dest) else os.path.join(cwd, dest)
            if dest.endswith("/") or os.path.isdir(dest_abs):   # a directory destination receives dest/<name>
                for src in raw[:-1]:
                    if not src.startswith("-"):
                        args.append(os.path.join(dest, os.path.basename(src.rstrip("/").rstrip(".")) or ""))
        expanded: list[str] = []
        for a in args:                               # a glob names every file it matches
            if any(ch in a for ch in "*?["):
                expanded += _glob.glob(a if os.path.isabs(a) else os.path.join(cwd, a))
            expanded.append(a)
        args = expanded
        if head == "ln" and len(args) >= 2:          # a link target is relative to the link's directory
            link_dir = os.path.dirname(args[-1]) or "."
            args += [os.path.join(link_dir, a) for a in args[:-1] if not a.startswith("-") and not os.path.isabs(a)]
        chosen = redirected + (args if writer else [])
        if not chosen:
            continue
        for p in bash_path_tokens(" ".join(shlex.quote(c) for c in chosen), cwd):
            if p not in out:
                out.append(p)
    return out


def write_target_paths(payload: dict[str, Any]) -> list[Path]:
    """Write/Edit/MultiEdit: file_path. Bash: only the paths the command writes."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    file_path = tool_input.get("file_path")
    cwd = payload.get("cwd")
    if isinstance(file_path, str) and file_path:
        p = Path(file_path).expanduser()
        if not p.is_absolute() and isinstance(cwd, str):
            p = Path(cwd) / p
        return [normalize(p)]
    command = tool_input.get("command")
    if isinstance(command, str) and command.strip():
        return bash_write_tokens(command, cwd if isinstance(cwd, str) else os.getcwd())
    return []


def target_paths(payload: dict[str, Any]) -> list[Path]:
    """Write/Edit/MultiEdit: file_path. Bash: every argv token that is a path."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    file_path = tool_input.get("file_path")
    if isinstance(file_path, str) and file_path:
        return [normalize(Path(file_path).expanduser())]
    command = tool_input.get("command")
    if isinstance(command, str) and command.strip():
        cwd = payload.get("cwd")
        return bash_path_tokens(command, cwd if isinstance(cwd, str) else os.getcwd())
    return []


# --- no-retry-copy ----------------------------------------------------------------


def bash_retry_copy_candidates(command: str, cwd: str | None) -> list[Path]:
    """Bare `<stem>-rN.<ext>` tokens of a shell command, resolved against the segment's
    working directory (`cd` tracked per segment as in bash_write_tokens). A generator
    script names its output as a plain argument, so a token needs no "/" to count."""
    out: list[Path] = []
    base = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    for segment in _SEGMENT_SPLIT.split(command):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, comments=True, posix=True)
        except ValueError:  # unbalanced quoting: fall back to plain whitespace tokens
            tokens = segment.split()
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
            tokens.pop(0)                            # leading env assignments only
        head = os.path.basename(tokens[0].lstrip("(")) if tokens else ""   # `(cd X && ...` tracks X too
        if head == "cd":                             # later segments resolve against the new directory
            target = tokens[1] if len(tokens) > 1 else os.path.expanduser("~")
            base = os.path.normpath(os.path.join(base, os.path.expanduser(target)))
            continue
        for raw in tokens:
            tok = _REDIRECT_PREFIX.sub("", raw).strip(_TOKEN_TRIM)
            if "=" in tok and not tok.startswith(("/", ".", "~")):
                tok = tok.split("=", 1)[1].strip(_TOKEN_TRIM)   # --out=foo-r3.md -> foo-r3.md
            if not tok or tok.startswith("-") or "://" in tok:
                continue
            tok = tok.rstrip("/") or tok                 # a trailing slash still names the file
            if not RETRY_SUFFIX.match(os.path.basename(tok)):
                continue
            tok = os.path.expanduser(tok)
            candidate = Path(tok) if os.path.isabs(tok) else Path(base) / tok
            resolved = normalize(candidate)
            if resolved not in out:
                out.append(resolved)
    return out


def retry_copy_candidates(payload: dict[str, Any]) -> list[Path]:
    """Paths a payload could file as a new cut. Write/Edit/MultiEdit: file_path. Bash:
    every path token plus every bare round-suffix token, deduplicated."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return []
    file_path = tool_input.get("file_path")
    if isinstance(file_path, str) and file_path:
        return write_target_paths(payload)
    command = tool_input.get("command")
    if not (isinstance(command, str) and command.strip()):
        return []
    cwd = payload.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    out: list[Path] = []
    for path in target_paths(payload) + bash_retry_copy_candidates(command, cwd):
        if path not in out:
            out.append(path)
    return out


def retry_copy_conflict(path: Path) -> Path | None:
    """The existing same-stem file that makes `<stem>-rN.<ext>` a retry copy, else None.
    A same-stem file parked under one extra marker (.superseded, .bak) still counts."""
    if "increments" not in path.parts or path.exists():
        return None
    match = RETRY_SUFFIX.match(path.name)
    if not match:
        return None
    stem, ext = match.group("stem"), match.group("ext")
    sibling_form = re.compile(rf"^{re.escape(stem)}(?:-r\d+)?{re.escape(ext)}(?:\.[A-Za-z0-9]+)?$")
    try:
        siblings = sorted(path.parent.iterdir())
    except OSError:
        return None
    for sibling in siblings:
        if sibling.name != path.name and sibling.is_file() and sibling_form.match(sibling.name):
            return sibling
    return None


# --- audit sidecar -----------------------------------------------------------


def is_hook_owned(path: Path, run: RunContext) -> bool:
    if path.name.casefold().startswith(COOLDOWN_PREFIX):   # the stop guard cooldown marker, any directory
        return True
    for owned in (run.sidecar_path, run.write_log_path,
                  run.write_log_path.with_name(run.write_log_path.name.replace(".jsonl", ".1.jsonl"))):
        if path.exists() and owned.exists() and same_file(path, owned):   # a hard link, wherever it sits
            return True
    if path.parent != run.state_path.parent:
        return False
    try:                                             # any multiply-linked file beside run state is suspect
        if path.exists() and not path.is_dir() and os.stat(path).st_nlink > 1:
            return True
    except OSError:
        pass
    name, state_name = path.name.casefold(), run.state_path.name.casefold()
    return name == state_name + ".audit-checkpoint.json" or (
        name.startswith(state_name + ".write-log") and name.endswith(".jsonl")
    ) or (name.startswith(state_name + ".audit-write-report-") and name.endswith(".txt"))


def load_sidecar(run: RunContext) -> dict[str, Any] | None:
    try:
        data = json.loads(run.sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_sidecar(run: RunContext, sidecar: dict[str, Any]) -> None:
    target = run.sidecar_path
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(target.parent), prefix=target.name + ".", delete=False
    )
    with handle:
        json.dump(sidecar, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(handle.name, target)


def findings_checkpoint(path: Path) -> str | None:
    """The checkpoint id named on the findings record's first line, else None."""
    try:
        with path.open(encoding="utf-8") as handle:
            first = handle.readline().strip()
    except OSError:
        return None
    key, sep, value = first.partition(":")
    if not sep or key.strip() != "checkpoint":
        return None
    return value.strip() or None


def review_line(path: Path) -> str | None:
    """'PASS' or 'REVISE' from the last `review:` line of the findings record."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.findall(r"(?m)^\s*review:\s*(PASS|REVISE)\b", text)
    return found[-1] if found else None


def hook_record_proposal(sidecar: dict[str, Any], path: Path) -> bool:
    """Record {path,size,mtime} for a proposal, replacing an older hook_record of the same path."""
    try:
        stat = path.stat()
    except OSError:
        return False
    hook_record = {"path": str(path), "size": stat.st_size, "mtime": stat.st_mtime}
    hook_records = sidecar.get("hook_recorded_writes")
    if not isinstance(hook_records, list):
        hook_records = []
    hook_records = [s for s in hook_records if not (isinstance(s, dict) and s.get("path") == hook_record["path"])]
    hook_records.append(hook_record)
    sidecar["hook_recorded_writes"] = hook_records
    return True


# --- hook output -------------------------------------------------------------


def advisory_json(event: str, text: str) -> str:
    return json.dumps(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    )
