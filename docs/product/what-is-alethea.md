# What is Alethea?

Alethea is long-term memory for AI coding agents. It runs on your machine, remembers what mattered across sessions, and lets the rest fade. This page explains the idea in plain language. It assumes you use an AI coding tool such as Claude Code or Cursor; it assumes nothing else.

Looking for something else? The [docs home](../README.md) has reading paths for users, evaluators, operators and contributors.

## The problem: every session starts with a stranger

An AI coding agent works inside a context window. When the session ends, the window is gone. The next session does not know who you are, which database you chose and why, or what broke last week. You explain it again, or the agent rediscovers it.

The two obvious fixes both have a catch:

- **Log everything.** If every line is kept, nothing stands out. Finding the one decision that matters means wading through noise.
- **Put everything in a vector database.** Retrieval returns whatever text is closest to the question. It has no sense of what mattered, what is out of date, or what is already known.

## The core idea: memory that is lossy on purpose

Human memory is useful because it is lossy in a structured way. It keeps what mattered, turns specifics into general facts, and lets irrelevant detail fade. Alethea copies that behaviour, one piece at a time.

| In the brain | In Alethea, in plain words |
|---|---|
| Working memory | The agent's own context window. Not part of Alethea. |
| Hippocampus (fast, raw capture) | A journal that records each turn as it happens. |
| Neocortex (durable knowledge) | The long-term memories and the links between them. |
| Amygdala (emotional weight) | A score for how consequential a piece of text is: decisions, corrections, causes, "never do this". |
| Surprise | Novelty: saying something already known adds little or nothing. |
| Sleep | A background pass, run when you are idle, that summarises the journal, weakens unused memories, links related ones and tidies up. |
| Forgetting curve | Every memory loses strength over time, at a rate that depends on its kind. |
| Testing effect | Every time a memory is recalled, it gets stronger. |

## The four kinds of memory

| Kind | What it holds | How it fades |
|---|---|---|
| Episodic | What happened in a session | Fastest |
| Semantic | Facts, decisions and the reasons behind them | Slowly |
| Procedural | How to do things | Slower still. (Like every kind, it is strengthened each time it is recalled.) |
| Identity | Who you are (role, preferences) | Never |

For a memory that is never recalled again, the calculated time until it is archived is roughly one month for episodic, two and a half to three and a half months for semantic, and five to seven months for procedural ([table](../architecture/decay-halflives.md)). These are calculated from the formulas, not measured on real use.

## How a memory gets in

There are two doors.

**Automatic.** With the Claude Code hooks installed, every prompt and every final reply is written to the journal. Most of it stays there until the next sleep cycle. A turn that looks durable (a decision, a correction, a cause) is promoted to a long-term memory immediately.

**Deliberate.** The agent or you calls `remember` or `reflect`. Deliberate writes skip the "is this chatter?" check and get a higher floor, so they are kept.

Every candidate memory passes the same gate, called `encode()`:

1. **Redaction.** Secret-looking strings (API keys, tokens, private keys, passwords in URLs) are replaced with a marker such as `[REDACTED:...]` before anything is stored. This is pattern-based and best effort; it misses unusual formats. See the [threat model](../architecture/threat-model.md).
2. **Trivia check.** "ok", "thanks" and similar are rejected.
3. **Novelty.** The new text is compared with what is already stored. If it restates an existing memory, that memory is strengthened and nothing new is written. The comparison is deliberately biased towards "these are different": two facts that differ in a number, a path, a negation ("enable auth" versus "disable auth") or a swapped name are never merged. A redundant copy is untidy but harmless; a wrong merge is silent and permanent.
4. **Salience.** Weak, low-consequence text below a threshold is not stored.
5. **Kind.** The text is filed as identity, procedural, semantic or episodic.

## What the sleep cycle does

There is no nightly schedule. Alethea sleeps when you stop using it: after about three idle minutes, and at least fifteen minutes after the previous cycle (`ALETHEA_IDLE_SLEEP`, `ALETHEA_SLEEP_INTERVAL`), and only if something has happened since. You can also run a full cycle yourself with `mnemo sleep`. Two lighter forms are triggered by the host's hooks:

- **Session end:** only the summarising step (stage 1 below).
- **Before the host compacts its context:** summarising and journal tidying (stages 1 and 2), but not the slower weakening and linking.

A full cycle runs these stages in order, in the background:

1. **Replay and summarise.** The journal is grouped into episodes (a gap of 15 minutes starts a new one). Each episode gets a short summary plus extracted facts, written through the same gate as any other memory.
2. **Tidy the journal.** Raw journal rows are deleted 30 days after they were summarised, or 90 days if they never were. The summaries and facts stay.
3. **Weaken what has gone unused.** Each memory loses strength for the time that has passed since the last pass, at a rate set by its kind. Identity memories are exempt. This runs at most once every six hours, and one pass applies at most 14 days of weakening, so a long break does not wipe out what you knew. A memory that falls too weak is **archived**: it no longer shows up in normal recall, but it is not erased.
4. **Delete narrowly.** Only archived episodic memories that were never recalled again are deleted for good, after about 45 days. Memories you asked to forget are deleted after about 90 days. Archived facts and procedures are kept, and identity memories are never deleted.
5. **Link related memories.** Facts, procedures and identity memories that are clearly related but not duplicates get a link, so recalling one can surface its neighbour.

