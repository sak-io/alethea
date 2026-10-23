"""Non-POSIX platforms fail cleanly (simulated by patching the module-level flags), and
reserved options warn once from the daemon."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from engram import cli, client, daemon, mcp_server


class UnsupportedPlatformTest(unittest.TestCase):
    def test_client_never_raises_and_reports_offline(self):
        with mock.patch.object(client, "SUPPORTED", False), mock.patch.object(client, "fcntl", None):
            self.assertEqual(client.call("ping"), {"ok": False, "offline": True})
            self.assertFalse(client.ping())
            with tempfile.TemporaryDirectory() as t:
                self.assertFalse(client.autostart(env={"ENGRAM_HOME": t}, wait=0.2))

    def test_cli_exits_zero_with_message_and_3_when_strict(self):
        with mock.patch.object(client, "SUPPORTED", False):
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(cli.main(["status"]), 0)
            self.assertIn("unsupported platform: POSIX only", err.getvalue())
            with redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["--strict", "status"]), cli.EXIT_OFFLINE)

    def test_mcp_main_logs_and_returns_zero(self):
        err = io.StringIO()
        with mock.patch.object(client, "SUPPORTED", False), \
                mock.patch.object(mcp_server, "serve", return_value=0), redirect_stderr(err):
            self.assertEqual(mcp_server.main(), 0)
        self.assertIn("unsupported platform: POSIX only", err.getvalue())

    def test_daemon_exits_2_with_clear_message(self):
        err = io.StringIO()
        with mock.patch.object(daemon, "SUPPORTED", False), redirect_stderr(err):
            self.assertEqual(daemon.main(), 2)
        self.assertIn("unsupported platform: POSIX only", err.getvalue())

    def test_posix_flags_true_here(self):
        self.assertTrue(client.SUPPORTED)
        self.assertTrue(daemon.SUPPORTED)


class ReservedOptionWarningTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(daemon, "_reserved_warned", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_warn(self, cfg):
        err = io.StringIO()
        with redirect_stderr(err):
            daemon.warn_reserved(cfg)
        return err.getvalue()

    def test_defaults_are_silent(self):
        self.assertEqual(self.run_warn({"ENGRAM_EMBEDDER": "hash", "ENGRAM_LLM_CONSOLIDATE": False}), "")

    def test_non_default_warns_once_on_one_line(self):
        cfg = {"ENGRAM_EMBEDDER": "openai", "ENGRAM_LLM_CONSOLIDATE": True}
        out = self.run_warn(cfg)
        self.assertEqual(out.count("\n"), 1)
        self.assertIn("ENGRAM_EMBEDDER=openai", out)
        self.assertNotIn("ENGRAM_LLM_CONSOLIDATE", out)  # implemented by T27: no longer reserved
        self.assertEqual(self.run_warn(cfg), "")

    def test_engram_constructor_warns(self):
        with tempfile.TemporaryDirectory() as t:
            err = io.StringIO()
            with redirect_stderr(err):
                e = daemon.Engram(home=t, env={"ENGRAM_HOME": t, "ENGRAM_EMBEDDER": "bert"})
            e.cortex.close()
        self.assertIn("ENGRAM_EMBEDDER=bert", err.getvalue())


if __name__ == "__main__":
    unittest.main()
