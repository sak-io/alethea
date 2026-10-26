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

from alethea import client
from alethea import daemon
from alethea.daemon import Alethea, Server, StartupError
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
        self.env = {"ALETHEA_HOME": self.home, "PATH": os.environ.get("PATH", "")}
        self.env.update(extra or {})
        self.alethea = Alethea(home=self.home, env=self.env)
        self.server = Server(self.alethea, env=self.env, **skw)
        try:
            assert self.server.acquire_instance()
        except BaseException:
            self.alethea.close()
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
        self.d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN}, auth_clock=self.clock,
                        auth_limits={"delay_unit": 0.001, "delay_cap": 0.01, "rate": 1e6,
                                     "burst": 1e6})
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

    def test_bare_ping_reveals_nothing(self):
        self.assertEqual(raw(self.d.port, req("ping")), {"ok": False, "error": "unauthorized"})
        self.assertEqual(raw(self.d.port, req("status")), {"ok": False, "error": "auth"})
        self.assertTrue(raw(self.d.port, req("ping", TOKEN))["ok"])

    def test_unix_stays_trusted_without_token(self):
        r = client.call("ping", env={"ALETHEA_HOME": self.d.home, "ALETHEA_TOKEN": "bad"})
        self.assertTrue(r.get("ok"))
        st = os.stat(os.path.join(self.d.home, "alethea.sock")).st_mode
        self.assertEqual(stat.S_IMODE(st), 0o600)

    def test_compare_digest_is_used(self):
        with mock.patch("alethea.daemon.hmac.compare_digest",
                        wraps=daemon.hmac.compare_digest) as cd:
            raw(self.d.port, req("ping", TOKEN))
            raw(self.d.port, req("ping", "nope" * 8))
        self.assertEqual(cd.call_count, 2)

    def test_correct_token_served_after_many_failures(self):
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN},
                   auth_limits={"delay_unit": 0.001, "delay_cap": 0.01, "rate": 1e6, "burst": 1e6})
        self.addCleanup(d.stop)
        for _ in range(50):
            self.assertEqual(raw(d.port, req("ping", "g" * 20))["error"], "auth")
        t0 = time.monotonic()
        self.assertTrue(raw(d.port, req("ping", TOKEN))["ok"])
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_failure_delay_grows_and_is_bounded(self):
        lim = daemon.AuthLimiter(self.clock)
        ds = [lim.fail("1.1.1.1") for _ in range(12)]
        self.assertEqual(ds, sorted(ds))
        self.assertLess(ds[0], ds[5])
        self.assertLessEqual(max(ds), 2.0)
        self.assertEqual(ds[-1], 2.0)
        lim.ok("1.1.1.1")
        self.assertEqual(lim.fail("1.1.1.1"), ds[0])
        # measured on the wire: later failures are slower, still capped
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN},
                   auth_limits={"delay_unit": 0.05, "delay_cap": 0.4, "rate": 1e6, "burst": 1e6})
        self.addCleanup(d.stop)
        times = []
        for _ in range(6):
            t0 = time.monotonic()
            raw(d.port, req("ping", "g" * 20))
            times.append(time.monotonic() - t0)
        self.assertGreater(times[4], times[0] + 0.1)
        self.assertLess(max(times), 0.4 + 0.5)

    def test_failure_flood_cannot_starve_correct_client(self):
        import threading
        stop = threading.Event()
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN})  # default limits
        self.addCleanup(d.stop)

        def flood():
            while not stop.is_set():
                try:
                    raw(d.port, req("ping", "g" * 20), timeout=1.0)
                except OSError:
                    pass

        ts = [threading.Thread(target=flood, daemon=True) for _ in range(8)]
        for t in ts:
            t.start()
        self.addCleanup(lambda: [stop.set()] + [t.join(3) for t in ts])
        time.sleep(0.3)
        worst = 0.0
        for _ in range(10):
            t0 = time.monotonic()
            self.assertTrue(raw(d.port, req("ping", TOKEN))["ok"])
            worst = max(worst, time.monotonic() - t0)
        self.assertLess(worst, 1.0)

    def test_failures_over_global_budget_are_dropped_silently(self):
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN},
                   auth_limits={"delay_unit": 0.0001, "delay_cap": 0.0001, "rate": 0.001,
                                "burst": 2})
        self.addCleanup(d.stop)
        res = [raw(d.port, req("ping", "g" * 20)) for _ in range(5)]
        self.assertEqual([r is None for r in res], [False, False, True, True, True])
        self.assertTrue(raw(d.port, req("ping", TOKEN))["ok"])

    def test_alternate_token_fields_rejected(self):
        t = json.dumps(TOKEN)
        good = {"verb": "status"}
        bodies = [
            dict(good, Token=TOKEN), dict(good, TOKEN=TOKEN), dict(good, auth={"token": TOKEN}),
            dict(good, **{"token ": TOKEN}), dict(good, token=[TOKEN]), dict(good, token=None),
        ]
        raws = [json.dumps(b).encode() + b"\n" for b in bodies]
        raws.append(('{"token":%s,"verb":"status","token":"wrong-wrong-wrong-wrong"}\n' % t
                     ).encode())  # last duplicate wins in the parser: wrong -> rejected
        for r in raws:
            self.assertFalse(raw(self.d.port, r, timeout=5)["ok"], r)
        # the parsed top-level token is what is checked AND what is stripped: consistent
        self.assertTrue(raw(self.d.port, ('{"verb":"status","token":"wrong-wrong-wrong-wrong",'
                                          '"token":%s}\n' % t).encode())["ok"])

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
        orig = self.d.alethea.handle
        self.d.alethea.handle = lambda verb, params: (seen.append(dict(params)), orig(verb, params))[1]
        raw(self.d.port, req("ping", TOKEN))
        self.assertEqual(seen, [{}])

    def test_oversize_request_dropped(self):
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN}, max_request=1024)
        self.addCleanup(d.stop)
        r = raw(d.port, req("remember", TOKEN, content="a" * 5000))
        self.assertEqual(r, {"ok": False, "error": "request too large"})

    def test_slow_loris_dropped(self):
        # short server deadlines (default 1.5 s first byte / 5 s read) keep the test ~1 s;
        # the behaviour under test (idle and partial lines are dropped) is the same.
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN}, read_timeout=0.6)
        self.addCleanup(d.stop)
        t0 = time.monotonic()
        s = raw(d.port, b"", read=False)
        self.addCleanup(s.close)
        self.assertEqual(s.recv(10), b"")  # idle socket closed at the first-byte deadline
        self.assertLess(time.monotonic() - t0, 3.0)
        s2 = raw(d.port, b'{"verb":"ping"', read=False)  # partial line
        self.addCleanup(s2.close)
        s2.settimeout(8)
        self.assertIn(b"read timeout", s2.recv(100))

    def test_connection_cap_applies_to_tcp(self):
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN})
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
        env = {"ALETHEA_HOME": home}
        env.update(extra)
        e = Alethea(home=home, env=env)
        self.addCleanup(e.close)
        return Server(e, env=env, **kw)

    def test_refusals(self):
        cases = [
            {"ALETHEA_BIND": "0.0.0.0", "ALETHEA_PORT": "0"},
            {"ALETHEA_BIND": "0.0.0.0", "ALETHEA_PORT": "0", "ALETHEA_TOKEN": "short"},
            {"ALETHEA_CONTAINER": "1", "ALETHEA_BIND": "127.0.0.1", "ALETHEA_PORT": "5"},
            {"ALETHEA_CONTAINER": "1", "ALETHEA_PORT": "5", "ALETHEA_TOKEN": TOKEN},  # no bind
            {"ALETHEA_CONTAINER": "1", "ALETHEA_BIND": "0.0.0.0", "ALETHEA_PORT": "5",
             "ALETHEA_TOKEN": "x" * 15},
            {"ALETHEA_PORT": "70000"},
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
        p = spawn(home, {"ALETHEA_CONTAINER": "1", "ALETHEA_BIND": "0.0.0.0",
                         "ALETHEA_PORT": "7777", "ALETHEA_TOKEN": secret})
        out, err = p.communicate(timeout=15)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b"at least 16", err)
        self.assertNotIn(secret.encode(), out + err)

    def test_container_mode_tcp_only_and_client_no_autostart(self):
        port = free_port()
        extra = {"ALETHEA_CONTAINER": "1", "ALETHEA_BIND": "127.0.0.1", "ALETHEA_PORT": str(port),
                 "ALETHEA_TOKEN": TOKEN}
        d = Daemon(extra=extra)
        self.addCleanup(d.stop)
        self.assertFalse(os.path.exists(os.path.join(d.home, "alethea.sock")))
        cenv = {"ALETHEA_HOME": make_home(), "ALETHEA_CONTAINER": "1", "ALETHEA_PORT": str(port),
                "ALETHEA_TOKEN": TOKEN}
        self.addCleanup(shutil.rmtree, cenv["ALETHEA_HOME"], True)
        self.assertTrue(client.call("ping", env=cenv)["ok"])
        self.assertFalse(client.call("ping", env=dict(cenv, ALETHEA_TOKEN="w" * 20)).get("ok"))
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(client.autostart(env=cenv))
        popen.assert_not_called()

    def test_loopback_tcp_without_token_generates_one(self):
        d = Daemon(extra={"ALETHEA_PORT": "0"})
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
        env = {"ALETHEA_HOME": d.home}
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
        self.assertGreater(len(os.path.join(home, "alethea.sock")), 104)
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
        env = {"ALETHEA_HOME": make_home(), "ALETHEA_HOST": "10.1.2.3", "ALETHEA_PORT": "9", "ALETHEA_CONTAINER": "1",
               "ALETHEA_TOKEN": TOKEN}
        self.addCleanup(shutil.rmtree, env["ALETHEA_HOME"], True)
        with mock.patch.object(client, "_exchange", side_effect=AssertionError) as ex:
            self.assertEqual(client.call("ping", env=env), {"ok": False, "offline": True})
        ex.assert_not_called()


