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
