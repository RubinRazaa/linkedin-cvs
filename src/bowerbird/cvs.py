"""Applicant CVs from the hiring dashboard, through a real logged-in Chrome window."""
import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from .core import BASE, REST, REST_EVERY, Stopped, deadline, pause, set_slow
from .jobs import save_jd

PROFILE = Path.home() / ".bowerbird" / "chrome-profile"
VIEWER = "com.linkedin.sdui.flagshipnav.hiring.appeval.ResumeViewer"
PAGE_SIZE = 30
VIRTUAL_DISPLAY = ":99"  # used on Linux servers with no screen
# every stage the dashboard shows, and its name in applicants.csv
STAGES = {"IN_REVIEW": "in review", "HIRER_SHORTLISTED": "shortlisted", "REJECTED": "rejected"}
LOGGED_OUT = ("/login", "/uas/", "/checkpoint", "/authwall", "/signup")

# Runs inside the logged-in LinkedIn page, so the session never leaves the browser.
FETCH_JS = """async ([path, body]) => {
  const csrf = (document.cookie.match(/JSESSIONID="?([^";]+)/) || [])[1];
  const headers = {'x-li-rsc-stream': 'true', 'csrf-token': csrf};
  if (body) {
    headers['content-type'] = 'application/json';
    headers['x-li-anchor-page-key'] = 'd_flagship3_hiring_applicants_table';
  }
  const r = await fetch(path, {method: body ? 'POST' : 'GET', headers, body, credentials: 'include'});
  return [r.status, r.url, await r.text()];
}"""


def parse_applicants(text):
    """[(application_id, name)] from an applicants-table response, in row order."""
    ids = list(dict.fromkeys(re.findall(r"APPLICANTS_TABLE_ROW_STATE_(\d+)", text)))
    names = re.findall(r'"([^"\\]{2,60}) was moved to Shortlist', text)
    if len(names) != len(ids):  # layout changed: keep the ids, drop the names
        names = [""] * len(ids)
    return list(zip(ids, names))


def parse_pdf_url(text):
    m = re.search(r'https:[^"\s\\]*/dms/prv/document/media/[^"\s\\]*', text)
    return m.group(0) if m else None


def filename(app_id, name):
    safe = "_".join(re.sub(r"[^\w\- ]", "", name).split())
    return f"{safe}_{app_id}.pdf" if safe else f"{app_id}.pdf"


def logged_out(url):
    return any(part in url for part in LOGGED_OUT)


