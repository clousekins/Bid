from __future__ import annotations

from app.services.company_discovery import extract_ats_from_url


def test_extract_greenhouse_board():
    assert extract_ats_from_url("https://boards.greenhouse.io/stripe/jobs/123") == (
        "greenhouse",
        "stripe",
    )


def test_extract_lever_board():
    assert extract_ats_from_url("https://jobs.lever.co/netlify/abcdef") == ("lever", "netlify")


def test_extract_ashby_board():
    assert extract_ats_from_url("https://jobs.ashbyhq.com/linear/job/xyz") == ("ashby", "linear")


def test_extract_workable_board():
    assert extract_ats_from_url("https://apply.workable.com/acme/j/ABC123/") == (
        "workable",
        "acme",
    )


def test_extract_ignores_non_ats():
    assert extract_ats_from_url("https://www.linkedin.com/jobs/view/123") is None
    assert extract_ats_from_url("https://remoteok.com/remote-jobs/123") is None
