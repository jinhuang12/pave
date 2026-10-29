# Design: orchestrate as a skill in the pave-init plugin

Date: 2026-10-05 (updated 2026-10-09 for the shared Claude Code and Codex source)
Status: implementation in the working tree; release validation remains separate

## 1. Purpose

Provide the `orchestrate` skill for Claude Code and Codex from one source in this plugin. Keep a persistent run log, decomposition with a one-agent clause, and lead alignment hooks. Preserve the original instructions except for required platform changes. Leave out PAVE's design-time graph YAML, validator, plan gates, and revision log.

In the user's words: "a lite or dynamic version of pave-init, which adopts orchestrator reminder/re-alignment hooks, run-state to have a persistent history of work, goal decomposition, and maybe some other things."

Success looks like:

- `/pave-init:orchestrate <task>` in Claude Code and `$pave-init:orchestrate <task>` in Codex run the worker-and-reviewer team from the plugin. Codex also needs the explicit custom-agent installation. Neither package depends on user-level Claude skill or agent files.
- A run that is compacted, resumed, or stopped early keeps orchestrating from its log instead of inventing state, and the hooks never fire for a session that does not own the run.
- Every event the orchestrator acts on is in one file a person can read afterwards, including a per-worker round summary.
- `python3 scripts/build_packages.py --check` and the test suites pass. The generated-file checks include the skill, both roles, and all three references for each platform.

## 2. Decisions

| decision | choice | why |
|---|---|---|
| Harness scope | Claude Code and Codex | Claude uses teammates and SendMessage. Codex uses named custom agents, followup_task for peer delivery, and send_message for events to the active lead. |
| Build | Shared `sources/` templates with platform bindings | Generate the skill, roles, and references for each platform. Each reader receives only its platform's instructions. `--check` detects stale or orphaned generated files. |
| Reviewer role | Copy the user's `adversarial-investigator` into the plugin as a role; keep the user-level copy for `/adversarial-review` | User decision. Known cost: two copies will drift; recorded in section 10. |
| Plugin name | Stays `pave-init`; rename deferred to its own spec | Review found exact-name gates in the generated plugin, Codex per-project installs, and every existing install's settings key would break, for a cosmetic prefix. |
| Persistent state | Append-only markdown run log | Nothing goes stale; no schema or validator. |
| Log writer | The orchestrator only | PAVE one-writer rule. Reviewer verdicts are already persisted verbatim in each reviewer's transcript, so round history is not lost; the worker's DONE message carries a one-line round summary the orchestrator copies. |
| Ownership | Per-session marker from `CLAUDE_CODE_SESSION_ID` or `CODEX_THREAD_ID` | The explicit `--runtime` selects the correct variable when both are present. Hooks use the payload session ID and act only on the run that session owns. |
| Hooks | Lead-only, plugin-level: goal restate on SessionStart resume/compact, a release reminder (Claude) and a speed-up question after run-log writes. The Stop check was built, reviewed, then dropped on 2026-10-06 (section 5) | A stop check that fires on normal waits would block 6 to 14 stops per wave. Staleness has no signal when the lead is the only writer and writes rarely. SubagentStart would hit the reviewers' research helpers. |
| PAVE bits imported | Run log, one-agent clause on each subtask, stopping vocabulary, frozen purpose, worker recovery loop, one lead hook | Each addresses a failure seen or named. |
| Left out | Staleness hook, SubagentStart restate, write log, audit checkpoint, deny hooks, revision log, debate/monitor/audit, rename | No consumer or evidence in a one-session tool. |

## 3. Placement

