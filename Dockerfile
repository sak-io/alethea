# Alethea sidecar: the daemon in container mode (TCP only, token mandatory). See docs/docker.md.
# Stdlib-only: no pip install, no third-party packages, no build stage needed.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    ALETHEA_HOME=/data \
    ALETHEA_CONTAINER=1 \
    ALETHEA_BIND=0.0.0.0 \
    ALETHEA_PORT=47600
# ALETHEA_TOKEN is deliberately NOT set here: the daemon refuses to start without it
# (supply it at run time, never at build time).

RUN groupadd --system --gid 10001 alethea \
 && useradd --system --uid 10001 --gid alethea --no-create-home --shell /usr/sbin/nologin alethea \
 && install -d -o alethea -g alethea -m 0700 /data

COPY alethea /app/alethea
COPY scripts/mnemo /app/scripts/mnemo

USER alethea:alethea
WORKDIR /data
VOLUME ["/data"]
EXPOSE 47600

# `mnemo status --strict` talks to the daemon over loopback TCP with ALETHEA_TOKEN (exit 3 if down).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "/app/scripts/mnemo", "status", "--strict"]

STOPSIGNAL SIGTERM
CMD ["python3", "-m", "alethea.daemon"]
