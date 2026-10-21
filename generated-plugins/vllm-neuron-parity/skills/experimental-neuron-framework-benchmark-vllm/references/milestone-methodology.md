# Milestone methodology (Neuron vs GPU performance milestones)

The citable, cross-platform source for how a Trn-vs-GPU benchmark becomes a
**milestone number**, distilled from the **"Neuron Agentic Optimization: Performance
Milestones"** doc. SKILL.md §B/§C cite this; `build_milestone_deliverable.py`
implements the frontier math + bar assignment described here. Where this doc and the
tooling disagree, the doc is the spec and the tooling follows it.

## 1. Performance = throughput-vs-interactivity Pareto frontier

Serving performance is **not** a single tok/s. It is the **Pareto frontier** of two
axes:

- **THROUGHPUT** — tok/s/server (**Y axis**). Aggregate server output rate; drives
  **cost efficiency** (more tok/s on one box = fewer boxes = lower $/token).
- **INTERACTIVITY** — tok/s/user (**X axis**) = server-throughput ÷ concurrency =
  **1/TPOT**. How fast a single user's stream feels.

Sweeping concurrency (and sharding) traces the frontier: higher concurrency buys
throughput at the cost of interactivity. **You compare two chips at a FIXED
interactivity point**, reading each chip's throughput off **its own** curve — never a
single raw tok/s ratio at mismatched operating points.

In the tooling this is exactly `per_user_otps` (= `output_throughput / concurrency`,
interactivity, X) vs `otps` (throughput, Y); `build_deliverables._pareto_frontier`
builds the non-dominated upper-right envelope over `(per_user_otps, otps)`.

## 2. Two standard workload shapes (Table 1)

Milestone numbers use **input-heavy** shapes, because agentic/multi-turn traffic is
dominated by context (prior turns, tool output, retrieved docs) far more than
generation:

| Shape | Ratio | Why |
|---|---|---|
| **8192 / 1024** | 8:1 | SemiAnalysis InferenceX v2 default; agentic / multi-turn. |
| **32768 / 2048** | 16:1 | Large agentic coding; the **KV-cache-pressure** regime not visible at 8k (KV per seq is ~4× the 8k shape, so it fits far fewer concurrent seqs). |

Any other shape (1:1, 1:8, 1k/10k, …) is a **one-off**, not a milestone number — run it
only for a specific ask. Both platforms must run the **same** shapes for a valid
comparison. (The example config also ships **512/1** purely as a `--smoke` go/no-go
point — not a milestone shape.)

## 3. Two baselines (Table 2) — controlled is PRIMARY

| Baseline | Role | Serving-stack optimizations |
|---|---|---|
| **controlled** | **PRIMARY** (milestone reporting) | APC + chunked prefill + spec-decode all **OFF**, DI **OFF** — isolates raw hardware/kernel from serving-stack feature maturity. |
| **production-representative** | secondary (GTM / customer-facing) | those optimizations **ON** where supported. |

**The controlled baseline is the number to report for a milestone**, because it removes
serving-stack maturity as a confounder — two chips compared with the same features off is
a hardware/kernel comparison. Production-representative answers a different, GTM-facing
question ("what does a customer actually get today") and is secondary.

### DI-vs-spec reconciliation (the doc is internally inconsistent — resolve it here)

The doc names the **third controlled toggle inconsistently**: Table 2 lists it as
**"disaggregated inference (DI)"**, while the Milestones section lists **"speculative
decoding"**. We reconcile this **explicitly**: since we **never run DI** (doc Q1
recommends starting *without* DI), DI is off by construction on both platforms. So the
operative controlled baseline is:

> **controlled = APC + chunked prefill + speculative decoding all OFF; DI off by
> construction (never run).**

This is what the driver enforces: under `baseline: controlled`, `neuron_sweep.py` forces
`--no-enable-prefix-caching` and **rejects** any regime carrying `speculative`
(`_regimes_for` raises SystemExit); chunked prefill is unavailable on the Neuron scheduler
regardless. The baseline is stamped into `regime_meta.json` so a controlled and a
production run never merge into one Pareto comparison.

## 4. Cost basis (Q1 / Q2)

- **Q2 — use PUBLIC LIST PRICE per server-hour**, not internal cost / PPA. This keeps the
  comparison to numbers anyone can verify. `build_cost_deliverables.py`'s `PRICE_PER_HR`
  holds those list rates (trn2 = 35.7608, the capacity-block public list rate, since a
  plain on-demand trn2 rate wasn't reliably published — see the code comment).
- **Q1 — single-instance, no DI.** Cost is per single serving instance with disaggregated
  inference off. Start without DI.

## 5. Milestone bars (Table 3) — read at equivalent interactivity

Comparing Trn throughput to GPU throughput at the same interactivity point yields a
**% of GPU throughput**, which maps to a bar (use this exact wording):

- **≥ 60% of GPU throughput = "Low bar"** — cost parity without PPAs; Trn commercially
  competitive on price/perf.
- **= 100% = "Mid bar"** — parity; the decision comes down to cost, where Trn wins 30-40%,
  plus ecosystem.
- **≥ 130% = "High bar"** — performance leadership.

## 6. Method (how to produce the number)

1. Sweep **sharding × concurrency** on **both** platforms at the **same shape** and the
   **same baseline**, `random` + `random_range_ratio: 0.0` (exact shapes) on both.
2. Plot **both** Pareto frontiers (throughput vs interactivity).
3. At a **fixed interactivity** both curves cover, read each platform's throughput and
   take **Trn ÷ GPU**, per-instance.
4. Assign the 60/100/130 bar.

`build_milestone_deliverable.py` implements steps 2-4: it consumes the Trn and GPU
`all_cells.csv`, builds both frontiers, **interpolates GPU throughput at the Trn
interactivity points**, and reports **%-of-GPU + the bar per shape×baseline**, emitting
`milestone_<shape>_<baseline>.md/.png` + `milestone_summary.csv` (CLI `--trn` / `--gpu` /
`--trn-instance` / `--gpu-instance` / `--baseline` / `--out`).

## What the tooling implements vs the operator owns

- **Tooling (`build_milestone_deliverable.py`):** builds both Pareto frontiers, the
  fixed-interactivity interpolation, the %-of-GPU, and the 60/100/130 bar assignment per
  shape×baseline.
- **Operator responsibility:** running a **matched** sweep on both platforms — the **same
  baseline** (`baseline: controlled` on Neuron ↔ APC+chunked+spec off on GPU), the **same
  standard shapes** (8192/1024 and 32768/2048), exact shapes (`random`, range 0.0), and the
  same per-instance budget. The tool cannot detect a mismatched baseline or shape set; a
  milestone number is only valid if the operator matched them (`preflight.py` catches the
  common Neuron-side mistakes before provisioning).
