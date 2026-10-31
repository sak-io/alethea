# Alethea – Technical Details

A specification of Alethea, written so the project can be built from this document. For the product rationale, see [product-concept.md](../product/product-concept.md).

Every `ALETHEA_*` and `MNEMO_*` environment variable is listed in one table in [configuration.md](../reference/configuration.md); where this document names one, that table is authoritative for its default and implementation status.

The numbers below (thresholds, weights, decay rates) are the reference starting values. Verify and tune them with tests.

---

## 1. Constraints and stack

- Pure Python 3 standard library by default. No pip installs, no network calls, no embedding API. Optional plugins (a learned `Embedder`, LLM-assisted consolidation) may add dependencies or network use, but only when explicitly enabled, and the core must work without them.
- Storage: one SQLite file in WAL mode, with FTS5 for full-text search.
- Single writer: one daemon process serializes all mutations behind a single `threading.RLock`.
- Test runner: stdlib `unittest` (`python3 -m unittest discover`). `pytest` is not assumed.

## 2. Layout

```
alethea/
  cortex.py        SQLite schema, decay/activation math, low-level storage API
  encode.py        salience scoring, novelty/dedup, classification, the encode() gate
  recall.py        retrieval: lexical+semantic fusion, spreading activation, briefing
  consolidate.py   sleep_cycle(): replay -> gist -> downscale -> recombine
  vectorize.py     Embedder protocol, hashed n-gram default, stemmer, cosine/jaccard
  gitctx.py        repo_root / repo_common_dir resolution (git rev-parse, fail-soft)
  export.py        sync-back: render high-confidence memories to host-native markdown rules
  daemon.py        always-on process: socket server, verb dispatch, sleep timer
  client.py        thin fail-soft client used by hooks, MCP server, and mnemo
  mcp_server.py    stdio JSON-RPC MCP server: recall/remember/reflect/forget/alethea_status
  hooks/alethea_hook.py            involuntary pathway: SessionStart/UserPromptSubmit/Stop/SessionEnd/PreCompact
  scripts/
    mnemo                     CLI: status/up/sleep/recall/remember/forget/dump/export-rules
    test_e2e.py, test_hooks.py, test_concurrency.py, show_mcp.py
  integrations/
    claude-code/CLAUDE.snippet.md  directive telling the agent when to recall/remember
    cursor/mcp.json                MCP registration template
    cursor/alethea.mdc              always-apply rule telling Cursor when to recall/remember
  .mcp.json                      registers the `alethea` MCP server for Claude Code
  (hook wiring)                  provided by the installed package: `mnemo install claude-code` (ADR 0009)
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

- `memories`: id, kind, content, gist, keywords, salience, strength, access_count, created_at, last_access, status (`active` / `archived` / `suppressed`), session_id, cwd, repo_root, vector (BLOB, float32), embedder_id, meta (JSON).
- `repo_root` is nullable (non-git directories) and indexed. See "Repository context" below.
- `embedder_id` (for example `hashed-ngram-v2:512`) records which embedder produced each vector, so vectors from different embedders are never compared.
- `mem_fts`: FTS5 virtual table (porter + unicode61 tokenizer) over content/gist/keywords, kept in sync by `AFTER INSERT/UPDATE/DELETE` triggers.
- `edges`: directed association graph `(src, dst, kind, weight, created_at)`. On a repeated link, upsert with `weight = MIN(1.0, weight + 0.15)` (Hebbian strengthening). Edge kinds include `abstracts` and `recombined`.
- `journal`: raw hippocampal buffer. Every observed turn is stored, with a `consolidated` flag that consolidation clears.
- `sessions`, `alethea_meta`: session records and key/value state (for example `last_downscale`, `last_sleep`).

### Activation (`Cortex.activation()`)

ACT-R optimized base-level learning, using Petrov's approximation:

```
B = ln(n / (1 - d)) - d * ln(age)
```

`n` is the access count and `age` is time since creation. The result is biased additively by salience (`+1.6 * (salience - 0.5)`) and a small recency term. This one function makes "reinforced last week" outrank "a slightly better textual match untouched for months."

### Repository context (`gitctx.py`)

`cwd` alone is fragile: repos move, are reached through symlinks, and have worktrees and subdirectories. Each memory is also tagged with `repo_root`.

- Resolve with `git -C <cwd> rev-parse --show-toplevel` on `os.path.realpath(cwd)`, so symlinked paths collapse to one root.
- For worktrees, `--show-toplevel` differs per worktree. Also resolve `git rev-parse --git-common-dir` (the shared `.git`) and use its parent as the canonical repo identity, so every worktree of one repo shares memories. Store the canonical value in `repo_root`; keep the literal path in `cwd`.
- Resolve in the hook process (which sees the real filesystem), not the daemon, which may run in a container. Pass `repo_root` as a verb argument. Cache per `cwd` for the process lifetime.
- Fail-soft: a missing `git` binary, a non-repo directory, a timeout (short, for example 1 s), or any error yields `repo_root = NULL`. This never blocks the session.
- Branch is deliberately not part of the key: decisions usually span branches. It may be stored in `meta` for display.

## 4. Embeddings (`vectorize.py`)

The default embedder is not a learned model. Instead, a 512-dim sparse-projected vector is built from:

- stemmed-word unigrams (weight 1.0),
- word bigrams (weight 0.5),
- 4-char-grams of the stemmed, whitespace-squashed text (weight 0.12, deliberately low: a typo/morphology backstop, not the primary signal).

Details that matter:

- Hash with `blake2b` and a fixed salt (`b"alethea-v1"`). Do not use Python's `hash()`: `PYTHONHASHSEED` randomization would make vectors written by one process unreadable by another.
- L2-normalize at the end.
- `stem()` is a hand-rolled suffix stripper (plural first, then longest suffix first), built to agree with FTS5's porter stemmer.
- Also provides `cosine` and `jaccard`.

Related text scores about 0.3–0.45 cosine versus about 0 for unrelated text. That is enough for ranking, but it cannot see synonymy ("database wiped" vs "postgres reset") or negation ("disable auth" vs "enable auth").

### `Embedder` protocol (first-class plugin)

```python
class Embedder(Protocol):
    id: str            # stable, versioned, e.g. "hashed-ngram-v2:512"
    dim: int
    def embed(self, text: str) -> bytes | Sequence[float]: ...
