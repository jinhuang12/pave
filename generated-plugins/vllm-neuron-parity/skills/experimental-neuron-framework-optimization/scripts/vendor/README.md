# Vendored brazil-sourced dependencies (offline, fleet-free)

These are **vendored copies** of modules the NKI kernel-optimization loop used to reach only
through Amazon's brazil test-runtime (`brazil-test-exec …`). They are pulled in here so the
loop — driven by the `experimental-neuron-framework-optimization` skill — can run the cost
analysis and Phase-0 post-processing **without brazil, the shared fleet, or a hardware run**.

Provenance and rationale are documented in `agent_artifacts/KERNEL_LOOP_DEPENDENCIES.md`
(§1B, §4, §5) and `PIDEV_REMOVAL_*`.

## Contents

| Vendored item | Upstream source | Consumed by | Offline-runnable? |
|---|---|---|---|
| `neuron_kernel_neff_sim/` (package) | `NeuronKernelNeffSim/src/neuron_kernel_neff_sim` | cost specialist (`nki-cost-specialist` / `nki-instruction-cost-analysis[-sim]`) | **Yes** — calibration is baked into `hardware_config.py`/`cost_model.py`; `cost_query` needs no network. (`boto3`/S3 is used only by `eval/`, not by `cost_query`.) |
| `dma_compute_order_check.py` | `nkilib_agentic_dev_plugin/skills/nki-dma-compute-order-check/scripts/` | `collect_perf_data.sh` Phase-0c → `stalls.json`; dma-order specialist | Yes on a **locally-ingested** BIR + profiler_db (pure stdlib). |
| `compute_sch_ineff_private.py` | KNKL `test/utils/` (marked private — header preserved) | `collect_perf_data.sh` scheduling-inefficiency post-process | Yes (pure stdlib: csv/glob/pathlib). |

## How to invoke

Preferred (offline, no brazil):

```bash
# calibrated cost model — use the self-locating launcher one dir up:
python3 ../nki_cost_query.py --target trn2 --list-opcodes
python3 ../nki_cost_query.py --target trn2 --spec '{"opcode":"MATMUL","engine":"Tensor","N":512,"matmul_k":128,"dtype":"bf16"}'
python3 ../nki_cost_query.py --target trn2 --spec '[ ...region ops... ]'   # -> per_engine_ii_ns / per_engine_latency_ns

# or import the package directly (this dir on PYTHONPATH):
PYTHONPATH="$(dirname "$0")" python3 -m neuron_kernel_neff_sim.cost_query --target trn2 ...
```

On-fleet fallback (only if this vendored copy is absent): the historical
`brazil-test-exec python3 -m neuron_kernel_neff_sim.cost_query …` still works inside the
brazil test-runtime.

## What is NOT vendored (and why)

- **`brazil-build`, `brazil-test-exec`, the shared fleet, the test-runtime env** — these are
  build/run *infrastructure*, not files; they cannot be pulled into a package. The loop's HW
  measure uses `measure.py` (local `pytest --test-mode compile-and-infer`) instead.
- **`metrics_collector.py`, `param_extractor.py`** (KNKL `test/utils/`) — part of the KNKL
  test harness, not loop scripts. `measure.py` reads `show_session_0.json` directly (it does
  not import `metrics_collector`); the loop derives node brackets via
  `propose_node_bracket.py --collect-only` (it does not import `param_extractor`).
- **`query_xray.py`, `parse_bir_sb_allocations.py`, `compute_sbuf_footprint.py`,
  `generate_nki_docs.py`** — these belong to *other* leaf skills (roofline-xray,
  sbuf-allocation, api-docs) that are not on the kernel-loop critical path and are not part of
  the NAD `experimental-neuron-framework-optimization` skill. `query_xray.py` additionally
  needs AWS creds + network (XRay DB), so it is not offline-runnable regardless. If those leaf
  skills are later made brazil-free, vendor their scripts into their own skills.

## Coverage limits of the cost model

`cost_query` models **compute (Vector/Scalar/GpSimd) + Tensor (MATMUL/MATMUL_MX/LDWEIGHTS) +
MEMSET** only. It **rejects DMA/collectives and uncalibrated opcode/engine pairs** with a
non-zero exit rather than guessing — the specialist must surface the gap, never fabricate.
Per-engine sums are pure arithmetic (no cross-engine overlap); use the full-NEFF
`neuron_kernel_neff_sim.cli` for wall-time.
