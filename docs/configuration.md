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
| `ENGRAM_EMBEDDER` (env or file) | `hash` | daemon | Which embedder produces vectors. `hash` (default, stdlib only, offline) or `fastembed:<model>`, for example `fastembed:sentence-transformers/all-MiniLM-L6-v2` (needs `pip install 'engram-memory[local]'`). `openai:<model>` sends memory text to an OpenAI-compatible HTTP endpoint (a THIRD PARTY unless it is loopback) and needs more opt-in, see "Remote embedder" below. An unknown value, a missing package, a model that cannot be loaded or any runtime error falls back to the hashed embedder (see "Embedder plugins" below). |
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

Related settings (env or `config.json`):

| Variable | Default | Meaning |
|---|---|---|
| `ENGRAM_EMBEDDER_LOAD_TIMEOUT` | `120` | Clamped to 1-900. Seconds the model may take to load (and download). The load runs on a background thread started with the daemon; if it is still running at the deadline it is abandoned (never joined) and the embedder is marked failed. |
| `ENGRAM_EMBEDDER_TIMEOUT` | `1.0` | Clamped to 0.05-10 (non-numbers use the default). Seconds one embedding call may take. A slower call counts as failure kind `timeout` and that call uses the hashed embedder. |

How it behaves:
- **The daemon never waits for a model.** With a non-default embedder the model loads on a background thread; until it is ready, and whenever it is failing, every call uses the hashed embedder and stores its vectors under the hashed id. A finished load switches new writes to the model atomically (old rows are not touched). The first-use download therefore no longer stalls any request, whatever the model library's own retry sleeps do.
- **No embedder call under a lock.** The daemon computes vectors before it takes the engine lock; dedup compares stored vectors; `recombine` and the sleep cycle use stored vectors only (sleep embeds new episode summaries before taking the lock).
- **Bounded calls.** Each embedding call runs on one worker thread with the timeout above and a queue of one: a request that finds the worker busy uses hashed immediately instead of queueing behind a slow call.
- **Circuit breaker.** After 3 consecutive failures or timeouts the model is bypassed entirely (no calls, no waiting) for a cooldown of 30 s, doubling each time up to 10 min. After the cooldown one probe call runs on the worker thread, never on a request thread; success closes the breaker, failure doubles the cooldown. A probe that hangs is given the call timeout, then counts as a failure and the stuck worker is abandoned and replaced. Failed loads are retried the same way, in the background.
- **Fail-soft.** A missing package, a model that cannot be downloaded or loaded, a load or call timeout, a vector of the wrong dimension, NaN/inf/zero output or any exception makes the call use the hashed embedder. One line per failure kind per process goes to the daemon's stderr (spec, kind and exception class only, never memory text). Lexical (full-text) search is never affected and the daemon never raises because of an embedder.
- **Observability.** `mnemo status` and `engram_status` show the id actually used for new writes and, when `ENGRAM_EMBEDDER` is not `hash`: the configured value, the state (`loading`, `ready`, `falling-back`), the reason (`timeout`, or kind and exception class, or `circuit breaker open (last failure: ...)`), the dimension of the vectors in use (hashed while falling back), the seconds until the next retry or probe, and a fallback counter. `status` never loads a model or waits.
- **Vectors of different embedders coexist.** Every row stores its `embedder_id`; vector search, dedup and recombine only compare rows with the same id. After changing the embedder, older rows are served by the lexical channel only (no error) until they are re-embedded (`mnemo reembed`, see "Remote embedder and reembed" below). A row written during a fallback carries the hashed id, never the model's id.
- **Thresholds are per embedder.** Each embedder declares `NEAR_DUPLICATE`, `SEM_GAIN` (the semantic scale factor), `SEM_MIN_COS` (when a hit counts as semantic) and the recombine band `RECOMBINE_LO`/`RECOMBINE_HI`. All embedders currently use the conservative hashed values (0.88, 1.8, 0.30, 0.34 to 0.88): the cosine distributions of real models are **unmeasured**, so nothing is tuned. `NEAR_DUPLICATE` is clamped to at least 0.88; the other declared values must be finite and inside sane ranges (SEM_GAIN 0.1 to 10, the rest 0 to 1) or the default is used.
- **Duplicate safety does not depend on the embedder.** The identifier, negation/antonym, substitution and word-order vetoes read the text, never the vectors. The dedup suites run against the hashed embedder, a fake dense embedder that deliberately puts opposite statements at cosine 0.97 or more, and the fastembed class with a stubbed model.
- **Privacy.** `fastembed:` runs on-device: memory text is not sent anywhere. The only network access is the one-time model download by the `fastembed` package itself (from the model hub); pre-populate its cache and set `HF_HUB_OFFLINE=1` to forbid even that.
- **Memory.** The decoded-vector cache is bounded by total floats (about 1M, roughly 32 MB), not just by entry count, so 1536-d vectors do not grow it.
- **Honest status of the real-model path.** It is verified only up to the package import, the real "model cannot be loaded" path and a stubbed model. The real MiniLM model could not be downloaded where this was built, so real-model quality, speed and cosine distributions are unverified. The recall quality numbers in `docs/architecture/lessons.md` are for the hashed embedder only.

