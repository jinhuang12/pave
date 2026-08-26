# Host-and-environment exploration: Neuron-host mechanics

## 1. Question investigated

What local evidence exists about Neuron-host environment mechanics — DLAMI
baseline venv layout, per-campaign venv creation, compile-cache behavior and
locking, SSH execution patterns, and hardware handoff/queueing — that the
plugin's venv-manager and hardware-queue design must respect?

Scope was read-only: `/Users/jinhun/GitHub/vllm-neuron` (docs + code),
`.codex-stage6-*` files at the NeuronAgenticDevelopment repo root,
environment/hardware-session evidence inside `feature_port_campaigns/*` and
`model_port_campaigns/*` (not their validation artifacts or state machinery),
`skills/vllm-neuron-feature-port/references/` hardware/venv procedure only,
and `artifacts/` at repo root. No SSH, no web.

## 2. Evidence inventory

vllm-neuron repo:
- `README.md:9-17` — install command, extra-index-url.
- `docs/getting-started/setup-guide.md:37-104` — three install options
  (source, DLAMI, container), env var table.
- `docs/tutorials/tutorial-di-1p1d-xpyd.md:91` — second DLAMI venv path variant.
- `vllm_neuron/envs.py:25-260,330-392` — `VLLM_NEURON_CPU_MODE` and cache-dir
  resolution logic.
- `vllm_neuron/compile/cache.py:1-391` — `CompilationLock`, `fetch_remote_or_compile`,
  `save_cache`, `create_cache_hash`.
- `vllm_neuron/nki/nki_cache.py:1-249` — NKI-specific FileLock cache path.
- `vllm_neuron/utils/core_allocator.py:1-60` — `fcntl.flock`-based NeuronCore
  allocator for parallel pytest workers.
- `requirements/core.txt` — dependency list (no Neuron SDK packages).
- `pyproject.toml:28-29` — dependency source of truth.

NeuronAgenticDevelopment repo:
- `.codex-stage6-block-fp8-hardware-command.txt` — out-of-band SSH handoff
  command (one line).
- `.codex-stage6-block-fp8-hardware-gate.py` — the gate script that command runs.
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/environment.md` —
  full environment-preflight transcript (ssh commands, venv discovery, source-
  of-truth resolution, make-edit-live).
- `skills/vllm-neuron-feature-port/references/environment-preflight.md` —
  the procedural reference this transcript was following.
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/stage8-compiler-resource-decision/da_position.md` —
  hardware resource contention analysis on a live compile run (no queue tool).
- `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/artifacts/environment-preflight.json`,
  `trn2-2-host-migration-inventory.json`, `trn2-2-host-migration-authority.json` —
  host-migration and cross-campaign contamination evidence.
- `artifacts/pr-readiness/post-review/*.diff` — PR-readiness diffs only, no
  host/environment evidence (checked, out of scope for this lens).

## 3. Findings with citations

### 3a. DLAMI baseline venv layout (verified in docs, not by SSH)

- `docs/getting-started/setup-guide.md:61`: `source /opt/aws_neuronx_venv_pytorch_inference_vllm_0_21_0_1_0_0/bin/activate`.
- `docs/tutorials/tutorial-di-1p1d-xpyd.md:91`: a second, differently-suffixed
  path, `/opt/aws_neuronx_venv_pytorch_inference_vllm_0_21/bin/activate` — the
  suffix convention is not perfectly consistent across docs (`_0_21_0_1_0_0`
  vs `_0_21`). Treat the exact suffix as something to re-discover per host,
  not hardcode.