```
jinhuang12-plugins/
├── sources/orchestrate/
│   ├── SKILL.md.tmpl                   shared skill instructions
│   └── references/*.md.tmpl           shared protocol, subtask, and model structure
├── sources/roles/*.md.tmpl             shared agent instructions
├── sources/bindings/{claude,codex}.toml platform tools, models, paths, and skill names
├── skills/orchestrate/
│   ├── SKILL.md                        generated Claude skill; registers no hooks
│   ├── hooks/
│   │   ├── _hook_common.sh             shared skeleton (section 5)
│   │   ├── goal_restate.sh
│   │   ├── release_reminder.sh         Claude only
│   │   └── speedup_nudge.sh
│   ├── references/*.md                 generated Claude references
│   ├── scripts/orchestrate_log.py, preflight.py
│   └── tests/test_hooks.sh, test_orchestrate_log.py
├── agents/orchestrate-worker.md        generated Claude role
├── agents/adversarial-investigator.md  generated Claude role
└── codex/
    ├── skills/orchestrate/SKILL.md     generated Codex skill
    ├── skills/orchestrate/references/  generated Codex references
    ├── agents/pave_init_*.toml         generated Codex roles
    ├── hooks/hooks.json                plugin-level hook registration
    └── orchestrate_preflight.py        installed-file checks; live tools checked by the lead
```

- `codex/tests/test_release_tools.py` asserts the generated marker in every `agents/*.md`; no orchestrate exclusion is needed.
- The build generates 26 native skill and role files plus six orchestrate reference files. The README identifies the shared sources and the platform outputs.
- `agents/adversarial-investigator.md` is the user-level file minus `memory: user`, with its `research-delegate` dispatch qualified as `pave-init:research-delegate` so the plugin copy depends on no user-level agent, and with the `claude-api` `prompt-audit` findings applied (the step-scripted dispatch section de-prescribed for Fable 5.1, keeping the sub-agent prompt contract, materiality standard, output format, and reply rules). The skill dispatches `pave-init:adversarial-investigator`.
- `agents/orchestrate-worker.md` is the user-level file with the DONE message change in section 6 and the audit's neutral wording for the finding-rejection rule.
- pave-init's fitness text (`orchestration/interview-and-fitness.md`) names the plugin's `orchestrate` skill as the simpler option for a one-session fan-out of independent subtasks. That file ships to Codex too, so the sentence names the skill, not its mechanics. `skills/pave-init/tests/test_doc_budget.py` pins that file's size; the ceiling moves in the same commit.
- All three `~/.claude/` paths in the current skill go: `preflight.py` is called at its plugin path, and the whole-goal brief skeleton is inlined in `references/protocol.md` instead of pointing at `~/.claude/skills/adversarial-review/SKILL.md`.

## 4. Run log

One append-only markdown file per run, written only by the orchestrator through `scripts/orchestrate_log.py`.

```
<cwd>/.orchestrate/
├── .gitignore                          contains "*"; written by init; worktree-safe
├── active-<session-id>                 one line: path of this session's open run
└── 2026-10-05-120312.md                the log
```

```
# orchestrate run 2026-10-05-120312
goal: make all 22 textkit tests pass without editing tests/

- 12:03 PLAN worker-1 -> da-1 | textkit/slug.py | pytest tests/test_slug.py | one agent: one module, one test file
- 12:03 PLAN worker-2 -> da-1 | textkit/duration.py | pytest tests/test_duration.py | one agent: same
- 12:03 PLAN worker-3 -> da-1 | textkit/roman.py | pytest tests/test_roman.py | one agent: same
- 12:06 DONE worker-1 r1 PASS
- 12:07 DONE worker-2 r2 PASS | r1 REVISE 1 HIGH duration.py:8
- 12:09 RESPAWN worker-3 -> worker-4 | bedrock 503, nothing written
- 12:12 DONE worker-4 r1 PASS
- 12:13 INTEGRATE 22/22 green; only 3 module files changed
- 12:14 REVIEW whole-goal da-1 PASS
- 12:15 CLOSE accepted | 3 modules, 1 respawn, 0 deferred
```

Line grammar: `- HH:MM EVENT <actor> <free text>`. Eight events:

