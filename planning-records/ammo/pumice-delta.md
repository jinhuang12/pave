# Pumice delta: old AMMO pin to new pin

Evidence record for the ammo plugin port. Other workers read this file instead of re-deriving the delta.
Scope is what the AMMO scripts depend on. It is not a full change log of Pumice.

How to read this file

- Old pin is `9d126713c03dcc88883b85c3d3a857b89982c957`. New pin is `769c752995b8a39b27df028176411d17c99ff441` (origin/mainline).
- Every command ran read-only in `/Users/jinhun/AWSMantle/src/AWSMantlePumice` under bash. No checkout, reset, pull, or branch change happened.
- Output is quoted as printed. It can hold em dashes that come from Pumice source comments.
- In zsh, write `${REF}:path` and not `$REF:path`. zsh reads `:r`, `:s`, `:e` after a variable as a modifier and drops text.
- AMMO line numbers in sections 6 and 8 come from the working tree of `/Users/jinhun/GitHub/ammo-server` at its HEAD `3e2b2c63`.
- AMMO line numbers in section 7 are copied from the touchpoints file, written on 2026-10-01 against AMMO `44674a3d`.
  The AMMO scripts have 6 later commits, so AMMO lines in section 7 can be off by a few.
  The Pumice side of section 7 was checked fresh.

## Summary

Answer: moving AMMO from `9d126713` to `769c7529` needs eight small edits and no proto work. Section 8 lists them. Next action for readers: use sections 4, 5, and 6 for the values, and section 7 for per-row verdicts.

- The old pin is a local stack of two commits on top of mainline commit `668dbdc8`. It is not an ancestor of the new pin. The delta is 145 upstream commits plus the loss of two local fixes for DeepSeek-V4 and DSA paths (1.2).
- The proto is unchanged, so `scripts/pumice_pb` needs no regeneration (3).
- vLLM goes from 0.30.0 to 0.32.0 at all six Pumice sites. torch stays 2.13.0 (`pyproject.toml:127`). The Pumice vLLM version check only warns (4.1, 4.2).
- `rust_grpc_server.py` gains six flags and loses none. Every flag the bench runner emits still exists. `PUMICE_*` names go from 134 to 144 (ten added, none removed) (5, 6).
- gpt-oss uses the `vllm` runner from confdb on sm90, sm100, and sm120. There is no `sm80` row, so L40S needs AMMO's patch, which still applies (4.4).
- Of 92 touchpoint rows under `scripts/`, 67 are unchanged, 21 only moved lines, and 4 changed in substance (7).

## 1. Commit range

The old pin is not an ancestor of the new pin. Read section 1.2 before you trust a two-dot range.

### 1.1 Range size and the 15 newest subjects

```
$ git log --oneline 9d126713..769c7529 | wc -l
     145
```

```
$ git log --oneline -15 9d126713..769c7529
769c7529 Pick measured Kimi-K3.1 MoE kernels for decode-sized calls
cb3e8576 Decide vLLM's breakable-graph eager breaks at call time
1e7fa570 Fold small Model Runner V2 decode step-tail launches
07597437 Compute the Kimi-K3.1 indexer projections in the MLA input GEMMs
b455b6f9 Fold the Kimi-K3.1 indexer key and query prep into two Triton kernels
64df8675 Split the Kimi-K3.1 indexer prefill rows across the TP ranks
fd7c795a Request wheel for small-pool SSD retention correction
7d92513e Scale bounded SSD batches for small CPU caches
2dbcbbf7 Request production wheel for bounded SSD eviction-ahead caching
db402135 Request validation wheel for SSD retention correction
5dabe514 Preserve warm cache coverage with bounded SSD spill batches
c4925869 Refresh validation wheel request after SSD admission review
93d4611f Keep background SSD completion out of CPU admission decisions
8f831aa1 Request validation wheel for bounded SSD eviction-ahead caching
948d109c Keep SSD spills off foreground DRAM cache ownership
```

```
$ git diff --shortstat 9d126713 769c7529
 326 files changed, 109141 insertions(+), 2776 deletions(-)
```

```
$ git diff --dirstat=files,3 9d126713 769c7529
   5.2% docs/fmr/
   7.6% docs/
   4.2% extensions/pumice_vllm_ext/models/fmr/
   4.6% extensions/pumice_vllm_ext/models/kimi_k3_specialized/specialized_decode/cutedsl/moe/
   8.5% extensions/pumice_vllm_ext/models/kimi_k3_specialized/specialized_decode/cutedsl/
   3.9% extensions/pumice_vllm_ext/models/kimi_k3_specialized/tokenspeed_mla/
   4.6% extensions/pumice_vllm_ext/models/kimi_k3_specialized/
   4.9% extensions/pumice_vllm_ext/models/
   3.9% extensions/pumice_vllm_ext/patches/
  15.6% pumice-rust/src/
   3.0% src/pumice/engine/
   3.0% src/pumice/vllm_adapter/
  18.0% tests/
```

### 1.2 The old pin is a local stack, not a mainline commit

The old pin sits on two local commits that no branch or remote ref in this clone holds. The new pin does not hold them.
The 145 commits are upstream work after the merge base. The diff `old..new` also reverses the two local commits.

```
$ git rev-parse 9d126713 769c7529 origin/mainline
9d126713c03dcc88883b85c3d3a857b89982c957
769c752995b8a39b27df028176411d17c99ff441
769c752995b8a39b27df028176411d17c99ff441
```

```
$ git merge-base --is-ancestor 9d126713 769c7529; echo "is-ancestor exit=$?  (0 = yes, 1 = no)"
is-ancestor exit=1  (0 = yes, 1 = no)
```

```
$ git merge-base 9d126713 769c7529
668dbdc8450032686150c2d9c1124a082924bdaa
```

```
$ git rev-list --count 668dbdc8..769c7529
145
```

```
$ git log --format='%h %ad(author) %cd(commit) %s' --date=short 769c7529..9d126713
9d126713 2026-07-29(author) 2026-10-01(commit) Populate DSV4 HMA metadata in execute_dummy_batch for idle DP ranks
25188505 2026-07-29(author) 2026-10-01(commit) Fix DSA warmup workspace arg order in reserve_dsa_peak_for_profiling
```

```
$ git show --numstat --format='%h %s' 9d126713 25188505 | cat
9d126713 Populate DSV4 HMA metadata in execute_dummy_batch for idle DP ranks

30	0	src/pumice/engine/model_runner.py
25188505 Fix DSA warmup workspace arg order in reserve_dsa_peak_for_profiling

9	1	src/pumice/engine/warmup.py
```

```
$ git log --oneline 769c7529 --grep='Populate DSV4 HMA metadata' --grep='Fix DSA warmup workspace' | wc -l
       0
```

```
$ for c in 9d126713 769c7529; do echo "$c: $(git show ${c}:src/pumice/engine/model_runner.py | grep -c 'DSV4/HMA sentinel metadata') hits"; done
9d126713: 1 hits
769c7529: 0 hits
```

```
$ git diff 9d126713 769c7529 -- src/pumice/engine/warmup.py
diff --git a/src/pumice/engine/warmup.py b/src/pumice/engine/warmup.py
index 4bc1a884..a8e84041 100644
--- a/src/pumice/engine/warmup.py
+++ b/src/pumice/engine/warmup.py
@@ -375,15 +375,7 @@ def reserve_dsa_peak_for_profiling(runner):
     from pumice.layers.sparse_attn_indexer import _get_shared_prefill_workspace
     from pumice.layers.mla_paged import _get_mla_workspace
     max_total_seq = config.max_model_len * 40
-    # Signature is (max_total_seq, head_dim, device, use_fp4_cache) — match the
-    # runtime caller in sparse_attn_indexer.py. Passing n_groups here put it in
-    # the `device` slot (torch.empty(..., device=1)) → "invalid device ordinal"
-    # under per-rank single-GPU pinning (CUDA_VISIBLE_DEVICES=<one gpu>).
-    # use_fp4_indexer_cache is the confdb-resolved flag the runtime indexer
-    # uses, so the reserved workspace shape matches the real allocation.
-    _get_shared_prefill_workspace(
-        max_total_seq, head_dim, device, config.use_fp4_indexer_cache
-    )
+    _get_shared_prefill_workspace(max_total_seq, head_dim, n_groups, device)
     _get_mla_workspace()
 
     # 3: transient peak. Worst-case prefill chunk: M query tokens against
```

```
$ git for-each-ref --contains 9d126713 | wc -l
       0
```

```
$ git reflog --all | grep 9d126713 | head -3
9d126713 refs/heads/mainline@{2}: pull --rebase (finish): refs/heads/mainline onto 668dbdc8450032686150c2d9c1124a082924bdaa
9d126713 HEAD@{9}: pull --rebase (finish): returning to refs/heads/mainline
9d126713 HEAD@{10}: pull --rebase (pick): Populate DSV4 HMA metadata in execute_dummy_batch for idle DP ranks
```

Meaning:

- `668dbdc8` is on origin/mainline. The old pin is that commit plus two local commits made on 2026-07-29 and rebased onto it.
- Local commit one adds a block to `execute_dummy_batch` in `model_runner.py` (DeepSeek-V4 HMA metadata for idle DP ranks, 30 lines).
- Local commit two changes the `_get_shared_prefill_workspace` call in `warmup.py` (DSA warmup argument order, 9 lines added, 1 removed).
- Neither fix is in the new pin. No commit with either subject exists in its history. The HMA block is absent from the new `model_runner.py`, and the new `warmup.py` has the old call again (outputs above). The commit messages name DeepSeek-V4 and DSA paths.
- Section 2.4 marks the `model_runner.py` hunks that come from the first local commit. The `config.py`, `rust_grpc_server.py`, and `resolve.rs` diffs are upstream only (proof below).

```
$ for f in src/pumice/config.py rust_grpc_server.py pumice-rust/src/confdb/resolve.rs pyproject.toml; do if git diff --quiet 668dbdc8 9d126713 -- $f; then echo "$f: same at merge base and old pin (diff is upstream only)"; else echo "$f: DIFFERS between merge base and old pin"; fi; done
src/pumice/config.py: same at merge base and old pin (diff is upstream only)
rust_grpc_server.py: same at merge base and old pin (diff is upstream only)
pumice-rust/src/confdb/resolve.rs: same at merge base and old pin (diff is upstream only)
pyproject.toml: same at merge base and old pin (diff is upstream only)
```


The Pumice checkout on disk is on neither pin. Its `mainline` branch is the two local fixes rebased on an older upstream commit. Do not read pin facts from its working tree. Use `git show 769c7529:<path>`.

```
$ git rev-parse --short HEAD; git merge-base --is-ancestor 769c7529 HEAD; echo "new pin is an ancestor of HEAD: exit=$? (1 = no)"; echo "HEAD only: $(git rev-list --count 769c7529..HEAD) commits, new pin only: $(git rev-list --count HEAD..769c7529) commits"; git range-diff --no-color 668dbdc8..9d126713 769c7529..HEAD | cut -c1-110
64ab152d
new pin is an ancestor of HEAD: exit=1 (1 = no)
HEAD only: 2 commits, new pin only: 96 commits
1:  25188505 = 1:  4ff7904f Fix DSA warmup workspace arg order in reserve_dsa_peak_for_profiling
2:  9d126713 = 2:  64ab152d Populate DSV4 HMA metadata in execute_dummy_batch for idle DP ranks
```

## 2. Diffs of the files AMMO cares about

All seven paths exist at both commits. No rename happened, so no `git log --diff-filter=R` search was needed.

```
$ for p in src/pumice/config.py rust_grpc_server.py src/pumice/compilation/backend.py src/pumice/engine/model_runner.py src/pumice/models/registry.py pumice-rust/src/confdb/resolve.rs pumice-rust/confdb/gpt_oss.toml; do for c in 9d126713 769c7529; do if git cat-file -e "${c}:${p}" 2>/dev/null; then echo "$c $p exists"; else echo "$c $p MISSING"; fi; done; done
9d126713 src/pumice/config.py exists
769c7529 src/pumice/config.py exists
9d126713 rust_grpc_server.py exists
769c7529 rust_grpc_server.py exists
9d126713 src/pumice/compilation/backend.py exists
769c7529 src/pumice/compilation/backend.py exists
9d126713 src/pumice/engine/model_runner.py exists
769c7529 src/pumice/engine/model_runner.py exists
9d126713 src/pumice/models/registry.py exists
769c7529 src/pumice/models/registry.py exists
9d126713 pumice-rust/src/confdb/resolve.rs exists
769c7529 pumice-rust/src/confdb/resolve.rs exists
9d126713 pumice-rust/confdb/gpt_oss.toml exists
769c7529 pumice-rust/confdb/gpt_oss.toml exists
```

```
$ git diff --stat 9d126713 769c7529 -- src/pumice/config.py rust_grpc_server.py src/pumice/compilation/backend.py src/pumice/engine/model_runner.py src/pumice/models/registry.py pumice-rust/src/confdb/resolve.rs pumice-rust/confdb/gpt_oss.toml
 pumice-rust/src/confdb/resolve.rs |  26 ++++++++-
 rust_grpc_server.py               |  27 ++++++++-
 src/pumice/config.py              |  33 ++++++++---
 src/pumice/engine/model_runner.py | 117 ++++++++++++++++----------------------
 4 files changed, 124 insertions(+), 79 deletions(-)
```

Result: three files are byte-identical (`backend.py`, `registry.py`, `gpt_oss.toml`). Four changed.

### 2.1 `src/pumice/config.py` (full diff)

Four changes matter. `REQUIRED_VLLM_VERSION` goes to 0.32.0. One architecture joins the breakable-CUDA-graph set. A new `_normalize_single_local_dp` helper runs in `__post_init__`. Comments now say CLI capture sizes override confdb.
The new helper returns at once unless `data_parallel_size_local` is 1. That field defaults to 0 (`config.py:377`). Only the Rust binary has a flag for it (`main.rs:433`, present at the old pin too). AMMO never passes it, so AMMO DP launches skip the helper:

```
$ git show 769c7529:src/pumice/config.py | sed -n '31,34p;377p'; git show 769c7529:pumice-rust/src/main.rs | sed -n 433p; echo "rust_grpc_server.py hits: $(git show 769c7529:rust_grpc_server.py | grep -c 'data-parallel-size-local')"; echo "AMMO hits: $(grep -rn -e 'data-parallel-size-local' -e 'data_parallel_size_local' /Users/jinhun/GitHub/ammo-server/ai_cli_session /Users/jinhun/GitHub/ammo-server/shared | wc -l | tr -d ' ')"
def _normalize_single_local_dp(config) -> None:
    """Map a one-leaf window to its global DP rank and host-local GPU group."""
    if config.data_parallel_size <= 1 or config.data_parallel_size_local != 1:
        return
    data_parallel_size_local: int = 0
            "--data-parallel-size-local" => {
rust_grpc_server.py hits: 0
AMMO hits: 0
```


```
$ git diff 9d126713 769c7529 -- src/pumice/config.py
diff --git a/src/pumice/config.py b/src/pumice/config.py
index 6cad4aa8..3a9d4e87 100644
--- a/src/pumice/config.py
+++ b/src/pumice/config.py
@@ -27,6 +27,19 @@ def _apply_prefix_caching_override(config) -> None:
         config.prefix_caching,
     )
 
+
+def _normalize_single_local_dp(config) -> None:
+    """Map a one-leaf window to its global DP rank and host-local GPU group."""
+    if config.data_parallel_size <= 1 or config.data_parallel_size_local != 1:
+        return
+    start = config.data_parallel_start_rank
+    if start:
+        if config.data_parallel_rank not in (0, start):
+            raise ValueError("single-leaf DP rank conflicts with data_parallel_start_rank")
+        config.data_parallel_rank = start
+    if config.data_parallel_rank_local is None:
+        config.data_parallel_rank_local = int(os.environ.get("VLLM_DP_RANK_LOCAL", "0"))
+
 # Accepted --dtype spellings (vLLM contract; "auto" handled separately).
 _STR_TO_TORCH_DTYPE = {
     "float16": torch.float16,
@@ -45,6 +58,7 @@ _BREAKABLE_CUDAGRAPH_ARCHITECTURES = frozenset(
     {
         "DeepseekV4ForCausalLM",
         "DeepSeekV4MTPModel",
+        "KimiK31ForConditionalGeneration",
         "KimiK3ForConditionalGeneration",
         "KimiLinearForCausalLM",
         "MiniMaxM3SparseForCausalLM",
@@ -69,7 +83,7 @@ MODEL_RUNNER_ENV_VAR = "PUMICE_USE_VLLM_RUNNER"
 # in pyproject.toml and `vllm_version` in pumice_version_info.json); it is the
 # one a grep for `vllm==<version>` misses, because the version appears here as a
 # bare string. tests/test_model_runner_flag.py asserts the lockstep.
-REQUIRED_VLLM_VERSION = "0.30.0"
+REQUIRED_VLLM_VERSION = "0.32.0"
 # Speculative methods whose drafter weights ship INSIDE the target checkpoint, so
 # a launch legitimately has no `--draft-model`. Defined here (the module with no
 # vLLM dependency) so the three K-arithmetic / spec-gate sites share ONE list:
@@ -340,8 +354,8 @@ class Config:
     # fields drive the torch.distributed world formation.
     data_parallel_size: int = 1
     data_parallel_rank: int = 0
-    # Host-local DP GPU index. Native vLLM adds this offset to its TP-local
-    # worker rank; the supervisor must not also apply a one-GPU visibility mask.
+    # Host-local DP rank. Native vLLM selects TP_LOCAL_RANK + DP_LOCAL_RANK * TP;
+    # the supervisor must not also offset ranks or apply a one-GPU visibility mask.
     data_parallel_rank_local: int | None = None
     # Coordinator (DP rank 0) host. Other ranks point their torch.distributed
     # TCP store here. Defaults to localhost for single-node dp.
@@ -479,8 +493,9 @@ class Config:
     # vLLM-runner EngineArgs.load_format (PR-S1). K3 pins "fastsafetensors".
     # None ⇒ vLLM's default loader.
     vllm_load_format: str | None = None
-    # vLLM-runner EngineArgs.enable_flashinfer_autotune. None preserves vLLM's
-    # optimization-level default; K3 pins False to match its container recipe.
+    # vLLM-runner EngineArgs.enable_flashinfer_autotune. None uses confdb, then
+    # vLLM's default if absent. Explicit True/False override confdb; K3's
+    # unchanged confdb default is False.
     vllm_enable_flashinfer_autotune: bool | None = None
     # vLLM-runner CompilationConfig.cudagraph_mode. None preserves vLLM's
     # model/platform default; explicit values are validated by vLLM.
@@ -629,7 +644,7 @@ class Config:
     cudagraph_mode: str = "full_and_piecewise"
 
     # Optional model-specific override for max PIECEWISE capture token count,
-    # populated from confdb when ``max_cudagraph_capture_size`` is set in the
+    # populated from CLI or confdb when ``max_cudagraph_capture_size`` is set in the
     # matching ``[smXX]`` section. Mirrors vLLM's
     # ``GptOssForCausalLMConfig.verify_and_update_config`` path
     # (vllm/model_executor/models/config.py:159-172) where gpt-oss bumps the
@@ -638,8 +653,9 @@ class Config:
     # ``None`` means "use the default formula"
     # (``min(max_num_seqs * (1 + num_speculative_tokens) * 2, 512)``).
     max_cudagraph_capture_size: int | None = None
-    # Explicit CUDA-graph capture grid from confdb ``cudagraph_capture_sizes``
-    # (vllm runner only); ``None`` lets vLLM derive its list from the max.
+    # Explicit CUDA-graph token grid from CLI or confdb ``cudagraph_capture_sizes``
+    # (vllm runner only). An explicit list/ceiling supersedes model capture
+    # defaults; ``None`` lets vLLM derive its list from the max.
     cudagraph_capture_sizes: list[int] | None = None
     use_breakable_cudagraph: bool = False
     reorder_batch_threshold: int | None = None
@@ -790,6 +806,7 @@ class Config:
         )
         assert 1 <= self.data_parallel_size <= 64, (
             f"data_parallel_size must be in [1, 64], got {self.data_parallel_size}")
+        _normalize_single_local_dp(self)
         assert 0 <= self.data_parallel_rank < self.data_parallel_size, (
             f"data_parallel_rank ({self.data_parallel_rank}) must be in "
             f"[0, data_parallel_size={self.data_parallel_size})")
```

