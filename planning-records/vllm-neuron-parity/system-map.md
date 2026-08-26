# System map - vllm-neuron-parity

Synthesized 2026-08-25 from six verified exploration reports (`exploration/*.md`);
every load-bearing claim below was spot-checked by the lead against primary
files. Citations live in the lens reports; this map points rather than re-cites.

## ADDENDUM 2026-08-25 — Neuron 2.32 re-baseline delta (7th lens, verified)

`exploration/plugin-024-delta.md` (lead spot checks all held via GitHub API:
fork branch/commit, 0.24 core.txt, deleted dirs, patches stub, MRv2 guard).
Supersedes 0.21-era facts below where they conflict; body text left intact
for provenance.

1. PROVENANCE CORRECTION: target-plugin.md surveyed the FORK on
   `feature/p-eagle-gpt-oss-20b` (@0e19f00), not upstream 0.21. The "7
   spec-decode test files" are fork-only P-EAGLE artifacts (+
   `functional/parallel_draft_inputs.py`); upstream ships ZERO test/ci at
   BOTH versions. Consequence: the fork overlay (8 files) must be re-applied
   on the 0.24 rebase; "near-zero regression safety net" is even truer.
2. Surfaces persist and grew: runner 8278→9086 lines (now TWO mixins — new
   `NeuronECConnectorModelRunnerMixin`), worker 1909→2225, scheduler
   1025→1084, platform 922→938. Monkeypatches now FOUR (new
   pin_memory_patch.py, same ad hoc pattern); `apply_patches()` stub still
   empty, docstring now specifically wrong. Collision-surface ranking
   carries forward, stakes slightly higher.
3. COMPILE SUBSYSTEM LEFT THE REPO: `compile/` (9 files), `fx_passes/` (8),
   `overrides/` (3) deleted; `nki/` gutted to a bare `__init__.py`; new
   unpinned core dep `libtorch-neuronx-lite` resolves ONLY from AWS's
   private Neuron index (public PyPI = placeholder). Two consequences:
   (a) freeze-replicate venv recipe gains a hard precondition — Neuron
   extra-index-url or DLAMI-preinstalled check, else silent placeholder;
   (b) campaigns can no longer patch FX-pass/compile behavior in-repo —
   compile-touching targets need re-scoping or an upstream-issue path.
   (New fx_passes_design.md doc describes the deleted tree — stale docs.)
4. Feature baseline: Llama-3 + Qwen3-Embedding-8B (pooling — new capability
   class) onboarded; Qwen3-VL MXFP8 (Trn3) closed with real model files;
   NEW disaggregated ENCODER (EPD) distinct from P/D disagg; LoRA
   definitively unsupported (explicit TODOs, runner lines 382/409/1289);
   PP still ❌ everywhere checked. Caveat: the doc reorg (matrix →
   per-model recipes) DROPPED several ❌ rows rather than flipping them —
   absence of a row is not support. Flagged possible regression: Qwen3-VL
   segmented prefill ✅→❌ (doc-only evidence, unverified against code).
5. Validation: 3-level framework core unchanged (KV BC ≥ 0.99 verbatim);
   accuracy/ grew 17→41 files via NEW accuracy_debugger/ orchestration
   pipeline + goldens/ + snapshot/ (internals unread — two risk tiers:
   stable primitives vs unread orchestration). lm-eval moved core→test
   extra; nixl pinned ==1.3.2.
6. MRv2: plugin already ships a fail-fast guard `_reject_v2_model_runner`
   (platform.py:287, called at :309-311; named crash mode
   CachedRequestData.all_token_ids → EngineDeadError; env default "0" at
   __init__.py:91). The v0.25 PagedAttention-removal/MRv2-default boundary
   one release above the new pin was already verified first-hand by the
   upstream lens — the delta report's web-only caveat is closed by
   cross-reference to exploration/upstream-and-tpu-sources.md.
