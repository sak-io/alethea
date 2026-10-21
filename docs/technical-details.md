# Engram – Technical Details

A specification of Engram, written so the project can be built from this document. For the product rationale, see `product-concept.md`.

The numbers below (thresholds, weights, decay rates) are the reference starting values. Verify and tune them with tests.

---

## 1. Constraints and stack

- Pure Python 3 standard library. No pip installs, no network calls, no embedding API.
- Storage: one SQLite file in WAL mode, with FTS5 for full-text search.
- Single writer: one daemon process serializes all mutations behind a single `threading.RLock`.
- Test runner: stdlib `unittest` (`python3 -m unittest discover`). `pytest` is not assumed.

## 2. Layout

```
engram/
  cortex.py        SQLite schema, decay/activation math, low-level storage API
  encode.py        salience scoring, novelty/dedup, classification, the encode() gate
  recall.py        retrieval: lexical+semantic fusion, spreading activation, briefing
  consolidate.py   sleep_cycle(): replay -> gist -> downscale -> recombine
  vectorize.py     hashed n-gram embeddings, stemmer, cosine/jaccard
  daemon.py        always-on process: socket server, verb dispatch, sleep timer
  client.py        thin fail-soft client used by hooks, MCP server, and mnemo
  mcp_server.py    stdio JSON-RPC MCP server: recall/remember/reflect/forget/engram_status
  hooks/engram_hook.py            involuntary pathway: SessionStart/UserPromptSubmit/Stop/SessionEnd/PreCompact
  scripts/
    mnemo                     CLI: status/up/sleep/recall/remember/forget/dump
    test_e2e.py, test_hooks.py, test_concurrency.py, show_mcp.py
  .mcp.json                      registers the `engram` MCP server for Claude Code
  .claude/settings.json          registers the hook wiring
  Dockerfile, docker-compose.yml sidecar deployment (stdlib-only image, TCP mode)
```


## 3. Memory model

### Stores (`KINDS` in `cortex.py`)

Each kind has its own decay exponent (`DECAY` dict):

| Kind | Decay `d` | Meaning |
| :---- | :---- | :---- |
| episodic | 0.50 | What happened, bound to a session/time. Fastest fade. |
| semantic | 0.20 | Facts, decisions, and the reasons behind them. |
| procedural | 0.10 | How to do things; strengthened by repetition. |
| identity | 0.0 | Who the user is. Exempt from decay. |

### Schema

- `memories`: id, kind, content, gist, keywords, salience, strength, access_count, created_at, last_access, status (`active` / `archived` / `suppressed`), session_id, cwd, vector (BLOB, 512-dim float32), meta (JSON).
- `mem_fts`: FTS5 virtual table (porter + unicode61 tokenizer) over content/gist/keywords, kept in sync by `AFTER INSERT/UPDATE/DELETE` triggers.
- `edges`: directed association graph `(src, dst, kind, weight, created_at)`. On a repeated link, upsert with `weight = MIN(1.0, weight + 0.15)` (Hebbian strengthening). Edge kinds include `abstracts` and `recombined`.
- `journal`: raw hippocampal buffer. Every observed turn is stored, with a `consolidated` flag that consolidation clears.
- `sessions`, `engram_meta`: session records and key/value state (for example `last_downscale`, `last_sleep`).

### Activation (`Cortex.activation()`)

ACT-R optimized base-level learning, using Petrov's approximation:

```
B = ln(n / (1 - d)) - d * ln(age)
```

`n` is the access count and `age` is time since creation. The result is biased additively by salience (`+1.6 * (salience - 0.5)`) and a small recency term. This one function makes "reinforced last week" outrank "a slightly better textual match untouched for months."

## 4. Embeddings (`vectorize.py`)

There is no real embedding model. Instead, a 512-dim sparse-projected vector is built from:

- stemmed-word unigrams (weight 1.0),
- word bigrams (weight 0.5),
- 4-char-grams of the stemmed, whitespace-squashed text (weight 0.12, deliberately low: a typo/morphology backstop, not the primary signal).

Details that matter:

