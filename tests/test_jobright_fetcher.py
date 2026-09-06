"""Jobright adapter.

Jobright has no public list API — /swan/job/list answers 401 "Cookie not
found" — so discovery goes through the paths their robots.txt names
explicitly (Allow: /remote-jobs/*, Allow: /jobs/*), reached from their own
sitemap. Each job page carries the record as Next.js __NEXT_DATA__.

These tests parse fixed HTML rather than calling the site, so they stay green
offline and do not hammer someone else's server on every run.
"""
from __future__ import annotations

import json

import pytest

from app.adapters.boards import ADAPTERS
from app.adapters.boards.fetchers import (
    JobrightChallenged,
    _jobright_next_data,
    _jobright_row,
    fetch_jobright,
    jobright_listings_from_html,
    jobright_search_urls,
)
from app.services.fetch_sites import resolve_site


def _entry(**overrides) -> dict:
    """The (job, company) pair the parser takes."""
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
    return job, company


class TestParsing:
    def test_a_job_page_becomes_a_listing(self):
        row = _jobright_row(*_entry())
        assert row is not None
        assert row.source == "Jobright", "source is the board, never the company"
        assert row.company_name == "Acme Analytics"
        assert row.job_title == "Senior Data Engineer"
        assert row.job_url.endswith("/jobs/info/69d5669fe63cea7a8b660fe4")
        assert row.external_id == "69d5669fe63cea7a8b660fe4"

    def test_the_posting_date_is_kept(self):
        """Most feeds give no date; Jobright does, and it drives the 24h window."""
        row = _jobright_row(*_entry())
        assert row.posted_at == "2026-08-15 11:20:33"

    def test_the_description_joins_every_summary_field(self):
        row = _jobright_row(*_entry())
        body = row.description or ""
        assert "batch pipelines" in body
        assert "Design ETL" in body
        assert "Own data quality" in body
        assert "$130K" in body, "salary belongs in the JD the tailor reads"

    def test_remote_and_work_model_reach_the_gates(self):
        row = _jobright_row(*_entry())
        assert row.is_remote_flag_from_board is True
        assert "Remote" in (row.location_raw or "")

    def test_an_onsite_job_is_not_marked_remote(self):
        row = _jobright_row(*_entry(job={"isRemote": "False", "workModel": "Onsite"}))
        assert row.is_remote_flag_from_board is False


