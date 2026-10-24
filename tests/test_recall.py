import math
import tempfile
import time
import unittest
from pathlib import Path

from alethea import cortex as c
from alethea import recall, vectorize

DAY = 86400.0


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.cx = c.Cortex(Path(self.tmp.name) / "m.db", clock=self.clock)

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def add(self, text, kind="semantic", eid=vectorize.EMBEDDER_ID, **kw):
        return self.cx.insert(kind, text, vector=vectorize.embed(text),
                              embedder_id=eid, **kw)

    def test_fts_query_is_forgiving_or_and_safe(self):
        q = recall._fts_query('the AND "auth" NEAR( bugs* ) fixing')
        self.assertIn(" OR ", q)
        self.assertNotIn("*", q)
        self.assertEqual(recall._fts_query("the of and"), "")

    def test_lexical_hit_found(self):
        a = self.add("postgres connection pool exhausted under load")
        self.add("user prefers dark mode in the editor")
        res = recall.candidates(self.cx, "postgres pool")
        self.assertEqual(res[0]["id"], a)

    def test_only_active_status(self):
        a = self.add("postgres connection pool exhausted")
        self.cx.set_status(a, "archived")
        self.assertEqual(recall.candidates(self.cx, "postgres pool"), [])

    def test_vector_channel_skips_other_embedder(self):
        a = self.add("deploy pipeline rollback", eid="other-embedder:1")
        res = [m for m in recall.candidates(self.cx, "deploy pipeline rollback")
               if m["id"] == a]
        self.assertTrue(all(m["sem"] == 0.0 for m in res))  # lexical only

    def test_vector_channel_recency_cap(self):
        old = self.add("zebra quokka narwhal")
        self.cx.update(old, last_access=1.0)
        for i in range(700):
            self.cx.db.execute(
                "INSERT INTO memories (id,kind,content,created_at,last_access,status,"
                "vector,embedder_id) VALUES (?,?,?,?,?,?,?,?)",
                ("f%d" % i, "semantic", "filler%d" % i, 10.0 + i, 10.0 + i, "active",
                 vectorize.embed("filler%d" % i), vectorize.EMBEDDER_ID))
        self.cx.db.commit()
        m = {x["id"]: x for x in recall.candidates(self.cx, "zebra quokka narwhal")}
        self.assertIn(old, m)                 # FTS covers whole corpus
        self.assertEqual(m[old]["sem"], 0.0)  # but outside the vector window

    def test_score_formula(self):
        a = self.add("postgres pool")
        m = recall.candidates(self.cx, "postgres pool")[0]
        rel = 0.55 * m["lex"] + 0.45 * min(1.0, 1.8 * m["sem"])
        act = self.cx.activation(self.cx.get(a))
        want = rel * (0.55 + 0.45 / (1 + math.exp(-act)))
        self.assertAlmostEqual(m["score"], want, places=6)

    def test_reinforced_memory_outranks_slightly_better_untouched(self):
        # b is the better lexical match (rank 1 of 12, a is rank 2); a was reinforced.
        b = self.add("cache invalidation strategy")
        a = self.add("cache invalidation strategy for sessions")
        fill = [self.add("cache filler%d" % i) for i in range(10)]
        old = self.clock.t - 60 * DAY
        for mid in (a, b):
            self.cx.update(mid, created_at=old, last_access=old)
        self.cx.update(a, strength=6.0, last_access=self.clock.t - 1 * DAY)
        self.cx.search_fts = lambda q, limit=20: [b, a] + fill
        by = {m["id"]: m for m in recall.candidates(self.cx, "cache invalidation strategy")}
        self.assertGreater(by[b]["relevance"], by[a]["relevance"])
        self.assertGreater(by[a]["score"], by[b]["score"])

    def test_recall_reinforces_hits_and_spreads(self):
        a = self.add("auth bug in login flow")
        b = self.add("chose redis sessions")
        self.cx.link(a, b)
        res = recall.recall(self.cx, "auth bug login")
        via = {m["id"]: m["via"] for m in res}
        self.assertEqual(via[a], "hit")
        self.assertEqual(via[b], "spread")
        self.assertAlmostEqual(self.cx.get(a)["strength"], 1.6)
        self.assertAlmostEqual(self.cx.get(b)["strength"], 1.35)

    def test_identity_cue_returns_identity_rows(self):
        i = self.add("The user is Ada, a compiler engineer", kind="identity")
        self.add("something unrelated", kind="semantic")
        res = recall.recall(self.cx, "Who am I talking to?")
        self.assertEqual(res[0]["id"], i)
        self.assertEqual(res[0]["via"], "identity")
        self.assertAlmostEqual(self.cx.get(i)["strength"], 1.6)

    def test_identity_cue_negative(self):
        self.assertFalse(recall.is_identity_cue("fix the login bug"))
        self.assertTrue(recall.is_identity_cue("who am i?"))

    def test_k_default_eight(self):
        for i in range(12):
            self.add("shared topic number%d widget" % i)
        self.assertEqual(len(recall.recall(self.cx, "shared topic widget")), 8)

    def test_adversarial_100kb_cues_are_fast(self):
        self.add("hello world")
        cues = ["who am i " * 12000, " " * 100000, "a" * 100000, '"' * 100000,
                "who" + " " * 100000 + "am i", "x? " * 33000]
        t0 = time.time()
        for cue in cues:
            recall.is_identity_cue(cue)
            recall.recall(self.cx, cue)
        self.assertLess(time.time() - t0, 5.0)

    def test_empty_cue(self):
        self.assertEqual(recall.recall(self.cx, ""), [])
        self.assertEqual(recall.recall(self.cx, None), [])


if __name__ == "__main__":
    unittest.main()
