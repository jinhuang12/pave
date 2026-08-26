# Stage-2 exploration — lens: delegates-and-runtime

## 1. Question investigated

For each delegated-skill candidate in the goal brief's delegation table
(`vllm-neuron-parity-goal-brief.md`), what is its actual contract (inputs,
gates, hardware assumptions, model/effort shape, failure modes), and what
harness/runtime facts constrain the generated `vllm-neuron-parity` plugin's
enforcement design (hooks, gates, deny-patterns)?

## 2. Evidence inventory

Primary evidence — every file read in full unless noted:

- `.pave/vllm-neuron-parity/requirements.md`, `vllm-neuron-parity-goal-brief.md` (context)
- `skills/neuron-framework-equivalence/SKILL.md`, `scripts/adapters/vllm_neuron.py`,
  `scripts/run_stage{0,1,5,7}.py`, `templates/equiv_config_template.json`
- `skills/experimental-neuron-framework-benchmark-vllm/SKILL.md` (full, 628 lines)
- `skills/experimental-neuron-framework-profiling-vllm-neuron/SKILL.md` (full, 562 lines)
- `skills/experimental-neuron-framework-profile-analysis-vllm-neuron/SKILL.md` (full, 570 lines)
- `skills/neuron-nki-profiling/SKILL.md`, `skills/neuron-nki-profile-querying/SKILL.md`
- `skills/experimental-neuron-autoport-compiler-debugging-vllm-neuron/SKILL.md`
- `skills/experimental-neuron-perf-compiler-artifact-debugging/SKILL.md`
- `skills/experimental-neuron-infra-runtime-troubleshooting/SKILL.md` (full, 73 lines)
- `skills/neuron-nki-writing/SKILL.md`, `skills/neuron-nki-debugging/SKILL.md`,
  `skills/neuron-nki-docs/SKILL.md`, `skills/experimental-neuron-nki-optimizing/SKILL.md`
- `skills/experimental-neuron-framework-aten-op-lowering/SKILL.md`,
  `skills/experimental-neuron-eager-op-lowering/SKILL.md`
- `skills/experimental-neuron-parallelism-planner/SKILL.md`,
  `skills/experimental-neuron-hardware-entitlement/SKILL.md`
- `skills/neuron-dev-contribution-validation/SKILL.md`
- `skills/vllm-neuron-feature-port/SKILL.md` (frontmatter + lead contract, as the
  only in-repo hook precedent — read as directed by the "beyond feature-port" ask)
