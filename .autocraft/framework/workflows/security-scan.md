---
id: security-scan
needs: [security-auditor]
description: Runs the security-auditor to collect and triage SCA, SAST and secret-scan results, write an audit report, and file prioritized issues. Run weekly, before releases, and after dependency changes.
---


# /security-scan

1. Invoke `security-auditor`.
2. Present the human with a short summary: counts by severity, top 3 things to fix and why they matter, anything urgent (e.g. leaked secret → rotate now).
3. Offer to start `/build-feature` on the created security issues (fix PRs go through the normal review loop).
4. Suggest scheduling it: the `security-scheduled` GitHub workflow already runs weekly scans; this skill triages the results.
