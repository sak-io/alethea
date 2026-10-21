"""sleep_cycle orchestration, triggers, journal purge, stage isolation, perf budget (T15)."""
import random
import time
import unittest
from unittest import mock

from engram import consolidate, encode, vectorize
from engram.cortex import Cortex, DAY_S

_SYL = "ba ke di mo tu ra ne si lo pa vi ze".split()
# ~1.7k pseudo-words: a natural-language-sized vocabulary, so FTS and the vector scan
# behave like real text rather than a 30-word toy corpus where every row matches.
WORDS = ["".join(p) for p in __import__("itertools").product(_SYL, _SYL, _SYL)]


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def sentence(rng, n=9):
    return " ".join(rng.choice(WORDS) + (str(rng.randint(0, 50)) if rng.random() < .2 else "")
                    for _ in range(n))


class Base(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Cortex(":memory:", clock=self.clock)
        self.rng = random.Random(7)

    def tearDown(self):
        self.c.close()

    def session(self, sid, turns=5, step=10.0):
        for _ in range(turns):
            self.clock.t += step
            self.c.journal_add(
                "We decided to always %s. Remember that %s." % (sentence(self.rng), sentence(self.rng)),
                session_id=sid, role="user", cwd="/w", repo_root="/w")
        self.clock.t += 2000

    def pending(self):
        return len(self.c.journal_pending())

    def stage_names(self, res):
        return list(res["stages"])


class StagesAndTriggersTest(Base):
    def test_full_cycle_runs_all_stages_with_counts_and_timings(self):
        for i in range(3):
            self.session("s%d" % i)
        res = consolidate.sleep_cycle(self.c)
        self.assertTrue(res["ok"])
        self.assertEqual(res["errors"], {})
        self.assertEqual(self.stage_names(res),
                         ["consolidate", "purge", "downscale", "recombine"])
        self.assertEqual(res["stages"]["consolidate"]["episodes"], 3)
        self.assertGreater(res["stages"]["consolidate"]["facts"], 0)
        for st in res["stages"].values():
            self.assertIn("ms", st)
        self.assertGreaterEqual(res["ms"], 0)
        self.assertEqual(self.pending(), 0)
        self.assertIsNotNone(self.c.meta_get("last_sleep"))

    def test_second_run_is_idempotent(self):
        self.session("a")
        consolidate.sleep_cycle(self.c)
        n = self.c.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        res = consolidate.sleep_cycle(self.c)
        self.assertEqual(res["stages"]["consolidate"]["episodes"], 0)
        self.assertEqual(self.c.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], n)
        self.assertFalse(res["stages"]["downscale"]["ran"])  # 6 h gate

    def test_precompact_deep_false_skips_downscale_and_recombine(self):
        self.session("a")
        with mock.patch.object(consolidate, "downscale") as d, \
                mock.patch.object(consolidate, "recombine") as r:
            res = consolidate.sleep_cycle(self.c, deep=False)
        d.assert_not_called()
        r.assert_not_called()
        self.assertEqual(self.stage_names(res), ["consolidate", "purge"])
        self.assertFalse(res["deep"])
        self.assertEqual(self.pending(), 0)

    def test_session_end_consolidate_only(self):
        self.session("a")
        with mock.patch.object(consolidate, "downscale") as d, \
                mock.patch.object(consolidate, "recombine") as r, \
                mock.patch.object(consolidate, "purge_journal") as p:
            res = consolidate.sleep_cycle(self.c, consolidate_only=True)
        for m in (d, r, p):
            m.assert_not_called()
        self.assertEqual(self.stage_names(res), ["consolidate"])
        self.assertEqual(self.pending(), 0)
        self.assertIsNone(self.c.meta_get("last_sleep"))

    def test_timer_trigger_default_is_deep(self):
        res = consolidate.sleep_cycle(self.c)
        self.assertTrue(res["deep"])