### 2.2 `rust_grpc_server.py` (full diff)

Six flags are added and one choice list grows. No flag is removed. No default of an existing flag changes.

```
$ git diff 9d126713 769c7529 -- rust_grpc_server.py
diff --git a/rust_grpc_server.py b/rust_grpc_server.py
index cfb8f161..7629fa7d 100644
--- a/rust_grpc_server.py
+++ b/rust_grpc_server.py
@@ -30,6 +30,10 @@ def parse_args():
     # default in pumice-rust/src/config.rs. Required when chunked prefill
     # is enabled (the default).
     p.add_argument("--max-num-batched-tokens", type=int, default=8192)
+    p.add_argument("--cudagraph-capture-sizes", type=int, nargs="+", default=None,
+                   help="Native vLLM CUDA graph token sizes; overrides model capture defaults.")
+    p.add_argument("--max-cudagraph-capture-size", type=int, default=None,
+                   help="Native vLLM capture ceiling; must agree with an explicit size list.")
     p.add_argument("--tensor-parallel-size", type=int, default=1)
     p.add_argument("--nnodes", type=int, default=1)
     p.add_argument("--node-rank", type=int, default=0)
@@ -145,6 +149,16 @@ def parse_args():
     p.add_argument("--language-model-only", action="store_true")
     p.add_argument("--mm-encoder-tp-mode", choices=("weights", "data"), default="weights")
     p.add_argument("--vllm-moe-backend", default=None)
+    p.add_argument("--vllm-attention-backend", default=None,
+                   help="Native vLLM target AttentionConfig.backend; unset preserves confdb/auto.")
+    p.add_argument("--vllm-spec-attention-backend", default=None,
+                   help="Native vLLM draft SpeculativeConfig.attention_backend.")
+    p.add_argument("--vllm-attention-config", default=None,
+                   help="Native vLLM AttentionConfig JSON object; explicit JSON keys win.")
+    p.add_argument("--enable-flashinfer-autotune",
+                   action=argparse.BooleanOptionalAction, default=None,
+                   dest="vllm_enable_flashinfer_autotune",
+                   help="Override vLLM FlashInfer autotuning; unset preserves confdb.")
     p.add_argument("--kv-connector-mode", type=str, default="both")
     p.add_argument("--kv-connector-engine-id", type=str, default=None)
     # -- CPU KV-cache offloading (docs/designs/kv-offloading.md §11) --
@@ -159,8 +173,9 @@ def parse_args():
                    help="Offloaded block size = gpu_block_size * factor. "
                         "Only 1 is supported today.")
     p.add_argument("--kv-offload-eviction-policy", type=str, default="lru",
-                   choices=["lru"],
-                   help="CPU tier eviction policy ('arc' reserved).")
+                   choices=["lru", "qos_lru"],
+                   help="DRAM/SSD eviction: LRU (default), or lowest QoS "
+                        "first with LRU ties.")
     p.add_argument("--kv-offload-cache-policy", type=str, default="spillover",
                    choices=["spillover", "explicit", "epc"],
                    help="Store policy: GPU-pressure spillover or explicit "
@@ -263,6 +278,10 @@ def main():
         # / EP off these are inert defaults, so always passing them is safe.
         "enable_expert_parallel": args.enable_expert_parallel,
         "vllm_moe_backend": args.vllm_moe_backend,
+        "vllm_attention_backend": args.vllm_attention_backend,
+        "vllm_spec_attention_backend": args.vllm_spec_attention_backend,
+        "vllm_attention_config": args.vllm_attention_config,
+        "vllm_enable_flashinfer_autotune": args.vllm_enable_flashinfer_autotune,
         "data_parallel_size": args.data_parallel_size,
         "data_parallel_rank": args.data_parallel_rank,
         "data_parallel_address": args.data_parallel_address,
@@ -275,6 +294,10 @@ def main():
         config["model_runner"] = args.model_runner
     if args.max_num_batched_tokens is not None:
         config["max_num_batched_tokens"] = args.max_num_batched_tokens
+    if args.cudagraph_capture_sizes is not None:
+        config["cudagraph_capture_sizes"] = args.cudagraph_capture_sizes
+    if args.max_cudagraph_capture_size is not None:
+        config["max_cudagraph_capture_size"] = args.max_cudagraph_capture_size
     if args.gpu_memory_utilization is not None:
         config["gpu_memory_utilization"] = args.gpu_memory_utilization
     if args.num_gpu_blocks is not None:
```

### 2.3 `src/pumice/compilation/backend.py`

Identical at both commits. It has not changed since 2026-07-23.

```
$ git diff --exit-code 9d126713 769c7529 -- src/pumice/compilation/backend.py; echo "exit=$?"
exit=0
```

```
$ git log -2 --format='%h %ad %s' --date=short 769c7529 -- src/pumice/compilation/backend.py
4b870f96 2026-07-23 feat: consolidate vLLM 0.25 model runtime parity
bbc9b400 2026-07-13 feat(deepseekv4): DeepSeek-V4-Flash serving on Pumice (paged MLA + FlashMLA sparse + fused MoE + PD-disagg scaffolding)
```

AMMO reference claims that rest on this file still hold. `torch-compile-contract.md:154` says `PumiceBackend.splitting_ops` holds `pumice::unified_attention` and `pumice::glm_mla_attention`:

```
$ git show 769c7529:src/pumice/compilation/backend.py | sed -n 1460,1468p
        self.splitting_ops = [
            "pumice::unified_attention",
            # GLM-5.2 DSA: the indexer (deep_gemm) + sparse MLA attend
            # (flashinfer) run eagerly inside this op; the splitter cuts the
            # graph here so inductor fuses the projections / norms / MoE glue
            # between the eager attention islands. Inert for non-GLM models
            # (the op never appears in their traced graphs).
            "pumice::glm_mla_attention",
        ]
```

The file is identical, so no env name in it is new. The names it reads:

```
$ git show 769c7529:src/pumice/compilation/backend.py | grep -o -E 'PUMICE_[A-Z_]+' | sort -u | tr '\n' ' '
PUMICE_CACHE_ROOT PUMICE_DATA_PARALLEL_RANK PUMICE_DISABLE_COMPILE_CACHE PUMICE_INDUCTOR_GRAPH_PARTITION PUMICE_LOG_LEVEL PUMICE_MAX_NUM_BATCHED_TOKENS PUMICE_PARTITION_PATCH 
```

### 2.4 `src/pumice/engine/model_runner.py` (summary)

No hunk touches a CLI flag, an env var, a default value, or the model-runner choice. The runner choice lives in `config.py`, `models/model_config.py`, and `async_llm.rs`, not here.

Proof: the changed lines hold no `environ`, `PUMICE_`, `VLLM_`, `add_argument`, `model_runner_kind`, or flag text. The one match is a comment.

```
$ git diff 9d126713 769c7529 -- src/pumice/engine/model_runner.py | grep -E '^[+-]' | grep -v -E '^(\+\+\+|---)' | grep -n -i -E 'environ|PUMICE_|VLLM_|add_argument|model_runner_kind|"vllm"|--[a-z]+-[a-z]+|default'
58:+            # Scope the barrier to this engine's TP ranks. Under DP the default
```

The 30 lines removed in the first two hunks are not upstream work. They undo the local commit `9d126713`:

```
$ echo "old..new   : $(git diff --numstat 9d126713 769c7529 -- src/pumice/engine/model_runner.py)"; echo "merge base..new (upstream only): $(git diff --numstat 668dbdc8 769c7529 -- src/pumice/engine/model_runner.py)"; echo "local commit  : $(git show --numstat --format= 9d126713 -- src/pumice/engine/model_runner.py)"
old..new   : 49	68	src/pumice/engine/model_runner.py
merge base..new (upstream only): 49	38	src/pumice/engine/model_runner.py
local commit  : 30	0	src/pumice/engine/model_runner.py
```

Hunk list (`git diff -U0`) with what each does:

```
$ git diff -U0 9d126713 769c7529 -- src/pumice/engine/model_runner.py | grep '^@@' | cut -c1-90
@@ -3811,29 +3810,0 @@ class ModelRunner:
@@ -3856 +3826,0 @@ class ModelRunner:
@@ -4442,12 +4411,0 @@ class ModelRunner:
@@ -4616,4 +4574,4 @@ class ModelRunner:
@@ -4621,4 +4578,0 @@ class ModelRunner:
@@ -4632 +4586 @@ class ModelRunner:
@@ -5318,0 +5273,7 @@ class ModelRunner:
@@ -5322 +5283,6 @@ class ModelRunner:
@@ -5615,3 +5581,3 @@ class ModelRunner:
@@ -5627,0 +5594,5 @@ class ModelRunner:
@@ -5632,3 +5602,0 @@ class ModelRunner:
@@ -5702,0 +5671 @@ class ModelRunner:
@@ -5714,0 +5684 @@ class ModelRunner:
@@ -5717 +5687 @@ class ModelRunner:
@@ -5725,0 +5696 @@ class ModelRunner:
@@ -5733,4 +5704,6 @@ class ModelRunner:
@@ -5740 +5713,4 @@ class ModelRunner:
@@ -5749 +5725,2 @@ class ModelRunner:
@@ -5751 +5728,2 @@ class ModelRunner:
@@ -5753 +5731,4 @@ class ModelRunner:
@@ -5758 +5739 @@ class ModelRunner:
```

| Old-side lines | What changes | Source of change |
|---|---|---|
| 3811, 3856 | Idle dummy forward no longer fills DSV4/HMA sentinel metadata or passes `hma_window_ctx` | reverses local commit `9d126713` |
| 4442 | NIXL descriptor builder drops the total-KV-head count used for GQA replication | upstream |
| 4616-4632 | NIXL region `replicated` flag is now `is_mla` only (GQA always uses head mapping) | upstream |
| 5318-5322 | KV-offload poll all-reduce uses the TP subgroup when one exists | upstream |
| 5615-5632 | KV-offload tick: deferred stores and flush run before new loads | upstream |
| 5702-5758 | SSD tick takes `speculative_job_ids`; status lanes use a 4-value priority | upstream |

AMMO reference claims that rest on this file still hold. `torch-compile-contract.md:173` says `compile_ranges` defaults to one `Range(1, max_num_batched_tokens)`. `torch-compile-contract.md:111` says it sets `enable_auto_functionalized_v2` to False. No hunk mentions either:

```
$ git show 769c7529:src/pumice/engine/model_runner.py | grep -n -E 'compile_ranges =|_compile_ranges = |enable_auto_functionalized_v2' | head -6
1273:                "enable_auto_functionalized_v2": False,
1357:            _compile_ranges = [Range(start=1, end=_max_tok)]
1381:                    _compile_ranges = [
```

```
$ git diff 9d126713 769c7529 -- src/pumice/engine/model_runner.py | grep -c -E 'compile_ranges|enable_auto_functionalized_v2|splitting_ops'; true
0
```

### 2.5 `src/pumice/models/registry.py`

Identical at both commits. The architecture table (`_PUMICE_MODELS`) is unchanged, so `workload-invocation.md:32` still holds.

```
$ git diff --exit-code 9d126713 769c7529 -- src/pumice/models/registry.py; echo "exit=$?"
exit=0
```

```
$ git log -1 --format='%h %ad %s' --date=short 769c7529 -- src/pumice/models/registry.py
de8b3e0b 2026-07-24 Add Gemma3 embedding model support (EmbeddingGemma)
```

### 2.6 `pumice-rust/src/confdb/resolve.rs` (full diff, it is short)

Every hunk is inside `mod tests`. No production line changes. The test list of vLLM-defaulted architectures gains `FMRForCausalLM`, and one new test covers FMR on Blackwell.

```
$ git diff 9d126713 769c7529 -- pumice-rust/src/confdb/resolve.rs
diff --git a/pumice-rust/src/confdb/resolve.rs b/pumice-rust/src/confdb/resolve.rs
index 51bc58eb..701528b5 100644
--- a/pumice-rust/src/confdb/resolve.rs
+++ b/pumice-rust/src/confdb/resolve.rs
@@ -495,10 +495,11 @@ mod tests {
         use std::collections::HashMap;
 
         // (architectures, hf attrs needed to satisfy any `condition =` gate)
-        let vllm_defaulted: [(&str, Vec<(&str, &str)>); 7] = [
+        let vllm_defaulted: [(&str, Vec<(&str, &str)>); 8] = [
             ("Gemma4ForConditionalGeneration", vec![]),
             ("Gemma4ForCausalLM", vec![]),
             ("GptOssForCausalLM", vec![]),
+            ("FMRForCausalLM", vec![]),
             ("GlmMoeDsaForCausalLM", vec![]),
             // nemotron_h is vLLM-runner-ONLY, not merely defaulted: Pumice has
             // no native NemotronHForCausalLM class, so a resolve that left this
@@ -542,6 +543,29 @@ mod tests {
         }
     }
 
+    #[test]
+    fn fmr_blackwell_resolves_nixl_attention_layout_by_architecture() {
+        use std::collections::HashMap;
+
+        let hf = HashMap::from([("model_type".to_string(), HfAttr::Str("llama".to_string()))]);
+        for cc in [(10, 0), (10, 3)] {
+            let out = resolve(&["FMRForCausalLM".to_string()], cc, None, &hf);
+            for (key, expected) in [
+                ("model_runner", "vllm"),
+                ("vllm_attention_backend", "flashinfer"),
+                ("kv_cache_layout", "HND"),
+                ("nixl_required_kv_cache_layout", "HND"),
+                ("block_size", "64"),
+            ] {
+                assert_eq!(
+                    out.get(key).map(String::as_str),
+                    Some(expected),
+                    "FMR {cc:?} must resolve {key} without falling back to llama defaults"
+                );
+            }
+        }
+    }
+
     fn extract_unknown_keys_are_dropped() {
         let entry = parse_inline(
             r#"
```

```
$ git diff -U0 9d126713 769c7529 -- pumice-rust/src/confdb/resolve.rs | grep '^@@'; git show 769c7529:pumice-rust/src/confdb/resolve.rs | grep -n 'mod tests'
@@ -498 +498 @@ mod tests {
@@ -501,0 +502 @@ mod tests {
@@ -544,0 +546,23 @@ mod tests {
243:mod tests {
```

### 2.7 `pumice-rust/confdb/gpt_oss.toml`

Identical at both commits. The gpt-oss rows are the same. Other confdb files did change:

```
$ git diff --exit-code 9d126713 769c7529 -- pumice-rust/confdb/gpt_oss.toml; echo "exit=$?"
exit=0
```

```
$ git diff --stat 9d126713 769c7529 -- pumice-rust/confdb | cat
 pumice-rust/confdb/fmr.toml         | 20 +++++++++++++++
 pumice-rust/confdb/glm_moe_dsa.toml |  5 ++++
 pumice-rust/confdb/kimi_k3.toml     |  9 ++++---
 pumice-rust/confdb/kimi_k31.toml    | 49 +++++++++++++++++++++++++++++++++++++
 4 files changed, 79 insertions(+), 4 deletions(-)
```

New or changed rows in other files (not gpt-oss): `fmr.toml` is new (sm100, runner vllm). `kimi_k31.toml` is new. `kimi_k3.toml` has comment changes only. `glm_moe_dsa.toml` adds `vllm_load_format = "fastsafetensors"`.

### 2.8 Neighbours that decide runner and defaults

Defaults and CLI handling changed in files outside the seven asked for. The runner-selection rules did not. AMMO scripts see these changes through the CLI and launch behaviour.

