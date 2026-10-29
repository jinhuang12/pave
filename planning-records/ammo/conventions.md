# Conventions - the interface contract every ammo worker follows

Read before editing. To change a rule here, send BLOCKED to team-lead; never change it locally. `R` = `/Users/jinhun/GitHub/pave/generated-plugins/ammo`. `SRC` = `/Users/jinhun/GitHub/ammo-server/ai_cli_session`. `S` = `/Users/jinhun/GitHub/ammo-server`.

## 1. Plugin layout (directories only; plan.md write sets are the file-level list)

```
R/
  .claude-plugin/  .codex-plugin/           manifests, name "ammo"
  README.md  VERSION  workflow.pave.yaml  revisions.yaml  history/
  skills/ammo/SKILL.md                     lead skill, address ammo:ammo
  skills/ammo-report/SKILL.md              report sub-skill, address ammo:ammo-report
  agents/                                  9 Claude role agents, addresses ammo:<name>
  codex/agents/  codex/hooks/  codex/scripts/  codex/instrumentation/  codex/install_agents.py  codex/RUNTIME.md
  orchestration/  references/  schemas/  scripts/  data/  hooks/  hooks/tests/  tests/
```

Flat like vllm-neuron-parity: `scripts/`, `references/`, `orchestration/`, `schemas/` sit at `R`, not under `skills/ammo/`.

## 2. One resolution layer, not per-file rewrites

`scripts/campaign_paths.py` is the only place that knows where things are. Everything else calls it.

```python
find_campaign_dir(start: Path | None = None, env: Mapping | None = None) -> Path | None
    # AMMO_ARTIFACT_DIR env, else walk up from start (default cwd) to the first <dir>/.ammo/active
    # and return <dir>/.ammo/<id>. No git calls: track worktrees live under <engine>/.claude/worktrees/<name>,
    # so the walk reaches the marker.
campaign_id(campaign_dir) -> str          # basename
engine_root(campaign_dir) -> Path         # parent of .ammo
plugin_root() -> Path                     # parent of scripts/
plugin_path(*parts) -> Path               # plugin_root()/parts ("schemas", "state.schema.json")
python_exe(campaign_dir | None) -> Path   # <track worktree>/.venv/bin/python when cwd is in another worktree of the engine's git repo (same common git dir; ruled 2026-10-28, addendum),
                                          # else target.json engine.venv, else AMMO_PYTHON, else <engine>/.venv/bin/python, else python3.
                                          # new_target.py records engine.venv from AMMO_PYTHON when that is set at scaffold (ruled 2026-10-28),
                                          # so the recorded venv is the frozen contract and wins afterwards. An empty env value counts as unset.
in_campaign(path, campaign_dir) -> bool   # realpath containment; the one path classifier
campaign_env(campaign_dir) -> dict        # every name in the env table (§4) derived from target.json
apply_env(env=os.environ) -> Path | None  # find, then env.setdefault each derived name; explicit env wins;
                                          # records every name it set in AMMO_APPLIED_ENV (JSON name -> prior value or null; first prior wins)
unapply_env(env=os.environ) -> None       # restores the AMMO_APPLIED_ENV record (AMMO_* and CARGO_TARGET_DIR only) and removes it;
                                          # new_target.py calls it first so a scaffold reads the user's env, never the active campaign's (ruled 2026-10-28)
```

- Every script calls `apply_env()` once at the top of `main()`. Scripts that today read `os.environ["AMMO_*"]` keep doing so; the layer fills the names in.
- `hooks/_find_campaign.sh` is a thin sourced wrapper: `eval "$(ammo_python campaign_paths.py env)"`, plus `ammo_in_campaign <path>` and `ammo_python`. Same contract as `_ammo_is_lead.sh`: takes `$INPUT`, silent, fail-open, `declare -F` guard, never `exit`.
- `codex/hooks/common.py` imports `campaign_paths` by path, the way it already imports `hook_cmd_classify`. Its `active_artifact_dir` becomes a call into the layer.
- `scripts/ammo` is a launcher: `scripts/ammo <script.py> [args]` execs `python_exe()` on the sibling script. Prose says `scripts/ammo new_target.py ...`; hooks use `ammo_python`.
- Layout idioms inside scripts (`parents[3]`, walking up to `.claude/schemas`, `.parent.parent / "data"`) become `plugin_path(...)`.

