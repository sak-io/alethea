---
id: data-ml
name: Data & ML Team
summary: For pipelines, analytics and machine-learning models: data quality, reproducibility, evaluation and monitoring.
fits: [data-pipeline, ml-service]
roles: [requirements-analyst, architect, principal-engineer, data-engineer, ml-engineer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [backend-developer, devops-engineer, technical-writer]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status]
---

# Playbook: Data & ML Team

**Questions & baseline → Architecture → Pipeline → Model → Evaluate → Release → Monitor.**

1. Spec names the decisions the data will support, freshness needs, and privacy limits.
2. `data-engineer` builds idempotent, tested pipelines; `ml-engineer` trains against a baseline with leakage-safe splits and agreed metrics.
3. `qa-engineer` owns data tests and regression checks; `pr-reviewer` checks leakage, reproducibility, PII.
4. `release-gatekeeper` requires thresholds met, a model card, drift alerts, a fallback and rollback to the previous model.
