"""mnemo CLI: argument parsing and formatting over engram.client. No engine logic.

Exit codes: 0 ok, and also when the daemon is offline or a command is not yet available
(fail-soft: a one-line note goes to stderr) | 1 daemon refused the request | 2 usage error |
3 daemon offline and 4 not yet available, both only with --strict / MNEMO_STRICT=1. Results go to stdout; messages and errors go to stderr.
The token is never printed.
"""
import argparse
import json
import os
import sys
import time

from engram import client, gitctx

EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_OFFLINE, EXIT_NA = 0, 1, 2, 3, 4
EPILOG = ("exit codes: 0 ok, or daemon offline / not yet available (one-line note on "
          "stderr; fail-soft); 1 daemon refused the request; 2 usage error; with --strict "
          "or MNEMO_STRICT=1: 3 daemon offline (try `mnemo up`), 4 not yet available. "
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
    p.add_argument("--strict", action="store_true",
                   help="exit 3 when the daemon is offline, 4 when not yet available "
                        "(also MNEMO_STRICT=1)")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND", parser_class=_Parser)
    sub.required = True

    def add(name, help_):
        s = sub.add_parser(name, help=help_, description=help_, epilog=EPILOG,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
        s.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="machine-readable JSON on stdout")
        s.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="exit non-zero when the daemon is offline")
        return s

    add("status", "show daemon and memory counts")
    s = add("up", "start the daemon if needed and wait for it")
    s.add_argument("--timeout", type=float, default=UP_TIMEOUT, help="seconds to wait")
    s = add("sleep", "run a consolidation cycle")
    s.add_argument("--quick", action="store_true", help="non-deep cycle")
    s = add("recall", "recall memories for a cue")
    s.add_argument("cue", help="cue text, or '-' for stdin")
    s.add_argument("-k", type=_pos_int, default=None, help="max results")
    s.add_argument("--scope", choices=["all", "repo"], default="all",
                   help="all (default, same-repo boosted) or repo (only this repo)")
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
    s = add("export-rules", "write .claude/engram_learned.md and .cursor/rules/engram_learned.mdc "
                            "for this repository (whole files owned by Engram)")
    s.add_argument("--target", choices=["all", "claude", "cursor"], default="all")
    s.add_argument("--if-changed", action="store_true",
                   help="only refresh existing Engram-owned files; create nothing, quiet")
    for name, verb in (("install", "install"), ("uninstall", "remove")):
        s = add(name, "%s Engram's hooks for a host (only claude-code so far)" % verb)
        s.add_argument("host", choices=["claude-code"])
        s.add_argument("--scope", choices=["user", "local", "project"], default=None,
                       help="user (default; ~/.claude/settings.json or $CLAUDE_CONFIG_DIR), "
                            "local (<git root>/.claude/settings.local.json) or project "
                            "(<git root>/.claude/settings.json, committed)")
        s.add_argument("--dry-run", action="store_true", help="print a diff, write nothing")
        if name == "install":
            s.add_argument("--status", action="store_true",
                           help="show where hooks are installed (all scopes unless --scope)")
            s.add_argument("--mcp", action="store_true",
                           help="also register the MCP server with `claude mcp add`")
    s = add("reembed", "re-embed stored vectors under the configured embedder (resumable, "
                       "background). Only a REMOTE target (openai:) needs --yes, because it sends "
                       "memory text to another host; without --yes it only shows what would be "
                       "sent. A local target (hash, fastembed) starts immediately, no --yes")
    s.add_argument("--dry-run", action="store_true", help="show what would be re-embedded; send nothing")
    s.add_argument("--yes", action="store_true",
                   help="confirm sending memory text to the remote host and start (required "
                        "for a remote embedder when not asked interactively)")
    s.add_argument("--status", action="store_true", help="show progress of the current/last run")
    s.add_argument("--cancel", action="store_true", help="stop the current run")
    s.add_argument("--batch", type=_pos_int, default=None, help="memories per batch (default 32, max 64)")
    s.add_argument("--include-identity", action="store_true",
                   help="also re-embed IDENTITY memories (skipped by default: the most personal)")
    return p


