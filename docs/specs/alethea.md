# Alethea: Requirements Spec

Sources: `[C§n]` = docs/product/product-concept.md, `[T§n]` = docs/architecture/technical-details.md. Source text is not restated; read the cited section. Numeric values are the reference starting values `[T preamble]`: tests assert the stated values but they stay configurable constants.

## 1. Goal, actors, archetype
Single-user, open-source, offline-first long-term memory for AI coding agents, modeled on human memory `[C§1,§3,§8]`. Archetype: library-cli (+ daemon, MCP server, hooks). Actors: **Developer** (human user), **Agent** (Claude Code, Cursor; Codex/Gemini/Grok via MCP only), **Host hook** (lifecycle events), **Operator** (runs daemon/Docker).

## 2. Scope
**v1 = everything in both documents (owner decision).** Delivery phases only order the work:
- **P1 Core (must):** R1-R16, R20-R24, R26.
- **P2 Host integration (must):** R17-R19 (Claude Code), R25 export.
- **P3 Optional / lower priority:** R27 learned/remote Embedder, R28 LLM Distiller, R29 Docker sidecar, R30 Cursor integration, R31 single-command installer.

Non-goals `[C§8]`: multi-tenancy, audit logging, tenant policy, funded/commercial GTM. No writes into hand-written `CLAUDE.md`/`.cursor/rules` files `[C§4,T§8]`.

## 3. Requirements and acceptance criteria (Given/When/Then, each automatable)

### Storage and model
- **R1 Stdlib-only, offline default** `[C§5.3,T§1]`. Given a clean Python 3 with no site-packages and no network, When the full P1 test suite runs via `python3 -m unittest discover`, Then it passes and no socket to a non-loopback address is opened.
- **R2 Schema** `[T§3 Schema]`. Given a new DB, Then WAL is on, tables `memories, mem_fts, edges, journal, sessions, alethea_meta` exist with listed columns, FTS triggers keep `mem_fts` in sync on insert/update/delete, `repo_root` is nullable+indexed.
- **R3 Four kinds with decay exponents** `[C§4,T§3 Stores]`. Then `DECAY` = 0.5/0.2/0.1/0.0; identity strength and activation are not reduced by `downscale`.
- **R4 Activation** `[T§3 Activation]`. Given two memories, When one has more recent reinforcement, Then B = ln(n/(1-d)) - d·ln(age) + 1.6(salience-0.5) + recency term ranks it higher; d=0 yields no age penalty (see G11, G12).
- **R5 Edges** `[T§3 Schema]`. When the same link is upserted twice, Then weight = min(1.0, w+0.15).

### Repo context
- **R6 repo_root resolution** `[C§4,T§3 Repo ctx,T§10]`. Given one repo reached via subdirectory, symlink, and second worktree, Then all resolve to the same `repo_root` (parent of git-common-dir); `cwd` stores the literal path; branch is not in the key.
- **R7 Fail-soft git** `[T§3]`. Given no `git` binary, a non-repo dir, or a >1 s timeout, Then `repo_root=NULL`, no exception, session unaffected; results cached per cwd.

### Embedding and similarity
- **R8 Hashed n-gram embedder** `[T§4]`. Then vectors are 512-dim, L2-normalized, built from stemmed unigrams (1.0), bigrams (0.5), 4-char-grams (0.12) with blake2b salt `alethea-v1`; the same text yields identical bytes across processes with different `PYTHONHASHSEED`; related text ≈0.3-0.45 cosine, unrelated ≈0.
- **R9 Embedder protocol + stamping** `[T§4]`. Then every vector row has `embedder_id`; vectors with differing ids are never compared (assert in a mixed-id test).
- **R10 Similarity safety** `[C§5.2,T§5 Similarity]`. Then `similarity=max(cosine,jaccard)` (never blend); identifier veto (digit, path separator, camelCase in symmetric difference, no token length floor) multiplies by 0.55; `NEAR_DUPLICATE=0.88`. Adversarial suite (swapped entities, negation, swapped identifiers, true restatement) merges **only** the true restatement. Suite written before similarity code.
- **R11 Negation/antonym veto** `[T§4]`. Given "enable auth" vs "disable auth" (also allow/deny, add/remove, negators), Then similarity is damped below `NEAR_DUPLICATE` under every shipped embedder.

