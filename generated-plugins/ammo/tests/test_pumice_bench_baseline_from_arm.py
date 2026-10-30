#!/usr/bin/env python3
"""Tests for the --baseline-from-arm flag on run_pumice_grpc_bench.py.

`--baseline-from` imports the comparator by LITERAL filename
`{prefix}_{tag}.json`, so the comparator identity is the filename prefix, not
the directory. Without this flag, a round >= 2 integration sweep pointed at a
pre-SHIP directory silently imports that directory's bf16 baseline arm and the
cumulative delta is read as incremental.

Ported from the vLLM-era `test_sweep_baseline_from_arm.py` (upstream commits
24e5b47 + 2551a9b). The semantics are engine-independent — the import is pure
file bookkeeping — so the whole contract carries over. What changes is the
harness: the Pumice runner launches a `pumice-server` subprocess and drives it
over gRPC, so the comparator arm's rows come from an import while the measured
arm's rows come from the mocked engine, exactly as in the real gate run.

Covers:
- default is 'baseline' (byte-identical to the pre-flag behavior)
- --baseline-from-arm opt imports the 'opt' filename prefix instead
- an unknown arm is rejected by argparse
- imported rows always land under baseline_label, whichever arm was read
- a non-default arm that the run cannot honor is disclosed as IGNORED
- a honored non-default arm names the imported comparator in the .md report
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent / "scripts"
for entry in (str(_SCRIPTS_DIR), str(_TESTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import run_pumice_grpc_bench as bench  # noqa: E402
from test_pumice_bench_profile import _MockPb2, _MockStub, _MockTokenizer  # noqa: E402

_FUTURE_MTIME = 2_000_000_000.0

# Three distinct latencies so a row can only have come from ONE source.
BASELINE_AVG = 0.050
IMPORTED_OPT_AVG = 0.030


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class TestBaselineFromArmArg:
    def test_default_is_baseline(self):
        ns = bench.parse_args(["--artifact-dir", "/tmp/x"])
        assert ns.baseline_from_arm == "baseline"

    def test_opt_is_accepted(self):
        ns = bench.parse_args(
            ["--artifact-dir", "/tmp/x", "--baseline-from-arm", "opt"]
        )
        assert ns.baseline_from_arm == "opt"

    def test_unknown_arm_is_rejected(self):
        with pytest.raises(SystemExit):
            bench.parse_args(
                ["--artifact-dir", "/tmp/x", "--baseline-from-arm", "mainline"]
            )


# ---------------------------------------------------------------------------
# Fixtures: a fake checkout, a scaffolded artifact dir, a mocked engine
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    """A fake Pumice checkout whose build is newer than its sources."""
    repo = tmp_path / "pumice"
    (repo / "pumice-rust" / "src").mkdir(parents=True)
    (repo / "pumice-rust" / "confdb").mkdir(parents=True)
    (repo / "pumice-rust" / "target" / "release").mkdir(parents=True)
    (repo / "pumice-rust" / "src" / "engine.rs").write_text("fn main(){}", encoding="utf-8")
    (repo / "pumice-rust" / "confdb" / "gpt_oss.toml").write_text("x=1", encoding="utf-8")
    (repo / "pumice-rust" / "build.rs").write_text("fn main(){}", encoding="utf-8")
    (repo / "pumice-rust" / "Cargo.toml").write_text("[package]", encoding="utf-8")
    for name in ("libpumice_rust.so", "pumice-server"):
        path = repo / "pumice-rust" / "target" / "release" / name
        path.write_text("bin", encoding="utf-8")
        os.utime(path, (_FUTURE_MTIME, _FUTURE_MTIME))
    return repo


def _make_artifact(tmp_path: Path) -> Path:
    artifact = tmp_path / "artifact"
    (artifact / "rounds" / "2" / "sweeps").mkdir(parents=True)
    (artifact / "state.json").write_text(
        json.dumps({"campaign": {"current_round": 2}}), encoding="utf-8"
    )
    (artifact / "target.json").write_text(
        json.dumps(
            {
                "artifact_dir": str(artifact),
                "target": {
                    "model_id": "/models/m",
                    "dtype": "mxfp4",
                    "tp": 1,
                    "ep": 1,
                    "max_model_len": 4096,
                },
                "workload": {
                    "input_len": 64,
                    "output_len": 8,
                    "batch_sizes": [2],
                    "num_iters": 3,
                },
                "bench": {
                    "runner": "pumice_grpc_bench",
                    "server_cmd": "pumice-server",
                    "extra_args": [],
                    "baseline_env": {},
                    "opt_env": {"PUMICE_X": "1"},
                    "baseline_label": "baseline",
                    "opt_label": "opt",
                },
            }
        ),
        encoding="utf-8",
    )
    return artifact


def _raw_bucket_json(avg_latency: float) -> dict:
    """The per-bucket raw schema the runner writes and the row builder reads."""
    return {
        "avg_latency": avg_latency,
        "latencies": [avg_latency, avg_latency, avg_latency],
        "percentiles": {
            "10": avg_latency,
            "50": avg_latency,
            "90": avg_latency,
            "99": avg_latency,
        },
    }


def _write_stage1_dir(artifact: Path) -> Path:
    """A pre-SHIP sweep dir holding BOTH arms — exactly the live bug's shape."""
    stage1 = artifact / "rounds" / "1" / "sweeps" / "baseline"
    json_dir = stage1 / "json"
    logs_dir = stage1 / "logs"
    json_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    for prefix, avg in (("baseline", BASELINE_AVG), ("opt", IMPORTED_OPT_AVG)):
        (json_dir / f"{prefix}_bs2.json").write_text(
            json.dumps(_raw_bucket_json(avg)), encoding="utf-8"
        )
        (json_dir / f"{prefix}_bs2.runner.json").write_text(
            json.dumps({"ok": True, "label": prefix, "batch_size": 2}), encoding="utf-8"
        )
        (logs_dir / f"{prefix}_bs2.log").write_text(
            f"=== {prefix} arm ===\n", encoding="utf-8"
        )
    return stage1


