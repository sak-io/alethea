import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

from tests.daemon_helpers import ROOT, InProc, make_home

MNEMO = os.path.join(ROOT, "scripts", "mnemo")


def run(home, *args, stdin=None, cwd=None, exe=MNEMO, extra_env=None):
    env = dict(os.environ, ALETHEA_HOME=home, PYTHONDONTWRITEBYTECODE="1")
    for k in ("ALETHEA_PORT", "ALETHEA_HOST", "ALETHEA_TOKEN", "PYTHONPATH"):
        env.pop(k, None)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, exe] + list(args), input=stdin, env=env,
                          cwd=cwd or tempfile.gettempdir(), capture_output=True,
                          text=True, timeout=60)


class WithDaemon(unittest.TestCase):
    def setUp(self):
        self.d = InProc()
        self.home = self.d.home

    def tearDown(self):
        self.d.stop()


class RoundTrip(WithDaemon):
    def test_remember_recall_dump_forget_round_trip(self):
        r = run(self.home, "remember", "--kind", "semantic",
                "Always run the zebra migration before deploying the quokka service")
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = r.stdout.strip()
        self.assertTrue(mid and "\n" not in mid)
        r = run(self.home, "recall", "zebra migration quokka")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(mid, r.stdout)
        r = run(self.home, "dump", "--kind", "semantic")
        self.assertIn(mid, r.stdout)
        r = run(self.home, "forget", mid)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(self.home, "forget", "no-such-id")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_status_counts_and_fields(self):
        run(self.home, "remember", "Never delete the walrus index on friday")
        r = run(self.home, "status")
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ("embedder", "fts5", "last sleep: never", "memories: 1"):
            self.assertIn(word, r.stdout)

    def test_sleep(self):
        r = run(self.home, "sleep", "--quick")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("sleep", r.stdout)

    def test_stdin_input(self):
        big = "marmot " * 5000 + "unique-stdin-marker"
        r = run(self.home, "remember", "-", stdin=big)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run(self.home, "recall", "-", stdin="marmot unique-stdin-marker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip())
        r = run(self.home, "remember", "-", stdin="  \n")
        self.assertEqual(r.returncode, 2)

    def test_json_output_valid_for_each_command(self):
        r = run(self.home, "remember", "--json", "Prefer tabs over spaces in the otter repo")
        self.assertEqual(r.returncode, 0, r.stderr)
        mid = json.loads(r.stdout)["id"]
        for args in (["status"], ["recall", "otter tabs"], ["dump"], ["sleep", "--quick"],
                     ["forget", mid]):
            r = run(self.home, "--json", *args)
            self.assertEqual(r.returncode, 0, (args, r.stderr))
            self.assertTrue(json.loads(r.stdout)["ok"], args)

    def test_repo_root_resolved_from_cwd_and_symlink(self):
        repo = tempfile.mkdtemp(prefix="eg")
        linkdir = tempfile.mkdtemp(prefix="eg")
        link = os.path.join(linkdir, "mnemo")
        try:
            subprocess.run(["git", "init", "-q", repo], check=True)
            sub = os.path.join(repo, "a", "b")
            os.makedirs(sub)
            os.symlink(MNEMO, link)
            r = run(self.home, "remember", "--json", "Gecko deploys need the blue key",
                    cwd=sub, exe=link)
            self.assertEqual(r.returncode, 0, r.stderr)
            m = json.loads(run(self.home, "dump", "--json").stdout)["memories"][0]
            self.assertEqual(os.path.realpath(m["repo_root"]), os.path.realpath(repo))
            self.assertEqual(os.path.realpath(m["cwd"]), os.path.realpath(sub))
        finally:
            shutil.rmtree(repo, ignore_errors=True)
            shutil.rmtree(linkdir, ignore_errors=True)

    def test_token_never_printed(self):
        tok = "s3cret-token-value"
        for args in (["status"], ["--json", "status"], ["forget", "nope"],
                     ["--json", "forget", "nope"], ["remember", "x" * 100000],
                     ["--strict", "up"], ["bogus"]):
            r = run(self.home, *args, extra_env={"ALETHEA_TOKEN": tok})
            self.assertNotIn(tok, r.stdout + r.stderr, args)

    def test_oversize_remember_clear_message(self):
        r = run(self.home, "remember", "-", stdin="a" * 5_000_000)
        self.assertEqual(r.returncode, 2)
        self.assertIn("content too large: max 200000", r.stderr)
        self.assertNotIn("not running", r.stderr)

    def test_dropped_request_is_not_reported_offline(self):
        import socket
        import threading
        home = make_home()
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(home, "alethea.sock"))
        srv.listen(8)

        def loop():
            while True:
                try:
                    c, _ = srv.accept()
                except OSError:
                    return
                line = c.makefile("rb").readline()
                if b'"ping"' in line:
                    c.sendall(b'{"ok":true,"pong":true}\n')
                c.close()
        threading.Thread(target=loop, daemon=True).start()
        try:
            r = run(home, "dump")
            self.assertEqual(r.returncode, 1)
            self.assertIn("refused or connection dropped", r.stderr)
        finally:
            srv.close()
            shutil.rmtree(home, ignore_errors=True)

    def test_epipe_with_head(self):
        for i in range(30):  # seeded in-process: 30 mnemo subprocesses cost ~4 s
            self.d.call("remember", {"content": "Penguin fact number %d about glaciers" % i})
        env = dict(os.environ, ALETHEA_HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run("%s %s dump | head -1" % (sys.executable, MNEMO), shell=True,
                           env=env, capture_output=True, text=True, cwd="/tmp")
        self.assertEqual(len(r.stdout.splitlines()), 1)
        self.assertNotIn("Traceback", r.stderr)
        self.assertNotIn("BrokenPipe", r.stderr)

    def test_keyboard_interrupt_exit_130(self):
        code = ("import sys\nfrom alethea import cli\n"
                "def boom(a): raise KeyboardInterrupt\n"
                "cli.COMMANDS['status']=boom\nsys.exit(cli.main(['status']))")
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 130)
        self.assertNotIn("Traceback", r.stderr)


