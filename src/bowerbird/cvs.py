"""Applicant CVs from the hiring dashboard, through a real logged-in Chrome window."""
import csv
import fcntl
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
from urllib.parse import unquote

from .core import BASE, REST, REST_EVERY, Stopped, deadline, pause, set_slow, slug
from .core import job_id as to_id
from .jobs import download_jds, find_folder, job_folder

PROFILE = Path.home() / ".bowerbird" / "chrome-profile"
VIEWER = "com.linkedin.sdui.flagshipnav.hiring.appeval.ResumeViewer"
PAGE_SIZE = 30
VIRTUAL_DISPLAY = ":99"  # used on Linux servers with no screen
# every stage the dashboard shows, and its name in applicants.csv
STAGES = {"IN_REVIEW": "in review", "HIRER_SHORTLISTED": "shortlisted", "REJECTED": "rejected"}
PARTS = ("cv", "job", "applicants", "details")  # what a run can collect
DEFAULT = PARTS[:3]  # details cost two more requests per applicant, so they are opt-in
DETAIL = "com.linkedin.sdui.generated.jobposting.dsl.impl.applicantDetail"
DETAIL_SCREEN = "com.linkedin.sdui.flagshipnav.hiring.appeval.ApplicantDetail"
KID = r'children":\["([^"\\]{1,300})"\]'  # one piece of text shown on the page
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


def parse_details(text):
    """{application_id: {applied, location, must_have, ...}} from the same response.

    Anything LinkedIn does not show, or shows in a new layout, is simply left out.
    """
    out = {}
    parts = re.split(r'\["Applied on: ([^"\\]+)"\]', text)
    for applied, row in zip(parts[1::2], parts[2::2]):
        app = re.search(r"ROW_VIEWED_STATE_(\d+)", row)
        if not app:  # the date is rendered twice; only the second is followed by the row
            continue
        kids = re.findall(r'children":\["([^"\\]{1,80})"\]', row[app.end():])[:10]
        d = {"applied": applied}
        try:
            d["applied"] = datetime.strptime(applied, "%m/%d/%Y").date().isoformat()
        except ValueError:
            pass  # another locale: keep LinkedIn's own text
        if "Must-have" in kids[1:]:
            at = kids.index("Must-have")
            d["must_have"] = kids[at - 1]
            before = kids[:at - 1]  # [current title, current company,] location
            if before:
                d["location"] = before[-1]
            if len(before) == 3:
                d["current_title"], d["current_company"] = before[:2]
        if "Preferred" in kids[1:]:
            at = kids.index("Preferred")
            d["preferred"] = kids[at - 1]
            if kids[at + 1:at + 2] and kids[at + 1].endswith(" fit"):
                d["fit"] = kids[at + 1]
        out[app.group(1)] = d
    return out


def parse_contact(text):
    """{email, phone} from the contact section of an applicant's detail."""
    kids = re.findall(KID, text)
    email = re.search(r'mailto:([^"\\?&]+)', text)
    return {"email": unquote(email.group(1)) if email else "",
            "phone": next((a for a, b in zip(kids, kids[1:]) if b == "Copy phone number"), "")}


def parse_screening(text):
    """[{question, ideal, answer}] from the screening section of an applicant's detail."""
    # LinkedIn streams some answers as separate numbered lines; put them back in place
    late = dict(re.findall(r"(?m)^([0-9a-f]+):(\[.*\])$", text))
    for _ in range(3):
        text = re.sub(r'"\$L([0-9a-f]+)"', lambda m: late.get(m.group(1), m.group(0)), text)
    kids = re.findall(KID, text)
    return list({q: {"question": q, "ideal": ideal.split(": ", 1)[1], "answer": answer}
                 for q, ideal, answer in zip(kids, kids[1:], kids[2:])
                 if ideal.startswith("Ideal answer: ")}.values())


def parse_pdf_url(text):
    m = re.search(r'https:[^"\s\\]*/dms/prv/document/media/[^"\s\\]*', text)
    return m.group(0) if m else None


def filename(app_id, name):
    safe = slug(name)
    return f"{safe}_{app_id}.pdf" if safe else f"{app_id}.pdf"


def logged_out(url):
    return any(part in url for part in LOGGED_OUT)


COLUMNS = ["application_id", "name", "stage", "applied", "location", "current_title",
           "current_company", "must_have", "preferred", "fit", "email", "phone", "screening",
           "file"]


