#!/usr/bin/env python3
r"""claude-pool: run `claude` on whichever of your accounts has the most usage left.

Drop-in for the Claude Code CLI: `claude-pool -p "hi" --output-format json` takes the same arguments and prints
the same output as `claude`. Before each run it picks a free account, and in print mode (-p) a run cut off by a
usage limit is rerun on the next free account (by default only when it ran no tools yet). An interactive session
that hits a usage limit goes on by itself on the next free account: same conversation, resumed with a nudge.

Accounts: one file per account in $CLAUDE_POOL_DIR (default ~/.config/claude-pool, on Windows %APPDATA%\claude-pool):
  <name>.token   an OAuth token from `claude setup-token` (Claude subscription)
  <name>.key     an Anthropic API key
Rename a file to <name>.token.off to take the account out. The folder is reread on every run. With no files,
claude-pool runs claude with your normal login, so it is safe to install before you add any accounts.

Usage is read from the `rate_limit_event` that `claude -p --output-format stream-json --verbose` emits, so in
print mode claude-pool always runs claude that way and converts the output back to the format you asked for.
It is kept in $CLAUDE_POOL_STATE, shared by every caller under a file lock. Numbers older than CLAUDE_POOL_FRESH
seconds are refreshed before a pick with one tiny Haiku call per account, so use by other apps is seen too.

`claude-pool --pool-help` lists the commands and settings.
"""
import getpass
import json
import os
import re
import shutil
import signal
import socket
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
    import ctypes
    import msvcrt
else:
    import fcntl

__version__ = "0.9.0"

EXIT_EXHAUSTED = 75  # EX_TEMPFAIL: every account is at its limit, try again after the reset
PROBE_TIMEOUT = 15  # seconds for one usage check
RUN_MAX_AGE = 6 * 3600  # a "running" mark older than this is dropped even if its pid can't be checked
ALERT_EVENTS = {"exhausted", "token_broken", "cli_changed", "cut_off_not_rerun"}
ALERT_EVERY = 3600  # the same alert (event + account) at most once an hour
HOST = socket.gethostname()

HELP = """claude-pool {version}: runs claude on whichever of your accounts has usage left.

Use it like claude:   claude-pool -p "hi" --output-format json

Commands:
  --pool-help              this help
  --pool-status            accounts with their usage; "next" is used next
  --pool-refresh [name..]  check usage of every account (or the named ones) now
  --pool-watch [minutes]   --pool-refresh every few minutes (default 5) until Ctrl+C
  --pool-check             check the setup: claude, folders, permissions, every token
  --pool-add <name>        add an account; paste the token from `claude setup-token`
                           (or pipe it: echo "$TOKEN" | claude-pool --pool-add work)
  --pool-off <name>        take an account out (renames it to .off)
  --pool-on <name>         put it back
  --pool-log [N]           the last N events (default 20): runs, switches, errors
  --pool-version           version

Settings (environment variables or CLAUDE_POOL_* lines in a .env file, all optional):
  CLAUDE_POOL_ENV_FILE     .env to read          the nearest .env from the current folder up
  CLAUDE_POOL_TOKEN_<NAME> an account's OAuth token instead of a file (CLAUDE_POOL_KEY_<NAME>: an API key)
  CLAUDE_POOL_DIR          account folder        {dir}
  CLAUDE_POOL_STATE        usage file            {state}
  CLAUDE_POOL_LOG          event log             {log}
  CLAUDE_POOL_BINARY       the real claude       claude
  CLAUDE_POOL_ACCOUNT      always use this account, no switching, no checks
  CLAUDE_POOL_STRATEGY     drain (soonest weekly reset first) or balance (least used first)
  CLAUDE_POOL_SWITCH_AT    5-hour % after which the next account is preferred (90)
  CLAUDE_POOL_SPREAD_AT    5-hour % after which a busy account gets no second run (75)
  CLAUDE_POOL_FRESH        seconds between usage checks, 0 = off (300)
  CLAUDE_POOL_LIMIT        % at which an account counts as full (100)
  CLAUDE_POOL_RERUN        after a limit cut-off: safe (only if no tools ran), always, never (safe)
  CLAUDE_POOL_SWITCH       off = an interactive session stays on its first account (on)
  CLAUDE_POOL_RESUME_PROMPT what a switched session is told first; empty = nothing, wait for you
  CLAUDE_POOL_ALERT        shell command run on problems, with CLAUDE_POOL_EVENT and CLAUDE_POOL_MESSAGE set

Exit code 75: every account is full. More: https://github.com/grega-rotar/claude-pool"""


ENV_ACCOUNT = re.compile(r"CLAUDE_POOL_(TOKEN|KEY)_(\w+)$")


def load_dotenv():
    """Settings and accounts from a .env file: CLAUDE_POOL_ENV_FILE, else the nearest .env from the current folder
    up. Only CLAUDE_POOL_* lines are read, so an app's other secrets never reach claude. A variable that is
    already set wins. Returns the file used, or None."""
    path = os.environ.get("CLAUDE_POOL_ENV_FILE")
    if path:
        path = os.path.expanduser(path)
    else:
        d = os.getcwd()
        while not os.path.isfile(os.path.join(d, ".env")):
            if os.path.dirname(d) == d:
                return None
            d = os.path.dirname(d)
        path = os.path.join(d, ".env")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        if os.environ.get("CLAUDE_POOL_ENV_FILE"):
            print(f"claude-pool: can't read {path} ({e.strerror})", file=sys.stderr)
        return None
    for line in lines:
        m = re.match(r"\s*(?:export\s+)?(CLAUDE_POOL_\w+)\s*=\s*(.*)$", line)
        if not m or m.group(1) in os.environ:
            continue
        value = m.group(2).strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        os.environ[m.group(1)] = value
    return path


