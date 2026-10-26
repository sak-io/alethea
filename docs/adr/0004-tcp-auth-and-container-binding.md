# ADR 0004: TCP bind policy and token authentication

Status: Accepted (architect proposal, implemented and reviewed) | Implements owner decision G14 | Threat: local-data exposure

**Context.** Owner: TCP binds 127.0.0.1 by default; `ALETHEA_TOKEN` required in container mode. Problem: a daemon bound to 127.0.0.1 *inside* a container is unreachable through a published port.

**Decision.**
- Default (non-container TCP fallback): bind 127.0.0.1, ephemeral or `ALETHEA_PORT`; token optional but enforced if `ALETHEA_TOKEN` is set.
- Container mode (`ALETHEA_CONTAINER=1`): bind address comes from `ALETHEA_BIND` (compose sets `0.0.0.0` inside the container); daemon **refuses to start** without `ALETHEA_TOKEN` (min 16 chars). `docker-compose.yml` publishes only `127.0.0.1:PORT:PORT`.
- Wire: every request line carries `"token"`; compared with `hmac.compare_digest`; failure returns `{"ok":false,"error":"auth"}` and closes. Token is never logged or echoed in `status`/`dump`. Clients read it from env or a 0600 config file.
- Unix socket mode: socket 0600 in a 0700 dir, no token needed.
- Request size cap (1 MiB per line) and read timeout to stop trivial DoS.

**Alternatives rejected.** mTLS (stdlib possible but heavy for a loopback tool); no auth with loopback publish (any local user/process could read all memories).
