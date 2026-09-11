# Hooks in generated skills — doctrine and the default lead-alignment pair

Read this when a plan chooses a hook as its enforcement mechanism, and when planning or building a workflow whose lead orchestrates across compaction or session boundaries. It carries the hook doctrine for any generated skill, then the standard lead-alignment pair: the invariants any adaptation must preserve, the legitimate omission conditions, and genericized templates to adapt.

## Contents

- Hook doctrine
- Why this pair exists
- The hooks
- The audit-due branch, the write log, and the two write guards
- Enforcement-record entries for the audit path
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

Scoping: settings- and plugin-level hooks fire in every actor's loop — subagents included — so a run-wide guard at that placement covers every hand by default, and the actor most likely to violate a prohibition is a worker that never read the lead contract. A skill-frontmatter hook fires only for the agent that invoked the skill: it never sees a subagent's tool call (measured with a logging hook in each placement; the harness docs name settings, managed policy, and plugins as the sources that run inside subagents). Three mechanisms narrow a hook to specific actors:

1. **Placement.** Register by the actor the hook watches. A hook only the lead must trigger — a lead-only event, or a tool-event hook that acts on the lead's own writes — goes in skill frontmatter: it ends with the skill, and invoking the skill is the opt-in. A hook that must see a subagent's tool call goes in the plugin-level hook registration, armed by the run marker and silent once the run records a terminal status. That boundary is wider than skill frontmatter's: any session in the repository whose marker points at a live run state is in scope, not only the session that invoked the skill — the price of seeing subagent writes, so keep such a hook advisory and marker-gated. Use role-scoped registration only when the harness supports it (an agent-frontmatter PostToolUse hook produced no events in the same measurement). Otherwise use an identity gate.
2. **Lead-only events.** User prompt submit and session start — including SessionStart's compact matcher, the only event that can inject content after compaction — fire only in the main session. Bind role reinjection there.
3. **Identity gate.** A payload is a seat when it carries `agent_id` (a subagent) or a session other than the lead's recorded session (a teammate); everything else is the lead. A role that unlocks a control (the updater, the reviewer) is matched by its exact registered name in every harness form, never by suffix — a look-alike name is one file the lead can write mid-run. The lead-session sidecar itself accepts only a Write whose whole content is the writing session's own id (never an Edit: its result depends on the file), so a re-claim is a visible transfer — the guard and its brief follow the file to the claiming session — and no actor can silence the gate. Compare every protected path by its on-disk spelling and a casefolded basename: the default macOS volume is case-insensitive, and a typed-string compare lets `RUN-STATE.JSON` alias the protected file. Deny the live graph and its ledger to every actor outside a `.landing` window through the same write guard, Bash shapes included: the audit counters and every deny glob are read from that file, so a hand-written graph re-keys them; and never let a shrunken declared-node count erase an open checkpoint — clamp and name the disagreement instead. `agent_type` alone does not make a seat — a lead started with `--agent` carries one — so a hook that gates on `agent_type` presence fires on the lead too. A hook that must ride a tool event but target one actor reads those fields and exits silently otherwise. When every role dispatches through the same generic agent type, `agent_type` cannot tell roles apart — dispatch each role with a distinct Agent-tool `name` and gate on the `agentName` recorded in the transcript the hook input points to.

A worker whose active duty — its own definition of done — decays within its node is mis-sized work: a node-sizing finding (`pave-spec.md` §9.12), not a reinjection target. Its latent standing rules — prohibitions orthogonal to the work in front of it — are different: compaction preserves active focus and summarizes away latent rules and prior hook injections alike. Target reminder machinery by latent-rule count times blast radius, never by wall-clock length: a long single-focus builder needs none; a seat carrying several rarely-exercised prohibitions over irreversible surfaces qualifies.

Two derived applications, both plan-time options recorded in the enforcement record — one entry naming prose and reinjection as rungs of the same rule (persistence after decay, not a second gate):

