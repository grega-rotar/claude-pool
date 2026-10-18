#!/bin/sh
# Two real Linux users sharing one pool (README "Option B"). Needs root: run it in a container or with sudo.
#   docker run --rm -v "$PWD":/src:ro python:3.12-slim sh /src/tests/multiuser.sh
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
fail() { echo "FAIL: $*"; exit 1; }

groupadd -f claude-pool
for u in alice bob; do id "$u" >/dev/null 2>&1 || useradd -m "$u"; usermod -aG claude-pool "$u"; done
id carol >/dev/null 2>&1 || useradd -m carol   # not in the group

POOL=/tmp/cp-multi/pool STATE=/tmp/cp-multi/state
rm -rf /tmp/cp-multi && mkdir -p /tmp/cp-multi/src && chmod 755 /tmp/cp-multi /tmp/cp-multi/src
# The test users can't read the checkout (CI keeps it in a private home), so run a copy everyone can read.
SRC=/tmp/cp-multi/src && cp "$REPO/claude_pool.py" "$SRC/" && chmod 644 "$SRC/claude_pool.py"
install -d -m 2770 -g claude-pool "$POOL" "$STATE"
# A fake claude every user can run: reports usage and echoes the prompt.
cat > /usr/local/bin/fake-claude <<'EOF'
#!/usr/bin/env python3
import json, sys, time
r = int(time.time()) + 3600
print(json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": 0.3, "resetsAt": r}, "seven_day": {"utilization": 0.2, "resetsAt": r + 86400}}}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok:" + sys.argv[2]}))
EOF
chmod 755 /usr/local/bin/fake-claude

as() { user=$1; shift
  runuser -u "$user" -- env HOME="/home/$user" CLAUDE_POOL_DIR="$POOL" CLAUDE_POOL_STATE="$STATE/usage.json" \
    CLAUDE_POOL_BINARY=fake-claude CLAUDE_POOL_FRESH=300 sh -c "umask 022; python3 $SRC/claude_pool.py $*"; }

# Root adds the tokens as the README says; in the shared folder they must come out 640, group claude-pool.
for t in one two; do
  echo "tok-$t" | env CLAUDE_POOL_DIR="$POOL" CLAUDE_POOL_STATE=/tmp/cp-multi/root.json CLAUDE_POOL_BINARY=fake-claude \
    python3 "$SRC/claude_pool.py" --pool-add "$t" >/dev/null || fail "root's --pool-add $t"
  [ "$(stat -c %a:%G "$POOL/$t.token")" = "640:claude-pool" ] || fail "$t.token is $(stat -c %a:%G "$POOL/$t.token")"
done

[ "$(as alice -p hi </dev/null)" = "ok:hi" ] || fail "alice's run"
[ "$(as bob -p hi </dev/null)" = "ok:hi" ] || fail "bob's run after alice (shared lock and usage files)"
as alice -p again </dev/null >/dev/null || fail "alice again after bob"
as bob --pool-refresh </dev/null >/dev/null || fail "bob's refresh"

runs=$(python3 -c "import json; u = json.load(open('$STATE/usage.json')); print(sum(r.get('runs', 0) for r in u.values()))")
[ "$runs" = 3 ] || fail "3 runs counted across both users, got $runs"
for f in "$STATE"/usage.json "$STATE"/usage.json.lock "$STATE"/events.log; do
  [ "$(stat -c %a:%G "$f")" = "660:claude-pool" ] || fail "$f is $(stat -c %a:%G "$f"), want 660:claude-pool"
done
grep -q '"event": "run"' "$STATE/events.log" || fail "both users log to one events.log"

# A user outside the group gets a clear error, not a traceback.
out=$(as carol --pool-check </dev/null 2>&1) && fail "carol's check should fail"
echo "$out" | grep -q "add this user to the folder's group" || fail "carol's message: $out"
echo "$out" | grep -q Traceback && fail "carol got a traceback: $out"
out=$(as carol -p hi </dev/null 2>&1) && fail "carol's run should fail"
echo "$out" | grep -q "add this user to the folder's group" || fail "carol's run message: $out"
echo "$out" | grep -q Traceback && fail "carol's run got a traceback: $out"

echo "multi-user OK: alice and bob share one pool; carol is kept out"
