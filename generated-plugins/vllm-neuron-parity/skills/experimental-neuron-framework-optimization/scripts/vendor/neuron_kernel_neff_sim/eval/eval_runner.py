"""Orchestrate: simulate each NEFF, fetch ground truth, emit a report."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import csv
import logging
import os

from tqdm import tqdm

from ..graph_builder import DynamicLoopUnsupportedError, build_graph
from ..isa_decoder import decode_binary
from ..neff_parser import parse_neff
from ..scheduler import Scheduler
from .discovery import NeffCase
from .ground_truth import GroundTruthBackend
from .histogram import histogram, summary_lines

log = logging.getLogger(__name__)


@dataclass
class EvalRow:
    test_name: str
    sim_ns: float | None
    gt_ns: float | None
    abs_err_pct: float | None
    rel_err_pct: float | None
    gt_source: str
    note: str = ""


def simulate_neff(neff_path: Path, target_default: str = "trn2") -> float:
    """Run the simulator on a single NEFF and return ``total_time_ns``."""
    neff = parse_neff(neff_path)
    target = neff.target or target_default
    decoded = {}
    for eng_name in neff.get_engine_names():
        bd = neff.engines[eng_name].bin_data
        if bd:
            decoded[eng_name] = decode_binary(bd, eng_name, target)
    graph = build_graph(neff, decoded)
    host_count = sum(
        1
        for v in neff.def_json.get("var", {}).values()
        if isinstance(v, dict) and v.get("type") in ("input", "remote")
    )
    rg = neff.def_json.get("replica_groups", [[[0]]])
    try:
        world_size = len(rg[0][0]) if rg and rg[0] else 1
    except (IndexError, TypeError):
        world_size = 1
    sched = Scheduler(
        target,
        vnc_count=neff.vnc_count,
        host_input_bytes=neff.input_bytes,
        host_input_count=host_count,
        host_output_bytes=neff.output_bytes,
        host_output_count=neff.output_count,
        cc_world_size=world_size,
    )
    result = sched.simulate(graph)
    return result.total_time_ns


def _simulate_worker(args: tuple[str, Path, str]) -> tuple[str, float | None, str]:
    """Worker entry point for ``ProcessPoolExecutor``.

    Must be top-level (picklable). Returns ``(test_name, sim_ns_or_None, note)``.
    """
    test_name, neff_path, target_default = args
    try:
        sim_ns = simulate_neff(neff_path, target_default=target_default)
        return test_name, sim_ns, ""
    except DynamicLoopUnsupportedError as exc:
        return test_name, None, f"unsupported: dynamic loops ({exc})"
    except Exception as exc:  # noqa: BLE001 — surface failures in the report
        return test_name, None, f"sim_error: {exc}"


def evaluate_cases(
    cases: list[NeffCase],
    backend: GroundTruthBackend,
    target_default: str = "trn2",
    workers: int | None = None,
) -> list[EvalRow]:
    """Simulate every case in parallel and pair with ground truth.

    Args:
        cases: NEFFs to evaluate.
        backend: ground-truth backend (called serially in the main process so
            backends that shell out via SSH don't fork-bomb the bastion).
        target_default: hardware target if the NEFF doesn't pin one.
        workers: number of worker processes; defaults to ``os.cpu_count()``.
    """
    if not cases:
        return []
    if workers is None:
        workers = os.cpu_count() or 1
    workers = max(1, min(workers, len(cases)))

    case_by_name = {c.test_name: c for c in cases}
    sim_results: dict[str, tuple[float | None, str]] = {}

    work = [(c.test_name, c.neff_path, target_default) for c in cases]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_simulate_worker, w) for w in work]
        with tqdm(total=len(futures), desc="simulating", unit="case", dynamic_ncols=True) as pbar:
            for fut in as_completed(futures):
                test_name, sim_ns, note = fut.result()
                sim_results[test_name] = (sim_ns, note)
                pbar.set_postfix_str(test_name[:50], refresh=False)
                pbar.update(1)

    rows: list[EvalRow] = []
    for case in tqdm(cases, desc="ground truth", unit="case", dynamic_ncols=True):
        sim_ns, note = sim_results.get(case.test_name, (None, "missing_sim"))
        if note.startswith("sim_error"):
            log.warning("simulation failed for %s: %s", case.test_name, note)

        gt = backend.fetch(case.test_name, neff_path=case.neff_path)
        gt_ns = gt.inference_time_s * 1e9 if gt.inference_time_s is not None else None

        if sim_ns is not None and gt_ns is not None and gt_ns > 0:
            rel_err = (sim_ns - gt_ns) / gt_ns * 100
            abs_err = abs(rel_err)
        else:
            rel_err = None
            abs_err = None

        rows.append(
            EvalRow(
                test_name=case_by_name[case.test_name].test_name,
                sim_ns=sim_ns,
                gt_ns=gt_ns,
                abs_err_pct=abs_err,
                rel_err_pct=rel_err,
                gt_source=gt.source,
                note=note,
            )
        )
    return rows


def write_report(rows: list[EvalRow], report_dir: Path) -> None:
    """Emit ``results.csv`` and ``histogram.md`` under ``report_dir``."""
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    csv_path = report_dir / "results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "test_name", "sim_ns", "gt_ns", "rel_err_pct", "abs_err_pct", "gt_source", "note",
        ])
        for r in rows:
            writer.writerow([
                r.test_name,
                f"{r.sim_ns:.0f}" if r.sim_ns is not None else "",
                f"{r.gt_ns:.0f}" if r.gt_ns is not None else "",
                f"{r.rel_err_pct:+.2f}" if r.rel_err_pct is not None else "",
                f"{r.abs_err_pct:.2f}" if r.abs_err_pct is not None else "",
                r.gt_source,
                r.note,
            ])

    paired = [r for r in rows if r.rel_err_pct is not None]
    errs = [r.rel_err_pct for r in paired if r.rel_err_pct is not None]
    md_path = report_dir / "histogram.md"
    with open(md_path, "w") as f:
        f.write("# Hardware Event Simulator — Accuracy Report\n\n")
        f.write(f"Cases attempted: {len(rows)}\n")
        f.write(f"Cases with both sim + ground truth: {len(paired)}\n\n")
        if not errs:
            f.write("_No paired sim/ground-truth results — nothing to histogram._\n")
            return
        f.write("## Summary\n\n```\n")
        f.write("\n".join(summary_lines(errs)))
        f.write("\n```\n\n")
        f.write("## Histogram (signed error %)\n\n```\n")
        f.write(histogram(errs, bins=20, low=-25, high=25))
        f.write("\n```\n\n")

        worst = sorted(paired, key=lambda r: -(r.abs_err_pct or 0.0))[:10]
        f.write("## Worst 10 cases\n\n")
        f.write("| err% | sim (us) | gt (us) | source | test |\n")
        f.write("|---|---|---|---|---|\n")
        for r in worst:
            f.write(
                f"| {r.rel_err_pct:+.1f} "
                f"| {(r.sim_ns or 0)/1000:.1f} "
                f"| {(r.gt_ns or 0)/1000:.1f} "
                f"| {r.gt_source} "
                f"| `{r.test_name[:80]}` |\n"
            )