## Remote embedder and `mnemo reembed` (T26, ADR 0011)

**PRIVACY WARNING.** `ENGRAM_EMBEDDER=openai:<model>` sends the text of your memories (and of every recall query) to the host in `ENGRAM_EMBEDDER_URL`, which for a hosted provider is a third party. It is off by default and never sends anything without both opt-ins below. Redaction is best-effort (see `docs/architecture/lessons.md`, known gaps): a secret the patterns do not recognise WILL be sent. Do not enable this for memories you would not paste into that provider's API.

| Variable | Default | Meaning |
|---|---|---|
| `ENGRAM_EMBEDDER` | `hash` | `openai:<model>` selects the remote embedder. The model name must be printable, no spaces, at most 128 characters. Stored ids are `openai:<model>:<dim>`; the dimension is learned from the first successful response and then enforced. |
| `ENGRAM_EMBEDDER_URL` | `https://api.openai.com/v1` | Base URL; the request goes to `<base>/embeddings`. `https://` required, except `http://` for loopback hosts only (`127.0.0.0/8`, `::1`, `localhost`: a local OpenAI-compatible server such as `http://127.0.0.1:11434/v1`). No credentials, query or fragment in the URL. Environment only (not `config.json`). |
| `ENGRAM_EMBEDDER_ALLOW_REMOTE` | unset | Must be `1`/`true`/`yes`/`on` for any non-loopback host. Without it the embedder reports `unavailable: remote not acknowledged`, the daemon uses hashed vectors and makes NO network call. Environment only. |
| `ENGRAM_EMBEDDER_API_KEY` | unset | The API key, read ONLY from this variable (never `OPENAI_API_KEY`). Required for non-loopback hosts. Sent only in the `Authorization` header of the verified connection; never logged, shown, stored or put on a command line. Keep it out of `config.json` and shell history. |
| `ENGRAM_EMBEDDER_TIMEOUT` | `1.0` | (existing) bounds every remote call in total (connect, TLS, send, receive). A hosted API is often slower than 1 s for a batch: raise it (max 10) or the embedder will mostly fall back to hashed. |

How it is contained: every string is redacted by `engram.redact` and capped (8000 characters, 64 strings per request) inside the adapter itself; recall cues are redacted too. HTTPS uses the default certificate and hostname verification (there is no switch to turn it off). Redirects are never followed (a 3xx is a failure, the key is not sent anywhere else). The response is capped at 8 MB and strictly validated (JSON, `Content-Type`, one finite non-empty vector per input, same dimension on every call, index order respected). 429 and 5xx are failures for the circuit breaker; `Retry-After` (capped at 60 s) pauses further calls without blocking any request. Only `http.client` is used: `HTTP_PROXY`/`HTTPS_PROXY` are NOT honoured and there are no cookies. The `User-Agent` is `engram/<version>`. `mnemo status` shows `remote: true`, host, model and the line `WARNING: memory text is sent to <host> for embedding`.

### `mnemo reembed`

Rewrites the vector and `embedder_id` of active memories whose id differs from the configured, READY embedder (switching back to `hash` is allowed and local). Nothing else changes.

- `mnemo reembed --dry-run`: how many memories and characters would be sent, to which host. Sends nothing.
- `mnemo reembed --yes [--batch N] [--include-identity]`: starts a background run. **Only a remote target (`openai:`) needs `--yes`; a local target (`hash`, `fastembed:`) starts immediately without it.** For a remote embedder it refuses without `--yes` (exit 0 with a message; exit 4 with `--strict`; an interactive terminal is asked instead). The daemon enforces the same with `confirm`.
- The consent is tied to the endpoint: the run records the host it was confirmed for, and if the host differs when the run resumes (restart, changed `ENGRAM_EMBEDDER_URL`), it pauses with `embedder host changed` and sends nothing until you run `mnemo reembed --yes` again.
- `mnemo reembed --status` / `--cancel`; progress (done/total, rate, ETA, last error) is also in `mnemo status`.
- IDENTITY memories are skipped (they are the most personal) unless `--include-identity`. Archived and suppressed rows are not migrated.
- Batches of 32 (max 64) are embedded without the engine lock and written in one transaction with the cursor (`engram_meta` key `reembed`), so a restart or `kill -9` resumes at the next batch; a run that was in progress when the daemon stopped continues at the next start. Failed batches are retried with backoff; after 5 in a row the run pauses with the reason and `--yes` resumes it. Until a row is migrated it is found by full-text search only.
- Real provider behaviour is UNVERIFIED: every test used a local stub server.
