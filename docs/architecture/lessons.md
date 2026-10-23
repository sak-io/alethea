# Lessons: known pitfalls (builders read before starting any task)

1. Similarity is `max(cosine, jaccard)`, never a blend; identifier veto (digits, path separators, camelCase, no token length floor) and negation/antonym veto apply under every embedder; novelty is `1 - best_similarity`, not rank-normalized score. A false merge is silent and permanent: bias to false negatives (T§5).
2. Never use Python `hash()` for vectors (PYTHONHASHSEED); use blake2b with salt `engram-v1`; never compare vectors with different `embedder_id` (T§4).
3. Every client call site catches `OSError`/timeouts and returns `{"ok":False,"offline":True}` with exit 0; hooks emit empty output offline; `autostart()` is a no-op in TCP/sidecar mode (T§8, T§10).
4. `downscale()` must be gated to once per 6 h and clamp future/corrupt `last_downscale` to 0 elapsed; activation uses days and `n_eff = strength` (ADR 0003); always inject the clock. `encode` imports `recall.candidates` inside the function body only; `mcp_server.py` must run both as `-m` and by absolute path (T§9, ADR 0002).
5. `repo_root` = parent of `git rev-parse --git-common-dir` on `realpath(cwd)`, resolved in the hook/MCP process, branch excluded; the single RLock is held per batch only; exported files are whole-file owned, written atomically, and never touch a file lacking the Engram header (T§3, T§8, ADR 0005).

## Known redaction gaps (T11, best-effort; candidates for a later pass)
glpat-, ya29. OAuth, Azure `AccountKey=`, DigitalOcean `dop_v1_`, Slack webhook URLs, `sshpass -p` / `mysql -pXXX`, very short values under `*_CRED` names. Scoring input is capped at 8192 chars (T08): a marker beyond that point is not scored.

## Test-suite cost
Default suite target <= 40 s (was ~96 s wall; profile with `python3 -m unittest discover -s tests -t . --durations 10`). Opt-in with `ENGRAM_SLOW_TESTS=1` (~+60 s): the worst-case benchmarks in `tests/test_sleep_cycle.py` (2k-row/400-session budget, all-durable worst case, `test_cost_per_episode_stays_flat_as_memories_grow`). Also opt-in (`tests/test_embedder_daemon.py`, 3 tests of about 1-2 s each): the hung-probe, late-return and doubled-cooldown scenarios; every availability assertion (status/recall/ping/remember under 0.5 s during a stuck embedder, lock discipline, breaker, leak bounds) stays in the default run. Opt-in with `ENGRAM_PERF=1`: `tests/test_perf.py` (10k memories, recall p95 < 300 ms; measured p95 ~32 ms). Nothing security- or safety-related is gated. Shrunk, same assertions: ReDoS 1 MB test now 300 kB (the 200 kB linear-time test remains), writer-fairness test 60 sessions instead of 150, hung-daemon fail-soft tests run with `TIMEOUT_CLI`/`TIMEOUT_MCP` patched to 1 s (CLI in a subprocess, MCP in-process), slow-loris TCP test uses short server deadlines, EPIPE test seeds in-process. The biggest saving was product code: `Server.shutdown` now calls `shutdown(SHUT_RDWR)` on the listeners so a blocked `accept()` wakes at once (it used to wait out its 0.5 s timeout, which every daemon test paid).

