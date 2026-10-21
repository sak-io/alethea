Status: IN REVIEW (T21 end-to-end, recall regression, suite cost)
Round: 1
Last review: none
Updated: 2026-10-21

## Core complete (through T21)
Built: cortex (SQLite+FTS5), hashed n-gram vectors, encode gate and dedup, recall with spreading activation, relevance floor, same-repo boost and `scope`, activation/decay, consolidation and sleep cycle, redaction, daemon (Unix + token-authed TCP), fail-soft client, MCP server (stdio), `mnemo` CLI, briefing.
Tests: default suite 417 tests (4 skipped: opt-in `ENGRAM_SLOW_TESTS`, `ENGRAM_PERF`), ~40 s. New in T21: `scripts/test_e2e.py` (12 tests, real daemon process, three front doors, restart, kill -9, daemon-down), `tests/test_recall_regression.py` (16), `tests/test_mcp_hygiene.py` (4), `tests/test_perf.py` (opt-in; p95 32 ms at 10k).
Recall corpus (80 memories incl. 20 distractors, 43 cues): recall@3 0.953, top-1 0.884; untuned held-out (30 cues) 0.833; 0 of 19 off-topic/nonsense cues return anything; gates 0.93 / 0.85 / 0.80.
Known gaps: no real semantic embedder (sem-only matches are weak, so paraphrases without shared words miss: "which database does the billing service use", "how to roll back a bad release"); FTS rank fusion ranks by position, not score; mnemo has no briefing command and MCP has no briefing tool; redaction gaps listed in lessons.md; export-rules/reembed not yet available; hooks, wiring and Phase 3 tasks (T22+) not started.
