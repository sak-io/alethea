"""Scale NFR (T21): 10k memories, recall p95 < 300 ms. Opt-in: ENGRAM_PERF=1."""
import os
import random
import tempfile
import time
import unittest
from pathlib import Path

from engram import cortex as c
from engram import recall, vectorize

WORDS = ("service database cache queue deploy rollback token session billing invoice "
         "kafka redis postgres gateway latency timeout retry schema migration index shard "
         "worker cron batch alert pager incident certificate secret rotate backup restore "
         "monorepo branch review lint build release canary flag tenant audit webhook").split()


@unittest.skipUnless(os.environ.get("ENGRAM_PERF") == "1", "set ENGRAM_PERF=1")
class RecallScale(unittest.TestCase):
    N = 10_000

    def test_recall_p95_under_300ms_at_10k_memories(self):
        rnd = random.Random(7)
        with tempfile.TemporaryDirectory() as tmp:
            cx = c.Cortex(Path(tmp) / "m.db")
            try:
                for i in range(self.N):
                    text = " ".join(rnd.choice(WORDS) for _ in range(14)) + " id%d" % i
                    cx.insert("semantic", text, vector=vectorize.embed(text),
                              embedder_id=vectorize.EMBEDDER_ID)
                n = cx.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                self.assertGreaterEqual(n, self.N)
                cues = [" ".join(rnd.choice(WORDS) for _ in range(4)) for _ in range(60)]
                times = []
                for cue in cues:
                    t = time.perf_counter()
                    recall.recall(cx, cue, reinforce=True)
                    times.append(time.perf_counter() - t)
                times.sort()
                p95 = times[int(len(times) * 0.95) - 1]
                print("recall p50 %.1f ms p95 %.1f ms" % (times[len(times) // 2] * 1e3, p95 * 1e3))
                self.assertLess(p95, 0.300)
            finally:
                cx.close()


if __name__ == "__main__":
    unittest.main()
