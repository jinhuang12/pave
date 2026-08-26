# Target-plugin lens: vLLM-Neuron integration surfaces, validation assets, campaign-touch file surfaces

## 1. Question investigated

What are the vLLM-Neuron plugin's integration surfaces with pinned vLLM 0.21,
its validation assets, and the concrete file surfaces port campaigns would
touch — needed for conflict-aware scheduling and route analysis in the
vllm-neuron-parity pave-init run.

Scope: read-only exploration of `/Users/jinhun/GitHub/vllm-neuron` (no SSH, no
web, no edits). Report path is the only file written.

## 2. Evidence inventory

Primary artifacts read (all under `/Users/jinhun/GitHub/vllm-neuron`):

- `pyproject.toml` (full)
- `requirements/core.txt`, `requirements/test.txt`, `requirements/vllm_build_requirements.txt`, `requirements/vllm_rc_test.txt` (full)
- `vllm_neuron/__init__.py` (full)
- `vllm_neuron/backend.py` (full)
- `vllm_neuron/vllm/platform.py` (full file read for lines 1-120 and 783-882; structure grepped for full 922 lines)
- `vllm_neuron/vllm/worker/neuron_model_runner.py` (structure grepped, 8278 lines — not fully read)
- `vllm_neuron/vllm/worker/neuron_worker.py` (structure grepped, 1909 lines)
- `vllm_neuron/vllm/core/scheduler.py` (structure grepped, 1025 lines)
- `vllm_neuron/vllm/kv_connector/neuron_nixl_connector.py`, `neuron_decode_bench_connector.py` (structure + headers)
- `vllm_neuron/vllm/patches/__init__.py`, `vllm_neuron/vllm/patches/port_hold_patch.py` (full)
- `vllm_neuron/envs.py` (grepped for env-var table and compile-cache resolver, lines ~25, ~120-260, 341-391)
- `vllm_neuron/accuracy/` directory listing (11 files, not individually read except via docs)
- `docs/guides/reference-feature-model-compatibility.md` (full)
- `docs/model-dev/accuracy-debugging-guide.md` (lines 1-120)
- `docs/model-dev/cpu-development.md` (lines 1-90)
- `docs/getting-started/setup-guide.md`, `docs/tutorials/tutorial-di-1p1d-xpyd.md`, `docs/guides/reference-configuration.md` (grepped for `/opt` paths)
- `test/` directory tree (full listing — 7 files total)
- Directory listings: `vllm_neuron/model/`, `vllm_neuron/nki/`, `vllm_neuron/compile/`, `vllm_neuron/parallel/`, `vllm_neuron/functional/`, `vllm_neuron/vllm/` subtree, `vllm_neuron/overrides/`

Not read in full (size-bounded triage only): `neuron_model_runner.py` (8278
lines — read class/def signatures only), `neuron_worker.py`, `scheduler.py`.
Not opened at all: `vllm_neuron/model/gpt_oss/*`, `vllm_neuron/model/llama3/*`,
`vllm_neuron/accuracy/logit_validation.py`, `kv_cache_analysis.py` (sizes only,
via `wc -l`). This is an INFERENCE-LIMITING gap — see section 6.

## 3. Findings with citations

### 3.1 Entry point into vLLM 0.21

- OBSERVED FACT: the plugin registers with vLLM's platform-plugin discovery
  via a setuptools entry point: `pyproject.toml:24-25`
  ```
  [project.entry-points."vllm.platform_plugins"]
  neuron = "vllm_neuron:register"
  ```
- OBSERVED FACT: `vllm_neuron.register()` (`vllm_neuron/__init__.py`, function
  `register`, ~line 211) checks for Neuron devices or CPU-mode env vars, then
  calls `vllm_neuron.backend.get_platform_class()` and
  `vllm_neuron.vllm.platform._patch_dcp_config_validation()`, returning the
  platform class path string.
- OBSERVED FACT: `get_platform_class()` (`vllm_neuron/backend.py`, lines
  ~85-93) hardcodes the return value `"vllm_neuron.vllm.platform.NeuronPlatform"`
  regardless of the `VLLM_NEURON_BACKEND` env var result — the backend
  selection logic (`NeuronBackend.VLLM_NEURON` vs `NEURON_NATIVE`) is computed
  by `get_backend()` but never consulted by `get_platform_class()`. This looks
  like dead branching (INFERENCE: the neuron_native path may be
  vestigial/unimplemented — not confirmed by further reading).
