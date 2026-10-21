# Investigation & Optimization Deep-Dive Playbook

A reusable pattern for driving a hard, measurement-heavy optimization (or debugging) effort to a
verified result while leaving a complete, auditable evidence trail.

**Scope within this skill.** `SKILL.md` defines *what order* to do the work in (the enforced
`plan → entitlement → profile → analyze → sweep → implement → report` stage machine) and
`references/SWEEP_ACCELERATION.md` defines *how to make step 5 cheap*. This file defines *how to record the
work as you do it* — the artifacts, the cadence, and the verdict vocabulary that make a claim
auditable. The three are complementary: the gate enforces order, this playbook enforces evidence.

Companion references in this same directory:

| File | Role |
|---|---|
| `FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md` | Framework/model anti-pattern → principle lessons. Source of the **bare** lesson codes (`Dn`, `En`, `Gn`, `Hn`, `Jn`, `Kn`, …) cited throughout `SKILL.md`; entries are `### <code>` headings under `Part A`–`Part N`. |
| `KERNEL_OPTIMIZATION_LESSONS.md` | Kernel/profiling anti-pattern → principle lessons. Source of the **`KERNEL`-prefixed** codes (`KERNEL A1`, `KERNEL A10`, `KERNEL B13`, …). |
| `OPTIMIZATION_BOOK.md` | NKI performance-engineering primer for Trainium2/3 — cost model, SBUF/PSUM allocation, instruction set, profiling loop, worked case studies, pre-flight checklist. Read before proposing or reviewing kernel work (step 6). |

---

## 0. Operating principles (the part that actually matters)

1. **Evidence over narrative.** Every claim about behavior, cost, or a bottleneck must cite a tool
   result you produced this session (a measured number, a parquet query, a profile, a file line).
   "I believe / it should" is not evidence.
2. **Re-derive inherited conclusions.** Never trust a headline from a prior step, a summary, or a
   sub-agent. Recompute the top term yourself. (Most big wins in practice come from finding that an
   inherited "residual/other/irreducible" bucket was mislabeled.)
3. **Two-part validation for every fix.** A fix is only real if *both* the mechanism moved AND the
   end-to-end metric moved. Mechanism moved + metric flat = wrong attribution (becomes the new top
   task). Metric moved + mechanism unmoved = you don't understand the lever yet.
4. **Own your mistakes explicitly.** When a previous change of yours caused the problem, say so and
   fix it. This is usually where the largest gains are.
5. **Drive each lever to a definite verdict** (win / regression-with-number / ruled-out-with-reason)
   before moving on. Exhaust config -> compiler/flags -> kernel, and record the negative results — a
   documented negative is progress.
6. **Preserve a known-good state.** After every experiment, restore/keep the best verified config
   live. Back up any source edit before changing it.
7. **State what you did NOT verify.** Be precise about the boundary between confirmed and assumed.

---

## 1. Artifact layout

```
<project_root>/
├── <TRACKER>.md                      # single running status log (append-only, timestamped)
├── <FRAMEWORK_LESSONS>.md            # durable anti-pattern -> principle lessons (framework/model)
├── <KERNEL_LESSONS>.md               # durable anti-pattern -> principle lessons (kernel/perf)
├── <PLAYBOOK>.md                     # this file
└── agent_artifacts/
    ├── deep_dives/STEP_NN_<topic>.md # one focused analysis per major investigation step
    ├── tmp/                          # scratch: scripts, serve/bench launchers, logs
    ├── traces/                       # profiles / captured traces
    ├── data/                         # ingested parquet, weights, downloaded artifacts
    └── <patches_or_port>/            # source edits + their .backup copies
```

Rules:
- Scratch, logs, launchers -> `tmp/`. Never leave them in the project root.
- Every source edit gets a sibling backup (`<file>.<what_changed>`) before/after the change.
- Caches / NEFFs / large build outputs -> a fast scratch disk, not the repo.

§7 binds each placeholder to a concrete path for this skill.

---

## 2. Deep-dive document — `STEP_NN_<topic>.md`

Write one when you complete a **major investigation step**: a new bottleneck localization, a lever
tested to a verdict, or a corrected conclusion. Numbered monotonically. Template:

```markdown
# STEP NN — <one-line claim/verdict, e.g. "The collective is the binding term">

## Premise
Where we were coming in (cite the prior STEP / measured baseline). What question this step answers.

## Hypothesis / lever
The specific thing being tested and the mechanism you expect.

## Evidence
Numbered, each with the tool output that produced it. Use tables for measured sweeps:

| config / variant | metric A | metric B | verdict |
|---|---|---|---|
| baseline | … | … | |
| variant | … | … | regress / win / ruled-out |

Device-free derivations (from already-captured data) preferred when hardware is scarce.

## Result / verdict
win (with numbers) | regression (with numbers) | ruled-out (with the reason).
If ruled out, state what it would have taken to work.

## What this localizes next
The single most-supported next lever, and what is now excluded by evidence.
```

Keep it tight and quantitative. The reader should be able to act on the verdict without rerunning
anything.

**A deep dive is not a substitute for the gate.** The mechanism sentence in `## Evidence` still
obeys `SKILL.md` G4 — it cites the profile artifact path in the same breath — and the deep dive is
an artifact to include in the specialist submission to `neuron-campaign submit`.

---

## 3. Tracker — `<TRACKER>.md`

Generate measurement summaries from `neuron-campaign report`; admitted evidence is the source
of truth. Optional narrative may explain a decision without duplicating or overriding the
measurement records. Template:

```markdown
## STATUS <YYYY-MM-DD HH:MM> — <headline>

<2–6 sentences: what was done, the numbers, the decision. Cite the deep dive / lesson it maps to.>

Verified best: <exact config> = <metric>. <live-server file / how to reproduce>.
Env/flags now available: <any new knobs and their defaults>.
```

Add short `### UPDATE HH:MM` sub-blocks when a status evolves within the same investigation.
The campaign database and reconciliation state establish "where are we right now."

This is the same document `SKILL.md` step 7 ships as the deliverable
(`docs/performance/<model>_optimization_tracker.md`): the STATUS blocks accumulate *during* the run
and the timeline table, per-kernel breakdown, reproduction commands, and ranked
outstanding-bottleneck table are assembled from them at report time. Do not defer it to the end.

---

## 4. Lessons — `<*_LESSONS>.md`

Durable, project-agnostic lessons distilled from the mistakes/finds. One row = one reusable
principle. Anonymized (no "the previous agent / prior session"). Template:

```markdown
| N | Anti-pattern (what you were tempted to do) | Principle (what to do instead, with the tell) |
|---|---|---|
```

Add a lesson when a mistake or discovery generalizes beyond this task. Keep them phrased as
"don't do X -> do Y, and here's the signal that tells you which case you're in."

The two lessons files in this directory are the accumulated result of this practice — read the
relevant Part before a stage rather than rediscovering its entries. New lessons from the current run
append there (framework/model → `FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md`, kernel/profiling →
`KERNEL_OPTIMIZATION_LESSONS.md`), keeping the existing `### <code>` numbering scheme so the codes
cited from `SKILL.md` stay stable.

---

## 5. Cadence (when to write what)

| Trigger | Artifact |
|---|---|
| Completed a major investigation step / localized a bottleneck / tested a lever to a verdict | `STEP_NN` deep dive |
| Any measured result, landed fix, or direction change | Tracker `STATUS`/`UPDATE` |
| A mistake or find that generalizes | Lessons row |
| Any source edit | Backup sibling file |
| End of experiment | Restore known-good; note it in tracker |

Do this *as you work*, not in a batch at the end — the point is an auditable trail and a
recoverable state at every checkpoint.

Mapped onto the campaign capabilities in `SKILL.md`:

| Stage | Deep dive to write | Also record |
|---|---|---|
| `plan` | `STEP_01_parallelism` — the DP×TP recipe and the interior-optimum TP sweep | tracker STATUS with the classified ask type (Gate A) |
| `entitlement` | `STEP_02_entitlement` — binding-operator hypothesis + the floor | accepted entitlement and its feasibility verdict |
| `profile` | `STEP_03_capture` — what was captured, phase verified from primary fields | profile submission with observed phase/capture; blocked submission if capture failed |
| `analyze` | `STEP_04_<binding op>` — the mechanism, citing the artifact path (G4) | tracker STATUS |
| `sweep` | one `STEP_NN` per axis driven to a verdict | `config_sweep.axes` and accepted `profile_attempt_ids` |
| `implement` | `STEP_NN_<lever>` with two-part validation (principle 3) | backup sibling for every source edit |
| `report` | — | measured profile metrics, matching equivalence, then `neuron-campaign audit --strict` |

---

## 6. Verdict vocabulary (use consistently)