7. NEW OPEN ITEM: Neuron SDK 2.32 release notes mention a
   `neuron-framework-autoport-vllm-neuron` "Neuron Agentic Development
   skill" — adjacent to our absorbed autoport machinery; competing tool,
   prerequisite, or irrelevant? Route to delegates/tooling scoping at
   intake planning.

## Target and boundaries

- Target system: the vLLM-Neuron platform plugin at /Users/jinhun/GitHub/vllm-neuron
  (fork jinhuang12/vllm-neuron), pinned exactly `vllm==0.21.0`, registered via
  the `vllm.platform_plugins` entry point → one hardcoded `NeuronPlatform` class
  path. Reference sources for parity: upstream vllm-project/vllm (local full
  clone exists at /Users/jinhun/GitHub/vllm, tags through v0.28.0 — upstream has
  already moved past the brief's v0.27.1) and vllm-project/tpu-inference (A2,
  via `gh` — no local clone).
- Boundary correction (highest-impact exploration finding): there is NO in-tree
  torch-xla TPU backend at or near the pin — deleted at v0.14.0, 4 months before
  v0.21.0. A2 sourcing is tpu-inference-only, and both its front-ends (torch via
  torchax, jax) lower to JAX/XLA kernels: design-port, not code-vendor.
- The generated plugin writes only: campaign worktrees/branches of the fork,
  PRs on the fork, per-campaign venvs + evidence dirs on hosts, and the
  repo-tracked scorecard/backlog/ledger/fingerprint artifacts in the
  NeuronAgenticDevelopment repo.

## Components and authorities

- Plugin integration surfaces (target-plugin lens): entry point (narrow) →
  `NeuronPlatform` (922 lines, wide fan-in: config mutation, quantization
  gating, DCP validation, attention-backend selection, 3 live monkeypatch
  mechanisms) → `NeuronModelRunner` (8278-line file, one ~7900-line class:
  load, warmup/compile, ~2000-line execute_model, spec decode, KV cache,
  encoder cache, capture) → `NeuronWorker` (1909) → `NeuronScheduler` (1025).
  NIXL + decode-bench connectors are cleanly isolated subclasses (low
  collision). A "centralized patches" module exists but is an empty stub —
  patches are applied ad hoc.
- Absorbed engines: feature-port skill (flat static graph, 20 nodes/59 edges,
  5 hooks + consent-gated blocking deny fragment) and the model-port/autoport
  released bundle (root graph + 5 sub-workflows, 44 nodes, digest-addressed
  static-release.yaml with approval_record — the structural ancestor for a
  two-campaign-type, evolving-tier plugin). The autoport `authorize-and-lease`
  sub-workflow (verify_resource_ownership / verify_resource_entitlement) is a
  working precedent for the hardware-queue lease.
- Delegates (delegates-and-runtime lens): 18 skills verified. Key authority
  facts: equivalence adapter hard-pinned to 0.24 (clean early exit on 0.21) and
  its auto-detect DEFAULTS TO THE FORBIDDEN NxDI stack when `--target-stack` is
  omitted; benchmark skill's primary happy path is self-provisioning (STOP gate
  at SKILL.md:216 is a spend gate, not a provisioning ban — the plugin's ban is
  stricter and must be enforced independently); two delegates (profiling,
  compiler-debugging) PRESCRIBE `rm -rf ~/.cache/vllm/neuron/compile_cache` as
  first-line remedies — direct conflict with forbidden effect #2 that must be
  intercepted at dispatch; contribution-validation delegate validates NAD-repo
  artifacts, NOT plugin PRs (doubly-sourced; use the absorbed skill's self-owned
  contribution-checklist.md instead); runtime-troubleshooting is a 73-line
  generic cheat sheet with no recovery/lease logic (the brief over-credited it);
  hardware-entitlement imports parallelism-planner's scripts (co-location
  dependency). Delegates reach sessions via NAD's deploy-to-~/.claude mechanism
  — the generated plugin CANNOT bundle them; they are an external dependency to
  preflight, and skill addressing may be namespaced (`nki-dev-suite:` prefix
  seen; unresolved).