- `skills/vllm-neuron-feature-port/hooks/irreversible-action-deny.json` (full)
- `skills/vllm-neuron-feature-port/references/delegation.md` (full — the absorbed
  skill's own already-verified delegate table; WIP/uncommitted per `git status`)
- `skills/vllm-neuron-feature-port/references/contribution-checklist.md` (header)
- `.claude/settings.local.json`, repo-root `hooks/hooks.json`
- `pyproject.toml` (console_scripts), `src/neuron_agentic_development/deploy.py`
- `GETTING_STARTED_GUIDE.md`
- Directory listings: `skills/`, `agents/`, repo root, `.claude/`

Commands run: `grep`, `find`, `ls`, `cat` (read-only) — no target-system edits.

## 3. Findings with citations

### 3.1 `neuron-framework-equivalence`

- Invocation shape: **skill-only**, 8-stage scripted pipeline (`STAGE0–7.md`,
  `scripts/run_stage*.py`) — "Do NOT write your own scripts. Run the bundled
  scripts... directly." `SKILL.md:106`.
- **Confirmed caveat, verbatim location:** the vLLM-Neuron adapter is version-pinned:
  `scripts/adapters/vllm_neuron.py:46-47`:
  `PINNED_VLLM_VERSION = "0.24.0"`, `PINNED_VLLM_NEURON_VERSION = "0.24.0"`.
  `SKILL.md:72-74`: "**Version check is automatic.** `get_adapter()` calls
  `adapter.check_environment()` immediately... If the installed `vllm` or
  `vllm-neuron` plugin is not on the `0.24` line... the run **exits early**."
- **Confirmed flag:** `--target-stack vllm_neuron` exists on every stage script
  (`run_stage0.py:35`, `run_stage1.py:80`, `run_stage5.py:64`, `run_stage7.py:199`),
  default `None` (auto-detect). Auto-detect default in
  `templates/equiv_config_template.json:4-5` is `"target_stack": "nxdi"` — i.e.
  **omitting the flag risks silently routing to the forbidden NxDI adapter**, not
  just "no adapter." `references/adapter-contract.md:74` confirms auto-detect reads
  target-file imports when unset.
- Gate: none of its own (no AskUserQuestion, no spend gate) — it is a
  measurement pipeline; Stage 4 is the only stage where source-reading/patching is
  allowed (`SKILL.md:125-130`).
- Hardware: none required for CPU-stage 0–2; device required "for device stages
  (5+)" (`SKILL.md:102`).
- Failure mode named by the skill itself: a version mismatch produces a clean
  early exit, not silent wrong output — but only if `--target-stack` is passed;
  without it, auto-detect can route to `nxdi` and produce a misleading pipeline
  run against the wrong stack.
- **Independent corroboration:** the absorbed feature-port skill's own
  delegation table already documents this exact caveat and already mandates the
  flag: `skills/vllm-neuron-feature-port/references/delegation.md:20` — "**ALWAYS
  pass `--target-stack vllm_neuron` explicitly** (auto-detect defaults to `nxdi`
  — a forbidden stack). **Runtime adapter-version check first:** adapter pinned
  to 0.24 line vs the 0.21 venv... unbridgeable mismatch → `validation_blocked`."

### 3.2 `experimental-neuron-framework-benchmark-vllm`

- Invocation shape: skill-only per its own frontmatter (`allowed-tools: Read
  Write Edit Bash Grep Glob`, `SKILL.md:4` — **no Agent/Task tool granted to the
  skill itself**; the "Running via a task-dispatching agent" section (`SKILL.md:594-611`)
  is guidance for an *external* orchestrator, not a capability the skill grants
  itself).
- **STOP gate, exact text and mechanism** (`SKILL.md:216`):
  > "**STOP-GATE — do not spend on unresolved answers.** Every 'must confirm'
  > item above has to be answered *concretely* before you rent or purchase
  > anything. ... **do NOT provision, launch, or purchase** — stop and ask again
  > for that specific item. It is always correct to halt and re-ask rather than
  > start a paid run on an assumption."
  Preceding "must confirm" list (`SKILL.md:208-214`): model/checkpoint, spec-decode
  `num_speculative_tokens`, AWS account/creds, instance type + capacity-block $
  ceiling, HF token source, S3 destination.
- Hardware assumption: the skill's **entire primary happy path (§A1–A4,
  `SKILL.md:277-537`) is renting/purchasing GPU EC2 capacity itself**
  (`aws ec2 run-instances`, capacity-block purchase, Kaizen fallback) — this is a
  provisioning workflow, not merely a benchmark-execution workflow.
- Neuron side (§B, `SKILL.md:541-563`) submits to Kaizen against an
  already-registered/bringup image — does not provision raw EC2 itself.
- Model/effort: none named (skill-only); no correctness gate, only
  latency/throughput targets loaded from `references/profiling-targets.md`.
- Forbidden effect #6 in the parity requirements ("Removing or bypassing the
  benchmark skill's provisioning STOP gate") maps directly onto `SKILL.md:216`.

### 3.3 `experimental-neuron-framework-profiling-vllm-neuron`

- Invocation shape: skill-only, two methods (serve+HTTP endpoints — primary;
  offline throughput+capture — fallback), `SKILL.md:72-86`.
- Hardware: "trn (preferably trn2) instance running Ubuntu 24.04 DLAMI"
  (`SKILL.md:27`); needs `NeuronProfiler` in `vllm_neuron/vllm/worker/neuron_profiler.py`
  (`SKILL.md:95-96`) — i.e. assumes the vllm_neuron package is importable/live.
- No user gate; it is a capture procedure with numerous hardware traps
  (`SKILL.md:319-444`).
