# Architecture

How the pieces fit, as built. This is a map, not the design record: decisions and their reasons are in [docs/adr/](adr/), the original plan is [architecture/plan.md](architecture/plan.md), the schema and algorithms are in [technical-details.md](technical-details.md). Verbs, tools and commands: [api.md](api.md). Trust boundaries and defences: [threat-model.md](threat-model.md).

## 1. One page

```
 Claude Code session                     Cursor / Codex / Gemini / Grok ...
   | hook JSON on stdin                          | MCP over stdio
   v                                             v
 engram/hooks/engram_hook.py               engram/mcp_server.py      mnemo (engram/cli.py)
   \__________________________________  ___________________________/        |
                                      \/                                    |
                    engram/client.py  (fail-soft: never raises)  <---------/
                                |  one JSON line out, one back
                                v
          Unix socket (0600 in a 0700 dir)   or   TCP + token (optional)
                                |
                       engram/daemon.py   Server (transport) -> Engram (verbs)
                                |  ONE FairLock; sleep takes it per batch only
        +-----------+-----------+------------+-----------------+----------------+
        |           |           |            |                 |                |
   encode.py    recall.py   consolidate.py  export.py      distill.py       reembed.py
  (gate, dedup, (fusion,    (sleep cycle)   (render only)  (opt-in LLM)     (opt-in remote
   redaction)   floors)                                                      embedder migration)
        \___________|___________|                                |
                    v                                            v
              cortex.py  SQLite WAL + FTS5  <--- vectorize.py (embedder registry; plugins below)
```

Everything is Python standard library plus SQLite. The only optional third-party code is `fastembed`, imported only when `ENGRAM_EMBEDDER=fastembed:<model>` (`pip install 'engram-memory[local]'`). Platform: POSIX only (`fcntl`, `AF_UNIX`).

## 2. Components

| Component | File | Role |
|---|---|---|
| Engine: store | `engram/cortex.py` | SQLite (WAL) schema, FTS5 index, edges, journal, sessions, `engram_meta` key/value. Four kinds with decay rates (`DECAY`): episodic 0.5, semantic 0.2, procedural 0.1, identity 0.0. Statuses `active`, `archived`, `suppressed`. Refuses a database whose `schema_version` is newer than the code. |
| Engine: write path | `engram/encode.py` | The `encode()` gate: redact, score, dedup against neighbours, then create / reinforce / reject. `similarity = max(cosine, jaccard)` damped by textual vetoes (identifier, negation/antonym, substitution, word order); merge only at 0.88 or more. `observe()` journals a turn and promotes it only when durable. |
| Engine: read path | `engram/recall.py` | Candidates from full-text search and the vector channel, fused by rank, boosted for the same repo (`SCOPE_BOOST` 1.15), spreading activation over edges, relevance floors so off-topic cues return nothing, reinforcement of hits. `briefing()` picks the strongest memories for a repo. |
| Sleep cycle | `engram/consolidate.py` | Stages: `consolidate` (group journal rows into episodes after a 15 minute gap, at least 4 turns, extract a gist) then `purge` (journal rows: 30 days once consolidated, 90 days otherwise), `downscale` (strength decay, gated to once per 6 h, elapsed time capped at 14 days, archive below activation -3.2), `recombine` (link semantically related memories, similarity band 0.34 to 0.88, at most 40 links). Each stage is isolated; the whole cycle has a wall-clock budget (120 s by default). |
| Locking | `engram/fairlock.py` | A handoff lock so a long sleep cycle (which takes the lock per batch) cannot starve requests. |
| Daemon | `engram/daemon.py` | `Server` owns sockets, limits, auth and the accept/sleep loops; `Engram` owns the verb table. One process per `ENGRAM_HOME` (flock on `daemon.lock`; a second start exits 0). A background loop runs the sleep cycle after `ENGRAM_IDLE_SLEEP` seconds idle, at most every `ENGRAM_SLEEP_INTERVAL`, only if something happened. |
| Client | `engram/client.py` | The only way hosts talk to the daemon. Every call has a deadline (hook 2 s, MCP 5 s, CLI 10 s) and returns `{"ok": false, "offline": true}` on any failure. `send_nowait` is fire-and-forget. `autostart()` spawns a detached daemon (single flight via `autostart.lock`, 10 s cooldown), never in TCP/container mode. |
| MCP server | `engram/mcp_server.py` | Hand-rolled stdio JSON-RPC over the client. Tools: `recall`, `remember`, `reflect`, `forget`, `engram_status`, plus two distillation tools when `ENGRAM_LLM_CONSOLIDATE` is on. Does not start the daemon. |
| CLI | `engram/cli.py` | `mnemo`: argument parsing and formatting over the client; fail-soft exit codes ([api.md](api.md)). |
| Claude Code hooks | `engram/hooks/engram_hook.py` | SessionStart: fetch the briefing and print it as `additionalContext`. UserPromptSubmit and Stop: fire-and-forget `observe` of the prompt and final reply. SessionEnd: `session_end`, then a detached refresh of existing export files. PreCompact: a non-deep `sleep`. Always exits 0, 2 s watchdog, stdout empty or one JSON object. |
| Rule export | `engram/export.py`, `engram/render.py` | The daemon renders items (verb `export_rules`, no paths accepted); the client process writes `.claude/engram_learned.md` and `.cursor/rules/engram_learned.mdc` atomically, with `O_NOFOLLOW`, never overwriting a file without Engram's header. `render.py` turns memory text into inert single-line list items under a "data, not instructions" header. |
| Redaction | `engram/redact.py` | Pure, linear-time, best-effort masking of secret shapes (`[REDACTED:kind]`) at every ingestion point. |
| Installer | `engram/install.py`, `engram/install_hosts.py` | `mnemo install\|uninstall claude-code` (merge hooks into `settings.json`), `... cursor` (MCP entry, project rule), `install --print mcp` (snippet only). Merge never replaces, entries are recognised by an exact command signature, writes are atomic and symlink-safe, invalid JSON is refused untouched. |
| Docker | `Dockerfile`, `docker-compose.yml`, [docker.md](docker.md) | The daemon in container mode: TCP only, token mandatory, non-root, read-only root, data in a volume. |

