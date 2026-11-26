#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright"]
# ///
"""Download the CV of everyone who applied to your LinkedIn job posts.

    uv run linkedin_cvs.py                  every open job post
    uv run linkedin_cvs.py JOB_ID [...]     only these job posts
    uv run linkedin_cvs.py --limit 3        at most 3 new CVs per job
    uv run linkedin_cvs.py --login          open the window to log in or clear a prompt
    uv run linkedin_cvs.py --jd JOB_ID      job description and details; no login, no Chrome

No cookies to copy. The first run opens a Chrome window: log in to LinkedIn
there once. The login is kept in ~/.linkedin_cvs and reused on every later run.
CVs land in cvs/<job_id>/; ones already there are skipped.

From Python:  from linkedin_cvs import download_cvs, job_details
"""
import argparse
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://www.linkedin.com"
PROFILE = Path.home() / ".linkedin_cvs" / "chrome-profile"
VIEWER = "com.linkedin.sdui.flagshipnav.hiring.appeval.ResumeViewer"
PAGE_SIZE = 30
VIRTUAL_DISPLAY = ":99"  # used on Linux servers with no screen
STAGES = ["IN_REVIEW", "HIRER_SHORTLISTED", "REJECTED"]  # every stage the dashboard shows
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


def pause():
    time.sleep(random.uniform(4, 9))  # human-ish pace; do not lower


class Stopped(Exception):
    pass


