# Patch mechanism inventory — vllm-neuron at the current pin

This inventory covers the vllm-neuron plugin at pin `vllm==0.21.0` (plugin version `0.21.0.1.0.0`, checkout `/Users/jinhun/GitHub/vllm-neuron`, branch `feature/p-eagle-gpt-oss-20b`, commit `0e19f00`). It is the authority a porter consults before choosing where a vendored upstream change lands. All paths are relative to the repo root. Line numbers move on a pin bump — re-derive them with grep and trust the mechanism over the line number.

## Pin identity

- vLLM pin: `requirements/core.txt:6` — `vllm==0.21.0`. Exact pin, no range. This file feeds the package's install requirements through `pyproject.toml:28` (`[tool.setuptools.dynamic] dependencies = {file = ["requirements/core.txt"]}`).
- Plugin version: `pyproject.toml:10` — `version = "0.21.0.1.0.0"`.
- Repo state: fork `jinhuang12/vllm-neuron`, branch `feature/p-eagle-gpt-oss-20b`, HEAD `0e19f00eb464b35d4436bf2a57450ad8b9c418e1`.

## Entry point

- Declaration: `pyproject.toml:24-25`
  ```toml
  [project.entry-points."vllm.platform_plugins"]
  neuron = "vllm_neuron:register"
  ```
- vLLM's plugin loader imports `vllm_neuron` and calls `register()` (`vllm_neuron/__init__.py:201-222`). `register()`:
  1. Returns `None` (no registration) if no `/dev/neuron*` device exists and neither `VLLM_NEURON_CPU_MODE=1` nor `VLLM_NEURON_CPU_COMPILE=1` is set (`__init__.py:210-215`).
  2. Calls `_patch_dcp_config_validation()` (Mechanism 2) (`__init__.py:218-220`).
  3. Returns the string `"vllm_neuron.vllm.platform.NeuronPlatform"` via `get_platform_class()` (`vllm_neuron/backend.py:85-93`). The return value is hardcoded; the `VLLM_NEURON_BACKEND` env var selects a `NeuronBackend` enum in `get_backend()` (`backend.py:24-77`), but `get_platform_class()` never consults it.
- Platform class: `NeuronPlatform(Platform)` at `vllm_neuron/vllm/platform.py:118` (922-line file; subclasses `vllm.platforms.Platform`, imported at `platform.py:19`).
- Importing `vllm_neuron` has side effects **before** `register()` runs. These execute in every process that imports the package, including spawn-mode worker subprocesses:
  - `os.environ["CUDA_VISIBLE_DEVICES"] = ""` (`__init__.py:9`).
  - Optional import redirector `torch_neuronx` → `libtorch_neuronx_lite`, a `sys.meta_path` finder gated on `VLLM_NEURON_LIBTORCH_NEURONX_LITE` (`__init__.py:14-17`, `vllm_neuron/utils/import_redirector.py:63-90`).
  - `PROMETHEUS_MULTIPROC_DIR` default (`__init__.py:34-39`).
  - `_init_backend()` (`__init__.py:68-192`): registers the `vllm_neuron` and `vllm_neuron_graph_capture` dynamo backends, installs `sys.modules["torch.neuron"]` (`__init__.py:113` or `:152`), and wraps `torch.accelerator.current_accelerator` (`__init__.py:164-179`; plus CPU-mode stream patches at `:182-186`).
  - The port-hold patch (Mechanism 4) applies at module scope (`__init__.py:196-198`).

## Mechanism 1: Platform hook and config-slot injection (sanctioned plugin API)

Not a monkeypatch. vLLM calls `NeuronPlatform` classmethod hooks; the plugin fills upstream config slots with dotted class paths. vLLM later imports those classes itself — including in worker subprocesses.

