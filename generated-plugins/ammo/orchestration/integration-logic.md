# Stage 6: Integration Validation

Stage 6 answers one question: which accepted track results can ship together?
It then validates the exact composition you select and promotes only that
composition into the campaign baseline. It never reopens a failed track and
never creates the next round. Read this before you decide anything in Stage 6.

Read the current round from `state.json`. Reconcile every track against its
primary Gate 5 artifacts before you decide. Every track must be terminal under
the state schema, and `T_AUDIT_S45` must have passed.

## Four Branches

Accepted means `PASS` or `GATED_PASS`. Rejected means `FAIL`. `diluted:true` is
evidence about measured impact, not a separate integration status. Use
`references/validation-defaults.md` for verdict semantics.

1. **No accepted track:** finalize the round as EXHAUSTED (§ Promotion and
   Finalization, EXHAUSTED paragraph).
2. **Exactly one unchanged accepted track:** use the single-track
   short-circuit (§ Single-Track Short-Circuit).
3. **Multiple compatible accepted tracks:** put their exact validated commits
   and environment settings on an integration branch, then run the full
   integration validation below. Per-track evidence cannot establish the
   behavior or the performance of a composition. The integration branch lives
   in your own worktree under the engine root. Create that worktree through the
   same worktree hook the tracks use (`hooks/worktree-create-with-build.sh`),
   and record the branch in `integration.combined_patch_branch`. If a selected
   track changes Rust or confdb code, run the rebuild in
   `references/impl-track-rules.md` § Worktree Build Rules before any Stage 6
   sweep.
4. **Overlap, gating interaction, merge conflict, or ambiguous compatibility:**
   judge compatibility and dominance from evidence. File overlap is a warning,
   not proof of incompatibility; disjoint files are not proof of independence.
   If a conflict or a semantic interaction needs code resolution, run the
   review loop in step 1 of § Combined Validation Workflow. Validate the
   resulting exact subset as a composition. If the defensible result is one
   unchanged track, fall back to the single-track short-circuit; if none
   remains, exhaust the round.

Select a subset on its evidence. Verdict labels alone or a single best number
do not decide it. Judge each candidate subset on correctness, production
activation, gating behavior, interaction risk, regressions across the frozen
workload, and clean E2E evidence. One subset dominates another only when its
evidence supports the same required behavior, shows no worse accepted-workload
result, and shows a better campaign objective. When the evidence does not
establish dominance, validate the plausible composition rather than guessing.

## Single-Track Short-Circuit

When exactly one accepted track remains, and its patch and environment have not
changed since Stage 5, it ships on its Stage 5 evidence: integration would add
no new technical information. Set `integration.status` to `gated_pass` for a
GATED_PASS track and to `single_pass` for a PASS track, and promote the result
it already validated. Copy the Stage 5 opt sweep results into the
`integration` slot and write `e2e_latency_combined` and `per_bs_verdict` from
them. Do not rerun. Any source, build, dispatch, or environment change
disables the short-circuit and requires the combined workflow.

## Combined Validation Workflow

The integration branch must contain exactly the selected, previously validated
track changes plus any independently reviewed conflict resolution. Record the
selected track identities and immutable commit/patch provenance before running
validation. Any later source, build, dispatch, or environment change invalidates
the result and requires validation again.

For every multi-track composition:

1. Resolve merge and semantic interactions without weakening any selected
   track's accepted boundary, fallback, or rollback control. When a conflict
   or an interaction needs code resolution, run this review loop:
   - Spawn `ammo-resolver` (`agents/ammo-resolver.md`). It returns a staged,
     uncommitted diff on the integration branch.
   - Spawn a fresh `pave-init:adversarial-investigator` as the independent
     merge reviewer: one-shot, with no `name`. Give it the staged diff and
     each selected track's validation results.
   - On REVISE, spawn a fresh `ammo-resolver` with the findings, then a fresh
     reviewer for the new diff.
   - After two rejected cycles, decide under branch 4 without that
     resolution.
   - Commit only a resolution the reviewer accepts.
2. Run full-model correctness against the current round's frozen golden
   comparator, not only component tests.
3. Run a clean, profiler-free production E2E measurement across the frozen
   batch-size/workload matrix against the current production baseline.
   `references/e2e-latency-guide.md` § Tool Selection owns the command, the
   comparator import, and its flags. Apply the thresholds and per-bucket rules
   in `references/validation-defaults.md`.
