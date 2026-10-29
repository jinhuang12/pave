# Audit Protocol (T_AUDIT)

Every audit is a fresh, independent sanity gate. The auditor first rebuilds what
the stage really did from primary evidence, and reads the institutional checklist
only after that. A stage may advance only after its audit produces a genuine
final `PASS`. This file owns dispatch,
triggers, remediation, and state recording; the lead applies it at every gate.

Two other files own the rest, so do not restate the auditor role here. The
auditor's reasoning, evidence, severity, and output contract live in
`agents/ammo-auditor.md`. The stage checklists live in
`references/audit-invariants.md`, and the hook delivers them only after the
auditor writes Phase 1.

## Mandatory Triggers

Spawn an auditor at every trigger below. No outcome ever earns an auto-pass.

| Trigger | Fire after | PASS state key |
|---|---|---|
| `T_AUDIT_S1` | Stage 1 baseline capture | `audit.stage_1.passed_at` |
| `T_AUDIT_S2` | Stage 2 bottleneck mining | `audit.stage_2.passed_at` |
| `T_AUDIT_S45` | every Stage 4-5 track is terminal (`PASS`, `GATED_PASS`, or `FAIL`) | `audit.stage_45.passed_at` |
| `T_AUDIT_S67` | SHIP: merge, environment promotion, and golden capture are complete; EXHAUSTED: integration outcome is recorded | `audit.stage_67.passed_at` |

Stage 3 gets no audit of its own, because its cross-critique, rebuttal, and
open-item protocol is already adversarial. This single exception weakens none of
the four triggers above.

Leave transition machinery to `scripts/ammo_state.py`. It is the sole authority
for transition enforcement and new-round requirements. Do not restate those
branches here.

## Dispatch

Spawn a new `ammo-auditor` instance for every gate and for every re-audit. Never
reuse an auditor. Never hand it an earlier audit's conclusions as facts.

```python
Agent(
    subagent_type="ammo:ammo-auditor",
    prompt=f"""
    plugin_root: {plugin_root}
    task: audit_gate
    artifact_dir: {artifact_dir}
    stage: {stage}       # stage_1 | stage_2 | stage_45 | stage_67
    round: {round}
    cycle: {cycle}
    """,
)
```

On Codex, `references/harness-binding.md` gives the spawn form. Use the task
name `audit_{stage}_r{round}_c{cycle}`, send the same lines, and add the
`recovery_note_path` line that the binding names.

Keep the prompt that thin. The auditor uses the exact supplied artifact directory
and resolves canonical paths from it. Its authority and workflow come from
`agents/ammo-auditor.md`; do not copy them into the dispatch prompt.

The lead may continue unrelated work while an audit reads evidence, but must not
perform the gated transition.

## Started-At Stamping

`audit.{stage}.started_at` and `.cycle` are stamped mechanically, not by the
lead. The `PostToolUse` hook `ammo-audit-start-stamp.sh` reads this dispatch
prompt when the `ammo-auditor` Agent call returns and writes both fields, so for
a foreground auditor `started_at` holds the return time. This is why the prompt
format above must not change. On Codex,
`codex/hooks/post_tool_use_guard.py` stamps the same fields from the dispatch
fields or the audit task name. `passed_at` stays
lead-written, after the lead verifies the final PASS verdict. State validation
rejects a gate with `passed_at` set and `started_at` missing; backfill it with
`ammo_state.py audit-started --artifact-dir DIR --stage {stage} --round {round} --cycle {cycle}`.

## Verdict Handling

The verdict file, not an auditor message, is authoritative for the audit result:

- `PASS`: no `BLOCKING` or `HIGH` finding remains. Only now may the lead record
  PASS metadata and continue.
- `BLOCKED`: at least one `BLOCKING` finding remains. Apply a bounded repair and
  re-audit.
- `NEEDS_INVESTIGATION`: consequential uncertainty remains. Dispatch a bounded
  `pave-init:adversarial-investigator`, resolve its severity, and re-audit; do not advance or stamp
  PASS.

