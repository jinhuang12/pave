# Neuron Compiler error codes — actions

Mapping from documented `NCC_*` error codes to the recommended user-side action.
Source: `aws-neuron-sdk/compiler/error-codes/` (e.g.
`/home/ubuntu/aws_docs/aws-neuron-sdk/compiler/error-codes/`) and the public
docs at
`https://awsdocs-neuron.readthedocs-hosted.com/en/latest/compiler/error-codes/index.html`.

When the compile log contains `[INTERNAL_ERROR] NCC_<code>:` or
`[ERROR] NCC_<code>:`, look the code up below first — most are framework- or
input-shape-fixable and don't need a compiler ticket.

## I/O / dtype / op support

| Code | Message | Action |
|------|---------|--------|
| `EARG001` | Unsupported Logical Neuron Core (LNC) configuration. | Use a supported LNC for the target (e.g. LNC=2 on trn2). |
| `EHCA005` / `EVRF015` | Custom call instruction with unrecognized target. | Use a supported custom call target. |
| `EUOC002` / `EVRF001` | Unsupported operator. | Run `neuronx-cc list-operators --framework XLA`; rewrite using a supported op. |
| `ESPP004` | Unsupported data type for code generation. | Cast to a supported dtype. |
| `ESPP047` / `EVRF005` | Unsupported 8-bit floating-point type (`F8E4M3FNUZ`, `F8E4M3B11FNUZ`, `F8E5M2FNUZ`). | Cast to `bfloat16` / `float16`. |
| `EVRF004` | Complex data types (`complex64`/`complex128`) not supported. | Convert to real ops. |
| `EVRF013` | TopK with int input (`int32`/`int64`) not supported. | Cast input to float. |
| `EVRF016` | Scatter-reduce on int/bool. | Cast to float dtype. |
| `EVRF022` | Shift-right-arithmetic on non-32-bit input. | Cast first arg to `int32`/`uint32`/`float32`. |
| `ESFH002` | uint64 constant not safely convertible to 32-bit. | Use `uint32` / restructure constant. |
| `EVRF006` | RNGBitGenerator with non-default RNG algorithm. | Use the default RNG; don't specify the algorithm. |

## Conv / reduce-window

| Code | Message | Action |
|------|---------|--------|
| `EVRF010` | Simultaneous input and kernel dilation in conv. | Use only one. |
| `EVRF011` | Strided conv combined with dilated input. | Apply up/downsample separately, or remove one. |
| `EVRF017` | Reduce-window with `base_dilation > 1`. | Set base dilation to 1, or implement dilation manually. |
| `EVRF018` | Reduce-window with `window_dilation > 1`. | Same: remove window dilation. |
| `EVRF019` | Reduce-window with !=2 operands. | Split into single-operand reduce_windows. |

## Out-of-bounds / sizing

| Code | Message | Action |
|------|---------|--------|
| `EVRF031` | Scatter out-of-bounds. | Ensure iota size matches the operand dimension size. |
| `EVRF056` | Gather out-of-bounds indices. | Ensure iota dim ≤ operand dim; check `max_position_embeddings >= sequence_length`. |
| `EBIR023` | MLP kernel intermediate exceeds 4096. | Tile the intermediate dim or increase TP. |

## Memory / instruction-count (most relevant to perf debugging)

These are the error codes most likely to appear when **scaling up** a model
that compiled at a smaller config. Treat them as a budget signal, not a
compiler bug.

| Code | Message | Action |
|------|---------|--------|
| `EOOM001` / `EOOM002` / `EVRF009` / `EXSP001` | Activation memory exceeds HBM limit. | Reduce batch/seq/tensor size, or shard via TP / PP via [neuronx-distributed](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/libraries/neuronx-distributed/activation_memory_reduction.html). |
| `EBVF030` / `EVRF007` / `EXTP004` | Number of instructions exceeds limit. | Apply model parallelism (TP/PP) to split the graph. |

## LNC=2 modular-flow partitioner (most relevant to "all-PyTorch" / no-NKI-kernel graphs)