def write_index(folder, people):
    """applicants.csv and applicants.json: one entry per applicant; 'file' is empty when no CV is saved."""
    files = {f.stem.rsplit("_", 1)[-1]: f.name for f in folder.glob("*.pdf")}
    rows = [{**dict.fromkeys(COLUMNS, ""), **p, "application_id": i, "file": files.get(i, "")}
            for i, p in people.items()]
    with open(folder / "applicants.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS)
        w.writeheader()  # the CSV holds the screening answers as one readable cell
        w.writerows({**r, "screening": " | ".join(f"{a['question']} {a['answer']}"
                                                   for a in r["screening"] or [])} for r in rows)
    (folder / "applicants.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))


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

        before_rename = Path.home() / ".linkedin_cvs"  # keep the login from the old name
        if before_rename.exists() and not PROFILE.parent.exists():
            before_rename.rename(PROFILE.parent)
        PROFILE.mkdir(parents=True, exist_ok=True)
        self.lock = open(PROFILE.parent / "lock", "w")  # freed when the process ends
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Stopped("another run is active: wait for it to finish")
        self.xvfb = ensure_display()
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
        """{application_id: {name, stage, applied, ...}} for everyone who applied, whatever their stage."""
        people = {}
        for stage, label in STAGES.items():
            start = 0
            while True:
                pause()
                text = self._fetch("/flagship-web/hiring/applicants-table/"
                                   f"?workflow={stage}&jobId={job_id}&start={start}")
                page, details = parse_applicants(text), parse_details(text)
                people.update({i: {"name": name, "stage": label, **details.get(i, {})}
                               for i, name in page})
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

    def details(self, job_id, app_id):
        """{email, phone, screening} of one applicant: two requests."""
        def section(name):
            pause()
            body = {"componentId": DETAIL + name, "clientArguments": {
                "payload": {"jobPostingId": job_id, "applicationId": app_id}, "states": [],
                "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                "screenId": DETAIL_SCREEN, "knownTemplateIds": []}}
            return self._fetch("/flagship-web/rsc-action/actions/component"
                               f"?componentId={DETAIL + name}&sduiid={DETAIL + name}",
                               json.dumps(body))
        return {**parse_contact(section("CtaSection")),
                "screening": parse_screening(section("ProfileAndScreeningSection"))}

    def pdf(self, url):
        r = self.ctx.request.get(url)  # shares the window's login
        data = r.body() if r.status == 200 else b""
        if not data.startswith(b"%PDF"):
            raise Stopped(f"resume link returned HTTP {r.status}, not a PDF")
        return data


def download(job_ids=None, out="cvs", include=DEFAULT, limit=None, slow=False, until=None,
             proxy=None, report=None):
    """Collect the chosen parts for each job post. Returns the saved CV paths.

    job_ids=None means every open job post; IDs and job links both work.
    include picks what to collect, any of:
      "cv"          each applicant's CV as a PDF (CVs already on disk are skipped)
      "job"         job.json and job.txt, and the job title in the folder name
      "applicants"  applicants.csv and applicants.json (id, name, stage, date applied,
                    location, qualification scores, fit, file)
      "details"     adds email, phone and screening answers to those two files. Not in
                    the default: two more requests per applicant, made once and kept
    limit caps the new CVs, and the new detail lookups, per job.
    until="06:00" stops cleanly at that local time; the next run continues with the rest.
    proxy="socks5://localhost:1080" sends Chrome's traffic through that proxy.
    report={} is filled as the run goes (saved, no_resume, stopped), so it is still
    there when the run raises Stopped.
    """
    from playwright.sync_api import sync_playwright

    include = set(include)
    if not include or include - set(PARTS):
        raise Stopped(f"include takes any of: {', '.join(PARTS)}")
    set_slow(slow)
    end = deadline(until)
    job_ids = [to_id(j) for j in job_ids or []]
    report = {} if report is None else report
    report.update(saved=[], no_resume=[], stopped=None)
    saved = report["saved"]
    if job_ids and include == {"job"}:  # public pages only: no login, no Chrome
        download_jds(job_ids, out, slow)
        return saved
    with sync_playwright() as pw:
        c = Client(pw, proxy)
        try:
            job_ids = job_ids or c.open_jobs()
            if not job_ids:
                raise Stopped("no open job posts visible. Run with --login and check "
                              "what LinkedIn shows on the job posts page")
            print(f"{len(job_ids)} job posts: {' '.join(job_ids)}")
            for job_id in job_ids:
                folder = find_folder(out, job_id) or Path(out) / job_id
                if "job" in include:
                    try:  # public page, no login; never worth stopping the run for
                        folder = job_folder(out, job_id)
                    except (Stopped, OSError) as e:
                        print(f"job {job_id}: job description not saved ({e})")
                folder.mkdir(parents=True, exist_ok=True)
                if include == {"job"}:
                    continue
                have = {f.stem.rsplit("_", 1)[-1] for f in folder.glob("*.pdf")}
                people = c.applicants(job_id)
                try:  # details from an earlier run are kept and never asked for twice
                    known = {r["application_id"]: r for r in
                             json.loads((folder / "applicants.json").read_text())}
                except (OSError, ValueError, KeyError, TypeError):
                    known = {}
                for i, p in people.items():
                    if isinstance(known.get(i, {}).get("screening"), list):
                        p.update({k: known[i].get(k, "") for k in ("email", "phone", "screening")})
                lookups = [i for i, p in people.items() if "screening" not in p][:limit]
                if "details" not in include:
                    lookups = []
                todo = [(i, p["name"]) for i, p in people.items() if i not in have][:limit]
                if "cv" not in include:
                    todo = []
                print(f"job {job_id}: {len(people)} applicants, {len(todo)} to download"
                      + (f", {len(lookups)} detail lookups" if lookups else ""))

                def out_of_time():
                    if end and datetime.now() >= end:
                        report["stopped"] = f"reached {until}"
                        print(f"reached {until}: stopping, the next run continues")
                        return True

                try:
                    for n, app_id in enumerate(lookups, 1):
                        if out_of_time():
                            return saved
                        people[app_id].update(c.details(job_id, app_id))
                        print(f"  details {n}/{len(lookups)}", flush=True)
                        if slow and n % REST_EVERY == 0:
                            time.sleep(random.uniform(*REST))
                    for app_id, name in todo:
                        if out_of_time():
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
                    if include & {"applicants", "details"}:
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
