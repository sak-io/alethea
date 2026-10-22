# Capability: secrets-and-config

## In plain words
Passwords, API keys and settings that differ between your laptop, testing and production. Leaked keys are the #1 cause of cloud bills and breaches.

## Trigger words
API key, token, secret, password, environment, config, .env, credentials, connect to

## Safe defaults
- Secrets live in the platform's secret store / CI secrets — **never in code, chat, issues, logs or screenshots**
- Local `.env` files are git-ignored; commit a `.env.example` with fake values
- Separate secrets per environment; least-privilege keys; rotate regularly and after any suspected leak
- Config validated at startup (fail fast with a clear message); sane defaults for non-secrets
- Secret scanning + push protection on; a leaked key is **rotated immediately** (deleting the commit isn't enough)
- Frontend/mobile apps can't keep secrets — anything shipped to a client is public

## Must test
- App fails fast with a clear error when required config is missing
- Secret scan passes (no keys in repo history)
- No secrets appear in logs or error output
- Different environments use different credentials

## Common mistakes
Committing .env; one key for everything; keys in the mobile/web bundle; sharing secrets in chat; never rotating; logging config objects

## Don't build it yourself
Secret storage. Use the platform secret manager (cloud, GitHub Actions secrets, Doppler/Vault).