class StageFailureIsolationTest(Base):
    STAGES = {"consolidate": "consolidate_episodes", "purge": "purge_journal",
              "downscale": "downscale", "recombine": "recombine"}

    def check(self, failing):
        self.session("a")
        with mock.patch.object(consolidate, self.STAGES[failing],
                               side_effect=RuntimeError("boom")):
            res = consolidate.sleep_cycle(self.c)  # must not raise
        self.assertFalse(res["ok"])
        self.assertEqual(list(res["errors"]), [failing])
        self.assertIn("boom", res["errors"][failing])
        self.assertEqual(self.stage_names(res),
                         ["consolidate", "purge", "downscale", "recombine"])
        for name, st in res["stages"].items():
            if name != failing:
                self.assertNotEqual(st.get("skipped"), "budget")
        return res

    def test_consolidate_failure_does_not_stop_later_stages(self):
        res = self.check("consolidate")
        self.assertTrue(res["stages"]["downscale"]["ran"])
        self.assertEqual(self.pending(), 5)  # journal untouched, retried next time

    def test_purge_failure_isolated(self):
        res = self.check("purge")
        self.assertEqual(self.pending(), 0)
        self.assertIn("links", res["stages"]["recombine"])

    def test_downscale_failure_isolated(self):
        res = self.check("downscale")
        self.assertIn("links", res["stages"]["recombine"])
        self.assertEqual(self.pending(), 0)

    def test_recombine_failure_isolated(self):
        res = self.check("recombine")
        self.assertTrue(res["stages"]["downscale"]["ran"])

    def test_failing_distiller_does_not_raise(self):
        class Bad:
            def distill(self, turns):
                raise ValueError("nope")
        self.session("a")
        res = consolidate.sleep_cycle(self.c, distiller=Bad())
        self.assertTrue(res["ok"])  # falls back to extractive gist

    def test_all_stages_failing_still_returns(self):
        with mock.patch.multiple(consolidate, consolidate_episodes=mock.DEFAULT,
                                 purge_journal=mock.DEFAULT, downscale=mock.DEFAULT,
                                 recombine=mock.DEFAULT) as ms:
            for m in ms.values():
                m.side_effect = OSError("disk")
            res = consolidate.sleep_cycle(self.c)
        self.assertEqual(len(res["errors"]), 4)


class PurgeTest(Base):
    def add(self, age_days, consolidated):
        rid = self.c.journal_add("x " * 5, session_id="p")
        created = self.clock.t - age_days * DAY_S
        self.c.db.execute("UPDATE journal SET created_at=?, consolidated=? WHERE id=?",
                          (created, int(consolidated), rid))
        self.c.db.commit()
        return rid

    def ids(self):
        return {r["id"] for r in self.c.db.execute("SELECT id FROM journal")}

    def test_retention_windows(self):
        keep = [self.add(29, True), self.add(89, False), self.add(1, True), self.add(1, False)]
        gone = [self.add(31, True), self.add(91, False), self.add(400, True)]
        out = consolidate.purge_journal(self.c)
        self.assertEqual(self.ids(), set(keep))
        self.assertEqual(out, {"consolidated": 2, "unconsolidated": 1})
        self.assertEqual(len(gone), 3)

    def test_unconsolidated_between_30_and_90_days_kept(self):
        rid = self.add(60, False)
        consolidate.purge_journal(self.c)
        self.assertIn(rid, self.ids())

    def test_purge_is_batched_and_idempotent(self):
        for _ in range(consolidate.PURGE_BATCH + 20):
            self.add(40, True)
        out = consolidate.purge_journal(self.c)
        self.assertEqual(out["consolidated"], consolidate.PURGE_BATCH + 20)
        self.assertEqual(consolidate.purge_journal(self.c),
                         {"consolidated": 0, "unconsolidated": 0})

    def test_sleep_cycle_purges(self):
        self.add(45, True)
        res = consolidate.sleep_cycle(self.c)
        self.assertEqual(res["stages"]["purge"]["consolidated"], 1)


class CountingLock:
    """Records how many encode() writes happen inside each held region."""

    def __init__(self):
        self.holds = 0
        self.depth = 0
        self.max_depth = 0
        self.writes = []
        self._cur = 0


class LockWrapper:
    def __init__(self, counter):
        self.k = counter

    def __enter__(self):
        self.k.holds += 1
        self.k.depth += 1
        self.k.max_depth = max(self.k.max_depth, self.k.depth)
        self.k._cur = 0
        return self

    def __exit__(self, *a):
        self.k.depth -= 1
        self.k.writes.append(self.k._cur)
        return False


class LockAndBudgetTest(Base):
    def test_lock_is_per_batch_not_per_run(self):
        for i in range(6):
            self.session("s%d" % i)
        k = CountingLock()
        real = encode.encode

        def counted(*a, **kw):
            k._cur += 1
            self.assertEqual(k.depth, 1, "encode must run under the lock")
            return real(*a, **kw)

        with mock.patch.object(encode, "encode", counted):
            res = consolidate.sleep_cycle(self.c, lock=LockWrapper(k))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(k.depth, 0)
        self.assertEqual(k.max_depth, 1)  # never nested / held across stages
        self.assertGreaterEqual(k.holds, 6)  # at least one hold per episode
        self.assertLessEqual(max(k.writes), 7)  # one episode: summary + <= 6 facts

    def test_budget_stops_cleanly_between_batches_and_resumes(self):
        for i in range(8):
            self.session("s%d" % i)
        ticks = {"n": 0}

        def timer():
            ticks["n"] += 1
            return ticks["n"] * 1.0  # one "second" per poll

        res = consolidate.sleep_cycle(self.c, budget_s=4.5, timer=timer)
        self.assertTrue(res["stopped"])
        self.assertTrue(res["stages"]["consolidate"]["stopped"])
        done = res["stages"]["consolidate"]["episodes"]
        self.assertTrue(0 < done < 8, done)
        for name in ("downscale", "recombine"):
            self.assertEqual(res["stages"][name].get("skipped"), "budget")
        left = self.pending()
        self.assertGreater(left, 0)
        self.assertEqual(left, (8 - done) * 5)  # whole episodes only: never half-written
        res2 = consolidate.sleep_cycle(self.c)  # resumable
        self.assertEqual(res2["stages"]["consolidate"]["episodes"], 8 - done)
        self.assertEqual(self.pending(), 0)

    def test_no_budget_means_no_stop(self):
        self.session("a")
        self.assertFalse(consolidate.sleep_cycle(self.c)["stopped"])