| Code | Message | Action |
|------|---------|--------|
| `NCC_IXRO002` | `Undefined SB Memloc dot.<id>_deconcat_*` at default LNC=2 codegen. Seen on graphs with **no NKI kernel calls** (`kernel_call_count: 0` — e.g. a model whose attention/MLP run through PyTorch-fallback ops instead of fused/native kernels). The LNC=2 **modular-flow partitioner** mis-shards a `dot` whose output is deconcat'd into unevenly-sized consumers (e.g. a fused QKV projection `[*, 6144]` split into Q/K/V of `4096`/`1024`/`1024`). | **Do NOT jump to `--logical-nc-config 1` (that's TP=1-only and breaks TP>1 collectives) and do NOT assume it requires new NKI kernels.** First try disabling *modular* partitioning while staying on LNC=2: set `--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=<N>` to a value **above the total graph MAC count** (a very large sentinel like `99999999999999` works generically) so the compiler does a single-partition compile instead of the buggy modular split. This has fixed this exact error on at least one all-PyTorch embedding-model graph (see the worked example below) with only a compile-flag change — verify on-device equivalence afterward, but do not assume kernel work is required until this flag sweep has been tried and failed. |

**Worked example (validated, `generic-encoder-only-model` on trn2):** `command.txt` showed
`--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=10`. Recompiling the same
`graph.hlo` directly with `neuronx-cc` while sweeping this threshold (`250B`, `500B`, `1T`, `2T`)
all failed identically; only a threshold **above the graph's total MAC count**
(`99999999999999`) avoided modular partitioning and compiled cleanly, with correct on-device
output (cos ≥ 0.999 vs reference). This became the model's `VLLM_NEURON_FORCE_LNC2_NOMODULAR=1`
flag. Cost: ~5-8% latency vs. a hypothetical fully-modular compile, but it unlocked TP>1 (whereas
`--logical-nc-config 1` cannot run at TP>1 at all — its NEFFs are incompatible with the LNC=2
collective topology).

**Before concluding this needs new/fixed NKI kernels**: that conclusion should only follow
*after* this flag sweep has been tried on the real `graph.hlo` via `neuronx-cc` directly (see
Quick start above) and failed for every threshold up to and including a value larger than the
graph's MAC count. Do not accept a claim of "requires NKI kernels" or "compiles but produces
silently wrong results" from a delegated report unless it comes with the actual command,
`log-neuron-cc.txt`, and on-device cosine-similarity output attached — re-run the check yourself
if that evidence isn't there.

## Internal-error codes NOT in this list

If the assertion in the log is **not** in the `NCC_E*` table — e.g.:

- `NCC_IMPR901: Need to split to perfect loopnest` (MaskPropagation)
- Generic `Assertion failed: False` from concurrent-futures
- `[INTERNAL_ERROR]` without a `NCC_E*` code

…that's a compiler-internal pass crash. The user can't fix it via input/config;
it needs a ticket. Follow [crash_triage.md](crash_triage.md) to:

1. Identify the failing pass via penguin sg `git log`.
2. Identify the HLO op (id, shape, op_name) on the diverging IR line.
3. Package `graph.hlo` + `command.txt` + `log-neuron-cc.txt` + crashing
   `penguin-sgNNNN/` into a tarball.

## When to file a ticket

| Situation | File ticket? | Workaround first |
|-----------|--------------|-------------------|
| `NCC_E*` code IS in the public table | No (usually) | Apply the documented action. |
| `NCC_E*` code is in the table but the documented workaround changes model semantics | Yes | Explain in the ticket what you tried. |
| Internal pass crash (`NCC_IMPR901` etc., or no code) | Yes | Try a known-good compiler version; bisect with [crash_triage.md](crash_triage.md). |
| Compile **succeeds** but runtime perf is unexpectedly bad | Yes (usually a perf bug, not a crash) | Use [dma_redundancy.md](dma_redundancy.md) and [compiler_diff.md](compiler_diff.md) first to pinpoint. |

## Cross-references

- Public index of all error codes:
  `https://awsdocs-neuron.readthedocs-hosted.com/en/latest/compiler/error-codes/index.html`
- Local copy in this repo:
  `/home/ubuntu/aws_docs/aws-neuron-sdk/compiler/error-codes/`
- Compiler API reference:
  `https://awsdocs-neuron.readthedocs-hosted.com/en/latest/compiler/`
- TP/PP guide (most common fix for memory/instruction errors):
  `https://awsdocs-neuron.readthedocs-hosted.com/en/latest/libraries/neuronx-distributed/`
