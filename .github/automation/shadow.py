#!/usr/bin/env python3
"""Shadow test (gölge takip): what would today's ORB hits have done?

Runs after the close. No broker, no orders, no money. For every hit in the
10:15 ORB watchlist it uses the ORIGINAL tools, unchanged:

  trader-entry  check SYMBOL --now "<date> 15:55"   -> did the setup trigger, and where
  trader-exit   plan --from <entry.json>            -> the stop and target (defaults: OR midpoint, 2R)

then walks the day's real 5-minute bars after the entry bar:
  - the stop is hit first   -> loss (a gap through the stop fills at the bar's open)
  - the target is hit first -> win  (a gap through the target fills at the bar's open)
  - both inside one bar     -> counted as the STOP (pessimistic: 5-minute bars hide the order)
  - neither by flat_by      -> closed at the 15:50 bar's close (15:55)
A cost per side (default 0.05 %) is taken off every trade for slippage/spread.

    python .github/automation/shadow.py --data data                  # today (New York)
    python .github/automation/shadow.py --data data --date 2026-11-10
    python .github/automation/shadow.py --data data --offline DIR --watchlist FILE --date D   # tests

Writes data/shadow/trades.csv (one row per hit, re-running a day replaces that
day) and data/shadow/summary.md.
"""

import argparse
import csv
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIELDS = ["date", "symbol", "status", "trigger_time", "entry", "stop", "target", "risk",
          "exit_time", "exit_price", "outcome", "r_gross", "r_net", "pct_net", "note"]


