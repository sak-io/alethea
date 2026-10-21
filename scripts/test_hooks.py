"""T22: Claude Code hook script against a REAL daemon (temp ENGRAM_HOME), run as a subprocess
with JSON on stdin exactly like Claude Code does. `python3 -m unittest scripts.test_hooks -v`."""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from engram import client
from tests.daemon_helpers import ROOT, InProc, make_home, wait_up

HOOK = os.path.join(ROOT, "engram", "hooks", "engram_hook.py")


def run_hook(payload, home, cwd=None, argv=(), env_extra=None, raw=None, timeout=15):
    """Return (rc, stdout bytes, stderr bytes, seconds)."""
    env = {"PATH": os.environ.get("PATH", ""), "ENGRAM_HOME": home,
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(env_extra or {})
    data = raw if raw is not None else json.dumps(payload).encode("utf-8")
    t = time.monotonic()
    p = subprocess.Popen([sys.executable, HOOK] + list(argv), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         cwd=cwd or tempfile.gettempdir())
    out, err = p.communicate(data, timeout=timeout)
    return p.returncode, out, err, time.monotonic() - t


def rows(home, sql, args=()):
    db = sqlite3.connect(os.path.join(home, "engram.db"), timeout=5)
    try:
        return db.execute(sql, args).fetchall()
    finally:
        db.close()


def wait_for(fn, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


class FakeDaemon:
    """Unix-socket server: mode 'hang' accepts and never replies; 'huge' replies a big briefing."""

    def __init__(self, mode):
        self.home = make_home()
        self.mode, self.conns = mode, []
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(os.path.join(self.home, "engram.sock"))
        self.sock.listen(8)
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            self.conns.append(c)
            if self.mode == "huge":
                threading.Thread(target=self._reply, args=(c,), daemon=True).start()

    def _reply(self, c):
        buf = b""
        while not buf.endswith(b"\n"):
            d = c.recv(65536)
            if not d:
                return
            buf += d
        body = {"ok": True, "text": "é" * 50000, "count": 1}
        c.sendall(json.dumps(body).encode() + b"\n")

    def stop(self):
        self.sock.close()
        for c in self.conns:
            c.close()
        shutil.rmtree(self.home, ignore_errors=True)


class LiveDaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = InProc()
        cls.home = cls.d.home

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def hook(self, payload, **kw):
        return run_hook(payload, self.home, **kw)

    def test_user_prompt_journals_and_prints_nothing(self):
        rc, out, err, _ = self.hook({"hook_event_name": "UserPromptSubmit", "session_id": "s-up",
                                     "cwd": self.home, "prompt": "uniqprompt alpha one"})
        self.assertEqual((rc, out, err), (0, b"", b""))
        r = rows(self.home, "SELECT role,session_id,content FROM journal WHERE session_id='s-up'")
        self.assertEqual(r, [("user", "s-up", "uniqprompt alpha one")])

    def test_stop_journals_last_assistant_message(self):
        rc, out, err, _ = self.hook({"hook_event_name": "Stop", "session_id": "s-stop",
                                     "cwd": self.home, "stop_hook_active": False,
                                     "last_assistant_message": "assistant said bravo two"})
        self.assertEqual((rc, out, err), (0, b"", b""))
        r = rows(self.home, "SELECT role,content FROM journal WHERE session_id='s-stop'")
        self.assertEqual(r, [("assistant", "assistant said bravo two")])

    def test_stop_hook_active_is_respected(self):
        rc, out, _, _ = self.hook({"hook_event_name": "Stop", "session_id": "s-active",
                                   "stop_hook_active": True,
                                   "last_assistant_message": "should not be stored"})
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(rows(self.home, "SELECT 1 FROM journal WHERE session_id='s-active'"), [])

    def test_stop_without_message_does_nothing(self):
        for p in ({}, {"last_assistant_message": None}, {"last_assistant_message": 5},
                  {"last_assistant_message": "  "}):
            p.update(hook_event_name="Stop", session_id="s-nomsg",
                     transcript_path="/nonexistent/x.jsonl")
            rc, out, err, _ = self.hook(p)
            self.assertEqual((rc, out, err), (0, b"", b""))
        self.assertEqual(rows(self.home, "SELECT 1 FROM journal WHERE session_id='s-nomsg'"), [])

    def test_session_start_returns_briefing_for_existing_memory(self):
        fact = "The zebra gateway listens on port 7431 for ingest"
        self.assertTrue(self.d.call("remember", {"content": fact, "kind": "semantic"}).get("ok"))
        rc, out, err, _ = self.hook({"hook_event_name": "SessionStart", "session_id": "s-start",
                                     "cwd": self.home, "source": "startup"})
        self.assertEqual((rc, err), (0, b""))
        doc = json.loads(out.decode("utf-8"))
        self.assertEqual(list(doc), ["hookSpecificOutput"])
        hso = doc["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        self.assertIn("zebra gateway", hso["additionalContext"])
        self.assertLessEqual(len(hso["additionalContext"]), 8000)
        self.assertEqual(out.count(b"\n"), 0 if not out.endswith(b"\n") else 1)

    def test_session_end_consolidates_in_background(self):
        sid = "s-end"
        self.hook({"hook_event_name": "UserPromptSubmit", "session_id": sid,
                   "prompt": "We decided that the quokka service uses port 9911 always."})
        rc, out, err, secs = self.hook({"hook_event_name": "SessionEnd", "session_id": sid,
                                        "reason": "other"})
        self.assertEqual((rc, out, err), (0, b"", b""))
        self.assertLess(secs, 1.5)
        ended = wait_for(lambda: rows(self.home, "SELECT ended_at FROM sessions WHERE id=?",
                                      (sid,))[0][0] is not None)
        self.assertTrue(ended)
        done = wait_for(lambda: rows(
            self.home, "SELECT COUNT(*) FROM journal WHERE session_id=? AND consolidated=0",
            (sid,))[0][0] == 0)
        self.assertTrue(done, "session_end did not consolidate")

    def test_precompact_runs_non_deep_sleep_without_waiting(self):
        before = self.d.call("status").get("last_sleep")
        rc, out, err, secs = self.hook({"hook_event_name": "PreCompact", "session_id": "s-pc",
                                        "trigger": "auto"})
        self.assertEqual((rc, out, err), (0, b"", b""))
        self.assertLess(secs, 1.5)
        self.assertTrue(wait_for(lambda: self.d.call("status").get("last_sleep") != before),
                        "no sleep ran")

    def test_daemon_completes_request_after_client_disconnects(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(os.path.join(self.home, "engram.sock"))
        req = {"verb": "observe", "content": "disconnect-proof charlie three",
               "session_id": "s-disc"}
        s.sendall(json.dumps(req).encode() + b"\n")
        s.close()
        self.assertTrue(wait_for(lambda: rows(
            self.home, "SELECT 1 FROM journal WHERE session_id='s-disc'")))

    def test_unknown_and_missing_events_do_nothing(self):
        for payload, argv in (({"hook_event_name": "Bogus", "prompt": "x"}, ()),
                              ({"prompt": "no event unknownzz"}, ()),
                              ({"hook_event_name": 7}, ()),
                              ({"prompt": "argv unknown unknownzz"}, ("Nope",))):
            rc, out, err, _ = self.hook(payload, argv=argv)
            self.assertEqual((rc, out, err), (0, b"", b""))
        self.assertEqual(rows(self.home, "SELECT 1 FROM journal WHERE content LIKE '%unknownzz%'"),
                         [])

    def test_event_from_argv_fallback(self):
        rc, out, _, _ = self.hook({"prompt": "argvfallback delta four", "session_id": "s-argv"},
                                  argv=("UserPromptSubmit",))
        self.assertEqual((rc, out), (0, b""))
        self.assertTrue(rows(self.home, "SELECT 1 FROM journal WHERE session_id='s-argv'"))

    def test_malformed_stdin_exits_zero_with_empty_stdout(self):
        cases = [b"", b"not json at all", b"[1,2,3]", b'"str"', b"null", b"\xff\xfe\x00\x01{",
                 os.urandom(4096), b"{" * 100000, b'{"hook_event_name":"UserPromptSubmit"',
                 b'{"hook_event_name":"UserPromptSubmit","prompt":"\xff\xfe"}',
                 b'{"hook_event_name":"UserPromptSubmit","prompt":["a"],"cwd":5,"session_id":[]}',
                 b'{"x":"' + b"a" * (10 << 20) + b'"}']
        for raw in cases:
            for argv in ((), ("SessionStart",), ("Stop",)):
                rc, out, err, secs = self.hook(None, raw=raw, argv=argv)
                self.assertEqual((rc, err), (0, b""), raw[:30])
                if argv == ("SessionStart",) and out:
                    hso = json.loads(out.decode())["hookSpecificOutput"]
                    self.assertEqual(hso["hookEventName"], "SessionStart")
                else:
                    self.assertEqual(out, b"")
                self.assertLess(secs, 2.5)
        # garbage never turns into non-SessionStart output
        rc, out, _, _ = self.hook(None, raw=b"[1]", argv=("UserPromptSubmit",))
        self.assertEqual((rc, out), (0, b""))

    def test_huge_prompt_is_capped_and_fast(self):
        big = "hugeprompt " + "word " * 200_000  # ~1 MB
        rc, out, _, secs = self.hook({"hook_event_name": "UserPromptSubmit",
                                      "session_id": "s-big", "prompt": big})
        self.assertEqual((rc, out), (0, b""))
        self.assertLess(secs, 2.5)
        r = rows(self.home, "SELECT LENGTH(content) FROM journal WHERE session_id='s-big'")
        self.assertEqual(len(r), 1)
        self.assertLessEqual(r[0][0], 200_000)

    def test_secret_in_prompt_never_reaches_database(self):
        secret = "AKIAQ3EXAMPLEKEY7654"
        secret2 = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        self.hook({"hook_event_name": "UserPromptSubmit", "session_id": "s-sec",
                   "prompt": "deploy with aws key %s and token %s now, port 8123 for api" % (
                       secret, secret2)})
        self.hook({"hook_event_name": "Stop", "session_id": "s-sec",
                   "last_assistant_message": "Using %s as requested" % secret2})
        self.assertTrue(rows(self.home, "SELECT 1 FROM journal WHERE session_id='s-sec'"))
        self.d.engram.cortex.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        blob = b""
        for name in ("engram.db", "engram.db-wal"):
            p = os.path.join(self.home, name)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    blob += f.read()
        self.assertNotIn(secret2.encode(), blob)
        self.assertNotIn(secret.encode(), blob)

    def test_twenty_concurrent_invocations(self):
        results = []

        def one(i):
            results.append(run_hook(
                {"hook_event_name": "UserPromptSubmit", "session_id": "s-conc",
                 "prompt": "concurrent prompt number %d marker qq%d" % (i, i)}, self.home))

        ts = [threading.Thread(target=one, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(results), 20)
        for rc, out, err, secs in results:
            self.assertEqual((rc, out, err), (0, b"", b""))
        n = rows(self.home, "SELECT COUNT(*) FROM journal WHERE session_id='s-conc'")[0][0]
        self.assertEqual(n, 20)

    def test_git_worktree_and_non_git_cwd(self):
        tmp = tempfile.mkdtemp(prefix="egw")
        self.addCleanup(shutil.rmtree, tmp, True)
        main, wt, plain = (os.path.join(tmp, n) for n in ("main", "wt", "plain"))
        os.mkdir(main)
        os.mkdir(plain)
        g = ["git", "-c", "user.email=a@b.c", "-c", "user.name=t"]
        for cmd in (["git", "init", "-q"], g + ["commit", "-q", "--allow-empty", "-m", "i"],
                    g + ["worktree", "add", "-q", wt]):
            subprocess.run(cmd, cwd=main, check=True, capture_output=True)
        for sid, cwd in (("s-wt", wt), ("s-plain", plain)):
            rc, out, err, _ = self.hook({"hook_event_name": "UserPromptSubmit",
                                         "session_id": sid, "cwd": cwd,
                                         "prompt": "where am i %s" % sid}, cwd=tmp)
            self.assertEqual((rc, out, err), (0, b"", b""))
        q = "SELECT repo_root,cwd FROM journal WHERE session_id=?"
        self.assertEqual(rows(self.home, q, ("s-wt",)),
                         [(os.path.realpath(main), wt)])
        self.assertEqual(rows(self.home, q, ("s-plain",)), [(None, plain)])
        rc, out, _, _ = self.hook({"hook_event_name": "SessionStart", "session_id": "s-wt2",
                                   "cwd": wt}, cwd=plain)
        self.assertEqual(rc, 0)
        if out:
            json.loads(out.decode())

    def test_debug_flag_is_one_line_without_content(self):
        rc, out, err, _ = self.hook({"hook_event_name": "UserPromptSubmit",
                                     "prompt": "topsecretcontent zulu"},
                                    env_extra={"ENGRAM_HOOK_DEBUG": "1"})
        self.assertEqual((rc, out), (0, b""))
        self.assertEqual(err.count(b"\n"), 1)
        self.assertNotIn(b"topsecretcontent", err)


class OfflineTests(unittest.TestCase):
    def test_daemon_down_all_events_exit_zero_fast_and_silent(self):
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        env = {"ENGRAM_PORT": "1"}  # TCP to a closed port: down, and autostart disabled
        for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreCompact", "X"):
            rc, out, err, secs = run_hook(
                {"hook_event_name": ev, "session_id": "s", "prompt": "p",
                 "last_assistant_message": "m"}, home, env_extra=env)
            self.assertEqual((rc, out, err), (0, b"", b""), ev)
            self.assertLess(secs, 2.0, ev)

    def test_hung_daemon_stays_within_deadline(self):
        fake = FakeDaemon("hang")
        self.addCleanup(fake.stop)
        for ev in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreCompact"):
            rc, out, err, secs = run_hook(
                {"hook_event_name": ev, "session_id": "s", "prompt": "p",
                 "last_assistant_message": "m"}, fake.home)
            self.assertEqual((rc, out, err), (0, b"", b""), ev)
            self.assertLess(secs, 2.4, ev)

    def test_hung_stdin_read_hits_watchdog(self):
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        env = {"PATH": os.environ.get("PATH", ""), "ENGRAM_HOME": home}
        t = time.monotonic()
        p = subprocess.Popen([sys.executable, HOOK, "UserPromptSubmit"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            p.wait(timeout=6)  # stdin pipe left open and empty: read blocks
            out, err = p.stdout.read(), p.stderr.read()
        finally:
            p.stdin.close()
            p.stdout.close()
            p.stderr.close()
        self.assertEqual((p.returncode, out, err), (0, b"", b""))
        self.assertLess(time.monotonic() - t, 3.0)

    def test_huge_briefing_is_capped_to_8000_chars_valid_json(self):
        fake = FakeDaemon("huge")
        self.addCleanup(fake.stop)
        rc, out, err, _ = run_hook({"hook_event_name": "SessionStart", "session_id": "s"},
                                   fake.home)
        self.assertEqual((rc, err), (0, b""))
        doc = json.loads(out.decode("utf-8"))
        self.assertEqual(doc["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertEqual(len(doc["hookSpecificOutput"]["additionalContext"]), 8000)
        self.assertEqual(out.strip().count(b"\n"), 0)

    def test_empty_briefing_prints_nothing(self):
        d = InProc()
        self.addCleanup(d.stop)
        rc, out, err, _ = run_hook({"hook_event_name": "SessionStart", "session_id": "s",
                                    "cwd": d.home}, d.home)
        self.assertEqual((rc, err), (0, b""))
        if out:  # a fresh store may still produce a header; then it must be valid and capped
            self.assertLessEqual(len(json.loads(out.decode())["hookSpecificOutput"]
                                     ["additionalContext"]), 8000)

    def test_offline_session_start_autostarts_daemon_without_waiting(self):
        home = make_home()
        def cleanup():
            r = client.call("status", timeout=1, env={"ENGRAM_HOME": home})
            if r.get("pid"):
                try:
                    os.kill(r["pid"], 15)
                except OSError:
                    pass
            time.sleep(0.3)
            shutil.rmtree(home, ignore_errors=True)

        self.addCleanup(cleanup)
        rc, out, err, secs = run_hook({"hook_event_name": "SessionStart", "session_id": "s",
                                       "cwd": home}, home)
        self.assertEqual((rc, out, err), (0, b"", b""))
        self.assertLess(secs, 2.0)
        self.assertTrue(wait_up(home, 10), "daemon was not autostarted")


if __name__ == "__main__":
    unittest.main()
