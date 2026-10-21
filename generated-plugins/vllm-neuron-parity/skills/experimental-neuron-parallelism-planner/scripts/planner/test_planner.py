"""Regression + unit tests for the refactored planner engine (§1/§2/§3).

Anchors:
  - non-EP per-device weight/KV totals match the OLD engine to the byte (segments preserve
    the aggregation; shard preserves the divisors).
  - EP per-device weights differ from the old engine by EXACTLY the dense-MLP correction
    (old replicated dense on ep_tp; new shards on full TP per the framework prescription).
  - decode FLOPs match the OLD engine minus lm_head (granite exact); gemma differs only by
    the k_eq_v v_proj FLOP unification (toward the oracle).
  - §3.2 cost model: per-component roofline max(load, flop), then serialize (sum) + comm.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # scripts/ on path

from planner.build import build_bundle, ep_default_enabled
from planner.cost import per_device_totals, step_time, OperatingPoint, eff_ctx
from planner.plan import min_kv_heads
from planner.shard import ShardConfig, weight_divisor, kv_cache_divisor, cp_g
from entitlement_formulas import flops_per_token, corrected_weight_bytes

SPECS = {
    "granite": os.path.join(HERE, "..", "configs", "granite4-8b-run.json"),
    "gemma31b": os.path.join(HERE, "..", "configs", "gemma4-31b-run.json"),
    "gemma_a4b": os.path.join(HERE, "..", "configs", "gemma4-a4b-run.json"),
}

# Frozen per-device baselines captured from the prior engine (the one this replaced), used as
# the regression reference so the tests stand alone -- no dependency on deleted code. Non-EP
# per-device weights and KV bytes must match to the byte; the EP (a4b) weights differ only by
# the intended dense-MLP correction (asserted separately below).
BASELINE = {
    "granite": {"pdw": {4: 4190109696.0, 8: 2095054848.0, 16: 1089470464.0, 32: 586678272.0},
                "kv": {(8, 1): 1342177280.0, (16, 2): 671088640.0}},
    "gemma31b": {"pdw": {4: 15348006912.0, 8: 7701528576.0, 16: 3878289408.0, 32: 2104295424.0},
                 "kv": {(8, 1): 2181038080.0, (16, 2): 1090519040.0}},
    "gemma_a4b": {"pdw_ep": {8: 7252934656.0},   # prior engine's EP weights (dense on ep_tp)
                  "kv": {(8, 1): 880803840.0, (16, 2): 545259520.0}},
}


def _pdw(b, cfg, S=8192, bs=8, prefill=False):
    tr = per_device_totals(b.weight_segs, b.kv_segs, b.flop_segs, cfg,
                           OperatingPoint(S, bs, "decode" if not prefill else "prefill"),
                           b.meta, b.arch.dtype_bytes)
    return sum(t.weight_bytes for t in tr.values()), sum(t.kv_bytes for t in tr.values())


@pytest.mark.parametrize("name", ["granite", "gemma31b"])
def test_nonep_weight_bytes_match_baseline(name):
    b = build_bundle(SPECS[name])
    for TP in (4, 8, 16, 32):
        cfg = ShardConfig(TP=TP, CP=1, DP=1, ep_enabled=False)
        new_w, _ = _pdw(b, cfg)
        assert abs(new_w - BASELINE[name]["pdw"][TP]) < 1, (name, TP, new_w)


@pytest.mark.parametrize("name", list(SPECS))
def test_kv_bytes_match_baseline(name):
    b = build_bundle(SPECS[name])
    for (TP, CP), old_kv in BASELINE[name]["kv"].items():
        cfg = ShardConfig(TP=TP, CP=CP, DP=1, ep_enabled=ep_default_enabled(b.arch))
        _, new_kv = _pdw(b, cfg)
        assert abs(new_kv - old_kv) <= max(1.0, old_kv * 1e-9), (name, TP, CP, new_kv, old_kv)


def test_total_weight_matches_corrected():
    for name, path in SPECS.items():
        b = build_bundle(path)
        cfg = ShardConfig(TP=1, CP=1, DP=1, ep_enabled=False)
        new_w, _ = _pdw(b, cfg)
        assert abs(new_w - corrected_weight_bytes(b.arch)) < 1, name


def test_ep_dense_mlp_correction_is_exact():
    """gemma_a4b: new (dense on full TP) vs the prior engine (dense on ep_tp=1) differ by
    EXACTLY dense*(1-1/TP) -- the intended EP dense-MLP correction."""
    b = build_bundle(SPECS["gemma_a4b"])
    a = b.arch
    dense = sum(s.multiplicity * s.unit for s in b.weight_segs if s.cls == "dense_mlp") * a.dtype_bytes
    cfg = ShardConfig(TP=8, CP=1, DP=1, ep_enabled=True)
    new_w, _ = _pdw(b, cfg)
    old_w = BASELINE["gemma_a4b"]["pdw_ep"][8]
    assert abs((old_w - new_w) - dense * (1 - 1 / 8)) < 1000


def test_decode_flops_match_old_minus_lmhead_granite():
    b = build_bundle(SPECS["granite"])
    a = b.arch
    S, bs = 4096, 8
    cfg = ShardConfig(TP=1, CP=1, DP=1, ep_enabled=False)
    tr = per_device_totals(b.weight_segs, b.kv_segs, b.flop_segs, cfg,
                           OperatingPoint(S, bs, "decode"), b.meta, a.dtype_bytes)
    new_flops = sum(t.flops for t in tr.values())
    lm = 2 * a.vocab_size * a.hidden_size * bs
    old = flops_per_token(a, S) * bs
    assert abs((new_flops - lm) - old) < 1e-3 * old   # exact for plain GQA


def test_swa_cp_convention():
    """Capped (sliding) attention-type -> cp_g == 1; global -> reclaims TP/n_kv (our §2.1)."""
    cfg = ShardConfig(TP=16, CP=4, DP=1)
    assert cp_g(cfg, "decode", cap=1024, n_kv=8, is_mla=False) == 1        # sliding
    assert cp_g(cfg, "decode", cap=None, n_kv=4, is_mla=False) == 4        # global, TP/n_kv=4
    assert cp_g(cfg, "decode", cap=None, n_kv=8, is_mla=False) == 2        # global, TP/n_kv=2


def test_prefill_dcp_reshards_attention_weights():
    """apply_prefill_dcp: qo weight divisor drops to TP/CP; decode stays TP."""
    cfg = ShardConfig(TP=8, CP=2, DP=1)
    assert weight_divisor("attn_qo", cfg, "decode") == 8
    assert weight_divisor("attn_qo", cfg, "prefill_dcp") == 4
    # kv cache divisor: decode min(TP,n_kv)*CP ; prefill-dcp min(TP/CP,n_kv)*CP
    assert kv_cache_divisor(cfg, "decode", None, 2, False) == min(8, 2) * 2      # =4
    assert kv_cache_divisor(cfg, "prefill_dcp", None, 2, False) == min(4, 2) * 2  # =4


def test_step_time_is_additive_across_components():
    """§3.2: step = sum of per-component max(load,flop) + comm, NOT a global max."""
    b = build_bundle(SPECS["granite"])
    cfg = ShardConfig(TP=8, CP=1, DP=1, ep_enabled=False)
    tr = per_device_totals(b.weight_segs, b.kv_segs, b.flop_segs, cfg,
                           OperatingPoint(8192, 8, "decode"), b.meta, b.arch.dtype_bytes)
    sc = step_time(tr, b.hw, comm_ms=0.5)
    manual = sum(max(c.load_ms, c.flop_ms) for c in sc.components) + 0.5
    assert abs(sc.step_ms - manual) < 1e-9
    # additive => step exceeds any single component (unlike the old global max)
    assert sc.step_ms > max(c.time_ms for c in sc.components)


def test_eff_ctx_caps_at_window():
    assert eff_ctx(None, 8192) == 8192
    assert eff_ctx(1024, 8192) == 1024
    assert eff_ctx(1024, 512) == 512


def test_plan_serving_modes():
    """colocated gates on BOTH TTFT and ITL, one config/bucket; disaggregated emits separate
    ITL-gated decode + TTFT-gated prefill pools. Both expose the full decode grid."""
    from planner.plan import plan
    b = build_bundle(SPECS["granite"])

    pc = plan(b, serving="colocated")
    assert set(pc["selected"]) == set(pc["buckets"])
    for S, t in pc["selected"].items():
        assert t is not None and {"ttft_ms", "ttft_ceiling_ms", "itl_ceiling_ms", "sla_met",
                                  "step_ms", "otps", "max_bs"} <= set(t)
        assert len(pc["decode_grid"][S]) > 0                 # full grid shown

    pd = plan(b, serving="disaggregated")
    assert "decode_pool" in pd and "prefill_pool" in pd
    for S in pd["buckets"]:
        assert pd["decode_pool"][S] is not None
        # disagg prefill pool is non-replicating: TP <= min KV heads (granite n_kv=8)
        assert pd["prefill_pool"][S]["TP"] <= min_kv_heads(b.arch)


def test_colocated_two_gate_selection():
    """Colocated VALID = meets both TTFT and ITL ceilings; among valid, max per-node throughput.
    If the valid set is non-empty the pick is in it and maximizes tput/node."""
    from planner.plan import (plan, build_grid, decode_grid, ttft_by_config, ttft_ceiling_ms)
    b = build_bundle(SPECS["gemma31b"])
    itl_ceil = b.decode_itl_ceiling_ms
    p = plan(b, serving="colocated", decode_itl_ceiling_ms=itl_ceil)
    grid = build_grid(b)
    for S, t in p["selected"].items():
        ttfts = ttft_by_config(b, grid, S)
        tc = ttft_ceiling_ms(S, b.prefill_itps_floor)
        dgrid = decode_grid(b, grid, S)
        valid = [r for r in dgrid
                 if ttfts[(r["TP"], r["CP"], r.get("DP", 1))] <= tc and r["step_ms"] <= itl_ceil]
        if valid:
            assert t["sla_met"]
            assert t["ttft_ms"] <= tc and t["step_ms"] <= itl_ceil     # meets BOTH gates
            assert abs(t["tput_per_node"] - max(r["tput_per_node"] for r in valid)) < 1e-6
        else:
            assert not t["sla_met"]                                    # no config satisfied both


# --- Change A: configurable usable HBM fraction --------------------------------

def test_usable_fraction_default_unchanged():
    """A bundle built without an override keeps the 0.90 default: usable_hbm == capacity*0.90.
    Guards backward-compat (the shipped run-specs set no usable_hbm_fraction)."""
    from planner.plan import usable_hbm, USABLE_FRACTION
    b = build_bundle(SPECS["granite"])
    assert b.hw.usable_fraction == USABLE_FRACTION == 0.90
    assert usable_hbm(b.hw) == b.hw.hbm_capacity_bytes_per_core * 0.90


def test_usable_fraction_scales_budget():
    """usable_hbm scales linearly with hw.usable_fraction, and a lower fraction cannot raise
    (only shrink or hold) max_feasible_bs for a fixed config."""
    from planner.plan import usable_hbm, max_feasible_bs
    b = build_bundle(SPECS["granite"])
    cfg = ShardConfig(TP=16, CP=2, DP=1, ep_enabled=False)

    b.hw.usable_fraction = 0.90
    full_budget, full_bs = usable_hbm(b.hw), max_feasible_bs(b, cfg, 8192, False)
    b.hw.usable_fraction = 0.45
    half_budget, half_bs = usable_hbm(b.hw), max_feasible_bs(b, cfg, 8192, False)

    assert abs(half_budget - full_budget / 2) < 1e-6      # linear in the fraction
    assert 0 < half_bs <= full_bs                          # tighter budget => no larger batch


# --- Change B: batch-swept interactivity landscape -----------------------------

def test_bs_ladder():
    """_bs_ladder: starts at 1, ends at max_bs, powers of two between, BS_GRANULARITY parity
    (except bs=1), strictly increasing, deduped."""
    from planner.plan import _bs_ladder, BS_GRANULARITY
    assert _bs_ladder(0) == []
    assert _bs_ladder(1) == [1]
    lad = _bs_ladder(538)
    assert lad[0] == 1 and lad[-1] == 538
    assert lad == sorted(set(lad))                          # strictly increasing, deduped
    assert all(v == 1 or v % BS_GRANULARITY == 0 for v in lad)   # parity (bs=1 exempt)


def test_decode_bs_sweep_monotone_and_pareto():
    """Each config's bs sweep is monotone in bs on both tput_per_node and step_ms; the
    pareto_pn flag matches an independent is_pareto_pn recomputation; and each config's max-bs
    row coincides with its decode_grid row (the sweep tail == the throughput-capacity landscape)."""
    from planner.plan import build_grid, decode_bs_sweep, decode_grid, is_pareto_pn
    b = build_bundle(SPECS["granite"])
    grid = build_grid(b)
    S = 8192
    rows = decode_bs_sweep(b, grid, S)
    assert rows and all("peak_hbm_gb" in r for r in rows)

    # per-config monotonicity in bs (higher batch => >= throughput and >= ITL)
    by_cfg = {}
    for r in rows:
        by_cfg.setdefault((r["TP"], r["CP"], r["DP"]), []).append(r)
    for cs in by_cfg.values():
        cs = sorted(cs, key=lambda r: r["bs"])
        for a, b2 in zip(cs, cs[1:]):
            assert b2["tput_per_node"] >= a["tput_per_node"] - 1e-6
            assert b2["step_ms"] >= a["step_ms"] - 1e-9

    # pareto_pn flag is exactly the DP-normalized dominance over the whole sweep
    for r in rows:
        assert r["pareto_pn"] == is_pareto_pn(r, [o for o in rows if o is not r])

    # the max-bs row of each config equals its decode_grid (max-batch landscape) row
    dg = {(r["TP"], r["CP"], r["DP"]): r for r in decode_grid(b, grid, S)}
    for cs in by_cfg.values():
        top = max(cs, key=lambda r: r["bs"])
        d = dg[(top["TP"], top["CP"], top["DP"])]
        assert top["bs"] == d["max_bs"]
        assert abs(top["tput_per_node"] - d["tput_per_node"]) < 1e-6
        assert abs(top["step_ms"] - d["step_ms"]) < 1e-9
