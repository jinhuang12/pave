"""
Pumice version pins
====================

Single source of truth for the Pumice facts that AMMO pins. Every later check
imports this module instead of repeating the numbers (conventions §6). It is
the plugin copy of the ammo-server pins module, re-pinned for a host-local
campaign: there is no container image, so there is no image digest.

Constants:
  PUMICE_COMMIT   - full SHA of the Pumice `mainline` commit this plugin
                    targets. The campaign engine root must be at this commit
                    (or descend from it) before preflight passes.
  REQUIRED_TORCH  - torch version the engine venv must have.

The vLLM pin is not a constant. Pumice publishes the vLLM release its vLLM
runner is built against as `pumice.config.REQUIRED_VLLM_VERSION`. check_pins()
compares the installed `vllm` with that value, and only when the model-runner
kind is `vllm`. The Pumice runner does not use vLLM.

CLI:
  python scripts/pumice_pins.py --assert [--model-runner KIND]
    Exit 0 when the installed `torch` (importlib.metadata) equals
    REQUIRED_TORCH and, when KIND is `vllm`, the installed `vllm` equals
    `pumice.config.REQUIRED_VLLM_VERSION`. Exit 1 with a one-line reason
    otherwise (missing package, version mismatch, or pumice cannot be read).
"""

from __future__ import annotations

import sys
from importlib import metadata as importlib_metadata
from typing import Optional

# Pumice pyproject.toml:127 at PUMICE_COMMIT, in the `cuda` extra:
#     "torch==2.13.0",
# The +cu130 local tag is the CUDA 13 wheel index that the same file names for
# the `cuda` extra (pyproject.toml:46-49). PEP 440 lets torch==2.13.0 accept it.
REQUIRED_TORCH = "2.13.0+cu130"
PUMICE_COMMIT = "769c752995b8a39b27df028176411d17c99ff441"

VLLM_RUNNER = "vllm"


def _installed_version(package: str) -> str | None:
    """Return the installed version of a package, or None if it is absent."""
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return None


def _engine_required_vllm() -> tuple[Optional[str], str]:
    """Return (pumice.config.REQUIRED_VLLM_VERSION, reason when it is unreadable)."""
    try:
        import pumice.config as pumice_config
    except ImportError as exc:
        return None, (
            "cannot read pumice.config.REQUIRED_VLLM_VERSION: pumice does not "
            f"import ({exc})"
        )
    required = getattr(pumice_config, "REQUIRED_VLLM_VERSION", None)
    if not isinstance(required, str) or not required:
        return None, "pumice.config.REQUIRED_VLLM_VERSION is missing"
    return required, ""


def check_pins(model_runner: Optional[str] = None) -> tuple[bool, str]:
    """Check the installed stack against the pins.

    ``model_runner`` is the runner kind the engine resolves for the target
    model (``pumice``, ``vllm``, ...). The vLLM check runs only for ``vllm``.

    Returns (ok, reason). reason is a one-line message; empty when ok.
    """
    torch_version = _installed_version("torch")
    if torch_version is None:
        return False, "torch is not installed"
    if torch_version != REQUIRED_TORCH:
        return False, (
            f"torch {torch_version} does not match REQUIRED_TORCH {REQUIRED_TORCH}"
        )

    if (model_runner or "").strip().lower() != VLLM_RUNNER:
        return True, ""

    vllm_version = _installed_version("vllm")
    if vllm_version is None:
        return False, "vllm is not installed (the model runner kind is vllm)"
    required, reason = _engine_required_vllm()
    if required is None:
        return False, reason
    if vllm_version != required:
        return False, (
            f"vllm {vllm_version} does not match "
            f"pumice.config.REQUIRED_VLLM_VERSION {required}"
        )
    return True, ""


def _model_runner_arg(argv: list[str]) -> Optional[str]:
    for index, arg in enumerate(argv):
        if arg == "--model-runner":
            return argv[index + 1] if index + 1 < len(argv) else None
        if arg.startswith("--model-runner="):
            return arg.split("=", 1)[1]
    return None


def main(argv: list[str]) -> int:
    if "--assert" not in argv:
        print(__doc__)
        return 0

    ok, reason = check_pins(model_runner=_model_runner_arg(argv))
    if ok:
        print("pumice_pins: ok")
        return 0

    print(f"pumice_pins: FAIL: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
