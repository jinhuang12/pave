"""CLI: ``brazil-test-exec python3 -m neuron_kernel_neff_sim.eval ...``.

See AGENTS.md for usage examples.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .compile_runner import locate_compiled_neffs, run_compile_only
from .discovery import discover_local_neffs
from .eval_runner import evaluate_cases, write_report
from .ground_truth import make_backend


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m neuron_kernel_neff_sim.eval",
        description=(
            "Hardware event simulator — batch accuracy evaluation. Simulate each "
            "NEFF in a directory (or compile-from-pkg), pair with ground-truth "
            "InferenceTime, and emit a CSV + histogram report."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--neff-dir", type=Path, help="Directory of pre-compiled NEFFs (out-*/file.neff).")
    src.add_argument(
        "--compile-from-pkg",
        type=Path,
        help="Path to a Brazil package whose fast tests should be compiled first (e.g. KaenaNeuronKernelLibrary).",
    )

    parser.add_argument(
        "--ground-truth",
        choices=["opensearch", "ntff", "auto"],
        default="auto",
        help="Where to read InferenceTime from. Default: auto (opensearch -> ntff fallback).",
    )
    parser.add_argument("--target", default="trn2", choices=["trn1", "trn2", "trn3"])
    parser.add_argument("--filter", dest="filter_pattern", default=None, help="pytest -k style filter.")
    parser.add_argument(
        "--kernel-index",
        default=None,
        help="Suffix for the kernel-perf-<index> AOSS index. Required for opensearch/auto modes.",
    )
    parser.add_argument(
        "--bastion",
        default="kernel-perf-bastion",
        help="SSH host that proxies awscurl to AOSS. Empty string = call awscurl directly.",
    )
    parser.add_argument(
        "--run-type",
        default=None,
        help="Optional RunType filter for OpenSearch (e.g. 'model', 'development'). Default: any.",
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument(
        "-j", "--workers", type=int, default=None,
        help="Number of parallel simulation workers (default: os.cpu_count()).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("neuron_kernel_neff_sim.eval")

    if args.compile_from_pkg is not None:
        log.info("Compiling fast tests in %s …", args.compile_from_pkg)
        result = run_compile_only(
            args.compile_from_pkg,
            filter_pattern=args.filter_pattern,
            target=args.target,
        )
        if result.returncode != 0:
            log.error("compile-only run failed (rc=%s):\n%s", result.returncode, result.stderr)
            return result.returncode
        cases = locate_compiled_neffs(args.compile_from_pkg)
    else:
        cases = discover_local_neffs(args.neff_dir)
        if args.filter_pattern:
            cases = [c for c in cases if args.filter_pattern in c.test_name]

    log.info("Loaded %d NEFF cases", len(cases))
    if not cases:
        log.error("no NEFF cases found")
        return 2

    backend = make_backend(
        mode=args.ground_truth,
        kernel_index=args.kernel_index,
        bastion=args.bastion if args.bastion else None,
        run_type=args.run_type,
    )

    rows = evaluate_cases(cases, backend, target_default=args.target, workers=args.workers)
    write_report(rows, args.report_dir)
    log.info("Wrote report to %s", args.report_dir)

    paired = [r for r in rows if r.rel_err_pct is not None]
    log.info("Paired sim+gt: %d/%d", len(paired), len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
