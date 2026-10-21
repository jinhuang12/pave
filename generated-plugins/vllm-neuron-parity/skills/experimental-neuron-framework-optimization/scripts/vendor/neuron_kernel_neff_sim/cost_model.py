"""Analytical instruction cost model based on Initiation Interval (II).

Computes per-instruction execution latency in nanoseconds using formula-based
models. Constants are initially from architecture docs and will be calibrated
against profiled hardware traces.
"""

from typing import Dict, Optional, Tuple

from .graph_builder import SimInstruction
from .hardware_config import get_hardware_config


# FP8 data types that enable double-performance modes
FP8_TYPES = {"fp8", "fp8_e4m3", "fp8_e5m2", "fp8_e3m4", "float8",
             "fp8e4", "fp8e5", "fp8e3",  # neuron-disasm short forms
             "fp8_e4m3fn_x4", "fp8_e5m2_x4", "float4_e2m1fn_x4"}

# Instructions that run on the Tensor Engine
TENSOR_ENGINE_OPS = {"MATMUL", "MATMUL_MX"}

# LDWEIGHTS pipelines fully with the subsequent MATMUL on the PE's
# separate weight-load path. Its II is hidden behind MATMUL's II.
PIPELINED_PE_OPS = {"LDWEIGHTS"}

# DMA trigger opcodes (issue cost only; actual transfer is async)
DMA_TRIGGER_OPS = {"PSEUDO_DMA_TRIGGER", "DMA_TRIGGER", "PSEUDO_DMA_DIRECT2D"}
DMA_TRANSFER_OPS = {"DMA_TRANSFER"}

# Map simulator opcode names (often PSEUDO_* placeholders emitted by the
# decoder) to the canonical hardware opcode name reported in
# OpcodeSummary.parquet. Used both for cost attribution and so the
# per-opcode accuracy analysis lines up sim and HW under the same key.
HW_OPCODE_ALIAS: Dict[str, str] = {
    "PSEUDO_DMA_DIRECT2D": "DMA_DIRECT2D",
    "PSEUDO_TENSOR_LOAD": "TENSOR_LOAD",
}


def canonical_opcode(opcode: str) -> str:
    """Return the hardware-canonical opcode name for a sim opcode."""
    return HW_OPCODE_ALIAS.get(opcode, opcode)

# Ops that don't write to memory and therefore don't pay write_drain.
# ALU_OP and MOVE are register-only operations on GpSimd.
_NO_DRAIN_OPS = frozenset({"ALU_OP", "MOVE"})

# Control/sequencer instructions with near-zero cost. MOVE was previously here
# but profiles show GpSimd register-load MOVE costs ~37-55ns (not free); see
# STATIC_COST_CYCLES_BY_ENGINE override below.
CONTROL_OPS = {"NOP", "CORE_BARRIER", "CUSTOM_OP_HEADER", "CUSTOM_OP_PAYLOAD",
               "PSEUDO_BRANCH_LABEL",
               "PSEUDO_CORE_BARRIER"}

# Real durations for control-style ops measured in profiles (median ns).
# EVENT_SEMAPHORE: includes the cross-engine wait time but the visible
# instruction duration on the engine is the median below; longer waits
# are modeled as cross-engine deps.
CONTROL_OP_REAL_NS: Dict[str, float] = {
    "EVENT_SEMAPHORE": 100.0,  # median ~50ns; 2026-05-28 sweep refit (was 200)
    "SET_ORDERING_MODE": 40.0,
    # ACT_TABLE_LOAD is real compute on Scalar engine (~862ns/inst avg).
    # Loads activation function lookup tables before each activation.
    "ACT_TABLE_LOAD": 862.0,
    # EVENT_SEMAPHORE_RANGE_CLEAR: empirical 21-NEFF mean 260ns on Scalar.
    "EVENT_SEMAPHORE_RANGE_CLEAR": 260.0,
}

# Per-engine EVENT_SEMAPHORE costs (real medians vary widely by engine):
# Scalar engine waits average ~3µs, Vector/GpSimd ~600ns, Tensor ~250ns.
# Calibrated to real Instruction.parquet medians.
EVENT_SEMAPHORE_NS_BY_ENGINE: Dict[str, float] = {
    # 2026-05-29 reverted to small per-signal cost. Sim only emits
    # EVENT_SEMAPHORE on the signaling engine (count is ~15% of HW's
    # which counts both signal and wait events). Sim attributes wait
    # time to cross-engine deps in the scheduler, not the EVENT_SEMAPHORE
    # opcode. Per-engine parquet active_time/inst medians for *signaling*
    # use are around 100-200ns; using the high cross-engine averages
    # (561-6298ns/inst) double-counted.
    "Scalar": 200.0,
    "SP": 200.0,
    "Vector": 200.0,
    "DVE": 200.0,
    "GpSimd": 200.0,
    "Pool": 200.0,
    "Activation": 200.0,
    "Tensor": 100.0,
    "PE": 100.0,
    "sync": 200.0,
    "Sync": 200.0,
}

# Static costs (engine cycles): per nisa-performance-model.md, all
# Single-Engine instructions use Formula A: latency = cpe * N * 100/freq.
# There is NO per-instruction static overhead in the canonical model.
# Default static = 0 cycles. Per-opcode entries below are kept for the
# few opcodes whose per-instruction setup cost is documented elsewhere.
STATIC_COST_CYCLES: Dict[str, int] = {}

# Cycles per element per nisa-performance-model.md. Default for SingleEngine
# instructions is cpe=1. Exceptions:
CYCLES_PER_ELEMENT: Dict[str, float] = {
    # Vector engine (per nisa-performance-model.md table)
    "RECIPROCAL": 26.0,           # cpe=26
    "TENSOR_TENSOR_POWER": 4.0,   # cpe=4 on GpSimd
    # All other ops default to cpe=1 (handled in _compute_engine_cost)
}

# Per-engine static cost overrides for ops not in the canonical NISA
# model. Engine selection (Vector vs Scalar vs GpSimd) only changes
# the frequency for documented ops.
STATIC_COST_CYCLES_BY_ENGINE: Dict[tuple, int] = {
    # ALU_OP and MOVE on GpSimd: register-only ops (no element scaling
    # and no drain). HW shows ~37 and ~47 ns/inst respectively. With
    # cpe=0 and freq=120: static_cycles = ns * 1.2.
    ("GpSimd", "ALU_OP"): 44,    # 37 ns * 1.2
    ("GpSimd", "MOVE"): 56,      # 47 ns * 1.2
}

