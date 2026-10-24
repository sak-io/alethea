"""Dedup safety under every shipped embedder (T25, R9-R11, lessons 1-2, ADR 0010).

The T02 suites (test_dedup_adversarial, test_dedup_extra, test_similarity) run on the default
hashed embedder; this module repeats their assertions, over the same fixture tables, for each
embedder in EMBEDDER_NAMES: hash, a deterministic FAKE dense embedder (384 and 1536 dims) that
puts the adversarial pairs at cosine >= 0.97 like real sentence models do, and the real
FastEmbedEmbedder class with only its model call stubbed. Distinct facts must never merge:
the vetoes read the text, not the embedding, and the constant-embedder test proves it.
"""
import unittest
from unittest import mock

from alethea import encode as enc
from alethea import vectorize
from alethea.cortex import Cortex
from tests import fake_dense
from tests.data.dedup_pairs import LONG_PAIRS, MERGE, PAIRS
from tests.data.dedup_pairs_extra import LONG_PAIRS_EXTRA, SHORT_PAIRS
from tests.embedder_helpers import EMBEDDER_NAMES, activated

# Categories that are one distinct fact against another (must never merge, whatever the vectors).
DISTINCT = [(c, a, b) for c, a, b in PAIRS + LONG_PAIRS + SHORT_PAIRS + LONG_PAIRS_EXTRA
            if c not in (MERGE, "unrelated")]
MERGES = [(c, a, b) for c, a, b in PAIRS if c == MERGE]
UNRELATED = [(c, a, b) for c, a, b in PAIRS if c == "unrelated"]
# Case-only identifier variants land at 0.96-0.97 in the fake; every other family is >= 0.97.
CASE_ONLY = ("case",)


def sim_under(eid, a, b):
    """encode.similarity with the vectors of embedder `eid` (similarity itself never calls an
    embedder; it takes vectors, so the dense cosine really is in play)."""
    used, vecs = vectorize.embed_batch((a, b), eid)
    assert used == eid, (used, eid)
    return enc.similarity(a, b, embedder=eid, vectors=tuple(vecs))


def raw_under(eid, a, b):
    used, (va, vb) = vectorize.embed_batch((a, b), eid)
    assert used == eid, (used, eid)
    return max(vectorize.cosine(va, vb), vectorize.jaccard(a, b))


