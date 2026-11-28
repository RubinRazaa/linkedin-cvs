"""Offline checks of the parsing and timing logic. Nothing here contacts LinkedIn."""
from datetime import datetime

import pytest

from bowerbird import core, jobs
from bowerbird.core import BASE, QUICK, SLOW, Stopped, deadline, job_id, set_slow
from bowerbird.cvs import filename, logged_out, parse_applicants, parse_pdf_url, write_index
from bowerbird.jobs import parse_job

SAMPLE = ('x APPLICANTS_TABLE_ROW_STATE_111 y APPLICANTS_TABLE_ROW_STATE_222 '
          'APPLICANTS_TABLE_ROW_STATE_111 ["Ann Lee was moved to Shortlist."] '
          '["Bo O\'Neil was moved to Shortlist."] '
          '"https://www.linkedin.com/dms/prv/document/media/v2/A/doc-pdf-analyzed/B/0/1?m=1&t=x" ')


def test_parse_applicants():
    assert parse_applicants(SAMPLE) == [("111", "Ann Lee"), ("222", "Bo O'Neil")]
    assert parse_applicants("APPLICANTS_TABLE_ROW_STATE_5") == [("5", "")]


def test_parse_pdf_url():
    assert parse_pdf_url(SAMPLE).endswith("/0/1?m=1&t=x")
    assert parse_pdf_url("nothing") is None


def test_filename():
    assert filename("222", "Bo O'Neil") == "Bo_ONeil_222.pdf"
    assert filename("5", "") == "5.pdf"


def test_logged_out():
    assert logged_out(f"{BASE}/uas/login?x=1")
    assert not logged_out(f"{BASE}/hiring/posted-jobs/")


def test_parse_job():
    job = parse_job('<h2 class="top-card-layout__title x">A &amp; B</h2>'
                    '<h3 class="description__job-criteria-subheader"> Employment type </h3>\n'
                    '<span class="t"> Full-time </span>'
                    '<div class="show-more-less-html__markup y"><p>Hi<br>there</p><ul><li>one</li></ul></div>')
    assert job["title"] == "A & B" and job["criteria"] == {"Employment type": "Full-time"}
    assert job["description"] == "Hi\nthere\n- one" and job["company"] == ""


def test_deadline():
    night = datetime(2026, 1, 1, 23, 0)
    assert deadline("06:00", night) == datetime(2026, 1, 2, 6, 0)
    assert deadline("23:30", night) == datetime(2026, 1, 1, 23, 30)
    assert deadline(None) is None


def test_pace():
    set_slow(True)
    assert core._pace == SLOW and SLOW[0] > QUICK[1]
    set_slow(False)
    assert core._pace == QUICK


def test_write_index(tmp_path):
    (tmp_path / "Ann_Lee_111.pdf").write_bytes(b"%PDF")
    write_index(tmp_path, {"111": ("Ann Lee", "in review"), "222": ("", "rejected")})
    assert (tmp_path / "applicants.csv").read_text().splitlines() == [
        "application_id,name,stage,file", "111,Ann Lee,in review,Ann_Lee_111.pdf", "222,,rejected,"]


def test_job_id():
    assert job_id("1234567890") == job_id(1234567890) == "1234567890"
    assert job_id(f"{BASE}/jobs/view/1234567890/?trk=x") == "1234567890"
    assert job_id(f"{BASE}/hiring/jobs/2345678901/detail/") == "2345678901"
    assert job_id(f"{BASE}/jobs/search/?currentJobId=1234567890&start=25") == "1234567890"
    with pytest.raises(Stopped):
        job_id("not a job")


def test_job_folder(tmp_path, monkeypatch):
    job = {"title": "Sales / Lead (UAE)", "company": "", "location": "", "url": "", "description": ""}
    monkeypatch.setattr(jobs, "job_details", lambda job_id: job)
    (tmp_path / "1234567890").mkdir()  # folder from before titles were used
    (tmp_path / "1234567890" / "a_1.pdf").write_bytes(b"%PDF")
    folder = jobs.job_folder(tmp_path, "1234567890")
    assert folder.name == "Sales_Lead_UAE_1234567890" and (folder / "a_1.pdf").exists()
    assert [d.name for d in tmp_path.iterdir()] == [folder.name]
    monkeypatch.setattr(jobs, "job_details", None)  # second time: no request at all
    assert jobs.job_folder(tmp_path, "1234567890") == folder
