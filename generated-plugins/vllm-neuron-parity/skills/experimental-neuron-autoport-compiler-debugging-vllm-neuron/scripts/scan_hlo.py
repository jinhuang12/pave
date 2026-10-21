#!/usr/bin/env python3
"""Scan a graph.hlo proto for the bring-up hazards that are INVISIBLE in fxgraph.txt.

make_fx / torch.export / CPU-compile hide f64 weak-scalars and pad patterns; they only
materialize at HLO lowering. This reaches past the FX graph into the raw proto.

Usage:
  scan_hlo.py f64 graph.hlo    # F64 (element_type==12) constants -> source_file:line (NCC_ESPP004)
  scan_hlo.py pad graph.hlo    # XLA 'pad' ops + edge_padding (the NCC_ISMP902 _pad ICE)
  scan_hlo.py act graph.hlo    # transcendental opcode counts (8-activation-LUT ceiling, NCC_INLA001)
  scan_hlo.py groups graph.hlo # replica_groups on collectives (verify EP/TP topology)

Note: .square()/.pow(2)/.pow(2.0) ALL lower to HLO 'power'; only x*x lowers to 'multiply'
— so trust the 'act' opcode count, never the Python source form.
"""
import sys
from collections import Counter


def _load(path):
    """Import hlo_pb2 from whichever location the installed stack provides."""
    last = None
    for mod in ("libneuronxla.proto",
                "neuronxcc.thirdparty_libs.xla.service"):
        try:
            hlo_pb2 = __import__(mod, fromlist=["hlo_pb2"]).hlo_pb2
            m = hlo_pb2.HloModuleProto()
            with open(path, "rb") as f:
                m.ParseFromString(f.read())
            return m
        except Exception as e:  # noqa: BLE001
            last = e
    raise SystemExit(f"could not import hlo_pb2 or parse {path}: {last}")


def cmd_f64(m):
    n = 0
    for c in m.computations:
        for i in c.instructions:
            if i.shape.element_type == 12:  # F64
                print(i.metadata.source_file, i.metadata.source_line, i.name)
                n += 1
    print(f"# {n} F64 constants (NCC_ESPP004). Wrap each literal: torch.full((),v,dtype=w.dtype)")


def cmd_pad(m):
    n = 0
    for c in m.computations:
        for i in c.instructions:
            if i.opcode == "pad":
                # edge_padding lives on i.padding_config if present
                print(i.opcode, i.name)
                n += 1
    print(f"# {n} 'pad' ops (NCC_ISMP902). Eliminate F.pad / cat-of-zeros / Conv1d(padding) /")
    print("#   edge slice-assign x[...,-1:]=v / clamp-index+gather -> arange one-hot + torch.where")


def cmd_act(m):
    trans = ['power', 'multiply', 'sqrt', 'rsqrt', 'exponential',
             'log-plus-one', 'logistic', 'cosine', 'sine']
    c = Counter(i.opcode for comp in m.computations for i in comp.instructions)
    for op in trans:
        print(f"{op:14s} {c.get(op, 0)}")
    distinct = sum(1 for op in trans if op != 'multiply' and c.get(op, 0))
    print(f"# ~{distinct} distinct transcendental tables; hardware ceiling is 8 (NCC_INLA001).")
    print("# Drop 'power' by writing x*x (NOT .square()/.pow(2), which still emit 'power').")


def cmd_groups(m):
    seen = set()
    for c in m.computations:
        for i in c.instructions:
            rg = getattr(i, "replica_groups", None)
            if rg:
                key = tuple(tuple(g.replica_ids) for g in rg)
                if key not in seen:
                    seen.add(key)
                    print(i.opcode, key)
    print("# Verify EP/TP topology from the COMPILED graph, not Python group names.")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("f64", "pad", "act", "groups"):
        print(__doc__)
        raise SystemExit(2)
    mode, path = sys.argv[1], sys.argv[2]
    m = _load(path)
    {"f64": cmd_f64, "pad": cmd_pad, "act": cmd_act, "groups": cmd_groups}[mode](m)


if __name__ == "__main__":
    main()
