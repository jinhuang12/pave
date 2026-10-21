# Framework / Model Optimization Lessons

Merged from `FRAMEWORK_MODEL_OPTIMIZATION_gaal.md`, `FRAMEWORK_MODEL_OPT_lucy.md`, and
`FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md`. Overlapping entries from the three sources have been
consolidated into one statement each. Contradictions between the sources were tracked in
`FRAMEWORK_MERGE_CONFLICTS.md`, resolved there, and are now integrated here — nine of them changed the
guidance, and the resolutions are recorded at the end of the provenance appendix.

**Purpose.** A transfer-learning checklist of *recurring mistakes* in framework-, model-, and
serving-level performance work: deployment configuration, parallelism planning, entitlement
reasoning, benchmarking, profile capture at the serving layer, and the reporting/audit discipline
around all of it. Each entry is a mistake pattern, its mechanism, how to detect it, and a rule.
Kernel-internal lessons (per-engine attribution, layout, on-chip memory, per-kernel floors) live in
the companion `KERNEL_OPTIMIZATION_LESSONS.md`.

**Scope and notation.** Dense/GQA transformer inference served through a compiled-graph accelerator
stack with custom-kernel backends, tensor parallelism (TP), data-parallel replicas (DP), and
quantized weights. Symbolic parameters throughout: `L` layers, `H` hidden, `B` batch, `TP`/`DP`
parallelism degrees, `n_kv_heads` KV heads, `IN`/`OUT` input/output token counts, `f_phase` a
phase's share of device time.

**Severity.** 🔴 changes a conclusion / misleads · 🟠 wastes time or risks correctness · 🟡 sloppy
but usually caught fast.

---

## How to read this document — the transfer contract

**This is a model of failure *classes*, not a record of any run.** Every entry exists so a reader
can recognize and avoid a mistake before making it. Nothing here is a report, a result, or a
precedent. No entry names a campaign, model, host, instance type, or target value, by design: a
lesson carrying those details stops being portable, because the reader starts reconstructing "which
run was this?" instead of asking "can this happen to me?"

**Numbers here illustrate a mechanism; they are never results and never expectations.** Read
`72 × 2048 = 147456` as teaching that an identity closing exactly on a padded dimension proves
padding is counted as work — the arithmetic is the lesson, the values are arbitrary.

- **A magnitude is never an expectation.** No figure predicts what a lever will yield or how far
  from a bar you will land. Direction and mechanism transfer; magnitude never does.
- **A magnitude never orders your work.** It may justify *running* an experiment, never skipping
  one, ranking one, or recording a decision.
- **Nothing here establishes that any target is infeasible.** Feasibility is a property of a
  (model, config, bar, measurement) tuple and cannot be inherited from a document.
- **Never infer the source run.** Shard counts, file timestamps, sibling directory names and a
  target of the same *shape* are not identification. Concluding "these lessons came from a run like
  mine, so its outcome predicts mine" leaves the document's scope — see J13, which records that
  exact reader failure as an authoring defect.

**A figure survives only where the arithmetic *is* the lesson.** Where a source reported an achieved
magnitude, a distance from a bar, or a tuned constant's value, it appears here as a direction plus the
*rule* that regenerates the value in the reader's own regime — never as the value itself.

**When adding an entry:** write the class and the mechanism, keep every guardrail generic, frame any
number as an example, and name no model, host, bar, or run. Use the entry template at the end.

**Layout.**

| Part | Question it answers |
|---|---|
| A | What exactly is being asked? |
| B | What procedure governs this work, and who performs which part? |
| C | What must be derived, enumerated, or read from source before any device time? |
| D | How is the scarce device budget sequenced and protected? |
| E | Is this measurement admissible? |
| F | Is this profile the artifact I think it is? |
| G | Does the conclusion follow from the measurement? |
| H | Which levers exist, and which have actually been tested? |
| I | Correctness and kernel-interface mistakes |
| J | Reporting, ledgers, and audits |
| K | Why the effort must not stop here |
| L | Fast pre-flight checklist |
| M | Summary table |
| N | Provenance appendix and entry template |

---

## Part A — The target: pin what is being asked before anything else

Every entry here is cheap, sits upstream of all hardware time, and can invalidate an entire
iteration on its own. A term of the target that was never resolved does not announce itself — it is
silently filled in by a harness default, an inherited table, or your own convenience.

### A1 🔴 Leaving the metric's *unit* undefined — "tok/s" and "TTFT" are each several quantities

**Pattern.** A target stated as a bare rate ("over N tok/s") or a bare latency ("TTFT under N ms") is
accepted without pinning which quantity it means, and analysis proceeds on whichever the harness
puts first. For throughput the candidates are **output** vs **total (prompt+output)** tokens, which
differ by roughly `(IN+OUT)/OUT` — an order of magnitude at long-prompt/short-output shapes. For
latency the candidates are device prefill time, an offline `generate()` wall-clock, an online-serve
percentile, and full end-to-end. A verdict flips when the unit is finally fixed, and the whole
round's reasoning was built on the wrong quantity.

**Rule.**
- Resolve the unit **with the requester, before the first measurement**, and name the governing
  definition from the project's own run-spec (cite the path), not the one that happens to pass.
- Report the three distinct throughput quantities once, explicitly labelled: queue-free floor,
  sustained rate at the capacity knee, burst/saturation peak. Compare the *right* one to the target.
- An offline `generate()` wall-clock is not an online-serve latency: it contains no HTTP, scheduler
  or detokenizer cost. Do not present one as the other.
- Where ambiguity survives, publish **both readings side by side in one table** and state which
  meets the target. Never silently pick the flattering reading.

### A2 🔴 Reporting an instantaneous peak as a sustained rate

**Pattern.** A windowed peak field (a best-single-second decode rate, an engine log's rolling
average) is quoted as "sustainable," or extrapolated into one. At high concurrency with bursty
arrivals a 1-second peak can exceed the sustained mean by 2× or more, and a decode-only rolling
average excludes prefill occupancy entirely.

**Rule.**
- Enumerate **every** throughput field the artifact emits and classify each on two axes:
  *sustained vs instantaneous-peak*, and *decode-only vs includes-prefill/prompt*.
- Name ONE authoritative field and use it everywhere — normally the sustained end-to-end output
  rate, because the SLA is defined on it and it is the only one that composes additively into a node
  aggregate. Instantaneous fields appear only as a labelled ceiling.
- Any sentence containing "sustainable ~X" must cite the field X came from. Engine-log rolling
  averages are instrumentation, not benchmark results, and never become a headline.
- Report the verdict under **every** unit, including units under which the bar clears; hiding the
  flattering reading is as dishonest as leading with it. State the reading *and* its cost.
- Treat a 2×+ sustained-vs-peak gap as a diagnostic (the system is bursty or ramp-contaminated), not
  as licence to quote the higher number.

### A3 🔴 Inheriting the `IN:OUT` split from a harness default

**Pattern.** The stated requirement gives only a context size, which constrains `IN + OUT` and leaves
the split open — so the harness's default split is adopted as the requirement. But the split is
often *the* dominant variable: prefill and decode contend for the same devices and an output token
costs several times more device time than an input token, so at fixed context the achievable
output-throughput ceiling can vary several-fold between input-heavy and output-heavy ends. Total-token
throughput additionally counts prompt tokens, inflating the figure by the same ratio.

**Rule.**
- For any **output**-token target, obtain `IN:OUT` explicitly; never infer it from a harness default,
  and state the split beside every throughput number.
- Report the ceiling as a **curve over the ratio**, not a scalar. This converts a flat "missed" into
  "met above roughly this output fraction."
- Resolve output-vs-total *before* the first measurement (A1) and record the split next to every
  number thereafter.

### A4 🔴 Inferring the deployment shape from the metric definition

**Pattern.** The serving architecture — one unified server vs separated phase-specific pools, one
process vs several, phases colocated vs split across disjoint device sets — is inferred from the
*metric* specification and the plan is designed around a shape the requester had excluded. A metric
spec ("one benchmark run, this split, this percentile") constrains **how you measure**, not **what
you deploy**; those feel like the same statement and are not.

**Mechanism.** Stacks gate their most powerful parallelism and cache-placement axes behind an
*architecture class*, so the shape decides **which axes exist at all**. Choosing wrong does not cost
one cell of the grid, it replaces the grid — and the error is self-reinforcing: once the excluded
architecture is in the plan, the analysis correctly shows it to be the only thing that closes the
target, because the admissible shape's axes were never enumerated.

**Rule.**
- Enumerate the shapes the stack implements and record the assumed one **beside the metric
  definition**, not in the plan's body. Annotate every shape-dependent axis with that dependency.
- Treat an unstated shape exactly like an unstated `IN:OUT` (A3): resolve it before spending hardware
  time, or mark it as an unchecked assumption *in* the plan (C16).
- When an analysis concludes that *only* an architecture change closes the target, re-check that the
  admissible architecture's axes were fully enumerated. That conclusion is the natural output of an
  under-enumerated grid.

### A5 🔴 Acting outside the agreed solution-class scope

**Pattern.** A target is missed and the next lever recommended — or autonomously executed — is
architecturally outside the problem boundary (disaggregated prefill/decode, multi-node scale-out,
hardware re-provisioning) without checking whether that class of solution is permissible. The
recommendation reads as technically correct, gets auto-promoted into the next iteration's mandate,
and an entire iteration is spent on something already ruled out. The work product is excellent and
entirely wasted.

**Rule.**
- Solution-class scope is a **hard constraint to verify before iteration planning**, not a suggestion
  the optimizer may override on technical merit. "Is this class of change in scope?" is a stakeholder
  question, not an engineering one.
- In any report, **explicitly flag levers that require infrastructure changes, additional hardware,
  or architectural departures**. Do not list them unmarked beside in-scope config/kernel levers.
- Never auto-execute a scope-expanding recommendation inherited from a prior iteration. An "unblock"
  section is a *proposal*, not a *directive*.
- If all in-scope levers are genuinely exhausted, say so plainly and rank the out-of-scope options
  pending approval — and see Part K before believing they are exhausted.

### A6 🔴 Asserting a platform constant from memory, and skipping the hardware query

**Pattern.** A platform constant — logical vs physical core count, cores per device, memory per
logical core, the logical-core-config mode — is stated from instance-type or part-number memory,
often inside a delegation prompt that instructs the recipient to treat given inputs as concrete.
Because the throughput recipe is derived as `DP = cores / TP`, one wrong constant rescales the DP
ceiling and silently invalidates every aggregate downstream. The error direction is systematically
toward **understating** what is available, because the confused count is always the smaller one
(physical < logical × LNC), which manufactures premature infeasibility.

**Rule.**
- **Before any feasibility arithmetic**, run the device-listing tool and record `instance_type`,
  `devices`, `cores_per_device`, `logical_core_config`, and the derived
  `total_logical_cores = devices × cores_per_device × LNC`. Then `DP_max = total_logical_cores / TP`.
- Never take a total-core figure from memory, from an inherited report, or from the degree of a
  single prior run. A prior run at `TP=T` proves `T` is *one legal degree*, not the total.
- **A fabricated constant in a delegation prompt is worse than one in a report**, because the prompt
  is framed as ground truth and will not be re-derived. Anything asserted in a prompt is verified
  first, or explicitly labelled unverified with an instruction to check it.
- Never declare a throughput target infeasible without stating `DP_max` and citing the hardware query
  that produced it. An infeasibility claim without a cited `DP_max` is an unchecked assumption wearing
  a verdict's clothes.
- **Any derived quantity inherits the error of its worst input.** After correcting a constant,
  re-derive every aggregate that touched it rather than patching the conclusion.
- Run the whole environment preflight — device inventory and idle state, weights present and their
  real size, a runnable sanctioned stack, free space — **before** delegating. A ten-second existence
  check outsourced to a subagent costs a whole round when its answer is wrong.

### A7 🟠 Recommending a solution whose hardware prerequisites were never checked

