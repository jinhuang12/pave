# Goal brief v3: vLLM-Neuron parity plugin (input for /pave-init)

Date: 2026-08-24 (v3, same day). Source: 5-round requirements interview (20 decisions),
a 4-agent grounding sweep, and follow-up structure discussion. Supersedes v1/v2.
Change from v2: backport-first. The vLLM upgrade is no longer an expected campaign
type — gap analysis estimates a backport route and an upgrade route per target, and
the upgrade is a fallback that the analysis must justify on cost.

> **AMENDMENT 2026-08-25 — Neuron 2.32 re-baseline (user decision, supersedes
> every 0.21-era baseline fact below).** Neuron SDK 2.32 (released 2026-08-17)
> ships vllm-neuron 0.24.0.1.1.0 pinned to vLLM 0.24.0; upstream
> vllm-project/vllm-neuron carries branch `release-0.24.0.1.1.0` and tag
> `v0.24.0.1.1.0` (verified). Decisions: (1) FULL RE-BASELINE — the fork syncs
> `release-0.24.0.1.1.0`, hosts run 2.32 DLAMI (compiler neuronx-cc
> 2.27.5334.0, one version across campaigns), gap analysis compares
> 0.24 → upstream. (2) The fork sync stays MANUAL (user-performed); the
> workflow verifies the branch exists on the fork at kickoff preflight and
> refuses to start a campaign until it does. Consequences: protected-branch
> list gains `release-0.24.0.1.1.0` (fork + upstream, 0.21 branch stays
> protected); the equivalence delegate's 0.24 adapter now matches natively
> (open item closed); the v0.25 MRv2-default/PagedAttention-deletion cliff
> sits directly above the new pin, so route-B costing crosses it immediately;
> baseline facts below (plugin surfaces, MRv2 tiers, MXFP8 partial-support,
> unsupported list) were surveyed on the 0.21 fork and are stale pending a
> bounded 0.21→0.24 delta re-scan; the debt ledger gets an absorption pass at
> first intake (0.21-era backports may be included in 0.24); the paused
> deepseek carry-over is re-evaluated under 2.32 (DeepSeek-V3.2
> sparse-attention NKI + MXFP8 flash-decode shipped). Kickoff preflight adds:
> SDK 2.32 uniformity, NumPy ≥ 2.2, `--native-int64` default-flip awareness.

## Goal statement

Build one PAVE workflow plugin that brings the vLLM-Neuron platform plugin
(/Users/jinhun/GitHub/vllm-neuron) to parity with upstream vLLM on NVIDIA GPUs.
One run: scan the upstream delta, resolve each requested target into the minimal
set of upstream changes it needs (cherry-picked commits/PRs to vendor into the
plugin at the current pin — or, when that is genuinely more expensive, a pin
upgrade), rank the result, and — after user approval of target + route — execute
campaigns until each target passes a correctness gate and a performance gate
against a GPU baseline. Each finished campaign pushes a branch and opens an
evidence-backed PR.

## Structure: one plugin, two campaign types, route-based gap analysis

```
[Parity plugin]
 ├── shared intake: gap scan → per-target ROUTE analysis → ranked backlog → kickoff GATE
 ├── type A: feature port   (backport at current pin; parallel worktrees)
 ├── type B: model port     (parallel worktrees)
 └── shared back-end: validation → evidence bundle → PR-open GATE → scorecard update

per-target route analysis:
  route A: backport — commits/PRs to vendor + plugin-side reimplementation, est. cost   (DEFAULT)
    source A1: upstream GPU vLLM   (feature's canonical implementation)
    source A2: TPU vLLM variant    (architectural cousin — evaluate when the
               feature is TPU-supported; see "TPU cousin-source option")
  route B: upgrade  — minimum unlocking vLLM version, breakage inventory, est. cost     (FALLBACK)
```

Route A is the default and matches precedent: the P-EAGLE campaign ported a feature
into the plugin at the frozen pin. Neuron features are never literal cherry-picks —
kernels are reimplemented plugin-side; what gets vendored is the upstream
scaffolding (config plumbing, drafter APIs, scheduler hooks).

