#!/usr/bin/env python3
"""Append-only run log for the orchestrate skill (spec section 4.1); standard library only.

Only the orchestrator calls it. The log is <cwd>/.orchestrate/<UTC stamp>.md; the marker
<cwd>/.orchestrate/active-$CLAUDE_CODE_SESSION_ID holds its absolute path.
Subcommands: init, append, status, adopt. Exit 0 ok, 1 refused or invalid input, 2 usage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path

EVENTS = ("PLAN", "DONE", "BLOCKED", "ESCALATE", "RESPAWN", "REVIEW", "INTEGRATE", "CLOSE")
STOPS = ("accepted", "closed_unaccepted", "blocked", "incomplete", "exhausted")
LINE = re.compile(r"^- \d\d:\d\d (\S+) (\S+) ?(.*)$")
RESPAWN = re.compile(r"(\S+) -> (\S+)")
SCRIPT = os.path.abspath(__file__)


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def read_lines(log):
    return [l for l in Path(log).read_text(encoding="utf-8").splitlines() if l.strip()]


def is_closed(log):
    lines = read_lines(log)
    m = LINE.match(lines[-1]) if lines else None
    return bool(m) and m.group(1) == "CLOSE"


def event_line(event, actor, text):
    """Return the log line for one event; exit 1 on an unknown event or a bad CLOSE."""
    if event not in EVENTS:
        fail(f"unknown event {event!r}; use one of {' '.join(EVENTS)}")
    if not actor or any(c.isspace() for c in actor):
        fail(f"{event} needs an actor with no spaces")
    text = " ".join(text.splitlines()).strip()
    if event == "CLOSE" and re.match(r"\w*", text).group() not in STOPS:
        fail(f"CLOSE text must begin with one of {', '.join(STOPS)}")
    clock = dt.datetime.now(dt.timezone.utc).strftime("%H:%M")
    return f"- {clock} {event} {actor} {text}".rstrip() + "\n"


def open_run(marker):
    """This session's open log path, or None."""
    log = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    return log if log and os.path.isfile(log) and not is_closed(log) else None


def write(path, text, mode="a"):
    try:
        with open(path, mode, encoding="utf-8") as fh:
            fh.write(text)
    except OSError as exc:
        fail(f"could not write {path}: {exc}\n{text.rstrip()}")


def cmd_init(args, home, marker):
    if current := open_run(marker):
        fail(f"this session already has an open run: {current}\n"
             f'close it with: python3 {SCRIPT} append CLOSE <actor> "incomplete | <reason>"')
    plans = [event_line("PLAN", *(p.split(None, 1) + ["", ""])[:2]) for p in args.plan]
    home.mkdir(parents=True, exist_ok=True)
    write(home / ".gitignore", "*\n", "w")
    for _ in range(3):  # another session may have started a run this same second
        run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        log = home / f"{run_id}.md"
        if not log.exists():
            break
        time.sleep(1)
    goal = " ".join(args.goal.splitlines()).strip()
    write(log, f"# orchestrate run {run_id}\ngoal: {goal}\n\n" + "".join(plans), "x")
    write(marker, f"{log}\n", "w")
    print(log)


def cmd_append(args, home, marker):
    if args.stdin == bool(args.event):
        fail("usage: append EVENT ACTOR TEXT, or append --stdin", 2)
    if args.stdin:
        rows = [(l.split(None, 2) + ["", ""])[:3] for l in sys.stdin.read().splitlines() if l.strip()]
    else:
        rows = [(args.event, args.actor or "", " ".join(args.text))]
    if not rows:
        fail("no event lines on stdin")
    lines = [event_line(*row) for row in rows]
    if any(row[0] == "CLOSE" for row in rows[:-1]):
        fail("CLOSE must be the last line appended")
    if not (log := open_run(marker)):
        fail("no open run for this session; start one with init, or adopt an orphaned log")
    write(log, "".join(lines))
    if rows[-1][0] == "CLOSE":
        marker.unlink()
        print("run closed; release every teammate ListAgents still lists")


def cmd_status(args, home, marker):
    if not (log := open_run(marker)):
        owned = {m.read_text(encoding="utf-8").strip() for m in home.glob("active-*") if m.is_file()}
        orphans = [p for p in sorted(home.glob("*.md")) if str(p) not in owned and not is_closed(p)]
        for path in orphans:
            print(f"no run for this session; adopt with: python3 {SCRIPT} adopt {path}")
        sys.exit(1) if orphans else fail("no open run for this session")
    lines = read_lines(log)
    # In file order: PLAN opens w, RESPAWN closes old and opens new, DONE closes w.
    plan, state, order = {}, {}, []  # state[w]: ("open", replaced-or-None) | ("done", text) | ("gone", None)
    for event, actor, text in (m.groups() for m in map(LINE.match, lines) if m):
        swap = RESPAWN.search(f"{actor} {text}") if event == "RESPAWN" else None
        if swap:
            old, actor = swap.groups()
            state[old], state[actor], plan[actor] = ("gone", None), ("open", old), plan.get(old, "")
        elif event == "PLAN":
            state[actor], plan[actor] = ("open", None), text
        elif event == "DONE":
            state[actor] = ("done", text)
        if (event == "PLAN" or swap) and actor not in order:
            order.append(actor)
    print(next((l for l in lines if l.startswith("goal:")), "goal:"))
    for worker in order:
        kind, info = state[worker]
        head = f"{worker} {plan.get(worker, '').split('|')[0].strip()}".rstrip()
        if kind == "done":
            print(f"{head} — DONE {info}".rstrip())
        elif kind == "open":
            tail = f", replaced {info}" if info else ""
            print(f"{head} — open{tail}" + (f"\n    {plan[worker]}" if plan[worker] else ""))
    print(f"last: {lines[-1] if lines else ''}")


def cmd_adopt(args, home, marker):
    log = Path(args.cwd, args.log).resolve()
    if not log.is_file() or is_closed(log):
        fail(f"cannot adopt {log}: it does not exist or is closed")
    home.mkdir(parents=True, exist_ok=True)
    write(marker, f"{log}\n", "w")
    print(log)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cwd", default=os.getcwd(), help="project directory (default: current)")
    p = sub.add_parser("init", parents=[common], help="start a run; prints the log path")
    p.add_argument("--goal", required=True)
    p.add_argument("--plan", action="append", default=[], help='"ACTOR TEXT"; repeatable')
    p = sub.add_parser("append", parents=[common], help="add event lines to this session's run")
    p.add_argument("event", nargs="?")
    p.add_argument("actor", nargs="?")
    p.add_argument("text", nargs="*")
    p.add_argument("--stdin", action="store_true", help="read EVENT ACTOR TEXT lines from stdin")
    sub.add_parser("status", parents=[common], help="goal, one line per subtask, last line")
    sub.add_parser("adopt", parents=[common], help="point this session's marker at LOG").add_argument("log")
    args = parser.parse_args(argv)
    if not (sid := os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()):
        fail("CLAUDE_CODE_SESSION_ID is not set; run this from a Claude Code Bash tool call", 2)
    home = Path(args.cwd).resolve() / ".orchestrate"
    {"init": cmd_init, "append": cmd_append, "status": cmd_status, "adopt": cmd_adopt}[args.cmd](
        args, home, home / f"active-{sid}")


if __name__ == "__main__":
    main()
