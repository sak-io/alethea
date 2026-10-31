# Business case (high level)

Why Alethea might be worth building, for whom, and what would have to be true. This is a thinking document for the owner, not a market study. **It contains no market sizes, revenue figures, user counts or quotes, because none are known.** Where a number would normally go, the text says it is unknown and how to find out.

Every claim carries an evidence level:

| Tag | Meaning |
|---|---|
| **[Verified]** | Checked in this repository (code, tests or docs) |
| **[Owner]** | Stated by the owner in the [product concept](product-concept.md); not independently checked |
| **[Assumption]** | Plausible but untested; listed in section 9 with a way to test it |
| **[Unknown]** | No evidence either way |

Product background: [What is Alethea](what-is-alethea.md). The owner's current direction is an open-source project, not a funded venture **[Owner]**; sections 6 to 8 explore what could follow and are options, not decisions.

## 1. The problem and its cost

AI coding agents start every session without memory of earlier ones **[Verified: this is the premise of the product; each host keeps its own context window]**. The developer pays by re-explaining context, re-making decisions, or reviewing work that ignores earlier decisions.

Cost in time or money per developer: **[Unknown]**. It has not been measured. To find out: time how long a sample of developers spend restating context at session start over a week, and count how often an agent contradicts a recorded decision.

Two simple fixes have known weaknesses **[Owner]**: logging everything buries the signal; a plain vector store returns the closest text with no sense of importance or staleness.

## 2. Customers

