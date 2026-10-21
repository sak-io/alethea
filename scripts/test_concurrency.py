"""Concurrency tests against a real daemon: no lost writes, recall stays live during sleep."""
import threading
import time
import unittest

from engram import consolidate
from tests.daemon_helpers import InProc


def fact(tag, i):
    return "Service s%s%d listens on port %d with token slot x%dq" % (tag, i, 4000 + i * 13, i)


class SlowDistiller(consolidate.Distiller):
    def distill(self, turns):
        time.sleep(0.3)  # simulates an LLM call; runs outside the lock
        return super().distill(turns)


class ConcurrencyTests(unittest.TestCase):
    def test_threads_distinct_remembers_all_survive(self):
        d = InProc()
        try:
            n_threads, per = 8, 50
            ids, errors = [[] for _ in range(n_threads)], []

            def work(t):
                for i in range(per):
                    r = d.call("remember", {"content": fact("t%d" % t, i)}, timeout=10)
                    if not (r.get("ok") and r.get("status") == "created"):
                        errors.append(r)
                    else:
                        ids[t].append(r["id"])

            ts = [threading.Thread(target=work, args=(t,)) for t in range(n_threads)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=120)
            self.assertEqual(errors, [])
            flat = [i for l in ids for i in l]
            self.assertEqual(len(flat), n_threads * per)
            self.assertEqual(len(set(flat)), n_threads * per)
            self.assertEqual(d.call("status")["total"], n_threads * per)
        finally:
            d.stop()

    def test_32_threads_distinct_facts_persist(self):
        d = InProc()
        try:
            out = []

            def work(t):
                out.append(d.call("remember", {"content": fact("w%d" % t, t)}, timeout=10))

            ts = [threading.Thread(target=work, args=(t,)) for t in range(32)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(timeout=60)
            self.assertEqual(len({r["id"] for r in out if r.get("ok")}), 32)
        finally:
            d.stop()

    def test_recall_during_long_sleep_under_2s(self):
        d = InProc(distiller=SlowDistiller())
        try:
            d.call("remember", {"content": "Recall target: the staging database is pg-stage9"})
            cx = d.engram.cortex
            for s in range(20):  # 20 episodes x 0.3 s distill = ~6 s sleep
                for turn in range(4):
                    cx.journal_add("We decided episode %d step %d: always pin build%d in CI"
                                   % (s, turn, s * 7 + turn), session_id="ep%d" % s)
            done = {}

            def run():
                t0 = time.monotonic()
                done["r"] = d.call("sleep", {"budget_s": 60}, timeout=60)
                done["s"] = time.monotonic() - t0

            t = threading.Thread(target=run)
            t.start()
            time.sleep(0.5)
            worst = 0.0
            for _ in range(8):
                t0 = time.monotonic()
                r = d.call("recall", {"cue": "staging database pg-stage9"}, timeout=5)
                worst = max(worst, time.monotonic() - t0)
                self.assertTrue(r["ok"], r)
                self.assertTrue(r["results"])
                time.sleep(0.2)
            self.assertTrue(t.is_alive(), "sleep finished too early to prove anything")
            self.assertLess(worst, 2.0)
            t.join(timeout=60)
            self.assertTrue(done["r"]["ok"], done)
            self.assertGreater(done["s"], 2.0)
        finally:
            d.stop()


if __name__ == "__main__":
    unittest.main()
