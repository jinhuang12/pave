# Requirements brief — vllm-neuron-parity (Stage 1 approval)

Rendered 2026-08-25 from `requirements.md`; that file and the goal brief
(`vllm-neuron-parity-goal-brief.md`) are the authorities.

## Goal

> Build one PAVE workflow plugin that brings the vLLM-Neuron platform plugin
> (/Users/jinhun/GitHub/vllm-neuron) to parity with upstream vLLM on NVIDIA
> GPUs. One run: scan the upstream delta, resolve each requested target into the
> minimal set of upstream changes it needs (cherry-picked commits/PRs vendored
> into the plugin at the current pin — or, when genuinely more expensive, a pin
> upgrade), rank the result, and — after user approval of target + route —
> execute campaigns until each target passes a correctness gate and a
> performance gate against a GPU baseline. Each finished campaign pushes a
> branch and opens an evidence-backed PR.

## What this approval covers

Approving here freezes the goal above, the requirements record
(`requirements.md`), and the fitness verdict. Later stages design and build the
plugin to serve them; changing the goal returns to this gate. This is gate 1 of
2 in this pave-init run (the other is plan approval).

## Requirements summary

| Area | Requirement |
|---|---|
| Shape | One plugin `vllm-neuron-parity`; two campaign types (feature port, model port); shared intake and back-end; output `plugins/` in this repo |
| Route model | Backport at pin 0.21.0 is the default; pin upgrade is a costed fallback; both estimated per target; user picks at kickoff |
| Port source | Route-A analysis costs two reference sources — upstream GPU vLLM and the TPU vLLM variant (architectural cousin: static shapes, XLA-style compile) — and recommends one per target; user approves source at kickoff |
| Gates | Exactly 3 per campaign: kickoff contract, design approval, close-out (PR / no-benefit closure / blocked) |
| Parity bar | Correctness + perf gates vs GPU baseline, declared per target at kickoff; baseline-skew controlled; logit/KV checks vs HF reference |
| Concurrency | Parallel worktrees + shared hardware queue; per-campaign freeze-replicated venvs; conflict-aware scheduling; upgrade runs exclusive |
| Recovery | Autonomous on the campaign's own Neuron hosts via exclusive queue lease; GPU baseline excluded; two-tier breaker (fingerprint + 10-attempt first-principles review), no attempt caps |
| Forbidden | Blocking hooks: protected branches, shared compile cache, /opt writes + `cp -a` venvs, NxDI imports, GPU-baseline reboot, benchmark STOP-gate removal |
| Persistence | Scorecard, backlog, debt ledger, fingerprint file: repo-tracked, single-writer, never on campaign branches |
| Adversarial review | Independent refutation pass after high-stakes steps (route analysis, designs, implementations, measurement verdicts); material findings only; proportionate, not ceremony |
| Absorbs | `vllm-neuron-feature-port` skill + model-port/autoport domain; supersedes the `.pave/` redesign (marked abandoned) |
| Evolution | **Evolving tier** (new 2026-08-25 decision): plugin ships revision machinery and may revise its own graph between campaigns |

Full record: `.pave/vllm-neuron-parity/requirements.md` (assumptions A1–A3,
8 open questions carried into exploration).

## Fitness verdict

**fit.** Deciding characteristics:

1. Long-horizon state that must survive sessions and runs (scorecard, ledger,
   fingerprints, campaign run state) — the core PAVE case.
2. Rich conditional and recovery routing (route A/B, two-tier breaker,
   queue-leased recovery, three close-out outcomes) that prose instructions
   would not hold across compactions.
3. Real role/authority separation (measurer never adjudicates; 3 user gates;
   delegated specialist skills).

## Open questions and gaps

Carried into exploration, none block design: route-analysis tracing method;
torch-neuronx ceiling vs PyTorch 2.13; NIXL rebase path; equivalence-delegate
version skew; paused deepseek campaign's fate; trn2 source-of-truth; route-B
regression-matrix assembly; kernel-gap escalation; TPU cousin-source mechanics
(torch-xla in-tree backend vs JAX-first tpu-inference as the per-feature
reference; TPU-support detection; vendorable vs design-port). Assumptions to verify:
standing-instance reachability, DLAMI venv presence, per-target weight
artifacts, skill-contract inventory.

## Amendment 2026-08-25 — Neuron 2.32 re-baseline (approved same day)

User decision at a mid-Stage-3 Stage-1 reopening (recorded verbatim in
run-state.json): **full re-baseline** — pin moves `vllm==0.21.0` →
`vllm==0.24.0` (official branch `release-0.24.0.1.1.0`, verified on
vllm-project/vllm-neuron); hosts on SDK 2.32 DLAMI (neuronx-cc 2.27.5334.0);
**fork sync stays manual** (user-owned; kickoff preflight verifies the branch
on the fork and refuses start until present). Protected-branch list gains the
0.24 release branch; equivalence-delegate skew question CLOSED; 0.21-era
baseline facts stale pending a bounded 0.21→0.24 delta re-scan; debt-ledger
absorption pass at first intake; deepseek carry-over re-evaluated under 2.32.
Fitness verdict unchanged: **fit**.
