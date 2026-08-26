# Exploration: upstream-and-tpu-sources

## 1. Question investigated

For the parity brief's route model (route A backport, sources A1 upstream-GPU
vLLM / A2 TPU vLLM variant; route B pin upgrade fallback): where do reference
implementations for the named gap targets actually live, and how traceable are
they to a minimal commit/PR/design set — (a) in upstream vLLM between the
`v0.21.0` pin and `v0.27.1`, and (b) in the TPU vLLM variant(s) — such that a
future gap-analysis stage can resolve a target into a route and detect
substrate (Model-Runner-V2 / MRv2) entanglement?

## 2. Evidence inventory

- **Local full clone of vllm-project/vllm** at `/Users/jinhun/GitHub/vllm`
  (`origin` = `https://github.com/vllm-project/vllm.git`, branch `main`,
  fetched during this session — tags through `v0.28.0` present). All tags
  `v0.20.0`..`v0.27.1` exist locally with full history; used for `git log`,
  `git show <tag>:<path>`, `git tag --contains`, `git merge-base --is-ancestor`.
  This is a materially better evidence source than GitHub search/UI for commit
  tracing and was used for the majority of findings below (verified, not
  reported).
- **Local clone of jinhuang12/vllm-neuron** at `/Users/jinhun/GitHub/vllm-neuron`
  (this is the actual target repo, `origin` = the fork, `upstream` =
  `vllm-project/vllm-neuron`). Pin confirmed at
  `requirements/core.txt:6` = `vllm==0.21.0`.
- **`gh` CLI against `vllm-project/tpu-inference`** (no local clone; read via
  `gh api repos/vllm-project/tpu-inference/contents/...` and
  `gh api repos/vllm-project/tpu-inference/commits/main`, sha
  `f5eb3abe460f0cb7232108733214fa97cf8d9f14` at fetch time 2026-08-25).
  Includes the repo's own `support_matrices/release/v6e/vllm/*.csv`
  self-reported feature matrices — a primary source, not a blog claim.
- **GitHub Releases / Issues via `gh`** for `vllm-project/vllm`: release notes
  for `v0.24.0`, `v0.25.0`; label list; RFC/feature-request issue search.
- No shallow clone into `/tmp` was needed — the existing local `vllm` clone and
  `gh api` covered everything required.
- Did **not** fetch the TPU-support blog post
  (`https://blog.vllm.ai/2025/10/16/vllm-tpu.html`) directly; its claim is
  corroborated by the tpu-inference README's own "About" section (fetched via
  `gh api`) instead, which is the primary source.

## 3. Findings with citations

### 3.1 Gap-target table (upstream introduction version, MRv2 entanglement)

