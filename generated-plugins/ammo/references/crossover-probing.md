# Crossover Probing Protocol

Some optimizations help at some batch sizes and hurt at others. Crossover probing finds the batch-size threshold where the optimization changes from beneficial to harmful, so the dispatch gate can turn it off where it hurts. Read this when the track-level verdict is `GATING_REQUIRED`: some batch sizes PASS and others REGRESSED. You measure the boundary with cheap kernel benchmarks, then spend 1-2 E2E runs to confirm it.

## When to Probe

Probe only when all three of these are true:
- At least one tested BS has verdict `PASS` (per `validation-defaults.md § Per-BS Tiered Verdict`: clears the PASS floor and significance check, not merely speedup ≥ 1.0)
- At least one tested BS has verdict `REGRESSED` (speedup below noise tolerance but above catastrophic)
- The gating feasibility check (`impl-track-rules.md` § GATING_REQUIRED Workflow, step 2) found a dispatch site that can gate

Two verdict patterns need no probe at all. If ALL tested BS are PASS/NOISE, no probing is needed. If all are REGRESSED or CATASTROPHIC, probing cannot help, and the track goes to the fallback ladder in `validation-defaults.md`.

## Skip the Probe When PASS and REGRESSED Interleave

If the PASS and REGRESSED batch sizes are interleaved (e.g., BS=1 PASS, BS=8 REGRESSED, BS=32 PASS), there is no single crossover point. In this case:
- **Skip probing entirely**
- Gate the optimization to the exact set of PASS batch sizes only (no interpolation)
- The dispatch condition is an explicit set membership check, not a threshold comparison

## Predict from Kernel Data, Then Confirm with E2E (Primary)

This kernel-informed protocol is the primary path. Cheap kernel-level data predicts the crossover, so you skip an expensive E2E binary search.

### Phase 1: Kernel Sweep (~1-2 minutes)
Run kernel-level benchmarks at intermediate BS values between the known beneficial and regressed ranges. Kernel benchmarks are cheap (~seconds each, no model load needed).

Example: If BS=8 is PASS and BS=32 is REGRESSED, test kernels at BS=12, 16, 20, 24.

### Phase 2: Delta Math Prediction
For each intermediate BS, compute the per-BS form of the Amdahl projection in `references/e2e-delta-math.md`:
```
predicted_e2e_improvement(BS) = f_e2e(BS) × (1 - T_kernel_opt(BS) / T_kernel_base(BS))
```

`f_e2e(BS)` is the component's production E2E share at that batch size, from the per-BS row of the `## Workload Dilution` table. Never substitute the decode-only `f_decode(BS)` (`references/e2e-delta-math.md` § Conversion Is Always Required). Kernel time, `decode_busy`, and the decode share each scale differently with batch size, so read `f_e2e` from the row for each BS. For an intermediate BS that was not profiled, interpolate linearly between the nearest profiled BS below it and the nearest profiled BS above it. This is approximate but sufficient for crossover prediction.

The predicted crossover is the largest BS whose prediction still clears the noise tolerance:
```
crossover_bs = max(BS) where predicted_e2e_improvement(BS) >= noise_tolerance_pct / 100
```

### Phase 3: E2E Confirmation (1-2 runs)
Run 1-2 E2E benchmarks at the predicted crossover BS to confirm:
- If E2E confirms (speedup at predicted crossover is PASS or NOISE): `crossover_threshold = confirmed BS`
- If E2E disconfirms: adjust by ±1-2 BS and re-confirm (max 2 adjustments)

## When the Kernel Sweep Is Inconclusive

If the kernel sweep is inconclusive (warm/cold speedup ratio > 1.5x at intermediate BS, so L2 pressure from the full model can make the kernel prediction wrong) AND the range spans > 15 batch-size values:
- **Skip probing entirely**
- Gate to exact PASS batch sizes only (same as the non-monotonic guard for interleaved verdicts above)
- This avoids burning 30 minutes on an unresolvable search

For 15 or fewer intermediate batch sizes, run bounded clean E2E probes and
consume the canonical generated per-BS verdicts from `validation-defaults.md`.
Do not reproduce noise arithmetic here. If the evidence does not establish a
monotonic threshold within the track's bounded probe budget, gate to the exact
confirmed PASS set.

## One Gating Attempt Per Track

One gating attempt per track, with no nested probing. Re-validation is `impl-track-rules.md` § GATING_REQUIRED Workflow, steps 8-10.

## Where to Record the Result

The champion records the probe in `rounds/{CR}/tracks/{op_id}/validation_results.md`: each probed BS with its kernel speedup and predicted E2E improvement, the predicted and confirmed BS, and the method. The lead merges it into `state.json` at `campaign.rounds[$IDX].parallel_tracks.tracks[op_id].gating.crossover_probing` (where `$IDX = campaign.current_round - 1`) in the schema's shape:

```json
{
  "probed_points": [12, 16, 20],
  "predicted_bs": 16,
  "confirmed_bs": 16,
  "converged": true,
  "time_minutes": 8.5
}
```

## When in Doubt, Pick the Narrower Range

When in doubt, use the last known-beneficial BS as the threshold (recorded in `state.json.campaign.rounds[N-1].parallel_tracks.tracks[op].gating.crossover_probing.confirmed_bs`). This conservative bias is deliberate: a slightly narrower beneficial range is better than shipping a regression. The env var gating always provides an escape hatch for operators.
