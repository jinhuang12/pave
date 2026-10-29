# Smoke run on g6e-cdd - the ammo plugin from a plain Pumice checkout

## Answer

The plugin ran a real AMMO campaign on one L40S GPU from a plain Pumice checkout, with no server. Stage 1 finished and its baseline is valid. The Stage 1 audit did not pass. It gave the verdict BLOCKED two times.

The cause of the second BLOCKED is a defect that AMMO had before the port. The mining script and its reference disagree on one denominator. On this GPU, that makes the Stage 2 tool fail at batch sizes 1 and 8. No agent inside the campaign can fix it, so the campaign lead paused the campaign through the plugin's own escalation path.

Next action: the user decides how to fix the mining-script denominator. After that, a third audit can run.

| Check | Result |
|---|---|
| Plugin validates and loads | Pass, with 1 warning |
| Preflight stops on a bad engine venv | Pass, both variants |
| Stage 1 baseline on gpt-oss-20b | Pass, engine commit matches |
| Stage 1 audit passed | No: BLOCKED after 2 cycles |
| Hooks fire in a real session | Pass |
| GPU leases free at the end | Pass |
| Container still running | Pass |

What the run found in the plugin:
- 5 defects were fixed during the run, with tests. The state check hook now sees writes made through a shell variable.
- 4 more are being fixed now. The most important: in headless mode, a background auditor is stopped after 10 min.
- 2 small defects stay open. AMMO had both before the port.
- 2 more defects came from AMMO before the port. Both affect Stage 2. They are recorded and not fixed here.

The full evidence follows below.

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

## Step 3. Real run, Stage 1 and T_AUDIT_S1

### 3.1 How the campaign was driven

Driver: `claude -p` (headless). No interactive tmux session was used. A one-shot role runs in the foreground of the lead's turn, so `-p` can drive Stage 1. The lead scaffolded the campaign itself from the `/ammo:ammo` flags. That tests the Invocation path of SKILL.md end to end; the subtask's order (worker scaffolds, then starts the lead) would have tested the resume path only. The resume path got its own test in session 2.

Session 1 launcher (`/tmp/w11/launch.sh` in the container):

```bash
source /tmp/w11/cleanenv.sh
export AMMO_PYTHON=/opt/pumice-venv/bin/python     # the engine venv is not <engine>/.venv on this image
cd /workspace/pumice
claude --plugin-dir /mnt/ammo --dangerously-skip-permissions --model opus \
  --output-format stream-json --verbose \
  -p "/ammo:ammo --model-id /hf/gpt-oss-20b --hardware L40S --dtype mxfp4 --tp 1 --data-parallel-size 1. This is a smoke run: run the campaign through Stage 1 and T_AUDIT_S1, then pause the campaign once T_AUDIT_S1 has passed. I will resume Stage 2 later." \
  < /dev/null > /tmp/w11/campaign.jsonl 2> /tmp/w11/campaign.err
```

Session 2 launcher (`/tmp/w11/launch2.sh`): the same, plus `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, `--include-hook-events`, and the prompt `/ammo:ammo This is a smoke run. Resume the active campaign. Run T_AUDIT_S1, then pause the campaign once T_AUDIT_S1 has passed. I will resume Stage 2 later.`

GPU state before the run (22:57 UTC):

```
host:      0, 109 MiB, 0 %   1, 1 MiB, 0 %   2, 1 MiB, 0 %   3, 1 MiB, 0 %
container: 0, NVIDIA L40S, 1 MiB, 46068 MiB, 0 %
           1, NVIDIA L40S, 1 MiB, 46068 MiB, 0 %