| Target | Pre-existed at v0.21.0 pin? | Version introduced (if new) | MRv2-entangled? | Evidence |
|---|---|---|---|---|
| **DFlash** | No — new after pin | `v0.24.0` (official release-notes highlight); underlying merge commit `0bae1d38480374365ad77bbea50be225237572ea` ("[MRV2][Spec Decode] DFlash (#44586)") dated 2026-06-10, first tag-reachable at `v0.23.1rc0` (2026-06-15) | **Partial.** Basic DFlash1 has a legacy (non-MRv2) V1 runner path: `vllm/v1/worker/gpu_model_runner.py:198,680-681` imports and instantiates `vllm.v1.spec_decode.dflash.DFlashProposer`. The newer DFlash2 candidate-selector and hybrid SWA+full-attention drafters are MRv2-only: `vllm/config/vllm.py` `use_v2_model_runner` property forces V2 via `self._is_dflash2_draft()` and `self._dflash_needs_multi_kv_group()` (verified, read directly, lines ~665-676 of `vllm/config/vllm.py` at current `main`, commit `6a9c69fa851389dcf1ee5d3a2363e27af665d26d`). | verified |
| **DSpark** | No — new after pin | `v0.25.0` (release-notes highlight "new DSpark (#46995)"); merge commit `f5a8d73377d0f0a4e00cba172f9fbd0d50471b07`, dated 2026-07-01, first tag-reachable at `v0.25.0` | **Yes, unconditionally.** `vllm/config/vllm.py:~656-661` (`use_v2_model_runner` property): "DSpark is implemented only by the V2 GPU model runner... force V2 for it." `grep -c "dspark" vllm/v1/worker/gpu_model_runner.py` = 0 (zero hits in the legacy runner). MRv2-only submodule: `vllm/v1/worker/gpu/spec_decode/dspark/{speculator.py,utils.py}`. | verified |
| **LoRA** | Yes — long pre-existing upstream feature, not new in 0.21→0.27 window | n/a | Not MRv2-specific upstream (works on both runners); on TPU, `LoRA_Torch` passes correctness+performance on the `vllm`-frontend path (see 3.3). On vllm-neuron the gap is that the plugin never implemented it: `vllm_neuron/vllm/worker/neuron_model_runner.py:377,404-407,1202-1205` are TODO stubs ("TODO: Inherit from LoRAModelRunnerMixin", "TODO: Initialize LORA configuration and manager", "TODO: Load LORA model wrapper"). | verified |
| **Pipeline / context parallelism** | Pipeline parallelism: long pre-existing. Prefill Context Parallelism (PCP): pre-existing before the pin — introduced `2fd893b4cec0975a2a8430077fd9b4f294eb3561` ("[Feature] Prefill Context Parallel (PCP) basic support (#28718)"), dated 2025-11-19, first tag-reachable `v0.12.0`; `git merge-base --is-ancestor` confirms it predates `v0.21.0`. | n/a (pre-existing) | PCP is MRv2-only: `vllm/config/vllm.py` `use_v2_model_runner` forces V2 "PCP runtime support is implemented only by the V2 model runner," and `check_and_update_config` raises if `prefill_context_parallel_size > 1` without V2 (`vllm/config/vllm.py:1671-1675`). Pipeline parallelism itself is not MRv2-exclusive. On vllm-neuron: partial plumbing exists — `vllm_neuron/parallel/neuron_parallel_state.py:685,712,737` and `vllm_neuron/vllm/worker/neuron_worker.py:614` pass `pipeline_parallel_size` through, but wiring depth into the model runner was not verified in this pass (see gaps). Context parallel in vllm-neuron is present only as Neuron's own decode-context-parallel (`decode_context_parallel_size`, `vllm_neuron/vllm/platform.py:105-110,547-638`), a different mechanism from upstream PCP. | verified (upstream); partially verified (plugin) |
| **Chunked prefill** | Yes — long pre-existing upstream feature | n/a | Not MRv2-specific. | On vllm-neuron: **partially supported, not absent.** `vllm_neuron/vllm/platform.py:342-346` warns but allows `enable_chunked_prefill`, restricted to "chunking prefills with batch size of 1" and explicitly "Mixing prefill and decode in the same batch is not supported" — i.e. no unified prefill+decode engine step. This is the precise gap the TPU cousin-source (unified batching) targets. | verified |
| **KV offloading** | Yes — pre-existing upstream (`VLLM_KV_OFFLOAD_MAX_BATCH_DESCRIPTORS` etc. present in current `vllm/envs.py:304`) | n/a | Not confirmed MRv2-specific in this pass. | Not found anywhere in vllm-neuron (`find vllm_neuron -name "*.py" | xargs grep -li kv_offload` → no hits) — genuinely absent from the plugin. | verified absence in plugin; upstream MRv2-coupling **not verified** (gap) |
| **MTP** (multi-token prediction) | Upstream: exists as part of several model families' spec-decode paths (e.g. GLM MTP fixes referenced in `v0.25.0` release notes: "GLM MTP post-final-norm fix (#47448)"); not a single dated "introduction." | n/a (diffuse, model-specific) | Not established as a single MRv2-gated feature; appears model-by-model. | vllm-neuron: only one comment reference, `vllm_neuron/vllm/worker/neuron_model_runner.py:7385` ("On GPU this handles MTP/EAGLE for hybrid models"), describing GPU behavior in a comment, not an implementation. Absent as a plugin feature. | verified absence in plugin; upstream picture is **incomplete** (gap) |
| **MXFP8** | Yes — long pre-existing upstream (earliest hit `59d7ffc17f4c948b4d25d014e4f90d0cd4c20990`, dated 2025-09-13, confirmed ancestor of `v0.21.0` via `git merge-base --is-ancestor`) | n/a | Not established as MRv2-gated. | **Contradicts the brief's "unsupported today" claim for MXFP8.** vllm-neuron already has a wired, non-trivial MXFP8 implementation for Llama3: `vllm_neuron/model/llama3/model_mx_fp8.py`, `weight_loaders_mx_fp8.py`, `weight_pack_mx_fp8.py`, referenced from `vllm_neuron/model/llama3/quantization.py:290,300` with routing comment "Trn3 has STATIC_MX kernels (4x prefill speedup)." This code is present in the very first tracked commit, `ae6c10e "Release 0.21.0.1.0.0"` (`git log --oneline -- vllm_neuron/model/llama3/model_mx_fp8.py` shows only that commit) — i.e. it shipped with the initial release, not added later. It is Llama3/Trn3-specific hand-rolled kernels, not integration with vLLM's generic `CompressedTensorsW8A8Mxfp8` quantization-method registry — that narrower reading ("no generic MXFP8 quant-method support") may be what the brief intends, but the blanket phrasing is stale/imprecise. | verified |
| **Sleep mode** | Yes — long pre-existing upstream (CUDA-specific virtual-memory-based weight offload) | n/a | Not MRv2-specific; it is CUDA-hardware-specific. | vllm-neuron: no `sleep()`/`wake_up()` methods anywhere (`find vllm_neuron -name "*.py" | xargs grep -ln "def sleep\|def wake_up"` → zero hits) — genuinely absent. **On TPU it is also absent by design**, not just unported: `tpu_inference/worker/tpu_worker.py` (`gh api ...tpu_worker.py`, lines ~782-791) has `sleep()`/`wake_up()` raising `NotImplementedError("Sleep mode is not supported on TPU: there is no analogue of CUDA's virtual-memory allocator for offloading weights...")`. This means **source A2 (TPU) does not exist for the sleep-mode target** — TPU's own team hit the same substrate mismatch Neuron would. | verified |
| **Weight reload** | Ambiguous term; closest upstream analogue is the RL-weight-sync `update_weights`/`start_weight_update`/`finish_weight_update` worker API. | n/a | Not established. | vllm-neuron: no hits for `reload_weights`/`update_weights`/`weight_reload` — absent. TPU: `tpu_inference/worker/tpu_worker.py:757-763` implements `update_weights()` as an explicit **no-op** ("Tunix writes into the exposed vLLM model weights directly, and Raiden's receiver auto-H2Ds on receipt. The phase is kept because vLLM's contract has it.") — i.e. TPU's "weight reload" is an out-of-band mechanism tied to Google's internal RL stack (Tunix/Raiden), not a portable design. **Source A2 is weak/inapplicable here too.** | verified |

