# AMMO Accelerator Binding

Read this file only when `AMMO_ACCELERATOR_KIND=neuron` or when you must verify runtime routing. `target.json` `runtime.profile` selects the profile at scaffold time. Do not ask the user to choose a backend.

## Profiles

| `AMMO_RUNTIME_PROFILE` | `AMMO_ACCELERATOR_KIND` | Engine |
|---|---|---|
| `pumice-cuda` | `cuda` | Pumice on NVIDIA GPUs. confdb selects the runner; for gpt-oss it is the embedded vLLM runner at the pinned version. |
| `pumice-neuron` | `neuron` | Pumice on AWS Neuron. The runner is `neuron`; there is no vLLM. |

v1 ships and tests the CUDA path only. `new_target.py` and `preflight_pumice.py` refuse every profile that does not resolve to `pumice-cuda`. The Neuron rules in this file and the Neuron branches in the scripts stay as they are, but v1 does not run or test them.

`vllm-cuda` and `vllm-neuron` are legacy names. `runtime_ops.py` maps them to `pumice-cuda` and `pumice-neuron` (`LEGACY_PROFILE_ALIASES`). A missing profile means `pumice-cuda`. Hooks and scripts key on `AMMO_ACCELERATOR_KIND`, not on the profile name.

`pumice-cuda` and `pumice-neuron` are also valid `runtime_profile` values in a serving manifest (`ammo_serving/2`). The manifest value must equal the campaign's `AMMO_RUNTIME_PROFILE`, so a Pumice campaign uses a `pumice-*` value, not the legacy name. The serving rules are the same for each CUDA pair and each Neuron pair: device ownership, LNC, and contiguous Neuron ranges do not depend on the engine.

The engine root is the Pumice checkout that `target.json` records as `engine.root` (`AMMO_ENGINE_ROOT`). The engine venv is `engine.venv` (`AMMO_PYTHON`). `references/campaign-env.md` lists both. The user builds both before the campaign starts. Your track worktree is a git worktree of the engine root under `<engine>/.claude/worktrees/<name>/`. The worktree build hook gives it a `.venv` that sees the packages of the engine venv and imports `pumice` and `pumice_rust` from the worktree.

The runtime profile selects device, source, cache, and profiler plumbing. AMMO's seven stages, roles, audits, five binding gates, GSM8K accuracy rule, end-to-end speedup criteria, workloads, statistics, verdicts, and stop logic are the same on both runtimes. `GPU_BLOCKED` is the serialized status on both.

## Authority

AMMO's target, reservation, environment, and validation contracts win when a vendored skill example differs. A skill must not select cores, change LNC, create a venv, or install packages. It must not change a workload, comparator, tolerance, statistic, gate, verdict, or stop condition. Use only the contiguous NeuronCore range that `references/gpu-pool.md` reserves for you, and the campaign LNC. LNC fixes the size of one reserved unit and is recorded in the run fingerprint, so changing it invalidates every reservation and comparison; report an LNC error to the lead instead of changing it.

v1 does not ship the Neuron skills that this file names. On a host that has them installed, load one by name: the Skill tool on Claude Code, `$<name>` on Codex. Read its `SKILL.md` and the references it points to. Its scripts are examples; AMMO's `scripts/` own every measurement. A Neuron campaign uses only the skills this file names plus `experimental-neuron-parallelism-planner`, a constants library that `experimental-neuron-hardware-entitlement` imports. Do not look for other vendored Neuron skills.

## Both Runtimes

- Edit only the assigned Pumice worktree. Upstream vLLM (CUDA), the Neuron SDK/compiler, torch, and the engine venv are pinned dependencies. Never `pip install torch` or `pip install vllm` in a campaign.
- Prove the selected source with `.venv/bin/python -c "import pumice, pumice_rust; print(pumice.__file__, pumice_rust.__file__)"`. Both paths must resolve inside the assigned worktree.
- Use `scripts/run_pumice_grpc_bench.py` for every AMMO baseline, correctness, profiling, and E2E measurement. It launches `rust_grpc_server.py` from the worktree. A server that you start by hand is diagnostic only and cannot satisfy a gate.
- A Rust or confdb edit is live only after `scripts/ammo pumice-stage-native.sh` rebuilds and re-stages the engine. The runner freshness gate refuses to measure a stale build. See § The freshness gate in `references/e2e-latency-guide.md`.

