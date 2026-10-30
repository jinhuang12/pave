#!/usr/bin/env python3
"""Accelerator reservation module for AMMO benchmark orchestration.

Provides a file-locked, JSON-backed reservation system for NVIDIA GPUs and
logical AWS NeuronCores. ``gpu_reservation.py`` remains the public filename and
CUDA CLI for compatibility. The binding is automatic:

* CUDA uses ``CUDA_VISIBLE_DEVICES`` and prints comma-separated physical IDs.
* vLLM-Neuron uses ``NEURON_VISIBLE_DEVICES`` and prints one contiguous
  ``start-end`` range that can be assigned back to ``NEURON_VISIBLE_DEVICES``.
  Standalone NKI/runtime commands may still use ``NEURON_RT_VISIBLE_CORES``.

Neuron IDs in this file are host-global IDs from the server-provided visible
range. The child process sees them as application-local IDs starting at zero,
which is the Neuron Runtime isolation contract.

State lives under ``AMMO_GPU_RES_DIR`` when set. Otherwise CUDA uses the one
host-wide lease table ``/tmp/ammo_gpu_res``, so two campaigns on one host can
never hold the same physical GPU. The CUDA pool is every GPU that
``nvidia-smi --query-gpu=index --format=csv,noheader`` lists (a torch device
count is the fallback). ``reserve`` takes only GPUs that the caller's
``CUDA_VISIBLE_DEVICES`` names, when it names any. Leases are owned by the
session id, which is the campaign id. The Neuron binding keeps its
visibility-hash state dir.

Agents dynamically request N GPUs from the pool:
    CVD=$(python gpu_reservation.py reserve --num-gpus 2 \
        --session-id {gpu_session_id} --no-auto-release) && \
        CUDA_VISIBLE_DEVICES=$CVD python benchmark.py

Subcommands
-----------
reserve         allocate N resources from the session pool
release-session release a session's resources (optionally its children)
status          dump raw state as JSON; --table for a human-readable view
force-clear     crash recovery: break stale holds (see force_clear())

Public API
----------
read_state()                              -> dict
reserve(num_gpus, session_id, ...)        -> list[int]
release_by_session(session_id, include_children=False) -> list[int]
write_reservation(gpu_ids, session_id, ...) -> None   (internal helper)
force_clear(...)                          -> None
check_and_reclaim_expired()               -> list[int]
print_table(state)                        -> None    (status --table renderer)

No GPU discovery or file creation occurs on import.  All state is initialised
lazily on the first read_state() call.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (module-level constants patchable in tests)
# ---------------------------------------------------------------------------

# STATE_DIR is assigned after _compute_state_dir() is defined. It remains a
# module-level constant so tests and emergency tooling can monkeypatch it.


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class LockTimeoutError(Exception):
    """Raised when the exclusive file lock cannot be acquired after all retries."""


class ReservationError(Exception):
    """Raised when an accelerator reservation cannot be created."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lock_path() -> Path:
    return STATE_DIR / "state.lock"


def _state_path() -> Path:
    return STATE_DIR / "state.json"


def _discover_gpu_count() -> int:
    """Return the number of NVIDIA GPUs visible to nvidia-smi.

    Returns 0 on any failure (nvidia-smi not found, no GPUs, etc.).
    The reservation pool comes from _discover_session_gpus().
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0


def _discover_session_gpus() -> list[int]:
    """Return the host GPU pool: every physical GPU ID on this host.

    The pool is host-wide on purpose. ``nvidia-smi`` ignores
    CUDA_VISIBLE_DEVICES, so every caller sees the same IDs, and the one
    lease table keeps two campaigns off one GPU. When nvidia-smi is missing
    or fails, a torch device count runs with CUDA_VISIBLE_DEVICES cleared,
    so it also counts every GPU. The caller's own visibility is applied at
    reserve time (_caller_visible_gpus()).

    Returns:
        Sorted list of integer GPU IDs, e.g. [0, 1, 2, 3].
        Empty list when neither nvidia-smi nor torch reports a GPU.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            ids = sorted({int(line.strip()) for line in result.stdout.splitlines()
                          if line.strip().isdigit()})
            if ids:
                return ids
    except (OSError, subprocess.SubprocessError):
        pass
    return _torch_gpu_ids()


