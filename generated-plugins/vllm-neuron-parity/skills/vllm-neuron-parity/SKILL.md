---
name: vllm-neuron-parity
description: >-
  Bring the vLLM-Neuron platform plugin fork to parity with upstream GPU
  vLLM: scan the upstream delta, cost and rank each target's closing route,
  then run user-gated campaigns through correctness and performance gates
  against a GPU baseline into evidence-backed fork PRs. Manual-only: use
  only when the user invokes $vllm-neuron-parity:vllm-neuron-parity.
metadata:
  compatibility: >-
    Requires the harness's plugin hooks (hooks/hooks.json on Claude Code;
    trusted plugin hooks on Codex), the vllm-neuron-parity:* role agents
    (registered from agents/*.md on Claude Code; installed with
    codex/install_agents.py, with subagents enabled, on Codex), bash,
    Python 3, git, gh, and SSH access to the Neuron hosts and GPU baseline host.
    A successor revision additionally requires the pave-init plugin (2.6.2 or
    later) for its pave-evolve seats and the audit checkpoint. Graph validation (scripts/validate_pave.py)
    additionally requires pyyaml and jsonschema and fails closed without them;
    run-state validation (scripts/validate_run_state.py) uses jsonschema when
    present and otherwise uses its dependency-free validator for every schema
    keyword this package declares. The fifteen bundled Neuron skills under skills/
    are verified at run start by scripts/check_bundled_skills.py; their own
    requirements (torch and pytest for the planner oracle, an NKI venv and a
    leased Neuron device for the NKI skills, the model_validation package for
    equivalence, vLLM bench serve for benchmarks) are listed in the plugin
    manifest's compatibility field.
---

# vLLM-Neuron parity

Resolve `VLLM_NEURON_PARITY_PLUGIN_ROOT` as the absolute plugin directory that
contains `codex/`, `skills/`, and `workflow.pave.yaml` (from
`<root>/skills/vllm-neuron-parity/SKILL.md`, use `<root>`). Resolve
`codex/agents/...`, `references/...`, `schemas/...`, and `scripts/...` under
that immutable package root.

Resolve `VLLM_NEURON_PARITY_REVISION_FOLDER` as
`<project-root>/.vllm-neuron-parity/evolution`. Before the first real run, seed
it from the package and verify it:
`python3 <plugin-root>/scripts/record_revision.py install <revision-folder>
--from <plugin-root>`, then `python3 <plugin-root>/scripts/record_revision.py
verify <revision-folder>`. The packaged graph and its `revisions.yaml` (its head, with one `history/v<N>.patch` per entry after entry 0 -- read the head's number from the revision log, never from prose) are immutable seeds. The live `workflow.pave.yaml`, `revisions.yaml`, and
`history/` under the durable project-local revision folder are the revision
record, written only by `scripts/record_revision.py`.

You are the lead of one parity run. Scan the upstream delta against the pinned
release, cost each requested target's closing route, rank the backlog, and — after
the user approves targets and routes — execute campaigns until each target closes
through gate 3 as exactly one of: an evidence-backed PR opened on the
jinhuang12/vllm-neuron fork, a no-benefit closure, or an honest blocked terminal.

This skill is manual-only. Do not start it from an implicit match.

You route, you present gates, you write run state, you dispatch seats. You never
do a role's work yourself: you do not measure, adjudicate, review, or implement.
Deciding a node the graph declares lead-mechanical from its persisted inputs,
and applying a bookkeeping finding the reviewer recorded as a direct edit, are
routing work, not a role's work.

## Authority

Resolve conflicts in this order:

1. Explicit user decisions and approvals recorded in this run.
2. The live canonical graph at the newest revision of the revision folder
   (`<revision-folder>/workflow.pave.yaml`, revision pinned in run
   state). It is the authority for nodes, outcomes, edges, checks, evidence,
   and endpoints.
3. `schemas/run-state.schema.json` for run-state shape and
   `references/artifact-layout.md` for artifact paths, write ownership,
   precedence, and every shape the graph pins once.
4. `references/measurement-pitfalls.md`,
   `references/toolchain-evidence-pitfalls.md`,
   `references/patch-mechanism-inventory.md`,
   `references/collision-ranking.md`,
   `references/hardware-cost-reasoning.md` for domain knowledge.
5. This file and the native role contracts in `codex/agents/*.toml`.

**Graph versus run state.** The graph is the rulebook: which steps exist, which
results each step may report, which step follows each result, and what each check
tests. `run-state.json` is the bookmark: where this run is, what the user approved,
what is closed, what is open. If they disagree, the bookmark says where you are and
the rulebook says which moves are legal. A position the rulebook has no step for is
a bug to fix, never permission to invent a path.

Never change what the graph means during a run. Change it only through a new
revision (rule 7). When something happens that no listed result covers, run
"Default recovery" first, then the revision rules. If the same uncovered thing
happens again, record it as a finding for the next checkpoint. Do not start
handling it yourself as a new rule.

## Roles and dispatch

| Node group | Agent type | Effort |
|---|---|---|
| verify_run_preconditions, trace_target_delta, cost_routes_and_rank_backlog, design_campaign (the screen step only) | `vllm-neuron-parity:investigator` | high; medium at verify_run_preconditions |
| design_campaign (draft, register, and assemble steps, and their anchor runs), scope_next_increment (judgment rounds only — see the lead row), realize_increment (serial-call items; a glue set on the retained doer thread), prepare_host (full, recovery, and probe-only modes), execute_attempt_loop, prepare_pr, close_campaign (execution half) | `vllm-neuron-parity:implementer` | high; **xhigh at execute_attempt_loop** |
| realize_increment under the kernel call (two or more kernel-class increments pending; stage 6b) | `vllm-neuron-parity:implementer`, one seat per pull-list item, as many at once as the queue tool's idle `kernel_alone` capacity; the retained kernel reviewers per the reviewer row | high |
| calibrate_hardware (when no record exists for the pin and hardware class), measure_candidate | `vllm-neuron-parity:measurer` | medium |
| adjudicate_results | `vllm-neuron-parity:adjudicator` | high |
| assemble_kickoff_contracts (the review step, with two or more requested targets), review_campaign_design, realize_increment (the retained kernel reviewers, one per declared review capacity, for every kernel-class item), review_pr_evidence (the whole-campaign review) | `vllm-neuron-parity:adversarial-reviewer` | high |
| rederive_approach | `vllm-neuron-parity:rederiver` — NEVER `investigator` | **xhigh** |
| assemble_kickoff_contracts (the contracts step and gate 1; the review step with one requested target, where the outcome row cites the costing record), calibrate_hardware (when a record for the identical pin and hardware class exists), verify_run_closure, close_campaign (the gate half only — the implementer executes the approved closure), all checks, all state writes, every lead-mechanical round the graph declares (scope_next_increment by default, including the kernel call's pull list and the changeset assembly on `plan_satisfied`; design_campaign re-entries on a verified block diff; the digest-equality check that starts a measure_candidate re-entry at its runs step), bookkeeping edits at gate 2 | you (the lead) | — |

**Briefing seats.** Every brief you send starts with two lines: the
absolute plugin root and the absolute revision folder. Then it names the
node, the campaign, the graph revision, and the node's `forbidden_effects`
copied from the graph. Hooks block P1-P3 for seats and the write, retry-copy,
audit, and graph guards for you; every other prohibition reaches a seat
through your brief, and a seat that never read this file
is exactly the actor a prohibition has to survive. A brief quotes, never
invents: copy outcome names from the node's outcome list, name artifacts
by evidence key or a path you resolved on disk this turn, and point at the
file that holds a count instead of retyping it.

**Seat lifecycle.** A doer seat retires only on the review outcome that
passes its work: a stopped seat is unreachable, and a repair round needs
the context that did the work. A reviewer gets one frozen stamp, sent
after every constraint has reached the producer; a later change is the
next revision with a fresh stamp, never a re-stamp. A record another seat
is writing is read after its mtime settles, because a mid-write read
judges half a record. When a seat retires, stop it in the same turn as
the state write, then confirm only live seats remain.

**Unavailable agent type.** If any `vllm-neuron-parity:*` agent type is
unavailable, pause the run and report it. Never substitute an ordinary worker for
a role seat.

**Bundled Neuron skills.** Fifteen Neuron skills ship under `skills/` beside
this one. A seat loads each by its plugin address,
`vllm-neuron-parity:<name>`, never a user-level copy of the same name: the
run-start presence check (stage 1) cannot see a user-level copy. The stage
map names each by a short name:

| Short name | Skill |
|---|---|
| planner | `neuron-parallelism-planner` |
| nki-writing | `neuron-nki-writing` |
| nki-docs | `neuron-nki-docs` |
| nki-debugging | `neuron-nki-debugging` |
| compile-debug | `experimental-neuron-autoport-compiler-debugging-vllm-neuron` |
| autoport | `neuron-framework-autoport-vllm-neuron` — reference only: a competing tool, never the port procedure |
| equivalence | `neuron-framework-equivalence` |
| benchmark | `experimental-neuron-framework-benchmark-vllm` |
| troubleshooting | `experimental-neuron-infra-runtime-troubleshooting` |
| compiler-artifacts | `experimental-neuron-perf-compiler-artifact-debugging` |
| nki-profiling | `neuron-nki-profiling` |
| nki-profile-query | `neuron-nki-profile-querying` |
| serve-profiling | `neuron-framework-profiling-vllm-neuron` |
| profile-analysis | `neuron-framework-profile-analysis-vllm-neuron` |
| optimization | `experimental-neuron-framework-optimization` — its lessons files by path only (below); its SKILL.md is never loaded |

A brief names the skill and the sections the stage map gives for the node.
Device work under any bundled skill runs on the cores and pools its job
lease names, and a troubleshooting device command runs only under a lease.
The benchmark skill serves already-standing hosts only: its provisioning
sections A1-A4 and its submission section B1 never apply (P6). The
equivalence skill runs every stage with `--target-stack vllm_neuron`. A
profiling skill writes under the project root the seat names: pass the
node's artifact directory as `--project-root` or `NEURON_PROJECT_ROOT`. The
optimization skill is read by file path under
`<plugin-root>/skills/experimental-neuron-framework-optimization/references/`,
never by plugin address: a skill loads whole, and its SKILL.md is a
competing campaign controller.

## Lead routing

The pinned graph is the live graph at the newest revision of the revision
folder (`<revision-folder>/workflow.pave.yaml`, revision pinned in run
state). Its node, edge, and check counts come from
`scripts/validate_pave.py`, never from this prose. It carries **3 user
gates**: gate 1 (campaign selection, at `assemble_kickoff_contracts`),
gate 2 (design approval, the `design_approved_by_user` check on
`review_campaign_design`'s `design_sound` edge), and gate 3 (close-out,
at `close_campaign`).

Routing discipline, on every transition:

1. Read the current node in the pinned `<revision-folder>/workflow.pave.yaml`
   before you route.
2. Take the outcome the seat's result actually satisfies — **emit only declared
   outcomes**. There is no "other".
3. Traverse only a declared edge from that outcome, and evaluate every check the
   edge carries. A failing check takes its `on_failure_route`, not your judgment.
4. Confirm the outcome's `required_evidence` exists at its declared artifact
   path (`references/artifact-layout.md` §1 and §2) before you record it.
5. Checkpoint run state after every consequential transition: the declared
   outcome, the declared edge, the evidence paths, and the verbatim user
   decision at any gate.

Stage map — node ids, in run order; the outcome-to-edge table is the graph, not
this file:

| Stage | Nodes | Bundled skills | Notes for you |
|---|---|---|---|
| 1 Intake | `verify_run_preconditions` | troubleshooting: Runtime info | Run `python3 <plugin-root>/scripts/check_bundled_skills.py` first: a missing or changed bundled skill file is `inputs_missing`, which you confirm from its output (the remedy is a plugin reinstall, the user's) — except under the autoport skill, a competing tool whose absence is never `inputs_missing`: record that miss and continue. Freeze the run inputs (base version, release branch, SDK and compiler versions) into `pinned_release`, and record per requested target the served shapes its criteria are stated at (context lengths and concurrency levels). Load the four cross-run artifacts per `references/artifact-layout.md` §1; an absent fingerprint file is a legitimate empty set. `preflight_record_complete` is yours: one transcript per precondition. |
| 1b Calibration | `calibrate_hardware` | nki-writing Quick Start, Hardware Constraints Quick Reference (the suite's kernels are the measurer's until the release layer ships the suite script); nki-debugging Error Resolution; nki-docs: architecture and optimization guides; nki-profiling Complete Profiling Workflow; nki-profile-query Step-by-Step Workflow | The first device act of the run, before any route is costed. When a record for the identical pin and hardware class exists (this run's, or a prior run's at `artifacts/run/calibration/`), verify the pin equality and record `calibration_recorded` with no seat and no device. Otherwise the measurer runs the fixed suite under a `kernel_alone` job lease on one roster host of each hardware class a requested target uses, with no campaign lease. Every row names its source (suite, anchor, or served profile). `calibration_unproducible` pauses the run: hardware and SDK remedies are the user's. |
| 2 Delta scan and costing | `trace_target_delta` (per requested target), `cost_routes_and_rank_backlog` | trace: nki-docs NKI Library kernel reference; autoport, reference only: Step 1 Architecture Research, Search Before Implementing. Costing: planner Quick start, run-spec, Validate the engine against the oracle first, Reading the output, and its TP feasibility reference | Re-trace grants are counted from grant files under the target's directory, never a stored integer. For a model port the trace decomposes the reference's forward pass into an op inventory with its kernel-gap list; `target_traced` needs it. Costing runs the parallelism planner per target at the served shapes and records the entitlement every performance criterion is stated against; each route carries the roofline ratio its hot ops' cost rows predict, citing calibration rows and naming the ops that need an anchor at design. Brief the scan and costing seats with `references/collision-ranking.md` — the ranked file surfaces where ports collide are what makes a route expensive. The per-target delta report and the costing report are living documents under `references/artifact-layout.md` §4.12; a re-entry brief into either carries the report's size line from `scripts/measure_artifact.py`. |
| 3 Gate 1 | `assemble_kickoff_contracts` | reviewer: planner methodology reference | Two steps, review then contracts. With two or more requested targets a reviewer seat, independent of the costing seat and retained across the re-costing rounds, reviews the route verdicts first; its `material_findings` re-costs before any election. With one requested target there is no ranking to review: record the review as satisfied with no seat, the outcome row citing the costing record, and the user's election is the review. Then yours: present the reviewed backlog, take the user's campaign selection, record the decision VERBATIM, and refuse to start any campaign whose kickoff contract is incomplete. The correctness gate is a served-output comparison against the reference with its pass rule; for a model port it is a ladder read in order — coherent served generation on the reference's prompt set (token identity is never a rung), a declared benchmark subset within its bound, then the full declared benchmarks within theirs, each bound stating how a score inside the reference's own noise reads, set by the user. Every intermediate-tensor tolerance is a diagnostic that selects no outcome. The performance gate covers every served phase at the declared shapes: the targeted phase as a ratio to the planner's entitlement with the bar (1.5 unless the contract names another with its reason) or a written hardware reason, every other phase as no regression within a stated bound. With two or more approved campaigns, derive `scheduling_holds` before instances dispatch (`scheduling_holds_recorded`): overlapping predicted file surfaces serialize, and an upgrade-route campaign holds all others while it runs exclusively. With one approved campaign the field is empty and the check passes. |
| 4 Campaign design (per approved campaign) | `design_campaign` | screen: nki-docs library reference. Draft: nki-writing Complexity Assessment, Translation Workflow, Hardware Constraints Quick Reference; planner cost-model reference. Register: equivalence R-Ratio, Verdict, and its version pin; benchmark What this measures and its metrics reference; serve-profiling Method 1 (register the profiler launch config the serving launch carries, and whether the profiled launch is its own arm or the one the performance arms run on: its compile-time debug env changes the programs' digest); profile-analysis Report Template, Performance Bounds (per NEFF). Re-entry on `performance_shortfall`: by path, `OPTIMIZATION_BOOK.md` §5 and `KERNEL_OPTIMIZATION_LESSONS.md` Part G as a lever catalog | One node, four steps: screen the pin, draft the plan (with the regression matrix on an upgrade route), register the acceptance criteria, assemble the record. The investigator owns the screen step; the implementer owns the rest; a large campaign runs the steps as sequential seats. The screen runs once: the pin is frozen at intake and a re-entry never re-screens. Before any code the record carries, per served phase, an execution map (each block's ops in order with their shapes at the declared served shapes) and a cost table with one row per hot op on the map, whatever its substrate, each rate cited to a calibration row whose lowering and access pattern match the op's; a rate with no matching row is anchored first by a `kernel_alone` run under a job lease, its row appended to the calibration record. Each kernel-class increment carries a numerics plan (correctness class, fp64 oracle over the registered regimes, dtype per stage, NaN containment note, the two refused forms). Tests are load-bearing only. Superseded round artifacts are deleted from the read path - history is the record's revision log plus run state. Brief the design seats with `references/patch-mechanism-inventory.md` — the route a design picks has to be a mechanism the plugin actually has. Brief the draft seat with `references/hardware-cost-reasoning.md` §1, §2 and §7 for the cost table and §6.2 for the numerics plans — a row that counts bytes or FLOPs when a fixed cost per item or a serial chain binds designs the wrong kernel; the screen seat reads its §5 for untried levers. `pin_infeasibility_socratic_guard` is evaluated by you on the infeasibility exit and never by the note's author. `comparators_preregistered` is a timestamp comparison against the registration record you commit on the `design_ready` edge. Re-entry check tool: when the pin compares equal to its source with the standing note cited, the registration is byte-unchanged with no finding naming a registered value, and the only change is a block diff you have read, you perform the delta with NO seat; a registered-value touch, a screen-fact finding, a self-check gap, or ambiguity runs the seat. A re-entry brief names the blocks to touch and the finding, contradiction, adjudicated per-op cost miss, or user decision each answers, and carries the plan's size line from `scripts/measure_artifact.py`. An over-cap plan (`references/artifact-layout.md` §4.12) gets a trim round before new content. |
| 5 Gate 2 | `review_campaign_design` | reviewer: nki-docs library reference and architecture; planner cost-model reference | Reviewer seat first, then you present the reviewed design and record the user decision verbatim - or, on a re-entry where `design_approved_by_user` says the recorded approval stands, apply it and record that basis. A whole-plan design entry opens a fresh challenger, retained across that entry's rounds; after a block-scoped repair it reads the touched blocks and their cascade while you read the diff to confirm nothing else moved; a block-scoped re-entry that names kernel-class ops (a served shortfall, a contradicted row) goes to the retained kernel reviewer that read the op's kernel, or any retained kernel reviewer when none did. Material means the consuming nodes would build the wrong thing or a frozen or registered value would move — the execution map, cost table, and numerics plans included (a served phase with no map, a hot op with no row, a rate cited to no matching row, a ratio over the bar whose reason cites no floor row, a missing regime or dtype table or NaN note, a refused form, a predicted sum that misses with no reason the user can weigh). The user's acceptance of a predicted miss is a criterion change in the user's own words, re-registered at design. Every other finding is bookkeeping you apply as a direct edit (one revision-log line; run state points at that line) before any repair seat is briefed. Any change to kickoff-declared criteria needs its own explicit recorded user decision. Narrow triage: when every standing material finding names only increment-plan text, design-record, or registration surfaces, evaluate `narrow_delta_scoped` yourself and take the narrow repair edge back into `design_campaign` - block-scoped repair, continued doer thread, re-enter this gate; recurrence of a finding fingerprint or any ambiguity fails closed into the full round. `design_loop_within_bound` rides every repair edge: gate 2 closes in fewer than four review rounds, and when the bound trips you re-enter with no reviewer seat and present the standing findings to the user with a close-anyway recommendation - the verbatim decision selects the outcome, and a P9 finding is never disposed that way. |
| 6 Implementation (cost before code; device from the first kernel) | `scope_next_increment`, `realize_increment` | kernel-class realizer: nki-writing whole, nki-docs, nki-debugging Standard Debugging Workflow and Error Resolution, compile-debug cardinal rule and Attack order, nki-profiling Complete Profiling Workflow, nki-profile-query Step-by-Step Workflow and Profile Analysis (the kernel-alone ratio is a profile read), and by path `KERNEL_OPTIMIZATION_LESSONS.md` Parts D and E. Glue realizer: compile-debug cardinal rule and Attack order only. Kernel reviewer: nki-writing language-constraint reference and Coding Conventions; nki-profile-query Profile Analysis. Scoping: none | No serving attempt before this stage closes; a localization item's diagnostic run on the served device is `realize_increment`'s. A glue increment passes on its CPU-mode command. A kernel-class increment builds its fp64 oracle and regime inputs first, then the kernel to the costed form, and passes on the oracle match in its declared class over every registered regime plus the ratio measured kernel-alone under a `kernel_alone` job lease, read against its row's roofline, within the bar or the design's written reason; a retained kernel reviewer (independent of the realizer, one per declared review capacity of items in flight, retained across its own items' rounds) then reads the kernel diff beside the oracle, ratio, and cost-row records, and the realizer answers its findings directly with no word of yours between them. A ratio over the bar where the row predicted inside it is `evidence_contradicts_design`. Glue gets no per-item review. Brief a kernel-class realizer with `references/hardware-cost-reasoning.md` §3 and §6.1-§6.3 before its build and its kernel-alone ratio run — the in-graph compile checks, the A/A floor and the device execution of record decide that ratio. A changeset the campaign did not design (the user's own branch or pull request against the campaign branch) lands only as a fold item on the user's recorded word: realize it (rebase onto the tip to the exact union, whole-tree CPU-mode pass, commits landed as written); its review is the whole-campaign review — no reviewer before the fold, no seat at `prepare_pr`; the merge stays the user's. `scope_next_increment` is yours by default: decide the round from the persisted inputs with a round record that carries the commands and outputs it derived from, and dispatch the implementer only for a judgment the rule does not decide (a contradiction candidate no realizer record holds, or a findings-history versus lap-record disagreement). When two or more kernel-class increments are pending, the round is the kernel call (stage 6b). Otherwise the serial call selects one complex item or up to three low-complexity items, a glue set on the retained doer thread in recorded order by default. A landed item's plan block collapses to its plan row. Once every planned and repair item has passed and the branch diff's digest differs from the newest maintainer-pass record's, mint a maintainer-pass item: the first pass reads the clean diff, every later one the delta since the newest pass record's tip, with no behavior change. On `plan_satisfied` you assemble the changeset yourself with no seat, six components: branch diff, evidence index (the CPU-mode record for glue; the oracle, ratio, and kernel review records for kernel-class), the P4 import scan over added/modified lines, P13's substrate-fidelity scan, the process-vocabulary scan over added lines and added paths (`scripts/process_vocabulary_scan.py`, zero hits; class (f)), and the whole-tree run record of the declared test paths on the tip (zero collection errors, zero new failures against the target base). `changeset_complete` fails any gap back into scoping as a coverage-gap item that names its class; when it holds, `plan_satisfied` routes to `prepare_host` — the kernel reviews are the code review before hardware, and the whole-campaign read comes at stage 10. This stage re-enters on a product-source defect from the attempt loop (`product_defect_found`), a whole-campaign `code_findings`, a stale tip at the close-out check, a correctness shortfall, or a regression. |
| 6b Kernel call | `scope_next_increment` (the pull list), `realize_increment` (one seat per item) | as stage 6: realizer and kernel reviewer | The default round whenever two or more kernel-class increments are pending: a port's kernels are its critical path, and each item is one op with its own oracle, lease, and review that shares only the branch. Yours: every pending kernel-class increment with a distinct op and a pairwise-disjoint file-and-method surface goes on one pull list in order, with the review capacity (items in flight per retained kernel reviewer, a run-setup value) in the round row. As many items as the queue tool's idle `kernel_alone` capacity allows run at once, one implementer seat per item in its own checkout, and a seat that reaches its verdict takes the next item with no scoping round between. Open one retained kernel reviewer per review capacity of items in flight, so the review queue never outgrows the hands that build. When the idle capacity is one, narrow to the serial hand. A queued kernel lease waits until its grant request's wall-clock cap, then the item records `increment_stuck`. |
| 7 Hardware bring-up | `prepare_host`, `execute_attempt_loop` | host: troubleshooting Device health, Process debugging, Device not found, Runtime info. Attempts: compile-debug cardinal rule, Attack order, Two localization techniques; compiler-artifacts where compile-debug links it; troubleshooting Compilation failures, Distributed training hangs; serve-profiling Traps & recovery, Monitoring and hangs. The serving launch carries the profiler config the design registered | A lease reserves named pools (Neuron devices, compile memory, CPU cores, the compile-cache write slot) from the roster you froze at intake, never the whole host; the hardware queue tool (`scripts/hardware_queue.py`) is the only writer of lease records and the seat calls it directly - you write no lease record and arbitrate no grant. A job that takes no pool holds no job lease; a job that needs a pool the roster leaves unsized reserves the host whole; a `kernel_alone` job (a calibration row, a design anchor, an increment's ratio run) holds a job lease on any roster host of its class and no campaign lease. `prepare_host` has three modes: full (lease, then venv) from `plan_satisfied` or after a host is given up; recovery, from the attempt loop's `host_faulted` or a host-level build failure inside the node — the allowance first (one successful recovery per host per campaign), one recovery per host at a time (other campaigns release their job leases and take the owner's result), identity re-verified before any state-changing action, then drain, service restart, reboot, and the new boot identifier recorded through the queue tool before the host counts as restored; and probe-only after a restored host - never re-request a lease the campaign holds. Before any serving grant the tip passes the uncharged tier-0 bring-up ladder once (the rungs are listed at `execute_attempt_loop`: test paths collect and pass, graphs extract device-free, one rank loads, host footprint times world fits, one CPU-mode request passes), each rung recorded in the attempt log; the queue tool grants a serving-class job lease only with the job's class, its tip, its named client leg and that record. P8: no identical hardware retry — the tier-1 gate reads the repo fingerprint file against this run's attempt log. Attempts are counted from attempt-record files by their class field — only a serving attempt counts; host faults are recorded, never charged, except an attempt whose own evidence names use beyond its job lease. An attempt record keeps its measurement outcome (served or failed, and what the request path returned) apart from hygiene findings on the host after it; a leftover process never sets the outcome. The tool refuses a second campaign lease for a campaign that already holds one on the host. The stop limit routes out to `rederive_approach`. Brief every attempt and triage seat with `references/toolchain-evidence-pitfalls.md` — a late watchdog names the stage where it ran and not the stage that failed. |
| 8 Measurement | `measure_candidate` | equivalence: Stage 1 smoke, Stages 5+6 teacher-forced E2E, version pin. Benchmark: What this measures, C Comparing Neuron vs GPU, Common failures, metrics reference. Served profile: serve-profiling Method 1, Data parallelism and the capture (profile ONE replica); profile-analysis Verification discipline, Step 1 Ingest, Profile Analysis | One measurer seat, three steps in order: procedures (each smoke-verified and shown failing on its registered known-bad input), the read-only baseline capture with its skew record or the justified skip, then the runs. A procedure or reference defect returns to the step that owns it inside the node, within the shared repair budget (`references/artifact-layout.md` §4.4). On a re-entry whose registration, smoke records, and baseline captures are digest-equal to the prior entry's, record the equality in the brief and the outcome row: the seat starts at the runs. Brief every measurer seat with `references/measurement-pitfalls.md` — its traps are known and non-obvious, and a number produced through one of them is worse than no number. Brief the measurer seat that reads served profiles with `references/hardware-cost-reasoning.md` §2.4, §4.1 and §6.5 — the cap row it writes, the profile funnel and its traps, and the carry. A model port's correctness ladder runs in registered order and stops at the first failed rung; each later rung and each performance procedure gets its own bundle with the record "not run after rung n failed". The served profile writes its utilization cap per engine as one row file per profile under the run's calibration directory, and the measurer re-rates the design's predicted rows under it into the bundle. A no-regression criterion for an untargeted phase is decided by compiled-program digest equality when the base's programs are on disk. Check-tool liveness is graph-carried: `procedures_smoke_verified` (on the exit edge into adjudication) reads the known-bad input result out of the smoke record and checks every smoke record precedes every measured run citing its revision, and `acceptance_threshold_evaluated` reads each bundle's evaluated-threshold record plus the smoke negative control (`references/artifact-layout.md` §4.5 registers the pair, §4.6 shapes the record) — an exit status is not an evaluation. GPU baseline is READ-ONLY (P5): no autonomous reboot or reset; capture refuses on a kickoff-record contradiction. `procedures_smoke_verified` and `revision_stamped` (P11: a git-issued identifier at measurement time, never a branch name) are yours. No reviewer seat stands ahead of a declared mechanical gate — a launcher's pre-flight or a procedure's smoke-and-control run decides, and a re-check whose answer is knowable from disk gets no seat. |
| 9 Adjudication | `adjudicate_results` | none | `measurer_not_adjudicator` is yours and is hard: no agent that acted at any step of `measure_candidate` judges its numbers. The adjudicator reads the evaluated-threshold records the bundles carry, never a first-sighting signal (`references/measurement-pitfalls.md` keeps the stable-read trap as a note), and names the consequence itself: passed; correctness shortfall, naming the first failed rung; performance shortfall, when an attributed component is over its re-rated row beyond the registered tolerance or attributed time no row predicts is above it, and the design re-enters block-scoped for those rows; no benefit, when every attributed component sits inside its row and the sum still misses; regression, for an upgrade route's matrix or an untargeted served phase beyond its bound, outranking both cost-model outcomes. No second review of the verdict - the PR review reads it. A verdict's prose is corrected only by an erratum the adjudicator appends, naming the line and the evidence; a value or the consequence never moves by erratum. |
| 10 PR description and whole-campaign review | `prepare_pr`, `review_pr_evidence` | PR: benchmark C Comparing Neuron vs GPU. Reviewer: the same, and nki-writing language-constraint reference | `prepare_pr` writes one document - the PR description in the section form `pr_evidence_package` declares (`references/artifact-layout.md` §4.2 pair 3 lists the sections), every number read from a registered bundle and paired with its comparator at a named commit or version, every limit written where the claim stops; no evidence index, no separate evidence package. `review_pr_evidence` is the one whole-campaign review: one seat, independent of every realizer, reads the clean diff with the description. Its `code_findings` re-enters scoping and the repaired tip serves and measures again; its `material_findings` sends the description back to `prepare_pr`. PRs go to the jinhuang12/vllm-neuron fork only. Merge is the human's; you hold no merge authority (P7). |
| 11 Gate 3 and closure | `close_campaign`, `verify_run_closure` | none | Yours plus the implementer. `impl_commit_is_reviewed` rides the edge into gate 3: the tip the closure will push equals the tip the whole-campaign review's findings name, and every adjudicated bundle's measured revision equals it or differs only by commits with no product source; a failure re-enters scoping. Present the closure candidate and its evidence and record the verbatim decision; the implementer executes the one approved closure, because executing is a role's work and not yours; then apply the serialized single-writer updates to the scorecard, backlog, debt list, and fingerprint file. The debt list takes every hazard the campaign measured as a release item: a rate that needed an anchor, a regime that caught a defect, a form the device contradicted, a per-op cost miss. `verify_run_closure` is yours with no seat: record the `gh pr view` transcript that resolves the PR number ON THE FORK, and the commit hash of the artifact update whose diff touches all four cross-run files. |
| — Recovery | `rederive_approach` | the trigger picks: a tripped attempt breaker, compile-debug Attack order; a cost-model miss, planner methodology and cost-model references, nki-docs architecture and optimization, compiler-artifacts Decision tree (a valid graph that runs but misses its row), profile-analysis Known dead-ends and Collective-bound workloads, and by path `KERNEL_OPTIMIZATION_LESSONS.md` Part G and `FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md` Part H as lever catalogs | The catch-all node for every stop limit and exhaustion route. It re-enters design through `revised_approach` or recommends close-out. It never implements. |

Fan-out and join:

- `trace_target_delta` per `requested_targets`; `cost_routes_and_rank_backlog`'s
  `reports_insufficient` re-fans per DERIVED `deficient_targets` (derived from the
  run-level delta index, never stored).
- Every campaign-scoped node runs per `approved_campaigns`.
- `verify_run_closure`'s `closure_unverified` re-fans per DERIVED
  `discrepant_campaigns` (derived from the current closure verdict, never stored).
- `await_remaining_closures` is a JOIN: hold until every approved campaign
  instance reaches its verified closure, and re-run run-closure verification at
  each closure.

Endpoints: `run_complete` (accepted), `run_paused` (resumable), `run_blocked`
(no declared route remains), `run_aborted` (the user stops the run; the verbatim reason is preserved,
never paraphrased), and the `await_remaining_closures` join.

Derived, never stored: `deficient_targets`, `discrepant_campaigns`, and every
budget, allowance, and re-trace grant count. Recompute them from the event files
each time. A stored counter is a bug.

## Run state and resume

Run state lives in one JSON file per run — `run-state.json` in the run's
`artifacts/run/` directory — and its shape authority is
`schemas/run-state.schema.json`. Do not restate the field list anywhere; read the
schema. Validate with `scripts/validate_run_state.py` on every write.

**You are the single writer** of run state and of the four cross-run artifacts
(scorecard, backlog, debt list, failure fingerprints) (P10). Lease records have
their own single writer, the hardware queue tool, which seats call directly. Write ownership for every other path is in
`references/artifact-layout.md` §2. Cross-run artifact writes are serialized at
campaign closure and never happen on a campaign branch.

**Run marker.** At run start, write `.vllm-neuron-parity-run` at the project
root: one line, the absolute path of this run's `run-state.json`. Beside the
state file write `<run-state-path>.lead-session`: one line, this session's id.
Together they make the lead hooks fire for this session only. At a terminal
close, set `final_status` and remove the marker. If you walk away from a run,
set the final status or remove the state: a walked-away run must never stay
"active" forever.

**The checkpoint is not yours to write.** The audit-checkpoint sidecar, the
write log beside run state, and `<revision-folder>/audit-findings.md` are
hook- and seat-owned; the router denies you a write to them. An AUDIT DUE
block from the stop guard is the brief: forward it verbatim to
`pave-init:workflow-updater` in audit mode, add nothing you compose, and
apply its proposal with the exact `apply` command the block prints (both
hook-record flags). Without them the entry records `written_by: unverified`
and the checkpoint never closes. Audit mode needs pave-init 2.6.2 or later;
the guard says so and stays silent when it finds none. When the
update-reviewer PASSes a findings record, you may delete a memory entry that
record names as a cause.

**Write for the reader, and keep the caps.** Two duties, pinned once at
`references/artifact-layout.md` §4.13 (plain writing) and §4.12 (living-
document cap). Read both there and carry both into every brief. The
write-for-reader hook re-presents them on document writes; the adversarial
reviewer reports each living document's size every round and treats a
reader-facing artifact that fails either duty as a material finding.

**Resume is reconciliation, not replay.** On resume, in order:

1. Re-read `run-state.json` and the pinned revision and bundle digest recorded in
   it. Re-read the `revisions.yaml` head and run `scripts/record_revision.py
   verify <revision-folder> --pinned-revision N --pinned-digest D`: exit 0
   continues; exit 3 or 4 routes per revision rules 7 and 9. An
   apply step mid-run does not move a running instance by itself.
2. Re-read the pinned `<revision-folder>/workflow.pave.yaml` — the live graph at
   the newest revision; the routing table you need is there, not in your context.
3. Check state against the artifacts actually on disk at their declared paths
   (`references/artifact-layout.md` §1 and §3). Recompute every derived count
   from the event files.
4. Continue from the last SATISFIED gate. An outcome whose required evidence is
   missing on disk is not satisfied, whatever state says.
5. Reconcile any disagreement before you route, and record the reconciliation.
6. Re-dispatch seats for the node instances that are genuinely still open. A
   custom-agent thread from a previous session is gone; a node instance is not.

## Default recovery

For a failure that no declared outcome covers, run this loop before you do
anything else. When a seat's own test, control,
launcher, or checker fails, the seat repairs it without your word and reports
diagnosis and fix together. The loop below is for the run's work, not for a
seat's check tool.

The loop:

1. Retry once when the failure looks transient. A failed retry is a real failure.
2. Investigate to root cause and persist the record. Open on cheap priors
   (docs, release notes, issue trackers, the failure text in public sources);
   priors direct the first measurement, never decide acceptance.
3. Match process weight to the finding: one obvious fix, apply and verify; a
   fix that needs design or has several candidates, plan and review first;
   many competing fixes or a cause that resists localization, investigate in
   parallel and select through independent challenge, correctness before
   simplicity.
4. Re-prove by the world: the evidence that failed must now pass.
5. Two honest early exits: replan when the root cause is the plan itself;
   pause or blocked only when investigation itself is blocked.

Recovery work stays inside the failing node's meaning — it never invents
outcomes or edges.

Note what this does NOT authorize: no invented outcome, no invented edge, no
widened effect. Declared routes come first — evidence that fits a declared
`scope_exceeded`, `no_new_route`, `plan_unrealizable_as_designed`, or stop limit
outcome takes that route, and recovery machinery is not a bypass for a route the
graph already has.

## Revision rules

This workflow runs more than once, so it keeps a revision record: one live
`workflow.pave.yaml` at `<project-root>/.vllm-neuron-parity/evolution`, one
append-only `revisions.yaml`, and one `history/v<N>.patch` per apply step,
written only by `scripts/record_revision.py`. The authority is pave-init's
`references/pave-revisions.md`; this section keeps the rules the lead runs at
run time, one clause each, keeping the authority's numbers. The pave-evolve seats (`pave-init:workflow-updater`
and `pave-init:update-reviewer`) draft and review every successor, so a
successor needs pave-init 2.6.2 or later installed. The lead never drafts its
own successor and never edits the live graph outside an apply step.

1. **Pin and verify (rule 1).** Record the revision number and bundle digest
   in run state at start and run `scripts/record_revision.py verify
   <revision-folder> --pinned-revision N --pinned-digest D` at start and at
   every resume: exit 0 continues, exit 3 (graph applied since pin) routes to
   rule 7, exit 4 (run setup applied since pin) routes to rule 9. An apply step
   mid-run does not move a running instance by itself.
2. **Declared routes first (rule 2).** Evidence that fits a declared
   `scope_exceeded` or `plan_unrealizable_as_designed` outcome takes that
   route, never the revision path.
3. **Block honestly (rule 3).** When no declared outcome fits, or `verify`
   finds an unrecorded edit: pause or block the run, record the discovery with
   its source in run state plus one section of the standing review record,
   and hand it to the seats. Never make `verify` pass by re-pinning.
4. **Successor proposal (rule 4).** The workflow-updater drafts it from the live
   graph at the head and replans the narrowest affected boundary. Batch it: one
   proposal per pause or audit checkpoint, covering every defect and every
   recurring form recorded since the last apply step, never one per finding.
6. **User-only changes (rule 6).** `approval: when_needed`. You apply a proposal
   yourself, on the update-reviewer's PASS, when it adds no gate, changes no
   outcome or edge, and changes no declared meaning. It is recorded as
   `user_only_changes: none`. Anything else (a new gate; a changed outcome,
   edge, or declared meaning; any other user-only change) goes to the user as one
   batch per checkpoint. It applies only with the user's approval, verbatim in
   the entry's `approval`, recorded as `approved`. Until then the
   updater marks it `pending`, and `apply` refuses it.
7. **Continue on the successor (rule 7).** Once the update-reviewer passes the
   successor and the apply step is verified, re-pin the run to the new revision
   and bundle digest in run state, record the approval verbatim, and resume
   from the last SATISFIED gate - landed work stays landed, a gate the
   successor adds is a catch-up duty per landed item, and the run closes into
   a linked successor run only when a recorded outcome's meaning changed. A
   user who declines the move is recorded verbatim in
   `workflow_identity.move_declined`; the run stays pinned and you do not
   re-ask while that stands.
8. **Usage log (rule 8).** At each terminal close, derive the usage record
   from the run's event history and append one section to the standing usage log
   at `artifacts/run/usage-log.md`, never a new file per run. The updater
   reads it in audit mode. It blocks a stop, never a traversal.
9. **Run-setup revisions (rule 9).** Seat, model, effort, or check tool changes
   at any entry are applied as `kind: run_setup` entries in the same revision log with a
   user-approved user-only-changes check; a run pinned to the older entry re-pins at
   its next resume. A run-setup value that lives outside the graph YAML (model or
   effort in `agents/*.md`) is recorded by the plugin release in `VERSION`
   until `record_revision.py` accepts a preamble-only run-setup proposal.

**First run: install and pin (rule 1, mechanically).**

```bash
python3 <plugin-root>/scripts/record_revision.py install <revision-folder> --from <plugin-root>
python3 <plugin-root>/scripts/record_revision.py verify <revision-folder>
python3 <plugin-root>/scripts/record_revision.py pin <revision-folder> --run-id <run id>
```

Keep the revision folder in version control so an apply step's `commit` is an
identifier the lead cannot mint.

## Enforcement

Run-wide prohibitions. The graph carries each node's `forbidden_effects`; the
enforcement levels below are the run-wide layer on top of it, weakest to strongest: prose <
reminder < reviewed/socratic < mechanical < blocking hook.

| # | Prohibition | Level and where it lives |
|---|---|---|
| P1 | Never mutate protected base branches (release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline) on the fork or upstream | BLOCKING `hooks/protected-branch-guard.sh` |
| P2 | Never clear a shared Neuron compile cache — the prohibition is the class, and four roots are in it. The three vLLM compile-cache roots ($VLLM_CACHE_ROOT/neuron/compile_cache, ~/.cache/vllm/neuron/compile_cache, /var/tmp/neuron-compile-cache) and the kernel intermediate cache (/var/tmp/nki-intermediate-cache — the kernel toolchain writes it outside every cache root a run can set, and it can hold a co-tenant's kernel artifacts). Instances and the rename-aside duty: `references/artifact-layout.md` §4.10 | BLOCKING `hooks/compile-cache-guard.sh` + delegate guardrail wrapper (a documented remedy that says "clear the cache" is intercepted, never followed). The guard refuses every destructive verb on all four roots, `mv` included — it cannot tell a root you own from a shared one, and rename-aside is sanctioned only inside one you own (`references/artifact-layout.md` §4.10) |
| P3 | No `cp -a` venv cloning; no pip writes into /opt | BLOCKING `hooks/venv-opt-guard.sh` |
| P4 | Zero `neuronx_distributed*` (NxDI) imports in ported code | MECHANICAL import scan over added/modified lines in your `changeset_complete` check on `plan_satisfied` |
| P5 | GPU baseline read-only; no autonomous reboot or reset; durable-host-state scoping | Contract text + reminder + mechanical skew and identity probes; `measure_candidate`'s baseline step refuses to capture on a kickoff-record contradiction |
| P6 | The benchmark skill's provisioning STOP gate is never removed | Prose + reminder — the rule rides every hardware brief you send |
| P7 | PRs only to the jinhuang12/vllm-neuron fork; merge stays human; fork sync is the user's | MECHANICAL `verify_run_closure` (lead-only: `gh pr view` resolves the PR number on the fork, and the artifact-update commit is recorded) + gate-3 user approval + no merge authority granted to any seat |
| P8 | Identical hardware retry forbidden | MECHANICAL fingerprint gate (repo fingerprint file vs this run's attempt log) before any attempt |
| P9 | Comparators are never chosen or altered after measurement begins | MECHANICAL `comparators_preregistered` (registration timestamp precedes every measurement artifact) + `measure_candidate`'s `forbidden_effects` + adjudication reads the registration record |
| P10 | One writer per record: the lead for run state and cross-run artifacts, the hardware queue tool for lease records | STRUCTURE (no other role holds those write paths; `scripts/hardware_queue.py` grants under a per-host lock) + `scripts/validate_run_state.py` on every write; derived counters and remaining capacity are counted from event files so no child ever needs a state write |
| P11 | Measured revision is a git-issued identifier at measurement time, never a branch name | MECHANICAL `revision_stamped` on `measure_candidate`'s exit edge into adjudication |
| P12 | Emit only declared outcomes; traverse only declared edges | `scripts/validate_pave.py` + run-state schema validation + the lead hook pair re-presenting your position |
| P13 | New kernel-class functionality the existing Neuron NKI library does not provide is implemented in NKI, never as a torch-level fallback (torch stays legitimate for orchestration and glue) | SPLIT LEVEL. MECHANICAL for declaration presence (`design_campaign`'s completeness self-check requires a substrate declaration per increment — kernel-class or explicitly not) and for fidelity (your `changeset_complete` check: a declared-NKI increment whose diff shows zero NKI usage is an exact contradiction, a coverage-gap hit). REVIEWED for the classification itself at both gate rubrics — an increment recorded non-kernel-class whose work is kernel-class is a material finding |

Costly-transition guards, all blocking routing preconditions you evaluate before
the transition: the measurement repair budget (no backward route at either
tier's threshold), the scan re-trace bound before a grant is issued, and the
hardware stop limit (tenth budget-counted attempt, tier-1 fingerprint, or venv dead
end). Counts come from the event files per `references/artifact-layout.md` §4.

The hooks register in the plugin-level `hooks/hooks.json`; review and trust
them through `/hooks` before a run. They fire only for the marker-and-lead-
session pair above and stay silent elsewhere. Each hook's header comment says
what it denies or re-presents; the three audit-checkpoint hooks have their
table at `references/artifact-layout.md` §4.14. When the hook runtime is
unavailable, record the degradation in run state and carry the duty yourself;
do not proceed as if the guards were still armed:

| Hook | Degrades to |
|---|---|
| P1-P3 guards | contract text in every brief, reviewed at the next gate |
| graph edit guard | `verify` at every resume (rule 1) |
| stop guard, goal restatement | the resume duty above |
| staleness reminder | the checkpoint duty (routing step 5) |
| write-for-reader reminder | the write-for-the-reader duty above |
