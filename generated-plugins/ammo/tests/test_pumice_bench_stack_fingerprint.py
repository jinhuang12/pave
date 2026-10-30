#!/usr/bin/env python3
"""The Pumice runner records the stack fingerprint at measurement time.

new_target.py writes ``state.runtime.stack_fingerprint`` at scaffold time. A
trn2 campaign then committed an engine fix to the worktree before the Stage 1
baseline, so state described a commit the baseline never ran. These tests pin
the fix: every sweep header carries a fingerprint taken when the run starts,
and a successful Stage 1 baseline stores that fingerprint in state.json. The
digest must still verify under the gate and report checkers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _TESTS_DIR.parent / "scripts"
for entry in (str(_SCRIPTS_DIR), str(_TESTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import generate_validation_report as report  # noqa: E402
import run_pumice_grpc_bench as bench  # noqa: E402
import runtime_ops  # noqa: E402
import verify_validation_gates as gates  # noqa: E402
from test_pumice_bench_profile import _MockPb2, _MockStub, _MockTokenizer  # noqa: E402
from test_pumice_bench_smoke import _make_artifact, _make_repo  # noqa: E402

_PROFILE = "pumice-cuda"


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(
        ["git", *argv],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    ).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _git_repo(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path)
    _git(repo, "init", "-q")
    return repo


@pytest.fixture
def engine(monkeypatch):
    """In-process server stub. Set ``engine["fail"]`` to fail every Generate."""
    config: dict = {"fail": None}

    class FakeServer(bench.ServerHandle):
        def start(self):
            self.start_unix = 1000.0
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text("", encoding="utf-8")

        def connect(self):
            self.pb2 = _MockPb2
            self.stub = _MockStub(fail=config["fail"])

        def wait_healthy(self, **kwargs):
            return

        def stop(self, **kwargs):
            return

    monkeypatch.setattr(bench, "ServerHandle", FakeServer)
    monkeypatch.setattr(bench, "resolve_server_exe", lambda spec, root: ["pumice-server"])
    monkeypatch.setattr(bench, "_load_tokenizer", lambda model_id: _MockTokenizer())
    monkeypatch.setenv("AMMO_RUNTIME_PROFILE", _PROFILE)
    monkeypatch.delenv("AMMO_STACK_FINGERPRINT", raising=False)
    return config


def _scaffold_state(artifact: Path, repo: Path, monkeypatch) -> dict:
    """Write state.json the way new_target.py does, fingerprint included."""
    monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(repo))
    monkeypatch.chdir(repo)
    metadata = runtime_ops.campaign_runtime_metadata(dict(os.environ))
    state = {"campaign": {"current_round": 1, "current_stage": "1_baseline"}, **metadata}
    (artifact / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def _run(artifact: Path, repo: Path, *, slot: str = "baseline") -> tuple[int, dict]:
    rc = bench.main(
        [
            "--artifact-dir", str(artifact),
            "--round", "1",
            "--slot", slot,
            "--labels", "baseline",
            "--repo-root", str(repo),
            "--prompt-source", "raw",
            "--warmup-waves", "1",
        ]
    )
    out = artifact / "rounds" / "1" / "sweeps" / slot / "e2e_latency_results.json"
    return rc, json.loads(out.read_text(encoding="utf-8"))


def _state(artifact: Path) -> dict:
    return json.loads((artifact / "state.json").read_text(encoding="utf-8"))


def test_baseline_records_head_at_measurement_not_scaffold(tmp_path, engine, monkeypatch):
    repo = _git_repo(tmp_path)
    scaffold_commit = _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    scaffolded = _scaffold_state(artifact, repo, monkeypatch)
    scaffold_stack = scaffolded["runtime"]["stack_fingerprint"]
    assert scaffold_stack["execution_source"]["cwd_git"]["commit"] == scaffold_commit

    # The engine fix lands after scaffolding and before the Stage 1 baseline.
    measured_commit = _commit(repo, "b.txt")
    rc, results = _run(artifact, repo)

    assert rc == 0
    header_stack = results["stack_fingerprint"]
    resolved = str(repo.resolve())
    assert header_stack["execution_source"]["cwd_git"]["commit"] == measured_commit
    assert header_stack["sources"][resolved]["commit"] == measured_commit

    state = _state(artifact)
    assert state["runtime"]["stack_fingerprint"] == header_stack
    assert state["runtime"]["stack_fingerprint"]["digest"] != scaffold_stack["digest"]
    # Only the fingerprint moves; the rest of the binding is untouched.
    for key in ("runtime_profile", "accelerator_kind", "resource_ids", "lnc"):
        assert state["runtime"][key] == scaffolded["runtime"][key]
    assert state["accelerator_resources"] == scaffolded["accelerator_resources"]
    assert state["campaign"] == scaffolded["campaign"]


def test_recorded_fingerprint_passes_gate_and_report_digest_checks(
    tmp_path, engine, monkeypatch
):
    repo = _git_repo(tmp_path)
    _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    scaffolded = _scaffold_state(artifact, repo, monkeypatch)
    _commit(repo, "b.txt")
    _run(artifact, repo)

    stack = _state(artifact)["runtime"]["stack_fingerprint"]
    payload, errors = gates._stack_fingerprint_payload(
        stack, label="state", runtime_profile=_PROFILE
    )
    assert errors == [] and payload is not None
    assert report._verified_stack_payload(stack, _PROFILE) is not None
    # A proof stamped at scaffold time still binds to the refreshed state: the
    # gate compares only fields that stay fixed for a session.
    assert gates._session_stack_identity(payload) == gates._session_stack_identity(
        {k: v for k, v in scaffolded["runtime"]["stack_fingerprint"].items() if k != "digest"}
    )


def test_repo_root_joins_sources_when_it_is_not_the_session_worktree(
    tmp_path, engine, monkeypatch
):
    repo = _git_repo(tmp_path)
    commit = _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    monkeypatch.delenv("AMMO_RUNTIME_SOURCE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    rc, results = _run(artifact, repo)

    assert rc == 0
    assert results["stack_fingerprint"]["sources"][str(repo.resolve())]["commit"] == commit


def test_opt_header_binds_to_cli_proof_when_engine_tree_is_cwd(
    tmp_path, engine, monkeypatch
):
    """Gate 5.3a: the opt header and the runtime_ops.py fingerprint proof agree.

    A Stage 5 track runs from its engine worktree (cwd == --repo-root) while the
    session worktree is AMMO_RUNTIME_SOURCE_DIR. The proof tool cannot add a
    source dir, so the runner must not add its --repo-root (the cwd) to
    ``sources``; the engine HEAD still lands in execution_source.cwd_git. If the
    runner added the cwd to sources, the gate would report an activation/current
    E2E ``sources`` mismatch.
    """
    track = _git_repo(tmp_path)  # engine worktree == cwd == repo_root
    _commit(track, "a.txt")
    session = tmp_path / "session"
    session.mkdir()
    _git(session, "init", "-q")
    _commit(session, "s.txt")
    artifact = _make_artifact(tmp_path)

    monkeypatch.setenv("AMMO_RUNTIME_SOURCE_DIR", str(session))
    monkeypatch.chdir(track)

    rc = bench.main(
        [
            "--artifact-dir", str(artifact),
            "--round", "1",
            "--slot", "opt/op1",
            "--labels", "baseline",
            "--repo-root", str(track),
            "--prompt-source", "raw",
            "--warmup-waves", "1",
        ]
    )
    assert rc == 0
    opt_header = (
        artifact / "rounds" / "1" / "sweeps" / "opt" / "op1" / "e2e_latency_results.json"
    )
    header_stack = json.loads(opt_header.read_text(encoding="utf-8"))["stack_fingerprint"]

    # The documented proof tool builds its fingerprint with no extra source dir.
    proof_stack = runtime_ops.resolve_runtime().stack_fingerprint()

    track_ctx = gates.TrackContext(
        track_id="op1",
        metadata={},
        validation_path=artifact / "v.md",
        validation_text="",
        evidence_path=artifact / "e.json",
        evidence={},
    )
    errors = gates._activation_stack_binding_errors(
        proof_stack=proof_stack,
        track=track_ctx,
        state={},
        artifact_dir=artifact,
        current_round=1,
        runtime_profile=_PROFILE,
    )
    assert errors == []
    # The engine tree HEAD is recorded via cwd_git, so it does not join sources.
    assert (
        header_stack["execution_source"]["cwd_git"]["commit"]
        == _git(track, "rev-parse", "HEAD")
    )
    assert str(track.resolve()) not in header_stack["sources"]


def test_failed_baseline_leaves_state_alone(tmp_path, engine, monkeypatch):
    repo = _git_repo(tmp_path)
    _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    _scaffold_state(artifact, repo, monkeypatch)
    before = (artifact / "state.json").read_text(encoding="utf-8")
    _commit(repo, "b.txt")
    engine["fail"] = "boom"

    rc, results = _run(artifact, repo)

    assert rc == 2
    assert "stack_fingerprint" in results
    assert (artifact / "state.json").read_text(encoding="utf-8") == before


def test_non_baseline_slot_leaves_state_alone(tmp_path, engine, monkeypatch):
    repo = _git_repo(tmp_path)
    _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    _scaffold_state(artifact, repo, monkeypatch)
    before = (artifact / "state.json").read_text(encoding="utf-8")
    _commit(repo, "b.txt")

    rc, results = _run(artifact, repo, slot="profiling")

    assert rc == 0
    assert "stack_fingerprint" in results
    assert (artifact / "state.json").read_text(encoding="utf-8") == before


def test_state_without_runtime_block_is_not_rewritten(tmp_path, engine, monkeypatch):
    repo = _git_repo(tmp_path)
    _commit(repo, "a.txt")
    artifact = _make_artifact(tmp_path)
    before = (artifact / "state.json").read_text(encoding="utf-8")
    monkeypatch.chdir(repo)

    rc, _ = _run(artifact, repo)

    assert rc == 0
    assert (artifact / "state.json").read_text(encoding="utf-8") == before


def test_invalid_server_stack_env_refuses_before_launch(tmp_path, engine, monkeypatch):
    repo = _git_repo(tmp_path)
    artifact = _make_artifact(tmp_path)
    monkeypatch.setenv("AMMO_STACK_FINGERPRINT", "not-json")
    monkeypatch.chdir(repo)

    with pytest.raises(SystemExit, match="stack fingerprint failed"):
        _run(artifact, repo)
