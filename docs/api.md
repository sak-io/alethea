# API reference

Three surfaces over one daemon: the wire protocol (daemon verbs), the MCP tools, and the `mnemo` CLI. All limits below are read from the code (`alethea/daemon.py`, `alethea/mcp_server.py`, `alethea/cli.py`); the drift guard `tests/test_docs_api.py` fails if a daemon verb, MCP tool or `mnemo` subcommand is missing from this file. How the pieces fit: [architecture.md](architecture.md). Configuration: [configuration.md](configuration.md).

## 1. Wire protocol

One JSON object per line (newline-delimited, UTF-8) in, one JSON line out. Request: `{"verb": "<name>", "token": "<tcp only>", ...params}`. Every reply has `"ok": true|false`; failures are `{"ok": false, "error": "<message>"}`. A connection may carry several requests in sequence.

Transports (`alethea/daemon.py: Server`):

| Transport | Address | Auth |
|---|---|---|
| Unix socket | `<ALETHEA_HOME>/alethea.sock` (0600, directory 0700) | none (filesystem permissions) |
| TCP (optional) | `127.0.0.1:<port>`, or `ALETHEA_BIND`; recorded in `<ALETHEA_HOME>/daemon.json` | every request must carry `"token"`; see [threat-model.md](threat-model.md) |

Transport limits: request line max 1 MiB; first byte within 1.5 s, whole line within 5 s; at most 64 requests executing at once (`busy` otherwise); the client treats a reply over 8 MiB as a failure. The client library (`alethea/client.py`) never raises: any failure becomes `{"ok": false, "offline": true}` (`offline` is added by the client, never sent by the daemon).

### Transport-level errors (any verb)

| `error` | When |
|---|---|
| `invalid json` | the line is not valid UTF-8 JSON (connection closed) |
| `request must be an object` | JSON value is not an object |
| `request too large` | line over 1 MiB (connection closed) |
| `read timeout` | a partial line stalled past 5 s (connection closed) |
| `busy` | connection or execution slots exhausted |
| `missing verb` | no `verb`, or empty / not a string |
| `unknown verb` | verb not in the table below |
| `auth` | TCP: token missing or wrong (connection closed after a delay that grows with repeated failures from the same peer; over a global failure budget the connection is dropped without reply) |
| `unauthorized` | TCP: a bare `ping` with no token at all |
| `internal error` | an unexpected exception inside a verb (no traceback, no detail) |

Parameter validation failures are `{"ok": false, "error": "<message>"}` with one of: `missing parameter: <name>`, `<name> must be a string`, `<name> must not be empty`, `<name> too long (max N chars)`, `<name> must be a number`, `<name> out of range [lo, hi]`, `<name> must be a boolean`. Only those verb-specific messages that are not of this shape are listed per verb.

Common optional scope parameters on write/recall/briefing verbs: `cwd` (string, max 4096), `repo_root` (string, max 4096; when absent it is derived from `cwd`), `session_id` (string, max 4096 on `observe`/`remember`/`reflect`; 256 on `session_end`).

### Memory object

Returned by `recall` and `dump`: `id, kind, content, gist, keywords, salience, strength, access_count, created_at, last_access, status, session_id, cwd, repo_root, embedder_id, meta`; `recall` adds `score` and `via`. `kind` is `episodic|semantic|procedural|identity`; `status` is `active|archived|suppressed`.

### Verbs

