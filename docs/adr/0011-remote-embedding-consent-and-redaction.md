# ADR 0011: Remote embedding: consent, redaction at the boundary, no redirects, key handling

Status: Accepted (T26). Real provider endpoints were NOT contacted in testing (stub servers only); real-provider behaviour is unverified.

## Context
`openai:<model>` embeds memory text with an OpenAI-compatible HTTP API. That sends the text of memories, and of recall cues, to a third party. Engram's privacy stance (redaction before storage, local by default) must not be weakened silently, and a remote service is outside our control in the sense of ADR 0010 (slow, failing, hostile).

## Decision
1. **Double opt-in, off by default.** `ENGRAM_EMBEDDER=openai:<model>` selects it; for any non-loopback host `ENGRAM_EMBEDDER_ALLOW_REMOTE=1` is also required. Without it the factory raises before creating a socket, status shows `unavailable: remote not acknowledged`, and the daemon uses hashed vectors. Endpoint: `ENGRAM_EMBEDDER_URL` (default `https://api.openai.com/v1`, path `/embeddings`). `http://` only for loopback; everything else `https://` with `ssl.create_default_context()`; no option disables verification. Config comes from the environment only (not `config.json`).
2. **Redaction at the adapter boundary.** `embedders_remote.prepare()` (redact, then cap at 8000 chars, never empty) is the only path to the wire, so no caller can bypass it. Batches are at most 64 strings. `recall.embed_cue` additionally redacts the cue (it did not before), so cue vectors match stored redacted text. Redaction is best-effort (known gaps in lessons.md).
3. **No redirects.** `http.client` does not follow them; any 3xx is a failure. No urllib opener: no proxy handler (`HTTP(S)_PROXY` is ignored; documented), no cookie jar.
4. **Key handling.** Only `ENGRAM_EMBEDDER_API_KEY` (no `OPENAI_API_KEY` fallback), format-checked (printable, no whitespace), held in the embedder object, used only in the `Authorization` header. It is never in status, logs, exceptions (`RemoteError` carries a fixed kind and detail), the DB, `daemon.json`, files or argv. A hostile server that echoes the key in its error body is covered by a test.
5. **Hardening.** One overall deadline per call (<= `ENGRAM_EMBEDDER_TIMEOUT`), 8 MB response cap (Content-Length and streamed), `Content-Type: application/json`, strict JSON (no NaN/Infinity, count and unique index checks, finite non-empty floats, one dimension for all items and calls), model name validated, `User-Agent: engram/<version>`. 429/5xx are failures for the ADR 0010 breaker; `Retry-After` (cap 60 s) makes the adapter refuse further calls without network or waiting. Id: `openai:<model>:<dim>`, dimension learned from a probe request at (background) load and enforced after.
6. **Reembed** (`engram/reembed.py`, verb `reembed` actions plan/start/status/cancel, `mnemo reembed`): background thread; batch vectors computed without the engine lock, then one short transaction writes vectors, `embedder_id`, cursor and counters (compare-and-set on content and old id, so a concurrent edit is never overwritten). Resumable via `engram_meta.reembed`; a run `running` at shutdown or crash resumes at next start, re-validating the target and consent on every batch. Failure backoff, pause after 5 consecutive failures; a hashed fallback answer is a failure. For a remote target `confirm` (CLI `--yes`) is required and IDENTITY memories are skipped unless `--include-identity`. One run at a time; sleeps between batches.

## Consequences
- With the default timeout (1 s) hosted APIs will often fall back; users raise `ENGRAM_EMBEDDER_TIMEOUT`.
- Unredacted secret shapes still leave the machine; this is documented, not solved.
- Users behind a mandatory HTTP proxy cannot use the adapter (alternative: honour proxies via urllib; rejected for surprise and redirect-handler risk).
- Archived/suppressed rows are not migrated; the thresholds of real models remain unmeasured (ADR 0010).
- Alternatives rejected: urllib with a custom opener (implicit proxies, redirect handler), consent via `config.json` (a file can be changed by a repo-level tool), a `verify=False` escape hatch.
