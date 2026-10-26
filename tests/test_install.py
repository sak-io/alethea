"""`mnemo install claude-code` (alethea/install.py). Always a temp HOME / CLAUDE_CONFIG_DIR: the
real ~/.claude is never read or written. The CLI subprocess tests use a tracked temp ALETHEA_HOME
and ALETHEA_PORT=1 so no daemon is started (and none leaks)."""
import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from alethea import install
from tests.daemon_helpers import ROOT, make_home

MNEMO = os.path.join(ROOT, "scripts", "mnemo")
TEMPLATE = os.path.join(ROOT, "integrations", "claude-code", "settings.hooks.json")
EVENTS = install.EVENTS
OWNED = ["-I", "-m", "alethea.hooks.alethea_hook"]


def ns(cmd="install", scope="user", dry_run=False, status=False, mcp=False):
    return argparse.Namespace(cmd=cmd, host="claude-code", scope=scope, dry_run=dry_run,
                              status=status, mcp=mcp)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ei")
        self.cfg = os.path.join(self.tmp, "cfgdir")  # CLAUDE_CONFIG_DIR
        self.path = os.path.join(self.cfg, "settings.json")
        p = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.cfg,
                                         "HOME": os.path.join(self.tmp, "nohome")})
        p.start()
        self.addCleanup(p.stop)
        self.out, self.err = [], []

    def run_cmd(self, **kw):
        return install.run(ns(**kw), out=self.out.append, err=self.err.append)

    def write(self, obj_or_text, path=None):
        path = path or self.path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))

    def read(self, path=None):
        with open(path or self.path) as f:
            return json.load(f)

    def text(self, path=None):
        with open(path or self.path) as f:
            return f.read()


FOREIGN = {
    "permissions": {"allow": ["Bash(ls:*)"], "deny": ["Read(.env)"]},
    "model": "opus",
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "notify", "args": ["done"]}]}],
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command",
                                                           "command": "other"}]}],
    },
    "env": {"A": "1"},
}


class BlockTest(unittest.TestCase):
    def test_shape_matches_hooks_reference(self):
        b = install.hooks_block("/x/python")["hooks"]
        self.assertEqual(list(b), list(EVENTS))
        for e, groups in b.items():
            self.assertEqual(len(groups), 1)
            h = groups[0]["hooks"][0]
            self.assertEqual(h, {"type": "command", "command": "/x/python", "args": OWNED,
                                 "timeout": 3 if e == "SessionEnd" else 5})
        self.assertEqual(b["SessionStart"][0]["matcher"], "*")
        for e in EVENTS[1:]:
            self.assertNotIn("matcher", b[e][0])

    def test_template_equals_generator(self):
        with open(TEMPLATE, encoding="utf-8") as f:
            self.assertEqual(f.read(), install.template_text())

    def test_default_interpreter_is_sys_executable(self):
        h = install.hooks_block()["hooks"]["Stop"][0]["hooks"][0]
        self.assertEqual(h["command"], sys.executable)


