# Docker sidecar

Runs the Alethea daemon in a container so several tools on one machine share a memory store without a Unix socket. Files: `Dockerfile`, `docker-compose.yml`, `.dockerignore`. Decision record: ADR 0013 (policy: ADR 0004).

**Status: the Dockerfile and compose file are covered by static tests. In T31 the image was built (`docker build --network none`) and run once with the compose hardening (read-only root, all capabilities dropped, `no-new-privileges`, uid 10001): it became healthy, `mnemo status --strict`, `remember` and `recall` worked inside it, data survived a restart, and it refused to start without a valid token. NOT verified: `docker compose up`, the published `127.0.0.1:47600` port with a host-side client, and `scripts.test_docker` (needs a reachable docker daemon; skipped otherwise). Run `python3 -m unittest scripts.test_docker -v` on a machine with docker to verify.**

## Start

```sh
export ALETHEA_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"   # >= 16 bytes
docker compose up -d --build
```

Compose refuses to start while `ALETHEA_TOKEN` is unset or empty; there is no default token. Keep the token out of shell history and VCS (for example a `0600` `.env` file next to the compose file, which `.gitignore`/`.dockerignore` must keep out; `.env` is excluded from the image build context). Host port defaults to 47600; override with `ALETHEA_HOST_PORT`.

## Connect a host-side client

On the host (hooks, `mnemo`, the MCP server) set the same values:

```sh
export ALETHEA_PORT=47600            # selects TCP; also disables autostart of a local daemon
export ALETHEA_TOKEN=...             # same secret
mnemo status
```

`ALETHEA_HOST` defaults to `127.0.0.1`, which matches the loopback-only publish, so the client sends the token. Do not set `ALETHEA_CONTAINER=1` on the host unless you want TCP-only there as well (it only turns off Unix socket use and autostart, which `ALETHEA_PORT` already does). For a Claude Code host, set the variables in the environment that launches Claude Code, then `mnemo install claude-code`.

## Container mode semantics (`ALETHEA_CONTAINER=1`)

- TCP only: no Unix socket. `ALETHEA_BIND` (set to `0.0.0.0` inside the container) and `ALETHEA_PORT` must be explicit, and a token of at least 16 bytes is mandatory; otherwise the daemon exits with code 2 and a message (the container then restarts in a loop, so check `docker compose logs alethea`).
- Data lives in the `alethea-data` volume mounted at `/data` (`ALETHEA_HOME`), directory mode 0700, owned by uid 10001. `docker compose down` keeps it; `docker compose down -v` deletes all memories. Back up by copying the volume while the container is stopped (there is no export command for the whole store).
- The daemon runs as non-root uid 10001, with a read-only root filesystem, all capabilities dropped and `no-new-privileges`. The image has no third-party packages and no pip step.
- Health check: `mnemo status --strict` inside the container, over loopback TCP with the token from the environment.
- The client never autostarts a daemon in this mode; if the container is down, hooks and `mnemo` are fail-soft (offline), as usual.

## Threat notes

- Memory text can contain anything the agent saw. Anyone who can reach the port with the token can read and write all of it; the token is the only credential.
- The compose file publishes `127.0.0.1` only. Any local user or process on the host can still connect to that port, so they need the token: keep it private, rotate it by changing the variable and `docker compose up -d`.
- There is no TLS. Never publish the port on a non-loopback address; for remote use put an SSH tunnel or VPN in front. The client refuses to send the token to a non-loopback host unless `ALETHEA_ALLOW_REMOTE_TOKEN` is set (plaintext).
- The token is an environment variable of the container: `docker inspect` and `/proc/<pid>/environ` show it to anyone with access to the docker socket (effectively root). The daemon also accepts a `0600` file `<ALETHEA_HOME>/token`, which is not used by the compose file. Never bake it into an image or build argument (a static test forbids `ALETHEA_TOKEN` in `ENV`/`ARG`).
- Failed auth attempts are rate limited by the daemon; requests are capped at 1 MiB per line (ADR 0004).
- Remote embedder/LLM keys (`ALETHEA_LLM_API_KEY`, etc.) are not passed by the compose file; add them to `environment:` deliberately if you opt in, and note the privacy warnings in `docs/configuration.md`.
