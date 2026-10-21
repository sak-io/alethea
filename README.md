# Engram

Long-term memory for AI coding agents, modeled on how human memory works.

Each AI session keeps its own context window. Engram is the layer that survives after the session ends: it remembers what mattered, abstracts specifics into durable facts, and lets the rest fade. It is a local daemon with a SQLite store, a command-line tool (`mnemo`) and an MCP server.

- Pure Python standard library plus SQLite. No pip installs, no network calls, no embedding API.
- MCP-capable hosts (Claude Code, Cursor, ...) can call the `recall`, `remember`, `reflect`, `forget` and `engram_status` tools voluntarily today. Claude Code hook wiring now exists (`.claude/settings.json`, `.mcp.json`, see `integrations/claude-code/README.md`): prompts and replies are journaled and a briefing is injected at session start. Contributors: hooks run in this repo's own sessions and journal your prompts (redacted) to `~/.engram`; opt-out steps are in that README.

## Status

Built and tested: the core engine (storage, encode gate and dedup, recall, activation and decay, consolidation and sleep cycle, redaction), the daemon (Unix socket, optional token-authenticated TCP), the fail-soft client, the stdio MCP server and the `mnemo` CLI.

Claude Code hooks and wiring are built (T22, T23). Not yet built: `export-rules` sync-back to host rule files (T24; the command exists but reports "not yet available"), `reembed`, and the optional plugins (learned embedder, LLM-assisted consolidation). Recall uses a hashed n-gram embedder, so paraphrases that share no words with a memory can miss.

## Requirements

- POSIX only (Linux, macOS, BSD). On other platforms the daemon refuses to start (exit 2) and the client, CLI and MCP server report `unsupported platform: POSIX only`.
- Python 3.9+ with SQLite compiled with FTS5.

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
```

The daemon keeps running until you stop it: `kill <pid>`, with the pid shown by `mnemo status`. `mnemo` exits 0 when the daemon is down; use `--strict` or `MNEMO_STRICT=1` for non-zero exit codes. `mnemo --help` lists all commands.

### Use it from Claude Code (MCP)

The MCP server does not start the daemon, so run `mnemo up` first. Then register the server in a project-level `.mcp.json`, using absolute paths:

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
