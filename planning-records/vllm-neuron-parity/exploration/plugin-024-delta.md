# Plugin 0.21→0.24 delta lens: vLLM-Neuron structural, feature, validation, dependency shifts

## 1. Question investigated

What changed in the vLLM-Neuron plugin between `release-0.21.0.1.0.0` and
`release-0.24.0.1.1.0` that affects (a) integration/collision surfaces, (b)
feature/model support baseline, (c) validation machinery — re-scoping the
parity backlog and freeze-replicate venv assumptions that `target-plugin.md`
grounded on the 0.21 fork. Also settles the plugin's exposure to upstream
vLLM's ModelRunnerV2 (MRv2) / PagedAttention-removal boundary at v0.25.

Scope: `git clone --depth 1` of two **upstream** `vllm-project/vllm-neuron`
branches to temp dirs (`release-0.21.0.1.0.0` → `/tmp/vllm-neuron-021-upstream`,
`release-0.24.0.1.1.0` → `/tmp/vllm-neuron-024`), read-only exploration of the
local fork at `/Users/jinhun/GitHub/vllm-neuron` (no fetch/checkout there), `gh`
not needed, one `git ls-remote` against the public GitHub URL, and two web
searches (vLLM MRv2 timeline; Neuron SDK 2.32 release notes). Both temp clones
deleted at end of this lens (see cleanup note in §2).

## 2. Evidence inventory

**Baseline read first, in full:**
`exploration/target-plugin.md` (all 473 lines) — the 0.21 fork lens this
report diffs against.

**Local fork (read-only, no fetch/checkout):**
- `git branch --show-current` / `git log -1` / `git remote -v` at
  `/Users/jinhun/GitHub/vllm-neuron` — established the fork is on
  `feature/p-eagle-gpt-oss-20b`, commit `0e19f00`, remotes `origin` =
  `jinhuang12/vllm-neuron`, `upstream` = `vllm-project/vllm-neuron`. **This
  is the critical evidence-inventory correction for this whole lens** — see
  §4 item 0.
- `requirements/core.txt`, `requirements/test.txt` (full, for upstream-0.21
  vs fork-0.21 sanity diff).

**`git ls-remote --heads https://github.com/vllm-project/vllm-neuron.git`**
(network read, no local repo touched) — full branch list, used to confirm
`release-0.24.0.1.1.0` exists as a real upstream branch distinct from
`release-0.24.0.1.1.0-workflows`.

**Upstream `release-0.21.0.1.0.0` clone** (`/tmp/vllm-neuron-021-upstream`,
commit `d9926310`, deleted after this lens):
- `git ls-tree -r --name-only HEAD` (full file list, 292 files)
- `requirements/{core,test,vllm_build_requirements,vllm_rc_test}.txt` (full)
- `.github/workflows/*.yml` (full)
- `pyproject.toml` (full, lines 1-70)
- `find` of `vllm_neuron/` top-level subdirectory tree

**Upstream `release-0.24.0.1.1.0` clone** (`/tmp/vllm-neuron-024`, commit
`f8abae64`, deleted after this lens):
- `git ls-tree -r --name-only HEAD` (full file list, 394 files)
- `pyproject.toml`, `requirements/*.txt` (full)
- `vllm_neuron/__init__.py` (def/class inventory, full grep), `backend.py`
  (full, 93 lines)
- `vllm_neuron/vllm/platform.py` (full def/class inventory grep, 938 lines;
  `_reject_v2_model_runner` and surrounding `check_and_update_config` read in
  full, lines 280-320)
- `vllm_neuron/vllm/worker/neuron_model_runner.py` (class signature +
  full named-method grep against the exact 0.21 method list, 9086 lines, not
  read in full — same size-bounded triage as baseline)
- `vllm_neuron/vllm/worker/neuron_worker.py` (full def/class inventory, 2225
  lines)
- `vllm_neuron/vllm/core/scheduler.py` (full def/class inventory, 1084 lines)
- `vllm_neuron/vllm/patches/__init__.py` (full, 16 lines), `pin_memory_patch.py`
  (full, 45 lines), `port_hold_patch.py` (line count only, 204 lines)
- `vllm_neuron/envs.py` (compile-cache resolver read in full, lines 375-435;
  `VLLM_NEURON_CPU_MODE`/`VLLM_NEURON_CPU_COMPILE` decl + resolver grep)
- `vllm_neuron/accuracy/` full listing (41 `.py` files) +
  `docs/model-dev/accuracy-debugging-guide.md` (grepped for the same
  threshold strings baseline cited)
- `docs/guides/reference-feature-model-compatibility.md` (full — now a stub
  redirect, see §4)
- `docs/model-recipes/{gpt-oss,llama-3,qwen3-vl,qwen3-embedding-8b}.md`
  (Features table sections read in full)
- `docs/design/compilation/compilation_cache.md` (lines 1-80)
- `.github/workflows/*.yml` (full, diffed byte-for-byte against 0.21)
- `test/`, `ci/` presence checked via `git ls-tree -r | grep` (zero hits,
  confirmed not a shallow-clone artifact — see §4 item 0)
- Full recursive file-list diffs: upstream-0.21 vs upstream-0.24 (both
  directions — new files and removed files enumerated exhaustively via
  `diff`+`sort` on `git ls-tree` output, not sampled)