# Per-engine cycles-per-element overrides.
CYCLES_PER_ELEMENT_BY_ENGINE: Dict[tuple, float] = {
    # Per nisa-performance-model.md: most ops use cpe=1; specific exceptions
    # only. Reciprocal=26 (already in CYCLES_PER_ELEMENT). TensorCopy uses
    # speedup model (handled in _compute_perf_speedup).
    # ALU_OP and MOVE on GpSimd are register-only (no element scaling).
    ("GpSimd", "ALU_OP"): 0.0,
    ("GpSimd", "MOVE"): 0.0,
    # Vector LOAD_MASK_SELECT: fixed mask-register load (no element scaling).
    ("Vector", "LOAD_MASK_SELECT"): 0.0,
    # GpSimd TENSOR_SCALAR_AFFINE_SELECT: per docs the affine selector is a
    # constant operation regardless of N (canonical doc says cpe=1 but
    # silicon shows fixed ~250ns). Override to 0.
    ("GpSimd", "TENSOR_SCALAR_AFFINE_SELECT"): 0.0,
}

# Matmul MIN_II in engine cycles. Per `trn_hw_info.md` `min_wave=128`,
# and empirical measurement (12273 II samples across the 21-NEFF
# perf-analysis corpus) shows median II ≈ 27ns ≈ 65 cycles for streaming
# bf16 N=128 matmuls. The 144-cycle value used previously was
# significantly above the silicon throughput limit. Lowered to 65 to
# match the dominant streaming case.
# Floor II = 27ns: HW streaming bf16 N=128 MATMUL issues back-to-back at
# ~26-27ns (modal in Instruction.parquet start-to-start deltas). 27ns at the
# 2.40GHz tensor freq = 65 cycles. (Was 130/54ns, ~2x the silicon streaming
# rate.) Tiny-N matmuls floor here too.
MATMUL_MIN_II = 65
MATMUL_MX_MIN_II = 65
MATMUL_DOUBLE_ROW_MIN_II = 184


# Per-packet DMA latency/bandwidth per "DMA Latency Model" (Quip As7AA7nUa2qe).
# Silicon-validated reorder-buffer simulation of HBM→DMA→SBUF read+write path.
# Each entry: packet_size (B), per-engine packet latency (ns), per-engine BW (GB/s).
# Bandwidth saturates at ~17.4 GB/s per engine for packets >= 32KB.
# Below 32KB, throughput is latency-bound by sem update overhead.
_DMA_PACKET_TABLE = [
    # (packet_size_bytes, latency_ns, bandwidth_gbps_per_engine)
    (2048,    1346.42, 1.52),
    (4096,    1501.16, 2.73),
    (8192,    1641.00, 4.99),
    (16384,   1769.12, 9.26),
    (32768,   1882.43, 17.41),
    (65536,   3762.13, 17.42),
    (131072,  7524.08, 17.42),
    (262144, 15061.53, 17.41),
    (524288, 30093.66, 17.43),
]

# HBM→SB DMA effective bandwidth (GB/s) per (LNC, partition, free_bytes_per_partition)
# from silicon ubench (16MB load tests, ubench/HBM-to-SB Transfer Performance...csv,
# StaticDMA mode — closest match for the static qSPIO0 descriptors used in
# MLP TKG kernels). LNC=1 = single physical core; LNC=2 = both cores in parallel.
# Lookup is bilinear in (partition, free_bytes).
_HBM2SB_BW_TABLE = {
    # (lnc, partition, free_bytes) -> GB/s
    (1, 32, 32):     4.77, (1, 32, 64):    11.31, (1, 32, 128):   22.61,
    (1, 32, 256):   45.20, (1, 32, 512):   90.20, (1, 32, 1024): 179.49,
    (1, 32, 2048): 265.21, (1, 32, 4096): 281.62, (1, 32, 8192): 292.77,
    (1, 32, 16384): 296.88, (1, 32, 32768): 300.32,
    (1, 64, 32):    16.69, (1, 64, 64):    33.34, (1, 64, 128):   66.75,
    (1, 64, 256):  128.56, (1, 64, 512):  215.20, (1, 64, 1024): 272.01,
    (1, 64, 2048): 289.09, (1, 64, 4096): 294.22, (1, 64, 8192): 297.66,
    (1, 64, 16384): 298.92, (1, 64, 32768): 300.12,
    (1, 128, 32):   54.39, (1, 128, 64):  108.84, (1, 128, 128): 211.21,
    (1, 128, 256): 330.08, (1, 128, 512): 371.45, (1, 128, 1024): 391.54,
    (1, 128, 2048): 397.72, (1, 128, 4096): 393.43, (1, 128, 8192): 407.39,
    (1, 128, 16384): 411.69, (1, 128, 32768): 419.15,
    (2, 32, 32):    11.29, (2, 32, 64):    22.56, (2, 32, 128):   45.08,
    (2, 32, 256):   89.52, (2, 32, 512):  176.61, (2, 32, 1024): 341.10,
    (2, 32, 2048): 492.80, (2, 32, 4096): 526.24, (2, 32, 8192): 522.57,
    (2, 32, 16384): 525.87, (2, 32, 32768): 526.00,
    (2, 64, 32):    22.53, (2, 64, 64):    44.85, (2, 64, 128):   89.03,
    (2, 64, 256):  176.10, (2, 64, 512):  339.53, (2, 64, 1024): 487.30,
    (2, 64, 2048): 513.54, (2, 64, 4096): 518.57, (2, 64, 8192): 523.13,
    (2, 64, 16384): 524.45, (2, 64, 32768): 525.12,
    (2, 128, 32):   38.32, (2, 128, 64):   76.96, (2, 128, 128): 156.65,
    (2, 128, 256): 315.24, (2, 128, 512): 431.36, (2, 128, 1024): 498.11,
    (2, 128, 2048): 530.64, (2, 128, 4096): 583.07, (2, 128, 8192): 599.46,
    (2, 128, 16384): 580.87, (2, 128, 32768): 540.97,
}


def _hbm2sb_bw_gbps(partition: int, free_bytes: int, lnc: int) -> float:
    """Look up effective HBM→SBUF BW from silicon ubench table.

    Uses StaticDMA mode (matches qSPIO0 compiled descriptors). For
    arbitrary (partition, free_bytes), interpolates from the nearest
    table entries. lnc=1 means one physical core active, lnc=2 means
    both cores running in parallel.
    """
    # Snap partition to nearest available (32, 64, 128).
    avail_p = [32, 64, 128]
    p_clamp = max(avail_p, key=lambda x: -abs(x - partition)) \
              if partition not in avail_p else partition
    # Walk through known free_bytes values for this partition.
    avail_fb = sorted({k[2] for k in _HBM2SB_BW_TABLE if k[0] == lnc and k[1] == p_clamp})
    if not avail_fb:
        return 300.0  # safe fallback
    if free_bytes <= avail_fb[0]:
        return _HBM2SB_BW_TABLE[(lnc, p_clamp, avail_fb[0])]
    if free_bytes >= avail_fb[-1]:
        return _HBM2SB_BW_TABLE[(lnc, p_clamp, avail_fb[-1])]
    # Linear interp between adjacent points.
    for i in range(len(avail_fb) - 1):
        f0, f1 = avail_fb[i], avail_fb[i + 1]
        if f0 <= free_bytes <= f1:
            v0 = _HBM2SB_BW_TABLE[(lnc, p_clamp, f0)]
            v1 = _HBM2SB_BW_TABLE[(lnc, p_clamp, f1)]
            t = (free_bytes - f0) / (f1 - f0)
            return v0 + t * (v1 - v0)
    return 300.0