- `check_and_update_config` (`vllm_neuron/vllm/platform.py:278-395`), runs at engine config build in the process that constructs `VllmConfig` (per the comment at `vllm_neuron/__init__.py:194-195`, the EngineCore subprocess never calls it):
  - `parallel_config.worker_cls = "vllm_neuron.vllm.worker.neuron_worker.NeuronWorker"` — only when the slot is `"auto"` (`platform.py:322-325`). `NeuronWorker` then constructs `NeuronModelRunner` directly (`vllm_neuron/vllm/worker/neuron_worker.py:379-381`); the model runner has no config slot of its own.
  - `scheduler_config.scheduler_cls = "vllm_neuron.vllm.core.scheduler.NeuronScheduler"` or `...NeuronAsyncScheduler` — only when the slot is `None` or one of the two upstream defaults; an explicit non-default value is kept and a warning is logged (`platform.py:375-395`).
  - `_auto_set_neuron_connector_module_path` (`platform.py:733-755`) injects `kv_connector_module_path` for the connector names `NeuronNixlConnector` and `NeuronDecodeBenchConnector`.
- `pre_register_and_update` (`platform.py:152-161`) registers `SyntheticNeuronModel` through `vllm.model_executor.models.registry.ModelRegistry.register_model` when `VLLM_NEURON_SYNTHETIC_MODEL=1`.
- `get_attn_backend_cls` (`platform.py:663-670`) returns `"vllm_neuron.vllm.attention.attn.NeuronAttentionBackend"`.
- `apply_config_platform_defaults` (`platform.py:173`) and `update_block_size_for_backend` (`platform.py:165`) mutate config defaults (optimization level O1, block_size 32).
- KV connectors integrate by plain subclassing, no patching: `NeuronNixlAgentMetadata`/`NeuronNixlConnectorWorker`/`NeuronNixlConnector` (`vllm_neuron/vllm/kv_connector/neuron_nixl_connector.py:48/60/496`) subclass `vllm.distributed.kv_transfer.kv_connector.v1.nixl.*`; `NeuronDecodeBenchConnector*` (`neuron_decode_bench_connector.py:85/118/222`) subclass the upstream `DecodeBenchConnector*` classes plus `SupportsHMA`.

## Mechanism 2: DCP config-validation patch (class-method rebinding with audit-hook fallback)

- Site: `vllm_neuron/vllm/platform.py:53-115` (`_patch_dcp_config_validation`, `_apply_dcp_patch`; assignment at `:115`).
- Target: `vllm.config.model.ModelConfig.verify_with_parallel_config`.
- When: at plugin registration (`register()` → `__init__.py:220`). If `vllm.config.model` is not importable then (circular import), a `sys.addaudithook` handler (`platform.py:81-90`) applies the patch on the first successful import of `vllm.config.model`. The hook self-disables through a flag; it cannot be removed (CPython API limit).
- What it replaces: wraps the original method so the upstream DCP assertion is bypassed when `decode_context_parallel_size > 1` and the model does not use MLA (prefill-DCP case).
- Idempotence: module global `_dcp_config_patched` (`platform.py:49`) plus a `_neuron_dcp_patched` marker attribute on the replacement function (`platform.py:95-99, 114`).

## Mechanism 3: Termination-timeout patches (module-attribute and class-attribute rebinding)

- Site: `vllm_neuron/vllm/platform.py:783-902` (`_patch_termination_timeouts` :783, `_patch_shutdown` :828, `_patch_ensure_worker_termination` :867; assignments at `:863-864` and `:902`).
- Targets:
  - `vllm.v1.utils.shutdown` AND `vllm.v1.engine.utils.shutdown` — both module bindings must be replaced, because `vllm/v1/engine/utils.py` does `from vllm.v1.utils import shutdown` and `weakref.finalize` captures that second binding (rationale at `platform.py:799-804`).
  - `vllm.v1.executor.multiproc_executor.MultiprocExecutor._ensure_worker_termination` (staticmethod replaced on the class).
- When: from `check_and_update_config` (`platform.py:282`). Conditional: it is a no-op when `VLLM_NEURON_WORKER_TERMINATION_TIMEOUT` is at its default of 5 (`vllm_neuron/envs.py:45`, resolver `:207-208`; guard `platform.py:810-820`).
- What it replaces: the hardcoded 5 s (shutdown) and 4 s (worker termination) SIGTERM-to-SIGKILL windows, so Neuron profiling (`NEURON_RT_INSPECT_ENABLE=1`) can flush data.
- Idempotence: class flag `_termination_timeout_patched` (`platform.py:135, 804-806`).

## Mechanism 4: Port-hold patch (import-time function rebinding on torch)