- **WIN** — mechanism moved AND metric moved, both with numbers.
- **REGRESSION** — metric got worse, with the number and the suspected cause.
- **RULED OUT** — tested, didn't help, with the reason and what would have changed the outcome.
- **BLOCKED** — can't test; state the blocker and what would unblock it.

Never report a lever as "done" without one of these verdicts and its supporting number.

These findings are evidence for campaign decisions. A lever's RULED OUT is not a
campaign-level ceiling or infeasibility proof. The controller reports NOT_YET until the
measured objectives pass. Keep large measured/floor gaps and untried layers explicit.
A capture failure is a blocked specialist submission with command, error, and log evidence;
it does not admit a profile or open analysis. Only matching measured performance and
correctness can certify MET. A reconciled allocated run may finalize with a miss/blocker
receipt and resume action.

Freeze baseline identity separately from permitted axes and source writes. Rank executable
one-factor control/treatment/drift candidates from this run's evidence, exposed cost,
predicted benefit, evidence quality and full cost. Use a cheap falsifier before kernel work;
label admission/replica changes as capacity changes. Budget planning, compilation, all arms,
correctness and submission against the workload deadline, preserving closeout reserve.
Choose a cheaper legal chain when needed.

Track profiles by source identity, canonical effective config and leg with witnessed defaults.
Freeze criterion, reference context and generation lengths; a short probe is not full-context
certification. Reuse exact references, never target correctness. Persist each completed arm
before cleanup. Reconcile native child state, expected output and submission receipt; resume
existing work and do not end the parent turn while it runs. Report observed scoped gains and
missing correctness independently of strict MET.

---

## 7. Project binding — placeholder -> concrete file

The orchestrator does not itself capture/measure. The **owning agent** produces the artifacts; the
orchestrator only reads/verifies them (`SKILL.md` Gate B). Paths below marked *(this skill)* ship
with the skill; the rest are **relative to the project root**, where `<run-id>` is
`$RUN_ID` and `<model>` is the model under optimization.

**The project root is `$NEURON_PROJECT_ROOT` (default: the cwd), and it must be the SAME value for
every delegated agent.** It is the shared input across this family: `--project-root` on the
parallelism-planner and hardware-entitlement CLIs, the output root for the
vllm-neuron profiling skill, and the output root for the analysis / querying skills. Export it once,
pass it down, and every row below resolves to one tree. There is no per-skill artifacts override to fall out of step with: `--project-root` is the only
way to name a different root, and it takes the same shape. If an agent must capture on a box
whose disk is not this project, point ITS root at that disk and copy the artifact back under this
one before citing it, because Gate B verifies files here.

| Playbook placeholder | Concrete file |
|---|---|
| `<PLAYBOOK>.md` | `references/INVESTIGATION_OPTIMIZATION_PLAYBOOK.md` (this file) *(this skill)* |
| `<FRAMEWORK_LESSONS>.md` | `references/FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md` *(this skill)* — bare `Dn`/`Hn`/`Gn`/`En`/`Jn`/`Kn` codes |
| `<KERNEL_LESSONS>.md` | `references/KERNEL_OPTIMIZATION_LESSONS.md` *(this skill)* — `KERNEL`-prefixed codes |
| kernel primer / cost model | `references/OPTIMIZATION_BOOK.md` *(this skill)* |
| sweep wall-time playbook | `references/SWEEP_ACCELERATION.md` *(this skill)* |
| stage gate + run state | `neuron-campaign`; `agent_artifacts/autobridge/<run-id>/campaign.db` |
| `<TRACKER>.md` | `docs/performance/<model>_optimization_tracker.md` (in-tree tracker format) |
| deep dives | `agent_artifacts/data/<run-id>/deep_dives/STEP_NN_<topic>.md` |
| traces | `agent_artifacts/data/<run-id>/profiles/` (+ `agent_artifacts/traces/`) |
| ingested data | `agent_artifacts/data/<run-id>/` |
| scratch/launchers/logs | `agent_artifacts/tmp/` |

**Delegation caveat for principles 1-2 (evidence / re-derive):** for the ORCHESTRATOR, "a tool result
you produced this session" means **an artifact a delegated agent produced and you verified on disk** —
never a serve/bench stdout line the orchestrator scraped, and never a number the orchestrator computed
by running Neuron tooling itself. A mechanism claim ("X binds") requires a delegated `nc_exec_running`
device capture localized by the profile-analysis agent; until then the binding operator is UNCERTIFIED.
