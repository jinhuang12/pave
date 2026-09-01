# Hooks in generated skills — doctrine and the default lead-alignment pair

Read this when a plan chooses a hook as its enforcement mechanism, and when planning or building a workflow whose lead orchestrates across compaction or session boundaries. It carries the hook doctrine for any generated skill, then the standard lead-alignment pair: the invariants any adaptation must preserve, the legitimate omission conditions, and genericized templates to adapt.

## Contents

- Hook doctrine
- Why this pair exists
- The two hooks
- Invariants any adaptation must preserve
- Registration and disclosure
- Legitimate omission conditions
- Templates

## Hook doctrine

A generated skill can ship hooks when its active harness exposes the needed events and payload. Instructions decay as a long-horizon run consumes context; a hook fires on tool events regardless of what any agent still remembers. Choose the enforcement rung first on the spectrum in `pave-spec.md` §9.14 — this section covers the semantic mechanism once a hook is the chosen rung. Use the native lead contract for registration, trust, and wire-format details.

Three uses, in order of preference:

1. **Observing guard.** A `PostToolUse` hook runs a guard script after a matching tool call — for example, validate the run-state file after every write to it. The hook reports; the lead still routes.
2. **Role reinjection.** A hook re-injects the lead contract, the current node, and any standing user directives the run's state records on a schedule — for example, on user-prompt submit or after compaction (SessionStart, compact matcher) — so the orchestrator keeps orchestrating: route, dispatch, verify, and never do worker tasks itself. Size the payload against what actually decays: conversation content and prior hook injections are summarized away, and native skill-body re-injection is capped per skill (~5k tokens), so a generated lead longer than the cap loses its tail sections first.
3. **Blocking guard.** A `PreToolUse` hook or `permissions.deny` rule refuses a matching tool call — for example, an edit before the make-edit-live procedure is established, or `git push` in a skill that must never push. Blocking is the last rung: a wrong match strands the run. Justify it only for a violation that is likely, costly, irreversible before the next required gate, and precisely detectable. When detection can misfire, choose an observing hook plus a later gate.

A hook is a candidate only for an always-on invariant — a rule that must hold on every matching tool call for the whole run. A check that one edge evaluates at a defined moment needs no hook; the routing table already triggers it while the instruction is fresh.

Scoping: tool events fire in every actor's loop — subagents included — so a run-wide guard covers every hand by default, and the actor most likely to violate a prohibition is a worker that never read the lead contract. Three mechanisms narrow a hook to specific actors:

1. **Placement.** Use the narrowest native registration scope the active harness supports. A skill-lifetime hook ends with the skill; a plugin-level hook needs explicit run ownership and terminal gates to preserve the same effective boundary. Use role-scoped registration only when the harness supports it. Otherwise use an identity gate.
2. **Lead-only events.** User prompt submit and session start — including SessionStart's compact matcher, the only event that can inject content after compaction — fire only in the main session. Bind role reinjection there.
3. **Identity gate.** Inside a subagent, hook input carries `agent_type` and `agent_id`; in the main session both are absent. A hook that must ride a tool event but target one actor reads those fields and exits silently otherwise. When every role dispatches through the same generic agent type, `agent_type` cannot tell roles apart — dispatch each role with a distinct Agent-tool `name` and gate on the `agentName` recorded in the transcript the hook input points to.

A worker whose active duty — its own definition of done — decays within its node is mis-sized work: a node-sizing finding (`pave-spec.md` §9.12), not a reinjection target. Its latent standing rules — prohibitions orthogonal to the work in front of it — are different: compaction preserves active focus and summarizes away latent rules and prior hook injections alike. Target reminder machinery by latent-rule count times blast radius, never by wall-clock length: a long single-focus builder needs none; a seat carrying several rarely-exercised prohibitions over irreversible surfaces qualifies.

Two derived applications, both plan-time options recorded in the enforcement record — one entry naming prose and reinjection as rungs of the same rule (persistence after decay, not a second gate):

