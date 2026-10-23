"""A slow, hung or failing embedder never degrades the daemon (T25 review, ADR 0010).

Real Engram + Server on a temp ENGRAM_HOME, real Unix-socket client calls with the 2 s budget a
hook has. The embedder is a controllable test double (tests/slow_embedder.py); the real
fastembed package is exercised in the last class when it is installed.
"""
import os
import shutil
import sys
import threading
import time
import unittest

from engram import client, vectorize
from engram.daemon import Engram, Server
from tests import slow_embedder
from tests.daemon_helpers import ROOT, make_home, spawn, wait_up

BUDGET = 2.0          # a hook's client budget
FAST = 0.5            # what a verb must answer in while the embedder is stuck
HASH_ID = vectorize.EMBEDDER_ID
DENSE_ID = slow_embedder.ControlledEmbedder.id
DECISION = "We decided to use postgres for the billing ledger because it needs transactions %d"


class Daemon:
    """Engram + Server in threads of this process (like tests.daemon_helpers.InProc) with env."""

    def __init__(self, ctl, extra_env=None):
        vectorize._reset_for_tests()
        slow_embedder.register(ctl)
        self.ctl = ctl
        self.home = make_home()
        self.env = {"ENGRAM_HOME": self.home, "PATH": os.environ.get("PATH", ""),
                    "ENGRAM_EMBEDDER": "slow:x"}
        self.env.update(extra_env or {})
        self.engram = Engram(home=self.home, env=self.env)
        ctl.lock_probe = self._lock_free
        self.server = Server(self.engram)
        assert self.server.acquire_instance()
        self.server.start()
        self.cenv = {"ENGRAM_HOME": self.home}

    def _lock_free(self):
        if self.engram.lock.acquire(timeout=0.5):
            self.engram.lock.release()
            return True
        return False

    def call(self, verb, params=None, budget=BUDGET):
        t = time.monotonic()
        r = client.call(verb, params, timeout=budget, env=self.cenv)
        return r, time.monotonic() - t

    def stop(self):
        self.ctl.stop()
        self.server.shutdown()
        vectorize._reset_for_tests()
        shutil.rmtree(self.home, ignore_errors=True)

    def rows(self):
        with self.engram.lock:
            return [tuple(r) for r in self.engram.cortex.db.execute(
                "SELECT embedder_id, length(vector)/4 FROM memories ORDER BY rowid")]


class Base(unittest.TestCase):
    def start(self, ctl, **env):
        d = Daemon(ctl, env)
        self.addCleanup(d.stop)
        return d

    def all_verbs_fast(self, d, limit=BUDGET, tag=""):
        """Every verb answers inside the hook budget (and `ok`, never offline)."""
        worst = 0.0
        i = int(time.monotonic() * 1000) % 1000000
        for verb, p in ([
                ("ping", {}), ("status", {}), ("briefing", {}),
                ("recall", {"cue": "billing ledger postgres"}),
                ("remember", {"content": "All verbs remember %s %d" % (tag, i)}),
                ("observe", {"content": DECISION % (900 + i)}),
                ("reflect", {"content": "Reflection about billing %s %d" % (tag, i)}),
                ("sleep", {"budget_s": 1.0})]):
            r, dt = d.call(verb, p, budget=limit)
            self.assertTrue(r.get("ok"), (verb, r))
            self.assertFalse(r.get("offline"), (verb, r))
            self.assertLess(dt, limit, (verb, dt))
            worst = max(worst, dt)
        return worst