compute apps: none
$ /mnt/ammo/scripts/ammo gpu_reservation.py status --table
GPU   Status     Command        Since    Lease Expires   Session
0     FREE       -              -        -               -
1     FREE       -              -        -               -
```

### 3.2 Timeline

| UTC | What happened | Evidence |
|---|---|---|
| 22:58:30 | Session 1 starts | `/tmp/w11/campaign.start` |
| 22:58:43 | Lead scaffolds campaign `20261028-225843-229b3b`, adds `--prompt-source raw` on its own (gpt-oss chat template needs 67 tokens, input_len is 64) | stream: `new_target.py ... --prompt-source raw` |
| 22:59 | Lead runs preflight: exit 0 | stream: `preflight_pumice.py --model-path /hf/gpt-oss-20b ... exit=0` |
| 23:00 | Lead dispatches `ammo:ammo-researcher`, `task_type: baseline`, foreground | stream: `TOOL Agent ... "run_in_background":false` |
| 23:02 | Researcher reserves GPU 0 (4 h lease, own id) and starts the baseline sweep with golden capture | lease table: `0 HELD stage1 baseline+profiling sweeps 2026-10-28 23:02` |
| 23:04 to 23:16 | GSM8K golden capture, 1319 questions, 744 s, accuracy 0.4223 | sweep log |
| 23:16 to 23:20 | Timing waves BS 1, 8, 32; `e2e_latency_results.json` written 23:19:53 | sweep log |
| 23:21 to 23:28 | Separate Nsys capture (`--slot profiling --nsys-profile --nsys-mode node`) | `sweeps/profiling/`, `profiling/nsys/baseline.nsys-rep` |
| 23:28 | Researcher stops its own leftover `nsys --start-agent` process (pid it started) and exports the SQLite | researcher transcript |
| 23:37:54 to 23:38:16 | Researcher releases its lease (`release-session`), then returns | lease table FREE; `release.log` |
| 23:38:33 | Lead ingests the baseline into `state.json`, sets `baseline.completed_at` | state |
| 23:38:39 | Lead spawns `ammo:ammo-auditor` with `run_in_background: true` and ends its turn | stream |
| 23:48:46 | Claude Code ends the `-p` session: "Background tasks still running after 600s; terminating." The auditor and its delegates are killed before a verdict | `/tmp/w11/campaign.err`; `audits/` empty |
| 23:50:44 | Session 2 starts with `/ammo:ammo` (resume) | `/tmp/w11/campaign2.start` |
| 23:51:06 | Lead reads the marker and state, spawns a fresh auditor, foreground | stream |
| 00:26 | Auditor writes Phase 1 to `rounds/1/audits/stage_1.md`; the Phase 2 hook delivers the checklist | auditor transcript |
| 00:46 | Auditor returns **Overall: BLOCKED** (1 BLOCKING, 1 HIGH, 10 LOW) | `audits/stage_1.md` |
| 00:47:26 | Lead dispatches a fresh researcher (`task_type: baseline`, attempt 2) to repair BLOCKING-1 and HIGH-1, foreground | transcript `b8f87a37...jsonl` |
| 00:47 to 01:10 | Researcher runs a control launch with no golden phase (one GPU lease, 00:59:49 to 01:04:47, released by the researcher) and cuts per-BS slices out of the one Nsys report with `nsys export --times`; a `mine_trace.py` dry run gives FATAL `decode_busy=1.0023` at BS1 | researcher hand-back; `sweeps/baseline_control/`; `profiling/nsys/baseline_bs{1,8,32}.sqlite` |
| 01:10:57 | Researcher returns; SubagentStop release hook fires | `release.log` |
| 01:11:14 | Lead dispatches `ammo:ammo-investigator` (`mode: decision_support`) on the two open forks | transcript |
| 01:17:49 | Investigator returns: keep the Stage 1 baseline as comparator (BS1 control delta -0.014%), class the BS1 FATAL as a Stage 2 toolkit concern | investigator hand-back |
| 01:18 | Lead writes `blockers/stage2_mine_trace_decode_busy_denominator.md` | campaign file |
| 01:18:11 | Lead dispatches a second researcher repair pass (attempt 3): drop the partial final step from each slice, write the HIGH-1 closure into `constraints.md` | transcript |
| 01:38:12 | Researcher returns, no GPU work in that pass | researcher hand-back; `release.log` |
| 01:38:17 | Lead dispatches `ammo:ammo-auditor` for T_AUDIT_S1 cycle 2, foreground | transcript |
| 02:03:15 | Auditor writes Phase 1 to `rounds/1/audits/stage_1_cycle_2.md`: preliminary BLOCKED (BLOCKING-1R, 0 HIGH, 7 new LOW); the Phase 2 hook delivers the checklist | auditor transcript; audit file |
| 02:13 | Auditor writes **Overall: BLOCKED** (`stage_1_cycle_2.md:518`) and returns | audit file |
| 02:13:48 | `ammo-audit-start-stamp.sh` stamps `audit.stage_1 = {started_at: 02:13:48, cycle: 2}` (return time, finding 7) | state |
| 02:14:08 | Lead writes a campaign-scoped `campaign.auditor_escalation` with a resumable task (`orchestration/audit-protocol.md:166`) | lead Bash call |
| 02:14:13 | Lead pauses: `ammo_state.py set --state $S --field campaign.status --value '"paused"' && ammo_state.py validate --state $S` | lead Bash call; `validate` PASS |
| 02:14:15 | `ammo-state-validate.sh` validates that write (path through `$S`) and writes its snapshot `/tmp/ammo-state-prev-b8f87a37-....json` | file mtime |
| 02:14:27 | Session 2 ends, `exit=0`, 18 turns, 2 h 24 min, USD 55.29 (`result` event) | `/tmp/w11/campaign2.exit` |

### 3.3 Plugin mirror changes during the run

The plugin under `/mnt/ammo` changed while the campaign ran. Other workers synced it from R during the run; I synced it once at the end. `stat -c %z` (ctime, UTC) of the changed files:

```
/mnt/ammo/scripts/new_target.py, preflight_pumice.py      before 23:04 (the step 2 redo used them)
/mnt/ammo/hooks/ammo-state-validate.sh                     2026-10-29 00:01:47
/mnt/ammo/scripts/artifact_layout.json                     2026-10-29 00:01:47
/mnt/ammo/hooks/ammo-artifact-layout-warn.sh               2026-10-29 00:07:39, then 02:15:02 (7fcb0fe)
```

team-lead reported one more resync at about 00:20, with no content change to these files. Time boundaries for the evidence:
- Before 00:01:47: session 1 and the first 11 min of session 2 ran the hooks from before 276aa6f.
- 00:01:47 (state-validate) or 00:07:39 (layout warn) to 02:15: 276aa6f hooks. The cycle 1 audit, both repairs, the cycle 2 audit and the lead's pause ran on them.
- From 02:15:02: the final mirror. The post-fix hook session and the replays in section 4 ran on it.

Final sync, from the Mac at 02:15:02 UTC:

```
$ rsync -a --delete --itemize-changes generated-plugins/ammo/ g6e-cdd:~/ammo-plugin-smoke/plugin/   (pycache lines removed)
<f.st.... codex/hooks/common.py
<f.st.... codex/hooks/post_tool_use_guard.py
<f.st.... hooks/ammo-artifact-layout-warn.sh
<f.st.... hooks/tests/test-ammo-artifact-layout-warn.sh
$ rsync -an --delete --itemize-changes ... | grep -v '^\.d' | wc -l     -> 0
R and mirror md5: ammo-artifact-layout-warn.sh e9747dd9...  ammo-state-validate.sh 91cf1deb...  (equal)
```

The two Codex files were behind on the mirror since 276aa6f. Claude Code does not run them.

### 3.4 Roles and models

| Role | Model alias | Model that ran | Calls |
|---|---|---|---|
| lead (both sessions) | `--model opus` | `global.anthropic.claude-opus-5-5[1m]` | 2 sessions |
| `ammo:ammo-researcher` | `opus` | `claude-opus-5-5` | 3 |
| `ammo:ammo-auditor` | `opus` | `claude-opus-5-5` | 3: one killed in session 1, two gave a verdict |
| `ammo:ammo-investigator` | `opus` | `claude-opus-5-5` | 1 |
| `ammo:ammo-delegate` | `sonnet` | `claude-sonnet-5` | 31 |
| clean-room session (step 1) | `--model haiku` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | 1 |
| post-fix hook session (section 4.2) | `--model sonnet` | `anthropic.claude-sonnet-5` | 1 |

Source: `jq '.message.model'` over each subagent transcript under `/root/.claude/projects/-workspace-pumice/<sid>/subagents/`, grouped by `agentType` in the `.meta.json` beside it. Claude Code 2.1.281, `permissionMode: bypassPermissions`, Bedrock aliases from section "Environment".

### 3.5 Final state at 02:17 UTC

Collected with `/tmp/w11/final.sh` (read-only) after the pause, the final sync and the post-fix session:

```
== marker
20261028-225843-229b3b
== state (jq filter)
{"status":"paused","stage":"1_baseline","round":1,
 "esc":{"stage":"stage_1","round":1,"scope":"campaign","reason":"T_AUDIT_S1 BLOCKED after 2 cycles (BLOCKING-1R): mine_trace.py:532-533 divides profiled kernel busy by the clean sweep decode wall, while references/e2e-delta-math.md:43 requires the same-trace wall; ... decode_busy exceeds DECODE_BUSY_MAX=1.0 at BS1 (1.0023) and BS8 (1.0158). The plugin tree /mnt/ammo is read-only, so no in-campaign agent can resolve it; this needs user/toolkit authority.",
        "verdict_files":["rounds/1/audits/stage_1.md","rounds/1/audits/stage_1_cycle_2.md"],
        "resumable_task":"User picks one: (1) change mine_trace.py to follow e2e-delta-math.md:43, (2) reconcile e2e-delta-math.md:43 vs :51, (3) accept BS1/BS8 as empirically ungrounded, (4) approve a capture-policy change plus Stage-1 recapture. Then rerun the mine_trace dry run ... and spawn a fresh T_AUDIT_S1 cycle 3. ..."},
 "audit":{"stage_1":{"started_at":"2026-10-29T02:13:48Z","cycle":2}},
 "baseline":{"started":"2026-10-28T22:59:35Z","completed":"2026-10-28T23:38:33Z"}}
