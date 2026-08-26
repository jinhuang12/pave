# Integration validation — vllm-neuron-parity (Stage 5, 2026-08-26)

Ten steps per pave-init orchestration/review-and-build.md §4, run by the
lead on the integrated package at
`/Users/jinhun/GitHub/NeuronAgenticDevelopment/plugins/vllm-neuron-parity/`
(29 files; an earlier "30" in this record was a miscount, caught by the
final reviewer). Overall: ALL PASS, with one recorded reformat (step 4) and
recorded validator-lag notes (steps 1, 2).

| # | Check | Result |
|---|---|---|
| 1 | Plugin structure (harness validator: plugin-dev:plugin-validator) | PASS |
| 2 | Skill quality (harness validator: plugin-dev:skill-reviewer) | PASS |
| 3 | validate_pave.py on shipped graph | PASS 31/89/5 |
| 4 | validate_traceability.py | PASS 171 rows (after reformat, below) |
| 5 | Shipped script/schema tests | PASS ×2 |
| 6 | Workflow scripts | N/A — none shipped |
| 7 | Hook pass/fail + registration | PASS |
| 8 | Evolving tier (freeze/verify/tamper) | PASS |
| 9 | TODO / broken refs / unapproved files | PASS |
| 10 | README.md + VERSION presence/completeness | PASS |

## Step detail

1. **Plugin structure** — manifest valid JSON/semver, name kebab-case,
   version matches VERSION; 6/6 agents valid with unique names; skill
   frontmatter parses, description 847 chars; all five hook commands
   resolve via `${CLAUDE_SKILL_DIR:-${CLAUDE_PLUGIN_ROOT}/...}`;
   workflow-manifest draft_digest matches shipped graph sha
   (9b61e2…8cf263); `history/vN` absence is the documented pre-freeze
   state (manifest active_revision: null agrees). Three warnings, none
   blocking: (a) `model: "fable"` on rederiver — allow-list lag;
   dispatch risk is disclosed and carried by the retry-then-pause
   contract, and the user declared the deployment issue fixed
   2026-08-26; forward test will exercise a live fable spawn;
   (b) `compatibility` key in plugin.json — undocumented manifest
   field, ignored by the runtime, also present (documented) in SKILL.md
   frontmatter; (c) `effort` agent-frontmatter key — intentional
   carrier for the approved per-node effort pins.
2. **Skill quality** — PASS on frontmatter validity, hooks
   registration (five commands, correct event names, Stop matcher-less),
   description triggering (manual-only + stop-guard blocks-at-most-one-
   stop-in-three disclosure verified against stop-guard.sh mechanics:
   exit 2 once, cooldown STOP_EVERY-1, stop_hook_active honored), and
   path resolution (all cited targets exist; `history/vN` pre-freeze
   state documented). Two minor optional notes, not defects: state the
   plugin-root path base once near Authority; `/vllm-neuron-parity`
   slash invocation relies on skill-as-slash-command support (no
   commands/ dir — by design).
3. **Graph** — `validate_pave.py` on the INTEGRATED
   `workflow.pave.yaml`: PASS, 31 nodes / 89 edges / 5 control
   endpoints; sha identical to approved v0.
4. **Traceability** — first run FAILed 199 errors: the approved
   human-format table (grouped rows, prose types) does not match
   `validate_traceability.py`'s row contract (`| type | id | file |`,
   one row per graph object; non-agent paths resolve inside the skill
   dir, `agents/*` at plugin root). Lead reformatted
   `traceability.md` mechanically from the shipped graph — 171 rows
   (8 roles, 24 evidence, 12 checks, 31 nodes, 89 edges, 5 endpoints,
   2 contracts), meaning unchanged (per-check style and per-node
   model/effort annotations were dropped from the note column; each is
   independently present in the shipped SKILL.md — final reviewer
   verified losslessness), seat/stage context preserved in a
   note column; original archived at
   `planning/archive/traceability-human-format-superseded-2026-08-26.md`.
   Layout impedance recorded: the approved plugin layout places
   references/, schemas/, scripts/, and the canonical graph at the
   plugin root — outside the validator's skill-dir resolution scope —
   so rows realized by those artifacts cite the in-skill citer
   (SKILL.md) and carry the true plugin-root path in the note column.
   Second run: PASS. FOR FINAL REVIEWER: confirm the reformat carries
   the approved table's content without semantic drift.
5. **Tests** — `tests/test_run_state_schema.py` PASS (21-field mirror,
   additionalProperties false, accept/reject/extra-reject) and
   `tests/test_workflow_pave.py` PASS (31/89/5 counts frozen), run as
   the plain scripts their docstrings document. Note: they are
   `__main__` scripts, not pytest collectables — `pytest` collects
   zero; the documented invocation is the contract.
6. **Workflow scripts** — none shipped; the approved graph declares no
   `workflow_script` binding (grep: zero hits). N/A.
7. **Hooks** — functional smoke test (step-1 validator): benign inputs
   → exit 0 silent ×5; prohibited inputs (force-push to main, rm -rf
   on the compile cache, cp -a venv clone) → exit 2 with remediation
   text on stderr; stop-guard and staleness-reminder silent without
   the run marker, per the ownership-evidence design. Registration
   matches the recorded placement: skill frontmatter scope, no
   settings fragment (none required — recorded in SKILL.md).
   Deeper evidence: unit 1's 38/38 behavior matrix in its worktree
   (build/stage5-integration.md).
8. **Evolving tier** — shipped `scripts/freeze_revision.py` exercised
   on a scratch copy of the shipped graph staged as
   `workflow.draft.pave.yaml` (the staging step the SKILL.md evolution
   contract documents): freeze → v1 PASS (manifest rewritten:
   active_revision 1, bundle_digest set); verify clean → PASS; verify
   after tamper (appended line to frozen file) → FAIL digest mismatch,
   exit 1. Scratch removed. Lead states the authority envelope at
   SKILL.md:286-290 (root goal/acceptance, effects, external access,
   interfaces, evidence/check strength never weaker, state authority)
   with the user-approval boundary for anything outside it.
9. **Residue** — zero TODO/TBD/placeholder; zero `.pave/` references;
   broken-reference checks covered by steps 1-2 (all paths resolve);
   file inventory = exactly the plan §1 booked set + the two recorded
   deviations (references/pave.schema.json — validator
   self-containment ruling; README.md + VERSION — lead-rendered per
   approval-briefs.md). 30 files, no unapproved auxiliaries.
10. **Delivered docs** — README.md rendered from the approved plan
    brief per approval-briefs.md section order (intro+install,
    workflow visual with all six faithful stage diagrams, shipped
    file structure, agents table, hooks/enforcement table, appendix),
    updated to what was built (v0-not-v1 revision note, shipped tree,
    permanent-phrasing rederiver row). VERSION seeded 1.0.0 with one
    initial-capability entry. Content accuracy is the final
    reviewer's scope.

Validation proves package coherence, not domain judgment. Open items
carried to the final review: the four R1 bookings (delegate-wrapper
covers both spawn paths in built code; §6/§7 Unit-1 texts greppable in
shipped SKILL.md; three-failure pause in rederiver contract;
substrate-register none-declaration device), P1 bare-push fail-open
residual, unit-2's live-fable-spawn forward-test booking, the step-4
reformat drift check, and README/VERSION accuracy.