Route B exists because backport cost is not always small. Some features are built
ON the new substrate (DFlash's upstream implementation is Model-Runner-V2-native);
"backporting" those is a full reimplementation against the old surface, and at some
cost point the upgrade is genuinely cheaper. The gap analysis must estimate BOTH
routes per target and recommend one; the user approves target + route at kickoff.

If route B is ever chosen: the upgrade runs as an exclusive campaign (no parallel
worktrees while the pin moves), classifies breakage as mechanical vs architectural
conflict, presents align-vs-diverge decisions (e.g. MRv2 vs the custom
NeuronModelRunner) at the design gate, and gates on a regression matrix — every
currently-supported feature re-passes on the new pin.

## TPU cousin-source option (added 2026-08-25)

Google TPU and AWS Neuron are architectural cousins: both are systolic-array
accelerators that need static shapes, ahead-of-time compilation, and bucketed
graphs — the exact constraints that make GPU implementations (dynamic shapes,
CUDA/Triton kernels) expensive to port. When a target feature/model is supported
on TPU vLLM (e.g. prefill+decode in a single engine step), the route analysis
must evaluate the TPU implementation as an alternative reference source (A2) and
recommend a source per target alongside the route.

Constraints on A2 (corrected 2026-08-25 by exploration — the in-tree torch-xla
TPU backend was deleted at vLLM v0.14.0, four months BEFORE the 0.21 pin;
v0.21.0's own vllm/platforms/tpu.py already delegates entirely to tpu_inference,
so there is no literal torch/XLA cousin near the pin at all):
- A2 sourcing goes exclusively through vllm-project/tpu-inference, a unified
  JAX+PyTorch backend where even the torch-syntax model front-end
  (tpu_inference/models/vllm/) lowers to JAX/XLA kernels via torchax. What
  ports is the design/algorithm for BOTH front-ends, rarely the code.
- Per-target A2 evaluation must resolve which front-end (vllm/torch vs jax) and
  which capability tier is the relevant comparison; the repo's own
  support_matrices/*.csv are the machine-readable detection source.
- The same baseline-skew caveat applies: the TPU reference tracks a newer vLLM
  than the 0.21 pin.
- The evidence bundle records which source was used; the debt ledger entry names
  the source commits/design docs either way.

## Concurrency mechanics

1. **Per-campaign venv**: each campaign creates its own venv + editable plugin
   install on the Neuron host. The hardware queue switches the active
   serve/bench environment between campaigns. No campaign ever mutates a shared
   install (the old single-live-install model is retired).
   Construction recipe (verified constraint: the plugin's own dependency
   metadata omits the entire Neuron SDK stack, so a naive fresh venv +
   `pip install -e .` yields an env that cannot run on Neuron): freeze-replicate
   the DLAMI baseline venv (`pip freeze` from
   /opt/aws_neuronx_venv_pytorch_inference_vllm_* → new venv → install with the
   Neuron extra index), pinning torch-neuronx/neuronx-cc/torch-xla/libneuronxla
   to the host baseline versions — one compiler version across campaigns is
   required for matched-config perf and shared compile-cache hits — then
   `pip install -e <worktree> --no-deps` for the plugin only, verified by an
   edit-live probe. Forbidden: `cp -a` venv cloning (clone shebangs still point
   at /opt python and would mutate the shared install) and any pip write into
   /opt. Budget ~10 GB disk per replica.
2. **Conflict-aware scheduling**: gap analysis predicts each target's touched
   plugin files. Campaigns whose surfaces overlap (most features touch the
   monolithic NeuronModelRunner) serialize against each other; disjoint
   campaigns run in parallel.
3. **Upstream motion is ignored**: gap analysis compares only against the pinned
   base it is invoked on. Syncing the fork with official vllm-project/vllm-neuron
   releases, and checking their roadmap, stays the user's manual job.
4. **Host-wide recovery is a queue operation**: reboot, driver reload, or device
   reset requires an exclusive lease on that host from the hardware queue — the
   queue drains or checkpoints the active campaign's job first; recovery never
   bypasses the queue. Reboot authority is scoped to the campaign's own assigned
   Neuron host(s): resolved instance identity (instance-id + hostname + boot-ID)
   is recorded at kickoff and re-verified immediately before any destructive
   action. The GPU baseline instance is excluded from autonomous reboot — its
   recovery requires user confirmation, because every campaign's gates depend
   on it.

## Parity scorecard (north star)

A repo-tracked feature × model matrix with correctness/perf status per cell,
updated by the shared back-end at the end of every campaign. Parity percentage is
the headline progress number across runs. The scorecard, the ranked backlog, and
the backport debt ledger are all repo-tracked artifacts that persist between runs.
They live in this workflow repo (NOT the vllm-neuron fork) and have exactly one
writer: the shared back-end applies updates serially, one campaign at a time,
under the same schema-validated integrity bar as run state. Campaign branches
never modify these files, so campaign PR diffs stay clean.

## Backport debt ledger (hard requirement)

Every vendored backport diverges from upstream and becomes a merge conflict when a
pin bump eventually happens (Neuron SDK or torch will force one someday). The
plugin must keep a per-backport debt ledger: upstream commits/PRs vendored, plugin
files touched, and the upstream version where the feature lives natively. Route
analysis reads this ledger so the "backport again vs upgrade now" comparison stays
honest as debt accumulates.

## Baseline-skew caveat (validation design constraint)

The GPU baseline runs a new vLLM (where the target feature exists natively) while
Neuron runs 0.21 + backport. Correctness-gate failures can therefore come from
surrounding machinery differences (sampler fixes, scheduler changes between
versions), not from the port itself. Validation must control for this: pin the
comparison methodology per target at kickoff, and the evidence bundle must state
the GPU-side vLLM version and flag known cross-version behavior differences.

## User decisions (locked, from the 2026-08-24 interview)

| Dimension | Decision |
|---|---|
| Deliverables | Execute feature ports and model ports at the current pin (backport route); pin upgrades only as a justified fallback route. Gap assessment is the intake step, not a standalone product. No continuous tracking. |
| Scope | Absorbs BOTH the vllm-neuron-feature-port skill and the model-architecture port (autoport) domain. |
| In-flight pave-init | The active `.pave/` feature-port redesign (Stage-1 `revision_requested`) is superseded. Reuse its materials as input. |
| Selection | Run opens with a full gap scan; workflow proposes a ranked backlog with per-target route recommendation (backport vs upgrade), effort, and dependency estimates; user approves target + route at a gate. |
| Port source (2026-08-25) | Route-A analysis costs two reference sources per target — upstream GPU vLLM (A1) and the TPU vLLM variant (A2, when the feature is TPU-supported) — and recommends one; user approves source with route at kickoff. TPU's static-shape/XLA design decisions transfer to Neuron more cheaply than GPU's dynamic-shape ones. |
| Parity bar | Correctness gate + performance gate, declared per target at kickoff (metric, threshold, method). No campaign starts without them. Upgrade-route campaigns gate on the regression matrix instead. A perf adjudication may conclude "correct port, no benefit on this hardware" (the P-EAGLE precedent) — that is a clean close-out at gate 3 with the scorecard cell set to measured-not-viable-at-pin and the evidence + upstream-issue draft kept, not a blocked terminal. |
| Correctness method | Declared per target at kickoff, controlling for baseline skew (above). Mechanics (verified): logit/KV checks (Levels 2/3 of the repo's accuracy framework) compare against HF-transformers reference logits generated on the GPU box or any host with the checkpoint — NOT against the GPU-vLLM engine, which exposes no logit/KV capture path. The GPU-vLLM baseline instance directly serves lm_eval score parity (Level 1), greedy token-match, and the perf gate. If a target genuinely needs Neuron-vLLM vs GPU-vLLM logit comparison, route analysis must size building that capture tooling into the campaign. |
| Validation models | Named per target at kickoff (e.g. draft+target pair for spec decode). |
| Hardware | Standing instances over SSH. Neuron host alias(es) and the GPU baseline instance are supplied at invocation — no fixed fleet. |
| Recovery | Autonomous for the campaign's own assigned Neuron host(s), executed as an exclusive hardware-queue lease with pre-action identity re-verification (see Concurrency mechanics #4); everything logged, no recovery gate. The GPU baseline instance is excluded — its recovery requires user confirmation. |
| Pin policy | Realized by the route analysis: minimal-unlocking-version vs latest-stable estimates are part of route B's costing; user picks at the kickoff gate. |
| Concurrency | Parallel isolated worktrees per approved target with a shared hardware queue — EXCEPT upgrade-route campaigns, which run exclusively. Isolation via per-campaign venvs; conflict-aware scheduling serializes campaigns with overlapping file surfaces. |
| Progress metric | Repo-tracked feature × model parity scorecard, updated per campaign; parity % is the north star. |
| Upstream motion | Ignored: gap analysis compares only against the pinned base. Fork sync with official plugin releases stays manual. |
| End state | Branch pushed and PR opened on the jinhuang12/vllm-neuron fork with evidence attached. Humans review and merge. |
| Budgets | Standing autonomy: no hard attempt caps, guarded by the two-tier no-progress breaker (requirement 9). Report spend continuously; a blocked terminal is allowed. |
| Gates | Exactly 3 user gates per campaign: (1) kickoff (target + route + thresholds), (2) design approval, (3) close-out — open the PR, record a no-benefit closure, or accept a blocked terminal. |
| Tests | Test layout is a per-campaign kickoff decision (CPU-mode `test/unit` precedent exists on the P-EAGLE branch). |

## Baseline facts (verified 2026-08-24)

- Pin: `vllm==0.21.0` (requirements/core.txt). Latest upstream: v0.27.1 (2026-08-11). Six minor releases behind.
- Integration surfaces: `vllm.platform_plugins` entry point → NeuronPlatform; NeuronWorker(WorkerBase); NeuronScheduler(Scheduler); custom ~7900-line NeuronModelRunner (NOT a GPUModelRunner subclass); NIXL connector subclasses; several targeted monkeypatches.
- Known mechanical breaks if the pin ever moves to v0.27.1: `NixlConnectorWorker` no longer importable (pull/push split, v0.24); `Scheduler.schedule()` now takes `throttle_prefills`.
- Known architectural conflicts on that path: Model Runner V2 default + legacy PagedAttention deleted (v0.25); PyTorch 2.13 required (v0.27, torch-neuronx ceiling unknown); NIXL pull/push redesign.
- DFlash (v0.24+) and DSpark (v0.25+) are confirmed upstream spec-decode drafters. MRv2 entanglement is tiered (corrected 2026-08-25): base DFlash1 has a legacy V1-runner path upstream; DFlash2 candidate-selection and hybrid drafters force MRv2; DSpark is unconditionally MRv2-only. MRv2-forcing upstream is feature-by-feature (config oracle), not a per-version binary.
- Unsupported today (re-scoped 2026-08-25): LoRA (TODO stubs in the model runner), KV offloading (absent), MTP (absent), sleep mode (absent; also absent on TPU by design), weight reload (absent; TPU no-ops it), audio. Chunked prefill is PARTIAL (batch-size-1 chunking allowed; no mixed prefill+decode batches — the exact gap TPU's unified batching addresses). Pipeline parallelism has partial plumbing (size threaded through worker/parallel state; runner-level support unverified). MXFP8 is PARTIAL: Llama3/Trn3 kernels shipped since the initial release — the gap is generic quant-registry support/other models, not zero-to-one.
- Validation assets already in-repo: `vllm_neuron/accuracy` 3-level framework (lm_eval task floors; three-way logit top-k; KV-cache BC >= 0.99); perf via upstream `vllm bench serve`; `NeuronDecodeBenchConnector` for decode-only isolation.
- The public repo ships no test suite or test CI; pyproject testpaths point at a missing internal layout.

## Requirements derived from campaign history

1. State integrity is a hard requirement: schema-validated appends and compaction (one past run-state.json is corrupt JSON; one resume file grew to 19.2 MB).
2. Hardware gates run in-workflow over SSH — no more out-of-band handoff files (the `.codex-stage6-*` pattern).
3. The NxDI contamination gate carries over: zero `neuronx_distributed*` imports in ported code.
4. Keep the per-campaign evidence-directory pattern; unify feature and model campaigns under one layout.
5. Benchmark baselines must be matched-config (a past run recorded a wrong-baseline "benefit" the user had to catch). Additionally: a verdict is blocked while any material deviation from kickoff-declared reference values is unexplained — anomaly investigation precedes acceptance; the agent that measured a number never adjudicates it; and kickoff-declared metrics/thresholds/methods change only by explicit user decision recorded in run state.
6. Preserve CPU-first increments (`VLLM_NEURON_CPU_MODE=1`) before spending hardware compile attempts.
7. Backport debt ledger (see above) maintained as a first-class artifact.
8. Irreversible-action prohibitions carry over as blocking hooks (the old skill's deny pattern, including ssh-wrapped forms): never mutate protected base branches (release-0.21.0.1.0.0 / main / mainline, on the fork or the vllm-project upstream remote), and never clear the shared Neuron compile cache (`$VLLM_CACHE_ROOT/neuron/compile_cache`, default `~/.cache/vllm/neuron/compile_cache`, plus `/var/tmp/neuron-compile-cache`) — it is host-shared across all campaign venvs and other users; cache clearing is never a debugging remedy.
9. Two-tier no-progress breaker (compatible with standing autonomy — neither tier is an attempt cap): (tier 1) every hardware attempt records a failure fingerprint in a repo-tracked file beside the scorecard; an identical fingerprint is never retried without a recorded changed hypothesis, and concurrent/later campaigns consult the file before burning compile time. (tier 2) At every 10 hardware attempts on one target, the orchestrator halts implementation and re-derives the approach from first principles against the accumulated evidence — the outcome is a revised design, a route change, or close-out at gate 3; never an 11th push on an unexamined strategy.
10. Practical adversarial review (added at Stage-1 approval, 2026-08-25): every
   high-stakes artifact — gap-scan/route-analysis verdicts, campaign designs,
   implementations (before hardware spend and before PR), measurement verdicts —
   gets an independent adversarial pass that tries to refute it before it is
   acted on; only verified material findings block; proportionate, never ceremony.

## Delegation candidates (skills in `skills/`)

The plugin should delegate to existing skills instead of re-implementing their
domains. This list is a starting inventory — the pave-init run must verify each
skill's actual contract during exploration, not trust this table.

| Role | Skill(s) | Note |
|---|---|---|
| Port engines (absorbed) | `vllm-neuron-feature-port`, `neuron-framework-autoport-vllm-neuron` | Absorb their graphs/references; do not dispatch them as-is. |
| Correctness / equivalence | `neuron-framework-equivalence` | Known caveat: runtime adapter pinned to the 0.24 line vs the 0.21 venv; always pass `--target-stack vllm_neuron`. |
| Benchmarking | `experimental-neuron-framework-benchmark-vllm` | Its provisioning STOP gate is PRESERVED: any action that provisions or purchases hardware stays blocked. "Reconcile with the 3-gate model" means pre-answering the gate's questions from the kickoff contract's spend notes — never removing the block. |
| Perf investigation | `experimental-neuron-framework-profiling-vllm-neuron`, `experimental-neuron-framework-profile-analysis-vllm-neuron`, `neuron-nki-profiling`, `neuron-nki-profile-querying` | For benefit anomalies and perf-gate misses. |
| Compiler / runtime debugging | `experimental-neuron-autoport-compiler-debugging-vllm-neuron`, `experimental-neuron-perf-compiler-artifact-debugging`, `experimental-neuron-infra-runtime-troubleshooting` | Runtime-troubleshooting supports the autonomous-recovery decision. |
| Kernel work | `neuron-nki-writing`, `neuron-nki-debugging`, `neuron-nki-docs`, `experimental-neuron-nki-optimizing` | Ports that need custom NKI kernels (P-EAGLE precedent). |
| Op lowering | `experimental-neuron-framework-aten-op-lowering`, `experimental-neuron-eager-op-lowering` | Missing-op gaps surfaced by ports. |
| Planning / capacity | `experimental-neuron-parallelism-planner`, `experimental-neuron-hardware-entitlement` | TP/parallelism choices at design time; instance access. |
| Contribution checks | `neuron-dev-contribution-validation` | Pre-PR validation before the PR-open gate. |

## Open items for the pave-init run to investigate (not user gates)

1. Route-analysis method: how to trace a feature's upstream implementation to the minimal commit/PR set and detect substrate entanglement (e.g. MRv2-native code) reliably.
2. torch-neuronx compatibility ceiling vs PyTorch 2.13 — bounds route B if ever taken.
3. NIXL pull/push rebase path for NeuronNixlConnectorWorker (route-B contingency).
4. Equivalence-validation delegate adapter pinned to the 0.24 line vs the 0.21 venv.
5. Fate of the in-flight deepseek-v4-flash-0731 campaign (paused at attempt 12) — migrate or resume under the new plugin.
6. Whether the trn2 live source tree now has a canonical git-tracked source of truth.
7. How a regression matrix is assembled if route B is ever taken, given the public repo ships no test suite (likely: generate it from the support table + accuracy framework).
8. Kernel-gap escalation: when route analysis finds a feature needs an NKI kernel that does not exist, how that work is sized and spun out to the NKI skills (or becomes its own campaign).
9. TPU cousin-source mechanics (re-scoped 2026-08-25): per feature, which tpu-inference front-end (torch/vllm vs jax) and capability tier is the right reference; support-matrix CSVs as the detection mechanism; known A2 dead ends already established — sleep mode (TPU raises NotImplementedError) and weight reload (TPU no-ops it; Tunix/Raiden-specific).

## Kickoff contract (gate 1 of 3)

Per campaign the user supplies: the approved target(s), route (backport by
default; upgrade only with route-analysis justification) and reference source
(upstream GPU vLLM or the TPU variant, per the route analysis), per-target correctness
method + perf metric + threshold (or regression-matrix scope for an upgrade),
validation model(s) and any required weight artifacts (e.g. drafter checkpoints for
spec decode — verify they exist before starting), Neuron host alias(es), the GPU
baseline instance alias and its vLLM version, the test layout for this campaign,
and any spend notes. The workflow must refuse to start without a complete kickoff
contract.
