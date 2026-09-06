"""A profile must not be shown jobs at the employer its own resume claims.

Hidden per profile, never deleted: Brian cannot apply to UnitedHealth while his
resume says he works there, but Bing Lin can, and the listing has to stay in the
table for her.
"""
from __future__ import annotations

import pytest

from app.services.employer_conflicts import (
    company_groups,
    conflict_reason,
    persona_employers,
)
from app.services.persona import load_persona


class TestOwnEmployers:
    def test_a_persona_hides_the_companies_it_claims(self):
        emps = persona_employers(load_persona("brian_gou"))
        assert "unitedhealth group" in emps
        assert "meta" in emps
        assert "amazon" in emps

    def test_a_subsidiary_counts_as_the_same_employer(self):
        """The case that motivated this: Optum hires under UnitedHealth."""
        emps = persona_employers(load_persona("brian_gou"))
        assert "optum" in emps
        assert "unitedhealthcare" in emps
        assert "instagram" in emps, "Meta's brands are Meta"
        assert "aws" in emps, "AWS is Amazon"

    def test_unrelated_companies_are_not_hidden(self):
        emps = persona_employers(load_persona("brian_gou"))
        assert "google" not in emps
        assert "snowflake" not in emps

    def test_hiding_is_per_profile(self):
        """The whole point — one profile's employer is another's opportunity."""
        brian = load_persona("brian_gou")
        bing = load_persona("bing_lin")
        assert conflict_reason(brian, "Optum")
        assert conflict_reason(bing, "Optum") is None
        assert conflict_reason(bing, "Google")
        assert conflict_reason(brian, "Google") is None


class TestReasons:
    def test_a_direct_employer_says_so(self):
        reason = conflict_reason(load_persona("brian_gou"), "UnitedHealth Group")
        assert "resume says UnitedHealth Group" == reason

    def test_a_subsidiary_names_the_parent(self):
        reason = conflict_reason(load_persona("brian_gou"), "Optum")
        assert reason == "same employer as UnitedHealth Group"

    def test_no_reason_for_an_unrelated_company(self):
        assert conflict_reason(load_persona("brian_gou"), "Databricks") is None

    def test_blank_company_is_handled(self):
        assert conflict_reason(load_persona("brian_gou"), "") is None


def test_company_name_variants_still_match():
    """normalize_company drops Inc/LLC, so the suffix must not defeat this."""
    persona = load_persona("brian_gou")
    assert conflict_reason(persona, "UnitedHealth Group, Inc.")
    assert conflict_reason(persona, "Optum, LLC")


def test_groups_config_loads_and_is_shaped_right():
    groups = company_groups()
    assert groups, "company_groups.yml should ship with parents defined"
    assert "Optum" in groups["UnitedHealth Group"]
    for parent, brands in groups.items():
        assert isinstance(parent, str) and parent
        assert all(isinstance(b, str) and b for b in brands), parent


@pytest.mark.parametrize(
    "persona_id", ["brian_gou", "bing_lin", "phong_v_cao", "albert_liu"]
)
def test_every_persona_hides_at_least_its_own_history(persona_id):
    persona = load_persona(persona_id)
    emps = persona_employers(persona)
    for role in persona.experience_ground_truth or []:
        company = str((role or {}).get("company") or "").strip()
        if company:
            assert conflict_reason(persona, company), f"{persona_id} / {company}"
    assert emps
