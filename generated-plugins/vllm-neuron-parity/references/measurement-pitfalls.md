# Measurement pitfalls — vllm-neuron parity runs

Operational rules for the measurer role. Each rule is an imperative the measurer must honor before a number becomes evidence.

Citation classes used below:
- Unqualified repo paths are relative to the vllm-neuron checkout (`/Users/jinhun/GitHub/vllm-neuron`). Verified at branch `feature/p-eagle-gpt-oss-20b`, commit `0e19f00`, plugin version `0.21.0.1.0.0` (`pyproject.toml:10`), vLLM pin `vllm==0.21.0` (`requirements/core.txt:6`). Line numbers move on a pin bump — re-derive with grep, and trust the rule over the line number.
- A citation naming a `<name>` skill points at an installed delegate skill in the host workspace, at that skill's own directory.
- A citation marked "(campaign history)" names an artifact from a prior port campaign. The rule stands on its own without it; treat the artifact as provenance, not as a file you must have.

## Pre-register the comparator set — the no-change baseline is mandatory

**Trap:** A benefit verdict of `benefit_shown` was reached by comparing the fixed code path only against other speculative-decode variants (92.7 tok/s vs a defective 127.9 and a sequential 40.0). No-spec at the same matched config was never measured. When it was measured (199.0 tok/s), the verdict flipped to `no_benefit`. The measuring agent had also selected the comparators.

**Rule:** Before any benefit measurement, write down the full comparator set and the decision rule. Always include the unchanged baseline at the exact matched config (same server args, same battery, same seed). The agent that measures a number does not adjudicate it. When a serving-config (DP×TP) choice affects fairness, expect an interior-optimum TP for collective-bound models — do not assume max-TP.

**Evidence:** `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json`, entries 51–53, and `benchmark/benefit.md` §8.5 (campaign history). TP note: `vllm-neuron-feature-port` skill, `references/delegation.md:21`.

## Do not adjudicate spec-decode comparisons from chunk-derived throughput — any harness, stock or custom

**Trap:** A campaign's own streaming harness derived "tokens/sec" from stream-chunk counts. Speculative decode emits multiple accepted tokens per stream chunk, so chunk counting undercounts spec configs and biases the comparison against them. Avoiding the stock tool does not avoid the trap: that campaign had already set `vllm bench serve` aside for unrelated dataset-shape reasons and reproduced the undercount in its custom harness. Whether the stock tool's own accounting shares this failure is unverified at this pin — its logic is upstream vLLM, not vendored in the fork.

**Rule:** For spec-decode benefit legs, measure end-to-end latency for a fixed N-token generation over `/v1/completions` with `stream: true`, not chunk-derived throughput. Repeat the fixed prompt battery (precedent: 24 prompts × 3 repeats = 72 requests per config) — never adjudicate from a single read.

