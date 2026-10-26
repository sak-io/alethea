"""Recall regression gate (T21): corpus precision/recall, relevance floor, scope, spread.

Measured on the committed corpus (tests/data/recall_corpus.py, 43 cues, 80 memories
incl. 20 near-miss distractors): recall@3 0.953, top-1 0.907 (hash salt alethea-v1, ADR 0014); untuned held-out split
(tests/data/recall_heldout.py, 30 cues) 0.833; 0 of 19 off-topic/nonsense cues return anything.
Gates sit just under those so a real regression fails.
"""
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from alethea import cortex as c
from alethea import recall, vectorize
from tests.data import recall_corpus as rc
from tests.data import recall_heldout as ho

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

    def test_untuned_heldout_split_gate(self):
        """tests/data/recall_heldout.py: no constant was tuned against it."""
        n = len(ho.CUES)
        self.assertGreaterEqual(n, 30)
        ok = sum(any(x in self.top(q) for x in e) for q, e in ho.CUES)
        self.assertGreaterEqual(ok / n, ho.MIN_RECALL_AT_3, "%d/%d" % (ok, n))

    def test_held_out_tagged_cues_gate(self):
        held = [(q, e) for q, e, tag in rc.CUES if tag == "held-out"]
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

    def snapshot(self):
        return {m["id"]: (m["strength"], m["access_count"], m["last_access"])
                for m in map(self.cx.get, self.ids.values())}

    def test_negative_cues_return_nothing_and_reinforce_nothing(self):
        before = self.snapshot()
        negatives = rc.NEGATIVE_CUES + rc.OFFTOPIC_CUES
        self.assertGreaterEqual(len(rc.OFFTOPIC_CUES), 12)
        returned = {q: [self.inv[m["id"]] for m in recall.recall(self.cx, q)]
                    for q in negatives}
        self.assertEqual({q: r for q, r in returned.items() if r}, {})
        self.assertEqual(before, self.snapshot())

    def test_partial_overlap_against_ml_memories_returns_nothing(self):
        ml = ["We train the ranking model with gradient boosting and tune the learning rate weekly.",
              "Machine learning features are computed nightly and stored in the feature store.",
              "The model learns from click feedback; deep learning experiments live in the research repo.",
              "Learning to rank evaluation uses NDCG on a held out set of queries."]
        for t in ml:
            self.add(t)
        before = self.snapshot()
        for q in ("learn to cook pasta from scratch", "learn guitar chords for beginners",
                  "stock market outlook for next quarter"):
            self.assertEqual(recall.recall(self.cx, q), [], q)
        self.assertEqual(before, self.snapshot())

    def test_one_irrelevant_extra_word_still_finds_the_memory(self):
        for cue, expected, _ in rc.CUES[:15]:
            if "direct" not in _:
                continue
            for extra in ("banana", "sunshine"):
                got = self.top(cue + " " + extra)
                self.assertTrue(any(e in got for e in expected), (cue, extra, got))

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

    def test_same_repo_outranks_near_equal_foreign_real_candidates(self):
        """Ten identical docs rank 1.0, 0.9, ... lexically; the same-repo copy sits 2nd."""
        cx = self.cx
        for mid in (self.foreign, self.null, self.here):
            cx.delete(mid)
        text = "rotate the payments gateway signing keys every quarter"
        first = self.add(text, repo_root="/r/other")
        here = self.add(text, repo_root="/r/here", cwd="/r/here")
        for _ in range(8):
            self.add(text, repo_root="/r/other")
        cue = "rotate payments gateway signing keys"
        plain = recall.recall(cx, cue, reinforce=False)
        self.assertEqual(plain[0]["id"], first)  # without context, FTS order wins
        scoped = recall.recall(cx, cue, reinforce=False, repo_root="/r/here")
        self.assertEqual(scoped[0]["id"], here)

    def test_irrelevant_same_repo_does_not_outrank_relevant_foreign(self):
        for mid in (self.foreign, self.null, self.here):
            self.cx.delete(mid)
        good = self.add("billing service retries failed charges with exponential backoff",
                        repo_root="/r/other")
        meh = self.add("billing dashboard colour palette and font tweaks for the settings page",
                       repo_root="/r/here")
        res = recall.recall(self.cx, "billing retries failed charges", reinforce=False,
                            repo_root="/r/here")
        self.assertEqual(res[0]["id"], good)
        self.assertNotEqual(res[0]["id"], meh)

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
