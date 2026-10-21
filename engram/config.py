"""Constants and config loading. Precedence: env > config.json > defaults. No secrets in code."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1
HASH_SALT = b"engram-v1"
DOWNSCALE_INTERVAL_S = 6 * 3600
MAX_REQUEST_BYTES = 1 << 20

DEFAULTS = {
    "ENGRAM_HOME": "~/.engram",
    "ENGRAM_EMBEDDER": "hash",
    "ENGRAM_SLEEP_INTERVAL": 900,
    "ENGRAM_IDLE_SLEEP": 180,
    "ENGRAM_LLM_CONSOLIDATE": False,
}


def home_dir(env=None):
    env = os.environ if env is None else env
    return Path(env.get("ENGRAM_HOME") or DEFAULTS["ENGRAM_HOME"]).expanduser()


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