1. **Dispatch-time check.** A `PreToolUse` hook on the agent-spawn tool, advisory only and edge-triggered: it fires only when run state already records a completed traversal of the target node (a re-entry dispatch) and asks whether the seat's question is already settled by verified on-disk evidence. Every-spawn firing is wallpaper. The advisory must ride `additionalContext` — a reason attached to an allow decision reaches the user, not the model — and any throttle is the hook's own counter file. The same advisory is the natural carrier for the brief-integrity reminder: a dispatch brief renders the graph and run state — facts by evidence key or resolved path, never retyped (`references/approval-briefs.md`). A template ships below; adopting it is a plan-time enforcement-record entry like any other, never a default registration.
2. **Latent-rule reinjection for rule-heavy seats.** Re-present a seat's standing prohibitions when it touches the matching tool class, throttled per window — tool events fire inside subagents, so this survives seat-side compaction no post-compaction event covers. Where the harness cannot gate on seat identity, ship run-wide tool-class reminders armed by the run marker and record the narrowing as a degradation.

Register by native placement first. Prefer a scope that cleans up automatically and treats explicit skill invocation as opt-in. Reach for project settings only for a rule the native hook surface cannot carry, such as a deny rule; ship that as one fragment the generated lead presents at run start behind one bounded approval question, with the decline path stated: which guards degrade to instructions and which prohibitions become review-only.

## Why this pair exists

The recorded failure cause of long-horizon leads is context decay, not disobedience: the lead follows the routing contract until the prose that states it leaves the context window, then invents outcomes and edges the graph never declared. Role reinjection on user events (UserPromptSubmit, SessionStart — including its compact matcher; PreCompact cannot inject, its output is discarded) covers most of a run. Two windows stay dark:

1. **The decision to stop.** A Stop with an active, non-terminal run is the highest-risk decay moment: the campaign silently stalls at its resume point, and no user event fires to re-inject anything.
2. **Long autonomous stretches.** Many tool calls with no user prompt and no compaction means zero reinjection; outcomes happen and never reach run state.

The pair covers exactly these two windows, socratically. It asks, never commands, because valid stops and mid-node quiet stretches are common.

This pair is one pre-derived answer to one universal failure mode. No two workflows share a failure surface: derive any further enforcement from the workflow's own evidence the same way, and size it on the same spectrum (`references/pave-spec.md` §9.14). A cwd-drift warning or a budget-burn alert may be justified by one workflow's field evidence — and neither belongs in a workflow whose evidence does not name the failure.

## The two hooks

| hook | event | rung | shape |
|---|---|---|---|
| stop-alignment check | Stop | socratic reinjection | Blocks a stop ONCE with the questions (why did you stop; is that the aligned action; if not, which DECLARED action advances the run; are finished subagents retired?), then a cooldown counter lets the next N−1 stops pass silently (default N=3: at most one nudge per 3 stops). |
| state-staleness reminder | PostToolUse (`Bash\|Write\|Edit`) | observing / socratic | When the run-state file's mtime exceeds a threshold while tools keep running, injects one throttled `additionalContext` question: which node are you actually in, and has an outcome occurred that is not recorded? |

## Invariants any adaptation must preserve

Stop hooks have no non-blocking channel: `additionalContext` is dropped, so the questions can only be delivered by blocking once (exit 2). That makes these invariants load-bearing. Losing the circuit breaker is the one catastrophic adaptation error: an infinite stop loop.

- **Cooldown circuit breaker**: a session-keyed marker file holding a countdown; the first stop nudges and writes N−1 (default N=3, one env-overridable knob, minimum 2 — the breaker needs at least one free pass after a nudge), each following stop decrements it and passes, and the stop after the marker is spent nudges again. A payload with `stop_hook_active` set short-circuits immediately.
- **Silent when stopping is correct**: no active run state found, or the run's terminal field is set. Do not try to enumerate every valid stop — the questions plus the breaker handle pending user gates and background waits at the cost of at most one bounce.
- **Marker-authoritative discovery**: the hooks act only on run state found via the ownership marker the lead writes at run start (`FOUND_STATE_VIA` = `marker`). A newest-by-mtime scan hit may belong to an abandoned run or a different session — blocking or nudging on it fires in sessions that do not own the run, which is the one detection misfire this design must exclude. The scan fallback exists only for lead-driven resume discovery, where judgment applies. Corollary: the generated state protocol must give the lead an abandon/pause duty (set the terminal field, or remove the state) so a walked-away run cannot stay "active" forever.
- **Staleness stays observing**: always exit 0; mtime-based (stateless), one env-overridable threshold, throttled to once per window per session, skipped for subagent payloads (`agent_type`/`agent_id` present — only the lead can act on it) and for terminal runs.
- **Fail open everywhere**: missing interpreter, unparsable payload or state → silent exit 0. The payload case must be explicit: emit a distinct parse sentinel and exit on it — defaulting a failed parse to an empty dict is indistinguishable from a minimal valid payload, and the hook will act on input it could not read. These hooks align; they must never strand a run.
- **Lead-only scope**: Stop never fires in a subagent (that is SubagentStop — do not register it); the staleness hook gates on payload identity.

