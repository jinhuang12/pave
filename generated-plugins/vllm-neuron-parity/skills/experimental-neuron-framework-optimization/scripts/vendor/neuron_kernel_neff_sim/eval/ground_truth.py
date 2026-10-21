"""Ground-truth ``InferenceTime`` lookup.

Two backends:

- :class:`NtffBackend` — read ``infer_result/ntff.json`` next to a NEFF on disk.
  Works fully offline; matches the convention used by ad-hoc ``/tmp/eval_*.py``
  scripts and the in-repo ``per_test_histogram.py``.

- :class:`OpenSearchBackend` — query ``InferenceTime`` from the kernel-perf
  AOSS collection. The collection has a VPC-only network policy, so direct
  ``awscurl`` from a Cloud Desktop usually fails; we shell out via
  ``ssh kernel-perf-bastion 'awscurl ...'`` as a fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import shlex
import subprocess

log = logging.getLogger(__name__)


_AOSS_ENDPOINT = "https://7864weg7nz33vjfg36v3.us-east-1.aoss.amazonaws.com"


@dataclass
class GroundTruth:
    """Result of a ground-truth lookup for a single test."""

    test_name: str
    inference_time_s: float | None
    source: str  # "ntff" | "opensearch" | "missing"


class GroundTruthBackend(ABC):
    """Strategy interface for fetching ground-truth ``InferenceTime``."""

    @abstractmethod
    def fetch(self, test_name: str, neff_path: Path | None = None) -> GroundTruth:
        ...


class NtffBackend(GroundTruthBackend):
    """Read ``total_time`` from ``<case_dir>/infer_result/ntff.json``."""

    def fetch(self, test_name: str, neff_path: Path | None = None) -> GroundTruth:
        if neff_path is None:
            return GroundTruth(test_name, None, "missing")
        ntff_path = neff_path.parent / "infer_result" / "ntff.json"
        if not ntff_path.exists():
            return GroundTruth(test_name, None, "missing")
        try:
            with open(ntff_path) as f:
                ntff = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Failed to read %s: %s", ntff_path, exc)
            return GroundTruth(test_name, None, "missing")
        for v in ntff.values():
            if isinstance(v, dict) and "total_time" in v:
                return GroundTruth(test_name, float(v["total_time"]), "ntff")
        return GroundTruth(test_name, None, "missing")


class OpenSearchBackend(GroundTruthBackend):
    """Fetch ``Metrics.InferenceTime`` for a test from the kernel-perf collection."""

    def __init__(
        self,
        kernel_index: str,
        bastion: str | None = "kernel-perf-bastion",
        days_back: int = 30,
        run_type: str | None = None,
    ) -> None:
        """Args:
            kernel_index: AOSS index suffix (becomes ``kernel-perf-<index>``).
            bastion: SSH host that proxies awscurl. Empty/None = direct call.
            days_back: how far back to search.
            run_type: optional filter on ``RunType.keyword`` (e.g. ``"model"``,
                ``"development"``). When None, accepts any RunType.
        """
        self.kernel_index = kernel_index
        self.bastion = bastion
        self.days_back = days_back
        self.run_type = run_type

    def _build_query(self, test_name: str) -> str:
        must: list[dict] = [
            {"range": {"Timestamp": {"gte": f"now-{self.days_back}d"}}},
            {"term": {"TestName.keyword": test_name}},
            {"range": {"Metrics.InferenceTime": {"gt": 0}}},
        ]
        if self.run_type:
            must.append({"term": {"RunType.keyword": self.run_type}})
        body = {
            "size": 5,
            "query": {"bool": {"must": must}},
            "sort": [{"Timestamp": "desc"}],
            "_source": ["TestName", "Timestamp", "Metrics.InferenceTime", "RunId", "RunType"],
        }
        return json.dumps(body)

    def _awscurl_cmd(self, query_body: str) -> list[str]:
        url = f"{_AOSS_ENDPOINT}/kernel-perf-{self.kernel_index}/_search"
        return [
            "awscurl",
            "--service", "aoss",
            "--region", "us-east-1",
            "-XPOST", url,
            "-H", "Content-Type: application/json",
            "-d", query_body,
        ]

    def _run_via_bastion(self, cmd: list[str]) -> str:
        # Quote each arg for the remote shell.
        remote_cmd = "export PATH=$PATH:$HOME/.local/bin; " + " ".join(shlex.quote(c) for c in cmd)
        ssh_cmd = ["ssh", self.bastion, remote_cmd]
        proc = subprocess.run(ssh_cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"bastion query failed: {proc.stderr}")
        return proc.stdout

    def _run_direct(self, cmd: list[str]) -> str:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"awscurl failed: {proc.stderr}")
        return proc.stdout

    def fetch(self, test_name: str, neff_path: Path | None = None) -> GroundTruth:
        cmd = self._awscurl_cmd(self._build_query(test_name))
        try:
            if self.bastion:
                stdout = self._run_via_bastion(cmd)
            else:
                stdout = self._run_direct(cmd)
        except (RuntimeError, FileNotFoundError) as exc:
            log.warning("OpenSearch query failed for %s: %s", test_name, exc)
            return GroundTruth(test_name, None, "missing")

        try:
            doc = json.loads(stdout)
        except json.JSONDecodeError:
            log.warning("OpenSearch returned non-JSON for %s: %s", test_name, stdout[:200])
            return GroundTruth(test_name, None, "missing")
        hits = doc.get("hits", {}).get("hits", [])
        if not hits:
            return GroundTruth(test_name, None, "missing")
        src = hits[0].get("_source", {})
        metrics = src.get("Metrics") or {}
        inf_time = metrics.get("InferenceTime")
        if inf_time is None or inf_time <= 0:
            return GroundTruth(test_name, None, "missing")
        return GroundTruth(test_name, float(inf_time), "opensearch")


class AutoBackend(GroundTruthBackend):
    """Try OpenSearch first, fall back to ntff on miss."""

    def __init__(self, opensearch: OpenSearchBackend, ntff: NtffBackend) -> None:
        self.opensearch = opensearch
        self.ntff = ntff

    def fetch(self, test_name: str, neff_path: Path | None = None) -> GroundTruth:
        gt = self.opensearch.fetch(test_name, neff_path)
        if gt.inference_time_s is not None:
            return gt
        return self.ntff.fetch(test_name, neff_path)


def make_backend(
    mode: str,
    kernel_index: str | None,
    bastion: str | None,
    run_type: str | None = None,
) -> GroundTruthBackend:
    """Construct a backend from CLI flags. ``mode`` is ``ntff`` | ``opensearch`` | ``auto``."""
    if mode == "ntff":
        return NtffBackend()
    if not kernel_index and mode in ("opensearch", "auto"):
        raise ValueError(
            f"--kernel-index is required for ground-truth={mode} "
            "(used to select the kernel-perf-<index> AOSS index)"
        )
    if mode == "opensearch":
        return OpenSearchBackend(
            kernel_index=kernel_index or "", bastion=bastion or None, run_type=run_type,
        )
    if mode == "auto":
        return AutoBackend(
            opensearch=OpenSearchBackend(
                kernel_index=kernel_index or "", bastion=bastion or None, run_type=run_type,
            ),
            ntff=NtffBackend(),
        )
    raise ValueError(f"Unknown ground-truth mode: {mode}")
