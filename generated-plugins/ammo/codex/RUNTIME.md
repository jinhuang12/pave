# AMMO on Codex

This file tells you how to install and configure AMMO for Codex. The root
`README.md` gives the short install steps. This file gives the configuration,
the parts that the plugin ships for Codex, and the dependencies.

Codex needs two installs. `codex plugin add` installs the plugin with its
skills, `$ammo:ammo` and `$ammo:ammo-report`. `codex/install_agents.py` installs
the eleven custom agents into the Pumice checkout. The campaign does not run
without the agents.

## Install

Let `PLUGIN` be the plugin root and `CHECKOUT` be the Pumice checkout where the
campaign runs.

1. Install the plugin from a Codex marketplace that lists it:

   ```sh
   codex plugin add ammo@<marketplace>
   ```

2. Install the custom agents into the checkout:

   ```sh
   python3 "$PLUGIN/codex/install_agents.py" --project "$CHECKOUT"
   ```

   The installer copies the TOMLs in `codex/agents/` to
   `$CHECKOUT/.codex/agents/` and records their hashes in
   `.ammo-codex-agents.json` there. It does not overwrite a file that it did
   not install or that was changed. `--user` installs into
   `$CODEX_HOME/agents/` (default `~/.codex/agents/`) instead. `--uninstall`
   removes only the files it owns. `--force` replaces or removes conflicting
   files; use it only after you review them.

3. Trust the checkout. Accept the Codex trust prompt, or add this to
   `~/.codex/config.toml`:

   ```toml
   [projects."/absolute/path/to/pumice-checkout"]
   trust_level = "trusted"
   ```

4. Add the settings in the next section to `$CHECKOUT/.codex/config.toml`.

5. Verify the agents:

   ```sh
   python3 "$PLUGIN/codex/install_agents.py" --project "$CHECKOUT" --check
   ```

6. Restart Codex in the checkout. Open `/hooks`, review the campaign hooks, and
   trust them when their paths point into this plugin.

Run `install_agents.py` again after each plugin update, then run `--check`.

## Configuration

AMMO was tested with Codex 0.153.4. The tested model is `gpt-6-astra` with a
ChatGPT subscription, or `openai.gpt-6-astra` on Amazon Bedrock, with high
reasoning effort. The role TOMLs do not set `model` or `model_provider`. Each
agent uses the root agent's model and keeps its own reasoning effort. If your
provider does not offer that model, select one that it offers. Keep your own
credentials and provider settings.

**Subscription (V2 agent tools).** Use these settings:

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

**Amazon Bedrock (V1 agent tools).** Start from the settings above, then make
these changes:

- Set `model = "openai.gpt-6-astra"` and `model_provider = "amazon-bedrock"`.
- Set `model_providers.amazon-bedrock.aws.region` to your Bedrock region.
- Set `model_reasoning_summary = "none"`. Bedrock rejects reasoning summaries
  for this model.
- Set `features.multi_agent = true` and
  `features.multi_agent_v2.enabled = false`.
- Set `features.context_management.experimental_mode = false`.
- Start Codex with `AMMO_CODEX_AGENT_MODE=v1` in its environment.

Do not change the provider or the agent mode while a campaign is active. Do not
set private token-budget settings or a context-window size. Subscription uses
the experimental context management. Bedrock uses ordinary compaction. Both
modes use the file recovery contract in `references/context-recovery.md`.

## Agent Operations

`references/codex-runtime.md` selects the execution guide for the agent mode:
`references/codex-runtime-v2.md` for subscription or
`references/codex-runtime-v1.md` for Bedrock. After a resume or a compaction,
the `SessionStart` hook injects the selected guide. `references/harness-binding.md`
maps each agent operation to both modes and to Claude Code.
`references/context-recovery.md` owns task names, recovery notes, and long
commands.

## What the Plugin Ships for Codex

- Eleven custom-agent TOMLs in `codex/agents/`: `ammo-auditor`,
  `ammo-champion`, `ammo-delegate`, `ammo-impl-champion`, `ammo-implementer`,
  `ammo-investigator`, `ammo-report-writer`, `ammo-report`, `ammo-researcher`,
  `ammo-resolver`, and `ammo-transcript-monitor`. Each installs as
  `ammo:<file name>`, for example `ammo:ammo-auditor`.
- The nine role files in `agents/`. Each TOML is a bootstrap that reads one
  role file. `ammo-implementer` reads `agents/ammo-impl-champion.md`, and
  `ammo-report` reads `agents/ammo-report-writer.md`.
- Six Python hook modules in `codex/hooks/`: `common.py`, `session_start.py`,
  `pre_compact.py`, `pre_tool_use_guard.py`, `post_tool_use_guard.py`, and
  `stop_gate_guard.py`.
- `codex/hooks.json`, which registers `SessionStart`, `PreCompact`,
  `PreToolUse`, `PostToolUse`, `SubagentStart`, `SubagentStop`, and `Stop`.
  The `hooks` key in `.codex-plugin/plugin.json` registers this file with
  Codex. `hooks/README.md` lists each hook.
- Codex-only scripts in `codex/scripts/`:
  - `create_worktree_with_build.sh`: the lead runs it to make a track worktree
    under `$CHECKOUT/.codex/worktrees/` before it spawns the impl-champion.
  - `remove_worktree_cleanup.sh`: removes a track worktree.
  - `resolve_codex_transcript.py`: finds a champion's rollout file for its
    monitor.
  - `monitor_queue_ack.py`: marks transcript-monitor queue records as
    acknowledged or resolved.
  - `codex_agent_registry.py`: V1 only. It binds each logical task name and
    recovery note to the native UUID. The hooks write the registry at
    `$CODEX_HOME/ammo-agent-registry.json`, and Codex's own parent records
    verify each binding.
- `codex/instrumentation/sitecustomize.py`: added to `PYTHONPATH` for Nsight
  children only.

The Codex hooks use the shared plugin files too: `scripts/ammo_state.py` and
`schemas/state.schema.json` validate `state.json`, and `scripts/` holds the GPU
reservation helper. Keep the plugin tree whole. A partial copy breaks these
links.

## Dependencies

- A Codex release with plugins, project custom agents in `.codex/agents/`,
  native multi-agent work, and lifecycle hooks, each turned on with the
  settings above.
- Python 3.11 or later. The installer needs `tomllib`.
- A Git checkout. The hooks and the worktree scripts use `git`.
- `/bin/sh` and `/usr/bin/env`. The hook registration runs the modules through
  them.
- The Python `jsonschema` package for full `state.json` validation. Without it
  the hooks report that schema validation was skipped. That is degraded
  operation.
- The pre-built Pumice checkout, its venv, and the GPUs that the root `README.md`
  lists. The plugin does not bundle them.
- `references/campaign-env.md` lists each environment name that the hooks and
  scripts read.

The campaign can change files in the checkout. Install the plugin where
campaign agents cannot change it, so that they cannot change its hooks or
`schemas/state.schema.json`.
