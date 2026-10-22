---
id: makers
name: Makers Team (games & devices)
summary: For games and embedded/IoT devices: testable logic separated from hardware or rendering, performance budgets, safe updates.
fits: [game, embedded-iot]
roles: [requirements-analyst, architect, principal-engineer, game-developer, embedded-engineer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper]
optional: [backend-developer, devops-engineer, ux-designer, delivery-lead]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status]
---

# Playbook: Makers Team

**Concept (core loop / device behaviour) → Architecture → Tiny playable/bootable version → Iterate → Soak & playtest → Release.**

1. Use only the builder role your product needs (`game-developer` or `embedded-engineer`; remove the other with `--remove-role`).
2. Get a tiny end-to-end version running in days, then iterate with reviewer and QA.
3. `release-gatekeeper` demands performance/memory budgets, OTA-with-rollback (devices), save-migration (games), and a **human safety sign-off** where harm is possible.
