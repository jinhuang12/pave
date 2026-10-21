"""Standalone command-line entry point for the hardware event simulator."""

import argparse
import sys
import time
from pathlib import Path

from .neff_parser import parse_neff
from .isa_decoder import decode_binary
from .graph_builder import build_graph
from .scheduler import Scheduler, SimResult
from .perfetto_writer import write_trace


def _flatten_intervals(intervals):
    """Merge overlapping (start, end) intervals; return total covered ns."""
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e:
            if e > cur_e:
                cur_e = e
        else:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
    total += cur_e - cur_s
    return total


def _compute_aggregate_util(result: SimResult):
    """Aggregate PE/DMA across all sub-rows into a single utilization."""
    pe_intervals = []
    dma_intervals = []
    for engine_name, evs in result.engine_timelines.items():
        is_pe = engine_name.startswith("PE")
        is_dma = engine_name.startswith("DMA")
        if not (is_pe or is_dma):
            continue
        for ev in evs:
            if ev.end_ns <= ev.start_ns:
                continue
            (pe_intervals if is_pe else dma_intervals).append(
                (ev.start_ns, ev.end_ns))
    total_ns = max(result.total_time_ns, 1.0)
    return {
        "PE (aggregate)": _flatten_intervals(pe_intervals) / total_ns,
        "DMA (aggregate)": _flatten_intervals(dma_intervals) / total_ns,
    }


# Per-engine clock freq (cycles/ns) and channel/array shape per target.
# Mirrors NeuronPerfUIComponents/SummaryPage/utils/query.ts:
#   peak_flops(period) = freq * period_ns * ops_per_cycle
# where ops_per_cycle is cols*rows*2 for Tensor and num_channels for the
# others. The query.ts file explicitly does NOT compute hardware FLOPS
# for Vector/Scalar/GpSimd (TODOs), so we report HFU/MFU only for Tensor.
_ENGINE_HW = {
    "trn1": {
        "Tensor": (2.80, 128, 128),
        "Vector": (1.12, 128),
        "Scalar": (1.40, 128),
        "GpSimd": (1.40, 128),
    },
    "trn2": {
        "Tensor": (2.40, 128, 128),
        "Vector": (0.96, 128),
        "Scalar": (1.20, 128),
        "GpSimd": (1.20, 128),
    },
    "trn3": {
        "Tensor": (2.40, 128, 128),
        "Vector": (1.20, 128),
        "Scalar": (1.20, 128),
        "GpSimd": (1.20, 128),
    },
}


def _engine_canonical(name: str) -> str:
    """Map timeline name (e.g., PE0, DVE0, Pool0) -> canonical engine type."""
    base = name.split(":", 1)[0].rstrip("0123456789")
    return {
        "PE": "Tensor",
        "DVE": "Vector",
        "Activation": "Scalar",
        "Pool": "GpSimd",
        "SP": "Sync",
    }.get(base, base)


def _peak_ops(eng: str, target: str, period_ns: float) -> float:
    """Theoretical peak ops over period_ns (matches query.ts formulas)."""
    cfg = _ENGINE_HW.get(target, _ENGINE_HW["trn2"]).get(eng)
    if not cfg:
        return 0.0
    freq = cfg[0]
    if eng == "Tensor":
        cols, rows = cfg[1], cfg[2]
        ops_per_cycle = cols * rows * 2  # opsPerMacc = 2
    else:
        ops_per_cycle = cfg[1]
    return freq * period_ns * ops_per_cycle


