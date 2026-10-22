#!/usr/bin/env python3
"""board.py - an agent-agnostic task board kept in plain files inside the repository.

Any AI system (Claude app/CLI, ChatGPT, Cursor, Codex, Grok, ...) or a human can use it, because
the "API" is just files plus git:

  work/tasks/<ID>.md      one task card per task (front matter + a self-contained brief)
  work/status/<ID>.json   one tiny live-status file per task (so two tasks never conflict)
  work/BOARD.md           generated table: the first thing an agent reads
  work/config.json        integration branch, lease length

States: not_started -> claimed -> in_review -> merged   (also: blocked)
Rule for every agent: pick ONLY a task that `next` lists. Never take a claimed, in_review,
merged or blocked task. A claim whose lease expired (agent died / hit its limit) is free again.

Stdlib only. Run `python3 scripts/board.py --help`.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

STATES = ("not_started", "claimed", "in_review", "merged", "blocked")


# ---------------------------------------------------------------------------------- basics
def utcnow():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def die(msg, code=1):
    print("board: " + msg, file=sys.stderr)
    sys.exit(code)


def git(*args, check=False):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode != 0:
        die("git %s failed: %s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r


def find_root(start):
    p = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(p, "work")) or os.path.isdir(os.path.join(p, ".git")):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return os.path.abspath(start)
        p = parent


ROOT = find_root(os.environ.get("BOARD_ROOT", "."))
WORK = os.path.join(ROOT, "work")


def config():
    path = os.path.join(WORK, "config.json")
    base = {"integration_branch": "main", "lease_hours": 6, "batch_prefix": "ci-batch-"}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            base.update(json.load(f))
    return base


# ---------------------------------------------------------------------------------- cards
def parse_list(v):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip() for x in v.split(",") if x.strip()]


def load_cards():
    cards = {}
    d = os.path.join(WORK, "tasks")
    if not os.path.isdir(d):
        return cards
    for name in sorted(os.listdir(d)):
        if not name.endswith(".md"):
            continue
        text = open(os.path.join(d, name), encoding="utf-8").read()
        m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
        if not m:
            die("%s: missing front matter" % name)
        meta = {"body": m.group(2)}
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.split("  #")[0].strip()
        meta["needs"] = parse_list(meta.get("needs", ""))
        meta["soft"] = parse_list(meta.get("soft", ""))
        meta["file"] = name
        if "id" not in meta:
            die("%s: no id" % name)
        cards[meta["id"]] = meta
    return cards


def status_path(tid):
    return os.path.join(WORK, "status", tid + ".json")


def load_status(tid):
    p = status_path(tid)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"id": tid, "state": "not_started"}


def save_status(st):
    os.makedirs(os.path.join(WORK, "status"), exist_ok=True)
    st["updated_at"] = iso(utcnow())
    with open(status_path(st["id"]), "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, sort_keys=True)
        f.write("\n")


def lease_expired(st):
    if st.get("state") != "claimed" or not st.get("claimed_at"):
        return False
    hours = float(st.get("lease_hours", config()["lease_hours"]))
    last = parse_iso(st.get("heartbeat_at") or st["claimed_at"])
    return utcnow() > last + dt.timedelta(hours=hours)


def effective_state(st):
    return "claimed (lease expired - free)" if lease_expired(st) else st.get("state", "not_started")


def is_free(st):
    return st.get("state", "not_started") == "not_started" or lease_expired(st)


def readiness(card, statuses):
    """('ready'|'ready_on_batch'|'waiting', blocking ids)."""
    waiting, on_batch = [], []
    for dep in card["needs"]:
        s = statuses.get(dep, {}).get("state", "not_started")
        if s == "merged":
            continue
        (on_batch if s == "in_review" else waiting).append(dep)
    if waiting:
        return "waiting", waiting
    if on_batch:
        return "ready_on_batch", on_batch
    return "ready", []


# ---------------------------------------------------------------------------------- render
def render():
    cards = load_cards()
    sts = {t: load_status(t) for t in cards}
    counts = {s: 0 for s in STATES}
    for t in cards:
        counts[sts[t].get("state", "not_started")] += 1
    cfg = config()
    out = ["# Task board", "",
           "> Generated by `python3 scripts/board.py render` - do not edit by hand. "
           "Protocol: [AGENTS.md](../AGENTS.md) and [work/README.md](README.md).", "",
           "Integration branch: `%s`. " % cfg["integration_branch"]
           + " | ".join("%s %d" % (s, counts[s]) for s in STATES), "",
           "| Task | Title | Tier | State | Owner | Needs | Branch / note |",
           "|---|---|---|---|---|---|---|"]
    for t in sorted(cards, key=task_key):
        c, st = cards[t], sts[t]
        state = effective_state(st)
        if st.get("state") == "not_started":
            r, blockers = readiness(c, sts)
            state = {"ready": "**not_started - READY**",
                     "ready_on_batch": "not_started - ready on batch branch",
                     "waiting": "not_started (waits for %s)" % ", ".join(blockers)}[r]
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            t, c.get("title", ""), c.get("tier", "N"), state, st.get("owner", ""),
            ", ".join(c["needs"]) or "-", " ".join(x for x in (st.get("branch", ""), st.get("note", "")) if x)))
    with open(os.path.join(WORK, "BOARD.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def task_key(t):
    m = re.match(r"([A-Za-z]*)(\d+)", t)
    return (m.group(1), int(m.group(2))) if m else (t, 0)


# ---------------------------------------------------------------------------------- git glue
def in_git():
    return git("rev-parse", "--is-inside-work-tree").returncode == 0


def commit_paths(paths, message, push, strict=False):
    """Commit `paths` and optionally push. strict=True (claims): a rejected push means we lost
    the race, so undo our commit and return False."""
    if not in_git():
        return True
    git("add", "--", *paths)
    if git("diff", "--cached", "--quiet").returncode == 0:
        return True
    git("commit", "-q", "-m", message, check=True)
    if not push:
        return True
    for attempt in range(1 if strict else 3):
        if git("push", "-q").returncode == 0:
            return True
        if strict:
            break
        git("pull", "-q", "--rebase", "--autostash")
    if strict:
        git("reset", "-q", "--hard", "HEAD~1")
        git("pull", "-q", "--rebase", "--autostash")
        return False
    die("push failed; run `git pull --rebase` and retry")


def sync(push):
    if push and in_git() and git("rev-parse", "--abbrev-ref", "@{u}").returncode == 0:
        git("pull", "-q", "--rebase", "--autostash")


# ---------------------------------------------------------------------------------- commands
def need_card(tid):
    cards = load_cards()
    if tid not in cards:
        die("unknown task %s" % tid)
    return cards[tid], cards


def cmd_status(a):
    cards = load_cards()
    sts = {t: load_status(t) for t in cards}
    if a.json:
        print(json.dumps({t: dict(sts[t], needs=cards[t]["needs"], effective=effective_state(sts[t]))
                          for t in sorted(cards, key=task_key)}, indent=2))
        return
    print(open(os.path.join(WORK, "BOARD.md"), encoding="utf-8").read() if os.path.exists(
        os.path.join(WORK, "BOARD.md")) else "no BOARD.md; run render")


def cmd_next(a):
    cards = load_cards()
    sts = {t: load_status(t) for t in cards}
    rows = []
    for t in sorted(cards, key=task_key):
        if not is_free(sts[t]):
            continue
        if a.kind and cards[t].get("kind") != a.kind:
            continue
        if a.tier and cards[t].get("tier", "N") != a.tier:
            continue
        r, blockers = readiness(cards[t], sts)
        if r == "ready" or (r == "ready_on_batch" and a.include_batch):
            rows.append({"id": t, "title": cards[t].get("title", ""), "tier": cards[t].get("tier", "N"),
                         "kind": cards[t].get("kind", ""), "readiness": r, "base_on_pending": blockers,
                         "card": "work/tasks/" + cards[t]["file"], "wip_branch": sts[t].get("wip_branch", "")})
    if a.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print("nothing ready (everything is claimed, in review, merged or waiting on dependencies)")
    else:
        for r in rows:
            extra = " [base on %s]" % ",".join(r["base_on_pending"]) if r["base_on_pending"] else ""
            wip = " [wip branch: %s]" % r["wip_branch"] if r["wip_branch"] else ""
            print("%s  tier %s  %s%s%s\n    card: %s" % (r["id"], r["tier"], r["title"], extra, wip, r["card"]))


def cmd_claim(a):
    card, cards = need_card(a.id)
    sync(not a.no_push)
    st = load_status(a.id)
    if not is_free(st):
        die("%s is %s (owner %s) - pick another task" % (a.id, effective_state(st), st.get("owner", "?")), 3)
    sts = {t: load_status(t) for t in cards}
    r, blockers = readiness(card, sts)
    if r == "waiting" or (r == "ready_on_batch" and not a.on_batch):
        die("%s depends on %s which is not merged%s" % (
            a.id, ", ".join(blockers), " (pass --on-batch to build on the pending batch branch)" if r != "waiting" else ""), 4)
    now = utcnow()
    keep = {k: st[k] for k in ("wip_branch",) if k in st}
    st = dict(keep, id=a.id, state="claimed", owner=a.agent, claimed_at=iso(now), heartbeat_at=iso(now),
              lease_hours=a.lease_hours or config()["lease_hours"])
    if a.branch:
        st["branch"] = a.branch
    save_status(st)
    render()
    ok = commit_paths(["work/status/%s.json" % a.id, "work/BOARD.md"], "board: %s claimed by %s" % (a.id, a.agent),
                      not a.no_push, strict=True)
    if not ok:
        die("lost the race for %s - someone claimed it first; run `next` again" % a.id, 5)
    print("claimed %s for %s (lease %sh). Card: work/tasks/%s" % (a.id, a.agent, st["lease_hours"], card["file"]))


def _update(a, mutate, message):
    need_card(a.id)
    sync(not a.no_push)
    st = load_status(a.id)
    mutate(st)
    save_status(st)
    render()
    commit_paths(["work/status/%s.json" % a.id, "work/BOARD.md"], message, not a.no_push)


def _owner_check(st, a):
    if st.get("owner") and getattr(a, "agent", None) and st["owner"] != a.agent and not a.force:
        die("%s is owned by %s, not %s (use --force to override)" % (st["id"], st["owner"], a.agent), 6)


def cmd_heartbeat(a):
    def m(st):
        _owner_check(st, a)
        if st.get("state") != "claimed":
            die("%s is not claimed" % a.id, 6)
        st["heartbeat_at"] = iso(utcnow())
    _update(a, m, "board: %s heartbeat" % a.id)


def cmd_submit(a):
    def m(st):
        _owner_check(st, a)
        st.update(state="in_review", branch=a.branch, note=a.note or "")
        if a.commit:
            st["commit"] = a.commit
    _update(a, m, "board: %s submitted (%s)" % (a.id, a.branch))


def cmd_merge(a):
    def m(st):
        st.update(state="merged", note=a.note or "")
        for k in ("claimed_at", "heartbeat_at", "lease_hours"):
            st.pop(k, None)
        if a.commit:
            st["commit"] = a.commit
    _update(a, m, "board: %s merged" % a.id)


def cmd_release(a):
    def m(st):
        keep = {k: st[k] for k in ("wip_branch",) if k in st}
        if a.wip_branch:
            keep["wip_branch"] = a.wip_branch
        st.clear()
        st.update(keep, id=a.id, state="not_started", note=a.note or "")
    _update(a, m, "board: %s released" % a.id)


def cmd_block(a):
    def m(st):
        st.update(state="blocked", note=a.reason)
    _update(a, m, "board: %s blocked" % a.id)


def cmd_render(a):
    render()
    print("work/BOARD.md updated")


def cmd_check(a):
    cards, errs = load_cards(), []
    if not cards:
        die("no task cards in work/tasks")
    for t, c in cards.items():
        for d in c["needs"] + c["soft"]:
            if d not in cards:
                errs.append("%s needs unknown task %s" % (t, d))
    state = {}

    def visit(t, path):
        if state.get(t) == 2:
            return
        if state.get(t) == 1:
            errs.append("dependency cycle: " + " -> ".join(path + [t]))
            return
        state[t] = 1
        for d in cards.get(t, {}).get("needs", []):
            if d in cards:
                visit(d, path + [t])
        state[t] = 2
    for t in cards:
        visit(t, [])
    sdir = os.path.join(WORK, "status")
    if os.path.isdir(sdir):
        for n in sorted(os.listdir(sdir)):
            if not n.endswith(".json"):
                continue
            st = json.load(open(os.path.join(sdir, n), encoding="utf-8"))
            if st.get("id") != n[:-5] or n[:-5] not in cards:
                errs.append("%s: id mismatch or no matching card" % n)
            if st.get("state") not in STATES:
                errs.append("%s: bad state %r" % (n, st.get("state")))
            if st.get("state") == "claimed" and not (st.get("owner") and st.get("claimed_at")):
                errs.append("%s: claimed without owner/claimed_at" % n)
            if st.get("state") == "merged":
                for d in cards.get(n[:-5], {}).get("needs", []):
                    if load_status(d).get("state") != "merged":
                        errs.append("%s merged but its dependency %s is not" % (n[:-5], d))
    board = os.path.join(WORK, "BOARD.md")
    if os.path.exists(board):
        before = open(board, encoding="utf-8").read()
        render()
        if open(board, encoding="utf-8").read() != before:
            errs.append("work/BOARD.md was stale (re-rendered now; commit it)")
    if errs:
        for e in errs:
            print("board check: " + e, file=sys.stderr)
        sys.exit(1)
    print("board check: ok (%d tasks)" % len(cards))


def cmd_import(a):
    """Create cards from a markdown file with '### T12 [tag] Title' headings."""
    text = open(a.file, encoding="utf-8").read()
    parts = re.split(r"(?m)^### (?=[A-Z]+\d+ )", text)[1:]
    os.makedirs(os.path.join(WORK, "tasks"), exist_ok=True)
    made = 0
    for part in parts:
        head, _, body = part.partition("\n")
        m = re.match(r"([A-Z]+\d+)\s+(?:\[([\w-]+)\]\s+)?(.*)", head)
        tid, kind, title = m.group(1), m.group(2) or "", m.group(3).strip()
        needs, soft = [], []
        dm = re.search(r"\*\*Depends on:\*\*\s*([^\n]*)", body) or re.search(r"Depends on:\*\*\s*([^\n]*)", body)
        if dm:
            line = dm.group(1).split("**ACs")[0]
            for p in re.findall(r"\(([^)]*)\)", line):
                soft += re.findall(r"[A-Z]+\d+", p)
            hard = re.sub(r"\([^)]*\)", "", line)
            needs = [x for x in re.findall(r"[A-Z]+\d+", hard)]
        path = os.path.join(WORK, "tasks", tid + ".md")
        if os.path.exists(path) and not a.force:
            continue
        verify = "Linux CI (core-linux)" if title.endswith("Linux") or "Linux" in head else "macOS CI (apple-macos)"
        fm = ["---", "id: " + tid, "title: " + re.sub(r"\s*\.\s*(Linux|macOS.*)$", "", title).rstrip("."),
              "kind: " + kind, "tier: " + a.default_tier, "needs: [%s]" % ", ".join(needs),
              "soft: [%s]" % ", ".join(soft), "verify: " + verify, "---", ""]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(fm) + "\n## Brief\n\n" + body.strip() + "\n")
        made += 1
    print("created %d card(s) in work/tasks" % made)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, tid=True, **kw):
        p = sp.add_parser(name, **kw)
        if tid:
            p.add_argument("id")
        p.add_argument("--no-push", action="store_true", help="do not git pull/push (local/testing)")
        p.set_defaults(f=fn)
        return p
    p = add("status", cmd_status, tid=False, help="show the board"); p.add_argument("--json", action="store_true")
    p = add("next", cmd_next, tid=False, help="list tasks you may claim")
    p.add_argument("--json", action="store_true"); p.add_argument("--kind"); p.add_argument("--tier")
    p.add_argument("--include-batch", action="store_true", help="also tasks whose deps are in_review")
    p = add("claim", cmd_claim, help="atomically claim a task (git push decides races)")
    p.add_argument("--agent", required=True); p.add_argument("--lease-hours", type=float)
    p.add_argument("--branch"); p.add_argument("--on-batch", action="store_true")
    p = add("heartbeat", cmd_heartbeat); p.add_argument("--agent", required=True); p.add_argument("--force", action="store_true")
    p = add("submit", cmd_submit, help="mark in_review (your branch is pushed, CI is running)")
    p.add_argument("--agent", required=True); p.add_argument("--branch", required=True)
    p.add_argument("--commit"); p.add_argument("--note"); p.add_argument("--force", action="store_true")
    p = add("merge", cmd_merge, help="integrator only: mark merged"); p.add_argument("--commit"); p.add_argument("--note")
    p = add("release", cmd_release, help="give a task back"); p.add_argument("--note"); p.add_argument("--wip-branch")
    p = add("block", cmd_block); p.add_argument("--reason", required=True)
    add("render", cmd_render, tid=False, help="regenerate work/BOARD.md")
    add("check", cmd_check, tid=False, help="validate cards, statuses and BOARD.md (cheap CI lint)")
    p = sp.add_parser("import-tasks", help="create cards from a '### T01 [kind] Title' markdown file")
    p.add_argument("file"); p.add_argument("--force", action="store_true"); p.add_argument("--default-tier", default="N")
    p.set_defaults(f=cmd_import)
    a = ap.parse_args()
    if a.cmd == "status" and not os.path.isdir(WORK):
        die("no work/ directory here")
    a.f(a)


if __name__ == "__main__":
    main()
