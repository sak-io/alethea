---
id: lean-build
description: Token-frugal build loop driven by the agent-agnostic task board - claim only not_started tasks, batch CI, risk-tiered review, cheap models for review, resumable by any AI system.
---

# /lean-build [task id]

Use instead of `/build-feature` when usage limits matter or several different AI tools may work on the project. Setup once: `autocraft board init . --branch <integration-branch>`, write `work/tasks/<ID>.md` cards (or `python3 scripts/board.py import-tasks docs/architecture/work-breakdown.md`), `python3 scripts/board.py render`, commit. See `docs/lean-orchestration.md` for the reasoning.

1. **Pick.** `python3 scripts/board.py next`. Only tasks listed there may be taken. Claim with `board.py claim <ID> --agent <name>`; a lost race means pick another.
2. **Brief the builder from the card only** (`work/tasks/<ID>.md` + `work/LESSONS.md` + the files the card names). Do not hand the whole plan to the agent.
3. **Build** on `<prefix>-<id>`; push early. Several finished tasks may share one batch branch so one CI cycle verifies them all (`next --include-batch` / `claim --on-batch`).
4. **Submit.** `board.py submit <ID> --agent <name> --branch <b>` once pushed.
5. **Review by tier** (`tier:` in the card):
   - `S` (safety/security/money/data-loss path): full `pr-reviewer` loop (max rounds from config), fresh context, reviewer runs the tests.
   - `N`: one light review pass when CI is green and it finds no BLOCKER/MAJOR; MINOR/NIT go to a tracked-items file.
   Reviewers run on a cheaper model tier.
6. **Merge** (integrator/human only): `board.py merge <ID> --commit <sha>` after CI is green. Update `work/BOARD.md` (render) and push.
7. **Budget hit?** `board.py release <ID> --wip-branch <b> --note "..."`; the next agent, from any tool, resumes. Leases expire on their own.

Rules: never merge to the default branch yourself; never skip a test; at most the configured number of agents at once; CI is batched, not per micro-change.
