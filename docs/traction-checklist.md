# Traction checklist: before announcing

For the maintainer. Nothing here has been done on your behalf: no publish, no push, no announcement. Each item says what to do and whether it was **verified** (someone ran it and the result is recorded) or **NOT verified**. Dates are when the check was made (2026-10-23, by the T31 build agent, in a Linux sandbox with a proxied network).

## 1. Blockers (do not announce until these are closed)

| # | Item | State | Details |
|---|---|---|---|
| 1 | **PyPI name** | **BLOCKER, changed since the docs were written** | `engram-memory` is NOT free: PyPI already has an unrelated project of that name (`engram-memory` 0.4.0, 0.4.1, 0.5.0b1; "Memory layer for AI agents"; MIT; repository Ashish-dwi99/Engram; last upload 2026-02-10). Publishing under that name is impossible, and a near-identical name invites confusion. Checked by querying `https://pypi.org/pypi/<name>/json`: 404 (free) for `engram-agent`, `engram-memory-cli`, `engram-mnemo`, `engram-local`, `engram-cortex`, `engram-daemon`, `engram-sleep`, `mnemo-engram`; 200 (taken) for `engram-memory`, `engram-mem`, `engram-mcp`, `mnemo`, `mnemo-memory`. Availability can change; re-check right before publishing. Decide the name, then change `name` in `pyproject.toml` and every `pipx install engram-memory` / `pip install 'engram-memory[local]'` / `engram-memory` mention (README, `docs/configuration.md`, `integrations/`, `tests/test_packaging.py`). The import package (`engram`) and commands (`mnemo`, `engram-daemon`, `engram-hook`, `engram-mcp`) are independent of the distribution name, but check whether that other project also installs a top-level `engram` package (not checked here), since two distributions sharing an import name overwrite each other. The first PyPI upload of a name is permanent; use TestPyPI first. |
| 2 | License holder and NOTICE | **NOT done** | `LICENSE` is the stock Apache-2.0 text with the unfilled appendix placeholder (`Copyright [yyyy] [name of copyright owner]`); there is no `NOTICE` file and `pyproject.toml` has no `authors`. Decide the copyright holder and year, add a `NOTICE` (Apache-2.0 section 4 only requires it if you want attribution carried), and add `authors` / `license` metadata. Apache-2.0 was the agents' default, not an owner decision (`docs/review-guide.md` section 7). The commit history is authored by "Claude <noreply@anthropic.com>": check that you are comfortable with that as the record. |
| 3 | Repository URL | **NOT verified** | `pyproject.toml` and the README point at `github.com/sak-io/engram`. Confirm that repository exists, is public and has the code on `main` (the README install line says "once this branch is merged to main"). |
| 4 | Merge to `main` | **NOT done** | Humans merge; nothing has reached `main` yet. |

## 2. Verified in this build (by running it)

| Check | Result |
|---|---|
| Wheel builds (`pip wheel . --no-deps`) and installs into a clean venv on Python 3.13; all four entry points are installed | verified 2026-10-23 |
| README quickstart, every command, from the built wheel in a clean venv with a temporary `HOME` and `ENGRAM_HOME` | verified 2026-10-23 (outputs in the T31 report) |
| Daemon wire protocol: error replies listed in `docs/api.md` were produced by a live daemon over the Unix socket and over TCP (`auth`, `unauthorized`) | verified 2026-10-23 |
| `docker build` of the `Dockerfile`, then `docker run` with the compose hardening (read-only root, `--cap-drop ALL`, `no-new-privileges`, volume at `/data`, token from the environment): container reaches `healthy`, runs as uid 10001, `mnemo status --strict`, `remember` and `recall` work inside it, data survives `docker restart`, refuses to start without a token or with a short one (exit 2) | verified 2026-10-23, **with limits**: the sandbox daemon had no network bridge, so the build ran with `--network none` and the container with `--network none`; `docker compose`, the published `127.0.0.1:47600` port and a host-side client over that port were NOT exercised |
| Full unit/integration suite on Python 3.13 and 3.9 | see the T31 report and `docs/architecture/status.md` |

## 3. Not verified: do these yourself

