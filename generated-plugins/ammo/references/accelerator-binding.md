# AMMO Accelerator Binding

For `target.json.serving`, use `serving-campaign.md` for measurement and objective policy. The batch-only invocation and instrument rules below apply to other targets. Accelerator ownership and runtime binding remain unchanged.

Read this file only when `AMMO_ACCELERATOR_KIND=neuron` or when you must verify runtime routing. The server selects the profile. Do not ask the user to choose a backend.

## Profiles

| `AMMO_RUNTIME_PROFILE` | `AMMO_ACCELERATOR_KIND` | Engine | Engine venv |
|---|---|---|---|
| `pumice-cuda` | `cuda` | Pumice on NVIDIA GPUs. confdb selects the runner; for gpt-oss it is the embedded vLLM 0.30.0 runner. | `/opt/pumice-venv` |
| `pumice-neuron` | `neuron` | Pumice on AWS Neuron. The runner is `neuron`; there is no vLLM. | `/opt/neuron-venv` |

`vllm-cuda` and `vllm-neuron` are legacy names. `runtime_ops.py` maps them to `pumice-cuda` and `pumice-neuron` (`LEGACY_PROFILE_ALIASES`). A missing profile means `pumice-cuda`. Hooks and scripts key on `AMMO_ACCELERATOR_KIND`, not on the profile name.

`pumice-cuda` and `pumice-neuron` are also valid `runtime_profile` values in a serving manifest (`ammo_serving/2`). The manifest value must equal the session's `AMMO_RUNTIME_PROFILE`, so a Pumice session uses a `pumice-*` value, not the legacy name. The serving rules are the same for each CUDA pair and each Neuron pair: device ownership, LNC, and contiguous Neuron ranges do not depend on the engine.

The engine checkout is `ENGINE_BASE_REPO=/workspace/pumice`. Your track worktree is a git worktree of it, and `.venv` in the worktree sees the engine venv through `--system-site-packages`.

The runtime profile selects device, source, cache, and profiler plumbing. AMMO's seven stages, roles, audits, five binding gates, GSM8K accuracy rule, end-to-end speedup criteria, workloads, statistics, verdicts, and stop logic are the same on both runtimes. `GPU_BLOCKED` is the serialized status on both.

## Authority

AMMO's target, reservation, environment, and validation contracts win when a vendored skill example differs. A skill must not select cores, change LNC, create a venv, or install packages. It must not change a workload, comparator, tolerance, statistic, gate, verdict, or stop condition. Use only the server-assigned contiguous NeuronCore range and LNC. LNC fixes the size of one reserved unit and is recorded in the run fingerprint, so changing it invalidates every reservation and comparison; report an LNC error to the lead instead of changing it.

The Neuron skills named in this file are directories under `.claude/skills/` (Claude Code) or `.codex/skills/` (Codex). Load one by name: the Skill tool on Claude Code, `$<name>` on Codex. Read its `SKILL.md` and the references it points to. Its scripts are examples; AMMO's `scripts/` own every measurement. A Neuron session ships only the skills this file names plus `experimental-neuron-parallelism-planner`, a constants library that `experimental-neuron-hardware-entitlement` imports. Session setup removes the other vendored Neuron skills, so do not look for them.

## Both Runtimes

- Edit only the assigned Pumice worktree. Upstream vLLM (CUDA), the Neuron SDK/compiler, torch, and the base image are pinned dependencies. Never `pip install torch` or `pip install vllm` in a session.
- Prove the selected source with `.venv/bin/python -c "import pumice, pumice_rust; print(pumice.__file__, pumice_rust.__file__)"`. Both paths must resolve inside the assigned worktree.
- Use `scripts/run_pumice_grpc_bench.py` for every AMMO baseline, correctness, profiling, and E2E measurement. It launches `rust_grpc_server.py` from the worktree. A server that you start by hand is diagnostic only and cannot satisfy a gate.
- A Rust or confdb edit is live only after `pumice-stage-native.sh` rebuilds and re-stages the engine. The runner freshness gate refuses to measure a stale build. See § The freshness gate in `references/e2e-latency-guide.md`.

## CUDA