## 3. Campaign workspace replaces the session

- Engine root: the Pumice checkout. Recorded in `target.json` as `engine.root`; `engine.venv` beside it.
- Campaign dir = `AMMO_ARTIFACT_DIR` = `<engine>/.ammo/<campaign-id>/`. This is what scripts already call `--artifact-dir`. It replaces `kernel_opt_artifacts/<id>/`; that root name becomes one key in `scripts/artifact_layout.json` (`campaign_root`), and every hook or script that matched `kernel_opt_artifacts/*/state.json` uses `in_campaign` instead.
- Campaign id: `new_target.py` mints `YYYYMMDD-HHMMSS-<6 hex>` unless `--campaign-id` is given. It is the value of every `session_id` field and `--session-id` flag; those names do not change.
- Marker `<engine>/.ammo/active`: one line, the campaign id. `new_target.py` writes it and appends `.ammo/` to `<engine>/.git/info/exclude` once. The marker is a pointer, never cleared: liveness comes from `state.json` `campaign.status`, which the stop guard and next-step reminder already read.
- `AMMO_SESSION_ID` readers fall back to the marker id through `apply_env()`.
- Track worktrees: created by the Agent tool under the engine root as today. `worktree-create-with-build.sh` resolves the engine root through the layer.

## 4. Env table (the only list of injected names)

`references/campaign-env.md` ships this table; `tests/test_env_inventory.py` fails on any `AMMO_*` name read in `scripts/`, `hooks/`, or `codex/` that is missing here.

| Name | Derived from | Default |
|---|---|---|
| `AMMO_ARTIFACT_DIR` | marker walk | none; scripts fail closed naming the search, hooks stay silent |
| `AMMO_SESSION_ID` | campaign id | basename of the campaign dir |
| `AMMO_ENGINE_ROOT`, `AMMO_RUNTIME_SOURCE_DIR` (same value) | `target.json engine.root` | parent of `.ammo` |
| `AMMO_PYTHON` | `target.json engine.venv` | `<engine>/.venv/bin/python` |
| `CARGO_TARGET_DIR` | explicit env | `<engine>/target` |
| `AMMO_APPLIED_ENV` | written by `apply_env()` | unset; JSON record of names it set, consumed by `unapply_env()` |
| `AMMO_GPU_RES_DIR` | explicit env | `/tmp/ammo_gpu_res` (host-wide, §5) |
| `AMMO_RUNTIME_PROFILE`, `AMMO_ACCELERATOR_KIND` | `target.json runtime.profile`, `runtime.accelerator_kind` | `pumice-cuda`, `cuda`; any other profile is refused at preflight (§6) |
| `AMMO_TP_SIZE`, `AMMO_DP_SIZE` | `target.json` parallelism | omitted when tp_size is 0 |
| `AMMO_STACK_FINGERPRINT` | computed by `preflight_pumice.py` at scaffold | engine commit, `pumice_rust` build id, torch, triton, flashinfer, driver, GPU name; `image_digest` null |
| `AMMO_INSTANCE_FAMILY`, `AMMO_ACCELERATOR_NAME` | `nvidia-smi` at scaffold | GPU product name |
| `AMMO_SERVING_SETUP`, `AMMO_SERVING_MANIFEST`, `AMMO_SERVING_MANIFEST_SHA256` | `--serving-manifest` flag of `new_target.py` | unset for batch targets |
| `AMMO_PUMICE_SERVER_EXE` | explicit env | `<engine>/target/release/pumice-server` or `rust_grpc_server.py` as the runner decides today |

Worker-2 (wave 0) completes this table from the 58 `AMMO_*` names the source reads; a name no script reads any more is dropped with a note in the table.

## 5. GPU ownership

`gpu_reservation.py` keeps its lease table and CLI. Changes: the default state dir is the host-wide `/tmp/ammo_gpu_res` (the name the hooks already use), replacing the `CUDA_VISIBLE_DEVICES`-hash path; the pool is every physical GPU `nvidia-smi --query-gpu=index --format=csv,noheader` lists (torch fallback); `reserve` filters candidates by the caller's `CUDA_VISIBLE_DEVICES` when set. Leases are owned by `session_id` (the campaign id), so two campaigns cannot hold one physical GPU. No per-GPU flock files: a flock dies with the CLI process, which is why the lease table exists. `/tmp/ammo_gpu_locks` is a retired name the sweep script warns about; do not use it.

