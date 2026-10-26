"""Pins the dedup FTS window cap (ADR 0008): exact within the window, a documented second copy
beyond it, a debug trail (meta flag, status block) and an env override to test the hypothesis."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alethea import encode
from alethea.cortex import Cortex

OLD = "We decided to rotate the billing database credentials every ninety days using the vault tool"
OLD_RESTATED = "We decided to rotate the billing database credentials every ninety days using the vault tool."


def distinct(i):
    return ("Decision %d: service number %d must use partition scheme %d for the %s queue because "
            "of incident %d" % (i, i * 7, i * 13, "abcdefghij"[i % 10] * 3, 1000 + i))


class DedupWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = Cortex(Path(self.tmp.name) / "e.db")
        self.addCleanup(self.c.close)

    def fill(self, n):
        for i in range(n):
            r = encode.encode(self.c, distinct(i), force=True)
            self.assertEqual(r["status"], "created", r)

    def test_within_window_restatement_reinforces_and_nothing_is_flagged(self):
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "50"}):
            first = encode.encode(self.c, OLD, force=True)
            self.fill(10)
            again = encode.encode(self.c, OLD_RESTATED, force=True)
            self.assertEqual(again["status"], "reinforced")
            self.assertEqual(again["id"], first["id"])
            st = encode.dedup_cap_state(self.c)
            self.assertFalse(st["cap_active"])
            self.assertEqual(st["stored_while_capped"], 0)

    def test_beyond_window_old_duplicate_is_stored_twice_and_is_flagged(self):
        """The documented limitation (ADR 0008): an old near-duplicate outside BOTH the FTS window
        and the vector window is stored as a second copy. It must be traceable, and the env
        override must make it reinforce instead."""
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "5"}), \
                mock.patch("alethea.recall.VECTOR_WINDOW_MIN", 3):
            first = encode.encode(self.c, OLD, force=True)
            self.fill(120)  # > the vector channel's max(pool*4, VECTOR_WINDOW_MIN) = 80 rows
            st = encode.dedup_cap_state(self.c)
            self.assertTrue(st["cap_active"])
            self.assertEqual(st["fts_window"], 5)
            again = encode.encode(self.c, OLD_RESTATED, force=True)
            self.assertEqual(again["status"], "created", "expected the documented second copy")
            self.assertNotEqual(again["id"], first["id"])
            self.assertEqual(again["dedup_window_capped"], 5)
            self.assertEqual(self.c.get(again["id"])["meta"]["dedup_window_capped"], 5)
            self.assertGreaterEqual(encode.dedup_cap_state(self.c)["stored_while_capped"], 1)
            # the debugging step from the ADR: widen the window and the same text reinforces
            with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "1000000"}):
                third = encode.encode(self.c, OLD_RESTATED + " ", force=True)
            self.assertEqual(third["status"], "reinforced")
            self.assertIn(third["id"], (first["id"], again["id"]))

    def test_flag_only_on_memories_created_while_capped(self):
        """capped = rows already beyond the window when the memory is written."""
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "5"}):
            for i in range(6):  # the 6th is written with 5 rows present: not beyond the window
                r = encode.encode(self.c, distinct(i), force=True)
                self.assertNotIn("dedup_window_capped", r, i)
            self.assertEqual(encode.dedup_cap_state(self.c)["stored_while_capped"], 0)
            r = encode.encode(self.c, distinct(50), force=True)  # 6 rows present > 5
            self.assertEqual(r["dedup_window_capped"], 5)
            self.assertEqual(encode.dedup_cap_state(self.c)["stored_while_capped"], 1)

    def test_env_override_and_bad_value(self):
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "1000000"}):
            self.assertEqual(encode.dedup_fts_window(), 1000000)
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "nonsense"}):
            self.assertEqual(encode.dedup_fts_window(), encode.DEDUP_FTS_WINDOW)
        with mock.patch.dict(os.environ, {"ALETHEA_DEDUP_FTS_WINDOW": "-5"}):
            self.assertGreaterEqual(encode.dedup_fts_window(), 1)
        os.environ.pop("ALETHEA_DEDUP_FTS_WINDOW", None)
        self.assertEqual(encode.dedup_fts_window(), 2000)

    def test_status_block_shape(self):
        st = encode.dedup_cap_state(self.c)
        self.assertEqual(set(st), {"fts_window", "newest_rowid", "cap_active", "stored_while_capped", "adr"})
        self.assertTrue(st["adr"].endswith("0008-dedup-fts-window-cap.md"))
        self.assertTrue(Path(__file__).resolve().parents[1].joinpath(st["adr"]).exists())


if __name__ == "__main__":
    unittest.main()
