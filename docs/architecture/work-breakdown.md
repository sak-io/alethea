# Engram: Work Breakdown (task cards)

Each card is the whole brief. Read `docs/architecture/lessons.md` first. Tier S = full review loop (max 2 rounds); tier N = single light review. Size target < 400 changed lines. Tests: stdlib `unittest` only, in `tests/` (e2e/hook/concurrency scripts in `scripts/`). Run from repo root. Reqs = ids in docs/specs/engram.md; `T§` = docs/technical-details.md. Full suite: `python3 -m unittest discover -s tests -t .`.

Tags: `[lib]` engine, `[proc]` daemon/client, `[host]` host integration, `[plug]` optional.

## Execution waves (max 2 parallel; a wave starts when all Needs are merged)
W1 T01 | W2 T02 + T03 | W3 T04 + T06 | W4 T05 + T07 | W5 T08 + T11 | W6 T09 + T14 | W7 T10 + T12 | W8 T13 + T18 | W9 T15 | W10 T16 | W11 T17 + T19 | W12 T20 + T22 | W13 T21 + T24 | W14 T23 | W15 (P3) T25 + T27 | W16 T26 + T28 | W17 T29 | W18 T30 | W19 T31.
Count: 31 tasks. Phase 0: 2 (S1 N1). Phase 1: 19 (S8 N11). Phase 2: 3 (S1 N2). Phase 3: 7 (S1 N6). Total S 11, N 20.

---
## PHASE 0: Skeleton and spec-first tests

### T01 Walking skeleton, CI, license, test guards [lib] tier N
Goal: repo builds and tests run; stdlib-only guard in place.
Files: `engram/__init__.py`, `engram/config.py` (constants + env/config loading, paths, 0700 dir helper), `tests/__init__.py`, `tests/test_skeleton.py`, `tests/netguard.py` (patches `socket.socket.connect` to fail on non-loopback), `.github/workflows/ci.yml` (unittest on 3.9 and latest), `LICENSE` (Apache-2.0), `README.md` stub, `.gitignore`.
Reqs: R1, G15, G17, G20. Needs: none.
Verify: `python3 -m unittest tests.test_skeleton -v`

### T02 Adversarial dedup and negation test suite (written BEFORE any similarity code) [lib] tier S
Goal: executable definition of correct dedup. Fixture table `tests/data/dedup_pairs.py`: swapped entities (staging/prod), negation (require / require no), swapped identifiers (`svc0000`/`svc0101`, `v1`/`v2`, `s3`, `p0`, paths, camelCase), antonyms (enable/disable, allow/deny, add/remove, negators), true restatements (must merge), unrelated. Test file asserts via `engram.encode.similarity` and `NEAR_DUPLICATE`; guard `@unittest.skipUnless(hasattr(...))` so the file is red-to-skip now, and T07 removes the guard. Parametrized by an `EMBEDDERS` list (hashed now, others added in T25). Includes the "blend would merge 0.70/0.60 -> 0.93" regression.
Files: `tests/data/dedup_pairs.py`, `tests/test_dedup_adversarial.py`.
Reqs: R10, R11, R9 (mixed-id assertion helper). Needs: T01.
Verify: `python3 -m unittest tests.test_dedup_adversarial -v` (skips until T07; must pass after T07)

---
## PHASE 1: Core

### T03 vectorize: stemmer, hashed embedder, cosine/jaccard, Embedder protocol [lib] tier N
Goal: R8/R9 foundation, no internal deps.
Files: `engram/vectorize.py`, `tests/test_vectorize.py`.
Details: 512-d, unigram 1.0, bigram 0.5, 4-gram 0.12, blake2b salt `engram-v1`, L2-normalized, `array('f')` bytes; `stem()` plural first then longest suffix (aligned with porter in FTS5 on a fixture list); `id = "hashed-ngram-v1:512"`; tokenizer shared with jaccard and veto (no length floor). Test determinism across subprocesses with different `PYTHONHASHSEED`; related 0.3-0.45, unrelated near 0.
Reqs: R8, R9. Needs: T01.
Verify: `python3 -m unittest tests.test_vectorize -v`

