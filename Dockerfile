# Engram sidecar: the daemon in container mode (TCP only, token mandatory). See docs/docker.md.
# Stdlib-only: no pip install, no third-party packages, no build stage needed.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ENGRAM_HOME=/data \
    ENGRAM_CONTAINER=1 \
    ENGRAM_BIND=0.0.0.0 \
    ENGRAM_PORT=47600
# ENGRAM_TOKEN is deliberately NOT set here: the daemon refuses to start without it
# (supply it at run time, never at build time).

RUN groupadd --system --gid 10001 engram \
 && useradd --system --uid 10001 --gid engram --no-create-home --shell /usr/sbin/nologin engram \
 && install -d -o engram -g engram -m 0700 /data

COPY engram /app/engram
COPY scripts/mnemo /app/scripts/mnemo

USER engram:engram
WORKDIR /data
VOLUME ["/data"]
EXPOSE 47600

# `mnemo status --strict` talks to the daemon over loopback TCP with ENGRAM_TOKEN (exit 3 if down).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "/app/scripts/mnemo", "status", "--strict"]

STOPSIGNAL SIGTERM
CMD ["python3", "-m", "engram.daemon"]
