"""Helpers for the remote-embedder and reembed tests: an in-process daemon (Engram + Server in
threads, env passed as a dict like tests.test_embedder_daemon) pointed at a tests.remote_stub.Stub."""
import json
import os
import shutil
import sqlite3
import time

from engram import client, vectorize
from engram.daemon import Engram, Server
from tests.daemon_helpers import make_home
from tests.remote_stub import KEY, wait_for

MODEL = "test-model"

# Planted secrets: the stub must never receive any of these (assert on everything it saw).
AWS = "AKIAIOSFODNN7EXAMPLE"
GHP = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
PWD = "hunter2hunter2xyz"
SECRETS = (AWS, GHP, PWD)
SECRET_TEXT = "deploy notes: aws key %s and token %s and password=%s end" % (AWS, GHP, PWD)


def remote_env(stub, **extra):
    env = {"ENGRAM_EMBEDDER": "openai:" + MODEL, "ENGRAM_EMBEDDER_URL": stub.url,
           "ENGRAM_EMBEDDER_API_KEY": KEY, "ENGRAM_EMBEDDER_TIMEOUT": "0.8"}
    env.update(extra)
    return env


class RDaemon:
    """In-process daemon. `env` entries are added to a minimal environment (never os.environ,
    so a developer's real ENGRAM_EMBEDDER_* can not leak into a test)."""

    def __init__(self, env=None, home=None, wait=True):
        vectorize._reset_for_tests()
        self.home = home or make_home()
        self.env = {"ENGRAM_HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.env.update(env or {})
        self.engram = Engram(home=self.home, env=self.env)
        self.server = Server(self.engram)
        assert self.server.acquire_instance()
        self.server.start()
        self.cenv = {"ENGRAM_HOME": self.home}
        if wait and "openai:" in self.env.get("ENGRAM_EMBEDDER", ""):
            vectorize.wait_settled(10)

    def call(self, verb, params=None, timeout=5):
        t = time.monotonic()
        r = client.call(verb, params, timeout=timeout, env=self.cenv)
        self.last_s = time.monotonic() - t
        return r

    def status(self):
        return self.call("status")

    def rows(self, where=""):
        with self.engram.lock:
            return [dict(r) for r in self.engram.cortex.db.execute(
                "SELECT rowid AS rid, id, kind, content, gist, strength, embedder_id, "
                "length(vector)/4 AS dim FROM memories %s ORDER BY rowid" % where)]

    def ids(self):
        return sorted({(r["embedder_id"], r["dim"]) for r in self.rows()})

    def reembed(self, action, **kw):
        return self.call("reembed", dict(kw, action=action))

    def wait_state(self, states, timeout=60):
        """Wait on conditions, not time: the run is in one of `states` AND its thread has
        returned (a paused/done state is published just before the thread exits)."""
        def ok():
            r = self.reembed("status")["reembed"]
            return r["state"] in states and not r["active"]
        return wait_for(ok, timeout)

    def stop(self):
        try:
            self.server.shutdown()
        finally:
            vectorize._reset_for_tests()

    def cleanup(self):
        self.stop()
        shutil.rmtree(self.home, ignore_errors=True)


def db_rows(home, sql="SELECT embedder_id, COUNT(*) FROM memories GROUP BY 1"):
    """Read-only look at a (possibly running) daemon's database from another process/thread."""
    con = sqlite3.connect("file:%s?mode=ro" % os.path.join(home, "engram.db"), uri=True, timeout=5)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def all_files_text(home):
    """Bytes of every file under `home` (db, wal, json, logs) for leak greps."""
    out = []
    for root, _dirs, files in os.walk(home):
        for f in files:
            try:
                with open(os.path.join(root, f), "rb") as fh:
                    out.append(fh.read())
            except OSError:
                pass
    return b"\n".join(out)


def seed(d, n, prefix="Decision", kind=None):
    """n distinct deliberate memories (ids returned)."""
    ids = []
    for i in range(n):
        p = {"content": "%s %d: component-%d speaks protocol-%d over port %d" % (
            prefix, i, i * 7 + 3, i * 11 + 1, 4000 + i)}
        if kind:
            p["kind"] = kind
        r = d.call("remember", p)
        assert r.get("ok"), r
        ids.append(r.get("id"))
    return ids


def jdump(o):
    return json.dumps(o, sort_keys=True, default=str)
