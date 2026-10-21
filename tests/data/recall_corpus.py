"""Recall regression corpus (T21): realistic engineering memories, cues, expected top-3.

MEMORIES: (key, kind, text). DISTRACTORS: near-miss memories that share vocabulary with
a target but answer a different question; they must not displace the expected memory.
CUES: (cue, expected keys, tag); a cue passes when any expected key is in the top 3.
tag "held-out" marks phrasing deliberately unlike the stored wording (not used for tuning).
NEGATIVE_CUES: nothing in the corpus is relevant; recall must return no memory.
EDGES: (src, dst, kind) for the structural-spread case ("the auth bug").
"""

MEMORIES = [
    # decisions
    ("d_postgres", "semantic", "We chose PostgreSQL over MongoDB for the billing service because we need transactional integrity and joins."),
    ("d_monorepo", "semantic", "Decision: keep all services in a single monorepo managed with Bazel to share protobuf definitions."),
    ("d_redis_sessions", "semantic", "Decided to move user sessions from signed cookies to a Redis store so tokens can be revoked server side."),
    ("d_grpc", "semantic", "Internal service-to-service calls use gRPC; the public API stays REST with JSON."),
    ("d_trunk", "semantic", "We use trunk-based development with short-lived branches and feature flags instead of long release branches."),
    ("d_python311", "semantic", "The backend targets Python 3.11; do not use features from 3.12 until the base image is upgraded."),
    ("d_no_orm", "semantic", "Chose plain SQL with sqlx instead of an ORM in the ledger module to keep queries explicit and reviewable."),
    ("d_kafka", "semantic", "Order events are published to Kafka with at-least-once delivery; consumers must be idempotent."),
    ("d_uuid7", "semantic", "Primary keys are UUIDv7 so they sort by creation time and index well."),
    ("d_tz", "semantic", "All timestamps are stored in UTC and converted to the user timezone only at the presentation layer."),
    # causes / bug post-mortems
    ("c_auth_race", "episodic", "The login bug was a race condition in token refresh: two tabs refreshed at once and the second invalidated the first token."),
    ("c_oom", "episodic", "The nightly export crashed with out of memory because the CSV writer buffered every row before flushing."),
    ("c_pool", "episodic", "Connection pool exhaustion in production was caused by a missing finally block that never returned connections after timeouts."),
    ("c_cert", "episodic", "The staging outage on March 3 happened because the TLS certificate expired and nobody was alerted."),
    ("c_n_plus_1", "episodic", "The slow dashboard was an N+1 query in the invoice list; fixed by eager loading line items."),
    ("c_flaky", "episodic", "The flaky checkout test failed because it depended on wall clock time; we froze the clock in the fixture."),
    ("c_deadlock", "episodic", "Deadlock in the inventory worker came from locking rows in inconsistent order; always lock by ascending product id."),
    ("c_cache", "episodic", "Stale prices on the storefront were caused by the CDN caching the pricing endpoint for an hour."),
    ("c_migration", "episodic", "The failed deploy was a migration that added a NOT NULL column without a default on a table with millions of rows."),
    ("c_encoding", "episodic", "Garbled customer names came from reading the upload as latin-1 when the files were UTF-8."),
    # procedures
    ("p_deploy", "procedural", "To deploy: merge to main, wait for CI, run make release, then promote the build in the console with the canary at 5 percent."),
    ("p_rollback", "procedural", "Rollback procedure: run kubectl rollout undo deployment/api, then verify the health endpoint and post in the incident channel."),
    ("p_migrate", "procedural", "Run database migrations with make migrate-up; always back up with pg_dump first and test on staging."),
    ("p_oncall", "procedural", "When paged, acknowledge within five minutes, open an incident document, and escalate to the secondary after fifteen minutes."),
    ("p_secrets", "procedural", "Rotate API secrets by creating the new key in the vault, deploying, then revoking the old key after 24 hours."),
    ("p_release_notes", "procedural", "Generate release notes with scripts/changelog.py --since the last tag and paste them into the GitHub release."),
    ("p_local_setup", "procedural", "Local setup: install Docker, copy .env.example to .env, run make dev and open http://localhost:8080."),
    ("p_lint", "procedural", "Before committing run ruff check and ruff format; CI rejects unformatted code."),
    ("p_profiling", "procedural", "To profile a slow endpoint enable pyinstrument with PROFILE=1 and read the HTML report in tmp/profiles."),
    ("p_backup_restore", "procedural", "Restore a database backup with pg_restore into a scratch instance, never directly into production."),
    # identity / preferences
    ("i_name", "identity", "The user's name is Priya Natarajan and she is a staff engineer on the payments platform team."),
    ("i_editor", "identity", "Priya prefers Neovim with tmux and dislikes graphical IDEs."),
    ("i_style", "identity", "She likes small focused pull requests and wants tests included with every change."),
    ("i_comm", "identity", "Priya wants terse answers, no emojis, and trade-offs listed instead of long explanations."),
    ("i_lang", "identity", "Her main languages are Go and Python; she is learning Rust on the side."),
    ("i_tz", "identity", "She works from Bangalore and is available 9am to 6pm India time."),
    # facts / system knowledge
    ("f_rate_limit", "semantic", "The public API rate limit is 100 requests per minute per API key, enforced at the gateway."),
    ("f_s3", "semantic", "User uploads are stored in the s3 bucket acme-uploads-prod with lifecycle expiry after 90 days."),
    ("f_staging", "semantic", "The staging environment lives at staging.acme.internal and is reset every Sunday night."),
    ("f_ci", "semantic", "CI runs on GitHub Actions; the full pipeline takes about twelve minutes and caches the Bazel output."),
    ("f_feature_flags", "semantic", "Feature flags are managed in LaunchDarkly and every flag needs an owner and an expiry date."),
    ("f_logging", "semantic", "Services emit structured JSON logs to stdout; Datadog collects them and the request id lives in the trace_id field."),
    ("f_sla", "semantic", "The payments API has a 99.95 percent availability target and a p99 latency budget of 400 ms."),
    ("f_search", "semantic", "Product search runs on Elasticsearch 8 with an hourly reindex job fed from the catalog database."),
    ("f_mobile", "semantic", "The mobile app is built with React Native and release builds go through Fastlane."),
    ("f_pii", "semantic", "Customer email addresses and phone numbers are PII and must never be written to logs."),
    ("f_cron", "semantic", "The invoice reminder cron job runs at 02:00 UTC from the scheduler service."),
    ("f_retry", "semantic", "HTTP clients retry idempotent requests three times with exponential backoff and jitter."),
    ("f_cdn", "semantic", "Static assets are served through CloudFront with content-hashed filenames and a one year cache lifetime."),
    ("f_graphql", "semantic", "The admin dashboard talks to a GraphQL gateway that federates the user, order and catalog services."),
    ("f_testing", "semantic", "Unit tests must run without network access; integration tests use testcontainers for Postgres."),
    ("f_webhooks", "semantic", "Stripe webhooks are verified with the signing secret and processed idempotently by event id."),
    ("f_k8s", "semantic", "Production runs on a managed Kubernetes cluster in eu-west-1 with three node pools: general, memory and gpu."),
    ("f_monitoring", "semantic", "Alerts go through PagerDuty; the error budget burn alert fires when four percent of the monthly budget burns in an hour."),
    ("f_dependencies", "semantic", "Dependencies are pinned in poetry.lock and Renovate opens weekly update pull requests."),
    ("f_review", "semantic", "Every pull request needs one approval from a code owner and passing checks before merge."),
    ("f_i18n", "semantic", "Translations live in the locales directory as JSON files and are synced from Crowdin nightly."),
    ("f_perf", "semantic", "The checkout page performance budget is 2 seconds largest contentful paint on a mid range phone."),
    ("f_audit", "semantic", "Audit log entries for admin actions are append only and retained for seven years."),
    ("f_sso", "semantic", "Employees sign in through Okta single sign on using SAML; service accounts use short lived OIDC tokens."),
    ("f_batch", "semantic", "The reconciliation batch job processes bank statement files dropped in the sftp inbox every morning."),
    ("f_ratelimit_login", "semantic", "Login attempts are throttled to five per minute per account to slow credential stuffing."),
]

