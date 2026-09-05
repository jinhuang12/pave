# Measurement pitfalls — vllm-neuron parity runs

Operational rules for every seat that turns an observation into evidence — the measurer above all, and any seat that reports a count. Each rule is an imperative to honor before a number becomes evidence. What the Neuron compiler and runtime say about themselves is the sibling file, `references/toolchain-evidence-pitfalls.md`; read that one before you credit a compiler flag, cost a compile, or localize a device wedge.

Citation classes used below:
- Unqualified repo paths are relative to the vllm-neuron checkout (`/Users/jinhun/GitHub/vllm-neuron`). Verified at branch `feature/p-eagle-gpt-oss-20b`, commit `0e19f00`, plugin version `0.21.0.1.0.0` (`pyproject.toml:10`), vLLM pin `vllm==0.21.0` (`requirements/core.txt:6`). Line numbers move on a pin bump — re-derive with grep, and trust the rule over the line number.
- A citation naming a `<name>` skill points at an installed delegate skill in the host workspace, at that skill's own directory.
- A citation marked "(campaign history)" names an artifact from a prior port campaign, or an `L-<id>` from a prior campaign's verified learning set re-checked at Neuron SDK 2.32 and vllm-neuron 0.24. The rule stands on its own without it; treat the artifact and the id as provenance, not as a file you must have.

## Pre-register the comparator set — the no-change baseline is mandatory

**Trap:** A benefit verdict of `benefit_shown` was reached by comparing the fixed code path only against other speculative-decode variants (92.7 tok/s vs a defective 127.9 and a sequential 40.0). No-spec at the same matched config was never measured. When it was measured (199.0 tok/s), the verdict flipped to `no_benefit`. The measuring agent had also selected the comparators.

**Rule:** Before any benefit measurement, write down the full comparator set and the decision rule. Always include the unchanged baseline at the exact matched config (same server args, same battery, same seed). The agent that measures a number does not adjudicate it. When a serving-config (DP×TP) choice affects fairness, expect an interior-optimum TP for collective-bound models — do not assume max-TP.

**Evidence:** `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json`, entries 51–53, and `benchmark/benefit.md` §8.5 (campaign history). TP note: `vllm-neuron-feature-port` skill, `references/delegation.md:21`.

## Prove the instrument before its verdict counts

**Trap:** The instruments that grade a port are themselves code, with dead paths and unstated scope. A shipped parity comparator had no scalar-score path and a dead `primary` field: whenever the criterion it needed was never recorded it exited zero with an empty evaluated-threshold list, and whenever the criterion was recorded it hard-failed on any token divergence. Both readings look like a verdict. Separately, a pre-registered gate called a low-level function with the changed parameter hard-coded, so it never reached the code the fix changed and graded a correct fix FAIL. A fingerprint can cover only part of the artifact it names. A global error metric can hide a catastrophic per-row defect, because sparse row geometry lowers the global ratio. A trailing dtype cast after a collective mints a small deterministic divergence that reads as a real parity failure. And on-device sampling can emit a token that disagrees with its own logprob payload.

**Rule:** Before a verdict counts, prove the instrument on a known-positive AND a known-negative. Register, per criterion, the value that must appear as evaluated in the evidence and a tripwire input the procedure must fail on; run both in the procedure's smoke record; and require the evidence bundle to show which threshold was evaluated, with the value read and the result of the comparison. An exit status is not an evaluation. State the field set a fingerprint covers and guard each uncovered field separately. Grade per row class, and record the geometry with the number. Record the capture point and dtype on both sides of any reference comparison, and compare at the same point. Exclude a sampling position that disagrees with its own payload, count those exclusions, cap the count, and over the cap judge the capture unusable and re-capture — never attribute it to port code.

**Rule (measured but not graded):** A criterion whose instrument cannot be made to fail is unadjudicable. Surface it; do not reword it, and do not grade with it.

**Evidence:** L-119, L-171, L-177, L-178, L-189, L-204 (campaign history). The graph carries this duty as `acceptance_threshold_evaluated` and as the negative-control half of `procedures_smoke_verified`; the registration shape is `references/artifact-layout.md` §4.5.

## A zero is evidence only with a firing control

