"""Discover NEFFs to evaluate — either from a pre-compiled directory or by
collecting test IDs from a sibling kernel package via pytest --collect-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class NeffCase:
    """A single NEFF + the test that produced it."""

    test_name: str          # sanitized name (matches the ``out-*`` directory)
    neff_path: Path         # absolute path to ``file.neff``
    case_dir: Path          # the ``out-<test_name>`` directory


def discover_local_neffs(neff_dir: Path) -> list[NeffCase]:
    """Walk a directory of pre-compiled NEFFs (``out-<name>/file.neff``).

    Mirrors the existing ad-hoc scripts under ``/tmp/eval_*.py``.
    """
    neff_dir = Path(neff_dir).resolve()
    cases: list[NeffCase] = []
    for neff_path in sorted(neff_dir.rglob("file.neff")):
        case_dir = neff_path.parent
        name = case_dir.name
        if name.startswith("out-"):
            test_name = name[len("out-"):]
        else:
            test_name = name
        cases.append(NeffCase(test_name=test_name, neff_path=neff_path, case_dir=case_dir))
    return cases


_PYTEST_NODEID_RE = re.compile(r"^(?P<file>[^:]+::[^\s]+)$")


def collect_fast_test_ids(pkg_root: Path, filter_pattern: str | None = None) -> list[str]:
    """Run ``pytest --collect-only -m fast`` against a Brazil package and return node IDs.

    Args:
        pkg_root: path to a Brazil package containing fast tests
            (e.g. ``KaenaNeuronKernelLibrary``).
        filter_pattern: optional ``-k`` style substring filter passed to pytest.
    """
    pkg_root = Path(pkg_root).resolve()
    cmd = [
        "brazil-build", "pytest", "--",
        "--collect-only", "-q",
        "-m", "fast",
        "test/integration",
    ]
    if filter_pattern:
        cmd.extend(["-k", filter_pattern])
    proc = subprocess.run(cmd, cwd=str(pkg_root), capture_output=True, text=True, check=False)
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(
            f"pytest --collect-only failed (rc={proc.returncode}):\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    node_ids: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not _PYTEST_NODEID_RE.match(line):
            continue
        node_ids.append(line)
    return node_ids
