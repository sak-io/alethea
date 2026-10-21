# Pre-release check – PR #<n>

| # | Item | Result | Evidence / note |
|---|---|---|---|
| 1 | Scope matches task; no debug code/secrets/untracked TODOs | | |
| 2 | Required CI checks green on head commit; integration tests ran | | |
| 3 | SCA/SAST/secret scans clean or consciously accepted | | |
| 4 | Migrations backward compatible & reversible | | |
| 5 | New config/env vars/secrets documented & provisioned | | |
| 6 | API/behaviour compatibility; breaking changes called out | | |
| 7 | Logs/metrics/alerts for new failure modes; rollback plan written | | |
| 8 | Docs, ADRs, CHANGELOG updated | | |
| 9 | Version bump / squash message ready | | |

**Risk:** low / medium / high – because …
**Rollback:** …
**Verdict:** RELEASE-READY | BLOCKED (see [RELEASE-BLOCKER] items)
