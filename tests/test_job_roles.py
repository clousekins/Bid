"""Concrete job titles inside a domain.

A domain says what field a job is in; a role says which job. "Data Engineering"
matches 446 listings in the current table, and selecting the domain alone
cannot express "only the senior ones".
"""
from __future__ import annotations

import pytest

from app.services.job_domains import (
    JOB_DOMAINS,
    JOB_ROLES,
    classify_roles,
    role_label,
    roles_for_domain,
    title_matches_roles,
)


class TestClassification:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Senior Data Engineer", "de_senior"),
            ("Sr. Data Engineer", "de_senior"),
            ("Staff Data Engineer", "de_staff"),
            ("Principal Data Engineer", "de_principal"),
            ("Lead Data Engineer", "de_lead"),
            ("AWS Data Engineer", "de_cloud"),
            ("Azure Data Engineer", "de_cloud"),
            ("Analytics Engineer", "de_analytics"),
            ("Data Architect", "de_architect"),
            ("Salesforce Administrator", "sf_admin"),
            ("Site Reliability Engineer", "dv_sre"),
        ],
    )
    def test_real_titles_land_on_the_right_role(self, title, expected):
        assert expected in classify_roles(title)

    def test_a_senior_title_also_matches_the_base_role(self):
        """Someone filtering on 'Data Engineer' should still see senior ones."""
        found = classify_roles("Senior Data Engineer")
        assert "de_data_engineer" in found and "de_senior" in found

    def test_an_unrelated_title_matches_nothing(self):
        assert classify_roles("Registered Nurse") == []
        assert classify_roles("") == []
        assert classify_roles(None) == []


class TestFiltering:
    def test_no_selection_keeps_everything(self):
        """Selecting a domain without picking titles means the whole domain."""
        assert title_matches_roles("Anything At All", None)
        assert title_matches_roles("Anything At All", [])

    def test_a_selection_narrows_to_those_titles(self):
        chosen = ["de_senior", "de_staff"]
        assert title_matches_roles("Senior Data Engineer", chosen)
        assert title_matches_roles("Staff Data Engineer", chosen)
        assert not title_matches_roles("Data Engineer", chosen)
        assert not title_matches_roles("Data Analyst", chosen)

    def test_selecting_the_base_role_keeps_its_seniors(self):
        chosen = ["de_data_engineer"]
        assert title_matches_roles("Senior Data Engineer", chosen)
        assert title_matches_roles("Data Engineer", chosen)
        assert not title_matches_roles("Salesforce Developer", chosen)


class TestCatalog:
    def test_the_domains_the_ui_shows_have_titles(self):
        """Every domain in the picker should offer something to narrow by."""
        for domain in JOB_DOMAINS:
            assert roles_for_domain(domain["id"]), f"{domain['id']} has no titles"

    def test_role_ids_are_unique_across_domains(self):
        seen = set()
        for roles in JOB_ROLES.values():
            for role in roles:
                assert role["id"] not in seen, role["id"]
                seen.add(role["id"])

    def test_every_role_has_a_readable_label(self):
        for roles in JOB_ROLES.values():
            for role in roles:
                assert role["label"] and role["label"] != role["id"]
                assert role_label(role["id"]) == role["label"]

    def test_data_engineering_covers_the_titles_actually_seen(self):
        """These are the real titles in the current jobs table."""
        labels = {r["label"] for r in roles_for_domain("de")}
        for expected in (
            "Data Engineer",
            "Senior Data Engineer",
            "Staff Data Engineer",
            "Principal Data Engineer",
            "Lead Data Engineer",
        ):
            assert expected in labels


def test_the_fetch_pipeline_accepts_roles():
    """The picker's selection has to reach the filter."""
    import inspect

    from app.services.discover import _filter_listings, collect_site_listings

    assert "roles" in inspect.signature(collect_site_listings).parameters
    assert "allowed_roles" in inspect.signature(_filter_listings).parameters


class TestSearchQueries:
    """A label reads well in a picker; it is not always a searchable title.

    Jobright filters server-side on `jobTitle`, so these strings are sent to a
    real search — "AWS / Azure / GCP Data Engineer" has to become three.
    """

    def test_a_plain_label_searches_for_itself(self):
        from app.services.job_domains import role_queries

        assert role_queries("de_senior") == ["Senior Data Engineer"]

    def test_a_compound_label_becomes_several_titles(self):
        from app.services.job_domains import role_queries

        assert role_queries("de_cloud") == [
            "AWS Data Engineer",
            "Azure Data Engineer",
            "GCP Data Engineer",
        ]

    def test_no_query_contains_a_slash(self):
        """A slash is a label separator, never part of a real job title."""
        from app.services.job_domains import role_queries

        for roles in JOB_ROLES.values():
            for role in roles:
                for query in role_queries(role["id"]):
                    assert "/" not in query, f"{role['id']}: {query}"

    def test_every_role_is_searchable(self):
        from app.services.job_domains import role_queries

        for roles in JOB_ROLES.values():
            for role in roles:
                assert role_queries(role["id"]), role["id"]

    def test_an_unknown_role_searches_for_nothing(self):
        from app.services.job_domains import role_queries

        assert role_queries("no_such_role") == []

    def test_a_domain_collects_its_titles_without_repeats(self):
        from app.services.job_domains import domain_queries

        queries = domain_queries("de")
        assert len(queries) == len(set(queries))
        assert "Data Engineer" in queries and "Data Architect" in queries

    def test_every_domain_has_something_to_search(self):
        from app.services.job_domains import domain_queries

        for domain in JOB_DOMAINS:
            assert domain_queries(domain["id"]), domain["id"]
