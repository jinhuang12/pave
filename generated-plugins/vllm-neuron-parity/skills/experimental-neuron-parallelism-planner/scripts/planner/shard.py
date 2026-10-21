"""Section 2 -- sharding: the single owner of all parallelism divisors.

`divisor(cls, layer_type, cfg, regime)` answers "how many ranks share one copy of this
(class, type) piece?" -- per-device = aggregate / divisor. This is the ONLY layer that knows
TP/CP/DP/EP. It encodes, in one place (so the three §3 consumers cannot drift):

  - attention Q/O: full TP (decode / standard prefill) or TP/CP (apply_prefill_dcp prefill).
  - attention K/V weight: min(head_tp, n_kv) -- the replication ceiling.
  - dense MLP: full TP (framework-prescribed; EP re-groups only the routed experts -- §2.2).
  - routed experts: world = TP*DP  (= ep_degree * ep_tp), the only EP-sharded class.
  - embed / lm_head: full TP.
  - CP (cp_g): shards the KV cache (decode) / attention sequence-compute (prefill-DCP),
    per attention-type -- capped types (sliding window) -> 1 (our convention, §2.1).

PP is deferred (§2 scope); ShardConfig has no PP field. n_kv per attention-type is supplied
by the caller from the segment's arch group (kept out of this module so it stays arch-free).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ShardConfig:
    TP: int
    CP: int = 1
    DP: int = 1
    ep_enabled: bool = False
    ep_degree: Optional[int] = None   # expert-dim split; None => full EP (= world)

    @property
    def world(self) -> int:
        return self.TP * self.DP

    def ep_grid(self) -> tuple[int, int]:
        """(ep_degree, ep_tp) with ep_degree*ep_tp == world. Full EP => (world, 1)."""
        w = self.world
        ep_deg = w if self.ep_degree is None else int(self.ep_degree)
        ep_tp = max(w // max(ep_deg, 1), 1)
        return ep_deg, ep_tp


# regime is "decode" | "prefill" | "prefill_dcp" (the DI apply_prefill_dcp path)
Regime = str


def _head_tp(cfg: ShardConfig, regime: Regime) -> int:
    """The head-shard-axis size for attention weights: TP normally, TP/CP under prefill-DCP."""
    return max(cfg.TP // cfg.CP, 1) if regime == "prefill_dcp" else cfg.TP


def cp_g(cfg: ShardConfig, regime: Regime, cap: Optional[int], n_kv: int, is_mla: bool) -> int:
    """Effective CP for an attention-type's sequence (KV cache / prefill token compute).

      - capped (sliding-window) type -> 1 (constant cache; our convention, §2.1).
      - standard prefill -> 1 (whole-prompt KV at full TP, cache not CP-sharded).
      - decode / prefill-DCP -> min(CP, TP // n_kv), the replication CP reclaims (MLA: n_kv=1).
    """
    if cap is not None:
        return 1
    if regime == "prefill":
        return 1
    nkv = 1 if is_mla else max(n_kv, 1)
    return min(cfg.CP, max(cfg.TP // nkv, 1))


def weight_divisor(cls: str, cfg: ShardConfig, regime: Regime,
                   n_kv: int = 0, is_mla: bool = False) -> int:
    """Per-device WEIGHT divisor for a class (§2.1/§2.2)."""
    if cls == "attn_qo":
        return _head_tp(cfg, regime)
    if cls == "attn_kv":
        nkv = 1 if is_mla else max(n_kv, 1)
        return max(min(_head_tp(cfg, regime), nkv), 1)
    if cls == "dense_mlp":
        return cfg.TP                       # framework-prescribed full TP (§2.2)
    if cls == "routed_experts":
        return cfg.world if cfg.ep_enabled else cfg.TP
    if cls == "embed":
        return cfg.TP
    raise KeyError(f"unknown weight class {cls!r}")


def kv_cache_divisor(cfg: ShardConfig, regime: Regime,
                     cap: Optional[int], n_kv: int, is_mla: bool) -> int:
    """Per-device KV-CACHE divisor: head divisor x sequence (CP) divisor (§2.1).

    head divisor = min(head_tp, n_kv); sequence divisor = cp_g. In prefill-DCP head_tp=TP/CP;
    in decode/standard-prefill head_tp=TP.
    """
    nkv = 1 if is_mla else max(n_kv, 1)
    head_div = max(min(_head_tp(cfg, regime), nkv), 1)
    return head_div * cp_g(cfg, regime, cap, n_kv, is_mla)


def flop_divisor(cls: str, cfg: ShardConfig, regime: Regime,
                 cap: Optional[int] = None, n_kv: int = 0, is_mla: bool = False) -> int:
    """Per-device FLOP divisor (§2.3): the class's weight divisor, times cp_g in prefill
    (the token/sequence-compute split). attn_score borrows attn_qo's divisor.

    Decode: cp_g = 1 (one token). Prefill: cp_g shards the per-type sequence compute.
    """
    wclass = "attn_qo" if cls == "attn_score" else cls
    base = weight_divisor(wclass, cfg, regime, n_kv=n_kv, is_mla=is_mla)
    if regime in ("prefill", "prefill_dcp") and cls in (
            "attn_qo", "attn_kv", "attn_score", "dense_mlp", "routed_experts"):
        base *= cp_g(cfg, regime, cap, n_kv, is_mla)
    return base
