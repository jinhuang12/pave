# Campaign environment names

This file is the only list of the environment names that AMMO reads. `scripts/campaign_paths.py` derives the names in section 1 from the campaign workspace. `tests/test_env_inventory.py` fails when a script, hook, or Codex file reads an `AMMO_*` name that has no row here.

How the names get set:

1. A script calls `apply_env()` at the top of `main()`. A hook sources `hooks/_find_campaign.sh` and calls `_find_campaign "$INPUT"`.
2. The layer finds the campaign: `AMMO_ARTIFACT_DIR` when it is set, else the first `<dir>/.ammo/active` marker found on the walk up from the start directory. The campaign dir is `<dir>/.ammo/<campaign-id>/`.
3. The layer reads `target.json` in the campaign dir and sets each section 1 name that is not set yet. A name that you set yourself keeps your value. An empty value counts as not set.

Outside a campaign the layer sets nothing. Scripts then stop and name the search. Hooks exit 0 and print nothing.

## 1. Injected names

`campaign_env()` returns exactly these names. A name in the "Omitted when" column is left out under that condition.

| Name | Derived from | Default | Omitted when |
|---|---|---|---|
| `AMMO_ARTIFACT_DIR` | the marker walk | none | never |
| `AMMO_SESSION_ID` | the campaign id | basename of the campaign dir | never |
| `AMMO_ENGINE_ROOT` | `target.json` `engine.root` | parent of `.ammo` | never |
| `AMMO_RUNTIME_SOURCE_DIR` | same value as `AMMO_ENGINE_ROOT` | parent of `.ammo` | never |
| `AMMO_PYTHON` | `target.json` `engine.venv` (a venv dir or a python file) | `<engine>/.venv/bin/python` | never |
| `CARGO_TARGET_DIR` | your environment | `<engine>/target` | never |
| `AMMO_GPU_RES_DIR` | your environment | `/tmp/ammo_gpu_res` (one lease table for the host) | never |
| `AMMO_RUNTIME_PROFILE` | `target.json` `runtime.profile` | `pumice-cuda` | never |
| `AMMO_ACCELERATOR_KIND` | `target.json` `runtime.accelerator_kind` | `cuda` | never |
| `AMMO_TP_SIZE` | `target.json` `target.tp` | none | `tp` is 0, missing, or not an integer |
| `AMMO_DP_SIZE` | `target.json` `target.dp`, else `state.json` `target.dp` | `1` | `AMMO_TP_SIZE` is omitted |
| `AMMO_STACK_FINGERPRINT` | `target.json` `runtime.stack_fingerprint`, written by `preflight_pumice.py` at scaffold, as compact JSON | none | the key is missing |
| `AMMO_INSTANCE_FAMILY` | `target.json` `runtime.instance_family`, else `runtime.accelerator_name` | none | both keys are missing |
| `AMMO_ACCELERATOR_NAME` | `target.json` `runtime.accelerator_name` (the GPU product name from `nvidia-smi` at scaffold) | none | the key is missing |
| `AMMO_SERVING_SETUP` | `pd` when `target.json` has `serving_manifest` | none | batch target |
| `AMMO_SERVING_MANIFEST` | `target.json` `serving_manifest.path`, from the `--serving-manifest` flag of `new_target.py` | none | batch target |
| `AMMO_SERVING_MANIFEST_SHA256` | `target.json` `serving_manifest.sha256` | none | batch target |

The CUDA path is the only profile that preflight accepts. Preflight refuses any other `AMMO_RUNTIME_PROFILE`.

## 2. Read, never injected

The layer does not set these names. You set them, or a script sets them for a child process that it starts.

