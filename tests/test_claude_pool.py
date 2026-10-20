"""Tests for claude-pool against a fake claude. Run: python3 -m unittest discover tests"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, "claude_pool.py")

# The fake claude acts on the token it gets: "limit*" is cut off by a usage limit, anything else succeeds.
# It logs its args and token, and echoes the prompt (last arg, or stdin) back as the result.
# Interactive (no -p): it runs the StopFailure hook from --settings as claude does, then waits to be stopped:
# "limit*" with a usage limit, "orgoff*" refused, "blip*" a short rate limit. Other tokens just exit.
FAKE = r'''#!/usr/bin/env python3
import json, os, shlex, subprocess, sys, time
args = sys.argv[1:]
if args == ["--help"]:
    print("Usage: claude [options] [command] [prompt]\n\nOptions:\n  --model <model>  Model\n  --add-dir <directories...>  Dirs\n"
          "  -r, --resume [value]  Resume\n  -c, --continue  Continue\n  --settings <file-or-json>  Settings\n"
          "  -w, --worktree [name]  Worktree\n  --plugin-dir <path>  Plugin\n  -v, --version  Version\n\n"
          "Commands:\n  mcp  MCP\n  update|upgrade  Update")
    sys.exit(0)
tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ("key:" + os.environ["ANTHROPIC_API_KEY"] if os.environ.get("ANTHROPIC_API_KEY") else "login")
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
if tok == "login" and cfg and os.path.isfile(os.path.join(cfg, "fake-login")):  # a logged-in config folder (Orca)
    tok = open(os.path.join(cfg, "fake-login")).read().strip()
settings = None
if "--settings" in args:
    with open(args[args.index("--settings") + 1]) as f:
        settings = json.load(f)
os.makedirs(os.environ["FAKE_LOG"], exist_ok=True)  # one file per call: parallel calls can't lose a line
call = os.path.join(os.environ["FAKE_LOG"], "%020d-%d.json" % (time.time_ns(), os.getpid()))
with open(call + ".tmp", "w") as f:  # renamed when whole, so a test polling the log never reads half a file
    f.write(json.dumps({"args": args, "token": tok, "settings": settings, "cwd": os.getcwd(), "config_dir": cfg,
                        "pool_secrets": sorted(k for k in os.environ if k.startswith(("CLAUDE_POOL_TOKEN_", "CLAUDE_POOL_KEY_")))}))
os.replace(call + ".tmp", call)
if "-p" not in args:
    failure = {"limit": ("rate_limit", "You've hit your session limit"),
               "orgoff": ("oauth_org_not_allowed", "Your organization has disabled Claude subscription access"),
               "blip": ("rate_limit", "API Error: Rate limit reached")}.get(tok.split("-")[0])
    if failure and settings:
        payload = {"session_id": "sess-1", "hook_event_name": "StopFailure", "error": failure[0],
                   "last_assistant_message": failure[1], "cwd": os.environ.get("FAKE_SESSION_CWD", os.getcwd())}
        for entry in settings["hooks"]["StopFailure"]:
            for h in entry["hooks"]:
                subprocess.run(shlex.split(h["command"]), input=json.dumps(payload), text=True)
        time.sleep(float(os.environ.get("FAKE_WAIT", "15")))  # a session at the limit waits for the reset
    if settings and "--resume" not in args and (os.environ.get("FAKE_PROMPT") or os.environ.get("FAKE_BANG")):
        # You type FAKE_PROMPT (the UserPromptSubmit hook sees it), then maybe run `! claude-pool --pool-switch FAKE_BANG`.
        if os.environ.get("FAKE_USAGE_A"):  # account a's usage moved on meanwhile
            with open(os.environ["CLAUDE_POOL_STATE"]) as f:
                state = json.load(f)
            state["a"].update(json.loads(os.environ["FAKE_USAGE_A"]))
            with open(os.environ["CLAUDE_POOL_STATE"], "w") as f:
                json.dump(state, f)
        payload = {"session_id": "sess-1", "hook_event_name": "UserPromptSubmit", "cwd": os.getcwd(),
                   "prompt": os.environ.get("FAKE_PROMPT") or "hi"}
        out = ""
        for entry in settings["hooks"]["UserPromptSubmit"]:
            for h in entry["hooks"]:
                out += subprocess.run(shlex.split(h["command"]), input=json.dumps(payload), text=True, capture_output=True).stdout
        if os.environ.get("FAKE_BANG"):
            r = subprocess.run([sys.executable, os.environ["FAKE_POOL"], "--pool-switch", os.environ["FAKE_BANG"]],
                               capture_output=True, text=True)
            out += r.stdout + r.stderr
        with open(os.path.join(os.environ["FAKE_LOG"], "hook-out.txt"), "w") as f:
            f.write(out)
        time.sleep(float(os.environ.get("FAKE_WAIT", "15")))
    print("session done on " + tok)
    sys.exit(0)
prompt = args[args.index("-p") + 1] if "-p" in args and args.index("-p") + 1 < len(args) and not args[args.index("-p") + 1].startswith("-") else sys.stdin.read()
reset = int(time.time()) + 3600
def out(e): print(json.dumps(e), flush=True)
out({"type": "system", "subtype": "init"})
if tok.startswith("bad"):
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Invalid bearer token"})
    sys.exit(1)
if tok.startswith("orgoff"):  # claude reports this one as "success" with is_error and a 403
    out({"type": "result", "subtype": "success", "is_error": True, "api_error_status": 403,
         "api_error_code": "oauth_not_allowed_for_organization",
         "result": "Your organization has disabled Claude subscription access for Claude Code"})
    sys.exit(1)
if tok.startswith("net"):
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "API Error: Connection error."})
    sys.exit(1)
if tok.startswith("nousage"):
    out({"type": "result", "subtype": "success", "is_error": False, "result": "echo:" + prompt.strip()})
    sys.exit(0)
if tok.startswith("limit"):
    if "tool" in tok:  # the run edited something before the limit cut it off
        out({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {}}]}})
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": reset,
         "unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": reset}, "seven_day": {"utilization": 0.5, "resetsAt": reset + 86400}}}})
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "You've hit your session limit"})
    sys.exit(1)
util = float(os.environ.get("FAKE_UTIL_" + tok.replace("-", "_"), os.environ.get("FAKE_UTIL", "0.2")))
out({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": reset,
     "unifiedWindows": {"five_hour": {"utilization": util, "resetsAt": reset}, "seven_day": {"utilization": util / 2, "resetsAt": reset + 86400}}}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "echo:" + prompt.strip()})
'''


class PoolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.pool = os.path.join(self.tmp, "pool")
        os.makedirs(self.pool)
        self.fake = os.path.join(self.tmp, "claude")
        with open(self.fake, "w") as f:
            f.write(FAKE)
        os.chmod(self.fake, os.stat(self.fake).st_mode | stat.S_IXUSR)
        if os.name == "nt":  # Windows runs no shebang scripts: wrap it in a .cmd
            with open(self.fake + ".cmd", "w") as f:
                f.write(f'@"{sys.executable}" "{self.fake}" %*\n')
            self.fake += ".cmd"
        self.log = os.path.join(self.tmp, "log")
        self.state = os.path.join(self.tmp, "usage.json")
        # Nothing of this machine's own claude, Orca or home folders: each test has its own.
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_POOL", "ANTHROPIC", "CLAUDE_CODE", "ORCA_"))
                    and k not in ("CLAUDE_CONFIG_DIR", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "APPDATA")}
        self.env.update(HOME=self.tmp, USERPROFILE=self.tmp)
        # drain: these tests count on its name order when nothing else differs. pace has its own tests below and in
        # test_pace.py.
        self.env.update(CLAUDE_POOL_STRATEGY="drain", CLAUDE_POOL_ORCA="off", CLAUDE_POOL_FRESH="0", CLAUDE_POOL_DIR=self.pool, CLAUDE_POOL_STATE=self.state, CLAUDE_POOL_BINARY=self.fake,
                        FAKE_LOG=self.log, COLUMNS="0")

    def add(self, name, secret, ext=".token"):
        with open(os.path.join(self.pool, name + ext), "w") as f:
            f.write(secret + "\n")

    def usage(self, rows):
        with open(self.state, "w") as f:
            json.dump(rows, f)

    def run_pool(self, *args, stdin=None, cwd=None, **env):
        return subprocess.run([sys.executable, POOL, *args], capture_output=True, text=True, encoding="utf-8", input=stdin,
                              env={**self.env, **env}, timeout=30, cwd=cwd or self.tmp)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        out = []
        for name in sorted(n for n in os.listdir(self.log) if n.endswith(".json")):
            with open(os.path.join(self.log, name)) as f:
                out.append(json.loads(f.read()))
        return out

    def test_no_accounts_uses_normal_login(self):
        r = self.run_pool("-p", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "echo:hi\n")
        self.assertEqual(self.calls()[0]["token"], "login")

    def test_no_accounts_shows_normal_login_usage(self):
        self.assertIn("login ", self.run_pool("--pool-status").stdout)
        r = self.run_pool("--pool-refresh", FAKE_UTIL="0.5")
        self.assertEqual([c["token"] for c in self.probes()], ["login"])
        line = [l for l in r.stdout.splitlines() if l.split()[1:2] == ["login"]][0]
        self.assertIn("50%", line)
        self.assertIn("0m ago", line)

    def test_text_output_and_stream_flags(self):
        self.add("a", "tok-a")
        r = self.run_pool("-p", "hi", "--output-format", "text")
        self.assertEqual(r.stdout, "echo:hi\n")
        args = self.calls()[0]["args"]
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertEqual(args.count("--output-format"), 1)
        self.assertIn("--verbose", args)

    def test_json_output_is_the_result_with_account(self):
        self.add("a", "tok-a")
        r = self.run_pool("-p", "hi", "--output-format=json")
        out = json.loads(r.stdout)
        self.assertEqual(out["result"], "echo:hi")
        self.assertEqual(out["pool_account"], "a")

    def test_json_verbose_output_is_all_events(self):
        self.add("a", "tok-a")
        out = json.loads(self.run_pool("-p", "hi", "--output-format", "json", "--verbose").stdout)
        self.assertIsInstance(out, list)
        self.assertEqual(out[-1]["type"], "result")
        self.assertEqual(self.calls()[0]["args"].count("--verbose"), 1)

    def test_stream_json_passes_through(self):
        self.add("a", "tok-a")
        lines = [json.loads(l) for l in self.run_pool("-p", "hi", "--output-format", "stream-json", "--verbose").stdout.splitlines()]
        self.assertEqual([e["type"] for e in lines], ["system", "system", "rate_limit_event", "result"])
        self.assertEqual(lines[0], {"type": "system", "subtype": "pool_account", "account": "a"})

    def test_balance_picks_lowest_weekly_usage_and_records_it(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 60, "five_hour": 1}, "b": {"seven_day": 10, "five_hour": 90}})
        self.run_pool("-p", "hi", FAKE_UTIL="0.4", CLAUDE_POOL_STRATEGY="balance")
        self.assertEqual(self.calls()[0]["token"], "tok-b")
        with open(self.state) as f:
            row = json.load(f)["b"]
        self.assertEqual((row["five_hour"], row["seven_day"], row["runs"]), (40.0, 20.0, 1))

    def test_drain_takes_soonest_weekly_reset(self):
        now = time.time()
        for n in "abc":
            self.add(n, "tok-" + n)
        self.usage({"a": {"seven_day": 80, "seven_day_resets_ts": now + 5 * 86400},
                    "b": {"seven_day": 10, "seven_day_resets_ts": now + 86400}})  # c: never used
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_drain_same_reset_takes_most_used(self):
        reset = time.time() + 86400
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 20, "seven_day_resets_ts": reset}, "b": {"seven_day": 70, "seven_day_resets_ts": reset}})
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_drain_keeps_unused_and_reset_accounts_for_last(self):
        now = time.time()
        self.add("fresh", "tok-fresh")
        self.add("old", "tok-old")
        self.add("live", "tok-live")
        self.usage({"old": {"seven_day": 90, "seven_day_resets_ts": now - 60},
                    "live": {"seven_day": 40, "seven_day_resets_ts": now + 6 * 86400}})
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-live")

    def test_drain_without_weekly_numbers_takes_soonest_5h_reset(self):
        now = time.time()
        for n in "abc":
            self.add(n, "tok-" + n)
        self.usage({"a": {"five_hour": 19, "resets_ts": now + 4 * 3600},
                    "b": {"five_hour": 20, "resets_ts": now + 3600}})  # c: no live 5-hour window
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_drain_same_5h_reset_takes_most_used_and_unused_last(self):
        reset = time.time() + 3600
        for n in "abc":
            self.add(n, "tok-" + n)
        self.usage({"a": {"five_hour": 10, "resets_ts": reset}, "b": {"five_hour": 40, "resets_ts": reset},
                    "c": {"five_hour": 80, "resets_ts": time.time() - 60}})
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_drain_moves_on_past_the_5h_margin_but_uses_it_as_last_resort(self):
        now = time.time()
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        rows = {"a": {"seven_day": 50, "five_hour": 92, "resets_ts": now + 600, "seven_day_resets_ts": now + 86400},
                "b": {"seven_day": 10, "five_hour": 5, "seven_day_resets_ts": now + 5 * 86400}}
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-b")
        rows["b"].update(five_hour=95, resets_ts=now + 600)
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-a")

    def test_drain_moves_on_past_the_7d_margin_but_uses_it_as_last_resort(self):
        now = time.time()
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        rows = {"a": {"seven_day": 97, "five_hour": 5, "resets_ts": now + 600, "seven_day_resets_ts": now + 86400},
                "b": {"seven_day": 10, "five_hour": 5, "seven_day_resets_ts": now + 5 * 86400}}
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-b")
        rows["b"].update(seven_day=95)
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-a")

    def test_pace_is_the_default_and_keeps_a_small_plan_from_running_dry(self):
        now = time.time()
        self.add("small", "tok-small")
        self.add("big", "tok-big")
        rows = {"small": {"seven_day": 30, "seven_day_resets_ts": now + 72 * 3600, "checked_ts": now,
                          "pace": {"seven_day": {"r": 40, "n": 200}}},
                "big": {"seven_day": 30, "seven_day_resets_ts": now + 120 * 3600, "checked_ts": now,
                        "pace": {"seven_day": {"r": 10, "n": 200}}}}
        self.usage(rows)
        self.run_pool("-p", "hi", CLAUDE_POOL_STRATEGY="")
        self.assertEqual(self.calls()[-1]["token"], "tok-big")
        self.usage(rows)  # the run saved the fake's numbers: back to the same start
        self.run_pool("-p", "hi")  # drain: the week that resets first
        self.assertEqual(self.calls()[-1]["token"], "tok-small")
        self.usage(rows)
        status = self.run_pool("--pool-status", CLAUDE_POOL_STRATEGY="").stdout
        self.assertIn("saving its week", [l for l in status.splitlines() if " small " in l][0])

    def test_runs_teach_the_pool_r(self):
        self.add("a", "tok-a")
        for util in ("0.2", "0.5"):  # the fake's weekly use is half its 5-hour use: r = 50
            self.run_pool("-p", "hi", FAKE_UTIL=util)
        with open(self.state) as f:
            self.assertEqual(json.load(f)["a"]["pace"]["seven_day"]["r"], 50.0)

    def test_skips_account_at_limit(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 1, "status": "rejected", "resets_ts": time.time() + 600}, "b": {"seven_day": 50}})
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_limit_cut_off_reruns_on_next_account_with_stdin_prompt(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}, "b": {"seven_day": 5}})
        r = self.run_pool("-p", "--output-format", "json", stdin="piped prompt")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual((out["result"], out["pool_account"], out["pool_switched"]), ("echo:piped prompt", "b", ["a -> b"]))
        self.assertEqual([c["token"] for c in self.calls()], ["limit-a", "tok-b"])
        with open(self.state) as f:
            self.assertEqual(json.load(f)["a"]["status"], "rejected")

    def test_last_account_cut_off_returns_its_result(self):
        self.add("a", "limit-a")
        r = self.run_pool("-p", "hi", "--output-format", "json")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(json.loads(r.stdout)["is_error"])

    def test_all_at_limit_exits_tempfail_without_running(self):
        self.add("a", "tok-a")
        self.usage({"a": {"seven_day": 100, "seven_day_resets_ts": time.time() + 9999}})
        r = self.run_pool("-p", "hi", "--output-format", "json")
        self.assertEqual(r.returncode, 75)
        self.assertEqual(json.loads(r.stdout)["subtype"], "error_pool_exhausted")
        self.assertEqual(self.calls(), [])

    def test_forced_account_does_not_rotate(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.run_pool("-p", "hi", CLAUDE_POOL_ACCOUNT="a")
        self.assertEqual([c["token"] for c in self.calls()], ["limit-a"])

    def test_api_key_account_drops_oauth_token(self):
        self.add("api", "sk-1", ext=".key")
        self.run_pool("-p", "hi", CLAUDE_CODE_OAUTH_TOKEN="stale")
        self.assertEqual(self.calls()[0]["token"], "key:sk-1")

    def test_off_files_are_ignored(self):
        self.add("a", "tok-a")
        os.rename(os.path.join(self.pool, "a.token"), os.path.join(self.pool, "a.token.off"))
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "login")

    def test_interactive_execs_claude_with_account(self):
        self.add("a", "tok-a")
        self.run_pool("--version")
        c = self.calls()[0]
        self.assertEqual((c["args"], c["token"]), (["--version"], "tok-a"))

    def read_usage(self):
        with open(self.state) as f:
            return json.load(f)

    def sessions(self):
        return [c for c in self.calls() if "-p" not in c["args"]]

    def test_session_moves_to_next_account_at_usage_limit(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600}})
        r = self.run_pool("fix the bug", "--model", "haiku", "--add-dir", "x", "y", "-c")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a hit its usage limit; going on with b", r.stderr)
        first, second = self.sessions()
        self.assertEqual(first["token"], "limit-a")
        self.assertEqual(first["args"][4:], ["fix the bug", "--model", "haiku", "--add-dir", "x", "y", "-c"])
        self.assertEqual(second["token"], "tok-b")
        # The caller's flags again (a resume doesn't keep them), not the prompt or -c, then the session and a nudge.
        self.assertEqual(second["args"][4:], ["--model", "haiku", "--add-dir", "x", "y", "--resume", "sess-1",
                                              "Continue where you left off. Your last turn was cut off by a usage limit."])
        self.assertEqual(second["args"][:4], first["args"][:4])  # the same --settings with the hooks, and the plugin
        self.assertEqual(self.read_usage()["a"]["status"], "rejected")  # checked, so every caller sees it
        events = [e["event"] for e in self.events()]
        self.assertIn("switch", events)
        self.assertFalse(os.path.exists(first["args"][1]), "the session's temp folder is removed")
        self.assertFalse(self.read_usage()["b"].get("running"))

    def test_session_moves_on_from_a_refused_token_and_marks_it(self):
        self.add("a", "orgoff-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600}})
        r = self.run_pool("hi")
        self.assertIn("a was refused; going on with b", r.stderr)
        self.assertEqual([c["token"] for c in self.sessions()], ["orgoff-a", "tok-b"])
        self.assertIn("disabled", self.read_usage()["a"]["error"])

    def test_session_chain_and_custom_resume_prompt(self):
        self.add("a", "limit-a")
        self.add("b", "limit-b")
        self.add("c", "tok-c")
        # b was checked a moment ago, so it is trusted without a recheck and the session goes a -> b -> c.
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600},
                    "b": {"seven_day": 50, "seven_day_resets_ts": time.time() + 7200, "checked_ts": time.time()}})
        self.run_pool("hi", CLAUDE_POOL_RESUME_PROMPT="go on")
        a, b, c = self.sessions()
        self.assertEqual([x["token"] for x in (a, b, c)], ["limit-a", "limit-b", "tok-c"])
        self.assertEqual(c["args"][4:], ["--resume", "sess-1", "go on"])

    def test_switch_rechecks_the_next_account_first(self):
        # b's saved numbers are old and say it has room, but it is full: the recheck skips it.
        self.add("a", "limit-a")
        self.add("b", "limit-b")
        self.add("c", "tok-c")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600},
                    "b": {"seven_day": 50, "seven_day_resets_ts": time.time() + 7200, "checked_ts": time.time() - 3600}})
        r = self.run_pool("hi")
        self.assertIn("a hit its usage limit; going on with c", r.stderr)
        self.assertEqual([x["token"] for x in self.sessions()], ["limit-a", "tok-c"])
        self.assertIn("limit-b", [c["token"] for c in self.probes()])

    # --- /pool and --pool-switch: a switch you ask for ------------------------------------------------------
    def asked(self, **env):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.add("c", "tok-c")
        soon = time.time() + 3600
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": soon},
                    "b": {"seven_day": 10, "seven_day_resets_ts": soon + 7200, "checked_ts": time.time()},
                    "c": {"status": "rejected", "resets_ts": soon, "checked_ts": time.time()}})
        r = self.run_pool("fix it", "--model", "haiku", FAKE_POOL=POOL, **env)
        with open(os.path.join(self.log, "hook-out.txt")) as f:
            return r, f.read()

    def test_pool_command_moves_the_session_without_a_nudge(self):
        r, out = self.asked(FAKE_PROMPT="/pool:switch b")
        self.assertEqual(r.returncode, 0, r.stderr)
        # The turn stops there, so the prompt never reaches the model: one green line.
        self.assertEqual(json.loads(out), {"continue": False, "stopReason":
                                           "\033[32mclaude-pool: moving this session to b; it restarts in a moment\033[0m"})
        first, second = self.sessions()
        self.assertEqual((first["token"], second["token"]), ("tok-a", "tok-b"))
        self.assertEqual(second["args"][4:], ["--model", "haiku", "--resume", "sess-1"])
        self.assertIn({"event": "switch", "reason": "asked"},
                      [{k: e.get(k) for k in ("event", "reason")} for e in self.events()])
        self.assertIn("a was left as you asked; going on with b", r.stderr)

    def test_pool_command_is_a_plugin_and_a_prompt_hook(self):
        self.asked(FAKE_PROMPT="/pool:switch b")
        args = self.sessions()[0]["args"]
        self.assertEqual(args[2], "--plugin-dir")
        self.assertIn("--pool-prompt", self.sessions()[0]["settings"]["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"])

    def test_pool_command_without_a_name_stays_on_the_best_account(self):
        for prompt, why in (("/pool:switch", "a is already the best account; staying on it"),
                            ("/pool:switch a", "this session already runs on a")):
            shutil.rmtree(self.log, ignore_errors=True)
            r, out = self.asked(FAKE_PROMPT=prompt, FAKE_WAIT="1")
            self.assertEqual(json.loads(out)["stopReason"], f"\033[32mclaude-pool: {why}\033[0m")
            self.assertEqual([c["token"] for c in self.sessions()], ["tok-a"])  # and claude's own exit isn't a switch

    def test_pool_command_without_a_name_takes_the_best_free_account(self):
        # Since the session started, a went past the 90% margin: b is the pool's pick now.
        later = {"five_hour": 95, "resets_ts": time.time() + 3600, "checked_ts": time.time()}
        r, out = self.asked(FAKE_PROMPT="/pool:switch", FAKE_USAGE_A=json.dumps(later))
        self.assertIn("moving this session to b", out)
        self.assertEqual([c["token"] for c in self.sessions()], ["tok-a", "tok-b"])

    def test_pool_command_refuses_a_full_or_unknown_account(self):
        for want, why in (("c", "c is full until"), ("zzz", "no account zzz; the pool has a, b, c")):
            shutil.rmtree(self.log, ignore_errors=True)
            r, out = self.asked(FAKE_PROMPT="/pool:switch " + want, FAKE_WAIT="1")
            self.assertEqual(json.loads(out)["continue"], False)
            self.assertTrue(json.loads(out)["stopReason"].startswith("\033[31m"), out)  # red
            self.assertIn(why, json.loads(out)["stopReason"])
            self.assertEqual([c["token"] for c in self.sessions()], ["tok-a"], want)
        r, out = self.asked(FAKE_PROMPT="/pool:switch zzz", FAKE_WAIT="1", NO_COLOR="1")
        self.assertTrue(json.loads(out)["stopReason"].startswith("claude-pool: no account zzz"))

    def test_pool_status_and_log_show_in_the_session(self):
        r, out = self.asked(FAKE_PROMPT="/pool:status", FAKE_WAIT="1")
        stop = json.loads(out)
        self.assertEqual(stop["continue"], False)  # the model never sees it
        self.assertIn("\033[1;32m1\033[0m", stop["stopReason"])  # in colour: #1, the next account, green
        lines = re.sub(r"\033\[[0-9;]*m", "", stop["stopReason"]).splitlines()
        self.assertEqual(lines[0], "claude-pool")
        self.assertTrue(lines[1].startswith("#  account"), lines)
        self.assertIn("this session", next(x for x in lines if x.split()[1:2] == ["a"]))
        self.assertIn("full until", next(x for x in lines if x.split()[1:2] == ["c"]))
        self.assertEqual([c["token"] for c in self.sessions()], ["tok-a"])  # nothing switched
        r, out = self.asked(FAKE_PROMPT="/pool:log 3", FAKE_WAIT="1")
        lines = json.loads(out)["stopReason"].splitlines()
        self.assertEqual(lines[0], "claude-pool")
        self.assertTrue(1 < len(lines) <= 4, lines)

    def test_other_prompts_pass_through(self):
        r, out = self.asked(FAKE_PROMPT="/pool:switches and /pool:switch b", FAKE_WAIT="1")
        self.assertEqual(out, "")
        self.assertEqual([c["token"] for c in self.sessions()], ["tok-a"])

    def test_pool_switch_from_the_sessions_shell(self):
        r, out = self.asked(FAKE_BANG="b")
        self.assertEqual(out.strip(), "claude-pool: moving this session to b; it restarts in a moment")
        self.assertEqual([c["token"] for c in self.sessions()], ["tok-a", "tok-b"])

    def test_pool_switch_outside_a_session_fails(self):
        r = self.run_pool("--pool-switch", "b")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("only works inside a claude-pool session", r.stderr)

    # --- Orca ---------------------------------------------------------------------------------------------

    def orca(self, profiles, shared=True):
        """A fake Orca data folder: {profile id: (account uuid, email, fake login or None for logged out)}."""
        root = os.path.join(self.tmp, "orca")
        sessions = os.path.join(self.tmp, "claude-home", "projects")
        os.makedirs(sessions, exist_ok=True)
        for pid, (uuid, email, login) in profiles.items():
            home = os.path.join(root, "claude-profiles", pid, "home")
            os.makedirs(home)
            with open(os.path.join(home, ".claude.json"), "w") as f:
                json.dump({"oauthAccount": {"accountUuid": uuid, "emailAddress": email}}, f)
            if login:
                with open(os.path.join(home, ".credentials.json"), "w") as f:
                    f.write("{}")
                with open(os.path.join(home, "fake-login"), "w") as f:
                    f.write(login)
            if shared:  # as Orca does: every profile's conversations live in one ~/.claude/projects
                try:
                    os.symlink(sessions, os.path.join(home, "projects"))
                except OSError:
                    self.skipTest("no symlinks here")
        return root

    def home_of(self, root, pid):
        return os.path.join(root, "claude-profiles", pid, "home")

    def test_orca_accounts_are_found_once_each_and_logged_out_ones_skipped(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1"), "p2": ("u2", "bob@x.com", "tok-2"),
                          "p3": ("u1", "alice@x.com", "tok-1"), "p4": ("u4", "carol@x.com", None)})
        out = self.run_pool("--pool-status", CLAUDE_POOL_ORCA=root).stdout
        names = [l.split()[1] for l in out.splitlines()[1:]]
        # macOS keeps the login in the Keychain, so there a profile without .credentials.json still counts.
        self.assertEqual(sorted(names), ["orca-alice", "orca-bob"] + (["orca-carol"] if sys.platform == "darwin" else []))

    def test_orca_session_starts_on_the_panes_own_account_and_moves_on(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1"), "p2": ("u2", "bob@x.com", "limit-2")})
        r = self.run_pool("hi", "--model", "haiku", CLAUDE_POOL_ORCA=root, CLAUDE_CONFIG_DIR=self.home_of(root, "p2"))
        self.assertIn("orca-bob hit its usage limit; going on with orca-alice", r.stderr)
        first, second = self.sessions()
        self.assertEqual((first["token"], first["config_dir"]), ("limit-2", self.home_of(root, "p2")))
        self.assertEqual((second["token"], second["config_dir"]), ("tok-1", self.home_of(root, "p1")))
        self.assertEqual(second["args"][4:], ["--model", "haiku", "--resume", "sess-1",
                                              "Continue where you left off. Your last turn was cut off by a usage limit."])

    def test_orca_resume_is_picked_in_pool_order_not_the_panes_account(self):
        # Orca reopens a pane on its own account with --resume: the pool's pick (soonest weekly reset) wins.
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1"), "p2": ("u2", "bob@x.com", "tok-2")})
        soon = time.time() + 3600
        self.usage({"orca-alice": {"seven_day": 50, "seven_day_resets_ts": soon},
                    "orca-bob": {"seven_day": 10, "seven_day_resets_ts": soon + 86400}})
        pane = dict(CLAUDE_POOL_ORCA=root, CLAUDE_CONFIG_DIR=self.home_of(root, "p2"))
        self.run_pool("--resume", "abc", **pane)
        self.assertEqual(self.sessions()[-1]["token"], "tok-1")
        self.run_pool("hi", **pane)  # a new session still starts on the pane's own account
        self.assertEqual(self.sessions()[-1]["token"], "tok-2")

    def test_orca_and_tokens_mix_when_they_share_the_conversations(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "limit-1")})
        self.add("work", "tok-w")
        own = self.home_of(root, "p1")
        self.run_pool("hi", CLAUDE_POOL_ORCA=root, CLAUDE_CONFIG_DIR=own)
        self.assertEqual([(c["token"], c["config_dir"]) for c in self.sessions()], [("limit-1", own), ("tok-w", own)])

    def test_no_switch_to_an_account_that_cant_see_the_conversation(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1"), "p2": ("u2", "bob@x.com", "limit-2")}, shared=False)
        self.run_pool("hi", CLAUDE_POOL_ORCA=root, CLAUDE_CONFIG_DIR=self.home_of(root, "p2"), FAKE_WAIT="1")
        self.assertEqual([c["token"] for c in self.sessions()], ["limit-2"])

    def test_print_mode_leaves_orca_out_unless_on(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1")})
        self.run_pool("-p", "hi", CLAUDE_POOL_ORCA="auto", ORCA_USER_DATA_PATH=root)
        self.assertEqual(self.calls()[-1]["token"], "login")
        self.assertIn("orca-alice", self.run_pool("--pool-status", CLAUDE_POOL_ORCA="auto", ORCA_USER_DATA_PATH=root).stdout)
        self.run_pool("-p", "hi", CLAUDE_POOL_ORCA="on", ORCA_USER_DATA_PATH=root)
        self.assertEqual(self.calls()[-1]["token"], "tok-1")
        r = self.run_pool("-p", "hi", CLAUDE_POOL_ORCA="off", ORCA_USER_DATA_PATH=root)
        self.assertEqual(self.calls()[-1]["token"], "login", r.stderr)

    def test_orca_account_off_and_on(self):
        root = self.orca({"p1": ("u1", "alice@x.com", "tok-1"), "p2": ("u2", "bob@x.com", "tok-2")})
        self.assertEqual(self.run_pool("--pool-off", "orca-bob", CLAUDE_POOL_ORCA=root).returncode, 0)
        self.assertNotIn("orca-bob", self.run_pool("--pool-status", CLAUDE_POOL_ORCA=root).stdout)
        self.run_pool("--pool-on", "orca-bob", CLAUDE_POOL_ORCA=root)
        self.assertIn("orca-bob", self.run_pool("--pool-status", CLAUDE_POOL_ORCA=root).stdout)

    def test_dir_file_account(self):
        cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "fake-login"), "w") as f:
            f.write("tok-d")
        self.add("d", cfg, ext=".dir")
        self.run_pool("-p", "hi")
        self.assertEqual((self.calls()[-1]["token"], self.calls()[-1]["config_dir"]), ("tok-d", cfg))

    # --- status line ---------------------------------------------------------------------------------------

    def banner_folder(self, account, pool, statusline=None):
        folder = os.path.join(self.tmp, "banner")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "session.json"), "w") as f:
            json.dump({"account": account, "pool": {n: {"kind": "token", "sessions": s} for n, s in pool.items()}}, f)
        if statusline:
            with open(os.path.join(folder, "statusline.json"), "w") as f:
                json.dump({"type": "command", "command": statusline}, f)
        return folder

    def test_statusline_shows_account_usage_reset_and_next(self):
        now = time.time()
        folder = self.banner_folder("a", {"a": "/s", "b": "/s", "c": "/other"})
        self.usage({"b": {"five_hour": 12, "resets_ts": now + 3600}, "c": {"five_hour": 1, "resets_ts": now + 3600}})
        live = {"rate_limits": {"five_hour": {"used_percentage": 85.4, "resets_at": int(now) + 7200},
                                "seven_day": {"used_percentage": 40, "resets_at": int(now) + 90000}}}
        r = self.run_pool("--pool-statusline", folder, stdin=json.dumps(live), NO_COLOR="1")
        at = time.strftime("%H:%M", time.localtime(int(now) + 7200))
        # c can't see this session's conversations, so it is never next.
        self.assertEqual(r.stdout, f"pool: a · 85% · resets {at} · week 40% · next: b 12%\n")
        row = self.read_usage()["a"]  # the live numbers are kept for every caller
        self.assertEqual((row["five_hour"], row["seven_day"], row["resets_ts"]), (85.4, 40, int(now) + 7200))

    def test_statusline_never_takes_usage_back(self):
        # Another session saved 9% for this window; this idle session still holds its 5% from earlier.
        now = int(time.time())
        folder = self.banner_folder("a", {"a": "/s", "b": "/s"})
        self.usage({"a": {"five_hour": 9, "resets_ts": now + 7200, "seven_day": 30, "seven_day_resets_ts": now + 90000}})
        live = lambda pct, reset: {"rate_limits": {"five_hour": {"used_percentage": pct, "resets_at": reset}}}  # noqa: E731
        r = self.run_pool("--pool-statusline", folder, stdin=json.dumps(live(5, now + 7200 + 30)), NO_COLOR="1")
        self.assertIn("pool: a · 9%", r.stdout)
        self.assertEqual(self.read_usage()["a"]["five_hour"], 9)
        self.run_pool("--pool-statusline", folder, stdin=json.dumps(live(80, now - 100)))  # an earlier window
        self.assertEqual(self.read_usage()["a"]["five_hour"], 9)
        self.run_pool("--pool-statusline", folder, stdin=json.dumps(live(11, now + 7200)))  # more: kept
        self.assertEqual(self.read_usage()["a"]["five_hour"], 11)
        self.run_pool("--pool-statusline", folder, stdin=json.dumps(live(2, now + 20000)))  # a new window
        row = self.read_usage()["a"]
        self.assertEqual((row["five_hour"], row["resets_ts"], row["seven_day"]), (2, now + 20000, 30))

    def test_statusline_reruns_while_idle(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.run_pool("hi")
        self.assertEqual(self.sessions()[-1]["settings"]["statusLine"]["refreshInterval"], 30)

    def test_statusline_warns_in_red_with_no_next_account(self):
        now = time.time()
        folder = self.banner_folder("a", {"a": "/s", "b": "/s"})
        self.usage({"a": {"five_hour": 95, "resets_ts": now + 3600}, "b": {"status": "rejected", "resets_ts": now + 1800}})
        out = self.run_pool("--pool-statusline", folder, stdin="{}").stdout
        until = time.strftime("%H:%M", time.localtime(now + 1800))
        self.assertIn("\033[31m95%", out)  # past CLAUDE_POOL_SWITCH_AT
        self.assertIn(f"\033[31m⚠ no next account until {until}", out)

    def test_statusline_is_right_aligned_and_green_while_low(self):
        now = time.time()
        folder = self.banner_folder("a", {"a": "/s", "b": "/s"})
        self.usage({"a": {"five_hour": 4, "resets_ts": now + 3600}, "b": {"five_hour": 75, "resets_ts": now + 3600}})
        out = self.run_pool("--pool-statusline", folder, stdin="{}", COLUMNS="80").stdout
        self.assertIn("\033[32m4%", out)
        self.assertIn("\033[33m75%", out)  # b's use, from 70%
        plain = self.run_pool("--pool-statusline", folder, stdin="{}", COLUMNS="80", NO_COLOR="1").stdout.rstrip("\n")
        self.assertEqual(len(plain), 76)  # the terminal width less claude's indent and margin
        self.assertTrue(plain.startswith(" ") and plain.endswith("next: b 75%"))

    def test_statusline_keeps_the_one_it_replaces(self):
        folder = self.banner_folder("a", {"a": "/s"}, statusline="echo OUTER")
        lines = self.run_pool("--pool-statusline", folder, stdin="{}", NO_COLOR="1").stdout.splitlines()
        self.assertEqual(lines[0], "OUTER")
        self.assertTrue(lines[1].startswith("pool: a"))
        # Without one in --settings, the settings files' status line runs (Orca's lives in the config folder).
        cfg = os.path.join(self.tmp, "cfg")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "settings.json"), "w") as f:
            json.dump({"statusLine": {"type": "command", "command": "echo FROM-CONFIG"}}, f)
        os.remove(os.path.join(folder, "statusline.json"))
        out = self.run_pool("--pool-statusline", folder, stdin="{}", CLAUDE_CONFIG_DIR=cfg).stdout
        self.assertEqual(out.splitlines()[0], "FROM-CONFIG")

    def test_session_gets_the_status_line(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.run_pool("--settings", '{"statusLine": {"type": "command", "command": "mine"}}', "hi")
        s = self.sessions()[-1]["settings"]
        self.assertIn("--pool-statusline", s["statusLine"]["command"])
        self.run_pool("hi", CLAUDE_POOL_BANNER="off")
        self.assertNotIn("statusLine", self.sessions()[-1]["settings"])

    def test_short_rate_limit_does_not_switch(self):
        self.add("a", "blip-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600}})
        r = self.run_pool("hi", FAKE_WAIT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["token"] for c in self.sessions()], ["blip-a"])

    def test_no_free_account_left_keeps_the_session(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600},
                    "b": {"status": "rejected", "resets_ts": time.time() + 3600}})
        r = self.run_pool("hi", FAKE_WAIT="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([c["token"] for c in self.sessions()], ["limit-a"])
        self.assertIn("exhausted", [e["event"] for e in self.events()])

    @unittest.skipIf(os.name == "nt", "no SIGTERM to send on Windows")
    def test_terminate_ends_the_session_without_a_switch(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600},
                    "b": {"status": "rejected", "resets_ts": time.time() + 3600}})
        p = subprocess.Popen([sys.executable, POOL, "hi"], env=self.env, cwd=self.tmp, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        deadline = time.time() + 10
        while not self.sessions() and time.time() < deadline:
            time.sleep(0.1)
        time.sleep(1)
        p.terminate()
        p.communicate(timeout=20)
        self.assertNotEqual(p.returncode, 0)  # claude's own exit from the SIGTERM it was passed
        self.assertEqual(len(self.sessions()), 1)
        self.assertFalse((self.read_usage().get("a") or {}).get("running"))

    def test_resume_runs_in_the_sessions_folder(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 3600}})
        wt = os.path.join(self.tmp, "wt")
        os.makedirs(wt)
        self.run_pool("-w", "feat", "hi", FAKE_SESSION_CWD=wt)
        first, second = self.sessions()
        self.assertEqual(second["args"][4:6], ["--resume", "sess-1"])  # -w feat is not run again
        self.assertNotIn("-w", second["args"])
        self.assertEqual(os.path.realpath(second["cwd"]), os.path.realpath(wt))

    def test_own_settings_are_kept_and_get_the_hook(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        user = os.path.join(self.tmp, "mine.json")
        with open(user, "w") as f:
            json.dump({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}, f)
        self.run_pool("--settings", user, "hi")
        s = self.sessions()[0]["settings"]
        self.assertEqual(s["model"], "opus")
        self.assertIn("Stop", s["hooks"])
        self.assertIn("--pool-hook", s["hooks"]["StopFailure"][0]["hooks"][0]["command"])
        self.assertEqual(self.sessions()[0]["args"].count("--settings"), 1)
        self.run_pool("--settings", '{"model": "sonnet"}', "hi")
        self.assertEqual(self.sessions()[1]["settings"]["model"], "sonnet")

    def test_commands_one_account_and_switch_off_hand_over_to_claude(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        for args, env in ((["mcp", "list"], {}), (["--version"], {}), (["hi"], {"CLAUDE_POOL_SWITCH": "off"}),
                          (["hi"], {"CLAUDE_POOL_ACCOUNT": "a"})):
            self.run_pool(*args, **env)
            self.assertIsNone(self.calls()[-1]["settings"], (args, env))
            self.assertEqual(self.calls()[-1]["args"], args)

    def test_hook_saves_its_input(self):
        out = os.path.join(self.tmp, "f.json")
        r = self.run_pool("--pool-hook", out, stdin='{"error": "rate_limit"}')
        self.assertEqual(r.returncode, 0)
        with open(out) as f:
            self.assertEqual(json.load(f), {"error": "rate_limit"})

    def test_help_parsing_and_args(self):
        import importlib.util
        spec_ = importlib.util.spec_from_file_location("claude_pool", POOL)
        cp = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(cp)
        text = ("Usage: claude\n\nOptions:\n  --add-dir <directories...>            Dirs\n"
                "  --allowedTools, --allowed-tools <tools...>\n      Tools\n  -r, --resume [value]  Resume\n"
                "  --model <model>                       Model\n  -c, --continue  Go on\n  --verbose  Loud\n\n"
                "Commands:\n  mcp                                   MCP\n  plugin|plugins  Plugins\n"
                "  attach <id|name>  Attach\n")
        spec = cp.parse_help(text)
        self.assertEqual(spec["flags"], {"--add-dir": "many", "--allowedTools": "many", "--allowed-tools": "many",
                                         "-r": "optional", "--resume": "optional", "--model": "one"})
        self.assertEqual(spec["commands"], ["mcp", "plugin", "plugins", "attach"])
        self.assertEqual(cp.resume_args(["--resume", "old", "--model=x", "hi", "--", "-x"], spec, "S", "go"),
                         ["--model=x", "--resume", "S", "go"])
        self.assertEqual(cp.resume_args(["-r", "--verbose", "hi"], spec, "S", ""), ["--verbose", "--resume", "S"])
        self.assertTrue(cp.session_mode(["--model", "mcp", "hi"], spec))  # "mcp" is the model here
        self.assertFalse(cp.session_mode(["plugins", "list"], spec))
        self.assertEqual(cp.parse_help(""), {"flags": {}, "commands": []})

    @unittest.skipIf(os.name == "nt", "a .py file is not a command on Windows")
    def test_refuses_to_call_itself(self):
        r = self.run_pool("-p", "hi", CLAUDE_POOL_BINARY=POOL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("claude-pool itself", r.stderr)

    @unittest.skipIf(os.name == "nt", "a shebang script is not a command on Windows")
    def test_finds_claude_in_local_bin_off_path(self):
        local_bin = os.path.join(self.tmp, ".local", "bin")
        os.makedirs(local_bin)
        shutil.copy(self.fake, os.path.join(local_bin, "claude"))
        env = dict(self.env)
        del env["CLAUDE_POOL_BINARY"]
        r = subprocess.run([sys.executable, POOL, "-p", "hi"], capture_output=True, text=True, timeout=30, cwd=self.tmp,
                           env={**env, "PATH": os.path.dirname(sys.executable)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "echo:hi\n")

    def probes(self):
        return [c for c in self.calls() if "--system-prompt" in c["args"] and "Reply ok." in c["args"]]

    def test_stale_accounts_are_probed_before_the_pick(self):
        now = time.time()
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        # b looks emptier on old numbers, but another app has used it since: the probe sees 80%.
        self.usage({"a": {"seven_day": 30, "seven_day_resets_ts": now + 86400, "checked_ts": now - 3600},
                    "b": {"seven_day": 30, "seven_day_resets_ts": now + 2 * 86400, "checked_ts": now - 3600}})
        self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300", FAKE_UTIL_tok_a="0.95", FAKE_UTIL_tok_b="0.1")
        self.assertEqual(sorted(c["token"] for c in self.probes()), ["tok-a", "tok-b"])
        self.assertIn("--tools", self.probes()[0]["args"])
        with open(self.state) as f:
            rows = json.load(f)
        self.assertEqual(rows["a"]["five_hour"], 95.0)
        self.assertEqual(self.calls()[-1]["token"], "tok-b")  # a is past the 90% margin now

    def test_interactive_starts_on_saved_numbers_and_checks_after(self):
        now = time.time()
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 30, "checked_ts": now - 3600}, "b": {"seven_day": 40, "checked_ts": now - 3600}})
        self.run_pool("--version", CLAUDE_POOL_FRESH="300")
        self.assertEqual(self.calls()[0]["args"], ["--version"])  # claude first, no wait for a check
        for _ in range(100):
            if len(self.probes()) == 2:
                break
            time.sleep(0.1)
        self.assertEqual(sorted(c["token"] for c in self.probes()), ["tok-a", "tok-b"])

    def test_interactive_with_no_numbers_checks_first(self):
        self.add("a", "tok-a")
        self.run_pool("--version", CLAUDE_POOL_FRESH="300")
        self.assertEqual(self.calls()[0]["token"], "tok-a")
        self.assertIn("Reply ok.", self.calls()[0]["args"])

    def test_fresh_numbers_are_not_probed(self):
        self.add("a", "tok-a")
        self.usage({"a": {"seven_day": 30, "checked_ts": time.time() - 10}})
        self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300")
        self.assertEqual(self.probes(), [])

    def test_api_key_accounts_are_not_probed(self):
        self.add("api", "sk-1", ext=".key")
        self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300")
        self.assertEqual(self.probes(), [])

    def test_broken_token_is_skipped(self):
        self.add("a", "bad-a")
        self.add("b", "tok-b")
        r = self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300")
        self.assertEqual(r.stdout, "echo:hi\n")
        self.assertEqual(self.calls()[-1]["token"], "tok-b")
        self.assertIn("broken: Invalid bearer token", self.run_pool("--pool-status").stdout)

    def test_org_refused_token_is_marked_broken_and_run_moves_on(self):
        self.add("a", "orgoff-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}, "b": {"seven_day": 5}})
        r = self.run_pool("-p", "hi")
        self.assertEqual(r.stdout, "echo:hi\n")
        self.assertEqual([c["token"] for c in self.calls()], ["orgoff-a", "tok-b"])
        self.assertIn("broken: Your organization has disabled", self.run_pool("--pool-status").stdout)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-b")
        self.assertEqual(len(self.calls()), 3)

    def test_check_marks_org_refused_token_broken(self):
        self.add("a", "orgoff-a")
        r = self.run_pool("--pool-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("token refused", r.stdout)

    def test_pool_refresh_probes_every_account(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"checked_ts": time.time()}})
        r = self.run_pool("--pool-refresh", FAKE_UTIL="0.5")
        self.assertEqual(len(self.probes()), 2)
        with open(self.state) as f:
            rows = json.load(f)
        self.assertEqual((rows["a"]["five_hour"], rows["b"]["five_hour"]), (50.0, 50.0))  # both parallel checks saved
        self.assertIn("50%", r.stdout)
        self.assertIn("0m ago", r.stdout)

    def test_pool_watch_refreshes_and_keeps_going(self):
        self.add("a", "tok-a")
        p = subprocess.Popen([sys.executable, POOL, "--pool-watch", "1"], stdout=subprocess.PIPE, text=True,
                             env={**self.env, "FAKE_UTIL": "0.5"}, cwd=self.tmp)
        try:
            out = "".join(p.stdout.readline() for _ in range(4))  # header, account, blank, "next check"
            self.assertIsNone(p.poll())  # still waiting for the next check
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
        self.assertIn("50%", out)
        self.assertIn("next check in 1 min", out)
        self.assertEqual(len(self.probes()), 1)

    def test_pool_watch_rejects_bad_minutes(self):
        r = self.run_pool("--pool-watch", "soon")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("minutes between checks", r.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_shared_folder_files_are_group_writable(self):
        shared = os.path.join(self.tmp, "shared")
        os.makedirs(shared)
        os.chmod(shared, 0o2770)
        self.add("a", "tok-a")
        old = os.umask(0o022)
        try:
            self.run_pool("-p", "hi", CLAUDE_POOL_STATE=os.path.join(shared, "usage.json"))
        finally:
            os.umask(old)
        for f in ("usage.json", "usage.json.lock"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(shared, f)).st_mode), 0o660, f)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_private_folder_keeps_umask(self):
        self.add("a", "tok-a")
        os.chmod(self.tmp, 0o700)
        self.run_pool("-p", "hi")
        self.assertEqual(stat.S_IMODE(os.stat(self.state).st_mode) & 0o020, 0)

    @unittest.skipIf(os.name == "nt" or os.getuid() == 0, "POSIX permissions, not as root")
    def test_unreadable_token_is_skipped(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        os.chmod(os.path.join(self.pool, "a.token"), 0)
        r = self.run_pool("-p", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("skipping a.token", r.stderr)
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_status_lists_accounts(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 30, "runs": 4, "seven_day_resets_ts": time.time() + 86400}})
        r = self.run_pool("--pool-status")
        self.assertIn("b", r.stdout)
        self.assertIn("next", [l for l in r.stdout.splitlines() if l.startswith("1  a ")][0])
        self.assertNotIn("\033[", r.stdout)  # no colour into a pipe

    def test_status_ranks_accounts_in_the_order_they_are_used(self):
        for n in ("a", "b", "c", "d"):
            self.add(n, "tok-" + n)
        soon = time.time() + 3600
        self.usage({"a": {"seven_day": 20, "seven_day_resets_ts": soon + 7200},
                    "b": {"seven_day": 50, "seven_day_resets_ts": soon},  # its week resets first: drain it first
                    "c": {"status": "rejected", "resets_ts": soon}})  # full: last, no number
        rows = [l.split()[:2] for l in self.run_pool("--pool-status").stdout.splitlines()[1:]]
        self.assertEqual(rows, [["1", "b"], ["2", "a"], ["3", "d"], ["-", "c"]])

    # --- reruns -----------------------------------------------------------------------------------------------

    def events(self):
        path = os.path.join(self.tmp, "events.log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(l) for l in f]

    def test_cut_off_after_tools_is_not_rerun(self):
        self.add("a", "limittool-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}})
        r = self.run_pool("-p", "hi", "--output-format", "json")
        self.assertEqual([c["token"] for c in self.calls()], ["limittool-a"])
        self.assertTrue(json.loads(r.stdout)["pool_cut_off"])
        self.assertIn("not rerunning", r.stderr)
        self.assertIn("cut_off_not_rerun", [e["event"] for e in self.events()])

    def test_rerun_always_reruns_after_tools(self):
        self.add("a", "limittool-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}})
        self.run_pool("-p", "hi", CLAUDE_POOL_RERUN="always")
        self.assertEqual([c["token"] for c in self.calls()], ["limittool-a", "tok-b"])

    def test_rerun_never(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}})
        self.run_pool("-p", "hi", CLAUDE_POOL_RERUN="never")
        self.assertEqual([c["token"] for c in self.calls()], ["limit-a"])

    def test_cut_off_is_logged_with_its_raw_result(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 50, "seven_day_resets_ts": time.time() + 86400}})
        self.run_pool("-p", "hi")
        ev = {e["event"]: e for e in self.events()}
        self.assertEqual((ev["cut_off"]["result"], ev["cut_off"]["status"]), ("You've hit your session limit", "rejected"))
        self.assertEqual((ev["switch"]["account"], ev["switch"]["to"]), ("a", "b"))
        self.assertEqual([e["account"] for e in self.events() if e["event"] == "run"], ["a", "b"])

    # --- checks -----------------------------------------------------------------------------------------------

    def test_network_error_does_not_mark_token_broken(self):
        self.add("a", "net-a")
        self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300")
        with open(self.state) as f:
            row = json.load(f)["a"]
        self.assertNotIn("error_ts", row)
        self.assertIn("checked_ts", row)  # not checked again on the next call
        self.assertIn("probe_failed", [e["event"] for e in self.events()])

    def test_cli_without_usage_numbers_is_reported(self):
        self.add("a", "nousage-a")
        r = self.run_pool("--pool-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("printed no usage numbers", r.stdout)
        self.assertIn("cli_changed", [e["event"] for e in self.events()])

    def test_waits_at_most_5s_for_another_callers_check(self):
        sys.path.insert(0, ROOT)
        import claude_pool
        self.add("a", "tok-a")
        os.environ["CLAUDE_POOL_STATE"] = self.state
        try:
            with claude_pool.locked(self.state + ".refresh"):
                start = time.time()
                r = self.run_pool("-p", "hi", CLAUDE_POOL_FRESH="300")
                took = time.time() - start
        finally:
            del os.environ["CLAUDE_POOL_STATE"]
        self.assertEqual(r.stdout, "echo:hi\n")
        self.assertLess(took, 9)
        self.assertEqual(self.probes(), [])
        self.assertIn("refresh_skipped", [e["event"] for e in self.events()])

    # --- parallel runs ----------------------------------------------------------------------------------------

    def test_busy_account_near_the_margin_gets_no_second_run(self):
        now = time.time()
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        rows = {"a": {"five_hour": 80, "resets_ts": now + 600, "seven_day": 40, "seven_day_resets_ts": now + 86400,
                      "running": [{"pid": os.getpid(), "host": __import__("socket").gethostname(), "ts": now}]},
                "b": {"five_hour": 10, "resets_ts": now + 600, "seven_day": 10, "seven_day_resets_ts": now + 5 * 86400}}
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-b")
        rows["a"]["running"] = [{"pid": 999999999, "host": __import__("socket").gethostname(), "ts": now}]  # gone
        self.usage(rows)
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[-1]["token"], "tok-a")

    def test_running_mark_is_removed_after_the_run(self):
        self.add("a", "tok-a")
        self.run_pool("-p", "hi")
        with open(self.state) as f:
            self.assertEqual(json.load(f)["a"]["running"], [])

    # --- alerts -----------------------------------------------------------------------------------------------

    def test_alert_runs_once_an_hour_per_problem(self):
        out = os.path.join(self.tmp, "alerts.txt")
        script = os.path.join(self.tmp, "alert.py")
        with open(script, "w") as f:
            f.write("import os, sys\nopen(%r, 'a').write(os.environ['CLAUDE_POOL_EVENT'] + '|' + "
                    "os.environ['CLAUDE_POOL_MESSAGE'] + '|' + sys.stdin.read()[:1] + '\\n')\n" % out)
        self.add("a", "tok-a")
        self.usage({"a": {"seven_day": 100, "seven_day_resets_ts": time.time() + 9999}})
        cmd = f'"{sys.executable}" "{script}"'
        for _ in range(2):
            self.assertEqual(self.run_pool("-p", "hi", CLAUDE_POOL_ALERT=cmd).returncode, 75)
        with open(out) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("exhausted|claude-pool on "))
        self.assertTrue(lines[0].endswith("|{"))  # the event as JSON on stdin

    # --- commands ---------------------------------------------------------------------------------------------

    def test_help_lists_commands_and_settings(self):
        r = self.run_pool("--pool-help")
        for word in ("--pool-add", "--pool-check", "--pool-log", "CLAUDE_POOL_RERUN", "CLAUDE_POOL_ALERT", self.pool):
            self.assertIn(word, r.stdout)

    def test_unknown_pool_command_fails(self):
        r = self.run_pool("--pool-nope")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--pool-help", r.stderr)

    def test_add_writes_a_private_token_and_checks_it(self):
        r = self.run_pool("--pool-add", "work", stdin="tok-work\n", CLAUDE_POOL_FRESH="300")
        self.assertEqual(r.returncode, 0, r.stderr)
        path = os.path.join(self.pool, "work.token")
        with open(path) as f:
            self.assertEqual(f.read(), "tok-work\n")
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual([c["token"] for c in self.probes()], ["tok-work"])
        self.assertIn("work", r.stdout)
        self.assertNotEqual(self.run_pool("--pool-add", "work", stdin="x").returncode, 0)  # no overwrite
        self.assertNotEqual(self.run_pool("--pool-add", "../evil", stdin="x").returncode, 0)

    def test_add_api_key_becomes_key_file(self):
        self.run_pool("--pool-add", "api", stdin="sk-ant-api03-xyz")
        self.assertTrue(os.path.exists(os.path.join(self.pool, "api.key")))

    def test_off_and_on(self):
        self.add("a", "tok-a")
        self.assertEqual(self.run_pool("--pool-off", "a").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.pool, "a.token.off")))
        self.assertNotEqual(self.run_pool("--pool-off", "a").returncode, 0)
        self.assertEqual(self.run_pool("--pool-on", "a").returncode, 0)
        self.assertTrue(os.path.exists(os.path.join(self.pool, "a.token")))

    def test_log_shows_events(self):
        self.add("a", "tok-a")
        self.run_pool("-p", "hi")
        r = self.run_pool("--pool-log", "5")
        self.assertIn(" run ", r.stdout)
        self.assertIn("account=a", r.stdout)

    def test_check_passes_with_a_good_token(self):
        self.add("a", "tok-a")
        r = self.run_pool("--pool-check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("a: token works", r.stdout)

    def test_check_fails_on_a_refused_token(self):
        self.add("a", "bad-a")
        r = self.run_pool("--pool-check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("token refused", r.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_world_readable_token_warns(self):
        self.add("a", "tok-a")
        os.chmod(os.path.join(self.pool, "a.token"), 0o644)
        self.assertIn("can be read by every user", self.run_pool("--pool-status").stderr)
        self.assertIn("WARN  a.token: every user can read it", self.run_pool("--pool-check").stdout)


    def write_env(self, folder, text):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, ".env"), "w") as f:
            f.write(text)

    def test_account_from_variable_and_its_secret_stays_out_of_claude(self):
        self.run_pool("-p", "hi", CLAUDE_POOL_TOKEN_WORK_2="tok-w")
        c = self.calls()[0]
        self.assertEqual((c["token"], c["pool_secrets"]), ("tok-w", []))
        self.assertIn("work-2", self.run_pool("--pool-status", CLAUDE_POOL_TOKEN_WORK_2="tok-w").stdout)

    def test_file_wins_over_variable_with_the_same_name(self):
        self.add("work", "tok-file")
        self.run_pool("-p", "hi", CLAUDE_POOL_TOKEN_WORK="tok-var")
        self.assertEqual(self.calls()[0]["token"], "tok-file")

    def test_dotenv_from_a_parent_folder(self):
        app = os.path.join(self.tmp, "app")
        self.write_env(app, "APP_KEY=secret\nDB_PASSWORD=x\n"
                            "export CLAUDE_POOL_TOKEN_MAIN=\"tok-env\"\nCLAUDE_POOL_ACCOUNT=main  # pinned\n")
        os.makedirs(os.path.join(app, "public"))
        self.run_pool("-p", "hi", cwd=os.path.join(app, "public"))
        c = self.calls()[0]
        self.assertEqual((c["token"], c["pool_secrets"]), ("tok-env", []))

    def test_dotenv_only_reads_pool_lines_and_real_variables_win(self):
        self.write_env(self.tmp, "CLAUDE_CODE_OAUTH_TOKEN=tok-app\nCLAUDE_POOL_ACCOUNT=nope\n")
        self.add("a", "tok-a")
        self.run_pool("-p", "hi", CLAUDE_POOL_ACCOUNT="a")
        self.assertEqual(self.calls()[0]["token"], "tok-a")
        os.remove(os.path.join(self.pool, "a.token"))
        self.write_env(self.tmp, "CLAUDE_CODE_OAUTH_TOKEN=tok-app\n")
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[1]["token"], "login")

    def test_dotenv_file_setting(self):
        conf = os.path.join(self.tmp, "conf")
        self.write_env(conf, "CLAUDE_POOL_KEY_API='sk-9'\n")
        self.run_pool("-p", "hi", CLAUDE_POOL_ENV_FILE=os.path.join(conf, ".env"))
        self.assertEqual(self.calls()[0]["token"], "key:sk-9")


if __name__ == "__main__":
    unittest.main()
