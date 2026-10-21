import unittest

from engram import consolidate
from engram.cortex import Cortex, DAY_S, DOWNSCALE_RATE


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def days(self, n):
        self.t += n * DAY_S


class DownscaleTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = Cortex(":memory:", clock=self.clock)

    def prime(self):
        """First call only sets the marker (0 elapsed)."""
        r = consolidate.downscale(self.c)
        self.assertTrue(r["ran"])
        self.assertEqual(r["periods"], 0.0)

    def test_per_kind_rate_on_strength(self):
        e = self.c.insert("episodic", "e", strength=4.0)
        s = self.c.insert("semantic", "s", strength=4.0)
        p = self.c.insert("procedural", "p", strength=4.0)
        self.prime()
        self.clock.days(5)
        consolidate.downscale(self.c)
        for mid, kind in ((e, "episodic"), (s, "semantic"), (p, "procedural")):
            want = 4.0 * 0.93 ** (5 * DOWNSCALE_RATE[kind])
            self.assertAlmostEqual(self.c.get(mid)["strength"], want, places=6)

    def test_second_call_within_6h_is_noop(self):
        mid = self.c.insert("episodic", "e", strength=4.0)
        self.prime()
        self.clock.days(2)
        self.assertTrue(consolidate.downscale(self.c)["ran"])
        after = self.c.get(mid)["strength"]
        self.clock.t += 5 * 3600
        self.assertFalse(consolidate.downscale(self.c)["ran"])
        self.assertEqual(self.c.get(mid)["strength"], after)
        self.clock.t += 2 * 3600
        self.assertTrue(consolidate.downscale(self.c)["ran"])

    def test_bad_last_downscale_clamps_to_zero_elapsed(self):
        for bad in ("garbage", "nan", "inf", "32503680000", ""):  # last is year 3000
            c = Cortex(":memory:", clock=self.clock)
            mid = c.insert("episodic", "e", strength=4.0)
            c.meta_set("last_downscale", bad)
            self.clock.days(30)
            r = consolidate.downscale(c)
            self.assertEqual(c.get(mid)["strength"], 4.0, bad)
            self.assertEqual(r["periods"], 0.0, bad)
            self.assertFalse(consolidate.downscale(c)["ran"], bad)  # marker reset to now

    def test_negative_last_downscale_clamped(self):
        mid = self.c.insert("episodic", "e", strength=4.0)
        self.c.meta_set("last_downscale", "-5")
        r = consolidate.downscale(self.c)
        self.assertLessEqual(r["periods"], 14.0)  # never more than the cap
        self.assertGreaterEqual(self.c.get(mid)["strength"], 4.0 * 0.93 ** 14)

    def test_period_cap_14_days(self):
        mid = self.c.insert("episodic", "e", strength=4.0, salience=1.0)
        self.prime()
        self.clock.days(400)
        r = consolidate.downscale(self.c)
        self.assertEqual(r["periods"], 14.0)
        self.assertAlmostEqual(self.c.get(mid)["strength"], 4.0 * 0.93 ** 14, places=6)

    def test_identity_never_touched(self):
        mid = self.c.insert("identity", "I am Ada", strength=1.0, salience=0.0)
        self.prime()
        for _ in range(40):
            self.clock.days(14)
            consolidate.downscale(self.c)
        m = self.c.get(mid)
        self.assertEqual((m["strength"], m["status"]), (1.0, "active"))

    def test_archive_then_hard_delete_after_45_days(self):
        mid = self.c.insert("episodic", "old fact", salience=0.0)
        other = self.c.insert("semantic", "other", salience=1.0)
        self.c.link(mid, other)
        self.prime()
        archived_on = deleted_on = None
        for day in range(1, 200):
            self.clock.days(1)
            consolidate.downscale(self.c)
            m = self.c.get(mid)
            if m is None:
                deleted_on = day
                break
            if m["status"] == "archived" and archived_on is None:
                archived_on = day
        self.assertIsNotNone(archived_on)
        self.assertLess(archived_on, 60)
        self.assertIsNotNone(deleted_on)
        self.assertIn(deleted_on - archived_on, (45, 46))
        self.assertEqual(self.c.edges_from(mid), [])
        self.assertIsNotNone(self.c.get(other))

    def test_archived_with_access_or_non_episodic_not_deleted(self):
        a = self.c.insert("episodic", "x")
        self.c.reinforce(a)
        b = self.c.insert("semantic", "y")
        for m in (a, b):
            self.c.update(m, status="archived",
                          meta={"archived_at": self.clock.t - 100 * DAY_S})
        consolidate.downscale(self.c)
        self.assertIsNotNone(self.c.get(a))
        self.assertIsNotNone(self.c.get(b))

    def test_suppressed_pruned_after_90_days_not_identity(self):
        a = self.c.insert("semantic", "a")
        b = self.c.insert("semantic", "b")
        i = self.c.insert("identity", "i")
        for m in (a, b, i):
            self.c.set_status(m, "suppressed")
        self.c.update(a, last_access=self.clock.t - 91 * DAY_S)
        self.c.update(b, last_access=self.clock.t - 89 * DAY_S)
        self.c.update(i, last_access=self.clock.t - 900 * DAY_S)
        r = consolidate.downscale(self.c)
        self.assertEqual(r["deleted"], 1)
        self.assertIsNone(self.c.get(a))
        self.assertIsNotNone(self.c.get(b))
        self.assertIsNotNone(self.c.get(i))


if __name__ == "__main__":
    unittest.main()