def child_env(**extra):
    """The environment for claude and alert commands: ours without the pool's account secrets."""
    env = {k: v for k, v in os.environ.items() if not ENV_ACCOUNT.match(k)}
    env.update(extra)
    return env


def env_num(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return float(default)


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


def log_path():
    return os.path.expanduser(os.environ.get("CLAUDE_POOL_LOG") or os.path.join(os.path.dirname(state_path()), "events.log"))


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
def locked(path, wait=None):
    """An exclusive lock on `path` across processes. Yields True once held; with `wait` seconds, False if it
    wasn't free by then (the block then runs without it)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+") as f:
        share(path)
        deadline = None if wait is None else time.time() + wait
        got = False
        while True:
            try:
                if WINDOWS:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if deadline is not None and time.time() >= deadline:
                    break
                time.sleep(0.05)
        try:
            yield got
        finally:
            if got and WINDOWS:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            elif got:
                fcntl.flock(f, fcntl.LOCK_UN)


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


def log(event, **fields):
    """One line in the event log (JSON lines, rotated at 1 MB); alert-worthy events also go to CLAUDE_POOL_ALERT."""
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, "host": HOST,
            "pid": os.getpid(), **fields}
    path = log_path()
    try:
        # Under a lock: on Windows two processes appending at once can overwrite each other's line.
        with locked(path + ".lock", wait=5):
            if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
                replace(path, path + ".1")
            with open(path, "a") as f:
                f.write(json.dumps(line) + "\n")
            share(path)
    except OSError:
        pass  # a log that can't be written must never fail a run
    if event in ALERT_EVENTS:
        alert(event, line)


def alert(event, line):
    cmd = os.environ.get("CLAUDE_POOL_ALERT")
    if not cmd:
        return
    key = f"{event}:{line.get('account', '')}"
    path = os.path.join(os.path.dirname(state_path()), "alerts.json")
    try:
        with locked(path + ".lock", wait=2):
            try:
                with open(path) as f:
                    sent = json.load(f)
            except (OSError, ValueError):
                sent = {}
            if time.time() - sent.get(key, 0) < ALERT_EVERY:
                return
            sent[key] = time.time()
            with open(path + ".tmp", "w") as f:
                json.dump(sent, f)
            share(path + ".tmp")
            replace(path + ".tmp", path)
        msg = f"claude-pool on {HOST}: {line.get('message') or event}"
        subprocess.run(cmd, shell=True, input=json.dumps(line), text=True, timeout=15, capture_output=True,
                       env=child_env(CLAUDE_POOL_EVENT=event, CLAUDE_POOL_MESSAGE=msg))
    except (OSError, subprocess.SubprocessError):
        pass


def env_accounts():
    """{name: (kind, secret)} for every CLAUDE_POOL_TOKEN_<NAME> / CLAUDE_POOL_KEY_<NAME> variable (or .env line)."""
    out = {}
    for k, v in sorted(os.environ.items()):
        m = ENV_ACCOUNT.match(k)
        if m and v.strip():
            out[m.group(2).lower().replace("_", "-")] = ("token" if m.group(1) == "TOKEN" else "key", v.strip())
    return out


def shown(pool):
    """The accounts to show and check: with none set up, the normal claude login as one row named "login"."""
    return pool or {"login": ("login", None)}


def accounts(warn=False):
    """{name: (kind, secret)} for every <name>.token / <name>.key in the pool folder, plus the accounts set in
    variables. A file wins over a variable with the same name."""
    out, d = env_accounts(), pool_dir()
    if not os.path.isdir(d):
        return out
    try:
        files = sorted(os.listdir(d))
    except OSError as e:
        sys.exit(f"claude-pool: can't read the account folder {d} ({e.strerror}); "
                 "for a shared pool, add this user to the folder's group")
    for f in files:
        name, ext = os.path.splitext(f)
        if ext not in (".token", ".key"):
            continue
        path = os.path.join(d, f)
        try:
            with open(path) as fh:
                secret = fh.read().strip()
        except OSError as e:  # another user's file without group read: skip it, don't fail the run
            print(f"claude-pool: skipping {f}: {e.strerror}", file=sys.stderr)
            continue
        if warn and not WINDOWS and os.stat(path).st_mode & stat.S_IROTH:
            print(f"claude-pool: warning: {path} can be read by every user; run chmod 600 (or 640 for a shared pool)",
                  file=sys.stderr)
        if secret:
            out[name] = ("token" if ext == ".token" else "key", secret)
    return out


def load_usage():
    try:
        with open(state_path()) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@contextmanager
def usage_update():
    """The usage dict to change in place, written back whole under the lock so parallel callers lose nothing."""
    path = state_path()
    with locked(path + ".lock"):
        usage = load_usage()
        yield usage
        with open(path + ".tmp", "w") as f:
            json.dump(usage, f, indent=1)
        share(path + ".tmp")
        replace(path + ".tmp", path)  # whole, so a parallel caller never reads half a file


def alive(pid):
    if WINDOWS:
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # another user's process: it exists
    return True


def running(row, now=None):
    """The runs still going on the account: same-host marks whose pid lives, other hosts' marks until RUN_MAX_AGE."""
    now = now or time.time()
    return [r for r in row.get("running") or [] if now - r.get("ts", 0) < RUN_MAX_AGE
            and (r.get("host") != HOST or alive(r.get("pid", 0)))]


def mark_running(name, on):
    with usage_update() as usage:
        row = usage.setdefault(name, {})
        marks = [r for r in running(row) if not (r.get("pid") == os.getpid() and r.get("host") == HOST)]
        if on:
            marks.append({"pid": os.getpid(), "host": HOST, "ts": time.time()})
        row["running"] = marks


def at_limit(row, now=None):
    """When the account's last known usage says wait: the reset time (Unix), else None."""
    now, full = now or time.time(), env_num("CLAUDE_POOL_LIMIT", 100)
    week_reset = row.get("seven_day_resets_ts") or 0
    if (row.get("seven_day") or 0) >= full and week_reset > now:
        return week_reset
    reset = row.get("resets_ts") or 0
    if reset > now and (row.get("status") == "rejected" or (row.get("five_hour") or 0) >= full):
        return reset
    return None


def five_hour(row, now):
    """The 5-hour usage, or 0 once that window has reset."""
    return (row.get("five_hour") or 0) if (row.get("resets_ts") or 0) > now else 0


def choose(pool, usage, skip=(), now=None):
    """The free account to run on next, else None.

    drain (default): the account whose 7-day window resets soonest, since its unused room is lost first; then the
    most used. Accounts with no live window (never used, or reset since) come last, so they don't start a window
    before it's needed. An account past CLAUDE_POOL_SWITCH_AT percent of its 5-hour window is used only when every
    free account is, so a run is less likely to be cut off midway; and one past CLAUDE_POOL_SPREAD_AT that is
    already running something gets no second run while another account has room.
    balance: the lowest 7-day usage (then 5-hour), spreading the load. Never used counts as 0."""
    now = now or time.time()
    row = lambda n: usage.get(n) or {}  # noqa: E731
    free = [n for n in pool if n not in skip and not at_limit(row(n), now) and not broken(row(n), now)]
    if os.environ.get("CLAUDE_POOL_STRATEGY") == "balance":
        return min(free, key=lambda n: (row(n).get("seven_day") or 0, five_hour(row(n), now)), default=None)
    roomy = [n for n in free if five_hour(row(n), now) < env_num("CLAUDE_POOL_SWITCH_AT", 90)]
    calm = [n for n in roomy if not (running(row(n), now) and five_hour(row(n), now) >= env_num("CLAUDE_POOL_SPREAD_AT", 75))]

    def key(n):
        reset = row(n).get("seven_day_resets_ts") or 0
        live = reset > now
        return (not live, reset if live else 0, -(row(n).get("seven_day") or 0) if live else 0, n)
    return min(calm or roomy or free, key=key, default=None)


def fresh_secs():
    return env_num("CLAUDE_POOL_FRESH", 300)


def broken(row, now=None):
    """The account's token was refused at its last check: skip it until rechecked."""
    return bool(row.get("error_ts")) and (now or time.time()) - row["error_ts"] < max(fresh_secs(), 300)


PROBE = ["-p", "ok", "--model", "haiku", "--max-turns", "1", "--tools", "", "--system-prompt", "Reply ok.",
         "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
         "--output-format", "stream-json", "--verbose"]
AUTH_ERROR = re.compile(r"(?i)authenticat|invalid.{0,20}(token|key|bearer)|401|unauthori|expired|revoked"
                        r"|disabled .{0,40}access|not_allowed_for_organization")


def refused(final):
    """The account's token was refused (logged out, revoked, subscription access turned off for the org). claude
    reports some of these as subtype "success" with is_error true and an HTTP status."""
    return bool(final.get("is_error")) and (final.get("api_error_status") in (401, 403) or bool(
        AUTH_ERROR.search(f"{final.get('api_error_code') or ''} {final.get('result') or ''}")))


def parse_events(events):
    """(rate_limit_info, result event) of one run's stream-json events."""
    info = next((e.get("rate_limit_info") for e in reversed(events) if e.get("type") == "rate_limit_event"), None)
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    return info, final


def touch(name):
    """Count the account as checked without new numbers, so a failing check isn't retried on every call."""
    with usage_update() as usage:
        usage.setdefault(name, {})["checked_ts"] = time.time()


def probe(name, kind, secret, binary):
    """One tiny Haiku call on the account, only to read its usage numbers. Returns what it found, for --pool-check."""
    try:
        r = subprocess.run([binary] + PROBE, env=account_env(kind, secret), cwd=tempfile.gettempdir(),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        out, err = r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        touch(name)
        log("probe_timeout", account=name, message=f"usage check of {name} took over {PROBE_TIMEOUT} s")
        return "timeout"
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    info, final = parse_events(events)
    if info:
        record(name, info, ran=False)
        return "ok"
    error = str(final.get("result") or err.strip() or "no reply")[:300]
    if refused(final) or AUTH_ERROR.search(error):
        record(name, None, ran=False, error=error)
        log("token_broken", account=name, error=error, message=f"the token of {name} was refused: {error}")
        return "broken"
    if final.get("subtype") == "success" and kind == "login":  # logged in with an API key: no usage windows
        touch(name)
        return "no_usage"
    if final.get("subtype") == "success":
        # claude ran but printed no usage: the CLI's output has changed, so picks would be blind.
        touch(name)
        log("cli_changed", account=name, message="claude answered but printed no rate_limit_event; usage can't be read")
        return "no_usage"
    touch(name)
    log("probe_failed", account=name, error=error, message=f"usage check of {name} failed: {error}")
    return "failed"


def refresh(pool, names=None, stale_only=False):
    """Probe the token accounts (API keys have no usage windows) whose numbers are missing or older than
    CLAUDE_POOL_FRESH, or all of `names`. One caller probes at a time; the others wait up to 5 s for its numbers,
    then go on with the old ones. Returns {name: result}."""
    def due(usage):
        now = time.time()
        return [n for n, (kind, _) in pool.items() if kind in ("token", "login") and (names is None or n in names)
                and (not stale_only or now - ((usage.get(n) or {}).get("checked_ts") or 0) >= fresh_secs())]
    if not due(load_usage()):
        return {}
    results = {}
    with locked(state_path() + ".refresh", wait=5 if stale_only else None) as got:
        if not got:
            log("refresh_skipped", message="another caller was checking usage; went on with the saved numbers")
            return {}
        binary = real_binary()

        def one(n):
            results[n] = probe(n, *pool[n], binary)
        threads = [threading.Thread(target=one, args=(n,)) for n in due(load_usage())]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    return results


def record(name, info, ran=True, error=None):
    """Store one run's rate_limit_info for the account."""
    with usage_update() as usage:
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


def account_env(kind, secret):
    env = child_env()
    if kind == "login":  # the normal claude login: no secret of ours
        return env
    if kind == "token":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = secret
        env.pop("ANTHROPIC_API_KEY", None)  # an API key would win over the token and bill the API instead
    else:
        env["ANTHROPIC_API_KEY"] = secret
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env


LIMIT_TEXT = re.compile(r"(?i)hit your .{0,40}limit|usage limit|limit reached|rate.?limit")


def cut_off(final, info):
    """The run stopped because the account hit its usage limit."""
    if final.get("is_error") is False and final.get("subtype") == "success":
        return False
    return (info or {}).get("status") == "rejected" or bool(LIMIT_TEXT.search(str(final.get("result") or "")))


def used_tools(events):
    """The run called a tool (edited a file, ran a command...), so running it again could do that twice."""
    for e in events:
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    return True
    return False


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
    rerun = os.environ.get("CLAUDE_POOL_RERUN") or "safe"
    tried, switched = set(), []
    name = forced or choose(pool, usage)
    if pool and not name:
        resets = [r for r in (at_limit(usage.get(n) or {}) for n in pool) if r]
        when = datetime.fromtimestamp(min(resets), timezone.utc).isoformat() if resets else "unknown"
        msg = f"claude-pool: every account is at its usage limit (first reset {when})"
        log("exhausted", resets_at=when, message=f"every account is at its usage limit (first reset {when})")
        final = {"type": "result", "subtype": "error_pool_exhausted", "is_error": True, "result": msg, "resets_at": when}
        if fmt != "text":
            print(json.dumps([final] if fmt == "json" and verbose else final))
        print(msg, file=sys.stderr)
        return EXIT_EXHAUSTED
    while True:
        env = account_env(*pool[name]) if name else child_env()
        if name:
            mark_running(name, True)
            if fmt == "stream-json":  # stream output can't get pool_account added at the end: say it up front
                print(json.dumps({"type": "system", "subtype": "pool_account", "account": name}), flush=True)
        try:
            code, events = run_once(cmd, env, data, live=fmt == "stream-json")
        finally:
            if name:
                mark_running(name, False)
        info, final = parse_events(events)
        ok = final.get("subtype") == "success" and final.get("is_error") is False
        if name:
            record(name, info)
            log("run", account=name, exit=code, ok=ok, cost=final.get("total_cost_usd"),
                five_hour=(load_usage().get(name) or {}).get("five_hour"))
            if ok and info is None and pool[name][0] == "token":
                log("cli_changed", account=name, message="claude answered but printed no rate_limit_event; usage can't be read")
            final["pool_account"] = name
            if switched:
                final["pool_switched"] = switched
        bad = bool(name) and not ok and refused(final)
        if bad:  # skipped until rechecked, and the run goes to the next account: a refused run did nothing
            error = str(final.get("result") or "refused")[:300]
            record(name, None, ran=False, error=error)
            log("token_broken", account=name, error=error, message=f"the token of {name} was refused: {error}")
        limited = bool(name) and (bad or cut_off(final, info))
        if name and not ok:
            # The raw shape of every failure, so a real limit cut-off can be checked against what cut_off() expects.
            log("cut_off" if limited else "run_failed", account=name, exit=code, subtype=final.get("subtype"),
                result=str(final.get("result") or "")[:300], status=(info or {}).get("status"))
        if not limited or forced or streaming_in or rerun == "never":
            emit(fmt, verbose, events, final)
            return code
        if rerun != "always" and used_tools(events):
            final["pool_cut_off"] = True
            log("cut_off_not_rerun", account=name,
                message=f"a run on {name} hit the usage limit after it had used tools; not rerun, so nothing runs twice")
            print(f"claude-pool: {name} hit its usage limit after the run used tools; not rerunning "
                  "(set CLAUDE_POOL_RERUN=always to rerun anyway)", file=sys.stderr)
            emit(fmt, verbose, events, final)
            return code
        tried.add(name)
        nxt = choose(pool, load_usage(), skip=tried)
        if not nxt:
            emit(fmt, verbose, events, final)
            return code
        print(f"claude-pool: {name} {'was refused' if bad else 'hit its usage limit'}; rerunning on {nxt}", file=sys.stderr)
        log("switch", account=name, to=nxt)
        if fmt == "stream-json":
            print(json.dumps({"type": "system", "subtype": "pool_switch", "from": name, "to": nxt}), flush=True)
        switched.append(f"{name} -> {nxt}")
        name = nxt


# Interactive sessions. claude-pool starts claude with a StopFailure hook (--settings) that writes the failure to a
# file. When the failure is a usage limit, claude is stopped and started again on the next account with
# `--resume <session> "<prompt>"`: the conversation, tool results included, carries over, so nothing runs twice.

USAGE_LIMIT = re.compile(r"(?i)hit your .{0,40}limit|usage limit")
RESUME_PROMPT = "Continue where you left off. Your last turn was cut off by a usage limit."
# Flags that start a session elsewhere, return at once or turn hooks off: claude gets the terminal to itself.
EXEC_FLAGS = {"-h", "--help", "-v", "--version", "--bg", "--background", "--desktop", "--cloud", "--environment",
              "--teleport", "--bare", "--safe-mode"}
# Flags that pick or make the session: dropped on a switch, since --resume <id> takes their place.
SESSION_FLAGS = {"-c", "--continue", "-r", "--resume", "--session-id", "--fork-session", "--from-pr", "-w",
                 "--worktree", "--tmux"}
# Used when `claude --help` can't be read: flag -> "one" value, "many" (<x...>) or "optional" ([x]).
CLI_FALLBACK = {
    "flags": {"--add-dir": "many", "--agent": "one", "--agents": "one", "--allowedTools": "many",
              "--allowed-tools": "many", "--append-system-prompt": "one", "--autocompact": "one", "--betas": "many",
              "--cloud": "optional", "-d": "optional", "--debug": "optional", "--debug-file": "one",
              "--disallowedTools": "many", "--disallowed-tools": "many", "--effort": "one", "--environment": "one",
              "--fallback-model": "one", "--file": "many", "--from-pr": "optional", "--input-format": "one",
              "--json-schema": "one", "--max-budget-usd": "one", "--mcp-config": "many", "--model": "one",
              "-n": "one", "--name": "one", "--output-format": "one", "--permission-mode": "one",
              "--permission-prompts": "one", "--plugin-dir": "one", "--plugin-url": "one",
              "--prompt-suggestions": "optional", "--remote-control": "optional",
              "--remote-control-session-name-prefix": "one", "-r": "optional", "--resume": "optional",
              "--session-id": "one", "--setting-sources": "one", "--settings": "one", "--system-prompt": "one",
              "--system-prompt-snapshot": "one", "--teleport": "optional", "--tools": "many", "-w": "optional",
              "--worktree": "optional"},
    "commands": ["agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install", "logs", "mcp",
                 "plugin", "plugins", "purge", "respawn", "rm", "setup-token", "stop", "kill", "ultrareview",
                 "update", "upgrade"]}


def parse_help(text):
    """{"flags": {flag: arity}, "commands": [...]} from `claude --help`."""
    flags, commands, section = {}, [], None
    for line in text.splitlines():
        if re.match(r"\S", line):
            section = line.strip().rstrip(":").lower()
            continue
        m = re.match(r"  (\S.*?)(?:\s{2,}|$)", line)
        if not m:
            continue
        if section == "options":
            spec = m.group(1)
            arg = re.search(r" (<[^>]*>|\[[^\]]*\])$", spec)
            arity = None if not arg else "optional" if arg.group(1)[0] == "[" else "many" if "..." in arg.group(1) else "one"
            for flag in re.findall(r"(?<![\w-])(--?[\w-]+)", spec[:arg.start()] if arg else spec):
                if arity:
                    flags[flag] = arity
        elif section == "commands":
            commands += m.group(1).split()[0].split("|")
    return {"flags": flags, "commands": commands}


def cli_spec(binary):
    """claude's flags and commands, read from its --help once per claude version and cached next to the usage file."""
    try:
        st = os.stat(binary)
        key = f"{os.path.realpath(binary)}:{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return CLI_FALLBACK
    path = os.path.join(os.path.dirname(state_path()), "cli.json")
    try:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("key") == key:
            return cached
    except (OSError, ValueError):
        pass
    try:
        text = subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return CLI_FALLBACK
    spec = parse_help(text)
    if not spec["flags"] or not spec["commands"]:
        return CLI_FALLBACK
    spec["key"] = key
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(spec, f)
        share(path + ".tmp")
        replace(path + ".tmp", path)
    except OSError:
        pass
    return spec


def split_args(args, spec):
    """claude's args as [(flag or None, [values])]: a flag with what it takes, or None with a positional (the
    prompt, or a command). Follows claude's own parsing: --x=v, a [value] only when the next arg isn't a flag,
    <values...> up to the next flag, and everything after "--" positional."""
    out, i, flags = [], 0, spec["flags"]
    while i < len(args):
        a = args[i]
        if a == "--":
            out += [(None, [x]) for x in args[i + 1:]]
            break
        name = a.split("=", 1)[0] if a.startswith("--") else a
        if not a.startswith("-") or a == "-":
            out.append((None, [a]))
            i += 1
            continue
        arity = flags.get(name)
        j = i + 1
        if "=" in a or arity is None:
            pass
        elif arity == "one":
            j = min(i + 2, len(args))
        else:
            while j < len(args) and not args[j].startswith("-") and (arity == "many" or j == i + 1):
                j += 1
        out.append((a, args[i + 1:j]))
        i = j
    return out


def session_mode(args, spec):
    """True when the args start an interactive session in this terminal, which claude-pool can switch."""
    parts = split_args(args, spec)
    if any(f and f.split("=", 1)[0] in EXEC_FLAGS for f, _ in parts):
        return False
    first = next((v[0] for f, v in parts if f is None), None)
    return first not in spec["commands"]


def resume_args(args, spec, session, prompt):
    """The args to go on with `session` on another account: the caller's flags (MCP config, --add-dir, model... are
    not kept by a resume), without the prompt and the flags that picked or made the session."""
    keep = []
    for f, values in split_args(args, spec):
        if f is None or f.split("=", 1)[0] in SESSION_FLAGS:
            continue
        keep += [f] + values
    return keep + ["--resume", session] + ([prompt] if prompt else [])


def with_hook(args, spec, settings_path, hook_file):
    """The args with our StopFailure hook added through --settings, merged into the caller's own --settings."""
    hook_cmd = " ".join(f'"{p}"' for p in (sys.executable, os.path.abspath(__file__)))
    hook_cmd += f' --pool-hook "{hook_file}"'
    if WINDOWS:  # claude runs hooks in a POSIX shell (Git Bash) there: forward slashes stay paths
        hook_cmd = hook_cmd.replace("\\", "/")
    settings, out = {}, []
    for f, values in split_args(args, spec):
        if f and f.split("=", 1)[0] == "--settings":
            raw = f.split("=", 1)[1] if "=" in f else (values[0] if values else "")
            try:
                if raw.lstrip().startswith("{"):
                    settings = json.loads(raw)
                else:
                    with open(os.path.expanduser(raw), encoding="utf-8") as fh:
                        settings = json.load(fh)
            except (OSError, ValueError) as e:
                sys.exit(f"claude-pool: can't read --settings {raw!r}: {e}")
            continue
        out += ([f] if f else []) + values
    hooks = settings.setdefault("hooks", {})
    hooks["StopFailure"] = list(hooks.get("StopFailure") or []) + [{"hooks": [{"type": "command", "command": hook_cmd}]}]
    with open(settings_path, "w", encoding="utf-8") as fh:
        json.dump(settings, fh)
    return ["--settings", settings_path] + out, settings


def hooks_run():
    """claude can run our hook: always on macOS and Linux; on Windows only with Git Bash, which runs its hooks."""
    if not WINDOWS:
        return True
    bash = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH")
    if bash and os.path.isfile(bash):
        return True
    return any(os.path.isfile(os.path.join(os.environ.get(v) or "", "Git", "bin", "bash.exe"))
               for v in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA")) or bool(shutil.which("bash"))


def hook(path):
    """--pool-hook <file>: claude's StopFailure hook. Saves the hook's input for the session's claude-pool."""
    data = sys.stdin.read()
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(data)
        replace(path + ".tmp", path)
    except OSError:
        pass
    return 0


def read_failure(path):
    try:
        with open(path, encoding="utf-8") as f:
            failure = json.load(f)
        os.remove(path)
        return failure
    except (OSError, ValueError):
        return None


REFUSED_ERRORS = {"authentication_failed", "oauth_org_not_allowed", "account_on_hold", "billing_error"}


def refused_failure(name, failure):
    """The account itself was refused (token revoked, org access off, on hold, no credit): skipped until rechecked."""
    if failure.get("error") not in REFUSED_ERRORS:
        return False
    error = str(failure.get("last_assistant_message") or failure.get("error"))[:300]
    record(name, None, ran=False, error=error)
    log("token_broken", account=name, error=error, message=f"the token of {name} was refused: {error}")
    return True


def usage_limited(name, pool, failure):
    """The failure is the account's usage limit, not a short rate limit claude retries by itself. The account is
    checked either way, so every caller sees its new numbers."""
    if failure.get("error") != "rate_limit":
        return False
    if pool[name][0] in ("token", "login"):
        refresh({name: pool[name]}, names=[name])
    text = f"{failure.get('last_assistant_message') or ''} {failure.get('error_details') or ''}"
    if at_limit(load_usage().get(name) or {}):
        return True
    if not USAGE_LIMIT.search(text):
        return False
    # claude says limit but the check didn't show it (it failed, or a per-model limit): full for 15 minutes.
    with usage_update() as usage:
        row = usage.setdefault(name, {})
        row["status"] = "rejected"
        if (row.get("resets_ts") or 0) <= time.time():
            row["resets_ts"] = time.time() + 900
    return True


def stop(p):
    if p.poll() is None:
        p.terminate()  # claude saves the conversation and puts the terminal back on SIGTERM
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def interactive(args, pool, name, binary, spec):
    """Run an interactive claude on `name`, and move the session to the next account each time a usage limit
    stops it. Returns claude's exit code."""
    tmp = tempfile.mkdtemp(prefix="claude-pool-")
    hook_file = os.path.join(tmp, "stop-failure.json")
    args, settings = with_hook(args, spec, os.path.join(tmp, "settings.json"), hook_file)
    if settings.get("disableAllHooks"):
        print("claude-pool: your --settings turn hooks off; this session can't switch accounts", file=sys.stderr)
    prompt = os.environ.get("CLAUDE_POOL_RESUME_PROMPT", RESUME_PROMPT)
    tty = None
    if not WINDOWS and sys.stdin is not None and sys.stdin.isatty():
        import termios
        tty = termios.tcgetattr(sys.stdin.fileno())
    p, cwd, switches, stopping = None, None, 0, []

    def forward(sig, _frame):  # a terminate or hang-up is passed on, and the session ends there
        stopping.append(sig)
        if p and p.poll() is None:
            p.terminate() if WINDOWS else p.send_signal(sig)
    # The terminal sends Ctrl+C to us and claude alike: only claude acts on it.
    sigs = {s: forward for s in (getattr(signal, n, None) for n in ("SIGTERM", "SIGHUP")) if s}
    # A no-op handler, not SIG_IGN: an ignored signal would stay ignored in claude and the commands it runs.
    sigs.update({s: lambda _s, _f: None for s in (getattr(signal, n, None) for n in ("SIGINT", "SIGQUIT")) if s})
    old = {s: signal.signal(s, h) for s, h in sigs.items()}
    try:
        while True:
            mark_running(name, True)
            log("session", account=name, resumed=switches > 0)
            p = subprocess.Popen([binary] + args, env=account_env(*pool[name]), cwd=cwd)
            nxt = None
            while p.poll() is None:
                try:
                    p.wait(0.25)
                except subprocess.TimeoutExpired:
                    pass
                failure = read_failure(hook_file) if os.path.exists(hook_file) else None
                if not failure or not failure.get("session_id") or stopping:
                    continue
                why = "was refused" if refused_failure(name, failure) else "hit its usage limit" if usage_limited(
                    name, pool, failure) else None
                if not why:
                    continue
                nxt = choose(pool, load_usage(), skip={name})
                if not nxt and why == "was refused":
                    log("switch_failed", account=name, message=f"{name} was refused and no other account is free")
                    continue
                if not nxt:  # claude waits for the reset and goes on by itself
                    resets = [r for r in (at_limit(load_usage().get(n) or {}) for n in pool) if r]
                    when = datetime.fromtimestamp(min(resets), timezone.utc).isoformat() if resets else "unknown"
                    log("exhausted", account=name, resets_at=when,
                        message=f"every account is at its usage limit (first reset {when}); the session waits")
                    continue
                time.sleep(1)  # the hook runs as the turn ends: give claude a moment to finish writing it
                if p.poll() is not None or stopping:  # you quit meanwhile: don't start it again
                    nxt = None
                    break
                stop(p)
                break
            mark_running(name, False)
            if not nxt:
                return p.returncode if p.returncode >= 0 else 128 - p.returncode  # killed by a signal: as a shell says
            if tty:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, tty)
            log("switch", account=name, to=nxt, session=failure["session_id"], reason=failure.get("error"))
            print(f"\nclaude-pool: {name} {why}; going on with {nxt}", file=sys.stderr, flush=True)
            switches += 1
            cwd = failure.get("cwd") or cwd  # with --worktree the session lives in the worktree
            args = resume_args(args, spec, failure["session_id"], prompt)
            name = nxt
    finally:
        for s, h in old.items():
            signal.signal(s, h)
        if p and p.poll() is None:
            stop(p)
        shutil.rmtree(tmp, ignore_errors=True)


def status():
    """The accounts as a table: usage, when it resets (local time), and what happens to each account next."""
    pool, usage = shown(accounts(warn=True)), load_usage()
    now = time.time()
    best = choose(pool, usage)
    pct = lambda v: "-" if v is None else f"{v:g}%"  # noqa: E731

    def when(ts):
        if not ts or ts <= now:
            return "-"
        t, left = datetime.fromtimestamp(ts), int(ts - now) // 60
        day = "" if t.date() == datetime.fromtimestamp(now).date() else t.strftime("%a ")
        return f"{day}{t:%H:%M} (in {left // 60}h{left % 60:02d}m)" if left >= 60 else f"{day}{t:%H:%M} (in {left}m)"

    def state(r):
        if broken(r):
            err = (r.get("error") or "token refused").split(" · ")[0]
            return "broken: " + (err if len(err) <= 80 else err[:79] + "…")
        reset = at_limit(r)
        busy = len(running(r, now))
        s = f"full until {when(reset).split(' (')[0]}" if reset else ""
        return ", ".join(x for x in (s, f"{busy} running" if busy else "") if x)

    mixed = any(kind == "key" for kind, _ in pool.values())  # a kind column only when API keys are in the pool
    head = ["account"] + (["kind"] if mixed else []) + ["5h", "7d", "5h resets", "7d resets", "checked", "runs", "status"]
    rows = []
    for n, (kind, _) in pool.items():
        r = usage.get(n) or {}
        st = state(r)
        rows.append([n] + ([kind] if mixed else []) +
                    [pct(r.get("five_hour")), pct(r.get("seven_day")), when(r.get("resets_ts")),
                     when(r.get("seven_day_resets_ts")),
                     "-" if not r.get("checked_ts") else f"{int((now - r['checked_ts']) // 60)}m ago",
                     str(r.get("runs", 0)), ("next" + (", " + st if st else "")) if n == best else st])
    widths = [max(len(x[i]) for x in [head] + rows) for i in range(len(head) - 1)]
    for x in [head] + rows:
        print("  ".join(c.ljust(w) for c, w in zip(x, widths)) + "  " + x[-1])
    return 0


def watch(minutes):
    """--pool-refresh again and again. Every check is a tiny Haiku call per account, so the wait is at least a minute."""
    wait = max(60, int(minutes * 60))
    try:
        while True:
            refresh(shown(accounts()))
            if sys.stdout.isatty():
                print("\033[H\033[2J", end="")  # clear the screen
            status()
            print(f"\nchecked {time.strftime('%H:%M')}, next check in {wait // 60} min. Ctrl+C to stop.", flush=True)
            time.sleep(wait)
    except KeyboardInterrupt:
        print()
        return 0


def valid_name(name):
    if not name or not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
        sys.exit("claude-pool: an account name may use letters, digits, dot, dash and underscore")
    return name


def add(name):
    name, d = valid_name(name), pool_dir()
    path = os.path.join(d, name + ".token")
    if any(os.path.exists(os.path.join(d, name + ext)) for ext in (".token", ".key", ".token.off", ".key.off")):
        sys.exit(f"claude-pool: account {name!r} already exists in {d}")
    if sys.stdin is not None and sys.stdin.isatty():
        print("Run `claude setup-token` and log in as this account, then paste the token here.")
        token = getpass.getpass("Token (hidden): ").strip()
    else:
        token = sys.stdin.read().strip()
    if not token:
        sys.exit("claude-pool: no token given")
    if token.startswith("sk-ant-api"):
        path = os.path.join(d, name + ".key")  # an API key, not a subscription token
    if not os.path.isdir(d):
        os.makedirs(d, mode=0o700)
    shared = not WINDOWS and bool(os.stat(d).st_mode & stat.S_IWGRP)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640 if shared else 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    if not WINDOWS:
        os.chmod(path, 0o640 if shared else 0o600)
    print(f"added {name} ({path})")
    if path.endswith(".token"):
        result = refresh({name: ("token", token)}, names=[name]).get(name)
        if result == "broken":
            print(f"claude-pool: warning: the token of {name} was refused; check it, or remove {path}", file=sys.stderr)
    return status()


def toggle(name, on):
    name, d = valid_name(name), pool_dir()
    for ext in (".token", ".key"):
        src, dst = (os.path.join(d, name + ext + ".off"), os.path.join(d, name + ext))
        if not on:
            src, dst = dst, src
        if os.path.exists(src):
            os.rename(src, dst)
            print(f"{name} is {'on' if on else 'off'}")
            log("account_on" if on else "account_off", account=name)
            return 0
    sys.exit(f"claude-pool: no {'disabled' if on else 'active'} account {name!r} in {d}")


def show_log(n):
    try:
        with open(log_path()) as f:
            lines = f.readlines()[-n:]
    except OSError:
        print(f"no events yet ({log_path()})")
        return 0
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("ts", "event", "host", "pid", "message") and v is not None)
        print(f"{e.get('ts')}  {e.get('event'):<18} {e.get('message') or extra}")
    return 0


def check():
    """--pool-check: everything a working setup needs, as OK / WARN / FAIL lines. Exit 1 on any FAIL."""
    failed = []

    def say(level, text):
        print(f"{level:<5} {text}")
        if level == "FAIL":
            failed.append(text)
    b = os.environ.get("CLAUDE_POOL_BINARY") or "claude"
    binary = shutil.which(b)
    if not binary:
        say("FAIL", f"claude not found ({b}); install Claude Code or set CLAUDE_POOL_BINARY")
    else:
        try:
            v = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            v = "?"
        say("OK", f"claude {v} ({binary})")
    d = pool_dir()
    try:
        files = sorted(os.listdir(d)) if os.path.isdir(d) else None
    except OSError as e:
        say("FAIL", f"account folder {d}: {e.strerror}; for a shared pool, add this user to the folder's group")
        return 1
    env_file = load_dotenv()
    if env_file:
        say("OK", f"settings from {env_file}")
    if env_accounts():
        say("OK", "accounts from variables: " + ", ".join(env_accounts()))
    if files is None:
        if not env_accounts():
            say("WARN", f"no account folder {d}; claude runs with the normal login (add one: claude-pool --pool-add <name>)")
    else:
        say("OK", f"account folder {d}")
        for f in files:
            p = os.path.join(d, f)
            if not f.endswith((".token", ".key")):
                continue
            if not os.access(p, os.R_OK):
                say("FAIL", f"{f}: this user can't read it (shared pool: chgrp the group, chmod 640)")
            elif not WINDOWS and os.stat(p).st_mode & stat.S_IROTH:
                say("WARN", f"{f}: every user can read it; chmod 600 (or 640 for a shared pool)")
    sp = state_path()
    try:
        with locked(sp + ".lock", wait=5) as got:
            say("OK" if got else "WARN", f"usage file {sp}" + ("" if got else " (lock busy for 5 s)"))
    except OSError as e:
        say("FAIL", f"usage file {sp}: {e.strerror}; this user must be able to write that folder")
    pool = accounts()
    if binary and pool:
        results = refresh(pool)
        for n, (kind, _) in pool.items():
            if kind == "key":
                say("OK", f"{n}: API key (no usage windows to check)")
                continue
            r = results.get(n)
            text = {"ok": "token works, usage read", "broken": "token refused: " + str((load_usage().get(n) or {}).get("error")),
                    "no_usage": "claude printed no usage numbers; claude-pool can't pick well with this claude version",
                    "timeout": f"check took over {PROBE_TIMEOUT} s", "failed": "check failed; see --pool-log"}.get(r, "not checked")
            say({"ok": "OK", "broken": "FAIL", "no_usage": "FAIL"}.get(r, "WARN"), f"{n}: {text}")
    alert_cmd = os.environ.get("CLAUDE_POOL_ALERT")
    say("OK" if alert_cmd else "WARN", f"alerts: {alert_cmd}" if alert_cmd else "no CLAUDE_POOL_ALERT; problems go to the log only")
    print()
    status()
    return 1 if failed else 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    cmd, rest = (args[0], args[1:]) if args else (None, [])
    if cmd == "--pool-hook":  # first: claude waits on it, so no .env or account reads
        return hook(rest[0]) if rest else 1
    load_dotenv()
    if cmd == "--pool-help":
        print(HELP.format(version=__version__, dir=pool_dir(), state=state_path(), log=log_path()))
        return 0
    if cmd == "--pool-version":
        print(__version__)
        return 0
    if cmd == "--pool-status":
        return status()
    if cmd == "--pool-refresh":
        refresh(shown(accounts()), names=rest or None)
        return status()
    if cmd == "--pool-watch":
        try:
            return watch(float(rest[0]) if rest else 5)
        except ValueError:
            sys.exit("claude-pool: --pool-watch takes the minutes between checks, e.g. --pool-watch 10")
    if cmd == "--pool-check":
        return check()
    if cmd == "--pool-add":
        return add(rest[0] if rest else "")
    if cmd in ("--pool-off", "--pool-on"):
        return toggle(rest[0] if rest else "", cmd == "--pool-on")
    if cmd == "--pool-log":
        return show_log(int(rest[0]) if rest and rest[0].isdigit() else 20)
    if cmd and cmd.startswith("--pool-"):
        sys.exit(f"claude-pool: unknown command {cmd}; see claude-pool --pool-help")
    if "-p" in args or "--print" in args:
        return run(args)
    # Interactive or a subcommand: pick the account. A session that can move to another account at a usage limit
    # runs under claude-pool; anything else gets claude to itself.
    pool = accounts()
    forced = os.environ.get("CLAUDE_POOL_ACCOUNT")
    if not forced and fresh_secs() > 0:
        refresh(pool, stale_only=True)
    name = forced or choose(pool, load_usage())
    env = account_env(*pool[name]) if name in pool else child_env()
    binary = real_binary()
    if (name in pool and not forced and len(pool) > 1 and os.environ.get("CLAUDE_POOL_SWITCH", "on") != "off"
            and session_mode(args, cli_spec(binary))):
        if hooks_run():
            return interactive(args, pool, name, binary, cli_spec(binary))
        print("claude-pool: Git Bash not found, so this session can't switch accounts at a usage limit "
              "(install Git for Windows or set CLAUDE_CODE_GIT_BASH_PATH)", file=sys.stderr)
    if name in pool:
        mark_running(name, True)  # exec keeps our pid, so the mark lives as long as the session
    if WINDOWS:  # no real exec on Windows: run it and pass its exit code on
        try:
            return subprocess.call([binary] + args, env=env)
        finally:
            if name in pool:
                mark_running(name, False)
    os.execve(binary, [binary] + args, env)


if __name__ == "__main__":
    sys.exit(main())