class SlowLoadTest(Base):
    def test_slow_model_load_never_blocks_requests_and_flips_atomically(self):
        ctl = slow_embedder.Control(load_delay=3.0)
        d = self.start(ctl)
        t0 = time.monotonic()
        r, dt = d.call("status")
        self.assertLess(dt, FAST)
        self.assertEqual(r["embedder_state"], "loading")
        self.assertTrue(r["embedder_falling_back"])
        self.assertEqual(r["embedder_id"], HASH_ID)
        # everything answers promptly while the load is in progress, writes carry the hashed id
        worst = self.all_verbs_fast(d, FAST, "loading")
        a, dt = d.call("remember", {"content": "The ledger service uses postgres for invoices"})
        self.assertEqual(a["status"], "created")
        self.assertLess(time.monotonic() - t0, 3.0, "test ran too slowly to prove anything")
        self.assertEqual({row[0] for row in d.rows()}, {HASH_ID})
        self.assertEqual(ctl.calls, 0)  # nothing was embedded by the dense embedder yet
        self.assertTrue(vectorize.wait_settled(10))
        r, _ = d.call("status")
        self.assertEqual((r["embedder_state"], r["embedder_id"], r["embedder_falling_back"]),
                         ("ready", DENSE_ID, False))
        b, _ = d.call("remember", {"content": "The warehouse robots charge overnight in bay four"})
        self.assertEqual(b["status"], "created")
        self.assertEqual(d.rows()[-1], (DENSE_ID, 384))     # new writes only
        self.assertEqual(d.rows()[0][0], HASH_ID)           # old rows untouched
        print("\n[latency] slow load: worst verb while loading %.3fs" % worst)

    def test_load_deadline_abandons_a_stuck_load(self):
        ctl = slow_embedder.Control(load_delay=30.0)
        d = self.start(ctl, ENGRAM_EMBEDDER_LOAD_TIMEOUT="1")
        time.sleep(1.3)
        r, dt = d.call("status")
        self.assertLess(dt, FAST)
        self.assertEqual(r["embedder_state"], "falling-back")
        self.assertIn("load-timeout", r["embedder_reason"])
        self.assertIsNotNone(r["embedder_retry_in_s"])
        self.assertEqual(r["embedder_id"], HASH_ID)
        self.all_verbs_fast(d, FAST, "deadline")

    def test_load_failure_reports_reason_and_retries_in_background_only(self):
        ctl = slow_embedder.Control(load_raises=True)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        r, dt = d.call("status")
        self.assertEqual(r["embedder_state"], "falling-back")
        self.assertIn("load OSError", r["embedder_reason"])
        self.assertGreater(r["embedder_retry_in_s"], 0)
        self.all_verbs_fast(d, FAST, "loadfail")
        self.assertEqual({row[0] for row in d.rows()}, {HASH_ID})


class SlowEmbedTest(Base):
    def test_slow_embed_times_out_to_hashed_and_nothing_else_waits(self):
        ctl = slow_embedder.Control(embed_delay=3.0)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        d.call("remember", {"content": "A baseline fact about the billing ledger postgres"})  # times out
        out = {}

        def slow():
            out["r"] = d.call("remember", {"content": "Another distinct fact about robots charging"})
        th = threading.Thread(target=slow)
        th.start()
        time.sleep(0.2)
        lat = {}
        for verb, p in (("status", {}), ("recall", {"cue": "billing ledger postgres"}), ("ping", {}),
                        ("remember", {"content": "A second remember while the first is in flight"})):
            r, dt = d.call(verb, p)
            lat[verb] = dt
            self.assertTrue(r.get("ok"), (verb, r))
            self.assertLess(dt, FAST, (verb, dt))
        th.join(5)
        r, dt = out["r"]
        self.assertTrue(r.get("ok"))
        self.assertLess(dt, 1.0 + FAST)   # timeout + 0.5
        self.assertTrue({row[0] for row in d.rows()} <= {HASH_ID})
        recall, _ = d.call("recall", {"cue": "billing ledger postgres"})
        self.assertTrue(recall["results"])  # lexical channel unaffected
        print("\n[latency] slow embed (3s): %s" % {k: round(v, 3) for k, v in lat.items()})

    def test_every_verb_inside_the_hook_budget(self):
        ctl = slow_embedder.Control(embed_delay=3.0)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        self.all_verbs_fast(d, BUDGET, "slowembed")

    def test_call_returning_after_the_timeout_counts_as_timeout_not_dense_vector(self):
        ctl = slow_embedder.Control(embed_delay=1.5)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        r, dt = d.call("remember", {"content": "A fact that the slow model embeds in 1.5 seconds"})
        self.assertLess(dt, 1.0 + FAST)
        self.assertEqual(d.rows()[-1], (HASH_ID, 512))
        time.sleep(1.0)  # the late result must not be adopted by anybody
        self.assertEqual(d.rows()[-1], (HASH_ID, 512))
        self.assertEqual(vectorize.status()["embedder_state"], "falling-back")

    def test_embedder_timeout_is_configurable(self):
        ctl = slow_embedder.Control(embed_delay=0.6)
        d = self.start(ctl, ENGRAM_EMBEDDER_TIMEOUT="0.2")
        self.assertTrue(vectorize.wait_settled(5))
        r, dt = d.call("remember", {"content": "Configurable timeout fact about invoices"})
        self.assertLess(dt, 0.2 + FAST)
        self.assertEqual(d.rows()[-1][0], HASH_ID)