- **Direct conflict with a parity forbidden effect:** the skill's own documented
  remedy for `NCC_EOOM002` compile-time HBM OOM is
  `SKILL.md:375`: "**Clear a stale cache entry:** `rm -rf
  ~/.cache/vllm/neuron/compile_cache/`... **This is often the real cause.**" and
  again under orphaned-worker recovery, `SKILL.md:424-425`: "A poisoned cache can
  also block re-launch: `rm -rf ~/.cache/vllm/neuron/compile_cache`." This is the
  exact shared path the parity requirements forbid clearing (Forbidden effect #2:
  `$VLLM_CACHE_ROOT/neuron/compile_cache`, `~/.cache/vllm/neuron/compile_cache`,
  `/var/tmp/neuron-compile-cache` — "cache clearing is never a debugging remedy").

### 3.4 `experimental-neuron-framework-profile-analysis-vllm-neuron`

- Marked **experimental** in its own header: "The analysis methodology (steps
  2–5) is adapted from the NKI profiling skill but not fully validated for
  framework-compiled NEFFs." (`SKILL.md:17-20`).
- Contains an unusually large "BLOCKING" verification-discipline section
  (`SKILL.md:22-128`) covering real incidents: replay-vs-live undercount, DP
  scaling barrier bugs, a fabricated subagent analysis (real incident, see
  `references/fabricated-analysis-case-study.md`), aggregation errors distinct
  from fabrication.
- Two open TODOs still unresolved in the file itself: `SKILL.md:273` ("TODO:
  define quality checks for framework-compiled NEFFs") and `SKILL.md:562` ("TODO:
  define report template for per-segment breakdown"), plus a live Backlog
  (`SKILL.md:567-570`) listing unadapted parallelism strategies (SP/EP/PP) and an
  unexplained `nc_model_switch` event. **This delegate's contract is explicitly
  incomplete**, not just cautious.
- No user gate; skill-only; hardware only indirectly (consumes artifacts a prior
  profiling run produced).

### 3.5 `neuron-nki-profiling`

- Invocation shape: skill-only. Config resolution is a **different mechanism**
  from run-state: `NKI_VENV_PATH` env var, or read from
  `.claude/nki-dev-suite.local.md` YAML frontmatter (`SKILL.md:44-48`) — a
  project-local dotfile convention independent of the parity plugin's run state.
- Hardware: "Profiling requires execution on actual Trainium/Inferentia
  hardware." (`SKILL.md:55`).
- No user gate. Aimed at raw NKI kernels (not the vllm-neuron serving path);
  complements (does not replace) §3.3's live-server method.

### 3.6 `neuron-nki-profile-querying`

- Invocation shape: skill-only; starts a localhost DuckDB-backed API server
  (`neuron-explorer view --disable-ui`, `SKILL.md:41-46`) or works via
  Python/DuckDB directly on parquet.
- No hardware required for the query step itself (works on already-captured
  NEFF/NTFF); prerequisite artifacts must exist (`SKILL.md:33`).
- No user gate; explicit interpretation-discipline rules at `SKILL.md:294-310`
  ("Don't diagnose from single metrics," "Don't skip the data quality check").

### 3.7 `experimental-neuron-autoport-compiler-debugging-vllm-neuron`

- Invocation shape: **skill only — no agent wrapper**, confirmed independently
  by the absorbed feature-port's own delegation table:
  `skills/vllm-neuron-feature-port/references/delegation.md:15` ("skill only —
  no agent wrapper; invoke by skill name").
- Scope explicitly disambiguated from §3.8: this skill is for bring-up (nothing
  compiles yet); the perf skill is for tuning a NEFF that already runs
  (`SKILL.md:32-40`).
- Cardinal rule: "error 70" is overloaded and uninformative alone; the real
  signal is the per-rank `NCC_*` code in `log-neuron-cc.txt` (`SKILL.md:57-80`).
- **Same cache-clearing conflict as §3.3**, from a different angle:
  `SKILL.md:105-107` (§0 of the decision tree): "Does the venv even run the code
  you edited?... **Wipe poisoned cache + kill orphans first.**" and
  `SKILL.md:146-148` (§8): "COMPILER/TOOLING BUG... Cache poisoning (HLO changed
  but fx cache key didn't)... **wipe cache** — then escalate a ticket." Both are
  first-class steps in this skill's own decision tree, not incidental asides.
- Hardware: assumes an active trn2 compile attempt with real `log-neuron-cc.txt`
  / `fxgraph.txt` / `graph.hlo` artifacts on disk; no user gate.

### 3.8 `experimental-neuron-perf-compiler-artifact-debugging`

- Invocation shape: skill-only, artifact-driven (HLO proto/text, penguin IR,
  compile logs) — does not itself execute anything on device; it reads
  already-produced compiler artifacts (`SKILL.md:18-83`).
- No cache-clearing remedy found in this file (distinct from §3.3/§3.7 — its
  workflow is entirely post-hoc artifact inspection: `hlo_convert`, penguin
  recompiles into a scratch dir, `command.txt` flag-lifting).
- Contains its own anti-fabrication gate: "Do not accept a root-cause claim
  without primary evidence" (`SKILL.md:187-207`) — same genre of rule as §3.4's
  verification discipline, independently written.
- No user gate; assumes a venv with `neuronx-cc` and (ideally) `hlo_convert` on
  the compiler-bundled path (`SKILL.md:86-90`).

### 3.9 `experimental-neuron-infra-runtime-troubleshooting`

- **Notably thin relative to its role in the brief.** Full file is 73 lines
  (`SKILL.md:1-73`): a generic Neuron-SDK command reference (`neuron-ls`,
  `neuron-top`, `dmesg`, `fuser /dev/neuron*`) plus a bulleted troubleshooting
  table for device/compilation/perf/distributed-hang issues.
- **Does not mention `vllm`, `vllm_neuron`, `NeuronModelRunner`, the hardware
  queue, or SSH anywhere** — it is a generic Neuron-SDK triage cheat sheet, not a
  vllm-neuron-specific or campaign-aware runtime-troubleshooting contract.
- No user gate, no model/agent wrapper, no references/ subdirectory (confirmed
  via directory listing — the skill has no supporting files at all beyond
  `SKILL.md`, `version`, `tags`).
- The brief's note "Runtime-troubleshooting supports the autonomous-recovery
  decision" is **not substantiated** by this skill's actual content — it offers
  no lease/identity/reboot-authority logic; that logic lives entirely in the
  parity requirements themselves (host identity re-verification, hardware-queue
  lease). This is a gap, not a verified capability.

### 3.10 `neuron-nki-writing`, `neuron-nki-debugging`, `neuron-nki-docs`

- All skill-only, all config via `NKI_VENV_PATH` / `.claude/nki-dev-suite.local.md`
  (writing: `SKILL.md:19-21` references mandatory constraint file;
  debugging: `SKILL.md:57-66`).
- `neuron-nki-docs` frontmatter carries `context: fork` (`SKILL.md:9`) — a
  Claude-Code skill-level directive that forks execution context when this skill
  is invoked; distinct from the hook mechanism in §3.16 and worth noting for the
  new plugin's own skill frontmatter design (progressive-disclosure vs.
  context-isolation are two different levers).
- `neuron-nki-debugging` hardware target flags: `--target trn1|trn1n|inf2|trn2|trn3`
  mapped to gen2/gen3/gen4 (`SKILL.md:76-84`); no user gate.
- None of the three name a version pin or a spend/hardware-provisioning gate —
  they assume the user/agent already has a live device session.

### 3.11 `experimental-neuron-nki-optimizing`

- **Not a flat skill — a meta-orchestrator.** Frontmatter: `agent:
  experimental-neuron-nki-optimizer-agent` (`SKILL.md:8`) and `allowed-tools:
  ["Read","Write","Grep","Glob","Bash","TodoWrite","AskUserQuestion","Skill","Task"]`
  (`SKILL.md:9`) — it is explicitly granted the `Skill` and `Task` tools and
  itself loads three other skills at the top of its own workflow: "Load ...
  nki-dev-suite:neuron-nki-docs, nki-dev-suite:neuron-nki-debugging,
  nki-dev-suite:neuron-nki-profiling" (`SKILL.md:32-36`), then dispatches
  `/neuron-nki-profiling`, `/neuron-nki-debugging`, `/neuron-nki-docs`,
  `/experimental-perfetto-explorer-query` by name through its own workflow
  phases (`SKILL.md:44-46, 83-89, 178-187`).
- **Note the `nki-dev-suite:` namespace prefix on the loaded skill names** — this
  implies these skills are addressed as members of a `nki-dev-suite` package
  namespace at invocation time, distinct from this repo's bare skill directory
  names (`skills/neuron-nki-docs/`, no `nki-dev-suite:` prefix on disk). This is
  a harness-addressing detail the parity plugin's own Skill-tool dispatch calls
  must resolve correctly — invoking a bare name vs. a namespaced name may not be
  interchangeable depending on how/where these skills are deployed (see §4).
- Has its own AskUserQuestion gate for functionality-changing optimizations
  (`SKILL.md:161-168`), but this is a code-safety gate, not a spend/hardware gate.

### 3.12 `experimental-neuron-framework-aten-op-lowering` / `experimental-neuron-eager-op-lowering`

- **Both target a different repo than the parity plugin's PR surface.**
  aten-op-lowering assumes a `torch-neuronx` checkout at `$TORCH_NEURONX_ROOT`
  (`SKILL.md:33-51`); eager-op-lowering assumes a specific
  `torch-neuron-eager-moduscope/private-torch-neuronx` checkout
  (`SKILL.md:26-36`). Neither touches `vllm_neuron` / the `jinhuang12/vllm-neuron`
  fork the parity plugin's acceptance criterion requires a PR against.
- eager-op-lowering's own success criteria include creating and pushing a branch
  to a **named personal fork** and opening a **cross-fork PR**: "The PR is raised
  from `sdeeptan-aws:op/aten-<name>` → `aws-neuron:main`" (`SKILL.md:326-328`),
  plus a mandatory S3 CSV upload to a Grafana dashboard
  (`SKILL.md:222-240`) — a completely separate contribution pipeline (branch
  naming, commit-count rule "one commit per branch," CSV schema) from the parity
  plugin's evidence-bundle-then-PR-open-gate model.
- aten-op-lowering is the newer/superset variant (7-gate runner, xfail triage,
  `references/` worked examples for 9 op patterns) but is equally repo-scoped to
  `torch-neuronx`, not `vllm_neuron`.
- Neither skill declares a user gate of its own; both are "run tests first, then
  gate-check" pipelines with hard xfail-discipline rules ("NEVER pre-populate
  xfails" appears verbatim in both, e.g. aten:`SKILL.md:370`, eager:`SKILL.md:169`).
- **Open question OQ8 in the requirements ("kernel-gap escalation... how that
  work is sized and spun out to the NKI skills or becomes its own campaign")
  is not resolved by either delegate's contract** — a missing-op finding that
  escalates here produces a torch-neuronx-repo artifact (a PR against a
  different fork with its own review process), which the parity plugin's
  single-PR-per-campaign, single-fork acceptance model has no slot for.

### 3.13 `experimental-neuron-parallelism-planner`

- Invocation shape: skill-only, CLI-driven (`python3.12 -m planner <run-spec>`,
  `SKILL.md:36-38`); pure analytic engine, no hardware access — "needs only the
  standard library" for the CLI, `torch` (meta/CPU) + `pytest` for the
  oracle-validation scripts (`SKILL.md:46-48`).
- Model/effort: run-spec JSON (HF `config.json` + hardware-constants JSON,
  `SKILL.md:110-133`); no user gate of its own, but explicitly requires the
  caller to supply SLA ceilings/objective (`SKILL.md:54-108`) — i.e. it needs
  inputs the parity plugin's kickoff contract would have to route in (validation
  model, host alias → hardware constants).
- Confirmed as the situational delegate the absorbed skill already uses:
  `references/delegation.md:21` ("a serving-config (DP×TP) choice is needed to
  make benefit evidence fair... Expect an interior-optimum TP for
  collective-bound models; do not assume max-TP").

### 3.14 `experimental-neuron-hardware-entitlement`

- **Explicitly builds on, and imports, the parallelism-planner's engine and
  oracle** rather than re-deriving them: "This skill reuses the planner's engine
  and oracle... imports them via `_planner` and never copies them"
  (`SKILL.md:25-27`); `scripts/_planner.py` "locates the
  `experimental-neuron-parallelism-planner` skill's `scripts/` directory... The
  single seam between the two skills." (`SKILL.md:164-166`). This is a real
  **inter-skill dependency**: hardware-entitlement cannot function if the
  parallelism-planner skill directory is not co-located/discoverable at runtime
  (sibling skill dir, or `$PARALLELISM_PLANNER_SCRIPTS`, per `SKILL.md:81`).
- Pure analytic (meta-device oracle), no hardware, no user gate — same shape as
  §3.13. Not present in the absorbed feature-port's own delegation.md table (that
  table lists parallelism-planner but not hardware-entitlement) — this delegate
  appears to be new to the parity brief's larger table, unvetted by prior
  campaign history.

### 3.15 `neuron-dev-contribution-validation`

- **Contract mismatch with the brief's stated use ("Pre-PR validation before the
  PR-open gate").** The skill's actual checklist
  (`SKILL.md:24-94`) validates **NAD-repository artifact contributions** — skill/
  agent/hook namespace compliance (`neuron-nki`, `neuron-framework`, etc.,
  `SKILL.md:26-32`), `NEURON_METADATA.md` fields (`SKILL.md:41-48`), AIM benchmark
  test counts and Neuroboros CR-reviewer counts (`SKILL.md:57-93`) — i.e. it
  validates a *skill or agent* being added to *this NAD repo*, not a *vllm-neuron
  code PR*.
- **Directly contradicted by the absorbed skill's own delegation table** —
  `skills/vllm-neuron-feature-port/references/delegation.md:73-75`:
  "`neuron-dev-contribution-validation` is **NOT a delegate here**: it validates
  NAD-repo artifact contributions, not plugin-repo PRs. Use the self-owned
  `references/contribution-checklist.md` instead." That self-owned checklist
  (`references/contribution-checklist.md:1-6`) explicitly states the same thing
  from its own side: "The NAD `neuron-dev-contribution-validation` skill
  validates NAD-repo artifacts, NOT plugin-repo PRs — this checklist is
  self-owned." This is a **confirmed, doubly-sourced contradiction** of the goal
  brief's delegation-table row for this skill.

## 4. Contradictions and stale documentation

| # | Contradiction | Evidence |
|---|---|---|
| 1 | Brief's delegation table lists `neuron-dev-contribution-validation` for "Pre-PR validation before the PR-open gate"; the actual skill validates NAD-repo skill/agent contributions, and the prior absorbed skill's own materials (delegation.md + contribution-checklist.md) already say explicitly it is not the right delegate for this. | §3.15 |
| 2 | Brief frames `experimental-neuron-infra-runtime-troubleshooting` as supporting "the autonomous-recovery decision"; the skill's actual content is a generic, vllm-neuron-agnostic Neuron-SDK command cheat sheet with no lease/identity/recovery logic. | §3.9 |
| 3 | The parity requirements forbid ever clearing the shared Neuron compile cache ("cache clearing is never a debugging remedy") while two in-scope delegates (`experimental-neuron-framework-profiling-vllm-neuron`, `experimental-neuron-autoport-compiler-debugging-vllm-neuron`) prescribe `rm -rf ~/.cache/vllm/neuron/compile_cache` as a first-line documented remedy in their own troubleshooting trees. | §3.3, §3.7 |
| 4 | `neuron-nki-optimizing` loads its sub-skills via a `nki-dev-suite:` namespaced name (`nki-dev-suite:neuron-nki-docs`, etc.) while this repo's on-disk skill directories carry bare names (`skills/neuron-nki-docs/`) — the namespace prefix implies a different deployment/addressing context than a literal read of this repo's `skills/` tree. Not fully resolved — see Remaining gaps. | §3.11 |

No stale-documentation findings beyond the above were found in the 18 skills read (all `SKILL.md` files read were internally consistent and, where TODOs exist, self-flagged as such — see §3.4).

## 5. Graph implications (observations only — no synthesis/graph proposed)

- The equivalence delegate (§3.1) needs a **hard precondition check** before
  dispatch: confirm the installed `vllm`/`vllm-neuron` line is 0.24.x (or that
  the campaign has bumped to it) and that `--target-stack vllm_neuron` is always
  passed — otherwise the delegate either hard-fails cleanly (good) or silently
  routes to the forbidden `nxdi` adapter (bad). This is a verify-before-trust
  point, not merely a doc note.
- The benchmark delegate (§3.2) is safe to reuse **only if the enforcement
  design never lets it reach its own §A1 provisioning branch** — since the
  parity plugin's own exclusions forbid hardware provisioning entirely, any
  dispatch of this skill must be scoped/steered to §A3/A4 (run the sweep against
  an already-standing, already-reachable GPU baseline instance) and never to
  §A1/A2 (rent/purchase). The skill's STOP-gate is a spend confirmation, not a
  provisioning ban — the plugin's own ban is stricter and must be enforced
  independently of trusting the delegate's gate.
- The compile-cache-clearing contradiction (§3.3/§3.7) means a naive "delegate
  and trust its remedies" design would violate a hard-forbidden effect the first
  time a real compile OOM or poisoning trap fires. Whatever the actual
  enforcement design turns out to be, it needs to intercept/override this
  specific remedy path from these two delegates specifically (not just rely on
  the delegates' own good judgment).
- The op-lowering delegates (§3.12) produce artifacts in a different repo
  (`torch-neuronx`) under a different contribution process than the parity
  plugin's target fork — the brief's own open question (OQ8, kernel-gap
  escalation) is real and unresolved at the delegate-contract level; it is a
  cross-repo hand-off, not a same-repo campaign step.
- `experimental-neuron-hardware-entitlement` (§3.14) has a hard runtime
  dependency on `experimental-neuron-parallelism-planner` being co-located/
  discoverable — any dispatch of the former must ensure the latter is resolvable
  in the execution environment (sibling skill dir or `$PARALLELISM_PLANNER_SCRIPTS`).
- The feature-port skill's `delegation.md` (§3, corroborating evidence) is a
  working precedent for a "delegate-then-verify Gate-B checklist" pattern already
  proven across 6 of these delegates in real campaigns — it is a candidate input
  artifact per the requirements' "Absorbed engines... contribute graphs and
  references," not something to re-derive from scratch.

## 6. Harness / runtime facts for the plugin's enforcement design

- **How skills in this repo reach a session:** there is no `.claude-plugin/plugin.json`
  or marketplace manifest anywhere in this repo (confirmed: `find . -maxdepth 1
  -iname ".claude-plugin"` empty at repo root). Distribution is via a `pip
  install .` package (`neuron_agentic_development`) with two console-script entry
  points defined in `pyproject.toml:16-17`:
  `deploy-neuron-agentic-development-to-kiro` and
  `deploy-neuron-agentic-development-to-claude`, both routed to
  `src/neuron_agentic_development/deploy.py`. `_deploy()` in that file copies
  artifact directories (skills/agents/hooks etc.) to
  `Path(os.path.expanduser(f"~/.{target}"))` — i.e. **`deploy_to_claude` copies
  these skills into the user's global `~/.claude/` tree**, not into any
  plugin-scoped bundle. Consequence: the generated `vllm-neuron-parity` plugin
  cannot bundle or guarantee these 18 delegates itself — it depends on the NAD
  package having been separately `pip install`ed and `deploy-...-to-claude`'d
  into the same machine's global Claude Code config. This is an external
  dependency the plugin's own kickoff contract or a preflight check should
  surface, not something pave-init can wire into the plugin's own manifest.
- **`.claude/settings.local.json`** at repo root only sets
  `permissions.additionalDirectories: ["/Users/jinhun/GitHub/vllm-neuron"]` — no
  plugin config, no hooks, no deny list currently installed at the project level.
- **Agent-spec files:** `agents/` holds paired `*.agent-spec.json` (AIM/Kiro
  format) + `*.md` (Claude Code markdown) files for roughly 20 named agents,
  including wrappers for several of the delegates read here (e.g.
  `experimental-neuron-framework-benchmark-vllm-agent`,
  `experimental-neuron-parallelism-planner-agent`,
  `experimental-neuron-hardware-entitlement-agent`,
  `experimental-neuron-nki-optimizer-agent`). `deploy.py` contains
  `_resolve_agent_specs`/`_transform_agent_spec`, which **rewrites `file://`
  paths in the `.agent-spec.json` at deploy time** relative to the deploy
  destination — confirming these agent-spec files are AIM-native artifacts that
  need a transform step, not directly consumable as a pave-init-generated
  plugin's own native `agents/*.md` definitions.