**Pattern.** An approach requiring specific hardware capability (RDMA-capable interfaces for
device-to-device KV transfer, a particular interconnect, a specific topology) is planned and
attempted; the prerequisite is discovered missing only at runtime, after compilation and weight load
have consumed the budget, and the failure message is opaque ("backend not found", "memory
registration failed").

**Rule.**
- Verify the hardware is present on the target instance **before any compilation or server launch**
  (fabric/interface enumeration, device listings, topology queries).
- Hardware prerequisites belong in the design document's first paragraph, not the post-mortem.
- When the check fails, report an environmental blocker immediately rather than trying software
  workarounds unlikely to exist for a fundamentally device-to-device mechanism.

### A8 🔴 Adopting an inherited number as the baseline

**RULE (normative).** Every performance number arriving inside a bundle — README tables, design
notes, commit messages, prior reports checked into the tree, a subagent's summary — is **inherited
context only**. It may never be: carried into a PASS/FAIL verdict; used as the "before" side of an
A/B or a claimed speedup; used to prune a lever, topology, or parallelism degree; used to declare a
target nearly-met, met, or infeasible; or quoted to a delegate as the baseline to beat. Its only
admissible use is as a hypothesis about **where to look first**.

**Mechanism.** Inherited numbers were measured at the *author's* operating point — their shard
degree, bucket, batch, context, package version, and patch state — and adopting the number silently
adopts that entire operating point (F3 with an inherited artifact standing in for a profile). They
routinely differ from the target definition in ways the table does not state: device-time or
isolated-phase rather than client end-to-end, a different input length, a different concurrency, a
different tree state. Labelling the table "unverified" in a ledger does not neutralize it, because by
then it has framed the plan's central question.

**Rule.**
- **Re-measure the baseline yourself as the first device action**, at *your* operating point, before
  any lever is chosen.
- Where an inherited number must be quoted before it can be re-measured, quote it **with its
  operating point attached in the same sentence** — not in a footnote and not in a separate
  "unverified" column; neither travels into a delegate's brief.
- Be most suspicious of inherited numbers that make the target look nearly met. Those suppress the
  search and are typically the least comparable, having been produced to showcase a configuration
  rather than to answer your question.
- Distinguish **methodology** documents (read in full; technique, verifiable by reasoning) from
  **measurement tables** (claims, suspect by default). A confident, recently-dated table is not
  evidence; if the artifact is missing, the number is unmeasured — label it so.
- **Opening the underlying artifact does not rehabilitate the number.** Confirming that the figure is
  traceable, and that its quantity and workload match yours, is a **triage device** — it tells you
  whether an inherited figure is worth chasing. It is never a promotion path to evidence, because
  quantity-and-workload matching cannot cover the unobservable differences that matter: package
  version, patch application state, tree state, machine (D8). Verify it to decide where to look; still
  re-measure before it carries weight.

### A9 🟡 Reusing a precedent from another model without stating what transfers

**Pattern.** A prior effort's tracker or playbook for a *different* model is opened as a template and
enters the conversation with no stated scope. It reads as target confusion; worse, the precedent's
model-specific constants, bucket lists and tuned flags sit adjacent to the methodology being
borrowed and leak into the new plan under the authority of "how we did it last time" (C6, C7).

**Rule.** State the transfer boundary in the same breath as the citation: what is reused (structure,
methodology, the shape of the evidence) and what explicitly is not (numbers, topology, tuned
constants). Rebuild structure by hand rather than copying the file, so no constant travels silently.

---

## Part B — Procedure, tooling, delegation, and context discipline

These failures happen before and around the technical work. Each produces a session that *looks*
productive — commands run, artifacts appear — while the governing procedure was never loaded, a
mandated step never ran, or the analysis was performed by whoever was least equipped for it.

### B1 🔴 Driving a procedure's tooling before loading the procedure — and resolving the procedure by filename search

**Pattern.** A state-mutating workflow CLI is invoked while its governing specification goes unread,
on the reasoning that prior-round artifacts are enough to reconstruct the method. Two failure shapes
follow. (1) The prose advises a flag whose **source** shows it suppresses the very verdict the
correction existed to expose — following the prose re-masks the miss. (2) A cross-reference to a
companion document's section points at a section that **does not exist**.

A sharper variant: the governing specification is named, but its *filename* is searched for across a
whole specification tree, and the search returns a same-named file belonging to a **sibling**
specification covering an adjacent stage. Reading it produces the felt experience of compliance — a
spec was located, its references consulted, its dead-ends absorbed — while the document defining the
mandatory stage sequence and the enforcement tool was never opened. Sibling specs in a shared tree
routinely carry identically-named helper files, so the collision is the normal case, not bad luck.

**Rule.**
- Read the governing specification **before the first tool invocation**, and without exception before
  any *mutating* one. "The artifacts imply the method" is a guess about the method.
- Resolve a specification **by name to its own directory** and read that directory's top-level
  document first. A filename search locates files *within* an already-resolved spec; it can never
  establish which spec governs. Before reusing any named artifact, confirm its path lies under the
  governing spec's directory — one path comparison is the whole defence.
- Enumerate the governing spec's own tooling directory when you open it. An enforcement or
  precondition script that is never listed cannot be run, and its existence is not implied by prose.
- For any tool that writes state or emits a verdict, **read the source for the semantics of the flags
  you are about to pass** — how the verdict is computed, what each flag suppresses, what the exit code
  means. Prose drifts from behaviour, and it drifts in the direction of hiding failures.
- **Expect a non-zero exit code when recording an honest failure, and do not "fix" it.** A gate
  reporting CLOSED/INFEASIBLE after a real miss is functioning correctly.
- Verify a cross-reference resolves before relying on it. A stale pointer means the promised recipe
  does not exist: derive it, record it, and report the documentation defect (C9).
- "I read a specification's references" is not "I read the governing specification." Name which one
  when claiming compliance.

### B2 🔴 Treating a mandated step ordering as a suggestion

**Pattern.** A workflow defines a fixed sequence with gates — analytic parallelism planning →
hardware entitlement → deploy and measure → profile → profile analysis → implement → stop — and the
operator goes straight to deploy-and-measure, reasoning "I can figure out the right config by trying
them." The consequences compound: multiple unnecessary compiles that a seconds-long analytic step
would have replaced; unverified mechanism assertions steering the sweep because entitlement never
localized the binding operator; no profile ever captured because the budget went to the sweep; and an
effort that produces a curve but no understanding, so the next iteration starts from scratch.

**Why it happens.** The operator conflates "I know how to launch a server and run a benchmark" with
"I am executing the workflow." The benchmark is one step of several. Going to it feels productive —
the device is running, numbers are appearing — but it is the serving-layer equivalent of writing code
before reading the spec.

**Rule.**
- Treat the ordering as a **hard dependency chain**. Analytic planning and entitlement are BLOCKING
  prerequisites; they cost minutes and zero device time.
- Enforce with a checklist artifact produced **before the first launch**: the parallelism recipe and
  its reasoning, the binding operator per phase with its entitlement floor and roofline ratio, the
  single config to compile first, and the hypothesis that config tests. If this document does not
  exist, the workflow has not started — regardless of whether a server is already running.
- Recognize the tells: more than one degree compiled without an analytic recommendation; the words
  "bandwidth-bound" or "collective-dominated" written with no entitlement calculation or profile
  cited; a third compile with no specialist consulted; a ledger with measurements but no
  parallelism-plan or entitlement section.

### B3 🔴 Partial compliance with an enforced stage order — and the ledger that *understates*

**Pattern.** A mechanically-enforced workflow is entered correctly: early stages are opened,
completed, recorded with artifacts. Work belonging to later stages is then performed without ever
opening them — the recording tool used for the quick stages and bypassed for the slow ones. The
ledger shows later stages "pending" while their artifacts sit on disk. Every familiar ledger failure
concerns *overstatement*; this one understates, and corrupts just as effectively: the trail no longer
reflects what was measured, the enforced ordering silently stops constraining anything, and stage
prerequisites that exist to prevent wasted work never run.

**Rule.**
- **Opening a stage is part of doing the work, not paperwork afterward.** If an artifact exists for a
  stage never opened, the sequence is already broken — repair it by opening and recording that stage
  before producing anything further.
- Audit the ledger in **both** directions: claims recorded without support, *and* work done without
  being recorded (J3).
- Selective use of an enforcement mechanism is worse than not using it, because it produces a trail
  that looks compliant precisely where it is most likely to be inspected.
- Check the sequence state whenever a stage's output is produced. One status command is the only thing
  that catches this class.

### B4 🔴 Owning the sequencing yourself, and performing the analysis a specialist owns

**Pattern (two faces of one error).** *Downward:* the orchestrator performs analysis that specialized
components exist for — computing bandwidth and roofline estimates, reasoning about collective scaling,
proposing kernel hypotheses — all inline. The result is lower-quality analysis (the specialist has a
validated model, worked examples, feasibility gates and fabrication case studies), unverifiable claims
(no artifact, no query, just narrative confidence from "obvious" first principles), wasted compile
cycles empirically discovering what an analytic step would have said, and skipped verification,
because self-generated analysis bypasses the gate applied to delegated claims.

*Upward:* a workflow whose purpose is to own a campaign end to end is instead invoked once per
hand-decomposed slice, with each slice's framing already fixed by the caller. Each invocation looks
correct and produces usable output. What is lost is everything the workflow provided *across* stages:
it never chooses the next step, never sees one stage's result in the context of the last, never applies
its cross-stage consistency checks. The caller becomes the only component with a view of the whole —
and therefore the sole reviewer of its own framing, a role nothing in the process audits.

**Rule.**
- **Give a workflow owner the objective, not a slice.** A caller-imposed decomposition replaces the
  procedure's judgement with yours at precisely the points it exists to arbitrate.
- **If a specialist exists for a sub-task, delegate to it.** The delegation table is closed and
  finite; check it before performing any analysis inline. The only exception is a specialist that is
  unavailable or returns nothing usable — and then run the analysis using *its* documented
  methodology, not ad-hoc reasoning.
- **The orchestrator's inline reasoning is hypothesis-generation, never conclusion.** Observations
  belong to the orchestrator; mechanisms belong to the analysis stage (G4).
- If you must drive stages yourself, **write down which cross-stage guarantees you have taken over** —
  sequencing, inter-stage consistency, choice of specialist path — and arrange a check for each.
  Guarantees do not survive the transfer implicitly.
- **A slice that succeeds is not evidence the decomposition was right.** Every slice can succeed while
  the sequence they compose is wrong. And **framing travels with a handoff and is not re-derived**: a
  framing error propagates unchallenged into every later stage.
- Tells that you are analysing what you should delegate: computing bandwidth, FLOPs or roofline ratios
  inline; writing "because" followed by a physical mechanism with nothing delegated; reasoning about
  kernel internals without the kernel specialist; more than two paragraphs explaining why a number is
  what it is with no delegated artifact cited.
- **Discovery is delegated too, when a specialist owns it.** Parallelism recipes, entitlement floors,
  profile rankings and artifact triage go to the specialist even though they are the plan's own inputs.
  What never transfers is the *timing* (discovery still completes before the plan is drawn — C16), the
  *verification* of what came back, and the environment preflight you must run **before** you can write a
  correct delegation prompt at all: device inventory, resolved paths, installed versions, free space (A6).
  Those are not analysis; they are the facts the delegation is built on, and a wrong one costs a round.

### B5 🟠 Finding an error in a purpose-built model's INPUT and rebuilding the model by hand

**Pattern.** A purpose-built analytic component produces a plan resting on mistaken inputs. The errors
are real and correctly identified — and the response is to retract the component's conclusions and
rebuild the analysis by hand: a fresh cost model, a fresh capacity derivation, a fresh operating-point
comparison. The hand-built replacement then carries every subsequent decision with no validation
suite, no cross-check against the component's calibrated formulas, and no reviewer. Catching a genuine
defect produced a *less*-reviewed artifact than the one it displaced, and lent it the authority of a
correction.

**Rule.**
- **Correcting an input is a re-run, not a rewrite.** Feed the corrected premise back in and re-derive
  with the same tool.
- Distinguish "this conclusion is wrong" from "this model is wrong." A bad input invalidates the
  output, not the machinery; only evidence about the machinery justifies replacing it.
- A hand-built substitute inherits none of the calibration of what it replaced and all of the
  authority of the correction that motivated it. That combination is the hazard.
- If you do build a parallel derivation, keep the tool's version alive and reconcile the two. Two
  models that disagree localize the error; a lone model cannot be wrong detectably.
- Note the contrast with B6: hand-built *infrastructure* announces itself through failed launches; a
  substituted *analysis* writes nothing tooling-shaped, fails nothing, and leaves no trace to notice.

### B6 🟠 Using an unsanctioned tool, the wrong measurement, or a hand-built harness the procedure reserves

**Pattern.** To get device numbers quickly, an out-of-scope benchmark tool is used, or the prescribed
measurement is substituted with a convenient one (wall-clock bench instead of the prescribed device
trace). The same failure with nothing to point at: the procedure reserves the measurement/timing
harness, the multi-replica serving strategy, and the parallelism scheme to dedicated tooling, and they
are hand-written anyway because each looks like a few minutes' work. They run — but every constraint
the reserved path encodes (valid replica topologies, correct isolation, the prescribed metric
definitions) is rediscovered one failed launch at a time, and the hand-rolled harness silently owns
the definition of the quantity the verdict rests on.

**Rule.**
- Re-read the active procedure's allowed-tools list and prescribed measurement path **before acting**,
  and use only those. A missing-seeming capability usually means a different intended mechanism —
  find it, don't substitute.
- Before writing any measurement harness, replica/serving strategy or parallelism scheme, check
  whether the procedure reserves it. If it does, use the reserved path.
- **"It's only a few lines" measures the writing, not the debugging or the risk.** The cost is paid in
  rediscovered constraints and in an unaudited metric definition.
- If a hand-rolled artifact already exists, reconcile its metric definitions against the prescribed
  ones before quoting any number it produced. The number may be right; what it *counts* is the open
  question.
- **Device time comes from the prescribed live system trace, not from a wall-clock bench and not from
  an isolated graph replay** (a standalone replay undercounts multi-rank collective traffic). A bench
  `elapsed_time` is end-to-end, not device time.
- **"Device time meets the SLA" ≠ "the request meets the SLA."** End-to-end adds tokenize, dispatch,
  device↔host readback and interpreter cost. If the SLA is end-to-end, report device time as the
  trustworthy *lower bound* and name the host portion.

### B7 🔴 Accepting a "blocked" or "the tool was unavailable" claim without personal verification

**Pattern.** A delegated run reports a target as BLOCKED — a checkpoint absent, a stack torn down —
and the blocker is accepted. The checkpoint was present, every shard of it, at the exact path the same
report cited (a hub cache holding config only had been confused with the staged weights). When the
blocker was disproven and the work re-tasked, the next round produced a **new** blocker, also
overstated: a project *dev* virtualenv that had never been a serving stack, while the sanctioned
platform environment was intact and importable.

The mirror form asserts nothing checkable: *"capability X was not available, so I did Y instead."*
Verification effort concentrates on claims that assert something, so an unavailability claim reads as
an apology and gets waved through — silently deleting a workflow step, often the one that bounds
feasibility.

**Rule.**
- **The orchestrator verifies every blocker personally, at the same depth, every time.** A blocker
  converts a measurable target into an unmeasurable one — the highest-cost claim in the workflow.
- A real blocker = exact command + log excerpt + on-device error. Anything less is a hypothesis. Route
  genuine build/compile failures to the artifact-debugging path instead of accepting a blocker.
- **Expect blocker substitution.** When the first is disproven, the next round often supplies a fresh
  one rather than running the job. Verify the replacement before re-planning around it.
- Treat "I could not use X, so I did Y" as a **high-cost claim requiring proof**: try X once yourself
  (usually cheap); ask which workflow step the fallback replaced and whether the fallback can
  discharge that step's *purpose* (an ad-hoc estimate cannot discharge an entitlement gate). A
  legitimate report — "I ran X, got error E, could not determine Z" — names the error and is trusted.
- Distinguish the project dev environment from the sanctioned runtime environment. A dev environment
  carrying a foreign-accelerator build is not the platform stack; its breakage says nothing about
  whether the workload can run.
- **Never certify completion on a self-inflicted blocker.** A configuration you chose is unfinished
  work, not an impediment.

### B8 🟠 A mandated capability absent from the environment, silently skipped

**Pattern.** A procedure mandates a specific capability for exactly the class of problem later
encountered. It is not present in the environment. Instead of being reported it is silently not used,
and the problem is worked by hand at the cost of a long, device-bound detour. Its absence is
established only when the omission is questioned afterward. This is the exact inverse of the
overstated blocker (B7): there a surmountable obstacle was reported as a blocker; here a real gap was
reported as nothing at all — and the requester, who could likely have supplied it in moments, never
learned it was missing.

**Rule.**
- Confirm during preflight that every capability the procedure mandates exists in the environment —
  not at the moment you first need it.
- If a mandated capability is absent, **name it in the next status report**, with what you did instead.
- The test that distinguishes this from an overstated blocker is **who can act on it**: a gap the
  requester can close must be surfaced; an obstacle you can work around must not be called a blocker.
- "The tool wasn't available" must never first appear in an answer to a question.

### B9 🟠 One giant delegation prompt and one final report, instead of verified facts and incremental evidence

**Pattern.** Multi-thousand-word briefs carrying ten simultaneous mandates are issued; the recipient
still fails the most elementary check in the list (does the cited file exist) and returns one large
end-of-run report whose central metric is mislabeled. The round's entire value must be reconstructed
by audit.

**Rule.**
- **Front-load a verification gate, not more instructions:** "verify and report these N facts about
  the environment, then proceed." A handful of confirmed facts outperforms pages of competing mandates.
- **Require incremental evidence** — each measurement reported with its artifact path as it is
  produced. A single final report concentrates unverifiable claims and makes the round all-or-nothing;
  a bad anchor metric should surface in minutes.
- **Version the report and retract explicitly**, with a `Corrections`/`Retractions` section naming each
  defect and its fix. Retracting is cheaper than defending.
- **Carry the failure forward, not just the fix.** When re-tasking, state the disproven claim
  explicitly ("X is FALSE, here is the command that proves it") so the next round cannot re-inherit it.

### B10 🟠 Full-reading huge machine-generated artifacts

**Pattern.** Multiple large generated artifacts (per-op JSON, traces, logs, compiler IR) are read end
to end, burning a large fraction of the context budget for data where a few summary rows were needed.

**Rule.** Size-check first (listing, line count); extract only what is needed with targeted queries
(`grep`, offsets, `jq`, dataframe reads) — the `by_type` summary, not every per-layer instance. Treat
context budget as scarce: one giant read can cost the rest of the session.

### B11 🟠 A self-sufficient brief read as specifying its own procedure

**Pattern.** A task arrives as a long, self-contained brief carrying its own verification checklist,
lever list, measurement rules, and reading list. Because the brief feels complete, its reading list is
treated as exhaustive, and the workflow specification that actually governs the task is never loaded;
its state-mutating tooling is then driven from inference about its purpose. Completing the brief's
front-loaded checks compounds the error: finishing a verification gate produces a sense of having been
*cleared to execute*, so the procedural question is never asked. A run of small, urgent, local
failures — each with an obvious individual fix — then crowds it out for the rest of the session.

**Rule.**
- **A brief cannot specify its own procedure.** Resolve which specification governs and load it before
  the first tool call — *especially* when the brief feels complete enough to make that seem
  unnecessary. Completeness is a reason to ask, not to skip.
- Treat any reading list in a brief as a **floor**. It tells you what the author knew you would need,
  not what the procedure requires.
- **A completed verification checklist clears facts, not procedure.** After a preflight gate, state
  explicitly what the *procedure* requires next, separately from what the task needs.
- **A streak of small local fixes is the characteristic cover for a skipped procedure.** When several
  consecutive failures each have an obvious local fix, that is the moment to re-ask the procedural
  question, not the moment to keep fixing (D14 is the diagnostic form of the same trap).

### B12 🔴 Forbidding a mechanism without naming the sanctioned replacement

**Pattern.** A brief prohibits a specific tool, harness or workflow for the task — on legitimate grounds —
and names nothing in its place. Worse, it gestures at a substitute without resolving one ("or whatever
harness the procedure prescribes"), which reads as licence to improvise. The recipient still needs the
capability, so it builds one. The improvised path then re-derives stack- and model-specific plumbing one
failed launch at a time — input-preprocessing mode, dtype and quantization wiring, cache roots, readiness
limits — and the entire allotment goes into the substitute rather than into the task, frequently without
ever reaching a first successful run. A negative constraint is cheap to write and expensive to satisfy,
because it transfers no information about what to do instead.

The same brief usually contains everything needed to prevent this: the environment already provides
sanctioned paths for capture, for analysis, and for the measurement the verdict rests on. They went
unnamed, so the one *named* instruction on the subject was the prohibition, and the recipient optimized
against the only routing signal it was given.

**Rule.**
- **Every prohibition names its replacement in the same sentence**, by the exact tool or entry point to
  use instead. "Do not use X" is an incomplete constraint; "use Y, not X" is a complete one.
- **Confirm the replacement exists and is reachable before prohibiting anything.** A prohibition whose
  substitute you have not located is a request to build one — B8's absent-capability gap, arriving by your
  own hand.
- **Delete permissive escape hatches.** A clause offering "or the harness the procedure prescribes"
  returns a routing decision to the recipient's discretion at precisely the point you meant to remove it.
- **Enumerate the capabilities the task requires and map each to a named owner *before* writing any
  constraint.** A capability with no named owner will be improvised, and the improvisation will silently
  own the definition of whatever quantity it measures (B6).
- **Prohibit only what you are willing to route.** Where no substitute can be named, say the capability is
  unowned and resolve it, rather than banning the only path known to exist.

### B13 🔴 Writing a gate as an ordering instruction, and a ledger as a deliverable

**Pattern.** A brief states the required sequence in prose — do the analytic work before consuming the
expensive resource — and lists the running record among the deliverables to be produced at the end. Both
are then satisfied in the wrong order, or not at all, and nothing detects either. An ordering sentence has
no observable, so a recipient that starts at the expensive step violates nothing checkable. A record
listed as a deliverable is understood as a terminal document, so it is deferred to a finish an
interruption may remove. What survives is expensive artifacts with no plan above them and no
interpretation beside them — the pile J1 describes, arriving through the brief rather than through the
session.

**Rule.**
- **Express a gate as a named artifact that must exist before a named class of action:** "file P exists on
  disk before the first launch." An ordering instruction is a preference; an artifact precondition is a
  gate. This is B2's pre-launch checklist artifact, restated as the *delegation's* precondition rather
  than as the recipient's first task.
- **State the check and name who runs it.** A precondition nobody evaluates is prose (C18's abort
  authority applied to a brief).
- **Frame the running record as a per-step obligation with a write-before-proceed rule**, never as a
  deliverable. "Append the row, then take the next action" is checkable at any instant; "produce a
  tracker" is checkable only at the end, which is exactly when it is unavailable.
- **Ask for the first artifact before authorizing the expensive class of work**, so the sequence is
  enforced by what you have received rather than by what you requested.
- **The tell:** a brief whose constraints are all sentences and whose checkable items are all final
  documents. Nothing in such a brief can fail early, so everything in it fails late.

---

## Part C — What must be derived, enumerated, or read from source before any device time

This part covers defects introduced while the plan is being *written*, when there is no measurement to
contradict them. They are the most expensive class, because every later measurement is taken inside
the space the plan defined: an axis never enumerated, a configuration wrongly pruned, or a constant
quietly chosen cannot be recovered by any amount of careful measurement downstream.

### C1 🔴 Reasoning upward from measurements with no entitlement floor to divide by

**Pattern.** A sweep produces the same throughput plateau across several topologies and the plateau is
written up as a per-node bandwidth or compute ceiling. No floor was ever computed, so the claim is a
*label on an aggregate* rather than a ratio against anything — and unfalsifiable in the direction that
matters, because a plateau is exactly what a hardware wall looks like **and** exactly what a uniformly
inefficient implementation looks like. A first-order roofline computed afterwards can put the floor an
order of magnitude above the measurement, at which point the deployment was declared "at the ceiling"
while consuming a small fraction of both peak compute and peak bandwidth.

**Rule.**
- Compute the entitlement floor **before the first device launch** — it costs minutes and zero device
  time: `peak_FLOPs / (2 · params)` for the compute floor, `BW / bytes_per_item_per_step` for the
  memory asymptote, then solve the roofline for **how much concurrency the floor would need**. That
  last number is the one that exposes the gap.
- Run the analytic entitlement/roofline **before profiling**. It tells you which experiments are worth
  running.
- Put a **`measured / floor` ratio column in the ledger's row template**, so every measurement is born
  with its ratio attached. You cannot write "at the ceiling" beside a cell reading 20×.
- Any ceiling or infeasibility claim is stated as `measured X vs floor Y = N×`, with the floor's
  derivation on disk. A mechanism named from a bucket total, an op count, or the *shape* of a plateau
  is rejected by construction.
- Derate honestly rather than dismissively: nobody lands on the floor, but a competent implementation
  sits within a small factor of it. Missing a target by less than the distance you sit from the floor
  is an **efficiency** finding, not a **feasibility** finding, and the two route to completely
  different next steps.

### C2 🔴 Brute-forcing the parallelism search by serial compiles instead of using the analytic model

**Pattern.** The topology search is conducted empirically: compile a degree, measure, tear down,
compile the next, hit an error, tear down again — each paying full weight-load and compilation cost.
No analytic step narrowed the search, though one exists that determines the interior-optimum degree in
seconds with zero device time and outputs a single recipe to compile and measure.

This is distinct from C1: C1 asks "how efficient is this config relative to physics?"; this asks
"which config should I even try first?" The parallelism model produces the *operating point*; the
entitlement model produces the *floor at that point*. Both are pre-device steps and both are routinely
skipped in favour of "just try it."

**Rule.**
- Before the FIRST compile, run the analytic model (hardware bandwidth × FLOPs, model dimensions, head
  counts, collective topology) to identify the interior optimum. For dense GQA models, collective cost
  grows with TP while weight-read time falls; there is a crossover, and blindly using max-TP or min-TP
  misses it.
- The same model produces the throughput recipe: `DP = total_cores / TP_opt`, stated as a gate output
  **before** any device time.
- Compile only the recommended recipe. If it fails, diagnose *that* config rather than retreating to
  "try all degrees." Each subsequent compile must carry a stated hypothesis about what it will reveal
  that the model could not predict.
- Track `(compile cycles spent) / (compile cycles the analytic model would have required)`. Above ~2,
  the search was not informed by the model and the methodology has broken down.

### C3 🔴 Reconciling a joint latency+throughput SLA into one operating point instead of a pair

**Pattern.** A joint SLA is treated as a single tuning problem, and a config that serves one leg is
assumed to serve both — or the SLA is declared infeasible without ever checking it as a pair.

**Rule.**
- Reconcile a joint SLA into a **pair** of operating points: latency = minimum-latency single request,
  which selects an **interior-optimum** shard degree (compute-bound prefill favours a higher degree,
  collective-bound short-sequence work favours a lower one — do not assume the maximum); throughput =
  aggregate at concurrency, which selects `DP = cores / TP` replicas plus a concurrency sweep.
- Expect the two legs to pull in **opposite** directions (latency ⇒ more devices per replica;
  throughput ⇒ more replicas). Check both capacity caps before promising a joint operating point.
- **Never declare a joint SLA infeasible without checking it as this pair.**
- Note that the pair framing governs *configuration choice*, not evidence: a conjunctive SLA still
  requires a single run in which both legs hold (E4).
- **Latency is monotone in shard degree only while the phase is compute-bound and collective cost stays a small
  share of the step.** Once per-step collective cost is comparable to the compute it hides behind — short
  sequences, small batch, high degree — the curve turns over and the optimum is interior. "More devices per
  replica for latency" therefore identifies the *direction*, never a licence to skip the interior points: sweep
  the degree and locate the turn (C11).

### C4 🔴 Assuming higher TP gives more KV room, hence a bigger batch

**Pattern.** With grouped-query attention, KV can be sharded across at most `n_kv_heads` ranks. Beyond
`TP = n_kv_heads` the extra ranks hold **replicated** copies, so per-**rank** KV bytes/token is
constant for all `TP ≥ n_kv_heads`, per-**replica** total KV grows linearly with `TP` (factor
`TP / n_kv_heads`), and the additional memory higher TP brings is consumed exactly by that replication:
**maximum decode batch per replica does not improve at all.** Doubling TP past `n_kv_heads` therefore
leaves batch unchanged, roughly doubles per-step collective cost, and halves DP — a compounded loss. A
capacity estimate dividing KV by `TP` instead of `min(TP, n_kv_heads)` overstates achievable batch by
the replication factor and inflates every derived ceiling.

**Rule.**
- Compute KV per rank as `L × ceil(n_kv_heads / min(TP, n_kv_heads)) × head_dim × 2 × dtype_bytes`,
  then multiply by `TP` for the replica total.
- For throughput-oriented serving, treat `TP = n_kv_heads` as the natural ceiling on useful TP and
  spend remaining devices on DP. The split is: TP for latency, DP for throughput, crossover pinned at
  `n_kv_heads`. Note the asymmetry: `n_kv_heads` is a hard ceiling on useful TP for **capacity**, and only a soft
  one for latency, where the optimum can sit either side of it and must be swept (C3).

### C5 🔴 On prefill-heavy workloads, treating output throughput as a `DP×TP` tuning problem

**Pattern.** Aggregate output tok/s is assumed to scale with per-replica decode batch and replica
count. Where the stack executes prefill and decode as **mutually exclusive** phases — commonly prefill
at batch 1, blocking decode while it runs — output throughput per replica instead follows the phase
time-share:

```
output_tps = OUT / (IN/P + OUT/D)      P = prefill tok/s, D = aggregate decode tok/s
```

When `IN ≫ OUT` the `IN/P` term dominates and output throughput becomes insensitive to `DP×TP`.
Raising concurrency then *deepens the serial prefill queue*: latency can degrade by more than an order
of magnitude while output throughput stays capped. Meanwhile **total** (input+output) throughput can
look excellent, because it counts the large input (A3).

> **Identity vs bound — the trap inside this lesson.** Substituting *measured* `P` and `D` into the
> formula reproduces the measurement you took. That agreement proves nothing: it is an **identity**
> describing what one deployment did, not a **bound** on what the hardware can do. A real bound
> requires **entitlement** `P` and `D`. Never present a measured-input formula as a ceiling (C1, G1).

**Rule.**
- Compute the phase time-share **first** on any prefill-heavy target; establish whether the binding
  term is `IN/P` or `OUT/D` before sweeping parallelism.
- Confirm the phase-exclusivity claim in the scheduler source (does the generative path admit more than
  one prefill per batch, or is multi-prefill packing gated to other runner types?) and in the engine's
  own periodic metrics. Both are observations: they establish which term to test, and the claim that the
  term *binds* still requires a profile citation before it steers a compile (G4).
- Derive the capacity caps:
  `cores_per_replica = ceil(weight_bytes / mem_per_device) × cores_per_device`, then
  `DP_max = floor(total_cores / cores_per_replica)`. When weights exceed one device, this — not total
  core count — bounds throughput.
- Use entitlement values, never measured ones, in any expression you intend to call a ceiling.

### C6 🔴 Treating a library default as neutral when its own documentation disowns it

**Pattern.** A capacity-limiting default (a cache-budget cap fraction, a memory-utilization ceiling, a
bucket list) is left at its shipped value because defaults read as neutral. But the constant's own
design notes describe it as a *safety-first* value derived from worst-case compile behaviour on **one
particular model family**, and carry an in-tree TODO to replace it with a compile-safe estimator.
Inherited unexamined onto a different architecture, precision and shard degree, it caps the exact
quantity the throughput leg depends on — and the resulting capacity is later reported as what the
hardware allowed, while other callers in the *same repository* run the knob several times higher.

**Rule.**
- Grade a default by its **provenance**, not by its status as a default. A constant whose documentation
  names the family it was tuned on, or carries a TODO, is an *open parameter* for every other family.
- Search the repository for other callers overriding the same knob before accepting it. In-tree
  overrides are simultaneously a licence and a sizing hint.
- **Where the effective value is a `min()` of two terms, establish which term binds before tuning
  either.** A capacity computed as `min(budget_after_weights, fraction × budget)` will not move when
  you raise the fraction if the first term binds — and that null result gets misread as "capacity is
  not the lever." Log both terms and their ratio.
- Anything that caps concurrency is a first-order lever for a throughput SLA and belongs in the
  pre-launch parameter list, not the post-mortem.
- Record **every accepted default** as an explicit list in the report. Any tunable you did not set, you
  chose by default (H1).

### C7 🔴 Carrying a prior run's lever *value* forward instead of its *sizing rule*

**Pattern.** A playbook records a tuned constant that worked earlier ("raise the cap fraction to X")
and the number is copied into the new run as though the number were the lever. It is not — the lever is
the **rule** that generated it, and the rule's inputs (footprint, memory per device, context length,
shard degree) have all changed. The copied value can leave most of the per-core budget unused while the
documented sizing rule called for a far higher fraction; the run then reports the cap as "raised" and
treats the resulting capacity as a hardware limit.

**Rule.**
- When a playbook gives both a value and a sizing rule, **the rule is the lever**. Re-derive the value
  from your own configuration, record the derivation, and quote the prior value only as a sanity check.
- **Generalized clamp diagnostic:** if two configurations whose inputs differ by N× produce a *derived
  capacity* that is nearly identical, a clamp binds — the resource did not run out. Two very different
  inputs, one output ⇒ read the clamp; do not credit the input.
- Corollary: an A/B run while a clamp binds credits **neither** arm; changing the clamp and another
  variable in the same run credits neither either (E2).

### C8 🟠 Taking a configuration or environment variable's contract from its name

**Pattern.** A variable is set according to its name's apparent meaning. One execution mode rejects it
in favour of a differently-named variable; that second variable then requires values in units its name
did not suggest — per-rank indices whose *count* must equal the parallel degree, rather than the coarser
hardware units implied. The error that catches it is a bare length assertion naming neither the expected
units nor the count, and several attempts are spent converting one wrong guess into another, each paying
a full startup cost. The sibling failure: the stack does not read the variable at all, the launch
succeeds (env vars are not validated), the lever is inert, and the measurement is attributed to a lever
that was never active — the only signal a single "unknown environment variable" warning line.

**Rule.**
- **Read the code that consumes and validates a variable before setting it:** accepted forms, units,
  cardinality, and which execution modes reject it outright. Grep the *installed* source for the name;
  if it is not read anywhere in the installed version, it does not exist in this stack, however
  authoritatively it is documented elsewhere.
- **Name-implied semantics are a hypothesis.** Variables that can denote either hardware units or
  execution ranks are a recurring trap — establish which is expected and what the count must match.
- **A bare assertion failure is a units/cardinality question, not a value question.** Read the code
  around the assertion instead of trying another value.
- **Verify the lever is active by checking the *output* it controls** — the startup line reporting cache
  size or maximum concurrency, the directory artifacts land in. A variable producing no change in the
  controlled output is not active, regardless of being set.
- Where each attempt costs a long startup, the source read is always cheaper than the guess. Budget it
  against one failed attempt, not against zero.

### C9 🔴 Letting a stated structural constraint prune the search space unverified

**Pattern.** A stated hard constraint — "degree `D` must divide head count `N`", "only degree `X` is
supported", "feature `F` is required" — is used to prune the space without locating the code that
enforces it. Constraints read as *physics* while measurements read as *claims*, so they bypass the
scepticism applied to numbers, which makes them the more dangerous of the two: a false constraint
deletes configurations **silently** and no later measurement can surface what was never tried.
Constraint statements are typically true of one code path at one moment and are routinely relaxed
afterwards by mechanisms that leave the wording untouched — padding to a legal shape, replication of an
under-sharded component, per-component degrees (C10), head-count conversion. A constraint written in
prose has no version and no test.

**Aggravating pattern — attribution laundering.** Restating an inherited constraint as your own finding
("constraint confirmed", "verified by analysis") when all that occurred was reading it. Downstream, a
checked constraint becomes indistinguishable from a copied one.

**Finding one gate is not finding all of them.** Validation is layered: an **outer** gate decides
whether a feature is reachable at all in this deployment class; an **inner** gate checks its parameters
once it is. A search for the feature's *parameter names* finds the inner one, because that is where the
parameters appear — so the search that feels like it discharged this rule systematically returns the
wrong validator, answering "is my configuration legal?" while leaving "is this feature reachable?"
unanswered.

**An unresolvable citation leaves the claim UNVERIFIED — not false, and not void.** Where a document
asserts a constraint and cites a section that does not exist, both convenient readings (stale; never
real) are unwarranted. Its truth is simply unestablished — and if a planned configuration is valid only
if the constraint is false, it is load-bearing.

**Rule.**
- Every constraint that prunes the space cites the **enforcing symbol** (file + function) or is tested
  once directly. Grep for the enforcement: a real constraint has an assert or raise with a message; a
  divisibility requirement has a `%` or `//` on that quantity in the sharding path.
- Cite the **first** gate on the path, not the first gate you found. Enumerate all of a feature's
  validators and read them in execution order. A feature conditioned on a deployment-class flag is
  **unavailable** until that class is confirmed admissible (A4), regardless of its parameters.
- State provenance in the same sentence: `enforced at <symbol>` versus `stated, unverified`. Never
  re-attribute an inherited constraint to your own analysis.
- A constraint that would leave hardware idle or force an awkward topology is a **signal to hunt for
  the relaxing mechanism** — that cost is exactly why such mechanisms get built.
- Apply all of this to constraints and confirmations **you** produced earlier in the same effort. Once
  written down, your own inference looks like a fact; re-derive it before it prunes a second decision.
- For an unverifiable-but-load-bearing constraint: promote it to a stated risk and **test it with the
  cheapest isolated probe, at minimum scale, BEFORE building anything that depends on it** — never as
  part of the expensive configuration.

### C10 🔴 Designing a topology from `devices × replicas` arithmetic without enumerating the stack's own axes

**Pattern.** A deployment topology — multiple independent replica *processes*, manual device-window
assignment, an external request router — is designed from first-principles arithmetic over devices,
without enumerating the parallelism degrees the stack already exposes. A mature stack rarely has one
global sharding degree: it commonly exposes **per-component** degrees (attention, FFN, embedding,
output projection), sequence/context parallelism, expert parallelism, and a topology-aware device mesh
— all within a **single process and a single world**.

Three consequences, increasing in severity: you hand-roll orchestration, placement and routing the
stack already owns; you inherit constraints that bind *only the axis you invented* (contiguous
zero-based device windows, and an aggregate that is now a multi-process measurement problem — E5); and
you **prune configurations that are legal on the axes you never enumerated**, which no later
measurement can reveal.

The tell: a component's natural sharding limit differs from the global degree (e.g. a KV-head limit
against a much wider FFN), and you conclude that devices must idle, that the global degree must be
capped, or that cross-process replication is the only way to use the machine. Per-component degrees
exist precisely for that mismatch.

**Rule.**
- Read two things before drawing any grid: the **config dataclass/schema** the runner accepts, and the
  module that **constructs the process groups**. Every field naming a parallel or replication degree is
  an axis; every group constructed there is an axis. The grid's dimensions are that enumerated list
  with defaults — not `{devices per replica, replicas}`.
- Prefer an axis implemented **inside** one process over an equivalent assembled outside it.
- When a component's sharding limit differs from the global degree, look for the per-component degree
  before concluding hardware must idle.
- Only if no such axis exists may you build one — and then it is **code**, carrying a correctness and
  measurement burden, not a configuration choice.

### C11 🟠 Bracketing an axis and skipping the interior point your own plan named

**Pattern.** A sweep measures the endpoints (maximum degree; minimum degree × replicas) and never runs
the interior point — which the plan itself had named as the likely optimum before any device time was
spent. The endpoints bracket the answer and the conclusion is drawn as though the axis were swept.
Interior points, once finally measured, are routinely the best joint operating point of the effort.

**Rule.**
- If the plan names an interior optimum, it is the **first** point measured, not the fallback (D2's
  ordering rule applied to an axis rather than a schedule).
- Before concluding anything from a sweep, list the points the plan called for and diff that list
  against the points actually run. An unmeasured interior point means the axis is **unswept**, whatever
  the endpoints show.
- Enumerate the *valid* points first (divisibility and capacity constraints often make the apparent
  extreme unavailable), so "the favourable end" means the favourable end of the **feasible** set.
- **Addendum — your own estimate anchors the delegate's search.** When the party issuing the work
  includes a feasibility sketch, whatever configurations that sketch names become the configurations
  measured, however carefully it is labelled a hypothesis. If you brief a delegate with your own
  arithmetic, state the **axis** and require the interior points explicitly, and leave the location of
  the optimum unspecified. A hypothesis that names candidates narrows the search; one that names the
  axis widens it.

### C12 🔴 Choosing the efficiency constant that makes the target reachable

**Pattern.** A feasibility projection is built on an assumed utilization (arithmetic-utilization
fraction, achieved-bandwidth fraction, overlap fraction) and — when the first assumption misses — a
higher one is adopted and the outcome reported as the projection. Efficiency assumptions are the free
parameter of every analytic projection and their plausible range is normally wide enough to **straddle
the target**, so selecting inside that range makes the verdict a restatement of the selection. The
manipulation is invisible: the arithmetic is correct, the constants are cited, only *which* efficiency
was chosen is defective. The pressure is one-directional, because the alternative to a favourable
constant is a negative report.

**Rule.**
- Do not report a projection *at* an assumed efficiency. **Invert it:** report the **required**
  efficiency per term for the target to be met, and set it beside any efficiency actually observed on
  this stack. Required efficiency is both the deliverable and the experiment design — it names which
  term must be measured first.
- Give every projection a **sensitivity row**: verdict at the low, central and high end of each assumed
  constant. If the verdict flips anywhere in the plausible range, the projection **has no verdict**. A
  single-column projection with a met/missed column is a preference, not a projection.
- This is the mirror of G1: there, a large `measured/floor` gap may not be called a ceiling; here, a
  favourable assumed efficiency may not be called a plan. Both substitute a **choice** for a
  measurement.

### C13 🔴 Deriving a per-phase efficiency requirement that grants the phase 100% of device time

**Pattern.** The efficiency a phase must achieve is derived as `phase_floor / (replicas / target_rate)`
and carried for many iterations as *the* engineering bar. The denominator is the wall-clock budget per
unit of work, so the expression implicitly grants the phase **all** device time — while the same
devices must concurrently serve the other phase of the same target. The correct form is

```
required_efficiency = phase_floor / (f_phase × wall_budget)
```

so omitting `f_phase` divides by a number larger than reality and the error is **one-directional: it
always understates the requirement**, by exactly `1/f_phase`.

The severity is not the percentage. It is that **the corrected requirement can cross an architectural
ceiling.** Below the ceiling the target is "hard but reachable by ordinary optimization"; above it, the
target requires a specific hardware/toolchain capability — which may be unavailable. The same
arithmetic slip moves the verdict from *reachable* to *reachable only via a blocked mechanism*, which
is a different project.

