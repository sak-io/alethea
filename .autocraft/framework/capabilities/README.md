# Capabilities

**Archetypes** say *what kind of thing* you are building. **Capabilities** are the things almost every product also needs — and that beginners don't know to ask for. Each is a short checklist the agents apply automatically.

| Capability | You need it if people say… |
|---|---|
| [authentication](authentication.md) | "login", "accounts", "admin", "roles" |
| [payments](payments.md) | "checkout", "subscription", "billing" |
| [database](database.md) | "save", "remember", "history", "orders" |
| [email-and-notifications](email-and-notifications.md) | "receipts", "reminders", "newsletter", "push" |
| [file-uploads](file-uploads.md) | "upload photos/documents" |
| [search](search.md) | "find", "filter", "autocomplete" |
| [analytics-observability](analytics-observability.md) | "know when it breaks", "usage stats" |
| [secrets-and-config](secrets-and-config.md) | "API key", "connect to X" (always applies) |
| [deployment-hosting](deployment-hosting.md) | "put it online", "go live" (always applies) |
| [privacy-compliance](privacy-compliance.md) | "personal data", "GDPR", "children", "health" |

`/discover` detects these from your description (using each file's *Trigger words*) and records them in `.autocraft/config.yml → capabilities`. `secrets-and-config`, `deployment-hosting` and `analytics-observability` are always on.

Format (checked by `scripts/validate.sh`): *In plain words · Trigger words · Safe defaults · Must test · Common mistakes · Don't build it yourself*. Add your own by copying a file.