1. **Dispatch-time check.** A `PreToolUse` hook on the agent-spawn tool, advisory only and edge-triggered: it fires only when run state already records a completed traversal of the target node (a re-entry dispatch) and asks whether the seat's question is already settled by verified on-disk evidence. Every-spawn firing is wallpaper. The advisory must ride `additionalContext` — a reason attached to an allow decision reaches the user, not the model — and any throttle is the hook's own counter file. The same advisory is the natural carrier for the brief-integrity reminder: a dispatch brief renders the graph and run state — facts by evidence key or resolved path, never retyped (`references/approval-briefs.md`). A template ships below; adopting it is a plan-time enforcement-record entry like any other, never a default registration.
2. **Latent-rule reinjection for rule-heavy seats.** Re-present a seat's standing prohibitions when it touches the matching tool class, throttled per window — tool events at plugin or settings placement fire inside subagents, so this survives seat-side compaction no post-compaction event covers. Where the harness cannot gate on seat identity, ship run-wide tool-class reminders armed by the run marker and record the narrowing as a degradation.

Register by native placement first. Prefer a scope that cleans up automatically and treats explicit skill invocation as opt-in. Reach for project settings only for a rule the native hook surface cannot carry, such as a deny rule; ship that as one fragment the generated lead presents at run start behind one bounded approval question, with the decline path stated: which guards degrade to instructions and which prohibitions become review-only.

## Why this pair exists

The recorded failure cause of long-horizon leads is context decay, not disobedience: the lead follows the routing contract until the prose that states it leaves the context window, then invents outcomes and edges the graph never declared. Role reinjection on user events (UserPromptSubmit, SessionStart — including its compact matcher; PreCompact and PostCompact cannot inject, their output is discarded) covers most of a run. `hooks/goal_restate.sh` (template below) is that reinjection's shipped form for the moments a context is rebuilt — session resume, compaction, and every seat's start: it asks the agent to state the goal from the record and the fewest steps toward it, so the fresh context reconciles the goal and prunes ceremony in the same breath. Two windows stay dark:

1. **The decision to stop.** A Stop with an active, non-terminal run is the highest-risk decay moment: the campaign silently stalls at its resume point, and no user event fires to re-inject anything.
2. **Long autonomous stretches.** Many tool calls with no user prompt and no compaction means zero reinjection; outcomes happen and never reach run state.

The pair covers exactly these two windows. The stop check asks one thing — one line per active seat, because only the lead knows what each seat waits on — and states the rest as duties, since valid stops are common but a duty the lead may answer with one word is a duty it answers with one word while the run's ceremony grows — the measured behaviour of the socratic form this replaces. It carries the duties no other moment re-asks: the declared edge, the routing fact only its context holds, the idle seat, the plain words in the last reply (no write hook sees a chat reply; `pave-spec.md` §8.5), the landed work no review has seen. The question a stop cannot answer — what the whole run's shape costs — moved to the audit-due branch below, where a count answers it.

This pair is one pre-derived answer to one universal failure mode. No two workflows share a failure surface: derive any further enforcement from the workflow's own evidence the same way, and size it on the same spectrum (`references/pave-spec.md` §9.14). A cwd-drift warning or a budget-burn alert may be justified by one workflow's field evidence — and neither belongs in a workflow whose evidence does not name the failure.

## The hooks

