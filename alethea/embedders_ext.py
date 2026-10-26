"""Optional embedders (T25). Imported by `alethea.vectorize` ONLY when ALETHEA_EMBEDDER selects one.

This module itself is stdlib-only; each embedder imports its third-party package inside its
loader, so selecting `fastembed:<model>` is the only thing that ever imports fastembed (and
through it onnxruntime, numpy, tokenizers, huggingface_hub). Anything that goes wrong here
(missing package, model cannot be downloaded or loaded, bad output) is turned into a hashed
fallback by `vectorize.embed_batch`; nothing in this file may log or echo memory text.

Thresholds are inherited from `vectorize.Embedder` (the conservative hashed values): the
cosine distribution of a real model has not been measured here, so none is tuned (ADR 0010).
"""
from alethea import vectorize

DEFAULT_FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _import_text_embedding():
    from fastembed import TextEmbedding  # noqa: PLC0415  (the one deliberate lazy import)
    return TextEmbedding


class FastEmbedEmbedder(vectorize.Embedder):
    """Local ONNX sentence embedder via the `fastembed` package (on-device; the model files are
    fetched once from the model hub on first use, no memory text ever leaves the machine).

    `model_factory(model_name)` returns an object with `.embed(list_of_str)` yielding one
    vector per text; tests inject a stub, production uses fastembed.TextEmbedding.
    """

    def __init__(self, model_name=DEFAULT_FASTEMBED_MODEL, model_factory=None):
        self.model_name = model_name
        if model_factory is None:
            model_factory = _import_text_embedding()  # ImportError -> 'unavailable'
        self._model = model_factory(model_name)
        probe = self.embed_many(["dimension probe"])[0]
        self.dim = len(probe)
        if self.dim <= 0:
            raise ValueError("model produced an empty vector")
        self.id = "fastembed:%s:%d" % (model_name, self.dim)

    def embed_many(self, texts):
        return [[float(x) for x in v] for v in self._model.embed(list(texts))]

    def embed(self, text):
        return self.embed_many([text])[0]


def make_fastembed(arg):
    """Factory for ALETHEA_EMBEDDER=fastembed:<model>."""
    return FastEmbedEmbedder(arg or DEFAULT_FASTEMBED_MODEL)
