# Skill package plan — vllm-neuron-parity

The delivered unit is a Claude Code plugin, same shape as pave-init.
One flat PAVE profile (no child profiles — every §9.12.1 packaging
condition was rejected; the profile dependency tree is the single root).
Evolving tier APPROVED at Stage 1 — the revision workspace ships.

## 1. Package layout

```
vllm-neuron-parity/                      # plugin root
  .claude-plugin/plugin.json             # name, description, version
  skills/vllm-neuron-parity/
    SKILL.md                             # lead workflow skill
    hooks/                               # per enforcement record
      protected-branch-guard.sh          # P1 (blocking)
      compile-cache-guard.sh             # P2 (blocking)
      venv-opt-guard.sh                  # P3 (blocking)
      state-staleness-reminder.sh        # lead-alignment pair
      stop-guard.sh                      # lead-alignment pair
  agents/
    investigator.md                      # opus
    implementer.md                       # opus
    measurer.md                          # opus
    adjudicator.md                       # opus
    adversarial-reviewer.md              # opus
    rederiver.md                         # model: fable, effort xhigh (user-directed pin, 2026-08-26)
  workflow.pave.yaml                     # canonical graph (v1 freeze)
  workflow-manifest.yaml                 # evolving-tier lineage
  history/                               # frozen revisions
  references/
    artifact-layout.md                   # from build/artifact-layout-reference.md
    measurement-pitfalls.md              # built domain reference (item 30)
    patch-mechanism-inventory.md         # built domain reference (item 30)
    collision-ranking.md                 # built domain reference (item 30)
  schemas/
    run-state.schema.json                # single shape authority
  scripts/
    validate_run_state.py
    validate_pave.py
    freeze_revision.py                   # evolving tier (copied)
  README.md                              # rendered view, never authority
  VERSION
```

## 2. Role -> agent mapping (model tiers per fragment assignments)

| Role | Realization | Model | Effort notes |
|---|---|---|---|
| lead | the generated SKILL.md itself (session model) | session | gate presentation, routing, all lead-evaluated checks, serialized state writes |
| user | human at the three gates + pauses | — | approvals recorded verbatim in run state |
| investigator | agents/investigator.md | opus | high; medium at verify_run_preconditions + assemble_delta_report |
| implementer | agents/implementer.md | opus | high; xhigh at execute_attempt_loop; medium at capture-class + record_changeset + acquire + preregister |
| measurer | agents/measurer.md | opus | medium; stabilize_and_package_evidence dispatches on sonnet |
| adjudicator | agents/adjudicator.md | opus | high (adjudicate_results, verify_run_closure) |
| adversarial_reviewer | agents/adversarial-reviewer.md | opus | high, all five review nodes; fresh seat per gate round |
| rederive_approach seat | agents/rederiver.md | fable | xhigh — the breaker's landing node redirects a campaign's remaining spend; top model at top effort by user direction (2026-08-26; the previously recorded fable dispatch failure is fixed on this deployment, superseding the inherit-session design R1's HIGH-1 produced). Operational fallback stays recorded: if a fable spawn fails with an intermittent 400, retry the spawn identically; after three identical failures, pause for the operator rather than substituting — never downgrade the seat; an undispatchable seat would dead-end all sixteen recovery routes, so the lead retries, then pauses, never substitutes. |
| delegate_dispatcher | capability of dispatching roles via the run-wide guardrail wrapper; not a separate agent | — | every spawned seat, teammate or sub-agent, inherits the dispatching node's forbidden_effects |

The lead pauses when a `vllm-neuron-parity:*` agent type is unavailable;
it never substitutes an ordinary worker.

Dispatch mechanics (user-directed, 2026-08-26): the primary seat for
each node instance is spawned as a NAMED TEAMMATE (background,
continuable via SendMessage), one per node instance, and retired when
its node instance closes — a dropped connection resumes the same seat
instead of losing its context, and repair rounds continue the seat
that did the work (doer seats; reviewer seats stay fresh per gate
round). One-shot subagents remain the mechanism for a
seat's internal fan-out (delegate skills through the guardrail
wrapper, read-only exploration). Teammate status changes continuity,
never authority: the lead stays the single state writer (P10),
teammates return results to the lead and never traverse edges or
present gates, forbidden-effects inheritance is unchanged, and a peer
message never grants a permission escalation.

