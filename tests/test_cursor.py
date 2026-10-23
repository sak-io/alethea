"""`mnemo install|uninstall cursor` and `mnemo install --print mcp` (engram/install_hosts.py).
Always a temp HOME / temp git repo: the real ~/.cursor is never read or written. UNVERIFIED against
a real Cursor (none available): these tests prove what Engram writes and that it is safe, not that
Cursor accepts it. CLI subprocess tests use ENGRAM_PORT=1 so no daemon starts (none leaks)."""
import argparse
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from engram import install, install_hosts as hosts
from tests.daemon_helpers import ROOT, make_home

MNEMO = os.path.join(ROOT, "scripts", "mnemo")
HOOK = os.path.join(ROOT, "engram", "hooks", "engram_hook.py")
INTEG = os.path.join(ROOT, "integrations", "cursor")
OWNED = ["-I", "-m", "engram.mcp_server"]

FOREIGN = {
    "mcpServers": {
        "github": {"command": "npx", "args": ["-y", "gh-mcp"], "env": {"T": "x"}},
        "docs": {"url": "https://example.invalid/mcp"},
    },
    "other": {"keep": [1, 2]},
}


def ns(cmd="install", scope="user", dry_run=False, status=False, mcp=False, host="cursor"):
    return argparse.Namespace(cmd=cmd, host=host, scope=scope, dry_run=dry_run, status=status,
                              mcp=mcp, format="json", **{"print": None})