**Rule.**
- Every per-phase requirement carries its **assumed `f_phase`, stated in the same expression.** A
  requirement with no time-share term is wrong by `1/f_phase`. Check the sum: if both phases'
  requirements were derived independently and each assumed the whole machine, they cannot both hold.
- Derive `f_phase` from the other phase's measured resource demand at the target operating point, not
  from a preference; note that `f_phase` itself depends on batch, cache dtype and replication factors.
- Publish the requirement as a **row across plausible `f_phase`**, exactly as C12 requires for any other
  assumed constant.
- **Re-check the corrected requirement against the architectural ceiling every time it moves,** and say
  so immediately and loudly if it crosses — everything downstream was scoped for the easier problem.
- Note the duality with C5: that entry says *throughput* is set by the phase time-share; this says
  *efficiency requirements* are too, with the share in the denominator rather than the numerator.
  Getting the first right and the second wrong is easy, because they look like different topics.

### C14 🔴 Computing the requirement once and comparing every later measurement against that fixed bar

**Pattern.** Required efficiency is computed early at one parallelism and batch configuration and then
treated as a fixed target while the configuration underneath changes repeatedly — so measured progress
is compared against a bar belonging to a configuration no longer in use.

**Mechanism.** Requirements derived from an entitlement model are **functions, not constants**. Their
inputs include the replica count, the per-replica wall budget (which scales with replica count), the
co-resident phase's time share (which scales with batch, cache dtype, replication), and the floor
itself (which scales with sharding degree). Change any and the number changes — sometimes in the *same*
direction as the measurement, so a real improvement leaves the gap unchanged; sometimes in the opposite
direction, so an apparent improvement is a regression against the bar that now applies. Because a
requirement looks like a target rather than a measurement, nobody re-runs it: it becomes the one number
computed once and never checked, while being the number every decision is scaled against.

**Rule.**
- Put the requirement and the measurement **in the same table, recomputed together, every iteration.**
  A requirement not recomputed alongside its measurement is stale by default.
- State the requirement's inputs beside it, so a configuration change makes the staleness visible.
- Compare each configuration against **its own** requirement. One bar applied across parallelism
  degrees is a category error, because the per-replica budget scales with the replica count.
- Report the **gap** (`measured/required`) rather than only the measured value, so a change in either
  term is visible in the quantity that decides the verdict.

### C15 🔴 Setting the engineering bar from an analytic queueing model at capacity = demand

**Pattern.** The required efficiency for a joint latency+throughput target is derived from a closed-form
queueing relation, and that number sets the bar — in the worst case placing it at the architectural
efficiency ceiling and framing the target as requiring near-perfect kernels.

**Mechanism.** Joint targets couple through **queue wait**, the most assumption-sensitive quantity in
the model. It diverges as utilization approaches 1, so in exactly the region a throughput target pushes
you into, small input errors produce unbounded output errors. It depends strongly on the **arrival
process**: an open-loop Poisson model and a closed-loop fixed-concurrency harness are different systems,
and the closed loop's finite population and lower burstiness give materially lower wait — so the
open-loop estimate *overstates* the required efficiency, manufacturing a false "this needs the ceiling"
conclusion. And a requirement derived where **capacity equals demand** is derived at utilization 1, a
divergent point: it has zero margin by construction and is therefore not a requirement at all.

**Rule.**
- Measure the **latency-versus-offered-load curve before setting any efficiency bar.** Sweep offered
  load at fixed concurrency, record the named percentile at each point, find the crossover, and convert
  it into the efficiency the deployment would need. It is inexpensive and needs no instrumentation.
- State the **arrival process** beside every queueing estimate (open-loop/Poisson,
  closed-loop/fixed-concurrency, deterministic) and state which direction its error runs.
- Derive requirements at an **explicit utilization below 1**, never at `capacity = demand`, and quote
  the utilization assumed.
- Until the curve is measured, label the modelled figure an **upper bound on the requirement**, not the
  requirement. A bar unchecked against a load sweep may not be used to characterize a target as needing
  the architectural ceiling (see also G7 on forms used outside their validity domain).

### C16 🔴 Writing the plan first and scheduling its discovery pass afterwards

**Pattern.** The plan and config grid are written first, and the discovery steps — tunable enumeration,
env-gate grep, capacity-cap derivation, constraint verification — are *instructed*, to be carried out
afterwards by a later phase or a different executor.

**Mechanism.** Discovery exists to **shape** the plan. Performed after it, discovery can only confirm or
embarrass a plan that has already fixed the axes, the configurations and the constants — and in practice
does neither, because the plan arrives carrying the authority of a decision while its inputs are
unchecked. The undiscovered assumptions then become the *constraints* on whoever executes it. The step
is nominally scheduled and effectively cancelled: the checklist is satisfied on paper, in the wrong
order, which is indistinguishable from not running it.

The self-diagnosing form: you find yourself writing instructions to verify something, for someone else
to perform, that you have not performed yourself — while handing over a grid built on the unverified
version.

**Rule.**
- The pre-flight checklist is a **precondition for writing** the plan, not its first item. Complete it,
  then draw the grid.
- Whoever forms a plan **owns** its discovery: the scheduling of it, the verification of what comes back,
  and the consequences of skipping it. Owning is not performing — each piece goes to the specialist that
  owns it (B4) — but discovery is not deferrable downstream of the decision it informs, and it may not be
  handed to the plan's *executor*, whose grid is already fixed by the undiscovered assumptions.
- Any verification you would instruct an executor to do, you must have already done.
- Mark every assumption that entered the plan without discovery, **in the plan**, so it reads as
  unchecked rather than authoritative. For each assumption, name the discovery step that establishes it
  and check whether that step has **already run**.

### C17 🟠 Characterizing the legs of a joint objective as conflicting when only one was engineered

**Pattern.** A joint objective combines a responsiveness bound and an aggregate-rate bound. Every
experiment is designed around aggregate rate — configurations chosen to maximize it, then swept over
load — while the responsiveness metric is merely recorded at each point because the harness emits it.
When no point satisfies both, the objective is reported as internally contradictory. No configuration
was ever built *for* the responsiveness leg: none tuned for short service time, none with admission or
batching controls set for latency, none at the operating point that leg would be served at. One leg was
engineered; the other was only observed.

**Rule.**
- Build and measure at least one configuration **per leg, tuned for that leg**, before characterizing
  the legs as conflicting.
- **A metric the harness happens to emit has not been optimized.** Distinguish "measured everywhere"
  from "targeted anywhere."
- Enumerate each leg's levers separately and confirm each list was exercised; the lever sets for
  responsiveness and aggregate rate are largely disjoint.
- A frontier traced from one leg's sweep describes that sweep, not the system.

### C18 🔴 A precondition check with no abort authority, and a silently clamped knob

**Pattern.** A launch requests a capacity-related knob at a chosen value. The platform accepts the flag,
then logs plainly that the request exceeds what the budget permits and that the **effective** value will
be clamped to a much smaller one. The warning is captured into a configuration-confirmation artifact by
design and echoed to the console, where it is read. The full-cost measurement is launched anyway, at an
offered load the platform had already announced it would not honour. The check existed, ran, produced
correct evidence, and changed nothing, because nothing was wired to fail on it — and the
requested-versus-effective mismatch then sat underneath every later failure as an unexamined confound.

**Rule.**
- **A precondition check must have abort authority.** Parse the served/effective configuration, compare
  every requested knob against its effective value, and **exit non-zero on mismatch** before any load is
  offered. Writing the evidence to a file is logging, not gating.
- Treat "effective value clamped to N" as a hard stop. Re-plan the point at a value the budget admits or
  raise the budget — never measure a configuration the platform has said it is not running.
- **Grep for clamp vocabulary, not just the knob's name** ("exceeds", "capped", "overriding",
  "effective", "falling back"). The clamp is usually reported in different words from the flag.
- A knob that cannot take effect makes its whole axis unsweepable. Determine each axis's reachable range
  before sweeping it.

### C19 🔴 Deriving capacity and batch from clean arithmetic

**Pattern.** Weight and KV footprint are computed from parameter counts times dtype sizes, an achievable
batch is derived from the remainder, and that batch feeds a throughput projection whose leading term
scales as `1/B`.

**Mechanism.** Two independent inflations compound. **Footprint is understated:** resident bytes also
include components excluded from the quantized majority (embeddings, output projection, norms),
alignment and padding, small tensors duplicated per shard, and framework workspace — `params × bytes` is
a *lower bound*, and frequently the number deciding whether a configuration fits at all. **Available
capacity is overstated:** stacks impose their own cache-budget caps (a fraction-of-free-memory cap, a
per-rank minimum, a block/page granularity, a configured maximum), so the batch the hardware could hold
is not the batch the allocator will grant.

**Rule.**
- Derive footprint from the **artifact**: sum on-disk tensor bytes per component, then confirm against
  resident memory after load. Never from a prose table.
- Before any `1/B` term enters a projection, establish `B` from the allocator's own arithmetic
  **including its caps**, and record those caps as accepted defaults (C6).
- Report every capacity-derived quantity with its **binding cap named** — cache-budget fraction,
  configured maximum, or physical memory. "Bound by memory" is usually wrong; a configured cap normally
  binds first.
- Any "configuration C does not fit" conclusion requires the artifact-derived footprint, not the
  formula-derived one.

### C20 🔴 Costing a multi-axis parallelism recipe without reconciling its core budget against physical cores

**Pattern.** A plan recommends a config across several parallelism axes (tensor × context/sequence ×
pipeline × data) and reports a headline aggregate throughput. The per-axis degrees are chosen to
maximize the aggregate, but their **product in cores** is never checked against the node's physical
core count. A downstream stage discovers the recipe requires far more cores than exist: the "best"
config is physically undeployable, and the aggregate it was ranked on assumed replicas that cannot
coexist on one node.

**Mechanism.** Cores consumed per replica = `TP × CP × PP` (every non-data axis multiplies). Data
parallelism then fills the remainder: `DP = floor(total_cores / cores_per_replica)`. A framework/engine
field that reports "cores" as the **tensor world size only** hides the context/pipeline multiplier, so a
recipe with `cores_per_replica` already equal to the whole node still *appears* to leave room for
several DP replicas. The reported node aggregate is then the per-replica figure times a replica count
the hardware cannot host.

**Rule.**
- Before ranking any config, compute `cores_per_replica = TP × CP × PP` and
  `DP = floor(total_cores / cores_per_replica)` explicitly; carry both next to the aggregate.
- Distrust any single "cores" field — verify it equals the product of the non-data axes, not just the
  tensor world size.
- A config whose `cores_per_replica` exceeds physical cores is not a config; re-plan on the axes that
  fit (extends A6 "assert a platform constant from memory"; complements C10 "designing a topology from
  devices×replicas without enumerating the stack's axes" — C10 warns against inventing an axis the stack
  already owns, this entry warns that the recipe's core *product* can silently exceed the node while a
  "cores" field shows only the tensor world size).

### C21 🟡 Budgeting or optimizing an operator the architecture config has nulled out

**Pattern.** An operator is assumed present because the architecture "has" it (a V projection, a bias, a
separate norm), and it is given a floor, a profile slot, or an optimization task — but a config flag has
removed it, so the effort targets an op that emits zero work.

**Mechanism.** Architecture flags can alias or fuse operators away: e.g. a "K equals V" flag aliases the
value projection to the key projection so **no V-projection matmul exists**; a "tie embeddings" flag
removes a separate output matmul; a "no bias" flag removes an add. The op still appears in a naïve
layer diagram and in a from-memory operator list.

**Rule.**
- Read the architecture flags from the config/source and mark which nominal operators are aliased, tied,
  or fused away **before** building the operator inventory. An op the config removed gets no floor, no
  profile expectation, and no kernel task.

### C22 🔴 Trusting a config knob's API acceptance instead of the compiled artifact, and running A/Bs that never changed the graph

**Pattern.** A knob (optimization level, a fusion flag, a layout option) is set through the framework
API, the call is accepted, and the run proceeds as if the knob took effect. In fact a platform shim
rewrote it to a default, or the launch reused a cached compiled graph — so the "changed" run executed
byte-identical code to the baseline, and the measured A/B delta (near zero, or noise) is reported as
"the knob does nothing."

**Mechanism.** Two independent ways the graph fails to change while the API says success: (a) a platform
code path silently reassigns the requested value to a supported default (no exception, sometimes no
log); (b) the compile cache is keyed on a hash that the knob does not perturb, so the launch hits the
cache and loads the old graph. Either way the **compiled artifact is identical**, and identical graphs
cannot produce a real A/B.

**Rule.**
- Prove the knob reached the compiler from the **compiled artifact**, not the API: inspect the emitted
  compiler command line and the graph/NEFF hash. A different setting that yields an **identical graph
  hash tested nothing** — the A/B is void, not negative.
- When a shim may rewrite the knob, confirm the effective value in the compiler invocation (extends C18
  "a silently clamped knob"; C18's rule is to gate on requested-vs-effective — here the ground truth is
  the artifact hash / command line, which also catches the cache-hit case).
- Force a distinct cache key (or a clean cache) when the axis under test does not itself change the
  hash, so the variable actually moves.

---

## Part D — Sequencing and protecting the scarce device budget

Device time is the binding constraint on these efforts, and the failures below are **scheduling**
errors rather than analysis errors: the profile deferred until the budget was gone, the decisive
config run last, each sweep point re-paying compilation, full weights spent on a question a reduced
model answers, documented traps rediscovered at full price, and dead time burned in oversized waits.
The corrective is a pre-launch plan, not more diligence during the run.

### D1 🔴 Substituting an end-to-end configuration sweep for a device profile — and capturing none

**Pattern.** The effort opens with a deployment-configuration sweep because those runs produce the
headline numbers directly. Each launch is expensive, the sweep consumes the whole budget, and
profiling is never enabled on *any* launch. The result is a scaling curve with **zero mechanism
evidence**: you learn a config is short of target but nothing about which resource binds, so the next
lever is chosen by guesswork. This is not a scheduling accident — it is the sweep crowding out the
profile, and it is easy to miss because the sweep *does* produce real, publishable-looking numbers.

**Rule.**
- Treat a device profile as a **hard prerequisite** for any mechanism claim, and an end-to-end A/B as
  **not a substitute**. Capture a profile after the *first* baseline launch, before the sweep — not
  after it, and not "if there's time left."
- Budget the session explicitly: one baseline launch, one profile capture, *then* the sweep. If the
  sweep would consume the budget, shrink the sweep, not the profile.
- **Verify profiling was actually armed** rather than assuming the launch inherited it: grep the launch
  log for the profiler-config/endpoint markers and confirm the artifact directory is non-trivially
  sized. A run that produced benchmark results but no trace files did not profile, however plausible
  its numbers look.

### D2 🔴 Scheduling the decisive configuration last, behind default startup timeouts

**Pattern.** The sweep is ordered by convenience (smallest topology first, widening as it goes), so the
configuration most likely to meet the target — usually the highest replica count — is launched **last**.
It then dies during bring-up: many replicas multiply the number of processes loading weights
concurrently from the same filesystem, so startup time scales *up* with replica count while the
engine-readiness timeout stays at its default. The session ends with the one decisive data point
missing, and every hour spent on configs that could never reach the target is unrecoverable.

**Rule.**
- Run the configuration your feasibility math says is **most likely to meet the target first**, so an
  interruption takes out a redundant point rather than the decisive one.
- Before launching any high-replica config: raise the engine-readiness/startup timeout well above
  default, and **stage the checkpoint to node-local disk**. Concurrent many-rank reads of a large
  sharded checkpoint from one shared or root filesystem is the standard cause of a bring-up that never
  reaches warm-up.
- Treat "startup time grows with replica count" as expected, and size the timeout from the measured
  single-replica load time × replica count, not from the default.

### D3 🔴 Misidentifying which timeout variable controls a startup phase

**Pattern.** A launch has several timeout variables controlling different phases — per-step iteration,
total launch-to-serving readiness, worker-termination grace. The operator raises the wrong one; the
compile finishes and warmup completes, but the front-end already gave up waiting and the
fully-functional engine is torn down. The log names the variable that governed — the one the operator
did not set. A long successful compile is wasted and the artifact cache may or may not have retained
its output.

**Rule.**
- **Before the first launch, enumerate ALL timeout variables the stack uses at startup** by grepping
  the source (`grep -r "TIMEOUT" $PACKAGE | grep -i "environ\|getenv\|env\."`) and list them in the
  launch script's comments.
- **Read the timeout variable name FROM the error message**, verbatim — not from memory.
- Set each startup timeout from the **measured** single-replica load time × replica count (D2), not to an
  arbitrarily large value. A generous timeout is not free protection: it is the mechanism by which a wedged
  bring-up consumes the session (D16), so raise the platform's readiness limits only as far as a healthy
  launch needs, and let failure be detected by **absence of forward progress in the log** rather than by a
  timeout expiring. Pair every launch with an operator-side deadline and a short polling interval keyed to
  progress markers (D10, D11) — that is the only mechanism that distinguishes "slow" from "wedged."
- After any timeout-triggered death, verify the compile cache retained its artifacts before relaunching:
  if it did, the next launch is fast; if not, a clear + fresh compile is needed (D7).

### D4 🟠 Not ordering the sweep around compiled-artifact reuse

**Pattern.** Every point in the sweep is launched as an independent server, so each re-pays graph
compilation and weight load. Because compilation dominates bring-up, the arithmetic goes badly: a
session can spend the large majority of its wall time in bring-up and only a small fraction measuring.
Nothing looks wrong while it happens — each individual launch is legitimate.

**Rule.**
- Enumerate the distinct **compiled-graph cache keys** your sweep needs *before* launching. Shard
  degree, block/slot counts, sequence buckets and the memory-utilization knob all change the key;
  replica count, batch size, arrival rate and profile capture usually do **not**.
- Group the sweep so each compiled artifact is brought up **once**, with every variation that reuses it
  — replica counts, batch sweep, arrival-rate sweep, *and the profile capture* — executed inside that
  single live server.
- Fold anything that would force a re-compile (notably profiler memory headroom, F4) into the **first**
  compile rather than discovering it later.
- Report the bring-up:measurement time ratio at the end of a session. If bring-up dominates, the sweep
  was mis-ordered, not the hardware slow.

### D5 🟠 Using a full-size-model launch for configuration shakedown

**Pattern.** A topology-level question — does this layout come up, is the plumbing correct, do the flags
parse — is answered by launching the full model, paying full weight load and full compile, and
discovering the failure many minutes in. The failure mode being tested had nothing to do with model
size.

**Rule.** Split the two questions and use the cheapest tool for each. *Does the config stand up?* →
reduced-size model (few layers) or the project's tiny end-to-end test: minutes, and it catches flag,
registration, topology and plumbing errors. *What are the SLA numbers, and what binds?* → full weights
on the real topology, the only authoritative source. Never let a topology-level startup failure be
discovered for the first time on a full-weight launch.

### D6 🟠 Spending device time before reading the tooling's own documented trap list

**Pattern.** A bring-up failure is hit, diagnosed from scratch, and worked around — when the failure and
its fix were already written down verbatim in the tooling documentation available at the start (in one
case both the many-rank checkpoint-contention mechanism *and* the "stage to node-local disk / raise the
timeout" fix). Device time is the scarcest resource and it was spent rediscovering documented behaviour.

**Rule.** Read the trap/recovery section of the tooling you are about to drive **before the first
launch**, not after the first failure, and lift its pre-launch checklist items (timeouts, staging,
memory headroom, method-vs-topology validity) into the launch command itself.

### D7 🔴 Clearing one compile cache while the stack reads from another — and remediations that silently no-op

**Pattern.** A stack has multiple compile-cache paths: a system-level compiler cache, a framework-level
cache under a cache root that defaults into the user's home, and possibly a user-pinned override. The
operator clears one and relaunches expecting a fresh compile; the framework reads a **different** path,
finds stale artifacts from a prior configuration, and loads them — producing results that look like a
model-quality issue rather than cache staleness. The signature: after a code change that should produce
a different graph, the model behaves exactly like the **old** graph, because it *is* the old graph.

The runbook variant is worse. An inherited runbook prescribes "delete the cache and retry", naming an
absolute path that belongs to a different layer of the toolchain and does not exist on this machine. The
recursive delete exits 0 regardless, the retry re-reads the untouched real cache, the identical failure
recurs, and the conclusion drawn is "cache clearing didn't help, so this is a genuine compiler bug" —
the strongest available evidence that a bug is real, manufactured entirely by a no-op.

**Rule.**
- **Enumerate ALL cache paths the stack can read from** and clear all of them whenever a graph-affecting
  change is made: the system compiler cache, the framework cache under its default root, and any
  explicitly configured root.
- **Pin the cache root explicitly** in the launch script rather than inheriting a default that varies
  with home directory, filesystem type and user. Several stacks silently relocate the cache when the
  default lands on a network filesystem, so the path you clean may not be the path you filled.
- **Resolve such paths from the code** — call the accessor the stack itself uses — rather than from a
  runbook's literal string, and print the resolved path into the run log before the first compile.
- **Any remediation that deletes, resets or clears must assert its target exists and fail loudly when
  it does not.** A silently-succeeding destructive step converts "not attempted" into "attempted and
  ineffective," which is a much harder claim to dislodge.
- **Verify the cache was actually missed** by checking compile time: a warm launch is fast, a cold
  compile is not. If your "fresh compile" finishes in warm-cache time, it found a cache you did not
  clear. Grep the log for cache hit/miss records after any graph-affecting change.
- Ask which *layer* owns the artifact before trusting a path for it, and state which layer a failure
  implicates.

### D8 🟠 Measuring a code path whose integration state was never verified

**Pattern.** A model arrives as source plus patches against shared packages, built for one package
version and applied to a tree at a *later* version. Whether each patch applied, applied with fuzz, or
was skipped is never checked before measurement. An opt-in fast path (a quantized kernel, a fused
kernel, a scheduler behaviour) then silently fails to engage, or engages half-patched, and its
performance is attributed to the model or the hardware. Install notes may even warn that the
environment can hold **two copies of a library**, where patching the wrong one leaves the runtime
unpatched — a warning easy to read past and impossible to detect afterwards from timings alone.

**Rule.**
- Before the first measurement, capture the integration state as an artifact: installed version of each
  patched package, the **resolved import path** of each, and a positive grep for a **sentinel string**
  unique to each patch. Record it in the ledger beside the baseline.
- Where the bundle names a verification command, run it and paste the output; where it does not, derive
  a sentinel from the patch diff.
- Treat a version gap between "patch built against" and "installed" as a **blocking unknown**. Applying
  cleanly does not mean being semantically correct against a moved API.
- Any performance conclusion about an opt-in path must cite evidence the path was engaged at run time —
  a log line, a shape, a memory footprint consistent only with that path — not merely that a flag was
  set.

### D9 🟡 Trusting a path or mount named in a deployment doc without verifying it exists

**Pattern.** A deployment guide prescribes cache/scratch locations on fast local storage; the commands
are copied forward onto a box where that mount **does not exist**, so caches and checkpoints silently
land on the single root filesystem. Nothing errors — but the slow shared path is exactly what makes
many-rank weight loading fall over later (D2), so the root cause is planted long before the symptom.

**Rule.** Verify every path, mount and device named in a deployment doc against the actual machine
before launch: existence, free space, and that it is the device you think it is. Where the prescribed
fast scratch is absent, say so explicitly and choose a deliberate substitute rather than letting a
default absorb it.

### D10 🟠 Blocking on oversized sleep intervals instead of tight polling loops

**Pattern.** A launch or compile is expected to take "a few minutes," so a large single sleep is
inserted before the status check. The operation finishes early and the session burns the remainder
staring at nothing; worse, a crash early in the window is not noticed until the sleep expires. Repeated
across compile, weight load, warmup and benchmark, cumulative dead time can exceed the actual work
time. It also creates the *appearance* of a hang and makes the session impossible to interrupt cleanly.

**Why it happens.** The operator has no model for the step's duration and picks a conservatively large
sleep. On the first run that is defensible; on subsequent runs the same constant is reused even though
the first attempt established the actual duration.

**Rule.**
- **Poll on short intervals; never a single large sleep.**
  ```
  for i in $(seq 1 N); do
    grep -q "READY_MARKER"   "$LOG" && break
    grep -q "FAILURE_MARKER" "$LOG" && { handle_failure; break; }
    sleep 15
  done
  ```
  Short intervals give bounded reaction time to any event; total timeout = interval × N.
- **Calibrate the timeout from the observed duration of the first attempt**, and use a much shorter
  timeout for a warm-cache repeat of the same graph shape.
- **Never sleep before the first check** — the event may already have happened.
- **Print elapsed time at each poll iteration** so the log shows progress rather than silence.

### D11 🟠 Reading elapsed time as progress, and blocking on a command that never exits

**Pattern.** Two properties combine. First, tooling subcommands that **serve** (viewers, UIs, inspection
APIs) perform their batch work, then bind a port and idle indefinitely; exiting is not part of their
contract. Second, placing such a command in a pipeline that waits for end-of-input blocks the parent
forever, so the whole step appears to be running. Every visible signal is consistent with work: the
process is alive, resident memory is large, cumulative CPU is nontrivial — because a lifetime average
amortizes a short busy prefix across a long idle tail. Nothing in the process state distinguishes
"computing" from "finished, waiting for a client that will never connect." The trap sharpens when the
command was reached as a **fallback** after a preferred invocation failed.

**Rule.**
- Prefer a subcommand that **converts and exits** over one that serves. If only a serving subcommand
  exists, run it detached with an explicit completion condition, never in a blocking pipeline.
- **Never make a step's completion depend on a long-running child's end-of-input.** No pipes into paging
  or tail-style consumers; redirect to a file and poll the file.
- **Every long-running step writes progress to a pollable artifact** and is polled on a timer, so "is it
  working?" is answered by a monotonically growing artifact rather than by process liveness.
- **Every long step is launched with an explicit deadline** and a stated expected duration. On overrun
  the default action is *inspect the output and reap*, not *keep waiting*: check output mtimes and I/O
  counters the first time you notice the duration.
- Read activity from **sampled deltas** (counters or output size at two points in time), never from a
  lifetime-average utilization figure.
- Note the converse relationship to D12: that entry says do not kill a step that has not finished; this
  says do not wait on one that already has. Both are settled the same way — by reading the step's own
  output instead of reasoning from elapsed time.
- Generalize: a rule of the form "notice X" will recur, because noticing competes with whatever you are
  focused on. A rule of the form "make X impossible to construct" will not — which is why the
  structural rules above replace the diagnostic one.

### D12 🟠 Aborting a warm bring-up on the strength of a backend-init log line

**Pattern.** A launch is killed after a generic "compiling model with <backend>" message, read as the
start of an expensive cold compile. Backend-init and graph-capture messages appear on **both** warm and
cold paths and do not indicate a cache miss. A warm bring-up reaches warmup-completion within minutes; a
cold compile cannot. Aborting loses the datapoint — and "aborted to save hardware time" then propagates
into a verdict as if the configuration had been measured and rejected (H9).

**Rule.**
- Grep forward for warmup-completed / server-started lines before concluding anything about cache state.
- Confirm a cache miss from the **cache directory itself** (artifact count/size before vs after) or an
  explicit miss record — never from an init banner.
- If you do abort, record the reason with log evidence so it can be audited rather than inherited.

### D13 🟠 Changing more than one axis when triaging a runtime failure

**Pattern.** A configuration fails at runtime (say a collective timeout on first forward). The next
attempt changes BOTH the suspected axis (reduce the slot/batch count) AND an unrelated one (a different
shard degree). When the second config works, the fix is attributed to the axis that was *narratively*
suspected — but two variables changed, so neither is confirmed. This is E2 applied to failure triage,
and equally damaging: a wrong root cause yields a workaround that either over-constrains the
configuration or does not address the real issue.

In this specific class, a collective timeout on first forward can be caused by a compiled collective
pattern exceeding a runtime buffer or timeout, by a slot/batch dimension whose allocation exceeds device
memory, by a shard degree that does not divide a model dimension cleanly at runtime even though it passes
validation, or by a genuine hardware/driver fault. Each has a different fix.

**Rule.**
- Change exactly one variable per retry. If `(degree=X, slots=Y)` fails, the next attempt is
  `(degree=X, slots=Z)` or `(degree=W, slots=Y)` — never both.
- For collective timeouts, first read the error details for which logical core failed and whether the
  runtime dump reports a collective-specific context; this often identifies the axis immediately.
- Before recompiling at a different degree, test whether the EXISTING compiled graph works at a reduced
  slot count — no recompile needed if the bucket already exists. This isolates "the degree is broken"
  from "batch too large at this degree" in seconds rather than minutes.

### D14 🟠 Nudging a magnitude and calling it a new hypothesis

**Pattern.** A full-cost measurement fails. The response is to reduce one load-related parameter and
re-run the identical shape; that fails the same way, so the parameter is reduced again and the shape run
a third time. Three full costs buy one bit of information, because no attempt changed the *mechanism*
under test. The evidence that would have ended the sequence was already in the first run's log — a
dumped scheduler/execution state naming the exact step that hung and how much work it carried, pointing
at a graph-variant or capacity mismatch rather than at offered load. No log was read between attempts.

**Rule.**
- **Distinguish changing a variable from changing a magnitude.** A retry at a different value of the same
  knob re-tests one hypothesis; it is a new experiment only if it can fail differently for a stated
  reason.
- **After the second failure of the same shape, stop launching and start reading.** Localize which step
  failed, and what it was doing, from artifacts already on disk before spending a third cost.
- **State the hypothesis and its discriminating observation before each retry.** "Reduce it and see" is
  not a hypothesis; if you cannot say what a pass would prove, the run has no information to give.
- Where one attempt costs minutes of load and compile, the log read is always cheaper. Budget it against
  one failed attempt, not against zero.

### D15 🟠 A finished or cancelled agent turn does not stop the processes it spawned

**Pattern.** A delegation launches a device or analysis job; the launching turn reports "cancelled," and
the run state is then reported as *nothing running* — while the spawned process keeps going for a long
time. Claiming "nothing is running" from memory was simply wrong.

**Rule.**
- After any interruption, cancellation, or subagent return, **check the process table and device status
  before asserting run state.** Never assert "nothing is running" from memory.
- Launch every serving/compile/ingest job with a **known PID and an explicit timeout**, logging to a
  file, so it can be found, monitored and killed later.
- **One device job at a time** on shared hardware.
- Tear down by **explicit process ID captured at launch**, then verify with read-only status checks.
  Avoid broad pattern-based kills: a broad `pkill -f <pattern>` can match — and kill — the controlling
  shell itself, interrupting the workflow and leaving state unclear. If unavoidable, scope it tightly and
  exclude the current process.

### D16 🔴 Relaunching over stale device state after an abnormal termination

**Pattern.** A server crashes or is killed mid-execution. The kill leaves collective state partially
initialized — device file descriptors released, so the device listing shows no holders, but internal
runtime structures not cleanly torn down. The next launch's collective bootstrap then hangs with most
ranks connected and a few never arriving, retrying against a long default timeout. This can burn a large
block of device time before anyone notices, because the device listing shows devices free and the process
tree is alive.

**Rule.**
- **After ANY abnormal termination (crash, OOM, timeout kill), perform a full cleanup before
  relaunching:** kill the entire process group rather than the parent PID
  (`kill -- -$(ps -o pgid= -p $PID | tr -d ' ')`, then `kill -9` survivors); wait for device state to
  drain; verify from the device listing that no processes hold devices; check for hidden holders
  (`fuser /dev/<device>*`); and if the hang persists after cleanup, a device reset or instance reboot may
  be required.
- **Detect the bootstrap hang pattern programmatically** — if the log shows ranks awaiting bootstrap
  parameters without advancing for longer than a short threshold, the launch is stuck: kill, clean up, and
  retry. Do not wait for the default retry timeout to expire, and do not raise it: a long bootstrap timeout
  converts a wedged launch into a lost session (D3).
- **Never launch a new server without confirming the prior one is fully terminated.** For multi-process
  parallel servers, a bare `kill` is not sufficient.

### D17 🟠 Verifying a destructive cleanup only against what it targeted

**Pattern.** A cleanup is scoped carefully to an explicit list of paths and run; afterwards the surrounding
tree is found to have lost more than the command could have reached. Two causes produce this and both
matter: another actor is still live in the workspace performing its own cleanup, or one step in the
sequence had broader reach than its name suggested. Checking only that the intended targets are gone
confirms the *intent* and cannot surface the discrepancy — so the wider loss is noticed later, attributed
to the cleanup by default, and the live actor goes unnoticed while it is still writing.

**Rule.**
- **After any destructive step, list the parent scope, not only the targets**, and reconcile what
  disappeared against what the command could reach. An unexplained difference is evidence of a concurrent
  actor and is investigated before any further work.
- **Establish that nothing spawned is still live in the workspace before deleting anything in it** —
  process table and device state first (D15). A cleanup racing a writer produces a state that neither
  party's log explains.
- **Prefer an explicit target list over recursive removal, and assert each target exists**, so a no-op is
  loud rather than silent (D7).
- **Confirm by positive check that resources you depend on and did not target survived** — inputs, caches,
  staged data. Absence of an error is not confirmation.
- **State which effects you can attribute to your command and which you cannot.** A cleanup with
  unexplained side effects is an open item, not a completed step.

### D18 🟠 A left-default knob auto-adjusts into a state that aborts bring-up

**Pattern.** The launch specifies only the parameters the operator was thinking about; every other knob
is left to the stack's defaults. Two ways this then aborts bring-up, both minutes in, both easy to
misread as a model or environment fault. (1) A dimension that must be one of a **discrete supported set**
(e.g. a prefill/token bucket) is left unset, and an automatic adjustment made for an *unrelated* feature
raises it to a value **off** that set; the graph resolver rejects it at worker init. (2) A feature that is
**on by default** imposes a precondition that conflicts with the chosen configuration (e.g. a caching mode
that requires a prefill segmentation the single-bucket config does not provide), and init aborts citing
that precondition. In both cases the operator never asked for the offending setting — a default or an
auto-derivation supplied it.

**Rule.**
- **Specify the measured configuration explicitly and minimally.** Pin every knob that has a discrete
  supported set to a value *in* that set (often the simplest: set the token/prefill bucket equal to the
  max sequence length to collapse it to one bucket), rather than letting an auto-adjust choose.
- **Disable features not under test** (caching modes, speculative paths, auto-batching heuristics) unless
  the run exists to measure them; a defaulted feature carries preconditions you did not sign up for.
- **Read the accepted values back from the startup log** (the effective-args echo) and confirm they equal
  intent before waiting on a compile — a bring-up-aborting knob is cheapest to catch in the echo, not at
  worker init N minutes later.
- Treat the abort message as naming the knob to pin, not as a model/hardware fault (D3 for the analogous
  timeout case).

### D19 🔴 A control set through a mechanism the platform overrides runs silently at the default

**Pattern.** A compilation or scheduling control — the optimization/instruction-scheduling level is the
classic one — is set through a mechanism the operator *believes* governs it (a compiler-environment flag,
an inherited export), and the number is then attributed to that setting. But the platform layer selects
its own default for the route in use and **silently overrides** the flag: the flag never reaches the
compiler invocation. Unlike D18 this fails **quietly** — bring-up succeeds, a real measurement is
produced, and it is credited to a configuration that never ran. The tell is a single line in the startup
log stating the *effective* level, which names a different value than the flag requested.

**Rule.**
- **Set the control through the framework's own first-class knob** (the CLI/config option the platform
  reads), not through a lower-layer environment flag it may ignore for this route.