class TestBadInput:
    def test_a_page_without_next_data_yields_nothing(self):
        assert _jobright_next_data("<html><body>nothing</body></html>") == {}

    def test_unparsable_json_yields_nothing(self):
        assert _jobright_next_data('<script id="__NEXT_DATA__">{not json</script>') == {}

    def test_a_record_missing_title_or_company_is_skipped(self):
        assert _jobright_row(*_entry(job={"jobTitle": ""})) is None
        assert _jobright_row(*_entry(company={"companyName": ""})) is None
        assert _jobright_row(*_entry(job={"jobId": ""})) is None

    def test_a_job_with_no_summary_still_parses(self):
        row = _jobright_row(
            *_entry(
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
        assert "fetch again" in str(exc.value), "the error must say what to do"


def test_it_is_wired_in_everywhere_the_ui_needs():
    assert "jobright" in ADAPTERS, "discover_feeds resolves adapters by this key"
    site = resolve_site("jobright")
    assert site and site["kind"] == "feed"
    assert site["label"] == "Jobright"


def test_it_is_declared_in_job_sites_config():
    from app.services.config_loader import load_job_sites

    ids = {s.get("id") for s in (load_job_sites().get("sites") or [])}
    assert "jobright" in ids


class TestCategoryPagesCarryTheJobs:
    """The change that made a full fetch possible.

    Category pages embed their whole first page of results, so a job needs no
    request of its own. Reading these was the difference between 347 requests
    that trip the rate limiter and 13 that do not.
    """

    @staticmethod
    def _page(entries: list, key: str = "defaultData") -> str:
        payload = {"props": {"pageProps": {key: entries}}}
        return (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(payload)
            + "</script></html>"
        )

    def test_every_embedded_job_is_read(self):
        job, company = _entry()
        second, _ = _entry(job={"jobId": "a" * 24, "jobTitle": "Data Architect"})
        page = self._page(
            [
                {"jobResult": job, "companyResult": company},
                {"jobResult": second, "companyResult": company},
            ]
        )
        rows = jobright_listings_from_html(page)
        assert [r.job_title for r in rows] == ["Senior Data Engineer", "Data Architect"]
        assert all(r.description for r in rows), "the JD comes with the listing"

    def test_a_single_job_page_still_works(self):
        """Job pages keep their one record under dataSource instead."""
        job, company = _entry()
        payload = {
            "props": {
                "pageProps": {"dataSource": {"jobResult": job, "companyResult": company}}
            }
        }
        html = (
            '<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>"
        )
        rows = jobright_listings_from_html(html)
        assert len(rows) == 1 and rows[0].job_title == "Senior Data Engineer"

    def test_a_page_with_no_jobs_yields_nothing(self):
        assert jobright_listings_from_html(self._page([])) == []
        assert jobright_listings_from_html("<html></html>") == []

    def test_unusable_records_are_skipped_not_fatal(self):
        job, company = _entry()
        page = self._page(
            [
                {"jobResult": {"jobTitle": ""}, "companyResult": company},
                "not a dict",
                {"jobResult": job, "companyResult": company},
            ]
        )
        assert len(jobright_listings_from_html(page)) == 1

    def test_requirements_reach_the_description(self):
        """The half of a JD the keyword scorer actually reads."""
        job, company = _entry(job={"requirements": ["5+ years of Spark", "dbt"]})
        rows = jobright_listings_from_html(
            self._page([{"jobResult": job, "companyResult": company}])
        )
        assert "Spark" in (rows[0].description or "")


class TestTitleSearch:
    """`jobTitle` is a real server-side filter, so titles become searches.

    A category page shows thirty jobs and has no second page — `page` and
    `offset` are ignored. Ten title searches under data-ai returned 265
    distinct jobs where the plain category page gave 30.
    """

    def test_a_domain_searches_every_title_it_knows(self):
        urls = jobright_search_urls(domains=["de"])
        assert len(urls) > 10, "one search per title, not one per domain"
        assert all("jobTitle=" in u for u in urls)
        assert any("Senior%20Data%20Engineer" in u for u in urls)

    def test_picked_titles_narrow_the_search(self):
        urls = jobright_search_urls(domains=["de"], roles=["de_senior", "de_staff"])
        assert len(urls) == 2
        assert any("Senior%20Data%20Engineer" in u for u in urls)
        assert any("Staff%20Data%20Engineer" in u for u in urls)
        assert not any("Data%20Architect" in u for u in urls)

    def test_a_compound_title_becomes_several_searches(self):
        """'AWS / Azure / GCP Data Engineer' is one row but three searches."""
        urls = jobright_search_urls(domains=["de"], roles=["de_cloud"])
        assert len(urls) == 3
        assert any("AWS" in u for u in urls) and any("GCP" in u for u in urls)

    def test_each_domain_searches_its_own_section(self):
        assert all(
            "/remote-jobs/data-ai?" in u for u in jobright_search_urls(domains=["de"])
        )
        assert all(
            "/remote-jobs/infrastructure-security?" in u
            for u in jobright_search_urls(domains=["dv"])
        )

    def test_no_duplicate_searches_across_domains(self):
        urls = jobright_search_urls(domains=["de", "ai", "ds", "da"])
        assert len(urls) == len(set(urls))

    def test_no_selection_falls_back_to_plain_categories(self):
        """fetch_jobright reads the category pages when nothing is picked."""
        assert jobright_search_urls(None, None) == []
        assert jobright_search_urls([], []) == []


def test_the_fetcher_takes_the_selection_and_a_deadline():
    """discover passes these through; a cancelled run would lose its results."""
    import inspect

    params = inspect.signature(fetch_jobright).parameters
    for name in ("domains", "roles", "time_budget"):
        assert name in params, name
    assert params["max_jobs"].default == 0, "0 means take everything selected"


def test_discover_hands_the_selection_to_adapters_that_want_it():
    from app.services.discover import _adapter_kwargs

    sent = _adapter_kwargs(fetch_jobright, {"roles": ["de_senior"]}, ["de"], 600)
    assert sent == {"domains": ["de"], "roles": ["de_senior"], "time_budget": 580.0}


def test_adapters_without_those_parameters_are_left_alone():
    from app.adapters.boards.fetchers import fetch_remoteok
    from app.services.discover import _adapter_kwargs

    assert _adapter_kwargs(fetch_remoteok, {"roles": ["de_senior"]}, ["de"], 600) == {}
