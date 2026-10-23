"""`mnemo reembed` and the daemon `reembed` verb (T26, ADR 0011).

Stub HTTP server only (tests/remote_stub.py): no real provider endpoint is contacted. Verify:
`python3 -m unittest tests.test_reembed tests.test_remote_embedder -v`.
"""
import json
import os
import shutil
import signal
import subprocess
import time
import unittest
from unittest import mock

from engram import client, reembed, vectorize
from tests.daemon_helpers import make_home, spawn, wait_up
from tests.remote_helpers import (AWS, GHP, MODEL, PWD, SECRET_TEXT, SECRETS, RDaemon, db_rows,
                                  remote_env, seed)
from tests.remote_stub import KEY, Stub, wait_for
from tests.test_cli import MNEMO, run

HASH_ID = vectorize.EMBEDDER_ID
OPENAI_ID = "openai:%s:16" % MODEL
IDENTITY = "My name is Alexandra Quillfeather and I live in Gothenburg"


class Base(unittest.TestCase):
    def setUp(self):
        for name, val in (("PAUSE_S", 0.01), ("BACKOFF_S", (0.05,) * 5), ("FAIL_LIMIT", 3)):
            p = mock.patch.object(reembed, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.stub = Stub()
        self.addCleanup(self.stub.stop)
        self.daemons = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for d in reversed(self.daemons):
            try:
                d.stop()
            except Exception:
                pass
        for d in self.daemons[:1]:
            shutil.rmtree(d.home, ignore_errors=True)

    def hashed_store(self, n=12, identity=True):
        d = RDaemon({})
        self.daemons.append(d)
        seed(d, n)
        if identity:
            d.call("remember", {"content": IDENTITY, "kind": "identity"})
        home = d.home
        d.stop()
        return home

    def remote(self, home, stub=None, **extra):
        d = RDaemon(remote_env(stub or self.stub, **extra), home=home)
        self.daemons.append(d)
        self.assertEqual(d.status()["embedder_state"], "ready", d.status())
        return d

    def snapshot(self, d):
        with d.engram.lock:
            return [tuple(r) for r in d.engram.cortex.db.execute(
                "SELECT id,kind,content,gist,keywords,salience,strength,access_count,created_at,"
                "last_access,status,meta FROM memories ORDER BY rowid")], [
                tuple(r) for r in d.engram.cortex.db.execute("SELECT * FROM edges ORDER BY 1,2,3")]

    def run_to_end(self, d, **kw):
        r = d.reembed("start", confirm=True, **kw)
        self.assertTrue(r["ok"], r)
        self.assertTrue(d.wait_state(("done",)), d.reembed("status"))
        return d.reembed("status")["reembed"]


class PlanAndConsent(Base):
    def test_plan_counts_memories_characters_host_and_sends_nothing(self):
        home = self.hashed_store(10)
        d = self.remote(home)
        before = len(self.stub.inputs)
        p = d.reembed("plan")
        self.assertTrue(p["ok"] and p["ready"])
        self.assertEqual(p["memories"], 10)
        self.assertEqual(p["identity_excluded"], 1)
        self.assertEqual(p["with_identity"]["memories"], 11)
        self.assertTrue(p["remote"])
        self.assertEqual(p["host"], "127.0.0.1:%d" % self.stub.port)
        with d.engram.lock:
            chars = d.engram.cortex.db.execute(
                "SELECT SUM(LENGTH(content)) FROM memories WHERE kind!='identity'").fetchone()[0]
        self.assertEqual(p["chars"], chars)
        self.assertEqual(len(self.stub.inputs), before)
        self.assertEqual(d.reembed("status")["reembed"]["state"], "idle")

    def test_start_without_confirm_is_refused_and_sends_nothing(self):
        d = self.remote(self.hashed_store(5))
        before = len(self.stub.inputs)
        r = d.reembed("start")
        self.assertFalse(r["ok"])
        self.assertIn("confirmation", r["error"])
        time.sleep(0.2)
        self.assertEqual(len(self.stub.inputs), before)
        self.assertEqual({e for e, _ in d.ids()}, {HASH_ID})

    def test_not_ready_embedder_refuses(self):
        self.stub.key = "other"
        d = RDaemon(remote_env(self.stub))
        self.daemons.append(d)
        r = d.reembed("start", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("not ready", r["error"])
        self.assertFalse(d.reembed("plan")["ready"])

    def test_bad_requests(self):
        d = self.remote(self.hashed_store(1, identity=False))
        for p in ({}, {"action": "nope"}, {"action": 5}, {"action": "start", "batch": 0},
                  {"action": "start", "batch": 9999}, {"action": "start", "confirm": "yes"}):
            r = d.call("reembed", p)
            self.assertIs(r["ok"], False, p)


class Migration(Base):
    def test_full_run_rewrites_only_vector_and_embedder_id(self):
        home = self.hashed_store(14)
        d = self.remote(home)
        before = self.snapshot(d)
        st = self.run_to_end(d, batch=4)
        self.assertEqual(st["done"], 14)
        self.assertEqual(st["total"], 14)
        self.assertEqual(self.snapshot(d), before)             # nothing but vector/embedder_id
        rows = d.rows()
        ident = [r for r in rows if r["kind"] == "identity"]
        rest = [r for r in rows if r["kind"] != "identity"]
        self.assertEqual({(r["embedder_id"], r["dim"]) for r in rest}, {(OPENAI_ID, 16)})
        self.assertEqual([r["embedder_id"] for r in ident], [HASH_ID])
        self.assertNotIn("Quillfeather", " ".join(self.stub.inputs))      # identity never sent
        sizes = [len(json.loads(c[2])["input"]) for c in self.stub.embedding_calls()[1:]]
        self.assertTrue(all(s <= 4 for s in sizes), sizes)
        # migrated rows are now reachable by the vector channel
        res = d.call("recall", {"cue": "component protocol port"})
        self.assertTrue(res["results"])

    def test_include_identity_sends_identity_rows(self):
        d = self.remote(self.hashed_store(3))
        self.run_to_end(d, include_identity=True)
        self.assertEqual({e for e, _ in d.ids()}, {OPENAI_ID})
        self.assertIn("Quillfeather", " ".join(self.stub.inputs))

    def test_idempotent_second_run_does_nothing(self):
        d = self.remote(self.hashed_store(6, identity=False))
        self.run_to_end(d)
        calls = len(self.stub.embedding_calls())
        self.assertEqual(d.reembed("plan")["memories"], 0)
        st = self.run_to_end(d)
        self.assertEqual(st["done"], 0)
        self.assertEqual(len(self.stub.embedding_calls()), calls)

    def test_secrets_in_stored_text_never_reach_the_server_on_reembed(self):
        home = self.hashed_store(2, identity=False)
        d = RDaemon({}, home=home)
        self.daemons.append(d)
        with d.engram.lock:     # simulate an OLD row that predates redaction
            d.engram.cortex.db.execute("UPDATE memories SET content=? WHERE rowid=1", (SECRET_TEXT,))
            d.engram.cortex.db.commit()
        d.stop()
        d = self.remote(home)
        self.run_to_end(d)
        wire = self.stub.all_bytes().decode("utf-8", "replace")
        for s in SECRETS:
            self.assertNotIn(s, wire)
        self.assertTrue(any("[REDACTED" in t for t in self.stub.inputs))

    def test_dimension_mix_coexists_and_a_new_model_migrates_again(self):
        home = self.hashed_store(6, identity=False)
        d = self.remote(home)
        self.run_to_end(d)
        d.stop()
        stub8 = Stub(dim=8)
        self.addCleanup(stub8.stop)
        d = RDaemon(remote_env(stub8), home=home)
        self.daemons.append(d)
        seed(d, 2, prefix="Newer")
        self.assertEqual({(e, n) for e, n in d.ids()}, {(OPENAI_ID, 16), ("openai:%s:8" % MODEL, 8)})
        self.assertTrue(d.call("recall", {"cue": "component protocol port"})["results"])   # lexical
        self.assertEqual(d.reembed("plan")["memories"], 6)
        self.run_to_end(d)
        self.assertEqual({(e, n) for e, n in d.ids()}, {("openai:%s:8" % MODEL, 8)})

    def test_reembed_back_to_hashed_needs_no_confirmation_and_no_network(self):
        home = self.hashed_store(5, identity=False)
        d = self.remote(home)
        self.run_to_end(d)
        d.stop()
        n = self.stub.count()
        d = RDaemon({}, home=home)
        self.daemons.append(d)
        p = d.reembed("plan")
        self.assertFalse(p["remote"])
        r = d.reembed("start")           # no confirm: the target is local
        self.assertTrue(r["ok"], r)
        self.assertTrue(d.wait_state(("done",)))
        self.assertEqual({e for e, _ in d.ids()}, {HASH_ID})
        self.assertEqual(self.stub.count(), n)

    def test_stale_write_does_not_overwrite_a_concurrently_changed_row(self):
        d = self.remote(self.hashed_store(2, identity=False))
        rows = d.rows()
        stale = [(r["rid"], r["id"], r["content"], r["embedder_id"]) for r in rows]
        with d.engram.lock:
            d.engram.cortex.db.execute("UPDATE memories SET content='edited meanwhile' WHERE id=?",
                                       (rows[0]["id"],))
            d.engram.cortex.db.commit()
        d.engram.reembed._write(stale, [b"\x00" * 64] * 2, OPENAI_ID)
        after = {r["id"]: r for r in d.rows()}
        self.assertEqual(after[rows[0]["id"]]["embedder_id"], HASH_ID)
        self.assertEqual(after[rows[1]["id"]]["embedder_id"], OPENAI_ID)


class Progress(Base):
    def test_failures_pause_with_reason_then_resume_from_the_cursor(self):
        d = self.remote(self.hashed_store(12, identity=False))
        self.stub.fail_after = 1 + 2          # the probe + two good batches, then 500s
        self.stub.after_mode = "500"
        d.reembed("start", confirm=True, batch=3)
        self.assertTrue(d.wait_state(("paused",)), d.reembed("status"))
        st = d.reembed("status")["reembed"]
        self.assertEqual(st["done"], 6)
        self.assertGreater(st["cursor"], 0)
        self.assertIn("http-5xx", st["last_error"])
        self.assertNotIn(KEY, json.dumps(st))
        with d.engram.lock:
            persisted = json.loads(d.engram.cortex.meta_get("reembed"))
        self.assertEqual((persisted["state"], persisted["done"]), ("paused", 6))
        self.assertTrue(d.status()["reembed"]["state"] == "paused")
        # heal the server and the breaker; the run resumes where it stopped
        self.stub.fail_after = None
        with vectorize._lock:
            vectorize._open_until = time.monotonic() - 1    # cooldown over: next call probes
            vectorize._failures = 0
        sent = len(self.stub.inputs)
        d.reembed("start", confirm=True, batch=3)
        self.assertTrue(d.wait_state(("done",)), d.reembed("status"))
        st = d.reembed("status")["reembed"]
        self.assertEqual(st["done"], 12)
        self.assertLessEqual(len(self.stub.inputs) - sent, 6 + 3)       # not the first 6 again
        self.assertEqual({e for e, _ in d.ids()}, {OPENAI_ID})

    def test_concurrent_writes_and_low_latency_during_a_run(self):
        d = self.remote(self.hashed_store(40, identity=False))
        self.stub.delay = 0.1
        d.reembed("start", confirm=True, batch=4)
        worst = 0.0
        for i in range(6):
            for verb, p in (("status", {}), ("ping", {}), ("recall", {"cue": "component protocol"}),
                            ("remember", {"content": "Written during the run number %d about topic-%d" % (i, i * 13)})):
                r = d.call(verb, p)
                self.assertTrue(r.get("ok"), (verb, r))
                if verb in ("status", "ping"):
                    worst = max(worst, d.last_s)
                if verb == "recall":
                    self.assertLess(d.last_s, 0.5 + 0.8)
            time.sleep(0.05)
        self.assertLess(worst, 0.5)
        self.assertTrue(d.wait_state(("done",), 40), d.reembed("status"))
        self.assertEqual({e for e, _ in d.ids()}, {OPENAI_ID})
        self.assertEqual(len(d.rows()), 46)

    def test_cancel_stops_the_run_and_leaves_the_rest_lexical_only(self):
        d = self.remote(self.hashed_store(30, identity=False))
        self.stub.delay = 0.15
        d.reembed("start", confirm=True, batch=2)
        self.assertTrue(wait_for(lambda: d.reembed("status")["reembed"]["done"] > 0, 10))
        r = d.reembed("cancel")
        self.assertEqual(r["reembed"]["state"], "cancelled")
        done = r["reembed"]["done"]
        time.sleep(0.5)
        self.assertEqual(d.reembed("status")["reembed"]["done"], done)
        self.assertFalse(d.engram.reembed.status()["active"])
        kinds = {e for e, _ in d.ids()}
        self.assertEqual(kinds, {HASH_ID, OPENAI_ID})
        self.assertTrue(d.call("recall", {"cue": "component protocol"})["results"])

    def test_only_one_run_at_a_time(self):
        d = self.remote(self.hashed_store(20, identity=False))
        self.stub.delay = 0.2
        self.assertTrue(d.reembed("start", confirm=True, batch=2)["ok"])
        r = d.reembed("start", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("already", r["error"])
        d.reembed("cancel")

    def test_status_shows_progress_rate_and_eta(self):
        d = self.remote(self.hashed_store(24, identity=False))
        self.stub.delay = 0.1
        d.reembed("start", confirm=True, batch=2)
        self.assertTrue(wait_for(lambda: d.status()["reembed"].get("rate_per_s"), 10))
        st = d.status()["reembed"]
        self.assertEqual(st["total"], 24)
        self.assertGreater(st["rate_per_s"], 0)
        self.assertIsNotNone(st["eta_s"])
        d.reembed("cancel")


class KillNineResume(unittest.TestCase):
    def test_kill_minus_nine_mid_run_resumes_on_restart(self):
        stub = Stub()
        self.addCleanup(stub.stop)
        home = make_home()
        self.addCleanup(shutil.rmtree, home, True)
        cenv = {"ENGRAM_HOME": home}
        procs = []
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        p = spawn(home)
        procs.append(p)
        self.assertTrue(wait_up(home))
        for i in range(40):
            self.assertTrue(client.call("remember", {"content": "Kill test %d: part-%d uses lane-%d" % (
                i, i * 5 + 1, i * 3 + 2)}, timeout=5, env=cenv)["ok"])
        p.terminate()
        p.communicate(timeout=15)
        env = remote_env(stub, ENGRAM_EMBEDDER_TIMEOUT="3")
        stub.delay = 0.15
        p = spawn(home, env)
        procs.append(p)
        self.assertTrue(wait_up(home))
        self.assertTrue(wait_for(lambda: client.call("status", timeout=5, env=cenv).get("embedder_state") == "ready", 15))
        r = client.call("reembed", {"action": "start", "confirm": True, "batch": 4}, timeout=5, env=cenv)
        self.assertTrue(r["ok"], r)
        self.assertTrue(wait_for(lambda: client.call("reembed", {"action": "status"}, timeout=5, env=cenv)
                                 ["reembed"]["done"] >= 8, 20))
        os.kill(p.pid, signal.SIGKILL)
        p.communicate(timeout=15)
        mid = dict(db_rows(home))
        self.assertTrue(0 < mid.get(OPENAI_ID, 0) < 40, mid)
        sent_before = len(stub.inputs)
        stub.delay = 0.0
        p = spawn(home, env)
        procs.append(p)
        self.assertTrue(wait_up(home))
        self.assertTrue(wait_for(lambda: client.call("reembed", {"action": "status"}, timeout=5, env=cenv)
                                 ["reembed"]["state"] == "done", 30),
                        client.call("reembed", {"action": "status"}, timeout=5, env=cenv))
        self.assertEqual(dict(db_rows(home)), {OPENAI_ID: 40})
        resent = len(stub.inputs) - sent_before
        self.assertLessEqual(resent, 40 - mid[OPENAI_ID] + 4 + 1)      # at most the rest (+ probe/batch)
        p.terminate()
        p.communicate(timeout=15)


class CliTests(Base):
    def setUp(self):
        super().setUp()
        self.d = self.remote(self.hashed_store(8))
        self.env = {"ENGRAM_EMBEDDER": "x"}    # the CLI itself never reads embedder config

    def cli(self, *args, **kw):
        return run(self.d.home, *args, **kw)

    def test_dry_run_prints_counts_and_host_and_sends_nothing(self):
        n = len(self.stub.inputs)
        r = self.cli("reembed", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("8 memories", r.stdout)
        self.assertIn("127.0.0.1:%d" % self.stub.port, r.stdout)
        self.assertIn("identity memories skipped: 1", r.stdout)
        self.assertEqual(len(self.stub.inputs), n)
        j = json.loads(self.cli("--json", "reembed", "--dry-run").stdout)
        self.assertEqual(j["memories"], 8)

    def test_without_yes_a_non_interactive_run_refuses_with_exit_zero(self):
        n = len(self.stub.inputs)
        r = self.cli("reembed", stdin="")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--yes", r.stderr)
        self.assertEqual(self.cli("--strict", "reembed", stdin="").returncode, 4)
        self.assertEqual(len(self.stub.inputs), n)
        self.assertEqual(self.d.reembed("status")["reembed"]["state"], "idle")

    def test_yes_runs_and_status_and_cancel_work(self):
        r = self.cli("reembed", "--yes", "--batch", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started", r.stdout)
        self.assertTrue(self.d.wait_state(("done",)))
        s = self.cli("reembed", "--status")
        self.assertIn("done", s.stdout)
        self.assertIn("8/8", s.stdout)
        st = self.cli("status")
        self.assertIn("memory text is sent to 127.0.0.1:%d for embedding" % self.stub.port, st.stdout)
        self.assertNotIn(KEY, st.stdout + st.stderr + s.stdout + s.stderr)
        self.assertEqual(self.cli("reembed", "--cancel").returncode, 0)
        again = self.cli("reembed", "--yes")
        self.assertIn("nothing to re-embed", again.stdout)

    def test_include_identity_flag(self):
        self.cli("reembed", "--yes", "--include-identity")
        self.assertTrue(self.d.wait_state(("done",)))
        self.assertEqual({e for e, _ in self.d.ids()}, {OPENAI_ID})

    def test_conflicting_flags_are_a_usage_error(self):
        self.assertEqual(self.cli("reembed", "--dry-run", "--yes").returncode, 2)
        self.assertEqual(self.cli("reembed", "--status", "--cancel").returncode, 2)

    def test_offline_is_fail_soft(self):
        r = run("/nonexistent-engram-home", "reembed", "--yes")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
