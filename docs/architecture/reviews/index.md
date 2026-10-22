# Per-task review index (T01-T21)

Source: `git log` of `autocraft/kickoff` and `docs/architecture/lessons.md`. Tier is from `work-breakdown.md` (S = full review loop, N = single light review). Review summaries were not kept as files; the "fix commits" column counts commits after the feature commit whose message names a review or a fix (a lower bound on review rounds: the log does not record rounds that produced no commit). Defects are quoted from commit messages, not reconstructed.

| Task | Tier | Fix commits | Main defects found in review (from commit messages) |
|---|---|---|---|
| T01 skeleton, CI | N | 0 | none recorded |
| T02 adversarial dedup suite | S | 2 (test follow-ups) | suite extended to 3 long pairs per category and long-sentence veto-off pairs; stubbed blend test, skip guard |
| T03 vectorize | N | 1 | committed `.pyc` files untracked |
| T04 cortex | N | 1 | missing busy_timeout; DB files not 0600; `set_status` did not report existence |
| T05 activation, decay | S | 1 | unsanitised activation inputs; non-strict `kind`; recency not pinned |
| T06 gitctx | N | 1 | multiple git calls; narrow fail-soft; no negative cache; bare repo returned a root |
| T07 similarity | S | 0 (ADR 0006 added) | substitution damp needed a decision record and a direct test |
| T08 encode scoring | N | 1 | unbounded scoring regex cost (QUANTITY number anchored, 8 KB input cap) |
| T09 recall | N | 0 | none recorded |
| T10 spread, briefing | N | 0 | none recorded |
| T11 redaction | S | 1 (+ gaps noted in lessons) | ReDoS, secret leaks, over-redaction; known remaining gaps listed in lessons.md |
| T12 encode gate, observe | S | 2 (round 2 explicit) | order/case vetoes missing, no input cap, metadata not redacted; round 2: identifier veto missed snake/kebab/dotted/ALL_CAPS tokens |
| T13 consolidate replay | N | 1 | long episodes not chunked; failing episodes not checkpointed or poisoned; zero-durability gist slots kept |
| T14 downscale, recombine | S | 1 | negative `last_downscale` not clamped; prune timestamps lacked a grace period |
| T15 sleep_cycle | N | 1 (+ perf commit) | FairLock handoff, dedup not exactly pruned, live-session purge guard missing, distiller ran under the lock; per-write dedup cost bounded |
| T16 daemon | S | 1 | idle sockets held work slots; no first-byte deadline; RecursionError on deep JSON; client tests missing |
| T17 TCP auth, container mode | S | 1 | a correct token could be locked out; no failure delay or global bucket; non-literal hosts accepted |
| T18 fail-soft client | S | 0 own (client tests added in T16 review) | none recorded separately |
| T19 MCP server | N | 0 | none recorded (output hygiene came with T21) |
| T20 mnemo CLI | N | 1 | offline must exit 0 (added `--strict`); `up` reported inaccurately; no size cap |
| T21 e2e, recall quality, perf | N | 1 | recall returned junk for off-topic cues (lexical coverage and 0.30 semantic floors added); fairness tests could not tell FairLock from RLock; default suite cost cut from ~96 s to ~37 s |

PR #1 whole-branch review follow-ups (README, env var table, POSIX guard, schema-version check, CI, ResourceWarnings, write-scaling) are in the `pr1-fixes` commits.
