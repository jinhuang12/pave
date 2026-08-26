# RELAY — extract-collision → builder-references (via lead, 2026-08-26)

Lead scratch, never ships. The extract-collision worker could not reach
builder-references by name and routed its final deliverable through the
lead. Verbatim below: the full file content for
references/collision-ranking.md plus the worker's verification log.
builder-references remains the single writer of the shipped file.

Worker's summary of open items it closed:
- Line counts confirmed 8278/922/1909/1025 at commit 0e19f00 (no
  drift); kv_connector 529 + 256.
- NIXL connectors VERIFIED isolated (class lines 48/60/496 and
  85/118/222); included as unranked contrast surface + rule 7.
- Test layout VERIFIED: test/unit/spec_decode/ only, exactly 7 files;
  zero coverage on all four ranked files (rule 8).
- Feature matrix deliberately NOT cited (stub redirect at 0.24 — would
  go stale); platform fan-in grounded on the verified method inventory.
- Cross-pin check: shallow clone of upstream release-0.24.0.1.1.0 gave
  9086/938/2225/1084 — same rank order; runner gains a 2nd mixin
  (NeuronECConnectorModelRunnerMixin, line 383). Clone deleted.
- Adjustment: runner is not literally one-class-per-file (two tiny
  module-level classes at 108/122 precede NeuronModelRunner) — text
  says "one ~7,900-line class body (378→EOF)"; dropped the
  exploration's unverified inference about execute_model's internal
  branching.

=== FILE CONTENT for references/collision-ranking.md ===

# Collision-surface ranking — runner, platform, worker, scheduler

This ranking tells you which files concurrent porting increments collide on most in
`vllm-neuron`. Use it for two decisions: (1) whether two increments can run in
parallel or must be serialized, and (2) which file surface a planned change will
touch. Rank is driven by collision probability, not file size — the platform file
is the smallest of the four but ranks second.

Line counts below were measured with `wc -l` at the repo's current pin
(`vllm==0.21.0`, `requirements/core.txt:6`). The ranking order is stable across the
0.21→0.24 pin: on upstream branch `release-0.24.0.1.1.0` the same four files
measure 9086 / 938 / 2225 / 1084 lines — all grew, none moved rank. Method line
numbers shift across pins; always re-derive them with grep (see Operational rules).

## Ranked collision surfaces

| Rank | Path | Lines | Collision driver | Typical edit shape | Mitigation for concurrent increments |
|---|---|---|---|---|---|
| 1 | `vllm_neuron/vllm/worker/neuron_model_runner.py` | 8278 | One class, `NeuronModelRunner` (line 378 to EOF, ~7,900-line body), holds every runtime subsystem: model loading, warmup/compile, core execution, spec decode, KV cache, encoder cache, capture/profiling. Any two increments that change runtime behavior meet here. | Cross-cutting: edits land inside shared method bodies (`execute_model` spans lines 4956–6984, ~2,000 lines) and in the class base list (connector features add mixins). | Single writer by default. Parallel work only when both increments map to disjoint, named method ranges agreed in advance. |
| 2 | `vllm_neuron/vllm/platform.py` | 922 | Wide fan-in: `NeuronPlatform` (line 118 to EOF) is the funnel for config mutation (`check_and_update_config`, 278), request validation (396), quantization gating (438), DCP/DP validation (501–648), attention-backend selection (663), NIXL device support (673), plus three runtime monkeypatch blocks (53–115, 783–904). Most feature classes must add or change a validator or config default here. | Mostly additive: a new classmethod validator plus one call line in a shared method (`check_and_update_config` or `validate_request`). The shared call sites are the merge points. | Parallel-safe when each increment only adds a new method; coordinate the one-line edits to the shared callers explicitly. |
| 3 | `vllm_neuron/vllm/worker/neuron_worker.py` | 1909 | One class, `NeuronWorker(WorkerBase)` (line 188), implements the full vLLM worker interface, plus module-level distributed-bootstrap helpers (`validate_cross_node_master_addr` 58, `resolve_ep_degree` 88, `rendezvous_ccom_bootstrap` 126). Increments that change device init, parallelism degree, or worker lifecycle touch it. Grew +316 lines across the 0.21→0.24 pin — the second-fastest-growing surface. | Additive module-level helpers plus edits inside the single class. | Serialize increments that both touch worker init or bootstrap; helpers added side by side rarely conflict. |
| 4 | `vllm_neuron/vllm/core/scheduler.py` | 1025 | Sole hook point for scheduling-class features: the only module in `vllm_neuron/vllm/core/` besides `__init__.py`. Batching, segmented/chunked prefill, and async-scheduling work must edit `NeuronScheduler(Scheduler)` (line 78) or `NeuronAsyncScheduler` (line 906), which subclass upstream `vllm.v1.core.sched.*`. | Localized overrides inside a compact two-class hierarchy. | Serialize only when two increments are both scheduling-class; otherwise low collision risk. |

