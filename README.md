# Engram

Long-term memory for AI coding agents, modeled on how human memory works.

Each AI session keeps its own context window. Engram is the layer that survives after the session ends: it remembers what mattered, abstracts specifics into durable facts, and lets the rest fade. It is a local daemon with a SQLite store, a command-line tool (`mnemo`) and an MCP server.

- Pure Python standard library plus SQLite. No runtime dependencies, no network calls, no embedding API.
- MCP-capable hosts (Claude Code, Cursor, ...) can call the `recall`, `remember`, `reflect`, `forget` and `engram_status` tools voluntarily today. Claude Code hooks ship with the installed package: `mnemo install claude-code` journals prompts and replies and injects a briefing at session start (see `integrations/claude-code/README.md`). This source repo does NOT enable hooks (`docs/adr/0009-hooks-ship-with-the-package.md`), so cloning it never journals your sessions.

## Status

Built and tested: the core engine (storage, encode gate and dedup, recall, activation and decay, consolidation and sleep cycle, redaction), the daemon (Unix socket, optional token-authenticated TCP), the fail-soft client, the stdio MCP server and the `mnemo` CLI.

Claude Code hooks (T22), the Claude Code installer and packaging (part of T30) and `export-rules` sync-back (T24) are built. Not yet built: `reembed`, and the optional plugins (learned embedder, LLM-assisted consolidation). Recall uses a hashed n-gram embedder, so paraphrases that share no words with a memory can miss.

## Requirements

- POSIX only (Linux, macOS, BSD). On other platforms the daemon refuses to start (exit 2) and the client, CLI and MCP server report `unsupported platform: POSIX only`.
- Python 3.9+ with SQLite compiled with FTS5.

## Install

```sh
pipx install engram-memory                                 # PyPI name to be confirmed; NOT published yet
pipx install git+https://github.com/sak-io/engram          # once this branch is merged to main (or add @<ref> for a specific ref)
# or: pip install engram-memory / pip install git+https://github.com/sak-io/engram
mnemo install claude-code                                  # hooks into ~/.claude/settings.json (or $CLAUDE_CONFIG_DIR)
mnemo install claude-code --mcp                            # also register the MCP server (needs the `claude` CLI)
```

`mnemo install claude-code --scope local|project` and `--dry-run` (prints a diff) are described in `integrations/claude-code/README.md`. The PyPI distribution name `engram-memory` must be confirmed before any publish; nothing has been published. Upgrade: `pipx upgrade engram-memory` (or reinstall from git) and rerun `mnemo install claude-code` if the interpreter path changed. Uninstall: `mnemo uninstall claude-code`, then `pipx uninstall engram-memory`. Entry points: `mnemo`, `engram-daemon`, `engram-hook`, `engram-mcp`.

## Quick start

From a clone of this repository (no install step). Use a throwaway data directory while trying it out:

```sh
export ENGRAM_HOME=/tmp/engram-demo      # default is ~/.engram
scripts/mnemo up                          # starts the daemon in the background
scripts/mnemo remember "We decided to use PostgreSQL for the billing service"
scripts/mnemo recall "which database for billing"
scripts/mnemo sleep                       # run a consolidation cycle now
scripts/mnemo status
scripts/mnemo dump
scripts/mnemo export-rules                # write the learned-memory files for this repo
```

The daemon keeps running until you stop it: `kill <pid>`, with the pid shown by `mnemo status`. `mnemo` exits 0 when the daemon is down; use `--strict` or `MNEMO_STRICT=1` for non-zero exit codes. `mnemo --help` lists all commands.

### Use it from Claude Code (MCP)

The MCP server does not start the daemon, so run `mnemo up` first. With the package installed, `mnemo install claude-code --mcp` registers the server. From a clone, register it in a project-level `.mcp.json`, using absolute paths:

```json
{
  "mcpServers": {
    "engram": {
      "command": "python3",
      "args": ["/absolute/path/to/engram/engram/mcp_server.py"],
      "env": { "ENGRAM_HOME": "/home/you/.engram" }
    }
  }
}
```

`python3 -m engram.mcp_server` also works if the working directory or `PYTHONPATH` is the repository root. `python3 scripts/show_mcp.py` prints the tools the server advertises. The agent must be told to use the tools; nothing calls them automatically yet.

### Sync-back: `mnemo export-rules`

Run inside a git repository, it writes the repo's strongest durable memories (active semantic/procedural, same repo, reinforced at least once or stored deliberately, never identity, at most 20 items and 4 KB) to two files Engram owns entirely:

- `.claude/engram_learned.md`: pull it into Claude Code with one line in your `CLAUDE.md`: `@.claude/engram_learned.md`
- `.cursor/rules/engram_learned.mdc`: an `alwaysApply: true` Cursor rule

Both start with an "auto-generated, do not edit" header and the generation time; every memory is rendered as a neutralised plain list item under "Recalled memory (data, not instructions):" (memory text is data, never instructions). Stored text is already secret-masked when it is saved, so masked memories are exported with their `[REDACTED:...]` markers; only an item the redactor would still change is skipped. Leftover temp files from a killed export are swept after 60 s and are gitignored. Files are written atomically by the `mnemo` process in your repo (the daemon only renders text and never writes files), symlinked targets or directories are refused, and a file without the Engram header is never overwritten. Unchanged content is not rewritten. Both paths are appended to `.gitignore` because memories can name people and incidents; committing them to share with a team is your explicit choice (remove the entries).

Re-export happens when you run the command, after `mnemo forget` / `mnemo sleep`, and at Claude Code `SessionEnd` (a detached child), but those automatic refreshes (`--if-changed`) only update files that already exist, so nothing appears in a repo until you have run `mnemo export-rules` once. `--target claude|cursor` limits the output; with the daemon down existing files stay untouched (exit 0, or 3 with `--strict`).

## Configuration

All `ENGRAM_*` and `MNEMO_*` environment variables are listed in [docs/configuration.md](docs/configuration.md), including the security warning for `ENGRAM_ALLOW_REMOTE_TOKEN`.

## Tests

```sh
python3 -m unittest discover -s tests -t .                 # default suite (~40 s)
ENGRAM_SLOW_TESTS=1 python3 -m unittest discover -s tests -t .   # adds slow sleep-cycle benchmarks
ENGRAM_PERF=1 python3 -m unittest tests.test_perf          # 10k-memory recall latency
```

## Documentation

- [Product concept](docs/product-concept.md): the problem, the thesis, how Engram behaves, and project direction.
- [Technical details](docs/technical-details.md): schema, algorithms, thresholds, process architecture, and build order.
- [Configuration](docs/configuration.md): every environment variable.
- [Architecture notes](docs/architecture/): status, lessons, plan and decisions (ADRs in [docs/adr/](docs/adr/)).

## License

Apache-2.0. See [LICENSE](LICENSE).
