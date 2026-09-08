from __future__ import annotations

from app.services.manual_ingest import parse_csv_jobs, parse_json_jobs


def test_parse_json_aliases():
    raw = """
    [
      {
        "company_name": "Acme",
        "job_title": "Senior Software Engineer",
        "job_url": "https://boards.greenhouse.io/acme/jobs/1",
        "loc": "Remote, US",
        "jd": "Build systems"
      }
    ]
    """
    jobs = parse_json_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["title"] == "Senior Software Engineer"
    assert jobs[0]["url"].startswith("https://")
    assert jobs[0]["description"] == "Build systems"


def test_parse_csv():
    raw = "company,title,url,location\nBeta,Backend Engineer,https://example.com/j/2,Remote US\n"
    jobs, meta = parse_csv_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Beta"
    assert meta["mapped_rows"] == 1


def test_parse_csv_job_link_alias():
    raw = "Company Name,Job Title,Job Link,Location\nAltCo,ML Engineer,https://example.com/j/2,Remote US\n"
    jobs, meta = parse_csv_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://example.com/j/2"
    assert "job_link" in meta["headers"] or "job_link" in str(meta)


class TestQuotesInsideFields:
    """A job description holds commas, newlines and quotes — all three.

    csv.Sniffer decides `doublequote` from whether a "" pair happens to fall
    inside the 8KB it samples, and a first quoted quote can easily sit past that
    mark. Reading a doubled-quote file as though quotes were not doubled does
    not raise: the field ends early and one job arrives as several rows, which
    reaches the caller as extra listings rather than as an error.
    """

    @staticmethod
    def _csv(description: str) -> str:
        import csv
        import io

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow(["company", "title", "url", "description"])
        for i in range(3):
            writer.writerow(
                [f"Acme {i}", "Data Engineer", f"https://example.com/j/{i}", description]
            )
        return buffer.getvalue()

    def test_a_quoted_quote_does_not_split_the_row(self):
        raw = self._csv('They call it the "modern data stack", and it is large.')
        jobs, meta = parse_csv_jobs(raw)
        assert len(jobs) == 3, meta
        assert meta["repaired_rows"] == 0
        assert '"modern data stack"' in jobs[0]["description"]

    def test_newlines_and_commas_survive(self):
        raw = self._csv("Own the pipelines.\n\nRequirements:\n- SQL, Python\n- dbt")
        jobs, _ = parse_csv_jobs(raw)
        assert len(jobs) == 3
        assert "- SQL, Python" in jobs[0]["description"]

    def test_a_late_first_quote_is_still_read(self):
        """The regression, which needs all three things at once.

        The sniffer's sample has to end before the first "" pair, so it reports
        doublequote=False — and a comma or newline has to follow that pair, or
        the misreading costs nothing and the row survives by luck. A long JD
        that quotes a phrase and then keeps going is both, which is why this
        showed up on real exports and not on a short fixture.
        """
        description = (
            ("word " * 2000)
            + 'we call it the "modern data stack", and then:\nSQL, Python, dbt'
        )
        raw = self._csv(description)
        assert raw.index('""') > 8192, "the fixture must outrun the sniffer's sample"

        jobs, meta = parse_csv_jobs(raw)
        assert len(jobs) == 3, f"one listing per job, not {len(jobs)}"
        assert meta["raw_rows"] == 3, "one row per job, not one per quoted phrase"
        assert jobs[0]["description"].endswith("SQL, Python, dbt")
