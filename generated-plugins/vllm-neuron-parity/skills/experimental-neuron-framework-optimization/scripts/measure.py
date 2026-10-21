#!/usr/bin/env python3
"""measure.py — deterministic local HW measure oracle for the NKI optimize loop.

Port of the Pi orchestrator's hw.mjs `measure()` to a plain, LLM-free script
that runs on THIS node (real Neuron devices, no brazil, no shared fleet). It is
the integrity anchor of the loop: pass/fail and the cycle count are decided by
CODE from raw pytest evidence, never by an agent's judgment (design decision
D19 in docs/optimize_loop_v2_beam.md — an LLM once reported a numerically-wrong
kernel as "passed").

Contract (unchanged from the fleet version, only the run command differs):
  * wipe the out-dir first, so cycles can never come from a stale file;
  * run the test in compile-and-infer mode on the LOCAL device
    (the harness auto-selects HostProvisioningMode.LOCAL when neuron-ls sees
    devices — pytest_plugin.py);
  * verdict: passed iff  pytest reported >=1 passed AND 0 failed AND 0 errored
    AND rc == 0;
  * cycles: a ROBUST reduction (skill-owned cycle_stats, a clone of the harness reducer) over
    EVERY <out>/out-*/infer_result/show_session_*.json the run emitted -- one file per profiled
    execution. Per NeuronCore its per-exec CycleCounts are IQR-outlier-filtered and averaged; the
    kernel value is the MAX across cores (slowest core bounds the step). The on-device CycleCount
    is a real profiled execution and varies run-to-run, so a single sample (or a max over one
    exec) can read many percent off -- a lone low draw is a phantom win, a lone high draw hides a
    real one. IQR-filtered average over N execs rejects those outliers. Trusted ONLY when passed.
    Enable multi-exec profiling (profile_all_runs -> the run profiles all execs, N traces from ONE
    bring-up) to feed the reducer N samples; with a single-exec profile it degrades to one sample.

Emits a single JSON line to stdout:
  {"passed": bool, "cycles": int|null (max-core IQR-avg), "n_samples": int,
   "cycles_min": int|null, "cycles_max": int|null, "cycles_spread": float|null ((max-min)/cycles),
   "qcd_max": float|null (worst per-core IQR/median %), "per_core": {...},
   "rc": int, "summary": str, "reason": str, "show_session": str|null}

Exit code mirrors the verdict for shell convenience: 0 iff passed.

Usage:
  python3 measure.py --repo <REPO> --node '<file::Cls::method[bracket]>' \
      --target trn2 [--out-dir neuron_test_output] [--k <filter>] \
      [--shadow /tmp/nkilib_shadow] [--timeout-s 1200] [--log /tmp/m.log]
  # multi-exec profiling (profile_all_runs) makes ONE run emit N show_session files -> N samples;
  # inspect cycles_spread / qcd_max: if they exceed the accept margin the win is unresolvable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cycle_stats  # skill-owned robust reducer (IQR-filtered avg over all show_session_*.json)


def build_shadow(repo: Path, shadow: Path) -> None:
    """Create/refresh the wheel-shadow so the local test/utils wins over the
    stale installed nkilib_testing wheel (verified requirement on this node)."""
    shadow.mkdir(parents=True, exist_ok=True)
    link = shadow / "nkilib_testing"
    src = repo / "test" / "utils"
    if link.is_symlink() or link.exists():
        try:
            link.unlink()
        except IsADirectoryError:
            shutil.rmtree(link)
    link.symlink_to(src, target_is_directory=True)


def max_cycle_count(show_session_path: Path):
    """Max CycleCount across NeffNodes[*].NodeInfo.Graphs[*] — the scorer's field."""
    try:
        j = json.loads(show_session_path.read_text())
    except Exception:
        return None
    mx = None
    for n in j.get("NeffNodes", j.get("neff_nodes", []) or []):
        ni = n.get("NodeInfo") or n.get("node_info") or {}
        for g in (ni.get("Graphs") or ni.get("graphs") or []):
            c = g.get("CycleCount", g.get("cycle_count"))
            if isinstance(c, (int, float)) and (mx is None or c > mx):
                mx = c
    return mx


def find_show_session(repo: Path, out_dir: str):
    base = repo / out_dir
    if not base.exists():
        return None
    for d in sorted(base.iterdir()):
        if not d.name.startswith("out-"):
            continue
        p = d / "infer_result" / "show_session_0.json"
        if p.exists():
            return p
    return None


def last_pytest_summary(out: str) -> str:
    lines = [l for l in out.splitlines() if re.search(r"\d+ (passed|failed|error)", l)]
    return re.sub(r"=+", "", lines[-1]).strip() if lines else ""