**Web sources:**
- vLLM blog `vllm.ai/blog/2026-03-24-mrv2` ("Model Runner V2: A Modular and
  Faster Core for vLLM") — MRv2 announcement, opt-in flag.
- Third-party blog `saucam.substack.com/p/mrv2-how-vllm-rewrote-its-model-runner`
  (May 2026) — MRv2 status snapshot, feature-parity caveats.
- `markaicode.com/vs/cuda-vs-vllm` (Aug 2026) — states PagedAttention removed
  and MRv2 made default as of vLLM 0.25.0 (July 2026). Third-party, not
  vLLM's own release notes — see §6 uncertainty note.
- SourceForge mirror of AWS Neuron SDK 2.32.0 release notes (dated 08/17/2026)
  — confirms "This release also upgrades vLLM Neuron ... to vLLM 0.24.0" and
  lists a new `neuron-framework-autoport-vllm-neuron` Neuron Agentic
  Development skill.

**Not read / triage limits carried forward from baseline, still unresolved
at 0.24:** `neuron_model_runner.py` method *bodies* (only signatures/line
numbers diffed), `neuron_worker.py`/`scheduler.py` private-helper bodies,
`vllm_neuron/model/{gpt_oss,llama3,qwen3,qwen3_vl}/*` model-code internals
(only directory listings and doc tables read), the new
`vllm_neuron/accuracy/accuracy_debugger/*` and `snapshot/*` subpackage
internals (file lists only, not opened).

**Cleanup:** `/tmp/vllm-neuron-024`, `/tmp/vllm-neuron-021-upstream`, and
`/tmp/vllm-neuron-024-work` removed at end of this lens.

## 3. Findings with citations

### 3.0 Evidence-inventory correction that reframes every downstream diff

`target-plugin.md` was captured against `/Users/jinhun/GitHub/vllm-neuron`,
which is **not** upstream `release-0.21.0.1.0.0` — it is the fork
(`jinhuang12/vllm-neuron`) checked out on `feature/p-eagle-gpt-oss-20b`
(confirmed: `git remote -v` shows `origin`=`jinhuang12/vllm-neuron`,
`upstream`=`vllm-project/vllm-neuron`; `git log -1` shows commit `0e19f00`,
not the `d9926310` tip of upstream `release-0.21.0.1.0.0`).

Diffing file lists (`git ls-tree -r --name-only HEAD`, sorted, `diff`)
between the fork and upstream `release-0.21.0.1.0.0` (292 files) shows the
fork adds exactly 8 files beyond upstream:

```
test/unit/spec_decode/test_eagle_parallel_drafting_config.py
test/unit/spec_decode/test_eagle3_multilayer_backbone.py
test/unit/spec_decode/test_eagle3_parallel_forward.py
test/unit/spec_decode/test_eagle3_two_pass_kv_prime.py
test/unit/spec_decode/test_eagle4_bucketing.py
test/unit/spec_decode/test_eagle4_routing.py
test/unit/spec_decode/test_parallel_draft_inputs.py
vllm_neuron/functional/parallel_draft_inputs.py
```

— and removes zero. **This means `target-plugin.md` §3.11's "public repo
ships a thin, spec-decode-only test suite (7 files)" is a fact about the
fork's P-EAGLE feature branch, not about upstream vllm-neuron.** Confirmed
directly: `git ls-tree -r --name-only HEAD | grep -E '^(test|ci)/'` returns
**zero** hits on both upstream `release-0.21.0.1.0.0` and upstream
`release-0.24.0.1.1.0` — upstream ships no `test/` or `ci/` directory at
either version (not a shallow-clone artifact; `git ls-tree` reads the git
object database directly, unaffected by `.gitignore`).

Practical consequence for the version-bump campaign: those 7 test files +
`parallel_draft_inputs.py` are fork-owned artifacts that must be re-applied
on top of the new 0.24 base during the rebase — they are not part of what
"moving to 0.24" inherits for free, and they are not what upstream's own
validation posture looks like at either version.

All findings below compare **upstream 0.21 vs upstream 0.24** (apples to
apples) unless explicitly marked "fork-only."

### Q1 — Structural surfaces

**File-size deltas (upstream 0.21 → upstream 0.24, exact `wc -l` /
`git ls-tree` counts):**

| File | 0.21 | 0.24 | Δ |
|---|---|---|---|
| `vllm_neuron/vllm/platform.py` | 922 | 938 | +16 |
| `vllm_neuron/vllm/worker/neuron_model_runner.py` | 8278 | 9086 | +808 |
| `vllm_neuron/vllm/worker/neuron_worker.py` | 1909 | 2225 | +316 |
| `vllm_neuron/vllm/core/scheduler.py` | 1025 | 1084 | +59 |
| `vllm_neuron/backend.py` | ~93 | 93 | ~0 |
| `vllm_neuron/__init__.py` | ~241 (baseline read in full) | 241 | ~0 |

**NeuronPlatform, NeuronModelRunner, NeuronWorker, NeuronScheduler all still
exist with the same shape and the monolith persists — confirmed, not
resolved.** `neuron_model_runner.py:383`:
`class NeuronModelRunner(KVConnectorModelRunnerMixin, NeuronECConnectorModelRunnerMixin)`
— note this is now **two** mixins, not one (0.21 had only
`KVConnectorModelRunnerMixin`); the second is new (§Q2). Every named public
method target-plugin.md §3.5 cited by name is still present, at shifted but
proportionally-consistent line numbers (`load_model` 1151→1222, `execute_model`
4956→5293, `take_draft_token_ids` 7460→8264, `initialize_kv_cache`
7651→8456, `ensure_kv_transfer_shutdown` 8277→9085, etc. — full grep, not
sampled). The ~2000-line `execute_model` body itself was still not read
(same triage limit as baseline).

`NeuronWorker(WorkerBase)` — same one-class shape
(`neuron_worker.py:320`, was `:188`), but the module-level helper surface
grew: baseline's 3 helpers (`validate_cross_node_master_addr`,
`resolve_ep_degree`, `rendezvous_ccom_bootstrap`) are joined by 3 new ones
(`_ephemeral_ccom_addr`, `_ccom_addr_from_visible_devices`,
`_offset_comm_id_port`) plus a new `_SuppressModelRegistryOverwrite`
logging-filter class (`neuron_worker.py:302`) — INFERENCE: expanded
multi-node CCOM-bootstrap address handling, method bodies not read.

`NeuronScheduler(Scheduler)` / `NeuronAsyncScheduler(NeuronScheduler,
AsyncScheduler)` — identical 2-class hierarchy, `SchedulerState(Enum)` still
first (`scheduler.py:40`, was `:42`). Structurally unchanged.

**The three monkeypatch mechanisms persist — and a fourth was added.**
1. DCP config-validation patch: `_patch_dcp_config_validation()`
   (`platform.py:53`) + new-named inner `_apply_dcp_patch(ModelConfig)`
   (`platform.py:93`) — same eager/lazy-audithook structure as baseline
   §3.3.1, functionally unchanged, minor internal rename.
