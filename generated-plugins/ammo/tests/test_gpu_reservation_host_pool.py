"""Host-wide GPU lease table (conventions section 5).

One state dir per host, a pool of every physical GPU that nvidia-smi lists,
leases owned by the campaign id, and a reserve that honors the caller's
CUDA_VISIBLE_DEVICES. Discovery is faked with an nvidia-smi stub on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
SCRIPT = _SCRIPTS_DIR / "gpu_reservation.py"

import gpu_reservation  # noqa: E402
from gpu_reservation import ReservationError, reserve  # noqa: E402

CALLER_ENV = (
    "AMMO_GPU_RES_DIR", "CUDA_VISIBLE_DEVICES", "AMMO_RUNTIME_PROFILE",
    "AMMO_ACCELERATOR_KIND", "NEURON_VISIBLE_DEVICES", "NEURON_RT_VISIBLE_CORES",
    "AMMO_ARTIFACT_DIR", "AMMO_SESSION_ID", "CLAUDE_SESSION_ID",
)


@pytest.fixture(autouse=True)
def clean_caller_env(monkeypatch):
    for name in CALLER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(gpu_reservation, "BACKOFF_DELAYS", [0.01, 0.05, 0.1, 0.2, 0.4, 0.8])


def _fake_nvidia_smi(bin_dir: Path, count: int = 4) -> Path:
    """An nvidia-smi that answers only the index query of section 5."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "nvidia-smi"
    lines = "\\n".join(str(i) for i in range(count))
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$*" = "--query-gpu=index --format=csv,noheader" ]; then\n'
        f"  printf '{lines}\\n'; exit 0\n"
        "fi\n"
        'echo "unexpected nvidia-smi args: $*" >&2; exit 2\n'
    )
    stub.chmod(0o755)
    return bin_dir


def _cli_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in CALLER_ENV}
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# State dir
# ---------------------------------------------------------------------------

class TestStateDir:
    def test_default_is_one_host_wide_dir_whatever_the_caller_cvd(self, monkeypatch):
        seen = set()
        for cvd in (None, "0,1", "2,3", ""):
            if cvd is None:
                monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
            else:
                monkeypatch.setenv("CUDA_VISIBLE_DEVICES", cvd)
            seen.add(gpu_reservation._compute_state_dir())
        assert seen == {Path("/tmp/ammo_gpu_res")}

    def test_ammo_gpu_res_dir_still_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AMMO_GPU_RES_DIR", str(tmp_path / "res"))
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
        assert gpu_reservation._compute_state_dir() == tmp_path / "res"


# ---------------------------------------------------------------------------
# Pool discovery
# ---------------------------------------------------------------------------

class TestPoolDiscovery:
    def test_pool_is_every_gpu_the_index_query_lists_even_under_cvd(self, tmp_path, monkeypatch):
        bin_dir = _fake_nvidia_smi(tmp_path / "bin", count=4)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
        assert gpu_reservation._discover_session_gpus() == [0, 1, 2, 3]

    def test_pool_falls_back_to_torch_with_cvd_cleared(self, tmp_path, monkeypatch):
        # No nvidia-smi on PATH. The fake torch reports 3 devices only when the
        # fallback clears CUDA_VISIBLE_DEVICES, so a CVD-relative count fails.
        fake = tmp_path / "site" / "torch"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text(
            "import os\n"
            "class cuda:\n"
            "    @staticmethod\n"
            "    def device_count():\n"
            "        return 1 if 'CUDA_VISIBLE_DEVICES' in os.environ else 3\n"
        )
        monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
        monkeypatch.setenv("PYTHONPATH", str(tmp_path / "site"))
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
        assert gpu_reservation._discover_session_gpus() == [0, 1, 2]

    def test_no_discovery_gives_an_empty_pool(self, tmp_path, monkeypatch):
        fake = tmp_path / "site" / "torch"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("raise ImportError('no torch in this venv')\n")
        monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
        monkeypatch.setenv("PYTHONPATH", str(tmp_path / "site"))
        assert gpu_reservation._discover_session_gpus() == []

    def test_empty_pool_is_not_persisted(self, tmp_path):
        # A host-wide table frozen empty by one GPU-less call would block every campaign.
        state_dir = tmp_path / "res"
        with mock.patch.object(gpu_reservation, "STATE_DIR", state_dir), \
             mock.patch("gpu_reservation._discover_session_gpus", return_value=[]):
            state = gpu_reservation.read_state()
        assert state["gpus"] == {}
        assert not (state_dir / "state.json").exists()

    def test_first_init_never_clobbers_a_table_another_process_wrote(self, tmp_path):
        state_dir = tmp_path / "res"
        lease = {"session_id": "other-campaign", "reserved_at": "2026-10-28T00:00:00Z",
                 "lease_expires": "2099-01-01T00:00:00Z", "command_snippet": "hold"}

        def racing_discovery():
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "state.json").write_text(json.dumps(
                {"gpus": {"0": lease, "1": None}, "gpu_count": 2, "audit": []}))
            return [0, 1]

        with mock.patch.object(gpu_reservation, "STATE_DIR", state_dir), \
             mock.patch("gpu_reservation._discover_session_gpus", side_effect=racing_discovery):
            state = gpu_reservation.read_state()
        assert state["gpus"]["0"]["session_id"] == "other-campaign"
        on_disk = json.loads((state_dir / "state.json").read_text())
        assert on_disk["gpus"]["0"]["session_id"] == "other-campaign"