**Trap:** Every evidence channel in this stack has a precondition you cannot see in its output: a dump format, a log verbosity, a file-selection rule, or a completeness rule. A search that returns zero is indistinguishable from a channel that never fired. Measured instances: a truncated dump gave a false grep-zero; a restricted kernel-message channel rendered empty although the kernel had killed the process; a per-rank log chosen as newest-by-timestamp belonged to a run that never warmed, so its counters were a scoped zero; a grep pattern matched a line the run verbosity never emitted.

**Rule:** Pair every census with two controls on the same artifact type at the same verbosity: a firing control that must hit, and a bogus control that must not. Validate the pattern itself — anchor numeric matches at word boundaries, confirm the target line exists at this verbosity, confirm the pattern matches only the pass you mean. Name the exact file you read, never "the newest". Record a zero you cannot prove complete as unproven, never as zero. For a scan whose validity depends on tree state, record the commit scanned, the tree state it was scanned in, and the tool's own completion signal (`references/artifact-layout.md` §4.6).

**Why:** a false zero and a false hit fail in opposite directions and cost different things — a false hit costs a lap, a false zero sends a defect downstream under a green record.

**Evidence:** L-028, L-049, L-076, L-082, L-101, L-127, L-156, L-205 (campaign history).

## Record the emitter, the stage, and the divisor with every count

**Trap:** A printed number in this stack is normalized, paced, sampled, or re-emitted, and none of that is visible in the number. The compiler driver re-prints the backend error block inside its own exception report, so one genuine error appears about three times in one log. A pass multiplies the intermediate representation between the two stages at which a count can be read, so a count read at the wrong stage understates the checked count. The compiler self-normalizes its percentage columns inside a shard group and caps its top-N rows, so the listed rows are a sample. The runtime's error-notification stream is paced by a hardware coalescing timer and a host drain thread, so a fixed period is not per-item device cost. Some log families double-emit and others do not, on the same file. A live-incrementing execution tally carries a label from the live request count, not from the compiled bucket. And a bare number can be a hard limit or a size report, with nothing in the line to tell you which.

**Rule:** Carry with every count its emitter, stage, and divisor or normalizer. Establish what it counts from the producing code, schema, or a controlled sample. If its role remains ambiguous, cross-check an independently emitted quantity with a known arithmetic relation; agreement without that relation proves nothing. Prove any double-emission factor on the measured file, per family. Do not normalize by assumption or infer per-item cost until the emission pacing is known. When a printed limit and a static specification disagree, establish what the installed program enforces before using either as a capacity bound.

**Evidence:** L-017, L-019, L-021, L-038, L-055, L-062, L-095, L-097, L-196, L-201, L-206, L-239, L-277 (campaign history).

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

**Rule:** Pin the cache root explicitly with `VLLM_CACHE_ROOT` (resolved to `$VLLM_CACHE_ROOT/neuron/compile_cache`); do not rely on any other variable. Confirm every rank resolves the same root. Record warm/cold cache state per measurement leg and keep compared legs at equal warmth. Never clear a shared cache directory as a debugging or measurement remedy — override the delegate skills' `rm -rf` instructions. The kernel toolchain writes its own intermediate cache outside the run root and outside any variable you set; that directory is shared state too, and a delete there destroys a co-tenant's artifacts. Inside a root you own, rename a partition aside rather than deleting it, so every new artifact is provably post-change: a re-trace rewrites the graph text in every key directory, so a stale pre-change graph can otherwise survive and raise a wall that reads like your fix failing.

**Rule (which fix site to pick):** The cache key hashes graph structure — graph text, replica groups, per-input metadata, versions, and compiler arguments — not values, not the cache path, and not the host. Re-derive the component list at your own pin before you reason from it: the fork at 0.21 also hashed an FX-pass source fingerprint, a port-added component that is NOT in the 0.24 key list, where the compile path moved into `libtorch_neuronx_lite`. So when two fix sites are both valid, change the input producer, keep the graph text identical, and the warm cache survives; batch every key-changing edit into one settling commit before a long compile; and prove the cache survived with a zero compiled-graph count on a start you expect to be warm — find the equivalent counter for your pin first, because the compile path moved into `libtorch_neuronx_lite` at 0.24 and the counter moves with it. An artifact built at the same pins is usable from another cache root.

