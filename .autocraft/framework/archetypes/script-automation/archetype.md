# Archetype: script-automation

## Summary
Small programs that automate a personal or team task: renaming files, pulling data into a spreadsheet, scraping a page, sending a report, gluing two tools together.

## In plain words
"I do this boring thing by hand every week; I want the computer to do it." Small and quick, but scripts that touch real files or accounts can do damage fast — so they get a dry-run mode and a backup.

## Typical products
File organizers, report generators, data cleaners, API glue scripts, scrapers (where allowed), spreadsheet automations.

## Questions the requirements-analyst must ask
1. What do you do by hand today, step by step? (default: ask for the steps)
2. How often, and should it run on its own (then add `scheduled-job`)? (default: run on demand)
3. What does it read and change (files, accounts, spreadsheets)? Anything that must never be touched? (default: read-only first)
4. What does a mistake cost? Is there a backup? (default: dry-run and backup before changes)
5. Who else will run it? (default: just you — but write a README anyway)

## Architecture must cover
Inputs/outputs and configuration (no hard-coded paths/keys), **dry-run mode**, logging of what was done, idempotency (safe to re-run), error handling that stops before damage, credentials via environment/secret store, permissions of the account used, dependency pinning, simple CLI interface and README.

## Testing strategy
- **Unit:** parsing and transformation logic with sample inputs, including messy ones.
- **Integration:** run against a temporary folder/test account with fixtures; run twice → same result.
- **Safety:** dry-run makes no changes; failure leaves things consistent.

## Walking skeleton (task 1)
`--dry-run` prints what it would do on sample data; one real action works on a test folder/account; tests pass in CI; README shows how to run.

## CI & tooling
Lint, unit/integration on fixtures, dependency audit, secret scan.

## Where it runs (simple defaults)
Your computer, or a scheduled GitHub Action / small cloud function if it should run unattended (then also follow `scheduled-job`).

## Security focus
Credentials never in the script or repo, least-privilege tokens, careful with deletion/overwrite, scraping terms of service and rate limits, untrusted input files.

## Release checklist additions
Dry-run verified on real data; backup/undo path; README with setup + examples; secrets documented; failure alerts if unattended.

## Common pitfalls (reviewers look for these)
Deleting or overwriting without a backup; hard-coded paths and tokens; no dry-run; silent failures; unpinned dependencies; scripts that only work on one machine.

## Suggested stacks
Python → `python` · Node → `node` · shell for tiny tasks.
