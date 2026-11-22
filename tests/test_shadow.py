"""Shadow test (.github/automation/shadow.py): the two measurement rules.

1. Clock times sort as clock times ("9:50 AM" before "10:00 AM"), not as text.
2. The ORB list exists at 10:15. A setup that triggered earlier is bought at the
   open of the 10:15 bar (same stop, 2R from the new entry), not at its old
   trigger price; if the price is already at/below the stop then, it is not bought.

SYNTHETIC five-minute bars (not market data), 2026-10-19, built from the TSLA fixture's meta.
"""

import contextlib
import copy
import csv
import io
import json
import os
import shutil
import tempfile
import unittest

from _load import load, FIX

shadow = load(os.path.join(".github", "automation"), "shadow")

TSLA = os.path.join(FIX, "trader-entry", "TSLA-2026-10-19-5m.json")
OPEN_TS = 1792416600       # 2026-10-19 09:30 EDT
DAY = "2026-10-19"


def make_chart(bars, symbol="TSLA"):
    """bars: 78 (o, h, l, c) tuples from 09:30; Yahoo chart JSON v8."""
    with open(TSLA, encoding="utf-8") as fh:
        doc = json.load(fh)
    res = doc["chart"]["result"][0]
    meta = copy.deepcopy(res["meta"])
    meta["regularMarketTime"] = OPEN_TS + 78 * 300
    meta["symbol"] = symbol
    ts = [OPEN_TS + 300 * i for i in range(len(bars))]
    quote = {"open": [b[0] for b in bars], "high": [b[1] for b in bars],
             "low": [b[2] for b in bars], "close": [b[3] for b in bars], "volume": [1000000] * len(bars)}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts, "indicators": {"quote": [quote]}}], "error": None}}


def flat_bar(p):
    return (p, p + 0.05, p - 0.05, p)


def day_bars(or_bars, rest):
    """or_bars: the three 9:30-9:45 bars; rest: dict {bar index: (o,h,l,c)} over a flat 100.0 backdrop."""
    bars = list(or_bars)
    for i in range(3, 78):
        bars.append(rest.get(i, flat_bar(100.0)))
    return bars