class PureTest(unittest.TestCase):
    def test_entry_shape(self):
        self.assertEqual(hosts.mcp_entry("/x/python"), {"command": "/x/python", "args": OWNED})
        self.assertEqual(hosts.mcp_entry()["command"], sys.executable)

    def test_integration_files_equal_generator(self):
        with open(os.path.join(INTEG, "mcp.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), hosts.mcp_snippet("json", "/ABSOLUTE/PATH/TO/python"))
        with open(os.path.join(INTEG, "mcp.codex.toml"), encoding="utf-8") as f:
            self.assertEqual(f.read(), hosts.mcp_snippet("toml", "/ABSOLUTE/PATH/TO/python"))
        with open(os.path.join(INTEG, "engram.mdc"), encoding="utf-8") as f:
            self.assertEqual(f.read(), hosts.RULE_TEXT)

    def test_rule_frontmatter_and_content(self):
        t = hosts.RULE_TEXT
        self.assertTrue(t.startswith("---\ndescription: "))
        self.assertIn("\nalwaysApply: true\n---\n", t)
        self.assertIn(hosts.RULE_MARKER, t)
        self.assertIn("`recall`", t)
        self.assertIn("`remember`", t)

    def test_hooks_example_is_labelled_unverified_and_valid_json(self):
        with open(os.path.join(INTEG, "hooks.example.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertIn("UNVERIFIED", d["_UNVERIFIED"])
        self.assertIn("NOT TESTED", d["_UNVERIFIED"])

    def test_toml_snippet_parses_and_escapes(self):
        s = hosts.mcp_snippet("toml", '/we"ird/py thon')
        self.assertIn('command = "/we\\"ird/py thon"', s)
        try:
            import tomllib
        except ImportError:  # 3.9
            return
        d = tomllib.loads(s)
        self.assertEqual(d["mcp_servers"]["engram"], {"command": '/we"ird/py thon', "args": OWNED})

    def test_merge_preserves_everything_and_is_pure(self):
        before = copy.deepcopy(FOREIGN)
        out = hosts.merge_mcp(FOREIGN, "/v/python")
        self.assertEqual(FOREIGN, before)
        self.assertEqual(list(out["mcpServers"]), ["github", "docs", "engram"])
        self.assertEqual(out["other"], FOREIGN["other"])
        self.assertEqual({k: v for k, v in out["mcpServers"].items() if k != "engram"},
                         FOREIGN["mcpServers"])
        self.assertEqual(hosts.merge_mcp(out, "/v/python"), out)
        self.assertEqual(hosts.remove_mcp(out), FOREIGN)

    def test_reinstall_updates_interpreter_in_place_keeping_user_keys(self):
        cfg = {"mcpServers": {"a": {}, "engram": {"command": "/old/python", "args": OWNED,
                                                  "env": {"K": "v"}}, "z": {}}}
        out = hosts.merge_mcp(cfg, "/new/python")
        self.assertEqual(list(out["mcpServers"]), ["a", "engram", "z"])
        self.assertEqual(out["mcpServers"]["engram"],
                         {"command": "/new/python", "args": OWNED, "env": {"K": "v"}})

    def test_legacy_forms_are_ours(self):
        for args in (["-m", "engram.mcp_server"], ["/home/u/engram/engram/mcp_server.py"]):
            e = {"command": "python3", "args": args}
            self.assertTrue(hosts.is_owned_mcp(e), args)
            out = hosts.merge_mcp({"mcpServers": {"engram": e}}, "/n/python")
            self.assertEqual(out["mcpServers"]["engram"]["args"], OWNED)
            self.assertEqual(hosts.remove_mcp({"mcpServers": {"engram": e}}), {})

    def test_foreign_engram_entries_are_not_ours_and_conflict(self):
        foreign = [
            {"command": "node", "args": OWNED},
            {"command": "python3", "args": ["-I", "-m", "engram.mcp_server", "--x"]},
            {"command": "python3", "args": ["-m", "engram.mcp_server2"]},
            {"command": "python3", "args": ["/x/other/mcp_server.py"]},
            {"command": "python3 -I -m engram.mcp_server"},
            {"url": "https://example.invalid/mcp"},
            "engram", None, 5, [],
        ]
        for e in foreign:
            self.assertFalse(hosts.is_owned_mcp(e), e)
            cfg = {"mcpServers": {"engram": e}}
            with self.assertRaises(install.InstallError):
                hosts.merge_mcp(cfg)
            self.assertEqual(hosts.remove_mcp(cfg), cfg)

    def test_shape_errors(self):
        for bad in ([], "x", {"mcpServers": []}, {"mcpServers": None}, {"mcpServers": "x"}):
            for fn in (hosts.merge_mcp, hosts.remove_mcp):
                with self.assertRaises(install.InstallError):
                    fn(bad)

    def test_remove_without_ours_changes_nothing_even_empty_containers(self):
        for s in ({"mcpServers": {}}, {"x": 1}):
            self.assertEqual(hosts.remove_mcp(s), s)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ecu")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init", "-q", self.repo], check=True, capture_output=True)
        self.out, self.err = [], []
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)
        self.user_json = os.path.join(self.home, ".cursor", "mcp.json")
        self.proj_json = os.path.join(self.repo, ".cursor", "mcp.json")
        self.rule = os.path.join(self.repo, ".cursor", "rules", "engram.mdc")

    def run_cmd(self, cwd=None, **kw):
        old = os.getcwd()
        os.chdir(cwd or self.repo)
        try:
            return install.run(ns(**kw), out=self.out.append, err=self.err.append)
        finally:
            os.chdir(old)

    def write(self, obj_or_text, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))

    def read(self, path):
        with open(path) as f:
            return json.load(f)

    def text(self, path):
        with open(path) as f:
            return f.read()


