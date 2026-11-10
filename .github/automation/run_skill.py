#!/usr/bin/env python3
"""Run one original Day Trader Skills tool exactly as the README documents it.

    python .github/automation/run_skill.py --data data trader-scanner "daily"
    python .github/automation/run_skill.py --data data trader-scanner "orb --at 10:15"
    python .github/automation/run_skill.py --data data trader-news ""

This is plumbing only. It adds no filter, no default, no ticker choice and no
schedule: the arguments go to the tool unchanged, and the tool's own defaults
apply. The tool runs with the data folder as its working directory, so it
reads and writes `trader/` there, the way the README describes ("Everything
lands in a trader/ folder ... each skill reads what the last one wrote").

What it records, for every run, under <data>/runs/<New York date>/<time>-<skill>/:
  run.json      the command, exit code, seconds, stdout and stderr
  stdout.txt    the tool's output as printed
  stderr.txt    the tool's messages and errors, if any
  files/        a copy of every trader/ file the run created or changed
The process exits with the tool's own exit code, so a failure is never hidden.

trader-broker is not runnable here: it places orders.
"""

import argparse
import datetime
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = {
    "trader-scanner": "trader-scanner/scan.py",
    "trader-news": "trader-news/news.py",
    "trader-entry": "trader-entry/entry.py",
    "trader-exit": "trader-exit/exit.py",
    "trader-timemachine": "trader-timemachine/timemachine.py",
    "trader-budget": "trader-budget/size.py",
    "trader-risk": "trader-risk/check.py",
    "trader-alerts": "trader-alerts/alerts.py",
    "trader-analyst": "trader-analyst/facts.py",
    "trader-pelosi": "trader-pelosi/ptr.py",
}
RUN_DIRS = []  # every run folder made in this process (run_chain.py reads it)
EXIT_MEANING = {
    "trader-scanner": "0 = the scan found something, 1 = it found nothing or could not run (the reason is printed)",
    "trader-news": "0 = something new, 1 = nothing new, 2 = no tickers",
    "trader-entry": "check: 0 = triggered, 1 = not triggered or could not run",
    "trader-alerts": "check: 0 = something rang; clock: 0 = open, 1 = closed",
    "trader-analyst": "0 = every ticker read, 1 = some failed, 2 = none",
    "trader-pelosi": "new: 0 = new filings, 1 = nothing new, 2 = error",
}


def new_york_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")), "New York"
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc), "UTC"


def snapshot(folder):
    out = {}
    for base, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(base, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, folder)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run one original Day Trader Skills tool, unchanged.")
    ap.add_argument("--data", required=True, help="folder that holds trader/ and runs/ (the results branch)")
    ap.add_argument("skill", choices=sorted(TOOLS))
    ap.add_argument("args", nargs="?", default="", help="the tool's own arguments, as one string")
    a = ap.parse_args(argv)

    data = os.path.abspath(a.data)
    trader = os.path.join(data, "trader")
    os.makedirs(trader, exist_ok=True)
    tool_args = shlex.split(a.args)
    cmd = [sys.executable, os.path.join(ROOT, TOOLS[a.skill])] + tool_args
    shown = "python %s %s" % (TOOLS[a.skill], " ".join(shlex.quote(x) for x in tool_args))

    now, zone = new_york_now()
    run_dir = os.path.join(data, "runs", now.strftime("%Y-%m-%d"), "%s-%s" % (now.strftime("%H%M%S"), a.skill))
    os.makedirs(run_dir, exist_ok=True)

    before = snapshot(trader)
    print("$ " + shown, flush=True)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    secs = round(time.time() - t0, 1)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    sys.stdout.write(out)
    sys.stderr.write(err)

    after = snapshot(trader)
    changed = sorted(k for k, v in after.items() if before.get(k) != v)
    for rel in changed:
        dst = os.path.join(run_dir, "files", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(trader, rel), dst)
    with open(os.path.join(run_dir, "stdout.txt"), "w", encoding="utf-8") as fh:
        fh.write(out)
    if err:
        with open(os.path.join(run_dir, "stderr.txt"), "w", encoding="utf-8") as fh:
            fh.write(err)
    record = {"skill": a.skill, "command": shown, "exit_code": p.returncode, "seconds": secs,
              "started": now.strftime("%Y-%m-%d %H:%M:%S"), "time_zone": zone,
              "python": sys.version.split()[0], "changed_files": ["trader/" + c.replace(os.sep, "/") for c in changed],
              "exit_code_meaning": EXIT_MEANING.get(a.skill), "stdout": out, "stderr": err,
              "github_run_id": os.environ.get("GITHUB_RUN_ID")}
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1, ensure_ascii=False)
        fh.write("\n")

    RUN_DIRS.append(run_dir)
    rel = os.path.relpath(run_dir, data).replace(os.sep, "/")
    md = ["## %s" % a.skill, "", "`%s`" % shown, "",
          "**Exit code %d** (%s), %s s, Python %s, %s %s" % (
              p.returncode, EXIT_MEANING.get(a.skill) or "0 = success", secs, record["python"], zone,
              record["started"]), ""]
    if err.strip():
        md += ["### stderr", "```", err.strip()[-6000:], "```", ""]
    md += ["### stdout", "```", out.strip()[-20000:] or "(empty)", "```", ""]
    if changed:
        md += ["Changed: " + ", ".join(record["changed_files"]), ""]
    md += ["Saved to `results` branch: `%s/`" % rel, ""]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write("run_dir=%s\nrun_rel=%s\nartifact=%s\n" % (run_dir, rel, rel.replace("/", "_")))
    if p.returncode != 0:
        print("::warning title=%s::exit code %d (%s). See the step summary." % (
            a.skill, p.returncode, EXIT_MEANING.get(a.skill) or "non-zero"))
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