- Site: `vllm_neuron/vllm/patches/port_hold_patch.py` (204 lines; `apply_port_hold_patch` :191-204, assignment :201).
- Target: `torch.distributed.init_process_group` (a torch API used by vLLM's distributed init, not a `vllm.*` symbol).
- When: at import of `vllm_neuron`, from module scope (`vllm_neuron/__init__.py:196-198`). Import-time application is deliberate: it survives spawn-mode re-imports, and the EngineCore subprocess never calls `check_and_update_config` (comment at `__init__.py:194-195`).
- What it replaces: for `tcp://127.0.0.1:<port>` init only, rank 0 binds the port immediately (or a fresh ephemeral port on `EADDRINUSE`), passes the fd to `TCPStore(master_listen_fd=)`, and publishes the actual port through a `/tmp/vllm_dist_port_<port>` rendezvous file that ranks 1..N poll. This removes a port-theft race against sibling NRT processes. Multi-node, `env://`, and explicit-store calls pass through unmodified (`port_hold_patch.py:56-62`).
- Idempotence: module global `_applied` (`port_hold_patch.py:29, 195-196`).

## Mechanism 5: Parallel-state module patching at worker init

- Site: `vllm_neuron/parallel/neuron_parallel_state.py` (imports the upstream module as `vllm_parallel_state` at `:33`).
  - `_patch_destroy` (`:622-629`) rebinds `vllm.distributed.parallel_state.destroy_model_parallel` to a wrapper that also destroys the Neuron groups. It saves the original in `_ORIGINAL_DESTROY_MODEL_PARALLEL` (`:94`) and restores it during teardown (`:1255`).
  - `_patch_getters` (`:631-662`) adds about 24 `get_neuron_*` accessor functions (EP, sampling DP, attention DP, embedding/LM-head/MLP DP, vision TP, DCP KV, wide-EP groups) as new attributes on `vllm.distributed.parallel_state`.
  - `in_the_same_node_as` replacement, two sites for the same target `vllm.distributed.parallel_state.in_the_same_node_as`: `vllm_neuron/vllm/worker/neuron_worker.py:433-490` (assignment `:490`, production path) and `neuron_parallel_state.py:806-822` (assignment `:822`, test/MPExecutor path). Reason: upstream `in_the_same_node_as` calls `torch.distributed.barrier()`, which reaches the accelerator hooks before backend dispatch and crashes on Neuron's PrivateUse1 backend.
- When: at worker initialization. `NeuronWorker.init_device` (`neuron_worker.py:346`) → `_init_neuron_distributed_environment_and_runtime` (`:492`) → the `in_the_same_node_as` patch (`:567`) → `init_neuron_distributed_environment` (`neuron_parallel_state.py:678`) → `_patch_getters(); _patch_destroy()` (`:757-758`). Test path: `neuron_parallel_state.py:935-936` and `vllm_neuron/utils/executor.py:986-987`.

## Mechanism 6: Pydantic schema mutation of ParallelConfig

- Site: `vllm_neuron/vllm/platform.py:758-780` (`_register_neuron_all2all_backend`; validator rebuild at `:780`).
- Target: `vllm.config.ParallelConfig.__pydantic_core_schema__` (the `all2all_backend` literal field) and `ParallelConfig.__pydantic_validator__`.
- When: unconditionally from `check_and_update_config` (`platform.py:283`).
- What it replaces: appends `"neuron"` to the accepted `all2all_backend` literal values and rebuilds the pydantic validator, because upstream only accepts CUDA backends.

## Ad hoc monkeypatches

Current verified count: **8 distinct vLLM-internal patch targets** across 6 patch sites, plus 4 torch-level patch sites.

| Patch site | Patched target | When it runs |
|---|---|---|
| `vllm_neuron/vllm/platform.py:93-115` (`_apply_dcp_patch`) | `vllm.config.model.ModelConfig.verify_with_parallel_config` | Plugin registration; audit-hook fallback fires on first import of `vllm.config.model` |
| `vllm_neuron/vllm/platform.py:828-864` (`_patch_shutdown`) | `vllm.v1.utils.shutdown` and `vllm.v1.engine.utils.shutdown` (both bindings) | `check_and_update_config`; only when `VLLM_NEURON_WORKER_TERMINATION_TIMEOUT` is not 5 |
| `vllm_neuron/vllm/platform.py:867-902` (`_patch_ensure_worker_termination`) | `vllm.v1.executor.multiproc_executor.MultiprocExecutor._ensure_worker_termination` | Same condition as above |
| `vllm_neuron/vllm/platform.py:758-780` (`_register_neuron_all2all_backend`) | `vllm.config.ParallelConfig.__pydantic_core_schema__` / `__pydantic_validator__` | `check_and_update_config`, unconditional |
| `vllm_neuron/parallel/neuron_parallel_state.py:622-629` (`_patch_destroy`) | `vllm.distributed.parallel_state.destroy_model_parallel` | Worker init (`init_neuron_distributed_environment`, `:757-758`); test path `:935-936`, `utils/executor.py:986-987` |
| `vllm_neuron/parallel/neuron_parallel_state.py:631-662` (`_patch_getters`) | `vllm.distributed.parallel_state` — adds about 24 `get_neuron_*` attributes | Same call sites as `_patch_destroy` |
| `vllm_neuron/vllm/worker/neuron_worker.py:433-490` | `vllm.distributed.parallel_state.in_the_same_node_as` | Worker init (`init_device` → `:567`) |
| `vllm_neuron/parallel/neuron_parallel_state.py:806-822` | `vllm.distributed.parallel_state.in_the_same_node_as` (test/MPExecutor path) | Once per worker process on the test path |

Torch-level patches (not vLLM internals, but they shape the same import-time layer):

| Patch site | Patched target | When it runs |
|---|---|---|
| `vllm_neuron/vllm/patches/port_hold_patch.py:191-204` | `torch.distributed.init_process_group` | Import of `vllm_neuron` (`__init__.py:196-198`) |
| `vllm_neuron/__init__.py:113, 152` | `sys.modules["torch.neuron"]`, `torch.neuron.*` | Import of `vllm_neuron` (`_init_backend`) |
| `vllm_neuron/__init__.py:164-186` | `torch.accelerator.current_accelerator` (plus CPU-mode `current_stream` / `current_device_index`) | Import of `vllm_neuron` (`_init_backend`) |
| `vllm_neuron/utils/import_redirector.py:63-90` | `sys.meta_path` finder: `torch_neuronx.*` → `libtorch_neuronx_lite.*`; blocks `libneuronxla` | Import of `vllm_neuron` (`__init__.py:16-17`), gated on `VLLM_NEURON_LIBTORCH_NEURONX_LITE` |

Collective dispatch registration (a separate seam, not a rebinding): `vllm_neuron/overrides/` registers `_c10d_functional::*` kernels through `torch.library.impl` — 7 on `AutogradPrivateUse1` (`overrides/neuron_collectives.py:12, 30, 42, 62, 74, 88, 101`) and 4 on `XLA` (`overrides/xla_collectives.py:50, 85, 121, 164`). The package auto-imports both modules (`overrides/__init__.py:9-12`) and is imported from `_init_backend` (`__init__.py:111`).

**Dead stub — do not use.** `vllm_neuron/vllm/patches/__init__.py` (16 lines) declares "All monkey-patches to upstream vLLM are applied here via `apply_patches()`. Called from `NeuronPlatform.check_and_update_config()`" and then defines `apply_patches()` with an empty body. Both claims are false: nothing calls it anywhere in the tree (a repo-wide grep for `apply_patches` matches only its own definition and its own docstring), and `check_and_update_config` does not reference it. A patch placed there never runs. Related stale config: `pyproject.toml:62` lists `vllm_neuron/patches/*` under `[tool.coverage.run] omit`, which does not match the real path `vllm_neuron/vllm/patches/*`.

## How patches layer at the pin

Application order in a full server start:

1. **Import time of `vllm_neuron`** — in every process that imports the package, including spawn-mode subprocesses: env defaults, import redirector, `_init_backend()` torch patches, dynamo backend registration, collective dispatch registration, port-hold patch.
2. **Plugin registration** — vLLM's plugin loader calls `register()`: DCP config-validation patch (or its audit hook), then `NeuronPlatform` is installed as the current platform from the returned class path.
3. **Platform hooks during config build** — `pre_register_and_update` (model registry), `apply_config_platform_defaults`, `update_block_size_for_backend`, then `check_and_update_config`: termination-timeout patches (env-gated), pydantic all2all schema patch, worker_cls / scheduler_cls / connector-module-path slot injection. This runs in the process that constructs `VllmConfig`; the EngineCore subprocess does not call it (`__init__.py:194-195`).
4. **Worker init** — `NeuronWorker.init_device`: `in_the_same_node_as` replacement, then `init_neuron_distributed_environment` applies `_patch_getters` / `_patch_destroy` onto `vllm.distributed.parallel_state`.

Precedence rules:

- Module- and class-attribute rebinding is last-writer-wins. Nothing in the plugin re-reads or defends a patched binding after application; every patch guards only against applying **itself** twice (flags `_dcp_config_patched`, `_neuron_dcp_patched`, `_termination_timeout_patched`, `_applied`).
- Config-slot injection defers to explicit user values: `worker_cls` is set only when `"auto"`; `scheduler_cls` only when `None` or an upstream default. A user-supplied class displaces the Neuron subclass.
- Wrapper patches (`_apply_dcp_patch`, `_patch_destroy`, port-hold) call the saved original, so they compose with the upstream behavior. Replacement patches (`shutdown`, `_ensure_worker_termination`, `in_the_same_node_as`) discard the original behavior entirely.
- Only `_patch_destroy` supports un-application (`_ORIGINAL_DESTROY_MODEL_PARALLEL` restored at `neuron_parallel_state.py:1255`). All other patches are permanent for the process lifetime.

## Porter rules

1. **Choose the layer by process scope.** If the change must be active in worker/EngineCore subprocesses under spawn, apply it at import time of `vllm_neuron` (pattern: `port_hold_patch.py` plus a call from `__init__.py`). If it only shapes engine configuration, put it in `NeuronPlatform.check_and_update_config`. If it needs an initialized distributed runtime, put it in the `NeuronWorker.init_device` chain.
2. **Prefer sanctioned upstream slots over rebinding.** Use `worker_cls`, `scheduler_cls`, `kv_connector` plus `kv_connector_module_path`, `get_attn_backend_cls`, and `ModelRegistry.register_model` when upstream exposes the seam. Subclass upstream classes (pattern: the NIXL connectors) instead of patching their methods.
3. **Do not put patches in `vllm_neuron/vllm/patches/__init__.py::apply_patches()`.** It is an empty stub with zero call sites. If you add a module under `vllm_neuron/vllm/patches/`, wire its `apply_*()` call explicitly from `vllm_neuron/__init__.py` or from a `NeuronPlatform` hook.
4. **When you rebind a module-level function, find and rebind every binding of it.** Upstream `from X import y` statements create copies that keep the old function (proof: the dual `shutdown` rebinding at `platform.py:863-864`, forced by a `weakref.finalize` capture). Grep upstream vLLM for `from <module> import <symbol>` before you patch `<module>.<symbol>`.
5. **Make every patch idempotent.** Guard with a module global or a marker attribute on the replacement (patterns: `_dcp_config_patched`, `_neuron_dcp_patched`, `_applied`, `_termination_timeout_patched`). Spawn re-imports and repeated platform-hook calls occur.
6. **Wrap, do not replace, when the upstream behavior must survive a version bump.** Save the original and call it (patterns: `_apply_dcp_patch`, `_patch_destroy`, the port-hold pass-through for non-loopback cases).
7. **Know what breaks a patch at a version bump:** the target module path moves or the symbol is renamed (the patch function then raises `ImportError` or `AttributeError` at apply time); upstream adds a new internal import binding of a patched function (rule 4); upstream changes the pydantic schema nesting that `_register_neuron_all2all_backend` walks (`platform.py:771-778` indexes four literal `"schema"` levels); circular-import timing at registration (only the DCP patch has an audit-hook fallback — copy that pattern if your patch must run at registration).
8. **Respect env gating and user overrides.** The termination patches are inert at the default timeout; slot injections yield to explicit user config. A vendored change must not assume a patch is always active — check its guard condition first.
9. **Do not conflate vLLM patches with torch patches.** `init_process_group`, `torch.neuron`, `torch.accelerator.*`, and the meta-path redirector patch torch, apply at import time, and are unconditional (the redirector excepted). Changes to torch-adjacent behavior land there, not in `NeuronPlatform`.