class HangTest(Base):
    def test_hung_embedder_never_holds_the_lock_or_any_verb(self):
        ctl = slow_embedder.Control(hang=True)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        r, dt = d.call("remember", {"content": "This call meets a hung model"})
        self.assertTrue(r.get("ok"))
        self.assertLess(dt, 1.0 + FAST)
        self.assertEqual(d.rows()[-1][0], HASH_ID)
        self.all_verbs_fast(d, FAST, "hang")
        r, _ = d.call("status")
        self.assertEqual(r["embedder_state"], "falling-back")

    def test_no_thread_leak_over_200_calls_with_a_hung_embedder(self):
        ctl = slow_embedder.Control(hang=True)
        d = self.start(ctl, ENGRAM_EMBEDDER_TIMEOUT="0.05")
        self.assertTrue(vectorize.wait_settled(5))
        base = threading.active_count()
        for i in range(200):
            r = d.engram.handle("remember", {"content": "hung call number %d about invoices" % i})
            self.assertTrue(r.get("ok"))
        self.assertLessEqual(threading.active_count(), base + 2)
        self.assertLess(ctl.calls, 5)   # one stuck call, then the breaker / busy worker bypass it

    def test_no_thread_leak_over_200_calls_with_a_failing_embedder(self):
        ctl = slow_embedder.Control(raises=True)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        base = threading.active_count()
        for i in range(200):
            self.assertTrue(d.engram.handle("remember", {"content": "failing call %d about invoices" % i})["ok"])
        self.assertLessEqual(threading.active_count(), base + 2)


class BreakerTest(Base):
    def test_breaker_opens_after_n_failures_makes_no_calls_and_recovers(self):
        ctl = slow_embedder.Control(raises=True)
        old = vectorize.COOLDOWN_MIN
        vectorize.COOLDOWN_MIN = 0.4
        self.addCleanup(setattr, vectorize, "COOLDOWN_MIN", old)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        for i in range(3):
            d.engram.handle("remember", {"content": "breaker warmup %d about invoices" % i})
        self.assertEqual(ctl.calls, 3)
        r = d.engram.handle("status", {})
        self.assertEqual(r["embedder_state"], "falling-back")
        self.assertIn("circuit breaker", r["embedder_reason"])
        t = time.monotonic()
        for i in range(30):
            d.engram.handle("remember", {"content": "during cooldown %d about invoices" % i})
        self.assertLess(time.monotonic() - t, 0.3)      # no embedder calls: nothing waits
        self.assertEqual(ctl.calls, 3)
        self.assertEqual({row[0] for row in d.rows()}, {HASH_ID})
        # recovery: the embedder works again; after the cooldown ONE probe runs off-thread
        ctl.raises = False
        time.sleep(0.5)
        t = time.monotonic()
        d.engram.handle("remember", {"content": "this request triggers the probe about invoices"})
        self.assertLess(time.monotonic() - t, 0.3)      # served hashed, the probe did not block it
        self.assertEqual(d.rows()[-1][0], HASH_ID)
        time.sleep(0.3)
        self.assertEqual(ctl.calls, 4)                  # exactly one probe call
        d.engram.handle("remember", {"content": "after recovery the dense embedder is back"})
        self.assertEqual(d.rows()[-1], (DENSE_ID, 384))
        self.assertFalse(d.engram.handle("status", {})["embedder_falling_back"])

    def test_failed_probe_doubles_the_cooldown(self):
        ctl = slow_embedder.Control(raises=True)
        old = vectorize.COOLDOWN_MIN
        vectorize.COOLDOWN_MIN = 0.2
        self.addCleanup(setattr, vectorize, "COOLDOWN_MIN", old)
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        for i in range(3):
            d.engram.handle("remember", {"content": "open the breaker %d about invoices" % i})
        time.sleep(0.3)
        d.engram.handle("remember", {"content": "probe me please about invoices"})
        time.sleep(0.2)
        self.assertEqual(ctl.calls, 4)
        self.assertGreater(vectorize._cooldown, 0.3)    # 0.2 doubled
        calls = ctl.calls
        for i in range(10):
            d.engram.handle("remember", {"content": "still cooling %d about invoices" % i})
        self.assertEqual(ctl.calls, calls)


