"""hardware_queue.py: the only writer of lease records. Grants fit remaining
capacity under a per-host lock; zero-reservation jobs are refused a lease;
oversize requests are defects; leaked grants are reaped once the job record
appears; a race for the last unit yields exactly one winner."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "hardware_queue.py"


def run(*args):
    p = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
    return p.returncode, out


class HardwareQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.roster = self.root / "roster.json"
        self.roster.write_text(json.dumps({"hosts": {"h1": {"target_class": "trn2", "pools": {
            "neuron_devices": 2, "compile_memory_gib": 100, "compile_cache_write_slot": 1, "scratch_disk": None}}}}))

    def tearDown(self):
        self.tmp.cleanup()

    def grant(self, campaign, job=None, *pools, extra=()):
        args = ["grant", "--root", str(self.root), "--roster", str(self.roster), "--host", "h1", "--campaign", campaign]
        if job:
            args += ["--job", job]
        for p in pools:
            args += ["--pool", p]
        return run(*args, *extra)

    def test_campaign_lease_reserves_nothing_and_jobs_fit_capacity(self):
        rc, out = self.grant("A", extra=("--markers-verified", "instance,hostname"))
        self.assertEqual(rc, 0); self.assertEqual(out["kind"], "campaign")
        rc, a = self.grant("A", "j1", "neuron_devices=2"); self.assertEqual(rc, 0)
        self.assertEqual(a["remaining"]["neuron_devices"], 0)
        rc, b = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 3, b)
        rc, _ = run("release", "--root", str(self.root), "--host", "h1", "--campaign", "A", "--job", "j1"); self.assertEqual(rc, 0)
        rc, b = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 0, b)
        rc, c = self.grant("C", "j3", "neuron_devices=1", "compile_memory_gib=60"); self.assertEqual(rc, 0, c)
        rc, d = self.grant("D", "j4", "compile_memory_gib=50"); self.assertEqual(rc, 3, d)

    def test_request_defects(self):
        rc, out = self.grant("A", "j1", "neuron_devices=3"); self.assertEqual(rc, 2); self.assertIn("oversize", out["reason"])
        rc, out = self.grant("A", "j1", "gpus=1"); self.assertEqual(rc, 2)
        rc, out = self.grant("A", "j1"); self.assertEqual(rc, 2); self.assertIn("holds no job lease", out["reason"])
        rc, out = run("release", "--root", str(self.root), "--host", "h1", "--campaign", "A", "--job", "nope"); self.assertEqual(rc, 2)
        rc, out = run("grant", "--root", str(self.root), "--roster", str(self.roster), "--host", "h9", "--campaign", "A"); self.assertEqual(rc, 2)

    def test_unsized_pool_is_indivisible(self):
        rc, _ = self.grant("A", "j1", "scratch_disk=1"); self.assertEqual(rc, 0)
        rc, out = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 3, out)
        rc, _ = run("release", "--root", str(self.root), "--host", "h1", "--campaign", "A", "--job", "j1")
        rc, _ = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 0)
        rc, out = self.grant("C", "j3", "scratch_disk=1"); self.assertEqual(rc, 3, out)

    def test_leaked_grant_is_reaped_only_when_job_record_closes(self):
        rec = self.root / "campaigns" / "A" / "attempts" / "attempt-j1.json"
        rc, _ = self.grant("A", "j1", "neuron_devices=2", extra=("--job-record", str(rec))); self.assertEqual(rc, 0)
        rc, out = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 3)
        rec.parent.mkdir(parents=True, exist_ok=True); rec.write_text(json.dumps({"attempt": "j1", "started_at": "x"}))
        rc, out = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 3, "an open record must not reap the live lease")
        rec.write_text(json.dumps({"attempt": "j1", "outcome": "attempt_passed"}))
        rc, out = self.grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 0, out)
        events = [json.loads(p.read_text()) for p in (self.root / "campaigns" / "A" / "attempts" / "leases").glob("*.json")]
        self.assertTrue(any(e["event"] == "release" and e["reason"].startswith("reaped") for e in events))

    def test_roster_that_sizes_no_pools_is_one_job_per_host(self):
        bare = self.root / "run-state.json"  # the run state's roster, no pools sized
        bare.write_text(json.dumps({"instance_roster": [{"instance": "h1", "target_class": "trn2"}]}))
        def grant(c, j, *pools):
            return run("grant", "--root", str(self.root), "--roster", str(bare), "--host", "h1", "--campaign", c, "--job", j, *sum([["--pool", p] for p in pools], []))
        rc, out = grant("A", "j1", "neuron_devices=1"); self.assertEqual(rc, 0, out)
        rc, out = grant("B", "j2", "neuron_devices=1"); self.assertEqual(rc, 3, out)
        rc, out = grant("B", "j2", "cpu_cores=1"); self.assertEqual(rc, 3, out)
        rc, out = grant("B", "j2", "neuron_devices=2"); self.assertEqual(rc, 2, out)
        rc, out = grant("B", "j2", "gpus=1"); self.assertEqual(rc, 2, out)

    def test_amend_applies_to_every_campaign_lease_on_the_host(self):
        self.grant("A"); self.grant("B")
        rc, out = run("amend", "--root", str(self.root), "--host", "h1", "--boot-identifier", "boot-2")
        self.assertEqual(rc, 0); self.assertEqual(sorted(x["campaign"] for x in out["applies_to"]), ["A", "B"])

    def test_race_for_the_last_units_has_exactly_two_winners(self):
        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(lambda i: self.grant(f"C{i}", f"j{i}", "neuron_devices=1")[0], range(8)))
        self.assertEqual(results.count(0), 2, results); self.assertEqual(results.count(3), 6, results)
        rc, st = run("status", "--root", str(self.root), "--roster", str(self.roster))
        self.assertEqual(st["hosts"]["h1"]["remaining"]["neuron_devices"], 0)


if __name__ == "__main__":
    unittest.main()