| event | written when |
|---|---|
| `PLAN` | a wave is spawned; one line per subtask, written in the same call that spawns the wave, so the log never lists a worker that does not exist yet |
| `DONE` | a worker's DONE message arrives; copies the worker's `Rounds:` line after a `\|` |
| `BLOCKED`, `ESCALATE` | the orchestrator rules on one; the line records the ruling. A re-scope stays inside the goal; a goal change is a user question, recorded as `ESCALATE ... -> user` |
| `RESPAWN` | a worker is replaced; `old -> new` and the reason |
| `REVIEW` | the whole-goal review verdict |
| `INTEGRATE` | the wave's integration check result |
| `CLOSE` | the run ends, with one of `accepted`, `closed_unaccepted`, `blocked`, `incomplete`, `exhausted` (`pave-spec.md` §9.13) |

Open subtask, derived in file order: `PLAN <w>` opens `w`; `RESPAWN <old> -> <new>` closes `old` and opens `new`; `DONE <w>` closes `w`. A worker is open when its latest such line opens it. A later `PLAN` for a worker that already has a `DONE` therefore reopens it; that is how a fix round after a whole-goal `REVIEW ... REVISE` is recorded (section 6, Step 6). Nothing else is derived.

### 4.1 `scripts/orchestrate_log.py`

Standard library only. Use `--runtime claude` or `--runtime codex` before the subcommand. Automatic selection is available when only one session variable is set.

- `init --goal "<text>" [--plan "<line>"]...` creates `.orchestrate/`, `.gitignore`, the log with header and goal, the `PLAN` lines given, and `active-<session-id>`. Refuses when that session already has an open run and prints its path and the `CLOSE incomplete` command. Prints the log path.
- `append EVENT <actor> "<text>"` appends one line to the session's open run. Also accepts several lines on stdin for a wave's `PLAN` block. Rejects an unknown event or a `CLOSE` without a stopping word. On `CLOSE` it removes the marker.
- `status` prints the goal, one line per subtask labelled with the first `|` field of its `PLAN` text (`worker-1 -> da-1 — DONE r1 PASS`, `worker-4 -> da-1 — open, replaced worker-3`), and the last line. Finished subtasks are one line each; open ones keep their `PLAN` text.
- `adopt <log>` re-points the marker at the current session, for a run resumed after `/clear` rotated the session id.

No lock: one writer. Teammates never call the script and subtasks do not mention it.

### 4.2 Authority

The log is the durable record; the orchestrator's context is working memory. After a context rebuild the file wins.

## 5. Hooks

Lead-only hooks, registered in the plugin's `hooks/hooks.json` for Claude and `codex/hooks/hooks.json` for Codex. Claude Code never runs a skill-frontmatter SessionStart hook and drops frontmatter hooks on resume, so none live in the skill's frontmatter. It sources `hooks/_hook_common.sh`, which carries the parts the lead-hooks doctrine calls invariant: payload parse with a parse-fail sentinel and fail-open exit, session id extraction, the run-log readers, and the JSON `additionalContext` emitter. Per-skill code is exactly the doctrine's re-derive list: discovery, terminal rule, question text, env prefix `ORCHESTRATE_`. pave-init's own hooks are not changed in this work; moving them onto the skeleton is a later refactor (section 10).

Discovery: `$root/.orchestrate/active-<payload session_id>` for `root` in payload `cwd`, then `CLAUDE_PROJECT_DIR`, then `$PWD`. No marker, or the marker's target missing, or its last line a `CLOSE`: exit 0. This is marker-authoritative and session-scoped, so a second session in the same cwd is never touched.