```
$ git diff --stat 9d126713 769c7529 -- src/pumice/models pumice-rust/src/config.rs pumice-rust/src/main.rs | cat
 pumice-rust/src/config.rs                | 362 ++++++++++++++++++++++++++++++-
 pumice-rust/src/main.rs                  | 340 ++++++++++++++++++++++++++++-
 src/pumice/models/gemma4_layer_config.py |  97 +++++++++
 src/pumice/models/model_config.py        |  63 +++++-
 4 files changed, 845 insertions(+), 17 deletions(-)
```

```
$ git diff -U0 9d126713 769c7529 -- src/pumice/models/model_config.py | grep -E '^[+-]' | grep -v -E '^(\+\+\+|---)' | grep -E 'autotune|cudagraph|kimi|model_runner' 
+        # layers; kimi_k31.toml gates on this to win over kimi_k3.toml. The
+        config.hf_config.pumice_kimi_dsa = any(
-    _apply_max_cudagraph_capture_size(config, settings)
-    _apply_cudagraph_capture_sizes(config, settings)
+    _apply_cudagraph_settings(config, settings)
-        "vllm_enable_flashinfer_autotune",
-        "vllm_enable_flashinfer_autotune",
+    if getattr(config, "vllm_enable_flashinfer_autotune", None) is None:
+            "vllm_enable_flashinfer_autotune",
+            "vllm_enable_flashinfer_autotune",
+def _apply_cudagraph_settings(config, settings: dict) -> None:
+        getattr(config, "cudagraph_capture_sizes", None) is not None
+        or getattr(config, "max_cudagraph_capture_size", None) is not None
+    _apply_max_cudagraph_capture_size(config, settings)
+    _apply_cudagraph_capture_sizes(config, settings)
```

Reading of those lines:

- `--enable-flashinfer-autotune` is tri-state. A CLI value (True or False) now beats confdb. Unset keeps confdb.
- An explicit `--cudagraph-capture-sizes` or `--max-cudagraph-capture-size` beats confdb for both values (`_apply_cudagraph_settings`).
- Kimi-K3.1 is detected by architecture and DSA config. It is not a gpt-oss path.

Runner choice code is unchanged between pins. The count below is the number of changed lines that mention the env var, the kinds list, or `_apply_model_runner`:

```
$ git diff 9d126713 769c7529 -- pumice-rust/src/config.rs src/pumice/models/model_config.py src/pumice/config.py | grep -E '^[+-]' | grep -c -E 'model_runner_kind_default_from_env|MODEL_RUNNER_KINDS|_apply_model_runner|USE_VLLM_RUNNER|MODEL_RUNNER_ENV_VAR'; true
0
```

```
$ git diff --quiet 9d126713 769c7529 -- pumice-rust/src/confdb/schema.rs && echo 'schema.rs: identical at both pins'; git show 769c7529:src/pumice/models/model_config.py | sed -n 455,460p
schema.rs: identical at both pins
    Precedence, lowest to highest:

      1. the dataclass default ("pumice")
      2. `PUMICE_USE_VLLM_RUNNER=1`
      3. this confdb key
      4. an explicit `--model-runner` on the command line
```

## 3. Proto evidence

Both proto trees are empty of change. AMMO does not regenerate `scripts/pumice_pb`.

```
$ git diff --exit-code 9d126713 769c7529 -- pumice-rust/proto src/pumice/grpc; echo "exit=$?"
exit=0
```

```
$ for p in pumice-rust/proto src/pumice/grpc; do echo "$p old=$(git rev-parse 9d126713:$p) new=$(git rev-parse 769c7529:$p)"; done
pumice-rust/proto old=d0019149f4201c5c9d9c9a5e22a3bfc084801e9e new=d0019149f4201c5c9d9c9a5e22a3bfc084801e9e
src/pumice/grpc old=9bf483d67094a49b426fd22c8c3d3b6d255ff32e new=9bf483d67094a49b426fd22c8c3d3b6d255ff32e
```

```
$ git log --oneline 9d126713..769c7529 -- pumice-rust/proto src/pumice/grpc | wc -l
       0
```

The values that `scripts/pumice_pb/README.md:9-10` records still hold:

```
$ for c in 9d126713 769c7529; do echo "$c last-change=$(git log -1 --format=%H $c -- pumice-rust/proto/pumice_engine.proto) sha256=$(git show ${c}:pumice-rust/proto/pumice_engine.proto | shasum -a 256 | cut -d' ' -f1)"; done
9d126713 last-change=1a1bcdbfe1929f00e19e24277b10dd9042a5413b sha256=9b4dbe3de02378403d6d7a231fdc2389a4f6d341b5cb452caa48f2b14ee29f88
769c7529 last-change=1a1bcdbfe1929f00e19e24277b10dd9042a5413b sha256=9b4dbe3de02378403d6d7a231fdc2389a4f6d341b5cb452caa48f2b14ee29f88
```

## 4. Pins at the new pin

### 4.1 vLLM: six sites agree on 0.32.0

`config.py:79-85` names six sites that must agree. The loop prints each at the new pin, then at the old pin.

```
$ for R in 769c7529 9d126713; do echo "== $R"; for spec in "src/pumice/config.py:^REQUIRED_VLLM_VERSION" "pumice-rust/src/async_llm.rs:pub const REQUIRED_VLLM_VERSION" "src/pumice/vllm_adapter/bootstrap.py:^REQUIRED_VLLM_VERSION" "pyproject.toml:\"vllm==" "pumice_version_info.json:\"vllm_version\""; do f="${spec%%:*}"; pat="${spec#*:}"; git show "${R}:${f}" | grep -n -- "$pat" | sed "s|^|${f}:|"; done; done
== 769c7529
src/pumice/config.py:86:REQUIRED_VLLM_VERSION = "0.32.0"
pumice-rust/src/async_llm.rs:33:pub const REQUIRED_VLLM_VERSION: &str = "0.32.0";
src/pumice/vllm_adapter/bootstrap.py:49:REQUIRED_VLLM_VERSION = "0.32.0"
pyproject.toml:177:    "vllm==0.32.0",
pyproject.toml:201:    "vllm==0.32.0",
pumice_version_info.json:9:  "vllm_version": "0.32.0",
== 9d126713
src/pumice/config.py:72:REQUIRED_VLLM_VERSION = "0.30.0"
pumice-rust/src/async_llm.rs:33:pub const REQUIRED_VLLM_VERSION: &str = "0.30.0";
src/pumice/vllm_adapter/bootstrap.py:49:REQUIRED_VLLM_VERSION = "0.30.0"
pyproject.toml:178:    "vllm==0.30.0",
pyproject.toml:202:    "vllm==0.30.0",
pumice_version_info.json:9:  "vllm_version": "0.30.0",
```

Site map. Line numbers are at the new pin:

| # | Site | Line | Value |
|---|---|---|---|
| 1 | `src/pumice/config.py` `REQUIRED_VLLM_VERSION` | 86 | `0.32.0` |
| 2 | `pumice-rust/src/async_llm.rs` `REQUIRED_VLLM_VERSION` | 33 | `0.32.0` |
| 3 | `src/pumice/vllm_adapter/bootstrap.py` `REQUIRED_VLLM_VERSION` | 49 | `0.32.0` |
| 4 | `pyproject.toml` `[cuda]` extra | 177 | `vllm==0.32.0` |
| 5 | `pyproject.toml` `[vllm-runner]` extra | 201 | `vllm==0.32.0` |
| 6 | `pumice_version_info.json` `vllm_version` | 9 | `0.32.0` |

Other places that also carry the version. They are not in the six:

```
$ git grep -n -F '0.32.0' 769c7529 -- extensions/pumice_vllm_ext/guard.py extensions/pyproject.toml configuration/Dockerfile.kimi-k3 generate-wheel.sh | sed 's/^769c7529://' | cut -c1-150
configuration/Dockerfile.kimi-k3:11:FROM vllm/vllm-openai:v0.32.0
configuration/Dockerfile.kimi-k3:24:    python3 -c "import platform, vllm, pumice, pumice_rust; assert platform.machine() == 'aarch64'; assert vllm.__
extensions/pumice_vllm_ext/guard.py:4:prebuilt CUDA wheel ``vllm==0.32.0``. A monkeypatch that targets a function whose
extensions/pumice_vllm_ext/guard.py:45:PINNED_VLLM_VERSION = "0.32.0"
extensions/pumice_vllm_ext/guard.py:131:    # dist-info as `0.32.0+precompiled` while the package's own `__version__`
extensions/pumice_vllm_ext/guard.py:132:    # string stays `0.32.0`. Comparing raw strings made this refuse on exactly
extensions/pumice_vllm_ext/guard.py:150:    ``"0.32.0+precompiled"`` -> ``"0.32.0"``; ``"0.32.0"`` is unchanged. Note a dev
extensions/pumice_vllm_ext/guard.py:168:    # Local-segment-insensitive: `0.32.0+precompiled` IS the pin, and warning that
extensions/pyproject.toml:8:description = "Content-hash-guarded patches applied onto the vLLM the host image ships (developed against the vllm==0.32.0
generate-wheel.sh:16:#   flashinfer-python/cubin/jit-cache==0.7.0.post1, vllm==0.32.0,
```

The Pumice test that locks the sites together:

```
$ git grep -n -E 'REQUIRED_VLLM_VERSION|vllm==' 769c7529 -- tests/test_model_runner_flag.py | sed 's/^769c7529://' | sed -n 1,8p
tests/test_model_runner_flag.py:32:    REQUIRED_VLLM_VERSION,
tests/test_model_runner_flag.py:157:    assert f'"vllm=={REQUIRED_VLLM_VERSION}"' in vllm_extra, vllm_extra
tests/test_model_runner_flag.py:166:        r'REQUIRED_VLLM_VERSION: &str = "([^"]+)"', rust
tests/test_model_runner_flag.py:168:    assert match is not None, "REQUIRED_VLLM_VERSION missing from async_llm.rs"
tests/test_model_runner_flag.py:169:    assert match.group(1) == REQUIRED_VLLM_VERSION
tests/test_model_runner_flag.py:1905:    Python layer is not installed. Pin must equal REQUIRED_VLLM_VERSION on the
tests/test_model_runner_flag.py:1923:    assert "vllm==0.32.0" in cuda_reqs, (
tests/test_model_runner_flag.py:1970:    m = re.search(r'REQUIRED_VLLM_VERSION[^=]*=\s*"([^"]+)"', async_llm)
```

The runtime version check is a warning, not a gate. It is the same at both pins:

```
$ git show 769c7529:pumice-rust/src/async_llm.rs | sed -n 29,33p; git show 769c7529:pumice-rust/src/async_llm.rs | grep -n -E 'PUMICE_SILENCE_VLLM_VERSION_WARNING|not enforced as a runtime|A version MISMATCH is still reported'
/// The vLLM release the `--model-runner=vllm` adapter is DEVELOPED against.
///
/// Reported in logs and used by the packaging checks; it is deliberately NOT
/// enforced as a runtime equality gate. See `assert_vllm_runner_available`.
pub const REQUIRED_VLLM_VERSION: &str = "0.32.0";
199:const VLLM_VERSION_WARN_SILENCE_ENV: &str = "PUMICE_SILENCE_VLLM_VERSION_WARNING";
1958:    /// A version MISMATCH is still reported — loudly, at `warn` — so an
1960:    /// with `PUMICE_SILENCE_VLLM_VERSION_WARNING=1` on images that intentionally
```

AMMO's `check_pins()` is stricter than Pumice. It fails on any mismatch. That is intended (conventions section 6).

### 4.2 torch: unchanged at 2.13.0

Pumice requires `torch==2.13.0`. The source line is `pyproject.toml:127`, in the `cuda` extra. The old pin had the same value at line 128.

```
$ for R in 769c7529 9d126713; do echo "== $R"; git show "${R}:pyproject.toml" | grep -n -E '^ *"(torch|torchaudio|torchvision|triton)[=<>]' ; done
== 769c7529
127:    "torch==2.13.0",
136:    "torchaudio==2.11.0",
137:    "torchvision==0.28.0",
142:    "triton>=3.7.0,<3.8",
== 9d126713
128:    "torch==2.13.0",
137:    "torchaudio==2.11.0",
138:    "torchvision==0.28.0",
143:    "triton>=3.7.0,<3.8",
```

Corroboration, same value:

```
$ git show 769c7529:pumice_version_info.json | sed -n 11p; git show 769c7529:build-wheel-container.sh | sed -n 176p; git show 769c7529:generate-wheel.sh | sed -n 15p
  "torch_version": "2.13.0",
      torch==2.13.0 torchaudio==2.11.0 torchvision==0.28.0 \
#   torch==2.13.0, triton>=3.7.0,<3.8,
```

Two stale mentions look like torch pins and are not. Do not read them:

```
$ git show 769c7529:requirements.txt | sed -n '1,7p;128p'; git show 769c7529:Dockerfile.dev | sed -n 6p
#
# This file is autogenerated by hatch-pip-compile with Python 3.12
#
# - pytest
# - mypy
# - torch>=2.4.0
# - transformers>=4.51.0
torch==2.9.1
# (torch 2.10+cu130, triton 3.6, flashinfer 0.6.6, vllm + mantle_extensions).
```

`requirements.txt` is an old hatch-pip-compile test file (header says `torch>=2.4.0`, pin 2.9.1). `Dockerfile.dev` line 6 is a comment about an older dev image.

The `+cu130` tag in AMMO's `REQUIRED_TORCH` comes from the cu130 index. Pumice's own text names that index:

```
$ git show 769c7529:pyproject.toml | sed -n 46,49p
# The CUDA 13 stack (torch+cu130, flashinfer-cubin/jit-cache pinned to
# match flashinfer-python) is still curated. On dev machines install
# with `pip install .[cuda] --extra-index-url
# https://download.pytorch.org/whl/cu130`.
```

`torch==2.13.0` accepts `2.13.0+cu130` under PEP 440, so AMMO's `"2.13.0+cu130"` stays correct. The plan says `REQUIRED_TORCH` reads the line named here: `pyproject.toml:127`.

Other pins that moved in `pyproject.toml` (context for the fingerprint, not used by AMMO checks):

```
$ git diff 9d126713 769c7529 -- pyproject.toml | grep -E '^[+-] +"' | grep -v -E 'vllm==' 
-    "transformers==5.14.1",
+    "transformers==5.19.0",
-    "nvidia-cutlass-dsl[cu13]==4.7.1",
+    "nvidia-cutlass-dsl[cu13]==4.8.0",
-    "tokenspeed-mla==0.1.8; platform_system == 'Linux'",
-    "humming-kernels[cu13]==0.1.12",
+    "tokenspeed-mla==0.2.15; platform_system == 'Linux'",
+    "humming-kernels[cu13]==0.1.17",
-    "flashinfer-python==0.6.18.post1",
-    "flashinfer-jit-cache==0.6.18.post1",
+    "flashinfer-python==0.7.0.post1",
+    "flashinfer-jit-cache==0.7.0.post1",
-    "flashinfer-cubin @ https://github.com/flashinfer-ai/flashinfer/releases/download/v0.6.18.post1/flashinfer_cubin-0.6.18.post1-py3-none-any.whl",
+    "flashinfer-cubin @ https://github.com/flashinfer-ai/flashinfer/releases/download/v0.7.0.post1/flashinfer_cubin-0.7.0.post1-py3-none-any.whl",
```

### 4.3 Does Pumice still depend on vLLM at run time?

Yes for the CUDA stack. The header comment in `pyproject.toml` is stale. Lines 20-21 say zero run-time dependency. The same file puts `vllm==0.32.0` in the `cuda` extra. It says confdb defaults gemma4, gpt-oss, GLM-5.2, DeepSeek-V4, and Kimi-K3 to the vLLM runner.

```
$ git show 769c7529:pyproject.toml | sed -n '18,21p;41,42p'
# Dependency strategy:
#
# Pumice is fully self-contained on torch + triton + flashinfer. Zero
# runtime dependency on vllm. All vllm-sourced kernels are vendored:
# Verification: `grep -rn "from vllm\|import vllm" src/pumice/` returns
# zero hits. `pip install .whl[cuda] && python -c "import pumice"`
```

```
$ diff <(git show 9d126713:pyproject.toml | sed -n 1,50p) <(git show 769c7529:pyproject.toml | sed -n 1,50p) && echo 'header lines 1-50 identical at both pins (the stale text is old)'
header lines 1-50 identical at both pins (the stale text is old)
```

```
$ git show 769c7529:pyproject.toml | sed -n 54,72p | grep -v '^ *#'
dependencies = [
    "transformers==5.19.0",
    "numpy>=2.2.0,<2.3",
    "xxhash",
    "pyzmq",
    "soundfile>=0.12",
    "librosa>=0.10",
]
```

```
$ git show 769c7529:pyproject.toml | sed -n 75,78p
# CUDA-13 stack (torch 2.13 + flashinfer 0.7.0.post1) PLUS vLLM 0.32.0,
# because confdb now defaults gemma4 / gpt-oss / GLM-5.2 / DeepSeek-V4 / Kimi-K3
# to `model_runner = "vllm"` (see confdb/*.toml and confdb/schema.rs::KNOWN_KEYS).
# vLLM therefore has to be present in any venv that serves those models, and
```

```
$ for c in 769c7529 9d126713; do echo "$c loose hits of 'from vllm|import vllm' outside vllm_adapter: $(git grep -n -E '(from vllm|import vllm)' $c -- src/pumice ':!src/pumice/vllm_adapter' | wc -l | tr -d ' ')"; echo "$c line-start hits outside vllm_adapter: $(git grep -n -E '^[[:space:]]*(from vllm|import vllm)' $c -- src/pumice ':!src/pumice/vllm_adapter' | wc -l | tr -d ' ')"; echo "$c line-start hits inside vllm_adapter: $(git grep -n -E '^[[:space:]]*(from vllm|import vllm)' $c -- src/pumice/vllm_adapter | wc -l | tr -d ' ')"; done; git grep -n -E '^[[:space:]]*from vllm import _custom_ops' 769c7529 -- src/pumice | sed 's/^769c7529://'
769c7529 loose hits of 'from vllm|import vllm' outside vllm_adapter: 42
769c7529 line-start hits outside vllm_adapter: 6
769c7529 line-start hits inside vllm_adapter: 57
9d126713 loose hits of 'from vllm|import vllm' outside vllm_adapter: 42
9d126713 line-start hits outside vllm_adapter: 6
9d126713 line-start hits inside vllm_adapter: 53
src/pumice/engine/kv_offload_worker.py:358:            from vllm import _custom_ops as vllm_ops
```

So the check quoted in `pyproject.toml:41-42` (zero hits) does not hold at either pin. Most loose hits are comments and docstrings. Some are real: the vLLM adapter has 57 line-start import lines (it imports lazily, `vllm_adapter/__init__.py:10`), and `src/pumice/engine/kv_offload_worker.py:358` imports `vllm._custom_ops` outside the adapter. The base `dependencies` list has no vllm entry (output above), so vLLM is required through the `cuda` extra. In the Rust launch path the vLLM import happens only when the runner is `vllm`:

```
$ git show 769c7529:pumice-rust/src/async_llm.rs | grep -n -E 'Called only from the `vllm` arm|assert_vllm_runner_available\(py\)'
1109:            Self::assert_vllm_runner_available(py)?;
1963:    /// Called only from the `vllm` arm, so the flag-off path never imports vLLM.
2458:            Self::assert_vllm_runner_available(py)?;
```

### 4.4 When does gpt-oss use the `vllm` runner?

Precedence, lowest to highest: default `pumice`, then env `PUMICE_USE_VLLM_RUNNER=1`, then the confdb `model_runner` key, then an explicit `--model-runner`.

```
$ git show 769c7529:pumice-rust/src/config.rs | sed -n 31,43p
/// Default runner kind, derived from `PUMICE_USE_VLLM_RUNNER`.
///
/// The vLLM runner is strictly opt-in: anything other than an explicit
/// truthy value (`1` / `true`, case-insensitive) keeps the legacy Pumice
/// runner, so an unset/garbage env can never divert a production launch.
/// An explicit `--model-runner` on the command line is parsed afterwards
/// and therefore wins over this default.
pub fn model_runner_kind_default_from_env(env_value: Option<&str>) -> String {
    match env_value.map(|v| v.trim().to_ascii_lowercase()) {
        Some(ref v) if v == "1" || v == "true" => "vllm".to_string(),
        _ => "pumice".to_string(),
    }
}
```

```
$ git show 769c7529:pumice-rust/src/confdb/schema.rs | sed -n 151,156p
    // PRECEDENCE, lowest to highest: hardcoded "pumice" default -> the
    // PUMICE_USE_VLLM_RUNNER env var -> this confdb key -> an explicit
    // `--model-runner` on the command line. Confdb outranks the env var so a
    // per-model default is not silently undone by an inherited environment,
    // and the CLI outranks confdb so an operator can always pin a runner for
    // a single launch (which is how we A/B and how we fall back).
