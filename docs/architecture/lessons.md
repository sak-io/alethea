# Lessons: known pitfalls (builders read before starting any task)

1. Similarity is `max(cosine, jaccard)`, never a blend; identifier veto (digits, path separators, camelCase, no token length floor) and negation/antonym veto apply under every embedder; novelty is `1 - best_similarity`, not rank-normalized score. A false merge is silent and permanent: bias to false negatives (T§5).
2. Never use Python `hash()` for vectors (PYTHONHASHSEED); use blake2b with salt `engram-v1`; never compare vectors with different `embedder_id` (T§4).
3. Every client call site catches `OSError`/timeouts and returns `{"ok":False,"offline":True}` with exit 0; hooks emit empty output offline; `autostart()` is a no-op in TCP/sidecar mode (T§8, T§10).
4. `downscale()` must be gated to once per 6 h and clamp future/corrupt `last_downscale` to 0 elapsed; activation uses days and `n_eff = strength` (ADR 0003); always inject the clock. `encode` imports `recall.candidates` inside the function body only; `mcp_server.py` must run both as `-m` and by absolute path (T§9, ADR 0002).
5. `repo_root` = parent of `git rev-parse --git-common-dir` on `realpath(cwd)`, resolved in the hook/MCP process, branch excluded; the single RLock is held per batch only; exported files are whole-file owned, written atomically, and never touch a file lacking the Engram header (T§3, T§8, ADR 0005).

## Known redaction gaps (T11, best-effort; candidates for a later pass)
glpat-, ya29. OAuth, Azure `AccountKey=`, DigitalOcean `dop_v1_`, Slack webhook URLs, `sshpass -p` / `mysql -pXXX`, very short values under `*_CRED` names. Scoring input is capped at 8192 chars (T08): a marker beyond that point is not scored.

## Test-suite cost
Worst-case benchmarks in `tests/test_sleep_cycle.py` are opt-in: `ENGRAM_SLOW_TESTS=1 python3 -m unittest discover -s tests -t .` (~+60 s). The default suite stays ~30 s so every task and CI run is cheap. Always-on regression kept: per-episode cost stays flat as memories grow, and the concurrent-writer fairness test.