- **Read the EFFECTIVE value back from the log and require it to equal intent** before any timing is
  trusted; never assume a flag propagated to the compiler. This is the quiet sibling of D18's loud abort
  and of D7's silently no-op remediation — a setting that does nothing while looking set.
- Any performance figure must carry the **verified** level it was compiled at; a level assumed from the
  flag, not confirmed from the log, invalidates every comparison built on it (E2), and separately from
  whether that level is even usable end to end (validate through compile→load→execute→correct before
  crediting a level with anything — the kernel-side companion of this trap).

---

### D20 🔴 A compile cache keyed on a high-level graph hash silently reuses a stale artifact after a lower-level kernel edit

**Pattern.** A vendored / lower-level kernel body is edited, the workload is re-run, and the measurement
is unchanged — because the compile cache key is a high-level graph hash that does NOT incorporate the
kernel body, so the run re-served the previously-compiled (stale) artifact. The "no change" is the OLD
code executing, not a null result.

**Rule.**
- When benchmarking a kernel/graph-body edit, do not trust a "no change" result until you confirm the
  compiled artifact actually changed — compare the artifact hash/id, not just the wall-clock.
- Invalidate or bump the cache when the edit is below the cache key's granularity; know exactly what the
  key does and does not incorporate.
- Pair this with the multi-root cache trap: clearing one cache while the stack reads from another is its
  own silent no-op (D7).

## Part E — Is this measurement admissible?

Every entry here concerns a number that exists, is real, and is inadmissible for the claim it is being
used to support — because of what it counted, what it varied, how many samples it had, or which run
shape produced it.

### E1 🟡 Requesting a prompt + output that exceeds the maximum sequence length

**Pattern.** `IN + OUT > max_sequence_length` makes every request fail, often silently as a client-side
error, producing an empty result that looks like a server problem.

**Rule.** Assert `IN + OUT ≤ max_sequence_length` (with headroom) when building a benchmark, and
sanity-check with a single request before the full run.

### E2 🔴 Quoting a delta from runs that differ in more than the one variable

**Pattern.** A latency or throughput delta is reported between two runs that also differ in prompt
length, batch, dtype, slot count, or tree state — so the delta is not cleanly attributable even when the
caveat is noted.

**Rule.** Match every variable except the one under test, in the same session, before quoting a delta.
Any A/B that varies more than one of {topology, batch, slots, dtype, sequence length, code state} credits
none of them. An A/B that crosses a width/occupancy mismatch (E10) or runs while a clamp binds (C7) is
confounded — retract it or re-run it.

### E3 🔴 Quoting a headline aggregate without checking its arithmetic identity, and counting events instead of bytes

**Pattern (identity).** A harness reports a rate alongside a total token count and a request count — say
`total_num_tokens: 147456`, `num_requests: 72`, on a 2048-token bucket. `72 × 2048 = 147456` exactly: an
identity that closes on the bucket dimension proves the figure counts **padded bucket tokens, prompt
included**. The tell sat in the same four-line result that was quoted for two rounds.

**Pattern (wrong statistic).** A host-readback suspect is "cleared" by **counting events** (a hundred-odd
readbacks, unremarkable) without ever **summing bytes**. Most of those events turned out to be tens of
megabytes each, totalling gigabytes and seconds of transfer. The cheap-to-count statistic hid the
expensive one.

**Rule.**
- Reconcile every headline aggregate against shapes and counts before quoting it: `requests × seq_len`,
  `steps × batch`, `elapsed × rate`. If the identity closes exactly on a padded or bucketed dimension, the
  number includes padding.
- **Count bytes, not descriptors or events.** A handful of large transfers dominates hundreds of small
  ones; an event count can look normal while the byte total blows the budget. Same for DMA, readback and
  collective analysis.