class Offline(unittest.TestCase):
    def setUp(self):
        self.home = make_home()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    CMDS = (["status"], ["sleep"], ["recall", "x"], ["remember", "x"],
            ["forget", "abc"], ["dump"])

    def test_strict_flag_and_env_exit_3(self):
        for args in self.CMDS:
            self.assertEqual(run(self.home, "--strict", *args).returncode, 3, args)
            self.assertEqual(run(self.home, *args, "--strict").returncode, 3, args)
            r = run(self.home, *args, extra_env={"MNEMO_STRICT": "1"})
            self.assertEqual(r.returncode, 3, args)

    def test_up_failing_strict(self):
        env = {"ALETHEA_PORT": "1"}
        self.assertEqual(run(self.home, "up", "--timeout", "0.3", extra_env=env).returncode, 0)
        r = run(self.home, "--strict", "up", "--timeout", "0.3", extra_env=env)
        self.assertEqual(r.returncode, 3)

    def test_hung_daemon_returns_within_budget(self):
        import socket
        import time
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(os.path.join(self.home, "alethea.sock"))
        srv.listen(8)
        try:
            # same code path as the real 10 s TIMEOUT_CLI, shrunk to 1 s to keep the suite fast
            code = ("import sys\nfrom alethea import client, cli\nclient.TIMEOUT_CLI = 1.0\n"
                    "sys.exit(cli.main(['status']))")
            env = dict(os.environ, ALETHEA_HOME=self.home, PYTHONPATH=ROOT,
                       PYTHONDONTWRITEBYTECODE="1")
            t = time.time()
            r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                               text=True, timeout=30)
            self.assertLess(time.time() - t, 8)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            srv.close()

    def test_every_daemon_command_fails_soft_with_exit_0(self):
        for args in self.CMDS:
            r = run(self.home, *args)
            self.assertEqual(r.returncode, 0, args)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.stdout, "", args)
            self.assertTrue(r.stderr.strip(), args)

    def test_status_down_suggests_up(self):
        r = run(self.home, "status")
        self.assertIn("down", r.stderr)
        self.assertIn("mnemo up", r.stderr)

    def test_offline_json_is_valid(self):
        r = run(self.home, "--json", "status")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(json.loads(r.stdout)["offline"])


class Usage(unittest.TestCase):
    def test_unknown_subcommand_and_bad_args(self):
        for args in (["bogus"], [], ["recall"], ["recall", "x", "-k", "0"],
                     ["remember", "x", "--salience", "7"], ["remember", "x", "--kind", "nope"]):
            r = run("/nonexistent-alethea-home", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_help_documents_exit_codes(self):
        r = run("/nonexistent-alethea-home", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("exit codes", r.stdout)

    def test_reembed_offline_is_fail_soft(self):
        r = run("/nonexistent-alethea-home", "reembed", "--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(run("/nonexistent-alethea-home", "--strict", "reembed").returncode, 3)


class Up(unittest.TestCase):
    def test_up_starts_daemon_and_is_idempotent(self):
        home = make_home()
        pid = None
        try:
            r = run(home, "up")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("started", r.stdout)
            pid = json.loads(run(home, "status", "--json").stdout)["pid"]
            r = run(home, "up")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("already running", r.stdout)
            self.assertEqual(json.loads(run(home, "status", "--json").stdout)["pid"], pid)
        finally:
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            shutil.rmtree(home, ignore_errors=True)

    def test_concurrent_up_only_one_claims_started(self):
        home = make_home()
        pid = None
        try:
            env = dict(os.environ, ALETHEA_HOME=home, PYTHONDONTWRITEBYTECODE="1")
            env.pop("ALETHEA_PORT", None)
            ps = [subprocess.Popen([sys.executable, MNEMO, "up"], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                  for _ in range(5)]
            outs = [p.communicate(timeout=60) + (p.returncode,) for p in ps]
            self.assertTrue(all(o[2] == 0 for o in outs), outs)
            self.assertTrue(all("running" in o[0] or "started" in o[0] for o in outs), outs)
            self.assertLessEqual(sum("started" in o[0] for o in outs), 1, outs)
            pid = json.loads(run(home, "status", "--json").stdout)["pid"]
        finally:
            if pid:
                os.kill(pid, signal.SIGTERM)
            shutil.rmtree(home, ignore_errors=True)

    def test_up_in_tcp_mode_fails_soft(self):
        home = make_home()
        try:
            r = run(home, "up", "--timeout", "0.5", extra_env={"ALETHEA_PORT": "1"})
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
