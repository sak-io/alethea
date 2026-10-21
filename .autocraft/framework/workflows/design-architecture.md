---
id: design-architecture
needs: [architect, principal-engineer]
description: Runs the architect and principal-engineer in a review loop until the architecture is approved (max 3 rounds, then escalate to the human). Produces architecture doc, API contract, threat model, ADRs and a work breakdown.
---


# /design-architecture – architect ⇄ principal-engineer loop

Precondition: a spec exists in `docs/specs/`. If not, run the `requirements-analyst` first.

For UI archetypes (`web-app`, `mobile-app`) run `ux-designer` first if `docs/design/` is missing, and again whenever a review finding asks for UX changes.

Subagents cannot call each other, so **you** (the main session) run the loop:

```
round = 1
loop:
  1. Invoke `architect` with: spec path(s), round number, and (if round > 1) the latest review file.
  2. Invoke `principal-engineer` with: the architect's changed files, round number, and prior reviews.
  3. Read docs/architecture/reviews/review-<round>.md
       APPROVED            -> exit loop
       ESCALATE TO HUMAN   -> exit loop, go to Escalation
       CHANGES REQUESTED   -> round += 1; if round > max_review_rounds (config, default 3) go to Escalation
```

Between rounds, show the human a **one-paragraph progress note** (what the reviewer pushed back on, what changed). Do not paste whole documents.

## On approval
- Confirm `docs/architecture/status.md` says `APPROVED`.
- Summarise for the human in plain language: what will be built, main technology choices and *why*, the biggest risks, and how many tasks are in the work breakdown.
- List any `HUMAN DECISION` questions still open and ask them.
- Autonomy L1/L2: **ask the human to approve the architecture** before continuing. L3: proceed, but still report.

## Escalation
Present both sides (architect's position, principal's position) neutrally with a recommendation and what each choice costs. Ask the human to decide, record the decision as an ADR, then resume the loop from the next round.