== validate
PASS
validate-exit=0
== e2e fingerprint (rounds/1/sweeps/baseline/e2e_latency_results.json)
{"pumice_commit":"769c752995b8a39b27df028176411d17c99ff441","cwd_git":"769c752995b8a39b27df028176411d17c99ff441",
 "generated_at":"2026-10-28T23:02:21.264802+00:00","gpu":"NVIDIA L40S","model_id":"/hf/gpt-oss-20b","vllm_version":"0.32.0","torch_version":"2.13.0+cu130"}
== leases
GPU   Status     Command                                     Since                Lease Expires        Session
-----------------------------------------------------------------------------------------------------------------------
0     FREE       -                                           -                    -                    -
1     FREE       -                                           -                    -                    -
== gpus (container)
0, NVIDIA L40S, 1 MiB, 46068 MiB, 0 %
1, NVIDIA L40S, 1 MiB, 46068 MiB, 0 %
== compute apps
(end)
== leftover procs (pumice|rust_grpc|nsys|vllm|run_pumice)
(end)
```

`audit.stage_1.passed_at` is absent: T_AUDIT_S1 did not pass. Stage 1 numbers in `state.json` (`campaign.rounds[0].baseline.e2e_latency`, avg seconds): BS1 3.2265, BS8 6.8224, BS32 9.1509.

`target.json`: `engine` `{"root":"/workspace/pumice","venv":"/opt/pumice-venv/bin/python"}`, `runtime.profile` pumice-cuda, NVIDIA L40S, target `{"tp":1,"dp":1,"ep":1,"dtype":"mxfp4","max_model_len":4096}`, workload BS [1,8,32], input 64, output 512, 10 iters, `bench.prompt_source` raw, runner `pumice_grpc_bench`. Its `stack_fingerprint`: pumice_commit 769c7529..., pumice_rust_build_id `sha256:c8f834c2...`, torch 2.13.0+cu130, triton 3.7.1, vllm 0.32.0, flashinfer 0.7.0.post1, driver 595.91.07, image_digest null.

GPU memory, before (22:57) and after (02:17):

| Where | Before | After |
|---|---|---|
| container GPU 0 / 1 | 1 MiB / 1 MiB | 1 MiB / 1 MiB |
| host GPU 0 / 1 / 2 / 3 | 109 / 1 / 1 / 1 MiB | 109 / 1 / 1 / 1 MiB |
| compute apps | none | none |

Host GPU 0 holds 109 MiB before and after. It belongs to the other container and was not touched. `docker ps` at the end: `ammo-plugin-smoke Up 8 hours`, `ammo-pumice-cuda-server Up 8 days`.

GPU leases taken during the run (all through `gpu_reservation.py`, all released by the researcher that took them):

| Reserve | Release | Purpose |
|---|---|---|
| 23:02:15, `--num-gpus 1 --lease-hours 4 --no-auto-release` | 23:37:54, `release-session` | Baseline and profiling sweeps |
| 00:59:49, `--num-gpus 1 --lease-hours 1` | 01:04:47, `release-session` | HIGH-1 control launch |

`/tmp/ammo_gpu_res/release.log`: 73 entries (146 lines), every one `Released GPUs: ` with nothing left to release, every one `ok`. First and last:

```
2026-10-28T23:00:34Z hook=PostToolUse sid=20261028-225843-229b3b Released GPUs:
 ok
2026-10-28T23:38:16Z hook=SubagentStop agent_type=ammo:ammo-researcher sid=942ac5ab-...:aa35051498f0b4045 scope=children Released GPUs:
 ok
...
2026-10-29T02:13:47Z hook=SubagentStop agent_type=ammo:ammo-auditor sid=b8f87a37-e1bc-4a4a-801e-5e8285a5a001 scope=exact Released GPUs:
 ok
