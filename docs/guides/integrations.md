# Integrations: which host gets what

An index, not a manual. The step-by-step instructions live in the integration READMEs and the root [README](../../README.md#quick-start); this page tells you which one to open.

| Host | What you get | Install | Details | Verified against a real host? |
|---|---|---|---|---|
| Claude Code | Automatic capture of prompts and replies, a briefing at session start, optional MCP tools | `mnemo install claude-code` (add `--mcp` for the tools) | [integrations/claude-code/README.md](../../integrations/claude-code/README.md), [hooks reference](../architecture/claude-code-hooks-reference.md) | No. Hooks were run by invoking the installed command with documented payloads, not in a live session. |
| Cursor | MCP tools; with `--scope project` an always-apply rule; learned rules via `mnemo export-rules` | `mnemo install cursor` | [integrations/cursor/README.md](../../integrations/cursor/README.md) | No. Tests use a temporary HOME. |
| Codex, Gemini CLI, Grok, other MCP hosts | MCP tools only; nothing is captured or injected automatically | `mnemo install --print mcp` (JSON) or `--format toml`, then paste the snippet into the host's config | [root README](../../README.md#mcp-only-hosts-codex-gemini-cli-grok-others) | No. Where each host wants the snippet is unchecked. |
| Any container or remote setup | The daemon in a container, reached over token-authenticated TCP | `docker compose up` | [Docker guide](docker.md) | Partly. See the guide. |

All of these talk to the same local daemon and store, which is why a decision recorded in Claude Code can be recalled from Cursor in the same repository. The MCP server never starts the daemon; run `mnemo up` first.

Every `mnemo` command and MCP tool is listed in the [API reference](../reference/api.md). Uninstall with `mnemo uninstall claude-code` or `mnemo uninstall cursor`; both remove only Alethea's own entries.
