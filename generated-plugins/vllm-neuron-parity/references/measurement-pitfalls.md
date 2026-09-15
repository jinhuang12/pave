# Measurement pitfalls — vllm-neuron parity runs

Rules for every seat that turns an observation into evidence, the measurer above all. Each section is one trap seen in a real campaign, the rule that prevents it, and where the evidence lives. Repo paths are relative to the vllm-neuron checkout; line numbers move on a pin bump, so trust the rule over the line. "L-<id> (campaign history)" names a verified learning from a prior campaign; the rule stands without the artifact. What the compiler and runtime say about themselves is the sibling file, `references/toolchain-evidence-pitfalls.md`.

## Pre-register the comparator set — the no-change baseline is mandatory

**Trap:** A `benefit_shown` verdict compared the fix only against other speculative-decode variants. The never-measured no-spec baseline was faster, and the verdict flipped to `no_benefit`. The measurer had also chosen the comparators.

**Rule:** Before any benefit measurement, write down the full comparator set and the decision rule. Always include the unchanged baseline at the exact matched config (same server args, same battery, same seed). The agent that measures a number does not adjudicate it. When a serving-config (DP×TP) choice affects fairness, expect an interior-optimum TP for collective-bound models — do not assume max-TP.

**Evidence:** P-EAGLE campaign `run-state.json` entries 51–53 and `benchmark/benefit.md` §8.5 (campaign history).

## Prove the check tool before its verdict counts

**Trap:** Check tools are code with dead paths. One comparator exited zero when its criterion was never recorded and hard-failed on any divergence when it was; a gate hard-coded the parameter the fix changed and graded a correct fix FAIL; a fingerprint covered part of its artifact; a global metric hid a per-row defect; a trailing dtype cast and on-device sampling each minted false divergence.

**Rule:** Before a verdict counts, prove the check tool on a known-positive AND a known-negative. Register, per criterion, the value that must appear as evaluated in the evidence and a known-bad input the procedure must fail on; run both in the procedure's smoke record; and require the evidence bundle to show which threshold was evaluated, with the value read and the result of the comparison. An exit status is not an evaluation. State the field set a fingerprint covers and guard each uncovered field separately. Grade per row class, and record the geometry with the number. Record the capture point and dtype on both sides of any reference comparison, and compare at the same point. Exclude a sampling position that disagrees with its own payload, count those exclusions, cap the count, and over the cap judge the capture unusable and re-capture — never attribute it to port code.

**Rule (measured but not graded):** A criterion whose check tool cannot be made to fail is unadjudicable. Surface it; do not reword it, and do not grade with it.

**Evidence:** L-119, L-171, L-177, L-178, L-189, L-204 (campaign history). The graph carries this as `acceptance_threshold_evaluated` and the negative-control half of `procedures_smoke_verified`; registration shape: `references/artifact-layout.md` §4.5.

## A zero is evidence only with a firing control

**Trap:** Every evidence channel has a precondition you cannot see in its output (dump format, verbosity, file selection), so a zero is indistinguishable from a channel that never fired. Measured: a truncated dump, an empty kernel channel after a kill, a "newest" log from a run that never warmed, a pattern the run verbosity never emitted.

**Rule:** Pair every count with two controls on the same artifact type at the same verbosity: a firing control that must hit, and a bogus control that must not. Validate the pattern itself — anchor numeric matches at word boundaries, confirm the target line exists at this verbosity, confirm the pattern matches only the pass you mean. Name the exact file you read, never "the newest". Record a zero you cannot prove complete as unproven, never as zero. For a scan whose validity depends on tree state, follow the scan-completeness discipline at `references/artifact-layout.md` §4.6.

**Why:** a false hit costs a round; a false zero sends a defect downstream under a green record.

**Evidence:** L-028, L-049, L-076, L-082, L-101, L-127, L-156, L-205 (campaign history).

## Record the emitter, the stage, and the divisor with every count

**Trap:** A printed number here is normalized, paced, sampled, or re-emitted, with nothing in the line to say so: the compiler re-prints one error about three times, a count differs by the stage it is read at, percentage columns are self-normalized and capped, and the runtime error stream is paced by a timer.