2. Termination-timeout patches: `_patch_termination_timeouts`/`_patch_shutdown`/
   `_patch_ensure_worker_termination` at `platform.py:799/844/883` (was
   `783/844/883`-ish region in 0.21) — unchanged.
3. Port-hold patch: `vllm_neuron/vllm/patches/port_hold_patch.py`, 204 lines
   (baseline saw "~150+"), applied from `vllm_neuron/__init__.py:232-234`
   (`from ...port_hold_patch import apply_port_hold_patch; apply_port_hold_patch()`)
   — same ad hoc, non-centralized application pattern.
4. **NEW: pin-memory patch** — `vllm_neuron/vllm/patches/pin_memory_patch.py`
   (full file read, 45 lines). Forces `vllm.utils.torch_utils.PIN_MEMORY`
   (a module-level constant cached at import from
   `current_platform.is_pin_memory_available()`) to `False` via a
   `sys.modules` rewrite, because Neuron's CPU-mode privateuse1 backend has
   no `PrivateUse1HooksInterface` registered and would otherwise raise
   `RuntimeError: Please register PrivateUse1HooksInterface ... first` when
   the pooling path (`vllm.v1.pool.metadata.build_pooling_cursor`) allocates
   pinned memory. Applied the same ad hoc way, from
   `vllm_neuron/__init__.py:239-241`. Ties directly to the new
   `NeuronPlatform.is_pin_memory_available` method (`platform.py:670`, new
   vs 0.21's inventory) and to the new pooling/embedding model support
   (Q2). **This is the "centralized patches" stub problem repeating with a
   4th mechanism** — see §4.

**"Centralized patches" stub is still a stub, word-for-word almost.**
`vllm_neuron/vllm/patches/__init__.py` (full file, 16 lines) — docstring
now says (more specifically than 0.21): *"All monkey-patches to upstream
vLLM are applied here via `apply_patches()`. Called from
`NeuronPlatform.check_and_update_config()` after vLLM is fully initialized."*
`apply_patches()` is defined and its body is still empty (file ends right
after the `def` line). `check_and_update_config` (`platform.py:309`, read in
full) does **not** call `apply_patches()` — it calls
`cls._reject_v2_model_runner()` and `cls._patch_termination_timeouts()`
directly. Grep for `apply_patches` across the whole 0.24 tree: zero call
sites outside the definition file. **The stub not only survived the version
bump, its docstring became more specific/more wrong.**

**New platform.py methods (not in 0.21's inventory):**
`_resolve_vision_auto_config` (207, multimodal-related),
**`_reject_v2_model_runner`** (287, MRv2 guard — see Q5),
`is_pin_memory_available` (670), `get_device_communicator_cls` (680),
`support_hybrid_kv_cache` (704), `_auto_set_neuron_connector_module_path`
(754), `_auto_set_neuron_ec_connector_module_path` (779). **Removed from
platform.py's inventory:** `_register_neuron_all2all_backend` — grepped the
whole 0.24 tree for this name, zero hits, confirms removal, not a
relocation. DCP validation consolidated: 0.21's four separate methods
(`_validate_component_dp_config`, `_validate_prefill_dcp_config`,
`_validate_decode_dcp_config`, `_validate_dcp_requires_neuron_nixl_connector`)
are replaced by `_validate_dcp_config` (599) plus
`_has_neuron_component_dp`/`_maybe_enable_moe_for_component_dp` — a
refactor/consolidation, not read in enough depth to confirm behavioral
equivalence (INFERENCE only).

**Compile/FX-pass/overrides subsystem removed from the plugin package
entirely — moved to an external, private-index Neuron SDK package (well-
evidenced inference).** Recursive file-list diff (upstream 0.21 → upstream
0.24) shows these entire top-level `vllm_neuron/` subpackages disappear:
`compile/` (9 files: `artifacts.py`, `backend.py`, `cache.py`,
`capture_backend.py`, `hlo.py`, `parallel_compile.py`, `parallel_trace.py`,
`platform.py`, `schema.py`), `fx_passes/` (8 files), `overrides/` (3 files),
and `nki/` collapses from 5 files (`nki_cache.py`, `nki_compile.py`,
`nki_cpu_sim.py`, `nki_dtype.py`, `nki_hop.py`) to a bare `__init__.py`
stub. Confirmed via `find vllm_neuron -maxdepth 1 -type d` on both clones and
`ls vllm_neuron/nki/` on 0.24 (only `__init__.py`). Grep across the entire
0.24 tree for the classes these files defined (`AliasingPass`,
`DeviceRewriterPass`, `NkiKernelWriteBackendConfigPass`, `FXPassManager`,
etc.) returns **zero hits** — the classes are gone, not renamed in place.
Meanwhile `requirements/core.txt` gains a new line
(`libtorch-neuronx-lite`, unpinned, comment: *"Neuron torch compile/runtime
framework ... Left unpinned to resolve based on vLLM's torch requirement"*)
— and `docs/design/compilation/fx_passes_design.md` (a doc that is itself
*new* at 0.24, see Q3) still describes the removed
`vllm_neuron/fx_passes/{__init__,base,aliasing_pass,backend_config_pass,collective_replica_groups_pass,device_rewriter,inplace_rewrite_pass,pass_manager}.py`
file tree by name, as if it still ships in-repo. PyPI metadata for
`libtorch-neuronx-lite` (fetched via WebFetch) confirms it is a placeholder
on public PyPI ("WRONG PACKAGE. Please install ... from
pip.repos.neuron.amazonaws.com") — i.e. it only resolves from AWS's private
Neuron package index, consistent with it being the real Neuron
compiler/runtime SDK package the removed in-repo code now delegates to.
**INFERENCE (well-supported, not confirmed by reading the private
package):** the FX-pass/compile-backend machinery moved out of the
open-source plugin and into this SDK package. Directly relevant to Q4 — see
below.

**Compile-cache mechanics that DID stay in the plugin are unchanged.**
`vllm_neuron/envs.py:380-430` (`get_neuron_compile_cache_dir()` /
`_resolve_neuron_compile_cache_dir()`, read in full) — identical resolution
order to baseline §3.12: explicit `VLLM_CACHE_ROOT` override →
`$VLLM_CACHE_ROOT/neuron/compile_cache`; else NFS/Lustre probe on `~` →
fallback `/tmp/vllm_neuron_wdir_$USER/neuron/compile_cache` with a warning;
else default `~/.cache/vllm/neuron/compile_cache`. Still consumed from
`vllm_neuron/utils/executor.py:661` (same line number as baseline cited for
0.21 — this file is essentially untouched around this call site). **New in
0.24, not in 0.21: a documented two-tier cache** — `docs/design/compilation/compilation_cache.md`
(new doc, read lines 1-80) describes an *optional* remote cache tier via
`NEURON_LIBTORCH_REMOTE_CACHE` (NFS/FSx mount, promoted via `save_cache()`),
layered on top of the unchanged local-cache path/lock semantics. This
remote-cache code was not located inside `vllm_neuron/` (grep for
`save_cache`/`NEURON_LIBTORCH_REMOTE_CACHE` returned no hits in the plugin
tree) — consistent with it living in the same external
`libtorch-neuronx-lite`-class package as the FX passes. NOT CONFIRMED by
reading that package.

**New unrelated locking subsystem:** `vllm_neuron/utils/core_allocator.py`
(new file) implements `CoreAllocator` + a private `_FileLock` for
NeuronCore-allocation coordination across processes — a *different* FileLock
usage than the compile cache, do not conflate the two when reasoning about
shared-resource conflicts.

### Q2 — Feature baseline shifts

**Feature-compatibility doc restructured into per-model recipe pages.**
`docs/guides/reference-feature-model-compatibility.md` (full file at 0.24)
is now a 14-line stub: *"Redirect to per-model feature support in model
recipe pages... See Model recipes for the full list."* The single 2-model
matrix baseline read is gone; feature status now lives in per-model
`docs/model-recipes/{gpt-oss,llama-3,qwen3-vl,qwen3-embedding-8b}.md`
"Features" tables (all four read in full).

**Two new model families onboarded** (closing target-plugin.md §4 item 5's
open question about Llama3/synthetic status — Llama3 is confirmed
first-class; synthetic status still unresolved, not re-checked here):
- **Llama-3** now has its own recipe page and its own feature table
  (`docs/model-recipes/llama-3.md:73-91`): FP8 static (ModelOpt) ✅, KV cache
  FP8 ✅, EAGLE3 spec decode ✅, disaggregated inference (1P1D/xPyD) ✅,
  segmented prefill ✅, TP ✅, DP ✅ ("see Known issues"), EP N/A (dense
  arch), PP ❌. New model dirs: `vllm_neuron/model/llama3/eagle3_model.py`
  (new file, found via grep for "compile" hits) plus new tutorials
  `tutorial-eagle3-speculative-decoding-llama-3-1.md`,
  `tutorial-llama3-70b.md`.
- **Qwen3-Embedding-8B** — an entirely new model family, not present at all
  in upstream 0.21's `vllm_neuron/model/` tree (confirmed via the
  0.21→0.24 new-file diff: `vllm_neuron/model/qwen3/{__init__,config,factory,model,model_embedding}.py`
  are all new). Feature table
  (`docs/model-recipes/qwen3-embedding-8b.md:51-70`): embeddings via
  `/v1/embeddings`/`LLM.embed()` ✅, classification/scoring/reranking ❌,
  BF16 ✅, FP8/MXFP4 ❌, KV cache FP8 ❌, TP/DP ✅, PP/CP ❌, Matryoshka
  variable-dim output ✅, async scheduling ❌, disagg ❌. Backed by new
  `vllm_neuron/model/pooling_adapter.py` and a new design doc
  `docs/design/vllm/pooling-models.md` — pooling/embedding-model support is
  a genuinely new capability class, not present in 0.21 at all.

**GPT-OSS 0.21→0.24** (`docs/model-recipes/gpt-oss.md:39-56`): MXFP4 (Trn3)
✅, BF16 (Trn2) ✅, TP/DP/EP ✅, PP ❌ (**pipeline parallelism still
unsupported** — directly answers the brief's PP question for GPT-OSS), EAGLE3
spec decode ✅, disaggregated inference (1P1D/xPyD) ✅, segmented prefill ✅,
torch.compile (XLA backend) ✅, CPU mode ✅. The 0.21 baseline's explicit ❌
rows for GPT-OSS (FP8 static weight quant, MXFP8 weight quant, multimodal
image input) have **no corresponding row at all** in the 0.24 table — the
doc reorg dropped those categories rather than flipping them to ✅; treat
as "not stated," not "resolved" (see §6).

**Qwen3-VL 0.21→0.24** (`docs/model-recipes/qwen3-vl.md:33-63`):
Text/single-image/multi-image/video ✅, BF16 ✅, **MXFP8 (Trn3 only) ✅ —
genuinely closed gap**, directly backed by three new model files
(`vllm_neuron/model/qwen3_vl/{model_mxfp8.py,weight_loaders_mxfp8.py,weight_pack_mxfp8.py}`,
confirmed new via the file-diff) and a new `utils/decode_kv.py`. TP ✅,
vision-encoder parallelism ✅ (new row), PP ❌, CP ❌ (new row, N/A→❌
distinction not resolvable from this pass), on-device sampling ✅,
**disaggregated encoder (EPD) (1E1PD/xEyPD) ✅ — new capability**, backed by
three new files/dirs: `vllm_neuron/vllm/ec_connector/neuron_nixl_ec_connector.py`
(595 lines), `vllm_neuron/vllm/disaggregated_encoder/{codec.py,protocol.py,router.py,routing.py}`
(4 files, 246+82+561+147=1036 lines), and
`vllm_neuron/vllm/worker/neuron_ec_connector_model_runner_mixin.py` (wired
directly into `NeuronModelRunner`'s base classes — see Q1). **This is
encoder-side disaggregation specifically (vision-encoder ↔ prefill/decode
split), not the same "disaggregated inference" the 0.21 matrix scored ❌ for
Qwen3-VL** (which was prefill/decode disagg) — be precise, do not conflate
the two disagg mechanisms when scoping a "disagg gap closed" campaign item.
**Segmented prefill ❌ and chunked prefill (mixed batching) ❌ for Qwen3-VL**
— segmented prefill is an explicit ❌ in the 0.24 table, whereas the 0.21
baseline stated segmented prefill was supported "by both" models including
Qwen3-VL. **This reads as a regression/flip, not a stale-doc artifact** —
flagged prominently in §4, not verified against code (doc-only evidence).
The 0.21 baseline's Qwen3-VL EAGLE3/MXFP4/KV-cache-FP8 ❌ rows have no
corresponding row in the 0.24 table (same "dropped, not flipped" caveat as
GPT-OSS).

**Chunked prefill:** the term "Chunked prefill (mixed batching)" appears as
an explicit row *only* in the Qwen3-VL table (❌); GPT-OSS and Llama-3 tables
use "Segmented prefill" (✅) with no separate chunked-prefill row.
INFERENCE: these may be the same underlying vLLM upstream feature named
differently per doc author, or a genuinely distinct capability — not
resolved by this lens; scheduler method bodies (`scheduler.py`) were not
read to settle this.

**LoRA — definitively still unsupported, now with explicit in-code TODOs**
(closes target-plugin.md §6 item 1's open gap). `neuron_model_runner.py`
grep for "lora" (case-insensitive) hits only:
- line 382: `# TODO: Inherit from LoRAModelRunnerMixin to support LoRA`
- lines 409-412: `# TODO: Initialize LORA configuration and manager ... Current: No LORA config or manager initialization`
- lines 1289-1292: `# TODO: Load LORA model wrapper if LORA config exists ... Current: Base model loaded without LORA support`
- line 2011: `lora_request=new_req_data.lora_request` (a pass-through field
  read from upstream request data, not an implementation)

No `lora`-named module exists under `vllm_neuron/` at 0.24 either. **LoRA
remains a confirmed, explicit gap** — this is a firmer answer than the
baseline's "not verified, open gap."

**MoE / expert-parallelism scaling machinery expanded.** New files:
`vllm_neuron/functional/moe/{build_all_gatherv_metadata,hierarchical_all2all_combine_reduce,hierarchical_all2all_dispatch_permute,pack_tokens}.py`
and `vllm_neuron/functional/collectives/{all_gather_v,reduce_scatter_v}.py`
— new "variable-size"/hierarchical all-to-all and all-gather-v collectives
for MoE (plausibly related to Neuron SDK 2.32's "uneven per-rank data
distribution via variable-size collectives," per the Neuron release-notes
web source — not confirmed by reading the Neuron-SDK side). Also new:
`vllm_neuron/functional/vendored_kernels/rotational_topk/*` (vendored
top-k kernel, likely MoE routing) and
`vllm_neuron/functional/attention/swa_fused.py` (fused sliding-window
attention — ties to the new `NeuronPlatform.support_hybrid_kv_cache`
method, Q1). None of these bodies were read; file existence and naming
only.

### Q3 — Validation machinery

**3-level accuracy framework: unchanged core, substantially expanded
orchestration layer around it.** `docs/model-dev/accuracy-debugging-guide.md`
still describes Level 1 (task-level, lm_eval/longbench thresholds), Level 2
(prompt-level, three-way HF-FP32/HF-BF16/Neuron logit top-k comparison +
KV-cache Bhattacharyya-Coefficient comparison, **"KV cache BC ≥ 0.99"
confirmed verbatim, unchanged**), Level 3 (module-level, per-module vs HF
reference) — same structure and pass/fail language baseline §3.8 quoted.
The pp_static `[0.03, 0.05]` numeric thresholds cited in the run's
requirements doc were not located in this doc at either version (same gap
as baseline — those numbers were never grounded to this file, only to
`requirements.md`; not re-resolved here).

Flat-file `vllm_neuron/accuracy/` core validation modules are essentially
unchanged (all 15 files baseline enumerated are still present under the
same names — `logit_validation.py`, `kv_cache_analysis.py`,
`tensor_compare.py`, etc. — plus one new flat file, `lm_eval.py`).

**But the directory as a whole grew from 17 to 41 Python files** (exact
count via `find -name "*.py"` on both upstream clones), driven by two
entirely new subpackages absent from 0.21:
- `vllm_neuron/accuracy/accuracy_debugger/` (9 files: `api.py`,
  `prompt_plugins/{base,kv_cache,logit_val,tensor_compare}.py`,
  `report_plugins/{base,kv_analysis,logit_validation,task_analysis,utils}.py`,
  `task_plugins/lm_eval_analyzer.py`, `utils/{api_utils,report_utils}.py`)
  — a new plugin-architected orchestration pipeline over the same
  logit/KV-cache/tensor-compare primitives.
- `vllm_neuron/accuracy/goldens/` (3 files: `fp8_kv_golden.py`,
  `reference_logits.py`, `reference_model.py`) — golden-reference fixtures,
  new.

Backed by new docs (all confirmed new via the 0.21→0.24 file diff, not
present at 0.21): `docs/model-dev/how-to-use-accuracy-debugger.md` and 10
files under `docs/design/accuracy/` (`accuracy_debugging_design.md`,
`dataset_eval_design.md`, `input_snapshot_design.md`,
`kv_cache_analysis_design.md`, `logit_validation_design.md`,
`module_test_guidelines.md`, `tensor_capture_design.md`,
`tensor_compare_design.md`, `tensor_replacement_design.md`, plus 7 PNG
diagrams) and new example scripts under
`examples/vllm_neuron/accuracy/{accuracy_debugger_pipeline.py,run_accuracy_debugger_gpt_oss.py,run_accuracy_debugger_llama.py,server_utils.py}`.
INFERENCE: the accuracy framework matured from "a library of validation
primitives" (0.21) to "a library of primitives plus a driver
pipeline/CLI/plugin system for running them" (0.24) — the underlying
pass/fail math (BC ≥ 0.99, top-k thresholds) appears unchanged; not
confirmed by reading `accuracy_debugger/api.py` itself.

**New `vllm_neuron/snapshot/` subpackage** (`__init__.py`, `capture.py`,
`config.py`, `context.py`, `meta.py`) — file existence only, not read;
plausibly related to the new `input_snapshot_design.md` doc above but not
confirmed to be the same subsystem.

**Public test suite: still zero at the upstream level, at both versions —
this was never a 0.21→0.24 regression, because upstream never shipped
tests.** See §3.0. `.github/workflows/` is byte-identical between 0.21 and
0.24 (`diff` on both `.yml` files: identical) — still only
`acknowledge-new-issue.yml` and `auto-label-issues.yml`, no test-running CI
job exists in the public repo at either version. `pyproject.toml`'s
`testpaths = ["test/unit", "test/vllm_neuron"]` is unchanged text at 0.24,
and **both** referenced paths are now nonexistent upstream (0.21 at least
had `test/unit` if you count the fork's overlay; upstream itself never had
either — this stale-testpaths issue is arguably worse framed at the
upstream level than baseline's framing, which only flagged
`test/vllm_neuron` as missing).

**Coverage-omit glob mismatch persists unchanged.** `pyproject.toml`'s
`[tool.coverage.run] omit` still lists `"vllm_neuron/patches/*"` (not
`"vllm_neuron/vllm/patches/*"`, the real location) — identical stale glob,
confirmed present verbatim in the 0.24 `pyproject.toml` read in full.

### Q4 — Dependency/runtime

**`requirements/core.txt` full diff (upstream 0.21 → upstream 0.24):**
```diff
+ # Neuron torch compile/runtime framework
+ # Left unpinned to resolve based on vLLM's torch requirement
+ libtorch-neuronx-lite
+
- vllm==0.21.0
+ vllm==0.24.0
- nixl
+ nixl==1.3.2
- datasets
- lm-eval[api,ifeval]
```

**"Zero Neuron SDK packages in core.txt" is now FALSE — this directly
contradicts the freeze-replicate venv recipe's grounding assumption.**
`libtorch-neuronx-lite` is a new, unpinned, mandatory core dependency.
Confirmed via WebFetch of `pypi.org/pypi/libtorch-neuronx-lite/json`: the
public-PyPI package is a placeholder ("WRONG PACKAGE. Please install ...
from pip.repos.neuron.amazonaws.com", version 0.0.1) — i.e. resolving
`pip install vllm-neuron==0.24.0.1.1.0` from a plain PyPI index will pull a
non-functional stub for this dependency unless the AWS Neuron private
package index is also configured. **This is the single most consequential
Q4 finding: the freeze-replicate venv recipe must add the Neuron pip index
as an extra-index-url, or the replicated venv will silently get the
placeholder package.** Not confirmed against an actual `pip install` run in
this lens (no network install performed) — flagged as needing a live
verification pass.

`requirements/vllm_rc_test.txt` and `requirements/vllm_build_requirements.txt`
are **byte-identical** between 0.21 and 0.24 (`diff`, confirmed no output).
These two files already listed `torch-neuronx>=2.5.0` and
`neuronx-cc>=2.0.0a0` at 0.21 — but neither file feeds `pyproject.toml`'s
`[tool.setuptools.dynamic]` (which only points at `core.txt` and
`test.txt`), so they were never part of the default install at either
version. Do not confuse these with the `core.txt` change above — the
`core.txt` change is new-in-0.24 and default-installed; these two are
unchanged and CI/build-only at both versions.

`vllm` pin bumped `0.21.0` → `0.24.0`, exact/unconditional at both (no
range). No direct `torch` pin in `core.txt` at either version — torch comes
transitively via vLLM's own dependency, unchanged pattern. `fastapi`
(`<0.137`) and `transformers` (`>=5.5.1,<6.0.0`) pins are byte-identical.
`nixl` went from unpinned to `==1.3.2` — tightened, not loosened.
`datasets` and `lm-eval[api,ifeval]` **removed from `core.txt`**; `datasets`
was already also in `test.txt` at 0.21 (redundant, no functional loss);
`lm-eval[api,ifeval]` is **newly added to `test.txt`** at 0.24 (moved from a
default runtime dependency to a test-only extra) — confirmed via full
`test.txt` diff, which also adds `sentence-transformers`, `httpx`,
`fastapi`, `uvicorn`, `mteb` (new test-only deps, plausibly for the new
Qwen3-Embedding pooling-model support and its own eval harness — INFERENCE,
not confirmed by reading test code).

**Compile-cache mechanics (FileLock-based local cache + atomic path
resolution) are unchanged in the code that stayed in the plugin** — see Q1.
Whether the atomic-rename-on-write detail baseline referenced (not directly
re-quoted in target-plugin.md's excerpt, but implied by "FileLock + atomic
rename") still holds inside the *external* `libtorch-neuronx-lite` package
was **not verified** — that code is no longer in this repo to read.

`VLLM_NEURON_CPU_MODE` / `VLLM_NEURON_CPU_COMPILE` — both still declared
(`envs.py:25-26`) with identical default `False` and the same
mutual-exclusivity contract (INFERENCE from unchanged declaration site;
the `RuntimeError` mutual-exclusion check in `__init__.py`'s `_init_backend`
was not re-read line-by-line at 0.24, only confirmed the two env vars still
exist and resolve the same way). Unchanged from baseline's grounding of the
CPU-first port-increment assumption.

### Q5 — MRv2 exposure at vLLM 0.24 (one release below the v0.25 boundary)

**The plugin already has an explicit, load-bearing guard against vLLM's
V2 model runner, and it fails fast rather than silently.**
`vllm_neuron/vllm/platform.py:287-305` (`_reject_v2_model_runner`, read in
full):

> "Neuron always runs its own v1-style NeuronModelRunner and has no V2 model
> runner. When V2 is enabled, the base scheduler stops sending
> `CachedRequestData.all_token_ids`, which our worker reads — so honoring V2
> would crash mid-decode with a KeyError → EngineDeadError. The plugin's
> `_init_backend` defaults `VLLM_USE_V2_MODEL_RUNNER` to `"0"` so V2 is never
> turned on implicitly; this guard catches the case where a user sets it
> truthy explicitly, failing fast with an actionable message."

Called unconditionally as the first line of `check_and_update_config`
(`platform.py:309`, read in full) — raises `ValueError` if
`VLLM_USE_V2_MODEL_RUNNER` is truthy. The default-off setting is confirmed
at `vllm_neuron/__init__.py:91`:
`os.environ.setdefault("VLLM_USE_V2_MODEL_RUNNER", "0")`.

**External confirmation of the timeline this guard sits inside** (web
search, not primary vllm-neuron evidence):
- vLLM's own blog (`vllm.ai/blog/2026-03-24-mrv2`, March 2026): MRv2
  announced, opt-in via `VLLM_USE_V2_MODEL_RUNNER=1`, "not yet
  feature-complete," "we plan to make MRV2 the default in the near future."
- Third-party technical blog (`saucam.substack.com`, May 2026, cites vLLM's
  own MRv2 design doc as its source for API/structural claims): "shipping
  since v0.17 behind the flag... not the default yet... LoRA, DBO, logits
  processors, and speculative decoding beyond Eagle/Eagle3/MTP remain
  V1-only" as of that writing.
- Third-party comparison article (`markaicode.com`, Aug 2026): **"As of
  vLLM 0.25.0 (July 2026), PagedAttention was removed and superseded by
  Model Runner V2 (MRv2)... now the default execution path for dense
  models."**

Putting the primary evidence and the web evidence together: at the plugin's
new pin (vLLM 0.24.0), MRv2 is upstream-present but opt-in and not default —
the plugin's V1-style `NeuronWorker`/`NeuronModelRunner`/`NeuronScheduler`
architecture (all three confirmed unchanged in shape, Q1) is compatible with
this state, and the plugin has already written defensive code anticipating
incompatibility. If the "PagedAttention removed, MRv2 default" claim for
v0.25 is accurate, then **the fork's current defense (force
`VLLM_USE_V2_MODEL_RUNNER=0`) may stop being sufficient one release past this
one** — not because the flag disappears, but because upstream may delete the
V1 code paths the flag currently preserves as a fallback, which would turn
this from a "fail fast with a clear error" situation into "the fallback
vllm-neuron depends on no longer exists." **This is INFERENCE about a
release the plugin does not yet target — not confirmed against v0.25 source,
which was not cloned in this lens (out of the brief's scope — the brief pins
0.24 as the destination).**

## 4. Contradictions and stale documentation

0. **The 0.21 baseline's evidence base was the fork, not upstream.** See
   §3.0 — `target-plugin.md` §3.11's test-suite finding, and by extension
   any inference resting on "the repo ships tests," describes the fork's
   P-EAGLE branch, not upstream vllm-neuron at either version. Not a defect
   in `target-plugin.md` (its stated scope was the fork), but a scope
   distinction this delta lens had to reconstruct to compare versions
   correctly.

1. **The "centralized patches" stub problem is unresolved and got worse.**
   Baseline flagged `patches/__init__.py`'s `apply_patches()` as an unused,
   aspirational stub. At 0.24 it is still unused (confirmed: not called
   anywhere), its docstring is now *more specific* about exactly where it's
   supposedly called from
   (`NeuronPlatform.check_and_update_config()`) — and that specific claim is
   directly falsifiable by reading `check_and_update_config` itself, which
   calls neither `apply_patches` nor anything from that module. A 4th ad hoc
   monkeypatch (`pin_memory_patch.py`) was added via the same
   non-centralized pattern the stub claims to replace.

2. **Coverage-omit glob mismatch persists verbatim.**
   `pyproject.toml`'s `omit = [..., "vllm_neuron/patches/*", ...]` still
   doesn't match the real `vllm_neuron/vllm/patches/*` path, unchanged
   between 0.21 and 0.24 (confirmed via full `pyproject.toml` read at both
   versions — identical text).

3. **`testpaths` stale reference persists and both entries are now
   nonexistent upstream.** `test/unit` and `test/vllm_neuron` are both
   absent from the tracked tree at 0.24 (upstream ships no `test/` at all —
   see §3.0). Baseline's framing ("`test/vllm_neuron` doesn't exist") should
   be read at the upstream level as "neither configured testpath exists,
   at either version" — this was not a regression, it was already true.

4. **New design docs describe removed code as if it still ships in-repo.**
   `docs/design/compilation/fx_passes_design.md` documents a
   `vllm_neuron/fx_passes/` file tree (8 named files, with class-name
   comments) that does not exist anywhere in the 0.24 git tree — confirmed
   via `git ls-tree` and a whole-repo class-name grep returning zero hits.
   This is a stronger version of baseline's "aspirational docstring"
   pattern: an entire *design document*, newly added at 0.24, describing a
   subsystem that was deleted from the same release. Likely explanation
   (INFERENCE, not confirmed): the design doc predates the migration of
   this code to the external SDK package and wasn't updated when the
   migration happened.

5. **Qwen3-VL segmented-prefill status reads as a flip from ✅ (0.21,
   stated "supported by both" models) to ❌ (0.24, explicit table row) —
   flagged as a notable delta, not confirmed against scheduler code.** Could
   be: (a) a genuine regression traded off for the new vision-encoder
   disaggregation architecture, (b) a doc-table oversight in the 0.24
   per-model-recipe rewrite, or (c) a real product decision. Not
   resolved — needs a targeted look at `NeuronScheduler` + Qwen3-VL model
   code, or a direct question to the plugin maintainers, before the parity
   backlog treats this either as "new gap" or "doc noise."

6. **Feature-matrix doc restructuring dropped several ❌ rows rather than
   flipping them.** GPT-OSS's 0.21 ❌ rows (FP8 static, MXFP8, multimodal)
   and Qwen3-VL's 0.21 ❌ rows (EAGLE3, MXFP4, KV-cache-FP8) have no
   corresponding row in the 0.24 per-model tables at all. Treat every
   "gap closed" claim in §3/Q2 above as grounded only where a 0.24 row
   explicitly states ✅ for that exact feature name — absence of a row is
   not evidence of either status.

## 5. Graph implications

(Observations only — no complete graph proposed, per role constraints.)

- **The high-collision surfaces target-plugin.md identified
  (`platform.py`, `neuron_model_runner.py`) are not just unchanged — they
  grew, and `neuron_model_runner.py` gained a second base-class mixin
  (`NeuronECConnectorModelRunnerMixin`).** Any conflict-aware scheduling
  logic built on "these two files are near-certain overlap points" from the
  0.21 lens carries forward unchanged to 0.24, with slightly higher stakes
  given the added EC-connector coupling.
- **The freeze-replicate venv recipe needs a preflight check before the
  version bump lands.** The `libtorch-neuronx-lite` addition to `core.txt`
  means a plain `pip install` from a standard index will resolve a
  non-functional placeholder; this needs either an extra-index-url pointing
  at the Neuron private package index, or verification that the target host
  venv already carries a working `libtorch-neuronx-lite` via some other
  mechanism (e.g. preinstalled in the DLAMI). This is a **new precondition**
  for the campaign's environment-setup step that didn't exist at 0.21.
- **The compile/FX-pass/NKI-compile subsystem exiting the open-source
  plugin package is a scope change for any campaign that expected to port
  or patch compile-time behavior.** If a feature-port campaign's plan
  assumed touching `vllm_neuron/compile/*` or `vllm_neuron/fx_passes/*`
  (as target-plugin.md's campaign-touch table might, by extension, for
  compile-related work), those files no longer exist to touch — the
  equivalent logic is now outside the repo entirely, in a package this
  repo's contributors likely cannot patch directly. Any node in the
  parity graph that assumed "we can patch the FX pass pipeline" needs
  re-scoping or removal.
- **The accuracy-validation surface split into "stable core primitives"
  (unchanged, low risk to touch) vs. "new orchestration layer"
  (`accuracy_debugger/`, `goldens/`, `snapshot/` — all unread internals,
  higher uncertainty).** A validation-machinery node in the graph should
  probably separate these two into different risk tiers rather than
  treating "the accuracy framework" as one uniform surface.
- **Two new monkeypatch/config-guard mechanisms
  (`pin_memory_patch.py`, `_reject_v2_model_runner`) are both defensive
  reactions to upstream vLLM's evolution (privateuse1 hook gaps under
  pooling; MRv2 rollout).** Both are evidence that the plugin team is
  already tracking upstream churn reactively, file-by-file, rather than
  through the (still-unused) centralized patch mechanism. A parity-debt
  node that models "how much of vllm-neuron's patch surface is ad hoc vs.
  centralized" should count 4 mechanisms now, not 3.
- **The MRv2/v0.25 boundary is real and one release past this pin, with a
  concrete named failure mode already documented in-repo
  (`CachedRequestData.all_token_ids` no longer sent under V2 →
  `EngineDeadError`).** This is a much more specific, more actionable
  parity-risk artifact than "MRv2 exists upstream" — any planning node
  about "how much runway does this fork have before the next mandatory
  rewrite" can cite this exact guard and its exact rationale rather than
  inferring risk generically.

## 6. Remaining evidence gaps

1. **`libtorch-neuronx-lite`'s actual contents were not read** — it is not
   in either git tree; PyPI only has a placeholder. The inference that it
   now hosts the removed FX-pass/compile-cache-remote-tier code is
   well-supported (removal + comment + private-index redirect all point the
   same way) but not confirmed by reading real package contents. Needs
   either AWS Neuron private-index access or asking someone who has
   installed it.
2. **The "PagedAttention removed / MRv2 default at v0.25.0" claim rests on
   one third-party article** (`markaicode.com`), not vLLM's own release
   notes or changelog for v0.25. The brief's own framing ("one release
   above the pin") suggests this is already a working assumption for the
   run, but this lens did not independently verify it against vLLM's
   official v0.25.0 release notes or changelog — worth a direct fetch of
   `github.com/vllm-project/vllm/releases/tag/v0.25.0` before treating it
   as settled.
3. **Qwen3-VL segmented-prefill ✅→❌ flip (§4 item 5) is doc-only evidence**
   — no scheduler or model code was read to confirm whether this is a real
   capability regression or a documentation artifact of the matrix→recipe
   restructuring.
4. **New subpackage internals not read at all:** `accuracy_debugger/*`,
   `goldens/*`, `snapshot/*`, `ec_connector/*`, `disaggregated_encoder/*`,
   `qwen3/*`, `qwen3_vl/*_mxfp8*`. File existence, names, and sizes only.
   Any campaign targeting these needs its own dedicated read-through before
   scoping.
5. **`execute_model`'s ~400+ line body (5293→5717) and the rest of the
   ~9000-line `NeuronModelRunner` class were not read**, same triage limit
   as baseline — still cannot confirm how spec-decode/quant/multimodal/DCP
   branching is organized internally, now with an added EC-connector mixin
   whose interaction with that branching is unexamined.
6. **Neuron SDK 2.32's new `neuron-framework-autoport-vllm-neuron` skill**
   (mentioned in the Neuron 2.32 release notes web source) is adjacent to
   this run's own port-implementer/port-reviewer skill work but was not
   investigated — unclear whether it's a competing tool, a prerequisite, or
   irrelevant to this campaign. Flagging for the lead to route to whichever
   lens owns tooling landscape, not resolved here.
7. **The "vLLM Neuron, launched in July 2026" phrasing in the SourceForge
   mirror of the Neuron 2.32 release notes is inconsistent with the branch
   history** (`git ls-remote` shows release branches back to `0.2.x`,
   long predating July 2026) — treated as unreliable marketing copy on a
   third-party mirror, not cited as fact anywhere above. Flagging so a
   later lens doesn't accidentally inherit it.
8. **DCP-validation consolidation (`_validate_dcp_config` replacing four
   0.21 methods) was not confirmed behaviorally equivalent** — only that
   the method *names* changed. If any campaign depends on the exact 0.21
   DCP validation branches, re-read this method before relying on it.