## CUDA

On `pumice-cuda` this binding adds nothing: CUDA graphs, Nsys, NCU, cache, reservation, and the activation-proof flow are defined elsewhere in this skill (`references/nsys-profiling-guide.md`, `references/torch-compile-contract.md`). The runner kind decides which compile stack applies; see § Which Runner You Compile Under in `references/torch-compile-contract.md`.

## Neuron Execution and Evidence

- Pumice Neuron code lives under `src/pumice/neuron_inference_core/` and `src/pumice/engine/neuron_model_runner.py`. The runner passes `--model-runner pumice` on Neuron. `default_model_runner()` returns `pumice` when the accelerator is Neuron, and the launch command adds the flag. It is required: confdb has no Neuron section, so without the flag a gpt-oss `[sm90]` row selects the vLLM runner, and a Neuron engine venv has no vLLM.
- Rebuild with `scripts/ammo pumice-stage-native.sh <worktree>/.venv/bin/python <worktree> --features nixl-stub`. Never compile `_ops` on Neuron (there is no nvcc).
- Rows carry `model_runner=neuron` and `vllm_version` null. The Pumice Neuron registry is llama3, gpt_oss, qwen3_moe, and qwen3_vl. TP must be a multiple of the LNC. Spec-decode and grammar tracks are off on Neuron.
- Prefill chunk budget. When `max_model_len` is greater than 4096 and `--max-num-batched-tokens` is absent, `new_target.py` freezes `--max-num-batched-tokens 4096` in `bench.extra_args` and prints a WARNING that says so. Pass the flag to choose another supported value. The Neuron runner (`src/pumice/engine/neuron_model_runner.py`) sets `kv_segment_size` to `max_num_batched_tokens` when that value equals `max_model_len`, and the segmented attention kernel (`neuron_inference_core/functional/attention/attention_segmented_cte.py`) compiles only `kv_segment_size` in `{512, 1024, 2048, 4096}`. Without the cap, `--max-model-len 8192` makes every TP rank die at warmup with `ValueError: kv_segment_size=8192 is not supported`, before `HealthCheck`. The Pumice gpt-oss-20b Neuron smoke test uses `--max-num-batched-tokens 4096`. Record the resolved value from the `NeuronModelRunner config:` log line in `constraints.md`. A prefill of `batch x input_len` tokens at or under the budget is unaffected by the cap.
- Reservation. One `gpu_reservation.py` resource is one logical NeuronCore, and every count in `references/gpu-pool.md` § How Much to Reserve means logical NeuronCores. The pool and every child reservation are contiguous. For an engine or serving-level command, assign the printed `start-end` value to `NEURON_VISIBLE_DEVICES`:

  ```bash
  NVD=$(scripts/ammo gpu_reservation.py reserve \
    --num-resources N --session-id {gpu_session_id} --no-auto-release) && \
    NEURON_VISIBLE_DEVICES=$NVD <engine-or-serving-profile-command>
  ```

  The engine maps each worker rank to one core and sets that worker's `NEURON_RT_VISIBLE_CORES`. Set `NEURON_RT_VISIBLE_CORES` yourself only for a direct, single-process NKI or runtime command, from the same reservation. Never pass it to an engine parent (the Pumice server or `run_pumice_grpc_bench.py`). An empty Neuron visibility value is not a no-core sentinel. A Neuron Profile or NKI capture reserves the count its declared production topology needs. If an unknown process uses your visible core range, report it to the lead and wait.
