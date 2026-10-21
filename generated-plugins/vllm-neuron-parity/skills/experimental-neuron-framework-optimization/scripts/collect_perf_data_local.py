#!/usr/bin/env python3.12
"""collect_perf_data_local.py -- brazil-free, fleet-free Phase-0 collection for the kernel loop.

Reproduces what brazil's `integration-test-separated --debug-kernels --enable-perf-analysis` used
to produce (`qor_separated_analysis.csv` + `stalls.json` + BIR + profiler_db + analysis_nc*.log),
but using ONLY the installed `nkilib_testing` wheel (the same harness `measure.py` drives) plus the
two post-processors vendored next to this script. NO brazil-build, NO shared fleet, NO
KaenaNeuronKernelLibrary source checkout.

Mechanism (mirrors build-tools/bin/custom-build's `integration-test-separated`, minus run_test.sh):
  Pass A (unseparated, +--debug-kernels): pytest compile-and-infer -> BIR + metrics + profiler NTFF
  Pass B (separated, NKILIB_ENABLE_SEPARATION_ANALYSIS=1): pytest -> SeparatedCompute/MemoryTime
  compute_sch_ineff_private.py A B -> qor_separated_analysis.csv   (unlocks separation/roofline/sharding)
  neuron-explorer ingest NTFF+NEFF -> profiler_db                  (region specialists)
  dma_compute_order_check.py <scheduled BIR> --profile profiler_db -> stalls.json (unlocks dma-order)

Both passes run with NKILIB_QOR_BRIDGE=1 so the staged-tree conftest emits harness metrics and
collects them into qor_data_*.csv (the checkout's conftest normally does this).

Emits one JSON line and writes <perf_data_dir>/manifest.json:
  {"ok":bool,"perf_data_dir":str,"artifacts_present":{profiler_db,qor_separated,stalls,bir,ntff,analysis_logs}}
Point analyze_local.py --perf-data-path at <perf_data_dir> to dispatch the full specialist set.
Exit 0 iff at least profiler_db + qor_separated were produced (stalls best-effort).
"""
from __future__ import annotations
import argparse, glob, json, os, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
COMPUTE_SCH = VENDOR / "compute_sch_ineff_private.py"
DMA_ORDER = VENDOR / "dma_compute_order_check.py"


def _run_pytest_pass(repo: Path, node: str, target: str, out_dir: str, separated: bool,
                     timeout_s: int, log: Path) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = ":".join([str(repo / "src")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["NKILIB_QOR_BRIDGE"] = "1"                      # arm the conftest metrics->qor_data bridge
    if separated:
        env["NKILIB_ENABLE_SEPARATION_ANALYSIS"] = "1"
    else:
        env.pop("NKILIB_ENABLE_SEPARATION_ANALYSIS", None)
    cmd = [sys.executable, "-m", "pytest", "-c", "test/pytest.ini", node,
           "--platform-target", target, "--test-mode", "compile-and-infer",
           "--metric-output", "file", "--enable-perf-analysis",
           "--output-directory", out_dir, "-p", "no:cacheprovider", "-q"]
    if not separated:
        cmd.insert(cmd.index("--metric-output"), "--debug-kernels")   # BIR only needed once
    shutil.rmtree(repo / out_dir, ignore_errors=True)
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=timeout_s)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or "") if isinstance(e.stdout, str) else "") + f"\n[collect] TIMEOUT {timeout_s}s"
        rc = 124
    log.write_text(out)
    passed = (" passed" in out.splitlines()[-1] if out.strip() else False) and rc == 0
    return rc if not passed else 0


