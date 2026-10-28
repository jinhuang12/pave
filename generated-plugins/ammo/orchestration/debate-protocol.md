# Stage 3: Candidate Proposal and Adversarial Debate

Read this file when Stage 3 starts. It owns Stage 3 policy and choreography: what a candidate is, what counts as evidence, how the debate runs, and how the lead picks winners. Projection math stays in `references/e2e-delta-math.md`. Artifact paths stay in `references/artifact-layout.md`. State shape stays in `schemas/state.schema.json`.

`{CR}` means `campaign.current_round`.

## Purpose

Stage 2 measured where the time goes. Stage 3 turns that profile into two or three optimization candidates that are worth building. Implementation is the expensive step: a track costs GPU hours and many agent hours, and a candidate that turns out to be infeasible wastes all of it. So Stage 3 does two things. It searches widely, so the winners are diverse and aimed at the largest E2E return the profile supports. And it tests each candidate cheaply before anyone builds it, with bounded experiments and cross-examination by a rival.

The goal is a strong portfolio. A candidate with a large projected gain and thin evidence can win when the record supports the bet. The rules below keep magnitude claims honest and make feasibility doubts explicit, so the lead can price a risky bet instead of rejecting it.

## Roles and flow

```
[Stage 2 profile] --> [2-4 champions propose alone] --> [lead: eligibility, op_ids]
      --> [cross critique] --> [rebuttal] --> [lead: rank, pick 2-3] --> [Stage 4 tracks]
```

The lead orchestrates and judges. Champions propose, run experiments, critique a rival, and defend their own work. Champions work from the same Stage 2 profile (`bottleneck_analysis.md`, with its Workload Dilution and Top Components tables) and do not see each other's drafts before proposals are published, because independent proposals are what make the portfolio diverse. A lens ("look at attention first") may steer where a champion starts. It does not limit what the champion may propose.

## What a candidate is

A candidate authors mechanism logic or host-side structure that changes the forward pass, targets a slice that Stage 2 measured, and projects a production E2E gain with `references/e2e-delta-math.md`.

Configuration is not a candidate. Retuned constants, autotune tables, backend selection, and environment or config flags leave the kernel body and the dispatch logic unchanged, so a win from them is a tuning result the campaign cannot ship as authored work. Hand-written logic that reaches the same effect (for example an explicit software pipeline in the kernel body) is a candidate.

A candidate is **lossy** when it introduces a precision reduction at any point in the dataflow that the baseline does not have: an output dtype with fewer mantissa or exponent bits than its input. Fusing kernels on a model that is already FP8 is lossless. Casting BF16 weights to FP8 for faster MMA is lossy. Declare which one applies; a lossy candidate's numerics are tested at Gate 5.1a. The declaration also sets `track_assignment` at selection: `quant` for a lossy candidate, `structural` for a host or dispatch mechanism, `lossless` otherwise.

Each champion submits two or three candidates, ranked by expected E2E return with the evidence behind each, that differ by component, mechanism, or technology.

### Declaration block

Every candidate carries one block. The lead checks that each field is filled and that the evidence pointers resolve. The lead does not substitute a technology preference of its own for the champion's reasoning.

```markdown
## Candidate Declaration
- Mechanism: <what you author, in one or two sentences>
- Category: <closest value from the schema's category enum; explain any mismatch>
- Precision: <lossless | lossy> and why, by the dtype rule
- Baseline: <the dispatched production implementation, from trace symbols and source, with evidence path>
- Technology: <kernel DSL, CUDA/C++, library extension, compiler pass, or host code> and why it fits this work
- Production integration: <compile, graph, dtype, dispatch, fallback implications>
- Evidence: <tier and scope of the magnitude claim, per the Evidence section>
- Projection: <per-BS f_e2e, speedup, and resulting E2E %, using the one boundary equation>
```

**Category routes the measurement.** Kernel-work mechanisms (`kernel_replacement`, `kernel_fusion`, `custom_kernel`, `weight_layout_transform`, `attention_kv_layout`) are measured at the kernel or fused-chain boundary and target `f_e2e` of that component or chain. Host and dispatch mechanisms (`dispatch_optimization`, `execution_pipeline_restructuring`, `communication_strategy`, `compute_graph_pass`) are measured at the actual-runner host interval and target the inter-kernel share. Category is a descriptor. It does not make an ineligible mechanism eligible.

## Evidence

Every magnitude claim answers two questions: how far did you check it against reality, and which part of production does it cover.

The tier is the champion's own verification ladder. Each rung asks a sharper question, and the answer either confirms the claim or corrects it before anyone builds on it. Climb as far as the load-bearing claim needs. A claim without its script and its log is analysis, whatever the code looks like.

| Tier | The question it answers | What you learn | Durable evidence |
|---|---|---|---|
| `tier_1` | Is the gain possible? | roofline, source, trace arithmetic | cited calculation or source |
| `tier_2` | Is the gain real at the boundary? | run it, keep the identity | script or command, log, target identity |
| `tier_3` | Is the mechanism the one you think it is? | Nsys identity; NCU counters when the claim is about bandwidth, occupancy, or cache | tier 2 plus claim-relevant profiler output |