On `pumice-cuda` this binding adds nothing: CUDA graphs, Nsys, NCU, cache, reservation, and the activation-proof flow are defined elsewhere in this skill (`references/nsys-profiling-guide.md`, `references/torch-compile-contract.md`). The runner kind decides which compile stack applies; see § Which Runner You Compile Under in `references/torch-compile-contract.md`.

## Neuron Execution and Evidence

- Pumice Neuron code lives under `src/pumice/neuron_inference_core/` and `src/pumice/engine/neuron_model_runner.py`. The runner passes `--model-runner pumice` on Neuron. `default_model_runner()` returns `pumice` when the accelerator is Neuron, and the launch command adds the flag. It is required: confdb has no Neuron section, so without the flag a gpt-oss `[sm90]` row selects the vLLM runner and the Neuron image has no vLLM.
- Rebuild with `pumice-stage-native.sh .venv/bin/python <worktree> --features nixl-stub`. Never compile `_ops` on Neuron (there is no nvcc).
- Rows carry `model_runner=neuron` and `vllm_version` null. TP must be a multiple of the LNC. Spec-decode and grammar tracks are off on Neuron.
- Use `scripts/runtime_ops.py` for device synchronization, control tensors, device identity, compile-cache mapping, profile start/stop, NEFF capture, event normalization, activation proof, and runtime/source fingerprints.
- A stack fingerprint names commits at two scopes. `execution_source.cwd_git.commit` and `sources[<dir>].commit` are the worktree HEAD when the fingerprint was taken. `server_stack_fingerprint.pumice_commit` is the Pumice commit baked into the server image. The session worktree branches from it, and it stays the same when you commit to the worktree. `run_pumice_grpc_bench.py` stamps a fresh fingerprint into every `e2e_latency_results.json`. A successful `--slot baseline` run also copies it into `state.json` `runtime.stack_fingerprint`, which `new_target.py` first wrote at scaffold time. Never edit a fingerprint by hand: that breaks its digest. To record a new source state, rerun the measurement.
- Compile cache: `NEURON_CACHE_ROOT` holds the NEFF cache. `--fresh-cache` has the same meaning as on CUDA. An arm whose Python or confdb sources changed since the last NEFF runs with `NEURON_DISABLE_COMPILE_CACHE=1`. A warm relaunch runs with `NEURON_ASSERT_CACHE_HIT=1`.
- If the server dies at TP bring-up and the first traceback in its log ends with `FileNotFoundError: [Errno 2] No such file or directory: '/psm_<hash>'`, `run_pumice_grpc_bench.py` relaunches it once and records `bringup_retries`, `bringup_retry_reason`, and `bringup_failed_server_log` on that launch's `launches` entry; a second failure stops the run and names both server logs.
- Attribution on Neuron is a standalone `neuron-explorer` chain, not a runner flag. Run the profiler-free timing first, then capture attribution as a separate step, per rank. Do not use `summary-json`: it is one aggregate dict that `normalize` rejects, and multi-rank `-d summary-json` panics in the vendor tool and writes a 0-byte file. The `StartProfile` RPC is CUDA-only and fails silently on Neuron. An error is logged, nothing crashes, and no artifact appears, so always confirm the profile file exists on disk before you trust a completed run.
  1. Capture. Take a NEFF the runner's server compiled (under `NEURON_CACHE_ROOT`) and run `scripts/runtime_ops.py capture-neff --neff <neff> --output <ntff> --tp-size <TP>`. This is the only step that touches the accelerator.
  2. Export. Run `neuron-explorer view --output-format json -n <neff> -s <ntff> --output-file <file>`. `--output-file` is required; redirecting stdout silently writes 0 bytes. Keep DMA. Never pass `--ignore-dma-trace`. Excluding DMA understated busy time by about 11 points in the run that proved this.
  3. Normalize. The export holds several row tables. Feed each table's row list to `scripts/runtime_ops.py normalize` on its own: `active_time` (labelled by `engine`), `cc_ops` (labelled by `operation`), and `dma` (no label field, so add `"name": "dma"` to each row before normalize). Then take the interval union across `pcore_idx`. LNC2 has two physical cores per logical core, so summing double counts.
