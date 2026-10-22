# ADR 0008: Dedup full-text window capped at 2000 rows

Status: Accepted (owner decision, kept at 2000 for now)

## Context
The PR #1 review measured `remember()` cost growing with corpus size (about 6.5 ms per write over the first 1k memories, about 95 ms per write between 10k and 20k before the fix). One cause was that the dedup path ranks the whole FTS table (`bm25` is computed for every matching row), which is O(corpus).

## Decision
The dedup path (`encode._best_duplicate`, `recall.candidates(..., relevance_floor=False, fts_window=N)`) ranks only the **N newest rows by rowid** in the FTS channel, with `N = DEDUP_FTS_WINDOW = 2000`. Recall (the user-facing `recall` verb and `mnemo recall`) is **not** capped and still searches the whole corpus. The 600-row vector window is unchanged and is also used by dedup.

Measured ms/write after the change: 1k-3k about 22.6, 3k-10k about 27.8, 10k-20k about 30.6 (before: 33.0, 59.4, 95.0).

## Consequence (the trade-off)
Exact while the store has 2000 rows or fewer. Beyond that, a near-duplicate that is neither among the 2000 newest rows nor in the 600-row vector window is not seen, so the new text is stored as a **second copy** instead of reinforcing the old memory. This is a false negative, the safe direction (principle 2, "prefer redundancy to false merges"): it never merges two distinct facts. It can cost: duplicated memories, split reinforcement (two traces each weaker than one), and slightly noisier briefings.

## How to recognise a problem caused by this cap
Symptoms: the same decision or fact shows up twice in `mnemo dump` / briefing, one old and one recent; an old well-established memory is not being reinforced by restatements.

Trace it:
1. `mnemo status` prints `dedup window: 2000 newest rows   cap ACTIVE|inactive   stored while capped: N`. If the cap is **inactive** (store at or under the window), this ADR is not the cause.
2. Every memory created while the cap was active carries `meta.dedup_window_capped = <window in force>` (visible in `mnemo dump --json`; also in the `remember` / `observe` result as `dedup_window_capped`). Only those can be cap-induced second copies.
3. Test the hypothesis: start the daemon with `ENGRAM_DEDUP_FTS_WINDOW=1000000` (see `docs/configuration.md`). If the duplicate then reinforces instead of being stored twice, the cap was the cause.

## If we change it
Raise `DEDUP_FTS_WINDOW` (or set the env var) and re-measure with `scripts/bench_write.py` (per-write cost by corpus size). Alternatives considered: an in-memory vector index or an inverted-index prefilter that keeps exactness at any size (more code, more state to keep consistent with the DB); a sleep-time "merge old duplicates" pass (needs the same veto rules and a safe merge story). Neither is built.

## Tests
`tests/test_dedup_window.py` pins the exact behaviour: exact at or under the window, the documented second copy beyond it, the `dedup_window_capped` flag, the env override, and the status block.
