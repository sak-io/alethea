"""Stdio MCP server (R24): hand-rolled JSON-RPC 2.0, stdlib only, no SDK.

Runs as `python3 -m engram.mcp_server` and by absolute path (`python3 /x/engram/mcp_server.py`).
A thin layer over engram.client: no memory logic here. stdout carries ONLY JSON-RPC (one
message per line, flushed); diagnostics go to stderr.

isError semantics (decided): the session must never break.
  * daemon down/hung/erroring      -> normal result, isError false, text "memory offline"
    (an informational outcome, not something the model can fix by retrying differently);
  * invalid tool arguments         -> normal result, isError true, text says what is wrong
    (the model can correct and retry); never a JSON-RPC protocol error;
  * unknown tool name              -> JSON-RPC error -32602 (per MCP spec).
"""
import json
import os
import sys

if __package__ in (None, ""):  # run by absolute path: make `engram` importable, drop script dir
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != _here]
    sys.path.insert(0, os.path.dirname(_here))

from engram import client, config, gitctx, render  # noqa: E402

SERVER_NAME, SERVER_VERSION = "engram", "0.1.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_LINE_BYTES = 1 << 20
MAX_CONTENT = 20000
MAX_CUE = 2000
MAX_K = 20
MAX_ITEM_CHARS = 400
MAX_TEXT_CHARS = 6000
OFFLINE_TEXT = "memory offline"

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL = (
    -32700, -32600, -32601, -32602, -32603)

_COMMON = {
    "cwd": {"type": "string", "maxLength": 4096,
            "description": "Working directory of the project; used to scope memories to its "
                           "git repository. Defaults to the server's own directory."},
    "session_id": {"type": "string", "maxLength": 256,
                   "description": "Optional session identifier."},
}


def _schema(props, required=()):
    props = dict(props)
    props.update(_COMMON)
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


TOOLS = {
    "recall": {
        "description": "Search long-term memory for facts, decisions, procedures and past "
                       "bug causes relevant to a cue. Returns a plain-text list.",
        "inputSchema": _schema({
            "cue": {"type": "string", "minLength": 1, "maxLength": MAX_CUE,
                    "description": "What to remember about (natural language or keywords)."},
            "k": {"type": "integer", "minimum": 1, "maximum": MAX_K,
                  "description": "Maximum number of memories to return (default 5)."},
            "scope": {"type": "string", "enum": ["all", "repo"], "default": "all",
                      "description": "all (default; current repo boosted) or repo (only "
                                     "memories of the current repository)."}},
            ["cue"])},
    "remember": {
        "description": "Store a durable fact, decision or procedure in long-term memory.",
        "inputSchema": _schema({
            "content": {"type": "string", "minLength": 1, "maxLength": MAX_CONTENT,
                        "description": "The thing to remember, as a self-contained statement."},
            "kind": {"type": "string", "enum": ["episodic", "semantic", "procedural", "identity"],
                     "description": "Optional memory kind; inferred when omitted."},
            "salience": {"type": "number", "minimum": 0, "maximum": 1,
                         "description": "Optional importance from 0 to 1."}},
            ["content"])},
    "reflect": {
        "description": "Store an insight that generalises earlier memories, linking it to them.",
        "inputSchema": _schema({
            "content": {"type": "string", "minLength": 1, "maxLength": MAX_CONTENT,
                        "description": "The generalised insight."},
            "cited_ids": {"type": "array", "maxItems": 50,
                          "items": {"type": "string", "maxLength": 128},
                          "description": "Ids of the memories this insight abstracts."}},
            ["content"])},
    "forget": {
        "description": "Forget (suppress) one memory by id.",
        "inputSchema": _schema({
            "id": {"type": "string", "minLength": 1, "maxLength": 128,
                   "description": "Memory id, as shown by recall."}}, ["id"])},
    "engram_status": {
        "description": "Show memory service health and counts.",
        "inputSchema": _schema({})},
}
CONSOLIDATE_TOOL = {
    "description": "Run a consolidation pass over pending journal entries now.",
    "inputSchema": _schema({})}


def _consolidate_enabled(env=None):
    try:
        return bool(config.load_config(env)["ENGRAM_LLM_CONSOLIDATE"])
    except Exception:
        return False


def _log(msg):
    try:
        sys.stderr.write("engram-mcp: %s\n" % msg)
        sys.stderr.flush()
    except Exception:
        pass


# ---- rendering -------------------------------------------------------------------------

_plain = render.plain  # one shared implementation (engram/render.py)
RECALL_HEADER = render.RECALL_HEADER


