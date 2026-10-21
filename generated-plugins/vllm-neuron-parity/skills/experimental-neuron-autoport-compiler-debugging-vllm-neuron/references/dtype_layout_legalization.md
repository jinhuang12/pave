# dtype & layout legalization (f64 literals, the _pad ICE, 4D strides)

The three most common lowering walls on a fresh port, all fixed in model code, all
requiring you to **reach past the FX graph into the raw HLO proto** because
`make_fx`/`torch.export`/CPU-compile hide them. Use `scripts/scan_hlo.py`.

---

## 1. f64 from bare Python literals — `NCC_ESPP004`

**Signal:** `NCC_ESPP004 f64 dtype is not supported` at `StableHLOToPythonPrinter.cc:824`,
repeated 60–80×. Surfaces to the user only as `compilation failed with 70`.

**Why a port trap:** a brand-new architecture is full of hand-written scalar math — muP
scaling `/24`, `route_scale=8.0`, log-scaling `tau`, `1.0/head_dim` attention scale
(every layer), RMSNorm `eps`, `pow(2)`, `shared/ep_degree`, short-conv `*0.0`. Each bare
Python float promotes to an **f64 HLO constant**. Also `torch.isfinite/isinf` (+inf),
`masked_fill(float)`, `torch.full_like` weak scalars. **`make_fx`/`torch.export`/CPU-compile
preserve the weak-scalar dtype, so f64 is invisible in `fxgraph.txt`** — it only
materializes on a ~7-min device recompile. A clean CPU compile is NOT proof.

**Localize** (parse HLO for F64 = `element_type==12`, map to source lines):

```bash
python3 scripts/scan_hlo.py f64 graph.hlo
# equivalently, inline:
python3 -c "from libneuronxla.proto import hlo_pb2; m=hlo_pb2.HloModuleProto(); \
m.ParseFromString(open('graph.hlo','rb').read()); \
[print(i.metadata.source_file, i.metadata.source_line, i.name) \
 for c in m.computations for i in c.instructions if i.shape.element_type==12]"
```

Because each recompile is expensive, **audit the whole file at once** — a tau-only fix
still left 72 ESPP004; per-layer sites (attn scaling, RMSNorm eps) dominate.

**Fix (behavior-preserving):** wrap every literal as a typed tensor
`torch.full((), v, dtype=w.dtype)`; `*0.0` → `torch.zeros_like`; inf → finite sentinel
`-1e30`; scalar-vs-tensor compares → tensor-vs-tensor thresholds; clamp/log via
`torch.maximum` on typed tensors.

---

## 2. Anything that lowers to XLA `pad` — `NCC_ISMP902` (Pelican `_pad` ICE)

**Signal:** `NCC_ISMP902 [Tensorizer] Transformation error on operator: _pad` →
`Simplifier error: Pelican exception: RAUW failed <repl> users at Value.cpp:149
useEmpty()`. The Pelican simplifier's replace-all-uses-with rewrite hits an empty
use-list assertion; the `pad` op is unlowerable.

**Pad-generating idioms to eradicate:**
- `F.pad`
- `torch.cat` with constant-zero tensors
- `nn.Conv1d(padding=W-1)` (causal short-conv)
- edge slice-assignment `x[...,-1:]=v` and `x[...,0]=v` (→ `pad edge_padding_low=255` + `select`)
- clamp-index + gather

**The port trap:** the pad source is frequently **not your model code** but **shared
sampler infra** — `functional/sampling.py`: `cdf[...,-1:]=1.0`, `sorted_mask[...,0]=False`,
`_top_k_filter` (topk→clamp→gather) — exposed only because your edit forced a fresh
recompile of the sampler subgraph.

**Localize:**
```bash
python3 scripts/scan_hlo.py pad graph.hlo    # prints pad opcode/name (+ edge_padding)
neuronx-cc compile graph.hlo ... --internal-compiler-debug-mode=penguin  # dump penguin sgs
# match the crashing sgNNNN to the sampler subgraph; use IDENTITY STUBS to prove the source
```

**Fix:** replace edge slice-assignment with an **arange one-hot mask + `torch.where`**;
replace topk+gather with **one-hot arange select + reduce-sum**; replace `F.pad`/`Conv1d`
padding with **`torch.cat` of an explicit zeros tensor**:
```python
# cdf[..., -1:] = 1.0   ->
V = cdf.shape[-1]; last = torch.arange(V, device=cdf.device) == (V-1)
cdf = torch.where(last, torch.ones_like(cdf), cdf)
# causal short-conv tap (no edge_padding):
zpref = xf[:j]*0.0; shifted = torch.cat([zpref, xf[:T-j]]); out += shifted * w[:, W-1-j]
```
The `_pad` ICE itself is a **true compiler bug**; it is worked around entirely in
model/shared code.

---

## 3. Stride / layout — `NCC_ITIN902`, `NCC_IBCG901`

| Code | Trigger | Fix |
|------|---------|-----|
| `NCC_ITIN902` (TensorInitialization/DotTransform) | Non-contiguous 4D reshape/transpose/reduction stride chains (MoE einsum, per-head 4D ops); **`AffineIV`**: in-graph square `[B,S]` one-hot via `zeros().scatter_` fails at **bs≥4** (compiles small) | Flatten 4D→3D/2D + `.contiguous()`; explicit per-expert matmul loop instead of einsum; **precompute one-hot host-side** or use `index_select`/`index_copy` |
| `NCC_IBCG901` (BIRCodeGenLoop) | >4 (5-level) stride nesting on small tensors (`[64,4,4]`): Sinkhorn row/col normalize, 4D divides/RoPE, reduce+broadcast+divide, in-place `setitem` | Keep tensors ≤3D + `.contiguous()`; sum+divide→softmax; `setitem`→`torch.cat` of contiguous halves; split RoPE onto the last `rope_dim` (64) only; avoid `torch.diag_embed` |

**Escape hatch:** when PyTorch decompositions keep hitting stride errors, **replace the
whole op with a hand-written NKI kernel** — opaque to the Tensorizer stride analysis. But
you then own the NKI kernel's own bring-up (SBUF, slice legality, the GDN trap) — see
[nki_kernel_bringup.md](nki_kernel_bringup.md).

---

## The `x*x` trap (affects both f64 and the 8-LUT ceiling)

`.square()`, `.pow(2)`, and `.pow(2.0)` **all lower to HLO `power`**; **only `x*x` lowers
to `multiply`.** A fresh graph still had 30 `power` ops after switching `pow(2)`→`square()`.
Verify by counting HLO opcodes, never the Python source form:

```bash
python3 scripts/scan_hlo.py act graph.hlo   # Counter over power/multiply/sqrt/rsqrt/exp/...
python -c "import torch,torch_xla,torch_xla.core.xla_model as xm; d=xm.xla_device(); \
x=torch.randn(8,16,device=d); h=torch_xla._XLAC._get_xla_tensors_hlo([x.pow(2).sum()]); \
print('power',h.count(' power('),'multiply',h.count(' multiply('))"
```
Removing the RMSNorm `power` table (write `x*x`) drops the 8th activation-LUT table and
clears one form of `NCC_INLA001` — see [port_error_codes.md](port_error_codes.md).
