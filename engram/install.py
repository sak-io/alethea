"""`mnemo install claude-code`: wire Engram's hooks into a Claude Code settings file (stdlib only).

This module is the single source of truth for the hooks block (`hooks_block`); the copy-paste
template integrations/claude-code/settings.hooks.json is generated from it and a test keeps the
two identical.

Safety contract (all tested in tests/test_install.py):
  * merge, never replace: every other key, permission, hook and their order survive;
  * entries we own are recognised by their signature (a python interpreter command with args
    `-I -m engram.hooks.engram_hook`, or the two legacy forms below), updated in place on
    reinstall (second run: unchanged, nothing written);
  * uninstall removes only owned entries (and the containers it leaves empty);
  * invalid JSON / a non-object `hooks` is refused with the file untouched;
  * writes are atomic (temp file in the same directory, fsync, rename), keep the file mode and
    never follow a symlink at the target or at its directory;
  * the first modification of an existing file keeps `<name>.engram-backup`.
Exit codes: 0 ok, 1 `--mcp` registration failed (hooks were still written), 2 user error.
"""
import copy
import difflib
import errno
import json
import os
import re
import shutil
import stat
import subprocess
import sys

from engram import gitctx
from engram.export import Refused, _open_dir

HOOK_MODULE = "engram.hooks.engram_hook"
# -I (isolated mode): `python -m` otherwise puts the CURRENT directory first on sys.path, so a
# project containing a fake `engram/` package would run its code in every session there.
OWNED_ARGS = ["-I", "-m", HOOK_MODULE]
LEGACY_ARGS = (["-m", HOOK_MODULE],)  # first installer version; still recognised as ours
LEGACY_SCRIPT_SUFFIX = "/engram/hooks/engram_hook.py"  # T23 static block (script path)
_PYTHON_RE = re.compile(r"^python(\d+(\.\d+)*)?(\.exe)?$")
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreCompact")
TIMEOUT_S = 5
SESSION_END_TIMEOUT_S = 3  # the hook has its own 2 s deadline; CC's default 1.5 s would cancel it
SCOPES = ("user", "local", "project")
MAX_BYTES = 4 << 20
BACKUP_SUFFIX = ".engram-backup"
MCP_TIMEOUT_S = 10

EXIT_OK, EXIT_PARTIAL, EXIT_USER = 0, 1, 2


class InstallError(Exception):
    """A user-facing problem: printed without a traceback, exit code 2."""


# ------------------------------------------------------------------ the hooks block

def hook_handler(event, python=None):
    return {"type": "command", "command": python or sys.executable, "args": list(OWNED_ARGS),
            "timeout": SESSION_END_TIMEOUT_S if event == "SessionEnd" else TIMEOUT_S}


def hook_group(event, python=None):
    group = {"matcher": "*"} if event == "SessionStart" else {}
    group["hooks"] = [hook_handler(event, python)]
    return group


def hooks_block(python=None):
    """The complete `{"hooks": {...}}` object Engram owns (exec form, no PATH dependence)."""
    return {"hooks": {e: [hook_group(e, python)] for e in EVENTS}}


def template_text(python="/ABSOLUTE/PATH/TO/python"):
    return json.dumps(hooks_block(python), indent=2) + "\n"


def is_owned(handler):
    """Ours = a python interpreter running our hook module/script, in a known signature.
    A handler that merely mentions engram (other command, other path, other args) is not."""
    if not isinstance(handler, dict) or handler.get("type", "command") != "command":
        return False
    cmd, args = handler.get("command"), handler.get("args")
    if not isinstance(cmd, str) or not isinstance(args, list):
        return False
    if not _PYTHON_RE.match(os.path.basename(cmd)):
        return False
    if args == OWNED_ARGS or args in list(LEGACY_ARGS):
        return True
    return (len(args) == 1 and isinstance(args[0], str)
            and args[0].endswith(LEGACY_SCRIPT_SUFFIX))


