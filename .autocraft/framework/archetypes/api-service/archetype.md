# Archetype: api-service

## Summary
A backend that other programs call over HTTP/gRPC/GraphQL: order management, payments, user accounts, internal platform services.

## In plain words
"I want other programs or apps to talk to my system." An API is a doorway with rules: who may enter, what they may ask for, and what they get back. Everything behind it (data, logic) stays private.

## Typical products
REST/GraphQL APIs, microservices, webhooks receivers, backends-for-frontends.

## Questions the requirements-analyst must ask
1. Who are the API consumers (your own apps, partners, the public)? (default: own apps)
2. What data does it own, and how sensitive is it? (default: personal)
3. Expected peak requests/second and acceptable latency? (default: 50 rps, p95 < 300 ms)
4. Does anything need to be exactly-once / never double-processed (payments, orders)? (default: yes, via idempotency keys)
5. How do callers authenticate? (default: OAuth2/JWT)

## Architecture must cover
API contract (OpenAPI), versioning and deprecation, idempotency, pagination, error format, authn/authz model (incl. object-level authorization), data model + migrations, concurrency and consistency, rate limiting, observability (logs/metrics/traces), config and secrets, deployment and rollback.

## Testing strategy
- **Unit:** domain logic, validation, state machines.
- **Integration:** every endpoint against a real test database; auth failure paths; migrations up/down.
- **Contract:** responses match the OpenAPI spec; consumer-visible changes are flagged.
- **E2E/smoke:** deployed health + one critical flow.

## Walking skeleton (task 1)
App boots, `GET /health`, DB connection, structured logging, OpenAPI file served, CI green with one unit and one integration test.

## CI & tooling
Lint, type-check, unit + integration (database service container), OpenAPI lint, dependency audit, build/container image.

## Where it runs (simple defaults)
A managed platform (Render/Fly.io/Railway/Cloud Run) with a managed Postgres database; a container if you know Docker. Avoid self-managed servers at the start.

## Security focus
Broken object-level authorization (IDOR), injection, mass assignment, authn/z on every route, rate limiting, secrets, PII in logs, SSRF, dependency CVEs.

## Release checklist additions
Backward-compatible API change (or version bump), migration safe for rolling deploy, new env vars provisioned, dashboards/alerts for new endpoints, rollback = redeploy previous version + migration plan.

## Common pitfalls (reviewers look for these)
Trusting client-supplied IDs/roles; non-atomic read-modify-write (overselling); missing idempotency; unbounded list queries; N+1 queries; swallowing errors; leaking stack traces; tests that only hit the happy path.

## Suggested stacks
Python + FastAPI + Postgres · Node + TypeScript + Fastify/NestJS + Postgres · Go + chi + Postgres. Language flag: `python | node | go`.
