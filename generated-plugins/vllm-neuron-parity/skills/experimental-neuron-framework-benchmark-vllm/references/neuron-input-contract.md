# Trainium bench — Input Contract

Everything needed to take a model from "please benchmark this" → delivered Trn2 numbers,
grounded in exactly what produced the Llama-3.1-8B run. Split into: **(0)** the intake
checklist + which track, **(A)** what the requester MUST give me, **(B)** what I obtain/verify
myself, **(C)** what I derive (don't ask), **(D)** the concrete environment constants,
**(E)** per-input failure modes, **(F)** the invocation. Nothing here is optional hand-waving —
a missing (A) item blocks the run; a missing (B) item I resolve but it costs time.

---

## 0. Intake — ask these FIRST; the answers pick the track and the required inputs

**Q0. Which track?** This is the fork that decides how much you owe me:

- **TRACK T (Trn-only):** "benchmark model X on Trainium." Needs the **A** items below. No GPU box, no comparison. Produces per-instance throughput/latency/Pareto for Trn2.
- **TRACK C (GPU-vs-Trn comparison / milestone %):** "how does X on Trn compare to H100?" — the milestone `%-of-GPU-at-equal-interactivity` number. This needs **A (Trn) PLUS a full matched GPU run (§A-GPU)** — a second box, second image, second spend authorization. `build_milestone_deliverable.py` has `--gpu required=True`; there is no comparison without it.

**Intake checklist (every request):**
1. Track T or C? (if C, collect §A-GPU too)
2. **A1** model id (exact) ·
3. **A2 CODE**: ECR image URI (Case 2) — *and the branch/CR it was built from* — **plus the driver RPM that matches that image's SDK** (see A2b) ·
4. **A3 WEIGHTS**: local `checkpoint_path` (preferred) OR HF `hf_id` (+ **A4** token/access if HF) ·
5. **A8 native context window** (`max_position_embeddings`) — do the milestone shapes fit, or is `rope_scaling` needed? ·
6. **A9 arch KV shape** (layers / KV-heads / head-dim, from the model's `config.json`) — so the fit-check is right for a non-Llama arch ·
7. **A6** FP8 checkpoint(s) if a quant regime · **A7** drafter + recipe-`k` if a spec regime ·
8. **A5** spend/time OK (and, for track C, spend OK for the GPU box too).

If the answer to A2 is "no image yet / it's not ported" → **STOP**: that's an autoport job (a different skill), not a bench job. Don't provision.

---

## A. What the REQUESTER must provide (blocks the run; I cannot invent these)

| # | Input | Why it's required | Llama-3 example |
|---|---|---|---|
| A1 | **HF model id** (exact) — ALWAYS required, even for local weights (it's the served-model-name + the label deliverables/ledger key on) | identity of the model | `NousResearch/Meta-Llama-3.1-8B-Instruct` |
| A2a (**CODE**) | **A vLLM-Neuron ECR image in which this model's arch is REGISTERED, built from the port's branch/CR** — full URI incl. tag + the branch/CR ref for provenance. The model's *code* (arch Python) is baked in at build time, NOT passed at runtime; the bench does NOT port models. **Case 2: requester/port-owner hands me the pre-built image URI.** | unregistered arch → server-start failure (whole group FAILED) — the single biggest gate | image `...-vllm-neuron`, from branch `<vllm-neuron pkg>/<branch>` (an upstream-supported arch can use the stock perf-test image) |
| A2b (**DRIVER**) | **The Neuron driver RPM that matches the image's SDK** — or explicit confirmation the host driver already works with this image. The `-nd` driver installed on the Desktop HOST must be compatible with the Neuron runtime/compiler baked in the image; a newer/older-SDK image may need a different RPM. | host driver ↔ in-image runtime mismatch → device init failure at server start | `aws-neuronx-dkms-2.x.<ver>.noarch.rpm` via `--driver`/`$NEURON_DRIVER_RPM`; unset → no `-nd` |
| A3 (**WEIGHTS**) | **Where weights load from.** Default = HF hub via `hf_id` (needs A4 access; ~tens of GB/pod). **Preferred here = a pre-staged local/FSx checkpoint dir** → set `checkpoint_path` (in-pod path, e.g. under `$FSX_TEAM_SHARED_RW`, already mounted) — no HF download, no gated-access friction. Per-regime: `checkpoint_path_by_regime`. Optional `hf_fallback:true` retries HF if the local dir fails (loudly logged). | wrong/missing weights → 403 (gated HF) or 404/`completed=0` (local path w/o served-model-name — now handled) | checkpoint on FSx: `<value of $FSX_TEAM_SHARED_RW>/<alias>/ckpts/<model>-neuron` (run `echo $FSX_TEAM_SHARED_RW` in-pod to get the literal — it must be written out, see the runbook's gotcha #1); OR public HF id |
| A4 | **HF access + token** — only if weights come from HF (A3=HF): model ungated OR a token whose account is **allowlisted**, delivered via env `HF_TOKEN` (NEVER pasted/committed). Not needed for a local checkpoint. | gated `meta-llama/*` 403 even with a token if the account isn't approved (Llama-3: `meta-llama/...`→403, `NousResearch/...`→OK) | `HF_TOKEN=hf_... python3 submit_neuron_bench.py …` |
| A5 | **Spend/time authorization** — OK to hold a trn2.48xlarge Desktop for up to N hours (default cap 8h `--desktop-timeout`) | Desktop bills while alive | "up to 4h is fine" |
| A6 | **If any regime is FP8/quantized:** the exact pre-quantized checkpoint id (per regime) | Neuron precision needs a real quantized checkpoint or `neuron_config.quantization`; can't be guessed | `hf_id_by_regime: {"fp8": "<...-FP8>"}` |
| A7 | **If spec-decode:** the drafter model id + `num_speculative_tokens` — the **deployment-recommended** value from the paper/recipe, NOT the HF card's *training* block size. This is a lookup burden, not a config value (the DSpark k=7-vs-γ=5 lesson: HF card said 7, paper deployed 5). | wrong k silently under/over-drafts → bogus "spec speedup" | `{"method":"eagle","model":"<draft>","num_speculative_tokens":3}` |
| A8 | **Native context window** (`max_position_embeddings` from the model's `config.json`) | decides whether a milestone shape fits or needs `rope_scaling`: if `max_shape(isl+osl) > native_window`, set `model.rope_scaling` (else the long shape is REJECTED on Neuron). Nothing auto-detects this. | 32k shape rejected on an 8k/32k-native model | Llama-3.1 native = 131072 → 32k/2k fits, no rope needed |
| A9 | **Arch KV shape** for the fit-check — layers / KV-heads / head-dim (from `config.json`), OR just hand me `config.json` and I compute `kv_bytes_per_token` | the fit-check default assumes MHA and **over-estimates GQA ~2×**, so it can FALSELY REJECT a config that would fit | Llama-3.1-8B: 32 layers × 8 KV-heads × 128 dim × 2(K+V) × 2 B = `131072` |

**Ask-if-nonstandard (safe to default, but confirm for a real deliverable):**
- **shardings** — `{tp, dp?, ep?}` pairs to sweep (each `tp*dp` ≤ 64 on trn2.48xlarge; `dp`
  defaults to 1). Default the box-filling ladder `[{tp:64,dp:1},{tp:32,dp:2},{tp:16,dp:4},{tp:8,dp:8}]`,
  pruning rungs the model/shape can't fit at that `tp`. A sharding whose `tp*dp` < 64 is legal
  but yields a **partial-box** number — only pick one deliberately. (Legacy `tp_list`
  `[32,16,8]` still works and means `dp=1`, i.e. all-partial-box.)
- **shapes** — default the two milestone standard shapes `8192/1024` + `32768/2048` (+ `512/1` smoke).
- **concurrencies** — default `[1,4,8,16,24,32]`; cap the 32k shape lower (`concs_by_shape`).
- **baseline** — `controlled` (APC+chunked+spec OFF) is the primary milestone bar; `production` for GTM.
- **MoE?** — if MoE, set `moe:true` and (to compare EP) `ep_list:[true,false]`.

### §A-GPU — additional inputs for TRACK C only (the matched GPU run)

The milestone `%-of-GPU` number **requires a matched GPU sweep** (`build_milestone_deliverable.py`
`--gpu` is required). For track C, on top of all the A items above, I also need the GPU half —
same model, **same shapes / regimes / baseline** (feature-matched, or the % is meaningless):

| # | GPU input | Note |
|---|---|---|
| G1 | **GPU instance** (e.g. `p5.48xlarge` = 8× H100) — and the GPU count | drives `gpu_count`; every sharding `tp*dp == gpu_count` |
| G2 | **Stock upstream-vLLM GPU image** | GPU uses stock vLLM (any registered arch), not the Neuron image |
| G3 | **AWS account/profile + creds to rent the GPU box** (`aws ec2`), and **key pair + security-group** | the GPU path rents a raw box (§A of SKILL), unlike Trn's Kaizen Desktop |
| G4 | **Spend authorization for the GPU box** — separate from A5; p5 on-demand ≈ $55/hr, capacity blocks are prepaid/non-refundable | confirm a ceiling before renting |
| G5 | **Matched shapes/regimes/baseline** = identical to the Trn config; workload = `random` + `random_range_ratio 0.0` on BOTH sides (Neuron pads to buckets → only exact shapes compare) | set the SAME `baseline` (controlled) both sides |

If the requester only wants Trn numbers (track T), skip §A-GPU entirely.

---

## B. What I obtain / verify myself (I don't need you to hand these over, but they gate the run)

| # | Item | How I get it | Concrete value used for Llama-3 |
|---|---|---|---|
| B1 | **Kaizen CLI + creds** | the `kaizen` CLI on `$PATH` (or `$KAIZEN_BIN`), service profile `$KAIZEN_PROFILE` (ada-managed, auto-pulls), region `$AWS_REGION`, in your team's Kaizen service account | — |
| B2 | **Cluster-account creds** (SEPARATE from the service profile — `connect` needs them for EKS DescribeCluster) | `ada credentials update --account="$KAIZEN_CLUSTER_ACCOUNT" --provider=isengard --role="$KAIZEN_CLUSTER_ROLE" --profile="$KAIZEN_CLUSTER_PROFILE" --once` | the cluster account |
| B3 | **InferencePerfAnalyzer (IPA) checkout** — the bench image LACKS `inference_perf_analyzer`; I vendor it into the pod via `--ipa-src <checkout>/src` | `git clone ssh://git.amazon.com:2222/pkg/InferencePerfAnalyzer` (was at commit `61215eb`) | `/tmp/InferencePerfAnalyzer/src` |
| B4 | **Neuron driver RPM** — installed on the Desktop host via `-nd` at start. **Must match the image's SDK (A2b)**; cached in Kaizen's S3 by SHA | `--driver <path>` or `$NEURON_DRIVER_RPM`. **Unset → the desktop starts without `-nd`** (fine when the host driver already matches) | a local `aws-neuronx-dkms-2.x.<ver>.noarch.rpm` matching the image's SDK |
| B5 | **Image discovery** (if A2 not given as a full URI) | `kaizen image-builder list_dlc_images --framework vllm --tag-filter contains=vllm --profile "$KAIZEN_PROFILE"` | a `...-vllm-neuron` tag |
| B6 | **Deliverables deps** — for building locally: `pandas matplotlib tabulate numpy` (or build IN-POD, it has them) | `pip install -r assets/gpu/requirements-deliverables.txt` | — |

---

## C. What I DERIVE automatically (do NOT ask the requester)

The driver computes these from A/B; the requester never sets them:
- **buckets** — `num_batched_tokens_buckets` (single prefill-chunk ∈ {512,1024,2048,4096,8192}),
  `num_seqs_buckets` (= concurrencies), `decode_context_length_buckets` (from isl+osl, `< max_model_len`).
- **`--max-num-batched-tokens`** (= the single prefill-chunk), **`--max-model-len`** (max isl+osl+buffer),
  **`--max-num-seqs`** (≥ max concurrency), **`ep_degree`** (= `tp*dp` when EP on — what the
  plugin requires once `dp>1`; leave it unpinned).
- **The 5 plugin invariants** (no `--device neuron`, chunk-size rules, decode-ctx `<` max_model_len,
  chunked-prefill off) — all encoded, never a requester concern.
- **fit-check** — hard-stops before provisioning if a bucket set won't fit at some TP (KV budget scales with TP).
- **`kv_bytes_per_token`** — has an MHA-heuristic default. For Llama I set the exact GQA value. **For a
  non-Llama arch this is NOT safe to leave default** (over-estimates GQA ~2× → can falsely reject a
  fitting config) — so A9 (arch KV shape / `config.json`) becomes a real intake item, and I compute it.
- **`rope_scaling`** — I emit `--rope-scaling` when set, but *whether it's needed* depends on A8 (native
  window) vs the longest shape — so A8 is a real intake item for any model whose native window ≤ the
  milestone shapes. Llama-3.1 (131072 native) needed none.

---

## D. Environment (set these once per shell; NOTHING site-specific is baked into the assets)

Every account id, profile, role and region is read from the environment, so the same
assets run against any team's Kaizen deployment. Export these before step 0:

```
KAIZEN_BIN              path to the kaizen CLI      (default: $PATH, else ~/.toolbox/bin/kaizen)
KAIZEN_PROFILE          service profile             (default: kaizen-access)
KAIZEN_CLUSTER_PROFILE  cluster-account profile     (default: cluster-role)
KAIZEN_CLUSTER_ACCOUNT  cluster account id          (no default — only used in the ada hint)
KAIZEN_CLUSTER_ROLE     cluster role name           (no default — only used in the ada hint)
AWS_REGION              the Kaizen service region   (desktops may SCHEDULE elsewhere —
                                                     that's Kaizen's placement, not yours)
NEURON_DRIVER_RPM       image-SDK-matched driver    (unset → desktop starts without -nd)
NEURON_CACHE_ALIAS      FSx NEFF-cache subdir       (default: the pod's $USER, else `shared`)
IPA_SRC                 IPA checkout's src/         (vendored into the pod)
```

Fixed facts that are NOT deployment-specific:

```
ECR (bench images)    <cluster-acct>.dkr.ecr.<region>.amazonaws.com/prod/neuron-kaizen-workload
IPA repo              ssh://git.amazon.com:2222/pkg/InferencePerfAnalyzer  (import: inference_perf_analyzer)
instance              trn2.48xlarge = 16 chips × 128 physical cores, LNC=2 → 64 LOGICAL cores, ~96 GB HBM/chip
results (durable)     $FSX_TEAM_SHARED_RW/<alias>/neuron-results   (state.json resume ledger lives here)
live log (in-pod)     /tmp/sweep.log  (LOCAL disk — never FSx/pipe; the anti-wedge fix)
```

---

## E. If an input is missing — the failure it causes (so we catch it at intake, not 30 min into compile)

| Missing input | Symptom | Where caught |
|---|---|---|
| A2 image / unregistered arch | `vllm serve` exits rc=1; group FAILED | preflight (image-URI shape) + first server start |
| A3 HF access (gated, not allowlisted) | `403 ... not in the authorized list` at weight download | first server start (~1 min in) |
| A4 token not in env | HF 401 / rate-limit on download | first server start |
| B2 cluster creds stale | `ExpiredTokenException` on DescribeCluster → `localhost:8080 refused` on every `connect` | first `connect` |
| B3 IPA not vendored (no `--ipa-src`) | `ModuleNotFoundError: inference_perf_analyzer` | driver launch (instant) |
| `tp*dp` > 64 | parallelism check: "needs N logical cores but trn2.48xlarge has 64" | preflight / driver (before provisioning) |
| EP on + `dp>1` + `ep_degree` pinned ≠ `tp*dp` | parallelism check hard-stop (the plugin's `NeuronWorker` would `ValueError` at init) | preflight / driver (before provisioning) |
| `tp*dp` < 64 (under-fills the box) | WARN naming the idle core count — the cell is a **partial-box** number | preflight / driver |
| bucket set too big for a TP | fit-check hard-stop (exit 2) | before provisioning |
| shape non-standard for a *milestone* number | preflight WARN (fine for one-off, not a milestone) | preflight |
| A2b driver ↔ image SDK mismatch | device/runtime init failure at server start | first server start |
| A8 long shape > native window, no `rope_scaling` | shape rejected / max-model-len error at server start | first server start (or preflight WARN) |
| A9 wrong KV shape (GQA served by MHA default) | fit-check FALSELY rejects a fitting config (exit 2) | before provisioning — annoying: blocks a valid run |
| TRACK C but no GPU run | `build_milestone_deliverable.py` errors (`--gpu` required) — no % number | at deliverable build (after both sweeps) — so collect §A-GPU at intake |

---

## F. The minimal invocation that worked (copy-paste shape)

```bash
# 0. creds (both!) — see §D for the env vars
ada credentials update --profile="$KAIZEN_PROFILE" --once
ada credentials update --account="$KAIZEN_CLUSTER_ACCOUNT" --provider=isengard \
  --role="$KAIZEN_CLUSTER_ROLE" --profile="$KAIZEN_CLUSTER_PROFILE" --once

# 1. preflight (provisions nothing)
python3 assets/neuron/preflight.py neuron_bench_config.json

# 2. smoke gate (Desktop start + ~3 cells, detached, keeps desktop warm)
HF_TOKEN=<tok> IPA_SRC=/tmp/InferencePerfAnalyzer/src \
  python3 assets/neuron/submit_neuron_bench.py \
  --config neuron_bench_config.json --name <model>-run --smoke

# 3. full sweep on the warm desktop
HF_TOKEN=<tok> IPA_SRC=/tmp/InferencePerfAnalyzer/src \
  python3 assets/neuron/submit_neuron_bench.py \
  --config neuron_bench_config.json --name <model>-run --reuse

# 4. pull + deliver (or build in-pod, then pull small files)
kaizen get-artifact --location FSX_TEAM_SHARED_RW --source <alias>/neuron-results \
  --destination ./results --directory --profile "$KAIZEN_PROFILE"
python3 assets/gpu/build_deliverables.py ./results --multi --instance trn2 --model-name "<Model>" --out ./deliverables
python3 assets/gpu/build_milestone_deliverable.py --trn ./deliverables/all_cells.csv --trn-instance trn2 \
  --gpu <gpu_all_cells.csv> --gpu-instance p5 --baseline controlled --out ./deliverables/milestone   # if comparing
```

The `neuron_bench_config.json` for step 1-3 is a copy of `assets/neuron/neuron_bench_config.example.json`
with A1/A2 filled in (+ A6/A7/shardings/shapes as needed); every field in that template carries a
`_field` doc string explaining what to put there.