| hook | event | rung | shape |
|---|---|---|---|
| stop-alignment check | Stop | socratic reinjection | Blocks a stop ONCE with one reply line per active seat plus the standing imperatives (template below); then a cooldown counter lets the next N−1 stops pass silently (default N=3: at most one nudge per 3 stops). There is no acknowledgement-only exit — a seat with no line is a seat the lead forgot. |
| audit-due branch of the same Stop hook | Stop | socratic reinjection | Every `AUDIT_EVERY` declared-node outcomes (default 40, one env knob), or after `AUDIT_BYTES` of logged growth, blocks one stop with the census brief and the checkpoint id. One Stop hook, one cooldown, one breaker; no second registration. Silent without an evolution root. |
| write log | PostToolUse (`Bash\|Write\|Edit`) | observing | Appends one record per write — stamp, session, `agent_id`/`agent_type`, tool, path, plus `size`, `mtime`, and `written` — so the census never walks the tree: for `Write`/`Edit` the target path, for `Bash` every argv token that is or resolves to a path, which is how a script-mediated writer is seen at all. Always exit 0; rotated at a declared size. |
| lead-only deny | PreToolUse (`Bash\|Write\|Edit`) | blocking | Refuses a write by the lead identity to a path matching a landed `runtime_bindings.deny` glob, read from the live graph at the ledger head. A seat's match is advisory (`additionalContext`) only. |
| no-strand re-cut rule | PreToolUse (`Write\|Edit`) | blocking | Refuses creating `<stem>-r<N>.<ext>` where a same-stem file already exists in that directory under a declared working-state root, for every actor; the remedy is always available. |
| state-staleness reminder | PostToolUse (`Bash\|Write\|Edit`) | observing / socratic | When the run-state file's mtime exceeds a threshold while tools keep running, injects one throttled `additionalContext` question: which node are you actually in, and has an outcome occurred that is not recorded? |
| goal restatement | SessionStart (`resume\|compact`), SubagentStart | socratic reinjection | At a context rebuild, injects one `additionalContext` question: state the goal and its reason from the record (the file wins over memory) and the fewest steps toward it — what breaks if each is skipped? A seat states its brief's goal against the run's. Never throttled: each rebuild is one firing. |

## The audit-due branch, the write log, and the two write guards

Ship these only for a workflow with an evolution root (`references/pave-revisions.md`); they are the running form of the evolution contract, not a mechanism beside it. The stop check re-asks alignment, which no context holds after a compaction; it cannot ask what the run's whole shape costs, because that answer is a count no context holds either. The audit-due branch asks it on a cadence, from files.

**Trigger.** Every `<PREFIX>_AUDIT_EVERY` completed outcomes whose node is a declared node id of the pinned graph (default 40, configurable — cadence is a per-workflow judgment; a `node: lead` pseudo-row never counts), OR when the write log shows more than `<PREFIX>_AUDIT_BYTES` written since the last checkpoint. The bytes fallback catches the run whose cost grows without traversals; the outcome count is the primary unit because it is the run's own measure of progress.

**Checkpoint sidecar.** The cycle lives in one file beside run state, like the ownership marker:

```json
{"checkpoint_id": "cp-<UTC stamp>", "at": "...Z", "outcomes_at": 812, "bytes_at": 1234567,
 "state": "DUE|OPEN|CLOSED|IDLE", "findings_record": null, "closed_by": null,
 "stamped_proposals": [{"path": "...", "size": 1234, "mtime": 1757530445.1}]}
```

The hooks write it and the PreToolUse guard denies the lead every write to it; the findings record is denied to every actor but the updater and the reviewer, and a close reads that record — and any pending proposal — only as those two seats left it (size and mtime stamped at their write), so nothing the lead can type alone, or order another seat to type, moves the cycle. On its first sight of a run with no sidecar the branch seeds an IDLE sidecar with the current declared-outcome count and write-log bytes and passes, so the first checkpoint covers the next window and never charges the run's whole history against an empty log. It survives a reboot, and its states are hook-derived:

- **DUE** — a checkpoint just opened and the lead owes one dispatch. The block text IS the whole brief: the census summary (or the raw counts when no census script is installed), the checkpoint id, the evolution root, the pave-init root, and the one line dispatching the workflow-updater in audit mode. The lead forwards it verbatim (behind whichever first line the updater contract requires for its harness) and adds nothing it composes — a brief the lead writes is the ceremony this branch measures.
- **OPEN** — the updater has written the findings record naming this checkpoint; the write guard stamps the sidecar when it sees that write, and the branch passes while OPEN. Do not register SubagentStop for this: the write is the evidence, and SubagentStop never fires in the lead's session.
- **CLOSED** — exactly one of two, both stamp-bound. (i) A `graph` or `binding` ledger entry newer than the checkpoint — any such entry, so a later `pin` cannot hide it — recording `drafted_by: workflow-updater`, whose patch bytes equal a proposal file the guard itself stamped in the sidecar. (ii) The findings record the guard stamped OPEN, stating `no_change_warranted` for this checkpoint, plus — whenever the census trend rose since the previous checkpoint — the update-reviewer's `review: PASS` line as stamped by the guard when the reviewer wrote it. A `pin` entry closes nothing by itself; neither does a landing whose patch the guard never stamped, nor a line the lead could have typed. On CLOSED the branch resets to IDLE with fresh counts.
- **STALL** — an OPEN checkpoint with no close within `<PREFIX>_AUDIT_STALL` further declared-node outcomes (default 10) blocks once more, naming the stall and its remedy: land the reviewed proposal with `--proposal` and `--stamps` (the stamp is what closes it), or have the updater record the no-change. Exception: a proposal the guard stamped as the updater's, recording `envelope_check: changed_pending_approval`, awaits the user, and only the user closes it — never nag over it; a file anyone else drops in `proposals/` exempts nothing.