class MergeTest(unittest.TestCase):
    def test_preserves_everything_and_order(self):
        out = install.merge(FOREIGN, "/v/python")
        self.assertEqual(list(out), list(FOREIGN))
        self.assertEqual(out["permissions"], FOREIGN["permissions"])
        self.assertEqual(out["env"], FOREIGN["env"])
        self.assertEqual(out["hooks"]["PreToolUse"], FOREIGN["hooks"]["PreToolUse"])
        self.assertEqual(list(out["hooks"])[:3], ["PreToolUse", "Stop", "SessionStart"])
        # foreign Stop and SessionStart groups stay first; ours is appended
        self.assertEqual(out["hooks"]["Stop"][0], FOREIGN["hooks"]["Stop"][0])
        self.assertEqual(len(out["hooks"]["Stop"]), 2)
        self.assertEqual(out["hooks"]["SessionStart"][0], FOREIGN["hooks"]["SessionStart"][0])
        self.assertEqual(install.installed_events(out), list(EVENTS))

    def test_input_not_mutated(self):
        import copy
        before = copy.deepcopy(FOREIGN)
        install.merge(FOREIGN, "/v/python")
        install.remove(install.merge(FOREIGN, "/v/python"))
        self.assertEqual(FOREIGN, before)

    def test_reinstall_updates_in_place_and_is_idempotent(self):
        a = install.merge(FOREIGN, "/old/python")
        b = install.merge(a, "/new/python")
        self.assertEqual(b, install.merge(FOREIGN, "/new/python"))
        self.assertEqual(install.merge(b, "/new/python"), b)

    def test_in_place_keeps_position_among_foreign_groups(self):
        s = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "a"}]},
                               install.hook_group("Stop", "/old/python"),
                               {"hooks": [{"type": "command", "command": "b"}]}]}}
        out = install.merge(s, "/new/python")["hooks"]["Stop"]
        self.assertEqual([g["hooks"][0]["command"] for g in out], ["a", "/new/python", "b"])

    def test_owned_handler_sharing_a_group_is_replaced_not_the_group(self):
        s = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "a"},
                                           install.hook_handler("Stop", "/old/python")]}]}}
        out = install.merge(s, "/new/python")["hooks"]["Stop"]
        self.assertEqual(len(out), 1)
        self.assertEqual([h["command"] for h in out[0]["hooks"]], ["a", "/new/python"])

    def test_duplicate_owned_entries_collapse_to_one(self):
        s = {"hooks": {"Stop": [install.hook_group("Stop", "/a/python"), install.hook_group("Stop", "/b/python")]}}
        out = install.merge(s, "/c/python")["hooks"]["Stop"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["hooks"][0]["command"], "/c/python")

    def test_uninstall_removes_only_owned(self):
        out = install.remove(install.merge(FOREIGN, "/v/python"))
        self.assertEqual(out, FOREIGN)

    def test_uninstall_drops_empty_hooks_key_and_events(self):
        s = install.merge({"permissions": {"allow": []}}, "/v/python")
        self.assertEqual(install.remove(s), {"permissions": {"allow": []}})

    def test_uninstall_without_ours_changes_nothing_even_empty_containers(self):
        s = {"hooks": {}, "x": 1}
        self.assertEqual(install.remove(s), s)
        s = {"hooks": {"Stop": []}}
        self.assertEqual(install.remove(s), s)

    def test_legacy_signatures_are_ours_and_updated_in_place_never_duplicated(self):
        old_module = {"type": "command", "command": "/old/venv/bin/python",
                      "args": ["-m", "alethea.hooks.alethea_hook"], "timeout": 5}
        old_script = {"type": "command", "command": "python3",
                      "args": ["/home/u/alethea/alethea/hooks/alethea_hook.py"], "timeout": 5}
        old_var = {"type": "command", "command": "python3",
                   "args": ["${CLAUDE_PROJECT_DIR}/alethea/hooks/alethea_hook.py"], "timeout": 5}
        for legacy in (old_module, old_script, old_var):
            self.assertTrue(install.is_owned(legacy), legacy)
            s = {"hooks": {"Stop": [{"hooks": [dict(legacy)]}]}}
            out = install.merge(s, "/new/python")
            self.assertEqual(len(out["hooks"]["Stop"]), 1)
            self.assertEqual(out["hooks"]["Stop"][0]["hooks"][0]["args"], OWNED)
            self.assertEqual(out["hooks"]["Stop"][0]["hooks"][0]["command"], "/new/python")
            self.assertNotIn("Stop", install.remove(s).get("hooks", {}))

    def test_foreign_hooks_that_merely_mention_alethea_are_not_owned(self):
        mod = "alethea.hooks.alethea_hook"
        foreign = [
            {"type": "command", "command": "python3", "args": ["alethea"]},
            {"type": "command", "command": "alethea-notify --module alethea.hooks.alethea_hook"},
            {"type": "command", "command": "python3", "args": ["/other/hooks/alethea_hook.py"]},
            {"type": "command", "command": "python3", "args": ["/x/alethea_hook.py"]},
            {"type": "command", "command": "python3", "args": ["/x/alethea/hooks/alethea_hook.sh"]},
            {"type": "command", "command": "echo", "args": ["-I", "-m", mod]},
            {"type": "command", "command": "/usr/bin/node", "args": ["-m", mod]},
            {"type": "command", "command": "python3", "args": ["-I", "-m", mod, "--extra"]},
            {"type": "command", "command": "python3", "args": ["-m", "alethea.hooks.alethea_hook2"]},
            {"type": "command", "command": "python3 -m " + mod},
            {"type": "command", "command": "python3", "args": "-I -m " + mod},
            {"type": "command", "command": "python3", "env": {"x": mod}},
            {"type": "prompt", "command": "python3", "args": ["-I", "-m", mod]},
            "alethea.hooks.alethea_hook", None, 5,
        ]
        for h in foreign:
            self.assertFalse(install.is_owned(h), h)
        s = {"hooks": {e: [{"hooks": [dict(h) if isinstance(h, dict) else h]}
                           for h in foreign] for e in EVENTS}}
        out = install.remove(install.merge(s, "/v/python"))
        self.assertEqual(out, s)

    def test_shape_errors(self):
        for bad in ([], {"hooks": []}, {"hooks": "x"}, {"hooks": None},
                    {"hooks": {"Stop": {}}}):
            with self.assertRaises(install.InstallError):
                install.merge(bad)
            with self.assertRaises(install.InstallError):
                install.remove(bad)


