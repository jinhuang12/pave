# Smoke run on g6e-cdd - the ammo plugin from a plain Pumice checkout

DRAFT - in progress. Steps 1 and 2 are recorded. Step 3 is running.

## Environment used for every step

All commands ran inside the container `ammo-plugin-smoke` on g6e-cdd, as root, with the plugin bind-mounted read-only at `/mnt/ammo` (rsync of `generated-plugins/ammo/` at 22:53 UTC).

The image injects names that a plain GPU host does not have. Every step ran through `/tmp/w11/run.sh`, which sources `/tmp/w11/cleanenv.sh`:

```bash
unset PYTHONPATH AMMO_PUMICE_PD_SETUP AMMO_FORK_TOKEN_KEY AMMO_RUNTIME_PROFILE AMMO_ACCELERATOR_KIND
export IS_SANDBOX=1
```

Names left in place (set by `docker run`, host-prep section 1):

```
ANTHROPIC_DEFAULT_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0
ANTHROPIC_DEFAULT_OPUS_MODEL=global.anthropic.claude-opus-5-5[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic.claude-sonnet-5
AWS_REGION=eu-west-1
CLAUDE_CODE_USE_BEDROCK=1
CLAUDE_CODE_USE_MANTLE=1
IS_SANDBOX=1
```

## Step 1. Clean-room load

`/root/.claude` and `/root/.claude.json` before the step:

```
ls: cannot access '/root/.claude': No such file or directory
ls: cannot access '/root/.claude.json': No such file or directory
```

Validate:

```
$ cd /tmp && claude plugin validate /mnt/ammo < /dev/null; echo EXIT=$?
Validating plugin manifest: /mnt/ammo/.claude-plugin/plugin.json

⚠ Found 1 warning:

  ❯ compatibility: Unknown field 'compatibility' (commonly seen in an MCPB/DXT manifest). Claude Code ignores unrecognized fields at load time, so it's safe to keep.

✔ Validation passed with warnings
EXIT=0
```

Session with the plugin, from an empty dir outside any campaign:

```
$ cd /tmp/w11/cr && claude --plugin-dir /mnt/ammo --dangerously-skip-permissions --model haiku \
    -p "Reply with the single word OK" --output-format stream-json --verbose < /dev/null > /tmp/w11/cleanroom-init.jsonl
EXIT=0
```

The `init` event of that session (filtered to the ammo names with `jq`):

```json
{"model":"global.anthropic.claude-haiku-4-5-20251001-v1:0","cwd":"/tmp/w11/cr",
 "plugins":[{"name":"ammo","path":"/mnt/ammo","source":"ammo@inline","version":"1.0.0"},{"name":"agents-md","path":"builtin","source":"agents-md@builtin"}],
 "skills":["ammo:ammo","ammo:ammo-report"],
 "slash":["ammo:ammo","ammo:ammo-report"],
 "agents":["ammo:ammo-auditor","ammo:ammo-champion","ammo:ammo-delegate","ammo:ammo-impl-champion","ammo:ammo-investigator","ammo:ammo-report-writer","ammo:ammo-researcher","ammo:ammo-resolver","ammo:ammo-transcript-monitor"],
 "claude_code_version":"2.1.281","permissionMode":"bypassPermissions"}
{"subtype":"success","is_error":false,"result":"OK","num_turns":1,"duration_ms":2234}
{"subtype":"hook_response","hook_name":"SessionStart:startup","hook_event":"SessionStart","exit_code":0,"stdout":""}
```

The plugin's SessionStart hook ran outside a campaign and printed nothing.

`/root/.claude` after the step:

```
/root/.claude
/root/.claude.json
/root/.claude/backups
/root/.claude/backups/.claude.json.backup.1793228101043
/root/.claude/plugins
/root/.claude/plugins/data
/root/.claude/plugins/data/ammo-inline
/root/.claude/projects
/root/.claude/projects/-tmp-w11-cr
/root/.claude/projects/-tmp-w11-cr/6df17497-1afa-44eb-bc51-25f11475ff49.jsonl
/root/.claude/projects/-tmp-w11-cr/memory
/root/.claude/session-env
/root/.claude/session-env/6df17497-1afa-44eb-bc51-25f11475ff49
/root/.claude/sessions
/root/.claude.json keys: firstStartTime firstStartVersion hasResetAutoModeOptInForDefaultOffer machineID migrationVersion opusProMigrationComplete pluginUsage seenNotifications sonnet1m45MigrationComplete userID
```

