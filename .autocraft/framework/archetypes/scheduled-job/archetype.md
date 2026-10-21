# Archetype: scheduled-job

## Summary
Work that runs without a user present: on a schedule (cron), on events/queues, or as batch pipelines. Failure is silent unless you design for it.

## In plain words
"I want something to run by itself every night / every hour / when something happens." Nobody is watching, so it must tell you when it fails — and when it never ran at all.

## Typical products
Nightly reports, data syncs/ETL, cleanup tasks, reminder emails, queue consumers, billing runs.

## Questions the requirements-analyst must ask
1. What triggers it: time (which timezone?), an event/queue message, or manual? (default: cron in UTC)
2. How long can it run and how much data per run? (default: < 15 min, < 1M rows)
3. What happens if a run is missed, fails halfway, or runs twice? (default: safe to re-run — idempotent)
4. Who must be told when it fails, and how fast? (default: alert the owner within 15 min)
5. Does it need historical backfill? (default: provide a manual backfill command)

## Architecture must cover
Trigger mechanism and hosting (cron service, GitHub Actions schedule, Kubernetes CronJob, cloud scheduler, queue worker), **idempotency and checkpointing**, concurrency control (no overlapping runs; locks), retries with backoff, dead-letter handling, timeouts, input/output contracts, timezone/DST handling, batching and memory limits, observability (run log, duration, rows processed, last-success timestamp), **alerting on failure AND on missed runs**, backfill/replay procedure, secrets and least-privilege credentials, cost.

## Testing strategy
- **Unit:** transformation/business logic with edge cases (empty input, duplicates, malformed data, DST boundaries).
- **Integration:** run the job against a real test database/queue; run it **twice** and prove the result is identical (idempotency); simulate a mid-run crash and resume.
- **Failure tests:** downstream outage, timeout, partial batch failure.
- **Dry-run mode** test: no side effects.

## Walking skeleton (task 1)
Job entrypoint runs locally and in CI, logs start/finish with a run id, writes a "last success" marker, has a `--dry-run` flag, and is wired to the real scheduler with an alert on failure.

## CI & tooling
Lint, type-check, unit + integration tests, build image/package, validate schedule definition (cron expression), dependency audit.

## Where it runs (simple defaults)
A scheduler on a managed platform (GitHub Actions schedule, Cloud Scheduler, Kubernetes CronJob, Render cron) running a container or script.

## Security focus
Least-privilege credentials, secrets management (never in the schedule definition), PII handling in logs/outputs, safe handling of untrusted input files/messages, blast radius of a bad run (destructive operations need guards and dry-run).

## Release checklist additions
Idempotency demonstrated; overlap prevention in place; failure + missed-run alerts exist and were tested; runbook (how to re-run, backfill, pause); schedule/timezone reviewed; destructive actions guarded; resource limits set; rollback = disable the schedule + revert; data produced by a bad run can be repaired.

## Common pitfalls (reviewers look for these)
Not idempotent; overlapping runs; unbounded memory loading entire tables; swallowing exceptions so the run "succeeds"; no alert when the job never runs; local-time/DST bugs; retry storms; side effects in dry-run; no way to backfill.

## Suggested stacks
Python script/package + scheduler of choice · Node worker · Go binary. Language flag: `python | node | go`.
