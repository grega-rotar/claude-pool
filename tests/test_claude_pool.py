"""Tests for claude-pool against a fake claude. Run: python3 -m unittest discover tests"""
import json
import os
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
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ("key:" + os.environ["ANTHROPIC_API_KEY"] if os.environ.get("ANTHROPIC_API_KEY") else "login")
os.makedirs(os.environ["FAKE_LOG"], exist_ok=True)  # one file per call: parallel calls can't lose a line
with open(os.path.join(os.environ["FAKE_LOG"], "%020d-%d.json" % (time.time_ns(), os.getpid())), "w") as f:
    f.write(json.dumps({"args": args, "token": tok}))
prompt = args[args.index("-p") + 1] if "-p" in args and args.index("-p") + 1 < len(args) and not args[args.index("-p") + 1].startswith("-") else sys.stdin.read()
reset = int(time.time()) + 3600
def out(e): print(json.dumps(e), flush=True)
out({"type": "system", "subtype": "init"})
if tok.startswith("bad"):
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "Invalid bearer token"})
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
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_POOL", "ANTHROPIC", "CLAUDE_CODE"))}
        self.env.update(CLAUDE_POOL_FRESH="0", CLAUDE_POOL_DIR=self.pool, CLAUDE_POOL_STATE=self.state, CLAUDE_POOL_BINARY=self.fake,
                        FAKE_LOG=self.log)

    def add(self, name, secret, ext=".token"):
        with open(os.path.join(self.pool, name + ext), "w") as f:
            f.write(secret + "\n")

    def usage(self, rows):
        with open(self.state, "w") as f:
            json.dump(rows, f)

    def run_pool(self, *args, stdin=None, **env):
        return subprocess.run([sys.executable, POOL, *args], capture_output=True, text=True, input=stdin,
                              env={**self.env, **env}, timeout=30)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        out = []
        for name in sorted(os.listdir(self.log)):
            with open(os.path.join(self.log, name)) as f:
                out.append(json.loads(f.read()))
        return out

    def test_no_accounts_uses_normal_login(self):
        r = self.run_pool("-p", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "echo:hi\n")
        self.assertEqual(self.calls()[0]["token"], "login")

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
        self.assertEqual([e["type"] for e in lines], ["system", "rate_limit_event", "result"])

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

    @unittest.skipIf(os.name == "nt", "a .py file is not a command on Windows")
    def test_refuses_to_call_itself(self):
        r = self.run_pool("-p", "hi", CLAUDE_POOL_BINARY=POOL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("claude-pool itself", r.stderr)

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

    def test_pool_refresh_probes_every_account(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"checked_ts": time.time()}})
        r = self.run_pool("--pool-refresh", FAKE_UTIL="0.5")
        self.assertEqual(len(self.probes()), 2)
        with open(self.state) as f:
            rows = json.load(f)
        self.assertEqual((rows["a"]["five_hour"], rows["b"]["five_hour"]), (50.0, 50.0))  # both parallel checks saved
        self.assertIn("50.0", r.stdout)
        self.assertIn("0m ago", r.stdout)

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
        self.assertIn("<- next", [l for l in r.stdout.splitlines() if l.startswith("a ")][0])

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


if __name__ == "__main__":
    unittest.main()
