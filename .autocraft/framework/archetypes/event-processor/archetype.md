# Archetype: event-processor

## Summary
A service that reacts to things happening: queue/stream messages, webhooks from other systems, file uploads, database changes. No user is waiting at a screen.

## In plain words
"When X happens (a payment arrives, a file is uploaded, a message is sent), automatically do Y." It must cope with messages arriving twice, late, out of order, or broken.

## Typical products
Webhook receivers (Stripe, GitHub), queue consumers, order-processing pipelines, notification senders, serverless functions, integrations between systems.

## Questions the requirements-analyst must ask
1. What are the events, where do they come from, and how many per minute? (default: ask; assume < 100/min)
2. What must happen for each event, and what if it fails? (default: retry, then park in a dead-letter queue and alert)
3. Can the same event arrive twice? (default: yes — must be safe)
4. Does order matter? (default: no, unless stated)
5. How fast must it react? (default: within a minute)

## Architecture must cover
Event contract/schema and versioning, **idempotent handlers** (dedupe key), retry with backoff, dead-letter queue and replay, ordering guarantees, webhook **signature verification**, concurrency limits, poison-message handling, transactional outbox if it also writes to a database and publishes, observability (lag, failures, age of oldest message), hosting model (serverless vs worker).

## Testing strategy
- **Unit:** handler logic with valid, malformed, duplicate and out-of-order events.
- **Integration:** real queue/emulator + database: process the same event twice → same result; failure → retry → dead-letter.
- **Contract:** sample events from the real provider validate against the schema.
- **Load/soak (light):** a burst of events does not lose or duplicate work.

## Walking skeleton (task 1)
Receives one test event end-to-end (queue or webhook), logs it with a correlation id, acks safely, has a dead-letter path and an alert on failures.

## CI & tooling
Lint, type-check, unit + integration (queue emulator), schema validation, build/package, dependency audit.

## Where it runs (simple defaults)
Serverless functions + managed queue (AWS Lambda/SQS, Cloud Run + Pub/Sub, Cloudflare Queues) or a small worker process on a managed platform.

## Security focus
Verify webhook signatures and timestamps (replay protection), validate payloads, least-privilege queue/DB permissions, secrets for provider keys, don't log sensitive payloads, protect the dead-letter queue contents.

## Release checklist additions
Idempotency proven; dead-letter queue + alert + replay runbook; retry limits set; schema versioning plan; consumer lag alert; rollback = redeploy previous version (messages stay queued).

## Common pitfalls (reviewers look for these)
Not idempotent; infinite retry loops; acking before the work is done; swallowing errors; no signature verification; assuming ordering; unbounded concurrency overwhelming a database; no alert on stuck queues.

## Suggested stacks
TypeScript/Python/Go functions or workers. Language flag: `node | python | go`.
