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
| `ENGRAM_EMBEDDER` (env or file) | `hash` | daemon | Which embedder produces vectors. `hash` (default, stdlib only, offline) or `fastembed:<model>`, for example `fastembed:sentence-transformers/all-MiniLM-L6-v2` (needs `pip install 'engram-memory[local]'`). `openai:<model>` is reserved for T26 and is an unknown value today. An unknown value, a missing package, a model that cannot be loaded or any runtime error falls back to the hashed embedder (see "Embedder plugins" below). |
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

## Embedder plugins (`ENGRAM_EMBEDDER`)

| Value | Meaning |
|---|---|
| `hash` (default) | Built-in hashed n-gram embedder, id `hashed-ngram-v1:512`. Stdlib only, offline, deterministic. |
| `fastembed:<model>` | Local ONNX sentence embedder through the optional `fastembed` package. Embedder id `fastembed:<model>:<dim>` (384 for MiniLM). |
| `openai:<model>` | Not available yet (T26); today it is reported as an unknown embedder and the hashed embedder is used. |

How it behaves:
- **Lazy.** The daemon imports nothing optional until the first embedding is needed, and with the default `hash` it never imports `fastembed`, `onnxruntime`, `numpy`, `tokenizers` or `huggingface_hub` (a test proves it with `sys.modules`). The first call with `fastembed:` loads the model inside that request (and may download it on first ever use), so that one call can be slow.
- **Fail-soft.** A missing package, a model that cannot be downloaded or loaded, a vector of the wrong dimension, NaN/inf/zero output or any exception makes that call use the hashed embedder instead. One line per failure kind per process goes to the daemon's stderr (spec, kind and exception class only, never memory text). A failed load is retried at most every 5 minutes. Lexical (full-text) search is never affected, and the daemon never raises because of an embedder.
- **Observability.** `mnemo status` and `engram_status` show the id actually used for new writes, and when `ENGRAM_EMBEDDER` is not `hash`: the configured value, the state (`not-loaded`, `active`, `falling-back`), whether it is currently falling back and a fallback counter. `status` never loads a model.
- **Vectors of different embedders coexist.** Every row stores its `embedder_id`; vector search, dedup and recombine only compare rows with the same id. After changing the embedder, older rows are served by the lexical channel only (no error) until they are re-embedded (`mnemo reembed`, T26, not built yet). A row written during a fallback carries the hashed id, so it is never mixed up with model vectors.
- **Thresholds are per embedder.** Each embedder declares `NEAR_DUPLICATE`, `SEM_GAIN` (the semantic scale factor), `SEM_MIN_COS` (when a hit counts as semantic) and the recombine band `RECOMBINE_LO`/`RECOMBINE_HI`. All embedders currently use the conservative hashed values (0.88, 1.8, 0.30, 0.34 to 0.88): the cosine distributions of real models are **unmeasured**, so nothing is tuned. `NEAR_DUPLICATE` is clamped to at least 0.88 whatever an embedder declares.
- **Duplicate safety does not depend on the embedder.** The identifier, negation/antonym, substitution and word-order vetoes read the text, never the vectors. The dedup suites run against the hashed embedder, a fake dense embedder that deliberately puts opposite statements at cosine 0.97 or more, and the fastembed class with a stubbed model.
- **Privacy.** `fastembed:` runs on-device: memory text is not sent anywhere. The only network access is the one-time model download by the `fastembed` package itself (from the model hub); pre-populate its cache and set `HF_HUB_OFFLINE=1` to forbid even that.
- **Honest status of the real-model path.** It is verified only up to the package import and with stubbed embeddings. The real MiniLM model could not be downloaded where this was built, so real-model quality, speed and cosine distributions are unverified. The recall quality numbers in `docs/architecture/lessons.md` are for the hashed embedder only.
