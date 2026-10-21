# Campaign kernel measurement — Synthesize a per-shape stub

**This is an LLM-executed procedure, not a script.** It is the FIRST gate of the Step-6 kernel
loop. Its input is the **target kernel** (named by the analyze-stage `kernel_target`); its output
is a validated, on-device-**green** pytest stub the loop then measures. Stub *synthesis* is a
per-kernel mapping (which `build_*` / `_run_*_test` / torch-reference to use, the builder's kwargs,
the shape derivation) that varies by kernel family — the LLM does it by READING the installed
source, which is general and low-brittleness. The deterministic anchors stay scripts:
`stage_kernel_tree.py` (build+self-verify the tree), `measure.py` (the sole pass/fail+cycles oracle,
D19), `propose_node_bracket.py` (node selection), `kernel_loop_scaffold.py`. Campaign tasks own admission and state.

**Why a procedure and not a Python generator:** a Python generator needs a hardcoded recipe per
kernel family (imports, kwarg map, enum set, shape formula) — brittle and incomplete. The LLM reads
the family's actual `test_*_common` helpers + torch ref + an example vector and adapts, so ANY
kernel with installed test helpers works with no code change. **Correctness is not trusted to the
LLM: it is gated by `measure.py` (green compile+infer + numeric check vs the kernel's own torch
reference) and an optional cost-model cross-check.** A wrong stub fails the gate.

---

## Input contract — the `kernel_target` (from the analyze stage / profile-analysis)

```json
{
  "kernel_name": "mlp_cte",
  "family": "mlp",                              // maps to nkilib_testing.tests.<...>.<family>
  "kernel_entry": "nkilib_src.nkilib.core.mlp.mlp::mlp",   // module::symbol (repo namespace)
  "source_file": "core/mlp/mlp_cte/basic/mlp_cte_basic_projection.py",  // the file the loop edits
  "shape": {"hidden": 5376, "intermediate": 21504, "tp": 32, "cp": 1, "seqlen": 8192,
            "dtype": "bfloat16", "act_fn": "GELU"},          // FULL per-device dims + parallelism
  "profiler_db": "agent_artifacts/profiles/<run>/ingested/<leg>_device/<cap>",  // the profile INPUT (collect.profiler_db): a full-model profiler_db from the current campaign profile. qor data is always generated internally regardless.
  "regime": "prefill/CTE",
  "source_addressable": true,                   // false => return to campaign configuration work; confirm against the profile
  "dispatch_fingerprint": "agent_artifacts/.../dispatch_fingerprint.json"   // MANDATORY compiler dispatch evidence. The campaign refuses a target whose shape does not match it completely.
}
```

`$NKILIB_PKGNAME` (e.g. `nkilib_src.nkilib`), `$NKILIB_TESTING_DIR`, `$TREE` come from
`stage_kernel_tree.py`'s manifest (`stage_manifest.json`).

## Produce the dispatch_fingerprint FIRST (autobridge) — do NOT hand-write `shape`

In autobridge the `shape` must be the config the model ACTUALLY dispatches, not a guess (the
gemma4 incident: a hand-written `partitioned_kv_load=True/s_prior=1280` shape optimized a branch
the deployment never runs — the real dispatch is `False/2048`). Recover it STATICALLY from the
compiler's own artifact (`fxgraph.txt` in the model's compile cache) — no runtime flag, no
interceptor, TP-agnostic — then derive `shape` from it. This is an ACTIVE step BEFORE the loop's
baseline, not just a passive check:

```bash
# 1) extract every NKI kernel's real dispatch config from the model's compile cache, pin the
#    operating-point bucket, and isolate the target kernel:
python3 scripts/parse_fxgraph_config.py \
  --cache-root "$VLLM_CACHE_ROOT" \                 # the compile cache the profile stage produced
  --require-arg-name <target-discriminator> \        # e.g. partitioned_kv_load for gemma4 attention
  --select bs=<operating-point-batch> \              # bucket-specific target (bs kept in kwargs)
  --kernel-entry <module::symbol> \
  --out agent_artifacts/<run>/dispatch_fingerprint.json
# `branch_signatures` in the output summarizes the distinct branches (bs-independent); `configs`
# is the bucket-pinned set.
# 2) set kernel_target.shape = one config's kwargs (the branch+bucket you are optimizing) and
#    kernel_target.dispatch_fingerprint = that file.
```

The campaign validates every NKI task target and REFUSES if `dispatch_fingerprint`
is absent, if `shape` contradicts the captured config, or if `shape` is ambiguous (matches more
than one dispatch — under-specified; add bs/s_prior/d_head to pin exactly one). If a cache entry
has no `fxgraph.txt` (NEFF-only), force one cold trace to regenerate it. The fingerprint is required for every campaign kernel target.