## 3. Enforcement record

The full §9.14.1 record is `build/enforcement-record.md` (promoted into
the plan bundle): 13 run-wide prohibitions P1-P13 with rungs and
why-not-stronger; 6 costly-transition guards; the CPU-mode evidence
chain; the lead-alignment hook pair entry (no omission condition).
Hooks P1-P3 register in generated-skill frontmatter scope; the plugin
declares the hook runtime dependency in compatibility metadata and the
lead-skill `description` discloses registration and the stop-guard's
blocks-a-stop-once behavior. No settings fragment is required — no rule
exceeds frontmatter scope.

## 4. State

Long-horizon workflow: working memory is the file system. One
machine-checkable `run-state.schema.json` is the single shape authority
(fields mirror the graph's `state.required`; prose never restates the
list) plus `validate_run_state.py`. Resume is reconciliation: reread
state, check against artifacts on disk, continue from the last
satisfied gate — written into the generated lead. The approved graph
governs design and review; the run's own persisted state governs an
active run.

## 5. Built domain references (item 30)

Three load-bearing exploration findings ship as BUILT references
(exploration/*.md and system-map.md do NOT ship): measurement pitfalls
(stock serving-bench undercount, decode-only connector), patch-mechanism
inventory, collision ranking (runner/platform/scheduler surfaces).
Runtime DoD/hardening text cites these built paths; the 34(a) final-gate
sweep greps the built package for `references/<topic>.md` placeholders
and any surviving `.pave/` path.

## 6. Build units (Stage 5, non-overlapping)

1. Lead skill + hooks (SKILL.md, hooks/, plugin.json, README, VERSION).
2. Agents (six role contracts).
3. Canonical graph + schemas + scripts (workflow.pave.yaml freeze,
   run-state schema, validators, revision workspace).
4. References (artifact-layout + three built domain references).

One writer per shared file; worktree isolation inside this repo.

Unit-1 lead duty (user-directed, 2026-08-26): the generated lead
carries pave-spec §9.8's default-recovery loop for undeclared failures
in its own prose — the generated plugin does not ship pave-spec, so a
citation would dangle. The loop: retry once when the failure looks
transient (a failed retry is a real failure); investigate to root
cause with a persisted investigation record, opening on cheap priors
(documentation, release notes, issue trackers, the failure text
searched in public sources — priors direct the first expensive
measurement, they never settle acceptance); match process weight to
what investigation found (one credible fix, mechanical to apply →
implement and verify; a fix that needs design, or several candidates
→ plan the fix and review the plan before implementing; many
competing fixes with real trade-offs, or a cause that resists
localization → investigate candidates in parallel, then select
through independent challenge, preferring correctness then
simplicity); re-prove by the world — the evidence that failed must
now pass. Two early exits, both honest results: replan
when the root cause is the plan itself; pause or blocked only when
investigation itself is blocked (unreproducible, evidence
unreachable). Recovery work stays inside the failing node's meaning —
it never invents outcomes or edges.

## 7. Evolution contract (evolving tier)

v0 = approved draft at delivery; v1 freeze via freeze_revision.py into
history/ with workflow-manifest.yaml lineage per
references/pave-revisions.md. Update runs start from the frozen active
revision, never the draft.

Successor rule 4 ships at the pave-revisions.md 2.2.9 wording
(user-directed, 2026-08-26; installed 2.2.8 reference predates it):
the semantic diff's why must name the gap the predecessor's plan
missed and state why the change holds for runs in general — a
successor ships to every future run, so a fix shaped to one run's
particulars degrades all the others; a reason that cannot be stated
apart from the triggering run is a review finding, enforced by rule
5's independent review (self-review never qualifies). Unit-1
obligation: the generated lead's evolution contract carries this
wording verbatim (the lead SKILL.md owns the contract text; unit 3
ships only the workspace machinery — draft, manifest, history/,
freeze script).
