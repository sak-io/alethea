"""encode() gate, observe(), redaction at ingestion, adversarial end-to-end (R13, R14, G16)."""
import unittest
from unittest import mock

from alethea import encode as enc
from alethea import vectorize
from alethea.cortex import Cortex
from tests.data.dedup_pairs import MERGE, PAIRS
from tests.data.secrets import POSITIVE

AWS = next(t for k, t, s in POSITIVE if k == "aws_key")
AWS_SECRET = "IOSFODNN7EXAMPLE"
STRONG = "We decided we must never deploy the billing service on fridays because it broke prod"


def raw_dump(cx):
    """Every value in every table (incl. FTS shadow tables): journal, edges, meta."""
    parts = []
    for (name,) in cx.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        for row in cx.db.execute('SELECT * FROM "%s"' % name).fetchall():
            parts.extend(bytes(v).decode("latin-1") if isinstance(v, (bytes, bytearray)) else str(v)
                         for v in tuple(row))
    return "\n".join(parts)


class WriteScalingTests(unittest.TestCase):
    """The dedup path bounds its FTS channel to the newest DEDUP_FTS_WINDOW rows."""

    def setUp(self):
        self.cx = Cortex(":memory:")
        self.addCleanup(self.cx.close)

    def test_dedup_passes_the_window_to_fts(self):
        enc.remember(self.cx, "We decided to use PostgreSQL for the billing service")
        calls = []
        orig = self.cx.search_fts

        def spy(q, limit=20, recent=None):
            calls.append(recent)
            return orig(q, limit=limit, recent=recent)
        with mock.patch.object(self.cx, "search_fts", spy):
            enc.remember(self.cx, "We decided to use Redis for the cache service")
        self.assertEqual(calls, [enc.DEDUP_FTS_WINDOW])

    def test_window_does_not_change_dedup_while_store_is_smaller(self):
        first = enc.remember(self.cx, "We decided to use PostgreSQL for the billing service")
        for i in range(25):
            enc.remember(self.cx, "We decided to use tool%d for the service number %d" % (i, i * 7))
        again = enc.remember(self.cx, "We decided to use PostgreSQL for the billing service")
        self.assertEqual((again["status"], again["id"]), ("reinforced", first["id"]))
        with mock.patch.object(enc, "DEDUP_FTS_WINDOW", 5):  # only newest 5 rows via FTS
            n = enc.remember(self.cx, "We decided to use Kafka for the ingest pipeline")
        self.assertEqual(n["status"], "created")