### Encoding
- **R12 Salience/durability/novelty/classify** `[T§5 Scoring]`. Then each marker class in the weight table scores per its weight on fixture phrases (<4-word penalty, long bonus); `durability` adds identity, decision, quantitative (longest-suffix-first, "seconds" ≠ bare "s"), causal, rule signals; `novelty = 1 - best_similarity` (not rank-normalized: top hit on a 1-item corpus of different text is not novelty 0); `classify` routes to the four kinds per cue order.
- **R13 encode() gate** `[T§5 gate]`. Then trivial chatter is rejected unless force/deliberate; near-duplicate reinforces +0.5 and writes nothing new; effective = affect·(0.45+0.55·surprise) with floors (explicit, identity 0.62, deliberate 0.55); below 0.34 rejected unless forced/deliberate. API reports reinforcement vs new write distinctly.
- **R14 observe()** `[T§5]`. Then raw content is always journaled; promoted to LTM iff durability ≥ 0.45.

### Retrieval
- **R15 Fused recall** `[C§4,T§6]`. Given a corpus, Then score = relevance·(0.55+0.45·sigmoid(activation)) with relevance = 0.55 lex + 0.45 min(1, 1.8 sem); FTS covers whole corpus, vector channel only the `max(pool·4,600)` most recently touched rows; bm25 rank-normalized; 2-hop spreading over edges surfaces a causally linked memory with no lexical overlap; a reinforced-last-week memory outranks a slightly better untouched match.
- **R16 Identity cue and reinforcement** `[T§6]`. Given "who am I talking to?", Then top identity rows are returned. Every returned hit gains +0.6 strength, spreads +0.35.
- **R17 Briefing** `[C§4,T§6 briefing]`. Then order is identity, top semantic+procedural by activation, recent episodes; scoped by `repo_root` else exact `cwd`; repo-scoped get a small boost, nothing excluded outright.

### Consolidation
- **R18 Sleep cycle** `[C§4,T§7]`. Then `replay` splits episodes at >15 min gaps; extractive gist keeps top 6 by durability after jaccard>0.6 dedup, writes one episodic summary + semantic facts linked by `abstracts` edges, skips <4-turn low-salience episodes; code fences, diffs, traces, long output are collapsed (keep final error line + command) and sentences split on newlines/bullets (fixture transcripts yield coherent gists).
- **R19 downscale** `[T§7.3,T§10]`. Then strength ×0.93^periods (cap 14 days), archive at activation < -3.2, hard-delete archived zero-access episodic after 45 days; second call within 6 h is a no-op; future/corrupt `last_downscale` clamps to 0 elapsed.
- **R20 recombine** `[T§7.4]`. Then top 60 by salience, `recombined` edges for 0.34 ≤ sim ≤ 0.88, max 40 per cycle.
- **R21 Triggers** `[T§7 Triggers]`. Then timer sleeps every 900 s only after 180 s idle; `PreCompact` runs non-deep sleep (no downscale/recombine); `SessionEnd` runs `consolidate_episodes` only.

### Processes and fail-soft
- **R22 Daemon** `[T§8]`. Then single writer behind one RLock; newline-delimited JSON over Unix socket with automatic loopback-TCP fallback; verbs remember, recall, observe, briefing, reflect, forget, sleep, session_end, status, dump, ping. Concurrency test: N parallel distinct facts all persist as distinct memories.
- **R23 Fail-soft client** `[C§5.1,T§8 client,T§10]`. Given daemon down/slow/killed mid-call, Then hooks, MCP server and `mnemo` return `{"ok":False,"offline":True}`/empty output, exit 0, never raise; `autostart()` is a no-op in TCP/sidecar mode.
- **R24 MCP server** `[T§8,T§8 repo_root in MCP]`. Then a hand-rolled stdio JSON-RPC server (no SDK) lists and serves recall, remember, reflect, forget, alethea_status; resolves `repo_root` itself; a memory written via the MCP server in repo X is recalled by a second client in X.
- **R25 mnemo CLI + export-rules** `[T§8 mnemo,T§8 Sync-back,T§10]`. Then CLI has status, up, sleep, recall, remember, forget, dump, export-rules (+ reembed R27). Export: idempotent, atomic (temp+rename), ≤20 items/4 KB, active semantic/procedural for `repo_root` above confidence floor, excludes identity/archived/suppressed, plain list items only, "auto-generated" header, gitignored by default (adds entry or warns), refuses to touch a file it does not own, leaves old file intact if daemon down, re-exports on SessionEnd, after sleep and after `forget`.
- **R26 Claude Code integration** `[C§4,T§8 Claude Code,T§10]`. Then `.claude/settings.json` wires SessionStart, UserPromptSubmit, Stop, SessionEnd, PreCompact; SessionStart emits valid `hookSpecificOutput.additionalContext` JSON and empty output/exit 0 when offline; `.mcp.json` registers `alethea`; `CLAUDE.snippet.md` is the one-sentence directive; `CLAUDE.md` imports `@.claude/alethea_learned.md` via snippet, never auto-edited.
- **R26b Observability / secrets** (capabilities). `alethea_status` reports counts per kind/status, last_sleep, embedder id, offline-state (G24); config via env/config file with no secrets in repo (G15).

