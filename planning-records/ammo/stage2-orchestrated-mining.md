# Stage 2 as an orchestrated child graph

Status: draft for user review, 2026-10-29, after one `/simplify` pass. Nothing
in the plugin changes until this lands through `/pave-evolve` as revision 2.
Drafts: `drafts/stage2/` (child graph, parent patch generator, semantic diff).

## Decision

Stage 2 becomes a PAVE composed node. The campaign lead runs the child graph.
One researcher mines the tables. One worker per selected kernel family writes
a reviewed dossier in parallel, as one pave-init orchestrate wave. The
researcher writes the report. The AMMO auditor judges the stage inside the
child run, so the separate `audit_s2` node goes.

```
[mine_tables: researcher]  -->  mined.json names the families
          |
[family_dossier x N: orchestrate wave, one reviewer per 2-3 workers]  -->  join
          |
[synthesize_report: researcher]  -->  bottleneck_analysis.md
          |
[whole_goal_review: ammo-auditor, stage_2]  -->  audits/stage_2.md  -->  parent reads the stamp
```
Loops: a FATAL retries the miner; a wrong partition or ceiling returns to the
miner; an audit blocker returns to the owner of the cited file, upstream first;
the audit cycle cap pauses the campaign.

**The gap this closes.** One researcher today holds the global trace read plus
five independent source, roofline, and library investigations in one context,
and the first independent check of any of it is the stage audit. The split
gives each family its own review loop before the audit sees the report, and
keeps the two global judgments with one agent each. The evidence is
structural; the implementation wave's dry run measures audit cycles before
and after.

**Why the lead orchestrates, not the researcher.** The researcher is a
one-shot subagent. Worker and reviewer messaging under a nested subagent is
unverified on Claude Code, and the lead already orchestrates the campaign.

**Why the auditor stays the gate.** The state engine refuses a `passed_at`
without the `started_at` that only the auditor dispatch hook writes. Keeping
the canonical dispatch keeps the stamps, the two-phase hooks, the verdict
format, and the Codex path unchanged. What changes is where the audit runs
and what it sees: the dossiers are in scope.

**What stays.** The script owns every mined number. Facts only, no proposals.
One author for the report. The `audit.stage_2` stamps, the verdict file path,
the cycle cap, and the pause.

## The orchestrate run

The orchestrate run covers the family wave only: it opens at the fan-out and
closes at the join. The miner, the report author, and the auditor are
dispatched the normal AMMO way, so the child state is the one resume record
and the orchestrate log holds only worker and reviewer lines.

Goal the lead pastes:

> One dossier per entry of `mined.json selected_families` plus the
> cross-cutting slice, each in the shape of `references/mining-dossier.md`,
> grounded in the trace, `mined.json`, and the engine source, and passed by
> its reviewer.

Rules the skill already owns and this record does not restate: wave size,
reviewer count and assignment, the review loop, ESCALATE at round four, the
log. Two additions:

- While the workers draft, the lead sends one reviewer a tables-only brief
  (the phase-accounting and partition bullets of audit-invariants.md). A
  partition finding then costs one worker round, not the whole wave.
- On a worker ESCALATE the lead marks that dossier ungrounded with the reason.
  The report discloses it. The audit decides whether that blocks.

The orchestrate whole-goal review covers the dossier set only: every selected
family has a dossier, no two dossiers contradict each other, and every ceiling
cites `mine_config.json`. The stage audit is the AMMO auditor.

## Seats

| Seat | Agent | Dispatch |
|---|---|---|
| miner | `ammo:ammo-researcher` | `task_type: mining_tables`; stops after `tables.md` |
| family worker | `pave-init:orchestrate-worker` | packet below, family substituted |
| family reviewer | `pave-init:adversarial-investigator` | per the orchestrate skill |
| report author | `ammo:ammo-researcher` | `task_type: mining_report`; the lead may resume the miner |
| auditor | `ammo:ammo-auditor` | canonical `audit_gate` block, `stage: stage_2` |

The two researcher task types replace today's single `mining`. Their inputs
and acceptance are the researcher's own sections, split at "stop after
tables.md" and "start from tables.md and the dossiers". WARNs from `mined.json`
are addressed in the report prose, as today.

### Worker packet (shipped as `orchestration/mining-orchestration.md`)

```
SUBTASK family-<LABEL>
Orchestrator: lead   Reviewer: <assigned>   Wave: 1 of 1
Goal: the dossier for family <LABEL> (patterns <PATTERNS>, f_e2e <X>, ceiling
  <C> from mine_config.json), grounded in the trace and the engine source.
One agent: one family, the template in references/mining-dossier.md, a fixed
  evidence set.
Write set: A/rounds/N/mining/families/<LABEL>.md
Inputs (read-only): A/rounds/N/mining/mined.json and tables.md, the trace
  sqlite and bucket slices under A/rounds/N/profiling/nsys/, the engine
  checkout E, references/e2e-delta-math.md, references/fusion-feasibility-heuristics.md.
Acceptance: every section of the template present; every claim cites a trace
  row, a mined.json field, or a file and line; the ceiling cites
  mine_config.json and is checked, not re-derived; no recommendation.
```

