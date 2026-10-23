"""Remote OpenAI-compatible embedder (T26, ADR 0011). Imported ONLY when ENGRAM_EMBEDDER=openai:...

PRIVACY: selecting this embedder sends memory text to a THIRD PARTY (the host in
ENGRAM_EMBEDDER_URL). It is off by default and doubly opt-in: ENGRAM_EMBEDDER=openai:<model>
AND, for any non-loopback host, ENGRAM_EMBEDDER_ALLOW_REMOTE=1. Everything that leaves the
process is redacted HERE (`prepare`, called by `embed_many`), so no caller can bypass it. The API
key comes only from ENGRAM_EMBEDDER_API_KEY and is used only in the Authorization header of the
validated connection. Redirects are never followed. stdlib `http.client` only: no urllib opener,
so no proxy handlers (HTTP(S)_PROXY is ignored), no cookie jar and no redirect handler.

Nothing in this file may log or put into an exception: the key, the URL, a response body or
memory text. Failures are fixed labels (`RemoteError.kind`) and fixed detail strings.
"""
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

from engram import __version__, vectorize
from engram.redact import redact

DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
MAX_ITEM_CHARS = 8000            # per string sent (after redaction)
PRE_CAP_CHARS = 64000            # cost bound before redaction (cut at a word boundary)
MAX_BATCH = 64                   # strings per HTTP request
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DIM = 65536
RETRY_AFTER_CAP_S = 60.0
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_KEY_RE = re.compile(r"^[\x21-\x7e]{1,512}$")     # printable ASCII, no whitespace/control
_ENV = None                                        # env mapping injected by the daemon


class RemoteError(vectorize._Bad):
    """A remote call or its configuration failed. `kind` is a short fixed label and `detail`
    a fixed human string; neither ever contains the key, a URL, a response body or memory text."""

    def __init__(self, kind, detail=None):
        vectorize._Bad.__init__(self, kind)
        self.detail = detail

    def __str__(self):
        return self.kind


def configure_env(env):
    """The daemon hands over its env mapping (tests inject a dict); default is os.environ."""
    global _ENV
    _ENV = env


def _environ():
    return os.environ if _ENV is None else _ENV


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _is_loopback_host(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def valid_model(model):
    return isinstance(model, str) and bool(_MODEL_RE.match(model))


def parse_endpoint(url):
    """(scheme, host, port, base_path) or RemoteError('unavailable', detail). No network."""
    url = (url or "").strip() or DEFAULT_OPENAI_URL
    try:
        u = urlsplit(url)
        port = u.port
        host = u.hostname
    except ValueError:
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_URL is not a valid URL")
    if u.scheme not in ("http", "https") or not host:
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_URL must be an http(s) URL with a host")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_URL must not contain credentials")
    if u.query or u.fragment or any(c.isspace() or ord(c) < 32 for c in url):
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_URL must not contain a query, "
                                         "fragment or whitespace")
    if u.scheme == "http" and not _is_loopback_host(host):
        raise RemoteError("unavailable", "http:// is only allowed for loopback hosts; use https://")
    return u.scheme, host, port, u.path.rstrip("/")


def _display_host(scheme, host, port):
    h = "[%s]" % host if ":" in host else host
    default = 443 if scheme == "https" else 80
    return h if port in (None, default) else "%s:%d" % (h, port)


def remote_status(spec):
    """Status keys for the configured spec. Never raises, never touches the network, and never
    includes the key or the URL path/query."""
    env = _environ()
    out = {"embedder_remote": True, "embedder_remote_host": None,
           "embedder_remote_model": None, "embedder_remote_loopback": None,
           "embedder_remote_acknowledged": _truthy(env.get("ENGRAM_EMBEDDER_ALLOW_REMOTE"))}
    try:
        model = spec.partition(":")[2].strip()
        out["embedder_remote_model"] = model if valid_model(model) else None
        scheme, host, port, _ = parse_endpoint(env.get("ENGRAM_EMBEDDER_URL"))
        out["embedder_remote_host"] = _display_host(scheme, host, port)
        out["embedder_remote_loopback"] = _is_loopback_host(host)
    except Exception:
        pass
    return out


