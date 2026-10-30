# Baseline - seeded ammo plugin, wave 0

The seeded tree holds 215 files. Every file from the conventions §10 source lists is at its §1 place. The codemod changed 96 files.

The §9 command (now with `--with psutil`) runs to the end on the seeded tree. The seeded-tree baseline is `445 failed, 1855 passed, 72 skipped, 1 xfailed, 29 errors`. Wave-1 workers compare against that line and against the per-test table in section 3.

No regression comes from the rewrite rules themselves, except one hook guard case (section 5). The other status changes come from files that moved, layout idioms that §2 gives to wave 1, and files the plugin does not ship.

`tests/test_parse_artifacts.py` is not shipped. It imports `eval/scripts/parse_artifacts.py` at module level, and `eval/` is not shipped (conventions §9 ruling, 2026-10-28). `test_sweep_dp.py` stays: it reaches `eval/` only inside 7 test bodies, so it loads, and those 7 cases fail.

## 1. Commands

The §9 command, verbatim (conventions §9 after the 2026-10-28 ruling that added `--with psutil`):

```
PYTEST="uv run --no-project --with pytest --with pyyaml --with jsonschema --with pydantic --with httpx --with numpy --with psutil python -m pytest"
cd R && $PYTEST tests -q -m "not neuron and not requires_gpu"
```

Source tree run. The working directory is `SRC/.claude/skills/ammo` (its `.pytest_cache` shows this is where the requirements baseline ran). Two additions keep ammo-server read only and do not change results: `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`.

```
cd /Users/jinhun/GitHub/ammo-server/ai_cli_session/.claude/skills/ammo
PYTHONDONTWRITEBYTECODE=1 $PYTEST -p no:cacheprovider tests -q -m "not neuron and not requires_gpu"
```

zsh does not split `$PYTEST`. Run the lines under `bash -c`.

The seed and the codemod (both scripts are in this folder):

```
python3 codemod.py seed    /tmp/ammo-seed/pre
cp -a /tmp/ammo-seed/pre /tmp/ammo-seed/post
python3 codemod.py rewrite /tmp/ammo-seed/post
(cd /tmp/ammo-seed && diff -ru pre post) > codemod.diff
rsync -a /tmp/ammo-seed/post/ R/          # no --delete: other workers' files stay
python3 codemod.py inventory R --content  # empty output, exit 0
```

A second run of the same steps into a fresh folder gave a byte-identical diff body and post tree.

## 2. Summary lines (this Mac, Python 3.12 from uv)

| Tree | Command | Summary line |
|---|---|---|
| Source | §9 | `1 failed, 1680 passed, 4 skipped, 1 xfailed, 1 warning in 60.20s` |
| R after codemod | §9, exact | `445 failed, 1855 passed, 72 skipped, 1 xfailed, 29 errors in 563.05s` |

The R line is the same on a second run with `--junitxml` added (549.71s). The run takes about 9 minutes, mostly the serving tests. `psutil` changes nothing on the source tree: its line before the ruling was the same counts.

The source failure is `test_sweep_profiler_guard.py::TestProfilerGuardIntegration::test_subprocess_baseline_nsys_exits_nonzero` (nsys is not on PATH). It fails the same way on R.

The R line includes files of other wave-0 workers that were present at run time. Without them, the seeded files give, per junit test case: 1713 passed, 374 failed, 29 errors, 5 skipped, 1 xfailed. pytest's own counts are a little higher because `test_codex_port.py` reports unittest subtests one by one.

| Other worker's file at run time | Result |
|---|---|
| `test_campaign_paths.py` | 48 passed |
| `test_env_inventory.py` | 5 passed |
| `test_hooks_json.py` | 9 passed |
| `test_hooks_silent.py` | 67 skipped (Linux only) |
| `test_path_refs.py` | 43 passed, 13 failed |
| `test_prose_bans.py` | 15 passed, 14 failed |
| `test_workflow_pave.py` | 13 passed |
| `conftest.py` | present; no seeded test carries `neuron` or `requires_gpu` yet |

## 3. Tests whose status changed, source to R

Per junit test case, seeded files only. "absent" means the test does not exist on that side. Owner is the wave-1 write set that holds the file (plan.md).

