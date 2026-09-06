"""Sector search: find jobs at companies in one industry.

The hard part is not fetching, it is deciding a listing really belongs to the
sector. Matching the sector's words anywhere in the text put a railway
maintenance firm and a GitLab account-executive role in "healthcare", because
almost every posting mentions medical cover, risk and compliance.
"""
from __future__ import annotations

import pytest

from app.services.sector_search import (
    SECTOR_HINTS,
    SectorHit,
    _BENEFITS_NOISE,
    _sector_evidence,
    sector_terms,
)


def _hit(company="Acme", title="Data Engineer", description=None) -> SectorHit:
    return SectorHit(
        company=company,
        title=title,
        url="https://example.com/1",
        location="Remote, US",
        source="test",
        description=description,
    )


class TestSectorTerms:
    def test_known_sector_returns_both_tiers(self):
        strong, weak = sector_terms("healthcare")
        assert "healthcare" in strong and "telehealth" in strong
        assert "health" in weak, "bare 'health' is too generic to be strong"

    def test_a_sector_we_do_not_know_still_works(self):
        """The operator types their own field; we should not require a list."""
        strong, weak = sector_terms("veterinary")
        assert strong == ("veterinary",)
        assert weak == ()

    def test_a_near_match_finds_the_right_list(self):
        strong, _ = sector_terms("health care")
        assert "telehealth" in strong

    def test_empty_input_yields_nothing(self):
        assert sector_terms("") == ((), ())


class TestBenefitsNoise:
    """Benefits paragraphs are why weak matching failed."""

    @pytest.mark.parametrize(
        "line",
        [
            "We offer comprehensive medical, dental and vision coverage.",
            "Full health insurance from day one.",
            "Generous PTO and a wellness stipend.",
            "401(k) matching and paid time off.",
        ],
    )
    def test_benefits_sentences_are_stripped(self, line):
        assert "medical" not in _BENEFITS_NOISE.sub(" ", line).lower()
        assert "health" not in _BENEFITS_NOISE.sub(" ", line).lower()

    def test_real_sector_text_survives(self):
        text = "We offer medical, dental and vision. Our platform serves hospital patient records."
        cleaned = _BENEFITS_NOISE.sub(" ", text).lower()
        assert "hospital" in cleaned and "patient records" in cleaned


class TestSectorEvidence:
    def test_company_name_is_strong_evidence(self):
        strong, weak = sector_terms("healthcare")
        assert _sector_evidence(_hit(company="Matter Health Care"), strong, weak)

    def test_title_is_strong_evidence(self):
        strong, weak = sector_terms("healthcare")
        assert _sector_evidence(_hit(title="Clinical Data Engineer"), strong, weak)

    def test_benefits_alone_is_not_evidence(self):
        """The regression: an unrelated employer with a normal benefits list."""
        strong, weak = sector_terms("healthcare")
        jd = (
            "Join our developer tooling team. We offer medical, dental and vision "
            "coverage, full health insurance and generous PTO."
        )
        assert _sector_evidence(_hit(company="gitlab", description=jd), strong, weak) == []

    def test_generic_words_alone_are_not_evidence(self):
        """'risk' and 'compliance' appear in every enterprise posting."""
        strong, weak = sector_terms("fintech")
        jd = "You will manage risk and ensure compliance across our developer platform."
        assert _sector_evidence(_hit(company="Walmart", description=jd), strong, weak) == []

    def test_a_strong_word_in_the_body_does_qualify(self):
        strong, weak = sector_terms("healthcare")
        jd = "Our platform integrates with EHR systems and is HIPAA compliant."
        found = _sector_evidence(_hit(description=jd), strong, weak)
        assert "ehr" in found or "hipaa" in found

    def test_evidence_reports_what_matched(self):
        """A wrong hit has to be obvious before the operator imports it."""
        strong, weak = sector_terms("healthcare")
        found = _sector_evidence(_hit(company="Pivotal Health", title="Clinical Engineer"), strong, weak)
        assert found and all(isinstance(x, str) for x in found)
        assert len(found) <= 5


def test_every_shipped_sector_separates_its_tiers():
    for name, tiers in SECTOR_HINTS.items():
        assert tiers["strong"], name
        overlap = set(tiers["strong"]) & set(tiers["weak"])
        assert not overlap, f"{name}: {overlap} is in both tiers"


@pytest.mark.asyncio
async def test_an_empty_sector_is_rejected_without_calling_out():
    from app.services.sector_search import search_sector

    result = await search_sector("")
    assert result["results"] == []
    assert result["error"]
