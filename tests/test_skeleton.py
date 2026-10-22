import json
import os
import socket
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import engram
from engram import config
from tests import netguard


class SkeletonTest(unittest.TestCase):
    def test_package_importable_with_version(self):
        self.assertTrue(engram.__version__)

    def test_no_third_party_modules_loaded(self):
        third = [m for m in sys.modules if m.split(".")[0] in ("numpy", "requests", "fastembed")]
        self.assertEqual(third, [])

    def test_netguard_blocks_non_loopback_connect(self):
        p = netguard.patch()
        try:
            s = socket.socket()
            with self.assertRaises(AssertionError):
                s.connect(("93.184.216.34", 80))
            s.close()
        finally:
            p.stop()

    def test_netguard_classifies_addresses(self):
        self.assertTrue(netguard._is_loopback(("127.0.0.1", 1)))
        self.assertTrue(netguard._is_loopback(("::1", 1, 0, 0)))
        self.assertFalse(netguard._is_loopback(("8.8.8.8", 1)))

    def test_private_dir_is_0700_even_if_preexisting(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "a" / "b"
            d.mkdir(parents=True, mode=0o755)
            config.ensure_private_dir(d)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_config_precedence_env_over_file_over_default(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(config.load_config({}, t)["ENGRAM_IDLE_SLEEP"], 180)
            Path(t, "config.json").write_text(json.dumps({"ENGRAM_IDLE_SLEEP": 5, "bogus": 1}))
            cfg = config.load_config({}, t)
            self.assertEqual(cfg["ENGRAM_IDLE_SLEEP"], 5)
            self.assertNotIn("bogus", cfg)
            cfg = config.load_config({"ENGRAM_IDLE_SLEEP": "7", "ENGRAM_LLM_CONSOLIDATE": "1"}, t)
            self.assertEqual(cfg["ENGRAM_IDLE_SLEEP"], 7)
            self.assertIs(cfg["ENGRAM_LLM_CONSOLIDATE"], True)

    def test_corrupt_config_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "config.json").write_text("{not json")
            self.assertEqual(config.load_config({}, t), config.DEFAULTS)

    def test_home_dir_honours_env(self):
        self.assertEqual(config.home_dir({"ENGRAM_HOME": "/x/y"}), Path("/x/y"))

    def test_sqlite_fts5_available(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("create virtual table t using fts5(a)")


if __name__ == "__main__":
    unittest.main()
