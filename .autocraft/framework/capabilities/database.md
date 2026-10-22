# Capability: database

## In plain words
Where your app remembers things permanently. Choosing it and changing its structure safely is a core engineering task; losing or leaking data is unrecoverable.

## Trigger words
save, store, remember, data, records, history, orders, profiles, database, SQL, table

## Safe defaults
- Default to a managed PostgreSQL (Supabase/Neon/RDS/Cloud SQL); SQLite only for single-user/local tools
- All structure changes via **versioned migrations** in the repo, applied by CI/deploy — never by hand
- Parameterized queries / an ORM (no string-built SQL); least-privilege database user per app
- Automatic backups on, and a **restore actually tested** once; separate dev/staging/prod databases
- Indexes for the queries you really run; constraints (not null, unique, foreign keys) enforce rules in the database
- Never use production data in tests; mask personal data in non-prod

## Must test
- Migrations apply on an empty database and upgrade from the previous version (and roll back if promised)
- Constraints actually reject bad data
- Concurrency: two simultaneous updates don't corrupt state (e.g. stock, balances)
- Queries on realistic data volumes stay fast

## Common mistakes
Editing production by hand; no backups or never restored; one database for dev and prod; SQL injection; missing indexes; N+1 queries; storing files or huge blobs in rows; destructive migrations without a plan

## Don't build it yourself
Hosting and patching a database server yourself. Use a managed service.