- Nsys, NCU, and CUDA graphs do not exist on Neuron. Take runtime facts from `scripts/runtime_ops.py` fingerprints and the `neuron-explorer` attribution below.
- Use `scripts/runtime_ops.py` for device synchronization, control tensors, device identity, compile-cache mapping, profile start/stop, NEFF capture, event normalization, activation proof, and runtime/source fingerprints.
- A stack fingerprint names commits at two scopes. `execution_source.cwd_git.commit` and `sources[<dir>].commit` are the worktree HEAD when the fingerprint was taken. `server_stack_fingerprint.pumice_commit` is the engine commit that `preflight_pumice.py` recorded at scaffold time (`AMMO_STACK_FINGERPRINT`). The track worktrees branch from the campaign branch, and this value stays the same when you commit to a worktree. `run_pumice_grpc_bench.py` stamps a fresh fingerprint into every `e2e_latency_results.json`. A successful `--slot baseline` run also copies it into `state.json` `runtime.stack_fingerprint`, which `new_target.py` first wrote at scaffold time. Never edit a fingerprint by hand: that breaks its digest. To record a new source state, rerun the measurement.
- Compile cache: `NEURON_CACHE_ROOT` holds the NEFF cache. `--fresh-cache` has the same meaning as on CUDA. An arm whose Python or confdb sources changed since the last NEFF runs with `NEURON_DISABLE_COMPILE_CACHE=1`. A warm relaunch runs with `NEURON_ASSERT_CACHE_HIT=1`.
- If the server dies at TP bring-up and the first traceback in its log ends with `FileNotFoundError: [Errno 2] No such file or directory: '/psm_<hash>'`, `run_pumice_grpc_bench.py` relaunches it once and records `bringup_retries`, `bringup_retry_reason`, and `bringup_failed_server_log` on that launch's `launches` entry; a second failure stops the run and names both server logs.
- Attribution on Neuron is a standalone `neuron-explorer` chain, not a runner flag. Run the profiler-free timing first, then capture attribution as a separate step, per rank. Do not use `summary-json`: it is one aggregate dict that `normalize` rejects, and multi-rank `-d summary-json` panics in the vendor tool and writes a 0-byte file. The `StartProfile` RPC is CUDA-only and fails silently on Neuron. An error is logged, nothing crashes, and no artifact appears, so always confirm the profile file exists on disk before you trust a completed run.
  1. Capture. Take a NEFF the runner's server compiled (under `NEURON_CACHE_ROOT`) and run `scripts/runtime_ops.py capture-neff --neff <neff> --output <ntff> --tp-size <TP>`. This is the only step that touches the accelerator.
  2. Export. Run `neuron-explorer view --output-format json -n <neff> -s <ntff> --output-file <file>`. `--output-file` is required; redirecting stdout silently writes 0 bytes. Keep DMA. Never pass `--ignore-dma-trace`. Excluding DMA understated busy time by about 11 points in the run that proved this.
  3. Normalize. The export holds several row tables. Feed each table's row list to `scripts/runtime_ops.py normalize` on its own: `active_time` (labelled by `engine`), `cc_ops` (labelled by `operation`), and `dma` (no label field, so add `"name": "dma"` to each row before normalize). Then take the interval union across `pcore_idx`. LNC2 has two physical cores per logical core, so summing double counts.
- Record the mechanism, the raw per-rank NTFF artifacts, the profiler and schema versions, and the normalization fingerprint. Do not read the HBM gauges in `neuron_metrics.py`; no gate may use them.
- Keep profiled evidence separate from authoritative profiler-free E2E timing. Normalize Neuron events before `scripts/mine_trace.py` applies the existing interval-union, phase-share, physical-ceiling, and `f_e2e` calculations.
- At Gate 5.2, measure the production-parity plugin/NEFF-bucket compiled boundary of the Pumice Neuron runner, with the Stage 1 bucket set and runtime synchronization. Confirm that execution through `scripts/runtime_ops.py`, and record the runtime profile, bucket set, and compile/cache state. A raw or uncompiled proxy violates the same production-parity invariant. CUDA graphs and `torch.cuda.Event` do not apply. An isolated NKI microbenchmark stays proxy-only unless dispatch evidence proves that it reproduces that boundary.
- At Gate 5.3a, record expected NEFF/bucket compilation, compile-cache use, execution of the Pumice Neuron runner from the worktree, and absence of fallback. This is the Neuron activation proof in the existing gate position. Prove it with NeuronProfiler or `neuron-explorer` plus `scripts/runtime_ops.py`.

