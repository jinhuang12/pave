"""Agent-facing cost query over the calibrated :class:`CostModel`.

Adapts a plain-dict NKI-ISA "op spec" into a :class:`SimInstruction`, runs the
calibrated cost model, and returns structured dicts (latency + initiation
interval, in nanoseconds). This is a *pure adapter + aggregator*: it adds NO
cost math of its own — every number comes from :class:`CostModel`.

Scope (v1): compute ops (Vector/Scalar/GpSimd), the Tensor engine
(MATMUL/MATMUL_MX/LDWEIGHTS), and MEMSET. DMA and collective ops are NOT
modeled and are rejected. Any ``(opcode, engine)`` pair that the cost model
does not calibrate is rejected rather than silently returning a generic
estimate — see :func:`build_instruction`.

CLI::

    python -m neuron_kernel_neff_sim.cost_query --spec '<json>' --target trn2
    python -m neuron_kernel_neff_sim.cost_query --spec-file ops.json
    echo '<json>' | python -m neuron_kernel_neff_sim.cost_query
    python -m neuron_kernel_neff_sim.cost_query --list-opcodes
    python -m neuron_kernel_neff_sim.cost_query --dump-rules

``--spec`` accepts a single op-spec object or a JSON list (a sequence).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

from .cost_model import (
    CostModel,
    PIPELINED_PE_OPS,
    TENSOR_ENGINE_OPS,
)
from .graph_builder import SimInstruction


class CostQueryError(ValueError):
    """Raised for an invalid or uncalibrated op spec."""


# ---------------------------------------------------------------------------
# Accept-list — derived LIVE from the cost model so recalibration flows through.
# ---------------------------------------------------------------------------

def _build_accept() -> Dict[str, List[str]]:
    """``opcode -> sorted list of engines`` the cost model calibrates.

    Sources, in order: the per-opcode rule table, the Tensor-engine matmul
    paths (MATMUL/MATMUL_MX/LDWEIGHTS), and MEMSET on Vector.
    """
    accept: Dict[str, set] = {}
    for opcode, engine in CostModel._OPCODE_RULES:
        accept.setdefault(opcode, set()).add(engine)
    for opcode in TENSOR_ENGINE_OPS | PIPELINED_PE_OPS:
        accept.setdefault(opcode, set()).add("Tensor")
    # MEMSET has a dedicated calibrated model (_memset_cost) on Vector.
    accept.setdefault("MEMSET", set()).add("Vector")
    return {op: sorted(engs) for op, engs in accept.items()}


_ACCEPT: Dict[str, List[str]] = _build_accept()

# DMA-family opcodes get a clearer "not modeled in v1" message than the generic
# uncalibrated rejection.
_DMA_PREFIXES = ("DMA", "PSEUDO_DMA")
_OUT_OF_SCOPE = {"PSEUDO_TRIGGER_COLLECTIVE_2", "PSEUDO_TRIGGER_COLLECTIVE"}


def _is_dma_family(opcode: str) -> bool:
    return opcode.startswith(_DMA_PREFIXES)


def calibrated_engines(opcode: str) -> List[str]:
    """Engines on which ``opcode`` is calibrated (empty if none)."""
    return list(_ACCEPT.get(opcode, ()))


def has_rule(opcode: str, engine: str) -> bool:
    """True if ``(opcode, engine)`` is calibrated and accepted."""
    return engine in _ACCEPT.get(opcode, ())


# ---------------------------------------------------------------------------
# Spec -> SimInstruction
# ---------------------------------------------------------------------------

# 16-bit dtypes are not required here, but documenting the default keeps the
# speedup gate (all-SBUF + same 16-bit dtype) reachable from a terse spec.
def _spec_get(spec: Dict[str, Any], *keys, default=None) -> Any:
    for k in keys:
        if k in spec:
            return spec[k]
    return default


def _spec_n(spec: Dict[str, Any]) -> int:
    """Free-dim element count (N), defaulting to 1."""
    val = _spec_get(spec, "N", "elements_per_partition", default=1)
    return int(val) if val is not None else 1


def _validate_opcode_engine(opcode: str, engine: str) -> None:
    """Raise CostQueryError if ``(opcode, engine)`` is not accepted."""
    if has_rule(opcode, engine):
        return
    if _is_dma_family(opcode):
        raise CostQueryError(
            f"{opcode!r} is a DMA opcode — DMA cost is not modeled in v1."
        )
    if opcode in _OUT_OF_SCOPE:
        raise CostQueryError(
            f"{opcode!r} is a collective op — not modeled in v1."
        )
    engs = calibrated_engines(opcode)
    if engs:
        raise CostQueryError(
            f"{opcode!r} is not calibrated on engine {engine!r}; "
            f"it is calibrated on {', '.join(engs)}."
        )
    raise CostQueryError(
        f"{opcode!r} is not calibrated on any engine — refusing to estimate."
    )


def build_instruction(spec: Dict[str, Any], engine: Optional[str] = None) -> SimInstruction:
    """Validate ``spec`` and build a :class:`SimInstruction`.

    ``engine`` overrides ``spec['engine']`` (used by the fan-out path to pin
    one calibrated engine at a time). Raises :class:`CostQueryError` for a
    missing/uncalibrated opcode or an uncalibrated ``(opcode, engine)`` pair.
    """
    opcode = spec.get("opcode")
    if not opcode:
        raise CostQueryError("op spec is missing required field 'opcode'.")
    opcode = str(opcode)

    eng = engine if engine is not None else spec.get("engine")
    if eng is None:
        raise CostQueryError(
            f"op spec for {opcode!r} has no 'engine' and none was supplied."
        )
    eng = str(eng)
    _validate_opcode_engine(opcode, eng)

    n = _spec_n(spec)
    dtype = str(spec.get("dtype", "bf16"))
    dst_dtype = str(spec.get("dst_dtype", ""))
    src_ms = str(spec.get("src_mem_space", "SB"))
    dst_ms = str(spec.get("dst_mem_space", "SB"))

    extra: Dict[str, Any] = {}
    if "matmul_k" in spec:
        extra["matmul_k"] = int(spec["matmul_k"])
    if spec.get("any_strided"):
        extra["any_strided"] = 1
    extra["src1_mem_space"] = str(spec.get("src1_mem_space", "SB"))
    extra["src1_dtype"] = str(spec.get("src1_dtype", dtype))
    if "weight_dtype" in spec:
        extra["weight_dtype"] = str(spec["weight_dtype"])

    return SimInstruction(
        index=0,
        engine_index=0,
        opcode=opcode,
        engine_name=eng,
        engine_type=eng,
        elements_per_partition=n,
        dtype=dtype,
        dst_dtype=dst_dtype,
        src_mem_space=src_ms,
        dst_mem_space=dst_ms,
        src_stride=int(spec.get("src_stride", 1)),
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------

def _cost_one(model: CostModel, spec: Dict[str, Any], engine: str) -> Dict[str, Any]:
    inst = build_instruction(spec, engine=engine)
    return {
        "engine": engine,
        # ns rounded to integers: the model is calibrated to ~tens-of-percent,
        # so sub-ns precision is noise (and costs agent tokens).
        "latency_ns": round(model.compute_latency_ns(inst)),
        "ii_ns": round(model.compute_ii_ns(inst)),
    }


def query_op(
    spec: Dict[str, Any],
    *,
    target: str = "trn2",
    vnc_count: int = 1,
    cc_world_size: int = 1,
    model: Optional[CostModel] = None,
) -> Dict[str, Any]:
    """Cost a single op.

    If ``spec`` pins an ``engine``, returns one result. If ``engine`` is
    omitted, fans out: returns one result per calibrated engine for that
    opcode (a "menu" — single-engine opcodes yield exactly one entry).
    """
    opcode = spec.get("opcode")
    if not opcode:
        raise CostQueryError("op spec is missing required field 'opcode'.")
    opcode = str(opcode)
    model = model or CostModel(target=target, vnc_count=vnc_count,
                               cc_world_size=cc_world_size)
    # Echo only a caller-supplied label (when it carries info beyond the
    # opcode); inputs the caller already knows (N/dtype/etc.) are not echoed.
    explicit_name = spec.get("name")
    label = {"name": explicit_name} if explicit_name and explicit_name != opcode else {}

    engine = spec.get("engine")
    if engine is not None:
        r = _cost_one(model, spec, engine)
        return {
            "opcode": opcode, "engine": engine, "target": target,
            "latency_ns": r["latency_ns"], "ii_ns": r["ii_ns"], **label,
        }

    # Fan-out: one result per calibrated engine.
    engs = calibrated_engines(opcode)
    if not engs:
        # Reuse the validation path for a precise DMA / out-of-scope / unknown msg.
        _validate_opcode_engine(opcode, engine="<unspecified>")
    results = [_cost_one(model, spec, e) for e in engs]
    return {"opcode": opcode, "target": target, "results": results, **label}


def query_sequence(
    specs: List[Dict[str, Any]],
    *,
    target: str = "trn2",
    vnc_count: int = 1,
    cc_world_size: int = 1,
) -> Dict[str, Any]:
    """Cost a sequence of ops.

    Returns per-op costs plus *raw* per-engine II and latency sums. The sums
    are pure arithmetic (Σ over ops on an engine) — NOT a wall-time estimate.
    Real wall time depends on cross-engine dependencies, scheduling overlap,
    and memory-capacity serialization, none of which this models; for that,
    simulate a full NEFF (``python -m neuron_kernel_neff_sim.cli``).

    Every op must resolve to exactly one engine: a multi-engine opcode without
    an explicit ``engine`` is rejected (run a single-op query first to pick).
    """
    model = CostModel(target=target, vnc_count=vnc_count,
                      cc_world_size=cc_world_size)
    ops: List[Dict[str, Any]] = []
    per_engine_ii: Dict[str, float] = {}
    per_engine_lat: Dict[str, float] = {}

    for i, spec in enumerate(specs):
        opcode = spec.get("opcode")
        if not opcode:
            raise CostQueryError(f"op #{i} is missing required field 'opcode'.")
        engine = spec.get("engine")
        if engine is None:
            engs = calibrated_engines(opcode)
            if len(engs) == 1:
                engine = engs[0]
            elif len(engs) > 1:
                raise CostQueryError(
                    f"op #{i} ({opcode!r}) is calibrated on multiple engines "
                    f"({', '.join(engs)}); specify 'engine' to include it in a "
                    "sequence."
                )
            else:
                _validate_opcode_engine(opcode, engine="<unspecified>")
        engine = str(engine)
        op = query_op(dict(spec, engine=engine), target=target, model=model)
        per_engine_ii[engine] = per_engine_ii.get(engine, 0.0) + op["ii_ns"]
        per_engine_lat[engine] = per_engine_lat.get(engine, 0.0) + op["latency_ns"]
        # target is reported once at the sequence level — drop the per-op echo.
        op.pop("target", None)
        ops.append(op)

    return {
        "ops": ops,
        "per_engine_ii_ns": {e: round(v) for e, v in per_engine_ii.items()},
        "per_engine_latency_ns": {e: round(v) for e, v in per_engine_lat.items()},
        "target": target,
    }


# ---------------------------------------------------------------------------
# Reference dumps (for symbolic-mode agent reasoning / drift-proof catalogs)
# ---------------------------------------------------------------------------

def list_opcodes() -> List[Dict[str, str]]:
    """Accepted ``(opcode, engine)`` pairs."""
    return [
        {"opcode": op, "engine": eng}
        for op in sorted(_ACCEPT)
        for eng in _ACCEPT[op]
    ]


def dump_rules() -> List[Dict[str, Any]]:
    """Per-opcode calibrated constants (floor/slope) from the rule table,
    plus the matmul/memset paths flagged as model-driven."""
    out: List[Dict[str, Any]] = []
    for (opcode, engine), rule in sorted(CostModel._OPCODE_RULES.items()):
        out.append({
            "opcode": opcode, "engine": engine,
            "floor_ns": rule.get("floor"),
            "slope_ns_per_elem": rule.get("slope"),
            "can_2x": rule.get("can_2x", False),
            "can_4x": rule.get("can_4x", False),
        })
    for opcode in sorted(TENSOR_ENGINE_OPS | PIPELINED_PE_OPS):
        out.append({"opcode": opcode, "engine": "Tensor", "model": "matmul (shape/dtype-driven)"})
    out.append({"opcode": "MEMSET", "engine": "Vector", "model": "memset (free-elem-driven)"})
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m neuron_kernel_neff_sim.cost_query",
        description=(
            "Query the calibrated NKI-ISA cost model. Returns latency and "
            "initiation interval (ns) for a single op, an engine fan-out menu, "
            "or a sequence. Compute + Tensor + MEMSET only; DMA/collectives "
            "and uncalibrated opcodes are rejected."
        ),
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--spec", help="Inline JSON: single op-spec or a list (sequence).")
    src.add_argument("--spec-file", help="Path to a JSON file with a spec or list.")
    p.add_argument("--target", default="trn2", choices=["trn1", "trn2", "trn3"])
    p.add_argument("--vnc-count", type=int, default=1)
    p.add_argument("--cc-world-size", type=int, default=1)
    p.add_argument("--list-opcodes", action="store_true",
                   help="Print accepted (opcode, engine) pairs as JSON and exit.")
    p.add_argument("--dump-rules", action="store_true",
                   help="Print per-opcode calibrated constants as JSON and exit.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_opcodes:
        print(json.dumps(list_opcodes(), indent=2))
        return 0
    if args.dump_rules:
        print(json.dumps(dump_rules(), indent=2))
        return 0

    if args.spec is not None:
        raw = args.spec
    elif args.spec_file is not None:
        with open(args.spec_file) as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    if not raw.strip():
        print("error: no spec provided (use --spec / --spec-file / stdin).",
              file=sys.stderr)
        return 2

    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 2

    kw = dict(target=args.target, vnc_count=args.vnc_count,
              cc_world_size=args.cc_world_size)
    try:
        if isinstance(spec, list):
            result = query_sequence(spec, **kw)
        elif isinstance(spec, dict):
            result = query_op(spec, **kw)
        else:
            print("error: spec must be a JSON object or list.", file=sys.stderr)
            return 2
    except CostQueryError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # Compact (no whitespace) — query results are agent-consumed; ~40% fewer
    # tokens than pretty-printed. (The --list-opcodes/--dump-rules catalogs are
    # also read by humans/doc-writers, so those stay indented above.)
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