class UserScopeTest(Base):
    def test_creates_private_dir_and_file_no_rule(self):
        self.assertEqual(self.run_cmd(), 0, self.err)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.user_json)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.user_json).st_mode), 0o600)
        self.assertEqual(self.read(self.user_json),
                         {"mcpServers": {"engram": {"command": sys.executable, "args": OWNED}}})
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor", "rules")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".cursor")))
        joined = "\n".join(self.out)
        self.assertIn("UNVERIFIED", joined)
        self.assertIn("no rule file", joined)
        self.assertIn("mnemo uninstall cursor", joined)

    def test_second_run_writes_nothing(self):
        self.run_cmd()
        st = os.stat(self.user_json)
        before = self.text(self.user_json)
        self.out.clear()
        self.assertEqual(self.run_cmd(), 0)
        after = os.stat(self.user_json)
        self.assertEqual((st.st_ino, st.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(self.text(self.user_json), before)
        self.assertIn("unchanged", "\n".join(self.out))

    def test_merge_keeps_foreign_mode_and_one_time_backup(self):
        self.write(FOREIGN, self.user_json)
        os.chmod(self.user_json, 0o640)
        original = self.text(self.user_json)
        self.assertEqual(self.run_cmd(), 0, self.err)
        self.assertEqual(stat.S_IMODE(os.stat(self.user_json).st_mode), 0o640)
        backup = self.user_json + ".engram-backup"
        self.assertEqual(self.text(backup), original)
        got = self.read(self.user_json)
        self.assertEqual(got["mcpServers"]["github"], FOREIGN["mcpServers"]["github"])
        self.assertEqual(got["other"], FOREIGN["other"])
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertEqual(self.text(backup), original)  # never overwritten
        self.assertEqual(self.read(self.user_json), FOREIGN)

    def test_uninstall_deletes_what_it_created_incl_empty_dir(self):
        self.run_cmd()
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(os.path.dirname(self.user_json)))

    def test_uninstall_keeps_dir_with_other_entries(self):
        self.run_cmd()
        other = os.path.join(self.home, ".cursor", "keep.txt")
        with open(other, "w") as f:
            f.write("x")
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(self.user_json))
        self.assertTrue(os.path.isfile(other))

    def test_uninstall_with_nothing_to_do_creates_nothing(self):
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))
        self.write({"a": 1}, self.user_json)
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertEqual(self.read(self.user_json), {"a": 1})

    def test_uninstall_leaves_foreign_engram_server_alone(self):
        cfg = {"mcpServers": {"engram": {"command": "node", "args": ["x.js"]}}}
        self.write(cfg, self.user_json)
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertEqual(self.read(self.user_json), cfg)

    def test_install_refuses_foreign_engram_server(self):
        cfg = {"mcpServers": {"engram": {"command": "node", "args": ["x.js"]}}}
        self.write(cfg, self.user_json)
        before = self.text(self.user_json)
        self.assertEqual(self.run_cmd(), 2)
        self.assertEqual(self.text(self.user_json), before)
        self.assertFalse(os.path.exists(self.user_json + ".engram-backup"))
        self.assertIn("not overwriting", self.err[-1])

    def test_dry_run_prints_diff_and_writes_nothing(self):
        self.write(FOREIGN, self.user_json)
        before = self.text(self.user_json)
        self.assertEqual(self.run_cmd(dry_run=True), 0)
        self.assertEqual(self.text(self.user_json), before)
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.user_json))), ["mcp.json"])
        self.assertIn("engram.mcp_server", "\n".join(self.out))

    def test_dry_run_creates_nothing_when_absent_and_uninstall_dry_run(self):
        self.assertEqual(self.run_cmd(dry_run=True), 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))
        self.run_cmd()
        before = self.text(self.user_json)
        self.out.clear()
        self.assertEqual(self.run_cmd(cmd="uninstall", dry_run=True), 0)
        self.assertEqual(self.text(self.user_json), before)
        self.assertIn("+++ /dev/null", "\n".join(self.out))

    def test_refuses_invalid_json_files_untouched(self):
        for bad in (b"{not json", b"", b"[1]", b"   ", b"\xff\xfe", b'{"mcpServers": []}',
                    b'{"mcpServers": null}'):
            with open(self._mk(self.user_json), "wb") as f:
                f.write(bad)
            self.err.clear()
            for cmd in ("install", "uninstall"):
                self.assertEqual(self.run_cmd(cmd=cmd), 2, (bad, cmd))
                with open(self.user_json, "rb") as f:
                    self.assertEqual(f.read(), bad)
            self.assertTrue(self.err)
            self.assertFalse(os.path.exists(self.user_json + ".engram-backup"))

    def _mk(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def test_pathological_json_is_a_user_error(self):
        for bad in ("[" * 100000 + "]" * 100000, '{"x": "' + "a" * (5 * 1024 * 1024) + '"}'):
            self.write(bad, self.user_json)
            self.assertEqual(self.run_cmd(), 2)
            self.assertEqual(self.text(self.user_json), bad)
            self.assertNotIn("Traceback", "".join(self.err))

    def test_refuses_symlinked_file(self):
        real = os.path.join(self.tmp, "real.json")
        self.write("{}", real)
        os.makedirs(os.path.dirname(self.user_json))
        os.symlink(real, self.user_json)
        for cmd in ("install", "uninstall"):
            self.assertEqual(self.run_cmd(cmd=cmd), 2)
        self.assertEqual(self.text(real), "{}")
        self.assertTrue(os.path.islink(self.user_json))

    def test_refuses_symlinked_cursor_dir(self):
        real = os.path.join(self.tmp, "realdir")
        os.makedirs(real)
        os.symlink(real, os.path.join(self.home, ".cursor"))
        for cmd in ("install", "uninstall"):
            self.assertEqual(self.run_cmd(cmd=cmd), 2)
        self.assertEqual(os.listdir(real), [])
        self.assertIn("symlink", self.err[-1])

    def test_no_temp_files_and_failed_write_leaves_original(self):
        self.write(FOREIGN, self.user_json)
        before = self.text(self.user_json)
        with mock.patch("os.rename", side_effect=OSError(28, "disk full")):
            self.assertEqual(self.run_cmd(), 2)
        self.assertEqual(self.text(self.user_json), before)
        d = os.path.dirname(self.user_json)
        self.assertFalse([n for n in os.listdir(d) if n.startswith(".mcp.json.tmp")])
        self.assertEqual(self.run_cmd(), 0)
        self.assertEqual(sorted(os.listdir(d)), ["mcp.json", "mcp.json.engram-backup"])

    def test_unexpected_error_is_exit_2_not_1(self):
        with mock.patch("engram.install_hosts.apply", side_effect=KeyError("boom")):
            self.assertEqual(self.run_cmd(), 2)
        self.assertIn("unexpected", self.err[-1])

    def test_status(self):
        self.assertEqual(self.run_cmd(status=True, scope="user"), 0)
        self.assertIn("not installed", self.out[-1])
        self.run_cmd()
        self.out.clear()
        self.run_cmd(status=True, scope="user")
        self.assertIn("installed", self.out[0])
        self.assertIn(sys.executable, "\n".join(self.out))

    def test_local_scope_and_mcp_flag_rejected(self):
        self.assertEqual(self.run_cmd(scope="local"), 2)
        self.assertIn("no `local` scope", self.err[-1])
        self.assertEqual(self.run_cmd(mcp=True), 2)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))


