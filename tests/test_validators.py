from __future__ import annotations

import pytest

from app.validators.dedupe import normalize_company
from app.validators.remote_us import check_remote_us
from app.validators.seniority import check_seniority
from app.validators.it_domain import check_it_domain


@pytest.fixture(autouse=True)
def _strict_region_gate(monkeypatch):
    """Pin the intake gate to US-only for this module.

    These cases are about US eligibility specifically. The mode is an operator
    setting in config/remote_us_rules.yml — it is 'remote_any_country' now — and
    without pinning it here, changing that file silently rewrites what these
    tests assert instead of failing honestly. Coverage of the other modes lives
    in test_job_country.py.
    """
    from app.validators import remote_us

    monkeypatch.setattr(remote_us, "_load_rules", lambda: {"mode": "us_remote_strict"})


def test_normalize_company_strips_suffix():
    assert normalize_company("Acme Inc.") == "acme"
    assert normalize_company("Acme LLC") == "acme"


def test_remote_us_pass():
    r = check_remote_us("Remote, US", "We are a remote US team")
    assert r.ok


def test_remote_us_reject_hybrid():
    r = check_remote_us("Hybrid — NYC", "Must be in office 3 days")
    assert not r.ok


def test_remote_us_reject_non_us_location():
    for loc in ("Spain", "India", "Brazil", "United Kingdom", "Canada", "Germany"):
        r = check_remote_us(loc, f"Fully remote role based in {loc}. Join us today.")
        assert not r.ok, loc
        assert r.reason == "not_us"


def test_remote_us_location_beats_jd_us_mention():
    r = check_remote_us(
        "Brazil",
        "Remote role serving customers in the United States. Join us.",
    )
    assert not r.ok
    assert r.reason == "not_us"


def test_remote_us_accepts_us_state_location():
    r = check_remote_us("Austin, TX", "Fully remote within the United States")
    assert r.ok
    r2 = check_remote_us(
        "Columbus, Ohio, United States",
        None,
        work_arrangement="Remote Solely",
    )
    assert r2.ok
    r3 = check_remote_us("Georgia, United States", "Fully remote")
    assert r3.ok


def test_remote_us_baja_california_not_us_state():
    r = check_remote_us("Baja California, Mexico", "Remote")
    assert not r.ok
    assert r.reason == "not_us"


def test_remote_us_georgia_country_not_us_state():
    r = check_remote_us("Tbilisi, Georgia", "Fully remote")
    assert not r.ok


def test_remote_us_does_not_match_english_us_pronoun():
    r = check_remote_us("Remote", "Come join us on a distributed team worldwide")
    assert not r.ok


def test_remote_us_bare_remote_passes_without_us_in_jd():
    r = check_remote_us("Remote", "Senior software engineer building backend APIs.")
    assert r.ok
    r2 = check_remote_us("Fully remote", "")
    assert r2.ok


def test_remote_us_bare_remote_rejects_non_us_in_jd():
    r = check_remote_us("Remote", "Fully remote role based in Spain.")
    assert not r.ok
    assert r.reason == "not_us"


def test_seniority_reject_junior():
    r = check_seniority("Junior Software Engineer")
    assert not r.ok


def test_seniority_allow_senior():
    r = check_seniority("Senior Software Engineer")
    assert r.ok


def test_it_domain():
    assert check_it_domain("Senior ML Engineer").ok
    assert not check_it_domain("Retail Store Manager", "sell shoes").ok
