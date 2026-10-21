# AUTO-STAGED standalone conftest for the decoupled kernel-loop tree.
#
# The installed nkilib_testing pytest plugin (auto-loaded via its pytest11 entry point) calls
# get_feature_flag(config, "<key>") for a set of feature-flag options. Most are registered by the
# plugin's own pytest_addoption, but a few (e.g. target_host_file) are normally registered by the
# CHECKOUT's test/conftest.py — which we deliberately do NOT depend on. get_feature_flag raises
# `no option named ...` when the option is absent, so this shim registers every referenced
# feature-flag key with a falsy default, skipping any the plugin already owns (duplicate -> ValueError).
#
# Result: get_feature_flag returns falsy for the standalone case, and the LOCAL (on-device)
# compile-and-infer provisioning path proceeds without the fleet/checkout conftest.

# Keys the installed plugin references via get_feature_flag but does NOT itself register (the
# checkout's test/conftest.py normally supplies them). Keep this MINIMAL: registering an option the
# plugin also registers can, depending on hook order, make the PLUGIN's registration conflict.
# Confirmed-missing on a standalone LOCAL compile-and-infer run: target_host_file. Expand ONLY if a
# run raises `no option named '<key>'` for another key.
_FEATURE_FLAG_KEYS = [
    "target_host_file",
]


def pytest_addoption(parser):
    group = parser.getgroup("nkilib-standalone-shim", "decoupled kernel-loop feature-flag shim")
    for key in _FEATURE_FLAG_KEYS:
        opt = "--" + key.replace("_", "-")
        try:
            group.addoption(opt, dest=key, action="store", default=None)
        except Exception:
            # already registered by the plugin/another conftest (argparse raises ArgumentError,
            # pytest may wrap it) -> that registration wins; skip ours.
            pass


# --- QoR / metrics bridge for the decoupled tree (brazil-free), env-gated -------------------
# The installed nkilib_testing emitter writes EMF metrics to <out>/metrics/ and qor_collector
# turns them into qor_data_*.csv (with ActiveInferenceTime / SeparatedComputeTime /
# SeparatedMemoryTime) -- but the CHECKOUT's test/conftest.py normally triggers emit()+collect_qor
# at teardown, which the decoupled tree lacks. This bridge reproduces that WITHOUT brazil so the
# separation/roofline/sharding specialists get their qor_separated input. It is gated on
# NKILIB_QOR_BRIDGE=1 (set only by collect_perf_data_local.py) so ordinary measure.py runs are
# completely unaffected. Two device passes -- one plain, one with NKILIB_ENABLE_SEPARATION_ANALYSIS=1
# -- are then diffed by compute_sch_ineff_private.py into qor_separated_analysis.csv.
import os as _os
import glob as _glob
import json as _json
import pytest as _pytest


def _install_numpy_safe_json():
    # The installed EMF serializer json.dumps() a metrics context that can hold numpy scalars;
    # outside brazil that raises "Object of type float32 is not JSON serializable". Install a
    # numpy-coercing default so emission succeeds. Idempotent; only touched when the bridge is on.
    if getattr(_json, "_nkilib_np_safe", False):
        return
    try:
        import numpy as _np
    except Exception:
        return
    _orig = _json.dumps

    def _safe(*a, **k):
        if "default" not in k:
            k["default"] = lambda o: (float(o) if isinstance(o, _np.floating)
                                      else int(o) if isinstance(o, _np.integer) else str(o))
        return _orig(*a, **k)

    _json.dumps = _safe
    _json._nkilib_np_safe = True


@_pytest.fixture(autouse=True)
def _qor_metrics_bridge(request, collector, emitter):
    """Emit harness metrics and collect them into qor_data_*.csv after each test. Best-effort;
    never fails the test. Active ONLY when NKILIB_QOR_BRIDGE=1 (collection passes). `collector`
    and `emitter` are declared as PARAMETERS (not getfixturevalue) so they are still alive during
    this fixture's teardown -- requesting them lazily at teardown fails ('already torn down')."""
    yield
    if _os.environ.get("NKILIB_QOR_BRIDGE") != "1":
        return
    try:
        _install_numpy_safe_json()
        emitter.emit(collector)                       # -> <out>/metrics/<test>_<ts>.json
        base = request.config.getoption("--output-directory", None)
        if not base:
            return
        outs = sorted(_glob.glob(_os.path.join(base, "out-*")), key=_os.path.getmtime)
        od = outs[-1] if outs else None
        if od and _os.path.isdir(od):
            from nkilib_testing.qor_collector import collect_qor_from_test_dir
            collect_qor_from_test_dir(od, base)       # -> <base>/qor_data_*.csv
    except Exception as _e:
        print(f"[qor_metrics_bridge] skipped: {_e}")