def _dma_per_engine_latency(per_engine_bytes: float, parallel_engines: int = 1) -> float:
    """Per-engine DMA latency in ns for a given byte count.

    Architectural model from "DMA Latency Model" (Quip As7AA7nUa2qe):
    - Each transfer is composed of multiple packets (typical 8-32 KB
      per packet, set by compiler max-prefetch-size).
    - Each packet incurs setup + transmission latency per the
      silicon-validated packet table.

    For per-engine bytes B with packet size P, the per-engine time is
        N_packets * latency(P)
    where N_packets = ceil(B / P).

    To pick P: large transfers naturally use the largest packet size
    that fits SBUF allocation. We assume P = min(B, 32KB) which is the
    saturation point of the per-engine bandwidth table.
    """
    if per_engine_bytes <= 0:
        return 0.0

    # Determine packet size: larger transfers can use larger packets.
    # Cap at 32KB (saturation point); below that, packet = per_engine_bytes.
    packet_size = min(per_engine_bytes, 32768)

    # Look up per-packet latency from table.
    if packet_size <= _DMA_PACKET_TABLE[0][0]:
        size, lat, _ = _DMA_PACKET_TABLE[0]
        packet_lat = lat * packet_size / size
    elif packet_size >= _DMA_PACKET_TABLE[-1][0]:
        packet_lat = _DMA_PACKET_TABLE[-1][1]
    else:
        packet_lat = _DMA_PACKET_TABLE[0][1]
        for i in range(len(_DMA_PACKET_TABLE) - 1):
            s0, l0, _ = _DMA_PACKET_TABLE[i]
            s1, l1, _ = _DMA_PACKET_TABLE[i + 1]
            if s0 <= packet_size <= s1:
                frac = (packet_size - s0) / (s1 - s0)
                packet_lat = l0 + frac * (l1 - l0)
                break

    n_packets = max(1, int(per_engine_bytes / packet_size))
    # Scale by HBM channel hashing efficiency for D parallel engines.
    HBM_BW = 716.0
    if parallel_engines > 1:
        D = parallel_engines
        eff = (1.0 - (1.0 - 1.0 / 16) ** D) / D
    else:
        eff = 1.0 / 16  # baseline single-engine efficiency
    # In the saturated regime, total per-engine latency = packet_lat × N
    # divided by parallel-engine speedup factor.
    return n_packets * packet_lat * (eff * 16)