def _text(fragment):
    """Readable text from a piece of HTML: paragraphs and list items become lines."""
    fragment = re.sub(r"<li[^>]*>", "- ", fragment)
    fragment = re.sub(r"<br\s*/?>|</p>|</li>|</ul>|</ol>", "\n", fragment)
    lines = [" ".join(line.split()) for line in html.unescape(re.sub(r"<[^>]+>", "", fragment)).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_job(page):
    """Fields of LinkedIn's public job posting page. Missing ones come back empty."""
    def first(pattern):
        m = re.search(pattern, page, re.S)
        return _text(m.group(1)) if m else ""
    desc = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
    return {
        "title": first(r'top-card-layout__title[^>]*>(.*?)</h'),
        "company": first(r'topcard__org-name-link[^>]*>(.*?)</a>'),
        "location": first(r'<span class="topcard__flavor topcard__flavor--bullet">(.*?)</span>'),
        "posted": first(r'posted-time-ago__text[^>]*>(.*?)</span>'),
        "applicants": first(r'num-applicants__caption[^>]*>(.*?)</'),
        # seniority level, employment type, job function, industries
        "criteria": {_text(k): _text(v) for k, v in re.findall(
            r'criteria-subheader">(.*?)</h3>\s*<span[^>]*>(.*?)</span>', page, re.S)},
        "description": _text(desc.group(1)) if desc else "",
        "description_html": desc.group(1).strip() if desc else "",
    }


def job_details(job_id):
    """Public details of a job post as a dict. No login and no Chrome needed."""
    req = urllib.request.Request(f"{BASE}/jobs-guest/jobs/api/jobPosting/{job_id}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise Stopped(f"job {job_id}: HTTP {e.code} (429 = too many requests, wait a minute; otherwise not public or closed)")
    job = {"job_id": str(job_id), "url": f"{BASE}/jobs/view/{job_id}", **parse_job(page)}
    if not job["title"]:
        raise Stopped(f"job {job_id}: no job posting in the response")
    return job


def download_jds(job_ids, out="cvs"):
    """Save <out>/<job_id>/job.json and job.txt for each job. Returns the job.json paths."""
    saved = []
    for n, job_id in enumerate(job_ids):
        if n:
            pause()
        job = job_details(job_id)
        folder = Path(out) / str(job_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
        (folder / "job.txt").write_text(
            f"{job['title']}\n{job['company']} | {job['location']}\n{job['url']}\n\n{job['description']}\n")
        saved.append(folder / "job.json")
        print(f"job {job_id}: saved {job['title']}")
    return saved


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
    def __init__(self, playwright):
        self.xvfb = ensure_display()
        PROFILE.mkdir(parents=True, exist_ok=True)
        # ponytail: visible Chrome window each run; go headless only if that proves safe.
        self.ctx = playwright.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=False, chromium_sandbox=True)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.goto(f"{BASE}/hiring/posted-jobs/")
        if logged_out(self.page.url):
            print("Log in to LinkedIn in the Chrome window that just opened "
                  "(one time only; waiting up to 5 minutes)...")
            self.page.wait_for_url(lambda u: "linkedin.com" in u and not logged_out(u),
                                   timeout=300_000)
            self.page.goto(f"{BASE}/hiring/posted-jobs/")

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
        """{application_id: name} for everyone who applied, whatever their stage."""
        people = {}
        for stage in STAGES:
            start = 0
            while True:
                pause()
                page = parse_applicants(self._fetch(
                    "/flagship-web/hiring/applicants-table/"
                    f"?workflow={stage}&jobId={job_id}&start={start}"))
                people.update(page)
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


def download_cvs(job_ids=None, out="cvs", limit=None):
    """Download new CVs. job_ids=None means every open job post. Returns saved paths."""
    from playwright.sync_api import sync_playwright

    saved = []
    with sync_playwright() as pw:
        c = Client(pw)
        try:
            job_ids = list(job_ids or c.open_jobs())
            if not job_ids:
                raise Stopped("no open job posts visible. Run with --login and check "
                              "what LinkedIn shows on the job posts page")
            print(f"{len(job_ids)} job posts: {' '.join(job_ids)}")
            for job_id in job_ids:
                folder = Path(out) / job_id  # one folder per job, like the Drive layout
                folder.mkdir(parents=True, exist_ok=True)
                have = {f.stem.rsplit("_", 1)[-1] for f in folder.glob("*.pdf")}
                people = c.applicants(job_id)
                todo = [(i, n) for i, n in people.items() if i not in have][:limit]
                print(f"job {job_id}: {len(people)} applicants, {len(todo)} to download")
                for app_id, name in todo:
                    pause()
                    url = c.pdf_url(job_id, app_id)
                    if not url:
                        print(f"  no resume: {name or app_id}")
                        continue
                    path = folder / filename(app_id, name)
                    path.write_bytes(c.pdf(url))
                    saved.append(path)
                    print(f"  saved {path.name}")
        finally:
            c.close()
    return saved


def login():
    """Open the window and leave it open until you close it (log in, clear prompts)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        c = Client(pw)
        print("Window open. Close it when LinkedIn shows your job posts.")
        c.page.wait_for_event("close", timeout=0)


def selftest():
    sample = ('x APPLICANTS_TABLE_ROW_STATE_111 y APPLICANTS_TABLE_ROW_STATE_222 '
              'APPLICANTS_TABLE_ROW_STATE_111 ["Ann Lee was moved to Shortlist."] '
              '["Bo O\'Neil was moved to Shortlist."] '
              '"https://www.linkedin.com/dms/prv/document/media/v2/A/doc-pdf-analyzed/B/0/1?m=1&t=x" ')
    assert parse_applicants(sample) == [("111", "Ann Lee"), ("222", "Bo O'Neil")]
    assert parse_applicants("APPLICANTS_TABLE_ROW_STATE_5") == [("5", "")]
    assert parse_pdf_url(sample).endswith("/0/1?m=1&t=x")
    assert parse_pdf_url("nothing") is None
    assert filename("222", "Bo O'Neil") == "Bo_ONeil_222.pdf"
    assert filename("5", "") == "5.pdf"
    assert logged_out(f"{BASE}/uas/login?x=1") and not logged_out(f"{BASE}/hiring/posted-jobs/")
    job = parse_job('<h2 class="top-card-layout__title x">A &amp; B</h2>'
                    '<h3 class="description__job-criteria-subheader"> Employment type </h3>\n'
                    '<span class="t"> Full-time </span>'
                    '<div class="show-more-less-html__markup y"><p>Hi<br>there</p><ul><li>one</li></ul></div>')
    assert job["title"] == "A & B" and job["criteria"] == {"Employment type": "Full-time"}
    assert job["description"] == "Hi\nthere\n- one" and job["company"] == ""
    print("selftest ok")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("job_ids", nargs="*", help="default: every open job post")
    p.add_argument("--out", default="cvs")
    p.add_argument("--limit", type=int, help="download at most N new CVs per job")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--login", action="store_true", help="open the window to log in or clear a prompt")
    p.add_argument("--jd", action="store_true", help="save the job description and details; no login needed")
    a = p.parse_args()
    if a.selftest:
        return selftest()
    if a.login:
        return login()
    try:
        if a.jd:
            if not a.job_ids:
                sys.exit("--jd needs at least one job ID")
            saved = download_jds(a.job_ids, a.out)
        else:
            saved = download_cvs(a.job_ids, a.out, a.limit)
    except Stopped as e:
        sys.exit(f"Stopped: {e}")
    print(f"done: {len(saved)} {'job posts' if a.jd else 'CVs'} saved under {a.out}/")


if __name__ == "__main__":
    main()
