"""Hardware configuration constants per target (trn1, trn2, trn3)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict


class ThrottleModel(ABC):
    """Abstract throttle model — arch-specific subclasses implement penalty logic."""

    @abstractmethod
    def compute_penalty_ns(
        self, idle_gap_ns: float, exec_latency_ns: float
    ) -> float:
        """Compute additional execution time due to throttling.

        Args:
            idle_gap_ns: How long the engine was idle before this instruction.
            exec_latency_ns: Nominal (unthrottled) execution latency.

        Returns:
            Additional nanoseconds to add to execution time.
        """
        ...


class NoThrottle(ThrottleModel):
    """No throttle penalty (e.g., trn1 uses a constant 8/7 multiplier baked into freq)."""

    def compute_penalty_ns(self, idle_gap_ns: float, exec_latency_ns: float) -> float:
        return 0.0


class DiDtThrottle(ThrottleModel):
    """Di/Dt current-based throttle with hysteresis (trn2).

    Per Throttle.parquet: trn2 di/dt protection is a 2-stage hysteresis.
    After engine becomes idle (gap > idle_threshold_ns), engine enters
    throttled state. Once it resumes at >=64 free-dim activity:
      * For first `activity_0_ns`, engine runs at 100% (ramp-up window)
      * For next `activity_1_ns`, engine runs at reduced ratio
        (ratio_numerator/ratio_denominator → 50% by default)
      * After total throttled time elapses, returns to 100%.

    For a single-instruction latency model we approximate this by adding
    fixed throttle penalty per idle-event, scaled by the burst length:
        penalty = (activity_1_ns) × slowdown
    only once per idle gap (the throttle window covers all subsequent
    instructions until activity_1_ns elapses).
    """

    def __init__(
        self,
        ratio_numerator: int = 4,
        ratio_denominator: int = 8,
        duration_ns: float = 3000.0,
        idle_threshold_ns: float = 100.0,
        activity_0_ns: float = 3400.0,
        activity_1_ns: float = 14000.0,
    ):
        self.ratio_numerator = ratio_numerator
        self.ratio_denominator = ratio_denominator
        self.duration_ns = duration_ns
        self.idle_threshold_ns = idle_threshold_ns
        self.activity_0_ns = activity_0_ns
        self.activity_1_ns = activity_1_ns
        self._slowdown = (ratio_denominator / ratio_numerator) - 1.0

    def compute_penalty_ns(self, idle_gap_ns: float, exec_latency_ns: float) -> float:
        if idle_gap_ns < self.idle_threshold_ns:
            return 0.0
        throttled_portion = min(exec_latency_ns, self.duration_ns)
        return throttled_portion * self._slowdown


@dataclass(frozen=True)
class EngineConfig:
    """Configuration for a single compute engine."""
    frequency: int  # GHz * 100 (e.g., 240 = 2.40 GHz)
    write_drain_ns: float
    semaphore_start_ns: float
    turnaround_cycles: int = 20  # L8: engine instruction turnaround


@dataclass(frozen=True)
class DMAConfig:
    """Configuration for the DMA engine."""
    bandwidth_per_engine: float  # GB/s (= bytes/ns)
    transpose_hbm_to_sbuf: float  # GB/s
    transpose_sbuf_to_sbuf: float  # GB/s
    num_engines: int = 16
    semaphore_start_ns: float = 1300.0
    write_drain_ns: float = 0.0
    # Per "DMA Trigger Time Microbenchmarks" (Quip JFyDAnX9EPL4):
    #   Static (no DGE)      : 0.7-0.8 µs
    #   HWDGE                : 1.2 µs
    #   SWDGE (compiler default): 1.5 µs
    # 2026-05-28: lowered 2000->1500 via empirical sweep - improved mean
    # abs from 16.57 to 16.32, W10 from 42.9 to 44.3, kept W20 at 72.9.
    static_startup_ns: float = 1500.0  # base DMA duration per trn_hw_info.md
    dge_startup_ns: float = 2000.0  # DGE DMA startup latency


@dataclass(frozen=True)
class HardwareConfig:
    """Complete hardware configuration for a target."""
    name: str
    tensor: EngineConfig
    vector: EngineConfig
    gpsimd: EngineConfig
    scalar: EngineConfig
    dma: DMAConfig
    throttle: ThrottleModel = None  # type: ignore[assignment]
    num_psum_banks: int = 8
    num_partitions: int = 128

    def __post_init__(self) -> None:
        if self.throttle is None:
            object.__setattr__(self, "throttle", NoThrottle())

    def get_engine_config(self, engine_type: str) -> EngineConfig:
        """Get config for a given engine type string."""
        mapping = {
            "Tensor": self.tensor,
            "PE": self.tensor,
            "Vector": self.vector,
            "DVE": self.vector,
            "GpSimd": self.gpsimd,
            "Pool": self.gpsimd,
            "Scalar": self.scalar,
            "Activation": self.scalar,
            # Sync (SP) shares scalar-engine cost characteristics on
            # trn2 (1.2 GHz, similar issue/drain). Real OpcodeSummary
            # reports SP-side ops under engine="sync".
            "Sync": self.scalar,
            "SP": self.scalar,
        }
        if engine_type in mapping:
            return mapping[engine_type]
        raise ValueError(f"Unknown engine type: {engine_type}")

    def cycles_to_ns(self, cycles: float, engine_type: str) -> float:
        """Convert engine cycles to nanoseconds."""
        config = self.get_engine_config(engine_type)
        return cycles * 100.0 / config.frequency


TRN1 = HardwareConfig(
    name="trn1",
    tensor=EngineConfig(frequency=280, write_drain_ns=150, semaphore_start_ns=81, turnaround_cycles=70),
    vector=EngineConfig(frequency=112, write_drain_ns=161, semaphore_start_ns=268, turnaround_cycles=20),
    gpsimd=EngineConfig(frequency=140, write_drain_ns=218, semaphore_start_ns=186, turnaround_cycles=305),
    scalar=EngineConfig(frequency=140, write_drain_ns=93, semaphore_start_ns=229, turnaround_cycles=20),
    dma=DMAConfig(bandwidth_per_engine=17, transpose_hbm_to_sbuf=335, transpose_sbuf_to_sbuf=215),
    # trn1: constant 8/7 multiplier baked into PE frequency (no dynamic throttle)
    throttle=NoThrottle(),
)

TRN2 = HardwareConfig(
    name="trn2",
    # All write_drain/semaphore_start values are canonical from
    # trn_hw_info.md (no empirical adjustment).
    tensor=EngineConfig(frequency=240, write_drain_ns=150, semaphore_start_ns=81, turnaround_cycles=76),
    vector=EngineConfig(frequency=96, write_drain_ns=161, semaphore_start_ns=268, turnaround_cycles=20),
    gpsimd=EngineConfig(frequency=120, write_drain_ns=218, semaphore_start_ns=186, turnaround_cycles=305),
    scalar=EngineConfig(frequency=120, write_drain_ns=93, semaphore_start_ns=229, turnaround_cycles=20),
    # DMA bandwidth: canonical 23 GB/s per nisa-performance-model.md.
    dma=DMAConfig(bandwidth_per_engine=23, transpose_hbm_to_sbuf=335, transpose_sbuf_to_sbuf=215),
    # Throttle parquet shows activity_1 throttle bursts of ~28µs wall-clock
    # at 50% throughput → 14µs nominal-time slowdown applied per streak.
    throttle=DiDtThrottle(ratio_numerator=4, ratio_denominator=8, duration_ns=14000.0),
)

TRN3 = HardwareConfig(
    name="trn3",
    tensor=EngineConfig(frequency=240, write_drain_ns=150, semaphore_start_ns=81, turnaround_cycles=76),
    vector=EngineConfig(frequency=120, write_drain_ns=161, semaphore_start_ns=268, turnaround_cycles=20),
    gpsimd=EngineConfig(frequency=120, write_drain_ns=218, semaphore_start_ns=186, turnaround_cycles=305),
    scalar=EngineConfig(frequency=120, write_drain_ns=93, semaphore_start_ns=229, turnaround_cycles=20),
    dma=DMAConfig(
        bandwidth_per_engine=33,
        transpose_hbm_to_sbuf=480,
        transpose_sbuf_to_sbuf=308,
        static_startup_ns=1000.0,
        dge_startup_ns=2000.0,
    ),
    # trn3: throttle behavior TBD — placeholder using trn2 values
    throttle=DiDtThrottle(ratio_numerator=4, ratio_denominator=8, duration_ns=3000.0),
)

TARGETS: Dict[str, HardwareConfig] = {
    "trn1": TRN1,
    "trn2": TRN2,
    "trn3": TRN3,
}


def get_hardware_config(target: str) -> HardwareConfig:
    """Get hardware configuration for a target name."""
    if target not in TARGETS:
        raise ValueError(f"Unknown target: {target}. Available: {list(TARGETS.keys())}")
    return TARGETS[target]