## Two distinct profile roles (do NOT re-capture a per-kernel profile)

- **Cycle oracle (accept/reject) = the isolated stub + `measure.py`.** The synthesized stub is
  measured on-device for its `CycleCount`; that is the ONLY thing that decides whether an edit is
  accepted. You cannot re-run the full model per candidate, so the isolated stub is the fast delta.
- **Analysis profile = a `profiler_db` (the profile INPUT) + always-generated qor data.**
  - The **profiler_db** comes from `collect.profiler_db`: a path (e.g. an autobridge Stage-3
    full-model capture ingested to parquet with `nki_source_location`), or null => the isolated
    stub's own `measure.py` capture, auto-ingested. It drives cost/compute + the region specialists
    (bottleneck/gap/offload/efficiency). Do NOT run a separate per-kernel device capture when a
    Stage-3 `profiler_db` is supplied (verified: the gemma4 leg-1 debug capture carries 1.97M
    `mlp_cte`-attributed rows).
  - The **qor data** (`qor_separated` + `stalls`) is ALWAYS generated by the internal brazil-free
    compiler run (`collect_perf_data_local.py`), opening separation/roofline/sharding + dma-order.
    It is an internal compiler artifact absent from an ingested profiler_db — never brazil, never
    optional.

## Procedure (the LLM performs these; every claim cites the file it read)

0. **Confirm the staged tree.** `stage_kernel_tree.py --run-id <run>` → `ok:true`. Note `$TREE`,
   `$NKILIB_PKGNAME`, `$PY` (the serving interpreter).
1. **Locate the family's installed test helpers.** Under `$NKILIB_TESTING_DIR/tests/**`, find
   `test_<family>_common.py`. Identify and READ the signatures of:
   - the **input builder** (`build_*` — e.g. `build_fused_norm_mlp`, `build_qkv_input`),
   - the **runner** (`_run_<family>_test`; if none, use `nkilib_testing.unit_test_framework.UnitTestFramework` directly, wiring `torch_ref` + `kernel_input_generator` + `output_tensor_descriptor`),
   - the **output descriptor** (`*_output_tensor_descriptor`),
   - the **torch reference** the runner compares against (the kernel's own `*_torch` ref, e.g. `nkilib.core.mlp.mlp_torch::mlp_torch_ref`).
2. **Read one real example.** Open an existing test class for the kernel (installed under
   `nkilib_testing/tests/**` or the model-config helper, e.g. `get_mlp_config`) to see the EXACT
   builder+runner call and which params map to shape/config. If the family ships a shape helper
   (`get_<family>_config`), READ and reuse its derivation.
3. **Derive the compilable per-config shape** from the `kernel_target.shape` (full model dims +
   parallelism), applying the family's alignment/sharding rules read in step 2. *(mlp example, from
   `get_mlp_config`: `hidden`→ceil/512·512, `intermediate`=align(full//tp) [<3584→512-mult else
   1024-mult], `vnc_degree`=2, `seqlen`=seqlen//cp. Unpadded/unsharded dims hit a compiler
   `ZeroDivisionError` — VERIFIED.)*
4. **Write the stub** to `$TREE/test/generated/test_<kernel>_gen.py` honoring the INVARIANTS below.
5. **VALIDATE (the gate):**
   ```
   propose_node_bracket.py --repo $TREE --testfile test/generated/test_<kernel>_gen.py \
       --node-prefix '<Class>::<method>' --target trn2 --shadow "" --require <shape tokens>   # -> status unique
   measure.py --repo $TREE --node "<repo-relative NODE>" --target trn2 --shadow "" --log /tmp/m_base.log   # -> passed:true + cycles
   nki_cost_query.py ...   # optional: cross-check measured cycles are in the cost model's band
   ```
   If compile/numeric FAILS: read the error, fix the stub (shape alignment, builder kwargs, imports,
   layout/quant/norm defaults) and retry. **The gate passes only on `passed:true` with a real
   CycleCount.** If the family has NO installed builder/runner, that is an honest BLOCKER (add the
   helper upstream) — never fabricate a stub or a `tpbSgCyclesSum`.
6. **Hand off:** include the node id, baseline measurement, complete target and fingerprint in
   the prepared campaign NKI task. Cite raw measurement and correctness artifacts in its
   submission. Use `kernel_loop_scaffold.py` for candidate worktrees and independent
   remeasurement; confirm the winning improvement beyond the configured noise margin.
   The campaign requires full-model remeasurement and matching equivalence after an accepted
   implementation before it can certify MET.

## INVARIANTS (must hold in every generated stub)

