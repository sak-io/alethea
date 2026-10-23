"""A stdlib stub of an OpenAI-compatible /embeddings server, for the T26 tests.

No real provider endpoint is ever contacted by any test: everything talks to this stub on
127.0.0.1. The stub records EVERYTHING it receives (request line, all headers, body) so tests
can assert that a planted secret never reached it.
"""
import http.server
import json
import os
import ssl
import subprocess
import threading
import time

from tests import fake_dense

KEY = "sk-stubKEY-9f8e7d6c5b4a39281706"


class Stub:
    def __init__(self, key=KEY, dim=16, tls=None, bind="127.0.0.1"):
        self.key, self.dim = key, dim
        self.mode = "ok"
        self.delay = 0.0
        self.retry_after = None
        self.location = None            # redirect target
        self.fail_after = None          # after this many OK embedding calls switch to self.after_mode
        self.after_mode = "500"
        self.after_dim = None
        self.requests = []              # [(requestline, headers_text, body_bytes)]
        self.inputs = []                # every string received in "input"
        self.ok_calls = 0
        self._m = threading.Lock()
        self._down = threading.Event()
        stub = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                stub._handle(self, body)

        self.httpd = http.server.ThreadingHTTPServer((bind, 0), H)
        self.httpd.daemon_threads = True
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*tls)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.port = self.httpd.server_address[1]
        self.scheme = "https" if tls else "http"
        self.url = "%s://127.0.0.1:%d/v1" % (self.scheme, self.port)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True,
                                       name="stub-http")
        self.thread.start()

    # --- recording ---

    def all_bytes(self):
        with self._m:
            return b"\n".join(rl.encode() + b"\n" + h.encode() + b"\n" + b
                              for rl, h, b in self.requests)

    def count(self):
        with self._m:
            return len(self.requests)

    def embedding_calls(self):
        with self._m:
            return [r for r in self.requests if r[0].split(" ")[1].endswith("/embeddings")]

    # --- behaviour ---

    def _send(self, h, status, body, ctype="application/json", headers=None):
        h.send_response(status)
        if ctype:
            h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        try:
            h.wfile.write(body)
        except OSError:
            pass

    def _handle(self, h, body):
        with self._m:
            self.requests.append((h.requestline, str(h.headers), body))
        if self.delay and self._down.wait(self.delay):
            return
        mode = self.mode
        if self.fail_after is not None and self.ok_calls >= self.fail_after:
            mode = self.after_mode
        auth = h.headers.get("Authorization")
        if self.key is not None and auth != "Bearer " + self.key:
            # a hostile/careless server that echoes the credential it was given
            msg = {"error": {"message": "Incorrect API key provided: %s" % (auth or "none")}}
            return self._send(h, 401, json.dumps(msg).encode(),
                              headers={"X-Echo": auth or ""})
        if mode == "500":
            return self._send(h, 500, b'{"error":"boom"}')
        if mode == "429":
            hdr = {"Retry-After": str(self.retry_after)} if self.retry_after is not None else {}
            return self._send(h, 429, b'{"error":"slow down"}', headers=hdr)
        if mode == "redirect":
            return self._send(h, 302, b"", headers={"Location": self.location})
        if mode == "html":
            return self._send(h, 200, b"<html>hi</html>", ctype="text/html")
        if mode == "badjson":
            return self._send(h, 200, b'{"data": [ {"index": 0, "embedding": [0.1, ')
        if mode == "huge":
            big = b" " * (9 * 1024 * 1024)
            return self._send(h, 200, big)
        if mode == "huge-chunked":
            h.send_response(200)
            h.send_header("Content-Type", "application/json")
            h.end_headers()
            try:
                for _ in range(200):
                    h.wfile.write(b" " * 65536)
            except OSError:
                pass
            return
        try:
            req = json.loads(body.decode("utf-8"))
            inputs = req["input"]
        except Exception:
            return self._send(h, 400, b'{"error":"bad request"}')
        with self._m:
            self.inputs.extend(inputs)
        dim = self.dim
        if self.after_dim is not None and self.fail_after is not None \
                and self.ok_calls >= self.fail_after:
            dim = self.after_dim
        data = [{"object": "embedding", "index": i, "embedding": fake_dense.fake_vector(t, dim)}
                for i, t in enumerate(inputs)]
        if mode == "shuffle":
            data.reverse()
        elif mode == "nan":
            txt = json.dumps({"data": data}).replace('"embedding": [', '"embedding": [NaN, ', 1)
            return self._send(h, 200, txt.encode())
        elif mode == "inf":
            data[0]["embedding"][0] = 1e999
            return self._send(h, 200, json.dumps({"data": data}).replace("Infinity", "1e999").encode())
        elif mode == "short":
            data = data[:-1]
        elif mode == "dupindex":
            for d in data:
                d["index"] = 0
        elif mode == "zero":
            for d in data:
                d["embedding"] = [0.0] * dim
        elif mode == "ragged":
            data[0]["embedding"] = data[0]["embedding"][:-1]
        elif mode == "strings":
            data[0]["embedding"][0] = "0.5"
        with self._m:
            self.ok_calls += 1
        self._send(h, 200, json.dumps({"object": "list", "data": data, "model": req.get("model"),
                                       "usage": {"prompt_tokens": 1, "total_tokens": 1}}).encode())

    def stop(self):
        self._down.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def make_self_signed(directory):
    """(certfile, keyfile) for localhost via the openssl CLI, or None when it is unavailable."""
    cert, key = os.path.join(directory, "c.pem"), os.path.join(directory, "k.pem")
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key,
                        "-out", cert, "-days", "2", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       check=True, capture_output=True, timeout=60)
    except Exception:
        return None
    return cert, key


def wait_for(pred, timeout=10.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()
