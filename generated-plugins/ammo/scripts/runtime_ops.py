#!/usr/bin/env python3
"""Accelerator runtime operations for AMMO's existing benchmark lifecycle.

This module is a narrow binding layer. It does not own AMMO stages, gates,
workloads, statistics, accuracy criteria, verdicts, or stop policy. The default
is CUDA so historical sessions keep their current behavior. A Neuron server
sets ``AMMO_RUNTIME_PROFILE=vllm-neuron`` once at startup.

The module is also a small command-line adapter for Neuron profile artifacts:

* ``normalize`` converts neuron-explorer JSON or Parquet rows to
  ``ammo_intervals/1``. ``mine_trace.py`` consumes that format and reuses its
  existing interval-union, phase-share, physical-ceiling, and ``f_e2e`` math.
* ``capture-neff`` runs the supported neuron-explorer NEFF/NTFF capture route.
* ``fingerprint`` prints the runtime and editable-source fingerprint stamped
  into every sweep result.

Profile artifacts are attribution evidence. The profiler-free latency sweep
remains the only authoritative end-to-end timing source.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import campaign_paths  # noqa: E402


CUDA_PROFILE = "vllm-cuda"
NEURON_PROFILE = "vllm-neuron"
PUMICE_CUDA_PROFILE = "pumice-cuda"
PUMICE_NEURON_PROFILE = "pumice-neuron"
# Profiles grouped by accelerator kind. The pumice-* profiles run the same
# hardware as the legacy vllm-* profiles; only the engine differs.
CUDA_PROFILES = frozenset({CUDA_PROFILE, PUMICE_CUDA_PROFILE})
NEURON_PROFILES = frozenset({NEURON_PROFILE, PUMICE_NEURON_PROFILE})
SUPPORTED_PROFILES = CUDA_PROFILES | NEURON_PROFILES
# Old campaign records were scaffolded under the vllm-* profiles. Map each to
# its pumice-* successor so a pumice server can still load and verify them.
# This is load-time normalization only; a live vllm-* session still refuses to
# resume on a pumice server (see orchestration/session_manager.py).
LEGACY_PROFILE_ALIASES = {
    "vllm-cuda": PUMICE_CUDA_PROFILE,
    "vllm-neuron": PUMICE_NEURON_PROFILE,
}
INTERVAL_SCHEMA = "ammo_intervals/1"
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# Source-scoped compatibility adapters for direct vLLM-Neuron revisions whose
# checkpoint loader cannot consume a Hugging Face repository ID. Keep this map
# narrow: it is a runtime load seam, not a model-support registry or an AMMO
# acceptance rule.
NEURON_LOCAL_CHECKPOINT_ADAPTERS: Mapping[str, frozenset[str]] = {
    "41758812f2b30ca7b43ec6c2a1137c927f53c7a1": frozenset(
        {"GlmMoeDsaForCausalLM"}
    ),
}


def _package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return None


def _run_text(argv: Sequence[str], *, cwd: Optional[Path] = None) -> Optional[str]:
    """Run a bounded read-only probe and return stripped stdout."""
    try:
        result = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _git_fingerprint(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_dir():
        return None
    commit = _run_text(["git", "rev-parse", "HEAD"], cwd=path)
    if commit is None:
        return None
    status = _run_text(["git", "status", "--porcelain", "--untracked-files=no"], cwd=path)
    branch = _run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    return {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status),
    }


def _module_source_path(name: str) -> Optional[Path]:
    """Return the resolved file imported for *name* without importing it."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or origin in {"built-in", "frozen"}:
        return None
    try:
        return Path(origin).expanduser().resolve()
    except OSError:
        return None


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _copy_engine_args(engine_args: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the few nested structures that runtime configuration may update."""
    out = dict(engine_args)
    for key in ("additional_config", "profiler_config"):
        value = out.get(key)
        if isinstance(value, Mapping):
            out[key] = copy.deepcopy(dict(value))
    return out


@dataclass(frozen=True)
class AcceleratorRuntime:
    runtime_profile: str
    env: Mapping[str, str]

    @property
    def accelerator_kind(self) -> str:
        return "neuron" if self.runtime_profile in NEURON_PROFILES else "cuda"

    @property
    def profiler_route(self) -> str:
        return (
            "neuron-profiler" if self.runtime_profile in NEURON_PROFILES else "nsys"
        )

    @property
    def is_neuron(self) -> bool:
        return self.runtime_profile in NEURON_PROFILES

    @property
    def is_cuda(self) -> bool:
        return self.runtime_profile in CUDA_PROFILES

    def requires_local_checkpoint(
        self, source_commit: str, architectures: Sequence[str]
    ) -> bool:
        """Return whether this exact Neuron source/model pair needs a path.

        CUDA never enters this compatibility seam. Neuron requires an exact
        source commit before selecting an adapter so a later plugin release
        cannot inherit a historical loader workaround by accident.
        """
        if self.is_cuda:
            return False
        if not isinstance(source_commit, str) or _COMMIT_RE.fullmatch(
            source_commit
        ) is None:
            raise ValueError(
                "vLLM-Neuron source commit must be a 40-character Git SHA"
            )
        if (
            isinstance(architectures, (str, bytes))
            or not architectures
            or not all(
                isinstance(item, str) and item.strip() for item in architectures
            )
        ):
            raise ValueError("Hugging Face architectures must be non-empty strings")
        required = NEURON_LOCAL_CHECKPOINT_ADAPTERS.get(source_commit.lower())
        return bool(required and required.intersection(architectures))

    def compile_cache_env(
        self, env: Mapping[str, str], sweep_cache_root: Path
    ) -> dict[str, str]:
        """Map AMMO's existing fresh-cache root to the active compiler cache."""
        root = Path(sweep_cache_root)
        out = dict(env)
        out["VLLM_CACHE_ROOT"] = str(root)
        if self.is_cuda:
            out["TRITON_CACHE_DIR"] = str(root / "triton_cache")
            return out

        # vLLM-Neuron derives its default NEFF cache from VLLM_CACHE_ROOT. The
        # explicit compiler URL keeps direct torch-neuronx compilation and the
        # plugin on the same isolated tree. Remove a stale CUDA-only cache var.
        out.pop("TRITON_CACHE_DIR", None)
        out["NEURON_COMPILE_CACHE_URL"] = str(root / "neuron" / "compile_cache")
        return out

    def _xla_model(self) -> Any:
        try:
            return importlib.import_module("torch_xla.core.xla_model")
        except Exception as exc:
            raise RuntimeError(
                "vllm-neuron runtime requires torch_xla.core.xla_model"
            ) from exc

    def synchronize(self, torch_mod: Any) -> None:
        if self.is_cuda:
            torch_mod.cuda.synchronize()
            return
        xm = self._xla_model()
        # mark_step materializes lazy graph work; wait_device_ops drains it.
        if hasattr(xm, "mark_step"):
            xm.mark_step()
        if hasattr(xm, "wait_device_ops"):
            xm.wait_device_ops()
            return
        sync = getattr(importlib.import_module("torch_xla"), "sync", None)
        if callable(sync):
            sync(wait=True)
            return
        raise RuntimeError("torch-xla exposes no supported device synchronization API")

    def control_device(self, torch_mod: Any) -> Any:
        if self.is_cuda:
            return "cuda"
        xm = self._xla_model()
        if hasattr(xm, "xla_device"):
            return xm.xla_device()
        return torch_mod.device("xla")

    def control_tensor(
        self, torch_mod: Any, value: Any, *, dtype: Any
    ) -> Any:
        return torch_mod.tensor(
            value, dtype=dtype, device=self.control_device(torch_mod)
        )

    def device_identity(self, torch_mod: Any) -> dict[str, Any]:
        if self.is_cuda:
            available = bool(torch_mod.cuda.is_available())
            return {
                "kind": "cuda",
                "name": torch_mod.cuda.get_device_name(0) if available else "unknown",
                "visible_devices": self.env.get("CUDA_VISIBLE_DEVICES"),
            }
        xm = self._xla_model()
        devices: list[str] = []
        try:
            devices = [str(v) for v in xm.get_xla_supported_devices()]
        except Exception:
            pass
        return {
            "kind": "neuron",
            "name": self.env.get("AMMO_ACCELERATOR_NAME", "AWS Neuron"),
            "visible_cores": self.env.get("NEURON_VISIBLE_DEVICES")
            or self.env.get("NEURON_RT_VISIBLE_CORES"),
            "lnc": self.env.get("NEURON_LOGICAL_NC_CONFIG"),
            "xla_devices": devices,
        }

    def configure_engine_profiling(
        self, engine_args: Mapping[str, Any], output_dir: Path
    ) -> dict[str, Any]:
        """Configure vLLM's plugin profiler without changing CUDA arguments.

        vLLM currently exposes plugin worker profilers through the ``cuda``
        ProfilerKind slot. vLLM-Neuron reinterprets that slot as
        ``NeuronProfiler``; this is why the literal remains ``cuda`` here.
        """
        out = _copy_engine_args(engine_args)
        if not self.is_neuron:
            return out
        additional = out.get("additional_config")
        if not isinstance(additional, MutableMapping):
            additional = {}
        else:
            additional = copy.deepcopy(dict(additional))
        neuron_profiler = additional.get("neuron_profiler")
        if not isinstance(neuron_profiler, MutableMapping):
            neuron_profiler = {}
        else:
            neuron_profiler = copy.deepcopy(dict(neuron_profiler))
        neuron_profiler.setdefault("activities", ["device_profile", "system_profile"])
        neuron_profiler["output_dir"] = str(Path(output_dir))
        additional["neuron_profiler"] = neuron_profiler
        out["additional_config"] = additional

        profiler = out.get("profiler_config")
        if not isinstance(profiler, MutableMapping):
            profiler = {}
        else:
            profiler = copy.deepcopy(dict(profiler))
        profiler["profiler"] = "cuda"
        out["profiler_config"] = profiler
        return out

    def start_profile(self, llm: Any) -> None:
        if not self.is_neuron:
            raise RuntimeError("runtime start_profile is reserved for vllm-neuron")
        llm.start_profile()

    def stop_profile(self, llm: Any) -> None:
        if not self.is_neuron:
            raise RuntimeError("runtime stop_profile is reserved for vllm-neuron")
        llm.stop_profile()

    def activation_proof(
        self, log_text: str, cache_root: Optional[Path] = None
    ) -> dict[str, Any]:
        """Return backend-neutral Gate-5.3a activation signals.

        This record is evidence input. It does not alter AMMO's gate or verdict
        rules. CUDA continues to use the configured log-pattern proof. Neuron
        records plugin execution, NEFF/bucket compilation, cache use, optional
        compiled-artifact corroboration, and logged fallback. The sweep binds
        these signals to a real NTFF or NEFF primary artifact before Gate 5.3a
        can pass.
        """
        text = log_text or ""
        if self.is_cuda:
            return {
                "runtime_profile": CUDA_PROFILE,
                "mechanism": "cuda_graph",
                "status": "configured_patterns_required",
                "hits": 0,
            }

        def hit(pattern: str) -> bool:
            return re.search(pattern, text, re.IGNORECASE | re.MULTILINE) is not None

        artifact = False
        artifact_sample: Optional[str] = None
        if cache_root is not None and Path(cache_root).exists():
            try:
                found = next(Path(cache_root).rglob("*.neff"), None)
            except OSError:
                found = None
            if found is not None:
                artifact = True
                artifact_sample = str(found)

        fallback = hit(
            r"fall(?:ing)?\s+back\s+to\s+(?:cpu|cuda|eager|upstream)|"
            r"neuron[^\n]{0,80}(?:disabled|fallback)"
        )
        checks = {
            "plugin_execution": hit(
                r"vllm[_-]neuron|neuron(?:platform|model|worker)[^\n]{0,40}(?:plugin|runner|activated)"
            ),
            "neff_or_bucket_compilation": hit(
                r"(?:compil(?:e|ed|ing)|load(?:ed|ing))[^\n]{0,80}(?:neff|bucket)|"
                r"(?:neff|bucket)[^\n]{0,80}(?:compil(?:e|ed|ing)|load(?:ed|ing))"
            ),
            "compile_cache_use": hit(
                r"compile[_ -]?cache|cache\s+(?:hit|reuse|used)|using\s+cached"
            ),
            "compiled_artifact_present": artifact,
            "fallback_absent": not fallback,
        }
        # A file-system-visible NEFF is useful corroboration but is not part of
        # the serving activation contract: the runtime may keep the compiled
        # artifact in a cache location outside the session mount. Gate 5.3a
        # binds the four observable serving signals named by the contract.
        passed = all(
            checks[name]
            for name in (
                "plugin_execution",
                "neff_or_bucket_compilation",
                "compile_cache_use",
                "fallback_absent",
            )
        )
        return {
            "runtime_profile": NEURON_PROFILE,
            "mechanism": "neff_bucket",
            "status": "pass" if passed else "fail",
            "hits": sum(1 for value in checks.values() if value),
            "checks": checks,
            "compiled_artifact_sample": artifact_sample,
        }

    def stack_fingerprint(
        self,
        env: Optional[Mapping[str, str]] = None,
        *,
        extra_source_dirs: Sequence[Path] = (),
    ) -> dict[str, Any]:
        """Return the self-digested runtime and source fingerprint.

        Each git block records the tree HEAD at the moment of this call:

        * ``execution_source.cwd_git.commit``: HEAD of the working directory.
        * ``sources[<dir>].commit``: HEAD of each source directory named by
          ``AMMO_RUNTIME_SOURCE_DIR`` (the §4 alias of ``AMMO_ENGINE_ROOT``,
          the campaign engine root that ``campaign_paths.apply_env`` fills)
          and of each ``extra_source_dirs`` entry (the runner's ``--repo-root``).
        * ``server_stack_fingerprint``: ``AMMO_STACK_FINGERPRINT``, the stack
          that ``preflight_pumice.py --write-fingerprint`` recorded in
          ``target.json`` at scaffold. Its ``pumice_commit`` is the engine
          HEAD then. It does not change when a track worktree gets new commits.

        Capture this when a measurement runs. A copy taken earlier describes
        the tree as it was then.
        """
        values = dict(self.env if env is None else env)
        raw_server_stack = values.get("AMMO_STACK_FINGERPRINT")
        server_stack: Optional[dict[str, Any]] = None
        if raw_server_stack:
            try:
                decoded = json.loads(raw_server_stack)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "AMMO_STACK_FINGERPRINT must be a JSON object"
                ) from exc
            if not isinstance(decoded, dict):
                raise ValueError("AMMO_STACK_FINGERPRINT must be a JSON object")
            server_stack = decoded
        packages = {
            name: _package_version(name)
            for name in (
                "vllm",
                "vllm-neuron",
                "torch",
                "torch-xla",
                "neuronx-cc",
                "libtorch-neuronx-lite",
            )
        }
        source_candidates = []
        for key in (
            "AMMO_RUNTIME_SOURCE_DIR",
            "VLLM_NEURON_SOURCE_DIR",
            "VLLM_SOURCE_DIR",
        ):
            raw = values.get(key)
            if raw:
                path = str(Path(raw).expanduser().resolve())
                if path not in source_candidates:
                    source_candidates.append(path)
        for extra in extra_source_dirs:
            path = str(Path(extra).expanduser().resolve())
            if path not in source_candidates:
                source_candidates.append(path)
        sources: dict[str, Any] = {}
        for raw in source_candidates:
            details = _git_fingerprint(Path(raw))
            if details is not None:
                sources[raw] = details

        # Record the directory the benchmark really executes from and the
        # module file Python resolves. An outer session worktree and an
        # operation worktree can share environment pins, so those pins alone
        # do not identify the source that produced a measurement.
        active_cwd = Path.cwd().resolve()
        module_sources: dict[str, Any] = {}
        for module_name in ("vllm", "vllm_neuron"):
            module_path = _module_source_path(module_name)
            if module_path is None:
                continue
            module_sources[module_name] = {
                "path": str(module_path),
                "git": _git_fingerprint(module_path.parent),
            }

        runtime_env_keys = (
            "AMMO_RUNTIME_PROFILE",
            "AMMO_ACCELERATOR_NAME",
            "AMMO_INSTANCE_FAMILY",
            "AMMO_STACK_IMAGE_DIGEST",
            "CUDA_VISIBLE_DEVICES",
            "NEURON_VISIBLE_DEVICES",
            "NEURON_RT_VISIBLE_CORES",
            "NEURON_LOGICAL_NC_CONFIG",
            "VLLM_CACHE_ROOT",
            "NEURON_COMPILE_CACHE_URL",
        )
        source_env_keys = (
            "AMMO_RUNTIME_SOURCE_COMMIT",
            "VLLM_NEURON_SOURCE_COMMIT",
            "VLLM_SOURCE_COMMIT",
        )
        payload: dict[str, Any] = {
            "runtime_profile": self.runtime_profile,
            "accelerator_kind": self.accelerator_kind,
            "packages": packages,
            "sources": sources,
            "environment": {
                key: values[key] for key in runtime_env_keys if values.get(key)
            },
            "source_environment": {
                key: values[key] for key in source_env_keys if values.get(key)
            },
            "execution_source": {
                "cwd": str(active_cwd),
                "cwd_git": _git_fingerprint(active_cwd),
                "modules": module_sources,
            },
        }
        if server_stack is not None:
            payload["server_stack_fingerprint"] = server_stack
        return {"digest": _canonical_digest(payload), **payload}