class ProjectScopeTest(Base):
    def test_writes_mcp_and_owned_rule_at_git_root_from_subdir(self):
        sub = os.path.join(self.repo, "a", "b")
        os.makedirs(sub)
        self.assertEqual(self.run_cmd(cwd=sub, scope="project"), 0, self.err)
        self.assertEqual(self.read(self.proj_json)["mcpServers"]["engram"]["args"], OWNED)
        self.assertEqual(self.text(self.rule), hosts.RULE_TEXT)
        self.assertEqual(stat.S_IMODE(os.stat(self.rule).st_mode), 0o644)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))
        self.assertFalse(os.path.exists(os.path.join(sub, ".cursor")))
        self.assertIn("ABSOLUTE interpreter path", self.err[0])

    def test_idempotent_and_updates_edited_owned_rule(self):
        self.run_cmd(scope="project")
        st = os.stat(self.rule)
        self.assertEqual(self.run_cmd(scope="project"), 0)
        self.assertEqual(os.stat(self.rule).st_ino, st.st_ino)
        with open(self.rule, "a") as f:
            f.write("tampered\n")
        self.assertEqual(self.run_cmd(scope="project"), 0)
        self.assertEqual(self.text(self.rule), hosts.RULE_TEXT)

    def test_hand_written_rule_is_never_overwritten_or_removed_and_nothing_else_written(self):
        self.write("my own rule\n", self.rule)
        self.assertEqual(self.run_cmd(scope="project"), 2)
        self.assertEqual(self.text(self.rule), "my own rule\n")
        self.assertFalse(os.path.exists(self.proj_json))  # conflict detected before any write
        self.assertIn("not overwriting", self.err[-1])
        self.write({"mcpServers": {"engram": hosts.mcp_entry()}}, self.proj_json)
        self.assertEqual(self.run_cmd(cmd="uninstall", scope="project"), 0)
        self.assertEqual(self.text(self.rule), "my own rule\n")
        self.assertFalse(os.path.exists(self.proj_json))

    def test_other_rules_and_export_file_untouched(self):
        learned = os.path.join(self.repo, ".cursor", "rules", "engram_learned.mdc")
        mine = os.path.join(self.repo, ".cursor", "rules", "style.mdc")
        self.write("learned\n", learned)
        self.write("style\n", mine)
        self.run_cmd(scope="project")
        self.run_cmd(cmd="uninstall", scope="project")
        self.assertEqual(self.text(learned), "learned\n")
        self.assertEqual(self.text(mine), "style\n")
        self.assertFalse(os.path.exists(self.rule))

    def test_uninstall_removes_only_created_dirs(self):
        self.run_cmd(scope="project")
        self.assertEqual(self.run_cmd(cmd="uninstall", scope="project"), 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".cursor")))

    def test_uninstall_keeps_user_content_in_cursor_dir(self):
        self.write(FOREIGN, self.proj_json)
        self.write("x", os.path.join(self.repo, ".cursor", "rules", "style.mdc"))
        self.run_cmd(scope="project")
        self.run_cmd(cmd="uninstall", scope="project")
        self.assertEqual(self.read(self.proj_json), FOREIGN)
        self.assertEqual(os.listdir(os.path.join(self.repo, ".cursor", "rules")), ["style.mdc"])

    def test_dry_run_writes_nothing_and_shows_both_files(self):
        self.assertEqual(self.run_cmd(scope="project", dry_run=True), 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".cursor")))
        joined = "\n".join(self.out)
        self.assertIn(self.proj_json, joined)
        self.assertIn(self.rule, joined)

    def test_refuses_symlinked_rules_dir_and_file(self):
        real = os.path.join(self.tmp, "elsewhere")
        os.makedirs(real)
        os.makedirs(os.path.join(self.repo, ".cursor"))
        os.symlink(real, os.path.join(self.repo, ".cursor", "rules"))
        self.assertEqual(self.run_cmd(scope="project"), 2)
        self.assertEqual(os.listdir(real), [])
        os.unlink(os.path.join(self.repo, ".cursor", "rules"))
        os.makedirs(os.path.join(self.repo, ".cursor", "rules"))
        target = os.path.join(self.tmp, "victim")
        self.write("v", target)
        os.symlink(target, self.rule)
        self.assertEqual(self.run_cmd(scope="project"), 2)
        self.assertEqual(self.run_cmd(cmd="uninstall", scope="project"), 2)
        self.assertEqual(self.text(target), "v")

    def test_outside_git_is_user_error(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertEqual(self.run_cmd(cwd=plain, scope="project"), 2)
        self.assertIn("git repository", self.err[-1])

    def test_status_shows_rule(self):
        self.run_cmd(scope="project")
        self.out.clear()
        self.run_cmd(scope="project", status=True)
        self.assertIn("installed", self.out[0])
        self.assertIn(self.rule, "\n".join(self.out))


class CliTest(unittest.TestCase):
    """The real CLI as a subprocess: temp HOME, poisoned cwd, no daemon (ENGRAM_PORT=1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ecc")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home,
                    "ENGRAM_HOME": make_home(), "ENGRAM_PORT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"}
        # a project that ships a fake `engram` package and a fake `git`: neither may ever run
        self.poison = os.path.join(self.tmp, "poison")
        self.marker = os.path.join(self.tmp, "PWNED")
        os.makedirs(os.path.join(self.poison, "engram"))
        with open(os.path.join(self.poison, "engram", "__init__.py"), "w") as f:
            f.write("open(%r, 'w').write('x')\n" % self.marker)
        with open(os.path.join(self.poison, "engram", "mcp_server.py"), "w") as f:
            f.write("open(%r, 'w').write('x')\n" % self.marker)
        subprocess.run(["git", "init", "-q", self.poison], check=True, capture_output=True)

    def mnemo(self, *args, cwd=None):
        return subprocess.run([sys.executable, MNEMO] + list(args), cwd=cwd or self.poison,
                              env=self.env, capture_output=True, text=True, timeout=30)

    def test_user_round_trip_from_poisoned_cwd(self):
        p = self.mnemo("install", "cursor")
        self.assertEqual(p.returncode, 0, p.stderr)
        path = os.path.join(self.home, ".cursor", "mcp.json")
        with open(path) as f:
            e = json.load(f)["mcpServers"]["engram"]
        self.assertEqual(e, {"command": sys.executable, "args": OWNED})
        self.assertIn("installed", self.mnemo("install", "cursor", "--status").stdout)
        self.assertEqual(self.mnemo("uninstall", "cursor").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))
        self.assertFalse(os.path.exists(self.marker))
        self.assertFalse(os.path.exists(os.path.join(self.poison, ".cursor")))

    def test_project_round_trip(self):
        p = self.mnemo("install", "cursor", "--scope", "project")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(self.poison, ".cursor", "rules",
                                                    "engram.mdc")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))
        self.assertEqual(self.mnemo("uninstall", "cursor", "--scope", "project").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.poison, ".cursor")))
        self.assertFalse(os.path.exists(self.marker))

    def test_recorded_command_runs_the_mcp_server_isolated_from_cwd(self):
        # the recorded `python -I -m engram.mcp_server` must not import ./engram of the cwd
        env = dict(self.env, PYTHONPATH=ROOT)  # stands in for a pip install; -I ignores it too
        p = subprocess.run([sys.executable] + OWNED, cwd=self.poison, env=env, input="",
                           capture_output=True, text=True, timeout=30)
        self.assertFalse(os.path.exists(self.marker), p.stderr)

    def test_invalid_json_exit_2_no_traceback(self):
        d = os.path.join(self.home, ".cursor")
        os.makedirs(d)
        with open(os.path.join(d, "mcp.json"), "w") as f:
            f.write("{oops")
        p = self.mnemo("install", "cursor")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("not valid JSON", p.stderr)

    def test_install_does_not_need_or_start_a_daemon(self):
        self.assertEqual(self.mnemo("install", "cursor").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.env["ENGRAM_HOME"], "daemon.json")))

    def test_usage_errors(self):
        for argv in (["install"], ["uninstall"], ["install", "vim"], ["uninstall", "--print", "mcp"],
                     ["install", "--print", "nope"]):
            p = self.mnemo(*argv)
            self.assertEqual(p.returncode, 2, argv)
            self.assertNotIn("Traceback", p.stderr)
        p = self.mnemo("install", "cursor", "--print", "mcp")
        self.assertEqual(p.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cursor")))

    def test_print_mcp_json_and_toml_write_nothing(self):
        before = sorted(os.listdir(self.home))
        p = self.mnemo("install", "--print", "mcp")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout),
                         {"mcpServers": {"engram": {"command": sys.executable, "args": OWNED}}})
        self.assertIn("MCP only", p.stderr)
        self.assertIn("No hooks", p.stderr)
        p = self.mnemo("install", "--print", "mcp", "--format", "toml")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("[mcp_servers.engram]\n"))
        self.assertEqual(sorted(os.listdir(self.home)), before)
        self.assertEqual(os.listdir(self.poison).count(".cursor"), 0)
        self.assertFalse(os.path.exists(self.marker))

    def test_claude_code_install_still_works(self):
        self.assertEqual(self.mnemo("install", "claude-code").returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".claude", "settings.json")))


class HookIgnoresCursorEventsTest(unittest.TestCase):
    """hooks.example.json is a sketch: prove the real hook is inert and silent for Cursor-style
    payloads (Cursor's real payloads are UNVERIFIED; these are guessed shapes)."""

    def test_exit_0_no_output(self):
        env = {"PATH": os.environ.get("PATH", ""), "ENGRAM_HOME": make_home(),
               "ENGRAM_PORT": "1", "HOME": tempfile.mkdtemp(prefix="ech")}
        for payload in ({"hook_event_name": "beforeSubmitPrompt", "prompt": "hello",
                         "conversation_id": "c1", "workspace_roots": ["/x"]},
                        {"hook_event_name": "stop", "status": "completed"}):
            p = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)


if __name__ == "__main__":
    unittest.main()
