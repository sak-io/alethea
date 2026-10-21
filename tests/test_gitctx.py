import os
import subprocess
import tempfile
import unittest
from unittest import mock

from engram import gitctx


def git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


class GitCtxTest(unittest.TestCase):
    def setUp(self):
        gitctx.cache_clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)
        self.repo = os.path.join(self.base, "repo")
        os.mkdir(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
            "commit", "-q", "--allow-empty", "-m", "i")

    def test_root_and_subdir(self):
        sub = os.path.join(self.repo, "a", "b")
        os.makedirs(sub)
        self.assertEqual(gitctx.repo_root(self.repo), self.repo)
        self.assertEqual(gitctx.repo_root(sub), self.repo)

    def test_symlink(self):
        link = os.path.join(self.base, "link")
        os.symlink(self.repo, link)
        self.assertEqual(gitctx.repo_root(link), self.repo)

    def test_worktree_maps_to_main_repo(self):
        wt = os.path.join(self.base, "wt")
        git(self.repo, "worktree", "add", "-q", "-b", "other", wt)
        self.assertEqual(gitctx.repo_root(wt), self.repo)

    def test_non_repo_none(self):
        d = os.path.join(self.base, "plain")
        os.mkdir(d)
        self.assertIsNone(gitctx.repo_root(d))

    def test_missing_dir_none(self):
        self.assertIsNone(gitctx.repo_root(os.path.join(self.base, "nope")))

    def test_missing_git_none(self):
        with mock.patch.dict(os.environ, {"PATH": os.path.join(self.base, "empty")}):
            self.assertIsNone(gitctx.repo_root(self.repo))

    def test_timeout_none(self):
        with mock.patch("subprocess.run",
                        side_effect=subprocess.TimeoutExpired("git", 1)):
            self.assertIsNone(gitctx.repo_root(self.repo))

    def test_cache_per_cwd(self):
        self.assertEqual(gitctx.repo_root(self.repo), self.repo)
        with mock.patch("subprocess.run", side_effect=AssertionError):
            self.assertEqual(gitctx.repo_root(self.repo), self.repo)

    def test_no_shell_argv_list_and_timeout(self):
        with mock.patch("subprocess.run", wraps=subprocess.run) as r:
            gitctx.repo_root(self.repo)
        self.assertTrue(r.call_args_list)
        for c in r.call_args_list:
            self.assertIsInstance(c.args[0], list)
            self.assertFalse(c.kwargs["shell"])
            self.assertEqual(c.kwargs["timeout"], 1.0)


if __name__ == "__main__":
    unittest.main()
