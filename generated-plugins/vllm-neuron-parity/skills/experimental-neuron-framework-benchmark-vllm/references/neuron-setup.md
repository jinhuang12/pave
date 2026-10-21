# Neuron / Trainium bench setup detail

The Trainium peer of `gpu-setup.md`. The Neuron sweep runs **inside a Kaizen
Desktop** (a long-lived pod that *is* the serving container), driven by
`submit_neuron_bench.py` → `neuron_sweep.py`. This doc covers the infra the driver
and launcher assume: the Desktop lifecycle, the `neuron_config` fields, the compile
cache, and the `trn2.48xlarge` core math.

## Kaizen access

- CLI: the `kaizen` binary on `$PATH` (or `$KAIZEN_BIN`; commonly `~/.toolbox/bin/kaizen`).
- Profile: **`$KAIZEN_PROFILE`** (default `kaizen-access`), region **`$AWS_REGION`**, in
  your team's Kaizen service account.
- Every command takes `--profile "$KAIZEN_PROFILE"`. `submit_neuron_bench.py` defaults to
  it (`--profile`); `desktop connect` additionally needs `$KAIZEN_CLUSTER_PROFILE`.

## Kaizen Desktop lifecycle

A **Desktop** is a long-running interactive workload with a **persistent home
directory**, tracked locally by `--name`. It keeps the warm NEFF compile cache and
lets `--smoke` and the full sweep share **one** provisioned box (the reason the sweep
uses a Desktop, not a one-shot `start-workload`).

```bash
KZ="${KAIZEN_BIN:-$(command -v kaizen || echo ~/.toolbox/bin/kaizen)}"
P="--profile ${KAIZEN_PROFILE:-kaizen-access}"

# start (1-32 char name: lowercase, digits, hyphen). Default entrypoint is
# 'sleep infinity' so the pod stays up for `connect` — do NOT override --command.
$KZ desktop start --name <name> --image <ecr-uri> --instanceType trn2.48xlarge \
    --nodeCount 1 --timeout 28800 --priority high \
    --neuron-driver-pkg-file /tmp/driver/aws-neuronx-dkms-2.x.<ver>.noarch.rpm $P

$KZ desktop list $P                         # all locally tracked desktops + status
$KZ desktop info --name <name> $P           # describe + how to sync files in
$KZ desktop connect --name <name> $P        # interactive shell ($HOME cwd)
$KZ desktop connect --name <name> --cmd '<bash>' $P   # one-shot; exits with its status
$KZ desktop stop --name <name> $P           # tear down (frees the box)
```

Flags that matter (from `kaizen desktop start --help`):

- `--timeout <s>` — max Desktop lifetime before teardown (CLI default **10800 s / 3 h**;
  `submit_neuron_bench.py` raises this to **28800 s / 8 h** via `--desktop-timeout`, and
  it **must exceed the sweep** or the box is torn down mid-run).
- `-nd, --neuron-driver-pkg-file <path>` — a **local** Neuron driver artifact (`.rpm`/`.deb`,
  ≤ 3 MB) installed on the host **before** the workload. The filename must follow
  `aws-neuronx-dkms-<version>.noarch.rpm` so the version can be derived.
  `submit_neuron_bench.py` passes it via `--driver` (default `$NEURON_DRIVER_RPM`, unset by
  design) and **skips `-nd` with a warning** if no RPM is given or it isn't present locally.
- `--priority high|medium|low`, `--nodeCount 1`, `--image <ecr-uri>`,
  `--instanceType trn2.48xlarge`.

`start` **fails if a Desktop with that name is already active** — stop it first or pick
another name. `submit_neuron_bench.py` reuses an existing one (`--reuse`, or auto if the
name is already tracked).

Capacity: a Desktop can sit **PENDING** if the shared Trainium pool is full — retry, or
try the other instance type.

## How the launcher stages + runs (submit_neuron_bench.py)

`connect --cmd` runs `bash -c` in the pod with `$HOME` as cwd. The launcher base64-encodes
each payload so multi-line scripts + `HF_TOKEN` ride as **one arg** and are **never
logged**:

- **Stage:** `neuron_sweep.py` + `config.json` are base64'd and written into
  `$HOME/neuron-bench-staging` (base64 avoids assuming any FSx path-mapping —
  `put-artifact` targets shared FSx storage, not the Desktop home).
- **Pod env** exported before the driver: `VLLM_PLUGINS=neuron`,
  `NEURON_SKIP_EFA_AFFINITY=1`, `HF_HUB_ENABLE_HF_TRANSFER=0`, `VLLM_LOGGING_LEVEL=ERROR`,
  `NEURON_RT_LOG_LEVEL=ERROR`, etc.
