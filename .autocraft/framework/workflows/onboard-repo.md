---
id: onboard-repo
needs: [repo-onboarder]
description: Creates the project's GitHub repository (private by default), installs the framework, configures CI/lint/test, SCA + SAST + secret scanning, labels, templates and main-branch protection, then verifies each control and writes an onboarding report.
---


# /onboard-repo

Precondition: architecture status is `APPROVED`. Needs the GitHub CLI authenticated (`gh auth status`) — if not, tell the human exactly how to log in (`gh auth login`) and wait.

1. Ask the human (with defaults): owner/org and repo name, visibility (**private**), language (from the architecture; `node`, `python`, `go`).
2. Run a **dry run** and show the human a short plain-language list of what will happen:
   `bash .autocraft/framework/scripts/onboard-repo.sh <owner/name> --lang <lang> --dry-run`
   (Run from the project folder; that folder becomes the repository.)
3. On confirmation, run it for real. This is an outward-facing action — it creates a repo under the human's account — so never skip the confirmation.
4. Invoke `repo-onboarder` to adapt CI to the real stack, add the walking-skeleton test, **verify every control by reading it back**, and write `docs/onboarding-report.md`.
5. Report to the human: a ✅/⚠️/❌ table. For each ⚠️/❌ explain in plain language what it means and what to do (e.g. "branch protection on private repos needs GitHub Pro/Team").
6. Do not proceed to `/build-feature` while any ❌ remains unless the human explicitly accepts the risk.