**Rule:** Carry with every count its emitter, stage, and divisor or normalizer. Establish what it counts from the producing code, schema, or a controlled sample. If its role remains ambiguous, cross-check an independently emitted quantity with a known arithmetic relation; agreement without that relation proves nothing. Prove any double-emission factor on the measured file, per family. Do not normalize by assumption or infer per-item cost until the emission pacing is known. When a printed limit and a static specification disagree, establish what the installed program enforces before using either as a capacity bound.

**Evidence:** L-017, L-019, L-021, L-038, L-055, L-062, L-095, L-097, L-196, L-201, L-206, L-239, L-277 (campaign history).

## Do not adjudicate spec-decode comparisons from chunk-derived throughput — any harness, stock or custom

**Trap:** A campaign's streaming harness derived tokens/sec from stream-chunk counts. Speculative decode emits several accepted tokens per chunk, so spec configs were undercounted; the custom harness reproduced the trap it had avoided the stock tool for.

**Rule:** For spec-decode benefit legs, measure end-to-end latency for a fixed N-token generation over `/v1/completions` with `stream: true`, not chunk-derived throughput. Repeat the fixed prompt battery (precedent: 24 prompts × 3 repeats = 72 requests per config) — never adjudicate from a single read.

**Evidence:** P-EAGLE campaign `benchmark/benefit.md:34-45, 51-75` (campaign history).

## Decode-bench connector output is throughput evidence only — never correctness

**Trap:** `NeuronDecodeBenchConnector` fakes a completed prefill; its `_fill_blocks` is a no-op, so decode runs against uninitialized KV and the logits are garbage by design.

**Rule:** Use runs made with this connector only as decode-throughput measurements at a fixed decode NEFF shape. Never cite their outputs as correctness, accuracy, or output-quality evidence, and never mix them into a leg that also checks outputs.

**Evidence:** `vllm_neuron/vllm/kv_connector/neuron_decode_bench_connector.py` (docstring item 5; `_fill_blocks`).

## Control compile-cache state — and never clear the shared cache

**Trap:** The compile cache is shared and multi-writer. Novel configs add compiles, so a cold leg pays what a warm leg does not; two delegate skills prescribe `rm -rf` of the cache as a first remedy, which destroys every co-tenant's warm state; `NEURON_COMPILE_CACHE_URL` is read nowhere.

**Rule:** Pin the cache root explicitly with `VLLM_CACHE_ROOT` (resolved to `$VLLM_CACHE_ROOT/neuron/compile_cache`); do not rely on any other variable. Confirm every rank resolves the same root. Record warm/cold cache state per measurement leg and keep compared legs at equal warmth. Never clear a shared cache directory as a debugging or measurement remedy — override the delegate skills' `rm -rf` instructions. The kernel toolchain writes its own intermediate cache outside the run root and outside any variable you set; that directory is shared state too, and a delete there destroys a co-tenant's artifacts. Inside a root you own, rename a partition aside rather than deleting it, so every new artifact is provably post-change: a re-trace rewrites the graph text in every key directory, so a stale pre-change graph can otherwise survive and raise a wall that reads like your fix failing.

**Rule (which fix site to pick):** The cache key hashes graph structure — graph text, replica groups, per-input metadata, versions, and compiler arguments — not values, not the cache path, and not the host. Re-derive the component list at your own pin before you reason from it: the fork at 0.21 also hashed an FX-pass source fingerprint, a port-added component that is NOT in the 0.24 key list, where the compile path moved into `libtorch_neuronx_lite`. So when two fix sites are both valid, change the input producer, keep the graph text identical, and the warm cache survives; batch every key-changing edit into one deciding commit before a long compile; and prove the cache survived with a zero compiled-graph count on a start you expect to be warm — find the equivalent counter for your pin first, because the counter moved with the compile path at 0.24. An artifact built at the same pins is usable from another cache root.

**Evidence:** `vllm_neuron/envs.py` (`get_neuron_compile_cache_dir`); `vllm_neuron/compile/cache.py` (`CompilationLock`, `save_cache`); the profiling and compiler-debugging delegate skills carry the conflicting `rm -rf` remedy; L-136, L-159, L-318, L-330, L-360 (campaign history).

## Do not adjudicate evidence on first sighting — wait for stable reads