What must be re-derived per workflow: the run-state discovery (marker file name, runs-directory glob), the terminal-classification field, the names of the routing section and state-write protocol the messages point at, and the env-var prefix.

## Registration and disclosure

Register both in the generated skill's frontmatter (invoking the skill is the opt-in; the hooks live and die with it). The generated `description` must disclose the blocks-a-stop behavior and its cadence (at most one block per N stops) — nothing registers silently — and `description` has a 1024-character budget, so disclose compactly. Each hook records its decline path (hook runtime unavailable): the stop check degrades to the lead's resume duty; the staleness reminder degrades to the checkpoint duty in the state-write protocol.

## Legitimate omission conditions

Record ONE of these in the enforcement record instead of the pair. Silent omission is a review finding, and so is importing the pair where a condition below holds:

- **No persisted run state.** The staleness hook has no signal; the stop hook has no resume point to defend. (If the workflow is long-horizon and has no persisted state, that is the defect to fix first.)
- **Single-session, short-horizon.** The run cannot cross a compaction or session boundary; the decay window the pair covers does not exist.
- **Every node is a user gate.** Stopping is almost always correct, so the one bounce is pure friction with nothing to catch.
- **No lead orchestrator.** Nothing long-horizon exists to align.
- **Hooks runtime unavailable** in the target environment — record the degradation, not just the omission.

## Templates

Adapt these three files into the generated skill's `hooks/`. `ADAPT:` marks every workflow-specific point. Test the invariants, not the wording: a nudge is followed by N−1 silent passes and the stop after that nudges again, `stop_hook_active` short-circuits, terminal runs are silent, staleness fires once then throttles, subagent payloads are skipped, unparsable payloads and empty stdin fail open, and state discovered without the ownership marker leaves both hooks silent.

### `hooks/_find_run_state.sh` (shared discovery)

```bash
#!/usr/bin/env bash
# Sourceable run-state discovery shared by the reinjection hooks.
# Sets FOUND_STATE ("" when none) and FOUND_STATE_LABEL.
# Prefers the active-run marker the lead writes at run start (one line: the
# absolute path of the live state file); falls back to the newest run
# directory by mtime, labeled as possibly belonging to a different run.
if ! declare -F find_run_state >/dev/null 2>&1; then
find_run_state() {
  local skill_dir="${1:-}"
  FOUND_STATE=""
  FOUND_STATE_LABEL="active run"
  FOUND_STATE_VIA=""
  # Quoted iteration: root paths may contain spaces.
  local root marker candidate
  local project_root="${PROJECT_ROOT:-}"  # ADAPT: bind to the native project-root environment value
  for root in "$project_root" "$(cd "$skill_dir/../.." 2>/dev/null && pwd)" "$PWD"; do
    [ -n "$root" ] || continue
    marker="$root/.<workflow-name>-run"            # ADAPT: marker file name
    if [ -f "$marker" ]; then
      candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        FOUND_STATE="$candidate"; FOUND_STATE_VIA="marker"; return 0
      fi
    fi
  done
  # Scan fallback: NOT ownership evidence — the hooks act only on
  # FOUND_STATE_VIA="marker". The scan exists for lead-driven resume.
  FOUND_STATE_LABEL="newest run by mtime — may not be this run"
  for root in "$project_root" "$(cd "$skill_dir/../.." 2>/dev/null && pwd)" "$PWD"; do
    [ -n "$root" ] || continue
    [ -d "$root/<runs-dir>" ] || continue           # ADAPT: runs directory
    candidate="$(ls -t "$root"/<runs-dir>/*/run-state.json 2>/dev/null | head -n 1)"  # ADAPT: state file name
    if [ -n "$candidate" ]; then FOUND_STATE="$candidate"; FOUND_STATE_VIA="scan"; return 0; fi
  done
  return 0
}
fi
```