| Name | Read by | Purpose |
|---|---|---|
| `AMMO_PUMICE_SERVER_EXE` | `preflight_pumice.py`, the gRPC bench runner | Server launcher to use. When it is not set, the runner picks `rust_grpc_server.py` or `pumice-server` as before. |
| `AMMO_PUMICE_PINS_FILE` | `preflight_pumice.py` | Path to a pins file. Wave 1 removes this search (conventions §6). Then move this row to section 4. |
| `AMMO_RUNTIME_SOURCE_COMMIT` | `runtime_ops.py`, report and gate scripts | Neuron plugin source commit. Not set on the CUDA path. |
| `AMMO_STACK_IMAGE_DIGEST` | `runtime_ops.py`, report and gate scripts | Container image digest. A host-local campaign has no image, so it stays unset and the fingerprint records `image_digest` as null. |
| `AMMO_NEURON_LNC` | `runtime_ops.py`, `deployment_ops.py` | Neuron logical core setting. Neuron only. |
| `AMMO_SERVING_CONTEXT` | `serving_contract.py`, `deployment_ops.py` | Input JSON path. `run_serving_sweep.py` sets it for each collector child. |
| `AMMO_SERVING_OUTPUT` | `serving_contract.py` | Result JSON path. `run_serving_sweep.py` sets it for each collector child. |
| `AMMO_SERVING_ROLE` | `serving_contract.py` | Worker role. `deployment_ops.py` sets it for each serving worker. |
| `AMMO_SERVING_RUN_ID` | `serving_contract.py` | Serving run id. `deployment_ops.py` sets it for each serving worker. |
| `AMMO_MODEL_LOAD_BINDING` | `run_vllm_bench_latency_sweep.py` | Model load binding. The sweep sets it for its own child. |
| `AMMO_LAYOUT_MANIFEST` | `ammo-artifact-layout-warn.sh` | Layout manifest path. The hook sets it for its inline Python. |
| `AMMO_REMINDER_STATE_DIR` | `ammo-next-step-reminder.sh`, `ammo-state-validate.sh`, Codex post-tool guard | Root of the previous-state snapshots. Default `/tmp`. |
| `AMMO_MSG_MIRROR_DIR` | `ammo-msg-check.sh`, `_ammo_msg_identity.sh` | Root of the message mirror channel. Default `/tmp/ammo-msg-mirror-<uid>`. |
| `AMMO_MSG_MIRROR_MAX_AGE_S` | `ammo-msg-check.sh` | Maximum age of a mirrored message in seconds. Default 7200. |
| `AMMO_MSG_CHECK_DEBUG` | `ammo-msg-check.sh` | Debug log switch. |
| `AMMO_VALIDATE_FAIL_OPEN` | `ammo_state.py`, `ammo-state-validate.sh`, Codex post-tool guard | Set to 1 to skip schema validation when `jsonschema` is missing. |
| `AMMO_ALLOW_PIP` | Codex pre-tool guard | Set to 1 to allow a pip install. |
| `AMMO_ALLOW_STOP` | Codex stop gate | Set to 1 to allow a stop while a gate is open. |
| `AMMO_ALLOW_UNRESERVED_GPU` | Codex pre-tool guard | Set to 1 to run a GPU command without a reservation. |
| `AMMO_ALLOW_ANCHOR_MUTATION` | Codex pre-tool guard | Set to 1 to change a frozen anchor during a validated recovery. |
| `AMMO_ALLOW_NONPARITY` | Codex pre-tool guard | Set to 1 to allow eager mode or disabled graphs on the CUDA path. |
| `AMMO_MONITOR_ALLOW_UNACKED` | Codex pre-tool guard | Set to 1 to continue with an unacknowledged monitor message. |
| `AMMO_MONITOR_QUEUE` | Codex hooks, `monitor_queue_ack.py` | Path of the monitor queue. |
| `AMMO_DEBUG_HOOKS` | Codex post-tool guard | Set to 1 for hook debug output. |
| `AMMO_AUDIT_PHASE2_DIR` | Codex post-tool guard | Sentinel dir for audit phase 2. Default `/tmp/ammo_codex_audit_phase2`. |
| `AMMO_STATE_ENGINE` | Codex post-tool guard | Path of `ammo_state.py` to use for state checks. |
| `AMMO_PROJECT_DIR` | Codex post-tool guard | Project root when `CODEX_PROJECT_DIR` is not set. |
| `AMMO_CODEX_AGENT_MODE` | Codex hooks | Set to `v1` to select the Codex v1 agent mode. |
| `AMMO_NSYS_CAPTURE_IDENTITIES_JSON` | `codex/instrumentation/sitecustomize.py` | JSON list of capture identities to put in the NVTX ranges of an Nsight child. No file in this plugin sets it. |
| `AMMO_CODEX_BEDROCK_REGION` | the ammo-server Codex runtime only | Bedrock login region. `codex/RUNTIME.md` names it, and no plugin file reads it. Move this row to section 4 when that text goes. |

