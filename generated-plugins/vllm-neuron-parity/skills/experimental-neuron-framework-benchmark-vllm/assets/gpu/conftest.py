"""Make IPA's orchestrator fixtures available to sweep_bench.py.

sweep_bench.py lives here (a skill asset), OUTSIDE IPA's package tree, so pytest
does NOT auto-discover IPA's own conftest — and without it the `--transport`
option and the `results_root` / `server_handle_pool` / `sweep_state` fixtures
the sweep depends on are undefined. Loading IPA's conftest as a plugin registers
all of them (hooks + fixtures).

`pytest_plugins` must live in the top-level (rootdir) conftest, which this is
whenever you invoke `pytest sweep_bench.py` from this directory.
"""

pytest_plugins = ("inference_perf_analyzer.orchestrator.conftest",)
