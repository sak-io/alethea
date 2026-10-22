---
id: web-product
name: Web Product Team
summary: Full team for websites and web apps with accounts and data: design, architecture, front end and back end, QA, review, release.
fits: [static-site, web-app, fullstack-app, internal-tool]
roles: [requirements-analyst, ux-designer, architect, principal-engineer, frontend-developer, backend-developer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [repo-onboarder, devops-engineer, technical-writer, developer]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status, onboard-repo]
---

# Playbook: Web Product Team

**Spec → UX design → Architecture (architect ⇄ principal) → Build (front/back end ⇄ reviewer, QA alongside) → Release gate → You merge.**

1. `requirements-analyst` → spec with acceptance criteria. `ux-designer` → flows, wireframes, every UI state, accessibility.
2. `architect` ⇄ `principal-engineer` (≤3 rounds) → architecture, API contract, threat model, task list.
3. Contract task first; then `backend-developer` and `frontend-developer` work **in parallel** on independent tasks.
4. `qa-engineer` turns acceptance criteria into automated tests and explores the running build.
5. `pr-reviewer` reviews every PR; `security-auditor` weekly and pre-release; `release-gatekeeper` runs the final gap check; `delivery-lead` keeps the board.
6. You approve the design and merge each release-ready PR.