class PerformanceBudgetTest(unittest.TestCase):
    """The T13 review saw consolidation slow down as memories grew (R21)."""

    def corpus(self, memories, sessions, rows_per_session=5, seed=3):
        rng = random.Random(seed)
        clock = Clock()
        c = Cortex(":memory:", clock=clock)
        for _ in range(memories):
            text = "We decided that " + sentence(rng)
            c.insert("semantic", text, gist=text, keywords=text, salience=0.6,
                     vector=vectorize.embed(text), embedder_id=vectorize.EMBEDDER_ID)
        for s in range(sessions):
            for r in range(rows_per_session):
                clock.t += 10
                if r < 2:  # decisions: durable, novel, written
                    text = ("We decided to always %s. Remember that %s."
                            % (sentence(rng), sentence(rng)))
                else:  # chatter: little durable content
                    text = "ok so %s maybe" % sentence(rng, 4)
                c.journal_add(text, session_id="s%d" % s)
            clock.t += 2000
        return c

    def test_2k_rows_400_sessions_over_1k_memories_under_budget(self):
        c = self.corpus(1000, 400)
        self.assertEqual(len(c.journal_pending()), 2000)
        t = time.perf_counter()
        res = consolidate.sleep_cycle(c, deep=False)
        elapsed = time.perf_counter() - t
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["stages"]["consolidate"]["episodes"], 400)
        self.assertEqual(len(c.journal_pending()), 0)
        # Every decision sentence is novel and durable, so each session still writes a
        # summary plus several facts through the full dedup gate.
        self.assertLess(elapsed, 30.0, "consolidation took %.1fs" % elapsed)
        c.close()

    def test_cost_per_episode_stays_flat_as_memories_grow(self):
        def per_episode(memories):
            c = self.corpus(memories, 15)
            t = time.perf_counter()
            consolidate.consolidate_episodes(c)
            dt = (time.perf_counter() - t) / 15
            c.close()
            return dt
        small, big = per_episode(100), per_episode(1200)
        # the vector scan is bounded (600 rows), so 12x the memories must not cost 12x
        self.assertLess(big, small * 6 + 0.05, "%.4f vs %.4f s/episode" % (big, small))


class FastPathEquivalenceTest(unittest.TestCase):
    """The perf fast paths must not change scoring."""

    def test_cosine_many_matches_cosine(self):
        rng = random.Random(5)
        texts = [sentence(rng, 12) for _ in range(40)] + ["", "x", "v2 v3"]
        blobs = [vectorize.embed(t) for t in texts]
        for cue in ("alpha beta", "", "x", texts[3], "v2"):
            q = vectorize.embed(cue)
            self.assertEqual(vectorize.cosine_many(q, blobs),
                             [vectorize.cosine(q, b) for b in blobs])

    def test_cosine_many_mismatched_dim_is_zero(self):
        q = vectorize.embed("alpha beta")
        self.assertEqual(vectorize.cosine_many(q, [b"\x00\x00\x80\x3f"]), [0.0])

    def test_recall_candidates_top_pool_is_exact(self):
        from engram import recall
        rng = random.Random(9)
        c = Cortex(":memory:", clock=Clock())
        for i in range(300):
            t = "We decided that " + sentence(rng)
            c.insert("semantic", t, gist=t, keywords=t, salience=rng.random(),
                     vector=vectorize.embed(t), embedder_id=vectorize.EMBEDDER_ID)
        # Cue made only of stopwords: no FTS channel, so relevance is the vector channel
        # alone and does not depend on `pool`; pool=10 must be a prefix of pool=1000.
        self.assertEqual(recall._fts_query("what is the of and"), "")
        cue = "what is the of and"
        got = recall.candidates(c, cue, pool=10)
        allc = recall.candidates(c, cue, pool=1000)
        self.assertEqual([m["id"] for m in got], [m["id"] for m in allc[:10]])
        c.close()


if __name__ == "__main__":
    unittest.main()
