"""Per-opcode accuracy analysis: compare simulated per-(engine, opcode) active
time against hardware OpcodeSummary.parquet, normalized correctly for VNC/LNC2.

Why VNC normalization matters: an LNC2 NEFF runs the same BIR program on two
physical NeuronCores. The hardware profiler's OpcodeSummary.parquet sums
``instruction_count`` and ``active_time_ns`` across both ``pcore_idx`` values,
so its totals are 2x what the simulator (which models a single pcore) produces.
The number of cores is read from Metadata.parquet (``vnc_enabled``) -- NOT
guessed from the test name, which is frequently wrong (many LNC2 kernels have
no ``vnc_2``/``lnc_2`` marker in their directory name).

Hardware target metric is ``active_time_ns`` (engine-busy time, excludes
sem-wait stalls). Sem-wait time is modeled separately by the scheduler as
cross-engine dependencies, so the per-opcode cost model must target
active_time, not the wait-inflated Instruction.parquet ``duration_ns``.

Structurally-absent opcodes (KNOWN LIMITATION): the profiler reports per-engine
queue/sequencer bookkeeping ops -- TENSOR_LOAD, DRAIN, WRITE,
EVENT_SEMAPHORE_RANGE_CLEAR, NOTIFY, COMPARE_BRANCH, BRANCH_PREFETCH_HINT, plus
an entire synthetic ``sync`` engine -- that are NOT standalone instructions in
the NEFF binary the simulator decodes. They total ~9% of all HW active time but
are rare (TENSOR_LOAD ~1 per 30 compute ops, DRAIN ~1 per 12 instructions) and
largely overlap compute (the sync-engine portion is SP queue coordination). The
simulator already approximates the per-instruction prologue/epilogue of these
via the scheduler's write_drain_ns and semaphore_start_ns; emitting them as
separate cost would double-count. They show ``sim_us=0`` in this report by
design. EVENT_SEMAPHORE is similar: the sim emits it only on the signaling
engine while HW counts every waiting engine's check.

Naming: simulator PSEUDO_* opcodes are canonicalized to their HW names via
cost_model.canonical_opcode (e.g. PSEUDO_DMA_DIRECT2D -> DMA_DIRECT2D) so they
line up in this report.

Requires ``pyarrow`` (to read the profiler parquet) and the simulator package
on the path. pyarrow is not in the Brazil test-runtime, so run with a Python
that has both -- e.g. point PYTHONPATH at the built site-packages:

    PYTHONPATH=<env>/test-runtime/lib/python3.10/site-packages \\
        python3 -m neuron_kernel_neff_sim.eval.per_opcode <neff_dir>

Usage:
    python -m neuron_kernel_neff_sim.eval.per_opcode <neff_dir> [--min-hw-us 1.0]
"""
from __future__ import annotations

import argparse
import glob
import os
import statistics
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


def hw_vnc_scale(case_dir: str) -> int:
    """Number of physical cores the OpcodeSummary aggregates over (1 or 2).

    Reads ``vnc_enabled`` from Metadata.parquet. Falls back to 1 if the file
    or column is missing.
    """
    import pyarrow.parquet as pq

    mp = os.path.join(case_dir, "infer_result", "profiler_db", "Metadata.parquet")
    if not os.path.exists(mp):
        return 1
    try:
        # Avoid .to_pandas() — it pulls in pandas/numpy which conflict with the
        # Brazil test-runtime numpy when both are on the path. Read raw columns.
        col = pq.read_table(mp, columns=["vnc_enabled"]).column("vnc_enabled")
        return 2 if bool(col[0].as_py()) else 1
    except Exception:
        return 1