### T04 cortex: schema, storage API, edges, journal, sessions, meta [lib] tier N
Goal: R2/R5 persistent layer (no activation yet).
Files: `engram/cortex.py`, `tests/test_cortex.py`.
Details: WAL, tables per T§3, FTS5 triggers (insert/update/delete sync tested), `repo_root` nullable+indexed, `KINDS`, `DECAY`, insert/get/reinforce (strength and access_count both)/link (upsert `min(1.0, w+0.15)`)/journal/sessions/meta, `schema_version`, FTS5-missing degrade flag (G17), injectable clock, one connection guarded by caller lock, 0700 dir.
Reqs: R2, R3 (constants), R5, G17. Needs: T03.
Verify: `python3 -m unittest tests.test_cortex -v`

### T05 activation (ADR 0003) and decay calibration [lib] tier S
Goal: `Cortex.activation()` with `n_eff = strength`, age in days (1-minute floor), salience term `1.6*(s-0.5)`, recency term, d=0 means no age penalty, identity not reduced.
Files: `engram/cortex.py` (activation), `tests/test_activation.py`, `docs/architecture/decay-halflives.md` (generated table: days-to-archive per kind under uniform 0.93 and under per-kind option; for owner).
Reqs: R3, R4, G2, G11, G12. Needs: T04.
Verify: `python3 -m unittest tests.test_activation -v`

### T06 gitctx: repo_root resolution, fail-soft, cache [lib] tier N
Goal: R6/R7.
Files: `engram/gitctx.py`, `tests/test_gitctx.py`.
Details: `realpath(cwd)`, `git rev-parse --show-toplevel` and `--git-common-dir`, parent of common dir; argv list, no shell; 1 s timeout; missing git (patch PATH), non-repo, error -> None; per-cwd cache. Tests build temp repo, subdir, symlink, `git worktree add`.
Reqs: R6, R7. Needs: T01.
Verify: `python3 -m unittest tests.test_gitctx -v`

### T07 similarity engine with vetoes (turns T02 green) [lib] tier S
Goal: `similarity(a,b)`, `novelty` helper, `NEAR_DUPLICATE`, identifier veto x0.55, negation/antonym veto, per-embedder threshold hook.
Files: `engram/encode.py` (similarity section only), `tests/test_similarity.py`, remove skip guard in `tests/test_dedup_adversarial.py`.
Reqs: R10, R11, R9. Needs: T02, T03.
Verify: `python3 -m unittest tests.test_dedup_adversarial tests.test_similarity -v`

### T08 encode scoring: amygdala, durability, classify, novelty [lib] tier N
Goal: R12 scoring with marker weights table; lists defined by developer and committed as fixture tables (G23).
Files: `engram/encode.py` (scoring), `engram/markers.py` (regex lists), `tests/data/phrases.py`, `tests/test_scoring.py`.
Details: <4-word penalty, long bonus; units longest-suffix-first ("seconds" is not bare "s"); `novelty` takes a `candidates` callable (injected in test; deferred import in prod).
Reqs: R12, G23. Needs: T07.
Verify: `python3 -m unittest tests.test_scoring -v`

### T09 recall: candidates, fusion, reinforcement, identity cue [lib] tier N
Goal: R15/R16.
Files: `engram/recall.py`, `tests/test_recall.py`.
Details: forgiving OR FTS query (stem, stopwords), bm25 rank-normalized, vector channel over `max(pool*4,600)` most recently touched rows, only same `embedder_id`, status active only, score formula; reinforce +0.6 hits / +0.35 spreads (spread stub here); `_IDENTITY_CUE`; recall k=8.
Reqs: R15, R16, R9, G5. Needs: T05, T07.
Verify: `python3 -m unittest tests.test_recall -v`

### T10 recall: spreading activation and briefing [lib] tier N
Goal: R15 (2-hop spread), R17.
Files: `engram/recall.py`, `tests/test_spread_briefing.py`.
Details: no-lexical-overlap causal link surfaces; briefing order identity (<=5), semantic+procedural (<=8), episodes (<=3), <=4 KB; `repo_root` else exact `cwd`; repo boost, unscoped eligible (G25); briefing does NOT reinforce (G6); suppressed/archived excluded.
Reqs: R15, R17, G5, G6, G25. Needs: T09.
Verify: `python3 -m unittest tests.test_spread_briefing -v`

