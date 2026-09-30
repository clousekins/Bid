"""Every bullet ends up emphasised, and nothing else on the page is touched."""
from app.services.resume_emphasis import (
    MAX_PER_BULLET,
    emphasise_bullets,
    jd_vocabulary,
)

JD = """
Senior Data Engineer. You will own Snowflake and dbt, orchestrate with Airflow,
stream through Kafka, and keep CI/CD and data contracts healthy. Python and SQL
throughout, on AWS.
"""


def bullets(html):
    import re

    return re.findall(r"<li>(.*?)</li>", html, re.S)


def strongs(fragment):
    import re

    return re.findall(r"<strong>(.*?)</strong>", fragment, re.S)


class TestWhatTheJobDescriptionAsksFor:
    def test_it_picks_the_terms_out_of_the_posting(self):
        found = jd_vocabulary(JD)
        for term in ("Snowflake", "dbt", "Airflow", "Kafka", "CI/CD", "data contracts"):
            assert term in found

    def test_it_does_not_invent_terms_the_posting_never_used(self):
        assert "Databricks" not in jd_vocabulary(JD)
        assert "BigQuery" not in jd_vocabulary(JD)

    def test_a_longer_name_wins_over_the_shorter_one_inside_it(self):
        # "Amazon S3" must not be reported only as "S3".
        assert jd_vocabulary("we use Amazon S3 heavily")[0] == "Amazon S3"


class TestABareBulletGetsEmphasis:
    def test_the_job_descriptions_own_term_is_preferred(self):
        html = "<li>Modeled curated layers in Snowflake for analysts using Hadoop extracts</li>"
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["Snowflake"], out

    def test_a_practice_counts_as_much_as_a_product(self):
        html = "<li>Added schema validation and structured logging to file ingestion</li>"
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["schema validation"], out

    def test_an_early_role_falls_back_to_its_own_technology(self):
        # The posting never mentions TCP/IP; the bullet still must not be bare.
        html = "<li>Diagnosed TCP/IP, DNS, routing and switching faults across user devices</li>"
        out = emphasise_bullets(html, JD)
        assert len(strongs(out)) == 1
        assert strongs(out)[0].upper() in {"TCP/IP", "DNS"}, out

    def test_an_activity_phrased_bullet_still_gets_something(self):
        # Named no tool at all; this is the shape that slipped through before.
        html = (
            "<li>Added automated tests to telemetry ingestion and transformation jobs, "
            "checking record counts before deployment</li>"
        )
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["automated tests"], out

    def test_a_command_line_bullet_is_not_left_bare(self):
        # Real output from the ten-role resume: no vocabulary term matched until
        # CLI was added, so this one shipped with no emphasis at all.
        html = (
            "<li>Created CLI utilities for repeatable file-processing and support "
            "tasks, reducing manual handling of scheduled data exchanges</li>"
        )
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["CLI"], out

    def test_a_geospatial_bullet_is_not_left_bare(self):
        # Andrew Kong's GIS roles shipped bare until the plain term was listed.
        html = "<li>Created maps and customized GIS applications for location data</li>"
        assert strongs(emphasise_bullets(html, JD)) == ["GIS"]

    def test_the_longer_tool_name_wins_over_the_gis_inside_it(self):
        html = "<li>Managed the map database in QGIS with topology checks</li>"
        assert strongs(emphasise_bullets(html, JD)) == ["QGIS"]

    def test_a_leadership_bullet_is_not_left_bare(self):
        html = (
            "<li>Completed an 8-week leadership development course and turned review "
            "findings into written guidance for technicians</li>"
        )
        assert strongs(emphasise_bullets(html, JD)) == ["leadership"]

    def test_a_collaboration_bullet_is_not_left_bare(self):
        # Bruno Raphael's earliest role shipped this one with no emphasis: its
        # only concrete noun is "data flows".
        html = (
            "<li>Worked with senior developers and business users to translate "
            "requirements into maintainable software, document data flows, and "
            "resolve production issues within agreed service timelines</li>"
        )
        out = emphasise_bullets(html, JD)
        assert len(strongs(out)) == 1, out
        assert strongs(out)[0] in {"requirements", "data flows", "production issues"}

    def test_healthcare_standards_are_emphasised(self):
        html = "<li>Ingested 837 claim and 835 remittance files alongside HL7 feeds</li>"
        out = emphasise_bullets(html, "We process 837 and 835 files and HL7 messages")
        assert strongs(out), out
        assert all(s in {"837", "835", "HL7", "EDI 837", "EDI 835"} for s in strongs(out))

    def test_a_real_tool_still_outranks_the_last_resort_terms(self):
        html = "<li>Added automated tests to the Airflow ingestion jobs</li>"
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["Airflow"], out

    def test_a_bullet_with_nothing_recognisable_is_left_alone_not_mangled(self):
        html = "<li>Coordinated with the wider group on quarterly planning</li>"
        out = emphasise_bullets(html, JD)
        assert out == html