- Hash with `blake2b` and a fixed salt (`b"engram-v1"`). Do not use Python's `hash()`: `PYTHONHASHSEED` randomization would make vectors written by one process unreadable by another.
- L2-normalize at the end.
- `stem()` is a hand-rolled suffix stripper (plural first, then longest suffix first), built to agree with FTS5's porter stemmer.
- Also provides `cosine` and `jaccard`.

Related text scores about 0.3–0.45 cosine versus about 0 for unrelated text. That is enough for ranking, but it cannot see synonymy or negation. `vectorize.embed()` is the only function that must change to swap in a real model. Define it behind an `Embedder` protocol with the hashed n-gram embedder as the default.

## 5. Encoding (`encode.py`)

### Scoring

1. `amygdala(text)`: regex-driven affective salience in [0, 1]. Weighted markers:

   | Marker | Weight |
   | :---- | :---- |
   | never / always / must | 0.40 |
   | broke / outage / data-loss | 0.35 |
   | critical / urgent / security | 0.30 |
   | explicit correction ("no, that's wrong") | 0.30 |
   | decision language | 0.25 |
   | explicit "remember this" | 0.25 |
   | errors / bugs | 0.20 |
   | deadlines | 0.20 |
   | causal language | 0.20 |
   | deprecation / migration | 0.15 |
   | shouting / emphasis | 0.15 |

   Fragments under 4 words are penalized. Long ones get a small bonus.

2. `durability(text)`: the union of `amygdala()` with content-type signals that carry no affect but are durable anyway:
   - identity or ownership claims ("I'm the tech lead for X"),
   - decision language,
   - quantitative observations (a regex over units such as `ms/s/min/%/$/rps`, ordered longest-suffix-first so `seconds` does not match bare `s`),
   - causal language,
   - named rules, conventions, and policies.

3. `novelty()`: dopaminergic prediction error. It runs `recall.candidates()` against the existing corpus and returns `1 - best_similarity` directly. Do not use the rank-normalized retrieval score, which would make the top hit on a small corpus always look like a perfect duplicate.

4. `classify(text)` routes to a kind by regex:
   - identity cues ("I'm...", "my role...") -> IDENTITY,
   - imperative or recipe language -> PROCEDURAL,
   - decision, state, or causal language -> SEMANTIC,
   - everything else -> EPISODIC.

### Similarity (the most important correctness rule)

`similarity(a, b) = max(cosine, jaccard)`. It is **never** a blend or average.

Add an **identifier veto**: if the symmetric-difference tokens between the two texts contain a digit, a path separator, or a camelCase boundary, damp the result (`sim *= 0.55`). `NEAR_DUPLICATE = 0.88`, deliberately conservative.

Why this is load-bearing: blending the signals, for example as `(cos + jac) / 1.4`, turns inputs of 0.70 and 0.60 into 0.93, a confidence neither signal supports. At a threshold near 0.78 this would silently merge distinct facts while the API reports "reinforced existing memory". Lexical overlap is blind to exactly what distinguishes near-identical technical statements: swapped entities ("staging" vs "prod"), negation ("require" vs "require no"), and swapped identifiers (`svc0000` vs `svc0101`).

Rules to preserve:

1. Use `max(cosine, jaccard)`, never a blend.
2. Apply the identifier veto (damp, do not merge) when differing tokens contain digits, path separators, or camelCase.
3. Put no length floor on tokens extracted for the veto. `v1`/`v2`, `s3`, and `p0` are often the only distinguishing token.
4. Bias hard toward false negatives. A redundant trace self-corrects under reinforcement. A false merge is silent and permanent.

### The `encode()` gate

1. Trivial chatter (ok, thanks, lol, etc., matched by a `_TRIVIAL` regex) is rejected unless `force` or `deliberate`.
2. If a near-duplicate exists (`similarity >= NEAR_DUPLICATE`), reinforce the existing trace (+0.5 strength) and write nothing new.
3. Otherwise compute effective salience:
   ```
   effective = affect * (0.45 + 0.55 * surprise)
   ```
   Floors apply: explicit salience acts as a floor (`max(effective, affect * 0.9)`), IDENTITY kind floors at 0.62, and deliberate calls floor at 0.55.