Wall clock: 22:53 to 22:55, about 2 min.

## Step 2. Fail-closed on a bad engine venv

```
$ python3 -m venv /tmp/badvenv
$ /tmp/badvenv/bin/python -c "import pumice"      -> ModuleNotFoundError: No module named 'pumice'
$ /tmp/badvenv/bin/python -c "import jsonschema"  -> ModuleNotFoundError: No module named 'jsonschema'
$ cd /workspace/pumice
$ AMMO_PYTHON=/tmp/badvenv/bin/python /mnt/ammo/scripts/ammo new_target.py --model-id /hf/gpt-oss-20b --hardware L40S --dtype bf16 --tp 1 --data-parallel-size 1 --prompt-source raw
usage: new_target.py [-h] [--artifact-dir ARTIFACT_DIR]
                     ...
new_target.py: error: cannot validate generated state against schema: No module named 'jsonschema'
scaffold-exit=2
$ ls -la /workspace/pumice/.ammo
ls: cannot access '/workspace/pumice/.ammo': No such file or directory
```

Preflight alone, same venv, no campaign:

```
$ AMMO_PYTHON=/tmp/badvenv/bin/python /mnt/ammo/scripts/ammo preflight_pumice.py --engine-root /workspace/pumice --model-path /hf/gpt-oss-20b
FAIL pumice import: cannot import pumice: No module named 'pumice'
FAIL pumice_rust import: cannot import pumice_rust, the cargo-built engine extension: No module named 'pumice_rust'; build and stage it into the engine venv
FAIL pumice pins: torch is not installed; runner kind unknown (confdb lookup failed: No module named 'pumice'); checked vLLM as well
FAIL proto stubs: cannot import pumice_pb: No module named 'google'

4 preflight check(s) failed.
preflight-exit=1
```

Which pythons hold `jsonschema` in this image:

```
/opt/pumice-venv/bin/python 3.12.12 4.26.0
/usr/bin/python3 ModuleNotFoundError: No module named 'jsonschema'
/usr/bin/python3.12 ModuleNotFoundError: No module named 'jsonschema'
```

Wall clock: 22:56, under 1 min.

### Step 2 redo after the fix (variants A and B)

Mirror state: the fixed `new_target.py` and `preflight_pumice.py` were already on g6e-cdd at 23:04 (same md5 as R: `bc07abda...` and `0a228c98...`). Both variants ran from a throwaway clone, so the real campaign in `/workspace/pumice/.ammo` was not touched:

```
$ git clone -q /workspace/pumice /tmp/engine-check     -> clone-exit=0
$ git -C /tmp/engine-check rev-parse HEAD              -> 769c752995b8a39b27df028176411d17c99ff441
```

Variant A, the plain venv `/tmp/badvenv` (no pumice, no plugin packages), 23:11 UTC:

```
$ cd /tmp/engine-check
$ AMMO_PYTHON=/tmp/badvenv/bin/python /mnt/ammo/scripts/ammo new_target.py --model-id /hf/gpt-oss-20b --hardware L40S --dtype mxfp4 --tp 1 --data-parallel-size 1 --prompt-source raw
usage: new_target.py [-h] [--artifact-dir ARTIFACT_DIR]
                     ... (usage block trimmed)
new_target.py: error: engine python /tmp/badvenv/bin/python lacks 'jsonschema'; the plugin needs these packages in the engine venv: httpx, jsonschema, psutil, pydantic, pyyaml; run `scripts/ammo preflight_pumice.py --engine-root /tmp/engine-check` for the full check
scaffold-exit=2
$ ls -la /tmp/engine-check/.ammo
ls: cannot access '/tmp/engine-check/.ammo': No such file or directory

$ AMMO_PYTHON=/tmp/badvenv/bin/python /mnt/ammo/scripts/ammo preflight_pumice.py --engine-root /tmp/engine-check --model-path /hf/gpt-oss-20b
FAIL pumice import: cannot import pumice: No module named 'pumice'
FAIL pumice_rust import: cannot import pumice_rust, the cargo-built engine extension: No module named 'pumice_rust'; build and stage it into the engine venv
FAIL plugin python deps: the engine python /tmp/badvenv/bin/python cannot import httpx, jsonschema, psutil, pydantic, yaml (httpx: No module named 'httpx'; jsonschema: No module named 'jsonschema'; psutil: No module named 'psutil'; pydantic: No module named 'pydantic'; yaml: No module named 'yaml'); install with `/tmp/badvenv/bin/python -m pip install httpx jsonschema psutil pydantic pyyaml` (a uv venv without pip: `uv pip install --python /tmp/badvenv/bin/python httpx jsonschema psutil pydantic pyyaml`)
FAIL pumice pins: torch is not installed; runner kind unknown (confdb lookup failed: No module named 'pumice'); checked vLLM as well
FAIL proto stubs: cannot import pumice_pb: No module named 'google'

5 preflight check(s) failed.
preflight-exit=1
```