**Version skew.** The branch needs the installed pave-init at 2.6.0 or newer (the updater's audit mode, the census script, the `runtime_bindings` schema). When it finds none (probe an override variable, the plugin cache beside the generated plugin, and the harness plugin caches) or an older one, it prints one line saying which and passes, rather than blocking on a brief nobody can execute.

**Write log (observing).** The census reads the log, never the tree: a Stop hook that walks a large scratch tree exceeds its timeout and fails open silently. Log every `Bash` argv token that resolves to a path, because a lead's dominant write channel is a script it runs, which a `Write|Edit` guard never sees. Each record carries `written` per path, with `size` and `mtime`: a `Write` or `Edit` target is written by definition, and a `Bash` argv path counts as written only when the file's mtime is within 120 s of the call — so a path a command only read never inflates the bytes trigger or the trend.

**Lead-only deny (blocking).** The deny globs live in the graph file's `runtime_bindings` block, land as a `kind: binding` entry, and are read from the root at the ledger head on every call — never from a hook-owned list, which the holder of the pen could rewrite. Fail OPEN on a parse error or while `.landing` exists, so a landing never strands a write. A glob starting with `/` is matched against the absolute path, so a scratch family that grew outside the workspace can be cut; every other glob matches workspace-relative. Every block prints the glob's `reason` and its `remedy`.

**One seat-side no-strand rule (blocking).** Creating `<stem>-r<N>.<ext>` where a same-stem file already exists in that directory, under a declared working-state root, is denied for every actor: the match is exact and the remedy is always available, stated per class — working state (a script, a record) is edited in place; world-produced output (a captured log, a transcript) takes a new event-keyed name, never `-rN`. Every other seat-side cut stays advisory: a wrong match on a seat's own working file strands it mid-node. Regrowth of a cut family before the next checkpoint trips DUE and blocks the LEAD over the order that created the form, never the seat over its file.

## Enforcement-record entries for the audit path

Copy these into the plan's enforcement record, adapted to the workflow's own counts (`references/pave-spec.md` §9.14.1: the rung, why the stronger rung is unnecessary, and why the cheaper one cannot catch the defect):

- **Audit-due block** — reinjection, blocking at most once per checkpoint and sharing the stop cooldown. Likely: ceremony grew in every long run measured. Costly: the growth was the dominant share of a run's writes and tokens. Not irreversible — a late audit loses nothing, which is why the rung stops below blocking: it blocks at most one stop per cooldown and never a traversal. Detectable: the trigger is a count of declared-node outcomes and logged bytes, not a judgment. Not stronger (denying the lead's next act) because no finding exists yet and a graph node would price the audit into every run; not cheaper (prose, or an advisory) because the advisory form fired thousands of times without changing the behaviour.
- **Lead-only deny** — blocking. Likely: the cut forms are the ones the lead re-created after each context rebuild. Costly: each is a file every later lap cites. Irreversible before the next gate: the file exists and is cited. Detectable: an exact glob match against a landed graph line, and the glob was checked at `propose` time against every path the graph declares. Not stronger than needed because it is reversible by the lead landing the reversal, which the graph-edit guard's landing allowance never blocks; not cheaper because the reason and remedy already rode an advisory that changed nothing.
- **Seat-side no-strand rule** — blocking, exactly one rule. Likely: measured in the thousands of re-cut files. Costly: the tree the run must carry and read. Irreversible: the re-cut file is what the next reader finds. Detectable: the strict `<stem>-r<N>.<ext>` form beside an existing same-stem file. Remedy always available, per class, printed in the block. Not stronger (a deny per cut family) because a wrong match strands a seat mid-node; not cheaper because the advisory form was already delivered thousands of times.
- **Checkpoint sidecar and findings record** — hook-written sidecar, blocking on the lead's write to it; the findings record blocks every actor but the updater and the reviewer, and the block text names the remedy (dispatch the updater). Likely: the actor whose ceremony is counted holds the pen, and can hand it to a generic seat. Costly and irreversible: a self-closed cycle is indistinguishable from an audited one afterwards. Detectable: two exact paths, and a close accepts the record only at the size and mtime stamped when those seats wrote it. Not cheaper: a lead-writable cycle marker is self-certification, the failure the whole path exists to remove.

