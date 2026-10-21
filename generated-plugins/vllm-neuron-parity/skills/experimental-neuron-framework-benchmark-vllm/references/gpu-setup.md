# GPU node setup detail

## RAID0 the local NVMe SSDs (optional, faster weight loading)

GPU instances (g6e etc.) ship several local NVMe SSDs. Striping them into one
RAID0 volume gives a fast `/scratch` for HF weight downloads. `assets/gpu/setup.sh`
does this automatically and idempotently; the manual equivalent:

```bash
lsblk                                  # identify data SSDs (exclude the root EBS vol)
sudo dnf install -y mdadm
sudo mdadm --create /dev/md0 --level=0 --force --run \
  --raid-devices=<N> /dev/nvme1n1 ... /dev/nvmeNn1
cat /proc/mdstat
sudo mkfs.ext4 -F /dev/md0
sudo mkdir -p /scratch && sudo mount /dev/md0 /scratch
sudo chown $USER:$USER /scratch
df -h /scratch
```

`setup.sh` auto-detects the data SSDs (skips the root volume / any disk with
partitions or a mountpoint), and falls back to a plain `/scratch` dir if <2 data
SSDs are present.

## Image

```bash
sudo docker pull vllm/vllm-openai:v0.20.0-cu130-ubuntu2404
```
Use a newer tag if a model's architecture isn't registered in 0.20.0. The
benchmark client (`vllm bench serve`) runs inside this same image so versions match.

## Orchestrator: InferencePerfAnalyzer (two transports)

The sweep is driven by **InferencePerfAnalyzer** (IPA), which drives `vllm bench serve`
per concurrency and gives the concurrency sweep, the TP×DP axis, per-cell `result.json`
in a fixed layout, a server cache (reuse across concurrencies), a failure-rate guard,
and a **resume ledger** — the things a plain `docker run` loop lacks. `sweep_bench.py`
uses IPA's transport-agnostic pool + `--transport`:

- **`--transport=docker` (PREFERRED):** IPA runs on the *host* and starts/stops the
  vLLM container per sharding via `docker run`. Rent the box yourself with a plain
  `aws ec2 run-instances` (SKILL.md §A1), keep it under your control, and iterate over
  SSH. IPA is **not on PyPI**, so the agent clones it locally, `scp`s the source onto
  the box, and `setup.sh` installs it `-e` into a venv. **Always pass
  `--transport=docker` explicitly** — IPA's own flag default is `kaizen`.
- **`--transport=kaizen` (LAST RESORT):** the whole sweep runs inside a **Kaizen pod**
  — the pod is the serving container; Kaizen provisions the GPU from a shared pool and
  tears it down. Use ONLY when you can't get an instance directly (A1's on-demand /
  capacity-block retries are exhausted). Needs a **baked image** (IPA + pytest
  pre-installed; pass as `KAIZEN_IMAGE`) — see the IPA image-baking guide — which is the
  main reason it's heavier. Kaizen has its own capacity limits (a workload can sit in
  `PENDING`); no local box, but no local control either.

For the docker path, `setup.sh` does:

```bash
# on the box (setup.sh does this — docker transport only):
python3 -m venv /scratch/bench-venv
/scratch/bench-venv/bin/pip install pytest
/scratch/bench-venv/bin/pip install -e ~/InferencePerfAnalyzer   # scp'd source ($HOME, not /scratch)
```
Override the paths with `IPA_SRC` / `BENCH_VENV` env vars. Only `pytest` is a network
install; everything else is the scp'd source + the already-pulled image.

**Resume:** both transports write a `state.json` ledger at `ORCHESTRATOR_RESULTS_ROOT`;
re-running the same sweep skips already-PASSED cells before starting a server. Point the
results root at a **durable** path (FSx under Kaizen, `/scratch` locally) so a
timed-out/paused sweep resumes without redoing work. Changing the config (image, flags,
shapes, …) archives the old ledger and starts fresh, so two configs never mix.