@pytest.fixture
def mocked_engine(monkeypatch):
    """Replace the server lifecycle + tokenizer with in-process stubs."""
    launched: list = []

    class FakeServer(bench.ServerHandle):
        def start(self):
            self.start_unix = 1000.0
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(
                '{"_aws":{},"spec_acceptance_rate":55.0}\n', encoding="utf-8"
            )
            launched.append(self)

        def connect(self):
            self.pb2 = _MockPb2
            self.stub = _MockStub()

        def wait_healthy(self, **kwargs):
            return

        def stop(self, **kwargs):
            return

    monkeypatch.setattr(bench, "ServerHandle", FakeServer)
    monkeypatch.setattr(bench, "resolve_server_exe", lambda spec, root: ["pumice-server"])
    monkeypatch.setattr(bench, "_load_tokenizer", lambda model_id: _MockTokenizer())
    return launched


def _run_sweep(tmp_path: Path, extra_args, labels: str = "opt"):
    """Run one gate sweep; return (results JSON, results .md text)."""
    artifact = _make_artifact(tmp_path)
    repo = _make_repo(tmp_path)
    stage1 = _write_stage1_dir(artifact)
    rc = bench.main(
        [
            "--artifact-dir", str(artifact),
            "--round", "2",
            "--slot", "opt/op007",
            "--labels", labels,
            "--repo-root", str(repo),
            "--prompt-source", "raw",
            "--warmup-waves", "1",
            "--baseline-from", str(stage1),
            *extra_args,
        ]
    )
    assert rc == 0, f"sweep exited {rc}"
    out_dir = artifact / "rounds" / "2" / "sweeps" / "opt" / "op007"
    results = json.loads((out_dir / "e2e_latency_results.json").read_text())
    md = (out_dir / "e2e_latency_results.md").read_text()
    return results, md


# ---------------------------------------------------------------------------
# Import behavior: which filename prefix is read
# ---------------------------------------------------------------------------