**Trap:** A watcher adjudicated when `exit-status.txt` appeared. The runner writes that file before the post-run source, HLO, NEFF, and cache-count files, so good evidence was graded `failed`.

**Rule:** After a completion signal appears, re-read the full evidence set until N consecutive reads are identical before adjudicating. Take N and the minimum re-read spacing from the campaign design record; never default them. A process-exit signal is not proof that output artifacts are fully materialized.

**Evidence:** GLM-5.2 campaign `working-profile-512/adjudication-manifest.json` (`watcher_race_adjudication`) (campaign history).

## A completion signal reports a position, not completion

**Trap:** Producers here signal before they finish: an artifact timestamp and the driver's tear-down line precede a compile's end, a warmup log line precedes device completion, a poll-derived global "last pass" can move backward, and a readback placed after the primary one reads zero on every failing run.

**Rule:** Take completion from the producer's own completion line plus a check that no producing process is still alive. Count progress per module tag, never from a global tail value. Gate any device claim on a device completion counter or a forced readback, never on a log line. Confirm readback order in the loaded module before you report a zero readback as clean. This is the producer-side half of the stable-read rule above.

**Evidence:** L-064, L-175, L-300, L-384 (campaign history).

## Size host memory in the deployed multi-rank context

**Trap:** A tensor-parallel serve loads one compiled artifact per rank into one host, so host memory binds at load and no Neuron counter reports it. A single-process load under-reports the peak, a kernel out-of-memory kill leaves no trace in the victim's log, and a cap on address space reads as a false out-of-memory finding.

**Rule:** Measure peak per-worker host memory from process-level sampling inside the full multi-rank serving context, at five-second spacing or faster to catch the transient peak, and add margin above any off-serve figure. Trace memory and log bytes on the same clock: a phase that allocates while the log stays flat is its own suspect, and log volume is not a proxy for allocation. When you bound a probe, cap private anonymous memory, never virtual address space. For a suspected kernel out-of-memory kill, read an authoritative kernel channel and treat an empty render or a non-zero exit as unproven, never as zero. Before you call a host wall a capacity limit, compare the failed leg against the last leg that served the same arguments, and record which leg is the comparator.

**Evidence:** L-148, L-156, L-173, L-183, L-194, L-246, L-345, L-368 (campaign history).

## Capture the stream the component actually writes

**Trap:** The compile phase redirects its own stdout, so the tee'd log stops growing while work proceeds. The runtime at debug level writes gigabytes to stdout in a minute, so a stderr-only capture is empty and an uncapped one blows its disk.

**Rule:** Tee both stdout and stderr into a size-capped sink for every leg, then aggregate from the file. Read a growth-rate collapse as a transition and confirm it against the stage markers. Write any harness input file to the consumer's own parser, not to shell semantics.

**Evidence:** L-123, L-126, L-129 (campaign history).

## CPU-mode results are never performance or hardware-accuracy evidence

**Trap:** `VLLM_NEURON_CPU_MODE=1` replaces Neuron device execution; its numbers say nothing about device performance and its accuracy differs from hardware.

**Rule:** Record the execution mode with every result. Never present CPU-mode timings as Neuron performance evidence, and never present CPU-mode outputs as hardware-accuracy evidence. Note the flag is mutually exclusive with `VLLM_NEURON_CPU_COMPILE` (the runtime raises `RuntimeError`).

**Evidence:** `vllm_neuron/envs.py` (flag definition); `vllm_neuron/__init__.py` (mutual-exclusion error); `docs/model-dev/cpu-development.md`.

## Verify tool-to-stack version alignment before spending

**Trap:** The equivalence skill's vLLM-Neuron adapter is pinned to the 0.24 line and exits before measuring on a 0.21 stack. One campaign lost its planned correctness method mid-gate this way.

**Rule:** Before dispatching any measurement tool, confirm the installed `vllm`/`vllm-neuron` versions match the tool's pin. On an unbridgeable mismatch, record `validation_blocked` and use the sanctioned fallback (greedy string-equality on a fixed battery plus an acceptance-rate floor from `/metrics`) — do not improvise a partial run. Record the exact target commit and vLLM pin with every measurement.

**Evidence:** `neuron-framework-equivalence` skill, `scripts/adapters/vllm_neuron.py` (`PINNED_VLLM_VERSION`); fallback precedent: P-EAGLE campaign `validation/correctness.md` (campaign history).

