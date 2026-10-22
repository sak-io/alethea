# Archetype: desktop-app

## Summary
Software installed and run on a user's computer (Windows, macOS, Linux): editors, utilities, creative tools, local-first apps.

## In plain words
"I want a program people download and run on their computer." You ship it to *them*, so updates, installers and working on many machine types are your problem.

## Typical products
Utilities, productivity apps, media tools, local-first note apps, companion apps for hardware.

## Questions the requirements-analyst must ask
1. Which operating systems? (default: Windows + macOS)
2. Does it need to work offline / store data locally? (default: yes, local files/DB)
3. Does it need special access (files, camera, USB, system tray)? (default: files only)
4. How do users get updates? (default: auto-update)
5. Free, paid, or enterprise-managed? (default: free/simple)

## Architecture must cover
UI toolkit choice (Electron/Tauri/.NET MAUI/Qt/native, with ADR), process model (UI vs background work), local storage and data migration between versions, **auto-update and signing**, OS integration and permissions, crash reporting, offline behavior, installer/packaging per OS, performance and memory, accessibility, telemetry with consent.

## Testing strategy
- **Unit:** core logic independent of the UI.
- **Integration:** storage, file handling, migrations from old data formats.
- **UI/E2E:** key flows with the toolkit's automation (Playwright for Electron, etc.).
- **Cross-OS matrix:** build and smoke-test on each supported OS in CI.
- **Upgrade tests:** install old version → upgrade → data intact.

## Walking skeleton (task 1)
App window opens, saves/loads one local file, packages an unsigned installer per OS in CI, smoke test launches it.

## CI & tooling
Lint, type-check, unit/integration, matrix build on Windows/macOS/Linux runners, packaging, dependency audit; signing keys in CI secrets.

## Where it runs (simple defaults)
On users' machines. Distribute via GitHub Releases or a store (Microsoft Store/Mac App Store); updates via the framework's updater.

## Security focus
Code signing and notarization, auto-update integrity, least privilege, safe handling of local files, Electron-specific hardening (context isolation, no remote code), secrets not in the binary, dependency CVEs.

## Release checklist additions
Signed and notarized builds; tested on each OS version; data migration from the previous release verified; update path tested; release notes; crash reporting on; rollback = pull the release and ship a fixed version (installed copies stay). Publishing is a **human step**.

## Common pitfalls (reviewers look for these)
Blocking the UI thread; path/permission assumptions per OS; no data migration; unsigned installers; insecure webview settings; unbounded memory; update mechanism that can be hijacked.

## Suggested stacks
Electron/Tauri + TypeScript → `node` (Tauri adds `rust`) · .NET → `dotnet`.
