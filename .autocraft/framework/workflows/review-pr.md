---
id: review-pr
needs: [pr-reviewer]
description: Reviews a single pull request (new or updated) with the pr-reviewer agent. Use for PRs created outside the build loop, by humans, or when new commits land on a reviewed branch.
---


# /review-pr <number>

1. Resolve the PR number (if omitted: `gh pr list --label agent:needs-review`).
2. Invoke `pr-reviewer` on it. If the PR was reviewed before, tell the reviewer to **verify each previous comment** against the new commits.
3. If `CHANGES REQUESTED` and the PR is agent-authored, offer to invoke `developer` to address the comments (continue as in `/build-feature` step 3). If human-authored, just report.
4. Summarise for the human: verdict, blockers/majors count, and what happens next.
