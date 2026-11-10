#!/usr/bin/env python3
"""Run original tools one after another, as the README's flow does
("each skill reads what the last one wrote"). Plumbing only.

    python .github/automation/run_chain.py --data data \
        "trader-scanner|daily|okwrite=trader/watchlist.json" \
        "trader-news||needs=trader/watchlist.json|ok=0,1" \
        "trader-analyst|@watchlist|needs=trader/watchlist.json"

Each item is  skill|args[|option...]
  args            the tool's own arguments, unchanged. "@watchlist" becomes the
                  symbols in trader/watchlist.json, in file order, all of them
                  (trader-analyst SKILL.md: "or the tickers on trader/watchlist.json").
  needs=FILE      run only if an earlier step in this chain wrote FILE, so a
                  later tool never reads an old file after a failed step. A
                  skipped step is reported as a failure, not hidden.
  ok=CODES        exit codes that the tool's own docs call a normal outcome
                  (news and pelosi: 1 = nothing new). Default: 0 only.
  sec=1           the tool needs SEC_USER_AGENT; without the secret the step
                  waits with a clear warning and the rest of the chain runs.
  okwrite=FILE    a non-zero exit still counts as normal if the step wrote FILE
                  (the scanner: 1 = it ran and found nothing, and it still
                  writes its watchlist; 1 without the file = it could not run).
Every step runs through run_skill.py (same records, same exit codes). The chain
exits 1 if any step failed or was skipped, else 0.
"""

import argparse
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_skill  # noqa: E402


def watchlist_symbols(data):
    path = os.path.join(data, "trader", "watchlist.json")
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    return [str(h.get("symbol")).upper() for h in doc.get("hits", []) if h.get("symbol")]


def summary(text):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--trading-day-only", action="store_true",
                    help="do nothing on weekends and NYSE holidays (the scanner's own calendar)")
    ap.add_argument("items", nargs="+")
    a = ap.parse_args(argv)
    data = os.path.abspath(a.data)
    trader = os.path.join(data, "trader")
    os.makedirs(trader, exist_ok=True)
    written, results = set(), []
    if a.trading_day_only:
        # the scanner's own NYSE calendar (holidays, weekends), read from the original tool
        import importlib.util
        spec = importlib.util.spec_from_file_location("trader_scan", os.path.join(run_skill.ROOT, "trader-scanner", "scan.py"))
        scan = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scan)
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        day = scan.to_eastern(now).date()
        is_open, why = scan.trading_day(day)
        if not is_open:
            msg = "%s is not an NYSE trading day (%s): nothing ran." % (day.isoformat(), why)
            print("::notice title=market closed::" + msg)
            summary("## Market closed\n\n" + msg + "\n")
            return 0
    for item in a.items:
        parts = item.split("|")
        skill, args = parts[0], (parts[1] if len(parts) > 1 else "")
        opts = {}
        for p in parts[2:]:
            k, _, v = p.partition("=")
            opts[k] = v
        ok_codes = {int(x) for x in opts.get("ok", "0").split(",")}
        if opts.get("sec") and not os.environ.get("SEC_USER_AGENT", "").strip():
            msg = ("WAITING: the SEC_USER_AGENT secret is not set, and this tool refuses to call SEC "
                   "without it. Add it under Settings > Secrets and variables > Actions.")
            print("::warning title=%s::%s" % (skill, msg))
            summary("## %s\n\n**%s**\n" % (skill, msg))
            results.append((skill, "waiting for SEC_USER_AGENT", True))
            continue
        missing = [n for n in opts.get("needs", "").split(",") if n and n not in written]
        if missing:
            msg = "skipped: %s was not written by an earlier step in this run" % ", ".join(missing)
            print("::error title=%s::%s" % (skill, msg))
            summary("## %s\n\n**SKIPPED**: %s\n" % (skill, msg))
            results.append((skill, "skipped", False))
            continue
        if "@watchlist" in args:
            syms = watchlist_symbols(data)
            if not syms:
                summary("## %s\n\ntrader/watchlist.json has no hits, so there is nothing to run.\n" % skill)
                results.append((skill, "no symbols", True))
                continue
            args = args.replace("@watchlist", " ".join(shlex.quote(s) for s in syms))
        before = run_skill.snapshot(trader)
        code = run_skill.main(["--data", data, skill, args])
        after = run_skill.snapshot(trader)
        now_written = {"trader/" + k.replace(os.sep, "/") for k, v in after.items() if before.get(k) != v}
        written |= now_written
        good = code in ok_codes or (opts.get("okwrite") in now_written)
        if not good:
            print("::error title=%s::exit code %s" % (skill, code))
        results.append((skill, code, good))
        sys.stdout.flush()
    line = ", ".join("%s=%s%s" % (s, c, "" if g else " (FAILED)") for s, c, g in results)
    print("Chain: " + line)
    summary("---\n**Chain:** " + line + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("paths<<EOF_PATHS\n%s\nEOF_PATHS\n" % "\n".join(run_skill.RUN_DIRS))
            fh.write("label=%s\n" % "_".join(os.path.relpath(d, data).replace(os.sep, "_")
                                              for d in run_skill.RUN_DIRS[:1]))
    return 0 if all(g for _, _, g in results) else 1


if __name__ == "__main__":
    sys.exit(main())
