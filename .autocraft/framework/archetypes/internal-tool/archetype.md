# Archetype: internal-tool

## Summary
An app for your own team or business, not the public: admin panels, dashboards, back-office tools, approval workflows, data-entry apps.

## In plain words
"I want a screen where my team can see and change our data." Fewer users, but they handle real company data, so *who can do what* matters more than looks.

## Typical products
Admin consoles, ops dashboards, CRUD apps over a database, approval/request workflows, reporting screens.

## Questions the requirements-analyst must ask
1. Who will use it, how many, and what do they do today (spreadsheet, email)? (default: ask)
2. What data sources does it read or change? (default: one database)
3. Roles and permissions — who can view, edit, approve, delete? (default: viewer/editor/admin)
4. Does every change need to be logged (who did what, when)? (default: yes)
5. How do people sign in (company single sign-on or accounts)? (default: company SSO if available)

## Architecture must cover
Data access layer (read-only vs read-write; never expose raw database credentials to the browser), role-based access control, audit log, SSO/auth, forms and validation, bulk actions with confirmation and undo, export (CSV) with permission checks, pagination/filtering/search, error handling, hosting behind a VPN/SSO.

## Testing strategy
- **Unit:** permission rules, validation, calculations.
- **Integration:** every action as every role (allowed and forbidden); audit log written; queries against a real test database.
- **E2E:** the 2–3 daily workflows.
- **Safety tests:** destructive actions need confirmation and are reversible or logged.

## Walking skeleton (task 1)
Sign-in via SSO/placeholder, one read-only table from the data source with pagination, role check, audit-log entry on view, CI green.

## CI & tooling
Lint, type-check, unit + integration, E2E for key workflows, dependency audit, build.

## Where it runs (simple defaults)
Behind company SSO or a VPN: an internal platform, a small managed host with IP allow-listing, or a low-code-friendly framework (Retool-style alternatives are fine; document the choice in an ADR).

## Security focus
Least-privilege database user, role checks on the server, audit logging, protecting bulk export, SSO session handling, PII exposure to too-broad roles, no public exposure.

## Release checklist additions
Roles tested; audit log verified; backups of any data the tool writes; users trained / short how-to written; access list reviewed; rollback = redeploy + data-fix plan for mistaken bulk edits.

## Common pitfalls (reviewers look for these)
Everyone is admin; browser holds database credentials; no audit trail; delete without confirmation; export leaks data to the wrong role; unbounded queries timing out; no pagination.

## Suggested stacks
TypeScript + React/Next.js → `node` · Django admin/Flask → `python`.
