# Alethea

Long-term memory for AI coding agents, modeled on how human memory works.

The name: Alethea comes from the Greek *aletheia*, "un-forgetting" (the opposite of Lethe, the river of forgetting). In neuroscience an engram is a memory trace, which is what this project stores.

Each AI session keeps its own context window. Alethea is the layer that survives after the session ends: it remembers what mattered, abstracts specifics into durable facts, and lets the rest fade. It is a local daemon with a SQLite store, a command-line tool (`mnemo`) and an MCP server.

- Pure Python standard library plus SQLite. No runtime dependencies; by default no network calls and no embedding API (remote embedding and LLM distillation exist but are off and need explicit opt-in).
- MCP-capable hosts (Claude Code, Cursor, ...) can call the `recall`, `remember`, `reflect`, `forget` and `alethea_status` tools voluntarily today. Claude Code hooks ship with the installed package: `mnemo install claude-code` journals prompts and replies and injects a briefing at session start (see `integrations/claude-code/README.md`). This source repo does NOT enable hooks (`docs/adr/0009-hooks-ship-with-the-package.md`), so cloning it never journals your sessions.

## Status

Built and tested: the core engine (storage, encode gate and dedup, recall, activation and decay, consolidation and sleep cycle, redaction), the daemon (Unix socket, optional token-authenticated TCP), the fail-soft client, the stdio MCP server and the `mnemo` CLI.

Claude Code hooks (T22), the Claude Code installer and packaging (part of T30) and `export-rules` sync-back (T24) are built. The remote OpenAI-compatible embedder and `mnemo reembed` (T26) are built but verified only against stub servers (no real provider was contacted). Optional LLM-assisted consolidation (T27, off by default; host-callback mode, or direct API with `ALETHEA_LLM_API`) is built and stub-tested only; distilled notes never become exported or briefed rules until reinforced, and API mode sends redacted transcripts to the configured host (see `docs/configuration.md`, ADR 0012). Recall uses a hashed n-gram embedder by default, so paraphrases that share no words with a memory can miss. An optional local embedder (`pip install 'alethea[local]'`, `ALETHEA_EMBEDDER=fastembed:sentence-transformers/all-MiniLM-L6-v2`, runs on-device) is wired in and fail-soft, but its real-model path is verified only up to the package import and with stubbed embeddings, and its cosine distributions are unmeasured, so it ships with the conservative hashed thresholds; see `docs/configuration.md`. `ALETHEA_EMBEDDER=openai:<model>` is off by default and sends memory text to a third party (redacted, best-effort) only with `ALETHEA_EMBEDDER_ALLOW_REMOTE=1` and an API key in `ALETHEA_EMBEDDER_API_KEY`; read the privacy warning in `docs/configuration.md` first.

## Requirements

- POSIX only (Linux, macOS, BSD). On other platforms the daemon refuses to start (exit 2) and the client, CLI and MCP server report `unsupported platform: POSIX only`.
- Python 3.9+ with SQLite compiled with FTS5.

## Install

**Not on PyPI yet** (the distribution name `alethea` was verified free on 2026-10-24; see [docs/traction-checklist.md](docs/traction-checklist.md)). Until it is published, install from a checkout or a built wheel:

```sh
git clone https://github.com/sak-io/alethea && cd alethea
python3 -m pip wheel . --no-deps -w dist                       # builds dist/alethea-<version>-py3-none-any.whl
python3 -m venv .venv && .venv/bin/pip install dist/alethea-*.whl
export PATH="$PWD/.venv/bin:$PATH"                             # puts mnemo, alethea-daemon, alethea-hook, alethea-mcp on PATH
```

(`pipx install dist/alethea-*.whl` works the same way.) The local embedder is an extra: `pip install 'dist/alethea-<version>-py3-none-any.whl[local]'`. Upgrade: rebuild and reinstall, then rerun `mnemo install claude-code` if the interpreter path changed. Uninstall: `mnemo uninstall claude-code` (and `mnemo uninstall cursor`), then remove the venv. Entry points: `mnemo`, `alethea-daemon`, `alethea-hook`, `alethea-mcp`.

## Quick start

Every command below was run in a clean venv from a built wheel (Python 3.13, Linux). Use a throwaway data directory and, for the last steps, a throwaway repository:

```sh
export ALETHEA_HOME=/tmp/alethea-demo      # default is ~/.alethea
mnemo up                                  # starts the daemon in the background
mnemo remember "We decided to use PostgreSQL for the billing service"
mnemo recall "which database for billing"
mnemo sleep                               # run a consolidation cycle now
mnemo status
mnemo dump
git init /tmp/alethea-demo-repo && cd /tmp/alethea-demo-repo
mnemo export-rules                        # write the learned-memory files for this repo
mnemo install claude-code --dry-run       # show the hooks diff; nothing is written
mnemo install --print mcp                 # MCP-only config snippet (see below)
```

The daemon keeps running until you stop it: `kill <pid>`, with the pid shown by `mnemo status`. `mnemo` exits 0 when the daemon is down; use `--strict` or `MNEMO_STRICT=1` for non-zero exit codes. `mnemo --help` lists all commands; every command, exit code, daemon verb and MCP tool is in [docs/api.md](docs/api.md).

Without installing, from a clone, `scripts/mnemo <command>` runs the same CLI.