```

Every file under `.ammo/20261028-225843-229b3b/` (67 files):

```
./blockers/stage2_mine_trace_decode_busy_denominator.md
./rounds/1/audits/stage_1.md
./rounds/1/audits/stage_1_cycle_2.md
./rounds/1/constraints.md
./rounds/1/profiling/nsys/baseline.nsys-rep
./rounds/1/profiling/nsys/baseline.sqlite
./rounds/1/profiling/nsys/baseline_bs1.sqlite
./rounds/1/profiling/nsys/baseline_bs1_decode_step_chain.csv
./rounds/1/profiling/nsys/baseline_bs1_profiledwave_cuda_gpu_kern_sum_start=3320446600_end=6573746312.csv
./rounds/1/profiling/nsys/baseline_bs1_profiledwave_kernels_by_grid.csv
./rounds/1/profiling/nsys/baseline_bs1_slice_cuda_gpu_kern_sum_start=3320446480_end=6573746180.csv
./rounds/1/profiling/nsys/baseline_bs32.sqlite
./rounds/1/profiling/nsys/baseline_bs32_decode_step_chain.csv
./rounds/1/profiling/nsys/baseline_bs32_profiledwave_cuda_gpu_kern_sum_start=120535979912_end=129664380808.csv
./rounds/1/profiling/nsys/baseline_bs32_profiledwave_kernels_by_grid.csv
./rounds/1/profiling/nsys/baseline_bs32_slice_cuda_gpu_kern_sum_start=120535979980_end=129639524442.csv
./rounds/1/profiling/nsys/baseline_bs8.sqlite
./rounds/1/profiling/nsys/baseline_bs8_decode_step_chain.csv
./rounds/1/profiling/nsys/baseline_bs8_profiledwave_cuda_gpu_kern_sum_start=42771536264_end=49643333256.csv
./rounds/1/profiling/nsys/baseline_bs8_profiledwave_kernels_by_grid.csv
./rounds/1/profiling/nsys/baseline_bs8_slice_cuda_gpu_kern_sum_start=42771536380_end=49632257194.csv
./rounds/1/profiling/nsys/baseline_per_bs_slices.json
./rounds/1/profiling/nsys/slice_baseline_per_bs.py
./rounds/1/sweeps/baseline/e2e_latency_results.json
./rounds/1/sweeps/baseline/e2e_latency_results.md
./rounds/1/sweeps/baseline/json/baseline_bs1.json
./rounds/1/sweeps/baseline/json/baseline_bs1.runner.json
./rounds/1/sweeps/baseline/json/baseline_bs32.json
./rounds/1/sweeps/baseline/json/baseline_bs32.runner.json
./rounds/1/sweeps/baseline/json/baseline_bs8.json
./rounds/1/sweeps/baseline/json/baseline_bs8.runner.json
./rounds/1/sweeps/baseline/json/golden_refs.json
./rounds/1/sweeps/baseline/logs/baseline.server.log
./rounds/1/sweeps/baseline/logs/baseline_bs1.log
./rounds/1/sweeps/baseline/logs/baseline_bs32.log
./rounds/1/sweeps/baseline/logs/baseline_bs8.log
./rounds/1/sweeps/baseline/status/baseline.json
./rounds/1/sweeps/baseline_control/e2e_latency_results.json
./rounds/1/sweeps/baseline_control/e2e_latency_results.md
./rounds/1/sweeps/baseline_control/json/baseline_bs1.json
./rounds/1/sweeps/baseline_control/json/baseline_bs1.runner.json
./rounds/1/sweeps/baseline_control/json/baseline_bs32.json
./rounds/1/sweeps/baseline_control/json/baseline_bs32.runner.json
./rounds/1/sweeps/baseline_control/json/baseline_bs8.json
./rounds/1/sweeps/baseline_control/json/baseline_bs8.runner.json
./rounds/1/sweeps/baseline_control/logs/baseline.server.log
./rounds/1/sweeps/baseline_control/logs/baseline_bs1.log
./rounds/1/sweeps/baseline_control/logs/baseline_bs32.log
./rounds/1/sweeps/baseline_control/logs/baseline_bs8.log
./rounds/1/sweeps/baseline_control/status/baseline.json
./rounds/1/sweeps/profiling/e2e_latency_results.json
./rounds/1/sweeps/profiling/e2e_latency_results.md
./rounds/1/sweeps/profiling/json/baseline_bs1.json
./rounds/1/sweeps/profiling/json/baseline_bs1.runner.json
./rounds/1/sweeps/profiling/json/baseline_bs32.json
./rounds/1/sweeps/profiling/json/baseline_bs32.runner.json
./rounds/1/sweeps/profiling/json/baseline_bs8.json
./rounds/1/sweeps/profiling/json/baseline_bs8.runner.json
./rounds/1/sweeps/profiling/logs/baseline.server.log
./rounds/1/sweeps/profiling/logs/baseline_bs1.log
./rounds/1/sweeps/profiling/logs/baseline_bs32.log
./rounds/1/sweeps/profiling/logs/baseline_bs8.log
./rounds/1/sweeps/profiling/nsys/baseline.nsys-rep
./rounds/1/sweeps/profiling/status/baseline.json
./state.json
./state.json.lock
./target.json
```

The Nsys report at `rounds/1/sweeps/profiling/nsys/baseline.nsys-rep` sits inside a sweep output dir, which `scripts/artifact_layout.json:106` forbids. The runner wrote it there (cycle 1 LOW-5, part of inherited defect (a)). A byte-identical copy sits at `rounds/1/profiling/nsys/baseline.nsys-rep` (same md5, `165a3bba...`).

T_AUDIT_S1 verdicts, from the audit files:
- Cycle 1 (`rounds/1/audits/stage_1.md`): **Overall: BLOCKED**. BLOCKING-1: the attribution capture is not bounded per bucket, and Stage 2's `mine_trace.py` cannot use it. HIGH-1: the timing ran after 744 s of GSM8K load, which no later arm repeats. 10 LOW.
- Cycle 2 (`rounds/1/audits/stage_1_cycle_2.md:518`): **Overall: BLOCKED**. BLOCKING-1 and HIGH-1 are resolved. New BLOCKING-1R: `mine_trace.py` FATALs on the BS1 and BS8 slices (`decode_busy` 1.0023 and 1.0158) because of inherited defect (b). Its own words: "The cause is a toolkit contract conflict, not a Stage-1 execution defect, and no compliant Stage-1 or Stage-2 action resolves it." 0 HIGH, 7 new LOW. LOW-C independently reports finding 7.

The repair, in short:
- Attempt 2 (00:47 to 01:10): a control launch with no golden phase. BS1 -0.014%, BS8 +0.94%, BS32 +0.2% against the baseline (recomputed from both `e2e_latency_results.json`: -0.014%, +0.936%, +0.224%). Per-BS `.sqlite` slices were cut from the one report and cross-checked against `baseline.sqlite`. The original report stayed byte-identical.
- Investigator (01:11 to 01:18): keep the baseline as comparator. Class the BS1 FATAL as a Stage 2 toolkit concern.
- Attempt 3 (01:18 to 01:38): no GPU. Each slice now ends at the last full forward step: 512 steps per BS, kernels 205,837 / 205,898 / 218,161. The HIGH-1 closure went into `constraints.md`.

## 4. Hooks

### 4.1 Next-step reminder (`hooks/ammo-next-step-reminder.sh`)

From the transcripts under `/root/.claude/projects/-workspace-pumice/` (`hook_additional_context`, `PostToolUse:Bash`):

```
session 1 (942ac5ab), 2026-10-28T22:58:44.964Z
  AMMO NEXT STEP: Next: dispatch ammo-researcher (task_type: baseline). T2.
