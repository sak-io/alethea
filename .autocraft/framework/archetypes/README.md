# Archetypes

An **archetype** is a project-type profile. The agents are the same for every project; the archetype tells them *what good looks like for this kind of product*: which questions to ask, what the architecture must cover, how to test, what to scan for, where it runs, and what to check before release.

> **Don't know which one you need?** Don't pick. Run `/discover` and describe your idea in your own words — the agents will recommend archetypes and explain why.

## The catalog (20 component types)

### Things people use
| Archetype | In one line | Typical flag |
|---|---|---|
| [static-site](static-site/archetype.md) | Pages that show information: landing page, blog, docs, portfolio | `node` |
| [web-app](web-app/archetype.md) | Interactive screens in the browser | `node` |
| [fullstack-app](fullstack-app/archetype.md) | Screens + server + database in one project (most SaaS) | `node`, `python` |
| [mobile-app](mobile-app/archetype.md) | iOS / Android apps from the stores | `node`, `flutter` |
| [desktop-app](desktop-app/archetype.md) | Programs people download and run | `node`, `dotnet`, `rust` |
| [game](game/archetype.md) | Interactive games | `node`, `dotnet` |
| [internal-tool](internal-tool/archetype.md) | Admin panels, dashboards, back-office tools | `node`, `python` |
| [chat-bot](chat-bot/archetype.md) | Bots inside Slack/Discord/Teams/Telegram | `node`, `python` |
| [plugin-extension](plugin-extension/archetype.md) | Extensions for browsers, IDEs, Shopify, WordPress, GitHub | `node` |

### Things that run behind the scenes
| Archetype | In one line | Typical flag |
|---|---|---|
| [api-service](api-service/archetype.md) | A backend other programs call | `python`, `node`, `go`, `java`, `dotnet` |
| [realtime-service](realtime-service/archetype.md) | Live updates: chat, collaboration, live feeds | `node`, `go` |
| [event-processor](event-processor/archetype.md) | Reacts to queue messages, webhooks, uploads | `node`, `python`, `go` |
| [scheduled-job](scheduled-job/archetype.md) | Runs by itself on a schedule | `python`, `node`, `go` |
| [script-automation](script-automation/archetype.md) | Small scripts that automate a chore | `python`, `node` |
| [data-pipeline](data-pipeline/archetype.md) | Moves/cleans data for reports and analysis | `python` |

### Intelligence
| Archetype | In one line | Typical flag |
|---|---|---|
| [ai-assistant](ai-assistant/archetype.md) | Chatbots, copilots, document Q&A, AI agents (LLM-based) | `python`, `node` |
| [ml-service](ml-service/archetype.md) | Train and serve your own prediction models | `python` |

### Foundations
| Archetype | In one line | Typical flag |
|---|---|---|
| [library-cli](library-cli/archetype.md) | Reusable packages, SDKs, command-line tools | `node`, `python`, `go`, `rust` |
| [infrastructure](infrastructure/archetype.md) | Cloud setup as code (Terraform etc.) | `terraform` |
| [embedded-iot](embedded-iot/archetype.md) | Firmware and connected devices | `rust` (or add your own CI) |

Everything has a safe default; every profile has the same sections (see below), so adding a new one is just copying a file.

## Recipes — common products and what they are made of

| I want to build… | Archetypes | Capabilities that usually come with it |
|---|---|---|
| A website for my business or portfolio | `static-site` | analytics, deployment |
| An online store | `fullstack-app` | authentication, payments, database, email, file-uploads, search |
| A SaaS product (accounts + subscription) | `fullstack-app` (+ `scheduled-job`) | authentication, payments, database, email |
| A mobile app with accounts and data | `mobile-app` + `api-service` | authentication, database, notifications, file-uploads |
| A dashboard for my team's data | `internal-tool` (+ `data-pipeline`) | authentication, database |
| An AI chatbot for my website or docs | `ai-assistant` + `web-app` (or `fullstack-app`) | database, analytics, privacy-compliance |
| A Slack/Discord helper | `chat-bot` (+ `ai-assistant`) | secrets-and-config |
| A nightly report or sync | `scheduled-job` (or `data-pipeline`) | email, analytics (missed-run alerts) |
| Process payments/webhooks reliably | `event-processor` + `api-service` | payments, database |
| A live chat or collaboration feature | `realtime-service` + `web-app` | authentication, database |
| A browser extension | `plugin-extension` | privacy-compliance |
| A marketplace of buyers and sellers | `fullstack-app` + `event-processor` | authentication, payments, search, file-uploads, email |
| A prediction/recommendation feature | `ml-service` + `api-service` | database, analytics |
| A smart device with a cloud dashboard | `embedded-iot` + `event-processor` + `web-app` | authentication, database |
| A game | `game` (+ `realtime-service` for multiplayer) | analytics, privacy-compliance |
| A tool other developers will install | `library-cli` | — |
| Set up the cloud for all of the above | `infrastructure` | secrets-and-config, deployment |

## Combining archetypes
List several in `.autocraft/config.yml`:
```yaml
archetypes: [mobile-app, api-service]
capabilities: [authentication, database, email-and-notifications]
experience: beginner
```
Agents read every listed profile. For multi-component projects:
- One spec and one architecture doc with a **section per component** and an explicit **contract** between them (usually the API's OpenAPI file or an event schema).
- Work-breakdown tasks are tagged `[mobile]`, `[api]`, `[job]`… and the contract task goes first so components can progress in parallel.
- Components live in one repo (monorepo, the default: `apps/…`, `services/…`) or separate repos (run onboarding per repo).
- Release gates combine: e.g. API changes must stay compatible with old app versions.

## Profile sections (required; checked by `scripts/validate.sh`)
*Summary · In plain words · Questions the requirements-analyst must ask · Architecture must cover · Testing strategy · Walking skeleton · CI & tooling · Where it runs (simple defaults) · Security focus · Release checklist additions · Common pitfalls · Suggested stacks*

## Add your own
Copy the closest profile to `archetypes/<new-name>/archetype.md`, rewrite each section, and add a row above. Ideas: `serverless-backend`, `cms`, `voice-assistant`, `ar-vr-app`, `blockchain-contract`, `mobile-sdk`, `design-system`, `monorepo-platform`.
