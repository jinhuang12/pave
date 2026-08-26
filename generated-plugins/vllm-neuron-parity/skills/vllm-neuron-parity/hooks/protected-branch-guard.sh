#!/usr/bin/env bash
# P1 -- Never mutate protected base branches (release-0.24.0.1.1.0,
# release-0.21.0.1.0.0, main, mainline) on the fork or upstream.
#
# Enforcement rung: BLOCKING PreToolUse hook (enforcement-record.md §1, P1).
# Blocking is justified there because the violation is likely (every seat runs
# git constantly), costly, irreversible before any gate, and precisely
# detectable by branch-name match.
#
# Event: PreToolUse, matcher "Bash". Fires in every actor's loop -- lead,
# teammate, and one-shot sub-agent -- because P1 is a run-wide prohibition
# with no legitimate actor.
#
# Exit semantics (doctrine: references/lead-alignment-hooks.md "Hook
# doctrine"): exit 2 with the reason on stderr refuses the call; exit 0
# allows. FAIL OPEN everywhere -- missing interpreter, unreadable payload,
# unparsable command -> silent exit 0. A guard must never strand a run.
set -uo pipefail
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
command -v "$PY" >/dev/null 2>&1 || exit 0
PAYLOAD="$(cat 2>/dev/null || true)"

REASON="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, re, shlex, sys

PROTECTED = {
    "release-0.24.0.1.1.0",
    "release-0.21.0.1.0.0",
    "main",
    "mainline",
}

try:
    payload = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)                      # unreadable payload: fail open
if not isinstance(payload, dict):
    sys.exit(0)
if payload.get("tool_name") not in (None, "Bash"):
    sys.exit(0)
command = ((payload.get("tool_input") or {}).get("command") or "")
if not isinstance(command, str) or not command.strip():
    sys.exit(0)


def segments(text):
    """Split a shell line into command segments on separators, then tokenize."""
    parts = re.split(r"(?:\|\||&&|[;\n|&])", text)
    out = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        try:
            toks = shlex.split(part, comments=True)
        except ValueError:
            continue                 # unbalanced quoting: skip, fail open
        if toks:
            out.append(toks)
    return out


def git_words(toks):
    """Return the git subcommand words, or None when this is not a git call."""
    i = 0
    while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
        i += 1                       # skip VAR=value prefixes
    if i < len(toks) and toks[i] in ("sudo", "env", "command", "nice", "time"):
        i += 1
        while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
            i += 1
    if i >= len(toks):
        return None
    exe = toks[i].rsplit("/", 1)[-1]
    if exe != "git":
        return None
    return toks[i + 1:]


def refspec_branch(tok):
    """Branch a push refspec would write, or None."""
    t = tok.lstrip("+")
    if ":" in t:
        dst = t.split(":", 1)[1]
    else:
        dst = t
    dst = dst.strip()
    if not dst:
        return None
    return dst.rsplit("/", 1)[-1] if dst.startswith("refs/heads/") else dst


hits = []
for toks in segments(command):
    words = git_words(toks)
    if words is None:
        continue
    # Skip leading git-level options (-C <dir>, -c k=v, --git-dir=...).
    i = 0
    while i < len(words) and words[i].startswith("-"):
        if words[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2
        else:
            i += 1
    if i >= len(words):
        continue
    sub = words[i]
    rest = words[i + 1:]

    if sub == "push":
        flags = [w for w in rest if w.startswith("-")]
        operands = [w for w in rest if not w.startswith("-")]
        if any(f in ("--mirror", "--all") for f in flags):
            hits.append("git push %s pushes every local branch, protected bases "
                        "included" % " ".join(f for f in flags
                                              if f in ("--mirror", "--all")))
            continue
        delete = any(f in ("--delete", "-d") for f in flags)
        # operands[0] is the remote when more than one operand is present.
        specs = operands[1:] if len(operands) > 1 else []
        for spec in specs:
            if spec.startswith(":"):          # git push origin :main
                b = refspec_branch(spec[1:])
                if b in PROTECTED:
                    hits.append("git push deletes protected branch %r" % b)
                continue
            b = refspec_branch(spec)
            if b in PROTECTED:
                hits.append("git push %s protected branch %r"
                            % ("deletes" if delete else "writes", b))
        if not specs and not delete:
            # No explicit refspec: git pushes the current branch. Resolve it in
            # the cwd from the payload: still a branch-name match, and it fails open
            # when git cannot answer (no repo, detached HEAD, missing binary),
            # when the command changes directory itself (cd ... && git push, or
            # git -C ... push -- resolution happens in the payload cwd, not the
            # effective directory of the command), or under a non-default
            # push.default that pushes a differently named remote ref. Recorded
            # residual; the explicit-refspec and mutation arms above are exact
            # matches.
            import subprocess
            cwd = payload.get("cwd") or None
            if cwd is None:
                # No payload cwd: resolving in the directory of the hook process
                # would be wrong-repo evidence in both directions -- skip, fail open.
                name = ""
            else:
                try:
                    cur = subprocess.run(
                        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
                        cwd=cwd, capture_output=True, text=True, timeout=5)
                    name = cur.stdout.strip() if cur.returncode == 0 else ""
                except Exception:
                    name = ""
            if name in PROTECTED and name:
                hits.append("git push with no refspec would push the currently "
                            "checked-out protected branch %r" % name)
    elif sub == "branch":
        mutating = False
        for w in rest:
            if w.startswith("--"):
                if w in ("--delete", "--force", "--move", "--copy"):
                    mutating = True
            elif w.startswith("-") and len(w) > 1:
                if set(w[1:]) & set("dDfmMcC"):
                    mutating = True
        if mutating:
            for w in rest:
                if not w.startswith("-") and refspec_branch(w) in PROTECTED:
                    hits.append("git branch mutates protected branch %r"
                                % refspec_branch(w))
    elif sub in ("update-ref", "symbolic-ref"):
        for w in rest:
            if w.startswith("refs/heads/") and w.rsplit("/", 1)[-1] in PROTECTED:
                hits.append("git %s rewrites protected ref %r" % (sub, w))

if hits:
    print("; ".join(sorted(set(hits))))
' 2>/dev/null || true)"

[ -n "$REASON" ] || exit 0
cat >&2 <<EOF
[protected-branch-guard] BLOCKED by prohibition P1: $REASON.

Protected base branches (fork and upstream) are never mutated by this
workflow: release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline.

Do this instead: push the campaign branch and open the PR on the
jinhuang12/vllm-neuron fork (close_campaign's only allowed effect). Merging a
PR is the human's, and fork sync with upstream is the user's. If a protected
base genuinely must move, that is a user decision at a gate -- report it and
stop; do not route around this guard.
EOF
exit 2
