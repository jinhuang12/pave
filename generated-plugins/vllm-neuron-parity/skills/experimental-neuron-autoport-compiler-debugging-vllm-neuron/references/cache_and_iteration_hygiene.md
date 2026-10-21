# Compile-cache poisoning & bring-up iteration hygiene

Cache poisoning bites **ports specifically** because only during a port does the *same
fx-graph* get recompiled dozens of times with *different HLO* as pad/f64/kernel fixes
land — a stable working model never mutates its graph. Symptom: a landed fix looks like a
regression, or *different node pairs give opposite results*. **Always recompile into a
fresh cache root you own before believing a regression; never clear a shared cache (the
plugin's P2).**

## The four poisoning mechanisms

1. **HLO changes but the `make_fx` cache key doesn't.** f64/pad/HLO-only fixes alter the
   HLO but not the fx hash, so a poisoned node-local NEFF is served and the fix appears
   not to take (Inkling jobs 30247 vs 30248 diverged on different node pairs).
2. **Failed compiles leave `.done`-marked broken artifacts.** A failed `neuronx-cc`
   compile writes broken BIR into the shared NKI cache marked complete, so a byte-identical
   *fixed* kernel re-raises the old error (`INLA001` on Qwen3.5 GDN).
3. **NFS/Lustre `NEURON_COMPILE_CACHE_URL` is silently rejected** → falls back to
   node-local `/tmp/vllm_neuron_wdir_$USER` that **persists across Slurm jobs** →
   cross-node non-determinism.
4. **The runner uses the LITERAL string `$USER` (unexpanded).** A bash
   `rm -rf /tmp/vllm_neuron_wdir_$USER` expands `$USER` and deletes the *wrong* dir.

## Escape a poisoned cache without clearing a shared one

Never delete a shared compile cache or the kernel intermediate cache: other jobs read
them, and the plugin's compile-cache guard refuses it (P2). Give the run a cache root
you own, and rename a poisoned partition aside inside that root only (the plugin's
`references/artifact-layout.md` §4.10). Mechanism 4 still applies to any command that
names the runner's directory: quote it as `"/tmp/vllm_neuron_wdir_\$USER"`.

## Distinguish a fresh compile from a stale cache hit

```bash
grep -aE 'cache hits|compiling|cache key|Falling back to /tmp' <run.out>
# 'Falling back to /tmp/.../compile_cache ... remote filesystem' => NFS rejected, node-local
# 'cache hits: 256 ... cache key: 0' => NOT a fresh compile, even if it "passed"
```

## Stale small-config graphs

Content-addressed caches reuse graphs from earlier smoke tests: a **1-layer model
allocates ~217K KV blocks** (nearly all HBM free) → a phantom `EOOM002` inconsistent with
the current 60-layer config. Detect via `fxgraph.txt` layer count:
```bash
grep -o 'modules_layers_modules_[0-9]*' <hash>/*/fxgraph.txt | sort -u   # only layer 0 => stale
# between config changes: a fresh cache root you own, never rm of a shared one (P2)
```

## Orphaned processes holding devices

Orphan `walrus_driver` / `VLLM::Worker` / `EngineCore` survive a crash and hold Logical
Neuron Cores (`Logical Neuron Core(s) not available ... ret=-16`).
```bash
neuron-ls | grep VLLM        # find PIDs; kill by PID, not device id
kill <pid>                   # only a pid your own job started; never pkill -f by name
```

## Environment: editable-install resolves to the wrong worktree

`pip install -e` finder pointing at the wrong worktree → edits to `__init__.py`
model-registration / `platform.py` / `registry.py` aren't the code actually imported →
intermittent "config not recognized". Fix: dedicated worktree + hardlink-cloned venv
(`cp -al`), editable finder repointed. Confirm with
`python -c "import vllm_neuron; print(vllm_neuron.__file__)"`.

## Timeouts & toolchain bumps (large-model port)

- Cold MoE compile (~531s) can lose a photo-finish against the **600s**
  `VLLM_ENGINE_READY_TIMEOUT_S`. Per-bucket prefill compiles take 1–2h and hit
  `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS` (default 1200s) / `VLLM_NEURON_COMPILATION_TIMEOUT`.
  Raise them:
  ```bash
  export VLLM_ENGINE_READY_TIMEOUT_S=1800
  export VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=14400
  export VLLM_NEURON_COMPILATION_TIMEOUT=14400
  export NKI_COMPILE_CACHE_URL=<node-local path>   # so launch 2 warms in minutes
  ```
- A **neuronx-cc version bump changes NEFF cache keys** → full multi-hour recompile; the
  compile backend migrated from `vllm_neuron/compile/{backend,cache}.py` into the
  `libtorch_neuronx_lite` wheel (accept staging's deletion of those files on merge).
- **Kill the running server before recompiling with a new toolchain** — it holds all 16
  devices with the old compiler.
- Monitor progress via the cache dir / `neuronx-cc` proc count; **never pipe a compile
  through `tail`** (hides progress until the process ends).
