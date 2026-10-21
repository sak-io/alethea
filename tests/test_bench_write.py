"""CI-safe smoke test for scripts/bench_write.py (small n); the real benchmark is opt-in."""
import io
import unittest

from scripts import bench_write


class BenchWriteSmoke(unittest.TestCase):
    def test_bands_report_and_every_distinct_fact_is_created(self):
        out = io.StringIO()
        bands = bench_write.run([50, 120], profile_last=30, out=out)
        self.assertEqual([(b[0], b[1]) for b in bands], [(0, 50), (50, 120)])
        for _lo, _hi, ms, created in bands:
            self.assertGreater(ms, 0)
        self.assertEqual(sum(b[3] for b in bands), 120)  # all facts distinct: none merged
        self.assertIn("ms/write", out.getvalue())
        self.assertIn("cProfile of the last 30 writes", out.getvalue())

    def test_facts_are_distinct_and_deterministic(self):
        a, b = list(bench_write.facts(300)), list(bench_write.facts(300))
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 300)


if __name__ == "__main__":
    unittest.main()