class TestWhatTheModelAlreadyDidIsRespected:
    def test_an_emphasised_bullet_is_untouched(self):
        html = "<li>Owned <strong>Airflow</strong> orchestration for billing loads</li>"
        assert emphasise_bullets(html, JD) == html

    def test_emphasis_past_the_cap_is_trimmed_keeping_the_earliest(self):
        html = (
            "<li>Used <strong>Python</strong>, <strong>SQL</strong>, "
            "<strong>Airflow</strong>, <strong>Kafka</strong> and "
            "<strong>dbt</strong> together</li>"
        )
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["Python", "SQL", "Airflow"]
        assert len(strongs(out)) == MAX_PER_BULLET
        # The trimmed words survive as plain text.
        assert "Kafka" in out and "dbt" in out


class TestItDoesNotDamageThePage:
    def test_only_list_items_are_changed(self):
        html = (
            '<div class="summary">Ten years of Python and SQL across data platforms.</div>'
            "<li>Built ingestion with Python and SQL</li>"
            '<div class="edu-school">Towson University | Towson, Maryland</div>'
        )
        out = emphasise_bullets(html, JD)
        assert "<strong>" not in out.split("<li>")[0]
        assert "<strong>" not in out.split("</li>")[1]
        assert len(strongs(out)) == 1

    def test_a_term_inside_an_attribute_is_not_wrapped(self):
        html = '<li><a href="https://python.example/sql">Built the loader</a></li>'
        out = emphasise_bullets(html, JD)
        assert 'href="https://python.example/sql"' in out

    def test_existing_markup_inside_the_bullet_survives(self):
        html = "<li>Tuned <em>heavy</em> Spark joins for the nightly load</li>"
        out = emphasise_bullets(html, JD)
        assert "<em>heavy</em>" in out
        assert strongs(out) == ["Spark"]

    def test_a_word_containing_a_term_is_not_matched(self):
        # "Pythonic" is not "Python"; "CI" inside "CI/CD" is not a separate hit.
        html = "<li>Wrote Pythonic helpers for the crew</li>"
        assert emphasise_bullets(html, JD) == html

    def test_slashed_names_match_as_one_token(self):
        html = "<li>Ran CI/CD promotion gates before each release</li>"
        out = emphasise_bullets(html, JD)
        assert strongs(out) == ["CI/CD"], out


class TestAWholeResume:
    def test_no_bullet_is_left_without_emphasis(self):
        html = "".join(
            f"<li>{text}</li>"
            for text in (
                "Owned ingestion into Snowflake with dbt for card transactions",
                "Added schema validation and structured logging to file ingestion",
                "Created unit and integration tests for the CLI workflows",
                "Standardized Linux deployment and monitoring across environments",
                "Diagnosed TCP/IP, DNS and switching faults across user devices",
                "Applied account permissions and patches through access control",
            )
        )
        out = emphasise_bullets(html, JD)
        for b in bullets(out):
            assert strongs(b), f"left bare: {b}"
            assert len(strongs(b)) <= MAX_PER_BULLET

    def test_it_is_idempotent(self):
        html = "<li>Built ingestion with Python for billing extracts</li>"
        once = emphasise_bullets(html, JD)
        assert emphasise_bullets(once, JD) == once

    def test_an_empty_job_description_still_emphasises(self):
        html = "<li>Built ingestion with Python for billing extracts</li>"
        out = emphasise_bullets(html, "")
        assert strongs(out) == ["Python"]
