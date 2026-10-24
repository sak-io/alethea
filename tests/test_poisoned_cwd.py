"""cwd shadowing: a project directory containing a fake `alethea/` package must never get its code
executed by Alethea's own commands (hooks run in every Claude Code session in that directory).
`python -m x` puts the CURRENT directory first on sys.path; `-I` (and the explicit-root bootstrap
for the daemon) prevents it. Each test runs the real command line from a poisoned cwd against an
'installed' copy of the package (tests.install_helpers) and asserts the marker never appears.
The control tests prove the poison works when the hardening is absent."""
import json
import os
import subprocess
import sys
import time
import unittest

from alethea import client, install
from tests.daemon_helpers import ROOT, make_home, wait_up
from tests.install_helpers import installed_python, poisoned_cwd, pwned

MNEMO = os.path.join(ROOT, "scripts", "mnemo")


def env_for(home, **extra):
    e = {"PATH": os.environ.get("PATH", ""), "ALETHEA_HOME": home, "PYTHONDONTWRITEBYTECODE": "1"}
    e.update(extra)
    return e


def mcp_messages():
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    return "".join(json.dumps(m) + "\n" for m in msgs).encode()


def daemon_pid(home):
    with open(os.path.join(home, "daemon.json")) as f:
        return int(json.load(f)["pid"])


class PoisonedCwdTest(unittest.TestCase):
    def setUp(self):
        self.py = installed_python()
        self.cwd = poisoned_cwd()
        self.home = make_home()

    def test_control_module_without_isolation_does_execute_the_fake(self):
        p = subprocess.run([self.py, "-m", "alethea.hooks.alethea_hook"], input=b"", cwd=self.cwd,
                           env=env_for(self.home, ALETHEA_PORT="1"), capture_output=True,
                           timeout=15)
        self.assertTrue(pwned(self.cwd), p.stderr)

    def test_installed_hook_command_lines_ignore_the_fake_and_still_work(self):
        block = install.hooks_block(self.py)
        payloads = {"SessionStart": {"source": "startup"},
                    "UserPromptSubmit": {"prompt": "we decided to use PostgreSQL"},
                    "Stop": {"last_assistant_message": "ok"}, "SessionEnd": {"reason": "other"},
                    "PreCompact": {"trigger": "auto"}}
        for event in install.EVENTS:
            h = block["hooks"][event][0]["hooks"][0]
            self.assertEqual(h["args"][0], "-I")
            payload = dict(payloads[event], hook_event_name=event, session_id="s-poison",
                           cwd=self.cwd, transcript_path="/nonexistent")
            p = subprocess.run([h["command"]] + h["args"], input=json.dumps(payload).encode(),
                               cwd=self.cwd, env=env_for(self.home, ALETHEA_PORT="1"),
                               capture_output=True, timeout=15)
            self.assertEqual(p.returncode, 0, (event, p.stderr))
            self.assertEqual(p.stdout, b"", event)
            self.assertFalse(pwned(self.cwd), event)
        # "still works": the real hook module ran (debug note proves it dispatched the event)
        p = subprocess.run([self.py, "-I", "-m", "alethea.hooks.alethea_hook"],
                           input=json.dumps({"hook_event_name": "Stop",
                                             "session_id": "s", "cwd": self.cwd}).encode(),
                           cwd=self.cwd, env=env_for(self.home, ALETHEA_PORT="1",
                                                     ALETHEA_HOOK_DEBUG="1"),
                           capture_output=True, timeout=15)
        self.assertIn(b"alethea-hook:", p.stderr)
        self.assertFalse(pwned(self.cwd))

    def test_mcp_command_ignores_the_fake_and_answers(self):
        cmd = install.mcp_command("user", self.py)
        argv = cmd[cmd.index("--") + 1:]
        self.assertEqual(argv[1:], ["-I", "-m", "alethea.mcp_server"])
        p = subprocess.run(argv, input=mcp_messages(), cwd=self.cwd,
                           env=env_for(self.home, ALETHEA_PORT="1"), capture_output=True,
                           timeout=20)
        replies = {json.loads(ln).get("id"): json.loads(ln) for ln in p.stdout.decode().splitlines()}
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "alethea", p.stderr)
        self.assertFalse(pwned(self.cwd))

    def test_hook_autostart_starts_the_real_daemon_not_the_fake(self):
        payload = {"hook_event_name": "SessionStart", "source": "startup",
                   "session_id": "s-auto", "cwd": self.cwd}
        p = subprocess.run([self.py, "-I", "-m", "alethea.hooks.alethea_hook"],
                           input=json.dumps(payload).encode(), cwd=self.cwd,
                           env=env_for(self.home), capture_output=True, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(wait_up(self.home, timeout=15), "daemon did not come up")
        self.assertFalse(pwned(self.cwd))
        self.assert_real_daemon(self.home)

    def test_client_autostart_from_poisoned_cwd(self):
        old = os.getcwd()
        os.chdir(self.cwd)
        try:
            self.assertTrue(client.autostart(env={"ALETHEA_HOME": self.home}, wait=10.0))
        finally:
            os.chdir(old)
        self.assertFalse(pwned(self.cwd))
        self.assert_real_daemon(self.home)

    def test_mnemo_up_from_poisoned_cwd(self):
        p = subprocess.run([sys.executable, MNEMO, "up"], cwd=self.cwd, env=env_for(self.home),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(pwned(self.cwd))
        self.assert_real_daemon(self.home)

    def assert_real_daemon(self, home):
        pid = daemon_pid(home)
        with open("/proc/%d/cmdline" % pid, "rb") if os.path.exists("/proc/%d" % pid) else \
                open(os.devnull, "rb") as f:
            args = f.read().decode(errors="replace")
        out = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True,
                             text=True).stdout
        self.assertIn("alethea.daemon", out or args)  # test helpers / pgrep rely on this text
        self.assertIn("-I", (out or args).split())
        if os.path.exists("/proc/%d/cwd" % pid):
            self.assertEqual(os.path.realpath("/proc/%d/cwd" % pid), os.path.realpath(home))
        self.assertTrue(client.ping(timeout=1.0, env={"ALETHEA_HOME": home}))

    def test_daemon_command_names_the_package_the_client_imported(self):
        cmd = client.daemon_command()
        self.assertEqual(cmd[:3], [sys.executable, "-I", "-c"])
        self.assertEqual(cmd[-1], ROOT)
        self.assertIn("alethea.daemon", cmd[3])
        self.assertNotIn("\n", cmd[3])  # one line: stays greppable in `ps`


if __name__ == "__main__":
    unittest.main()