## 6. Pins and the CUDA-only gate

- `scripts/pumice_pins.py` is a copy of `S/shared/pumice_pins.py` with `PUMICE_COMMIT = "769c752995b8a39b27df028176411d17c99ff441"` and `REQUIRED_TORCH` read from the line of Pumice `origin/mainline` that `planning-records/ammo/pumice-delta.md` names. The vLLM pin is not a constant: `check_pins()` compares the installed vLLM with `pumice.config.REQUIRED_VLLM_VERSION`, and only when the model-runner kind is `vllm`. `preflight_pumice.py` imports the sibling module; its three-tier pins-file search goes.
- Pin literals (`9d126713`, `0.30.0`, `769c7529`, `0.32.0`, torch versions) may appear only in `pumice_pins.py`, `scripts/pumice_pb/README.md`, `scripts/serving_benchmark_adapter.py` (its WHEEL_BLOBS digest table is keyed by vLLM version; ruled 2026-10-28), and `planning-records/`. References say "the pinned version". `tests/test_prose_bans.py` enforces this.
- Pin-bound data regenerated at the new pin: `hooks/pumice_env_allowlist.txt` (recipe in its header; its count test updates), `serving_benchmark_adapter.py` version table row.
- One gate: `require_supported_profile()` in `runtime_ops.py` (it already refuses unsupported profiles at lines 524-529) is called by `new_target.py` and `preflight_pumice.py`. Neuron branches in scripts stay as they are. The sentence "v1 ships and tests the CUDA path only" appears in `skills/ammo/SKILL.md`, `README.md`, and `references/accelerator-binding.md`, and nowhere else. `tests/conftest.py` defines markers `neuron` and `requires_gpu`; those tests are deselected by default.

## 7. Hooks

- `hooks/hooks.json` is generated by `planning-records/ammo/gen_hooks_json.py` from `SRC/.claude/settings.local.json` plus the keep list; the rewrite is `$CLAUDE_PROJECT_DIR/.claude/hooks/X` to `${CLAUDE_PLUGIN_ROOT}/hooks/X`. `tests/test_hooks_json.py` regenerates and compares. `CLAUDE_PROJECT_DIR` stays legal inside hooks.
- Kept: pretool-guard, skill-files-guard, env-default-guard, worktree-create-with-build, worktree-remove-cleanup, precompact, postcompact, stop-guard, subagent-release, gpu-release, monitor-reminder, audit-start-stamp, state-validate, next-step-reminder, artifact-layout-warn, audit-phase2-guard, inject-audit-phase2, `_ammo_is_lead.sh`. team-spawn-guard stays iff it enforces the team-member-vs-subagent contract of SKILL.md; the hook header records the reason.
- Dropped: venv-python-guard, pip-guard, agent-cwd-guard, msg-mirror, statusline. Dropped 2026-10-29 (prompt audit, user ruling): msg-check and `_ammo_msg_identity.sh`; the channel had no writer once msg-mirror went, and the team inbox is dead on Claude Code 2.1.202 and later. `hooks/README.md` lists every hook with its `intent` from `S/docs/ammo-intent-map.json` and the drop reasons.
- Every hook sources `_find_campaign.sh`; outside a campaign every hook exits 0 with empty stdout (`tests/test_hooks_silent.py`). `WorktreeCreate` cannot decline, so it falls back to a plain worktree add.
- `skill-files-guard` matches one prefix, the plugin root, instead of five `.claude/...` substrings.
- Codex: hook bodies under `codex/hooks/`, the Codex-only scripts (`codex_agent_registry.py`, `monitor_queue_ack.py`, `resolve_codex_transcript.py`, `create_worktree_with_build.sh`, `remove_worktree_cleanup.sh`) under `codex/scripts/`, `sitecustomize.py` under `codex/instrumentation/`, `RUNTIME.md` under `codex/`. Registration in `hooks/hooks.json` when event names match, else `codex/hooks.json` with the difference noted in `hooks/README.md`.

## 8. Text rules (every .md)