- OBSERVED FACT: pin is `vllm==0.21.0`, unconditional (`requirements/core.txt`,
  line 6: `vllm==0.21.0`). No version range — exact pin, matching the run's
  Baseline fact in requirements.md.

### 3.2 NeuronPlatform (`vllm_neuron/vllm/platform.py`, 922 lines)

- OBSERVED FACT: `NeuronPlatform(Platform)` class body spans
  `vllm_neuron/vllm/platform.py:118` to EOF (922). It subclasses
  `vllm.platforms.Platform` (imported at line 19).
- OBSERVED FACT: method inventory (grep of `def `) shows this class owns, in
  one file: device naming/counting (`get_device_name`, `device_count`,
  `set_device`, `device_id_to_physical_device_id`), CLI arg registration
  (`pre_register_and_update`), config mutation
  (`check_and_update_config` at line 278, `apply_config_platform_defaults` at
  173, `update_block_size_for_backend` at 165), request validation
  (`validate_request` at 396, `_validate_quantization_config` at 438), DCP
  (decode-context-parallel) validation (`_validate_component_dp_config`,
  `_validate_prefill_dcp_config`, `_validate_decode_dcp_config`,
  `_validate_dcp_requires_neuron_nixl_connector`, lines 501-648), attention
  backend selection (`get_attn_backend_cls` at 663), NIXL device support
  (`get_nixl_supported_devices` at 673), all2all backend registration
  (`_register_neuron_all2all_backend` at 758), and process-termination
  monkeypatches (`_patch_termination_timeouts`/`_patch_shutdown`/
  `_patch_ensure_worker_termination`, lines 783-904).
- This is the platform-level integration surface: any feature port that
  touches device/config/attention-backend selection, quantization gating, or
  DP/EP/DCP validation touches this one file.

### 3.3 Monkeypatches of vLLM internals — three distinct mechanisms found

1. **DCP config validation patch** — `vllm_neuron/vllm/platform.py:53-115`.
   `_patch_dcp_config_validation()` patches
   `vllm.config.model.ModelConfig.verify_with_parallel_config` to bypass an
   upstream assertion (`TP > num_kv_heads` when DCP enabled) for the
   prefill-DCP case. Applied either eagerly at plugin registration, or lazily
   via `sys.addaudithook` if a circular import blocks the eager path (lines
   71-90) — OBSERVED FACT, self-disabling hook, single boolean check overhead
   documented in the docstring.
2. **Process-termination timeout patches** —
   `vllm_neuron/vllm/platform.py:783-882` (`_patch_termination_timeouts`,
   `_patch_shutdown`, `_patch_ensure_worker_termination`). Rewrites
   `vllm.v1.utils.shutdown`, `vllm.v1.engine.utils.shutdown`, and
   `vllm.v1.executor.multiproc_executor.MultiprocExecutor._ensure_worker_termination`
   at runtime, module-level name rebinding in two modules to satisfy a
   `weakref.finalize` capture (documented rationale: Neuron profiling
   (`NEURON_RT_INSPECT_ENABLE=1`) needs longer than the hardcoded 4-5s
   SIGTERM→SIGKILL windows). Config knob:
   `VLLM_NEURON_WORKER_TERMINATION_TIMEOUT` (`envs.py:207`).
3. **Port-theft race patch** — `vllm_neuron/vllm/patches/port_hold_patch.py`
   (full file, ~150+ lines seen). Monkeypatches `init_process_group` for
   loopback TCP init only, to eliminate a TOCTOU port-theft race against
   sibling NRT processes. Applied unconditionally at import time via
   `apply_port_hold_patch()`, called from `vllm_neuron/__init__.py` near EOF
   (comment: "Import-time so it survives spawn-mode re-imports").

- CONTRADICTION/STALE DOCUMENTATION (see section 4): a fourth, apparently
  intended, centralized mechanism — `vllm_neuron/vllm/patches/__init__.py`
  — is NOT actually used for any of the three patches above.