4. Capture separate claim-appropriate profiler evidence in the
   `integration_profiling` slot. It must prove that every selected optimized
   path and gated boundary actually activates after composition. Profiled
   latency is not timing authority.
5. Write the coherent result to the current round's `integration` state and
   artifact slots. Never combine metrics from different source trees or
   environments into one verdict.

If a composition fails, use its evidence to remove or repair the implicated
track or interaction, then validate the new exact subset. A track that passed on
its own may still serve as the unchanged single-track fallback. Never ship an
extra track because its individual result looked harmless, except for the narrow
diluted-track carve-out below. Otherwise every shipped multi-track set must be
the set measured in the passing integration run.

### Diluted-track carve-out when composition loses

This is an explicit exception to the exact-set rule above. When a measured
multi-track composition loses to the best individual track, a track that would
otherwise be dropped may still ship alongside that best track only when all of
the following hold:

- the dropped track is a distinct mechanism/component and is recorded
  `diluted:true`;
- its current Stage-5 per-batch-size evidence contains no regression, re-read
  from that track's `validation_summary.json` rather than a stale state marker;
  and
- it lands as a second, independently cherry-picked commit alongside, not
  instead of or folded invisibly into, the best individual track.

Record the diluted track as shipped with `diluted:true` and record why the
combined objective selected the best individual result. Cumulative speedup
comes from the measured E2E of the shipped pair, with no added diluted-track
contribution (`references/validation-defaults.md` § DILUTED_PASS Ship Path).

## Pre-SHIP Mechanical Checks

Before merging to the campaign mainline, the branch checked out at the engine
root, run these four fail-closed checks: (1) the added lines of the integration
diff hold no conflict markers; (2) `integration.per_bs_verdict` holds no
REGRESSED or CATASTROPHIC; (3) every integration sweep row has
`opt.returncode == 0`; (4) `ammo_state.py validate` passes.

## Promotion and Finalization

After a passing integration decision:

1. Merge or cherry-pick the exact validated patch set onto the campaign
   mainline and record its final commit identity.
2. Promote the environment contract. Set `target.json` `bench.baseline_env` to
   its prior keys plus only the `bench.opt_env` keys and values that the
   passing validation used, then empty `bench.opt_env`. Environment naming and
   isolation are owned by `references/impl-track-rules.md` and the sweep
   runner.
3. Capture fresh golden references from the promoted mainline in the current
   round's `golden_capture` slot. These become the next round's correctness
   comparator; they do not replace the current round's original evidence.
4. Record the accepted track identities, integration outcome, metrics, commit,
   promoted environment, dilution annotations where applicable, and current
   round finalization fields in schema-valid state. Finalize the current round
   in place.
5. Run `T_AUDIT_S67` according to `orchestration/audit-protocol.md`. It covers
   the exact integration, environment promotion, golden capture, shipped
   bookkeeping, and campaign-evaluation inputs.
6. Enter Stage 7. **Only Stage 7** may evaluate the stop condition, increment
   `campaign.current_round`, or create the next-round state and artifacts.

For EXHAUSTED, record why each track was rejected and the terminal
integration/round outcome in place, then run `T_AUDIT_S67` before Stage 7.
There is no environment promotion or golden capture.

Cumulative improvement, Stage 7 continuation, terminal-token choice,
all-diluted round handling, and mining invalidation are owned by `SKILL.md`
under `Stage 7: Campaign Evaluation`.

Until `T_AUDIT_S67` passes, current-round `shipped` identities are a provisional
promotion record, not a reportable final ship. A rejection removes the
implicated identities from the current round's `shipped` list and from
`campaign.shipped_optimizations`. `orchestration/audit-protocol.md` § Repair
and Re-audit owns the rest of a Stage-67 rejection.

## Authority Pointers

- State shapes and enums: `schemas/state.schema.json`
- Legal transitions and terminal sets: `scripts/transitions.json`
- Track verdicts, correctness, E2E, activation, and gating:
  `references/validation-defaults.md`
- Environment flags and fallback contracts: `references/impl-track-rules.md`
- Canonical integration, profiling, and golden paths:
  `references/artifact-layout.md`
- Conflict-resolution role: `agents/ammo-resolver.md`
- Stage 6 audit (`T_AUDIT_S67`): `orchestration/audit-protocol.md` and
  `references/audit-invariants.md`
- Stage 7 continuation: `SKILL.md`
