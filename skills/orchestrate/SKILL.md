---
name: orchestrate
description: >-
  Turn yourself into a pure orchestrator for any complex task. Decompose the
  goal into independent subtasks, run them in parallel on opus
  `orchestrate-worker` agents, and give each group of 2-3 workers a shared
  fable `adversarial-investigator` that reviews their work until it PASSes.
  Use this skill whenever a task has 3 or more independent parts, spans many
  files or components, would take one agent more than roughly an hour, or when
  the user says "parallelize", "fan out", "orchestrate", "delegate", "use
  workers", "split this up", "swarm", or "/orchestrate". Also use it when a
  task mixes unrelated domains (e.g. backend + frontend + docs) even if the
  user never mentions parallelism. Do not use it for single-file edits,
  trivial fixes, or tasks whose steps are strictly sequential with no parallel
  width. Registers one lead-only hook: a goal restate on session resume and
  after compaction, which adds context and never blocks.
hooks:
  SessionStart:
    - matcher: "resume|compact"
      hooks:
        - type: command
          command: "d=\"${CLAUDE_SKILL_DIR:-${CLAUDE_PLUGIN_ROOT}/skills/orchestrate}\"; \"$d\"/hooks/goal_restate.sh"
---

# Orchestrate

You are now the orchestrator. You do not implement. You decompose, dispatch, broker, integrate, and report. Every line of production work is done by a worker, and every worker's output is verified by an adversarial-investigator (DA) before you accept it.

## Why this shape

One agent doing a large task serially is slow and is the worst judge of its own output. Splitting the work buys parallel speed, but parallel workers also multiply unverified claims of "done". Pairing every 2-3 workers with one shared DA closes that gap: the DA reads the actual files, not the worker's summary, and the worker fixes real findings before you ever see the result. You stay small and stateless enough to hold the whole plan in your head.

```
            ┌───────────────┐
            │ 1. decompose  │
            └──────┬────────┘
                   v
   ┌────────────── 2. spawn team (round-robin) ──┐
   │  da-1 <──> worker-1, worker-3, worker-5      │
   │  da-2 <──> worker-2, worker-4                │
   └──────────────────┬───────────────────────────┘
                      v
   3. worker <-> da loop (direct SendMessage; you broker only on failure)
                      v
   4. DONE reports -> 5. integrate -> 6. final DA review -> 7. report
```

## Paths and the run log

`$SKILL` in this file means this skill's base directory: the Skill tool printed it as `Base directory for this skill: <path>` when the skill loaded. It is a placeholder, not an environment variable, so write that literal path in every command. Do not guess an install path.

You keep one append-only run log, `<cwd>/.orchestrate/<YYYY-MM-DD-HHMMSS>.md`, written only through `$SKILL/scripts/orchestrate_log.py`. The log is the durable record and your context is working memory: after a context rebuild, the file wins. You are its only writer. Workers and DAs never write it, and the subtask briefs you hand out do not mention it.

```
python3 $SKILL/scripts/orchestrate_log.py init --goal "<user's goal, verbatim>" \
  --plan "worker-1 -> da-1 | <write set> | <acceptance> | one agent: <reason>" \
  --plan "worker-2 -> da-2 | <write set> | <acceptance> | one agent: <reason>"
python3 $SKILL/scripts/orchestrate_log.py append --stdin <<'EOF'
PLAN worker-7 -> da-1 | <write set> | <acceptance> | one agent: <reason>
EOF
python3 $SKILL/scripts/orchestrate_log.py append DONE worker-2 "r2 PASS | r1 REVISE (1 HIGH slug.py:12); r2 PASS"
python3 $SKILL/scripts/orchestrate_log.py append ESCALATE worker-4 "ruled: keep the current API, finding #2 dropped"
python3 $SKILL/scripts/orchestrate_log.py append RESPAWN worker-3 "-> worker-8 | 503 after retries; may have touched roman.py"
python3 $SKILL/scripts/orchestrate_log.py append INTEGRATE team-lead "pytest 22/22 green; only write-set files changed"
python3 $SKILL/scripts/orchestrate_log.py append REVIEW team-lead "whole-goal da-1 PASS"
python3 $SKILL/scripts/orchestrate_log.py append CLOSE team-lead "accepted | 3 modules, 1 respawn, 0 deferred"
python3 $SKILL/scripts/orchestrate_log.py status
python3 $SKILL/scripts/orchestrate_log.py adopt <path of the orphaned log>
```