def write_index(folder, people):
    """applicants.csv: one row per applicant; 'file' is empty when no CV is saved."""
    files = {f.stem.rsplit("_", 1)[-1]: f.name for f in folder.glob("*.pdf")}
    with open(folder / "applicants.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["application_id", "name", "stage", "file"])
        w.writerows([i, name, stage, files.get(i, "")] for i, (name, stage) in people.items())


def ensure_display():
    """On a Linux server with no screen, start a virtual one. Returns the process or None."""
    if sys.platform != "linux" or os.environ.get("DISPLAY"):
        return None
    if not shutil.which("Xvfb"):
        raise Stopped("no display found: install one with 'sudo apt install xvfb'")
    # If :99 is already running (e.g. left up for --login), Xvfb exits and we reuse it.
    proc = subprocess.Popen(["Xvfb", VIRTUAL_DISPLAY, "-screen", "0", "1440x900x24"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    os.environ["DISPLAY"] = VIRTUAL_DISPLAY
    return proc


class Client:
    def __init__(self, playwright, proxy=None):
        from playwright.sync_api import Error

        self.xvfb = ensure_display()
        before_rename = Path.home() / ".linkedin_cvs"  # keep the login from the old name
        if before_rename.exists() and not PROFILE.parent.exists():
            before_rename.rename(PROFILE.parent)
        PROFILE.mkdir(parents=True, exist_ok=True)
        # ponytail: visible Chrome window each run; go headless only if that proves safe.
        self.ctx = playwright.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False, chromium_sandbox=True,
            proxy={"server": proxy} if proxy else None)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        try:
            self.page.goto(f"{BASE}/hiring/posted-jobs/")
        except Error as e:
            # With a proxy set Chrome never falls back to a direct connection.
            self.close()
            raise Stopped("could not reach LinkedIn"
                          + (f" through {proxy} (is the tunnel up?)" if proxy else "")
                          + f": {str(e).splitlines()[0]}")
        if logged_out(self.page.url):
            print("Log in to LinkedIn in the Chrome window that just opened "
                  "(one time only; waiting up to 5 minutes)...")
            try:
                self.page.wait_for_url(lambda u: "linkedin.com" in u and not logged_out(u),
                                       timeout=300_000)
                self.page.goto(f"{BASE}/hiring/posted-jobs/")
            except Error:  # nobody logged in (unattended run), or the window was closed
                self.close()
                raise Stopped("not logged in to LinkedIn: run with --login and log in once")

    def close(self):
        self.ctx.close()
        if self.xvfb:
            self.xvfb.terminate()

    def _fetch(self, path, body=None):
        status, url, text = self.page.evaluate(FETCH_JS, [path, body])
        # Stop at the first odd response instead of retrying into a flagged session.
        if status != 200 or logged_out(url) or not text:
            raise Stopped(f"HTTP {status}, {len(text)} bytes at {url.split('?')[0]} "
                          "(logged out, or LinkedIn challenged the request)")
        return text

    def open_jobs(self):
        """IDs of the job posts on the Open tab of Manage job posts."""
        text = self._fetch("/flagship-web/hiring/posted-jobs/")
        return list(dict.fromkeys(re.findall(r"jobId=(\d{8,12})", text)))

    def applicants(self, job_id):
        """{application_id: (name, stage)} for everyone who applied, whatever their stage."""
        people = {}
        for stage, label in STAGES.items():
            start = 0
            while True:
                pause()
                page = parse_applicants(self._fetch(
                    "/flagship-web/hiring/applicants-table/"
                    f"?workflow={stage}&jobId={job_id}&start={start}"))
                people.update({i: (name, label) for i, name in page})
                if len(page) < PAGE_SIZE:
                    break
                start += PAGE_SIZE
        return people

    def pdf_url(self, job_id, app_id):
        body = {"clientArguments": {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "requestedStateKeys": [],
            "payload": {"applicationId": app_id, "jobId": job_id},
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
            "states": [], "screenId": VIEWER, "knownTemplateIds": []},
            "isModal": True}
        return parse_pdf_url(self._fetch(
            f"/flagship-web/rsc-action/actions/navigation?screenId={VIEWER}&sduiid={VIEWER}",
            json.dumps(body)))

    def pdf(self, url):
        r = self.ctx.request.get(url)  # shares the window's login
        data = r.body() if r.status == 200 else b""
        if not data.startswith(b"%PDF"):
            raise Stopped(f"resume link returned HTTP {r.status}, not a PDF")
        return data


def download_cvs(job_ids=None, out="cvs", limit=None, slow=False, until=None, proxy=None,
                 index=False, report=None):
    """Download new CVs. job_ids=None means every open job post. Returns saved paths.

    until="06:00" stops cleanly at that local time; the next run continues with the rest.
    proxy="socks5://localhost:1080" sends Chrome's traffic through that proxy.
    index=True also writes applicants.csv (id, name, stage, file) in each job folder.
    report={} is filled as the run goes (saved, no_resume, stopped), so it is still
    there when the run raises Stopped.
    """
    from playwright.sync_api import sync_playwright

    set_slow(slow)
    end = deadline(until)
    report = {} if report is None else report
    report.update(saved=[], no_resume=[], stopped=None)
    saved = report["saved"]
    with sync_playwright() as pw:
        c = Client(pw, proxy)
        try:
            job_ids = list(job_ids or c.open_jobs())
            if not job_ids:
                raise Stopped("no open job posts visible. Run with --login and check "
                              "what LinkedIn shows on the job posts page")
            print(f"{len(job_ids)} job posts: {' '.join(job_ids)}")
            for job_id in job_ids:
                folder = Path(out) / job_id  # one folder per job, like the Drive layout
                folder.mkdir(parents=True, exist_ok=True)
                if not (folder / "job.json").exists():
                    try:  # public page, no login; never worth stopping the CV run for
                        save_jd(job_id, folder)
                    except (Stopped, OSError) as e:
                        print(f"job {job_id}: job description not saved ({e})")
                have = {f.stem.rsplit("_", 1)[-1] for f in folder.glob("*.pdf")}
                people = c.applicants(job_id)
                todo = [(i, name) for i, (name, _) in people.items() if i not in have][:limit]
                print(f"job {job_id}: {len(people)} applicants, {len(todo)} to download")
                try:
                    for app_id, name in todo:
                        if end and datetime.now() >= end:
                            report["stopped"] = f"reached {until}"
                            print(f"reached {until}: stopping, the next run continues")
                            return saved
                        pause()
                        url = c.pdf_url(job_id, app_id)
                        if not url:
                            report["no_resume"].append(app_id)
                            print(f"  no resume: {name or app_id}")
                            continue
                        path = folder / filename(app_id, name)
                        path.write_bytes(c.pdf(url))
                        saved.append(path)
                        print(f"  saved {path.name}", flush=True)
                        if slow and len(saved) % REST_EVERY == 0:
                            time.sleep(random.uniform(*REST))
                finally:
                    if index:
                        write_index(folder, people)
        finally:
            c.close()
    return saved


def login(proxy=None):
    """Open the window and leave it open until you close it (log in, clear prompts)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        c = Client(pw, proxy)
        print("Window open. Close it when LinkedIn shows your job posts.")
        c.page.wait_for_event("close", timeout=0)
