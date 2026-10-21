# ADR 0002: Package layout and script entry points

Status: Proposed (architect) | Refines T§2 | Gaps: S G26

**Context.** T§2 draws everything under `engram/`, but Claude Code uses `-m engram.mcp_server` (needs a package) while Cursor uses `python3 /abs/path/mcp_server.py` (runs as a script, where sibling imports break).

**Decision.** Repo root contains the package `engram/` (all modules from T§2, plus `engram/hooks/engram_hook.py`) and at repo root: `scripts/` (mnemo, e2e/concurrency/hook tests, show_mcp.py), `integrations/`, `tests/` (per-module unit tests, `tests/__init__.py`), `.mcp.json`, `.claude/settings.json`, `Dockerfile`, `docker-compose.yml`, `LICENSE` (Apache-2.0). Entry points (`mcp_server.py`, `engram_hook.py`, `daemon.py`, `mnemo`) begin with a 3-line bootstrap that inserts the repo root into `sys.path` when `__package__` is empty, so both `-m` and absolute-path invocation work. Run all tests from repo root: `python3 -m unittest discover -s tests -t .` (scripts tests are in `tests/` or imported from `scripts/`).

**Alternatives rejected.** Flat layout at repo root (no package: breaks `-m engram.mcp_server`); `pip install -e` (violates no-install).

**Consequence.** One test per entry style is mandatory (Lesson 4).
