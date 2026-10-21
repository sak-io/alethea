# Archetype: fullstack-app

## Summary
One codebase that contains both the user interface and the server logic, usually with a database: SaaS products, community sites, booking systems, small stores. The most common "I want an app" shape.

## In plain words
"I want an app people sign in to and use in the browser, that remembers their stuff." One project, one deployment: pages the user sees plus the behind-the-scenes code and a database.

## Typical products
SaaS, marketplaces, booking/scheduling apps, membership sites, CRMs, simple stores.

## Questions the requirements-analyst must ask
1. Who are the users and what are the 3 main things they do? (default: ask)
2. Do people need accounts? Different kinds of users (customer/admin)? (default: email sign-in, user + admin)
3. What data does the app store, and is any of it personal or payment data? (default: personal)
4. Does it take payments or send emails/notifications? (default: no — add later)
5. How many users in the first year? (default: < 1,000)

## Architecture must cover
Framework with server rendering (Next.js/Remix/Rails/Django), routing and page/data-loading model, database schema + migrations, auth and sessions, authorization (who can see/edit what), validation on the server (never trust the browser), background work if any, file storage, email, error handling, environment config, deployment. UX artifacts from `ux-designer`. Capabilities listed in config (auth, payments, email…) are part of the design.

## Testing strategy
- **Unit:** business rules, validation.
- **Integration:** server actions/routes against a real test database, including authorization (user A cannot read user B's data).
- **Component:** UI states (loading, empty, error, success).
- **E2E:** sign up → core task → sign out in a real browser; run in CI.
- **Accessibility:** automated + keyboard checks.

## Walking skeleton (task 1)
App runs locally and deploys to a preview URL; home page, a database-backed page, sign-in placeholder, health route, migrations run in CI, one unit/integration/E2E test.

## CI & tooling
Lint, type-check, unit + integration (database service container), Playwright E2E, migration check, build, dependency audit, preview deploys.

## Where it runs (simple defaults)
A managed platform (Vercel/Render/Fly.io/Railway) with a managed Postgres database. Avoid running your own servers at the start.

## Security focus
Authorization on every server route (not just hiding buttons), CSRF, XSS, SQL injection (use parameterized queries/ORM), secure sessions/cookies, rate limiting on login, secrets only on the server, personal-data handling.

## Release checklist additions
Migrations backward compatible and reversible; accessibility pass; environment variables set in production; backups of the database enabled and restore tested once; error monitoring on; rollback = redeploy previous build (database changes need a plan).

## Common pitfalls (reviewers look for these)
Authorization only in the UI; trusting client input; no migrations (manual DB edits); secrets in client bundles; N+1 queries; missing empty/error states; no backups; tests only for happy paths.

## Suggested stacks
Next.js/Remix + Postgres → `node` · Django + Postgres → `python` · Rails (add a CI template).
