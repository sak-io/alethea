# Capability: analytics-observability

## In plain words
Knowing what your product is doing and when it breaks: error tracking, logs, metrics, alerts, and (separately) product analytics about how people use it.

## Trigger words
monitor, errors, crashes, logs, alert, analytics, metrics, uptime, dashboard, tracking, performance

## Safe defaults
- Error tracking from day one (Sentry-style) in every component, including mobile and jobs
- Structured logs (JSON) with a request/correlation id; **never log secrets or personal data**
- Health endpoint + uptime check; alert someone who will actually act (email/phone), on symptoms users feel (errors, latency, failed jobs, missed runs)
- Basic metrics: request rate, errors, latency, queue lag, job success; one simple dashboard
- Product analytics: privacy-friendly, with consent where required; track a few key events, not everything
- Write a short runbook: 'if X alerts, check Y'

## Must test
- An intentional error reaches the error tracker with context but no secrets
- Alert fires in staging when the health check is forced to fail
- Log redaction removes tokens and personal data
- Missed scheduled run triggers an alert

## Common mistakes
No alerts until after the outage; alerts that nobody reads; logging passwords/tokens/personal data; tracking users without consent; dashboards no one looks at

## Don't build it yourself
Monitoring stacks. Use a hosted error tracker, platform logs/metrics and an uptime service.