| # | Item | Why it matters | How |
|---|---|---|---|
| 5 | **Real Claude Code session with the hooks** | Hooks were only run by invoking the installed command with JSON shaped like the documented payloads. Unconfirmed live: that Claude Code accepts the exec-form `args`, honours `timeout`, injects `additionalContext` at SessionStart, passes `last_assistant_message` on Stop, and fires SessionEnd/PreCompact as documented. | `mnemo install claude-code`, start `claude`, send a prompt, then `mnemo status` (journal count rises) and `mnemo dump`; start a second session and look for the "Recalled memory" briefing; run with `ENGRAM_HOOK_DEBUG=1`. Add `--mcp` and check the `recall` tool is listed. |
| 6 | **Real Cursor** | Config paths, the `mcpServers` shape, the `.mdc` frontmatter and reload behaviour are from recalled documentation. | `mnemo install cursor --dry-run`, then install, restart Cursor, check the Engram server appears and `recall` is callable; try `--scope project` for the rule file. |
| 7 | **Codex, Gemini CLI, Grok** (MCP only) | `mnemo install --print mcp` prints a snippet; where each host wants it (`~/.codex/config.toml`, `~/.gemini/settings.json`) is unchecked, and these hosts get no hooks, so nothing is captured automatically. | Paste the snippet, restart the host, ask it to call `recall`. |
| 8 | **Real embedding model quality** (`fastembed:<model>`) | The model could not be downloaded where this was built. The path is verified up to the package import, the real "cannot load, fall back to hashed" path and a stubbed model. Real cosine distributions, speed and recall quality are unmeasured, so every embedder ships the conservative hashed thresholds (`NEAR_DUPLICATE` 0.88, `SEM_GAIN` 1.8, `SEM_MIN_COS` 0.30, recombine band 0.34 to 0.88). | `pip install '<dist>[local]'`, `ENGRAM_EMBEDDER=fastembed:sentence-transformers/all-MiniLM-L6-v2`, measure unrelated-pair and related-pair cosines on a real corpus, run `tests/test_dedup_dense.py` ideas with the real model (opposite statements must not merge), then tune per-embedder thresholds in `vectorize.py` (ADR 0010). Re-measure recall@3 (`tests/test_recall_regression.py`; hashed baseline 0.953 tuning set, 0.833 held-out against a 0.85 target). |
| 9 | **Real remote providers** (`openai:<model>` embeddings, `ENGRAM_LLM_API=openai\|anthropic`) | Every test used a stub server on loopback. Real TLS, response shapes, rate limiting and latency (the 1 s default embedding timeout will often fall back) are unverified. | Use a throwaway key and a non-sensitive store; check `mnemo status`, `mnemo reembed --dry-run` then `--yes`. |
| 10 | **Distillation quality** (host callback and API) | Never run with a real model; quality is unmeasured. The gate often drops plain procedures and open issues (lessons.md). | Enable `ENGRAM_LLM_CONSOLIDATE=1` on a scratch store and read the results of `mnemo dump`. |
| 11 | **Docker with compose and a host client** | See section 2 for the part that was run. | `export ENGRAM_TOKEN=...; docker compose up -d --build; ENGRAM_PORT=47600 mnemo status`; `python3 -m unittest scripts.test_docker -v` on a machine with a working docker daemon (it is skipped otherwise). |
| 12 | **macOS** | CI is ubuntu only; the code uses only POSIX features. Unix socket path length limits differ (the daemon falls back to loopback TCP over 104 bytes). | Run the suite on macOS; add it to the CI matrix. |
| 13 | **Permission-denied tests** | Skipped because the sandbox ran as root. | Run the suite as a normal user. |
| 14 | **CI on `main`** | The workflow (Python 3.9, 3.11, 3.13, 3.14 and the wheel `package` job) has not run on GitHub for this branch. | Check the first run after merging. |
| 15 | **Security scan** | No SCA / SAST / secret-scan report exists. The package has no runtime dependencies; the `fastembed` extra does. | Run the `security-auditor` (`/security-scan`) and a secret scan before publishing. |
| 16 | **Human code review** | Every review so far was by AI agents. | See `docs/review-guide.md`. |
| 17 | **Scale and soak** | Write cost measured to 20k memories on a loaded sandbox; larger stores and long-lived daemons untested. | `scripts/bench_write.py`, `ENGRAM_PERF=1`. |

## 4. Signals to watch after announcing

From the product concept: GitHub engagement, unprompted requests for other agents' integrations, and mentions in adjacent communities. There is no telemetry in Engram and none should be added without consent; these are manual observations.