Contrast surface (not ranked): `vllm_neuron/vllm/kv_connector/` —
`neuron_nixl_connector.py` (529 lines, 3 classes) and
`neuron_decode_bench_connector.py` (256 lines, 3 classes) cleanly subclass upstream
connector ABCs with no entanglement in the runner monolith. When a KV/disaggregation
change can live in a connector subclass instead of the runner, put it there — it
converts a rank-1 collision into a low-risk isolated edit.

## Ranking rationale

### 1. `neuron_model_runner.py` — the monolith
- One class from line 378 to end of file. The class body alone is ~7,900 lines.
- Subsystems inside the one class (method → line at the current pin): model loading
  (`load_model` 1151, `init_tensor_replacement` 1468, `get_model` 1507);
  warmup/compile (`extract_prefill_graphs` 4340, `warmup_prefill` 4410,
  `extract_decode_graphs` 4673, `warmup_decode` 4762, `parallel_compile` 4886);
  core execution (`execute_model` 4956, `sample_tokens` 5355,
  `execute_dummy_batch` 6984); spec decode (`take_draft_token_ids` 7460);
  KV cache (`initialize_kv_cache` 7651 through `clear_kv_snapshot` 8090);
  encoder cache (`get_encoder_cache` 8136 through `clear_encoder_cache_snapshot`
  8185); capture/profiling (`enable_capture` 8247, `profile_run` 8260,
  `capture_model` 8264); shutdown (`ensure_kv_transfer_shutdown` 8277).
- Because all of these share one class body and one shared state, assume
  near-certain overlap between any two increments that both change runtime
  execution — unless each is scoped to one of the disjoint method clusters above
  (for example, encoder-cache-only vs spec-decode-only).
- The class base list is itself a collision point: at 0.21 the class is
  `NeuronModelRunner(KVConnectorModelRunnerMixin)`; at upstream 0.24 it gains a
  second mixin (`NeuronECConnectorModelRunnerMixin`). Two connector-class
  increments will both edit the same class statement.
- No test coverage exists for this file in the repo.

### 2. `platform.py` — wide fan-in
- Smallest of the four files, but almost every feature class passes through it:
  a feature that adds a CLI/config knob edits `pre_register_and_update` (152) or
  `apply_config_platform_defaults` (173); a quantization feature edits
  `_validate_quantization_config` (438); a DCP/DP feature edits the validators at
  501–648 (including `_validate_dcp_requires_neuron_nixl_connector`, 623); an
  attention feature edits `get_attn_backend_cls` (663).
- `check_and_update_config` (278) and `validate_request` (396) are the shared
  call sites that chain these validators — most increments add one call line
  there, which is where textual merge conflicts concentrate.
- The file also hosts monkeypatches of vLLM internals (DCP config-validation
  patch at 53–115; termination-timeout patches at 783–904). An increment that
  touches these must preserve their applied-at-registration semantics, which
  couples it to `vllm_neuron/__init__.py`'s `register()` path.
- No test coverage exists for this file in the repo.

### 3. `neuron_worker.py` — worker lifecycle and distributed bootstrap
- One class implementing vLLM's `WorkerBase` (import at line 29), plus
  module-level helpers for cross-node address validation, EP-degree resolution,
  and CCOM rendezvous bootstrap.
- Fewer feature classes funnel through it than through the runner or platform,
  which is why it ranks below the smaller platform file. But multi-node,
  expert-parallel, and lifecycle changes must edit it, and it grew +316 lines
  across the 0.21→0.24 pin (new bootstrap helpers) — active surface, not a
  frozen one.
- No test coverage exists for this file in the repo.

### 4. `scheduler.py` — sole hook, localized edits
- The only scheduler module in the plugin. Any batching, prefill-segmentation,
  or async-scheduling feature has exactly one place to go: `NeuronScheduler`
  (78–905) or `NeuronAsyncScheduler` (906–end), subclasses of upstream
  `vllm.v1.core.sched.scheduler.Scheduler` and `async_scheduler.AsyncScheduler`.
- It ranks last because the hierarchy is compact and edits localize to
  overridden methods; it collides only when two increments are both
  scheduling-class. Growth across the pin was the smallest of the four
  (+59 lines).
