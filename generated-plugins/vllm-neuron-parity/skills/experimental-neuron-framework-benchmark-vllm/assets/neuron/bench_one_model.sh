#!/bin/bash
# vllm bench serve for ONE bringup model on Neuron (trn2).
# Serves the model, waits for the OpenAI endpoint, runs `vllm bench serve`
# against the sharegpt dataset, prints the metrics block, tears the server down.
#
# Injected by the submitter via env:
#   MODEL_ID   - HF id (e.g. allenai/OLMo-2-0425-1B)
#   TP         - tensor-parallel size (e.g. 8)
#   NUM_PROMPTS- bench request count (default 200)
#   MAX_CONC   - max concurrency (default = max_num_seqs)
#   SEQ_LEN    - max model len (default 4096)
#   HF_TOKEN   - HF auth (gated/large weights); injected by submitter, not written here
set -uo pipefail

export PYTHONUNBUFFERED=1
export VLLM_PLUGINS=neuron
export TOKENIZERS_PARALLELISM=false
export NEURON_SKIP_EFA_AFFINITY=1
export HF_HUB_ENABLE_HF_TRANSFER=0
export VLLM_LOGGING_LEVEL=ERROR VLLM_CONFIGURE_LOGGING=0
export NEURON_RT_LOG_LEVEL=ERROR NEURON_RT_LOG_LOCATION=console
export TRANSFORMERS_VERBOSITY=error PYTHONWARNINGS=ignore TQDM_DISABLE=1
cd /workspace/plugin

: "${MODEL_ID:?MODEL_ID required}"
TP="${TP:-8}"
NUM_PROMPTS="${NUM_PROMPTS:-200}"
SEQ_LEN="${SEQ_LEN:-4096}"
MAX_NUM_SEQS="${MAX_NUM_SEQS:-16}"
MAX_CONC="${MAX_CONC:-$MAX_NUM_SEQS}"
PORT="${PORT:-8000}"
OUT="${WORKLOAD_OUTPUT_RW:-/tmp}/bench"
mkdir -p "$OUT"

echo "########## VLLM BENCH START $(date -u +%H:%M:%S) :: ${MODEL_ID} tp=${TP} ##########"
neuron-ls 2>&1 | head -3 || echo "(neuron-ls unavailable)"

# --- sharegpt dataset (download once; node has internet) ---
DS="$OUT/sharegpt.json"
if [ ! -s "$DS" ]; then
  echo "=== downloading sharegpt dataset ==="
  curl -fsSL -o "$DS" \
    https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json \
    || { echo "WARN: sharegpt download failed; falling back to random dataset"; DS=""; }
fi

# --- serve (background) ---
neuron_config='{"neuron_config": {"quantization": "bf16", "num_batched_tokens_buckets": ['"$SEQ_LEN"'], "num_seqs_buckets": ['"$MAX_NUM_SEQS"']}}'
echo "=== vllm serve ${MODEL_ID} (tp=${TP}, max_model_len=${SEQ_LEN}) ==="
vllm serve "$MODEL_ID" \
  --tensor-parallel-size "$TP" \
  --dtype bfloat16 \
  --max-model-len "$SEQ_LEN" \
  --max-num-batched-tokens "$SEQ_LEN" \
  --max-num-seqs "$MAX_NUM_SEQS" \
  --no-enable-log-requests \
  --no-enable-prefix-caching \
  --additional-config "$neuron_config" \
  > "$OUT/serve.log" 2>&1 &
SERVE_PID=$!

# --- wait for readiness (compile can take minutes) ---
echo "=== waiting for server (pid ${SERVE_PID}) ==="
READY=0
for i in $(seq 1 180); do   # up to ~30 min
  if ! kill -0 "$SERVE_PID" 2>/dev/null; then echo "SERVE DIED — tail:"; tail -30 "$OUT/serve.log"; exit 1; fi
  if curl -fsS "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then READY=1; echo "server READY after ~$((i*10))s"; break; fi
  sleep 10
done
[ "$READY" = 1 ] || { echo "SERVER NEVER READY — tail:"; tail -40 "$OUT/serve.log"; kill "$SERVE_PID" 2>/dev/null; exit 1; }

# --- bench ---
echo "=== vllm bench serve (num_prompts=${NUM_PROMPTS}, max_concurrency=${MAX_CONC}) ==="
BENCH_ARGS=(--backend vllm --model "$MODEL_ID" --host localhost --port "$PORT"
            --num-prompts "$NUM_PROMPTS" --max-concurrency "$MAX_CONC"
            --save-result --result-dir "$OUT" --result-filename "result.json")
if [ -n "$DS" ]; then
  BENCH_ARGS+=(--dataset-name sharegpt --dataset-path "$DS")
else
  BENCH_ARGS+=(--dataset-name random --random-input-len 1024 --random-output-len 256)
fi
set -x
vllm bench serve "${BENCH_ARGS[@]}" 2>&1 | tee "$OUT/bench.out"
BENCH_RC=${PIPESTATUS[0]}
set +x

echo "### BENCH ${MODEL_ID} tp=${TP} :: rc=${BENCH_RC}"
# echo the key metrics block for CloudWatch scraping
grep -iE "throughput|TTFT|TPOT|ITL|latency|request|token|p50|p99|mean|median" "$OUT/bench.out" | sed 's/^/METRIC| /'

kill "$SERVE_PID" 2>/dev/null
echo "########## VLLM BENCH DONE $(date -u +%H:%M:%S) :: ${MODEL_ID} ##########"