- **Hook precedents in this repo, exhaustively:**
  1. Repo-root `hooks/hooks.json` — one `SessionStart` hook
     (`check-env.sh`) with `"supportedClients": ["kiro-cli"]`
     (`hooks/hooks.json:9`) — **Kiro-only; does not fire under Claude Code.** This
     is a distinct, non-skill-scoped hook mechanism, separate from skill
     frontmatter hooks.
  2. `skills/vllm-neuron-feature-port/SKILL.md` frontmatter — **the only
     `SKILL.md` in the entire `skills/` tree that declares a `hooks:` key**
     (confirmed via `grep -rl "^hooks:" skills/*/SKILL.md` → one match). It
     registers five skill-lifetime hooks: `PostToolUse` ×3 (state validation on
     Write/Edit, contamination+premature-edit guard on Edit/Write/Bash, staleness
     reminder on Bash/Write/Edit), `Stop` (alignment check, "blocks a stop at most
     once" per its own description), `UserPromptSubmit` + `PreCompact` +
     `SessionStart` (all three routed to the same `routing_contract_reinjection.sh`).
  3. A **separate, consent-gated deny-pattern file**,
     `skills/vllm-neuron-feature-port/hooks/irreversible-action-deny.json`, which
     is explicitly NOT auto-installed ("nothing here registers silently" — its own
     `_comment` field). It is presented via one `AskUserQuestion` at run start; on
     accept, its `permissions.deny` array (git push/PR-create/checkout-main/
     merge-main/reset-main/rebase-main variants, plus `rm -rf` on several
     compile-cache path variants) and its one `PreToolUse` hook entry are merged
     into the **project's `.claude/settings.local.json`** (not the committed
     `settings.json` — "the consent is per-user and per-machine"). The
     accompanying `hooks/deny_main_mutation.sh` is branch-aware: it runs `git
     branch --show-current` in the target checkout (`deny_main_mutation.sh:154`)
     to catch bare `git commit`/`git merge` on `main`/`mainline`, which a static
     deny string cannot detect.
  This is the exact precedent the parity requirements' "deny-pattern precedent
  in the absorbed skill" and "Enforcement expectation... blocking hooks" language
  refers to, and it is the **only** such precedent in the repo.

