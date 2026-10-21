"""Helpers: real daemon on a temp home, in-process (thread) or subprocess."""
import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from engram import client
from engram.daemon import Engram, Server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_HOMES = []


def make_home():
    """Short temp home (AF_UNIX path limit), remembered so that any daemon the code under test
    autostarted there (hooks and mnemo spawn detached daemons on purpose) is stopped when the
    test process exits instead of leaking."""
    h = tempfile.mkdtemp(prefix="eg")
    _HOMES.append(h)
    return h


def track_home(path):
    """Register a temp home that was not created by make_home() for the same reaping."""
    _HOMES.append(path)
    return path


def _is_engram_daemon(pid):
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "args="], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return False
    return "engram.daemon" in out


def reap_daemons():
    """SIGTERM every engram daemon recorded in a tracked home (verified by command line)."""
    for h in list(_HOMES):
        try:
            with open(os.path.join(h, "daemon.json")) as f:
                pid = json.load(f).get("pid")
            if pid and _is_engram_daemon(pid):
                os.kill(int(pid), signal.SIGTERM)
        except Exception:
            pass


atexit.register(reap_daemons)


class InProc:
    """Engram + Server running in threads of this process."""

    def __init__(self, home=None, read_timeout=None, max_conns=64, **ekw):
        self.home = home or make_home()
        self.env = {"ENGRAM_HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.engram = Engram(home=self.home, env=self.env, **ekw)
        self.server = Server(self.engram, read_timeout=read_timeout, max_conns=max_conns)
        assert self.server.acquire_instance()
        self.server.start()

    def call(self, verb, params=None, timeout=5):
        return client.call(verb, params, timeout=timeout, env=self.env)

    def stop(self):
        self.server.shutdown()
        shutil.rmtree(self.home, ignore_errors=True)


def spawn(home, extra_env=None):
    env = dict(os.environ, ENGRAM_HOME=home, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    env.update(extra_env or {})
    return subprocess.Popen([sys.executable, "-m", "engram.daemon"], env=env, cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)


def wait_up(home, timeout=10):
    env = {"ENGRAM_HOME": home}
    end = time.time() + timeout
    while time.time() < end:
        if client.call("ping", timeout=0.5, env=env).get("ok"):
            return True
        time.sleep(0.05)
    return False
