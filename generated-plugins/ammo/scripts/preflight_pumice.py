#!/usr/bin/env python3
"""AMMO preflight: check the pre-built Pumice engine before a campaign measures.

``run_pumice_grpc_bench.py`` reaches into the Pumice gRPC contract (proto
message fields), the ``pumice-server`` CLI (named flags), the CloudWatch EMF
metrics daemon (metric names on stdout), the local model layout
(``<path>/config.json``), and the versions that ``scripts/pumice_pins.py``
pins. If one of these moves, the sweep degrades without a loud error: the
runner falls back to "no data" and a track cannot qualify. This script finds
those gaps first and names each one.

Run it once at campaign start and again after any engine change, with the
engine venv python (``scripts/ammo preflight_pumice.py`` picks it). It launches
no server. It reads ``nvidia-smi`` to see the GPUs.

Checks, one line each (a FAIL names the gap):
  runtime profile    runtime_ops.require_supported_profile(): pumice-cuda only
  engine commit      a Pumice checkout (``rust_grpc_server.py``) whose HEAD is
                     pumice_pins.PUMICE_COMMIT or descends from it
  engine venv        this interpreter is the recorded engine venv (AMMO_PYTHON)
  pumice import      ``pumice`` imports
  pumice_rust import the cargo-built engine extension imports
  pumice pins        torch, and vLLM when the model runner kind is ``vllm``
                     (an unknown kind checks vLLM too)
  proto stubs        every field and method the runner uses
  server flags       every launch flag the runner passes
  cmb-api patch      the cargo ``[patch.crates-io]`` row a track build needs
  GPU                ``nvidia-smi`` lists a GPU and CUDA_VISIBLE_DEVICES does
                     not hide them all
  model path         a directory with ``config.json`` and ``tokenizer.json``

Inputs. ``campaign_paths.apply_env()`` runs first. Inside a campaign it fills
AMMO_ENGINE_ROOT, AMMO_PYTHON and the other names in
references/campaign-env.md from ``target.json``, and the model path defaults
to ``target.json`` ``target.model_id``. The model runner kind comes from
``--model-runner`` in ``bench.extra_args``, else the confdb row for the model's
architecture on this GPU's compute capability, else
``PUMICE_USE_VLLM_RUNNER``, else ``pumice`` (Pumice's own precedence).

Exit codes
----------
  0  every check passed
  1  one or more checks failed (one line per failure)

Usage
-----
  <engine venv python> scripts/preflight_pumice.py [--verbose]
      [--engine-root DIR] [--model-path DIR] [--skip-server-flags]

Stack fingerprint (scaffold)
----------------------------
  <engine venv python> scripts/preflight_pumice.py --engine-root DIR \\
      --write-fingerprint PATH

  Writes one JSON object to PATH (``-`` means stdout) and exits 0. It runs no
  check, so a scaffold with a broken venv still finishes; the preflight node
  reports the gaps. ``new_target.py`` records the object as ``target.json``
  ``runtime.stack_fingerprint``, and campaign_paths turns that key into
  AMMO_STACK_FINGERPRINT. Keys (FINGERPRINT_KEYS); a value it cannot read is
  null:
    pumice_commit         git HEAD of the engine root
    pumice_rust_build_id  ``sha256:<hex>`` of the pumice_rust file Python imports
    torch, triton, flashinfer, vllm   installed versions in this interpreter
    nvidia_driver, gpu_name           ``nvidia-smi``, first visible GPU
    image_digest          always null: a host-local campaign has no image
  Run it with the engine venv python, because the package versions come from
  the running interpreter.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import campaign_paths  # noqa: E402
import pumice_pins  # noqa: E402
import runtime_ops  # noqa: E402


# ---------------------------------------------------------------------------
# Coupling tables
# ---------------------------------------------------------------------------

#: gRPC proto fields the runner reads / writes. A field rename here makes the
#: runner silently skip that path (``getattr``-and-continue on the message).
PROTO_FIELDS = {
    "GenerateRequest": ["request_id", "tokenized", "sampling_params", "stream"],
    "TokenizedInput": ["input_ids"],
    "SamplingParams": ["temperature", "max_tokens", "ignore_eos"],
    "GenerateResponse": ["chunk", "complete"],
    "GenerateStreamChunk": ["token_ids", "prompt_tokens_cached"],
    "GenerateComplete": [
        "finish_reason",
        "completion_tokens",
        "prompt_tokens_cached",
        "output_ids",
    ],
    "HealthCheckRequest": [],
    "StartProfileRequest": [],
    "StopProfileRequest": [],
}

#: gRPC methods on the ``PumiceEngine`` service. Missing method -> hard stop.
PROTO_METHODS = ["Generate", "HealthCheck", "StartProfile", "StopProfile"]

#: ``pumice-server`` flags the runner passes on every launch (see
#: ``build_server_cmd``). The runner NEVER falls back on a missing flag: the
#: launch fails healthchecking and the sweep aborts. So we check them here.
SERVER_FLAGS = [
    "--model",
    "--port",
    "--max-model-len",
    "--tensor-parallel-size",
    "--max-num-seqs",
    "--metrics-port",
    "--enable-prefix-caching",
    "--no-enable-prefix-caching",
]

#: EMF metric names the runner scrapes off the server log. A rename here
#: silently drops the value (parse_emf_* returns None), which then null-pads
#: the ``spec_decode`` and prefix-cache-parity blocks in the results row.
EMF_METRIC_NAMES = [
    "spec_acceptance_rate",
    "prefix_cache_hit_rate",
]

#: Prometheus counter names the ``--metrics-port`` scrape reads for WS4.4
#: verification. Missing counter fails-closed the regime check.
PROM_COUNTER_NAMES = [
    "prefix_cache_hits_total",
    "prefix_cache_queries_total",
]

#: Keys of the stack fingerprint object (conventions §4), in output order.
FINGERPRINT_KEYS = (
    "pumice_commit",
    "pumice_rust_build_id",
    "torch",
    "triton",
    "flashinfer",
    "vllm",
    "nvidia_driver",
    "gpu_name",
    "image_digest",
)

#: The file that marks a Pumice checkout: the launcher the runner uses.
_ENGINE_MARKER = "rust_grpc_server.py"


# ---------------------------------------------------------------------------
# Small probes
# ---------------------------------------------------------------------------


Result = Tuple[str, bool, str]


def _run_text(argv: Sequence[str], cwd: Optional[Path] = None) -> Optional[str]:
    """Run a bounded read-only probe; return stdout, or None when it fails."""
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            list(argv),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git(root: Path, *args: str) -> Tuple[int, str]:
    """Run git in *root*; return (exit code, stripped stdout). 127 when git is absent."""
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 127, ""
    return result.returncode, (result.stdout or "").strip()


def _package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken dist-info must not crash the probe
        return None


def _nvidia_smi_rows() -> Optional[List[Dict[str, str]]]:
    """Rows of ``nvidia-smi --query-gpu``; None when nvidia-smi does not run."""
    text = _run_text(
        [
            "nvidia-smi",
            "--query-gpu=index,name,compute_cap,driver_version",
            "--format=csv,noheader",
        ]
    )
    if text is None:
        return None
    rows: List[Dict[str, str]] = []
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4 or not parts[0]:
            continue
        rows.append(
            {
                "index": parts[0],
                "name": parts[1],
                "compute_cap": parts[2],
                "driver_version": parts[3],
            }
        )
    return rows


def _visible_row(rows: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
    """The first GPU this process can use: CUDA_VISIBLE_DEVICES' first index, else row 0."""
    if not rows:
        return {}
    first = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
    for row in rows:
        if first and row.get("index") == first:
            return row
    return rows[0]