def render_recall(results):
    items = []
    for m in results[:MAX_K]:
        if not isinstance(m, dict):
            continue
        text = _plain(m.get("gist") or m.get("content"), MAX_ITEM_CHARS)
        if text:
            items.append("- [%s] (id %s) %s" % (_plain(m.get("kind"), 16),
                                                _plain(m.get("id"), 64), text))
    if not items:
        return "no matching memories"
    out = RECALL_HEADER + "\n" + "\n".join(items)
    return out if len(out) <= MAX_TEXT_CHARS else out[:MAX_TEXT_CHARS - 3] + "..."


def render_status(r):
    parts = ["memory online", "total=%s" % r.get("total"),
             "pending=%s" % r.get("journal_pending"), "embedder=%s" % r.get("embedder_id")]
    if r.get("embedder_configured") not in (None, "hash"):
        parts += ["embedder_configured=%s" % r.get("embedder_configured"),
                  "embedder_state=%s" % r.get("embedder_state"),
                  "falling_back=%s" % bool(r.get("embedder_falling_back")),
                  "fallbacks=%s" % r.get("embedder_fallbacks"),
                  "reason=%s" % r.get("embedder_reason")]
    return _plain(" ".join(parts), 500)


# ---- tools -----------------------------------------------------------------------------

class ArgError(ValueError):
    pass


def _check(args, spec):
    if not isinstance(args, dict):
        raise ArgError("arguments must be an object")
    props = spec["inputSchema"]["properties"]
    for k in args:
        if k not in props:
            raise ArgError("unknown argument: %s" % _plain(k, 40))
    for k in spec["inputSchema"]["required"]:
        if k not in args:
            raise ArgError("missing argument: %s" % k)
    for k, v in args.items():
        p = props[k]
        t = p["type"]
        if t == "string":
            ok = isinstance(v, str)
        elif t == "integer":
            ok = isinstance(v, int) and not isinstance(v, bool)
        elif t == "number":
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v == v
        else:
            ok = isinstance(v, list)
        if not ok:
            raise ArgError("%s must be of type %s" % (k, t))
        if t == "string":
            if len(v) > p.get("maxLength", 1 << 30):
                raise ArgError("%s too long (max %d)" % (k, p["maxLength"]))
            if len(v.strip()) < p.get("minLength", 0):
                raise ArgError("%s must not be empty" % k)
            if "enum" in p and v not in p["enum"]:
                raise ArgError("%s must be one of %s" % (k, ", ".join(p["enum"])))
        elif t in ("integer", "number"):
            if v < p.get("minimum", v) or v > p.get("maximum", v):
                raise ArgError("%s out of range [%s, %s]" % (k, p.get("minimum"), p.get("maximum")))
        else:
            if len(v) > p["maxItems"] or not all(
                    isinstance(i, str) and len(i) <= p["items"]["maxLength"] for i in v):
                raise ArgError("%s must be a list of at most %d id strings" % (k, p["maxItems"]))


class RpcError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code, self.message = code, message


