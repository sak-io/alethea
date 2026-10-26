"""Helpers for tests/test_distiller.py: a real daemon (threads) with extra env and an injectable
clock, a scripted stub HTTP server, and episode seeding. No real model or provider is ever
contacted: the only sockets are loopback."""
import http.server
import json
import os
import shutil
import threading
import time

from alethea import client
from alethea.daemon import Alethea, Server
from tests.daemon_helpers import make_home

TURNS = [
    "We decided to use Postgres 15 for the billing service because row level security matters.",
    "The deploy failed because the staging database ran out of disk space.",
    "Fixed by expanding the volume to 200 GB and adding a disk usage alert.",
    "Always run the migration dry run before applying it to production.",
    "Open question: who owns the nightly backup job for the billing database?",
]
GOOD = [
    "Decision: the billing service uses Postgres 15 because it needs row level security.",
    "Decision: the platform team owns the nightly backup job because they run the storage cluster.",
]


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class Daemon:
    """Alethea + Server in this process, with its own env dict (also used by the client)."""

    def __init__(self, env=None, clock=None, flag=True):
        self.home = make_home()
        self.env = {"ALETHEA_HOME": self.home, "PATH": os.environ.get("PATH", "")}
        if flag:
            self.env["ALETHEA_LLM_CONSOLIDATE"] = "1"
        self.env.update(env or {})
        self.clock = clock or Clock()
        self.alethea = Alethea(home=self.home, env=self.env, clock=self.clock)
        self.server = Server(self.alethea)
        assert self.server.acquire_instance()
        self.server.start()
        self.cortex = self.alethea.cortex

    def call(self, verb, params=None, timeout=5):
        return client.call(verb, params, timeout=timeout, env=self.env)

    def stop(self):
        self.server.shutdown()
        shutil.rmtree(self.home, ignore_errors=True)

    def episode(self, session="s1", turns=None, repo_root="/tmp/repo-x", gap=2000.0):
        """Journal one episode (observe each turn) then move the clock past the episode gap."""
        for t in (turns or TURNS):
            r = self.call("observe", {"content": t, "session_id": session, "role": "user",
                                      "repo_root": repo_root})
            assert r.get("ok"), r
        self.clock.advance(gap)

    def sleep(self, **kw):
        """The idle sleep cycle as the timer runs it (api allowed)."""
        return self.alethea._run_sleep(api_ok=True, keep_sessions=(), **kw)

    def consolidate(self):
        return self.call("sleep", {"consolidate_only": True})

    def memories(self, **filt):
        rows = self.cortex.db.execute("SELECT id FROM memories").fetchall()
        out = [self.cortex.get(r["id"]) for r in rows]
        return [m for m in out if all(m.get(k) == v for k, v in filt.items())]

    def llm_memories(self):
        return [m for m in self.memories() if (m["meta"] or {}).get("source", "").startswith("llm-")]

    def journal_pending(self):
        return self.cortex.db.execute(
            "SELECT COUNT(*) FROM journal WHERE consolidated=0").fetchone()[0]

    def dist(self):
        return self.call("status")["distiller"]

    def first_pending(self, n=1):
        r = self.call("pending", {"n": n})
        assert r.get("ok"), r
        return r["episodes"]


class Stub:
    """Loopback HTTP server. `script(n, path, headers, body)` -> (status, headers, body) where
    body is bytes; or None to hang (until `release`). Records every request."""

    def __init__(self, script):
        self.script, self.requests, self.release = script, [], threading.Event()
        self.arrived = threading.Event()
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n)
                stub.requests.append((self.path, dict(self.headers), body))
                stub.arrived.set()
                out = stub.script(len(stub.requests) - 1, self.path, dict(self.headers), body)
                if out is None:
                    stub.release.wait(30)
                    return
                code, hdrs, data = out
                self.send_response(code)
                for k, v in hdrs.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def openai_reply(bullets_text):
    return (200, {"Content-Type": "application/json"}, json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": bullets_text}}]}).encode())


def anthropic_reply(bullets_text):
    return (200, {"Content-Type": "application/json"}, json.dumps(
        {"content": [{"type": "text", "text": bullets_text}]}).encode())


def timed(fn, *a, **kw):
    t = time.monotonic()
    r = fn(*a, **kw)
    return r, time.monotonic() - t