def resolve_runtime(
    env: Optional[Mapping[str, str]] = None,
) -> AcceleratorRuntime:
    values = dict(os.environ if env is None else env)
    profile = values.get("AMMO_RUNTIME_PROFILE", CUDA_PROFILE).strip()
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(
            "AMMO_RUNTIME_PROFILE must be one of: "
            + ", ".join(sorted(SUPPORTED_PROFILES))
        )
    return AcceleratorRuntime(profile, values)


def require_supported_profile(
    profile: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the runtime profile this plugin runs, or raise ValueError.

    The one CUDA-only gate (conventions §6). ``new_target.py`` and
    ``preflight_pumice.py`` call it. The plugin runs only ``pumice-cuda``;
    ``resolve_runtime`` above still accepts every profile so the Neuron
    branches and old campaign records keep loading.

    ``profile`` wins when given. Else ``AMMO_RUNTIME_PROFILE`` from *env*
    (default ``os.environ``) is used; unset or empty means ``pumice-cuda``,
    the default in references/campaign-env.md. A set ``AMMO_ACCELERATOR_KIND``
    must be ``cuda``.
    """
    values = os.environ if env is None else env
    raw = profile if profile is not None else values.get("AMMO_RUNTIME_PROFILE")
    chosen = (raw or "").strip() or PUMICE_CUDA_PROFILE
    if chosen != PUMICE_CUDA_PROFILE:
        raise ValueError(
            f"runtime profile {chosen!r} is not supported: this plugin runs "
            f"only the {PUMICE_CUDA_PROFILE!r} profile"
        )
    kind = (values.get("AMMO_ACCELERATOR_KIND") or "").strip().lower()
    if kind and kind != "cuda":
        raise ValueError(
            f"AMMO_ACCELERATOR_KIND={kind!r} is not supported: the "
            f"{PUMICE_CUDA_PROFILE!r} profile runs on CUDA"
        )
    return PUMICE_CUDA_PROFILE


def _visible_resource_ids(value: str, *, allow_ranges: bool) -> list[int]:
    """Parse the numeric visibility forms emitted by AMMO providers."""
    raw = value.strip()
    if not raw or raw == "-1":
        return []
    result: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"invalid empty resource ID in {value!r}")
        if allow_ranges and "-" in token:
            parts = token.split("-", 1)
            try:
                start, end = int(parts[0]), int(parts[1])
            except ValueError as exc:
                raise ValueError(
                    f"invalid NeuronCore visibility range {token!r}"
                ) from exc
            if start < 0 or end < start:
                raise ValueError(f"invalid NeuronCore visibility range {token!r}")
            result.extend(range(start, end + 1))
            continue
        try:
            item = int(token)
        except ValueError as exc:
            raise ValueError(f"resource ID must be numeric, got {token!r}") from exc
        if item < 0:
            raise ValueError(f"resource ID must be non-negative, got {item}")
        result.append(item)
    if len(set(result)) != len(result):
        raise ValueError(f"visibility contains duplicate resource IDs: {value!r}")
    return result


def _cuda_visible_resource_ids(value: str) -> list[int]:
    """Map CUDA numeric or UUID visibility to app-local resource IDs.

    CUDA renumbers every visible GPU or MIG instance from zero inside the
    process. Preserve that app-local truth for UUID visibility. A mixed list
    is ambiguous because numeric, full-GPU UUID, and MIG UUID tokens name
    different scopes, so fail closed.
    """
    raw = value.strip()
    if not raw or raw == "-1":
        return []
    tokens = [token.strip() for token in raw.split(",")]
    if any(not token for token in tokens):
        raise ValueError(f"invalid empty CUDA visibility token in {value!r}")

    scopes: list[str] = []
    for token in tokens:
        if re.fullmatch(r"\d+", token):
            scopes.append("numeric")
        elif re.fullmatch(r"GPU-[A-Za-z0-9-]+", token):
            scopes.append("gpu_uuid")
        elif re.fullmatch(r"MIG-[A-Za-z0-9][A-Za-z0-9._/-]*", token):
            scopes.append("mig_uuid")
        else:
            raise ValueError(f"invalid CUDA visibility token {token!r}")
    if len(set(scopes)) != 1:
        raise ValueError(
            "CUDA_VISIBLE_DEVICES cannot mix numeric, GPU UUID, or MIG UUID scopes"
        )
    if len(set(tokens)) != len(tokens):
        raise ValueError(f"CUDA visibility contains duplicate resources: {value!r}")
    if scopes[0] == "numeric":
        return _visible_resource_ids(value, allow_ranges=False)
    return list(range(len(tokens)))


def campaign_runtime_metadata(
    env: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Build additive runtime/resource state for ``new_target.py``.

    Historical clients did not set ``AMMO_RUNTIME_PROFILE`` or
    ``CUDA_VISIBLE_DEVICES``. That exact case retains the old one-GPU scaffold.
    Neuron state is derived only from the provider-owned contiguous visibility
    range; it never fabricates GPU fields.
    """
    values = dict(os.environ if env is None else env)
    runtime = resolve_runtime(values)
    declared_kind = values.get("AMMO_ACCELERATOR_KIND")
    if declared_kind and declared_kind != runtime.accelerator_kind:
        raise ValueError(
            "AMMO_ACCELERATOR_KIND conflicts with AMMO_RUNTIME_PROFILE"
        )

    if runtime.is_cuda:
        resource_unit = "gpu"
        visible = values.get("CUDA_VISIBLE_DEVICES", "0")
        resource_ids = _cuda_visible_resource_ids(visible)
        lnc: Optional[int] = None
    else:
        resource_unit = "neuron_core"
        visible_name = (
            "NEURON_VISIBLE_DEVICES"
            if values.get("NEURON_VISIBLE_DEVICES") is not None
            else "NEURON_RT_VISIBLE_CORES"
        )
        visible = values.get(visible_name, "")
        resource_ids = _visible_resource_ids(visible, allow_ranges=True)
        if resource_ids and resource_ids != list(
            range(resource_ids[0], resource_ids[0] + len(resource_ids))
        ):
            raise ValueError(
                f"{visible_name} must name one contiguous core range"
            )
        raw_lnc = values.get(
            "NEURON_LOGICAL_NC_CONFIG", values.get("AMMO_NEURON_LNC", "2")
        )
        try:
            lnc = int(raw_lnc)
        except (TypeError, ValueError) as exc:
            raise ValueError("Neuron LNC must be 1 or 2") from exc
        if lnc not in (1, 2):
            raise ValueError("Neuron LNC must be 1 or 2")

    common = {
        "accelerator_kind": runtime.accelerator_kind,
        "resource_unit": resource_unit,
        "resource_count": len(resource_ids),
        "resource_ids": resource_ids,
    }
    return {
        "runtime": {
            "runtime_profile": runtime.runtime_profile,
            **common,
            "instance_family": values.get("AMMO_INSTANCE_FAMILY") or None,
            "lnc": lnc,
            "stack_fingerprint": runtime.stack_fingerprint(values),
        },
        "accelerator_resources": {
            **common,
            "visible_resources": visible,
        },
    }


def _first(row: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    return None


def _number(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Neuron event {field} must be numeric, got {value!r}")
    return int(value)


def normalize_neuron_events(
    rows: Iterable[Mapping[str, Any]],
    *,
    source: str,
    profiler_version: Optional[str] = None,
    schema_version: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize neuron-explorer rows to AMMO's seven-field interval shape.

    Neuron Explorer schemas evolve. Field aliases here cover the stable event
    concepts while the output records the installed schema version. Unknown or
    malformed rows fail loud; they are never guessed into gate evidence.
    """
    events: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"Neuron event {index} must be an object")
        start_value = _first(
            row,
            (
                "start_ns",
                "start_ts",
                "start_time_ns",
                "start_time",
                "timestamp_ns",
                "ts",
                # neuron-explorer 2.30.10.0 `view --output-format json` names the
                # start field bare `timestamp` in the instruction and cc_ops
                # tables. Keep it last so the explicit `*_ns` names win first.
                "timestamp",
            ),
        )
        end_value = _first(
            row, ("end_ns", "end_ts", "end_time_ns", "end_time")
        )
        duration_value = _first(row, ("duration_ns", "duration"))
        if start_value is None:
            raise ValueError(f"Neuron event {index} has no start timestamp")
        start_ns = _number(start_value, "start")
        if end_value is None:
            if duration_value is None:
                raise ValueError(f"Neuron event {index} has no end or duration")
            end_ns = start_ns + _number(duration_value, "duration")
        else:
            end_ns = _number(end_value, "end")
        if end_ns < start_ns:
            raise ValueError(f"Neuron event {index} has end before start")

        graph_id = _first(
            row, ("graph_id", "neff_id", "model_id", "model_name", "neff_name")
        )
        symbol = _first(
            row,
            (
                "opcode",
                "model_id",
                "model_name",
                "neff_name",
                "op_name",
                "instruction_name",
                "event_name",
                "name",
                "event_type",
                "event",
                # neuron-explorer 2.30.10.0 `view --output-format json` labels
                # active_time rows only by `engine` (tensor, vector, scalar,
                # gpsimd, sync, cc_instruction). `engine` is also a stream_id
                # alias below; a row that carries only `engine` reuses that one
                # value for both fields, which is the intended per-engine
                # window identity, not a collision.
                "engine",
                # The cc_ops table names the collective by `operation`
                # (AllReduce, AllGather, or Invalid for the startup barrier).
                # Its `type` is the constant "cc_op" and its `dtype` is the
                # payload dtype, so neither identifies the op; keep `operation`
                # last so it never shadows a real instruction/model name.
                "operation",
            ),
        )
        if symbol is None:
            raise ValueError(f"Neuron event {index} has no event or model name")
        device_id = _first(
            row,
            (
                "device_id",
                "deviceId",
                "neuron_core",
                "nc_id",
                "logical_nc",
                "pcore_idx",
            ),
        )
        stream_id = _first(row, ("stream_id", "streamId", "engine_id", "engine"))
        process_id = _first(
            row, ("process_id", "globalPid", "pid", "worker_gid", "rank")
        )
        events.append(
            {
                "start_ns": start_ns,
                "end_ns": end_ns,
                "symbol": str(symbol),
                "device_id": 0 if device_id is None else device_id,
                "stream_id": 0 if stream_id is None else stream_id,
                "process_id": 0 if process_id is None else process_id,
                "graph_id": graph_id,
            }
        )
    events.sort(key=lambda item: (item["start_ns"], item["end_ns"], item["symbol"]))
    return {
        "schema": INTERVAL_SCHEMA,
        "runtime_profile": NEURON_PROFILE,
        "source": source,
        "profiler": {
            "product": "neuron-explorer",
            "product_version": profiler_version,
            "schema_version": schema_version,
        },
        "events": events,
    }


def _load_profile_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, Mapping):
            for key in ("events", "rows", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value]
        if isinstance(data, list):
            return [dict(item) for item in data]
        raise ValueError(f"{path}: JSON must be a row list or contain events/rows/data")
    if path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq  # type: ignore

            return [dict(item) for item in pq.read_table(path).to_pylist()]
        except ImportError:
            try:
                import pandas as pd  # type: ignore

                return pd.read_parquet(path).to_dict(orient="records")
            except ImportError as exc:
                raise RuntimeError(
                    "Parquet normalization requires pyarrow or pandas"
                ) from exc
    raise ValueError(f"Unsupported profile input: {path} (want .json or .parquet)")


def build_neuron_explorer_capture_command(
    *, neff: Path, output: Path, profile_nth_exec: int = 2, tp_size: int = 1
) -> list[str]:
    if profile_nth_exec < 1 or tp_size < 1:
        raise ValueError("profile_nth_exec and tp_size must be positive")
    return [
        "neuron-explorer",
        "capture",
        "-n",
        str(neff),
        "-s",
        str(output),
        f"--profile-nth-exec={profile_nth_exec}",
        "--enable-dge-notifs",
        f"--collectives-workers-per-node={tp_size}",
        f"--collectives-worker-count={tp_size}",
    ]


def _main(argv: Optional[Sequence[str]] = None) -> int:
    campaign_paths.apply_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    fp = sub.add_parser("fingerprint", help="Print the runtime stack fingerprint")
    fp.add_argument("--output", type=Path)

    norm = sub.add_parser(
        "normalize", help="Normalize neuron-explorer JSON/Parquet events"
    )
    norm.add_argument("--input", type=Path, required=True)
    norm.add_argument("--output", type=Path, required=True)
    norm.add_argument("--profiler-version")
    norm.add_argument("--schema-version")

    capture = sub.add_parser(
        "capture-neff", help="Capture one NEFF to NTFF with neuron-explorer"
    )
    capture.add_argument("--neff", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--profile-nth-exec", type=int, default=2)
    capture.add_argument("--tp-size", type=int, default=1)

    args = parser.parse_args(argv)
    if args.command == "fingerprint":
        payload = resolve_runtime().stack_fingerprint()
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "normalize":
        rows = _load_profile_rows(args.input)
        payload = normalize_neuron_events(
            rows,
            source=str(args.input),
            profiler_version=args.profiler_version,
            schema_version=args.schema_version,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.command == "capture-neff":
        if not args.neff.is_file():
            raise SystemExit(f"NEFF not found: {args.neff}")
        if shutil.which("neuron-explorer") is None:
            raise SystemExit("neuron-explorer not found on PATH")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        command = build_neuron_explorer_capture_command(
            neff=args.neff,
            output=args.output,
            profile_nth_exec=args.profile_nth_exec,
            tp_size=args.tp_size,
        )
        env = dict(os.environ)
        env["NEURON_RT_ENABLE_DGE_NOTIFICATIONS"] = "1"
        return subprocess.run(command, env=env, check=False).returncode
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(_main())
