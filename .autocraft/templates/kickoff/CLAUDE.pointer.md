# This project is built with an autocraft agent team

**Owner's source documents (authoritative inputs):** {SOURCES_LIST}
**Framework:** `sak-io/autocraft` on GitHub (a public repository; no special access is needed to clone it).

## Kickoff (the team is not installed in this repo yet)
When the owner asks you to kick off / start / build this project:

1. **Get autocraft into this session.** If `/tmp/autocraft` does not exist, clone it OUTSIDE this repo: `git clone https://github.com/sak-io/autocraft /tmp/autocraft`. If the clone is blocked by the session's network settings, make sure `sak-io/autocraft` is in scope (the owner may have selected it when starting the session; otherwise use the `add_repo` tool for it) and try again. If it still fails, or the clone has no `bin/autocraft`, tell the owner exactly which step failed and stop.
2. **Act as the `team-builder`.** Read `/tmp/autocraft/roles/team-builder.md` and `/tmp/autocraft/workflows/kickoff.md` and follow them (section "Pointer-driven kickoff"). Read the source documents yourself, run `python3 /tmp/autocraft/bin/autocraft recommend --file <each source document>` as a first guess only, explain the lean and full options in plain words (including contradictions or risks you noticed in the documents), recommend one, and ask the owner at most 4 short questions.
3. **Install after the owner confirms**, mapping the answers to flags: `python3 /tmp/autocraft/bin/autocraft init . --tool claude-code --sources {SOURCES_ARG} --git-mode session-branch --team <team> --add-role <extra roles> --archetype <types> --capability <needs> --experience <beginner|intermediate|expert> --autonomy <1|2|3> --owner <owner's GitHub handle>`. Notes: `--tool claude-code` is correct for Claude app Code sessions too. For a project that arrives with a technical plan, "lean" means `--team starter --add-role architect` (add `qa-engineer` when personal data or access control is involved). Do NOT pass `--lang` yet: CI and GitHub hardening are added later, after the plan is approved.
4. **Commit** the result on the session branch (`git add -A && git commit`), then `git push -u origin HEAD` (if there is no remote, just commit). Tell the owner in plain words what was added: the team (`.claude/agents`, commands in `.claude/skills`), a one-time copy of the framework in `.autocraft/framework` (so later sessions do not need to fetch autocraft again), and `.claude/settings.json` which pre-approves routine safe commands (git, tests, the autocraft tool).
5. **Continue with the `kickoff` workflow** (now installed in `.claude/skills/`): ingest the source documents into a spec, validate the technical plan, get the owner's approval, then build with reviewers, QA and a security check. This pointer is replaced by the full team guide as soon as `autocraft init` runs.

Never paste or commit secrets. Nothing reaches `main` until the owner merges the pull request.
