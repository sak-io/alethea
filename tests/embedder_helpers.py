"""Helpers: activate a shipped/fake embedder for one test, with guaranteed cleanup."""
import contextlib
from unittest import mock

from alethea import embedders_ext, vectorize
from tests import fake_dense

# Name -> how to select it. The fastembed class is real; only its model call is stubbed.
EMBEDDER_NAMES = ["hash", "fake-dense-384", "fake-dense-1536", "fastembed-mocked"]
MINILM = "fastembed:sentence-transformers/all-MiniLM-L6-v2"


class StubTextEmbedding:
    """Stands in for fastembed.TextEmbedding: .embed(list) -> iterator of float vectors."""
    calls = 0

    def __init__(self, model_name):
        self.model_name = model_name

    def embed(self, texts):
        StubTextEmbedding.calls += len(texts)
        for t in texts:
            yield fake_dense.fake_vector(t, 384)


@contextlib.contextmanager
def activated(name):
    """Configure vectorize for `name`; yield the embedder id that writes will carry."""
    vectorize._reset_for_tests()
    patches = []
    try:
        if name == "hash":
            vectorize.configure("hash")
        elif name.startswith("fake-dense-"):
            vectorize.register_scheme("fake", fake_dense.make_fake)
            vectorize.configure("fake:%s" % name.rsplit("-", 1)[1])
        elif name == "fastembed-mocked":
            p = mock.patch.object(embedders_ext, "_import_text_embedding",
                                  return_value=StubTextEmbedding)
            p.start()
            patches.append(p)
            vectorize.configure(MINILM)
        else:
            raise ValueError(name)
        eid, _ = vectorize.embed_batch(["warm up"])
        yield eid
    finally:
        for p in patches:
            p.stop()
        vectorize._reset_for_tests()
