---
id: build-my-team
description: Sets up the right team of AI agents for the software you want to build, in the format of the AI tool you use (Claude Code, Claude app, Cursor, Copilot, Gemini CLI, Windsurf, Cline, Codex/AGENTS.md, or plain prompts). Interviews you, recommends a lean and a full team, installs and explains it.
---

# /build-my-team – "set up agents for me"

Delegate to the `team-builder` role. Assume the person has never configured agents.

1. **Goal:** ask for the idea in their words → run `discover` (archetypes + capabilities).
2. **Setting:** which AI tool(s)? GitHub or not? experience level? how often to be asked (autonomy 1–3)?
3. **Recommend:** `autocraft recommend "<idea>"`, then present **Lean** (`starter`) vs **Full** (the matching team) in plain language, one line per role, plus what to skip at first. Recommend one.
4. **Install** on confirmation:
   `autocraft init . --tool <tools> --team <team> --archetype <a,b> --capability <c,d> --experience <level>`
   (`--dry-run` first if they want to see the file list).
5. **Orient:** tell them exactly how to start in their tool (e.g. "type `/start`", "open Copilot chat and choose the `architect` agent", "say: *Act as the requirements-analyst from AGENTS.md*"). Point to `docs/tools/<tool>.md`.
6. **Offer customization** (`customize-team`) and `/status`.
7. Run `autocraft doctor` and report anything left for the human.
