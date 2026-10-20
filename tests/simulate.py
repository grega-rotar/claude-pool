"""A deterministic workload simulation of the pick strategies, and a timing of the pick.

    python3 tests/simulate.py

Every hour some jobs want 5-hour % of work. Each job goes to the account claude_pool.choose() picks, and moves on
to the next pick when that account hits a limit midway, as a cut-off print run does. The accounts' usage follows
the model below, so the numbers are the strategy's, not Anthropic's:

- a 5-hour window starts at an account's first use and ends 5 hours later; it holds 100%
- a weekly window resets at a fixed time each week and holds 100%; one full 5-hour window uses r% of it
- usage is seen at once (no checks, no other apps)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_pool  # noqa: E402

HOUR = 3600
START = 1_800_000_000  # a fixed clock, so every run is the same


def simulate(strategy, accounts, demand, days=14):
    """accounts: [(name, r or None for no weekly limit, hours until its first weekly reset)].
    demand(hour) -> [5-hour % each job wants this hour]. Returns the measures as a dict."""
    os.environ["CLAUDE_POOL_STRATEGY"] = strategy
    pool = {n: ("token", "") for n, _, _ in accounts}
    rows = {n: {"checked_ts": START, "five_hour": 0, "resets_ts": 0} for n, _, _ in accounts}
    week_reset = {}
    for n, r, first in accounts:
        if r is None:
            rows[n]["weekly"] = "none"
        else:
            week_reset[n] = START + first * HOUR
            rows[n].update(seven_day=0, seven_day_resets_ts=week_reset[n], pace={"seven_day": {"r": r, "n": 200}})
    asked = done = switches = week_out_hours = unused_at_reset = 0
    open_hours = demand_hours = 0
    last = None
    me = {"pid": os.getpid(), "host": claude_pool.HOST, "ts": START}
    for h in range(days * 24):
        now = START + h * HOUR
        for n, r, _ in accounts:
            row = rows[n]
            row["checked_ts"], row["running"] = now, []
            if row["resets_ts"] and row["resets_ts"] <= now:
                row.update(five_hour=0, resets_ts=0, status="allowed")
            if r is not None and week_reset[n] <= now:
                unused_at_reset += 100 - row["seven_day"]
                week_reset[n] += 168 * HOUR
                row.update(seven_day=0, seven_day_resets_ts=week_reset[n])
            if r is not None and row["seven_day"] >= 100:
                week_out_hours += 1
        jobs = demand(h)
        if jobs:
            demand_hours += 1
            open_hours += sum(1 for n in pool if not claude_pool.at_limit(rows[n], now))
        for j, want in enumerate(jobs):
            asked += want
            tried = set()
            while want > 1e-9:
                n = claude_pool.choose(pool, rows, skip=tried, now=now)
                if n is None:
                    break
                if j == 0 and tried == set():
                    switches += last is not None and n != last
                    last = n
                row, r = rows[n], dict((a, b) for a, b, _ in accounts)[n]
                if not row["resets_ts"]:
                    row["resets_ts"] = now + 5 * HOUR
                room = 100 - row["five_hour"]
                if r is not None:
                    room = min(room, (100 - row["seven_day"]) * 100 / r)
                used = max(0.0, min(want, room))
                row["five_hour"] += used
                if r is not None:
                    row["seven_day"] = min(100.0, row["seven_day"] + used * r / 100)
                if row["five_hour"] >= 100 - 1e-9:
                    row.update(five_hour=100.0, status="rejected")
                row["running"] = row["running"] + [me]
                want -= used
                done += used
                tried.add(n)
    return {"strategy": strategy, "asked": round(asked), "done": round(done), "done %": round(100 * done / asked, 1) if asked else 100.0,
            "week-out hours": week_out_hours, "open accounts": round(open_hours / demand_hours, 2) if demand_hours else 0,
            "switches": switches, "unused week % at resets": round(unused_at_reset)}


def office_hours(jobs, each):
    """`jobs` parallel jobs of `each` 5-hour % an hour, weekdays 9:00-17:00, nothing else."""
    return lambda h: [each] * jobs if (h // 24) % 7 < 5 and 9 <= h % 24 < 17 else []


def steady(jobs, each):
    return lambda h: [each] * jobs


SCENARIOS = {
    # A small plan whose week resets first: drain spends it in the first days, and the office-hours peaks after
    # that have one account fewer.
    "small plan resets first": ([("a", 20, 6 * 24), ("b", 10, 7 * 24), ("c", 10, 8 * 24)], office_hours(3, 20)),
    "two plans, office hours": ([("a", 30, 3 * 24), ("b", 12, 160)], office_hours(2, 15)),
    # One Team account with no weekly limit next to two weekly-limited ones.
    "mixed, no weekly limit": ([("team", None, 0), ("a", 15, 2 * 24), ("b", 15, 5 * 24)], office_hours(3, 20)),
    # Same plans: the weeks hold the work under any order, so pace has nothing to improve.
    "same plans, staggered resets": ([("a", 10, 4 * 24), ("b", 10, 5 * 24), ("c", 10, 6 * 24)], office_hours(3, 20)),
    # More work than the weeks hold: no strategy can do all of it.
    "steady, over capacity": ([("a", 20, 2 * 24), ("b", 20, 4 * 24), ("c", 20, 6 * 24)], steady(2, 20)),
    # Light work: every strategy does all of it.
    "light": ([("a", 20, 1 * 24), ("b", 20, 3 * 24)], office_hours(1, 5)),
}


def compare():
    out = {}
    for name, (accounts, demand) in SCENARIOS.items():
        out[name] = [simulate(s, accounts, demand) for s in ("drain", "balance", "pace")]
    return out


def bench(sizes=(1, 2, 5, 10, 100), calls=2000):
    """Microseconds per choose() call on in-memory rows: mixed plans, resets, learned r, stale and unknown rows."""
    os.environ.pop("CLAUDE_POOL_STRATEGY", None)
    now, out = time.time(), {}
    for size in sizes:
        pool, rows = {}, {}
        for i in range(size):
            n = f"acct{i:03d}"
            pool[n] = ("token", "")
            kind = i % 4
            row = {"five_hour": (i * 7) % 95, "resets_ts": now + (i % 5) * 3600, "checked_ts": now - (i % 3) * 3000}
            if kind == 0:
                row["weekly"] = "none"
            elif kind != 1:  # 1: weekly unknown
                row.update(seven_day=(i * 13) % 90, seven_day_resets_ts=now + (i % 7) * 86400 + 3600,
                           pace={"seven_day": {"r": 10 + i % 20, "n": 40 + i}} if kind == 2 else {})
            rows[n] = row
        claude_pool.choose(pool, rows, now=now)
        t = time.perf_counter()
        for _ in range(calls):
            claude_pool.choose(pool, rows, now=now)
        out[size] = (time.perf_counter() - t) / calls * 1e6
    return out


if __name__ == "__main__":
    for name, results in compare().items():
        print(f"\n{name}")
        keys = list(results[0])
        print("  ".join(f"{k:>12}" for k in keys))
        for r in results:
            print("  ".join(f"{str(r[k]):>12}" for k in keys))
    print("\nchoose() in memory, microseconds per call:")
    for size, us in bench().items():
        print(f"  {size:>3} accounts: {us:8.1f}")