### T11 redact: secret redaction (pure) [lib] tier S
Goal: G16 redaction function.
Files: `engram/redact.py`, `tests/data/secrets.py` (positive and negative fixtures), `tests/test_redact.py`.
Details: AWS keys, GitHub/Slack/Stripe-style tokens, JWTs, bearer headers, `password=`/`api_key=` assignments, PEM private-key blocks (multi-line), `ENGRAM_TOKEN` values; replace with `[REDACTED:kind]`; idempotent; must not mangle git SHAs, UUIDs, ordinary hex; linear-time (no catastrophic regex; test with 1 MB adversarial input under 1 s).
Reqs: G16, R14. Needs: T01.
Verify: `python3 -m unittest tests.test_redact -v`

### T12 encode gate and observe [lib] tier S
Goal: R13/R14 with redaction at every ingestion point.
Files: `engram/encode.py` (`encode()`, `observe()`), `tests/test_encode_gate.py`.
Details: trivial chatter reject unless force/deliberate; near-dup reinforces +0.5 and writes nothing, result distinguishes `reinforced` vs `created` vs `rejected`; effective salience formula and floors; threshold 0.34; `observe()` journals redacted content always, promotes at durability >= 0.45; deferred import of `recall.candidates`; reflect support (`abstracts` edges to cited ids, deliberate floor).
Reqs: R13, R14, G3, G16. Needs: T08, T09, T11.
Verify: `python3 -m unittest tests.test_encode_gate tests.test_dedup_adversarial -v`

### T13 consolidate: replay, extractive gist, pre-filter [lib] tier N
Goal: R18.
Files: `engram/consolidate.py` (replay, extract_gist, consolidate_episodes, Distiller protocol with extractive default), `tests/data/transcripts/`, `tests/test_consolidate_gist.py`.
Details: gap > 15 min splits; collapse code fences/diffs/traces/long output (keep command and final error line); split on newlines and bullets; jaccard > 0.6 dedup; top 6 by durability; one episodic summary plus semantic facts via `encode()`, `abstracts` edges; skip < 4 turns low-salience; mark journal rows consolidated.
Reqs: R18. Needs: T12.
Verify: `python3 -m unittest tests.test_consolidate_gist -v`

### T14 downscale and recombine [lib] tier S
Goal: R19/R20.
Files: `engram/consolidate.py` (downscale, recombine), `tests/test_downscale.py`, `tests/test_recombine.py`.
Details: strength x `DOWNSCALE_RATE[kind]^periods` (uniform 0.93 default, ADR 0003), cap 14 days of periods per call, identity exempt, archive at activation < -3.2, hard-delete archived zero-access episodic after 45 days, suppressed prune after 90 days (plan), 6 h gate via `engram_meta.last_downscale`, future/corrupt value clamps to 0 elapsed (test: string, negative, year 3000), second call no-op; recombine top 60 by salience, band 0.34-0.88, max 40 per cycle, same-embedder only.
Reqs: R19, R20, R9, G4, G9, G12. Needs: T05, T07.
Verify: `python3 -m unittest tests.test_downscale tests.test_recombine -v`

### T15 sleep_cycle orchestration, triggers, journal purge [lib] tier N
Goal: R21 + G16 retention.

**Added from T13 review (performance):** the reviewer saw consolidation slow down as memories grow (about 27 ms per episode at 100 memories; 20k journal rows over 4k sessions did not finish in 100 s), likely the encode() dedup scan per write. Before building sleep_cycle, profile it. T15 must include a measurable budget test (e.g. 2k journal rows / 400 sessions consolidate in well under 30 s on 1k existing memories), and if the dedup scan is the cause, bound it (candidate pool cap, vector prefilter) WITHOUT weakening the similarity vetoes or `NEAR_DUPLICATE`.

Files: `engram/consolidate.py` (sleep_cycle with `deep` flag, per-batch lock hooks per ADR 0005), `tests/test_sleep_cycle.py`.
Details: stages replay -> gist -> downscale -> recombine; `deep=False` skips last two; `session_end` runs consolidate_episodes only; purge consolidated journal rows older than 30 days and unconsolidated older than 90 (plan); stage callbacks allow lock release between batches; idempotent and resumable.
Reqs: R18, R19, R20, R21, G16. Needs: T13, T14.
Verify: `python3 -m unittest tests.test_sleep_cycle -v`