### 3.2 MRv2 default and PagedAttention removal (version-skew facts)

- **PagedAttention deleted**: commit `d715b3aa1ea6af3f663eb6d3cd8f5b6bb15770e9`,
  "Delete PagedAttention (#47361)", dated 2026-07-02, first tag-reachable at
  `v0.25.0`. Confirmed via `git show --stat` — deletes
  `csrc/attention/attention_kernels.cuh`, `paged_attention_v1.cu`,
  `paged_attention_v2.cu`, `vllm/_custom_ops.py` paged-attention bindings.
  (verified)
- **MRv2 default for all dense models**: commit
  `a2f713002df9fd08c0fe13272c76547421721f2d`, "[ModelRunner V2] Enable by
  default for all dense models (#44443)", dated 2026-07-02, first
  tag-reachable at `v0.25.0`. Release-notes text (fetched via
  `gh release view v0.25.0 -R vllm-project/vllm`): "Model Runner V2 is now the
  default for all dense models (#44443)." (verified)
- The default is **not** a single global flip; it is model/feature-conditional
  logic in `vllm/config/vllm.py`, property `use_v2_model_runner` (env var
  `VLLM_USE_V2_MODEL_RUNNER: bool | None = None` in `vllm/envs.py:299`, "If
  unset, use config defaults" per `vllm/envs.py:2037`). The oracle/"per-model
  default" mechanism itself predates the pin: first commit adding the property,
  `ae4f59f0ece88ca25ba1064ae6d3b512c3bfc606` ("[Model Runner v2] Oracle for
  model runner v2 - qwen3 dense model by default [1/N] (#39337)"), dated
  2026-05-14 (same day as the `v0.21.0` tag but first tag-reachable at
  `v0.21.1rc0`) — meaning the conditional-default machinery existed right at
  the pin boundary, and specific forcing conditions (dspark, dflash2, PCP, hybrid
  DFlash) were added incrementally afterward. This matters for route analysis:
  MRv2-forcing is feature-by-feature, not "MRv2 vs not" as a single binary per
  vLLM version. (verified)
- **PyTorch requirement**: `torch==2.11.0` unchanged from `v0.21.0` through
  `v0.26.0`; bumped to `torch==2.13.0` exactly at `v0.27.0` (confirmed via
  `git show <tag>:requirements/cuda.txt` for all 8 tags `v0.21.0`..`v0.27.1`).
  The bump commit is `75ccdf31458070501a7ca01eb1ac11728a0933fd`, "[Core] Update
  PyTorch to 2.13.0, torchvision to 0.28.0, triton to 3.7.1 (#48155)", dated
  2026-07-23. This confirms the brief's "PyTorch 2.13 required (v0.27)" claim
  exactly; whether `torch-neuronx` has a 2.13 ceiling is **not verified in this
  pass** (out of scope — no local torch-neuronx source; would need AWS
  Neuron-SDK release notes). (verified for the vLLM side; **could not verify**
  torch-neuronx ceiling)
- **NIXL pull/push redesign**: real and ongoing. Representative commits:
  `df8e63f4edabc06e51c10fe479fc9b95c303542f` ("nixl refactor: new transfer
  design (#40731)"); push-mode KV-transfer additions such as "[KV Connector]:
  Support KV push from Prefill to Decode node using Nixl KV Connector
  (#35264)" and later bugfixes ("[PD][NixlPush][Bugfix] Fix prefix caching
  (#48758)", 2026-window). vllm-neuron's own
  `vllm_neuron/vllm/kv_connector/neuron_nixl_connector.py` has no `push`/`pull`
  string matches at all — suggesting it predates or ignores the push/pull mode
  split; **not verified further** which mode (if any) it implements today.
  (verified upstream churn exists; **could not verify** plugin's current mode)

### 3.3 TPU backend state: torch-xla in-tree path vs tpu-inference plugin

- **The in-tree `torch_xla` TPU backend was removed well before the pin, not
  "around" it.** Commit `e7596371a403903218ded3a9f446981fde5737f5`,
  "[Refactor][TPU] Remove torch_xla path and use tpu-inference (#30808)",
  authored by a Google contributor, dated **2026-01-07**. `git tag --contains`
  shows it first reachable at `v0.14.0` (tagged 2026-01-16). `git merge-base
  --is-ancestor e7596371a4 v0.21.0` returns true. `v0.21.0` itself is tagged
  2026-05-14 — **about 4 months after** the torch_xla path was deleted.
  `git show v0.21.0:vllm/platforms/tpu.py` at the pin already reads only:
  ```
  try:
      from tpu_inference.platforms import TpuPlatform as TpuInferencePlatform
      TpuPlatform = TpuInferencePlatform
  except ImportError:
      logger.error("tpu_inference not found, please install tpu_inference...")
  ```
  **This directly contradicts the brief's framing** ("the torch-xla TPU
  backend that was in-tree around the 0.21 pin is the closer literal
  cousin") — there is no in-tree torch_xla backend at or near the pin to use
  as a literal cousin source. (verified, high confidence)
- **tpu-inference is not simply "JAX-first"; it is an explicit unified
  JAX+PyTorch backend, but the unification is via a JAX-lowering shim, not
  literal torch/XLA code.** The repo's own description (`gh repo view
  vllm-project/tpu-inference`): "TPU inference for vLLM, with unified JAX and
  PyTorch support." The README (fetched via `gh api
  repos/vllm-project/tpu-inference/contents/README.md`) states: "vLLM TPU is
  now powered by `tpu-inference`... unifying JAX and PyTorch under a single
  lowering path," citing the Oct 2025 blog post "vLLM TPU: A New Unified
  Backend Supporting PyTorch and JAX on TPU." The mechanism (doc:
  `docs/developer_guides/torchax_model_development.md`, fetched via `gh api`)
  is **torchax**: a `torch.Tensor` subclass wrapping a `jax.Array`, so PyTorch
  `nn.Module` forward passes are traced via `torch.func.functional_call` and
  every op becomes a JAX op under `jax.jit`. KV cache and attention kernels
  (e.g. RaggedPagedAttention) are JAX-based regardless of whether the model
  front-end is `torch` or `jax`. Repo evidence: `tpu_inference/models/vllm/`
  (torch/vllm-style model defs, e.g. `dflash.py`, `experimental/deepseek_v4.py`,
  `vllm_model_wrapper.py`) sit alongside `tpu_inference/models/jax/` (native
  JAX model defs, e.g. `gemma4.py`, `gemma4_mtp.py`), and
  `tpu_inference/layers/vllm/backends/{flash_attn.py,flash_attn_mla.py}`
  provide the torch-facing attention-backend shims over the same JAX kernels.
  **Practical implication for route A2**: what is literally re-usable
  (torch-syntax model files under `models/vllm/`) is still bottom-lowered to
  JAX/XLA kernels via torchax, not to a torch-native/eager or torch-XLA
  execution path comparable to `torch-neuronx`. So even the "torch" side of
  tpu-inference is design/algorithm-portable rather than code-vendorable into
  a torch-neuronx runtime — this refines, rather than simply confirms, the
  brief's "what ports from TPU is the design, rarely the code" caveat: it
  applies to *both* the JAX and the nominally-PyTorch parts of tpu-inference.
  (verified — architecture and mechanism directly quoted from the plugin's own
  developer docs)
- **TPU feature-support self-reporting (primary source, not inferred):** the
  repo ships machine-readable support matrices, updated 2026-08-20 per the
  README's dashboard timestamp. For the `vllm`-frontend path on v6e
  (`support_matrices/release/v6e/vllm/feature_support_matrix.csv`, fetched via
  `gh api`):
  - Chunked Prefill: ✅ Passing (both correctness and performance)
  - KV Cache Offload: ✅ Passing
  - LoRA_Torch: ✅ Passing
  - Speculative Decoding: DFlash: ❓ Untested (present in code —
    `tpu_inference/models/vllm/dflash.py` and
    `tpu_inference/spec_decode/jax/dflash.py` both exist — but not validated)
  - Speculative Decoding: Eagle3, Ngram: ✅ Passing
  `parallelism_support_matrix.csv`: PP ✅ Passing (single- and multi-host); CP
  ❓ Untested (single-host); TP/DP/EP/SP ✅ Passing.
  `quantization_support_matrix.csv`: lists mxfp4, fp8 (compressed-tensor),
  int4/int8, nvfp4 — **no MXFP8 row at all**, and a repo-wide code search
  (`gh api search/code?q=repo:vllm-project/tpu-inference+mxfp8`) returned zero
  hits. MXFP8 is not present in the TPU codebase in any form. (verified)
  Sleep mode and generic weight-reload are not row items in this feature
  matrix at all (they're worker-level ops, not model features); their status
  was established directly from `tpu_worker.py` (see table above).
- **MTP on TPU**: implemented, at least experimentally, on both fronts —
  `tpu_inference/models/jax/gemma4_mtp.py` (JAX) and
  `tpu_inference/models/vllm/experimental/deepseek_v4.py` (torch/vllm path,
  under an `experimental` directory, implying not yet production-graded).
  (verified presence; maturity not independently verified)
- **DFlash on TPU**: present in *both* front-ends —
  `tpu_inference/models/vllm/dflash.py` (torch) and
  `tpu_inference/spec_decode/jax/dflash.py` (JAX) — but marked Untested in the
  v6e support matrix. This is a real A2 candidate for DFlash specifically, but
  its correctness is not vendor-validated per the matrix itself. (verified)

### 3.4 Commit/PR traceability practice (upstream vLLM)

- **Release notes are high-quality and PR-linked.** Fetched via `gh release
  view v0.24.0 -R vllm-project/vllm` and `v0.25.0`: both are structured
  (Highlights → categorized sections: Model Support, Hardware, etc.), and
  essentially every claim inline-cites a PR number, e.g. "PagedAttention has
  been removed (#47361)," "new DSpark (#46995) and DFlash (#46770, #46853)
  drafters." This makes "which PR introduced X" tractable directly from
  release notes for headline features; second-order changes require `git log
  --grep`/`-S` against the local clone (used throughout this report).
  (verified)
- **Labels exist and are used**: `gh api repos/vllm-project/vllm/labels`
  includes `RFC`, `speculative-decoding`, `tpu`, plus feature-specific labels
  like `mrv2` ("Model Runner V2 specific") observed on issue #47172. Searching
  `gh issue list -R vllm-project/vllm --search "<title> in:title"` reliably
  surfaces tracker/RFC issues, e.g.:
  - `#41286` "[Feature]: Migration from Model Runner v1 to Model Runner v2"
    (2026-04-29, right before the pin)
  - `#47172` "Model Runner V2 Remaining TODOs" (2026-06-30, label `mrv2`)
  - `#51212` "[RFC]: Model Runner V2 Pluggable Design" (2026-08-06, label
    `RFC`)
  - `#50853` "[RFC]: Complete Model Runner V2 pipeline parallelism..."
  - `#52038` "[RFC]: LoRA adapter support for DFlash speculative decoding
    draft models" (2026-08-12) — directly relevant: shows LoRA+DFlash
    integration is itself still an open upstream RFC, not finished upstream
    work, as of the brief's date.
  (verified)
- **Net assessment**: tracing a *named, headline* feature (DFlash, DSpark,
  MRv2 default, PagedAttention removal) to its introducing PR/commit and
  surrounding RFC is feasible and fast using `gh release view` +
  `gh issue list --search` + local `git log --grep/-S`, because vLLM's release
  process consistently PR-links. Tracing a *diffuse* feature (MTP, generic KV
  offloading) to a minimal commit set is harder — these show up as many
  small, model-specific PRs across releases rather than one traceable
  RFC/PR chain (see MTP row in 3.1). Gap analysis should expect
  headline-feature tracing to be cheap and diffuse-feature tracing to require
  a broader `git log --grep`/label sweep per target. (inferred from the
  pattern observed across the ~10 targets checked, not exhaustively proven
  for every possible future target)

## 4. Contradictions and stale documentation

1. **Brief line 67 (TPU cousin-source option) is factually wrong about the
   torch-xla backend's timing relative to the pin.** It states the torch-xla
   backend "was in-tree around the 0.21 pin." Evidence in 3.3 shows it was
   removed 4 months before the pin tag was cut (`v0.14.0`, 2026-01-16, vs
   `v0.21.0`, 2026-05-14), and `v0.21.0`'s own `vllm/platforms/tpu.py` already
   delegates entirely to `tpu_inference`. **There is no in-tree torch_xla
   backend near the pin at all** — A2 sourcing must go through tpu-inference
   exclusively for any pin-contemporary comparison; there is no earlier,
   more-literal torch/XLA cousin to fall back to. This is the single most
   consequential correction from this exploration.
2. **Brief line 170's blanket "MXFP8 unsupported" is contradicted by code.**
   vllm-neuron already ships wired MXFP8 kernels for Llama3 on Trn3
   (`model_mx_fp8.py`, present since the initial `ae6c10e "Release
   0.21.0.1.0.0"` commit). The gap, if any, is narrower than the brief states
   (e.g. "no generic quantization-method-registry MXFP8," or "no MXFP8 outside
   Llama3") — gap analysis should re-scope this target rather than treat it as
   a clean zero-to-one port.
3. **Brief's "DFlash is MRv2-native upstream" (line 44, line 169) is an
   oversimplification, not fully wrong.** Base DFlash1 runs on the legacy
   (pre-MRv2) V1 runner via `vllm.v1.spec_decode.dflash.DFlashProposer`; only
   DFlash2's candidate selector and hybrid multi-KV-group drafters are
   MRv2-only. If the target model's checkpoint only needs DFlash1 semantics,
   the "full reimplementation against the old surface" cost the brief warns
   about may not apply — this is a real route-A-vs-route-B cost lever gap
   analysis should not skip.
4. **"tpu-inference plugin is JAX-first" (brief lines 51, 67, 219) is
   imprecise.** The plugin's own framing is "unified JAX and PyTorch," and it
   ships a real torch/vllm-model-definition front-end
   (`tpu_inference/models/vllm/`). The more accurate framing, per 3.3, is: both
   front-ends bottom out in JAX/XLA kernels via torchax, so neither is more
   "literally vendorable" into a torch-neuronx runtime than the other — the
   brief's conclusion (design-port, rarely code) is right, but for a subtler
   reason than "JAX-first" alone implies.
5. No stale documentation found in the vllm-neuron repo itself contradicting
   its own code in the areas checked (LoRA TODOs, chunked-prefill warning,
   and MXFP8 routing comment all matched the code they described).

## 5. Graph implications

(Descriptive only — not proposing a graph; flagging what a gap-analysis /
route-analysis node needs to do given the evidence above.)

- A route-analysis step per target should **not** treat "TPU-supported →
  evaluate A2" as binary; it must resolve *which* TPU front-end (vllm/torch or
  jax) and *which capability tier* (e.g. DFlash1 vs DFlash2) is the relevant
  comparison, since support and MRv2-entanglement differ by tier (3.1, 3.3).
- For **sleep mode** and **weight reload**, A2 is a dead end by primary-source
  evidence (TPU explicitly does not support sleep mode and no-ops weight
  reload for unrelated reasons) — the route analysis should default straight
  to A1-only or route B for these two without spending analysis budget on A2.
- For **MXFP8**, the gap-analysis target definition itself needs re-scoping
  before route costing, since the "unsupported" premise is partly false.
- Detecting MRv2 entanglement reliably (open question #1/#9 in the brief) is
  tractable via a repeatable check: (a) grep the target feature/spec-decode
  method through `vllm/config/vllm.py`'s `use_v2_model_runner` property and
  `_validate_v2_model_runner`/`_dflash_needs_multi_kv_group`/`_is_dflash2_draft`
  helpers for explicit forcing conditions; (b) grep for the feature's name in
  the legacy `vllm/v1/worker/gpu_model_runner.py` vs the MRv2
  `vllm/v1/worker/gpu/` tree — presence in the legacy runner is strong
  evidence the feature is *not* MRv2-exclusive. Both checks were used directly
  in this exploration and are mechanical/repeatable for future targets not
  covered here (KV offloading's MRv2 coupling, MTP's, etc. — see gaps below).
- Traceability cost should be modeled as two tiers per the 3.4 pattern:
  cheap (headline features, findable via `gh release view` + RFC issue search)
  vs. expensive (diffuse features spread across many small PRs, needing a
  broader `git log` sweep).

## 6. Remaining evidence gaps (could not verify)

- **torch-neuronx's actual PyTorch-version ceiling** relative to the `v0.27.0`
  bump to `torch==2.13.0` — not checked; requires AWS Neuron SDK release notes
  or a Neuron host, out of scope for this pass (public-web + local-repo only).
- **KV offloading's and MTP's relationship to MRv2** — not established either
  way in upstream code in this pass (time-boxed); KV offloading env vars exist
  in `vllm/envs.py` but the runner-level implementation was not traced to
  confirm/deny MRv2 coupling. MTP appears diffuse/model-specific rather than a
  single gated feature, but this is inferred from release-note mentions, not
  from reading the MTP dispatch code directly.
- **vllm-neuron's NIXL connector's current pull/push mode** — no `push`/`pull`
  string matches found in `vllm_neuron/vllm/kv_connector/neuron_nixl_connector.py`;
  did not read the full file to determine what transfer design it actually
  implements relative to upstream's push/pull split.
- **Depth of vllm-neuron's existing pipeline-parallelism plumbing** —
  confirmed `pipeline_parallel_size` is threaded through
  `neuron_parallel_state.py` and `neuron_worker.py`, but did not verify whether
  this is live/tested functionality or dead/partial scaffolding (e.g. whether
  `NeuronModelRunner` actually splits stages). This directly affects whether
  "pipeline parallelism" should be scoped as a full port or a completion task.
- **DFlash/DSpark TPU correctness** beyond the self-reported CSV status
  (Untested for DFlash; no DSpark row was found in the v6e vllm feature matrix
  at all — did not check the JAX-frontend matrix or other TPU generations
  (v7x) for DSpark, so DSpark's TPU support status is unconfirmed either way).
- Did not independently fetch `https://blog.vllm.ai/2025/10/16/vllm-tpu.html`
  (the announcement blog for the unified backend) — relied on the
  tpu-inference README's own restatement of it, which is a primary source but
  a secondhand rendering of the blog's content.
- **vLLM `v0.28.0`** exists in the fetched tag list (released after the
  brief's stated "v0.27.1 at brief time") — not investigated at all; flagged
  only so a future run knows the upstream horizon has already moved past the
  brief's stated latest.
