"""Jobright adapter.

Jobright has no public list API — /swan/job/list answers 401 "Cookie not
found" — so discovery goes through the paths their robots.txt names
explicitly (Allow: /remote-jobs/*, Allow: /jobs/*), reached from their own
sitemap. Each job page carries the record as Next.js __NEXT_DATA__.

These tests parse fixed HTML rather than calling the site, so they stay green
offline and do not hammer someone else's server on every run.
"""
from __future__ import annotations

import pytest

from app.adapters.boards import ADAPTERS
from app.adapters.boards.fetchers import (
    JobrightChallenged,
    _jobright_next_data,
    _jobright_row,
)
from app.services.fetch_sites import resolve_site


def _entry(**overrides) -> dict:
    """One jobList entry, the shape their listing pages embed."""
    job = {
        "jobId": "69d5669fe63cea7a8b660fe4",
        "jobTitle": "Senior Data Engineer",
        "jobSeniority": "Senior Level",
        "jobLocation": "United States",
        "isRemote": "True",
        "workModel": "Remote",
        "publishTime": "2026-08-15 11:20:33",
        "salaryDesc": "$130K/yr - $160K/yr",
        "jobSummary": "Build and own batch pipelines.",
        "jdResponsibilitySummary": "Design ETL into the warehouse.",
        "coreResponsibilities": ["Own data quality", "Mentor engineers"],
    }
    job.update(overrides.pop("job", {}))
    company = {"companyName": "Acme Analytics", "companyDesc": "A data company."}
    company.update(overrides.pop("company", {}))
    return {"jobResult": job, "companyResult": company}


class TestParsing:
    def test_a_job_page_becomes_a_listing(self):
        row = _jobright_row(_entry())
        assert row is not None
        assert row.source == "Jobright", "source is the board, never the company"
        assert row.company_name == "Acme Analytics"
        assert row.job_title == "Senior Data Engineer"
        assert row.job_url.endswith("/jobs/info/69d5669fe63cea7a8b660fe4")
        assert row.external_id == "69d5669fe63cea7a8b660fe4"

    def test_the_posting_date_is_kept(self):
        """Most feeds give no date; Jobright does, and it drives the 24h window."""
        row = _jobright_row(_entry())
        assert row.posted_at == "2026-08-15 11:20:33"

    def test_the_description_joins_every_summary_field(self):
        row = _jobright_row(_entry())
        body = row.description or ""
        assert "batch pipelines" in body
        assert "Design ETL" in body
        assert "Own data quality" in body
        assert "$130K" in body, "salary belongs in the JD the tailor reads"

    def test_remote_and_work_model_reach_the_gates(self):
        row = _jobright_row(_entry())
        assert row.is_remote_flag_from_board is True
        assert "Remote" in (row.location_raw or "")

    def test_an_onsite_job_is_not_marked_remote(self):
        row = _jobright_row(_entry(job={"isRemote": "False", "workModel": "Onsite"}))
        assert row.is_remote_flag_from_board is False


class TestBadInput:
    def test_a_page_without_next_data_yields_nothing(self):
        assert _jobright_next_data("<html><body>nothing</body></html>") == {}

    def test_unparsable_json_yields_nothing(self):
        assert _jobright_next_data('<script id="__NEXT_DATA__">{not json</script>') == {}

    def test_a_record_missing_title_or_company_is_skipped(self):
        assert _jobright_row(_entry(job={"jobTitle": ""})) is None
        assert _jobright_row(_entry(company={"companyName": ""})) is None
        assert _jobright_row(_entry(job={"jobId": ""})) is None

    def test_a_job_with_no_summary_still_parses(self):
        row = _jobright_row(
            _entry(
                job={
                    "jobSummary": "",
                    "jdResponsibilitySummary": "",
                    "coreResponsibilities": [],
                    "salaryDesc": "",
                },
                company={"companyDesc": ""},
            )
        )
        assert row is not None and row.description is None


class TestBotChallenge:
    """Their Cloudflare check must be reported, not read as "no jobs today"."""

    def test_a_security_check_page_raises(self):
        page = "<html><title>Security check | Jobright</title></html>"
        with pytest.raises(JobrightChallenged):
            _jobright_next_data(page)

    def test_the_error_says_what_to_do(self):
        page = '<html><head><link href="//challenges.cloudflare.com"></head></html>'
        with pytest.raises(JobrightChallenged) as exc:
            _jobright_next_data(page)
        assert "CSV upload" in str(exc.value)


def test_it_is_wired_in_everywhere_the_ui_needs():
    assert "jobright" in ADAPTERS, "discover_feeds resolves adapters by this key"
    site = resolve_site("jobright")
    assert site and site["kind"] == "feed"
    assert site["label"] == "Jobright"


def test_it_is_declared_in_job_sites_config():
    from app.services.config_loader import load_job_sites

    ids = {s.get("id") for s in (load_job_sites().get("sites") or [])}
    assert "jobright" in ids