class FakeDenseFixtureTest(unittest.TestCase):
    """The fake really is as bad as a real sentence model (otherwise the suite proves nothing)."""

    def test_adversarial_pairs_are_near_identical_vectors(self):
        for cat, a, b in DISTINCT:
            with self.subTest(cat=cat, b=b[-50:]):
                floor = 0.95 if cat in CASE_ONLY else 0.97
                self.assertGreaterEqual(fake_dense.cosine(a, b), floor)

    def test_named_failure_modes_at_097(self):
        pairs = [
            ("enable auth on the gateway", "disable auth on the gateway"),
            ("alice owns the billing service", "bob owns the billing service"),
            ("retry 3 times then fail", "retry 5 times then fail"),
            ("upgrade to v1.2.3 today", "upgrade to v1.2.4 today"),
            ("restart svc0000 after the deploy", "restart svc0001 after the deploy"),
            ("the api is thread safe", "the api is not thread safe"),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertGreaterEqual(fake_dense.cosine(a, b), 0.97)

    def test_restatements_stay_close_and_unrelated_far(self):
        for _c, a, b in MERGES:
            self.assertGreaterEqual(fake_dense.cosine(a, b), 0.97)
        for _c, a, b in UNRELATED:
            self.assertLess(fake_dense.cosine(a, b), 0.5)

    def test_vectors_are_deterministic_and_dimensioned(self):
        for dim in (384, 1536):
            v = fake_dense.fake_vector("hello world", dim)
            self.assertEqual(len(v), dim)
            self.assertEqual(v, fake_dense.fake_vector("hello world", dim))
            self.assertAlmostEqual(sum(x * x for x in v), 1.0, places=9)


class PerEmbedderDedupTest(unittest.TestCase):
    def test_only_true_restatements_merge(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for cat, a, b in PAIRS:
                    with self.subTest(embedder=name, cat=cat, a=a, b=b):
                        s = sim_under(eid, a, b)
                        if cat == MERGE:
                            self.assertGreaterEqual(s, enc.threshold(eid))
                        else:
                            self.assertLess(s, enc.threshold(eid))

    def test_long_pairs_vetoed_below_threshold(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for cat, a, b in LONG_PAIRS + LONG_PAIRS_EXTRA:
                    with self.subTest(embedder=name, cat=cat, b=b[-40:]):
                        self.assertGreaterEqual(raw_under(eid, a, b), enc.NEAR_DUPLICATE)  # only a veto helps
                        self.assertLess(sim_under(eid, a, b), enc.threshold(eid))
                        self.assertLess(sim_under(eid, b, a), enc.threshold(eid))

    def test_short_extra_pairs_below_threshold_both_orders(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for cat, a, b in SHORT_PAIRS:
                    with self.subTest(embedder=name, cat=cat, a=a[-40:]):
                        self.assertLess(sim_under(eid, a, b), enc.threshold(eid))
                        self.assertLess(sim_under(eid, b, a), enc.threshold(eid))

    def test_symmetric(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for cat, a, b in PAIRS:
                    with self.subTest(embedder=name, cat=cat):
                        self.assertAlmostEqual(sim_under(eid, a, b),
                                               sim_under(eid, b, a), places=9)

    def test_similarity_unit_cases(self):
        """test_similarity.py cases, per embedder."""
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                t = enc.threshold(eid)
                self.assertAlmostEqual(sim_under(eid, "use postgres here", "use postgres here"), 1.0)
                self.assertEqual(sim_under(eid, "", ""), 0.0)
                self.assertLess(sim_under(eid, "upload to s3 nightly", "upload to s4 nightly"), t)
                self.assertLess(sim_under(eid, "the api is thread safe", "the api isn't thread safe"), t)
                a = "run the nightly migration before every production deploy of the api"
                self.assertGreaterEqual(sim_under(eid, a, a + " today"), t)
                self.assertAlmostEqual(sim_under(eid, "A depends on B", "B depends on A"),
                                       enc.VETO_FACTOR * raw_under(eid, "A depends on B", "B depends on A"))
                self.assertEqual(enc.threshold(eid), max(enc.NEAR_DUPLICATE, enc.threshold(eid)))
                self.assertGreaterEqual(enc.threshold(eid), 0.88)

    def test_substitution_damp_exact_under_each_embedder(self):
        pre = "when the release engineering team prepares the weekly rollout they deploy to "
        post = " before the final sign off happens on friday afternoon"
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                a, b = pre + "staging" + post, pre + "prod" + post
                self.assertAlmostEqual(sim_under(eid, a, b),
                                       raw_under(eid, a, b) * enc.VETO_FACTOR)

    def test_encode_end_to_end_distinct_facts_never_merge(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for cat, a, b in DISTINCT:
                    with self.subTest(embedder=name, cat=cat, b=b[-40:]):
                        cx = Cortex(":memory:")
                        self.addCleanup(cx.close)
                        ra, rb = enc.encode(cx, a, force=True), enc.encode(cx, b, force=True)
                        self.assertEqual((ra["status"], rb["status"]), ("created", "created"))
                        rows = cx.db.execute("SELECT embedder_id FROM memories").fetchall()
                        self.assertEqual([r[0] for r in rows], [eid, eid])

    def test_encode_end_to_end_restatements_merge(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for _c, a, b in MERGES:
                    with self.subTest(embedder=name, b=b):
                        cx = Cortex(":memory:")
                        self.addCleanup(cx.close)
                        self.assertEqual(enc.encode(cx, a, force=True)["status"], "created")
                        self.assertEqual(enc.encode(cx, b, force=True)["status"], "reinforced")

    def test_encode_end_to_end_unrelated_not_merged(self):
        for name in EMBEDDER_NAMES:
            with self.subTest(embedder=name), activated(name) as eid:
                for _c, a, b in UNRELATED:
                    with self.subTest(embedder=name, b=b):
                        cx = Cortex(":memory:")
                        self.addCleanup(cx.close)
                        self.assertEqual(enc.encode(cx, a, force=True)["status"], "created")
                        self.assertEqual(enc.encode(cx, b, force=True)["status"], "created")


class VetoesIgnoreTheEmbeddingTest(unittest.TestCase):
    """The strongest form of the claim: make the vector channel say 'identical' (cosine 1.0)
    for every pair and distinct facts still do not merge, because vetoes read the text."""

    def test_distinct_facts_below_threshold_even_at_cosine_one(self):
        with mock.patch.object(enc, "cosine", return_value=1.0):
            for cat, a, b in DISTINCT:
                with self.subTest(cat=cat, b=b[-40:]):
                    self.assertLess(enc.similarity(a, b), enc.NEAR_DUPLICATE)

    def test_veto_damping_is_independent_of_the_vectors(self):
        a, b = "enable auth on the gateway", "disable auth on the gateway"
        for cos in (0.0, 0.5, 0.97, 1.0):
            with mock.patch.object(enc, "cosine", return_value=cos):
                s = enc.similarity(a, b)
                self.assertAlmostEqual(s, max(cos, vectorize.jaccard(a, b)) * enc.VETO_FACTOR)


class RestatementLimitsTest(unittest.TestCase):
    """Where true restatements may fail to merge (documented: ADR 0006/0007, lessons 1)."""

    def test_pure_reordering_does_not_merge_under_any_embedder(self):
        a, b = "Alice reports to Bob in the platform team", "Bob reports to Alice in the platform team"
        for name in EMBEDDER_NAMES:
            with activated(name) as eid:
                with self.subTest(embedder=name):
                    self.assertLess(sim_under(eid, a, b), enc.threshold(eid))


if __name__ == "__main__":
    unittest.main()
