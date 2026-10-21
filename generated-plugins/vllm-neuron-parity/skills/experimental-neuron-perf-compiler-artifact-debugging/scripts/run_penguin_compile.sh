#!/bin/bash
# Stage an HLO into a fresh dir and run penguin debug compile.
# Usage: run_penguin_compile.sh <source_graph.hlo> <output_dir> [extra neuronx-cc flags]
#
# Example:
#   run_penguin_compile.sh \
#     /var/tmp/compile_cache/<hash>/graph.hlo \
#     /mnt/data/penguin_dbg_<run_name>

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <source_graph.hlo> <output_dir> [extra neuronx-cc flags]"
    echo ""
    echo "Required env: NEURONX_CC_TARGET (default: trn2)"
    echo "Optional env: NEURONX_CC_FLAGS (extra flags appended)"
    exit 1
fi

SRC_HLO="$1"; shift
OUT_DIR="$1"; shift

TARGET="${NEURONX_CC_TARGET:-trn2}"

if [ ! -f "$SRC_HLO" ]; then
    echo "ERROR: source HLO not found: $SRC_HLO" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
cp "$SRC_HLO" "$OUT_DIR/graph.hlo"
cd "$OUT_DIR"

NEURONXCC=$(command -v neuronx-cc)
if [ -z "$NEURONXCC" ]; then
    echo "ERROR: neuronx-cc not on PATH (activate the venv first)" >&2
    exit 3
fi

echo "=== penguin compile ==="
echo "  source HLO: $SRC_HLO"
echo "  output dir: $OUT_DIR"
echo "  target:     $TARGET"
echo "  compiler:   $($NEURONXCC --version 2>&1 | head -1)"
echo ""

set +e
$NEURONXCC compile graph.hlo \
    --framework XLA \
    --target "$TARGET" \
    --output graph.neff \
    --logfile log-neuron-cc.txt \
    --auto-cast=none \
    --verbose=35 \
    -O1 \
    --internal-backend-options=--enable-verifier=false \
    --internal-compiler-debug-mode=penguin \
    "$@"
RC=$?
set -e

echo ""
echo "=== exit=$RC ==="
if [ $RC -eq 0 ]; then
    echo "  NEFF: $(ls -lh graph.neff | awk '{print $5}')"
fi

echo "  penguin sg dirs: $(ls -d penguin-sg* 2>/dev/null | wc -l)"
echo "  TiledOffloadedMemCpy ops: $(grep -c TiledOffloadedMemCpy penguin-sg*/penguin.py 2>/dev/null | awk -F: '{s+=$2} END {print (s ? s : 0)}')"

if grep -qE "INTERNAL_ERROR|Assertion failed" log-neuron-cc.txt 2>/dev/null; then
    echo ""
    echo "  ASSERT detected:"
    grep -m1 -A1 "INTERNAL_ERROR\|Assertion failed" log-neuron-cc.txt | sed 's/^/    /'
fi

exit $RC