The cycle works in small batches so requests are never blocked behind it, and it has a time budget of about two minutes. If it runs out of time it stops between episodes and resumes next time. If one stage fails, the others still run.

Summaries are written by extraction (ranking sentences), not by a language model, unless you turn on the optional LLM-assisted mode. Extraction on messy terminal output can produce disjointed snippets. "Sleep" is a metaphor for this maintenance job. It has been exercised by automated tests and simulations, not through weeks of real use.

## A short example session

This is an illustration of the design, using the commands from the [README quick start](../../README.md#quick-start). The hook-driven parts have not been observed in a live Claude Code session yet (see [maturity](#how-mature-is-it)).

**Monday.** You and the agent debate databases. You say: "We decided to use PostgreSQL for the billing service because we need transactions." Decision and causal language count towards the durability score, so a turn like this is a candidate for immediate promotion to a semantic memory (if it scores too low, it still waits in the journal for the sleep cycle). The rest of the chat stays in the journal. Once you have been idle for a few minutes, or when the session ends, the sleep cycle compresses it into an episode summary.

Or, deliberately and without hooks:

```sh
mnemo remember "We decided to use PostgreSQL for the billing service"
```

**Thursday.** You ask a new session to add an invoices table. At session start the hook injects a short briefing: who you are first, then the strongest decisions and procedures, then a few recent episodes from this repository. The agent sees the PostgreSQL decision without being told.

**Later.** The agent wonders about a past outage and calls `recall` with a question. Matching memories come back, ranked by how well they match and how strongly they are held; each one returned is strengthened. Relevance floors are designed so that a question unrelated to anything stored returns nothing rather than a weak guess (0 of 19 off-topic cues in the test corpus returned a memory).

```sh
mnemo recall "which database for billing"
```

**Months later.** The billing decision was recalled several times, so it is strong. A one-off debugging episode that was never revisited has faded and been archived.

## What Alethea is not

- Not a replacement for `CLAUDE.md` or Cursor rules. Those hold static ground truth you write by hand (stack, style, commands). Alethea holds dynamic history that is reinforced and decays. It can write a separate, auto-generated file of its strongest memories (`mnemo export-rules`) for the host to load; it never edits your own files.
- Not a chat log or a search engine over transcripts. Forgetting is a feature.
- Not a cloud service. There is no hosted Alethea; nothing is sent anywhere by default.
- Not multi-user or multi-tenant. It is designed for one person's machine.
- Not a security product. It reduces the chance of storing secrets and defends against hostile text in memories, but see the known gaps in the [threat model](../architecture/threat-model.md).
- Not a learned-embedding system by default. Recall uses keyword search plus a simple hashed text vector, so a paraphrase that shares no words with a memory can miss. A local embedding model is an optional extra.
- Not available on Windows. POSIX only: tested on Linux; macOS and BSD should work but are untested.

## Privacy

- **Local-first.** The data is one SQLite file under `~/.alethea` (or `ALETHEA_HOME`). The daemon listens on a Unix socket readable only by you.
- **No network calls by default**, and no embedding API.
- **Remote features are opt-in and off by default**: a remote embedding provider and LLM-assisted summaries. Each needs explicit configuration, and the remote embedder needs a second acknowledgement. They send redacted text, but redaction is best effort, so enable them only for data you would be willing to send to that provider.
- **Not encrypted at rest.** Prompts and replies in the journal are stored in plaintext for up to 30 to 90 days. Use disk encryption if that matters.
- **The source repository never records your sessions.** The hooks ship with the installed package and only run after `mnemo install claude-code`.
- **No telemetry.**

## How mature is it?

Honest summary, details in [status](../architecture/status.md) and the [traction checklist](../project/traction-checklist.md):

| Verified (by running it) | Not verified |
|---|---|
| The engine, daemon, client, MCP server and CLI pass an automated test suite (run locally on Python 3.9 and 3.13; CI is configured for 3.9, 3.11, 3.13 and 3.14) | A real Claude Code session with the hooks installed |
| The README quick start, run from a built wheel in a clean virtual environment | A real Cursor, Codex, Gemini CLI or Grok |
| The wheel builds and installs; the entry points work | Recall quality with a real embedding model, or with real provider endpoints |
| The Docker image builds and runs hardened, with no network | `docker compose`, the published port, and macOS |
| Recall on a test corpus: top-3 hit rate 0.95 on the set it was tuned on, 0.83 on a held-out set | Quality on real sessions; the corpus was written by the build agents |

Every code review so far was done by AI agents. No human has reviewed the code, and no independent security scan has been run. It is not published on PyPI yet.

## Where to go next

- Try it: [README quick start](../../README.md#quick-start).
- Decide whether it is worth it: [business case](business-case.md).
- See how it is built: [architecture overview](../architecture/overview.md).
- Look something up: [API reference](../reference/api.md), [configuration](../reference/configuration.md).