```

The gpt-oss rows. There is no `[sm80]` row:

```
$ git grep -n -E '^\[sm[0-9]+\]|^model_runner' 769c7529 -- pumice-rust/confdb/gpt_oss.toml | sed 's/^769c7529://'
pumice-rust/confdb/gpt_oss.toml:21:[sm90]
pumice-rust/confdb/gpt_oss.toml:30:model_runner = "vllm"
pumice-rust/confdb/gpt_oss.toml:45:[sm100]
pumice-rust/confdb/gpt_oss.toml:52:model_runner = "vllm"
pumice-rust/confdb/gpt_oss.toml:75:[sm120]
pumice-rust/confdb/gpt_oss.toml:84:model_runner = "vllm"
```

| GPU class | Compute capability | Family section | gpt-oss `model_runner` |
|---|---|---|---|
| A100, L40S (sm80, sm89) | major 8 | `sm80` | no row, so no confdb value, so `pumice` unless env or CLI says `vllm` |
| H100, H200 | major 9 | `sm90` | `vllm` (gpt_oss.toml:30) |
| B200, B300 | major 10 | `sm100` | `vllm` (gpt_oss.toml:52) |
| Consumer Blackwell | major 12 | `sm120` | `vllm` (gpt_oss.toml:84) |
| No CUDA device (Neuron) | defaults to (9, 0) | `sm90` | `vllm` |

Why `sm80` has no row: the resolver maps major to a family name and returns an empty map when the entry has no such section.

```
$ git show 769c7529:pumice-rust/src/confdb/resolve.rs | sed -n 70,79p; git show 769c7529:pumice-rust/src/confdb/resolve.rs | sed -n 184,186p
pub fn cc_to_family(cc_major: u32) -> String {
    // Special-cased majors in the Python table.
    match cc_major {
        9 => "sm90".to_string(),
        10 => "sm100".to_string(), // B200 (10.0) + B300 (10.3)
        12 => "sm120".to_string(),
        // Fallback: `sm{major}0` — matches the Python `f"sm{major}0"` line.
        other => format!("sm{other}0"),
    }
}
    let Some(hw_section) = entry.sections.get(hw_family) else {
        return out;
    };
```

```
$ git show 769c7529:pumice-rust/src/confdb/resolve.rs | sed -n 278,282p
    fn cc_unknown_major_falls_back_to_sm_n0() {
        // Matches the Python `f"sm{major}0"` fallback for unknown majors.
        assert_eq!(cc_to_family(11), "sm110");
        assert_eq!(cc_to_family(8), "sm80");
    }
```

```
$ git show 769c7529:src/pumice/models/model_config.py | sed -n 42,47p
    # Determine compute capability
    if torch.cuda.is_available():
        compute_capability = torch.cuda.get_device_capability()
    else:
        # Default to sm90 when no GPU available (e.g., during testing)
        compute_capability = (9, 0)
