"""T17: TCP listener, token auth, container mode, AF_UNIX fallback (ADR 0004). Real daemons."""
import contextlib
import io
import json
import os
import shutil
import socket
import stat
import tempfile
import time
import unittest
from unittest import mock

from engram import client
from engram import daemon
from engram.daemon import Engram, Server, StartupError
from tests.daemon_helpers import ROOT, make_home, spawn

TOKEN = "t" * 8 + "0123456789abcdef-secret-TOKEN"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Daemon:
    def __init__(self, home=None, extra=None, **skw):
        self.home = home or make_home()
        self.env = {"ENGRAM_HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.env.update(extra or {})
        self.engram = Engram(home=self.home, env=self.env)
        self.server = Server(self.engram, env=self.env, **skw)
        try:
            assert self.server.acquire_instance()
        except BaseException:
            self.engram.close()
            raise
        self.server.start()
        self.port = self.server.tcp_addr[1] if self.server.tcp_addr else None

    def stop(self):
        self.server.shutdown()
        shutil.rmtree(self.home, ignore_errors=True)


def raw(port, payload, host="127.0.0.1", timeout=3.0, read=True):
    """Send bytes, return the first reply line decoded (or None on EOF/timeout)."""
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        if payload:
            s.sendall(payload)
        if not read:
            return s
        buf = b""
        while b"\n" not in buf:
            try:
                c = s.recv(65536)
            except (socket.timeout, ConnectionResetError):
                return None
            if not c:
                return None
            buf += c
        return json.loads(buf.split(b"\n")[0])
    finally:
        if read:
            s.close()


def req(verb, token=None, **kw):
    d = dict(kw, verb=verb)
    if token is not None:
        d["token"] = token
    return json.dumps(d).encode() + b"\n"


class TcpAuth(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.d = Daemon(extra={"ENGRAM_PORT": "0", "ENGRAM_TOKEN": TOKEN}, auth_clock=self.clock)
        self.addCleanup(self.d.stop)

    def test_correct_token_works_on_every_request(self):
        s = socket.create_connection(("127.0.0.1", self.d.port), timeout=3)
        self.addCleanup(s.close)
        f = s.makefile("rwb")
        for _ in range(2):
            f.write(req("status", TOKEN))
            f.flush()
            self.assertTrue(json.loads(f.readline())["ok"])
        f.write(req("status"))  # no token on a later request: not trusted by history
        f.flush()
        self.assertEqual(json.loads(f.readline())["error"], "auth")

    def test_missing_wrong_short_tokens_rejected_and_closed(self):
        for tok in (None, "wrong" * 8, "x", "", TOKEN[:-1], TOKEN + "x", 12345):
            r = raw(self.d.port, json.dumps({"verb": "recall", "cue": "x", "token": tok}
                                            if tok is not None else
                                            {"verb": "recall", "cue": "x"}).encode() + b"\n")
            self.assertEqual(r, {"ok": False, "error": "auth"}, tok)
            self.clock.t += 1000  # keep the limiter out of this test

    def test_bare_ping_reveals_nothing(self):
        self.assertEqual(raw(self.d.port, req("ping")), {"ok": False, "error": "unauthorized"})
        self.assertEqual(raw(self.d.port, req("status")), {"ok": False, "error": "auth"})
        self.assertTrue(raw(self.d.port, req("ping", TOKEN))["ok"])

    def test_unix_stays_trusted_without_token(self):
        r = client.call("ping", env={"ENGRAM_HOME": self.d.home, "ENGRAM_TOKEN": "bad"})
        self.assertTrue(r.get("ok"))
        st = os.stat(os.path.join(self.d.home, "engram.sock")).st_mode
        self.assertEqual(stat.S_IMODE(st), 0o600)

    def test_compare_digest_is_used(self):
        with mock.patch("engram.daemon.hmac.compare_digest",
                        wraps=daemon.hmac.compare_digest) as cd:
            raw(self.d.port, req("ping", TOKEN))
            raw(self.d.port, req("ping", "nope" * 8))
        self.assertEqual(cd.call_count, 2)

    def test_bruteforce_backoff(self):
        for _ in range(daemon.AUTH_FREE_FAILS):
            self.assertEqual(raw(self.d.port, req("ping", "g" * 20))["error"], "auth")
        # locked out: even the right token is refused without being compared
        with mock.patch("engram.daemon.hmac.compare_digest") as cd:
            r = raw(self.d.port, req("ping", TOKEN))
        self.assertEqual(r, {"ok": False, "error": "rate limited"})
        cd.assert_not_called()
        self.clock.t += 2
        self.assertTrue(raw(self.d.port, req("ping", TOKEN))["ok"])
        # backoff doubles while the attacker keeps failing
        lim = self.d.server.limiter
        for _ in range(6):
            lim.fail("9.9.9.9")
        self.assertTrue(lim.blocked("9.9.9.9"))
        self.clock.t += 5
        self.assertTrue(lim.blocked("9.9.9.9"))
        self.clock.t += daemon.AUTH_BACKOFF_MAX_S
        self.assertFalse(lim.blocked("9.9.9.9"))

    def test_token_never_leaks(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            raw(self.d.port, req("recall", TOKEN, cue=5))          # bad param, good token
            raw(self.d.port, req("bogus", TOKEN))
            raw(self.d.port, req("remember", TOKEN, content="hello world fact", kind="nope"))
            raw(self.d.port, b'{"verb":"ping","token":"' + TOKEN.encode() + b'"\n')  # bad json
            self.clock.t += 100
            outs = [raw(self.d.port, req(v, TOKEN)) for v in ("status", "dump", "ping")]
            outs.append(raw(self.d.port, req("ping", TOKEN[:-2] + "zz")))
            time.sleep(0.1)
        blob = err.getvalue() + json.dumps(outs)
        self.assertNotIn(TOKEN, blob)
        self.assertNotIn(TOKEN[:-2], blob)

    def test_token_not_forwarded_to_verbs(self):
        seen = []
        orig = self.d.engram.handle
        self.d.engram.handle = lambda verb, params: (seen.append(dict(params)), orig(verb, params))[1]
        raw(self.d.port, req("ping", TOKEN))
        self.assertEqual(seen, [{}])

    def test_oversize_request_dropped(self):
        d = Daemon(extra={"ENGRAM_PORT": "0", "ENGRAM_TOKEN": TOKEN}, max_request=1024)
        self.addCleanup(d.stop)
        r = raw(d.port, req("remember", TOKEN, content="a" * 5000))
        self.assertEqual(r, {"ok": False, "error": "request too large"})

    def test_slow_loris_dropped(self):
        t0 = time.monotonic()
        s = raw(self.d.port, b"", read=False)
        self.addCleanup(s.close)
        self.assertEqual(s.recv(10), b"")  # idle socket closed at the first-byte deadline
        self.assertLess(time.monotonic() - t0, 3.0)
        s2 = raw(self.d.port, b'{"verb":"ping"', read=False)  # partial line
        self.addCleanup(s2.close)
        s2.settimeout(8)
        self.assertIn(b"read timeout", s2.recv(100))

    def test_connection_cap_applies_to_tcp(self):
        d = Daemon(extra={"ENGRAM_PORT": "0", "ENGRAM_TOKEN": TOKEN})
        self.addCleanup(d.stop)
        import threading
        d.server.pending = threading.BoundedSemaphore(1)
        hold = raw(d.port, b"", read=False)
        self.addCleanup(hold.close)
        time.sleep(0.1)
        self.assertEqual(raw(d.port, req("ping", TOKEN)), {"ok": False, "error": "busy"})


class Policy(unittest.TestCase):
    def mk(self, extra, **kw):
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        env = {"ENGRAM_HOME": home}
        env.update(extra)
        e = Engram(home=home, env=env)
        self.addCleanup(e.close)
        return Server(e, env=env, **kw)

    def test_refusals(self):
        cases = [
            {"ENGRAM_BIND": "0.0.0.0", "ENGRAM_PORT": "0"},
            {"ENGRAM_BIND": "0.0.0.0", "ENGRAM_PORT": "0", "ENGRAM_TOKEN": "short"},
            {"ENGRAM_CONTAINER": "1", "ENGRAM_BIND": "127.0.0.1", "ENGRAM_PORT": "5"},
            {"ENGRAM_CONTAINER": "1", "ENGRAM_PORT": "5", "ENGRAM_TOKEN": TOKEN},  # no bind
            {"ENGRAM_CONTAINER": "1", "ENGRAM_BIND": "0.0.0.0", "ENGRAM_PORT": "5",
             "ENGRAM_TOKEN": "x" * 15},
            {"ENGRAM_PORT": "70000"},
        ]
        for extra in cases:
            srv = self.mk(extra)
            with self.assertRaises(StartupError, msg=extra):
                srv.acquire_instance()
            self.assertIsNone(srv.tcp_listener)
            self.assertIsNone(srv.listener)

    def test_refusal_is_nonzero_exit_with_clear_error_and_no_token(self):
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        secret = "short-secret"
        p = spawn(home, {"ENGRAM_CONTAINER": "1", "ENGRAM_BIND": "0.0.0.0",
                         "ENGRAM_PORT": "7777", "ENGRAM_TOKEN": secret})
        out, err = p.communicate(timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"at least 16", err)
        self.assertNotIn(secret.encode(), out + err)

    def test_container_mode_tcp_only_and_client_no_autostart(self):
        port = free_port()
        extra = {"ENGRAM_CONTAINER": "1", "ENGRAM_BIND": "127.0.0.1", "ENGRAM_PORT": str(port),
                 "ENGRAM_TOKEN": TOKEN}
        d = Daemon(extra=extra)
        self.addCleanup(d.stop)
        self.assertFalse(os.path.exists(os.path.join(d.home, "engram.sock")))
        cenv = {"ENGRAM_HOME": make_home(), "ENGRAM_CONTAINER": "1", "ENGRAM_PORT": str(port),
                "ENGRAM_TOKEN": TOKEN}
        self.addCleanup(shutil.rmtree, cenv["ENGRAM_HOME"], True)
        self.assertTrue(client.call("ping", env=cenv)["ok"])
        self.assertFalse(client.call("ping", env=dict(cenv, ENGRAM_TOKEN="w" * 20)).get("ok"))
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(client.autostart(env=cenv))
        popen.assert_not_called()

    def test_loopback_tcp_without_token_generates_one(self):
        d = Daemon(extra={"ENGRAM_PORT": "0"})
        self.addCleanup(d.stop)
        tp = os.path.join(d.home, "token")
        self.assertEqual(stat.S_IMODE(os.stat(tp).st_mode), 0o600)
        self.assertEqual(raw(d.port, req("ping")), {"ok": False, "error": "unauthorized"})
        with open(tp) as f:
            self.assertTrue(raw(d.port, req("ping", f.read().strip()))["ok"])


class UnixFallback(unittest.TestCase):
    def check_end_to_end(self, d):
        with open(os.path.join(d.home, "daemon.json")) as f:
            info = json.load(f)
        self.assertEqual(info["host"], "127.0.0.1")
        self.assertEqual(info["port"], d.port)
        self.assertIsNone(info["socket"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d.home, "daemon.json")).st_mode), 0o600)
        tp = os.path.join(d.home, "token")
        self.assertEqual(stat.S_IMODE(os.stat(tp).st_mode), 0o600)
        with open(tp) as f:
            self.assertGreaterEqual(len(f.read().strip()), 16)
        env = {"ENGRAM_HOME": d.home}
        self.assertTrue(client.call("ping", env=env)["ok"])
        r = client.call("remember", {"content": "fallback path works end to end"}, env=env)
        self.assertTrue(r["ok"], r)
        self.assertEqual(client.call("status", env=env)["total"], 1)
        os.unlink(tp)  # client without a token is refused (daemon keeps its in-memory one)
        self.assertFalse(client.call("status", env=env).get("ok"))

    def test_long_home_path_falls_back_to_tcp(self):
        base = tempfile.mkdtemp(prefix="eg")
        self.addCleanup(shutil.rmtree, base, True)
        home = os.path.join(base, "d" * 100)
        self.assertGreater(len(os.path.join(home, "engram.sock")), 104)
        d = Daemon(home=home)
        self.addCleanup(d.stop)
        self.assertTrue(d.server.fallback)
        self.assertIsNone(d.server.listener)
        self.check_end_to_end(d)

    def test_bind_error_falls_back_to_tcp(self):
        real = socket.socket.bind

        def bind(self, addr):
            if self.family == socket.AF_UNIX:
                raise PermissionError(13, "denied")
            return real(self, addr)

        with mock.patch.object(socket.socket, "bind", bind):
            d = Daemon()
        self.addCleanup(d.stop)
        self.assertTrue(d.server.fallback)
        self.check_end_to_end(d)

    def test_fallback_token_only_sent_to_loopback(self):
        env = {"ENGRAM_HOME": make_home(), "ENGRAM_HOST": "10.1.2.3", "ENGRAM_PORT": "9", "ENGRAM_CONTAINER": "1",
               "ENGRAM_TOKEN": TOKEN}
        self.addCleanup(shutil.rmtree, env["ENGRAM_HOME"], True)
        with mock.patch.object(client, "_exchange", side_effect=AssertionError) as ex:
            self.assertEqual(client.call("ping", env=env), {"ok": False, "offline": True})
        ex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
