---
id: mobile
name: Mobile App Team
summary: For iOS/Android apps (and their backend): UX, mobile and back-end developers, QA with device checklists, and a release gate that understands app stores.
fits: [mobile-app]
roles: [requirements-analyst, ux-designer, architect, principal-engineer, mobile-developer, backend-developer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [devops-engineer, technical-writer, repo-onboarder]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status, onboard-repo]
---

# Playbook: Mobile App Team

**Spec → UX → Architecture → Contract → Build (mobile ∥ backend) → Device QA → Release gate → You submit to the stores.**

1. `ux-designer` designs for thumbs, interruptions, offline and permission-denied states.
2. `architect` decides native vs cross-platform (ADR), offline sync, secure storage, **force-update and old-version compatibility**.
3. API contract first; `mobile-developer` and `backend-developer` proceed in parallel against it (mock server for the app).
4. `qa-engineer` maintains the **device/OS test matrix** and the manual checklist.
5. `release-gatekeeper` adds store-review items, staged rollout and a rollback story (halt rollout + ship a fix). **Store submission is a human step.**