- Plain English, ASD-STE100. No em dashes, no "not X but Y".
- "session" becomes "campaign" in prose; `session_id` stays where it names a field or flag.
- Sentences that only make sense with the server go (web UI, S3 pause/resume, cross-pod, ttyd, LIGHTGRID, `/sessions` API). Pause and resume stay as the lead's state-driven resume.
- The lead `SKILL.md` names no Claude or Codex tool by name (vllm-neuron-parity's `test_codex_port.py::test_lead_uses_codex_runtime_binding` is the model). The tool and role mapping lives in `codex/RUNTIME.md` (ported) and one table in `references/harness-binding.md`.

## 9. Acceptance battery (one block, run by every worker and every reviewer)

```
PYTEST="uv run --no-project --with pytest --with pyyaml --with jsonschema --with pydantic --with httpx --with numpy --with psutil python -m pytest"
cd R && $PYTEST tests -q -m "not neuron and not requires_gpu"     # no regression against planning-records/ammo/baseline.md
```

A test that imports from a directory the plugin does not ship (`eval/`, `tests/agents/`) is not shipped either (ruled 2026-10-28).

Structural tests, written in wave 0 and shipped under `R/tests/`:
- `test_path_refs.py`: every `scripts/x`, `references/x`, `schemas/x`, `orchestration/x`, `data/x` token in any `.md`, `hooks.json`, or `.toml` under R resolves to a file.
- `test_env_inventory.py`: every `AMMO_*` name read in `scripts/`, `hooks/`, `codex/` is in `references/campaign-env.md`.
- `test_hooks_silent.py`: every `hooks.json` command, run from a temp dir with no `.ammo/`, exits 0 with empty stdout. Linux only.
- `test_prose_bans.py`: the em-dash ban applies to `.md` files only; the four byte-identical PAVE tool copies (`scripts/validate_pave.py`, `scripts/record_revision.py`, `references/pave.schema.json`, `references/pave-composition.schema.json`) are exempt from every prose ban (ruled 2026-10-28). Otherwise, banned tokens anywhere under R except `hooks/README.md` and `README.md` migration tables: `ttyd`, `LIGHTGRID`, `cross-pod`, `/data/sessions`, `/opt/pumice-venv`, `/opt/neuron-venv`, `/usr/local/share/ammo`, `/app/shared`, `/workspace/`, `kernel_opt_artifacts`, `.claude/skills/ammo`, `.codex/skills/ammo`, `.claude/schemas`, `.claude/agents`, the em dash, and pin literals outside §6's allowed files.
- `test_guidance_semantic_contract.py` (ported): relative links resolve, stage graph and all roles preserved.

Linux-only checks (hook harnesses, `test_hooks_silent.py`) run on `g6e-cdd` inside the warm container: `rsync -a R/ g6e-cdd:~/ammo-plugin-smoke/plugin/ && ssh g6e-cdd docker exec ammo-plugin-smoke bash -lc '<command>'`.

## 10. Sources (read-only for everyone)

- Copy source is the Claude dialect: `SRC/.claude/skills/ammo/{scripts,references,orchestration,data,tests,report}`, `SRC/.claude/agents`, `SRC/.claude/hooks`, `SRC/.claude/schemas`. `S/scripts/render_ammo_variants.py` (`DEST_MAP`, `PER_VARIANT`, `CODEX_RULES`) is the map of what differs per dialect.
- Codex-only: `SRC/.codex/agents/*.toml`, `SRC/.codex/hooks/*.py`, `SRC/.codex/hooks.json`, `SRC/.codex/skills/ammo/{RUNTIME.md,instrumentation,scripts/<Codex-only five>}`, and the six Codex-only tests under `SRC/.codex/skills/ammo/tests`.
- Server-side: `S/shared/pumice_pins.py`, `S/tests/unit/{test_ammo_serving.py,test_ammo_serving_campaign.py,test_ammo_serving_runtime.py,test_pumice_pins.py,test_codex_hook_campaign_resolution.py,test_env_default_guard_hook.py,test_worktree_hook.py}`, `S/docs/ammo-intent-map.json`.
- Generic PAVE tooling, byte-identical in pave-init and vllm-neuron-parity, copied with `cp`: `validate_pave.py`, `record_revision.py`, `pave.schema.json`, `pave-composition.schema.json`; adapted: `codex/install_agents.py`, `tests/test_workflow_pave.py`, `tests/test_codex_port.py`, `README.md` sections 1-6.
- Pumice mainline: `/Users/jinhun/AWSMantle/src/AWSMantlePumice`, ref `origin/mainline` = `769c7529`; read with `git show origin/mainline:<path>` or a throwaway worktree under `/tmp`, never by changing that checkout's branch.