- `README.md:9-15`: source install uses
  `pip install --extra-index-url=https://pip.repos.neuron.amazonaws.com -e .`
  — this is the Neuron SDK's PyPI-style index, confirming the plugin's own
  `pyproject.toml`/`requirements/core.txt` deliberately does NOT vendor the
  Neuron SDK stack (torch-neuronx, neuronx-cc, torch-xla, libneuronxla are
  absent from `requirements/core.txt` — verified by reading the file in full;
  it lists only `vllm==0.21.0`, fastapi, transformers, prometheus_client,
  ml_dtypes, nixl, and generic Python deps). This is the exact "verified
  constraint" the goal brief's freeze-replicate recipe cites, and it is
  grounded directly in this file, not assumed.

### 3b. Per-campaign venv creation — no such mechanism exists yet; prior practice used the shared /opt venv directly

- `skills/vllm-neuron-feature-port/references/environment-preflight.md:23-26,105-115`
  documents the OLD (still current-on-disk) procedure: activate the shared
  `/opt/aws_neuronx_venv_pytorch_inference_vllm_0_21_0_1_0_0` venv directly,
  then run `pip install -e .` **inside that same shared venv** against
  whichever tracked source tree is live. There is no per-campaign venv step
  anywhere in this reference.
- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/environment.md:155-172`
  is the executed instance of that procedure: `pip install -e . --no-deps`
  was run with the shared `/opt/...` venv activated, uninstalling the prior
  wheel and installing an editable pointer to
  `~/p-eagle-port/vllm-neuron` directly into the DLAMI venv's site-packages
  (confirmed via `check_edit_live.py`, line 172).
- Direct evidence this caused cross-campaign contamination:
  `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/artifacts/environment-preflight.json`
  (`"editable_finder_mapping": {"vllm_neuron": "/home/ubuntu/p-eagle-port/vllm-neuron/vllm_neuron"}`,
  `"direct_url": "editable file:///home/ubuntu/p-eagle-port/vllm-neuron"`) shows
  that when the deepseek campaign moved onto `trn2-2`, the shared `/opt` venv
  there was still pointed at the P-EAGLE campaign's editable checkout from a
  prior campaign. The same artifact records the required remediation:
  `"Create or rebind an isolated campaign-local environment to clean pinned
  source."` — i.e., the deepseek campaign itself independently arrived at
  "we need per-campaign isolation," by hitting the exact failure mode the new
  requirements' freeze-replicate recipe is designed to prevent.
