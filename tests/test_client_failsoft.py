import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from engram import client


class FakeServer:
    """handler(conn) runs in a thread per connection after one request line is read."""

    def __init__(self, handler, mode="unix", home=None):
        self.handler, self.mode = handler, mode
        self.seen = []
        if mode == "unix":
            self.sock = socket.socket(socket.AF_UNIX)
            self.sock.bind(os.path.join(home, "engram.sock"))
        else:
            self.sock = socket.socket()
            self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1] if mode == "tcp" else None
        self.conns = []
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self._h, args=(c,), daemon=True).start()

    def _h(self, c):
        try:
            buf = b""
            while b"\n" not in buf:
                d = c.recv(65536)
                if not d:
                    return
                buf += d
            self.seen.append(json.loads(buf.split(b"\n")[0]))
            self.handler(c)
        except Exception:
            pass

    def close(self):
        self.sock.close()
        for c in self.conns:
            try:
                c.close()
            except OSError:
                pass


def ok_handler(c):
    c.sendall(b'{"ok":true,"v":1}\n')
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="eg")
        self.env = {"ENGRAM_HOME": self.home}
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.close()

    def serve(self, handler, mode="unix"):
        s = FakeServer(handler, mode, self.home)
        self.servers.append(s)
        return s

    def assertOffline(self, r):
        self.assertEqual(r, {"ok": False, "offline": True})

    def timed(self, budget):
        t = time.monotonic()
        r = client.call("ping", timeout=budget, env=self.env)
        self.assertLess(time.monotonic() - t, budget + 0.6)
        return r


class FailSoft(Base):
    def test_happy_unix(self):
        self.serve(ok_handler)
        self.assertEqual(client.call("ping", {"a": 1}, env=self.env), {"ok": True, "v": 1})

    def test_daemon_down(self):
        self.assertOffline(client.call("ping", env=self.env))

    def test_stale_socket_file(self):
        s = socket.socket(socket.AF_UNIX)
        s.bind(os.path.join(self.home, "engram.sock"))
        s.close()
        self.assertOffline(client.call("ping", env=self.env))

    def test_hung_server_returns_within_budget(self):
        self.serve(lambda c: time.sleep(5))
        self.assertOffline(self.timed(0.5))

    def test_drip_feed_total_deadline(self):
        def drip(c):
            for _ in range(40):
                c.sendall(b"x")
                time.sleep(0.1)
        self.serve(drip)
        self.assertOffline(self.timed(0.6))

    def test_killed_mid_reply(self):
        def h(c):
            c.sendall(b'{"ok": tr')
            c.close()
        self.serve(h)
        self.assertOffline(self.timed(1.0))

    def test_closed_without_reply(self):
        self.serve(lambda c: c.close())
        self.assertOffline(self.timed(1.0))

    def test_half_closed_shutdown_wr(self):
        def h(c):
            c.sendall(b'{"ok":true}')  # no newline
            c.shutdown(socket.SHUT_WR)
            time.sleep(3)
        self.serve(h)
        self.assertOffline(self.timed(1.0))

    def test_garbage_reply(self):
        self.serve(lambda c: c.sendall(b"\xff\xfe not json\n"))
        self.assertOffline(self.timed(1.0))

    def test_non_dict_json_reply(self):
        self.serve(lambda c: c.sendall(b"[1,2]\n"))
        self.assertOffline(self.timed(1.0))

    def test_huge_reply_100mb(self):
        def h(c):
            blob = b"a" * (1 << 20)
            try:
                for _ in range(100):
                    c.sendall(blob)
            except OSError:
                pass
        self.serve(h)
        self.assertOffline(self.timed(2.0))

    def test_unserializable_params(self):
        self.assertOffline(client.call("x", {"a": object()}, env=self.env))

    def test_oversize_request(self):
        self.serve(ok_handler)
        self.assertOffline(client.call("x", {"a": "z" * (2 << 20)}, env=self.env))

    def test_bad_env_and_args(self):
        self.assertOffline(client.call(None, None, timeout="bad", env={"ENGRAM_HOME": "/nonexistent/x"}))

    def test_wrappers_use_budgets(self):
        self.assertEqual((client.TIMEOUT_HOOK, client.TIMEOUT_MCP, client.TIMEOUT_CLI), (2.0, 5.0, 10.0))
        self.assertOffline(client.hook_call("ping", env=self.env))
        self.assertOffline(client.mcp_call("ping", env=self.env))


