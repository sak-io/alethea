import unittest

from engram import consolidate
from engram.cortex import Cortex

BASE = "the build uses sqlite for local storage of memories"
RELATED = "the build uses postgres for remote storage of memories"
NEAR_DUP = "the build uses sqlite for local storage of memories today"
UNRELATED = "deploy the kubernetes cluster tonight"


class RecombineTest(unittest.TestCase):
    def setUp(self):
        self.c = Cortex(":memory:", clock=lambda: 1000.0)
        self.addCleanup(self.c.close)

    def edges(self):
        return self.c.db.execute("SELECT * FROM edges WHERE kind='recombined'").fetchall()

    def test_related_pair_linked_others_not(self):
        a = self.c.insert("semantic", BASE, embedder_id="e1")
        b = self.c.insert("semantic", RELATED, embedder_id="e1")
        self.c.insert("semantic", UNRELATED, embedder_id="e1")
        self.assertEqual(consolidate.recombine(self.c), 1)
        e = self.edges()
        self.assertEqual({e[0]["src"], e[0]["dst"]}, {a, b})

    def test_near_duplicate_not_linked(self):
        self.c.insert("semantic", BASE)
        self.c.insert("semantic", NEAR_DUP)
        self.assertEqual(consolidate.recombine(self.c), 0)

    def test_veto_damps_similarity_below_band(self):
        # identifier veto: same words but different identifiers must not look related
        self.c.insert("semantic", "see config in src/app/main.py for the setting")
        self.c.insert("semantic", "see config in src/app/other.py for the setting")
        from engram.encode import similarity
        s = similarity("see config in src/app/main.py for the setting",
                       "see config in src/app/other.py for the setting")
        expected = 1 if consolidate.RECOMBINE_LO <= s <= consolidate.RECOMBINE_HI else 0
        self.assertEqual(consolidate.recombine(self.c), expected)

    def test_idempotent_no_duplicate_links(self):
        self.c.insert("semantic", BASE)
        self.c.insert("procedural", RELATED)
        self.assertEqual(consolidate.recombine(self.c), 1)
        self.assertEqual(consolidate.recombine(self.c), 0)
        self.assertEqual(len(self.edges()), 1)
        self.assertAlmostEqual(self.edges()[0]["weight"], 0.15)

    def test_episodic_archived_and_cross_embedder_excluded(self):
        self.c.insert("episodic", BASE)
        self.c.insert("semantic", RELATED)  # only partner is episodic: excluded
        self.c.insert("semantic", BASE, embedder_id="e1")
        self.c.insert("semantic", RELATED, embedder_id="e2")  # different embedder
        z = self.c.insert("semantic", RELATED + " ", embedder_id="e1")
        self.c.set_status(z, "archived")
        self.assertEqual(consolidate.recombine(self.c), 0)

    def test_cap_40_links(self):
        for i in range(30):
            self.c.insert("semantic", "alpha beta gamma delta epsilon w%d" % i)
        self.assertEqual(consolidate.recombine(self.c), 40)
        self.assertEqual(len(self.edges()), 40)

    def test_only_top_60_by_salience(self):
        for i in range(60):
            self.c.insert("semantic", "zq%dx unique%dy" % (i, i), salience=0.9)
        a = self.c.insert("semantic", BASE, salience=0.1)
        b = self.c.insert("semantic", RELATED, salience=0.1)
        self.assertEqual(consolidate.recombine(self.c), 0)
        self.assertIsNone(self.c.edge_weight(a, b, "recombined"))


if __name__ == "__main__":
    unittest.main()