class CostModel:
    """Analytical cost model computing instruction latency in nanoseconds."""

    def __init__(self, target: str = "trn2", vnc_count: int = 1,
                 cc_world_size: int = 1):
        self.hw = get_hardware_config(target)
        self.target = target
        # VNC=2 splits HBM bandwidth across 2 cores
        self.vnc_count = max(1, vnc_count)
        # Collective-operation world size (number of NCs in replica group).
        # Used by collective-cost model to look up per-r alpha/beta values.
        self.cc_world_size = max(1, cc_world_size)

    def compute_latency_ns(self, inst: SimInstruction) -> float:
        """End-to-end execution latency (start → result visible).

        This is what cross-engine consumers see. For pipelined operations
        latency >> II.
        """
        if inst.opcode == "PSEUDO_TRIGGER_COLLECTIVE_2":
            return self._collective_latency(inst)
        # Per-opcode HW-derived rule table takes precedence — covers compute,
        # control, and sem opcodes for engines we have data for.
        rule_ns = self._opcode_rule_cost(inst)
        if rule_ns is not None:
            return rule_ns
        if inst.opcode in CONTROL_OPS or inst.opcode in CONTROL_OP_REAL_NS:
            return self._control_op_cost(inst)
        if inst.opcode in PIPELINED_PE_OPS:
            return self._ldweights_latency(inst)
        if inst.opcode in TENSOR_ENGINE_OPS:
            return self._matmul_latency(inst)
        if inst.opcode == "PSEUDO_EXTENSION":
            return self._pseudo_extension_cost(inst)
        if inst.opcode in DMA_TRIGGER_OPS:
            return self._dma_trigger_cost(inst)
        if inst.opcode in DMA_TRANSFER_OPS:
            return self.compute_dma_transfer_time(inst)
        if inst.opcode.startswith("DMA") or inst.opcode.startswith("PSEUDO_DMA"):
            return self._dma_transfer_cost(inst)
        return self._compute_engine_cost(inst)

    def compute_ii_ns(self, inst: SimInstruction) -> float:
        """Initiation Interval — minimum time before the next instruction
        on the same engine can issue.

        For pipelined PE operations (MATMUL/LDWEIGHTS), II << latency.
        For non-pipelined engines, II ≈ latency.
        """
        if inst.opcode == "PSEUDO_TRIGGER_COLLECTIVE_2":
            # II for collective trigger on Pool0 — the trigger itself is fast,
            # the actual collective happens on the CC engines and we model the
            # full latency in compute_latency_ns. The Pool0 II is just the
            # inter-trigger spacing.
            return 200.0
        if inst.opcode in CONTROL_OPS or inst.opcode in CONTROL_OP_REAL_NS:
            return self._control_op_cost(inst)
        if inst.opcode in PIPELINED_PE_OPS:
            return self._matmul_ii(inst)  # LDWEIGHTS II = MATMUL II (paired)
        if inst.opcode in TENSOR_ENGINE_OPS:
            return self._matmul_ii(inst)
        if inst.opcode == "PSEUDO_EXTENSION":
            return self._pseudo_extension_cost(inst)
        if inst.opcode in DMA_TRIGGER_OPS:
            return self._dma_trigger_cost(inst)
        if inst.opcode in DMA_TRANSFER_OPS:
            return self.compute_dma_transfer_time(inst)
        if inst.opcode.startswith("DMA") or inst.opcode.startswith("PSEUDO_DMA"):
            return self._dma_transfer_cost(inst)
        # Vector/GpSimd/Scalar are not deeply pipelined for these workloads;
        # II ≈ latency.
        return self._compute_engine_cost(inst)

    def _matmul_ii(self, inst: SimInstruction) -> float:
        """MATMUL initiation interval — the start-to-start cadence of
        back-to-back matmuls on the PE, governed by min_wave quantization.

        Measured from trn2 Instruction.parquet (same-shape consecutive matmuls,
        p5 = true streaming minimum):
          * The 128-wide systolic array (min_wave=128) processes a full-width
            tile (matmul_n=128) as ONE optimal wave: bf16/fp16 II ≈ 27ns
            (= MATMUL_MIN_II=65 cycles @ 2.40GHz). N=64 (half array) is 32ns —
            a partial wave still costs ~a full wave (min_wave floor).
          * So II = ceil(matmul_n / min_wave) waves × wave_ii. wave_ii ≈ 27ns
            for bf16/fp16; fp8 DoubleRowInterleave is ~same/slightly less; fp32
            is single-pumped and streams K serially within a wave (~4x slower).

        matmul_n (=elements_per_partition, the dst free dim) sets the wave
        count. matmul_k (the contract dim) only matters for fp32, whose II
        scales with K because it cannot quad-pump (1 element/cycle).
        """
        MIN_WAVE = 128
        n = max(inst.elements_per_partition, 1)
        k_dim = inst.extra.get("matmul_k", 0) if inst.extra else 0
        freq = self.hw.tensor.frequency  # 240 for trn2

        # Per-wave II floor in cycles (MATMUL_MIN_II = 65 cyc = 27ns @ 2.40GHz).
        wave_ii = MATMUL_MX_MIN_II if inst.opcode == "MATMUL_MX" else MATMUL_MIN_II

        dt = (inst.dtype or "").lower()
        wdt = (inst.extra.get("weight_dtype", "") if inst.extra else "").lower()
        is_fp32 = "fp32" in dt or "float32" in dt or "fp32" in wdt or "float32" in wdt
        is_fp8 = (inst.dtype in FP8_TYPES) if inst.dtype else False
        if not is_fp8 and wdt:
            is_fp8 = wdt in FP8_TYPES

        if is_fp32:
            # fp32: single-pumped (no quad-pump), ~4 cycles/free-col.
            # HW p5: N=64→107ns(257cyc)≈4.0/elem, N=128→237ns(569cyc)≈4.4/elem.
            ii_cycles = max(wave_ii, int(4.2 * n), k_dim)
        else:
            # bf16/fp16/fp8: wave-quantized. Number of 128-wide waves needed
            # for matmul_n, each costing wave_ii cycles.
            waves = (n + MIN_WAVE - 1) // MIN_WAVE
            ii_cycles = waves * wave_ii

        latency_ns = ii_cycles * 100.0 / freq
        if self.target == "trn1":
            latency_ns *= 8.0 / 7.0
        return latency_ns

    def _matmul_latency(self, inst: SimInstruction) -> float:
        """MATMUL active duration (engine-busy time per event).

        Distinct from II: II is the next-issue cadence (`_matmul_ii`),
        latency is how long this single instruction occupies the engine
        as visible in `OpcodeSummary.active_time_ns / count`.

        Shape-aware model fitted to 21-NEFF perf-analysis corpus
        (12,739 MATMUL events):
          - bf16/fp16: relatively flat (~30-160ns) regardless of N/K.
            Average 43ns matches the suite mean.
          - fp32 with K=128: ~163ns; K=512: ~510ns. Clear K-dominated
            scaling at ~1ns/k for fp32 (one wave per K element).

        Returns the empirical mean for the dtype family. The flat
        return for low-precision dtypes is correct on average; per-
        kernel variation is captured by II (`_matmul_ii`), not latency.
        """
        n = max(inst.elements_per_partition, 1)
        k_dim = inst.extra.get("matmul_k", 0) if inst.extra else 0
        dtype = inst.dtype or ""
        weight_dt = inst.extra.get("weight_dtype", "") if inst.extra else ""
        is_fp32 = dtype == "fp32" or dtype == "float32" or weight_dt in ("fp32", "float32")
        if is_fp32:
            # fp32 active scales with K: ~1ns/k cycle plus shape floor.
            # Fitted from {(128,16):270, (128,128):163, (128,512):510}:
            # base 100 + 0.85*k matches the wider-K cases reasonably.
            return max(55.0, 100.0 + 0.85 * k_dim)
        # bf16/fp16/fp8: OpcodeSummary active_time/inst is ~57ns median across
        # the corpus (was modeled flat at 43ns -> 0.76x under). The active
        # duration is near-flat in N (systolic wave time) with a slow rise on
        # the largest tiles; the per-kernel median of 57ns is the best single
        # value. A mild N term captures the large-tile tail.
        return 55.0 + 0.00002 * n * k_dim

    # Bytes-per-element table used by MEMSET and other byte-scaled cost paths.
    _DTYPE_BYTES: Dict[str, int] = {
        "fp32": 4, "float32": 4, "int32": 4, "uint32": 4,
        "fp16": 2, "float16": 2, "bf16": 2, "bfloat16": 2,
        "uint16": 2, "int16": 2,
        "fp8e4": 1, "fp8e5": 1, "uint8": 1, "int8": 1,
    }

    def _memset_cost(self, inst: SimInstruction) -> float:
        """MEMSET cost on Vector engine.

        Calibrated against OpcodeSummary active_time_ns over the 583-NEFF tkg
        corpus (89 single-signature cases for the law, 494 for validation):
        cost scales with the number of FREE ELEMENTS written per partition,
        NOT bytes. The fill engine writes one element per lane per cycle, so
        dtype width is irrelevant (a uint8 and an fp32 fill of N elements cost
        the same). Strided and contiguous fills share the same slope
        (~0.57-0.59 ns/elem measured; 0.72 with the floor folded in), so no
        per-stride branch is needed.

          active_ns ≈ max(35, 0.72 * free_elems + 40)

        The previous bytes-based model both over-charged tiny fills (flat 59ns
        vs HW ~64-111ns it should scale) AND collapsed large fills to the 59ns
        fast path (the byte-scaling only fired when a `strided` flag happened to
        be set, which most fills lack). Result on the corpus: aggregate
        0.88x->0.99x, per-event median +23%->+1%, within-20% 38%->75%.
        No drain, no perf-mode speedup (HW data: 0% have any speedup mode).
        """
        FLOOR_NS = 35.0
        NS_PER_ELEM = 0.72
        BASE_NS = 40.0

        n_elements = max(inst.elements_per_partition, 1)
        return max(FLOOR_NS, NS_PER_ELEM * n_elements + BASE_NS)

    # Per-opcode cost rules derived from HW Instruction.parquet on the
    # 583-NEFF tkg corpus.
    #
    # Cost model:  dur = max(floor_n1, floor_large + slope * N / speedup)
    #   - floor_n1:     median dur observed at N<=4 (small-N pipeline latency)
    #   - floor_large:  intercept of linear fit on N>=64 (large-N regression)
    #   - slope:        ns per element at mode=None (mode=2x slope is ÷2,
    #                   mode=4x slope is ÷4 — confirmed in HW data)
    #   - can_2x:       gets 2x speedup when all_sbuf AND same-dtype AND 16-bit
    #   - can_4x:       gets 4x speedup (subset of 2x conditions, opcode-specific)
    #
    # Source: /tmp/opcode_fit.py output (linear regression separated by mode).
    # Per-opcode rules (floor_ns, slope_ns_per_elem). Cost model:
    #   dur = max(floor, slope * N / speedup)
    # floor is observed median at N=1; slope is asymptotic ns/elem at large N.
    # Speedup applied per perf-mode rules below.
    # Per-opcode rules. Hybrid approach combining the compiler's canonical
    # cost model (Cayman.py / Sunda.py LatencyModel for trn2) with HW silicon
    # measurements from Instruction.parquet:
    #
    # The compiler's standalone cost is: (start_latency + cpe*N) * 100/freq
    # but this underestimates measured silicon by 30-50% for most opcodes.
    # The gap is the engine's pipeline drain (Vector write_drain=161ns) plus
    # per-instruction setup overhead not captured in the throughput formula.
    #
    # For practical accuracy, we set:
    #   floor = compiler start_latency*100/freq + observed_overhead
    #   slope = compiler cpe (in cycles) * 100/freq
    # and allow per-opcode adjustments where data justifies them.
    _OPCODE_RULES = {
        # ==== Vector engine (trn2 freq=96, 100/freq=1.042 ns/cycle) ====
        # Slopes are calibrated to OpcodeSummary active_time_ns (engine-busy,
        # excludes sem-wait stalls — which the scheduler models separately via
        # cross-engine deps). The raw Instruction.parquet duration_ns slopes
        # were scaled by the measured per-op active/instruction_time ratio:
        #   TENSOR_TENSOR 0.62, CAST 0.68, COPY 0.64, TENSOR_SCALAR 0.73,
        #   STREAM_SHUFFLE 0.76, TENSOR_REDUCE 0.785, COPY_PREDICATED 0.89.
        # Floors stay near the N=1 duration (at small N active≈instruction_time).
        ("TENSOR_TENSOR", "Vector"):  dict(floor=94, slope=0.64, slope_2x=0.32, can_2x=True,  can_4x=False),
        ("TENSOR_TENSOR_ARITH", "Vector"): dict(floor=94, slope=0.64, slope_2x=0.32, can_2x=True, can_4x=False),
        ("TENSOR_SCALAR", "Vector"):  dict(floor=112, slope=0.77, slope_2x=0.66, slope_4x=0.19, can_2x=True,  can_4x=True),
        ("TENSOR_SCALAR_ARITH", "Vector"): dict(floor=112, slope=0.77, slope_2x=0.66, slope_4x=0.19, can_2x=True, can_4x=True),
        ("CAST",          "Vector"):  dict(floor=101, slope=0.71, slope_2x=0.47, can_2x=True, can_4x=False),
        # COPY: the HW perf-mode (2x/4x) gate predicts the WRONG direction here
        # (bf16 under, fp32 over), so it is disabled and a flat law used. Audit
        # against active_time: 0.825x -> 1.00x, per-event w20 60%.
        ("COPY",          "Vector"):  dict(floor=125, slope=0.6, can_2x=False, can_4x=False),
        ("TENSOR_COPY",   "Vector"):  dict(floor=125, slope=0.6, can_2x=False, can_4x=False),
        ("STREAM_SHUFFLE","Vector"):  dict(floor=111, slope=0.76, can_2x=False, can_4x=False),
        ("COPY_PREDICATED","Vector"): dict(floor=140, slope=1.45, can_2x=False, can_4x=False),
        ("TENSOR_REDUCE", "Vector"):  dict(floor=100, slope=1.33, can_2x=False, can_4x=False),
        ("TENSOR_REDUCE_ARITH", "Vector"): dict(floor=100, slope=1.33, can_2x=False, can_4x=False),
        ("EXPONENTIAL",   "Vector"):  dict(floor=101, slope=0.71, can_2x=True,  can_4x=False),
        ("DROPOUT",       "Vector"):  dict(floor=101, slope=0.71, can_2x=True,  can_4x=False),
        ("SCALAR_TENSOR_TENSOR", "Vector"): dict(floor=132, slope=0.947, can_2x=False, can_4x=False),
        ("SCALAR_TENSOR_TENSOR_ARITH", "Vector"): dict(floor=132, slope=0.947, can_2x=False, can_4x=False),
        # Reciprocal: compiler cpe=8 -> 8.33 ns/elem. Silicon shows fixed ~140ns
        # at N=1; for N>1 the slope kicks in.
        ("RECIPROCAL",    "Vector"):  dict(floor=137, slope=8.33, can_2x=False, can_4x=False),
        ("STREAM_TRANSPOSE","Vector"):dict(floor=167, slope=0.0, can_2x=False, can_4x=False),
        ("LOAD_MASK_SELECT","Vector"):dict(floor=6,   slope=0.0, can_2x=False, can_4x=False),
        ("COPY_PREDICATED_SCALAR","Vector"): dict(floor=220, slope=0.0, can_2x=False, can_4x=False),
        ("MAX8",          "Vector"):  dict(floor=230, slope=0.0, can_2x=False, can_4x=False),
        ("FIND_INDEX8",   "Vector"):  dict(floor=231, slope=0.0, can_2x=False, can_4x=False),
        ("MATCH_VALUE_LOAD","Vector"):dict(floor=77,  slope=0.0, can_2x=False, can_4x=False),
        # ==== Vector engine control/sem opcodes ====
        # All N=1, fixed engine-busy time per HW.
        ("EVENT_SEMAPHORE","Vector"): dict(floor=54,  slope=0.0, can_2x=False, can_4x=False),
        ("EVENT_SEMAPHORE_RANGE_CLEAR","Vector"): dict(floor=308, slope=0.0, can_2x=False, can_4x=False),
        ("NOTIFY",        "Vector"):  dict(floor=17,  slope=0.0, can_2x=False, can_4x=False),
        ("DRAIN",         "Vector"):  dict(floor=13,  slope=0.0, can_2x=False, can_4x=False),
        ("SET_ORDERING_MODE","Vector"): dict(floor=50, slope=0.0, can_2x=False, can_4x=False),
        ("COMPARE_BRANCH","Vector"):  dict(floor=117, slope=0.0, can_2x=False, can_4x=False),
        ("BRANCH_PREFETCH_HINT","Vector"): dict(floor=60, slope=0.0, can_2x=False, can_4x=False),
        ("MOVE",          "Vector"):  dict(floor=81,  slope=0.0, can_2x=False, can_4x=False),
        ("ALU_OP",        "Vector"):  dict(floor=46,  slope=0.0, can_2x=False, can_4x=False),
        ("TENSOR_LOAD",   "Vector"):  dict(floor=880, slope=0.0, can_2x=False, can_4x=False),
        # Scalar engine compute opcodes
        ("ACTIVATE",      "Scalar"):  dict(floor=205, slope=0.70, can_2x=False, can_4x=False),
        ("ACTIVATION",    "Scalar"):  dict(floor=205, slope=0.70, can_2x=False, can_4x=False),
        ("CAST",          "Scalar"):  dict(floor=145, slope=0.42, can_2x=False, can_4x=False),
        ("COPY",          "Scalar"):  dict(floor=143, slope=0.96, can_2x=False, can_4x=False),
        ("ACT_TABLE_LOAD","Scalar"):  dict(floor=999, slope=0.0, can_2x=False, can_4x=False),
        # GpSimd engine compute opcodes
        ("IOTA",          "GpSimd"):  dict(floor=128, slope=1.54, can_2x=False, can_4x=False),
        # UNKNOWN: GpSimd spill/catch-all ops, fixed ~940ns active (right-skewed
        # 304-4380ns). Falls through to a flat 219ns otherwise (4.4x under).
        # Re-validate if the decoder later classifies these (task #50).
        ("UNKNOWN",       "GpSimd"):  dict(floor=940, slope=0.0, can_2x=False, can_4x=False),
        ("ALU_OP",        "GpSimd"):  dict(floor=37,  slope=0.0, can_2x=False, can_4x=False),
        ("MOVE",          "GpSimd"):  dict(floor=47,  slope=0.0, can_2x=False, can_4x=False),
        ("INDIRECT_COPY", "GpSimd"):  dict(floor=797, slope=0.0, can_2x=False, can_4x=False),
        ("TENSOR_SCALAR_AFFINE_SELECT", "GpSimd"): dict(floor=248, slope=0.0, can_2x=False, can_4x=False),
    }

    def _opcode_rule_cost(self, inst: SimInstruction) -> Optional[float]:
        """If a per-opcode rule exists for this (opcode, engine), use it.

        Per HW Instruction.parquet on 583-NEFF corpus:
          - mode=2x triggers when: all_sbuf AND same-dtype AND 16-bit
          - mode=4x triggers when: 2x conditions AND opcode in {COPY, TENSOR_SCALAR}
          - strided ops at 50% effective speedup (data shows ~50% of strided
            ops still hit 2x; non-strided ops nearly always hit 2x when eligible)
        Returns None if no rule exists for this opcode/engine.
        """
        rule = self._OPCODE_RULES.get((inst.opcode, inst.engine_type))
        if rule is None:
            return None
        n = max(inst.elements_per_partition, 1)
        # Select per-mode slope. HW perf-mode (2x/4x) requires all operands in
        # SBUF, all the same dtype, and that dtype being 16-bit. We track both
        # src and dst dtype so the same-dtype gate is correct (TENSOR_TENSOR
        # with fp16 inputs but fp32 accumulation dst gets NO speedup).
        slope = rule["slope"]
        if rule["can_2x"] or rule["can_4x"]:
            _16BIT = ("fp16", "float16", "bf16", "bfloat16", "uint16", "int16")
            extra = inst.extra or {}
            src1_ms = extra.get("src1_mem_space", "SB")
            all_sbuf = (inst.src_mem_space == "SB" and inst.dst_mem_space == "SB"
                        and src1_ms == "SB")
            src_dt = (inst.dtype or "").lower()
            dst_dt = (inst.dst_dtype or src_dt).lower()
            src1_dt = (extra.get("src1_dtype", src_dt) or src_dt).lower()
            same_dt = (src_dt == dst_dt == src1_dt)
            is_16bit = (src_dt in _16BIT and dst_dt in _16BIT and src1_dt in _16BIT)
            if all_sbuf and same_dt and is_16bit:
                # Use the measured per-mode slope (slope_4x / slope_2x), else
                # fall back to slope/4 or slope/2.
                if rule["can_4x"]:
                    fast = rule.get("slope_4x", rule["slope"] / 4.0)
                else:
                    fast = rule.get("slope_2x", rule["slope"] / 2.0)
                # Strided ops only hit the speedup ~50% of the time (HW data),
                # so use the midpoint between full-speed and no-speed slope.
                if inst.extra and inst.extra.get("any_strided"):
                    slope = (fast + rule["slope"]) / 2.0
                else:
                    slope = fast
        # dur = floor + slope * N. floor is small-N pipeline latency; slope is
        # the per-element cost for the selected perf-mode.
        return rule["floor"] + slope * n

    def _ldweights_latency(self, inst: SimInstruction) -> float:
        """LDWEIGHTS active duration.

        Stages weights for the upcoming MATMUL — the stage-in is fast and
        throughput (next-issue interval) is captured via `_matmul_ii`. Per
        OpcodeSummary active_time/inst (audit, 583-NEFF corpus), the engine-busy
        duration is dtype-dependent: bf16 ~49ns and fp8 ~63ns (16-bit/8-bit
        weights stage fast), but fp32 ~208ns (double-pumped, 4x the data). Using
        a flat 46ns under-counted fp32 LDWEIGHTS. The fp32 value is set to 120
        (below the 208 mean) to lift the corpus aggregate 0.937x->0.989x without
        over-charging the per-event median.
        """
        dt = (inst.dtype or "").lower()
        if "fp32" in dt or "float32" in dt:
            return 120.0
        return 46.0

    def _compute_engine_cost(self, inst: SimInstruction) -> float:
        """Vector/GpSimd/Scalar engine cost: (STATIC_COST + N_src/speedup) * 100/freq."""
        # MEMSET has its own model: byte-scaled fill, no drain, no speedup.
        # See _memset_cost for derivation from HW Instruction.parquet.
        if inst.opcode == "MEMSET":
            return self._memset_cost(inst)

        # Per-opcode HW-derived rule path (returns ns directly, no drain).
        rule_ns = self._opcode_rule_cost(inst)
        if rule_ns is not None:
            return rule_ns

        n_elements = max(inst.elements_per_partition, 1)
        engine_type = inst.engine_type

        # Look up static cost (engine-specific override > opcode default)
        static_cycles = STATIC_COST_CYCLES_BY_ENGINE.get(
            (engine_type, inst.opcode),
            STATIC_COST_CYCLES.get(inst.opcode, 0),
        )

        # Look up cycles per element (engine-specific override > opcode default)
        cpe = CYCLES_PER_ELEMENT_BY_ENGINE.get(
            (engine_type, inst.opcode),
            CYCLES_PER_ELEMENT.get(inst.opcode, 1.0),
        )

        # Compute perf mode speedup (trn2+ only)
        speedup = self._compute_perf_speedup(inst)

        # Strided access penalty: any non-contiguous read/write halves
        # the effective bandwidth (no dual-port speedup, sub-block jumps).
        if inst.extra and inst.extra.get("any_strided"):
            speedup = max(1.0, speedup * 0.5)

        # Total II in engine cycles (compute-only, before drain)
        ii_cycles = static_cycles + cpe * n_elements / speedup

        # Convert to nanoseconds
        config = self.hw.get_engine_config(engine_type)
        ns = ii_cycles * 100.0 / config.frequency

        # Add per-instruction write_drain. The architectural Formula A
        # gives throughput-bound compute time; HW active_time_ns includes
        # the drain (L6+L8 pipeline stages) that follows ops that write
        # to memory. Pure register-only ops (ALU_OP, MOVE) don't drain.
        if inst.opcode not in _NO_DRAIN_OPS:
            ns += config.write_drain_ns

        return ns

    def _compute_perf_speedup(self, inst: SimInstruction) -> float:
        """Compute DVE performance mode speedup factor for trn2+.

        Models HW's `performance_mode` field on Vector instructions. From
        Instruction.parquet on the 583-NEFF corpus: only Vector ops have
        it (None / "2x" / "4x"); MEMSET, TENSOR_REDUCE, STREAM_*, RECIPROCAL
        never get any speedup mode in HW.

        Conditions inferred from operand-pattern analysis:
          - 2x requires: all_sbuf AND same-dtype-throughout AND 16-bit dtype
          - 4x requires the 2x conditions AND opcode is COPY/TENSOR_SCALAR
          - PSUM source disables speedup
          - Stridedness reduces speedup hit rate ~half (modeled as 0.5x penalty)
        """
        if self.target == "trn1":
            return 1.0
        if inst.engine_type not in ("Vector", "DVE"):
            return 1.0

        # Opcodes eligible for any speedup mode (MEMSET, REDUCE, STREAM,
        # RECIPROCAL never get one in HW).
        OPS_2X = ("TENSOR_TENSOR", "TENSOR_TENSOR_ARITH",
                  "COPY", "TENSOR_COPY", "CAST",
                  "TENSOR_SCALAR", "TENSOR_SCALAR_ARITH",
                  "ACTIVATION", "DROPOUT", "EXPONENTIAL")
        OPS_4X = ("COPY", "TENSOR_COPY", "TENSOR_SCALAR", "TENSOR_SCALAR_ARITH")

        if inst.opcode not in OPS_2X:
            return 1.0

        all_sbuf = inst.src_mem_space == "SB" and inst.dst_mem_space == "SB"
        if not all_sbuf:
            return 1.0

        dtype = inst.dtype.lower() if inst.dtype else ""
        is_16bit = dtype in ("bf16", "bfloat16", "fp16", "float16",
                             "uint16", "int16")
        if not is_16bit:
            return 1.0

        speedup = 4.0 if inst.opcode in OPS_4X else 2.0

        # Stridedness: HW data shows ~50% of strided ops still get speedup,
        # so apply half-penalty (probabilistic average).
        if inst.extra and inst.extra.get("any_strided"):
            speedup = max(1.0, speedup * 0.5)

        return speedup

    # Collective-operation cost model. Constants fitted from nccom-test
    # bf16 sweeps on Trainium2 (compute1-st-kaena-training-1-12) across
    # r ∈ {2,4,8,16,32,64} for {all_reduce, all_gather, reduce_scatter,
    # alltoall}, payload sizes 1KiB..256MiB.
    #
    # Form: latency_ns = alpha + beta * size_bytes
    # All fits had R² >= 0.95 (most >= 0.99).
    #
    # alltoall scales worst with r (per-byte cost grows ~8x from r=2 to r=64
    # because pairwise sends grow as r²). The other ops scale better.
    _CC_FIT: Dict[Tuple[str, int], Tuple[float, float]] = {
        # (op, r) -> (alpha_ns, beta_ns_per_byte)
        # ALL_REDUCE r∈{8,16,32}: refit from real transformer-block workload
        # (excludes op_id=-1 BARRIER and op_id=0 first-call warmup). Real-workload
        # betas are ~3-6x larger than nccom-test microbenchmark fits; the
        # microbenchmark runs collectives back-to-back without compute
        # interleaving, so it under-estimates production fabric cost.
        ("ALL_REDUCE", 2):  (1133,  0.0204),
        ("ALL_REDUCE", 4):  (8000,  0.0125),
        ("ALL_REDUCE", 8):  (12709, 0.0600),
        ("ALL_REDUCE", 16): (17208, 0.0552),
        ("ALL_REDUCE", 32): (20928, 0.0315),
        ("ALL_REDUCE", 64): (66018, 0.0066),
        ("ALL_GATHER", 2):  (3275,  0.0108),
        ("ALL_GATHER", 4):  (5000,  0.0052),
        ("ALL_GATHER", 8):  (6431,  0.0069),
        ("ALL_GATHER", 16): (17723, 0.0054),
        ("ALL_GATHER", 32): (32499, 0.0051),
        ("ALL_GATHER", 64): (41578, 0.0030),
        ("REDUCE_SCATTER", 2):  (5233,  0.0061),
        ("REDUCE_SCATTER", 4):  (7000,  0.0069),
        ("REDUCE_SCATTER", 8):  (10862, 0.0092),
        ("REDUCE_SCATTER", 16): (22473, 0.0049),
        ("REDUCE_SCATTER", 32): (31153, 0.0049),
        ("REDUCE_SCATTER", 64): (38632, 0.0038),
        ("ALL_TO_ALL", 2):  (1148,  0.0066),
        ("ALL_TO_ALL", 4):  (1456,  0.0085),
        ("ALL_TO_ALL", 8):  (2304,  0.0218),
        ("ALL_TO_ALL", 16): (5000,  0.0526),
        ("ALL_TO_ALL", 32): (3000,  0.0470),
        ("ALL_TO_ALL", 64): (1781,  0.0568),
    }
    _CC_DTYPE_BYTES: Dict[str, int] = {
        "bfloat16": 2, "bf16": 2,
        "float16": 2, "fp16": 2,
        "float32": 4, "fp32": 4,
        "int32": 4, "uint32": 4,
        "int16": 2, "uint16": 2,
        "int8": 1, "uint8": 1, "fp8": 1, "fp8e4": 1, "fp8e5": 1,
    }

    def _collective_latency(self, inst: SimInstruction) -> float:
        """End-to-end collective-operation latency.

        Looks up alpha + beta * payload_bytes from the calibrated
        _CC_FIT table. Falls back to a default 25 µs if the
        (op, r) is unknown or the size info is missing.
        """
        cc_type = inst.extra.get("cc_type", "") if inst.extra else ""
        n_elems = inst.extra.get("cc_n_elems", 0) if inst.extra else 0
        dtype = inst.extra.get("cc_dtype", "bfloat16") if inst.extra else "bfloat16"
        elem_bytes = self._CC_DTYPE_BYTES.get(dtype, 2)
        payload_bytes = n_elems * elem_bytes

        # Snap world size to nearest known r in the fit table.
        r = self.cc_world_size
        known_r = sorted({k[1] for k in self._CC_FIT.keys()})
        # Pick the closest r >= world_size, or the largest if world_size is bigger.
        r_lookup = next((kr for kr in known_r if kr >= r), known_r[-1])

        fit = self._CC_FIT.get((cc_type, r_lookup))
        if fit is None:
            # Try ALL_REDUCE as a fallback for unknown ops.
            fit = self._CC_FIT.get(("ALL_REDUCE", r_lookup), (25000.0, 0.01))
        alpha, beta = fit
        return alpha + beta * payload_bytes

    def _pseudo_extension_cost(self, inst: SimInstruction) -> float:
        """PSEUDO_EXTENSION: SW-DGE descriptor generation on GpSimd.

        Two regimes, distinguished by the `dge_mode` field (decoded from the
        instruction's `mode=` operand):
          - INDIRECTION (SRC_INDIRECTION / DST_INDIRECTION): an indirect or
            gather DMA that HW reports as DMA_INDIRECT / DMA_GATHER_TRANSPOSE
            with substantial active time (gather index walk + descriptor
            compute). Audit (583-NEFF corpus): 2040 such ops carry ~2.4ms of HW
            active that the sim previously dropped (sim_us=0). Per-event HW
            blends INDIRECT (~820ns) and GATHER_TRANSPOSE (~1580ns); a single
            ~1100ns charge centers the per-case aggregate (ratio ~0.93, median
            +1%). HW cannot be split further — both decode as one mode here.
          - plain DGE (no mode=): a near-free descriptor extension; HW shows ~0
            standalone active time (overlaps the paired DIRECT2D). Keep ~17.5ns.

        Gating on dge_mode is essential: only ~2040 of ~8760 PSEUDO_EXTENSION
        ops are indirection; charging all of them would over-count the ~6720
        plain-DGE extensions in MLP/QKV by ~1100ns each.
        """
        mode = (inst.extra or {}).get("dge_mode")
        if mode and "INDIRECTION" in mode:
            return 1100.0
        return 17.5

    def _dma_trigger_cost(self, inst: SimInstruction) -> float:
        """DMA trigger cost on the issuing engine.

        PSEUDO_DMA_DIRECT2D corresponds to HW DMA_DIRECT2D, whose engine-busy
        active_time is ~496ns on GpSimd and ~663ns on the sync engine.

        NOTE (audit): the sim issues DIRECT2D triggers almost entirely on
        Pool0/GpSimd, while the HW profiler attributes the majority to the
        `sync` engine (SP-issued). This is an ENGINE-ATTRIBUTION mismatch in the
        decoder/graph_builder, not a per-event cost error — the sync bucket
        reads 0.76x and the gpsimd bucket 1.39x for the same reason (sim emits
        on gpsimd what HW splits to sync). Per-event cost tuning cannot fix it;
        tracked as a decoder follow-up. Keep the measured 496ns per-event cost.
        """
        if inst.opcode == "PSEUDO_DMA_DIRECT2D":
            return 496.0
        return 60.0

    def compute_dma_transfer_time(self, inst: SimInstruction) -> float:
        """DMA transfer time using packet-aware architectural model.

        Per "DMA Latency Model" (Quip As7AA7nUa2qe):
          * Per-engine packet latency varies with packet size:
            8KB=1641ns, 16KB=1769ns, 32KB=1882ns (saturates).
          * Compiler max-prefetch-size sets default packet size (~8KB).
          * Per uBM Handover: HBM hashing means D=16 engines achieve
            (1-(1-1/16)^16)/(1/16) = 10x effective parallelism (vs 16x ideal).
        """
        if inst.dma_queue == "qGpSimdDynamic":
            return 1500.0 + inst.transfer_bytes / 600.0

        transfer_bytes = inst.transfer_bytes
        if transfer_bytes <= 0:
            return self.hw.dma.static_startup_ns

        if inst.src_mem_space == "SB" and inst.dst_mem_space == "SB":
            bandwidth = self.hw.dma.transpose_sbuf_to_sbuf
            return self.hw.dma.static_startup_ns + transfer_bytes / bandwidth

        bandwidth = self._get_dma_bandwidth(inst.src_mem_space, inst.dst_mem_space)
        is_hbm = inst.src_mem_space != "SB" or inst.dst_mem_space != "SB"
        if is_hbm:
            if transfer_bytes >= 8 * 1024 * 1024:
                bandwidth = bandwidth * 1.5
            elif transfer_bytes >= 4 * 1024 * 1024:
                bandwidth = bandwidth * 1.3
            elif transfer_bytes >= 1 * 1024 * 1024:
                bandwidth = bandwidth * 1.1
            elif transfer_bytes < 64 * 1024:
                bandwidth = bandwidth * 0.5

        trigger_ns = self.hw.dma.static_startup_ns
        transfer_ns = transfer_bytes / bandwidth if bandwidth > 0 else 0.0
        return trigger_ns + transfer_ns

    def _dma_transfer_cost(self, inst: SimInstruction) -> float:
        """Cost for DMA transfer instructions (non-trigger DMA ops in .bin)."""
        n_elements = max(inst.elements_per_partition, 1)
        n_parts = inst.num_partitions

        # SBUF→SBUF strided DMA (SW-DGE model from NKI ISA doc)
        if inst.src_mem_space == "SB" and inst.dst_mem_space == "SB":
            # SW-DGE: 750 + 48 * N_src (in DMA engine cycles)
            # DMA engine runs at 850 MHz → 1 cycle ≈ 1.18 ns
            ii_cycles = 750 + 48 * n_elements
            return ii_cycles * 100.0 / 850  # approximate DMA engine frequency

        # HBM transfers: bandwidth-limited
        elem_bytes = 2  # default bf16
        if inst.dtype:
            from .graph_builder import _dtype_bytes
            elem_bytes = _dtype_bytes(inst.dtype)

        total_bytes = n_elements * n_parts * elem_bytes
        bandwidth = self._get_dma_bandwidth(inst.src_mem_space, inst.dst_mem_space)
        if bandwidth <= 0:
            return self.hw.dma.static_startup_ns

        return self.hw.dma.static_startup_ns + total_bytes / bandwidth

    def _get_dma_bandwidth(self, src: str, dst: str) -> float:
        """Effective per-transfer DMA bandwidth.

        The architectural HBM throughput from uBM Handover (Quip
        XaMjAdbUuUAr) gives 461 GB/s aggregate for LNC1 with 16 engines
        via the channel-hashing model. nisa-performance-model.md
        Formula E adds partition-iteration striding (ceil(128/16)=8
        rounds), bringing per-transfer effective bandwidth to ~230 GB/s.

        Real workloads with sequential descriptor chains achieve
        roughly 1/3 of theoretical aggregate (per profiled VNC=2 MLP
        TKG measurements: 100-310 GB/s effective). The /6 divisor
        captures this combined effect of partition striding × dependency
        serialization.
        """
        if src == "SB" and dst == "SB":
            return self.hw.dma.transpose_sbuf_to_sbuf
        return self.hw.dma.bandwidth_per_engine * self.hw.dma.num_engines / 2

    def _control_op_cost(self, inst: SimInstruction) -> float:
        """Cost for control/sequencer instructions.

        EVENT_SEMAPHORE active_time varies widely by engine (561-6298ns),
        so it gets a per-engine table. Other ops use a flat default.
        """
        if inst.opcode == "EVENT_SEMAPHORE":
            engine_type = inst.engine_type
            if engine_type in EVENT_SEMAPHORE_NS_BY_ENGINE:
                return EVENT_SEMAPHORE_NS_BY_ENGINE[engine_type]
            # Fall back: try by raw engine name prefix.
            ename = inst.engine_name
            for key, val in EVENT_SEMAPHORE_NS_BY_ENGINE.items():
                if ename.startswith(key):
                    return val
            return CONTROL_OP_REAL_NS["EVENT_SEMAPHORE"]
        if inst.opcode in CONTROL_OP_REAL_NS:
            return CONTROL_OP_REAL_NS[inst.opcode]
        if inst.opcode == "NOP":
            cycle_count = inst.raw.extra.get("cycle", 0) if inst.raw else 0
            if cycle_count > 0:
                config = self.hw.get_engine_config(inst.engine_type)
                return cycle_count * 100.0 / config.frequency
            return 20.0  # Minimal cost for NOP
        if inst.opcode == "CORE_BARRIER":
            return 500.0  # ~500 ns from NKI ISA doc
        return 1.0  # Negligible for other control ops