def _ingest_profiler_db(repo: Path, unsep_out: str) -> str | None:
    explorer = shutil.which("neuron-explorer")
    if explorer is None:
        return None
    base = repo / unsep_out
    for d in sorted((p for p in base.glob("out-*") if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        ir = d / "infer_result"
        neff = d / "file.neff"
        if neff.exists() and ir.is_dir() and any(ir.glob("*.ntff")):
            db = ir / "profiler_db"
            if not (db / "Instruction.parquet").exists():
                db.mkdir(parents=True, exist_ok=True)
                subprocess.run([explorer, "view", "--ingest-only", "-d", str(ir),
                                "--neff-path", str(neff), "--output-format", "parquet",
                                "--output-file", str(db) + "/"],
                               capture_output=True, text=True, timeout=600)
            if (db / "Instruction.parquet").exists():
                return str(db)
    return None


def _find_scheduled_bir(repo: Path, unsep_out: str) -> str | None:
    """Pick a post-scheduler BIR JSON (instruction stream after scheduling, before codegen)."""
    jsons = list((repo / unsep_out).glob("out-*/artifacts/nc*/sg*/bir_debug.*.json"))
    if not jsons:
        return None
    # prefer the explicit post-schedule pass; else the highest-index json.
    for pref in ("after-prefetch_scheduling_after_sched", "after-lower_control", "after-perf_sim"):
        hit = [p for p in jsons if pref in p.name]
        if hit:
            return str(sorted(hit)[0])
    def _idx(p):
        try:
            return int(p.name.split(".")[1])
        except Exception:
            return -1
    return str(max(jsons, key=_idx))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="staged decoupled tree (stage_kernel_tree.py output)")
    ap.add_argument("--node", required=True, help="pytest node id")
    ap.add_argument("--target", default="trn2")
    ap.add_argument("--run-id", default="local")
    ap.add_argument("--out-base", default="neuron_test_output/perf_data",
                    help="base dir (under --repo) for the assembled perf_data/<run-id>/")
    ap.add_argument("--timeout-s", type=int, default=1800)
    a = ap.parse_args(argv)

    repo = Path(a.repo).resolve()
    logdir = repo / a.out_base / a.run_id
    logdir.mkdir(parents=True, exist_ok=True)
    unsep, sep = "neuron_test_output_unseparated", "neuron_test_output_separated"

    print(f"[collect] Pass A (unseparated, --debug-kernels) ...", flush=True)
    rcA = _run_pytest_pass(repo, a.node, a.target, unsep, False, a.timeout_s, logdir / "passA.log")
    print(f"[collect] Pass B (separated) ...", flush=True)
    rcB = _run_pytest_pass(repo, a.node, a.target, sep, True, a.timeout_s, logdir / "passB.log")

    artifacts = {"profiler_db": False, "qor_separated": False, "stalls": False,
                 "bir": False, "ntff": False, "analysis_logs": False}

    # QoR CSV from the two runs
    qor_csv = logdir / "qor_separated_analysis.csv"
    r = subprocess.run([sys.executable, str(COMPUTE_SCH), str(repo / unsep), str(repo / sep), str(qor_csv)],
                       capture_output=True, text=True)
    (logdir / "compute_sch.log").write_text((r.stdout or "") + "\n" + (r.stderr or ""))
    if qor_csv.exists() and qor_csv.stat().st_size > 0 and sum(1 for _ in open(qor_csv)) > 1:
        artifacts["qor_separated"] = True

    # profiler_db (ingest the unseparated run's NTFF+NEFF)
    db = _ingest_profiler_db(repo, unsep)
    if db:
        artifacts["profiler_db"] = True
        artifacts["ntff"] = True
        shutil.copytree(db, logdir / "profiler_db", dirs_exist_ok=True)

    # stalls.json from the scheduled BIR + profiler_db
    bir = _find_scheduled_bir(repo, unsep)
    if bir:
        artifacts["bir"] = True
        if db:
            stalls = logdir / "stalls.json"
            r = subprocess.run([sys.executable, str(DMA_ORDER), bir, "--profile", str(logdir / "profiler_db"),
                                "--json-output", str(stalls)], capture_output=True, text=True)
            (logdir / "dma_order.log").write_text((r.stdout or "") + "\n" + (r.stderr or ""))
            if stalls.exists() and stalls.stat().st_size > 0:
                artifacts["stalls"] = True

    # analysis_nc*.log (per-instruction analysis from --enable-perf-analysis)
    for lg in (repo / unsep).glob("out-*/analysis_nc*.log"):
        shutil.copy2(lg, logdir / lg.name)
        artifacts["analysis_logs"] = True

    ok = artifacts["profiler_db"] and artifacts["qor_separated"]
    manifest = {"ok": ok, "perf_data_dir": str(logdir), "run_id": a.run_id,
                "artifacts_present": artifacts, "passA_rc": rcA, "passB_rc": rcB,
                "node": a.node, "target": a.target, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (logdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
