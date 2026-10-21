import json
import os
import shutil
import signal
import socket
import stat
import threading
import time
import unittest

from engram import client, daemon
from tests.daemon_helpers import InProc, make_home, spawn, wait_up


def raw(home, data, wait=True, timeout=5):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(os.path.join(home, "engram.sock"))
    if data:
        try:
            s.sendall(data)
        except OSError:
            pass
    if not wait:
        return s
    buf = b""
    try:
        while not buf.endswith(b"\n"):
            c = s.recv(65536)
            if not c:
                break
            buf += c
    except OSError:
        pass
    s.close()
    return buf


class VerbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = InProc()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_ping_and_files(self):
        self.assertTrue(client.ping(env=self.d.env))
        h = self.d.home
        self.assertEqual(stat.S_IMODE(os.stat(h).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(h + "/engram.sock").st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(h + "/token").st_mode), 0o600)
        with open(h + "/daemon.json") as f:
            self.assertIn("pid", json.load(f))

    def test_remember_recall_forget_dump(self):
        r = self.d.call("remember", {"content": "The deploy key rotation procedure uses vault7 weekly",
                                     "repo_root": "/r/x"})
        self.assertTrue(r["ok"] and r["status"] == "created", r)
        got = self.d.call("recall", {"cue": "deploy key rotation vault7"})
        self.assertTrue(got["ok"])
        self.assertIn(r["id"], [m["id"] for m in got["results"]])
        self.assertNotIn("vector", got["results"][0])
        dump = self.d.call("dump", {"limit": 100000})
        self.assertFalse(dump["ok"])  # over the cap is rejected
        dump = self.d.call("dump", {"limit": daemon.DUMP_MAX})
        self.assertLessEqual(len(dump["memories"]), daemon.DUMP_MAX)
        self.assertEqual(self.d.call("forget", {"id": r["id"]}), {"ok": True, "forgotten": True})
        m = [x for x in self.d.call("dump", {"limit": 500})["memories"] if x["id"] == r["id"]][0]
        self.assertEqual(m["status"], "suppressed")
        self.assertIn("suppressed_at", m["meta"])
        self.assertFalse(self.d.call("forget", {"id": "nope"})["ok"])

    def test_repo_root_passed_wins_over_gitctx(self):
        r = self.d.call("remember", {"content": "Repo scoped fact alpha9 lives in the monorepo",
                                     "cwd": "/definitely/not/a/dir", "repo_root": "/given/root"})
        mem = [m for m in self.d.call("dump", {"limit": 500})["memories"] if m["id"] == r["id"]][0]
        self.assertEqual(mem["repo_root"], "/given/root")

    def test_observe_briefing_reflect_session_end_sleep(self):
        r = self.d.call("observe", {"content": "We decided to always use WAL mode for sqlite stores",
                                    "session_id": "s1", "cwd": "/tmp", "repo_root": "/r/y"})
        self.assertTrue(r["ok"] and r["journal_id"])
        f = self.d.call("remember", {"content": "Cache invalidation bug caused by stale etag4 header"})
        rf = self.d.call("reflect", {"content": "Stale headers are a recurring cause of cache bugs",
                                     "cited_ids": [f["id"]]})
        self.assertTrue(rf["ok"], rf)
        b = self.d.call("briefing", {"repo_root": "/r/y"})
        self.assertTrue(b["ok"] and isinstance(b["text"], str))
        self.assertTrue(self.d.call("session_end", {"session_id": "s1", "wait": True})["ok"])
        self.assertTrue(self.d.call("session_end", {"session_id": "s1"})["ok"])
        s = self.d.call("sleep", {})
        self.assertTrue(s["ok"], s)

    def test_status_has_no_secrets_or_content(self):
        self.d.call("remember", {"content": "TOPSECRETCONTENT marker fact zz91"})
        s = self.d.call("status")
        with open(self.d.home + "/token") as f:
            tok = f.read().strip()
        blob = json.dumps(s)
        self.assertNotIn(tok, blob)
        self.assertNotIn("TOPSECRETCONTENT", blob)
        for k in ("counts", "last_sleep", "embedder_id", "version"):
            self.assertIn(k, s)

    def test_token_never_echoed_or_stored(self):
        r = self.d.call("remember", {"content": "Token leak probe fact qq77 stays clean",
                                     "token": "SEKRET"})
        self.assertTrue(r["ok"])
        self.assertNotIn("SEKRET", json.dumps(self.d.call("dump", {"limit": 500})))

    def test_validation_errors_not_tracebacks(self):
        bad = [("remember", {}), ("remember", {"content": 5}),
               ("remember", {"content": "x" * 300000}),
               ("remember", {"content": "ok fact", "kind": "bogus"}),
               ("remember", {"content": "ok fact", "salience": "high"}),
               ("recall", {}), ("recall", {"cue": ["a"]}), ("recall", {"cue": "a", "k": -1}),
               ("recall", {"cue": "a" * 10000}), ("forget", {}), ("forget", {"id": 3}),
               ("reflect", {"content": "x y z", "cited_ids": "abc"}),
               ("dump", {"limit": "many"}), ("session_end", {}), ("nope", {}),
               ("export_rules", {}), ("reembed", {}), ("pending", {})]
        for verb, p in bad:
            r = self.d.call(verb, p)
            self.assertIs(r.get("ok"), False, (verb, p, r))
            self.assertIn("error", r)
            self.assertNotIn("Traceback", json.dumps(r))
        self.assertEqual(self.d.call("export_rules")["error"], "unsupported")
        self.assertTrue(self.d.call("ping")["ok"])

    def test_non_object_and_garbage_requests(self):
        for data in (b"[1,2]\n", b"42\n", b"not json\n", b'{"verb": 5}\n', b'{"x":1}\n',
                     b"\xff\xfe\n"):
            r = json.loads(raw(self.d.home, data))
            self.assertFalse(r["ok"])
        self.assertTrue(client.ping(env=self.d.env))


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.d = InProc(read_timeout=1.0, max_conns=4)

    def tearDown(self):
        self.d.stop()

    def test_oversize_request_rejected_and_closed(self):
        line = b'{"verb":"remember","content":"' + b"a" * ((1 << 20) + 8) + b'"}\n'
        buf = raw(self.d.home, line)
        self.assertFalse(json.loads(buf)["ok"])
        self.assertTrue(client.ping(env=self.d.env))

    def test_oversize_without_newline(self):
        buf = raw(self.d.home, b"a" * ((1 << 20) + 10))
        self.assertIn(b"too large", buf)
        self.assertTrue(client.ping(env=self.d.env))

    def test_slow_loris_does_not_block_good_client(self):
        loris = [raw(self.d.home, b'{"verb":"pi', wait=False) for _ in range(3)]
        t0 = time.time()
        r = self.d.call("remember", {"content": "Loris neighbour fact kk31 persists"}, timeout=2)
        self.assertTrue(r["ok"], r)
        self.assertLess(time.time() - t0, 2)
        time.sleep(1.3)  # read deadline (1.0 s here) drops the loris connections
        for s in loris:
            s.settimeout(2)
            data = s.recv(4096)
            self.assertIn(b"timeout", data)
            s.close()
        self.assertTrue(client.ping(env=self.d.env))

    def test_idle_sockets_do_not_starve_good_client(self):
        idle = [raw(self.d.home, b"", wait=False) for _ in range(200)]
        try:
            t0 = time.time()
            r = self.d.call("remember", {"content": "Served despite idle flood fact ee63"},
                            timeout=2)
            self.assertTrue(r["ok"], r)
            self.assertLess(time.time() - t0, 1.0)
            time.sleep(1.4)  # first-byte deadline (1.0 s here) reaps the idle sockets
            for s in idle[:5]:
                s.settimeout(1)
                self.assertEqual(s.recv(10), b"")
        finally:
            for s in idle:
                s.close()
        self.assertTrue(client.ping(env=self.d.env))

    def test_work_slots_exhausted_replies_busy(self):
        gate = threading.Event()
        self.d.engram.verbs["block"] = lambda p: gate.wait(10) and {"ok": True}
        ts = [threading.Thread(target=self.d.call, args=("block", None, 12)) for _ in range(4)]
        for t in ts:
            t.start()
        time.sleep(0.4)
        try:
            self.assertIn(b"busy", raw(self.d.home, b'{"verb":"ping"}\n'))
        finally:
            gate.set()
            for t in ts:
                t.join()
        self.assertTrue(client.ping(env=self.d.env))

    def test_deeply_nested_json_gets_reply(self):
        """Depth 10000 raises RecursionError on CPython <= 3.13 ("invalid json") but parses on
        3.14+ (then it is a valid JSON non-object). Either way the client must get a structured
        error and the server must survive."""
        buf = raw(self.d.home, b"[" * 10000 + b"]" * 10000 + b"\n")
        r = json.loads(buf)
        self.assertIs(r["ok"], False)
        self.assertIn(r["error"], ("invalid json", "request must be an object"))
        self.assertTrue(client.ping(env=self.d.env))

    def test_very_deeply_nested_json_hits_recursion_path_on_every_python(self):
        """400000 levels (800 KB, under the 1 MB request cap) raises RecursionError on every
        supported CPython, so this always exercises the RecursionError -> "invalid json" reply."""
        for payload in (b"[" * 400000 + b"]" * 400000,
                        b'{"verb":"ping","x":' + b"[" * 400000 + b"]" * 400000 + b"}"):
            buf = raw(self.d.home, payload + b"\n")
            self.assertEqual(json.loads(buf), {"ok": False, "error": "invalid json"})
            self.assertTrue(client.ping(env=self.d.env))

    def test_exception_in_verb_does_not_kill_server(self):
        self.d.engram.verbs["boom"] = lambda p: 1 / 0
        r = self.d.call("boom")
        self.assertEqual(r, {"ok": False, "error": "internal error"})
        self.assertTrue(client.ping(env=self.d.env))


