"""Pre-rename data dir (~/.engram): one stderr hint, never an automatic migration."""
import contextlib
import io
import os
import tempfile
import types
import unittest
from unittest import mock

from alethea import cli, config, daemon


class LegacyHintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {"HOME": self.tmp}, clear=False)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("ALETHEA_HOME", None)

    def test_hint_when_only_old_dir_exists(self):
        os.mkdir(os.path.join(self.tmp, ".engram"))
        h = config.legacy_home_hint()
        self.assertIn(".engram", h)
        self.assertIn(".alethea", h)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".alethea")))  # no auto-migration

    def test_no_hint_when_new_dir_exists_or_old_missing_or_home_overridden(self):
        self.assertIsNone(config.legacy_home_hint())
        os.mkdir(os.path.join(self.tmp, ".engram"))
        os.mkdir(os.path.join(self.tmp, ".alethea"))
        self.assertIsNone(config.legacy_home_hint())
        os.rmdir(os.path.join(self.tmp, ".alethea"))
        self.assertIsNone(config.legacy_home_hint({"ALETHEA_HOME": "/x"}))

    def test_status_prints_hint_to_stderr(self):
        os.mkdir(os.path.join(self.tmp, ".engram"))
        err = io.StringIO()
        with mock.patch.object(cli.client, "cli_call", return_value={"ok": True}), \
                mock.patch.object(cli, "_finish", return_value=0), \
                contextlib.redirect_stderr(err):
            cli.cmd_status(types.SimpleNamespace(json=False))
        self.assertIn("old data directory", err.getvalue())

    def test_daemon_prints_hint_then_exits_when_other_daemon_won(self):
        os.mkdir(os.path.join(self.tmp, ".engram"))
        stub = mock.Mock()
        stub.acquire_instance.return_value = False
        err = io.StringIO()
        with mock.patch.object(daemon, "Server", return_value=stub), \
                mock.patch.object(daemon, "SUPPORTED", True), \
                contextlib.redirect_stderr(err):
            self.assertEqual(daemon.main([]), 0)
        self.assertIn("old data directory", err.getvalue())


class PreRenameDocsTest(unittest.TestCase):
    def test_configuration_doc_has_the_pre_rename_note(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        text = open(os.path.join(root, "docs", "configuration.md"), encoding="utf-8").read()
        self.assertIn("pre-rename", text)
        self.assertIn("must be removed by hand", text)


if __name__ == "__main__":
    unittest.main()
