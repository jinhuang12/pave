#!/usr/bin/env bash
# P3 -- No `cp -a` venv cloning; no pip writes into /opt.
#
# Enforcement rung: BLOCKING PreToolUse hook (enforcement-record.md §1, P3).
# Same shape as P2: the venv-replication dead-end pressure makes the shortcut
# likely (the run-wide reflection of realize_increment's addition), /opt damage
# breaks co-tenants, and both patterns are precise.
#
# Event: PreToolUse, matcher "Bash". Run-wide across lead, custom agents, and
# one-shot sub-agents.
#
# Exit semantics: exit 2 with the reason on stderr refuses the call; exit 0
# allows. FAIL OPEN on missing interpreter, unreadable payload, or unparsable
# command.
set -uo pipefail
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
command -v "$PY" >/dev/null 2>&1 || exit 0
PAYLOAD="$(cat 2>/dev/null || true)"

REASON="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, re, shlex, sys

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

VENV = re.compile(r"(?:^|[/\s=])(?:\.?venv[\w.-]*|virtualenvs?|"
                  r"[\w.-]*[-_]venv)(?:/|$)")
OPT = re.compile(r"^/opt(?:/|$)")
PIP_DIRS = ("--target", "-t", "--prefix", "--root", "--install-option",
            "--build", "--src")


def segments(text):
    out = []
    for part in re.split(r"(?:\|\||&&|[;\n|&])", text):
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


def head(toks):
    """Return (executable basename, full executable path, remaining tokens)."""
    i = 0
    while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
        i += 1
    if i < len(toks) and toks[i] in ("sudo", "env", "command", "nice", "time"):
        i += 1
        while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
            i += 1
    if i >= len(toks):
        return "", "", []
    return toks[i].rsplit("/", 1)[-1], toks[i], toks[i + 1:]


hits = []
for toks in segments(command):
    exe, exe_path, rest = head(toks)

    # (a) venv cloning by archive copy. `cp -a`, and the -R/-r/-p forms that
    # clone a venv just as unusably (absolute shebangs, baked paths).
    # Only the copy mechanisms the prohibition names: `cp` and its rsync
    # equivalent. Archiving a venv for evidence is not the violation.
    if exe in ("cp", "rsync"):
        archive = False
        for w in rest:
            if w.startswith("--"):
                if w in ("--archive", "--recursive", "--preserve",
                         "--preserve=all", "--no-dereference"):
                    archive = True
            elif w.startswith("-") and len(w) > 1 and set(w[1:]) & set("aRrp"):
                archive = True
        venv_toks = [w for w in rest if not w.startswith("-") and VENV.search(w)]
        if archive and venv_toks:
            hits.append("`%s` clones a virtualenv (%s) -- a copied venv carries "
                        "absolute paths and is a known dead end"
                        % (exe, venv_toks[0]))

    # (b) pip writes into /opt. Either an explicit destination flag under /opt,
    # or a pip/python whose own prefix is /opt with no destination elsewhere.
    is_pip = exe in ("pip", "pip3") or (
        exe.startswith("python") and rest[:2] == ["-m", "pip"]) or (
        exe == "uv" and rest[:1] == ["pip"])
    if is_pip and any(a in ("install", "uninstall", "download", "wheel")
                      for a in rest):
        args = rest
        dest = None
        explicit_dest = False
        for idx, w in enumerate(args):
            key, _, inline = w.partition("=")
            if key in PIP_DIRS:
                val = inline if inline else (args[idx + 1]
                                             if idx + 1 < len(args) else "")
                if val:
                    explicit_dest = True
                    if OPT.match(val):
                        dest = val
        if dest:
            hits.append("pip writes into %s" % dest)
        elif not explicit_dest and OPT.match(exe_path):
            hits.append("pip run from %s installs into the /opt environment"
                        % exe_path)
        else:
            for w in args:
                key, _, inline = w.partition("=")
                if key == "PYTHONUSERBASE" and OPT.match(inline):
                    hits.append("pip writes into %s via PYTHONUSERBASE" % inline)

    # (c) the same /opt write reached through an env-var prefix.
    for w in toks:
        key, sep, val = w.partition("=")
        if sep and key in ("PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX",
                           "PIP_ROOT") and OPT.match(val):
            hits.append("%s=%s directs a package install into /opt" % (key, val))

if hits:
    print("; ".join(sorted(set(hits))))
' 2>/dev/null || true)"

[ -n "$REASON" ] || exit 0
cat >&2 <<EOF
[venv-opt-guard] BLOCKED by prohibition P3: $REASON.

Two shortcuts are closed here: cloning a virtualenv by archive copy, and
writing packages into /opt. A cloned venv breaks on absolute paths and burns
attempts on a fault that is not the campaign's; /opt is co-tenant territory and
the damage is not yours to undo.

Do this instead: build the campaign environment the way replicate_campaign_venv
declares -- create it fresh and install from the pinned requirement set, in a
path you own under the campaign worktree or lease scope. If replication keeps
failing, that is replication_failed or host_faulted: take the declared route.
EOF
exit 2