# ------------------------------------------------------------------ pure merge / remove

def _check_shape(settings):
    if not isinstance(settings, dict):
        raise InstallError("settings file is not a JSON object; refusing to touch it")
    if "hooks" not in settings:
        return
    hooks = settings["hooks"]
    if not isinstance(hooks, dict):
        raise InstallError("the `hooks` key is not an object; refusing to touch the file")
    for event, groups in hooks.items():
        if event in EVENTS and not isinstance(groups, list):
            raise InstallError("hooks.%s is not an array; refusing to touch the file" % event)


def _owned_in(group):
    return isinstance(group, dict) and isinstance(group.get("hooks"), list) \
        and any(is_owned(h) for h in group["hooks"])


def merge(settings, python=None):
    """New settings with our five events installed. Pure; `settings` is not modified."""
    _check_shape(settings)
    out = copy.deepcopy(settings)
    hooks = out.setdefault("hooks", {})
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        want = hook_group(event, python)
        placed = False
        emptied = []
        for i, g in enumerate(list(groups)):
            if not _owned_in(g):
                continue
            if placed:  # duplicate owned handler: drop it, keep foreign siblings
                g["hooks"] = [h for h in g["hooks"] if not is_owned(h)]
                if not g["hooks"]:
                    emptied.append(g)
                continue
            others = [h for h in g["hooks"] if not is_owned(h)]
            if not others:
                groups[i] = want  # group is wholly ours: match the desired shape
            else:  # shared with a foreign handler: replace only our handler, in place
                g["hooks"] = [want["hooks"][0] if is_owned(h) else h for h in g["hooks"]]
            placed = True
        hooks[event] = [g for g in groups if not any(g is e for e in emptied)]
        if not placed:
            hooks[event].append(want)
    return out


def remove(settings):
    """New settings without our entries (and the containers that leaves empty). Pure."""
    _check_shape(settings)
    out = copy.deepcopy(settings)
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept, touched = [], False
        for g in groups:
            if _owned_in(g):
                touched = True
                g["hooks"] = [h for h in g["hooks"] if not is_owned(h)]
                if not g["hooks"]:
                    continue
            kept.append(g)
        if touched:
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not hooks and "hooks" in out and _had_owned(settings):
        del out["hooks"]
    return out


def _had_owned(settings):
    return any(_owned_in(g) for groups in settings.get("hooks", {}).values()
               if isinstance(groups, list) for g in groups)


def installed_events(settings):
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if not isinstance(hooks, dict):
        return []
    return [e for e in EVENTS if isinstance(hooks.get(e), list)
            and any(_owned_in(g) for g in hooks[e])]


def owned_interpreters(settings):
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    found = set()
    if isinstance(hooks, dict):
        for groups in hooks.values():
            for g in groups if isinstance(groups, list) else []:
                if _owned_in(g):
                    found.update(h.get("command") for h in g["hooks"] if is_owned(h))
    return sorted(str(c) for c in found)


# ------------------------------------------------------------------ paths and file I/O

def git_toplevel(cwd):
    try:
        p = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True,
                           text=True, timeout=5, stdin=subprocess.DEVNULL,
                           env=gitctx.scrubbed_env())
    except (OSError, subprocess.SubprocessError):
        return None
    top = p.stdout.strip()
    return top if p.returncode == 0 and top else None


def user_config_dir(environ=None):
    environ = os.environ if environ is None else environ
    d = environ.get("CLAUDE_CONFIG_DIR")
    if d:
        return os.path.abspath(os.path.expanduser(d))
    return os.path.join(os.path.expanduser("~"), ".claude")


def target_for(scope, cwd=None, environ=None):
    """(directory, file name) of the settings file for `scope`."""
    if scope == "user":
        return user_config_dir(environ), "settings.json"
    top = git_toplevel(cwd or os.getcwd())
    if not top:
        raise InstallError("--scope %s needs a git repository (run from inside one)" % scope)
    return (os.path.join(top, ".claude"),
            "settings.local.json" if scope == "local" else "settings.json")


