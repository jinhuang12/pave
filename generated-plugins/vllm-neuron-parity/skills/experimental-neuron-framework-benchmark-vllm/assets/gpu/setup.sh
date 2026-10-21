#!/usr/bin/env bash
# One-time GPU node setup: RAID0 the local NVMe SSDs -> /scratch, pull the
# vLLM image, create the bench venv and pip-install InferencePerfAnalyzer (the
# orchestrator that drives the sweep) + pytest, stage the ShareGPT dataset.
# Run once after the instance is up. Idempotent: safe to re-run.
#
# The agent scp's the InferencePerfAnalyzer source tree onto the box first
# (default dest: $HOME/InferencePerfAnalyzer — /scratch doesn't exist until step 1
# below); this installs it -e into a venv. Override paths via env: IPA_SRC,
# BENCH_VENV, VLLM_IMG.
set -uo pipefail

VLLM_IMG="${VLLM_IMG:-vllm/vllm-openai:v0.20.0-cu130-ubuntu2404}"
# IPA source: scp'd BEFORE this script runs, so it must land somewhere that exists
# pre-setup — the home dir, NOT /scratch (which THIS script creates in step 1).
IPA_SRC="${IPA_SRC:-$HOME/InferencePerfAnalyzer}"
BENCH_VENV="${BENCH_VENV:-/scratch/bench-venv}"

echo "=== 1. RAID0 local NVMe SSDs -> /scratch (optional; skipped if /scratch ready) ==="
if mountpoint -q /scratch; then
  echo "  /scratch already mounted — skipping RAID setup"
else
  # data SSDs only: exclude the root EBS volume (it carries partitions / is mounted at /)
  mapfile -t SSDS < <(lsblk -dpno NAME,TYPE | awk '$2=="disk"{print $1}' \
      | grep -E '/dev/nvme[0-9]+n1$' \
      | while read -r d; do
          # skip any disk that has partitions or is the root device
          if ! lsblk -no MOUNTPOINT "$d" | grep -q . && [ "$(lsblk -no TYPE "$d" | tail -n +2 | grep -c part)" -eq 0 ]; then
            echo "$d"
          fi
        done)
  echo "  candidate data SSDs: ${SSDS[*]:-none}"
  if [ "${#SSDS[@]}" -ge 2 ]; then
    sudo dnf install -y mdadm >/dev/null 2>&1 || sudo apt-get install -y mdadm >/dev/null 2>&1 || true
    sudo mdadm --create /dev/md0 --level=0 --force --run --raid-devices="${#SSDS[@]}" "${SSDS[@]}"
    cat /proc/mdstat
    sudo mkfs.ext4 -F /dev/md0
    sudo mkdir -p /scratch
    sudo mount /dev/md0 /scratch
    sudo chown "$USER:$USER" /scratch
  else
    echo "  <2 data SSDs found — using plain dir on root volume instead"
    sudo mkdir -p /scratch && sudo chown "$USER:$USER" /scratch
  fi
fi
mkdir -p /scratch/hf /scratch/results
# Point the HF cache at /scratch (the big RAID0), NOT the small root EBS. IPA
# hardcodes HF_VOLUME=~/.cache/huggingface, so without this symlink the ~200GB+
# weight download lands on root and can fill a default-sized root volume (a 235B
# model ≈ its whole 250GB). Symlink makes IPA's mount resolve to /scratch/hf.
if [ ! -L "$HOME/.cache/huggingface" ]; then
  rm -rf "$HOME/.cache/huggingface" 2>/dev/null || true
  mkdir -p "$HOME/.cache"
  ln -s /scratch/hf "$HOME/.cache/huggingface"
  echo "  symlinked ~/.cache/huggingface -> /scratch/hf (weights use the RAID0, not root EBS)"
fi
df -h /scratch

echo "=== 2. docker pull ${VLLM_IMG} ==="
sudo docker pull "$VLLM_IMG"