### 3.4 NIXL connector subclasses (`vllm_neuron/vllm/kv_connector/`)

- OBSERVED FACT (`neuron_nixl_connector.py:48-496`): three classes —
  `NeuronNixlAgentMetadata(NixlAgentMetadata)` (line 48),
  `NeuronNixlConnectorWorker(NixlConnectorWorker)` (line 60),
  `NeuronNixlConnector(NixlConnector)` (line 496) — all subclassing
  upstream vLLM's `vllm.distributed.kv_transfer.kv_connector.v1.nixl.*`
  classes directly (imports at lines 26-31). Docstring (lines 2-17) states
  this class "Supports all DCP DI topologies via unified head_ratio/seq_ratio
  math" for 4 DCP/TP prefill↔decode combinations, and explicitly says it
  "Replaces the monkey-patch approach" — i.e., an earlier NIXL integration
  was itself a monkeypatch, now superseded by clean subclassing (OBSERVED
  FACT from docstring; the prior monkeypatch code itself was not located —
  INFERENCE that it was removed, not verified by git history in this lens).
- OBSERVED FACT (`neuron_decode_bench_connector.py:85-222`): three more
  classes — `NeuronDecodeBenchConnectorScheduler`,
  `NeuronDecodeBenchConnectorWorker`, `NeuronDecodeBenchConnector` —
  subclass upstream `DecodeBenchConnector*` plus `SupportsHMA` mixin. This is
  the `NeuronDecodeBenchConnector` cited in requirements.md's perf-gate
  method (decode-only isolation benchmarking).

### 3.5 NeuronModelRunner monolith (`vllm_neuron/vllm/worker/neuron_model_runner.py`, 8278 lines)

- OBSERVED FACT: this single file is the largest in the plugin
  (`wc -l` = 8278; next-largest non-model file is `neuron_worker.py` at 1909).
  One class, `NeuronModelRunner(KVConnectorModelRunnerMixin)`, spans line 378
  to EOF — i.e., the class body alone is ~7900 lines.
- OBSERVED FACT: subsystems living inside this one class (grep of top-level
  `def` inside the class, non-underscore-prefixed methods only — private
  helpers are far more numerous and not enumerated here):
  - Model loading: `load_model` (1151), `init_tensor_replacement` (1468),
    `get_model` (1507)
  - Graph/compile warmup: `extract_prefill_graphs` (4340),
    `warmup_prefill` (4410), `extract_decode_graphs` (4673),
    `warmup_decode` (4762), `parallel_compile` (4886)
  - Core execution: `execute_model` (4956) running to ~6984 (~2000 lines),
    `sample_tokens` (5355), `execute_dummy_batch` (6984)
  - Speculative decoding: `take_draft_token_ids` (7460)
  - KV cache management: `initialize_kv_cache` (7651),
    `get_kv_cache_view_for_connector_registration` (7826),
    `get_kv_cache_spec` (7841), `get_kv_caches` (7924),
    `get_block_table_info` (7955), `get_kv_cache_config` (7989),
    `get_block_tables` (8037), `clear_kv_snapshot` (8090)
  - Encoder cache (multimodal): `get_encoder_cache` (8136),
    `enable_encoder_cache_snapshot` (8173),
    `clear_encoder_cache_snapshot` (8185)
  - Capture/profiling: `enable_capture`/`disable_capture` (8247/8252),
    `profile_run` (8260), `capture_model` (8264)
  - Shutdown: `ensure_kv_transfer_shutdown` (8277)
  - INFERENCE: execute_model spanning ~2000 lines (4956→6984) strongly
    suggests inlined per-feature branching (spec decode, quantization,
    multimodal, DCP) rather than delegated helper modules — not verified by
    reading the method body itself (out of the size-bounded triage budget for
    this lens).

### 3.6 NeuronWorker (`vllm_neuron/vllm/worker/neuron_worker.py`, 1909 lines)

- OBSERVED FACT: one class `NeuronWorker(WorkerBase)` at line 188, plus
  module-level helpers `validate_cross_node_master_addr` (58),
  `resolve_ep_degree` (88), `rendezvous_ccom_bootstrap` (126). Structure
  grepped only (public-method breakdown not enumerated — time-boxed).

