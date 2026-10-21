"""Git context: resolve repo_root for a cwd (R6/R7). Fail-soft: never raises."""
import os
import subprocess

TIMEOUT = 1.0
_cache = {}


def cache_clear():
    _cache.clear()


def _git(args, cwd):
    out = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        timeout=TIMEOUT, shell=False, stdin=subprocess.DEVNULL,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def repo_root(cwd):
    """Main-repo root (parent of git common dir) for cwd, or None.

    Worktrees map to the main repository; branch is not part of the result.
    """
    try:
        real = os.path.realpath(cwd)
        if real in _cache:
            return _cache[real]
        if not os.path.isdir(real):
            return None
        top = _git(["rev-parse", "--show-toplevel"], real)
        common = _git(["rev-parse", "--git-common-dir"], real)
        if not top or not common:
            return None
        if not os.path.isabs(common):
            common = os.path.join(real, common)
        common = os.path.realpath(common)
        root = os.path.dirname(common)
        if not root:
            return None
        _cache[real] = root
        return root
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
