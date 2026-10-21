#!/usr/bin/env python3
"""analyze_local.py — local, fleet-free Phase-0 for the NKI optimize loop.

Brazil is never available, so there is NO brazil path here. analyze reproduces ONLY the
deterministic, code-side part that the Kiro orchestrator needs to decide WHICH specialists to
dispatch and WHAT context to hand them — the LLM fan-out itself is done by Kiro (subagents),
not here.

It does two things, both pure code, no LLM:

1. COLLECT (two artifacts, both REQUIRED; no brazil, no source-only):
   a. profiler_db (the device profile) — REQUIRED. Resolve it from EITHER:
      * an explicit --perf-data-path (the job's `collect.profiler_db` INPUT — the Stage-3
        ingested profiler_db in autobridge, or a supplied capture), OR
      * the profile measure.py left on disk (out-*/file.neff + out-*/infer_result/*.ntff),
        INGESTED with neuron-explorer into a profiler_db.
      If NO profiler_db can be resolved, this FAILS (non-zero). There is NO source-only fallback.
   b. qor data (qor_separated + stalls) — ALWAYS GENERATED here by the internal compiler run
      collect_perf_data_local.py (two separated/unseparated compile-and-infer passes on the
      installed nkilib_testing wheel, brazil-free). qor is an internal compiler artifact absent
      from an ingested profiler_db, so it is regenerated every run — never an input, never
      optional, no on/off flag.

2. GATE + CONTEXT: emit the artifact-present gate the specialist table keys on
   (qor_separated / stalls / profiler_db) plus a "dispatch context" block the
   orchestrator pastes into each specialist prompt:
     - perf_data_path,
     - kernel_under_test (resolved from the test node's kernel import),
     - test_name, target,
     - measured_cycles (from measure.py, if the caller passed one).

The profile is MANDATORY and analysis is ALWAYS full: with a profiler_db present
every region specialist (bottleneck/gap/offload/efficiency) dispatches alongside
cost+compute, and the qor/stalls specialists (separation/roofline/dma-order/
sharding) dispatch when their artifacts are present. A missing profile is a HARD
FAILURE, never a quieter analysis.

Emits one JSON line on success:
  {"mode": "collected", "perf_data_path": str, "artifacts_present": {...},
   "kernel_under_test": str, "test_name": str, "target": str,
   "measured_cycles": int|null, "dispatch_specialists": [type,...],
   "dispatch_context": str}
On a missing profile it prints an "error"/"mode":"FAILED_NO_PROFILE" JSON to
stderr and exits non-zero.
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

# specialist table (mirrors analyze.mjs SPECIALISTS: type -> required artifact).
# requires=None -> source-driven, always applicable. The profile is mandatory (a missing
# profiler_db is a hard failure upstream), so the region specialists always dispatch too.
SPECIALISTS = [
    ("nki-separation-specialist", "qor_separated", "kernel"),
    ("nki-roofline-specialist", "qor_separated", "kernel"),
    ("nki-dma-order-specialist", "stalls", "kernel"),
    ("nki-cost-specialist", None, "kernel"),
    ("nki-compute-specialist", None, "kernel"),
    ("nki-sharding-specialist", "qor_separated", "kernel"),
    ("nki-bottleneck-specialist", "profiler_db", "region"),
    ("nki-gap-specialist", "profiler_db", "region"),
    ("nki-offload-specialist", "profiler_db", "region"),
    ("nki-efficiency-specialist", "profiler_db", "region"),
]


def applicable(artifacts: dict, region_analysis: bool) -> list[str]:
    out = []
    for typ, req, scope in SPECIALISTS:
        if scope == "region" and not region_analysis:
            continue
        if req is None or artifacts.get(req) is True:
            out.append(typ)
    return out


def resolve_kernel_path(repo: Path, node: str) -> str:
    """Best-effort: map a test node id to its kernel source file. Reads the test
    file's `from nkilib... import` of the kernel under test; falls back to the
    core/<area> mirror. Honest 'unknown' if not resolvable."""
    m = re.match(r"([^:]+\.py)::", node)
    if not m:
        return "unknown"
    test_file = repo / m.group(1)
    if not test_file.exists():
        return "unknown"
    try:
        txt = test_file.read_text()
    except Exception:
        return "unknown"
    # find an import from nkilib_src that looks like the KERNEL module (skip utils/enums/generators).
    _SKIP = ("utils", "common_types", "tensor_generator", "kernel_assert", "logging", "allocator")
    for mm in re.finditer(r"from\s+(nkilib(?:_src\.nkilib)?[\w.]+)\s+import", txt):
        mod = mm.group(1).replace("nkilib_src.", "")
        if any(s in mod for s in _SKIP):
            continue
        rel = mod.replace(".", "/") + ".py"
        for base in ("src/nkilib_src", "src"):
            cand = repo / base / rel
            if cand.exists():
                return str(cand.relative_to(repo))
    return "unknown"


def run_internal_qor_collect(repo: Path, node: str, target: str) -> tuple[str | None, dict]:
    """ALWAYS-run, brazil-free qor/stalls generator — the internal compiler run.

    Invokes collect_perf_data_local.py (two separated/unseparated compile-and-infer passes on the
    installed nkilib_testing wheel + the vendored post-processors) to produce
    qor_separated_analysis.csv (unlocks separation/roofline/sharding) and stalls.json (unlocks
    dma-order), plus its own profiler_db. Returns (perf_data_dir | None, artifacts_present).

    qor is an internal compiler artifact absent from an ingested profiler_db, so it is generated
    every run — never brazil, never optional. Never raises; on failure returns (None, {}) and the
    qor/stalls specialists simply do not open (the profiler_db still drives cost/compute + region).
    """
    script = Path(__file__).resolve().parent / "collect_perf_data_local.py"
    if not script.exists():
        return None, {}
    cmd = [sys.executable, str(script), "--repo", str(repo), "--node", node, "--target", target]
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=3600)
    except Exception:
        return None, {}
    # collect_perf_data_local.py prints its manifest JSON as the last stdout line.
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                man = json.loads(line)
                return man.get("perf_data_dir"), (man.get("artifacts_present") or {})
            except Exception:
                continue
    return None, {}


def try_local_ntff_ingest(repo: Path, measure_out_dir: str) -> tuple[str | None, dict]:
    """Ingest the device profile measure.py ALREADY captured on this node.

    measure.py runs the kernel unit test in compile-and-infer mode, which leaves a
    device NTFF (``<out-*>/infer_result/profile_exec_*.ntff``) and the matching NEFF
    (``<out-*>/file.neff``) on disk. Ingest them with neuron-explorer into a
    ``profiler_db`` (parquet, incl. ``Instruction.parquet`` with ``nki_source_location``)
    so analyze runs in FULL mode WITHOUT brazil and WITHOUT a redundant capture. The baseline
    measure always leaves a device capture, so this is the normal path; the profile is mandatory
    and a genuinely absent profile is a hard failure upstream (never a source-only fallback).

    Returns ``(profiler_db_path, artifacts)`` or ``(None, {})`` when there is nothing to
    ingest (no run dir / no NEFF+NTFF) or neuron-explorer is unavailable. Never raises.
    """
    explorer = shutil.which("neuron-explorer")
    if explorer is None:
        return None, {}
    base = repo / measure_out_dir
    if not base.exists():
        return None, {}
    # measure.py wipes + writes ONE out-* per run; pick the most recent that has both a
    # NEFF (for correlation) and an NTFF (the device trace).
    cand = None
    for d in sorted((p for p in base.glob("out-*") if p.is_dir()),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        ir = d / "infer_result"
        if (d / "file.neff").exists() and ir.is_dir() and any(ir.glob("*.ntff")):
            cand = d
            break
    if cand is None:
        return None, {}
    ir = cand / "infer_result"
    db = ir / "profiler_db"
    # Reuse a prior ingest if present; else run neuron-explorer (bounded). The NEFF MUST be
    # passed explicitly (--neff-path): the raw NTFF alone cannot be correlated to instructions.
    if not (db / "Instruction.parquet").exists():
        db.mkdir(parents=True, exist_ok=True)
        cmd = [explorer, "view", "--ingest-only", "-d", str(ir),
               "--neff-path", str(cand / "file.neff"),
               "--output-format", "parquet", "--output-file", str(db) + "/"]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception:
            return None, {}
    if not (db / "Instruction.parquet").exists():
        return None, {}
    artifacts = {"profiler_db": True}
    # qor_separated / stalls are NOT in an ingested profiler_db — they are generated separately by
    # the internal compiler run (run_internal_qor_collect). If a prior collect already left them
    # beside this run, surface them here too.
    if (cand / "artifacts" / "qor_separated").exists() or list(cand.glob("artifacts/analysis_nc*.log")):
        artifacts["qor_separated"] = True
    for s in (ir / "stalls.json", cand / "stalls.json"):
        if s.exists():
            artifacts["stalls"] = True
    return str(db), artifacts


def _pq_num_rows(path: Path):
    """Row count of a parquet file, or None if no parquet reader is available."""
    try:
        import pyarrow.parquet as pq
        return pq.ParquetFile(str(path)).metadata.num_rows
    except Exception:
        try:
            import pandas as pd
            return len(pd.read_parquet(str(path)))
        except Exception:
            return None


def _pq_col_coverage(path: Path, col: str):
    """(non_null, total) for one column of a parquet file, or None if unreadable/absent."""
    try:
        import pyarrow.parquet as pq
        t = pq.read_table(str(path), columns=[col])
        return t.num_rows - t.column(0).null_count, t.num_rows
    except Exception:
        try:
            import pandas as pd
            df = pd.read_parquet(str(path), columns=[col])
            return int(df[col].notna().sum()), len(df)
        except Exception:
            return None


def _pq_non_null_masks(path: Path, cols):
    """{col: [non-null per row]} for those of `cols` the parquet file has, or None if unreadable."""
    try:
        import pyarrow.parquet as pq
        pf = pq.ParquetFile(str(path))
        present = [c for c in cols if c in pf.schema_arrow.names]
        t = pf.read(columns=present)
        return {c: t.column(c).is_valid().to_pylist() for c in present}
    except Exception:
        try:
            import pandas as pd
            df = pd.read_parquet(str(path))
            return {c: df[c].notna().tolist() for c in cols if c in df.columns}
        except Exception:
            return None


def _pq_source_coverage(path: Path):
    """(attributed, compiled, scoped) source-location coverage of Instruction.parquet, or None.

    `compiled` counts rows with a bir_id: the runtime wraps every kernel in a fixed barrier
    pre/post-amble (DRAIN, EVENT_SEMAPHORE, NOTIFY, TENSOR_LOAD, COMPARE_BRANCH) that has no BIR
    instruction and so no source line under any compile flag. Counting those rows failed small
    kernels: a 255-row profile whose 21 compiled instructions were all attributed read as 8%.
    Without a bir_id column `scoped` is False and every row counts.

    A row is attributed when nki_source_location OR bir_debug_info_source_location is set.
    neuron-explorer fills nki_source_location from the BIR location only as a last resort, and
    not at all once the NEFF's kernel debug info names the instruction, so the BIR column can
    hold a line the NKI column lacks.
    """
    masks = _pq_non_null_masks(path, ("nki_source_location", "bir_debug_info_source_location", "bir_id"))
    if masks is None or "nki_source_location" not in masks:
        return None
    nki = masks["nki_source_location"]
    bir_loc = masks.get("bir_debug_info_source_location", [False] * len(nki))
    compiled = masks.get("bir_id")
    attributed = [a or b for a, b in zip(nki, bir_loc)]
    if compiled is None:
        return sum(attributed), len(attributed), False
    return sum(a for a, c in zip(attributed, compiled) if c), sum(compiled), True


def validate_device_profiler_db(perf_dir: str):
    """D10a — the resolved profile must be a CORRECT DEVICE profile. No system trace required.

    Returns (ok, error, warnings). ok=False => hard fail with `error`. Checks:
      * Instruction.parquet exists AND rows > 0            (real device instructions)
      * Metadata.ticks_per_nanosec present                 (cycle units known)
      * source-location coverage >= 50% of compiled rows   (sufficient kernel attribution; else FAIL;
                                                             see _pq_source_coverage)
    When no parquet reader is available the deep checks are skipped with a warning (never a false fail).
    """
    warns = []
    d = Path(perf_dir)
    ins = d / "Instruction.parquet"
    if not ins.exists():
        return False, f"{ins} missing — not a profiler_db.", warns
    nrows = _pq_num_rows(ins)
    if nrows is None:
        warns.append(f"no parquet reader available to inspect {ins}; skipped deep content checks.")
        return True, "", warns
    if nrows <= 0:
        return False, f"{ins} has 0 instruction rows (empty/failed device capture).", warns
    md = d / "Metadata.parquet"
    if md.exists():
        cov = _pq_col_coverage(md, "ticks_per_nanosec")
        if cov is not None and cov[0] == 0:
            return False, "Metadata.ticks_per_nanosec is absent/null (cycle units unknown).", warns
    else:
        warns.append("Metadata.parquet missing; ticks_per_nanosec (cycle-unit scaling) unverified.")
    cov = _pq_source_coverage(ins)
    if cov is None:
        warns.append("could not read nki_source_location; attribution coverage unverified.")
    else:
        attributed, compiled, scoped = cov
        if not scoped:
            warns.append(f"{ins} has no bir_id column; source-location coverage also counts the "
                         "runtime's barrier pre/post-amble, so small kernels read low.")
        if compiled <= 0:
            return False, (f"{ins} has no compiler-emitted instructions (no row carries a bir_id), "
                           "so none of the trace maps back to the kernel."), warns
        frac = attributed / compiled
        if frac < 0.50:
            return False, (f"source-location coverage {100 * frac:.0f}% of {compiled} compiled "
                           "instructions (< 50%) — insufficient kernel attribution (were the debug "
                           "compile flags XLA_IR_DEBUG=1 / XLA_HLO_DEBUG=1 / NEURON_FRAMEWORK_DEBUG=1 "
                           "set at compile?)."), warns
    return True, "", warns


def build_context(perf_dir, kernel, test_name, target, cycles, mode,
                  regime=None, context=None, qor_data_path=None) -> str:
    lines = [
        "dispatch_role: specialist",
        "dispatch_depth: 1",
        f"perf_data_path: {perf_dir}",
    ]
    if qor_data_path and str(qor_data_path) != str(perf_dir):
        lines.append(f"qor_data_path: {qor_data_path}   # internal compiler run: "
                     f"qor_separated_analysis.csv + stalls.json live here (separation/roofline/"
                     f"sharding/dma-order)")
    lines += [
        f"kernel_under_test: {kernel}",
        f"test_name: {test_name}",
        f"target: {target}",
    ]
    if cycles is not None:
        lines.append(f"measured_latency_cycles: {cycles}")
    # VENDORED offline cost model (KERNEL_LOOP_DEPENDENCIES §4 no longer holds): if the
    # co-packaged launcher is a sibling of this script, tell the cost specialist to use it —
    # calibrated latency_ns/ii_ns with NO brazil-test-exec / shared fleet / hardware run.
    # (Compute+Tensor+MEMSET only; DMA/collectives + uncalibrated opcodes are rejected, not guessed.)
    _cq = Path(__file__).resolve().parent / "nki_cost_query.py"
    if _cq.exists():
        lines.append(f"cost_query_cmd: python3 {_cq}   # offline; same args as "
                     f"`neuron_kernel_neff_sim.cost_query` (--target/--spec/--spec-file/--list-opcodes). "
                     f"Prefer this over `brazil-test-exec python3 -m neuron_kernel_neff_sim.cost_query`.")
    # DESCRIPTIVE caller context (regime + free-text). This is passed VERBATIM to the
    # analysis specialists so the orchestrator can hand down what is TRUE about the
    # workload (decode vs prefill, production batch, a binding-op hypothesis to confirm,
    # an SLA the kernel sits under, layout/shape facts the node bracket cannot express).
    # It is deliberately factual-only: naming a specific FIX / shard axis / "answer" here
    # would break the answer-blind contract (the specialists must DISCOVER the fix), so the
    # orchestrator must keep this prescriptive-free. A binding-op prior MUST be phrased as a
    # hypothesis to confirm/refute against evidence, never as a conclusion.
    if regime:
        lines.append(f"regime: {regime}")
    if context:
        lines.append("caller_context (descriptive, NOT a prescribed fix; confirm any "
                     "hypothesis against evidence):")
        for cl in str(context).splitlines() or [str(context)]:
            lines.append(f"  {cl}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--node", required=True)
    p.add_argument("--target", default="trn2")
    p.add_argument("--out-base", default="neuron_test_output/perf_data")
    p.add_argument("--measure-out-dir", default="neuron_test_output",
                   help="measure.py's --out-dir under --repo; analyze auto-ingests the NTFF+NEFF "
                        "left there (out-*/file.neff + out-*/infer_result/*.ntff) into a profiler_db "
                        "so it runs in FULL mode without brazil. The baseline measure always leaves a "
                        "device capture; a genuinely absent profile is a HARD FAILURE (no source-only "
                        "fallback).")
    p.add_argument("--measured-cycles", type=int, default=None,
                   help="CycleCount from measure.py (the cost specialist's anchor)")
    p.add_argument("--perf-data-path", default=None,
                   help="the REQUIRED profiler_db INPUT (the job's collect.profiler_db): the Stage-3 "
                        "full-model capture ingested to parquet (nki_source_location) in autobridge, or "
                        "a supplied capture. Scope it to the kernel via --kernel-under-test. May point "
                        "at the profiler_db dir itself or its parent. If omitted, analyze ingests the "
                        "measure.py capture on disk; if neither resolves, it FAILS (profile required).")
    p.add_argument("--kernel-under-test", default=None,
                   help="explicit kernel source (repo-relative), from the kernel_target — overrides "
                        "the import-heuristic so it is not misresolved to a utils/enums import.")
    p.add_argument("--region-analysis", action="store_true",
                   help="DEPRECATED / no-op: region specialists ALWAYS dispatch when a profile is "
                        "present (analysis is always full).")
    p.add_argument("--regime", default=None,
                   help="descriptive workload regime passed to the specialists, e.g. "
                        "'decode' / 'prefill' / 'bs1 single-token decode'.")
    p.add_argument("--context", default=None,
                   help="free-text DESCRIPTIVE context handed verbatim to the analysis "
                        "specialists (shape/usage/prod-batch/binding-op HYPOTHESIS-to-confirm). "
                        "Must NOT name a fix or shard axis (answer-blind contract).")
    p.add_argument("--job", default=None,
                   help="optional kernel measurement input JSON. Its `collect.profiler_db` (the "
                        "REQUIRED profile INPUT) sets --perf-data-path when that is not given on the "
                        "CLI; `regime`/`context` are also read from the job when not given on the CLI. "
                        "Explicit CLI flags take precedence. qor data is always generated internally "
                        "regardless of the job.")
    args = p.parse_args(argv)

    # Fold the measurement input's collect/analyze blocks into Phase-0. The profile is a REQUIRED
    # INPUT: collect.profiler_db sets --perf-data-path when the CLI did not. qor data is always
    # generated internally (run_internal_qor_collect), independent of the job. Explicit CLI wins.
    if args.job:
        jp = Path(os.path.expanduser(args.job))
        if not jp.exists():
            print(json.dumps({"error": f"--job file not found: {jp}"}))
            return 2
        try:
            job = json.loads(jp.read_text())
        except Exception as e:
            print(json.dumps({"error": f"--job {jp}: not valid JSON ({e})"}))
            return 2
        job = job.get("nakos_config", job)
        collect = job.get("collect") or {}
        analyze = job.get("analyze") or {}
        if not args.perf_data_path and collect.get("profiler_db"):
            args.perf_data_path = collect["profiler_db"]
        if args.regime is None:
            args.regime = job.get("regime") or analyze.get("regime")
        if args.context is None:
            args.context = job.get("context") or analyze.get("context")

    repo = Path(args.repo).resolve()

    # The device profile is MANDATORY (no source-only path). Two artifacts, both required:
    #   (a) profiler_db — the INPUT: --perf-data-path (job collect.profiler_db) if given, else the
    #       profile measure.py left on disk (auto-ingested). HARD FAIL if neither resolves.
    #   (b) qor_separated + stalls — ALWAYS generated here by the internal compiler run
    #       (run_internal_qor_collect / collect_perf_data_local.py). Never brazil, never optional.
    perf_dir, artifacts = (None, {})
    # (1) explicit profiler_db INPUT (Stage-3 model profile, or an assembled/local capture).
    if args.perf_data_path:
        pp = Path(os.path.expanduser(args.perf_data_path))
        man = pp / "manifest.json"
        if man.exists() and (pp / "profiler_db" / "Instruction.parquet").exists():
            # assembled perf_data dir (profiler_db + qor + stalls). VERIFY each cited file exists on
            # disk (never trust the manifest blindly -- the campaign's cited-artifact discipline).
            perf_dir = str(pp / "profiler_db")
            artifacts = {"profiler_db": True}
            try:
                ap_present = json.loads(man.read_text()).get("artifacts_present", {})
            except Exception:
                ap_present = {}
            if ap_present.get("qor_separated") and (pp / "qor_separated_analysis.csv").exists():
                artifacts["qor_separated"] = True
            if ap_present.get("stalls") and (pp / "stalls.json").exists():
                artifacts["stalls"] = True
        else:
            cand = pp if (pp / "Instruction.parquet").exists() else (pp / "profiler_db")
            if (cand / "Instruction.parquet").exists():
                perf_dir = str(cand)
                artifacts = {"profiler_db": True}
    # (2) else AUTO-INGEST the profile measure.py already captured on this node (NTFF+NEFF).
    if perf_dir is None:
        perf_dir, artifacts = try_local_ntff_ingest(repo, args.measure_out_dir)

    # (b) qor data is ALWAYS generated by the internal compiler run — regardless of how the
    # profiler_db was obtained. collect_perf_data_local.py yields a self-contained assembled dir
    # (profiler_db + qor_separated + stalls). It is brazil-free and unconditional (no flag).
    qor_dir, qor_artifacts = run_internal_qor_collect(repo, args.node, args.target)
    if perf_dir is None and qor_dir:
        # no external/measure profiler_db: use the internal collect's own (co-located with qor/stalls).
        _db = Path(qor_dir) / "profiler_db"
        if (_db / "Instruction.parquet").exists():
            perf_dir, artifacts = str(_db), dict(qor_artifacts)
    if qor_artifacts.get("qor_separated"):
        artifacts["qor_separated"] = True
    if qor_artifacts.get("stalls"):
        artifacts["stalls"] = True

    kernel = args.kernel_under_test or resolve_kernel_path(repo, args.node)
    m = re.search(r"::(\w+)\b", args.node)
    test_name = m.group(1) if m else ""

    # HARD FAIL if no profiler_db resolved — the profile is required; there is no source-only path.
    if perf_dir is None:
        print(json.dumps({
            "error": ("PROFILE REQUIRED BUT NOT FOUND — no profiler_db (Instruction.parquet) could be "
                      "resolved from --perf-data-path / collect.profiler_db, the measure.py capture "
                      "under %r, or the internal collect. Supply collect.profiler_db, or run the "
                      "baseline measure (it leaves out-*/file.neff + out-*/infer_result/*.ntff to "
                      "auto-ingest). FAILING rather than analyzing without a profile." % args.measure_out_dir),
            "mode": "FAILED_NO_PROFILE",
            "kernel_under_test": kernel, "test_name": test_name, "target": args.target,
        }), file=sys.stderr)
        return 3

    # D10a: the resolved profile must be a correct DEVICE profile (no system trace required).
    ok, verr, vwarn = validate_device_profiler_db(perf_dir)
    if not ok:
        print(json.dumps({
            "error": "DEVICE PROFILE INVALID — " + verr,
            "mode": "FAILED_BAD_PROFILE", "perf_data_path": perf_dir,
            "kernel_under_test": kernel, "test_name": test_name, "target": args.target,
        }), file=sys.stderr)
        return 3

    # Profile present => FULL analysis ALWAYS. Region specialists are never gated off; the
    # --region-analysis flag is retained only for backward compatibility and has no effect.
    region_analysis = True
    specialists = applicable(artifacts, region_analysis)
    ctx = build_context(perf_dir, kernel, test_name, args.target,
                        args.measured_cycles, "collected",
                        regime=args.regime, context=args.context, qor_data_path=qor_dir)

    print(json.dumps({
        "mode": "collected",
        "perf_data_path": perf_dir,
        "qor_data_path": qor_dir,
        "artifacts_present": artifacts,
        "kernel_under_test": kernel,
        "test_name": test_name,
        "target": args.target,
        "measured_cycles": args.measured_cycles,
        "dispatch_specialists": specialists,
        "dispatch_context": ctx,
        "warnings": vwarn,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