| Segment | Need | Evidence |
|---|---|---|
| Individual developers who use AI agents daily, possibly across several tools | Continuity between sessions without hand-maintaining notes; privacy | **[Assumption]**. This is the only segment the current product is built for (single user, one machine). |
| Small teams | Shared project memory (decisions, causes, procedures) across people | **[Assumption]**. Not supported today: the design is explicitly single-user, and exported rule files are the only sharing path (committing them is the user's choice) **[Verified]**. |
| Tool vendors and platform builders (agent hosts, IDE plugins, internal developer platforms) | An embeddable, host-agnostic memory layer with a security posture | **[Assumption]**. The MCP interface and zero dependencies make embedding feasible **[Verified]**; whether vendors want a third-party layer rather than their own is **[Unknown]**. |

Number of people in any segment, and willingness to pay: **[Unknown]**. To find out: see the validation plan in section 10.

## 3. Value proposition

For a developer who works with AI agents: the agent knows what you decided and why, without you restating it, and without sending your history to a cloud service.

The delivered value today is bounded by the maturity of the product. It works in tests and in a scripted quick start; it has not been used in a live Claude Code or Cursor session, and recall quality on real sessions is unmeasured **[Verified: see [status](../architecture/status.md)]**.

## 4. Differentiation

| Differentiator | What is true today | Evidence |
|---|---|---|
| Local-first | Data in one SQLite file; Unix socket; no network calls by default; remote embedding and LLM features are opt-in | **[Verified]** ([threat model](../architecture/threat-model.md)) |
| Standard library only | No runtime dependencies; Python 3.9+; one optional extra for a local embedding model | **[Verified]** (`pyproject.toml`) |
| Security and hostile-text defences | Redaction before storage; stored text rendered as inert list items under a "data, not instructions" header; model-written memories withheld from briefings until reinforced; merge-only installer. Best effort, and no human security review has happened | **[Verified]** that the defences exist and are tested; **[Unknown]** how well they hold against real attackers |
| Host-agnostic through MCP | One store serves Claude Code, Cursor and any MCP host; automatic capture only where the host has hooks (Claude Code) | **[Verified]** in code and tests; not verified against real hosts other than by recorded payloads |
| Brain-inspired consolidation | Four memory kinds with different decay, reinforcement on recall, a sleep cycle that summarises, archives and links | **[Verified]** that it is implemented; **[Unknown]** whether it gives better results than a simpler design for real users |

The last row is the central unproven bet: the design is principled, but no comparison against a simpler baseline on real sessions exists.

## 5. Alternatives and competitive landscape

Qualitative only. This project has not benchmarked any alternative, and descriptions of other systems are general characterisations that should be re-checked against current documentation before use in any external material **[Assumption]**.

| Category | Examples | How they work (general) | Where Alethea differs | Where they are stronger |
|---|---|---|---|---|
| Vector-store memory | Plain embedding databases wrapped as agent memory | Store chunks, retrieve nearest neighbours | Importance, decay, dedup safety and consolidation on top of retrieval | Learned embeddings give better paraphrase recall than Alethea's default hashed vectors **[Verified: documented limitation]** |
| Paging-style agent memory | MemGPT / Letta style | The model manages its own memory tiers, moving text in and out of context | Alethea is a separate service, not an agent runtime, and works with the host's own agent | Tighter integration with the agent loop |
| Memory services | Mem0 and similar | Extract and store user or app facts, usually as a service or library | Local-first, coding-specific encoding, no required cloud | Likely broader in scope and better resourced **[Assumption]**; the owner notes they could add engineering-specific tuning quickly **[Owner]** |
| Graph-based retrieval | HippoRAG, GraphRAG | Build a knowledge graph over documents to answer multi-hop questions | Alethea has association links and spreading activation, but over a small personal store, not a document corpus | Designed for graph reasoning over large corpora |
| Vendor built-in memory | Claude Code, Cursor and other hosts' own memory features | Per-vendor, tied to that tool | Works across tools; user-controlled and inspectable store | Zero setup, native UX, no daemon. The owner regards this as the existential risk **[Owner]** |

Take-away: Alethea does not have a unique retrieval technique. Its position is the combination of local-first, no dependencies, safety work and host independence. Whether that combination matters to enough people is **[Unknown]**.

## 6. Possible business models (options, not decisions)

The core is open source under Apache-2.0 **[Verified: `LICENSE`]**. Anything below would be additional.

| Option | Idea | Fit with current design | Main risk |
|---|---|---|---|
| Open source only | Build reputation, no revenue | Direct fit; the owner's stated direction **[Owner]** | No funding for maintenance |
| Support and sponsorship | Donations, sponsors, paid support for teams | Fits as is | Small, uncertain income **[Assumption]** |
| Hosted team sync | A service that shares selected memories across a team | Needs multi-user design, access control, TLS and encryption; none exist today **[Verified]**. Conflicts with the local-first stance and the owner's "single-user" scope | Large build; the privacy promise changes |
| Enterprise features | Audit logging, policy, SSO, retention controls | Explicitly out of scope today **[Owner]** | Wrong shape for the current user base |
| Vendor licensing or integration | Help a tool vendor embed it | Technically feasible | Vendors may prefer in-house memory |

None of these has been tested with a customer.

## 7. Costs and risks

| Item | Notes | Evidence |
|---|---|---|
| Development cost to date | Built by AI agents under the owner's direction; cash cost and time not recorded here | **[Unknown]** |
| Maintenance | One owner; every review so far was by AI agents; no human code review | **[Verified]** |
| Platform risk | Hosts ship native memory for free and may make a third-party layer unnecessary | **[Owner]** |
| Quality risk | Default recall cannot match paraphrases that share no words; extraction-based summaries can be disjointed on noisy sessions | **[Verified: documented limitation]** |
| Adoption friction | A daemon, a socket and hook wiring; the owner calls this too much friction for a five-minute trial | **[Owner]** |
| Security and privacy risk | Memory text may contain secrets the redactor misses; no encryption at rest; no TLS on TCP; no independent security scan yet | **[Verified]** ([threat model](../architecture/threat-model.md)) |
| Unverified integrations | Real Claude Code, Cursor, embedding models and provider endpoints are untested | **[Verified]** ([traction checklist](../project/traction-checklist.md)) |
| Platform coverage | POSIX only; macOS untested; Windows unsupported | **[Verified]** |
| Release blockers | Not yet published or merged to `main`; `NOTICE` file undecided | **[Verified]** (traction checklist) |

## 8. Success metrics and leading indicators

Targets are **[Unknown]**: there is no baseline. The owner should set them after the first measurements. The product has no telemetry and none should be added without consent **[Verified]**, so these are manual observations.

| Metric | Type | How to observe |
|---|---|---|
| Stars, forks, issues opened by people other than the owner | Leading | Repository insights |
| Unprompted requests for integrations with other agents | Leading **[Owner]** | Issue tracker, discussions |
| Mentions in adjacent communities (MCP tooling, agent tooling) | Leading **[Owner]** | Manual search |
| Installs (after publishing) | Lagging | Package index download statistics, which are noisy |
| Users who still have the hooks installed after four weeks | Lagging, best signal | Ask a small group directly; no telemetry exists |
| Recall quality on real sessions (top-3 hit rate) | Product quality | Re-run the regression harness on a corpus from real use; today's 0.95 and 0.83 are from a synthetic corpus **[Verified]** |
| Time spent restating context, with and without Alethea | Value | Small user study (section 10) |

## 9. Key assumptions to validate

| # | Assumption | Why it matters | Evidence now |
|---|---|---|---|
| A1 | Developers lose meaningful time to re-explaining context to agents | The whole value proposition | [Assumption] |
| A2 | Developers use more than one AI tool often enough to want a shared memory | The cross-vendor differentiator pays off only then **[Owner]** | [Assumption] |
| A3 | Vendor built-in memory will leave a gap that a third-party layer fills (control, privacy, portability) | Platform risk is existential **[Owner]** | [Assumption] |
| A4 | Structured forgetting gives better results than keeping everything | The central design bet | [Assumption]; no comparison exists |
| A5 | Users accept a daemon plus hook wiring if the payoff is visible quickly | Adoption | [Owner] flags friction; [Unknown] tolerance |
| A6 | The hooks and installer work in real Claude Code and Cursor sessions | Everything user-facing depends on it | [Unknown]: never tested live |
| A7 | Recall quality on real sessions is good enough with the default embedder | Trust | [Unknown]: only synthetic corpus |
| A8 | Local-first and security work are valued by the target users | Differentiation | [Assumption] |
| A9 | Teams want shared agent memory and would pay for it | Any commercial path | [Unknown]; the product does not support it |
| A10 | Tool vendors would integrate rather than build their own | Vendor path | [Unknown] |

## 10. Validation plan

Cheap steps first, in order. Each ends in a decision, not a report.

1. **Make the product real.** Run the unverified checks in the [traction checklist](../project/traction-checklist.md) (live Claude Code, live Cursor, a real embedding model). Outcome: A6 and A7 move from unknown to known. If hooks do not work live, fix that before anything else.
2. **Dogfood.** The owner uses it for several weeks on real projects and keeps a short log: times it helped, times it was wrong or noisy. Outcome: first real signal on A1, A4, A7.
3. **Small pilot.** Five to ten developers who use AI agents daily install it and are interviewed after two to four weeks (the sample size is a suggestion, not a statistical design). Ask what they would lose if it were removed. Outcome: A1, A2, A5.
4. **Baseline comparison.** Run the same set of questions against Alethea and one simple alternative (for example a plain notes file or a vector store) on a corpus from real sessions. Outcome: A4.
5. **Publish and watch.** Publish to the package index, post once, track the leading indicators in section 8 for a defined period. Outcome: A3, A8.
6. **Only then** explore team or vendor questions (A9, A10) by talking to a handful of potential customers before building anything.

## 11. Decisions the owner must make

| # | Decision | Recommended default |
|---|---|---|
| 1 | Stay open source only, or reserve the option of a commercial layer | Stay open source until step 3 of the validation plan shows demand |
| 2 | Target the individual developer only, or plan for teams | Individual developer; revisit after the pilot |
| 3 | Who holds the license and `NOTICE` | The owner's chosen pseudonym is already in `LICENSE`; decide on `NOTICE` before publishing |
| 4 | When to publish to the package index | After the live-host checks in step 1 |
| 5 | Whether to accept that commit history is authored by an AI agent identity | Decide before announcing (see the traction checklist) |
| 6 | What counts as success and by when | Set targets after dogfooding, so they are based on a baseline |
| 7 | Whether to commission a human security review | Yes, before promoting it to teams |
| 8 | Whether a one-command installer is worth prioritising | Yes if the pilot shows friction is the main drop-off |
