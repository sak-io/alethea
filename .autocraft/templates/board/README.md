# work/ - the agent-agnostic task board

- `tasks/<ID>.md` - task card: front matter (`needs`, `soft`, `tier`, `verify`) + a self-contained brief.
- `status/<ID>.json` - the only thing agents change: `state` (not_started | claimed | in_review | merged | blocked), `owner`, `claimed_at`, `heartbeat_at`, `lease_hours`, `branch`, `commit`, `wip_branch`, `note`. One file per task, so claims never merge-conflict.
- `BOARD.md` - generated (`python3 scripts/board.py render`), validated by `board.py check`.
- `config.json` - integration branch, lease length.
- `CONTEXT.md` - short repo map; `LESSONS.md` - mistakes already paid for.

A claim is a git push of the status file to the integration branch: the first push wins. Dependencies that are `in_review` make a task "ready on batch" (`claim --on-batch`).
