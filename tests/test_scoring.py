import unittest

from alethea import encode
from tests.data import phrases


class AmygdalaTests(unittest.TestCase):
    def test_each_marker_class_fires(self):
        from alethea import markers
        table = {n: rx for n, _, rx in markers.AMYGDALA}
        for name, text in phrases.AMYGDALA_HITS:
            with self.subTest(name=name, text=text):
                self.assertTrue(table[name].search(text))
                self.assertGreater(encode.amygdala(text), 0.0)

    def test_weights_match_table(self):
        from alethea import markers
        w = {n: x for n, x, _ in markers.AMYGDALA}
        self.assertEqual(w["absolute"], 0.40)
        self.assertEqual(w["incident"], 0.35)
        self.assertEqual(w["critical"], 0.30)
        self.assertEqual(w["correction"], 0.30)
        self.assertEqual(w["decision"], 0.25)
        self.assertEqual(w["remember"], 0.25)
        self.assertEqual(w["error"], 0.20)
        self.assertEqual(w["deadline"], 0.20)
        self.assertEqual(w["causal"], 0.20)
        self.assertEqual(w["deprecation"], 0.15)
        self.assertEqual(w["emphasis"], 0.15)

    def test_neutral_is_zero(self):
        for t in phrases.NEUTRAL + ["", "   ", None]:
            self.assertEqual(encode.amygdala(t), 0.0)

    def test_short_fragment_penalized(self):
        self.assertLess(encode.amygdala("never again"), encode.amygdala("never do that again ok"))
        self.assertAlmostEqual(encode.amygdala("never again"), 0.20)

    def test_long_bonus_and_clamp(self):
        short = encode.amygdala("you must never skip the tests here")
        long_ = encode.amygdala("you must never skip the tests here " + "and so on " * 6)
        self.assertGreater(long_, short)
        stacked = ("must never break prod; critical security outage because of a bug, we decided "
                   "to remember this deadline, deprecated migration, NO WAY!! wrong, that's wrong")
        self.assertLessEqual(encode.amygdala(stacked), 1.0)
        self.assertGreater(encode.amygdala(stacked), 0.9)


class DurabilityTests(unittest.TestCase):
    def test_affect_free_signals_are_durable(self):
        for t in phrases.DURABLE_NO_AFFECT:
            with self.subTest(t=t):
                self.assertEqual(encode.amygdala(t), 0.0)
                self.assertGreaterEqual(encode.durability(t), 0.45)

    def test_includes_amygdala(self):
        t = "you must never commit secrets here"
        self.assertGreaterEqual(encode.durability(t), encode.amygdala(t))

    def test_chatter_not_durable(self):
        for t in phrases.NEUTRAL:
            self.assertLess(encode.durability(t), 0.45)

    def test_quantity_units_longest_suffix_first(self):
        from alethea import markers
        for text, expected in phrases.QUANTITY:
            with self.subTest(text=text):
                self.assertEqual(bool(markers.QUANTITY.search(text)), expected)

    def test_seconds_is_not_bare_s(self):
        from alethea import markers
        m = markers.QUANTITY.search("took 3 seconds")
        self.assertEqual(m.group(0), "3 seconds")
        self.assertEqual(markers.QUANTITY.search("took 250 ms").group(0), "250 ms")


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        for text, kind in phrases.CLASSIFY:
            with self.subTest(text=text):
                self.assertEqual(encode.classify(text), kind)

    def test_kind_is_valid(self):
        from alethea import cortex
        for text, _ in phrases.CLASSIFY:
            self.assertIn(encode.classify(text), cortex.KINDS)
        self.assertEqual(encode.classify(""), "episodic")


class NoveltyTests(unittest.TestCase):
    def test_injected_candidates_callable(self):
        seen = []

        def cands(text):
            seen.append(text)
            return ["the api uses sqlite for storage"]

        t = "the api uses sqlite for storage"
        self.assertAlmostEqual(encode.novelty(t, cands), 0.0)
        self.assertEqual(seen, [t])

    def test_one_item_different_corpus_is_not_zero(self):
        n = encode.novelty("deploy goes through github actions", lambda t: ["grandma bakes apple pie"])
        self.assertGreater(n, 0.9)

    def test_empty_is_fully_novel(self):
        self.assertEqual(encode.novelty("anything at all", lambda t: []), 1.0)

    def test_is_one_minus_similarity(self):
        a, b = "restart the worker after deploy", "restart the worker after the deploy"
        self.assertAlmostEqual(encode.novelty(a, lambda t: [b]), 1.0 - encode.similarity(a, b))


class AdversarialPerformanceTests(unittest.TestCase):
    SHAPES = {
        "comma_digits": "9," * 50000,
        "letters": "a" * 100000,
        "spaces": " " * 100000,
        "bang": "!" * 100000,
        "negations": "not " * 25000,
        "digits_unit": "1" * 100000 + "x",
        "caps": "AB " * 33000,
        "dots": "1." * 50000,
        "dollars": "$1" * 50000,
    }
    slowest = 0.0

    def _time(self, fn, *a):
        import time
        t0 = time.perf_counter()
        fn(*a)
        dt = time.perf_counter() - t0
        type(self).slowest = max(type(self).slowest, dt)
        self.assertLess(dt, 1.0)

    def test_public_functions_are_fast(self):
        from alethea import markers
        regexes = ([rx for _, _, rx in markers.AMYGDALA]
                   + [markers.IDENTITY, markers.RULE, markers.QUANTITY,
                      markers.CLASS_IDENTITY, markers.CLASS_PROCEDURAL, markers.CLASS_SEMANTIC])
        for name, text in self.SHAPES.items():
            for fn in (encode.amygdala, encode.durability, encode.classify):
                with self.subTest(shape=name, fn=fn.__name__):
                    self._time(fn, text)
            with self.subTest(shape=name, fn="novelty"):
                self._time(encode.novelty, text, lambda t, s=text: [s, "x y"])
            with self.subTest(shape=name, fn="regexes_clipped"):
                self._time(lambda: [rx.search(text[:markers.MAX_CHARS]) for rx in regexes])
        print("slowest adversarial: %.3fs" % type(self).slowest)

    def test_quantity_regex_linear_even_unclipped(self):
        from alethea import markers
        for text in ("9," * 50000, "1" * 100000 + "x", "1." * 50000, "$1" * 50000):
            self._time(markers.QUANTITY.search, text)


if __name__ == "__main__":
    unittest.main()