## 7. Remaining evidence gaps

- Did not resolve the `nki-dev-suite:` namespace prefix seen in
  `experimental-neuron-nki-optimizing/SKILL.md:32-36` against how skills are
  actually addressed once deployed to `~/.claude/` by `deploy_to_claude` — i.e.
  whether that prefix reflects a real installed-plugin namespace elsewhere on
  this machine (out of scope: reading outside this repo/`.pave` workspace) or is
  itself stale/aspirational text inside that one skill. Flagged, not resolved.
- Did not read `neuron-framework-autoport-vllm-neuron` or
  `neuron-framework-autoport` (out of this lens's assigned scope — the brief
  marks these "absorbed... not dispatched as-is," and they were not in the
  explicit skill list given). Their contracts are unverified by this report.
- Did not verify the `experimental-perfetto-explorer-query` skill that
  `neuron-nki-optimizing` dispatches internally (out of assigned scope; it is not
  a row in the brief's delegation table).
- Did not verify whether any of the 18 skills' referenced `agents/*.md` /
  `*.agent-spec.json` wrapper files (e.g.
  `experimental-neuron-framework-benchmark-vllm-agent.md`) diverge from their
  wrapped `SKILL.md` contract — only the `SKILL.md` files themselves were read in
  depth per the assigned scope; the agent wrapper files were confirmed to exist
  (via `ls agents/`) but not read for contract drift.
- Did not test the equivalence adapter's actual runtime behavior against a live
  0.21 venv (no hardware access, read-only lens) — the "exits early" claim and
  the auto-detect-defaults-to-nxdi claim are both taken from the skill's own
  documentation and the absorbed skill's corroborating note, not from an
  executed reproduction.
- Did not check whether `experimental-neuron-parallelism-planner` or
  `experimental-neuron-hardware-entitlement` have been exercised against the
  specific models named in the parity brief's known-unsupported-feature list
  (LoRA, pipeline/context parallelism, etc.) — both skills' own docs list which
  architectures their oracle covers (`hardware-entitlement/SKILL.md:91-96`) but
  this was not cross-checked against the parity brief's target model list.