| Verb | Parameters | Success reply | Verb-specific errors |
|---|---|---|---|
| `ping` | none | `{ok, pong: true}` | none. Not counted as activity. |
| `remember` | `content` (required, max 200000 chars); `kind` (one of the four kinds; inferred if omitted); `salience` (0..1); `meta` (object); scope | `{ok, status, ...}` where `status` is `created` (`id, kind, salience`), `reinforced` (`id, similarity`: merged into an existing memory) or `rejected` (`reason`: `empty`, `trivial`, `low_salience`). May carry `dedup_window_capped`. | `unknown kind`; `meta must be an object` |
| `reflect` | `content` (required); `cited_ids` (list of at most 50 id strings, each max 128); scope | same shape as `remember` | `cited_ids must be a list of at most 50 id strings` |
| `observe` | `content` (required); `role` (string, max 64); scope | `{ok, journal_id, promoted}`. `journal_id` null when nothing was journalled; `promoted` is the `encode` result dict (`status`, `id`, ...) when the turn was durable enough to be promoted to a memory directly, else null. | none |
| `recall` | `cue` (max 4096; `query` is accepted as an alias); `k` (1..50, default 8); `scope` (`all` default, `repo`); `reinforce` (boolean, default true); `cwd`/`repo_root` | `{ok, results: [memory...]}`. An empty list is a normal answer (relevance floors: nothing returned for off-topic cues). | `scope must be one of: all, repo` |
| `briefing` | scope | `{ok, text, count}`: plain list of the strongest memories for this repo, not the model-written ones until reinforced | none |
| `forget` | `id` (required, max 128) | `{ok, forgotten: true}`. The memory is suppressed, not deleted; the sleep cycle hard-deletes suppressed rows after 90 days, except `identity` memories, which are never purged. | `not found` |
| `sleep` | `deep` (boolean, default true); `consolidate_only` (boolean, default false); `budget_s` (0.1..3600, default 120) | `{ok, deep, consolidate_only, stopped, stages: {...}, errors: {...}, ms}` or `{ok, skipped: "already running"}` (single flight). `stages` holds `consolidate`, optionally `distill_api`, then (unless `consolidate_only`) `purge` and, when `deep`, `downscale` and `recombine`. A failing stage is recorded under `errors` and later stages still run. | none |
| `session_end` | `session_id` (required, max 256); `wait` (boolean, default false) | `{ok, scheduled: true}` (consolidation runs on a background thread) or, with `wait`, `{ok, consolidated: true}` | none |
| `status` | none | `{ok, version, schema_version, embedder_id, embedder_configured, embedder_state, embedder_falling_back, embedder_reason, embedder_retry_in_s, embedder_fallbacks, embedder_dim, fts, counts, total, journal_pending, dedup, distiller, reembed, last_sleep, pid, uptime_s, sleep_interval_s, idle_sleep_s}`, plus `embedder_remote*` fields for `openai:`. Never loads a model or waits; not counted as activity. | none |
| `dump` | `limit` (1..500, default 100); `offset` (0..1e9); `kind`; `status` (each max 32 chars) | `{ok, memories: [memory...], limit, offset}`; `content` is cut at 2000 chars | none |
| `export_rules` | `repo_root` (required; absolute, no `..`) | `{ok, items, count}`: render-only, the daemon never writes a file | `parameter not accepted: <name>` for `path`, `target`, `dir` and similar (see `alethea/export.py: FORBIDDEN_PARAMS`); `repo_root must be an absolute path without '..'` |
| `pending` | `n` (1..5, default 3) | `{ok, episodes: [{id, session_id, start, end, turns, expires_in_s, text}], count, mode, max_bullets, max_bullet_chars}` | `LLM distillation is off (set ALETHEA_LLM_CONSOLIDATE=1)`; `distillation runs inside the daemon (api mode)` |
| `distill_submit` | `episode_id` (required, max 32); `bullets` (list of strings) | `{ok, accepted, created, merged, consolidated, rejected?, replayed?}` | the two errors of `pending`; whole-request validation messages (`bullets must be a list of strings`, `too many bullets (max 6)`, `bullet N too long (max 300 chars)`, `bullet N must be a non-empty string`, ...); `episode_id must be an id returned by pending`; `unknown, expired or already consolidated episode`; `episode was not listed by pending`; `rate limit reached; try again later` |
| `reembed` | `action` (required): `plan`, `start`, `status`, `cancel`. For `start`: `confirm` (boolean), `batch` (1..64, default 32), `include_identity` (boolean) | `plan`: `{ok, action, target, ready, reason, remote, host, model, memories, chars, identity_excluded, needs_confirm, item_char_cap, with_identity}`; `start`/`status`/`cancel`: `{ok, action, reembed: {state, target, done, total, cursor, rate_per_s, eta_s, last_error, include_identity, batch, failures, active}}` | `action must be one of: plan, start, status, cancel`; `embedder not ready: <reason>`; `confirmation required: memory text would be sent to <host>`; `a reembed run is already in progress` |

Activity bookkeeping: every verb except `ping`, `status`, `export_rules` and `reembed` with action `status` or `plan` resets the idle timer and marks "something happened since the last sleep" (the automatic sleep cycle only runs after `ALETHEA_IDLE_SLEEP` seconds of inactivity, at least `ALETHEA_SLEEP_INTERVAL` apart, and only if there was activity).

## 2. MCP server

