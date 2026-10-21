import math
import tempfile
import unittest
from pathlib import Path

from engram import cortex as c

DAY = 86400.0


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class ActivationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.cx = c.Cortex(Path(self.tmp.name) / "m.db", clock=self.clock)

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def test_age_in_days_matches_formula(self):
        mid = self.cx.insert("episodic", "x", salience=0.5)
        self.clock.t += 10 * DAY
        exp = math.log(1.0 / 0.5) - 0.5 * math.log(10) + 0.3 * math.exp(-10 / 7)
        self.assertAlmostEqual(self.cx.activation(mid), exp, places=9)

    def test_one_minute_age_floor(self):
        mid = self.cx.insert("episodic", "x")
        a0 = self.cx.activation(mid)
        self.clock.t += 10
        self.assertAlmostEqual(self.cx.activation(mid), a0, places=3)

    def test_salience_term(self):
        hi = self.cx.insert("semantic", "a", salience=0.9)
        lo = self.cx.insert("semantic", "b", salience=0.1)
        self.assertAlmostEqual(self.cx.activation(hi) - self.cx.activation(lo), 1.6 * 0.8, places=9)

    def test_identity_no_age_penalty_and_not_reduced(self):
        mid = self.cx.insert("identity", "me")
        a0 = self.cx.activation(mid)
        self.clock.t += 3650 * DAY
        a1 = self.cx.activation(mid)
        # only the recency term may fade; the base stays ln(strength)
        self.assertAlmostEqual(a1, math.log(1.0) + 0.3 * math.exp(-3650 / 7), places=9)
        self.assertGreater(a1, a0 - 0.31)
        self.assertGreater(a1, c.ARCHIVE_ACTIVATION)

    def test_strength_drives_activation(self):
        mid = self.cx.insert("semantic", "x")
        a0 = self.cx.activation(mid)
        self.cx.reinforce(mid, 1.0)
        self.assertAlmostEqual(self.cx.activation(mid) - a0, math.log(2.0), places=9)

    def test_strength_floor(self):
        a = c.base_activation("episodic", 0.0, 0.5, 1, 1)
        self.assertTrue(math.isfinite(a))
        self.assertAlmostEqual(a, c.base_activation("episodic", 1e-3, 0.5, 1, 1))

    def test_recent_access_outranks_stale(self):
        a = self.cx.insert("semantic", "a")
        b = self.cx.insert("semantic", "b")
        self.clock.t += 30 * DAY
        self.cx.reinforce(a, 0.0)
        self.assertGreater(self.cx.activation(a), self.cx.activation(b))

    def test_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            self.cx.activation("nope")


class RobustTest(unittest.TestCase):
    BAD = (float("nan"), float("inf"), float("-inf"), -5.0, None)

    def test_bad_strength_age_finite(self):
        for bad in self.BAD:
            for args in ((bad, 0.5, 1, 1), (1.0, 0.5, bad, 1), (1.0, 0.5, 1, bad), (1.0, bad, 1, 1)):
                for kind in c.KINDS:
                    self.assertTrue(math.isfinite(c.base_activation(kind, *args)), (kind, args))

    def test_corrupt_rows_finite(self):
        tmp = tempfile.TemporaryDirectory()
        clock = Clock()
        cx = c.Cortex(Path(tmp.name) / "m.db", clock=clock)
        try:
            mid = cx.insert("episodic", "x")
            for bad in self.BAD + (clock.t + 100 * DAY,):
                for col in ("created_at", "last_access"):
                    mem = cx.get(mid)
                    mem[col] = bad
                    self.assertTrue(math.isfinite(cx.activation(mem)), (col, bad))
            self.assertTrue(math.isfinite(cx.activation(cx.get(mid), now=float("nan"))))
        finally:
            cx.close()
            tmp.cleanup()

    def test_recency_pinned(self):
        base = c.base_activation("identity", 1.0, 0.5, 1, 1e9)
        self.assertAlmostEqual(c.base_activation("identity", 1.0, 0.5, 1, 0) - base, 0.3)
        self.assertAlmostEqual(c.base_activation("identity", 1.0, 0.5, 1, 7) - base, 0.3 / math.e)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            c.base_activation("bogus", 1, 0.5, 1)


class CalibrationTest(unittest.TestCase):
    def days(self, kind, table, s=0.5):
        for day in range(1, 2000):
            st = c.DOWNSCALE_FACTOR ** (table[kind] * day)
            if c.base_activation(kind, st, s, day, day) < c.ARCHIVE_ACTIVATION:
                return day
        return None

    def test_adr_uniform_anchors(self):
        self.assertTrue(28 <= self.days("episodic", c.UNIFORM_RATE) <= 34)
        self.assertTrue(36 <= self.days("semantic", c.UNIFORM_RATE) <= 46)

    def test_per_kind_slows_durable_kinds(self):
        e, s, p = (self.days(k, c.DOWNSCALE_RATE) for k in ("episodic", "semantic", "procedural"))
        self.assertLess(e, s)
        self.assertLess(s, p)

    def test_rates_scale_with_decay(self):
        for k, d in c.DECAY.items():
            self.assertAlmostEqual(c.DOWNSCALE_RATE[k], d / 0.5)

    def test_identity_never_archives(self):
        self.assertIsNone(self.days("identity", c.DOWNSCALE_RATE))


if __name__ == "__main__":
    unittest.main()
