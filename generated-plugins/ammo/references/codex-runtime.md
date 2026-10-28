# Codex Execution Guide Selector

Read the guide for the campaign's agent mode before any agent dispatch,
message, or recovery:

- `AMMO_CODEX_AGENT_MODE=v1`: read `references/codex-runtime-v1.md`. This mode
  uses Amazon Bedrock and the `multi_agent_v1` tools.
- Otherwise: read `references/codex-runtime-v2.md`. This mode uses a ChatGPT
  subscription and the `agents` tools.

`codex/RUNTIME.md` lists the provider and tool settings for each mode. Set them
before you start Codex. Never change modes inside an active campaign. After a
resume or a compaction, the `SessionStart` hook injects the selected guide.
`references/harness-binding.md` maps each operation to both modes and to
Claude Code.