### `hooks/stop_alignment_check.sh` (Stop)

```bash
#!/usr/bin/env bash
# Stop-alignment socratic check. Blocks a stop ONCE (Stop hooks have no
# non-blocking channel), then a cooldown counter lets the next STOP_EVERY-1
# stops pass (default 3: at most one nudge per 3 stops). Silent when no
# active run or the run is terminal.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SKILL_DIR="$(cd "$HOOK_DIR/.." && pwd)"
PY="${<PREFIX>_PYTHON:-python3}"                    # ADAPT: env-var prefix
PY="${PY/#\~\//$HOME/}"
TAG="[stop_alignment_check]"
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys
try: payload = json.loads(sys.stdin.read())
except Exception: print("PARSE_FAIL"); sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print("1" if payload.get("stop_hook_active") else "0")
' 2>/dev/null || true)"
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0  # fail open on unreadable input
SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"; [ -n "$SESSION_ID" ] || SESSION_ID=default
[ "$(printf '%s\n' "$FIELDS" | sed -n 3p)" = "1" ] && exit 0   # never loop

. "$HOOK_DIR/_find_run_state.sh"
find_run_state "$SKILL_DIR"
[ -n "$FOUND_STATE" ] && [ -f "$FOUND_STATE" ] || exit 0
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0   # scan hit is not ownership; never block on it

STOP_EVERY="${<PREFIX>_STOP_EVERY:-3}"                  # ADAPT: env-var prefix
case "$STOP_EVERY" in *[!0-9]*|''|0|1) STOP_EVERY=3 ;; esac  # breaker needs >=2
MARKER="${TMPDIR:-/tmp}/<workflow-name>-stop-nudged-${SESSION_ID}"   # ADAPT
if [ -f "$MARKER" ]; then                               # cooldown: pass and count down
  LEFT="$(head -n 1 "$MARKER" 2>/dev/null)"
  case "$LEFT" in *[!0-9]*|'') LEFT=1 ;; esac
  if [ "$LEFT" -gt 1 ]; then printf '%s\n' "$((LEFT - 1))" > "$MARKER" 2>/dev/null || true
  else rm -f "$MARKER"; fi
  exit 0
fi

SUMMARY="$("$PY" - "$FOUND_STATE" <<'PYEOF' 2>/dev/null
import json, os, sys, time
path = sys.argv[1]
try: state = json.load(open(path, encoding="utf-8"))
except Exception: sys.exit(0)   # unparsable state is the validator hook's business
terminal = state.get("terminal_classification")   # ADAPT: terminal field
if isinstance(terminal, dict) and terminal.get("status"):
    print("TERMINAL"); sys.exit(0)
history = [e for e in (state.get("traversal_history") or []) if isinstance(e, dict)]
last = history[-1] if history else {}
print("ACTIVE")
print((state.get("run_identity") or {}).get("run_id", "<unset>"))
print(state.get("restart_from") or "<unset>")     # ADAPT: resume field
print("%s.%s" % (last.get("node", "<none>"), last.get("outcome", "<none>")))
print(int((time.time() - os.path.getmtime(path)) / 60))
PYEOF
)" || exit 0
[ "$(printf '%s\n' "$SUMMARY" | sed -n 1p)" = "ACTIVE" ] || exit 0
RUN_ID="$(printf '%s\n' "$SUMMARY" | sed -n 2p)"
RESTART="$(printf '%s\n' "$SUMMARY" | sed -n 3p)"
LAST="$(printf '%s\n' "$SUMMARY" | sed -n 4p)"
AGE_MIN="$(printf '%s\n' "$SUMMARY" | sed -n 5p)"
printf '%s\n' "$((STOP_EVERY - 1))" > "$MARKER" 2>/dev/null || true
cat >&2 <<EOF
$TAG Active run $RUN_ID ($FOUND_STATE_LABEL): restart_from=$RESTART, last traversal $LAST, run state last written ${AGE_MIN} min ago.

You decided to stop. Socratic check -- answer to yourself, then act:
  a. Why did you stop, and is stopping the most aligned action in the current state?
  b. If not, which DECLARED next action advances the run from $RESTART?
     Re-read the routing section; emit only declared outcomes over declared edges.
  c. Are any subagents or teammates done or no longer needed? Retire them
     now -- an idle agent costs tokens, and a late message from one can
     derail the run.

Valid reasons to stop exist: a pending user decision, waiting on a background
subagent, a recorded pause, a closed terminal. If stop is the right call, stop
again -- the next $((STOP_EVERY - 1)) stops pass through before this check
fires again.
EOF
exit 2
```

