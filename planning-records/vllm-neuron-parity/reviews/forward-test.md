# Clean-room forward test — vllm-neuron-parity (Stage 5 §6, 2026-08-26)

Tester: fresh one-shot `pave-init:forward-tester`, no model/effort
override, temp output dir `/tmp/vnp-forward-test-20260826`, no-live-mutation
rule, representative prompt derived by the lead (recorded gap: plan carried
none). Packaged plugin driven natively headless: `claude -p --plugin-dir
<plugin> --permission-mode bypassPermissions`, resumed across legs.

## Verdict

**PASS with three transferable doc defects — all repaired** (see Repairs).
Gate 1 was REACHED with a ranked 9-target backlog; the run stopped clean at
the gate awaiting a user decision the tester (correctly) did not answer.
Fresh-session start, native skill + role-agent resolution, evidence
discipline, self-auditing, and gate behavior all confirmed real.

## Session ledger (tester-reported)

| Leg | Mode | Result | Cost |
|---|---|---|---|
| S1 | fresh `claude -p` | intake + 9 tracers dispatched; killed in-flight seats at turn end | $67.16 |
| S2 | `--resume` | recovered seats, 9/9 delta reports landed | $50.28 |
| S3 | `--resume` | delta_mapped accepted | $10.81 |
| S4 | `--resume` (+1 coaching line: poll, don't end turn) | costing → 2-round adversarial review → GATE 1, stopped clean | $27.33 |

Total ≈ $155.6, ~3h45m wall, 909 files / 10 MB emitted.

## Lead's independent verification (§6 "inspect its artifacts")

- Fork `/Users/jinhun/GitHub/vllm-neuron`: clean porcelain, HEAD `0e19f00`,
  branch `feature/p-eagle-gpt-oss-20b` — verified by the lead directly.
- Marker `.vllm-neuron-parity-run` written at run cwd with correct content.
- Emitted `run-state.json`: `active_node_runs` empty, `ranked_backlog`
  populated, deviations (D4, D6) recorded in `completed_outcomes` /
  `open_questions`, `gate_approval_records` empty — CORRECT fail-closed
  behavior (gate presented, never answered; nothing to record).
- 9 per-target delta reports + index (verdicts, coverage-diff,
  sufficiency-record) on disk; preflight record cites transcript + exit
  code per claim.
- Strays: ~11 throwaway scratch files written to /tmp root by sonnet sweep
  delegates (copied to `.strays/`); zero Write/Edit outside temp dir
  otherwise; no pushes/PRs/hardware/venv/cache writes. The workflow
  DECLINED a venv install it wanted (arctic_inference) and surfaced it at
  the gate — the effect discipline held where it mattered.
- Intake caught the deliberately contradictory prompt (baseline
  release-0.24.0.1.1.0 pins vllm==0.24.0, not 0.21.0), froze a resolution,
  recorded-not-substituted, re-flagged at every stop; pulled the standing
  2026-08-11 EAGLE close-out from prior campaign data to prevent a re-spend.

## Transferable defects → repairs (all landed 2026-08-26)

1. **Headless seat-kill** — lead ends its turn "waiting on the seat"; in
   `claude -p` that terminates the process and kills in-flight seats
   (reproduced 3×; S4 proved one instruction suffices). Harness artifact,
   but transferable. FIX: `SKILL.md` Dispatch mechanics — new
   "Non-interactive sessions" paragraph: wait synchronously, poll declared
   artifact paths, never end the turn while seats run.
2. **Seat-report write path vs harness guard** — the harness's subagent
   report-file guard refused tracer `report.md` writes; 7/9 reports were
   lead-transcribed (self-disclosed as D4), unbacking the layout's "hash
   changed ⇒ a tracer wrote" implication. FIX:
   `references/artifact-layout.md` §4.7 + §2 row — lead-transcribed
   verbatim hand-back is now a first-class landing path, recorded in run
   state; content author stays the tracer; hash change with neither path
   behind it is a violation.
3. **README validator overclaim** — README said graph AND run-state
   validation "fail closed" without jsonschema; `validate_run_state.py`
   actually degrades to a passing stdlib basic check. FIX: README §1 now
   states validate_pave.py fails closed; validate_run_state.py is full
   only with jsonschema and labels its fallback mode.

## Non-defects and residuals (handed forward)

- D6: one seat ran at `high` effort where its contract pins `medium` —
  self-caught and logged by the run's own audit; contract was correct.
- Strays to /tmp root by delegates: violates the layout's stray-write rule
  (already written, `artifact-layout.md` §2); throwaway content, zero
  live-target impact. Residual: brief-writers must restate scratch homes.
- Rederiver seat not exercised (gate 1 stops before any campaign);
  fable-pin residual stands as previously recorded.
- Tester process note (corrected): the lead's 13:35 cap directive and the
  tester's final report crossed in flight — the tester later confirmed
  (`pgrep`: zero headless sessions) that ALL FOUR legs, including S4 which
  reached gate 1, had already exited before the cap message arrived, and no
  leg launched after it. No cap violation occurred; the lead's earlier
  read of S4 as past-cap was wrong and is corrected here.
- Gate-1 content preserved (backlog rank eagle3 > ngram > suffix > mtp >
  draft_model > eagle > ngram_gpu > medusa > mlp_speculator; 4 user-owned
  decisions; family serialization on neuron_model_runner.py:730-741) for
  the first real run to reference, not to reuse as evidence — forward-test
  output is validation evidence only (pave-revisions.md).

## Post-repair gates — ALL CLOSED PASS (2026-08-26)

Doc-only delta, no graph/hook/schema/script change (graph mtime-verified
by the reviewer). Repeated gates:

- Validation steps 1/2/9/10: plugin-validator PASS, skill-reviewer PASS,
  residue clean (29 files), README current.
- Final-review delta round (fresh reviewer final-reviewer-2): round 1
  REVISE — 1 HIGH (fail-closed claim survived in SKILL.md frontmatter +
  plugin.json, both outranking the corrected README), 2 MEDIUM
  (transcription provenance unnamed; polling exit unreachable on the
  refused-write path), 1 LOW (blanket stray-write sentence). All fixed,
  plus the validator's pre-existing `replan_required` →
  `plan_unrealizable_as_designed` correction. Round 2 PASS with every fix
  re-verified against primary evidence. Full record:
  reviews/plan-review.md (delta-round section).