- **A bucket size is not a sequence length.** Verify the *actual* token count (prompt characters ÷
  characters-per-token, or the harness's own prompt-length flag) before labelling any measurement with a
  context size.
- **Distrust computed and aggregated numbers hardest** — interval-overlap merges, cross-rank aggregation,
  warmup filtering — and recompute from raw fields. Direct field reads (a duration, a byte size, an event
  count) are usually right.

### E4 🔴 Evidencing a conjunctive SLA by stitching two runs at two load points

**Pattern.** A conjunctive target — latency under a bar **and** throughput over a bar — is certified by
stitching two executions: the latency from a `concurrency=1` low-load run and the throughput from a
separate saturation run at unbounded arrival rate. Presented as one operating point, it was two. When a
single finite-rate run is finally measured, the honest frontier appears: at the load where latency passes,
throughput sits below its bar; at the load where throughput passes, tail latency sits above its bar. The
"pair of operating points" framing (C3) is what made the stitch feel legitimate.

The lesson is that the conjunction was **unevidenced**, not that conjunctions are unachievable.

**Rule.**
- **An unbounded arrival rate measures maximum throughput and unbounded queueing; its latency numbers are
  meaningless as an SLA claim** — the percentile there is dominated by queue depth, not response time.
  Never quote a percentile latency from a saturation run.
- **A conjunctive SLA must be evidenced by a single run at a single finite arrival rate** in which both
  quantities are simultaneously true. Two runs at two load points can never demonstrate a conjunction,
  however well each is measured.
- C3's pair framing is about choosing ONE config that can serve both duties. It is not a licence to source
  each metric from a different load point.
- **Sweep finite arrival rates to locate the frontier** and publish the whole table (rate → worst tail →
  aggregate throughput → per-bar verdicts). The frontier table *is* the current iteration's deliverable when the
  bars look irreconcilable; it converts a miss from an opinion into a measured curve — without licensing the
  conclusion that the bars cannot be reconciled at all (K1).

### E5 🔴 Multiplying one replica by the replica count and reporting it as the aggregate

**Pattern.** A joint SLA is certified MET on an aggregate obtained by measuring **one** replica and
multiplying by the planned replica count, in a campaign where every run had a replica degree of 1 — the
multi-replica config was never launched. When the replicas are finally run concurrently and their rates
summed, the true aggregate falls materially below the extrapolation and misses the bar the extrapolation
had cleared. By then the `× N` figures have propagated into the analytic floor, the calibration record,
the tracker headline and several deep-dive documents.

**Mechanism.** The multiply is exact only if replicas are genuinely independent — disjoint devices, no
shared bottleneck. That is an assumption, not a measurement. Note the symmetry: multiplying your way *to*
a target and multiplying your way *to a rejection* are the same error.

**Rule.**
- A node aggregate is **measured** by launching N replicas, driving **one client per replica
  concurrently**, summing the per-replica throughput, and taking the **worst** (not the mean) percentile
  latency across replicas. Anything else is a projection.
- **Verify the parallelism knob actually took effect** before attributing anything to it. Grep the served
  config or engine log for the degree and the visible device window; do not assume a flag did what its
  name suggests.
- **Extrapolation is legitimate; unlabelled extrapolation is not.** Tag every projected figure inline and
  permanently — `(extrapolated ×N, not measured)` — in the same cell or sentence as the number, including
  analytic floors that were themselves built as per-replica × N. A footnote does not travel with the number
  when it gets quoted. Tag every aggregate `MEASURED` or `PROJECTED` in the **summary line**.
- **Colocation contention is only observable when replicas run together.** Per-replica rates measured in
  isolation cannot tell you whether replicas interfere through shared bandwidth, host CPU, NUMA or fabric.
  Near-equal concurrent per-replica rates are a *result* worth reporting, not an assumption to build on.
- Be most suspicious when the multiply is what carries a number **across** the threshold in either
  direction; that is where the unvalidated assumption decides the verdict.
- **Two concurrent replicas are a necessary gate, not a licence to extrapolate.** They falsify *independence*
  cheaply, which is the assumption the multiply rests on, and they legitimately size the full run. They cannot
  bound contention that only appears at higher occupancy — bandwidth saturation, host CPU, NUMA, fabric
  congestion — which is monotone in replica count. So run the two-replica check before any projection, and still
  measure at N before the aggregate faces the bar.
- **DP-style projection (`--evidence dp_projected`) is diagnostic.** A measured per-replica
  number multiplied by N cannot establish the concurrent N-replica aggregate. The two-replica
  check does not bound contention at higher occupancy. Measure all N replicas concurrently
  before certifying MET. The campaign controller accepts only measured node throughput;
  a `dp_projected` value remains diagnostic evidence.

### E6 🔴 A percentile with too few samples, and assumed monotonicity across an unmeasured gap

**Pattern.** The single operating point that "passed" the latency bar reported a P99 computed over a
handful of requests, clearing the threshold by a fraction of a percent. A P99 over six samples is the
maximum of six draws: it carries no tail information, and a margin that thin is far inside its own
sampling noise. The same verdict also rested on **assumed monotonicity** across a wide unmeasured load
gap, with "everything in between also misses" presented as measurement rather than interpolation.

**Rule.**
- **Match the sample count to the percentile.** A P99 needs on the order of 10²–10³ samples; with N
  samples the highest trustworthy percentile is roughly `1 − 1/N`. With few samples, report the maximum
  and the sample count rather than dressing it as a percentile.
- **State the margin next to the sample count.** A margin smaller than the statistic's own noise is not a
  pass — flag it explicitly.
- **A verdict must not rest on interpolation between measured points.** Measure at least one intermediate
  point; where a gap remains, say "assumed monotonic, unmeasured between A and B" *in the verdict*.
- Percentile latency under load is frequently **non-monotonic** near a saturation knee (scheduler
  batching, bucket transitions, admission effects). Monotonicity is a hypothesis about the system, not a
  property of percentiles.

### E7 🟠 Comparing sustained rates from runs with different herd sizes or durations

**Pattern.** Two sustained-output numbers for the *same configuration* differ substantially — one from a
few hundred prompts over several minutes, the other from a fraction of that prompt count over roughly
half the elapsed time. The gap reads as a real performance difference. It is structural: in a fixed-herd
benchmark, wall-clock includes an initial prefill wave and a tail drain during which decode occupancy is
below steady state, and that non-steady fraction is larger for the shorter run.

**Rule.**
- Sustained rate is a function of prompt count, duration and arrival pattern, not of the config alone.
  Only compare numbers taken at the same prompt count, arrival rate and I/O lengths; otherwise normalize
  or state the confound.
- **Run long enough that the steady-state window dominates**, and prefer a steady-state window measurement
  over a whole-run average when comparing configurations.
- **When two numbers for one config disagree, suspect run *shape* before suspecting the system.** Check
  prompt count, elapsed time, concurrency cap and arrival rate first — cheaper than any performance
  investigation.

### E8 🔴 A harness that emits full metrics for a run that failed

**Pattern.** A measurement point is launched straight at full cost and returns a **single** usable sample:
nearly every request errored, because a step downstream of the first unit of output deadlocked. The harness
nonetheless writes a complete, well-formed result artifact and prints the whole metrics table — mean,
median and tail latency, throughput, duration — every field populated, nothing marked invalid. The failure
count sits in that same table, unread. The point is then re-run at full cost twice more, and later a
latency figure from that lone sample is quoted as corroborating a baseline.

**Rule.**
- **Prove the shape on a handful of requests before paying full cost.** A minimum-scale probe that carries
  at least one request *past* the step that fails — beyond the first unit of output, into the steady-state
  path — costs a fraction of a point and localizes the defect. Never let the first execution of a new
  shape also be a measurement.
- **Encode a completion-rate acceptance gate that fails loudly.** Assert `completed / attempted` against a
  threshold as a postflight step; below it the run yields **no metrics** and writes no quotable artifact.
  Harnesses compute percentiles over whatever survived and will not tell you the run was void.
- **Read the success/failure counts before any derived metric.** They decide whether the rest of the table
  means anything, and they are exactly the fields the eye skips.
- **A metric from a run that missed its acceptance criteria is not a measurement, however plausible.** Do
  not carry it forward as weak, suggestive or corroborating.
- Distinct from E6, where too few samples were collected *by design*: here a full-scale run failed and
  still emitted a complete metrics table.

### E9 🟠 Collecting a sweep before the normalized comparison metric exists

**Pattern.** A sweep exists to decide whether a cost is **per-invocation fixed** or **per unit of work**,
by varying how much work each invocation carries. The quantity that answers this — time normalized per
unit of work, plus the capacity figure derived from it — is named in the deliverable but never implemented
as a reduction over the harness's raw fields. Points are queued while the only comparison capable of
separating the two hypotheses does not exist. Had a point succeeded, its per-invocation time would have
been **uninterpretable**: rising per-invocation time is the expected signature of *both* hypotheses once
work per invocation grows.

**Rule.**
- **Implement the reduction from raw fields to the reported metric, and exercise it on one trial point,
  before collecting a sweep.** A sweep of individually valid points can be collectively incomparable.
- When the sweep varies the dimension the metric normalizes by, **the normalization *is* the experiment**.
- Carry the normalized quantity AND the derived decision figure in **every row**.
- **Name the discriminating signature in advance:** which way the normalized quantity must move for each
  reading. A sweep with no pre-stated discriminator yields a table and no conclusion.

### E10 🟠 Compiling for a width the achievable occupancy can never fill

**Pattern.** Compiled graph width (a batch/slot bucket) is chosen independently of the concurrency the
memory budget can sustain, so every step executes the padded width. Two consequences, and the second is
more damaging: (1) a throughput tax that reads as "larger batch didn't help, so batch is a dead end"; and
(2) **invalid per-item cost evidence** — measuring "cost at 1 item" vs "cost at N items" inside one
fixed-width bucket compares *the same graph shape doing the same work*, so a flat result proves nothing
about fixed-vs-variable cost while reading exactly like proof of a large fixed overhead.

**Rule.**
- Match compiled width to **achieved** occupancy: read the startup log's reported capacity/concurrency and
  set width from it, not from the requested slot count. Re-check after anything that moves the memory
  budget.
- To measure per-item scaling, vary width **with** item count (multiple buckets in one server, or one
  server per width). A per-item cost measured at constant width is not a measurement.

### E11 🔴 Letting a single measurement path decide a verdict

**Pattern.** One measurement path decides a verdict while two or three independent views of the same
quantity were already available — and disagreed by a large factor. Serving stacks expose the same quantity
through several channels that fail differently: the benchmark client reports end-to-end blended numbers;
the engine's periodic metrics report per-phase capability; `actual_batch / step_time` reconstructs the
step; the device profile gives kernel time. Any one can be distorted (padding, queueing, host overhead,
phase interleaving) while the others are not. The companion failure: noticing a large anomaly, diagnosing
it correctly, and then recording it as a caveat while leaving the verdict standing.

**Rule.**
- **No number decides anything until ≥2 independent paths in your own data agree** within a stated
  tolerance.
- Grep the serving log on **every** run for phase-separated throughput, running vs waiting request counts,
  cache usage, and any reported concurrency limit — free, no instrumentation.
- A metric ≥2× off an independent view of the same quantity, **or** ≥2× off its own computed floor, is a
  **hard stop**: it becomes the top task, not a caveat. Banned phrase: *"this does not change the
  conclusion."* An anomaly explained but not eliminated is an open defect.
- Banned reasoning: *"that measurement cannot change the answer, so I skipped it."* It presupposes the
  number under suspicion and identifies precisely the experiment most needed — especially when the suspect
  number is the one crossing the threshold. This holds without exception: an unresolved disagreement between
  two views of one quantity is never time-boxed away (K4).

### E12 🔴 Confusing device-level with end-to-end served throughput — in either direction

**Pattern.** Two distinct metrics get conflated. **Device-level** throughput
(`batch / decode_step_time_device × replicas`) measures raw generation capacity, excluding prefill
serialization, host scheduling and request lifecycle. **End-to-end served** throughput
(`total_output_tokens / wall_time` from a serving benchmark) includes all of it. The confusion runs both
ways: an implementer reports device-level as though it satisfies a served-throughput SLA; an auditor takes
a served number from a *different and suboptimal configuration* and uses it to reject a device-level claim
about a better configuration that was never benchmarked end to end.

**Rule.**
- Always label which metric you are reporting: "device decode throughput" vs "served output throughput."
  Both are real and useful, for different purposes.
- A device-level measurement establishes the **ceiling** of what a served workload could achieve at that
  configuration with perfect scheduling. The gap between the two is the **prefill/scheduling tax** at that
  input length.
- **Never reject a throughput claim by citing a serving benchmark at a DIFFERENT configuration** (J8).
- **A first-principles estimate cannot stand in for the served measurement.** Deriving an expected
  end-to-end rate from a device-level number and a scheduling penalty (`device decode throughput ÷
  expected prefill serialization penalty`) is a projection over a configuration nobody served: it may
  motivate the run, it may argue that a claim is *not refuted*, and it may never verify one (G8). The
  served measurement at the claimed configuration stays an open item that blocks the verdict (J5).

### E13 🟠 Failing to capture environment and topology evidence at launch

**Pattern.** Replica isolation (disjoint device windows) is set correctly by the launch script via an
environment variable, but that variable is **never echoed into any artifact**. After teardown the
assignment cannot be reconstructed: serve logs record the parallel degree but not the device window, the
process environment is gone with the process, and no driver script recording per-replica arguments
survives. Disjointness then has to be argued from the launch script's *source* plus an incidental runtime
error from an unrelated failed launch — inference, not evidence. The claim was correct; the evidence for it
no longer existed.

**Rule.**
- **Echo the runtime environment into a file at launch**, beside the log: device/core window,
  visible-device variable, parallel degrees, port, PID, and the fully expanded command line. One `env |
  grep` plus `set -x` costs nothing and is the only copy that survives teardown.
- **Any evidence that lives only in process state is already lost.** Process environment, device allocation
  tables and in-memory scheduler state vanish at teardown; persist them at the moment of launch.
- **A launch script's source is not evidence that a specific run used it.** Scripts get edited and invoked
  with different arguments; only the run's own captured invocation proves what it did.
- **Distinguish "isolated by construction" from "isolated, verified"** and state which one you have. An
  error from a *failed* launch tells you about that failure, not about the successful run's topology.

### E14 🟠 Treating the achievable floor as fixed across toolchain revisions, and reading only one of device/host time

**Pattern.** A baseline is measured once, and every later gap is computed against it as if the toolchain
were constant. A compiler/runtime upgrade later changes the numbers, and the change is misattributed —
or missed — because only the wall-clock (or only the device time) was tracked.

**Mechanism.** A toolchain revision can move real cost **between layers**: device-execution time can
drop materially with no model change, while host overhead (dispatch, tokenization, sync, runtime init)
regresses by a comparable amount, so the wall-clock barely moves and hides a genuine device win (or a
genuine host regression). A single-number baseline cannot separate these.

**Rule.**
- Re-baseline after any toolchain revision; treat "re-measure on the new stack" as a zero-code lever,
  because the achievable floor is a property of the (model, config, **toolchain**) tuple, not of the
  model alone.
- Always report device time and wall-clock **side by side** and attribute any delta to the device or the
  host layer explicitly (extends E12 "device-level vs end-to-end throughput", and complements C14
  "computing the requirement once and comparing every later measurement against that fixed bar" — C14 is
  the *bar* going stale, this is the *achievable floor* going stale across a toolchain revision); a
  device improvement masked by a host regression is a real result that a wall-clock-only view discards.

---

## Part F — Is this profile the artifact I think it is?

### F1 🔴 Trusting a profile's *label* instead of verifying which graph was captured

**Pattern.** A profiling step returns a capture labelled "decode" (or "prefill"). Its numbers are real and
plausible, so its conclusions get reported — but the capture was actually the *other* phase.
Autoregressive inference compiles to two graphs with opposite bottlenecks (prefill: many tokens per step,
compute-leaning; decode: one token per sequence, memory/serialization-leaning), so a mislabeled capture
makes every downstream conclusion describe the wrong graph.

**Rule.**
- Before analyzing any inference profile, confirm the phase from **primary fields**: the per-instruction
  source/kernel signature (decode-kernel vs prefill-kernel names) and the per-step token/row count against
  the batch size. A "decode" profile with zero decode-kernel instructions is a mislabeled prefill profile —
  stop and recapture.
- Treat a delegated phase label as a claim to verify, not a fact. Make phase verification a hard gate in
  any capture delegation: "report decode-kernel vs prefill-kernel instruction counts; do not return a
  capture labelled decode if it is prefill."

### F2 🔴 Capturing decode under an open, high-arrival load that stays prefill-saturated

**Pattern.** Driving a capture with high concurrency and short prompts keeps the server prefill-saturated,
so a window that looks like "steady state" sits entirely on the prefill graph and the decode graph never
appears — the usual cause of F1. Skipping warm-up iterations does not clear a standing prefill backlog.

**Rule.** To capture decode deterministically, drive a **closed batch**: fixed small concurrency, long
output, ignore end-of-sequence, and **no new arrivals during the window**, so once the initial prefills
drain every sequence is decoding. Start the capture after the first-token latencies have elapsed, and pair
it with F1's phase-verification gate.

### F3 🟠 Acting on a profile-derived lever whose operating point ≠ the served operating point

**Pattern.** A profile captured at a small batch over-weights fixed per-step costs (weight read, per-layer
overhead) and reads "raise the batch / sequence-slot count" as the top lever — but at the *served* batch
that lever may already be realized, capped by cache capacity, or past the memory→compute crossover into a
different bound.

**Rule.** Record the profiled batch and context length beside every profile-derived lever and reconcile
them with where you actually serve before acting. Ask whether the lever is already realized or already
exhausted at the served point.

### F4 🟠 Not accounting for the profiler's own memory allocation before the first profiling launch

**Pattern.** The device profiler needs a multi-gigabyte pool plus a notification buffer **per profiled
core**. On a large model that already fills most of device memory, the profiler silently fails: generation
succeeds, the start-profile endpoint returns 200, but the output directory contains only empty stubs and
the log shows an allocation failure for profiler buffers. The operator then iterates on the
memory-utilization knob — too little freed, then too much (the model can no longer allocate its prefill
tensors) — and each attempt requires a fresh compile, because changing that knob changes the block count
and therefore the graph hash.

**Rule.**
- **Compute the profiler's memory budget BEFORE choosing the memory-utilization knob:**
  ```
  profiler_need_per_core  ≈ pool + notification_buffer
  available_per_core      = (HBM_per_device / cores_per_device) × util − weights_per_core − KV_per_core
  ```
  If `available_per_core < profiler_need_per_core`, reduce the *cache* footprint rather than the
  utilization knob — this avoids a recompile.
- **Test profiler activation on the FIRST request** (check for the allocation-failure line immediately)
  rather than discovering it after the full benchmark. One start-profile call plus one request is a
  seconds-long validation.
- Freeing headroom by shrinking concurrency is not free, so let the capture's **purpose** set the knob. For
  **structure** — which kernels run, in what order, on which engines, whether the expected fast path engaged — a
  low-occupancy capture is valid and the profiler's headroom wins. For **magnitudes** that will rank levers or
  support a per-item cost claim, occupancy must match the served point and the headroom must come from elsewhere:
  reduce the cache footprint, capture fewer cores, or fall back to a system-level trace needing no per-core pool.
  A capture taken at reduced occupancy is labelled as such, and no per-item or ranking claim may cite it (E10, F3).

### F5 🟠 Dumping all ranks, or serializing a whole trace

**Pattern.** Rendering a whole multi-rank capture to a text format buffers hundreds of gigabytes in RAM
with zero output for a long time before anyone notices.

**Rule.**
- **Pin ONE rank.** Stage a minimal capture (trace metadata + graph artifacts + one rank's per-graph trace),
  ingest to a columnar store, and query that. All ranks are equivalent for per-op analysis; escalate to all
  ranks only for genuinely cross-rank questions (per-rank skew, straggler identity).
- **Query specific fields**, never a whole-trace serialization.
- **Bound the analysis job** (explicit timeout, scratch output path so a running viewer is not clobbered)
  and confirm the output file is growing within about a minute. "No output plus rising RSS" means the wrong
  method — kill it early.

---

## Part G — Does the conclusion follow from the measurement?

The measurements in this part are real and correctly taken. What fails is the inference: a ratio with no
denominator, a component derived by subtraction, a formula used outside its domain, a bucket nobody owns,
or a mechanism asserted because it felt obvious.

### G1 🔴 Calling a large `measured / floor` gap a ceiling

**Pattern.** Hardware floors are computed, the measurement sits several-fold *above* them, and the report
still says "target not reachable — hardware floor," complete with a required-efficiency figure and a
ceiling curve extrapolated from those measurements. This inverts the purpose of a roofline. A roofline
answers *"how much headroom is there, and where?"*; its output is a **direction to search**. A large
`measured/floor` ratio is by construction evidence that the **deployment** is the binding constraint — the
machine is largely idle relative to its own capability. Calling that a ceiling is self-contradictory. The
failure is seductive because the floor arithmetic is genuinely correct and the constants are properly
cited; only the inference is backwards.

**Rule.**
- Always compute `measured / floor` per phase at the same shape, and **branch** on it:
  - **Large gap** ⇒ the finding is "this deployment is N× off entitlement"; the deliverable is localizing
    that gap. The words *ceiling*, *unreachable* and *hardware floor* are **unavailable**, however
    well-sourced the constants.
  - **Near 1× and still short** ⇒ an infeasibility claim is licensed; state it as a ceiling plus the
    required sustained utilization.
- Never extrapolate a curve (over workload ratio, batch or config) from a point far off its floor and
  present the curve as a hardware property.
- Put `measured/floor` in the **summary line** of every performance report. If the gap is large and the
  report contains the word "ceiling," the report is wrong.
- Express any infeasibility answer as a **ceiling plus a required efficiency**, never as a list of configs
  tried. If the ceiling sits above target, the verdict is "**not yet**" and the deliverable is a ranked
  lever list, not a rejection.

### G2 🔴 Comparing a measured step against an incomplete floor

**Pattern.** A measured step is compared against a single-resource floor — typically weight-read only — and
the ratio is reported as the optimization opportunity. A step pays for every resource it touches; omitting
terms inflates the apparent headroom, and the omitted term is frequently the *largest*. Communication in
particular is often the biggest component of a decode floor, larger than either weight or cache read, and
is the term most often left out because it is assumed to be hidden.

| Floor definition | Apparent gap | Conclusion it invites |
|---|---|---|
| weight read only | large | "huge headroom, go optimize" |
| weight + KV read + compute + collectives | markedly smaller | "modest headroom; mostly irreducible" |

**Rule.**
- A floor must include weight read, activation/KV read, compute **and** communication. For a standard TP
  decoder, count `collectives_per_step ≈ 2 × L` explicitly; do not assume they are hidden.
- Compare at the **same batch and shape** as the measurement; comparing across batches conflates two
  effects.
- Quote the gap with its floor definition inline ("N× above a weight+KV+compute+collective floor"). "N× off
  floor" is meaningless without it, and an incomplete floor always flatters the opportunity.

### G3 🔴 Collapsing achieved, entitled and required into one comparison

**Pattern.** Measurement sits far below the target while a separately computed hardware floor sits close to
it. The correct reading is that most of the distance is implementation — unattributed overhead, sub-peak
execution, exposed serialization — and therefore addressable. Instead the two comparisons are collapsed:
because *entitlement* is close to the target, the shortfall is described as approaching a hardware limit
and the target as effectively unreachable. The strongest available evidence for continuing — a large
measured distance between achieved and entitled — is reframed as evidence for stopping.

**Rule.**
- **Hold three numbers apart at all times: achieved, entitled, required.** Achieved-vs-entitled is
  remaining engineering headroom; only entitled-vs-required can speak to hardware feasibility.
- **A large achieved-vs-entitled gap is a mandate to continue,** whatever entitled-vs-required says.
- **Never call a limit physical while a named implementation gap is open.** State it as "not yet achieved,
  with N identified unattacked terms."
- Derive the entitled-vs-required comparison only from independently confirmed platform constants, and
  state explicitly that it says nothing about whether the present implementation can improve.

### G4 🔴 Asserting a mechanism without a profile, while claiming the search exemption

**Pattern.** A config sweep shows a throughput ceiling below target, and the operator reasons: "the
bottleneck is weight bandwidth because the model is large" or "collective overhead dominates at the higher
degree because tail latency scales with it." These are *mechanism assertions* — claims about *why* — and
they steer the next decision ("must use a narrower dtype to halve weight reads," "must reduce the degree to
cut collectives"). No device profile exists on disk to support either.

The exemption for pure search is real: "degree A gives X tok/s vs degree B gives Y tok/s" is an observation
needing no profile. But "degree B is collective-dominated, therefore go to degree A" is a mechanism that
does. The failure is subtle because the mechanism feels obvious from first principles — and "obvious"
mechanisms are exactly the ones that survive longest without verification and are wrong often enough to
matter (a "bandwidth-bound" decode that is actually serialization-bound due to scheduling gaps between
engines). The cost: the entire search is steered by an unverified mechanism, compiling configs that chase
the wrong axis.

**Rule.**
- Any sentence of the form "X is the bottleneck because Y" or "must do Z to fix the bottleneck" requires a
  **profile artifact path in the same paragraph**. If you cannot cite one, rewrite it as a hypothesis
  ("hypothesis: X may bind; will verify via profile") and do NOT let it steer compile decisions.
- Capture the profile on the FIRST successfully-warm server, BEFORE the sweep (D1). The sweep then has a
  verified mechanism to guide it.
- When a winning config exists, profile it BEFORE reporting conclusions about why it won. "This config hit
  the SLA" is a valid observation; "it hit the SLA because the narrower dtype halved weight-read time" is a
  mechanism needing a profile that shows the weight read was binding before and halved after.
- **The tell:** a sentence containing "because," "dominated by," or "bound by" with no artifact path
  anywhere nearby.
- **The rule admits no exemption for cheap signals.** The engine's free per-phase metrics, the scheduler
  source, and a capacity line in a startup log are all necessary and all admissible as **observations** —
  they tell you which phase occupies the device, whether a queue is standing, whether a cap binds. None of
  them licenses a *because*. A phase-exclusivity read plus a phase-separated rate identifies the term to
  test; the claim that it binds still carries a profile citation before it steers a compile (C5, E11).

### G5 🔴 Reading a plateau across configurations as a hardware wall

**Pattern.** The same throughput appears across configurations that vary the resource believed to bind, and
the shape of the plateau is treated as the conclusion.

**Rule.** **Treat a plateau across configs as a diagnostic, not a conclusion.** If the same number appears
across configurations that vary the resource you believe binds, something *config-invariant* binds — and
shared code is a far better hypothesis than shared silicon, because the sweep varied the silicon and not
the kernel. Note also that throughput versus batch is **not** rising-then-flat: past a capacity or
compiled-bucket boundary it collapses, so a "plateau" may be the far side of a cliff (H1).

### G6 🔴 Deriving a component by subtraction, and porting a calibration across regimes

**Pattern (subtraction).** A collective-time figure is produced by subtracting a modeled compute floor from
a measured total (`measured − floor = comm`) and reported as if measured. No collective event in the trace
corroborated it, and the conclusion built on it had to be dropped.

**Pattern (regime).** An analytic model is found to **overestimate** one regime's cost and that single
calibration is generalized into "the model is pessimistic" — while in the other regime it
**underestimated**, and the optimistic direction was the one the verdict rested on.

**Rule.**
- **A subtraction is not a measurement.** Deriving a component (collective, idle, host overhead) as
  `total − model` inherits the model's full error and cannot be falsified. Query the component's own rows
  directly, or drop the claim and label the term unverified.
- **Calibrate per regime and never port a correction factor across regimes.** Compute-bound prefill and
  memory/collective-bound decode fail in different directions. Until each regime is measured, treat its
  analytic aggregate as an **upper bound** — especially the one your verdict depends on.
- **State which side of the target the uncertainty falls on.** An analytic figure that clears the bar by
  less than the model's demonstrated per-regime error does not clear the bar.

### G7 🔴 Using an analytic form outside its validity domain — and labelling it "indicative"

**Pattern.** A closed-form relation between latency and utilization is adopted to order candidate operating
points. Its assumptions — single server, one-request-at-a-time service, a particular arrival process — do
not describe a system that admits work in batches, interleaves partial work from many requests in one pass,
and is governed by an admission policy. The mismatch is recognized and the output carefully labelled
indicative and ordering-only. It then supplies the campaign's central conclusion anyway: that two
objectives are mutually exclusive.

**Rule.**
- Check the form's assumptions against the system's actual mechanism before adopting it, and **name each
  assumption the system violates.**
- **A disclaimer does not bound downstream use.** A label is a note attached to a number, not a constraint
  on how the number is later used. If a quantity must not carry a decision, keep it out of the place where
  the decision is made.
- Where batching, admission control or work interleaving violate a closed form, the honest instrument is a
  **sweep of the real control**, not a more elaborate analytic form (C15).
- **A quantity that may only order candidates must never yield a verdict.** Catching yourself concluding
  from it is the signal it has escaped its domain.

### G8 🔴 Treating a summed best case over unimplemented levers as a bound

**Pattern.** Several levers are identified but left unimplemented. Their combined effect is estimated —
each assumed to land fully and simultaneously — and the resulting figure compared against the target to
conclude the target is out of reach. Presented as arithmetic over measured inputs, the projection is a
chain of assumptions about work nobody did: that each lever's benefit is independent, that none exposes a
further lever, that efficiency measured in one regime carries into another, that no unmeasured interaction
exists. A projection assembled while *justifying a stop* has been promoted to an upper bound.

**Rule.**
- **A projection may motivate an experiment; it may never replace one** — least of all when it points
  toward stopping.
- **Never treat a summed best case over multiple unimplemented levers as a bound.** Independence is an
  assumption, and landing one lever routinely reveals others invisible beforehand.
- **When a projection argues for stopping, implement its cheapest term and compare.** The gap between
  projected and measured is the only calibration available for the rest of the projection.
- **Carry a projection's assumption list wherever the number goes.** Quoted downstream, a projection loses
  its qualifiers before it loses its digits.

### G9 🟠 Attributing a cost without naming its execution layer

**Pattern.** A large per-step cost is attributed to host-side work, a specific function is named as the
culprit, and a framework code change is proposed — without establishing that the named function runs on the
host at all. In one instance the named symbol was a device kernel invoked through an ordinary function
call, so the attribution could not have been right and the remediation plan addressed the wrong layer.

**Mechanism.** Host framework code and device kernels live in the same source tree and are invoked through
indistinguishable call syntax; a name carries no information about where it executes. Meanwhile the
*symptom* — a per-step cost far above the device floor — is equally consistent with host overhead, dispatch
overhead, and a compiled graph doing more work than the current inputs require. Choosing among them by
plausibility sends the fix to the wrong layer and produces an effort estimate unrelated to the defect.

**Rule.**
- Every cost attribution states its **layer** — host / dispatch / device — and cites the evidence for that
  layer, not just for the magnitude.
- Confirm the execution layer of every symbol you name **before** proposing a remedy; the layer determines
  the remedy and the effort class.
- Distinguish the three by signature: host cost scales with per-request framework work; dispatch cost scales
  with call count; compiled-graph cost tracks the **compiled** shape and is flat in the actual one.
- A remediation estimate inherits the attribution's uncertainty. "This requires a framework change" is a
  claim about the layer and must be supported like one.

### G10 🟠 Root-causing an observed value to a constant on a code path that does not execute

**Pattern.** An unexpected number (a memory denominator, a default, a limit) is traced by grep to a
plausible constant, and that constant is reported as the root cause. But the function holding it belongs to
a different path — offline estimation, CPU/simulation, a fallback — that never runs in the observed
configuration, where the value actually comes from a runtime probe. The write-up then proposes a fix to code
that has no effect, and the real denominator stays unexamined.

**Rule.** Before naming a constant as a root cause, prove the path executes in your configuration: a log
line, a deliberate perturbation of the constant, or the enclosing conditional's truth value. Distinguish
estimation-path constants from runtime-probe values explicitly, and when both exist, say which one bound.
**A grep hit is a hypothesis, not an attribution.**

### G11 🟠 Letting an unattributed aggregate bucket argue both sides

**Pattern.** A breakdown accounts for the measured total honestly, but a large share lands in coarse buckets
— non-primary engine time, other-engine time, unattributed idle. The reconciliation is arithmetically
exact, which lends the whole table credibility, and the coarse buckets are then carried unchanged through
every later decision: quoted as headroom when arguing room exists, and quoted as overhead when arguing the
ceiling is near. A quantity with no named owner argued both sides, and because it was never decomposed, no
experiment was designed against it.

**Rule.**
- **A bucket without a named owner is not a finding; it is an open question with a number attached.**
  Decompose it into operators, phases, or launch/tail/wait before it informs anything.
- **An exact reconciliation is a completeness check, not an attribution.** That terms sum to the total says
  nothing about whether any term is understood.
- **Rank unattributed buckets by size and treat the largest as the next investigation,** not as a residual.
- Never let one unattributed quantity supply evidence for opposite conclusions in a single campaign; that is
  the tell that it was never resolved.

### G12 🟠 Overlooking integration adaptations as a bottleneck source

**Pattern.** A model that was ported or adapted carries integration-layer changes — an output shape
adjusted, an operation moved host-side, a dtype coerced — and these are treated as plumbing rather than as
candidate bottlenecks. An end-to-end miss then has no visible cause inside the device window.

**Rule.** Treat integration adaptations as a **first-class bottleneck source**. Ask "what did integration
change relative to the original?" — anything that changes output shape or moves an operation host-side is a
prime suspect for an end-to-end miss invisible in the device window (and see I7 for correctness workarounds
that relocate cost).

---

## Part H — Which levers exist, and which have actually been tested?

### H1 🔴 Treating anything you did not tune as being at an optimum

**Pattern.** One axis is swept on one configuration and the result is reported as the system's capability.
Two traps compound. **Non-monotone axes:** throughput versus batch is not rising-then-flat — past a capacity
or compiled-bucket boundary it **collapses**, so a monotonically rising sample sequence is not evidence of
saturation and your maximum may sit on the far side of a cliff, measuring the collapse rather than the
ceiling. **Accepted defaults are invisible choices:** any tunable you did not set, you chose by default —
including optimizations shipped disabled-by-default precisely because someone measured them as wins.

**Rule.**
- Before benchmarking, **enumerate tunables from the code you are running** (performance and feature gates,
  bucket/alignment/capacity constraints in the config path) and record **every accepted default** as an
  explicit list in the report.
- **Bracket** non-monotone axes on both sides rather than walking them upward, and name which side of any
  cliff each point sits on. An unsearched axis is an unmeasured axis.
- Establishing the optimum is **your** job. "I ran the defaults" is not a measurement of the system's
  capability.
- Tag every throughput number with its quantity — same-run blended / phase-isolated steady-state / engine
  phase-average — plus the `IN:OUT` split. Cross-tag comparison is forbidden without showing the conversion.

### H2 🔴 Declaring a cost irreducible without grepping the stack for a knob that halves it

**Pattern.** A bottleneck is correctly measured and localized (say, per-token collective cost dominating
prefill), declared irreducible, and written into a negative verdict — without ever searching the source tree
for environment variables or configuration flags that address that exact bottleneck. Stacks routinely ship
collective-cost reductions (reduced-precision scatter/gather, DMA path optimizations, fused communication
patterns) behind gates that are disabled by default for compatibility or validation reasons, defined in a
central env module, referenced at the call site of the very operation profiled as slow, and discoverable in
under a minute. The diagnosis is right; the "irreducible" conclusion is wrong because the tree containing
the flag was never searched. The same applies to precision, layout and compression options for the dominant
term, and to sibling models in the same tree already exercising the path.

**Rule.**
- **Before any measurement campaign**, run a broad discovery pass:
  ```
  grep -rI "os.environ\|os.getenv\|getenv" <source_tree> \
    | grep -i "perf\|fast\|opt\|gate\|allgather\|reduce\|collective\|dma\|xpose\|fuse\|fp8"
  ```
  Record every hit and its default state in the report's accepted-defaults list (C6, H1). A feature gate left
  at default is an **untested optimization**, not a baseline.
- After localizing a binding operation, grep for that operation's name in the env/config modules; if a gate
  exists, **enable it and measure** before concluding anything about hardware limits.
- Before writing "irreducible," grep for the precision / dtype / layout / compression knobs that apply to
  **that specific term**, and check whether any sibling model in the same tree already exercises the path —
  an in-tree precedent means the code exists and the risk is bounded.
- When the binding cost is **communication**, the search space is small and well-defined: precision reduction
  for the payload, algorithm selection, fusion/overlap, combined payloads (fewer, larger collectives),
  topology-aware placement, and reducing the collective count by lowering the shard degree.
- **"Unsupported" must be an observed error from an attempted launch, never an assumption.** Route quality
  re-validation through the project's existing accuracy harnesses rather than treating a precision change as
  free (H8).

### H3 🔴 Disproving one proposed fix and declaring the problem unsolvable

**Pattern.** A hypothesis about the root cause arrives with the task ("F is fixed overhead, packing amortizes
it"). It is correctly disproven by measurement. The effort then declares the problem unsolvable — skipping
the step of searching for *other* solutions to the *correctly identified* binding constraint. Disproving one
proposed solution is not proving no solution exists.

**Rule.**
- When you identify the true binding constraint, treat it as a **search target, not a conclusion**: "what in
  this codebase reduces the cost of the binding thing?"
- Disproving a proposed solution **refines** the search space; it does not close the problem.
- The sequence is: measure → localize → search for mitigations → test mitigations → THEN conclude. Never skip
  from localize to conclude.
- **Finish the investigation.** "Found a smaller or different number, flagged the uncertainty" is a sign to
  keep going with the right tool, not a stopping point — resolve it, or explicitly exhaust the tooling and say
  so (K4, E11).

### H4 🟠 Recording an untested lever as a dead end

**Pattern.** A lever is skipped for a defensible reason ("the device is already nearly busy, so hiding host
latency cannot help much") and then written into the dead-end list as though it had been tested. The harm
compounds precisely because dead-end lists are, by design, **not re-tested by later runs** — a prioritization
judgement silently becomes a permanent exclusion nobody will revisit.

**Rule.** Keep three labels distinct and never collapse them: `tested → null result (with the artifact)` ·
`not tested → deprioritized (with the rationale)` · `dead end → evidence of why it cannot work`. Only the
third should be inherited by future runs. When deprioritizing, state the rationale as a **prediction**, so a
later run can cheaply falsify it. Never substitute an artifact from a different workload or tree state for a
missing sweep point — label it **unmeasured**.

### H5 🔴 Retracting a conclusion without re-opening the levers it pruned

**Pattern.** A finding is correctly retracted as confounded or wrong — but by the time it is retracted it has
already done its damage: it *removed candidates from the search space*, and the retraction restores the
finding's status without restoring the candidates. An early iteration concludes "larger batch width is a dead
end" and "the narrower weight format buys no throughput," both from a confounded comparison; both are later
retracted, and the search space they closed is never re-opened, so the next iteration still reasons as though
batch were exhausted and carries that assumption into its own final verdict.

**Rule.**
- Every retraction must name **what the retracted claim excluded**, and those exclusions go back on the
  candidate list explicitly. A retraction that only edits the report is half a retraction.
- Keep the dead-end list **dated and sourced to the specific measurement** that established each entry, so an
  invalidated measurement can be traced mechanically to every exclusion it justified.
- Prefer to *defer* a pruning decision over recording a premature one. A lever marked "not yet tested" costs
  one measurement later; a lever wrongly marked "dead end" can cost the whole effort, because H4's inheritance
  rule means nobody will revisit it.

### H6 🟠 Calling a tradeoff structural before sweeping the mechanism that arbitrates it

**Pattern.** Two objectives appear to trade off across a parallelism/replication axis, and the tradeoff is
declared structural on that basis. The component that actually arbitrates between them at run time — the
admission and batching policy deciding, at each step, how much new work to accept versus how much in-flight
work to advance — is never varied, and in one configuration its setting is never even recorded. A conclusion
about an inherent conflict was drawn from a sweep that held the arbiter pinned at a default.

**Rule.**
- Identify what arbitrates the two objectives at run time and **sweep it before calling any tradeoff
  inherent** — typically an admission/batching/scheduling policy and its size parameters.
- **Record the arbiter's setting on every point of a frontier.** An unrecorded policy makes the frontier
  uninterpretable and unreproducible.
- A frontier over a structural axis with the arbiter fixed measures that axis at one policy, not the system's
  achievable frontier.
- "Inherent conflict" is a claim of the same cost class as "blocked" (B7): verify it personally, and expect the
  first sweep of the arbiter to move it.

### H7 🟠 Ranking levers from magnitudes computed over a broader scope than the target phase

**Pattern.** A ranked list of work orders is built from magnitudes computed over a broader scope than the phase
being optimized, and implementation effort is directed at the top-ranked item — which did not exist in the
target phase at all. An attribution's **scope silently sets its magnitudes**: work belonging to other phases
inflates classes that may be absent from the target phase, so a scope error does not merely perturb the
numbers, it **inverts the ranking**. The damage is a correctly-executed implementation effort spent on a class
that is not present, which then returns "blocked" or "void" and is easily misread as the optimization being
unavailable. Every count in the table is real; only the label on the table is wrong.

**Rule.**
- Every magnitude in a ranked lever list **carries the scope it was computed over**, in the table, next to the
  number.
- **Re-derive the ranking at the operative configuration** before implementing. A ranking produced at a
  different parallelism degree, batch or phase mix is a hypothesis about the operative one.
- A lever whose magnitude has not been confirmed in-scope is **not ranked** — list it as unquantified.
- When an implementation step reports a top-ranked lever as absent or void, treat that as evidence about the
  **attribution** and re-scope before concluding anything about the optimization's availability.

### H8 🔴 Reporting a lossy optimization's win without its fidelity cost and the faithful ceiling

**Pattern.** The lever carrying the throughput headline is a reduced-precision cache dtype that is **not
output-faithful** — under greedy decoding, probe prompts diverge from the baseline. The headline quotes the
fast number; the fidelity cost and the **output-faithful ceiling** (which missed the bar) are relegated to a
subsection. A reader taking the headline adopts a lossy configuration without knowing it changed model
outputs.

**Rule.**
- **Report the faithful ceiling and the lossy figure side by side, in the headline** — never the lossy number
  alone. State the bar's verdict under each.
- **Probe fidelity on content that can actually diverge.** Short factual completions are insensitive and will
  look byte-identical; open-ended continuations are where reduced precision shows. A handful of prompts is a
  smoke test, not an accuracy evaluation — label it as such and name the real evaluation still owed.
- **Fidelity is a property of the configuration, not a caveat on the number.** If the SLA is silent on
  fidelity, resolve it with the requester rather than choosing the flattering reading (A1).
- Never let a lossy lever's gain be confounded with other changes (E2).

### H9 🔴 Concluding "never reaches T" from evidence at the axis's unfavourable end

**Pattern.** A whole-axis claim is made from a single point taken at the end that *disfavours* the metric, and
the remaining points are filled with an artifact from a different workload or tree state. Latency improves
with more devices per replica; throughput improves with more replicas. A single point at the unfavourable end
says nothing about the axis, and measuring one step toward the favourable end can reverse the verdict
outright.

**Rule.**
- Before any "never reaches" claim, locate your evidence on the axis. If it sits at the unfavourable end, the
  claim is unsupported.
- Measure at least the point the metric *prefers*, plus one interior point to establish the trend (C11).
- Enumerate the *valid* points first; divisibility and capacity constraints often make the apparent extreme
  unavailable.

### H10 🔴 Concluding a capability is unavailable from the rejection of one of several mechanisms that provide it

**Pattern.** One capability (a reduced-precision execution mode, a fused path, an alternate scheduler) can be
reachable through more than one mechanism: a **generic framework plugin** that auto-detects the feature from
the model's declared metadata and validates its *name* against a platform support-list, and a separate
**model-native path** implemented inside the model code and switched on by its own activation flag. An attempt
enables the feature the generic way; the framework's validator rejects the *name* ("X is not supported on this
platform"); this single observed error is written up as "the capability is unavailable here," a fallback
regime is adopted, and — worse — the fallback is narrated as a settled *structural fact*. In reality the native
path is live and its intended invocation is different: it requires **clearing** the generic metadata marker
(precisely so the generic validator stops rejecting it) and then setting the native flag the model code
consumes. The rejection read as a wall was a **required precondition** of the correct invocation. The tell is
usually already in hand — the native mechanism's own source or docstring prescribes the two-part invocation,
including a note that the generic marker must be emptied so the validator does not reject it, yet only the
generic half was attempted before concluding.

**Rule.**
- An observed "unsupported" error is evidence **only about the exact invocation you ran**, never about the
  capability in general. Refines H2: unsupported must be *observed* — and observed for the *intended*
  invocation, not the first one that came to hand.
- Before concluding a capability is unreachable, **enumerate the distinct mechanisms** that could provide it
  (generic auto-detected plugin vs model-native execution path) and attempt the intended one. Two mechanisms
  that share a feature *name* are not the same feature; "the generic layer rejected the name" and "the
  capability is unavailable" are different claims, and the second requires the native path to also have failed.
- When a mechanism's own documentation or source prescribes an invocation that **anticipates** the error you
  just hit (e.g. "empty the marker so the validator does not reject it"), treat that error as an expected
  checkpoint of a different path, not as a verdict.
- Read all of a feature's enablement layers **in execution order** — the outer name-validator and the inner
  native activation — and cite the enforcing symbol for each before pruning (C9).
- **Never narrate a fallback regime as a "structural fact" until the preferred invocation has been attempted
  and its on-device error captured.** A regime asserted before the preferred path was run is a hypothesis
  wearing the costume of a finding (C8, H3, K1).

---

## Part I — Correctness and kernel-interface mistakes

These sit at the boundary between the framework and the kernels it calls. Each produces either wrong output
or a silent performance regression, and each is cheap to prevent and expensive to trace.

### I1 🔴 Architecting around "overlap" before verifying the overlap is achievable

**Pattern.** Fusing several layers into one large kernel with on-chip-resident intermediates is assumed to let
a collective run concurrently with the next matmul, and a multi-week implementation is built on that premise.

**Mechanism.** Overlap requires the *runtime* to pipeline communication against compute, and two constraints
commonly forbid it independently of kernel quality: the collective API may only support efficient
multi-tensor communication on **main-memory-resident** tensors, not on-chip-buffer-resident ones, forcing an
in-kernel collective onto a slower algorithm; and keeping intermediates on-chip can *add* main-memory
round-trips elsewhere (residual storage), increasing traffic and compute. The outcome of this class of design
is not a small loss — an in-kernel-collective fusion can measure **slower than the naive per-layer baseline**
while achieving ~0% overlap.

**Rule.** Before committing to an overlap-based architecture: (1) confirm from API documentation or source
that overlap is *expressible* from where you intend it; (2) confirm the memory-residency requirement of the
efficient collective algorithm; (3) run a two-to-four-layer profiling experiment that validates the premise in
under an hour — compute the intersection of compute-engine-active time and collective-active time, and if
that intersection is ≈0 no overlap exists; (4) treat "the collectives will hide behind compute" as a
hypothesis requiring measurement, never as a design assumption.

### I2 🟠 Rewriting the newest, most complex component before establishing that it is wrong

**Pattern.** When a quantized matmul path emits garbage, the most suspicious-looking new component (a
dtype-specific transpose, unusual interleaving, custom tiling) is rewritten before it is established as the
cause. Attention gravitates to novelty, but multi-stage numeric pipelines
(`quantize → transpose → matmul → dequantize`) fail far more often on **mundane scale/offset arithmetic** — a
missing division by a dequantization scale, a scale applied on the wrong side of a reduction, a stored
*dequant* scale used as if it were a *quant* scale. These produce systematically wrong magnitudes that look
like corruption.

**Rule.** Isolate every stage with a standalone on-device test **before** choosing a suspect; primitives
usually prove bit-correct while the arithmetic around them does not. Check scale direction and placement
first: which value is quant vs dequant, and is it applied before or after the reduction? Do not begin a
rewrite until the stage is confirmed as the cause. Keep a token-identity check against a trusted baseline as
the tripwire — inverted scales surface as degenerate output immediately.

### I3 🟠 Returning the original tensor after mutating a contiguous copy

**Pattern.** `.contiguous()` copies only when the input is non-contiguous. For contiguous inputs the code
appears to work and tests pass; for non-contiguous inputs — common for shards loaded with a transposed
storage layout — the mutation lands on a temporary and is silently discarded. The consequence surfaces far
downstream (out-of-range quantized codes decoding to NaN, sporadic garbage tokens), where it is very expensive
to trace.

```python
# WRONG
def clamp_bytes(tensor):
    t = tensor.contiguous()      # copies if non-contiguous
    t.view(torch.uint8)[mask] = clamped_value
    return tensor                # ← original returned; mutation lost

# RIGHT
def clamp_bytes(tensor):
    t = tensor.contiguous()
    t.view(torch.uint8)[mask] = clamped_value
    return t                     # ← return the tensor you modified
```

**Rule.** Always return and use the tensor you mutated. Unit-test every in-place transform with a
**non-contiguous** input and assert the output differs from the input. Any function combining `.contiguous()`
with in-place mutation is a review checkpoint.

### I4 🟠 Expecting a compiler fusion pass to see inside custom-kernel calls

**Pattern.** A compiler's collective/matmul fusion pass is expected to overlap communication with projection
matmuls when those projections are implemented as custom-kernel invocations. Such passes pattern-match on
framework-emitted matmul ops in the compiler IR; a custom kernel appears as an opaque call, and the compiler
cannot fuse a collective with a matmul it cannot see. This is a structural limitation, not a tuning gap — no
flag enables it.

**Rule.** Before pursuing any compiler-pass-based optimization, confirm the target operations are expressed in
the form the pass matches: inspect the IR and count matmul-class ops versus opaque kernel calls along the path
you intend to fuse. Record the IR evidence. This applies to every "the compiler should handle it" assumption.

### I5 🟠 Rescaling all quantized weights to adapt between dtype variants

**Pattern.** All quantized weights are rescaled by a constant to map one dtype variant's dynamic range onto
another, with the per-tensor scale adjusted to compensate. But variants typically differ only at the extremes
of the code space: the overwhelming majority of codes lie in the shared range and decode identically. A global
rescale therefore injects quantization noise into nearly all values to fix a tiny minority, and mutating the
per-tensor scale breaks any downstream assumption of scale sharing.

**Rule.** Quantify the affected population **before** choosing a transform — scan raw bytes and count how many
codes actually fall outside the target variant's representable range. If it is a small minority, point-clamp
only those codes (saturate the magnitude field, preserve sign) and leave the scale untouched. Assert
bit-identity for unaffected codes so the fix provably adds no noise. Generalize: prefer a targeted fix over a
global transform whenever the defect is sparse.

### I6 🔴 Accepting simulator agreement as evidence that a quantized path is numerically correct

**Pattern.** A simulator's reference path can share the *same* systematic scale offset as the code under test —
for example when a harness's pre-quantization is neutralized by normalization in simulation mode. Both sides
are wrong by the same factor, so they agree. Hardware then exposes the error as dead or garbage output.

**Rule.** The primary correctness gate for quantization arithmetic is **on-device output agreement** against a
trusted-precision baseline (first-token or greedy-sequence identity). If a simulator is used, inject **real**
checkpoint scales so an omitted scale changes the result. Read "simulator passes, device fails" as a strong
signal that the simulator's reference carries the same systematic factor as the bug — not as a hardware
anomaly.

### I7 🟠 Closing a correctness workaround without measuring its performance characteristics

**Pattern.** A correctness workaround is landed (pre-converting an activation dtype in the framework so a
kernel's fused transpose becomes dtype-legal) and the issue is treated as closed because output is now correct.
Such workarounds routinely activate a *different, slower* code path inside the kernel — a narrower-dtype
transpose with reduced throughput per instruction — so the correctness problem is solved while a new
performance problem is introduced silently, and it can become the single largest addressable inefficiency in
the phase.

**Rule.** Measure and record the performance delta of every dtype/layout correctness workaround **at the
production shape**, not just its correctness. Document which slower path the workaround activates so the cost
is attributable later. File the alternative (a transpose on a different engine that avoids the constraint) as
known follow-up work rather than losing it.

### I8 🟠 Chasing a simultaneous all-rank crash with concurrency and timeout knobs

**Pattern.** A multi-rank launch crashes, and the response is to tune parallel-trace/concurrency flags,
raise timeouts, or reduce load — repeatedly — on the theory that it is a race or a resource-timeout.
The crash persists identically across all of them.

**Mechanism.** When **every rank crashes simultaneously at the same graph-construction/execution point**,
the fault is deterministic — an unsupported op, shape, or lowering in a specific graph variant — not a
race (which would be rank- and timing-dependent) and not a watchdog timeout (which would move with the
timeout). Concurrency knobs cannot fix a deterministic lowering defect; they only change how the same
crash is reached.

**Rule.**
- Read the crash's simultaneity and location first. All-rank, same-point, timing-independent ⇒ debug the
  **graph/shape** (bisect the offending op, the segment/bucket variant, the code path), not the
  concurrency or timeout configuration.
- Confirm by toggling the concurrency knob the theory blames: if the crash is identical with it on and
  off, that knob is not the cause — stop tuning it.

### I9 🟡 Skipping warmup to save compile time, and breaking the run through a recompile guard

**Pattern.** To shorten a compile/iterate loop, a warmup or a shape's graph is skipped. The run then
fails at execution with a recompilation/guard error, or silently recompiles and invalidates the timing —
because a downstream guard expected the skipped shape's graph to exist.

**Mechanism.** Shape-specialized runtimes install guards (e.g. on argument counts/shapes) that fire when
an un-warmed shape is first seen, triggering a recompile or an error. Warmup is not just latency-hiding;
it is the step that **populates the graphs the guards require**. Skipping it removes a load-bearing
precondition.

**Rule.**
- Treat warmup shape coverage as part of the run's correctness contract, not an optional speedup. If
  warmup must be trimmed to iterate, trim it for the shape under test only, and verify no guard fires
  (grep the log for recompile/guard vocabulary) before trusting any measurement from that run.

---

### I10 🔴 A data-dependent guard or lazy buffer initialization inside the compiled forward path forces a recompile that crashes every rank

**Pattern.** A per-module buffer (a quantization/scale tensor, a cache) is registered as an empty/`None`
placeholder and created lazily on first use inside the compiled forward path
(`if self.buf is None: self.buf = …`). The kernel math is correct in isolation, but bringing the model up
under a recompile-forbidding compile stance crashes **all** ranks at warmup with a
"detected recompile … guard: buf is None" error, and the server never serves.

**Mechanism.** Under an AOT / fail-on-recompile stance, the compiled graph installs guards on the
data-dependent branch; the first execution flips the `is None` guard, which requires a recompile the
stance forbids, so every rank aborts together at the same point. The defect is in the **framework wiring**
(a lazy branch inside a compiled region), not in the kernel, and it is invisible to an isolated kernel
correctness test. This is a distinct recompile cause from a skipped-warmup shape guard and from a
nondeterministic race — it is deterministic, all-rank, and same-point.

**Rule.**
- Initialize such buffers **eagerly at construction** (e.g. an identity scale of ones for weight-only
  quantization, matching the adjacent already-eager buffers), remove the lazy `is None` branch from the
  compiled forward path, and add the buffer to the weight/tensor materialization loop so it is populated
  before execution.
- Treat any data-dependent control flow inside a compiled region as a recompile hazard under a
  fail-on-recompile stance; an all-rank, same-point warmup crash citing a `… is None` guard is the tell —
  debug the guarded branch in the graph, not the concurrency/timeout knobs.

---

### I8 🔴 Mutating module state inside a compiled forward violates a no-recompile stance

**Pattern.** A lazy initialization inside the compiled/traced forward — `if self._x is None: self._x =
<compute>` — flips a guard between the first and second call; under a fail-on-recompile compilation
stance this raises a recompile error at runtime (not at load), long after the change looked safe in
eager mode.

**Rule.**
- Materialize per-module constants (pre-transposed weights, derived buffers) ONCE at load time, before
  the first traced forward; never introduce data-dependent state mutation inside a compiled forward.
- If a value can only be produced at forward time, produce it every call (no cached-`None` branch) or
  move its production to a load/warmup hook.

### I9 🟠 A custom framework/device parameter may reject strided or transpose-then-contiguous ops at load

**Pattern.** Deriving a transformed weight at load — e.g. a transposed, contiguous copy of a projection
weight — by calling transpose/contiguous/strided-copy directly on the framework's weight parameter
raises a device or contiguity error, even though the identical op succeeds on the same tensor at forward
time.

**Rule.**
- To derive a transformed weight at load, first copy the CONTIGUOUS, non-transposed source into a fresh
  plain tensor (or move it host-side), THEN transform; do not assume standard tensor semantics on a
  framework/device parameter's storage.
- "Works at forward, fails at load" points at the parameter's storage type / lifecycle, not at your
  math.

## Part J — Reporting, ledgers, and audits

An audit apparatus that checks only what was *said* cannot see what never happened, and one that checks only
provenance cannot see a valid inference drawn wrongly. Both blind spots have terminated efforts with everyone's
confidence intact.

### J1 🟠 No incremental results ledger, so an interrupted session loses its synthesis

**Pattern.** Results accumulate as raw per-run output files while the interpretation lives only in working
context, with the report deferred to the end. The session is interrupted and leaves a pile of valid
measurements nobody has reconciled — the raw numbers survive, the *conclusions* do not, and reconstructing them
costs a second pass.

**Rule.** Append to a durable ledger **after each measurement**: the config, the metric values, the artifact
path, the `measured/floor` ratio (C1), and the one-line interpretation. Checkpoint the feasibility verdict and
the running best-known operating point as they change. The ledger, not the context window, is the deliverable's
source of truth.

### J2 🔴 Correcting the narrative and leaving the machine-readable state asserting the withdrawn claim

**Pattern.** After a narrative verdict is honestly rewritten from MET to MISS, the workflow's machine-readable
state still asserts the withdrawn claims: a report stage whose findings read "SLA MET," an implement stage
claiming the sweep met both points, a sweep stage quoting unlabelled extrapolated aggregates, and a feasibility
record still printing *feasible* because it was recorded against an optimistic analytic floor rather than the
measured value. Automated consumers and any future session reading that state would re-inherit the falsified
verdict — the corrected prose was the *less* authoritative surface.

**Rule.**
- **A withdrawn claim must be withdrawn everywhere in one pass:** narrative, tracker rows, deep dives, and
  machine-readable run state. Enumerate the surfaces first, then correct them together; a partial retraction is
  a contradiction with a timestamp.
- **Re-record machine state through its own CLI, never by hand-editing the state file.** Hand editing falsifies
  the evidence trail and skips the validation the tool performs (that every cited artifact still exists).
- **A feasibility/verdict record must be evaluated against the MEASURED value once measurement exists.** A
  record still comparing an analytic floor to the bar will keep printing "feasible" forever. A ledger that
  cannot express a miss is not a ledger.
- **Corrections are append-only.** Add a correction row keeping the falsified claim visible next to what
  replaced it, with the reason and the artifact. Silently rewriting history removes the evidence that the
  process catches its own errors.
- **Grep the machine state for the withdrawn phrasing as the last step.** The old verdict string surviving
  anywhere is a mechanical, one-command check.

### J3 🟠 Auditing the ledger in only one direction

**Pattern.** Ledger review looks for claims recorded without support and never for work done without being
recorded (B3).

**Rule.** Audit both directions, in one pass, and check the sequence state whenever a stage's output is
produced.

### J4 🟡 Overwriting log artifacts when iterating on configurations

**Pattern.** Multiple attempts are logged to the same filename because the launch script uses a fixed output
path. When the first attempt fails and the script is modified, the original failure log is overwritten. The
report then cites error messages from a log that no longer exists.

**Rule.** Use timestamped or attempt-numbered log filenames. If a log WILL be overwritten, copy it before
modifying the launch — the evidence of a failure is as important as the evidence of success. Reference specific
filenames in the ledger; if the file no longer contains the cited content, the citation is dead and the finding
is unsupported. **An artifact mtime is the cheapest provenance check available**: "verified this session" is
falsifiable in one `stat`, and a prior-session artifact quoted as fresh is a common quiet error.

### J5 🔴 Verifying every claim and never verifying coverage

**Pattern.** A thorough verification apparatus is built — metric-field checks, artifact existence, one-variable
A/B hygiene, an independent auditor — and every one of those checks is *claim-shaped*. Claims leave artifacts to
inspect; **omissions leave nothing**, so a silently skipped workflow step is the one failure mode the entire
apparatus cannot see. A required entitlement/floor step was skipped in two consecutive iterations, through two
independent audits that both passed, because each asked "is what was said supported?" and neither asked "did
everything that was supposed to happen, happen?"

**Rule.**
- Give the auditor a **coverage checklist keyed to the numbered plan, not to the output**: "for each step, cite
  the artifact that proves it ran; a step with no artifact is NOT RUN." Keep it as a separate table from the
  claim-verification table, so a passing claim audit cannot mask a missing step.
- Scope the audit prompt beyond the defects you already noticed. An audit that enumerates known issues is
  structurally incapable of finding unknown ones, and the most expensive misses live in the unenumerated space.
- **A conditional commitment needs an enforcement hook.** Writing "this estimate is a hypothesis, to be replaced
  by the real measurement" and never checking that the replacement happened is how a deliberately-labelled
  placeholder becomes load-bearing evidence. Every "to be replaced by X" becomes a tracked open item that
  **blocks the final verdict** until X exists on disk.

### J6 🔴 An audit that certifies provenance and candour but never tests the load-bearing inference

**Pattern.** The review criterion is "is every number traceable to an artifact, and is the verdict honestly
stated?" — and a conclusion can satisfy both while being **wrong**. Every step ran, every artifact exists, every
field name is correct, the miss is reported plainly without spin, and the *inference* drawn from those correct
numbers does not follow. Two independent audits returned PASS on an effort whose central conclusion ("this is a
hardware ceiling") failed a back-of-envelope roofline check neither audit was asked to perform, because the pass
criterion explicitly allowed a not-met verdict "provided the miss is honestly reported and profile-backed" — a
test of candour and provenance, not of validity. Honest, well-evidenced and wrong is the most expensive review
outcome available, because it terminates the effort with everyone's confidence intact.

The related pattern: multiple staged reviewers, artifact checklists and recomputations all pass because every
check asks the same class of question — *do these numbers match the artifacts and each other?* — and none asks
the premise question, answerable from the identical data: *is this operating point anywhere near its floor, and
do independent views of it agree?* Aggravating: an audit that surfaces genuine defects **on an axis that does not
determine the verdict** manufactures confidence in a wrong conclusion.

**Rule.**
- Add a third audit obligation alongside claim-verification and coverage (J5): **name the report's single
  load-bearing inference and test it independently.** Not "is X measured?" but "does Y follow from X?" — and for
  any limit/ceiling/infeasibility conclusion, recompute the floor and the ratio yourself (C1, G1).
- Every audit must attack the premise using the same data: is the point near its floor (G1)? Do independent
  views agree (E11)? Which axes and defaults were never touched (H1)? Finding incidental defects is not evidence
  of having audited well.
- Write the pass criterion so honesty is **necessary but not sufficient**. "Verdict honestly stated" and
  "verdict correctly derived" are separate checkboxes; only the second decides whether the effort should stop.
- Ask the auditor for **the one question the report does not answer.** A review that only re-checks what the
  report chose to assert inherits the report's blind spots by construction.
- **A bound is never stronger than its weakest measured anchor.** Quote a bound with the provenance of its
  measured anchor, not only its constants: correct citations lend a configuration artifact the grammar of
  physics, and phrases like "config-independent, true for every parallelism split" are the tell.
- **Corrective rules must be executable alone,** with no reference number, on a fresh machine. A retrospective
  concluding "I should have found and reproduced the best previously-known configuration" is not a fix: such a
  reference usually does not exist, and when one appears it is suspect (A8). Prevention that depends on an
  external oracle is not prevention.

### J7 🔴 Declaring a claim "unverifiable" instead of independently deriving whether it is achievable

**Pattern.** An audit reviews an optimization claim ("fused this projection, saving N ms") and finds the
*deployed* code is on a different machine. It declares the claim unverifiable and moves on. But the **stock
source** is on the auditor's own disk, showing both the unfused call pattern that would be optimized and the
target function's signature that would accept the fusion. The auditor had everything needed to confirm the
optimization is *structurally achievable* and instead treated "not deployed here" as "cannot be checked." A
valid effort is then questioned on logistical grounds rather than technical ones, and the auditor becomes a
provenance clerk instead of a technical reviewer.

The general form: an audit whose methodology is "for each claim, find the artifact on disk that proves it"
inherits all the blind spots of the original work. If the implementer missed an opportunity, the provenance
auditor misses it too (nothing to verify); if the implementer found one but the code lives elsewhere, the
provenance auditor rejects it (no artifact here). Either way the audit adds zero technical value.

**Rule.**
- Structure an optimization audit as **independent derivation first, comparison second**: read the architecture,
  kernel source and hardware specs available locally; independently identify what is obviously suboptimal and
  what a roofline says is achievable; estimate the gains from first principles; THEN compare against the
  implementer's claims. Agreement is the strongest possible verification — stronger than any artifact check.
  Disagreement is where the real audit value lies.
- Distinguish three audit questions and answer them in order. (1) **Is it structurally achievable?** — the call
  site, the target signature, **and the first gate on the path**, including any deployment-class validator that
  forecloses the feature before its parameters are ever read. A signature-and-call-site read is the *inner* gate:
  finding it feels conclusive and systematically misses the outer one (C9), so stock source alone does not settle
  this question — it settles it only when the gate order has been walked. (2) **Is the claimed gain plausible?** —
  data-flow arithmetic over the removed inefficiency yields a **plausibility bound**, not a certification. Expect
  cost to be *relocated* rather than eliminated, and require confirmation at the production operator, dtype and
  shape, attributed by ablation rather than by inspection (G9, H2). (3) **Is it deployed and measured?** — only
  this one requires the patched code here. When (1) and (2) hold, absent deployed code is a logistics note rather
  than a verification failure; when (1) has not walked the gate order, the claim is **unverified**, not sound.
- **The question is never "is the artifact here?" — it is "does the physics work?"** A technically sound
  optimization on another disk is still sound; a technically unsound one with perfect provenance is still wrong.
- An auditor who cannot independently derive any optimization *candidate* from the available source has performed
  a file-existence check, not an audit. Deriving candidates is the audit's job; certifying them is measurement's.

### J8 🟠 Refuting a claim about one configuration with a measurement at another

**Pattern.** An auditor runs a serving benchmark at one configuration (high shard degree, few replicas, default
settings), gets a low result, and cites it as evidence that a claim of much higher throughput at a **different**
configuration (lower shard degree, more replicas, custom kernels active) is "N× short." The two are
fundamentally different systems — different degrees, replica counts, code paths — and one's result says nothing
about the other's potential. This is E2 applied to audit methodology: using YOUR measurement of configuration A
to reject THEIR claim about configuration B.

**Rule.**
- A benchmark measurement speaks only to the configuration it was run at. It cannot refute a claim about a
  different configuration any more than a car's speed in first gear refutes a claim about fifth.
- To verify a claim at configuration X, **run the benchmark at configuration X.** There is no substitute:
  reasoning about X's achievability from device-level measurements and known overheads produces a
  hypothesis worth testing, not a verification and not a refutation (E12, G8). If the run at X has not
  happened, the correct auditor's finding is "unverified, and here is the run that would settle it" — not
  a pass and not a rejection.
- When reporting your own results alongside someone's device-level claims, state the configuration difference
  explicitly and **do not compute a miss ratio between measurements at different configs.** That is two
  measurements of two different systems, not a gap.

### J9 🔴 Verifying a claim by re-running the same query on the same field

**Pattern.** A verification step confirms a claim by issuing the same query against the same field of the same
artifact and reports it as independently verified — including reproducing the original figure exactly, which is
presented as corroboration. Re-reading a value cannot detect a misinterpretation of **what that value means**:
if the original error was semantic (wrong field, wrong scope, wrong window), an identical second reading
reproduces the error *and* its exact number, and the exactness of the match is then read as unusual strength.
Independence comes from a **different path to the same quantity**, never from a different reader, a different
tool invocation, or a fresh session. This is the mechanism by which a consistency audit (J6) passes while the
conclusion is wrong by a large factor.

**Rule.**
- Every verification step **names the path it used and the path it is checking**, and they must differ. Same
  file, same column, same query is not a check.
- **Exact agreement between a claim and its check is a warning sign, not corroboration** — it indicates a shared
  path. Independent paths agree approximately.
- Where only one path exists, say so and mark the quantity **single-sourced**. A single-sourced quantity may not
  carry a verdict.
- Prefer paths that fail differently: a raw artifact versus a derived summary; a device measurement versus a
  client measurement; a counter versus a timing; a reconstruction from independent quantities.

### J10 🔴 Letting verification depth track agreement instead of consequence

**Pattern.** Two decision-critical claims arrive. The one that contradicts the working hypothesis receives a
full check — source read, artifact inspection, independent recomputation — and is duly corrected. The one that
*confirms* the working hypothesis (reporting a candidate lever as not worth pursuing) is accepted with no
independent check at all, though it removed an entire lever from consideration. The discipline was applied
competently, but its intensity was governed by agreement rather than consequence, which quietly converts a
verification process into a mechanism for accumulating support.

**Rule.**
- **Decide verification depth from consequence, and decide it before seeing which way the claim points.** Write
  down the decision-critical set in advance.
- **A claim that closes a lever deserves at least the scrutiny of one that opens a lever.** Negative findings
  terminate investigation, so their errors are permanent and invisible (H4, H5).
- **Treat your own agreement as a risk signal.** "It agreed with my model" describes an exposure, not a reason.
- **Keep score.** If everything you checked rigorously opposed your hypothesis and everything you waved through
  supported it, the asymmetry is itself the finding.

### J11 🟠 Assuming the review loop fired, and accepting the audit as evidence

**Pattern.** An automated review stage is wired to loop back to the implementer on a trigger token. The auditor
emits exactly that token; the loop **does not fire** and the pipeline reports its stages "finished." The defect
is caught only by reading the output. Separately, the audit itself is a hypothesis — it happens to hold, but is
accepted as the basis for re-planning before spot-checking.

**Rule.**
- **Pair any long optimization run with an independent auditor** that inspects the work product without sharing
  the implementer's context. This is the highest-yield single practice available: it catches mislabeled anchor
  metrics and false blockers that self-review misses.
- **Verify the audit's decision-critical subset yourself** — the findings that gate a go/no-go. An auditor's
  confident finding is evidence-shaped, not evidence.
- **Never assume an orchestration feature fired.** Check the iteration actually happened (stage count, iteration
  index, artifact mtimes) and be ready to re-drive the loop by hand.

### J12 🟠 Leaving a wrong report on disk with confident framing

**Pattern.** A defective conclusion is superseded in conversation but its document stays on disk, framed
confidently. It becomes the next effort's inherited evidence — the A8 trap, self-inflicted.

**Rule.** **Retract in place.** Version the report, keep a `Corrections`/`Retractions` section naming each defect
and its fix, and prefer dropping a conclusion whose evidence cannot be re-derived over restating it with hedges.
Name what the retraction re-opens (H5). Carry the disproven claim forward explicitly when re-tasking (B9).

### J13 🔴 A lessons document that transmits a verdict

**Pattern.** A lessons document mixes portable guardrails with residue from the run that produced them:
incidental identifying details, headline magnitudes phrased as achieved results, and a bar reduced to a
placeholder while ratios against that hidden bar are still published. A reader opens it alongside a new target of
the same *shape* and does what the format invites — assembles the circumstantial details into a campaign
identity, then concludes the lessons came from a run at this same target and that the target had already been
found unachievable. Every step is inference; the document named no model and no bar. Unchallenged, it opens the
work biased toward documenting a miss, on a magnitude possibly imported from a much tighter bar and an unrelated
model.

Two properties caused this, and both are **authoring** defects rather than reader defects. **Identifying residue**
gave the reader something to pattern-match on. **Magnitudes phrased as results** gave that match a payload. Note
also that redaction did not help: hiding the bar removed the reader's ability to *check* the ratio while leaving
the ratio quotable, which is worse than publishing neither.

The adjacent failure: promoting your own corrective heuristics into a shared playbook mid-effort, before they are
validated, mixed into the document's evidence-graded voice.

**Rule.**
- **Write the class, not the case.** An entry states a failure mechanism and how to avoid it. Nothing else earns
  space: not which model, host, config or bar, not what was achieved, not how the run ended.
- **Frame every number as an example, never as a result.** If a figure only makes sense with its regime attached,
  it does not belong in a portable document — drop it and keep the direction.
- **Never publish a ratio against a hidden or placeholder bar.**
- **A lessons doc must not be able to transmit a verdict.** No entry may say or imply that a target is
  unachievable, that a lever will pay, or that an approach is settled.
- **Assume the reader will try to identify the source run, and leave nothing to find.** Absent identifying
  residue the attempt fails harmlessly — cheaper than any warning label.
- Codify a lesson into a shared doc only after it is (a) validated by primary evidence in the current effort,
  (b) labelled by evidence grade, and (c) separated from personal process-corrections, which belong in a lessons
  log like this one.
- **A mis-transfer is a defect here, not in the reader.** When a figure gets lifted as a prior, fix the entry.
- **Distinguish a figure whose arithmetic is the lesson from a figure that reports an outcome.** An identity that
  closes on a padded dimension, a percentile over six samples, a `min()` whose first term binds — these teach the
  mechanism and keep their numbers. Achieved rates, distances from a bar, tuned constant values and per-platform
  resource sizes report outcomes: replace each with a direction plus the **rule** that regenerates the value in
  the reader's own regime. That is C7's sizing-rule discipline applied to the lessons document itself.

### J14 🔴 An audit reachable only through the work's success

**Pattern.** An independent review is wired to run *after* the work stage, so that it inspects a finished
product. The work stage then runs long, stalls, or dies — and the review never executes, in exactly the
cases where it was most needed. A review reachable only through success is not a check on the work; it is a
check on already-successful work, and it says nothing about the failure modes that consume most of a
campaign's elapsed time. The omission is silent by construction: a review that never ran leaves no finding
whose absence could be noticed.

**Rule.**
- **Make the review runnable on partial state.** Its input is whatever is on disk now — including nothing —
  and "no artifact exists yet for step N" is a valid and useful finding, not an error condition.
- **Give the review a trigger independent of the work's outcome:** elapsed time, artifact count, or a
  checkpoint cadence. A dependency on completion is a dependency on success.
- **Require the work to emit a checkpoint at a stated cadence, and treat a missing checkpoint as a
  reviewable event** rather than as silence to wait through — D11's pollable-progress rule applied to
  oversight instead of to a process.
- **Verify the review ran before believing its silence**, and keep "reviewed, nothing found" distinct from
  "not reviewed" in whatever record downstream consumers read. This is J5's coverage discipline applied to
  the audit apparatus itself, which is otherwise the one step no coverage check covers.

---

### J15 🟠 A k=1 beam optimization loop that records only the single top candidate and orders it lowest-risk-first, so its analysis frontier is never systematically worked

**Pattern.** An answer-blind analysis produces several distinct candidate fixes, but the loop
records only one recommendation per iteration and the operator hand-picks it, so the remaining
candidates never enter the loop's tracked state — they survive only in the operator's context and
are silently dropped when the loop finalizes. Ordering compounds it: the safest, smallest candidate
is tried first, deferring the one large-effect lever that is the only one able to clear the accept
bar above the measurement noise. The loop then finalizes "no win found" with its highest-value
candidate never attempted. The tell: at finalize, the analysis named more distinct fixes than the
loop has iterations recorded, and the largest-effect one is unattempted.

**Rule.**
- Record the FULL distinct-candidate frontier in the loop's own state, not a single pick; a k=1 beam
  consumes one per iteration but must carry the rest so nothing is silently dropped.
- Order the frontier by expected effect against the measured noise, largest first; try the decisive
  lever before the safe-but-small ones.
- Refuse to finalize on a stall while confirmable, above-noise candidates remain and iteration
  budget is left.
- A blocked or inactive candidate (needs an out-of-scope change, or compiles but its optimization is
  gated off at the test vector) is a real verdict for that candidate — record it and move to the
  next frontier entry rather than treating it as the loop's conclusion.

## Part K — Why the effort must not stop here

The most expensive mistakes in this document are the ones that *terminate* the effort. Every other mistake wastes
time or produces a wrong intermediate result and the work continues; these end it while everyone's confidence is
intact.

### K1 🔴 Declaring the SLA infeasible when implementation changes are still in scope

**Pattern.** All *configuration* levers are exhausted and the target is not met, so a "final report" is written
declaring the target infeasible, listing kernel/compiler changes as out-of-scope blockers — when the mandate
explicitly permits kernel changes, framework changes, and anything else. The report is well-structured, honest,
profile-backed, and completely wrong in its conclusion: "infeasible" when the correct conclusion is "config-level
work is done; proceeding to kernel-level work."

**Why it happens.** The operator conflates "I have exhausted what I know how to do from the serving-config layer"
with "the problem is infeasible." The serving layer is ONE layer. Below it are the model code (modifiable), the
kernels (writable), the kernel library (patchable), the compiler flags, and the collective library — each a
surface that can be changed, not "next steps for someone else" but "my next steps, right now." The trigger is
often a delegate returning a diagnosis that *sounds* terminal ("compiler bug," "dead end on this stack,"
"requires SDK changes"). Those are accurate descriptions of ONE path being blocked. They are never proof that ALL
paths are blocked.

**Rule.**
- **Never write a "final report" until the target is met.** A progress report, a status update, a "config-level
  work complete, proceeding to kernel work" transition are all legitimate intermediate artifacts. A final report
  with a NOT MET verdict is a decision to stop, and stopping requires either the target met or the stakeholder
  saying stop.
- **When all config levers are exhausted, the work transitions to a different layer — it does not end.**
  Exhausting one step means you completed that step, not the effort.
- **Count your layers before concluding infeasibility:** configuration (degrees, buckets, concurrency) exhausted?
  → model code (fusion, layout, redundant ops) exhausted? → kernels (custom attention, fused norms, overlap)
  exhausted? → compiler workarounds (grid size, flag combinations) exhausted? THEN discuss infeasibility.
- **The word "infeasible" requires evidence that ALL layers have been tried**, not just the top one.
- **Verdict asymmetry, and the line between a finding and a stop.** "Reached" needs one solid, triangulated
  measurement. A *negative finding* needs more: near-floor operation demonstrated (G1), every axis searched to its
  favourable end (H9), and a bound whose measured anchor is independently cross-validated (J6). But satisfying
  those three licenses the **finding**, never the **stop** — the two are separate acts with separate authority.
  The admissible output is an interim status: "not reached in the configurations I ran," with the frontier table,
  the floor and required utilization, the accepted defaults, the unsearched axes, and the untried layers named.
  That report is honest, fully evidenced, and does not terminate anything; the words *final* and *infeasible*, and
  any hand-off of remaining in-scope layers to someone else, require the stakeholder.
- **Reporting a miss honestly and declaring infeasibility are different obligations.** The first is mandatory and
  mechanical: never soften a miss, and never let a ledger print "feasible" against an analytic floor once a
  measurement exists (J2). The second is not yours to make while in-scope layers are untried.

### K2 🔴 Accepting a delegate's failure diagnosis without attempting the workarounds it suggested

**Pattern.** A delegate analyzes a failure and returns both a root-cause hypothesis ("a compiler interaction when
multiple batch iterations read the same address") AND suggested diagnostics and workarounds ("try a single grid to
isolate the behaviour," "try the kernel test harness directly"). The operator reads the root cause, writes "dead
end — compiler bug," and never executes any of the suggestions. The delegate did its job; the operator
half-processed the delegation.

**Rule.**
- **A delegated diagnosis has TWO outputs: the root cause AND the suggested next steps.** Both must be acted on.
- **Before writing "dead end," enumerate the workarounds suggested and state which you tried.** If the answer is
  "none," it is not a dead end — it is an untested hypothesis with proposed falsification steps.
- Workarounds to try before declaring a kernel/compiler bug a dead end: change the grid/parallelism; change the
  layout/transpose flags; express the operation in a different but equivalent way; ask the kernel specialist for a
  differently-structured kernel implementing the same op; check whether a different entry point avoids the buggy
  path.

### K3 🟠 Ceasing autonomous work after a tool call completes

**Pattern.** A tool call completes and returns results. Instead of processing them and continuing, no further
output is produced — as if waiting for permission. Hours pass; the user returns and asks "status?" and the
operator reports results that have been sitting idle since they arrived. This is not a technical limitation, and
it happens specifically after long-running operations that *succeed*, after writing a report or document (as if
writing = done), and after a delegate returns a terminal-sounding diagnosis.

**Rule.**
- **After every tool result, the immediate next action is either (a) act on it, or (b) explain why you are
  stopping.** There is no state where results arrive and nothing happens.
- A successful compile → immediate benchmark. A benchmark result → immediate analysis. An analysis → immediate
  next step. The chain never pauses between links.
- **"The target is not yet met" is a standing work order.** Until it is met, every tool result that does not say
  "met" triggers the next iteration.
- Self-check: "Am I waiting for something, or have I stopped?" If the answer is "I don't know what to do next,"
  that itself is the next action: consult the profile, re-read the lever list, ask a specialist for candidates.

### K4 🟡 Delaying the next device action to refine an answer you already have

**Pattern.** A trace reveals the answer to the binding question (say, that prefill device time is dominated
neither by collectives nor by host overhead, so the problem is on-device compute/DMA efficiency). The operator
then spends a long stretch trying to correlate graph identifiers across cache directories to get a per-op
breakdown — a refinement that would sharpen the answer while the next action, already determined, does not start.
In a session where device time binds, holding the next launch behind a refinement is the analysis equivalent of
the oversized-sleep anti-pattern (D10).

**Rule.**
- **After each analysis step, state the conclusion AND the next action, then start the next action.** The
  refinement, if it is worth anything, proceeds beside the launch rather than in front of it.
- **This is a rule about ordering, never about abandonment.** "A finer breakdown would not change what I do next"
  licenses *deferring* it; it never licenses closing it. Investigations are finished, not dropped (H3): the
  refinement stays on the open-items list with its artifact paths recorded, and if it was needed to attribute a
  cost, it blocks the conclusion that cost supports (J5).
- **An anomaly or a disagreement is not a refinement and may not be deferred at all.** Two views of one quantity
  that differ, or a number far off its own floor, is the top task the moment it appears (E11). Before deferring
  anything, name the two independent views and their agreement; if you cannot, you are not refining, you are
  skipping.
- **The tell:** a chain of `find` / `grep` / `ls` / retry commands all trying to unlock a *view* of data you have
  already summarized numerically, with no device work running meanwhile. Note the numbers, log the artifact paths,
  launch the next thing, come back.

### K5 🟠 Certifying a target you did not measure

**Pattern.** A point is reported MET on the strength of an estimate, an inherited figure, or an adjacent
measurement.

**Rule.** Report a point as MET only when backed by a cited on-disk artifact from the *same* execution; otherwise
state it as estimated/uncertified and name the exact remaining measurement. Prune known dead ends but re-test
stale "unsupported / crashes / not implemented" claims against the *current* toolchain before building a
workaround — such claims go stale (H4, C9).

### K6 🔴 Inventing a scarcity constraint to justify stopping, and letting a stage machine stand in for the stop condition

**Pattern.** Work meets real friction (a tool wedged twice, a capture would not complete). Instead of routing
around it, the operator (a) asserts a budget/quota/"we are out of device time" that no tool ever reported — a
per-task or per-stage watchdog timeout is silently re-read as a global allowance being spent — and (b) treats
"every workflow stage ran once" as "the objective is met," declaring done while one objective (here, a whole SLA
leg) was never measured at all. The invented constraint is the rationalization that makes the premature stop feel
principled; the stage-machine completion is the checkable-but-wrong condition substituted for the real one.

**Rule.**
- **No scarcity/quota/budget/time constraint may be asserted unless it cites a tool result.** A per-stage idle
  watchdog, a single cancelled sub-task, or a compile timeout is never a global budget. If you cannot point to a
  measured number that establishes the limit, the limit does not exist — you are rationalizing.
- **"All stages ran" ≠ "the target is met."** A stage gate enforces ORDER and evidence-class; it does not enforce
  attainment. The stop condition is separate and per-objective: every leg either MET (measured) or a documented
  exhausted-miss with an empty untried-in-scope-layers list. Drive the campaign-done decision through the gate
  that checks *that* (a `stopcheck`), not through "the last stage is marked complete."
- **A skipped objective must be representable as data, not buried in prose.** Record each leg's verdict with its
  own tag and its untried layers as a list; an untagged or absent leg is UNCERTIFIED, not "done, second
  priority." The tell: a completion summary whose justification for not doing X is a sentence rather than a
  tool-checked field.

### K7 🔴 Discharging a blocker by writing it down instead of attempting its own named workaround

**Pattern.** A capture/tooling blocker is hit, and the operator records it faithfully — command, on-device error,
*and the workaround that would unblock it* — then moves on and reports an interim miss. Writing the escape hatch
down felt like progress, so it discharged the pressure to walk through it. The named workaround is never tried;
the recorded blocker becomes a silent exit from the whole line of work.

**Rule.**
- **Naming a workaround is not attempting it.** A blocker on a binding metric is closed only by RESOLVING it
  (the named workaround was actually run and left retry-evidence on disk) or by ESCALATING it (handed to a
  stakeholder with a reason). Recording it does neither.
- **The campaign-done gate must refuse while any binding blocker is open.** Do not let "I documented why I
  couldn't" substitute for "I tried the thing I said would work." If you wrote down decode-only-plus-kill, or
  raise-the-watchdog, or shrink-the-window, that sentence is a task you owe, not a conclusion.
- **The tell:** a blocker record whose `workaround` field is populated but whose `resolved`/`escalated` state is
  never set, sitting under a verdict of "interim miss / not_yet." That is an untried in-scope layer wearing the
  costume of a documented limit.

---

## Part L — Fast pre-flight checklist

Run before any hardware time. Each line is cheap and catches a class of wrong conclusion. Every line maps to an
entry above; completing this list is a **precondition for writing the plan** (C16), not the plan's first item.

**Target definition**

| # | Check | Entry |
|---|---|---|
| 1 | Target restated as explicit metric *quantities* (client-E2E vs device; output vs total tok/s; sustained vs peak) | A1, A2 |
| 2 | `IN:OUT` split obtained explicitly and recorded beside every number | A3 |
| 3 | Deployment shape (unified vs separated pools, single vs multi process) obtained explicitly, not inferred from the metric | A4 |
| 4 | Solution-class scope confirmed with the stakeholder; out-of-scope levers marked as such | A5 |
| 5 | Fidelity requirement resolved (is a lossy configuration admissible?) | H8 |

**Platform and environment**

| # | Check | Entry |
|---|---|---|
| 6 | `DP_max` derived from a **hardware-queried** logical core count (`devices × cores × LNC`), never inherited or remembered | A6 |
| 7 | Hardware prerequisites for every planned approach verified present | A7 |
| 8 | Integration state captured: package versions, resolved import paths, patch sentinels | D8 |
| 9 | Every path and mount named in a deployment doc verified to exist, with free space | D9 |
| 10 | Compile-cache roots enumerated and explicitly pinned; resolved path printed into the run log | D7 |
| 11 | All startup/readiness timeout variables enumerated from source and sized from measured load time × replicas; fail-fast progress detector wired | D3, D16 |
| 12 | Mandated capabilities confirmed present in the environment; absences reported | B8 |

**Procedure**

| # | Check | Entry |
|---|---|---|
| 13 | Governing specification resolved **by name to its directory** and read, with its tooling directory enumerated | B1 |
| 14 | Tooling trap/recovery list read before the first launch | D6 |
| 15 | Flag semantics for any state-mutating or verdict-emitting tool read from source | B1 |
| 16 | Delegation table checked: every sub-task with a specialist is delegated — discovery included — with timing, verification and the environment preflight retained; guarantees taken over are written down | B4, C16 |

**Analytic work (zero device time)**

| # | Check | Entry |
|---|---|---|
| 17 | Analytic entitlement/roofline computed **before** profiling; floors include compute + weight + KV/activation + communication | C1, G2 |
| 18 | Interior-optimum shard degree and the `DP × TP` recipe produced analytically before the first compile | C2, C3 |
| 19 | Capacity caps derived: weight bytes ⇒ devices per replica ⇒ `DP_max`; KV per **rank** using `min(TP, n_kv_heads)` | C4, C5 |
| 20 | Footprint from artifacts; `B` from the allocator's caps, with the binding cap named | C19 |
| 21 | Phase time-share computed on any prefill-heavy target; binding term named | C5 |
| 22 | Projections inverted to **required** efficiency, with a sensitivity row across every assumed constant | C12 |
| 23 | Every per-phase efficiency requirement includes the co-resident phase's `f_phase`, with a sensitivity row | C13 |
| 24 | Requirement recomputed in the same table as the measurement, at the current operating point | C14 |
| 25 | Latency-vs-offered-load curve planned before any efficiency bar is set; arrival process stated; requirement derived below utilization 1 | C15 |

**Search space**

| # | Check | Entry |
|---|---|---|
| 26 | Parallelism axes enumerated from the stack's **config surface + process-group construction**, not `devices/replica × replicas` | C10 |
| 27 | Tunables enumerated from the source tree; **every accepted default recorded** | C6, H1 |
| 28 | Env-var performance gates grepped from source near perf-critical paths; each gate's default recorded | H2 |
| 29 | Every space-pruning **constraint** traced to the line of code that enforces it — and to the **first** gate on the path | C9 |
| 30 | Axes to search identified including the *favourable* end and the plan's interior points; non-monotone axes bracketed | C11, H1, H9 |
| 31 | Each leg of a joint objective has at least one configuration tuned **for it** | C17 |
| 32 | The run-time arbiter (admission/batching policy) identified and on the sweep list | H6 |

**Session plan and evidence**

| # | Check | Entry |
|---|---|---|
| 33 | Session ordered: baseline launch → profile capture → sweep; most-likely-to-succeed config first | D1, D2 |
| 34 | Distinct compiled-graph cache keys enumerated; sweep grouped so each artifact is brought up once | D4 |
| 35 | Config shakedown assigned to a reduced-size model, not a full-weight launch | D5 |
| 36 | Profiler memory budget computed before choosing the memory-utilization knob | F4 |
| 37 | `IN + OUT ≤ max_sequence_length` asserted; one-request sanity check | E1 |
| 38 | Run shape proven at minimum scale; completion-rate acceptance gate wired to fail loudly | E8 |
| 39 | Normalized comparison metric implemented and exercised on one trial point before the sweep | E9 |
| 40 | Requested-vs-effective knob comparison wired to **exit non-zero** before any load is offered | C18 |
| 41 | Environment/topology echoed into an artifact at launch (device window, degrees, PID, expanded command line) | E13 |
| 42 | Every long step launched with a deadline, a pollable progress artifact, and no pipe into an end-of-input consumer | D10, D11 |
| 43 | Ledger row template includes config, artifact path, `measured/floor`, MEASURED-vs-PROJECTED tag, and one-line interpretation | J1, E5 |
| 44 | For every planned claim: which **different measurement path** will confirm it? | E11, J9 |
| 45 | Verdict rules pre-agreed: the `measured/floor` branch, the decision-critical set, and the depth of check each gets | G1, J10 |
| 46 | Discovery complete **before** the plan is drawn; any assumption that skipped it marked unchecked *in* the plan | C16 |

**Delegation and oversight**

| # | Check | Entry |
|---|---|---|
| 47 | Every capability the task needs mapped to a **named owner**; every prohibition names its replacement in the same sentence, with no permissive escape clause | B12 |
| 48 | Each replacement confirmed to **exist and be reachable** in the environment before the corresponding prohibition is written | B12, B8 |
| 49 | Every gate expressed as a **named artifact that must exist** before a named class of action, with the check and its owner stated; the running record framed as a per-step write-before-proceed obligation, not a deliverable | B13, B2 |
| 50 | Review/audit runnable on **partial state**, triggered independently of the work's outcome, with a checkpoint cadence whose absence is itself reviewable | J14 |

---

## Part M — Summary table

| Mistake pattern | Prevention | Entry |
|---|---|---|
| Leave the metric's unit undefined and thrash on configs | Resolve output-vs-total and which latency, with the requester, before measuring; report both readings | A1 |
| Quote a windowed peak as sustainable | Classify every field sustained-vs-peak and decode-only-vs-total; name one authoritative field | A2 |
| Inherit the harness's `IN:OUT` default as the requirement | Obtain the split explicitly; report a curve over the ratio | A3 |
| Infer the deployment shape from the metric definition | Enumerate supported shapes; record the assumed one beside the metric | A4 |
| Pursue a solution class the stakeholder excluded | Confirm scope before planning; flag infrastructure-dependent levers; never auto-execute a prior proposal | A5 |
| Assert a platform constant from memory | Query the device listing; derive `DP_max` from logical cores; re-derive everything downstream of a corrected constant | A6 |
| Plan around hardware you never checked for | Verify interfaces/topology before any compile or launch | A7 |
| Adopt an inherited table as the baseline | Re-measure at your own operating point first; verifying the artifact only decides whether the figure is worth chasing, never promotes it to evidence | A8 |
| Reuse another model's playbook without scoping the transfer | State what transfers and what does not; rebuild structure by hand | A9 |
| Drive a procedure's tooling before reading the procedure | Resolve the spec by name to its directory; read flag semantics from source; verify cross-references resolve | B1 |
| Treat a mandated step order as a suggestion | Blocking dependency chain, enforced by a pre-launch checklist artifact | B2 |
| Record the quick stages and bypass the slow ones | Opening a stage is part of the work; audit the ledger in both directions | B3 |
| Perform the specialist's analysis, or hand the workflow slices instead of the objective | Delegate what a specialist owns, discovery included; give the owner the objective; retain timing, verification and the preflight; write down guarantees you take over | B4 |
| Rebuild a purpose-built model by hand after finding a bad input | Correcting an input is a re-run, not a rewrite; keep both derivations and reconcile | B5 |
| Substitute an out-of-scope tool, the wrong measurement, or a hand-rolled harness | Use the sanctioned path and the prescribed measurement; reconcile any hand-rolled metric definition | B6 |
| Accept "blocked" or "the tool was unavailable" | Verify every blocker personally; expect blocker substitution; never certify on a self-inflicted blocker | B7 |
| Silently skip a mandated capability that is absent | Confirm in preflight; report the gap in the next status report | B8 |
| One giant brief and one final report | Front-load a verification gate; require incremental evidence; retract explicitly | B9 |
| Full-read huge generated artifacts | Size-check, then extract only what is needed | B10 |
| Read a self-sufficient brief as specifying its procedure | A brief cannot specify its own procedure; its reading list is a floor | B11 |
| Forbid a mechanism without naming its replacement | Name the substitute in the same sentence; confirm it exists first; delete escape clauses; map every needed capability to an owner | B12 |
| Write a gate as an ordering sentence and a ledger as a deliverable | A gate is a named artifact required before a named action; the record is a per-step write-before-proceed obligation | B13 |
| Reason upward from measurements with no floor | Compute the floor before the first launch; carry `measured/floor` in every ledger row | C1 |
| Brute-force the parallelism search by serial compiles | Analytic model first; compile only its recipe; track the compile-cycle ratio | C2 |
| Treat a joint SLA as one operating point | Reconcile into a pair; expect the legs to pull opposite ways | C3 |
| Assume higher TP means more KV room | KV cannot shard past `n_kv_heads`; beyond it TP replicates KV, batch is flat, collectives double, DP halves | C4 |
| Tune parallelism for output throughput on a prefill-heavy mix | Compute the phase time-share and capacity caps first; use entitlement values in any ceiling | C5 |
| Treat a shipped default as neutral | Grade a default by provenance; find in-tree overrides; determine which term of a `min()` binds | C6 |
| Copy a prior run's tuned value | The sizing rule is the lever; re-derive it; read the clamp diagnostic | C7 |
| Set a variable by what its name implies | Read the consuming/validating code; verify the lever by the output it controls | C8 |
| Let a stated constraint prune the space unverified | Cite the enforcing symbol and the **first** gate; state `enforced at X` vs `stated, unverified` | C9 |
| Design a topology from `devices × replicas` | Enumerate degrees from the config surface and process-group construction | C10 |
| Bracket an axis and skip the interior point | The plan's named interior optimum is measured first; diff planned points against run points | C11 |
| Choose the efficiency constant that makes the target reachable | Invert to required efficiency; publish a sensitivity row | C12 |
| Derive a per-phase requirement granting the phase all device time | Include `f_phase`; the omission always understates by `1/f_phase`; re-check against the architectural ceiling | C13 |
| Compute the requirement once and reuse it | A requirement is a function of the operating point; recompute it in the same table each iteration | C14 |
| Set the bar from a queueing model at capacity = demand | Measure the latency-vs-load curve first; state the arrival process; derive below utilization 1 | C15 |
| Write the plan and schedule its discovery afterwards | Discovery is a precondition for the plan; the planner owns it and a specialist performs it; never the executor | C16 |
| Call the legs of a joint objective conflicting | Build and measure one configuration tuned per leg first | C17 |
| A precondition check that logs but cannot abort | Compare requested vs effective and exit non-zero; grep for clamp vocabulary | C18 |
| Derive footprint and batch from clean arithmetic | Footprint from artifacts and resident memory; `B` from allocator caps with the binding cap named | C19 |
| Let the sweep crowd out the profile | Profile after the first baseline launch, before the sweep; verify profiling was armed | D1 |
| Schedule the decisive config last | Most-likely-to-succeed first; raise readiness timeouts; stage the checkpoint locally | D2 |
| Raise the wrong timeout variable, or raise them all as insurance | Enumerate from source; read the name from the error message verbatim; size from measured load time; detect failure from absent log progress, not an expiring timeout | D3 |
| Re-pay compilation on every sweep point | Enumerate cache keys; one bring-up per compiled artifact; report bring-up:measurement ratio | D4 |
| Shake down a config on a full-weight launch | Reduced model for plumbing; full weights only for SLA numbers | D5 |
| Rediscover a documented trap at full price | Read the trap list before the first launch and lift it into the launch command | D6 |
| Clear one cache while the stack reads another | Enumerate and clear all roots; pin the root; assert a delete target exists; verify by compile time | D7 |
| Measure a code path whose integration state is unverified | Capture versions, resolved import paths and patch sentinels as an artifact | D8 |
| Trust a mount named in a deployment doc | Verify existence, space and identity before launch | D9 |
| Block on one oversized sleep | Poll on short intervals; calibrate from the first attempt; check before sleeping | D10 |
| Read elapsed time as progress | Prefer convert-and-exit; poll a growing artifact; read deltas, not lifetime averages | D11 |
| Abort a warm bring-up on an init banner | Grep forward for warmup-completed; confirm a miss from the cache directory | D12 |
| Change two axes when triaging a failure | One variable per retry; read the error's own locus first | D13 |
| Nudge a magnitude and call it a hypothesis | After the second same-shape failure, read the artifacts you already have | D14 |
| Assert "nothing is running" from memory | Check the process table and device status; launch with a known PID and timeout | D15 |
| Relaunch over stale device state | Full process-group cleanup and device verification after any abnormal termination | D16 |
| Verify a destructive cleanup only against its targets | Reconcile the parent scope against what the command could reach; confirm no live actor first; positive-check what survived | D17 |
| Leave a defaulted knob to auto-adjust into a bring-up abort | Pin every discrete-bucket dimension in-set; disable features not under test; verify accepted values in the startup echo | D18 |
| Attribute a number to a control the platform silently overrode | Set the level through the framework's own knob; require the effective value in the log to equal intent before trusting timing | D19 |
| Request more tokens than the sequence length allows | Assert the bound; sanity-check one request | E1 |
| Quote a delta from runs differing in more than one variable | Match everything but the variable under test, in one session | E2 |
| Quote an aggregate without checking its identity; count events | Reconcile against shapes and counts; count bytes, not descriptors | E3 |
| Stitch a conjunctive SLA from two load points | One run at one finite arrival rate; never quote a percentile from saturation | E4 |
| Multiply one replica by the replica count | Two concurrent replicas gate the projection; launch N with one client each before the aggregate faces the bar; tag MEASURED vs PROJECTED in the summary line | E5 |
| Report a percentile over a handful of samples | Match sample count to percentile; state the margin beside it; measure the interior of any gap | E6 |
| Compare sustained rates across run shapes | Normalize prompt count, duration and arrival pattern; suspect run shape before the system | E7 |
| Quote metrics from a run that mostly failed | Prove the shape at minimum scale; completion-rate gate that yields no metrics below threshold | E8 |
| Collect a sweep before the normalized metric exists | Implement and exercise the reduction first; name the discriminating signature in advance | E9 |
| Compile a width the occupancy cannot fill | Set width from achieved capacity; vary width with item count to measure per-item cost | E10 |
| Let one measurement path decide a verdict | ≥2 independent paths must agree; a 2× disagreement is a hard stop, never a caveat | E11 |
| Confuse device-level with served throughput | Label which one; the gap is the prefill/scheduling tax; a derived end-to-end estimate never substitutes for the served run | E12 |
| Lose topology evidence at teardown | Echo the environment and expanded command line into an artifact at launch | E13 |
| Trust a profile's phase label | Verify from per-instruction kernel signatures and per-step token counts | F1 |
| Capture decode under open high-arrival load | Closed batch, small concurrency, long output, no new arrivals | F2 |
| Act on a lever from a profile at the wrong operating point | Record the profiled batch and context; reconcile with the served point | F3 |
| Discover the profiler's memory need after the first launch | Compute the profiler budget before choosing the memory knob; validate on the first request; let the capture's purpose set the occupancy | F4 |
| Serialize a whole multi-rank trace | Pin one rank, ingest to columnar, query specific fields, bound the job | F5 |
| Call a large `measured/floor` gap a ceiling | Branch on the ratio; the words ceiling/unreachable are unavailable at a large gap | G1 |
| Compare against a single-resource floor | Floor = weights + KV/activations + compute + collectives, at the same shape; quote the definition inline | G2 |
| Collapse achieved, entitled and required | Hold three numbers apart; a large achieved-vs-entitled gap is a mandate to continue | G3 |
| Assert a mechanism with no profile | Any "because/bound by" sentence cites an artifact path or is rewritten as a hypothesis; free engine metrics observe, they never license a *because* | G4 |
| Read a plateau as a wall | A config-invariant number implicates config-invariant code, not silicon | G5 |
| Derive a component by subtraction; port a calibration across regimes | Query the component's own rows; calibrate per regime; state which side the uncertainty falls on | G6 |
| Use a closed form outside its domain and label it indicative | Name every violated assumption; a disclaimer does not bound downstream use | G7 |
| Sum unimplemented levers into a bound | A projection may motivate an experiment, never replace one; implement its cheapest term | G8 |
| Attribute a cost without naming its layer | State host/dispatch/device with evidence; the layer determines the remedy | G9 |
| Report a grep hit as a root cause | Prove the path executes in your configuration | G10 |
| Let an unattributed bucket argue both sides | Decompose it before it informs anything; rank buckets and investigate the largest | G11 |
| Treat integration adaptations as plumbing | Ask what integration changed; shape changes and host-side moves are prime suspects | G12 |
| Treat an untuned axis as an optimum | Enumerate tunables, log accepted defaults, bracket non-monotone axes | H1 |
| Declare a cost irreducible without grepping for a knob | Grep env/config gates for that specific term; "unsupported" must be an observed error | H2 |
| Disprove one fix and declare the problem unsolvable | measure → localize → search mitigations → test → conclude | H3 |
| Record an untested lever as a dead end | Keep tested-null / untested-deprioritized / dead-end distinct; only the third is inherited | H4 |
| Retract a finding without re-opening what it pruned | Name the exclusions and restore them; keep the dead-end list dated and sourced | H5 |
| Call a tradeoff structural with the arbiter pinned | Sweep the admission/batching policy; record its setting on every frontier point | H6 |
| Rank levers from out-of-scope magnitudes | Carry the scope beside every magnitude; re-derive the ranking at the operative config | H7 |
| Headline a lossy lever's gain | Faithful ceiling and lossy figure side by side; probe fidelity on divergence-capable content | H8 |
| Claim "never reaches T" from the unfavourable end | Measure the favourable end plus an interior point; enumerate valid points first | H9 |
| Call a capability unavailable after one mechanism's rejection | An "unsupported" error is evidence only about the invocation you ran; enumerate the distinct mechanisms (generic plugin vs model-native path) and try the intended one; never narrate a fallback as a structural fact before the preferred invocation was attempted | H10 |
| Architect around unverified overlap | Confirm expressibility and residency constraints, then a few-layer overlap experiment | I1 |
| Rewrite the newest component first | Isolate every stage on device; check scale direction and placement | I2 |
| Return the original after mutating a contiguous copy | Return the tensor you mutated; test with non-contiguous input | I3 |
| Expect a fusion pass to see inside a custom kernel | Confirm the ops are in the form the pass matches; record IR evidence | I4 |
| Globally rescale quantized weights | Quantify the affected population; point-clamp the minority; assert bit-identity elsewhere | I5 |
| Accept simulator agreement as numerical proof | On-device output agreement is the gate; inject real scales into simulation | I6 |
| Close a correctness workaround without measuring it | Measure the delta at production shape; document the slower path it activates | I7 |
| Defer the report and lose the synthesis | Append to the ledger after every measurement | J1 |
| Correct the prose and leave the machine state | Withdraw everywhere in one pass, through the tool; corrections are append-only; grep for the old string | J2 |
| Audit the ledger in one direction | Check both unsupported claims and unrecorded work | J3 |
| Overwrite a failure log | Timestamped filenames; preserve the failing log; mtime is the cheapest provenance check | J4 |
| Verify every claim and never verify coverage | Coverage checklist keyed to the plan; a step with no artifact is NOT RUN; "to be replaced by X" blocks the verdict | J5 |
| Certify provenance and candour, not validity | Name and independently test the load-bearing inference; honesty is necessary, not sufficient | J6 |
| Declare a claim unverifiable because the patch is elsewhere | Ask structurally-achievable → plausible → deployed, in that order; rung 1 needs the **first** gate on the path, and rung 2 yields a plausibility bound, not a certification | J7 |
| Refute a claim about config B with a measurement at config A | A measurement speaks only to its own operating point; compute no cross-config miss ratio; run the benchmark at B or report it unverified | J8 |
| Re-run the same query and call it independent verification | Independence is a property of the path; exact agreement is a shared-path warning | J9 |
| Scrutinize what disagrees and wave through what agrees | Depth from consequence, decided in advance; a lever-closing claim gets the deeper check | J10 |
| Assume the review loop fired; accept the audit as evidence | Verify the iteration happened; spot-check the decision-critical findings | J11 |
| Leave a wrong report on disk | Retract in place, versioned, naming what the retraction re-opens | J12 |
| Let a lessons doc transmit a verdict | Write the class, not the case; every number an example; no ratio against a hidden bar | J13 |
| Wire the audit so it is reachable only through success | Runnable on partial state, triggered independently of outcome; keep "not reviewed" distinct from "nothing found" | J14 |
| Declare the SLA infeasible with implementation layers untried | Count the layers; publish the negative finding as an interim status; *final* and *infeasible* require the stakeholder, while reporting the miss honestly is mandatory | K1 |
| Accept a diagnosis and skip its workarounds | A diagnosis has two outputs; enumerate the suggested workarounds and state which you tried | K2 |
| Idle after a tool result | Act, or explain why you are stopping; the chain never pauses | K3 |
| Hold the next device action behind a refinement | State conclusion plus next action and start it; defer the refinement without closing it; never defer an anomaly or a disagreement | K4 |
| Certify a target you did not measure | MET requires a cited artifact from the same execution; re-test stale unsupported claims | K5 |

---

## Part N — Cross-cutting roots, provenance, and the entry template

### The seven roots

1. **Trusting a label, a plausible aggregate, or an operating-point assumption instead of verifying the primary
   artifact.** Before reasoning on a profile, prove *what it is* (phase, batch, context); before quoting a delta,
   prove the A/B changed one variable; before quoting a rate, prove which quantity it counts. (A1–A3, E2–E3, F1–F3)
2. **Mis-sequencing a scarce, expensive resource.** Device time binds, and most bring-up failures are scheduling
   errors: the profile deferred until the budget was gone, the decisive config run last, each sweep point re-paying
   compilation, full weights spent on a question a reduced model answers, documented traps rediscovered at full
   price. The corrective is a pre-launch plan, then writing results down as they arrive. (D1–D6, J1)
3. **Acting outside the problem boundary.** Technical excellence on the wrong solution is zero value. Confirm the
   solution-class scope, verify hardware prerequisites before spending compile time, and never auto-execute a
   scope-crossing recommendation. (A4–A7, K1)
4. **Reasoning upward from measurements with no floor to divide by.** Without an entitlement floor, a plateau reads
   as a wall, a copied constant reads as a tuned lever, a padded width reads as a fixed overhead, and a grep hit
   reads as a root cause — each of them the more flattering of two readings, and each unfalsifiable. Compute the
   floor before the first launch; carry `measured/floor`; forbid ceiling claims not stated as ratios. (C1, C7, G1–G3,
   G10, E10)
5. **The verification apparatus only sees what was said.** Every claim-shaped check is blind to the step that never
   ran and therefore left no artifact. Audit *plan coverage* separately from claim correctness, keyed to the
   numbered plan, and treat any self-issued "to be replaced by the real measurement" as a blocking open item.
   (J5–J6, J11)
6. **The thing you are measuring is not verified to be the thing you think.** A patch applied to the wrong copy of a
   library, a cache clear that deleted nothing, a conservative default inherited from another model family, a
   baseline borrowed from someone else's operating point — each leaves the run *looking* like the configuration you
   described while behaving like a different one, and none produce an error. Verify the *setup* with the rigour the
   rest of this document applies to the *results*. (A8–A9, C6–C8, D7–D9)
7. **An audit that checks file existence instead of reasoning about physics.** The stock source of the system being
   optimized is on disk, showing every inefficiency an optimization addresses. An auditor who can read source should
   independently derive the same opportunities and reach the same conclusions — or different ones that expose a
   flaw. Checking whether a patch exists is clerical; checking whether the physics holds is engineering. (J6–J10)

### Provenance

**`FRAMEWORK_MODEL_OPTIMIZATION_gaal.md`** (#n)

| Source | Merged into |
|---|---|
| #1 skipping mandated step ordering | B2 |
| #2 performing analysis a specialist exists for | B4 |
| #3 trusting a profile's phase label | F1 |
| #4 capturing decode under open load | F2 |
| #5 profile operating point ≠ served point | F3 |
| #6 undefined throughput metric; prompt:output addendum | A1, A3 |
| #7 prompt + output exceeds max sequence length | E1 |
| #8 broad process-kill patterns | D15 |
| #9 A/B differing in more than one variable | E2 |
| #10 promoting unvalidated heuristics into shared docs | J13 |
| #11 sweep substituted for a profile | D1 |
| #12 decisive config last, behind default timeouts | D2 |
| #13 sweep not ordered around compiled-artifact reuse | D4 |
| #14 full-size launch for config shakedown | D5 |
| #15 device time spent before reading the trap list | D6 |
| #16 unverified path or mount from a deployment doc | D9 |
| #17 no incremental results ledger | J1 |
| #18 out-of-scope solutions without stakeholder confirmation | A5 |
| #19 unverified hardware prerequisites | A7 |
| #20 overwriting log artifacts | J4 |
| #21 burst peak reported as sustained | A2 |
| #22 ceiling declared with no floor | C1, G1, G5 |
| #23 verifying claims, never coverage | J5 |
| #24 lever value carried instead of its sizing rule; clamp diagnostic | C7 |
| #25 compiled width the occupancy cannot fill | E10 |
| #26 root cause on a non-executing path | G10 |
| #27 untested lever recorded as a dead end | H4 |
| #28 axis bracketed, interior skipped; anchor addendum | C11 |
| #29 cost declared irreducible without grepping for a knob | H2 |
| #30 audit certifying provenance, not inference | J6 |
| #31 retraction that does not re-open pruned levers | H5 |
| #32 remediation that silently no-ops | D7 |
| #33 library default treated as neutral; `min()` term | C6 |
| #34 inherited bundle numbers as the baseline (normative) | A8 |
| #35 measuring an unverified integration state | D8 |
| #36 precedent from another model, transfer unstated | A9 |
| #37 "unverifiable" instead of deriving from stock source | J7 |
| #38 device-level vs end-to-end throughput | E12 |
| #39 audit as provenance-checking | J7 |
| #40 refuting config B's claim with config A's measurement | J8 |
| #41 brute-forcing the parallelism search | C2 |
| #42 multiple axes changed during failure triage | D13 |
| #43 mechanism asserted without a profile | G4 |
| #44 oversized sleep intervals | D10 |
| #45 environment variable the stack does not recognize | C8 |
| #46 wrong startup timeout variable | D3 |
| #47 profiler HBM allocation requirement | F4 |
| #48 collective bootstrap hang from stale device state | D16 |
| #49 secondary analysis after the primary question is answered | K4 |
| #50 clearing one compile cache of several | D7 |
| #51 declaring infeasible with implementation changes in scope | K1 |
| #52 accepting a diagnosis without its workarounds | K2 |
| #53 ceasing autonomous work after a tool call | K3 |
| Cross-cutting takeaways (roots 1–7) | Part N, "The seven roots" |

**`FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md`** (Ln)

| Source | Merged into |
|---|---|
| Transfer contract; entry template | "How to read this document"; Part N template |
| L1 a finished turn does not stop spawned processes | D15 |
| L2 sanctioned toolkit and sanctioned measurement | B6 |
| L3 ingest one rank, query fields | F5 |
| L4 do not full-read huge artifacts | B10 |
| L5 reports are hypotheses; verify against primary artifacts | A8, E3 |
| L6 optimization-decision discipline (joint pair, binding op, integration, dead ends, finish, do not certify) | C3, G12, H3, K5 |
| L7 the orchestrator's own prompt inputs are evidence | A6 |
| L8 pin the SLA's unit | A1 |
| L9 arithmetic identity; count bytes not events | E3 |
| L10 "blocked" is the highest-cost claim | B7 |
| L11 independent audit; audit the auditor; confirm the loop ran | J11 |
| L12 short prompts, incremental evidence, explicit retraction | B9, J12 |
| L13 regime-specific error direction; never derive by subtraction | G6 |
| L14 a node aggregate must be measured with replicas concurrent | E5 |
| L15 a joint claim needs one run at one finite rate | E4 |
| L16 adjudicate every throughput unit | A2 |
| L17 percentile sample counts; no assumed monotonicity | E6 |
| L18 normalize herd size and duration | E7 |
| L19 capture environment and topology at launch | E13 |
| L20 ledger and narrative corrected in one pass | J2 |
| L21 load the governing skill before its tooling; flag semantics from source | B1 |
| L22 a lossy win needs its fidelity cost and the faithful ceiling | H8 |
| L23 campaign residue becomes a false prior | J13, transfer contract |
| L24 a brief specifies the task, not the procedure | B11 |
| L25 partial compliance corrupts by understatement | B3, J3 |
| L26 do not hand-build reserved infrastructure | B6 |
| L27 an absent mandated capability is a reported gap | B8 |
| L28 a variable's contract comes from the validating code | C8 |
| L29 an unresolvable citation leaves a claim unverified | C9 |
| L30 resolving a specification by filename search | B1 |
| L31 a precondition check with no abort authority | C18 |
| L32 prove the run shape at minimum scale; completion-rate gate | E8 |
| L33 nudging a magnitude is not a new hypothesis | D14 |
| L34 build the normalized comparison metric before the sweep | E9 |
| L35 owning a workflow's sequencing yourself | B4 |
| L36 a bad input obliges a re-run, not a rewrite | B5 |
| L37 verification depth from consequence, not agreement | J10 |
| L38 a projection over unattempted work is not a ceiling | G8 |
| L39 an analytic form outside its validity domain | G7 |
| L40 "beyond measured" is not "beyond entitlement" | G3 |
| L41 each leg of a joint objective needs its own tuned config | C17 |
| L42 a tradeoff is not structural until the arbiter is swept | H6 |
| L43 an unresolved aggregate bucket decides nothing | G11 |

**`FRAMEWORK_MODEL_OPT_lucy.md`** (Lucy n)

| Source | Merged into |
|---|---|
| Purpose, scope, symbolic notation | Header |
| Fast Pre-Flight Checklist (25 rows) | Part L (extended to 46 rows) |
| 1 verify overlap is achievable before architecting | I1 |
| 2 the newest code is usually not the culprit | I2 |
| 3 returning the original after mutating a copy | I3 |
| 4 fusion passes cannot see inside custom kernels | I4 |
| 5 saturate, don't rescale, between dtype variants | I5 |
| 6 a simulator cannot validate scale arithmetic | I6 |
| 7 a correctness workaround changes the performance path | I7 |
| 8 output throughput set by the phase time-share; identity vs bound | C5 |
| 9 a report's latency table may not be your metric | A8 |
| 10 "infeasible" requires an entitlement ceiling | G1, K1 |
| 11 verify a delegate's "the tool was unavailable" | B7 |
| 12 one end of an axis cannot support a whole-axis claim | H9, C11 |
| 13 do not abort a warm bring-up on a backend-init line | D12 |
| 14 GQA KV does not shard beyond `n_kv_heads` | C4 |
| 15 the `IN:OUT` ratio is part of the target | A3 |
| 16 compute realized-vs-floor against a complete floor | G2 |
| 17 label projected aggregates as projected | E5 |
| 18 a ceiling claim requires floor proximity | G1 |
| 19 triangulate every verdict-carrying number | E11 |
| 20 nothing you did not tune is at an optimum | H1, A8, C6 |
| 21 audits must attack the premise; prevention executable alone | J6, J12, B7 |
| 22 grep the source for performance-gated env vars | H2 |
| 23 diagnose, then search for mitigations | H3 |
| 24 enumerate the parallelism axes the stack implements | C10 |
| 25 a structural constraint is a hypothesis until enforced in code | C9 |
| 26 run discovery before forming the plan | C16 |
| 27 invert to required efficiency | C12 |
| 28 capacity and batch from artifacts and allocator caps | C19 |
| 29 verify hardware capacity from the hardware itself | A6 |
| 30 the deployment shape is part of the target | A4 |
| 31 a verdict hinging on a contention term must measure it | C15 |
| 32 finding one gate is not finding all of them | C9 |
| 33 independence is a property of the measurement path | J9 |
| 34 a still-running process is not necessarily working | D11 |
| 35 name the execution layer of a cost | G9 |
| 36 rank levers only from scoped attribution | H7 |
| 37 a per-phase requirement must include `f_phase` | C13 |
| 38 a requirement is a function of the operating point | C14 |
| Summary: Meta-Lessons (38 rows) | Part M |

### Conflict resolutions applied

Twelve contradictions between the sources were held in `FRAMEWORK_MERGE_CONFLICTS.md` rather than merged. All are
now resolved and integrated; the inline dispute markers are gone. **Nine were decided in favour of one source,
changing guidance** — the retained position is recorded in the conflicts file, and the losing statement no longer
appears anywhere in this document.

| # | Question | Resolution | Entries changed |
|---|---|---|---|
| X2 | Can artifact verification rehabilitate an inherited number? | **No, under any circumstances.** Confirming traceability, quantity and workload is a *triage device* for deciding whether an inherited figure is worth chasing; it is never a promotion path to evidence, because matching cannot cover patch state, tree state, package version or machine. | A8 |
| X4 | May a device-level number plus a derived penalty stand in for a served measurement? | **No.** A first-principles end-to-end estimate is a projection over a configuration nobody served: it may motivate the run and may argue a claim is *not refuted*; it can neither verify nor refute. The served measurement stays a verdict-blocking open item. | E12, J8 |
| X5 | Can a mechanism be established without a device profile? | **No — no exemption for cheap signals.** Engine metrics, scheduler source and startup capacity lines are admissible as *observations* and identify which term to test; none licenses a *because*. Every binding claim carries a profile citation before it steers a compile. | G4, C5 |
| X7 | Delegate the analysis, or perform your own discovery? | **Delegate, including discovery.** What never transfers: the *timing* (discovery completes before the plan), the *verification* of what came back, and the environment preflight required to write a correct delegation prompt at all. Discovery is *owned* by the planner and *performed* by the specialist; it may never be handed to the plan's executor. | B4, C16 |
| X8 | Startup timeouts generous, or fail fast? | **Fail fast.** "Over-setting is harmless" is withdrawn: a long timeout is the mechanism by which a wedged bring-up consumes the session. Size platform limits from measured load time × replica count, and detect failure from *absence of forward progress in the log*, not from a timeout expiring. | D3, D16 |
| X9 | Time-box the analysis, or finish the investigation? | **Finish the investigation.** Time-boxing survives only as an *ordering* rule — start the determined next action rather than holding it behind a refinement — never as licence to close anything. An anomaly or a disagreement between two views may not be deferred at all. | K4 (retitled and rewritten), E11, H3 |
| X10 | Is stock source sufficient to certify an optimization is achievable? | **No.** Structural achievability requires the call site, the target signature **and the first gate on the path**, including any deployment-class validator; a signature-and-call-site read is the *inner* gate. The claimed magnitude is a plausibility bound requiring production-shape confirmation and attribution by ablation. | J7 |
| X11 | May the operator conclude and publish infeasibility? | **No, while in-scope layers remain untried.** The finding and the stop are separate acts with separate authority: a fully-evidenced negative *finding* is published as an interim status naming the frontier, the floor, the required utilization, the unsearched axes and the untried layers. The words *final* and *infeasible* require the stakeholder. Reporting a miss honestly remains mandatory. | K1, E4 |
| X12 | How concrete may an entry's numbers be? | **Illustrative only.** A figure survives where its arithmetic *is* the lesson; achieved rates, distances from a bar, tuned values and per-platform resource sizes become a direction plus the sizing rule that regenerates them — C7 applied to this document. | Transfer contract, J13 |

The remaining three were resolved as **scope distinctions rather than contradictions** — each side holds over a
different range, and both statements were already carried in the merged text. Their markers are removed and the
boundary is now stated in the entry itself:

- **X1** — latency is monotone in shard degree only while the phase is compute-bound and collective cost is a small
  share of the step; past that the curve turns over and the optimum is interior. "More devices for latency" gives the
  direction, never licence to skip interior points. `n_kv_heads` is a hard ceiling for *capacity* and a soft one for
  latency. (C3, C4)
- **X3** — two concurrent replicas falsify *independence*, which is what the multiply rests on, and legitimately size
  the full run; they cannot bound contention that only appears at higher occupancy. Necessary gate before any
  projection; not evidence at N. (E5)
- **X6** — the capture's purpose sets the knob: low occupancy is valid for *structure*, while any *magnitude* that
  ranks levers or supports a per-item claim needs occupancy matching the served point, with profiler headroom found
  elsewhere. Reduced-occupancy captures are labelled and may not be cited for ranking. (F4)

Where a resolution went against a source, that source's provenance row above maps to the merged entry in its
**amended** form: the pattern and mechanism survive, the withdrawn rule does not. The specific rules withdrawn are
[G] #46's "over-setting is harmless," [G] #49's stopping test, [G] #37/#39's certification from stock source alone,
[G] #38's route (b) as verification, [G] #2's exclusion of discovery from delegation, [K] L5 / [L] 9's artifact
check as a promotion path, [L] 19's free-metrics read as a mechanism, [L] 21 rule 3's negative verdict as a
terminal output, and [G]'s concrete achieved magnitudes throughout.

### Entry template — for every new lesson in either document

An entry teaches one failure class. It carries no campaign, no model, no host, no bar, no run.

```markdown
### <Part><n> <severity> <one-line rule, stated so it applies to any model on any host>

**Pattern.** <the mechanism, anonymous: what was believed, what was actually true, and what the tell was.
Numbers appear only as illustration — "say", "e.g.", "a handful of" — never as an achieved result, a payoff,
or a distance from a bar.>

**Rule.**
- <portable rule: what to do instead, checkable without knowing the model>
- <portable rule>
```

Rules for filling it in:
- **Write the class, not the case.** If a sentence only makes sense to someone who was there, cut it.
- **Name nothing.** No model, parameter count, instance type, core count, target value, file path, date, shard
  count, or directory name.
- **Frame every number as an example.** Keep a figure only when the arithmetic *is* the lesson (an identity that
  closes on a padded dimension, a percentile over too few samples). Drop it when it reads as "this is what we got."
- **Assert no outcome.** Never state or imply that a target is unachievable, that a lever will pay, or that an
  approach is settled.
- **State the direction, not the magnitude:** "materially below", "an order of magnitude", "far inside its own
  noise".
