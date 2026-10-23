# Cursor integration

Preferred: `mnemo install cursor` (needs `pip install engram-memory`). The files here are the copy-paste equivalents.

**Status: UNVERIFIED against a real Cursor.** No Cursor was available when this was written. Tested: the JSON/rule files Engram writes, merge/uninstall safety, and that the hook script ignores Cursor-style payloads without output. Not tested: that Cursor discovers `mcp.json` / `engram.mdc` at these locations, accepts this shape, reloads servers as described, or applies the rule. If something differs, the constants in `engram/install_hosts.py` are the one place to fix.

## What `mnemo install cursor` does

| Scope | Writes |
|---|---|
| `--scope user` (default) | `~/.cursor/mcp.json`: adds `mcpServers.engram`. No rule (Cursor has no file-based user rules). |
| `--scope project` (inside a git repo) | `<git root>/.cursor/mcp.json` (same entry) and `<git root>/.cursor/rules/engram.mdc` (an Engram-owned, always-apply rule telling the model to call `recall`/`remember`). |

Safety (same discipline as `mnemo install claude-code`): merges into an existing `mcp.json` without touching other servers or keys; idempotent (a second run writes nothing); the first change to an existing file keeps `mcp.json.engram-backup`; atomic writes, no symlinks followed; invalid JSON, a non-object `mcpServers`, a foreign `engram` server or a hand-written `engram.mdc` are refused (exit 2, nothing written); `mnemo uninstall cursor` removes only the entry and files Engram wrote (and a directory it leaves empty); `--dry-run` prints a diff. The interpreter is recorded as an absolute path with `-I -m engram.mcp_server`, so a project containing a fake `engram/` package is never imported. Hand-written rules and `.cursor/rules/engram_learned.mdc` (written by `mnemo export-rules`) are never touched. `--scope project` writes a machine-specific interpreter path into `mcp.json`: do not commit that file.

Restart Cursor or reload MCP servers afterwards and check Settings > MCP.

## Files

- `mcp.json`: the entry for `~/.cursor/mcp.json` or `.cursor/mcp.json`; replace `/ABSOLUTE/PATH/TO/python` with the interpreter where Engram is installed. A test keeps it identical to the installer's output.
- `engram.mdc`: the rule; identical to what `--scope project` writes (a test enforces it).
- `mcp.codex.toml`: the same server as a Codex `config.toml` table (see "Other MCP hosts").
- `hooks.example.json`: UNVERIFIED sketch of Cursor hooks. Engram's hook only understands Claude Code event names, so this does nothing useful today. `mnemo install cursor` never installs hooks.

## Briefing and learned rules

`mnemo export-rules` writes `.cursor/rules/engram_learned.mdc` (a separate, generated, always-apply file). Memories written from Claude Code in the same repository are available to Cursor's MCP `recall` tool because both use the same local daemon and store.

## Other MCP hosts (Codex, Gemini CLI, Grok, ...): MCP only

`mnemo install --print mcp` (JSON) or `mnemo install --print mcp --format toml` prints a snippet to stdout and writes nothing. These hosts get the `recall`/`remember` tools only: **no hooks**, so nothing is captured or injected automatically; add a sentence to the host's own instruction file asking the model to call `recall` and `remember`. Where the snippet goes (Codex `~/.codex/config.toml`, Gemini CLI `~/.gemini/settings.json`) is UNVERIFIED; check the host's MCP documentation.
