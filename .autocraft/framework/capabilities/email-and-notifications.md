# Capability: email-and-notifications

## In plain words
Sending messages to users: emails (welcome, receipts, resets), push notifications, SMS. Deliverability, consent and spam rules are surprisingly strict.

## Trigger words
email, notify, notification, alert, reminder, newsletter, push, SMS, receipt, welcome

## Safe defaults
- Use a transactional provider (Postmark/SendGrid/SES/Resend); set up SPF, DKIM, DMARC on your domain
- Send from a queue/background job, not inside the user's request; retry with backoff; dedupe
- Templates in the repo, tested with sample data; plain-text alternative included
- Always honor unsubscribe/preferences for non-essential messages; record consent
- Rate-limit per user to avoid spam/abuse; never put secrets in message bodies or links without expiry
- Use a sandbox/test inbox outside production — never email real customers from dev

## Must test
- Template renders with missing/odd data
- Duplicate trigger sends one message
- Provider failure retries and then alerts
- Unsubscribed users are not messaged
- Links (reset, verify) expire and are single-use

## Common mistakes
Sending synchronously inside requests; no retries; emailing real users from test environments; missing unsubscribe; HTML-only emails; sending too fast and getting blocked; secrets in URLs

## Don't build it yourself
Mail servers and deliverability. Use a transactional email provider.
