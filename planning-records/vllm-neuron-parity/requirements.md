# Requirements - vllm-neuron-parity

Source authority: `/Users/jinhun/GitHub/NeuronAgenticDevelopment/vllm-neuron-parity-goal-brief.md`
(v3, 2026-08-24; 24-decision interview + 12-finding adversarial review folded in
2026-08-25). This file restates that brief as the run's requirements record; on
any wording conflict the brief wins until plan approval, after which the
approved bundle wins. Statement classes: `USER REQUIREMENT` (UR), `OBSERVED
FACT` (OF), `ASSUMPTION` (A), `OPEN QUESTION` (OQ).

## Goal

Build one PAVE workflow plugin that brings the vLLM-Neuron platform plugin
(/Users/jinhun/GitHub/vllm-neuron) to parity with upstream vLLM on NVIDIA GPUs.
One run: scan the upstream delta, resolve each requested target into the minimal
set of upstream changes it needs (cherry-picked commits/PRs vendored into the
plugin at the current pin — or, when genuinely more expensive, a pin upgrade),
rank the result, and — after user approval of target + route — execute campaigns
until each target passes a correctness gate and a performance gate against a GPU
baseline. Each finished campaign pushes a branch and opens an evidence-backed PR.
(UR, verbatim from the brief.)

## Run identity

- Run: `vllm-neuron-parity-2026-08-25`; workspace `.pave/vllm-neuron-parity/`.
- Generated plugin name: `vllm-neuron-parity` (UR, 2026-08-25).
- Output: `plugins/vllm-neuron-parity/` in NeuronAgenticDevelopment (UR, 2026-08-25).
- Evolution tier: **evolving** (UR, 2026-08-25 — departs from the prior skill's
  static tier; ships revision workspace, freeze script, seven-rule contract).
- pave-init validation runtime: `uv run --no-project --with pyyaml --with jsonschema python`
  (UR, approved alternate interpreter; no persistent installs).

## Purpose and target

- Target systems: the vllm-neuron fork (jinhuang12/vllm-neuron) vs upstream
  vllm-project/vllm (OF). Pin AMENDED 2026-08-25 (UR, Neuron 2.32 re-baseline):
  `vllm==0.24.0` via official branch `release-0.24.0.1.1.0` (branch + tag
  verified on vllm-project/vllm-neuron); fork sync is MANUAL and user-owned —
  kickoff preflight verifies the branch exists on the fork and refuses to
  start a campaign until it does. Hosts: SDK 2.32 DLAMI, compiler neuronx-cc
  2.27.5334.0 (the "one compiler version"). The pinned base, release-branch
  name, and SDK version are invocation-time inputs recorded per run — never
  graph constants.
- North star: repo-tracked feature × model parity scorecard; parity % is the
  cross-run progress number (UR).
- Two campaign types under one plugin: type A feature port, type B model port;
  shared intake (gap scan → route analysis → ranked backlog → kickoff gate) and
  shared back-end (validation → evidence bundle → PR gate → scorecard update) (UR).

## Scope and exclusions

- Backport-first: route A (vendor upstream commits/PRs at the frozen pin) is the
  default; route B (pin upgrade) is a fallback that route analysis must justify
  on cost; both routes costed per target (UR).