### 3.7 NeuronScheduler (`vllm_neuron/vllm/core/scheduler.py`, 1025 lines)

- OBSERVED FACT: `SchedulerState(Enum)` (42), `NeuronScheduler(Scheduler)`
  (78, runs to ~905), `NeuronAsyncScheduler(NeuronScheduler, AsyncScheduler)`
  (906). Two-class hierarchy subclassing upstream `vllm.v1.core.sched.*`
  types (import not individually confirmed in this pass — INFERENCE from
  class names and directory `vllm_neuron/vllm/core/`).
- This is the sole scheduler file in `vllm_neuron/vllm/core/` (only file
  besides `__init__.py`) — chunked-prefill / continuous-batching /
  segmented-prefill logic most likely concentrates here (INFERENCE — method
  bodies not read).

### 3.8 Accuracy framework (3-level, `vllm_neuron/accuracy/`)

- OBSERVED FACT: directory contains 15 files including
  `logit_validation.py` (2310 lines — second-largest file in the whole
  package), `kv_cache_analysis.py` (920 lines), `tensor_compare.py` (1227
  lines), `tensor_histogram.py` (686 lines), plus
  `encoder_cache_analysis.py`, `kv_cache_visualize.py`,
  `logit_visualization.py`, `plotting.py`, `tensor_alignment_utils.py`,
  `tensor_capture.py`, `tensor_io.py`, `tensor_replacement.py`, `testing.py`,
  `types.py`, `utils.py`, `constants.py`.
- OBSERVED FACT (`docs/model-dev/accuracy-debugging-guide.md:30-72`),
  confirms requirements.md's OF claim of a 3-level framework:
  - Level 1 (task-level): lm_eval/longbench aggregate score thresholds.
  - Level 2 (prompt-level): "Logit validation — Three-way comparison (HF
    FP32 vs HF BF16 vs Neuron) using top-k error maps at k={5, 50, 1000, all}
    and divergence detection" and "KV cache analysis — Three-way comparison
    of KV caches with per-layer, per-head error metrics and Bhattacharyya
    Coefficient (BC) to classify errors as BF16-inherent vs anomalous."
    Pass/fail: "Logit divergence within tolerance maps; KV cache BC ≥ 0.99" —
    matches requirements.md's "KV-cache BC ≥ 0.99" verbatim.
  - Level 3 (module-level): per-module (attention, MLP, RMSNorm, embedding,
    RoPE, decoder layer) tests against HF reference, run in CPU mode and on
    hardware.
  - CORRECTION to requirements.md phrasing: the doc's Level-2 comparison is
    explicitly three-way **HF FP32 vs HF BF16 vs Neuron**, not "logit top-k
    vs HF reference" alone as summarized in the brief — the brief's
    phrasing is a compressed paraphrase, not wrong, but the file evidence
    adds the FP32-vs-BF16 HF split that the requirements doc doesn't state.

### 3.9 CPU mode (`VLLM_NEURON_CPU_MODE`)

- OBSERVED FACT: env var declared `envs.py:25` (`VLLM_NEURON_CPU_MODE: bool =
  False`) and resolver at `envs.py:120-123`. Sibling
  `VLLM_NEURON_CPU_COMPILE` at line 26/124-127 — mutually exclusive, enforced
  by a `RuntimeError` in `vllm_neuron/__init__.py`'s `_init_backend()`
  ("VLLM_NEURON_CPU_MODE and VLLM_NEURON_CPU_COMPILE are not compatible with
  each other").
- OBSERVED FACT (`vllm_neuron/__init__.py`, `_init_backend` body): CPU mode
  sets `PJRT_DEVICE=CPU`, conditionally imports `torch_neuronx` only if HW
  present, and (when `NKI_SIMULATOR=1` is also set) defaults
  `NKI_PRECISE_FP=1`. `_current_accelerator_wrapper` forces
  `torch.device("cpu")` when `VLLM_NEURON_CPU_MODE` is true.
- OBSERVED FACT (`docs/model-dev/cpu-development.md:51-58`): in CPU mode,
  "Tensor operations run on CPU", "Model compilation is simulated (no NEFF
  generation)", "NKI kernels fall back to PyTorch reference
  implementations" (only when NKI_SIMULATOR is also set — simulator is
  "not auto-activated by CPU mode", line 63-64).
