"""mnemo CLI: argument parsing and formatting over engram.client. No engine logic.

Exit codes: 0 ok | 1 daemon refused the request | 2 usage error | 3 daemon unreachable |
4 not yet available. Results go to stdout; messages and errors go to stderr.
The token is never printed.
"""
import argparse
import json
import os
import sys
import time

from engram import client, gitctx

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_OFFLINE, EXIT_NA = 0, 1, 2, 3, 4
EPILOG = ("exit codes: 0 ok; 1 daemon returned an error; 2 usage error; "
          "3 daemon unreachable (try `mnemo up`); 4 not yet available. "
          "Pass '-' as CONTENT/CUE to read it from stdin.")
UP_TIMEOUT = 5.0


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("mnemo: error: %s\n" % message)
        raise SystemExit(EXIT_USAGE)


def _pos_int(s):
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError("not an integer: %r" % s)
    if v < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return v


def _unit(s):
    try:
        v = float(s)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % s)
    if not 0.0 <= v <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return v


def build_parser():
    p = _Parser(prog="mnemo", description="Engram memory CLI.", epilog=EPILOG,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="machine-readable JSON on stdout")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", parser_class=_Parser)
    sub.required = True

    def add(name, help_):
        s = sub.add_parser(name, help=help_, description=help_, epilog=EPILOG,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        s.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")
        return s

    add("status", "show daemon and memory counts")
    s = add("up", "start the daemon if needed and wait for it")
    s.add_argument("--timeout", type=float, default=UP_TIMEOUT, help="seconds to wait")
    s = add("sleep", "run a consolidation cycle")
    s.add_argument("--quick", action="store_true", help="non-deep cycle")
    s = add("recall", "recall memories for a cue")
    s.add_argument("cue", help="cue text, or '-' for stdin")
    s.add_argument("-k", type=_pos_int, default=None, help="max results")
    s.add_argument("--no-reinforce", action="store_true", help="do not strengthen hits")
    s = add("remember", "store a memory")
    s.add_argument("content", help="text, or '-' for stdin")
    s.add_argument("--kind", choices=["episodic", "semantic", "procedural", "identity"])
    s.add_argument("--salience", type=_unit, default=None, help="0..1")
    s = add("forget", "forget a memory by id")
    s.add_argument("id")
    s = add("dump", "list stored memories")
    s.add_argument("--kind")
    s.add_argument("--status")
    s.add_argument("--limit", type=_pos_int, default=None)
    s.add_argument("--offset", type=int, default=None)
    add("export-rules", "write host rule files (not yet available)")
    add("reembed", "re-embed stored vectors (not yet available)")
    return p


def _out(s):
    sys.stdout.write(s + "\n")


def _err(s):
    sys.stderr.write("mnemo: %s\n" % s)


def _text_arg(v):
    return sys.stdin.read() if v == "-" else v


def _ctx():
    cwd = os.getcwd()
    return {"cwd": cwd, "repo_root": gitctx.repo_root(cwd)}


def _oneline(s, n=200):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _finish(r, args, fmt):
    """Common exit handling; fmt(r) prints the human form."""
    if r.get("offline"):
        if args.json:
            _out(json.dumps(r))
        _err("daemon is not running (start it with `mnemo up`)")
        return EXIT_OFFLINE
    if args.json:
        _out(json.dumps(r, sort_keys=True))
    elif r.get("ok"):
        fmt(r)
    if not r.get("ok"):
        _err(str(r.get("error") or "request failed"))
        return EXIT_ERROR
    return EXIT_OK


def _fmt_status(r):
    _out("daemon: up (pid %s, uptime %ss)" % (r.get("pid"), r.get("uptime_s")))
    _out("embedder: %s   fts5: %s   schema: %s" % (
        r.get("embedder_id"), "yes" if r.get("fts") else "no", r.get("schema_version")))
    ls = r.get("last_sleep")
    _out("last sleep: %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ls))
                             if ls else "never"))
    _out("memories: %s   journal pending: %s" % (r.get("total"), r.get("journal_pending")))
    for kind in sorted(r.get("counts") or {}):
        by = r["counts"][kind]
        _out("  %-10s %s" % (kind, "  ".join("%s=%s" % (k, by[k]) for k in sorted(by))))


def cmd_status(args):
    r = client.cli_call("status")
    if r.get("offline") and not args.json:
        _err("daemon: down (start it with `mnemo up`)")
        return EXIT_OFFLINE
    return _finish(r, args, _fmt_status)


def cmd_up(args):
    if client.ping(timeout=0.5):
        r = {"ok": True, "started": False}
    else:
        r = {"ok": bool(client.autostart(wait=max(0.1, args.timeout))), "started": True}
    if args.json:
        _out(json.dumps(r))
    elif r["ok"]:
        _out("daemon started" if r["started"] else "daemon already running")
    if not r["ok"]:
        _err("daemon did not come up (TCP/sidecar mode or start failure)")
        return EXIT_OFFLINE
    return EXIT_OK


def cmd_sleep(args):
    r = client.cli_call("sleep", {"deep": not args.quick})
    return _finish(r, args, lambda r: _out("sleep cycle done"))


def cmd_recall(args):
    p = _ctx()
    p["cue"] = _text_arg(args.cue)
    if not p["cue"].strip():
        _err("empty cue")
        return EXIT_USAGE
    if args.k:
        p["k"] = args.k
    if args.no_reinforce:
        p["reinforce"] = False

    def fmt(r):
        for m in r.get("results") or []:
            _out("%s  [%s]  %s" % (m.get("id"), m.get("kind"),
                                   _oneline(m.get("gist") or m.get("content"))))
    return _finish(client.cli_call("recall", p), args, fmt)


def cmd_remember(args):
    p = _ctx()
    p["content"] = _text_arg(args.content)
    if not p["content"].strip():
        _err("empty content")
        return EXIT_USAGE
    if args.kind:
        p["kind"] = args.kind
    if args.salience is not None:
        p["salience"] = args.salience

    def fmt(r):
        _out(str(r.get("id") or r.get("memory_id") or "stored"))
    return _finish(client.cli_call("remember", p), args, fmt)


def cmd_forget(args):
    return _finish(client.cli_call("forget", {"id": args.id}), args,
                   lambda r: _out("forgotten %s" % args.id))


def cmd_dump(args):
    p = {k: v for k, v in (("kind", args.kind), ("status", args.status),
                           ("limit", args.limit), ("offset", args.offset)) if v is not None}

    def fmt(r):
        for m in r.get("memories") or []:
            _out("%s  [%s/%s]  %s" % (m.get("id"), m.get("kind"), m.get("status"),
                                      _oneline(m.get("content"))))
    return _finish(client.cli_call("dump", p), args, fmt)


def _later(args):
    _err("%s is not yet available" % args.cmd)
    return EXIT_NA


COMMANDS = {"status": cmd_status, "up": cmd_up, "sleep": cmd_sleep, "recall": cmd_recall,
            "remember": cmd_remember, "forget": cmd_forget, "dump": cmd_dump,
            "export-rules": _later, "reembed": _later}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.cmd](args)
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # fail-soft: never a traceback
        _err("unexpected error: %s" % type(e).__name__)
        return EXIT_ERROR
