#!/usr/bin/env bash
# Self-test for scripts/board.py: claim race over a bare remote, deps, lease expiry, check.
set -euo pipefail
here="$(cd "$(dirname "$0")/../.." && pwd)"
board="${BOARD_PY:-$here/scripts/board.py}"
command -v python3 >/dev/null || { echo "test-board: python3 missing, skipped"; exit 0; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
fail() { echo "test-board FAIL: $*" >&2; exit 1; }

git init -q --bare -b main "$tmp/remote.git"
git clone -q "$tmp/remote.git" "$tmp/seed" 2>/dev/null
mkdir -p "$tmp/seed/work/tasks"
printf '{"integration_branch":"main","lease_hours":6}\n' > "$tmp/seed/work/config.json"
card() { printf -- '---\nid: %s\ntitle: Task %s\nkind: lib\ntier: N\nneeds: [%s]\nsoft: []\nverify: Linux CI\n---\n\n## Brief\nx\n' "$1" "$1" "$2" > "$tmp/seed/work/tasks/$1.md"; }
card T01 ""; card T02 ""; card T03 "T01"
( cd "$tmp/seed" && BOARD_ROOT=. python3 "$board" render --no-push >/dev/null \
  && git add -A && git commit -qm seed && git push -q origin HEAD:main )
git clone -q "$tmp/remote.git" "$tmp/a"; git clone -q "$tmp/remote.git" "$tmp/b"
A() { ( cd "$tmp/a" && BOARD_ROOT=. python3 "$board" "$@" ); }
B() { ( cd "$tmp/b" && BOARD_ROOT=. python3 "$board" "$@" ); }

A check >/dev/null || fail "check on a clean board"
A next | grep -q '^T01' || fail "T01 should be ready"
A next | grep -q '^T03' && fail "T03 depends on T01, must not be offered"
A claim T01 --agent alice >/dev/null || fail "alice claims T01"
# bob has not pulled; his claim of T01 must lose the race (or see it taken after sync)
if B claim T01 --agent bob >/dev/null 2>&1; then fail "bob must not get T01"; fi
B claim T02 --agent bob >/dev/null || fail "bob claims T02"
A claim T03 --agent alice >/dev/null 2>&1 && fail "T03 blocked by unmerged T01"
A submit T01 --agent alice --branch feat/t01 >/dev/null || fail "submit"
A merge T01 >/dev/null || fail "merge"
A next | grep -q '^T03' || fail "T03 ready after T01 merged"
# lease expiry frees a claimed task
( cd "$tmp/b" && git pull -q origin main && python3 - <<'P'
import json
p="work/status/T02.json"; d=json.load(open(p)); d["heartbeat_at"]="2000-01-01T00:00:00Z"; d["claimed_at"]="2000-01-01T00:00:00Z"
json.dump(d,open(p,"w"))
P
)
B next --no-push | grep -q '^T02' || fail "expired lease should free T02"
A check >/dev/null || fail "final check"
echo "test-board: OK"