- User authority: 3 gates per campaign (kickoff contract, design approval,
  close-out); criterion changes only by explicit user decision; PR merge human.

## Current work sequence

Before this plugin (as evidenced, not as documented): campaigns ran under two
separate engines. Real sequence observed in P-EAGLE (fullest trace):
environment preflight over one-shot ssh → editable install INTO the shared /opt
DLAMI venv (now forbidden; caused documented cross-campaign contamination
caught by deepseek's preflight) → CPU-first increments → hardware
compile/serve attempts with failure fingerprints → correctness gate
(greedy-equality + acceptance-floor fallback after the equivalence delegate
hard-exited on version mismatch; decisive sequential-control experiment) →
benefit gate (~8 re-entries, manual streaming harness after rejecting
`vllm bench serve` for a real measurement bug) → wrong-baseline incident (user
caught it) → no-benefit close-out with DMA-bound root cause. GLM-5.2 model port
built a forensic 4-phase GPU-oracle capture tool; closed at ~45% token-exact
(downgraded to "semantic smoke pass") with NO perf gate ever run. deepseek is
parked mid-graph at `authorize_and_lease` (attempt 12, two live hardware
blockers), genuinely open.

## Evidence flow

- Correctness evidence: 3-level accuracy framework exists in-repo (lm_eval
  floors; three-way HF-FP32/HF-BF16/Neuron logit top-k, thresholds in code
  `pp_static_thresholds [0.03,0.05]`, `agg_bc_threshold 0.99`; KV-cache BC) —
  but NO campaign ever invoked it; every real gate used ad hoc harnesses.
  Logit/KV comparison is HF-reference-based; no GPU-vLLM logit-capture path
  exists anywhere (GLM's oracle captures token IDs/text only).
- Perf evidence: `vllm bench serve` documented but never observed used;
  P-EAGLE explicitly bypassed it (chunk-count undercounts spec configs);
  `NeuronDecodeBenchConnector` exists, never observed invoked, and explicitly
  does NOT check correctness when used.
- LOAD-BEARING GAP: the brief's "GPU-vLLM baseline serves lm_eval parity,
  greedy token-match, and the perf gate" is design intent — done once,
  forensically, by hand (GLM oracle), never as a repeatable procedure. The
  shared validation back-end is NEW CONSTRUCTION.
- Adjudication failure modes observed (both real): (1) wrong-baseline — the
  measuring agent picked its own comparators across re-entries until the user
  intervened; (2) watcher/evidence race — completion signal read before
  artifacts materialized, producing a false `failed` (gates need
  stable-reads-before-verdict, not first-sighting).
- Upstream traceability: release notes are PR-linked and high quality; RFC/mrv2
  labels searchable. Headline features trace cheaply; diffuse features (MTP, KV
  offload) need `git log --grep/-S` sweeps. MRv2 entanglement is mechanically
  detectable (config oracle forcing conditions + legacy-runner grep) — a
  repeatable check the gap-analysis stage can encode.

## State and persistence

- Feature-port: 15-required-field schema, whole-document rewrite protocol, sole
  lead writer, H1 observing validation. The schema was NEVER exercised against
  a real run (both real campaign files predate it; validator fail-closed on
  missing jsonschema silently degraded in the field).
- Model-port: far richer 35-key campaign-state (resource_leases,
  effect_authorizations, child_runs, evidence_invalidations, integration
  transactions) + one-file-per-event artifacts/{failures,recovery,events}
  pattern + campaign-state.json.lock.
- Confirmed state failures (reproduced first-hand): corrupt JSON via
  append-splice at line 90 (same file also carries an invented node name
  `increment_implement`); the 19.2 MB resume file — 86% is ONE unbounded inline
  `traversal_history` field rewritten whole per resume. Bloat is specific to
  inlined logs, not event-logging per se: the one-file-per-event pattern in the
  same campaign stayed small.
- Cross-run artifacts (scorecard/backlog/ledger/fingerprints) have NO precedent
  — new construction, single-writer, in this workflow repo.

## Failure and recovery

- Fingerprint no-identical-retry: independently invented by BOTH engines
  (feature-port `attempt_budgets.per_fingerprint_retry_bound: 3` field-tested;
  autoport's dedicated diagnose-failure sub-workflow) — the two-tier breaker's
  tier 1 generalizes; tier 2 (10-attempt first-principles review) is new.
- Hardware recovery: NO reusable queue/lease tool exists anywhere. Closest
  precedents: deepseek's host-migration authority JSON (instance-id + hostname,
  sequential lease-release-then-reacquire; no boot-ID anywhere in history) and
  the in-plugin fcntl-flock core_allocator (single-host pytest scope — a
  technique precedent only). The hardware queue is new construction.
- deepseek pause: parked at authorize_and_lease with two live blockers (8
  concurrent compiler jobs exhaust the 2 TiB host; NEFF-load allocation failure)
  — a real carry-over the new plugin must adopt or close.
- Environment recovery traps: two delegates' documented remedies violate the
  cache-clear prohibition; the compile cache itself is filelock-per-key +
  atomic-rename, multi-writer-safe by design — never a scratch dir.

## Concurrency and resources

- Venv isolation: freeze-replicate has NEVER been executed anywhere in history
  (first run must self-verify; edit-live probe mechanics are proven, only the
  target venv changes). DLAMI venv path suffix is inconsistent across docs —
  discover per host, never hardcode. Plugin dependency metadata omits the whole
  Neuron SDK stack (verified in requirements/core.txt) — the freeze-replicate
  rationale is grounded.
- Compile cache: shared, multi-writer-safe (FileLock 0.001s acquire +
  wait-for-completion; atomic rename promotion; NKI cache uses a separate
  blocking-timeout lock). Campaigns can share one cache root; the queue does
  not need to serialize it.
- Collision surfaces for conflict-aware scheduling: `neuron_model_runner.py`
  (near-certain overlap for any two runtime-behavior campaigns) >
  `platform.py` (config/validation fan-in) > scheduler > model subpackages
  (per-model, disjoint) > NIXL connectors (isolated). Six files consume the
  cache-dir resolver.
- SSH: one-shot `ssh host '<cmd>'` narrated into transcripts is the only
  observed pattern; no wrapper tooling exists. The `.codex-stage6-*` handoff
  pair is the banned anti-pattern (its gate script CONTENT — source hash, NxDI
  import scan, numeric tolerances, result.json — is good reusable design; only
  the out-of-band delivery failed). Its `NEURON_COMPILE_CACHE_URL` env var is
  dead — the real override is `VLLM_CACHE_ROOT`.

## Acceptance and closure

- Per campaign: kickoff-declared correctness + perf gates; upgrade-route
  campaigns gate on a regression matrix (note: public repo ships only 7
  spec-decode unit tests — near-zero regression safety net; matrix likely
  generated from support table + accuracy framework).
- Close-out gate 3 outcomes: PR opened / no-benefit closure (P-EAGLE precedent:
  `closed_correct_no_benefit` terminal exists in the old graph) / blocked.
  History shows both "no perf gate ever run" (GLM) and "no gate reached"
  (deepseek) — the kickoff-contract refusal rule exists to prevent exactly
  these.
- Old graph's real close-outs: accepted, closed_correct_no_benefit, blocked,
  exhausted, closed_unaccepted (5 terminals); autoport adds `incomplete`.

## Contradictions

(Those affecting design; full lists in the lens reports.)
1. Brief's TPU framing corrected: no torch-xla backend near the pin; A2 =
   tpu-inference only (both fronts JAX-lowered). GOAL BRIEF + REQUIREMENTS
   UPDATED 2026-08-25.
2. "MXFP8 unsupported" too blunt: Llama3/Trn3 MXFP8 kernels shipped since the
   initial release; gap is generic-registry/other-models. UPDATED.
3. "DFlash is MRv2-native" oversimplified: DFlash1 has a legacy V1-runner path;
   DFlash2/hybrid force MRv2; DSpark unconditionally MRv2. UPDATED.
4. GPU-baseline validation mechanic + Level 1-3 framework: documented as
   established, never actually run in any campaign — design intent.
5. Delegation table over-credits: contribution-validation (wrong domain),
   runtime-troubleshooting (no recovery logic); two delegates prescribe the
   forbidden cache clear.
6. Feature-port shipped graph drifted from its approved copy without gates
   (commit 92991ce); its enforcement-record pointer targets a file that only
   exists in the abandoned workspace; its state schema requires fields no real
   campaign ever wrote.
7. vllm-neuron docs disagree on the DLAMI venv suffix; feature matrix covers 2
   of ≥4 model families; testpaths point at a missing directory; patches
   module is an aspirational stub.

## Missing capabilities

(Genuinely new construction for the generated plugin.)
1. Shared validation back-end: repeatable GPU-baseline lm_eval/token-match/perf
   procedure (exists once as a hand-built forensic tool), comparator-set
   pre-registration separated from measurement, stable-read evidence collection.
2. Hardware queue + lease service with identity re-verification (boot-ID has no
   precedent) and exclusive leases for recovery.
3. Freeze-replicate venv manager (first execution self-verifying; path
   discovery; SDK-version uniformity check across hosts).
4. Gap-scan / route-analysis engine: per-target A-vs-B costing, A1-vs-A2 source
   evaluation (mechanical MRv2-entanglement check exists; two-tier traceability
   cost model), touched-file prediction for conflict scheduling.
5. Cross-run parity scorecard / backlog / debt ledger / fingerprint file with
   single-writer serialization.
6. In-band SSH hardware-gate execution (replacing out-of-band handoffs).
7. Delegate-dispatch guardrails: interception of forbidden remedies
   (cache-clear), forced `--target-stack vllm_neuron`, benchmark-skill scoping
   away from its provisioning branch, delegate-availability preflight.
8. Compaction/event-log state design preventing the 19.2 MB inline-log failure.

## Graph implications

1. Structural ancestor: autoport's root + named sub-workflows (composition),
   not feature-port's flat file. Candidate sub-graph seams: intake/route
   analysis; campaign execution (×2 types, largely shared); hardware
   queue/lease; validation back-end; close-out/PR; scorecard update.
2. The P1-P9 enforcement-rung table (prior-run-2026-08-12/skill-package-plan.md)
   is the reusable template for the Stage-3 enforcement record; H4's
   branch-aware deny script (ssh-aware caveats documented) is the blocking-rung
   precedent; extend deny list with /opt-write + `cp -a` venv patterns and the
   delegate cache-clear interception.
3. Adversarial-review requirement (UR 10) maps naturally onto the high-stakes
   nodes: route-analysis verdict, campaign design, pre-hardware implementation
   review, measurement adjudication (measurer-never-adjudicates + pre-registered
   comparators), PR evidence.
4. The two-tier breaker: tier 1 generalizes both engines' fingerprint
   mechanisms into the shared repo-tracked file; tier 2 is a new orchestrator
   node with a declared re-entry edge every 10 hardware attempts per target.
5. Evolving tier: the model-port static-release.yaml (digest + approval_record)
   is a local precedent for revision bundles; pave-revisions.md governs.
6. Kickoff contract must add: delegate-availability preflight, venv/SDK
   uniformity verification, GPU-baseline instance + vLLM version, weight
   artifacts existence, and the deepseek carry-over decision (adopt or close).