class Tcp(Base):
    def test_tcp_with_token_loopback(self):
        s = self.serve(ok_handler, "tcp")
        self.env.update(ENGRAM_PORT=str(s.port), ENGRAM_TOKEN="t" * 20)
        self.assertTrue(client.call("ping", env=self.env)["ok"])
        self.assertEqual(s.seen[0]["token"], "t" * 20)
        self.assertEqual(s.seen[0]["verb"], "ping")

    def test_tcp_discovery_file_and_token_file(self):
        s = self.serve(ok_handler, "tcp")
        with open(os.path.join(self.home, "daemon.json"), "w") as f:
            json.dump({"host": "127.0.0.1", "port": s.port}, f)
        with open(os.path.join(self.home, "token"), "w") as f:
            f.write("filetoken-1234567890\n")
        os.chmod(os.path.join(self.home, "token"), 0o600)
        self.assertTrue(client.call("ping", env=self.env)["ok"])
        self.assertEqual(s.seen[0]["token"], "filetoken-1234567890")

    def test_unix_down_falls_back_to_tcp(self):
        s = self.serve(ok_handler, "tcp")
        self.env["ENGRAM_PORT"] = str(s.port)
        self.assertTrue(client.call("ping", env=self.env)["ok"])

    def test_token_not_sent_to_remote_host(self):
        sent = []
        orig = client._exchange
        client._exchange = lambda k, a, p, d: sent.append(p) or b'{"ok":true}'
        try:
            self.env.update(ENGRAM_CONTAINER="1", ENGRAM_HOST="203.0.113.5", ENGRAM_PORT="9",
                            ENGRAM_TOKEN="secret" * 4)
            self.assertOffline(client.call("ping", env=self.env))
            self.assertEqual(sent, [])
            self.env["ENGRAM_ALLOW_REMOTE_TOKEN"] = "1"
            self.assertTrue(client.call("ping", env=self.env)["ok"])
            self.assertIn(b"secret", sent[0])
        finally:
            client._exchange = orig

    def test_auth_error_passthrough(self):
        s = self.serve(lambda c: c.sendall(b'{"ok":false,"error":"auth"}\n'), "tcp")
        self.env["ENGRAM_PORT"] = str(s.port)
        self.assertEqual(client.call("ping", env=self.env)["error"], "auth")

    def test_token_never_in_offline_result(self):
        self.env.update(ENGRAM_TOKEN="topsecrettopsecret", ENGRAM_PORT="1")
        self.assertNotIn("topsecret", repr(client.call("ping", env=self.env)))


class ReplyAndTokenFile(Base):
    def test_reply_without_ok_key_is_offline(self):
        for body in (b'{"hello":"world"}\n', b'{}\n', b'{"error":"x"}\n'):
            s = self.serve(lambda c, b=body: (c.sendall(b), c.close()))
            self.assertOffline(client.call("ping", env=self.env))
            s.close()
            os.unlink(os.path.join(self.home, "engram.sock"))

    def _token_path(self, mode):
        p = os.path.join(self.home, "token")
        with open(p, "w") as f:
            f.write("filetoken-1234567890\n")
        os.chmod(p, mode)

    def test_loose_token_file_ignored_and_never_echoed(self):
        s = self.serve(ok_handler)
        for mode in (0o640, 0o644):
            self._token_path(mode)
            r = client.call("ping", env=self.env)
            self.assertTrue(r["ok"])
            self.assertNotIn("filetoken", repr(r))
        self.assertTrue(s.seen)
        self.assertTrue(all("token" not in req for req in s.seen))

    def test_private_token_file_used(self):
        s = self.serve(ok_handler)
        self._token_path(0o600)
        self.assertTrue(client.call("ping", env=self.env)["ok"])
        self.assertEqual(s.seen[0]["token"], "filetoken-1234567890")