def _torch_gpu_ids() -> list[int]:
    """Fallback pool: torch's device count with CUDA_VISIBLE_DEVICES cleared."""
    env = {k: v for k, v in os.environ.items() if k != "CUDA_VISIBLE_DEVICES"}
    try:
        result = subprocess.run(
            [sys.executable, "-c",
             "import torch; print(torch.cuda.device_count())"],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        count = int(result.stdout.strip()) if result.returncode == 0 else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        count = 0
    return list(range(count))


def _caller_visible_gpus() -> set[int] | None:
    """Return the GPU IDs the caller's CUDA_VISIBLE_DEVICES allows.

    None means no filter: the variable is unset or empty. Otherwise the set
    holds its integer IDs. "-1", or a list with no integer ID, allows none.
    GPU-/MIG- UUID entries cannot be matched to pool indices and are skipped.
    """
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES", "").strip()
    if not cvd:
        return None
    visible: set[int] = set()
    for x in cvd.split(","):
        x = x.strip()
        if x.isdigit():
            visible.add(int(x))
        elif x.startswith("GPU-") or x.startswith("MIG-"):
            logger.warning(
                f"Non-integer GPU ID in CUDA_VISIBLE_DEVICES: {x}, skipping. "
                "gpu_reservation.py requires integer GPU IDs."
            )
    return visible


def _accelerator_kind() -> str:
    """Return the immutable session accelerator binding.

    The explicit server metadata wins. Presence of Neuron visibility is a
    compatibility fallback for session templates created before that metadata
    was injected.
    """
    kind = os.environ.get("AMMO_ACCELERATOR_KIND", "").strip().lower()
    if kind in {"cuda", "neuron"}:
        return kind
    profile = os.environ.get("AMMO_RUNTIME_PROFILE", "").strip().lower()
    if (
        profile == "vllm-neuron"
        or "NEURON_VISIBLE_DEVICES" in os.environ
        or "NEURON_RT_VISIBLE_CORES" in os.environ
    ):
        return "neuron"
    return "cuda"


def _discover_session_neuron_cores() -> list[int]:
    """Parse the exclusive host-global logical NeuronCore range.

    AMMO intentionally accepts one integer or one inclusive ``start-end``
    range. A discontiguous list is rejected because every Neuron child process
    must receive one contiguous, exclusively owned range.
    """
    visible_name = (
        "NEURON_VISIBLE_DEVICES"
        if "NEURON_VISIBLE_DEVICES" in os.environ
        else "NEURON_RT_VISIBLE_CORES"
    )
    visible = os.environ.get(visible_name, "").strip()
    if not visible or visible == "-1":
        return []
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", visible)
    if not match:
        logger.warning(
            "Invalid %s=%r: AMMO requires one contiguous start-end range",
            visible_name,
            visible,
        )
        return []
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if end < start:
        logger.warning(
            "Invalid %s=%r: range end precedes start",
            visible_name,
            visible,
        )
        return []
    return list(range(start, end + 1))


def _discover_pool() -> list[int]:
    """The resource table a fresh state file is created with."""
    if _accelerator_kind() == "neuron":
        return _discover_session_neuron_cores()
    return _discover_session_gpus()


def _discover_session_resources() -> list[int]:
    """Resources this caller may use: the pool seen through its visibility."""
    if _accelerator_kind() == "neuron":
        return _discover_session_neuron_cores()
    pool = _discover_session_gpus()
    visible = _caller_visible_gpus()
    return pool if visible is None else [gid for gid in pool if gid in visible]


def _resource_name(*, plural: bool = True) -> str:
    if _accelerator_kind() == "neuron":
        return "logical NeuronCores" if plural else "logical NeuronCore"
    return "GPUs" if plural else "GPU"


def _format_visibility(resource_ids: list[int]) -> str:
    """Format allocated host IDs for the bound runtime's visibility variable."""
    if _accelerator_kind() == "neuron":
        if not resource_ids:
            return ""
        if len(resource_ids) == 1:
            return str(resource_ids[0])
        expected = list(range(resource_ids[0], resource_ids[-1] + 1))
        if resource_ids != expected:
            raise ReservationError(
                "Neuron reservations must be a contiguous logical NeuronCore range"
            )
        return f"{resource_ids[0]}-{resource_ids[-1]}"
    return ",".join(str(resource_id) for resource_id in resource_ids)


def _format_released_resources(resource_ids: list[int]) -> str:
    """Format released IDs for diagnostics, where contiguity is not required."""
    return ",".join(str(resource_id) for resource_id in resource_ids)


def _compute_state_dir() -> Path:
    """Compute the state directory path at call time.

    Uses the AMMO_GPU_RES_DIR env var if set. Otherwise CUDA uses the
    host-wide /tmp/ammo_gpu_res, whatever CUDA_VISIBLE_DEVICES says, so all
    campaigns on one host share one lease table. Neuron keeps a hash of its
    visible range.
    """
    env_dir = os.environ.get("AMMO_GPU_RES_DIR")
    if env_dir:
        return Path(env_dir)
    if _accelerator_kind() == "neuron":
        visible = os.environ.get(
            "NEURON_VISIBLE_DEVICES",
            os.environ.get("NEURON_RT_VISIBLE_CORES", ""),
        )
        hash_suffix = hashlib.sha256(visible.encode()).hexdigest()[:12]
        return Path(f"/tmp/ammo_neuron_res_{hash_suffix}")
    return Path(DEFAULT_GPU_RES_DIR)


# Delays between successive flock attempts (seconds).  4 delays -> 5 attempts.
DEFAULT_GPU_RES_DIR = "/tmp/ammo_gpu_res"
STATE_DIR: Path = _compute_state_dir()
BACKOFF_DELAYS: list[float] = [0.1, 0.2, 0.4, 0.8]


def _init_state() -> dict:
    """Create and persist initial state from the accelerator pool.

    CUDA uses every physical GPU on the host (_discover_session_gpus()).
    Neuron uses the exclusive host-global range from ``NEURON_VISIBLE_DEVICES``
    (with ``NEURON_RT_VISIBLE_CORES`` accepted for standalone legacy commands).

    An empty pool is returned but not written, so one call on a host without
    a GPU tool cannot freeze the shared table empty. The first write never
    replaces a table another process wrote first (_create_state()).
    """
    accelerator_kind = _accelerator_kind()
    resource_ids = _discover_pool()
    state: dict = {
        "gpu_count": len(resource_ids) if accelerator_kind == "cuda" else 0,
        "runtime_profile": (
            "vllm-neuron" if accelerator_kind == "neuron"
            else os.environ.get("AMMO_RUNTIME_PROFILE") or "pumice-cuda"
        ),
        "accelerator_kind": accelerator_kind,
        "resource_unit": (
            "neuron_core" if accelerator_kind == "neuron" else "gpu"
        ),
        "resource_count": len(resource_ids),
        "lnc": (
            int(os.environ.get("NEURON_LOGICAL_NC_CONFIG", "2"))
            if accelerator_kind == "neuron"
            else None
        ),
        "audit": [],
    }
    table_name = "resources" if accelerator_kind == "neuron" else "gpus"
    state[table_name] = {str(resource_id): None for resource_id in resource_ids}
    if not resource_ids:
        return state
    return _create_state(state)


def _create_state(state: dict) -> dict:
    """Write state.json only if it does not exist yet; return what is on disk.

    ``status`` reads without the lock, so two first calls can race. A hard
    link refuses to replace an existing file, so a fresh empty table never
    overwrites a lease another process just wrote.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = _state_path()
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        try:
            os.link(tmp_path, target)
        except FileExistsError:
            return json.loads(target.read_text())
        return state
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _acquire_flock() -> IO:
    """Acquire an exclusive advisory lock on the lock file.

    Attempts up to len(BACKOFF_DELAYS)+1 times with exponential backoff.
    Returns the open file handle (caller must close / release it).
    Raises LockTimeoutError if all attempts fail.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = _lock_path()
    attempts = len(BACKOFF_DELAYS) + 1  # e.g. 5 with default 4-element list

    fh = None
    for attempt in range(attempts):
        try:
            fh = open(lock_file, "w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh  # success
        except BlockingIOError:
            if fh is not None:
                fh.close()
                fh = None
            if attempt < len(BACKOFF_DELAYS):
                time.sleep(BACKOFF_DELAYS[attempt])
            # else: last attempt failed — fall through to raise below

    raise LockTimeoutError(
        f"Could not acquire GPU reservation lock after {attempts} attempts"
    )


def _release_flock(fh: IO) -> None:
    """Release and close the lock file handle."""
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def _write_state(state: dict) -> None:
    """Atomically write state to state.json via a temp-file + rename."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = _state_path()
    fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _resource_table(state: dict) -> dict[str, dict | None]:
    """Return the provider-neutral resource table, including legacy files."""
    resources = state.get("resources")
    if isinstance(resources, dict):
        return resources
    gpus = state.get("gpus")
    if isinstance(gpus, dict):
        return gpus
    raise ReservationError("Reservation state has no resource table")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_state() -> dict:
    """Read and return the state dict, initialising state.json if absent."""
    state_file = _state_path()
    if not state_file.exists():
        return _init_state()
    return json.loads(state_file.read_text())


def _lease_expired(entry: dict, now: datetime) -> bool:
    """Durable serving claims require confirmed cleanup, never time alone.

    Unknown nonempty release policies are also held. An older or malformed
    policy must not silently expose a still-running accelerator worker.
    """
    if entry.get("release_policy"):
        return False
    return datetime.fromisoformat(entry["lease_expires"]) <= now


def write_reservation(
    gpu_ids: list[int],
    session_id: str,
    command_snippet: str = "",
    lease_hours: float = 0.25,
    *,
    require_cleanup: bool = False,
) -> None:
    """Standalone low-level reservation function that manages its own locking.

    For pool-based dynamic allocation, use reserve() instead. Does NOT
    auto-release existing session reservations or reclaim expired leases.

    Acquires the exclusive lock, checks that each GPU is free (or expired),
    writes reservation entries, and releases the lock.

    With require_cleanup=True, expiry and ordinary release cannot reclaim the
    claim. The deployment must confirm cleanup or an operator must investigate.
    Raises ReservationError if any resource is still held.
    """
    fh = _acquire_flock()
    try:
        state = read_state()
        resources = _resource_table(state)
        now = datetime.now(tz=timezone.utc)

        # Validate that all requested resource IDs exist in state.
        known = set(resources.keys())
        unknown = [gid for gid in gpu_ids if str(gid) not in known]
        if unknown:
            raise ReservationError(
                f"{_resource_name()} IDs not found in state: {unknown}"
            )

        # Check all GPUs are free before writing any
        held: list[str] = []
        for gid in gpu_ids:
            key = str(gid)
            entry = resources.get(key)
            if entry is not None:
                # Check if the lease has expired
                if not _lease_expired(entry, now):
                    held.append(
                        f"{_resource_name(plural=False)} {gid} held by "
                        f"session={entry['session_id']} "
                        f"until {entry['lease_expires']}"
                    )
        if held:
            raise ReservationError(
                f"Cannot reserve {_resource_name()} — already held:\n"
                + "\n".join(held)
            )

        # Write entries
        reserved_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        lease_expires = (now + timedelta(hours=lease_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for gid in gpu_ids:
            resources[str(gid)] = {
                "session_id": session_id,
                "reserved_at": reserved_at,
                "lease_expires": lease_expires,
                "command_snippet": command_snippet[:80],
            }
            if require_cleanup:
                resources[str(gid)]["release_policy"] = "confirmed_cleanup"

        _write_state(state)
    finally:
        _release_flock(fh)


def reserve(
    num_gpus: int,
    session_id: str,
    command_snippet: str = "",
    lease_hours: float = 0.25,
    auto_release: bool = True,
) -> list[int]:
    """Dynamically reserve *num_gpus* GPUs from the pool.

    Uses contiguous-first allocation:
      - For num_gpus=1, takes the first free GPU.
      - For num_gpus>1, requires a contiguous block of free GPUs.

    When *auto_release* is True (default), releases any existing reservations
    for *session_id* before allocating, so retries don't exhaust the pool.
    Set *auto_release* to False when multiple agents share a session_id and
    should not evict each other.

    Returns the list of allocated GPU IDs on success.
    Raises ReservationError if the request cannot be satisfied.
    """
    if num_gpus <= 0:
        raise ValueError(f"num_gpus must be positive, got {num_gpus}")

    fh = _acquire_flock()
    try:
        state = read_state()
        resources = _resource_table(state)
        now = datetime.now(tz=timezone.utc)
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Reclaim expired leases (inline) ---
        for key, entry in list(resources.items()):
            if entry is None:
                continue
            if _lease_expired(entry, now):
                state["audit"].append(
                    {
                        "action": "reclaim_expired",
                        "gpu_id": key,
                        "reclaimed_at": now_str,
                        "was_session": entry.get("session_id"),
                        "lease_expired_at": entry["lease_expires"],
                    }
                )
                resources[key] = None

        # --- Auto-release existing reservations for this session_id ---
        if auto_release:
            for key, entry in resources.items():
                if (entry is not None and entry.get("session_id") == session_id
                        and not entry.get("release_policy")):
                    resources[key] = None

        # --- Find free GPUs ---
        # Iterate the actual pool keys (physical IDs), not range(gpu_count).
        # Physical IDs may be non-contiguous (e.g. [4,5,6,7]), and
        # range(gpu_count) would wrongly return 0-based indices that map to
        # different physical GPUs. A caller restricted by CUDA_VISIBLE_DEVICES
        # gets only GPUs it can see.
        visible = _caller_visible_gpus() if _accelerator_kind() == "cuda" else None
        all_pool_ids = sorted(
            int(k) for k in resources.keys() if visible is None or int(k) in visible
        )
        free_set = set()
        for gpu_id in all_pool_ids:
            if resources.get(str(gpu_id)) is None:
                free_set.add(gpu_id)

        if len(free_set) < num_gpus:
            raise ReservationError(
                f"Not enough free {_resource_name()}: requested {num_gpus}, "
                f"available {len(free_set)}"
            )

        # --- Contiguous-first allocation ---
        allocated: list[int] | None = None

        if num_gpus == 1:
            # Just take the first free GPU
            for i in all_pool_ids:
                if i in free_set:
                    allocated = [i]
                    break
        else:
            # Scan for a contiguous block of num_gpus consecutive free GPUs
            run_start = None
            run_len = 0
            prev_i = None
            for i in all_pool_ids:
                if i in free_set and (prev_i is None or i == prev_i + 1):
                    if run_start is None:
                        run_start = i
                        run_len = 1
                    else:
                        run_len += 1
                    if run_len >= num_gpus:
                        allocated = list(range(run_start, run_start + num_gpus))
                        break
                    prev_i = i
                elif i in free_set:
                    run_start = i
                    run_len = 1
                    prev_i = i
                    if run_len >= num_gpus:
                        allocated = [i]
                        break
                else:
                    run_start = None
                    run_len = 0
                    prev_i = None

        if allocated is None:
            raise ReservationError(
                f"No contiguous block of {num_gpus} free {_resource_name()} "
                f"available. Free {_resource_name()}: {sorted(free_set)}"
            )

        # --- Write reservation entries ---
        reserved_at = now_str
        lease_expires = (now + timedelta(hours=lease_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        for gid in allocated:
            resources[str(gid)] = {
                "session_id": session_id,
                "reserved_at": reserved_at,
                "lease_expires": lease_expires,
                "command_snippet": command_snippet[:80],
            }

        _write_state(state)
        return allocated
    finally:
        _release_flock(fh)


def _default_session_id() -> str:
    """Return the portable default reservation owner.

    AMMO_SESSION_ID is the campaign id; inside a campaign the CLI fills it
    from the .ammo/active marker (campaign_paths.apply_env()).
    CLAUDE_SESSION_ID is the Claude-runtime fallback. 'cli' is the last resort
    for manual use.
    """
    return (
        os.environ.get("AMMO_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "cli"
    )


def release_by_session(session_id: str, include_children: bool = False, *,
                       cleanup_confirmed: bool = False) -> list[int]:
    """Clear all GPU entries owned by *session_id*.

    If include_children is true, also clear entries whose session_id starts
    with ``<session_id>:``. Subagent reservations use that prefix.

    Durable claims require cleanup_confirmed=True for the exact owner; a
    parent-wide cleanup cannot clear them. The caller must stop all workers.
    Returns the list of released GPU IDs (as integers).
    """
    fh = _acquire_flock()
    try:
        state = read_state()
        resources = _resource_table(state)
        released: list[int] = []
        for key, entry in resources.items():
            owner = entry.get("session_id") if entry is not None else None
            owned = owner == session_id or (
                include_children
                and isinstance(owner, str)
                and owner.startswith(f"{session_id}:")
            )
            if owned and (not entry.get("release_policy") or (
                    cleanup_confirmed and not include_children
                    and entry.get("release_policy") == "confirmed_cleanup")):
                resources[key] = None
                released.append(int(key))
        if released:
            _write_state(state)
        return released
    finally:
        _release_flock(fh)


def force_clear(
    gpu_ids: list[int] | None = None,
    session_id: str | None = None,
    force_no_session: bool = False,
) -> None:
    """Forcibly clear reservation entries.

    Must provide either session_id or force_no_session=True.

    If session_id is given, clears only entries owned by that session.
    If gpu_ids is given together with session_id, further restricts to those GPUs.
    If force_no_session=True, clears all entries (or only gpu_ids if given).
    An audit log entry is written for each cleared GPU.

    Raises ValueError if neither session_id nor force_no_session=True is supplied.
    """
    if session_id is None and not force_no_session:
        raise ValueError(
            "force_clear requires either session_id or force_no_session=True"
        )

    fh = _acquire_flock()
    try:
        state = read_state()
        resources = _resource_table(state)
        now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        gpu_keys: list[str]
        if gpu_ids is not None:
            gpu_keys = [str(g) for g in gpu_ids]
        else:
            gpu_keys = list(resources.keys())

        for key in gpu_keys:
            entry = resources.get(key)
            if entry is None:
                continue
            # Filter by session_id if provided
            if session_id is not None and entry.get("session_id") != session_id:
                continue
            # Log to audit before clearing
            state["audit"].append(
                {
                    "action": "force_clear",
                    "gpu_id": key,
                    "cleared_at": now_str,
                    "was_session": entry.get("session_id"),
                }
            )
            resources[key] = None

        _write_state(state)
    finally:
        _release_flock(fh)


def check_and_reclaim_expired() -> list[int]:
    """Check for expired leases, clear them, write state, and return reclaimed IDs.

    Acquires the exclusive lock so it is safe to call standalone from hooks.
    Reads fresh state under the lock to avoid stale-state races.
    Returns the list of reclaimed GPU IDs (as integers).
    """
    fh = _acquire_flock()
    try:
        state = read_state()
        resources = _resource_table(state)
        now = datetime.now(tz=timezone.utc)
        reclaimed: list[int] = []
        now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        for key, entry in resources.items():
            if entry is None:
                continue
            if _lease_expired(entry, now):
                state["audit"].append(
                    {
                        "action": "reclaim_expired",
                        "gpu_id": key,
                        "reclaimed_at": now_str,
                        "was_session": entry.get("session_id"),
                        "lease_expired_at": entry["lease_expires"],
                    }
                )
                resources[key] = None
                reclaimed.append(int(key))

        if reclaimed:
            _write_state(state)

        return reclaimed
    finally:
        _release_flock(fh)


# ---------------------------------------------------------------------------
# Display helpers (`status --table`)
# ---------------------------------------------------------------------------

def _iso_to_display(iso: str | None) -> str:
    """Convert ISO timestamp to a more readable local-ish form."""
    if iso is None:
        return "-"
    # Input is always UTC Z-suffix; strip and reformat for display
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # Display in UTC without timezone suffix for brevity
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso


def _truncate(s: str | None, width: int) -> str:
    if s is None:
        return "-"
    return s if len(s) <= width else s[: width - 1] + "…"


def print_table(state: dict) -> None:
    """Print one fixed-width row per pool resource."""
    resources = _resource_table(state)
    if not resources:
        print(f"No {_resource_name()} tracked in state.")
        return

    # Column widths
    COL_GPU = 4
    COL_STATUS = 9
    COL_CMD = 42
    COL_SINCE = 19
    COL_EXPIRES = 19
    COL_SESSION = 16

    id_heading = "Core" if state.get("accelerator_kind") == "neuron" else "GPU"
    header = (
        f"{id_heading:<{COL_GPU}}  "
        f"{'Status':<{COL_STATUS}}  "
        f"{'Command':<{COL_CMD}}  "
        f"{'Since':<{COL_SINCE}}  "
        f"{'Lease Expires':<{COL_EXPIRES}}  "
        f"{'Session':<{COL_SESSION}}"
    )
    separator = "-" * len(header)
    print(header)
    print(separator)

    for gpu_id in sorted(resources.keys(), key=lambda x: int(x)):
        entry = resources[gpu_id]
        if entry is None:
            row = (
                f"{gpu_id:<{COL_GPU}}  "
                f"{'FREE':<{COL_STATUS}}  "
                f"{'-':<{COL_CMD}}  "
                f"{'-':<{COL_SINCE}}  "
                f"{'-':<{COL_EXPIRES}}  "
                f"{'-':<{COL_SESSION}}"
            )
        else:
            cmd = _truncate(entry.get("command_snippet"), COL_CMD)
            since = _iso_to_display(entry.get("reserved_at"))
            expires = _iso_to_display(entry.get("lease_expires"))
            session = _truncate(entry.get("session_id"), COL_SESSION)
            row = (
                f"{gpu_id:<{COL_GPU}}  "
                f"{'HELD':<{COL_STATUS}}  "
                f"{cmd:<{COL_CMD}}  "
                f"{since:<{COL_SINCE}}  "
                f"{expires:<{COL_EXPIRES}}  "
                f"{session:<{COL_SESSION}}"
            )
        print(row)


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def _apply_campaign_env() -> None:
    """Fill the campaign names (AMMO_SESSION_ID, AMMO_GPU_RES_DIR, ...) that
    are unset, then recompute STATE_DIR from them. Explicit env wins."""
    global STATE_DIR
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import campaign_paths
    except ImportError:
        return
    campaign_paths.apply_env()
    STATE_DIR = _compute_state_dir()


if __name__ == "__main__":
    _apply_campaign_env()
    parser = argparse.ArgumentParser(
        description="AMMO accelerator reservation pool manager"
    )
    sub = parser.add_subparsers(dest="command")

    # --- reserve ---
    p_reserve = sub.add_parser(
        "reserve", help="Reserve N accelerator resources from the pool"
    )
    reserve_count = p_reserve.add_mutually_exclusive_group(required=True)
    reserve_count.add_argument(
        "--num-gpus",
        type=int,
        help="Number of GPUs to reserve (backward-compatible on Neuron)",
    )
    reserve_count.add_argument(
        "--num-resources",
        type=int,
        help="Number of accelerator resources to reserve",
    )
    p_reserve.add_argument(
        "--session-id",
        type=str,
        default=_default_session_id(),
        help="Session identifier (default: $AMMO_SESSION_ID, $CLAUDE_SESSION_ID, or 'cli')",
    )
    p_reserve.add_argument(
        "--lease-hours",
        type=float,
        default=0.25,
        help="Lease duration in hours (default: 0.25 = 15 min; pass 2.0 for long sweeps/nsys)",
    )
    p_reserve.add_argument(
        "--command-snippet", type=str, default="", help="Command description"
    )
    p_reserve.add_argument(
        "--no-auto-release",
        action="store_true",
        default=False,
        help="Skip auto-release of existing reservations for this session_id",
    )

    # --- release-session ---
    p_release = sub.add_parser(
        "release-session", help="Release all resources held by a session"
    )
    p_release.add_argument(
        "--session-id", type=str, required=True, help="Session identifier"
    )
    p_release.add_argument(
        "--include-children",
        action="store_true",
        help="Also release subagent sessions prefixed with '<session-id>:'",
    )

    # --- status ---
    p_status = sub.add_parser("status", help="Print current reservation state as JSON")
    p_status.add_argument(
        "--table",
        action="store_true",
        help="Print a human-readable table instead of JSON (one row per pool GPU)",
    )

    # --- force-clear ---
    p_force = sub.add_parser(
        "force-clear", help="Forcibly clear reservations after a crash"
    )
    force_target = p_force.add_mutually_exclusive_group(required=True)
    force_target.add_argument(
        "--gpu-ids",
        metavar="IDS",
        help="Comma-separated GPU IDs to clear (e.g. 0,1).",
    )
    force_target.add_argument(
        "--all",
        action="store_true",
        help="Clear reservations on all accelerator resources.",
    )
    force_session = p_force.add_mutually_exclusive_group(required=True)
    force_session.add_argument(
        "--session-id",
        metavar="ID",
        help="Only clear reservations owned by this session ID.",
    )
    force_session.add_argument(
        "--force-no-session",
        action="store_true",
        help="Emergency override: clear without session check.",
    )

    args = parser.parse_args()

    if args.command == "reserve":
        try:
            requested_count = (
                args.num_resources
                if args.num_resources is not None
                else args.num_gpus
            )
            gpu_ids = reserve(
                num_gpus=requested_count,
                session_id=args.session_id,
                command_snippet=args.command_snippet,
                lease_hours=args.lease_hours,
                auto_release=not args.no_auto_release,
            )
            print(_format_visibility(gpu_ids))
            sys.exit(0)
        except (ReservationError, LockTimeoutError) as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    elif args.command == "release-session":
        try:
            released = release_by_session(
                args.session_id,
                include_children=args.include_children,
            )
            print(
                f"Released {_resource_name()}: {_format_released_resources(released)}",
                file=sys.stderr,
            )
            sys.exit(0)
        except (ReservationError, LockTimeoutError) as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    elif args.command == "status":
        state = read_state()
        if args.table:
            print_table(state)
        else:
            print(json.dumps(state, indent=2))

    elif args.command == "force-clear":
        # Resolve GPU IDs (None means all)
        if args.all:
            gpu_ids = None
        else:
            try:
                gpu_ids = [int(x.strip()) for x in args.gpu_ids.split(",")]
            except ValueError:
                print(
                    f"ERROR: --gpu-ids must be comma-separated integers, got: {args.gpu_ids!r}",
                    file=sys.stderr,
                )
                sys.exit(1)

        if args.force_no_session:
            print(
                "WARNING: --force-no-session is an emergency override. "
                "All matched reservations will be cleared regardless of session ownership.",
                file=sys.stderr,
            )

        try:
            force_clear(
                gpu_ids=gpu_ids,
                session_id=args.session_id if not args.force_no_session else None,
                force_no_session=args.force_no_session,
            )
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        print(f"{_resource_name(plural=False)} reservations cleared.")

    else:
        parser.print_help()
        sys.exit(1)