### `hooks/state_staleness_reminder.sh` (PostToolUse `Bash|Write|Edit`)

```bash
#!/usr/bin/env bash
# State-staleness socratic reminder. Observing: always exit 0. Fires when the
# run-state file has not been written for STALE_SECONDS while tools keep
# running; throttled once per window per session; skips subagents and
# terminal runs.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SKILL_DIR="$(cd "$HOOK_DIR/.." && pwd)"
PY="${<PREFIX>_PYTHON:-python3}"; PY="${PY/#\~\//$HOME/}"   # ADAPT
TAG="[state_staleness_reminder]"
STALE_SECONDS="${<PREFIX>_STALE_SECONDS:-900}"              # ADAPT
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys
def find(node, key):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], (str, int)): return str(node[key])
        for v in node.values():
            f = find(v, key)
            if f: return f
    elif isinstance(node, list):
        for item in node:
            f = find(item, key)
            if f: return f
    return ""
try: payload = json.loads(sys.stdin.read())
except Exception: print("PARSE_FAIL"); sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print(find(payload, "agent_type").replace("\n", " "))
print(find(payload, "agent_id").replace("\n", " "))
' 2>/dev/null || true)"
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0  # fail open on unreadable input
SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"; [ -n "$SESSION_ID" ] || SESSION_ID=default
case "$(printf '%s\n' "$FIELDS" | sed -n 3p)" in ""|main|lead|root|primary) ;; *) exit 0 ;; esac
[ -n "$(printf '%s\n' "$FIELDS" | sed -n 4p)" ] && exit 0   # subagent: cannot act

. "$HOOK_DIR/_find_run_state.sh"
find_run_state "$SKILL_DIR"
[ -n "$FOUND_STATE" ] && [ -f "$FOUND_STATE" ] || exit 0
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0   # scan hit is not ownership; do not nudge about it

NOW="$(date +%s)"
STATE_MTIME="$(stat -f %m "$FOUND_STATE" 2>/dev/null || stat -c %Y "$FOUND_STATE" 2>/dev/null || echo "$NOW")"
[ $(( NOW - STATE_MTIME )) -ge "$STALE_SECONDS" ] || exit 0
THROTTLE="${TMPDIR:-/tmp}/<workflow-name>-stale-nudged-${SESSION_ID}"   # ADAPT
if [ -f "$THROTTLE" ]; then
  LAST="$(stat -f %m "$THROTTLE" 2>/dev/null || stat -c %Y "$THROTTLE" 2>/dev/null || echo 0)"
  [ $(( NOW - LAST )) -ge "$STALE_SECONDS" ] || exit 0
fi
touch "$THROTTLE" 2>/dev/null || true

"$PY" - "$FOUND_STATE" "$FOUND_STATE_LABEL" "$TAG" "$(( NOW - STATE_MTIME ))" "$STALE_SECONDS" <<'PYEOF' 2>/dev/null || exit 0
import json, sys
path, label, tag, age, window = sys.argv[1:6]
try: state = json.load(open(path, encoding="utf-8"))
except Exception: sys.exit(0)
terminal = state.get("terminal_classification")   # ADAPT: terminal field
if isinstance(terminal, dict) and terminal.get("status"): sys.exit(0)
history = [e for e in (state.get("traversal_history") or []) if isinstance(e, dict)]
last = history[-1] if history else {}
text = (
    "%s run state last written %d min ago (run %s, %s; state %s). It says "
    "restart_from=%s; last traversal %s.%s. Socratic check: which node are you "
    "ACTUALLY in right now, and has any outcome occurred since that entry that is "
    "not recorded? If yes, record the traversal now per the state-write protocol "
    "and index any evidence artifacts at their declared paths. If you are mid-node "
    "with nothing to record, continue; this fires at most once per %d min."
    % (tag, int(age) // 60, (state.get("run_identity") or {}).get("run_id", "<unset>"),
       label, path, state.get("restart_from") or "<unset>",
       last.get("node", "<none>"), last.get("outcome", "<none>"), int(window) // 60)
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                         "additionalContext": text}}))
PYEOF
exit 0
```