### Phase 3 (optional, lower priority)
- **R27 Learned/remote Embedder** `[C§7,T§4,T§12]`. Given `ALETHEA_EMBEDDER` selects a local ONNX/fastembed (`all-MiniLM-L6-v2`) or remote embedder, Then it loads lazily (stdlib path never imports it), per-embedder thresholds apply, failure/missing package falls back to hashed for that call with one log line and lexical recall unaffected, `mnemo reembed` is batched+resumable and until done mismatched rows are lexical-only, and the adversarial + antonym suites pass for each shipped embedder.
- **R28 LLM Distiller** `[C§7,T§7.2]`. Given `ALETHEA_LLM_CONSOLIDATE` on, Then episodes queue as pending, `alethea_consolidate_pending` exposes them to the host agent (daemon makes no model call in this mode), bullets return via `remember` through the full `encode()` gate, length-capped, never executed; malformed/timeout output leaves the episode unconsolidated and on the extractive path; episodes marked consolidated only after success; rate-limited. Direct-API mode runs only in idle sleep. Default off.
- **R29 Docker sidecar** `[T§2,T§8 Deployment,T§11]`. Then `Dockerfile`+`docker-compose.yml` build a stdlib-only image running the daemon in TCP mode; `docker compose up` yields a passing `ping`/E2E remember-recall from the host.
- **R30 Cursor integration** `[C§4,C§7,T§8]`. Then `cursor/mcp.json` launches `python3 <abs path>/mcp_server.py`; `alethea.mdc` has `description`, `alwaysApply: true` and the directive; briefing reaches Cursor through the exported `.cursor/rules/alethea_learned.mdc`; capture is voluntary unless Cursor hooks exist (G8); cross-host test: Claude Code write recalled from Cursor MCP in same repo.
- **R31 Installer** `[C§7,C§8,T§12]`. Given Claude Code and/or Cursor present, When the installer runs once, Then it writes only the detected hosts' MCP registration, directive snippet and hook wiring, is idempotent, and reports what it changed.

## 4. Non-functional needs
- **Availability/safety:** memory failures never surface to the session `[C§5.1]` (R23). Hook latency budget: ASSUMPTION p95 < 1 s when daemon is up, hard timeout 2 s.
- **Scale:** ASSUMPTION single user, ≤100k memories, ≤1 req/s sustained; recall p95 < 300 ms at 10k memories.
- **Privacy/security:** journal stores every raw turn and may hold secrets or PII (flag for architect and security-auditor); local-only data; DB dir 0700, socket 0600; TCP mode loopback-bound (G14, G15). LLM and exported text are untrusted prompt input `[T§7,T§8]`.
- **Compat:** Python version, SQLite with FTS5 (G17).
- **Budget/cost:** zero by default `[C§5.3]`.

## 5. Assumptions
A1 Thresholds and weights are constants, tuned by tests `[T preamble]`. A2 Tests run with `unittest` only `[T§1]`. A3 Layout per `[T§2]`; tests live in `scripts/` per source, plus unit tests per module. A4 Codex/Gemini/Grok are supported through the MCP server only, no dedicated integration files. A5 CLI binary is named `mnemo` as in the docs even though the product is Alethea.

