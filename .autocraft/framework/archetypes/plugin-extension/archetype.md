# Archetype: plugin-extension

## Summary
Code that extends someone else's platform: browser extensions, IDE plugins, WordPress/Shopify/Salesforce apps, Slack/Notion integrations, GitHub Actions.

## In plain words
"I want to add a feature to a tool people already use." The host platform sets the rules — what you may do, how you are reviewed, and how you are updated.

## Typical products
Chrome/Firefox extensions, VS Code extensions, Shopify apps, WordPress plugins, GitHub Actions.

## Questions the requirements-analyst must ask
1. Which platform(s) and which versions? (default: ask)
2. What permissions/data access will it need? (default: the minimum)
3. Where is it published and who reviews it (store review)? (default: the platform's marketplace)
4. Does it need a backend of its own? (default: no — add an `api-service` component only if needed)
5. Free or paid, and how are updates delivered? (default: free, marketplace updates)

## Architecture must cover
Platform APIs and their limits/deprecations, manifest and **permission minimization**, isolation between the extension and host, storage and sync, handling platform version changes, any backend and auth (OAuth with the platform), packaging and marketplace requirements, uninstall/cleanup, privacy disclosures.

## Testing strategy
- **Unit:** logic separated from platform APIs (wrap them behind interfaces).
- **Integration:** against the platform's test harness/emulator/sandbox store.
- **E2E:** load the packaged extension in a real host instance (e.g. Playwright with extension loaded, VS Code test runner).
- **Compatibility:** supported host versions matrix.

## Walking skeleton (task 1)
Extension installs into the host in a test environment, shows one UI element, performs one action, and CI packages a valid artifact.

## CI & tooling
Lint, type-check, tests in the host harness, manifest validation, package build, dependency audit.

## Where it runs (simple defaults)
Inside the host platform; distributed via its marketplace. A tiny backend (if any) on a managed serverless platform.

## Security focus
Least-privilege permissions, content-script isolation, handling untrusted page/host data, OAuth tokens storage, no remote code execution, supply-chain risk (a compromised update hits all users), privacy policy.

## Release checklist additions
Permissions justified; marketplace listing/privacy info ready; tested on supported host versions; versioned package inspected; rollback = publish a fixed version (review delays apply). Marketplace submission is a **human step**.

## Common pitfalls (reviewers look for these)
Requesting broad permissions "just in case"; breaking when the host updates; injecting unsafe HTML into pages; storing tokens insecurely; heavy background work slowing the host; no uninstall cleanup.

## Suggested stacks
TypeScript → `node` (browser/VS Code) · PHP (WordPress; add CI template) · Docker/TypeScript (GitHub Actions).
