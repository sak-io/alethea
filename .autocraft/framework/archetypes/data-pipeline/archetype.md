# Archetype: data-pipeline

## Summary
Moves and reshapes data from sources into a place where people can analyze it: ETL/ELT jobs, a warehouse, reports and dashboards.

## In plain words
"I have data in several places and want one trustworthy place to ask questions and make charts." Most failures are *bad data that looks fine*, so checks matter as much as code.

## Typical products
Analytics warehouse, nightly sync from apps/CRMs, reporting dashboards, data exports, metrics pipelines.

## Questions the requirements-analyst must ask
1. Which sources, how big, how often updated? (default: ask)
2. Which questions/reports must it answer? (default: ask for 3)
3. How fresh must the data be (hourly, daily)? (default: daily)
4. Is any data personal or regulated? (default: yes — minimize and restrict)
5. Who consumes it (dashboards, analysts, other systems)? (default: a dashboard tool)

## Architecture must cover
Sources and extraction method (API, DB replica, files), landing/raw → cleaned → modeled layers, transformation tool (SQL/dbt/Spark/pandas), orchestration and scheduling, **idempotent and incremental loads**, schema-change handling, **data quality checks** (null/unique/freshness/volume/range), lineage and documentation, access control and PII masking, cost controls, backfill and reprocessing, secrets for source systems.

## Testing strategy
- **Unit:** transformation logic on small fixtures (edge cases, nulls, duplicates, time zones).
- **Data tests:** uniqueness, not-null, accepted values, referential integrity, row-count reconciliation vs source, freshness.
- **Integration:** run the pipeline on a sample dataset end-to-end in an ephemeral schema; run twice → identical.
- **Regression:** key metrics compared with a known-good snapshot.

## Walking skeleton (task 1)
One source → raw table → one modeled table → one data-quality test, scheduled, with failure alerts; runnable on sample data in CI.

## CI & tooling
Lint (SQL/Python), unit + data tests on sample data, schema/docs generation, dependency audit.

## Where it runs (simple defaults)
Managed warehouse (BigQuery/Snowflake/Postgres) + a managed orchestrator or scheduled GitHub Actions/Cloud scheduler for small workloads; dbt for transformations.

## Security focus
PII minimization and masking, column/row-level access, source credentials, no data in logs, access reviews, retention policy.

## Release checklist additions
Data tests pass on real data; row counts reconcile; backfill/rollback plan (rebuild from raw); docs/lineage updated; freshness alert exists; cost estimate checked; consumers notified of schema changes.

## Common pitfalls (reviewers look for these)
Non-idempotent loads creating duplicates; silent schema drift; time-zone/DST errors; full-table reloads at scale; no reconciliation with source; metrics defined differently in different places; PII copied everywhere.

## Suggested stacks
SQL + dbt, Python (pandas/polars) → `python`.
