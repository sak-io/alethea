# Engram

Long-term memory for AI coding agents (Claude Code, Codex, Gemini, Grok), modeled on how human memory works.

Each AI session keeps its own context window. Engram is the layer that survives after the session ends and feeds the next one. It remembers what mattered, abstracts specifics into durable facts, and lets the rest fade.

- Pure Python 3 standard library with SQLite storage. No pip installs, no network calls, no embedding API.
- Memory is exposed to agents over MCP, so it follows you between tools.
- Command-line interface: `mnemo`.

> Status: design stage. The documents below specify the product and the technical design.

## Documentation

- [Product concept](docs/product-concept.md): the problem, the thesis, how Engram behaves, and project direction.
- [Technical details](docs/technical-details.md): schema, algorithms, thresholds, process architecture, and build order.
