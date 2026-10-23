# ADR 0010: Embedder plugin loading, fail-soft and per-embedder thresholds

Status: Accepted (T25)

## Context
The default embedder is a hashed n-gram projection; semantic paraphrases miss. `docs/technical-details.md` section 4 makes the `Embedder` a first-class plugin that must never cost the zero-dependency default, must never break the daemon, and must not weaken duplicate safety (a false merge is silent and permanent).

## Decision
1. **Selection and loading.** `ENGRAM_EMBEDDER` is `hash` (default) or `<scheme>:<arg>`. `engram.vectorize` keeps a registry: built-in lazy schemes (`fastembed` maps to `engram.embedders_ext:make_fastembed`, imported only when selected) plus `register_scheme(name, factory)`. `openai` is the reserved hook for T26 (one `_LAZY` entry); until then it is an unknown embedder. The daemon calls `vectorize.configure()` at start; nothing optional is imported, and no model is loaded, until the first embedding.
2. **One embedder per operation.** `vectorize.embed_batch(texts, embedder_id=None)` returns `(id_used, vectors)`; encode, recall and similarity use it, so a write, a query or a comparison never mixes embedders, and the stored `embedder_id` is the id that really produced the vector (hashed when a fallback happened).
3. **Fail-soft.** Any failure (import error, load error, exception, wrong dimension, NaN/inf, zero vector, wrong count) returns hashed vectors for that call, increments a counter and logs one stderr line per failure kind per process (spec, kind, exception class; never memory text). Failed loads are retried at most every 300 s. Vectors are validated and re-serialised as float32 before use.
4. **Per-embedder thresholds.** `Embedder` declares `NEAR_DUPLICATE`, `SEM_GAIN`, `SEM_MIN_COS`, `RECOMBINE_LO`, `RECOMBINE_HI`; consumers read them through `vectorize.tuning(embedder_id, name, default)`, which only answers for the currently loaded optional embedder and otherwise returns the module default. `encode.threshold()` is `max(0.88, registered, declared)`. All shipped embedders inherit the hashed values.
5. **Coexistence.** Vector search filters by `embedder_id`; rows with another id are reachable through FTS only. `recombine` skips pairs with different ids; dedup only compares candidates with the writing embedder's id. Dimensions are arbitrary (mismatched lengths score 0.0).
6. **Safety is textual.** The vetoes (identifier, negation/antonym, substitution, order) work on text. `tests/test_dedup_dense.py` proves it: a fake dense embedder puts adversarial pairs at cosine >= 0.97; a patch forcing cosine 1.0 on every pair still leaves every distinct pair below the threshold; the end-to-end `encode()` pairs run per embedder.

## Consequences
- Thresholds for real models are NOT tuned: their cosine distribution is unmeasured (the model could not be downloaded in the build environment). Retuning needs a measurement on a real model; a declared `NEAR_DUPLICATE` below 0.88 is clamped, so tuning can only make merging stricter.
- A fallback mid-run produces rows under the hashed id; they coexist with model rows and are lexical-only to model queries until `mnemo reembed` (T26). The stored `(embedder_id, vector length / 4)` is what T26 needs.
- The first model call blocks one request (load, possible download) while the engine lock is held. Accepted for a single-user daemon; a background preload is a possible later improvement.
- `similarity()` with an id whose embedder is not loaded (for example recombine over rows from a previous embedder) uses hashed vectors for the text comparison but keeps that id for the threshold; thresholds are never below the hashed one, so this is the safe direction.
- Privacy: `fastembed` runs on-device; only the package's model download touches the network.

## Alternatives considered
Importing `fastembed` at start to fail early (rejected: breaks the "stdlib path never imports extras" rule); retrying a failed load on every call (rejected: repeated downloads and imports); trusting a dense cosine for merging (rejected: models place opposite statements close together, which is why the vetoes stay).
