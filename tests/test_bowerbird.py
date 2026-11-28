"""Offline checks of the parsing and timing logic. Nothing here contacts LinkedIn."""
import json
from datetime import datetime

import pytest

from bowerbird import core, cvs, jobs
from bowerbird.core import BASE, QUICK, SLOW, Stopped, deadline, job_id, set_slow
from bowerbird.cvs import (filename, logged_out, parse_applicants, parse_contact, parse_details,
                           parse_pdf_url, parse_screening, write_index)
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
    write_index(tmp_path, {"111": {"name": "Ann Lee", "stage": "in review", "applied": "2026-01-02"},
                           "222": {"name": "", "stage": "rejected"}})
    assert (tmp_path / "applicants.csv").read_text().splitlines() == [
        "application_id,name,stage,applied,location,current_title,current_company,must_have,preferred,"
        "fit,email,phone,screening,file",
        "111,Ann Lee,in review,2026-01-02,,,,,,,,,,Ann_Lee_111.pdf", "222,,rejected,,,,,,,,,,,"]
    first = json.loads((tmp_path / "applicants.json").read_text())[0]
    assert first["applied"] == "2026-01-02" and first["file"] == "Ann_Lee_111.pdf"


def test_parse_details():
    kid = 'x "children":["%s"] y '
    row = ('["Applied on: 01/02/2026"] name ["Applied on: 01/02/2026"] ROW_VIEWED_STATE_111 '
           + "".join(kid % k for k in ["Buyer", "Acme", "Pune, India", "3/3", "Must-have", "4/5", "Preferred", "Top fit"]))
    assert parse_details(row + '["Applied on: soon"] n ["Applied on: soon"] ROW_VIEWED_STATE_222') == {
        "111": {"applied": "2026-01-02", "location": "Pune, India", "current_title": "Buyer",
                "current_company": "Acme", "must_have": "3/3", "preferred": "4/5", "fit": "Top fit"},
        "222": {"applied": "soon"}}
    assert parse_details("nothing") == {}


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


class FakeClient:
    lookups = 0

    def __init__(self, *a):
        pass

    def close(self):
        pass

    def open_jobs(self):
        return ["1234567890"]

    def applicants(self, job_id):
        return {"111": {"name": "Ann Lee", "stage": "in review"}, "222": {"name": "Bo", "stage": "rejected"}}

    def pdf_url(self, job_id, app_id):
        return "u" if app_id == "111" else None

    def details(self, job_id, app_id):
        FakeClient.lookups += 1
        if FakeClient.lookups == 1:  # as if LinkedIn's layout had changed
            return {"email": "", "phone": "", "screening": []}
        return {"email": "a@b.co", "phone": "", "screening": [
            {"question": "Based in Pune?", "ideal": "Yes", "answer": "Yes"}]}

    def pdf(self, url):
        return b"%PDF"


def test_download_include(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setattr(cvs, "Client", FakeClient)
    monkeypatch.setattr(cvs, "pause", lambda: None)
    monkeypatch.setattr(jobs, "job_details", lambda job_id: {
        "title": "Sales Lead", "company": "", "location": "", "url": "", "description": ""})
    report = {}
    cvs.download(out=tmp_path, include=["cv"], report=report)
    assert sorted(f.name for f in (tmp_path / "1234567890").iterdir()) == ["Ann_Lee_111.pdf"]
    assert report["no_resume"] == ["222"]
    cvs.download(out=tmp_path, report=report)  # everything; the folder gains its title
    folder = tmp_path / "Sales_Lead_1234567890"
    assert sorted(f.name for f in folder.iterdir()) == [
        "Ann_Lee_111.pdf", "applicants.csv", "applicants.json", "job.json", "job.txt"]
    assert report["saved"] == []  # the CV was already there
    cvs.download(out=tmp_path, include=["details"], limit=1)  # finds nothing: not kept
    cvs.download(out=tmp_path, include=["details"])
    cvs.download(out=tmp_path, include=["details"])   # everyone known: no more lookups
    cvs.download(out=tmp_path)                        # a plain run keeps the details
    assert FakeClient.lookups == 3
    rows = json.loads((folder / "applicants.json").read_text())
    assert [r["email"] for r in rows] == ["a@b.co", "a@b.co"]
    assert "Based in Pune? Yes" in (folder / "applicants.csv").read_text()
    with pytest.raises(Stopped):
        cvs.download(include=["emails"])


def test_parse_detail_sections():
    kid = 'x "children":["%s"] y '
    assert parse_contact(kid % "+971 5" + kid % "Copy phone number" + '"mailto:a%2Bb@c.co?x"') == {
        "email": "a+b@c.co", "phone": "+971 5"}
    assert parse_contact("nothing") == {"email": "", "phone": ""}
    assert parse_screening("".join(kid % k for k in ["Based in Pune?", "Ideal answer: Yes", "No"])) == [
        {"question": "Based in Pune?", "ideal": "Yes", "answer": "No"}]
    late = kid % "Q2?" + '"children":"$L2c" "$L2d"\n2c:["Ideal answer: No"]\n2d:["$","p",{"children":["No"]}]'
    assert parse_screening(late) == [{"question": "Q2?", "ideal": "No", "answer": "No"}]
