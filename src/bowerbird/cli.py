"""Download the CV of everyone who applied to your LinkedIn job posts.

    bowerbird                  every open job post
    bowerbird JOB_ID [...]     only these job posts
    bowerbird --limit 3        at most 3 new CVs per job
    bowerbird --slow           long pauses, for unattended overnight runs
    bowerbird --slow --until 06:00   nightly run: stop at 06:00, continue next night
    bowerbird --proxy socks5://localhost:1080   send Chrome through your own network
    bowerbird --login          open the window to log in or clear a prompt
    bowerbird --jd JOB_ID      job description and details; no login, no Chrome
    bowerbird --index          also write applicants.csv in each job folder
    bowerbird --json           print one JSON result on stdout, for calling from an app

No cookies to copy. The first run opens a Chrome window: log in to LinkedIn
there once. The login is kept in ~/.bowerbird and reused on every later run.
CVs land in cvs/<job_id>/ next to job.json and job.txt (the job description);
CVs already there are skipped.

From Python:  from bowerbird import download_cvs, job_details
"""
import argparse
import contextlib
import json
import sys

from .core import Stopped, deadline
from .cvs import download_cvs, login
from .jobs import download_jds


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
    p.add_argument("--index", action="store_true",
                   help="also write applicants.csv (id, name, stage, file) in each job folder")
    p.add_argument("--json", action="store_true",
                   help="print one JSON result on stdout; progress goes to stderr")
    p.add_argument("--login", action="store_true", help="open the window to log in or clear a prompt")
    p.add_argument("--jd", action="store_true", help="save the job description and details; no login needed")
    a = p.parse_args()
    if a.jd and not a.job_ids:
        sys.exit("--jd needs at least one job ID")
    report = {"saved": [], "no_resume": [], "stopped": None}
    error = None
    # With --json, stdout carries the result only.
    with contextlib.redirect_stdout(sys.stderr if a.json else sys.stdout):
        try:
            if a.login:
                return login(a.proxy)
            if a.jd:
                report["saved"] = download_jds(a.job_ids, a.out, a.slow)
            else:
                download_cvs(a.job_ids, a.out, a.limit, a.slow, a.until, a.proxy, a.index, report)
        except Stopped as e:
            error = str(e)
        else:
            print(f"done: {len(report['saved'])} {'job posts' if a.jd else 'CVs'} saved under {a.out}/")
    if a.json:
        print(json.dumps({"ok": error is None, "stopped": error or report["stopped"],
                          "saved": [str(f) for f in report["saved"]],
                          "no_resume": report["no_resume"]}))
    if error:
        sys.exit(f"Stopped: {error}")
