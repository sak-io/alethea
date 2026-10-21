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
    env = dict(os.environ, ENGRAM_HOME=home, PYTHONDONTWRITEBYTECODE="1")
    for k in ("ENGRAM_PORT", "ENGRAM_HOST", "ENGRAM_TOKEN", "PYTHONPATH"):
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
        r = run(self.home, "status", extra_env={"ENGRAM_TOKEN": tok})
        self.assertNotIn(tok, r.stdout + r.stderr)


class Offline(unittest.TestCase):
    def setUp(self):
        self.home = make_home()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_every_daemon_command_fails_soft_with_exit_3(self):
        for args in (["status"], ["sleep"], ["recall", "x"], ["remember", "x"],
                     ["forget", "abc"], ["dump"]):
            r = run(self.home, *args)
            self.assertEqual(r.returncode, 3, args)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.stdout, "", args)
            self.assertTrue(r.stderr.strip(), args)

    def test_status_down_suggests_up(self):
        r = run(self.home, "status")
        self.assertIn("down", r.stderr)
        self.assertIn("mnemo up", r.stderr)

    def test_offline_json_is_valid(self):
        r = run(self.home, "--json", "status")
        self.assertEqual(r.returncode, 3)
        self.assertTrue(json.loads(r.stdout)["offline"])


class Usage(unittest.TestCase):
    def test_unknown_subcommand_and_bad_args(self):
        for args in (["bogus"], [], ["recall"], ["recall", "x", "-k", "0"],
                     ["remember", "x", "--salience", "7"], ["remember", "x", "--kind", "nope"]):
            r = run("/nonexistent-engram-home", *args)
            self.assertEqual(r.returncode, 2, args)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(r.stdout, "")

    def test_help_documents_exit_codes(self):
        r = run("/nonexistent-engram-home", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("exit codes", r.stdout)

    def test_later_tasks_say_not_yet_available(self):
        for c in ("export-rules", "reembed"):
            r = run("/nonexistent-engram-home", c)
            self.assertEqual(r.returncode, 4)
            self.assertIn("not yet available", r.stderr)


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

    def test_up_in_tcp_mode_fails_soft(self):
        home = make_home()
        try:
            r = run(home, "up", "--timeout", "0.5", extra_env={"ENGRAM_PORT": "1"})
            self.assertEqual(r.returncode, 3)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
