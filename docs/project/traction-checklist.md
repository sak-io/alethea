# Traction checklist: before announcing

For the maintainer. Nothing here has been done on your behalf: no publish, no push, no announcement. Each item says what to do and whether it was **verified** (someone ran it and the result is recorded) or **NOT verified**. Dates are when the check was made (2026-10-23, by the T31 build agent, in a Linux sandbox with a proxied network).

## 1. Blockers (do not announce until these are closed)

| # | Item | State | Details |
|---|---|---|---|
| 1 | **PyPI name** | Decided: `alethea` | The distribution name is `alethea` (the owner chose it; the earlier candidate was taken by an unrelated project). Verified free on PyPI and npm on 2026-10-24 by querying `https://pypi.org/pypi/alethea/json` (404). Availability can change; re-check right before publishing. The import package (`alethea`) and commands (`mnemo`, `alethea-daemon`, `alethea-hook`, `alethea-mcp`) share the name. The first PyPI upload of a name is permanent; use TestPyPI first. Nothing is published yet. |
| 2 | License holder and NOTICE | Partly done (2026-10-26) | `LICENSE` now reads `Copyright 2026 sak-io` (the owner chose the pseudonym) and `pyproject.toml` lists `sak-io` as author. Still open: a `NOTICE` file is optional (Apache-2.0 section 4 only requires it if you want attribution carried). Apache-2.0 was the agents' default, confirmed by the owner. The commit history is authored by "Claude <noreply@anthropic.com>": check that you are comfortable with that as the record. |
| 3 | Repository URL | Done (2026-10-26) | The repository was renamed to `github.com/sak-io/alethea`; `pyproject.toml`, the README and the integrations README point at it. The code is on `main`. |
| 4 | Merge to `main` | **NOT done** | Humans merge; nothing has reached `main` yet. |

## 2. Verified in this build (by running it)

| Check | Result |
|---|---|
| Wheel builds (`pip wheel . --no-deps`) and installs into a clean venv on Python 3.13; all four entry points are installed | verified 2026-10-23 |
| README quickstart, every command, from the built wheel in a clean venv with a temporary `HOME` and `ALETHEA_HOME` | verified 2026-10-23 (outputs in the T31 report) |
| Daemon wire protocol: error replies listed in `docs/reference/api.md` were produced by a live daemon over the Unix socket and over TCP (`auth`, `unauthorized`) | verified 2026-10-23 |
| `docker build` of the `Dockerfile`, then `docker run` with the compose hardening (read-only root, `--cap-drop ALL`, `no-new-privileges`, volume at `/data`, token from the environment): container reaches `healthy`, runs as uid 10001, `mnemo status --strict`, `remember` and `recall` work inside it, data survives `docker restart`, refuses to start without a token or with a short one (exit 2) | verified 2026-10-23, **with limits**: the sandbox daemon had no network bridge, so the build ran with `--network none` and the container with `--network none`; `docker compose`, the published `127.0.0.1:47600` port and a host-side client over that port were NOT exercised |
| Full unit/integration suite on Python 3.13 and 3.9 | see the T31 report and `docs/architecture/status.md` |

## 3. Not verified: do these yourself