### T16 daemon: Engram class, verbs, socket server, sleep loop [proc] tier S
Goal: R22.
Files: `engram/daemon.py`, `tests/test_daemon.py`, `scripts/test_concurrency.py`.
Details: one RLock; `Engram` dispatches remember, recall, observe, briefing, reflect, forget (suppress), sleep, session_end, status, dump, ping, plus `export_rules`/`reembed`/`pending` stubs returning `{"ok":false,"error":"unsupported"}` until later tasks; Unix socket 0600 in 0700 dir with automatic loopback-TCP fallback; NDJSON with 1 MiB line cap and read timeout; bind failure = another instance won (G18); `_sleep_loop` thread, 900 s interval after 180 s idle; per-batch lock release (ADR 0005); `status` fields (R26b). Concurrency test: 32 threads x distinct facts all persist distinct, plus recall during sleep stays under 2 s.
Reqs: R22, R21, R26b, G13, G18. Needs: T15.
Verify: `python3 -m unittest tests.test_daemon scripts.test_concurrency -v`

### T17 daemon TCP auth and container mode (ADR 0004) [proc] tier S
Goal: G14.
Files: `engram/daemon.py` (auth layer, bind policy), `tests/test_tcp_auth.py`.
Details: default 127.0.0.1; `ENGRAM_CONTAINER=1` requires `ENGRAM_TOKEN` (>= 16 chars) else exit non-zero; `hmac.compare_digest`; wrong/missing token -> `auth` error and close; token not in logs/status/dump (test greps captured logs); oversized and slow-loris requests dropped.
Reqs: G14, G15, R22. Needs: T16.
Verify: `python3 -m unittest tests.test_tcp_auth -v`

### T18 client: fail-soft, autostart, timeouts [proc] tier S
Goal: R23. Needs only a protocol, tests use a fake server so it can run beside T13.
Files: `engram/client.py`, `tests/test_client_failsoft.py`.
Details: Unix then TCP discovery (incl. token); catches OSError/timeouts/JSON errors/broken pipe mid-call -> `{"ok":False,"offline":True}`; timeouts hooks 2 s, MCP 5 s, CLI 10 s; `autostart()` spawns daemon detached, retries connect once (G18), no-op in TCP/container mode; no imports from the engine; tests: daemon down, hung server, server killed mid-reply, garbage reply.
Reqs: R23, G18, G19. Needs: T01.
Verify: `python3 -m unittest tests.test_client_failsoft -v`

### T19 MCP server [proc] tier N
Goal: R24.
Files: `engram/mcp_server.py`, `tests/test_mcp_server.py`, `scripts/show_mcp.py`.
Details: stdio JSON-RPC (initialize, tools/list, tools/call, ping); tools recall, remember, reflect, forget, engram_status; sixth tool `engram_consolidate_pending` only when LLM mode enabled (G10); resolves `repo_root` via gitctx at startup and per call if root passed; offline -> tool result text "memory offline", never an error exit; runs via `-m` and by absolute path (ADR 0002, test both); cross-client test: write in repo X, second client in X recalls.
Reqs: R24, R23, G10. Needs: T18, T16.
Verify: `python3 -m unittest tests.test_mcp_server -v`

### T20 mnemo CLI and status [proc] tier N
Goal: R25 (without export) and R26b.
Files: `scripts/mnemo`, `engram/cli.py`, `tests/test_cli.py`.
Details: status, up (autostart), sleep, recall, remember, forget, dump; `export-rules` and `reembed` subcommands registered but delegate later; always exit 0 on offline, empty or one-line output; status shows counts per kind/status, last_sleep, embedder id, FTS5, offline state.
Reqs: R25, R26b, R23. Needs: T18, T16.
Verify: `python3 -m unittest tests.test_cli -v`

### T21 End-to-end, recall regression corpus, performance [lib] tier N
Goal: R1 gate, G27, scale NFRs.

