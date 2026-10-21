---
id: api-backend
name: API & Backend Team
summary: For APIs, event processors, scheduled jobs, realtime services and automation: strong on contracts, data, idempotency and operations.
fits: [api-service, event-processor, scheduled-job, realtime-service, script-automation]
roles: [requirements-analyst, architect, principal-engineer, backend-developer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [devops-engineer, repo-onboarder, technical-writer]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status, onboard-repo]
---

# Playbook: API & Backend Team

**Spec → Architecture (architect ⇄ principal) → Build (backend ⇄ reviewer, QA) → Release gate → You merge.**

1. Spec focuses on consumers, data sensitivity, load, failure behaviour ("what if it runs twice / fails halfway?").
2. Architecture produces the **contract first** (OpenAPI / event schema), threat model (authorization!), idempotency and retry design.
3. `backend-developer` builds the walking skeleton, then one endpoint/handler per PR with integration tests on a real database/queue.
4. `qa-engineer` adds contract, failure and concurrency tests. `pr-reviewer` checks authorization, idempotency, migrations.
5. `release-gatekeeper` checks migrations, config, alerts (incl. missed runs for jobs), rollback.
6. You merge; `security-auditor` keeps scanning.
