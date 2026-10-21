---
id: customize-team
description: Changes an installed agent team - add or remove roles, switch teams, tune strictness, add house rules and standards, create a brand-new role, add another AI tool - and regenerates all tool files safely.
---

# /customize-team

Everything is plain files; **never edit generated files by hand** (they are overwritten on sync). Customize in these places, then run `autocraft sync`:

| To… | Do this |
|---|---|
| Add / remove a role | `autocraft add-role <id>` / `autocraft remove-role <id>` (edits `.autocraft/config.yml`) |
| Switch or combine teams | set `team:` in config, e.g. `team: [mobile, ai-product]` |
| Add another AI tool | `autocraft add-tool cursor` (or edit `tools:`) |
| Change strictness / speed | `autonomy_level`, `max_review_rounds`, `max_parallel_developers` in config |
| Add house rules (style, frameworks, company standards) to one role | create `.autocraft/custom/roles/<id>.append.md` — its text is appended to that role |
| Change a role completely | copy `.autocraft/framework/roles/<id>.md` to `.autocraft/custom/roles/<id>.md` and edit |
| Add a new role | `autocraft new-role <id>`, fill in the template, add it with `add-role` |
| Rules for **all** roles | `.autocraft/custom/team-rules.md` (appended to every role and the team guide) |

Procedure: ask what they want to change and why → make the smallest change → `autocraft sync` → `autocraft doctor` → explain what changed and its effect in plain language. **Never remove independent review or the human merge gate**; if asked, explain the risk and suggest a lighter alternative (fewer roles, higher autonomy for low-risk tasks).
