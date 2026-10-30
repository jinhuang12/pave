# Mining Orchestration (Stage 2 Family Wave)

Stage 2 mines the tables, runs the family wave, and then writes the report
(`skills/ammo/SKILL.md` § Stage 2). This file covers the family wave: one
dossier per selected kernel family, written in parallel, each reviewed before
the report uses it. The wave opens when `mined.json` names the families and
closes when every dossier ends with its status line. A serving target opens
no family run.

Run the wave with the pave-init orchestrate skill. That skill owns the wave
size, the reviewer count and assignment, the review loop, ESCALATE, the run
log, and the whole-goal review. This file adds only what is AMMO-specific.
`references/harness-binding.md` § Stage 2 Family Wave binds the wave on each
harness. The miner, the report author, and the auditor are not part of the
wave: the lead dispatches them the normal AMMO way.

## Goal

Paste this as the orchestrate goal:

> One dossier per entry of `mined.json selected_families` plus the
> cross-cutting slice, each in the shape of `references/mining-dossier.md`,
> grounded in the trace, `mined.json`, and the engine source, and passed by
> its reviewer.

The whole-goal review checks the dossier set only: every selected family has a
dossier, no two dossiers contradict each other, and every ceiling cites
`mine_config.json`. The stage gate is still `T_AUDIT_S2`, after the report.

## Worker Packet

Send one packet per entry of `selected_families`. Copy each value from that
entry in `mined.json`; do not pick or round. Name each worker
`family-<LABEL>-r<N>`, because a family label recurs in later rounds. `A` is
the campaign dir and `E` is the engine checkout, both absolute.

```
SUBTASK family-<LABEL>-r<N>

Orchestrator: team-lead
Reviewer: <assigned reviewer>
Wave: 1 of 1

## Goal
The dossier for family <LABEL> (symbol_patterns <PATTERNS>, f_e2e <F_E2E>,
physical_ceiling <CEILING> from mine_config.json), grounded in the trace and
the engine source.

## One agent
One agent: one family, the template in references/mining-dossier.md, and a
fixed evidence set.

## Write set
- A/rounds/<N>/mining/families/<LABEL>.md

## Inputs (read-only)
- plugin_root: <absolute plugin root>
- A/rounds/<N>/mining/mined.json (this family's entry in selected_families,
  with its chain), A/rounds/<N>/mining/tables.md, and
  A/rounds/<N>/mining/mine_config.json
- the trace sqlite and the bucket slices under A/rounds/<N>/profiling/nsys/
- the engine checkout E
- references/mining-dossier.md, references/e2e-delta-math.md,
  references/fusion-feasibility-heuristics.md
- Technology Landscape: <assigned | omitted>

## Acceptance
- Every section of the template is present, in order.
- Every claim cites a trace row, a mined.json field, or a file and line.
- The ceiling cites mine_config.json and is checked, not derived again.
- No recommendation, ranking, or speedup estimate.
- The worker writes the last line of the file, its status line when the review ends
  (references/mining-dossier.md § 7. Status Line).

## Context
AMMO Stage 2, round <N>. One worker per selected family. The researcher
folds the dossiers into bottleneck_analysis.md, then the AMMO auditor
reviews the report and the dossiers.
```

The cross-cutting packet has the same shape. Its label is `cross_cutting`, its
goal names the cross-cutting slice instead of a family, and it follows the
template's § Cross-Cutting Dossier. Assign the Technology Landscape only to the
top three families by `addressable_e2e` in `mined.json`; mark it omitted in
every other packet.

## Ungrounded Dossiers

When a worker sends ESCALATE, rule on it as the orchestrate skill says. If the
ruling leaves the dossier without a PASS, the worker writes `UNGROUNDED: <reason>`
as its last line, with the reason the loop gave. A worker that finds its
family's patterns or ceiling wrong does the same, with the evidence. The lead
never writes a dossier. The report discloses every ungrounded dossier, and the
audit decides whether it blocks.

## Audit Repair

When `T_AUDIT_S2` blocks, route by the files its BLOCKING and HIGH findings
cite. A finding that cites any file outside `families/` and
`bottleneck_analysis.md`, such as `mine_config.json`, `mined.json`,
`tables.md`, or a trace slice, reopens the tables, and the new tables open
every family again.
Findings that cite only dossiers or `bottleneck_analysis.md` reopen only the
cited families, each with its packet and the findings. With no family cited,
only the report is written again.