```

- `HashedNgramEmbedder` is the default and the reference implementation.
- Optional embedders (local ONNX/fastembed such as `all-MiniLM-L6-v2`, or a remote API) are selected by config (`ALETHEA_EMBEDDER`, env or config file) and loaded lazily, so the stdlib-only path never imports them.
- Fail-soft: if the selected embedder is unavailable or raises (missing package, model not downloaded, network error), fall back to the hashed embedder for that call and log once. Lexical search keeps working regardless.
- All vector consumers (`recall.candidates`, `novelty`, `similarity`, `recombine`) go through the protocol. Only compare vectors with matching `embedder_id`.
- Changing embedder: new writes use the new one. Provide `mnemo reembed` to rewrite stored vectors in batches (resumable); until it completes, rows with a different `embedder_id` are served by the lexical channel only.
- Thresholds are per-embedder. `NEAR_DUPLICATE`, the semantic scale factor (`sem * 1.8`), and the recombine band are calibrated for hashed n-grams. Dense models have a different cosine distribution (unrelated text is often 0.2–0.4, near-paraphrases 0.8+), so each embedder declares its own thresholds, defaulting to the conservative hashed values until tuned.
- **Dedup safety must hold under every embedder.** Dense models also embed "enable X" and "disable X" very close together. The `max(cosine, jaccard)` rule and the identifier veto stay in force. Add a negation/antonym veto (differing tokens that include a negator or an opposing verb pair such as enable/disable, allow/deny, add/remove) that damps similarity just like the identifier veto. The adversarial dedup suite must run against each shipped embedder.

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
- **`briefing()`**, used at `SessionStart`, returns identity memories first, then the highest-activation semantic and procedural memories, then a few recent episodic memories scoped to the session's repository. Scope by `repo_root` when set, otherwise fall back to exact `cwd`. Repo-scoped memories get a small activation boost over unscoped ones, but nothing is excluded outright, so cross-repo facts still surface.

## 7. Consolidation (`consolidate.py`)

`sleep_cycle()` runs four stages.

1. **`replay()`.** Group pending journal rows by session. Split into episodes wherever there is a gap over 15 minutes (`EPISODE_GAP`).
2. **`extract_gist()` / `consolidate_episodes()`.** Extractive distillation with no model in the loop (the default). Rank sentences by `durability()`, drop near-identical candidates (jaccard > 0.6), and keep the top 6. Write one episodic summary memory plus zero or more semantic facts, each linked back to its episode with an `abstracts` edge. Skip episodes shorter than 4 turns that are also low-salience.

   Pre-filter before ranking so the extractive pass is less fragile on coding transcripts: strip or collapse fenced code blocks, diffs, stack traces, and long terminal output (keep at most the final error line and the command that produced it), and split sentences on newlines and bullet markers as well as punctuation.

   **Optional LLM-assisted pass** (off by default, `ALETHEA_LLM_CONSOLIDATE`). A `Distiller` protocol with the extractive implementation as default and fallback. An LLM distiller takes the episode and returns structured bullets (decision, cause, procedure, open issue), each typed so it can be classified into a kind. Two delivery modes:
   - *Host-agent callback:* the daemon never calls a model itself. It queues episodes as pending-consolidation items. The next session's MCP server exposes them (an `alethea_consolidate_pending` tool, or MCP sampling where the host supports it), and the host agent returns the synthesized bullets via `remember`. This keeps the daemon offline and key-free.
   - *Direct API:* a configured provider endpoint, run only during idle sleep.

   Rules: the pass is best-effort and rate-limited. Any failure, timeout, or malformed output leaves the episode on the extractive path and is never an error to the session. LLM output is untrusted text: it goes through the normal `encode()` gate (novelty, dedup with the vetoes, thresholds), is capped in length, and is never executed or used to modify configuration. Episodes are marked `consolidated` only after a distiller succeeds, so they can be retried.
3. **`downscale()`.** Synaptic homeostasis.
   - Multiply all non-identity strength by `0.93 ^ periods`, where periods scale with wall-clock time elapsed (capped at 14 days' worth per call).
   - Archive anything whose activation falls below `ARCHIVE_ACTIVATION = -3.2`.
   - Hard-delete archived episodic memories with zero access count after `PRUNE_AFTER_DAYS = 45`.
   - Rate-limit to once per 6 hours via `alethea_meta.last_downscale`. Clamp a future or corrupted timestamp to 0 elapsed time. Without this gate, a daemon restart loop or clock skew can compress months of forgetting into seconds.
4. **`recombine()`.** REM-like. Sample the top 60 semantic/procedural/identity memories by salience, compute pairwise cosine, and add a `recombined` edge for pairs in the band 0.34 <= sim <= 0.88 (clearly related, not near-duplicate). Cap at 40 new links per cycle.

Triggers:

- **Timer.** The daemon's `_sleep_loop` runs every `ALETHEA_SLEEP_INTERVAL = 900` s, and only after `ALETHEA_IDLE_SLEEP = 180` s of inactivity.
- **Hooks.** `PreCompact` runs a sleep with `deep = False` (skip downscale and recombine). `SessionEnd` runs `consolidate_episodes` only.

## 8. Process architecture

### `daemon.py`

The only writer. Newline-delimited JSON over a Unix socket. It falls back automatically to loopback TCP if `AF_UNIX` binding is denied, and always uses TCP in sidecar/container mode.

Verbs: `remember`, `recall`, `observe`, `briefing`, `reflect`, `forget`, `sleep`, `session_end`, `status`, `dump`, `ping`.

An `Alethea` class dispatches verbs to the encode/recall/consolidate modules behind one lock. A background `_sleep_loop` thread runs `sleep()` on the timer when idle.

### `client.py`

Every consumer (hooks, MCP server, `mnemo`) goes through it. The hard rule: **a memory service that is down, slow, or broken must never break the session using it.**

- Every call catches `OSError` and timeouts and returns `{"ok": False, "offline": True}` instead of raising.
- Callers treat `{"ok": False}` as a normal, silent outcome.
- `autostart()` lets a session launch the daemon on demand. It is a no-op in TCP/sidecar mode, where the container owns lifecycle.

### `mcp_server.py`

A hand-rolled stdio JSON-RPC MCP server with no SDK dependency. It exposes `recall`, `remember`, `reflect`, `forget`, and `alethea_status`, and forwards each to the daemon through `client.py`.

### `hooks/alethea_hook.py`

The involuntary pathway, wired by the installed package (`mnemo install claude-code`, ADR 0009; this repo does not enable hooks itself) to `SessionStart`, `UserPromptSubmit`, `Stop`, `SessionEnd`, and `PreCompact`.

- `SessionStart` fetches the briefing.
- Turn events feed `observe()`.
- `PreCompact` and `SessionEnd` trigger the consolidation described in section 7.

### `scripts/mnemo`

A CLI over `client.py` with subcommands `status`, `up`, `sleep`, `recall`, `remember`, `forget`, `dump`, `export-rules`.

### Deployment

Run the daemon directly, or as a Docker sidecar (stdlib-only image, TCP mode). `.mcp.json` registers the `alethea` MCP server for Claude Code.

### Host integrations (Claude Code, Cursor)

Both hosts have native, hand-curated instruction files (`CLAUDE.md`; `.cursor/rules/*.mdc`, with `.cursorrules` as the legacy form). Alethea does not replace or write into them. The division of labor:

| Layer | Owner | Contents | Changes |
| :---- | :---- | :---- | :---- |
| Static ground truth | The user, in native files | Stack, style, build commands, guardrails | By hand, rarely |
| Dynamic memory | Alethea | Decisions, bug causes, procedures, preferences, identity | Reinforced and decayed automatically |

**Capability matrix.** The two hosts do not offer the same pathways, so the integration degrades by capability rather than assuming parity:

| Pathway | Claude Code | Cursor |
| :---- | :---- | :---- |
| Voluntary (MCP: recall/remember/reflect/forget) | `.mcp.json` | `.cursor/mcp.json` or `~/.cursor/mcp.json` |
| Involuntary capture (observe every turn) | Lifecycle hooks | Cursor's hooks, if the installed version provides them; otherwise none (voluntary only) |
| Session briefing | `SessionStart` hook | No `SessionStart` equivalent: delivered by the sync-back file, which an always-apply rule loads |
| "When to consult Alethea" directive | `CLAUDE.md` snippet | `alethea.mdc` with `alwaysApply: true` |

Verify each host's current hook events and output schema against its docs before wiring; they change between versions.

**Claude Code.**
- `SessionStart` returns the briefing as added context. The documented shape is `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "<briefing>"}}` (or plain stdout). Confirm against the installed version; the hook must still exit 0 with empty output when the daemon is offline.
- The `CLAUDE.md` snippet is one sentence: call `recall` for unfamiliar domain logic, past architecture decisions, or earlier bug investigations; call `remember` when a decision is finalized or a non-obvious cause is found.

**Cursor.**
- `mcp.json` launches the stdlib MCP server by absolute path (`python3 /path/to/alethea/mcp_server.py`), not `-m alethea.mcp_server`, which needs the package on `PYTHONPATH`.
- `alethea.mdc` carries `description`, `alwaysApply: true`, and the same recall/remember directive. Use `.mdc` rules, not the legacy `.cursorrules`.
- Without a `SessionStart` hook, the agent has no automatic briefing, so the sync-back file below is Cursor's briefing.

**`repo_root` in the MCP server.** The "resolve in the hook process" rule in section 3 does not cover MCP calls, which arrive without hook context. `mcp_server.py` resolves `repo_root` itself from its own working directory at startup (and per call if the host passes a root), using `gitctx.py` and the same fail-soft rules. Both hosts, working in the same repo, therefore read and write the same memories.

#### Sync-back: `mnemo export-rules`

Renders a snapshot of Alethea's strongest durable memories into a markdown file the host loads natively. One-way and regenerated, never merged.

- **Selection.** Active semantic and procedural memories for the current `repo_root`, ranked by activation, above a confidence floor (for example `access_count >= 2` or deliberate `remember`), capped at a small count and byte budget (for example 20 items, 4 KB) so it cannot bloat every prompt. Identity memories are excluded by default (see privacy).
- **Targets.** `.cursor/rules/alethea_learned.mdc` (with `alwaysApply: true` frontmatter) and `.claude/alethea_learned.md`, which `CLAUDE.md` pulls in with an `@.claude/alethea_learned.md` import.
- **Whole-file ownership.** The file is entirely Alethea's: a header says "auto-generated, do not edit", with the generation time. Each export overwrites it atomically (write temp, rename). Never edit inside a hand-written file.
- **Staleness.** The export is a snapshot and does not decay on its own. Regenerate it on `SessionEnd` and after `sleep`, so a memory that has faded or been forgotten drops out on the next export. `forget` of an exported item should trigger a re-export.
- **Privacy.** The default target is gitignored (`mnemo export-rules` adds the entry or warns), because memories can name people, incidents, and personal preferences. Committing the file to share with a team is an explicit opt-in, and identity memories are never written to a repo-level file.
- **Untrusted text.** Exported content flows into an always-on prompt. Only memories that passed the `encode()` gate are eligible, each is length-capped, and exported text is rendered as plain list items, never as headings or fenced instructions.
- **Fail-soft.** If the daemon is down, `export-rules` leaves the existing file untouched and exits quietly when invoked from a hook.

## 9. Build order

Build in dependency order:

1. `vectorize.py`: embedding, stemmer, cosine/jaccard. No internal dependencies.
2. `cortex.py`: schema, the `Cortex` class (insert/get/reinforce/link/journal/session management), and `activation()`. Depends only on `vectorize`.
3. `encode.py`: `amygdala`, `durability`, `novelty`, `similarity`, `classify`, `encode()`, `observe()`. Depends on `cortex` and `vectorize`. It has a deferred import of `recall.candidates`, done inside the function body and not at module level, because `recall` also depends on `cortex`.
4. `recall.py`: `candidates()`, `spread()`, `recall()`, `briefing()`. Depends on `cortex` and `vectorize`.
5. `consolidate.py`: `sleep_cycle()` and its stages. Depends on `cortex`, `vectorize`, and `encode` (reuses `classify`, `durability`, `encode`).
6. `daemon.py`: socket server wrapping an `Alethea` class.
7. `client.py`: fail-soft client plus `autostart()`.
8. `mcp_server.py`, `hooks/alethea_hook.py`, `scripts/mnemo`: thin layers over `client.py` with no logic of their own. Build last.
9. Tests throughout.

Write the adversarial dedup tests **before** trusting any similarity-combination logic.

## 10. Test checklist

- **Dedup adversarial pairs:** swapped entities, negation, swapped identifiers, and true restatement. Only the true restatement may merge.
- **Concurrency:** many distinct facts written in parallel must all survive as distinct memories.
- **Fail-soft client:** with the daemon down or slow, hooks, MCP server, and `mnemo` return silently and never raise.
- **Downscale gate:** a second call inside 6 hours is a no-op. A future or corrupted `last_downscale` clamps to 0 elapsed time.
- **End-to-end:** remember, recall, observe, sleep, and the briefing path.
- **Hooks:** each wired event behaves correctly and tolerates an offline memory service.
- **Embedder plugin:** the adversarial dedup suite passes for every shipped embedder; a missing or failing optional embedder falls back to hashed n-grams without raising; mixed `embedder_id` rows are never compared.
- **Antonym/negation pairs:** "enable auth" vs "disable auth" and similar must not merge under any embedder.
- **Distiller:** a failing or malformed LLM response leaves the episode unconsolidated and falls back to extraction; code/trace pre-filtering yields coherent gists on fixture transcripts.
- **Repo context:** the same repo reached via a subdirectory, a symlink, and a second worktree resolves to one `repo_root`; non-git directories and a missing `git` binary yield `NULL` without error; briefing falls back to `cwd` when `repo_root` is `NULL`.
- **Host integrations:** the `SessionStart` hook emits valid host JSON and empty output when offline; the MCP server tags memories with `repo_root` so a Claude Code write is recalled from Cursor in the same repo.
- **Export:** `export-rules` is idempotent and atomic; respects the item and byte caps; omits identity, archived, and forgotten memories; never touches a file it does not own; leaves the old file intact when the daemon is down.

## 11. Review priorities

1. Check that `similarity()` and `novelty()` still use `max()` and the identifier veto, and that the adversarial tests pass.
2. Check that every client call site honors the fail-soft contract.
3. Check that `downscale()`'s 6-hour rate limit and timestamp clamping are present.
4. Run the test suites yourself rather than trusting reported pass counts.
5. Build and run the Docker files.

## 12. Extensions

- Ship the `Embedder` protocol (section 4) with a local-model embedder (ONNX/fastembed, `all-MiniLM-L6-v2`) and a remote-API embedder as optional extras, keeping hashed n-grams as the default. Dedup safety must hold under every embedder.
- Ship the optional LLM `Distiller` (section 7), starting with the host-agent callback mode.
- Add a single-command installer to reduce setup friction. It should also write the host integration files (MCP registration, directive snippet, hook wiring) for whichever of Claude Code and Cursor it detects.
