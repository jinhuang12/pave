#!/usr/bin/env python3
"""PostToolUse write log (Bash|Write|Edit): one record per written path.

Appends to `<state>.write-log.jsonl` beside the run state:
  {"at": "...Z", "session_id": "...", "agent_id": "...|null",
   "agent_type": "...|null", "tool": "Bash|Write|Edit", "path": "/abs/path"}
For Write/Edit/MultiEdit the record names `tool_input.file_path`; for Bash it
names every argv token that is a path (hooks/runtime_bindings.py
`bash_path_tokens`: shlex-split, `>`/`>>` prefixes stripped, absolute, or
resolving against the payload cwd to an existing path or one whose parent
exists). The stop guard's audit branch and the census read this log; the
router denies the lead writing it.

Rotation: when the log exceeds 20 MB it is renamed to `.write-log.1.jsonl`
(one generation kept) before the append. Timestamps come from the clock.
Always exits 0; silent without a run marker or on any error.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_bindings as rb  # noqa: E402


def _rotate(log: Path) -> None:
    try:
        if log.stat().st_size > rb.WRITE_LOG_ROTATE_BYTES:
            os.replace(log, log.with_name(log.name.replace(".jsonl", ".1.jsonl")))
    except OSError:
        pass


def main() -> int:
    try:
        payload = rb.load_payload(sys.stdin.read())
        if not payload:
            return 0
        run = rb.discover_run(payload)
        if run is None:
            return 0
        paths = rb.target_paths(payload)
        if not paths:
            return 0
        now = datetime.now(timezone.utc)
        at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        tool = payload.get("tool_name")
        edit_tool = tool in ("Write", "Edit", "MultiEdit")
        records = []
        for path in paths:
            size = mtime = None
            written = edit_tool
            try:
                st = os.stat(path)
                if not os.path.isdir(path):
                    size, mtime = st.st_size, st.st_mtime
                    # a Bash mention is a write only when the file just changed
                    written = edit_tool or (now.timestamp() - st.st_mtime) <= 120
                else:
                    written = False
            except OSError:
                written = edit_tool
            records.append({
                "at": at,
                "session_id": payload.get("session_id"),
                "agent_id": payload.get("agent_id"),
                "agent_type": payload.get("agent_type"),
                "tool": tool if isinstance(tool, str) else None,
                "path": str(path),
                "size": size,
                "mtime": mtime,
                "written": written,
            })
        log = run.write_log_path
        _rotate(log)
        with log.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:  # never strand a run over its own bookkeeping
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