4. Reject writes below `ENCODE_THRESHOLD = 0.34` unless forced or deliberate.

### `observe()` (involuntary pathway)

Always journal the raw content. Additionally, promote straight to long-term storage if `durability(content) >= 0.45`.

## 6. Retrieval (`recall.py`)

Three fused signals:

- **Lexical.** FTS5 `bm25()` over content/gist/keywords. The query is a forgiving `OR` built by stemming and stopword-filtering the cue (`_fts_query`). bm25 is rank-normalized (`1 - i/n`) rather than trusted as an absolute score, because its magnitude depends on corpus size.
- **Semantic.** Cosine over the 512-dim vectors, computed against the most recently touched rows (`max(pool * 4, 600)`). This recency cap applies only to the vector channel. FTS5 still covers the whole corpus.
- **Structural.** `spread()`: 2-hop spreading activation across `edges` from the top direct hits, so "the auth bug" can surface a decision made *because of* that bug even if the wording is unrelated.

Scoring:

```
relevance = 0.55 * lex + 0.45 * min(1, sem * 1.8)
score     = relevance * (0.55 + 0.45 * sigmoid(activation))
```

Activation modulates relevance and never overrides it.

Other behavior:

- **Identity special case.** Questions like "who am I talking to?" are pure stopwords with almost no lexical or vector signal. When the cue matches `_IDENTITY_CUE`, pull the top identity rows directly.
- **Reinforcement.** Every returned result is reinforced (testing effect): +0.6 strength for direct hits, +0.35 for associative spreads.
- **`briefing()`**, used at `SessionStart`, returns identity memories first, then the highest-activation semantic and procedural memories, then a few recent episodic memories scoped to the session's `cwd`.

## 7. Consolidation (`consolidate.py`)

`sleep_cycle()` runs four stages.

