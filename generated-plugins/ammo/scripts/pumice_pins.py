"""
Pumice version pins
====================

Single source of truth for the version numbers that the Pumice CUDA base
image locks. Every later check imports this module instead of repeating
the numbers (plan WS1.1).

Constants:
  REQUIRED_VLLM      - vLLM version baked into pumice-cuda-base:local.
  REQUIRED_TORCH     - torch version baked into pumice-cuda-base:local.
  CUDA_BASE_DIGEST   - digest of the public DLC base image the CUDA base
                        builds FROM. Pull by digest only.
  PUMICE_COMMIT      - full SHA of the Pumice `mainline` commit this repo
                        targets (plan section 0).

CLI:
  python shared/pumice_pins.py --assert
    Exit 0 when the installed `vllm` and `torch` versions
    (importlib.metadata) equal REQUIRED_VLLM and REQUIRED_TORCH, and, if
    `pumice` imports, `pumice.config.REQUIRED_VLLM_VERSION == REQUIRED_VLLM`.
    Exit 1 with a one-line reason otherwise (missing package, version
    mismatch, or pumice/vllm disagreement).
"""

from __future__ import annotations

import sys
from importlib import metadata as importlib_metadata

REQUIRED_VLLM = "0.30.0"
REQUIRED_TORCH = "2.13.0+cu130"
CUDA_BASE_DIGEST = (
    "sha256:970ca980451d9b6590e0e507502b341551ff8de5d09713b142972d14f40cb79f"
)
PUMICE_COMMIT = "9d126713c03dcc88883b85c3d3a857b89982c957"


def _installed_version(package: str) -> str | None:
    """Return the installed version of a package, or None if it is absent."""
    try:
        return importlib_metadata.version(package)
    except importlib_metadata.PackageNotFoundError:
        return None


def check_pins() -> tuple[bool, str]:
    """Check the installed stack against the pins.

    Returns (ok, reason). reason is a one-line message; empty when ok.
    """
    vllm_version = _installed_version("vllm")
    if vllm_version is None:
        return False, "vllm is not installed"
    if vllm_version != REQUIRED_VLLM:
        return False, (
            f"vllm {vllm_version} does not match REQUIRED_VLLM {REQUIRED_VLLM}"
        )

    torch_version = _installed_version("torch")
    if torch_version is None:
        return False, "torch is not installed"
    if torch_version != REQUIRED_TORCH:
        return False, (
            f"torch {torch_version} does not match REQUIRED_TORCH {REQUIRED_TORCH}"
        )

    try:
        import pumice.config as pumice_config
    except ImportError:
        # pumice is not on the import path. The vllm and torch pins already
        # passed, so there is nothing left to check.
        return True, ""

    pumice_required_vllm = getattr(pumice_config, "REQUIRED_VLLM_VERSION", None)
    if pumice_required_vllm != REQUIRED_VLLM:
        return False, (
            f"pumice.config.REQUIRED_VLLM_VERSION {pumice_required_vllm!r} "
            f"does not match REQUIRED_VLLM {REQUIRED_VLLM!r}"
        )

    return True, ""


def main(argv: list[str]) -> int:
    if "--assert" not in argv:
        print(__doc__)
        return 0

    ok, reason = check_pins()
    if ok:
        print("pumice_pins: ok")
        return 0

    print(f"pumice_pins: FAIL: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