class FileTest(Base):
    def test_creates_dir_and_file_private(self):
        self.assertEqual(self.run_cmd(), 0, self.err)
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(install.installed_events(self.read()), list(EVENTS))
        self.assertEqual(self.read(), install.hooks_block())
        self.assertFalse(os.path.exists(self.path + ".alethea-backup"))
        joined = "\n".join(self.out)
        for needle in (self.path, "mnemo status", "mnemo uninstall claude-code",
                       "ALETHEA_PORT=1", "Privacy", "masked", "~/.alethea"):
            self.assertIn(needle, joined)

    def test_second_run_is_unchanged_and_writes_nothing(self):
        self.run_cmd()
        before, st = self.text(), os.stat(self.path)
        self.out.clear()
        self.assertEqual(self.run_cmd(), 0)
        after = os.stat(self.path)
        self.assertEqual(self.text(), before)
        self.assertEqual((st.st_ino, st.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertIn("unchanged", self.out[0])

    def test_merge_into_existing_keeps_mode_and_makes_one_time_backup(self):
        self.write(FOREIGN)
        os.chmod(self.path, 0o640)
        original = self.text()
        self.assertEqual(self.run_cmd(), 0, self.err)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        backup = self.path + ".alethea-backup"
        self.assertEqual(self.text(backup), original)
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o640)
        self.assertEqual(self.read()["permissions"], FOREIGN["permissions"])
        # later modifications never overwrite the first backup
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertEqual(self.text(backup), original)
        self.assertEqual(self.read(), FOREIGN)

    def test_reinstall_with_other_interpreter_updates_in_place(self):
        self.run_cmd()
        with mock.patch.object(sys, "executable", "/elsewhere/python"):
            self.run_cmd()
        cmds = {h["command"] for _, _, h in
                ((e, g, h) for e in EVENTS for g in self.read()["hooks"][e] for h in g["hooks"])}
        self.assertEqual(cmds, {"/elsewhere/python"})

    def test_uninstall_deletes_file_and_dir_the_installer_created(self):
        self.run_cmd()
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.cfg))

    def test_uninstall_keeps_dir_with_other_entries(self):
        self.run_cmd()
        other = os.path.join(self.cfg, "keep.txt")
        with open(other, "w") as f:
            f.write("x")
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertTrue(os.path.isfile(other))

    def test_uninstall_keeps_file_when_user_content_remains(self):
        self.write(FOREIGN)
        self.run_cmd()
        self.run_cmd(cmd="uninstall")
        self.assertEqual(self.read(), FOREIGN)

    def test_uninstall_keeps_empty_object_file_that_has_a_backup(self):
        self.write({"hooks": {}})
        self.run_cmd()
        self.run_cmd(cmd="uninstall")
        self.assertTrue(os.path.exists(self.path))
        self.assertNotIn("hooks", self.read())

    def test_dry_run_uninstall_of_only_ours_shows_deletion(self):
        self.run_cmd()
        self.out.clear()
        self.assertEqual(self.run_cmd(cmd="uninstall", dry_run=True), 0)
        self.assertTrue(os.path.exists(self.path))
        self.assertIn("+++ /dev/null", "\n".join(self.out))

    def test_uninstall_when_nothing_to_do(self):
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertFalse(os.path.exists(self.cfg))  # does not even create the directory
        self.write({"a": 1})
        self.assertEqual(self.run_cmd(cmd="uninstall"), 0)
        self.assertEqual(self.read(), {"a": 1})
        self.assertIn("unchanged", "\n".join(self.out))

    def test_dry_run_prints_diff_and_writes_nothing(self):
        self.write(FOREIGN)
        before = self.text()
        self.assertEqual(self.run_cmd(dry_run=True), 0)
        self.assertEqual(self.text(), before)
        self.assertFalse(os.path.exists(self.path + ".alethea-backup"))
        diff = "\n".join(self.out)
        self.assertIn("--- ", diff)
        self.assertIn("+++ ", diff)
        self.assertIn("+            \"command\"", diff)
        self.assertIn("alethea.hooks.alethea_hook", diff)

    def test_dry_run_creates_nothing_when_absent(self):
        self.assertEqual(self.run_cmd(dry_run=True), 0)
        self.assertFalse(os.path.exists(self.cfg))
        self.assertIn("/dev/null", "\n".join(self.out))

    def test_dry_run_uninstall(self):
        self.run_cmd()
        before = self.text()
        self.assertEqual(self.run_cmd(cmd="uninstall", dry_run=True), 0)
        self.assertEqual(self.text(), before)
        self.assertIn("-I", before)

    def test_refuses_invalid_json(self):
        for bad in ("{not json", "", "[1]", "   ", "\xff\xfe"):
            self.write(bad)
            with open(self.path, "wb") as f:
                f.write(bad.encode("latin-1"))
            self.err.clear()
            self.assertEqual(self.run_cmd(), 2, bad)
            with open(self.path, "rb") as f:
                self.assertEqual(f.read(), bad.encode("latin-1"))
            self.assertTrue(self.err)
            self.assertFalse(os.path.exists(self.path + ".alethea-backup"))
            self.assertEqual(self.run_cmd(cmd="uninstall"), 2)

    def test_refuses_non_object_hooks(self):
        for bad in ('{"hooks": []}', '{"hooks": "x"}', '{"hooks": {"Stop": {}}}'):
            self.write(bad)
            self.assertEqual(self.run_cmd(), 2, bad)
            self.assertEqual(self.text(), bad)
            self.assertIn("hooks", self.err[-1])

    def test_hooks_null_and_other_non_objects_are_user_errors(self):
        for bad in ('{"hooks": null}', '{"hooks": 0}', '{"hooks": false}', '{"hooks": []}'):
            for cmd in ("install", "uninstall"):
                self.write(bad)
                self.err.clear()
                self.assertEqual(self.run_cmd(cmd=cmd), 2, (bad, cmd))
                self.assertEqual(self.text(), bad)
                self.assertIn("hooks", self.err[-1])
                self.assertNotIn("unexpected", self.err[-1])

    def test_pathological_json_is_a_user_error_not_a_crash(self):
        deep = "[" * 100000 + "]" * 100000
        for bad in (deep, '{"a": ' * 5000 + "1" + "}" * 5000,
                    '{"x": "' + "a" * (5 * 1024 * 1024) + '"}'):
            self.write(bad)
            self.err.clear()
            self.assertEqual(self.run_cmd(), 2)
            self.assertEqual(self.run_cmd(cmd="uninstall"), 2)
            self.assertEqual(self.text(), bad)
            self.assertTrue(self.err)
            self.assertNotIn("Traceback", "".join(self.err))

    def test_deep_but_parseable_json_is_handled_without_exit_1(self):
        # parses (depth < recursion limit) but may overflow in merge/dump: any outcome but 1
        self.write('{"k": ' + "[" * 800 + "]" * 800 + "}")
        self.assertIn(self.run_cmd(), (0, 2))

    def test_unexpected_internal_error_is_exit_2_not_1(self):
        with mock.patch("alethea.install.apply", side_effect=KeyError("boom")):
            self.assertEqual(self.run_cmd(), 2)
        self.assertIn("unexpected", self.err[-1])

    def test_refuses_symlinked_target_file(self):
        real = os.path.join(self.tmp, "real.json")
        self.write("{}", real)
        os.makedirs(self.cfg)
        os.symlink(real, self.path)
        self.assertEqual(self.run_cmd(), 2)
        self.assertEqual(self.text(real), "{}")
        self.assertTrue(os.path.islink(self.path))

    def test_refuses_symlinked_directory(self):
        real = os.path.join(self.tmp, "realdir")
        os.makedirs(real)
        os.symlink(real, self.cfg)
        self.assertEqual(self.run_cmd(), 2)
        self.assertEqual(os.listdir(real), [])
        self.assertIn("symlink", self.err[-1])
        self.assertIn("CLAUDE_CONFIG_DIR=%s" % os.path.realpath(real), self.err[-1])

    def test_no_temp_files_left_behind(self):
        self.write(FOREIGN)
        self.run_cmd()
        self.run_cmd(cmd="uninstall")
        self.assertEqual(sorted(os.listdir(self.cfg)),
                         ["settings.json", "settings.json.alethea-backup"])

    def test_failed_write_leaves_original_intact(self):
        self.write(FOREIGN)
        before = self.text()
        with mock.patch("os.rename", side_effect=OSError(28, "disk full")):
            self.assertEqual(self.run_cmd(), 2)
        self.assertEqual(self.text(), before)
        self.assertNotIn(".settings.json.tmp%d" % os.getpid(), os.listdir(self.cfg))

    def test_user_scope_defaults_to_home_dot_claude(self):
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        env = dict(os.environ, HOME=home)
        env.pop("CLAUDE_CONFIG_DIR")
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.run_cmd(), 0, self.err)
        self.assertTrue(os.path.isfile(os.path.join(home, ".claude", "settings.json")))

    def test_status_reports_scope_file(self):
        self.assertEqual(self.run_cmd(status=True, scope="user"), 0)
        self.assertIn("no settings file", self.out[-1])
        self.run_cmd()
        self.out.clear()
        self.run_cmd(status=True, scope="user")
        self.assertIn("installed (%s)" % ", ".join(EVENTS), self.out[0])
        self.assertIn(sys.executable, "\n".join(self.out))

    def test_mcp_prints_command_when_claude_missing(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(self.run_cmd(mcp=True), 0)
        joined = "\n".join(self.out)
        self.assertIn("claude mcp add --scope user alethea -- %s -I -m alethea.mcp_server"
                      % sys.executable, joined)

    def test_mcp_runs_claude_list_form_when_present(self):
        fake = os.path.join(self.tmp, "bin")
        os.makedirs(fake)
        log = os.path.join(self.tmp, "argv.txt")
        exe = os.path.join(fake, "claude")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\nprintf '%%s\\n' \"$@\" > %s\necho added\n" % log)
        os.chmod(exe, 0o755)
        with mock.patch.dict(os.environ, {"PATH": fake + os.pathsep + os.environ["PATH"]}):
            self.assertEqual(self.run_cmd(mcp=True), 0, self.err)
        with open(log) as f:
            self.assertEqual(f.read().split("\n")[:-1],
                             ["mcp", "add", "--scope", "user", "alethea", "--", sys.executable,
                              "-I", "-m", "alethea.mcp_server"])
        self.assertIn("added", "\n".join(self.out))

    def test_mcp_failure_is_exit_1_but_hooks_stay_written(self):
        fake = os.path.join(self.tmp, "bin")
        os.makedirs(fake)
        exe = os.path.join(fake, "claude")
        with open(exe, "w") as f:
            f.write("#!/bin/sh\necho boom >&2\nexit 3\n")
        os.chmod(exe, 0o755)
        with mock.patch.dict(os.environ, {"PATH": fake + os.pathsep + os.environ["PATH"]}):
            self.assertEqual(self.run_cmd(mcp=True), 1)
        self.assertEqual(install.installed_events(self.read()), list(EVENTS))


