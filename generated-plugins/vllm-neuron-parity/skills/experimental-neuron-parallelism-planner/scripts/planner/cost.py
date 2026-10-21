"""Section 3 -- per-device totals + cost model.

per_device_totals(segments, cfg, regime, op, meta) -> {component: Triple}
  Combines §1 segments x §2 divisors x operating point (S, bs). One uniform per-segment form,
  eval factors gated by quantity (§3.1 "what scales what"). Sums into the three components
  (attention / ffn / lm_head), each a (weight_bytes, kv_bytes, flops) Triple.

step_time(triples, hw, comm_ms) -> StepCost
  Per component: max((weight+kv)/BW, flop/TE)   -- roofline, load = weights+KV summed (§3.2).
  Step: sum over components + comm              -- serialized, not pipelined (§3.2).

meta : {attn_type_label -> (n_kv, is_mla)} so the divisor layer can apply the KV ceiling /
       cp_g per attention-type without cost.py importing arch internals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from planner.segments import Segment
from planner import shard
from planner.shard import ShardConfig, Regime


@dataclass
class Triple:
    weight_bytes: float = 0.0
    kv_bytes: float = 0.0
    flops: float = 0.0
    layers: int = 0                # multiplicity (layer count) for this (component, type)

    def __iadd__(self, o: "Triple") -> "Triple":
        self.weight_bytes += o.weight_bytes
        self.kv_bytes += o.kv_bytes
        self.flops += o.flops
        self.layers += o.layers
        return self

    @property
    def ai(self) -> float:
        """Arithmetic intensity: FLOPs / (weight+KV bytes read from HBM) -- the roofline
        x-axis for this triple. A property (derived from the triple's own fields), so it is
        defined once here in §3 and consumed, never recomputed, downstream."""
        denom = self.weight_bytes + self.kv_bytes
        return self.flops / denom if denom else 0.0

    def times(self, hw) -> dict:
        """load_ms (weights+KV / HBM BW), flop_ms (FLOPs / TE peak), and time=max (§3.2)."""
        load_ms = (self.weight_bytes + self.kv_bytes) / hw.hbm_bw_bytes_per_s * 1e3
        flop_ms = self.flops / hw.te_peak_flops * 1e3
        return {"load_ms": load_ms, "flop_ms": flop_ms, "time_ms": max(load_ms, flop_ms),
                "per_layer_ms": max(load_ms, flop_ms) / self.layers if self.layers else 0.0,
                "ai": self.ai,
                "bound": ("kv_load" if self.kv_bytes > self.weight_bytes else "weight_load")
                         if load_ms >= flop_ms else "flop"}


@dataclass
class OperatingPoint:
    S: int
    bs: int
    regime: Regime = "decode"           # decode | prefill | prefill_dcp
    sampled_tokens: int = 0             # lm_head positions; 0 => derive (one per sequence = bs)

    @property
    def is_prefill(self) -> bool:
        return self.regime in ("prefill", "prefill_dcp")

    @property
    def n_tok(self) -> int:
        return self.S * self.bs if self.is_prefill else self.bs

    @property
    def lm_head_tokens(self) -> int:
        if self.sampled_tokens:
            return self.sampled_tokens
        # lm_head projects only the SAMPLED positions (a real stack index_selects the
        # sampling positions before the logit projection -- vllm-neuron gemma4 model.py):
        #   prefill -> the last position per sequence only = bs;
        #   decode  -> every decoded position = n_tok (q_len*bs; q_len=1 today, >1 for MTP).
        return self.bs if self.is_prefill else self.n_tok


def eff_ctx(cap: Optional[int], S: int) -> int:
    """Canonical §3 derived quantity: tokens a segment caches/attends over. cap None => full S."""
    return S if cap is None else min(S, cap)


def _meta_for(seg: Segment, meta: Dict[str, Tuple[int, bool]]) -> Tuple[int, bool]:
    return meta.get(seg.layer_type, (0, False))


def per_type_totals(
    weight_segs: List[Segment],
    kv_segs: List[Segment],
    flop_segs: List[Segment],
    cfg: ShardConfig,
    op: OperatingPoint,
    meta: Dict[str, Tuple[int, bool]],
    dtype_bytes: int,
) -> Dict[Tuple[str, str], Triple]:
    """§3.1 step 2: per-device (weight, kv, flop) keyed by (component, layer_type) -- e.g.
    ('attention','sliding'), ('attention','global'), ('ffn','dense'), ('lm_head',''). This is
    the finest aggregation the design defines; per-component (step 3) is a roll-up of it
    (`roll_up_components`). Also records `layers` (multiplicity) per key for per-layer views."""
    out: Dict[Tuple[str, str], Triple] = defaultdict(Triple)
    layers: Dict[Tuple[str, str], int] = defaultdict(int)

    def K(s):
        return (s.component, s.layer_type)

    for s in weight_segs:
        n_kv, is_mla = _meta_for(s, meta)
        d = shard.weight_divisor(s.cls, cfg, op.regime, n_kv=n_kv, is_mla=is_mla)
        out[K(s)].weight_bytes += s.multiplicity * s.unit * dtype_bytes / d
        layers[K(s)] = max(layers[K(s)], s.multiplicity)

    for s in kv_segs:
        n_kv, is_mla = _meta_for(s, meta)
        d = shard.kv_cache_divisor(cfg, op.regime, s.cap, n_kv, is_mla)
        out[K(s)].kv_bytes += s.multiplicity * s.unit / d * eff_ctx(s.cap, op.S) * op.bs
        layers[K(s)] = max(layers[K(s)], s.multiplicity)

    for s in flop_segs:
        n_kv, is_mla = _meta_for(s, meta)
        d = shard.flop_divisor(s.cls, cfg, op.regime, cap=s.cap, n_kv=n_kv, is_mla=is_mla)
        tokens = op.lm_head_tokens if s.sampled_only else op.n_tok
        val = s.multiplicity * s.unit / d * tokens
        if s.ctx_scaled:
            val *= eff_ctx(s.cap, op.S)
        out[K(s)].flops += val
        layers[K(s)] = max(layers[K(s)], s.multiplicity)

    for k in out:
        out[k].layers = layers[k]
    return dict(out)


def roll_up_components(per_type: Dict[Tuple[str, str], Triple]) -> Dict[str, Triple]:
    """§3.1 step 3: sum per-(component, layer_type) triples into the three components."""
    out: Dict[str, Triple] = {"attention": Triple(), "ffn": Triple(), "lm_head": Triple()}
    for (comp, _lt), t in per_type.items():
        out[comp] += t
    return out


def per_device_totals(weight_segs, kv_segs, flop_segs, cfg, op, meta, dtype_bytes):
    """Per-device (weight, kv, flop) per component (§3.1 step 3). Roll-up of per_type_totals."""
    return roll_up_components(
        per_type_totals(weight_segs, kv_segs, flop_segs, cfg, op, meta, dtype_bytes))


# --- §3.2 cost model -----------------------------------------------------------

@dataclass
class ComponentCost:
    name: str
    load_ms: float
    flop_ms: float
    weight_bytes: float
    kv_bytes: float
    flops: float = 0.0

    @property
    def time_ms(self) -> float:
        return max(self.load_ms, self.flop_ms)

    @property
    def ai(self) -> float:
        """Arithmetic intensity for this component: FLOPs / (weight+KV bytes)."""
        denom = self.weight_bytes + self.kv_bytes
        return self.flops / denom if denom else 0.0

    @property
    def binding(self) -> str:
        # load vs flop; within load, weight vs kv is diagnostic only (§3.2).
        if self.flop_ms > self.load_ms:
            return "flop"
        return "kv_load" if self.kv_bytes > self.weight_bytes else "weight_load"


@dataclass
class StepCost:
    components: List[ComponentCost]
    comm_ms: float

    @property
    def step_ms(self) -> float:
        return sum(c.time_ms for c in self.components) + self.comm_ms

    @property
    def binding(self) -> str:
        """The component/term contributing the most time (comm included)."""
        best = max(self.components, key=lambda c: c.time_ms)
        if self.comm_ms > best.time_ms:
            return "comm"
        return f"{best.name}:{best.binding}"

    def as_dict(self) -> dict:
        return {
            "step_ms": self.step_ms,
            "comm_ms": self.comm_ms,
            "binding": self.binding,
            "components": {
                c.name: {"time_ms": c.time_ms, "load_ms": c.load_ms,
                         "flop_ms": c.flop_ms, "ai": c.ai, "binding": c.binding}
                for c in self.components
            },
        }


def step_time(triples: Dict[str, Triple], hw, comm_ms: float) -> StepCost:
    """§3.2: per-component roofline max(load, flop), then serialize (sum) + comm."""
    bw = hw.hbm_bw_bytes_per_s
    te = hw.te_peak_flops
    comps = []
    for name in ("attention", "ffn", "lm_head"):
        t = triples[name]
        load_ms = (t.weight_bytes + t.kv_bytes) / bw * 1e3
        flop_ms = t.flops / te * 1e3
        comps.append(ComponentCost(name, load_ms, flop_ms, t.weight_bytes, t.kv_bytes, t.flops))
    return StepCost(comps, comm_ms)