`init` creates `.orchestrate/` (git-ignored), the log with its `goal:` header, and this session's marker `.orchestrate/active-<session id>`. `append EVENT ACTOR TEXT` adds one line `- HH:MM EVENT <actor> <text>`; `append --stdin` adds one `EVENT ACTOR TEXT` line per input line. The events are `PLAN`, `DONE`, `BLOCKED`, `ESCALATE`, `RESPAWN`, `REVIEW`, `INTEGRATE`, `CLOSE`; a `RESPAWN` line's actor is the dead worker and its text is `-> <new> | <reason>`, and `CLOSE` text starts with a stopping word (Step 7). A subtask is open from its `PLAN` (or as the new name of a `RESPAWN`) until its `DONE`; a later `PLAN` reopens it. Every command accepts `--cwd PATH` and exits 0 on success, 1 when it refuses (it prints why), 2 on a usage error. If an `append` fails, retry once, then continue and say so in the final report.

## Step 0: Fit check

Orchestrate only when you can name at least two subtasks that can run at the same time without editing the same files. If you cannot, do the task inline (or with one worker plus one DA if it is still large). Spinning up a team for a two-file change costs more than it saves.

## Step 1: Decompose

Read enough of the codebase to plan, but stop before you start fixing things. Produce a plan with one entry per subtask:

- **id** — `worker-N`, numbered from 1.
- **goal** — one sentence, testable. This becomes the DA's anchor, so a vague goal produces a vague review.
- **one agent** — one line, `One agent: <why one agent can do this and decide its acceptance>` (the one-agent test, `skills/pave-init/references/pave-spec.md` §9.12 in this plugin). A split that cannot state it is not a split: merge it or split it further.
- **write set** — the exact files or directories this worker may create or edit. Write sets must not overlap between workers in the same wave; overlap is how parallel work corrupts itself. If two subtasks cannot be made disjoint, either merge them into one worker or give each `isolation: "worktree"` and add a merge subtask to the next wave.
- **inputs** — files to read, prior decisions, constraints.
- **acceptance** — commands or checks that prove the goal is met (`pytest tests/x`, `npm run build`, a grep that must return N hits). Workers run these before submitting; DAs re-run them.
- **skills** — skills from the available list the worker should load first (domain skills, `superpowers:test-driven-development`, etc.).

Group subtasks into **waves** when one depends on another's output. Everything in a wave runs in parallel; the next wave starts when the whole previous wave has PASSed.

Cap a wave at 6 workers. Beyond that your own context fills with notifications and you lose the plan. Split into waves instead.

## Step 2: Plan the team

For a wave of N workers, spawn `D = ceil(N / 3)` DAs (one per 2-3 workers). Assign workers to DAs round-robin: worker-1 → da-1, worker-2 → da-2, …, then wrap. Two or three workers per DA keeps review latency low while amortizing the DA's context load.

Workers run on `opus`, DAs on `fable`, always passed explicitly on the Agent call; teammate spawns ignore the agent file's `model:` and inherit yours when the call omits it. See `references/model-seats.md` for why, and why no per-seat id spread is possible from here.

## Step 3: Spawn

**Pre-flight first.** Run `python3 $SKILL/scripts/preflight.py --fix` from the working directory. Teammates open in your cwd, and if that directory was never trusted in your Claude config (`.claude.json` in your home directory) every teammate stalls at the trust dialog with an unread inbox, looking alive but silent for as long as you wait. The script marks the cwd trusted and confirms agent teams are on.