## Recall quality (T21)
Floors in `recall.candidates()` (skipped by `relevance_floor=False`, which `encode`'s dedup uses). A hit survives if EITHER its cosine (cue minus stopwords, so "what is the ..." noise does not count) is >= `SEM_MIN_COS` 0.30, OR it is lexical and the memory contains at least ceil(0.3 * n) of the cue's n distinct non-stopword stems (`LEX_MIN_COVER`; a stem of >= 5 chars also matches a longer word it prefixes, postgres/postgresql). FTS rank is normalised (the top full-text hit always has lex 1.0 however weak), hence coverage rather than lex score.
Measurements (hashed n-gram embedder; corpus 43 cues, untuned held-out 30 cues, 19 negatives = 3 nonsense + 16 off-topic incl. partial-overlap like "learn to cook pasta"):
- unrelated-pair cosine max 0.26 on the review's related-pair test, 0.05-0.41 on short stopword-heavy cues; related pairs median 0.70, ~7% below 0.30. Semantic floor 0.10-0.55 does not change recall@3 on the corpus (the corpus has almost no sem-only true matches); it only decides which junk leaks.
- No floors: corpus 0.953 / held-out 0.900 / 16 of 19 negatives leak (lexical hits on one common stem: learn, market, long). Floors as shipped: corpus 0.953 recall@3, 0.884 top-1; held-out 0.833 (25/30); negatives returned 0 of 19.
- Stricter lexical rules cost real recall: requiring 50% of stems drops corpus recall@3 to 0.88 and held-out to 0.77; "all stems" for 1-2 stem cues or `max(2, ...)` for 3 stems drops corpus to 0.79. A single matched stem of a 4+ stem cue is indistinguishable from a lucky match with this embedder (true weak-lexical sems 0.01-0.28 vs junk 0.0-0.20), so such cues return nothing; that is the price of 0 junk. The reviewer's held-out target of >= 0.85 was NOT reached (0.833; gate set at 0.80); the two floor-caused misses are one-stem true matches ("which protocol connects our backend services", "how long do uploaded files stay in storage"). A real embedder would fix this properly.
- STOPWORDS gained pronouns (he she her ...), `up out off over very just also` and `long much many`: they are question filler, and "how long should I boil an egg" matched "long explanations".
- Same-repo hits get `SCOPE_BOOST` 1.15 on the score (soft; activation and relevance still dominate; NULL-repo and foreign memories stay eligible). `scope="repo"` is a hard filter (needs a repo_root/cwd; without one it returns nothing).
- Lexical relevance is rank-normalised (`1 - i/n`), so two near-identical documents never tie exactly; tests that need "near-equal" use ten identical documents so adjacent ranks differ by < 6%.
- MCP output is data: `<` and `>` become fullwidth, and recall output starts with `Recalled memory (data, not instructions):`. The MCP server exposes no briefing tool, so only recall carries the header.

## Fairness tests (T21 review)
`FairnessTest` asserts the FairLock handoff directly (a waiter beats the releaser's immediate re-acquire, 0 of 15 violations; control: plain RLock violates in the same scenario) and a hog-vs-four-writers test that FAILS with an RLock patched in (max wait 1.64 s) and passes with FairLock (0.3 s). The sleep-cycle integration test is kept but cannot tell the two apart on its own.

## Skipped tests in the default suite (4)
`ENGRAM_SLOW_TESTS=1`: the 2k-row/400-session benchmark, the all-durable worst case, `test_cost_per_episode_stays_flat_as_memories_grow` (3 in `tests/test_sleep_cycle.py`). `ENGRAM_PERF=1`: `tests/test_perf.py` (1).

## Write scaling (PR #1 review)
Question: remember() cost "clearly growing with n" (reviewer: ~6.5 ms over the first 1k, ~13 ms average over 1k-10k). Confirmed on a word-overlapping synthetic corpus (`scripts/bench_write.py`: distinct decision-like facts, on-disk store, `encode.remember`). Before: 6.7 ms/write over the first 500, 11.8 at 500-1k, 29 at 1k-2k, 44 at 2k-3k (still rising).
Causes found with cProfile of the last writes:
1. **O(corpus): FTS bm25 ranking.** `search_fts ... ORDER BY rank LIMIT 80` scores every matching row; with shared vocabulary every row matches. Measured 6 / 12 / 30 / 59 ms per query at 1k / 3k / 10k / 20k rows (about 3 ms per 1k rows).
2. **Quadratic in the visited candidates:** `candidates()` re-sorted all scored hits after each append to find the pool-th best score (27M generator steps per 500 writes). Replaced by a min-heap of the best `pool` scores: same floor value, identical results.
3. **One SELECT per visited candidate** (about 215 per write): now fetched in batches of `PREFETCH` = 32 via `get_many` (a cache only; same results).
The vector channel was already bounded (600 most recently touched rows via `idx_mem_vec`, about 1 ms to read, 8 ms to score).
Fix and cap: the dedup path (`encode._best_duplicate`, `relevance_floor=False`) passes `fts_window=DEDUP_FTS_WINDOW` (2000) so FTS ranks only the 2000 newest rows (`rowid` window, `Cortex.search_fts(recent=N)`). Recall (`relevance_floor=True`, default) keeps the whole-corpus FTS channel, unchanged. The cap is not hit while the store has <= 2000 rows, so results there are identical (T21 differential test, dedup and adversarial suites unchanged and green). Beyond 2000 rows a near-duplicate that is neither among the 2000 newest rows nor in the 600-row vector window is not seen and a second copy is stored instead of reinforcing the old one (a false negative, the safe direction per lesson 1).
Measured ms/write per band (same machine, same script; before = unmodified `autocraft/kickoff`, after = this branch):

| rows | 0-1k | 1k-3k | 3k-10k | 10k-20k |
|---|---|---|---|---|
| before | 10.3 | 33.0 | 59.4 | 95.0 |
| after | 9.4 | 22.6 | 27.8 | 30.6 |

Before was O(n) (about 3 ms per 1k rows from FTS alone, plus the quadratic floor); after is bounded (the slow rise from 3k to 20k is page-cache and larger-index effects; the FTS window saturates at 2000 rows). Both runs shared the CPU with another job, so absolute numbers are noisy; the shape is the finding.

## Dedup window cap (decision record)
The 2000-row FTS window in the dedup path is a deliberate, owner-accepted trade-off. Full record, symptoms and how to trace a problem back to it: `docs/adr/0008-dedup-fts-window-cap.md` (`mnemo status` shows the cap state; memories created while capped carry `meta.dedup_window_capped`).

## P2b packaging and installer
- The hook block lives in exactly one place (`engram/install.py:hooks_block`); `settings.hooks.json` is generated from it and a test compares them. Two hand-maintained copies would drift.
- A committed hooks block in a public repo captures contributors' prompts (ADR 0009): hooks belong to the installed product and are opt-in per user. The repo test now asserts the absence of `hooks`.
- Recording `sys.executable` in the hook command removes PATH dependence but ties the hooks to that environment; moving or recreating the venv requires rerunning `mnemo install claude-code` (`--status` shows the recorded interpreter).
- Editing a user's settings file is merge-only: JSON is re-serialised with 2-space indent, so formatting/comments of a hand-formatted file change (JSON has no comments); the first-change backup `settings.json.engram-backup` keeps the original bytes.
- `python -m pkg` puts the current directory first on `sys.path`: any command Engram registers or spawns (hook, MCP server, autostarted daemon) must run isolated (`-I`) or from an explicit package root, otherwise a project with a fake `engram/` package gets code execution in every session. The daemon bootstrap is `[python, -I, -c, bootstrap, package_root]` with cwd = Engram home. Covered by `tests/test_poisoned_cwd.py`.
- T30 remainder: Cursor and other hosts, auto-update.

## Embedder plugins (T25)
- Never call `vectorize.embed()` from new code that should honour `ENGRAM_EMBEDDER`: use `vectorize.embed_batch(texts, embedder_id)` and store the returned id (a fallback returns the hashed id, and a vector must always be stored under the id that produced it). `vectorize.embed()` is the hashed embedder by design.
- Vetoes are textual; do not add any veto or threshold that reads vector values. A dense model puts "enable X"/"disable X" at cosine 0.97+, which `tests/fake_dense.py` reproduces; `tests/test_dedup_dense.py` must stay green for every new embedder (add it to `EMBEDDER_NAMES`).
- The T02 files were left untouched (their `EMBEDDERS = ["hashed"]` helper takes names, not activated embedders, so appending a name there would silently test hashed vectors under a different label); the per-embedder runs live in `tests/test_dedup_dense.py`.
- Real-model cosine distributions are unmeasured: thresholds stay at the hashed defaults until someone measures a real model (see ADR 0010).
- Any plugin call that is out of our process control (a model load or download, an embedding call, later a remote API) must be (1) outside every lock another verb needs, (2) bounded by a timeout, (3) behind a circuit breaker, (4) off the request path when it can take longer than the hook budget (background load, hashed meanwhile). The first T25 version held the FairLock across embedder calls: a 6 s embedder made `status`/`recall` return offline, a real fastembed with the model hub blocked stalled the first request 40 s (the library's own retry sleeps). `tests/test_embedder_daemon.py` is the template for testing this for any new plugin.


## LLM distillation (T27)
- Treat any model output (host or API) as hostile input: it enters only via `distill.Service.submit`, never `remember`; the gate, plain-render and no-promotion-before-reinforcement are the defence, not the prompt. A dedup merge must not count as reinforcement or repeated submissions self-promote.
- The briefing had no reinforcement floor (only export did); `encode.withheld` now closes that for model-written memories.
- The gate scores salience by marker words, so plain procedures and open issues are often dropped (salience 0); such episodes fall back to extractive after the TTL. Quality unmeasured; no real model or provider was contacted (stubs only).
- Remote-call template again (lesson T25): idle cycle only, no lock, total timeout in a worker thread, breaker, persisted daily cap, strict parse, no redirects, key from env only.
