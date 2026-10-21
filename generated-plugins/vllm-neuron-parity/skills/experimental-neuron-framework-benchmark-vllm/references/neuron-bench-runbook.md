# Neuron vLLM bench — end-to-end runbook (model-agnostic)

A self-contained, hand-off-ready procedure for benchmarking **any** model on
Trainium2 with the vLLM-Neuron sweep, following the "Performance Milestones" doc.
Written so an agent with **no prior conversation context** can execute it. It is
parameterized: fill in the `<INPUTS>` block, then run the phases in order.

The skill's deeper references remain the source of truth for *why*:
`neuron-input-contract.md` (intake), `neuron-setup.md` (infra/`neuron_config`),
`milestone-methodology.md` (comparison math). This doc is the *what-to-do* sequence
plus the hard-won operational gotchas that aren't obvious from the code.

---

## 0. Inputs to collect BEFORE doing anything (the intake contract)

Do not provision on an assumption for money/secrets/code items. Full contract in
`neuron-input-contract.md`. The minimum set:

| # | Input | Notes |
|---|-------|-------|
| A1 | Exact HF model id | e.g. `Org/Model-Name`. Always required (bench client `--model` + ledger key). |
| A2a | **CODE**: vLLM-Neuron **ECR image URI** + the branch/CR it was built from | The requester supplies a pre-built image; the bench does **not** port models. **No image / arch not registered → STOP, that's an autoport job, not a bench job.** |
| A2b | **DRIVER**: the Neuron driver RPM matching that image's SDK | Local `.rpm` path via `--driver`/`$NEURON_DRIVER_RPM` (passed on as `--neuron-driver-pkg-file`). Unset → the desktop starts without `-nd`; confirm the host driver already matches. |
| A3 | **WEIGHTS**: local `checkpoint_path` (preferred, no HF download) **or** HF `hf_id` | Local path must be an **absolute** path visible in-pod (e.g. under `$FSX_TEAM_SHARED_RW` — run `echo $FSX_TEAM_SHARED_RW` in-pod to get the literal you must write out). If HF + gated → **A4** `HF_TOKEN` via env (never echoed) + allowlisted account. |
| A8 | Native context window (`max_position_embeddings`) | Do the milestone shapes (8k/1k, 32k/2k) fit, or is `rope_scaling` needed? |
| A9 | Arch KV shape (layers / KV-heads / head-dim, or its `config.json`) | So the fit-check is right for a non-Llama arch (MHA default over-rejects GQA/MoE). |
| A6/A7 | FP8 checkpoint (if a quant regime) · drafter + recipe `k` (if spec decode) | Only if those regimes are requested. |
| — | **MoE?** expert count + active experts | If MoE: sweep needs `--enable-expert-parallel`, `neuron_config.ep_degree`, and `--hf-overrides {"architectures":["<ArchClass>"]}`. |
| A5 | Spend/time OK; node budget | Confirm the number of Kaizen nodes you may use in parallel and the max wall-clock. |

**Track T vs C:** T = Trn-only numbers (this runbook). C = GPU-vs-Trn milestone `%`
— C *additionally* needs a full matched GPU run (see `gpu-setup.md` +
`build_milestone_deliverable.py --gpu`). This runbook is the **Trn** side.

---

## 1. The sweep matrix (from the doc)

Sweep **every** cell the milestone doc specifies. Standard baselines × shapes ×
concurrency ladder:

- **Baselines (2):**
  - `controlled` — APC **off**, chunked/segmented prefill **off**, spec decode **off**, disaggregated inference **off**. This is the PRIMARY bar.
  - `production` — APC **on**, segmented prefill **on** (best chunk size), KV-cache FP8 **on** (production only), spec decode only if a ported drafter exists.
