#!/bin/bash
# Layer-count bisection driver for a bring-up compile crash.
#
# A crash that depends on model size points at the offending layer type. This runs a
# bench/launch command at a sweep of reduced layer counts and greps each run for the
# crash signature vs a successful NEFF.
#
# PORT-SPECIFIC RULE: 4 layers can FAIL where 6 COMPILES (ISCH719 EP-collective /
# extra-output topology). Always include 6, and prefer 6 (not 4) as the smoke config.
# After finding the threshold, STUB the suspect layer to identity: if the error persists,
# the culprit is SHARED INFRA (the sampler's _pad ops), not your layer.
#
# Usage: layer_bisect.sh '<bench command using $L for num_hidden_layers>'
#   The command string MUST reference $L where the layer count goes.
#
# Example:
#   layer_bisect.sh 'HF_OVERRIDES="{\"text_config\":{\"num_hidden_layers\":$L}}" python bench.py'

set -uo pipefail

CMD_TEMPLATE="${1:-}"
if [ -z "$CMD_TEMPLATE" ]; then
    echo "Usage: $0 '<bench command referencing \$L>'" >&2
    exit 1
fi

# 4 first (may fail where 6 passes), then climb.
LAYERS="${LAYER_SWEEP:-4 6 8 16 32}"
SIG='INTERNAL_ERROR|NCC_[IE][A-Z]+[0-9]+|Assertion failed|compilation failed with 70|Throughput|NEFF'

for L in $LAYERS; do
    echo "================ num_hidden_layers=$L ================"
    CMD=$(L=$L eval "echo \"$CMD_TEMPLATE\"")
    echo "+ $CMD"
    # -a: neuron logs often flagged binary. Show first couple of signal lines only.
    eval "$CMD" 2>&1 | grep -a -iE "$SIG" 2>/dev/null | head -3
    echo "(rc=${PIPESTATUS[0]})"
done

cat <<'EOF'

=== Interpreting the threshold ===
  - crash appears at/after a specific L  -> that layer type is implicated
    (e.g. global vs sliding attention in Gemma4 — one layer of the type may suffice)
  - crash at L=4 but clean at L=6        -> ISCH719 EP-collective/extra-output topology;
                                            use 6 reduced layers, remove extra graph outputs
  - NEXT: stub the suspect layer to nn.Identity; if the error PERSISTS the culprit is
    shared infra (sampler / mask helper), not your layer.
EOF