## 3. Embedder and distiller plugins

Embedders (`engram/vectorize.py`, ADR 0010, 0011):

| `ENGRAM_EMBEDDER` | Module | Notes |
|---|---|---|
| `hash` (default) | `vectorize.py` | 512-dim hashed n-gram vectors, id `hashed-ngram-v1:512`. Offline, deterministic. |
| `fastembed:<model>` | `engram/embedders_ext.py` | Local ONNX model through the optional package. |
| `openai:<model>` | `engram/embedders_remote.py` | Sends redacted text to an OpenAI-compatible endpoint; double opt-in; `http.client` only. |

The contract every embedder lives under: it never runs under the engine lock (the daemon embeds before taking it); it loads on a background thread; every call is bounded by a timeout on one worker thread; three consecutive failures open a circuit breaker with a doubling cooldown; any failure makes that call use the hashed embedder, and a vector is always stored under the id of the embedder that produced it. Vectors of different ids coexist and are never compared (older rows are found by full-text search until `mnemo reembed` migrates them). All thresholds are currently the hashed values; real-model cosine distributions are unmeasured.

Distiller (`engram/distill.py`, `engram/llm_api.py`, ADR 0012), off by default: with `ENGRAM_LLM_CONSOLIDATE` on, episodes are either queued for the MCP host's own model (`engram_consolidate_pending` / `engram_distill_submit`) or, with `ENGRAM_LLM_API`, sent to an API during idle sleep cycles. Model output is hostile input: it enters only through `Service.submit`, which validates it and runs every bullet through the full `encode()` gate; it is never `deliberate`, never identity, and is withheld from briefings and exports until `access_count` reaches 2 (`encode.withheld`).

## 4. Data flow

**Write (automatic, Claude Code):** hook reads the prompt/reply, `send_nowait("observe")`; the daemon redacts, caps (256 KiB per turn), appends to `journal`, and promotes the turn straight to a memory only if it scores as durable. Later the sleep cycle replays unconsolidated journal rows into episodes, writes a summary memory and extracted facts through the same `encode()` gate, and marks the rows consolidated.

**Write (deliberate):** `remember` / `reflect` via MCP or `mnemo`: embed outside the lock, then `encode()` under the lock. Deliberate writes skip the trivial-text check, get a salience floor of 0.55 and are stamped `meta.deliberate` (which `export-rules` requires of memories that were never reinforced).

**Read:** `recall` embeds the (redacted) cue outside the lock, then fuses the channels, applies floors, reinforces the hits (unless `reinforce` is false) and returns them. MCP and hooks render results through `render.py` before they reach a model.

**Forgetting:** strength decays per kind during `downscale`; low-activation memories become `archived`; unreinforced archived episodic rows are deleted after 45 days; suppressed rows (`forget`) after 90 days; identity memories are never decayed or purged.

## 5. Processes and files

Under `ENGRAM_HOME` (default `~/.engram`, mode 0700): `engram.db` (+ WAL/SHM, 0600), `engram.sock` (0600), `daemon.json` (pid, socket, TCP address), `daemon.lock`, `autostart.lock` / `autostart.stamp`, `token` (0600, created when a listener starts and no `ENGRAM_TOKEN` is set). Configuration precedence: environment, then `config.json`, then defaults ([configuration.md](configuration.md)).

Three kinds of long-lived or short-lived processes: one daemon; one short-lived hook process per Claude Code event; one MCP server per host session. They share nothing but the socket.

## 6. Testing layout

`tests/` holds the unit and integration suites (`python3 -m unittest discover -s tests -t .`); `scripts/test_e2e.py`, `scripts/test_hooks.py` and `scripts/test_concurrency.py` start real daemon and hook processes and are wrapped into the default run by `tests/test_e2e.py`, `tests/test_hooks_wrapper.py` and `tests/test_concurrency_wrapper.py`. `tests/netguard.py` patches `socket.connect` to fail for non-loopback addresses (used by the skeleton guard test). Remote embedder and distiller tests use a local stub server. CI (`.github/workflows/ci.yml`) runs the suite on Python 3.9, 3.11, 3.13 and 3.14 and a `package` job that builds the wheel and exercises the installed commands in a clean venv.
