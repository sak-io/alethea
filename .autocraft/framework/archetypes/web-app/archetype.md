# Archetype: web-app

## Summary
A user-facing website or single-page app that runs in the browser: dashboards, storefronts, admin consoles, marketing sites with logic.

## In plain words
"I want a website people use in the browser, with buttons, forms and screens." This archetype is the screens. If it also saves data or has accounts, pair it with `api-service` — or use `fullstack-app` for one combined project.

## Typical products
SPA (React/Vue/Svelte), server-rendered app (Next.js/Remix), admin UI on top of an existing API.

## Questions the requirements-analyst must ask
1. Who are the users and what are their top 3 tasks? (default: ask the human to list them)
2. Needs sign-in? Which roles see what? (default: email login + user/admin)
3. Which browsers and devices must work? (default: last 2 versions of evergreen browsers + mobile web)
4. Accessibility target? (default: WCAG 2.2 AA)
5. Does it talk to an existing API? Is there a design/brand to follow? (default: no brand; a simple clean system)

## Architecture must cover
Rendering model (SPA/SSR/static), routing, state management, API client and error handling, auth/session handling (cookies vs tokens), design system/components, accessibility, i18n if needed, performance budget, SEO if public, hosting/CDN, environment config, feature flags. UX artifacts from `ux-designer` (flows, wireframes, states) are inputs.

## Testing strategy
- **Unit:** pure logic, hooks/utilities, form validation.
- **Component:** components render and behave per state (empty, loading, error, success).
- **Integration:** pages against a mocked or test API (e.g. MSW).
- **E2E:** the critical user journeys in a real browser (Playwright), run in CI.
- **Accessibility:** automated checks (axe) in component/E2E tests plus keyboard-only flow.

## Walking skeleton (task 1)
App builds and deploys a home page + one routed page + a call to `/health` (or mock), lint/type-check/unit/E2E smoke green in CI.

## CI & tooling
Lint, type-check, unit/component tests, Playwright E2E job (browsers installed), build, bundle-size budget, dependency audit, optional preview deployments.

## Where it runs (simple defaults)
Static hosting + CDN (Vercel/Netlify/Cloudflare Pages) for a single-page app; a managed platform if it renders on the server.

## Security focus
XSS (escaping, no `dangerouslySetInnerHTML` with user data), CSRF, content security policy, secure cookie flags, token storage, open redirects, third-party scripts, secrets never in the client bundle, dependency CVEs.

## Release checklist additions
Accessibility check done; works in target browsers; no console errors; bundle-size budget met; error tracking wired; analytics/consent compliant; cache-busting and CDN invalidation; feature flag for risky UI; rollback = redeploy previous build.

## Common pitfalls (reviewers look for these)
Missing loading/empty/error states; keyboard traps and missing labels; fetching in render loops; client-side-only authorization; storing tokens in localStorage without thought; giant bundles; tests that assert implementation details; flaky E2E waits.

## Suggested stacks
TypeScript + React (Vite or Next.js) · Vue/Nuxt · SvelteKit. Language flag: `node`.