- This directly grounds requirements.md's "CPU-first increments
  (VLLM_NEURON_CPU_MODE=1) before hardware attempts" as a real, working mode,
  not aspirational.

### 3.10 Feature-support matrix

- OBSERVED FACT (`docs/guides/reference-feature-model-compatibility.md`,
  full file, dated 2026-07-14): matrix covers only two models — GPT-OSS and
  Qwen3-VL. Unsupported today, per the matrix:
  - GPT-OSS: FP8 static weight quantization ❌, MXFP8 weight quant ❌,
    multimodal (image input) ❌.
  - Qwen3-VL: Speculative decoding (EAGLE3) ❌, FP8 static weight quant ❌,
    MXFP4 weight quant ❌, KV cache FP8 ❌, disaggregated inference ❌,
    expert parallelism N/A (architecture doesn't have it).
  - Supported by both: continuous batching, segmented prefill, prefix
    caching (APC), structured outputs/tool calling, on-device sampling,
    tensor/data parallelism.
  - Footnote: MXFP4 is Trn3-only; EAGLE3 support "Includes parallel drafting
    (P-EAGLE): tested with GPT-OSS-20B + amazon/GPT-OSS-20B-P-EAGLE".
- INFERENCE: this two-model matrix is narrower than the full model set the
  repo ships (`vllm_neuron/model/` also has `llama3` and `synthetic`
  subpackages not covered in the compatibility table) — the matrix may be
  stale relative to the model directory, or Llama3/synthetic are
  intentionally excluded as dev/reference models rather than supported
  recipes. Not resolved in this lens (see section 4/6).

### 3.11 Test layout

- OBSERVED FACT: `test/` contains exactly one populated subtree —
  `test/unit/spec_decode/` — with 7 files: `test_eagle3_two_pass_kv_prime.py`,
  `test_parallel_draft_inputs.py`, `test_eagle3_parallel_forward.py`,
  `test_eagle4_bucketing.py`, `test_eagle3_multilayer_backbone.py`,
  `test_eagle4_routing.py`, `test_eagle_parallel_drafting_config.py`. All 7
  are spec-decode/EAGLE-focused; no tests exist for scheduler, model runner,
  worker, platform, NIXL connectors, or accuracy modules in the public tree.
  This directly confirms requirements.md's OF "Public repo ships no test
  suite" (more precisely: ships a thin, spec-decode-only test suite, not
  zero tests).
- CONTRADICTION (see section 4): `pyproject.toml:47`
  (`testpaths = ["test/unit", "test/vllm_neuron"]`) references
  `test/vllm_neuron`, which does not exist in the repo
  (`ls test/vllm_neuron` → "No such file or directory").

### 3.12 Compile-cache paths

- OBSERVED FACT (`vllm_neuron/envs.py:341-391`,
  `get_neuron_compile_cache_dir()` / `_resolve_neuron_compile_cache_dir()`):
  - If `VLLM_CACHE_ROOT` is set: `$VLLM_CACHE_ROOT/neuron/compile_cache`
    (line 376) — matches requirements.md's forbidden-effect #2 path exactly.
  - If unset (default `~/.cache/vllm`): probes the filesystem; if that
    default resolves to a remote FS (NFS/Lustre), falls back to
    `/tmp/vllm_neuron_wdir_$USER/neuron/compile_cache` (line 382) and logs a
    warning (line 385-388); otherwise uses
    `~/.cache/vllm/neuron/compile_cache` (line 391) — matches
    requirements.md's `~/.cache/vllm/neuron/compile_cache` path.
  - requirements.md's third forbidden path, `/var/tmp/neuron-compile-cache`,
    was NOT found by this grep in `vllm_neuron/**/*.py` — either it's a
    legacy/alternate path referenced elsewhere (docs, scripts outside
    `vllm_neuron/`), or it's aspirational in the requirements doc. NOT
    RESOLVED in this lens (see section 6).
  - Consumers of `get_neuron_compile_cache_dir()`: `vllm_neuron/nki/nki_cache.py:249`,
    `vllm_neuron/vllm/worker/neuron_profiler.py:77`,
    `vllm_neuron/utils/executor.py:661`,
    `vllm_neuron/compile/parallel_compile.py:66`,
    `vllm_neuron/compile/backend.py:212`,
    `vllm_neuron/compile/capture_backend.py:50`. Any campaign touching
    compile/warmup or NKI caching intersects this shared cache resource —
    directly relevant to conflict-aware scheduling (concurrent campaigns
    must not race on the same cache dir unless it's per-venv/per-host
    scoped, which is not verified in this lens).

