#!/usr/bin/env python3
"""Track active PAVE Codex subagents for conservative PostToolUse scoping.

Codex documents ``agent_id`` and ``agent_type`` on SubagentStart and
SubagentStop, but not on PostToolUse.  The PAVE lead-only staleness reminder
therefore uses a session-scoped activity latch: while any PAVE subagent is
active, the reminder stays silent.  The planning-layout adapter treats a
write as subagent-owned during that same interval.  This can suppress a lead
reminder during concurrent subagent work, but it avoids sending lead duties to
workers or letting worker writes look lead-owned.

The hook is advisory.  Every error fails open with exit status 0.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Iterator

try:  # POSIX is already required by the canonical bash hooks.
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - native Windows is unsupported here.
    fcntl = None  # type: ignore

_MAX_AGE_SECONDS = 24 * 60 * 60


def _safe_session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:24]


def _base_dir() -> Path:
    root = Path(os.environ.get("TMPDIR") or tempfile.gettempdir())
    return root / "pave-init-codex-subagents"


def _state_path(session_id: str) -> Path:
    return _base_dir() / f"{_safe_session_key(session_id)}.json"


def _lock_path(session_id: str) -> Path:
    return _base_dir() / f"{_safe_session_key(session_id)}.lock"


@contextlib.contextmanager
def _locked(session_id: str) -> Iterator[None]:
    base = _base_dir()
    base.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(session_id)
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_state_unlocked(session_id: str) -> set[str]:
    path = _state_path(session_id)
    try:
        if time.time() - path.stat().st_mtime > _MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            return set()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return set()
    agents = data.get("active_agent_ids", []) if isinstance(data, dict) else []
    return {str(item) for item in agents if item}


def _write_state_unlocked(session_id: str, agents: set[str]) -> None:
    path = _state_path(session_id)
    if not agents:
        path.unlink(missing_ok=True)
        return
    payload = {
        "session_id_hash": _safe_session_key(session_id),
        "updated_at": int(time.time()),
        "active_agent_ids": sorted(agents),
    }
    temp = path.with_suffix(f".tmp.{os.getpid()}")
    temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temp, path)


def active_agent_ids(session_id: str) -> set[str]:
    """Return the active PAVE subagent ids for one Codex session."""

    if not session_id:
        return set()
    try:
        with _locked(session_id):
            return _read_state_unlocked(session_id)
    except OSError:
        return set()


def update_activity(session_id: str, agent_id: str, active: bool) -> None:
    """Add or remove one PAVE subagent id."""

    if not session_id or not agent_id:
        return
    try:
        with _locked(session_id):
            agents = _read_state_unlocked(session_id)
            if active:
                agents.add(agent_id)
            else:
                agents.discard(agent_id)
            _write_state_unlocked(session_id, agents)
    except OSError:
        return


def _load_payload() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"start", "stop"}:
        return 0

    payload = _load_payload()
    session_id = str(payload.get("session_id") or "")
    agent_id = str(payload.get("agent_id") or "")
    agent_type = str(payload.get("agent_type") or "")

    # The matcher should already restrict this to PAVE agents.  Keep a second
    # identity gate in the script so a broad future registration cannot turn
    # unrelated subagents into false lead-scope signals.
    if not agent_type.startswith("pave_init_"):
        return 0

    update_activity(session_id, agent_id, active=args[0] == "start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
