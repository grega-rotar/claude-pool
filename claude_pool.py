#!/usr/bin/env python3
r"""claude-pool: run `claude` on whichever of your accounts has the most usage left.

Drop-in for the Claude Code CLI: `claude-pool -p "hi" --output-format json` takes the same arguments and prints
the same output as `claude`. Before each run it picks a free account, and in print mode (-p) a run cut off by a
usage limit is rerun at once on the next free account.

Accounts: one file per account in $CLAUDE_POOL_DIR (default ~/.config/claude-pool, on Windows %APPDATA%\claude-pool):
  <name>.token   an OAuth token from `claude setup-token` (Claude subscription)
  <name>.key     an Anthropic API key
Rename a file to <name>.token.off to take the account out. The folder is reread on every run. With no files,
claude-pool runs claude with your normal login, so it is safe to install before you add any accounts.

By default it drains first the usage that expires first (CLAUDE_POOL_STRATEGY, see the README).

Usage is read from the `rate_limit_event` that `claude -p --output-format stream-json --verbose` emits, so in
print mode claude-pool always runs claude that way and converts the output back to the format you asked for.
It is kept in $CLAUDE_POOL_STATE (default ~/.local/state/claude-pool/usage.json), shared by every caller under
a file lock. Numbers older than CLAUDE_POOL_FRESH seconds (default 300) are refreshed before a pick with one tiny
Haiku call per account (about $0.0001, 2-3 s, all accounts in parallel), so use by other apps is seen too.
`claude-pool --pool-refresh` refreshes every account now.

Other settings: CLAUDE_POOL_BINARY (the real claude, default `claude`), CLAUDE_POOL_ACCOUNT (always use this
account), CLAUDE_POOL_LIMIT (percent of a window that counts as full, default 100).
`claude-pool --pool-status` lists the accounts and their last known usage.
"""
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

WINDOWS = os.name == "nt"
if WINDOWS:
    import msvcrt
else:
    import fcntl

__version__ = "0.4.1"

EXIT_EXHAUSTED = 75  # EX_TEMPFAIL: every account is at its limit, try again after the reset


def pool_dir():
    if os.environ.get("CLAUDE_POOL_DIR"):
        return os.path.expanduser(os.environ["CLAUDE_POOL_DIR"])
    if WINDOWS:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "claude-pool")
    return os.path.expanduser("~/.config/claude-pool")


def state_path():
    if os.environ.get("CLAUDE_POOL_STATE"):
        return os.path.expanduser(os.environ["CLAUDE_POOL_STATE"])
    if WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "claude-pool", "usage.json")


def share(path):
    """In a group-writable folder (several users sharing one pool), make our file group-writable too, so the
    other users can lock and replace it. Elsewhere files keep the user's umask."""
    if WINDOWS:
        return
    try:
        if os.stat(os.path.dirname(path) or ".").st_mode & stat.S_IWGRP and os.stat(path).st_uid == os.getuid():
            os.chmod(path, 0o660)
    except OSError:
        pass


@contextmanager
def locked(path):
    """An exclusive lock on `path` across processes, held for the with-block."""
    with open(path, "a+") as f:
        share(path)
        if not WINDOWS:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
            return
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError:  # LK_LOCK gives up after about 10 s; keep waiting like flock does
                time.sleep(0.1)
        try:
            yield
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def replace(src, dst):
    """os.replace, retried on Windows while another process has `dst` open for a moment."""
    for _ in range(50):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if not WINDOWS:
                raise
            time.sleep(0.05)
    os.replace(src, dst)


def limit_pct():
    return float(os.environ.get("CLAUDE_POOL_LIMIT") or 100)


def accounts():
    """{name: (kind, secret)} for every <name>.token / <name>.key in the pool folder."""
    out, d = {}, pool_dir()
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        name, ext = os.path.splitext(f)
        if ext in (".token", ".key"):
            try:
                with open(os.path.join(d, f)) as fh:
                    secret = fh.read().strip()
            except OSError as e:  # another user's file without group read: skip it, don't fail the run
                print(f"claude-pool: skipping {f}: {e.strerror}", file=sys.stderr)
                continue
            if secret:
                out[name] = ("token" if ext == ".token" else "key", secret)
    return out


def load_usage():
    try:
        return json.load(open(state_path()))
    except (OSError, ValueError):
        return {}