Known limit: evolution contract rule 5 — the reviewer is neither the updater nor the lander — stays prose-enforced. `drafted_by` binds the drafter's identity only, and the ledger's `review` field is lead-typed. The kill criterion covers it: two consecutive checkpoints with no drop in the trend number pause the audit loop and go to the user with the numbers (`references/pave-revisions.md`).

## Invariants any adaptation must preserve

Stop hooks have no non-blocking channel: `additionalContext` is dropped, so the questions can only be delivered by blocking once (exit 2). That makes these invariants load-bearing. Losing the circuit breaker is the one catastrophic adaptation error: an infinite stop loop.

- **Cooldown circuit breaker**: a session-keyed marker file holding a countdown; the first stop nudges and writes N−1 (default N=3, one env-overridable knob, minimum 2 — the breaker needs at least one free pass after a nudge), each following stop decrements it and passes, and the stop after the marker is spent nudges again. Clamp the count on read to N−1 and deny the marker to every actor through the write guard: the file sits in a temp dir the lead can write, and an unclamped count silences every stop for the rest of the run. A payload with `stop_hook_active` set short-circuits immediately.
- **Silent when stopping is correct**: no active run state found, or the run's terminal field is set. Do not try to enumerate every valid stop — the questions plus the breaker handle pending user gates and background waits at the cost of at most one bounce.
- **Marker-authoritative discovery**: the hooks act only on run state found via the ownership marker the lead writes at run start (`FOUND_STATE_VIA` = `marker`). A newest-by-mtime scan hit may belong to an abandoned run or a different session — blocking or nudging on it fires in sessions that do not own the run, which is the one detection misfire this design must exclude. The scan fallback exists only for lead-driven resume discovery, where judgment applies. Corollary: the generated state protocol must give the lead an abandon/pause duty (set the terminal field, or remove the state) so a walked-away run cannot stay "active" forever.
- **Staleness stays observing**: always exit 0; mtime-based (stateless), one env-overridable threshold, throttled to once per window per session, skipped for seat payloads (`agent_id` present, or a session other than the lead's — only the lead can act on it; `agent_type` alone is not a seat) and for terminal runs.
- **Fail open everywhere**: missing interpreter, unparsable payload or state → silent exit 0. The payload case must be explicit: emit a distinct parse sentinel and exit on it — defaulting a failed parse to an empty dict is indistinguishable from a minimal valid payload, and the hook will act on input it could not read. These hooks align; they must never strand a run.
- **One Stop hook, one breaker**: the audit-due branch lives inside the same script and shares the cooldown marker — at most one block per N stops in total, whichever branch fires. Its sidecar is hook-written and lead-denied; a hook never trusts a value the audited actor may write.
- **Lead-only scope**: Stop never fires in a subagent (that is SubagentStop — do not register it); the staleness hook gates on payload identity.

What must be re-derived per workflow: the run-state discovery (marker file name, runs-directory glob), the terminal-classification field, the names of the routing section and state-write protocol the messages point at, and the env-var prefix.

## Registration and disclosure

Register the pair in the generated skill's frontmatter — both are lead-only (Stop never fires in a subagent; the staleness reminder skips subagent payloads), invoking the skill is the opt-in, and the hooks live and die with it. A hook that must see subagent writes — a layout warning, a write-for-the-reader reminder — is not this pair and registers at plugin level (Scoping, above). The goal restatement registers at plugin level too: SubagentStart must reach every seat, and one registration serves both events. The generated `description` must disclose the blocks-a-stop behavior and its cadence (at most one block per N stops) — nothing registers silently — and `description` has a 1024-character budget, so disclose compactly. Each hook records its decline path (hook runtime unavailable): the stop check degrades to the lead's resume duty; the staleness reminder degrades to the checkpoint duty in the state-write protocol; the goal restatement degrades to the lead's resume reconciliation duty and, for seats, the brief-reading duty in each role contract.

## Legitimate omission conditions

Record ONE of these in the enforcement record instead of the pair. Silent omission is a review finding, and so is importing the pair where a condition below holds:

- **No persisted run state.** The staleness hook has no signal; the stop hook has no resume point to defend. (If the workflow is long-horizon and has no persisted state, that is the defect to fix first.)
- **Single-session, short-horizon.** The run cannot cross a compaction or session boundary; the decay window the pair covers does not exist.
- **Every node is a user gate.** Stopping is almost always correct, so the one bounce is pure friction with nothing to catch.
- **No lead orchestrator.** Nothing long-horizon exists to align.
- **No seats** (goal restatement only). The SubagentStart half has no receiver; keep the SessionStart half whenever the run can resume or compact.
- **No evolution root** (audit-due branch only). A workflow with no revision ledger has nowhere to land a cut and no graph to read bindings from: the branch records nothing and passes, and the stop check ships without it.
- **Hooks runtime unavailable** in the target environment — record the degradation, not just the omission.

## Templates

Adapt these files into the generated skill's `hooks/` (the dispatch advisory only where a plan adopts it). `ADAPT:` marks every workflow-specific point. Test the invariants, not the wording: a nudge is followed by N−1 silent passes and the stop after that nudges again, `stop_hook_active` short-circuits, terminal runs are silent, staleness fires once then throttles, subagent payloads are skipped, a context rebuild fires the restatement once while startup and clear stay silent, unparsable payloads and empty stdin fail open, and state discovered without the ownership marker leaves the hooks silent.

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
# Stop-alignment check. Blocks a stop ONCE (Stop hooks have no non-blocking
# channel), then a cooldown counter lets the next STOP_EVERY-1 stops pass
# (default 3: at most one nudge per 3 stops). Silent when no active run or the
# run is terminal. Every active seat gets one reply line; there is no
# acknowledgement-only exit. The whole-run ceremony question is not asked here:
# it belongs to the audit-due branch below, which shares this cooldown.
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
seats = [e for e in (state.get("active_node_runs") or []) if isinstance(e, dict)]  # ADAPT: active-seat field
print("; ".join("%s at %s" % (s.get("seat", "<seat>"), s.get("node", "<node>")) for s in seats) or "none recorded")
PYEOF
)" || exit 0
[ "$(printf '%s\n' "$SUMMARY" | sed -n 1p)" = "ACTIVE" ] || exit 0
RUN_ID="$(printf '%s\n' "$SUMMARY" | sed -n 2p)"
RESTART="$(printf '%s\n' "$SUMMARY" | sed -n 3p)"
LAST="$(printf '%s\n' "$SUMMARY" | sed -n 4p)"
AGE_MIN="$(printf '%s\n' "$SUMMARY" | sed -n 5p)"
SEATS="$(printf '%s\n' "$SUMMARY" | sed -n 6p)"
printf '%s\n' "$((STOP_EVERY - 1))" > "$MARKER" 2>/dev/null || true
cat >&2 <<EOF
$TAG Active run $RUN_ID ($FOUND_STATE_LABEL): restart_from=$RESTART, last traversal $LAST, run state last written ${AGE_MIN} min ago.
Active seats: $SEATS

