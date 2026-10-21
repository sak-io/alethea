# Archetype: infrastructure

## Summary
Code that defines and manages cloud infrastructure: networks, databases, servers, permissions, deployment pipelines (Infrastructure as Code).

## In plain words
"I want the places where my software runs set up reliably and repeatably." One bad change can delete a database or open it to the internet, so every change is **planned, reviewed and reversible**.

## Typical products
Terraform/OpenTofu/Pulumi/CloudFormation stacks, Kubernetes manifests/Helm charts, deployment pipelines, environment setup.

## Questions the requirements-analyst must ask
1. Which cloud(s) and which accounts/environments (dev, staging, prod)? (default: one cloud; dev + prod)
2. What must run there (link to the app archetypes)? (default: ask)
3. Budget ceiling per month? (default: ask; set alerts)
4. Availability and data-protection needs (backups, regions)? (default: single region, daily backups)
5. Who may change production? (default: only via reviewed PR, applied by CI)

## Architecture must cover
Environment layout and isolation, state storage and locking, module structure, networking (private by default), identity and least-privilege roles, secrets management, backups/DR, tagging/cost allocation, **plan → review → apply** workflow with approvals, drift detection, policy-as-code, observability baseline, destroy protection for stateful resources.

## Testing strategy
- **Static:** format, validate, lint (tflint), security scan (checkov/tfsec/trivy) — all in CI.
- **Plan tests:** `plan` output reviewed on every PR; assertions that no stateful resource is destroyed unintentionally.
- **Module tests:** apply/destroy modules in a throwaway account (Terratest-style) on a schedule.
- **Policy tests:** forbidden configurations (public buckets, open security groups) fail.

## Walking skeleton (task 1)
Remote state with locking, one small resource in a dev environment, CI that runs fmt/validate/lint/scan and posts the plan on PRs, apply only on merge with approval.

## CI & tooling
Terraform fmt/validate, tflint, checkov, plan on PR, gated apply, cost estimate (infracost-style).

## Where it runs (simple defaults)
It *is* the platform. Prefer managed services and a single cloud; start with one small environment.

## Security focus
Least-privilege IAM, no public data stores, encryption at rest/in transit, secrets never in state or code, protecting the state file, CI credentials (short-lived/OIDC), audit logging.

## Release checklist additions
Plan reviewed line by line (no unexpected destroys); blast radius and rollback understood; cost impact estimated; backups verified; change window for risky changes; **applying to production is a human-approved step**.

## Common pitfalls (reviewers look for these)
Hand-edited cloud resources (drift); secrets in state; wildcard IAM; no state locking; one giant stack; destroy-able databases; unreviewed plans; no cost alerts.

## Suggested stacks
Terraform/OpenTofu → `terraform` · Pulumi (TypeScript/Python) → `node | python`.
