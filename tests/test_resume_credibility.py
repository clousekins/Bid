"""Does the generated resume read as true?

Judged on the resume's own text — dates against the technology it claims —
not against the persona prompt. A tailored resume is written fresh for every
posting and nobody reads the thousand that go out, so this reads them.
"""
from __future__ import annotations

import pytest

from app.services.resume_credibility import (
    parse_roles,
    resume_text,
    score_credibility,
)

HEADER = "PROFESSIONAL EXPERIENCE\nAcme Corp  Jan 2020 - Jan 2023\nSenior Data Engineer\n"


def _resume(body: str, *, summary: str = "") -> str:
    return f"PROFESSIONAL SUMMARY\n{summary}\n{HEADER}{body}\nTECHNICAL SKILLS\nPython, SQL\n"


class TestAnachronism:
    """The strongest signal that a resume is not true."""

    def test_technology_claimed_before_it_existed_is_caught(self):
        bad = (
            "PROFESSIONAL EXPERIENCE\nOld Corp  Jan 2010 - Jan 2012\nEngineer\n"
            "Built Snowflake warehouses and Kubernetes clusters.\n"
        )
        report = score_credibility(bad)
        kinds = {f.kind for f in report.findings}
        assert "anachronism" in kinds
        messages = " ".join(f.message for f in report.findings)
        assert "snowflake" in messages and "kubernetes" in messages
        assert report.score < 70

    def test_technology_claimed_after_it_existed_is_fine(self):
        ok = _resume("Built Snowflake warehouses and Kubernetes clusters.")
        assert not [f for f in score_credibility(ok).findings if f.kind == "anachronism"]


class TestSectionBoundaries:
    """Both of these produced false findings on real resumes before the fix."""

    def test_skills_list_is_not_attributed_to_the_oldest_job(self):
        """A skills list is a claim about a career, not about one job."""
        text = (
            "PROFESSIONAL EXPERIENCE\nOld Corp  Jan 2010 - Jan 2012\nEngineer\n"
            "Wrote SQL reports.\n"
            "TECHNICAL SKILLS\nDatabricks, Snowflake, Kubernetes, Airflow\n"
        )
        assert not [f for f in score_credibility(text).findings if f.kind == "anachronism"]

    def test_a_projects_section_with_a_qualifier_still_ends_the_role(self):
        """'SELECTED PROJECTS' was missed, dating every project to a 2014 job."""
        text = (
            "PROFESSIONAL EXPERIENCE\nUni  Jan 2014 - Jun 2014\nWeb Developer\n"
            "Built campus web pages.\n"
            "SELECTED PROJECTS\nDistributed LLM training with PyTorch and Kubernetes.\n"
        )
        assert not [f for f in score_credibility(text).findings if f.kind == "anachronism"]

    def test_roles_are_parsed_with_their_dates(self):
        roles = parse_roles(
            "Acme  Feb 2023 - Present\nEngineer\nx\nBeta  Oct 2021 - May 2022\nEngineer\ny\n"
        )
        assert len(roles) == 2
        assert roles[0].ongoing is True
        assert (roles[1].start_year, roles[1].end_year) == (2021, 2022)


class TestMetricPlausibility:
    """A level is not a change; conflating them flagged a third of the set."""

    @pytest.mark.parametrize(
        "line",
        [
            "Maintained over 99.9% availability for analytics pipelines.",
            "Ensured about 98% data accuracy across downstream reporting.",
            "Improved SLA compliance for business-critical pipelines to 99.7%.",
        ],
    )
    def test_service_levels_are_not_flagged(self, line):
        assert not [f for f in score_credibility(_resume(line)).findings if f.kind == "metrics"]

    @pytest.mark.parametrize(
        "line",
        [
            "Reduced pipeline runtime by 95% across all jobs.",
            "Improved throughput by about 99%.",
        ],
    )
    def test_extreme_improvements_are_flagged(self, line):
        assert [f for f in score_credibility(_resume(line)).findings if f.kind == "metrics"]

    def test_an_ordinary_improvement_is_not_flagged(self):
        line = "Reduced processing time by approximately 40%."
        assert not [f for f in score_credibility(_resume(line)).findings if f.kind == "metrics"]

    def test_an_absurd_speedup_is_flagged(self):
        assert [
            f
            for f in score_credibility(_resume("Delivered a 50x speedup.")).findings
            if f.kind == "metrics"
        ]


def test_a_claimed_tenure_that_the_dates_do_not_support():
    text = (
        "PROFESSIONAL SUMMARY\n20 years of experience in data engineering.\n"
        "PROFESSIONAL EXPERIENCE\nAcme  Jan 2020 - Jan 2023\nEngineer\nBuilt pipelines.\n"
    )
    findings = score_credibility(text).findings
    assert any(f.kind == "timeline" and "20 years" in f.message for f in findings)


def test_the_same_bullet_under_two_employers():
    line = "Developed and maintained ETL pipelines in Python and SQL for ingesting source data."
    text = (
        f"PROFESSIONAL EXPERIENCE\nAcme  Jan 2020 - Jan 2022\nEngineer\n{line}\n"
        f"Beta  Jan 2018 - Jan 2020\nEngineer\n{line}\n"
    )
    assert any(f.kind == "repetition" for f in score_credibility(text).findings)


def test_a_clean_resume_scores_full_marks():
    text = _resume(
        "Built Spark pipelines on Databricks, reducing runtime by about 35%.\n"
        "Maintained over 99.9% availability for downstream analytics.",
        # The helper's single role spans 2020-2023, so the summary must agree.
        summary="3 years of experience building data platforms.",
    )
    report = score_credibility(text)
    assert report.score == 100, [f.message for f in report.findings]


def test_an_empty_resume_is_reported_not_silently_passed():
    report = score_credibility("")
    assert report.score == 0
    assert report.findings


def test_html_is_reduced_to_readable_text():
    html = "<style>.a{color:red}</style><h1>Name</h1><ul><li>Built&nbsp;pipelines</li></ul>"
    text = resume_text(html)
    assert "color:red" not in text
    assert "Built pipelines" in text


def test_every_finding_explains_itself():
    """A bare score is not actionable — each finding must say what and where."""
    bad = (
        "PROFESSIONAL EXPERIENCE\nOld Corp  Jan 2010 - Jan 2012\nEngineer\n"
        "Built Snowflake pipelines.\n"
    )
    for finding in score_credibility(bad).findings:
        assert finding.message
        assert finding.severity in {"high", "medium", "low"}
        assert finding.kind
