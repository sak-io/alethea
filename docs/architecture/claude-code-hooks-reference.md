# Claude Code integration reference (fetched 2026-10-21)

Source: official Claude Code docs (code.claude.com: hooks reference, MCP, memory, Claude Code on the web), summarised by a research agent. Items marked **not documented** were not found in the pages it read. Several details are version-gated; re-check against the installed version before relying on them. This file is data for the T22/T23/T24 builders, not a spec of Alethea behaviour.

## Hook configuration (`.claude/settings.json`)
```json
{"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": [
  {"type": "command", "command": "${CLAUDE_PROJECT_DIR}/path/to/hook", "args": [], "timeout": 5}]}]}}
```
- Structure: event -> matcher group (`"matcher"`, omitted/`""`/`"*"` = all) -> `"hooks"` array of handlers. Handler `type` `command`: `command`, optional `args` (exec form, recommended with path placeholders), `timeout` in **seconds**.
- `${CLAUDE_PROJECT_DIR}` = project root where the session started (also an env var). Multiple matching hooks run **in parallel**; identical handlers across settings files run once.
- Default timeouts: command 600 s; `UserPromptSubmit` 30 s; **`SessionEnd` 1.5 s**.

## Per-event stdin (JSON) and effects
Common fields: `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `prompt_id`.

| Event | Extra stdin fields | Output / effect |
| :---- | :---- | :---- |
| SessionStart | `source` ("startup", "resume", "clear", "compact", "fork"), optional `model`, `session_title` | plain stdout on exit 0 is added to context; or `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}`. Cannot block. Each of plain stdout / additionalContext / systemMessage capped at **10,000 chars** (overflow saved to a file with a 2,000-char preview). |
| UserPromptSubmit | `prompt` | **plain stdout on exit 0 is added to context** (so a journaling hook must print nothing). Exit 2 blocks the prompt. JSON: `decision: "block"`, `hookSpecificOutput.additionalContext`. |
| Stop | `stop_hook_active`, **`last_assistant_message`** (text of Claude's final response), `background_tasks`, `session_crons` | `decision: "block"` + `reason` would force continuation (never use it). Claude never sees stderr from exit 0. |
| SessionEnd | `reason` | cannot block; JSON output discarded; short timeout. |
| PreCompact | `trigger` ("manual" or "auto"), `custom_instructions` | exit 2 or `decision: "block"` blocks compaction (never use it). |

Universal JSON output fields: `continue`, `stopReason`, `suppressOutput`, `systemMessage`.

## Exit codes
0 = success (JSON applied; stdout added to context only for UserPromptSubmit and SessionStart). **2 = blocking error** on blocking events (stderr fed back). Any other non-zero = non-blocking error (transcript shows a hook error). A timeout cancels the hook and discards its output; a crash or missing script is non-blocking. By default a failing hook does **not** block the session (`onFailure: "block"` is opt-in, v2.1.295+). Alethea hooks must always exit 0 and must never exit 2.

## Transcript
`transcript_path` is a JSONL conversation file; the line shape is **not documented** in what was read. Alethea must not depend on it: use `last_assistant_message` from the Stop payload, and treat transcript reading as optional best effort (any parse error => ignore).

## MCP (`.mcp.json`, project scope)
`{"mcpServers": {"<name>": {"command": "...", "args": [...], "env": {...}}}}` for stdio; `${VAR}` and `${VAR:-default}` expand in command, args, env. Interactive sessions ask for approval before using project-scoped servers (reset: `claude mcp reset-project-choices`); `-p`, Agent SDK and cloud sessions load them without asking.

## CLAUDE.md imports
`@path/to/file` anywhere in CLAUDE.md. Relative paths resolve from the containing file's directory; absolute and `~/` allowed; max 4 hops; not parsed inside code spans or fenced blocks; imports from outside the working directory trigger a one-time approval dialog in project-level files.

## Cloud sessions
SessionStart hooks run in cloud sessions and the repo's `.claude/settings.json` is read; project `.mcp.json` servers load without a prompt. Whether every other hook event runs in cloud sessions is **not documented** in the pages read (verify under "Settings in cloud sessions").
