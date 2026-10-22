# Archetype: mobile-app

## Summary
An app installed on phones/tablets (iOS, Android or both). Releases go through app stores, so mistakes are slow and costly to undo.

## In plain words
"I want an app people install from the App Store / Google Play." Once released you cannot take it back from phones, so testing and careful rollout matter more than on the web.

## Typical products
Consumer apps, companion apps for a service, internal field-worker apps.

## Questions the requirements-analyst must ask
1. iOS, Android, or both? (default: both, one codebase)
2. Must it work offline or on poor networks? (default: graceful degradation + local cache)
3. Device features needed: camera, location, push notifications, biometrics, payments? (default: push only)
4. Which minimum OS versions? (default: iOS 16+/Android 10+)
5. Has a backend API? Who owns it? (default: a separate `api-service` component; ask whether it exists yet)
6. Store accounts ready (Apple Developer, Google Play)? (default: no — plan for it)

## Architecture must cover
Cross-platform vs native choice (with ADR), navigation, state management, local storage/offline sync and conflict handling, API client with retry/backoff, auth and secure token storage (Keychain/Keystore), push notifications, permissions flow, deep links, crash/analytics reporting, **app versioning and force-update strategy** (old versions stay in the wild), feature flags/remote config, build flavors and signing, store release process. UX artifacts from `ux-designer` are inputs.

## Testing strategy
- **Unit:** business logic, view-models/state, serialization.
- **Widget/component:** screens in each state (loading, empty, error, offline).
- **Integration:** against a mock/test API; storage and sync logic.
- **UI/E2E on emulator or device:** critical flows (sign-in, core task) — run in CI where feasible, otherwise on a documented manual device checklist.
- **Manual device matrix:** small set of real devices/OS versions before each store submission.

## Walking skeleton (task 1)
App launches to a home screen, navigates to a second screen, calls the API health/mocked endpoint, CI builds a debug artifact and runs unit/widget tests.

## CI & tooling
Lint/analyze, unit + widget tests, debug build for each platform (iOS builds need macOS runners), dependency audit, signing handled via CI secrets (never in repo), optional beta distribution (TestFlight/Play internal track).

## Where it runs (simple defaults)
On users' phones, distributed through the stores. Needs developer accounts (Apple ≈ $99/yr, Google ≈ $25 once). Any server it talks to is a separate `api-service`.

## Security focus
Secure storage for tokens, certificate pinning where warranted, no secrets in the app binary (assume it will be unpacked), permission minimization, deep-link validation, insecure local data, jailbreak/root sensitivity for payments, privacy disclosures (data collected).

## Release checklist additions
Version/build number bumped; release notes; **store review requirements** (privacy labels, permission strings, screenshots, age rating); tested on OS-version/device matrix; crash-free baseline known; **staged rollout** plan (e.g. 5% → 25% → 100%); backend compatible with **older app versions still in use**; kill-switch / force-update path; rollback = halt rollout + ship a fixed build (you cannot recall installed apps). Submission to stores is a **human step**.

## Common pitfalls (reviewers look for these)
Blocking the UI thread; no handling of offline/slow/failed network; storing tokens in plain preferences; assuming the latest OS; ignoring permission denial; unbounded memory/image use; breaking changes to the API that strand old app versions; hard-coded endpoints/keys.

## Suggested stacks
React Native/Expo (TypeScript) → language flag `node` · Flutter → `flutter` · native Swift/Kotlin (needs platform CI runners; tell the onboarder).
