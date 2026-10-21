#!/usr/bin/env bash
# Continuously push the results tree to S3 so a long sweep's data is durable even
# if the orchestrator (the agent/laptop driving the run over SSH) dies, or the box
# is torn down (e.g. a capacity block auto-terminating at its window end). Runs ON
# THE BOX, detached — like capture_logs.sh — so it does NOT depend on the local
# orchestrator staying alive.
#
# WHY THIS EXISTS: in the run #3 full sweep, the incremental syncs were driven by
# the Mac-side agent. When the agent was killed, syncing stopped, so cells computed
# after the last sync were stranded on /scratch and lost when the block terminated
# the box. Moving the sync loop onto the box (detached) means the ONLY hard stop is
# the box itself going away, and everything up to the last interval is already in S3.
#
# Run it detached on the box, alongside the sweep + capture_logs.sh:
#   BUCKET=... RUN_PREFIX=... AWS_PROFILE=... REGION=... \
#     nohup bash sync_results.sh 300 >/tmp/sync.out 2>&1 &
# (interval defaults to 300s = 5 min; `aws s3 sync` is incremental+idempotent, so a
#  frequent loop is cheap — it only uploads changed/new files.)
#
# CREDENTIALS ON THE BOX (read this): `aws s3 sync` needs credentials available to
# the box itself, NOT the orchestrator's. Best: attach an INSTANCE PROFILE / IAM role
# to the box at launch (--iam-instance-profile) with s3:PutObject on the bucket —
# then no creds to manage or expire. Failing that, the orchestrator can copy short-
# lived creds onto the box (they expire — a long run may outlive them; the loop logs
# the failure and keeps retrying so it recovers if creds are refreshed). Verify once
# up front: `aws sts get-caller-identity` on the box before trusting the loop.

set -uo pipefail

INTERVAL="${1:-300}"
RESULTS_ROOT="${ORCHESTRATOR_RESULTS_ROOT:-/scratch/results}"
: "${BUCKET:?set BUCKET env var - your S3 bench bucket, e.g. <your-bench-bucket>}"
: "${RUN_PREFIX:?set RUN_PREFIX env var - this run prefix, e.g. <model>_<regime>_<date>}"
DEST="s3://${BUCKET}/${RUN_PREFIX}/results/"
PROFILE_ARG=""; [ -n "${AWS_PROFILE:-}" ] && PROFILE_ARG="--profile ${AWS_PROFILE}"
REGION_ARG="";  [ -n "${REGION:-}" ]      && REGION_ARG="--region ${REGION}"

echo "Syncing ${RESULTS_ROOT} -> ${DEST} every ${INTERVAL}s. Ctrl-C to stop."
# Fail loudly up front if creds don't work — better than silently never syncing.
aws sts get-caller-identity $PROFILE_ARG $REGION_ARG >/dev/null 2>&1 \
  || echo "  WARN: aws sts get-caller-identity failed — no valid creds on the box yet. " \
          "The loop will keep retrying; attach an IAM instance profile or refresh creds."

while true; do
  if [ -d "$RESULTS_ROOT" ]; then
    # --no-progress keeps the detached log readable; sync is incremental (only new/
    # changed files upload). Non-fatal on failure (e.g. transient creds) — retry next tick.
    if aws s3 sync "$RESULTS_ROOT" "$DEST" --no-progress $PROFILE_ARG $REGION_ARG 2>&1; then
      echo "  [$(date -u +%H:%M:%SZ)] synced $(find "$RESULTS_ROOT" -name result.json 2>/dev/null | wc -l | tr -d ' ') result.json cell(s)"
    else
      echo "  [$(date -u +%H:%M:%SZ)] sync FAILED (creds expired / network?) — will retry in ${INTERVAL}s"
    fi
  fi
  sleep "$INTERVAL"
done