echo "=== 3. bench venv + InferencePerfAnalyzer (orchestrator) + pytest ==="
# DOCKER-TRANSPORT ONLY. Under --transport=docker the orchestrator runs on the HOST
# (not in the vLLM container); it starts/stops the vLLM container per sharding and
# drives `vllm bench serve` inside it. The Kaizen transport uses a baked image
# (IPA+pytest pre-installed) instead, so this venv step is unnecessary there.
if [ ! -d "$IPA_SRC" ]; then
  echo "  ERROR: IPA source not found at $IPA_SRC — scp the InferencePerfAnalyzer"
  echo "         source tree onto the box first (or set IPA_SRC). Skipping venv."
else
  python3 -m venv "$BENCH_VENV"
  # shellcheck disable=SC1091
  "$BENCH_VENV/bin/pip" install --quiet --upgrade pip
  "$BENCH_VENV/bin/pip" install --quiet pytest
  # IPA's pyproject.toml declares NO [project] dependencies (they're Brazil-managed
  # via its Config), so `pip install -e` does NOT pull IPA's runtime deps. Install
  # them explicitly or `import inference_perf_analyzer.orchestrator` fails with
  # ModuleNotFoundError (requests — HTTP health/metrics; boto3 — S3 publish/sync).
  "$BENCH_VENV/bin/pip" install --quiet requests boto3
  "$BENCH_VENV/bin/pip" install --quiet -e "$IPA_SRC"
  echo "  venv: $BENCH_VENV"

  # --- Patch IPA's docker-command timeout for LARGE models -----------------
  # IPA run_local.py hardcodes _DOCKER_CMD_TIMEOUT_S = 120, which gates BOTH
  # `docker run -d` and `docker stop`. For a large multi-GPU model, `docker run
  # -d --gpus all ...` blocks on GPU/NVML device reservation longer than 120s
  # before returning the container id -> subprocess.run raises TimeoutExpired ->
  # every NEW server config fails to start (and the created container then
  # orphans, and `docker stop` also blows the 120s cap so the pool can't tear it
  # down). This bit run #1: only the first tp8 server ever came up; tp4_dp2 and
  # the 2nd shape never launched. Pre-pulling the image (step 2) does NOT fix it
  # — the timeout is on the detached-run's device attach, not the pull.
  # Fix here (docker transport, our own IPA clone): bump the constant. Override
  # via IPA_DOCKER_CMD_TIMEOUT_S. Upstream ask: make this env-configurable in IPA.
  IPA_DOCKER_CMD_TIMEOUT_S="${IPA_DOCKER_CMD_TIMEOUT_S:-900}"
  RUN_LOCAL="$IPA_SRC/src/inference_perf_analyzer/orchestrator/run_local.py"
  if [ -f "$RUN_LOCAL" ] && grep -q '^_DOCKER_CMD_TIMEOUT_S = ' "$RUN_LOCAL"; then
    sed -i.bak -E "s/^_DOCKER_CMD_TIMEOUT_S = .*/_DOCKER_CMD_TIMEOUT_S = ${IPA_DOCKER_CMD_TIMEOUT_S}  # patched by setup.sh for large multi-GPU models/" "$RUN_LOCAL"
    echo "  patched IPA _DOCKER_CMD_TIMEOUT_S -> ${IPA_DOCKER_CMD_TIMEOUT_S}s (was 120; large-model docker-run/stop need longer)"
  else
    echo "  NOTE: could not find _DOCKER_CMD_TIMEOUT_S in $RUN_LOCAL — IPA layout changed? verify the docker timeout before a large-model run."
  fi

  "$BENCH_VENV/bin/python" -c "import inference_perf_analyzer.orchestrator as o; \
import pytest; print('  IPA + pytest OK:', pytest.__version__)" \
    || echo "  WARN: IPA import failed — check the scp'd source at $IPA_SRC"
fi

echo "=== 4. stage ShareGPT dataset (default workload; harmless if you use random) ==="
DS=/scratch/sharegpt.json
if [ ! -s "$DS" ]; then
  curl -fL -o "$DS" \
    https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json \
    && echo "  sharegpt staged ($(du -h "$DS" | cut -f1))" \
    || echo "  WARN: sharegpt download failed (bench will fall back to --dataset-name random)"
else
  echo "  sharegpt already present"
fi

echo "=== 5. GPU sanity ==="
nvidia-smi --query-gpu=index,name,memory.total --format=csv 2>/dev/null || echo "  (nvidia-smi unavailable)"
echo "=== SETUP DONE ==="
