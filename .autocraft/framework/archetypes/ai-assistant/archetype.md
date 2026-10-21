# Archetype: ai-assistant

## Summary
An application powered by a large language model (LLM): chatbots, copilots, document Q&A (RAG), AI agents that use tools, content generation features.

## In plain words
"I want an AI that talks to people / answers questions about our documents / does tasks for them." Unlike normal code, its answers vary — so you *measure* quality with test sets and add guardrails.

## Typical products
Support chatbots, knowledge-base Q&A, writing/summarizing assistants, agents that call tools or APIs.

## Questions the requirements-analyst must ask
1. What should the assistant do and — just as important — **never** do? (default: ask)
2. What knowledge does it rely on (your documents, a database, the web)? (default: your documents)
3. Can it take actions (send emails, change data, spend money)? Who approves? (default: read-only; human approves actions)
4. What data may it see and must it never reveal? (default: no personal data in prompts without consent)
5. Cost and speed limits per conversation? (default: set a monthly budget cap)

## Architecture must cover
Model choice and provider abstraction (swap models), prompt/system-prompt management (versioned), retrieval pipeline if RAG (chunking, embeddings, vector store, citations), **tool/function calling with permissions and human approval for risky actions**, conversation memory, streaming UX, **guardrails** (input/output filtering, refusal behavior, prompt-injection defenses), **evaluation harness** (golden Q&A set, regression scoring), logging with redaction, rate limiting and cost caps, fallbacks and timeouts, latency/caching.

## Testing strategy
- **Unit:** prompt builders, parsers, tool wrappers, retrieval ranking.
- **Evals:** a versioned set of realistic questions with expected qualities (correct, grounded in sources, safe); run on every prompt/model change; score thresholds in CI (using a cheap model or recorded outputs).
- **Safety tests:** prompt-injection attempts, requests for forbidden content, attempts to exfiltrate other users' data.
- **Integration:** tool calls hit sandboxed/test systems; timeouts and provider errors handled.
- **Cost tests:** token usage per conversation under budget.

## Walking skeleton (task 1)
A chat endpoint/UI that calls the model with a versioned system prompt, streams a reply, logs redacted traces, enforces a cost cap, and runs a 5-question eval in CI.

## CI & tooling
Lint, type-check, unit tests, eval run (recorded/mocked by default, live on a schedule), dependency audit, secret scan (API keys!).

## Where it runs (simple defaults)
Same as the host app (serverless/managed platform). Model via a provider API; vector store as a managed service or Postgres+pgvector.

## Security focus
**Prompt injection** (documents or web pages that instruct the model), data leakage across users, over-permissive tools, API-key protection and spend caps, PII in prompts/logs, unsafe output rendering (XSS), jailbreaks.

## Release checklist additions
Eval scores meet thresholds with no regression; red-team checklist run; spend cap and alerts set; human-approval for risky tools verified; logging redaction verified; fallback message if the provider is down; usage disclosure to users; rollback = revert prompt/model version.

## Common pitfalls (reviewers look for these)
No evals ("it seemed fine"); trusting model output as code/SQL/HTML; secrets in prompts; unlimited tool power; no cost cap; prompts edited in production with no versioning; RAG with no citations; ignoring prompt injection.

## Suggested stacks
Python or TypeScript with the Anthropic SDK → `python | node`.
