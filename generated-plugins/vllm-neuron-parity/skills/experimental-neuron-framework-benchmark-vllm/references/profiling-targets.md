# Profiling targets — production request shapes & latency bars

Where to point the sweep and what bars to beat, derived from **production Bedrock
serving data**, not guesses. Use these to pick `shapes`, `concurrencies`, and the
pass/fail latency bars in `bench_config.json`.

> **Source:** Bedrock inference data lake (Athena, table
> `forklift_sample_emf_global`). Window: 7 full days, 2026-06-29 → 07-05. Sampled
> successful requests (`status_code=1000`). Numbers reflect the production serving
> fleet and are the request-shape + latency targets to hit/beat when porting &
> optimizing these families on Trainium. Data is **sampled**, not the full
> population (weekly cycle: weekdays ~2.7B vs weekends ~1.6B sampled req/day).

## Recommended sweep (union across the six generation models)

One sweep covering the real operating range across mulberry, coffee, berry, fern,
kiwi, haiku. Profile the full cross-product where feasible; at minimum the primary
points.

| Dimension | Sweep points | Primary | Notes |
|---|---|---|---|
| **ISL** (input tokens) | 512 / 2K / 4K / 8K / 32K / 64K | 2K–8K | Long-context tail (32K–64K) is fern + coffee; drives prefill cost. |
| **OSL** (output tokens) | 1 / 128 / 256 / 512 | 128 | Include OSL=1 (classify/short-answer); >512 is a thin tail. |
| **Concurrency** | 16 / 24 / 32 | 24 | Union of configured decode slots/replica. **Profile at slot count, not idle.** |
| **TTFT bar (p50)** | ≤ 500 ms (short) → ≤ 1.4 s (32K+) | — | Prefill-bound: gate against ISL. |
| **TPOT bar (p50)** | ≤ 13 ms/tok (~75+ tok/s) | — | Fleet TPOT ~9–15 ms/tok; beat the midpoint. |

**How to read the bars:** TTFT is prefill-dominated → gate it *against ISL* (short
prompts clear ~0.5 s; 32K+ up to ~1.4 s). TPOT is ~input-independent → hold a single
~13 ms/tok bar. Always profile at the configured batch/slot count — idle-batch
numbers understate real serving latency.

## Per-model baselines (7-day clean generation traffic)

| Model | Reqs (7d) | TTFT p50/p90/p99 (ms) | TPOT p50/p90 (ms/tok) | Median ISL/OSL |
|---|---|---|---|---|
| mulberry | 4.29B | 508 / 839 / 1619 | 9.1 / 12.9 | 2615 / 94 |
| coffee | 2.50B | 689 / 1366 / 2914 | 14.5 / 21.6 | 11694 / 213 |
| berry | 1.58B | 1023 / 1639 / 2427 | 15.1 / 22.1 | 4907 / 229 |
| fern | 1.47B | 1346 / 1902 / 4820 | 13.4 / 22.5 | 34332 / 189 |
| haiku | 124M | 228 / 548 / 1209 | 10.3 / 15.8 | 1280 / 89 |
| kiwi | 107M | 1159 / 1647 / 3010 | 13.0 / 22.8 | 7509 / 112 |

## Concurrency (per replica, 7-day) — compare utilization, not raw conc

Configured slots differ by model, so compare **util%**, not raw concurrency. The
Trainium optimization goal is to sustain the configured slot count at/below the
latency bars above.

| Model | conc p50/p90/p99 | max | slots/replica | util p90 |
|---|---|---|---|---|
| coffee | 3 / 39 / 47 | 64 | ~24 | 0.91 (hottest) |
| berry | 12 / 22 / 28 | 32 | 32 | 0.79 |
| fern | 8 / 20 / 25 | 32 | ~32 | 0.74 |
| mulberry | 5 / 10 / 13 | 16 | ~16 | 0.69 |
| kiwi | 1 / 15 / 27 | 32 | ~24 | 0.60 |
| haiku | 1 / 5 / 12 | 24 | ~24 | 0.27 |

## Request-shape structure (why medians can mislead)

- Per-model ISL is ~log-normal (unimodal in log-space) **except coffee**, which is
  **bimodal** (~4K and ~32K) — its median (11694) lands in the valley and describes
  almost no real request.
- OSL peaks low for every model (mode ~64–128); long tails to 16K+ exist but are
  thin (<1%).
- **mulberry OSL is bimodal:** a ~442M-request spike at 2–3 tokens (yes/no /
  classify-style) plus the generation hump — hence OSL=1 is a real profiling point.
- Fleet-wide ~30% of ALL requests emit zero output tokens, but that is 100%
  classifier models (`aup-*`, `ts-*`), excluded from these generation targets.

## Statistical caveats (carry these into any comparison)

- **Never use fleet-wide ISL/OSL stats** — the fleet mixes classifier / chat /
  long-context workloads. Always condition on model + workload mode.
- coffee (ISL) and mulberry (OSL) are **bimodal at the single-model level** — a
  median lands in the valley; sweep both modes, don't profile the median.
- **coffee's latency tail is confounded by saturation** (p90 util 0.91) — its p99
  partly reflects queueing, not model speed.
- Excluded from targets (bad/atypical data): `sonnet-4-20250514` (~57% corrupt TTFT),
  `claude-3-7-sonnet` (all-zero latency stream), `opus-4-1` (ISL 16 / OSL 5 = canary
  probe, not real load).

## Turning targets into a config

- **shapes**: use the ISL×OSL primaries (`{2K,4K,8K}×{128,256}`) plus at least one
  long-context point (`32K/189` or `64K` for fern/coffee) and one `OSL:1` classify
  point. For a specific model, use its median ISL/OSL row above as the center.
- **concurrencies**: center on the configured slot count (16/24/32), not a generic
  `[1..512]` — real serving runs at the slot count. Still overshoot by a doubling to
  bracket the knee.
- **latency bars**: set `targets.ttft_p50_ms_by_isl` and `targets.tpot_p50_ms` (see
  `bench_config.example.json`); the deliverables flag each cell PASS/FAIL against them.