| Scope (what it covers) | Boundary | Can size projected E2E? |
|---|---|---|
| `bound` | conservative zero-cost removal of measured device work from the baseline trace | yes |
| `proxy` | standalone, reconstructed, synthetic, or identity-unverified harness | no |
| `production_boundary` | A/B at a declared boundary in the actual production path | yes |
| `clean_e2e` | clean production sweep | ship signal only |

A proxy can show that a kernel works and how fast it can run. It cannot say how much production E2E it saves, because production dispatch, shapes, cache pressure, and the compiled graph can all differ from the harness. This is the rule that catches the most expensive mistakes, so an open objection that the measurement does not cover the projected boundary sets the candidate's EV to zero until the boundary is measured.

**Baseline provenance.** A production-boundary kernel claim calls the production entry point with the shapes, dtypes, strides, layouts, and compile or graph mode that decide dispatch, and it ties to Stage 2 with profiler evidence that fits the claim. Nsys establishes identity, ordering, and occurrence. Reach for NCU only when the claim depends on registers, occupancy, bandwidth, or cache behavior. Check the Stage 1 trace for the kernels you plan to replace: vLLM's Inductor passes may already fuse the chain, and a harness that measures the unfused chain measures code that does not run in production.

**Experiments.** Run the smallest bounded experiment that settles the load-bearing uncertainty. Arithmetic, source or ISA inspection, a small kernel prototype, a production-boundary benchmark, and a claim-appropriate profiler capture are all in scope. Hold the conditions the claim depends on: production API and dispatch, shapes and dtypes, compile and graph mode, working set. A cache or multi-layer test is needed exactly when cache residency is load-bearing. Report the buckets that argue against you. Stage 3 does not modify vLLM source, download model weights, or run a clean full-model sweep; those belong to Stage 4. When no experiment is affordable, declare the gap and its scope consequence instead of doing implementation-grade work to fill a template.

Every experiment leaves its script or command, a log with device identity, inputs, method, and result, and profiler output when the claim needs it, under `rounds/{CR}/debate/micro_experiments/`. The proposal cites those artifacts and says which values are measured, which are bounds, and which are assumptions.

**Projection.** Use the measured `f_e2e` for each target batch size and the one boundary equation in `references/e2e-delta-math.md`. `f_decode` is a diagnostic. Report the projection for every complete workload bucket and compare it with `campaign.config.min_e2e_improvement_pct`.

## The debate

### Proposals

Each champion publishes exactly one file, atomically, at `rounds/{CR}/debate/proposals/{champion_id}_proposal.md`, with frontmatter (`champion`, `stance: proposal`, `revision`, `summary`) and one section per candidate. Write for one busy engineer who reads it once to make one decision: verdict first, each number once with its source, evidence in tables.

When every proposal is in, the lead checks each candidate against the sections above. A candidate that fails gets one explicit revise request naming what is missing; the champion republishes with `revision` incremented and sends `{type: "proposal_revised", champion_id, revision}`. A candidate that still fails is out. If a critique later shows that the mechanism is really a flag flip or that a declaration was false, the candidate is out then, whatever its projected gain.

Survivors get a stable `OP-NNN` id and a two-to-four-word name for the mechanism. The lead records id, name, proposal path and section, accepted revision, and verdict in `rounds/{CR}/debate/eligibility_verdicts.md`. The id is the key in state, paths, and prompts from here on.

Also read `exhausted_technologies` from prior rounds. A recorded failure is evidence about a component, technology, and shape. It does not ban them: a candidate may retry the tuple when the mechanism, target, or invalidating evidence changed, and it says how.

### Critique and rebuttal

Every candidate receives one critique from a champion who did not propose it. The lead sends one `critique_assignment` per pair (critic `op_id`, target `op_id`, target path and section, output path); a ring over the owners works when adjacent owners differ, and an explicit map works otherwise. If fewer than two champions survive, ask the cohort for replacement proposals rather than allow self-critique.

The critic reads the assigned section and writes `rounds/{CR}/debate/round_1/{critic_op_id}_critique_{target_op_id}.md`. Attack what would make the candidate fail in Stage 4: feasibility math, evidence scope and provenance, hardware assumptions, production and compile safety, precision, regressions at other batch sizes, and readings of the profile the proposer missed. Start as soon as your target is published; waiting for unrelated candidates only stalls the round on the slowest champion.

The proposer reads its incoming critique and writes `rounds/{CR}/debate/round_1/{op_id}_rebuttal.md`. Answer each point with evidence, concede it, or give a concrete mitigation; one table row per critique is enough. End with `## Open Items`: what remains unresolved, or `none`.

A champion may rebut an objection that its measurement does not cover the declared boundary, but only the lead or the critic who raised it may close that objection, and only against a cited measurement of the boundary. A rebuttal cannot close it by assertion.

### A second round, when it earns its cost