DISTRACTORS = [
    ("x_mongo", "semantic", "MongoDB is used by the analytics team for clickstream storage, not for transactional data."),
    ("x_old_deploy", "episodic", "Last Tuesday the deploy to the sandbox environment took forty minutes because the registry was slow."),
    ("x_cookie", "semantic", "The marketing site sets a cookie banner preference that expires after thirty days."),
    ("x_auth_docs", "semantic", "The authentication documentation page was reorganised into a quickstart and a reference section."),
    ("x_pool_party", "episodic", "The team pool party is on Friday afternoon, bring swimwear and sunscreen."),
    ("x_cert_renewal", "episodic", "Reminder that the office badge certificate renewal form is due by the end of the quarter."),
    ("x_csv", "semantic", "CSV exports from the reports page use semicolons as separators for the German locale."),
    ("x_python2", "semantic", "A legacy cron script still runs on Python 2.7 in the data warehouse and is scheduled for retirement."),
    ("x_lint_js", "procedural", "The frontend package uses eslint and prettier configured in package.json for style checks."),
    ("x_backup_laptop", "procedural", "Back up your laptop weekly using Time Machine to the shared drive."),
    ("x_rollback_ui", "episodic", "Design asked to roll back the new button colour on the settings page."),
    ("x_secrets_hr", "semantic", "HR salary information is confidential and stored in the finance share."),
    ("x_dark_mode", "semantic", "The web app dark mode toggle is stored in local storage under the key theme."),
    ("x_redis_cache", "semantic", "The product catalog cache in Redis has a five minute ttl and is warmed after each deploy."),
    ("x_kafka_logs", "semantic", "Kafka retention for the logging topic is three days and brokers run on dedicated nodes."),
    ("x_alert_email", "semantic", "Weekly digest emails are sent on Monday morning and include a summary of closed tickets."),
    ("x_stripe_pricing", "semantic", "Stripe fee for card payments is documented in the finance wiki with the current pricing table."),
    ("x_timezone_meeting", "semantic", "The weekly retro is at 4pm Pacific which is awkward for the Bangalore office."),
    ("x_go_tour", "semantic", "A tour of Go generics was presented in the guild meeting and the slides are in the wiki."),
    ("x_migrate_cloud", "episodic", "Plans to migrate the data warehouse to another cloud provider were postponed until next year."),
]