class Hardening(unittest.TestCase):
    def test_lock_file_is_0600_under_umask_0(self):
        old = os.umask(0)
        try:
            d = Daemon()
        finally:
            os.umask(old)
        self.addCleanup(d.stop)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(d.home, "daemon.lock")).st_mode), 0o600)

    def test_client_never_sends_token_to_names_or_non_loopback(self):
        for host in ("10.0.0.5", "example.com", "127.0.0.1.evil.test", "0.0.0.0",
                     "::ffff:127.0.0.1", "localhost.", "evil"):
            home = make_home()
            self.addCleanup(shutil.rmtree, home, True)
            with open(os.path.join(home, "daemon.json"), "w") as f:
                json.dump({"host": host, "port": 4321}, f)
            fd = os.open(os.path.join(home, "token"), os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, TOKEN.encode())
            os.close(fd)
            env = {"ALETHEA_HOME": home, "ALETHEA_CONTAINER": "1"}
            with mock.patch.object(client, "_exchange", side_effect=AssertionError) as ex:
                self.assertEqual(client.call("ping", env=env), {"ok": False, "offline": True})
            ex.assert_not_called()
        # loopback literals (and 'localhost', mapped explicitly) do get it
        for host, want in (("127.0.0.1", "127.0.0.1"), ("127.5.5.5", "127.5.5.5"),
                           ("::1", "::1"), ("localhost", "127.0.0.1")):
            home = make_home()
            self.addCleanup(shutil.rmtree, home, True)
            with open(os.path.join(home, "daemon.json"), "w") as f:
                json.dump({"host": host, "port": 4321}, f)
            env = {"ALETHEA_HOME": home, "ALETHEA_CONTAINER": "1", "ALETHEA_TOKEN": TOKEN}
            with mock.patch.object(client, "_exchange", return_value=b'{"ok":true}') as ex:
                self.assertTrue(client.call("ping", env=env)["ok"])
            kind, addr, payload, _ = ex.call_args[0]
            self.assertEqual(addr, (want, 4321))
            self.assertIn(TOKEN.encode(), payload)

    def test_slow_loris_and_idle_sockets_do_not_delay_good_client(self):
        import threading
        d = Daemon(extra={"ALETHEA_PORT": "0", "ALETHEA_TOKEN": TOKEN})
        self.addCleanup(d.stop)
        idle = [socket.create_connection(("127.0.0.1", d.port)) for _ in range(200)]
        self.addCleanup(lambda: [c.close() for c in idle])
        stop = threading.Event()

        def drip():
            c = socket.create_connection(("127.0.0.1", d.port))
            try:
                for ch in b'{"verb":"ping","token":"':
                    if stop.is_set():
                        return
                    c.sendall(bytes([ch]))
                    stop.wait(1.0)
            except OSError:
                pass
            finally:
                c.close()

        t = threading.Thread(target=drip, daemon=True)
        t.start()
        self.addCleanup(lambda: (stop.set(), t.join(3)))
        time.sleep(0.3)
        for _ in range(5):
            t0 = time.monotonic()
            self.assertTrue(raw(d.port, req("ping", TOKEN))["ok"])
            self.assertLess(time.monotonic() - t0, 1.0)

    def test_sigterm_closes_listener_and_port_is_rebindable(self):
        import signal
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        port = free_port()
        extra = {"ALETHEA_PORT": str(port), "ALETHEA_TOKEN": TOKEN}
        for _ in range(2):  # run, SIGTERM, immediately restart on the same port
            p = spawn(home, extra)
            self.addCleanup(lambda p=p: p.poll() is None and p.kill())
            end = time.time() + 10
            r = None
            while time.time() < end and not (r and r.get("ok")):
                try:
                    r = raw(port, req("ping", TOKEN), timeout=1)
                except OSError:
                    time.sleep(0.05)
            self.assertTrue(r and r["ok"])
            with open(os.path.join(home, "daemon.json")) as f:
                self.assertEqual(json.load(f)["port"], port)
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(home, "daemon.json")))
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1)

    def test_second_tcp_daemon_exits_quietly(self):
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        port = free_port()
        extra = {"ALETHEA_CONTAINER": "1", "ALETHEA_BIND": "127.0.0.1", "ALETHEA_PORT": str(port),
                 "ALETHEA_TOKEN": TOKEN}
        p1 = spawn(home, extra)
        self.addCleanup(lambda: (p1.kill(), p1.communicate()))
        end = time.time() + 10
        while time.time() < end:
            try:
                if raw(port, req("ping", TOKEN), timeout=1).get("ok"):
                    break
            except OSError:
                time.sleep(0.05)
        p2 = spawn(home, extra)
        out, err = p2.communicate(timeout=15)
        self.assertEqual((p2.returncode, out, err), (0, b"", b""))
        self.assertTrue(raw(port, req("ping", TOKEN))["ok"])

    def test_subprocess_long_home_fallback_roundtrip(self):
        base = tempfile.mkdtemp(prefix="eg")
        self.addCleanup(shutil.rmtree, base, True)
        home = os.path.join(base, "d" * 100)
        os.makedirs(home, mode=0o700)
        p = spawn(home)
        self.addCleanup(lambda: (p.terminate(), p.communicate()))
        from tests.daemon_helpers import wait_up
        self.assertTrue(wait_up(home))
        env = {"ALETHEA_HOME": home}
        self.assertTrue(client.call("remember", {"content": "long path subprocess"}, env=env)["ok"])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(home, "token")).st_mode), 0o600)
        self.assertEqual(client.call("status", env=env)["total"], 1)


if __name__ == "__main__":
    unittest.main()
