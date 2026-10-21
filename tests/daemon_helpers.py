"""Helpers: real daemon on a temp home, in-process (thread) or subprocess."""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from engram import client
from engram.daemon import Engram, Server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_home():
    return tempfile.mkdtemp(prefix="eg")  # short path: AF_UNIX limit


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