def _out(s):
    sys.stdout.write(s + "\n")


def _err(s):
    sys.stderr.write("mnemo: %s\n" % s)


def _text_arg(v):
    return sys.stdin.read() if v == "-" else v


def _too_large(text, limit):
    if len(text) > limit:
        _err("content too large: max %d characters (got %d)" % (limit, len(text)))
        return True
    return False


def _ctx():
    cwd = os.getcwd()
    return {"cwd": cwd, "repo_root": gitctx.repo_root(cwd)}


def _oneline(s, n=200):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def _strict(args):
    return bool(getattr(args, "strict", False)) or os.environ.get("MNEMO_STRICT") == "1"


def _offline_exit(args):
    return EXIT_OFFLINE if _strict(args) else EXIT_OK


def _finish(r, args, fmt):
    """Common exit handling; fmt(r) prints the human form."""
    if r.get("offline"):
        if args.json:
            _out(json.dumps(r))
        if client.ping(timeout=0.5):
            _err("request refused or connection dropped (daemon is running)")
            return EXIT_ERROR
        _err("daemon is not running (start it with `mnemo up`)")
        return _offline_exit(args)
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
    if r.get("embedder_configured") not in (None, "hash"):
        _out("embedder configured: %s   state: %s   falling back: %s   fallbacks: %s" % (
            r.get("embedder_configured"), r.get("embedder_state"),
            "yes" if r.get("embedder_falling_back") else "no", r.get("embedder_fallbacks")))
        if r.get("embedder_reason") or r.get("embedder_retry_in_s") is not None:
            _out("embedder reason: %s   next retry in: %ss" % (
                r.get("embedder_reason") or "-", r.get("embedder_retry_in_s")))
    dist = r.get("distiller") or {}
    if dist.get("mode") not in (None, "off") or dist.get("error"):
        age = dist.get("oldest_pending_age_s")
        _out("distiller: %s   queued: %s   oldest pending: %s" % (
            dist.get("mode"), dist.get("queue_length"), "-" if age is None else "%ss" % age))
        if dist.get("notice"):
            _out("distiller: %s (%s)   calls today: %s/%s   breaker: %s" % (
                dist.get("notice"), dist.get("provider"), dist.get("calls_today"),
                dist.get("daily_cap"), "OPEN" if (dist.get("breaker") or {}).get("open") else "closed"))
        if dist.get("error"):
            _out("distiller: %s" % dist.get("error"))
    if r.get("embedder_remote"):
        _out("WARNING: memory text is sent to %s for embedding (model %s; remote embedder%s)" % (
            r.get("embedder_remote_host") or "an unknown host", r.get("embedder_remote_model") or "?",
            "; currently falling back to hashed, nothing is sent" if r.get("embedder_falling_back")
            else ""))
    _fmt_reembed_line(r.get("reembed"))
    ls = r.get("last_sleep")
    _out("last sleep: %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ls))
                             if ls else "never"))
    _out("memories: %s   journal pending: %s" % (r.get("total"), r.get("journal_pending")))
    d = r.get("dedup") or {}
    if d:
        _out("dedup window: %s newest rows   cap %s   stored while capped: %s   (see %s)" % (
            d.get("fts_window"), "ACTIVE" if d.get("cap_active") else "inactive",
            d.get("stored_while_capped"), d.get("adr")))
    for kind in sorted(r.get("counts") or {}):
        by = r["counts"][kind]
        _out("  %-10s %s" % (kind, "  ".join("%s=%s" % (k, by[k]) for k in sorted(by))))


def _fmt_reembed_line(re_):
    if not re_ or re_.get("state") in (None, "idle"):
        return
    extra = ""
    if re_.get("rate_per_s"):
        extra = "   %s/s   eta %ss" % (re_["rate_per_s"], re_.get("eta_s"))
    _out("reembed: %s %s/%s%s%s" % (re_["state"], re_.get("done"), re_.get("total"), extra,
                                    "   last error: %s" % re_["last_error"] if re_.get("last_error")
                                    else ""))


def cmd_status(args):
    r = client.cli_call("status")
    if r.get("offline") and not args.json:
        _err("daemon: down (start it with `mnemo up`)")
        return _offline_exit(args)
    return _finish(r, args, _fmt_status)


