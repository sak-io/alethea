---
id: starter
name: Starter Team (lean)
summary: The smallest team that is still safe: a planner, a builder and an independent reviewer, plus a security check. Start here when unsure.
fits: [static-site, script-automation, library-cli, web-app, fullstack-app, api-service]
roles: [requirements-analyst, developer, pr-reviewer, security-auditor]
optional: [architect, qa-engineer, technical-writer, ux-designer]
workflows: [discover, explain, start, build-feature, review-pr, security-scan, status]
---

# Playbook: Starter Team

**Idea → Spec → Build → Review → You merge.**

1. `requirements-analyst` writes a short spec with acceptance criteria (half a page for small things).
2. `developer` builds one small task at a time on a branch, with tests.
3. `pr-reviewer` reviews in a **fresh context** — never the same session that wrote the code — and the two iterate (max 3 rounds, then ask you).
4. `security-auditor` runs before you ship anything real, and weekly after.
5. You review the summary and merge.

**Why this works:** even this tiny team gets the biggest benefit of multi-agent work — a builder who is not the judge of their own work. Grow into a bigger team when the project grows (`autocraft init . --team web-product`).