1. **`replay()`.** Group pending journal rows by session. Split into episodes wherever there is a gap over 15 minutes (`EPISODE_GAP`).
2. **`extract_gist()` / `consolidate_episodes()`.** Extractive distillation with no model in the loop. Rank sentences by `durability()`, drop near-identical candidates (jaccard > 0.6), and keep the top 6. Write one episodic summary memory plus zero or more semantic facts, each linked back to its episode with an `abstracts` edge. Skip episodes shorter than 4 turns that are also low-salience.
3. **`downscale()`.** Synaptic homeostasis.
   - Multiply all non-identity strength by `0.93 ^ periods`, where periods scale with wall-clock time elapsed (capped at 14 days' worth per call).
   - Archive anything whose activation falls below `ARCHIVE_ACTIVATION = -3.2`.
   - Hard-delete archived episodic memories with zero access count after `PRUNE_AFTER_DAYS = 45`.
   - Rate-limit to once per 6 hours via `engram_meta.last_downscale`. Clamp a future or corrupted timestamp to 0 elapsed time. Without this gate, a daemon restart loop or clock skew can compress months of forgetting into seconds.
4. **`recombine()`.** REM-like. Sample the top 60 semantic/procedural/identity memories by salience, compute pairwise cosine, and add a `recombined` edge for pairs in the band 0.34 <= sim <= 0.88 (clearly related, not near-duplicate). Cap at 40 new links per cycle.

Triggers:

- **Timer.** The daemon's `_sleep_loop` runs every `ENGRAM_SLEEP_INTERVAL = 900` s, and only after `ENGRAM_IDLE_SLEEP = 180` s of inactivity.
- **Hooks.** `PreCompact` runs a sleep with `deep = False` (skip downscale and recombine). `SessionEnd` runs `consolidate_episodes` only.

## 8. Process architecture

### `daemon.py`

The only writer. Newline-delimited JSON over a Unix socket. It falls back automatically to loopback TCP if `AF_UNIX` binding is denied, and always uses TCP in sidecar/container mode.

Verbs: `remember`, `recall`, `observe`, `briefing`, `reflect`, `forget`, `sleep`, `session_end`, `status`, `dump`, `ping`.

An `Engram` class dispatches verbs to the encode/recall/consolidate modules behind one lock. A background `_sleep_loop` thread runs `sleep()` on the timer when idle.

### `client.py`

Every consumer (hooks, MCP server, `mnemo`) goes through it. The hard rule: **a memory service that is down, slow, or broken must never break the session using it.**

- Every call catches `OSError` and timeouts and returns `{"ok": False, "offline": True}` instead of raising.
- Callers treat `{"ok": False}` as a normal, silent outcome.
- `autostart()` lets a session launch the daemon on demand. It is a no-op in TCP/sidecar mode, where the container owns lifecycle.

### `mcp_server.py`

A hand-rolled stdio JSON-RPC MCP server with no SDK dependency. It exposes `recall`, `remember`, `reflect`, `forget`, and `engram_status`, and forwards each to the daemon through `client.py`.

### `hooks/engram_hook.py`

The involuntary pathway, wired through `.claude/settings.json` to `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`, and `PreCompact`.

- `SessionStart` fetches the briefing.
- Turn events feed `observe()`.
- `PreCompact` and `SessionEnd` trigger the consolidation described in section 7.

### `scripts/mnemo`

A CLI over `client.py` with subcommands `status`, `up`, `sleep`, `recall`, `remember`, `forget`, `dump`.

### Deployment

Run the daemon directly, or as a Docker sidecar (stdlib-only image, TCP mode). `.mcp.json` registers the `engram` MCP server for Claude Code.

## 9. Build order

Build in dependency order:

1. `vectorize.py`: embedding, stemmer, cosine/jaccard. No internal dependencies.
2. `cortex.py`: schema, the `Cortex` class (insert/get/reinforce/link/journal/session management), and `activation()`. Depends only on `vectorize`.
3. `encode.py`: `amygdala`, `durability`, `novelty`, `similarity`, `classify`, `encode()`, `observe()`. Depends on `cortex` and `vectorize`. It has a deferred import of `recall.candidates`, done inside the function body and not at module level, because `recall` also depends on `cortex`.
4. `recall.py`: `candidates()`, `spread()`, `recall()`, `briefing()`. Depends on `cortex` and `vectorize`.
5. `consolidate.py`: `sleep_cycle()` and its stages. Depends on `cortex`, `vectorize`, and `encode` (reuses `classify`, `durability`, `encode`).
6. `daemon.py`: socket server wrapping an `Engram` class.
7. `client.py`: fail-soft client plus `autostart()`.
8. `mcp_server.py`, `hooks/engram_hook.py`, `scripts/mnemo`: thin layers over `client.py` with no logic of their own. Build last.
9. Tests throughout.

Write the adversarial dedup tests **before** trusting any similarity-combination logic.

## 10. Test checklist

- **Dedup adversarial pairs:** swapped entities, negation, swapped identifiers, and true restatement. Only the true restatement may merge.
- **Concurrency:** many distinct facts written in parallel must all survive as distinct memories.
- **Fail-soft client:** with the daemon down or slow, hooks, MCP server, and `mnemo` return silently and never raise.
- **Downscale gate:** a second call inside 6 hours is a no-op. A future or corrupted `last_downscale` clamps to 0 elapsed time.
- **End-to-end:** remember, recall, observe, sleep, and the briefing path.
- **Hooks:** each wired event behaves correctly and tolerates an offline memory service.

## 11. Review priorities

1. Check that `similarity()` and `novelty()` still use `max()` and the identifier veto, and that the adversarial tests pass.
2. Check that every client call site honors the fail-soft contract.
3. Check that `downscale()`'s 6-hour rate limit and timestamp clamping are present.
4. Run the test suites yourself rather than trusting reported pass counts.
5. Build and run the Docker files.

## 12. Extensions

- Make the embedding function pluggable through an `Embedder` protocol. Add a remote-API embedder as a seam, keeping hashed n-grams as the default. Dedup safety must hold under either embedder.
- Add a single-command installer to reduce setup friction.
