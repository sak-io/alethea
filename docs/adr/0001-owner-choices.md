# ADR 0001: Owner technical choices (adopted as given)

Status: Accepted (given by owner)
Sources: `[T§n]` = docs/technical-details.md, `[S Gn]` = docs/specs/engram.md gaps. Alternatives are noted only where the architect sees real risk.

| # | Decision | Source | Note / risk |
|---|---|---|---|
| 1 | Pure stdlib Python 3, unittest, offline by default; plugins opt-in and lazy | T§1 | Min Python 3.9 (S G17) |
| 2 | One SQLite file, WAL, FTS5 (porter+unicode61) | T§1,§3 | FTS5 missing -> degrade to vector-only (G17) |
| 3 | Single-writer daemon, one `threading.RLock`, NDJSON over Unix socket, loopback TCP fallback | T§1,§8 | Lock held by long sleep stages: see ADR 0005 |
| 4 | Four kinds with decay 0.5/0.2/0.1/0.0 | T§3 | Calibration: ADR 0003 |
| 5 | ACT-R base-level activation + salience + recency | T§3 | Needs units/`n` definition: ADR 0003 |
| 6 | `repo_root` = parent of git-common-dir, resolved in hook/MCP process, fail-soft, branch not in key | T§3 | none |
| 7 | Hashed n-gram embedder (512-d, blake2b salt `engram-v1`), `Embedder` protocol, `embedder_id` stamping | T§4 | Weak on synonymy; accepted |
| 8 | `similarity = max(cosine, jaccard)`, identifier veto x0.55, negation/antonym veto, `NEAR_DUPLICATE=0.88`, bias to false negatives | T§4,§5 | Highest-risk logic; adversarial tests first |
| 9 | Encode gate: amygdala, durability, novelty (1-best_sim), classify, thresholds as stated | T§5 | Marker lists defined by developer (G23) |
| 10 | Fused recall (lexical bm25 rank-normalized, semantic over recency pool, 2-hop spread), activation modulates relevance only | T§6 | none |
| 11 | Sleep cycle replay -> extractive gist -> downscale (6 h gate) -> recombine; optional Distiller (host-callback first) | T§7 | none |
| 12 | Hand-rolled stdio MCP server, hooks, `mnemo` CLI as thin layers over fail-soft `client.py` | T§8 | none |
| 13 | Sync-back export: whole-file ownership, atomic, gitignored, identity excluded, plain list items | T§8 | none |
| 14 | Docker sidecar in TCP mode; build order per T§9 | T§8,§9 | Auth: ADR 0004 |

## Owner decisions on spec gaps (AGREED by owner)
- **G1** Codex, Gemini, Grok are MCP-only; no dedicated integration files.
- **G3** `reflect` = deliberate write of a synthesized insight; `forget` = status `suppressed` (hidden from recall/export, eligible for later prune).
- **G14** TCP binds 127.0.0.1 by default; `ENGRAM_TOKEN` required in container mode (details ADR 0004).
- **G16** Redact common secret patterns before journaling; purge consolidated journal rows after 30 days.
- **G20** Apache-2.0.
- **Scope** v1 = everything in both documents. All other gaps: recommended defaults accepted.
