# NeuronKernelNeffSim

Hardware event simulator for compiled Neuron NEFF kernels. Parses a NEFF, decodes
per-engine instruction streams, builds a dependency graph, and simulates execution
with an analytical timing model. Outputs Perfetto-compatible traces and accuracy
reports against hardware ground truth.

## Usage

Single-NEFF simulation:

```
brazil-test-exec python3 -m neuron_kernel_neff_sim.cli <path/to/file.neff> -o trace.json
```

Batch accuracy evaluation:

```
brazil-test-exec python3 -m neuron_kernel_neff_sim.eval \
    --neff-dir <path/to/neuron_test_output_dir> \
    --ground-truth ntff \
    --target trn2 \
    --report-dir /tmp/eval_out
```
