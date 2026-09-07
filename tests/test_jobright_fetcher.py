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
    JOBRIGHT_CONTRACT,
    JOBRIGHT_PASSES,
    JOBRIGHT_REMOTE,
    JOBRIGHT_SENIORITIES,
    JobrightChallenged,
    _jobright_next_data,
    _jobright_row,
    fetch_jobright,
    jobright_listings_from_html,
    jobright_search_urls,
)
from app.services.fetch_sites import resolve_site


def _titles(urls: list[str]) -> set[str]:
    """The distinct job titles a URL list searches for.

    Each title is now swept across the seniority ladder, so the number of URLs
    is a multiple of the number of searches. What these tests care about is
    which titles were asked for.
    """
    return {u.split("jobTitle=")[1].split("&")[0] for u in urls}


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
        assert len(_titles(urls)) > 10, "one search per title, not one per domain"
        assert all("jobTitle=" in u for u in urls)
        assert any("Senior%20Data%20Engineer" in u for u in urls)

    def test_picked_titles_narrow_the_search(self):
        urls = jobright_search_urls(domains=["de"], roles=["de_senior", "de_staff"])
        assert len(_titles(urls)) == 2
        assert any("Senior%20Data%20Engineer" in u for u in urls)
        assert any("Staff%20Data%20Engineer" in u for u in urls)
        assert not any("Data%20Architect" in u for u in urls)

    def test_a_compound_title_becomes_several_searches(self):
        """'AWS / Azure / GCP Data Engineer' is one row but three searches."""
        urls = jobright_search_urls(domains=["de"], roles=["de_cloud"])
        assert len(_titles(urls)) == 3
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
    assert sent == {
        "domains": ["de"],
        "roles": ["de_senior"],
        # The window decides what comes back, not what a past run ingested.
        "skip_seen": False,
        "time_budget": 580.0,
    }


def test_adapters_without_those_parameters_are_left_alone():
    from app.adapters.boards.fetchers import fetch_remoteok
    from app.services.discover import _adapter_kwargs

    assert _adapter_kwargs(fetch_remoteok, {"roles": ["de_senior"]}, ["de"], 600) == {}


class TestTickedTitlesAreTheSelection:
    """The picker opens with every domain already on.

    So ticking three titles arrives as twelve domains plus three roles. Read
    as "those titles, plus everything in the other eleven domains" it became
    70 searches and a six minute wait for a request meant to be small.
    """

    ALL_DOMAINS = ["ai", "de", "da", "ds", "fs", "be", "fe", "ce", "sq", "sf", "dv", "se"]

    def test_ticked_titles_win_over_the_default_domains(self):
        urls = jobright_search_urls(
            self.ALL_DOMAINS, ["de_senior", "de_staff", "de_architect"]
        )
        assert len(_titles(urls)) == 3
        assert all("/remote-jobs/data-ai?" in u for u in urls)

    def test_a_domain_with_no_ticked_title_is_not_searched(self):
        urls = jobright_search_urls(["de", "sf"], ["de_senior"])
        assert len(_titles(urls)) == 1
        assert "Senior%20Data%20Engineer" in urls[0]
        assert not any("software-engineering" in u for u in urls)

    def test_titles_from_several_domains_all_run(self):
        urls = jobright_search_urls(self.ALL_DOMAINS, ["de_senior", "ai_mlops"])
        assert len(_titles(urls)) == 2
        assert any("Senior%20Data%20Engineer" in u for u in urls)
        assert any("MLOps%20Engineer" in u for u in urls)

    def test_domains_alone_still_search_all_their_titles(self):
        """No titles ticked means the whole domain, which is the wide case."""
        assert len(jobright_search_urls(["de"], [])) > 10
        assert len(jobright_search_urls(self.ALL_DOMAINS, [])) > 60

    def test_a_role_finds_its_domain_without_the_domain_given(self):
        urls = jobright_search_urls([], ["de_senior"])
        assert _titles(urls) == {"Senior%20Data%20Engineer"}
        assert all("/remote-jobs/data-ai?" in u for u in urls)

    def test_a_small_selection_stays_a_small_run(self):
        """One ticked title is one title's sweep, not all twelve domains'."""
        urls = jobright_search_urls(self.ALL_DOMAINS, ["de_senior"])
        assert len(_titles(urls)) == 1
        assert len(urls) == len(JOBRIGHT_PASSES)


