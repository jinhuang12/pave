# Exploration: validation and acceptance (lens 2)

## 1. Question investigated

How were correctness and performance gates ACTUALLY executed and adjudicated
in past port campaigns — exact commands, configs, artifacts, thresholds, and
where adjudication went wrong — so the new plugin's shared validation
back-end can be designed from practice, not theory.

## 2. Evidence inventory

Primary artifacts read in full or in relevant part:

- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/correctness.md` (256 lines)
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/benchmark/benefit.md` (743 lines)
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/validation/battery-*.json` (per-prompt outputs)
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/benchmark/bench-*.json`
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json` (entries 51–53, the wrong-baseline incident)
- `feature_port_campaigns/spec-decode-economics-2026-08-11/FINDINGS.md` and `run-state.json`
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/acceptance-surfaces/capture_gpu_oracle.py`
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/working-profile-512/{acceptance-matrix.json,adjudication-manifest.json,http-adjudication-manifest.json,adjudicate_completed_run.py}`
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/final-product-scope-closure-20260820.json`
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/post-pr-remediation-queue.md`
- `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/artifacts/{campaign.json,acceptance-contract.json}`
- `/Users/jinhun/GitHub/vllm-neuron/vllm_neuron/accuracy/logit_validation.py`, `kv_cache_analysis.py`
- `/Users/jinhun/GitHub/vllm-neuron/vllm_neuron/vllm/kv_connector/neuron_decode_bench_connector.py`
- `/Users/jinhun/GitHub/vllm-neuron/examples/vllm_neuron/accuracy/{run_logit_validation_online.py,run_kv_cache_analysis.py}`
- `/Users/jinhun/GitHub/vllm-neuron/docs/model-dev/accuracy-debugging-guide.md`
- `skills/vllm-neuron-feature-port/references/{contribution-checklist.md,delegation.md}`
- `skills/experimental-neuron-framework-benchmark-vllm/{SKILL.md,references/metrics.md}` (result-artifact format only, per scope boundary)

Not opened (out of my scope per the brief): campaign state-machine/run-state
structure beyond the specific entries needed for the wrong-baseline
reconstruction; the benchmark skill's provisioning/spend-gate logic; the
equivalence skill's adapter internals beyond the version-pin fact already
cited by the P-EAGLE report.

## 3. Findings, with citations

### 3.1 The three validation "levels" that exist in the repo, and how they are actually invoked

Per `/Users/jinhun/GitHub/vllm-neuron/docs/model-dev/accuracy-debugging-guide.md:30-72`:

- **Level 1 (task-level):** `lm_eval` against a running vLLM server, scores vs
  user thresholds (`:36-45`). Example invocation given (`:277-282`):
  `lm_eval --model vllm --model_args pretrained=...,tensor_parallel_size=8 --tasks gsm8k --batch_size auto`.
- **Level 2 (prompt-level):** three-way logit comparison (HF FP32 → HF BF16 →
  Neuron) via teacher forcing, plus KV-cache comparison via Bhattacharyya
  Coefficient (`:47-59`). This is implemented by
  `vllm_neuron/accuracy/logit_validation.py` and `kv_cache_analysis.py`, driven
  by the example CLIs `run_logit_validation_online.py` /
  `run_logit_validation_offline.py` / `run_kv_cache_analysis.py`.
- **Level 3 (module-level):** per-module CPU+hardware tests vs HF reference
  (`:61-67`) — no example script inventoried in this pass; `testing.py` in the
  accuracy package is the likely home (not opened).

Concrete default thresholds found in code (not just docs), confirming the
requirements-brief numbers:
- `vllm_neuron/accuracy/logit_validation.py:54-62` —
  `pp_static_thresholds: [0.03, 0.05]` (per-prompt max target-linf),
  `agg_bc_threshold: 0.99` (Bhattacharyya coefficient), `agg_sigma_ratio_threshold: 1.0`.
- `kv_cache_analysis.py:575-591` — `_compute_bc` implements the same BC metric
  used for KV-cache classification (BF16-inherent vs anomalous), matching the
  "KV-cache BC ≥ 0.99" requirement line.

`run_logit_validation_online.py:192-249` shows the actual online mechanics:
compute FP32 + BF16 "golden" logits on CPU via
`AutoModelForCausalLM.from_pretrained(...)` with teacher forcing (`:46-117`),
then start (or attach to) a vLLM server with
`--logprobs-mode raw_logits --no-async-scheduling --additional-config
'{"neuron_config": {"on_device_sampling_config": {}, ...}}'` (`:216-227`), and
diff via `multi_prompt_logit_validation` (`:243-251`). Note (`:8-9` and inline
comment): `async_scheduling` must be OFF for logprobs to return, and on-device
sampling is explicitly re-enabled through `additional_config` for the
generate path. This is the concrete mechanism behind the goal-brief's claim
that Levels 2/3 compare against **HF-transformers reference logits, not
GPU-vLLM** — there is no GPU-vLLM logit-capture code path anywhere in this
tree; the closest thing to a GPU reference is the ad hoc `capture_gpu_oracle.py`
tool built during the GLM-5.2 model-port campaign (§3.4), which never emits
raw logits either — it captures token IDs/text.

### 3.2 P-EAGLE feature-port campaign: one campaign's gate execution end-to-end

This is the fullest real trace of a correctness-gate → benefit-gate → close-out
in the corpus. Reconstructed from
`feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/{validation/correctness.md,benchmark/benefit.md,run-state.json}`.

**Correctness gate (`validate_correctness.md`):**
1. Pre-flight: re-verify commit on host, `check_edit_live.py` PASS (editable
   install actually imports the branch), `git status --porcelain` clean
   (correctness.md:17-28).
2. Equivalence-skill version check FIRST, before running anything: adapter
   hard-pinned `PINNED_VLLM_VERSION = "0.24.0"` at
   `skills/neuron-framework-equivalence/scripts/adapters/vllm_neuron.py:46-47`,
   installed stack is `0.21.0` → hard exit, tool cannot run (`correctness.md:30-46`).
   Fallback mode ("greedy-equality") is design-sanctioned for this reason.
3. Three servers launched serially (parallel-spec / no-spec / sequential-spec),
   identical serve recipe except `--speculative-config`
   (`correctness.md:76-93`), each torn down with `kill -SIGINT <APIServer-pid>`
   before the next.
4. 24-prompt fixed battery (mixed types) at `temperature=0, seed=12345,
   max_tokens=64` via `/v1/completions` (`correctness.md:95-107`) — chosen
   because `logprobs` crashes the OpenAI completions handler under on-device
   sampling (a pre-existing plugin bug cited at
   `vllm/entrypoints/openai/completion/serving.py:623`), so exact string match
   substitutes for token-ID match.
5. Primary check: char-identical output vs no-spec baseline (17/24 match).
   Secondary check: acceptance-rate floor from `/metrics` counters
   (parallel 22.57% > sequential 19.41%, no collapse).
6. **Decisive control experiment**, not in any generic doc: run the same
   battery through the *pre-existing* sequential-EAGLE3 path (unmodified by
   this change) and show it diverges from no-spec MORE (10/24) than the new
   parallel path (7/24) — proving the divergence is pre-existing verify-graph
   numerics, not a defect the port introduced (`correctness.md:141-183`).
7. Reviewer explicitly flags a "strict-reading caveat": under a literal
   token-exact reading the verdict would be `incorrect`; the reviewer
   documents the reasoning for overriding to `correct` and leaves the override
   path open for the lead (`correctness.md:185-203`). Verdict: `correct`.

**Benefit gate (`benchmark_benefit.md`), across ~8 re-entries over 2026-08-10/11:**
1. Tooling decision: `vllm bench serve` works OOTB but the benefit signal is
   single-request latency at `max_num_seqs=1`; a manual streaming harness
   (`bench.py` over `/v1/completions stream:true`) is used instead, because
   spec-decode emits multiple accepted tokens per stream chunk and chunk-count
   throughput undercounts spec configs — "e2e latency for a fixed 64-token
   generation" is used as the honest metric instead (`benefit.md:33-49`).
2. Configs differ only in `--speculative-config`; battery reused from
   validation, repeated ×3 = 72 requests/config (`benefit.md:51-75`).
3. First verdict (`benefit_shown`): parallel is 2.64× faster than *sequential
   EAGLE3* — the design-declared baseline — at higher acceptance
   (`benefit.md:104-125`). Honest caveat recorded in the same doc: no-spec is
   faster than ALL spec modes (`benefit.md:15-22, 117-125`).
4. Multiple re-entries (oracle-leg documented-recipe run, stock-wheel control,
   chat-workload round, GSM8K round, external cross-check) progressively test
   whether the "no-spec wins" finding is an artifact of drafter choice, K
   value, workload distribution, or the editable install — none flip the
   ranking (`benefit.md:152-332`).
5. **Root-cause found** (`benefit.md:450-559`, §7.7): a genuine port defect in
   `_forward_parallel` (`vllm_neuron/model/llama3/eagle3_model.py`) — the
   parallel drafter never gets real per-token target hidden states for
   context positions, only for the single bonus token — discovered via a
   pre-registered two-investigator fan-out (CPU code audit + hardware
   discriminator) whose interpretation rule was written down BEFORE either
   result existed. Fixed under an approved design revision; hardware
   re-validated (§8.1–8.4).
6. **The wrong-baseline incident** (this is precedent 5 in the requirements —
   see §3.3 for the exact text) — a benefit verdict of `benefit_shown` was
   reached (`run-state.json` entry ~51) by comparing the FIXED parallel path
   only against other SPEC variants (defective parallel, sequential), never
   against no-spec at the same matched config. The user caught it.
7. Final verdict after correction: `no_benefit` — spec decode is net-negative
   vs no-spec in every regime tested (TP8 serve K∈{2,5}; TP16 offline K=5).
   Root cause profiled to hardware: drafter NEFF is DMA-bound (23.77ms/step,
   97.4% DMA-busy, 7.1% compute-active) due to a KV-gather over-fetching the
   full `max_model_len` window regardless of active seqlen
   (`spec-decode-economics-2026-08-11/FINDINGS.md:26-47`).
8. Campaign closed `closed_no_benefit_with_scoped_source_fix_path` — "correct
   port, no benefit" — exactly the precedent the requirements brief encodes as
   a clean close-out route (`FINDINGS.md:6-13`).

### 3.3 The wrong-baseline incident, verbatim (requirement 5's precedent)

`feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/run-state.json`, entry
with `"node": "benchmark_benefit", "outcome": "verdict_correction_opened"`
(around line 450):

> "USER FLAG: 'something is wrong — think about the high level goal and the
> results.' Lead concurs; entry 51's benefit_shown outcome used WRONG
> BASELINES. The claim 'benefit holds in the batched/offline regime' compared
> fixed parallel (92.7 tok/s) against the DEFECTIVE parallel (127.9) and
> SEQUENTIAL (40.0) — never against NO-SPEC at that config, which was never
> measured. ... Entry 51's benefit_shown is SUPERSEDED pending the missing
> baseline. ... PRE-REGISTERED RULE before measurement: run no-spec at the
> exact matched config ... If no-spec tok/s > 92.7 -> outcome is no_benefit
> ...; if <= 92.7 -> offline-regime benefit claim survives with the correct
> baseline. Either outcome recorded."

The following entry resolves it: no-spec measured at 199.0 tok/s > 92.7 →
verdict flips to `no_benefit` (`run-state.json`, next entry; also
`benefit.md:659-676`, §8.5). **Root of the failure:** the agent measuring the
number (benchmark_benefit) also picked which comparators counted as "the
baseline" across several re-entries, without an explicit, pre-registered
statement that no-spec-at-matched-config specifically had to be one of them —
until the user manually re-derived the high-level goal and caught the gap.
This is direct evidence for the requirements-brief rule "the agent that
measured a number never adjudicates it" and "kickoff-declared metrics/
thresholds/methods change only by explicit user decision."

### 3.4 GLM-5.2 model-port campaign: a heavier, more forensic (and partly unfinished) validation apparatus

`model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/acceptance-surfaces/capture_gpu_oracle.py`
is exactly the "GPU-vLLM vs Neuron-vLLM capture tooling" the goal brief
anticipates sizing into a campaign when logit/KV comparison against the
GPU-vLLM *engine* (not HF) is genuinely needed. It is a real, working, ~1270-line
tool, not a stub:

- Four phases (`build-corpus` → `capture-offline` → `capture-http` →
  `assemble`), each independently re-runnable so a 756GB checkpoint load is
  never repeated just to fix evidence packaging (`capture_gpu_oracle.py:1-11`).
- Pins and re-verifies, at every phase: checkpoint file SHA-256s and full
  content inventory (`:103-235`), vLLM git revision + dirty-tree check +
  installed-package identity + `.so` runtime-extension hashes
  (`:342-404`), GPU count/model via `nvidia-smi` (`:445-463`), and — for the
  HTTP-serving leg — that the recorded PID is really the server holding the
  HTTP listener via `/proc` socket-inode cross-checks (`:494-558, 573-683`).
- Captures offline (`LLM.generate`, greedy, `temperature=0, seed=0,
  min_tokens==max_tokens, ignore_eos=True`) and both HTTP surfaces
  (`/v1/completions`, `/v1/chat/completions`) at concurrency 32, and requires
  the three surfaces' output token IDs to match exactly before assembling a
  final oracle (`:1058-1066`).
- Test matrix (`working-profile-512/acceptance-matrix.json`): a small
  9-case cross of prefill buckets {16,128,512} × output lengths {1,16,32}, at
  concurrency 32, bounded to ≤512 total tokens.

**What this campaign actually adjudicated, and what it did not:**
`working-profile-512/adjudication-manifest.json` records
`"status": "passed_interim_activation_and_offline_only"` with an explicit
`"claim_boundary"` field stating it verifies only "bounded offline activation
and serving behavior at total request length <=512. It does not verify
either OpenAI HTTP surface, pinned incumbent parity, or the full 2048-token
profile" — and `remaining_branch_a_hard_gates.pinned_incumbent_oracle_parity.status
== "unrun"` at that snapshot. The GPU-oracle comparison the tool was built for
was NOT complete at this checkpoint.

It was completed later: `final-product-scope-closure-20260820.json`'s
`semantic_diagnostic` block reports `gpu_oracle_sha256`, `cuda_requests: 384`,
`exact_comparison_requests: 320`, `exact_request_matches: 143` — i.e. **only
~45% token-exact match** — with the judgment downgraded to
`"semantic_smoke_pass_token_exact_parity_not_claimed"` and free text:
"Representative longer CUDA and Neuron responses retained the same meaning.
Token-exact parity is not claimed because FP8 backend execution and reduction
order are not bit-exact." The same closure record's `excluded_from_acceptance`
block states `"performance_targets": "not_claimed"` — **this campaign closed
without ever running a performance gate**, contrary to the "correctness gate +
performance gate" pattern the new plugin is meant to enforce universally.

### 3.5 Adjudication mechanics bug found in the GLM-5.2 evidence chain

`adjudicate_completed_run.py` (same working-profile-512 dir) and
`adjudication-manifest.json`'s `watcher_race_adjudication` block document a
real race condition in the evidence-collection pipeline: "The watcher stopped
waiting when exit-status.txt appeared. The runner writes exit-status.txt
before the post-run source, HLO, NEFF, and cache-count files. The watcher
therefore evaluated file existence during that creation window" — causing a
false `failed`/incomplete read of otherwise-good evidence, which had to be
superseded by re-deriving the same numbers from the completed files
(`adjudication-manifest.json:71-84`). This is a second, distinct adjudication
failure mode from the wrong-baseline one: not a wrong comparator, but a
**race between evidence production and evidence collection** that an
automated gate must be robust to (fixed-point re-read / retry-until-stable,
not "first file that appears wins").

### 3.6 deepseek-v4-flash-0731 campaign: correctness/perf gates never reached

`model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/artifacts/campaign.json`
shows `"status": "host_migrated_pending_canonical_refreeze_and_fresh_p1"` and
`"current_node": "initialize_campaign"`; `acceptance-contract.json` is still
recording environment/checkpoint pins, not evidence. No validation or
benchmark artifact exists anywhere in this campaign's `artifacts/` tree (list
of ~60 files inspected: all are plan-critique, host-migration, entitlement,
and authorization/attempt-repair records — see §6). This confirms the
requirement note "deepseek campaign paused at attempt 12" refers to a
compile/implementation stall, never a validation-gate outcome — there is no
correctness or benefit gate evidence to reconstruct here at all.

### 3.7 Table: validation methods observed in practice

| Method | Tool / mechanism | Artifact produced | Runtime cost hint (if evident) |
|---|---|---|---|
| Greedy-equality + acceptance-floor (fallback correctness) | Manual serve + fixed prompt battery + `/metrics` counter diffs | `validation/battery-*.json`, `validation/correctness.md`, host metrics snapshots | 3 serialized serve+battery cycles; compile cache grows by ~fresh-graph count per novel config (`correctness.md:27-28`: 14→32 dirs) |
| Sequential-vs-parallel control experiment | Re-run same battery through unmodified sibling code path | Comparison table in `correctness.md` | Adds one more serve+battery cycle |
| Equivalence-skill logit/KV validation | `neuron-framework-equivalence` skill, `--target-stack vllm_neuron` | Not observed executed in this corpus — blocked every time by the 0.24-vs-0.21 adapter version pin (`correctness.md:30-46`; `delegation.md:20`) | Blocked before any cost incurred |
| Level-1 task validation | `lm_eval --model vllm ...` | lm_eval score report | Not observed run in any campaign in this corpus (doc-only, `accuracy-debugging-guide.md:277-282`) |
| Level-2 logit validation (HF vs Neuron) | `run_logit_validation_online.py` / `_offline.py`, `vllm_neuron/accuracy/logit_validation.py` | Pass/fail bool + top-k error map, colorized report | CPU golden generation (FP32+BF16 autoregressive per prompt) + one vLLM server launch; not observed run in any campaign in this corpus |
| Level-2 KV-cache BC | `run_kv_cache_analysis.py`, `kv_cache_analysis.py` (`_compute_bc`) | Per-layer/head BC report | Not observed run in any campaign in this corpus |
| Manual streaming e2e-latency harness (perf, spec-decode) | Ad hoc `bench.py` over `/v1/completions stream:true` | `benchmark/bench-*.json` (TTFT/ITL/e2e per request) | 72 requests/leg (24×3), ~1–5 min per leg depending on config |
| `vllm bench serve` (documented default, per skill) | Upstream CLI | `result.json` (per `skills/experimental-neuron-framework-benchmark-vllm/references/metrics.md:1-38`) | Not observed literally invoked in any campaign artifact in this corpus — P-EAGLE explicitly opted OUT of it (`benefit.md:33-41`) because concurrency=1 spec-decode signal isn't its native use case |
| `NeuronDecodeBenchConnector` (decode-only isolation) | KV-connector plugin that fakes "prefill already happened" | Server-side throughput at fixed decode NEFF | Not observed invoked in any campaign in this corpus; code exists and is documented (`neuron_decode_bench_connector.py:1-56`) but correctness is explicitly NOT checked when used (`:198-219`, `_fill_blocks` no-op leaves KV as garbage) |
| GPU-oracle forensic capture (model-port, ad hoc) | `capture_gpu_oracle.py` (4-phase, hash-pinned) | `*-gpu-oracle*.json` bundle; `semantic_diagnostic` block in closure record | Loads a 756GB checkpoint once across 4 phases specifically to avoid repeat cost; ~45% exact-match outcome, downgraded to semantic-only |
| Contribution-checklist gate | `skills/vllm-neuron-feature-port/references/contribution-checklist.md` | Pass/fail per 10 items; `evidence_stale` vs `defects_found` vs `contribution_ready` | Re-verification of paths/dangling refs, not hardware |

## 4. Contradictions and stale documentation

- **The goal brief says "GPU-vLLM baseline serves lm_eval parity, greedy
  token-match, and the perf gate"** (requirements.md:97, goal-brief.md:150) as
  an already-verified mechanic. In this corpus, **no campaign was found that
  actually ran a GPU-vLLM baseline for lm_eval, greedy token-match, or a perf
  gate.** P-EAGLE's correctness and benefit gates were entirely Neuron-vs-
  Neuron (sequential-EAGLE3 control, no-spec baseline) — no GPU box was used.
  GLM-5.2's `capture_gpu_oracle.py` is the one real GPU-comparison tool found,
  and even it only compares token IDs/text, not logits, and its own campaign
  closed without a performance gate. **Inference:** the "GPU-vLLM baseline
  serves the perf gate" mechanic is closer to a design intention validated by
  one ad hoc tool-build than an established, repeatable procedure — the new
  plugin's shared back-end would be automating something that has been done
  once, forensically, by hand, not something with a stable existing recipe.
- **The equivalence-skill delegate is stated as the correctness-validation
  path** (`delegation.md:20`) but was blocked by a version mismatch (0.24
  adapter vs 0.21 venv) in the one campaign that tried to use it
  (`correctness.md:30-46`). This is flagged as an explicit open question in
  the requirements (OQ 4) and is confirmed here as a live, not hypothetical,
  blocker — every future correctness gate that wants the equivalence skill's
  logit/KL/cosine machinery will hit the same wall until the adapter is
  updated or a 0.21-compatible path is built.
- **`accuracy-debugging-guide.md`'s Level 1/2/3 framework reads as an
  established, routinely-used workflow** ("Start at Level 1... then Level
  2... then Level 3"), but no campaign artifact in either
  `feature_port_campaigns/` or `model_port_campaigns/` shows these specific
  tools (`lm_eval`, `logit_validation.py`, `kv_cache_analysis.py`) actually
  invoked. Every real correctness gate found in practice used ad hoc
  greedy-equality/output-diff harnesses instead. This does not mean the
  Level 1-3 framework is broken — only that its *actual use in a live
  campaign* is not evidenced in this corpus; the doc may be aspirational or
  used for one-off debugging outside the campaign directories this
  exploration was scoped to.
- The requirements brief's "perf via upstream `vllm bench serve`" claim
  (requirements.md:98) is technically true as a documented tool, but the one
  campaign that discusses it explicitly (P-EAGLE) chose NOT to use it for the
  exact regime the new plugin cares about (single-request spec-decode
  latency), for a stated reason (chunk-counting undercounts spec configs).
  Any shared back-end that defaults unconditionally to `vllm bench serve`
  would reproduce a measurement bug already caught and worked around once.

## 5. Graph implications (observations only — not a proposal)

- A shared validation back-end needs an explicit, pre-registered "what counts
  as the comparator set" step that is authored/approved by someone other than
  whoever runs the measurement — the wrong-baseline incident happened because
  the measuring agent picked the comparators across several verdict
  re-entries without that separation being enforced mechanically.
- Evidence-collection pipelines that read completion signals from multiple
  files/processes need to tolerate races between "job process exited" and
  "job's output artifacts fully materialized" (§3.5) — a plausible node-level
  contract is "poll until N consecutive stable reads," not "first sighting."
- Given the equivalence-skill adapter is blocked on the pinned 0.21 venv, any
  graph node that names "equivalence skill" as the correctness method for a
  target needs a pre-flight version-compatibility check with a defined
  fallback (greedy-equality + acceptance-floor, per the P-EAGLE precedent) —
  this cannot be an unconditional dispatch.
- The GPU-vLLM-baseline correctness/perf claims in the requirements describe
  a capability that exists as a hand-built, single-use tool
  (`capture_gpu_oracle.py`) rather than a reusable component; if the new
  plugin's shared back-end is meant to reuse this mechanic across campaigns,
  that tool (or something like it) needs to become a first-class, versioned
  part of the back-end rather than a per-campaign artifact reinvented each
  time.
- "No performance gate was run" (GLM-5.2) and "no gate reached at all"
  (deepseek) are both real historical outcomes that a "declared at kickoff,
  cannot be skipped" gate policy must be designed to prevent — they are not
  hypothetical failure modes.

## 6. Remaining evidence gaps ("not found / could not verify")

- No campaign artifact in this corpus shows `lm_eval` (Level 1) actually
  invoked against a served vLLM-Neuron endpoint with a real threshold
  comparison — only the doc example.
- No campaign artifact shows `logit_validation.py` / `kv_cache_analysis.py`
  (Level 2) actually invoked in production use inside a campaign directory —
  only the example scripts and unit-level default thresholds in the source.
- No campaign artifact shows `vllm bench serve` literally invoked and its
  `result.json` consumed for a Neuron-side perf verdict; P-EAGLE explicitly
  bypassed it. Could not confirm whether any *other*, unindexed campaign
  (outside `feature_port_campaigns/` and `model_port_campaigns/`) does use it
  — out of scope to search further given the read-only/scope boundary.
- No campaign artifact shows `NeuronDecodeBenchConnector` actually invoked in
  a benchmark run — only its source and doc comment. Could not verify its
  claimed runtime-cost profile (e.g., how much faster decode-only isolation
  is vs a full prefill+decode `vllm bench serve` run) from any real numbers.
- Could not verify whether the GLM-5.2 `pinned_incumbent_oracle_parity`
  gate was ever formally re-run to a `passed` (rather than the downgraded
  `semantic_smoke_pass_token_exact_parity_not_claimed`) status, or whether a
  performance gate for GLM-5.2 exists anywhere outside this corpus — the
  `final-product-scope-closure-20260820.json` explicitly excludes performance
  claims, and no later artifact superseding that closure was found in the
  directory listing.
- Level 3 (module-level) validation: no example script was located under
  `examples/vllm_neuron/accuracy/` matching "module" or "per-component" naming
  in this pass; `vllm_neuron/accuracy/testing.py` is the likely home but its
  contents were not read (scope/time boundary) — cannot confirm its CLI
  surface or output-artifact format.
- Could not determine the exact current adjudication threshold used to decide
  "match closely enough to call it semantic parity" in the GLM-5.2 closure
  (143/320 = 44.7% exact match was accepted as "semantic smoke pass") — this
  reads as a judgment call made in the moment, not a pre-registered numeric
  threshold; no threshold constant was found for this decision.