- **Run:** `--collect-only`/`--smoke` run **synchronously** (connect blocks on the exit
  status); `--reuse` (full) runs **detached** (`nohup … & disown`, tee'd to
  `$RR/sweep.log` on FSx) and returns immediately with `SWEEP_PID`/`SWEEP_LOG`.

## FSx durable paths (why the ledger lives there)

The Desktop's home is persistent but the box is not — a teardown loses on-box-only state.
So point `--results-root` at a **durable FSx path** (default
`$FSX_TEAM_SHARED_RW/<alias>/neuron-results`; convention `$FSX_TEAM_SHARED_RW/<alias>/...`).
The driver's `state.json` **resume ledger** and every cell's `result.json` land there, so:

- a re-run skips PASSED cells (cross-workload resume survives a torn-down Desktop),
- the parent session pulls results independently of the launcher:

```bash
$KZ get-artifact --location FSX_TEAM_SHARED_RW \
    --source <alias>/neuron-results --destination ./results --directory $P
```

Gate on completeness before trusting a cell: valid only when **`completed == num_prompts`**
(`num_prompts = max(conc*num_prompts_per_conc, min_prompts)`); the ledger records
`completed` per cell.

## `neuron_config` field reference

Neuron expresses precision/quant/spec/EP/CP inside a `neuron_config` dict merged into
vLLM-Neuron's `--additional-config` (**not** CUDA flags). The driver
(`build_neuron_config`) assembles it from the derived buckets → `regime.neuron_config` →
`model.neuron_config_extra` → the spec-decode mapping. Fields:

- `num_batched_tokens_buckets` — prefill NEFF buckets = the unique ISLs, padded up to a
  512 stride.
- `num_seqs_buckets` — decode-batch NEFF buckets = the unique **concurrencies**. This is
  the **concurrent-seq count, NOT sequence length** (confusing the two allocated KV for
  1024 seqs and OOM'd OLMo-2-13B).
- `decode_context_length_buckets` — per-context decode buckets, from `max(isl+osl)+buffer`.
- `quantization` — `"bf16"`, `"mxfp4"`, … (precision; pair with a pre-quantized checkpoint
  via `hf_id_by_regime` for real quant).
- `speculation_length` + `enable_fused_speculation` — set by the driver from
  `regime.speculative` (`{method, model, num_speculative_tokens}`); vLLM-Neuron does **not**
  take GPU's `--speculative-config` verbatim.
- `ep_degree` (+ `moe_ep_degree`/`moe_tp_degree` if split) — MoE expert-parallel degree.
  `--enable-expert-parallel` is emitted only when `model.moe` is true and carries **no
  degree** itself.
- `context_parallel_size` — CP degree.

## NEFF compile cache (warmup once)

Neuron compiles **one NEFF per bucket** (~minutes each via `neuronx-cc`), cached in
**`/var/tmp/neuron-compile-cache`**. All of a group's buckets compile into **one server per
`(model, regime, tp, dp, ep)`** on the **first** server start, then the server is **reused
across the whole shape×concurrency sweep** — so multi-minute compile is paid **once per
sharding**, not per cell. (A `dp>1` server compiles the same NEFF set — bucket derivation is
dp-invariant — and each replica loads it.) The persistent Desktop keeps this cache warm across `--smoke` → full sweep, and
the driver's `--startup-timeout` (default 3600 s) **includes** that first-start compile.

## trn2.48xlarge core math

- **16 Trainium2 chips × 128 physical cores**, LNC=2 → **64 LOGICAL cores** (the schedulable
  unit; every sharding's `tp*dp` must be ≤ 64 — `neuron_sweep._LOGICAL_CORES`).
- **~96 GB HBM/chip.**
- **`tp*dp` is the core budget, not `tp`.** vLLM-Neuron gives each DP replica its OWN
  disjoint window of `tp` logical cores (`tp_global_ranks = range(dp_rank*tp,
  (dp_rank+1)*tp)` over a global world of `tp*dp`), so cores **multiply** across `dp` —
  replicas do not share them. `tp16_dp4` = 64 cores = the whole box.
- **Unlike GPU, filling the box is not enforced.** `tp8_dp1` occupies 8 of 64 cores and
  leaves 56 idle — legal, but that cell's tok/s is a **partial-box** number (the driver and
  `preflight.py` both WARN). The box-filling ladder on trn2.48xlarge is
  `tp64_dp1 / tp32_dp2 / tp16_dp4 / tp8_dp8`.
- The KV budget scales with `tp` only (a TP=N replica's weights+KV live on just those N
  cores; `dp` adds replicas, it doesn't enlarge one), so **low tp is where the fit-check OOM
  bites** — and raising `dp` does not relieve it.
- `trn1.32xlarge` = 32 logical cores (the other row in `_LOGICAL_CORES`).