def _compute_flops_util(graph, result: SimResult, target: str):
    """Per-engine FLOPS utilization = elements processed / peak ops.

    Mirrors NeuronPerfUIComponents/SummaryPage/utils/query.ts:
      Tensor      : sum(2 * partitions * M * N) over MATMUL/MATMUL_MX
      Vector/Scalar/GpSimd: sum(partitions * elements_per_partition)
                            over element-touching ops on that engine
    Denominator = freq * period * ops_per_cycle where ops_per_cycle is
    cols*rows*2 for Tensor and num_channels for others.
    """
    period_ns = max(result.total_time_ns, 1.0)
    ops_done = {"Tensor": 0.0, "Vector": 0.0, "Scalar": 0.0, "GpSimd": 0.0}

    # Only count an instruction if it actually executed (has a SimEvent).
    executed = {ev.instruction_index for ev in result.events}

    for inst in graph.instructions:
        if inst.index not in executed:
            continue
        eng = _engine_canonical(inst.engine_name)
        if eng not in ops_done:
            continue
        if inst.opcode in ("MATMUL", "MATMUL_MX"):
            m = (inst.extra or {}).get("matmul_m", 0)
            n = (inst.extra or {}).get("matmul_n", 0)
            ops_done["Tensor"] += 2.0 * inst.num_partitions * m * n
        else:
            # Element-touching ops on Vector/Scalar/GpSimd contribute
            # partitions * elements_per_partition. Skip control ops by
            # checking elements_per_partition > 0.
            if inst.elements_per_partition > 0:
                ops_done[eng] += inst.num_partitions * inst.elements_per_partition

    out = {}
    for eng in ("Tensor", "Vector", "Scalar", "GpSimd"):
        peak = _peak_ops(eng, target, period_ns)
        if peak <= 0:
            continue
        out[eng] = min(1.0, ops_done[eng] / peak)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hardware event simulator: NEFF -> Perfetto trace"
    )
    parser.add_argument(
        "--neff", required=True, type=str,
        help="Path to the .neff file to simulate"
    )
    parser.add_argument(
        "--target", default="trn2", choices=["trn1", "trn2", "trn3"],
        help="Hardware target (default: trn2)"
    )
    parser.add_argument(
        "--output", "-o", default="cycle_sim_trace.json", type=str,
        help="Output path for Perfetto trace JSON (default: cycle_sim_trace.json)"
    )
    parser.add_argument(
        "--show-phases", action="store_true",
        help="Show ReadInit/Exec/WriteDrain sub-phases in trace"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print detailed simulation info"
    )

    args = parser.parse_args()

    neff_path = Path(args.neff)
    if not neff_path.exists():
        print(f"Error: NEFF file not found: {neff_path}", file=sys.stderr)
        return 1

    t0 = time.time()

    # Phase 1: Parse NEFF
    if args.verbose:
        print(f"Parsing NEFF: {neff_path}")
    neff = parse_neff(neff_path)
    if args.verbose:
        print(f"  Target: {neff.target or args.target}")
        print(f"  Engines: {list(neff.engines.keys())}")
        print(f"  Semaphores: {neff.semaphore_count}")

    target = neff.target or args.target

    # Phase 2: Decode instruction streams in parallel (each engine
    # spawns a neuron-disasm subprocess — embarrassingly parallel).
    if args.verbose:
        print("Decoding instruction streams...")
    decoded_engines = {}
    engine_names = list(neff.get_engine_names())
    if len(engine_names) >= 3:
        from concurrent.futures import ThreadPoolExecutor
        def _decode(name):
            return name, decode_binary(neff.engines[name].bin_data, name, target)
        with ThreadPoolExecutor(max_workers=min(len(engine_names), 8)) as ex:
            for name, decoded in ex.map(_decode, engine_names):
                decoded_engines[name] = decoded
                if args.verbose:
                    print(f"  {name}: {len(decoded)} instructions")
    else:
        for engine_name in engine_names:
            engine_data = neff.engines[engine_name]
            decoded = decode_binary(engine_data.bin_data, engine_name, target)
            decoded_engines[engine_name] = decoded
            if args.verbose:
                print(f"  {engine_name}: {len(decoded)} instructions")

    # Phase 3: Build dependency graph
    if args.verbose:
        print("Building dependency graph...")
    graph = build_graph(neff, decoded_engines)
    if args.verbose:
        cross_deps = sum(
            len(inst.cross_engine_deps) for inst in graph.instructions
        )
        print(f"  Total instructions: {graph.total_instructions}")
        print(f"  Cross-engine dependencies: {cross_deps}")

    # Phase 4: Simulate
    if args.verbose:
        print("Running simulation...")
    # Pass NEFF metadata so the scheduler can model host-DMA setup,
    # VNC bandwidth split, etc. (matches what callers like batch_validate.py
    # already pass).
    host_input_count = sum(
        1 for v in neff.def_json.get("var", {}).values()
        if isinstance(v, dict) and v.get("type") in ("input", "remote")
    )
    scheduler = Scheduler(
        target, vnc_count=neff.vnc_count,
        host_input_bytes=neff.input_bytes,
        host_input_count=host_input_count,
        host_output_bytes=neff.output_bytes,
        host_output_count=neff.output_count,
    )
    result = scheduler.simulate(graph)

    # Phase 5: Write trace
    if args.verbose:
        print(f"Writing trace to: {args.output}")
    write_trace(result, graph, args.output, show_phases=args.show_phases,
                vnc_count=neff.vnc_count)

    elapsed = time.time() - t0

    # Compute aggregated PE/DMA active utilization and per-engine FLOPS
    # utilization. Engine util is "any-activity" per timeline; FLOPS
    # util is FLOPS done / theoretical-peak FLOPS over total_time.
    aggregate_util = _compute_aggregate_util(result)
    flops_util = _compute_flops_util(graph, result, target)

    # Summary
    print(f"Simulation complete:")
    print(f"  Total simulated time: {result.total_time_ns:.0f} ns ({result.total_time_ns/1000:.1f} µs)")
    print(f"  Instructions simulated: {graph.total_instructions}")
    # PE throttle: ns of PE-busy time spent under di/dt throttle (idle
    # ramp + frequent-gap regions, where the chip runs at 4/8 = 50%).
    pe_busy = result.pe_busy_ns
    pe_throt = result.pe_throttled_busy_ns
    pe_throt_pct = (pe_throt / pe_busy * 100.0) if pe_busy > 0 else 0.0
    print(f"  PE throttle:")
    print(f"    Total throttled time: {pe_throt:.0f} ns ({pe_throt/1000:.1f} µs)")
    print(f"    PE throttled: {pe_throt_pct:.1f}% of PE busy ({pe_busy/1000:.1f} µs)")
    print(f"  Aggregate utilization (active):")
    for label, val in aggregate_util.items():
        print(f"    {label}: {val*100:.1f}%")
    print(f"  FLOPS utilization (elements processed vs theoretical peak):")
    for engine, val in sorted(flops_util.items()):
        print(f"    {engine}: {val*100:.2f}%")
    print(f"  Engine utilization:")
    for engine_name, util in sorted(result.engine_utilization.items()):
        # Skip per-queue DMA rows — show only the aggregate above.
        if engine_name.startswith("DMA:"):
            continue
        print(f"    {engine_name}: {util*100:.1f}%")
    print(f"  Wall time: {elapsed:.2f}s")
    print(f"  Trace written to: {args.output}")

    if result.violations:
        print(
            f"[CycleSim] {len(result.violations)} dependency violations:",
            file=sys.stderr)
        for v in result.violations:
            print(f"  {v}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
