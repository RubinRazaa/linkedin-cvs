"""Download the CV of everyone who applied to your LinkedIn job posts.

    bowerbird                  every open job post
    bowerbird JOB_ID [...]     only these job posts
    bowerbird --limit 3        at most 3 new CVs per job
    bowerbird --slow           long pauses, for unattended overnight runs
    bowerbird --slow --until 06:00   nightly run: stop at 06:00, continue next night
    bowerbird --proxy socks5://localhost:1080   send Chrome through your own network
    bowerbird --login          open the window to log in or clear a prompt
    bowerbird --include cv     only the CVs (default: cv,job,applicants)
    bowerbird --include job JOB_ID   job description only; no login, no Chrome
    bowerbird --json           print one JSON result on stdout, for calling from an app

No cookies to copy. The first run opens a Chrome window: log in to LinkedIn
there once. The login is kept in ~/.bowerbird and reused on every later run.
Each job gets cvs/<Job_Title>_<job_id>/ with the CVs, job.json and job.txt (the job
description) and applicants.csv/.json (the applicant list). CVs already there are skipped.

From Python:  from bowerbird import download, job_details
"""
import argparse
import contextlib
import json
import sys

from .core import Stopped, deadline
from .cvs import PARTS, download, login


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("job_ids", nargs="*", help="default: every open job post")
    p.add_argument("--out", default="cvs")
    p.add_argument("--limit", type=int, help="download at most N new CVs per job")
    p.add_argument("--slow", action="store_true",
                   help="15-40 s between requests and a few minutes' rest every 20 CVs")
    p.add_argument("--until", metavar="HH:MM", type=lambda v: deadline(v) and v,
                   help="stop cleanly at this local time; the next run continues")
    p.add_argument("--proxy", metavar="URL",
                   help="send Chrome through this proxy, e.g. socks5://localhost:1080")
    p.add_argument("--include", metavar="PARTS", default=",".join(PARTS),
                   type=lambda v: [x.strip() for x in v.split(",") if x.strip()],
                   help=f"what to collect, comma-separated (default: {','.join(PARTS)})")
    p.add_argument("--json", action="store_true",
                   help="print one JSON result on stdout; progress goes to stderr")
    p.add_argument("--login", action="store_true", help="open the window to log in or clear a prompt")
    a = p.parse_args()
    report = {"saved": [], "no_resume": [], "stopped": None}
    error = None
    # With --json, stdout carries the result only.
    with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):
        try:
            if a.login:
                return login(a.proxy)
            download(a.job_ids, a.out, a.include, a.limit, a.slow, a.until, a.proxy, report)
        except Stopped as e:
            error = str(e)
        else:
            print(f"done: {len(report['saved'])} CVs saved under {a.out}/")
    if a.json:
        print(json.dumps({"ok": error is None, "stopped": error or report["stopped"],
                          "saved": [str(f) for f in report["saved"]],
                          "no_resume": report["no_resume"]}))
    if error:
        sys.exit(f"Stopped: {error}")