class LockDisciplineTest(Base):
    def test_no_embedder_call_ever_happens_under_the_engine_lock(self):
        ctl = slow_embedder.Control()
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        sid = {"session_id": "s1"}
        for i in range(6):
            d.call("observe", dict(sid, content="We decided to use redis for sessions because it is fast %d" % i))
        d.call("remember", {"content": "Lock discipline remember about invoices"})
        d.call("reflect", {"content": "Lock discipline reflect about invoices"})
        d.call("recall", {"cue": "which store holds sessions"})
        d.call("sleep", {"consolidate_only": False, "budget_s": 2.0})
        d.call("briefing", {})
        self.assertGreater(ctl.calls, 3)
        self.assertEqual(ctl.lock_violations, 0)

    def test_dense_dedup_uses_stored_vectors_not_new_embedder_calls(self):
        ctl = slow_embedder.Control()
        d = self.start(ctl)
        self.assertTrue(vectorize.wait_settled(5))
        for i in range(3):
            d.call("remember", {"content": "Seed fact %d about the billing ledger and postgres" % i})
        before = ctl.calls
        r, _ = d.call("remember", {"content": "Seed fact 1 about the billing ledger and postgres."})
        self.assertEqual(r["status"], "reinforced")
        self.assertEqual(ctl.calls - before, 1)    # only the new text; candidates' vectors are stored


class RealFastembedTest(unittest.TestCase):
    """Real daemon process + real fastembed with the model download blocked (HTTP 403)."""

    @classmethod
    def setUpClass(cls):
        try:
            import fastembed  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("fastembed not installed in this interpreter "
                                    "(pip install 'engram-memory[local]')")

    @staticmethod
    def _reap(proc):
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except Exception:
            proc.kill()
            proc.communicate()

    def test_blocked_download_answers_every_verb_promptly_and_status_explains(self):
        home = make_home()
        cache = make_home()
        env = {"ENGRAM_EMBEDDER": "fastembed:sentence-transformers/all-MiniLM-L6-v2",
               "ENGRAM_EMBEDDER_LOAD_TIMEOUT": "3", "FASTEMBED_CACHE_PATH": cache, "HF_HOME": cache}
        proc = spawn(home, env)
        self.addCleanup(self._reap, proc)
        self.addCleanup(shutil.rmtree, home, True)
        self.assertTrue(wait_up(home, 15))
        cenv = {"ENGRAM_HOME": home}
        lat = {}
        for verb, p in (("status", {}), ("remember", {"content": "The billing service uses postgres"}),
                        ("recall", {"cue": "billing postgres"}), ("ping", {}), ("briefing", {}),
                        ("observe", {"content": "We decided to use redis because it is fast"})):
            t = time.monotonic()
            r = client.call(verb, p, timeout=BUDGET, env=cenv)
            lat[verb] = time.monotonic() - t
            self.assertTrue(r.get("ok"), (verb, r))
            self.assertLess(lat[verb], BUDGET, verb)
        end = time.time() + 12
        st = {}
        while time.time() < end:
            st = client.call("status", timeout=BUDGET, env=cenv)
            if st.get("embedder_state") == "falling-back":
                break
            time.sleep(0.25)
        self.assertEqual(st.get("embedder_state"), "falling-back", st)
        self.assertTrue(st["embedder_reason"])
        self.assertEqual(st["embedder_id"], HASH_ID)
        r = client.call("recall", {"cue": "billing postgres"}, timeout=BUDGET, env=cenv)
        self.assertTrue(r["results"])
        print("\n[latency] real fastembed, blocked download: %s" % {k: round(v, 3) for k, v in lat.items()})


if __name__ == "__main__":
    unittest.main()