def _mkdir_private(parent_fd, name, mode):
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        os.mkdir(name, mode, dir_fd=parent_fd)


def _open_target_dir(directory, create, dir_mode):
    """fd of `directory`; its final component must not be a symlink. Caller closes."""
    directory = os.path.abspath(directory)
    parent, name = os.path.split(directory)
    pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if create:
            _mkdir_private(pfd, name, dir_mode)
        return _open_dir(pfd, name, False)
    finally:
        os.close(pfd)


def read_file(dfd, name):
    """(raw bytes, st_mode) of the file or (None, None). Refuses symlinks/non-regular files."""
    try:
        st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(st.st_mode):
        raise Refused("%s is a symlink or not a regular file" % name)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise Refused("%s is a symlink" % name)
        raise
    with os.fdopen(fd, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise InstallError("%s is larger than %d bytes; refusing" % (name, MAX_BYTES))
    return data, stat.S_IMODE(st.st_mode)


def _write_new(dfd, name, data, mode, exclusive):
    """Atomic write of `data`: temp file + fsync + rename (or link-free O_EXCL for backups)."""
    tmp = ".%s.tmp%d" % (name, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:  # never replace an existing backup
            try:
                os.link(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
            except FileExistsError:
                pass
            os.unlink(tmp, dir_fd=dfd)
        else:
            os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    try:
        os.fsync(dfd)
    except OSError:
        pass


def parse_settings(raw, label):
    if raw is None:
        return {}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InstallError("%s is not valid UTF-8; refusing to touch it" % label)
    if not text.strip():
        raise InstallError("%s is empty (not valid JSON); refusing to touch it" % label)
    try:
        data = json.loads(text)
    except (RecursionError, MemoryError):
        raise InstallError("%s is too deeply nested or too large to process; refusing to touch it"
                           % label)
    except ValueError as e:
        raise InstallError("%s is not valid JSON (%s); refusing to touch it" % (label, e))
    _check_shape(data)
    return data


def dump(settings):
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ operations

def apply(scope, action, dry_run=False, cwd=None, environ=None, python=None):
    """action 'install' | 'uninstall'. Returns a dict describing what happened; raises
    InstallError / Refused for user errors."""
    directory, name = target_for(scope, cwd, environ)
    path = os.path.join(directory, name)
    create = action == "install" and not dry_run
    dir_mode = 0o700 if scope == "user" else 0o755
    new_file_mode = 0o644 if scope == "project" else 0o600
    try:
        dfd = _open_target_dir(directory, create, dir_mode)
    except FileNotFoundError:
        dfd = None
    except Refused as e:
        hint = ""
        if os.path.islink(directory):
            hint = ("; to use the real directory run: CLAUDE_CONFIG_DIR=%s mnemo %s claude-code"
                    % (os.path.realpath(directory), action)) if scope == "user" else ""
        raise InstallError("%s%s" % (e, hint))
    except OSError as e:
        raise InstallError("cannot open %s: %s" % (directory, e.strerror or e))
    try:
        if dfd is None:
            raw, mode = None, None
        else:
            raw, mode = read_file(dfd, name)
        settings = parse_settings(raw, path)
        new = merge(settings, python) if action == "install" else remove(settings)
        old_text = raw.decode("utf-8") if raw is not None else ""
        new_text = dump(new)
        changed = new != settings or (raw is None and action == "install")
        if raw is None and action == "uninstall":
            changed = False
        res = {"path": path, "scope": scope, "action": action, "dry_run": dry_run,
               "changed": changed, "created": raw is None and changed, "backup": None,
               "diff": "", "events": installed_events(new)}
        if not changed:
            return res
        backup = name + BACKUP_SUFFIX
        # uninstall that leaves nothing but `{}` and has no backup (so the installer created the
        # file): delete it, and its directory if that is now empty
        delete = (action == "uninstall" and new == {} and dfd is not None
                  and not _exists(dfd, backup))
        res["deleted"] = delete
        res["diff"] = "".join(difflib.unified_diff(
            old_text.splitlines(True), [] if delete else new_text.splitlines(True),
            fromfile=path if raw is not None else "/dev/null",
            tofile="/dev/null" if delete else path))
        if dry_run:
            return res
        if delete:
            os.unlink(name, dir_fd=dfd)
            try:
                os.fsync(dfd)
            except OSError:
                pass
            res["removed_dir"] = _rmdir_if_empty(directory, dfd)
            return res
        # the backup protects the user's own content; an Engram-only file has none to protect
        if raw is not None and new != {} and not _exists(dfd, backup):
            _write_new(dfd, backup, raw, mode, exclusive=True)
            res["backup"] = os.path.join(directory, backup)
        _write_new(dfd, name, new_text.encode("utf-8"),
                   mode if mode is not None else new_file_mode, exclusive=False)
        return res
    finally:
        if dfd is not None:
            os.close(dfd)


def _rmdir_if_empty(directory, dfd):
    """Remove `directory` only if it has no entries (never a symlink: dfd was opened NOFOLLOW)."""
    try:
        if os.listdir(dfd):
            return False
        parent, name = os.path.split(os.path.abspath(directory))
        pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.rmdir(name, dir_fd=pfd)
        finally:
            os.close(pfd)
        return True
    except OSError:
        return False


def _exists(dfd, name):
    try:
        os.stat(name, dir_fd=dfd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def status(scope, cwd=None, environ=None):
    directory, name = target_for(scope, cwd, environ)
    path = os.path.join(directory, name)
    try:
        dfd = _open_target_dir(directory, False, 0o700)
    except FileNotFoundError:
        return {"scope": scope, "path": path, "exists": False, "events": [], "commands": []}
    try:
        raw, _ = read_file(dfd, name)
    finally:
        os.close(dfd)
    if raw is None:
        return {"scope": scope, "path": path, "exists": False, "events": [], "commands": []}
    settings = parse_settings(raw, path)
    return {"scope": scope, "path": path, "exists": True, "events": installed_events(settings),
            "commands": owned_interpreters(settings)}


def mcp_command(scope, python=None):
    return ["claude", "mcp", "add", "--scope", scope, "engram", "--", python or sys.executable,
            "-I", "-m", "engram.mcp_server"]


def register_mcp(scope, out, err):
    """Run `claude mcp add` when a claude binary exists, else print the command. Returns rc."""
    cmd = mcp_command(scope)
    exe = shutil.which("claude")
    if not exe:
        out("`claude` not found on PATH; register the MCP server yourself with:")
        out("  " + " ".join(_quote(c) for c in cmd))
        return 0
    try:
        p = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, timeout=MCP_TIMEOUT_S,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        err("could not run `claude mcp add`: %s" % type(e).__name__)
        out("run it yourself: " + " ".join(_quote(c) for c in cmd))
        return 1
    for stream in (p.stdout, p.stderr):
        if stream.strip():
            out(stream.rstrip())
    if p.returncode != 0:
        err("`claude mcp add` exited %d" % p.returncode)
        return 1
    out("registered MCP server `engram` (scope %s)" % scope)
    return 0


def _quote(s):
    import shlex
    return shlex.quote(s)


PRIVACY = (
    "Privacy: Engram journals your prompts and Claude's final replies for each turn (secrets "
    "matching known patterns are masked before storage; best effort, not a guarantee) and "
    "distils memories from them. Everything stays on this machine in ~/.engram (or "
    "$ENGRAM_HOME); no network calls, no embedding API.")


def run(args, out=None, err=None):
    """Entry point for `mnemo install|uninstall claude-code`. Never raises; returns exit code."""
    out = out or (lambda s: sys.stdout.write(s + "\n"))
    err = err or (lambda s: sys.stderr.write("mnemo: %s\n" % s))
    try:
        return _run(args, out, err)
    except (InstallError, Refused) as e:
        err("%s" % e)
        return EXIT_USER
    except OSError as e:
        err("%s%s" % (e.strerror or type(e).__name__,
                      " (%s)" % e.filename if e.filename else ""))
        return EXIT_USER
    except (RecursionError, MemoryError):
        err("the settings file is too deeply nested or too large to process; file untouched")
        return EXIT_USER
    except Exception as e:  # never a traceback, and exit 1 is reserved for `--mcp` failures
        err("unexpected error: %s; the settings file was not modified" % type(e).__name__)
        return EXIT_USER


def _run(args, out, err):
    cmd = args.cmd
    host = getattr(args, "host", None)
    if getattr(args, "print", None) or host == "cursor" or host is None:
        from engram import install_hosts
        if getattr(args, "print", None):
            return install_hosts.run_print(args, out, err)
        if host == "cursor":
            return install_hosts.run_cursor(args, out, err)
        raise InstallError("name a host (claude-code or cursor), or use --print mcp")
    scope = getattr(args, "scope", None)
    if cmd == "install" and getattr(args, "status", False):
        rc = 0
        for sc in ([scope] if scope else SCOPES):
            try:
                st = status(sc)
            except InstallError as e:
                if scope:
                    raise
                out("%-7s unavailable (%s)" % (sc, e))
                continue
            state = ("installed (%s)" % ", ".join(st["events"])) if st["events"] else (
                "not installed" if st["exists"] else "no settings file")
            out("%-7s %s: %s" % (sc, st["path"], state))
            for c in st["commands"]:
                note = "" if c == sys.executable else "  (differs from this interpreter)"
                out("        interpreter: %s%s" % (c, note))
        return rc
    scope = scope or "user"
    if cmd == "install" and scope == "project":
        err("WARNING: --scope project writes .claude/settings.json, which is normally committed: "
            "it will capture the prompts of EVERY contributor who opens this repo in Claude Code. "
            "Prefer --scope local (uncommitted) or --scope user.")
    res = apply(scope, cmd, dry_run=args.dry_run)
    if res["dry_run"]:
        out("dry run: %s %s" % (res["path"], "would change" if res["changed"] else "unchanged"))
        if res["diff"]:
            out(res["diff"].rstrip("\n"))
        return 0
    if not res["changed"]:
        out("unchanged: %s (%s)" % (res["path"],
            "Engram hooks already installed" if cmd == "install" else "no Engram hooks found"))
        rc = 0
    elif cmd == "install":
        out("%s: %s" % ("created" if res["created"] else "updated", res["path"]))
        out("hooks written for: %s" % ", ".join(res["events"]))
        out("command: %s %s" % (sys.executable, " ".join(OWNED_ARGS)))
        rc = 0
    elif res.get("deleted"):
        out("removed: %s (it contained only Engram's hooks)%s" % (
            res["path"], "; removed the now-empty directory" if res.get("removed_dir") else ""))
        rc = 0
    else:
        out("updated: %s (Engram hooks removed; nothing else touched)" % res["path"])
        rc = 0
    if res["backup"]:
        out("backup of the previous file: %s" % res["backup"])
    if cmd == "install":
        if getattr(args, "mcp", False):
            rc = register_mcp(scope, out, err) or rc
        out("verify: `mnemo status`, then start a Claude Code session and send a prompt")
        out("disable: `mnemo uninstall claude-code --scope %s`, or set ENGRAM_PORT=1 to stop "
            "daemon autostart" % scope)
        out(PRIVACY)
    else:
        out("the MCP server (if registered) stays; remove it with `claude mcp remove engram`")
    return rc
