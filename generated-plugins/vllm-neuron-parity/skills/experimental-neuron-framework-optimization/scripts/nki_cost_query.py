#!/usr/bin/env python3
"""Thin launcher for the VENDORED neuron_kernel_neff_sim cost model (offline, no brazil).

The calibrated NKI-ISA cost model (`neuron_kernel_neff_sim`) used to be reachable ONLY via
`brazil-test-exec python3 -m neuron_kernel_neff_sim.cost_query` inside the brazil test-runtime
(KERNEL_LOOP_DEPENDENCIES.md §4C called it "the one loop dependency with no local
replacement"). It has since been vendored into `scripts/vendor/neuron_kernel_neff_sim/`
(from the NeuronKernelNeffSim package), so the kernel loop's cost specialist can query
calibrated `latency_ns` / `ii_ns` WITHOUT brazil, the shared fleet, or a hardware run.

This launcher adds the sibling `vendor/` dir to sys.path and delegates to
`neuron_kernel_neff_sim.cost_query.main()`, so callers need not manage PYTHONPATH.

Usage (identical args to `python3 -m neuron_kernel_neff_sim.cost_query`):
  python3 nki_cost_query.py --target trn2 --list-opcodes
  python3 nki_cost_query.py --target trn2 --spec '{"opcode":"MATMUL","engine":"Tensor","N":512,"matmul_k":128,"dtype":"bf16"}'
  python3 nki_cost_query.py --target trn2 --spec '[ ...region ops... ]'   # sequence -> per_engine sums

Compute (Vector/Scalar/GpSimd) + Tensor (MATMUL/MATMUL_MX/LDWEIGHTS) + MEMSET only;
DMA/collectives and uncalibrated opcodes are rejected (non-zero exit), never guessed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

try:
    from neuron_kernel_neff_sim.cost_query import main
except ModuleNotFoundError as e:  # pragma: no cover
    sys.stderr.write(
        f"[nki_cost_query] vendored neuron_kernel_neff_sim not found next to this "
        f"launcher ({e}). Expected at {Path(__file__).resolve().parent / 'vendor' / 'neuron_kernel_neff_sim'}.\n")
    raise SystemExit(2)

if __name__ == "__main__":
    raise SystemExit(main())