def at_limit(row, now=None):
    """When the account's last known usage says wait: the reset time (Unix), else None."""
    now, full = now or time.time(), limit_pct()
    week_reset = row.get("seven_day_resets_ts") or 0
    if (row.get("seven_day") or 0) >= full and week_reset > now:
        return week_reset
    reset = row.get("resets_ts") or 0
    if reset > now and (row.get("status") == "rejected" or (row.get("five_hour") or 0) >= full):
        return reset
    return None


def choose(pool, usage, skip=(), now=None):
    """The free account to run on next, else None.

    drain (default): the account whose 7-day window resets soonest, since its unused room is lost first; then the
    most used. Accounts with no live window (never used, or reset since) come last, so they don't start a window
    before it's needed. An account past CLAUDE_POOL_SWITCH_AT percent of its 5-hour window is used only when every
    free account is, so a run is less likely to be cut off midway.
    balance: the lowest 7-day usage (then 5-hour), spreading the load. Never used counts as 0."""
    now = now or time.time()
    row = lambda n: usage.get(n) or {}  # noqa: E731
    free = [n for n in pool if n not in skip and not at_limit(row(n), now) and not broken(row(n), now)]
    if os.environ.get("CLAUDE_POOL_STRATEGY") == "balance":
        return min(free, key=lambda n: (row(n).get("seven_day") or 0, row(n).get("five_hour") or 0), default=None)
    margin = float(os.environ.get("CLAUDE_POOL_SWITCH_AT") or 90)
    roomy = [n for n in free if (row(n).get("five_hour") or 0) < margin or (row(n).get("resets_ts") or 0) <= now]

    def key(n):
        reset = row(n).get("seven_day_resets_ts") or 0
        live = reset > now
        return (not live, reset if live else 0, -(row(n).get("seven_day") or 0) if live else 0, n)
    return min(roomy or free, key=key, default=None)


def fresh_secs():
    return float(os.environ.get("CLAUDE_POOL_FRESH") or 300)


def broken(row, now=None):
    """The account's last check failed without usage numbers (a bad or revoked token): skip it until rechecked."""
    return bool(row.get("error_ts")) and (now or time.time()) - row["error_ts"] < max(fresh_secs(), 300)


PROBE = ["-p", "ok", "--model", "haiku", "--max-turns", "1", "--tools", "", "--system-prompt", "Reply ok.",
         "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
         "--output-format", "stream-json", "--verbose"]


