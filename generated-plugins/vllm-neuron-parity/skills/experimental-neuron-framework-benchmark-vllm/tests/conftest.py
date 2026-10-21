"""Shared path setup for the experimental-neuron-framework-benchmark-vllm deliverables tests.

These tests live inside the skill (alongside its assets/, not in the package-level tests/
dir) so the skill stays self-contained — the same co-location the
neuron-parallelism-planner skill uses for its unit tests. The deliverables
scripts (build_deliverables.py, build_cost_deliverables.py) sit in the sibling assets/gpu/
dir and import each other / stdlib as flat modules (not a package), so we put that dir on
sys.path before importing them.

Scope note: these two scripts import only pandas at module load (matplotlib is imported
lazily inside the plotting functions, so tests that don't plot never need it). We do NOT
test sweep_bench.py here — it imports `inference_perf_analyzer.orchestrator` at module
load, which isn't available in the unit-test env; its pure helpers would need to be
factored into a dependency-free module first (tracked as a follow-up).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GPU = os.path.normpath(os.path.join(HERE, "..", "assets", "gpu"))
if GPU not in sys.path:
    sys.path.insert(0, GPU)
