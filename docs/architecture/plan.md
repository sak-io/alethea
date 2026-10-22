# Engram: Architecture Plan (adopt mode)

Status: DRAFT for owner + principal-engineer review. Inputs: docs/specs/engram.md, docs/technical-details.md (`T§`). Owner choices: ADR 0001. New decisions: ADRs 0002-0005.

## 1. Modules and dependencies (build order = T§9)

```
vectorize ─┬─> cortex ─┬─> recall ──┐
           │           └─> encode <─┘ (deferred import of recall.candidates)
           └───────────────> consolidate (cortex, vectorize, encode)
redact (pure, no deps; used by encode.observe/remember and daemon)
gitctx (pure, subprocess git)
daemon (Engram class: encode, recall, consolidate, redact, export)  <── client (no engram deps, stdlib socket only)
mcp_server, hooks/engram_hook.py, scripts/mnemo  ->  client + gitctx   (no logic of their own)
export (reads via daemon verb; writes files)
plugins (lazy): embedders_ext, distiller
```

Rules: `client.py` imports nothing from the engine (keeps hooks fast, fail-soft, and testable without a daemon). Thin layers import only `client`, `gitctx`. No module-level cycle: the encode/recall cycle is broken by the deferred import (T§9). All clocks injected. Phases: P0 skeleton + adversarial tests; P1 core (R1-R16, R20-R24, R26b); P2 Claude Code + export (R17-R19 per spec phase table are engine items already built in P1; P2 here = hooks, wiring, export); P3 optional (R27-R31).

## 2. Cross-cutting design notes
- **Data:** one SQLite file `~/.engram/engram.db` (dir 0700). Schema versioned in `engram_meta.schema_version`; migrations = ordered idempotent steps run at open. Vectors float32 BLOB (2 KB/row at 512-d).
- **API:** NDJSON, one request per line `{"verb","args","token?","v":1}`; reply `{"ok":bool,...}`. Errors never raise through client. Verbs per T§8 plus `export_rules`, `reembed`, `pending` (G13).
- **Config:** env > `~/.engram/config.json` (0600) > defaults; thresholds are module constants overridable per embedder.
- **Observability:** `engram_status` (counts per kind/status, last_sleep, embedder id, FTS5 availability, offline-state), rotating log in `~/.engram/`, log-once on fallbacks. No metrics server.
- **Testing:** unittest only. Unit tests per module in `tests/`, fixture phrase tables for marker lists (G23), a committed recall regression corpus (G27), fake clock, fake git, in-process daemon on a temp dir for e2e. CI = `python3 -m unittest discover -s tests -t .` on 3.9 and latest, with a socket guard that fails on non-loopback connects (R1).
- **Security:** threat model summary in section 4; full `threat-model.md` is produced as part of task T02/T11/T17 outputs and consolidated in T31. Exported and LLM text treated as untrusted. Secrets never logged.

## 3. Plan validation: findings and recommended changes (owner decides)