- No test coverage exists for this file in the repo.

## Operational rules

1. **Serialize on the runner.** Do not run two increments that both edit
   `neuron_model_runner.py` at the same time, unless each is scoped to a
   disjoint method cluster from the rationale above and the scopes are declared
   before work starts. Treat `execute_model` (4956–6984) as single-writer
   always — it is too large and too shared to partition.
2. **Declare file+range scope up front.** Before an increment starts, list the
   files and the named methods it will edit. Two in-flight increments whose
   declared scopes intersect on a rank-1 or rank-2 file are a scheduling
   conflict — resolve by ordering, not by merging later.
3. **Read only the named slices.** Never read the runner (or worker) end to end.
   Derive the method index first, then read only the ranges you will edit:
   ```
   grep -n "^class \|    def " vllm_neuron/vllm/worker/neuron_model_runner.py
   ```
   Then read the specific line ranges. The same procedure applies to
   `platform.py`, `neuron_worker.py`, and `scheduler.py`.
4. **Re-derive line numbers per pin.** All line numbers in this document are
   valid at the `vllm==0.21.0` pin. They shift on every version bump (every
   ranked file grew at 0.24). Trust the grep, not this table, for exact lines;
   trust this table for rank and rationale — the rank order held across
   0.21→0.24.
5. **Coordinate the platform merge points.** Increments may add new validator
   methods to `platform.py` in parallel, but the one-line registration edits in
   `check_and_update_config` (278) and `validate_request` (396) must be applied
   by one hand, or sequenced.
6. **Detect collisions early.** Before merging an increment, diff its touched
   ranges against every other in-flight increment's declared scope
   (`git diff --unified=0 -- <file>` and compare hunk ranges). An undeclared
   hunk in a rank-1/rank-2 file is a stop-and-review signal.
7. **Prefer connector isolation.** For KV-transfer and disaggregation work,
   implement in `vllm_neuron/vllm/kv_connector/` subclasses where possible
   instead of the runner body. Platform-side gating for these features lives at
   `_validate_dcp_requires_neuron_nixl_connector` (`platform.py:623`).
8. **Add tests with every increment.** The repo's only tests are
   `test/unit/spec_decode/` (7 files, all spec-decode). None of the four ranked
   files has a regression net — each increment that edits them must bring its
   own tests, because concurrent-edit breakage will not be caught otherwise.

=== END FILE CONTENT ===

=== VERIFICATION LOG (worker's, verbatim) ===
Verified at /Users/jinhun/GitHub/vllm-neuron, commit 0e19f00, branch feature/p-eagle-gpt-oss-20b, pin vllm==0.21.0 (requirements/core.txt:6).

1. Line counts 8278/922/1909/1025 — VERIFIED (wc -l), no drift. kv_connector: 529 + 256.
2. All four paths exist; none renamed/moved/absent — VERIFIED. vllm_neuron/vllm/core/ = __init__.py + scheduler.py only.
3. Runner class 378→EOF — VERIFIED; ADJUSTED "one class" → "one ~7,900-line class body" (ExecuteModelState 108, AsyncNeuronModelRunnerOutput 122 precede it).
4. All 18 runner method lines cited (load_model 1151 … ensure_kv_transfer_shutdown 8277) — VERIFIED exact via grep.
5. execute_model span 4956–6984 — VERIFIED (bounded by execute_dummy_batch 6984). DROPPED unverified inference about inlined per-feature branching.
6. Platform inventory (278/396/438/501/543/579/623/663/673/758/783/828/867, DCP patch 53–115, class 118, pre_register 152, defaults 173) — VERIFIED exact. Fork already has _apply_dcp_patch at 93 (delta tagged 0.24-new; not cited).
7. NeuronWorker(WorkerBase) 188; helpers 58/88/126; WorkerBase import line 29 — VERIFIED. _SuppressModelRegistryOverwrite 170 already in fork.
8. Scheduler: 42/78/906; upstream subclassing was INFERENCE in exploration — UPGRADED TO CONFIRMED (imports scheduler.py:17-18).
9. NIXL isolation — VERIFIED (48/60/496; 85/118/222).
10. Tests: only test/unit/spec_decode/, exactly 7 files — VERIFIED.
11. Cross-pin: upstream release-0.24.0.1.1.0 clone → 9086/938/2225/1084, same rank order; runner 2nd mixin at line 383 — VERIFIED; clone deleted.
12. Feature-matrix doc NOT cited (stub redirect at 0.24 — would go stale).
13. Zero .pave references / exploration citations in file text.
=== END VERIFICATION LOG ===
