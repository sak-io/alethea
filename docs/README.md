# Alethea documentation

Alethea is long-term memory for AI coding agents. The project [README](../README.md) has the pitch, install and quick start. This is the map of everything else.

## Start here

Pick the path that matches why you came.

| You are... | Read, in this order |
|---|---|
| **A new user** | [README](../README.md) (install, quick start) -> [What is Alethea](product/what-is-alethea.md) -> [Integrations](guides/integrations.md) -> [CLI and tools reference](reference/api.md) |
| **Evaluating it, or thinking about the business** | [What is Alethea](product/what-is-alethea.md) -> [Business case](product/business-case.md) -> [Status](architecture/status.md) (what is verified) -> [Traction checklist](project/traction-checklist.md) |
| **An integrator or operator** | [Integrations](guides/integrations.md) -> [Configuration](reference/configuration.md) -> [Docker](guides/docker.md) -> [API reference](reference/api.md) -> [Threat model](architecture/threat-model.md) |
| **A contributor or reviewer** | [Architecture overview](architecture/overview.md) -> [Technical details](architecture/technical-details.md) -> [Reviewer's guide](project/review-guide.md) -> [Lessons](architecture/lessons.md) -> [ADRs](#decisions-adrs) |
| **A security reviewer** | [Threat model](architecture/threat-model.md) -> [Reviewer's guide](project/review-guide.md) (properties P1 to P16) -> [Configuration](reference/configuration.md) (remote-call warnings) -> [ADR 0004](adr/0004-tcp-auth-and-container-binding.md), [0009](adr/0009-hooks-ship-with-the-package.md), [0011](adr/0011-remote-embedding-consent-and-redaction.md), [0012](adr/0012-llm-distillation.md) |

## Every document

| Document | Purpose | Audience |
|---|---|---|
| [../README.md](../README.md) | Front door: pitch, install, quick start, host setup | Everyone |
| **Product** | | |
| [product/what-is-alethea.md](product/what-is-alethea.md) | The problem, the idea, how it behaves, what it is not, privacy, maturity, in plain language | New users, evaluators |
| [product/business-case.md](product/business-case.md) | High-level business case with evidence levels, assumptions and a validation plan | Owner, evaluators |
| [product/product-concept.md](product/product-concept.md) | The owner's internal design thesis (source document) | Contributors, owner |
| **Guides** | | |
| [guides/integrations.md](guides/integrations.md) | Which host gets what, with links to the installers' READMEs | Users, integrators |
| [guides/docker.md](guides/docker.md) | Running the daemon as a container sidecar | Operators |
| [../integrations/claude-code/README.md](../integrations/claude-code/README.md) | Claude Code hooks and MCP setup | Users |
| [../integrations/cursor/README.md](../integrations/cursor/README.md) | Cursor and other MCP hosts | Users |
| **Reference** | | |
| [reference/api.md](reference/api.md) | Daemon verbs, MCP tools, `mnemo` commands, exit codes, errors | Integrators, users |
| [reference/configuration.md](reference/configuration.md) | Every `ALETHEA_*` and `MNEMO_*` variable | Operators |
| **Architecture** | | |
| [architecture/overview.md](architecture/overview.md) | High-level architecture: components, flows, decisions, quality attributes | Contributors, evaluators |
| [architecture/technical-details.md](architecture/technical-details.md) | Schema, algorithms, thresholds, process details (source document) | Contributors |
| [architecture/threat-model.md](architecture/threat-model.md) | Assets, trust boundaries, defences, known gaps | Security reviewers, operators |
| [architecture/claude-code-hooks-reference.md](architecture/claude-code-hooks-reference.md) | Claude Code hook events and payloads as the hook script uses them | Contributors |
| [architecture/decay-halflives.md](architecture/decay-halflives.md) | Generated table: days until a memory is archived, per kind | Contributors |
| [architecture/lessons.md](architecture/lessons.md) | Pitfalls, redaction gaps and recall measurements | Contributors |
| [architecture/plan.md](architecture/plan.md) | The original build plan | Contributors |
| [architecture/work-breakdown.md](architecture/work-breakdown.md) | Task cards T01 to T31 | Contributors |
| [architecture/status.md](architecture/status.md) | What is built, what is unverified, known gaps | Everyone evaluating maturity |
| [architecture/reviews/index.md](architecture/reviews/index.md) | Per-task review history (T01 to T21) | Reviewers |
| [specs/alethea.md](specs/alethea.md) | Requirements with acceptance criteria, traced to the source documents | Contributors |
| **Project (maintainers and reviewers)** | | |
| [project/review-guide.md](project/review-guide.md) | What to read, which properties matter, where they are enforced and tested | Reviewers |
| [project/traction-checklist.md](project/traction-checklist.md) | Release blockers and checks not yet done | Maintainer |

## Decisions (ADRs)

Architecture decision records, one per significant decision. Convention: `docs/adr/` is where the team framework expects them.

| ADR | Decision |
|---|---|
| [0001](adr/0001-owner-choices.md) | Owner technical choices, adopted as given |
| [0002](adr/0002-package-layout.md) | Package layout and script entry points |
| [0003](adr/0003-activation-and-downscale.md) | Activation uses effective strength; time units; downscale calibration |
| [0004](adr/0004-tcp-auth-and-container-binding.md) | TCP bind policy and token authentication |
| [0005](adr/0005-sleep-lock-granularity.md) | Sleep cycle must not hold the global lock for its whole run |
| [0006](adr/0006-substitution-damp.md) | Substitution damp in similarity |
| [0007](adr/0007-order-and-case-vetoes.md) | Order and case vetoes in similarity |
| [0008](adr/0008-dedup-fts-window-cap.md) | Dedup full-text window capped at 2000 rows |
| [0009](adr/0009-hooks-ship-with-the-package.md) | Hooks ship with the installed package, not the source repo |
| [0010](adr/0010-embedder-plugin-loading-and-thresholds.md) | Embedder plugin loading, containment and per-embedder thresholds |
| [0011](adr/0011-remote-embedding-consent-and-redaction.md) | Remote embedding: consent, redaction at the boundary, key handling |
| [0012](adr/0012-llm-distillation.md) | Optional LLM distillation |
| [0013](adr/0013-docker-sidecar-packaging.md) | Docker sidecar packaging |
| [0014](adr/0014-hash-salt-version-and-semantic-floor.md) | Hash salt, embedder id `hashed-ngram-v2:512`, semantic floor |

## Layout and conventions

| Folder | Holds |
|---|---|
| `product/` | What the product is and why (outsider and owner views) |
| `guides/` | How to do things |
| `reference/` | Look-up material that must match the code (`api.md` is guarded by `tests/test_docs_api.py`) |
| `architecture/` | How it is built, plus build notes and status |
| `adr/`, `specs/` | Decision records and requirements; paths fixed by the team framework |
| `project/` | Material for maintainers and reviewers |

Links in these files are checked by `tests/test_docs_links.py`: every relative link must resolve and every document must be listed above.
