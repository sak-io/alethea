# Archetype: game

## Summary
An interactive game: browser, mobile, desktop or console, from a simple puzzle to a multiplayer title.

## In plain words
"I want to build a game." The core question is "is it fun?", which only playing can answer — so build a tiny playable version first and test it with real people.

## Typical products
Browser games, mobile casual games, prototypes/jams, multiplayer games.

## Questions the requirements-analyst must ask
1. What is the core loop (what does the player do every few seconds)? (default: ask)
2. Platform(s) and input (touch, keyboard, controller)? (default: browser, keyboard/touch)
3. Single-player or multiplayer? Online accounts/leaderboards? (default: single-player, local high score)
4. Engine preference (Godot/Unity/Phaser) or none? (default: Godot or Phaser — free and simple)
5. Art/audio: who makes them, and is licensing clear? (default: free licensed assets, tracked)

## Architecture must cover
Engine choice (ADR), game loop and state model, separation of **game logic from rendering** (to test it), input handling, asset pipeline and licensing, save data and migration, performance budget (frame rate, memory), difficulty/balancing data kept out of code, multiplayer model if any (authority, latency, cheating), analytics/telemetry with consent, build targets.

## Testing strategy
- **Unit:** pure game rules (scoring, collisions, state transitions) with deterministic seeds.
- **Integration:** save/load, level loading, progression.
- **Playtests:** scripted bots / replay of recorded inputs through levels; a human playtest checklist per release.
- **Performance:** frame-time and memory budgets in CI where feasible.
- **Multiplayer:** desync and latency simulation.

## Walking skeleton (task 1)
A window/canvas with a controllable object, one rule, a restart; builds for the target in CI; logic tests run headless.

## CI & tooling
Lint/format, headless logic tests, build per target (engine-specific CI images), asset-size check, dependency audit.

## Where it runs (simple defaults)
Browser games: static hosting. Others: platform stores (Steam/itch.io/app stores) — a **human step**; multiplayer needs a small backend (`realtime-service`).

## Security focus
Cheating/tampering for competitive features, server authority for multiplayer, save-file tampering, third-party SDKs and ads, privacy and children's-data rules (COPPA/GDPR-K), asset license compliance.

## Release checklist additions
Playtested on target devices; performance budget met; asset licenses recorded; save compatibility with the previous version; store/age-rating/privacy requirements; crash reporting; rollback = patch release.

## Common pitfalls (reviewers look for these)
Logic tangled with rendering (untestable); frame-rate dependent physics; no save migration; huge unoptimized assets; hard-coded balance numbers; unclear licensing; no way to restart/recover from bad states.

## Suggested stacks
Phaser/TypeScript → `node` · Godot (GDScript/C#) · Unity (C#). Add an engine-specific CI template if not `node`/`dotnet`.
