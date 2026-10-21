"""Drive ``brazil-build integration-test --test-mode compile-only`` against a
sibling Brazil package, then locate the produced NEFFs.

Used when the eval CLI is invoked with ``--compile-from-pkg``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .discovery import NeffCase


@dataclass
class CompileResult:
    returncode: int
    stdout: str
    stderr: str


def run_compile_only(
    pkg_root: Path,
    filter_pattern: str | None = None,
    target: str = "trn2",
    extra_args: list[str] | None = None,
) -> CompileResult:
    """Run compile-only for fast tests in a Brazil package.

    The compiler writes NEFFs to ``<pkg_root>/neuron_test_output*/out-<name>/file.neff``.
    """
    pkg_root = Path(pkg_root).resolve()
    cmd = [
        "brazil-build", "integration-test",
        "--test-mode=compile-only",
        "-m", "fast",
        "-n", "auto", "--maxprocesses", "64", "--dist", "worksteal",
        "--platform-target", target,
    ]
    if filter_pattern:
        cmd.extend(["-k", filter_pattern])
    if extra_args:
        cmd.extend(extra_args)
    proc = subprocess.run(cmd, cwd=str(pkg_root), capture_output=True, text=True, check=False)
    return CompileResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def locate_compiled_neffs(pkg_root: Path) -> list[NeffCase]:
    """Scan compile-only output directories for NEFFs."""
    pkg_root = Path(pkg_root).resolve()
    cases: list[NeffCase] = []
    for output_root in sorted(pkg_root.glob("neuron_test_output*")):
        if not output_root.is_dir():
            continue
        for neff_path in sorted(output_root.rglob("file.neff")):
            case_dir = neff_path.parent
            name = case_dir.name
            test_name = name[len("out-"):] if name.startswith("out-") else name
            cases.append(NeffCase(test_name=test_name, neff_path=neff_path, case_dir=case_dir))
    return cases