- **Shapes (2):** `8k ISL / 1k OSL` and `32k ISL / 2k OSL`. (A `512/1` point is a cheap smoke only, not a milestone shape.)
- **Concurrency ladder:** `[1,2,4,8,16,32,64,128,256,512,1024]` for 8k/1k; `[1,4,8,16,32,64,128]` for 32k/2k — **"or saturation"** (stop climbing once the box saturates / OOMs; that ceiling *is* a datapoint).
- **Workload (for a comparable number):** `random` + `random_range_ratio: 0.0` (exact shapes — Neuron pads to compiled buckets, so only exact shapes compare), `seed_mode: per_cell` when APC is on (see gotcha #3), warmups = 2, total requests ≥ 50 (`num_prompts_per_conc` × conc, floored at `min_prompts: 50`).
- **Sharding:** `shardings: [{tp, dp?, ep?}]`, each `tp*dp` ≤ box logical cores (64 on trn2.48xlarge) — `dp` is replica DP (`--data-parallel-size`), one full model copy per replica on its own window of `tp` cores. Unlike GPU, `tp*dp == box` is **not enforced**: prefer the box-filling ladder (`tp64_dp1 / tp32_dp2 / tp16_dp4 / tp8_dp8`) for anything reported **per-instance**, and treat an under-filling sharding (e.g. `tp8_dp1` = 8 of 64 cores) as a **partial-box** number — the driver WARNs but does not normalize. Sweep **EP on-vs-off** via `model.ep_list: [true, false]` if MoE (never make EP a regime); with EP on and `dp>1` leave `ep_degree` unpinned (the plugin requires `tp*dp` and the driver defaults it there).

---

## 2. Split the matrix into per-slice config files (parallelism strategy)

Each **slice** = one config JSON = one Kaizen node. Splitting lets you run up to N
nodes in parallel and isolates failures. The proven decomposition:

- Split by **(baseline × shape)** first: `controlled-8k`, `controlled-32k`, `production-8k`, `production-32k`.
- Within a shape, split the concurrency ladder into **lo / mid / hi** bands (a slice compiles one server per `(tp, ep)` group and reuses it across its concurrencies):
  - `lo` = `[1,2,4,8,16]`, `mid` = `[32,64,128]`, `hi` = `[256,512,1024]` (8k) / `[64,128]` (32k).
- **Split the `hi` band to ONE concurrency per node.** (Gotcha #4: a grouped hi slice dies *whole* at server-start when its largest conc OOMs or hits a kernel bug, so the smaller concs in the group never run. One-conc-per-node means a failing conc fails alone = a clean saturation/failure datapoint without taking out its neighbors.)

Use distinct **results-root sub-dirs** and **S3 sub-prefixes** per slice so nothing
overwrites: `.../q<run>-<slug>/` (e.g. `q35v3-c8k-lo`, `q35v3-c8k-256`).

**Per-slice config JSON — required fields** (see `assets/neuron/nb.*.json` for live examples):

```jsonc
{
  "image": "<A2a ECR URI>",
  "instance": "trn2.48xlarge",
  "models": [{
    "key": "<short-key>",              // dir prefix, e.g. "modelx"
    "hf_id": "<A1>",                    // ALWAYS required
    "checkpoint_path": "<$FSX_TEAM_SHARED_RW literal>/.../<weights-dir>",  // A3, ABSOLUTE (gotcha #1)
    "shardings": [{ "tp": 8, "dp": 8 }],   // tp*dp = 64 = fills the box; {"tp":8} alone = dp 1 = 8/64 cores
    "ep_list": [true],                  // MoE only; [true,false] to sweep EP
    "neuron_config": { "quantization": "bf16", ... }   // leave ep_degree UNPINNED: EP+dp>1 requires tp*dp (=64), which the driver defaults to
  }],
  "regimes": [{ "name": "bf16", "prec": "bf16", "neuron_config": { "quantization": "bf16" } }],
  "shapes": [{ "isl": 8192, "osl": 1024 }],
  "concurrencies": [256],              // this slice's band (one value for a hi split)
  "buckets": {
    "num_batched_tokens_buckets": [512],   // single prefill chunk ∈ {512,1024,2048,4096,8192}
    "num_seqs_buckets": [256],             // = max concurrency in this slice
    "decode_context_length_buckets": []    // must be strictly < max_model_len
  },
  "server": {
    "enable_prefix_caching": false,        // false=controlled, true=production
    "max_num_seqs": 256,
    "extra_flags": ["--no-async-scheduling", "--no-scheduler-reserve-full-isl",
                    "--hf-overrides", "{\"architectures\":[\"<ArchClass>\"]}"]
  },
  "workload": { "dataset": "random", "random_range_ratio": 0.0, "seed_mode": "per_cell",
                "num_prompts_per_conc": 5, "min_prompts": 50, "warmups_per_conc": 2,
                "temperature": 0, "ignore_eos": true },
  "bench_timeout_s": 32400,              // 9h — do NOT set fast timeouts (gotcha #5)
  "save_detailed": true,
  "skip_fit_check": true                 // on hi slices, let hardware demonstrate saturation
}
```

**Plugin bucket invariants (hard constraints — a violating config errors at server start):**
- No `--device neuron` flag.
- `--max-num-batched-tokens` is a **single value** ∈ {512,1024,2048,4096,8192}, `== num_batched_tokens_buckets[-1]`, **and ≥ `max_num_seqs`**. If `max_num_seqs > 512`, bump the chunk to the smallest supported size ≥ `max_num_seqs` (the driver's FIX#14 does this automatically; know it exists).
- `decode_context_length_buckets` strictly `< max_model_len`.
- Local checkpoint requires `--served-model-name <hf_id>` (the driver sets this from `hf_id`).

---

## 3. Preflight (cheap, do it before provisioning)

```bash
export PATH=$PATH:~/.toolbox/bin   # or set $KAIZEN_BIN
# refresh both creds (see gotcha #2)
ada credentials update --profile="$KAIZEN_PROFILE" --once
ada credentials update --account="$KAIZEN_CLUSTER_ACCOUNT" --provider=isengard \
  --role="$KAIZEN_CLUSTER_ROLE" --profile="$KAIZEN_CLUSTER_PROFILE" --once

# validate every config parses + expands to the expected cells (no server, no compile):
python3 assets/neuron/submit_neuron_bench.py --config <slice>.json --collect-only --profile "$KAIZEN_PROFILE"

# confirm the ECR image tag exists and is pullable in the account you'll run in:
aws ecr describe-images --repository-name <repo> --image-ids imageTag=<tag> --profile "$KAIZEN_PROFILE" --region "$AWS_REGION"

# confirm the weights dir exists in-pod-visible FSx (absolute path, file count sane):
#   ls <checkpoint_path>  → expect config.json + *.safetensors
```

Also run a **`--smoke`** slice first (≈3 cells, smallest tp+shape, synchronous) as a
go/no-go gate before launching the full fan-out.

---

## 4. Launch the fleet (batch workloads with an in-command supervisor)

Two provisioning modes; pick per node-budget:
- **`kaizen desktop`** — 1 per instance-type per alias (a hard VALIDATION_EXCEPTION limit). Use ONE desktop as a long-lived **staging box** (shares FSx; used for harvesting/inspection).
- **`kaizen start-workload`** — batch, **no per-alias limit** → this is how you get parallelism. Each slice = one `start-workload` with a self-contained `--command`.

**The `--command` MUST wrap the driver in a supervisor loop** (gotcha #6). Pattern
(paths hard-baked, driver + config + IPA staged from durable FSx assets):

```bash
# pod script (base64'd into --command):
export PYTHONUNBUFFERED=1
export NEURON_COMPILE_CACHE_URL=$FSX_TEAM_SHARED_RW/<alias>/neuron-compile-cache
A=$FSX_TEAM_SHARED_RW/<alias>/<assets>       # holds neuron_sweep.py, <slice>.json, ipa/, bd.py
RR=$FSX_TEAM_SHARED_RW/<alias>/<run>-<slug>  # this slice's results root (distinct per slice)
mkdir -p "$RR"; STAGE=/tmp/stage; mkdir -p "$STAGE"
cp "$A/neuron_sweep.py" "$STAGE/"; cp "$A/<slice>.json" "$STAGE/config.json"; cp -r "$A/ipa" "$STAGE/"
cd "$STAGE"; export PYTHONPATH="$STAGE/ipa:${PYTHONPATH:-}"
LOG="$RR/sweep.log"; SUPLOG="$RR/supervisor.log"; n=0
while [ $n -lt 12 ]; do n=$((n+1))
  echo "[sup] attempt $n $(date -u +%FT%TZ)" >> "$SUPLOG"
  python3 neuron_sweep.py --config config.json --results-root "$RR" --startup-timeout 7200 >> "$LOG" 2>&1
  rc=$?; echo "[sup] driver rc=$rc" >> "$SUPLOG"; [ $rc -eq 0 ] && break; sleep 15
done
```

```bash
# submit (repeat per slice; capture the T_ id):
WID=$(kaizen start-workload --profile="$KAIZEN_PROFILE" \
  --image "<ECR URI>" --instanceType trn2.48xlarge --nodeCount 1 \
  --neuron-driver-pkg-file "<driver.rpm>" --timeout 32400 --priority high \
  --command "bash -lc 'echo <BASE64> | base64 -d | bash'" 2>&1 | grep -aoE "T_[0-9a-f-]{36}" | head -1)
echo "<slug> $WID" >> ids.txt   # TRACK every id → slug mapping in a file
```

- **If weights are a local checkpoint, put NO `HF_TOKEN` in the `--command`** (avoids leaking a token into workload logs). Only pass it (via env, never inline-echoed) when actually HF-downloading a gated model.
- Stage the driver + configs + IPA + `bd.py` **once** to a durable FSx assets dir; every slice reads from there, so a driver patch propagates on the next supervisor restart.

---

## 5. Monitor (poll on an interval; never assume)

```bash
# refresh the cluster profile first (connect needs it), then count clean cells per slice.
# CLEAN = completed == num_prompts AND fail_frac ≤ threshold. Write a small clean.py that
# reads each cell's result.json/state.json and prints "<Nclean>c/<Ndirty>d" per slice.
ada credentials update --account="$KAIZEN_CLUSTER_ACCOUNT" ... --profile="$KAIZEN_CLUSTER_PROFILE" --once
kaizen desktop connect --name <staging> --cmd 'for d in <slugs>; do python3 clean.py $RR/<run>-$d; done' --profile "$KAIZEN_PROFILE"
```

**Verification rules (all learned the hard way):**
1. **`workloadStatus: SUCCEEDED` is meaningless** — the supervisor exits 0 after 12 failed attempts, so a slice can be SUCCEEDED with **0 clean cells**. ALWAYS verify clean-cell counts from `result.json`, never trust the workload status.
2. Get status with `kaizen get-workload --workload-id <T_...> --profile "$KAIZEN_PROFILE"` (field is `workloadStatus`; note it's `--workload-id`, not `-w`).
3. A `NotFound` pod (`resource-pod-...-1 not found`) means the workload finished/released its pod — check `get-workload`, not the pod.
4. Counts flat between checks is **normal** — the big cells (high conc, 32k) run 1–2h each; long timeouts let them finish rather than killing them. Only investigate if a slice is SUCCEEDED-with-0 or a pod is wedged.

**When a slice is SUCCEEDED-with-0 or stuck, read the REAL root cause** (gotcha #7):
the driver embeds the last **250** lines of the vLLM server log into `$RR/sweep.log`
(which is on FSx and survives pod death; `/tmp/serve_*.log` does not). Grep it:
```bash
grep -iE "out of memory|oom|nrt|gloo|barrier|nki|Error|RuntimeError|allocat|exceed" $RR/sweep.log | tail -30
```
Classify the failure:
- **OOM / KV won't fit** → genuine saturation ceiling = the "or saturation" datapoint. Document it, cancel that node, move on. Don't chase.
- **`gloo ... barrier` / `WorkerProc.wait_for_ready`** → TP-worker rendezvous flake (cluster comms). Transient — let the supervisor keep retrying; it may catch a stable window.
- **NKI kernel / Dynamo "failed to run FX node"** → a real plugin/compiler bug at that shape. Reproducible = the finding for that cell. May have a code-side workaround (bring it to the human; don't silently assume one works).
- **APC-on 500s** (production only) → if prefix-caching reproducibly returns `Internal Server Error` across retries, that IS the production-baseline finding; stop retrying.

---

## 6. Harvest each completed slice → S3 (do it as ladders finish, not just at the end)

The build script matches the GPU layout via `_PATH_RE`, so the same
`build_deliverables.py` consumes Neuron output. Build **in-pod** (the pod has the
deps), write into the S3-backed home so it auto-syncs:

```bash
# in-pod (via connect or a workload exec on the staging box):
python3 ~/bd.py $RR --instance trn2.48xlarge --model-name <ModelName> --no-plots --out ~/s3files/<run>/<slug>
#   NOTE: results dir is a POSITIONAL arg (not --results-root).
#   ~/s3files/... on a Kaizen desktop auto-syncs to
#   s3://kaizen-desktop-prod-<acct>-<region>/kaizen_home/s3files-home-<alias>/<run>/<slug>/
```
Each slice → its **own** S3 sub-prefix (`<run>/<slug>/`) so nothing overwrites.
Sanity-check `all_cells.csv`: `otps` should climb with conc until the knee, then
flatten/drop (a drop = saturation, still a valid clean datapoint).

**At the very end**, merge `all_cells.csv` across slices per `(baseline, shape)` into
`<run>/merged/`, and (track C only) run `build_milestone_deliverable.py --gpu <gpu-run>`
for the % bars.

---

## 7. Durable-asset & resume discipline

- Stage assets ONCE to FSx: `neuron_sweep.py`, all slice configs, `ipa/`, `bd.py`.
- **Compile cache** on FSx (`NEURON_COMPILE_CACHE_URL=$FSX_TEAM_SHARED_RW/<alias>/neuron-compile-cache`) is shared and persists across pod reschedules — but **verify it's actually being populated** (a pod reschedule wipes `/tmp`; if the cache path is empty despite successful compiles, the pod is compiling to a local dir and every restart recompiles — worth fixing).
- **Disk-resume:** a clean cell on disk (`completed == num_prompts`, `fail_frac` under threshold) is skipped on the next driver invocation, so a supervisor restart costs at most the in-flight cell. Dirty cells are re-tried.
- Keep an `ids.txt` (slug → `T_...`) and update it on every relaunch; supersede dead ids.

---

## Operational gotchas (the non-obvious failure modes — read these)

1. **`checkpoint_path` must be an ABSOLUTE path.** A literal `${FSX_TEAM_SHARED_RW}/...` inside a JSON string is **not** shell-expanded (the driver reads JSON, not a shell) → vLLM "Repo id must be in the form namespace/repo" → SUCCEEDED-with-0. Write out the literal value of `$FSX_TEAM_SHARED_RW` instead (`echo $FSX_TEAM_SHARED_RW` in-pod).
2. **Two creds, both refreshed on a schedule.** `$KAIZEN_PROFILE` (submit/exec) AND `$KAIZEN_CLUSTER_PROFILE` (`connect` needs EKS DescribeCluster in the CLUSTER account, `$KAIZEN_CLUSTER_ACCOUNT`). Stale cluster creds → `ExpiredTokenException` → `localhost:8080 refused`. Refresh both every ~30 min (a cron/loop). **Never print live cred/session tokens.**
3. **Prefix-cache seed footgun.** `vllm bench serve --seed` defaults to 0 → identical prompts across cells → warm-cache TTFT on APC-on servers (falsely fast). Use `seed_mode: per_cell` for any APC-on (production) slice.
4. **Grouped hi slices die whole.** If concurrencies `[256,512,1024]` share one server and the c1024 server-start OOMs/bugs, c256/c512 never run. Split hi bands to **one conc per node**.
5. **No fast timeouts.** Real 32k / high-conc cells run 90 min+. Set `bench_timeout_s: 32400` (9h) and `--startup-timeout 7200`. Wedge protection is the driver's **pre-cell `/health` guard** (catches DEAD servers), NOT a short timer. A premature timeout kills legitimate slow work.
6. **Batch workloads need a supervisor loop.** A bare `start-workload` runs the driver once; `rc=1` (a mid-run server death / pod reschedule) then marks the workload terminally FAILED. Wrap in the 12× retry loop (§4). Also: the driver must **break cleanly on a dead server, not `raise`** — a raised exception in a batch workload marks it terminally FAILED; a clean non-zero exit lets the supervisor retry.
7. **Capture 250 log lines, not 60.** vLLM v1 prints the real engine-core error (NRT/OOM/gloo/NKI) *far above* its "Engine core initialization failed. See root cause above" traceback. The driver tails 250 lines of the server log into the FSx `sweep.log` so the true cause survives pod death. If you shorten this, you lose the root cause and can't tell saturation from a bug.
8. **Kaizen `exec`/`connect` can SIGKILL (137) under load.** A killed `connect` does NOT mean the detached sweep died — poll again with a fresh `connect`. Never assume a dropped client killed the run.
9. **`kaizen get-workload` help text can leak into output** if you pass the wrong flag — it's `--workload-id`, output field `workloadStatus`.