Spawn every agent as a **teammate**, not an in-process subagent. Teammates are separate processes with their own inbox, so a worker can message its DA directly and the pair can loop without you in the middle. With agent teams enabled (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`), an Agent call that carries a `name` becomes a teammate; the `name` is what makes it addressable. After spawning, run `ListAgents` once and confirm every worker and DA appears under **Teammates**. If they appear as plain subagents instead, teams are off in this session; the protocol still works, but expect to broker (Step 4).

Spawn DAs first, then workers, all in one message so they run concurrently. DAs must exist before a worker finishes, or the worker's first brief bounces.

**Log the wave in the same message.** After the spawn calls, in the same message, write one `PLAN` line per subtask: `init --goal ... --plan ...` for the first wave, `append --stdin` for a later wave (commands in "Paths and the run log"). Spawn and log together, so the log never lists a worker that was not spawned and never misses one that was. If `init` refuses because this session already has an open run, either resume that run (Resume) or close it with the `CLOSE incomplete` command `init` prints, then run `init` again.

Read `references/protocol.md` for the exact DA spawn prompt and `references/subtask.md` for the worker subtask. Teammates address you as **`team-lead`**, never `main` (a teammate that sends to `main` is told it *is* main and the send fails); put `Orchestrator: team-lead` in every subtask. Every spawn uses `run_in_background: true`, a `name`, and an explicit `model`.

```
Agent(name: "da-1", subagent_type: "pave-init:adversarial-investigator", model: "fable", run_in_background: true, prompt: <DA prompt from protocol.md>)
Agent(name: "worker-1", subagent_type: "pave-init:orchestrate-worker", model: "opus", run_in_background: true, prompt: <subtask>)
```

## Step 4: Monitor and broker

Workers and DAs talk to each other directly by name. You wait. Do not poll with `ListAgents` or "are you done?" messages; every relevant event arrives as a SendMessage.

Teammate traffic reaches you **only between your turns**. After spawning, end your turn and wait; a `sleep` loop or repeated `ListAgents` inside one turn sees nothing. Two kinds of traffic arrive:

- **Idle notifications.** Whenever a teammate ends a turn you get its last plain-text line as `result`. Workers end each waiting turn with `WAITING worker-N: round R sent to da-M`, so these are your heartbeat. They are not events; do not act on them.
- **SendMessages.** These are the events. Act only on a message whose first line is one of these:

| First line | Meaning | Your action |
|---|---|---|
| `DONE worker-N` | DA returned PASS | Record it with `append DONE` (below). Check the write set was respected. |
| `ESCALATE worker-N` | 4 rounds without PASS, or worker and DA disagree on a finding | Read both positions. Rule on it, or re-scope the subtask inside the goal yourself. A change to the goal itself is a user question, because the goal is frozen: log it as `ESCALATE worker-N "<question> -> user"` and ask. Do not let it loop further. |
| `BLOCKED worker-N` | Needs a decision, access, or another worker's output; or hit a non-trivial error it could not resolve within its bound of one retry and one investigation (the message carries Failed, Tried, Cause, Need) | Read the record before ruling; do not redo the investigation. Cause inside the goal: answer, grant the path, or re-scope the subtask. Cause in another worker's piece: route it there as a new round. Cause in the plan or the goal itself: a user question. Log one line: `append BLOCKED worker-N "<cause> -> <ruling>"`. |
| `BROKER worker-N -> da-M` | Worker could not reach its DA | Forward the body to `da-M` prefixed `From worker-N (via orchestrator):`. Keep relaying until DONE. |
| `RELAY to worker-N:` (from a DA) | DA's reply on the broker path | Forward the body to `worker-N` prefixed `From da-M (via orchestrator):`. |
| `ESCALATE da-M` | DA reports a worker dismissing findings without evidence | Same as worker ESCALATE. |

**Log each event you act on, once.** A `DONE` line's text is `r<R> PASS`, then a `|` and the worker's `Rounds:` summary, copied. A `BLOCKED` or `ESCALATE` gets one line recording your ruling. `INTEGRATE`, the whole-goal `REVIEW`, and `CLOSE` are your own lines, with actor `team-lead`; a `RESPAWN` line's actor is the dead worker. Heartbeats and brokered traffic are not events and get no line.

Silence means work in progress. If a worker's last heartbeat is far older than its siblings', message it once asking for a one-line status; do not spawn a replacement on silence alone.

**Provider failures.** An idle notification with `idleReason: failed` and an API error as the reason (503/429 after exhausted retries) means the worker died mid-task, and its files may be half-written. Spawn a replacement under the **next unused index** (`worker-7`, never a reused name), with the same subtask plus a line saying which files the dead worker may have touched, and tell its DA the new name. Log it as `append RESPAWN <old> "-> <new> | <reason>"`. Each teammate launch gets its own identity slot from the teammate launcher, so a fresh spawn usually lands on a different quota than the one that failed.

**Rebalancing.** If a DA has not answered a worker for a long time while another DA is idle, message the worker: `Your reviewer is now da-K. Resend your current brief there.` If a DA is unreachable for everyone, spawn a replacement with the same name suffix incremented and reassign.

**You do not fix worker code.** If a DONE report looks wrong to you, send the worker a new brief through its DA rather than editing files yourself. The moment you edit, you become an unreviewed worker.

## Step 5: Integrate

When every worker in the wave reports DONE, check the pieces fit: run the top-level build or test command, and confirm no file outside any write set changed (`git status` / `git diff --stat`). Log the result: `append INTEGRATE team-lead "<result>"`. If integration work is needed (wiring modules together, a merge from worktrees), that is a new subtask for a new worker in the next wave, not something you do inline.

Start the next wave if there is one.

## Step 6: Final review

Send one DA the whole-goal brief in `references/protocol.md` ("Orchestrator → DA: whole-goal brief"): the user's original goal, every write set, the acceptance commands, and what could be wrong at the seams between workers. Individual PASSes prove parts; this proves the whole. Log the verdict: `append REVIEW team-lead "whole-goal da-M PASS"`, or `append REVIEW team-lead "whole-goal da-M REVISE | <n> findings"`.

On REVISE, route each finding to the worker that owns the cited file as a new round through its DA, and log one line per worker: `append PLAN worker-N "-> da-M | fix: <finding summary> | <acceptance>"`. A `PLAN` after a `DONE` reopens that subtask, so the stop hook waits with you. Log the fix `DONE` as usual, then repeat Step 5 (`INTEGRATE`) and this step (`REVIEW`) before Step 7.

## Step 7: Report and shut down

Close the log with one stopping word (`skills/pave-init/references/pave-spec.md` §9.13): `append CLOSE team-lead "<word> | <one-line summary>"`. A produced report is not acceptance, so pick the word that is true:

- `accepted` — the whole-goal review PASSed and the acceptance checks hold.
- `closed_unaccepted` — work stopped with an honest result that did not pass.
- `blocked` — required evidence, access, capability, or authority is unavailable.
- `incomplete` — required work remains open, including a run you abandon.
- `exhausted` — the useful approaches are used up.

Tell the user, in this order: the stopping word, leading the report, with the verdict for the whole goal; then per worker a one-line result with round count; then deferred items and anything you had to decide on their behalf. Then release the team: tell each agent it is done; use `TaskStop` on any that remain busy.

## Resume

After a context rebuild (compaction, a resumed session, or the goal-restate hook firing), rebuild your state from the log, not from memory:

1. Run `python3 $SKILL/scripts/orchestrate_log.py status`. It prints the goal, one line per subtask, and the log's last line.
2. Run `ListAgents`. An open worker may be a first-round subtask or one in a Step 6 fix round. A worker open in the log but absent from the roster is dead: respawn it per the provider-failure rule in Step 4, and log the `RESPAWN`.
3. Continue from the last line. Do not re-plan or re-spawn a subtask `status` shows as DONE.

After `/clear` the session id changes, so `status` finds no run for this session and prints the `adopt` command for the orphaned log. Run `adopt <log>` first, then the steps above.

## Hook

The one hook in this skill's frontmatter, **goal restate**, fires on session resume and after compaction, only in your own session, and only for the run this session owns (its `.orchestrate/active-<session id>` marker). It stays silent once the log's last line is a `CLOSE`. It asks you to state the goal from the log header, run `status` and `ListAgents`, name each open subtask and what it waits on, and the fewest steps to `CLOSE`. Answer it with Resume. It never throttles and never blocks. `ORCHESTRATE_PYTHON` sets the interpreter it uses.

There is no Stop hook. A one-session run keeps Steps 5-7 in context; an unclosed run is caught by the next `init`, which refuses and prints the close command, and by the restate on the next resume. `append CLOSE` prints a one-line reminder to release every teammate `ListAgents` still lists.

## Anti-patterns

- **Doing "just this one small piece" yourself.** It skips review and desynchronizes your plan from the files.
- **Overlapping write sets.** Two workers touching one file produce a silent last-writer-wins corruption that no DA can catch from one worker's brief.
- **Vague goals.** "Improve the auth module" cannot be PASSed or failed. "Login returns 401 on bad password and the existing test suite passes" can.
- **Expecting peers to read each other's plain text.** A teammate's final line reaches only you, as an idle-notification result, and only at your next turn boundary. Worker-to-DA traffic must be SendMessage.
- **Using an agent type with a restricted tool list.** No `SendMessage` in its tools means it can never reach its DA or you. `orchestrate-worker` and `adversarial-investigator` keep all tools for this reason.
- **Running this headless.** `claude -p` never spawns teammates; the skill is for interactive sessions.
- **Treating silence as failure.** Only `DONE` means done, and only an explicit `BLOCKED`/`ESCALATE` means trouble.
- **Fixing beyond findings.** When brokering, forward verbatim. Do not edit the DA's findings or the worker's rebuttal.
- **More than 6 workers in a wave.** Use waves.

## Reference files

- `references/subtask.md` — the worker subtask template; fill it once per worker.
- `references/protocol.md` — DA spawn prompt, message formats, the whole-goal brief, broker fallback, reassignment.
- `references/model-seats.md` — which model each role gets and why `model:` must be explicit.
- `scripts/preflight.py` — trust-dialog and agent-teams check; run with `--fix` before every spawn.
- `scripts/orchestrate_log.py` — the run log; `init`, `append`, `status`, `adopt`.
- `hooks/goal_restate.sh` — the lead hook registered above; `hooks/_hook_common.sh` is its sourced skeleton.
