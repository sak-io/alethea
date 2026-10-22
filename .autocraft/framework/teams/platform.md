---
id: platform
name: Platform & Infrastructure Team
summary: For cloud infrastructure as code, deployment pipelines and environments — careful, plan-first, human-approved applies.
fits: [infrastructure]
roles: [requirements-analyst, architect, principal-engineer, devops-engineer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [repo-onboarder, technical-writer]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status, onboard-repo]
---

# Playbook: Platform & Infrastructure Team

**Requirements (what must run where, budget) → Architecture → IaC PRs with plans → Review → Human-approved apply.**

1. `devops-engineer` writes IaC in small modules; every PR shows the **plan**; no unexpected destroys.
2. `security-auditor` scans IaC (IAM, public exposure, encryption); `pr-reviewer` reads the plan line by line.
3. `release-gatekeeper` checks blast radius, cost, backups/restore, rollback. **Applying to production is always a human-approved step.**
