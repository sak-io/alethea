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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.cx = c.Cortex(Path(self.tmp.name) / "m.db", clock=self.clock)

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def add(self, text, kind="semantic", **kw):
        return self.cx.insert(kind, text, vector=vectorize.embed(text),
                              embedder_id=vectorize.EMBEDDER_ID, **kw)


class SpreadTest(Base):
    def test_no_lexical_overlap_causal_link_surfaces(self):
        bug = self.add("auth token refresh race condition bug")
        dec = self.add("migrated sessions to redis store", kind="episodic")
        self.cx.link(bug, dec, "caused")
        res = recall.recall(self.cx, "auth token refresh race", reinforce=False)
        via = {m["id"]: m["via"] for m in res}
        self.assertEqual(via[bug], "hit")
        self.assertEqual(via[dec], "spread")

    def test_two_hops_and_no_third(self):
        a = self.add("auth token refresh race condition bug")
        b = self.cx.insert("episodic", "zebra alpha")
        d = self.cx.insert("episodic", "quokka beta")
        e = self.cx.insert("episodic", "narwhal gamma")
        self.cx.link(a, b)
        self.cx.link(b, d)
        self.cx.link(d, e)
        hits = recall.candidates(self.cx, "auth token refresh race")
        got = [m["id"] for m in recall.spread(self.cx, hits)]
        self.assertIn(b, got)
        self.assertIn(d, got)
        self.assertNotIn(e, got)

    def test_spread_skips_inactive_and_does_not_reinforce(self):
        a = self.add("auth token refresh race condition bug")
        b = self.cx.insert("episodic", "zebra alpha")
        d = self.cx.insert("episodic", "quokka beta")
        self.cx.link(a, b)
        self.cx.link(b, d)
        self.cx.set_status(b, "archived")
        hits = recall.candidates(self.cx, "auth token refresh race")
        before = self.cx.get(d)["strength"]
        self.assertEqual(recall.spread(self.cx, hits), [])
        self.assertEqual(self.cx.get(d)["strength"], before)


class BriefingTest(Base):
    def test_order_and_caps(self):
        for i in range(7):
            self.add("identity fact %d" % i, kind="identity")
        for i in range(12):
            self.add("semantic fact %d" % i, kind="semantic" if i % 2 else "procedural")
        for i in range(6):
            self.add("episode %d" % i, kind="episodic")
        b = recall.briefing(self.cx)
        labels = [ln.split("]")[0][1:] for ln in b["text"].splitlines()]
        self.assertEqual(labels, ["identity"] * 5 + ["fact"] * 8 + ["episode"] * 3)

    def test_does_not_reinforce(self):
        ids = [self.add("a fact", kind="identity"), self.add("b fact"),
               self.add("an episode", kind="episodic")]
        before = [self.cx.get(i) for i in ids]
        self.clock.t += DAY
        recall.briefing(self.cx)
        self.assertEqual(before, [self.cx.get(i) for i in ids])

    def test_excludes_archived_and_suppressed(self):
        a = self.add("archived fact")
        b = self.add("suppressed ident", kind="identity")
        d = self.add("archived ep", kind="episodic")
        for m, s in ((a, "archived"), (b, "suppressed"), (d, "archived")):
            self.cx.set_status(m, s)
        self.assertEqual(recall.briefing(self.cx)["text"], "")

    def test_repo_scope_episodes(self):
        self.add("mine ep", kind="episodic", repo_root="/r/a")
        self.add("other ep", kind="episodic", repo_root="/r/b")
        self.add("free ep", kind="episodic")
        t = recall.briefing(self.cx, repo_root="/r/a")["text"]
        self.assertIn("mine ep", t)
        self.assertIn("free ep", t)
        self.assertNotIn("other ep", t)

    def test_repo_boost_ranks_scoped_fact_but_unscoped_eligible(self):
        for i in range(8):
            self.add("filler %d" % i)
        scoped = self.add("scoped fact", repo_root="/r/a")
        # identical activation otherwise: boost lets the scoped one in, unscoped alone qualifies
        items = recall.briefing(self.cx, repo_root="/r/a")["items"]
        self.assertIn(scoped, [m["id"] for m in items])
        solo = tempfile.TemporaryDirectory()
        cx2 = c.Cortex(Path(solo.name) / "m.db", clock=self.clock)
        try:
            cx2.insert("semantic", "global fact")
            self.assertIn("global fact", recall.briefing(cx2, repo_root="/r/a")["text"])
        finally:
            cx2.close()
            solo.cleanup()

    def test_cwd_fallback_exact_match(self):
        self.add("cwd ep", kind="episodic", cwd="/w/x")
        self.add("elsewhere ep", kind="episodic", cwd="/w/y")
        t = recall.briefing(self.cx, cwd="/w/x")["text"]
        self.assertIn("cwd ep", t)
        self.assertNotIn("elsewhere ep", t)
        self.assertNotIn("cwd ep", recall.briefing(self.cx, cwd="/w/x/sub")["text"])

    def test_byte_cap_with_multibyte(self):
        for i in range(20):
            self.add("é" * 400 + str(i), kind="identity")
        b = recall.briefing(self.cx, max_bytes=1000)
        self.assertLessEqual(len(b["text"].encode("utf-8")), 1000)
        self.assertLessEqual(len(recall.briefing(self.cx)["text"].encode("utf-8")), 4096)

    def test_adversarial_100kb(self):
        self.add("x " * 50000, kind="identity")
        self.add("(a" * 50000 + "!", kind="semantic")
        t0 = time.time()
        b = recall.briefing(self.cx)
        self.assertLess(time.time() - t0, 1.0)
        self.assertLessEqual(len(b["text"].encode()), 4096)


if __name__ == "__main__":
    unittest.main()
