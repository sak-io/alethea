"""Adversarial dedup suite (R9, R10, R11). Written before similarity code.

Skipped until engram.encode.similarity exists (T07 removes the guard).
"""
import unittest

from tests.data.dedup_pairs import BLEND_COSINE, BLEND_JACCARD, MERGE, PAIRS

try:
    from engram import encode
except ImportError:  # package/module not built yet
    encode = None

HAVE_SIM = encode is not None and hasattr(encode, "similarity") and hasattr(encode, "NEAR_DUPLICATE")

# Embedder ids under test; T25 appends the dense ones.
EMBEDDERS = ["hashed"]


def sim(a, b, embedder):
    """Similarity under a named embedder (default embedder for 'hashed')."""
    if embedder == "hashed":
        return encode.similarity(a, b)
    return encode.similarity(a, b, embedder=embedder)


def assert_mixed_ids_not_compared(test, compare, vec_a, vec_b):
    """R9 helper: `compare(vec_a, vec_b)` with differing embedder_id must
    refuse (raise ValueError) or return None/0.0; never a real score."""
    try:
        result = compare(vec_a, vec_b)
    except ValueError:
        return
    test.assertIn(result, (None, 0.0), "vectors with different embedder_id were compared")


class BlendRegressionTest(unittest.TestCase):
    def test_blend_would_merge_but_max_does_not(self):
        blend = (BLEND_COSINE + BLEND_JACCARD) / 1.4
        self.assertAlmostEqual(blend, 0.93, places=2)
        self.assertGreaterEqual(blend, 0.88)  # the bug this suite guards against
        self.assertLess(max(BLEND_COSINE, BLEND_JACCARD), 0.88)


@unittest.skipUnless(HAVE_SIM, "engram.encode.similarity not implemented yet (T07)")
class DedupAdversarialTest(unittest.TestCase):
    def test_only_true_restatements_merge(self):
        for emb in EMBEDDERS:
            for cat, a, b in PAIRS:
                with self.subTest(embedder=emb, category=cat, a=a, b=b):
                    s = sim(a, b, emb)
                    if cat == MERGE:
                        self.assertGreaterEqual(s, encode.NEAR_DUPLICATE)
                    else:
                        self.assertLess(s, encode.NEAR_DUPLICATE)

    def test_similarity_is_symmetric(self):
        for emb in EMBEDDERS:
            for cat, a, b in PAIRS:
                with self.subTest(embedder=emb, category=cat):
                    self.assertAlmostEqual(sim(a, b, emb), sim(b, a, emb), places=9)

    def test_identifier_swap_stays_below_threshold(self):
        for emb in EMBEDDERS:
            with self.subTest(embedder=emb):
                s = sim("restart svc0000 after the deploy", "restart svc0101 after the deploy", emb)
                self.assertLess(s, encode.NEAR_DUPLICATE)

    def test_mixed_embedder_ids_never_compared(self):
        cmp = getattr(encode, "compare_vectors", None)
        if cmp is None:
            self.skipTest("no vector compare entry point yet")
        assert_mixed_ids_not_compared(self, cmp, ("id-a", [1.0, 0.0]), ("id-b", [1.0, 0.0]))


if __name__ == "__main__":
    unittest.main()