def load_hw_opcodes(case_dir: str) -> Optional[Dict[Tuple[str, str], Tuple[float, int]]]:
    """Load HW OpcodeSummary, returning {(engine, opcode): (active_ns, count)}
    normalized to a single pcore (divided by the VNC scale).
    """
    import pyarrow.parquet as pq

    p = os.path.join(case_dir, "infer_result", "profiler_db", "OpcodeSummary.parquet")
    if not os.path.exists(p):
        return None
    scale = float(hw_vnc_scale(case_dir))
    tbl = pq.read_table(
        p, columns=["engine", "opcode", "active_time_ns", "instruction_count"]
    )
    engines = tbl.column("engine").to_pylist()
    opcodes = tbl.column("opcode").to_pylist()
    active = tbl.column("active_time_ns").to_pylist()
    counts = tbl.column("instruction_count").to_pylist()
    out: Dict[Tuple[str, str], Tuple[float, int]] = {}
    for eng, op, act, cnt in zip(engines, opcodes, active, counts):
        op = str(op)
        # The sim emits both indirect and gather DMAs as one PSEUDO_EXTENSION
        # (indistinguishable in the decoded stream), so merge HW's two opcodes
        # into DMA_INDIRECT to compare against the merged sim bucket.
        if op == "DMA_GATHER_TRANSPOSE":
            op = "DMA_INDIRECT"
        key = (str(eng), op)
        prev = out.get(key, (0.0, 0))
        out[key] = (
            prev[0] + float(act) / scale,
            prev[1] + (int(cnt / scale) if cnt else 0),
        )
    return out


# DMA-queue / descriptor opcodes that HW attributes to the synthetic `sync`
# engine when issued from the SP engine, rather than to `scalar`. The SP
# physical engine carries both scalar compute (ACTIVATE) and DMA-queue
# coordination; the profiler splits the latter onto a separate `sync` track.
_SP_SYNC_OPCODES = frozenset({"DMA_DIRECT2D", "TENSOR_LOAD", "WRITE",
                              "DRAIN", "NOTIFY", "COMPARE_BRANCH",
                              "EVENT_SEMAPHORE_RANGE_CLEAR"})


def canonical_engine(name: str, opcode: str = "") -> str:
    """Map a simulator engine_name (PE0, DVE0, SP0, Pool0, ...) to the
    lowercase engine label used by OpcodeSummary.parquet.

    SP-engine DMA-queue/sequencer ops are relabeled to ``sync`` to match how
    the hardware profiler tracks them (see _SP_SYNC_OPCODES).
    """
    n = name.lower()
    if n.startswith("pe"):
        return "tensor"
    if n.startswith("dve"):
        return "vector"
    if n.startswith("sp"):
        return "sync" if opcode in _SP_SYNC_OPCODES else "scalar"
    if n.startswith("pool"):
        return "gpsimd"
    if n.startswith("sync"):
        return "sync"
    if n.startswith("activation"):
        return "scalar"
    return n


def simulate_opcodes(neff_path: str) -> Optional[Dict[Tuple[str, str], Tuple[float, int]]]:
    """Run the simulator and aggregate per-(engine, opcode) active time + count."""
    from ..neff_parser import parse_neff
    from ..isa_decoder import decode_binary
    from ..graph_builder import build_graph, DynamicLoopUnsupportedError
    from ..scheduler import Scheduler
    from ..cost_model import canonical_opcode

    neff = parse_neff(neff_path)
    decoded = {
        e: decode_binary(neff.engines[e].bin_data, e, "trn2")
        for e in neff.get_engine_names()
    }
    try:
        graph = build_graph(neff, decoded)
    except DynamicLoopUnsupportedError:
        return None
    host_input_count = sum(
        1 for v in neff.def_json.get("var", {}).values()
        if isinstance(v, dict) and v.get("type") in ("input", "remote")
    )
    sched = Scheduler(
        "trn2", vnc_count=neff.vnc_count,
        host_input_bytes=neff.input_bytes, host_input_count=host_input_count,
        host_output_bytes=neff.output_bytes, host_output_count=neff.output_count,
    )
    result = sched.simulate(graph)
    agg: Dict[Tuple[str, str], List[float]] = defaultdict(lambda: [0.0, 0])
    for ev in result.events:
        if ev is None:
            continue
        inst = graph.instructions[ev.instruction_index]
        op = canonical_opcode(inst.opcode)
        # An indirection-mode PSEUDO_EXTENSION is HW's DMA_INDIRECT /
        # DMA_GATHER_TRANSPOSE (the sim can't tell them apart, so it reports the
        # blended cost under the merged DMA_INDIRECT bucket — HW is merged the
        # same way in load_hw_opcodes so the comparison lines up).
        if op == "PSEUDO_EXTENSION" and (inst.extra or {}).get("dge_mode"):
            op = "DMA_INDIRECT"
        key = (canonical_engine(inst.engine_name, op), op)
        agg[key][0] += ev.latency_ns
        agg[key][1] += 1
    return {k: (v[0], int(v[1])) for k, v in agg.items()}


