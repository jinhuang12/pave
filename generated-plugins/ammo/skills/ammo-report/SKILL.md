---
name: ammo-report
description: Generate a standalone technical report from completed AMMO campaign artifacts, including measured results, charts, implementation details, failures, and reusable lessons. Read by the ammo-report-writer role at Stage 7b of an ammo:ammo campaign; not for direct use.
---

# AMMO Report Content Authority

Write a publication-quality `{artifact_dir}/REPORT.md` for engineers deploying Pumice. The reader should not need to know AMMO's roles, stages, gates, or internal vocabulary. Explain the work as profiling, analysis, candidate evaluation, implementation, and validation.

This file owns report content and source selection. The `ammo-report-writer` role (the Codex alias is `ammo-report`) owns execution. The lead owns the independent fact-check lifecycle (`skills/ammo/SKILL.md`, Stage 7b).

## Source Authority

Start with `state.json` and `target.json`. Iterate `state.json.campaign.rounds[]`; never infer the current or authoritative round from directory timestamps.

For round `N`, use only these paths:

| Evidence | Path |
|---|---|
| Baseline constraints | `rounds/{N}/constraints.md` |
| Clean baseline timing and golden references | `rounds/{N}/sweeps/baseline/` |
| Nsys/NCU evidence | `rounds/{N}/profiling/{nsys,ncu}/` |
| Bottleneck analysis | `rounds/{N}/mining/bottleneck_analysis.md` |
| Typed selection contract | `state.json.campaign.rounds[N-1].debate.selected_candidates[]` |
| Debate details | `rounds/{N}/debate/` |
| Track verdict | `rounds/{N}/tracks/{op_id}/validation_results.md` |
| Gate 5.1a/5.2 evidence | `rounds/{N}/tracks/{op_id}/validator_tests/` |
| Activation evidence | `rounds/{N}/sweeps/opt_profiling/{op_id}/` and `rounds/{N}/profiling/nsys/opt/{op_id}/` |
| Full-model correctness | `rounds/{N}/sweeps/opt_correctness/{op_id}/` |
| Clean per-track timing | `rounds/{N}/sweeps/opt/{op_id}/` |
| Combined timing/activation | `rounds/{N}/sweeps/{integration,integration_profiling}/` |
| Final code identity | track `worktree_branch`, `commit_sha`, `diff.patch`, and integrated `commit_sha` in state |

`state.json` is authoritative for identities, statuses, selection fields, shipped sets, and integration outcomes. Raw JSON, SQLite, logs, and profiler files are authoritative for measurements. Markdown explains evidence but cannot override it. If sources conflict, disclose the conflict and use the higher-authority source.

## Facts to Reconstruct

Extract, rather than guess:

- target model, hardware, dtype, TP/DP/EP, workload lengths, and batch sizes from `target.json` and `state.json.target`;
- original Round-1 baseline from `campaign.rounds[0].baseline` and `rounds/1/sweeps/baseline/`;
- each round's bottleneck, addressable `f_e2e`, candidates, selected typed obligations, outcomes, and audit disposition;
- shipped optimizations from `campaign.shipped_optimizations[]`, joined to the matching round/track by `op_id` and `round`;
- actual speedups and significance from clean per-track or integration manifests, never profiler-run latency;
- activation from claim-appropriate profiler artifacts;
- cumulative speedup as original Round-1 latency divided by the latest accepted integrated latency;
- failures, exhausted alternatives, gating/dilution decisions, and unresolved evidence limitations.

Use the title and scope in the final track verdict for a candidate's final name. Compare projections from the typed selected-candidate record with measured results, but preserve their evidence scope and do not present a bound or proxy as an observed E2E result.

## Required Report Structure

1. **Executive Summary**: target, method in one sentence, dominant bottleneck, what shipped, measured E2E effect, important failures, and remaining opportunity.
2. **Model and Hardware Context**: architecture facts supported by artifacts, workload, hardware table, and the relevant compute/memory regime.
3. **Measurement Methodology**: production parity, clean timing versus profiling, benchmark configuration, correctness method, and baseline table.
4. **Bottleneck Analysis**: component/kernel breakdown, occurrence-weighted E2E shares, physical limits, and the evidence that drove candidate generation.
5. **Approaches Evaluated**: all serious candidates, their mechanism, evidence scope, projection, selection reason, and preimplementation disposition.
6. **Implementation and Results**: shipped code, activation proof, correctness, boundary performance, clean E2E results, projection error, integration, enablement, and rollback.
7. **What Failed**: include when any implemented track failed; explain cause and evidence without turning a hypothesis into fact.
8. **Remaining Opportunities**: untried or unresolved addressable work and the mechanical reason the campaign ended.
9. **Key Lessons**: three to five actionable lessons tied to specific evidence.
10. **Appendix**: reproduction commands, source/commit identity, and complete relevant code listings when the artifacts and worktree still make them available.

