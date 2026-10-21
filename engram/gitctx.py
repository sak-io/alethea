"""Git context: resolve repo_root for a cwd (R6/R7). Fail-soft: never raises."""
import os
import subprocess
import time

TIMEOUT = 1.0
NEG_TTL = 5.0
_cache = {}
_neg = {}
_clock = time.monotonic


def cache_clear():
    _cache.clear()
    _neg.clear()


def _resolve(real):
    if not os.path.isdir(real):
        return None
    out = subprocess.run(
        ["git", "rev-parse", "--is-bare-repository", "--git-common-dir"],
        cwd=real, capture_output=True, text=True, timeout=TIMEOUT,
        shell=False, stdin=subprocess.DEVNULL,
    )
    if out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    if len(lines) != 2 or lines[0].strip() == "true" or not lines[1].strip():
        return None
    common = lines[1]
    if not os.path.isabs(common):
        common = os.path.join(real, common)
    return os.path.dirname(os.path.realpath(common)) or None


def repo_root(cwd):
    """Main-repo root (parent of git common dir) for cwd, or None.

    Worktrees map to the main repository; branch is not part of the result.
    Never raises. Failures are negatively cached for NEG_TTL seconds.
    """
    try:
        real = os.path.realpath(cwd)
        if real in _cache:
            return _cache[real]
        now = _clock()
        t = _neg.get(real)
        if t is not None and now - t < NEG_TTL:
            return None
        root = _resolve(real)
        if root is None:
            _neg[real] = now
            return None
        _cache[real] = root
        return root
    except Exception:
        return None