class TestBaselineFromArmImport:
    def test_default_imports_the_baseline_prefix(self, tmp_path, mocked_engine):
        """Absent the flag, the comparator is the {baseline_label}_ prefix."""
        results, _md = _run_sweep(tmp_path, [])
        row = results["results"][0]
        assert row["baseline"]["avg_s"] == pytest.approx(BASELINE_AVG)
        # Backward compat: the default run's header gains no new key.
        assert "baseline_source_arm" not in results

    def test_arm_opt_imports_the_opt_prefix(self, tmp_path, mocked_engine):
        """--baseline-from-arm opt reads the promoted arm's files instead."""
        results, _md = _run_sweep(tmp_path, ["--baseline-from-arm", "opt"])
        row = results["results"][0]
        assert row["baseline"]["avg_s"] == pytest.approx(IMPORTED_OPT_AVG)
        assert row["baseline"]["avg_s"] != pytest.approx(BASELINE_AVG)
        # A non-default comparator arm is disclosed in the artifact.
        assert results["baseline_source_arm"] == "opt"

    def test_imported_rows_land_under_baseline_label(self, tmp_path, mocked_engine):
        """The import writes under baseline_label whichever arm it read, so the
        results collection and every downstream gate keep reading one key."""
        results, _md = _run_sweep(tmp_path, ["--baseline-from-arm", "opt"])
        out_dir = Path(results["out_dir"]) if results.get("out_dir") else None
        row = results["results"][0]
        assert row["baseline"]["ok"] is True
        assert results["bench"]["baseline_label"] == "baseline"
        if out_dir is not None:
            # The on-disk filename is the baseline prefix, not the arm read.
            assert (out_dir / "json" / "baseline_bs2.json").is_file()
            assert not (out_dir / "json" / "opt_bs2.json").read_text() == json.dumps(
                _raw_bucket_json(IMPORTED_OPT_AVG)
            )

    def test_missing_arm_files_fail_loudly(self, tmp_path, mocked_engine):
        """A directory with no files under the requested prefix must abort, and
        the message must name the prefix actually looked for."""
        artifact = _make_artifact(tmp_path)
        repo = _make_repo(tmp_path)
        stage1 = artifact / "rounds" / "1" / "sweeps" / "baseline"
        (stage1 / "json").mkdir(parents=True)
        (stage1 / "json" / "baseline_bs2.json").write_text(
            json.dumps(_raw_bucket_json(BASELINE_AVG)), encoding="utf-8"
        )
        with pytest.raises(SystemExit) as excinfo:
            bench.main(
                [
                    "--artifact-dir", str(artifact),
                    "--round", "2",
                    "--slot", "opt/op007",
                    "--labels", "opt",
                    "--repo-root", str(repo),
                    "--prompt-source", "raw",
                    "--baseline-from", str(stage1),
                    "--baseline-from-arm", "opt",
                ]
            )
        assert "opt_<tag>.json" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Disclosure: the flag is never silently dead, and never silently honored
# ---------------------------------------------------------------------------


class TestBaselineFromArmDisclosure:
    """The .md report is what an auditor reads. Both the ignored case and the
    honored case must be visible there, not only in the JSON header."""

    def test_honored_arm_is_named_in_the_md_report(self, tmp_path, mocked_engine):
        results, md = _run_sweep(tmp_path, ["--baseline-from-arm", "opt"])
        warnings = results.get("warnings") or []
        provenance = [w for w in warnings if "comparator imported from arm" in w]
        assert provenance, f"no comparator-provenance warning in {warnings}"
        assert "'opt'" in provenance[0]
        assert "--baseline-from-arm" in provenance[0]
        # Visible to a reader of the .md alone.
        assert "comparator imported from arm 'opt'" in md

    def test_ignored_arm_is_disclosed_when_the_import_is_gated_off(
        self, tmp_path, mocked_engine
    ):
        """--labels baseline,opt gates the import block off, so the flag cannot
        act. The run must say so instead of implying a promoted comparator."""
        results, md = _run_sweep(
            tmp_path, ["--baseline-from-arm", "opt"], labels="baseline,opt"
        )
        warnings = results.get("warnings") or []
        ignored = [w for w in warnings if "was IGNORED" in w]
        assert ignored, f"no ignored-flag warning in {warnings}"
        assert "--baseline-from-arm opt" in ignored[0]
        assert "--baseline-from-arm opt was IGNORED" in md
        # The gated-off run imports nothing, so it must not also claim provenance.
        assert not [w for w in warnings if "comparator imported from arm" in w]

    def test_default_run_gains_no_arm_warning(self, tmp_path, mocked_engine):
        """Backward compat: the default arm adds no arm-related warning."""
        results, _md = _run_sweep(tmp_path, [])
        warnings = results.get("warnings") or []
        assert not [w for w in warnings if "--baseline-from-arm" in w]
        assert not [w for w in warnings if "comparator imported from arm" in w]
