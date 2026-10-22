# Archetype: library-cli

## Summary
Reusable code other developers install (a library/SDK) or a command-line tool people run. The public interface is the product.

## In plain words
"I want to share reusable code or a command other developers can install." Its users are developers; your interface is the product, and changing it later can break their work.

## Typical products
Open-source packages, internal SDKs, CLIs, developer tools, GitHub Actions.

## Questions the requirements-analyst must ask
1. Who uses it (developers of which language/platform)? (default: ask)
2. What is the smallest public interface that solves the problem? (default: keep tiny; expand later)
3. Where is it published (npm, PyPI, Go modules, internal registry, GitHub releases)? (default: private registry or GitHub releases)
4. Supported language/runtime versions and operating systems? (default: current + previous major)
5. Stability promise (semantic versioning)? (default: yes)

## Architecture must cover
Public API surface (and what is deliberately private), semantic versioning and deprecation policy, error model, configuration, dependency minimization, cross-platform behaviour, CLI UX (flags, exit codes, help, stdout vs stderr, machine-readable output), documentation and examples, packaging and publish pipeline, supply-chain integrity (provenance, signed releases).

## Testing strategy
- **Unit:** every public function incl. edge cases.
- **Integration:** real filesystem/network where relevant; CLI invoked as a subprocess checking output and exit codes.
- **Compatibility matrix:** supported runtime versions/OS in CI.
- **Public API/snapshot tests:** detect accidental breaking changes.
- **Doc tests:** examples in the README actually run.

## Walking skeleton (task 1)
Package builds, exports one function (or CLI `--help`/`--version`), has tests across the version matrix, and a dry-run of the publish step.

## CI & tooling
Lint, type-check, test matrix, build, API-diff check, dependency audit, dry-run publish, changelog check.

## Where it runs (simple defaults)
Published to a package registry (npm, PyPI, crates.io, GitHub Releases) and installed by users; no server to run.

## Security focus
Dependency footprint and typosquatting, publish credentials (trusted publishing / provenance), safe defaults, no telemetry without consent, command/path injection in CLIs, secrets in logs or error output.

## Release checklist additions
Semver bump matches the change (breaking = major); changelog and migration notes; docs/examples updated; matrix green; package contents inspected (no stray files/secrets); publish is reversible only via a new version — plan accordingly; tag + release notes. Publishing is a **human step**.

## Common pitfalls (reviewers look for these)
Accidentally widening the public API; breaking changes in a minor release; heavy transitive dependencies; unclear errors; global state; tests tied to one OS; CLI that prints human text where scripts need JSON; non-zero exit codes not used.

## Suggested stacks
TypeScript (`node`) · Python (`python`) · Go (`go`).