You decided to stop. Reply one line per active seat above, in this form:
  <seat>: waits on <grant | frozen value | design change | seat working | nothing>; next act: <...>
"nothing" means act before you stop. There is no acknowledgement-only exit: a
seat with no line is a seat you forgot. Then these five, as duties, not
questions. The next $((STOP_EVERY - 1)) stops pass before this fires again.
  1. Take a declared edge from $RESTART (re-read the routing section) or
     propose a graph change; never an invented edge.
  2. Write to run state anything routing depends on that lives only in your
     context.
  3. Retire every idle subagent and teammate now.
  4. Restate any codename in your last reply to the user -- id, round number,
     control letter, lease or seat name, section number, hash -- in ordinary
     words (ADAPT: cite this skill's write-for-the-reader section).
  5. Landed work the next lap builds on that no review has seen: run the batch
     review before you continue.
EOF
exit 2
```

The audit-due branch is the same script, after the cooldown check and before or after the block above (whichever fires, it writes the one cooldown marker and exits 2 at most once per N stops). ADAPT: name this workflow's evolution root, the declared-node source, and the census script path; the block text is the census summary, the checkpoint id, the evolution root, the pave-init root, and the dispatch line — nothing composed at the moment of the block. Route a `verify` result of `graph landed since pin` there too: block once with the evolution contract rule 1 route, so an inert run pinned behind the ledger head cannot keep looping.

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
    "actually in right now, and has any outcome occurred since that entry that is "
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

### `hooks/goal_restate.sh` (SessionStart `resume|compact`, SubagentStart)

Register at plugin level, not skill frontmatter. Observing: always exit 0; silent without the marker, on a terminal run, on an unrelated event, and on `startup`/`clear` (no goal exists yet). Never throttled — each context rebuild is one firing.

```bash
#!/usr/bin/env bash
# Goal restatement at context rebuild. Asks; never injects the goal text --
# the record wins over any hook.
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PY="${<PREFIX>_PYTHON:-python3}"; PY="${PY/#\~\//$HOME/}"   # ADAPT: env-var prefix
TAG="[goal_restate]"
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0
. "$HOOK_DIR/_find_run_state.sh"
find_run_state
[ -n "$FOUND_STATE" ] && [ -f "$FOUND_STATE" ] || exit 0
[ "${FOUND_STATE_VIA:-}" = "marker" ] || exit 0   # scan hit is not ownership; stay silent
PAYLOAD_FILE="$(mktemp "${TMPDIR:-/tmp}/<workflow-name>-restate.XXXXXX" 2>/dev/null)" || exit 0   # ADAPT
trap 'rm -f "$PAYLOAD_FILE"' EXIT
printf '%s' "$PAYLOAD" > "$PAYLOAD_FILE" 2>/dev/null || exit 0
"$PY" - "$FOUND_STATE" "$TAG" "$PAYLOAD_FILE" <<'PYEOF' 2>/dev/null || exit 0
import json, os, sys
state_path, tag, payload_file = sys.argv[1:4]
try:
    payload = json.load(open(payload_file, encoding="utf-8"))
    state = json.load(open(state_path, encoding="utf-8")) or {}
except Exception:
    sys.exit(0)                                   # fail open
terminal = state.get("terminal_classification")   # ADAPT: terminal field
if isinstance(terminal, dict) and terminal.get("status"):
    sys.exit(0)
contract = os.path.join(os.path.dirname(state_path), "run-contract.md")   # ADAPT: where the goal is recorded
record = contract if os.path.isfile(contract) else state_path               # not written yet: run state
event = str(payload.get("hook_event_name") or "")
if event == "SessionStart":
    if str(payload.get("source") or "") not in ("resume", "compact"):
        sys.exit(0)                               # startup/clear: no goal exists yet
    text = (f"{tag} Your context was just rebuilt. Before acting, in a few lines: (1) the goal "
            f"and its reason as recorded in {record} -- the file wins over memory; (2) the "
            "declared next step and the fewest after it; for each, what breaks if you skip it -- "
            "cut any with no answer (a seat for a fact knowable from disk, a re-gate of a recorded "
            "approval, a lap with no new evidence). A needed cut the graph forbids is a graph "
            "defect: record it and route it to pave-evolve.")   # ADAPT: this workflow's evolution route
elif event == "SubagentStart":
    text = (f"{tag} Before acting, in a few lines: the goal of your brief, why it serves the run's "
            f"goal (recorded in {record}), and the fewest steps that produce the evidence the brief "
            "asks for. Add none the brief does not need; if the brief gives no reason, say so in "
            "your report.")
else:
    sys.exit(0)
print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
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
