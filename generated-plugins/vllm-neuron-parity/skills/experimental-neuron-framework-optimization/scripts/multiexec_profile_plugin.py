#!/usr/bin/env python3
"""multiexec_profile_plugin.py — make one kernel bring-up profile ALL executions.

The kernel-loop oracle wants N cycle samples per bring-up so its robust reducer (cycle_stats,
IQR-filtered average) can reject a run-to-run outlier. The harness already RUNS the kernel
`num_runs` times (determinism check) but, with the test's `InferenceArgs.profile_all_runs = False`,
profiles only the LAST execution -> one NTFF -> one show_session_0.json -> one sample.

This pytest plugin flips that to profile every execution -- WITHOUT mutating the harness's shared
`TKG_INFERENCE_ARGS` singleton in place. Instead it builds a CONFIGURED COPY of that config object
(`dataclasses.replace(orig, profile_all_runs=True)`, which returns a fresh instance and re-runs
__post_init__) and rebinds the name the test resolves at call time to our copy. Nothing in the
installed package is edited; the original singleton is left untouched for any other consumer.

Result: one bring-up emits show_session_0..N-1.json (N = num_runs), all fed to cycle_stats.

Enabled by measure.py via `-p multiexec_profile_plugin` (the scripts dir is put on PYTHONPATH).
Set MEASURE_MULTIEXEC=0 to disable (fall back to single-exec profiling).
"""
from __future__ import annotations

import dataclasses
import os
import sys

_ATTR = "TKG_INFERENCE_ARGS"


def _configured_copy(orig):
    """A fresh, skill-configured copy of the inference-args object with all-exec profiling on.

    Uses dataclasses.replace so the original shared instance is NOT mutated. Falls back to a
    shallow attribute copy if the object is not a dataclass."""
    try:
        return dataclasses.replace(orig, profile_all_runs=True)
    except Exception:
        import copy
        c = copy.copy(orig)
        try:
            c.profile_all_runs = True
        except Exception:
            pass
        return c


def _inject(mod) -> bool:
    cur = getattr(mod, _ATTR, None)
    if cur is None or getattr(cur, "profile_all_runs", True):
        return False
    setattr(mod, _ATTR, _configured_copy(cur))
    return True


def pytest_collection_modifyitems(config, items):
    """After collection every test module is imported and its `TKG_INFERENCE_ARGS` name is bound;
    rebind that name (in each collected item's module) to our all-exec copy before any test runs."""
    if os.environ.get("MEASURE_MULTIEXEC", "1") == "0":
        return
    seen, patched = set(), 0
    for it in items:
        mod = getattr(it, "module", None)
        if mod is None or id(mod) in seen:
            continue
        seen.add(id(mod))
        if _inject(mod):
            patched += 1
    # also cover the source module, in case a test resolves the singleton via the package name
    src = sys.modules.get("nkilib_testing.common_dataclasses")
    if src is not None:
        _inject(src)
    if patched:
        print(f"[multiexec_profile_plugin] all-exec profiling enabled on {patched} test module(s)")