## Researcher

Use `neuron-nki-profiling` only to identify the NEFF to capture (its `identify-neffs.py` technique); capture itself goes through `scripts/runtime_ops.py capture-neff`, never through the skill's own capture path, which pins cores and LNC. Use `neuron-nki-profile-querying` for measured queries, `neuron-explorer-profile-schema` for version-matched fields and units, `neuron-nki-docs` for API or architecture facts, and `experimental-neuron-hardware-entitlement` for the physical bound (mandatory FLOPs and HBM bytes per operator) behind `physical_ceiling`. Supply measurements and physical bounds only. Do not propose an optimization. Preserve raw Neuron outputs and the normalized intervals. Time with `scripts/run_pumice_grpc_bench.py`, which has no `--accelerator-profile` flag on Neuron. If a capture fails, keep the raw NeuronProfiler and `neuron-explorer` output and diagnose through `scripts/runtime_ops.py` and the skills above.

## Champion

Stage 3 debate. Use `neuron-nki-docs` for API and architecture facts, `experimental-neuron-hardware-entitlement` for the physical bound behind a projection, and `experimental-neuron-memory-system` for SBUF, PSUM, and HBM placement of a kernel candidate. A bounded NKI prototype allowed by the protocol's Evidence section uses `neuron-nki-writing` and `neuron-nki-debugging` in the track worktree. Reuse the Stage 1 profile; do not recapture it.

## Implementer

Use `neuron-nki-docs` for API facts, `neuron-nki-writing` for an assigned NKI implementation, `neuron-nki-debugging` for NKI compile or runtime failures, `neuron-nki-profiling` for NEFF/NTFF capture, `neuron-nki-profile-querying` for profile analysis, and `neuron-explorer-profile-schema` for field definitions. When a framework change fails to compile or lowers badly, use `experimental-neuron-autoport-compiler-debugging-vllm-neuron` (torch-xla and neuronx-cc failures on the Pumice Neuron runner) and `experimental-neuron-perf-compiler-artifact-debugging` (what the compiler did with the graph). Use `experimental-neuron-infra-runtime-troubleshooting` for a device or runtime error, and `experimental-neuron-memory-system` for on-chip placement. The selected AMMO mechanism, worktree, gates, and verdict remain your authority.

## Monitor

Judge Neuron evidence by the same rules as CUDA. Profiler-free timing from `scripts/run_pumice_grpc_bench.py` is the only E2E authority. Attribution must come from the `neuron-explorer` chain above, and activation proof is the Gate 5.3a NEFF/bucket record. Use `neuron-nki-profile-querying` and `neuron-explorer-profile-schema` to check a cited profile value. A champion who cites Nsys has the wrong runtime.

## Auditor

Apply the same audit questions to both runtimes. On Neuron, reconstruct claims from raw profile outputs, version-matched schema, normalized intervals, activation proof, and fingerprints. Verify the `scripts/runtime_ops.py` evidence behind the activation proof, the intervals, and the fingerprints. Use `neuron-nki-profile-querying`, `neuron-explorer-profile-schema`, and `neuron-nki-docs`. A Neuron skill adds no audit gate or verdict rule.

## Delegate

Use only the skills named by the bounded prompt, from `neuron-nki-docs`, `neuron-nki-writing`, `neuron-nki-debugging`, `neuron-nki-profiling`, `neuron-nki-profile-querying`, and `neuron-explorer-profile-schema`. `neuron-nki-writing` is allowed only for an assigned NKI micro-experiment. Match the profiler to the claim: NeuronProfiler or `neuron-explorer` for timing and sequencing, `neuron-nki-profiling` and `neuron-nki-profile-querying` for kernel counters. A valid single-unit kernel experiment holds one logical NeuronCore; use the assigned production topology when the claim needs it. Use `scripts/runtime_ops.py` for runtime operations and to inspect fingerprints, activation proof, and normalized intervals. Preserve commands, raw values, raw profile files, normalized intervals, and source citations. The caller owns interpretation and decisions.
