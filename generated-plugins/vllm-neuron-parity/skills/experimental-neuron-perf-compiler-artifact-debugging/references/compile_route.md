# Compile route: native torch.compile vs XLA

vllm-neuron 0.29 (Neuron SDK 2.33) compiles through the native torch.compile route by default. The XLA route still exists, is on its deprecation path (last release 2.34), and is the route every artifact recipe in this skill was written for. Read the route before you look for a file.

## Read the route

| Signal | Native route | XLA route |
|---|---|---|
| `VLLM_NEURON_BACKEND` | unset, `neuron_native`, or the alias `neuron` | `vllm_neuron` or the alias `nxdi` |
| Start-up log, only when no `--optimization-level` was passed | `Using default optimization level O2 on the Neuron native route` | no such line |
| In code | `vllm_neuron.envs.is_native_backend()` is true | false |

The variable decides the route; the log line only confirms it. With an explicit `--optimization-level`, both routes print `Explicit --optimization-level: keeping O<n>` instead. Record the route with every compile artifact you cite. Two runs on different routes write different files for the same graph.

## Where the artifacts are

| Artifact | XLA route | Native route |
|---|---|---|
| Compiler input | `graph.hlo` (HLO proto) in `compile_cache/<hash>/dev0_*.rank*/` | `module.mlir` (StableHLO bytecode) in `$TORCH_NEURONX_DEBUG_DIR/neuronx-cc/<hash>/`; the readable text with `loc(...)` entries is the per-job `stablehlo/*.stablehlo.mlir` under `torch_neuronx/rank<r>/<job>/` |
| FX graph dump | `fxgraph.txt` beside `graph.hlo` | `torch_neuronx/rank<r>/<job>/fx_graphs/*.fx.txt` and `transformed_fx/` |
| Compiler log | `log-neuron-cc.txt` beside `graph.hlo` | `neuronx-cc/<hash>/log-neuron-cc.txt` |
| Compile flags | `command.txt` beside `graph.hlo` | `compiler_args.txt` beside `module.mlir` (with `metadata.txt` and `module.neff`) |
| Input fingerprints | `example_inputs.txt` | `inputs/inp-<rank>-<n>.p`, only with `TORCH_NEURONX_DYNAMO_TENSOR_DUMPS` |
| Kept by default | yes, in the cache | no |

Native artifacts exist only when the compile ran with `TORCH_NEURONX_PRESERVE_COMPILATION_ARTIFACTS=1`. `TORCH_NEURONX_DEBUG_DIR` defaults to `/tmp/neuron_backend`; set it to a fresh directory you own before the run, never to a shared one. A crash on the native route leaves nothing in `compile_cache/`: rerun with the two variables set, then stage from the kept tree.

## Recompile a native graph by hand

Lift every flag from the kept `compiler_args.txt`, as crash_triage.md step 2 lifts them from `command.txt` on the XLA route, drop only the input, output and logfile paths, and add `--internal-compiler-debug-mode=penguin`:

```bash
neuronx-cc compile module.mlir <every flag from compiler_args.txt> \
    --output module.neff --internal-compiler-debug-mode=penguin
```

Never hand-write the set. The runner passes its own (`compile_options["compiler_args"]` in `neuron_model_runner_v2.py`): `--auto-cast=none`, `--verbose=35`, the run's `-O<n>`, `--internal-hlo2tensorizer-options=...` (with `--experimental-unsafe-fp8e4m3fn-as-fp8e4m3` for FP8 models off trn3), `--internal-backend-options=--enable-verifier=false --enable-nested-dynamic-loop`, the LNC argument when it differs from the compiler default, and per-graph levels from `resolve_graph_compiler_args` (decode can compile at -O3). Lite reads the shell's `NEURON_CC_FLAGS` only when the runner passes none, so your shell's value is not the run's set. A dropped flag produces a different failure that masks the real one: an FP8 model fails with `NCC_EVRF051`, an NKI-kernel graph fails in the BIR verifier, a decode graph compiles at the wrong level. If the kept directory is gone, rebuild the set from the runner code at your pin, never from memory. Which `--framework` value the native compile passes is not settled from the wheel; `compiler_args.txt` records it.

## Source attribution

Native lowering writes each op's `file:line` from the FX node's `stack_trace`, and the owning module from `nn_module_stack`, into the StableHLO `loc(...)` entries of the kept `stablehlo/*.stablehlo.mlir`, with no variable to set. The three XLA-route variables `XLA_IR_DEBUG`, `XLA_HLO_DEBUG` and `NEURON_FRAMEWORK_DEBUG` are read by no native framework or compiler code and are not in the NEFF cache key, so setting them changes nothing and forces no recompile. An empty location on native points at the graph or the compiler, not at a missing flag.

## XLA-only recipes in these skills

`hlo_convert` (reads an HLO proto; the native StableHLO text is already readable), `scripts/scan_hlo.py` and the torch_xla `_get_xla_tensors_hlo` probes in the port skill, the f64-literal hunt in `graph.hlo` (the native lowering casts f64 to f32 itself), and every `compile_cache/<hash>/` path. The penguin stages and everything that reads penguin IR or the compiler log apply to both routes.

## NEFF cache on the native route

Key: graph, target, LNC, optimization level, `NEURON_CC_FLAGS`, compiler version. NEFF roots: `native_neff_cache` beside the XLA `compile_cache`, `~/.cache/neuron_libtorch` (`NEURON_LIBTORCH_CACHE_ROOT`), `TORCH_NEURONX_NEFF_CACHE_DIR` (default `/tmp/neff_cache`) and `TORCH_NEURONX_NEFF_LOCAL_CACHE_DIR` (default `/tmp/local_cache`). The precompile package root `$VLLM_CACHE_ROOT/neuron/torch_compile` holds packages that point into the NEFF cache, not the NEFFs themselves; it is protected all the same. Never clear a shared root; point the cache variables at a fresh root you own instead.