def _valid_id(i):
    return (isinstance(i, (str, int)) and not isinstance(i, bool)) or (
        isinstance(i, float) and i == i)


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _dump(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


class Server:
    def __init__(self, env=None, cwd=None):
        self.env = env
        self.consolidate = _consolidate_enabled(env)
        try:
            start_cwd = cwd or os.getcwd()
        except OSError:
            start_cwd = None
        self.cwd = start_cwd
        self.repo_root = gitctx.repo_root(start_cwd) if start_cwd else None
        self.session_id = "mcp-%d" % os.getpid()
        self.done = False

    def tools(self):
        t = dict(TOOLS)
        if self.consolidate:
            t["engram_consolidate_pending"] = CONSOLIDATE_TOOL
        return t

    def _scope(self, args):
        cwd = args.get("cwd")
        if cwd:
            root = gitctx.repo_root(cwd)
        else:
            cwd, root = self.cwd, self.repo_root
        return {"cwd": cwd, "repo_root": root,
                "session_id": args.get("session_id") or self.session_id}

    def _forward(self, verb, params):
        r = client.mcp_call(verb, params, env=self.env)
        return r if isinstance(r, dict) else dict(client.OFFLINE)

    def run_tool(self, name, args):
        """Returns (text, is_error). Never raises."""
        spec = self.tools()[name]
        try:
            _check(args, spec)
        except ArgError as e:
            return "invalid arguments: %s" % e, True
        scope = self._scope(args)
        if name == "recall":
            r = self._forward("recall", {"cue": args["cue"], "k": args.get("k", 5),
                                         "scope": args.get("scope", "all"), **scope})
            if not r.get("ok"):
                return OFFLINE_TEXT, False
            return render_recall(r.get("results") or []), False
        if name in ("remember", "reflect"):
            p = {k: v for k, v in args.items() if k in ("content", "kind", "salience", "cited_ids")}
            r = self._forward(name, {**p, **scope})
            if not r.get("ok"):
                return OFFLINE_TEXT, False
            st = r.get("status")
            if st == "rejected":
                return "not stored (%s)" % _plain(r.get("reason"), 40), False
            return "%s (id %s)" % (_plain(st, 20), _plain(r.get("id"), 64)), False
        if name == "forget":
            r = self._forward("forget", {"id": args["id"]})
            if r.get("ok"):
                return "forgotten", False
            if r.get("offline"):
                return OFFLINE_TEXT, False
            return "no such memory", False
        if name == "engram_status":
            r = self._forward("status", {})
            return (render_status(r) if r.get("ok") else OFFLINE_TEXT), False
        r = self._forward("sleep", {"consolidate_only": True, "budget_s": 4.0})
        return ("consolidation done" if r.get("ok") else OFFLINE_TEXT), False

    # ---- JSON-RPC ----------------------------------------------------------------------

    def dispatch(self, method, params):
        """Returns result dict; raises RpcError."""
        if method == "initialize":
            want = params.get("protocolVersion") if isinstance(params, dict) else None
            ver = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}
        if method == "ping":
            return {}
        if method == "shutdown":
            self.done = True
            return {}
        if method == "tools/list":
            return {"tools": [dict(name=n, **s) for n, s in self.tools().items()]}
        if method == "tools/call":
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                raise RpcError(INVALID_PARAMS, "name required")
            if params["name"] not in self.tools():
                raise RpcError(INVALID_PARAMS, "unknown tool")
            args = params.get("arguments")
            text, err = self.run_tool(params["name"], {} if args is None else args)
            return {"content": [{"type": "text", "text": text}], "isError": err}
        raise RpcError(METHOD_NOT_FOUND, "method not found")

    def handle_message(self, msg):
        """One decoded JSON value -> response object or None (notification)."""
        if isinstance(msg, dict) and ("result" in msg or "error" in msg) \
                and "method" not in msg:
            return None  # a response from the client: ignore
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" \
                or not isinstance(msg.get("method"), str):
            rid = msg.get("id") if isinstance(msg, dict) else None
            return _error(rid if _valid_id(rid) else None, INVALID_REQUEST, "invalid request")
        if "id" not in msg:  # notification: never answered
            if msg["method"] == "exit":
                self.done = True
            return None
        rid = msg["id"]
        if not _valid_id(rid):
            return _error(None, INVALID_REQUEST, "invalid id")
        try:
            return {"jsonrpc": "2.0", "id": rid,
                    "result": self.dispatch(msg["method"], msg.get("params"))}
        except RpcError as e:
            return _error(rid, e.code, e.message)
        except Exception as e:
            _log("internal error: %s" % type(e).__name__)
            return _error(rid, INTERNAL, "internal error")

    def handle_line(self, raw):
        """bytes line -> serialized reply string or None."""
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _dump(_error(None, PARSE_ERROR, "invalid utf-8"))
        if not text.strip():
            return None
        try:
            msg = json.loads(text)
        except (ValueError, RecursionError):
            return _dump(_error(None, PARSE_ERROR, "parse error"))
        if isinstance(msg, list):
            if not msg:
                return _dump(_error(None, INVALID_REQUEST, "empty batch"))
            out = [r for r in (self.handle_message(m) for m in msg) if r is not None]
            return _dump(out) if out else None
        r = self.handle_message(msg)
        return _dump(r) if r is not None else None


OVERSIZE = object()


def _read_line(stream):
    """Next line as bytes without newline; None at EOF; OVERSIZE for a line over the cap."""
    line = stream.readline(MAX_LINE_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
        while True:  # drain the rest of the oversize line in bounded chunks
            more = stream.readline(65536)
            if not more or more.endswith(b"\n"):
                break
        return OVERSIZE
    return line.rstrip(b"\r\n")


def serve(stdin=None, stdout=None, server=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout
    server = server or Server()
    while not server.done:
        try:
            line = _read_line(stdin)
            if line is None:
                break
            if line is OVERSIZE:
                reply = _dump(_error(None, INVALID_REQUEST, "line too long"))
            else:
                reply = server.handle_line(line)
            if reply is not None:
                stdout.write(reply + "\n")
                stdout.flush()
        except (BrokenPipeError, KeyboardInterrupt):
            break
        except Exception as e:
            _log("loop error: %s" % type(e).__name__)
    return 0


def main():
    try:
        if not client.SUPPORTED:
            _log(client.UNSUPPORTED_MSG + "; every tool will report the memory service as offline")
        return serve()
    except BaseException as e:
        if not isinstance(e, KeyboardInterrupt):
            _log("fatal: %s" % type(e).__name__)
        return 0


if __name__ == "__main__":
    sys.exit(main())