**Added from T10 review (MAJOR for retrieval quality):** `recall.candidates()` counts semantic-only hits with cosine ~0.02 as direct hits, so a nonsense cue returns and REINFORCES (+0.6) unrelated memories. Add a relevance floor (drop sem-only hits with cosine < ~0.10 and relevance < ~0.05; do not apply to lexical hits; tune on a real-phrase fixture) and a test that a nonsense cue returns nothing and reinforces nothing.

Files: `scripts/test_e2e.py`, `tests/data/recall_corpus.py` (fixture corpus with expected top-3, includes held-out phrasing), `tests/test_recall_regression.py`, `tests/test_perf.py` (10k memories, recall p95 < 300 ms; skipped unless `ENGRAM_PERF=1` but run once in review).
Details: remember -> recall -> observe -> sleep -> briefing through a real in-process daemon; run full suite under `tests/netguard.py`.
Reqs: R1, R15, R17, G27, Scale NFR. Needs: T19, T20.
Verify: `python3 -m unittest scripts.test_e2e tests.test_recall_regression -v && ENGRAM_PERF=1 python3 -m unittest tests.test_perf -v`

---
## PHASE 2: Host integration (Claude Code) and export

### T22 Claude Code hooks [host] tier S
Goal: R26 hooks (fail-soft call sites).
Files: `engram/hooks/engram_hook.py`, `scripts/test_hooks.py`.
Details: events SessionStart (emits `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":...}}`, empty output and exit 0 offline), UserPromptSubmit (journal prompt), Stop (last assistant message from payload or transcript path, fail-soft, G7), SessionEnd (session_end + re-export trigger), PreCompact (non-deep sleep); `repo_root` resolved in hook process and passed; verify event names/schema against current Claude Code docs; hard 2 s timeout; never raises, always exit 0 (test with daemon down, hung, malformed stdin).
Reqs: R26, R18, R21, R23, G7, G19. Needs: T16, T18.
Verify: `python3 -m unittest scripts.test_hooks -v`

### T23 Claude Code wiring and directive [host] tier N
Goal: R26 config files.
Files: `.claude/settings.json`, `.mcp.json`, `integrations/claude-code/CLAUDE.snippet.md`, `tests/test_wiring.py`.
Details: settings wires the five events to the hook by relative-safe command; `.mcp.json` registers `engram`; snippet is one sentence plus the `@.claude/engram_learned.md` import line shown as an example (never auto-edited); tests parse JSON and assert commands resolve to existing files and run offline.
Reqs: R26. Needs: T22, T19.
Verify: `python3 -m unittest tests.test_wiring -v`

### T24 export-rules (sync-back) [host] tier N
Goal: R25 export.
Files: `engram/export.py`, `engram/daemon.py` (`export_rules` verb), `engram/cli.py`, `tests/test_export.py`.
Details: select active semantic/procedural for `repo_root` above confidence floor (`access_count >= 2` or deliberate), exclude identity/archived/suppressed, <= 20 items and 4 KB, plain list items only (strip leading `#`, backticks fences, newlines), "auto-generated" header with time; targets `.claude/engram_learned.md` and `.cursor/rules/engram_learned.mdc` (alwaysApply frontmatter); atomic temp+rename; idempotent (stable output, no timestamp churn when content equal); refuses a file lacking the Engram header; gitignore entry added or warning; daemon down leaves file untouched; triggers after SessionEnd, sleep, and forget.
Reqs: R25, G3. Needs: T16, T20, T22.
Verify: `python3 -m unittest tests.test_export -v`

---
## PHASE 3: Optional plugins (cuttable; core must work without them)

### T25 Embedder plugin loading, per-embedder thresholds, fallback [plug] tier S
Goal: R27 part 1.
Files: `engram/vectorize.py` (registry, lazy loader, `ENGRAM_EMBEDDER`), `engram/embedders_ext.py` (local ONNX/fastembed `all-MiniLM-L6-v2`, import only on selection), `tests/test_embedder_plugin.py`, extend `EMBEDDERS` in T02 suite.
Details: missing package or error -> hashed fallback for that call, one log line, lexical unaffected; per-embedder thresholds (conservative hashed defaults); stdlib path never imports extras (test via `sys.modules`); adversarial and antonym suites run for each shipped embedder (a fake dense embedder fixture that places "enable X"/"disable X" close, to prove the vetoes hold; real model test skipped if package absent).
Reqs: R27, R9, R10, R11, G22. Needs: T07, T12.
Verify: `python3 -m unittest tests.test_embedder_plugin tests.test_dedup_adversarial -v`

