---
id: ai-product
name: AI Product Team
summary: For LLM-powered features: chatbots, assistants, document Q&A, agents, bots — evaluation-first, with injection and cost defences.
fits: [ai-assistant, chat-bot]
roles: [requirements-analyst, architect, principal-engineer, ai-engineer, backend-developer, frontend-developer, qa-engineer, pr-reviewer, security-auditor, release-gatekeeper, delivery-lead]
optional: [ux-designer, devops-engineer, ml-engineer, technical-writer]
workflows: [discover, explain, start, design-architecture, build-feature, review-pr, release-check, security-scan, status]
---

# Playbook: AI Product Team

**Spec (incl. what it must NEVER do) → Architecture → Eval set → Build → Red-team → Release → You merge.**

1. The spec lists allowed and forbidden behaviours, data it may see, and actions it may take (with human approval).
2. `architect` designs provider abstraction, RAG/tools, guardrails, logging redaction, cost caps.
3. `ai-engineer` and `qa-engineer` **write the eval set before the feature**; prompts are versioned files; CI runs evals.
4. `security-auditor` red-teams: prompt injection, data leakage between users, over-powerful tools.
5. `release-gatekeeper` requires eval scores with no regression, spend caps, fallback when the provider is down.