| File | Change | Count | Cause | Owner |
|---|---|---|---|---|
| test_ammo_state.py | passed to failed | 93 | schema lookup idiom | worker-8 |
| test_new_target_v2_scaffold.py | passed to failed / error | 28 / 12 | schema lookup idiom | worker-8 |
| test_state_schema_semantics.py | passed to failed | 14 | schema lookup idiom | worker-8 |
| test_fail_closed.py | passed to failed | 13 | schema lookup idiom | worker-8 |
| test_new_target_runtime.py | passed to failed | 5 | schema lookup idiom | worker-8 |
| test_status_set_sync.py | passed to failed | 5 | schema lookup idiom | worker-8 |
| test_mining_invalidated_schema.py | passed to failed | 3 | schema lookup idiom | worker-8 |
| test_new_target_dp_ep.py | passed to failed | 3 | schema lookup idiom | worker-8 |
| test_diluted_pass.py | passed to failed | 1 | schema lookup idiom | worker-8 |
| test_neuron_role_routing.py | passed to failed | 9 | dialect-dir idiom | worker-8 |
| test_guidance_semantic_contract.py | passed to failed | 1 | harness moved | worker-8 |
| test_guidance_semantic_contract.py | passed to failed | 1 | README at root | worker-8 |
| test_runtime_ops.py | passed to failed | 1 | Neuron bundle not shipped | worker-7 |
| test_sweep_dp.py | passed to failed | 7 | eval not shipped | worker-7 |
| test_parse_artifacts.py | passed to absent | 6 | not copied (§9 ruling) | none |
| test_mine_trace.py | passed to skipped | 1 | one dialect only | worker-7 |
| test_preflight_vllm_api.py | passed to absent | 19 | not copied (requirements 13) | none |
| test_hook_semantics.py | absent to failed | 113 | Codex dialect layout | worker-9 |
| test_context_recovery.py | absent to failed | 38 | Codex dialect layout | worker-9 |
| test_codex_v1_runtime.py | absent to error | 15 | Codex dialect layout | worker-9 |
| test_agent_runtime_contracts.py | absent to failed / passed | 2 / 1 | Codex dialect layout | worker-9 |
| test_drift_compat.py | absent to failed / passed | 1 / 3 | Claude twin of contract test | worker-9 |
| test_codex_hook_campaign_resolution.py | absent to error / passed | 2 / 5 | Codex dialect layout | worker-9 |
| test_resolve_codex_transcript.py | absent to passed | 3 | none | worker-9 |
| test_ammo_serving.py | absent to passed | 117 | none | worker-9 |
| test_ammo_serving_campaign.py | absent to failed / passed | 2 / 40 | schema lookup idiom | worker-9 |
| test_ammo_serving_runtime.py | absent to passed | 18 | none | worker-9 |
| test_pumice_pins.py | absent to passed | 14 | none | worker-7 |
| test_env_default_guard_hook.py | absent to failed / passed | 2 / 34 | files not shipped | worker-10 |
| test_worktree_hook.py | absent to failed / passed | 12 / 18 | macOS tools | worker-10 |
| test_codex_port.py | absent to failed / passed | 19 / 2 | vllm-neuron-parity copy | worker-5 |

What each cause means:

- **schema lookup idiom.** `scripts/new_target.py:308` (also run by `test_ammo_serving_campaign.py`) and several tests use `Path(__file__).resolve().parents[3] / "schemas"`, which was the `.claude` dir and is now the pave repo root. `scripts/ammo_state.py:72` walks up from the state file to `.claude/schemas/state.schema.json`. In the plugin, the campaign dir is under the engine, so no walk from it reaches the plugin. Conventions §2 gives both idioms to `plugin_path()`. The codemod leaves them, because a rewrite to `schemas/` would walk up the engine tree, which is wrong.
- **dialect-dir idiom.** The test takes the `.claude` dir as `parents[1]` of the skill root (`CLI_ROOT`, `CLAUDE_DIR`). In R that role is R itself.
- **harness moved.** `test_retired_hook_names_and_bare_python3_are_absent` looks for `test-ammo-audit-phase2.sh` in `hooks/` and `scripts/`. The harness is now in `hooks/tests/` (conventions §1).
- **README at root.** `test_progressive_disclosure_reduces_hot_path_without_deleting_roles` asserts the skill root has no `README.md`. In R the skill root is the plugin root, which holds worker-4's README.
- **Neuron bundle not shipped.** The test looks for `_neuron_shared/skills/neuron-nki-profile-querying`. Requirements 13 drops it. The test is a candidate for the `neuron` marker.
- **eval not shipped.** 7 cases in `test_sweep_dp.py` import `eval/scripts/parse_artifacts.py` inside the test body (requirements 13). The file loads, so it stays (team-lead ruling: module-level imports only). `test_parse_artifacts.py` imported it at module level, so it is not copied.
- **one dialect only.** `test_mine_trace.py` skips its two-dialect parity check: "only one variant is materialized in this tree".
- **Codex dialect layout.** The six Codex-only tests take `parents[4]` as `ai_cli_session` and read `.codex/hooks`, `.codex/agents`, `.codex/skills/ammo/...`, and `.codex/skills/ammo/references/codex-runtime*.md`, `context-recovery.md` (Codex-only references that §10 does not list). The Codex hooks find scripts as `Path(__file__).resolve().parents[1]/'skills'/'ammo'/'scripts'` (`codex/hooks/common.py:97`, `:434`; `pre_tool_use_guard.py:23`, `:387`, `:458`, `:560`; `post_tool_use_guard.py:614`, `:750`; `session_start.py:75`). In R that is `codex/skills/ammo/scripts`, which does not exist, so the Codex hooks cannot import `hook_cmd_classify` yet. The target differs per line (`scripts/` or `codex/scripts/`), so it is not a token rename. It is §2 `plugin_path()` work.
- **Claude twin of contract test.** `test_drift_compat.py` expects the Codex role name `ammo-implementer` in `test_guidance_semantic_contract.py`. R ships the Claude twin, which says `ammo-impl-champion`.
- **files not shipped.** `test_env_default_guard_hook.py` reads `ai_cli_session/.claude/settings.local.json` and the Codex allowlist copy. R has neither (`hooks/hooks.json` replaces the settings file).
- **macOS tools.** BSD `sed` and no `flock` on this Mac. Judge on Linux.
- **vllm-neuron-parity copy.** `tests/test_codex_port.py` is copied unchanged on purpose (plan.md). It tests vllm-neuron-parity files.

## 4. Hook harnesses

**Authoritative Linux results: inside the warm container `ammo-plugin-smoke` on g6e-cdd, 2026-10-28** (Amazon Linux 2023, bash 5.2.15). The plugin dir is mounted read only at `/mnt/ammo`. The first run used the mount in place. The second ran on a copy (`cp -r /mnt/ammo /tmp/ammo && cd /tmp/ammo`). Both gave the same pass and fail counts for every harness, so no harness failed for lack of write access. The source copy ran from `/tmp/ammo-seed-src/ai_cli_session/.claude/hooks` in the same container.

The team lead's command, verbatim:

```
rsync -a /Users/jinhun/GitHub/pave/generated-plugins/ammo/ g6e-cdd:~/ammo-plugin-smoke/plugin/ && ssh g6e-cdd docker exec ammo-plugin-smoke bash -lc 'cd /mnt/ammo && for t in hooks/tests/test-*.sh; do bash $t >/dev/null 2>&1 && echo PASS $t || echo FAIL $t; done'
```

Its FAIL lines (the other 16 lines say PASS):

```
FAIL hooks/tests/test-ammo-msg-check.sh
FAIL hooks/tests/test-ammo-msg-mirror.sh
FAIL hooks/tests/test-ammo-skill-files-guard.sh
```

I did not see the stop-guard or next-step-reminder failures that another run reported. In my three full runs in the container, stop-guard was 21/21 and next-step-reminder was 27/27 every time.

Per harness, source against R, in the container:

| Harness | Source | R | Why R differs |
|---|---|---|---|
| artifact-layout-warn | 54/54 | 54/54 | |
| audit-phase2 | 18/18 | 18/18 | |
| audit-start-stamp | 53/53 | 53/53 | |
| compaction | 58/58 | 58/58 | |
| env-default-guard | 41/41 | 41/41 | |
| gpu-release | 12/12 | 12/12 | |
| is-lead | 27/27 | 27/27 | |
| monitor-reminder | 49/49 | 49/49 | |
| msg-check | 119/119 | 118/119 | case 119: no `settings.local.json` |
| msg-mirror | 23/23 | 22/23 | case 23: no `settings.local.json` |
| next-step-reminder | 27/27 | 27/27 | |
| pip-guard | 45/45 | 45/45 | |
| pretool-guard | 22/22 | 22/22 | |
| skill-files-guard | 19/19 | 18/19 | case 17: guard now too broad |
| state-validate | 119/119 | 119/119 | |
| stop-guard | 21/21 | 21/21 | |
| team-spawn-guard | 40/40 | 40/40 | |
| venv-python-guard | 23/23 | 23/23 | |
| find-campaign (worker-2) | none | 21/21 | |