### 3.13 Venv / DLAMI `/opt` assumptions

- OBSERVED FACT (`docs/getting-started/setup-guide.md:61`):
  `source /opt/aws_neuronx_venv_pytorch_inference_vllm_0_21_0_1_0_0/bin/activate`
  — exact venv name matches the pin version `0.21.0.1.0.0` in
  `pyproject.toml:10`.
- OBSERVED FACT (`docs/tutorials/tutorial-di-1p1d-xpyd.md:91`): a second,
  differently-suffixed venv path,
  `/opt/aws_neuronx_venv_pytorch_inference_vllm_0_21/bin/activate` (no
  trailing `.1.0.0`) — a naming inconsistency between two docs (see section
  4).
- OBSERVED FACT (`docs/guides/reference-configuration.md:240`):
  `export NEURON_COMPILED_ARTIFACTS=/opt/neuron-cache/gpt-oss-20b` — a
  second `/opt`-rooted path family (compiled-artifact cache, distinct from
  the venv path and from `get_neuron_compile_cache_dir()`'s
  `$VLLM_CACHE_ROOT`-based path). This grounds requirements.md's Assumption
  A1 (DLAMI baseline venv under `/opt/aws_neuronx_venv_pytorch_inference_vllm_*`)
  as directly evidenced, with the caveat that the exact suffix varies by doc.

### 3.14 Package layout / what's excluded from the wheel

- OBSERVED FACT (`pyproject.toml:40-45`): `[tool.setuptools.packages.find]`
  restricts the shipped wheel to `vllm_neuron` and `vllm_neuron.*` only —
  `test/`, `ci/`, `docs/`, `examples/`, `tools/` are excluded from
  distribution (comment explicitly calls out excluding a sibling
  `vllm_neuron_tests` package). Coverage config
  (`[tool.coverage.run] omit`, lines 55-64) separately excludes
  `vllm_neuron/patches/*` — NOTE this path (`vllm_neuron/patches/*`, no
  `vllm/` segment) does not match the actual patches location
  `vllm_neuron/vllm/patches/*` found in this lens — likely a stale/wrong
  coverage-omit glob (see section 4).

## 4. Contradictions and stale documentation

