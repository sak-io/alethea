"""Git context: resolve repo_root for a cwd (R6/R7). Fail-soft: never raises."""
import os
import subprocess
import threading
import time
from collections import OrderedDict

TIMEOUT = 1.0
NEG_TTL = 5.0
MAX_ENTRIES = 1024  # LRU bound for each cache
_cache = OrderedDict()
_neg = OrderedDict()
_lock = threading.Lock()
_clock = time.monotonic


def _put(cache, key, value):
    with _lock:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > MAX_ENTRIES:
            cache.popitem(last=False)


def _get(cache, key):
    with _lock:
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
    return None


def cache_clear():
    with _lock:
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
        hit = _get(_cache, real)
        if hit is not None:
            return hit
        now = _clock()
        t = _get(_neg, real)
        if t is not None and now - t < NEG_TTL:
            return None
        root = _resolve(real)
        if root is None:
            _put(_neg, real, now)
            return None
        _put(_cache, real, root)
        return root
    except Exception:
        return None
