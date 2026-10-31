# Architecture overview

The high-level map of Alethea as built, readable in about ten minutes. It says what the parts are and how data moves between them. It does not repeat schemas, thresholds or algorithms (those are in the [deep dives](#deep-dives)) and it is not the design record (that is the [ADRs](../adr/0001-owner-choices.md)). New to the product? Read [What is Alethea](../product/what-is-alethea.md) first.

## 1. System context

```
 Claude Code session                     Cursor / Codex / Gemini / Grok ...
   | hook JSON on stdin                          | MCP over stdio
   v                                             v
 alethea/hooks/alethea_hook.py               alethea/mcp_server.py      mnemo (alethea/cli.py)
   \__________________________________  ___________________________/        |
                                      \/                                    |
                    alethea/client.py  (fail-soft: never raises)  <---------/
                                |  one JSON line out, one back
                                v
          Unix socket (0600 in a 0700 dir)   or   TCP + token (optional)
                                |
                       alethea/daemon.py   Server (transport) -> Alethea (verbs)
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

Everything is the Python standard library plus SQLite. The only optional third-party code is `fastembed`, imported only when `ALETHEA_EMBEDDER=fastembed:<model>` is set (`pip install 'alethea[local]'`). Platform: POSIX only (`fcntl`, `AF_UNIX`).

Three kinds of process, sharing nothing but the socket: one daemon per `ALETHEA_HOME`; one short-lived hook process per Claude Code event; one MCP server per host session. The daemon is the only writer.

## 2. Components

| Component | Code | Role | Deep dive |
|---|---|---|---|
| Store | [`cortex.py`](../../alethea/cortex.py) | SQLite (WAL) schema, FTS5 index, association edges, journal, sessions, key/value meta. Four memory kinds with per-kind decay. Refuses a database newer than the code. | [technical-details: memory model](technical-details.md#3-memory-model), [decay half-lives](decay-halflives.md) |
| Write path | [`encode.py`](../../alethea/encode.py) | The `encode()` gate: redact, score, dedup against neighbours, then create, reinforce or reject. `observe()` journals a turn and promotes it only when durable. | [technical-details: encoding](technical-details.md#5-encoding-encodepy), ADR [0006](../adr/0006-substitution-damp.md), [0007](../adr/0007-order-and-case-vetoes.md), [0008](../adr/0008-dedup-fts-window-cap.md) |
| Read path | [`recall.py`](../../alethea/recall.py) | Full-text and vector candidates fused by rank, same-repo boost, spreading activation over edges, relevance floors, reinforcement of hits. `briefing()` picks the strongest memories for a repo. | [technical-details: retrieval](technical-details.md#6-retrieval-recallpy), ADR [0014](../adr/0014-hash-salt-version-and-semantic-floor.md) |
| Sleep cycle | [`consolidate.py`](../../alethea/consolidate.py) | Groups journal rows into episodes, extracts gists, weakens and archives, links related memories, purges old rows. Each stage is isolated and the cycle has a wall-clock budget. | [technical-details: consolidation](technical-details.md#7-consolidation-consolidatepy), ADR [0003](../adr/0003-activation-and-downscale.md), [0005](../adr/0005-sleep-lock-granularity.md) |
| Locking | [`fairlock.py`](../../alethea/fairlock.py) | A handoff lock so a long sleep cycle, which takes the lock per batch, cannot starve requests. | ADR [0005](../adr/0005-sleep-lock-granularity.md) |
| Daemon | [`daemon.py`](../../alethea/daemon.py) | `Server` owns sockets, limits and auth; `Alethea` owns the verb table. Runs the idle-triggered sleep loop. | [technical-details: processes](technical-details.md#8-process-architecture), ADR [0004](../adr/0004-tcp-auth-and-container-binding.md), [API](../reference/api.md) |
| Client | [`client.py`](../../alethea/client.py) | The only way hosts talk to the daemon. Hard deadlines; returns `{"ok": false, "offline": true}` on any failure; can autostart a detached daemon. | [API](../reference/api.md), [threat model: availability](threat-model.md#8-availability) |
| MCP server | [`mcp_server.py`](../../alethea/mcp_server.py) | Hand-rolled stdio JSON-RPC. Tools `recall`, `remember`, `reflect`, `forget`, `alethea_status`, plus two distillation tools when enabled. Does not start the daemon. | [API](../reference/api.md) |
| CLI | [`cli.py`](../../alethea/cli.py) | `mnemo`: argument parsing and formatting over the client; fail-soft exit codes. | [API](../reference/api.md) |
| Claude Code hooks | [`hooks/alethea_hook.py`](../../alethea/hooks/alethea_hook.py) | SessionStart briefing; prompt and reply capture; SessionEnd and PreCompact consolidation. Always exits 0 within a 2 s watchdog. | [hooks reference](claude-code-hooks-reference.md), ADR [0009](../adr/0009-hooks-ship-with-the-package.md), [Claude Code guide](../../integrations/claude-code/README.md) |
| Rule export | [`export.py`](../../alethea/export.py), [`render.py`](../../alethea/render.py) | The daemon renders text; the client process writes `.claude/alethea_learned.md` and `.cursor/rules/alethea_learned.mdc` atomically. `render.py` makes memory text inert list items. | [technical-details: sync-back](technical-details.md#sync-back-mnemo-export-rules), [threat model: hostile text](threat-model.md#3-hostile-memory-text) |
| Redaction | [`redact.py`](../../alethea/redact.py) | Linear-time, best-effort masking of secret shapes at every ingestion point. | [threat model: secrets](threat-model.md#4-secrets-in-memory-redaction-and-its-limits) |
| Embedders | [`vectorize.py`](../../alethea/vectorize.py), [`embedders_ext.py`](../../alethea/embedders_ext.py), [`embedders_remote.py`](../../alethea/embedders_remote.py) | Hashed n-gram default; optional local `fastembed` and remote OpenAI-compatible plugins, each contained and fail-soft. | [technical-details: embeddings](technical-details.md#4-embeddings-vectorizepy), ADR [0010](../adr/0010-embedder-plugin-loading-and-thresholds.md), [0011](../adr/0011-remote-embedding-consent-and-redaction.md) |
| Distiller | [`distill.py`](../../alethea/distill.py), [`llm_api.py`](../../alethea/llm_api.py) | Off by default. LLM-assisted episode distillation, via the host's model or a direct API; output is treated as hostile input. | ADR [0012](../adr/0012-llm-distillation.md) |
| Re-embed | [`reembed.py`](../../alethea/reembed.py) | Resumable migration of stored vectors to a new embedder (`mnemo reembed`). | ADR [0011](../adr/0011-remote-embedding-consent-and-redaction.md) |
| Installer | [`install.py`](../../alethea/install.py), [`install_hosts.py`](../../alethea/install_hosts.py) | `mnemo install\|uninstall claude-code\|cursor`, `install --print mcp`. Merge-only, atomic, symlink-safe. | [integrations guide](../guides/integrations.md), [threat model: installer](threat-model.md#7-installer-and-file-writes) |
| Docker | [`Dockerfile`](../../Dockerfile), [`docker-compose.yml`](../../docker-compose.yml) | The daemon in container mode: TCP only, token mandatory, non-root, read-only root, data in a volume. | [Docker guide](../guides/docker.md), ADR [0013](../adr/0013-docker-sidecar-packaging.md) |

## 3. The three main flows

### Write path

Automatic (Claude Code hooks):

1. A hook fires on the user's prompt or the assistant's final reply and sends `observe` without waiting for an answer.
2. The daemon redacts the text, caps it (256 KiB per turn) and appends it to the `journal`.
3. If the turn scores as durable (a decision, a correction, a cause, a constraint), it also goes through `encode()` and may become a long-term memory at once. Otherwise it waits for the sleep cycle.

Deliberate (`remember` or `reflect`, via MCP or `mnemo`):

1. The text is embedded outside the engine lock, then passed to `encode()` under the lock.
2. `encode()` redacts, rejects trivial chatter, looks for a near-duplicate and either reinforces it or stores a new memory.
3. Deliberate writes skip the trivial-text check, get a salience floor and are stamped so that `export-rules` can trust them.

### Read path

1. A host calls `recall` (MCP or `mnemo`), or a SessionStart hook asks for a briefing.
2. For `recall`, the cue is redacted and embedded outside the lock. Candidates come from full-text search and the vector channel and are fused by rank.
3. Same-repo memories get a boost, related memories are pulled in over association edges, and relevance floors drop off-topic hits.
4. Scores are weighted by how strongly each memory is currently held, and the hits are reinforced.
5. Results are rendered as inert, single-line list items under a "data, not instructions" header before they reach a model. A briefing reinforces nothing.

### Sleep cycle

1. The daemon starts a cycle after `ALETHEA_IDLE_SLEEP` idle seconds (at most every `ALETHEA_SLEEP_INTERVAL`, and only if something happened), or a hook asks for one (PreCompact, SessionEnd).
2. Consolidate: journal rows are grouped into episodes; each gets a summary and extracted facts, written through the same `encode()` gate.
3. Downscale: strength decays per memory kind; weak memories are archived. Gated to once per 6 hours so restarts or clock skew cannot compress forgetting.
4. Recombine: related semantic, procedural and identity memories are linked.
5. Purge: old journal rows are deleted, and so are episodic memories that were archived long ago and never recalled. Identity memories are never decayed or purged.

All thresholds and timings are in [technical-details.md](technical-details.md); numbers are deliberately not repeated here.

## 4. Key design decisions

| Decision | Why | Record |
|---|---|---|
| Similarity is `max(cosine, jaccard)` with text vetoes (identifier, negation, substitution, word order); merge only at a high threshold | A false merge of two distinct facts is silent and permanent; a redundant copy is merely untidy | [technical-details: similarity](technical-details.md#similarity-the-most-important-correctness-rule), ADR [0006](../adr/0006-substitution-damp.md), [0007](../adr/0007-order-and-case-vetoes.md) |
| One daemon, single writer, fair lock | Simple consistency; sleep must not starve requests | ADR [0005](../adr/0005-sleep-lock-granularity.md) |
| Activation on effective strength; per-kind decay and downscale | Reinforced memories outrank slightly better textual matches; kinds fade at different rates | ADR [0003](../adr/0003-activation-and-downscale.md), [decay half-lives](decay-halflives.md) |
| Dedup full-text window capped at 2000 rows | Bounded write cost, at the price of occasionally storing an old near-duplicate twice | ADR [0008](../adr/0008-dedup-fts-window-cap.md) |
| Hooks ship with the installed package, not the source repo | Cloning the repo must never journal anyone's sessions | ADR [0009](../adr/0009-hooks-ship-with-the-package.md) |
| Embedders are plugins, contained and fail-soft; the default is a hashed n-gram vector | Zero dependencies and offline by default; learned models are opt-in | ADR [0010](../adr/0010-embedder-plugin-loading-and-thresholds.md), [0014](../adr/0014-hash-salt-version-and-semantic-floor.md) |
| Remote calls need explicit consent and redaction at the boundary | Memory text can be sensitive | ADR [0011](../adr/0011-remote-embedding-consent-and-redaction.md), [0012](../adr/0012-llm-distillation.md) |
| TCP only with a token, loopback by default; container mode requires explicit binding | A listening port is an attack surface | ADR [0004](../adr/0004-tcp-auth-and-container-binding.md), [0013](../adr/0013-docker-sidecar-packaging.md) |
| Owner-chosen stack and package layout | Starting constraints of the project | ADR [0001](../adr/0001-owner-choices.md), [0002](../adr/0002-package-layout.md) |

## 5. Quality attributes

| Attribute | How it is met | Evidence |
|---|---|---|
| Privacy | Local-first: Unix socket by default, no network calls by default, redaction before storage. Remote embedding and LLM distillation are off and need explicit opt-in. No encryption at rest, no TLS on TCP. | [Threat model](threat-model.md), [configuration](../reference/configuration.md) |
| Fail-soft | The client never raises and has hard deadlines; hooks exit 0 within 2 s; a hung or failing embedder falls back to the hashed one behind a circuit breaker. | Tests named in [threat model: availability](threat-model.md#8-availability) |
| Latency | Embedding happens outside the engine lock; sleep releases the lock per batch; dedup cost is bounded. Client deadlines: hook 2 s, MCP 5 s, CLI 10 s. | Measured only on a loaded sandbox up to 20k memories; see [status](status.md) |
| Portability | POSIX only. Host-agnostic through MCP; automatic capture and briefing only where the host has hooks (Claude Code). | CI is Linux only; macOS untested |
| Python 3.9 | Source stays compatible with 3.9; CI is configured for 3.9, 3.11, 3.13 and 3.14; the suite was also run locally on 3.9 and 3.13. | `.github/workflows/ci.yml` |
| No dependencies | Standard library plus SQLite with FTS5 at runtime; `fastembed` is an optional extra. | `pyproject.toml` |

## Deep dives

| Topic | Document |
|---|---|
| Schema, algorithms, thresholds, process details | [technical-details.md](technical-details.md) |
| Assets, trust boundaries, defences, known gaps | [threat-model.md](threat-model.md) |
| Decisions and their reasons | [ADR 0001](../adr/0001-owner-choices.md) and the [full ADR list](../README.md#decisions-adrs) |
| Requirements with acceptance criteria | [specs/alethea.md](../specs/alethea.md) |
| Claude Code hook payloads and behaviour | [claude-code-hooks-reference.md](claude-code-hooks-reference.md) |
| How fast each memory kind fades | [decay-halflives.md](decay-halflives.md) |
| Pitfalls found while building, measurements | [lessons.md](lessons.md) |
| Original build plan | [plan.md](plan.md) |
| Task cards (T01 to T31) | [work-breakdown.md](work-breakdown.md) |
| What is built and what is unverified | [status.md](status.md) |
| Review history | [reviews/index.md](reviews/index.md) |
| Every environment variable | [configuration](../reference/configuration.md) |
| Daemon verbs, MCP tools, CLI commands, exit codes | [API reference](../reference/api.md) |

## Testing layout

`tests/` holds the unit and integration suites (`python3 -m unittest discover -s tests -t .`). `scripts/test_e2e.py`, `scripts/test_hooks.py` and `scripts/test_concurrency.py` start real daemon and hook processes and are wrapped into the default run by `tests/test_e2e.py`, `tests/test_hooks_wrapper.py` and `tests/test_concurrency_wrapper.py`. `tests/netguard.py` blocks non-loopback connections for the skeleton guard test. Remote embedder and distiller tests use a local stub server. CI (`.github/workflows/ci.yml`) runs the suite on Python 3.9, 3.11, 3.13 and 3.14 plus a `package` job that builds the wheel and exercises the installed commands in a clean venv.

## Files on disk

Under `ALETHEA_HOME` (default `~/.alethea`, mode 0700): `alethea.db` (+ WAL/SHM, 0600), `alethea.sock` (0600), `daemon.json` (pid, socket, TCP address), `daemon.lock`, `autostart.lock` and `autostart.stamp`, and `token` (0600, created when a listener starts and no `ALETHEA_TOKEN` is set). Configuration precedence: environment, then `config.json`, then defaults.