| hook | event | behaviour |
|---|---|---|
| `goal_restate.sh` | SessionStart `resume\|compact` | `additionalContext`, exit 0. Prints the literal log `status` command and `ListAgents` for Claude or `list_agents` for Codex. Asks for the goal, open subtasks, and fewest steps to `CLOSE`; you orchestrate, you do not implement. Never throttled. |
| `release_reminder.sh` | PostToolUse Bash after a run-log write (Claude only) | `additionalContext`, exit 0. Names the agents `orchestrate_log.py releasable` lists, with the `TaskStop` call and the `RELEASE` line to log. |
| `speedup_nudge.sh` | PostToolUse Bash after a run-log write | `additionalContext`, exit 0, at most once per `ORCHESTRATE_SPEEDUP_SECONDS` (600). Asks whether anything else can overlap, run in parallel, or be cut. |

The skill description discloses the hooks in one sentence, as the doctrine requires.

**Stop hook dropped (2026-10-06).** It was built and passed review, then removed by the user's decision under §4.11 and §9.14: in a one-session run Steps 5-7 are still in context, an unclosed run is already caught by the next `init` (refuses, prints the close command) and by the restate on resume, and the first live run showed released teammates terminate on a plain message. Its one real reminder, release idle teammates, moved to where it binds: `append CLOSE` prints it. The hook earns its place in multi-session campaigns (pave-init, vllm-neuron-parity), not here.

Seat alignment rides in the subtask: the worker contract already makes a seat restate its goal, reviewer, and write set from the subtask before acting.

## 6. Skill body changes

The original migration from `~/.claude/skills/orchestrate/SKILL.md` made the changes below. The shared source preserves them; platform bindings supply Codex tools, addresses, model settings, skill names, and paths.

1. **Step 1, decompose.** Each subtask gains one line, `One agent: <why one agent can do this and decide its acceptance>` (`pave-spec.md` §9.12). A split that cannot state it is not a split.
2. **Step 3, spawn.** The spawn call and `init` (or `append` for a later wave) happen in the same step, `PLAN` lines included. Model is still passed explicitly on every spawn.
3. **Step 4, monitor.** The ESCALATE row gains: re-scope a subtask inside the goal yourself; a change to the goal is a user question. Each event the orchestrator acts on gets one `append`. A `DONE` is recorded by copying the worker's `Rounds:` line.
4. **Step 6, final review.** A whole-goal `REVISE` routes each finding to its owning worker as a new round and is logged as one `PLAN worker-N -> da-M | fix: <finding> | <acceptance>` line per worker, which reopens that subtask; the fix `DONE` is logged as usual, then `INTEGRATE` and `REVIEW` are repeated before `CLOSE`.
5. **Step 7, report.** `CLOSE` with a stopping word; the report leads with that word.
6. **New: Resume.** After a context rebuild: `status`, then `ListAgents`; a worker open in the log but absent from the roster is dead, respawn it per the provider-failure rule; continue from the last line. After `/clear`, `adopt` first.
7. **Worker contract.** The DONE message gains one line: `Rounds: r1 REVISE (1 HIGH slug.py:12); r2 PASS`. Nothing else changes.
8. **Worker contract, errors.** A non-trivial error that is neither a review finding nor the worker's own code runs the default recovery loop of `pave-spec.md` §9.8 inside the worker, bounded at one retry (if transient) and one investigation to root cause with a tried record. A cause inside the write set is fixed and re-proved by the check that failed; any other cause is a `BLOCKED` message carrying Failed, Tried, Cause, Need. The orchestrator rules from the record (re-scope, route to the owning worker, or ask the user) and logs one `BLOCKED` line. The worker owns the loop because it already holds the context and every tool; routing through the lead would add two turn-boundary hops and a cold investigator.
9. Unchanged: protocol, reviewer loop, broker fallback, reassignment, model seats (reviewer role is `pave-init:adversarial-investigator`), `preflight.py`, anti-patterns.

## 7. Error handling

| failure | handling |
|---|---|
| `init` with an open run in this session | Refuses; prints the open run and the `CLOSE incomplete` command. |
| Session id rotated by `/clear` | Marker is orphaned; `status` finds none and tells the lead to `adopt <log>`. |
| Hook cannot parse payload or log | Exit 0. Hooks align; they never strand a run. |
| Run abandoned without `CLOSE` | Restate asks on the next resume or compaction. The Resume section tells the lead to `CLOSE incomplete` when it abandons. |
| `append` fails (disk, permissions) | Non-zero exit with the line on stderr; the lead retries once, then continues and notes it in the report. |