### `hooks/dispatch_advisory.sh` (PreToolUse `Agent|Task`) — optional, plan-time adoption only

```bash
#!/usr/bin/env bash
# Dispatch advisory (OPTIONAL). Adopt only through an enforcement-record entry;
# never register by default. PreToolUse on the agent-spawn tool. Advisory only:
# always exits 0, and the message rides additionalContext, never a permission
# decision. Edge-triggered: fires only when run state already records a
# completed traversal of the dispatch's target node (a re-entry dispatch) —
# every-spawn firing is wallpaper. Throttled by its own counter file.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
. "$HOOK_DIR/_find_run_state.sh"            # defines find_run_state
find_run_state                              # sets FOUND_STATE ("" when none)
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0   # scan hit is not ownership; stay silent
PAYLOAD="$(cat 2>/dev/null || true)"
# Counter keyed to the run's state path — every run's file is named the same,
# so basename alone would share one counter across runs.
COUNTER="${TMPDIR:-/tmp}/dispatch-advisory-$(printf %s "$FOUND_STATE" | cksum | cut -d' ' -f1).count"  # ADAPT: name per skill
HOOK_PAYLOAD="$PAYLOAD" python3 - "$FOUND_STATE" "$COUNTER" <<'PYEOF' 2>/dev/null || exit 0
import json, os, sys
state_path, counter = sys.argv[1], sys.argv[2]
try:
    payload = json.loads(os.environ.get("HOOK_PAYLOAD") or "{}")
    state = json.load(open(state_path, encoding="utf-8"))
except Exception:
    sys.exit(0)                                  # advisory: always fail open
terminal = state.get("terminal_classification")  # ADAPT: terminal field
if isinstance(terminal, dict) and terminal.get("status"):
    sys.exit(0)
prompt = str((payload.get("tool_input") or {}).get("prompt") or "")
history = [e for e in (state.get("traversal_history") or [])  # ADAPT: history field
           if isinstance(e, dict)]
done = {str(e.get("node")) for e in history if e.get("node")}
target = next((n for n in sorted(done, key=len, reverse=True) if n in prompt), None)
if not target:
    sys.exit(0)                                  # first-entry dispatch: silent
try:
    n = int(open(counter).read().strip() or 0)
except Exception:
    n = 0
if n >= 3:                                       # at most 3 nudges per run
    sys.exit(0)
open(counter, "w").write(str(n + 1))
text = (
    "[dispatch-advisory] This brief targets %r, a node this run already "
    "traversed. Before the seat starts: (1) is its question already settled by "
    "verified on-disk evidence? A mechanically knowable answer is lead routing "
    "work, not a seat. (2) The brief is a rendered view: facts by evidence key "
    "or resolved path, outcome tokens copied from the node's own list, counts "
    "pointed at rather than retyped; the artifact wins when the two disagree."
    % target
)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                         "additionalContext": text}}))
PYEOF
exit 0
```

A worked, tested instance of the default pair and its shared discovery (with a test section covering every invariant above) exists in any skill this reference generated; the templates here are the transferable shape, and the dispatch advisory ships only where a plan's enforcement record adopts it.
