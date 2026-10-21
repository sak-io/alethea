"""Recall regression gate (T21): corpus precision/recall, relevance floor, scope, spread.

Measured on the committed corpus (tests/data/recall_corpus.py, 43 cues, 80 memories
incl. 20 near-miss distractors): recall@3 0.953, top-1 0.884, precision@3 0.328 (cap
1/3 for single-target cues). Gates sit just under those so a real regression fails.
"""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from engram import cortex as c
from engram import recall, vectorize
from tests.data import recall_corpus as rc

DAY = 86400.0


class Clock:
    t = 1_000_000_000.0

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


class CorpusTest(Base):
    def setUp(self):
        super().setUp()
        self.ids = {}
        for key, kind, text in rc.MEMORIES + rc.DISTRACTORS + rc.AUTH_EXTRA:
            self.ids[key] = self.add(text, kind)
        for a, b, kind in rc.EDGES:
            self.cx.link(self.ids[a], self.ids[b], kind)
        self.inv = {v: k for k, v in self.ids.items()}

    def top(self, cue, n=3):
        res = recall.recall(self.cx, cue, k=n, reinforce=False)
        return [self.inv[m["id"]] for m in res][:n]

    def test_corpus_size(self):
        self.assertGreaterEqual(len(rc.MEMORIES), 60)
        self.assertGreaterEqual(len(rc.CUES), 40)

    def test_precision_recall_gate(self):
        hit3 = top1 = relevant = returned = 0
        misses = []
        for cue, expected, tag in rc.CUES:
            got = self.top(cue)
            hit3 += any(e in got for e in expected)
            top1 += bool(got) and got[0] in expected
            relevant += sum(g in expected for g in got)
            returned += len(got)
            if not any(e in got for e in expected):
                misses.append((tag, cue, got))
        n = len(rc.CUES)
        msg = "misses: %r" % misses
        self.assertGreaterEqual(hit3 / n, rc.MIN_RECALL_AT_3, msg)
        self.assertGreaterEqual(top1 / n, rc.MIN_TOP1, msg)
        self.assertGreaterEqual(relevant / returned, rc.MIN_PRECISION_AT_3, msg)

    def test_held_out_phrasing_recall_floor(self):
        held = [(q, e) for q, e, tag in rc.CUES if tag == "held-out"]
        self.assertGreaterEqual(len(held), 10)
        ok = sum(any(x in self.top(q) for x in e) for q, e in held)
        self.assertGreaterEqual(ok / len(held), 0.75)

    def test_distractor_never_beats_target_for_direct_cues(self):
        for cue, expected, tag in rc.CUES:
            if tag != "direct":
                continue
            got = self.top(cue)
            if any(e in got for e in expected):
                first = min(got.index(e) for e in expected if e in got)
                self.assertLessEqual(first, 1, (cue, got))

    def test_nonsense_cue_returns_nothing_and_reinforces_nothing(self):
        before = {m["id"]: (m["strength"], m["access_count"], m["last_access"])
                  for m in map(self.cx.get, self.ids.values())}
        for cue in rc.NEGATIVE_CUES:
            self.assertEqual(recall.recall(self.cx, cue), [], cue)
        after = {m["id"]: (m["strength"], m["access_count"], m["last_access"])
                 for m in map(self.cx.get, self.ids.values())}
        self.assertEqual(before, after)

    def test_floor_never_drops_lexical_hits(self):
        # a rare lexical token with almost no semantic overlap must still be returned
        m = self.add("qzx7 marker for the frobnicator")
        res = recall.candidates(self.cx, "frobnicator")
        self.assertIn(m, [r["id"] for r in res])

    def test_the_auth_bug_structural_spread(self):
        res = recall.recall(self.cx, rc.AUTH_CUE, reinforce=False)
        via = {self.inv[m["id"]]: m["via"] for m in res}
        self.assertEqual(via["c_auth_race"], "hit")
        self.assertEqual(via["e_auth_fix"], "spread")
        self.assertEqual(via["e_auth_decision"], "spread")


class ActivationModulatesTest(Base):
    def test_stale_better_match_beats_reinforced_weaker_match(self):
        best = self.add("postgres connection pool exhausted by leaked connections", "episodic")
        weak = self.add("pool table schedule for the office social")
        self.clock.t += 400 * DAY
        for _ in range(30):
            self.cx.reinforce(weak, 1.0)
        res = recall.recall(self.cx, "postgres connection pool exhausted", reinforce=False)
        self.assertEqual(res[0]["id"], best)
        self.assertGreater(self.cx.activation(weak), self.cx.activation(best))

    def test_reinforcement_raises_score_but_only_within_modulation_band(self):
        a = self.add("rotate the staging api key every quarter")
        self.clock.t += 60 * DAY
        base = recall.candidates(self.cx, "rotate staging api key")[0]["score"]
        for _ in range(30):
            self.cx.reinforce(a, 1.0)
        top = recall.candidates(self.cx, "rotate staging api key")[0]
        self.assertGreater(top["score"], base)
        self.assertLessEqual(top["score"], top["relevance"] + 1e-9)  # never above relevance
        self.assertGreaterEqual(top["score"], 0.55 * top["relevance"] - 1e-9)


class ScopeTest(Base):
    TEXT = "billing service retries failed charges with exponential backoff"

    def setUp(self):
        super().setUp()
        self.foreign = self.add(self.TEXT, repo_root="/r/other", cwd="/r/other")
        self.null = self.add(self.TEXT + " ", repo_root=None, cwd=None)
        self.here = self.add(self.TEXT + "  ", repo_root="/r/here", cwd="/r/here/sub")

    def ids(self, **kw):
        res = recall.recall(self.cx, "billing retries failed charges", reinforce=False, **kw)
        return [m["id"] for m in res]

    def test_same_repo_outranks_equal_scoring_foreign(self):
        # lexical rank normalisation never yields exact ties on real text, so pin the
        # candidate scores equal and check that only the boost separates them.
        def fake(cortex, cue, pool=20, embedder_id=None):
            out = []
            for mid in (self.foreign, self.null, self.here):  # foreign listed first
                m = cortex.get(mid)
                m["score"], m["relevance"] = 0.5, 0.5
                out.append(m)
            return out
        with mock.patch.object(recall, "candidates", fake):
            res = recall.recall(self.cx, "billing", reinforce=False,
                                repo_root="/r/here", cwd="/r/here/sub")
        self.assertEqual(res[0]["id"], self.here)
        self.assertEqual({m["id"] for m in res}, {self.foreign, self.null, self.here})

    def test_boost_is_soft_activation_still_matters(self):
        # a much better match from another repo is not buried by the boost
        best = self.add("billing service retries failed charges", repo_root="/r/other")
        self.assertEqual(self.ids(repo_root="/r/here")[0], best)

    def test_scope_all_keeps_null_repo_and_foreign_eligible(self):
        got = self.ids(repo_root="/r/here", scope="all")
        self.assertEqual(set(got), {self.foreign, self.null, self.here})

    def test_scope_repo_is_a_hard_filter(self):
        self.assertEqual(self.ids(repo_root="/r/here", scope="repo"), [self.here])

    def test_scope_repo_without_context_returns_nothing(self):
        self.assertEqual(self.ids(scope="repo"), [])

    def test_no_context_means_no_reordering(self):
        self.assertEqual(set(self.ids()), {self.foreign, self.null, self.here})

    def test_bad_scope_rejected(self):
        with self.assertRaises(ValueError):
            self.ids(scope="galaxy")


if __name__ == "__main__":
    unittest.main()
