# Enforcement record — vllm-neuron-parity (§9.14.1)

Every run-wide prohibition and costly-transition guard, each with its
enforcement rung and why a stronger rung is unnecessary. Rungs, weakest
to strongest: prose (contract text) < reinjection (hook re-presents the
rule) < reviewed/socratic (non-doer judges) < mechanical check <
blocking hook. Blocking requires: violation likely, costly, irreversible
before the next required gate, and precisely detectable. (Register
items 6, 15; fragment enforcement blocks; c11, c13.)

## 1. Run-wide prohibitions

| # | Prohibition | Rung | Why not stronger |
|---|---|---|---|
| P1 | Never mutate protected base branches (release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline) on the fork or upstream | BLOCKING hook (pre-push/branch guard) | Strongest rung justified: likely (agents run git constantly), costly, irreversible before any gate, precisely detectable (branch name match). |
| P2 | Never clear the shared Neuron compile cache ($VLLM_CACHE_ROOT/neuron/compile_cache, ~/.cache/vllm/neuron/compile_cache, /var/tmp/neuron-compile-cache) | BLOCKING hook (command-pattern guard) + delegate-wrapper interception | Documented serving bring-up remedies INCLUDE cache clearing, so the violation is likely under delegation; hours of recompile for every tenant = costly; path patterns are precise. The delegate guardrail wrapper intercepts every spawned seat the hook scope might miss, teammate or sub-agent. |
| P3 | No `cp -a` venv cloning; no pip writes into /opt | BLOCKING hook (command-pattern guard) | Same shape as P2: the venv-replication dead-end pressure makes the shortcut likely (item 15 run-wide reflection of realize_increment's addition); /opt damage breaks co-tenants; patterns precise. |
| P4 | Zero neuronx_distributed* (NxDI) imports in ported code | MECHANICAL check (import scan over added-modified lines per layout §4.6) at record_changeset + review_implementation a3 | A hook on file writes would false-positive on reading/vendored context; the scan is exact over the diff, runs before the gate that matters (impl review), and the re-run instrument re-checks on repair. Violation is detectable-at-rest, not irreversible. |
| P5 | GPU baseline: read-only; no autonomous reboot/reset; durable-host-state scoping per layout §4.10 (cache writes named; ephemeral serving lifecycle allowed) | Contract text + REINJECTION + mechanical skew/identity probes (capture refuses on kickoff-record contradiction) | A blocking hook cannot see remote SSH side effects precisely; the capture node's identity-verify + refuse activities are the precise detector; the reviewed rung (adjudication provenance) catches fabrication. |
| P6 | Benchmark skill's provisioning STOP gate never removed | Prose + REINJECTION (rule rides every hardware brief) | Removal is a text edit in a delegate brief - unlikely, cheap to catch at review, reversible; hook machinery would be ceremony (§4.11). |
| P7 | PRs only to the jinhuang12/vllm-neuron fork; merge stays human; fork sync manual (user-owned) | MECHANICAL check (closure_evidence_settled verifies PR URL resolvable ON THE FORK) + gate-3 user approval | The world-produced PR URL is the precise detector; the human-merge rule is enforced by never granting merge authority (capability absence beats any hook). |
| P8 | Identical hardware retry forbidden (fingerprint gate) | MECHANICAL: tier-1 gate reads layout §4.2 pair 5 (repo fingerprint file + this-run attempt-log) | Precisely detectable by fingerprint equality; the pair's format falsifier is recorded in the layout entry; blocking a retry pre-attempt is exactly a routing precondition, not a hook. |
| P9 | Comparators never chosen or altered after measurement begins | MECHANICAL comparators_preregistered (timestamp precedes every measurement artifact) + forbidden_effects at all four measure children + adjudication reads registration digest (§4.5) | Registration-timestamp arithmetic is exact; a hook cannot judge comparator identity. |
| P10 | Lead single-writer: run state + cross-run artifacts + lease records | Structure (no other role holds write paths, §2 of layout) + validate_run_state.py on every write | Ownership-by-structure is stronger than detection; derived counters (budgets, allowances, re-trace grants) are COUNTED from event files precisely so no child ever needs state writes. |
| P11 | Measured revision: git-issued identifier at measurement time, never a branch name | MECHANICAL revision_stamped check on runs_to_stabilization | Exact string-shape + agreement test; nothing stronger exists. |
| P12 | Emit only declared outcomes; traverse only declared edges | validate_pave.py at build + run-state schema validation + lead-alignment hook pair (below) | The graph + schema are the authority; runtime drift is caught by the staleness/stop hooks re-presenting the position. |
| P13 | Kernel-substrate rule: new kernel-class functionality the existing Neuron NKI library does not already provide is implemented in NKI, never as torch-level fallback (user-directed amendment, 2026-08-26) | SPLIT RUNG. MECHANICAL for declaration presence (assemble_design_record's completeness self-check requires the substrate register with an explicit non-kernel-class declaration per undeclared increment — mirrors the patch-decision register's none-declaration device) and for fidelity (record_changeset's substrate-fidelity check: every design-declared-NKI increment's diff shows NKI usage; hit = coverage-gap class (d)). REVIEWED for classification (both gate rubrics challenge the classification itself — an increment recorded non-kernel-class whose work is kernel-class is a material finding). | Classification is irreducibly judgment — no scan decides what should have been a kernel, and an absence-of-torch predicate false-fires on legitimate boundary plumbing (an NKI kernel imports torch at its edges). Fidelity therefore uses a PRESENCE predicate on the doer's own declaration: zero NKI usage in a declared-NKI increment is an exact contradiction, precisely detectable in §9.14.1's sense, riding the changeset-scan machinery that already ships (no new subsystem, §4.11 satisfied). Non-declaration — the remaining escape once fidelity is mechanical — is closed by the register's mandatory every-increment decision. Residual: a wrong classification that survives both reviews; bounded by gate-2 user approval of the presented register. |

## 2. Costly-transition guards

| Guard | Site | Rung | Justification (blocking test) |
|---|---|---|---|
| budget_spent_routing_check | inside run_candidate_measurements / stabilize | BLOCKING procedural precondition (no backward route at either tier's threshold) | Likely (stubborn defect re-presents), costly (each lap re-traverses leased hardware), precisely detectable (path counts per layout §4.4). |
| bound-exhaustion guard (scan re-trace) | assemble_delta_report | BLOCKING precondition on grant issuance | Grant files by scan-entry id = precise; re-trace on leased time = costly. |
| hardware breaker (tenth attempt / tier-1 / venv dead end, c7 three-condition) | execute_attempt_loop / replicate_campaign_venv | BLOCKING precondition (breaker_tripped routes out) | Attempt counts + fingerprint reads are exact; a run-away attempt loop is the costliest failure in the graph. |
| pin_infeasibility_socratic_guard | screen_infeasibility_to_record edge | SOCRATIC (lead evaluates; never the note author) | Campaign-ending transition; mechanical grep would false-pass a moved-not-removed surface (wrong-match stranding); bound = falls through to the progress screen, no counter. |
| design_approved_by_user / kickoff_contract_approved / gate-3 approval | gate edges | USER gate (reviewed) | Approval is the user's by definition; verbatim records in run state. |
| recovery allowance / flap bound | recover_leased_host + acquire | MECHANICAL derivation from recovery records (layout §4.2 pair 6; never a stored counter) | Derived-from-events is re-checkable by any reader; c10 exclusion applied at acquisition. |

## 3. Evidence-chain rules (not point checks)

- **CPU-mode evidence chain (item 6)**: per-increment pre-hardware
  evidence = provenance (command transcript with exit code, layout
  §4.6) -> non-doer re-check (the recomputed gap instrument / a5
  re-run) -> adversarial review. Review ALONE is never the chain.
- **Increment acceptance rung accuracy (item 15)**: command exit = rung
  1 (world-produced); test ADEQUACY = rung 2 via review — records never
  claim rung-1 authority for adequacy.
- **Exclusivity is not exhaustiveness** (reviewer standing note): every
  outcome-precedence declaration also states coverage; audited per
  fragment at the whole-bundle round.

## 4. Lead-alignment hook pair (default entry)

The standard pair from pave-init `references/lead-alignment-hooks.md`
ships with the generated plugin, adapted to its run-state location and
terminal signal:
- state-staleness reminder (periodic socratic reinjection of position +
  unrecorded-outcome check),
- stop guard (blocks-a-stop-once when the run is active and state says
  mid-node; disclosed in the generated skill description).
No omission condition applies — this is a long-horizon, session-crossing
workflow, exactly the pair's target case. The `.pave-init-run`-style
marker mechanism maps to the generated plugin's own run marker.

## 5. Merged tidies (item 15) and residuals

- realize_increment's /opt + venv-clone prohibition is reflected
  run-wide as P3 (one rule, one table row - not per-node copies).
- no_progress -> rederive_approach wording: rederive reaches real stops
  via the monotone-exhaustion argument (recorded non-filed residual;
  distinct from scan's corrected :228 mislabel).
- c12 residual on record: baseline_unusable's name now also covers a
  design-side digest mismatch first surfacing at capture
  (smallest-change ruling; destination right).
- g4 residual, accepted at the REVIEWED rung (R1 E5 LOW-1, 2026-08-26):
  the budget instrument counts defect-record files written inside the
  writers' own legal scope, so a seat could file redundant non-novel
  records in the right place and reach tier-1 exhaustion without doing
  the repairs. Why not stronger: the escape buys the doer MORE work and
  user visibility (re-derivation + gate re-presentation), and the
  fabrication leaves a recomputable signature - identical cited
  file-sets with no intervening 27(t) procedure revision -
  visible to rederive_approach (consumes the measurement artifacts)
  and to adjudication. §4.4's reduction claim stays as written.