```

The embedded test guards the sm100 result. `GptOssForCausalLM` must resolve `model_runner = "vllm"` at compute capability (10, 0):

```
$ git show 769c7529:pumice-rust/src/confdb/resolve.rs | sed -n 494,503p; git show 769c7529:pumice-rust/src/confdb/resolve.rs | sed -n 523,528p
    fn shipping_models_default_to_the_vllm_runner() {
        use std::collections::HashMap;

        // (architectures, hf attrs needed to satisfy any `condition =` gate)
        let vllm_defaulted: [(&str, Vec<(&str, &str)>); 8] = [
            ("Gemma4ForConditionalGeneration", vec![]),
            ("Gemma4ForCausalLM", vec![]),
            ("GptOssForCausalLM", vec![]),
            ("FMRForCausalLM", vec![]),
            ("GlmMoeDsaForCausalLM", vec![]),
            let out = resolve(&[arch.to_string()], (10, 0), None, &hf);
            assert_eq!(
                out.get("model_runner").map(String::as_str),
                Some("vllm"),
                "{arch} must default to the vLLM runner on sm100"
            );
```

AMMO's own sm80 patch still applies. It adds an `[sm80]` row with `model_runner = "vllm"`. `gpt_oss.toml` is identical at both pins, so the patch context is unchanged. Dry run against a scratch copy outside the repo:

```
$ rm -rf /tmp/pdelta_sm80 && mkdir -p /tmp/pdelta_sm80 && git archive 769c7529 pumice-rust/confdb/gpt_oss.toml | tar -x -C /tmp/pdelta_sm80 && cd /tmp/pdelta_sm80 && patch -p1 --dry-run < /Users/jinhun/GitHub/ammo-server/docker/pumice-sm80-gpt-oss.patch; echo "patch exit=$?"; rm -rf /tmp/pdelta_sm80
patching file 'pumice-rust/confdb/gpt_oss.toml'
patch exit=0
```

The patch comments cite `config.py:1328-1392` and `config.py:1360-1374`. Those line numbers are stale (config.py grew 17 lines). They are comments only.

### 4.5 Values for the plugin copy of `pumice_pins.py`

Conventions section 6 fixes the plugin's pins. The values below come from the evidence above. The first command proves that `origin/mainline` is the new pin and that the checkout was not moved:

```
$ git rev-parse origin/mainline 769c7529 && git rev-parse --abbrev-ref HEAD && git status --porcelain | wc -l | tr -d ' '
769c752995b8a39b27df028176411d17c99ff441
769c752995b8a39b27df028176411d17c99ff441
mainline
0
```

| Constant | Value | Source | AMMO line to change (`shared/pumice_pins.py`) |
|---|---|---|---|
| `PUMICE_COMMIT` | `769c752995b8a39b27df028176411d17c99ff441` | the command above | 36, was `9d126713c03dcc88883b85c3d3a857b89982c957` |
| `REQUIRED_TORCH` | `2.13.0+cu130` (no change) | Pumice `pyproject.toml:127` says `torch==2.13.0`; the `+cu130` local tag is AMMO's index choice (4.2) | 32 |
| `REQUIRED_VLLM` | not a constant in the plugin | conventions section 6: `check_pins()` compares the installed vLLM with `pumice.config.REQUIRED_VLLM_VERSION` (`config.py:86`, `0.32.0`), and only when the runner kind is `vllm` | 31 and 75-80 today |

The server copy compares `pumice.config.REQUIRED_VLLM_VERSION` with its own `REQUIRED_VLLM` on every call, with no runner condition. Lines 75-80 today:

```
$ sed -n 75,80p /Users/jinhun/GitHub/ammo-server/shared/pumice_pins.py
    pumice_required_vllm = getattr(pumice_config, "REQUIRED_VLLM_VERSION", None)
    if pumice_required_vllm != REQUIRED_VLLM:
        return False, (
            f"pumice.config.REQUIRED_VLLM_VERSION {pumice_required_vllm!r} "
            f"does not match REQUIRED_VLLM {REQUIRED_VLLM!r}"
        )
```

Whether the runner kind is `vllm` is decided by the rules in 4.4. For gpt-oss on L40S or A100 without AMMO's sm80 patch it is `pumice`, so the vLLM check does not apply there.

## 5. `PUMICE_*` env names

Recipe from the header of `pumice_env_allowlist.txt` (lines 12-14):

`grep -rhoE 'PUMICE_[A-Z0-9_]{3,}' src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$'`

The checkout is read-only here, so the same recipe runs through `git grep` on each commit. First proof that `git grep` gives the seed. The old pin must reproduce the 134 names exactly:

```
$ grep -E '^PUMICE_' /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt | sort -u | wc -l
     134
```

```
$ diff <(grep -E '^PUMICE_' /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt | sort -u) <(git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 9d126713 -- src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$') && echo 'old pin recipe output == allowlist seed (identical)'; git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 9d126713 -- src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$' | wc -l
old pin recipe output == allowlist seed (identical)
     134
```

Now the new pin:

```
$ git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 769c7529 -- src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$' | wc -l
     144
```

Added (in the new pin, not in the seed):

```
$ comm -13 <(grep -E '^PUMICE_' /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt | sort -u) <(git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 769c7529 -- src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$')
PUMICE_CACHE_EXPORT_LEASE_BUDGET
PUMICE_CACHE_EXPORT_LEASE_TTL_MS
PUMICE_CACHE_EXPORT_PINNED_BLOCK_PCT
PUMICE_CACHE_EXPORT_RESTORE_BUDGET
PUMICE_CACHE_EXPORT_STALL_MS
PUMICE_KV_PEER_FAILURE_BACKOFF_MS
PUMICE_MAX_MEDIA_BYTES_PER_ITEM
PUMICE_RECOVERY_WATCHDOG_TEST_CHILD
PUMICE_SSD_READ_THREADS
PUMICE_SSD_WRITE_THREADS
```

Removed (in the seed, not in the new pin):

```
$ comm -23 <(grep -E '^PUMICE_' /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt | sort -u) <(git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 769c7529 -- src pumice-rust/src | sort -u | grep -v -e '_$' -e '^PUMICE_MODELS$') | wc -l
       0
```

Result: 134 names become 144. Ten added, none removed.

Where each added name is read, with its default. Commands: `git grep -n -w -F <NAME> 769c7529 -- src pumice-rust/src`.

| Name | Read at | Default or role |
|---|---|---|
| `PUMICE_CACHE_EXPORT_LEASE_BUDGET` | `pumice-rust/src/cache_export.rs:53` | 8 |
| `PUMICE_CACHE_EXPORT_LEASE_TTL_MS` | `pumice-rust/src/cache_export.rs:58` | 10000 |
| `PUMICE_CACHE_EXPORT_PINNED_BLOCK_PCT` | `pumice-rust/src/cache_export.rs:55` | 25, capped at 100 |
| `PUMICE_CACHE_EXPORT_RESTORE_BUDGET` | `pumice-rust/src/cache_export.rs:54` | 2 |
| `PUMICE_CACHE_EXPORT_STALL_MS` | `pumice-rust/src/cache_export.rs:337` | 2500, 0 disables |
| `PUMICE_KV_PEER_FAILURE_BACKOFF_MS` | `pumice-rust/src/kv_connector/nixl.rs:2963` | 10 s (nixl.rs:79) |
| `PUMICE_MAX_MEDIA_BYTES_PER_ITEM` | `pumice-rust/src/validators.rs:47` | 10 MiB; the override applies on the vLLM runner (validators.rs:26-27) |
| `PUMICE_RECOVERY_WATCHDOG_TEST_CHILD` | `pumice-rust/src/recovery_watchdog.rs:276` | none; inside a Rust test |
| `PUMICE_SSD_READ_THREADS` | `src/pumice/engine/ssd_thread_pool.py:63` | 16 (ssd_thread_pool.py:27) |
| `PUMICE_SSD_WRITE_THREADS` | `src/pumice/engine/ssd_thread_pool.py:64` | 16 (ssd_thread_pool.py:27) |

The last `grep -v` of the recipe drops two entries: a name fragment that ends in an underscore, and the registry dict `_PUMICE_MODELS`. They show when the filter is inverted:

```
$ git grep -hoE 'PUMICE_[A-Z0-9_]{3,}' 769c7529 -- src pumice-rust/src | sort -u | grep -e '_$' -e '^PUMICE_MODELS$'
PUMICE_KV_HANDOFF_ADMISSION_
PUMICE_MODELS
```

The 8 `VLLM_*` names come from `src/pumice/_envs.py`. That file did not change:

```
$ git diff --exit-code 9d126713 769c7529 -- src/pumice/_envs.py; echo "exit=$?"
exit=0
```

Two scope notes:

- The recipe scans `src` and `pumice-rust/src` only. `PUMICE_VLLM_EXTENSIONS` (read under `extensions/` and `setup.py`) is already in the allowlist, so that gap is old.
- Names that AMMO scripts mention and Pumice never reads at either pin: `PUMICE_DISABLE_LOG_STATS` (`ncu_sanity_driver.py:172`), `PUMICE_MY_OPT` (schema example), `PUMICE_PINS_FILE`, `PUMICE_SERVER_EXE`, `PUMICE_CUDA_PROFILE`, `PUMICE_NEURON_PROFILE`. Check:

```
$ for n in PUMICE_DISABLE_LOG_STATS PUMICE_PINS_FILE PUMICE_SERVER_EXE PUMICE_CUDA_PROFILE PUMICE_NEURON_PROFILE; do echo "$n files: old=$(git grep -l -w -F $n 9d126713 -- . | wc -l | tr -d ' ') new=$(git grep -l -w -F $n 769c7529 -- . | wc -l | tr -d ' ')"; done
PUMICE_DISABLE_LOG_STATS files: old=0 new=0
PUMICE_PINS_FILE files: old=0 new=0
PUMICE_SERVER_EXE files: old=0 new=0
PUMICE_CUDA_PROFILE files: old=0 new=0
PUMICE_NEURON_PROFILE files: old=0 new=0
```

## 6. Server CLI and the flags AMMO passes

### 6.1 argparse flags of `rust_grpc_server.py` at the new pin

The command you asked for. Multi-line calls show only the first line of the call, so the full flag list follows in 6.2:

```
$ git show 769c7529:rust_grpc_server.py | grep -n add_argument | cut -c1-110
26:    p.add_argument("--model", type=str, required=True, help="Path to model directory")
27:    p.add_argument("--port", type=int, default=50051, help="gRPC listen port")
28:    p.add_argument("--max-model-len", type=int, default=4096)
32:    p.add_argument("--max-num-batched-tokens", type=int, default=8192)
33:    p.add_argument("--cudagraph-capture-sizes", type=int, nargs="+", default=None,
35:    p.add_argument("--max-cudagraph-capture-size", type=int, default=None,
37:    p.add_argument("--tensor-parallel-size", type=int, default=1)
38:    p.add_argument("--nnodes", type=int, default=1)
39:    p.add_argument("--node-rank", type=int, default=0)
40:    p.add_argument("--master-addr", type=str, default="127.0.0.1")
41:    p.add_argument("--master-port", type=int, default=29501)
42:    p.add_argument("--headless", action="store_true")
43:    p.add_argument("--enforce-eager", action="store_true")
44:    p.add_argument("--trust-remote-code", action="store_true")
45:    p.add_argument("--max-num-seqs", type=int, default=512)
46:    p.add_argument("--gpu-memory-utilization", type=float, default=None,
51:    p.add_argument("--num-gpu-blocks", "--num-gpu-blocks-override",
56:    p.add_argument("--draft-model", type=str, default=None,
63:    p.add_argument("--speculative-method", type=str, default="eagle3",
65:    p.add_argument("--num-speculative-tokens", type=int, default=3)
66:    p.add_argument("--parallel-drafting", action="store_true",
74:    p.add_argument("--enable-chunked-prefill", dest="enable_chunked_prefill",
77:    p.add_argument("--no-enable-chunked-prefill", dest="enable_chunked_prefill",
80:    p.add_argument("--no-compile", action="store_true")
81:    p.add_argument("--attention-backend", type=str, default="auto")
82:    p.add_argument("--kv-cache-dtype", type=str, default="auto")
83:    p.add_argument("--dtype", type=str, default="auto",
86:    p.add_argument("--moe-backend", type=str, default="auto")
87:    p.add_argument("--model-runner", type=str, default=None,
89:    p.add_argument("--cmb-port", type=int, default=None)
90:    p.add_argument("--metrics-port", type=int, default=None)
110:    hybrid_kv.add_argument(
117:    hybrid_kv.add_argument(
124:    prefix_caching.add_argument(
130:    prefix_caching.add_argument(
137:    p.add_argument("--prefix-match-unit", type=int, default=None)
138:    p.add_argument(
143:    p.add_argument("--kv-connector", type=str, default="noop")
144:    p.add_argument("--nixl-backend", type=str.upper,
146:    p.add_argument("--nixl-backends", type=lambda value: value.split(","), default=[])
147:    p.add_argument("--enable-cumem-allocator", action="store_true")
148:    p.add_argument("--skip-mm-profiling", action="store_true")
149:    p.add_argument("--language-model-only", action="store_true")
150:    p.add_argument("--mm-encoder-tp-mode", choices=("weights", "data"), default="weights")
151:    p.add_argument("--vllm-moe-backend", default=None)
152:    p.add_argument("--vllm-attention-backend", default=None,
154:    p.add_argument("--vllm-spec-attention-backend", default=None,
156:    p.add_argument("--vllm-attention-config", default=None,
158:    p.add_argument("--enable-flashinfer-autotune",
162:    p.add_argument("--kv-connector-mode", type=str, default="both")
163:    p.add_argument("--kv-connector-engine-id", type=str, default=None)
166:    p.add_argument("--enable-kv-offload", action="store_true",
168:    p.add_argument("--kv-offload-cpu-bytes", type=parse_byte_size, default=0,
172:    p.add_argument("--kv-offload-block-size-factor", type=int, default=1,
175:    p.add_argument("--kv-offload-eviction-policy", type=str, default="lru",
179:    p.add_argument("--kv-offload-cache-policy", type=str, default="spillover",
184:    p.add_argument("--kv-offload-prompt-only", type=lambda v: v.lower() != "false",
187:    p.add_argument("--kv-offload-tiers", type=str, default="cpu",
190:    p.add_argument("--kv-offload-ssd-dir", type=str, default="",
193:    p.add_argument("--kv-offload-ssd-bytes", type=parse_byte_size, default=0,
197:    p.add_argument("--kv-offload-store-watermark-pct", type=int, default=0,
206:    p.add_argument("--enable-expert-parallel", action="store_true",
209:    p.add_argument("--data-parallel-size", type=int, default=1,
212:    p.add_argument("--data-parallel-rank", type=int, default=0,
214:    p.add_argument("--data-parallel-address", type=str, default="127.0.0.1",
216:    p.add_argument("--data-parallel-port", type=int, default=13345,
218:    p.add_argument("--data-parallel-store-port", type=int, default=13346,
```

Parser is strict. An unknown flag stops the launch:

```
$ git show 769c7529:rust_grpc_server.py | grep -n -E 'parse_args|parse_known'
24:def parse_args():
221:    return p.parse_args()
225:    args = parse_args()
```

### 6.2 Complete flag lists and the delta

```
$ flags() { git show "$1:rust_grpc_server.py" | python3 -I -c 'import ast,sys; t=ast.parse(sys.stdin.read()); c=[n for n in ast.walk(t) if isinstance(n,ast.Call) and getattr(n.func,"attr","")=="add_argument"]; [print(n.lineno,*[a.value for a in n.args if isinstance(a,ast.Constant) and str(a.value).startswith("--")]) for n in sorted(c,key=lambda n:n.lineno)]'; }; echo "count old=$(flags 9d126713 | wc -l | tr -d ' ') new=$(flags 769c7529 | wc -l | tr -d ' ')"; echo '--- diff of flag names (< old only, > new only)'; diff <(flags 9d126713 | cut -d' ' -f2- | tr ' ' '\n' | sort) <(flags 769c7529 | cut -d' ' -f2- | tr ' ' '\n' | sort); true
count old=61 new=67
--- diff of flag names (< old only, > new only)
2a3
> --cudagraph-capture-sizes
12a14
> --enable-flashinfer-autotune
35a38
> --max-cudagraph-capture-size
61a65,66
> --vllm-attention-backend
> --vllm-attention-config
62a68
> --vllm-spec-attention-backend
```

```
$ flags() { git show "$1:rust_grpc_server.py" | python3 -I -c 'import ast,sys; t=ast.parse(sys.stdin.read()); c=[n for n in ast.walk(t) if isinstance(n,ast.Call) and getattr(n.func,"attr","")=="add_argument"]; [print(n.lineno,*[a.value for a in n.args if isinstance(a,ast.Constant) and str(a.value).startswith("--")]) for n in sorted(c,key=lambda n:n.lineno)]'; }; flags 769c7529
26 --model
27 --port
28 --max-model-len
32 --max-num-batched-tokens
33 --cudagraph-capture-sizes
35 --max-cudagraph-capture-size
37 --tensor-parallel-size
38 --nnodes
39 --node-rank
40 --master-addr
41 --master-port
42 --headless
43 --enforce-eager
44 --trust-remote-code
45 --max-num-seqs
46 --gpu-memory-utilization
51 --num-gpu-blocks --num-gpu-blocks-override
56 --draft-model
63 --speculative-method
65 --num-speculative-tokens
66 --parallel-drafting
74 --enable-chunked-prefill
77 --no-enable-chunked-prefill
80 --no-compile
81 --attention-backend
82 --kv-cache-dtype
83 --dtype
86 --moe-backend
87 --model-runner
89 --cmb-port
90 --metrics-port
110 --enable-hybrid-kv-cache
117 --no-enable-hybrid-kv-cache
124 --enable-prefix-caching
130 --no-enable-prefix-caching
137 --prefix-match-unit
138 --enable-mamba-fine-grained-prefix-cache
143 --kv-connector
144 --nixl-backend
146 --nixl-backends
147 --enable-cumem-allocator
148 --skip-mm-profiling
149 --language-model-only
150 --mm-encoder-tp-mode
151 --vllm-moe-backend
152 --vllm-attention-backend
154 --vllm-spec-attention-backend
156 --vllm-attention-config
158 --enable-flashinfer-autotune
162 --kv-connector-mode
163 --kv-connector-engine-id
166 --enable-kv-offload
168 --kv-offload-cpu-bytes
172 --kv-offload-block-size-factor
175 --kv-offload-eviction-policy
179 --kv-offload-cache-policy
184 --kv-offload-prompt-only
187 --kv-offload-tiers
190 --kv-offload-ssd-dir
193 --kv-offload-ssd-bytes
197 --kv-offload-store-watermark-pct
206 --enable-expert-parallel
209 --data-parallel-size
212 --data-parallel-rank
214 --data-parallel-address
216 --data-parallel-port
218 --data-parallel-store-port
```

Changes in the choices of an existing flag: `--kv-offload-eviction-policy` accepts `qos_lru` (new) besides `lru`. See the diff in 2.2.

### 6.3 `pumice-server` (Rust binary) flags

`resolve_server_exe` (`run_pumice_grpc_bench.py:1280`) prefers `target/release/pumice-server` over `rust_grpc_server.py`, so the binary parser matters when it is built. It parses argv by hand and rejects unknown flags. Quoted flag literals in `main.rs`, old versus new:

```
$ diff <(git show 9d126713:pumice-rust/src/main.rs | grep -oE '"--[a-z0-9-]+"' | sort -u) <(git show 769c7529:pumice-rust/src/main.rs | grep -oE '"--[a-z0-9-]+"' | sort -u); true
4a5
> "--cudagraph-capture-sizes"
21a23
> "--enable-flashinfer-autotune"
43a46
> "--long-prefill-token-threshold"
48a52
> "--max-cudagraph-capture-size"
63a68
> "--no-enable-flashinfer-autotune"
71a77,78
> "--priority-aging-steps"
> "--scheduling-policy"
77a85,86
> "--vllm-attention-backend"
> "--vllm-attention-config"
78a88
> "--vllm-spec-attention-backend"
```

```
$ git show 769c7529:pumice-rust/src/main.rs | sed -n 510,513p
            }
            other => {
                return Err(format!("Unknown argument: {}", other));
            }
```

Ten flag names are added and none removed. Three exist only in the Rust binary: `--scheduling-policy`, `--priority-aging-steps`, `--long-prefill-token-threshold`. Their defaults are off (`fcfs`, none, 0). The other seven are in `rust_grpc_server.py` too (its `--enable-flashinfer-autotune` also accepts the `--no-` form).

Two validation rules changed in `config.rs`. Both are in `validate_native_dp`, which runs when DP is above 1. The old rule rejected any TP size other than 1 for the vLLM runner. The new rules allow TP from 1 to 64 for it, and require `nnodes` 1. Old code, then new code:

```
$ git show 9d126713:pumice-rust/src/config.rs | sed -n 1133,1138p; echo ---; git show 769c7529:pumice-rust/src/config.rs | sed -n 1316,1328p
        if (self.data_parallel_hybrid_lb || self.model_runner_kind == "vllm")
            && self.tensor_parallel_size != 1
        {
            return Err("native vLLM DP currently requires tensor-parallel-size=1".into());
        }
        let sync = self
---
        if self.model_runner_kind == "vllm" && !(1..=64).contains(&self.tensor_parallel_size) {
            return Err("native vLLM DP tensor-parallel-size must be in [1, 64]".into());
        }
        if self.model_runner_kind == "vllm" && self.nnodes != 1 {
            return Err("each native vLLM DP leaf requires nnodes=1; use data-parallel-address for cross-host DP".into());
        }
        if self.data_parallel_hybrid_lb
            && self.tensor_parallel_size != 1
            && self.model_runner_kind != "vllm"
        {
            return Err("hybrid DP with TP>1 requires --model-runner=vllm".into());
        }
        let sync = self
```

New rules that can stop a launch (all in the Rust parser and Python bridge, none apply unless the flags are used):

- `--vllm-attention-backend`, `--vllm-spec-attention-backend`, `--vllm-attention-config` need the vLLM runner. With an explicit non-vLLM runner the parser stops. With a resolved non-vLLM runner `async_llm.rs` stops.
- `--scheduling-policy priority` needs `--priority-aging-steps N` with N above 0. Aging steps with the default `fcfs` policy are rejected.

```
$ git show 769c7529:pumice-rust/src/config.rs | grep -n -E 'vLLM attention overrides require|requires --priority-aging-steps|applies only to --scheduling-policy priority'; git show 769c7529:pumice-rust/src/async_llm.rs | grep -n 'vLLM attention overrides require'
1166:            return Err("vLLM attention overrides require --model-runner=vllm".into());
1180:                "--scheduling-policy priority requires --priority-aging-steps <N> with N > 0: \
1186:                Err("--priority-aging-steps applies only to --scheduling-policy priority".into())
2342:                "vLLM attention overrides require the vLLM runner, resolved {resolved_runner}"
```

### 6.4 Flags AMMO passes, with file:line and status at the new pin

Status is computed against both parsers at the new pin: the argparse list from 6.2 and the quoted literals in `main.rs`. The generator read each AMMO line in the working tree (branch `pumice-on-main`) and stopped if the flag was not on it. The "Line text" column shows the first cited line. Check one by hand with `sed -n <N>p <file>`.

Bench runner `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py`:

| Flag | AMMO file:line | Line text | Use | At new pin |
|---|---|---|---|---|
| `--model` | `scripts/run_pumice_grpc_bench.py:1369` | `("--model", str(model_id)),` | always passed by `build_server_cmd` | present in both |
| `--port` | `scripts/run_pumice_grpc_bench.py:1370,1572` | `("--port", str(port)),` | always passed; rewritten per DP rank at 1572 | present in both |
| `--max-model-len` | `scripts/run_pumice_grpc_bench.py:1371` | `("--max-model-len", str(max_model_len)),` | always passed | present in both |
| `--tensor-parallel-size` | `scripts/run_pumice_grpc_bench.py:1372` | `("--tensor-parallel-size", str(tensor_parallel_size)),` | always passed | present in both |
| `--max-num-seqs` | `scripts/run_pumice_grpc_bench.py:1373,3907` | `("--max-num-seqs", str(max_num_seqs)),` | always passed; read back at 3907 | present in both |
| `--metrics-port` | `scripts/run_pumice_grpc_bench.py:475` | `_METRICS_PORT_FLAG = "--metrics-port"` | passed when a metrics port is set (constant `_METRICS_PORT_FLAG`) | present in both |
| `--model-runner` | `scripts/run_pumice_grpc_bench.py:1378` | `defaults.append(("--model-runner", str(model_runner)))` | passed when `default_model_runner()` returns a kind (Neuron) | present in both |
| `--enable-prefix-caching` | `scripts/run_pumice_grpc_bench.py:1384,1398` | `"--enable-prefix-caching"` | prefix-cache regime pin | present in both |
| `--no-enable-prefix-caching` | `scripts/run_pumice_grpc_bench.py:1386,1398` | `else "--no-enable-prefix-caching"` | prefix-cache regime pin | present in both |
| `--prefix-caching` | `scripts/run_pumice_grpc_bench.py:1400` | `if tok in ("--prefix-caching",):` | STRIP only (never emitted); no such flag exists | MISSING at new pin |
| `--data-parallel-size` | `scripts/run_pumice_grpc_bench.py:1536` | `dp_size = int(_flag_value(argv, "--data-parallel-size") or 1` | read by `plan_dp_cohort` from extra_args | present in both |
| `--data-parallel-rank` | `scripts/run_pumice_grpc_bench.py:1537,1559` | `if dp_size <= 1 or _has_flag(argv, "--data-parallel-rank"):` | added per rank by `plan_dp_cohort` | present in both |
| `--data-parallel-address` | `scripts/run_pumice_grpc_bench.py:1553` | `("--data-parallel-address", _flag_value(argv, "--data-parall` | added per rank | present in both |
| `--data-parallel-port` | `scripts/run_pumice_grpc_bench.py:1554` | `("--data-parallel-port", _flag_value(argv, "--data-parallel-` | added per rank | present in both |
| `--data-parallel-store-port` | `scripts/run_pumice_grpc_bench.py:1555` | `("--data-parallel-store-port", _flag_value(argv, "--data-par` | added per rank | present in both |
| `--draft-model` | `scripts/run_pumice_grpc_bench.py:4697,4704` | `draft = _flag_value(tgt.extra_args, "--draft-model")` | read and rewritten from extra_args | present in both |
| `--enable-chunked-prefill` | `scripts/run_pumice_grpc_bench.py:32` | `"extra_args": ["--enable-chunked-prefill"],` | schema doc example only | present in both |

Preflight list `preflight_pumice.py:70-77` (`SERVER_FLAGS`). It checks eight flags against `--help`:

| Flag | AMMO file:line | At new pin |
|---|---|---|
| `--model` | `scripts/preflight_pumice.py:70` | present in both |
| `--port` | `scripts/preflight_pumice.py:71` | present in both |
| `--max-model-len` | `scripts/preflight_pumice.py:72` | present in both |
| `--tensor-parallel-size` | `scripts/preflight_pumice.py:73` | present in both |
| `--max-num-seqs` | `scripts/preflight_pumice.py:74` | present in both |
| `--metrics-port` | `scripts/preflight_pumice.py:75` | present in both |
| `--enable-prefix-caching` | `scripts/preflight_pumice.py:76` | present in both |
| `--no-enable-prefix-caching` | `scripts/preflight_pumice.py:77` | present in both |

Flags written into `bench.extra_args` by the scaffold `new_target.py` (not the bench runner, but they reach the server command):

| Flag | AMMO file:line | At new pin |
|---|---|---|
| `--data-parallel-size` | `.claude/skills/ammo/scripts/new_target.py:451` | present in both |
| `--distributed-executor-backend` | `.claude/skills/ammo/scripts/new_target.py:452` | MISSING at new pin |
| `--enable-expert-parallel` | `.claude/skills/ammo/scripts/new_target.py:455` | present in both |
| `--max-num-seqs` | `.claude/skills/ammo/scripts/new_target.py:457` | present in both |
| `--max-num-batched-tokens` | `.claude/skills/ammo/scripts/new_target.py:459` | present in both |

Findings for 6.4:

- Every flag the bench runner emits exists at the new pin in both parsers. Nothing it emits was removed.
- `--prefix-caching` (`run_pumice_grpc_bench.py:1400`) does not exist at either pin. It is only stripped, so it is harmless. Same finding as touchpoint SS-28.
- `--distributed-executor-backend` (`new_target.py:452`) does not exist at either pin. Both parsers reject it (section 6.1 and 6.3). `workload-invocation.md:198` already says not to use it. The bench runner never strips it (0 hits in the next command), so a scaffolded DP above 1 target carries it into every rank command. This predates the delta.

```
$ for c in 9d126713 769c7529; do echo "$c: $(git grep -n -e 'distributed-executor-backend' -e 'distributed_executor_backend' $c -- rust_grpc_server.py pumice-rust/src src/pumice/config.py | wc -l | tr -d ' ') hits in Pumice"; done; echo "AMMO bench runner hits: $(grep -c 'distributed-executor-backend' /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py)"
9d126713: 0 hits in Pumice
769c7529: 0 hits in Pumice
AMMO bench runner hits: 0
```

- New flags that a campaign may now put in `bench.extra_args`: `--cudagraph-capture-sizes`, `--max-cudagraph-capture-size`, `--vllm-attention-backend`, `--vllm-spec-attention-backend`, `--vllm-attention-config`, `--enable-flashinfer-autotune` / `--no-enable-flashinfer-autotune`. Defaults leave the old behaviour.

## 7. Touchpoint rows under `scripts/`

Source: `/Users/jinhun/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md`. The file has 444 rows in its own count:

```
$ grep -c -E '^\| [A-Z]{2}-[0-9]+ ' /Users/jinhun/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md
444
```

Selection: rows whose AMMO site is under `ai_cli_session/_ammo_shared/scripts/` or `ai_cli_session/.claude/skills/ammo/scripts/`. That is every row of the "skill scripts" table (SS) except SS-86 (a schema file). The row-check script is in the appendix. Save it as `/tmp/pdelta_rowcheck.py`. It writes the rows as JSON:

```
$ python3 -I /tmp/pdelta_rowcheck.py > /tmp/pdelta_rows.json; python3 -I -c "import json; print(len(json.load(open('/tmp/pdelta_rows.json'))), 'rows selected')"
92 rows selected
```

```
$ echo "SS rows in file: $(grep -c -E '^\| SS-[0-9]+ ' /Users/jinhun/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md)"; grep -E '^\| SS-86 ' /Users/jinhun/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md | cut -d'|' -f2,3
SS rows in file: 93
 SS-86 | `ai_cli_session/_ammo_shared/schemas/state.schema.json` 
```

The rows by AMMO file:

```
$ python3 -I -c "import json,collections; r=json.load(open('/tmp/pdelta_rows.json')); c=collections.Counter(x['site'].strip('\`').split(':')[0].split(' ')[0].replace('ai_cli_session/','') for x in r); [print(v,k) for k,v in c.most_common()]"
53 _ammo_shared/scripts/run_pumice_grpc_bench.py
11 _ammo_shared/scripts/preflight_pumice.py
7 _ammo_shared/scripts/pumice_pb/README.md
6 .claude/skills/ammo/scripts/new_target.py
5 _ammo_shared/scripts/hook_cmd_classify.py
2 _ammo_shared/scripts/pumice_pb/pumice_engine_pb2.py
2 _ammo_shared/scripts/pumice_pb/pumice_engine_pb2_grpc.py
2 _ammo_shared/scripts/ncu_sanity_driver.py
1 _ammo_shared/scripts/pumice_pb/__init__.py
1 _ammo_shared/scripts/runtime_ops.py
1 _ammo_shared/scripts/verify_validation_gates.py
1 .claude/skills/ammo/scripts/ammo_state.py
```

`serving_benchmark_adapter.py` and `deployment_ops.py` have no rows in the touchpoints file. The adapter does carry a Pumice-pin fact; see section 8.

Method for the Pumice side. For each cited `file:line` the script reads the line at `9d126713`, then looks for the same text at `769c7529`. Pumice files named by these rows that differ between the pins:

```
$ python3 -I -c "import json; r=json.load(open('/tmp/pdelta_rows.json')); [print(p) for p in sorted({p for x in r for p in x['changed_files']})]" | while read p; do echo "$p  $(git diff --shortstat 9d126713 769c7529 -- $p | sed 's/^ *//')"; done
pumice-rust/src/async_llm.rs  1 file changed, 737 insertions(+), 34 deletions(-)
pumice-rust/src/block_manager.rs  1 file changed, 341 insertions(+), 25 deletions(-)
pumice-rust/src/block_manager_grouped.rs  1 file changed, 1531 insertions(+), 186 deletions(-)
pumice-rust/src/config.rs  1 file changed, 356 insertions(+), 6 deletions(-)
pumice-rust/src/dp_coordinator.rs  1 file changed, 181 insertions(+), 66 deletions(-)
pumice-rust/src/engine.rs  1 file changed, 4278 insertions(+), 426 deletions(-)
pumice-rust/src/grpc_server.rs  1 file changed, 15 insertions(+), 1 deletion(-)
pumice-rust/src/main.rs  1 file changed, 339 insertions(+), 1 deletion(-)
pumice-rust/src/metrics.rs  1 file changed, 324 insertions(+), 11 deletions(-)
pumice-rust/src/scheduler.rs  1 file changed, 2105 insertions(+), 253 deletions(-)
rust_grpc_server.py  1 file changed, 25 insertions(+), 2 deletions(-)
setup.py  1 file changed, 12 insertions(+), 2 deletions(-)
src/pumice/config.py  1 file changed, 25 insertions(+), 8 deletions(-)
src/pumice/engine/model_runner.py  1 file changed, 49 insertions(+), 68 deletions(-)
src/pumice/vllm_adapter/bootstrap.py  1 file changed, 156 insertions(+), 53 deletions(-)
src/pumice/vllm_adapter/multinode.py  1 file changed, 18 insertions(+), 9 deletions(-)
```

Rule for the last column. `unchanged` means the cited line or role is identical. `changed: line moved only` means the same text now sits on another line. `changed: <other>` means a behaviour or content change, and the evidence is in 7.1. Hand verdicts (rows whose Pumice source is a whole file or a non-line claim) are in 7.1 too.

Totals: 67 unchanged, 21 line moved only, 4 changed in substance (of 92).

| id | AMMO site (at 44674a3d) | Encoded fact | kind | Pumice source (9d126713) | Note at 9d126713 | At 769c7529 |
|---|---|---|---|---|---|---|
| SS-01 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2-5` | Pumice is a server: `pumice-server` or `rust_grpc_server.py` | path | pumice-rust/src/main.rs; rust_grpc_server.py | unchanged | unchanged: both launchers exist; flags only added |
| SS-02 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:30-36` | Schema text; example env `PUMICE_MY_OPT` | env | none: AMMO-internal (placeholder example) | unchanged | unchanged |
| SS-03 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:32` | Schema example flag `--enable-chunked-prefill` | flag | pumice-rust/src/main.rs; rust_grpc_server.py | unchanged | unchanged: `--enable-chunked-prefill` still in both parsers |
| SS-04 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:61-62` | Request uses `TokenizedInput.input_ids` | proto | pumice-rust/proto/pumice_engine.proto:33-35 | unchanged | unchanged |
| SS-05 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:80-86` | Freshness note: rebuild after Rust or confdb edits | build | pumice-rust/build.rs | unchanged | unchanged |
| SS-06 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:95` | RPCs `StartProfile` and `StopProfile` | proto | pumice-rust/proto/pumice_engine.proto:8-9 | unchanged | unchanged |
| SS-07 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:172-187` | `_ENGINE_SOURCE_GLOBS`: `pumice-rust/src/**/*.rs`, `pumice-rust/confdb/*.toml`, `pumice-rust/build.rs`, `pumice-rust/Cargo.toml`, `Cargo.toml` | path | pumice-rust/src; pumice-rust/confdb; pumice-rust/build.rs; pumice-rust/Cargo.toml; Cargo.toml | all present | unchanged: all five glob targets still exist |
| SS-08 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:172-187 (artifacts)` | Artifact names `libpumice_rust.so`, `.dylib`, `pumice_rust.so`, `pumice-server` | build | pumice-rust/Cargo.toml:8, :11-12 | unchanged | unchanged |
| SS-09 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:472-475` | `_METRICS_PORT_FLAG = "--metrics-port"`; counters `prefix_cache_hits_total`, `prefix_cache_queries_total` in `pumice-rust/src/metrics.rs` | flag | pumice-rust/src/main.rs; pumice-rust/src/metrics.rs:562, :569; pumice-rust/src/prometheus_exporter.rs | unchanged | changed: line moved only (metrics.rs:562->646, metrics.rs:569->653) |
| SS-10 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:479` | `_PREFIX_CACHE_OVERRIDE_TAG = "Prefix caching launch override:"` | log-string | src/pumice/config.py:25 | unchanged | unchanged: cited line identical |
| SS-11 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_PLATFORM)` | `ENGINE_INFRA_ENV` keeps `PUMICE_PLATFORM`; comment says config.py and config.rs read it | env | pumice-rust/src/config.rs:603; src/pumice/config.py:891 | unchanged | changed: line moved only (config.rs:603->633, config.py:891->908) |
| SS-12 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_VLLM_RUNNER_ONLY)` | `ENGINE_INFRA_ENV` keeps `PUMICE_VLLM_RUNNER_ONLY`; comment says setup.py reads it | env | setup.py:56 | unchanged | unchanged: cited line identical |
| SS-13 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_USE_VLLM_RUNNER)` | Comment names `PUMICE_USE_VLLM_RUNNER` as a strip candidate | env | pumice-rust/src/config.rs:575 | unchanged | changed: line moved only (config.rs:575->605) |
| SS-14 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_ORCHESTRATOR)` | Comment names `PUMICE_ORCHESTRATOR` | env | src/pumice/engine/model_runner.py:1471 | unchanged | unchanged: cited line identical |
| SS-15 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_NEURON_CONFIG)` | Comment names `PUMICE_NEURON_CONFIG` | env | src/pumice/engine/neuron_model_runner.py:507 | unchanged | unchanged |
| SS-16 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_NCCL_SO_PATH)` | Comment names `PUMICE_NCCL_SO_PATH` | env | src/pumice/utils/pynccl_wrapper.py:35 | unchanged | unchanged |
| SS-17 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_FLASH_ATTN_SRC_DIR)` | Comment names `PUMICE_FLASH_ATTN_SRC_DIR` | env | cmake/external_projects/vllm_flash_attn.cmake:27 | unchanged | unchanged |
| SS-18 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_NCCL_PORT)` | Comment names `PUMICE_NCCL_PORT` | env | pumice-rust/src/async_llm.rs:2269 | unchanged | changed: line moved only (async_llm.rs:2269->2511) |
| SS-19 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:842-861 (PUMICE_VLLM_DIST_PORT)` | Comment names `PUMICE_VLLM_DIST_PORT` | env | src/pumice/vllm_adapter/bootstrap.py:1572 | unchanged | changed: line moved only (bootstrap.py:1572->1664) |
| SS-20 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:901-913 (PUMICE_CACHE_ROOT)` | Fresh-cache mode sets `PUMICE_CACHE_ROOT` | env | src/pumice/compilation/backend.py:73 | unchanged | unchanged |
| SS-21 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:906-907` | Fresh-cache mode deliberately does NOT set `PUMICE_DISABLE_COMPILE_CACHE`, so saving still works within a run | env | src/pumice/compilation/backend.py:258 | unchanged | unchanged |
| SS-22 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:919-924` | Pumice source layout is `src/pumice` | path | src/pumice | unchanged | unchanged: `src/pumice` is still the package root |
| SS-23 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:996-1003` | `build.rs` embeds confdb, so a confdb edit needs a rebuild | build | pumice-rust/build.rs | unchanged | unchanged |
| SS-24 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1037-1083` | Staged extension globs (`pumice_rust*.so`, `_ops`) | build | pumice-rust/Cargo.toml:8; src/pumice/_ops | unchanged | unchanged: globs fit; setup.py adds one more `_ops` extension (`pumice_glm_indexer_ops`) |
| SS-25 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1277-1306` | Launcher order: `target/release/pumice-server`, then `rust_grpc_server.py`, then PATH | path | Cargo.toml:2 (workspace target dir); rust_grpc_server.py | unchanged | unchanged: `rust_grpc_server.py` still at repo root |
| SS-26 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1365-1396` | Server flags `--model`, `--port`, `--max-model-len`, `--tensor-parallel-size`, `--max-num-seqs`, `--model-runner` | flag | pumice-rust/src/main.rs; rust_grpc_server.py | unchanged | unchanged: all six flags exist (section 6.4) |
| SS-27 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1365-1396 (prefix flags)` | Prefix-cache flags `--enable-prefix-caching`, `--no-enable-prefix-caching` | flag | pumice-rust/src/main.rs:150-154; rust_grpc_server.py | unchanged | changed: line moved only (main.rs:150->192) |
| SS-28 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1396` | Strips a `--prefix-caching <value>` flag pair | flag | MISSING at 9d126713 | no such flag in main.rs or rust_grpc_server.py (also absent at 11a7899e); defensive strip, harmless | unchanged: `--prefix-caching` still absent |
| SS-29 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1410-1423` | default_model_runner: gpt_oss `[sm90]` sets `model_runner=vllm`; apply_model_config defaults cc to (9,0) | confdb | pumice-rust/confdb/gpt_oss.toml:21-36; src/pumice/config.py:968 | comment says "at 11a7899e" (:1412); confdb identical; apply_model_config moved | changed: line moved only (config.py:968->985); gpt_oss.toml identical; cc default (9, 0) at `model_config.py:47` |
| SS-30 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1410-1423 (log)` | Error text `--model-runner=vllm requires vLLM in this environment` | log-string | pumice-rust/src/async_llm.rs:1761 | unchanged | changed: line moved only (async_llm.rs:1761->1967) |
| SS-31 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1472-1497` | The Rust loader needs a model directory that holds `tokenizer.json` | log-string | pumice-rust/src/async_llm.rs:977 ("Failed to load tokenizer from") | unchanged | changed: line moved only (async_llm.rs:977->1135) |
| SS-32 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1512-1551` | DP control plane is `pumice-rust/src/dp_coordinator.rs` | doc-citation | pumice-rust/src/dp_coordinator.rs | file identical | changed: coordinator internals rewritten (wave numbering and completion); AMMO fact still holds |
| SS-33 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1528` | Pumice's fixed DP ports 13345/13346 | flag | rust_grpc_server.py:201, :203 (defaults 13345, 13346); pumice-rust/src/config.rs:403-405 | defaults, not fixed: both are flags | changed: line moved only (rust_grpc_server.py:201->216, rust_grpc_server.py:203->218, config.rs:403->414) |
| SS-34 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1512-1551 (DP flags)` | DP flags `--data-parallel-size`, `--data-parallel-rank`, `--data-parallel-port`, `--data-parallel-store-port`, `--data-parallel-address` | flag | pumice-rust/src/main.rs:353-375; rust_grpc_server.py:194-205 | unchanged | changed: the five flags are unchanged (`main.rs:353->395`, `rust_grpc_server.py:194->209`), but the DP rules in `config.rs` changed (`validate_native_dp`: vLLM runner now allows TP 1 to 64, needs `nnodes` 1) and `config.py` adds `_normalize_single_local_dp` (7.1) |
| SS-35 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1578-1600` | Stubs come from `pumice-rust/proto/pumice_engine.proto`; `pumice.grpc` comments out `prompt_tokens_cached` | proto | pumice-rust/proto/pumice_engine.proto:118, :131; src/pumice/grpc/pumice_engine.proto:119, :130 | unchanged | unchanged |
| SS-36 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1602` | Needs `grpcio>=1.80.0`, `protobuf>=6.31.1` | dep | src/pumice/grpc/pumice_engine_pb2_grpc.py:8 (GRPC 1.80.0); src/pumice/grpc/pumice_engine_pb2.py:5 (Protobuf 6.31.1) | unchanged | unchanged |
| SS-37 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1936-1972` | Generate request fields | proto | pumice-rust/proto/pumice_engine.proto | unchanged | unchanged |
| SS-38 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2436` | Comment: Pumice cannot disable prefix caching | flag | pumice-rust/src/block_manager.rs:198, :309; pumice-rust/src/main.rs:153 | claim wrong: BlockManager has a prefix_caching switch and `--no-enable-prefix-caching` sets it (also true at 11a7899e) | changed: line moved only (block_manager.rs:198->230, block_manager.rs:309->346, main.rs:153->195); AMMO comment is still wrong |
| SS-39 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2508` | Docstring: BlockManager always prefix-caches, no disable flag | flag | pumice-rust/src/block_manager.rs:198, :309; pumice-rust/src/main.rs:153 | claim wrong, same as :2436 | changed: line moved only (block_manager.rs:198->230, block_manager.rs:309->346, main.rs:153->195); AMMO docstring is still wrong |
| SS-40 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2465-2499` | EMF lines carry `_aws`; `spec_acceptance_rate` is a percent | log-string | pumice-rust/src/emf_metrics.rs:38; pumice-rust/src/metrics.rs:91 | unchanged | unchanged: cited line identical |
| SS-41 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2465-2499 (log)` | metrics.rs log fragment ` \| spec accept {:.1}%` | log-string | pumice-rust/src/metrics.rs:1034 | unchanged | changed: line moved only (metrics.rs:1034->1316) |
| SS-42 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2516-2530` | EMF gauge `prefix_cache_hit_rate` (percent) | log-string | pumice-rust/src/metrics.rs:96 | unchanged | unchanged: cited line identical |
| SS-43 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2576-2600` | /metrics counters `prefix_cache_hits_total`, `prefix_cache_queries_total` | log-string | pumice-rust/src/metrics.rs:562, :569 | unchanged | changed: line moved only (metrics.rs:562->646, metrics.rs:569->653) |
| SS-44 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:2826-2830` | Pumice `SamplingParams` has no stop field | proto | pumice-rust/proto/pumice_engine.proto:13 | still no stop field | unchanged |
| SS-45 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3176` | Pumice has no `vllm.profiler.wrapper` analogue | path | pumice-rust/src/engine.rs:2864-2880 (cuda_profiler_action is the only profile hook) | no `profiler.wrapper` in src or pumice-rust | changed: `engine.rs:2864` moved to `3117` and `cuda_profiler_action` now calls `model_runner.cuda_profiler_action`; AMMO fact holds, `profiler.wrapper` has 0 hits at both pins (7.1) |
| SS-46 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3227-3233` | `WorkerProcessMonitor` calls `os._exit(1)` on a dead worker | log-string | src/pumice/vllm_adapter/multinode.py:236, :259 | unchanged | changed: line moved only (multinode.py:259->260); failure path moved into a new `fail()` method |
| SS-47 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3227-3233 (psm)` | `_PSM_ATTACH_RACE_LINE = "FileNotFoundError: [Errno 2] No such file or directory: '/psm_"` | log-string | src/pumice/engine/shm_broadcast.py:70 (CPython SharedMemory names) | unchanged | unchanged |
| SS-48 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3264-3267` | Pumice spawns worker processes for TP ranks 1..N-1 and one per DP rank | log-string | src/pumice/vllm_adapter/multinode.py:284 ("TP worker ... exited unexpectedly") | unchanged | changed: line moved only (multinode.py:284->301) |
| SS-49 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3336-3340` | `HealthCheck` reply has `healthy` and `message` | proto | pumice-rust/proto/pumice_engine.proto:139-142 | unchanged | unchanged |
| SS-50 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3347-3350` | First launch on a fresh FlashInfer cache can JIT for about 11 min | doc-citation | CLAUDE.md (Blackwell first-launch note) | unchanged | unchanged |
| SS-51 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:3359-3364` | `StartProfile` and `StopProfile` calls | proto | pumice-rust/proto/pumice_engine.proto:8-9 | unchanged | unchanged |
| SS-52 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:4679` | Loader takes a model directory | path | pumice-rust/src/async_llm.rs:977 | unchanged | changed: line moved only (async_llm.rs:977->1135) |
| SS-53 | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:4693` | Flag `--draft-model` | flag | pumice-rust/src/main.rs; rust_grpc_server.py | unchanged | unchanged: `--draft-model` still in both parsers |
| SS-54 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:45-61` | `PROTO_FIELDS` the bench needs | proto | pumice-rust/proto/pumice_engine.proto | unchanged | unchanged |
| SS-55 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:64` | `PROTO_METHODS` Generate, HealthCheck, StartProfile, StopProfile | proto | pumice-rust/proto/pumice_engine.proto:6-9 | unchanged | unchanged |
| SS-56 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:69-78` | `SERVER_FLAGS` incl. `--metrics-port`, both prefix-cache flags | flag | pumice-rust/src/main.rs | unchanged | unchanged: all eight flags still in `main.rs` |
| SS-57 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:83-86` | `EMF_METRIC_NAMES` incl. `spec_acceptance_rate`, `prefix_cache_hit_rate` | log-string | pumice-rust/src/metrics.rs:91, :96 | unchanged | unchanged: cited line identical |
| SS-58 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:90-93` | `PROM_COUNTER_NAMES` hits and queries totals | log-string | pumice-rust/src/metrics.rs:562, :569 | unchanged | changed: line moved only (metrics.rs:562->646, metrics.rs:569->653) |
| SS-59 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:112-118` | Pins file paths | path | none: AMMO-internal | unchanged | unchanged |
| SS-60 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:155-157` | Repo marker `rust_grpc_server.py` | path | rust_grpc_server.py | unchanged | unchanged: launcher file still present |
| SS-61 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:264-282` | Reads flags from `pumice-rust/src/main.rs` | path | pumice-rust/src/main.rs | unchanged | unchanged: `main.rs` still hand-parses argv; flags only added |
| SS-62 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:289-293` | `parse_args` rejects `--help` with `Unknown argument: --help` | log-string | pumice-rust/src/main.rs:446 | unchanged | changed: line moved only (main.rs:446->512) |
| SS-63 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:361-366` | cmb-api Cargo.toml check | dep | pumice-rust/Cargo.toml:102 | unchanged | unchanged |
| SS-64 | `ai_cli_session/_ammo_shared/scripts/preflight_pumice.py:383-387` | `Failed to load tokenizer from <path>/tokenizer.json` | log-string | pumice-rust/src/async_llm.rs:977 | unchanged | changed: line moved only (async_llm.rs:977->1135) |
| SS-65 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:7` | Source branch `mainline` | path | CLAUDE.md (trunk rule) | unchanged | unchanged |
| SS-66 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:8` | Source commit `11a7899eb9350312ed61a61141652d8b277afb6d` | hash | repo root at commit 11a7899e (ancestor of 9d126713, 60 commits back) | unchanged | unchanged: proto identical (README commit literal must name the new pin) |
| SS-67 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:9` | Proto `pumice-rust/proto/pumice_engine.proto`, last change `1a1bcdbfe1929f00e19e24277b10dd9042a5413b` | hash | pumice-rust/proto/pumice_engine.proto | last change still 1a1bcdbf | unchanged |
| SS-68 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:10` | Proto SHA-256 `9b4dbe3d...` | hash | pumice-rust/proto/pumice_engine.proto | SHA-256 still 9b4dbe3d... | unchanged |
| SS-69 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:12` | `prompt_tokens_cached` is tag 6 and tag 7; the Python copy comments it out | proto | pumice-rust/proto/pumice_engine.proto:118, :131; src/pumice/grpc/pumice_engine.proto:119, :130 | unchanged | unchanged |
| SS-70 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:16-17` | Generator grpcio-tools 1.80.0, protobuf 6.31.1; CUDA lock has protobuf 6.33.6, grpcio 1.83.1 | dep | src/pumice/grpc/pumice_engine_pb2_grpc.py:8; src/pumice/grpc/pumice_engine_pb2.py:5 (the lock is in the external base image) | unchanged | unchanged |
| SS-71 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:27` | `PROTO_DIR=/Users/jinhun/AWSMantle/src/AWSMantlePumice/pumice-rust/proto` | path | pumice-rust/proto | unchanged | unchanged: proto directory identical |
| SS-72 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/__init__.py:1-10` | Package doc: stubs generated from the pumice-rust proto | proto | pumice-rust/proto/pumice_engine.proto | unchanged | unchanged |
| SS-73 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/pumice_engine_pb2.py:5` | Generated with Protobuf 6.31.1 | dep | src/pumice/grpc/pumice_engine_pb2.py:5 | unchanged | unchanged |
| SS-74 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/pumice_engine_pb2.py:27` | Proto package `pumice.grpc.engine` | proto | pumice-rust/proto/pumice_engine.proto:3 | unchanged | unchanged |
| SS-75 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/pumice_engine_pb2_grpc.py:8` | `GRPC_GENERATED_VERSION = '1.80.0'` | dep | src/pumice/grpc/pumice_engine_pb2_grpc.py:8 | unchanged | unchanged |
| SS-76 | `ai_cli_session/_ammo_shared/scripts/pumice_pb/pumice_engine_pb2_grpc.py:28-55` | RPC paths `/pumice.grpc.engine.PumiceEngine/<Method>` | proto | pumice-rust/proto/pumice_engine.proto:3-9 | unchanged | unchanged |
| SS-77 | `ai_cli_session/_ammo_shared/scripts/runtime_ops.py:413-416` | Reads `server_stack_fingerprint.pumice_commit` | hash | repo root at commit 11a7899e (ancestor of 9d126713, 60 commits back) | unchanged | changed: pin moves to 769c7529; the fingerprint must show it |
| SS-78 | `ai_cli_session/_ammo_shared/scripts/ncu_sanity_driver.py:32-44` | Cites `pumice-rust/src/async_llm.rs` and `pumice-rust/src/grpc_server.rs` | doc-citation | pumice-rust/src/async_llm.rs; pumice-rust/src/grpc_server.rs | unchanged | unchanged: both files exist; `StartProfile` handler at `grpc_server.rs:450` |
| SS-79 | `ai_cli_session/_ammo_shared/scripts/ncu_sanity_driver.py:172` | `env.setdefault("PUMICE_DISABLE_LOG_STATS", "1")` | env | MISSING at 9d126713 | not read anywhere in Pumice (also absent at 11a7899e); the setting is inert | unchanged: name still read nowhere in Pumice (0 files at both pins) |
| SS-80 | `ai_cli_session/_ammo_shared/scripts/hook_cmd_classify.py:426-429` | Engine entry points `rust_grpc_server.py`, `pumice-server` | path | rust_grpc_server.py; pumice-rust/Cargo.toml:11-12 | unchanged | unchanged: launcher file and `[[bin]]` still present |
| SS-81 | `ai_cli_session/_ammo_shared/scripts/hook_cmd_classify.py:442` | Accelerator regex includes `pumice` | path | none: AMMO-internal | unchanged | unchanged |
| SS-82 | `ai_cli_session/_ammo_shared/scripts/hook_cmd_classify.py:452` | Bench script name | path | none: AMMO-internal | unchanged | unchanged |
| SS-83 | `ai_cli_session/_ammo_shared/scripts/hook_cmd_classify.py:465` | `cargo build` compiles the pumice-rust extension | build | pumice-rust/Cargo.toml | unchanged | unchanged |
| SS-84 | `ai_cli_session/_ammo_shared/scripts/hook_cmd_classify.py:489` | Import regex `pumice\|pumice_rust` | path | pumice-rust/Cargo.toml:8; src/pumice | unchanged | unchanged |
| SS-85 | `ai_cli_session/_ammo_shared/scripts/verify_validation_gates.py` | Runtime profile constants only | build | none: AMMO-internal | unchanged | unchanged |
| SS-87 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:40` | `NEURON_MAX_KV_SEGMENT = 4096` | flag | src/pumice/neuron_inference_core/functional/attention/attention_segmented_cte.py:25 (SUPPORTED_SEGMENT_SIZES) | unchanged | unchanged |
| SS-88 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:41-45` | `NEURON_FULL_BLOCK_TABLES_ENV = "PUMICE_RUST_EMIT_FULL_BLOCK_TABLES"`; the Neuron e2e smoke conftest sets it to 1 (nrta 1006) | env | pumice-rust/src/scheduler.rs:1301; pumice-rust/src/block_manager_grouped.rs:1038; tests/neuron/e2e/smoke/conftest.py:92 | unchanged | changed: line moved only (scheduler.rs:1301->1397, block_manager_grouped.rs:1038->1116) |
| SS-89 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:214-218` | Neuron baseline_env scaffold mirrors the setdefault in Pumice's Neuron conftest | env | tests/neuron/e2e/smoke/conftest.py:92 | unchanged | unchanged |
| SS-90 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:467-473` | pumice-* profiles use runner `pumice_grpc_bench` with `server_cmd` `pumice-server` | build | pumice-rust/Cargo.toml:11-12 ([[bin]] pumice-server) | unchanged | unchanged |
| SS-91 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:600-602` | kv_segment sizes 512, 1024, 2048, 4096 | flag | src/pumice/neuron_inference_core/functional/attention/attention_segmented_cte.py:25 | unchanged | unchanged |
| SS-92 | `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:675-685` | gpt-oss-20b smoke uses kv_segment 4096 | flag | src/pumice/neuron_inference_core/functional/attention/attention_segmented_cte.py:25 | unchanged | unchanged |
| SS-93 | `ai_cli_session/.claude/skills/ammo/scripts/ammo_state.py:691-697` | Runtime profile aliases | build | none: AMMO-internal | unchanged | unchanged |

### 7.1 Evidence for the hand verdicts

These rows have a verdict set by hand: SS-01, 03, 07, 22, 24, 25, 26, 28, 32, 34, 45, 46, 53, 56, 60, 61, 66, 71, 77, 78, 79, 80. Each block is a command and its output.

Entry points, crate layout, and glob targets exist at both pins (SS-01, 07, 22, 24, 25, 60, 61, 71, 78, 80). `[[bin]] pumice-server` is `Cargo.toml` lines 11-12:

```
$ for c in 9d126713 769c7529; do echo "== $c"; git ls-tree --name-only $c -- rust_grpc_server.py Cargo.toml pumice-rust/Cargo.toml pumice-rust/build.rs pumice-rust/src/main.rs pumice-rust/src/async_llm.rs pumice-rust/src/grpc_server.rs pumice-rust/src/dp_coordinator.rs src/pumice pumice-rust/confdb pumice-rust/proto | tr '\n' ' '; echo; git show ${c}:pumice-rust/Cargo.toml | sed -n 8,12p; done
== 9d126713
Cargo.toml pumice-rust/Cargo.toml pumice-rust/build.rs pumice-rust/confdb pumice-rust/proto pumice-rust/src/async_llm.rs pumice-rust/src/dp_coordinator.rs pumice-rust/src/grpc_server.rs pumice-rust/src/main.rs rust_grpc_server.py src/pumice 
name = "pumice_rust"
crate-type = ["cdylib", "rlib"]

[[bin]]
name = "pumice-server"
== 769c7529
Cargo.toml pumice-rust/Cargo.toml pumice-rust/build.rs pumice-rust/confdb pumice-rust/proto pumice-rust/src/async_llm.rs pumice-rust/src/dp_coordinator.rs pumice-rust/src/grpc_server.rs pumice-rust/src/main.rs rust_grpc_server.py src/pumice 
name = "pumice_rust"
crate-type = ["cdylib", "rlib"]

[[bin]]
name = "pumice-server"
```

Flags in both parsers (SS-03, 26, 53, 56) and the absent `--prefix-caching` (SS-28). Counts are the number of quoted literals, `py` for `rust_grpc_server.py` and `rs` for `main.rs`:

```
$ for f in --enable-chunked-prefill --model --port --max-model-len --tensor-parallel-size --max-num-seqs --model-runner --draft-model --metrics-port --enable-prefix-caching --no-enable-prefix-caching --prefix-caching; do printf "%s" "$f"; for c in 9d126713 769c7529; do printf "  %s: py=%s rs=%s" "$c" "$(git show ${c}:rust_grpc_server.py | grep -c -F -e "\"$f\"")" "$(git show ${c}:pumice-rust/src/main.rs | grep -c -F -e "\"$f\"")"; done; echo; done
--enable-chunked-prefill  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--model  9d126713: py=1 rs=2  769c7529: py=1 rs=4
--port  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--max-model-len  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--tensor-parallel-size  9d126713: py=1 rs=8  769c7529: py=1 rs=9
--max-num-seqs  9d126713: py=1 rs=1  769c7529: py=1 rs=4
--model-runner  9d126713: py=1 rs=1  769c7529: py=1 rs=6
--draft-model  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--metrics-port  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--enable-prefix-caching  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--no-enable-prefix-caching  9d126713: py=1 rs=1  769c7529: py=1 rs=1
--prefix-caching  9d126713: py=0 rs=0  769c7529: py=0 rs=0
```

The five DP flags of SS-34 keep their names in both parsers. Their lines moved, and the DP rules around them changed (2.1 and 6.3):

```
$ for c in 9d126713 769c7529; do echo "== $c"; for f in --data-parallel-size --data-parallel-rank --data-parallel-port --data-parallel-store-port --data-parallel-address; do printf "%s py=%s rs=%s\n" "$f" "$(git show ${c}:rust_grpc_server.py | grep -c -F -e "\"$f\"")" "$(git show ${c}:pumice-rust/src/main.rs | grep -c -F -e "\"$f\"")"; done; done
== 9d126713
--data-parallel-size py=1 rs=1
--data-parallel-rank py=1 rs=1
--data-parallel-port py=1 rs=1
--data-parallel-store-port py=1 rs=1
--data-parallel-address py=1 rs=1
== 769c7529
--data-parallel-size py=1 rs=4
--data-parallel-rank py=1 rs=1
--data-parallel-port py=1 rs=1
--data-parallel-store-port py=1 rs=1
--data-parallel-address py=1 rs=1
```

SS-24: `setup.py` gains one extension. SS-79: the env name is read nowhere:

```
$ git diff 9d126713 769c7529 -- setup.py | grep -E '^[+-].*CMakeExtension'; for c in 9d126713 769c7529; do echo "$c PUMICE_DISABLE_LOG_STATS files: $(git grep -l PUMICE_DISABLE_LOG_STATS $c | wc -l | tr -d ' ')"; done
+        CMakeExtension("pumice._ops.pumice_glm_indexer_ops"),
9d126713 PUMICE_DISABLE_LOG_STATS files: 0
769c7529 PUMICE_DISABLE_LOG_STATS files: 0
```

SS-32: the cited file is the DP control plane. It changed in substance (wave numbering and completion). The AMMO fact only needs the file to exist:

```
$ git diff --shortstat 9d126713 769c7529 -- pumice-rust/src/dp_coordinator.rs; git diff 9d126713 769c7529 -- pumice-rust/src/dp_coordinator.rs | grep -E '^[+-]' | grep -E 'next_wave|current_wave|WaveComplete|fn (start_wave|complete_wave)' | head -8
 1 file changed, 181 insertions(+), 66 deletions(-)
+        current_wave: u64,
+    WaveComplete { engine_index: u32, wave: u64 },
-    next_wave: u64,
+    current_wave: u64,
-            next_wave: 0,
+            current_wave: 0,
+    fn start_wave(&mut self, engine_index: u32, wave: u64) -> Option<CoordMsg> {
+        if engine_index as usize >= self.engines.len() || wave < self.current_wave {
```

SS-45: `profiler.wrapper` is read nowhere at either pin, so the AMMO fact holds. The engine hook has the same name on a different line, and it now delegates to the model runner:

```
$ for c in 9d126713 769c7529; do echo "$c profiler.wrapper hits: $(git grep -n -F 'profiler.wrapper' $c -- src pumice-rust/src | wc -l | tr -d ' ')"; done; git grep -n 'fn cuda_profiler_action\|self.model_runner.cuda_profiler_action' 769c7529 -- pumice-rust/src/engine.rs pumice-rust/src/model_runner.rs | sed 's/^769c7529://' 
9d126713 profiler.wrapper hits: 0
769c7529 profiler.wrapper hits: 0
pumice-rust/src/engine.rs:3117:    fn cuda_profiler_action(&self, start: bool) {
pumice-rust/src/engine.rs:3121:            let result = self.model_runner.cuda_profiler_action(py, start);
pumice-rust/src/model_runner.rs:429:    fn cuda_profiler_action(&self, py: Python<'_>, start: bool) -> PyResult<()> {
pumice-rust/src/model_runner.rs:684:    fn cuda_profiler_action(&self, py: Python<'_>, start: bool) -> PyResult<()> {
```

SS-46: `os._exit(1)` stays in `WorkerProcessMonitor._exit_node`. The failure body moved into a new `fail()` method, so the cited second line moved by one. The behaviour is the same:

```
$ for c in 9d126713 769c7529; do echo "$c os._exit(1) hits: $(git show ${c}:src/pumice/vllm_adapter/multinode.py | grep -c 'os._exit(1)')"; done; git show 769c7529:src/pumice/vllm_adapter/multinode.py | grep -n 'def fail\|self.fail(reason' 
9d126713 os._exit(1) hits: 2
769c7529 os._exit(1) hits: 2
272:    def fail(self, reason: str, failed_process: Any = None) -> None:
304:                self.fail(reason, failed)
```

SS-66 and SS-71: the proto directory and the Python copy are identical (section 3). SS-66 names the old commit in a README, so the README text moves with the pin. SS-77 reads `pumice_commit` from the fingerprint, which now has to show the new pin:

```
$ git diff --quiet 9d126713 769c7529 -- pumice-rust/proto src/pumice/grpc && echo 'proto and grpc: identical'; git rev-parse 769c7529; sed -n 413,416p /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/scripts/runtime_ops.py
proto and grpc: identical
769c752995b8a39b27df028176411d17c99ff441
        * ``server_stack_fingerprint.pumice_commit``: the Pumice commit baked
          into the server image (``shared/pumice_pins.py``). The session
          worktree branches from it. It does not change when the worktree
          gets new commits.
```

## 8. What AMMO must change

Answer: eight edits are needed for the new pin. Four break something if skipped. One is a data reseed with its test. Three are text only. Seven checks need no edit. Four old defects stay open and do not come from the delta.

Tags. `Break`: a gate or test fails, or a result is wrong, at the new pin. `Data`: regenerate a list. `Text`: comment or prose only. `Plugin copy` names the file in `generated-plugins/ammo` that carries the same fact (conventions sections 1 and 6; write sets in plan.md wave 1). Paths are in the ammo-server repo unless marked.

### A. Must change

| # | Tag | AMMO site | Edit | Pumice evidence | Plugin copy |
|---|---|---|---|---|---|
| 1 | Break | `shared/pumice_pins.py:31`, check at 75-80 | `REQUIRED_VLLM` from `0.30.0` to `0.32.0` | 4.1, 4.5 | `scripts/pumice_pins.py`: no literal, compare with `pumice.config.REQUIRED_VLLM_VERSION` |
| 2 | Break | `shared/pumice_pins.py:36` | `PUMICE_COMMIT` to the new full SHA | 4.5 | `scripts/pumice_pins.py` |
| 3 | Break | `tests/unit/test_pumice_pins.py:165,171` | expected values follow items 1 and 2 | 4.5 | `tests/test_pumice_pins.py` |
| 4 | Break | `ai_cli_session/_ammo_shared/scripts/serving_benchmark_adapter.py:27-35` | add a `0.32.0` row to `WHEEL_BLOBS` | below | `scripts/serving_benchmark_adapter.py` |
| 5 | Data, Break | `ai_cli_session/_ammo_shared/hooks/pumice_env_allowlist.txt`, `tests/unit/test_env_default_guard_hook.py:229-234` | reseed to 144 names; test expects 144 | 5 | `hooks/pumice_env_allowlist.txt` |
| 6 | Text | `ai_cli_session/_ammo_shared/scripts/pumice_pb/README.md:8` | commit literal names the old pin | 3 | `scripts/pumice_pb/README.md` |
| 7 | Text | `ai_cli_session/_ammo_shared/scripts/run_pumice_grpc_bench.py:1416` | comment says "gpt_oss at 9d126713" | 4.4 | `scripts/run_pumice_grpc_bench.py` |
| 8 | Text | reference docs, grep below | replace the vLLM version literal | 4.1 | `references/*.md` |

Item 1. `check_pins()` fails on any mismatch with Pumice's own value, so the old literal makes it fail wherever `pumice` imports at the new pin:

```
$ sed -n 75,80p /Users/jinhun/GitHub/ammo-server/shared/pumice_pins.py
    pumice_required_vllm = getattr(pumice_config, "REQUIRED_VLLM_VERSION", None)
    if pumice_required_vllm != REQUIRED_VLLM:
        return False, (
            f"pumice.config.REQUIRED_VLLM_VERSION {pumice_required_vllm!r} "
            f"does not match REQUIRED_VLLM {REQUIRED_VLLM!r}"
        )
```

Item 3. The test lines that hold the old values:

```
$ sed -n '165p;171p' /Users/jinhun/GitHub/ammo-server/tests/unit/test_pumice_pins.py
        (pumice_pins.REQUIRED_VLLM, "0.30.0"),
        (pumice_pins.PUMICE_COMMIT, "9d126713c03dcc88883b85c3d3a857b89982c957"),
```

Item 4. With vLLM 0.32.0 installed the adapter stops at `verify_wheel` (lines 79-81 of the same file). The row needs the git blob SHA-1 of `vllm/benchmarks/serve.py` and `vllm/benchmarks/lib/endpoint_request_func.py` from an image that ships the 0.32.0 wheel. Pumice holds no copy of them:

```
$ sed -n 27,35p /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/scripts/serving_benchmark_adapter.py; sed -n 77,81p /Users/jinhun/GitHub/ammo-server/ai_cli_session/_ammo_shared/scripts/serving_benchmark_adapter.py; for c in 9d126713 769c7529; do echo "$c: $(git grep -l -e 'endpoint_request_func' -e 'benchmarks/serve.py' $c | wc -l | tr -d ' ') files in Pumice mention them"; done
# Installed-wheel releases qualified for preserve_request_ids, keyed by the
# exact vllm.__version__. Values are the git-blob SHA-1s of
# (vllm/benchmarks/serve.py, vllm/benchmarks/lib/endpoint_request_func.py),
# measured in the image that ships the wheel. 0.30.0: pumice-cuda-base:local
# sha256:b6334f8fe2c086f0f4adb0739cc31f92915d2a6ec587ca5eef6b9386327efe63.
WHEEL_BLOBS = {
    '0.30.0': ('0292ff939a553fcc78f9048e6e22d95d46633a12',
               '54c6ce63b0fa5b961fe4887aeff4d9ee5666edb2'),
}
        if Path(module.__file__).resolve() != path:
            raise ContractError('benchmark import is outside the pinned vLLM wheel')
    pinned = WHEEL_BLOBS.get(getattr(vllm, '__version__', None))
    if pinned is None:
        raise ContractError('unsupported benchmark wheel version; qualify a new adapter first')
9d126713: 0 files in Pumice mention them
769c7529: 0 files in Pumice mention them
```

`tests/unit/test_serving_benchmark_adapter_wheel.py` keys on the `0.30.0` row (lines 61, 89, 112). Keep that row for the test, or move the test to the new row. That test file is not in the server-side test list of conventions section 10, so the team lead decides who owns it.

Item 5. Reseed with the recipe in section 5 at the new pin. Update the header (lines 12-20: commit and count) and the count lines in the test (229, 232, 234). The 8 `VLLM_*` names stay.

Item 6. The proto and its Python copy are identical at both pins (section 3). Do not regenerate. Replace the commit literal at line 8, keep lines 9-10 (last change and SHA-256 still hold), and record the section 3 command with its empty output.

Item 8. Every hit of the old version literal in the Claude-dialect tree, which is the plugin copy source (conventions section 10). The matched text is cut to the words around the literal:

```
$ git -C /Users/jinhun/GitHub/ammo-server grep -n -o -E '.{0,34}0\.30\.0.{0,16}' -- ai_cli_session/.claude/skills/ammo | sed 's#ai_cli_session/.claude/skills/ammo/##'
references/accelerator-binding.md:11:r gpt-oss it is the embedded vLLM 0.30.0 runner. | `/opt
references/e2e-delta-math.md:187:-oss on CUDA): the installed vLLM 0.30.0 package.
references/e2e-latency-guide.md:37:A it is `vllm`, the embedded vLLM 0.30.0 runner). On Neu
references/nsys-profiling-guide.md:146:nels come from the installed vLLM 0.30.0 package. Host-s
references/serving-measurement.md:91:hat `shared/pumice_pins.py` pins (0.30.0) in `/opt/pumic
references/torch-compile-contract.md:12:e model runs in the embedded vLLM 0.30.0 runner. The com
references/torch-compile-contract.md:12: Verify each one in the installed 0.30.0 package, not fr
references/torch-compile-contract.md:277:re relative to the installed vLLM 0.30.0 package. Pumice
references/torch-compile-contract.md:279:| Component | vLLM runner (vLLM 0.30.0) | Pumice runne
references/validation-defaults.md:32:nner `pumice`, the installed vLLM 0.30.0 package for run
references/validation-defaults.md:241:in `src/pumice/ir/ops/`; the vLLM 0.30.0 tests for runne
references/validation-defaults.md:455: runner `vllm`, the vLLM version (0.30.0)
scripts/serving_benchmark_adapter.py:30:n the image that ships the wheel. 0.30.0: pumice-cuda-ba
scripts/serving_benchmark_adapter.py:33:    '0.30.0': ('0292ff939a5
```

The reference docs hold 11 lines in 7 files: `accelerator-binding.md:11`, `e2e-delta-math.md:187`, `e2e-latency-guide.md:37`, `nsys-profiling-guide.md:146`, `serving-measurement.md:91`, `torch-compile-contract.md:12,277,279`, `validation-defaults.md:32,241,455`. Replace each literal with "the pinned version" (conventions section 6). Their Pumice-side claims about `backend.py`, `model_runner.py`, and `registry.py` still hold (sections 2.3 to 2.5). The `serving_benchmark_adapter.py` hits are item 4. The `_ammo_shared` and `.codex` trees carry the same text:

```
$ git -C /Users/jinhun/GitHub/ammo-server grep -c -E '0\.30\.0' -- ai_cli_session/_ammo_shared ai_cli_session/.codex | sed 's#ai_cli_session/##' | awk -F: '{n+=$2; f++} END {print n " hits in " f " files"}'
23 hits in 15 files
```


### B. Checked, no edit needed

- `shared/pumice_pins.py:32` `REQUIRED_TORCH` stays `2.13.0+cu130`. Pumice requires `torch==2.13.0` at `pyproject.toml:127` (section 4.2).
- Every flag in `run_pumice_grpc_bench.py` exists at the new pin: lines 1369-1378, 1384-1386, 1536-1559, 4697 (section 6.4).
- `preflight_pumice.py:70-77` `SERVER_FLAGS`: all eight exist (section 6.4).
- gRPC contract: `preflight_pumice.py` `PROTO_FIELDS` and `PROTO_METHODS` rest on an identical proto (section 3).
- Metric names `spec_acceptance_rate`, `prefix_cache_hit_rate`, `prefix_cache_hits_total`, `prefix_cache_queries_total` still exist (rows SS-40 to SS-43, SS-57, SS-58). Number of Pumice files that hold each name:

```
$ for n in spec_acceptance_rate prefix_cache_hit_rate prefix_cache_hits_total prefix_cache_queries_total; do printf '%s' "$n"; for c in 9d126713 769c7529; do printf '  %s=%s' "$c" "$(git grep -l -w -F $n $c -- src pumice-rust/src | wc -l | tr -d ' ')"; done; echo; done
spec_acceptance_rate  9d126713=2  769c7529=2
prefix_cache_hit_rate  9d126713=3  769c7529=3
prefix_cache_hits_total  9d126713=1  769c7529=1
prefix_cache_queries_total  9d126713=1  769c7529=1
```

- `docker/pumice-sm80-gpt-oss.patch` applies cleanly to the new `gpt_oss.toml` (section 4.4). It stays needed: upstream still has no `[sm80]` row, and L40S reports major 8.
- The five `_ENGINE_SOURCE_GLOBS` targets of the freshness gate (`run_pumice_grpc_bench.py:173-179`) still exist (row SS-07, 7.1).

### C. Old defects found on the way (not caused by the delta)

- `ai_cli_session/.claude/skills/ammo/scripts/new_target.py:451-452` writes `--distributed-executor-backend external_launcher` for DP above 1. Neither Pumice parser knows it (section 6.4), so the server stops at launch. The plan gives this file to the `new_target.py` write set (plan.md wave 1).
- `run_pumice_grpc_bench.py:1400` strips `--prefix-caching`, which never existed. Harmless.
- `ai_cli_session/_ammo_shared/scripts/ncu_sanity_driver.py:172` sets `PUMICE_DISABLE_LOG_STATS`. Pumice reads no such name at either pin (section 5 and the SS-79 row). Inert.
- Touchpoint rows SS-38 and SS-39 (`run_pumice_grpc_bench.py` prefix-cache comments) claim Pumice cannot disable prefix caching. That is wrong at both pins.

### D. Awareness

- The new pin drops two local fixes that the old pin had: DSV4 HMA metadata in the idle dummy forward (idle DP ranks), and the DSA warmup workspace argument order. A DeepSeek-V4 or DSA campaign with data parallel can hit the old failures again. Section 1.2.
- `resolve_server_exe` (`run_pumice_grpc_bench.py:1280`) prefers the Rust binary when built. Three new flags exist only there (section 6.3). A campaign that wants them must set `bench.server_cmd` to the binary or build it.
- `plan_dp_cohort` (`run_pumice_grpc_bench.py:1507`) still fits the DP contract: one process per rank, flags unchanged. Pumice now also allows TP above 1 with DP on the vLLM runner (section 6.3).
- The vLLM extension package `pumice_vllm_ext` now ships in the Pumice wheel (`setup.py` `package_dir`) and registers as a vLLM plugin entry point. `pyproject.toml:209` says it stays inert unless `PUMICE_VLLM_EXTENSIONS` is set. The bench runner strips `PUMICE_*` from each arm (`_sanitize_engine_op_env`, `run_pumice_grpc_bench.py:864`), so an arm only gets it if the target declares it.
- Pumice's own runtime check on the vLLM version is a warning (section 4.1). AMMO's exact match is the stricter and only gate.
- The Pumice checkout on disk is 96 commits behind the new pin and carries the two local fixes (section 1.2). A campaign engine root must be at the new pin before preflight. Read pin facts with `git show 769c7529:<path>`.
- `bench.extra_args` may now carry six new flags (section 6.4). No AMMO script needs them.
- Server-only files still name the old pin and are outside the plugin source lists (conventions section 10). They need an edit only if the server image is re-pinned:

```
$ git -C /Users/jinhun/GitHub/ammo-server grep -c -E '9d126713|0\.30\.0' -- shared/pumice_pd_setup.py tests/unit/test_dockerfile_pumice_cuda.py tests/unit/test_pumice_deploy_scripts.py tests/unit/test_accelerator_provider.py tests/unit/test_pumice_pd_setup.py tests/unit/test_neuron_deploy_contract.py
shared/pumice_pd_setup.py:2
tests/unit/test_accelerator_provider.py:2
tests/unit/test_dockerfile_pumice_cuda.py:3
tests/unit/test_neuron_deploy_contract.py:2
tests/unit/test_pumice_deploy_scripts.py:7
tests/unit/test_pumice_pd_setup.py:6
```


## Appendix. Script behind section 7

`/tmp/pdelta_rowcheck.py` reads the touchpoints table and compares each cited Pumice line at the two commits. It runs only `git show` and `git diff --quiet`, so it does not change the Pumice checkout. To repeat section 7, save the block below as `/tmp/pdelta_rowcheck.py`, then run `python3 -I /tmp/pdelta_rowcheck.py > /tmp/pdelta_rows.json`:

```python
#!/usr/bin/env python3
"""Check Pumice-side facts for AMMO touchpoint rows under scripts/.

Reads the touchpoints table, keeps the SS rows whose AMMO site is under
ai_cli_session/_ammo_shared/scripts/ or ai_cli_session/.claude/skills/ammo/scripts/,
and for every Pumice file:line citation compares the cited line at OLD with
the same text at NEW. Read-only: uses only `git show` and `git diff --quiet`.
"""
import json
import re
import subprocess
import sys

REPO = "/Users/jinhun/AWSMantle/src/AWSMantlePumice"
TOUCH = "/Users/jinhun/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md"
OLD, NEW = "9d126713", "769c7529"
SITE_OK = ("ai_cli_session/_ammo_shared/scripts/", "ai_cli_session/.claude/skills/ammo/scripts/")
PATH_RE = re.compile(r"[A-Za-z0-9_./-]+\.(?:rs|py|toml|cmake|proto|md|json|sh)")
CITE_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:rs|py|toml|cmake|proto|md|json|sh))((?::\d+(?:-\d+)?(?:,\s*)?)+)")


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True)


def show(rev, path):
    r = git("show", f"{rev}:{path}")
    return r.stdout.split("\n") if r.returncode == 0 else None


def changed(path):
    return git("diff", "--quiet", OLD, NEW, "--", path).returncode != 0


def resolve(path):
    for cand in (path, "pumice-rust/src/" + path, "src/pumice/" + path):
        if show(OLD, cand) is not None or show(NEW, cand) is not None:
            return cand
    return None


def rows():
    for line in open(TOUCH, encoding="utf-8"):
        if not re.match(r"^\| SS-\d+ ", line):
            continue
        cols = [c.strip() for c in line.replace("\\|", "\x00").split("|")]
        # cols: '', id, site, fact, kind, source, copies, note, ''
        rid, site, fact, kind, source, copies, note = cols[1:8]
        site_path = site.strip("`").split(":")[0].split(" ")[0]
        if not site_path.startswith(SITE_OK):
            continue
        yield {
            "id": rid,
            "site": site.replace("\x00", "\\|"),
            "fact": fact.replace("\x00", "\\|"),
            "kind": kind,
            "source": source.replace("\x00", "\\|"),
            "note": note.replace("\x00", "\\|"),
        }


def analyse(row):
    src = row["source"]
    cited = []
    for m in CITE_RE.finditer(src):
        path, spec = m.group(1), m.group(2)
        real = resolve(path)
        if real is None:
            cited.append({"path": path, "real": None})
            continue
        for a, b in re.findall(r":(\d+)(?:-(\d+))?", spec):
            cited.append({"path": real, "lo": int(a), "hi": int(b) if b else int(a)})
    cited_paths = set()
    for p in PATH_RE.findall(src):
        real = resolve(p)
        if real:
            cited_paths.add(real)
    out = {"changed_files": sorted(p for p in cited_paths if changed(p)), "remaps": []}
    for c in cited:
        if c.get("real", 1) is None:
            continue
        p = c["path"]
        if not changed(p):
            continue
        old, new = show(OLD, p), show(NEW, p)
        seg = old[c["lo"] - 1:c["hi"]]
        rep = next((s for s in seg if s.strip()), "")
        hits = [i + 1 for i, s in enumerate(new) if s == rep] if rep.strip() else []
        out["remaps"].append({
            "path": p, "lo": c["lo"], "hi": c["hi"],
            "in_place": new[c["lo"] - 1:c["hi"]] == seg,
            "new_lines": hits[:4],
        })
    return out


if __name__ == "__main__":
    result = []
    for row in rows():
        a = analyse(row)
        row.update(a)
        result.append(row)
    json.dump(result, sys.stdout, indent=1)
```

