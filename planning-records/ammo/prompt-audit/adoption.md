# Prompt audit: adoption decisions

Lead review of the six wave-1 records (189 rows). Date 2026-10-29.

## Result

- Adopted: 181 rows. 23 of them are adopted in a changed form under a ruling below.
- Rejected: 3 rows (stage-3 `stance` field, stage-7 row 14, SKILL.md:204 debate enrichment).
- User rulings (2026-10-29): decision 4 amended, both evidenced steps go (R9); msg-check hook dropped (R10); ammo-investigator replaced by the pave-init adversarial-investigator (R11, second ruling). Adopted rows rise to 184, rejected to 3.
- Every adopted row keeps its stage, gate, verdict, and stop rule. No binding gate changes meaning.

Row ids below are `<record>-<row>`: w6 = shared.md, w1 = stage-1.md, w2 = stage-3.md, w3 = stage-4-5.md, w4 = stage-6.md, w5 = stage-7.md.

## Rulings

| # | Topic | Ruling |
|---|---|---|
| R1 | Neuron prose | One fixed line per role file: "On Neuron, which v1 does not run, follow `references/accelerator-binding.md` § {Role}." Every other Neuron clause in stage text moves to accelerator-binding.md § Neuron Execution and Evidence, or goes if that file already has it. Legacy names `vllm-cuda` and `vllm-neuron` stay only in accelerator-binding.md and scripts. `tests/test_neuron_role_routing.py` asserts the fixed line per role and reads the moved text from accelerator-binding.md. It must pass under `-m neuron`. |
| R2 | Sweep commands and comparator | `references/e2e-latency-guide.md` § Tool Selection is the one owner of every sweep command and of the comparator rule. Rule: a Stage 5 or 6 sweep imports its comparator (`--baseline-from`). Round 1 imports the Stage 1 baseline dir. Round 2 and later import the latest promoted integration dir with `--baseline-from-arm opt`. A paired rerun happens only when the comparator is stale (audit-invariants.md § Cross-Artifact Checks). validation-defaults.md § Bench Runner Guidance becomes one pointer plus the contamination reason. integration-logic.md step 3 and the S67 bullet point to the owner. Rows: w1-9, w1-10, w1-31, w4-11, w4-22, w4-23, plus the Stage 5, Stage 6, post-SHIP Quickstart rows and validation-defaults.md:68. |
| R3 | Audit gate text in SKILL.md | SKILL.md keeps the four trigger names and one pointer to audit-protocol.md § Dispatch and § Repair and Re-audit. The five steps at 184-190 go (w6-21). w4's keep of line 190 is covered by the pointer. |
| R4 | Flag route on Pumice | A campaign flag on the Pumice runner is `PUMICE_<SCOPE>_<MECHANISM>[_<ARCH>]`, read with `os.environ.get` (or `env::var`), default off. `hooks/ammo-env-default-guard.sh` and its Codex twin change: a new `PUMICE_*` read is allowed when its default is off, and blocked when truthy (the same rule the `VLLM_*` branch applies). A default change to an allowlisted Pumice name stays blocked. Reason: the runner's A/B model needs `opt_env` flags with the `PUMICE_` prefix (run_pumice_grpc_bench.py docstring and sanitizer), and the guard today blocks every new one. `VLLM_*` via `runtime_pkg.patch` stays for the vLLM runner only. Rows: w3-22, w3-23, w4-20, plus guard tests. |
| R5 | Fresh cache | Reword e2e-latency-guide.md:239 to match practice: `--fresh-cache` on the opt arm forces a cold compile of the changed code; the warmup wave (`--warmup-waves`, default 1, discarded) absorbs the compile, so it does not enter timed waves. The Stage 1 command does not change. Row w1-17. |
| R6 | Merge review loop | integration-logic.md step 1 owns the loop. The resolver returns a staged, uncommitted diff. The lead spawns a fresh reviewer (role `ammo-investigator`, or its replacement under R11) and, on REVISE, a fresh resolver with the findings. Two rejected cycles route to branch 4. The lead commits only an accepted resolution. Rows w4-10, w4-19. |
| R7 | Stage 7 audit naming | `T_AUDIT_S67` is the Stage 6 audit that must pass before Stage 7 starts. Its bullets on Stage 7 work go (w5-1, w5-6). SKILL.md:106 and the audit-invariants heading say so (w5-10). |
| R8 | Serving line | The nine role files keep their one serving line. The eleven Codex TOMLs drop theirs (w6-10). w5-14 rejected. |
| R9 | Decision 4 "no ceremony removed" | User ruling: amended. A step may go when the record cites evidence that nothing reads its output. w6-7 (early S45 partials) and w4-21 (resolver's own GSM8K and E2E runs) are adopted. requirements.md decision 4 records the amendment. |
| R10 | msg-check hook | User ruling: drop (w6-6). Its channel has no writer and the inbox is dead on Claude Code 2.1.202 and later. Conventions §7 moves msg-check and `_ammo_msg_identity.sh` to the drop list; `_ammo_is_lead.sh` stays for monitor-reminder. |
| R11 | Investigator and auditor roles | User ruling (second, same day): replace `ammo-investigator` with the pave-init `adversarial-investigator` in this wave. Same agent name on Claude and Codex: `pave-init:adversarial-investigator`; on Codex, pave-init's own `codex/install_agents.py` installs it. The ammo plugin now requires the pave-init plugin from the same marketplace; the Stage 2 redesign needs the same dependency. Brief convention, stated once in SKILL.md: one-shot, no `name`, pass plugin_root, the primary artifact paths, and one concrete decision question; it returns a verdict with file:line evidence. The auditor stays because six hooks and guards key on its name and the started_at stamp. Follow-up for later: a preflight check that the pave-init plugin is installed. |
| R12 | RETRY_WITH_CONTINGENCY | Meaning, supplied by the lead: Gate 5.2 failed (it has no universal floor, so "failed" is the word in the file), and the proposal's Production integration line names a fallback mechanism. The champion implements that fallback once and reruns Gates 5.1a to 5.2. No named fallback, or a rerun that misses the floor, exhausts the rung, and FAIL may be authored. Row w3-34. |
| R13 | Worktree order | harness-binding.md § Claude Code Notes owns the Claude Code order. The worker verifies the semantics of `isolation: worktree`, WorktreeCreate, and EnterWorktree against the Claude Code docs before writing (ask the claude-code-guide agent). impl-champion.md:19 and parallel-tracks.md:18-25 then point there. Row w3-3. |
| R14 | Integration branch location | The lead's own worktree under the engine root, created through the same worktree hook, named by `integration.combined_patch_branch`. A Rust or confdb change needs the rebuild in impl-track-rules.md § Worktree Build Rules before any Stage 6 sweep. Row w4-6. |
| R15 | Codex role reads | A Codex TOML sends the role to the lead skill only when its Claude role file does. `ammo-delegate.toml` and `ammo-report-writer.toml` read only their role files. test_codex_port.py exempts both. Rows w6-9 and the w5 pointer. |
| R16 | Tests | Each test file has one owner in wave 2 (see write sets). A worker whose prose edit breaks a pin outside its write set reports the exact assert to the lead instead of editing it. |

## Per-row decisions

Decision words: adopt, adopt-mod (adopted under the named ruling), reject.

### shared.md (w6)

| Row | Decision | Note |
|---|---|---|
| 1 | adopt | Wording only. The stamp hook stays open by the user's decision. |
| 2 | adopt | |
| 3 | adopt | |
| 4 | adopt | |
| 5 | adopt | |
| 6 | adopt | R10, user ruling |
| 7 | adopt | R9, user ruling. Rows in artifact-layout.md, artifact_layout.json, codex guard, and test fixtures go with it. |
| 8 | adopt | Defect fix: the copy loses the cycle-1 verdict. `stage_67.md` stays the first-cycle name. |
| 9 | adopt | R15 |
| 10 | adopt | R8 |
| 11 | adopt | |
| 12 | adopt | |
| 13 | adopt | |
| 14 | adopt-mod | R1 |
| 15 | adopt | Test change with R1 |
| 16 | adopt | |
| 17 | adopt | Same line as w5-19 |
| 18 | adopt | |
| 19 | adopt | |
| 20 | adopt | |
| 21 | adopt | R3 |
| 22 | adopt | |
| 23 | adopt | |
| 24 | adopt | |
| 25 | adopt | |
| 26 | adopt | |
| 27 | adopt | |

### stage-1.md (w1)

| Row | Decision | Note |
|---|---|---|
| 1 | adopt | |
| 2 | adopt | Graph lines 666-667 change with it |
| 3 | adopt | |
| 4 | adopt | |
| 5 | adopt-mod | R1 |
| 6 | adopt | |
| 7 | adopt | |
| 8 | adopt | |
| 9 | adopt | R2 |
| 10 | adopt | R2 |
| 11 | adopt | |
| 12 | adopt | |
| 13 | adopt | |
| 14 | adopt | |
| 15 | adopt | |
| 16 | adopt | R1 |
| 17 | adopt-mod | R5 |
| 18 | adopt | |
| 19 | adopt | |
| 20 | adopt | |
| 21 | adopt-mod | R1 move |
| 22 | adopt | |
| 23 | adopt | |
| 24 | adopt | |
| 25 | adopt | |
| 26 | adopt | |
| 27 | adopt | |
| 28 | adopt-mod | R1 move. The Neuron model list stays. |
| 29 | adopt | |
| 30 | adopt | |
| 31 | adopt | R2 |
| 32 | adopt | One edit with w3-32 |
| 33 | adopt | This wording wins over w3-31 (it keeps the reason) |
| 34 | adopt | |
| 35 | adopt | R1 |
| 36 | adopt | Sentence option. No launch change. |

### stage-3.md (w2)

| Row | Decision | Note |
|---|---|---|
| 1-3 | adopt | SKILL.md edits go to the SKILL.md owner |
| 4 | adopt-mod | R1 |
| 5-14 | adopt | |
| 15 | adopt | |
| 16-18 | adopt | |
| 19-20 | adopt | |
| 21-28 | adopt | |
| 29 | superseded | By row 30 |
| 30 | adopt | Update crossover-probing.md:36 pointer |
| 31-33 | adopt | |
| 34 | adopt | Align tests/test_verify_validation_gates.py:827 to the same shape |
| 35-39 | adopt | |
| stance field | reject | Three words, no measurable cost |

### stage-4-5.md (w3)

| Row | Decision | Note |
|---|---|---|
| 1 | adopt | Graph lines 494-498 change with it |
| 2 | adopt | |
| 3 | adopt-mod | R13 |
| 4 | adopt-mod | R1 |
| 5-6 | adopt | |
| 7 | adopt | validation-defaults.md owner adds the three items first |
| 8 | adopt-mod | R1 |
| 9-13 | adopt | |
| 14 | adopt | Intent map is read-only; note only |
| 15-21 | adopt | |
| 22 | adopt-mod | R4 |
| 23 | adopt | With 22 |
| 24 | adopt | Add the S45 bullet |
| 25 | adopt | |
| 26-30 | adopt | |
| 31 | adopt-mod | Use the w1-33 wording |
| 32 | adopt | One edit with w1-32 and w1-34 |
| 33 | adopt | R1 |
| 34 | adopt | R12 |
| 35-36 | adopt | |
| 37 | adopt | R1 |

### stage-6.md (w4)

| Row | Decision | Note |
|---|---|---|
| 1-5 | adopt | |
| 6 | adopt | R14 |
| 7 | adopt | |
| 8 | adopt | The reminder in ammo_state.py changes with it |
| 9 | adopt | |
| 10 | adopt | R6 |
| 11 | adopt-mod | R2: point to e2e-latency-guide.md § Tool Selection |
| 12 | adopt | |
| 13 | adopt | Graph line 513 lists the same checks |
| 14 | adopt | |
| 15 | adopt | The worker's version, with the shipped-list clause |
| 16-18 | adopt | |
| 19 | adopt | R6 |
| 20 | adopt | R4 naming |
| 21 | adopt | R9, user ruling |
| 22 | adopt-mod | R2 |
| 23 | adopt | R2 |
| 24 | adopt | |

### stage-7.md (w5)

| Row | Decision | Note |
|---|---|---|
| 1 | adopt | R7 |
| 2 | adopt | Mechanics: stop guard, reminder, graph, three fixtures |
| 3-5 | adopt | |
| 6 | adopt | R7 |
| 7 | adopt | Pointer option: champion-common-patterns.md and transcript-monitor.md |
| 8-9 | adopt | |
| 10 | adopt | R7 |
| 11-13 | adopt | |
| 14 | reject | R8 |
| 15-18 | adopt | |
| 19 | adopt | Same line as w6-17 |
| 20-26 | adopt | |

## Lead-owned items adopted

These sit outside every wave-1 scope. They go to the mechanics worker unless named otherwise.

- workflow.pave.yaml: 494-498 fallback pointer and rung (w3-1, R12); 513 pre-ship check list (w4-13); 666-667 "mechanical gate" to `ingest-baseline` (w1-2); 984-985 fact-check re-entry (w5-2).
- scripts/ammo_state.py reminders: 1871-1872 pre-ship list; 1929-1931 stop text and fact-check condition; 1938 "4-phase" to two phases; 1940-1943 `ingest-baseline` before `T_AUDIT_S1`; 2029-2036 `gated_pass` for a GATED_PASS passer and the check list; 2045-2059 name `advance`. Pins: tests/test_hook_semantics.py:941, tests/test_ammo_state.py:744-758, hooks/tests/test-ammo-next-step-reminder.sh:261-272.
- scripts/artifact_layout.json: 102, 158, 214 writer is `run_pumice_grpc_bench.py`; 258 eligibility description matches debate-protocol.md:87.
- scripts/run_pumice_grpc_bench.py:775 error text names slot `profiling` for Stage 1.
- hooks/ammo-pretool-guard.sh:83-90 also warns on `--no-compile` and `PUMICE_COMPILE_TARGET=0`.
- hooks/ammo-stop-guard.sh:56 and 83-88 (w5-2).
- references/codex-runtime-v1.md:9-11 and codex-runtime-v2.md:9-12 keep one pointer to context-recovery.md (w6-11).
- references/shutdown-protocol.md:31 "Phase C" becomes "the rebuttal step".
- references/artifact-layout.md:108, 230, 246-249 use the debate-protocol step names and record format.
- references/validation-defaults.md:351 `--profile-waves 1` replaces `--num-iters 1`; 486-498 drop the shouting and the false "enforces it mechanically" claim.
- agents/ammo-delegate.md:11 adds "except the fact-check verdict at Stage 7b".
- tests/test_guidance_semantic_contract.py:244 comment (w5-26).

## Deferred (outside this audit)

- scripts/verify_validation_gates.py:183-191 and :1877 expect the Stage 1 baseline under `rounds/{current_round}/sweeps/baseline`, which the R2 comparator rule contradicts in round 2 and later. Script change for a later wave.
- A preflight check that the pave-init plugin is installed (R11).
- Intent-map lines 413, 759, 1045 in ammo-server are stale after the Stage 4-5 edits; the map is read-only here.

## Rejected


- stage-7 row 14: R8.
- stage-3 `stance` frontmatter field: no measurable cost.
- SKILL.md:204 debate enrichment (w2 cross-scope): it writes schema fields; dropping it is a schema change outside this audit.
- Removing `run_vllm_bench_latency_sweep.py` (w1 cross-scope): runner `vllm` is live for gpt-oss.

## Wave 2 write sets

| Worker | Scope | Write set |
|---|---|---|
| worker-6 | Lead skill, audits, shared roles | skills/ammo/SKILL.md; orchestration/audit-protocol.md; agents/ammo-auditor.md; agents/ammo-delegate.md; agents/ammo-investigator.md; references/audit-invariants.md; references/shutdown-protocol.md; tests/test_guidance_semantic_contract.py |
| worker-1 | Stage 1 references and Neuron landing | agents/ammo-researcher.md; references/e2e-latency-guide.md; references/nsys-profiling-guide.md; references/workload-invocation.md; references/gpu-pool.md; references/accelerator-binding.md; tests/test_neuron_role_routing.py; tests/test_validation_report_v2.py |
| worker-2 | Stage 3 and Codex role files | agents/ammo-champion.md; orchestration/debate-protocol.md; references/e2e-delta-math.md; references/crossover-probing.md; references/fusion-feasibility-heuristics.md; references/champion-common-patterns.md; references/harness-binding.md; references/context-recovery.md; references/codex-runtime-v1.md; references/codex-runtime-v2.md; codex/agents/*.toml; codex/RUNTIME.md; tests/test_codex_port.py |
| worker-3 | Stages 4-5 | agents/ammo-impl-champion.md; agents/ammo-transcript-monitor.md; orchestration/parallel-tracks.md; references/impl-track-rules.md; references/cudagraph-safety.md; references/torch-compile-contract.md; references/session-development.md (delete); references/validation-defaults.md; tests/test_verify_validation_gates.py |
| worker-4 | Stages 6, 7, 7b | orchestration/integration-logic.md; agents/ammo-resolver.md; agents/ammo-report-writer.md; skills/ammo-report/SKILL.md; references/writing-style.md; references/artifact-layout.md; scripts/artifact_layout.json; tests/test_artifact_layout_manifest.py |
| worker-5 | Mechanics: hooks, scripts, graph | hooks/* (not hooks/README.md rows owned above); codex/hooks/*; scripts/ammo_state.py; scripts/run_pumice_grpc_bench.py; workflow.pave.yaml; references/campaign-env.md; planning-records/ammo/gen_hooks_json.py; tests/test_hook_semantics.py; tests/test_ammo_state.py; tests/test_env_default_guard_hook.py; tests/test_hooks_json.py; tests/test_env_inventory.py; tests/test_workflow_pave.py; hooks/tests/* |

Acceptance for every worker: `bash tests/run_all.sh` green from the plugin root, the owned quotes.tsv rows no longer match (`grep -F` loop prints nothing for adopted rows), 0 em dashes, no edit outside the write set. worker-1 also runs `tests/test_neuron_role_routing.py -m neuron`. Integration runs the Neuron-marked test after every worker is DONE, because the fixed pointer line lands in seven role files owned by four workers.
