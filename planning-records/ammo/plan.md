# Plan - orchestrated hand-port of AMMO into generated-plugins/ammo

Read `requirements.md` then `conventions.md` (`R`, `SRC`, `S`, `$PYTEST` are defined there). Write sets are absolute, file-level, and disjoint inside a wave. Workers run on opus, reviewers on fable. Reviewers review `diff -ru <copy source> <file>` for ported files, not whole files.

Shared rules for every worker:
- Edit deltas on the seeded tree (wave 0 lands it). Do not rename a module, public function, CLI flag, or state field; a needed cross-worker change is a BLOCKED to team-lead.
- Acceptance for every worker includes conventions §9: `$PYTEST` with no regression against `planning-records/ammo/baseline.md`, and the structural tests green for the worker's files. Per-worker bullets below list only the extra checks.
- Findings from a later wave go to the worker that owns the file; workers stay on the roster until the whole-goal review passes.

## Wave 0 - seed, shared layer, graph, warm host (4 workers, 2 reviewers)

| Worker | Surface | Write set | Reviewer |
|---|---|---|---|
| worker-1 | Seed copy + codemod + baseline | everything under `R/` except the write sets of worker-2 and worker-4; `planning-records/ammo/codemod.py`, `planning-records/ammo/baseline.md` | da-1 |
| worker-2 | Resolution layer + structural tests | `R/scripts/campaign_paths.py`, `R/scripts/ammo`, `R/hooks/_find_campaign.sh`, `R/references/campaign-env.md`, `R/tests/conftest.py`, `R/tests/test_campaign_paths.py`, `R/tests/test_path_refs.py`, `R/tests/test_env_inventory.py`, `R/tests/test_hooks_silent.py`, `R/tests/test_prose_bans.py`, `R/tests/test_hooks_json.py`, `R/hooks/hooks.json`, `R/hooks/tests/test-find-campaign.sh`, `planning-records/ammo/gen_hooks_json.py` | da-2 |
| worker-3 | Warm smoke container on g6e-cdd | on g6e-cdd: container `ammo-plugin-smoke`, `/home/jinhun/ammo-plugin-smoke/`; `planning-records/ammo/host-prep.md` | da-1 |
| worker-4 | PAVE graph, README, VERSION | `R/workflow.pave.yaml`, `R/revisions.yaml`, `R/history/.gitkeep`, `R/scripts/validate_pave.py`, `R/scripts/record_revision.py`, `R/references/pave.schema.json`, `R/references/pave-composition.schema.json`, `R/tests/test_workflow_pave.py`, `R/README.md`, `R/VERSION` | da-2 |

Also at t0, outside the worker roster: a sonnet research seat writes `planning-records/ammo/pumice-delta.md` (team-lead spawns it): diffs of `src/pumice/config.py`, `rust_grpc_server.py`, `src/pumice/compilation/backend.py`, `src/pumice/engine/model_runner.py` between `9d126713` and `769c7529`; the `PUMICE_*` name set at HEAD diffed against the 134-name seed in `pumice_env_allowlist.txt`; the `REQUIRED_TORCH` source line; whether gpt-oss still selects the `vllm` runner; the proto diff command and its empty output; the rows of `~/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md` that name plugin scripts. Wave-1 workers read it instead of re-deriving.

Wave 1 starts when worker-1 and worker-2 PASS. worker-3 and worker-4 may still be open; worker-3 gates only wave 2, worker-4 only the Final.