def _cut_clean(text):
    """Redact FIRST (a cut inside a secret would leave a prefix the patterns no longer match),
    then cap. Over-long input is pre-cut at a word boundary to bound the redaction cost."""
    if not isinstance(text, str):
        text = str(text)
    if len(text) > PRE_CAP_CHARS:
        text = text[:PRE_CAP_CHARS]
        cut = max(text.rfind(" "), text.rfind("\n"), text.rfind("\t"))
        if cut > PRE_CAP_CHARS - 512:
            text = text[:cut]
    text = redact(text)
    if len(text) > MAX_ITEM_CHARS:
        text = text[:MAX_ITEM_CHARS]
    return text if text.strip() else "(empty)"


def prepare(texts):
    """What would be sent for `texts`: redacted, capped, never empty. The only path to the wire."""
    return [_cut_clean(t) for t in texts]


def _no_constant(_name):
    raise ValueError("non-finite number")


class OpenAIEmbedder(vectorize.Embedder):
    """OpenAI-compatible `POST <base>/embeddings` client. id = openai:<model>:<dim>."""

    remote = True

    def __init__(self, model, scheme, host, port, base_path, key, timeout_fn=None,
                 ssl_context=None):
        self.model = model
        self.scheme, self.host, self.port, self.base_path = scheme, host, port, base_path
        self._key = key
        self._ctx = ssl_context
        self._timeout_fn = timeout_fn or (lambda: vectorize.EMBED_TIMEOUT)
        self._not_before = 0.0
        self._lock = threading.Lock()
        self.dim = 0
        self.id = ""

    def __repr__(self):
        return "<OpenAIEmbedder %s @ %s>" % (self.model, self.display_host)

    @property
    def display_host(self):
        return _display_host(self.scheme, self.host, self.port)

    # --- the single place memory text leaves the process ---

    def embed_many(self, texts):
        return self._embed(prepare(texts), float(self._timeout_fn()))

    def embed(self, text):
        return self.embed_many([text])[0]

    def probe(self, timeout):
        """Learn the dimension from the first successful response, then fix the id."""
        vec = self._embed(prepare(["dimension probe"]), timeout, learn=True)[0]
        self.id = "openai:%s:%d" % (self.model, len(vec))

    def _embed(self, items, timeout, learn=False):
        deadline = time.monotonic() + max(0.05, timeout)
        out = []
        for i in range(0, len(items), MAX_BATCH):
            out.extend(self._request(items[i:i + MAX_BATCH], deadline, learn))
        return out

    def _connect(self, remaining):
        if self.scheme == "https":
            ctx = self._ctx or ssl.create_default_context()   # verifies chain and hostname
            return http.client.HTTPSConnection(self.host, self.port, timeout=remaining,
                                               context=ctx)
        return http.client.HTTPConnection(self.host, self.port, timeout=remaining)

    def _request(self, chunk, deadline, learn):
        now = time.monotonic()
        with self._lock:
            if now < self._not_before:
                raise RemoteError("rate-limited", "server asked us to wait (Retry-After)")
        if deadline - now <= 0:
            raise RemoteError("timeout", "deadline exceeded")
        body = json.dumps({"model": self.model, "input": chunk,
                           "encoding_format": "float"}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "engram/%s" % __version__, "Accept-Encoding": "identity",
                   "Connection": "close", "Content-Length": str(len(body))}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        conn = None
        try:
            conn = self._connect(max(0.01, deadline - now))
            conn.request("POST", self.base_path + "/embeddings", body, headers)
            self._arm(conn, deadline)
            resp = conn.getresponse()
            self._check_status(resp)
            ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise RemoteError("protocol", "unexpected Content-Type")
            clen = resp.getheader("Content-Length")
            if clen is not None and (not clen.isdigit() or int(clen) > MAX_RESPONSE_BYTES):
                raise RemoteError("too-large", "response too large")
            raw, total = [], 0
            while True:
                self._arm(conn, deadline)
                part = resp.read(65536)
                if not part:
                    break
                total += len(part)
                if total > MAX_RESPONSE_BYTES:
                    raise RemoteError("too-large", "response too large")
                raw.append(part)
        except RemoteError:
            raise
        except socket.timeout:
            raise RemoteError("timeout", "request timed out")
        except ssl.SSLError:
            raise RemoteError("tls", "TLS connection or certificate verification failed")
        except (OSError, http.client.HTTPException):
            raise RemoteError("network", "connection failed")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return self._parse(b"".join(raw), len(chunk), learn)

    @staticmethod
    def _arm(conn, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise RemoteError("timeout", "deadline exceeded")
        if conn.sock is not None:
            conn.sock.settimeout(left)

    def _check_status(self, resp):
        st = resp.status
        if st == 200:
            return
        if 300 <= st < 400:
            raise RemoteError("redirect", "redirects are never followed")
        if st in (401, 403):
            raise RemoteError("auth", "the server rejected the API key (HTTP %d)" % st)
        if st == 429:
            wait = 0.0
            try:
                wait = float((resp.getheader("Retry-After") or "").strip())
            except ValueError:
                pass
            if math.isfinite(wait) and wait > 0:
                with self._lock:
                    self._not_before = time.monotonic() + min(wait, RETRY_AFTER_CAP_S)
            raise RemoteError("rate-limited", "HTTP 429")
        if st >= 500:
            raise RemoteError("http-5xx", "server error (HTTP %d)" % st)
        raise RemoteError("http-%d" % st, "unexpected HTTP status %d" % st)

    def _parse(self, raw, n, learn):
        try:
            doc = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
            data = doc["data"]
            if not isinstance(data, list) or len(data) != n:
                raise RemoteError("protocol", "wrong number of embeddings")
            slots = [None] * n
            for item in data:
                idx = item["index"]
                if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < n \
                        or slots[idx] is not None:
                    raise RemoteError("protocol", "bad embedding index")
                emb = item["embedding"]
                if not isinstance(emb, list) or not emb or len(emb) > MAX_DIM:
                    raise RemoteError("protocol", "bad embedding")
                vec = []
                for x in emb:
                    if isinstance(x, bool) or not isinstance(x, (int, float)):
                        raise RemoteError("protocol", "bad embedding value")
                    f = float(x)
                    if not math.isfinite(f):
                        raise RemoteError("nonfinite", "non-finite embedding value")
                    vec.append(f)
                slots[idx] = vec
        except RemoteError:
            raise
        except Exception:
            raise RemoteError("protocol", "malformed response")
        dims = {len(v) for v in slots}
        if len(dims) != 1:
            raise RemoteError("dimension", "embeddings differ in dimension")
        d = dims.pop()
        if learn:
            self.dim = d
        elif d != self.dim:
            raise RemoteError("dimension", "dimension changed")
        return slots


def make_openai(arg):
    """Factory for ENGRAM_EMBEDDER=openai:<model>. Runs on the background loader thread.
    Raises RemoteError('unavailable', ...) BEFORE any network I/O when consent or configuration
    is missing, so an unacknowledged remote never sees a single byte."""
    env = _environ()
    model = (arg or "").strip()
    if not valid_model(model):
        raise RemoteError("unavailable", "invalid model name")
    scheme, host, port, base = parse_endpoint(env.get("ENGRAM_EMBEDDER_URL"))
    loopback = _is_loopback_host(host)
    if not loopback and not _truthy(env.get("ENGRAM_EMBEDDER_ALLOW_REMOTE")):
        raise RemoteError("unavailable", "remote not acknowledged")
    key = env.get("ENGRAM_EMBEDDER_API_KEY") or ""
    if key and not _KEY_RE.match(key):
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_API_KEY has an invalid format")
    if not key and not loopback:
        raise RemoteError("unavailable", "ENGRAM_EMBEDDER_API_KEY is not set")
    emb = OpenAIEmbedder(model, scheme, host, port, base, key or None)
    emb.probe(max(vectorize.EMBED_TIMEOUT, min(vectorize.LOAD_TIMEOUT, 10.0)))
    return emb
