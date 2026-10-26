#!/usr/bin/env python3
"""Claude Code hook entry point (R26). Stdlib only.

Reads the hook JSON from stdin, takes the event from `hook_event_name` (fallback argv[1]),
dispatches. Contract (see docs/architecture/claude-code-hooks-reference.md):
  SessionStart     -> briefing, printed as hookSpecificOutput.additionalContext (<= 8000 chars)
  UserPromptSubmit -> observe(prompt, role=user); prints NOTHING (stdout would become context)
  Stop             -> observe(last_assistant_message, role=assistant); never blocks
  SessionEnd       -> session_end, fire-and-forget (the daemon consolidates in the background)
  PreCompact       -> non-deep sleep, fire-and-forget
Always exits 0 (never 2), never raises, 2 s hard deadline (watchdog), stdout is either empty
or exactly one JSON object. stderr is silent unless ALETHEA_HOOK_DEBUG=1 (one line, no content).
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

DEADLINE_S = 2.0
STDIN_CAP = 2 << 20
BRIEFING_CAP = 8000
MAX_CONTENT_CHARS = 200_000  # daemon.MAX_CONTENT_CHARS
TURN_CAP_BYTES = 256 * 1024  # same as the daemon's per-turn default (encode.MAX_TURN_BYTES)
BRIEFING_WAIT_S = 1.2  # worst case (hung daemon) stays <= 1.3 s
BRIEFING_LINE_CHARS = 400
SEND_TIMEOUT_S = 0.3
_T0 = time.monotonic()
_debug_notes = []


def _note(msg):
    _debug_notes.append(msg)


def _finish(out=None):
    """Write the (optional) single JSON line, flush, and leave without any cleanup hang."""
    try:
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
    except BaseException:
        pass
    try:
        if os.environ.get("ALETHEA_HOOK_DEBUG") == "1":
            sys.stderr.write("alethea-hook: %s %.0fms\n" % (
                ",".join(_debug_notes) or "ok", (time.monotonic() - _T0) * 1000))
            sys.stderr.flush()
    except BaseException:
        pass
    os._exit(0)


def _watchdog():
    _note("deadline")
    _finish(None)


def _cap_text(text):
    """Cap to the daemon's per-turn limits (200k chars request field, 256 KB encode cap)."""
    text = text[:MAX_CONTENT_CHARS]
    b = text.encode("utf-8", "replace")
    if len(b) <= TURN_CAP_BYTES:
        return text
    return b[:TURN_CAP_BYTES].decode("utf-8", "ignore")


def _read_payload():
    try:
        raw = sys.stdin.buffer.read(STDIN_CAP + 1)
    except BaseException:
        return {}
    if not raw or len(raw) > STDIN_CAP:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def _s(payload, key, maxlen):
    v = payload.get(key)
    return v if isinstance(v, str) and 0 < len(v) <= maxlen else None


def _scope(payload):
    cwd = _s(payload, "cwd", 4096)
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:
            cwd = None
    params = {}
    if cwd:
        params["cwd"] = cwd
        try:
            from alethea import gitctx
            root = gitctx.repo_root(cwd)
        except BaseException:
            root = None
        if root and len(root) <= 4096:
            params["repo_root"] = root
    sid = _s(payload, "session_id", 256)
    if sid:
        params["session_id"] = sid
    return params


def _send_only(verb, params):
    from alethea import client
    return client.send_nowait(verb, params, timeout=SEND_TIMEOUT_S)


def _call(verb, params, timeout):
    from alethea import client
    return client.call(verb, params, timeout=timeout)


def _spawn_autostart():
    """Start the daemon in the background so later events work; never waits for it."""
    try:
        code = ("import sys; sys.path.insert(0, %r); from alethea import client; "
                "client.autostart()" % _ROOT)
        subprocess.Popen([sys.executable, "-I", "-c", code], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
        _note("autostart")
    except Exception:
        pass


def on_session_start(payload):
    r = _call("briefing", _scope(payload), BRIEFING_WAIT_S)
    if r.get("offline"):
        _note("offline")
        _spawn_autostart()
        return None
    text = r.get("text") if r.get("ok") else None
    if not isinstance(text, str) or not text.strip():
        return None
    from alethea import render  # memory is data: neutralise it before it reaches the model
    ctx = render.render_list(text.splitlines(), BRIEFING_LINE_CHARS, BRIEFING_CAP)
    if not ctx:
        return None
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": ctx}})


def _observe(text, role, payload):
    if not isinstance(text, str) or not text.strip():
        return None
    params = _scope(payload)
    params.update(content=_cap_text(text), role=role)
    if not _send_only("observe", params):  # reply unused: do not make the user wait
        _note("offline")
    return None


def on_user_prompt(payload):
    return _observe(payload.get("prompt"), "user", payload)


def on_stop(payload):
    if payload.get("stop_hook_active") is True:
        return None
    return _observe(payload.get("last_assistant_message"), "assistant", payload)


def _spawn_export(params):
    """Refresh already-exported rule files in a detached child (the hook stays fast). Only
    when the repo already has an Alethea-owned export file: nothing is ever created here."""
    try:
        root = params.get("repo_root")
        if not root:
            return
        from alethea import export
        if not any(export._exists(root, t) for t in export.TARGETS):
            return
        subprocess.Popen([sys.executable, "-m", "alethea.export", params.get("cwd") or root],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                         cwd=_ROOT, env=dict(os.environ, PYTHONPATH=_ROOT,
                                             PYTHONDONTWRITEBYTECODE="1"))
        _note("export")
    except Exception:
        pass


def on_session_end(payload):
    params = _scope(payload)
    if "session_id" not in params:
        return None
    if not _send_only("session_end", params):
        _note("offline")
    else:
        _spawn_export(params)
    return None


def on_pre_compact(payload):
    params = _scope(payload)
    params["deep"] = False
    if not _send_only("sleep", params):
        _note("offline")
    return None


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_user_prompt,
    "Stop": on_stop,
    "SessionEnd": on_session_end,
    "PreCompact": on_pre_compact,
}


def main():
    wd = threading.Timer(DEADLINE_S, _watchdog)
    wd.daemon = True
    wd.start()
    try:
        signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    except (ValueError, OSError, AttributeError):
        pass
    out = None
    try:
        payload = _read_payload()
        event = payload.get("hook_event_name")
        if not isinstance(event, str):
            event = sys.argv[1] if len(sys.argv) > 1 else None
        handler = HANDLERS.get(event) if isinstance(event, str) else None
        if handler:
            out = handler(payload)
        else:
            _note("unknown-event")
    except BaseException as e:  # incl. KeyboardInterrupt: a hook must never fail the session
        _note("error:" + type(e).__name__)
    _finish(out)


if __name__ == "__main__":
    main()
