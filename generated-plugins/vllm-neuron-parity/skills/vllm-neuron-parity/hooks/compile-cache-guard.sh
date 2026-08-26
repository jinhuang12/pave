#!/usr/bin/env bash
# P2 -- Never clear the shared Neuron compile cache:
#   $VLLM_CACHE_ROOT/neuron/compile_cache
#   ~/.cache/vllm/neuron/compile_cache
#   /var/tmp/neuron-compile-cache
#
# Enforcement rung: BLOCKING PreToolUse hook (enforcement-record.md §1, P2),
# paired with the delegate guardrail wrapper. Blocking is justified there
# because documented serving bring-up remedies INCLUDE cache clearing, so the
# violation is likely under delegation; a cleared cache costs every tenant
# hours of recompile; and the path patterns are precise.
#
# Event: PreToolUse, matcher "Bash". Run-wide: fires in the lead, in every
# named teammate, and in every one-shot sub-agent -- the actor most likely to
# try a cache clear is a delegate that never read the lead contract.
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

# The three protected cache roots, matched by shape so that an unexpanded
# $VLLM_CACHE_ROOT, a ~ prefix, and an absolute expansion all hit.
CACHE_PATTERNS = (
    re.compile(r"neuron/+compile_cache"),          # both vllm cache roots
    re.compile(r"neuron-compile-cache"),           # /var/tmp form
    # The cache root itself, or the root with a glob/neuron tail: removing it
    # removes the cache. A sibling path under the root (logs, weights) is not
    # P2 and is deliberately not matched.
    re.compile(r"VLLM_CACHE_ROOT\}?/*(?:\*|neuron/?\*?)?$"),
)
DESTRUCTIVE = {
    "rm": "removes", "rmdir": "removes", "unlink": "removes",
    "shred": "destroys", "truncate": "truncates", "mv": "moves away",
    "trash": "removes",
}


def touches_cache(tok):
    if not isinstance(tok, str):
        return False
    return any(p.search(tok) for p in CACHE_PATTERNS)


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
    i = 0
    while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
        i += 1
    if i < len(toks) and toks[i] in ("sudo", "env", "command", "nice", "time",
                                     "xargs"):
        i += 1
        while i < len(toks) and ("=" in toks[i] and not toks[i].startswith("-")):
            i += 1
    if i >= len(toks):
        return "", []
    return toks[i].rsplit("/", 1)[-1], toks[i + 1:]


hits = []
for toks in segments(command):
    exe, rest = head(toks)
    cache_toks = [t for t in rest if touches_cache(t)]
    if exe in DESTRUCTIVE and cache_toks:
        hits.append("`%s` %s the shared Neuron compile cache (%s)"
                    % (exe, DESTRUCTIVE[exe], cache_toks[0]))
    elif exe == "find" and cache_toks:
        if any(f in rest for f in ("-delete", "-exec", "-execdir", "-ok")):
            hits.append("`find ... -delete/-exec` clears the shared Neuron "
                        "compile cache (%s)" % cache_toks[0])
    elif exe in ("git",) and rest[:1] == ["clean"] and cache_toks:
        hits.append("`git clean` clears the shared Neuron compile cache (%s)"
                    % cache_toks[0])
    elif exe == "rsync" and cache_toks and any(
            t.startswith("--delete") for t in rest):
        hits.append("`rsync --delete` clears the shared Neuron compile cache "
                    "(%s)" % cache_toks[0])

if hits:
    print("; ".join(sorted(set(hits))))
' 2>/dev/null || true)"

[ -n "$REASON" ] || exit 0
cat >&2 <<EOF
[compile-cache-guard] BLOCKED by prohibition P2: $REASON.

The Neuron compile cache is shared with every co-tenant on the host. Clearing
it costs everyone hours of recompilation, and it is irreversible. Protected
roots: \$VLLM_CACHE_ROOT/neuron/compile_cache,
~/.cache/vllm/neuron/compile_cache, /var/tmp/neuron-compile-cache.

Do this instead: a bring-up remedy that reads "clear the compile cache" is
intercepted, not followed. Point the run at a private cache root you own for
this campaign, or record the recompile-suspected symptom as an attempt-record
observation and take a declared route (host recovery, or the breaker into
re-derivation). Never widen the blast radius to shared state.
EOF
exit 2
