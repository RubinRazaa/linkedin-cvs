"""Job descriptions from LinkedIn's public job page. No login and no Chrome."""
import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .core import BASE, Stopped, pause, set_slow


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


def download_jds(job_ids, out="cvs", slow=False):
    """Save <out>/<job_id>/job.json and job.txt for each job. Returns the job.json paths."""
    set_slow(slow)
    saved = []
    for n, job_id in enumerate(job_ids):
        if n:
            pause()
        folder = Path(out) / str(job_id)
        job = save_jd(job_id, folder)
        saved.append(folder / "job.json")
        print(f"job {job_id}: saved {job['title']}")
    return saved


def save_jd(job_id, folder):
    """Write job.json and job.txt into folder. Returns the job dict."""
    job = job_details(job_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
    (folder / "job.txt").write_text(
        f"{job['title']}\n{job['company']} | {job['location']}\n{job['url']}\n\n{job['description']}\n")
    return job