## Dataset

The **default workload is `dataset: sharegpt`** (native-production — see SKILL.md
"Config philosophy"): natural-language conversational prompts, the closest thing the
harness has to production traffic, so spec-decode acceptance and APC (prefix-cache)
hit rates are realistic. `setup.sh` stages the file:
```
https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json
```
Under ShareGPT, request lengths come from the dataset, not the `shapes` list —
`shapes` only tags output dirs and sizes the concurrency grid; the server window is
sized from `workload.seq_len`.

For a **strict matched-config GPU-vs-Neuron comparison, run a second pass with
`dataset: random` and `random_range_ratio 0`** (EXACT shapes). ShareGPT's variable
lengths pad against a bucketed Neuron server but not on GPU (the Neuron side pads to
the bucket, the GPU side doesn't), which skews the comparison; exact shapes also avoid
overflowing `max-model-len` at long context. Under `random` the `(isl, osl)` shapes
ARE the request lengths (or, with `random_range_ratio 0.8`, a realistic spread around
them). **Do not benchmark spec decode on `random`** — predictive drafters barely
accept on random tokens (~1 token/cycle vs τ≈3.5 on chat), silently understating the
regime; see SKILL.md "Config philosophy".

## SSH / security group

- The instance is launched with a key pair (import your existing `.pem`'s public
  half into the region; key pairs are region-scoped).
- User is `ec2-user` (Amazon Linux 2023 DL AMI).
- The bench host's egress IP must be in the SG's port-22 ingress. Add it with
  `authorize-security-group-ingress` (see SKILL.md §A1). The SG is shared — never
  delete it or its rules; terminating the instance detaches it automatically.
- Old SSH clients reject `accept-new`; use
  `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null`.

## What the orchestrator does per cell (so you know what to expect)

The sweep (`sweep_bench.py`) builds one cell per
`model × regime × shape × sharding(tp,dp) × concurrency` and hands each to IPA's
transport-agnostic pool:

- **Server**: `server_handle_pool.get(server_argv)` starts a `vllm serve` at the
  sharding (`--tensor-parallel-size tp` + `--data-parallel-size dp` when dp>1), bound
  to the whole box — as a Kaizen-pod subprocess or a `docker run` container per
  `--transport`. It's one-server-at-a-time: a changed argv (new regime/sharding) stops
  the old server; the same argv (a new concurrency) **reuses the running server**, so
  you pay model-load once per (regime, sharding), not per cell.
- **Bench**: `run_bench()` runs `vllm bench serve` at `--max-concurrency <conc>` and
  writes `result.json` (+ `metrics_before/after.txt`, `run_manifest.json`).
- **Resume**: before starting a server, `sweep_state.is_done(cid)` skips a cell that
  already PASSED; the cell is recorded PASSED/FAILED after.
- **Readiness / teardown / networking** are handled inside IPA per transport — you
  don't hand-roll `docker run` flags or the health poll.

## Results layout (IPA contract — what to sync back)

Each `(model, regime)` gets a run dir with a `regime_meta.json` marker; each cell
writes to:
```
$ORCHESTRATOR_RESULTS_ROOT/<key>__<regime>/                 <- + regime_meta.json here
    <key>_<isl>_<osl>_<tag>/conc_<C>/result.json
        tag = tp<N>            (dp=1)
            = tp<N>_dp<M>      (dp>1)
```
`build_deliverables.py --multi` reads `regime_meta.json` to attribute each run's
regime. Sync the whole `results/` tree back (not a flat glob, and include the
`regime_meta.json` files) before terminating.

## Capacity

`InsufficientInstanceCapacity` is common for g6e. Retry order: other AZs in the
region → smaller g6e size (still fits if ≥ model size) → another region. A 4× L40S
node (g6e.12xlarge, 184 GB) holds all dense bringup models and Mixtral 8x7B at TP4.
TP8 needs an 8-GPU node (g6e.48xlarge).
