"""Offline checks of the parsing and timing logic. Nothing here contacts LinkedIn."""
from datetime import datetime

from bowerbird import core
from bowerbird.core import BASE, QUICK, SLOW, deadline, set_slow
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
