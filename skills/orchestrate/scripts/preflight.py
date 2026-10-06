#!/usr/bin/env python3
"""Pre-flight for the orchestrate skill.

Teammates open in the lead's working directory. If that directory has never
been trusted in ~/.claude.json, every teammate stalls at the "trust this
directory" dialog and never reads its inbox, so the whole team looks alive
but silent. Run this before spawning; pass --fix to mark the cwd trusted.

Usage: python3 preflight.py [--fix] [--cwd PATH]
Exit 0 when ready, 1 when something still blocks spawning.

Caveat: when cwd is your home directory, Claude Code itself only ever grants
home trust for the current session. --fix persists it, which is what makes
teammates spawned from ~ start cleanly, but it is a permanent trust grant for
everything under home. Run orchestrations from a project directory when you
would rather not have that.
"""
import argparse, json, os, shutil, sys, time

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="set hasTrustDialogAccepted for cwd")
    ap.add_argument("--cwd", default=os.getcwd())
    a = ap.parse_args()
    ok = True

    teams = os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", "")
    print(f"agent teams env: {'on' if teams == '1' else 'OFF (named spawns will be in-process subagents; expect to broker)'}")

    cfg = os.path.expanduser("~/.claude.json")
    try:
        with open(cfg) as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"cannot read {cfg}: {e}")
        return 1

    cwd = os.path.realpath(a.cwd)
    projects = data.setdefault("projects", {})
    entry = projects.get(cwd) or projects.get(a.cwd) or {}
    trusted = bool(entry.get("hasTrustDialogAccepted"))
    print(f"trust for {cwd}: {'accepted' if trusted else 'NOT accepted'}")

    if not trusted:
        if cwd == os.path.realpath(os.path.expanduser("~")):
            print("note: cwd is your home directory; --fix persists home trust, which Claude Code normally keeps session-only")
        if a.fix:
            shutil.copy2(cfg, f"{cfg}.bak-{int(time.time())}")
            entry = projects.setdefault(cwd, entry)
            entry["hasTrustDialogAccepted"] = True
            tmp = cfg + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, cfg)
            print("fixed: hasTrustDialogAccepted=true written (backup kept next to the file)")
        else:
            print("blocker: teammates will stall at the trust dialog. Re-run with --fix, or accept the dialog in each pane.")
            ok = False

    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
