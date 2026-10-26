"""Constants and config loading. Precedence: env > config.json > defaults. No secrets in code."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1
HASH_SALT = b"alethea-v1"
DOWNSCALE_INTERVAL_S = 6 * 3600
MAX_REQUEST_BYTES = 1 << 20

DEFAULTS = {
    "ALETHEA_HOME": "~/.alethea",
    "ALETHEA_EMBEDDER": "hash",
    "ALETHEA_EMBEDDER_LOAD_TIMEOUT": 120,   # seconds the model may take to load (background)
    "ALETHEA_EMBEDDER_TIMEOUT": "1.0",      # seconds one embedding call may take (float)
    "ALETHEA_SLEEP_INTERVAL": 900,
    "ALETHEA_IDLE_SLEEP": 180,
    "ALETHEA_LLM_CONSOLIDATE": False,       # T27: off by default; see docs/adr/0012
    "ALETHEA_LLM_API": "",                  # "", "openai" or "anthropic" (direct-API mode)
    "ALETHEA_LLM_ALLOW_REMOTE": False,      # acknowledge that transcripts leave this machine
    "ALETHEA_LLM_URL": "",
    "ALETHEA_LLM_MODEL": "",
    "ALETHEA_LLM_PENDING_TTL": 3 * 24 * 3600,   # seconds a queued episode waits for the host
    "ALETHEA_LLM_BUDGET": 5,                # API mode: episodes per sleep cycle
    "ALETHEA_LLM_DAILY_CAP": 50,            # API mode: calls per UTC day
    "ALETHEA_LLM_TIMEOUT": "30",            # API mode: seconds per call (float)
}
# ALETHEA_LLM_API_KEY is deliberately NOT here: it is read from the process environment only,
# never from config.json and never copied into any config dict, status or log.


def home_dir(env=None):
    env = os.environ if env is None else env
    return Path(env.get("ALETHEA_HOME") or DEFAULTS["ALETHEA_HOME"]).expanduser()


def ensure_private_dir(path):
    """Create dir (and parents) and force mode 0700. Returns Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(p, 0o700)
    return p


def _coerce(default, raw):
    if isinstance(default, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


def load_config(env=None, home=None):
    """Merge defaults < <home>/config.json < env. Bad/missing file is ignored."""
    env = os.environ if env is None else env
    home = Path(home) if home else home_dir(env)
    cfg = dict(DEFAULTS)
    try:
        with open(home / "config.json", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in DEFAULTS:
                    cfg[k] = _coerce(DEFAULTS[k], v)
    except (OSError, ValueError):
        pass
    for k, d in DEFAULTS.items():
        if k in env:
            cfg[k] = _coerce(d, env[k])
    return cfg


def llm_mode(cfg):
    """'off' (default), 'host' (host-callback) or 'api' (direct API; needs a valid adapter)."""
    if not cfg.get("ALETHEA_LLM_CONSOLIDATE"):
        return "off"
    return "api" if str(cfg.get("ALETHEA_LLM_API") or "").strip() else "host"