### Install the hooks (Claude Code)

`mnemo install claude-code` merges five hooks into `~/.claude/settings.json` (or `$CLAUDE_CONFIG_DIR`), so Claude Code journals prompts and final replies and injects a briefing at session start; `--scope local|project` and `--mcp` are described in `integrations/claude-code/README.md`. `mnemo install claude-code --status` shows where it is installed. These hooks have not yet been exercised in a live Claude Code session (see the checklist).

### MCP-only hosts (Codex, Gemini CLI, Grok, others)

Hosts without a hook interface get the MCP tools only: `recall`, `remember`, `reflect`, `forget`, `alethea_status`. Nothing is captured or injected automatically, so tell the model (in the host's rules or instructions file) to call `recall` before starting work and `remember` for decisions. Print the server entry and paste it into the host's MCP configuration:

```sh
mnemo install --print mcp                 # JSON mcpServers entry
mnemo install --print mcp --format toml   # Codex-style [mcp_servers.alethea] table
```

It writes nothing. Where each host wants the snippet (Codex `~/.codex/config.toml`, Gemini CLI `~/.gemini/settings.json`) is unverified; check the host's documentation. Cursor has its own installer, `mnemo install cursor` (MCP entry plus, with `--scope project`, a rule file; also unverified against a real Cursor, see `integrations/cursor/README.md`). Start the daemon with `mnemo up` first: the MCP server does not start it.

### Use it from Claude Code (MCP)

The MCP server does not start the daemon, so run `mnemo up` first. With the package installed, `mnemo install claude-code --mcp` registers the server. From a clone, register it in a project-level `.mcp.json`, using absolute paths:

```json
{
  "mcpServers": {
    "alethea": {
      "command": "python3",
      "args": ["/absolute/path/to/alethea/alethea/mcp_server.py"],
      "env": { "ALETHEA_HOME": "/home/you/.alethea" }
    }
  }
}
```

`python3 -m alethea.mcp_server` also works if the working directory or `PYTHONPATH` is the repository root. `python3 scripts/show_mcp.py` prints the tools the server advertises. The agent must be told to use the tools; nothing calls them automatically yet.

### Sync-back: `mnemo export-rules`

Run inside a git repository, it writes the repo's strongest durable memories (active semantic/procedural, same repo, reinforced at least once or stored deliberately, never identity, at most 20 items and 4 KB) to two files Alethea owns entirely:

- `.claude/alethea_learned.md`: pull it into Claude Code with one line in your `CLAUDE.md`: `@.claude/alethea_learned.md`
- `.cursor/rules/alethea_learned.mdc`: an `alwaysApply: true` Cursor rule

Both start with an "auto-generated, do not edit" header and the generation time; every memory is rendered as a neutralised plain list item under "Recalled memory (data, not instructions):" (memory text is data, never instructions). Stored text is already secret-masked when it is saved, so masked memories are exported with their `[REDACTED:...]` markers; only an item the redactor would still change is skipped. Leftover temp files from a killed export are swept after 60 s and are gitignored. Files are written atomically by the `mnemo` process in your repo (the daemon only renders text and never writes files), symlinked targets or directories are refused, and a file without the Alethea header is never overwritten. Unchanged content is not rewritten. Both paths are appended to `.gitignore` because memories can name people and incidents; committing them to share with a team is your explicit choice (remove the entries).

Re-export happens when you run the command, after `mnemo forget` / `mnemo sleep`, and at Claude Code `SessionEnd` (a detached child), but those automatic refreshes (`--if-changed`) only update files that already exist, so nothing appears in a repo until you have run `mnemo export-rules` once. `--target claude|cursor` limits the output; with the daemon down existing files stay untouched (exit 0, or 3 with `--strict`).

## Configuration

All `ALETHEA_*` and `MNEMO_*` environment variables are listed in [docs/configuration.md](docs/configuration.md), including the security warning for `ALETHEA_ALLOW_REMOTE_TOKEN`.

## Tests

```sh
python3 -m unittest discover -s tests -t .                 # default suite (~40 s)
ALETHEA_SLOW_TESTS=1 python3 -m unittest discover -s tests -t .   # adds slow sleep-cycle benchmarks
ALETHEA_PERF=1 python3 -m unittest tests.test_perf          # 10k-memory recall latency
```

## Documentation

- [Product concept](docs/product-concept.md): the problem, the thesis, how Alethea behaves, and project direction.
- [Technical details](docs/technical-details.md): schema, algorithms, thresholds, process architecture, and build order.
- [Configuration](docs/configuration.md): every environment variable.
- [Architecture](docs/architecture.md): how the engine, daemon, client, MCP server, CLI, hooks, sleep cycle, plugins, installer and Docker image fit together.
- [API reference](docs/api.md): daemon verbs, MCP tools, `mnemo` commands and exit codes.
- [Threat model](docs/threat-model.md): assets, trust boundaries, defences against hostile memory text, consent for remote calls, known gaps.
- [Docker sidecar](docs/docker.md) and [traction checklist](docs/traction-checklist.md) (what to verify before announcing).
- [Reviewer's guide](docs/review-guide.md).
- [Architecture notes](docs/architecture/): status, lessons, plan and decisions (ADRs in [docs/adr/](docs/adr/)).

## License

Apache-2.0. See [LICENSE](LICENSE).
