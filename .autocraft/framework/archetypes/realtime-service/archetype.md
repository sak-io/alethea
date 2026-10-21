# Archetype: realtime-service

## Summary
A service where updates are pushed to users instantly over persistent connections: chat, live collaboration, notifications, dashboards, multiplayer.

## In plain words
"I want things to update on screen immediately without refreshing." Connections stay open, so scaling, reconnects and message ordering become the hard parts.

## Typical products
Chat, collaborative editing, live dashboards/feeds, presence ("who's online"), multiplayer backends.

## Questions the requirements-analyst must ask
1. What updates in real time and how quickly must it arrive? (default: within a second)
2. How many people connected at once, and in how many rooms? (default: < 1,000 concurrent)
3. Must messages be delivered even if someone is offline? (default: yes — stored and replayed)
4. Is order important? Can two people edit the same thing? (default: yes → define conflict rules)
5. Who may join/see which channel? (default: authenticated members only)

## Architecture must cover
Transport (WebSocket/SSE/managed service), connection auth and per-channel authorization, message schema/versioning, ordering and delivery guarantees, reconnection and resync (catch up after drop), fan-out and scaling across instances (pub/sub backplane), presence, conflict resolution (CRDT/OT/last-write-wins with rules), backpressure and rate limits, persistence of history, observability (connections, lag, dropped messages).

## Testing strategy
- **Unit:** message handling, ordering, conflict resolution.
- **Integration:** multiple simulated clients: join, send, receive, disconnect, reconnect, catch up.
- **Authorization:** a user cannot subscribe to or read someone else's channel.
- **Load:** thousands of simulated connections; message latency percentiles.
- **Chaos:** server restart and network drop mid-conversation.

## Walking skeleton (task 1)
Authenticated connection, one channel, two test clients exchanging a message, reconnect with resume, CI runs a multi-client test.

## CI & tooling
Lint, type-check, unit + multi-client integration, load smoke test, dependency audit.

## Where it runs (simple defaults)
A managed realtime service (Ably/Pusher/Supabase Realtime/Cloudflare Durable Objects) or a managed platform with Redis for fan-out. Avoid hand-rolling scaling at first.

## Security focus
Authenticate on connect and re-check on subscribe, channel authorization, message size/rate limits, input sanitization (XSS in chat), origin checks, denial of service by connection floods, token expiry on long-lived connections.

## Release checklist additions
Load test at 2× expected peak; reconnect/resync verified; rate limits set; graceful deploys that don't drop everyone at once; dashboards for connections/lag; rollback = redeploy + clients reconnect automatically.

## Common pitfalls (reviewers look for these)
No auth on subscribe; assuming one server instance; lost messages on reconnect; unbounded message queues; trusting client timestamps; rendering unsanitized messages; thundering-herd reconnects after deploy.

## Suggested stacks
TypeScript (WebSocket/Socket.IO) → `node` · Go → `go` · Elixir/Phoenix (add a CI template).