## Always pass `--target-stack vllm_neuron` — auto-detect routes to the wrong stack

**Trap:** Every equivalence-skill stage script accepts `--target-stack`, default unset, and the config template's auto-detect default is `nxdi`, so omitting the flag silently measures the wrong stack and returns plausible numbers for it.

**Rule:** Pass `--target-stack vllm_neuron` explicitly on every equivalence-skill stage invocation. Treat any equivalence result whose config does not show `vllm_neuron` as invalid.

**Evidence:** `neuron-framework-equivalence` skill, `templates/equiv_config_template.json`.

## State the validation level and the reference for every accuracy claim

**Trap:** The accuracy framework has three levels that answer different questions: Level 1 task scores via `lm_eval`, Level 2 prompt-level logit and KV-cache comparison, Level 3 module-level vs HF. Levels 2 and 3 compare against HF-transformers CPU logits, not GPU-vLLM, so a Level-2 pass does not support a "matches GPU serving" claim.

**Rule:** Label every accuracy result with its level and its reference implementation. Apply the framework's own pass criteria: per-prompt max target-Linf under `pp_static_thresholds [0.03, 0.05]`, aggregate Bhattacharyya coefficient above `agg_bc_threshold` (0.99), and `agg_sigma_ratio_threshold` (σ-ratio ≤ 1.0). Do not substitute one level's pass for another's claim.

**Evidence:** `docs/model-dev/accuracy-debugging-guide.md`; `vllm_neuron/accuracy/logit_validation.py` (`DEFAULT_AGGREGATE_CONFIG`); `vllm_neuron/accuracy/kv_cache_analysis.py` (`_compute_bc`).

## Logit capture needs a dedicated server config — logprobs fail silently or crash otherwise

**Trap:** Under async scheduling with on-device sampling, logprobs are not returned at all; in one campaign, requesting them through the completions handler crashed the server, forcing exact string match as the substitute check.

**Rule:** For Level-2 online logit validation, launch the server the way the bundled example does: `--max-logprobs -1 --logprobs-mode raw_logits --no-async-scheduling --no-enable-prefix-caching`, with on-device sampling re-enabled through `--additional-config '{"neuron_config": {"on_device_sampling_config": {}, ...}}'`. If logprobs are unavailable on a given config, fall back to greedy exact string match and say so in the evidence — do not report absent logprobs as agreement.

**Evidence:** `examples/vllm_neuron/accuracy/run_logit_validation_online.py` (server flags, additional-config); crash precedent: P-EAGLE campaign `validation/correctness.md:95-107` (campaign history).

## Declare the parity criterion before capture — cross-backend runs are not bit-exact

**Trap:** A GPU-oracle vs Neuron comparison matched 143 of 320 requests token-exact (FP8 execution and reduction order are not bit-exact) and had to be downgraded after the fact to a semantic pass with no pre-registered threshold.

**Rule:** Before capturing any cross-backend comparison, declare the match criterion (token-exact, semantic, or statistical) and its numeric threshold. Never promote a semantic pass into a token-exact parity claim. When reading a prior closure record, check its exclusions — a closed campaign with `performance_targets: not_claimed` is not performance evidence.

**Evidence:** GLM-5.2 campaign `artifacts/final-product-scope-closure-20260820.json` (`semantic_diagnostic`, `excluded_from_acceptance`) (campaign history).

## Treat framework-NEFF profile analysis as approximate — verify against live traces

**Trap:** The profile-analysis delegate says its own methodology is not fully validated for framework-compiled NEFFs; its hard-gate section records replay-derived times undercutting live times, aggregation errors, and one fabricated subagent analysis.

**Rule:** Take device time from the live `nc_exec_running` system trace, never from an isolated replay. Recompute overlap-merged and cross-rank numbers independently of any delegate's summary. Do not accept a root-cause or bottleneck claim without the primary profile artifact behind it, and do not diagnose from a single metric.

**Evidence:** `experimental-neuron-framework-profile-analysis-vllm-neuron` skill, `SKILL.md` (experimental banner, verification discipline, `references/fabricated-analysis-case-study.md`); `neuron-nki-profile-querying` skill, `SKILL.md`.