1. **`vllm_neuron/vllm/patches/__init__.py` is an unused stub.**
   Docstring claims "All monkey-patches to upstream vLLM are applied here via
   `apply_patches()`" (lines 3-7), and the module defines `apply_patches()`
   (line 15), but the function body is empty (file ends at line 16, right
   after the `def` line) and `apply_patches` is never imported or called
   anywhere else in `vllm_neuron` (grep found only the definition and the
   docstring's self-reference). The three actual monkeypatch mechanisms
   found (DCP config patch, termination-timeout patches, port-hold patch)
   are each applied ad hoc from `vllm_neuron/__init__.py` or
   `vllm_neuron/vllm/platform.py` directly, not through this "centralized"
   module. TREAT the docstring as aspirational/stale documentation, not
   current behavior.
2. **Coverage-omit glob mismatch.** `pyproject.toml:62`
   (`"vllm_neuron/patches/*"` in `[tool.coverage.run] omit`) does not match
   the real patches path `vllm_neuron/vllm/patches/*`. Coverage is likely
   silently NOT omitting patch files as intended (or omitting nothing,
   depending on glob semantics) — a stale path, not verified against actual
   coverage output in this lens.
3. **`pyproject.toml:47` testpaths includes a non-existent directory.**
   `testpaths = ["test/unit", "test/vllm_neuron"]` — `test/vllm_neuron` does
   not exist (`ls` confirms). `pytest`'s behavior on a missing testpath
   entry was not verified (may warn or error at collection) — not resolved
   in this lens.
4. **Venv path naming inconsistency across docs.**
   `setup-guide.md:61` uses `..._vllm_0_21_0_1_0_0` (matches pyproject
   version exactly); `tutorial-di-1p1d-xpyd.md:91` uses `..._vllm_0_21`
   (shorter, version-truncated). Campaigns relying on A1 (standing-instance
   venv path) should verify the actual path on the target host rather than
   trust either doc literally.
5. **Feature-support matrix covers 2 of ≥4 model families in the tree.**
   `docs/guides/reference-feature-model-compatibility.md` only tabulates
   GPT-OSS and Qwen3-VL, but `vllm_neuron/model/` also ships `llama3` and
   `synthetic` subpackages. Whether Llama3 is a first-class supported recipe
   with its own (undocumented) feature support, or a dev/reference-only
   model, is NOT resolved by this lens — flagged as an open gap.
6. **NIXL connector docstring implies a prior monkeypatch-based
   implementation was replaced** (`neuron_nixl_connector.py:5`, "Replaces the
   monkey-patch approach") but the superseded code was not located in this
   pass (may be fully removed, or may survive in `vllm_neuron/overrides/` or
   elsewhere — not checked).

## 5. Graph implications

(Observations only — no complete graph proposed, per role constraints.)

- The **platform entry point is a single narrow surface**
  (`pyproject.toml` entry point → `vllm_neuron.register` → one hardcoded
  class path) — cheap for route analysis to model as one node, but
  `NeuronPlatform` itself (922 lines, `vllm/platform.py`) is a **wide
  fan-in** surface: DCP validation, quantization gating, attention-backend
  selection, and three separate monkeypatches all live in this one file.
  Any conflict-aware scheduler modeling "file surfaces touched" must treat
  `vllm_neuron/vllm/platform.py` as high-collision — most feature classes
  that touch config validation or attention-backend selection will overlap
  here.
- **`neuron_model_runner.py` (8278 lines, one class) is the highest-risk
  collision surface in the repo.** Spec decode, KV cache management, encoder
  cache, warmup/compile, and core execution all live inside one class body.
  Route analysis / conflict scheduling should assume near-certain overlap
  between any two campaigns that both touch runtime execution behavior,
  unless campaigns can be scoped to genuinely disjoint method ranges (e.g.,
  encoder-cache-only vs spec-decode-only) — not verified whether such
  disjoint scoping is safe without reading method bodies.
- **Compile-cache path resolution (`envs.py:341-391`) is a single shared
  choke point** touched by 6+ files (`nki_cache.py`, `neuron_profiler.py`,
  `executor.py`, `parallel_compile.py`, `backend.py`, `capture_backend.py`).
  This is the resource requirements.md's forbidden-effect #2 protects
  against clearing — any campaign compiling on a shared host intersects
  this path family.
- **NIXL connectors and the decode-bench connector are comparatively
  well-isolated** (two small files, clean subclassing of upstream ABCs, no
  entanglement with the model-runner monolith found in this pass) —
  candidate for lower-conflict-risk campaign scheduling relative to the
  model-runner/platform files.
- **Test layout gives almost no regression safety net** (7 files, all
  spec-decode). Any campaign touching scheduler, worker, platform, or
  accuracy modules starts with zero existing coverage in the public tree —
  reinforces requirements.md's "test layout is a per-campaign kickoff
  decision."

### Campaign-touch surfaces (grounded, not exhaustive)

| Feature class | Files a port would most likely touch | Grounding |
|---|---|---|
| Spec decode (EAGLE/EAGLE3/P-EAGLE) | `vllm_neuron/vllm/spec_decode/{decorator.py,eagle.py}`; `vllm_neuron/vllm/sample/rejection_sampler.py`; `neuron_model_runner.py` (`take_draft_token_ids` L7460, warmup/graph-extract methods L4340-4886); `vllm_neuron/functional/{spec_decode_correction.py,parallel_draft_inputs.py}`; existing tests in `test/unit/spec_decode/*` (7 files) | Directory listing + grep of model-runner method names; only populated test subtree |
| LoRA | Not located as a distinct module in this pass — no `lora` directory found under `vllm_neuron/`. INFERENCE: LoRA support may not exist yet in this plugin, or lives inline in `neuron_model_runner.py`'s `load_model`/`execute_model` — NOT VERIFIED, open gap |
| Chunked / segmented prefill | `vllm_neuron/vllm/core/scheduler.py` (`NeuronScheduler`, L78-905); `neuron_model_runner.py` warmup/graph-extract methods (`extract_prefill_graphs` L4340, `warmup_prefill` L4410); feature-matrix names this "segmented prefill" (supported for both current models) | Scheduler is the only file in `vllm/core/`; feature-matrix confirms feature name and support status |
| KV cache offload / disaggregated inference | `vllm_neuron/vllm/kv_connector/{neuron_nixl_connector.py,neuron_decode_bench_connector.py}`; `NeuronPlatform._validate_dcp_requires_neuron_nixl_connector` (platform.py L623); `neuron_model_runner.py` KV methods (L7651-8090) | Direct file read of NIXL connector classes; platform.py DCP/NIXL validation method names |
| Quantization (FP8/MXFP8/MXFP4) | `vllm_neuron/model/gpt_oss/{model_mxfp4.py,weight_loaders_mxfp4.py}`; `vllm_neuron/model/llama3/{model_static_fp8.py,model_mx_fp8.py}`; `NeuronPlatform._validate_quantization_config` (platform.py L438); `vllm_neuron/functional/rmsnorm_quant.py` | File sizes/names from `wc -l` listing; platform.py method name; feature-matrix rows for FP8/MXFP8/MXFP4 |
| New model onboarding | New subpackage under `vllm_neuron/model/<name>/`; `vllm_neuron/model/registry.py`; `vllm_neuron/model/interfaces.py`; `vllm_neuron/model/kv_cache.py`; likely additions to `docs/model-recipes/` and `docs/guides/reference-feature-model-compatibility.md` | Directory structure of `vllm_neuron/model/` (gpt_oss, llama3, qwen3_vl, synthetic each as sibling subpackages) |

## 6. Remaining evidence gaps

1. **LoRA support status unconfirmed.** No `lora`-named module found under
   `vllm_neuron/`; not verified against `neuron_model_runner.py`'s ~7900-line
   class body (out of this lens's read budget) or against
   `docs/guides/features-guide.md` (listed by grep hit but not opened).
   Needed before route analysis can cost a LoRA-port target.
2. **`execute_model` (L4956-6984, ~2000 lines) body not read.** Cannot
   confirm how deeply spec decode / quantization / multimodal / DCP branches
   are inlined here versus delegated — material for estimating campaign
   blast radius inside the model-runner monolith.
3. **`/var/tmp/neuron-compile-cache`** (named in requirements.md's forbidden
   list) not found by grep in `vllm_neuron/**/*.py`. Either it lives outside
   `vllm_neuron/` (scripts, CI, docs not grepped for this exact string) or is
   stale in the requirements doc. Needs a follow-up grep across the full
   repo tree (`docs/`, `ci/`, ⁠`.github/`) and possibly the standing hosts
   themselves (out of scope for this lens — no SSH).
4. **`docs/guides/features-guide.md` not opened** — only referenced via grep
   hit and cross-links from the compatibility matrix. Likely contains
   per-feature configuration detail (e.g., how EAGLE3/P-EAGLE, DCP, and
   quantization are enabled) that would sharpen the campaign-touch table.
5. **Llama3 and synthetic model support status vs the 2-model compatibility
   matrix** — not resolved (section 4, item 5). Needed to know whether
   Llama3 is a live campaign target or a dev-only reference model.
6. **Prior NIXL monkeypatch code** (referenced by
   `neuron_nixl_connector.py:5`'s docstring as superseded) not located —
   git-log/git-blame investigation would confirm removal, out of scope for
   a read-only file-tree lens without git-history commands run here.
7. **`neuron_worker.py` and `scheduler.py` method-level detail** not
   enumerated beyond class/def names — a deeper pass would map private
   helper methods to feature classes for finer-grained conflict scheduling.
8. **Coverage-omit glob mismatch** (section 4, item 2) not confirmed against
   an actual `coverage run` invocation — inferred from path comparison only.
