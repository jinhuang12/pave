# Writing Style for AMMO Artifacts

Use this for proposals, critiques, rebuttals, validation reports, monitor logs, and decision records.
Write in ASD-STE100 Simplified Technical English. Put the verdict or next action first, followed by the evidence needed to assess it.

## Writing rules
Write what a staff engineer would keep: the numbers, the verdict, the non-obvious reasoning, and the evidence pointers.

1. Use familiar words, active voice, and one main claim per sentence. Name the actor and action.
2. State each conclusion once in prose. Keep repeated values or verdict tokens wherever a required parsed block needs them.
3. Cite measured values and factual claims at their first use. Reuse the evidence pointer when needed to make a later claim clear.
4. Explain uncertainty, limitations, and failed attempts directly. Describe their effect on the decision.
5. Use tables for comparisons, risks, attempts, and responses to critiques. Include every material item.
6. Use emphasis for the verdict and facts that affect the decision. Remove protocol narration, self-praise, and repeated conclusions.

Formatting guidance does not create a new acceptance gate.

## Length

Write the length the decision needs: as long as the evidence and the non-obvious reasoning require, and no longer. Three formats are fixed because other agents parse them:

| Artifact | Format |
|---|---|
| Rebuttal | one table row per critique |
| Monitor poll, no findings | one line: `Poll 12 (lines 296–468): smoke test pass, no flags` |
| Monitor poll, finding | the DA-MONITOR message + one line of context |

## What stays complete

Brevity never overrides a machine contract. Gates, hooks, and the orchestrator parse the structures below, so keep their headings and field labels verbatim:

- Proposals: the `## Candidate Declaration` block with all eight labeled fields, and the authored-mechanism description.
- `validation_results.md`: the Decision/Overall heading with an explicit `PASS`/`FAIL`/`GATED_PASS` token, the `## Gate 5.3` E2E table, the Files-modified / Scope-adherence list, and (for GATED_PASS) the dispatch-mechanism + env-var + crossover + pre/post per-BS tables.
- `bottleneck_analysis.md`: the literal `## Technology Landscape` heading, the `## Workload Dilution` and `## Top Components` tables (with `f_e2e` and the per-BS `decode_busy` / `decode_share_of_e2e` cells).
- Rebuttals: the `## Open Items` list, or `none`.
- All artifacts: every real number, evidence pointer (`file:line`, log path, trace ref), and per-BS data row.
