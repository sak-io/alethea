# ADR 0002: Package layout and script entry points

Status: Accepted (architect proposal, implemented and reviewed) | Refines T§2 | Gaps: S G26

**Context.** T§2 draws everything under `alethea/`, but Claude Code uses `-m alethea.mcp_server` (needs a package) while Cursor uses `python3 /abs/path/mcp_server.py` (runs as a script, where sibling imports break).

**Decision.** Repo root contains the package `alethea/` (all modules from T§2, plus `alethea/hooks/alethea_hook.py`) and at repo root: `scripts/` (mnemo, e2e/concurrency/hook tests, show_mcp.py), `integrations/`, `tests/` (per-module unit tests, `tests/__init__.py`), `.mcp.json`, `.claude/settings.json`, `Dockerfile`, `docker-compose.yml`, `LICENSE` (Apache-2.0). Entry points (`mcp_server.py`, `alethea_hook.py`, `daemon.py`, `mnemo`) begin with a 3-line bootstrap that inserts the repo root into `sys.path` when `__package__` is empty, so both `-m` and absolute-path invocation work. Run all tests from repo root: `python3 -m unittest discover -s tests -t .` (scripts tests are in `tests/` or imported from `scripts/`).

**Alternatives rejected.** Flat layout at repo root (no package: breaks `-m alethea.mcp_server`); `pip install -e` (violates no-install).

**Consequence.** One test per entry style is mandatory (Lesson 4).