class Autostart(Base):
    def test_noop_in_container_mode(self):
        self.env["ENGRAM_CONTAINER"] = "1"
        t = time.monotonic()
        self.assertFalse(client.autostart(env=self.env, cmd=["/nonexistent"]))
        self.assertLess(time.monotonic() - t, 0.5)
        self.assertFalse(os.path.exists(os.path.join(self.home, "autostart.lock")))

    def test_noop_in_tcp_mode(self):
        self.env["ENGRAM_PORT"] = "1"
        self.assertFalse(client.autostart(env=self.env))
        self.assertFalse(os.path.exists(os.path.join(self.home, "autostart.lock")))

    def test_already_up(self):
        self.serve(ok_handler)
        self.assertTrue(client.autostart(env=self.env, cmd=["/nonexistent"]))

    def test_spawns_once_and_connects(self):
        script = os.path.join(self.home, "fakedaemon.py")
        with open(script, "w") as f:
            f.write(
                "import os,socket,time\n"
                "h=os.environ['ENGRAM_HOME']\n"
                "open(os.path.join(h,'spawned'),'a').write('x')\n"
                "s=socket.socket(socket.AF_UNIX);s.bind(os.path.join(h,'engram.sock'));s.listen(4)\n"
                "s.settimeout(8)\n"
                "end=time.time()+8\n"
                "while time.time()<end:\n"
                "  c,_=s.accept();c.recv(4096);c.sendall(b'{\"ok\":true}\\n');c.close()\n")
        pids = []
        orig = subprocess.Popen

        def spy(*a, **k):
            p = orig(*a, **k)
            pids.append(p.pid)
            return p
        client.subprocess.Popen = spy
        try:
            r1 = client.autostart(env=self.env, wait=3.0, cmd=[sys.executable, script])
            r2 = client.autostart(env=self.env, wait=3.0, cmd=[sys.executable, script])
            self.assertEqual((r1, r2), (True, True))
            self.assertEqual(len(pids), 1)
            with open(os.path.join(self.home, "spawned")) as f:
                self.assertEqual(f.read(), "x")
        finally:
            client.subprocess.Popen = orig
            for p in pids:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass

    def _no_spawn(self, wait):
        calls = []
        orig = client.subprocess.Popen
        client.subprocess.Popen = lambda *a, **k: calls.append(1)
        try:
            t = time.monotonic()
            r = client.autostart(env=self.env, wait=wait, cmd=["x"])
            return r, calls, time.monotonic() - t
        finally:
            client.subprocess.Popen = orig

    def test_spawn_failure_bounded_then_cooldown(self):
        t = time.monotonic()
        self.assertFalse(client.autostart(env=self.env, wait=0.5, cmd=["/nonexistent/daemon"]))
        self.assertLess(time.monotonic() - t, 1.5)
        r, calls, _ = self._no_spawn(0.3)
        self.assertEqual(calls, [])

    def test_lock_held_by_other_does_not_spawn_or_hang(self):
        lf = open(os.path.join(self.home, "autostart.lock"), "a+")
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            r, calls, el = self._no_spawn(0.5)
        finally:
            lf.close()
        self.assertFalse(r)
        self.assertEqual(calls, [])
        self.assertLess(el, 1.5)

    def test_hung_daemon_does_not_hang_autostart(self):
        self.serve(lambda c: time.sleep(5))
        t = time.monotonic()
        self.assertFalse(client.autostart(env=self.env, wait=0.6, cmd=["/nonexistent"]))
        self.assertLess(time.monotonic() - t, 2.0)


if __name__ == "__main__":
    unittest.main()
