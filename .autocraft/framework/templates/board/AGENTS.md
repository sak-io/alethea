# AGENTS.md - start here (any AI system or human)

This project's task state is **files in git**, so every tool (Claude app/CLI, ChatGPT, Codex, Cursor, Grok, humans) sees the same truth.

1. Read `work/BOARD.md` (live status) and `work/CONTEXT.md` (repo map, how to verify).
2. `python3 scripts/board.py next` lists the **only** tasks you may take (`not_started`, dependencies met). Never touch `claimed`, `in_review`, `merged` or `blocked` tasks.
3. `python3 scripts/board.py claim <ID> --agent <your-name>` commits and pushes the claim; if the push loses the race, pick another. No shell? Edit `work/status/<ID>.json` (`state: "claimed"`, `owner`, `claimed_at`, `heartbeat_at`) and push it.
4. Read `work/tasks/<ID>.md` (self-contained brief) and `work/LESSONS.md`. Read only what the card names.
5. Work on a task branch, push early (CI is the verifier). When done: `board.py submit <ID> --agent <name> --branch <branch>`. A different agent or human reviews (tier S = full review, tier N = one light pass). Only the integrator runs `board.py merge <ID>`, after CI is green.
6. Out of budget? `board.py release <ID> --wip-branch <branch> --note "what is left"`. Claims expire after `lease_hours` without `board.py heartbeat`.

Rules: nothing reaches the default branch until the owner merges; never commit secrets; never skip a test or check to get green; never edit `work/BOARD.md` by hand (generated).