# ---------------------------------------------------------------------------
# Caller CUDA_VISIBLE_DEVICES filter
# ---------------------------------------------------------------------------

@pytest.fixture
def host_pool(tmp_path):
    state_dir = tmp_path / "res"
    with mock.patch.object(gpu_reservation, "STATE_DIR", state_dir), \
         mock.patch("gpu_reservation._discover_session_gpus", return_value=[0, 1, 2, 3]):
        yield state_dir


class TestCallerVisibleFilter:
    def test_reserve_under_cvd_2_3_returns_only_2_and_3(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
        assert reserve(1, "camp-a", auto_release=False) == [2]
        assert reserve(1, "camp-b", auto_release=False) == [3]
        with pytest.raises(ReservationError):
            reserve(1, "camp-c", auto_release=False)
        # GPUs 0 and 1 stay free for callers that may see them.
        monkeypatch.delenv("CUDA_VISIBLE_DEVICES")
        assert reserve(2, "camp-d", auto_release=False) == [0, 1]

    def test_reserve_two_under_cvd_2_3(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "3,2")
        assert reserve(2, "camp-a") == [2, 3]

    def test_cvd_filter_skips_a_gpu_another_campaign_holds(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2")
        assert reserve(1, "camp-a") == [2]
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
        assert reserve(1, "camp-b") == [3]

    def test_cvd_without_integer_ids_reserves_nothing(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "-1")
        with pytest.raises(ReservationError):
            reserve(1, "camp-a")

    def test_empty_cvd_is_no_filter(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        assert reserve(1, "camp-a") == [0]

    def test_cvd_outside_the_pool_reserves_nothing(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "7")
        with pytest.raises(ReservationError):
            reserve(1, "camp-a")

    def test_session_resources_are_the_pool_seen_through_cvd(self, host_pool, monkeypatch):
        assert gpu_reservation._discover_session_resources() == [0, 1, 2, 3]
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1,3")
        assert gpu_reservation._discover_session_resources() == [1, 3]

    def test_state_table_keeps_the_whole_host_pool(self, host_pool, monkeypatch):
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2,3")
        reserve(1, "camp-a")
        state = json.loads((host_pool / "state.json").read_text())
        assert sorted(map(int, state["gpus"])) == [0, 1, 2, 3]
        assert state["gpu_count"] == 4


# ---------------------------------------------------------------------------
# Two campaigns, one shared dir
# ---------------------------------------------------------------------------

class TestTwoCampaigns:
    def test_two_campaign_ids_never_hold_one_gpu(self, host_pool, monkeypatch):
        for cvd_a, cvd_b in ((None, None), ("0,1,2", "1,2,3"), ("0,1,2,3", None)):
            for name, cvd in (("A", cvd_a), ("B", cvd_b)):
                if cvd is None:
                    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
                else:
                    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", cvd)
                reserve(2, f"campaign-{name}")  # auto-release: each retry frees only its own
            state = json.loads((host_pool / "state.json").read_text())
            owners = [e["session_id"] for e in state["gpus"].values() if e]
            assert sorted(owners) == ["campaign-A"] * 2 + ["campaign-B"] * 2
            gpu_reservation.release_by_session("campaign-A")
            gpu_reservation.release_by_session("campaign-B")

    def test_concurrent_cli_reserves_from_two_campaigns_are_disjoint(self, tmp_path):
        bin_dir = _fake_nvidia_smi(tmp_path / "bin", count=4)
        state_dir = tmp_path / "shared-res"
        base = dict(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", AMMO_GPU_RES_DIR=str(state_dir))
        callers = [
            ("20261028-000000-aaaaaa", None),
            ("20261028-000000-aaaaaa", "0,1,2,3"),
            ("20261028-000000-bbbbbb", None),
            ("20261028-000000-bbbbbb", "3,2,1,0"),
        ]
        procs = []
        for cid, cvd in callers:
            extra = dict(base)
            if cvd is not None:
                extra["CUDA_VISIBLE_DEVICES"] = cvd
            procs.append(subprocess.Popen(
                [sys.executable, str(SCRIPT), "reserve", "--num-gpus", "1",
                 "--session-id", cid, "--no-auto-release"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=_cli_env(**extra), cwd=tmp_path))
        got = []
        for proc in procs:
            out, err = proc.communicate(timeout=30)
            assert proc.returncode == 0, err
            got.append(int(out.strip()))
        assert sorted(got) == [0, 1, 2, 3]
        state = json.loads((state_dir / "state.json").read_text())
        owners = sorted(e["session_id"] for e in state["gpus"].values() if e)
        assert owners == [callers[0][0]] * 2 + [callers[2][0]] * 2

    def test_cli_reserve_under_cvd_prints_a_physical_id_from_cvd(self, tmp_path):
        bin_dir = _fake_nvidia_smi(tmp_path / "bin", count=4)
        env = _cli_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                       AMMO_GPU_RES_DIR=str(tmp_path / "res"), CUDA_VISIBLE_DEVICES="2,3")
        out = []
        for cid in ("camp-a", "camp-b"):
            r = subprocess.run([sys.executable, str(SCRIPT), "reserve", "--num-gpus", "1",
                                "--session-id", cid], capture_output=True, text=True, env=env,
                               cwd=tmp_path, timeout=30)
            assert r.returncode == 0, r.stderr
            out.append(r.stdout.strip())
        assert out == ["2", "3"]


# ---------------------------------------------------------------------------
# Campaign id and the bare CLI
# ---------------------------------------------------------------------------

class TestCampaignCli:
    def test_default_owner_is_the_marker_campaign_id(self, tmp_path):
        engine = tmp_path / "engine"
        cid = "20261028-120000-abc123"
        (engine / ".ammo" / cid).mkdir(parents=True)
        (engine / ".ammo" / "active").write_text(cid + "\n")
        state_dir = tmp_path / "res"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(json.dumps(
            {"gpus": {"0": None, "1": None}, "gpu_count": 2, "audit": []}))
        r = subprocess.run([sys.executable, str(SCRIPT), "reserve", "--num-gpus", "1"],
                           capture_output=True, text=True, cwd=engine, timeout=30,
                           env=_cli_env(AMMO_GPU_RES_DIR=str(state_dir)))
        assert r.returncode == 0, r.stderr
        state = json.loads((state_dir / "state.json").read_text())
        assert state["gpus"]["0"]["session_id"] == cid

    def test_status_table_runs_with_no_env(self, tmp_path):
        # No AMMO_* names, no CVD, no GPU tool: prints an empty table or the
        # host's current leases, and exits 0.
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        r = subprocess.run([sys.executable, str(SCRIPT), "status", "--table"],
                           capture_output=True, text=True, cwd=tmp_path, timeout=60,
                           env={"PATH": str(bin_dir), "HOME": str(tmp_path)})
        assert r.returncode == 0, r.stderr
        assert "No GPUs tracked in state." in r.stdout or r.stdout.startswith("GPU")
