# ADR 0005: Sleep cycle must not hold the global lock for its whole run

Status: Proposed (architect) | Concerns T§1, T§7, T§8 | Risk: hook latency budget (2 s)

**Context.** One RLock serializes all mutations. A full `sleep_cycle()` (replay, gist, downscale over all rows, recombine over 60x60 pairs) can run long; a `SessionStart` briefing arriving then would block past the 2 s hook timeout and silently look "offline".

**Decision.** Keep one writer and one RLock, but `sleep_cycle()` acquires it per stage and per batch (e.g. 200 rows or one episode), releasing between batches; read verbs (`recall`, `briefing`, `status`) never wait more than one batch. Each stage is idempotent and resumable (journal `consolidated` flag, `last_downscale` gate). Timer sleeps still start only after 180 s idle.

**Alternatives rejected.** Separate read connection with no lock (SQLite WAL allows it, but the owner mandates the single lock; revisit only if measured p95 fails); running sleep in a subprocess (breaks single-writer).