class CliTest(unittest.TestCase):
    """The real CLI as a subprocess: temp HOME, no daemon (ALETHEA_PORT=1)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ec")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = {"PATH": os.environ.get("PATH", ""), "HOME": self.home,
                    "ALETHEA_HOME": make_home(), "ALETHEA_PORT": "1",
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def mnemo(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, MNEMO] + list(args), cwd=cwd or self.tmp,
                              env=env or self.env, capture_output=True, text=True, timeout=30)

    def repo(self):
        r = os.path.join(self.tmp, "repo")
        os.makedirs(r)
        subprocess.run(["git", "init", "-q", r], check=True, capture_output=True)
        return r

    def test_user_install_uninstall_round_trip(self):
        p = self.mnemo("install", "claude-code")
        self.assertEqual(p.returncode, 0, p.stderr)
        path = os.path.join(self.home, ".claude", "settings.json")
        with open(path) as f:
            self.assertEqual(install.installed_events(json.load(f)), list(EVENTS))
        p = self.mnemo("install", "claude-code", "--status")
        self.assertIn("installed", p.stdout)
        self.assertEqual(self.mnemo("uninstall", "claude-code").returncode, 0)
        self.assertFalse(os.path.exists(path))

    def test_local_scope_writes_settings_local_json_at_git_root(self):
        r = self.repo()
        sub = os.path.join(r, "a", "b")
        os.makedirs(sub)
        p = self.mnemo("install", "claude-code", "--scope", "local", cwd=sub)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(r, ".claude", "settings.local.json")))
        self.assertFalse(os.path.exists(os.path.join(r, ".claude", "settings.json")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")))
        self.assertNotIn("WARNING", p.stderr)

    def test_project_scope_warns_about_every_contributor(self):
        r = self.repo()
        p = self.mnemo("install", "claude-code", "--scope", "project", cwd=r)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("EVERY contributor", p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(r, ".claude", "settings.json")))
        self.assertEqual(self.mnemo("uninstall", "claude-code", "--scope", "project",
                                    cwd=r).returncode, 0)

    def test_local_and_project_outside_git_are_user_errors(self):
        for scope in ("local", "project"):
            p = self.mnemo("install", "claude-code", "--scope", scope)
            self.assertEqual(p.returncode, 2, scope)
            self.assertIn("git repository", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_invalid_json_exit_2_no_traceback(self):
        d = os.path.join(self.home, ".claude")
        os.makedirs(d)
        with open(os.path.join(d, "settings.json"), "w") as f:
            f.write("{oops")
        p = self.mnemo("install", "claude-code")
        self.assertEqual(p.returncode, 2)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("not valid JSON", p.stderr)

    def test_install_does_not_need_or_start_a_daemon(self):
        self.assertEqual(self.mnemo("install", "claude-code").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.env["ALETHEA_HOME"], "daemon.json")))

    def test_unknown_host_is_usage_error(self):
        self.assertEqual(self.mnemo("install", "vim").returncode, 2)


if __name__ == "__main__":
    unittest.main()
