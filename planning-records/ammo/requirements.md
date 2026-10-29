# Requirements - ammo plugin (hand-port from ammo-server)

Decisions from the 2026-10-28 interview. Mechanisms live in `conventions.md`; work split and checks live in `plan.md`. This file holds only what the user decided and the facts that drove the decisions.

## Goal

Extract AMMO (Agentic Model-on-Machine Optimizer) from the `ammo-server` repository, branch `pumice-on-main`, into a standalone plugin at `/Users/jinhun/GitHub/pave/generated-plugins/ammo` so an AMMO campaign runs without the FastAPI server, the session container, the web UI, or S3. Same plugin shape as `generated-plugins/vllm-neuron-parity`.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Build method | Orchestrated hand-port into PAVE plugin shape. No pave-init campaign. A hand-written `workflow.pave.yaml` validated with `validate_pave.py`. `/simplify` ran over the plan before implementation (done 2026-10-28; its findings are folded into these three files). |
| 2 | Run location | Host-local. The user runs Claude Code or Codex on the GPU host inside a Pumice checkout. No SSH layer, no host lease. |
| 3 | Runtime and pin | Pumice-CUDA only. Re-pin from Pumice `9d126713` (vLLM 0.30) to Pumice mainline `769c752995b8a39b27df028176411d17c99ff441` (2026-10-28, Pumice publishes `REQUIRED_VLLM_VERSION = "0.32.0"`). The pin is an invocation-time input recorded in `target.json` and `state.json`, never graph text. Neuron code paths stay in scripts; v1 does not ship or test them (conventions §6). |
| 4 | Graph faithfulness | Stages and gates 1:1 as nodes: 7 stages, 4 audits, 5 binding gates, debate protocol, report + fact-check. No ceremony removed. `docs/ammo-intent-map.json` in ammo-server is the checklist. Amended 2026-10-29: a step may go when a prompt-audit record cites evidence that nothing reads its output (`prompt-audit/adoption.md` R9). First uses: the early Stage 4-5 partial verdicts and the resolver's own GSM8K and E2E runs. |
| 5 | Harnesses | Claude Code and Codex. |
| 6 | Environment | The user pre-builds Pumice, its venv, and model weights. A preflight node checks commit, pins, cargo build, venv, GPUs, model access, and fails closed naming the gap. |
| 7 | GPU ownership | `gpu_reservation.py` is the only lock. Two campaigns on one host cannot hold the same GPU. Mechanism: conventions §5. |
| 8 | Verification | Static battery green, a clean-room run inside a fresh container on `g6e-cdd`, and a real Stage 1 baseline there. Details: plan.md wave 2. |
| 9 | ammo-server | Untouched. The plugin is a copy; making the container install it is a later task. |
| 10 | Output | `generated-plugins/ammo`, plugin name `ammo`, marketplace entry, records under `planning-records/ammo`. Branch `ammo-plugin` in the pave repo, commit at the end, no push. |
| 11 | Smoke environment | `g6e-cdd` (4x L40S): a fresh plain container from image `ammo-server-pumice-cuda:latest` with no server, Pumice upgraded to the pin inside, plugin bind-mounted, Claude Code run inside. |
| 12 | Hooks | Campaign hooks only. Keep and drop lists: conventions §7. |
| 13 | Extras | Ship the serving P/D campaign and the report sub-skill with GSM8K data. Do not ship `eval/`, the Neuron skills bundle, or `tests/agents/` (unrun role-play prompts). |
| 14 | Workspace | One campaign lives at `<pumice-checkout>/.ammo/<campaign-id>/`. This replaces the server's `kernel_opt_artifacts/<session>/` root and the session id. Implementation tracks keep Agent worktree isolation. |

## Facts that drove the decisions

- The server tree renders one canonical tree into two dialects (`scripts/render_ammo_variants.py`: `DEST_MAP`, `PER_VARIANT`, `CODEX_RULES`). `_ammo_shared/` holds 15 of 20 references and 2 of 4 orchestration files; the rest, plus `new_target.py`, `ammo_state.py`, `transcript_filter.py`, exist only per variant and differ between dialects. The Claude dialect is the copy source (conventions §10).
- Server-side tests outside `_ammo_shared` cover plugin code and are ported, not rewritten: `tests/unit/test_ammo_serving*.py` (3 files), `test_pumice_pins.py`, `test_codex_hook_campaign_resolution.py`, `test_env_default_guard_hook.py`, `test_worktree_hook.py`.
- Pumice protos did not change between the two pins (`git diff --exit-code 9d126713 769c7529 -- pumice-rust/proto src/pumice/grpc` is empty). `src/pumice/config.py` and `rust_grpc_server.py` did. 145 commits between them. The previous re-pin needed 19 edits; `~/logs/pumice-verify/20261001-pumice-pull/ammo-touchpoints.md` lists 444 places AMMO encodes a Pumice fact.
- `g6e-cdd` host has cargo, python3, git, no `claude`, `codex`, `uv`, and no Pumice outside the running container `ammo-pumice-cuda-server`. The image carries `claude`.
- Source-tree test baseline on this Mac with the plan's pytest command: 1680 passed, 4 skipped, 1 xfailed, 1 failed (`test_sweep_profiler_guard::test_subprocess_baseline_nsys_exits_nonzero`, nsys not on PATH). Four hook harnesses fail on macOS (msg-check, is-lead, audit-phase2, compaction: flock and GNU stat). Hook harnesses are judged on Linux.

## Assumptions told to the user

- Internal names stay: `session_id` fields, `--session-id` flags, `AMMO_SESSION_ID`. A campaign id is their value. Only user-facing text says "campaign". Reason: about 18k lines of tests key on these names.
- The lead skill never writes the user's `.claude/settings.local.json`. Discovery goes through the `.ammo/active` marker.
