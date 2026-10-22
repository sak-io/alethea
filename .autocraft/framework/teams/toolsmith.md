---
id: toolsmith
name: Toolsmith Team
summary: For libraries, SDKs, command-line tools, plugins/extensions and desktop apps: public-interface stability, compatibility matrices, packaging.
fits: [library-cli, plugin-extension, desktop-app]
roles: [requirements-analyst, architect, principal-engineer, developer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, technical-writer]
optional: [frontend-developer, ux-designer, devops-engineer, delivery-lead]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status]
---

# Playbook: Toolsmith Team

**Spec (the public interface IS the product) → Architecture → Build → Docs → Compatibility check → You publish.**

1. Keep the public surface tiny; `principal-engineer` challenges every exported name.
2. `developer` builds with a compatibility matrix in CI; `qa-engineer` adds public-API snapshot tests and CLI subprocess tests.
3. `technical-writer` ensures the README example runs. `release-gatekeeper` checks semver, changelog, package contents. **Publishing is a human step.**
