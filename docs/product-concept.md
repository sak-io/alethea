# Alethea – Product Concept

## 1. What it is

Alethea is a long-term memory system for AI coding agents (Claude Code, Cursor, Codex, Gemini, Grok). It is deliberately modeled on human memory architecture rather than built as a vector-store CRUD app.

Each AI session has its own context window, which is ephemeral working memory. Alethea is the layer that survives after a session ends and feeds the next one.

## 2. The problem

Coding agents start every session as a stranger. They forget who you are, the decisions you made, why you made them, and what broke last week. Two obvious fixes both fail:

- **Log everything.** A system that remembers everything indiscriminately surfaces nothing useful. Signal drowns in noise.
- **Store everything in a vector database.** Retrieval returns whatever is lexically or semantically closest, with no sense of what mattered, what is stale, or what was already known.

## 3. Core thesis

Human memory is useful *because* it is lossy in a structured way. It privileges what mattered, abstracts specifics into general facts, and lets irrelevant detail fade. Every component of Alethea implements one piece of that behavior.

| Brain concept | What Alethea does |
| :---- | :---- |
| Working memory | The session's own context window (not part of Alethea) |
| Hippocampus | A journal: fast, raw capture of every turn |
| Neocortex | Durable memories and the associations between them |
| Amygdala | Salience scoring: consequential material is encoded more strongly |
| Prediction error | Novelty: restating what is already known encodes weakly or not at all |
| Sleep | Consolidation: replay, distill, prune, recombine |
| Forgetting curve | Memory strength decays over time, at different rates per memory type |
| Testing effect | Every recall strengthens the memory it retrieves |

## 4. How it behaves

**Four kinds of memory**, each fading at its own rate:

- **Episodic**: what happened, bound to a session and time. Fades fastest.
- **Semantic**: facts, decisions, and the reasons behind them.
- **Procedural**: how to do things. Strengthened by repetition.
- **Identity**: who the user is. Never decays.

**Two ways memories get in:**

- **Involuntary**: hooks capture every turn automatically. High-salience material is promoted to long-term memory immediately. The rest waits for consolidation.
- **Voluntary**: the agent or user deliberately calls remember, recall, reflect, or forget. This produces better abstractions than automatic consolidation can.

**Waking up informed.** At the start of a session, the agent receives a briefing: who the user is first, then the strongest decisions and procedures, then a few recent episodes from the current repository (falling back to the working directory outside git). Memories are scoped by repository root, so they follow an engineer across subdirectories, worktrees, and symlinked paths.

**Sleeping.** When idle, or at session boundaries, Alethea consolidates. It groups raw turns into episodes, distills them into summaries and facts, weakens everything slightly, archives what has faded, and links related memories it had not connected before.

**Recalling.** Retrieval fuses keyword match, semantic similarity, and association links. It then weights results by how strongly each memory is currently held, so a decision reinforced last week beats a better textual match left untouched for months.

**Living alongside native memory.** Claude Code and Cursor already have hand-written instruction files (`CLAUDE.md`, `.cursor/rules`). Alethea leaves those alone. The split is simple: native files hold *static ground truth* (stack, style, build commands, guardrails) that the user curates; Alethea holds *dynamic history* (decisions, bug causes, procedures, preferences) that is reinforced, decays, and is deduplicated automatically. A short directive in the native file tells the agent when to consult Alethea.

Alethea can also write back. A one-way export renders its strongest, most-reinforced memories for the current repository into a separate auto-generated markdown file that the host loads natively. This is how memory reaches hosts with no session-start hook (Cursor), and it means the best of what Alethea has learned is visible and reviewable as plain text. The export is a regenerated snapshot, not a second source of truth, and it is kept out of version control by default because memories can be personal.

## 5. Design principles

1. **A broken memory must never break the session.** If the memory service is down, slow, or failing, the agent continues as if nothing happened.
2. **Prefer redundancy to false merges.** A redundant memory is untidy and self-corrects under reinforcement. A false merge of two distinct facts is silent and permanent. Duplicate detection is biased hard toward "these are different".
3. **Zero dependencies, fully offline by default.** Pure Python 3 standard library, with SQLite as storage. No pip installs, no network calls, no embedding API, no cost. Learned embedders and LLM-assisted consolidation are opt-in plugins; the default install never needs them, and the system degrades to the default behavior if they fail.
4. **Lossy by design.** Forgetting is a feature. Nothing is kept merely because it was said.

## 6. Differentiation

- Cross-vendor portability. Memory is exposed over MCP, so it follows an engineer between Claude Code, Cursor, Codex, Gemini, and Grok. Because memories are keyed by repository, work done in Claude Code is recalled in Cursor on the same repo.
- Engineering-specific encoding: decisions, corrections, causes, constraints, quantitative observations, and incident language are recognized and weighted.
- A real consolidation cycle, distinct from retrieval, rather than an append-only store.

## 7. Known limitations

- Semantic matching uses hashed n-gram vectors by default, not a learned embedding model. It cannot see synonymy ("database wiped" vs "postgres reset") or negation ("enable auth" vs "disable auth"). This is the highest-leverage upgrade path. The `Embedder` is a first-class plugin: users can opt into a local model (for example ONNX or fastembed with `all-MiniLM-L6-v2`) or a remote API. Learned embeddings are not a free fix for negation either, so the duplicate-safety rule holds under every embedder.
- Consolidation distills by extraction only (no LLM). On noisy coding sessions (stack traces, diffs, terminal output), sentence-ranking heuristics can produce disjointed snippets. An optional, off-by-default LLM-assisted consolidation pass can synthesize structured bullets instead. Extraction remains the default and the fallback.
- Hosts differ in what they expose. Claude Code offers lifecycle hooks, so capture and briefing are automatic. Cursor is MCP-first: unless its hooks cover it, capture is voluntary only and the briefing arrives through the exported rules file. Cross-vendor parity is best-effort, not guaranteed.
- Setup involves a daemon, a socket, and hook wiring. That is likely too much friction for a five-minute trial.

## 8. Direction

The decision is to pursue Alethea as an **open-source project**, not a funded commercial venture.

**Why:**
- Platform risk is existential: Claude, ChatGPT, and Gemini are all shipping native cross-session memory as a free built-in feature.
- The cross-vendor differentiator only pays off if engineers switch agent tools often enough to want continuity.
- Funded products such as Mem0 and Zep solve an adjacent problem and could add engineering-specific tuning quickly.
- Open source de-risks adoption: no go-to-market motion is needed to get real signal. The technical rigor (the duplicate-safety rule, the four-store decay model, a distinct consolidation cycle) earns credibility with an open-source and MCP-tooling audience.

**Scope:** a single-user design (daemon plus MCP server plus the core encode/recall/consolidate model). A pluggable `Embedder` seam (default `HashedNgramEmbedder`, with local-model and remote-API options) lets users plug in a real embedding model, and an optional LLM consolidation pass can be enabled for higher-quality gists. Multi-tenancy, audit logging, and tenant policy are out of scope, as they are the wrong shape for a solo open-source user.

**Open questions:**
- License: MIT vs Apache-2.0 (leaning Apache-2.0, undecided).
- Repository structure for the release.
- Whether to formalize a traction-validation checklist (GitHub engagement, unprompted cross-agent integration requests, mentions in adjacent communities).
- A single-command installer to reduce setup friction, proposed but not yet scoped.