class SleepLoopTests(unittest.TestCase):
    def test_timer_rule_with_injected_clock(self):
        now = [1000.0]
        d = InProc(mono=lambda: now[0], clock=lambda: now[0])
        try:
            e = d.engram
            e.sleep_interval, e.idle_sleep = 900, 180
            self.assertFalse(e.sleep_tick())  # no activity
            d.call("remember", {"content": "Loop probe fact vv12 exists"})
            now[0] += 100
            self.assertFalse(e.sleep_tick())  # interval not elapsed
            now[0] += 800
            e.last_activity = now[0] - 10
            self.assertFalse(e.sleep_tick())  # interval elapsed but not idle
            e.last_activity = now[0] - 200
            self.assertTrue(e.sleep_tick())
            self.assertIsNotNone(d.call("status")["last_sleep"])
            self.assertFalse(e.sleep_tick())  # nothing new since
        finally:
            d.stop()

    def test_env_overrides(self):
        home = make_home()
        try:
            e = daemon.Engram(home=home, env={"ENGRAM_HOME": home, "ENGRAM_SLEEP_INTERVAL": "5",
                                              "ENGRAM_IDLE_SLEEP": "2"})
            self.assertEqual((e.sleep_interval, e.idle_sleep), (5, 2))
            e.close()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_sleep_loop_survives_errors(self):
        d = InProc()
        try:
            d.engram.sleep_due = lambda: 1 / 0
            self.assertFalse(d.engram.sleep_tick())
            self.assertTrue(d.call("ping")["ok"])
        finally:
            d.stop()


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.home = make_home()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
            p.stderr.close()
        shutil.rmtree(self.home, ignore_errors=True)

    def start(self):
        p = spawn(self.home)
        self.procs.append(p)
        self.assertTrue(wait_up(self.home), p.poll())
        return p

    def test_second_daemon_exits_quietly(self):
        a = self.start()
        b = spawn(self.home)
        self.procs.append(b)
        self.assertEqual(b.wait(timeout=10), 0)
        self.assertEqual(b.stderr.read(), b"")
        self.assertTrue(client.ping(env={"ENGRAM_HOME": self.home}))
        self.assertIsNone(a.poll())

    def test_sigterm_cleans_up(self):
        p = self.start()
        env = {"ENGRAM_HOME": self.home}
        client.call("remember", {"content": "Persist across shutdown fact ww55"}, env=env)
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertFalse(os.path.exists(self.home + "/engram.sock"))
        self.assertFalse(os.path.exists(self.home + "/daemon.json"))
        self.assertFalse(client.ping(env=env))
        wal = self.home + "/engram.db-wal"
        self.assertTrue(not os.path.exists(wal) or os.path.getsize(wal) == 0)

    def test_sigint_cleans_up(self):
        p = self.start()
        p.send_signal(signal.SIGINT)
        self.assertEqual(p.wait(timeout=10), 0)
        self.assertFalse(os.path.exists(self.home + "/engram.sock"))

    def test_kill9_mid_request_then_recovery(self):
        env = {"ENGRAM_HOME": self.home}
        p = self.start()
        acked = []
        for i in range(5):
            r = client.call("remember", {"content": "Acked fact number zq%d about ports%d"
                                         % (i, i * 7)}, env=env)
            self.assertTrue(r["ok"], r)
            acked.append(r["id"])
        stop = threading.Event()
        results = []

        def hammer():
            n = 0
            while not stop.is_set():
                n += 1
                results.append(client.call(
                    "remember", {"content": "Inflight fact hh%d marker%d" % (n, n * 3)},
                    timeout=2, env=env))

        t = threading.Thread(target=hammer)
        t.start()
        time.sleep(0.3)
        p.kill()  # kill -9 mid-request
        p.wait()
        time.sleep(0.3)
        stop.set()
        t.join(timeout=10)
        self.assertFalse(t.is_alive())
        self.assertEqual(client.call("ping", env=env), {"ok": False, "offline": True})
        self.assertTrue(any(r.get("offline") for r in results))
        self.assertTrue(os.path.exists(self.home + "/engram.sock"))  # stale socket left behind
        self.start()  # stale socket cleaned, DB recovered
        ids = {m["id"] for m in client.call("dump", {"limit": 500}, env=env)["memories"]}
        self.assertTrue(set(acked) <= ids)
        self.assertTrue(client.call("remember", {"content": "Post recovery fact rr44 works"},
                                    env=env)["ok"])


class ClientMinorTests(unittest.TestCase):
    def setUp(self):
        self.home = make_home()
        self.env = {"ENGRAM_HOME": self.home}

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_reply_without_ok_is_offline(self):
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(self.home + "/engram.sock")
        srv.listen(1)

        def serve():
            c, _ = srv.accept()
            c.recv(4096)
            c.sendall(b'{"hello":"world"}\n')
            c.close()
        t = threading.Thread(target=serve)
        t.start()
        self.assertEqual(client.call("ping", env=self.env), {"ok": False, "offline": True})
        t.join()
        srv.close()

    def test_loose_token_file_ignored(self):
        p = self.home + "/token"
        with open(p, "w") as f:
            f.write("sekret-token-123456")
        for mode in (0o644, 0o640):
            os.chmod(p, mode)
            self.assertIsNone(client._token(self.env, self.home))
        os.chmod(p, 0o600)
        self.assertEqual(client._token(self.env, self.home), "sekret-token-123456")


if __name__ == "__main__":
    unittest.main()
