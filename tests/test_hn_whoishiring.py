from __future__ import annotations

from app.adapters.boards.fetchers import parse_hn_hiring_comment


def test_parse_hn_remote_engineer():
    text = (
        "Acme Labs | Senior Backend Engineer | Remote (US) | Full-time | "
        "https://boards.greenhouse.io/acmelabs/jobs/1 "
        "We are hiring a remote Python engineer."
    )
    listing = parse_hn_hiring_comment(text, story_id="1", comment_id="99")
    assert listing is not None
    assert listing.company_name == "Acme Labs"
    assert "Backend" in listing.job_title
    assert listing.source == "HN Who is Hiring"
    assert "greenhouse" in listing.job_url


def test_parse_hn_skips_non_eng():
    text = "Flower Shop | Retail Associate | NYC | Full-time"
    assert parse_hn_hiring_comment(text, story_id="1", comment_id="2") is None


def test_parse_hn_strips_html():
    text = "Stripe | Frontend Engineer | Remote | <a href=\"https://stripe.com/jobs\">link</a>"
    listing = parse_hn_hiring_comment(text, story_id="1", comment_id="3")
    assert listing is not None
    assert listing.company_name == "Stripe"