| # | Item | Why it matters | How |
|---|---|---|---|
| 5 | **Real Claude Code session with the hooks** | Hooks were only run by invoking the installed command with JSON shaped like the documented payloads. Unconfirmed live: that Claude Code accepts the exec-form `args`, honours `timeout`, injects `additionalContext` at SessionStart, passes `last_assistant_message` on Stop, and fires SessionEnd/PreCompact as documented. | `mnemo install claude-code`, start `claude`, send a prompt, then `mnemo status` (journal count rises) and `mnemo dump`; start a second session and look for the "Recalled memory" briefing; run with `ALETHEA_HOOK_DEBUG=1`. Add `--mcp` and check the `recall` tool is listed. |
| 6 | **Real Cursor** | Config paths, the `mcpServers` shape, the `.mdc` frontmatter and reload behaviour are from recalled documentation. | `mnemo install cursor --dry-run`, then install, restart Cursor, check the Alethea server appears and `recall` is callable; try `--scope project` for the rule file. |
| 7 | **Codex, Gemini CLI, Grok** (MCP only) | `mnemo install --print mcp` prints a snippet; where each host wants it (`~/.codex/config.toml`, `~/.gemini/settings.json`) is unchecked, and these hosts get no hooks, so nothing is captured automatically. | Paste the snippet, restart the host, ask it to call `recall`. |
| 8 | **Real embedding model quality** (`fastembed:<model>`) | The model could not be downloaded where this was built. The path is verified up to the package import, the real "cannot load, fall back to hashed" path and a stubbed model. Real cosine distributions, speed and recall quality are unmeasured, so plugin embedders ship the conservative hashed thresholds (`NEAR_DUPLICATE` 0.88, `SEM_GAIN` 1.8, `SEM_MIN_COS` 0.30, recombine band 0.34 to 0.88); the hashed embedder itself uses `SEM_MIN_COS` 0.35 (ADR 0014). | `pip install '<dist>[local]'`, `ALETHEA_EMBEDDER=fastembed:sentence-transformers/all-MiniLM-L6-v2`, measure unrelated-pair and related-pair cosines on a real corpus, run `tests/test_dedup_dense.py` ideas with the real model (opposite statements must not merge), then tune per-embedder thresholds in `vectorize.py` (ADR 0010). Re-measure recall@3 (`tests/test_recall_regression.py`; hashed baseline 0.953 tuning set, 0.833 held-out against a 0.85 target). |
| 9 | **Real remote providers** (`openai:<model>` embeddings, `ALETHEA_LLM_API=openai\|anthropic`) | Every test used a stub server on loopback. Real TLS, response shapes, rate limiting and latency (the 1 s default embedding timeout will often fall back) are unverified. | Use a throwaway key and a non-sensitive store; check `mnemo status`, `mnemo reembed --dry-run` then `--yes`. |
| 10 | **Distillation quality** (host callback and API) | Never run with a real model; quality is unmeasured. The gate often drops plain procedures and open issues (lessons.md). | Enable `ALETHEA_LLM_CONSOLIDATE=1` on a scratch store and read the results of `mnemo dump`. |
| 11 | **Docker with compose and a host client** | See section 2 for the part that was run. | `export ALETHEA_TOKEN=...; docker compose up -d --build; ALETHEA_PORT=47600 mnemo status`; `python3 -m unittest scripts.test_docker -v` on a machine with a working docker daemon (it is skipped otherwise). |
| 12 | **macOS** | CI is ubuntu only; the code uses only POSIX features. Unix socket path length limits differ (the daemon falls back to loopback TCP over 104 bytes). | Run the suite on macOS; add it to the CI matrix. |
| 13 | **Permission-denied tests** | Skipped because the sandbox ran as root. | Run the suite as a normal user. |
| 14 | **CI on `main`** | The workflow (Python 3.9, 3.11, 3.13, 3.14 and the wheel `package` job) has not run on GitHub for this branch. | Check the first run after merging. |
| 15 | **Security scan** | No SCA / SAST / secret-scan report exists. The package has no runtime dependencies; the `fastembed` extra does. | Run the `security-auditor` (`/security-scan`) and a secret scan before publishing. |
| 16 | **Human code review** | Every review so far was by AI agents. | See `docs/project/review-guide.md`. |
| 17 | **Scale and soak** | Write cost measured to 20k memories on a loaded sandbox; larger stores and long-lived daemons untested. | `scripts/bench_write.py`, `ALETHEA_PERF=1`. |

## 4. Signals to watch after announcing

From the product concept: GitHub engagement, unprompted requests for other agents' integrations, and mentions in adjacent communities. There is no telemetry in Alethea and none should be added without consent; these are manual observations.