**Evidence:** `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/benchmark/benefit.md:34-45` (tool decision and the measurement-honesty note locating the undercount in the campaign's own harness) and `:51-75` (×3 repeats) (campaign history).

## Decode-bench connector output is throughput evidence only — never correctness

**Trap:** `NeuronDecodeBenchConnector` fakes a completed prefill so decode can be benchmarked in isolation. Its `_fill_blocks` is a no-op: decode runs against uninitialized KV, "the resulting logits are garbage, but correctness is not checked."

**Rule:** Use runs made with this connector only as decode-throughput measurements at a fixed decode NEFF shape. Never cite their outputs as correctness, accuracy, or output-quality evidence, and never mix them into a leg that also checks outputs.

**Evidence:** `vllm_neuron/vllm/kv_connector/neuron_decode_bench_connector.py:40-47` (docstring item 5) and `:198-219` (`_fill_blocks` no-op).

## Control compile-cache state — and never clear the shared cache

**Trap:** The compile cache is a shared, multi-writer resource (per-key `FileLock`, atomic-rename promotion, safe concurrent access by design). Novel configs add fresh compiles (one validation run grew the cache 14 → 32 entries), so a cold leg pays compile cost a warm leg does not. Two delegate skills prescribe `rm -rf ~/.cache/vllm/neuron/compile_cache` as a first-line remedy — clearing destroys warm state for every campaign sharing the root and skews all subsequent timing. Separately, `NEURON_COMPILE_CACHE_URL` is read nowhere in the codebase; setting it does not move the cache.

**Rule:** Pin the cache root explicitly with `VLLM_CACHE_ROOT` (resolved to `$VLLM_CACHE_ROOT/neuron/compile_cache`); do not rely on any other variable. Record warm/cold cache state per measurement leg and keep compared legs at equal warmth. Never clear a shared cache directory as a debugging or measurement remedy — override the delegate skills' `rm -rf` instructions.

**Evidence:** `vllm_neuron/envs.py:341-391` (`get_neuron_compile_cache_dir`; `:364` reads `VLLM_CACHE_ROOT`, `:376` joins `neuron/compile_cache`); `vllm_neuron/compile/cache.py:328-376` (`CompilationLock`) and `:156-220` (`save_cache`, atomic rename, concurrent-safe); `grep -rn NEURON_COMPILE_CACHE_URL` in the repo returns zero hits. Cache growth: `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/correctness.md:27-28` (campaign history). Conflicting remedies: `experimental-neuron-framework-profiling-vllm-neuron` skill `SKILL.md:375, 424-425`; `experimental-neuron-autoport-compiler-debugging-vllm-neuron` skill `SKILL.md:105-107, 146-148`.

## Do not adjudicate evidence on first sighting — wait for stable reads

**Trap:** A watcher stopped waiting when `exit-status.txt` appeared. The runner writes that file before the post-run source, HLO, NEFF, and cache-count files, so the watcher evaluated file existence inside the creation window and produced a false `failed` verdict on good evidence.

**Rule:** After a completion signal appears, re-read the full evidence set until N consecutive reads are identical before adjudicating. Take N and the minimum re-read spacing from the campaign design record; never default them. A process-exit signal is not proof that output artifacts are fully materialized.

**Evidence:** GLM-5.2 model-port campaign, `working-profile-512/adjudication-manifest.json:71-84` (`watcher_race_adjudication` block) (campaign history).

## CPU-mode results are never performance or hardware-accuracy evidence

**Trap:** `VLLM_NEURON_CPU_MODE=1` is the sanctioned CPU development mode: it replaces Neuron device execution. Numbers produced under it say nothing about device performance, and its accuracy behavior differs from hardware.

**Rule:** Record the execution mode with every result. Never present CPU-mode timings as Neuron performance evidence, and never present CPU-mode outputs as hardware-accuracy evidence. Note the flag is mutually exclusive with `VLLM_NEURON_CPU_COMPILE` (the runtime raises `RuntimeError`).

**Evidence:** `vllm_neuron/envs.py:25, 120-122` (flag definition); `vllm_neuron/__init__.py:82-85` (mutual-exclusion error); `docs/model-dev/cpu-development.md` (sanctioned development scope).

## Verify tool-to-stack version alignment before spending

**Trap:** The equivalence skill's vLLM-Neuron adapter is hard-pinned to the 0.24 line (`PINNED_VLLM_VERSION = "0.24.0"`). Against a 0.21 stack it exits before any measurement. One campaign hit this live and lost its planned correctness method mid-gate.

**Rule:** Before dispatching any measurement tool, confirm the installed `vllm`/`vllm-neuron` versions match the tool's pin. On an unbridgeable mismatch, record `validation_blocked` and use the sanctioned fallback (greedy string-equality on a fixed battery plus an acceptance-rate floor from `/metrics`) — do not improvise a partial run. Record the exact target commit and vLLM pin with every measurement.

**Evidence:** `neuron-framework-equivalence` skill, `scripts/adapters/vllm_neuron.py:46-47`; fallback precedent: `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/correctness.md:30-46, 95-109` (campaign history); target pin: `requirements/core.txt:6` (`vllm==0.21.0`).

## Always pass `--target-stack vllm_neuron` — auto-detect routes to the wrong stack

**Trap:** Every equivalence-skill stage script accepts `--target-stack`, default unset. The config template's auto-detect default is `"target_stack": "nxdi"` — omitting the flag can silently run the whole measurement pipeline against the wrong (forbidden) stack and return plausible numbers for it.

**Rule:** Pass `--target-stack vllm_neuron` explicitly on every equivalence-skill stage invocation. Treat any equivalence result whose config does not show `vllm_neuron` as invalid.

**Evidence:** `neuron-framework-equivalence` skill, `templates/equiv_config_template.json:4-5`; `vllm-neuron-feature-port` skill, `references/delegation.md:20`.

## State the validation level and the reference for every accuracy claim

**Trap:** The accuracy framework has three levels that answer different questions: Level 1 (task scores via `lm_eval` vs user thresholds), Level 2 (prompt-level logit and KV-cache comparison), Level 3 (module-level vs HF). Levels 2/3 compare against HF-transformers reference logits (FP32/BF16 on CPU), not against GPU-vLLM. A Level-2 pass does not support a "matches GPU serving" claim, and a task-level score does not support a token-level parity claim.

**Rule:** Label every accuracy result with its level and its reference implementation. Apply the framework's own pass criteria: per-prompt max target-Linf under `pp_static_thresholds [0.03, 0.05]`, aggregate Bhattacharyya coefficient above `agg_bc_threshold` (0.99), and `agg_sigma_ratio_threshold` (σ-ratio ≤ 1.0). Do not substitute one level's pass for another's claim.

**Evidence:** `docs/model-dev/accuracy-debugging-guide.md:30-72` (levels; "KV cache BC ≥ 0.99" at `:59`) and `:277-283` (`lm_eval` invocation); `vllm_neuron/accuracy/logit_validation.py:53-63` (`DEFAULT_AGGREGATE_CONFIG`); `vllm_neuron/accuracy/kv_cache_analysis.py:575-589` (`_compute_bc`).

## Logit capture needs a dedicated server config — logprobs fail silently or crash otherwise

**Trap:** Under async scheduling with on-device sampling, logprobs are not returned at all. Separately, one campaign found that requesting `logprobs` through the OpenAI completions handler under on-device sampling crashed the server (pre-existing bug), forcing exact string match as the substitute correctness check.

**Rule:** For Level-2 online logit validation, launch the server the way the bundled example does: `--max-logprobs -1 --logprobs-mode raw_logits --no-async-scheduling --no-enable-prefix-caching`, with on-device sampling re-enabled through `--additional-config '{"neuron_config": {"on_device_sampling_config": {}, ...}}'`. If logprobs are unavailable on a given config, fall back to greedy exact string match and say so in the evidence — do not report absent logprobs as agreement.

**Evidence:** `examples/vllm_neuron/accuracy/run_logit_validation_online.py:8-9` (async-scheduling note), `:206-215` (additional-config) and `:216-227` (server flags). Crash precedent: `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/correctness.md:95-107`, citing upstream `vllm/entrypoints/openai/completion/serving.py:623` (campaign history).

## Declare the parity criterion before capture — cross-backend runs are not bit-exact

**Trap:** A GPU-oracle vs Neuron comparison produced only 143/320 (~45%) token-exact request matches; FP8 backend execution and reduction order are not bit-exact. The claim had to be downgraded after the fact to "semantic smoke pass, token-exact parity not claimed," with no pre-registered threshold for what "semantic" required. The same closure record excluded performance from acceptance entirely.

**Rule:** Before capturing any cross-backend comparison, declare the match criterion (token-exact, semantic, or statistical) and its numeric threshold. Never promote a semantic pass into a token-exact parity claim. When reading a prior closure record, check its exclusions — a closed campaign with `performance_targets: not_claimed` is not performance evidence.

**Evidence:** GLM-5.2 model-port campaign, `artifacts/final-product-scope-closure-20260820.json` (`semantic_diagnostic` and `excluded_from_acceptance` blocks) (campaign history).

## Treat framework-NEFF profile analysis as approximate — verify against live traces

**Trap:** The profile-analysis delegate states its own methodology is "not fully validated for framework-compiled NEFFs," and its hard-gate section is distilled from real failures: replay-derived device times undercutting live times, aggregation errors, and one fully fabricated subagent analysis. Single query metrics invite over-diagnosis.

**Rule:** Take device time from the live `nc_exec_running` system trace, never from an isolated replay. Recompute overlap-merged and cross-rank numbers independently of any delegate's summary. Do not accept a root-cause or bottleneck claim without the primary profile artifact behind it, and do not diagnose from a single metric.

**Evidence:** `experimental-neuron-framework-profile-analysis-vllm-neuron` skill, `SKILL.md:17-20` (experimental banner) and `:22-128` (verification discipline, incl. `references/fabricated-analysis-case-study.md`); `neuron-nki-profile-querying` skill, `SKILL.md:294-310`; `vllm-neuron-feature-port` skill, `references/delegation.md:18`.