- Record the mechanism, the raw per-rank NTFF artifacts, the profiler and schema versions, and the normalization fingerprint. Do not read the HBM gauges in `neuron_metrics.py`; no gate may use them.
- Keep profiled evidence separate from authoritative profiler-free E2E timing. Normalize Neuron events before `scripts/mine_trace.py` applies the existing interval-union, phase-share, physical-ceiling, and `f_e2e` calculations.
- At Gate 5.3a, record expected NEFF/bucket compilation, compile-cache use, execution of the Pumice Neuron runner from the worktree, and absence of fallback. This is the Neuron activation proof in the existing gate position.

## Researcher

Use `neuron-nki-profiling` only to identify the NEFF to capture (its `identify-neffs.py` technique); capture itself goes through `scripts/runtime_ops.py capture-neff`, never through the skill's own capture path, which pins cores and LNC. Use `neuron-nki-profile-querying` for measured queries, `neuron-explorer-profile-schema` for version-matched fields and units, `neuron-nki-docs` for API or architecture facts, and `experimental-neuron-hardware-entitlement` for the physical bound (mandatory FLOPs and HBM bytes per operator) behind `physical_ceiling`. Supply measurements and physical bounds only. Do not propose an optimization. Preserve raw Neuron outputs and the normalized intervals.

## Champion

Stage 3 debate. Use `neuron-nki-docs` for API and architecture facts, `experimental-neuron-hardware-entitlement` for the physical bound behind a projection, and `experimental-neuron-memory-system` for SBUF, PSUM, and HBM placement of a kernel candidate. A bounded NKI prototype allowed by the protocol's Evidence section uses `neuron-nki-writing` and `neuron-nki-debugging` in the session worktree. Reuse the Stage 1 profile; do not recapture it.

## Implementer

Use `neuron-nki-docs` for API facts, `neuron-nki-writing` for an assigned NKI implementation, `neuron-nki-debugging` for NKI compile or runtime failures, `neuron-nki-profiling` for NEFF/NTFF capture, `neuron-nki-profile-querying` for profile analysis, and `neuron-explorer-profile-schema` for field definitions. When a framework change fails to compile or lowers badly, use `experimental-neuron-autoport-compiler-debugging-vllm-neuron` (torch-xla and neuronx-cc failures on the Pumice Neuron runner) and `experimental-neuron-perf-compiler-artifact-debugging` (what the compiler did with the graph). Use `experimental-neuron-infra-runtime-troubleshooting` for a device or runtime error, and `experimental-neuron-memory-system` for on-chip placement. The selected AMMO mechanism, worktree, gates, and verdict remain your authority.

## Monitor

Judge Neuron evidence by the same rules as CUDA. Profiler-free timing from `scripts/run_pumice_grpc_bench.py` is the only E2E authority. Attribution must come from the `neuron-explorer` chain above, and activation proof is the Gate 5.3a NEFF/bucket record. Use `neuron-nki-profile-querying` and `neuron-explorer-profile-schema` to check a cited profile value. Nsys does not exist on Neuron; a champion who cites it has the wrong runtime.

## Investigator

Remain read-only. Use `neuron-nki-docs`, `neuron-nki-debugging`, `neuron-nki-profile-querying`, and `neuron-explorer-profile-schema` to test one evidence question; add `experimental-neuron-perf-compiler-artifact-debugging` for a compiler-artifact question and `experimental-neuron-infra-runtime-troubleshooting` for a device or runtime error. Inspect `scripts/runtime_ops.py` fingerprints, activation proof, and normalized intervals. Do not change the environment or evidence.

## Auditor

Apply the same audit questions to both runtimes. On Neuron, reconstruct claims from raw profile outputs, version-matched schema, normalized intervals, activation proof, and fingerprints. Use `neuron-nki-profile-querying`, `neuron-explorer-profile-schema`, and `neuron-nki-docs`. A Neuron skill adds no audit gate or verdict rule.

## Delegate

Use only the skills named by the bounded prompt. `neuron-nki-writing` is allowed only for an assigned NKI micro-experiment. Preserve commands, raw values, raw profile files, normalized intervals, and source citations. The caller owns interpretation and decisions.
