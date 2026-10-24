"""Order-swap and case-only identifier pairs end to end (ADR 0007)."""
import unittest

from alethea import encode as enc
from alethea import vectorize
from alethea.cortex import Cortex
from tests.data.dedup_pairs_extra import LONG_PAIRS_EXTRA, SHORT_PAIRS


def raw(a, b):
    return max(vectorize.cosine(vectorize.embed(a), vectorize.embed(b)), vectorize.jaccard(a, b))


class ExtraPairs(unittest.TestCase):
    def test_long_pairs_would_merge_without_vetoes(self):
        for cat, a, b in LONG_PAIRS_EXTRA:
            with self.subTest(cat=cat, a=a[-60:]):
                self.assertGreaterEqual(raw(a, b), enc.NEAR_DUPLICATE)

    def test_similarity_stays_below_threshold(self):
        for cat, a, b in SHORT_PAIRS + LONG_PAIRS_EXTRA:
            with self.subTest(cat=cat, a=a[-60:]):
                self.assertLess(enc.similarity(a, b), enc.NEAR_DUPLICATE)
                self.assertLess(enc.similarity(b, a), enc.NEAR_DUPLICATE)

    def test_encode_creates_two_memories(self):
        for cat, a, b in SHORT_PAIRS + LONG_PAIRS_EXTRA:
            with self.subTest(cat=cat, a=a[-60:]):
                cx = Cortex(":memory:")
                self.addCleanup(cx.close)
                ra, rb = enc.encode(cx, a, force=True), enc.encode(cx, b, force=True)
                self.assertEqual((ra["status"], rb["status"]), ("created", "created"))
                self.assertEqual(cx.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2)

    def test_identical_text_still_merges(self):
        a = "Alice reports to Bob in the platform team"
        self.assertEqual(enc.similarity(a, a), 1.0)
        self.assertGreaterEqual(enc.similarity(a, a.lower() + "."), enc.NEAR_DUPLICATE)

    def test_vetoes_do_not_stack(self):
        a, b = "A depends on B", "B depends on A"
        self.assertAlmostEqual(enc.similarity(a, b), enc.VETO_FACTOR * raw(a, b))


if __name__ == "__main__":
    unittest.main()


class ProseUnchanged(unittest.TestCase):
    def test_plain_plural_prose_not_identifier_vetoed(self):
        self.assertFalse(enc._identifier_veto("call the user before rendering",
                                              "call the users before rendering"))
        self.assertTrue(enc._identifier_veto("call get_user before", "call get_users before"))