## 8. Testing

Build: `python3 scripts/build_packages.py --check` clean; `codex/tests` pass without a generated-role exclusion; `skills/pave-init/tests` pass with the doc-budget ceiling moved. Codex port tests cover native reference rendering, hook ownership, and the printed status commands for both platforms.

Log script (`skills/orchestrate/tests/test_orchestrate_log.py`, unittest):

- `init` writes gitignore, header, `PLAN` lines, and the marker; a second `init` in the same session refuses.
- `append` rejects an unknown event and a `CLOSE` without a stopping word; `CLOSE` removes the marker.
- `status` derives open subtasks correctly across `PLAN`, `DONE`, `RESPAWN`, including a subtask reopened by a second `PLAN` after its `DONE`.
- `adopt` re-points the marker.

Hooks (`skills/orchestrate/tests/test_hooks.sh`, payload builders copied from `skills/pave-init/tests/test_hooks.sh`):

- Silent with no marker; silent for a session id that owns no run; silent after `CLOSE`.
- Restate names the open subtasks computed in file order: a RESPAWN replacement, a fix-round `PLAN` after a `DONE`, and `none` when every subtask is `DONE`.
- Restate: `compact` and `resume` emit `additionalContext`; `startup` does not.

Both test files are added to `.github/workflows/validate.yml`; a test CI does not run gates nothing.

Review before the live run: `/adversarial-review` on the package, fixes applied, so the live run tests the final text.

Prompt audit before PASS: the reviewer runs the bundled `claude-api` skill's `prompt-audit` over `SKILL.md`, the references, and both agent files (targets: workers Opus 5.5, reviewer and lead Fable 5.1) and folds material findings into the review.

Live run, from a real install (version bump, then `/plugin update pave-init`, since `--plugin-dir` may not load the plugin into teammate processes), on the 3-module sandbox:

- Log shows `PLAN`, `DONE` with `Rounds`, `INTEGRATE`, `REVIEW`, `CLOSE accepted`.
- `/compact` mid-run triggers the restate question and the lead resumes from `status`.
- `append CLOSE` prints the release reminder and the lead releases the team.

## 9. Work order

1. Keep the skill, role, and reference instructions in shared templates. Generate both platform outputs from bindings. Keep `preflight.py`, `orchestrate_log.py`, and their tests in the plugin; update the README and generated-file checks.
2. Hooks: `_hook_common.sh`, the restate hook (the Stop hook was built here and dropped on 2026-10-06), their tests, CI wiring.
3. Adversarial review, fixes, then the live run.
4. Version bump and `VERSION` changelog entry. The user-level `~/.claude/skills/orchestrate/` and `~/.claude/agents/*.md` are not touched by this work; the user retires them by hand later.

Steps 1 and 2 can run in parallel.

## 10. Deferred, with the reason recorded

- **Plugin rename to `pave`.** Own spec. Must cover: `write_limits.py` and `stop-guard.sh` exact-name sets in vllm-neuron-parity (a pave-evolve round, since that plugin is revision-controlled), `codex/install_agents.py` names written into every Codex project, existing installs keyed `pave-init@jinhuang12-plugins`, and a word-boundary form for any new forbidden token (`pave:` would false-match "PAVE:" in three reference files).
- **Single-source reviewer.** The plugin and user-level `adversarial-investigator` are two copies and already differ: the plugin copy carries the `pave-init:` prefixes and the prompt-audit rewrite, the user copy carries `memory: user`. The user chose to keep both for now; the user-level copy is retired by hand.
- **pave-init hooks onto `_hook_common.sh`.** Same skeleton, separate refactor with pave-init's own tests as the gate.
