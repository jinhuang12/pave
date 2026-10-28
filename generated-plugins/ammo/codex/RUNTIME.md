# AMMO Codex runtime installation

This export is installed in two parts. **Copying only the skill directory is not
runnable**: the AMMO workflow names custom agents and relies on lifecycle hooks,
hook registration, and a trusted state schema that live outside the skill root.

## Install both parts

Let `AMMO_EXPORT` be this export directory and `PROJECT` be the consuming Git
project root.

1. Copy the export's skill content into
   `$PROJECT/.codex/skills/ammo/`. The packaging-only `runtime-overlay/`
   directory does not need to be nested inside the installed skill.
2. Overlay `runtime-overlay/.codex/` onto `$PROJECT/.codex/`, preserving the
   `agents/`, `hooks/`, and `schemas/` subdirectories and `hooks.json`.
3. Start a fresh Codex project session (or reload the runtime) so the new agent
   definitions and hook registration are discovered.

For example:

```sh
mkdir -p "$PROJECT/.codex/skills/ammo" "$PROJECT/.codex"
rsync -a --exclude '/runtime-overlay/' "$AMMO_EXPORT/" "$PROJECT/.codex/skills/ammo/"
cp -a "$AMMO_EXPORT/runtime-overlay/.codex/." "$PROJECT/.codex/"
```

The example uses `rsync` only as an install-time copy tool; an equivalent copy
that omits the packaging-only overlay from the skill destination is fine.

## Runtime configuration

Use Codex **0.153.4**. AMMO selects `gpt-6-astra` with a ChatGPT subscription or `openai.gpt-6-astra` on Amazon Bedrock, with high reasoning effort.
AMMO role TOMLs omit `model` and `model_provider`; they inherit the consuming root's selection and keep their role-specific reasoning effort.
A standalone consumer must select a model available through its own provider. Preserve its credentials and provider settings.

AMMO server sessions select **AI access** at creation. Bedrock is the default;
the access choice remains fixed on resume. The server uses its existing AWS
credentials or ChatGPT login. It validates the native account/model metadata;
Bedrock also requires an authenticated Mantle model-list read. No credential
values enter the UI or session metadata.

The server generates both configuration layers from
`orchestration/codex_runtime.py`. `AMMO_CODEX_BEDROCK_REGION` sets the region for
new Bedrock sessions (default `us-west-2`); a saved session keeps its region.
This does not change the AWS region used by other AMMO services.

For a standalone Subscription export, use the packaged V2 settings:

```toml
[features]
multi_agent = false
hooks = true
shell_tool = true
unified_exec = true

[features.context_management]
experimental_mode = true

[features.multi_agent_v2]
enabled = true
hide_spawn_agent_metadata = false
tool_namespace = "agents"
max_concurrent_threads_per_session = 17

[agents]
max_concurrent_threads_per_session = 16
max_depth = 3
job_max_runtime_seconds = 7200
```

For a standalone Bedrock export, select `model = "openai.gpt-6-astra"`,
`model_provider = "amazon-bedrock"`, and `model_providers.amazon-bedrock.aws.region`.
Set `model_reasoning_summary = "none"`; Bedrock Astra rejects reasoning summaries.
Set `features.multi_agent = true`, `features.multi_agent_v2.enabled = false`,
and `features.context_management.experimental_mode = false`. Keep the other
settings above. Launch with `AMMO_CODEX_AGENT_MODE=v1` and install
`references/codex-runtime-v1.md` as `references/codex-runtime.md`.

Subscription uses the supported experimental context configuration; Bedrock
uses ordinary compaction. Both modes preserve the same AMMO workflow.
Do not force private token-budget settings or a context-window size. Both paths use the file recovery contract in `references/context-recovery.md`.

Tracked `exec_command` launches and final completion run the Bash hooks. AMMO uses `tty=false`, empty `write_stdin` polling, and owned interruption.
The non-TTY rule is a workflow instruction: the hook payload exposes the command, not the TTY setting.