## 6. Gaps, ambiguities and contradictions
1. Concept lists Codex/Gemini/Grok as targets `[C§1,§6]`; tech plan specifies only Claude Code and Cursor. RESOLVED (AGREED by owner): Codex/Gemini/Grok are MCP-only, documented, covered by the generic MCP test.
2. Strength vs activation: `activation` uses access count `n`, but `downscale` multiplies `strength` and archives by activation `[T§3,§7]`, so decay would not change activation. Default: use `n_eff = strength` (float) in the formula; reinforce increments both.
3. `reflect` and `forget` semantics undefined `[C§4,T§8]`. RESOLVED (AGREED by owner): `reflect` = deliberate write of a synthesized insight (runs `encode()` with deliberate floor, links `abstracts` edges to cited ids); `forget` = status `suppressed` (hidden from recall and export, eligible for later prune), not hard delete.
4. Status `suppressed` vs `archived` lifecycle and whether archived semantic/procedural memories are ever deleted `[T§3,§7]`. Default: only archived zero-access episodic are hard-deleted; archived others kept and excluded from recall.
5. Briefing and recall limits (counts, byte size) unspecified `[T§6]`. Default: recall k=8; briefing identity ≤5, semantic+procedural ≤8, episodes ≤3, ≤4 KB.
6. Does briefing reinforce what it returns? `[T§6]`. Default: no (avoids self-reinforcing loop); only explicit recall reinforces.
7. How hooks capture assistant output: events listed but payload/transcript use unspecified `[T§8 hooks]`. Default: UserPromptSubmit journals the prompt; Stop journals the last assistant message from the hook payload or transcript path, fail-soft if unavailable.
8. Cursor hook availability "if the installed version provides them" is untestable `[T§8]`. Default: ship voluntary+export path only; Cursor hook wiring behind a documented, fixture-tested optional file.
9. Identity exemption incomplete: archive and prune rules do not say identity is exempt `[C§4,T§7]`. Default: identity never archived or pruned.
10. Tool list conflict: MCP server exposes five tools `[T§8]` but `alethea_consolidate_pending` is a sixth `[T§7]`. Default: sixth tool registered only when LLM consolidation is enabled.
11. `ln(age)` undefined at age 0 `[T§3]`. Default: age floored at 1 second.
12. Activation threshold -3.2 and weights tuned for what time scale is unstated. Default: tests assert behavior on simulated clocks (fake time injection), not raw constants alone.
13. Verbs missing for features: no daemon verbs for export, reembed, pending-consolidation `[T§8]`. Default: add `export_rules`, `reembed`, `pending`/`consolidate_pending`.
14. TCP/sidecar mode has no authentication `[T§8 Deployment]`, conflicting with local-data privacy. RESOLVED (AGREED by owner): TCP mode binds 127.0.0.1 by default and requires `ALETHEA_TOKEN` in container mode. Architect note: see ADR 0004 (container needs an explicit non-loopback bind inside the container, compose publishes loopback only, daemon refuses to start without a token).
15. Storage locations, permissions and config file format unspecified; secrets (remote embedder/LLM API keys) handling absent `[T§4,§7]`. Default: `~/.alethea/` (0700), env vars preferred, config file 0600, keys never logged or exported.
16. Journal retention and secret redaction: every turn stored raw `[C§4,T§3]`. RESOLVED (AGREED by owner): redact common secret patterns (keys, tokens, private-key blocks) before journaling; purge consolidated journal rows after 30 days.
17. Python minimum version and FTS5 availability unspecified `[T§1]`. Default: Python 3.9+; startup check reports missing FTS5 in `status` and degrades to vector-only without crashing.
18. Daemon autostart race when two sessions launch simultaneously `[T§8]`. Default: bind failure means another instance won; client retries connect once.
19. Hook and client timeouts unspecified `[T§8]`. Default: hooks 2 s, MCP calls 5 s, `mnemo` 10 s.
20. Open decisions in concept `[C§8]`: license, repo structure, traction checklist. RESOLVED (AGREED by owner): Apache-2.0. Other defaults accepted: layout per `[T§2]` (see ADR 0002), checklist as a doc only (no code).
21. Installer is "not yet scoped" `[C§8]` yet listed as extension `[T§12]`; owner scope says include. Default: a stdlib `mnemo install` command, P3, per R31.
22. Remote-API embedder provider unnamed, and remote use contradicts "no network calls" only when enabled `[C§5.3,T§4]`. Default: generic OpenAI-compatible HTTP endpoint via config, off by default.
23. Many regex/marker lists (trivial chatter, identity cues, decision, causal) are described, not enumerated `[T§5]`. Default: developer defines lists; acceptance via fixture phrase tables committed with tests.
24. Observability not specified (logging, metrics, status contents) `[T§8]`. Default: `alethea_status` fields in R26b, rotating log in `~/.alethea/`, log-once for embedder fallback.
25. Concept says briefing episodes come from "current repository"; tech's cwd fallback applies when `repo_root` is NULL `[C§4,T§6]`: consistent, but unscoped memories (NULL repo) in a repo session are unspecified. Default: they are eligible with no boost.
26. Docs name "mnemo" CLI and "Alethea" product, and tech §2 layout root is `alethea/` while `[T§8]` Cursor path uses `/path/to/alethea/mcp_server.py`; the `-m alethea.mcp_server` form is rejected for Cursor only. Default: support both entry styles for Claude Code, absolute path for Cursor.
27. No acceptance numbers for recall quality beyond ranking examples `[T§10]`. Default: a committed fixture corpus with expected top-3 hits as regression gate.

Handoff: From requirements-analyst to architect. Status: spec complete. Owner answered G1, G3, G14, G16, G20 (RESOLVED above); all other gaps: recommended defaults ACCEPTED by owner. V1 scope = everything in the docs. Architect flags G2 as needing a calibration decision (ADR 0003, plan section 3).
