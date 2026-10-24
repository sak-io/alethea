# ADR 0013: Docker sidecar packaging

Status: Accepted (developer decision, T28; for reviewer and owner confirmation) | Implements R29, G14 | Extends ADR 0004

**Context.** ADR 0004 fixed the policy (container mode, token mandatory, loopback publish). T28 had to choose how the image is built and how the secret reaches it.

**Decision.**
- The token comes only from the `ALETHEA_TOKEN` environment variable via compose `${ALETHEA_TOKEN:?...}`: compose fails when it is unset, and there is no default. It is never an `ENV`/`ARG` in the Dockerfile and never in the build context (`.env`, `token`, keys are in `.dockerignore`).
- The image copies only `alethea/` and `scripts/mnemo` (no `pip install`, no project metadata, `PYTHONPATH=/app`), so there is nothing to resolve or download at build time. The entry point is `python3 -m alethea.daemon`; the health check is `mnemo status --strict` (authenticated, so a wrong token makes the container unhealthy).
- Runs as uid 10001 with `/data` pre-created 0700, read-only root fs, `cap_drop: ALL`, `no-new-privileges`.
- Base image `python:3.13-slim` by tag (the code supports 3.9+); not digest-pinned.

**Alternatives rejected.** Docker/Compose secrets (a file `/run/secrets/...` that the daemon does not read; would need a code change, and the env var is already visible to whoever controls the docker socket). `pip install .` in the image (adds build-time network and a wheel step for no gain). A default dev token (would be a published credential).

**Consequences.** The token is visible to `docker inspect` for users with docker access. Unpinned base tag: rebuilds can change the image; pin a digest for production. Live build is covered by `scripts/test_docker.py`, which skips without a docker daemon.