class _Acc:
    """Mutable per-(engine, opcode) accumulator (typed, for clarity)."""
    __slots__ = ("hw_ns", "sim_ns", "ratios")

    def __init__(self) -> None:
        self.hw_ns: float = 0.0
        self.sim_ns: float = 0.0
        self.ratios: List[float] = []


def analyze(neff_dir: str, min_hw_us: float = 1.0) -> List[dict]:
    """Aggregate per-opcode sim vs HW across all cases under neff_dir."""
    records: Dict[Tuple[str, str], _Acc] = defaultdict(_Acc)
    for case_dir in sorted(glob.glob(os.path.join(neff_dir, "out-*"))):
        hw = load_hw_opcodes(case_dir)
        if hw is None:
            continue
        neffs = sorted(glob.glob(os.path.join(case_dir, "*.neff"))
                       + glob.glob(os.path.join(case_dir, "file.neff")))
        if not neffs:
            continue
        try:
            sim = simulate_opcodes(neffs[0])
        except Exception:
            continue
        if sim is None:
            continue
        for k in set(hw) | set(sim):
            h_ns, _ = hw.get(k, (0.0, 0))
            s_ns, _ = sim.get(k, (0.0, 0))
            rec = records[k]
            rec.hw_ns += h_ns
            rec.sim_ns += s_ns
            if h_ns > 100 and s_ns > 100:
                rec.ratios.append((s_ns - h_ns) / h_ns * 100)
    rows = []
    for (engine, opcode), rec in records.items():
        if rec.hw_ns < min_hw_us * 1000:
            continue
        ratio = rec.sim_ns / rec.hw_ns if rec.hw_ns else 0.0
        med = statistics.median(rec.ratios) if rec.ratios else None
        rows.append({
            "engine": engine, "opcode": opcode,
            "hw_us": rec.hw_ns / 1e3, "sim_us": rec.sim_ns / 1e3,
            "ratio": ratio, "median_rel_pct": med, "n_paired": len(rec.ratios),
        })
    return sorted(rows, key=lambda r: -r["hw_us"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("neff_dir", help="Directory containing out-* case dirs")
    ap.add_argument("--min-hw-us", type=float, default=1.0,
                    help="Skip opcodes below this cumulative HW active time")
    args = ap.parse_args()
    rows = analyze(args.neff_dir, args.min_hw_us)
    print(f"{'engine':<10}{'opcode':<28}{'hw_us':>10}{'sim_us':>10}"
          f"{'ratio':>8}{'med_rel':>10}{'n':>6}")
    b5 = b10 = b20 = tot = 0
    for r in rows:
        mds = f"{r['median_rel_pct']:+.1f}%" if r["median_rel_pct"] is not None else "n/a"
        ras = f"{r['ratio']:.2f}x" if r["ratio"] else "—"
        print(f"{r['engine']:<10}{r['opcode']:<28}{r['hw_us']:>10.1f}"
              f"{r['sim_us']:>10.1f}{ras:>8}{mds:>10}{r['n_paired']:>6}")
        if r["hw_us"] >= 10 and r["ratio"]:
            tot += 1
            err = abs(r["ratio"] - 1) * 100
            b5 += err <= 5
            b10 += err <= 10
            b20 += err <= 20
    if tot:
        print(f"\nAccuracy (opcodes with HW>=10us): "
              f"±5% {b5}/{tot}  ±10% {b10}/{tot}  ±20% {b20}/{tot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