CUES = [
    ("why did we pick postgres for billing", ["d_postgres"], "direct"),
    ("which database does the billing service use", ["d_postgres"], "held-out"),
    ("how are services organised in the repository", ["d_monorepo"], "held-out"),
    ("why are sessions stored in redis", ["d_redis_sessions"], "direct"),
    ("how do internal services talk to each other", ["d_grpc"], "held-out"),
    ("what branching strategy do we follow", ["d_trunk"], "held-out"),
    ("which python version should the backend use", ["d_python311"], "direct"),
    ("do we use an ORM", ["d_no_orm"], "held-out"),
    ("are kafka consumers required to be idempotent", ["d_kafka"], "direct"),
    ("what kind of primary keys do we use", ["d_uuid7"], "held-out"),
    ("what timezone are timestamps stored in", ["d_tz"], "direct"),
    ("what caused the login bug with two tabs", ["c_auth_race"], "direct"),
    ("export job ran out of memory", ["c_oom"], "direct"),
    ("production connection pool exhausted why", ["c_pool"], "direct"),
    ("why did staging go down in march", ["c_cert"], "held-out"),
    ("slow dashboard invoice queries", ["c_n_plus_1"], "direct"),
    ("flaky checkout test cause", ["c_flaky"], "direct"),
    ("inventory worker deadlock lock ordering", ["c_deadlock"], "direct"),
    ("why were storefront prices out of date", ["c_cache"], "held-out"),
    ("deploy failed because of a migration", ["c_migration"], "direct"),
    ("customer names show garbled characters", ["c_encoding"], "held-out"),
    ("how do I deploy a release with the canary", ["p_deploy"], "direct"),
    ("how to roll back a bad release", ["p_rollback"], "held-out"),
    ("how do I run database migrations", ["p_migrate"], "direct"),
    ("when paged acknowledge and escalate", ["p_oncall"], "direct"),
    ("how do we rotate an api secret", ["p_secrets"], "direct"),
    ("how to produce release notes", ["p_release_notes"], "direct"),
    ("setting up my local environment", ["p_local_setup"], "held-out"),
    ("what must I run before committing code", ["p_lint"], "held-out"),
    ("how to profile a slow endpoint", ["p_profiling"], "direct"),
    ("restoring a database backup", ["p_backup_restore"], "direct"),
    ("what is the user's name", ["i_name"], "direct"),
    ("which editor does she prefer", ["i_editor"], "direct"),
    ("how should answers be phrased for her", ["i_comm"], "held-out"),
    ("what is the api rate limit per key", ["f_rate_limit"], "direct"),
    ("where are user uploads stored", ["f_s3"], "direct"),
    ("how long does the CI pipeline take", ["f_ci"], "direct"),
    ("can customer email be logged", ["f_pii"], "held-out"),
    ("when does the invoice reminder job run", ["f_cron"], "direct"),
    ("how do we retry failed http requests", ["f_retry"], "held-out"),
    ("who must approve a pull request", ["f_review"], "held-out"),
    ("how do employees log in", ["f_sso"], "held-out"),
    ("login throttling attempts per minute", ["f_ratelimit_login"], "direct"),
]

NEGATIVE_CUES = ["zxqv flurb wibble", "qqqq xjzk plorf", "lorem ipsum dolor sit amet"]

# 'the auth bug': the cue names the bug; the fix and the decision share no words with it.
AUTH_EXTRA = [
    ("e_auth_fix", "episodic", "Serialized refresh with a per-user mutex and a short lived idempotency key."),
    ("e_auth_decision", "episodic", "Moved the whole flow behind a single leader so replicas never contend."),
]
AUTH_CUE = "the auth bug"
AUTH_CUE_FULL = "auth token refresh race condition bug"
EDGES = [("c_auth_race", "e_auth_fix", "caused"), ("c_auth_race", "e_auth_decision", "caused")]

# Regression gate (tests/test_recall_regression.py).
MIN_RECALL_AT_3 = 0.93   # expected memory in the top 3
MIN_TOP1 = 0.85          # expected memory ranked first
MIN_PRECISION_AT_3 = 0.31  # relevant / returned among top 3 (single-target cues cap this at 1/3)
