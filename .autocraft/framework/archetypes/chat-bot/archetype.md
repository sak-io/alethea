# Archetype: chat-bot

## Summary
A bot that lives inside a messaging platform (Slack, Discord, Teams, Telegram, WhatsApp) and responds to commands, messages or events.

## In plain words
"I want a helper that people can talk to inside Slack/Discord." It can be rule-based (commands) or AI-powered (add the `ai-assistant` archetype).

## Typical products
Slack workflow bots, community moderation bots, notification bots, support triage bots.

## Questions the requirements-analyst must ask
1. Which platform(s) and which channels/workspaces? (default: ask)
2. Commands/buttons only, or free-text conversation? (default: slash commands + buttons)
3. What data or systems does it read/change, and who is allowed to trigger what? (default: read-only; admin-only writes)
4. Does it send proactive messages (alerts, reminders)? (default: yes, rate-limited)
5. Is it installed in one workspace or distributed publicly? (default: one workspace)

## Architecture must cover
Platform app model (events API vs websocket), request **signature verification**, acknowledgement deadlines (respond within seconds, work asynchronously), command/permission model mapped to platform users, state storage, rate limits and retries with the platform, OAuth install flow, message formatting, idempotency of event delivery, hosting.

## Testing strategy
- **Unit:** command parsing, permission checks, message formatting.
- **Integration:** simulated platform events (recorded payloads) → correct reply; duplicate event delivery handled.
- **Contract:** payloads validate against the platform's schema.
- **E2E (staging workspace):** one real round trip before release.

## Walking skeleton (task 1)
Bot installed in a test workspace, answers one slash command, verifies signatures, deployed with a health route and CI green.

## CI & tooling
Lint, type-check, unit + integration with recorded events, build, dependency audit.

## Where it runs (simple defaults)
Serverless function or small managed web service (needs a public HTTPS URL), or a long-running worker for websocket-based bots.

## Security focus
Signature verification, least-privilege scopes, token storage, authorization of who may run which command, prompt injection if AI-powered, avoiding leaking private-channel data.

## Release checklist additions
Scopes minimal and documented; rate-limit handling verified; install/uninstall flow tested; admin how-to written; rollback = redeploy previous version / revoke token.

## Common pitfalls (reviewers look for these)
Not answering within the platform's deadline; ignoring duplicate deliveries; over-broad scopes; anyone can run admin commands; tokens in logs; spamming channels.

## Suggested stacks
TypeScript (Bolt) → `node` · Python (Bolt/discord.py) → `python`.