### worker-1 acceptance
- `codemod.py` is a closed ordered rule set in the shape of `render_ammo_variants.py`'s `CODEX_RULES` (conventions §2 table rows, `kernel_opt_artifacts` to the `campaign_root` key, `$CLAUDE_PROJECT_DIR/.claude/hooks/` to `${CLAUDE_PLUGIN_ROOT}/hooks/`), applied once over the copied tree; its output diff is committed as `planning-records/ammo/codemod.diff`.
- Every file in conventions §10 "copy source", "Codex-only", and "server-side" lists lands at its §1 location with the Claude dialect where twins exist; `tests/agents/`, `eval/`, `preflight_vllm_api.py` and `test_preflight_vllm_api.py` are not copied (requirements 13; `preflight_pumice.py:4` says it replaces the vLLM preflight).
- Server-side tests fix their one-line scripts path and import from `R/scripts`.
- `baseline.md` records the `$PYTEST` summary line on this Mac before and after the codemod, and the hook harness results from `rsync` + `docker exec` on g6e-cdd once worker-3 reports the container up (or on the host's bash if the container is not up yet, marked as such).
- `install_agents.py` and `test_codex_port.py` are copied from vllm-neuron-parity unchanged for worker-5 to adapt. Worker-4 copies the graph tooling itself. Worker-1 does not create `hooks/hooks.json`; worker-2 generates it.

### worker-2 acceptance
- `campaign_paths.py` implements the §2 signatures; `test_campaign_paths.py` covers env override, walk from engine root, walk from a `.claude/worktrees/<name>` child, not found, `in_campaign` true and false, `python_exe` fallbacks, `apply_env` setdefault semantics.
- `campaign-env.md` lists every `AMMO_*` name the seeded `scripts/`, `hooks/`, `codex/` read (`grep -rhoE 'AMMO_[A-Z0-9_]+'`), each with its row; `test_env_inventory.py` passes on the seeded tree.
- `test_path_refs.py`, `test_prose_bans.py`, `test_hooks_silent.py`, `test_hooks_json.py` run and report their current failures as a list in the DONE message; they are expected to fail on the seeded tree and pass after wave 1.
- `_find_campaign.sh` has a shell test under `R/hooks/tests/test-find-campaign.sh` (env, walk, worktree child, none, silent on failure).
- `gen_hooks_json.py` produces `R/hooks/hooks.json` from `SRC/.claude/settings.local.json` and the §7 keep list; `test_hooks_json.py` regenerates and compares byte for byte.

### worker-3 acceptance
- `ssh g6e-cdd docker ps` lists `ammo-plugin-smoke` started from `ammo-server-pumice-cuda:latest` with `--entrypoint bash`, no published ports, `--gpus '"device=<ids>"'` restricted to GPUs that `nvidia-smi --query-gpu=index,memory.used` showed idle at start, and `/home/jinhun/ammo-plugin-smoke/plugin` bind-mounted read-only at `/mnt/ammo`.
- Inside: `/workspace/pumice` at `769c7529` (`git fetch` from the configured remote, `git checkout --detach 769c7529`; the user's branch is not reset), `cargo build --release` reusing the image's cargo target dir when present, `pip install -e .` into the engine venv; the exact commands and the time each took are in `host-prep.md`.
- `python -c "import pumice, pumice_rust; from pumice import config; print(config.REQUIRED_VLLM_VERSION)"` inside the engine venv prints `0.32.0`.
- A gpt-oss model is present under the image's HF cache or downloaded; its path and size are in `host-prep.md`. The sm80 patch from `S/docker/pumice-sm80-gpt-oss.patch` applies to `769c7529` or its conflict is recorded.
- `claude --version` works inside the container; credentials path recorded. A build failure that cannot be fixed inside two attempts is a BLOCKED with Failed/Tried/Cause/Need.

### worker-4 acceptance
- `uv run --no-project --with pyyaml --with jsonschema python R/scripts/validate_pave.py R/workflow.pave.yaml` exits 0.
- Node ids are exactly: `scaffold_target`, `preflight_engine`, `1_baseline`, `audit_s1`, `2_bottleneck_mining`, `audit_s2`, `3_debate`, `4_5_parallel_tracks` (dynamic fan-out per winner, with gates 5.1a 5.1b 5.2 5.3a 5.3b as checks), `audit_s45`, `6_integration`, `audit_s67`, `7_campaign_eval`, `7b_report`; control nodes `campaign_complete`, `campaign_exhausted`, `blocked`, `paused_for_user`, plus the join before `6_integration`. Stage ids and loop edges are derived from `R/scripts/transitions.json` (`stage_ladder`, `outcome_next_stage`, `terminal_statuses`, `audit_stage_key_map`); outcome names copied from the stage text of `SRC/.claude/skills/ammo/SKILL.md`. The state contract names `schemas/state.schema.json` and `scripts/transitions.json` as the live run state (graph governs design, `state.json` governs a run).
- `test_workflow_pave.py` asserts the node-id set equals the list above, that the ids cover `stage_ladder`, and that README quotes the validator's counts.
- `revisions.yaml` entry 0 is written with `record_revision.py init`; `record_revision.py verify` on a seeded `/tmp` folder passes.
- `README.md` follows vllm-neuron-parity sections 1-6 with ammo names: what it does, install for both harnesses (the six Codex steps), what the user pre-builds (Pumice at the pin, venv, cargo build, weights, GPUs), how a campaign starts (`/ammo:ammo` with target flags from `references/workload-invocation.md`), workspace layout (§3), GPU ownership (§5), a link to `hooks/README.md`, the graph render with counts, not in v1 (Neuron, eval, server features). `VERSION` is `1.0.0` with one entry. A README check: `grep -c '^## ' R/README.md` >= 6 and `test_prose_bans.py` passes on it.

## Wave 1 - de-containerize the six surfaces (6 workers, reviewers da-1 and da-2 continue)

| Worker | Surface | Write set (under R) | Reviewer |
|---|---|---|---|
| worker-5 | Lead skill, report skill, orchestration, agents, Codex agents, manifests | `skills/`, `orchestration/`, `agents/`, `codex/agents/`, `codex/install_agents.py`, `codex/RUNTIME.md`, `references/harness-binding.md` (new), `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `tests/test_codex_port.py`, and the ammo entry in `/Users/jinhun/GitHub/pave/.claude-plugin/marketplace.json` | da-1 |
| worker-6 | References | `references/*.md` except `campaign-env.md` and `harness-binding.md` | da-2 |
| worker-7 | Measurement scripts + pin | `scripts/run_pumice_grpc_bench.py`, `scripts/run_vllm_bench_latency_sweep.py`, `scripts/preflight_pumice.py`, `scripts/pumice_pins.py`, `scripts/pumice_pb/`, `scripts/runtime_ops.py`, `scripts/mine_trace.py`, `scripts/ncu_sanity.sh`, `scripts/ncu_sanity_driver.py`; tests `test_pumice_bench_*.py`, `test_preflight_pumice*.py`, `test_pumice_pins.py`, `test_runtime_ops.py`, `test_mine_trace*.py`, `test_sweep_*.py`, `test_correctness_comparator.py`, `test_parse_artifacts.py`, `test_evidence_freshness.py` | da-1 |
| worker-8 | State, scaffold, gates | `scripts/new_target.py`, `scripts/ammo_state.py`, `scripts/hook_cmd_classify.py`, `scripts/verify_validation_gates.py`, `scripts/generate_validation_report.py`, `scripts/reconcile_track_state.py`, `scripts/check_projection_accuracy.py`, `scripts/create_evidence_template.py`, `scripts/transcript_filter.py`, `scripts/transitions.json`, `scripts/artifact_layout.json`, `schemas/state.schema.json`; tests: every `tests/*.py` not named for worker-2, worker-4, worker-7, worker-9, plus `tests/scripts/`, `tests/fixtures/`, `tests/run_all.sh` | da-2 |
| worker-9 | Serving, GPU lock, Codex runtime | `scripts/serving_*.py`, `scripts/run_serving_sweep.py`, `scripts/deployment_ops.py`, `scripts/gpu_reservation.py`, `schemas/serving.schema.json`, `schemas/serving-arm.schema.json`, `data/`, `codex/hooks/`, `codex/scripts/`, `codex/instrumentation/`, `codex/hooks.json` (if §7 needs it); tests `test_ammo_serving*.py`, `test_gpu_reservation*.py`, `test_neuron_reservation.py`, `test_codex_hook_campaign_resolution.py`, the six Codex-only tests | da-1 |
| worker-10 | Claude hooks | `hooks/` except `_find_campaign.sh`, `hooks.json` (regenerated through `gen_hooks_json.py` only), `hooks/README.md`, `hooks/pumice_env_allowlist.txt`, `hooks/tests/` except `test-find-campaign.sh`; tests `test_env_default_guard_hook.py`, `test_worktree_hook.py` | da-2 |

### worker-5 acceptance
- `python3 -c "import json;assert json.load(open('R/.claude-plugin/plugin.json'))['name']=='ammo'"`; same for `.codex-plugin`.
- `skills/ammo/SKILL.md` frontmatter: `name: ammo`, description ending `Manual-only: use only when the user invokes /ammo:ammo.`, `metadata.compatibility` present. `skills/ammo-report/SKILL.md` has `name: ammo-report`. The runtime-binding paragraph (conventions §8) replaces "Read server-injected AMMO_RUNTIME_PROFILE" with reading `target.json`.
- `python3 R/codex/install_agents.py --project /tmp/ammo-codex-check && python3 R/codex/install_agents.py --project /tmp/ammo-codex-check --check` exits 0 and reports eleven agents.
- `$PYTEST tests/test_codex_port.py -q` passes, including one new test that every `agents/*.md` has a TOML twin.
- `grep -c "CUDA path only" R/skills/ammo/SKILL.md` = 1; `test_path_refs.py` and `test_prose_bans.py` pass on the write set.

### worker-6 acceptance
- `diff <(ls SRC/.claude/skills/ammo/references) <(ls R/references | grep -v -e campaign-env -e harness-binding -e pave)` is empty.
- Stale pin facts replaced per conventions §6 using `pumice-delta.md`; `grep -c "CUDA path only" R/references/accelerator-binding.md` = 1 and 0 in every other reference.
- `test_path_refs.py`, `test_prose_bans.py`, and the ported `test_guidance_semantic_contract.py` link tests pass on `R/references`.

### worker-7 acceptance
- `pumice_pins.py` per conventions §6; `test_pumice_pins.py` passes with the new constants; `preflight_pumice.py` imports the sibling and calls `require_supported_profile()`.
- Every CLI flag and env var `run_pumice_grpc_bench.py` passes to `rust_grpc_server.py` or `pumice-server` exists at `769c7529` (list with file:line in the DONE message, cross-checked against `pumice-delta.md`); the `0.30.0` literal at the runner's version table is replaced per §6.
- `scripts/pumice_pb/README.md` records the proto diff command from requirements and its empty output; no regeneration.
- `grep -n "AMMO_RUNTIME_SOURCE_DIR\|/workspace" R/scripts/preflight_pumice.py R/scripts/runtime_ops.py` shows the env name only as a §4 alias and no `/workspace`.

### worker-8 acceptance
- `new_target.py` mints the campaign id, writes `<engine>/.ammo/<id>/{target.json,state.json}`, writes `.ammo/active`, appends `.ammo/` to `.git/info/exclude` once, records `engine.root`, `engine.venv`, `runtime.profile = pumice-cuda`, `runtime.accelerator_kind = cuda`, calls `require_supported_profile()`; one test per fact in `test_new_target_v2_scaffold.py` or a new `test_new_target_campaign.py`.
- `artifact_layout.json` has the `campaign_root` key; `test_artifact_layout_manifest.py` passes; `ammo_state.py` and the gate scripts resolve schemas through `plugin_path`.
- `hook_cmd_classify.py` classifies `.ammo/<id>/...` paths as campaign paths; parity tests pass.
- `bash R/tests/run_all.sh` runs `$PYTEST` and the `hooks/tests/*.sh` loop, with `BASE` resolved to R and the eval dirs removed.

### worker-9 acceptance
- `gpu_reservation.py` per conventions §5; a new test shows two campaign ids cannot hold one physical GPU in the shared state dir, and `reserve` filters by `CUDA_VISIBLE_DEVICES`; existing reservation tests pass.
- Ported serving tests pass; `serving_benchmark_adapter.py` has the version-table row for the pinned vLLM (value from `pumice-delta.md`, literal allowed only through `pumice_pins`).
- `deployment_ops.py` resolves the engine root through `campaign_paths`.
- `codex/hooks/common.py` imports `campaign_paths`; `test_codex_hook_campaign_resolution.py` passes with the marker; `python3 -m py_compile R/codex/hooks/*.py R/codex/scripts/*.py` exits 0; `grep -rnE '^\s*(from|import) (shared|app)\b' R/codex` is empty.
- `cmp R/data/gsm8k_full.json SRC/.claude/skills/ammo/data/gsm8k_full.json` and the subset file: identical.

### worker-10 acceptance
- `hooks.json` regenerated through `gen_hooks_json.py`; `test_hooks_json.py` passes; every command path exists.
- Every kept hook sources `_find_campaign.sh`, uses `ammo_python`, and classifies paths with `ammo_in_campaign`; no `kernel_opt_artifacts`, no `.venv/bin/python` probe relative to the hook file.
- `hooks/README.md` per conventions §7 with intents from `S/docs/ammo-intent-map.json`.
- `pumice_env_allowlist.txt` regenerated at `769c7529` with its header recipe; `test_env_default_guard_hook.py` count updated and passing.
- On g6e-cdd in the warm container: every `hooks/tests/test-*.sh` passes and `test_hooks_silent.py` passes (rsync + docker exec; output pasted).

## Wave 2 - clean-room run and real smoke on g6e-cdd (1 worker)

| Worker | Surface | Write set | Reviewer |
|---|---|---|---|
| worker-11 | Clean-room + Stage 1 smoke | `planning-records/ammo/smoke-g6e-cdd.md`; inside `ammo-plugin-smoke`: `/workspace/pumice/.ammo/`, `/root/.claude` plugin install | da-1 |

### worker-11 acceptance
- Plugin loaded from the bind mount: `claude plugin validate /mnt/ammo` (or the equivalent `claude plugin` subcommand, named in the record) exits 0, and `claude --plugin-dir /mnt/ammo -p "/ammo:ammo --help"` lists the skill.
- Fail-closed check first: with `AMMO_PYTHON` pointed at a venv without `pumice`, `/ammo:ammo` scaffolds and preflight stops naming the missing import; `.ammo/<id>/state.json` exists with `campaign.status` not advanced past preflight.
- Real run: with the real venv, one L40S reserved through `gpu_reservation.py`, gpt-oss with the sm80 patch, the campaign reaches `T_AUDIT_S1` passed: `rounds/1/baseline/e2e_latency_results.json` exists, its fingerprint `pumice_commit` is `769c7529`, `state.json` shows `audit.stage_1.passed_at`. Transcript excerpts, every file under `.ammo/<id>/`, and GPU memory before and after are in the record.
- Hooks fired: the record quotes one next-step reminder and one state-validate pass from the session.
- `gpu_reservation.py status --table` is empty at the end; the container is stopped, not removed.

## Final
- Whole-goal review by da-2: marketplace install, `bash R/tests/run_all.sh`, `validate_pave.py`, `claude plugin validate`, the smoke record, and the seams (SKILL.md text against hooks, graph node names against SKILL.md stage names, env table against scripts).
- team-lead commits on branch `ammo-plugin`, no push.