def cmd_up(args):
    r = {"ok": True, "started": False}
    if not client.ping(timeout=0.5):
        client.autostart(wait=max(0.1, args.timeout))
        r["ok"] = client.ping(timeout=1.0)
        r["started"] = r["ok"] and _spawned_by_me()
    if args.json:
        _out(json.dumps(r))
    elif r["ok"]:
        _out("daemon started" if r["started"] else "daemon already running")
    if not r["ok"]:
        _err("daemon did not come up (TCP/sidecar mode or start failure)")
        return _offline_exit(args)
    return EXIT_OK


def _spawned_by_me():
    """autostart() stamps its spawner's pid under the lock; only the spawner matches."""
    try:
        with open(os.path.join(client._home(None), "autostart.stamp")) as f:
            return f.read().strip() == str(os.getpid())
    except OSError:
        return False


def cmd_sleep(args):
    r = client.cli_call("sleep", {"deep": not args.quick})
    rc = _finish(r, args, lambda r: _out("sleep cycle done"))
    if rc == EXIT_OK and r.get("ok"):
        _refresh_exports()
    return rc


def cmd_recall(args):
    p = _ctx()
    p["cue"] = _text_arg(args.cue)
    if not p["cue"].strip():
        _err("empty cue")
        return EXIT_USAGE
    from engram.daemon import MAX_CUE_CHARS
    if _too_large(p["cue"], MAX_CUE_CHARS):
        return EXIT_USAGE
    if args.k:
        p["k"] = args.k
    if args.no_reinforce:
        p["reinforce"] = False
    if args.scope != "all":
        p["scope"] = args.scope

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
    from engram.daemon import MAX_CONTENT_CHARS
    if _too_large(p["content"], MAX_CONTENT_CHARS):
        return EXIT_USAGE
    if args.kind:
        p["kind"] = args.kind
    if args.salience is not None:
        p["salience"] = args.salience

    def fmt(r):
        _out(str(r.get("id") or r.get("memory_id") or "stored"))
    return _finish(client.cli_call("remember", p), args, fmt)


def cmd_forget(args):
    rc = _finish(client.cli_call("forget", {"id": args.id}), args,
                 lambda r: _out("forgotten %s" % args.id))
    if rc == EXIT_OK:
        _refresh_exports()
    return rc


def cmd_dump(args):
    p = {k: v for k, v in (("kind", args.kind), ("status", args.status),
                           ("limit", args.limit), ("offset", args.offset)) if v is not None}

    def fmt(r):
        for m in r.get("memories") or []:
            _out("%s  [%s/%s]  %s" % (m.get("id"), m.get("kind"), m.get("status"),
                                      _oneline(m.get("content"))))
    return _finish(client.cli_call("dump", p), args, fmt)


def _refresh_exports():
    """After forget/sleep: refresh already-exported files (quiet, best effort). The daemon
    cannot write files, so this client-side call (and SessionEnd) is where re-export happens."""
    try:
        from engram import export
        export.run(os.getcwd(), if_changed=True)
    except Exception:
        pass


def cmd_export_rules(args):
    from engram import export
    targets = tuple(export.TARGETS) if args.target == "all" else (args.target,)
    r = export.run(os.getcwd(), targets=targets, if_changed=args.if_changed)
    strict = _strict(args)
    if args.json:
        _out(json.dumps(r, sort_keys=True))
    if r.get("offline"):
        if not args.if_changed:
            _err("daemon is not running; export files left untouched (start it with `mnemo up`)")
        return EXIT_OFFLINE if strict else EXIT_OK
    for w in r["warnings"]:
        _err(w)
    if not r["ok"]:
        _err(str(r.get("error") or "export failed"))
        return EXIT_ERROR if strict else EXIT_OK
    if not args.json and not args.if_changed:
        for t, st in sorted(r["results"].items()):
            _out("%s/%s: %s" % (export.TARGETS[t][0], export.TARGETS[t][1], st))
    if r["refused"]:
        return EXIT_ERROR if strict else EXIT_OK
    return EXIT_OK