# opening range 9:30-9:45: high 100.0, low 98.0 -> or-mid stop 99.0
OR = [(99.0, 100.0, 98.0, 99.5), (99.5, 100.0, 98.5, 99.4), (99.4, 99.9, 98.6, 99.2)]


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shadow-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.charts = os.path.join(self.tmp, "charts")
        os.makedirs(self.charts)
        os.makedirs(os.path.join(self.tmp, "data", "trader"))

    def run_day(self, charts):
        for sym, bars in charts.items():
            with open(os.path.join(self.charts, sym + ".json"), "w", encoding="utf-8") as fh:
                json.dump(make_chart(bars, sym), fh)
        wl = {"mode": "orb", "as_of": DAY + " 10:15", "hits": [{"symbol": s} for s in charts]}
        with open(os.path.join(self.tmp, "data", "trader", "watchlist.json"), "w", encoding="utf-8") as fh:
            json.dump(wl, fh)
        with contextlib.redirect_stdout(io.StringIO()):
            code = shadow.main(["--data", os.path.join(self.tmp, "data"), "--offline", self.charts, "--date", DAY])
        self.assertEqual(code, 0)
        with open(os.path.join(self.tmp, "data", "shadow", "trades.csv"), encoding="utf-8", newline="") as fh:
            return {r["symbol"]: r for r in csv.DictReader(fh)}

    def test_clock_minutes_sorts_as_time(self):
        self.assertLess(shadow.clock_minutes("9:50 AM"), shadow.clock_minutes("10:00 AM"))
        self.assertLess(shadow.clock_minutes("10:15 AM"), shadow.clock_minutes("1:05 PM"))
        self.assertEqual(shadow.clock_minutes("15:55"), 15 * 60 + 55)
        self.assertGreater(shadow.clock_minutes(""), shadow.clock_minutes("3:55 PM"))

    def test_early_trigger_is_bought_at_the_1015_open(self):
        # bar index 4 = 9:50 bar: closes 100.6 above the range high -> trigger "9:50 AM"
        # by 10:15 (index 9) the price is 101.5; the target 101.5 + 2 * 2.5 = 106.5 is hit later
        early = day_bars(OR, {3: (99.4, 100.5, 99.3, 100.4), 4: (100.4, 100.7, 100.3, 100.6),
                              9: (101.5, 101.6, 101.4, 101.5), 20: (101.5, 107.0, 101.4, 106.8)})
        rows = self.run_day({"EARLY": early})
        r = rows["EARLY"]
        self.assertEqual(r["status"], "traded")
        self.assertEqual(r["trigger_time"], "9:50 AM")
        self.assertEqual(r["entry_time"], "10:15")
        self.assertAlmostEqual(float(r["entry"]), 101.5)
        self.assertAlmostEqual(float(r["stop"]), 99.0)
        self.assertAlmostEqual(float(r["target"]), 106.5)
        self.assertTrue(r["outcome"].startswith("target"))
        self.assertAlmostEqual(float(r["r_gross"]), 2.0, places=2)
        self.assertIn("late entry", r["note"])

    def test_early_trigger_already_below_stop_is_not_bought(self):
        dead = day_bars(OR, {3: (99.4, 100.5, 99.3, 100.4), 4: (100.4, 100.7, 100.3, 100.6),
                             5: (100.6, 100.6, 98.0, 98.2), 6: flat_bar(98.2), 7: flat_bar(98.2),
                             8: flat_bar(98.2), 9: (98.5, 98.6, 98.4, 98.5)})
        for i in range(10, 78):
            dead[i] = flat_bar(98.5)
        rows = self.run_day({"DEAD": dead})
        self.assertEqual(rows["DEAD"]["status"], "stale")
        self.assertEqual(rows["DEAD"]["r_net"], "")

    def test_early_trigger_opening_a_hair_above_the_stop_is_no_risk(self):
        # 19 Nov DLR: the 10:15 open was a hair above the stop (float noise), risk ~0,
        # and the 0.1% cost divided by ~0 risk gave -24518R. Risk below the round-trip cost = "no risk".
        edge = day_bars(OR, {3: (99.4, 100.5, 99.3, 100.4), 4: (100.4, 100.7, 100.3, 100.6),
                             9: (99.000001, 99.1, 98.9, 99.0)})
        for i in range(10, 78):
            edge[i] = flat_bar(99.0)
        rows = self.run_day({"EDGE": edge})
        self.assertEqual(rows["EDGE"]["status"], "no risk")
        self.assertEqual(rows["EDGE"]["r_net"], "")

    def test_normal_trade_with_risk_below_round_trip_cost_is_no_risk(self):
        # narrow opening range 99.9-100.0 -> stop 99.95; trigger close 100.03 after the list:
        # risk 0.08 is below the minimum 2 * 0.0005 * 100.03 = 0.10
        narrow = [(99.95, 100.0, 99.9, 99.95)] * 3
        bars = day_bars(narrow, {12: (100.0, 100.05, 99.98, 100.03)})
        rows = self.run_day({"TINY": bars})
        r = rows["TINY"]
        self.assertEqual(r["status"], "no risk")
        self.assertEqual(r["r_net"], "")
        self.assertEqual(r["exit_price"], "")
        self.assertIn("below the minimum", r["note"])

    def test_reclassify_old_rows(self):
        bad = {"status": "traded", "entry": "1096.6400", "stop": "1096.5100", "risk": "0.1300",
               "exit_time": "3:55 PM", "exit_price": "1095.0", "r_gross": "-12.0", "r_net": "-9.434",
               "pct_net": "-0.2", "note": ""}
        good = {"status": "traded", "entry": "100.0000", "stop": "99.0000", "risk": "1.0000",
                "exit_time": "3:55 PM", "exit_price": "101.0", "r_gross": "1.000", "r_net": "0.900",
                "pct_net": "0.9", "note": ""}
        broken = dict(good, risk="n/a")
        shadow.reclassify([bad, good, broken], 0.0005)
        self.assertEqual(bad["status"], "no risk")
        for k in ("exit_time", "exit_price", "r_gross", "r_net", "pct_net"):
            self.assertEqual(bad[k], "")
        self.assertIn("below the minimum", bad["note"])
        self.assertEqual(good["status"], "traded")
        self.assertEqual(good["r_net"], "0.900")
        self.assertEqual(broken["status"], "traded")

    def test_real_fixture_tsla_late_entry_row(self):
        # replaces the brittle grep in the workflow: parse the CSV instead of matching a text line
        import gzip
        charts = os.path.join(self.tmp, "c60")
        os.makedirs(charts)
        src = os.path.join(FIX, "trader-timemachine", "TSLA-60d5m.json.gz")
        with gzip.open(src) as fi, open(os.path.join(charts, "TSLA.json"), "wb") as fo:
            shutil.copyfileobj(fi, fo)
        wl = os.path.join(self.tmp, "wl.json")
        with open(wl, "w", encoding="utf-8") as fh:
            json.dump({"mode": "orb", "as_of": "2026-10-09 10:15 ET", "hits": [{"symbol": "TSLA"}]}, fh)
        data = os.path.join(self.tmp, "sd")
        with contextlib.redirect_stdout(io.StringIO()):
            code = shadow.main(["--data", data, "--date", "2026-10-09", "--offline", charts, "--watchlist", wl])
        self.assertEqual(code, 0)
        with open(os.path.join(data, "shadow", "trades.csv"), encoding="utf-8", newline="") as fh:
            r = {x["symbol"]: x for x in csv.DictReader(fh)}["TSLA"]
        self.assertEqual(r["status"], "traded")
        self.assertEqual(r["trigger_time"], "9:50 AM")
        self.assertEqual(r["entry_time"], "10:15")
        self.assertEqual(r["entry"], "384.7750")
        self.assertEqual(r["stop"], "384.2500")
        self.assertEqual(r["outcome"], "target")

    def test_trigger_after_the_list_keeps_its_own_price(self):
        # drift flat, then a close above 100.0 at the 10:30 bar (index 12)
        late = day_bars(OR, {12: (100.0, 100.7, 99.9, 100.6)})
        rows = self.run_day({"LATE": late})
        r = rows["LATE"]
        self.assertEqual(r["status"], "traded")
        self.assertEqual(r["trigger_time"], "10:35 AM")
        self.assertEqual(r["entry_time"], "10:35")
        self.assertAlmostEqual(float(r["entry"]), 100.6)
        self.assertNotIn("late entry", r["note"])

    def test_first_three_uses_real_time_order(self):
        rows = []
        for sym, t in (("AAA", "10:00 AM"), ("BBB", "9:50 AM"), ("CCC", "9:55 AM"),
                       ("DDD", "10:05 AM"), ("EEE", "9:50 AM")):
            rows.append({"date": DAY, "symbol": sym, "status": "traded", "trigger_time": t, "r_net": "1.0"})
        picked = shadow.first_three(rows)
        self.assertEqual([r["symbol"] for r in picked], ["BBB", "EEE", "CCC"])
        # and the summary still builds from such rows
        shadow.write_summary(os.path.join(self.tmp, "summary.md"), rows, 0.0005)


if __name__ == "__main__":
    unittest.main()