session 2 (b8f87a37), 2026-10-28T23:50:49.929Z
  AMMO NEXT STEP: AUDIT REQUIRED (T_AUDIT_S1): Stage 1 baseline complete. Spawn ammo-auditor (4-phase: inventory → reconstruction → checklist → reconciliation). Stage: stage_1.
```

The first came right after the scaffold, the second right after the resume. Both name the right next step for the state at that time.

### 4.2 State-validate pass in a real session (post-fix, final mirror)

`hooks/ammo-state-validate.sh` prints nothing on a pass. After every validate, pass or block, it overwrites the snapshot `/tmp/ammo-state-prev-<session id>.json` with the validated state (`ammo-state-validate.sh`, "PREV_STATE snapshot" block). So a pass shows as empty hook stdout plus a fresh snapshot equal to `state.json`.

Session script `/tmp/w11/svcheck.sh` (cleanenv, `AMMO_PYTHON` set, `--model sonnet`, `--include-hook-events`, `< /dev/null`). The prompt said it was not the lead and must run exactly one Bash command:

```bash
cd /workspace/pumice; A=/workspace/pumice/.ammo/20261028-225843-229b3b; V=$(/mnt/ammo/scripts/ammo ammo_state.py get $A/state.json --field "campaign.rounds[0].baseline.completed_at"); /mnt/ammo/scripts/ammo ammo_state.py set --state $A/state.json --field "campaign.rounds[0].baseline.completed_at" --value "\"$V\""; echo "set-exit=$? value=$V"
```

Result, 02:15:26 to 02:15:40 UTC, `exit=0`:

```
init: {"session_id":"b3fd8499-c04f-4125-9139-6521ead9ed80","model":"anthropic.claude-sonnet-5","plugins":["ammo","agents-md"]}
tool_use Bash: the command above, unchanged
tool_result: "set-exit=0 value=2026-10-28T23:38:33Z"
hook_response SessionStart:startup  exit 0  stdout {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"# AMMO Optimization Detected ...
hook_response PreToolUse:Bash       exit 0  stdout ""   (x2)
hook_response PostToolUse:Bash      exit 0  stdout ""   (x3)
hook_response PostToolUse:Bash      exit 0  stdout {"hookSpecificOutput":{..."AMMO NEXT STEP: AUDIT REQUIRED (T_AUDIT_S1): ..."}}
hook_response Stop                  exit 0  stdout ""
final reply: "The field was read and rewritten successfully (idempotent round-trip). Stopping here as instructed - not acting as AMMO lead, ..."   (its em dash shown as a hyphen)

$ ls -la --time-style=+%T /tmp/ammo-state-prev-*
-rw-------. 1 root root 6840 02:15:38 /tmp/ammo-state-prev-b3fd8499-c04f-4125-9139-6521ead9ed80.json   <- new, this session
-rw-------. 1 root root 6840 02:14:15 /tmp/ammo-state-prev-b8f87a37-e1bc-4a4a-801e-5e8285a5a001.json   <- session 2, the lead's pause write
-rw-r--r--. 1 root root 3266 00:22:00 /tmp/ammo-state-prev-da2sid658687.json                          <- a reviewer's test, not mine
$ jq -S . /tmp/ammo-state-prev-b3fd8499-....json | md5sum      -> e49f52dbc6876fc7e362b21b62a42731
$ jq -S . $A/state.json | md5sum                               -> e49f52dbc6876fc7e362b21b62a42731
$ md5sum $A/state.json   before and after the session         -> f1720d3581b6e08e60013eb037ff339c (unchanged; the same value was written)
```

The write named `state.json` only through `$A`. The hook found it, validated it, wrote the snapshot, and printed nothing: a pass. There is no snapshot for session 1 (942ac5ab), although its lead wrote `state.json` through `$A` with `ingest-baseline` at 23:38:33. That absence is the pre-fix gap (finding 2).

The same pass also shows in the campaign itself. The lead's pause at 02:14:13 wrote through `$S` (`S=.ammo/20261028-225843-229b3b/state.json`), and the session 2 snapshot is dated 02:14:15.

### 4.3 State-validate replay, before and after the fix

`/tmp/w11/hookrepro.py` feeds a PostToolUse Bash payload to the hook with `AMMO_REMINDER_STATE_DIR=/tmp/w11/hookrepro-state`, so live snapshots are not touched. The hook only reads the command text; it does not run it. Case 4 sets `campaign.status = bogus_status` in the throwaway clone's campaign under `/tmp/engine-check` (restored to `active` after each run).

Before the fix (23:40:12 UTC, pre-276aa6f mirror):

```
--- 1. lead's own command (path in a shell variable)
    exit=0 stdout='' validated(snapshot written)=False
--- 2. same command, literal path
    exit=0 stdout='' validated(snapshot written)=True
--- 3. ingest-baseline, literal path
    exit=0 stdout='' validated(snapshot written)=False
--- 4. clone campaign with campaign.status=bogus_status, literal path
    exit=0 stdout='{"decision":"block","reason":"state.json violates schema (/mnt/ammo/schemas/state.schema.json):\\n  - campaign.status: \'bogus_status\' is not one of [\'active\', \'paused\', \'campaign_complete\', \'campaign_exhausted\']\\nFix the values and retry the write.", ...' validated(snapshot written)=True
```

After the fix (02:17:08 UTC, final mirror):

```
--- 1. lead's own command (path in a shell variable)
    exit=0 stdout='' validated(snapshot written)=True
--- 2. same command, literal path
    exit=0 stdout='' validated(snapshot written)=True
--- 3. ingest-baseline, literal path
    exit=0 stdout='' validated(snapshot written)=True
--- 4. clone campaign with campaign.status=bogus_status, literal path
    exit=0 stdout='{"decision":"block","reason":"state.json violates schema (/mnt/ammo/schemas/state.schema.json):\\n  - campaign.status: \'bogus_status\' is not one of [\'active\', \'paused\', \'campaign_complete\', \'campaign_exhausted\']\\nFix the values and retry the write.", ...' validated(snapshot written)=True
replay-exit=0
```

Case 2 is the pass and case 4 the block. Cases 1 and 3 moved from not validated to validated.

### 4.4 Layout warn (`hooks/ammo-artifact-layout-warn.sh`)

- `grep: warning: ? at start of expression` appeared 192 times in the transcripts, first at 22:59:24 and last at 00:01:35. The 00:01:47 mirror change removed it (finding 3).
- At 00:17:56 a delegate's read-only `find`/`cat` command drew 3 warnings in one stdout, and Claude Code rejected them: `hook_non_blocking_error` "Hook output looks like a JSON object but is not valid JSON" (findings 5 and 6).
- On the final mirror (7fcb0fe):

```
$ /tmp/w11/layoutrepro.sh      # the 00:17:56 command: glob tokens only, read with python3 -c
exit=0 objects=0
$ /tmp/w11/layoutrepro2.sh     # touch $A/rounds/1/bogus_a.txt $A/rounds/1/bogus_b.txt (text only, nothing written)
exit=0 objects=1
single JSON: ok
LAYOUT WARN: rounds/1/bogus_a.txt is outside the canonical AMMO V2 layout. ...
LAYOUT WARN: rounds/1/bogus_b.txt is outside the canonical AMMO V2 layout. ...
```

### 4.5 Other hooks seen in the run

| Hook | When | What it did |
|---|---|---|
| `ammo-postcompact.sh` (SessionStart) | 22:53 clean room, 23:50:44, 02:15:27 | Silent outside a campaign; "# AMMO Optimization Detected" inside one |
| `inject-audit-phase2.sh` (PostToolUse Write) | 00:25:55, 02:03:15 | "Audit Phase 2 hook: the Phase 1 verdict file is on disk. This block delivers the Phase 2 instructions; it is not a tool error." |
| `ammo-audit-start-stamp.sh` (PostToolUse Agent) | 00:46:41, 02:13:48 | Stamped `audit.stage_1` cycle 1 and cycle 2, at return time (finding 7) |
| `ammo-subagent-release.sh`, `ammo-gpu-release.sh` | 73 times | `release.log` entries, nothing left to release (section 3.5) |
| Stop hook | end of the post-fix session | exit 0, empty |

## 5. Findings

### Plugin defects found by this run and fixed during it

| # | Defect | Evidence | Fix and check |
|---|---|---|---|
| 1 | Scaffold under an engine venv without `jsonschema` died on schema validation with no hint | Step 2 first attempt | 276aa6f. Step 2 redo, variant A: exit 2 names all five packages; preflight adds a `plugin python deps` check |
| 2 | `ammo-state-validate.sh` skipped writes that name `state.json` through a shell variable (`$A/state.json`), and its writer list missed `ingest-baseline`. Session 1's lead wrote state that way, unvalidated | Replay cases 1 and 3 before the fix; no session 1 snapshot (section 4.2, 4.3) | 276aa6f. Post-fix session pass, the lead's pause write validated at 02:14:15, replay cases 2 (pass) and 4 (block) |
| 3 | `artifact_layout.json` held a Python-only `(?:` group. GNU grep warned on every Bash call, and correct writes warned as drift | 192 grep warnings, 22:59:24 to 00:01:35 (section 4.4) | 276aa6f. None after the 00:01:47 mirror change |
| 5 | `hooks/ammo-artifact-layout-warn.sh` printed one JSON object per flagged path (`emit_warn` at :144-154, called from `check_path` at :216 in the loop at :263-268, before the fix). With two or more paths, Claude Code dropped every warning | 00:17:56 `hook_non_blocking_error`; replay on the 276aa6f mirror: `exit=0 objects=3`, `json.loads` "Extra data" | 7fcb0fe. Replay with two bad paths: one JSON object holding both warnings |
| 6 | The same hook checked glob tokens as literal paths. `sweeps/integration*`, `sweeps/opt*`, `sweeps/*/status/*.json` warned on a read-only command, because `hook_cmd_classify.py --mode readonly` treats a piped `python3 -c` as a write | Same event and replay as 5 | 7fcb0fe. Replay of the 00:17:56 command: `objects=0` |

Fix files for 276aa6f: `new_target.py:798,943` (`_require_plugin_modules`), `preflight_pumice.py:420-442`, `campaign_paths.PLUGIN_PYTHON_DEPS`, `hooks/ammo-state-validate.sh:55-68,120` (campaign-state fallback, writers derived from `ammo_state.py`), `scripts/artifact_layout.json`, `hooks/ammo-artifact-layout-warn.sh` (python `re`, read-only commands skipped). 7fcb0fe: `hooks/ammo-artifact-layout-warn.sh` and its shell test.

### Plugin defects open at the end of the run

team-lead ruling at 02:26: 4, 8, 9 and 10 are being fixed now (fixed in commit a0c0f7f). 7 and 11 are inherited and stay open as LOW.

| # | Severity | Defect | Evidence | Fix shape |
|---|---|---|---|---|
| 4 | MEDIUM, fix in flight | `orchestration/audit-protocol.md:42` spawns the auditor with `run_in_background=True`. `references/harness-binding.md:60-63` says one-shot roles spawn without it. Under `claude -p`, Claude Code stops background tasks after 600 s, so session 1 lost its auditor and 10 min of work. The README says nothing about headless runs | `/tmp/w11/campaign.err`: "Background tasks still running after 600s; terminating." at 23:48:46; `audits/` was empty | Drop `run_in_background=True` from `audit-protocol.md:42`; add a README line on `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` for `-p` |
| 7 | LOW, inherited, stays open | `hooks/ammo-audit-start-stamp.sh` runs on PostToolUse of the Agent call. For a foreground auditor that is the return time. `audit.stage_1.started_at` held 00:46:41 for cycle 1 (started 23:51:06) and holds 02:13:48 for cycle 2 (started 01:38:17). The cycle 2 auditor reports the same as its LOW-C | `state.json` `audit.stage_1`; section 3.2 | Stamp on PreToolUse after the permission decision, or record both times |
| 8 | LOW, fix in flight | `claude plugin validate` warns on the unknown `compatibility` field in `.claude-plugin/plugin.json` | Step 1 | Move it under `metadata` or drop it |
| 9 | LOW, fix in flight | `agents/ammo-champion.md` and `agents/ammo-impl-champion.md` pin `model: global.anthropic.claude-opus-5-5[1m]`, a Bedrock-only id. The other agents use the `opus` and `sonnet` aliases. Not exercised in Stage 1 | `grep '^model:' agents/*.md` | Use the `opus` alias |
| 10 | LOW, fix in flight | At 00:47:01 the lead ran `touch /mnt/ammo/.wtest` to see if the plugin dir is writable. The mount is read-only, so nothing changed | Session 2 lead Bash call | A SKILL.md line that the plugin dir is read-only |
| 11 | LOW, inherited, stays open | With `campaign.status = paused` and a campaign-scoped `auditor_escalation` that asks for a user decision, the next-step reminder still says "AUDIT REQUIRED (T_AUDIT_S1) ... Spawn ammo-auditor", and the SessionStart text tells a new session to act as lead. `scripts/ammo_state.py:1915-1951` `_build_reminder` checks only `terminal_statuses` before the stage branches; the server source has the same function at `ammo_state.py:1918`. SKILL.md never names `paused` or `auditor_escalation` | Post-fix session, 02:15:38 (section 4.2); `ammo_state.py next-step` at 02:17 prints the same line | When paused, return "Paused: read `campaign.auditor_escalation.resumable_task`; get the user decision first" |

Observation, not a defect: the Pumice checkout has its own `.claude/skills/` (13 skills such as `pumice-nsys-compare`, `cuda-illegal-memory-debug`). Claude Code lists them next to `ammo:ammo` in every session in `/workspace/pumice` (`init.skills` of the post-fix session). No AMMO agent called one in this run.

### Inherited AMMO defects found by the live run (recorded, not fixed here)

team-lead ruling: these exist in the server source too, so this subtask records them and does not fix them. Both block or weaken Stage 2, which is out of scope.

**(a) One Nsys capture for all buckets, where the references require one report per bucket.**
- Runner: `scripts/run_pumice_grpc_bench.py:4174` calls `server.start_profile()` once, the bucket loop runs at `:4177`, and `server.stop_profile()` runs once at `:4345`. Server source: `ai_cli_session/.claude/skills/ammo/scripts/run_pumice_grpc_bench.py:4136`, `:4139`, `:4307`.
- Contradicts: `references/nsys-profiling-guide.md:93` ("The runner writes one `.nsys-rep` for each label and bucket"), `references/artifact-layout.md:68` and `:341` (`baseline_bs{BS}.nsys-rep` feeds Stage 2), `scripts/artifact_layout.json:106`.
- Found as T_AUDIT_S1 cycle 1 BLOCKING-1. The researcher repaired it inside the campaign by cutting per-BS `.sqlite` slices with `nsys export --times` (no per-BS `.nsys-rep` exists, because nsys cannot cut a report).
- Fix shape: start and stop one Nsys capture per bucket in the runner.

**(b) `mine_trace.py` divides same-trace kernel busy time by the clean sweep's decode wall.**
- Plugin: `scripts/mine_trace.py:532-533` (`decode_busy = (mean_busy_ns/1e9 * n_steps) / decode_avg_s`, where `decode_avg_s` comes from the profiler-free sweep), checked against `DECODE_BUSY_MAX = 1.0` at `:77`. Server source: `mine_trace.py:522-523` and `:67`.
- Contradicts: `references/e2e-delta-math.md:43` ("`decode_busy` likewise denominates the decode region's own wall taken from the **same** trace as its merged-busy numerator").
- Numbers on this target: `decode_busy` 1.0004 (BS1), 1.0038 (BS8), 0.9955 (BS32), mean step. Nsys inflates step time by about 0.8% at BS1, more than the clean idle share on a decode-saturated L40S, so BS1 and BS8 FATAL.
- Campaign blocker: `.ammo/20261028-225843-229b3b/blockers/stage2_mine_trace_decode_busy_denominator.md`.
- Fix shape: take the decode wall from the same trace as the busy numerator.

### Workload facts (not defects)

- GSM8K golden accuracy is 0.4223 (557 of 1319) with raw prompts and `max_tokens` 1024. The auditor recomputed it and accepted it as a comparator (Phase 2, "Golden comparator is usable").
- The control launch with no golden phase (HIGH-1 repair) moved BS1 by -0.01%, BS8 by +0.94%, BS32 by +0.2%. The investigator classed the BS8 gap as launch-to-launch band noise.

## 6. Wall clock per step (UTC)

| Step | Start | End | Time |
|---|---|---|---|
| 1. Clean-room load | 22:53 | 22:55 | 2 min |
| 2. Fail-closed, first try | 22:56 | 22:56 | under 1 min |
| 2. Redo, variants A and B | 23:11 | 23:12 | 1 min |
| 3. Session 1 | 22:58:30 | 23:48:46 | 50 min, USD 19.33 |
| 3. Stage 1 baseline (state stamps) | 22:59:35 | 23:38:33 | 39 min |
| 3. Lost background auditor | 23:38:39 | 23:48:46 | 10 min |
| 3. Session 2 | 23:50:44 | 02:14:27 | 2 h 24 min, USD 55.29 |
| 3. T_AUDIT_S1 cycle 1 | 23:51:06 | 00:46:41 | 56 min |
| 3. Repair: two researcher passes and one investigator | 00:47:26 | 01:38:12 | 51 min |
| 3. T_AUDIT_S1 cycle 2 | 01:38:17 | 02:13:48 | 36 min |
| 3. Escalation and pause | 02:13:55 | 02:14:27 | under 1 min |
| 4. Final sync, post-fix hook session, replays | 02:15:02 | 02:21:00 | 6 min |

From the first command to the last: 22:53 to 02:21, about 3 h 30 min. Session costs are the `total_cost_usd` of each `result` event.

## 7. Deviations from SKILL.md and from the subtask

1. Every step unset the image's `PYTHONPATH` and the four `AMMO_*` names it injects (section "Environment"), so the run saw what a plain GPU host has.
2. `AMMO_PYTHON=/opt/pumice-venv/bin/python` was exported, because this image's engine venv is not `<engine>/.venv`. The scaffold recorded it as `engine.venv`, which then governs (conventions section 2).
3. The campaign lead scaffolded the campaign from the `/ammo:ammo` flags. I did not scaffold it ahead of time. This tested the SKILL.md Invocation path; session 2 tested the resume path.
4. The prompt told the lead to pause once T_AUDIT_S1 passed. SKILL.md has no smoke-run stop; this was the only way to end at Stage 1. The lead paused on its own after the BLOCKED, through `audit-protocol.md:166`.
5. Session 2 ran with `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` and `--include-hook-events`, after session 1 lost its auditor (finding 4).
6. I did not run `claude -p "/ammo:ammo --help"`. In `/workspace/pumice` that would start the lead skill, which can scaffold. The clean-room `init` event lists `ammo:ammo` in `skills` and `slash` instead (step 1).
7. Step 2 ran from a throwaway clone `/tmp/engine-check` (team-lead ruling), so the real campaign stayed clean.
8. The time budget ran over, with team-lead approval: cap 01:30, then 02:15, then 02:25 so cycle 2 could reach its final verdict.
9. Other workers changed the plugin mirror during the run (section 3.3).
10. The optional step 5 (`pumice-stage-native.sh` in a worktree) was not run. Stage 1 creates no track worktree, and the budget was spent on the audit cycles.
11. `plan.md` acceptance asks for a passed T_AUDIT_S1 (`audit.stage_1.passed_at` set). That was not met. team-lead set the fallback "reached T_AUDIT_S1 verdict X" at 00:30 and kept it at 01:02. At 01:24 team-lead ruled that the blocker is an inherited defect and that Stage 2 is out of scope.
12. `plan.md` says the container ends stopped. The subtask says to leave it running. I followed the subtask: `ammo-plugin-smoke Up 8 hours`.

## 8. BLOCKED messages and how each closed

| Sent | Subject | Resolution |
|---|---|---|
| 22:57 | Scaffold dies on missing `jsonschema` before preflight runs | Plugin defect confirmed, fixed in 276aa6f. Step 2 redone as variants A and B |
| 23:41 | State-validate never validated a real campaign write | Two plugin defects confirmed, fixed in 276aa6f. Acceptance moved to a post-fix session plus replay cases 2 and 4 |
| 01:23 | Stage 2 mining will FATAL on this target; one Nsys report for all buckets | Ruled inherited AMMO defects: recorded here, not fixed. Stage 2 out of scope |
| 02:01 | Layout warn drops all warnings with 2+ paths, and warns on glob tokens | Fixed in 7fcb0fe |
| 02:09 | The 02:15 cap lands in the middle of cycle 2 Phase 2 | Cap moved to 02:25. The lead paused at 02:14:27 |
| 02:17 | Next-step reminder ignores a paused campaign | Agreed: inherited LOW 11, not fixed here |
| 02:23 | Open findings 4 and 7 to 10 not yet routed | 4, 8, 9, 10 being fixed now (commit a0c0f7f); 7 stays open as inherited LOW |

## 9. What I left behind

- Container `ammo-plugin-smoke` is running. I did not touch `ammo-pumice-cuda-server` or host GPUs 0 and 1.
- The campaign `/workspace/pumice/.ammo/20261028-225843-229b3b` is paused, with the marker in `.ammo/active`. The engine checkout is at 769c7529, with the sm80 patch as its only tracked change (`M pumice-rust/confdb/gpt_oss.toml`). `.ammo/` is in `.git/info/exclude`.
- My scripts and logs: `/tmp/w11/` (launchers, stream logs, replays, `final.sh`, `final.txt`). The throwaway clone `/tmp/engine-check` and the venvs `/tmp/badvenv`, `/tmp/badvenv2` are kept for re-checks. The lead left `/tmp/state_backup_pre_pause.json`.
- `/root/.claude*`: the transcripts of the clean-room session, both campaign sessions and the post-fix session.
- On the host: nothing outside `~/ammo-plugin-smoke/`. On the Mac: only this record. Two scratch files I wrote in the Mac's `/tmp` (a `git status` capture before the run and a file list for this record) were removed before the review request.