The two `settings.local.json` cases read `$(dirname "$0")/../settings.local.json`. That file is not shipped (`hooks/hooks.json` replaces it). msg-mirror is on the conventions §7 drop list. Case 17 is the guard change in section 5.

Earlier runs, not authoritative:

- g6e-cdd host bash, the subtask's host-shell form (`rsync -a R/ g6e-cdd:~/ammo-plugin-smoke/plugin/ && ssh g6e-cdd 'cd ~/ammo-plugin-smoke/plugin && for t in hooks/tests/test-*.sh; do bash $t || echo FAIL $t; done'`). Same results as the container, except state-validate: 26/119 on both source and R. The host `python3` is 3.9 with jsonschema 3.2.0, which has no `Draft202012Validator`, so the hook reports "jsonschema unavailable".
- This Mac (macOS): source fails audit-phase2, compaction, is-lead, msg-check, msg-mirror, venv-python-guard (flock and GNU stat). R fails the same six plus skill-files-guard. R msg-mirror has one more failed case than source (case 23).

## 5. Semantic gaps the codemod leaves for wave 1

The rules rewrite path tokens everywhere, so a match pattern that held a `.claude/...` prefix now holds a bare dir name. These lines now match more than they did. Each one is in a wave-1 write set that §7 already tells its owner to rewrite.

| Place in R | Now | Owner |
|---|---|---|
| `hooks/ammo-skill-files-guard.sh:39-43` | denies any path with `references/`, `orchestration/`, `scripts/`, `schemas/`, `agents/` | worker-10 |
| `codex/hooks/pre_tool_use_guard.py:157-159` | same, for `/references/`, `/orchestration/`, `/agents/` | worker-9 |
| `hooks/ammo-state-validate.sh:119` | walks up for `schemas/state.schema.json` | worker-10 |
| `hooks/inject-audit-phase2.sh:47-52` | reads `${CLAUDE_PROJECT_DIR}/references/...`, the engine, not the plugin | worker-10 |
| `hooks/ammo-gpu-release.sh:52`, `hooks/ammo-subagent-release.sh:42` | fallback `${CLAUDE_PROJECT_DIR:-.}/scripts`; the first choice `../scripts` resolves | worker-10 |

Left as they were, on purpose:

- `kernel_opt_artifacts` in hooks, hook harnesses, Codex hooks, and tests. There it is a match pattern, or a fixture that a pattern reads, so the pairs move together to `in_campaign` (§3). Only the prose examples in `references/*.md`, the `state.schema.json` description, and the `new_target.py` usage text became `.ammo/`. `tests/test_prose_bans.py` flags every one left.
- Bare `.claude/hooks/` in prose and harness "Run:" comments. It is not in the rule list.
- Bare `.claude/skills/ammo` and `.codex/skills/ammo` with no subpath, in the Codex hooks' context checks.
- `.codex/agents/`, `.codex/schemas/`, `.claude/worktrees`, `.claude/teams`, `.claude/projects`. These are real Claude Code or Codex locations, or Codex text that worker-5 owns.
- Every script layout idiom (`parents[3]`, the `.claude/schemas` walk, `.parent.parent / "data"`), per §2.

## 6. Notes for the team lead

1. Resolved by the 2026-10-28 ruling. `$PYTEST` now has `--with psutil`, so the two serving test files load. They give 157 passed and 2 failed (the schema lookup idiom). They take about 7 minutes of the 9-minute run.
2. Resolved by the same ruling. `test_parse_artifacts.py` is not copied. The 7 `eval/` cases in `test_sweep_dp.py` stay as known failures. worker-7 owns them.
3. conventions §2 has no "was/is" table now. The codemod uses the rule list in the subtask.
4. Codex `hooks/pumice_env_allowlist.txt` is not copied, because §10 lists only `SRC/.codex/hooks/*.py`. `codex/hooks/pre_tool_use_guard.py` reads it as a sibling file. Until worker-9 points it at `hooks/pumice_env_allowlist.txt`, the Codex PUMICE guard sees an empty allowlist.
5. `S/docs/ammo-intent-map.json` is not copied. It has no §1 place, and worker-10 reads it from ammo-server.
6. All Claude hooks are copied, including the five on the §7 drop list. worker-10 deletes them.

## 7. Inventory checks

`python3 codemod.py inventory R --content` prints nothing and exits 0. It also checks that each seeded file equals codemod(source).