Reject a `BLOCKING` rating that names no campaign-impact mechanism
(`agents/ammo-auditor.md` § Severity Classification). Re-audit it instead of
sending it to a fix owner, because the doubt is in the rating.

A PASS is genuine only when the verdict contains the independent Phase 1, the
hook-delivered Phase 2, reconciliation against primary evidence, and an explicit
final `Overall: PASS`. This file defines no separate attestation record: PASS
provenance is the complete verdict plus the state record that points to it.

After verifying that final PASS, the lead records both fields in the current
round:

```text
audit.{stage}.passed_at = <ISO-8601 UTC>
audit.{stage}.verdict_file = "rounds/{N}/audits/{verdict_filename}"
```

Never stamp either field from a partial file, an auditor message, a Phase 1-only
file, or a verdict that still contains `BLOCKING` or `HIGH` findings.

## Repair and Re-audit

Send each finding to the narrowest capable fix owner. Pick that owner from the
finding's evidence, category, and scope. The auditor never repairs its own
finding, and the lead never repairs a stage deliverable inline. Mining-artifact
findings go to `ammo-researcher`, which owns the derivation chain and regenerates
tables from primary data instead of hand-patching values. After a repair, spawn a
fresh auditor with the same stage and round; it must verify the repair from
primary evidence and look for collateral damage.

An attributable Stage-67 track rejection needs the track's own implementer.
Immediately resume or spawn that track's `ammo-impl-champion` in an isolated
repair worktree to remove its code and environment contract. Use `ammo-resolver`
only when reconstructing the remaining composition introduces a merge/semantic
conflict. The lead owns state and promotion bookkeeping but does not edit source.
Preserve the track's historical Stage-5 verdict, and record the promotion
rejection in `dropped`, `integration.final_decision`, and the round summary
before re-audit.

Allow at most three audit-fix cycles at a gate. If blockers remain, keep the
narrowest safe scope, and keep no rejected code:

- At Stage 45, if every blocker is attributable to named `op_id` values,
  quarantine those tracks as `FAIL` with an `audit_escalation` reason, record a
  track-scoped `campaign.auditor_escalation`, and audit the surviving scope.
- At Stage 67, code and environment are already promoted, so a track-scoped
  blocker requires removing the implicated change and environment contract,
  rerunning integration correctness, E2E, activation, and golden capture, then
  auditing that reconstructed scope. Merely marking the track `FAIL` is never
  sufficient. If safe attribution or reconstruction is unavailable, pause as
  campaign-scoped.
- If any blocker affects shared baseline, environment, workflow, decision,
  integration, or campaign evidence, set `campaign.status = "paused"` and record
  a campaign-scoped `campaign.auditor_escalation` with a resumable task.
- If scope is uncertain, treat it as campaign-scoped.

The post-quarantine audit may stamp PASS only if the remaining scope genuinely
passes. If it exposes a campaign-scoped blocker, pause instead.

## Artifact Paths

All new verdicts are round-scoped:

```text
rounds/{N}/audits/stage_1.md
rounds/{N}/audits/stage_2.md
rounds/{N}/audits/stage_45.md
rounds/{N}/audits/stage_67.md
rounds/{N}/audits/{stage}_cycle_{C}.md
```

The first audit uses the unadorned filename; re-audits add `_cycle_{C}`. The
auditor writes only its assigned cycle file, and `verdict_file` names the
passing one. See `references/artifact-layout.md` for the full tree.

## References

- `agents/ammo-auditor.md`: independent reconstruction, delegate fanout,
  severity, and verdict contract
- `agents/ammo-delegate.md`: bounded evidence helper
- `references/audit-invariants.md`: hook-delivered Phase 2 checklist
- `references/artifact-layout.md`: canonical artifact locations
- `scripts/ammo_state.py`: state validation, compatibility, and transitions
- `hooks/inject-audit-phase2.sh` and `hooks/ammo-next-step-reminder.sh`: Phase 2 delivery and audit reminders
- `hooks/ammo-audit-start-stamp.sh`: stamps `started_at`/`cycle` from the auditor dispatch prompt
