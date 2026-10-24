"""Unit tests for the similarity engine (R9, R10, R11)."""
import unittest

from alethea import encode


class SimilarityTest(unittest.TestCase):
    def test_identical_is_one(self):
        self.assertAlmostEqual(encode.similarity("use postgres here", "use postgres here"), 1.0)

    def test_empty_is_zero(self):
        self.assertEqual(encode.similarity("", ""), 0.0)

    def test_identifier_veto_damps_digit_swap_of_short_token(self):
        s = encode.similarity("upload to s3 nightly", "upload to s4 nightly")
        self.assertLess(s, encode.NEAR_DUPLICATE)

    def test_pure_addition_without_veto_is_not_damped(self):
        a = "run the nightly migration before every production deploy of the api"
        self.assertGreaterEqual(encode.similarity(a, a + " today"), encode.NEAR_DUPLICATE)

    def test_negation_contraction_vetoed(self):
        a, b = "the api is thread safe", "the api isn't thread safe"
        self.assertLess(encode.similarity(a, b), encode.NEAR_DUPLICATE)

    def test_substitution_damp_applies_to_long_entity_swap(self):
        pre = "when the release engineering team prepares the weekly rollout they deploy to "
        post = " before the final sign off happens on friday afternoon"
        a, b = pre + "staging" + post, pre + "prod" + post
        raw = max(encode.cosine(encode.vectorize.embed(a), encode.vectorize.embed(b)),
                  encode.jaccard(a, b))
        self.assertAlmostEqual(encode.similarity(a, b), raw * encode.VETO_FACTOR)

    def test_substitution_damp_does_not_stack_with_other_vetoes(self):
        pre = "when the release engineering team prepares the weekly rollout they deploy to "
        post = " before the final sign off happens on friday afternoon"
        for x, y, n in (("v1", "v2", 1), ("staging", "no prod", 1)):
            a, b = pre + x + post, pre + y + post
            raw = max(encode.cosine(encode.vectorize.embed(a), encode.vectorize.embed(b)),
                      encode.jaccard(a, b))
            self.assertAlmostEqual(encode.similarity(a, b), raw * encode.VETO_FACTOR ** n)

    def test_threshold_never_below_default(self):
        encode.register_threshold("loose", 0.5)
        try:
            self.assertEqual(encode.threshold("loose"), encode.NEAR_DUPLICATE)
            encode.register_threshold("strict", 0.95)
            self.assertEqual(encode.threshold("strict"), 0.95)
        finally:
            encode._THRESHOLDS.clear()

    def test_novelty_is_one_minus_best_similarity(self):
        self.assertAlmostEqual(encode.novelty("x y z", ["x y z", "q"]), 0.0)
        self.assertEqual(encode.novelty("x y z", []), 1.0)

    def test_compare_vectors_refuses_mixed_ids(self):
        self.assertIsNone(encode.compare_vectors(("a", [1.0]), ("b", [1.0])))
        self.assertAlmostEqual(encode.compare_vectors(("a", [1.0, 0.0]), ("a", [1.0, 0.0])), 1.0)


if __name__ == "__main__":
    unittest.main()