def _compute_capability(rows: Optional[List[Dict[str, str]]]) -> Optional[Tuple[int, int]]:
    raw = _visible_row(rows).get("compute_cap", "")
    try:
        major, minor = raw.split(".", 1)
        return int(major), int(minor)
    except ValueError:
        return None


def _flag_value(args: Sequence[str], flag: str) -> Optional[str]:
    """Value of ``--flag value`` or ``--flag=value`` in *args*, else None."""
    for index, arg in enumerate(args):
        if arg == flag:
            return args[index + 1] if index + 1 < len(args) else None
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def _load_target(campaign: Optional[Path]) -> dict:
    if campaign is None:
        return {}
    try:
        data = json.loads((Path(campaign) / "target.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(data: Mapping, key: str) -> Mapping:
    value = data.get(key) if isinstance(data, Mapping) else None
    return value if isinstance(value, Mapping) else {}


# ---------------------------------------------------------------------------
# Engine root
# ---------------------------------------------------------------------------


def _find_repo_root(explicit: Optional[Path] = None) -> Optional[Path]:
    """Return the Pumice checkout this run checks, or None.

    Order: *explicit* (``--engine-root``), then ``AMMO_ENGINE_ROOT`` and its
    alias ``AMMO_RUNTIME_SOURCE_DIR`` (campaign_paths fills both from
    ``target.json`` ``engine.root``), then the current directory and its
    parents. The marker is ``rust_grpc_server.py``, the launcher the runner
    uses. A track that checks its own worktree passes ``--engine-root .``.
    """
    starts: List[Path] = []
    if explicit is not None:
        starts.append(Path(explicit))
    for name in ("AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR"):
        stamped = (os.environ.get(name) or "").strip()
        if stamped:
            starts.append(Path(stamped))
    starts.append(Path.cwd())
    for start in starts:
        start = start.expanduser()
        for candidate in (start, *start.parents):
            if (candidate / _ENGINE_MARKER).is_file():
                return candidate
    return None


def _engine_search_text() -> str:
    return (
        f"no Pumice checkout ({_ENGINE_MARKER}) found at --engine-root, "
        f"AMMO_ENGINE_ROOT, or up from {Path.cwd()}"
    )


# ---------------------------------------------------------------------------
# Checks (each returns (label, ok, detail))
# ---------------------------------------------------------------------------


def _check_runtime_profile() -> Result:
    try:
        profile = runtime_ops.require_supported_profile()
    except ValueError as exc:
        return ("runtime profile", False, str(exc))
    return ("runtime profile", True, profile)


def _check_engine_commit() -> Result:
    """HEAD of the engine root is the pinned commit or descends from it."""
    label = "engine commit"
    root = _find_repo_root()
    if root is None:
        return (label, False, _engine_search_text())
    code, head = _git(root, "rev-parse", "HEAD")
    if code != 0 or not head:
        return (label, False, f"{root} is not a git checkout (git rev-parse HEAD failed)")
    pin = pumice_pins.PUMICE_COMMIT
    if head == pin:
        return (label, True, f"{root} HEAD is the pinned commit {pin[:12]}")
    code, _ = _git(root, "merge-base", "--is-ancestor", pin, "HEAD")
    if code == 0:
        return (label, True, f"{root} HEAD {head[:12]} descends from the pinned commit {pin[:12]}")
    if code == 1:
        return (
            label,
            False,
            f"{root} HEAD {head[:12]} is not the pinned commit {pin} and does not "
            "descend from it; check out the pinned commit",
        )
    return (
        label,
        False,
        f"the pinned commit {pin} is not in {root}; fetch it, then check it out",
    )


def _check_engine_venv() -> Result:
    """This interpreter is the recorded engine venv, or the checked tree's own .venv."""
    label = "engine venv"
    recorded = (os.environ.get("AMMO_PYTHON") or "").strip()
    here = Path(sys.prefix).resolve()
    if not recorded:
        return (label, True, f"no recorded venv; this interpreter runs from {here}")
    python = Path(recorded).expanduser()
    if not python.exists():
        return (
            label,
            False,
            f"the recorded engine python {python} does not exist "
            "(target.json engine.venv, else AMMO_PYTHON)",
        )
    allowed = [python.parent.parent.resolve()]
    root = _find_repo_root()
    if root is not None:
        allowed.append((root / ".venv").resolve())
    if here in allowed:
        return (label, True, f"this interpreter runs from {here}")
    return (
        label,
        False,
        f"this interpreter runs from {here}, not the recorded engine venv "
        f"{allowed[0]}; run preflight with {python}",
    )


def _check_pumice_imports() -> Result:
    try:
        importlib.import_module("pumice")
    except ImportError as exc:
        return ("pumice import", False, f"cannot import pumice: {exc}")
    return ("pumice import", True, "pumice imports")


def _check_pumice_rust_import() -> Result:
    label = "pumice_rust import"
    try:
        module = importlib.import_module("pumice_rust")
    except ImportError as exc:
        return (
            label,
            False,
            f"cannot import pumice_rust, the cargo-built engine extension: {exc}; "
            "build and stage it into the engine venv",
        )
    return (label, True, f"pumice_rust imports from {getattr(module, '__file__', '?')}")


def _resolve_model_runner(
    model_dir: Optional[Path],
    extra_args: Sequence[str],
    env: Mapping[str, str],
    gpu_rows: Optional[List[Dict[str, str]]],
) -> Tuple[Optional[str], str]:
    """Return (runner kind or None when unknown, how it was decided).

    Pumice's precedence, highest first: an explicit ``--model-runner``, the
    confdb ``model_runner`` key, ``PUMICE_USE_VLLM_RUNNER``, then ``pumice``.
    """
    explicit = _flag_value(extra_args, "--model-runner")
    if explicit:
        return explicit.strip().lower(), "--model-runner in bench.extra_args"
    if model_dir is None:
        return None, "no model path, so the confdb row cannot be read"
    try:
        config = json.loads((Path(model_dir) / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"cannot read {model_dir}/config.json: {exc}"
    architectures = config.get("architectures") if isinstance(config, dict) else None
    if not isinstance(architectures, list) or not architectures:
        return None, f"{model_dir}/config.json names no architectures"
    capability = _compute_capability(gpu_rows)
    if capability is None:
        return None, "no GPU compute capability from nvidia-smi, so the confdb row cannot be read"
    try:
        from pumice.models.confdb import load_model_config

        try:
            settings = load_model_config(
                architectures, capability, types.SimpleNamespace(**config)
            )
        except TypeError:
            settings = load_model_config(architectures, capability)
    except Exception as exc:  # noqa: BLE001 - any confdb failure means "unknown"
        return None, f"confdb lookup failed: {exc}"
    family = f"sm{capability[0]}{capability[1]}"
    runner = (settings or {}).get("model_runner")
    if runner:
        return str(runner).strip().lower(), f"confdb row for {architectures[0]} on {family}"
    flag = (env.get("PUMICE_USE_VLLM_RUNNER") or "").strip().lower()
    if flag in ("1", "true"):
        return "vllm", "PUMICE_USE_VLLM_RUNNER"
    return "pumice", f"default; no confdb model_runner row for {architectures[0]} on {family}"


def _check_pins(model_runner: Optional[str] = None, how: str = "") -> Result:
    """Compare the installed stack with ``scripts/pumice_pins.py``.

    vLLM is checked only for the ``vllm`` runner kind. An unknown kind checks
    vLLM too: the CUDA engine venv carries vLLM through Pumice's ``cuda`` extra.
    Neuron installs no vLLM; that branch stays as it was.
    """
    kind = (os.environ.get("AMMO_ACCELERATOR_KIND") or "").strip().lower()
    if kind == "neuron":
        return ("pumice pins", True, "skipped on Neuron (no vllm install)")
    if model_runner:
        checked = model_runner
        note = f"runner kind {model_runner} ({how})" if how else f"runner kind {model_runner}"
    else:
        checked = pumice_pins.VLLM_RUNNER
        note = f"runner kind unknown ({how}); checked vLLM as well" if how else (
            "runner kind unknown; checked vLLM as well"
        )
    ok, reason = pumice_pins.check_pins(model_runner=checked)
    return ("pumice pins", bool(ok), f"{reason or 'pins match'}; {note}")


def _check_proto() -> Result:
    """Every proto field the runner reads must exist in the owned stubs."""
    try:
        pb2 = importlib.import_module("pumice_pb.pumice_engine_pb2")
    except Exception as exc:
        return ("proto stubs", False, f"cannot import pumice_pb: {exc}")
    missing: List[str] = []
    for message_name, field_names in PROTO_FIELDS.items():
        cls = getattr(pb2, message_name, None)
        if cls is None or not hasattr(cls, "DESCRIPTOR"):
            missing.append(message_name)
            continue
        fields = cls.DESCRIPTOR.fields_by_name
        for field in field_names:
            if field not in fields:
                missing.append(f"{message_name}.{field}")
    service = pb2.DESCRIPTOR.services_by_name.get("PumiceEngine")
    if service is None:
        missing.append("PumiceEngine service")
    else:
        have_methods = {m.name for m in service.methods}
        for method in PROTO_METHODS:
            if method not in have_methods:
                missing.append(f"PumiceEngine.{method}")
    if missing:
        return ("proto stubs", False, "missing: " + ", ".join(missing))
    return ("proto stubs", True, "every field + method present")


def _server_probe_argv() -> Tuple[List[str], str]:
    """Return (argv, label) for the ``--help`` probe.

    Probe the launcher the runner uses (``resolve_server_exe`` in
    ``run_pumice_grpc_bench.py``): ``AMMO_PUMICE_SERVER_EXE`` when set,
    else ``<engine root>/rust_grpc_server.py`` under this interpreter (the
    engine venv python), else the ``pumice-server`` binary. The Rust binary
    parses argv by hand and answers ``Unknown argument: --help``; the Python
    launcher has argparse and lists every flag.
    """
    exe = (os.environ.get("AMMO_PUMICE_SERVER_EXE") or "").strip()
    if exe:
        return ([exe, "--help"], exe)
    root = _find_repo_root()
    if root is not None:
        launcher = root / _ENGINE_MARKER
        return ([sys.executable, str(launcher), "--help"], str(launcher))
    return (["pumice-server", "--help"], "pumice-server")


def _pumice_parser_source() -> Optional[Path]:
    """Return ``pumice-rust/src/main.rs`` of the campaign engine tree.

    Looks in ``AMMO_ENGINE_ROOT`` and its alias ``AMMO_RUNTIME_SOURCE_DIR``,
    then the current directory and its parents. Unlike ``_find_repo_root``
    (which keys on ``rust_grpc_server.py``), this locates the parser source
    directly so the flag fallback works even when only the Rust tree is present.
    """
    roots: List[Path] = []
    for name in ("AMMO_ENGINE_ROOT", "AMMO_RUNTIME_SOURCE_DIR"):
        env_root = (os.environ.get(name) or "").strip()
        if env_root:
            roots.append(Path(env_root))
    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    for root in roots:
        candidate = root / "pumice-rust" / "src" / "main.rs"
        if candidate.is_file():
            return candidate
    return None


def _check_server_flags() -> Result:
    """Run the server launcher with ``--help`` and grep for every launch flag.

    ``pumice-server`` (pumice-rust/src/main.rs ``parse_args``) has no
    ``--help``: it prints ``Unknown argument: --help`` and exits (found live
    on trn2-1). So when the probe answers that (or cannot run), fall back to
    the flag literals in the parser source of the engine tree. The
    ``rust_grpc_server.py`` launcher does print a usable ``--help``.
    """
    argv, label = _server_probe_argv()
    help_text = ""
    probe_error = ""
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        help_text = (result.stdout or "") + "\n" + (result.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        probe_error = f"cannot run {label} --help: {exc}"

    if help_text and "Unknown argument" not in help_text:
        missing = [flag for flag in SERVER_FLAGS if flag not in help_text]
        if not missing:
            return ("server flags", True, f"every launch flag present in {label} --help")
        # --help ran but is missing flags; try the parser source before failing.
        source = _pumice_parser_source()
        if source is None:
            return (
                "server flags",
                False,
                f"missing from {label} --help: " + ", ".join(missing),
            )
        text = source.read_text(encoding="utf-8", errors="replace")
        src_missing = [flag for flag in SERVER_FLAGS if f'"{flag}"' not in text]
        if src_missing:
            return (
                "server flags",
                False,
                f"missing from {source} parse_args: " + ", ".join(src_missing),
            )
        return ("server flags", True, f"every launch flag parsed by {source}")

    # No usable --help (Unknown argument, empty, or probe failure): the parser
    # source is the source of truth.
    source = _pumice_parser_source()
    if source is None:
        detail = probe_error or f"{label} has no --help"
        return (
            "server flags",
            False,
            detail + "; pumice-rust/src/main.rs not found (pass --engine-root "
            "or run from the engine checkout)",
        )
    text = source.read_text(encoding="utf-8", errors="replace")
    missing = [flag for flag in SERVER_FLAGS if f'"{flag}"' not in text]
    if missing:
        return (
            "server flags",
            False,
            f"missing from {source} parse_args: " + ", ".join(missing),
        )
    return (
        "server flags",
        True,
        f"every launch flag parsed by {source} (pumice-server has no --help)",
    )


def _cargo_config_candidates() -> List[Path]:
    """Cargo config files a build in the engine tree reads, nearest first.

    Cargo reads ``.cargo/config.toml`` in the build directory and its parents,
    then ``$CARGO_HOME/config.toml``. ``CARGO_HOME`` defaults to ``~/.cargo``.
    """
    candidates: List[Path] = []
    root = _find_repo_root()
    if root is not None:
        candidates.append(root / ".cargo" / "config.toml")
    home = (os.environ.get("CARGO_HOME") or "").strip()
    cargo_home = Path(home).expanduser() if home else Path.home() / ".cargo"
    candidates.append(cargo_home / "config.toml")
    return candidates


def _check_cmb_api_patch() -> Result:
    """The ``[patch.crates-io]`` row for ``amzn-mantle-cmb-api`` is required.

    ``pumice-rust/Cargo.toml`` names the internal crate without a lock, so a
    track build with the ``cmb`` feature cannot resolve it unless a cargo
    config carries the patch row (WS2.2). No patch -> ``cargo build`` in a
    track fails with "the crate amzn-mantle-cmb-api is not found".
    """
    seen: List[str] = []
    for path in _cargo_config_candidates():
        if not path.is_file():
            seen.append(f"{path} (absent)")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "[patch.crates-io]" in text and "amzn-mantle-cmb-api" in text:
            return ("cmb-api patch", True, str(path))
        seen.append(f"{path} (no [patch.crates-io] amzn-mantle-cmb-api row)")
    return (
        "cmb-api patch",
        False,
        "no cargo config has the [patch.crates-io] row for amzn-mantle-cmb-api: "
        + "; ".join(seen)
        + ". CARGO_HOME defaults to ~/.cargo when it is not set",
    )


def _check_gpu() -> Result:
    """At least one GPU is listed by nvidia-smi and not hidden by CUDA_VISIBLE_DEVICES."""
    visible = os.environ.get("CUDA_VISIBLE_DEVICES")
    if visible is not None and visible.strip() in ("", "-1"):
        return ("GPU", False, f"CUDA_VISIBLE_DEVICES={visible!r} hides every GPU")
    rows = _nvidia_smi_rows()
    if rows is None:
        return ("GPU", False, "nvidia-smi did not run, so no GPU is visible")
    if not rows:
        return ("GPU", False, "nvidia-smi lists no GPU")
    names = ", ".join(f"{row['index']}: {row['name']}" for row in rows)
    scope = f"; CUDA_VISIBLE_DEVICES={visible}" if visible else ""
    return ("GPU", True, f"{len(rows)} GPU(s) ({names}){scope}")


def _check_model_path(model_path: Optional[str]) -> Result:
    """The Pumice Rust loader takes a directory that contains ``config.json``.

    ``async_llm.rs`` panics with ``Failed to load tokenizer from
    <path>/tokenizer.json`` when the path is an HF id. Refuses to guess: the
    caller passes the exact path the sweep will pass to ``--model``.
    """
    if not model_path:
        return ("model path", True, "skipped (no --model-path and no target.json model_id)")
    p = Path(model_path).expanduser()
    if not p.is_dir():
        return ("model path", False, f"{p} is not a directory")
    for name in ("config.json", "tokenizer.json"):
        if not (p / name).is_file():
            return ("model path", False, f"{p / name} does not exist")
    return ("model path", True, str(p))


def _check_emf_names() -> Result:
    """Static sanity: the runner literally reads these names, listed here."""
    # This check is intentionally a static string list. The metric names are
    # the runner's contract; if the server stops emitting them a live sweep
    # would nullify the affected results row. There is no import path to
    # introspect at preflight time.
    return (
        "EMF metric names",
        True,
        "runner reads: " + ", ".join(EMF_METRIC_NAMES),
    )


def _check_prom_names() -> Result:
    return (
        "Prometheus counters",
        True,
        "runner scrapes: " + ", ".join(PROM_COUNTER_NAMES),
    )


def _resolve_model_dir(model_id: Optional[str]) -> Tuple[Optional[str], Optional[Path]]:
    """Return (path to check, directory for confdb) for *model_id*.

    A cached Hugging Face id resolves the way the runner resolves it
    (``run_pumice_grpc_bench._resolve_local_model_path``).
    """
    if not model_id:
        return None, None
    if os.path.isabs(model_id) or os.path.isdir(model_id):
        path = str(Path(model_id).expanduser())
    else:
        try:
            from run_pumice_grpc_bench import _resolve_local_model_path

            path = _resolve_local_model_path(model_id)
        except SystemExit:
            path = model_id
        except Exception:  # noqa: BLE001 - the model path check names the gap
            path = model_id
    directory = Path(path)
    return path, directory if directory.is_dir() else None


# ---------------------------------------------------------------------------
# Stack fingerprint
# ---------------------------------------------------------------------------


def _module_file_digest(name: str) -> Optional[str]:
    """``sha256:<hex>`` of the file Python would import for *name*, without importing it."""
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return None
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or origin in ("built-in", "frozen"):
        return None
    digest = hashlib.sha256()
    try:
        with open(origin, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return "sha256:" + digest.hexdigest()


def stack_fingerprint(engine_root: Optional[Path]) -> Dict[str, Optional[str]]:
    """The stack fingerprint object of conventions §4 (see the module docstring)."""
    commit: Optional[str] = None
    if engine_root is not None and Path(engine_root).is_dir():
        code, head = _git(Path(engine_root), "rev-parse", "HEAD")
        commit = head if code == 0 and head else None
    gpu = _visible_row(_nvidia_smi_rows())
    values = {
        "pumice_commit": commit,
        "pumice_rust_build_id": _module_file_digest("pumice_rust"),
        "torch": _package_version("torch"),
        "triton": _package_version("triton"),
        "flashinfer": _package_version("flashinfer-python"),
        "vllm": _package_version("vllm"),
        "nvidia_driver": gpu.get("driver_version") or None,
        "gpu_name": gpu.get("name") or None,
        "image_digest": None,
    }
    return {key: values[key] for key in FINGERPRINT_KEYS}


def _write_fingerprint(destination: str, engine_root: Optional[Path]) -> int:
    text = json.dumps(stack_fingerprint(engine_root), indent=2) + "\n"
    if destination == "-":
        sys.stdout.write(text)
        return 0
    path = Path(destination).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"preflight: cannot write the fingerprint to {path}: {exc}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


#: Checks that take no input, by name, in run order before the pins check.
#: main() looks each name up when it runs.
CHECKS: Tuple[str, ...] = (
    "_check_runtime_profile",
    "_check_engine_commit",
    "_check_engine_venv",
    "_check_pumice_imports",
    "_check_pumice_rust_import",
)

#: Checks that take no input, by name, in run order after the pins check.
LATE_CHECKS: Tuple[str, ...] = (
    "_check_proto",
    "_check_server_flags",
    "_check_cmb_api_patch",
    "_check_gpu",
    "_check_emf_names",
    "_check_prom_names",
)


def _guarded(check: Callable[..., Result], *args) -> Result:
    try:
        return check(*args)
    except Exception as exc:  # noqa: BLE001 - never let one check crash the tool
        return (getattr(check, "__name__", "check"), False, f"exception: {exc}")


def main(argv: Optional[List[str]] = None) -> int:
    campaign = campaign_paths.apply_env()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print every OK line too (default: only failures print detail).",
    )
    p.add_argument(
        "--engine-root",
        default=None,
        help="Pumice checkout to check (default: target.json engine.root through "
        "AMMO_ENGINE_ROOT, else up from the current directory).",
    )
    p.add_argument(
        "--model-path",
        default=None,
        help="Local model directory used with pumice-server --model (default: "
        "target.json target.model_id). Checked for config.json + tokenizer.json.",
    )
    p.add_argument(
        "--skip-server-flags",
        action="store_true",
        help="Skip the pumice-server --help probe (use when the binary is "
        "not on PATH; the check would otherwise fail).",
    )
    p.add_argument(
        "--write-fingerprint",
        default=None,
        metavar="PATH",
        help="Write the stack fingerprint JSON to PATH ('-' for stdout), run no "
        "check, and exit 0.",
    )
    args = p.parse_args(argv)

    explicit_root: Optional[Path] = None
    if args.engine_root:
        explicit_root = Path(args.engine_root).expanduser().resolve()
        # The flag wins over target.json; both names carry the engine root (§4).
        os.environ["AMMO_ENGINE_ROOT"] = str(explicit_root)
        os.environ["AMMO_RUNTIME_SOURCE_DIR"] = str(explicit_root)

    if args.write_fingerprint:
        root = explicit_root if explicit_root is not None else _find_repo_root()
        return _write_fingerprint(args.write_fingerprint, root)

    target = _load_target(campaign)
    bench = _section(target, "bench")
    model_id = args.model_path or _section(target, "target").get("model_id")
    model_path, model_dir = _resolve_model_dir(model_id if isinstance(model_id, str) else None)
    extra_args = [str(a) for a in (bench.get("extra_args") or []) if isinstance(a, (str, int, float))]
    runner_env = dict(os.environ)
    baseline_env = bench.get("baseline_env")
    if isinstance(baseline_env, Mapping):
        runner_env.update({str(k): str(v) for k, v in baseline_env.items()})

    checks = globals()
    results: List[Result] = [_guarded(checks[name]) for name in CHECKS]
    kind, how = _resolve_model_runner(model_dir, extra_args, runner_env, _nvidia_smi_rows())
    results.append(_guarded(_check_pins, kind, how))
    for name in LATE_CHECKS:
        if name == "_check_server_flags" and args.skip_server_flags:
            results.append(("server flags", True, "skipped (--skip-server-flags)"))
            continue
        results.append(_guarded(checks[name]))
    results.append(_guarded(_check_model_path, model_path))

    failures = [(label, detail) for label, ok, detail in results if not ok]

    for label, ok, detail in results:
        marker = "OK" if ok else "FAIL"
        if ok and not args.verbose:
            continue
        print(f"{marker:4s} {label}: {detail}")

    if failures:
        print(f"\n{len(failures)} preflight check(s) failed.", file=sys.stderr)
        return 1
    if args.verbose:
        print(f"\nAll {len(results)} preflight checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
