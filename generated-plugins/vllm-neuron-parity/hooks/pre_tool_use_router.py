#!/usr/bin/env python3
"""Route tool-use hooks for an active vLLM-Neuron parity run.

Three guard modes (protected-branch, compile-cache, venv-opt) gate the Codex
blocking guard scripts on an active run marker. Four modes read the run
directly (helpers in hooks/write_limits.py):

  write-limits  PreToolUse Bash|Edit|Write|MultiEdit. Blocked path patterns from the
                    graph's write_limits block bind the lead: a lead target
                    matching one is refused (exit 2) with reason and remedy; a
                    seat's match is advisory (additionalContext). Fails OPEN on
                    a parse error or while <revision-folder>/.applying exists.
  no-retry-copy          PreToolUse Bash|Edit|Write|MultiEdit, every actor. Refuses
                    creating <stem>-rN.<ext> under an increments/ component when
                    a same-stem same-extension file already sits there (a parked
                    marker such as .superseded still counts); for Bash any
                    argument, redirect target, or copy/move destination naming
                    the form counts. A path built inside a script body stays
                    invisible here: the write log and the write_report catch it after.
  audit-sidecar     PreToolUse Bash|Edit|Write|MultiEdit. The lead never writes
                    the hook-owned <state>.audit-checkpoint.json,
                    <state>.write-log*.jsonl or <state>.audit-write-report-*.txt, or the
                    updater's findings record in the revision folder.
  audit-hook-record       PostToolUse Write|Edit|MultiEdit. The workflow updater's findings
                    record naming the open checkpoint sets the sidecar OPEN;
                    its files under <revision-folder>/proposals/ are hook_recorded
                    (path, size, mtime) for record_revision.py apply --hook-records.

Every unexpected exception fails open: nothing printed, exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_limits as rb  # noqa: E402

SCRIPTS = {
    "protected-branch": "protected-branch-guard.sh",
    "compile-cache": "compile-cache-guard.sh",
    "venv-opt": "venv-opt-guard.sh",
}
TAG = "[vllm-neuron-parity router]"
NO_RETRY_COPY_TEXT = (
    "edit the existing file {existing} in place; external output gets a "
    "name keyed to its event, never -rN"
)

_load_payload = rb.load_payload
_candidate_roots = rb.candidate_roots
_terminal_is_settled = rb.terminal_is_settled


def _state_is_valid(state_path: Path) -> bool:
    validator = _plugin_root() / "scripts" / "validate_run_state.py"
    if not validator.is_file():
        return False
    try:
        completed = subprocess.run(
            [sys.executable, str(validator), str(state_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _has_active_run(payload: dict[str, Any]) -> bool:
    for root in _candidate_roots(payload):
        marker = root / rb.MARKER
        try:
            state_path = Path(marker.read_text(encoding="utf-8").splitlines()[0].strip())
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (IndexError, OSError, TypeError, ValueError):
            continue
        if (
            isinstance(state, dict)
            and _state_is_valid(state_path)
            and not _terminal_is_settled(state)
        ):
            return True
    return False


def _plugin_root() -> Path:
    configured = os.environ.get("PLUGIN_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _run_guard_script(guard: str, raw: str, payload: dict[str, Any]) -> int:
    if not _has_active_run(payload):
        return 0
    script = _plugin_root() / "skills" / "vllm-neuron-parity" / "hooks" / SCRIPTS[guard]
    if not script.is_file():
        return 0
    cwd = payload.get("cwd")
    run_cwd = cwd if isinstance(cwd, str) and Path(cwd).is_dir() else os.getcwd()
    try:
        completed = subprocess.run(
            [str(script)],
            input=raw,
            text=True,
            capture_output=True,
            check=False,
            cwd=run_cwd,
            env=os.environ.copy(),
        )
    except OSError:
        return 0
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


# --- run-reading modes -------------------------------------------------------


def _block(text: str) -> int:
    sys.stderr.write(f"{TAG} Denied: {text}\n")
    return 2


def mode_write_limits(payload: dict[str, Any], run: rb.RunContext) -> int:
    deny = rb.load_limits(run)["deny"]
    if not deny:
        return 0
    who = rb.actor(payload, run)
    for path in rb.target_paths(payload):
        entry = rb.matching_deny(path, run, deny)
        if entry is None:
            continue
        reason = str(entry.get("reason") or "").strip()
        remedy = str(entry.get("remedy") or "").strip()
        text = (
            f"{path} matches the blocked path pattern {entry.get('glob')!r} bound to the lead "
            f"(applied by checkpoint {entry.get('created_by')}). Reason: {reason} "
            f"Remedy: {remedy}"
        )
        if who == "lead":
            return _block(text)
        sys.stdout.write(
            rb.advisory_json(
                "PreToolUse",
                f"{TAG} {text} The glob binds the lead, not you; if this write "
                "was ordered by the lead, report the order instead of executing it.",
            )
        )
        return 0
    return 0


def mode_no_retry_copy(payload: dict[str, Any], run: rb.RunContext) -> int:
    for path in rb.retry_copy_candidates(payload):
        existing = rb.retry_copy_conflict(path)
        if existing is not None:
            token = ""                               # a shell actor sees which argument tripped it
            if str(payload.get("tool_name") or "") == "Bash":
                token = f"the argument {path.name} of this command names a new file: "
            return _block(
                f"{token}{path} is a retry copy of {existing.name}: "
                + NO_RETRY_COPY_TEXT.format(existing=existing)
            )
    return 0


def mode_audit_sidecar(payload: dict[str, Any], run: rb.RunContext) -> int:
    who = rb.actor(payload, run)
    findings = rb.true_case(run.revision_folder.resolve()) / rb.FINDINGS_NAME
    for path in rb.write_target_paths(payload):      # mentions (apply --hook-records <sidecar>) pass
        if rb.is_hook_owned(path, run):              # every actor: no seat writes these either
            return _block(
                f"{path} is hook-owned audit state (checkpoint sidecar, write log, write_report, or "
                "stop cooldown marker); the stop guard and the router write it, no actor does. "
                "Leave it, and let the audit check close the checkpoint through the updater."
            )
        if rb.is_revision_log_surface(path, run) and not (findings.parent / ".applying").exists():
            return _block(
                f"{path} is the live graph or its revision log; it changes only through "
                "record_revision.py apply <root> <N> --proposal <patch> --hook-records <sidecar> (which "
                "sets the .applying marker while it writes), never through a direct write by any "
                "actor: a graph written by hand re-keys the audit counters and every blocked path pattern."
            )
        if rb.is_lead_session_file(path, run) and not rb.lead_session_content_ok(payload):
            return _block(
                f"{path} names the lead session and is the identity every lead-keyed control "
                "reads; it accepts only a Write whose whole content is this session's own id "
                f"({payload.get('session_id')}) written with the Write tool, never an Edit, a shell write, or another id."
            )
        if who in ("updater", "reviewer"):
            continue
        if rb.same_file(path, findings):             # every actor but the two audit seats
            return _block(
                f"{path} is the audit findings record: the workflow-updater writes it and the "
                "update-reviewer appends its one review: line; no other actor edits it, the "
                "lead included. A checkpoint closes through an applied hook_recorded proposal or the "
                "updater's own no-change record, never through text anyone else types."
            )
    return 0


def mode_audit_hook_record(payload: dict[str, Any], run: rb.RunContext) -> int:
    who = rb.actor(payload, run)
    if who not in ("updater", "reviewer"):
        return 0
    sidecar = rb.load_sidecar(run)
    if sidecar is None:
        return 0
    evolution = rb.true_case(run.revision_folder.resolve())
    changed = False
    for path in rb.target_paths(payload):
        if rb.same_name(path, evolution / rb.FINDINGS_NAME):
            named = rb.findings_checkpoint(path)
            if not (named and named == sidecar.get("checkpoint_id")):
                continue
            if who == "updater" and str(sidecar.get("state") or "").upper() in ("DUE", "OPEN"):
                sidecar["state"] = "OPEN"
                sidecar["findings_record"] = str(path)
                changed = True
            else:                                   # the reviewer's one line, hook_recorded
                verdict = rb.review_line(path)
                if verdict and sidecar.get("review") != verdict:
                    sidecar["review"] = verdict
                    changed = True
            try:                                    # the record as these two seats left it
                st = path.stat()
                sidecar["findings_stat"] = {"size": st.st_size, "mtime": st.st_mtime}
                changed = True
            except OSError:
                pass
        elif who == "updater" and path.parent == evolution / rb.PROPOSALS_DIR and path.is_file():
            changed = rb.hook_record_proposal(sidecar, path) or changed
    if changed:
        rb.save_sidecar(run, sidecar)
    return 0


MODES: dict[str, Callable[[dict[str, Any], rb.RunContext], int]] = {
    "write-limits": mode_write_limits,
    "no-retry-copy": mode_no_retry_copy,
    "audit-sidecar": mode_audit_sidecar,
    "audit-hook-record": mode_audit_hook_record,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("guard", choices=tuple(SCRIPTS) + tuple(MODES))
    args = parser.parse_args(argv)

    raw = sys.stdin.read()
    payload = _load_payload(raw)
    if not payload:
        return 0
    if args.guard in SCRIPTS:
        return _run_guard_script(args.guard, raw, payload)
    try:
        run = rb.discover_run(payload)
        if run is None:
            return 0
        return MODES[args.guard](payload, run)
    except Exception:  # fail open: a hook must never strand a run
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
