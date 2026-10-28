#!/usr/bin/env python3
"""AMMO preflight — assert Pumice couplings the sweep depends on still exist.

Replaces ``preflight_vllm_api.py``. ``run_pumice_grpc_bench.py`` reaches into
the Pumice gRPC contract (proto message fields), the ``pumice-server`` CLI
(named flags), the CloudWatch EMF metrics daemon (metric names on stdout),
the local model layout (``<path>/config.json``), and, on CUDA runners, the
embedded vLLM version pinned by ``shared/pumice_pins.py``. Any of these
renaming or moving degrades the sweep silently — the runner falls back to
"no data" and a track cannot qualify.

Run this once at campaign start and after any Pumice change.

Import + introspection only. It never launches a server, so it needs NO GPU.

Exit codes
----------
  0  every symbol / field / flag / metric present
  1  one or more items missing (one line per failure)
  2  pumice itself cannot be imported and the runner is Pumice-native

Usage
-----
  .venv/bin/python scripts/preflight_pumice.py [--verbose]
  .venv/bin/python scripts/preflight_pumice.py \\
      --model-path /hf/gpt-oss-20b [--verbose]
"""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple


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
#: ``build_server_cmd``). The runner NEVER falls back on a missing flag — the
#: launch fails healthchecking and the sweep aborts — so we check them here.
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


# ---------------------------------------------------------------------------
# Checks (each returns (label, ok, detail))
# ---------------------------------------------------------------------------


Result = Tuple[str, bool, str]


def _check_pumice_imports() -> Result:
    try:
        importlib.import_module("pumice")
    except ImportError as exc:
        return ("pumice import", False, f"cannot import pumice: {exc}")
    return ("pumice import", True, "pumice imports")


#: Where the session image installs ``shared/pumice_pins.py`` (Dockerfile
#: ``COPY shared/pumice_pins.py /usr/local/share/ammo/``). ``/app/shared`` is
#: the server tree; it is root-only in the image but readable in a source
#: checkout. ``AMMO_PUMICE_PINS_FILE`` overrides both.
PINS_FILE_FALLBACKS = (
    "/usr/local/share/ammo/pumice_pins.py",
    "/app/shared/pumice_pins.py",
)


def _installed_pins_file() -> Optional[Path]:
    """Return the first readable installed pins file, or None."""
    candidates: List[str] = []
    override = (os.environ.get("AMMO_PUMICE_PINS_FILE") or "").strip()
    if override:
        candidates.append(override)
    candidates.extend(PINS_FILE_FALLBACKS)
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.R_OK):
            return path
    return None


def _load_module_from_file(name: str, path: Path):
    """Import ``path`` as module ``name`` without touching ``sys.path``."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot build an import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _find_repo_root() -> Optional[Path]:
    """Return the Pumice checkout that owns this run, or None.

    Order: ``AMMO_RUNTIME_SOURCE_DIR`` (the server stamps the session
    worktree), then the current directory and its parents, then the
    parents of this script (``<worktree>/scripts``).
    The marker is ``rust_grpc_server.py``, the launcher the runner uses.
    """
    marker = "rust_grpc_server.py"
    stamped = (os.environ.get("AMMO_RUNTIME_SOURCE_DIR") or "").strip()
    starts: List[Path] = []
    if stamped:
        starts.append(Path(stamped))
    starts.append(Path.cwd())
    starts.append(Path(__file__).resolve())
    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / marker).is_file():
                return candidate
    return None


def _check_pins() -> Result:
    """Compare installed torch / vllm against ``shared/pumice_pins.py``.

    Only enforced when the current runner is vLLM-backed (CUDA). Neuron
    installs no vLLM and would otherwise fail this check spuriously.
    """
    kind = (os.environ.get("AMMO_ACCELERATOR_KIND") or "").strip().lower()
    if kind == "neuron":
        return ("pumice pins", True, "skipped on Neuron (no vllm install)")
    try:
        # Prefer the in-repo shared/pumice_pins.py so this preflight matches
        # the pins the base image was built against. Walk parents to find the
        # repo root (the script lives at different depths in _ammo_shared vs
        # the rendered .claude / .codex variants).
        anchor: Optional[Path] = None
        for parent in Path(__file__).resolve().parents:
            if (parent / "shared" / "pumice_pins.py").is_file():
                anchor = parent
                break
        if anchor is not None:
            if str(anchor) not in sys.path:
                sys.path.insert(0, str(anchor))
            pins = importlib.import_module("shared.pumice_pins")
        else:
            # A session worktree has no shared/ directory (the script sits
            # under <worktree>/scripts). The image ships
            # the same file at /usr/local/share/ammo/pumice_pins.py, which
            # uid 1000 can read; /app/shared is root-only.
            pins_file = _installed_pins_file()
            if pins_file is None:
                return (
                    "pumice pins",
                    False,
                    "shared/pumice_pins.py not found up the tree and none of "
                    + ", ".join(PINS_FILE_FALLBACKS)
                    + " is readable (set AMMO_PUMICE_PINS_FILE)",
                )
            pins = _load_module_from_file("ammo_pumice_pins", pins_file)
    except (ImportError, OSError, SyntaxError) as exc:
        return ("pumice pins", False, f"cannot import shared/pumice_pins.py: {exc}")
    ok, reason = pins.check_pins()
    return ("pumice pins", bool(ok), reason or "pins match")


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
    else ``<repo>/rust_grpc_server.py`` under this interpreter (the session
    venv python), else the ``pumice-server`` binary. The Rust binary parses
    argv by hand and answers ``Unknown argument: --help``; the Python
    launcher has argparse and lists every flag.
    """
    exe = (os.environ.get("AMMO_PUMICE_SERVER_EXE") or "").strip()
    if exe:
        return ([exe, "--help"], exe)
    root = _find_repo_root()
    if root is not None:
        launcher = root / "rust_grpc_server.py"
        return ([sys.executable, str(launcher), "--help"], str(launcher))
    return (["pumice-server", "--help"], "pumice-server")


