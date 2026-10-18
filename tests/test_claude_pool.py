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
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"args": args, "token": tok}) + "\n")
prompt = args[args.index("-p") + 1] if "-p" in args and args.index("-p") + 1 < len(args) and not args[args.index("-p") + 1].startswith("-") else sys.stdin.read()
reset = int(time.time()) + 3600
def out(e): print(json.dumps(e), flush=True)
out({"type": "system", "subtype": "init"})
if tok.startswith("limit"):
    out({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": reset,
         "unifiedWindows": {"five_hour": {"utilization": 1.0, "resetsAt": reset}, "seven_day": {"utilization": 0.5, "resetsAt": reset + 86400}}}})
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "You've hit your session limit"})
    sys.exit(1)
util = float(os.environ.get("FAKE_UTIL", "0.2"))
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
        self.log = os.path.join(self.tmp, "log")
        self.state = os.path.join(self.tmp, "usage.json")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_POOL", "ANTHROPIC", "CLAUDE_CODE"))}
        self.env.update(CLAUDE_POOL_DIR=self.pool, CLAUDE_POOL_STATE=self.state, CLAUDE_POOL_BINARY=self.fake,
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
        with open(self.log) as f:
            return [json.loads(l) for l in f]

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

    def test_picks_lowest_weekly_usage_and_records_it(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 60, "five_hour": 1}, "b": {"seven_day": 10, "five_hour": 90}})
        self.run_pool("-p", "hi", FAKE_UTIL="0.4")
        self.assertEqual(self.calls()[0]["token"], "tok-b")
        with open(self.state) as f:
            row = json.load(f)["b"]
        self.assertEqual((row["five_hour"], row["seven_day"], row["runs"]), (40.0, 20.0, 1))

    def test_skips_account_at_limit(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 1, "status": "rejected", "resets_ts": time.time() + 600}, "b": {"seven_day": 50}})
        self.run_pool("-p", "hi")
        self.assertEqual(self.calls()[0]["token"], "tok-b")

    def test_limit_cut_off_reruns_on_next_account_with_stdin_prompt(self):
        self.add("a", "limit-a")
        self.add("b", "tok-b")
        self.usage({"b": {"seven_day": 5}})
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

    def test_refuses_to_call_itself(self):
        r = self.run_pool("-p", "hi", CLAUDE_POOL_BINARY=POOL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("claude-pool itself", r.stderr)

    def test_status_lists_accounts(self):
        self.add("a", "tok-a")
        self.add("b", "tok-b")
        self.usage({"a": {"seven_day": 30, "runs": 4}})
        r = self.run_pool("--pool-status")
        self.assertIn("b", r.stdout)
        self.assertIn("<- next", [l for l in r.stdout.splitlines() if l.startswith("b ")][0])


if __name__ == "__main__":
    unittest.main()