Include a linked table of contents. Define technical terms on first use. Translate the internal terms below. An artifact citation keeps the literal name, because it helps the reader locate evidence: for example the state key `$.campaign.shipped_optimizations`.

| Internal term | Write instead |
|---|---|
| champion | optimization candidate, approach, or proponent |
| debate, adversarial debate | candidate evaluation or structured peer review |
| `T_AUDIT_*`, `ammo-auditor` | independent audit |
| Stage 1 to Stage 7 | a plain description of the step |
| Gate 5.x | a plain description, such as correctness validation or validation step |
| campaign, used as jargon | optimization effort or study |
| ship, shipped | merged, accepted, or deployed |
| worktree | development working copy or branch |
| f_decode | the term, defined on first use |

## Visuals

Produce these five required PNGs under `{artifact_dir}/report_assets/`:

| PNG filename | Purpose |
|---|---|
| `kernel_breakdown_pie.png` | GPU time by kernel category |
| `bw_utilization_bar.png` | Per-operation HBM bandwidth utilization |
| `e2e_results_bar.png` | Original baseline versus accepted result at every batch size |
| `roofline_plot.png` | Arithmetic intensity versus measured throughput |
| `nsys_timeline_synthetic.png` | One decode-step kernel sequence and relative durations |

For every produced PNG, also write a self-contained reproducible Python script
under `report_assets/`. Each script must read the cited campaign artifacts,
derive the plotted values, and emit its named portable PNG; do not hardcode
campaign measurements into the script.

A chart may be skipped only when the evidence source needed for that specific
chart is absent. In that case, the report must explicitly name the omitted PNG,
identify the missing evidence source, and state that the chart was omitted for
that reason. Do not substitute synthesized measurements. A schematic timeline
must be labeled as schematic and derived from measured kernel order/durations.
Use matplotlib from the existing environment and do not install dependencies.

Diagrams must be portable PNGs rather than renderer-specific Mermaid. Keep labels, units, legends, and source notes readable.

## Results Rules

- Use the original clean baseline for campaign-level before/after claims. A paired same-launch baseline may be used only for the local A/B claim it measured.
- Never use Nsys/NCU latency as clean E2E timing.
- Show per-batch-size results and significance; do not hide unfavorable buckets.
- For multi-part optimizations, separate contributions only when an artifact measured them separately.
- For `GATED_PASS`, show pre/post gating results, activation range, dispatch mechanism, and rollback.
- For `diluted:true`, explain the special ship conditions and report measured E2E without inflating it.
- Distinguish measured causality from plausible explanation. Label inferences.
- Cite every number to a primary artifact using a relative path and, for text, a line or JSON key when practical.

## Code and Reproduction

Resolve code through the track's recorded worktree branch/commit and the integrated commit. Use `diff.patch` to identify scope, then read the actual files at that commit. Do not present a materialized `.venv` edit as shipped engine code; describe upstream dependency patches separately.

Reproduction commands must use the engine venv Python that `target.json` `engine.venv` records, the target workload, required environment flags, and the canonical round/slot paths. Include enable, disable, and rollback instructions. Do not claim a command was run unless an artifact records it.

## Quality Bar

Before fact-check handoff, verify:

- every quantitative claim is traceable;
- shipped, failed, gated, diluted, and exhausted dispositions match state and raw validation artifacts;
- activation and timing evidence are not conflated;
- projection-versus-actual comparisons preserve evidence scope;
- no missing evidence is silently filled in;
- all five required PNGs exist with scripts that read real artifacts, or the
  report explicitly records the evidence-source omission for each skipped PNG;
- the executive summary stands alone;
- internal workflow jargon is translated;
- reproduction and rollback instructions match the recorded implementation.

## Serving campaigns

For a serving target, reconstruct claims from sealed serving results and `round.serving.accounting`, following `references/serving-campaign.md`. Do not fill E2E/TPOT batch columns with another objective. State the fixed offered load and local topology.
