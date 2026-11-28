"""Job descriptions from LinkedIn's public job page. No login and no Chrome."""
import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .core import BASE, Stopped, pause, set_slow, slug
from .core import job_id as to_id


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
    job_id = to_id(job_id)  # a job link works too
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


def find_folder(out, job_id):
    """The folder already holding this job, whatever title it was named with."""
    return next((d for d in Path(out).glob(f"*{job_id}")
                 if d.is_dir() and d.name.rsplit("_", 1)[-1] == job_id), None)


def job_folder(out, job_id, refresh=False):
    """The folder of a job, <out>/<Job_Title>_<job_id>, with job.json and job.txt in it.

    The details are fetched once (again with refresh=True). An older folder of the
    same job, named by ID only or with an earlier title, is renamed, never duplicated.
    """
    old = find_folder(out, job_id)
    cached = old and not refresh and (old / "job.json").exists()
    job = json.loads((old / "job.json").read_text()) if cached else job_details(job_id)
    folder = Path(out) / "_".join(filter(None, [slug(job["title"])[:60], job_id]))
    if old and old != folder:
        old.rename(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if not cached:
        (folder / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
        (folder / "job.txt").write_text(
            f"{job['title']}\n{job['company']} | {job['location']}\n{job['url']}\n\n{job['description']}\n")
    return folder


def download_jds(job_ids, out="cvs", slow=False):
    """Save job.json and job.txt in each job's folder. Returns the job.json paths."""
    set_slow(slow)
    job_ids = [to_id(j) for j in job_ids]
    saved = []
    for n, job_id in enumerate(job_ids):
        if n:
            pause()
        saved.append(job_folder(out, job_id, refresh=True) / "job.json")
        print(f"job {job_id}: saved {saved[-1].parent.name}")
    return saved