An independent check, which does not use `codemod.py`, derives the expected paths from §10 with `find` and `sed`. It prints nothing (expected 215, R held 238 files, the other 23 belong to worker-2 and worker-4):

```bash
#!/usr/bin/env bash
set -u
S=/Users/jinhun/GitHub/ammo-server; SRC=$S/ai_cli_session; R=${1:-/Users/jinhun/GitHub/pave/generated-plugins/ammo}
VNP=/Users/jinhun/GitHub/pave/generated-plugins/vllm-neuron-parity
skip='/__pycache__/|/\.pytest_cache/|\.DS_Store$'
{
  ( cd "$SRC/.claude" && find skills/ammo/scripts skills/ammo/references skills/ammo/orchestration skills/ammo/data skills/ammo/tests \
      skills/ammo/SKILL.md skills/ammo/report agents hooks schemas -type f ) | grep -vE "$skip" \
    | grep -vE '^skills/ammo/tests/agents/|(^|/)(test_)?preflight_vllm_api\.py$|^skills/ammo/tests/test_parse_artifacts\.py$' \
    | sed -E 's#^skills/ammo/report/SKILL\.md$#skills/ammo-report/SKILL.md#; s#^skills/ammo/(scripts|references|orchestration|data|tests)/#\1/#; s#^hooks/(test-[^/]+\.sh)$#hooks/tests/\1#'
  ( cd "$SRC/.codex" && ls agents/*.toml hooks/*.py hooks.json ) | sed -E 's#^#codex/#'
  echo codex/RUNTIME.md
  ( cd "$SRC/.codex/skills/ammo" && find instrumentation -type f ) | grep -vE "$skip" | sed 's#^#codex/#'
  for f in codex_agent_registry.py monitor_queue_ack.py resolve_codex_transcript.py create_worktree_with_build.sh remove_worktree_cleanup.sh; do
    test -f "$SRC/.codex/skills/ammo/scripts/$f" && echo "codex/scripts/$f"; done
  for f in test_agent_runtime_contracts.py test_codex_v1_runtime.py test_context_recovery.py test_drift_compat.py test_hook_semantics.py test_resolve_codex_transcript.py; do
    test -f "$SRC/.codex/skills/ammo/tests/$f" && echo "tests/$f"; done
  test -f "$S/shared/pumice_pins.py" && echo scripts/pumice_pins.py
  for f in test_ammo_serving.py test_ammo_serving_campaign.py test_ammo_serving_runtime.py test_pumice_pins.py test_codex_hook_campaign_resolution.py test_env_default_guard_hook.py test_worktree_hook.py; do
    test -f "$S/tests/unit/$f" && echo "tests/$f"; done
  test -f "$VNP/codex/install_agents.py" && echo codex/install_agents.py
  test -f "$VNP/tests/test_codex_port.py" && echo tests/test_codex_port.py
} | sort -u > /tmp/ammo-inv-expected.txt
others='^(scripts/campaign_paths\.py|scripts/ammo|hooks/_find_campaign\.sh|hooks/hooks\.json|hooks/tests/test-find-campaign\.sh|references/campaign-env\.md|tests/conftest\.py|tests/test_campaign_paths\.py|tests/test_path_refs\.py|tests/test_env_inventory\.py|tests/test_hooks_silent\.py|tests/test_prose_bans\.py|tests/test_hooks_json\.py|workflow\.pave\.yaml|revisions\.yaml|history/.*|scripts/validate_pave\.py|scripts/record_revision\.py|references/pave\.schema\.json|references/pave-composition\.schema\.json|tests/test_workflow_pave\.py|README\.md|VERSION)$'
( cd "$R" && find . -type f | sed 's#^\./##' ) | grep -vE "$skip|^\.pytest_cache/" | sort -u > /tmp/ammo-inv-actual.txt
comm -23 /tmp/ammo-inv-expected.txt /tmp/ammo-inv-actual.txt | sed 's/^/MISSING /'
comm -13 /tmp/ammo-inv-expected.txt /tmp/ammo-inv-actual.txt | grep -vE "$others" | sed 's/^/EXTRA /'
for p in tests/agents eval scripts/preflight_vllm_api.py tests/test_preflight_vllm_api.py tests/test_parse_artifacts.py; do test -e "$R/$p" && echo "FORBIDDEN $p"; done
exit 0
```

`hooks/hooks.json` is not in the seed: it is absent from both the pre and post trees and from `codemod.diff`. The file now in R is worker-2's.
