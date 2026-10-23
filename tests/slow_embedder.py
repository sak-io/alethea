"""A controllable test embedder: slow load, slow embed, hang, raise, count calls (T25 review)."""
import threading
import time

from engram import vectorize
from tests import fake_dense


class Control:
    """Knobs shared with the embedder instances a daemon under test loads."""

    def __init__(self, **kw):
        self.load_delay = 0.0
        self.embed_delay = 0.0
        self.hang = False           # block in embed() until `release` is set
        self.hang_probe = False     # hang only on the breaker's probe call
        self.raises = False
        self.load_raises = False
        self.calls = 0
        self.texts = []
        self.lock_probe = None      # callable(); returns True if the engine lock was free
        self.lock_violations = 0
        self.release = threading.Event()
        self.__dict__.update(kw)
        self._mu = threading.Lock()

    def stop(self):
        self.release.set()


class ControlledEmbedder(vectorize.Embedder):
    dim = 384
    id = "slow-test:384"

    def __init__(self, ctl):
        self.ctl = ctl

    def embed_many(self, texts):
        c = self.ctl
        with c._mu:
            c.calls += 1
            c.texts.extend(texts)
        if c.lock_probe is not None and not c.lock_probe():
            c.lock_violations += 1
            c.violating = list(texts)
        if c.hang or (c.hang_probe and any("embedder probe" in t for t in texts)):
            c.release.wait()
        if c.embed_delay:
            time.sleep(c.embed_delay)
        if c.raises:
            raise RuntimeError("controlled failure")
        return [fake_dense.fake_vector(t, self.dim) for t in texts]

    def embed(self, text):
        return self.embed_many([text])[0]


def register(ctl):
    """Register scheme `slow` (ENGRAM_EMBEDDER=slow:x) bound to `ctl`."""
    def factory(arg):
        if ctl.load_delay:
            time.sleep(ctl.load_delay)
        if ctl.load_raises:
            raise OSError("controlled load failure")
        return ControlledEmbedder(ctl)
    vectorize.register_scheme("slow", factory)