- Route-A reference sources (UR, 2026-08-25): per target the analysis costs
  upstream GPU vLLM (A1) AND the TPU vLLM variant (A2, when the feature is
  TPU-supported — TPU's static-shape/XLA design decisions transfer to Neuron
  more cheaply than GPU's dynamic-shape ones) and recommends one; the user
  approves source with route at kickoff; evidence bundle and debt ledger record
  the source used. Corrected by exploration (OF, 2026-08-25): A2 goes
  exclusively through tpu-inference (the in-tree torch-xla backend was deleted
  at v0.14.0, months before the pin); tpu-inference is unified JAX+PyTorch but
  both front-ends lower to JAX/XLA kernels via torchax, so A2 is design-port
  for both; known A2 dead ends: sleep mode, weight reload. The same
  baseline-skew caveat applies.
- Absorbs BOTH `skills/vllm-neuron-feature-port` and the model-port/autoport
  domain; supersedes the `.pave/vllm-neuron-feature-port` redesign (marked
  abandoned at this run's initialization; materials reused as input) (UR).
- Exclusions: no continuous upstream tracking (gap analysis compares only against
  the pinned base at invocation); fork sync with official plugin releases stays
  manual (REAFFIRMED 2026-08-25 for the 0.24 sync — user performs it; kickoff
  preflight verifies); PR merge stays human; no hardware provisioning ever (UR).
- Re-baseline follow-ups (UR, 2026-08-25): bounded 0.21→0.24 delta re-scan of
  the plugin surfaces before route costing; debt-ledger absorption pass at
  first intake (0.21-era backports may ship in 0.24); scorecard rows
  re-verified against the new pin, never carried forward as passed.

## Authority and conflict order

1. Explicit user decisions at the 3 campaign gates and in run state.
2. The kickoff contract (gate 1): targets, routes, thresholds, methods, models,
   hosts, test layout, spend notes. The workflow refuses to start without a
   complete contract (UR).
3. Kickoff-declared metrics/thresholds/methods change ONLY by explicit user
   decision recorded in run state (UR, wrong-baseline precedent).
- Exactly 3 user gates per campaign: kickoff, design approval, close-out (UR).
- The agent that measured a number never adjudicates it (UR).

## Allowed and forbidden effects

Allowed (UR): SSH to standing instances named at invocation; parallel isolated
worktrees; per-campaign venvs (freeze-replicate recipe); autonomous recovery of
the campaign's own assigned Neuron host(s) via exclusive hardware-queue lease
with pre-action identity re-verification; branch push + PR open on the
jinhuang12/vllm-neuron fork; serialized single-writer updates to scorecard /
backlog / debt ledger in the workflow repo.

Forbidden (UR — irreversible-action prohibitions carry over as blocking hooks,
including ssh-wrapped forms):
1. Mutating protected base branches (the pinned release branches —
   release-0.24.0.1.1.0 and the historical release-0.21.0.1.0.0 — plus main /
   mainline, on the fork or the vllm-project upstream remote).
2. Clearing the shared Neuron compile cache (`$VLLM_CACHE_ROOT/neuron/compile_cache`,
   `~/.cache/vllm/neuron/compile_cache`, `/var/tmp/neuron-compile-cache`).
3. `cp -a` venv cloning and any pip write into /opt.
4. `neuronx_distributed*` (NxDI) imports in ported code (contamination gate).
5. Autonomous reboot/reset of the GPU baseline instance (user confirmation only).
6. Removing or bypassing the benchmark skill's provisioning STOP gate.

Kernel-substrate rule (user-directed amendment at plan approval,
2026-08-26; verbatim direction: "option 2, add nki rule" — electing the
run-wide standing rule over per-campaign kickoff clauses): new
kernel-class functionality that the existing Neuron NKI library does not
already provide is implemented in NKI, never as torch-level fallback
code (the user's example: a DSA-indexer port must be NKI, not torch).
Torch remains legitimate for orchestration and glue outside kernel-class
work. Enforcement sits at the reviewed rung, not a hook or scan:
substrate is declared per kernel-class increment in the campaign design
and checked at both the design and implementation review gates —
mechanical detection is infeasible because torch code is legitimate glue
everywhere, so "this should have been a kernel" is reviewer judgment
against a concrete design declaration.

## Evidence and artifacts

- Correctness (OF, verified): repo's 3-level accuracy framework — lm_eval task
  floors; three-way logit top-k vs HF-transformers reference logits (NOT
  GPU-vLLM capture, which does not exist); KV-cache BC ≥ 0.99. GPU-vLLM baseline
  serves lm_eval parity, greedy token-match, and the perf gate.
- Perf: upstream `vllm bench serve`; `NeuronDecodeBenchConnector` for decode-only
  isolation (OF). Matched-config baselines are a hard requirement; anomaly
  investigation precedes any verdict (UR).
- Baseline-skew control: GPU baseline runs newer vLLM than Neuron 0.21+backport;
  methodology pinned per target at kickoff; evidence bundle states GPU-side vLLM
  version and flags known cross-version differences (UR).
- Persistent repo-tracked artifacts, single-writer via the shared back-end, never
  on campaign branches: parity scorecard, ranked backlog, backport debt ledger,
  failure-fingerprint file (UR).
- Per-campaign evidence directory, one layout for both campaign types (UR).

## Roles and independence

- Delegate to existing skills in `skills/` (inventory table in the brief:
  equivalence, benchmark, profiling ×4, compiler/runtime debugging ×3, NKI ×4,
  op lowering ×2, parallelism/entitlement, contribution validation); the run
  must verify each contract during exploration, not trust the table (UR + OQ).
- Absorbed engines (`vllm-neuron-feature-port`, autoport) contribute graphs and
  references; they are not dispatched as-is (UR).
- Measurement vs adjudication separation (UR).
- Practical adversarial review after high-stakes steps (UR, 2026-08-25, added at
  approval): investigations (gap scan / route analysis), campaign designs,
  implementations (before hardware spend and before PR), and measurement
  verdicts each get an independent adversarial pass that tries to refute the
  artifact; only verified material findings block. "Practical" = proportionate
  and material-only, not ceremony — Stage 3 sizes each review point against
  `references/technique-selection.md` costs.

## Current workflow

- Existing: `skills/vllm-neuron-feature-port` (16-node graph, static v0),
  `feature_port_campaigns/` + `model_port_campaigns/` histories, the abandoned
  `.pave/vllm-neuron-feature-port` redesign bundle (OF).
- Known pain: one corrupt run-state.json; one 19.2 MB resume file; out-of-band
  hardware handoff files (`.codex-stage6-*`); wrong-baseline benefit the user
  caught; deepseek campaign paused at attempt 12 (OF).

## Recovery and parallelism

- Autonomous recovery scoped to the campaign's own Neuron hosts, executed as an
  exclusive hardware-queue lease (drain/checkpoint first, identity re-verified
  pre-action); everything logged, no recovery user gate (UR).
- Two-tier no-progress breaker, no attempt caps: (1) failure-fingerprint
  no-identical-retry with a repo-tracked fingerprint file; (2) at every 10
  hardware attempts per target, the orchestrator halts and re-derives the
  approach from first principles — revised design, route change, or gate-3
  close-out (UR, user's design).
- Parallel worktrees + shared hardware queue; conflict-aware scheduling
  serializes campaigns with overlapping predicted file surfaces; upgrade-route
  campaigns run exclusively (UR).
- CPU-first increments (`VLLM_NEURON_CPU_MODE=1`) before hardware attempts (UR).

## State and resume

- Schema-validated appends and compaction for run state (UR, history-derived).
- Hardware gates run in-workflow over SSH — no out-of-band handoff files (UR).
- Scorecard/backlog/ledger persist between runs in the workflow repo (UR).
- Evolving tier: the generated lead may revise its own graph between campaigns
  under the seven-rule evolution contract; v1 freezes before first real
  execution (UR, 2026-08-25).

## Acceptance and closure

- Per campaign: correctness gate + perf gate declared at kickoff; upgrade-route
  campaigns gate on a regression matrix instead (UR).
- Gate 3 close-out outcomes: PR opened with evidence; no-benefit closure
  ("correct port, not viable on this hardware" — scorecard cell
  measured-not-viable-at-pin, evidence + upstream-issue draft kept); or blocked
  terminal (UR).
- Generated-plugin acceptance (this run): pave-init Stage 4 plan approval and
  Stage 6 validation + final review + clean-room forward test.

## Runtime constraints

- Claude Code harness; standing Neuron + GPU instances over SSH, supplied at
  invocation — no fixed fleet (UR).
- Pin `vllm==0.24.0` (2026-08-25 re-baseline; was 0.21.0); upstream past
  v0.27.1 (tags through v0.28.0 in the local clone). The brief's Baseline
  facts were surveyed on the 0.21 fork and are stale pending the 0.21→0.24
  delta re-scan; the v0.25 MRv2-default/PagedAttention-deletion boundary now
  sits directly above the pin (OF).
- Public repo ships no test suite; test layout is a per-campaign kickoff
  decision (OF + UR).
- ~10 GB disk per venv replica; one compiler version across campaigns (UR).

## Risks and enforcement

- Costly failures: protected-branch mutation, shared-cache clearing, /opt
  mutation, GPU-baseline loss, wrong-baseline verdicts, state corruption.
- Enforcement expectation (UR): the six forbidden effects map to blocking hooks
  (deny-pattern precedent in the absorbed skill, ssh-wrapped forms included);
  everything else defaults to observing/reinjection. The Stage 3 enforcement
  record must justify each rung.
- Lead-alignment hook pair: expected by default; any omission must record a
  condition from `references/lead-alignment-hooks.md`.

## Assumptions

- A1: Standing instances are reachable over SSH at campaign time and carry the
  DLAMI baseline venv under `/opt/aws_neuronx_venv_pytorch_inference_vllm_*`.
- A2: Required weight artifacts (e.g. drafter checkpoints) exist per target —
  verified at kickoff before start.
- A3: The skills inventory table is directionally right; contracts verified in
  Stage 2 (each row is OQ until verified).

## Open questions

Carried into exploration/planning (not user gates) — the brief's 8 open items:
route-analysis method (commit-set tracing, substrate entanglement); torch-neuronx
ceiling vs PyTorch 2.13; NIXL pull/push rebase path; ~~equivalence-delegate 0.24
adapter vs 0.21 venv~~ (CLOSED 2026-08-25: re-baseline to 0.24 makes the
adapter match natively; `--target-stack vllm_neuron` still forced at
dispatch); fate of the paused deepseek-v4-flash-0731 campaign (re-evaluate
under SDK 2.32's DeepSeek-V3.2 sparse-attention NKI features); trn2
live-tree source of truth; regression-matrix assembly if route B; kernel-gap
escalation to NKI skills; TPU cousin-source mechanics (which TPU codebase is the
useful reference per feature class — in-tree torch-xla backend near the pin vs
JAX-first tpu-inference; how TPU support for a target is detected reliably;
vendorable vs design-port only).

## Irrelevant categories

None — every sufficiency category is settled by the brief or the 2026-08-25
Stage-1 decisions.

## PAVE fitness

Verdict: **fit** (see `reviews/requirements-brief.md`).