def load_entry_module():
    spec = importlib.util.spec_from_file_location("trader_entry", os.path.join(ROOT, "trader-entry", "entry.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_json(args):
    p = subprocess.run([sys.executable] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    out = p.stdout.decode("utf-8", "replace")
    try:
        return p.returncode, json.loads(out), p.stderr.decode("utf-8", "replace")
    except ValueError:
        return p.returncode, None, (p.stderr.decode("utf-8", "replace") or out).strip()


def hhmm(entry, ts):
    return entry.to_eastern(entry.utc_of(ts)).strftime("%H:%M")


def walk(bars, plan, day, entry_mod):
    """-> (exit_time, exit_price, outcome)"""
    stop, target = plan["stop"], plan["target"]
    flat = plan.get("flat_by") or "15:55"
    trig_start = plan["_bar_start"]
    last = None
    for b in bars:
        t = entry_mod.to_eastern(entry_mod.utc_of(b["t"]))
        if t.date() != day:
            continue
        start = t.strftime("%H:%M")
        if start <= trig_start:
            continue
        end = (t + datetime.timedelta(minutes=5)).strftime("%H:%M")
        if end > flat:
            break
        if b["o"] <= stop:
            return start, b["o"], "stop (gap)"
        if b["o"] >= target:
            return start, b["o"], "target (gap)"
        hit_stop, hit_target = b["l"] <= stop, b["h"] >= target
        if hit_stop:
            return start, stop, "stop" + (" (same bar as target)" if hit_target else "")
        if hit_target:
            return start, target, "target"
        last = (end, b["c"])
    if last is None:
        return None, None, "no bars after entry"
    flat_bar = (datetime.datetime.strptime(flat, "%H:%M") - datetime.timedelta(minutes=5)).strftime("%H:%M")
    if last[0] < flat_bar:
        return None, None, "data ends at %s, before %s: not counted" % (last[0], flat)
    return last[0], last[1], "flat"


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def stats(rows):
    rs = [fnum(r["r_net"]) for r in rows if fnum(r["r_net"]) is not None]
    if not rs:
        return None
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": len(rs), "win_rate": 100.0 * len(wins) / len(rs), "avg": sum(rs) / len(rs),
            "total": sum(rs), "avg_win": sum(wins) / len(wins) if wins else 0.0,
            "avg_loss": sum(losses) / len(losses) if losses else 0.0, "max_dd": dd}


def write_summary(path, rows, cost):
    traded = [r for r in rows if r["status"] == "traded"]
    days = sorted({r["date"] for r in rows})
    first3 = []
    for d in days:
        day_rows = sorted((r for r in traded if r["date"] == d), key=lambda r: r["trigger_time"])
        first3 += day_rows[:3]
    lines = ["# Gölge takip özeti (sanal, para yok)", "",
             "Kural: orijinal ORB girişi, orijinal plan (stop = açılış aralığı ortası, hedef = 2R), "
             "15:55'te kapanış. Aynı mumda stop ve hedef = stop sayılır. Taraf başı maliyet %%%.2f." % (cost * 100), "",
             "Gün sayısı: %d. Toplam tarama sonucu: %d, işleme dönüşen: %d." % (len(days), len(rows), len(traded)), ""]

    def block(title, rs):
        s = stats(rs)
        lines.append("## " + title)
        lines.append("")
        if not s:
            lines.append("Henüz işlem yok.")
        else:
            lines.extend(["| İşlem | Kazanma oranı | Ortalama R | Toplam R | Ort. kazanç R | Ort. kayıp R | En kötü düşüş R |",
                      "| --- | --- | --- | --- | --- | --- | --- |",
                      "| %d | %%%.0f | %+.2f | %+.1f | %+.2f | %+.2f | %.1f |" % (
                          s["n"], s["win_rate"], s["avg"], s["total"], s["avg_win"], s["avg_loss"], s["max_dd"])])
        lines.append("")

    block("Tüm sinyaller", traded)
    block("Her günün ilk 3 sinyali (gerçekçi senaryo)", first3)
    lines += ["## Günlük", "", "| Gün | İşlem | Toplam R |", "| --- | --- | --- |"]
    for d in days[-30:]:
        s = stats([r for r in traded if r["date"] == d])
        lines.append("| %s | %d | %s |" % (d, s["n"] if s else 0, "%+.1f" % s["total"] if s else "-"))
    lines += ["", "1R = giriş ile stop arasındaki fark. +2R = riske edilenin iki katı kazanç.",
              "Bu bir geçmiş kaydıdır, tahmin değildir."]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--date", help="YYYY-MM-DD New York (default today)")
    ap.add_argument("--watchlist", help="default <data>/trader/watchlist.json")
    ap.add_argument("--offline", help="folder of saved Yahoo 5m charts <TICKER>.json (tests)")
    ap.add_argument("--cost", type=float, default=0.0005, help="cost per side as a fraction (default 0.0005)")
    a = ap.parse_args(argv)

    em = load_entry_module()
    data = os.path.abspath(a.data)
    today = em.to_eastern(em.utc_now()).date()
    day = datetime.date.fromisoformat(a.date) if a.date else today
    out_dir = os.path.join(data, "shadow")
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "trades.csv")

    stale = os.path.join(out_dir, "today.md")
    if os.path.exists(stale):
        os.remove(stale)  # never re-post an old day's report
    is_open, why = em.trading_day(day)
    if not is_open:
        print("%s is not a trading day (%s): nothing to test." % (day, why))
        return 0

    wl_path = a.watchlist or os.path.join(data, "trader", "watchlist.json")
    try:
        with open(wl_path, encoding="utf-8") as fh:
            wl = json.load(fh)
    except (OSError, ValueError) as e:
        print("::error::no readable watchlist at %s: %s" % (wl_path, e))
        return 1
    if wl.get("mode") != "orb" or not str(wl.get("as_of", "")).startswith(day.isoformat()):
        print("::error::the watchlist is not today's ORB scan (mode=%s, as_of=%s): did orb.yml run?"
              % (wl.get("mode"), wl.get("as_of")))
        return 1
    symbols = []
    for h in wl.get("hits", []):
        s = str(h.get("symbol") or "").upper()
        if s and s not in symbols:
            symbols.append(s)

    now = "%s 15:55" % day.isoformat()
    tmp = tempfile.mkdtemp(prefix="shadow-")
    charts = a.offline or os.path.join(tmp, "charts")
    os.makedirs(charts, exist_ok=True)
    new_rows = []
    for sym in symbols:
        row = {k: "" for k in FIELDS}
        row.update(date=day.isoformat(), symbol=sym)
        try:
            if not a.offline:
                text = em.yahoo_get(em.chart_path(sym, em.chart_range(day, today, "5m"), "5m"))
                with open(os.path.join(charts, em.yahoo_symbol(sym) + ".json"), "w", encoding="utf-8") as fh:
                    fh.write(text)
                time.sleep(0.4)
            chart = em.get_chart(sym, "5m", day=day, offline=charts, today=today)
        except Exception as e:  # one bad ticker must not stop the rest
            row.update(status="error", note="chart: %s" % e)
            new_rows.append(row)
            continue
        ej = os.path.join(tmp, sym + "-entry.json")
        code, res, err = run_json([os.path.join(ROOT, "trader-entry", "entry.py"), "check", sym,
                                   "--now", now, "--offline", charts, "--json", "--write", ej])
        if not res or res.get("status") != "triggered":
            row.update(status="no trigger", note=(res or {}).get("reason") or err[-200:])
            new_rows.append(row)
            continue
        pj = os.path.join(tmp, sym + "-plan.json")
        code, plan, err = run_json([os.path.join(ROOT, "trader-exit", "exit.py"), "plan", "--from", ej,
                                    "--now", now, "--json", "--write", pj])
        if not plan or plan.get("stop") is None or plan.get("target") is None:
            row.update(status="no plan", note=err[-200:])
            new_rows.append(row)
            continue
        plan["_bar_start"] = res["trigger"]["bar_start"]
        t, px, outcome = walk(chart["bars"], plan, day, em)
        entry, stop = float(plan["entry"]), float(plan["stop"])
        risk = entry - stop
        row.update(status="traded" if px is not None else "no exit", trigger_time=res["trigger"]["time"],
                   entry="%.4f" % entry, stop="%.4f" % stop, target="%.4f" % float(plan["target"]),
                   risk="%.4f" % risk, outcome=outcome, note="; ".join(plan.get("warnings") or []))
        if px is not None and risk > 0:
            gross = px - entry
            net = gross - a.cost * (entry + px)
            row.update(exit_time=t, exit_price="%.4f" % px, r_gross="%.3f" % (gross / risk),
                       r_net="%.3f" % (net / risk), pct_net="%.3f" % (100.0 * net / entry))
        new_rows.append(row)

    rows = [r for r in read_rows(csv_path) if r.get("date") != day.isoformat()] + new_rows
    rows.sort(key=lambda r: (r["date"], r["trigger_time"], r["symbol"]))
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    summary = write_summary(os.path.join(out_dir, "summary.md"), rows, a.cost)

    traded = [r for r in new_rows if r["status"] == "traded"]
    s = stats(traded)
    head = "%s: %d hits, %d traded%s" % (day, len(new_rows), len(traded),
                                        ", total %+.1fR net" % s["total"] if s else "")
    print(head)
    for r in new_rows:
        print("  %-6s %-10s %s %s %s" % (r["symbol"], r["status"], r["trigger_time"], r["outcome"], r["r_net"]))
    # the daily report posted to the GitHub issue (shadow.yml)
    ts = stats(traded)
    day_first3 = sorted(traded, key=lambda r: r["trigger_time"])[:3]
    f3 = stats(day_first3)
    allt = stats([r for r in rows if r["status"] == "traded"])
    n_days = len({r["date"] for r in rows})
    rep = ["## %s gölge takip (sanal, para yok)" % day.isoformat(), "",
           "Tarama sonucu: %d hisse, işleme dönüşen: %d." % (len(new_rows), len(traded)), ""]
    if ts:
        wins = sum(1 for r in traded if fnum(r["r_net"]) > 0)
        rep += ["- **Bugün, tüm sinyaller:** %d işlem, %d kazanç / %d kayıp, toplam **%+.1fR**" % (
                    ts["n"], wins, ts["n"] - wins, ts["total"]),
                "- **Bugün, ilk 3 sinyal:** toplam **%+.1fR** (%s)" % (
                    f3["total"], ", ".join("%s %s" % (r["symbol"], r["r_net"]) for r in day_first3))]
    else:
        rep.append("- Bugün işlem yok.")
    if allt:
        rep += ["- **Başlangıçtan beri (%d gün):** %d işlem, kazanma %%%.0f, ortalama %+.2fR, toplam %+.1fR, en kötü düşüş %.1fR" % (
                    n_days, allt["n"], allt["win_rate"], allt["avg"], allt["total"], allt["max_dd"])]
    rep += ["", "Ayrıntı: `results` dalı, `shadow/summary.md` ve `shadow/trades.csv`.",
            "1R = giriş ile stop arasındaki fark. Tek gün bir şey kanıtlamaz."]
    with open(os.path.join(out_dir, "today.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("## Bugün: %s\n\n" % head + "\n".join(summary) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