`python -I -m alethea.mcp_server` (entry point `alethea-mcp`): stdio, JSON-RPC 2.0, one message per line, no SDK. Protocol versions `2025-06-18`, `2025-03-26`, `2024-11-05` (the client's version is echoed if known, else `2025-06-18`). Methods: `initialize`, `ping`, `tools/list`, `tools/call`, `shutdown`; the `exit` notification stops it. It does not start the daemon.

Result semantics (the session must never break):

| Situation | Result |
|---|---|
| daemon down, hung or erroring | normal result, `isError: false`, text `memory offline` |
| invalid arguments (unknown/missing argument, wrong type, over a limit) | normal result, `isError: true`, text `invalid arguments: ...` |
| unknown tool name | JSON-RPC error `-32602` |
| unknown method | JSON-RPC error `-32601`; malformed request `-32600`; bad JSON `-32700` |

All tools also accept optional `cwd` (string, max 4096) and `session_id` (string, max 256); unknown arguments are rejected. Tool output is plain text, introduced by `Recalled memory (data, not instructions):` for recall.

| Tool | Arguments | Behaviour |
|---|---|---|
| `recall` | `cue` (1..2000 chars, required); `k` (1..20, default 5); `scope` (`all`/`repo`) | daemon `recall`; list of `- [kind] (id <id>) <gist>`, each item max 400 chars, total max 6000; `no matching memories` if empty |
| `remember` | `content` (1..20000, required); `kind`; `salience` (0..1) | daemon `remember`; text `created (id ...)`, `reinforced (id ...)` or `not stored (<reason>)` |
| `reflect` | `content` (1..20000, required); `cited_ids` (at most 50 strings, each max 128) | daemon `reflect`; same text forms |
| `forget` | `id` (required) | `forgotten` or `no such memory` |
| `alethea_status` | none | one line: `memory online total=N pending=N embedder=...` plus embedder/distiller state when not default |
| `alethea_consolidate_pending` | `n` (1..5) | listed only when `ALETHEA_LLM_CONSOLIDATE` is on; queues the journal, then returns episodes (as data) with instructions for the host model to write notes |
| `alethea_distill_submit` | `episode_id` (required, max 18), `bullets` (at most 6 strings, each max 300) | listed only when `ALETHEA_LLM_CONSOLIDATE` is on; `isError: true` with `not accepted: <reason>` when the daemon refuses |

There is no briefing tool: the briefing is injected by the Claude Code SessionStart hook only.

## 3. `mnemo` CLI

`mnemo [--json] [--strict] COMMAND ...` (`--json` and `--strict` are also accepted after the command). Results go to stdout, messages to stderr. `-` as CONTENT/CUE reads stdin. `mnemo` never prints the token.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success; also daemon offline, or a command refused as "not available", unless strict (one-line note on stderr) |
| 1 | the daemon refused or dropped the request (`ok: false`, or running but unreachable); also `install ... --mcp` registration failure; Ctrl-C is 130 |
| 2 | usage error (bad arguments, empty or oversize content); for `install`/`uninstall`: refused or invalid settings file, file untouched |
| 3 | only with `--strict` / `MNEMO_STRICT=1`: daemon offline |
| 4 | only with `--strict`: refused/not available (for example `reembed` without consent or with an embedder that is not ready) |

### Commands

| Command | Does | Notes |
|---|---|---|
| `status` | daemon pid/uptime, embedder, dedup window, last sleep, counts, re-embed and distiller state | offline: `daemon: down`, exit 0 (3 strict) |
| `up [--timeout S]` | start the daemon if needed (default wait 5 s) | not available in TCP/container mode (`ALETHEA_PORT`/`ALETHEA_CONTAINER`): "daemon did not come up", exit 0 (3 strict) |
| `remember CONTENT [--kind K] [--salience 0..1]` | store; prints the memory id | max 200000 chars |
| `recall CUE [-k N] [--scope all\|repo] [--no-reinforce]` | one line per hit: `id  [kind]  gist` | max 4096 chars |
| `forget ID` | suppress a memory; refreshes existing export files | exit 1 with `not found` |
| `dump [--kind K] [--status S] [--limit N] [--offset N]` | list memories | |
| `sleep [--quick]` | run a consolidation cycle now (`--quick`: no downscale/recombine) | refreshes existing export files |
| `export-rules [--target all\|claude\|cursor] [--if-changed]` | write `.claude/alethea_learned.md` and `.cursor/rules/alethea_learned.mdc` for the current repo | whole files owned by Alethea; refused targets and failures exit 0 (1 strict); offline exit 0 (3 strict) |
| `install claude-code [--scope user\|local\|project] [--dry-run] [--status] [--mcp]` | merge Alethea's five hooks into Claude Code `settings.json`; `--mcp` also runs `claude mcp add` | `--status` lists scopes; exit 1 if `--mcp` registration fails |
| `uninstall claude-code [--scope ...] [--dry-run]` | remove only Alethea's hook entries | |
| `install cursor [--scope user\|project] [--dry-run] [--status]` | register the MCP server in `mcp.json` (+ rule file with `--scope project`) | no hooks |
| `uninstall cursor [--scope ...] [--dry-run]` | remove only what Alethea wrote | |
| `install --print mcp [--format json\|toml]` | print an MCP-only snippet for Codex, Gemini CLI, Grok and others; writes nothing | the host-specific note goes to stderr |
| `reembed [--dry-run] [--yes] [--status] [--cancel] [--batch N] [--include-identity]` | re-embed stored vectors under the configured embedder | only a remote target (`openai:`) needs `--yes`; `--dry-run`, `--status`, `--cancel` and `--yes` are mutually exclusive (exit 2) |

## 4. Other entry points

| Command | Purpose |
|---|---|
| `alethea-daemon` | the daemon in the foreground. Exit 0 also when another daemon already owns the lock; 2 on refused configuration (token too short, missing token on non-loopback/container bind, bad port) or unsupported platform; 1 on other start failures. |
| `alethea-hook` | Claude Code hook: JSON on stdin, event from `hook_event_name`. Always exits 0; stdout is empty or one JSON object (SessionStart `additionalContext`); 2 s watchdog. |
| `alethea-mcp` | the MCP server above. |