class TestTheSenioritySweep:
    """One title is one search only if you ignore their seniority filter.

    A bare `jobTitle` search answers with thirty jobs and no second page.
    `seniority` splits that same search six more ways, largely into different
    jobs — measured against live pages, one title reached 174 distinct remote
    listings where the bare search returned 30.
    """

    def test_a_title_is_swept_across_every_seniority(self):
        urls = jobright_search_urls(["de"], ["de_senior"])
        assert len(urls) == len(JOBRIGHT_PASSES)
        levels = {u.split("seniority=")[1] for u in urls if "seniority=" in u}
        assert levels == {str(s) for s in JOBRIGHT_SENIORITIES}

    def test_the_bare_search_is_kept_alongside_the_sweep(self):
        """Unfiltered returns jobs the per-level pages do not.

        One bare page per employment pool — full time and contract.
        """
        urls = jobright_search_urls(["de"], ["de_senior"])
        assert sum(1 for u in urls if "seniority=" not in u) == 2

    def test_every_search_pins_remote(self):
        """The path is only a slug; this param is what keeps onsite jobs out."""
        urls = jobright_search_urls(["de", "dv"], ["de_senior", "dv_sre"])
        assert urls and all(f"workModel={JOBRIGHT_REMOTE}" in u for u in urls)

    def test_titles_are_covered_before_any_is_swept(self):
        """A run cut short by its time budget should have seen every title.

        Depth-first, a 900s budget spent its whole life on the first two
        titles and never reached the rest of the selection.
        """
        urls = jobright_search_urls(["de"], ["de_senior", "de_staff", "de_lead"])
        first_pass = urls[:3]
        assert not any("seniority=" in u for u in first_pass)
        assert len(_titles(first_pass)) == 3, "all three titles before any sweep"

    def test_no_duplicate_pages_in_a_sweep(self):
        urls = jobright_search_urls(["de", "ai"], ["de_senior", "ai_mlops"])
        assert len(urls) == len(set(urls))

    def test_nothing_selected_still_means_no_searches(self):
        assert jobright_search_urls(None, None) == []


class TestContractAndPartTimeAreIncluded:
    """An unfiltered Jobright page is full time only, not "everything".

    Every job a bare page returns carries employmentType "Full-time".
    Contract and part-time are a separate pool reached only by asking:
    `jobTypes=2` answered with 30 jobs, none of them on the bare page, and
    swept across seniority it reached 145 more for the same title.
    """

    def test_each_title_is_read_for_both_employment_pools(self):
        urls = jobright_search_urls(["de"], ["de_senior"])
        contract = [u for u in urls if f"jobTypes={JOBRIGHT_CONTRACT}" in u]
        full_time = [u for u in urls if "jobTypes=" not in u]
        assert len(contract) == len(full_time) == len(urls) // 2

    def test_full_time_carries_no_job_type_param(self):
        """Their default. Sending jobTypes=1 explicitly changes nothing."""
        urls = jobright_search_urls(["de"], ["de_senior"])
        assert any("jobTypes=" not in u for u in urls)
        assert not any("jobTypes=1" in u for u in urls)

    def test_internships_are_not_fetched(self):
        """jobTypes=4 is internships, which the seniority gate drops anyway."""
        urls = jobright_search_urls(["de"], None)
        assert not any("jobTypes=4" in u for u in urls)

    def test_both_pools_are_covered_before_any_depth(self):
        """A truncated run must not have read full time only."""
        urls = jobright_search_urls(["de"], ["de_senior", "de_staff"])
        first_pass = urls[:4]          # 2 titles x 2 pools
        assert not any("seniority=" in u for u in first_pass)
        assert sum(1 for u in first_pass if "jobTypes=" in u) == 2

    def test_a_selection_doubles_rather_than_duplicates(self):
        urls = jobright_search_urls(["de", "ai"], ["de_senior", "ai_mlops"])
        assert len(urls) == len(set(urls))
        assert len(urls) == 2 * len(JOBRIGHT_PASSES)


class TestEmploymentTypeSurvivesParsing:
    """Fetching contract roles is only half an answer if nothing says so.

    The fetcher now asks for the contract pool as well as full time, so the
    listing has to carry which one it came back as — otherwise the export is
    a mixed pile with no way to tell them apart.
    """

    def test_the_type_is_kept_on_the_listing(self):
        job, company = _entry()
        job["employmentType"] = "Contract"
        row = _jobright_row(job, company)
        assert row.raw["employment_type"] == "Contract"

    def test_a_compound_type_is_kept_verbatim(self):
        job, company = _entry()
        job["employmentType"] = "Part-time, Contract"
        assert _jobright_row(job, company).raw["employment_type"] == "Part-time, Contract"

    def test_a_missing_type_is_none_not_a_guess(self):
        """Absent and "Full-time" are different answers."""
        job, company = _entry()
        job.pop("employmentType", None)
        assert _jobright_row(job, company).raw["employment_type"] is None

    def test_a_blank_type_is_none(self):
        job, company = _entry()
        job["employmentType"] = "   "
        assert _jobright_row(job, company).raw["employment_type"] is None