- **Kernel-under-edit** imported from the STAGED repo namespace (`$NKILIB_PKGNAME…<kernel_entry>`),
  so it resolves to `$TREE/src` and worktree edits WIN (verified: PYTHONPATH=`$TREE/src`).
- **Builder / runner / output-descriptor / torch-reference** imported from the INSTALLED
  `nkilib_testing.tests.*` + the kernel's installed `*_torch` ref (invariant ground truth — never
  edited by the loop).
- Exactly **one** parametrized node; **`tpbSgCyclesSum=None`** (never invent a perf golden);
  `measure.py` reads the real CycleCount.
- Node id contains **descriptive shape tokens** (`vnc-… s-… h-… i-… dtype-… <regime>`) for
  `propose_node_bracket --require`.
- **Deterministic inputs** (`torch.manual_seed(42)` / seeded generator).
- Runs via the installed harness in **`--test-mode compile-and-infer`** (emits `show_session_0.json`).
- **Numeric check** vs the kernel's own torch reference at the family's `rtol` (bf16 ≈ 1.6e-2). Never
  weaken tolerances to pass.
- Import the harness via `--shadow ""` (installed `nkilib_testing`, no repo-local `test/utils`).

## Worked example — `mlp_cte` (validated GREEN this run)

Family `mlp`; helpers `nkilib_testing.tests.core.mlp.test_mlp_common.{build_fused_norm_mlp,
_run_mlp_test, mlp_output_tensor_descriptor}`; ref `nkilib.core.mlp.mlp_torch.mlp_torch_ref`;
kernel entry `nkilib_src.nkilib.core.mlp.mlp::mlp`. Stub body:

```python
import pytest, nki.language as nl
from nkilib_testing.common_dataclasses import CompilerArgs
from nkilib_testing.tests.core.mlp.test_mlp_common import (
    build_fused_norm_mlp, _run_mlp_test, mlp_output_tensor_descriptor)
from nkilib_testing.tests.utils.tensor_generators import gaussian_tensor_generator
from nkilib_src.nkilib.core.utils.common_types import (
    ActFnType, NormType, QuantizationType, MLPGateUpWeightLayout, ComputationMode)
from nkilib_src.nkilib.core.mlp.mlp import mlp
_PARAMS = [(2, 1, 8192, 5632, 1024, None, 0.016)]          # vnc,batch,seqlen,hidden,intermediate,tpbSgCyclesSum,rtol
_IDS = ["vnc-2_b-1_s-8192_h-5632_i-1024_dtype-bfloat16_act-gelu_cte"]
class TestMlpCteGen:
    @pytest.mark.compile_and_infer
    @pytest.mark.parametrize("vnc_degree,batch,seqlen,hidden,intermediate,tpbSgCyclesSum,rtol", _PARAMS, ids=_IDS)
    def test_unit(self, test_manager, platform_target, vnc_degree, batch, seqlen, hidden, intermediate, tpbSgCyclesSum, rtol):
        ki = build_fused_norm_mlp(batch=batch, seqlen=seqlen, hidden=hidden, intermediate=intermediate,
            dtype=nl.bfloat16, quantization_type=QuantizationType.NONE, quant_dtype=None, is_input_quantized=False,
            fused_add=False, norm_type=NormType.NO_NORM, store_add=False, lnc_degree=vnc_degree if vnc_degree>1 else None,
            skip_gate=False, act_fn_type=ActFnType.GELU, gate_bias=False, up_bias=False, down_bias=False, norm_bias=False,
            mode=ComputationMode.PREFILL, gate_up_w_layout=MLPGateUpWeightLayout.CONTIGUOUS, tensor_generator=gaussian_tensor_generator())
        ki["quant_clipping_bound"]=0.0; ki["force_cte_mode"]=False; ki["sbm"]=None
        _run_mlp_test(test_manager=test_manager, kernel_input=ki,
            compiler_args=CompilerArgs(logical_nc_config=vnc_degree, platform_target=platform_target),
            output_tensor_descriptor=mlp_output_tensor_descriptor, rtol=rtol, kernel_entry=mlp)
```
Validation results (this run): `--shadow ""` → node **unique**; `measure.py` at tp=8 shape
(`h5632 i3072 s1024`) = **passed, cycles 1,384,971,169**; at the gemma4 leg-1 tp=32 shape
(`h5632 i1024 s8192`) = **passed, cycles 2,118,487,523** (the loop baseline).

## Adding a new kernel = NO code change

Point the loop at a different `kernel_target` (family + kernel_entry + source_file + shape). The LLM
reads that family's `test_<family>_common` helpers + torch ref + an example, derives the shape, and
writes the stub. If a needed builder/runner is missing from the installed harness, the gate reports a
documented BLOCKER (add it upstream), never a fabricated stub.
