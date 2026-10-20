"""Tests for the pace strategy, the r estimate and the simulation, in process. Run: python3 -m unittest discover tests"""
import builtins
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS))
sys.path.insert(0, TESTS)
import claude_pool as cp  # noqa: E402
import simulate  # noqa: E402

NOW = 1_800_000_000
H = 3600


def week(used, hours, r=None, n=200, checked=NOW, **extra):
    """A usage row with a weekly window `used` % full that resets in `hours`, and a learned r."""
    row = {"seven_day": used, "seven_day_resets_ts": NOW + hours * H, "checked_ts": checked, **extra}
    if r is not None:
        row["pace"] = {"seven_day": {"r": r, "n": n}}
    return row


class PaceTest(unittest.TestCase):
    def setUp(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_POOL")}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def pick(self, rows, **kw):
        return cp.choose({n: ("token", "") for n in rows}, rows, now=NOW, **kw)

    # --- picking ----------------------------------------------------------------------------------------------

    def test_pace_is_the_default_and_unknown_values_fall_back_to_it(self):
        self.assertEqual(cp.strategy(), "pace")
        os.environ["CLAUDE_POOL_STRATEGY"] = "nonsense"
        self.assertEqual(cp.strategy(), "pace")
        os.environ["CLAUDE_POOL_STRATEGY"] = "drain"
        self.assertEqual(cp.strategy(), "drain")

    def test_one_account(self):
        self.assertEqual(self.pick({"a": week(40, 50, r=20)}), "a")
        self.assertEqual(self.pick({"a": {}}), "a")

    def test_week_that_resets_with_room_to_spare_goes_first(self):
        # a: 50% left, resets in 5 hours: even non-stop use can't spend it (p >= 1). b has more room, but keeps it.
        rows = {"a": week(50, 5, r=20), "b": week(10, 100, r=20)}
        self.assertGreaterEqual(cp.pace(rows["a"], NOW)[2], 1)
        self.assertEqual(self.pick(rows), "a")

    def test_saves_the_account_that_would_run_dry_before_its_reset(self):
        # a is a small plan whose week resets first: drain spends it, pace keeps it for when it's needed.
        rows = {"a": week(30, 72, r=40), "b": week(30, 120, r=10)}
        os.environ["CLAUDE_POOL_STRATEGY"] = "drain"
        self.assertEqual(self.pick(rows), "a")
        os.environ["CLAUDE_POOL_STRATEGY"] = "pace"
        self.assertEqual(self.pick(rows), "b")

    def test_low_weekly_room_goes_last(self):
        self.assertEqual(self.pick({"a": week(80, 140, r=15), "b": week(20, 140, r=15)}), "b")

    def test_bigger_plan_goes_first_at_the_same_percent(self):
        self.assertEqual(self.pick({"small": week(50, 72, r=30), "big": week(50, 72, r=5)}), "big")

    def test_past_the_5h_margin_only_as_last_resort(self):
        rows = {"a": week(10, 100, r=10, five_hour=92, resets_ts=NOW + 600), "b": week(50, 100, r=10)}
        self.assertEqual(self.pick(rows), "b")
        rows["b"].update(five_hour=95, resets_ts=NOW + 600)
        self.assertIn(self.pick(rows), ("a", "b"))
        self.assertIsNotNone(self.pick(rows))

    def test_busy_account_gets_no_second_run(self):
        me = [{"pid": os.getpid(), "host": cp.HOST, "ts": NOW}]
        rows = {"a": week(10, 100, r=10, five_hour=80, resets_ts=NOW + 600, running=me), "b": week(40, 100, r=10)}
        self.assertEqual(self.pick(rows), "b")

    def test_all_full_is_none(self):
        rows = {"a": week(100, 10), "b": {"five_hour": 100, "resets_ts": NOW + 600, "status": "rejected"}}
        self.assertIsNone(self.pick(rows))

    def test_refused_token_is_skipped(self):
        self.assertEqual(self.pick({"a": week(10, 100, r=10, error_ts=NOW - 10), "b": week(60, 100, r=10)}), "b")

    def test_near_equal_values_tie_and_the_5h_window_decides(self):
        # b has a touch more weekly room, but within PACE_STEP: a's live 5-hour window goes first, as in drain.
        rows = {"a": week(40, 100, r=10, five_hour=30, resets_ts=NOW + 3 * H), "b": week(38, 100, r=10)}
        self.assertEqual(self.pick(rows), "a")
        rows["a"]["seven_day"] = 41  # a little more use doesn't flip the pick
        self.assertEqual(self.pick(rows), "a")

    def test_identical_accounts_spread_over_servers_and_stay_put_on_one(self):
        rows = {f"acct{i}": week(20, 100, r=10) for i in range(5)}
        picks = set()
        for server in (f"server{i}" for i in range(10)):
            os.environ["CLAUDE_POOL_SERVER_ID"] = server
            first = self.pick(rows)
            self.assertEqual({self.pick(rows) for _ in range(5)}, {first})
            picks.add(first)
        self.assertGreater(len(picks), 1)

    def test_server_affinity_never_beats_room(self):
        rows = {"a": week(85, 140, r=10), "b": week(10, 140, r=10)}
        for server in (f"server{i}" for i in range(10)):
            os.environ["CLAUDE_POOL_SERVER_ID"] = server
            self.assertEqual(self.pick(rows), "b")

    def test_hundred_accounts(self):
        rows = {f"acct{i:03d}": week(60 + i % 30, 100 + i, r=10 + i % 7) for i in range(100)}
        rows["acct042"] = week(40, 4, r=12)  # resets in 4 hours with room left
        self.assertEqual(self.pick(rows), "acct042")

    def test_the_callers_own_login_still_comes_first_while_it_has_room(self):
        self.assertEqual(self.pick({"a": week(50, 5, r=20), "own": week(10, 100, r=20)}, prefer="own"), "own")

    # --- missing information ----------------------------------------------------------------------------------

    def test_no_weekly_numbers_is_no_weekly_limit(self):
        for row in ({}, {"seven_day": None, "seven_day_resets_ts": None}, {"five_hour": 10}):
            self.assertEqual(cp.pace(row, NOW)[0], "none")
        # Unlimited: before an account that would spend weekly room.
        self.assertEqual(self.pick({"u": {"five_hour": 10, "resets_ts": NOW + H}, "k": week(50, 84, r=10)}), "u")

    def test_no_weekly_limit(self):
        self.assertEqual(cp.pace({"weekly": "none"}, NOW)[0], "none")
        self.assertEqual(cp.pace({}, NOW, kind="key")[0], "none")
        # No weekly cost: before a weekly account that would spend scarce room...
        self.assertEqual(self.pick({"team": {"weekly": "none", "five_hour": 50, "resets_ts": NOW + H}, "pro": week(20, 100, r=15)}), "team")
        # ...but after one whose room is lost at its reset anyway.
        self.assertEqual(self.pick({"team": {"weekly": "none"}, "pro": week(40, 3, r=15)}), "pro")
        # Its 5-hour limit still counts.
        self.assertEqual(self.pick({"team": {"weekly": "none", "five_hour": 100, "resets_ts": NOW + H}, "pro": week(20, 100, r=15)}), "pro")

    def test_stale_numbers_count_but_never_as_room_to_spare(self):
        row = week(50, 5, r=20, checked=NOW - 3 * 86400)
        state, s, p = cp.pace(row, NOW)[:3]
        self.assertEqual((state, p), ("stale", None))
        self.assertLess(s, cp.pace(week(50, 5, r=20), NOW)[1])

    def test_no_5h_numbers(self):
        self.assertEqual(self.pick({"a": {"seven_day": 10, "seven_day_resets_ts": NOW + 50 * H, "checked_ts": NOW}}), "a")

    def test_reset_week_counts_as_a_full_week(self):
        state, s = cp.pace(week(90, -1, r=10), NOW)[:2]
        self.assertEqual((state, s), ("known", 1.0))

    def test_manual_r(self):
        os.environ["CLAUDE_POOL_WINDOW_WEEK"] = "12.5"
        self.assertEqual(cp.ratio({}), (12.5, "set"))
        self.assertAlmostEqual(cp.pace(week(50, 50), NOW)[2], (50 / 12.5) / 10)

    def test_no_r_until_enough_was_learned(self):
        self.assertEqual(cp.ratio(week(10, 50, r=20, n=10)), (None, None))
        r, sure = cp.ratio(week(10, 50, r=20, n=40))
        self.assertGreater(r, 20)  # the margin: never less careful than what was learned
        self.assertEqual(sure, "low")
        self.assertEqual(cp.ratio(week(10, 50, r=20, n=150))[1], "high")

    def test_broken_state_never_raises(self):
        for junk in ({"pace": "x"}, {"pace": {"seven_day": [1]}}, {"weeks": {"seven_day_opus": "x"}}, {"weeks": [1, 2]},
                     {"pace": {"seven_day": {"r": "a", "n": None}}}, {"pace": {"seven_day": {"a": "x", "s": 3}}}):
            row = {**week(20, 50), **junk}
            cp.pace(row, NOW)
            cp.learn(row, NOW)
            self.pick({"a": row})

    def test_corrupt_usage_file_reads_as_empty(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.environ["CLAUDE_POOL_STATE"] = os.path.join(tmp, "usage.json")
        with open(os.environ["CLAUDE_POOL_STATE"], "w") as f:
            f.write('{"a": {"seven_day": 4')  # cut off mid-write
        self.assertEqual(cp.load_usage(), {})
        self.assertEqual(cp.choose({"a": ("token", "")}, cp.load_usage()), "a")

    def test_pick_makes_no_calls_and_reads_no_files(self):
        rows = {f"a{i}": week(i, 50 + i, r=10 + i) for i in range(10)}
        boom = mock.Mock(side_effect=AssertionError("no I/O in the pick"))
        with mock.patch.object(subprocess, "run", boom), mock.patch.object(subprocess, "Popen", boom), \
                mock.patch.object(builtins, "open", boom):
            self.assertIsNotNone(self.pick(rows))

    # --- learning r -------------------------------------------------------------------------------------------

    def readings(self, row, *steps):
        for five, wk in steps:
            row.update(five_hour=five, seven_day=wk)
            cp.learn(row, NOW)
        return row.get("pace", {}).get("seven_day", {})

    def fresh_row(self):
        return {"resets_ts": NOW + 4 * H, "seven_day_resets_ts": NOW + 90 * H}

    def test_r_from_usage_gains(self):
        st = self.readings(self.fresh_row(), (10, 10), (30, 14))
        self.assertEqual((st["r"], st["n"]), (20.0, 20))

    def test_rounding_cancels_out_over_samples(self):
        # 5-point steps where the weekly % moves 1 point every other step (true r = 10)
        st = self.readings(self.fresh_row(), (0, 20), (5, 20), (10, 21), (15, 21), (20, 22), (25, 22), (30, 23))
        self.assertEqual(st["r"], 10.0)

    def test_small_gains_wait_for_more(self):
        row = self.fresh_row()
        st = self.readings(row, (10, 10), (12, 11), (14, 11))
        self.assertNotIn("r", st)
        st = self.readings(row, (16, 12))  # 6 points since the first reading: one sample over all of it
        self.assertAlmostEqual(st["r"], 100 * 2 / 6, places=1)

    def test_no_5h_gain_is_no_sample(self):
        st = self.readings(self.fresh_row(), (10, 10), (10, 15))
        self.assertNotIn("r", st)

    def test_window_resets_start_over(self):
        row = self.fresh_row()
        self.readings(row, (60, 30))
        row["resets_ts"] = NOW + 9 * H  # a new 5-hour window
        st = self.readings(row, (10, 31), (30, 35))
        self.assertEqual(st["r"], 20.0)  # (30-10) and (35-31), nothing across the reset

    def test_numbers_going_down_are_no_sample(self):
        row = self.fresh_row()
        st = self.readings(row, (50, 30), (40, 31), (60, 35))
        self.assertEqual(st["r"], 20.0)  # from the reading after the drop

    def test_changed_workload_drops_the_old_samples(self):
        row = self.fresh_row()
        steps = [(i * 10, 10 + i) for i in range(9)]  # r = 10
        st = self.readings(row, *steps)
        self.assertEqual(st["r"], 10.0)
        st = self.readings(row, (140, 48))  # a 60-point step that took 30 weekly points: r = 50, 5x off
        self.assertEqual((st["r"], len(st["s"])), (50.0, 1))

    def test_samples_are_bounded(self):
        row = self.fresh_row()
        self.readings(row, *[(i * 5, i) for i in range(30)])
        self.assertLessEqual(len(row["pace"]["seven_day"]["s"]), cp.SAMPLES)

    def test_each_weekly_window_learns_its_own_r(self):
        row = {**self.fresh_row(), "weeks": {"seven_day_opus": [0, NOW + 90 * H]}}
        self.readings(row, (10, 10))
        row["weeks"]["seven_day_opus"][0] = 10
        self.readings(row, (30, 12))
        self.assertEqual(row["pace"]["seven_day"]["r"], 10.0)
        self.assertEqual(row["pace"]["seven_day_opus"]["r"], 50.0)

    def test_accounts_learn_apart(self):
        a, b = self.fresh_row(), self.fresh_row()
        self.readings(a, (0, 0), (20, 2))
        self.readings(b, (0, 0), (20, 8))
        self.assertEqual((a["pace"]["seven_day"]["r"], b["pace"]["seven_day"]["r"]), (10.0, 40.0))

    def test_no_weekly_limit_forgets_what_was_learned(self):
        row = self.fresh_row()
        self.readings(row, (0, 0), (20, 2))
        row["weekly"] = "none"
        cp.learn(row, NOW)
        self.assertNotIn("pace", row)

    # --- saving usage reports ---------------------------------------------------------------------------------

    def recorded(self, windows, old=None):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        os.environ["CLAUDE_POOL_STATE"] = os.path.join(tmp, "usage.json")
        if old:
            with open(os.environ["CLAUDE_POOL_STATE"], "w") as f:
                json.dump({"a": old}, f)
        cp.record("a", {"status": "allowed", "unifiedWindows": windows}, ran=False)
        return cp.load_usage()["a"]

    def test_report_without_a_weekly_window_means_no_weekly_limit(self):
        row = self.recorded({"five_hour": {"utilization": 0.14, "resetsAt": time.time() + H}})
        self.assertEqual((row["weekly"], row["seven_day"]), ("none", None))
        self.assertEqual(cp.pace(row, time.time())[0], "none")

    def test_report_without_a_weekly_window_drops_old_weekly_numbers(self):
        old = {"seven_day": 40, "seven_day_resets_ts": time.time() + 50 * H}
        row = self.recorded({"five_hour": {"utilization": 0.14, "resetsAt": time.time() + H}}, old)
        self.assertEqual((row["weekly"], row["seven_day"]), ("none", None))

    def test_weekly_window_back_clears_no_limit(self):
        w = {"five_hour": {"utilization": 0.1, "resetsAt": time.time() + H},
             "seven_day": {"utilization": 0.3, "resetsAt": time.time() + 50 * H},
             "seven_day_opus": {"utilization": 0.5, "resetsAt": time.time() + 50 * H}}
        row = self.recorded(w, {"weekly": "none"})
        self.assertNotIn("weekly", row)
        self.assertEqual(row["weeks"]["seven_day_opus"][0], 50.0)
        self.assertEqual(len(cp.weeks(row)), 2)

    # --- status and capacity ----------------------------------------------------------------------------------

    def test_status_shows_pace_and_capacity(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        for n in ("pro", "team", "new", "learning"):
            with open(os.path.join(tmp, n + ".token"), "w") as f:
                f.write("tok\n")
            os.chmod(os.path.join(tmp, n + ".token"), 0o600)
        now = time.time()
        rows = {"pro": {"seven_day": 40, "seven_day_resets_ts": now + 80 * H, "checked_ts": now, "pace": {"seven_day": {"r": 20, "n": 120}}},
                "team": {"weekly": "none", "five_hour": 10, "resets_ts": now + H, "checked_ts": now},
                "learning": {"seven_day": 10, "seven_day_resets_ts": now + 80 * H, "checked_ts": now}}
        with open(os.path.join(tmp, "usage.json"), "w") as f:
            json.dump(rows, f)
        os.environ.update(CLAUDE_POOL_DIR=tmp, CLAUDE_POOL_STATE=os.path.join(tmp, "usage.json"), CLAUDE_POOL_ORCA="off")
        out = io.StringIO()
        with redirect_stdout(out):
            cp.status()
        text = out.getvalue()
        self.assertIn("pace", text.splitlines()[0])
        self.assertIn("no limit", text)
        r = cp.ratio(rows["pro"])[0]  # learned 20, plus the margin
        self.assertIn(f"{r:.0f} high", [l for l in text.splitlines() if " pro " in l][0])
        self.assertIn(f"1 account with an estimate: {100 / r:.1f} full 5-hour windows a week, {60 / r:.1f} left", text)
        self.assertIn("2 with no weekly limit", text)  # team, and new with no numbers at all
        self.assertIn("1 not estimated yet", text)

    def test_capacity_is_marked_hypothetical(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cp.capacity(20)
        text = out.getvalue()
        self.assertTrue(text.startswith("Hypothetical"))
        self.assertIn("5.0 full 5-hour windows a week, 15% of non-stop use", text)
        self.assertIn("1         5.0           0.15", text)
        self.assertIn("100       500.0", text)


class SimulationTest(unittest.TestCase):
    """The same workloads under each strategy: pace never does less work, and does more where drain spends a
    week too early. Where the work is light or more than the weeks hold, no strategy can do better."""

    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_POOL")}, clear=True):
            cls.results = {name: {r["strategy"]: r for r in rs} for name, rs in simulate.compare().items()}

    def test_pace_never_does_less_than_drain(self):
        for name, r in self.results.items():
            self.assertGreaterEqual(r["pace"]["done"], r["drain"]["done"], name)

    def test_pace_does_more_where_drain_runs_a_week_dry(self):
        for name in ("small plan resets first", "two plans, office hours", "mixed, no weekly limit"):
            r = self.results[name]
            self.assertGreater(r["pace"]["done"], r["drain"]["done"], name)
            self.assertLess(r["pace"]["week-out hours"], r["drain"]["week-out hours"], name)

    def test_no_gain_when_there_is_nothing_to_gain(self):
        for name in ("light", "steady, over capacity", "same plans, staggered resets"):
            r = self.results[name]
            self.assertEqual(r["pace"]["done"], r["drain"]["done"], name)

    def test_the_pick_is_fast(self):
        times = simulate.bench(sizes=(100,), calls=200)
        self.assertLess(times[100], 20_000)  # microseconds; loose for slow CI machines, see simulate.py for real numbers


if __name__ == "__main__":
    unittest.main()