- This is the concrete basis for forbidden-effect #3 in the requirements
  (`cp -a` venv cloning and any pip write into `/opt`) — the old model's `pip
  install -e .` into `/opt` is precisely what must stop.

### 3c. Compile-cache behavior and locking (verified in code)

- Cache-root resolution: `vllm_neuron/envs.py:341-391`
  (`get_neuron_compile_cache_dir` / `_resolve_neuron_compile_cache_dir`).
  If `VLLM_CACHE_ROOT` is set, cache dir is unconditionally
  `$VLLM_CACHE_ROOT/neuron/compile_cache` (envs.py:376). If unset, defaults to
  `~/.cache/vllm/neuron/compile_cache`, with an NFS/Lustre probe that falls
  back to `/tmp/vllm_neuron_wdir_$USER/neuron/compile_cache` and a warning
  (envs.py:378-391) — "FileLock semantics" are explicitly called out as
  incompatible with NFS (envs.py:349-350), which is why the fallback exists.
- Per-key compilation locking: `vllm_neuron/compile/cache.py:328-376`
  (`CompilationLock`). Uses `filelock.FileLock` on a `.lock` sidecar file per
  hash key, `acquire(timeout=0.001)` (near-nonblocking — a losing process does
  NOT block, it falls through to `_wait_for_completion`, cache.py:150-153).
  On any exception inside the `with` block the process calls
  `sys.exit(f"FATAL: {exc_val}")` (cache.py:367-371) — compile failures are
  fatal to the process, not silently swallowed.
- Multi-node remote cache promotion: `save_cache` (cache.py:156-220) copies a
  completed local entry into the remote cache via a per-process staging dir
  then an atomic `rename` (cache.py docstring, 156-163); concurrent promotion
  from multiple nodes is explicitly designed to be a safe no-op for the loser
  (cache.py:171-174). NKI kernel cache entries are promoted alongside
  (cache.py:216-220), delegating to `save_nki_cache_to_remote`
  (`vllm_neuron/nki/nki_cache.py:188-...`, also atomic-rename based,
  nki_cache.py:192-197).
- NKI-specific cache path uses its own `filelock.FileLock` with a real
  blocking timeout (`_LOCK_TIMEOUT`, `nki_cache.py:167`), unlike the near-zero
  timeout in `CompilationLock` — two different locking postures in the same
  codebase for two cache subsystems.
- Cross-process NeuronCore allocation for parallel test workers uses a
  separate mechanism: `vllm_neuron/utils/core_allocator.py:1-60`, an
  `fcntl.flock`-based exclusive lock on a JSON state file at
  `/tmp/vllm_neuron/core_allocator/cores.lock` (core_allocator.py:31-33),
  independent of the filelock-based compile cache. This is the closest thing
  in the plugin codebase to a "hardware resource allocator," but it is scoped
  to NeuronCore-count coordination among **pytest-xdist workers on one host**,
  not cross-campaign or cross-host scheduling.
- The design intent throughout `compile/cache.py` and `nki/nki_cache.py` is
  that **concurrent readers/writers sharing one cache root are safe** — the
  lock-per-key and atomic-rename design exists specifically so that multiple
  processes (or campaigns, if they share `VLLM_CACHE_ROOT`) can hit the same
  cache without corrupting it. This supports (does not merely assume) the
  requirements' framing that the shared compile cache must never be cleared,
  because it is a working multi-writer resource, not a scratch dir.

### 3d. SSH execution patterns (from campaign history, not the vllm-neuron repo — it contains no SSH tooling itself)

- `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/environment.md:1-205`
  is a full transcript of the pattern actually used: every command is
  `ssh <host> '<single-quoted remote command>'`, one command per line, no
  session persistence, no wrapper script — e.g. line 10
  (`ssh trn2-2 'echo OK; hostname; whoami'`), line 27 (chained
  `source .../activate && python --version && pip show ...`), line 90
  (`git clone --branch ... ~/preflight-tmp/oss-check`). The environment.md
  file is itself the evidence artifact — no tool automates this; it is
  agent-typed ssh invocations narrated into a markdown transcript.
- The out-of-band pattern this run must NOT repeat:
  `.codex-stage6-block-fp8-hardware-command.txt` contains one fully-formed
  shell line meant to be run manually (by a human or a separate agent) over
  SSH: it activates the shared `/opt` venv, `cd`s into a specific worktree,
  sets `VLLM_PLUGINS`, `PJRT_DEVICE`, `NEURON_RT_VISIBLE_CORES`,
  `NEURON_CC_FLAGS`, `NEURON_PLATFORM_TARGET_OVERRIDE`, `VLLM_CACHE_ROOT`,
  `NEURON_COMPILE_CACHE_URL`, `PYTHONPATH`, and then runs
  `.codex-stage6-block-fp8-hardware-gate.py`. `.codex-stage6-block-fp8-hardware-gate.py`
  itself is a self-contained script with a hardcoded `WORKTREE` path
  (line 15-17) that does its own NxDI-contamination scan (`nxdi_modules()`,
  lines 38-46, and the `runtime_import_scan_before_activation` check at
  lines 95-98) before running a `torch.compile(..., backend="vllm_neuron")`
  correctness gate against `neuron:0` and writing `result.json`. This is
  exactly the pattern named in the requirements as forbidden going forward
  ("Hardware gates run in-workflow over SSH — no out-of-band handoff files").
  **Note**: `NEURON_COMPILE_CACHE_URL` set in that command line does not
  correspond to any environment variable read anywhere in the vllm-neuron
  codebase (`grep -rn "NEURON_COMPILE_CACHE_URL" .` in vllm-neuron returns
  zero hits; the actual cache-root override read by the plugin is
  `VLLM_CACHE_ROOT`, `envs.py:364`). This specific handoff command was setting
  an env var that had no effect on the plugin's real cache-resolution logic —
  a latent bug in the old out-of-band pattern, not merely a style problem.

### 3e. Hardware handoff/queueing — no formal queue existed; ad hoc authorization/lease language appears once, at the state-machine level

- No lock file, lease file, or queue data structure for **cross-campaign or
  cross-host** hardware access was found anywhere in the vllm-neuron repo or
  in the campaign histories. The only host-level locking found
  (`core_allocator.py`) is scoped to parallel pytest workers on one host
  (3c above), not to campaigns.
- `model_port_campaigns/glm-5-2-trn2-1-concurrency-32-plugin-native-vllm21/artifacts/stage8-compiler-resource-decision/da_position.md:1-89`
  shows how resource contention on a live compile run was actually handled:
  an ad hoc adversarial ("devil's advocate") agent read `ssh trn2-1` process
  tables, memory, and the current compile-worker count
  (`vllm_neuron/compile/parallel_compile.py:63` /
  `vllm_neuron/envs.py:153-155`, `VLLM_NEURON_PARALLEL_COMPILE_WORKERS`,
  default 8) and produced a written recommendation not to restart the run —
  no automated queue, lease, or scheduler was involved; a human/agent judgment
  call substituted for a queue.
- The one place "lease" and "authorization" language appears is at the
  campaign **state-machine** level, not a hardware-queue tool:
  `model_port_campaigns/deepseek-v4-flash-0731-trn2-1-direct-vllm-neuron/artifacts/trn2-2-host-migration-authority.json`
  records a user-directed host migration (trn2-1 → trn2-2) and lists required
  canonical actions including `"Release every trn2-1 resource lease."` and
  `authority_limits.authorization_or_lease_creation_authorized: false`
  (gating a fresh lease grant behind a subsequent P1 authorization step). This
  shows the campaign's own state machine already modeled "resource lease" as
  a concept tied to canonical state transitions, but this is bespoke to that
  one campaign's JSON artifacts, not a reusable queue component; there is no
  evidence of a mechanism that would let two *concurrent* campaigns coordinate
  for the *same* host — the deepseek campaign's migration was sequential
  (fully vacate trn2-1 authorization before trn2-2 authorization), not a
  live multi-tenant queue.
- `trn2-2-host-migration-inventory.json` additionally records an
  `"occupancy": {"neuron_device_holders": []}` field — evidence that *some*
  device-occupancy bookkeeping was attempted for this one campaign's own
  inventory, but it is a point-in-time inventory snapshot, not a live
  queue/lease service other campaigns could consult.
- Conclusion: prior practice had **no reusable hardware-queue mechanism**.
  The new plugin's "exclusive hardware-queue lease with pre-action identity
  re-verification" (requirements) is new construction, not a formalization of
  an existing tool — though the deepseek campaign's authority/lease JSON
  shape is a reasonable input to that design (it already captures
  instance-id + hostname, `trn2-2-host-migration-inventory.json`'s
  `target.instance_id`, `target.hostname` fields, matching the requirements'
  "instance-id + hostname + boot-ID" identity re-verification language,
  minus boot-ID, which was not found recorded anywhere in this evidence).

### 3f. `VLLM_NEURON_CPU_MODE` (verified, CPU-first precedent is real and current)

- `vllm_neuron/envs.py:25,120-122` defines the flag; used pervasively across
  `vllm_neuron/vllm/platform.py:125-126`, `neuron_worker.py` (5 call sites),
  `neuron_model_runner.py:1163`, `compile/backend.py:29,188`,
  `vllm/spec_decode/eagle.py:468`, `nki/nki_hop.py` (3 sites),
  `utils/dtype_utils.py:30`, `__init__.py` (4 sites).
- `docs/model-dev/cpu-development.md:48,68,80,127,172,176,249,259,286` documents
  it as the sanctioned CPU-development mode; incompatible with
  `VLLM_NEURON_CPU_COMPILE` (`__init__.py:82-84` enforces this at runtime with
  a raised error, not just a doc note).
- Unit tests already use this precedent directly:
  `test/unit/spec_decode/test_eagle3_two_pass_kv_prime.py:34`,
  `test_eagle3_parallel_forward.py:37`, `test_eagle3_multilayer_backbone.py:29`,
  `test_eagle4_routing.py:28` all `os.environ.setdefault("VLLM_NEURON_CPU_MODE", "1")`.
  This directly grounds the requirements' "CPU-first increments... before
  hardware attempts" and the P-EAGLE `test/unit` test-layout precedent named
  in the goal brief.

## 4. Verified vs. assumed facts

**Verified environment facts (grounded in files read this session):**
- The DLAMI-shipped venv path pattern `/opt/aws_neuronx_venv_pytorch_inference_vllm_<suffix>` is documented in two places with two different suffix conventions (setup-guide.md:61 vs tutorial-di-1p1d-xpyd.md:91) — real, but not a single canonical string.
- `requirements/core.txt` contains zero Neuron SDK packages (torch-neuronx, neuronx-cc, torch-xla, libneuronxla all absent) — this is the literal grounding for the freeze-replicate recipe's stated rationale.
- The compile cache is resolved from `VLLM_CACHE_ROOT` (or a documented fallback chain) at `envs.py:341-391`, is filelock-protected per hash key (`cache.py:328-376`), and is designed for safe concurrent/multi-node access via atomic rename (`cache.py:156-220`).
- `VLLM_NEURON_CPU_MODE` is a first-class, heavily-used flag, not a doc-only convention.
- Prior practice (P-EAGLE campaign, `environment.md`) installed editable source directly into the shared `/opt` DLAMI venv, and this directly caused a documented cross-campaign contamination (deepseek campaign's `environment-preflight.json`).
- One out-of-band SSH-handoff file pair existed at the NeuronAgenticDevelopment repo root (`.codex-stage6-*`) and used an environment variable (`NEURON_COMPILE_CACHE_URL`) that the plugin's cache-resolution code does not read.
- No cross-campaign hardware-queue/lease tool exists in either repo today; the closest precedent is bespoke JSON state in one campaign (deepseek) and an ad hoc human/agent judgment call in another (glm-5-2, da_position.md).

**Assumed, must verify at runtime (cannot be confirmed without SSH — do NOT SSH per this task's boundary):**
- Whether any *currently standing* Neuron host still has the exact
  `/opt/aws_neuronx_venv_pytorch_inference_vllm_0_21_0_1_0_0` path, and whether
  its `pip freeze` output is internally consistent (no leftover editable
  pointers from old campaigns, per the exact contamination class found in the
  deepseek evidence).
- Whether the shared compile cache directories named in the requirements
  (`$VLLM_CACHE_ROOT/neuron/compile_cache`, `~/.cache/vllm/neuron/compile_cache`,
  `/var/tmp/neuron-compile-cache`) currently hold live entries from other
  users/campaigns on the standing hosts (the P-EAGLE `environment.md:126-128`
  found them empty on `trn2-2` on 2026-08-07 — a stale, host-specific,
  point-in-time fact, not a current guarantee).
- Whether `torch-neuronx`/`neuronx-cc`/`torch-xla`/`libneuronxla` versions are
  actually uniform across whichever hosts get assigned to this run (requirement:
  "one compiler version across campaigns" — this is a constraint to enforce,
  not a fact yet observed for the live fleet).
- Disk headroom for `~10 GB per venv replica` on the actual standing hosts (the
  deepseek migration inventory recorded `root_available_bytes: 4145784557568`
  ≈ 3.9 TiB on trn2-2 as of 2026-08-12 — plausible headroom, but stale and
  host-specific, not re-verifiable here).
- Whether `boot-ID` (named in the requirements' identity re-verification
  language) is obtainable/stable on the actual instance type in use; no local
  evidence shows this was ever recorded by prior campaigns (only instance-id
  and hostname were, per `trn2-2-host-migration-inventory.json`).

## 5. What the freeze-replicate venv recipe can be grounded on locally vs. what remains assumption A1

Grounded locally (no runtime dependency):
- The rationale ("naive fresh venv + `pip install -e .` yields an env that
  cannot run on Neuron") is directly confirmed by `requirements/core.txt`
  omitting the Neuron SDK stack.
- `pip freeze` + reinstall-with-extra-index-url is a reasonable mechanical
  approach given the documented install command in `README.md:9-15` uses that
  same extra-index-url.
- `pip install -e <worktree> --no-deps` (plugin-only, no-deps) is exactly the
  step already exercised and verified working in
  `feature_port_campaigns/p-eagle-gpt-oss-20b-2026-08-07/environment.md:157-172`
  — the mechanics of "editable install + `check_edit_live.py` verification"
  are proven to work in this codebase; only the *target* changes (a fresh
  per-campaign venv instead of the shared `/opt` venv).
- The compile-cache design (3c above) supports sharing one cache root across
  independently-created venvs, as long as `VLLM_CACHE_ROOT` (or its default
  resolution) points to the same shared path and the compiler-version pin is
  respected — this is a real design property of `cache.py`/`envs.py`, not an
  assumption.

Remains assumption A1 (per the requirements doc's own labeling, restated
here with what specifically is unverifiable without SSH):
- That standing instances are reachable over SSH at campaign time.
- That they still carry the DLAMI baseline venv at the expected
  `/opt/aws_neuronx_venv_pytorch_inference_vllm_*` path (exact suffix TBD per
  host, per 3a above).
- That a `pip freeze` from that baseline venv, replayed into a fresh venv with
  the Neuron extra-index-url, actually reproduces a working Neuron-capable
  environment on first try (no local evidence of this exact recipe — freeze +
  replay into a *new* venv — having been executed anywhere in the campaign
  history; all prior campaigns installed directly into the shared venv,
  3b above, which is precisely the pattern being replaced. There is no
  fallback precedent to point to if freeze-replicate has an unforeseen gap —
  e.g., native extensions with absolute paths baked in, or DLAMI-vendored
  packages not available on the Neuron package index by exact pinned version).

## 6. Contradictions and stale documentation

1. **`skills/vllm-neuron-feature-port/references/environment-preflight.md`
   (lines 23-26, 105-115) is stale against the new requirements.** It
   documents and mandates installing directly into the shared `/opt` DLAMI
   venv — precisely forbidden effect #3 in the new requirements ("any pip
   write into /opt"). This file is a live, current-on-disk reference (not
   marked deprecated) that the new plugin must NOT reuse as-is for the venv
   procedure, even though the requirements doc says the absorbed skill's
   materials are "reused as input." Its environment-discovery steps (venv
   version resolution, source-tree discovery, marker-file diffing, make-edit-
   live verification) remain directly reusable; its installation *target*
   does not.
2. **The `.codex-stage6-*` out-of-band handoff pair is itself now-obsolete
   evidence of the exact anti-pattern the requirements explicitly ban**
   ("Hardware gates run in-workflow over SSH — no out-of-band handoff
   files"). Its `NEURON_COMPILE_CACHE_URL` env var (in the `.txt` command)
   has no effect in the current vllm-neuron codebase (3d above) — either a
   leftover from an earlier plugin version, a misremembered variable name, or
   dead configuration; either way it should not be treated as a real
   supported override when the plugin's own hardware-gate tooling is built.
3. **Two DLAMI venv suffix conventions appear in vllm-neuron's own docs**
   (`setup-guide.md:61` = `..._0_21_0_1_0_0`; `tutorial-di-1p1d-xpyd.md:91` =
   `..._0_21`) — internally inconsistent; the plugin's venv-manager should
   discover the path (e.g., `ls /opt | grep vllm`) rather than hardcode either
   string.
4. **No contradiction found, but worth flagging as a documentation gap**: the
   setup guide's env var table (`setup-guide.md:95-99`) lists only
   `VLLM_CACHE_ROOT`, `VLLM_NEURON_LOG_LEVEL`, and `HF_TOKEN` — it omits
   `VLLM_NEURON_CPU_MODE` entirely (that appears only in
   `docs/guides/reference-configuration.md:183` and `docs/model-dev/*`). A
   reader following only the setup guide would not learn about CPU-first mode
   from that page.

## 7. Graph implications

(Observational only — not proposing plan structure; the planner owns
synthesis.)

- Whatever venv-manager component exists needs a **discovery step** before a
  **freeze step**, because the exact `/opt` venv path/suffix is not a fixed
  literal (finding 3a, contradiction 3).
- The freeze-replicate recipe's riskiest, least-locally-grounded step is the
  "replay `pip freeze` into a fresh venv and expect it to be Neuron-capable"
  step (section 5) — this has no local precedent of ever having been run; any
  node built around it should treat first execution as a verification step in
  its own right, not an assumed-safe mechanical operation.
- A hardware-queue/lease component is genuinely new construction, not a
  wrapper around an existing tool (section 3e) — no code or reusable schema to
  delegate to locally, though the deepseek campaign's `*-host-migration-authority.json`
  shape (instance-id + hostname + lease-release-before-reacquire sequencing)
  is a usable reference shape for the "resolved instance identity... recorded
  at kickoff and re-verified" requirement — minus boot-ID, which has no local
  precedent at all.
- The compile-cache locking design (3c) already assumes and supports multiple
  concurrent writers sharing one cache root — this is a point in favor of
  letting parallel campaigns share `VLLM_CACHE_ROOT` directly (as the
  requirements intend) rather than needing the workflow to build its own
  cache-serialization layer on top; the plugin's own FileLock/atomic-rename
  design already does that job.
- The `core_allocator.py` fcntl-flock pattern (3c) is a working, in-repo
  precedent for "exclusive lease over a shared numbered resource on one host"
  — relevant prior art for a hardware-queue's *implementation technique* even
  though its current scope (pytest-xdist workers) is unrelated to
  cross-campaign scheduling.

## 8. Remaining evidence gaps

- No local evidence of the freeze-replicate recipe (or anything like it)
  having been executed anywhere in this codebase's history — first real
  execution is unverified territory (section 5).
- No local evidence of `boot-ID` ever being captured for identity
  re-verification (section 3e, 4).
- No local evidence of a live, currently-populated compile cache on any
  standing host — the one on-disk observation (P-EAGLE, trn2-2, 2026-08-07)
  found it empty; current occupancy is unknown without SSH.
- No local evidence of what happens when two campaigns' freeze-replicate
  venvs disagree on Neuron SDK versions during a `torch-neuronx`/`neuronx-cc`
  pin mismatch — the "one compiler version across campaigns" requirement is
  stated but not exercised anywhere in the evidence reviewed.
- Whether `VLLM_NEURON_REMOTE_CACHE` (`envs.py:202`) or `VLLM_NEURON_SWITCH_CC`
  (`envs.py:196`) — both real, defined env vars not otherwise investigated
  here — interact with the freeze-replicate/hardware-queue design; out of this
  lens's read budget, flagged for another pass if relevant.
- Host identity fields available in practice (instance-id, hostname, AMI,
  kernel — all present in `trn2-2-host-migration-inventory.json`) were
  observed for exactly one host on one date; whether the *currently* assigned
  hosts for this run expose the same fields via the same commands is
  unverified without SSH.