| Sev | Finding | Recommendation |
|---|---|---|
| BLOCKER | **Activation vs downscale (G2)**: with `n` = access count, decaying `strength` never changes activation, so archival never triggers. G2's default (`n_eff = strength`) fixes the mechanism but exposes a second problem: G11 floors age at 1 s, and `d*ln(age)` in seconds drives an unreinforced episodic memory below -3.2 within about 50 minutes. | ADR 0003: strength as `n_eff`, age in days (1-minute floor), injectable clock. Simulated outcome: episodic archives about day 30, semantic about day 40-45 unreinforced. |
| MAJOR | Decay is counted twice (age term and strength shrink) and downscale is kind-agnostic, so the kind-specific exponents are largely overridden: facts and procedures fade nearly as fast as episodes. | Recommend per-kind downscale rate scaled by `d_kind/0.5`. Ship owner's uniform 0.93 behind a `DOWNSCALE_RATE` table; T05 outputs a half-life table so the owner can choose. |
| MAJOR | **Container TCP vs G14**: loopback-bound daemon inside a container is unreachable via published port. | ADR 0004: explicit `ENGRAM_BIND`, token mandatory (refuse start), compose publishes `127.0.0.1` only. |
| MAJOR | Global RLock held across a full sleep can exceed the 2 s hook budget and make a healthy daemon look offline. | ADR 0005: per-batch locking. |
| MAJOR | Redaction (G16) is best-effort; regex lists miss secrets and will over-match (e.g. hex hashes). Also `remember`/`reflect` content must be redacted, not only the journal, or secrets reach LTM and the exported always-on file. | Redact at every ingestion point (`observe`, `remember`, `reflect`, `reembed` not needed); fixture table of positive and negative cases; document as best-effort, never claim secret safety. |
| MAJOR | Entry-point import breakage when run by absolute path (Cursor) vs `-m` (Claude Code). | ADR 0002 bootstrap and a test per entry style. |
| MINOR | Hook latency: each hook is a new Python process (startup ~30-50 ms) plus a git call (up to 1 s timeout). | Cache `repo_root` per process only helps within one process; also cache on disk keyed by cwd for 60 s (cheap, fail-soft). Hook hard timeout 2 s. |
| MINOR | Vector scan in pure Python: 600 rows x 512 dims per novelty/recall is about 0.3M multiply-adds (tens of ms); acceptable for the 300 ms p95 target, but store vectors as `array('f')` and pre-normalize. Dense (P3) embedders would be 768+ dims. | Perf test in T21 at 10k memories. |
| MINOR | `last_downscale` gate at 6 h plus "periods" cap 14 days: a daemon dormant for 3 months forgets only 14 days' worth. Intentional (safe), keep. | none |
| MINOR | Journal grows with every turn; 30-day purge only covers consolidated rows. Unconsolidated rows (daemon off, no SessionEnd) accumulate. | Purge unconsolidated rows after 90 days too (constant), reported in status. Owner may veto. |
| MINOR | `forget` suppresses but G3 says "eligible for later prune": no prune rule exists. | Prune suppressed rows after 90 days in downscale (constant); identity never pruned (G9) unless explicitly forgotten by the user. Owner may veto. |
| INFO | R10 lists "no token length floor" for the veto but jaccard needs tokens; use the same tokenizer for veto and jaccard. | In T07 card. |
| INFO | Spec phase table puts R17-R19 in P2 but they are engine behavior (briefing, sleep, downscale); the plan builds them in P1 and uses P2 for host wiring. | Plan ordering only. |

## 4. Threat model (summary)
Assets: journal and memories (may contain PII/secrets), exported rules files, token. Boundaries: hook/MCP/CLI -> daemon socket; daemon -> filesystem; exported file -> agent prompt; LLM/remote embedder output -> encode gate.
Threats and mitigations: other local user reads socket/DB (0700/0600, token for TCP); secret persisted in journal (redaction, 30-day purge); prompt injection via exported/LLM text (encode gate, length caps, plain list items, never executed); memory poisoning by false merge (adversarial dedup, bias to false negatives); denial via huge request (1 MiB cap, timeouts); git binary/hook path injection (argv list, no shell, realpath); export clobbering a user file (ownership header check, refuse otherwise); supply chain (stdlib only core, optional deps lazy and pinned in docs).

## 5. Risks
1. Similarity false merges (silent, permanent). Tier S, tests first (T02), re-run per embedder (T25).
2. Fail-soft violations at any call site; a lint-style test greps hooks/MCP/mnemo for direct socket use.
3. Activation calibration (ADR 0003) shifts the whole feel of memory; needs owner sign-off.
4. Marker regex lists (G23) are the heart of encode quality; fixtures may be over-fitted. Mitigation: T21 recall regression corpus with held-out phrases.
5. Concurrency: lock plus SQLite connection thread rules (`check_same_thread`); single connection used only under the lock.
6. Host formats (hook JSON, Cursor mdc) change between versions; verify against docs at implementation time (T§8 note), fixture-tested.
7. Scope: all-in v1 is about 31 tasks; P3 is explicitly cuttable without affecting P1/P2.