**Evidence:** `vllm_neuron/envs.py:341-391` (`get_neuron_compile_cache_dir`; `:364` reads `VLLM_CACHE_ROOT`, `:376` joins `neuron/compile_cache`); `vllm_neuron/compile/cache.py:328-376` (`CompilationLock`) and `:156-220` (`save_cache`, atomic rename, concurrent-safe); `grep -rn NEURON_COMPILE_CACHE_URL` in the repo returns zero hits. Cache growth: `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/correctness.md:27-28` (campaign history). Conflicting remedies: `experimental-neuron-framework-profiling-vllm-neuron` skill `SKILL.md:375, 424-425`; `experimental-neuron-autoport-compiler-debugging-vllm-neuron` skill `SKILL.md:105-107, 146-148`. Shared-cache and key-structure additions: L-136, L-159, L-318, L-330, L-360 (campaign history).

## Do not adjudicate evidence on first sighting — wait for stable reads

**Trap:** A watcher stopped waiting when `exit-status.txt` appeared. The runner writes that file before the post-run source, HLO, NEFF, and cache-count files, so the watcher evaluated file existence inside the creation window and produced a false `failed` verdict on good evidence.

**Rule:** After a completion signal appears, re-read the full evidence set until N consecutive reads are identical before adjudicating. Take N and the minimum re-read spacing from the campaign design record; never default them. A process-exit signal is not proof that output artifacts are fully materialized.

**Evidence:** GLM-5.2 model-port campaign, `working-profile-512/adjudication-manifest.json:71-84` (`watcher_race_adjudication` block) (campaign history).

## A completion signal reports a position, not completion

**Trap:** The producers in this stack signal before they finish. A compiled-artifact timestamp and the driver's own tear-down line both precede the real end of a compile. A framework log line saying a warmup stage completed precedes device completion, because with async scheduling disabled the runner skips its only readback and the asynchronous device primitive returns early. Under the compiler's modular flow there is no per-pass barrier, so a poll-derived global "last pass" reports one module's position and can move backward. And a diagnostic readback placed after the primary readback reads zero on every failing run, which reads as clean.

**Rule:** Take completion from the producer's own completion line plus a check that no producing process is still alive. Count progress per module tag, never from a global tail value. Gate any device claim on a device completion counter or a forced readback, never on a log line. Confirm readback order in the loaded module before you report a zero readback as clean. This is the producer-side half of the stable-read rule above.

**Evidence:** L-064, L-175, L-300, L-384 (campaign history).

## Size host memory in the deployed multi-rank context

**Trap:** On this hardware class a tensor-parallel serve loads one compiled artifact per rank into one host, so the binding resource at load is host memory, not device memory — and no Neuron counter reports it. Declared graph I/O bytes do not size it: the host-reservation multiplier is not constant, and host memory can rise while declared graph state falls. An isolated single-process load under-reports the concurrent peak. A kernel out-of-memory kill leaves no trace in the victim's log. And a bound on a probe that caps address space instead of private memory aborts the process on a harmless reservation, which reads as a false out-of-memory finding.

**Rule:** Measure peak per-worker host memory from process-level sampling inside the full multi-rank serving context, at five-second spacing or faster to catch the transient peak, and add margin above any off-serve figure. Trace memory and log bytes on the same clock: a phase that allocates while the log stays flat is its own suspect, and log volume is not a proxy for allocation. When you bound a probe, cap private anonymous memory, never virtual address space. For a suspected kernel out-of-memory kill, read an authoritative kernel channel and treat an empty render or a non-zero exit as unproven, never as zero. Before you call a host wall a capacity limit, compare the failed leg against the last leg that served the same arguments, and record which leg is the comparator.

**Evidence:** L-148, L-156, L-173, L-183, L-194, L-246, L-345, L-368 (campaign history).

## Capture the stream the component actually writes

**Trap:** Components here write to non-obvious streams at non-obvious rates. The compile phase redirects its own stdout away, so the tee'd log stops growing while work proceeds — a collapse in log-growth rate is a stage transition, not a stall. The runtime at debug level writes its log to stdout, about two gigabytes in ninety seconds in one measured load: a capture that reads only stderr gets an empty stream, and a capture with no size guard blows its cap. And a harness that parses its own input file as key-value data with a line pattern does not run shell semantics over it.

**Rule:** Tee both stdout and stderr into a size-capped sink for every leg, then aggregate from the file. Read a growth-rate collapse as a transition and confirm it against the stage markers. Write any harness input file to the consumer's own parser, not to shell semantics.

**Evidence:** L-123, L-126, L-129 (campaign history).

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