One round of critique and rebuttal is the default. Run a second round only when a live, material disagreement about a measurement remains that a bounded experiment can settle: the champions rerun with disclosed methods, production-parity methodology (CUDA graphs plus `torch.compile`) takes precedence, and the round writes `round_2/{op_id}_argument.md`, a critique, and a rebuttal on the same rules. Do not run a round because an open-items list is non-empty; carry unresolved items into scoring and weigh them there. Stopping does not record an item as closed.

Publish each artifact through a temporary sibling and an atomic rename, so appearance of the final path means the artifact is complete. If an artifact misses its phase, ping the owner once with the expected path; after a short grace period, drop the stalled candidate and repair the affected critique assignment so no survivor lacks a critique.

## Winner selection

Read the whole record: proposals, critiques, rebuttals, and any second round. Then, for each surviving candidate:

1. **Floor.** Its admissible projected E2E gain (from `bound`, `production_boundary`, or `clean_e2e` evidence) meets `min_e2e_improvement_pct`. A candidate with only `proxy` magnitude, or with an open boundary-scope objection, has no admissible gain. The one exception is a host-only mechanism with no removable device work: it may take a slot as a `contingent_host_spike` when a measured production host-slice ceiling meets the floor and feasibility is grounded; record the ceiling in `host_slice_ceiling_pct`, keep its projection and EV at zero, and make the actual-runner boundary A/B its first Stage 4 gate.

2. **P(success).** Score 0 to 10 how likely this candidate is to pass Stages 4 and 5 as proposed. Start from the evidence tier: about 3 for analysis only, about 7 for measured execution, about 9 for a measured hardware claim. Then move it, up or down, for what the record shows: unaddressed material critiques, an undisclosed precision reduction, a measured baseline that diverges from Stage 2 without explanation, a replacement that gives up production capabilities without beats-baseline evidence, warm-cache-only numbers for a bandwidth-bound kernel, an isolated speedup for a repeated kernel without occurrence weighting, and the integration risk of the change itself. A conceded critique with a mitigation is resolved. Write the reasons for each P in `selection_rationale`. Judge the reasoning: a compact proposal that clears every point scores the same as a long one.

3. **EV.** `EV_pct = P/10 × projected_e2e_delta_pct`, with the delta corrected by the lead when needed: cold-cache numbers when warm/cold exceeds 1.5×, occurrence weighting for repeated kernels, integration overhead subtracted. Impact enters linearly and uncapped, so a large delta with thin evidence stays in the race.

4. **Portfolio.** Rank by EV and take the top candidates, two or three, with two rules. Do not pick two candidates that are the same bet: same component, same mechanism category, same technology class. Two candidates on the same component through different, algorithmically independent mechanisms are fine. And one slot may go to the candidate with the largest admissible delta whose P is at least 3, even when its EV is not top-ranked; record it as `selection_mode: long_shot` and give it `production_boundary_spike` as its first Stage 4 obligation. That slot is how the campaign funds high-risk, high-reward work: the track measures the declared boundary first and stops if the gain is not there, before the expensive part begins. Prefer three winners when the campaign's GPU pool can run three tracks at once.

Set `stage_4_validation_obligations` from the schema enum for the checks the record calls for: for example `cuda_graph_check` when graph safety was disputed, `numerics_sweep` for a lossy candidate, `crossover_probe` when a batch-size regression is plausible.

Write `selected_winners` and the typed `selected_candidates` entries to the current round in `state.json`; the schema owns the fields (`op_id`, `name`, `selection_mode`, `track_assignment`, `score_breakdown` with `evidence_tier` and `evidence_scope`, `stage_4_validation_obligations`, `cited_evidence`, `proposal_file`). The validator checks the EV identity and rejects a newly selected `proxy` scope. Those typed entries plus the canonical gates are the complete blocking validation contract; prose cannot add a gate.

Then write `rounds/{CR}/debate/summary.md` from `state.json`: for each winner, name and id, the mechanism in one sentence, P and EV with the reasons, obligations, cited evidence, and one short paragraph on why it beat the field. When the summary and the state disagree, the state is right and the summary is corrected.

## Handoff

Confirm that each selected record resolves to its accepted proposal revision and that every live objection is either weighed in P or carried as a Stage 4 obligation. Retire the debate roster per `references/shutdown-protocol.md`. Then open one isolated implementation track per winner under `orchestration/parallel-tracks.md`.

Before Stage 4, reconcile the selected proposals and outstanding validation obligations. Release the owned debate-round agents under `references/shutdown-protocol.md`, then create the selected implementation tracks.

## Artifacts

| Artifact | Path | Author |
|---|---|---|
| Proposal | `rounds/{CR}/debate/proposals/{champion_id}_proposal.md` | champion |
| Eligibility record | `rounds/{CR}/debate/eligibility_verdicts.md` | lead |
| Critique | `rounds/{CR}/debate/round_{N}/{critic_op_id}_critique_{target_op_id}.md` | champion |
| Rebuttal | `rounds/{CR}/debate/round_{N}/{op_id}_rebuttal.md` | champion |
| Second-round argument | `rounds/{CR}/debate/round_2/{op_id}_argument.md` | champion |
| Experiments | `rounds/{CR}/debate/micro_experiments/` | champion |
| Summary | `rounds/{CR}/debate/summary.md` | lead |