## Managed server installation

The server image separately installs the complete `.codex` tree at `/opt/codex-managed-hooks` and `requirements.toml` at `/etc/codex/requirements.toml`.
Managed requirements enable `unified_exec` and require managed hooks only. A project setting cannot override a conflicting managed requirement.
Rebuild the image and verify those installed files as well as the project template and generated `CODEX_HOME/config.toml`.

Project hooks alone do not provide the server's root-owned enforcement boundary. A standalone managed installation must use a trusted owner, the complete hook dependency tree, and its actual Python/interpreter paths.
Start a fresh Codex root after changing the runtime. Keep active campaigns on their installed runtime until a separate migration is validated.

## Runtime manifest

The overlay installs:

- 11 custom-agent TOMLs under `.codex/agents/`: `ammo-auditor`,
  `ammo-champion`, `ammo-delegate`, `ammo-impl-champion`, `ammo-implementer`,
  `ammo-investigator`, `ammo-report-writer`, `ammo-report`, `ammo-researcher`,
  `ammo-resolver`, and `ammo-transcript-monitor`.
- Six Python hook modules under `.codex/hooks/`: `common.py`,
  `session_start.py`, `pre_compact.py`, `pre_tool_use_guard.py`,
  `post_tool_use_guard.py`, and `stop_gate_guard.py`.
- `.codex/hooks.json`, which registers `SessionStart`, `PreCompact`,
  `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop`.
- `.codex/schemas/state.schema.json`, loaded by the post-tool state guard from
  the schema directory beside the installed hooks.

There are nine canonical role documents in the skill. The extra TOMLs are
compatibility aliases: `ammo-impl-champion` reads
`agents/ammo-implementer.md`, and `ammo-report-writer` reads
`agents/ammo-report.md`. No duplicate alias Markdown files are required.

V1 additionally uses `scripts/codex_agent_registry.py` to bind logical task
names and recovery notes to native UUIDs. The registry persists in
`CODEX_HOME/ammo-agent-registry.json`; the hooks own writes. Native SQLite
parent edges verify each binding. Copy the full scripts tree with the hooks.

The hook-to-skill references close only when the two install parts use the
documented layout. In particular, hooks resolve
`scripts/ammo_state.py`, `gpu_reservation.py`, and
`monitor_queue_ack.py`, as well as the skill's agent, orchestration, reference,
and report contracts. Moving either tree independently breaks that closure.

## Operational dependencies

- A Codex release that supports project-local skills, `.codex/agents/*.toml`,
  native multi-agent execution, and Codex lifecycle hooks. Native multi-agent
  support must be enabled in the consuming runtime using the configuration
  appropriate to that Codex release.
- A Git project root. Hook root discovery and AMMO worktree operations use
  `git` and project-relative `.codex` paths.
- `/bin/sh`, `/usr/bin/env`, and Python 3. The hook registration invokes the
  scripts through these system tools.
- The Python `jsonschema` package for full `state.schema.json` enforcement.
  Hooks can report that schema validation was skipped when it is unavailable,
  but that is degraded operation rather than semantic parity.
- The installed AMMO skill scripts and their project Python environment. AMMO
  worktree commands expect the worktree virtual environment; GPU benchmarks
  additionally require the consuming project's CUDA, vLLM, and profiling
  stack. Those workload dependencies are not bundled in this overlay.
- Writable campaign artifact/worktree locations and temporary storage. GPU
  sessions should provide their normal `CUDA_VISIBLE_DEVICES`,
  `AMMO_SESSION_ID`, `AMMO_GPU_RES_DIR`, and `AMMO_PROJECT_DIR` integration
  values; the package contains no site-specific values.

For hardened deployments, install `.codex/hooks/` and `.codex/schemas/` as
policy assets that campaign subagents cannot modify. In particular,
`state.schema.json` must remain a regular, non-symlink file beside the hooks.
