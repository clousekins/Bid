from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.adapters.base import RawListing
from app.services.discover import _filter_listings, _passes_prefilter


def _listing(**kwargs) -> RawListing:
    defaults = {
        "source": "Test",
        "company_name": "Acme",
        "job_title": "Senior Software Engineer",
        "job_url": "https://example.com/jobs/1",
        "location_raw": "Remote",
        "description": "Build backend services with Python.",
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "is_remote_flag_from_board": True,
        "canonical_url": "https://example.com/jobs/1",
    }
    defaults.update(kwargs)
    return RawListing(**defaults)


def test_prefilter_accepts_bare_remote():
    ok, reason = _passes_prefilter(_listing())
    assert ok, reason


def test_prefilter_rejects_hybrid_location():
    ok, reason = _passes_prefilter(_listing(location_raw="Hybrid — NYC"))
    assert not ok
    assert reason == "hybrid_or_onsite"


def test_filter_fresh_requires_posted_at():
    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    rows, _, drops = _filter_listings(
        [
            _listing(
                job_url="https://example.com/fresh",
                canonical_url="https://example.com/fresh",
                posted_at=datetime.now(timezone.utc).isoformat(),
            ),
            _listing(
                job_url="https://example.com/nodate",
                canonical_url="https://example.com/nodate",
                posted_at=None,
            ),
        ],
        cutoff=cutoff,
        queries=["Software Engineer"],
        us_remote_strict=True,
        require_posted_at=True,
    )
    assert len(rows) == 1
    assert drops.get("missing_posted_at") == 1


class TestCompanyDiversity:
    """One employer must not flood the list — but nor should it lose roles.

    The picker's whole point is choosing titles. A company hiring five of the
    titles that were ticked has five jobs worth seeing; the earlier version
    built a group of up to eight and then kept only the first of them.
    """

    @staticmethod
    def _listing(company: str, title: str):
        from app.adapters.base import RawListing

        return RawListing(
            source="Jobright",
            company_name=company,
            job_title=title,
            job_url=f"https://jobright.ai/jobs/info/{company}-{title}".lower().replace(
                " ", "-"
            ),
            location_raw="United States (Remote)",
            is_remote_flag_from_board=True,
        )

    def test_every_role_at_one_company_survives(self):
        from app.services.discover import diversify_by_company

        rows = [
            self._listing("Acme", t)
            for t in ("Data Engineer", "Senior Data Engineer", "Staff Data Engineer")
        ]
        assert len(diversify_by_company(rows)) == 3

    def test_no_company_takes_the_whole_head_of_the_list(self):
        from app.services.discover import diversify_by_company

        rows = [self._listing("Acme", f"Data Engineer {i}") for i in range(5)]
        rows += [self._listing("Globex", "Data Engineer")]
        out = diversify_by_company(rows)
        assert len(out) == 6
        # First pass is one per company, so Globex is not buried behind Acme.
        assert {r.company_name for r in out[:2]} == {"Acme", "Globex"}

    def test_a_single_employer_is_still_capped(self):
        from app.services.discover import diversify_by_company

        rows = [self._listing("Acme", f"Data Engineer {i}") for i in range(20)]
        assert len(diversify_by_company(rows)) == 8
        assert len(diversify_by_company(rows, per_company=3)) == 3
