# Configuration

Engram is configured with environment variables. Precedence: environment, then `<ENGRAM_HOME>/config.json`, then defaults. Four keys can also be set in `config.json` (marked "file" below); the rest are environment-only.

**Platform: POSIX only** (Linux, macOS, BSD). The daemon needs `fcntl` and `AF_UNIX`. On a platform without them the daemon exits with code 2 and the message `unsupported platform: POSIX only`; `mnemo`, the client and the MCP server stay fail-soft (exit 0, or an "offline" result) and report the same message.

Boolean variables accept `1`, `true`, `yes`, `on` (case-insensitive); anything else is false.

| Variable | Default | Read by | Meaning |
|---|---|---|---|
| `ENGRAM_HOME` | `~/.engram` | all | Data directory (database, socket, lock files, token). Created with mode 0700. Set it to a temp dir for experiments and tests. |
| `ENGRAM_TOKEN` | unset | daemon, client | Shared secret for TCP connections (at least 16 bytes). If unset, the daemon creates a 0600 file `<home>/token`; the client reads the same file and ignores it when group/world readable. Unix-socket connections do not need it. Never printed. |
| `ENGRAM_BIND` | unset | daemon | Address for the optional TCP listener. Setting it (or `ENGRAM_PORT`/`ENGRAM_CONTAINER`) enables TCP in addition to the Unix socket. A non-loopback bind requires a token. |
| `ENGRAM_PORT` | unset | daemon, client | TCP port (0-65535; 0 picks a free port). On the client, setting it selects TCP and disables autostart. |
| `ENGRAM_HOST` | `127.0.0.1` (or `daemon.json`) | client | Host the client connects to over TCP. IP literals only; `localhost` maps to 127.0.0.1; names are never resolved. |
| `ENGRAM_CONTAINER` | false | daemon, client | Sidecar/container mode: TCP only (no Unix socket), requires explicit `ENGRAM_BIND`, `ENGRAM_PORT` and a token. The client never autostarts a daemon in this mode. |
| `ENGRAM_ALLOW_REMOTE_TOKEN` | false | client | **Security warning.** By default the client sends the token only to loopback addresses or the Unix socket. Setting this to true sends it to a non-loopback host in **plaintext** (there is no TLS), where anyone on the network path can read it. Use only on a trusted network or through a tunnel (SSH, VPN). |
| `ENGRAM_MAX_TURN_BYTES` | `262144` | daemon (encode) | Per-turn input cap in bytes applied before redaction and journaling (minimum 1024). Read from the daemon process environment. |
| `ENGRAM_DEDUP_FTS_WINDOW` | `2000` | daemon (encode) | Newest rows (by rowid) the duplicate check's full-text search may rank. Exact while the store has this many rows or fewer; beyond it an old near-duplicate can be stored as a second copy (never a false merge). Memories created while capped carry `meta.dedup_window_capped`. Set very large to test whether the cap caused a missed duplicate. See ADR 0008. |
| `ENGRAM_SLEEP_INTERVAL` (file) | `900` | daemon | Minimum seconds between automatic sleep cycles. |
| `ENGRAM_IDLE_SLEEP` (file) | `180` | daemon | Seconds of inactivity required before an automatic sleep cycle starts. |
| `ENGRAM_EMBEDDER` (file) | `hash` | daemon | **Reserved (not yet implemented).** Only the built-in hashed n-gram embedder exists. A non-default value is ignored and the daemon prints one warning to stderr at startup. |
| `ENGRAM_LLM_CONSOLIDATE` (file) | false | MCP server, daemon | **Reserved (not yet implemented).** No LLM consolidation exists. The only effect today is that the MCP server also advertises the `engram_consolidate_pending` tool (which runs the normal extractive consolidation). The daemon prints one stderr warning if it is set. |
| `ENGRAM_HOOK_DEBUG` | unset | Claude Code hook | `1` makes `engram/hooks/engram_hook.py` write one diagnostic line to stderr (event outcome and elapsed ms; never prompt text, tokens or memory content). Otherwise the hook is silent: stdout is empty or one JSON object, exit code always 0. |
| `MNEMO_STRICT` | unset | `mnemo` | `1` makes `mnemo` exit 3 when the daemon is offline and 4 for commands not yet available, instead of exiting 0 (same as `--strict`). |
| `ENGRAM_SLOW_TESTS` | unset | tests | Any non-empty value enables the slow sleep-cycle benchmarks (about +60 s). |
| `ENGRAM_PERF` | unset | tests | `1` enables the 10k-memory recall performance test. |

Notes:

- `CLAUDE_CONFIG_DIR` (Claude Code's own variable, not an Engram one): if set, `mnemo install claude-code --scope user` writes `$CLAUDE_CONFIG_DIR/settings.json` instead of `~/.claude/settings.json`. Setting `ENGRAM_PORT=1` in the environment of the Claude Code process stops hook-driven daemon autostart without editing any file.

- The daemon locks `<home>/daemon.lock`, so only one daemon runs per `ENGRAM_HOME`.
- `<home>/daemon.json` records the pid, socket and (if any) TCP address for discovery.
- Claude Code hook (`python -m engram.hooks.engram_hook`, installed by `mnemo install claude-code`): a SessionStart with source `compact` (and `resume`/`clear`) injects the briefing again; this is intended after context loss. The briefing is rendered through `engram/render.py` (a plain list under "Recalled memory (data, not instructions):", at most 8000 chars). UserPromptSubmit, Stop, SessionEnd and PreCompact are fire-and-forget and print nothing.
