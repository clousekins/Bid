"""ATS keyword scoring and per-job technical feasibility."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.ats_score import (
    extract_terms,
    jd_requirements,
    score_resume,
    technical_feasibility,
)
from app.services.persona import load_persona
from app.validators.dedupe import normalize_title

JD = """
About us: we are a fast growing startup.
Required: strong experience with Python, Spark and Snowflake.
Experience with dbt and Airflow is required.
Nice to have: exposure to Kubernetes and Terraform.
"""


def test_terms_are_found_by_their_real_world_spellings():
    found = extract_terms("We use PySpark, BigQuery, K8s and scikit-learn daily.")
    assert {"spark", "bigquery", "kubernetes", "scikit-learn"} <= found


def test_short_terms_do_not_match_inside_words():
    """'go' and 'r' would otherwise match half the English in a posting."""
    found = extract_terms("A good organisation for regular reporting and goals.")
    assert "go" not in found
    assert "r" not in found


def test_requirements_outrank_passing_mentions():
    required, mentioned = jd_requirements(JD)
    assert {"python", "spark", "snowflake", "dbt", "airflow"} <= required
    # Nice-to-haves are seen but are not requirements.
    assert "kubernetes" in mentioned
    assert "kubernetes" not in required


def test_a_resume_covering_the_requirements_beats_one_that_does_not():
    good = """
    John Smith — john@example.com — (555) 555-5555
    SKILLS: Python, Spark, Snowflake, dbt, Airflow, Kubernetes
    EXPERIENCE: Senior Data Engineer building Spark and Airflow pipelines into Snowflake.
    EDUCATION: BS Computer Science
    """ + "filler content. " * 120
    poor = """
    John Smith — john@example.com — (555) 555-5555
    SKILLS: Excel, PowerPoint
    EXPERIENCE: Office administrator.
    EDUCATION: BA History
    """ + "filler content. " * 120
    assert score_resume(good, JD).score > score_resume(poor, JD).score + 30


def test_the_score_explains_itself():
    """A bare number is not actionable — the caller must see what is missing."""
    resume = "Python developer. john@example.com (555) 555-5555. Experience. Education. Skills."
    b = score_resume(resume, JD, job_title="Senior Data Engineer")
    assert 0 <= b.score <= 100
    assert b.keyword_points + b.structure_points + b.title_points == b.score
    assert "snowflake" in b.missing
    assert "python" in b.matched
    assert b.notes


def test_structure_problems_cost_points():
    body = "Python Spark Snowflake dbt Airflow. " * 40
    with_contact = (
        "john@example.com (555) 555-5555 EXPERIENCE EDUCATION SKILLS " + body
    )
    without = body
    assert score_resume(with_contact, JD).structure_points > score_resume(without, JD).structure_points


def test_a_table_is_flagged_because_parsers_drop_it():
    body = "EXPERIENCE EDUCATION SKILLS john@example.com (555) 555-5555 " + "Python Spark. " * 60
    plain = score_resume(body, JD)
    tabular = score_resume(body + "<table><tr><td>x</td></tr></table>", JD)
    assert tabular.structure_points < plain.structure_points
    assert any("table" in n for n in tabular.notes)


def test_an_empty_resume_scores_zero_structure():
    assert score_resume("", JD).structure_points == 0


@pytest.mark.parametrize("persona_id", ["brian_gou"])
def test_feasibility_separates_a_matching_role_from_an_unrelated_one(persona_id):
    persona = load_persona(persona_id)
    data_job = SimpleNamespace(title="Senior Data Engineer", description=JD)
    unrelated = SimpleNamespace(
        title="Dental Office Manager",
        description="Required: dental billing, patient scheduling, insurance claims.",
    )
    assert technical_feasibility(persona, data_job).score > technical_feasibility(
        persona, unrelated
    ).score


def test_feasibility_says_so_when_there_is_no_jd_yet():
    persona = load_persona("brian_gou")
    b = technical_feasibility(persona, SimpleNamespace(title="Data Engineer", description=None))
    assert b.score == 0
    assert any("no job description" in n for n in b.notes)


def test_feasibility_is_judged_against_history_not_wishes():
    """A persona cannot be feasible for tools absent from their ground truth."""
    persona = load_persona("brian_gou")
    job = SimpleNamespace(
        title="Embedded Engineer",
        description="Required: experience with COBOL. Required: Fortran expertise.",
    )
    assert technical_feasibility(persona, job).score < 50


class TestCsvDuplicateTitles:
    """Re-postings of one role must collapse to a single normalized title."""

    @pytest.mark.parametrize(
        "variant",
        [
            "Senior Data Engineer (Remote)",
            "Senior Data Engineer - 100% Remote - W2",
            "Senior Data Engineer  Req #12345",
            "Senior Data Engineer [Contract]",
            "senior data engineer",
        ],
    )
    def test_variants_collapse(self, variant):
        assert normalize_title(variant) == "senior data engineer"

    def test_genuinely_different_roles_stay_apart(self):
        assert normalize_title("Staff Data Engineer") != normalize_title("Senior Data Engineer")
        assert normalize_title("Data Analyst") != normalize_title("Data Engineer")

    def test_a_word_containing_a_stopword_survives(self):
        """'Solidity' must not be eaten by the requisition-id rule."""
        assert normalize_title("Solidity Developer") == "solidity developer"
        assert normalize_title("Contractor Relations Lead") == "relations lead"


@pytest.mark.asyncio
async def test_reposted_job_is_rejected_on_csv_upload(tmp_path, monkeypatch):
    """Same company + same role under a different URL must not enter twice."""
    from app.config import get_settings
    from app.db import session as sess
    from app.db.session import get_session_factory, init_db
    from app.services.pipeline import process_listing

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'dupe.db').as_posix()}")
    monkeypatch.setenv("ACCESS_TOKEN", "")
    monkeypatch.setenv("PERSONA_OUTPUT_ROOT", str(tmp_path / "applications"))
    get_settings.cache_clear()
    sess._engine = None
    sess._session_factory = None
    await init_db()

    base = {"company": "Acme Corp", "location": "Remote, US", "description": "x" * 200}
    factory = get_session_factory()
    async with factory() as db:
        first = await process_listing(
            db, {**base, "title": "Senior Data Engineer", "url": "https://a.example/1"}
        )
        assert first.ok, first.reason

        # Same role, different link and decoration — the CSV re-export case.
        again = await process_listing(
            db,
            {**base, "title": "Senior Data Engineer (Remote) - W2", "url": "https://b.example/2"},
        )
        assert not again.ok
        assert again.reason == "duplicate_company_title"
        assert again.job is not None and again.job.id == first.job.id

        # A genuinely different role at the same company still gets in.
        other = await process_listing(
            db, {**base, "title": "Staff Machine Learning Engineer", "url": "https://c.example/3"}
        )
        assert other.ok, other.reason


class TestFeasibilityRanking:
    """Regressions from real rows the operator spotted in the jobs table.

    Coverage ratio alone rated "Power Generation Electrical Engineer" at 89 —
    above a Databricks Architect role — because staffing boards paste one tech
    stack across every requisition regardless of the discipline.
    """

    STACK_JD = """
    Required: strong experience with AWS, Azure, Databricks, Snowflake,
    Spark, SQL and Java. Experience with solution architecture is required.
    """

    def _job(self, title, description):
        return SimpleNamespace(title=title, description=description)

    def test_a_different_discipline_cannot_outrank_a_matching_role(self):
        persona = load_persona("brian_gou")
        electrical = technical_feasibility(
            persona, self._job("Power Generation Electrical Engineer", self.STACK_JD)
        )
        databricks = technical_feasibility(
            persona, self._job("Senior Azure Databricks Architect", self.STACK_JD)
        )
        assert electrical.score < databricks.score
        assert any("different field" in n for n in electrical.notes)

    def test_a_shared_generic_word_is_not_evidence(self):
        """'Electrical Engineer' and 'Data Engineer' share only 'engineer'."""
        persona = load_persona("brian_gou")
        b = technical_feasibility(
            persona, self._job("Electrical Engineer", self.STACK_JD)
        )
        assert b.score <= 40

    def test_an_unreadable_jd_is_not_scored_as_a_bad_match(self):
        """A zero sorts to the bottom as if the fit were bad; it is unknown."""
        persona = load_persona("brian_gou")
        blurb = "We are a technology consulting company delivering solutions worldwide."
        b = technical_feasibility(persona, self._job("AI Data Engineer", blurb))
        assert b.score > 0
        assert any("title alone" in n for n in b.notes)

    def test_no_jd_at_all_still_reports_zero_and_says_why(self):
        persona = load_persona("brian_gou")
        b = technical_feasibility(persona, self._job("AI Data Engineer", None))
        assert b.score == 0
        assert any("fetch the JD" in n for n in b.notes)

    def test_one_matched_term_is_not_called_a_strong_match(self):
        persona = load_persona("brian_gou")
        b = technical_feasibility(
            persona, self._job("Data Engineer", "Required: experience with Python.")
        )
        assert "strong match" not in " ".join(b.notes)
        assert any("thin evidence" in n for n in b.notes)

    def test_ai_titles_are_recognised(self):
        """'AI' was absent from the vocabulary, so AI roles read as another field."""
        assert "ai" in extract_terms("Hands-on artificial intelligence work.")
        persona = load_persona("brian_gou")
        b = technical_feasibility(
            persona, self._job("AI Solutions Engineer", self.STACK_JD)
        )
        assert b.score > 40


def test_scoring_a_page_of_jobs_stays_fast():
    """This runs for every row of the table; a slow scan showed up as lag."""
    import time

    persona = load_persona("brian_gou")
    jd = TestFeasibilityRanking.STACK_JD + ("filler prose about the company. " * 400)
    jobs = [SimpleNamespace(title=f"Data Engineer {i}", description=jd + str(i)) for i in range(50)]
    start = time.perf_counter()
    for job in jobs:
        technical_feasibility(persona, job)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"50 uncached jobs took {elapsed:.2f}s"


class TestATitleWordIsNotAToolFragment:
    """A word inside a tool's name is not, on its own, evidence of the tool.

    The persona knows Power BI. Splitting that into words let "power" answer
    for "Power Generation Electrical Engineer", which then read as a partial
    match instead of another discipline entirely — the exact thing the
    feasibility gate exists to catch.

    Matching whole terms fixes it, but only if a compound term still earns
    every title word it covers: counting "machine learning" as one hit against
    two words rated a Machine Learning Engineer at half, which is worse than
    the bug.
    """

    @staticmethod
    def _score(title: str) -> float:
        from app.services.ats_score import _role_alignment

        return _role_alignment(load_persona("brian_gou"), title)[0]

    @staticmethod
    def _note(title: str) -> str:
        from app.services.ats_score import _role_alignment

        return _role_alignment(load_persona("brian_gou"), title)[1]

    def test_a_fragment_of_a_tool_name_is_not_a_match(self):
        assert self._score("Power Generation Electrical Engineer") == 0.0
        assert "different field" in self._note("Power Generation Electrical Engineer")

    def test_but_the_whole_tool_name_is(self):
        assert self._score("Power BI Developer") == 1.0
        assert self._note("Power BI Developer") == ""

    def test_a_compound_term_covers_every_word_it_spans(self):
        """Otherwise knowing the tool scores worse than not naming it."""
        assert self._score("Machine Learning Engineer") == 1.0
        assert self._score("CI/CD Engineer") == 1.0

    def test_single_word_tools_still_match_alone(self):
        for title in ("Spark Engineer", "Databricks Architect", "Snowflake Developer"):
            assert self._score(title) == 1.0, title

    def test_the_persona_s_own_discipline_still_matches(self):
        assert self._score("Senior Data Engineer") == 1.0

    def test_other_disciplines_are_still_turned_away(self):
        for title in ("Registered Nurse", "Mechanical Design Engineer", "Civil Site Engineer"):
            assert self._score(title) == 0.0, title
            assert "different field" in self._note(title)
