#!/usr/bin/env bash
# Continuously snapshot the active vLLM bench container's logs to the results
# tree so they survive the container. IPA (run_local.py) does `docker rm -f`
# after each cell (and on argv change), so server logs otherwise DIE WITH THE
# CONTAINER — making a failed/slow server-start impossible to debug post-hoc.
# This is exactly what bit run #1 (Qwen3-235B): the box was torn down before the
# one-shot rsync, and only a manual live capture preserved any launch evidence.
#
# DOCKER TRANSPORT ONLY. (Kaizen has its own log story: `--tail-logs` streams pod
# logs, and the pod's stdout is captured by the workload — this script is for the
# `--transport=docker` path where IPA drives short-lived containers on the host.)
#
# Run in its OWN tmux/background window alongside the sweep:
#   ORCHESTRATOR_RESULTS_ROOT=/scratch/results ./capture_logs.sh [interval_s]   # default 30
# or detached:
#   ORCHESTRATOR_RESULTS_ROOT=/scratch/results nohup ./capture_logs.sh 30 >/tmp/caplog.out 2>&1 &
#
# Logs nest under $ORCHESTRATOR_RESULTS_ROOT/serverlogs, so they group with the
# run and any results sync (rsync or `aws s3 sync` over /scratch/results) ships
# them alongside result.json. Export ORCHESTRATOR_RESULTS_ROOT in THIS window too
# (falls back to /scratch/results if unset). build_deliverables.py already skips a
# serverlogs/ dir when walking results, so this never pollutes the cell parse.
#
# Each cycle dumps the FULL log of the currently-running bench container (`docker
# logs` returns full history each time), so re-dumping is fine — the file grows and
# the sync overwrites it. Sampling means a container living < interval could be
# missed, but a slow-loading or saturating 235B server is up for MINUTES, which is
# exactly the case we need captured.

set -uo pipefail

LOGDIR="${ORCHESTRATOR_RESULTS_ROOT:-/scratch/results}/serverlogs"
INTERVAL="${1:-30}"

echo "Capturing bench container logs to $LOGDIR every ${INTERVAL}s. Ctrl-C to stop."

while true; do
  # IPA names its containers bench-<pid>-<hash>; grab whichever is currently up.
  cid=$(sudo docker ps -q --filter "name=bench-" | head -1)
  if [ -n "$cid" ]; then
    name=$(sudo docker inspect -f '{{.Name}}' "$cid" 2>/dev/null | tr -d '/')
    if [ -n "$name" ]; then
      # mkdir inside the loop (idempotent) so the target always exists before the
      # write — self-heals if the run dir appears after startup or LOGDIR changes.
      mkdir -p "$LOGDIR"
      sudo docker logs "$cid" > "${LOGDIR}/${name}.log" 2>&1 || true
    fi
  fi
  sleep "$INTERVAL"
done