Variant B, `/tmp/badvenv2` = a plain venv plus the five packages the message lists, 23:12 UTC. The plain venv has pip, so the install used pip (with `UV_CONSTRAINT` unset, host-prep trap 1):

```
$ python3 -m venv /tmp/badvenv2
$ env -u UV_CONSTRAINT -u PIP_CONSTRAINT /tmp/badvenv2/bin/python -m pip install -q httpx jsonschema psutil pydantic pyyaml   -> pip-exit=0
$ /tmp/badvenv2/bin/python -c "import httpx, jsonschema, psutil, pydantic, yaml; print('deps ok')"   -> deps ok
$ /tmp/badvenv2/bin/python -c "import pumice"   -> ModuleNotFoundError: No module named 'pumice'

$ cd /tmp/engine-check
$ AMMO_PYTHON=/tmp/badvenv2/bin/python /mnt/ammo/scripts/ammo new_target.py --model-id /hf/gpt-oss-20b --hardware L40S --dtype mxfp4 --tp 1 --data-parallel-size 1 --prompt-source raw
Initialized campaign 20261028-231202-ad4e55: /tmp/engine-check/.ammo/20261028-231202-ad4e55
Created: state.json, target.json, rounds/1/ scaffold
Marker: /tmp/engine-check/.ammo/active
Next: write rounds/1/constraints.md (Phase 1)
scaffold-exit=0
$ cat /tmp/engine-check/.ammo/active                         -> 20261028-231202-ad4e55
$ grep -n ammo /tmp/engine-check/.git/info/exclude           -> 7:.ammo/

$ /mnt/ammo/scripts/ammo preflight_pumice.py --model-path /hf/gpt-oss-20b    # inside the campaign: the launcher picks engine.venv
FAIL pumice import: cannot import pumice: No module named 'pumice'
FAIL pumice_rust import: cannot import pumice_rust, the cargo-built engine extension: No module named 'pumice_rust'; build and stage it into the engine venv
FAIL pumice pins: torch is not installed; runner kind unknown (confdb lookup failed: No module named 'pumice'); checked vLLM as well
FAIL proto stubs: cannot import pumice_pb: No module named 'google'

4 preflight check(s) failed.
preflight-exit=1
```

State after that preflight (`jq` filter over `state.json` and `target.json`):

```json
{"session_id":"20261028-231202-ad4e55","status":"active","stage":"1_baseline","round":1,
 "rounds0":{"round_id":1,"baseline_started":null,"audit":{}}}
{"engine":{"root":"/tmp/engine-check","venv":"/tmp/badvenv2/bin/python"},
 "runtime":{"profile":"pumice-cuda","accelerator_kind":"cuda","accelerator_name":"NVIDIA L40S",
  "fp":{"flashinfer":null,"gpu_name":"NVIDIA L40S","image_digest":null,"nvidia_driver":"595.91.07",
        "pumice_commit":"769c752995b8a39b27df028176411d17c99ff441","pumice_rust_build_id":null,
        "torch":null,"triton":null,"vllm":null}}}
```

The state schema has no preflight stage. The scaffold writes `campaign.status = active` and `current_stage = 1_baseline` as the start state, and preflight runs before any Stage 1 work. These values are the scaffold's own: `baseline.started_at` is null and `audit` is empty, so nothing moved past preflight.

Wall clock: 23:11 to 23:12, about 1 min for both variants.