class GateTests(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.cx = Cortex(":memory:", clock=lambda: self.t[0])
        self.addCleanup(self.cx.close)

    def count(self):
        return self.cx.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def test_trivial_rejected(self):
        for t in ("ok", "Thanks!", "lol", "sounds good."):
            self.assertEqual(enc.encode(self.cx, t)["status"], "rejected", t)
        self.assertEqual(self.count(), 0)

    def test_trivial_allowed_with_force_or_deliberate(self):
        self.assertEqual(enc.encode(self.cx, "ok", force=True)["status"], "created")
        self.assertEqual(enc.encode(self.cx, "thanks", deliberate=True)["status"], "created")

    def test_low_salience_rejected_unless_forced(self):
        text = "the cat sat on the warm windowsill today"
        self.assertEqual(enc.encode(self.cx, text)["reason"], "low_salience")
        self.assertEqual(enc.encode(self.cx, text, force=True)["status"], "created")

    def test_threshold_value(self):
        self.assertEqual(enc.ENCODE_THRESHOLD, 0.34)

    def test_strong_content_created_with_effective_formula(self):
        r = enc.encode(self.cx, STRONG)
        self.assertEqual(r["status"], "created")
        m = self.cx.get(r["id"])
        self.assertAlmostEqual(m["salience"], enc.amygdala(STRONG), places=6)  # surprise 1
        self.assertEqual(m["embedder_id"], vectorize.EMBEDDER_ID)

    def test_explicit_salience_floor(self):
        r = enc.encode(self.cx, "x marks the spot for the treasure chest", salience=0.8, force=True)
        self.assertGreaterEqual(self.cx.get(r["id"])["salience"], 0.8 * 0.9 - 1e-9)

    def test_identity_floor(self):
        r = enc.encode(self.cx, "I am the lead of the platform team here")
        self.assertEqual(r["kind"], "identity")
        self.assertGreaterEqual(self.cx.get(r["id"])["salience"], 0.62)

    def test_deliberate_floor(self):
        r = enc.remember(self.cx, "the cat sat on the warm windowsill today")
        self.assertGreaterEqual(self.cx.get(r["id"])["salience"], 0.55)

    def test_restatement_reinforces_and_writes_nothing(self):
        a = enc.encode(self.cx, STRONG)
        before = self.cx.get(a["id"])
        r = enc.encode(self.cx, STRONG.lower() + ".")
        self.assertEqual(r["status"], "reinforced")
        self.assertEqual(r["id"], a["id"])
        self.assertEqual(self.count(), 1)
        after = self.cx.get(a["id"])
        self.assertAlmostEqual(after["strength"] - before["strength"], 0.5)

    def test_other_embedder_never_deduped(self):
        a = enc.encode(self.cx, STRONG, embedder_id="other:1")
        r = enc.encode(self.cx, STRONG)
        self.assertEqual(r["status"], "created")
        self.assertNotEqual(a["id"], r["id"])

    def test_recall_candidates_imported_lazily(self):
        with mock.patch("alethea.recall.candidates", return_value=[]) as c:
            enc.encode(self.cx, STRONG)
        c.assert_called_once()
        self.assertNotIn("recall", vars(enc))

    def test_reflect_links_abstracts_edges(self):
        a = enc.encode(self.cx, STRONG)["id"]
        b = enc.remember(self.cx, "billing deploys are risky on weekends")["id"]
        r = enc.reflect(self.cx, "pattern: avoid risky weekend rollouts everywhere", [a, b])
        self.assertEqual(r["status"], "created")
        for cid in (a, b):
            self.assertIsNotNone(self.cx.edge_weight(r["id"], cid, "abstracts"))
        self.assertGreaterEqual(self.cx.get(r["id"])["salience"], 0.55)

    def test_forget_suppresses_and_stamps(self):
        a = enc.encode(self.cx, STRONG)["id"]
        self.t[0] = 5000.0
        self.assertTrue(enc.forget(self.cx, a))
        m = self.cx.get(a)
        self.assertEqual(m["status"], "suppressed")
        self.assertEqual(m["meta"]["suppressed_at"], 5000.0)
        self.assertFalse(enc.forget(self.cx, "nope"))

    def test_observe_always_journals_and_promotes_durable(self):
        r = enc.observe(self.cx, STRONG, session_id="s")
        self.assertIsNotNone(r["journal_id"])
        self.assertEqual(r["promoted"]["status"], "created")
        r = enc.observe(self.cx, "ok thanks", session_id="s")
        self.assertIsNotNone(r["journal_id"])
        self.assertIsNone(r["promoted"])
        self.assertEqual(len(self.cx.journal_pending()), 2)
        self.assertEqual(self.count(), 1)

    def test_adversarial_pairs_create_two_memories(self):
        for cat, a, b in PAIRS:
            if cat in (MERGE, "unrelated"):
                continue
            with self.subTest(cat=cat, a=a, b=b):
                cx = Cortex(":memory:")
                self.addCleanup(cx.close)
                ra = enc.encode(cx, a, force=True)
                rb = enc.encode(cx, b, force=True)
                self.assertEqual((ra["status"], rb["status"]), ("created", "created"))
                self.assertNotEqual(ra["id"], rb["id"])
                self.assertEqual(cx.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2)

    def test_true_restatements_reinforce(self):
        for cat, a, b in PAIRS:
            if cat != MERGE:
                continue
            with self.subTest(a=a):
                cx = Cortex(":memory:")
                self.addCleanup(cx.close)
                ra = enc.encode(cx, a, force=True)
                rb = enc.encode(cx, b, force=True)
                self.assertEqual(rb["status"], "reinforced")
                self.assertEqual(rb["id"], ra["id"])
                self.assertEqual(cx.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)

    def test_threshold_boundary(self):
        with mock.patch.object(enc, "amygdala", return_value=0.339):
            self.assertEqual(enc.encode(self.cx, "alpha beta gamma delta")["status"], "rejected")
        with mock.patch.object(enc, "amygdala", return_value=0.34):
            self.assertEqual(enc.encode(self.cx, "epsilon zeta eta theta")["status"], "created")

    def test_effective_uses_surprise(self):
        with mock.patch.object(enc, "amygdala", return_value=0.8):
            a = enc.encode(self.cx, "alpha beta gamma delta epsilon")
            self.assertAlmostEqual(self.cx.get(a["id"])["salience"], 0.8)  # surprise 1
            with mock.patch.object(enc, "similarity", return_value=0.5):
                b = enc.encode(self.cx, "zeta eta theta iota kappa")
        self.assertAlmostEqual(self.cx.get(b["id"])["salience"], 0.8 * (0.45 + 0.55 * 0.5))

    def test_explicit_salience_floor_exact(self):
        with mock.patch.object(enc, "similarity", return_value=0.0):
            enc.encode(self.cx, "first unrelated seed words here", force=True)
        with mock.patch.object(enc, "similarity", return_value=0.8):  # surprise 0.2
            r = enc.encode(self.cx, "second distinct words go here", salience=0.6, force=True)
        self.assertAlmostEqual(self.cx.get(r["id"])["salience"], 0.6 * 0.9)

    def test_identity_floor_exact(self):
        with mock.patch.object(enc, "amygdala", return_value=0.1):
            r = enc.encode(self.cx, "I am the owner of the ledger")
        self.assertAlmostEqual(self.cx.get(r["id"])["salience"], 0.62)

    def test_deliberate_floor_exact(self):
        r = enc.remember(self.cx, "quiet plain words about nothing")
        self.assertAlmostEqual(self.cx.get(r["id"])["salience"], 0.55)

    def test_vectors_of_other_embedder_not_compared(self):
        self.cx.insert("semantic", STRONG, vector=vectorize.embed(STRONG), embedder_id="other:1")
        with mock.patch.object(enc, "similarity", side_effect=AssertionError("compared")):
            r = enc.encode(self.cx, STRONG)
        self.assertEqual(r["status"], "created")

    def test_input_capped_before_redact_and_journal(self):
        import time
        big = ("x" * 99 + " ") * 100000  # ~10 MB
        t0 = time.time()
        r = enc.observe(self.cx, big, session_id="s")
        self.assertLess(time.time() - t0, 1.0)
        stored = self.cx.journal_pending()[0]["content"]
        self.assertLessEqual(len(stored.encode()), enc.MAX_TURN_BYTES + len(enc.TRUNCATION_MARKER) + 64)
        self.assertTrue(stored.endswith(enc.TRUNCATION_MARKER))
        self.assertIsNotNone(r["journal_id"])

    def test_cap_configurable_by_env(self):
        with mock.patch.dict("os.environ", {"ALETHEA_MAX_TURN_BYTES": "2048"}):
            enc.observe(self.cx, "word " * 2000)
        self.assertLess(len(self.cx.journal_pending()[0]["content"]), 2048 + 64)


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.cx = Cortex(":memory:")
        self.addCleanup(self.cx.close)

    def assert_clean(self):
        self.assertNotIn(AWS_SECRET, raw_dump(self.cx))
        if self.cx.fts_available:
            self.assertEqual(self.cx.db.execute(
                "SELECT COUNT(*) FROM mem_fts WHERE mem_fts MATCH ?", ('"%s"' % AWS_SECRET,)
            ).fetchone()[0], 0)

    def test_journaled_turn_never_stores_secret(self):
        r = enc.observe(self.cx, "We decided we must never leak: " + AWS, session_id="s")
        self.assertIsNotNone(r["journal_id"])
        self.assertEqual(r["promoted"]["status"], "created")
        self.assertIn("[REDACTED:", self.cx.journal_pending()[0]["content"])
        self.assert_clean()

    def test_journal_only_turn_redacted(self):
        enc.observe(self.cx, "ok " + AWS)
        self.assert_clean()

    def test_remember_reflect_and_meta_redacted(self):
        a = enc.remember(self.cx, "the build key is " + AWS, meta={"note": AWS})
        enc.reflect(self.cx, "lesson: rotate " + AWS, [a["id"]], meta={"n": [AWS]})
        enc.encode(self.cx, "force " + AWS, force=True)
        self.assert_clean()
        m = self.cx.get(a["id"])
        for f in ("content", "gist", "keywords"):
            self.assertNotIn(AWS_SECRET, m[f])

    def test_metadata_strings_redacted(self):
        enc.observe(self.cx, "We decided we must never leak keys here", session_id=AWS,
                    role=AWS, cwd="/x/" + AWS, repo_root="/y/" + AWS)
        enc.remember(self.cx, "plain deliberate note", session_id=AWS, cwd=AWS, repo_root=AWS)
        self.assert_clean()

    def test_all_fixture_secrets_never_stored(self):
        for kind, text, secret in POSITIVE:
            with self.subTest(kind=kind):
                cx = Cortex(":memory:")
                self.addCleanup(cx.close)
                enc.remember(cx, text)
                enc.observe(cx, text)
                self.assertNotIn(secret, raw_dump(cx))


if __name__ == "__main__":
    unittest.main()