def probe(name, kind, secret, binary):
    """One tiny Haiku call on the account, only to read its usage numbers."""
    try:
        r = subprocess.run([binary] + PROBE, env=account_env(kind, secret), cwd=tempfile.gettempdir(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        out, err = r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        out, err = "", "probe timed out"
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    info = next((e.get("rate_limit_info") for e in reversed(events) if e.get("type") == "rate_limit_event"), None)
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    record(name, info, ran=False, error=None if info else str(final.get("result") or err.strip() or "no usage in reply")[:200])


def refresh(pool, names=None, stale_only=False):
    """Probe the token accounts (API keys have no usage windows) whose numbers are missing or older than
    CLAUDE_POOL_FRESH, or all of `names`. One caller probes at a time; the others wait and reuse its numbers."""
    def due(usage):
        now = time.time()
        return [n for n, (kind, _) in pool.items() if kind == "token" and (names is None or n in names)
                and (not stale_only or now - ((usage.get(n) or {}).get("checked_ts") or 0) >= fresh_secs())]
    if not due(load_usage()):
        return
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with locked(path + ".refresh"):
        binary = real_binary()
        threads = [threading.Thread(target=probe, args=(n, *pool[n], binary)) for n in due(load_usage())]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def record(name, info, ran=True, error=None):
    """Store one run's rate_limit_info for the account, under a lock so parallel callers lose no rows."""
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with locked(path + ".lock"):
        usage = load_usage()
        row = usage.setdefault(name, {})
        if info:
            w = info.get("unifiedWindows") or {}
            pct = lambda k: round(100 * (w.get(k) or {}).get("utilization", 0), 1) if k in w else row.get(k)  # noqa: E731
            row.update(status=info.get("status"), five_hour=pct("five_hour"), seven_day=pct("seven_day"),
                       resets_ts=(w.get("five_hour") or {}).get("resetsAt") or info.get("resetsAt"),
                       seven_day_resets_ts=(w.get("seven_day") or {}).get("resetsAt") or row.get("seven_day_resets_ts"),
                       checked_ts=time.time())
            row.pop("error", None)
            row.pop("error_ts", None)
        elif error:
            row.update(error=error, error_ts=time.time(), checked_ts=time.time())
        if ran:
            row["runs"] = row.get("runs", 0) + 1
            row["last_used"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(path + ".tmp", "w") as f:
            json.dump(usage, f, indent=1)
        share(path + ".tmp")
        replace(path + ".tmp", path)  # whole, so a parallel caller never reads half a file


def account_env(kind, secret):
    env = dict(os.environ)
    if kind == "token":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = secret
        env.pop("ANTHROPIC_API_KEY", None)  # an API key would win over the token and bill the API instead
    else:
        env["ANTHROPIC_API_KEY"] = secret
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env


def cut_off(final, info):
    """The run stopped because the account hit its usage limit."""
    if final.get("is_error") is False and final.get("subtype") == "success":
        return False
    return (info or {}).get("status") == "rejected" or bool(re.search(r"(?i)hit your .*limit", str(final.get("result") or "")))


def flag_value(args, name):
    """(value, index of the flag) for `--name value` or `--name=value`, else (None, None)."""
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1], i
        if a.startswith(name + "="):
            return a.split("=", 1)[1], i
    return None, None


def stream_args(args):
    """The caller's args with --output-format stream-json --verbose, plus (format asked for, --verbose given)."""
    fmt, i = flag_value(args, "--output-format")
    out = list(args)
    if i is not None:
        del out[i:i + (1 if "=" in out[i] else 2)]
    verbose = "--verbose" in out
    out += ["--output-format", "stream-json"] + ([] if verbose else ["--verbose"])
    return out, fmt or "text", verbose


def real_binary():
    b = os.environ.get("CLAUDE_POOL_BINARY") or "claude"
    path = shutil.which(b)
    if not path:
        sys.exit(f"claude-pool: {b} not found; set CLAUDE_POOL_BINARY to the real claude")
    if os.path.realpath(path) == os.path.realpath(__file__):
        sys.exit(f"claude-pool: {b} is claude-pool itself; set CLAUDE_POOL_BINARY to the real claude")
    return path


def piped_stdin(wait=3.0):
    """A prompt piped on stdin, read whole so a rerun can send it again; None for a terminal. Like claude, give
    up when nothing arrives within `wait` seconds, so a caller that leaves stdin open without writing doesn't hang."""
    if sys.stdin is None or sys.stdin.isatty():
        return None
    chunks, started, done = [], threading.Event(), threading.Event()
    fd = sys.stdin.fileno()

    def read():  # a thread, since select() can't wait on a pipe on Windows; raw reads, so exiting never blocks on it
        while True:
            chunk = os.read(fd, 65536)
            started.set()
            if not chunk:
                break
            chunks.append(chunk)
        done.set()
    threading.Thread(target=read, daemon=True).start()
    if not started.wait(wait):
        return b""
    done.wait()
    return b"".join(chunks)


def run_once(cmd, env, data, live):
    """One claude run; returns (exit code, events). With `live`, each stream-json line is written out as it comes."""
    p = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE if data is not None else None, stdout=subprocess.PIPE)
    # So a caller's terminate stops claude too. Windows can only terminate a process, not signal it.
    forward = lambda sig, _f: p.terminate() if WINDOWS else p.send_signal(sig)  # noqa: E731
    sigs = [getattr(signal, s) for s in ("SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, s)]
    old = {s: signal.signal(s, forward) for s in sigs}
    try:
        if data is not None:
            try:
                p.stdin.write(data)
                p.stdin.close()
            except BrokenPipeError:
                pass
        events = []
        for raw in p.stdout:
            if live:
                sys.stdout.buffer.write(raw)
                sys.stdout.flush()
            try:
                events.append(json.loads(raw))
            except ValueError:
                if not live:
                    sys.stderr.buffer.write(raw)  # not JSON: keep it visible
        return p.wait(), events
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def emit(fmt, verbose, events, final):
    if fmt == "stream-json":
        return  # already written live
    if fmt == "json":
        print(json.dumps(events if verbose else final))
    elif final.get("result") is not None:
        sys.stdout.buffer.write((final["result"] + "\n").encode("utf-8"))  # as claude does, whatever the console
        sys.stdout.flush()


def run(args):
    """Print mode: run claude on the best account, rerun on the next when a usage limit cuts it off."""
    pool = accounts()
    forced = os.environ.get("CLAUDE_POOL_ACCOUNT")
    if forced and forced not in pool:
        sys.exit(f"claude-pool: no account {forced!r} in {pool_dir()}")
    if not forced and fresh_secs() > 0:
        refresh(pool, stale_only=True)
    usage = load_usage()
    sargs, fmt, verbose = stream_args(args)
    cmd = [real_binary()] + sargs
    streaming_in = flag_value(args, "--input-format")[0] == "stream-json"
    # A prompt piped on stdin is read once, so a rerun gets it again. A stream-json conversation can't be replayed.
    data = None if streaming_in else piped_stdin()
    tried, switched = set(), []
    name = forced or choose(pool, usage)
    if pool and not name:
        resets = [r for r in (at_limit(usage.get(n) or {}) for n in pool) if r]
        when = datetime.fromtimestamp(min(resets), timezone.utc).isoformat() if resets else "unknown"
        msg = f"claude-pool: every account is at its usage limit (first reset {when})"
        final = {"type": "result", "subtype": "error_pool_exhausted", "is_error": True, "result": msg, "resets_at": when}
        if fmt != "text":
            print(json.dumps([final] if fmt == "json" and verbose else final))
        print(msg, file=sys.stderr)
        return EXIT_EXHAUSTED
    while True:
        env = account_env(*pool[name]) if name else dict(os.environ)
        code, events = run_once(cmd, env, data, live=fmt == "stream-json")
        final = next((e for e in reversed(events) if e.get("type") == "result"), {})
        info = next((e.get("rate_limit_info") for e in reversed(events) if e.get("type") == "rate_limit_event"), None)
        if name:
            record(name, info)
            final["pool_account"] = name
            if switched:
                final["pool_switched"] = switched
        if not name or forced or streaming_in or not cut_off(final, info):
            emit(fmt, verbose, events, final)
            return code
        tried.add(name)
        nxt = choose(pool, load_usage(), skip=tried)
        if not nxt:
            emit(fmt, verbose, events, final)
            return code
        print(f"claude-pool: {name} hit its usage limit; rerunning on {nxt}", file=sys.stderr)
        if fmt == "stream-json":
            print(json.dumps({"type": "system", "subtype": "pool_switch", "from": name, "to": nxt}), flush=True)
        switched.append(f"{name} -> {nxt}")
        name = nxt


def status():
    pool, usage = accounts(), load_usage()
    if not pool:
        print(f"no accounts in {pool_dir()} (claude runs with your normal login)")
        return 0
    fmt_ts = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "-"  # noqa: E731
    best = choose(pool, usage)
    age = lambda ts: "-" if not ts else f"{int((time.time() - ts) // 60)}m ago"  # noqa: E731
    print(f"{'account':<20} {'kind':<6} {'5h %':>6} {'7d %':>6}  {'5h reset':<21} {'7d reset':<21} {'checked':<9} runs")
    for n, (kind, _) in pool.items():
        r = usage.get(n) or {}
        mark = " <- next" if n == best else (" (at limit)" if at_limit(r) else "")
        if broken(r):
            mark = f" (broken: {r.get('error')})"
        print(f"{n:<20} {kind:<6} {r.get('five_hour') if r.get('five_hour') is not None else '-':>6} "
              f"{r.get('seven_day') if r.get('seven_day') is not None else '-':>6}  {fmt_ts(r.get('resets_ts')):<21} "
              f"{fmt_ts(r.get('seven_day_resets_ts')):<21} {age(r.get('checked_ts')):<9} {r.get('runs', 0)}{mark}")
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--pool-status"]:
        return status()
    if args[:1] == ["--pool-refresh"]:
        refresh(accounts(), names=args[1:] or None)
        return status()
    if args[:1] == ["--pool-version"]:
        print(__version__)
        return 0
    if "-p" in args or "--print" in args:
        return run(args)
    # Interactive or a subcommand: pick the account and hand over to claude.
    pool = accounts()
    if not os.environ.get("CLAUDE_POOL_ACCOUNT") and fresh_secs() > 0:
        refresh(pool, stale_only=True)
    name = os.environ.get("CLAUDE_POOL_ACCOUNT") or choose(pool, load_usage())
    env = account_env(*pool[name]) if name in pool else dict(os.environ)
    binary = real_binary()
    if WINDOWS:  # no real exec on Windows: run it and pass its exit code on
        return subprocess.call([binary] + args, env=env)
    os.execve(binary, [binary] + args, env)


if __name__ == "__main__":
    sys.exit(main())