def _fmt_plan(r, include_identity):
    n, c = r.get("memories"), r.get("chars")
    if include_identity and r.get("with_identity"):
        n, c = r["with_identity"]["memories"], r["with_identity"]["chars"]
    _out("reembed plan: %s memories (%s characters) to re-embed as %s" % (n, c, r.get("target")))
    if r.get("remote"):
        _out("REMOTE embedder: this sends memory text (redacted, at most %s characters each) to "
             "%s (model %s)" % (r.get("item_char_cap"), r.get("host") or "?", r.get("model") or "?"))
    if not include_identity and r.get("identity_excluded"):
        _out("identity memories skipped: %s (pass --include-identity to send them too)"
             % r["identity_excluded"])
    return n


def _fmt_progress(r):
    p = r.get("reembed") or {}
    _out("reembed: %s   %s/%s   rate %s/s   eta %ss   target %s" % (
        p.get("state"), p.get("done"), p.get("total"), p.get("rate_per_s") or "-",
        p.get("eta_s") if p.get("eta_s") is not None else "-", p.get("target")))
    if p.get("last_error"):
        _out("last error: %s" % p["last_error"])


def _refuse(args, msg):
    _err(msg)
    return EXIT_NA if _strict(args) else EXIT_OK


def cmd_reembed(args):
    modes = [m for m in ("dry_run", "status", "cancel") if getattr(args, m)]
    if len(modes) > 1 or (modes and args.yes):
        _err("--dry-run, --status, --cancel and --yes are mutually exclusive")
        return EXIT_USAGE
    if args.status or args.cancel:
        act = "status" if args.status else "cancel"
        return _finish(client.cli_call("reembed", {"action": act}), args, _fmt_progress)
    plan = client.cli_call("reembed", {"action": "plan"})
    if plan.get("offline") or not plan.get("ok"):
        return _finish(plan, args, lambda r: None)
    if not plan.get("ready"):
        if args.json:
            _out(json.dumps(plan, sort_keys=True))
        return _refuse(args, "embedder not ready: %s" % (plan.get("reason") or "unknown"))
    if args.json and args.dry_run:
        _out(json.dumps(plan, sort_keys=True))
        return EXIT_OK
    if not args.json:
        n = _fmt_plan(plan, args.include_identity)
    else:
        n = (plan.get("with_identity") or plan)["memories"] if args.include_identity \
            else plan.get("memories")
    if args.dry_run:
        return EXIT_OK
    if not n:
        if not args.json:
            _out("nothing to re-embed")
        return EXIT_OK
    confirm = args.yes
    if plan.get("remote") and not confirm:
        if sys.stdin.isatty() and sys.stderr.isatty():
            sys.stderr.write("Send this memory text to %s? [y/N] " % (plan.get("host") or "the remote host"))
            sys.stderr.flush()
            confirm = sys.stdin.readline().strip().lower() in ("y", "yes")
        if not confirm:
            return _refuse(args, "not started: pass --yes to send memory text to %s "
                                 "(nothing was sent)" % (plan.get("host") or "the remote host"))
    p = {"action": "start", "confirm": bool(confirm), "include_identity": bool(args.include_identity)}
    if args.batch:
        p["batch"] = args.batch
    return _finish(client.cli_call("reembed", p), args, lambda r: (
        _out("reembed started in the background; follow it with `mnemo reembed --status`")))


def cmd_install(args):
    from engram import install
    return install.run(args)


COMMANDS = {"install": cmd_install, "uninstall": cmd_install, "status": cmd_status, "up": cmd_up, "sleep": cmd_sleep, "recall": cmd_recall,
            "remember": cmd_remember, "forget": cmd_forget, "dump": cmd_dump,
            "export-rules": cmd_export_rules, "reembed": cmd_reembed}


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not client.SUPPORTED and args.cmd not in ("install", "uninstall"):
        _err(client.UNSUPPORTED_MSG)
        return _offline_exit(args)
    try:
        return COMMANDS[args.cmd](args)
    except BrokenPipeError:
        return EXIT_OK
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # fail-soft: never a traceback
        _err("unexpected error: %s" % type(e).__name__)
        return EXIT_ERROR
