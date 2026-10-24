# ADR 0009: Hooks ship with the installed package, not with the source repo

Status: accepted (owner decision, P2b). Supersedes the T23 choice to commit hooks in this repo's `.claude/settings.json`.

## Context
T23 committed a `hooks` block in this repository's `.claude/settings.json`, so every contributor who opened the repo in Claude Code had their prompts journaled to `~/.alethea`. The repository is public. Contributors did not opt in, and their prompts may contain material unrelated to Alethea. The unit users actually run is the installed product, not a source checkout.

## Decision
- Hooks are provided by the installed package through `mnemo install claude-code` (`alethea/install.py`, the single source of truth for the hooks block). They are not enabled in the source repo: `.claude/settings.json` holds permissions only, and a test (`tests/test_wiring.py`) fails if a `hooks` key returns.
- The repo's `.mcp.json` stays; it only exposes the `recall`/`remember` tools when the agent asks.
- The installer runs hooks as `<interpreter> -I -m alethea.hooks.alethea_hook` (exec form), where `<interpreter>` is the Python that has Alethea installed, so there is no PATH or checkout-location dependence.
- Scopes: `user` (default), `local` (uncommitted), `project` (committed; the installer warns that it captures every contributor).
- Packaging: `pyproject.toml`, distribution name `alethea` (free on PyPI when checked 2026-10-24; nothing has been published), runtime stays stdlib-only.

## Consequences
- Contributors are not captured by default; capture is an explicit, reversible per-user action (`mnemo uninstall claude-code`).
- Developers working on Alethea who want dogfooding run `mnemo install claude-code --scope local` (or user) from a dev install (`pip install -e .`).
- The hooks block lives in one place; `integrations/claude-code/settings.hooks.json` is generated from it and a test keeps them equal.
- Editing a user's settings file is now our responsibility: merge-only, idempotent, atomic, symlink-refusing, backup on first change (tested).
- Moving or recreating the Python environment invalidates the recorded interpreter path; rerun the installer.
- Other hosts (Cursor) and automatic update of recorded paths remain future work (T29/T30 remainder).
