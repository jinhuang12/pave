# Architecture-feature risk table — what each new feature stresses in the compiler

Use this to *predict* where a port will break, and (with the good-vs-bad fxgraph diff in
[porting_workflow.md](porting_workflow.md) §4) to map an isolated failing feature to its
likely code. Each row: feature → what it stresses → codes it triggers → the pre-emptive fix.
Codes are defined in [port_error_codes.md](port_error_codes.md).

| Architecture feature | What it stresses in the compiler | Codes it triggers | Pre-emptive fix / lever |
|----------------------|----------------------------------|-------------------|--------------------------|
| **MoE experts (einsum / per-expert 4D)** | Non-contiguous 4D stride maps in the tensorizer; DotTransform | `ITIN902`, `IBCG901` | Explicit per-expert matmul loop or `NF.moe_cte` NKI kernel; flatten 4D + `.contiguous()` |
| **MoE routing (top-k / sort)** | HLO `sort` (unsupported on trn2); internal error wrappers; batch-dependent | `EVRF029`, `INAS001`, bare `IGAA901` | Iterative argmax + `-inf` mask; single `scatter_` with full `[N,K]`; check top-k is even reachable |
| **MoE + EP collectives** | SubgraphForkPass topology; extra graph outputs; 4-layer configs; cross-node barriers | `ISCH719`; runtime `status=1206`/`NRT_NETWORK_PROXY_FAILURE` | Kill scalar multiplies; remove extra outputs; **6 layers not 4**; -O2; verify `replica_groups` from compiled graph |
| **MoE small per-rank intermediate** | Kernel alignment guard gap (`I%128`); tiny cross-DP decode batch under-fills 128-row tiles | `INKI016`; runtime DGE OOB | `moe_cte` for bf16 (guards `I_TP<128`); **zero-pad token dim to `block_size`** |
| **MLA / CSA (compressed-KV, indexer)** | 4D per-head reshapes; Sinkhorn/hyper-connections small-matrix normalize; indexer RoPE+relu activation tables; dynamic-shape return | `ITIN902`, `IBCG901`, `INLA001`, runtime `status=101` | Flatten ≤3D; softmax not sum/divide; fixed-arity/fixed-size output; drop dead indexer scoring |
| **Hybrid + sliding-window attention** (SWA + global) | Heterogeneous head dims; KV promoted to graph I/O; scratchpad blowup at long prefill | `INKI016` (head_dim>128), `EOOM002`, `IGCA037` | Bump `MAX_HEAD_DIM` in both attn files or manual attention; **SWA-skip** when `chunk_size≥window`; `KV_GMU_BUDGET_CAP_FRACTION` |
| **Exotic head_dim (256/512)** | Every stock NKI attention validator (cap 128); bespoke kernel SBUF fit | `INKI016`, `IGCA037`, `IBIR297`, `ISCH715` | Two-file cap bump / manual attention / d256 kernel; stream per-tile SBUF; PSUM at partition 0 |
| **GatedDeltaNet / linear-attn recurrence** | In-place slice writes (`xla::unselect`); SymInt loop counts; unlowerable index/gather; PSUM partition access in hand kernels | dynamo `Unsupported`, `xla::unselect` FATAL, `IBTN006`, `INLA001`(b) | Fully out-of-place rewrite (Neumann series + `torch.stack`); static loop count; hoist ops host-side; validate full-graph |
| **Short-conv mixer (causal depthwise)** | `nn.Conv1d(padding)` / `F.pad` → XLA `pad` Simplifier ICE | `ISMP902` (`_pad`) | Shifted-tap sum via `*0.0` prefix + `torch.cat`, no `edge_padding` |
| **Novel norms / activations (muP, log-scaling tau, RMSNorm eps, route_scale)** | Bare Python float literals → f64 constants (invisible in FX); 8-activation-LUT ceiling | `ESPP004`, `INLA001`(a) | Wrap every literal `torch.full((),v,dtype=)`; `x*x` not `.square()`; drop a transcendental table |
| **FP8 KV cache** | NKI kernel dtype contract (prior vs active); OCP fp8 flag on wrong target | `INKI016`, `EOCP001` | Cast actives to `float8_e4m3`; gate `--experimental-unsafe-fp8e4m3fn-as-fp8e4m3` to trn2 only |
| **Split-KV / segmented prefill** | I/O double-counting; bucket-equality constraint; context bucket 256 | `EOOM002`, `ITEN404`, config hard-fail | `KVCacheTensor.shared_by`; `num_batched_tokens_buckets == kv_segment_size_buckets`; avoid bucket 256 |
| **Distributed argmax / sampler** | `AllGather` non-zero-dim mis-opt (wrong output at B>1); shared sampler `pad` ops | wrong output (no code), `ISMP902` | Transpose `[B,1]→[1,B]`, gather dim 0, transpose back; arange one-hot in the sampler |
| **Data-dependent shapes / `.item()`** | Materialization on placeholder XLA tensors → native segfault; DGE OOB from `PAD_SLOT_ID=-1` | segfault (no code), runtime `status=5` | Fixed-size gather over compile-time constant + mask; `torch.where(slot>=0, slot, garbage_slot)` |

**How to read it:** after the good-vs-bad histogram names the ops unique to the failing
cluster (e.g. `einsum`+`softplus` → MoE experts; `conv1d`/`pad` → short-conv;
`scatter_`+`arange` → in-graph one-hot), jump to the matching row for the expected code
and fix, then confirm against the actual `log-neuron-cc.txt`.