## 3. Not environment names

The inventory grep also finds these tokens. They are not read from the environment.

| Name | Where | What it is |
|---|---|---|
| `AMMO_MSG_NS` | `_ammo_msg_identity.sh` | Shell variable: message namespace, the lead session id. |
| `AMMO_MSG_OWN_SID` | `_ammo_msg_identity.sh` | Shell variable: the session id of this hook process. |
| `AMMO_MSG_IS_LEAD` | `_ammo_msg_identity.sh` | Shell variable: 1 for the lead. |
| `AMMO_MSG_INPROC` | `_ammo_msg_identity.sh` | Shell variable: 1 inside an in-process subagent. |
| `AMMO_MSG_ID_SOURCE` | `_ammo_msg_identity.sh` | Shell variable: which identity rule decided. |
| `AMMO_MSG_HOOK_AGENT_ID` | `_ammo_msg_identity.sh` | Shell variable: `agent_id` from the hook input. |
| `AMMO_MSG_ARGV_AGENT_ID` | `_ammo_msg_identity.sh` | Shell variable: `--agent-id` from the process arguments. |
| `AMMO_MSG_ARGV_AGENT_TYPE` | `_ammo_msg_identity.sh` | Shell variable: `--agent-type` from the process arguments. |
| `AMMO_MSG_BSD` | `_ammo_msg_identity.sh` | Part of the private shell variable `_AMMO_MSG_BSD`. |
| `AMMO_MSG_HELPER_DIR` | `_ammo_msg_identity.sh` | Part of the private shell variable `_AMMO_MSG_HELPER_DIR`. |
| `AMMO_TRACK_LOCAL_RUNTIME_PACKAGES` | `worktree-create-with-build.sh` | Bash array of packages to stage in a track worktree. |
| `AMMO_TRACK_LOCAL_RUNTIME_EXTRA_GLOBS` | `worktree-create-with-build.sh` | Bash array of extra file patterns to stage. |
| `AMMO_CAPTURE_V2` | `run_pumice_grpc_bench.py` | Schema id inside capture JSON. |
| `AMMO_CAPTURE_V1` | `codex/instrumentation/sitecustomize.py` | Prefix of a capture identity string. |
| `AMMO_EXPORT` | `codex/RUNTIME.md` | Placeholder name for an export dir in the install steps. |
| `AMMO_NORMALIZED_INTERVALS` | `mine_trace.py` | Input format label. |
| `AMMO_TASK` | `codex_agent_registry.py` | Metadata line in a Codex spawn message. |
| `AMMO_TEST_GUARD` | `hooks/tests/test-ammo-audit-phase2.sh` | Test harness variable. |
| `AMMO_TEST_HOOK` | `hooks/tests/test-*.sh` | Test harness variable: the hook under test. |
| `AMMO_TEST_INJECT` | `hooks/tests/test-ammo-audit-phase2.sh` | Test harness variable. |
| `AMMO_TEST_INPUT` | `hooks/tests/test-ammo-is-lead.sh` | Test harness variable. |

## 4. Dropped

No name is dropped yet. When a change removes the last reader of a name, move its row to a table in this section and give the reason. `tests/test_env_inventory.py` fails when a script still reads a name listed here.
