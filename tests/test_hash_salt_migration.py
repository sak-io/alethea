"""Hash salt rename and the embedder id that versions it (ADR 0014).

Stored vectors are keyed by `embedder_id`. Changing the salt changes every hashed vector, so the id
changed with it (`hashed-ngram-v1:512` -> `hashed-ngram-v2:512`): rows written by the old build
are a different embedder and must never be compared with current vectors.
Verify: `python3 -m unittest tests.test_hash_salt_migration -v`.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alethea import config, consolidate, encode, recall, vectorize
from alethea.cortex import Cortex
from tests.remote_helpers import RDaemon

OLD_ID = "hashed-ngram-v1:512"
TEXT = "We use Postgres for the billing database"
# sha256 of vectorize.embed(TEXT). If this fails you changed the salt, the features, their
# weights or the dimension: bump vectorize.EMBEDDER_ID (and retire the old id) in the same change,
# then update this digest.
GOLDEN = "4bde7f69fc8a198d953e0d30a8ff782e427a76cbaccca84d69bd0e78cca47ab1"


class SaltAndIdTest(unittest.TestCase):
    def test_salt_and_id(self):
        self.assertEqual(vectorize.SALT, b"alethea-v1")
        self.assertEqual(config.HASH_SALT, vectorize.SALT)
        self.assertEqual(vectorize.EMBEDDER_ID, "hashed-ngram-v2:512")
        self.assertIn(OLD_ID, vectorize.RETIRED_HASHED_IDS)
        self.assertNotIn(vectorize.EMBEDDER_ID, vectorize.RETIRED_HASHED_IDS)

    def test_vector_algorithm_is_pinned_to_the_embedder_id(self):
        self.assertEqual(hashlib.sha256(vectorize.embed(TEXT)).hexdigest(), GOLDEN,
                         "vector algorithm changed: bump vectorize.EMBEDDER_ID")

    def test_another_salt_gives_different_vectors(self):
        """Why the id must change with the salt."""
        before = vectorize.embed_floats(TEXT)
        try:
            with mock.patch.object(vectorize, "SALT", b"another-salt"):
                vectorize._bucket.cache_clear()
                other = vectorize.embed_floats(TEXT)
        finally:
            vectorize._bucket.cache_clear()
        self.assertLess(vectorize.cosine(before, other), 0.5)


class RetiredIdTest(unittest.TestCase):
    CUE = "zebra quartz harmonica"           # shares no word with MEM, so only a vector can match
    MEM = "The invoice archive is rotated nightly by the batch runner"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cx = Cortex(Path(self.tmp.name) / "m.db")
        self.addCleanup(self.cx.close)

    def test_embed_batch_never_labels_current_vectors_with_a_retired_id(self):
        eid, (v,) = vectorize.embed_batch(["hi"], OLD_ID)
        self.assertEqual((eid, v), (vectorize.EMBEDDER_ID, vectorize.embed("hi")))

    def test_recall_never_compares_a_retired_id_row_with_a_current_cue(self):
        # The stored vector is the cue's own current vector: cosine 1.0 IF it were compared.
        cue_vec = vectorize.embed(self.CUE)
        old = self.cx.insert("semantic", self.MEM, vector=cue_vec, embedder_id=OLD_ID)
        self.assertEqual(recall.recall(self.cx, self.CUE, reinforce=False), [])
        # control: the same row under the current id IS found by its vector, so the test can fail
        self.cx.delete(old)
        new = self.cx.insert("semantic", self.MEM, vector=cue_vec, embedder_id=vectorize.EMBEDDER_ID)
        self.assertEqual([m["id"] for m in recall.recall(self.cx, self.CUE, reinforce=False)], [new])

    def test_remember_does_not_merge_into_a_retired_id_row(self):
        text = "deploys to production need a second reviewer on call"
        self.cx.insert("semantic", text, vector=vectorize.embed(text), embedder_id=OLD_ID)
        r = encode.remember(self.cx, text)
        self.assertEqual(r["status"], "created")   # identical text, but another embedder: no merge
        ids = sorted(x[0] for x in self.cx.db.execute("SELECT embedder_id FROM memories"))
        self.assertEqual(ids, [OLD_ID, vectorize.EMBEDDER_ID])

    def test_recombine_skips_pairs_across_the_retired_and_the_current_id(self):
        a = "the billing service stores invoices in postgres and archives old rows nightly"
        b = "the billing service archives invoices in postgres every night using a cron job"
        self.cx.insert("semantic", a, vector=vectorize.embed(a), embedder_id=OLD_ID)
        self.cx.insert("semantic", b, vector=vectorize.embed(b), embedder_id=vectorize.EMBEDDER_ID)
        self.assertEqual(consolidate.recombine(self.cx), 0)


class ReembedMigratesRetiredRowsTest(unittest.TestCase):
    def test_reembed_with_the_hashed_embedder_rewrites_old_id_rows(self):
        d = RDaemon({})
        self.addCleanup(lambda: __import__("shutil").rmtree(d.home, ignore_errors=True))
        self.addCleanup(d.stop)
        texts = ["Decision %d: component-%d speaks protocol-%d over port %d" % (i, i, i + 1, 4000 + i)
                 for i in range(4)]
        for t in texts:
            self.assertTrue(d.call("remember", {"content": t})["ok"])
        stale = b"\x00\x00\x80\x3f" * vectorize.DIM          # a vector from "another algorithm"
        with d.alethea.lock:
            d.alethea.cortex.db.execute("UPDATE memories SET embedder_id=?, vector=?", (OLD_ID, stale))
            d.alethea.cortex.db.commit()
        self.assertEqual({e for e, _ in d.ids()}, {OLD_ID})
        self.assertEqual(d.reembed("plan")["memories"], len(texts))
        self.assertTrue(d.reembed("start")["ok"])          # local target: no confirmation
        self.assertTrue(d.wait_state(("done",)), d.reembed("status"))
        self.assertEqual(d.ids(), [(vectorize.EMBEDDER_ID, vectorize.DIM)])
        with d.alethea.lock:
            rows = d.alethea.cortex.db.execute("SELECT content, vector FROM memories").fetchall()
        self.assertEqual(len(rows), len(texts))
        for r in rows:
            self.assertEqual(bytes(r["vector"]), vectorize.embed(r["content"]))


if __name__ == "__main__":
    unittest.main()