### T26 Remote embedder and `mnemo reembed` [plug] tier N
Goal: R27 part 2.
Files: `engram/embedders_ext.py` (OpenAI-compatible HTTP embedder, config only, off by default, key from env, never logged), `engram/cortex.py`/`engram/daemon.py` (`reembed` verb batched, resumable via meta cursor), `engram/cli.py`, `tests/test_reembed.py`.
Details: local stub HTTP server in tests; mismatched rows lexical-only until done; resume after interruption.
Reqs: R27, G15, G22. Needs: T25, T20.
Verify: `python3 -m unittest tests.test_reembed -v`

### T27 LLM Distiller (host-callback first) [plug] tier N
Goal: R28.
Files: `engram/consolidate.py` (pending queue, Distiller impl), `engram/daemon.py` (`pending` verb), `engram/mcp_server.py` (`engram_consolidate_pending`), `tests/test_distiller.py`.
Details: default off (`ENGRAM_LLM_CONSOLIDATE`); episodes queued pending, daemon makes no model call in host mode; bullets return via `remember` through full `encode()` gate, length-capped, never executed; malformed/timeout leaves episode unconsolidated on extractive path; consolidated only after success; rate-limited; direct-API mode only during idle sleep.
Reqs: R28, G10. Needs: T13, T16, T19.
Verify: `python3 -m unittest tests.test_distiller -v`

### T28 Docker sidecar [plug] tier N
Goal: R29.
Files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `scripts/test_docker.py` (skips if docker absent), `tests/test_compose_config.py`.
Details: python slim base, no pip, non-root user, volume for `/data`, `ENGRAM_CONTAINER=1`, `ENGRAM_BIND=0.0.0.0`, `ENGRAM_TOKEN` from env file (required), publishes `127.0.0.1:PORT` only; static test parses compose for loopback publish and token; live test ping and remember/recall from host when docker present.
Reqs: R29, G14. Needs: T17, T20.
Verify: `python3 -m unittest tests.test_compose_config scripts.test_docker -v`

### T29 Cursor integration [plug] tier N
Goal: R30.
Files: `integrations/cursor/mcp.json`, `integrations/cursor/engram.mdc`, optional `integrations/cursor/hooks.example.json` (fixture-tested, G8), `tests/test_cursor.py`.
Details: mcp.json launches `python3 <abs path>/engram/mcp_server.py`; `.mdc` has `description`, `alwaysApply: true`, directive; briefing via exported `.cursor/rules/engram_learned.mdc`; cross-host test: write via Claude Code path, recall via Cursor MCP command in same repo.
Reqs: R30, G8, G26. Needs: T19, T24.
Verify: `python3 -m unittest tests.test_cursor -v`

### T30 Installer (`mnemo install`) [plug] tier N
Goal: R31.
Files: `engram/installer.py`, `engram/cli.py`, `tests/test_installer.py`.
Details: detect Claude Code (`.claude/`, `claude` on PATH) and Cursor (`.cursor/`); write only detected hosts' MCP registration, snippet, hook wiring; merge JSON without clobbering user keys; idempotent; `--dry-run`; reports changes; never edits CLAUDE.md or `.cursor/rules` hand-written files (prints the import line instead).
Reqs: R31. Needs: T23, T29.
Verify: `python3 -m unittest tests.test_installer -v`

### T31 Release docs and final checks [plug] tier N
Goal: finish docs and cross-checks.
Files: `docs/architecture/architecture.md`, `docs/architecture/threat-model.md`, `docs/architecture/api.md` (consolidated from plan + code), `README.md`, `docs/traction-checklist.md` (doc only), MCP-only note for Codex/Gemini/Grok (G1).
Details: README quickstart verified by running its commands; api.md lists every verb and error; update `status.md`.
Reqs: G1, G20, G26, R1. Needs: T21, T24, T28, T30 (T25-T27 optional).
Verify: `python3 -m unittest discover -s tests -t . && python3 -m unittest discover -s scripts -p "test_*.py" -t .`
