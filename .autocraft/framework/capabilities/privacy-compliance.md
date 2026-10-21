# Capability: privacy-compliance

## In plain words
Handling people's personal data lawfully and respectfully: knowing what you collect, why, who sees it, and how a person can ask you to delete it.

## Trigger words
personal data, GDPR, privacy, consent, cookies, children, health, financial, delete my data, terms, policy, PII

## Safe defaults
- Collect the minimum; write down what you store, why, where, and for how long (a one-page data map)
- Privacy policy and terms published; cookie/analytics consent where required; age rules if children may use it
- Users can export and delete their data; deletion actually removes it from backups on a schedule and from third parties
- Encrypt in transit and at rest; restrict who can see personal data; don't copy production data to dev
- Breach plan: who to tell and how fast (often 72 hours under GDPR)
- Health, payments, children's data: **stop and involve a legal/compliance professional** (a human decision)

## Must test
- Data export and deletion flows work for a test user
- Personal data doesn't appear in logs, analytics or error trackers
- Consent choices are respected (no tracking before consent)
- Retention job removes expired data

## Common mistakes
Collecting 'just in case'; no deletion path; personal data in logs; copying production data to laptops; ignoring cookie rules; using third-party SDKs that quietly collect data

## Don't build it yourself
Legal advice. Use templates as a start, and get a qualified human to review for anything regulated.