The cross-cutting packet uses the same shape with the template's cross-cutting
variant. The Technology Landscape section is assigned only to the top three
families by `addressable_e2e` in `mined.json`; the other dossiers omit it.

### Dossier template (shipped as `references/mining-dossier.md`)

Sections, each with its evidence:

1. Kernel chain in one decode step: trace order, occurrence count against layers x decode steps, phase membership.
2. Source map: dispatch path from the engine entry point to the kernel, file and line for every hop.
3. Physical ceiling: the `mine_config.json` value, checked against bytes moved, flops per call, and peak for this GPU, with the assumptions listed.
4. Removable fraction: the byte and kernel-count bound a fusion or a better kernel could remove, as a bound.
5. Technology Landscape (top three only): the block in `agents/ammo-researcher.md`, unchanged.
6. Correctness invariants a change must preserve.
7. Last line: `PASS` from the reviewer, or `UNGROUNDED: <reason>` from the lead.

Cross-cutting variant: sections 1 to 4 become gap accounting per decode step
(`measured_idle_ns`, `in_graph_fraction` from `mined.json`), launch overhead
against kernel time, graph replay boundaries, the MoE routing glue chain, and
at TP above 1 the per-rank table from the nsys guide's TP section.

## Audit checklist change

`references/audit-invariants.md`, After Stage 2, gets one bullet: every
family in `mined.json selected_families` has a dossier that is grounded or
disclosed as ungrounded in the report, and the dossiers obey the same
cited-claim and proposal-neutral rules as the report (HIGH). Nothing else in
the checklist changes. The hook delivers it as today.

## State

The child outcome map in the parent patch is the one place that names the
crossing. The lead acts on the parent outcome:

| Parent outcome | Lead action |
|---|---|
| pass | mining enrichment to state.json; `audit.stage_2` stamps already written by the auditor flow; advance |
| escalated | pause per audit-protocol.md, Repair and Re-audit |
| user_authority_needed | pause with the blocker record |

## Script changes

`scripts/mine_trace.py` writes two more things to `mined.json`: the family
selection by a stated rule (highest `f_e2e` across buckets above a floor, at
most five) so the lead copies instead of picks, and one ordered kernel chain
per selected family (symbols, counts, durations for one decode step) so the
workers and reviewers cite fields instead of re-querying the sqlite.

## Dependency

The ammo plugin would require the pave-init plugin from the same marketplace:
the orchestrate skill, `orchestrate-worker`, and `adversarial-investigator`.
Record it in the skill's compatibility metadata and the README host prep.

## Changes to land

1. `workflow.pave.yaml` per `drafts/stage2/stage2.semantic.patch`;
   `workflow.stage2-mining.pave.yaml` from the child draft; revision 2 in
   `revisions.yaml` and `history/v2.patch` written against the live text.
2. `skills/ammo/SKILL.md`: Stage 2 describes the child run; the T_AUDIT_S2
   sentence moves inside it.
3. `agents/ammo-researcher.md`: `task_type` values `mining_tables` and
   `mining_report`; the dossier template moves to `references/mining-dossier.md`.
4. New `orchestration/mining-orchestration.md` (goal, packet, tables-only brief).
5. `references/audit-invariants.md`: the new bullet. `orchestration/audit-protocol.md`:
   the T_AUDIT_S2 row says where it runs. Other T_AUDIT_S2 mentions follow:
   `agents/ammo-auditor.md`, `references/artifact-layout.md`,
   `references/serving-campaign.md`.
6. `scripts/mine_trace.py`: selection and chains. `scripts/ammo_state.py`:
   the Stage 2 next-step text only; stamps unchanged.
7. `scripts/artifact_layout.json` and `references/artifact-layout.md`: the
   `mining/families/` slot.
8. Tests: `test_workflow_pave.py` audit-map test passes on the draft today;
   add graph name drift for the child, layout manifest, researcher contract,
   codex port for the new reference; a dry run of the wave in the smoke container.

## Open questions

1. Selection floor and cap: five families plus cross-cutting fills one
   orchestrate wave. A trace with more above the floor needs a second wave or
   a higher floor. The dry run decides.
2. Codex: the orchestrate skill is Claude Code only. Stage 2 on Codex keeps
   the one-agent path until a Codex binding exists.

## From the simplify pass

Cut: the pasted review checklist and its second verdict vocabulary (the
auditor's protocol already defines both); three destination-encoding blocked
outcomes (one `blocked` routed by the cited file's owner); the `escalated`
outcome (the cycle-bound check already routes there); a check that compared a
report before it existed; two hand-kept state fields the log and the files
already hold; the ceiling re-derivation in every dossier; packet bodies that
restated the researcher's own contract; the full-file re-dump posing as the
patch. Kept open: the family count, Codex.