def _num(s: str, pat: str) -> int:
    m = re.search(pat, s or "")
    return int(m.group(1)) if m else 0


def failure_reason(out: str, rc: int, summary: str) -> str:
    lines = [re.sub(r"=+", "", l).strip() for l in out.splitlines()]
    lines = [l for l in lines if l]

    def find(pat):
        for l in reversed(lines):
            if re.search(pat, l):
                return l
        return ""
    return (
        find(r"\bno tests ran\b|\bno tests collected\b|\d+ deselected|\d+ selected")
        or summary
        or find(r"ERROR|Traceback|AttributeError|BUILD FAILED|failed to compile")
        or (lines[-1] if lines else f"rc={rc}")
    )


def measure(repo: Path, node: str, target: str, out_dir: str, kfilter: str | None,
            shadow: Path | None, timeout_s: int, log: Path | None) -> dict:
    # one bring-up; reduce across ALL show_session_*.json via cycle_stats (D19: cycles can't be stale)
    if shadow is not None:
        build_shadow(repo, shadow)
    env = dict(os.environ)
    pp = [str(shadow)] if shadow is not None else []
    pp.append(str(repo / "src"))
    pp.append(os.path.dirname(os.path.abspath(__file__)))   # so -p multiexec_profile_plugin imports
    if env.get("PYTHONPATH"):
        pp.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(pp)

    cmd = [
        sys.executable, "-m", "pytest", "-c", "test/pytest.ini",
        "-p", "multiexec_profile_plugin",       # one bring-up profiles ALL execs -> N samples
        node,
        "--platform-target", target, "--test-mode", "compile-and-infer",
        "--output-directory", out_dir, "-p", "no:cacheprovider", "-q",
    ]
    if kfilter:
        cmd += ["-k", kfilter]

    shutil.rmtree(repo / out_dir, ignore_errors=True)   # D19: cycles can never be stale
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                              text=True, timeout=timeout_s)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        out += "\n[measure] TIMEOUT after %ds" % timeout_s
        rc = 124

    if log is not None:
        try:
            log.write_text(out)
        except Exception:
            pass

    summary = last_pytest_summary(out)
    passed_n = _num(summary, r"(\d+) passed")
    failed_n = _num(summary, r"(\d+) failed")
    errored_n = _num(summary, r"(\d+) error")
    passed = passed_n >= 1 and failed_n == 0 and errored_n == 0 and rc == 0

    # Robust reduction: pool CycleCount across ALL show_session_*.json this run emitted (one per
    # profiled exec) and IQR-filter-average per core, then take the max-core value (cycle_stats,
    # a skill-owned clone of the harness reducer). With multi-exec profiling this is one bring-up,
    # N samples; a lone run-to-run outlier cannot be banked as a win. Falls back to a single
    # sample when only one exec was profiled.
    files = cycle_stats.find_show_sessions(repo, out_dir) if passed else []
    stats = cycle_stats.robust_cycle_count(files) if files else {}
    cycles = stats.get("cycles") if passed else None
    reason = "" if (passed and cycles) else failure_reason(out, rc, summary)

    return {
        "passed": bool(passed),
        "cycles": cycles,                              # max-core IQR-filtered average
        "n_samples": stats.get("n_samples", 0),
        "cycles_min": stats.get("cycles_min"),
        "cycles_max": stats.get("cycles_max"),
        "cycles_spread": stats.get("cycles_spread"),   # (max-min)/cycles across all samples
        "qcd_max": stats.get("qcd_max"),               # worst per-core dispersion (IQR/median %)
        "per_core": stats.get("per_core", {}),
        "rc": rc,
        "summary": summary,
        "reason": reason[:400],
        "show_session": str(files[-1]) if files else None,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True, help="KNKL repo root (cwd for pytest)")
    p.add_argument("--node", required=True, help="pytest node id (file::Cls::method[bracket])")
    p.add_argument("--target", default="trn2")
    p.add_argument("--out-dir", default="neuron_test_output")
    p.add_argument("--k", default=None, help="optional -k filter")
    p.add_argument("--shadow", default="/tmp/nkilib_shadow",
                   help="wheel-shadow dir (symlinks nkilib_testing->test/utils); '' to disable")
    p.add_argument("--timeout-s", type=int, default=1200)
    p.add_argument("--log", default=None, help="write full pytest output here")
    args = p.parse_args(argv)

    shadow = Path(args.shadow) if args.shadow else None
    res = measure(Path(args.repo).resolve(), args.node, args.target, args.out_dir,
                  args.k, shadow, args.timeout_s,
                  Path(args.log) if args.log else None)
    print(json.dumps(res))
    return 0 if res["passed"] and res["cycles"] else 1


if __name__ == "__main__":
    sys.exit(main())
