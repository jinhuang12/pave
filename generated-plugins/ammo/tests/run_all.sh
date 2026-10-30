#!/bin/bash
# AMMO test runner: runs the full local test surface of the plugin.
#   1. the pytest suite under tests/ (the conventions §9 battery)
#   2. every bash hook harness under hooks/tests/test-*.sh
#
# Each child is judged by its EXIT CODE (each harness exits non-zero on any
# failure), NOT by parsing its summary line: harnesses use different summary
# formats ("Results: N passed ..." vs "N / N passed"), so exit code is the only
# uniform signal. The runner prints a per-child line + an aggregate footer and
# exits 1 if ANY child failed.
#
# Usage:  bash tests/run_all.sh
# PYTEST overrides the pytest command. Default: the conventions §9 uv command,
# else `python3 -m pytest` when uv is not on PATH.
# The hook harnesses are judged on Linux (they need flock and GNU stat).
set -uo pipefail

# ── Resolve BASE (the plugin root) from this script's own location ──
#    THIS = <plugin>/tests/run_all.sh  →  BASE = <plugin>
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTEST_DIR="$BASE/tests"
HARNESS_DIR="$BASE/hooks/tests"

FAILED=0
TOTAL=0
declare -a FAILED_NAMES=()

run_child() {
    local label="$1"; shift
    TOTAL=$((TOTAL + 1))
    echo ""
    echo "──────────────────────────────────────────────"
    echo ">>> $label"
    echo "──────────────────────────────────────────────"
    local rc=0
    "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "<<< FAILED ($label) — exit $rc"
        FAILED=$((FAILED + 1))
        FAILED_NAMES+=("$label")
    else
        echo "<<< OK ($label)"
    fi
}

cd "$BASE" || exit 1

# ── 1. pytest suite ──
if [ -n "${PYTEST:-}" ]; then
    read -r -a PYTEST_CMD <<<"$PYTEST"
elif command -v uv >/dev/null 2>&1; then
    PYTEST_CMD=(uv run --no-project --with pytest --with pyyaml --with jsonschema
                --with pydantic --with httpx --with numpy --with psutil python -m pytest)
else
    echo "note: uv is not on PATH; using python3 -m pytest"
    PYTEST_CMD=(python3 -m pytest)
fi
run_child "pytest: tests" "${PYTEST_CMD[@]}" "$PYTEST_DIR" -q -m "not neuron and not requires_gpu"

# ── 2. every hook harness ──
shopt -s nullglob
for harness in "$HARNESS_DIR"/test-*.sh; do
    run_child "hook: $(basename "$harness")" bash "$harness"
done
shopt -u nullglob

# ── Aggregate footer ──
echo ""
echo "================================================"
echo "AMMO run_all: $((TOTAL - FAILED))/$TOTAL child suites passed"
if [ "$FAILED" -gt 0 ]; then
    echo "FAILED suites:"
    for n in "${FAILED_NAMES[@]}"; do
        echo "  - $n"
    done
    echo "================================================"
    exit 1
fi
echo "All suites green."
echo "================================================"
exit 0