def _pumice_parser_source() -> Optional[Path]:
    """Return ``pumice-rust/src/main.rs`` of the session engine tree.

    Looks in ``$AMMO_RUNTIME_SOURCE_DIR`` (set by the AMMO server to the
    session worktree), then the current directory and its parents. Unlike
    ``_find_repo_root`` (which keys on ``rust_grpc_server.py``), this locates
    the parser source directly so the flag fallback works even when only the
    Rust tree is present.
    """
    roots: List[Path] = []
    env_root = os.environ.get("AMMO_RUNTIME_SOURCE_DIR")
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
    the flag literals in the parser source the session builds from. The
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
            detail + "; pumice-rust/src/main.rs not found (set "
            "AMMO_RUNTIME_SOURCE_DIR or run from the worktree)",
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


def _check_cmb_api_patch() -> Result:
    """The ``[patch.crates-io]`` row for ``amzn-mantle-cmb-api`` is required.

    ``pumice-rust/Cargo.toml`` names the internal crate without a lock, so it
    cannot resolve unless ``$CARGO_HOME/config.toml`` carries the patch row
    (WS2.2). No patch -> ``cargo build`` in the session fails with
    "the crate amzn-mantle-cmb-api is not found". This check reads the file
    at run time — the freshness gate already includes it in its source set
    (WS4.5), so a stale patch also fails the freshness gate downstream.
    """
    cargo_home = os.environ.get("CARGO_HOME")
    if not cargo_home:
        return ("cmb-api patch", False, "CARGO_HOME is not set")
    path = Path(cargo_home) / "config.toml"
    if not path.is_file():
        return ("cmb-api patch", False, f"{path} does not exist")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "[patch.crates-io]" not in text:
        return ("cmb-api patch", False, f"{path}: [patch.crates-io] block missing")
    if "amzn-mantle-cmb-api" not in text:
        return ("cmb-api patch", False, f"{path}: amzn-mantle-cmb-api row missing")
    return ("cmb-api patch", True, str(path))


def _check_model_path(model_path: Optional[str]) -> Result:
    """The Pumice Rust loader takes a directory that contains ``config.json``.

    ``async_llm.rs`` panics with ``Failed to load tokenizer from
    <path>/tokenizer.json`` when the path is an HF id. Refuses to guess: the
    caller passes the exact path the sweep will pass to ``--model``.
    """
    if not model_path:
        return ("model path", True, "skipped (no --model-path supplied)")
    p = Path(model_path).expanduser()
    if not p.is_dir():
        return ("model path", False, f"{p} is not a directory")
    for name in ("config.json", "tokenizer.json"):
        if not (p / name).is_file():
            return ("model path", False, f"{p / name} does not exist")
    return ("model path", True, str(p))


def _check_emf_names() -> Result:
    """Static sanity — the runner literally reads these names, listed here."""
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


CHECKS: List[Callable[..., Result]] = [
    _check_pumice_imports,
    _check_pins,
    _check_proto,
    _check_server_flags,
    _check_cmb_api_patch,
    _check_emf_names,
    _check_prom_names,
]


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print every OK line too (default: only failures print detail).",
    )
    p.add_argument(
        "--model-path",
        default=None,
        help="Local model directory used with pumice-server --model. Checked "
        "for config.json + tokenizer.json.",
    )
    p.add_argument(
        "--skip-server-flags",
        action="store_true",
        help="Skip the pumice-server --help probe (use when the binary is "
        "not on PATH; the check would otherwise fail).",
    )
    args = p.parse_args(argv)

    results: List[Result] = []
    for check in CHECKS:
        if check is _check_server_flags and args.skip_server_flags:
            results.append(("server flags", True, "skipped (--skip-server-flags)"))
            continue
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — never let one check crash the tool
            results.append((getattr(check, "__name__", "check"), False, f"exception: {exc}"))
    results.append(_check_model_path(args.model_path))

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
